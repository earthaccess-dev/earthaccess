"""Tests for Earthdata system configuration and proxy routing."""

import pytest
import responses
from earthaccess import status
from earthaccess.auth import PROD, Auth
from earthaccess.auth.auth import BasicAuthResponseHook
from earthaccess.auth.system import (
    PROXY_ENV_VAR,
    System,
    get_proxy,
    set_proxy,
)
from earthaccess.search import DataGranulesQuery

PROXY = "https://earthdata-proxy.example.workers.dev"


@pytest.fixture(autouse=True)
def reset_proxy(monkeypatch):
    """Ensure each test starts with no proxy configured."""
    monkeypatch.delenv(PROXY_ENV_VAR, raising=False)
    set_proxy(None)
    yield
    set_proxy(None)


class TestProxyConfiguration:
    def test_default_is_no_proxy(self):
        assert get_proxy() is None

    def test_set_proxy_normalizes_trailing_slash(self):
        set_proxy(PROXY)
        assert get_proxy() == f"{PROXY}/"

    def test_set_proxy_none_disables_proxy(self):
        set_proxy(PROXY)
        set_proxy(None)
        assert get_proxy() is None

    def test_env_var_fallback(self, monkeypatch):
        monkeypatch.setenv(PROXY_ENV_VAR, PROXY)
        assert get_proxy() == f"{PROXY}/"

    def test_explicit_proxy_wins_over_env_var(self, monkeypatch):
        monkeypatch.setenv(PROXY_ENV_VAR, "https://from-env.example")
        set_proxy(PROXY)
        assert get_proxy() == f"{PROXY}/"

    def test_route_leaves_url_unchanged_without_proxy(self):
        assert PROD.route(PROD.cmr_base_url) == PROD.cmr_base_url

    def test_route_prefixes_url_with_proxy(self):
        set_proxy(PROXY)
        assert PROD.route(PROD.cmr_base_url) == f"{PROXY}/{PROD.cmr_base_url}"


class TestProxiedEDL:
    def test_find_or_create_token_url_is_proxied(self):
        set_proxy(PROXY)
        auth = Auth()
        assert auth.EDL_FIND_OR_CREATE_TOKEN_URL == (
            f"{PROXY}/https://urs.earthdata.nasa.gov/api/users/find_or_create_token"
        )

    def test_edl_hostname_is_never_rewritten(self):
        set_proxy(PROXY)
        auth = Auth()
        assert auth.system.edl_hostname == "urs.earthdata.nasa.gov"


class TestAuthHookProxy:
    def setup_method(self):
        self.hook = BasicAuthResponseHook("urs.earthdata.nasa.gov", ("u", "p"))

    def test_direct_edl_url_matches(self):
        assert self.hook._is_edl_url(
            "https://urs.earthdata.nasa.gov/api/users/find_or_create_token"
        )

    def test_proxied_edl_url_matches(self):
        set_proxy(PROXY)
        assert self.hook._is_edl_url(
            f"{PROXY}/https://urs.earthdata.nasa.gov/api/users/find_or_create_token"
        )

    def test_other_hosts_do_not_match(self):
        set_proxy(PROXY)
        assert not self.hook._is_edl_url("https://example.com/")
        assert not self.hook._is_edl_url(
            f"{PROXY}/https://urs.earthdata.nasa.gov.evil.com/profile"
        )
        assert not self.hook._is_edl_url(f"{PROXY}/https://evil.com/profile")


class TestProxiedCMR:
    def test_granule_query_targets_proxy(self):
        set_proxy(PROXY)
        query = DataGranulesQuery(Auth()).short_name("ATL08")
        assert query._build_url().startswith(
            f"{PROXY}/https://cmr.earthdata.nasa.gov/search/granules"
        )

    def test_granule_query_targets_proxy_without_auth(self):
        set_proxy(PROXY)
        query = DataGranulesQuery().short_name("ATL08")
        assert query._build_url().startswith(
            f"{PROXY}/https://cmr.earthdata.nasa.gov/search/granules"
        )


@responses.activate
def test_status_uses_proxy():
    set_proxy(PROXY)
    responses.add(
        responses.GET,
        f"{PROXY}/https://status.earthdata.nasa.gov/api/v1/statuses",
        json={
            "statuses": [
                {"name": "Earthdata Login", "status": "OK"},
                {"name": "Common Metadata Repository", "status": "OK"},
            ]
        },
        status=200,
    )

    assert status() == {
        "Earthdata Login": "OK",
        "Common Metadata Repository": "OK",
    }


def test_system_is_frozen():
    system = System(
        PROD.cmr_base_url, PROD.status_url, PROD.status_api_url, PROD.edl_hostname
    )
    with pytest.raises(Exception):
        system.edl_hostname = "other"  # type: ignore
