# Search datasets by instrument

If we know the name of the instrument but not the dataset we want, we can use the `instrument` keyword to find datasets for a particular instrument.  For example, the ATLAS instrument on-board ICESat-2 or the MODIS instrument on the Terra and Aqua satellites.

```python
import earthaccess

results = earthaccess.search_datasets(instrument="ATLAS")
```

Using `instrument="ATLAS"` returns datasets that match "ATLAS".

See [Search](../../user/explanation/search.md) for more details.
