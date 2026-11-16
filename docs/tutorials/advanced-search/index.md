# Advanced Search tutorials

## Learning goals

Beyond the basic `search_data()` / `search_datasets()` keyword calls, the CMR
search API supports spatial, temporal, and metadata filters that let you
narrow millions of granules down to exactly what you need — and once you have
granules, `earthaccess` lets you select *which files inside a multi-file
granule* to work with (e.g. only a few HLS bands).

In this section you will learn to:

- Combine search filters: `bounding_box`, `temporal`, `cloud_cover`,
  `day_night_flag`, `orbit_number`, `version`, `point`, `polygon`, `line`.
- Search with class-based queries (`earthaccess.search.GranuleQuery` /
  `CollectionQuery`) for programmatic, reusable search objects.
- Filter the files inside a multi-file granule by glob pattern, role, or size
  (`AssetFilter`), so you only download the bands you want.
- Handle **restricted** (access-controlled) datasets with an authenticated
  session.

## Prerequisites

- An authenticated session for restricted data: `earthaccess.login()`.
- The [search API reference](../../api/index.md) for the full parameter list.

## Tutorials

- [Search for data using filters](../../user/howto/search-granules.md) — search
  granules within a dataset using spatial and temporal filters.
- [Filtering granules by file (band) patterns](filter-bands.ipynb) — select
  specific files inside multi-file granules (e.g. HLS bands `B02`–`B04`) with
  `AssetFilter` glob patterns, and download only those files.

## Try it next

- [Search services](../../user/howto/search-services.md) — discover subsetting /
  transformation services on a dataset.
- [How search works](../../user/explanation/search.md) — collections vs
  granules, and the search parameters shared between them.
- [Results Class tutorials](../results/index.md) — filtering and processing the
  results of a search.
