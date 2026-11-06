# Get OPeNDAP URLs

Some NASA data collections support access using OPeNDAP, the Open-source Project for a Network Data Access Protocol.  If a collection supports access through OPeNDAP, granule metdata returned by `earthaccess.search_data` contains an OPeNDAP url. There is no built-in method to get these urls but the code below will return an OPeNDAP url if one exists.

```python
from itertools import chain

def get_opendap_link(granule) -> list[str]:
    """Returns OPeNDAP service url if exists, otherwise returns
    empty list.
    """
    return [
        link["URL"]
        for link in granule.get("umm").get("RelatedUrls", [])
        if link.get("Type") == "USE SERVICE API"
        and link.get("Subtype") == "OPENDAP DATA"
    ]

opendap_link = list(chain.from_iterable(
    [get_opendap_link(granule) for granule in results]
    )
```

If the collection does not support the OPeNDAP service, the above code returns an empty list.    