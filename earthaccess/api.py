import datetime
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import requests
import s3fs
from fsspec import AbstractFileSystem
from typing_extensions import (
    Any,
    Dict,
    List,
    Optional,
    Union,
    deprecated,
)

import earthaccess
from earthaccess._access import AccessType, normalize_access
from earthaccess.exceptions import LoginStrategyUnavailable, ServiceOutage
from earthaccess.search import (
    CollectionQuery,
    CollectionResults,
    DataCollection,
    DataCollections,
    DataGranule,
    DataGranules,
    DataServices,
    GranuleQuery,
    GranuleResults,
)

from .auth import PROD, Auth, System
from .auth.system import route
from .plugins import Asset, Job, UnsupportedOutputError
from .search.query import (
    CollectionQuery as NewCollectionQuery,
    GranuleQuery as NewGranuleQuery,
)
from .store import Store
from .store.target import TargetLocation

if TYPE_CHECKING:
    # Type checking stubs to help the type checker understand __getattr__ behavior

    _auth: Auth
    _store: Optional[Store]

logger = logging.getLogger(__name__)


def status(system: System = PROD, raise_on_outage: bool = False) -> dict[str, str]:
    """Get the statuses of NASA's Earthdata services.

    Parameters:
        system: The Earthdata system to access, defaults to PROD.
        raise_on_outage: If True, raises exception on errors or outages.

    Returns:
        A dictionary containing the statuses of Earthdata services.

    Examples:
        >>> earthaccess.status()  # doctest: +SKIP
        {'Earthdata Login': 'OK', 'Common Metadata Repository': 'OK'}
        >>> earthaccess.status(earthaccess.UAT)  # doctest: +SKIP
        {'Earthdata Login': 'OK', 'Common Metadata Repository': 'OK'}

    Raises:
        ServiceOutage: if at least one service status is not `"OK"`
    """
    services = ("Earthdata Login", "Common Metadata Repository")
    statuses = {service: "Unknown" for service in services}
    msg = (
        f"Unable to retrieve Earthdata service statuses for {system}."
        f"  Try again later, or visit {system.status_url} to check service statuses."
    )

    try:
        with requests.get(route(system.status_api_url)) as r:
            r.raise_for_status()
            statuses_json = r.json()

        for entry in statuses_json.get("statuses", []):
            name = entry.get("name", "")

            if service := next(filter(name.startswith, services), None):
                statuses[service] = entry.get("status", "Unknown")
    except (json.JSONDecodeError, requests.exceptions.RequestException):
        logger.error(msg)

    if raise_on_outage and any(
        status not in {"OK", "Unknown"} for status in statuses.values()
    ):
        msg = f"At least 1 service is in an unhealthy/unknown state: {services}"
        raise ServiceOutage(msg)

    return statuses


def _normalize_location(location: Optional[str]) -> Optional[str]:
    """Handle user-provided `daac` and `provider` values.

    These values must have a capital letter as the first character
    followed by capital letters, numbers, or an underscore. Here we
    uppercase all strings to handle the case when users provide
    lowercase values (e.g. "pocloud" instead of "POCLOUD").

    https://wiki.earthdata.nasa.gov/display/ED/CMR+Data+Partner+User+Guide?src=contextnavpagetreemode
    """
    if location is not None:
        location = location.upper()
    return location


def search_datasets(
    query: Optional[NewCollectionQuery] = None,
    count: int = -1,
    prefetch: int = 20,
    page_size: int = 2000,
    **kwargs: Any,
) -> CollectionResults:
    """Search datasets (collections) using NASA's CMR.

    [https://cmr.earthdata.nasa.gov/search/site/docs/search/api.html](https://cmr.earthdata.nasa.gov/search/site/docs/search/api.html)

    Parameters:
        query: A CollectionQuery object (e.g. ``earthaccess.CollectionQuery``).
            When provided, the query parameters are extracted from this object.
            Cannot be used together with keyword arguments.
        count: Number of records to get, -1 = all
        prefetch: How many results to fetch immediately when the search object
            is created (default: 20). Set to 0 to defer all fetching until you
            iterate/index.
        page_size: Results per CMR page when streaming/iterating (default:
            2000, CMR's maximum; must be between 1 and 2000).
        kwargs (Dict):
            arguments to CMR:

            * **keyword**: (str) Filter collections by keywords.  Case-insensitive and
              supports wildcards ? and *
            * **short_name**: (str) Filter collections by product short name; e.g. ATL08
            * **doi**: (str) Filter by DOI
            * **daac**: (str) Filter by DAAC; e.g. NSIDC or PODAAC
            * **data_center**: (str) An alias for `daac`
            * **provider**: (str) Filter by data provider; each DAAC can have more than
               one provider, e.g. POCLOUD, PODAAC, etc.
            * **has_granules**: (bool) If true, only return collections with granules.  Default: True
            * **temporal**: (tuple) A tuple representing temporal bounds in the form
              `(date_from, date_to)`.  Dates can be `datetime` objects or ISO 8601
              formatted strings.  Date strings can be full timestamps; e.g. YYYY-MM-DD HH:mm:ss
              or truncated YYYY-MM-DD
            * **bounding_box**: (tuple) Filter collection by those that intersect bounding box.
              A tuple representing spatial bounds in the form
              `(lower_left_lon, lower_left_lat, upper_right_lon, upper_right_lat)`
            * **polygon**: (List[tuples]) Filter by polygon.  Polygon must be a list of
              tuples containing longitude-latitude pairs representing polygon vertices.
              Vertices must be in counter-clockwise order and the final vertex must be the
              same as the first vertex; e.g. [(lon1,lat1),(lon2,lat2),(lon3,lat3),(lon4,lat4),(lon1,lat1)]
            * **point**: (Tuple[float,float])  Filter by collections intersecting a point,
              where the point is a longitude-latitude pair; e.g. (lon,lat)
            * **line**: (List[tuples]) Filter collections that overlap a series of connected
              points.  Points are represented as tuples containing longitude-latitude pairs;
              e.g. [(lon1,lat1),(lon2,lat2),(lon3,lat3)]
            * **circle**: (List[float, float, float]) Filter collections that intersect a
              circle defined as a point with a radius.  Circle parameters are a list
              containing latitude, longitude and radius in meters; e.g. [lon, lat, radius_m].
              The circle center cannot be the north or south poles.  The radius mst be
              between 10 and 6,000,000 m
            * **cloud_hosted**: (bool) Return only collected hosted on Earthdata Cloud.  Default: True
            * **downloadable**: (bool) If True, only return collections that can be downloaded
              from an online archive
            * **concept_id**: (str) Filter by Concept ID; e.g. C3151645377-NSIDC_CPRD
            * **instrument**: (str) Filter by Instrument name; e.g. ATLAS
            * **project**: (str) Filter by project or campaign name; e.g. ABOVE
            * **fields**: (List[str]) Return only the UMM fields listed in this parameter
            * **revision_date**: tuple(str,str) Filter by collections that have revision date
              within the range
            * **debug**: (bool) If True prints CMR request.  Default: True

    Returns:
        A CollectionResults object containing DataCollection results. Supports iteration,
            indexing (results[0], results[-1], results[0:10]), and conversion to list.
            Use len(results) for loaded count, results.total() for total CMR matches.
            Each DataCollection provides dataset information like concept_id, doi, etc.

    Raises:
        RuntimeError: The CMR query failed.
        ValueError: If both query and kwargs are provided.

    Examples:
        Using keyword arguments (backward compatible):
        ```python
        datasets = earthaccess.search_datasets(
            keyword="sea surface anomaly",
            cloud_hosted=True
        )
        ```

        Using a query object:
        ```python
        from earthaccess import CollectionQuery

        query = CollectionQuery().keyword("sea surface anomaly").cloud_hosted(True)
        datasets = earthaccess.search_datasets(query=query)
        ```

    """
    # Handle query object vs kwargs
    replay_kwargs: Optional[Dict[str, Any]] = None
    if query is not None:
        if kwargs:
            raise ValueError(
                "Cannot use both 'query' parameter and keyword arguments. "
                "Use either a query object or keyword arguments, not both."
            )
        # Validate the query
        validation = query.validate()
        if not validation.is_valid:
            errors = "; ".join(f"{e.field}: {e.message}" for e in validation.errors)
            raise ValueError(f"Invalid query: {errors}")
        # Use clean, replayable kwargs from the query builder; the legacy
        # query layer (DataCollections.parameters) accepts these directly.
        kwargs = query.to_kwargs()
        replay_kwargs = kwargs

    if not kwargs:
        logger.warning(
            "A valid set of parameters is needed to search for datasets on CMR"
        )
        return CollectionResults(DataCollections(), limit=0)
    auth = earthaccess.__auth__
    if auth and isinstance(auth, Auth) and auth.authenticated:
        cmr_query = DataCollections(auth).parameters(**kwargs)
    else:
        cmr_query = DataCollections().parameters(**kwargs)
    datasets_found = cmr_query.hits()
    logger.info(f"Datasets found: {datasets_found}")
    return CollectionResults(
        cmr_query,
        limit=count if count > 0 else None,
        prefetch=prefetch,
        page_size=page_size,
        query_kwargs=replay_kwargs,
    )


def search_data(
    query: Optional[NewGranuleQuery] = None,
    count: int = -1,
    max_items: Optional[int] = None,
    prefetch: int = 20,
    page_size: int = 2000,
    **kwargs: Any,
) -> GranuleResults:
    """Search for dataset files (granules) using NASA's CMR.

    [https://cmr.earthdata.nasa.gov/search/site/docs/search/api.html](https://cmr.earthdata.nasa.gov/search/site/docs/search/api.html)

    The CMR does not permit queries across all granules in all collections in order to provide fast search responses. Granule queries must target a subset of the collections in the CMR using a condition like provider, provider_id, concept_id, collection_concept_id, short_name, version or entry_title.

    Parameters:
        query: A GranuleQuery object (e.g. ``earthaccess.GranuleQuery``).
            When provided, the query parameters are extracted from this object.
            Cannot be used together with keyword arguments.
        count: Number of records to get, -1 = all
        max_items: Alias for count (pystac-client compatible). If both are provided,
            max_items takes precedence.
        prefetch: How many results to fetch immediately when the search object
            is created (default: 20). Set to 0 to defer all fetching until you
            iterate/index.
        page_size: Results per CMR page when streaming/iterating (default:
            2000, CMR's maximum; must be between 1 and 2000).
        kwargs (Dict):
            arguments to CMR:

            * **short_name**: (str) Filter granules by product short name; e.g. ATL08
            * **version**: (str) Filter by dataset version
            * **daac**: (str) a provider code for any DAAC, e.g. NSIDC or PODAAC
            * **data_center**; (str) An alias for daac
            * **provider**: (str) Only match granules from a given provider.  A DAAC can
              have more than one provider, e.g PODAAC and POCLOUD, NSIDC_ECS and NSIDC_CPRD.
            * **cloud_hosted**: (bool) If True, only match granules hosted in Earthdata Cloud
            * **downloadable**: (bool) If True, only match granules that are downloadable.
              A granule is downloadable when it contains at least one RelatedURL of type
              GETDATA.
            * **online_only**: (bool) Alias of downloadable
            * **orbit_number**; (float) Filter granule by the orbit number in which a
              granule was acquired
            * **granule_name**; (str) Filter by granule name.  Granule name can contain
              wild cards, e.g `MODGRNLD.*.daily.*`.
            * **instrument**; (str) Filter by instrument name, e.g. "ATLAS"
            * **platform**; (str) Filter by platform, e.g. satellite or plane
            * **cloud_cover**: (tuple) Filter by cloud cover.  Tuple is a range of
              cloud covers, e.g. (0, 20).  Cloud cover values in metadata may be fractions
              (i.e. (0.,0.2)) or percentages.  CMRS searches for cloud cover range based on
              values in metadata. Note collections without cloud_cover in metadata will return
              zero granules.
            * **day_night_flag**: (str) Filter for day- and night-time images, accepts
              'day', 'night', 'unspecified'.
            * **temporal**: (tuple) A tuple representing temporal bounds in the form
              `(date_from, date_to)`.  Dates can be `datetime` objects or ISO 8601
              formatted strings.  Date strings can be full timestamps; e.g. YYYY-MM-DD HH:mm:ss
              or truncated YYYY-MM-DD
            * **bounding_box**: (tuple) Filter collection by those that intersect bounding box.
              A tuple representing spatial bounds in the form
              `(lower_left_lon, lower_left_lat, upper_right_lon, upper_right_lat)`
            * **polygon**: (list[tuples]) Filter by polygon.  Polygon must be a list of
              tuples containing longitude-latitude pairs representing polygon vertices.
              Vertices must be in counter-clockwise order and the final vertex must be the
              same as the first vertex; e.g. [(lon1,lat1),(lon2,lat2),(lon3,lat3),
              (lon4,lat4),(lon1,lat1)]
            * **point**: (tuple(float,float))  Filter by collections intersecting a point,
              where the point is a longitude-latitude pair; e.g. (lon,lat)
            * **line**: (list[tuples]) Filter collections that overlap a series of connected
              points.  Points are represented as tuples containing longitude-latitude pairs;
              e.g. [(lon1,lat1),(lon2,lat2),(lon3,lat3)]
            * **circle**: (tuple(float, float, float)) Filter collections that intersect a
              circle defined as a point with a radius.  Circle parameters are a tuple
              containing latitude, longitude and radius in meters; e.g. (lon, lat, radius_m).
              The circle center cannot be the north or south poles.  The radius mst be
              between 10 and 6,000,000 m


    Returns:
        A GranuleResults object containing DataGranule results. Supports iteration,
            indexing (results[0], results[-1], results[0:10]), and conversion to list.
            Use len(results) for loaded count, results.total() for total CMR matches.
            Each DataGranule can be used to access files via `download()` or `open()`.

    Raises:
        RuntimeError: The CMR query failed.
        ValueError: If both query and kwargs are provided, or if query is invalid.

    Examples:
        Using keyword arguments (backward compatible):
        ```python
        granules = earthaccess.search_data(
            short_name="ATL06",
            bounding_box=(-46.5, 61.0, -42.5, 63.0),
            )
        ```

        Using a query object:
        ```python
        from earthaccess import GranuleQuery

        query = GranuleQuery().short_name("ATL06").bounding_box(-46.5, 61.0, -42.5, 63.0)
        granules = earthaccess.search_data(query=query)
        ```
    """
    # Handle query object vs kwargs
    replay_kwargs: Optional[Dict[str, Any]] = None
    if query is not None:
        if kwargs:
            raise ValueError(
                "Cannot use both 'query' parameter and keyword arguments. "
                "Use either a query object or keyword arguments, not both."
            )
        # Validate the query
        validation = query.validate()
        if not validation.is_valid:
            errors = "; ".join(f"{e.field}: {e.message}" for e in validation.errors)
            raise ValueError(f"Invalid query: {errors}")
        # Use clean, replayable kwargs from the query builder; the legacy
        # query layer (DataGranules.parameters) accepts these directly.
        kwargs = query.to_kwargs()
        replay_kwargs = kwargs

    # max_items takes precedence over count for pystac-client compatibility
    if max_items is not None:
        count = max_items

    auth = earthaccess.__auth__
    if auth and auth.authenticated:
        cmr_query = DataGranules(auth).parameters(**kwargs)
    else:
        cmr_query = DataGranules().parameters(**kwargs)
    granules_found = cmr_query.hits()
    logger.info(f"Granules found: {granules_found}")
    return GranuleResults(
        cmr_query,
        limit=count if count > 0 else None,
        prefetch=prefetch,
        page_size=page_size,
        query_kwargs=replay_kwargs,
    )


def search_services(count: int = -1, **kwargs: Any) -> List[Any]:
    """Search the NASA CMR for Services matching criteria.

    See <https://cmr.earthdata.nasa.gov/search/site/docs/search/api.html#service>.

    Parameters:
        count:
            maximum number of services to fetch (if less than 1, all services
            matching specified criteria are fetched [default])
        kwargs:
            keyword arguments accepted by the CMR for searching services

    Returns:
        list of services (possibly empty) matching specified criteria, in UMM
        JSON format

    Examples:
        ```python
        services = search_services(provider="POCLOUD", keyword="COG")
        ```
    """
    auth = earthaccess.__auth__
    if auth and isinstance(auth, Auth):
        query = DataServices(auth=auth).parameters(**kwargs)
    else:
        query = DataServices(auth=None).parameters(**kwargs)
    hits = query.hits()
    logger.info(f"Services found: {hits}")

    return query.get(hits if count < 1 else min(count, hits))


def login(
    strategy: str = "all",
    persist: bool = False,
    system: System = PROD,
) -> Auth:
    """Authenticate with Earthdata login (https://urs.earthdata.nasa.gov/).

    Attempt to login via _only_ the specified strategy, unless the `"all"`
    strategy is used, in which case each of the individual strategies is
    attempted in the following order, until one succeeds: `"environment"`,
    `"netrc"`, `"interactive"`.  In this case, only when all strategies fail
    does login fail.

    Parameters:
        strategy:
            An authentication method.

            * `"all"`: try each of the following methods, in order, until one
              succeeds.
            * `"environment"`: retrieve either an Earthdata login token from the
              `EARTHDATA_TOKEN` environment variable, or a username and password
              pair from the `EARTHDATA_USERNAME` and `EARTHDATA_PASSWORD`
              environment variables (specifying a token takes precedence).
            * `"netrc"`: retrieve username and password from `~/.netrc` (or
              `~/_netrc` on Windows), or from the file specified by the `NETRC`
              environment variable.
            * `"interactive"`: enter username and password via interactive
              prompts.
        persist: if `True`, persist credentials to a `.netrc` file
        system: the Earthdata system to access

    Returns:
        An instance of Auth.

    Raises:
        LoginAttemptFailure: If the NASA Earthdata Login service rejects
            credentials.

    Examples:
        Login using the default strategy (tries environment, netrc, then interactive):
        ```python
        import earthaccess

        auth = earthaccess.login()
        print(auth.authenticated)  # True if login succeeded
        ```

        Login using environment variables:
        ```python
        # Set EARTHDATA_USERNAME and EARTHDATA_PASSWORD, or EARTHDATA_TOKEN
        auth = earthaccess.login(strategy="environment")
        ```

        Login interactively and save credentials to .netrc:
        ```python
        auth = earthaccess.login(strategy="interactive", persist=True)
        ```

        Login to the UAT (User Acceptance Testing) environment:
        ```python
        auth = earthaccess.login(system=earthaccess.UAT)
        ```
    """
    # Set the underlying Auth object's earthdata system,
    # before triggering the getattr function for `__auth__`.
    earthaccess._auth._set_earthdata_system(system)

    auth = earthaccess.__auth__
    if not isinstance(auth, Auth):
        raise RuntimeError("Authentication object is not properly initialized")

    if strategy == "all":
        for strategy in ["environment", "netrc", "interactive"]:
            try:
                auth.login(
                    strategy=strategy,
                    persist=persist,
                    system=system,
                )
            except LoginStrategyUnavailable as err:
                logger.debug(err)
                continue

            if auth.authenticated:
                earthaccess._store = Store(auth)
                break
    else:
        auth.login(
            strategy=strategy,
            persist=persist,
            system=system,
        )
        if auth.authenticated:
            earthaccess._store = Store(auth)

    return auth


def _split_assets(assets: Any) -> tuple[List[Any], List[Any]]:
    """Partition assets into file assets and OPeNDAP assets."""
    files: List[Any] = []
    opendap: List[Any] = []
    for asset in assets:
        if getattr(asset, "access", None) == "opendap":
            opendap.append(asset)
        else:
            files.append(asset)
    return files, opendap


_VIRTUAL_FORMATS = frozenset({"icechunk", "kerchunk"})


def _is_virtual_asset(value: Any) -> bool:
    """Whether *value* is an Asset naming a virtual store (not a file)."""
    if isinstance(value, (str, Path, bytes, list, tuple, dict)):
        return False
    return getattr(value, "format", None) in _VIRTUAL_FORMATS


def _looks_like_icechunk_store(value: Union[str, Path]) -> bool:
    """Cheap icechunk-URI sniff: scheme, suffix, or the first-snapshot marker."""
    text = str(value)
    if text.startswith("icechunk://") or text.rstrip("/").endswith(".icechunk"):
        return True
    path = Path(text)
    return (path / "snapshots" / "1CECHNKREP0F1RSTCMT0").is_file()


def _open_virtual_asset(
    asset: Any, *, load: bool, access: AccessType | None = "https"
) -> Any:
    return _open_virtual(asset.href, load=load, access=access)


def _open_virtual(uri: Any, *, load: bool, access: AccessType | None = "https") -> Any:
    from .virtual import open_virtual

    return open_virtual(uri, load=load, access=access)


def _download_opendap(
    assets: Any, path: Optional[Union[Path, str, TargetLocation]]
) -> List[Path]:
    """Serialize OPeNDAP assets to local NetCDF files with pydap (one per URL)."""
    if path is None:
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        destination = Path.cwd() / "data" / f"{today}-{uuid4().hex[:6]}"
    elif isinstance(path, TargetLocation):
        if not path.is_local():
            raise UnsupportedOutputError(
                "OPeNDAP results can only be downloaded to a local path "
                f"(got {path.path!r}); pydap serializes files locally"
            )
        destination = Path(path.path)
    else:
        destination = Path(path)
    destination.mkdir(parents=True, exist_ok=True)

    import pydap.client

    before = set(destination.iterdir())
    pydap.client.to_netcdf(
        [_opendap_https_url(asset.href) for asset in assets],
        session=get_requests_https_session(),
        output_path=str(destination),
    )
    return sorted(set(destination.iterdir()) - before)


def _opendap_https_url(href: str) -> str:
    """``to_netcdf`` talks HTTP, so map the DAP scheme back to https (keep the query)."""
    for scheme in ("dap4://", "dap2://"):
        if href.startswith(scheme):
            return "https://" + href[len(scheme) :]
    return href


def download(
    granules: Union[DataGranule, List[DataGranule], str, List[str], Job],
    path: Optional[Union[Path, str, TargetLocation]] = None,
    provider: Optional[str] = None,
    threads: int = 8,
    *,
    access: AccessType | None = "https",
    show_progress: Optional[bool] = None,
    credentials_endpoint: Optional[str] = None,
    max_workers: Optional[int] = None,
    parallel: Union[str, bool, None] = None,
) -> List[Path]:
    """Retrieves data granules from a remote storage system.

       By default, data are downloaded over HTTPS. Set ``access="s3"`` to use
       direct S3 access (requires the appropriate credentials and network).

    Parameters:
        granules: a granule, list of granules, a granule link (HTTP), or a list of granule links (HTTP)
        path: Target directory to store the remote data granules. Can be a local path,
            cloud storage URI (s3://, gs://, az://), or TargetLocation object. If not
            supplied, defaults to a subdirectory of the current working directory
            of the form `data/YYYY-MM-DD-UUID`, where `YYYY-MM-DD` is the year,
            month, and day of the current date, and `UUID` is the last 6 digits
            of a UUID4 value.
        provider: if we download a list of URLs, we need to specify the provider.
        access: access method, either ``"https"`` (default) or ``"s3"``. Legacy
            values ``"direct"``, ``"indirect"``, and ``"external"`` are accepted.
            When URLs are provided, their scheme takes precedence.
        credentials_endpoint: S3 credentials endpoint to be used for obtaining temporary S3 credentials. This is only required if
            the metadata doesn't include it, or we pass urls to the method instead of `DataGranule` instances.
        threads: parallel number of threads to use to download the files, adjust as necessary, default = 8
        show_progress: whether or not to display a progress bar. If not specified, defaults to `True` for interactive sessions
            (i.e., in a notebook or a python REPL session), otherwise `False`.
        max_workers: Maximum number of worker threads for parallel processing. If not specified, defaults to the value of `threads`.
        parallel: Executor type for parallel downloads. Options: "threads" (default), "serial", "dask", "lithops".

    Returns:
        List of downloaded files. For a plugin job whose assets are OPeNDAP URLs,
        the constrained data is serialized to local NetCDF files (needs netCDF4).

    Raises:
        Exception: A file download failed.

    Examples:
        Download granules to a local directory:
        ```python
        import earthaccess

        earthaccess.login()
        granules = earthaccess.search_data(short_name="ATL06", count=3)
        files = earthaccess.download(granules, "./data")
        print(files)  # [PosixPath('data/ATL06_file1.h5'), ...]
        ```

        Download to a cloud storage location:
        ```python
        from earthaccess import TargetLocation

        files = earthaccess.download(
            granules,
            TargetLocation("s3://my-bucket/data", storage_options={"profile": "my-aws-profile"})
        )
        ```

        Download a single granule:
        ```python
        files = earthaccess.download(granules[0], "./data")
        ```

        Download with custom parallelization:
        ```python
        files = earthaccess.download(granules, "./data", threads=16)
        ```
    """
    provider = _normalize_location(str(provider))

    if isinstance(granules, Job):
        # A plugin job: wait for it, then download the assets it produced.
        if granules.output != "assets":
            raise UnsupportedOutputError(
                f"cannot download output={granules.output!r}; "
                "only 'assets' jobs can be downloaded"
            )
        files, opendap = _split_assets(granules.results())
        if opendap:
            return _download_opendap(opendap, path)
        granules = [asset.href for asset in files]

    if isinstance(granules, DataGranule):
        granules = [granules]
    elif isinstance(granules, str):
        granules = [granules]

    # Convert Path or string to TargetLocation if needed
    if path is not None and not isinstance(path, TargetLocation):
        path = TargetLocation(path)

    try:
        store = earthaccess.__store__
        if store is None:
            raise RuntimeError(
                "Store is not initialized. Please call earthaccess.login() first."
            )
        return store.get(
            granules,
            path,
            provider,
            threads,
            access=normalize_access(access),
            credentials_endpoint=credentials_endpoint,
            show_progress=show_progress,
            max_workers=max_workers,
            parallel=parallel,
        )
    except AttributeError as err:
        logger.error(
            f"{err}: You must call earthaccess.login() before you can download data"
        )

    return []


def open(
    granules: Union[
        str,
        Path,
        List[str],
        List[DataGranule],
        "Asset",
        List["Asset"],
        "DataCollection",
        Job,
    ],
    provider: Optional[str] = None,
    *,
    access: AccessType | None = "https",
    credentials_endpoint: Optional[str] = None,
    show_progress: Optional[bool] = None,
    max_workers: Optional[int] = None,
    open_kwargs: Optional[Dict[str, Any]] = None,
    parallel: Union[str, bool, None] = None,
    load: bool = True,
) -> Union[List[AbstractFileSystem], List[str], "Any"]:
    """Returns a list of file-like objects that can be used to access files
    hosted on S3 or HTTPS by third party libraries like xarray.

    By default, files are opened over HTTPS. Set ``access="s3"`` to open files
    directly from S3.

    Parameters:
        granules: a list of granule instances **or** list of URLs, e.g. `s3://some-granule`.
            If a list of URLs is passed, we need to specify the data provider.
        provider: e.g. POCLOUD, NSIDC_CPRD, etc.
        access: access method, either ``"https"`` (default) or ``"s3"``. Legacy
            values ``"direct"``, ``"indirect"``, and ``"external"`` are accepted.
            When URLs are provided, their scheme takes precedence.
        credentials_endpoint: S3 credentials endpoint for obtaining temporary AWS credentials.
        show_progress: whether or not to display a progress bar. If not specified, defaults to `True` for interactive sessions
            (i.e., in a notebook or a python REPL session), otherwise `False`.
        max_workers: Maximum number of worker threads for parallel processing. If not specified, defaults to 8.
        open_kwargs: Additional keyword arguments to pass to `fsspec.open`, such as `cache_type` and `block_size`.
            Defaults to using `blockcache` with a block size determined by the file size (4 to 16MB).
        parallel: Executor type for parallel file opening. Options: "threads" (default), "serial", "dask", "lithops".
        load: When the input is a virtual store (an ``Asset`` with format
            ``icechunk``/``kerchunk``, a local icechunk store path, or a
            ``DataCollection`` advertising one), ``True`` (default) returns a
            concrete lazily-loaded dataset; ``False`` returns the manifest-form
            virtual dataset. Ignored for regular file/granule inputs.

    Returns:
        A list of "file pointers" to remote (i.e. s3 or https) files. For a
        plugin job whose assets are OPeNDAP URLs, a list of those URLs to open
        with xarray (``engine="pydap"``). For a virtual store, the opened
        xarray dataset (or a list of them for a list / plugin job of stores).

    Examples:
        Open granules and load with xarray:
        ```python
        import earthaccess
        import xarray as xr

        earthaccess.login()
        granules = earthaccess.search_data(short_name="MUR-JPL-L4-GLOB-v4.1", count=3)
        files = earthaccess.open(granules)

        # Open with xarray
        ds = xr.open_mfdataset(files, engine="h5netcdf")
        ```

        Open granules for streaming access:
        ```python
        files = earthaccess.open(granules)
        for f in files:
            data = f.read(1024)  # Read first 1KB
            print(f"File size: {f.size} bytes")
        ```

        Open with custom fsspec options:
        ```python
        files = earthaccess.open(
            granules,
            open_kwargs={"cache_type": "all", "block_size": 8 * 1024 * 1024}
        )
        ```
    """
    if isinstance(granules, Job):
        # A plugin job: wait for it. Non-asset outputs (xarray / dataframe) are
        # already usable objects; asset outputs become hrefs. OPeNDAP assets are
        # returned as URLs to open with xarray (engine="pydap"), and virtual
        # stores (vds) are opened as datasets.
        if granules.output != "assets":
            return granules.results()
        files, opendap = _split_assets(granules.results())
        virtual = [asset for asset in files if _is_virtual_asset(asset)]
        if virtual:
            if opendap or len(virtual) != len(files):
                raise UnsupportedOutputError(
                    "cannot mix virtual stores with file assets in one job"
                )
            return [
                _open_virtual_asset(asset, load=load, access=normalize_access(access))
                for asset in virtual
            ]
        if opendap:
            return [asset.href for asset in opendap]
        granules = [asset.href for asset in files]

    # A virtual store is opened as a dataset, not as a file pointer.
    if _is_virtual_asset(granules):
        return _open_virtual_asset(granules, load=load, access=normalize_access(access))
    if (
        isinstance(granules, list)
        and granules
        and all(_is_virtual_asset(asset) for asset in granules)
    ):
        return [
            _open_virtual_asset(asset, load=load, access=normalize_access(access))
            for asset in granules
        ]
    if isinstance(granules, (str, Path)) and _looks_like_icechunk_store(granules):
        return _open_virtual(granules, load=load, access=normalize_access(access))
    if isinstance(granules, DataCollection):
        return _open_virtual(granules, load=load, access=normalize_access(access))

    store = earthaccess.__store__
    if store is None:
        raise RuntimeError(
            "Store is not initialized. Please call earthaccess.login() first."
        )

    return store.open(
        granules=granules,
        provider=_normalize_location(provider),
        access=normalize_access(access),
        credentials_endpoint=credentials_endpoint,
        show_progress=show_progress,
        max_workers=max_workers,
        open_kwargs=open_kwargs,
        parallel=parallel,
    )


def get_s3_credentials(
    daac: Optional[str] = None,
    provider: Optional[str] = None,
    results: Optional[List[DataGranule]] = None,
) -> Dict[str, Any]:
    """Returns temporary (1 hour) credentials for direct access to NASA S3 buckets.

    We can use the daac name, the provider, or a list of results from earthaccess.search_data().
    If we use results, earthaccess will use the metadata on the response to get the credentials,
    which is useful for missions that do not use the same endpoint as their DAACs, e.g. SWOT.

    Parameters:
        daac: a DAAC short_name like NSIDC or PODAAC, etc.
        provider: if we know the provider for the DAAC, e.g. POCLOUD, LPCLOUD etc.
        results: List of results from search_data()

    Returns:
        A dictionary with S3 credentials for the DAAC or provider containing:
        - accessKeyId: AWS access key
        - secretAccessKey: AWS secret key
        - sessionToken: AWS session token
        - expiration: Credential expiration time

    Examples:
        Get credentials by DAAC:
        ```python
        import earthaccess

        earthaccess.login()
        creds = earthaccess.get_s3_credentials(daac="PODAAC")
        print(creds["accessKeyId"])
        ```

        Get credentials from search results (recommended for dataset-specific endpoints):
        ```python
        granules = earthaccess.search_data(short_name="SWOT_L2_LR_SSH_Expert_2.0", count=1)
        creds = earthaccess.get_s3_credentials(results=granules)
        ```

        Use credentials with boto3:
        ```python
        import boto3

        creds = earthaccess.get_s3_credentials(daac="NSIDC")
        s3 = boto3.client(
            "s3",
            aws_access_key_id=creds["accessKeyId"],
            aws_secret_access_key=creds["secretAccessKey"],
            aws_session_token=creds["sessionToken"],
        )
        ```
    """
    daac = _normalize_location(daac)
    provider = _normalize_location(provider)
    auth = earthaccess.__auth__
    if auth is None:
        raise RuntimeError(
            "Authentication is not initialized. Please call earthaccess.login() first."
        )
    if not isinstance(auth, Auth):
        raise RuntimeError("Authentication object is not properly initialized")

    if results is not None:
        endpoint = results[0].get_s3_credentials_endpoint()
        return auth.get_s3_credentials(endpoint=endpoint)
    return auth.get_s3_credentials(daac=daac, provider=provider)


def collection_query() -> CollectionQuery:
    """Returns a query builder instance for NASA collections (datasets).

    This function returns a CMR-based query builder for searching NASA
    data collections. For the new earthaccess-native query builders with
    additional features like validation and STAC conversion, use
    `earthaccess.CollectionQuery` directly.

    Returns:
        A query builder instance for data collections.

    Examples:
        Search for cloud-hosted collections:
        ```python
        import earthaccess

        earthaccess.login()
        query = earthaccess.collection_query()
        collections = query.keyword("temperature").cloud_hosted(True).get(10)
        for c in collections:
            print(c["umm"]["ShortName"])
        ```

        Search by DAAC:
        ```python
        query = earthaccess.collection_query()
        collections = query.daac("PODAAC").get(5)
        ```

        Using the new query builders (recommended):
        ```python
        from earthaccess import CollectionQuery

        query = CollectionQuery().keyword("temperature").cloud_hosted(True)
        results = earthaccess.search_datasets(query=query)
        ```
    """
    auth = earthaccess.__auth__
    if auth and isinstance(auth, Auth) and auth.authenticated:
        query_builder = DataCollections(auth)
    else:
        query_builder = DataCollections()
    return query_builder


def granule_query() -> GranuleQuery:
    """Returns a query builder instance for data granules.

    This function returns a CMR-based query builder for searching NASA
    data granules. For the new earthaccess-native query builders with
    additional features like validation and STAC conversion, use
    `earthaccess.GranuleQuery` directly.

    Returns:
        A query builder instance for data granules.

    Examples:
        Search for granules by short name:
        ```python
        import earthaccess

        earthaccess.login()
        query = earthaccess.granule_query()
        granules = query.short_name("ATL06").temporal("2023-01", "2023-02").get(10)
        for g in granules:
            print(g["umm"]["GranuleUR"])
        ```

        Search with spatial constraints:
        ```python
        query = earthaccess.granule_query()
        granules = query.short_name("MUR-JPL-L4-GLOB-v4.1").bounding_box(-180, -90, 180, 90).get(5)
        ```

        Using the new query builders (recommended):
        ```python
        from earthaccess import GranuleQuery

        query = GranuleQuery().short_name("ATL06").temporal("2023-01", "2023-02")
        results = earthaccess.search_data(query=query)
        ```
    """
    auth = earthaccess.__auth__
    if auth and isinstance(auth, Auth) and auth.authenticated:
        query_builder = DataGranules(auth)
    else:
        query_builder = DataGranules()
    return query_builder


def get_fsspec_https_session() -> AbstractFileSystem:
    """Returns a fsspec session that can be used to access datafiles across many different DAACs.

    Returns:
        An fsspec instance able to access data across DAACs.

    Examples:
        ```python
        import earthaccess

        earthaccess.login()
        fs = earthaccess.get_fsspec_https_session()
        with fs.open(DAAC_GRANULE) as f:
            f.read(10)
        ```
    """
    store = earthaccess.__store__
    if store is None:
        raise RuntimeError(
            "Store is not initialized. Please call earthaccess.login() first."
        )
    session = store.get_fsspec_session()
    return session


def get_requests_https_session() -> requests.Session:
    """Returns a requests Session instance with an authorized bearer token.
    This is useful for making requests to restricted URLs, such as data granules or services that
    require authentication with NASA EDL.

    Returns:
        An authenticated requests Session instance.

    Examples:
        ```python
        import earthaccess

        earthaccess.login()

        req_session = earthaccess.get_requests_https_session()
        data = req_session.get(granule_url, headers = {"Range": "bytes=0-100"})

        ```
    """
    store = earthaccess.__store__
    if store is None:
        raise RuntimeError(
            "Store is not initialized. Please call earthaccess.login() first."
        )
    session = store.get_requests_session()
    return session


@deprecated("Use get_s3_filesystem instead")
def get_s3fs_session(
    daac: Optional[str] = None,
    provider: Optional[str] = None,
    results: Optional[DataGranule] = None,
) -> s3fs.S3FileSystem:
    """Returns a fsspec s3fs file session for direct access when we are in us-west-2.

    Parameters:
        daac: Any DAAC short name e.g. NSIDC, GES_DISC
        provider: Each DAAC can have a cloud provider.
            If the DAAC is specified, there is no need to use provider.
        results: A list of results from search_data().
            `earthaccess` will use the metadata from CMR to obtain the S3 Endpoint.

    Returns:
        An `s3fs.S3FileSystem` authenticated for reading in-region in us-west-2 for 1 hour.
    """
    return get_s3_filesystem(daac, provider, results)


def get_s3_filesystem(
    daac: Optional[str] = None,
    provider: Optional[str] = None,
    results: Optional[DataGranule] = None,
    endpoint: Optional[str] = None,
) -> s3fs.S3FileSystem:
    """Return an `s3fs.S3FileSystem` for direct access when running within the AWS us-west-2 region.

    This function returns an authenticated S3 filesystem that can be used to read
    NASA Earthdata files directly from S3 when running in AWS us-west-2 region.

    Parameters:
        daac: Any DAAC short name e.g. NSIDC, GES_DISC
        provider: Each DAAC can have a cloud provider.
            If the DAAC is specified, there is no need to use provider.
        results: A list of results from search_data().
            `earthaccess` will use the metadata from CMR to obtain the S3 Endpoint.
        endpoint: URL of a cloud provider credentials endpoint to be used for obtaining
            AWS S3 access credentials.

    Returns:
        An authenticated s3fs session valid for 1 hour.

    Examples:
        Get filesystem by DAAC:
        ```python
        import earthaccess

        earthaccess.login()
        fs = earthaccess.get_s3_filesystem(daac="PODAAC")
        files = fs.ls("podaac-ops-cumulus-protected/")
        ```

        Get filesystem from search results (recommended):
        ```python
        granules = earthaccess.search_data(short_name="MUR-JPL-L4-GLOB-v4.1", count=1)
        fs = earthaccess.get_s3_filesystem(results=granules)

        # Read data directly
        with fs.open(granules[0].data_links(access="s3")[0]) as f:
            data = f.read(1024)
        ```

        Use with xarray for direct S3 access:
        ```python
        import xarray as xr

        fs = earthaccess.get_s3_filesystem(results=granules)
        s3_urls = [g.data_links(access="s3")[0] for g in granules]
        files = [fs.open(url) for url in s3_urls]
        ds = xr.open_mfdataset(files, engine="h5netcdf")
        ```
    """
    daac = _normalize_location(daac)
    provider = _normalize_location(provider)
    store = earthaccess.__store__
    if store is None:
        raise RuntimeError(
            "Store is not initialized. Please call earthaccess.login() first."
        )

    if results:
        endpoint = results[0].get_s3_credentials_endpoint()
        if endpoint:
            session = store.get_s3_filesystem(endpoint=endpoint)
        else:
            raise ValueError("No s3 credentials specified in the given DataGranule")
    elif endpoint:
        session = store.get_s3_filesystem(endpoint=endpoint)
    elif daac or provider:
        session = store.get_s3_filesystem(daac=daac, provider=provider)
    else:
        raise ValueError(
            "Invalid set of input arguments given. Please provide either "
            "a valid result, an endpoint, a daac, or a provider."
        )
    return session


def get_edl_token() -> str:
    """Returns the current token used for EDL.

    This token can be used for authenticated requests to NASA Earthdata services.

    Returns:
        EDL token (access_token string)

    Raises:
        RuntimeError: If not authenticated or token is not available.

    Examples:
        Get the token for custom API requests:
        ```python
        import earthaccess
        import requests

        earthaccess.login()
        token = earthaccess.get_edl_token()

        # Use token in custom requests
        response = requests.get(
            "https://cmr.earthdata.nasa.gov/search/collections",
            headers={"Authorization": f"Bearer {token}"}
        )
        ```
    """
    auth = earthaccess.__auth__
    if auth is None:
        raise RuntimeError(
            "Authentication is not initialized. Please call earthaccess.login() first."
        )
    if not isinstance(auth, Auth):
        raise RuntimeError("Authentication object is not properly initialized")
    token = auth.token
    if isinstance(token, Mapping):
        access_token = token.get("access_token")
        if isinstance(access_token, str):
            return access_token
    elif isinstance(token, str):
        return token
    raise RuntimeError("Token is not available or not in expected format")


def auth_environ() -> Dict[str, str]:
    """Returns a dictionary with EDL credentials for environment variable export.

    This is useful for passing credentials to subprocess calls or for setting
    environment variables that other tools may use for authentication.

    Returns:
        A dictionary containing EARTHDATA_USERNAME and EARTHDATA_PASSWORD.

    Raises:
        RuntimeError: If not authenticated with earthaccess.login() first.

    Examples:
        Set environment variables for subprocess:
        ```python
        import os
        import earthaccess

        earthaccess.login()
        env = earthaccess.auth_environ()
        os.environ.update(env)
        # Now subprocess calls can use EARTHDATA_USERNAME and EARTHDATA_PASSWORD
        ```

        Pass to subprocess:
        ```python
        import subprocess

        result = subprocess.run(
            ["some_tool", "--arg"],
            env={**os.environ, **earthaccess.auth_environ()}
        )
        ```
    """
    auth = earthaccess.__auth__
    if auth is None:
        raise RuntimeError(
            "Authentication is not initialized. Please call earthaccess.login() first."
        )
    if not isinstance(auth, Auth):
        raise RuntimeError("Authentication object is not properly initialized")
    if not auth.authenticated:
        raise RuntimeError(
            "`auth_environ()` requires you to first authenticate with `earthaccess.login()`"
        )
    return {"EARTHDATA_USERNAME": auth.username, "EARTHDATA_PASSWORD": auth.password}
