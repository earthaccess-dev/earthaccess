"""Tests for search persistence: save(), load(), fingerprinting, and verification.

These tests exercise the save/load round-trip entirely offline by building
SearchResults from static fixtures and mocking the CMR search functions used
by verification.
"""

import gzip
import json
from unittest.mock import MagicMock, patch

import earthaccess
import pytest
from earthaccess.search import (
    DataCollection,
    DataGranule,
    DataGranulesQuery,
    GranuleResults,
    SearchResults,
    load_search,
    save_search,
)
from earthaccess.search.persistence import (
    _serialize_query_params,
    compute_fingerprint,
)

from tests.unit.fixtures import load_granule_fixture


def make_granule(fixture_name="HLSS30_umm", concept_id="G1-TEST"):
    """Build a DataGranule from a fixture with an overridable concept id."""
    data = load_granule_fixture(fixture_name)
    data["meta"]["concept-id"] = concept_id
    return DataGranule(data, cloud_hosted=True)


def make_results(concept_ids, query=None, limit=10, hits=12070):
    """Build a populated GranuleResults (offline) for saving."""
    results = GranuleResults.__new__(GranuleResults)
    SearchResults.__init__(results, query=query, limit=limit, prefetch=0)
    results._cached_results = [make_granule(concept_id=cid) for cid in concept_ids]
    results._exhausted = True
    results._materialized = True
    results._total_hits = hits
    return results


def make_query(**kwargs):
    return DataGranulesQuery().parameters(**kwargs)


# =============================================================================
# Fingerprint
# =============================================================================


def test_fingerprint_is_stable_regardless_of_order():
    """The fingerprint only depends on the set of concept-IDs, not their order."""
    ids_a = ["G1", "G2", "G3"]
    ids_b = ["G3", "G1", "G2"]

    fp_a = compute_fingerprint(make_results(ids_a))
    fp_b = compute_fingerprint(make_results(ids_b))

    assert fp_a == fp_b
    assert fp_a.startswith("sha256:")


def test_fingerprint_changes_when_results_change():
    """Different concept-ID sets produce different fingerprints."""
    fp_1 = compute_fingerprint(make_results(["G1", "G2"]))
    fp_2 = compute_fingerprint(make_results(["G1", "G2", "G3"]))

    assert fp_1 != fp_2


# =============================================================================
# Query serialization
# =============================================================================


def test_serialize_query_params_handles_temporal():
    """Temporal ranges are serialized for replay as start/end pairs."""
    results = make_results(
        ["G1"],
        query=make_query(short_name="ATL06", temporal=("2024-01-01", "2024-12-31")),
    )
    params = _serialize_query_params(results)

    assert params["short_name"] == "ATL06"
    assert tuple(params["temporal"]) == (
        "2024-01-01T00:00:00Z",
        "2024-12-31T23:59:59Z",
    )


def test_serialize_query_params_no_temporal():
    """Queries without temporal round-trip unchanged."""
    results = make_results(["G1"], query=make_query(short_name="ATL06"))
    params = _serialize_query_params(results)

    assert params == {"short_name": "ATL06"}


def test_query_builder_to_kwargs_keeps_spatial_clean():
    """to_kwargs() returns replayable tuples, not flattened CMR strings."""
    from earthaccess.search.query import GranuleQuery

    q = (
        GranuleQuery()
        .short_name("ATL06")
        .bounding_box(-46.5, 61.0, -42.5, 63.0)
        .temporal("2024-01-01", "2024-12-31")
        .cloud_cover(0, 20)
    )
    kwargs = q.to_kwargs()

    assert kwargs["bounding_box"] == (-46.5, 61.0, -42.5, 63.0)
    assert kwargs["temporal"][0].year == 2024
    assert kwargs["cloud_cover"] == (0.0, 20.0)
    # to_cmr flattens, to_kwargs does not
    assert isinstance(q.to_cmr()["bounding_box"], str)


def test_search_data_captures_query_kwargs():
    """search_data() stores replayable kwargs on the returned results."""
    from earthaccess.search.query import GranuleQuery

    q = GranuleQuery().short_name("ATL06").bounding_box(-46.5, 61.0, -42.5, 63.0)
    expected = q.to_kwargs()

    # simulate what search_data does: capture to_kwargs, then build the query
    results = make_results(["G1"], query=make_query(short_name="ATL06"))
    results._query_kwargs = expected

    assert results.query_params is not None
    assert results.query_params["bounding_box"] == (-46.5, 61.0, -42.5, 63.0)


def test_legacy_flattened_spatial_params_normalized():
    """Flattened legacy params (bbox/polygon/cloud_cover) are restored."""
    from earthaccess.search.persistence import _normalize_flattened_params

    flat = {
        "short_name": "ATL06",
        "bounding_box": "-46.5,61.0,-42.5,63.0",
        "polygon": "-10.0,40.0,-8.0,40.0,-8.0,42.0,-10.0,42.0,-10.0,40.0",
        "cloud_cover": "0,20",
        "point": ["42.5,10.75"],
        "orbit_number": "1000%2C2000",
    }
    norm = _normalize_flattened_params(flat)

    assert norm["bounding_box"] == (-46.5, 61.0, -42.5, 63.0)
    assert norm["cloud_cover"] == (0.0, 20.0)
    assert norm["point"] == (42.5, 10.75)
    assert norm["orbit_number"] == (1000.0, 2000.0)
    assert len(norm["polygon"]) == 5


def test_loaded_query_params_are_replayable(tmp_path):
    """After load, query_params restore tuples so search_data(**params) works."""
    from earthaccess.search.query import GranuleQuery

    q = (
        GranuleQuery()
        .short_name("ATL06")
        .bounding_box(-46.5, 61.0, -42.5, 63.0)
        .temporal("2024-01-01", "2024-12-31")
    )
    results = make_results(["G1"], query=make_query(short_name="ATL06"))
    results._query_kwargs = q.to_kwargs()
    path = results.save(tmp_path / "spatial.gz")

    loaded = SearchResults.load(path, verify=False, limit=None)
    params = loaded.query_params
    assert params is not None

    assert params["bounding_box"] == (-46.5, 61.0, -42.5, 63.0)
    assert isinstance(params["temporal"], tuple)
    # the rebuilt query object accepts these and matches the original
    rebuilt = loaded.rebuild_query()
    assert rebuilt.to_cmr()["bounding_box"] == "-46.5,61.0,-42.5,63.0"


def test_rebuild_query_matches_original(tmp_path):
    """rebuild_query() produces a query with identical CMR output."""
    from earthaccess.search.query import GranuleQuery

    q = (
        GranuleQuery()
        .short_name("ATL06")
        .bounding_box(-46.5, 61.0, -42.5, 63.0)
        .temporal("2024-01-01", "2024-12-31")
    )
    results = make_results(["G1"], query=make_query(short_name="ATL06"))
    results._query_kwargs = q.to_kwargs()
    path = results.save(tmp_path / "rebuild.gz")

    loaded = SearchResults.load(path, verify=False, limit=None)
    rebuilt = loaded.rebuild_query()

    assert rebuilt.to_cmr() == q.to_cmr()


# =============================================================================
# save() / load() round-trip
# =============================================================================


def test_save_and_load_roundtrip(tmp_path):
    """Saved results reload with the same granules, hits, and fingerprint."""
    results = make_results(
        ["G1", "G2"], query=make_query(short_name="HLSS30"), limit=10, hits=12070
    )
    path = results.save(tmp_path / "search.gz")

    assert path.exists()

    loaded = SearchResults.load(path, verify=False, limit=None)

    assert len(loaded) == 2
    assert [g["meta"]["concept-id"] for g in loaded] == ["G1", "G2"]
    assert loaded.total() == 12070
    assert loaded._stored_fingerprint == compute_fingerprint(results)
    assert loaded.verification is None


def test_save_raises_without_loaded_results(tmp_path):
    """Saving a search with no results raises ValueError."""
    results = make_results([], query=make_query(short_name="HLSS30"))
    with pytest.raises(ValueError, match="returned no results to save"):
        results.save(tmp_path / "empty.gz")


def test_save_payload_is_compressed_and_valid(tmp_path):
    """The payload is gzipped JSON Lines with header, results, and trailer."""
    results = make_results(["G1"], query=make_query(short_name="HLSS30"))
    path = results.save(tmp_path / "search.gz")

    with gzip.open(path, "rt") as f:
        lines = [json.loads(line) for line in f.read().splitlines() if line.strip()]

    header = lines[0]
    assert header["format"] == "earthaccess-search-v2"
    assert header["kind"] == "granules"
    assert header["limit"] == 2000  # default limit
    assert header["cmr_hits"] == 12070

    # One result line then a trailer with the fingerprint
    result_line = lines[1]
    assert result_line["meta"]["concept-id"] == "G1"
    trailer = lines[2]
    assert trailer["fingerprint"].startswith("sha256:")
    assert trailer["count"] == 1


def test_load_rejects_unknown_format(tmp_path):
    """Loading a payload with an unknown format raises ValueError."""
    path = tmp_path / "bad.gz"
    with gzip.open(path, "wt") as f:
        json.dump({"format": "other-v1"}, f)

    with pytest.raises(ValueError, match="Unrecognized search payload format"):
        SearchResults.load(path, verify=False, limit=None)


def test_module_level_functions_roundtrip(tmp_path):
    """save_search()/load_search() mirror the methods."""
    results = make_results(["G1"], query=make_query(short_name="HLSS30"))
    path = save_search(results, tmp_path / "module.gz")

    loaded = load_search(path, verify=False, limit=None)

    assert len(loaded) == 1
    assert loaded._stored_fingerprint == compute_fingerprint(results)


def test_save_with_limit_persists_prefix(tmp_path):
    """save(limit=1000) resets pagination and saves the first 1000."""
    from unittest.mock import patch

    from earthaccess.search import DataGranule, GranuleResults, SearchResults
    from earthaccess.search.persistence import load

    # Build a fake CMR query returning 10k granules in 2000-per-page slices.
    class Paged:
        def __init__(self, total):
            self.total = total
            self.cursor = 0
            self.headers = {}

        def hits(self):
            return self.total

        def page(self, n):
            start = self.cursor
            self.cursor = min(start + n, self.total)
            return [
                DataGranule(
                    {
                        "umm": {
                            "GranuleUR": f"g{i}",
                            "DataGranule": {
                                "ArchiveAndDistributionInformation": [{"Size": 1.0}]
                            },
                        },
                        "meta": {"concept-id": f"G{i}-PAGED"},
                    }
                )
                for i in range(start, self.cursor)
            ]

    pager = Paged(10_000)

    results = GranuleResults.__new__(GranuleResults)
    SearchResults.__init__(
        results,
        query=make_query(short_name="HLSS30"),
        limit=None,
        prefetch=0,
        query_kwargs={"short_name": "HLSS30"},
    )

    def fake_fetch(self, page_size, search_after=None):
        if search_after is not None and int(search_after) >= 9_999:
            return []
        page = pager.page(page_size)
        if page:
            results._last_search_after = str(int(search_after or -1) + len(page))
        return page

    with patch.object(SearchResults, "_fetch_page", fake_fetch):
        path = results.save(tmp_path / "prefix.gz", limit=1000)

    loaded = load(path, verify=False, limit=None)
    assert len(loaded) == 1000
    assert loaded._cached_results[0]["meta"]["concept-id"] == "G0-PAGED"
    assert loaded._cached_results[-1]["meta"]["concept-id"] == "G999-PAGED"


def test_save_all_with_limit_minus_one(tmp_path):
    """save(limit=-1) persists every matching result."""
    from unittest.mock import patch

    from earthaccess.search import DataGranule, GranuleResults, SearchResults
    from earthaccess.search.persistence import load

    class Paged:
        def __init__(self, total):
            self.total = total
            self.cursor = 0
            self.headers = {}

        def hits(self):
            return self.total

        def page(self, n):
            start = self.cursor
            self.cursor = min(start + n, self.total)
            return [
                DataGranule(
                    {
                        "umm": {"GranuleUR": f"g{i}"},
                        "meta": {"concept-id": f"G{i}-ALL"},
                    }
                )
                for i in range(start, self.cursor)
            ]

    pager = Paged(5_000)
    results = GranuleResults.__new__(GranuleResults)
    SearchResults.__init__(
        results,
        query=make_query(short_name="HLSS30"),
        limit=None,
        prefetch=0,
        query_kwargs={"short_name": "HLSS30"},
    )

    def fake_fetch(self, page_size, search_after=None):
        if search_after is not None and int(search_after) >= 4_999:
            return []
        page = pager.page(page_size)
        if page:
            results._last_search_after = str(int(search_after or -1) + len(page))
        return page

    with patch.object(SearchResults, "_fetch_page", fake_fetch):
        path = results.save(tmp_path / "all.gz", limit=-1)

    loaded = load(path, verify=False, limit=None)
    assert len(loaded) == 5_000
    assert loaded._cached_results[-1]["meta"]["concept-id"] == "G4999-ALL"


def test_save_default_persists_first_page(tmp_path):
    """save() without limit persists only the first page (default 2000)."""
    from unittest.mock import patch

    from earthaccess.search import DataGranule, GranuleResults, SearchResults
    from earthaccess.search.persistence import load

    class Paged:
        def __init__(self, total):
            self.total = total
            self.cursor = 0
            self.headers = {}

        def hits(self):
            return self.total

        def page(self, n):
            start = self.cursor
            self.cursor = min(start + n, self.total)
            return [
                DataGranule(
                    {
                        "umm": {"GranuleUR": f"g{i}"},
                        "meta": {"concept-id": f"G{i}-DEFAULT"},
                    }
                )
                for i in range(start, self.cursor)
            ]

    pager = Paged(5_000)
    results = GranuleResults.__new__(GranuleResults)
    SearchResults.__init__(
        results,
        query=make_query(short_name="HLSS30"),
        limit=None,
        prefetch=0,
        query_kwargs={"short_name": "HLSS30"},
    )

    def fake_fetch(self, page_size, search_after=None):
        if search_after is not None and int(search_after) >= 4_999:
            return []
        page = pager.page(page_size)
        if page:
            results._last_search_after = str(int(search_after or -1) + len(page))
        return page

    with patch.object(SearchResults, "_fetch_page", fake_fetch):
        path = results.save(tmp_path / "default.gz")

    loaded = load(path, verify=False, limit=None)
    assert len(loaded) == 2_000
    assert loaded._cached_results[0]["meta"]["concept-id"] == "G0-DEFAULT"
    assert loaded._cached_results[-1]["meta"]["concept-id"] == "G1999-DEFAULT"


def test_load_with_offset_and_limit(tmp_path):
    """load(offset=..., limit=...) returns a slice without materializing all."""
    from unittest.mock import patch

    from earthaccess.search import DataGranule, GranuleResults, SearchResults
    from earthaccess.search.persistence import load

    class Paged:
        def __init__(self, total):
            self.total = total
            self.cursor = 0
            self.headers = {}

        def hits(self):
            return self.total

        def page(self, n):
            start = self.cursor
            self.cursor = min(start + n, self.total)
            return [
                DataGranule(
                    {
                        "umm": {"GranuleUR": f"g{i}"},
                        "meta": {"concept-id": f"G{i}-SLICE"},
                    }
                )
                for i in range(start, self.cursor)
            ]

    pager = Paged(10_000)
    results = GranuleResults.__new__(GranuleResults)
    SearchResults.__init__(
        results,
        query=make_query(short_name="HLSS30"),
        limit=None,
        prefetch=0,
        query_kwargs={"short_name": "HLSS30"},
    )

    def fake_fetch(self, page_size, search_after=None):
        if search_after is not None and int(search_after) >= 9_999:
            return []
        page = pager.page(page_size)
        if page:
            results._last_search_after = str(int(search_after or -1) + len(page))
        return page

    with patch.object(SearchResults, "_fetch_page", fake_fetch):
        path = results.save(tmp_path / "slice.gz", limit=-1)

    # Full load
    full = load(path, verify=False, limit=None)
    assert len(full) == 10_000

    # A slice loads only the requested window
    slice1 = load(path, verify=False, offset=2000, limit=2000)
    assert len(slice1) == 2000
    assert slice1._cached_results[0]["meta"]["concept-id"] == "G2000-SLICE"
    assert slice1._cached_results[-1]["meta"]["concept-id"] == "G3999-SLICE"

    # Slicing verifies nothing (partial load)
    assert slice1.verification is None


def test_save_with_offset_persists_window(tmp_path):
    """save(limit=1000, offset=2000) saves results 2000-2999."""
    from unittest.mock import patch

    from earthaccess.search import DataGranule, GranuleResults, SearchResults
    from earthaccess.search.persistence import load

    class Paged:
        def __init__(self, total):
            self.total = total
            self.cursor = 0
            self.headers = {}

        def hits(self):
            return self.total

        def page(self, n):
            start = self.cursor
            self.cursor = min(start + n, self.total)
            return [
                DataGranule(
                    {
                        "umm": {"GranuleUR": f"g{i}"},
                        "meta": {"concept-id": f"G{i}-WINDOW"},
                    }
                )
                for i in range(start, self.cursor)
            ]

    pager = Paged(10_000)
    results = GranuleResults.__new__(GranuleResults)
    SearchResults.__init__(
        results,
        query=make_query(short_name="HLSS30"),
        limit=None,
        prefetch=0,
        query_kwargs={"short_name": "HLSS30"},
    )

    def fake_fetch(self, page_size, search_after=None):
        if search_after is not None and int(search_after) >= 9_999:
            return []
        page = pager.page(page_size)
        if page:
            results._last_search_after = str(int(search_after or -1) + len(page))
        return page

    with patch.object(SearchResults, "_fetch_page", fake_fetch):
        path = results.save(tmp_path / "window.gz", limit=1000, offset=2000)

    loaded = load(path, verify=False, limit=None)
    assert len(loaded) == 1000
    assert loaded._cached_results[0]["meta"]["concept-id"] == "G2000-WINDOW"
    assert loaded._cached_results[-1]["meta"]["concept-id"] == "G2999-WINDOW"


def test_load_default_materializes_first_page(tmp_path):
    """load() without args materializes only the first page (2000), offline."""
    from unittest.mock import patch

    from earthaccess.search import DataGranule, GranuleResults, SearchResults
    from earthaccess.search.persistence import load

    class Paged:
        def __init__(self, total):
            self.total = total
            self.cursor = 0
            self.headers = {}

        def hits(self):
            return self.total

        def page(self, n):
            start = self.cursor
            self.cursor = min(start + n, self.total)
            return [
                DataGranule(
                    {
                        "umm": {"GranuleUR": f"g{i}"},
                        "meta": {"concept-id": f"G{i}-DEFAULTLOAD"},
                    }
                )
                for i in range(start, self.cursor)
            ]

    pager = Paged(10_000)
    results = GranuleResults.__new__(GranuleResults)
    SearchResults.__init__(
        results,
        query=make_query(short_name="HLSS30"),
        limit=None,
        prefetch=0,
        query_kwargs={"short_name": "HLSS30"},
    )

    def fake_fetch(self, page_size, search_after=None):
        if search_after is not None and int(search_after) >= 9_999:
            return []
        page = pager.page(page_size)
        if page:
            results._last_search_after = str(int(search_after or -1) + len(page))
        return page

    with patch.object(SearchResults, "_fetch_page", fake_fetch):
        path = results.save(tmp_path / "defaultload.gz", limit=-1)

    # No verify -> no network; only the first page is loaded.
    with patch.object(earthaccess, "search_data", side_effect=AssertionError("no net")):
        loaded = load(path)

    assert len(loaded) == 2000
    assert loaded.verification is None
    assert loaded._cached_results[0]["meta"]["concept-id"] == "G0-DEFAULTLOAD"

    # limit=None loads everything (still offline by default).
    full = load(path, limit=None)
    assert len(full) == 10_000
    assert full.verification is None


def test_load_tolerates_truncated_payload(tmp_path):
    """An interrupted save leaves completed lines loadable (last page lost)."""
    import gzip as gzip_mod

    # Build a JSONL payload and truncate it mid-line (simulating a cancelled
    # write: header + two complete result lines + a partial trailing line).
    lines = [
        {
            "format": "earthaccess-search-v2",
            "kind": "granules",
            "limit": 2000,
            "cmr_hits": 100,
            "saved_at": "2026-01-01T00:00:00Z",
            "query_params": {"short_name": "HLSS30"},
        },
        {"GranuleUR": "g0", "meta": {"concept-id": "G0-TRUNC"}},
        {"GranuleUR": "g1", "meta": {"concept-id": "G1-TRUNC"}},
        {"GranuleUR": "g2", "meta": {"concept-id": "G2-TRUNC"}},
    ]
    full = "\n".join(json.dumps(item) for item in lines) + "\n"
    # Cut partway through the last line so it is not valid JSON.
    truncated_bytes = full.encode()[: len(full.encode()) - 10]

    path = tmp_path / "truncated.gz"
    with gzip_mod.open(path, "wb") as f:
        f.write(truncated_bytes)

    loaded = SearchResults.load(path, verify=False, limit=None)
    # The completed result lines are recovered; the partial one is dropped.
    assert len(loaded) == 2
    assert loaded._cached_results[0]["meta"]["concept-id"] == "G0-TRUNC"


# =============================================================================
# Verification
# =============================================================================


def _fake_fresh_results(concept_ids, hits):
    """Build a fake fresh SearchResults to be returned by mocked search_data."""
    fresh = make_results(concept_ids, query=make_query(short_name="HLSS30"), hits=hits)
    return fresh


def test_load_verify_unchanged(tmp_path):
    """verify=True reports unchanged when the search is identical."""
    results = make_results(["G1", "G2"], query=make_query(short_name="HLSS30"))
    path = results.save(tmp_path / "search.gz")

    with patch.object(
        earthaccess,
        "search_data",
        return_value=_fake_fresh_results(["G2", "G1"], 12070),
    ) as mock_search:
        loaded = SearchResults.load(path, verify=True, limit=None)

    mock_search.assert_called_once()
    report = loaded.verification
    assert report is not None
    assert report is not None
    assert report["unchanged"] is True
    assert report["fingerprint_match"] is True
    assert report["cmr_hits_match"] is True
    assert report["added"] == []
    assert report["removed"] == []


def test_load_verify_detects_removed_granule(tmp_path):
    """A granule that disappears is reported as removed."""
    results = make_results(["G1", "G2"], query=make_query(short_name="HLSS30"))
    path = results.save(tmp_path / "search.gz")

    with patch.object(
        earthaccess,
        "search_data",
        return_value=_fake_fresh_results(["G1"], 11999),
    ):
        loaded = SearchResults.load(path, verify=True, limit=None)

    report = loaded.verification
    assert report is not None
    assert report["unchanged"] is False
    assert report["fingerprint_match"] is False
    assert report["removed"] == ["G2"]
    assert report["added"] == []


def test_load_verify_detects_added_granule(tmp_path):
    """A granule that appears is reported as added."""
    results = make_results(["G1"], query=make_query(short_name="HLSS30"))
    path = results.save(tmp_path / "search.gz")

    with patch.object(
        earthaccess,
        "search_data",
        return_value=_fake_fresh_results(["G1", "G2"], 12071),
    ):
        loaded = SearchResults.load(path, verify=True, limit=None)

    report = loaded.verification
    assert report is not None
    assert report["unchanged"] is False
    assert report["added"] == ["G2"]
    assert report["removed"] == []


def test_load_verify_detects_hit_count_change(tmp_path):
    """A change in cmr-hits alone is reported even with matching granules."""
    results = make_results(["G1"], query=make_query(short_name="HLSS30"), hits=12070)
    path = results.save(tmp_path / "search.gz")

    with patch.object(
        earthaccess,
        "search_data",
        return_value=_fake_fresh_results(["G1"], 13000),
    ):
        loaded = SearchResults.load(path, verify=True, limit=None)

    report = loaded.verification
    assert report is not None
    assert report["unchanged"] is False
    assert report["fingerprint_match"] is True
    assert report["cmr_hits_match"] is False
    assert report["saved_cmr_hits"] == 12070
    assert report["current_cmr_hits"] == 13000


def test_load_verify_replays_saved_query(tmp_path):
    """Verification re-runs the search with the saved query parameters."""
    results = make_results(["G1"], query=make_query(short_name="HLSS30"))
    path = results.save(tmp_path / "search.gz")

    with patch.object(earthaccess, "search_data", return_value=MagicMock()) as m:
        m.return_value._cached_results = [make_granule(concept_id="G1")]
        m.return_value._total_hits = 12070
        m.return_value.limit = None
        SearchResults.load(path, verify=True, limit=None)

    # search_data was called with the saved params
    call_kwargs = m.call_args.kwargs
    assert call_kwargs["short_name"] == "HLSS30"


def test_load_verify_offline_has_no_report(tmp_path):
    """verify=False does not touch the network and leaves verification=None."""
    results = make_results(["G1"], query=make_query(short_name="HLSS30"))
    path = results.save(tmp_path / "search.gz")

    with patch.object(earthaccess, "search_data", side_effect=AssertionError("no net")):
        loaded = SearchResults.load(path, verify=False, limit=None)

    assert loaded.verification is None
    assert len(loaded) == 1


def test_load_verify_collections(tmp_path):
    """Collections verify through search_datasets with a collections payload."""
    collection = DataCollection(
        {
            "umm": {"ShortName": "ATL06", "EntryTitle": "test"},
            "meta": {"concept-id": "C1-TEST", "provider-id": "TEST"},
        }
    )
    from earthaccess.search import CollectionResults

    results = CollectionResults.__new__(CollectionResults)
    SearchResults.__init__(
        results, query=make_query(short_name="ATL06"), limit=10, prefetch=0
    )
    results._cached_results = [collection]
    results._exhausted = True
    results._materialized = True
    results._total_hits = 5

    path = results.save(tmp_path / "collections.gz")

    fresh = CollectionResults.__new__(CollectionResults)
    SearchResults.__init__(
        fresh, query=make_query(short_name="ATL06"), limit=10, prefetch=0
    )
    fresh._cached_results = [collection]
    fresh._exhausted = True
    fresh._materialized = True
    fresh._total_hits = 5

    with patch.object(earthaccess, "search_datasets", return_value=fresh):
        loaded = SearchResults.load(path, verify=True, limit=None)

    assert loaded.verification is not None
    assert loaded.verification["unchanged"] is True
    assert loaded._cached_results[0]["meta"]["concept-id"] == "C1-TEST"
