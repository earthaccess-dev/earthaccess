import contextlib
import json
import logging
import os.path
import pickle

import earthaccess
import pytest
import responses
from earthaccess.results import DataCollection, DataGranule
from earthaccess.search import DataCollectionsQuery, DataGranulesQuery
from earthaccess.utils._search import get_results
from vcr.unittest import VCRTestCase  # type: ignore[import-untyped]

logging.basicConfig()
logging.getLogger("vcr").setLevel(logging.ERROR)

REDACTED_STRING = "REDACTED"


def unique_results(results):
    """When we invoke a search request multiple times we want to ensure that we don't
    get the same results back. This is a one shot test as the results are preserved
    by VCR but still useful.
    """
    unique_concept_ids = {result["meta"]["concept-id"] for result in results}
    return len(unique_concept_ids) == len(results)


def redact_login_request(request):
    if "/api/users/" in request.path and "/api/users/tokens" not in request.path:
        _, user_name = os.path.split(request.path)
        request.uri = request.uri.replace(user_name, REDACTED_STRING)

    return request


def redact_key_values(keys_to_redact):
    def redact(payload):
        for key in keys_to_redact:
            if key in payload:
                payload[key] = REDACTED_STRING
        return payload

    def before_record_response(response):
        body = response["body"]["string"].decode("utf8")

        with contextlib.suppress(json.JSONDecodeError):
            payload = json.loads(body)
            redacted_payload = (
                list(map(redact, payload))
                if isinstance(payload, list)
                else redact(payload)
            )
            response["body"]["string"] = json.dumps(redacted_payload).encode()

        return response

    return before_record_response


class TestResults(VCRTestCase):
    def _get_vcr(self, **kwargs):
        myvcr = super()._get_vcr(**kwargs)
        myvcr.cassette_library_dir = "tests/unit/fixtures/vcr_cassettes"
        myvcr.decode_compressed_response = True
        # Header matching is not set by default, we need that to test the
        # search-after functionality is performing correctly.
        myvcr.match_on = [
            "method",
            "scheme",
            "host",
            "port",
            "path",
            "query",
            "headers",
        ]
        myvcr.filter_headers = [
            "Accept-Encoding",
            "Authorization",
            "Cookie",
            "Set-Cookie",
            "User-Agent",
        ]
        myvcr.filter_query_parameters = [
            ("client_id", REDACTED_STRING),
        ]

        myvcr.before_record_response = redact_key_values(
            [
                "access_token",
                "uid",
                "first_name",
                "last_name",
                "email_address",
                "nams_auid",
            ],
        )

        myvcr.before_record_request = redact_login_request

        return myvcr

    def test_no_results(self):
        """If we search for a collection that doesn't exist, we should get no results."""
        granules = earthaccess.search_data(
            # STAC collection name; correct short name is OPERA_L3_DSWX-HLS_V1
            # Example discussed in: https://github.com/earthaccess-dev/earthaccess/pull/839
            short_name="OPERA_L3_DSWX-HLS_V1_1.0",
            bounding_box=(-95.19, 30.59, -94.99, 30.79),
            temporal=("2024-04-30", "2024-05-31"),
        )
        assert len(granules) == 0

    def test_data_links(self):
        granules = earthaccess.search_data(
            short_name="SEA_SURFACE_HEIGHT_ALT_GRIDS_L4_2SATS_5DAY_6THDEG_V_JPL2205",
            temporal=("2020", "2022"),
            count=1,
        )

        g = granules[0]
        assert g.data_links()[0].startswith("https://")
        assert g.data_links(access="https")[0].startswith("https://")
        assert g.data_links(access="s3")[0].startswith("s3://")

    def test_get_more_than_2000(self):
        """If we execute a get with a limit of more than 2000
        then we expect multiple invocations of a cmr granule search and
        to not fetch back more results than we ask for.
        """
        granules = earthaccess.search_data(short_name="MOD02QKM", count=3000)

        # Assert that we performed one 'hits' search and two 'results' search queries
        self.assertEqual(len(self.cassette), 3)
        self.assertEqual(len(granules), 4000)
        self.assertTrue(unique_results(granules))

    def test_get(self):
        """If we execute a get with no arguments then we expect
        to get the maximum no. of granules from a single CMR call (2000)
        in a single request.
        """
        granules = earthaccess.search_data(short_name="MOD02QKM", count=2000)

        # Assert that we performed one 'hits' search and one 'results' search queries
        self.assertEqual(len(self.cassette), 2)
        self.assertEqual(len(granules), 2000)
        self.assertTrue(unique_results(granules))

    def test_get_all_less_than_2k(self):
        """If we execute a get_all then we expect multiple
        invocations of a cmr granule search and
        to not fetch back more results than we ask for.
        """
        granules = earthaccess.search_data(
            short_name="TELLUS_GRAC_L3_JPL_RL06_LND_v04",
            count=2000,
        )

        # Assert that we performed a hits query, a results query, and a final
        # query that returns an empty page (to detect the end of the results).
        self.assertEqual(len(self.cassette), 3)
        self.assertEqual(len(granules), 163)
        self.assertTrue(unique_results(granules))

    def test_get_all_more_than_2k(self):
        """If we execute a get_all then we expect multiple
        invocations of a cmr granule search and
        to not fetch back more results than we ask for.
        """
        granules = earthaccess.search_data(
            short_name="CYGNSS_NOAA_L2_SWSP_25KM_V1.2",
            count=3000,
        )

        # Assert that we performed a hits query, two results queries, and a
        # final query that returns an empty page.
        self.assertEqual(len(self.cassette), 4)
        self.assertEqual(
            len(granules),
            int(self.cassette.responses[0]["headers"]["CMR-Hits"][0]),
        )
        self.assertEqual(
            len(granules),
            min(3000, int(self.cassette.responses[0]["headers"]["CMR-Hits"][0])),
        )
        self.assertTrue(unique_results(granules))

    @responses.activate
    def test_get_paginates_past_short_first_page(self):
        """A page shorter than the requested page_size must not end pagination.

        Regression test: for some searches CMR returns fewer than `page_size`
        items on a page even though more results remain (e.g. the granule search
        for C3974616058-LPCLOUD over 2024 returns 1985 items on the first page
        of 2000). The old code stopped paginating after such a short page,
        silently dropping the rest of the results.
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

        self.assertEqual(len(results), 3985)
        self.assertEqual(len(responses.calls), 3)

    def test_collections_less_than_2k(self):
        """If we execute a get_all then we expect multiple
        invocations of a cmr granule search and
        to not fetch back more results than we ask for.
        """
        query = DataCollectionsQuery().daac("PODAAC").cloud_hosted(True)
        collections = query.get(20)

        # Assert that we performed a single search results query
        self.assertEqual(len(self.cassette), 1)
        self.assertEqual(len(collections), 20)
        self.assertTrue(unique_results(collections))
        self.assert_is_using_search_after(self.cassette)

    def test_collections_more_than_2k(self):
        """If we execute a get_all then we expect multiple
        invocations of a cmr granule search and
        to not fetch back more results than we ask for.
        """
        query = DataCollectionsQuery()
        collections = query.get(3000)

        # Assert that we performed two search results queries
        self.assertEqual(len(self.cassette), 2)
        self.assertEqual(len(collections), 4000)
        self.assertTrue(unique_results(collections))
        self.assert_is_using_search_after(self.cassette)

    def assert_is_using_search_after(self, cass):
        first_request = True

        for request in cass.requests:
            # Verify the page number was not used
            self.assertTrue("page_num" not in request.uri)
            # Verify that Search After was used in all requests except first
            self.assertEqual(first_request, "CMR-Search-After" not in request.headers)
            first_request = False


def test_get_doi_returns_doi_when_present():
    collection = DataCollection(
        {"umm": {"DOI": {"DOI": "doi:10.16904/envidat.lwf.34"}}, "meta": {}},
    )

    assert collection.doi == "doi:10.16904/envidat.lwf.34"


def test_get_doi_returns_empty_string_when_doi_missing():
    collection = DataCollection({"umm": {"DOI": {}}, "meta": {}})

    assert collection.doi is None


def test_get_doi_returns_empty_string_when_doi_key_missing():
    collection = DataCollection({"umm": {}, "meta": {}})

    assert collection.doi is None


@responses.activate
def test_get_citation_apa_format():
    collection = DataCollection(
        {"umm": {"DOI": {"DOI": "doi:10.16904/envidat.lwf.34"}}, "meta": {}},
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
        {"umm": {"DOI": {"DOI": "doi:10.16904/envidat.lwf.34"}}, "meta": {}},
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


@pytest.mark.parametrize(
    ("access", "expected_prefix"),
    [
        pytest.param(None, "https://", id="default"),
        pytest.param("https", "https://", id="https"),
        pytest.param("external", "https://", id="legacy-external"),
        pytest.param("indirect", "https://", id="legacy-indirect"),
        pytest.param("s3", "s3://", id="s3"),
        pytest.param("direct", "s3://", id="legacy-direct"),
    ],
)
def test_data_links_access_aliases(access, expected_prefix):
    granule = DataGranule(
        {
            "umm": {
                "RelatedUrls": [
                    {
                        "URL": "https://data.example.nasa.gov/protected/coll/file.nc",
                        "Type": "GET DATA",
                    },
                    {
                        "URL": "s3://protected/coll/file.nc",
                        "Type": "GET DATA VIA DIRECT ACCESS",
                    },
                ],
            },
            "meta": {},
        },
        cloud_hosted=True,
    )

    kwargs = {} if access is None else {"access": access}
    links = granule.data_links(**kwargs)

    assert links
    assert all(link.startswith(expected_prefix) for link in links)


def test_derived_s3_links_use_each_link_not_just_the_first():
    """When a cloud granule only offers HTTPS links, each derived S3 link should
    come from its own HTTPS link, not from the first one repeated.
    """
    granule = DataGranule(
        {
            "umm": {
                "RelatedUrls": [
                    {
                        "URL": "https://data.example.nasa.gov/protected/coll/file_a.nc",
                        "Type": "GET DATA",
                    },
                    {
                        "URL": "https://data.example.nasa.gov/protected/coll/file_b.nc",
                        "Type": "GET DATA",
                    },
                ],
            },
            "meta": {},
        },
        cloud_hosted=True,
    )

    s3_links = granule.data_links(access="s3")

    assert s3_links == [
        "s3://protected/coll/file_a.nc",
        "s3://protected/coll/file_b.nc",
    ]
    # Each input link maps to a distinct S3 URL; the first is not duplicated.
    assert len(set(s3_links)) == len(s3_links)


def test_data_links_in_region_preserves_https_for_https():
    url = "https://example.com/data/file.nc"
    granule = DataGranule(
        {
            "umm": {"RelatedUrls": [{"URL": url, "Type": "GET DATA"}]},
            "meta": {},
        },
        cloud_hosted=False,
    )

    with pytest.deprecated_call():
        links = granule.data_links(in_region=True)

    assert links == [url]


def test_data_links_s3_skips_unrecognized_protected_urls():
    granule = DataGranule(
        {
            "umm": {
                "RelatedUrls": [
                    {"URL": "https://example.com/protected_file.nc", "Type": "GET DATA"}
                ]
            },
            "meta": {},
        },
        cloud_hosted=True,
    )

    assert granule.data_links(access="s3") == []


def test_data_links_s3_uses_explicit_links_without_cloud_hosted():
    granule = DataGranule(
        {
            "umm": {
                "RelatedUrls": [
                    {"URL": "s3://bucket/file.nc", "Type": "GET DATA VIA DIRECT ACCESS"}
                ]
            },
            "meta": {},
        },
        cloud_hosted=False,
    )

    assert granule.data_links(access="s3") == ["s3://bucket/file.nc"]


# --- Results containers -------------------------------------------------------


def _granule(n: int) -> DataGranule:
    return DataGranule(
        {
            "meta": {"concept-id": f"G{n}-PROV", "provider-id": "PROV"},
            "umm": {"GranuleUR": f"granule-{n}"},
        }
    )


class _CountingSource:
    """An iterator that records how many items have been taken from it."""

    def __init__(self, items):
        self._items = iter(items)
        self.taken = 0

    def __iter__(self):
        return self

    def __next__(self):
        item = next(self._items)
        self.taken += 1
        return item


def test_results_from_list_can_be_iterated_repeatedly():
    granules = [_granule(n) for n in range(3)]
    results = earthaccess.GranuleResults(granules)

    assert list(results) == granules
    assert list(results) == granules
    assert len(results) == 3
    assert results[0] is granules[0]
    assert results[-1] is granules[-1]
    assert results[1:] == granules[1:]
    assert bool(results)
    assert not earthaccess.GranuleResults([])


def test_results_from_iterator_load_once_and_only_as_needed():
    granules = [_granule(n) for n in range(5)]
    source = _CountingSource(granules)
    results = earthaccess.GranuleResults(source)

    assert source.taken == 0
    assert results[1] is granules[1]
    assert source.taken == 2

    assert list(results) == granules
    assert list(results) == granules
    assert source.taken == 5


def test_results_all_returns_an_independent_list():
    granules = [_granule(n) for n in range(2)]
    results = earthaccess.GranuleResults(iter(granules))

    everything = results.all()
    everything.clear()

    assert results.all() == granules


def test_results_query_params_are_a_snapshot():
    bounding_box = [-10, 20, 10, 50]
    results = earthaccess.GranuleResults(
        [], query_params={"short_name": "ATL06", "bounding_box": bounding_box}
    )

    bounding_box.append(99)
    returned = results.query_params
    assert returned is not None
    returned["short_name"] = "changed"

    assert results.query_params == {
        "short_name": "ATL06",
        "bounding_box": [-10, 20, 10, 50],
    }


def test_results_display_uses_stored_search_details_without_contacting_cmr():
    results = earthaccess.GranuleResults(
        [_granule(0)],
        query_params={"short_name": "ATL06"},
        query_options={"temporal": {"exclude_boundary": True}},
        hits=1234,
    )

    with responses.RequestsMock():  # fails if any HTTP request is made
        text_repr = repr(results)
        text_str = str(results)
        text_html = results._repr_html_()

    assert text_repr == (
        "GranuleResults(params={'short_name': 'ATL06'}, "
        "options={'temporal': {'exclude_boundary': True}})"
    )
    assert text_str == (
        "Number of results: 1234\n"
        "Query parameters:\n"
        "    {'short_name': 'ATL06'}\n"
        "Query parameter options:\n"
        "    {'temporal': {'exclude_boundary': True}}"
    )
    for row in [
        "<td>Query Type</td>",
        '<td style="text-align: left;">DataGranulesQuery</td>',
        "<td>Number of Results</td>",
        '<td style="text-align: left;">1234</td>',
        "<td>Query parameters</td>",
        "<td>Query options</td>",
    ]:
        assert row in text_html


def test_lazy_results_can_be_pickled():
    granules = [_granule(n) for n in range(3)]
    results = earthaccess.GranuleResults(iter(granules), query_params={"count": 3})

    restored = pickle.loads(pickle.dumps(results))

    assert [g["meta"]["concept-id"] for g in restored] == [
        g["meta"]["concept-id"] for g in granules
    ]
    assert restored.query_params == {"count": 3}


def test_search_data_records_replayable_query_params(monkeypatch):
    granules = [_granule(n) for n in range(2)]
    monkeypatch.setattr(DataGranulesQuery, "hits", lambda _self: 40)
    monkeypatch.setattr(DataGranulesQuery, "get", lambda _self, _limit=0: granules)

    results = earthaccess.search_data(short_name="ATL06", count=2)

    assert isinstance(results, earthaccess.GranuleResults)
    assert results.query_params == {"count": 2, "short_name": "ATL06"}
    assert results.hits == 40
    assert list(results) == granules

    again = earthaccess.search_data(**results.query_params)
    assert again.query_params == results.query_params


def test_each_search_returns_results_with_its_own_query_params(monkeypatch):
    monkeypatch.setattr(DataGranulesQuery, "hits", lambda _self: 1)
    monkeypatch.setattr(DataGranulesQuery, "get", lambda _self, _limit=0: [])

    results = earthaccess.search_data(short_name="ATL06", count=1)
    first = results
    results = earthaccess.search_data(
        short_name="ATL03", temporal=("2020-01-01", "2020-02-01", True), count=1
    )

    assert results.query_params == {
        "count": 1,
        "short_name": "ATL03",
        "temporal": ("2020-01-01", "2020-02-01", True),
    }
    assert "{'temporal': {'exclude_boundary': True}}" in repr(results)
    assert first.query_params == {"count": 1, "short_name": "ATL06"}


def test_search_datasets_returns_collection_results(monkeypatch):
    collections = [DataCollection({"meta": {"concept-id": "C1-PROV"}, "umm": {}})]
    monkeypatch.setattr(DataCollectionsQuery, "hits", lambda _self: 1)
    monkeypatch.setattr(
        DataCollectionsQuery, "get", lambda _self, _limit=0: collections
    )

    results = earthaccess.search_datasets(short_name="ATL06", count=1)

    assert isinstance(results, earthaccess.CollectionResults)
    assert results.query_params == {"count": 1, "short_name": "ATL06"}
    assert results[0] is collections[0]


@pytest.mark.parametrize("function", ["download", "open"])
def test_download_and_open_pass_a_plain_list_to_the_store(monkeypatch, function):
    received = []

    class FakeStore:
        def get(self, granules, *_args, **_kwargs):
            received.append(granules)
            return []

        def open(self, granules, *_args, **_kwargs):
            received.append(granules)
            return []

    monkeypatch.setattr(earthaccess, "__store__", FakeStore(), raising=False)
    granules = [_granule(n) for n in range(2)]

    getattr(earthaccess, function)(earthaccess.GranuleResults(iter(granules)))

    assert type(received[0]) is list
    assert received[0] == granules
