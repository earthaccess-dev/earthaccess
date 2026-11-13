# Search for datasets using keywords

How do we search for datasets using keywords?  `earthaccess.search_datasets` has a `keyword` parameter that allows us to search for datasets that contain a set of keywords or for datasets that contain a short phrase.

## Search for datasets using a set of keywords

The example below searches for datasets that contain the words in a set of fields.  A list of these fields can be found [here](https://cmr.earthdata.nasa.gov/search/site/docs/search/api.html#c-keyword).

The example below searches for datasets that contain **all* of the words "sea", "ice" and "arctic".

```python

results = earthaccess.search_datasets(
    keyword="arctic sea ice"
    )
```

The keyword search is case insensitive and also supports wildcards `*` and `?`.

## Search for datasets using a key phrase

The example below searches for datasets that contain the phrase "arctic sea ice".

```python

results = earthaccess.search_datasets(
    keyword='"arctic sea ice"'
    )
```

A phrase is distinguished from a list of words by encapsulating the phrase in double-quotes `"`.  This means that we have to encapsulate this double-quoted string in single quotes.

See [Search](../../user/explanation/search.md#search-for-datasets-using-search_datasets) explanation page for more details.
