# Search for datasets or data using a time range

When searching for data, we can define a temporal (time) range using date strings formatted YYYY-MM-DD.

```python
import earthaccess

results = earthaccess.search_data(
    short_name="ATL03",
    temporal=('2025-04-01', '2025-04-04')
    )
```

Or, we can provide `datetime` objects.

```python
import earthaccess
import datetime as dt

results_dt = earthaccess.search_data(
    short_name='ATL03',
    temporal=(dt.datetime(2026, 4, 1), dt.datetime(2026, 4, 4))
)
```

These two methods return slightly different results, when only year, month and day are defined.
The date `'2026-04-04'` is resolved as `2026-04-04T23:59:59`.  Whereas, `dt.datetime(2026, 4, 4)` is resolved as `2026-04-04T00:00:00`.  Explicitly setting hour, minute and seconds returns the same number of granules for both temporal range searches.

See [Search](../../user/explanation/search.md#search-for-datasets-or-data-by-time-range) for more details.
