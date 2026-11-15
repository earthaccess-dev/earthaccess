"""Search package for NASA CMR queries and results.

This package provides classes for searching NASA's Common Metadata Repository (CMR)
and working with search results.
"""

# Re-export base query classes from cmr package for backward compatibility
# These are used internally by DataCollectionsQuery and DataGranulesQuery
from cmr import CollectionQuery as CmrCollectionQuery, GranuleQuery as CmrGranuleQuery
from earthaccess.search.persistence import load_search, save_search
from earthaccess.search.queries import DataCollectionsQuery, DataGranulesQuery

# New query builder classes (earthaccess-native, no cmr dependency)
from earthaccess.search.query import (
    BoundingBox,
    CollectionQuery,
    DateRange,
    GranuleQuery,
    Point,
    Polygon,
    QueryBase,
    StacItemQuery,
    ValidationError,
    ValidationResult,
)
from earthaccess.search.results import (
    CollectionResults,
    CustomDict,
    DataCollection,
    DataGranule,
    GranuleFilter,
    GranuleResults,
    SearchResults,
)
from earthaccess.search.services import DataServicesQuery

__all__ = [
    # New query builder classes (earthaccess-native)
    "GranuleQuery",
    "CollectionQuery",
    "QueryBase",
    "StacItemQuery",
    "BoundingBox",
    "DateRange",
    "Point",
    "Polygon",
    "ValidationError",
    "ValidationResult",
    # Legacy query classes (cmr-based, for internal use)
    "CmrCollectionQuery",
    "CmrGranuleQuery",
    # Query executor classes
    "DataCollectionsQuery",
    "DataGranulesQuery",
    # Result classes
    "CustomDict",
    "DataCollection",
    "DataGranule",
    "GranuleFilter",
    "SearchResults",
    "GranuleResults",
    "CollectionResults",
    # Service class
    "DataServicesQuery",
    # Persistence
    "save_search",
    "load_search",
]
