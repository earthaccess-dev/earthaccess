# STAC Query Builders

Build queries using STAC-native parameters instead of CMR parameters.

## Overview

earthaccess provides three query builder classes:

| Class | Source Format | Best For |
|-------|---------------|----------|
| `GranuleQuery` | CMR | Querying NASA data with CMR parameters |
| `CollectionQuery` | CMR | Searching for NASA datasets |
| `StacItemQuery` | STAC | Querying with STAC-native parameters |

This page covers `StacItemQuery`. For CMR-native queries, see:
- [Granule Queries](../granules/granules-query.md)
- [Collection Queries](../collections/collections-query.md)

---

## `StacItemQuery` - STAC-Native Queries

Build queries using STAC-native parameters and terminology.

**Import**:
```python
from earthaccess import StacItemQuery
```

### Constructor

Create a new STAC query:

```python
from earthaccess import StacItemQuery

# Start with no constraints
query = StacItemQuery()

# Or with initial constraints (kwargs)
query = StacItemQuery(
    collections="C1234567890-NSIDC",
    bbox=[-120, 35, -118, 37]
)
```

### Method Chaining API

All filter methods return `self`, enabling method chaining:

```python
query = (
    StacItemQuery()
    .collections("C1234-NSIDC", "C5678-PODAAC")
    .bbox([-120, 35, -118, 37])
    .datetime("2023-01-01T00:00:00Z", "2023-02-01T00:00:00Z")
)
```

### Core Methods

#### `.collections()`

Filter by STAC collection ID(s).

```python
query.collections("C1234-NSIDC")
query.collections("C1234-NSIDC", "C5678-PODAAC")  # Multiple
```

**STAC Equivalence**: `"collection"` field in Items

---

#### `.bbox()`

Filter by geographic bounding box.

```python
# [west, south, east, north]
query.bbox([-120, 35, -118, 37])
```

**Parameters**:
- Expects list of 4 floats: [min_lon, min_lat, max_lon, max_lat]
- Longitude range: [-180, 180]
- Latitude range: [-90, 90]

**STAC Equivalence**: `"bbox"` field in STAC search

---

#### `.datetime()`

Filter by date/time range.

```python
# Specific date
query.datetime("2023-01-15T00:00:00Z")

# Date range
query.datetime("2023-01-01T00:00:00Z", "2023-02-01T00:00:00Z")
```

**Parameters**:
- Start datetime (ISO 8601 string)
- End datetime (ISO 8601 string, optional)
- If only start is provided, searches from that date onwards

**STAC Equivalence**: `"datetime"` field in STAC search

---

#### `.query()`

Advanced CQL2 filter expressions.

```python
query.query("cloud_cover < 20")
query.query("platform = 'LANDSAT_8' AND cloud_cover < 15")
```

**STAC Equivalence**: `"filter"` parameter with CQL2 expressions

---

### Complete Example

```python
from earthaccess import StacItemQuery
import earthaccess

# Build query
query = (
    StacItemQuery()
    .collections("C1234567890-NSIDC")          # Collection ID
    .bbox([-120, 35, -118, 37])                 # Geographic bounds
    .datetime("2023-01-01T00:00:00Z",          # Start date
              "2023-02-01T00:00:00Z")           # End date
    .query("cloud_cover < 20")                  # Additional filter
)

# Validate query
validation = query.validate()
if not validation.is_valid():
    print("Validation errors:")
    for error in validation.errors:
        print(f"  - {error}")

# Execute query
earthaccess.login()
granules = earthaccess.search_data(query=query)

print(f"Found {len(granules)} granules")
for granule in granules:
    print(f"  - {granule.id()}")
```

---

## Query Validation

All queries can be validated before execution:

```python
query = StacItemQuery().collections("invalid-collection")

validation = query.validate()

if not validation.is_valid():
    print(f"Invalid query: {validation.errors}")
else:
    print("Query is valid")
```

**Validation Checks**:
- Required parameters are present
- Parameter types are correct
- Parameter values are within valid ranges
- Datetime ranges are valid (start <= end)

---

## Converting Query Output Formats

### To CMR Format

Convert STAC query to CMR format:

```python
query = StacItemQuery().collections("C1234-NSIDC")

cmr_params = query.to_cmr()
print(cmr_params)
# {'collection_concept_id': 'C1234-NSIDC', ...}
```

### To STAC Format

Convert to STAC search parameters:

```python
stac_params = query.to_stac()
print(stac_params)
# {'collections': ['C1234-NSIDC'], ...}
```

---

## Combining with CMR Queries

You can also use CMR-native queries and convert to STAC:

```python
from earthaccess import GranuleQuery

# Build CMR query
cmr_query = GranuleQuery().short_name("ATL06")

# Convert to STAC
stac_params = cmr_query.to_stac()
```

---

## Query Parameters Reference

### Supported STAC Parameters

| Parameter | Method | CMR Equivalent | Notes |
|-----------|--------|-----------------|-------|
| Collection ID | `.collections()` | `collection_concept_id` | STAC collection ID |
| Bounding box | `.bbox()` | `bounding_box` | Geographic extent |
| Datetime | `.datetime()` | `temporal` | ISO 8601 format |
| CQL2 Filter | `.query()` | N/A | Advanced filtering |

### Parameters NOT in STAC (Use CMR Query Instead)

Some NASA-specific parameters don't have STAC equivalents:
- Short name (use collection ID instead)
- Version ID
- Provider ID
- Data center
- Instrument
- Platform
- Processing level

For these, use `GranuleQuery` instead:

```python
from earthaccess import GranuleQuery

# Use CMR parameters
query = GranuleQuery().short_name("ATL06").processing_level("2")
```

---

## Error Handling

```python
from earthaccess import StacItemQuery

try:
    query = StacItemQuery().collections("INVALID")
    validation = query.validate()
    if not validation.is_valid():
        raise ValueError(f"Invalid query: {validation.errors}")

    results = earthaccess.search_data(query=query)
except ValueError as e:
    print(f"Error: {e}")
```

---

## Relationship to CMR and STAC

**STAC Query → CMR**:
- `StacItemQuery` translates STAC parameters to CMR parameters
- Executes via earthaccess search_data() which queries CMR
- Returns NASA data (CMR records)

**STAC Query → STAC**:
- Can also be used to query external STAC catalogs
- Future feature for multi-catalog search

```
StacItemQuery
    ↓
to_cmr() → search_data() → CMR → NASA datasets
    ↓
to_stac() → [future] external STAC catalogs
```

---

## Performance Notes

- Queries are lazy (not executed until `search_data()` is called)
- Query validation is fast (milliseconds)
- Complex queries with many parameters may take longer to execute
- Datetime filters significantly improve query performance

---

## See Also

- [GranuleQuery (CMR)](../granules/granules-query.md) - CMR-native queries
- [CollectionQuery (CMR)](../collections/collections-query.md) - CMR collection queries
- [STAC Specification](https://stacspec.org/) - Official STAC spec
- [STAC Search API](https://github.com/stac-api/stac-api-spec) - STAC API specification
