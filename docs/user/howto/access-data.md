### **Accessing the data**

**Option 1: Using the data links**

If we already have a workflow in place for downloading our data, we can use *earthaccess* as a search-only library and get HTTP links from our query results. This could be the case if our current workflow uses a different language and we only need the links as input.

```python

# By default, data_links() returns HTTPS links, which work from anywhere.
data_links = [granule.data_links() for granule in results]

# If the data are cloud-hosted, request direct S3 links explicitly. Direct
# access only works from AWS us-west-2 (or wherever the bucket is accessible).
s3_links = [granule.data_links(access="s3") for granule in results]

# retrieve OPeNDAP service URLs when the collection provides them
opendap_links = [
    link["URL"]
    for granule in results
    for link in granule.get("umm", {}).get("RelatedUrls", [])
    if link.get("Type") == "USE SERVICE API"
    and link.get("Subtype") == "OPENDAP DATA"
]

```

Not every collection provides OPeNDAP access. If the search results do not
include OPeNDAP service metadata, `opendap_links` will be an empty list.

> Note: *earthaccess* can get S3 credentials for us, or authenticated HTTP sessions in case we want to use them with a different library.

**Option 2: Download data to a local folder**

This option is practical if you have the necessary space available on disk. The *earthaccess* library will print out the approximate size of the download and its progress.
```python
files = earthaccess.download(results, "./local_folder")

```

**Option 3: Stream data directly to xarray**

By default, `earthaccess.open` uses HTTPS, which works from anywhere. If you are
in the same Amazon Web Services (AWS) region as the data (us-west-2) and want
direct S3 access, pass `access="s3"`.

```python
import xarray as xr

files = earthaccess.open(results)  # HTTPS (default)
# files = earthaccess.open(results, access="s3")  # direct S3

ds = xr.open_mfdataset(files)

```

And that's it in just one line of code! This same piece of code will also work for data that are not hosted in the cloud, i.e. located at NASA storage centers.


> More examples coming soon!
