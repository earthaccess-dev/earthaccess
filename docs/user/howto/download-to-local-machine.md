# Download data to a local machine

Once we have found data using `earthaccess.search_data` we can download that data to our local machine.  Our local machine may be a laptop, workstation, storage attached to a high performance computing (HPC) cluster, or cloud computing resource.  We use `earthaccess.download` to do this.

`earthaccess.search_data` returns a list of granules matching our search.  Here, we assign that list to a variable named `results`.  `earthaccess.download` accepts this `results` variable directly.

!!! important

    We will need to authenticate your NASA Earthdata Login account using
    `earthaccess.login` to download data

```python
files = earthaccess.download(results, local_path="./local_folder")
```

By default, data are downloaded to the current working directory.  We can select an alternative dowload folder with the `local_path` keyword argument.  

See the [Download](user/explanation/access#downloading-data) explanation page for more details