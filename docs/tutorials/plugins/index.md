# Plugins

earthaccess plugins reach services that are not part of the core library.
Each plugin is a small, independently installable package that registers itself
with earthaccess through the `earthaccess.plugins` entry-point group and appears
under `ea.plugins` once installed.

<img src="./earthaccess-v2.png" width="80%">

```bash
# The plugins are not on PyPI yet; install them from GitHub.
pip install "earthaccess-plugin-harmony @ git+https://github.com/betolink/earthaccess-plugin-harmony"   # subset/transform with Harmony
pip install "earthaccess-plugin-pydap @ git+https://github.com/betolink/earthaccess-plugin-pydap"       # lazy OPeNDAP access
pip install "earthaccess-plugin-sliderule @ git+https://github.com/betolink/earthaccess-plugin-sliderule" # ICESat-2 / GEDI processing
pip install "earthaccess-plugin-vds @ git+https://github.com/betolink/earthaccess-plugin-vds"             # virtual icechunk / kerchunk stores
```

## The model

Every plugin follows the same flow: a request returns a `Job`, and the job's
`results()` block and return the plugin's output. Results can then be opened or
downloaded.

```python
import earthaccess as ea

earthaccess.login()
results = ea.search_data(short_name="ATL06", bounding_box=(-120, 40, -110, 45))

job = ea.plugins.harmony.request(results, variables=["h_li"])   # returns immediately
job.status()      # PENDING / RUNNING / PAUSED / SUCCEEDED / PARTIAL / FAILED / CANCELED
job.info()        # backend detail; never the request payload
job.results()     # blocks, returns the plugin's output
```

| Plugin | What it does | `results()` are |
| --- | --- | --- |
| [`harmony`](harmony.ipynb) | subset / reproject / reformat in the cloud | `list[Asset]` (files) |
| [`pydap`](pydap.ipynb) | OPeNDAP access as a request | OPeNDAP URLs (or native pydap datasets) |
| [`sliderule`](sliderule.ipynb) | on-demand ICESat-2 / GEDI processing | `GeoDataFrame` |
| [`vds`](vds.ipynb) | virtual stores: create/append icechunk or kerchunk-parquet | `list[Asset]` (store hrefs) |

See also [VDS in depth](vds-in-depth.ipynb): composing with Harmony output, pydap's OPeNDAP limit, `source=` merges, the manifest form, kerchunk versioning, and running on a Dask cluster.

## Discovering plugins

```python
ea.list_plugins()               # table of known/installed plugins
ea.list_plugins(details=True)   # + contract version, auth, output, needs
ea.plugins.harmony              # get a plugin instance (loads it on first use)
ea.plugins.get("harmony")       # programmatic lookup
ea.plugins.available()          # names of known/installed plugins
```

## Is this dataset supported?

Ask before submitting — cheap and, for most plugins, offline:

```python
ea.plugins.harmony.capabilities(results)      # Capabilities(ok=True/False/None, ...)
ea.plugins.harmony.capabilities("ATL06")      # by collection short name

results.list_services()         # every installed plugin at once (no name needed)
ea.list_services(results)       # the same, top-level
```

`Capabilities` reports the operations a collection supports (`subset_bbox`,
`reproject`, `lazy_read`, `process`, ...) and the output formats. Passing results
a plugin cannot handle fails early with `UnsupportedCollectionError`; pass
`check=False` to bypass it.

## Opening and downloading results

```python
dataset = ea.open(job)             # file assets, xarray results, etc.
files = ea.download(job, "./data") # asset jobs
```

- Harmony assets are files: `ea.open`/`ea.download` stream or fetch them.
- PyDAP returns OPeNDAP URLs (with a variable constraint): `ea.open(job)` gives
  them to you to pass to xarray (`engine="pydap"`), and `ea.download(job, path)`
  serializes the constrained data to local NetCDF.
- SlideRule returns a `GeoDataFrame`, already usable.

## Writing your own plugin

```bash
cookiecutter gh:betolink/earthaccess-plugin-spec --directory template
```

Subclass `PluginBase`, declare `Options` (a dataclass) and implement `_request()`.
Run the conformance suite (`from earthaccess.testing import run_conformance`) in
CI. See the [spec repository](https://github.com/betolink/earthaccess-plugin-spec)
for the full contract and the conformance suite.
