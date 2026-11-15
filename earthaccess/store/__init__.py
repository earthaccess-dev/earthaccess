"""Data access and storage package for NASA Earthdata.

This package provides internal components for data access operations.
Users should NOT import from this package directly - use the top-level
earthaccess API instead:

    >>> import earthaccess
    >>> earthaccess.login()
    >>> granules = earthaccess.search_data(short_name="ATL06", count=5)
    >>> files = earthaccess.open(granules)  # Stream granules
    >>> paths = earthaccess.download(granules, "./data")  # Download granules

Internal Modules:
    store: Store class (internal, used by earthaccess.open/download)
    file_wrapper: EarthAccessFile class and file handling utilities
    download: Download operations for HTTP and S3
    assets: Asset model and filtering
    daac: DAAC mappings and endpoints
    parallel: Executor abstraction for parallel operations
    filesystems: FileSystem factory for S3/HTTPS access
    streaming: Streaming utilities for large result sets
    target: Target filesystem abstraction for downloads

Note:
    The Store class is an internal implementation detail. It is instantiated
    automatically by earthaccess when needed. Users should not create Store
    instances directly.
"""

from typing import Any

# =============================================================================
# EAGER IMPORTS - Modules with NO auth dependencies (safe to import early)
# =============================================================================
# Export assets (no auth dependency)
from earthaccess.store.assets import Asset, AssetFilter, filter_assets

# Export DAAC utilities (no auth dependency)
from earthaccess.store.daac import (
    DAAC_TEST_URLS,
    DAACS,
    find_provider,
    find_provider_by_shortname,
)

# Export filesystem factory (depends on auth.credentials but not auth.auth)
from earthaccess.store.filesystems import (
    DefaultFileSystemFactory,
    FileSystemFactory,
    MockFileSystemFactory,
)

# Export parallel execution (no auth dependency)
from earthaccess.store.parallel import (
    DaskDelayedExecutor,
    Executor,
    LithopsEagerFunctionExecutor,
    SerialExecutor,
    ThreadPoolExecutorWrapper,
    execute_with_credentials,
    get_executor,
)

# Export target filesystem (no auth dependency)
from earthaccess.store.target import (
    FsspecFilesystem,
    LocalFilesystem,
    TargetFilesystem,
    TargetLocation,
)

# =============================================================================
# LAZY IMPORTS - Modules that depend on auth (loaded on first access)
# =============================================================================

# These are loaded lazily to avoid circular imports with earthaccess.auth
_lazy_imports = {
    # download module
    "DEFAULT_CHUNK_SIZE": "earthaccess.store.download",
    "clone_session": "earthaccess.store.download",
    "download_cloud_file": "earthaccess.store.download",
    "download_cloud_granules": "earthaccess.store.download",
    "download_file": "earthaccess.store.download",
    "download_granules": "earthaccess.store.download",
    # file_wrapper module
    "EarthAccessFile": "earthaccess.store.file_wrapper",
    "get_url_granule_mapping": "earthaccess.store.file_wrapper",
    "is_interactive": "earthaccess.store.file_wrapper",
    "make_instance": "earthaccess.store.file_wrapper",
    "open_files": "earthaccess.store.file_wrapper",
    "optimal_block_size": "earthaccess.store.file_wrapper",
    # store module
    "Store": "earthaccess.store.core",
    # streaming module
    "StreamingAuthContext": "earthaccess.store.streaming",
    "StreamingExecutor": "earthaccess.store.streaming",
    # distributed module (pickleable contexts for Dask, Ray, etc.)
    "DistributedWorkerContext": "earthaccess.store.distributed",
    "DistributedStreamingIterator": "earthaccess.store.distributed",
    "process_granule_in_worker": "earthaccess.store.distributed",
    # Backward compatibility aliases from distributed module
    "WorkerContext": "earthaccess.store.distributed",
    "StreamingIterator": "earthaccess.store.distributed",
}

# Special case: StreamingAuthContext is actually AuthContext in the module
_lazy_renames = {
    "StreamingAuthContext": "AuthContext",
}

# Cache for lazily loaded attributes
_lazy_cache: dict[str, Any] = {}


def __getattr__(name: str) -> Any:
    """Lazy import handler for attributes that depend on auth."""
    if name in _lazy_cache:
        return _lazy_cache[name]

    if name in _lazy_imports:
        import importlib

        module_path = _lazy_imports[name]
        module = importlib.import_module(module_path)

        # Handle renamed exports
        attr_name = _lazy_renames.get(name, name)
        attr = getattr(module, attr_name)

        _lazy_cache[name] = attr
        return attr

    # Handle backward compatibility aliases
    if name == "_is_interactive":
        return __getattr__("is_interactive")
    if name == "_optimal_fsspec_block_size":
        return __getattr__("optimal_block_size")
    if name == "_open_files":
        return __getattr__("open_files")
    if name == "_get_url_granule_mapping":
        return __getattr__("get_url_granule_mapping")

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Legacy exports
    "Store",
    # Assets
    "Asset",
    "AssetFilter",
    "filter_assets",
    # DAAC
    "DAACS",
    "DAAC_TEST_URLS",
    "find_provider",
    "find_provider_by_shortname",
    # File wrapper exports
    "EarthAccessFile",
    "make_instance",
    "optimal_block_size",
    "is_interactive",
    "open_files",
    "get_url_granule_mapping",
    # Download exports
    "download_file",
    "download_cloud_file",
    "download_granules",
    "download_cloud_granules",
    "clone_session",
    "DEFAULT_CHUNK_SIZE",
    # Filesystem factory
    "FileSystemFactory",
    "DefaultFileSystemFactory",
    "MockFileSystemFactory",
    # Parallel execution
    "Executor",
    "SerialExecutor",
    "ThreadPoolExecutorWrapper",
    "DaskDelayedExecutor",
    "LithopsEagerFunctionExecutor",
    "get_executor",
    "execute_with_credentials",
    # Streaming
    "StreamingExecutor",
    "StreamingAuthContext",
    # Distributed execution (Dask, Ray, etc.)
    "DistributedWorkerContext",
    "DistributedStreamingIterator",
    "process_granule_in_worker",
    # Backward compatibility aliases
    "WorkerContext",
    "StreamingIterator",
    # Target filesystem
    "TargetFilesystem",
    "LocalFilesystem",
    "FsspecFilesystem",
    "TargetLocation",
    # Backward compatibility (private names)
    "_is_interactive",
    "_optimal_fsspec_block_size",
    "_open_files",
    "_get_url_granule_mapping",
]
