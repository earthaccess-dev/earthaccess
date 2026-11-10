"""Unit tests for earthaccess.virtual.

Covers the three public-facing modules:
  - core        (virtualize / _load_via_kerchunk)
  - _parser     (SUPPORTED_PARSERS / resolve_parser / get_urls_for_parser)
  - _credentials (get_granule_credentials_endpoint_and_region)

All external I/O is mocked so the suite runs without network access or
optional heavy dependencies.
"""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from earthaccess.results import DataCollection, DataGranule
from earthaccess.virtual._credentials import (
    get_granule_credentials_endpoint_and_region,
)
from earthaccess.virtual._parser import (
    SUPPORTED_PARSERS,
    get_urls_for_parser,
    resolve_parser,
)
from earthaccess.virtual.core import _load_via_kerchunk, virtualize

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ACCESS_CASES = [
    pytest.param(None, "https", id="default"),
    pytest.param("https", "https", id="https"),
    pytest.param("s3", "s3", id="s3"),
    pytest.param("direct", "s3", id="legacy-direct"),
    pytest.param("indirect", "https", id="legacy-indirect"),
]


def _make_granules(n: int = 1, base_url: str = "s3://bucket/file") -> list[DataGranule]:
    granules = []
    for i in range(n):
        g = MagicMock()
        g.data_links.return_value = [f"{base_url}_{i}.nc"]
        g.__getitem__ = MagicMock(
            side_effect=lambda key, i=i: {
                "meta": {
                    "collection-concept-id": f"C{i}-PODAAC",
                    "provider-id": "PODAAC",
                },
            }[key],
        )
        granules.append(g)
    return cast("list[DataGranule]", granules)


def _patch_internals(mock_vds: MagicMock | None = None):
    """Return the two patches used by most virtualize() tests."""
    if mock_vds is None:
        mock_vds = MagicMock()
    return (
        patch(
            "earthaccess.virtual.core.build_obstore_registry",
            return_value=MagicMock(),
        ),
        patch(
            "earthaccess.virtual.core._open_virtual_mfdataset",
            return_value=mock_vds,
        ),
    )


# ---------------------------------------------------------------------------
# core — input validation
# ---------------------------------------------------------------------------


def test_virtualize_empty_granules_raises() -> None:
    """virtualize() raises ValueError when granules list is empty."""
    with pytest.raises(ValueError, match=r"[Nn]o granules"):
        virtualize([])


def test_virtualize_multi_granule_no_concat_dim_raises() -> None:
    """virtualize() raises ValueError for >1 granule without concat_dim."""
    with (
        patch(
            "earthaccess.virtual.core.build_obstore_registry",
            return_value=MagicMock(),
        ),
        pytest.raises(ValueError, match="concat_dim"),
    ):
        virtualize(_make_granules(2))


def test_virtualize_invalid_parser_string_raises() -> None:
    """virtualize() raises ValueError for an unrecognised parser string."""
    with pytest.raises(ValueError, match="BadParser"):
        virtualize(_make_granules(1), parser="BadParser")


# ---------------------------------------------------------------------------
# core — happy paths
# ---------------------------------------------------------------------------


def test_virtualize_load_false_returns_virtual_dataset() -> None:
    """virtualize(load=False) returns the raw virtual dataset without calling kerchunk."""
    mock_vds = MagicMock()
    reg_patch, open_patch = _patch_internals(mock_vds)
    with (
        reg_patch,
        open_patch,
        patch("earthaccess.virtual.core._load_via_kerchunk") as mock_load,
    ):
        result = virtualize(_make_granules(1), load=False)

    assert result is mock_vds
    mock_load.assert_not_called()


def test_virtualize_load_true_delegates_to_kerchunk(tmp_path) -> None:
    """virtualize(load=True) calls _load_via_kerchunk and returns its result."""
    expected_ds = MagicMock()
    reg_patch, open_patch = _patch_internals()
    with (
        reg_patch,
        open_patch,
        patch(
            "earthaccess.virtual.core._load_via_kerchunk",
            return_value=expected_ds,
        ) as mock_load,
    ):
        result = virtualize(
            _make_granules(1),
            load=True,
            reference_dir=str(tmp_path),
        )

    mock_load.assert_called_once()
    assert result is expected_ds


@pytest.mark.parametrize(("access", "expected"), ACCESS_CASES)
def test_virtualize_normalizes_access(access, expected) -> None:
    """virtualize() normalizes access before forwarding it to its helpers."""
    reg_patch, open_patch = _patch_internals()
    with (
        reg_patch as registry,
        open_patch as open_virtual,
        patch("earthaccess.virtual.core._load_via_kerchunk") as mock_load,
    ):
        kwargs = {} if access is None else {"access": access}
        virtualize(_make_granules(1), load=True, **kwargs)

    assert registry.call_args.kwargs["access"] == expected
    assert open_virtual.call_args.kwargs["access"] == expected
    assert mock_load.call_args.kwargs["access"] == expected


# ---------------------------------------------------------------------------
# core — DMR++ fallback behaviour
# ---------------------------------------------------------------------------


def test_virtualize_dmrpp_fallback_emits_user_warning() -> None:
    """When DMR++ sidecars are missing, virtualize() warns and retries with HDFParser."""
    mock_vds_hdf = MagicMock()
    call_count = {"n": 0}

    def side_effect(*args, **kwargs):  # noqa: ARG001
        call_count["n"] += 1
        if call_count["n"] == 1:
            msg = "no .dmrpp sidecar"
            raise FileNotFoundError(msg)
        return mock_vds_hdf

    with (
        patch(
            "earthaccess.virtual.core.build_obstore_registry",
            return_value=MagicMock(),
        ),
        patch(
            "earthaccess.virtual.core._open_virtual_mfdataset",
            side_effect=side_effect,
        ),
        pytest.warns(UserWarning, match="HDFParser"),
    ):
        result = virtualize(_make_granules(1), parser="DMRPPParser")

    assert result is mock_vds_hdf
    assert call_count["n"] == 2


# ---------------------------------------------------------------------------
# _parser — SUPPORTED_PARSERS
# ---------------------------------------------------------------------------


def test_supported_parsers_contains_canonical_names() -> None:
    """SUPPORTED_PARSERS includes the three canonical parser names."""
    assert isinstance(SUPPORTED_PARSERS, frozenset)
    assert {"DMRPPParser", "HDFParser", "NetCDF3Parser"} <= SUPPORTED_PARSERS


# ---------------------------------------------------------------------------
# _parser — resolve_parser
# ---------------------------------------------------------------------------


def test_resolve_parser_dmrpp_returns_instance() -> None:
    """resolve_parser('DMRPPParser') returns a DMRPPParser instance."""
    assert type(resolve_parser("DMRPPParser")).__name__ == "DMRPPParser"


def test_resolve_parser_invalid_string_raises() -> None:
    """resolve_parser raises ValueError and lists valid names in the message."""
    with pytest.raises(ValueError, match="DMRPPParser"):
        resolve_parser("UnknownParser")


# ---------------------------------------------------------------------------
# _parser — get_urls_for_parser
# ---------------------------------------------------------------------------


def test_get_urls_dmrpp_appends_dmrpp_suffix() -> None:
    """get_urls_for_parser with DMRPPParser appends '.dmrpp' to each URL."""
    granule = cast("DataGranule", MagicMock())
    granule.data_links.return_value = ["s3://bucket/file.nc"]  # type: ignore[attr-defined]
    urls = get_urls_for_parser(
        [granule],
        resolve_parser("DMRPPParser"),
        access="direct",
    )
    assert urls == ["s3://bucket/file.nc.dmrpp"]


def test_get_urls_normalizes_access_for_data_links() -> None:
    """get_urls_for_parser normalizes legacy access values."""
    mock = MagicMock()
    mock.data_links.return_value = ["https://example.com/file.h5"]
    granule = cast("DataGranule", mock)
    get_urls_for_parser([granule], resolve_parser("HDFParser"), access="indirect")
    mock.data_links.assert_called_once_with(access="https")


# ---------------------------------------------------------------------------
# _credentials — get_granule_credentials_endpoint_and_region
# Uses real DataGranule / DataCollection objects (no MagicMock stand-ins).
# ---------------------------------------------------------------------------

_granule_no_endpoint = DataGranule(
    {
        "meta": {"collection-concept-id": "C1234-PROV"},
        "umm": {
            "RelatedUrls": [
                {"URL": "https://data.earthdata.nasa.gov/data.h5", "Type": "GET DATA"},
            ],
        },
    },
    cloud_hosted=True,
)


@patch("earthaccess.search_datasets")
def test_credentials_endpoint_from_granule(mock_search_datasets) -> None:
    """Endpoint embedded in the granule UMM-G record is used directly."""
    endpoint_url = "https://archive.daac.earthdata.nasa.gov/s3credentials"
    granule = DataGranule(
        {
            "meta": {"collection-concept-id": "C1234-PROV"},
            "umm": {
                "RelatedUrls": [
                    {
                        "URL": "https://data.earthdata.nasa.gov/data.h5",
                        "Type": "GET DATA",
                    },
                    {
                        "URL": "s3://bucket/data.h5",
                        "Type": "GET DATA VIA DIRECT ACCESS",
                    },
                    {"URL": endpoint_url, "Type": "VIEW RELATED INFORMATION"},
                ],
            },
        },
        cloud_hosted=True,
    )

    assert get_granule_credentials_endpoint_and_region(granule) == (
        endpoint_url,
        "us-west-2",
    )
    mock_search_datasets.assert_not_called()


@patch("earthaccess.search_datasets")
def test_credentials_endpoint_from_collection(mock_search_datasets) -> None:
    """Falls back to the collection record when the granule has no endpoint."""
    coll_endpoint = "https://archive.other-daac.earthdata.nasa.gov/s3credentials"
    coll_region = "us-east-1"
    mock_search_datasets.return_value = [
        DataCollection(
            {
                "meta": {"concept-id": "C1234-PROV"},
                "umm": {
                    "DirectDistributionInformation": {
                        "Region": coll_region,
                        "S3CredentialsAPIEndpoint": coll_endpoint,
                    },
                },
            },
        ),
    ]

    assert get_granule_credentials_endpoint_and_region(_granule_no_endpoint) == (
        coll_endpoint,
        coll_region,
    )
    mock_search_datasets.assert_called_once_with(count=1, concept_id="C1234-PROV")


@patch("earthaccess.search_datasets")
def test_credentials_collection_missing_region_defaults_to_us_west_2(
    mock_search_datasets,
) -> None:
    """Region defaults to us-west-2 when the collection record omits it."""
    coll_endpoint = "https://archive.other-daac.earthdata.nasa.gov/s3credentials"
    mock_search_datasets.return_value = [
        DataCollection(
            {
                "meta": {"concept-id": "C1234-PROV"},
                "umm": {
                    "DirectDistributionInformation": {
                        "S3CredentialsAPIEndpoint": coll_endpoint,
                    },
                },
            },
        ),
    ]

    _, region = get_granule_credentials_endpoint_and_region(_granule_no_endpoint)
    assert region == "us-west-2"


@patch("earthaccess.search_datasets")
def test_credentials_raises_when_no_endpoint_anywhere(mock_search_datasets) -> None:
    """ValueError raised when neither granule nor collection has an endpoint."""
    mock_search_datasets.return_value = [
        DataCollection(
            {
                "meta": {"concept-id": "C1234-PROV"},
                "umm": {"DirectDistributionInformation": {"Region": "us-east-1"}},
            },
        ),
    ]

    with pytest.raises(ValueError, match="did not provide an S3CredentialsAPIEndpoint"):
        get_granule_credentials_endpoint_and_region(_granule_no_endpoint)


def test_virtualize_raises_for_missing_requested_access_links() -> None:
    granule = DataGranule(
        {
            "umm": {
                "RelatedUrls": [
                    {
                        "URL": "https://example.com/data/file.h5",
                        "Type": "GET DATA",
                    }
                ]
            },
            "meta": {
                "concept-id": "G1-TEST",
                "collection-concept-id": "C1-TEST",
            },
        },
        cloud_hosted=False,
    )

    with pytest.raises(ValueError, match="no s3 data links"):
        virtualize([granule], access="s3")


def _make_kerchunk_granule() -> DataGranule:
    return DataGranule(
        {
            "meta": {"collection-concept-id": "C1-TEST"},
            "umm": {
                "RelatedUrls": [
                    {
                        "URL": "s3://bucket/file.nc",
                        "Type": "GET DATA VIA DIRECT ACCESS",
                    },
                ],
            },
        },
        cloud_hosted=True,
    )


def test_load_via_kerchunk_s3_uses_s3_storage_options(monkeypatch, tmp_path) -> None:
    """access='s3' must use S3 credentials, not the HTTPS session's options."""
    s3_fs = MagicMock()
    s3_fs.storage_options = {"key": "key", "secret": "secret", "token": "token"}
    endpoint = "https://example.com/s3credentials"

    monkeypatch.setattr(
        "earthaccess.virtual.core.get_granule_credentials_endpoint_and_region",
        lambda _granule: (endpoint, "us-west-2"),
    )
    get_s3_filesystem = MagicMock(return_value=s3_fs)
    monkeypatch.setattr("earthaccess.get_s3_filesystem", get_s3_filesystem)
    monkeypatch.setattr(
        "earthaccess.get_fsspec_https_session",
        MagicMock(side_effect=AssertionError("HTTPS session must not be used")),
    )

    captured = {}

    def fake_open_dataset(path, *, engine, storage_options):
        captured["path"] = path
        captured["engine"] = engine
        captured["storage_options"] = storage_options
        return "dataset"

    monkeypatch.setattr("xarray.open_dataset", fake_open_dataset)

    result = _load_via_kerchunk(
        vds=MagicMock(),
        granules=[_make_kerchunk_granule()],
        group="/",
        access="s3",
        reference_dir=str(tmp_path),
        reference_format="json",
    )

    assert result == "dataset"
    get_s3_filesystem.assert_called_once_with(endpoint=endpoint)
    assert captured["storage_options"] == {
        "remote_protocol": "s3",
        "remote_options": s3_fs.storage_options,
    }


def test_load_via_kerchunk_https_uses_https_storage_options(
    monkeypatch,
    tmp_path,
) -> None:
    """The default HTTPS path must keep using the HTTPS session's options."""
    https_fs = MagicMock()
    https_fs.storage_options = {"client_kwargs": {"trust_env": False}}
    get_https_session = MagicMock(return_value=https_fs)
    monkeypatch.setattr("earthaccess.get_fsspec_https_session", get_https_session)
    monkeypatch.setattr(
        "earthaccess.get_s3_filesystem",
        MagicMock(side_effect=AssertionError("S3 credentials must not be used")),
    )
    monkeypatch.setattr("xarray.open_dataset", MagicMock(return_value="dataset"))

    result = _load_via_kerchunk(
        vds=MagicMock(),
        granules=[_make_kerchunk_granule()],
        group="/",
        access="https",
        reference_dir=str(tmp_path),
        reference_format="json",
    )

    assert result == "dataset"
    get_https_session.assert_called_once_with()
