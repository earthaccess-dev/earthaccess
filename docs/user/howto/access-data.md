### **Accessing the data**

**Option 1: Using the data links**

If we already have a workflow in place for downloading our data, we can use *earthaccess* as a search-only library and get HTTP links from our query results. This could be the case if our current workflow uses a different language and we only need the links as input.

```python

# HTTPS is the default access method and works for on-prem and cloud-hosted data.
https_links = [granule.data_links(access="https") for granule in results]

# Cloud-hosted data also provide S3 links for direct access, e.g. from us-west-2.
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
# HTTPS is the default access method.
files = earthaccess.download(results, "./local_folder")

# In us-west-2, you can opt into direct S3 downloads.
files = earthaccess.download(results, "./local_folder", access="s3")

```

**Option 3: Stream data directly to xarray**

HTTPS access works from anywhere, including on-prem and cloud-hosted data.

```python
import xarray as xr

files = earthaccess.open(results)

ds = xr.open_mfdataset(files)

```

Direct S3 access works best if you are in the same Amazon Web Services (AWS) region as the data (us-west-2) and you are working with gridded (processing level 3 and above) datasets without nested or hierarchical data variables.

```python
files = earthaccess.open(results, access="s3")

ds = xr.open_mfdataset(files)

```


> More examples coming soon!
