import copy
import html
import json
import pprint
import uuid
import warnings
from collections.abc import Iterable, Iterator, Mapping, Sequence
from functools import cache
from textwrap import dedent, indent
from typing import TYPE_CHECKING, Any, ClassVar, overload

import requests

import earthaccess

from ._access import AccessType, normalize_access

if TYPE_CHECKING:
    import geopandas as gpd
from .formatters import _repr_granule_html
from .services import DataServicesQuery


@cache
def _citation(*, doi: str, format_: str, language: str) -> str:
    response = requests.get(
        "https://citation.doi.org/format",
        params={"doi": doi, "style": format_, "lang": language},
    )
    response.raise_for_status()
    return response.text


def _geometry_to_geojson(geometry: dict[str, Any]) -> dict[str, object]:
    """Convert a UMM-G Geometry to a GeoJSON representation.

    It is assumed that the `dict` contains only a single key-value pair,
    and this property is the result of converting the value of that pair
    into an equivalent GeoJSON structure, depending on the key, as follows:

    |Key                   |Value converted to GeoJSON|
    |:---------------------|:-------------------------|
    |`"Lines"`             |`"MultiLineString"`       |
    |`"Points"`            |`"MultiPoint"`            |
    |`"BoundingRectangles"`|`"MultiPolygon"`          |
    |`"GPolygons"`         |`"MultiPolygon"`          |

    Parameters:
        geometry: A UMM-G Geometry dictionary.

    Returns:
        A GeoJSON representation of the UMM-G Geometry.

    Raises:
        ValueError: If the UMM-G Geometry is invalid or unsupported.
    """
    if "GPolygons" in geometry:
        return {
            "type": "MultiPolygon",
            "coordinates": [
                [
                    # Outer ring (counterclockwise)
                    [
                        [point["Longitude"], point["Latitude"]]
                        for point in poly["Boundary"]["Points"]
                    ],
                    # Inner rings (clockwise)
                    *(
                        [
                            [point["Longitude"], point["Latitude"]]
                            # In UMM-G, boundary points are always counterclockwise,
                            # but GeoJSON wants inner rings to be clockwise, so we
                            # must reverse the points in ExclusiveZone Boundaries.
                            for point in reversed(boundary["Points"])
                        ]
                        # In UMM-G, ExclusiveZone is optional.
                        for boundary in poly.get("ExclusiveZone", {}).get(
                            "Boundaries",
                            [],
                        )
                    ),
                ]
                for poly in geometry["GPolygons"]
            ],
        }

    if "BoundingRectangles" in geometry:
        return {
            "type": "MultiPolygon",
            "coordinates": [
                [
                    [
                        [
                            rect["WestBoundingCoordinate"],
                            rect["SouthBoundingCoordinate"],
                        ],
                        [
                            rect["EastBoundingCoordinate"],
                            rect["SouthBoundingCoordinate"],
                        ],
                        [
                            rect["EastBoundingCoordinate"],
                            rect["NorthBoundingCoordinate"],
                        ],
                        [
                            rect["WestBoundingCoordinate"],
                            rect["NorthBoundingCoordinate"],
                        ],
                        [
                            rect["WestBoundingCoordinate"],
                            rect["SouthBoundingCoordinate"],
                        ],
                    ],
                ]
                for rect in geometry["BoundingRectangles"]
            ],
        }

    if "Points" in geometry:
        return {
            "type": "MultiPoint",
            "coordinates": [
                [p["Longitude"], p["Latitude"]] for p in geometry["Points"]
            ],
        }

    if "Lines" in geometry:
        return {
            "type": "MultiLineString",
            "coordinates": [
                [[p["Longitude"], p["Latitude"]] for p in line["Points"]]
                for line in geometry["Lines"]
            ],
        }

    msg = f"Invalid Geometry in granule's horizontal spatial extent: {geometry}"
    raise ValueError(msg)


class CustomDict(dict):
    _basic_umm_fields_: ClassVar[list] = []
    _basic_meta_fields_: ClassVar[list] = []

    def __init__(
        self,
        collection: dict[str, Any],
        fields: list[str] | None = None,
        cloud_hosted: bool = False,  # noqa: FBT001, FBT002
    ):
        super().__init__(collection)
        self.cloud_hosted = cloud_hosted
        self.uuid = str(uuid.uuid4())

        self.render_dict: Any
        if fields is None:
            self.render_dict = self
        elif fields[0] == "basic":
            self.render_dict = self._filter_fields_(self._basic_umm_fields_)
        else:
            self.render_dict = self._filter_fields_(fields)

    def _umm_record(self) -> dict[str, Any]:
        umm = self.get("umm")
        return umm if isinstance(umm, dict) else {}

    def _filter_fields_(self, fields: list[str]) -> dict[str, Any]:
        umm = self._umm_record()
        filtered_dict = {
            "umm": {(field, umm[field]) for field in fields if field in umm},
        }
        basic_dict = {
            "meta": {
                (field, self["meta"][field])
                for field in self._basic_meta_fields_
                if field in self["meta"]
            },
        }
        basic_dict.update(filtered_dict)
        return basic_dict

    def _filter_related_links(self, link_type: str) -> list[str]:
        """Filter RelatedUrls from the UMM fields on CMR."""
        return [
            link["URL"]
            for link in self._umm_record().get("RelatedUrls", [])
            if link.get("Type") == link_type
        ]


class DataCollection(CustomDict):
    """Dictionary-like object to represent a data collection from CMR."""

    _basic_meta_fields_: ClassVar[list] = [
        "concept-id",
        "granule-count",
        "provider-id",
    ]

    _basic_umm_fields_: ClassVar[list] = [
        "ShortName",
        "Abstract",
        "SpatialExtent",
        "TemporalExtents",
        "DataCenters",
        "RelatedUrls",
        "ArchiveAndDistributionInformation",
        "DirectDistributionInformation",
    ]

    @property
    def summary(self) -> dict[str, Any]:
        """Summary containing short_name, concept-id, file-type, and cloud-info (if cloud-hosted).

        Returns:
            A summary of the collection metadata.
        """
        # we can print only the concept-id
        summary_dict: dict[str, Any]
        summary_dict = {
            "short-name": self.get_umm("ShortName"),
            "concept-id": self.concept_id,
            "version": self.version,
            "file-type": self.data_type,
            "get-data": self.data_links,
        }
        if "Region" in self.s3_bucket:
            summary_dict["cloud-info"] = self.s3_bucket

        return summary_dict

    def get_umm(self, umm_field: str) -> str | dict[str, Any]:
        """Return a field value from the collection UMM record.

        Parameters:
            umm_field: Valid UMM item, i.e. `TemporalExtent`.

        Returns:
            The value of a given field inside the UMM (Unified Metadata Model).
        """
        return self._umm_record().get(umm_field, "")

    @property
    def doi(self) -> str | None:
        """Retrieve the Digital Object Identifier (DOI) for this collection.

        Returns:
            This collection's DOI information, or `None`, if it has none.
        """
        doi = self._umm_record().get("DOI", {})
        if isinstance(doi, dict):
            return doi.get("DOI", None)
        return None

    def citation(self, *, format: str, language: str) -> str | None:  # noqa:A002
        """Fetch a formatted citation for this collection using its DOI.

        Parameters:
            format: Citation format style (e.g., 'apa', 'bibtex', 'ris').
                 For a full list of valid formats, visit <https://citation.doi.org/>
            language: Language code (e.g., 'en-US').
                 For a full list of valid language codes, visit <https://citation.doi.org/>

        Returns:
             The formatted citation as a string, or `None`, if this collection does not have a DOI.

        Raises:
             requests.RequestException: if fetching citation information from citations.doi.org failed.
        """
        return (
            None
            if not (doi := self.doi)
            else _citation(doi=doi, format_=format, language=language)
        )

    @property
    def concept_id(self) -> str:
        """Return the collection concept ID.

        Returns:
            A collection's `concept_id`.This id is the most relevant search field on granule queries.
        """
        return self["meta"]["concept-id"]

    @property
    def data_type(self) -> str:
        """Return the collection file distribution type.

        Returns:
            The collection data type, i.e. HDF5, CSV etc., if available.
        """
        archive_info = self._umm_record().get("ArchiveAndDistributionInformation")
        if not isinstance(archive_info, dict):
            return ""
        return str(archive_info.get("FileDistributionInformation", ""))

    @property
    def version(self) -> str:
        """Return the collection version.

        Returns:
            The collection's version.
        """
        return self._umm_record().get("Version", "")

    @property
    def abstract(self) -> str:
        """Return the collection abstract.

        Returns:
            The abstract of a collection.
        """
        return self._umm_record().get("Abstract", "")

    @property
    def landing_page(self) -> str:
        """Return the first landing page link for the collection.

        Returns:
            The first landing page for the collection (can be many), if available.
        """
        links = self._filter_related_links("LANDING PAGE")
        return links[0] if len(links) > 0 else ""

    @property
    def data_links(self) -> list[str]:
        """Return the collection GET DATA links.

        Returns:
            The GET DATA links (usually a landing page link, a DAAC portal, or an FTP location).
        """
        return self._filter_related_links("GET DATA")

    @property
    def s3_bucket(self) -> dict[str, Any]:
        """Return the collection direct distribution information.

        Returns:
            The S3 bucket information if the collection has it (**cloud hosted collections only**).
        """
        info = self._umm_record().get("DirectDistributionInformation")
        return info if isinstance(info, dict) else {}

    @property
    def services(self) -> dict[Any, list[dict[str, Any]]]:
        """Return list of services available for this collection."""
        services = self.get("meta", {}).get("associations", {}).get("services", [])
        queries = (
            DataServicesQuery(auth=earthaccess.__auth__).parameters(concept_id=service)
            for service in services
        )

        return {
            service: query.get_all()
            for service, query in zip(services, queries, strict=False)
        }

    def __repr__(self) -> str:
        return json.dumps(
            self.render_dict,
            sort_keys=False,
            indent=2,
            separators=(",", ": "),
        )

    @property
    def __geo_interface__(self) -> dict[str, object]:
        """A GeoJSON representation of this granule.

        This granule must contain a `dict` value at the path
        `self["umm"]["SpatialExtent"]["HorizontalSpatialDomain"]["Geometry"]`.
        It is assumed that the `dict` contains only a single key-value pair,
        and this property is the result of converting the value of that pair
        into an equivalent GeoJSON structure, depending on the key, as follows:

        |Key                   |Value converted to GeoJSON|
        |:---------------------|:-------------------------|
        |`"Lines"`             |`"MultiLineString"`       |
        |`"Points"`            |`"MultiPoint"`            |
        |`"BoundingRectangles"`|`"MultiPolygon"`          |
        |`"GPolygons"`         |`"MultiPolygon"`          |

        Raises:
            ValueError: If this granule does not contain a value at the path
                `self["umm"]["SpatialExtent"]["HorizontalSpatialDomain"]["Geometry"]`,
                or the value there is not a `dict` containing a value for one of
                `"Points"`, `"Lines"`, `"BoundingRectangles"`, or `"GPolygons"`.

        See Also:
            - [`__geo_interface__` Specification](https://gist.github.com/sgillies/2217756)
            - [NASA UMM-G JSON Schema](https://git.earthdata.nasa.gov/projects/EMFD/repos/unified-metadata-model/browse/granule/v1.6.6/umm-g-json-schema.json)
            - [The GeoJSON Format](https://datatracker.ietf.org/doc/html/rfc7946)
        """
        try:
            geometry = self["umm"]["SpatialExtent"]["HorizontalSpatialDomain"][
                "Geometry"
            ]
        except KeyError:
            msg = "Granule has no horizontal spatial extent"
            raise ValueError(msg) from None

        return _geometry_to_geojson(geometry)


class DataGranule(CustomDict):
    """Dictionary-like object to represent a granule from CMR."""

    _basic_meta_fields_: ClassVar[list] = [
        "concept-id",
        "provider-id",
    ]

    _basic_umm_fields_: ClassVar[list] = [
        "GranuleUR",
        "SpatialExtent",
        "TemporalExtent",
        "RelatedUrls",
        "DataGranule",
    ]

    def __init__(
        self,
        collection: dict[str, Any],
        fields: list[str] | None = None,
        cloud_hosted: bool = False,  # noqa: FBT001, FBT002
    ):
        super().__init__(collection)
        self.cloud_hosted = cloud_hosted
        # TODO: maybe add area, start date and all that as an instance value
        self["size"] = self._size
        self.uuid = str(uuid.uuid4())
        self.render_dict: Any
        if fields is None:
            self.render_dict = self
        elif fields[0] == "basic":
            self.render_dict = self._filter_fields_(self._basic_umm_fields_)
        else:
            self.render_dict = self._filter_fields_(fields)

    def __repr__(self) -> str:
        """Return a basic string representation of the granule.

        Returns:
            A basic representation of a data granule.
        """
        data_links = list(self.data_links())

        # Not all granules have spatial coverage, set to None if missing
        # TODO: We should have a granule metadata validator method
        if "SpatialExtent" not in self["umm"]:
            self["umm"]["SpatialExtent"] = None

        return f"""
        Collection: {self["umm"]["CollectionReference"]}
        Spatial coverage: {self["umm"]["SpatialExtent"]}
        Temporal coverage: {self["umm"]["TemporalExtent"]}
        Size(MB): {self._size}
        Data: {data_links}\n\n
        """.strip().replace("  ", "")

    def _repr_html_(self) -> str:
        """Return an HTML representation of the granule.

        Returns:
            A rich representation for a data granule if we are in a Jupyter notebook.
        """
        return _repr_granule_html(self)

    def __hash__(self) -> int:  # type: ignore[override]
        return hash(self["meta"]["concept-id"])

    def get_s3_credentials_endpoint(self) -> str | None:
        for link in self["umm"]["RelatedUrls"]:
            if "/s3credentials" in link["URL"]:
                return link["URL"]
        return None

    @property
    def _size(self) -> float:
        """Return the total granule size in MB.

        Returns:
            The total size for the granule in MB.
        """
        try:
            data_granule = self["umm"]["DataGranule"]
            total_size = sum(
                [
                    float(s["Size"])
                    for s in data_granule["ArchiveAndDistributionInformation"]
                    if "ArchiveAndDistributionInformation" in data_granule
                ],
            )
        except Exception:  # noqa: BLE001
            try:
                data_granule = self["umm"]["DataGranule"]
                total_size = sum(
                    [
                        float(s["SizeInBytes"])
                        for s in data_granule["ArchiveAndDistributionInformation"]
                        if "ArchiveAndDistributionInformation" in data_granule
                    ],
                ) / (1024 * 1024)
            except Exception:  # noqa: BLE001
                total_size = 0
        return total_size

    @property
    def size(self) -> float:
        """Return the total granule size in MB.

        Returns:
            The total size for the granule in MB.
        """
        return self._size

    def _derive_s3_link(self, links: list[str]) -> list[str]:
        s3_links = []
        for link in links:
            if link.startswith("s3"):
                s3_links.append(link)
            elif link.startswith("https://") and (
                "cumulus" in link or "protected" in link
            ):
                _, separator, suffix = link.partition("nasa.gov/")
                if separator:
                    s3_links.append(f"s3://{suffix}")
        return s3_links

    def data_links(
        self,
        access: AccessType | None = None,
        in_region: bool | None = None,  # noqa: FBT001
    ) -> list[str]:
        """Return data links for the requested granule access mode.

        Returns the data links from a granule.

        Parameters:
            access: ``"https"`` (default) or ``"s3"``. Legacy values
                ``"external"`` and ``"indirect"`` map to HTTPS, and ``"direct"``
                maps to S3.
            in_region: Deprecated. Use ``access`` instead.

        Returns:
            The data links for the requested access type.
        """
        if in_region is not None:
            warnings.warn(
                "`in_region` is deprecated; use `access='s3'` or "
                "`access='https'` instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            if access is None:
                # Preserve the old in_region behavior for on-prem granules.
                access = "s3" if in_region and self.cloud_hosted else "https"

        canonical_access = normalize_access(access)
        https_links = self._filter_related_links("GET DATA")
        s3_links = self._filter_related_links("GET DATA VIA DIRECT ACCESS")

        if canonical_access == "https":
            return https_links

        if s3_links:
            return s3_links

        if self.cloud_hosted:
            # This is guessing the S3 links for some cloud collections that for
            # some reason only offered HTTPS links
            return self._derive_s3_link(https_links)
        return []

    @property
    def dataviz_links(self) -> list[str]:
        """Return related visualization links for the granule.

        Returns:
            The data visualization links, usually the browse images.
        """
        return self._filter_related_links("GET RELATED VISUALIZATION")

    @property
    def __geo_interface__(self) -> dict[str, object]:
        """A GeoJSON representation of this granule.

        This granule must contain a `dict` value at the path
        `self["umm"]["SpatialExtent"]["HorizontalSpatialDomain"]["Geometry"]`.
        It is assumed that the `dict` contains only a single key-value pair,
        and this property is the result of converting the value of that pair
        into an equivalent GeoJSON structure, depending on the key, as follows:

        |Key                   |Value converted to GeoJSON|
        |:---------------------|:-------------------------|
        |`"Lines"`             |`"MultiLineString"`       |
        |`"Points"`            |`"MultiPoint"`            |
        |`"BoundingRectangles"`|`"MultiPolygon"`          |
        |`"GPolygons"`         |`"MultiPolygon"`          |

        Raises:
            ValueError: If this granule does not contain a value at the path
                `self["umm"]["SpatialExtent"]["HorizontalSpatialDomain"]["Geometry"]`,
                or the value there is not a `dict` containing a value for one of
                `"Points"`, `"Lines"`, `"BoundingRectangles"`, or `"GPolygons"`.

        See Also:
            - [`__geo_interface__` Specification](https://gist.github.com/sgillies/2217756)
            - [NASA UMM-G JSON Schema](https://git.earthdata.nasa.gov/projects/EMFD/repos/unified-metadata-model/browse/granule/v1.6.6/umm-g-json-schema.json)
            - [The GeoJSON Format](https://datatracker.ietf.org/doc/html/rfc7946)
        """
        try:
            geometry = self["umm"]["SpatialExtent"]["HorizontalSpatialDomain"][
                "Geometry"
            ]
        except KeyError:
            msg = "Granule has no horizontal spatial extent"
            raise ValueError(msg) from None

        return _geometry_to_geojson(geometry)


class Results[ResultItem: (DataGranule, DataCollection)](Sequence[ResultItem]):
    """Search results, together with the search inputs that produced them.

    `search_data()` returns a `GranuleResults` and `search_datasets()` returns a
    `CollectionResults`. Both can be indexed, sliced, and iterated any number of
    times, and both can be passed to `download()` and `open()` as before.

    The keyword arguments of the search are kept in `query_params`, so the
    search can be repeated later:

    ```python
    results = earthaccess.search_data(short_name="ATL06", count=10)
    again = earthaccess.search_data(**results.query_params)
    ```

    Items can be supplied as a sequence (loaded up front) or as an iterator
    (loaded the first time they are needed). Either way, each item is taken from
    its source once and then kept, so later iterations return the same items
    without searching again. A results object that is still loading from an
    iterator is not safe to read from several threads at once; call `all()`
    first.

    `ResultItem` is the type of each result: `DataGranule` for `GranuleResults`
    and `DataCollection` for `CollectionResults`. It lets type checkers and
    editors know that `search_data(...)[0]` is a `DataGranule`.
    """

    _query_type: ClassVar[str] = "unknown"

    def __init__(
        self,
        items: Iterable[ResultItem],
        *,
        query_params: Mapping[str, Any] | None = None,
        query_options: Mapping[str, Any] | None = None,
        hits: int | None = None,
    ) -> None:
        """Create a results object.

        Parameters:
            items: The results, as a sequence or as an iterator.
            query_params: The keyword arguments of the search that produced
                these results. Each search returns a new results object with its
                own `query_params`. A copy is stored, so if a list or dict passed
                to the search (a bounding box, say) is changed afterwards, the
                stored value still matches the search that was run.
            query_options: The CMR query options of the search, shown when the
                results are displayed.
            hits: The number of matches CMR reported for the search. This can be
                larger than the number of items when the search had a `count`.
        """
        self._items: list[ResultItem]
        self._source: Iterator[ResultItem] | None
        if isinstance(items, Sequence):
            self._items, self._source = list(items), None
        else:
            self._items, self._source = [], iter(items)
        self._query_params = (
            None if query_params is None else copy.deepcopy(dict(query_params))
        )
        self._query_options = (
            None if query_options is None else copy.deepcopy(dict(query_options))
        )
        self._hits = hits

    def _load(self, n: int | None = None) -> None:
        """Take items from the source until `n` are loaded (`None` means all)."""
        while self._source is not None and (n is None or len(self._items) < n):
            try:
                self._items.append(next(self._source))
            except StopIteration:
                self._source = None

    @property
    def query_params(self) -> dict[str, Any] | None:
        """The keyword arguments of the search, or `None` if not known.

        Pass them back to the same search function to repeat the search. A new
        copy is returned on each access, so changing it does not change these
        results.
        """
        return copy.deepcopy(self._query_params)

    @property
    def query_options(self) -> dict[str, Any] | None:
        """The CMR query arguments of the search, or `None` if not known.

        Pass them back to the same search function to repeat the search. A new
        copy is returned on each access, so changing it does not change these
        results.
        """
        return copy.deepcopy(self._query_options)

    @property
    def hits(self) -> int | None:
        """The number of matches CMR reported for the search, if known."""
        return self._hits

    def all(self) -> list[ResultItem]:
        """Return every result as a new list, loading any not yet loaded.

        Returns:
            A list of all results. Changing the list does not change these
            results.
        """
        self._load()
        return list(self._items)

    def __iter__(self) -> Iterator[ResultItem]:
        i = 0
        while True:
            self._load(i + 1)
            if i >= len(self._items):
                return
            yield self._items[i]
            i += 1

    def __len__(self) -> int:
        self._load()
        return len(self._items)

    def __bool__(self) -> bool:
        self._load(1)
        return bool(self._items)

    @overload
    def __getitem__(self, index: int) -> ResultItem: ...

    @overload
    def __getitem__(self, index: slice) -> list[ResultItem]: ...

    def __getitem__(self, index: int | slice) -> ResultItem | list[ResultItem]:
        if isinstance(index, slice) or index < 0:
            self._load()
        else:
            self._load(index + 1)
        return self._items[index]

    def __getstate__(self) -> dict[str, Any]:
        # Iterators cannot be pickled, so load everything first.
        self._load()
        return self.__dict__.copy()

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"params={self._query_params!r}, "
            f"options={self._query_options!r})"
        )

    def _repr_html_(self) -> str:
        query_type = html.escape(self._query_type)
        hits = html.escape(self._hits_label())
        params = html.escape(str(self._query_params))
        options = html.escape(str(self._query_options))
        return dedent(f"""
            <table>
                <tr>
                    <td>Query Type</td>
                    <td style="text-align: left;">{query_type}</td>
                </tr>
                <tr>
                    <td>Number of Results</td>
                    <td style="text-align: left;">{hits}</td>
                </tr>
                <tr>
                    <td>Query parameters</td>
                    <td style="text-align: left;">{params}</td>
                </tr>
                <tr>
                    <td>Query options</td>
                    <td style="text-align: left;">{options}</td>
                </tr>
            </table>
        """)

    def __str__(self) -> str:
        return (
            f"Number of results: {self._hits_label()}\n"
            f"Query parameters:\n"
            f"{indent(pprint.pformat(self._query_params), ' ' * 4)}\n"
            f"Query parameter options:\n"
            f"{indent(pprint.pformat(self._query_options), ' ' * 4)}"
        )

    def _hits_label(self) -> str:
        return "unknown" if self._hits is None else str(self._hits)


class GranuleResults(Results[DataGranule]):
    """Results of a granule search, as returned by `search_data()`."""

    _query_type: ClassVar[str] = "DataGranulesQuery"

    def to_gdf(self) -> "gpd.GeoDataFrame":
        """Convert these granules to a GeoPandas GeoDataFrame.

        This is the same as `DataGranulesQuery.to_geopandas(results)`.

        Returns:
            A GeoDataFrame with one row per granule.

        Raises:
            ModuleNotFoundError: If geopandas is not installed.

        Example:
            ```python
            granules = earthaccess.search_data(short_name="MYD11A1", count=100)
            gdf = granules.to_gdf()
            ```
        """
        from .search import DataGranulesQuery  # noqa: PLC0415

        return DataGranulesQuery.to_geopandas(self)


class CollectionResults(Results[DataCollection]):
    """Results of a collection search, as returned by `search_datasets()`."""

    _query_type: ClassVar[str] = "DataCollectionsQuery"

    def to_gdf(self) -> "gpd.GeoDataFrame":
        """Convert these collections to a GeoPandas GeoDataFrame.

        This is the same as `DataCollectionsQuery.to_geopandas(results)`.

        Returns:
            A GeoDataFrame with one row per collection.

        Raises:
            ModuleNotFoundError: If geopandas is not installed.

        Example:
            ```python
            collections = earthaccess.search_datasets(keyword="above ground biomass")
            gdf = collections.to_gdf()
            ```
        """
        from .search import DataCollectionsQuery  # noqa: PLC0415

        return DataCollectionsQuery.to_geopandas(self)
