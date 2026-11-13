# Search for datasets or data using a polygon

We can use `earthaccess` to search for data within a region defined by a polygon.  The following example searches for ICESat-2 land ice heights (ATL06) over the Jakobshavn Isbrae in Greenland.

```python
polygon = [
    (-49.64860422604741, 69.23553485026147),
    (-49.667876114626296, 69.07309059285959),
    (-49.1722491331669, 69.03175841820749),
    (-47.53552489113113, 69.03872918462292),
    (-47.35616491854395, 69.22149993224824),
    (-48.1447695277283, 69.33507802083219),
    (-49.178671242118384, 69.29455117736225),
    (-49.64860422604741, 69.23553485026147),
]

results = earthaccess.search_data(
    short_name="ATL06",
    polygon=polygon,
    )
```

`polygon` is defined as a list of _tuples_ `(longitude, latitude)` that define the vertices of the polygon.  Polygon vertices must be in counter-clockwise order.

See [Search](../../user/explanation/search.md#search-for-datasets-or-data-using-a-region-of-interest) explanation page for more details and other spatial search methods.
