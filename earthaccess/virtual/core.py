"""Core implementation of ``earthaccess.virtualize()``.

This module contains the single public entry point for creating virtual
xarray Datasets from NASA Earthdata granules.
"""

from __future__ import annotations

import json
import logging
import tempfile
import warnings
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

import earthaccess
from earthaccess._access import AccessType, CanonicalAccessType, normalize_access
from earthaccess.virtual._credentials import (
    build_obstore_registry,
    get_granule_credentials_endpoint_and_region,
)
from earthaccess.virtual._parser import get_urls_for_parser, resolve_parser

if TYPE_CHECKING:
    from collections.abc import Callable

    import xarray as xr

    from earthaccess.virtual._types import (
        CombineAttrsType,
        CombineType,
        CompatType,
        DataVarsType,
        JoinType,
        ParallelType,
        ParserType,
        ReferenceFormatType,
    )


logger = logging.getLogger(__name__)


def virtualize(  # noqa: PLR0913, C901
    granules: list[earthaccess.DataGranule],
    *,
    access: AccessType | None = "https",
    load: bool = False,
    group: str = "/",
    concat_dim: str | None = None,
    combine: CombineType = "nested",
    preprocess: Callable[[xr.Dataset], xr.Dataset] | None = None,
    data_vars: DataVarsType = "all",
    coords: str = "different",
    compat: CompatType = "no_conflicts",
    combine_attrs: CombineAttrsType = "drop_conflicts",
    join: JoinType = "outer",
    loadable_variables: list[str] | None = None,
    drop_variables: list[str] | None = None,
    parallel: ParallelType = "dask",
    parser: ParserType = "DMRPPParser",
    reference_dir: str | None = None,
    reference_format: ReferenceFormatType = "json",
    tree: bool = False,
    **xr_combine_kwargs: Any,
) -> xr.Dataset | xr.DataTree:
    """Create a virtual xarray Dataset from NASA Earthdata granules.

    Uses VirtualiZarr to open granules as virtual datasets backed by cloud
    object storage without downloading data.  By default returns a virtual
    dataset (``load=False``); set ``load=True`` to return a concrete
    lazily-loaded xarray Dataset via a kerchunk round-trip.

    The ``parser`` controls which VirtualiZarr backend reads the files.  The
    default ``"DMRPPParser"`` is the fastest option and uses NASA pre-computed
    DMR++ sidecar files.  When those sidecars are absent earthaccess
    automatically falls back to ``"HDFParser"`` and emits a ``UserWarning``.

    Parameters:
        granules: One or more ``DataGranule`` objects from
            ``earthaccess.search_data()``.
        access: Access method. ``"https"`` (default) works anywhere; ``"s3"``
            uses direct S3 access. Legacy values ``"direct"`` and ``"indirect"``
            are accepted.
        load: When ``False`` (default) returns a virtual dataset with
            ``ManifestArray`` variables.  When ``True`` materialises the
            references via a kerchunk round-trip and returns a concrete,
            lazily-loaded ``xr.Dataset`` backed by dask arrays.
        group: HDF5/NetCDF4 group path to open.  Defaults to the root
            group ``"/"``.
        concat_dim: Dimension name used to concatenate granules along when
            ``combine="nested"``.  Required when ``len(granules) > 1``.
        combine: How to combine multiple granules.  ``"nested"`` (default)
            stacks them in order along ``concat_dim``; ``"by_coords"`` aligns
            them on their shared coordinates and does not take a
            ``concat_dim``.
        preprocess: Optional callable applied to each single-granule virtual
            dataset before combining.
        data_vars: Forwarded to ``xarray.combine_nested``.
        coords: Forwarded to ``xarray.combine_nested``.
        compat: Forwarded to ``xarray.combine_nested``.
        combine_attrs: Forwarded to ``xarray.combine_nested``.
        join: How coordinate values are combined when ``combine="by_coords"``
            (``"outer"``, ``"inner"``, ``"left"``, ``"right"``, ``"exact"``,
            ``"override"``).
        loadable_variables: Variable names to load eagerly as real arrays
            instead of virtual references.  Useful for index coordinates that
            need to be sliced by label.
        drop_variables: Variable names to omit from the opened dataset.
        parallel: Parallelism backend.  ``"dask"`` (default) wraps opens in
            ``dask.delayed``; ``"lithops"`` uses Lithops; ``False`` disables
            parallelism.
        parser: VirtualiZarr parser to use.  One of ``"DMRPPParser"``
            (default), ``"HDFParser"``, ``"NetCDF3Parser"``, a lowercase alias
            (``"dmrpp"``, ``"hdf"``, ``"hdf5"``, ``"netcdf3"``), or a
            pre-instantiated parser object.
        reference_dir: Directory for kerchunk reference files when
            ``load=True``.  A temporary directory is used when ``None``.
        reference_format: Serialisation format when ``load=True``.
            ``"json"`` (default) or ``"parquet"``.
        tree: When ``True``, return an ``xr.DataTree`` via
            ``open_virtual_datatree`` (one node per HDF5/NetCDF4 group).
            Requires exactly one granule and ``load=False``.
        **xr_combine_kwargs: Additional keyword arguments forwarded to
            ``xarray.combine_nested``.

    Returns:
        An ``xr.Dataset``, or an ``xr.DataTree`` when ``tree=True``.  With
        ``load=False`` the dataset contains ``ManifestArray`` variables; with
        ``load=True`` it contains dask arrays backed by the kerchunk reference
        store.

    Raises:
        ValueError: If ``granules`` is empty.
        ValueError: If ``combine="nested"``, ``len(granules) > 1``, and
            ``concat_dim`` is ``None``.
        ValueError: If ``combine="by_coords"`` and ``concat_dim`` is not
            ``None``.
        ValueError: If ``tree=True`` with more than one granule, or with
            ``load=True``.
        ValueError: If ``parser`` is an unrecognised string.
        ImportError: If ``earthaccess[virtualizarr]`` is not installed.

    Examples:
        ```python
        import earthaccess

        granules = earthaccess.search_data(
            count=5,
            temporal=("2024-01-01", "2024-01-05"),
            short_name="MUR-JPL-L4-GLOB-v4.1",
        )

        # Virtual dataset (no data downloaded)
        vds = earthaccess.virtualize(granules, access="https", concat_dim="time")
        vds.virtualize.to_kerchunk("mur_combined.json", format="json")

        # Loaded dataset (kerchunk round-trip, lazy dask arrays)
        ds = earthaccess.virtualize(granules, access="s3", load=True, concat_dim="time")
        ```
    """
    access = normalize_access(access)

    if len(granules) == 0:
        msg = "No granules provided. At least one granule is required."
        raise ValueError(msg)

    if tree:
        if len(granules) != 1:
            msg = (
                "tree=True requires exactly one granule. "
                "open_virtual_datatree only opens a single data source."
            )
            raise ValueError(msg)
        if load:
            msg = "tree=True is only supported with load=False."
            raise ValueError(msg)
    elif combine == "nested":
        if len(granules) > 1 and concat_dim is None:
            msg = (
                "concat_dim is required when virtualizing more than one granule "
                "with combine='nested'. Pass concat_dim='<dimension_name>' to "
                "specify how to concatenate, or use combine='by_coords'."
            )
            raise ValueError(msg)
    elif concat_dim is not None:
        msg = (
            "concat_dim is not valid with combine='by_coords'. Align granules "
            "on their coordinates instead, or pass combine='nested'."
        )
        raise ValueError(msg)

    # Validate / resolve parser early so callers get a clear error before any
    # network activity.
    resolved_parser = resolve_parser(parser, group=group if group != "/" else None)

    registry = build_obstore_registry(granules, access=access)

    def _open_once(parser_obj: Any, obj_registry: Any) -> xr.Dataset | xr.DataTree:
        return _open_virtual_dispatch(
            granules=granules,
            parser=parser_obj,
            registry=obj_registry,
            access=access,
            concat_dim=concat_dim,
            combine=combine,
            join=join,
            preprocess=preprocess,
            loadable_variables=loadable_variables,
            drop_variables=drop_variables,
            parallel=parallel,
            data_vars=data_vars,
            coords=coords,
            compat=compat,
            combine_attrs=combine_attrs,
            tree=tree,
            **xr_combine_kwargs,
        )

    # Attempt to open with the requested parser; fall back to HDFParser if
    # DMR++ sidecars are not present.
    try:
        vds = _open_once(resolved_parser, registry)
    except FileNotFoundError:
        if type(resolved_parser).__name__ != "DMRPPParser":
            raise
        warnings.warn(
            "DMR++ sidecar files were not found for one or more granules. "
            "Falling back to HDFParser. "
            "Set parser='HDFParser' to silence this warning.",
            UserWarning,
            stacklevel=2,
        )
        resolved_parser = resolve_parser(
            "HDFParser",
            group=group if group != "/" else None,
        )
        registry = build_obstore_registry(granules, access=access)
        vds = _open_once(resolved_parser, registry)

    if not load:
        return vds

    import xarray as xr

    if not isinstance(vds, xr.Dataset):
        msg = "load=True requires a Dataset; open with tree=False."
        raise TypeError(msg)

    return _load_via_kerchunk(
        vds=vds,
        granules=granules,
        group=group,
        access=access,
        reference_dir=reference_dir,
        reference_format=reference_format,
    )


# ---------------------------------------------------------------------------
# Internal helpers — separated to make mocking clean in tests
# ---------------------------------------------------------------------------


def _open_virtual_dispatch(  # noqa: PLR0913
    granules: list[earthaccess.DataGranule],
    parser: Any,
    registry: Any,
    access: CanonicalAccessType,
    concat_dim: str | None,
    combine: CombineType,
    join: JoinType,
    preprocess: Callable | None,
    loadable_variables: list[str] | None,
    drop_variables: list[str] | None,
    parallel: ParallelType,
    data_vars: DataVarsType,
    coords: str,
    compat: CompatType,
    combine_attrs: CombineAttrsType,
    *,
    tree: bool,
    **xr_combine_kwargs: Any,
) -> xr.Dataset | xr.DataTree:
    """Route granules to the correct VirtualiZarr opener.

    ``tree=True`` uses ``open_virtual_datatree`` (single granule only), a
    single granule uses ``open_virtual_dataset`` directly, and everything else
    goes through ``open_virtual_mfdataset``.
    """
    if tree:
        url = get_urls_for_parser(granules, parser, access=access)[0]
        return _open_virtual_datatree(
            url,
            parser=parser,
            registry=registry,
            loadable_variables=loadable_variables,
            **xr_combine_kwargs,
        )

    if len(granules) == 1:
        url = get_urls_for_parser(granules, parser, access=access)[0]
        return _open_virtual_dataset_single(
            url,
            parser=parser,
            registry=registry,
            preprocess=preprocess,
            drop_variables=drop_variables,
            loadable_variables=loadable_variables,
            **xr_combine_kwargs,
        )

    return _open_virtual_mfdataset(
        granules=granules,
        parser=parser,
        registry=registry,
        access=access,
        concat_dim=concat_dim,
        combine=combine,
        join=join,
        preprocess=preprocess,
        loadable_variables=loadable_variables,
        drop_variables=drop_variables,
        parallel=parallel,
        data_vars=data_vars,
        coords=coords,
        compat=compat,
        combine_attrs=combine_attrs,
        **xr_combine_kwargs,
    )


def _open_virtual_dataset_single(  # noqa: PLR0913
    url: str,
    parser: Any,
    registry: Any,
    *,
    preprocess: Callable | None,
    drop_variables: list[str] | None,
    loadable_variables: list[str] | None,
    **kwargs: Any,
) -> xr.Dataset:
    """Open a single URL with ``vz.open_virtual_dataset``, then apply preprocess."""
    try:
        import virtualizarr as vz
    except ImportError as exc:
        msg = (
            "earthaccess.virtualize() requires `pip install earthaccess[virtualizarr]`"
        )
        raise ImportError(msg) from exc

    vds = vz.open_virtual_dataset(
        url,
        registry=registry,
        parser=parser,
        drop_variables=drop_variables,
        loadable_variables=loadable_variables,
        **kwargs,
    )
    return preprocess(vds) if preprocess else vds


def _open_virtual_datatree(
    url: str,
    parser: Any,
    registry: Any,
    *,
    loadable_variables: list[str] | None,
    **kwargs: Any,
) -> xr.DataTree:
    """Open a single URL with ``vz.open_virtual_datatree`` (returns a DataTree)."""
    try:
        import virtualizarr as vz
    except ImportError as exc:
        msg = (
            "earthaccess.virtualize() requires `pip install earthaccess[virtualizarr]`"
        )
        raise ImportError(msg) from exc

    return vz.open_virtual_datatree(
        url,
        registry=registry,
        parser=parser,
        loadable_variables=loadable_variables,
        **kwargs,
    )


def _open_virtual_mfdataset(  # noqa: PLR0913
    granules: list[earthaccess.DataGranule],
    parser: Any,
    registry: Any,
    access: CanonicalAccessType,
    concat_dim: str | None,
    combine: CombineType,
    join: JoinType,
    preprocess: Callable | None,
    loadable_variables: list[str] | None,
    drop_variables: list[str] | None,
    parallel: ParallelType,
    data_vars: DataVarsType,
    coords: str,
    compat: CompatType,
    combine_attrs: CombineAttrsType,
    **xr_combine_kwargs: Any,
) -> xr.Dataset:
    """Thin wrapper around ``vz.open_virtual_mfdataset`` for testability."""
    try:
        import virtualizarr as vz  # noqa: PLC0415
    except ImportError as exc:
        msg = (
            "earthaccess.virtualize() requires `pip install earthaccess[virtualizarr]`"
        )
        raise ImportError(msg) from exc

    urls = get_urls_for_parser(granules, parser, access=access)

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Numcodecs codecs*",
            category=UserWarning,
        )
        return vz.open_virtual_mfdataset(
            urls=urls,
            registry=registry,
            parser=parser,
            preprocess=preprocess,
            parallel=parallel,
            combine=combine,
            concat_dim=concat_dim,
            join=join,
            data_vars=data_vars,
            coords=coords,
            compat=compat,
            combine_attrs=combine_attrs,
            loadable_variables=loadable_variables,
            drop_variables=drop_variables,
            **xr_combine_kwargs,
        )


def _load_via_kerchunk(  # noqa: PLR0913
    vds: xr.Dataset,
    granules: list[earthaccess.DataGranule],
    group: str,
    access: CanonicalAccessType,
    reference_dir: str | None,
    reference_format: ReferenceFormatType,
) -> xr.Dataset:
    """Materialise a virtual dataset coordinates for "fancy" slicing via a kerchunk round-trip.

    Needed until https://github.com/zarr-developers/VirtualiZarr/issues/360
    is resolved.
    """
    import xarray as xr  # noqa: PLC0415

    if access == "s3":
        credentials_endpoint, _ = get_granule_credentials_endpoint_and_region(
            granules[0],
        )
        fs = earthaccess.get_s3_filesystem(endpoint=credentials_endpoint)
    else:
        fs = earthaccess.get_fsspec_https_session()

    if reference_dir is None:
        ref_dir = Path(tempfile.gettempdir())
    else:
        ref_dir = Path(reference_dir)
        ref_dir.mkdir(exist_ok=True, parents=True)

    collection_id = granules[0]["meta"]["collection-concept-id"]

    if group in (None, "/"):
        group_name = "root"
    else:
        group_name = group.replace("/", "_").replace(" ", "_").lstrip("_")

    ref_path = ref_dir / f"{collection_id}-{group_name}.{reference_format}"

    # Round-trip: write kerchunk reference, then reopen with xarray.
    vds.virtualize.to_kerchunk(str(ref_path), format=reference_format)

    storage_options = {
        "remote_protocol": access,
        "remote_options": fs.storage_options,
    }
    return xr.open_dataset(
        str(ref_path),
        engine="kerchunk",
        storage_options=storage_options,
    )


# ---------------------------------------------------------------------------
# open_virtual — open existing virtual stores (Icechunk / VirtualiZarr)
# ---------------------------------------------------------------------------


def _is_icechunk_uri(uri: str) -> bool:
    if uri.startswith("icechunk://") or "icechunk" in uri:
        return True
    return _is_local_icechunk_store(uri)


def _is_local_icechunk_store(uri: str) -> bool:
    """Return ``True`` if *uri* is a local directory containing an Icechunk repo."""
    from urllib.parse import urlparse

    parsed = urlparse(uri)
    if parsed.scheme not in ("", "file"):
        return False
    path = Path(parsed.path) if parsed.scheme == "file" else Path(uri)
    return (path / "snapshots" / "1CECHNKREP0F1RSTCMT0").is_file()


def _is_kerchunk_uri(uri: str) -> bool:
    return uri.endswith((".parquet", ".json"))


def _edl_bearer_token() -> str | None:
    """Return the user's EDL access token, or ``None`` when not authenticated."""
    try:
        token = earthaccess.__auth__.token["access_token"]
    except (AttributeError, TypeError, KeyError):
        return None
    return token


def _s3_static_credentials(
    collection: earthaccess.DataCollection,
) -> Any:
    """Return ``S3StaticCredentials`` built from a collection's cached credentials.

    Safe to wrap in ``functools.partial`` as a ``get_credentials`` callback for
    refreshable icechunk credentials: each invocation reads the collection's
    (cached) temporary S3 credentials and returns a fresh set.
    """
    from datetime import UTC, datetime, timedelta

    import icechunk

    creds = collection.s3_credentials
    return icechunk.S3StaticCredentials(
        access_key_id=creds["accessKeyId"],
        secret_access_key=creds["secretAccessKey"],
        session_token=creds["sessionToken"],
        expires_after=datetime.now(UTC) + timedelta(hours=1),
    )


def _build_vcc_credentials(
    vccs: dict[str, Any] | None,
    *,
    collection: earthaccess.DataCollection | None,
) -> dict[str, Any]:
    """Build an ``authorize_virtual_chunk_access`` mapping from repo VCCs.

    Maps each virtual chunk container ``url_prefix`` to an icechunk credential:

    - ``s3://`` uses refreshable NASA S3 credentials when a ``collection`` is
      available, otherwise credentials from the process environment.
    - ``http(s)://`` uses the ``HttpAccess`` no-auth sentinel.
    - ``file://`` uses the ``LocalFileSystemAccess`` no-auth sentinel.
    - ``gs://``/``gcs://`` and ``azure://``/``az://``/``abfs://`` fall back to
      environment credentials.

    Credential selection is driven by ``collection`` presence.
    """
    from urllib.parse import urlparse

    import icechunk

    if not vccs:
        return {}

    mapping: dict[str, Any] = {}
    for prefix in vccs:
        scheme = urlparse(prefix).scheme
        if scheme == "s3":
            if collection is not None:
                mapping[prefix] = icechunk.s3_credentials(
                    get_credentials=partial(_s3_static_credentials, collection),
                )
            else:
                mapping[prefix] = icechunk.s3_from_env_credentials()
        elif scheme in ("http", "https"):
            mapping[prefix] = icechunk.credentials.HttpAccess
        elif scheme in ("file", ""):
            mapping[prefix] = icechunk.credentials.LocalFileSystemAccess
        elif scheme in ("gs", "gcs"):
            mapping[prefix] = icechunk.gcs_from_env_credentials()
        elif scheme in ("az", "azure", "abfs"):
            mapping[prefix] = icechunk.azure_from_env_credentials()
    return mapping


def _inject_http_vcc_headers(config: Any, *, token: str) -> Any:
    """Rebuild HTTP VCC store configs with the EDL bearer header.

    Only containers pointing at NASA hosts get the header injected; other HTTP
    containers stay anonymous so we never leak the token to arbitrary hosts.

    Returns the (possibly new) config when at least one container was modified,
    otherwise ``None``.
    """
    import icechunk

    vccs = config.virtual_chunk_containers
    if not vccs:
        return None

    modified = False
    for prefix, vcc in vccs.items():
        if prefix.startswith(("https://", "http://")) and _is_nasa_url(prefix):
            store = icechunk.http_store(
                headers={"Authorization": f"Bearer {token}"},
            )
            config.set_virtual_chunk_container(
                icechunk.VirtualChunkContainer(prefix, store, name=vcc.name),
            )
            modified = True
    return config if modified else None


def _authorize_vccs(
    storage: Any,
    *,
    collection: earthaccess.DataCollection | None,
    explicit: dict[str, Any] | None,
) -> tuple[Any | None, dict[str, Any]]:
    """Authorize the repo's virtual chunk containers for reading.

    Fetches the repo config, builds a credential mapping for every VCC, injects
    the EDL bearer header into NASA HTTP containers, and merges any explicit
    user-supplied mapping on top (explicit keys win).

    Returns a ``(config, mapping)`` pair. ``config`` is only non-``None`` when
    it must be passed to ``Repository.open`` (i.e. HTTP header injection
    modified it); ``mapping`` is the merged ``authorize_virtual_chunk_access``.
    """
    import icechunk

    config = icechunk.Repository.fetch_config(storage)
    vccs = config.virtual_chunk_containers if config is not None else None

    mapping = _build_vcc_credentials(vccs, collection=collection)

    config_to_pass: Any | None = None
    token = _edl_bearer_token()
    if token and vccs:
        config_to_pass = _inject_http_vcc_headers(config, token=token)

    if explicit:
        mapping = {**mapping, **explicit}

    return config_to_pass, mapping


def _icechunk_storage(
    uri: str,
    storage_options: dict[str, Any] | None = None,
    access: AccessType | None = "https",
) -> Any:
    """Resolve *uri* to the icechunk ``Storage`` for its repository."""
    from urllib.parse import urlparse

    import icechunk

    parsed = urlparse(uri)
    canonical_access = normalize_access(access)

    if canonical_access == "s3" or parsed.scheme == "s3":
        opts = dict(storage_options) if storage_options else {}
        return icechunk.s3_storage(
            bucket=parsed.netloc,
            prefix=parsed.path.lstrip("/"),
            **opts,
        )
    if parsed.scheme in ("http", "https"):
        if storage_options:
            return icechunk.http_storage(uri, storage_options)
        if _is_nasa_url(uri) and (token := _edl_bearer_token()):
            return icechunk.http_storage(
                uri,
                headers={"Authorization": f"Bearer {token}"},
            )
        return icechunk.redirect_storage(uri)
    if parsed.scheme == "file":
        return icechunk.local_filesystem_storage(parsed.path)
    return icechunk.local_filesystem_storage(uri)


def _as_url(uri: str) -> str:
    """Return a URL VirtualiZarr accepts (local paths become ``file://``)."""
    from urllib.parse import urlparse

    if urlparse(uri).scheme:
        return uri
    return Path(uri).expanduser().resolve().as_uri()


def _open_icechunk(
    uri: str,
    storage_options: dict[str, Any] | None = None,
    access: AccessType | None = "https",
    authorize_virtual_chunk_access: dict[str, Any] | None = None,
    **kwargs: Any,
) -> xr.Dataset:
    try:
        import icechunk
        import xarray as xr
    except ImportError as exc:
        msg = (
            "earthaccess.open_virtual() with an Icechunk store requires "
            "`pip install earthaccess[virtualizarr]`"
        )
        raise ImportError(
            msg,
        ) from exc

    storage = _icechunk_storage(uri, storage_options, access)
    config, vcc_map = _authorize_vccs(
        storage,
        collection=None,
        explicit=authorize_virtual_chunk_access,
    )

    repo = icechunk.Repository.open(
        storage=storage,
        config=config,
        authorize_virtual_chunk_access=vcc_map or None,
    )
    session = repo.readonly_session("main")
    store = session.store
    return xr.open_zarr(store, **kwargs)


def _open_icechunk_virtual(
    uri: str,
    storage_options: dict[str, Any] | None = None,
    access: AccessType | None = "https",
    authorize_virtual_chunk_access: dict[str, Any] | None = None,
    **kwargs: Any,
) -> xr.Dataset:
    """Open an icechunk store as a manifest-form Dataset (``load=False``).

    Variables stay ``ManifestArray``-backed so the result can be combined and
    written again without reading chunk bytes. Coordinate values are attached
    concretely because xarray needs them to concatenate.
    """
    try:
        import icechunk
        import virtualizarr as vz
        import xarray as xr
        from virtualizarr.parsers import IcechunkParser
    except ImportError as exc:
        msg = (
            "earthaccess.open_virtual(load=False) with an Icechunk store "
            "requires `pip install earthaccess[virtualizarr]`"
        )
        raise ImportError(
            msg,
        ) from exc

    storage = _icechunk_storage(uri, storage_options, access)
    config, vcc_map = _authorize_vccs(
        storage,
        collection=None,
        explicit=authorize_virtual_chunk_access,
    )
    repo = icechunk.Repository.open(
        storage=storage,
        config=config,
        authorize_virtual_chunk_access=vcc_map or None,
    )
    concrete = xr.open_zarr(repo.readonly_session("main").store, consolidated=False)

    url = _as_url(uri)
    group = kwargs.pop("group", None)
    parser = IcechunkParser(group=group, skip_variables=list(concrete.coords))
    vds = vz.open_virtual_dataset(
        url,
        parser=parser,
        registry=_build_registry_for_url(url),
        **kwargs,
    )
    for name in concrete.coords:
        vds.coords[name] = concrete[name]
    return vds


def _open_kerchunk(
    uri: str,
    storage_options: dict[str, Any] | None = None,
    **kwargs: Any,
) -> xr.Dataset:
    try:
        import xarray as xr
    except ImportError as exc:
        msg = "earthaccess.open_virtual() requires `pip install earthaccess[virtualizarr]`"
        raise ImportError(
            msg,
        ) from exc

    store_opts = storage_options or {}
    return xr.open_dataset(uri, engine="kerchunk", storage_options=store_opts, **kwargs)


# ---------------------------------------------------------------------------
# force_external — download kerchunk refs and rewrite s3:// URLs to https://
# ---------------------------------------------------------------------------


def _transform_refs(obj: Any, https_base: str) -> None:
    """Recursively walk a kerchunk refs dict/list and rewrite s3:// URLs in-place."""
    prefix = "s3://"
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and v.startswith(prefix):
                obj[k] = https_base + v[len(prefix) :]
            elif isinstance(v, (dict, list)):
                _transform_refs(v, https_base)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str) and v.startswith(prefix):
                obj[i] = https_base + v[len(prefix) :]
            elif isinstance(v, (dict, list)):
                _transform_refs(v, https_base)


def _sanitize_references_for_external(url: str) -> str:
    """Download a kerchunk reference file, rewrite ``s3://`` URLs to ``https://``.

    The HTTPS base is inferred from the reference file URL's host, which works
    for all NASA Earthdata Cloud DAACs (PODAAC, NSIDC, GES DISC, LP DAAC, …).

    Returns the local path to the sanitized file.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if not parsed.scheme:
        return url

    local = Path(tempfile.gettempdir()) / f"external_{Path(url).name}"

    if local.exists():
        return str(local)

    host = parsed.netloc
    https_base = f"https://{host}/"
    fs = earthaccess.get_fsspec_https_session()

    if url.endswith(".json"):
        with fs.open(url) as f:
            refs: dict[str, Any] = json.load(f)
        _transform_refs(refs, https_base)
        local.write_text(json.dumps(refs))

    elif url.endswith(".parquet"):
        import pandas as pd

        with fs.open(url) as f:
            refs_df = pd.read_parquet(f)
        if "path" in refs_df.columns:
            mask = refs_df["path"].str.startswith("s3://", na=False)
            refs_df.loc[mask, "path"] = (
                https_base + refs_df.loc[mask, "path"].str[len("s3://") :]
            )
        refs_df.to_parquet(local)

    return str(local)


def _open_kerchunk_from_collection(
    collection: earthaccess.DataCollection,
    url: str,
    access: AccessType | None = "https",
    **kwargs: Any,
) -> xr.Dataset:
    try:
        import fsspec
        import xarray as xr
        import zarr
    except ImportError as exc:
        msg = "earthaccess.open_virtual() requires `pip install earthaccess[virtualizarr]`"
        raise ImportError(
            msg,
        ) from exc

    canonical_access = normalize_access(access)
    if canonical_access == "s3":
        daac_fs = collection.get_s3_filesystem()
        remote_protocol = "s3"
    else:
        daac_fs = earthaccess.get_fsspec_https_session()
        remote_protocol = "https"

    remote_options = {"asynchronous": True, **daac_fs.storage_options}
    fs = fsspec.filesystem(
        "reference",
        fo=url,
        remote_protocol=remote_protocol,
        asynchronous=True,
        remote_options=remote_options,
    )
    store = zarr.storage.FsspecStore(fs, read_only=True)
    return xr.open_zarr(store, consolidated=False, **kwargs)


def _open_icechunk_from_collection(
    collection: earthaccess.DataCollection,
    url: str,
    access: AccessType | None = "https",
    authorize_virtual_chunk_access: dict[str, Any] | None = None,
    **kwargs: Any,
) -> xr.Dataset:
    try:
        from datetime import UTC, datetime, timedelta

        import icechunk
        import xarray as xr
    except ImportError as exc:
        msg = (
            "earthaccess.open_virtual() with an Icechunk store requires "
            "`pip install earthaccess[virtualizarr]`"
        )
        raise ImportError(
            msg,
        ) from exc

    canonical_access = normalize_access(access)
    if canonical_access == "s3":
        from urllib.parse import urlparse

        creds = collection.get_s3_credentials()
        ice_creds = icechunk.S3StaticCredentials(
            access_key_id=creds["accessKeyId"],
            secret_access_key=creds["secretAccessKey"],
            session_token=creds["sessionToken"],
            expires_after=datetime.now(UTC) + timedelta(hours=1),
        )
        parsed = urlparse(url)
        storage = icechunk.s3_storage(
            bucket=parsed.netloc,
            prefix=parsed.path.lstrip("/"),
            get_credentials=lambda: ice_creds,
        )
    elif _is_nasa_url(url) and (token := _edl_bearer_token()):
        storage = icechunk.http_storage(
            url,
            headers={"Authorization": f"Bearer {token}"},
        )
    else:
        storage = icechunk.redirect_storage(url)

    config, vcc_map = _authorize_vccs(
        storage,
        collection=collection,
        explicit=authorize_virtual_chunk_access,
    )

    repo = icechunk.Repository.open(
        storage=storage,
        config=config,
        authorize_virtual_chunk_access=vcc_map or None,
    )
    session = repo.readonly_session("main")
    store = session.store
    return xr.open_zarr(store, **kwargs)


# ---------------------------------------------------------------------------
# write_virtual — create/append virtual datasets in an Icechunk store
# ---------------------------------------------------------------------------


def _derive_vccs_from_vds(vds: xr.Dataset) -> dict[str, Any]:
    """Collect the virtual chunk containers needed to write *vds*.

    Walks every ``ManifestArray`` in the dataset and derives one container per
    unique ``scheme://host/`` prefix found in the virtual chunk references.
    ``s3://`` containers are configured for ``us-west-2`` (the default NASA
    cloud region); ``http(s)://`` containers need no credentials at write time.

    Returns a mapping of ``url_prefix`` to an icechunk ``ObjectStoreConfig``.
    """
    from urllib.parse import urlparse

    import icechunk

    try:
        from virtualizarr.manifests import ManifestArray
    except ImportError as exc:
        msg = (
            "earthaccess.write_virtual() requires "
            "`pip install earthaccess[virtualizarr]`"
        )
        raise ImportError(msg) from exc

    prefixes: dict[str, str] = {}
    for var in vds.variables.values():
        data = var.data
        if not isinstance(data, ManifestArray):
            continue
        for path in data.manifest.iter_nonempty_paths():
            parsed = urlparse(path)
            if parsed.scheme in ("s3", "https", "http"):
                prefixes[f"{parsed.scheme}://{parsed.netloc}/"] = parsed.scheme

    if not prefixes:
        msg = (
            "write_virtual() requires a virtual dataset (load=False) backed by "
            "ManifestArray variables; no virtual chunk references were found."
        )
        raise ValueError(msg)

    vccs: dict[str, Any] = {}
    for prefix, scheme in prefixes.items():
        if scheme == "s3":
            vccs[prefix] = icechunk.s3_store(region="us-west-2")
        else:
            vccs[prefix] = icechunk.http_store()
    return vccs


def _write_virtual_to_icechunk(
    vds: xr.Dataset,
    store: str,
    *,
    append_dim: str | None,
    commit: bool,
    storage_options: dict[str, Any] | None,
) -> xr.Dataset:
    """Create (or append to) an Icechunk store from a virtual dataset.

    On first write the store is created with the containers derived from the
    dataset's virtual chunk references.  On subsequent writes the existing repo
    is opened and, when ``append_dim`` is given, the dataset is appended along
    that dimension via VirtualiZarr's ``to_icechunk``.
    """
    from urllib.parse import urlparse

    import icechunk

    vccs = _derive_vccs_from_vds(vds)

    parsed = urlparse(store)
    if parsed.scheme == "s3":
        opts = dict(storage_options) if storage_options else {"from_env": True}
        storage = icechunk.s3_storage(
            bucket=parsed.netloc,
            prefix=parsed.path.lstrip("/"),
            **opts,
        )
    else:
        storage = icechunk.local_filesystem_storage(store)

    if icechunk.Repository.exists(storage):
        repo = icechunk.Repository.open(storage=storage)
    else:
        config = icechunk.RepositoryConfig.default()
        for prefix, store_config in vccs.items():
            config.set_virtual_chunk_container(
                icechunk.VirtualChunkContainer(prefix, store_config),
            )
        repo = icechunk.Repository.create(storage=storage, config=config)
        repo.save_config()

    session = repo.writable_session("main")
    vds.vz.to_icechunk(session.store, append_dim=append_dim)
    if commit:
        message = "write_virtual append" if append_dim else "write_virtual create"
        session.commit(message)
    return vds


def write_virtual(
    vds: xr.Dataset,
    store: str | Path,
    *,
    format: str = "icechunk",
    append_dim: str | None = None,
    commit: bool = True,
    storage_options: dict[str, Any] | None = None,
) -> xr.Dataset:
    """Write (or append) a virtual dataset to an Icechunk store.

    ``vds`` is the virtual dataset returned by ``earthaccess.virtualize()``
    with ``load=False``.  On the first call the store is created at ``store``
    (a local path or an ``s3://`` URI) with the virtual chunk containers
    derived from the dataset's references.  Subsequent calls with
    ``append_dim`` (e.g. ``"time"``) open the existing store and append the
    dataset along that dimension.

    Parameters:
        vds: The virtual dataset to write (``ManifestArray``-backed).
        store: Target Icechunk store location — a local path, or an
            ``s3://bucket/prefix`` URI (write credentials from
            ``storage_options`` or the environment).
        format: Store format. Only ``"icechunk"`` is currently supported.
        append_dim: When set, append the dataset along this dimension instead
            of writing to a new group.
        commit: When ``True`` (default), commit the write to the store's
            ``main`` branch.
        storage_options: Extra options for ``s3://`` stores (e.g. AWS write
            credentials).  Ignored for local paths.

    Returns:
        The input ``vds`` unchanged.

    Raises:
        ValueError: If ``vds`` contains no virtual chunk references, or if
            ``format`` is not ``"icechunk"``.
        ImportError: If ``earthaccess[virtualizarr]`` is not installed.
    """
    if format != "icechunk":
        msg = (
            f"Unsupported format {format!r}: write_virtual() currently only "
            "supports 'icechunk'."
        )
        raise ValueError(msg)

    import importlib.util

    if importlib.util.find_spec("icechunk") is None:
        msg = (
            "earthaccess.write_virtual() requires "
            "`pip install earthaccess[virtualizarr]`"
        )
        raise ImportError(msg)

    return _write_virtual_to_icechunk(
        vds=vds,
        store=str(store),
        append_dim=append_dim,
        commit=commit,
        storage_options=storage_options,
    )


# ---------------------------------------------------------------------------
# open_virtual via VirtualiZarr (load=False)
# ---------------------------------------------------------------------------


def _is_nasa_url(url: str) -> bool:
    """Return ``True`` if the URL belongs to a NASA Earthdata host."""
    return "nasa.gov" in url.lower()


def _build_registry_for_url(url: str) -> Any:
    """Build an ``ObjectStoreRegistry`` for the given reference file *url*.

    A ``LocalStore`` for ``file://`` is always registered so that local
    reference files can be read.  For remote URLs an authenticated
    ``HTTPStore`` is also registered so that referenced data files can
    be resolved with the user's EDL credentials.
    """
    from urllib.parse import urlparse

    try:
        from obspec_utils.registry import ObjectStoreRegistry
        from obstore.store import HTTPStore, LocalStore
    except ImportError:
        try:
            from virtualizarr.registry import (  # type: ignore[no-redef]
                ObjectStoreRegistry,
            )
        except ImportError:
            msg = (
                "earthaccess.open_virtual(load=False) requires "
                "`pip install earthaccess[virtualizarr]`"
            )
            raise ImportError(
                msg,
            ) from None

    stores: dict[str, Any] = {"file://": LocalStore.from_url("file:///")}

    parsed = urlparse(url)
    if parsed.scheme == "s3":
        from obstore.store import S3Store

        stores[f"s3://{parsed.netloc}"] = S3Store(bucket=parsed.netloc)
    if parsed.scheme == "https":
        try:
            token = earthaccess.__auth__.token["access_token"]
        except (AttributeError, TypeError, KeyError):
            pass
        else:
            http_store = HTTPStore.from_url(
                f"https://{parsed.netloc}",
                client_options={
                    "default_headers": {"Authorization": f"Bearer {token}"},
                },
            )
            stores[f"https://{parsed.netloc}"] = http_store

    if not parsed.scheme or parsed.scheme == "file":
        path = Path(parsed.path).resolve()
        parent = path.parent if path.suffix else path
        file_prefix = parent.as_uri()
        stores[file_prefix] = LocalStore.from_url(file_prefix)

    return ObjectStoreRegistry(stores)


def _download_reference_file(url: str) -> str:
    """Download a remote reference file to a local cache (``/tmp/cached_*``).

    Returns the local path.  Already-local files are returned as-is.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if not parsed.scheme:
        return url

    local = Path(tempfile.gettempdir()) / f"cached_{Path(url).name}"
    if local.exists():
        return str(local)

    if _is_nasa_url(url):
        fs = earthaccess.get_fsspec_https_session()
    else:
        import fsspec

        fs = fsspec.filesystem("https")

    with fs.open(url) as src:
        local.write_bytes(src.read())
    return str(local)


def _open_virtual_via_virtualizarr(
    url: str,
    *,
    registry_url: str | None = None,
    **kwargs: Any,
) -> xr.Dataset:
    """Open a kerchunk reference file using VirtualiZarr (``load=False``).

    First the reference file is opened via an fsspec reference filesystem
    to load inline coordinate values, then VirtualiZarr virtualises the
    remaining data variables and the two results are merged.

    An ``ObjectStoreRegistry`` is automatically configured from
    *registry_url* (defaults to *url*) so that referenced data files can
    be resolved with the user's EDL credentials.

    For JSON files the reference file is first downloaded to a local cache;
    parquet files are read directly from their URL.
    """
    try:
        import fsspec
        import virtualizarr as vz
        import xarray as xr
        import zarr
        from virtualizarr.parsers import KerchunkJSONParser, KerchunkParquetParser
    except ImportError as exc:
        msg = (
            "earthaccess.open_virtual(load=False) requires "
            "`pip install earthaccess[virtualizarr]`"
        )
        raise ImportError(
            msg,
        ) from exc

    auth_url = registry_url or url
    registry = _build_registry_for_url(auth_url)

    ref_path = _download_reference_file(url) if url.endswith(".json") else url

    daac_fs = (
        earthaccess.get_fsspec_https_session()
        if _is_nasa_url(auth_url)
        else fsspec.filesystem("https")
    )
    remote_options = {"asynchronous": True, **daac_fs.storage_options}
    fs = fsspec.filesystem(
        "reference",
        fo=ref_path,
        remote_protocol="https",
        asynchronous=True,
        remote_options=remote_options,
    )
    store = zarr.storage.FsspecStore(fs, read_only=True)
    kds = xr.open_zarr(store, consolidated=False)

    parser: KerchunkJSONParser | KerchunkParquetParser
    if url.endswith(".json"):
        parser = KerchunkJSONParser(skip_variables=list(kds.coords))
    elif url.endswith(".parquet"):
        parser = KerchunkParquetParser(skip_variables=list(kds.coords))
    else:
        msg = (
            f"Unsupported virtual store format: {url}. "
            "Expected a .json or .parquet file."
        )
        raise ValueError(
            msg,
        )

    vds = vz.open_virtual_dataset(ref_path, parser=parser, registry=registry, **kwargs)

    for k in kds.coords:
        vds.coords[k] = kds[k]
    return vds


def _resolve_virtual_url(
    uri: str | Path | earthaccess.DataCollection,
) -> tuple[str, earthaccess.DataCollection | None]:
    if isinstance(uri, earthaccess.DataCollection):
        url = uri.virtual_collection_url()
        if url is None:
            msg = (
                f"Collection {uri.get('meta', {}).get('concept-id', '')} "
                "does not have a virtual store (no VIRTUAL COLLECTION "
                "URL found in its RelatedUrls)."
            )
            raise ValueError(msg)
        return url, uri
    return str(uri), None


def _validate_virtual_uri(uri: object, url: str) -> None:
    if not _is_icechunk_uri(url) and not _is_kerchunk_uri(url):
        msg = (
            f"Unrecognised virtual store URI: {uri}. "
            "Expected a .icechunk, .parquet, or .json file/URI."
        )
        raise ValueError(msg)


def open_virtual(  # noqa: PLR0913
    uri: str | Path | earthaccess.DataCollection,
    *,
    access: AccessType | None = "https",
    storage_options: dict[str, Any] | None = None,
    force_external: bool = False,
    load: bool = True,
    authorize_virtual_chunk_access: dict[str, Any] | None = None,
    **kwargs: Any,
) -> xr.Dataset:
    """Open a URI or collection as a virtual xarray Dataset.

    Supports two kinds of virtual stores:

    - **Icechunk** — a versioned Zarr store (``.icechunk`` file/URI).
    - **VirtualiZarr / kerchunk** — reference-file-backed datasets
      (``.parquet`` or ``.json`` files).

    When given a ``DataCollection``, the virtual store URL is extracted from
    its metadata (``GET DATA`` + ``VIRTUAL COLLECTION`` subtype).

    Parameters:
        uri: A ``DataCollection``, or a path/URI to the virtual store
            (``.icechunk``, ``.parquet``, or ``.json``).
        access: ``"https"`` (default) or ``"s3"``. Legacy values ``"indirect"``
            and ``"direct"`` are accepted.
        storage_options: Additional options forwarded to the storage backend.
            Ignored when ``uri`` is a ``DataCollection``.
        force_external: When ``True``, download kerchunk reference files and
            rewrite ``s3://`` URLs to ``https://``, so the dataset can be
            opened without direct S3 access.  Only applies to ``.json`` and
            ``.parquet`` reference files.  Requires authentication.
        load: When ``True`` (default), returns a concrete lazily-loaded dataset
            via the kerchunk engine.  When ``False``, returns a virtual dataset
            backed by ``ManifestArray`` objects via VirtualiZarr's
            ``open_virtual_dataset``.
        authorize_virtual_chunk_access: Optional mapping of icechunk virtual
            chunk container url-prefixes to credentials.  When omitted, the
            containers are authorized automatically using the user's EDL
            credentials (NASA S3 / HTTP).  When given, these entries override
            the auto-detected ones.  Only applies to Icechunk stores.
        **kwargs: Additional keyword arguments forwarded to the opener.

    Returns:
        An ``xr.Dataset`` backed by the virtual store.

    Raises:
        ValueError: If the URI is not recognised, or the collection has no
            virtual collection URL.
        ImportError: If the required optional dependency is not installed.
        AttributeError: If the user is not authenticated.

    Examples:
        >>> import earthaccess
        >>> ds = earthaccess.open_virtual("s3://bucket/refs.parquet")
        >>> ds = earthaccess.open_virtual("/local/store.icechunk")
        >>> ds = earthaccess.open_virtual("s3://bucket/store.icechunk", access="s3")
        >>> ds = earthaccess.open_virtual(collection)
        >>> ds = earthaccess.open_virtual(collection, force_external=True)
    """
    access = normalize_access(access)
    url, collection = _resolve_virtual_url(uri)
    _validate_virtual_uri(uri, url)

    if _is_icechunk_uri(url):
        if collection is not None:
            return _open_icechunk_from_collection(
                collection,
                url,
                access=access,
                authorize_virtual_chunk_access=authorize_virtual_chunk_access,
                **kwargs,
            )
        opener = _open_icechunk_virtual if not load else _open_icechunk
        return opener(
            url,
            storage_options=storage_options,
            access=access,
            authorize_virtual_chunk_access=authorize_virtual_chunk_access,
            **kwargs,
        )

    if not load:
        uri_to_open = _sanitize_references_for_external(url) if force_external else url
        return _open_virtual_via_virtualizarr(uri_to_open, registry_url=url, **kwargs)

    if force_external:
        sanitized = _sanitize_references_for_external(url)
        opts = {
            "remote_protocol": "https",
            "remote_options": earthaccess.get_fsspec_https_session().storage_options,
        }
        return _open_kerchunk(sanitized, storage_options=opts, **kwargs)

    if collection is not None:
        return _open_kerchunk_from_collection(collection, url, access=access, **kwargs)
    return _open_kerchunk(url, storage_options=storage_options, **kwargs)
