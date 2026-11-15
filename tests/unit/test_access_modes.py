"""Tests for HTTPS-default access modes and request guardrails."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import earthaccess
import pytest
from earthaccess._access import normalize_access
from earthaccess.search import DataGranule
from earthaccess.store import Store
from earthaccess.store.validation import (
    raise_for_missing_links,
    raise_for_mixed_providers,
    validate_url_protocols,
)
from earthaccess.virtual._credentials import validate_granules


def _granule(
    concept_id: str = "G1-TEST",
    provider: str = "POCLOUD",
    *,
    https: tuple[str, ...] = (),
    s3: tuple[str, ...] = (),
    cloud_hosted: bool | None = None,
) -> DataGranule:
    related_urls = [{"URL": url, "Type": "GET DATA"} for url in https] + [
        {"URL": url, "Type": "GET DATA VIA DIRECT ACCESS"} for url in s3
    ]
    return DataGranule(
        {
            "meta": {"concept-id": concept_id, "provider-id": provider},
            "umm": {"RelatedUrls": related_urls},
        },
        cloud_hosted=bool(s3) if cloud_hosted is None else cloud_hosted,
    )


def _store() -> Store:
    store = Store.__new__(Store)
    store.auth = MagicMock(authenticated=True)
    store._s3_credentials = {}
    store.thread_locals = threading.local()
    store._current_executor_type = None
    return store


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "https"),
        ("https", "https"),
        ("indirect", "https"),
        ("external", "https"),
        ("s3", "s3"),
        ("direct", "s3"),
    ],
)
def test_normalize_access(value, expected):
    assert normalize_access(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("S3", "s3"), ("HTTPS", "https"), ("Direct", "s3"), ("Indirect", "https")],
)
def test_normalize_access_is_case_insensitive(value, expected):
    assert normalize_access(value) == expected


def test_normalize_access_rejects_unknown_value():
    with pytest.raises(ValueError, match="Unknown access"):
        normalize_access("ftp")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Unknown access"):
        normalize_access(123)  # type: ignore[arg-type]


def test_data_links_default_is_https():
    granule = _granule(
        https=("https://example.com/a.nc",),
        s3=("s3://bucket/a.nc",),
    )
    assert granule.data_links() == ["https://example.com/a.nc"]


@pytest.mark.parametrize(
    ("access", "expected"),
    [
        ("https", ["https://example.com/a.nc"]),
        ("indirect", ["https://example.com/a.nc"]),
        ("external", ["https://example.com/a.nc"]),
        ("s3", ["s3://bucket/a.nc"]),
        ("direct", ["s3://bucket/a.nc"]),
    ],
)
def test_data_links_aliases(access, expected):
    granule = _granule(
        https=("https://example.com/a.nc",),
        s3=("s3://bucket/a.nc",),
    )
    assert granule.data_links(access=access) == expected


def test_derived_s3_links_use_each_link():
    granule = _granule(
        https=(
            "https://data.example.nasa.gov/protected/a.nc",
            "https://data.example.nasa.gov/protected/b.nc",
        ),
        cloud_hosted=True,
    )
    assert granule.data_links(access="s3") == [
        "s3://protected/a.nc",
        "s3://protected/b.nc",
    ]


def test_s3_derivation_skips_unrecognized_https_urls():
    granule = _granule(https=("https://example.com/protected_file.nc",))
    assert granule.data_links(access="s3") == []


def test_data_links_defaults_to_https_for_deprecated_in_region():
    granule = _granule(
        https=("https://example.com/a.nc",),
        s3=("s3://bucket/a.nc",),
        cloud_hosted=False,
    )
    with pytest.warns(DeprecationWarning, match="in_region"):
        assert granule.data_links(in_region=True) == ["https://example.com/a.nc"]


def test_data_links_s3_uses_explicit_links_without_cloud_hosted():
    granule = _granule(
        s3=("s3://bucket/a.nc",),
        cloud_hosted=False,
    )
    assert granule.data_links(access="s3") == ["s3://bucket/a.nc"]


def test_missing_https_links_suggest_s3():
    granules = [
        _granule("G-HTTPS", https=("https://example.com/a.nc",)),
        _granule("G-S3ONLY", s3=("s3://bucket/a.nc",)),
    ]
    with pytest.raises(
        ValueError,
        match="G-S3ONLY has no HTTPS data links; it offers S3 links",
    ):
        raise_for_missing_links(granules, "https")


def test_missing_s3_links_raise_for_each_granule():
    granules = [
        _granule("G-CLOUD", s3=("s3://bucket/a.nc",)),
        _granule("G-ONPREM", https=("https://example.com/a.nc",)),
    ]
    with pytest.raises(ValueError, match="G-ONPREM has no S3 data links"):
        raise_for_missing_links(granules, "s3")


def test_mixed_providers_raise_for_s3():
    granules = [
        _granule("G-A", provider="POCLOUD", s3=("s3://a/a.nc",)),
        _granule("G-B", provider="PODAAC", s3=("s3://b/b.nc",)),
    ]
    with pytest.raises(ValueError, match="multiple providers"):
        raise_for_mixed_providers(granules)


def test_validate_url_protocols_rejects_mixed_lists():
    with pytest.raises(ValueError, match="Cannot mix S3 and HTTPS"):
        validate_url_protocols(["s3://bucket/a.nc", "https://example.com/b.nc"])


def test_open_defaults_to_https_without_s3_credentials():
    store = _store()
    granule = _granule(
        https=("https://example.com/a.nc",),
        s3=("s3://bucket/a.nc",),
    )
    store._open_urls_https = MagicMock(return_value=["https-file"])
    store.get_s3_filesystem = MagicMock()

    assert store.open([granule]) == ["https-file"]
    store.get_s3_filesystem.assert_not_called()
    mapping = store._open_urls_https.call_args.args[0]
    assert list(mapping) == ["https://example.com/a.nc"]


def test_open_s3_uses_s3_without_https_fallback():
    store = _store()
    granule = _granule(s3=("s3://bucket/a.nc",), cloud_hosted=False)
    store.get_s3_filesystem = MagicMock(return_value=MagicMock())
    store._open_urls_https = MagicMock()

    with patch("earthaccess.store.core._open_files", return_value=["s3-file"]) as rf:
        assert store.open([granule], access="s3") == ["s3-file"]

    store.get_s3_filesystem.assert_called_once()
    store._open_urls_https.assert_not_called()
    assert list(rf.call_args.args[0]) == ["s3://bucket/a.nc"]


def test_open_s3_mixed_providers_fails_before_credentials():
    store = _store()
    store.get_s3_filesystem = MagicMock()
    granules = [
        _granule("G-A", provider="POCLOUD", s3=("s3://a/a.nc",)),
        _granule("G-B", provider="PODAAC", s3=("s3://b/b.nc",)),
    ]

    with pytest.raises(ValueError, match="multiple providers"):
        store.open(granules, access="s3")

    store.get_s3_filesystem.assert_not_called()


def test_open_s3_missing_links_fails_before_credentials():
    store = _store()
    store.get_s3_filesystem = MagicMock()
    granules = [
        _granule("G-CLOUD", s3=("s3://bucket/a.nc",)),
        _granule("G-ONPREM", https=("https://example.com/a.nc",)),
    ]

    with pytest.raises(ValueError, match="G-ONPREM has no S3 data links"):
        store.open(granules, access="s3")

    store.get_s3_filesystem.assert_not_called()


def test_open_urls_mixed_protocols_raise():
    store = _store()
    with pytest.raises(ValueError, match="Cannot mix S3 and HTTPS"):
        store.open(["s3://bucket/a.nc", "https://example.com/b.nc"])


def test_open_s3_url_requires_provider_or_endpoint():
    store = _store()
    with pytest.raises(ValueError, match="requires provider="):
        store.open(["s3://bucket/a.nc"])


def test_get_defaults_to_https_without_s3_credentials():
    store = _store()
    granule = _granule(
        https=("https://example.com/a.nc",),
        s3=("s3://bucket/a.nc",),
    )
    store._download_onprem_granules = MagicMock(return_value=["local"])
    store.get_s3_filesystem = MagicMock()

    assert store.get([granule], "/tmp") == ["local"]
    store.get_s3_filesystem.assert_not_called()
    assert store._download_onprem_granules.call_args.args[0] == [
        "https://example.com/a.nc"
    ]


def test_get_s3_mixed_providers_fails_before_credentials():
    store = _store()
    store.get_s3_filesystem = MagicMock()
    granules = [
        _granule("G-A", provider="POCLOUD", s3=("s3://a/a.nc",)),
        _granule("G-B", provider="PODAAC", s3=("s3://b/b.nc",)),
    ]

    with pytest.raises(ValueError, match="multiple providers"):
        store.get(granules, "/tmp", access="s3")

    store.get_s3_filesystem.assert_not_called()


def test_get_s3_uses_links_without_cloud_hosted():
    store = _store()
    granule = _granule(s3=("s3://bucket/a.nc",), cloud_hosted=False)
    store.get_s3_filesystem = MagicMock(return_value=MagicMock())
    store.download_cloud_file = MagicMock(return_value="/tmp/a.nc")

    with patch("earthaccess.store.core.get_executor") as executor:
        executor.return_value.map.return_value = ["/tmp/a.nc"]
        executor.return_value.shutdown.return_value = None
        assert store.get([granule], "/tmp", access="s3") == ["/tmp/a.nc"]

    store.get_s3_filesystem.assert_called_once()


def test_get_urls_mixed_protocols_raise():
    store = _store()
    with pytest.raises(ValueError, match="Cannot mix S3 and HTTPS"):
        store.get(["s3://bucket/a.nc", "https://example.com/b.nc"], "/tmp")


def test_get_cloud_s3_urls_require_provider_or_endpoint():
    store = _store()
    with pytest.raises(ValueError, match="can't yet guess the provider"):
        store.get(["s3://cumulus/a.nc"], "/tmp")

    store._download_onprem_granules = MagicMock(return_value=["local"])
    store.download_cloud_file = MagicMock(return_value="/tmp/a.nc")
    with patch("earthaccess.store.core.get_executor") as executor:
        executor.return_value.map.return_value = ["/tmp/a.nc"]
        executor.return_value.shutdown.return_value = None
        assert store.get(["s3://public-bucket/a.nc"], "/tmp") == ["/tmp/a.nc"]


def test_api_open_and_download_forward_https_default(monkeypatch):
    store = MagicMock()
    store.open.return_value = []
    store.get.return_value = []
    monkeypatch.setattr(earthaccess, "_store", store)
    granule = _granule(https=("https://example.com/a.nc",))

    earthaccess.open([granule])
    earthaccess.download([granule], "/tmp")

    assert store.open.call_args.kwargs["access"] == "https"
    assert store.get.call_args.kwargs["access"] == "https"


def test_virtual_granule_validation_rejects_mixed_s3_providers():
    granules = [
        _granule("G-A", provider="POCLOUD", s3=("s3://a/a.nc",)),
        _granule("G-B", provider="PODAAC", s3=("s3://b/b.nc",)),
    ]
    with pytest.raises(ValueError, match="multiple providers"):
        validate_granules(granules, "s3")
