from __future__ import annotations

import tempfile
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Union
from urllib.parse import urlparse

import earthaccess
from earthaccess._access import AccessType, normalize_access

if TYPE_CHECKING:
    import xarray as xr

# Type alias for parser - either a string name or a VirtualiZarr parser instance
ParserType = Union[Literal["DMRPPParser", "HDFParser", "NetCDF3Parser"], Any]

# Supported parser names that map to VirtualiZarr parsers
SUPPORTED_PARSERS = {"DMRPPParser", "HDFParser", "NetCDF3Parser"}


def _get_parser(parser: ParserType, group: str | None = None) -> Any:
    """Get a VirtualiZarr parser instance.

    Parameters:
        parser:
            Either a string name matching a VirtualiZarr parser class
            ("DMRPPParser", "HDFParser", "NetCDF3Parser") or a parser instance.
        group:
            Path to the netCDF4 group. Only used for DMRPPParser and HDFParser.

    Returns:
        A VirtualiZarr parser instance.

    Raises:
        ValueError: If the parser name is not recognized.
        ImportError: If the required VirtualiZarr parser is not available.
    """
    if not isinstance(parser, str):
        # User passed a parser instance directly
        return parser

    if parser not in SUPPORTED_PARSERS:
        raise ValueError(
            f"Unknown parser: {parser!r}. "
            f"Supported parsers are: {', '.join(sorted(SUPPORTED_PARSERS))}. "
            "You can also pass a VirtualiZarr parser instance directly."
        )

    try:
        if parser == "DMRPPParser":
            from virtualizarr.parsers import DMRPPParser

            return DMRPPParser(group=group)
        elif parser == "HDFParser":
            from virtualizarr.parsers import HDFParser

            return HDFParser(group=group)
        elif parser == "NetCDF3Parser":
            from virtualizarr.parsers import NetCDF3Parser

            return NetCDF3Parser()
    except ImportError as e:
        raise ImportError(
            f"Failed to import {parser} from virtualizarr. "
            "Make sure virtualizarr is installed: `pip install earthaccess[virtualizarr]`"
        ) from e

    # This should never be reached due to the check above
    raise ValueError(f"Unknown parser: {parser!r}")


def _get_urls_for_parser(
    granules: list[earthaccess.DataGranule],
    parser: ParserType,
    access: AccessType | None,
) -> list[str]:
    """Get the appropriate URLs based on the parser type.

    For DMRPPParser, appends '.dmrpp' to data URLs.
    For other parsers, returns the data URLs directly.

    Parameters:
        granules:
            List of granules to get URLs for.
        parser:
            The parser being used (string name or instance).
        access:
            Access method ("https" or "s3"). Legacy "direct"/"indirect" are accepted.

    Returns:
        List of URLs appropriate for the parser.
    """
    canonical_access = normalize_access(access)
    data_urls = [granule.data_links(access=canonical_access)[0] for granule in granules]

    # Check if this is a DMRPPParser (either by string name or class name)
    is_dmrpp = False
    if isinstance(parser, str):
        is_dmrpp = parser == "DMRPPParser"
    else:
        # Check the class name for parser instances
        is_dmrpp = type(parser).__name__ == "DMRPPParser"

    if is_dmrpp:
        return [url + ".dmrpp" for url in data_urls]
    else:
        return data_urls


def open_virtual_mfdataset(
    granules: list[earthaccess.DataGranule],
    group: str | None = None,
    access: AccessType | None = "https",
    preprocess: callable | None = None,  # type: ignore
    parallel: Literal["dask", "lithops", False] = "dask",
    load: bool = True,
    reference_dir: str | None = None,
    reference_format: Literal["json", "parquet"] = "json",
    parser: ParserType = "DMRPPParser",
    **xr_combine_nested_kwargs: Any,
) -> xr.Dataset:
    """Open multiple granules as a single virtual xarray Dataset.

    Uses VirtualiZarr to create a virtual xarray dataset with ManifestArrays. This
    virtual dataset can be used to create zarr reference files. See
    [https://virtualizarr.readthedocs.io](https://virtualizarr.readthedocs.io) for
    more information on virtual xarray datasets.

    > WARNING: This feature is experimental and may change in the future.

    Parameters:
        granules:
            The granules to open
        group:
            Path to the netCDF4 group in the given file to open. If None, the root
            group will be opened. If the file does not have groups, this parameter
            is ignored.
        access:
            The access method to use. One of "https" (default) or "s3". Legacy
            "direct"/"indirect" values are also accepted.
        preprocess:
            A function to apply to each virtual dataset before combining
        parallel:
            Open the virtual datasets in parallel (using dask.delayed or lithops)
        load:
            If load is True, earthaccess will serialize the virtual references in
            order to use lazy indexing on the resulting xarray virtual ds.
        reference_dir:
            Directory to store kerchunk references. If None, a temporary directory
            will be created and deleted after use.
        reference_format:
            When load is True, earthaccess will serialize the references using this
            format, json (default) or parquet.
        parser:
            The VirtualiZarr parser to use for reading files. Supported string values
            are "DMRPPParser" (default), "HDFParser", and "NetCDF3Parser". You can also
            pass a VirtualiZarr parser instance directly. DMRPPParser reads from DMR++
            sidecar files (appends .dmrpp to data URLs), while HDFParser and NetCDF3Parser
            read from the actual data files.
        xr_combine_nested_kwargs:
            Xarray arguments describing how to concatenate the datasets. Keyword
            arguments for xarray.combine_nested. See
            [https://docs.xarray.dev/en/stable/generated/xarray.combine_nested.html](https://docs.xarray.dev/en/stable/generated/xarray.combine_nested.html)

    Returns:
        Concatenated xarray.Dataset

    Examples:
        ```python
        >>> results = earthaccess.search_data(count=5, temporal=("2024"), short_name="MUR-JPL-L4-GLOB-v4.1")
        >>> vds = earthaccess.open_virtual_mfdataset(results, access="https", load=False, concat_dim="time", coords="minimal", compat="override", combine_attrs="drop_conflicts")
        >>> vds
        <xarray.Dataset> Size: 29GB
        Dimensions:           (time: 5, lat: 17999, lon: 36000)
        Coordinates:
            time              (time) int32 20B ManifestArray<shape=(5,), dtype=int32,...
            lat               (lat) float32 72kB ManifestArray<shape=(17999,), dtype=...
            lon               (lon) float32 144kB ManifestArray<shape=(36000,), dtype...
        Data variables:
            mask              (time, lat, lon) int8 3GB ManifestArray<shape=(5, 17999...
            sea_ice_fraction  (time, lat, lon) int8 3GB ManifestArray<shape=(5, 17999...
            dt_1km_data       (time, lat, lon) int8 3GB ManifestArray<shape=(5, 17999...
            analysed_sst      (time, lat, lon) int16 6GB ManifestArray<shape=(5, 1799...
            analysis_error    (time, lat, lon) int16 6GB ManifestArray<shape=(5, 1799...
            sst_anomaly       (time, lat, lon) int16 6GB ManifestArray<shape=(5, 1799...
        Attributes: (12/42)
            Conventions:                CF-1.7
            title:                      Daily MUR SST, Final product

        >>> vds.virtualize.to_kerchunk("mur_combined.json", format="json")
        >>> vds = open_virtual_mfdataset(results, access="https", concat_dim="time", coords='minimal', compat='override', combine_attrs="drop_conflicts")
        >>> vds
        <xarray.Dataset> Size: 143GB
        Dimensions:           (time: 5, lat: 17999, lon: 36000)
        Coordinates:
        * lat               (lat) float32 72kB -89.99 -89.98 -89.97 ... 89.98 89.99
        * lon               (lon) float32 144kB -180.0 -180.0 -180.0 ... 180.0 180.0
        * time              (time) datetime64[ns] 40B 2024-01-01T09:00:00 ... 2024-...
        Data variables:
            analysed_sst      (time, lat, lon) float64 26GB dask.array<chunksize=(1, 3600, 7200), meta=np.ndarray>
            analysis_error    (time, lat, lon) float64 26GB dask.array<chunksize=(1, 3600, 7200), meta=np.ndarray>
            dt_1km_data       (time, lat, lon) timedelta64[ns] 26GB dask.array<chunksize=(1, 4500, 9000), meta=np.ndarray>
            mask              (time, lat, lon) float32 13GB dask.array<chunksize=(1, 4500, 9000), meta=np.ndarray>
            sea_ice_fraction  (time, lat, lon) float64 26GB dask.array<chunksize=(1, 4500, 9000), meta=np.ndarray>
            sst_anomaly       (time, lat, lon) float64 26GB dask.array<chunksize=(1, 3600, 7200), meta=np.ndarray>
        Attributes: (12/42)
            Conventions:                CF-1.7
            title:                      Daily MUR SST, Final product
        ```
    """
    try:
        import virtualizarr as vz
        import xarray as xr
        from obstore.auth.earthdata import NasaEarthdataCredentialProvider
        from obstore.store import HTTPStore, S3Store
        from virtualizarr.registry import ObjectStoreRegistry
    except ImportError as e:
        raise ImportError(
            "`earthaccess.open_virtual_dataset` requires `pip install earthaccess[virtualizarr]`"
        ) from e

    if len(granules) == 0:
        raise ValueError("No granules provided. At least one granule is required.")

    # Get collection ID for reference file naming
    collection_id = granules[0]["meta"]["collection-concept-id"]

    canonical_access = normalize_access(access)
    parsed_url = urlparse(granules[0].data_links(access=canonical_access)[0])
    fs = earthaccess.get_fsspec_https_session()

    if canonical_access == "s3":
        credentials_endpoint, region = get_granule_credentials_endpoint_and_region(
            granules[0]
        )
        bucket = parsed_url.netloc

        if load:
            fs = earthaccess.get_s3_filesystem(endpoint=credentials_endpoint)

        s3_store = S3Store(
            bucket=bucket,
            region=region,
            credential_provider=NasaEarthdataCredentialProvider(credentials_endpoint),
            virtual_hosted_style_request=False,
            client_options={"allow_http": True},
        )
        obstore_registry = ObjectStoreRegistry({f"s3://{bucket}": s3_store})
    else:
        domain = parsed_url.netloc
        # Get auth token, with fallback for when auth is not initialized
        auth_token = None
        if earthaccess.__auth__ is not None and earthaccess.__auth__.token is not None:
            auth_token = earthaccess.__auth__.token.get("access_token")

        if auth_token is None:
            raise ValueError(
                "Authentication required. Please run earthaccess.login() first."
            )

        http_store = HTTPStore.from_url(
            f"https://{domain}",
            client_options={
                "default_headers": {
                    "Authorization": f"Bearer {auth_token}",
                },
            },
        )
        obstore_registry = ObjectStoreRegistry({f"https://{domain}": http_store})

    # Get the parser instance and appropriate URLs
    parser_instance = _get_parser(parser, group=group)
    granule_urls = _get_urls_for_parser(granules, parser, canonical_access)

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Numcodecs codecs*",
            category=UserWarning,
        )
        vmfdataset = vz.open_virtual_mfdataset(
            urls=granule_urls,
            registry=obstore_registry,
            parser=parser_instance,
            preprocess=preprocess,
            parallel=parallel,
            combine="nested",
            **xr_combine_nested_kwargs,
        )

    if load:
        if reference_dir is None:
            ref_dir = Path(tempfile.gettempdir())
        else:
            ref_dir = Path(reference_dir)
            ref_dir.mkdir(exist_ok=True, parents=True)  # type: ignore

        if group is None or group == "/":
            group_name = "root"
        else:
            group_name = group.replace("/", "_").replace(" ", "_").lstrip("_")

        ref_ = ref_dir / Path(f"{collection_id}-{group_name}.{reference_format}")
        # We still need the round trip because: https://github.com/zarr-developers/VirtualiZarr/issues/360
        vmfdataset.virtualize.to_kerchunk(str(ref_), format=reference_format)

        storage_options = {
            "remote_protocol": canonical_access,
            "remote_options": fs.storage_options,
        }
        vds = xr.open_dataset(
            str(ref_),
            engine="kerchunk",
            storage_options=storage_options,
        )
        return vds

    return vmfdataset


def open_virtual_dataset(
    granule: earthaccess.DataGranule,
    group: str | None = None,
    access: AccessType | None = "https",
    load: bool = True,
    reference_dir: str | None = None,
    reference_format: Literal["json", "parquet"] = "json",
    parser: ParserType = "DMRPPParser",
) -> xr.Dataset:
    """Open a granule as a single virtual xarray Dataset.

    Uses VirtualiZarr to create a virtual xarray dataset with ManifestArrays. This
    virtual dataset can be used to create zarr reference files. See
    [https://virtualizarr.readthedocs.io](https://virtualizarr.readthedocs.io) for
    more information on virtual xarray datasets.

    > WARNING: This feature is experimental and may change in the future.

    Parameters:
        granule:
            The granule to open
        group:
            Path to the netCDF4 group in the given file to open. If None, the root
            group will be opened. If the file does not have groups, this parameter
            is ignored.
        access:
            The access method to use. One of "https" (default) or "s3". Legacy
            "direct"/"indirect" values are also accepted.
        load:
            If load is True, earthaccess will serialize the virtual references in
            order to use lazy indexing on the resulting xarray virtual ds. If False,
            returns the raw virtual dataset with ManifestArrays.
        reference_dir:
            Directory to store kerchunk references. If None, a temporary directory
            will be created and deleted after use. Only used when load=True.
        reference_format:
            When load is True, earthaccess will serialize the references using this
            format, json (default) or parquet.
        parser:
            The VirtualiZarr parser to use for reading files. Supported string values
            are "DMRPPParser" (default), "HDFParser", and "NetCDF3Parser". You can also
            pass a VirtualiZarr parser instance directly. DMRPPParser reads from DMR++
            sidecar files (appends .dmrpp to data URLs), while HDFParser and NetCDF3Parser
            read from the actual data files.

    Returns:
        xarray.Dataset

    Examples:
        Using the default DMRPPParser:
        ```python
        >>> results = earthaccess.search_data(count=2, temporal=("2023"), short_name="SWOT_L2_LR_SSH_Expert_2.0")
        >>> vds = earthaccess.open_virtual_dataset(results[0], access="https")
        >>> vds
        <xarray.Dataset> Size: 149MB
        ...
        ```

        Getting the raw virtual dataset without loading (for custom serialization):
        ```python
        >>> vds = earthaccess.open_virtual_dataset(results[0], access="https", load=False)
        >>> vds.virtualize.to_kerchunk("my_refs.json", format="json")
        ```

        Using the HDFParser for datasets without DMR++ files:
        ```python
        >>> vds = earthaccess.open_virtual_dataset(results[0], parser="HDFParser")
        ```
    """
    return open_virtual_mfdataset(
        granules=[granule],
        group=group,
        access=access,
        parallel=False,
        preprocess=None,
        load=load,
        reference_dir=reference_dir,
        reference_format=reference_format,
        parser=parser,
    )


def get_granule_credentials_endpoint_and_region(
    granule: earthaccess.DataGranule,
) -> tuple[str, str]:
    """Retrieve credentials endpoint for direct access granule link.

    Parameters:
        granule:
            The first granule being included in the virtual dataset.

    Returns:
        credentials_endpoint:
            The S3 credentials endpoint. If this information is in the UMM-G record, then it is used from there. If not, a query for the collection is performed and the information is taken from the UMM-C record.
        region:
            Region for the data. Defaults to us-west-2. If the credentials endpoint is retrieved from the UMM-C record for the collection, the Region information is also used from UMM-C.

    """
    credentials_endpoint = granule.get_s3_credentials_endpoint()
    region = "us-west-2"

    if credentials_endpoint is None:
        collection_results = earthaccess.search_datasets(
            count=1,
            concept_id=granule["meta"]["collection-concept-id"],
        )
        collection_s3_bucket = collection_results[0].s3_bucket
        credentials_endpoint = collection_s3_bucket.get("S3CredentialsAPIEndpoint")
        region = collection_s3_bucket.get("Region", "us-west-2")

    if credentials_endpoint is None:
        raise ValueError("The collection did not provide an S3CredentialsAPIEndpoint")

    return credentials_endpoint, region
