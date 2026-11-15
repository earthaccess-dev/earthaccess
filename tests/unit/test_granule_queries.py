# package imports
import datetime as dt
from unittest import mock

import pytest
from earthaccess.search import DataGranulesQuery
from earthaccess.search.queries import is_cloud_hosted

valid_single_dates = [
    ("2001-12-12", "2001-12-21", "2001-12-12T00:00:00Z,2001-12-21T23:59:59Z"),
    ("2021-02-01", "", "2021-02-01T00:00:00Z,"),
    ("1999-02-01 06:00", "2009-01-01", "1999-02-01T06:00:00Z,2009-01-01T23:59:59Z"),
    (
        dt.datetime(2021, 2, 1, tzinfo=dt.UTC),
        dt.datetime(2021, 2, 2, tzinfo=dt.UTC),
        "2021-02-01T00:00:00Z,2021-02-02T00:00:00Z",
    ),
    (
        "2019-03-10T00:00:00Z",
        "2019-03-11T23:59:59Z",
        "2019-03-10T00:00:00Z,2019-03-11T23:59:59Z",
    ),
    (
        "2019-03-10T00:00:00Z",
        "2019-03-10T00:00:00-01:00",
        "2019-03-10T00:00:00Z,2019-03-10T01:00:00Z",
    ),
]

invalid_single_dates = [
    ("2001-12-45", "2001-12-21", None),
    ("2021w1", "", None),
    ("2999-02-01", "2009-01-01", None),
]


bbox_queries = [
    ([-134.7, 54.9, -100.9, 69.2], True),
    ([-10, 20, 0, 40], True),
    ([10, 20, 30, 40], True),
]


@pytest.mark.parametrize("start,end,expected", valid_single_dates)
def test_query_can_parse_single_dates(start, end, expected):
    granules = DataGranulesQuery().short_name("MODIS").temporal(start, end)
    assert granules.params["temporal"][0] == expected


@pytest.mark.parametrize("start,end,expected", invalid_single_dates)
def test_query_can_handle_invalid_dates(start, end, expected):  # noqa: ARG001
    granules = DataGranulesQuery().short_name("MODIS")
    assert "temporal" not in granules.params
    with pytest.raises(ValueError):
        granules.temporal(start, end)


@pytest.mark.parametrize("bbox,expected", bbox_queries)
def test_query_handles_bbox(bbox, expected):
    granules = DataGranulesQuery().short_name("MODIS").bounding_box(*bbox)
    assert ("bounding_box" in granules.params) == expected


def _collection(concept_id: str):
    return mock.Mock(concept_id=concept_id)


def test_doi_single_collection_sets_concept_id():
    with mock.patch("earthaccess.search.queries.DataCollectionsQuery") as dc:
        dc.return_value.doi.return_value.get.return_value = [_collection("C1-FOO")]
        granules = DataGranulesQuery().doi("10.5067/AQR50-3Q7CS")
    assert granules.params["concept_id"] == "C1-FOO"


def test_doi_no_collection_warns_and_sets_nothing(caplog):
    with mock.patch("earthaccess.search.queries.DataCollectionsQuery") as dc:
        dc.return_value.doi.return_value.get.return_value = []
        granules = DataGranulesQuery().doi("10.5067/NOPE")
    assert "concept_id" not in granules.params
    assert "couldn't find any associated collections" in caplog.text


def test_doi_multiple_collections_warns_and_picks_first(caplog):
    with mock.patch("earthaccess.search.queries.DataCollectionsQuery") as dc:
        dc.return_value.doi.return_value.get.return_value = [
            _collection("C1-FOO"),
            _collection("C2-BAR"),
        ]
        granules = DataGranulesQuery().doi("10.5067/MULTI")
    assert granules.params["concept_id"] == "C1-FOO"
    assert "maps to 2 collections" in caplog.text


@pytest.mark.parametrize(
    "related_urls,expected",
    [
        ([], False),
        ([{"Type": "GET DATA", "URL": "https://data.example.com/f.nc"}], False),
        (
            [
                {"Type": "GET DATA", "URL": "https://data.example.com/f.nc"},
                {
                    "Type": "GET DATA VIA DIRECT ACCESS",
                    "URL": "s3://cumulus-prod-protected/f.nc",
                },
            ],
            True,
        ),
        ([{"Type": "GET DATA", "URL": "s3://bucket-protected/f.nc"}], True),
    ],
)
def test_is_cloud_hosted(related_urls, expected):
    granule = {"umm": {"RelatedUrls": related_urls}}
    assert is_cloud_hosted(granule) is expected
