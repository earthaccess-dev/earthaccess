from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

# A VirtualiZarr parser: either a canonical class-name string or a pre-built
# parser instance.  The strings are the exact class names exported from
# ``virtualizarr.parsers`` so that callers can pass them directly without
# importing VirtualiZarr themselves.
ParserType = (
    Literal[
        "DMRPPParser",
        "HDFParser",
        "NetCDF3Parser",
        "KerchunkJSONParser",
        "KerchunkParquetParser",
    ]
    | Any
)

ParallelType = Literal["dask", "lithops", False]
ReferenceFormatType = Literal["json", "parquet"]

# Mirrors virtualizarr.open_virtual_mfdataset's ``combine`` parameter.
CombineType = Literal["nested", "by_coords"]

# Mirrors xarray.core.types.JoinOptions
JoinType = Literal["outer", "inner", "left", "right", "exact", "override"]

# Mirrors xarray.core.types.CompatOptions
CompatType = Literal[
    "identical",
    "equals",
    "broadcast_equals",
    "no_conflicts",
    "override",
    "minimal",
]

# Mirrors xarray.core.types.CombineAttrsOptions
CombineAttrsType = (
    Literal["drop", "identical", "no_conflicts", "drop_conflicts", "override"]
    | Callable[..., Any]
)

# Mirrors the data_vars parameter of xarray.combine_nested / open_mfdataset
DataVarsType = Literal["all", "minimal", "different"] | list[str]
