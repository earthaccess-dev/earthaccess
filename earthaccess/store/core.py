"""Internal store module for NASA Earthdata access.

This module provides the Store class, an internal implementation for downloading
and streaming granules from NASA's Earthdata cloud (S3) or on-premises (HTTPS)
archives.

Users should NOT import from this module directly. Use the top-level API:

    >>> import earthaccess
    >>> earthaccess.login()
    >>> granules = earthaccess.search_data(short_name="ATL06", count=5)
    >>> files = earthaccess.open(granules)  # Stream granules
    >>> paths = earthaccess.download(granules, "./data")  # Download granules

Note:
    The Store class is instantiated automatically by earthaccess when needed.
"""

import datetime
import logging
import threading
from functools import lru_cache
from itertools import chain
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union
from uuid import uuid4

import fsspec
import requests
import s3fs
from multimethod import multimethod as singledispatchmethod
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from typing_extensions import deprecated

from .._access import AccessType, CanonicalAccessType, normalize_access
from ..auth.system import route
from ..search import DataCollections, DataGranule, GranuleResults
from .daac import DAAC_TEST_URLS, find_provider

# Import from new modular components
from .file_wrapper import (
    get_url_granule_mapping as _get_url_granule_mapping,
    is_interactive as _is_interactive,
    open_files as _open_files,
)
from .filesystems import DefaultFileSystemFactory, FileSystemFactory
from .parallel import get_executor
from .target import TargetLocation
from .validation import (
    _S3_ACCESS_HINT,
    validate_granule_access,
    validate_url_protocols,
)

logger = logging.getLogger(__name__)


class Store:
    """Internal data access class for NASA Earthdata granules.

    This class is an internal implementation detail of earthaccess. Users should
    NOT instantiate this class directly. Instead, use the top-level API functions:

        >>> import earthaccess
        >>> earthaccess.login()
        >>> granules = earthaccess.search_data(short_name="ATL06", count=5)
        >>> files = earthaccess.open(granules)  # Stream granules
        >>> paths = earthaccess.download(granules, "./data")  # Download granules

    The Store class handles:
        - Authentication and session management
        - Explicit HTTPS (default) or direct S3 access
        - S3 credential caching with automatic refresh
        - Parallel download/open operations

    Note:
        This class is instantiated automatically by the earthaccess module.
        Direct instantiation is not supported as part of the public API.
    """

    __module__ = "earthaccess.store"

    def __init__(
        self,
        auth: Any,
        pre_authorize: bool = False,
        fs_factory: Optional[FileSystemFactory] = None,
    ) -> None:
        """Store is the class to access data.

        Parameters:
            auth: Auth instance to download and access data.
            pre_authorize: Whether to pre-authorize with all DAAC endpoints (default: False)
            fs_factory: Optional FileSystemFactory for dependency injection (default: DefaultFileSystemFactory)
        """
        self.thread_locals = threading.local()
        # Initialize filesystem factory (default to DefaultFileSystemFactory)
        self._fs_factory = fs_factory or DefaultFileSystemFactory()

        if auth.authenticated is True:
            self.auth = auth
            self._s3_credentials: Dict[
                Tuple, Tuple[datetime.datetime, Dict[str, str]]
            ] = {}
            oauth_profile = route(f"https://{auth.system.edl_hostname}/profile")
            # sets the initial URS cookie
            self._requests_cookies: Dict[str, Any] = {}
            self.set_requests_session(oauth_profile, bearer_token=True)
            if pre_authorize:
                # collect cookies from other DAACs
                for url in DAAC_TEST_URLS:
                    self.set_requests_session(url)

        else:
            logger.warning("The current session is not authenticated with NASA")
            self.auth = None
        self._current_executor_type: Optional[str] = None  # Track current executor type

    def _derive_concept_provider(self, concept_id: Optional[str] = None) -> str:
        if concept_id is not None:
            provider = concept_id.split("-")[1]
            return provider
        return ""

    def _set_executor_type(self, parallel: Union[str, bool, None]) -> None:
        """Set the current executor type for session strategy selection."""
        if parallel is None or parallel is True:
            self._current_executor_type = "threads"
        elif parallel is False:
            self._current_executor_type = "serial"
        elif isinstance(parallel, str):
            parallel_lower = parallel.lower()
            if parallel_lower in ("threads", "thread", "threadpool"):
                self._current_executor_type = "threads"
            elif parallel_lower in ("serial", "none", "disabled"):
                self._current_executor_type = "serial"
            elif parallel_lower == "dask":
                self._current_executor_type = "dask"
            elif parallel_lower == "lithops":
                self._current_executor_type = "lithops"
            else:
                self._current_executor_type = "threads"  # Default fallback
        else:
            # Custom executor - assume distributed for safety
            self._current_executor_type = "distributed"

    def _use_session_cloning(self) -> bool:
        """Determine if session cloning is appropriate for current executor."""
        if self._current_executor_type is None:
            # Default to True for backward compatibility
            return True

        return self._current_executor_type in ["threads", "threadpool", "serial"]

    def _derive_daac_provider(self, daac: str) -> Union[str, None]:
        provider = find_provider(daac, True)
        return provider

    def _is_cloud_collection(self, concept_id: List[str]) -> bool:
        collection = DataCollections(self.auth).concept_id(concept_id).get()
        if len(collection) > 0 and "s3-links" in collection[0]["meta"]:
            return True
        return False

    def _own_s3_credentials(self, links: List[Dict[str, Any]]) -> Union[str, None]:
        for link in links:
            if "/s3credentials" in link["URL"]:
                return link["URL"]
        return None

    def set_requests_session(
        self, url: str, method: str = "get", bearer_token: bool = True
    ) -> None:
        """Sets up a `requests` session with bearer tokens that are used by CMR.

        Mainly used to get the authentication cookies from different DAACs and URS.
        This HTTPS session can be used to download granules if we want to use a direct,
        lower level API.

        Parameters:
            url: used to test the credentials and populate the class auth cookies
            method: HTTP method to test, default: "GET"
            bearer_token: if true, will be used for authenticated queries on CMR

        Returns:
            fsspec HTTPFileSystem (aiohttp client session)
        """
        if not hasattr(self, "_http_session"):
            self._http_session = self.auth.get_session(bearer_token)

        resp = self._http_session.request(method, url, allow_redirects=True)

        if resp.status_code in [400, 401, 403]:
            new_session = requests.Session()
            resp_req = new_session.request(
                method, url, allow_redirects=True, cookies=self._requests_cookies
            )
            if resp_req.status_code in [400, 401, 403]:
                resp.raise_for_status()
            else:
                self._requests_cookies.update(new_session.cookies.get_dict())
        elif 200 <= resp.status_code < 300:
            self._requests_cookies = self._http_session.cookies.get_dict()
        else:
            resp.raise_for_status()

    @deprecated("Use get_s3_filesystem instead")
    def get_s3fs_session(
        self,
        daac: Optional[str] = None,
        concept_id: Optional[str] = None,
        provider: Optional[str] = None,
        endpoint: Optional[str] = None,
    ) -> s3fs.S3FileSystem:
        """Returns a s3fs instance for a given cloud provider / DAAC.

        Parameters:
           daac: any of the DAACs, e.g. NSIDC, PODAAC
           provider: a data provider if we know them, e.g. PODAAC -> POCLOUD
           endpoint: pass the URL for the credentials directly

        Returns:
           An `s3fs.S3FileSystem` authenticated for reading in-region in us-west-2 for 1 hour.
        """
        return self.get_s3_filesystem(daac, concept_id, provider, endpoint)

    def get_s3_filesystem(
        self,
        daac: Optional[str] = None,
        concept_id: Optional[str] = None,
        provider: Optional[str] = None,
        endpoint: Optional[str] = None,
    ) -> s3fs.S3FileSystem:
        """Return an `s3fs.S3FileSystem` instance for a given cloud provider / DAAC.

        Parameters:
            daac: any of the DAACs, e.g. NSIDC, PODAAC
            provider: a data provider if we know them, e.g. PODAAC -> POCLOUD
            endpoint: pass the URL for the credentials directly

        Returns:
            a s3fs file instance
        """
        if self.auth is None:
            raise ValueError(
                "A valid Earthdata login instance is required to retrieve S3 credentials"
            )
        if not any([concept_id, daac, provider, endpoint]):
            raise ValueError(
                "At least one of the concept_id, daac, provider or endpoint"
                "parameters must be specified. "
            )

        if concept_id is not None:
            provider = self._derive_concept_provider(concept_id)

        # Get existing S3 credentials if we already have them
        location = (
            daac,
            provider,
            endpoint,
        )  # Identifier for where to get S3 credentials from
        need_new_creds = False
        try:
            dt_init, creds = self._s3_credentials[location]
        except KeyError:
            need_new_creds = True
        else:
            # If cached credentials are expired, invalidate the cache
            delta = datetime.datetime.now() - dt_init
            if round(delta.seconds / 60, 2) > 55:
                need_new_creds = True
                self._s3_credentials.pop(location)

        if need_new_creds:
            # Don't have existing valid S3 credentials, so get new ones
            now = datetime.datetime.now()
            if endpoint is not None:
                creds = self.auth.get_s3_credentials(endpoint=endpoint)
            elif daac is not None:
                creds = self.auth.get_s3_credentials(daac=daac)
            elif provider is not None:
                creds = self.auth.get_s3_credentials(provider=provider)
            # Include new credentials in the cache
            self._s3_credentials[location] = now, creds

        return s3fs.S3FileSystem(
            key=creds["accessKeyId"],
            secret=creds["secretAccessKey"],
            token=creds["sessionToken"],
        )

    @lru_cache
    def get_fsspec_session(self) -> fsspec.AbstractFileSystem:
        """Returns a fsspec HTTPS session with bearer tokens that are used by CMR.

        This HTTPS session can be used to download granules if we want to use a direct,
        lower level API.

        Returns:
            fsspec HTTPFileSystem (aiohttp client session)
        """
        token = self.auth.token["access_token"]
        client_kwargs = {
            "headers": {"Authorization": f"Bearer {token}"},
            # This is important! If we trust the env and send a bearer token,
            # auth will fail!
            "trust_env": False,
        }
        session = fsspec.filesystem("https", client_kwargs=client_kwargs)
        return session

    def get_requests_session(self) -> requests.Session:
        """Returns a requests HTTPS session with bearer tokens that are used by CMR.

        This HTTPS session can be used to download granules if we want to use a direct,
        lower level API.

        Returns:
            requests Session
        """
        if hasattr(self, "_http_session"):
            return self._http_session
        else:
            raise AttributeError("The requests session hasn't been set up yet.")

    def open(
        self,
        granules: Union[List[str], List[DataGranule]],
        provider: Optional[str] = None,
        *,
        access: AccessType | None = "https",
        show_progress: Optional[bool] = None,
        credentials_endpoint: Optional[str] = None,
        max_workers: Optional[int] = None,
        open_kwargs: Optional[Dict[str, Any]] = None,
        parallel: Union[str, bool, None] = None,
    ) -> List[fsspec.spec.AbstractBufferedFile]:
        """Returns a list of file-like objects that can be used to access files
        hosted on S3 or HTTPS by third party libraries like xarray.

        By default files are opened over HTTPS. Set ``access="s3"`` to open
        files directly from S3.

        Parameters:
            granules: a list of granule instances **or** list of URLs, e.g. `s3://some-granule`.
                If a list of URLs is passed, we need to specify the data provider.
            provider: e.g. POCLOUD, NSIDC_CPRD, etc.
            access: access method, either ``"https"`` (default) or ``"s3"``. Legacy
                values are accepted. When URLs are provided, their scheme takes
                precedence.
            show_progress: whether or not to display a progress bar. If not specified, defaults to `True` for interactive sessions
                (i.e., in a notebook or a python REPL session), otherwise `False`.
            credentials_endpoint: S3 credentials endpoint
            max_workers: Maximum number of worker threads for parallel processing. Default varies by executor.
            open_kwargs: Additional keyword arguments to pass to `fsspec.open`, such as `cache_type` and `block_size`.
                Defaults to using `blockcache` with a block size determined by the file size (4 to 16MB).

        Returns:
            A list of "file pointers" to remote (i.e. `s3://` or `https://`) files.
        """
        if show_progress is None:
            show_progress = _is_interactive()

        if len(granules):
            return self._open(
                granules,
                provider,
                access=normalize_access(access),
                credentials_endpoint=credentials_endpoint,
                max_workers=max_workers,
                show_progress=show_progress,
                open_kwargs=open_kwargs,
                parallel=parallel,
            )
        return []

    @singledispatchmethod
    def _open(
        self,
        granules: Union[List[str], List[DataGranule]],
        provider: Optional[str] = None,
        *,
        access: CanonicalAccessType = "https",
        credentials_endpoint: Optional[str] = None,
        max_workers: Optional[int] = None,
        show_progress: bool = True,
        open_kwargs: Optional[Dict[str, Any]] = None,
        parallel: Union[str, bool, None] = None,
    ) -> List[Any]:
        raise NotImplementedError("granules should be a list of DataGranule or URLs")

    def _get_credentials_endpoint_from_collection(
        self, concept_id: str
    ) -> Optional[str]:
        """Fetches the S3 credentials endpoint from the collection metadata."""
        try:
            # We can use the internal search module or just a direct request
            # Using DataCollections might be cleaner if available, but a direct request is also fine.
            # Let's use the auth session to query CMR.
            base_url = route(self.auth.system.cmr_base_url)
            url = f"{base_url}/search/collections.umm_json?concept_id={concept_id}"

            response = self.auth.get_session().get(url)
            if response.ok:
                data = response.json()
                if "items" in data and len(data["items"]) > 0:
                    umm = data["items"][0]["umm"]
                    return umm.get("DirectDistributionInformation", {}).get(
                        "S3CredentialsAPIEndpoint"
                    )
        except Exception as e:
            logger.debug(f"Failed to fetch collection metadata for {concept_id}: {e}")
        return None

    def _s3_location_for_granules(
        self,
        granules: List[DataGranule],
        credentials_endpoint: Optional[str],
    ) -> Tuple[str, Optional[str]]:
        """Resolve the S3 provider and credentials endpoint for a granule batch."""
        provider = granules[0]["meta"]["provider-id"]
        endpoint = credentials_endpoint or self._own_s3_credentials(
            granules[0]["umm"]["RelatedUrls"]
        )

        if endpoint is None:
            collection_concept_id = granules[0]["meta"].get("collection-concept-id")
            if collection_concept_id:
                endpoint = self._get_credentials_endpoint_from_collection(
                    collection_concept_id
                )

        return provider, endpoint

    def _s3_filesystem_for_granules(
        self,
        granules: List[DataGranule],
        credentials_endpoint: Optional[str],
    ) -> s3fs.S3FileSystem:
        """Return an authenticated S3 filesystem for a validated granule batch."""
        provider, endpoint = self._s3_location_for_granules(
            granules,
            credentials_endpoint,
        )
        if endpoint is not None:
            logger.info("using endpoint: %s", endpoint)
            return self.get_s3_filesystem(endpoint=endpoint)
        logger.info("using provider: %s", provider)
        return self.get_s3_filesystem(provider=provider)

    @_open.register
    def _open_granules(
        self,
        granules: List[DataGranule],
        provider: Optional[str] = None,
        *,
        access: CanonicalAccessType = "https",
        credentials_endpoint: Optional[str] = None,
        max_workers: Optional[int] = None,
        show_progress: bool = True,
        open_kwargs: Optional[Dict[str, Any]] = None,
        parallel: Union[str, bool, None] = None,
    ) -> List[Any]:
        total_size = round(sum([granule.size for granule in granules]) / 1024, 2)
        logger.info(f"Opening {len(granules)} granules, approx size: {total_size} GB")

        if self.auth is None:
            raise ValueError(
                "A valid Earthdata login instance is required to retrieve credentials"
            )

        validate_granule_access(granules, access)

        if access == "s3":
            s3_fs = self._s3_filesystem_for_granules(
                granules,
                credentials_endpoint,
            )
            url_mapping = _get_url_granule_mapping(granules, access="s3")
            try:
                return _open_files(
                    url_mapping,
                    fs=s3_fs,
                    max_workers=max_workers,
                    show_progress=show_progress,
                    open_kwargs=open_kwargs,
                    parallel=parallel,
                )
            except Exception as e:
                msg = (
                    "An exception occurred while trying to access remote files on S3. "
                    "Check that S3 access is available for the requested granules. "
                    f"{_S3_ACCESS_HINT} Exception: {e}"
                )
                raise RuntimeError(msg) from e

        url_mapping = _get_url_granule_mapping(granules, access="https")
        return self._open_urls_https(
            url_mapping,
            max_workers=max_workers,
            show_progress=show_progress,
            open_kwargs=open_kwargs,
            parallel=parallel,
        )

    @_open.register
    def _open_granule_results(
        self,
        granules: GranuleResults,
        provider: Optional[str] = None,
        *,
        access: CanonicalAccessType = "https",
        credentials_endpoint: Optional[str] = None,
        max_workers: Optional[int] = None,
        show_progress: bool = True,
        open_kwargs: Optional[Dict[str, Any]] = None,
        parallel: Union[str, bool, None] = None,
    ) -> List[Any]:
        """Open GranuleResults (from search_data) by converting to list."""
        return self._open_granules(
            granules.all(),  # type: ignore[arg-type]
            provider,
            access=access,
            credentials_endpoint=credentials_endpoint,
            max_workers=max_workers,
            show_progress=show_progress,
            open_kwargs=open_kwargs,
            parallel=parallel,
        )

    @_open.register
    def _open_urls(
        self,
        granules: List[str],
        provider: Optional[str] = None,
        *,
        access: CanonicalAccessType = "https",
        credentials_endpoint: Optional[str] = None,
        max_workers: Optional[int] = None,
        show_progress: bool = True,
        open_kwargs: Optional[Dict[str, Any]] = None,
        parallel: Union[str, bool, None] = None,
    ) -> List[Any]:
        if self.auth is None:
            raise ValueError(
                "A valid Earthdata login instance is required to retrieve S3 credentials"
            )

        protocol = validate_url_protocols(granules)
        url_mapping: Mapping[str, None] = {url: None for url in granules}

        if protocol == "s3":
            if provider is None and credentials_endpoint is None:
                msg = "Opening s3:// URLs requires provider= or credentials_endpoint=."
                raise ValueError(msg)

            if provider is not None:
                s3_fs = self.get_s3_filesystem(provider=provider)
            else:
                s3_fs = self.get_s3_filesystem(endpoint=credentials_endpoint)

            try:
                return _open_files(
                    url_mapping,
                    fs=s3_fs,
                    max_workers=max_workers,
                    show_progress=show_progress,
                    open_kwargs=open_kwargs,
                    parallel=parallel,
                )
            except Exception as e:
                msg = (
                    "An exception occurred while trying to access remote files on S3. "
                    "Check that S3 access is available for the requested granules. "
                    f"{_S3_ACCESS_HINT} Exception: {e}"
                )
                raise RuntimeError(msg) from e

        return self._open_urls_https(
            url_mapping,
            max_workers=max_workers,
            show_progress=show_progress,
            open_kwargs=open_kwargs,
            parallel=parallel,
        )

    def get(
        self,
        granules: Union[List[DataGranule], List[str]],
        path: Optional[Union[Path, str, TargetLocation]] = None,
        provider: Optional[str] = None,
        threads: int = 8,
        *,
        access: AccessType | None = "https",
        credentials_endpoint: Optional[str] = None,
        show_progress: Optional[bool] = None,
        max_workers: Optional[int] = None,
        parallel: Union[str, bool, None] = None,
    ) -> List[Path]:
        """Retrieves data granules from a remote storage system.

        By default, data are downloaded over HTTPS. Set ``access="s3"`` to use
        direct S3 access.

        Parameters:
            granules: List of DataGranule instances or URLs
            path: Target directory to store the remote data granules. Can be a local path,
                cloud storage URI (s3://, gs://, az://), or TargetLocation object. If not
                supplied, defaults to a subdirectory of the current working directory
                of the form `data/YYYY-MM-DD-UUID`, where `YYYY-MM-DD` is the year,
                month, and day of the current date, and `UUID` is the last 6 digits
                of a UUID4 value.
            provider: a valid cloud provider, each DAAC has a provider code for their cloud distributions
            access: access method, either ``"https"`` (default) or ``"s3"``. Legacy
                values are accepted. When URLs are provided, their scheme takes
                precedence.
            credentials_endpoint: If provided, this will be used to get S3 credentials
            threads: Parallel number of threads to use to download the files;
                adjust as necessary, default = 8.
            show_progress: whether or not to display a progress bar. If not specified, defaults to `True` for interactive sessions
                (i.e., in a notebook or a python REPL session), otherwise `False`.
            max_workers: Maximum number of worker threads for parallel processing. If not specified, defaults to the value of `threads`.

        Returns:
            List of downloaded files
        """
        if not granules:
            raise ValueError("List of URLs or DataGranule instances expected")

        if path is None:
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            uuid = uuid4().hex[:6]
            path = Path.cwd() / "data" / f"{today}-{uuid}"

        # Convert Path or string to TargetLocation if needed
        if not isinstance(path, TargetLocation):
            path = TargetLocation(path)

        if show_progress is None:
            show_progress = _is_interactive()

        # Use threads as default max_workers if not specified
        if max_workers is None:
            max_workers = threads

        return self._get(
            granules,
            path,
            provider,
            access=normalize_access(access),
            credentials_endpoint=credentials_endpoint,
            max_workers=max_workers,
            show_progress=show_progress,
            parallel=parallel,
        )

    @singledispatchmethod
    def _get(
        self,
        granules: Union[List[DataGranule], List[str]],
        path: Union[Path, TargetLocation],
        provider: Optional[str] = None,
        *,
        access: CanonicalAccessType = "https",
        credentials_endpoint: Optional[str] = None,
        max_workers: Optional[int] = None,
        show_progress: bool = True,
        parallel: Union[str, bool, None] = None,
    ) -> List[Path]:
        """Retrieves data granules from a remote storage system.

        Parameters:
            granules: A list of granules (DataGranule) instances or a list of granule links (HTTP).
            path: Target directory to store the remote data granules. Can be a local path,
                cloud storage URI (s3://, gs://, az://), or TargetLocation object.
            provider: a valid cloud provider, each DAAC has a provider code for their cloud distributions
            access: access method, either ``"https"`` (default) or ``"s3"``.
            max_workers: Maximum number of worker threads for parallel processing.
            show_progress: Whether or not to display a progress bar.

        Returns:
            None
        """
        raise NotImplementedError(f"Cannot _get {granules}")

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(Exception),
    )
    def download_cloud_file(
        self,
        s3_fs: fsspec.AbstractFileSystem,
        file: str,
        path: Union[Path, TargetLocation],
    ) -> Path:
        """Download a single file from S3 to local path or cloud storage.

        Parameters:
            s3_fs: Authenticated S3 filesystem instance.
            file: S3 URI of the file to download (e.g., s3://bucket/key).
            path: Target directory as Path or TargetLocation.

        Returns:
            Path to the downloaded file.

        Note:
            Skips download if file already exists at target location.
            Retries up to 3 times with exponential backoff on failure.
        """
        # Handle TargetLocation
        if isinstance(path, TargetLocation):
            filesystem = path.get_filesystem()
            file_name = filesystem.basename(file)
            target_path = filesystem.join(file_name)

            if filesystem.exists(target_path):
                return Path(target_path)  # Skip if already exists

            # Download to temporary location, then copy to target
            import tempfile

            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir) / Path(file).name
                s3_fs.get([file], str(temp_path), recursive=False)
                logger.info(f"Downloading: {file_name}")

                # Copy to target filesystem
                with open(temp_path, "rb") as src_file:
                    with filesystem.open(target_path, "wb") as dst_file:
                        dst_file.write(src_file.read())

                return Path(target_path)
        else:
            # Original Path-based logic
            file_name = path / Path(file).name
            if file_name.exists():
                return file_name  # Skip if already exists

            s3_fs.get([file], str(path), recursive=False)
            logger.info(f"Downloading: {file_name}")
            return file_name

    @_get.register
    def _get_urls(
        self,
        granules: List[str],
        path: Union[Path, TargetLocation],
        provider: Optional[str] = None,
        *,
        access: CanonicalAccessType = "https",
        credentials_endpoint: Optional[str] = None,
        max_workers: Optional[int] = None,
        show_progress: bool = True,
        parallel: Union[str, bool, None] = None,
    ) -> List[Path]:
        data_links = granules
        protocol = validate_url_protocols(data_links)

        if protocol == "s3":
            if (
                provider is None
                and credentials_endpoint is None
                and "cumulus" in data_links[0]
            ):
                msg = (
                    "earthaccess can't yet guess the provider for cloud collections, "
                    "use one from `earthaccess.list_cloud_providers()` or pass the "
                    "S3 credential endpoint."
                )
                raise ValueError(msg)

            if credentials_endpoint is not None:
                logger.info(
                    f"Accessing cloud dataset using credentials_endpoint: {credentials_endpoint}"
                )
                s3_fs = self.get_s3_filesystem(endpoint=credentials_endpoint)
            elif provider is not None:
                logger.info(f"Accessing cloud dataset using provider: {provider}")
                s3_fs = self.get_s3_filesystem(provider=provider)
            else:
                s3_fs = s3fs.S3FileSystem()

            def _download(file: str) -> Union[Path, None]:
                return self.download_cloud_file(s3_fs, file, path)

            executor = get_executor(
                parallel, max_workers=max_workers, show_progress=show_progress
            )
            try:
                results = list(executor.map(_download, data_links))
                return [r for r in results if r is not None]
            except Exception as e:
                msg = (
                    "An exception occurred while trying to access remote files on S3. "
                    "Check that S3 access is available for the requested granules. "
                    f"{_S3_ACCESS_HINT} Exception: {e}"
                )
                raise RuntimeError(msg) from e
            finally:
                executor.shutdown(wait=True)

        return self._download_onprem_granules(
            data_links,
            path,
            max_workers=max_workers,
            show_progress=show_progress,
            parallel=parallel,
        )

    @_get.register
    def _get_granules(
        self,
        granules: List[DataGranule],
        path: Union[Path, TargetLocation],
        provider: Optional[str] = None,
        *,
        access: CanonicalAccessType = "https",
        credentials_endpoint: Optional[str] = None,
        max_workers: Optional[int] = None,
        show_progress: bool = True,
        parallel: Union[str, bool, None] = None,
    ) -> List[Path]:
        if self.auth is None:
            raise ValueError(
                "We need to be logged into NASA EDL in order to download data granules"
            )

        validate_granule_access(granules, access)

        data_links = list(
            chain.from_iterable(
                granule.data_links(access=access) for granule in granules
            )
        )

        total_size = round(sum(granule.size for granule in granules) / 1024, 2)
        logger.info(
            f" Getting {len(granules)} granules, approx download size: {total_size} GB"
        )

        if access == "s3":
            s3_fs = self._s3_filesystem_for_granules(
                granules,
                credentials_endpoint,
            )

            # Handle TargetLocation directory creation
            if isinstance(path, TargetLocation):
                filesystem = path.get_filesystem()
                filesystem.mkdir("", exist_ok=True)  # Create base directory
            else:
                path.mkdir(parents=True, exist_ok=True)

            # Set executor type for session strategy selection
            self._set_executor_type(parallel)

            def _download(file: str) -> Union[Path, None]:
                return self.download_cloud_file(s3_fs, file, path)

            executor = get_executor(
                parallel, max_workers=max_workers, show_progress=show_progress
            )
            try:
                results = list(executor.map(_download, data_links))
                return [r for r in results if r is not None]
            except Exception as e:
                msg = (
                    "An exception occurred while trying to access remote files on S3. "
                    "Check that S3 access is available for the requested granules. "
                    f"{_S3_ACCESS_HINT} Exception: {e}"
                )
                raise RuntimeError(msg) from e
            finally:
                executor.shutdown(wait=True)

        return self._download_onprem_granules(
            data_links,
            path,
            max_workers=max_workers,
            show_progress=show_progress,
            parallel=parallel,
        )

    @_get.register
    def _get_granule_results(
        self,
        granules: GranuleResults,
        path: Union[Path, TargetLocation],
        provider: Optional[str] = None,
        *,
        access: CanonicalAccessType = "https",
        credentials_endpoint: Optional[str] = None,
        max_workers: Optional[int] = None,
        show_progress: bool = True,
        parallel: Union[str, bool, None] = None,
    ) -> List[Path]:
        """Download GranuleResults (from search_data) by converting to list."""
        return self._get_granules(
            granules.all(),  # type: ignore[arg-type]
            path,
            provider,
            access=access,
            credentials_endpoint=credentials_endpoint,
            max_workers=max_workers,
            show_progress=show_progress,
            parallel=parallel,
        )

    def _clone_session_in_local_thread(
        self, original_session: requests.Session
    ) -> None:
        """Clone the original session and store it in the local thread context.

        This method creates a new session that replicates the headers, cookies, and authentication settings
        from the provided original session. The new session is stored in a thread-local storage.

        Parameters:
            original_session (requests.Session): The session to be cloned.

        Returns:
            None
        """
        if not hasattr(self.thread_locals, "local_thread_session"):
            local_thread_session = self.auth.get_session()  # type: ignore
            local_thread_session.headers.update(original_session.headers)
            local_thread_session.cookies.update(original_session.cookies)
            local_thread_session.auth = original_session.auth
            self.thread_locals.local_thread_session = local_thread_session

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(Exception),
    )
    def _download_file(self, url: str, directory: Union[Path, TargetLocation]) -> Path:
        """Download a single file using a bearer token.

        Parameters:
            url: the granule url
            directory: local directory or target location

        Returns:
            A local filepath or an exception.
        """
        # If the get data link is an Opendap location
        if "opendap" in url and url.endswith(".html"):
            url = url.replace(".html", "")
        local_filename = url.split("/")[-1]

        # Handle TargetLocation
        if isinstance(directory, TargetLocation):
            filesystem = directory.get_filesystem()
            target_path = filesystem.join(local_filename)

            if filesystem.exists(target_path):
                logger.info(f"File {local_filename} already downloaded")
                return Path(target_path)

            # Use executor-aware session strategy
            if self._use_session_cloning():
                # Efficient session cloning for ThreadPoolExecutor
                original_session = self.get_requests_session()
                # This reuses the auth cookie, we make sure we only authenticate N threads instead
                # of one per file, see #913
                self._clone_session_in_local_thread(original_session)
                session = self.thread_locals.local_thread_session
            else:
                # Per-worker authentication for distributed executors
                if not hasattr(self, "worker_session"):
                    self.worker_session = self.get_requests_session()
                session = self.worker_session

            with session.get(url, stream=True, allow_redirects=True) as r:
                r.raise_for_status()
                with filesystem.open(target_path, "wb") as f:
                    # Cap memory usage for large files at 1MB per write to disk per thread
                    # https://docs.python-requests.org/en/latest/user/quickstart/#raw-response-content
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        f.write(chunk)
            return Path(target_path)
        else:
            # Original Path-based logic
            path = directory / Path(local_filename)
            if not path.exists():
                # Use executor-aware session strategy
                if self._use_session_cloning():
                    # Efficient session cloning for ThreadPoolExecutor
                    original_session = self.get_requests_session()
                    # This reuses the auth cookie, we make sure we only authenticate N threads instead
                    # of one per file, see #913
                    self._clone_session_in_local_thread(original_session)
                    session = self.thread_locals.local_thread_session
                else:
                    # Per-worker authentication for distributed executors
                    if not hasattr(self, "worker_session"):
                        self.worker_session = self.get_requests_session()
                    session = self.worker_session
                with session.get(url, stream=True, allow_redirects=True) as r:
                    r.raise_for_status()
                    with open(path, "wb") as f:
                        # Cap memory usage for large files at 1MB per write to disk per thread
                        # https://docs.python-requests.org/en/latest/user/quickstart/#raw-response-content
                        for chunk in r.iter_content(chunk_size=1024 * 1024):
                            f.write(chunk)
            else:
                logger.info(f"File {local_filename} already downloaded")
            return path

    def _download_onprem_granules(
        self,
        urls: List[str],
        directory: Union[Path, TargetLocation],
        *,
        max_workers: Optional[int] = None,
        show_progress: bool = True,
        parallel: Union[str, bool, None] = None,
    ) -> List[Any]:
        """Downloads a list of URLS into the data directory.

        Parameters:
            urls: list of granule URLs from an on-prem collection
            directory: local directory to store the downloaded files
            max_workers: Maximum number of worker threads for parallel download
            show_progress: Whether to show progress bars during download

        Returns:
            A list of local filepaths to which the files were downloaded.
        """
        if urls is None:
            raise ValueError("The granules didn't provide a valid GET DATA link")
        if self.auth is None:
            raise ValueError(
                "We need to be logged into NASA EDL in order to download data granules"
            )
        # Handle TargetLocation directory creation
        if isinstance(directory, TargetLocation):
            filesystem = directory.get_filesystem()
            filesystem.mkdir("", exist_ok=True)  # Create base directory
        else:
            directory.mkdir(parents=True, exist_ok=True)

        # Set executor type for session strategy selection
        self._set_executor_type(parallel)

        arguments = [(url, directory) for url in urls]

        # Get executor and execute
        executor = get_executor(
            parallel, max_workers=max_workers, show_progress=show_progress
        )
        try:
            # Map the download function to arguments
            results = list(
                executor.map(lambda args: self._download_file(*args), arguments)
            )
            return results
        finally:
            executor.shutdown(wait=True)

    def _open_urls_https(
        self,
        url_mapping: Mapping[str, Union[DataGranule, None]],
        *,
        max_workers: Optional[int] = None,
        show_progress: bool = True,
        open_kwargs: Optional[Dict[str, Any]] = None,
        parallel: Union[str, bool, None] = None,
    ) -> List[fsspec.AbstractFileSystem]:
        https_fs = self.get_fsspec_session()

        try:
            return _open_files(
                url_mapping,
                https_fs,
                max_workers=max_workers,
                show_progress=show_progress,
                open_kwargs=open_kwargs,
                parallel=parallel,
            )
        except Exception:
            logger.exception(
                "An exception occurred while trying to access remote files via HTTPS"
            )
            raise
