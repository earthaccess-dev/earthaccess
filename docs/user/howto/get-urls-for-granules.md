# Get URLs for Granules

If we already have a workflow to download data, we can still use `earthaccess.search_data` to search for data granules and then get URLs for these data granules from the search results. This could be the case if our current workflow uses a different language and we only need the links as input.

`earthaccess.search_data` returns a list of results that match our search criteria.  We use the `data_links` method for each result in this list to get the URL for the data.  The URL we want depends on if we are [_in-region_](../../user/reference/glossary/cloud-glossary.md#in-region-vs-out-of-region) or [_out-of-region_](../../user/reference/glossary/cloud-glossary.md#in-region-vs-out-of-region).

If we are not working in AWS `us-region-2`, then we are _out-of-region_ and need to set `access="external"`.

```python
data_links = [granule.data_links(access="external") for granule in results]
```

If we are working in AWS `us-region-2`, then we are _in-region_ and need to set `access="direct"`.

```python
data_links = [granule.data_links(access="direct") for granule in results]
```
