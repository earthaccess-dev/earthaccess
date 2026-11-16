# package imports
import datetime as dt

import pytest
from earthaccess.search import DataCollectionsQuery

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


def test_default_params():
    query = DataCollectionsQuery()
    assert query.params == {"has_granules": True}


def test_query_can_find_cloud_provider():
    query = DataCollectionsQuery().daac("PODAAC").cloud_hosted(True)
    assert query.params["provider"] == "POCLOUD"
    query = DataCollectionsQuery().cloud_hosted(True).daac("PODAAC")
    assert query.params["provider"] == "POCLOUD"
    # SEDAC does not have a cloud provider, so it should default to the on prem provider
    query = DataCollectionsQuery().cloud_hosted(True).daac("SEDAC")
    assert query.params["provider"] == "ESDIS"
    query = DataCollectionsQuery().daac("ASDC").cloud_hosted(True)
    assert query.params["provider"] == "LARC_CLOUD"
    query = DataCollectionsQuery().cloud_hosted(True).daac("ASDC")
    assert query.params["provider"] == "LARC_CLOUD"


def test_querybuilder_can_handle_doi():
    doi = "10.5067/AQR50-3Q7CS"
    query = DataCollectionsQuery().doi(doi)
    assert query.params["doi"] == doi
    query = DataCollectionsQuery().cloud_hosted(True).daac("PODAAC").doi(doi)
    assert query.params["doi"] == doi


def test_querybuilder_can_handle_has_granules():
    query = DataCollectionsQuery().has_granules(False)
    assert not query.params["has_granules"]
    query = DataCollectionsQuery().has_granules(True)
    assert query.params["has_granules"]
    query = DataCollectionsQuery().has_granules(None)
    assert "has_granules" not in query.params


@pytest.mark.parametrize("has_granules", [True, False])
def test_url_includes_default_or_explicit_has_granules(has_granules):
    url = DataCollectionsQuery().parameters(has_granules=has_granules)._build_url()
    assert f"has_granules={str(has_granules).lower()}" in url


def test_url_omits_has_granules_when_none():
    url = DataCollectionsQuery().parameters(has_granules=None)._build_url()
    assert "has_granules" not in url


@pytest.mark.parametrize("start,end,expected", valid_single_dates)
def test_query_can_parse_single_dates(start, end, expected):
    query = DataCollectionsQuery().temporal(start, end)
    assert query.params["temporal"][0] == expected


@pytest.mark.parametrize("start,end,expected", invalid_single_dates)
def test_query_can_handle_invalid_dates(start, end, expected):  # noqa: ARG001
    query = DataCollectionsQuery()
    assert "temporal" not in query.params
    with pytest.raises(ValueError):
        query.temporal(start, end)
