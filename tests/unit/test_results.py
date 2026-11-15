"""Tests for earthaccess results classes and search functionality.

This module contains two types of tests:
1. VCR-based tests that record/playback HTTP interactions for search workflows
2. Unit tests with static fixtures for DataGranule/DataCollection methods

VCR tests use pytest-recording for HTTP cassette management.
See docs/contributing/testing-guide.md for guidelines.
"""

import json
import logging
from pathlib import Path

import earthaccess
import pystac
import pytest
import responses
from earthaccess.search import (
    DataCollection,
    DataCollectionsQuery,
    DataGranule,
    DataGranulesQuery,
)
from earthaccess.search._utils import get_results

logging.basicConfig()
logging.getLogger("vcr").setLevel(logging.ERROR)


def unique_results(results):
    """Ensure search results have unique concept IDs.

    When we invoke a search request multiple times we want to ensure that we don't
    get the same results back. This is a one shot test as the results are preserved
    by VCR but still useful.
    """
    unique_concept_ids = {result["meta"]["concept-id"] for result in results}
    return len(unique_concept_ids) == len(results)


def assert_is_using_search_after(cassette):
    """Assert that CMR search-after pagination is being used correctly."""
    first_request = True

    for request in cassette.requests:
        # Verify the page number was not used
        assert "page_num" not in request.uri
        # Verify that Search After was used in all requests except first
        assert first_request == ("CMR-Search-After" not in request.headers)
        first_request = False


# =============================================================================
# VCR-Based Search Tests
# =============================================================================


@pytest.mark.vcr
def test_no_results(vcr):
    """If we search for a collection that doesn't exist, we should get no results."""
    granules = earthaccess.search_data(
        # STAC collection name; correct short name is OPERA_L3_DSWX-HLS_V1
        # Example discussed in: https://github.com/nsidc/earthaccess/pull/839
        short_name="OPERA_L3_DSWX-HLS_V1_1.0",
        bounding_box=(-95.19, 30.59, -94.99, 30.79),
        temporal=("2024-04-30", "2024-05-31"),
    )
    assert len(granules) == 0


@pytest.mark.vcr
def test_data_links(vcr):
    """Test that data links return correct S3 and HTTPS URLs."""
    results = earthaccess.search_data(
        short_name="SEA_SURFACE_HEIGHT_ALT_GRIDS_L4_2SATS_5DAY_6THDEG_V_JPL2205",
        temporal=("2020", "2022"),
        count=1,
    )

    # Convert to list to fetch results (SearchResults is lazy)
    granules = list(results)
    g = granules[0]
    # `access` specified
    assert g.data_links(access="direct")[0].startswith("s3://")
    assert g.data_links(access="external")[0].startswith("https://")


def _register_granule_pages(
    *, total: int, limit: int, short_name: str = "MOD02QKM", page_size: int = 2000
):
    """Register CMR granule-search responses for a ``search_data`` + iteration.

    The sequence a ``SearchResults`` triggers is: one ``hits`` request
    (``page_size=0``), one prefetch (``page_size=20``), then successive
    ``page_size`` requests until ``limit`` is reached or the feed is empty.

    Returns the total number of items the responses will serve (so tests can
    assert how many results iteration should yield).
    """
    url = "https://cmr.earthdata.nasa.gov/search/granules.umm_json"

    def items(start, end):
        return [
            {"meta": {"concept-id": f"G{i}-{short_name}"}, "umm": {}}
            for i in range(start, end)
        ]

    # hits() query
    responses.add(
        responses.GET,
        url,
        json={"hits": total, "items": []},
        headers={"CMR-Hits": str(total)},
        status=200,
    )

    # prefetch page
    responses.add(
        responses.GET,
        url,
        json={"hits": total, "items": items(0, min(20, total))},
        headers={"CMR-Hits": str(total)},
        status=200,
    )

    served = 0
    while served < limit and served < total:
        page_end = min(served + page_size, total)
        responses.add(
            responses.GET,
            url,
            json={"hits": total, "items": items(served, page_end)},
            headers={"CMR-Hits": str(total)},
            status=200,
        )
        served = page_end

    return min(total, limit)


@responses.activate
def test_get_more_than_2000():
    """Iterating past 2000 granules issues multiple page fetches."""
    served = _register_granule_pages(total=3000, limit=3000)

    results = earthaccess.search_data(short_name="MOD02QKM", count=3000)
    granules = list(results)

    assert len(granules) == served == 3000
    assert unique_results(granules)
    # hits + prefetch + 2 full pages
    assert len(responses.calls) == 4


@responses.activate
def test_get():
    """A single-page search returns the requested number of granules."""
    served = _register_granule_pages(total=3000, limit=2000)

    results = earthaccess.search_data(short_name="MOD02QKM", count=2000)
    granules = list(results)

    assert len(granules) == served == 2000
    assert unique_results(granules)


@responses.activate
def test_get_all_less_than_2k():
    """Search for a collection with fewer than 2000 granules returns all of them."""
    served = _register_granule_pages(total=163, limit=2000)

    results = earthaccess.search_data(
        short_name="TELLUS_GRAC_L3_JPL_RL06_LND_v04", count=2000
    )
    granules = list(results)

    assert len(granules) == served == 163
    assert unique_results(granules)


@responses.activate
def test_get_all_more_than_2k():
    """Search for a collection with more than 2000 granules pages correctly."""
    served = _register_granule_pages(total=3000, limit=3000)

    results = earthaccess.search_data(
        short_name="CYGNSS_NOAA_L2_SWSP_25KM_V1.2", count=3000
    )
    granules = list(results)

    assert len(granules) == served == 3000
    assert unique_results(granules)
    assert len(responses.calls) == 4


@responses.activate
def test_get_paginates_past_short_first_page():
    """A page shorter than the requested page_size must not end pagination.

    Regression test for https://github.com/earthaccess-dev/earthaccess/pull/1444.

    CMR may return fewer than `page_size` items on a page even though more
    results remain (e.g. the granule search for C3974616058-LPCLOUD over 2024
    returns 1985 items on the first page of 2000, well short of the 12,070
    total hits). Stopping pagination as soon as a page came back short
    silently dropped the remaining results. The only reliable signals that
    pagination is complete are an empty page or reaching the requested
    `limit`.
    """
    url = "https://cmr.earthdata.nasa.gov/search/granules.umm_json"

    # First page is short (fewer than the requested page_size), but CMR
    # still signals more results via the CMR-Search-After header.
    responses.add(
        responses.GET,
        url,
        json={
            "hits": 12070,
            "items": [
                {"meta": {"concept-id": f"G{i}-LPCLOUD"}, "umm": {}}
                for i in range(1985)
            ],
        },
        headers={"CMR-Search-After": '["lpcloud",1718999394000,4165185217]'},
        status=200,
    )
    responses.add(
        responses.GET,
        url,
        json={
            "hits": 12070,
            "items": [
                {"meta": {"concept-id": f"G{i}-LPCLOUD"}, "umm": {}}
                for i in range(1985, 3985)
            ],
        },
        headers={"CMR-Search-After": '["lpcloud",1722041241000,4166963562]'},
        status=200,
    )
    # Final request: empty page, no CMR-Search-After header.
    responses.add(
        responses.GET,
        url,
        json={"hits": 12070, "items": []},
        status=200,
    )

    query = DataGranulesQuery()
    query.concept_id("C3974616058-LPCLOUD")
    query.temporal("2024-01-01", "2024-12-31")

    results = get_results(query.session, query, limit=12070)

    assert len(results) == 3985
    assert len(responses.calls) == 3


@pytest.mark.vcr
def test_collections_less_than_2k(vcr):
    """Test collection search with fewer than 2000 results."""
    query = DataCollectionsQuery().daac("PODAAC").cloud_hosted(True)
    collections = query.get(20)

    # Assert that we performed a single search results query
    assert len(vcr) == 1
    assert len(collections) == 20
    assert unique_results(collections)
    assert_is_using_search_after(vcr)


@pytest.mark.vcr
def test_collections_more_than_2k(vcr):
    """Test collection search pagination with more than 2000 results."""
    query = DataCollectionsQuery()
    collections = query.get(3000)

    # Assert that we performed two search results queries
    # With truncated cassettes (max 20 items per response), we get up to 40 items
    assert len(vcr) == 2
    assert len(collections) <= 40
    assert unique_results(collections)
    assert_is_using_search_after(vcr)


# =============================================================================
# Unit Tests with Static Fixtures
# =============================================================================


def test_get_doi_returns_doi_when_present():
    collection = DataCollection(
        {"umm": {"DOI": {"DOI": "doi:10.16904/envidat.lwf.34"}}, "meta": {}}
    )

    assert collection.doi == "doi:10.16904/envidat.lwf.34"


def test_granule_repr_is_concise_pystac_style():
    """print(granule) shows a short, pystac-style summary, not raw dicts."""
    from tests.unit.fixtures import load_granule_fixture

    data = load_granule_fixture("HLSS30_umm")
    granule = DataGranule(data, cloud_hosted=True)

    r = repr(granule)

    assert r.startswith("<DataGranule ")
    assert r.endswith(">")
    assert "granule_ur=HLS.S30.T55JGM.2015332T001732.v2.0" in r
    assert "temporal=2015-11-28" in r
    assert "size=" in r
    assert "cloud_hosted" not in r
    # No raw dict dumps or full link lists
    assert "{" not in r
    assert "s3://" not in r


def test_granule_repr_handles_minimal_data():
    """A bare granule still renders without raising."""
    granule = DataGranule(
        {"umm": {"GranuleUR": "test-granule"}, "meta": {"concept-id": "G1-TEST"}}
    )
    r = repr(granule)

    assert "<DataGranule granule_ur=test-granule" in r
    assert "temporal=n/a" in r
    assert "size=n/a" in r


def test_collection_repr_is_concise_pystac_style():
    """print(collection) shows a short, pystac-style summary, not raw JSON."""
    collection = DataCollection(
        {
            "umm": {
                "ShortName": "HLSS30",
                "Version": "2.0",
                "DirectDistributionInformation": {"Region": "us-west-2"},
            },
            "meta": {"concept-id": "C2021957295-LPCLOUD"},
        }
    )

    r = repr(collection)

    assert r.startswith("<DataCollection ")
    assert r.endswith(">")
    assert "short_name=HLSS30" in r
    assert "version=2.0" in r
    assert "concept_id=C2021957295-LPCLOUD" in r
    assert "cloud_hosted" not in r
    # No raw JSON dump
    assert "{" not in r


def test_collection_repr_omits_version_when_missing():
    """Collections without a version omit the version field."""
    collection = DataCollection(
        {"umm": {"ShortName": "MOD02QKM"}, "meta": {"concept-id": "C1-LAADS"}}
    )
    r = repr(collection)

    assert "version=" not in r
    assert "cloud_hosted" not in r


def test_get_doi_returns_empty_string_when_doi_missing():
    collection = DataCollection({"umm": {"DOI": {}}, "meta": {}})

    assert collection.doi is None


def test_get_doi_returns_empty_string_when_doi_key_missing():
    collection = DataCollection({"umm": {}, "meta": {}})

    assert collection.doi is None


@responses.activate
def test_get_citation_apa_format():
    collection = DataCollection(
        {"umm": {"DOI": {"DOI": "doi:10.16904/envidat.lwf.34"}}, "meta": {}}
    )

    responses.add(
        responses.GET,
        "https://citation.doi.org/format?doi=doi:10.16904/envidat.lwf.34&style=apa&lang=en-US",
        body="Meusburger, K., Graf Pannatier, E., & Schaub, M. (2019). 10-HS Pfynwald (Version 2019) [Dataset]. EnviDat. https://doi.org/10.16904/ENVIDAT.LWF.34",
        status=200,
    )

    citation = collection.citation(format="apa", language="en-US")

    assert (
        citation
        == "Meusburger, K., Graf Pannatier, E., & Schaub, M. (2019). 10-HS Pfynwald (Version 2019) [Dataset]. EnviDat. https://doi.org/10.16904/ENVIDAT.LWF.34"
    )


@responses.activate
def test_get_citation_different_language():
    collection = DataCollection(
        {"umm": {"DOI": {"DOI": "doi:10.16904/envidat.lwf.34"}}, "meta": {}}
    )

    responses.add(
        responses.GET,
        "https://citation.doi.org/format?doi=doi:10.16904/envidat.lwf.34&style=apa&lang=fr-FR",
        body="Meusburger, K., Graf Pannatier, E., & Schaub, M. (2019). 10-HS Pfynwald (Version 2019) [Jeu de données]. EnviDat. https://doi.org/10.16904/ENVIDAT.LWF.34",
        status=200,
    )

    citation = collection.citation(format="apa", language="fr-FR")

    assert (
        citation
        == "Meusburger, K., Graf Pannatier, E., & Schaub, M. (2019). 10-HS Pfynwald (Version 2019) [Jeu de données]. EnviDat. https://doi.org/10.16904/ENVIDAT.LWF.34"
    )


def test_get_citation_returns_none_when_doi_missing():
    collection = DataCollection({"umm": {}, "meta": {}})

    assert collection.citation(format="apa", language="en-US") is None


def test_get_citation_returns_none_when_doi_empty():
    collection = DataCollection({"umm": {"DOI": {"DOI": ""}}, "meta": {}})

    assert collection.citation(format="apa", language="en-US") is None


# =============================================================================
# Tests for to_dict() and to_stac() methods
# =============================================================================


def test_collection_to_dict():
    """Test that to_dict returns a plain dictionary."""
    collection = DataCollection(
        {
            "umm": {
                "ShortName": "TestCollection",
                "Version": "1.0",
                "Abstract": "A test collection",
            },
            "meta": {
                "concept-id": "C123456-TEST",
                "provider-id": "TEST",
            },
        }
    )

    result = collection.to_dict()
    assert isinstance(result, dict)
    assert result["umm"]["ShortName"] == "TestCollection"
    assert result["meta"]["concept-id"] == "C123456-TEST"


def test_collection_to_stac():
    """Test that to_stac returns a valid STAC Collection structure."""
    collection = DataCollection(
        {
            "umm": {
                "ShortName": "TestCollection",
                "Version": "1.0",
                "Abstract": "A test collection for STAC conversion",
                "DOI": {"DOI": "10.5067/TEST"},
                "TemporalExtents": [
                    {
                        "RangeDateTimes": [
                            {
                                "BeginningDateTime": "2020-01-01T00:00:00Z",
                                "EndingDateTime": "2020-12-31T23:59:59Z",
                            }
                        ]
                    }
                ],
                "SpatialExtent": {
                    "HorizontalSpatialDomain": {
                        "Geometry": {
                            "BoundingRectangles": [
                                {
                                    "WestBoundingCoordinate": -180.0,
                                    "SouthBoundingCoordinate": -90.0,
                                    "EastBoundingCoordinate": 180.0,
                                    "NorthBoundingCoordinate": 90.0,
                                }
                            ]
                        }
                    }
                },
                "RelatedUrls": [
                    {"Type": "LANDING PAGE", "URL": "https://example.com/landing"},
                    {"Type": "GET DATA", "URL": "https://example.com/data"},
                ],
            },
            "meta": {
                "concept-id": "C123456-TEST",
                "provider-id": "TEST",
            },
        }
    )

    stac = collection.to_stac()

    # Check required STAC fields
    assert isinstance(stac, pystac.Collection)
    assert stac.STAC_OBJECT_TYPE == pystac.STACObjectType.COLLECTION
    assert stac.stac_extensions
    assert stac.id
    assert stac.description
    assert stac.extent
    assert stac.links

    # Check extent structure
    assert stac.extent.spatial.bboxes == [[-180.0, -90.0, 180.0, 90.0]]

    # Check DOI extension
    assert stac.extra_fields["sci:doi"] == "10.5067/TEST"

    # Check CMR-specific properties
    assert stac.extra_fields["cmr:concept_id"] == "C123456-TEST"


def test_granule_to_dict():
    """Test that DataGranule.to_dict returns a plain dictionary."""
    from earthaccess.search import DataGranule

    granule = DataGranule(
        {
            "umm": {
                "GranuleUR": "test_granule_001",
                "CollectionReference": {
                    "ShortName": "TestCollection",
                    "Version": "1.0",
                },
                "TemporalExtent": {
                    "RangeDateTime": {
                        "BeginningDateTime": "2020-06-01T00:00:00Z",
                        "EndingDateTime": "2020-06-01T23:59:59Z",
                    }
                },
                "SpatialExtent": {
                    "HorizontalSpatialDomain": {
                        "Geometry": {
                            "BoundingRectangles": [
                                {
                                    "WestBoundingCoordinate": -10.0,
                                    "SouthBoundingCoordinate": 30.0,
                                    "EastBoundingCoordinate": 10.0,
                                    "NorthBoundingCoordinate": 50.0,
                                }
                            ]
                        }
                    }
                },
                "RelatedUrls": [],
            },
            "meta": {
                "concept-id": "G123456-TEST",
                "provider-id": "TEST",
            },
        }
    )

    result = granule.to_dict()
    assert isinstance(result, dict)
    assert result["umm"]["GranuleUR"] == "test_granule_001"
    assert result["meta"]["concept-id"] == "G123456-TEST"


def test_granule_to_stac():
    """Test that DataGranule.to_stac returns a valid STAC Item structure."""
    from earthaccess.search import DataGranule

    granule = DataGranule(
        {
            "umm": {
                "GranuleUR": "test_granule_001",
                "CollectionReference": {
                    "ShortName": "TestCollection",
                    "Version": "1.0",
                },
                "TemporalExtent": {
                    "RangeDateTime": {
                        "BeginningDateTime": "2020-06-01T00:00:00Z",
                        "EndingDateTime": "2020-06-01T23:59:59Z",
                    }
                },
                "SpatialExtent": {
                    "HorizontalSpatialDomain": {
                        "Geometry": {
                            "BoundingRectangles": [
                                {
                                    "WestBoundingCoordinate": -10.0,
                                    "SouthBoundingCoordinate": 30.0,
                                    "EastBoundingCoordinate": 10.0,
                                    "NorthBoundingCoordinate": 50.0,
                                }
                            ]
                        }
                    }
                },
                "RelatedUrls": [
                    {"Type": "GET DATA", "URL": "https://example.com/data.nc"},
                    {
                        "Type": "GET RELATED VISUALIZATION",
                        "URL": "https://example.com/browse.png",
                    },
                ],
                "DataGranule": {"ArchiveAndDistributionInformation": [{"Size": 100.5}]},
            },
            "meta": {
                "concept-id": "G123456-TEST",
                "provider-id": "TEST",
            },
        }
    )

    stac = granule.to_stac()

    # Check required STAC Item fields
    assert isinstance(stac, pystac.Item)
    assert stac.STAC_OBJECT_TYPE == pystac.STACObjectType.ITEM
    assert stac.id == "test_granule_001"
    assert stac.geometry
    assert stac.bbox
    assert stac.properties
    assert stac.assets
    assert stac.links

    # Check geometry
    assert stac.geometry["type"] == "Polygon"
    assert stac.bbox == [-10.0, 30.0, 10.0, 50.0]

    # Check collection reference
    assert stac.collection_id == "TestCollection_v1.0"

    # Check assets
    assert "data" in stac.assets
    assert stac.assets["data"].href == "https://example.com/data.nc"
    # Thumbnail asset uses filename as key (e.g., "browse" from browse.png)
    assert "browse" in stac.assets
    assert "thumbnail" in stac.assets["browse"].roles

    # Check CMR-specific properties
    assert stac.properties["cmr:concept_id"] == "G123456-TEST"


def test_granule_to_stac_with_s3_links():
    """Test STAC conversion with S3 direct access links."""
    from earthaccess.search import DataGranule

    granule = DataGranule(
        {
            "umm": {
                "GranuleUR": "s3_granule_001",
                "CollectionReference": {"ShortName": "CloudData"},
                "TemporalExtent": {"SingleDateTime": "2020-06-15T12:00:00Z"},
                "SpatialExtent": {},
                "RelatedUrls": [
                    {"Type": "GET DATA VIA DIRECT ACCESS", "URL": "s3://bucket/data.nc"}
                ],
            },
            "meta": {
                "concept-id": "G789-CLOUD",
                "provider-id": "CLOUD",
            },
        },
        cloud_hosted=True,
    )

    stac = granule.to_stac()

    # Check that S3 asset has cloud-optimized role
    assert "data" in stac.assets
    assert stac.assets["data"].href == "s3://bucket/data.nc"
    assert "cloud-optimized" in stac.assets["data"].roles


def test_collection_to_stac_minimal():
    """Test STAC conversion with minimal collection data."""
    collection = DataCollection(
        {
            "umm": {
                "ShortName": "MinimalCollection",
            },
            "meta": {
                "concept-id": "C999-MIN",
            },
        }
    )

    stac = collection.to_stac()

    # Should still produce valid STAC structure
    assert isinstance(stac, pystac.Collection)
    assert stac.STAC_OBJECT_TYPE == pystac.STACObjectType.COLLECTION
    assert stac.id == "MinimalCollection"
    assert stac.extent
    # Default bbox when no spatial info
    assert stac.extent.spatial.bboxes == [[-180.0, -90.0, 180.0, 90.0]]


# =============================================================================
# Tests for STAC Asset Naming
# =============================================================================


def test_extract_asset_key_from_band_filename():
    """Test extracting asset key from HLS-style band filenames."""
    from earthaccess.search import DataGranule

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
                        "Type": "GET DATA VIA DIRECT ACCESS",
                        "URL": "s3://lp-prod/HLS.L30.T10SEG.2023001T185019.v2.0.B03.tif",
                    },
                    {
                        "Type": "GET DATA VIA DIRECT ACCESS",
                        "URL": "s3://lp-prod/HLS.L30.T10SEG.2023001T185019.v2.0.Fmask.tif",
                    },
                ],
            },
            "meta": {"concept-id": "G123-LP", "provider-id": "LPCLOUD"},
        },
        cloud_hosted=True,
    )

    stac = granule.to_stac()

    # Check that asset keys are meaningful band names, not generic "data_0", "data_1"
    assert "B02" in stac.assets, f"Expected 'B02' key, got: {list(stac.assets.keys())}"
    assert "B03" in stac.assets, f"Expected 'B03' key, got: {list(stac.assets.keys())}"
    assert "Fmask" in stac.assets, (
        f"Expected 'Fmask' key, got: {list(stac.assets.keys())}"
    )

    # Check that generic keys are NOT present
    assert "data" not in stac.assets
    assert "data_1" not in stac.assets
    assert "data_2" not in stac.assets


def test_extract_asset_key_s3_and_https_grouping():
    """Test that S3 and HTTPS versions of the same file are grouped together."""
    from earthaccess.search import DataGranule

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

    stac = granule.to_stac()

    # Should have only one B02 asset, not B02 and B02_https
    assert "B02" in stac.assets
    assert len([k for k in stac.assets if k.startswith("B02")]) == 1

    # HTTPS should be the primary href by default (works outside AWS)
    assert stac.assets["B02"].href.startswith("https://")

    # S3 should be in alternate
    assert "alternate" in stac.assets["B02"].extra_fields
    assert stac.assets["B02"].extra_fields["alternate"]["href"].startswith("s3://")

    # access="s3" flips the primary href to S3
    stac_s3 = granule.to_stac(access="s3")
    assert stac_s3.assets["B02"].href.startswith("s3://")
    assert (
        stac_s3.assets["B02"].extra_fields["alternate"]["href"].startswith("https://")
    )


def test_to_stac_invalid_access_raises():
    """Test that an invalid access strategy raises a ValueError."""
    from earthaccess.search import DataGranule

    granule = DataGranule(
        {
            "umm": {
                "GranuleUR": "test_granule",
                "CollectionReference": {"ShortName": "TestCollection"},
                "SpatialExtent": {},
                "RelatedUrls": [],
            },
            "meta": {"concept-id": "G123-TEST", "provider-id": "TEST"},
        }
    )

    with pytest.raises(ValueError, match="access"):
        granule.to_stac(access="bogus")


def test_extract_asset_key_thumbnail():
    """Test that thumbnail/browse assets get proper naming."""
    from earthaccess.search import DataGranule

    granule = DataGranule(
        {
            "umm": {
                "GranuleUR": "test_granule",
                "CollectionReference": {"ShortName": "TestCollection"},
                "TemporalExtent": {"SingleDateTime": "2023-01-01T00:00:00Z"},
                "SpatialExtent": {},
                "RelatedUrls": [
                    {
                        "Type": "GET RELATED VISUALIZATION",
                        "URL": "https://example.com/browse.png",
                    },
                    {
                        "Type": "GET RELATED VISUALIZATION",
                        "URL": "https://example.com/quicklook.jpg",
                    },
                ],
            },
            "meta": {"concept-id": "G123-TEST", "provider-id": "TEST"},
        }
    )

    stac = granule.to_stac()

    # Thumbnails should be named from filename (without extension)
    assert "browse" in stac.assets or "thumbnail" in stac.assets
    assert (
        "thumbnail" in stac.assets["browse"].roles
        or "visual" in stac.assets["browse"].roles
    )


def test_extract_asset_key_single_data_file():
    """Test that single data files use the filename as key."""
    from earthaccess.search import DataGranule

    granule = DataGranule(
        {
            "umm": {
                "GranuleUR": "ATL08_20190221121851_08410203_005_01",
                "CollectionReference": {"ShortName": "ATL08"},
                "TemporalExtent": {"SingleDateTime": "2019-02-21T00:00:00Z"},
                "SpatialExtent": {},
                "RelatedUrls": [
                    {
                        "Type": "GET DATA",
                        "URL": "https://example.com/ATL08_20190221121851_08410203_005_01.h5",
                    },
                ],
            },
            "meta": {"concept-id": "G123-NSIDC", "provider-id": "NSIDC_ECS"},
        }
    )

    stac = granule.to_stac()

    # Single data file should use filename (without extension) as key
    # Or just "data" if the filename matches granule ID
    assert len(stac.assets) == 1
    asset_key = list(stac.assets.keys())[0]
    # Either the key is "data" (when filename == granule_id) or the filename
    assert asset_key in ("data", "ATL08_20190221121851_08410203_005_01")


def test_extract_asset_key_netcdf_with_extension():
    """Test that file extensions are removed from asset keys."""
    from earthaccess.search import DataGranule

    granule = DataGranule(
        {
            "umm": {
                "GranuleUR": "mur_sst_20230101",
                "CollectionReference": {"ShortName": "MUR-JPL"},
                "TemporalExtent": {"SingleDateTime": "2023-01-01T00:00:00Z"},
                "SpatialExtent": {},
                "RelatedUrls": [
                    {
                        "Type": "GET DATA",
                        "URL": "https://example.com/mur_sst_20230101.nc",
                    },
                ],
            },
            "meta": {"concept-id": "G123-PODAAC", "provider-id": "PODAAC"},
        }
    )

    stac = granule.to_stac()

    # Key should be the filename without extension, or "data" if it matches granule ID
    asset_key = list(stac.assets.keys())[0]
    assert ".nc" not in asset_key  # Extension should be removed


# =============================================================================
# Parametrized Tests for Multi-File Collections (Real CMR Data)
# =============================================================================


def load_fixture(fixture_name: str) -> dict:
    """Load a UMM JSON fixture file from the granules directory."""
    fixture_path = Path(__file__).parent / "fixtures" / "granules" / fixture_name
    with open(fixture_path) as f:
        return json.load(f)


@pytest.mark.parametrize(
    "fixture_file,expected_keys,description",
    [
        pytest.param(
            "HLSL30_umm.json",
            [
                "B01",
                "B02",
                "B03",
                "B04",
                "B05",
                "B06",
                "B07",
                "B09",
                "B10",
                "B11",
                "Fmask",
                "VZA",
                "VAA",
                "SAA",
                "SZA",
            ],
            "HLS Landsat 30m multi-band COGs",
            id="HLSL30",
        ),
        pytest.param(
            "HLSS30_umm.json",
            [
                "B01",
                "B02",
                "B03",
                "B04",
                "B05",
                "B06",
                "B07",
                "B08",
                "B09",
                "B10",
                "B11",
                "B12",
                "B8A",
                "Fmask",
                "VZA",
                "VAA",
                "SAA",
                "SZA",
            ],
            "HLS Sentinel 30m multi-band COGs",
            id="HLSS30",
        ),
        pytest.param(
            "GEDI02_B_umm.json",
            ["data"],
            "GEDI L2B single HDF5 file",
            id="GEDI02_B",
        ),
    ],
)
def test_multifile_collection_asset_extraction(
    fixture_file, expected_keys, description
):
    """Test STAC asset naming extracts meaningful keys from various collections.

    This test uses real CMR UMM responses to verify that asset keys are
    properly extracted from multi-file granules.
    """
    from earthaccess.search import DataGranule

    # Load fixture data
    fixture_data = load_fixture(fixture_file)

    # Determine cloud_hosted from provider
    provider = fixture_data["meta"].get("provider-id", "")
    cloud_hosted = provider in ("LPCLOUD", "POCLOUD", "ORNL_CLOUD", "GES_DISC")

    # Create DataGranule from fixture
    granule = DataGranule(fixture_data, cloud_hosted=cloud_hosted)

    # Convert to STAC
    stac = granule.to_stac()

    # Check that all expected asset keys are present
    asset_keys = list(stac.assets.keys())
    for expected_key in expected_keys:
        assert expected_key in asset_keys, (
            f"Expected asset key '{expected_key}' not found. "
            f"Got: {asset_keys}. Collection: {description}"
        )

    # For multi-band data, verify no generic "data_N" keys are present
    if len(expected_keys) > 1:
        generic_keys = [k for k in asset_keys if k.startswith("data_")]
        assert len(generic_keys) == 0, (
            f"Found generic keys {generic_keys} instead of meaningful names. "
            f"Collection: {description}"
        )


@pytest.mark.parametrize(
    "fixture_file,description",
    [
        pytest.param("HLSL30_umm.json", "HLS Landsat 30m", id="HLSL30"),
        pytest.param("HLSS30_umm.json", "HLS Sentinel 30m", id="HLSS30"),
        pytest.param("EMITL2ARFL_umm.json", "EMIT L2A Reflectance", id="EMITL2ARFL"),
        pytest.param("GEDI02_B_umm.json", "GEDI L2B", id="GEDI02_B"),
    ],
)
def test_s3_and_https_assets_grouped(fixture_file, description):
    """Test that S3 and HTTPS versions of the same file are grouped together.

    With ``access="s3"``, S3 URLs are the primary href and HTTPS is kept as
    an alternate access method in the same asset.
    """
    from earthaccess.search import DataGranule

    # Load fixture data
    fixture_data = load_fixture(fixture_file)

    # Create DataGranule (cloud_hosted=True for grouping behavior)
    granule = DataGranule(fixture_data, cloud_hosted=True)

    # Convert to STAC, preferring S3 hrefs
    stac = granule.to_stac(access="s3")
    assets = stac.assets

    # Count how many assets have both S3 and HTTPS versions
    assets_with_alternate = 0
    for key, asset in assets.items():
        href = asset.href
        if href.startswith("s3://"):
            # S3 is primary, check for HTTPS alternate
            alt = asset.extra_fields.get("alternate")
            if alt is not None:
                alt_href = alt.get("href", "")
                if alt_href.startswith("https://"):
                    assets_with_alternate += 1

    # For cloud-hosted data with both S3 and HTTPS URLs, we expect grouping
    # (At minimum, data assets should have alternates)
    data_assets = [
        k for k in assets.keys() if not k.startswith("thumbnail") and k != "browse"
    ]
    if data_assets:
        assert assets_with_alternate > 0, (
            f"Expected S3 assets to have HTTPS alternates. Collection: {description}"
        )


def test_asset_objects_carry_alternate_access_url():
    """Asset objects expose both the primary href and the alternate scheme."""
    from earthaccess.search import DataGranule

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

    assets = granule.assets()
    b02 = next(a for a in assets if a.title or "B02" in a.href)

    # Cloud-hosted: S3 is the primary href, HTTPS is the alternate
    assert b02.href.startswith("s3://")
    assert b02.alternate is not None
    assert b02.alternate.startswith("https://")

    # The alternate scheme is exposed for the formatter to render both icons
    assert set([b02.href, b02.alternate]) == {
        "s3://lp-prod/HLS.L30.T10SEG.2023001T185019.v2.0.B02.tif",
        "https://data.lpdaac.earthdatacloud.nasa.gov/lp-prod/HLS.L30.T10SEG.2023001T185019.v2.0.B02.tif",
    }


def test_collection_s3_credentials_is_cached():
    """DataCollection.s3_credentials fetches the endpoint once and caches it."""
    from unittest.mock import patch

    from earthaccess.auth import Auth

    creds = {
        "accessKeyId": "KEY",
        "secretAccessKey": "SECRET",
        "sessionToken": "TOKEN",
    }
    collection = DataCollection(
        {
            "umm": {
                "DirectDistributionInformation": {
                    "S3CredentialsAPIEndpoint": "https://data.example.nasa.gov/s3credentials",
                },
            },
            "meta": {},
        }
    )

    with patch.object(Auth, "get_s3_credentials", return_value=creds) as mock:
        assert collection.s3_credentials == creds
        assert collection.s3_credentials == creds
        mock.assert_called_once_with(
            endpoint="https://data.example.nasa.gov/s3credentials",
        )


def test_collection_s3_credentials_raises_without_endpoint():
    """DataCollection.s3_credentials raises when no S3CredentialsAPIEndpoint exists."""
    collection = DataCollection(
        {"umm": {"DirectDistributionInformation": {}}, "meta": {}}
    )

    with pytest.raises(ValueError, match="S3CredentialsAPIEndpoint"):
        _ = collection.s3_credentials


def test_granule_s3_credentials_is_cached():
    """DataGranule.s3_credentials derives the endpoint and caches the result."""
    from unittest.mock import patch

    from earthaccess.auth import Auth

    creds = {
        "accessKeyId": "KEY",
        "secretAccessKey": "SECRET",
        "sessionToken": "TOKEN",
    }
    granule = DataGranule(
        {
            "umm": {
                "RelatedUrls": [
                    {
                        "URL": "https://data.example.nasa.gov/s3credentials",
                        "Type": "GET DATA",
                    },
                ],
            },
            "meta": {},
        }
    )

    with patch.object(Auth, "get_s3_credentials", return_value=creds) as mock:
        assert granule.s3_credentials == creds
        assert granule.s3_credentials == creds
        mock.assert_called_once()


def test_granule_s3_credentials_raises_without_endpoint():
    """DataGranule.s3_credentials raises when no s3credentials endpoint exists."""
    granule = DataGranule({"umm": {"RelatedUrls": []}, "meta": {}})

    with pytest.raises(ValueError, match="s3credentials endpoint"):
        _ = granule.s3_credentials
