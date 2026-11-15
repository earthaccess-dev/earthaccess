"""earthaccess: A Python library for NASA Earthdata.

earthaccess simplifies the discovery and access of NASA Earth science data
from the cloud and on-premises archives.

Quick Start:
    ```python
    import earthaccess

    # Authenticate with NASA Earthdata Login
    earthaccess.login()

    # Search for data
    granules = earthaccess.search_data(
        short_name="ATL06",
        temporal=("2023-01", "2023-02"),
        bounding_box=(-180, -90, 180, 90),
    )

    # Download data
    files = earthaccess.download(granules, "./data")

    # Or stream data directly (no download)
    files = earthaccess.open(granules)
    ```

Key Features:
    - **Authentication**: Seamless login with NASA Earthdata Login
    - **Search**: Find datasets (collections) and data files (granules)
    - **Access**: Download or stream data from cloud or on-premises archives
    - **Cloud-native**: Direct S3 access when running in AWS us-west-2
    - **Virtual datasets**: Create virtual Zarr datasets using VirtualiZarr

Main Functions:
    - `login()`: Authenticate with Earthdata Login
    - `search_datasets()`: Search for collections/datasets
    - `search_data()`: Search for granules/files
    - `download()`: Download granules to local or cloud storage
    - `open()`: Stream granules as file-like objects
    - `open_virtual_dataset()`: Create virtual Zarr datasets

For more information, see https://earthaccess.readthedocs.io/
"""

import logging
import threading
from importlib.metadata import version
from typing import TYPE_CHECKING, Any, Optional

from . import plugins, testing
from .api import (
    auth_environ,
    collection_query,
    download,
    get_edl_token,
    get_fsspec_https_session,
    get_requests_https_session,
    get_s3_credentials,
    get_s3_filesystem,
    get_s3fs_session,
    granule_query,
    login,
    open,
    search_data,
    search_datasets,
    search_services,
    status,
)
from .auth import Auth, distribute_credentials
from .auth.system import PROD, UAT, get_proxy, set_proxy
from .plugins import list_plugins, list_services
from .search import (
    DataCollection,
    DataCollectionsQuery,
    DataGranule,
    DataGranulesQuery,
    DataServicesQuery,
    SearchResults,
    load_search,
    save_search,
)
from .search.query import (
    BoundingBox,
    CollectionQuery,
    DateRange,
    GranuleQuery,
    Point,
    Polygon,
)
from .store.core import Store
from .virtual import open_virtual, virtualize, write_virtual

logger = logging.getLogger(__name__)

__all__ = [
    # system.py
    "PROD",
    "UAT",
    "set_proxy",
    "get_proxy",
    # auth.py
    "Auth",
    "distribute_credentials",
    # search.py
    "DataCollection",
    "DataCollectionsQuery",
    "DataGranule",
    "DataGranulesQuery",
    "DataServicesQuery",
    "SearchResults",
    # query package (new query builders)
    "GranuleQuery",
    "CollectionQuery",
    "BoundingBox",
    "DateRange",
    "Point",
    "Polygon",
    # store.py
    "Store",
    # api.py
    "login",
    "status",
    "search_datasets",
    "search_data",
    "search_services",
    "get_requests_https_session",
    "get_fsspec_https_session",
    "get_s3fs_session",
    "get_s3_credentials",
    "get_s3_filesystem",
    "get_edl_token",
    "granule_query",
    "collection_query",
    "open",
    "download",
    "auth_environ",
    # virtual
    "open_virtual",
    "virtualize",
    "write_virtual",
    # persistence
    "save_search",
    "load_search",
    # plugins
    "plugins",
    "list_plugins",
    "list_services",
    "testing",
]

__version__ = version("earthaccess")

_auth = Auth()
_store: Optional[Store] = None
_lock = threading.Lock()

if TYPE_CHECKING:
    # Help type checkers resolve the __getattr__-provided attributes.
    __auth__: Auth
    __store__: Optional[Store]


def __getattr__(name: str) -> Any:
    """Module-level getattr to handle automatic authentication when accessing
    `earthaccess.__auth__` and `earthaccess.__store__`.

    Other unhandled attributes raise as `AttributeError` as expected.
    """
    global _auth, _store

    if name not in ["__auth__", "__store__"]:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    return _auth if name == "__auth__" else _store
