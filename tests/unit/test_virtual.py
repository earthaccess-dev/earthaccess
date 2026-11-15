"""Unit tests for earthaccess.virtual.

Covers the three public-facing modules:
  - core        (virtualize / _load_via_kerchunk)
  - _parser     (SUPPORTED_PARSERS / resolve_parser / get_urls_for_parser)
  - _credentials (get_granule_credentials_endpoint_and_region)

All external I/O is mocked so the suite runs without network access or
optional heavy dependencies.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from earthaccess.search import DataCollection, DataGranule
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


def _patch_internals(mock_vds: Any | None = None):
    """Return patches for the registry and all virtual openers."""
    if mock_vds is None:
        mock_vds = MagicMock()
    return (
        patch(
            "earthaccess.virtual.core.build_obstore_registry",
            return_value=MagicMock(),
        ),
        patch(
            "earthaccess.virtual.core._open_virtual_dataset_single",
            return_value=mock_vds,
        ),
        patch(
            "earthaccess.virtual.core._open_virtual_mfdataset",
            return_value=mock_vds,
        ),
        patch(
            "earthaccess.virtual.core._open_virtual_datatree",
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
    with pytest.raises(ValueError, match="concat_dim"):
        virtualize(_make_granules(2))


def test_virtualize_by_coords_with_concat_dim_raises() -> None:
    """combine='by_coords' rejects a concat_dim."""
    with pytest.raises(ValueError, match="concat_dim"):
        virtualize(_make_granules(2), combine="by_coords", concat_dim="time")


def test_virtualize_tree_multi_granule_raises() -> None:
    """tree=True requires exactly one granule."""
    with pytest.raises(ValueError, match="tree"):
        virtualize(_make_granules(2), tree=True)


def test_virtualize_tree_with_load_raises() -> None:
    """tree=True is incompatible with load=True."""
    with pytest.raises(ValueError, match="tree"):
        virtualize(_make_granules(1), tree=True, load=True)


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
    reg_patch, single_patch, mf_patch, tree_patch = _patch_internals(mock_vds)
    with (
        reg_patch,
        single_patch,
        mf_patch,
        tree_patch,
        patch("earthaccess.virtual.core._load_via_kerchunk") as mock_load,
    ):
        result = virtualize(_make_granules(1), load=False)

    assert result is mock_vds
    mock_load.assert_not_called()


def test_virtualize_load_true_delegates_to_kerchunk(tmp_path) -> None:
    """virtualize(load=True) calls _load_via_kerchunk and returns its result."""
    import xarray as xr

    expected_ds = MagicMock()
    reg_patch, single_patch, mf_patch, tree_patch = _patch_internals(xr.Dataset())
    with (
        reg_patch,
        single_patch,
        mf_patch,
        tree_patch,
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


@pytest.mark.parametrize(
    ("access", "expected"),
    [
        (None, "https"),
        ("https", "https"),
        ("indirect", "https"),
        ("s3", "s3"),
        ("direct", "s3"),
    ],
)
def test_virtualize_normalizes_access(access, expected) -> None:
    """virtualize() defaults to HTTPS and canonicalizes legacy aliases."""
    with (
        patch(
            "earthaccess.virtual.core.build_obstore_registry",
            return_value=MagicMock(),
        ) as registry,
        patch(
            "earthaccess.virtual.core._open_virtual_dataset_single",
            return_value=MagicMock(),
        ),
    ):
        if access is None:
            virtualize(_make_granules(1))
        else:
            virtualize(_make_granules(1), access=access)

    assert registry.call_args.kwargs["access"] == expected


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


# ---------------------------------------------------------------------------
# core — dispatch behaviour
# ---------------------------------------------------------------------------


def test_virtualize_single_granule_uses_open_virtual_dataset() -> None:
    """A single granule is opened directly, not through the mfdataset path."""
    mock_vds = MagicMock()
    with (
        patch(
            "earthaccess.virtual.core.build_obstore_registry",
            return_value=MagicMock(),
        ),
        patch(
            "earthaccess.virtual.core._open_virtual_dataset_single",
            return_value=mock_vds,
        ) as single,
        patch("earthaccess.virtual.core._open_virtual_mfdataset") as mf,
    ):
        result = virtualize(_make_granules(1))

    assert result is mock_vds
    single.assert_called_once()
    mf.assert_not_called()


def test_virtualize_single_granule_forwards_loadable_and_drop_variables() -> None:
    """loadable_variables / drop_variables are forwarded to the single-file opener."""
    mock_vds = MagicMock()
    with (
        patch(
            "earthaccess.virtual.core.build_obstore_registry",
            return_value=MagicMock(),
        ),
        patch(
            "earthaccess.virtual.core._open_virtual_dataset_single",
            return_value=mock_vds,
        ) as single,
    ):
        virtualize(
            _make_granules(1),
            loadable_variables=["time"],
            drop_variables=["qc"],
        )

    kwargs = single.call_args.kwargs
    assert kwargs["loadable_variables"] == ["time"]
    assert kwargs["drop_variables"] == ["qc"]


def test_virtualize_multi_granule_uses_open_virtual_mfdataset() -> None:
    """Multiple granules are combined through open_virtual_mfdataset."""
    mock_vds = MagicMock()
    with (
        patch(
            "earthaccess.virtual.core.build_obstore_registry",
            return_value=MagicMock(),
        ),
        patch(
            "earthaccess.virtual.core._open_virtual_mfdataset",
            return_value=mock_vds,
        ) as mf,
        patch("earthaccess.virtual.core._open_virtual_dataset_single") as single,
    ):
        result = virtualize(_make_granules(2), concat_dim="time")

    assert result is mock_vds
    mf.assert_called_once()
    single.assert_not_called()


def test_virtualize_by_coords_forwards_combine_and_join() -> None:
    """combine='by_coords' does not require concat_dim and forwards join."""
    mock_vds = MagicMock()
    with (
        patch(
            "earthaccess.virtual.core.build_obstore_registry",
            return_value=MagicMock(),
        ),
        patch(
            "earthaccess.virtual.core._open_virtual_mfdataset",
            return_value=mock_vds,
        ) as mf,
    ):
        result = virtualize(_make_granules(2), combine="by_coords", join="inner")

    assert result is mock_vds
    kwargs = mf.call_args.kwargs
    assert kwargs["combine"] == "by_coords"
    assert kwargs["join"] == "inner"


def test_virtualize_tree_returns_datatree() -> None:
    """tree=True dispatches to open_virtual_datatree."""
    mock_tree = MagicMock()
    with (
        patch(
            "earthaccess.virtual.core.build_obstore_registry",
            return_value=MagicMock(),
        ),
        patch(
            "earthaccess.virtual.core._open_virtual_datatree",
            return_value=mock_tree,
        ) as tree,
    ):
        result = virtualize(_make_granules(1), tree=True)

    assert result is mock_tree
    tree.assert_called_once()


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
            "earthaccess.virtual.core._open_virtual_dataset_single",
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


def test_get_urls_passes_access_to_data_links() -> None:
    """get_urls_for_parser forwards the access argument to granule.data_links."""
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


# ---------------------------------------------------------------------------
# core — _open_icechunk (S3 / direct-access path)
# ---------------------------------------------------------------------------


class TestOpenIcechunk:
    """Unit tests for ``_open_icechunk``.

    All icechunk and xarray I/O is mocked so the suite has no external
    dependencies.
    """

    @pytest.fixture(autouse=True)
    def _mock_all(self):
        from unittest.mock import MagicMock, patch

        store = MagicMock()
        session = MagicMock()
        session.store = store
        repo_obj = MagicMock()
        repo_obj.readonly_session.return_value = session
        repo_cls = MagicMock()
        repo_cls.open.return_value = repo_obj
        dataset = MagicMock()

        self.mock_store = store
        self.mock_session = session
        self.mock_repo_obj = repo_obj
        self.mock_repo_cls = repo_cls
        self.mock_dataset = dataset

        p_s3 = patch("icechunk.s3_storage")
        p_http = patch("icechunk.http_storage")
        p_local = patch("icechunk.local_filesystem_storage")
        p_repo = patch("icechunk.Repository", repo_cls)
        p_xr = patch("xarray.open_zarr", return_value=dataset)

        self.mock_s3 = p_s3.start()
        self.mock_http = p_http.start()
        self.mock_local = p_local.start()
        p_repo.start()
        p_xr.start()
        # No virtual chunk containers by default; VCC-specific tests override.
        repo_cls.fetch_config.return_value = None
        yield
        for p in [p_s3, p_http, p_local, p_repo, p_xr]:
            p.stop()

    def test_direct_access_with_https_url_calls_s3_storage(self):
        """access='direct' with an HTTPS URL builds S3 storage from parsed URI."""
        from earthaccess.virtual.core import _open_icechunk

        result = _open_icechunk(
            "https://bucket.daac/path/to/store.icechunk", access="direct"
        )

        self.mock_s3.assert_called_once_with(
            bucket="bucket.daac",
            prefix="path/to/store.icechunk",
        )
        self.mock_http.assert_not_called()
        self.mock_local.assert_not_called()
        assert result is self.mock_dataset

    def test_s3_uri_calls_s3_storage(self):
        """s3:// URI with default access builds S3 storage."""
        from earthaccess.virtual.core import _open_icechunk

        result = _open_icechunk("s3://my-bucket/key/store.icechunk")

        self.mock_s3.assert_called_once_with(
            bucket="my-bucket",
            prefix="key/store.icechunk",
        )
        assert result is self.mock_dataset

    def test_https_with_storage_options_calls_http_storage(self):
        """storage_options provided with HTTPS URI calls http_storage."""
        from earthaccess.virtual.core import _open_icechunk

        sopts = {"token": "abc"}
        result = _open_icechunk(
            "https://example.com/store.icechunk", storage_options=sopts
        )

        self.mock_http.assert_called_once_with(
            "https://example.com/store.icechunk", sopts
        )
        self.mock_s3.assert_not_called()
        self.mock_local.assert_not_called()
        assert result is self.mock_dataset

    def test_local_file_calls_local_storage(self):
        """Local file path with no storage_options calls local_filesystem_storage."""
        from earthaccess.virtual.core import _open_icechunk

        result = _open_icechunk("/local/store.icechunk")

        self.mock_local.assert_called_once_with("/local/store.icechunk")
        self.mock_s3.assert_not_called()
        self.mock_http.assert_not_called()
        assert result is self.mock_dataset

    def test_s3_uri_with_extra_storage_options(self):
        """storage_options are forwarded as kwargs to s3_storage."""
        from earthaccess.virtual.core import _open_icechunk

        sopts = {"region": "us-west-2", "from_env": True}
        result = _open_icechunk("s3://bucket/store.icechunk", storage_options=sopts)

        self.mock_s3.assert_called_once_with(
            bucket="bucket",
            prefix="store.icechunk",
            region="us-west-2",
            from_env=True,
        )
        assert result is self.mock_dataset

    def test_nasa_https_uri_uses_bearer_headers(self):
        """A NASA HTTPS store is opened with the EDL bearer header, not redirects."""
        import earthaccess
        from earthaccess.virtual.core import _open_icechunk

        original = earthaccess.__auth__.token
        earthaccess.__auth__.token = {"access_token": "tok"}
        try:
            result = _open_icechunk(
                "https://archive.podaac.earthdata.nasa.gov/store.icechunk"
            )
        finally:
            earthaccess.__auth__.token = original

        self.mock_http.assert_called_once_with(
            "https://archive.podaac.earthdata.nasa.gov/store.icechunk",
            headers={"Authorization": "Bearer tok"},
        )
        assert result is self.mock_dataset

    def test_non_nasa_https_uri_uses_redirect_storage(self):
        """A non-NASA HTTPS store falls back to redirect storage."""
        from unittest.mock import patch

        from earthaccess.virtual.core import _open_icechunk

        with patch("icechunk.redirect_storage", return_value="REDIRECT") as mock_redir:
            result = _open_icechunk("https://example.com/store.icechunk")

        mock_redir.assert_called_once_with("https://example.com/store.icechunk")
        assert result is self.mock_dataset

    def test_open_authorizes_detected_virtual_chunk_containers(self):
        """Repository.open receives an authorize_virtual_chunk_access mapping."""
        import icechunk
        from earthaccess.virtual.core import _open_icechunk

        config = MagicMock()
        config.virtual_chunk_containers = {"file:///data/": MagicMock()}
        self.mock_repo_cls.fetch_config.return_value = config

        _open_icechunk("/local/store.icechunk")

        call_kwargs = self.mock_repo_cls.open.call_args.kwargs
        assert call_kwargs["authorize_virtual_chunk_access"] == {
            "file:///data/": icechunk.credentials.LocalFileSystemAccess,
        }


class TestOpenIcechunkFromCollection:
    """Unit tests for ``_open_icechunk_from_collection``.

    All icechunk and xarray I/O is mocked so the suite has no external
    dependencies.
    """

    @pytest.fixture(autouse=True)
    def _mock_all(self):
        store = MagicMock()
        session = MagicMock()
        session.store = store
        repo_obj = MagicMock()
        repo_obj.readonly_session.return_value = session
        repo_cls = MagicMock()
        repo_cls.open.return_value = repo_obj
        dataset = MagicMock()

        self.mock_store = store
        self.mock_session = session
        self.mock_repo_obj = repo_obj
        self.mock_repo_cls = repo_cls
        self.mock_dataset = dataset

        p_http = patch("icechunk.http_storage")
        p_redirect = patch("icechunk.redirect_storage")
        p_repo = patch("icechunk.Repository", repo_cls)
        p_xr = patch("xarray.open_zarr", return_value=dataset)

        self.mock_http = p_http.start()
        self.mock_redirect = p_redirect.start()
        p_repo.start()
        p_xr.start()
        repo_cls.fetch_config.return_value = None
        yield
        for p in [p_http, p_redirect, p_repo, p_xr]:
            p.stop()

    def _collection(self):
        from earthaccess.search import DataCollection

        return DataCollection({"umm": {}, "meta": {}})

    def test_nasa_https_collection_uses_bearer_headers(self):
        """A NASA collection store is opened via http_storage with bearer headers."""
        import earthaccess
        from earthaccess.virtual.core import _open_icechunk_from_collection

        original = earthaccess.__auth__.token
        earthaccess.__auth__.token = {"access_token": "tok"}
        try:
            result = _open_icechunk_from_collection(
                self._collection(),
                "https://archive.podaac.earthdata.nasa.gov/store.icechunk",
            )
        finally:
            earthaccess.__auth__.token = original

        self.mock_http.assert_called_once_with(
            "https://archive.podaac.earthdata.nasa.gov/store.icechunk",
            headers={"Authorization": "Bearer tok"},
        )
        self.mock_redirect.assert_not_called()
        assert result is self.mock_dataset

    def test_non_nasa_collection_uses_redirect_storage(self):
        """A non-NASA collection store falls back to redirect storage."""
        from earthaccess.virtual.core import _open_icechunk_from_collection

        result = _open_icechunk_from_collection(
            self._collection(),
            "https://example.com/store.icechunk",
        )

        self.mock_redirect.assert_called_once_with("https://example.com/store.icechunk")
        self.mock_http.assert_not_called()
        assert result is self.mock_dataset

    def test_collection_s3_vccs_authorized_with_refreshable_creds(self):
        """S3 VCCs on a collection use refreshable credentials from the collection."""
        import icechunk
        from earthaccess.search import DataCollection
        from earthaccess.virtual.core import _open_icechunk_from_collection

        collection = DataCollection({"umm": {}, "meta": {}})
        collection.s3_credentials = {
            "accessKeyId": "AK",
            "secretAccessKey": "SK",
            "sessionToken": "ST",
        }
        config = MagicMock()
        config.virtual_chunk_containers = {"s3://podaac-bucket/": MagicMock()}
        self.mock_repo_cls.fetch_config.return_value = config

        _open_icechunk_from_collection(
            collection,
            "https://archive.podaac.earthdata.nasa.gov/store.icechunk",
        )

        call_kwargs = self.mock_repo_cls.open.call_args.kwargs
        cred = call_kwargs["authorize_virtual_chunk_access"]["s3://podaac-bucket/"]
        assert isinstance(cred, icechunk.S3Credentials.Refreshable)


class TestVccAuthorization:
    """Behavioral tests for the VCC credential helpers."""

    def _vccs(self, *prefixes):
        return {p: MagicMock() for p in prefixes}

    def test_empty_vccs_returns_empty_mapping(self):
        from earthaccess.virtual.core import _build_vcc_credentials

        mapping = _build_vcc_credentials(
            None,
            collection=None,
        )
        assert mapping == {}

    def test_s3_vcc_without_collection_uses_env_credentials(self):
        import icechunk
        from earthaccess.virtual.core import _build_vcc_credentials

        mapping = _build_vcc_credentials(
            self._vccs("s3://public-bucket/"),
            collection=None,
        )
        assert isinstance(
            mapping["s3://public-bucket/"],
            icechunk.S3Credentials.FromEnv,
        )

    def test_s3_vcc_with_collection_uses_refreshable_creds(self):
        import icechunk
        from earthaccess.search import DataCollection
        from earthaccess.virtual.core import _build_vcc_credentials

        collection = DataCollection({"umm": {}, "meta": {}})
        collection.s3_credentials = {
            "accessKeyId": "AK",
            "secretAccessKey": "SK",
            "sessionToken": "ST",
        }

        mapping = _build_vcc_credentials(
            self._vccs("s3://nasa-bucket/"),
            collection=collection,
        )
        assert isinstance(
            mapping["s3://nasa-bucket/"],
            icechunk.S3Credentials.Refreshable,
        )

    def test_https_vcc_uses_http_access_sentinel(self):
        import icechunk
        from earthaccess.virtual.core import _build_vcc_credentials

        mapping = _build_vcc_credentials(
            self._vccs("https://archive.podaac.earthdata.nasa.gov/"),
            collection=None,
        )
        assert mapping["https://archive.podaac.earthdata.nasa.gov/"] is (
            icechunk.credentials.HttpAccess
        )

    def test_file_vcc_uses_local_sentinel(self):
        import icechunk
        from earthaccess.virtual.core import _build_vcc_credentials

        mapping = _build_vcc_credentials(
            self._vccs("file:///data/"),
            collection=None,
        )
        assert mapping["file:///data/"] is icechunk.credentials.LocalFileSystemAccess

    def test_http_header_injection_only_for_nasa_hosts(self):
        from earthaccess.virtual.core import _inject_http_vcc_headers

        config = MagicMock()
        config.virtual_chunk_containers = {
            "https://archive.podaac.earthdata.nasa.gov/": MagicMock(name="nasa"),
            "https://public.example.com/": MagicMock(name="other"),
        }

        with (
            patch("icechunk.http_store", return_value="HTTP_STORE") as mock_store,
            patch("icechunk.VirtualChunkContainer", return_value="VC") as mock_vc,
        ):
            sentinel = "tok"
            result = _inject_http_vcc_headers(config, token=sentinel)

        assert result is config
        mock_vc.assert_called_once_with(
            "https://archive.podaac.earthdata.nasa.gov/",
            "HTTP_STORE",
            name=config.virtual_chunk_containers[
                "https://archive.podaac.earthdata.nasa.gov/"
            ].name,
        )
        mock_store.assert_called_once_with(
            headers={"Authorization": "Bearer tok"},
        )

    def test_authorize_vccs_merges_explicit_over_auto(self):
        import icechunk
        from earthaccess.virtual.core import _authorize_vccs

        storage = MagicMock()
        config = MagicMock()
        config.virtual_chunk_containers = {
            "s3://bucket/": MagicMock(),
            "file:///data/": MagicMock(),
        }
        repo_cls = MagicMock()
        repo_cls.fetch_config.return_value = config

        with patch("icechunk.Repository", repo_cls):
            cfg, mapping = _authorize_vccs(
                storage,
                collection=None,
                explicit={"s3://bucket/": "EXPLICIT"},
            )

        assert mapping["s3://bucket/"] == "EXPLICIT"
        assert mapping["file:///data/"] is icechunk.credentials.LocalFileSystemAccess
        assert cfg is None


def test_is_icechunk_uri_detects_local_store_directories(tmp_path):
    """A local directory containing an Icechunk repo is recognised as a store."""
    import tempfile
    from pathlib import Path

    from earthaccess.virtual.core import _is_icechunk_uri

    store_dir = tmp_path / "store"
    (store_dir / "snapshots").mkdir(parents=True)
    (store_dir / "snapshots" / "1CECHNKREP0F1RSTCMT0").touch()

    assert _is_icechunk_uri(str(store_dir))
    assert _is_icechunk_uri(f"file://{store_dir}")
    # A path that is not an icechunk store and does not mention "icechunk".
    plain = Path(tempfile.gettempdir()) / "plain_store_dir"
    assert not _is_icechunk_uri(str(plain))
