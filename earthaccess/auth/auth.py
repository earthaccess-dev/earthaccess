from __future__ import annotations

import getpass
import importlib.metadata
import logging
import os
import platform
import shutil
from netrc import NetrcParseError
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlparse

import requests
import requests.cookies
from tinynetrc import Netrc
from typing_extensions import deprecated

from earthaccess.auth.system import PROD, System, get_proxy, route
from earthaccess.exceptions import LoginAttemptFailure, LoginStrategyUnavailable
from earthaccess.store.daac import DAACS

try:
    user_agent = f"earthaccess v{importlib.metadata.version('earthaccess')}"
except importlib.metadata.PackageNotFoundError:
    user_agent = "earthaccess"


logger = logging.getLogger(__name__)


def netrc_path() -> Path:
    """Return the path of the `.netrc` file.

    The path may or may not exist.

    See [the `.netrc` file](https://www.gnu.org/software/inetutils/manual/html_node/The-_002enetrc-file.html).

    Returns:
        `Path` of the `NETRC` environment variable, if the value is non-empty;
        otherwise, the path of the platform-specific default location:
        `~/_netrc` on Windows systems, `~/.netrc` on non-Windows systems.
    """
    sys_netrc_name = "_netrc" if platform.system() == "Windows" else ".netrc"
    env_netrc = os.environ.get("NETRC")

    return Path(env_netrc) if env_netrc else Path.home() / sys_netrc_name


class BasicAuthResponseHook:
    """Response hook that adds EDL credentials only when redirected to the EDL host.

    This hook ensures that credentials are sent only to the EDL host, not to any
    other host that might be in the redirect chain. This is more secure than
    maintaining a hardcoded allowlist of hosts.
    """

    def __init__(self, hostname: str, auth: tuple[str, str]) -> None:
        self.hostname = hostname
        self.auth = auth

    def _is_edl_url(self, url: str) -> bool:
        """Return True if `url` targets EDL, directly or through the proxy."""
        if urlparse(url).hostname == self.hostname:
            return True

        proxy = get_proxy()
        if not proxy or not url.startswith(proxy):
            return False

        # Through the proxy, the target URL is embedded as the path. Parse it
        # and compare hostnames exactly so that look-alike hosts (e.g.
        # "urs.earthdata.nasa.gov.evil.com") do not receive credentials.
        target = urlparse(url[len(proxy) :])
        return target.scheme == "https" and target.hostname == self.hostname

    def __call__(self, r: requests.Response, **kwargs: Any) -> requests.Response:
        from http.cookiejar import CookieJar

        # If the response's URL is not for the EDL system we're authenticating
        # against, then simply return the response unchanged.  Otherwise, we'll
        # prepare a new request below with the user's EDL credentials.
        if not self._is_edl_url(r.url):
            return r

        # Consume content and release the original connection to allow our new
        # request to reuse the same one.
        r.content
        r.close()

        prepared_request = r.request.copy()
        cookies: CookieJar = prepared_request._cookies  # type: ignore
        requests.cookies.extract_cookies_to_jar(cookies, r.request, r.raw)
        prepared_request.prepare_cookies(cookies)
        prepared_request.prepare_auth(self.auth)

        new_r = r.connection.send(prepared_request, **kwargs)
        new_r.history.append(r)
        new_r.request = prepared_request

        return new_r


def _create_earthdata_session(
    edl_hostname: str, auth: tuple[str, str] | None = None
) -> requests.Session:
    """Create a requests session configured for Earthdata authentication.

    This function creates a standard requests.Session with:
    - User-Agent header set to earthaccess version
    - Response hook for EDL authentication during redirects (if auth provided)

    Parameters:
        edl_hostname: The EDL hostname (e.g., "urs.earthdata.nasa.gov")
        auth: Optional tuple of (username, password) for EDL authentication

    Returns:
        A configured requests.Session instance.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})

    if auth:
        hook = BasicAuthResponseHook(edl_hostname, auth)
        session.hooks["response"].append(hook)

    return session


@deprecated(
    "SessionWithHeaderRedirection is deprecated. "
    "Use auth.get_session() to get an authenticated session, "
    "or _create_earthdata_session() for low-level session creation."
)
def SessionWithHeaderRedirection(
    edl_hostname: str, auth: tuple[str, str] | None = None
) -> requests.Session:
    """Create a requests session configured for Earthdata authentication.

    .. deprecated:: 1.0.0a2
        Use :meth:`Auth.get_session` to get an authenticated session, or
        :func:`_create_earthdata_session` for low-level session creation.

    This function is a backward compatibility alias for ``_create_earthdata_session()``.

    Parameters:
        edl_hostname: The EDL hostname (e.g., "urs.earthdata.nasa.gov")
        auth: Optional tuple of (username, password) for EDL authentication

    Returns:
        A configured requests.Session instance.
    """
    return _create_earthdata_session(edl_hostname, auth)


class Auth(object):
    """Authentication class for operations that require Earthdata login (EDL).

    This class handles authentication with NASA's Earthdata Login service,
    providing access to restricted datasets and cloud resources.

    Note:
        Users should typically use `earthaccess.login()` rather than
        instantiating this class directly.

    Attributes:
        authenticated (bool): Whether the user is currently authenticated.
        token (dict): The current EDL token, if authenticated.
        system (System): The Earthdata system (PROD or UAT).

    Examples:
        Using the top-level API (recommended):
        ```python
        import earthaccess

        auth = earthaccess.login()
        if auth.authenticated:
            print("Successfully logged in!")
        ```

        Getting S3 credentials for direct data access:
        ```python
        auth = earthaccess.login()
        creds = auth.get_s3_credentials(daac="PODAAC")
        print(creds["accessKeyId"])
        ```

        Getting an authenticated requests session:
        ```python
        auth = earthaccess.login()
        session = auth.get_session()
        response = session.get("https://some-earthdata-url.nasa.gov/data")
        ```
    """

    __module__ = "earthaccess.auth"

    def __init__(self) -> None:
        # Maybe all these predefined URLs should be in a constants.py file
        self.authenticated = False
        self.token: Optional[Mapping[str, str]] = None
        self._login_strategy: Optional[str] = None
        self._set_earthdata_system(PROD)

    def login(
        self,
        strategy: str = "netrc",
        persist: bool = False,
        system: Optional[System] = None,
    ) -> Any:
        """Authenticate with Earthdata login.

        Parameters:
            strategy:
                The authentication method.

                * **"interactive"**: Enter a username and password.
                * **"netrc"**: (default) Retrieve a username and password from ~/.netrc.
                * **"environment"**:
                    Retrieve either a username and password pair from the
                    `EARTHDATA_USERNAME` and `EARTHDATA_PASSWORD` environment variables,
                    or an Earthdata login token from the `EARTHDATA_TOKEN` environment
                    variable.
            persist: Will persist username and password credentials in a `.netrc` file.
            system: the EDL endpoint to log in to Earthdata, defaults to PROD

        Returns:
            An instance of Auth.

        Raises:
            LoginAttemptFailure: If the NASA Earthdata Login service rejects
                credentials.
        """
        if system is not None:
            self._set_earthdata_system(system)

        if self.authenticated and (system == self.system):
            logger.debug("We are already authenticated with NASA EDL")
            return self

        if strategy == "interactive":
            self._interactive(persist)
            self._login_strategy = "interactive"
        elif strategy == "netrc":
            self._netrc()
            self._login_strategy = "netrc"
        elif strategy == "environment":
            self._environment()
            self._login_strategy = "environment"

        return self

    def _set_earthdata_system(self, system: System) -> None:
        self.system = system

    @property
    def EDL_FIND_OR_CREATE_TOKEN_URL(self) -> str:
        """EDL endpoint used to exchange credentials for a token."""
        return route(
            f"https://{self.system.edl_hostname}/api/users/find_or_create_token"
        )

    @property
    def _eula_url(self) -> str:
        return route(
            f"https://{self.system.edl_hostname}/users/earthaccess/unaccepted_eulas"
        )

    @property
    def _apps_url(self) -> str:
        return route(f"https://{self.system.edl_hostname}/application_search")

    @deprecated("No replacement, as tokens are now refreshed automatically.")
    def refresh_tokens(self) -> bool:
        """Refresh CMR tokens.

        Tokens are used to do authenticated queries on CMR for restricted and early access datasets.
        This method renews the tokens to make sure we can query the collections allowed to our EDL user.
        """
        return self.authenticated

    def get_s3_credentials(
        self,
        daac: Optional[str] = None,
        provider: Optional[str] = None,
        endpoint: Optional[str] = None,
    ) -> Dict[str, str]:
        """Gets AWS S3 credentials for a given NASA cloud provider.

        The easier way is to use the DAAC short name; provider is optional if we know it.

        Parameters:
            daac: The name of a NASA DAAC, e.g. NSIDC or PODAAC.
            provider: A valid cloud provider. Each DAAC has a provider code for their cloud distributions.
            endpoint: Getting the credentials directly from the S3Credentials URL.

        Returns:
            A Python dictionary with the temporary AWS S3 credentials containing:
            - accessKeyId: AWS access key ID
            - secretAccessKey: AWS secret access key
            - sessionToken: AWS session token
            - expiration: Token expiration timestamp

        Examples:
            Get credentials by DAAC:
            ```python
            auth = earthaccess.login()
            creds = auth.get_s3_credentials(daac="NSIDC")
            ```

            Get credentials by provider:
            ```python
            creds = auth.get_s3_credentials(provider="POCLOUD")
            ```

            Get credentials from a specific endpoint:
            ```python
            creds = auth.get_s3_credentials(
                endpoint="https://data.lpdaac.earthdatacloud.nasa.gov/s3credentials"
            )
            ```
        """
        if self.authenticated:
            user, pwd = getattr(self, "username", None), getattr(self, "password", None)
            session = (
                _create_earthdata_session(self.system.edl_hostname, (user, pwd))
                if user and pwd
                else self.get_session()
            )
            if endpoint is None:
                auth_url = self._get_cloud_auth_url(
                    daac_shortname=daac, provider=provider
                )
            else:
                auth_url = endpoint
            if auth_url.startswith("https://"):
                cumulus_resp = session.get(auth_url, timeout=15, allow_redirects=True)
                auth_resp = session.get(
                    cumulus_resp.url, allow_redirects=True, timeout=15
                )
                if not (auth_resp.ok):  # type: ignore
                    # Let's try to authenticate with Bearer tokens
                    _session = self.get_session()
                    cumulus_resp = _session.get(
                        auth_url, timeout=15, allow_redirects=True
                    )
                    auth_resp = _session.get(
                        cumulus_resp.url, allow_redirects=True, timeout=15
                    )
                    if not (auth_resp.ok):
                        logger.error(
                            f"Authentication with Earthdata Login failed with:\n{auth_resp.text[0:1000]}"
                        )
                        logger.error(
                            f"Consider accepting the EULAs available at {self._eula_url} and applications at {self._apps_url}"
                        )
                        return {}

                    return auth_resp.json()
                return auth_resp.json()
            else:
                # This happens if the cloud provider doesn't list the S3 credentials or the DAAC
                # does not have cloud collections yet
                logger.info(
                    f"Credentials for the cloud provider {daac} are not available"
                )
                return {}
        else:
            logger.info("We need to authenticate with EDL first")
            return {}

    def get_session(self, bearer_token: bool = True) -> requests.Session:
        """Returns a new request session instance.

        Parameters:
            bearer_token: whether to include bearer token

        Returns:
            A requests Session instance with Auth and bearer token headers.

        Examples:
            Get an authenticated session for custom requests:
            ```python
            auth = earthaccess.login()
            session = auth.get_session()
            response = session.get("https://cmr.earthdata.nasa.gov/search/collections")
            ```

            Get a session without bearer token (uses basic auth on redirects):
            ```python
            session = auth.get_session(bearer_token=False)
            ```
        """
        user, pwd = getattr(self, "username", None), getattr(self, "password", None)
        auth = (user, pwd) if user and pwd else None
        session = _create_earthdata_session(self.system.edl_hostname, auth)

        if bearer_token and self.token:
            # This will avoid the use of the netrc after we are logged in
            session.trust_env = False
            session.headers["Authorization"] = f"Bearer {self.token['access_token']}"

        return session

    def _interactive(
        self,
        persist_credentials: bool = False,
    ) -> bool:
        username = input("Enter your Earthdata Login username: ")
        password = getpass.getpass(prompt="Enter your Earthdata password: ")
        authenticated = self._get_credentials(username, password, None)
        if authenticated:
            logger.debug("Using user provided credentials for EDL")
            if persist_credentials:
                self._persist_user_credentials(username, password)
        return authenticated

    def _netrc(self) -> bool:
        netrc_loc = netrc_path()

        try:
            my_netrc = Netrc(str(netrc_loc))
        except FileNotFoundError as err:
            raise LoginStrategyUnavailable(f"No .netrc found at {netrc_loc}") from err
        except NetrcParseError as err:
            raise LoginStrategyUnavailable(
                f"Unable to parse .netrc file {netrc_loc}"
            ) from err

        creds = my_netrc[self.system.edl_hostname]
        if creds is None:
            raise LoginStrategyUnavailable(
                f"Earthdata Login hostname {self.system.edl_hostname} not found in .netrc file {netrc_loc}"
            )

        username = creds["login"]
        password = creds["password"]

        if username is None:
            raise LoginStrategyUnavailable(
                f"Username not found in .netrc file {netrc_loc}"
            )
        if password is None:
            raise LoginStrategyUnavailable(
                f"Password not found in .netrc file {netrc_loc}"
            )

        authenticated = self._get_credentials(username, password, None)

        if authenticated:
            logger.debug("Using .netrc file for EDL")

        return authenticated

    def _environment(self) -> bool:
        username = os.getenv("EARTHDATA_USERNAME")
        password = os.getenv("EARTHDATA_PASSWORD")
        token = os.getenv("EARTHDATA_TOKEN")

        if (not username or not password) and not token:
            raise LoginStrategyUnavailable(
                "Either the environment variables EARTHDATA_USERNAME and "
                "EARTHDATA_PASSWORD must both be set, or EARTHDATA_TOKEN must be set for "
                "the 'environment' login strategy."
            )

        logger.debug("Using environment variables for EDL")
        return self._get_credentials(username, password, token)

    def _get_credentials(
        self,
        username: Optional[str],
        password: Optional[str],
        user_token: Optional[str],
    ) -> bool:
        if user_token is not None:
            self.token = {"access_token": user_token}
            self.authenticated = True
        elif username is not None and password is not None:
            token_resp = self._find_or_create_token(username, password)

            if not (token_resp.ok):  # type: ignore
                msg = f"Authentication with Earthdata Login failed with:\n{token_resp.text}"
                logger.error(msg)
                raise LoginAttemptFailure(msg)

            logger.info("You're now authenticated with NASA Earthdata Login")
            self.username = username
            self.password = password

            token = token_resp.json()
            logger.debug(
                f"Using token with expiration date: {token['expiration_date']}"
            )
            self.token = token
            self.authenticated = True

        return self.authenticated

    def _find_or_create_token(self, username: str, password: str) -> requests.Response:
        with _create_earthdata_session(
            self.system.edl_hostname, (username, password)
        ) as session:
            return session.post(
                self.EDL_FIND_OR_CREATE_TOKEN_URL,
                headers={"Accept": "application/json"},
                timeout=10,
            )

    def _persist_user_credentials(self, username: str, password: str) -> bool:
        # See: https://github.com/sloria/tinynetrc/issues/34

        netrc_loc = netrc_path()
        logger.info(f"Persisting credentials to {netrc_loc}")

        try:
            netrc_loc.touch(exist_ok=True)
            netrc_loc.chmod(0o600)
        except Exception as e:
            logger.error(e)
            return False

        my_netrc = Netrc(str(netrc_loc))
        my_netrc[self.system.edl_hostname] = {
            "login": username,
            "password": password,
        }
        my_netrc.save()

        urs_cookies_path = Path.home() / ".urs_cookies"

        if not urs_cookies_path.exists():
            urs_cookies_path.write_text("")

        # Create and write to .dodsrc file
        dodsrc_path = Path.home() / ".dodsrc"

        if not dodsrc_path.exists():
            dodsrc_contents = (
                f"HTTP.COOKIEJAR={urs_cookies_path}\nHTTP.NETRC={netrc_loc}"
            )
            dodsrc_path.write_text(dodsrc_contents)

        if platform.system() == "Windows":
            local_dodsrc_path = Path.cwd() / dodsrc_path.name

            if not local_dodsrc_path.exists():
                shutil.copy2(dodsrc_path, local_dodsrc_path)

        return True

    def _get_cloud_auth_url(
        self, daac_shortname: Optional[str] = "", provider: Optional[str] = ""
    ) -> str:
        for daac in DAACS:
            if (
                daac_shortname == daac["short-name"]
                or provider in daac["cloud-providers"]
                and len(daac["s3-credentials"]) > 0
            ):
                return str(daac["s3-credentials"])
        return ""

    def __repr__(self) -> str:
        """Return string representation of Auth status."""
        if not self.authenticated:
            return "Auth(authenticated=False)"

        username = getattr(self, "username", None)
        user_str = f"user='{username}'" if username else "user=<token>"
        strategy_str = (
            f"strategy='{self._login_strategy}'" if self._login_strategy else ""
        )
        system_str = f"system={self.system.edl_hostname}"

        parts = ["Auth(authenticated=True", user_str]
        if strategy_str:
            parts.append(strategy_str)
        parts.append(system_str + ")")

        return ", ".join(parts)

    def _repr_html_(self) -> str:
        """Return HTML representation for Jupyter notebook display."""
        from earthaccess.formatting.html import _repr_auth_html

        return _repr_auth_html(self)
