"""Virtual dataset utilities for cloud-native access.

This package provides tools for working with virtual datasets using
VirtualiZarr parsers (DMR++, HDF5, NetCDF3) and Kerchunk references.
"""

from earthaccess.virtual.core import open_virtual, virtualize, write_virtual
from earthaccess.virtual.dmrpp import (
    SUPPORTED_PARSERS,
    get_granule_credentials_endpoint_and_region,
    open_virtual_dataset,
    open_virtual_mfdataset,
)

__all__ = [
    "open_virtual_dataset",
    "open_virtual_mfdataset",
    "get_granule_credentials_endpoint_and_region",
    "open_virtual",
    "virtualize",
    "write_virtual",
    "SUPPORTED_PARSERS",
]
