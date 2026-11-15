"""Unit tests for API integration with new query classes.

Tests the ability to use the new GranuleQuery and CollectionQuery objects
with search_data() and search_datasets() API functions.
"""

from unittest.mock import MagicMock, patch

import earthaccess
import pytest
from earthaccess.search.query import CollectionQuery, GranuleQuery
from earthaccess.search.results import SearchResults


class TestSearchDataWithGranuleQuery:
    """Test search_data() with new GranuleQuery objects."""

    def test_search_data_accepts_granule_query_object(self):
        """search_data() should accept a GranuleQuery object as first positional arg."""
        query = GranuleQuery().short_name("ATL03").temporal("2020-01", "2020-02")

        with patch("earthaccess.api.DataGranulesQuery") as mock_dg:
            mock_query = MagicMock()
            mock_query.hits.return_value = 0
            mock_query.get_all.return_value = []
            mock_query.parameters.return_value = mock_query
            mock_dg.return_value = mock_query

            results = earthaccess.search_data(query=query)

            # Returns SearchResults, not a list
            assert isinstance(results, SearchResults)
            # Should have called parameters with the CMR-converted dict
            mock_query.parameters.assert_called_once()
            call_kwargs = mock_query.parameters.call_args[1]
            assert "short_name" in call_kwargs
            assert call_kwargs["short_name"] == "ATL03"

    def test_search_data_query_with_count(self):
        """search_data() should respect count parameter when using query object."""
        query = GranuleQuery().short_name("ATL08")

        with patch("earthaccess.api.DataGranulesQuery") as mock_dg:
            mock_query = MagicMock()
            mock_query.hits.return_value = 100
            mock_query.get.return_value = [MagicMock() for _ in range(10)]
            mock_query.parameters.return_value = mock_query
            mock_dg.return_value = mock_query

            # Patch _fetch_page to avoid HTTP call during prefetch
            with patch.object(SearchResults, "_fetch_page", return_value=[]):
                results = earthaccess.search_data(query=query, count=10)

                # Returns SearchResults with limit
                assert isinstance(results, SearchResults)
                assert results.limit == 10
                # total() returns CMR hits
                assert results.total() == 100

    def test_search_data_query_with_bounding_box(self):
        """search_data() should pass clean bounding_box tuple from query object."""
        query = GranuleQuery().short_name("ATL03").bounding_box(-180, -90, 180, 90)

        with patch("earthaccess.api.DataGranulesQuery") as mock_dg:
            mock_query = MagicMock()
            mock_query.hits.return_value = 0
            mock_query.get_all.return_value = []
            mock_query.parameters.return_value = mock_query
            mock_dg.return_value = mock_query

            earthaccess.search_data(query=query)

            call_kwargs = mock_query.parameters.call_args[1]
            assert "bounding_box" in call_kwargs
            assert call_kwargs["bounding_box"] == (-180.0, -90.0, 180.0, 90.0)

    def test_search_data_query_with_polygon(self):
        """search_data() passes polygon as a list (regression for #polygon-query)."""
        query = (
            GranuleQuery()
            .short_name("ATL03")
            .polygon([(-10, 40), (-8, 40), (-8, 42), (-10, 42), (-10, 40)])
        )

        with patch("earthaccess.api.DataGranulesQuery") as mock_dg:
            mock_query = MagicMock()
            mock_query.hits.return_value = 0
            mock_query.get_all.return_value = []
            mock_query.parameters.return_value = mock_query
            mock_dg.return_value = mock_query

            earthaccess.search_data(query=query)

            call_kwargs = mock_query.parameters.call_args[1]
            assert call_kwargs["polygon"] == [
                (-10.0, 40.0),
                (-8.0, 40.0),
                (-8.0, 42.0),
                (-10.0, 42.0),
                (-10.0, 40.0),
            ]

    def test_search_data_query_with_temporal(self):
        """search_data() should pass temporal parameters from query object."""
        query = GranuleQuery().short_name("ATL03").temporal("2020-01-01", "2020-12-31")

        with patch("earthaccess.api.DataGranulesQuery") as mock_dg:
            mock_query = MagicMock()
            mock_query.hits.return_value = 0
            mock_query.get_all.return_value = []
            mock_query.parameters.return_value = mock_query
            mock_dg.return_value = mock_query

            earthaccess.search_data(query=query)

            call_kwargs = mock_query.parameters.call_args[1]
            assert "temporal" in call_kwargs

    def test_search_data_kwargs_still_works(self):
        """search_data() should still accept kwargs for backward compatibility."""
        with patch("earthaccess.api.DataGranulesQuery") as mock_dg:
            mock_query = MagicMock()
            mock_query.hits.return_value = 0
            mock_query.get_all.return_value = []
            mock_query.parameters.return_value = mock_query
            mock_dg.return_value = mock_query

            earthaccess.search_data(short_name="ATL03", temporal=("2020-01", "2020-02"))

            call_kwargs = mock_query.parameters.call_args[1]
            assert call_kwargs["short_name"] == "ATL03"
            assert call_kwargs["temporal"] == ("2020-01", "2020-02")

    def test_search_data_query_and_kwargs_raises_error(self):
        """search_data() should raise error if both query and kwargs provided."""
        query = GranuleQuery().short_name("ATL03")

        with pytest.raises(
            ValueError, match="Cannot use both 'query' parameter and keyword arguments"
        ):
            earthaccess.search_data(query=query, short_name="ATL08")


class TestSearchDatasetsWithCollectionQuery:
    """Test search_datasets() with new CollectionQuery objects."""

    def test_search_datasets_accepts_collection_query_object(self):
        """search_datasets() should accept a CollectionQuery object."""
        query = CollectionQuery().keyword("temperature").cloud_hosted(True)

        with patch("earthaccess.api.DataCollectionsQuery") as mock_dc:
            mock_query = MagicMock()
            mock_query.hits.return_value = 0
            mock_query.get_all.return_value = []
            mock_query.parameters.return_value = mock_query
            mock_dc.return_value = mock_query

            results = earthaccess.search_datasets(query=query)

            # Returns SearchResults, not a list
            assert isinstance(results, SearchResults)
            mock_query.parameters.assert_called_once()
            call_kwargs = mock_query.parameters.call_args[1]
            assert "keyword" in call_kwargs
            assert call_kwargs["keyword"] == "temperature"

    def test_search_datasets_query_with_count(self):
        """search_datasets() should respect count parameter with query object."""
        query = CollectionQuery().keyword("ocean")

        with patch("earthaccess.api.DataCollectionsQuery") as mock_dc:
            mock_query = MagicMock()
            mock_query.hits.return_value = 100
            mock_query.get.return_value = [MagicMock() for _ in range(5)]
            mock_query.parameters.return_value = mock_query
            mock_dc.return_value = mock_query

            # Patch _fetch_page to avoid HTTP call during prefetch
            with patch.object(SearchResults, "_fetch_page", return_value=[]):
                results = earthaccess.search_datasets(query=query, count=5)

            # Returns SearchResults with limit
            assert isinstance(results, SearchResults)
            assert results.limit == 5
            # total() returns CMR hits
            assert results.total() == 100

    def test_search_datasets_query_with_daac(self):
        """search_datasets() should pass daac from query object."""
        query = CollectionQuery().daac("NSIDC")

        with patch("earthaccess.api.DataCollectionsQuery") as mock_dc:
            mock_query = MagicMock()
            mock_query.hits.return_value = 0
            mock_query.get_all.return_value = []
            mock_query.parameters.return_value = mock_query
            mock_dc.return_value = mock_query

            earthaccess.search_datasets(query=query)

            call_kwargs = mock_query.parameters.call_args[1]
            assert "daac" in call_kwargs
            assert call_kwargs["daac"] == "NSIDC"

    def test_search_datasets_kwargs_still_works(self):
        """search_datasets() should still accept kwargs for backward compatibility."""
        with patch("earthaccess.api.DataCollectionsQuery") as mock_dc:
            mock_query = MagicMock()
            mock_query.hits.return_value = 0
            mock_query.get_all.return_value = []
            mock_query.parameters.return_value = mock_query
            mock_dc.return_value = mock_query

            earthaccess.search_datasets(keyword="ice", cloud_hosted=True)

            call_kwargs = mock_query.parameters.call_args[1]
            assert call_kwargs["keyword"] == "ice"
            assert call_kwargs["cloud_hosted"] is True

    def test_search_datasets_query_and_kwargs_raises_error(self):
        """search_datasets() should raise error if both query and kwargs provided."""
        query = CollectionQuery().keyword("ocean")

        with pytest.raises(
            ValueError, match="Cannot use both 'query' parameter and keyword arguments"
        ):
            earthaccess.search_datasets(query=query, keyword="ice")


class TestQueryToStac:
    """Test that query objects can generate STAC-compatible output."""

    def test_granule_query_to_stac(self):
        """GranuleQuery.to_stac() should produce valid STAC parameters."""
        query = (
            GranuleQuery()
            .short_name("ATL03")
            .temporal("2020-01-01", "2020-12-31")
            .bounding_box(-180, -90, 180, 90)
        )

        stac_params = query.to_stac()

        assert "collections" in stac_params
        assert stac_params["collections"] == ["ATL03"]
        assert "datetime" in stac_params
        assert "bbox" in stac_params
        assert stac_params["bbox"] == [-180.0, -90.0, 180.0, 90.0]

    def test_collection_query_to_stac(self):
        """CollectionQuery.to_stac() should produce valid STAC parameters."""
        query = (
            CollectionQuery()
            .keyword("temperature")
            .temporal("2020-01-01", "2020-12-31")
        )

        stac_params = query.to_stac()

        assert "q" in stac_params  # keyword maps to 'q' in STAC
        assert stac_params["q"] == "temperature"
        assert "datetime" in stac_params


class TestQueryValidation:
    """Test query validation before search."""

    def test_granule_query_validates_before_search(self):
        """Invalid GranuleQuery should raise error when used with search_data()."""
        # Spatial query without collection limiter
        query = GranuleQuery().bounding_box(-180, -90, 180, 90)

        with pytest.raises(ValueError, match="require"):
            earthaccess.search_data(query=query)

    def test_valid_granule_query_passes_validation(self):
        """Valid GranuleQuery should not raise validation errors."""
        query = GranuleQuery().short_name("ATL03").bounding_box(-180, -90, 180, 90)

        with patch("earthaccess.api.DataGranulesQuery") as mock_dg:
            mock_query = MagicMock()
            mock_query.hits.return_value = 0
            mock_query.get_all.return_value = []
            mock_query.parameters.return_value = mock_query
            mock_dg.return_value = mock_query

            # Should not raise
            earthaccess.search_data(query=query)
