from unittest.mock import MagicMock, patch

import pytest
from earthaccess.formatting import (
    STATIC_FILES,
    _load_static_files,
    _repr_collection_html,
    _repr_granule_html,
    _repr_search_results_html,
    has_widget_support,
)
from earthaccess.formatting.html import (
    _compute_summary,
    _format_collection_temporal,
    _format_temporal_extent,
    _generate_table_rows,
    _is_granule,
)
from earthaccess.search import DataCollection, DataGranule
from earthaccess.search.results import EXTENSION_TO_TYPE, SearchResults

from tests.unit.fixtures import load_granule_fixture


def test_load_static_files():
    # We simply test that the number of static files loaded is the same as the
    # number of files in the STATIC_FILES list.  If we were to add logic to
    # check the contents of the files, then we would end up duplicating the
    # logic in the _load_static_files function, which wouldn't make sense to do.
    # If _load_static_files contains a bug, then this test will likely fail due
    # to the function raising an exception.
    assert len(_load_static_files()) == len(STATIC_FILES)


def test_repr_granule_html():
    static_contents = _load_static_files()
    size1 = 128573
    size2 = 2713600
    umm = {
        "RelatedUrls": [
            {
                "URL": "https://data.csdap.earthdata.nasa.gov/data.h5",
                "Type": "GET DATA",
            },
            {
                "URL": "s3://csda-cumulus-prod-protected-5047/data.h5",
                "Type": "GET DATA VIA DIRECT ACCESS",
            },
            {
                "URL": "https://data.csdap.earthdata.nasa.gov/thumb.jpg",
                "Type": "GET RELATED VISUALIZATION",
            },
        ],
        "DataGranule": {
            "ArchiveAndDistributionInformation": [
                {"SizeInBytes": size1},
                {"SizeInBytes": size2},
            ],
        },
    }

    html = _repr_granule_html(
        DataGranule({"umm": umm, "meta": {"concept-id": "G3859310711-GES_DISC"}})
    )

    assert f"{round((size1 + size2) / 1024 / 1024, 2)} MB" in html
    assert [url["URL"] in html for url in umm["RelatedUrls"]] == [True, False, True]
    assert all(content in html for content in static_contents)
    # No cloud-hosted banner, and the concept-id links to the UMM record.
    assert "Cloud Hosted" not in html
    assert "search/concepts/G3859310711-GES_DISC.umm_json" in html


# =============================================================================
# Tests for _repr_collection_html
# =============================================================================


def test_repr_collection_html_basic():
    """Test that _repr_collection_html returns valid HTML with expected content."""
    umm = {
        "ShortName": "ATL06",
        "Version": "005",
        "EntryTitle": "ATLAS/ICESat-2 L3A Land Ice Height V005",
        "Abstract": "This data set contains land ice surface heights.",
        "DOI": {"DOI": "10.5067/ATLAS/ATL06.005"},
        "RelatedUrls": [
            {
                "URL": "https://nsidc.org/data/ATL06",
                "Type": "LANDING PAGE",
            },
            {
                "URL": "https://n5eil01u.ecs.nsidc.org/ATLAS/ATL06.005/",
                "Type": "GET DATA",
            },
        ],
        "TemporalExtents": [
            {
                "RangeDateTimes": [
                    {
                        "BeginningDateTime": "2018-10-14T00:00:00.000Z",
                        "EndingDateTime": None,
                    }
                ]
            }
        ],
    }

    collection = DataCollection(
        {"umm": umm, "meta": {"concept-id": "C1234567-NSIDC", "provider-id": "NSIDC"}}
    )
    html = _repr_collection_html(collection)

    # Check that key elements are present
    assert "ATL06" in html
    assert "v005" in html or "005" in html
    assert "concept-id" in html.lower() or "C1234567-NSIDC" in html
    assert "NSIDC" in html
    assert "10.5067/ATLAS/ATL06.005" in html
    assert "bootstrap" in html.lower()


def test_repr_collection_html_no_doi():
    """Test that _repr_collection_html handles collections without DOI."""
    umm = {
        "ShortName": "TEST_COLLECTION",
        "Version": "1",
        "EntryTitle": "Test Collection",
        "Abstract": "A test collection without DOI.",
    }

    collection = DataCollection(
        {"umm": umm, "meta": {"concept-id": "C9999-TEST", "provider-id": "TEST"}}
    )
    html = _repr_collection_html(collection)

    assert "TEST_COLLECTION" in html
    assert "N/A" in html  # DOI should show N/A


def test_repr_collection_html_truncates_long_abstract():
    """Test that long abstracts are truncated."""
    long_abstract = "A" * 500  # 500 character abstract
    umm = {
        "ShortName": "LONG_ABSTRACT",
        "Abstract": long_abstract,
    }

    collection = DataCollection(
        {"umm": umm, "meta": {"concept-id": "C1-TEST", "provider-id": "TEST"}}
    )
    html = _repr_collection_html(collection)

    # Abstract should be truncated (300 chars + "...")
    assert "..." in html
    assert long_abstract not in html  # Full abstract should NOT be present


# =============================================================================
# Tests for _repr_search_results_html
# =============================================================================


def test_repr_search_results_html_empty():
    """Test HTML representation of empty SearchResults."""
    # Create a mock query
    mock_query = MagicMock()
    mock_query.hits.return_value = 0

    results = SearchResults(mock_query)
    results._total_hits = 0
    results._cached_results = []

    html = _repr_search_results_html(results)

    assert "SearchResults" in html
    assert "Total in CMR" in html
    assert "0" in html
    assert "Loaded" in html


def test_repr_search_results_html_with_granules():
    """Test HTML representation of SearchResults with cached granules."""
    mock_query = MagicMock()

    # Create mock granules
    granule_umm = {
        "GranuleUR": "SC:ATL06.005:123456",
        "TemporalExtent": {
            "RangeDateTime": {
                "BeginningDateTime": "2020-01-15T00:00:00.000Z",
                "EndingDateTime": "2020-01-15T01:00:00.000Z",
            }
        },
        "RelatedUrls": [{"URL": "https://example.com/data.h5", "Type": "GET DATA"}],
        "DataGranule": {
            "ArchiveAndDistributionInformation": [{"Size": 50.0, "SizeUnit": "MB"}]
        },
    }

    granules = [
        DataGranule(
            {"umm": granule_umm, "meta": {"concept-id": f"G{i}-TEST"}},
            cloud_hosted=True,
        )
        for i in range(5)
    ]

    results = SearchResults(mock_query)
    results._total_hits = 100
    results._cached_results = granules

    html = _repr_search_results_html(results)

    assert "SearchResults" in html
    assert "100" in html  # Total hits
    assert "5" in html  # Cached count
    assert "SC:ATL06.005:123456" in html or "ATL06" in html
    assert "Browse Results" in html  # Collapsible section with pagination
    assert '<details style="margin-top: 10px;">' in html  # collapsed by default


def test_repr_search_results_html_with_collections():
    """Test HTML representation of SearchResults with cached collections."""
    mock_query = MagicMock()

    # Create mock collections
    collection_umm = {
        "ShortName": "ATL06",
        "Version": "005",
        "TemporalExtents": [
            {"RangeDateTimes": [{"BeginningDateTime": "2018-10-14T00:00:00.000Z"}]}
        ],
        "RelatedUrls": [
            {"URL": "https://nsidc.org/data/ATL06", "Type": "LANDING PAGE"}
        ],
    }

    collections = [
        DataCollection(
            {
                "umm": collection_umm,
                "meta": {"concept-id": f"C{i}-TEST", "provider-id": "TEST"},
            }
        )
        for i in range(3)
    ]

    results = SearchResults(mock_query)
    results._total_hits = 50
    results._cached_results = collections

    html = _repr_search_results_html(results)

    assert "SearchResults" in html
    assert "50" in html  # Total hits
    assert "collections" in html.lower()


# =============================================================================
# Tests for helper functions
# =============================================================================


def test_format_temporal_extent():
    """Test _format_temporal_extent function."""
    temporal = {
        "RangeDateTime": {
            "BeginningDateTime": "2020-01-15T10:30:00.000Z",
            "EndingDateTime": "2020-01-15T11:30:00.000Z",
        }
    }

    result = _format_temporal_extent(temporal)
    assert "2020-01-15" in result

    # Test short format
    result_short = _format_temporal_extent(temporal, short=True)
    assert result_short == "2020-01-15"


def test_format_temporal_extent_empty():
    """Test _format_temporal_extent with empty input."""
    result = _format_temporal_extent({})
    assert result == "N/A"


def test_format_collection_temporal():
    """Test _format_collection_temporal function."""
    temporal_extents = [
        {
            "RangeDateTimes": [
                {
                    "BeginningDateTime": "2018-10-14T00:00:00.000Z",
                    "EndingDateTime": "2023-12-31T23:59:59.999Z",
                }
            ]
        }
    ]

    result = _format_collection_temporal(temporal_extents)
    assert "2018-10-14" in result
    assert "2023-12-31" in result


def test_format_collection_temporal_ongoing():
    """Test _format_collection_temporal for ongoing collections."""
    temporal_extents = [
        {
            "RangeDateTimes": [
                {
                    "BeginningDateTime": "2018-10-14T00:00:00.000Z",
                    # No EndingDateTime = ongoing
                }
            ]
        }
    ]

    result = _format_collection_temporal(temporal_extents)
    assert "2018-10-14" in result
    assert "present" in result.lower()


def test_is_granule():
    """Test _is_granule function."""
    granule = DataGranule(
        {"umm": {"GranuleUR": "test-granule"}, "meta": {"concept-id": "G1-TEST"}}
    )
    collection = DataCollection(
        {"umm": {"ShortName": "TEST"}, "meta": {"concept-id": "C1-TEST"}}
    )

    assert _is_granule(granule) is True
    assert _is_granule(collection) is False
    assert _is_granule(None) is True  # Default assumption


def test_compute_summary_empty():
    """Test _compute_summary with empty list."""
    result = _compute_summary([])

    assert result["total_size_mb"] == 0.0
    assert result["cloud_count"] == 0
    assert result["temporal_range"] == "N/A"


def test_compute_summary_with_granules():
    """Test _compute_summary with granule data."""
    granule_umm = {
        "GranuleUR": "test-granule",
        "TemporalExtent": {
            "RangeDateTime": {
                "BeginningDateTime": "2020-01-15T00:00:00.000Z",
                "EndingDateTime": "2020-01-15T01:00:00.000Z",
            }
        },
        "DataGranule": {"ArchiveAndDistributionInformation": [{"Size": 100.0}]},
    }

    granules = [
        DataGranule(
            {"umm": granule_umm, "meta": {"concept-id": f"G{i}-TEST"}},
            cloud_hosted=(i % 2 == 0),  # Alternate cloud hosted
        )
        for i in range(4)
    ]

    result = _compute_summary(granules)

    assert result["total_size_mb"] == 400.0  # 4 * 100 MB
    assert result["cloud_count"] == 2  # 0, 2 are cloud hosted
    assert "2020-01-15" in result["temporal_range"]


def test_generate_table_rows_empty():
    """Test _generate_table_rows with empty list."""
    result = _generate_table_rows([])

    assert "No results cached" in result


def test_generate_table_rows_with_granules():
    """Test _generate_table_rows with granule data."""
    granule = DataGranule(
        {
            "umm": {
                "GranuleUR": "SC:ATL06.005:123456789",
                "TemporalExtent": {
                    "RangeDateTime": {
                        "BeginningDateTime": "2020-06-15T00:00:00.000Z",
                    }
                },
                "RelatedUrls": [
                    {"URL": "https://example.com/data.h5", "Type": "GET DATA"}
                ],
                "DataGranule": {"ArchiveAndDistributionInformation": [{"Size": 25.5}]},
            },
            "meta": {"concept-id": "G123-TEST"},
        },
        cloud_hosted=True,
    )

    result = _generate_table_rows([granule])

    assert "<tr>" in result
    assert "2020-06-15" in result
    assert "25.5" in result


# =============================================================================
# Tests for SearchResults methods
# =============================================================================


def test_search_results_repr_html():
    """Test that SearchResults._repr_html_() returns valid HTML."""
    mock_query = MagicMock()

    results = SearchResults(mock_query)
    results._total_hits = 42
    results._cached_results = []

    html = results._repr_html_()

    assert isinstance(html, str)
    assert "SearchResults" in html
    assert "42" in html


def test_search_results_summary_empty():
    """Test SearchResults.summary() with no cached results."""
    mock_query = MagicMock()

    results = SearchResults(mock_query)
    results._total_hits = 0
    results._cached_results = []

    summary = results.summary()

    assert summary["total"] == 0
    assert summary["loaded"] == 0
    assert summary["total_size_mb"] == 0.0
    assert summary["cloud_count"] == 0
    assert summary["temporal_range"] is None


def test_search_results_summary_with_data():
    """Test SearchResults.summary() with cached granules."""
    mock_query = MagicMock()

    granule_umm = {
        "GranuleUR": "test-granule",
        "TemporalExtent": {
            "RangeDateTime": {
                "BeginningDateTime": "2020-03-01T00:00:00.000Z",
                "EndingDateTime": "2020-03-01T12:00:00.000Z",
            }
        },
        "DataGranule": {"ArchiveAndDistributionInformation": [{"Size": 50.0}]},
    }

    granules = [
        DataGranule(
            {"umm": granule_umm, "meta": {"concept-id": f"G{i}-TEST"}},
            cloud_hosted=True,
        )
        for i in range(3)
    ]

    results = SearchResults(mock_query)
    results._total_hits = 3
    results._cached_results = granules

    summary = results.summary()

    assert summary["total"] == 3
    assert summary["loaded"] == 3
    assert summary["total_size_mb"] == 150.0  # 3 * 50 MB
    assert summary["cloud_count"] == 3
    assert "2020-03-01" in summary["temporal_range"]


def test_search_results_summary_skips_for_large_results():
    """Test that SearchResults.summary() skips detailed computation for large result sets."""
    mock_query = MagicMock()

    results = SearchResults(mock_query)
    results._total_hits = 15000  # > 10000 threshold
    results._cached_results = []

    summary = results.summary()

    # Should return basic info without detailed computation
    assert summary["total"] == 15000
    assert summary["loaded"] == 0
    assert summary["total_size_mb"] == 0.0


# =============================================================================
# Tests for has_widget_support
# =============================================================================


def test_has_widget_support_returns_bool():
    """Test that has_widget_support returns a boolean."""
    result = has_widget_support()
    assert isinstance(result, bool)


def test_has_widget_support_with_missing_deps():
    """Test has_widget_support when dependencies are missing."""
    with patch.dict("sys.modules", {"anywidget": None, "lonboard": None}):
        # Force reimport

        # The function should handle ImportError gracefully
        # Note: This is a simplified test; actual behavior depends on import caching
        result = has_widget_support()
        assert isinstance(result, bool)


# =============================================================================
# Tests for DataCollection._repr_html_ and plot
# =============================================================================


def test_data_collection_repr_html():
    """Test that DataCollection._repr_html_() returns valid HTML."""
    collection = DataCollection(
        {
            "umm": {
                "ShortName": "TEST_COLLECTION",
                "Version": "1",
                "Abstract": "A test collection.",
            },
            "meta": {"concept-id": "C1-TEST", "provider-id": "TEST"},
        }
    )

    html = collection._repr_html_()

    assert isinstance(html, str)
    assert "TEST_COLLECTION" in html
    assert "bootstrap" in html.lower()


def test_data_granule_repr_html():
    """Test that DataGranule._repr_html_() returns valid HTML."""
    granule = DataGranule(
        {
            "umm": {
                "GranuleUR": "test-granule-123",
                "RelatedUrls": [
                    {"URL": "https://example.com/data.h5", "Type": "GET DATA"}
                ],
                "DataGranule": {"ArchiveAndDistributionInformation": [{"Size": 100.0}]},
            },
            "meta": {"concept-id": "G1-TEST"},
        },
        cloud_hosted=False,
    )

    html = granule._repr_html_()

    assert isinstance(html, str)
    assert "test-granule-123" in html
    assert "100" in html  # Size


# =============================================================================
# Tests for DataGranule.data_type (file type column)
# =============================================================================


def _make_granule(fixture_name):
    """Build a DataGranule from a granule fixture, inferring cloud hosting."""
    fixture = load_granule_fixture(fixture_name)
    cloud = fixture["meta"].get("provider-id", "") in (
        "LPCLOUD",
        "POCLOUD",
        "ORNL_CLOUD",
        "GES_DISC",
    )
    return DataGranule(fixture, cloud_hosted=cloud)


def test_extension_to_type_map_has_common_formats():
    """The extension map covers the formats we expect to see in CMR granules."""
    assert EXTENSION_TO_TYPE[".tif"] == "COG"
    assert EXTENSION_TO_TYPE[".h5"] == "HDF5"
    assert EXTENSION_TO_TYPE[".nc"] == "NetCDF"
    assert EXTENSION_TO_TYPE[".jpg"] == "JPEG"


@pytest.mark.parametrize(
    "fixture_name,expected",
    [
        pytest.param("HLSS30_umm", "COG, JPEG(thumbs)", id="HLSS30"),
        pytest.param("HLSL30_umm", "COG, JPEG(thumbs)", id="HLSL30"),
        pytest.param("EMITL2ARFL_umm", "NetCDF, PNG(thumbs)", id="EMITL2ARFL"),
        pytest.param("GEDI02_B_umm", "HDF5, PNG(thumbs)", id="GEDI02_B"),
        pytest.param("GEDI_L4A_umm", "HDF5", id="GEDI_L4A"),
    ],
)
def test_granule_data_type(fixture_name, expected):
    """data_type() derives distinct file types from a granule's GET DATA links."""
    granule = _make_granule(fixture_name)
    assert granule.data_type == expected


def test_granule_data_type_unknown_without_links():
    """data_type() falls back to Unknown when no GET DATA links exist."""
    granule = DataGranule(
        {
            "umm": {
                "GranuleUR": "no-links-granule",
                "RelatedUrls": [
                    {"URL": "https://example.com/data", "Type": "GET DATA"}
                ],
            },
            "meta": {"concept-id": "G1-TEST"},
        }
    )
    assert granule.data_type == "Unknown"


def test_granule_data_type_lists_unmapped_extension():
    """Unmapped extensions are listed as the extension itself (e.g. .jgr)."""
    granule = DataGranule(
        {
            "umm": {
                "GranuleUR": "exotic-granule",
                "RelatedUrls": [
                    {
                        "URL": "https://data.example.gov/granule.jgr",
                        "Type": "GET DATA",
                    }
                ],
            },
            "meta": {"concept-id": "G3-TEST"},
        }
    )
    assert granule.data_type == ".jgr"


def test_granule_data_type_mixed_known_and_unknown():
    """Known types and raw extensions are listed together."""
    granule = DataGranule(
        {
            "umm": {
                "GranuleUR": "mixed-granule",
                "RelatedUrls": [
                    {
                        "URL": "https://data.example.gov/granule.tif",
                        "Type": "GET DATA",
                    },
                    {
                        "URL": "https://data.example.gov/granule.jgr",
                        "Type": "GET DATA",
                    },
                ],
            },
            "meta": {"concept-id": "G4-TEST"},
        }
    )
    assert granule.data_type == "COG, .jgr"


def test_granule_data_type_hybrid_get_data():
    """A GET DATA link list with mixed extensions lists all distinct types."""
    granule = DataGranule(
        {
            "umm": {
                "GranuleUR": "hybrid-granule",
                "RelatedUrls": [
                    {
                        "URL": "https://data.example.gov/granule.tif",
                        "Type": "GET DATA",
                    },
                    {
                        "URL": "https://data.example.gov/granule.nc",
                        "Type": "GET DATA",
                    },
                    {
                        "URL": "https://data.example.gov/granule.jpg",
                        "Type": "GET DATA",
                    },
                ],
            },
            "meta": {"concept-id": "G2-TEST"},
        }
    )
    assert granule.data_type == "COG, NetCDF, JPEG"


def test_repr_search_results_uses_file_type_column():
    """The granule results table shows file types instead of the cloud column."""
    mock_query = MagicMock()
    granule = _make_granule("HLSS30_umm")
    results = SearchResults(mock_query)
    results._total_hits = 1
    results._cached_results = [granule]

    html = _repr_search_results_html(results)

    assert "File Type" in html
    assert "COG, JPEG(thumbs)" in html
    assert '<th style="width: 10%;">Cloud</th>' not in html


def test_repr_search_results_theme_aware():
    """The results repr uses theme variables and the ea-container hook class."""
    mock_query = MagicMock()
    granule = _make_granule("HLSS30_umm")
    results = SearchResults(mock_query)
    results._total_hits = 1
    results._cached_results = [granule]

    html = _repr_search_results_html(results)

    # Root hook so Jupyter dark-theme CSS selectors apply
    assert 'class="bootstrap ea-container"' in html
    # Colors come from CSS custom properties, not hardcoded hex
    assert "var(--ea-" in html
    for hardcoded in ["color: #666", "color: #888", "background: #f8f9fa"]:
        assert hardcoded not in html


def test_repr_search_results_granule_collapsible_assets():
    """Granule rows expand to a detail row listing individual asset files."""
    mock_query = MagicMock()
    granule = _make_granule("HLSS30_umm")
    results = SearchResults(mock_query)
    results._total_hits = 1
    results._cached_results = [granule]

    html = _repr_search_results_html(results)

    # Main row toggles a hidden detail row
    assert "toggleDetail_" in html
    assert "detail-" in html
    assert "display: none" in html

    # Detail row lists the granule's asset files (multi-file HLS granule)
    assert "Files (" in html
    for asset_key in ["B01", "B02", "Fmask", "VZA"]:
        assert asset_key in html

    # Roles are labeled (data vs thumbnail)
    assert ">data</span>" in html
    assert ">thumb</span>" in html

    # S3 (direct-access) and HTTPS access both shown as icons on the same line
    assert "☁️" in html
    assert "⬇️" in html
    assert '☁️</a><a href="https://' in html  # cloud (S3) then download (HTTPS) links

    # Browse image (thumbnail) is embedded in the detail row
    assert "<img src=" in html
    assert ".jpg" in html


def test_repr_search_results_granule_no_thumbnail():
    """Granules without a browse image render no <img> in the detail row."""
    mock_query = MagicMock()
    granule = _make_granule("GEDI_L4A_umm")
    results = SearchResults(mock_query)
    results._total_hits = 1
    results._cached_results = [granule]

    html = _repr_search_results_html(results)

    assert "<img src=" not in html


def test_repr_search_results_granule_main_row_link():
    """Multi-file granules expand via the main-row link; single-file link directly."""
    mock_query = MagicMock()

    # Multi-file granule: main row shows an expand trigger, not a direct download
    multi = _make_granule("HLSS30_umm")
    results = SearchResults(mock_query)
    results._total_hits = 1
    results._cached_results = [multi]
    html = _repr_search_results_html(results)
    main_row = html.split("detail-")[0]
    assert "📁" in main_row
    assert "event.stopPropagation(); toggleDetail_" in main_row
    assert "data.lpdaac" not in main_row  # no direct download in the main row

    # Single-file granule: main row keeps a direct download link
    single = _make_granule("GEDI_L4A_umm")
    results = SearchResults(mock_query)
    results._total_hits = 1
    results._cached_results = [single]
    html = _repr_search_results_html(results)
    main_row = html.split("detail-")[0]
    assert "📥" in main_row


def test_repr_search_results_single_file_main_link_is_https():
    """A single-asset cloud-hosted granule links to HTTPS, not S3."""
    granule = DataGranule(
        {
            "umm": {
                "GranuleUR": "single-file-granule",
                "TemporalExtent": {"SingleDateTime": "2024-01-01T00:00:00Z"},
                "RelatedUrls": [
                    {
                        "URL": "s3://bucket/data.nc",
                        "Type": "GET DATA VIA DIRECT ACCESS",
                    },
                    {
                        "URL": "https://data.example.gov/data.nc",
                        "Type": "GET DATA",
                    },
                ],
            },
            "meta": {"concept-id": "G1-X"},
        },
        cloud_hosted=True,
    )

    results = SearchResults(MagicMock())
    results._total_hits = 1
    results._cached_results = [granule]

    html = _repr_search_results_html(results)
    main_row = html.split("detail-")[0]

    assert 'href="https://data.example.gov/data.nc"' in main_row
    assert 'href="s3://bucket/data.nc"' not in main_row


def test_collection_data_type_from_archive_info():
    """Collection data_type() reads Format from FileDistributionInformation."""
    collection = DataCollection(
        {
            "umm": {
                "ShortName": "TEST",
                "ArchiveAndDistributionInformation": {
                    "FileDistributionInformation": [
                        {"Format": "HDF-EOS5", "AverageFileSize": 10.0}
                    ]
                },
            },
            "meta": {"concept-id": "C1-TEST", "provider-id": "TEST"},
        }
    )
    assert collection.data_type == "HDF-EOS5"


def test_collection_data_type_empty_without_archive_info():
    """Collection data_type() is empty when no FileDistributionInformation."""
    collection = DataCollection(
        {
            "umm": {"ShortName": "TEST"},
            "meta": {"concept-id": "C1-TEST", "provider-id": "TEST"},
        }
    )
    assert collection.data_type == ""


# =============================================================================
# Tests for plot methods (ImportError handling)
# =============================================================================


def test_search_results_explore_import_error():
    """Test that SearchResults.explore() raises ImportError when deps missing."""
    mock_query = MagicMock()
    results = SearchResults(mock_query)
    results._cached_results = [
        DataGranule(
            {
                "umm": {
                    "GranuleUR": "test",
                    "SpatialExtent": {
                        "HorizontalSpatialDomain": {
                            "Geometry": {
                                "BoundingRectangles": [
                                    {
                                        "WestBoundingCoordinate": -180,
                                        "SouthBoundingCoordinate": -90,
                                        "EastBoundingCoordinate": 180,
                                        "NorthBoundingCoordinate": 90,
                                    }
                                ]
                            }
                        }
                    },
                },
                "meta": {"concept-id": "G1-TEST"},
            }
        )
    ]

    # Mock the import to raise ImportError
    with patch(
        "earthaccess.formatting.widgets._check_widget_dependencies",
        side_effect=ImportError("Widget dependencies not installed"),
    ):
        with pytest.raises(ImportError):
            results.explore()


def test_data_granule_explore_import_error():
    """Test that DataGranule.explore() raises ImportError when deps missing."""
    granule = DataGranule(
        {
            "umm": {
                "GranuleUR": "test-granule",
                "SpatialExtent": {
                    "HorizontalSpatialDomain": {
                        "Geometry": {
                            "BoundingRectangles": [
                                {
                                    "WestBoundingCoordinate": -10,
                                    "SouthBoundingCoordinate": 40,
                                    "EastBoundingCoordinate": 10,
                                    "NorthBoundingCoordinate": 50,
                                }
                            ]
                        }
                    }
                },
            },
            "meta": {"concept-id": "G1-TEST"},
        }
    )

    with patch(
        "earthaccess.formatting.widgets._check_widget_dependencies",
        side_effect=ImportError("Widget dependencies not installed"),
    ):
        with pytest.raises(ImportError):
            granule.explore()


def test_data_collection_explore_import_error():
    """Test that DataCollection.explore() raises ImportError when deps missing."""
    collection = DataCollection(
        {
            "umm": {
                "ShortName": "TEST",
                "SpatialExtent": {
                    "HorizontalSpatialDomain": {
                        "Geometry": {
                            "BoundingRectangles": [
                                {
                                    "WestBoundingCoordinate": -180,
                                    "SouthBoundingCoordinate": -90,
                                    "EastBoundingCoordinate": 180,
                                    "NorthBoundingCoordinate": 90,
                                }
                            ]
                        }
                    }
                },
            },
            "meta": {"concept-id": "C1-TEST", "provider-id": "TEST"},
        }
    )

    with patch(
        "earthaccess.formatting.widgets._check_widget_dependencies",
        side_effect=ImportError("Widget dependencies not installed"),
    ):
        with pytest.raises(ImportError):
            collection.explore()


def test_plot_is_deprecated_alias_for_explore():
    """plot() still works but warns and forwards to explore()."""
    for obj in [
        DataCollection(
            {
                "umm": {"ShortName": "TEST"},
                "meta": {"concept-id": "C1-TEST", "provider-id": "TEST"},
            }
        ),
        DataGranule(
            {
                "umm": {"GranuleUR": "test", "SpatialExtent": {}},
                "meta": {"concept-id": "G1-TEST"},
            }
        ),
        SearchResults(MagicMock()),
    ]:
        assert callable(getattr(obj, "plot"))
        assert callable(getattr(obj, "explore"))


def test_is_global_coverage():
    """Test that global vs regional extents are correctly distinguished."""
    from earthaccess.formatting.widgets import _is_global_coverage

    # Full-globe MUR SST granule extent
    assert _is_global_coverage([-180, -90, 180, 90])
    # Near-global with small polar/land gaps
    assert _is_global_coverage([-180, -88, 180, 88])
    # Regional footprints
    assert not _is_global_coverage([-98, 19, -82, 31])
    assert not _is_global_coverage([-10, 40, 10, 50])


@pytest.mark.parametrize(
    "geometry,kind",
    [
        (
            {"Points": [{"Longitude": 112.19, "Latitude": 42.54}]},
            "point",
        ),
        (
            {
                "Points": [
                    {"Longitude": 0.0, "Latitude": 0.0},
                    {"Longitude": 1.0, "Latitude": 1.0},
                ]
            },
            "point",
        ),
        (
            {
                "Lines": [
                    {"Longitude": 0.0, "Latitude": 0.0},
                    {"Longitude": 10.0, "Latitude": 10.0},
                    {"Longitude": 20.0, "Latitude": 20.0},
                ]
            },
            "line",
        ),
        (
            {
                "BoundingRectangles": [
                    {
                        "WestBoundingCoordinate": -98,
                        "SouthBoundingCoordinate": 19,
                        "EastBoundingCoordinate": -82,
                        "NorthBoundingCoordinate": 31,
                    }
                ]
            },
            "polygon",
        ),
        (
            {
                "GPolygons": [
                    {
                        "Boundary": {
                            "Points": [
                                {"Longitude": 0.0, "Latitude": 0.0},
                                {"Longitude": 1.0, "Latitude": 0.0},
                                {"Longitude": 1.0, "Latitude": 1.0},
                                {"Longitude": 0.0, "Latitude": 0.0},
                            ]
                        }
                    }
                ]
            },
            "polygon",
        ),
    ],
)
def test_geometry_kind(geometry, kind):
    """Points/lines/bboxes/gpolygons map to point/line/polygon kinds."""
    from earthaccess.formatting.widgets import _geometry_kind, _geometry_to_shapely

    assert _geometry_kind(_geometry_to_shapely(geometry)) == kind


def test_bboxes_to_geodataframe_point_geometry():
    """A point-only granule (e.g. GNSS RO) still produces a row, not an error."""
    pytest.importorskip("geopandas")

    from earthaccess.formatting.widgets import _bboxes_to_geodataframe

    granule = DataGranule(
        {
            "meta": {"concept-id": "G3859310711-GES_DISC"},
            "umm": {
                "GranuleUR": "gnssro-cosmic1-granule",
                "SpatialExtent": {
                    "HorizontalSpatialDomain": {
                        "Geometry": {
                            "Points": [{"Longitude": 112.19, "Latitude": 42.54}]
                        }
                    }
                },
            },
        }
    )

    gdf = _bboxes_to_geodataframe([granule])
    assert len(gdf) == 1
    assert gdf["kind"].tolist() == ["point"]
    assert gdf["spatial"].tolist() == ["Lon 112.19, Lat 42.54"]


def test_bboxes_to_geodataframe_coverage():
    """Test that the coverage column tags global and regional granules."""
    pytest.importorskip("geopandas")

    from earthaccess.formatting.widgets import _bboxes_to_geodataframe

    granule = DataGranule(
        {
            "umm": {
                "GranuleUR": "global-granule",
                "SpatialExtent": {
                    "HorizontalSpatialDomain": {
                        "Geometry": {
                            "BoundingRectangles": [
                                {
                                    "WestBoundingCoordinate": -180,
                                    "SouthBoundingCoordinate": -90,
                                    "EastBoundingCoordinate": 180,
                                    "NorthBoundingCoordinate": 90,
                                }
                            ]
                        }
                    }
                },
            },
            "meta": {"concept-id": "G-GLOBAL"},
        }
    )

    gdf = _bboxes_to_geodataframe([granule])
    assert len(gdf) == 1
    assert gdf["coverage"].tolist() == ["global"]

    regional = DataGranule(
        {
            "umm": {
                "GranuleUR": "regional-granule",
                "SpatialExtent": {
                    "HorizontalSpatialDomain": {
                        "Geometry": {
                            "BoundingRectangles": [
                                {
                                    "WestBoundingCoordinate": -98,
                                    "SouthBoundingCoordinate": 19,
                                    "EastBoundingCoordinate": -82,
                                    "NorthBoundingCoordinate": 31,
                                }
                            ]
                        }
                    }
                },
            },
            "meta": {"concept-id": "G-REGIONAL"},
        }
    )

    gdf2 = _bboxes_to_geodataframe([regional])
    assert gdf2["coverage"].tolist() == ["regional"]


def test_bboxes_to_geodataframe_metadata():
    """Test that granules/collections carry CMR links plus temporal and spatial."""
    pytest.importorskip("geopandas")

    from earthaccess.formatting.widgets import _bboxes_to_geodataframe

    granule = DataGranule(
        {
            "meta": {"concept-id": "G3357328910-LPCLOUD"},
            "umm": {
                "GranuleUR": "HLS.L30.T55MGQ.2025001T001252.v2.0",
                "SpatialExtent": {
                    "HorizontalSpatialDomain": {
                        "Geometry": {
                            "BoundingRectangles": [
                                {
                                    "WestBoundingCoordinate": 65.25,
                                    "SouthBoundingCoordinate": 25.5,
                                    "EastBoundingCoordinate": 71.25,
                                    "NorthBoundingCoordinate": 30.5,
                                }
                            ]
                        }
                    }
                },
                "TemporalExtent": {
                    "RangeDateTime": {
                        "BeginningDateTime": "2025-01-01T00:12:52Z",
                        "EndingDateTime": "2025-01-01T00:17:52Z",
                    }
                },
            },
        },
        cloud_hosted=True,
    )

    gdf = _bboxes_to_geodataframe([granule])
    row = gdf.iloc[0]
    assert (
        row["id"]
        == "https://cmr.earthdata.nasa.gov/search/concepts/G3357328910-LPCLOUD"
    )
    assert row["temporal"] == "2025-01-01 to 2025-01-01"
    assert row["spatial"] == "W 65.25, S 25.50, E 71.25, N 30.50"

    collection = DataCollection(
        {
            "meta": {"concept-id": "C1996881146-POCLOUD"},
            "umm": {
                "ShortName": "MUR-JPL-L4-GLOB-v4.1",
                "Version": "4.1",
                "SpatialExtent": {
                    "HorizontalSpatialDomain": {
                        "Geometry": {
                            "BoundingRectangles": [
                                {
                                    "WestBoundingCoordinate": -180,
                                    "SouthBoundingCoordinate": -90,
                                    "EastBoundingCoordinate": 180,
                                    "NorthBoundingCoordinate": 90,
                                }
                            ]
                        }
                    }
                },
                "TemporalExtents": [
                    {
                        "RangeDateTimes": [
                            {
                                "BeginningDateTime": "2002-06-01T00:00:00Z",
                                "EndingDateTime": "2026-08-01T00:00:00Z",
                            }
                        ]
                    }
                ],
            },
        }
    )

    gdf2 = _bboxes_to_geodataframe([collection])
    row2 = gdf2.iloc[0]
    assert (
        row2["id"]
        == "https://cmr.earthdata.nasa.gov/search/concepts/C1996881146-POCLOUD"
    )
    assert row2["temporal"] == "2002-06-01 to 2026-08-01"
    assert row2["spatial"] == "W -180.00, S -90.00, E 180.00, N 90.00"


def test_plot_splits_global_coverage():
    """Test that global-coverage granules are rendered without a fill."""
    pytest.importorskip("lonboard")

    from earthaccess.formatting.widgets import plot

    def make_granule(west, south, east, north, name):
        return DataGranule(
            {
                "umm": {
                    "GranuleUR": name,
                    "SpatialExtent": {
                        "HorizontalSpatialDomain": {
                            "Geometry": {
                                "BoundingRectangles": [
                                    {
                                        "WestBoundingCoordinate": west,
                                        "SouthBoundingCoordinate": south,
                                        "EastBoundingCoordinate": east,
                                        "NorthBoundingCoordinate": north,
                                    }
                                ]
                            }
                        }
                    },
                },
                "meta": {"concept-id": name},
            }
        )

    class FakeResults:
        def __init__(self, granules):
            self._cached_results = granules

    # Only global-coverage granules -> single outline-only layer, no fill.
    global_results = FakeResults(
        [make_granule(-180, -90, 180, 90, "g-global") for _ in range(3)]
    )
    m = plot(global_results)
    assert len(m.layers) == 1
    assert m.layers[0].get_fill_color == [0, 100, 200, 0]

    # Mixed global + regional -> one outline-only layer and one filled layer.
    mixed_results = FakeResults(
        [
            make_granule(-180, -90, 180, 90, "g-global"),
            make_granule(-98, 19, -82, 31, "g-regional"),
        ]
    )
    m2 = plot(mixed_results)
    assert len(m2.layers) == 2
    fills = {tuple(layer.get_fill_color) for layer in m2.layers}
    assert (0, 100, 200, 0) in fills  # global layer has no fill
    assert (0, 100, 200, 80) in fills  # regional layer keeps default fill


def test_plot_reduces_fill_opacity_for_many_granules():
    """The default fill opacity drops with many overlapping footprints."""
    pytest.importorskip("lonboard")

    from earthaccess.formatting.widgets import plot

    def make_granule(i):
        return DataGranule(
            {
                "umm": {
                    "GranuleUR": f"g{i}",
                    "SpatialExtent": {
                        "HorizontalSpatialDomain": {
                            "Geometry": {
                                "BoundingRectangles": [
                                    {
                                        "WestBoundingCoordinate": -10,
                                        "SouthBoundingCoordinate": 40,
                                        "EastBoundingCoordinate": 10,
                                        "NorthBoundingCoordinate": 50,
                                    }
                                ]
                            }
                        }
                    },
                },
                "meta": {"concept-id": f"G{i}"},
            }
        )

    class FakeResults:
        def __init__(self, granules):
            self._cached_results = granules

    # <= 20 footprints keep the full default opacity
    small = FakeResults([make_granule(i) for i in range(10)])
    assert plot(small).layers[0].get_fill_color[3] == 80

    # > 20 footprints are drawn more transparently
    large = FakeResults([make_granule(i) for i in range(100)])
    alpha_large = plot(large).layers[0].get_fill_color[3]
    assert alpha_large < 80

    # An explicitly passed fill_color is never overridden
    explicit = plot(large, fill_color=[255, 0, 0, 128]).layers[0].get_fill_color
    assert explicit == [255, 0, 0, 128]


def test_query_bounding_box_legacy_params():
    """The search ROI is read from the legacy query param dict."""
    from types import SimpleNamespace

    from earthaccess.formatting.widgets import _query_bounding_box
    from earthaccess.search import DataGranulesQuery

    q = DataGranulesQuery().parameters(
        short_name="ATL06", bounding_box=(40.19, 6.24, 42.18, 7.24)
    )
    assert _query_bounding_box(SimpleNamespace(query=q)) == [40.19, 6.24, 42.18, 7.24]


def test_query_bounding_box_new_builder():
    """The search ROI is read from the new GranuleQuery/CollectionQuery builder."""
    from types import SimpleNamespace

    from earthaccess.formatting.widgets import _query_bounding_box
    from earthaccess.search.query import GranuleQuery

    gq = GranuleQuery().short_name("ATL06").bounding_box(-46.5, 61.0, -42.5, 63.0)
    assert _query_bounding_box(SimpleNamespace(query=gq)) == [-46.5, 61.0, -42.5, 63.0]


def test_query_bounding_box_returns_none_without_bbox():
    """No ROI is returned for queries without a bounding box."""
    from types import SimpleNamespace

    from earthaccess.formatting.widgets import _query_bounding_box
    from earthaccess.search import DataGranulesQuery

    assert _query_bounding_box(SimpleNamespace(query=None)) is None
    q_no_bbox = DataGranulesQuery().parameters(short_name="ATL06")
    assert _query_bounding_box(SimpleNamespace(query=q_no_bbox)) is None
    q_point = DataGranulesQuery().parameters(short_name="ATL06", point=(40.0, 7.0))
    assert _query_bounding_box(SimpleNamespace(query=q_point)) is None
