"""TDD Tests for SearchResults class and API integration.

Tests the lazy pagination wrapper that enables memory-efficient
iteration through large CMR result sets.
"""

from unittest.mock import Mock, patch

import pytest
from earthaccess.search import DataGranule, SearchResults


def create_mock_query(
    hits: int = 0, get_return: list | None = None, page_size: int = 2000
) -> Mock:
    """Create a properly configured mock query for SearchResults tests.

    Since SearchResults now prefetches results, the mock needs:
    - headers: dict for HTTP headers
    - get(count): method to return results
    - hits(): method to return total hits count
    """
    mock_query = Mock()
    mock_query.headers = {}
    mock_query.hits.return_value = hits
    mock_query.get.return_value = get_return if get_return is not None else []
    mock_query._page_size = page_size
    return mock_query


class FakeGranule:
    """A lightweight stand-in for DataGranule carrying only its result index.

    Streaming tests only inspect ``_idx`` and never call granule methods, so
    constructing real ``Mock(spec=DataGranule)`` objects (which are ~100x
    slower) is unnecessary.
    """

    __slots__ = ("_idx",)

    def __init__(self, idx: int):
        self._idx = idx


class PagedQuery:
    """A stateless query serving CMR-style pages keyed by ``search_after``.

    ``search_after`` is the last index already served (or ``None`` to start at
    the first result), matching how real CMR paginates.
    """

    def __init__(self, total_items: int, page_size: int = 2000):
        self.total_items = total_items
        self.page_size = page_size
        self.headers: dict[str, str] = {}
        self.calls: list[int] = []

    def hits(self) -> int:
        return self.total_items

    def page(self, page_size: int, search_after: str | None = None) -> list:
        """Return the page of fake granules after ``search_after``."""
        self.calls.append(page_size)
        start = 0 if search_after is None else int(search_after) + 1
        end = min(start + page_size, self.total_items)
        if start >= end:
            return []
        return [FakeGranule(i) for i in range(start, end)]


def paged_search_results(pager: PagedQuery, limit=None, prefetch: int = 0):
    """Build a SearchResults whose _fetch_page pulls from a PagedQuery.

    Returns ``(results, fetch_mock)`` where ``fetch_mock`` simulates the CMR
    pagination protocol (sets ``_last_search_after`` to the last index served).
    """
    from earthaccess.search import SearchResults

    query = pager
    # Construct without network; prefetch will be driven through _fetch_page.
    results = SearchResults.__new__(SearchResults)
    SearchResults.__init__(results, query=query, limit=limit, prefetch=0)
    results._cached_results = []
    results._exhausted = False
    results._materialized = False
    results._total_hits = pager.total_items

    def _fetch(page_size: int, search_after: str | None = None) -> list:
        page = pager.page(page_size, search_after)
        if page:
            last_index = int(search_after or -1) + len(page)
            results._last_search_after = str(last_index)
        return page

    if prefetch > 0:
        count = prefetch if limit is None else min(prefetch, limit)
        with patch.object(SearchResults, "_fetch_page", side_effect=_fetch):
            initial = _fetch(count)
            results._cached_results.extend(initial)
            results._initial_search_after = results._last_search_after
            if len(initial) < count:
                results._exhausted = True
                results._materialized = True

    return results, _fetch


class TestSearchResultsCreation:
    """Test SearchResults instantiation."""

    def test_create_with_query(self) -> None:
        """Test creating SearchResults with a query object."""
        mock_query = create_mock_query()
        results = SearchResults(mock_query, prefetch=0)

        assert results.query is mock_query
        assert results.limit is None
        assert results._cached_results == []
        assert results._exhausted is False

    def test_create_with_limit(self) -> None:
        """Test creating SearchResults with a limit."""
        mock_query = create_mock_query()
        results = SearchResults(mock_query, limit=100, prefetch=0)

        assert results.limit == 100

    def test_repr_before_fetch(self) -> None:
        """Test string representation before any fetches."""
        mock_query = create_mock_query()
        results = SearchResults(mock_query, prefetch=0)

        assert "SearchResults" in repr(results)
        assert "total=?" in repr(results)
        assert "loaded=0" in repr(results)

    def test_prefetch_loads_initial_results(self) -> None:
        """Test that prefetch loads initial results on creation."""
        mock_granules = [Mock(spec=DataGranule) for _ in range(5)]
        mock_query = create_mock_query(hits=100, get_return=mock_granules)

        # Mock _fetch_page to avoid HTTP calls
        with patch.object(SearchResults, "_fetch_page") as mock_fetch:
            mock_fetch.return_value = mock_granules
            results = SearchResults(mock_query, prefetch=20)

            # Should have prefetched the available results
            assert len(results._cached_results) == 5
            mock_fetch.assert_called_once()

    def test_default_prefetch_is_20(self) -> None:
        """The default prefetch is 20 and the default page size is 2000."""
        mock_query = create_mock_query()
        with patch.object(SearchResults, "_fetch_page", return_value=[]):
            results = SearchResults(mock_query, prefetch=20, page_size=2000)

        assert results._window == 2000

    def test_page_size_is_configurable(self) -> None:
        """page_size controls the streaming window."""
        mock_query = create_mock_query()
        results = SearchResults(mock_query, prefetch=0, page_size=500)
        assert results._window == 500

    def test_page_size_rejects_out_of_range(self) -> None:
        """page_size must be between 1 and 2000."""
        mock_query = create_mock_query()
        with pytest.raises(ValueError, match="page_size must be between 1 and 2000"):
            SearchResults(mock_query, prefetch=0, page_size=0)
        with pytest.raises(ValueError, match="page_size must be between 1 and 2000"):
            SearchResults(mock_query, prefetch=0, page_size=2001)


class TestSearchResultsLen:
    """Test __len__ behavior - now returns cached count, not total hits."""

    def test_len_returns_cached_count(self) -> None:
        """Test that __len__ returns the number of cached results."""
        mock_query = create_mock_query()
        results = SearchResults(mock_query, prefetch=0)

        # Initially no results cached
        assert len(results) == 0

        # After caching some results
        results._cached_results = [Mock() for _ in range(25)]
        assert len(results) == 25

    def test_total_calls_hits_on_query(self) -> None:
        """Test that total() calls hits() on the query object."""
        mock_query = create_mock_query(hits=1000)
        results = SearchResults(mock_query, prefetch=0)

        total = results.total()

        mock_query.hits.assert_called_once()
        assert total == 1000

    def test_total_caches_result(self) -> None:
        """Test that total() caches the result."""
        mock_query = create_mock_query(hits=500)
        results = SearchResults(mock_query, prefetch=0)

        # Call total twice
        results.total()
        results.total()

        # hits() should only be called once
        mock_query.hits.assert_called_once()

    def test_hits_is_alias_for_total(self) -> None:
        """Test that hits() is an alias for total()."""
        mock_query = create_mock_query(hits=750)
        results = SearchResults(mock_query, prefetch=0)

        # hits() should return same value as total()
        hits_result = results.hits()
        assert hits_result == 750
        assert hits_result == results.total()


class TestSearchResultsIteration:
    """Iterating a fully-materialized result set replays the cache."""

    @pytest.mark.parametrize("n", [0, 1, 3, 10])
    def test_replays_materialized_cache(self, n):
        """list(results) returns every cached item, in order, repeatedly."""
        results = SearchResults(create_mock_query(), prefetch=0)
        results._materialized = True
        results._cached_results = [Mock(spec=DataGranule) for _ in range(n)]

        assert list(results) == results._cached_results
        assert list(results) == results._cached_results  # re-entrant

    def test_limit_does_not_truncate_materialized_cache(self):
        """Limit is enforced at fetch time, not on a cached replay."""
        results = SearchResults(create_mock_query(hits=10), limit=100, prefetch=0)
        results._materialized = True
        results._cached_results = [Mock(spec=DataGranule) for _ in range(10)]

        assert len(list(results)) == 10


class TestSearchResultsToStac:
    """Test SearchResults.to_stac() returns pystac objects."""

    def test_to_stac_returns_pystac_items(self) -> None:
        """Test that to_stac() returns pystac Items with HTTPS hrefs by default."""
        import pystac
        from earthaccess.search.results import DataGranule

        granule = DataGranule(
            {
                "umm": {
                    "GranuleUR": "HLS.L30.T10SEG.2023001T185019.v2.0",
                    "CollectionReference": {"ShortName": "HLSL30"},
                    "TemporalExtent": {"SingleDateTime": "2023-01-01T00:00:00Z"},
                    "SpatialExtent": {},
                    "RelatedUrls": [
                        {
                            "Type": "GET DATA VIA DIRECT ACCESS",
                            "URL": "s3://lp-prod/HLS.L30.T10SEG.2023001T185019.v2.0.B02.tif",
                        },
                        {
                            "Type": "GET DATA",
                            "URL": "https://data.lpdaac.earthdatacloud.nasa.gov/lp-prod/HLS.L30.T10SEG.2023001T185019.v2.0.B02.tif",
                        },
                    ],
                },
                "meta": {"concept-id": "G123-LP", "provider-id": "LPCLOUD"},
            },
            cloud_hosted=True,
        )
        mock_query = create_mock_query(hits=1)
        results = SearchResults(mock_query, prefetch=0)
        results._cached_results = [granule]

        stac_items = results.to_stac()

        assert len(stac_items) == 1
        assert isinstance(stac_items[0], pystac.Item)
        # Default access prefers HTTPS so stac_load works outside AWS
        assert stac_items[0].assets["B02"].href.startswith("https://")

    def test_to_stac_access_s3(self) -> None:
        """Test that access="s3" makes S3 the primary asset href."""
        from earthaccess.search.results import DataGranule

        granule = DataGranule(
            {
                "umm": {
                    "GranuleUR": "g1",
                    "CollectionReference": {"ShortName": "HLSL30"},
                    "SpatialExtent": {},
                    "RelatedUrls": [
                        {"Type": "GET DATA VIA DIRECT ACCESS", "URL": "s3://b/x.tif"},
                        {"Type": "GET DATA", "URL": "https://data/x.tif"},
                    ],
                },
                "meta": {"concept-id": "G1-LP", "provider-id": "LPCLOUD"},
            },
            cloud_hosted=True,
        )
        mock_query = create_mock_query(hits=1)
        results = SearchResults(mock_query, prefetch=0)
        results._cached_results = [granule]

        stac_items = results.to_stac(access="s3")
        assert stac_items[0].assets["x"].href.startswith("s3://")


class TestAPIIntegrationWithSearchResults:
    """Test that API functions return SearchResults."""

    def test_search_data_returns_search_results(self) -> None:
        """Test that search_data returns SearchResults."""
        with (
            patch("earthaccess.api.DataGranulesQuery") as mock_dg,
            patch.object(SearchResults, "_fetch_page", return_value=[]),
        ):
            mock_query = Mock()
            mock_query.hits.return_value = 0
            mock_query.get_all.return_value = []
            mock_query.parameters.return_value = mock_query
            mock_query.headers = {}
            mock_dg.return_value = mock_query

            import earthaccess

            # This should return SearchResults
            result = earthaccess.search_data(short_name="TEST")

            assert isinstance(result, SearchResults)

    def test_search_datasets_returns_search_results(self) -> None:
        """Test that search_datasets returns SearchResults."""
        with (
            patch("earthaccess.api.DataCollectionsQuery") as mock_dc,
            patch.object(SearchResults, "_fetch_page", return_value=[]),
        ):
            mock_query = Mock()
            mock_query.hits.return_value = 0
            mock_query.get_all.return_value = []
            mock_query.parameters.return_value = mock_query
            mock_query.headers = {}
            mock_dc.return_value = mock_query

            import earthaccess

            result = earthaccess.search_datasets(keyword="TEST")

            assert isinstance(result, SearchResults)

    def test_search_results_exportable_from_earthaccess(self) -> None:
        """Test that SearchResults can be imported from earthaccess package."""
        from earthaccess import SearchResults

        assert SearchResults is not None
        # Verify it's the same class
        from earthaccess.search import SearchResults as ResultsSearchResults

        assert SearchResults is ResultsSearchResults


class TestSearchResultsCaching:
    """repr()/loaded/offset/source expose the stream position."""

    def test_repr_includes_loaded_offset_source(self) -> None:
        """repr() reports total, loaded, offset, and source."""
        results = SearchResults(create_mock_query(hits=100), prefetch=0)
        results._total_hits = 100
        results._cached_results = [Mock() for _ in range(25)]
        results._offset = 25

        repr_str = repr(results)

        assert "total=100" in repr_str
        assert "loaded=25" in repr_str
        assert "offset=25" in repr_str
        assert "source=" in repr_str

    def test_loaded_offset_source_properties(self) -> None:
        """loaded/offset/source are exposed as read-only properties."""
        results = SearchResults(create_mock_query(hits=100), prefetch=0)
        results._cached_results = [Mock() for _ in range(25)]
        results._offset = 25

        assert results.loaded == 25
        assert results.offset == 25
        assert results.source  # CMR PROD / UAT / file path

    def test_source_reflects_loaded_file_path(self) -> None:
        """source() reports the payload file when results were loaded."""
        results = SearchResults(create_mock_query(), prefetch=0)
        results._source = "saved_search.gz"

        assert results.source == "saved_search.gz"


class TestStreamingIteration:
    """Iteration streams; the internal cache stays bounded to a window.

    Regression tests for: iterating a very large result set must not retain
    every item in memory (bounded cache window), while explicit
    materialization (list()/all()) still returns everything, and reset() /
    a fresh search return to the initial prefetch.
    """

    def test_iteration_keeps_bounded_window(self):
        """After streaming N items, the cache holds only the last page."""
        pager = PagedQuery(total_items=6_000, page_size=2000)
        results, fetch = paged_search_results(pager)

        with patch.object(SearchResults, "_fetch_page", side_effect=fetch):
            count = 0
            for _granule in results:
                count += 1

        assert count == 6_000  # yielded everything
        # Cache is bounded to one page, not the whole set
        assert len(results._cached_results) <= 2000
        assert len(results) <= 2000
        assert results._materialized is False

    @pytest.mark.parametrize("consumed", [1, 19, 20, 25, 2_000, 2_005, 18_000, 20_000])
    def test_offset_equals_results_consumed(self, consumed):
        """offset() equals how many results were consumed, across page boundaries."""
        pager = PagedQuery(total_items=100_000, page_size=2000)
        results, fetch = paged_search_results(pager, prefetch=20)

        with patch.object(SearchResults, "_fetch_page", side_effect=fetch):
            for _i, _granule in enumerate(results):
                if _i + 1 >= consumed:
                    break

        assert results.offset == consumed

    def test_offset_and_repr_after_20k_like_user_scenario(self):
        """Iterating 20k leaves loaded=2000 (the last page) and offset=20000."""
        from earthaccess.search import GranuleResults

        pager = PagedQuery(total_items=2_500_923, page_size=2000)
        query = pager

        def _fetch(page_size: int, search_after: str | None = None) -> list:
            page = pager.page(page_size, search_after)
            if page:
                last_index = int(search_after or -1) + len(page)
                results._last_search_after = str(last_index)
            return page

        results = GranuleResults.__new__(GranuleResults)
        SearchResults.__init__(results, query=query, limit=None, prefetch=0)
        results._total_hits = pager.total_items

        with patch.object(SearchResults, "_fetch_page", side_effect=_fetch):
            for _i, _granule in enumerate(results):
                if _i + 1 >= 20_000:
                    break

        # The cache holds the last page fetched (items 18000..20000).
        assert results.loaded == 2_000
        assert results.offset == 20_000
        assert results._cache_start == 18_000
        assert getattr(results._cached_results[0], "_idx") == 18_000
        assert repr(results) == (
            "GranuleResults(total=2500923, loaded=2000, offset=20000, source=CMR PROD)"
        )

    def test_list_still_returns_everything(self):
        """list(results) returns all items even though the cache is bounded."""
        pager = PagedQuery(total_items=10_000, page_size=2000)
        results, fetch = paged_search_results(pager)

        with patch.object(SearchResults, "_fetch_page", side_effect=fetch):
            items = list(results)

        assert len(items) == 10_000
        assert len(results._cached_results) <= 2000

    def test_all_materializes_fully(self):
        """all() returns and caches the complete result set."""
        pager = PagedQuery(total_items=10_000, page_size=2000)
        results, fetch = paged_search_results(pager)

        with patch.object(SearchResults, "_fetch_page", side_effect=fetch):
            items = results.all()

        assert len(items) == 10_000
        assert len(results._cached_results) == 10_000
        assert results._materialized is True

    def test_reset_returns_to_prefetch(self):
        """reset() clears materialized results back to the initial prefetch."""
        pager = PagedQuery(total_items=100_000, page_size=2000)
        results, fetch = paged_search_results(pager, prefetch=20)

        with patch.object(SearchResults, "_fetch_page", side_effect=fetch):
            # Prefetch happened at construction (20), then stream everything
            results.reset()  # back to 20 prefetched
            assert len(results._cached_results) == 20

            results.reset(prefetch=0)
            assert len(results._cached_results) == 0

    def test_iteration_is_repeatable(self):
        """A second streaming pass re-yields the full result set."""
        pager = PagedQuery(total_items=5_000, page_size=2000)
        results, fetch = paged_search_results(pager, prefetch=20)

        with patch.object(SearchResults, "_fetch_page", side_effect=fetch):
            first = sum(1 for _ in results)
            second = sum(1 for _ in results)

        assert first == 5_000
        assert second == 5_000  # stale window must not truncate a re-scan

    def test_iteration_leaves_last_page_in_cache(self):
        """After a full stream, the cache holds the last page (a sliding window)."""
        pager = PagedQuery(total_items=10_000, page_size=2000)
        results, fetch = paged_search_results(pager, prefetch=20)

        with patch.object(SearchResults, "_fetch_page", side_effect=fetch):
            count = sum(1 for _ in results)

        assert count == 10_000
        # The cache is the last page fetched: 5 pages of 2000, the final one
        # starts at index 8000 and holds the remaining 2000 items.
        assert results._cache_start == 8_000
        assert len(results._cached_results) == 2_000
        assert results.offset == 10_000
        # The first cached item is the last page's first item (index 8000)
        assert getattr(results._cached_results[0], "_idx") == 8_000

    def test_index_after_iteration_returns_true_first_item(self):
        """Random access after a stream rebuilds a prefix and returns item 0."""
        pager = PagedQuery(total_items=10_000, page_size=2000)
        results, fetch = paged_search_results(pager, prefetch=20)

        with patch.object(SearchResults, "_fetch_page", side_effect=fetch):
            sum(1 for _ in results)  # full stream leaves a window
            first = results[0]
            # _ensure_cached rebuilt a prefix from the start
            assert results._cache_start == 0
            assert getattr(first, "_idx") == 0

    def test_fresh_search_starts_at_prefetch(self):
        """A freshly constructed SearchResults holds the prefetch window."""
        pager = PagedQuery(total_items=100_000, page_size=2000)
        results, fetch = paged_search_results(pager, prefetch=20)

        with patch.object(SearchResults, "_fetch_page", side_effect=fetch):
            assert len(results._cached_results) == 20
            assert results._exhausted is False
            assert results._materialized is False
