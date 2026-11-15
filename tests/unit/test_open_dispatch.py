"""Top-level tests for ``earthaccess.open``'s virtual-store dispatch.

A virtualization job persists an ``Asset`` whose ``format`` is ``icechunk`` or
``kerchunk``; ``ea.open`` must recognise it (and bare icechunk stores /
collections) and hand off to ``open_virtual`` instead of the file store.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import earthaccess
from earthaccess.plugins import Asset, Job
from earthaccess.search import DataCollection


def _collection(related_urls: list[dict[str, Any]]) -> DataCollection:
    return DataCollection(
        {
            "meta": {"concept-id": "C1234-EXAMPLE", "provider-id": "PODAAC"},
            "umm": {"RelatedUrls": related_urls},
        }
    )


def _marker_store(tmp_path: Path) -> Path:
    """A directory that carries icechunk's first-snapshot marker."""
    store = tmp_path / "sst.icechunk"
    (store / "snapshots").mkdir(parents=True)
    (store / "snapshots" / "1CECHNKREP0F1RSTCMT0").write_text("{}")
    return store


# ---------------------------------------------------------------------------
# Asset dispatch
# ---------------------------------------------------------------------------


def test_open_asset_dispatches_icechunk_to_open_virtual() -> None:
    asset = Asset(href="s3://bucket/store", access="s3", auth="none", format="icechunk")
    sentinel = MagicMock()

    with patch("earthaccess.virtual.open_virtual", return_value=sentinel) as opener:
        opened = earthaccess.open([asset])

    assert opened == [sentinel]
    opener.assert_called_once_with("s3://bucket/store", load=True, access="https")


def test_open_asset_dispatches_kerchunk_and_forwards_load() -> None:
    asset = Asset(
        href="s3://bucket/refs.parquet", access="s3", auth="none", format="kerchunk"
    )
    sentinel = MagicMock()

    with patch("earthaccess.virtual.open_virtual", return_value=sentinel) as opener:
        opened = earthaccess.open(asset, load=False)

    assert opened is sentinel
    opener.assert_called_once_with(
        "s3://bucket/refs.parquet", load=False, access="https"
    )


def test_open_job_assets_are_opened_as_datasets() -> None:
    asset = Asset(
        href="file:///tmp/sst.icechunk", access="file", auth="none", format="icechunk"
    )
    job = Job(lambda: [asset], plugin="vds", output="assets")
    sentinel = MagicMock()

    with patch("earthaccess.virtual.open_virtual", return_value=sentinel) as opener:
        opened = earthaccess.open(job)

    assert opened == [sentinel]
    opener.assert_called_once_with(
        "file:///tmp/sst.icechunk", load=True, access="https"
    )


# ---------------------------------------------------------------------------
# URI / collection dispatch
# ---------------------------------------------------------------------------


def test_open_local_icechunk_store_dispatches_to_open_virtual(tmp_path: Path) -> None:
    store = _marker_store(tmp_path)
    sentinel = MagicMock()

    with patch("earthaccess.virtual.open_virtual", return_value=sentinel) as opener:
        opened = earthaccess.open(str(store))

    assert opened is sentinel
    opener.assert_called_once_with(str(store), load=True, access="https")


def test_open_bare_parquet_is_not_intercepted(monkeypatch: Any) -> None:
    """Only Asset.format decides kerchunk; a bare path stays a file request."""
    store = MagicMock()
    monkeypatch.setattr(earthaccess, "_store", store)

    with patch("earthaccess.virtual.open_virtual") as opener:
        earthaccess.open(["s3://bucket/refs.parquet"], provider="POCLOUD")

    opener.assert_not_called()
    store.open.assert_called_once()


def test_open_collection_dispatches_to_open_virtual() -> None:
    collection = _collection(
        [
            {
                "Type": "GET DATA",
                "Subtype": "VIRTUAL COLLECTION",
                "URL": "s3://bucket/store.icechunk",
            }
        ]
    )
    sentinel = MagicMock()

    with patch("earthaccess.virtual.open_virtual", return_value=sentinel) as opener:
        opened = earthaccess.open(collection)

    assert opened is sentinel
    opener.assert_called_once_with(collection, load=True, access="https")


# ---------------------------------------------------------------------------
# DataCollection.virtual_collection_url
# ---------------------------------------------------------------------------


def test_virtual_collection_url_reads_the_related_url_subtype() -> None:
    collection = _collection(
        [
            {"Type": "GET DATA", "URL": "https://example.com/data"},
            {
                "Type": "GET DATA",
                "Subtype": "VIRTUAL COLLECTION",
                "URL": "s3://bucket/store.icechunk",
            },
        ]
    )

    assert collection.virtual_collection_url() == "s3://bucket/store.icechunk"


def test_virtual_collection_url_is_none_without_the_link() -> None:
    collection = _collection([{"Type": "GET DATA", "URL": "https://example.com/data"}])

    assert collection.virtual_collection_url() is None


# ---------------------------------------------------------------------------
# open_virtual(load=False) for icechunk
# ---------------------------------------------------------------------------


def test_open_virtual_dispatches_load_false_to_the_manifest_reader(
    monkeypatch: Any,
) -> None:
    from earthaccess.virtual import core

    calls: list[tuple[str, str]] = []

    def manifest(url: str, **kwargs: Any) -> Any:
        del kwargs
        calls.append(("manifest", url))
        return "VDS"

    def concrete(url: str, **kwargs: Any) -> Any:
        del kwargs
        calls.append(("concrete", url))
        return "DS"

    monkeypatch.setattr(core, "_open_icechunk_virtual", manifest)
    monkeypatch.setattr(core, "_open_icechunk", concrete)

    assert core.open_virtual("store.icechunk", load=False) == "VDS"
    assert core.open_virtual("store.icechunk", load=True) == "DS"
    assert calls == [("manifest", "store.icechunk"), ("concrete", "store.icechunk")]
