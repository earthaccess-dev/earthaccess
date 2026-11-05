# Stream data directly to xarray

Once we have found data using `earthaccess.search_data` we can _stream_ data directly to the tool we are using to work with that data.  Here, we use the Python `xarray` package as an example.

_Streaming_ data means that we are reading data directly into memory rather than copying the data file to local storage (e.g. our laptop).

We use `earthaccess.open` to _stream_ data.  This creates a virtual file system that can be passed to `xarray`.

!!! important

    You will need to authenticate your NASA Earthdata Login account using
    `earthaccess.login` to download data

```python
import xarray as xr

files = earthaccess.open(results)

ds = xr.open_mfdataset(files)
```

See the [Streaming Data]() tutorial for more details and a discussion of when it is useful to stream data and when it is not useful.