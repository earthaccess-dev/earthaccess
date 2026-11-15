"""File wrapper module for earthaccess store.

Contains the EarthAccessFile proxy class that wraps fsspec file objects
and associates them with their source granule metadata.
"""

from pickle import dumps, loads
from typing import Any, Dict, List, Mapping, Optional, Union

import fsspec

import earthaccess

from ..auth import Auth
from ..search import DataGranule
from .parallel import get_executor

__all__ = [
    "EarthAccessFile",
    "make_instance",
    "optimal_block_size",
    "is_interactive",
    "open_files",
    "get_url_granule_mapping",
]


def is_interactive() -> bool:
    """Detect if earthaccess is being used in an interactive session.

    Interactive sessions include Jupyter Notebooks, IPython REPL, and default Python REPL.

    Returns:
        bool: True if in an interactive session, False otherwise.
    """
    try:
        from IPython import get_ipython  # type: ignore

        # IPython Notebook or REPL:
        if get_ipython() is not None:
            return True
    except ImportError:
        pass

    import sys

    # Python REPL
    return hasattr(sys, "ps1")


class EarthAccessFile:
    """Handle for a file-like object pointing to an on-prem or Earthdata Cloud granule."""

    def __init__(
        self, f: fsspec.spec.AbstractBufferedFile, granule: DataGranule
    ) -> None:
        """EarthAccessFile connects an Earthdata search result with an open file-like object.

        The class implements custom serialization, but otherwise passes all attribute and method calls
        directly to the file-like object given during initialization. An instance of
        this class can be treated like that file-like object itself.

        Note that `type()` applied to an instance of this class is expected to disagree with
        the `__class__` attribute on the instance.

        Parameters:
            f: a file-like object
            granule: a granule search result
        """
        self.f = f
        self.granule = granule

    def __getattribute__(self, name: str) -> Any:
        # use super().__getattribute__ to avoid infinite recursion
        if (name in EarthAccessFile.__dict__) or (name in self.__dict__):
            # accessing our attributes
            return super().__getattribute__(name)
        else:
            # access proxied attributes
            proxy = super().__getattribute__("f")
            return getattr(proxy, name)

    def __reduce_ex__(self, protocol: Any) -> Any:
        return make_instance, (
            self.__class__,
            self.granule,
            earthaccess.__auth__,
            dumps(self.f),
        )

    def __repr__(self) -> str:
        return repr(self.f)


def optimal_block_size(file_size: int) -> int:
    """Determine the optimal block size based on file size.

    Note: we could even be smarter if we know the chunk sizes of the variables
    we need to cache, e.g. using the `dmrpp` file and the `wellknownparts` cache type.

    Uses `blockcache` for all files with block sizes adjusted by file size:

    - <100MB: 4MB
    - >100MB: 4-16MB

    Parameters:
        file_size (int): Size of the file in bytes.

    Returns:
        block_size (int): Optimal block size in bytes.
    """
    if file_size < 100 * 1024 * 1024:
        block_size = 4 * 1024 * 1024
    elif 100 * 1024 * 1024 <= file_size < 1024 * 1024 * 1024:
        block_size = 8 * 1024 * 1024
    else:
        block_size = 16 * 1024 * 1024

    return block_size


def open_files(
    url_mapping: Mapping[str, Union[DataGranule, None]],
    fs: fsspec.AbstractFileSystem,
    *,
    max_workers: Optional[int] = None,
    show_progress: bool = True,
    open_kwargs: Optional[Dict[str, Any]] = None,
    parallel: Union[str, bool, None] = None,
) -> List["EarthAccessFile"]:
    """Open multiple files from URLs and wrap them in EarthAccessFile objects.

    Parameters:
        url_mapping: Mapping of URLs to their associated granule metadata.
        fs: The filesystem to use for opening files.
        max_workers: Maximum number of worker threads.
        show_progress: Whether to show a progress bar.
        open_kwargs: Additional keyword arguments to pass to fs.open().
        parallel: Parallel execution strategy.

    Returns:
        List of EarthAccessFile objects.
    """

    def multi_thread_open(data: tuple[str, Optional[DataGranule]]) -> EarthAccessFile:
        url, granule = data
        f_size = fs.info(url)["size"]
        default_cache_type = "background"  # block cache with background fetching
        default_block_size = optimal_block_size(f_size)

        open_kw = (open_kwargs or {}).copy()

        open_kw.setdefault("cache_type", default_cache_type)
        open_kw.setdefault("block_size", default_block_size)

        f = fs.open(url, **open_kw)
        return EarthAccessFile(f, granule)  # type: ignore

    # Get executor based on parallel parameter
    executor = get_executor(
        parallel, max_workers=max_workers, show_progress=show_progress
    )

    # Execute using the executor
    try:
        results = list(executor.map(multi_thread_open, url_mapping.items()))
        return results
    finally:
        # Ensure executor is properly shut down
        executor.shutdown(wait=True)


def make_instance(
    cls: Any, granule: DataGranule, auth: Auth, data: Any
) -> EarthAccessFile:
    """Deserialize an EarthAccessFile from pickle data.

    This function is used by pickle to recreate EarthAccessFile instances.
    It handles re-authentication if needed.

    Parameters:
        cls: The EarthAccessFile class.
        granule: The granule metadata.
        auth: The Auth object for authentication.
        data: The pickled file object data.

    Returns:
        A new EarthAccessFile instance.
    """
    # Attempt to re-authenticate
    current_auth = getattr(earthaccess, "__auth__", None)
    if current_auth is None or not current_auth.authenticated:
        setattr(earthaccess, "__auth__", auth)
        earthaccess.login()

    # Access mode is explicit, so the serialized file object is returned as-is.
    return EarthAccessFile(loads(data), granule)


def get_url_granule_mapping(
    granules: List[DataGranule], access: str
) -> Mapping[str, DataGranule]:
    """Construct a mapping between file urls and granules.

    Parameters:
        granules: List of granule objects.
        access: Access type ('s3' or 'https'; legacy aliases are accepted).

    Returns:
        Mapping of URLs to their source granule objects.
    """
    url_mapping = {}
    for granule in granules:
        for url in granule.data_links(access=access):
            url_mapping[url] = granule
    return url_mapping


# Backward compatibility aliases (private names used by store.py)
_is_interactive = is_interactive
_optimal_fsspec_block_size = optimal_block_size
_open_files = open_files
_get_url_granule_mapping = get_url_granule_mapping
