"""Result classes for CMR search results.

This module provides the DataCollection, DataGranule, and SearchResults classes
for representing and working with NASA CMR search results.
"""

import os
import uuid
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from functools import cache, cached_property
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Union

import pystac
import requests
import s3fs
from typing_extensions import deprecated

import earthaccess
from earthaccess._access import AccessType, normalize_access
from earthaccess.formatting import (
    _repr_collection_html,
    _repr_granule_html,
    _repr_search_results_html,
)
from earthaccess.search.services import DataServices


@cache
def _citation(*, doi: str, format: str, language: str) -> str:
    response = requests.get(
        "https://citation.doi.org/format",
        params={"doi": doi, "style": format, "lang": language},
    )
    response.raise_for_status()
    return response.text


# Map common file extensions to human-readable data types. Used to derive a
# granule's file type(s) from the extension of its GET DATA links when the
# collection does not advertise one (e.g. via ArchiveAndDistributionInformation).
EXTENSION_TO_TYPE: Dict[str, str] = {
    ".tif": "COG",
    ".tiff": "COG",
    ".h5": "HDF5",
    ".hdf": "HDF5",
    ".hdf5": "HDF5",
    ".he5": "HDF5",
    ".nc": "NetCDF",
    ".nc4": "NetCDF",
    ".zarr": "Zarr",
    ".csv": "CSV",
    ".txt": "Text",
    ".json": "JSON",
    ".xml": "XML",
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
}


def _extension_from_url(url: str) -> str:
    """Return the file extension of a URL's path, lowercased."""
    return os.path.splitext(url.split("?")[0].split("#")[0])[1].lower()


def _type_for_extension(ext: str) -> str:
    """Map a file extension to a readable type, falling back to the extension.

    Returns the human-readable type from :data:`EXTENSION_TO_TYPE` when a
    match exists (e.g. ``".tif"`` -> ``"COG"``), otherwise the extension
    itself (e.g. ``".jgr"``) so unknown formats are still listed.
    """
    return EXTENSION_TO_TYPE.get(ext, ext)


class CustomDict(dict):
    _basic_umm_fields_: List = []
    _basic_meta_fields_: List = []

    def __init__(
        self,
        collection: Dict[str, Any],
        fields: Optional[List[str]] = None,
        cloud_hosted: bool = False,
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

    def _filter_fields_(self, fields: List[str]) -> Dict[str, Any]:
        filtered_dict = {
            "umm": dict(
                (field, self["umm"][field]) for field in fields if field in self["umm"]
            )
        }
        basic_dict = {
            "meta": dict(
                (field, self["meta"][field])
                for field in self._basic_meta_fields_
                if field in self["meta"]
            )
        }
        basic_dict.update(filtered_dict)
        return basic_dict

    def _filter_related_links(self, filter: str) -> List[str]:
        """Filter RelatedUrls from the UMM fields on CMR."""
        matched_links: List = []
        if "RelatedUrls" in self["umm"]:
            for link in self["umm"]["RelatedUrls"]:
                if link["Type"] == filter:
                    matched_links.append(link["URL"])
        return matched_links

    def to_geopandas(self, fields: Optional[List[str]] = None) -> "Any":
        """Return a one-row :class:`geopandas.GeoDataFrame` for this item.

        The geometry column is built from :attr:`__geo_interface__` (the
        granule/collection's horizontal spatial extent, as a shapely geometry
        in EPSG:4326). By default the full UMM record is flattened into columns
        (nested fields become dotted column names, e.g.
        ``umm.TemporalExtents.0.RangeDateTimes.0.BeginningDateTime``); the CMR
        metadata block (``concept-id``, ``provider-id``, ...) is always included
        under ``meta.*``.

        Parameters:
            fields: Optional list of top-level UMM field names to include
                (e.g. ``["ShortName", "TemporalExtents"]``). If omitted (the
                default), the entire UMM record is flattened.

        Returns:
            A one-row GeoDataFrame (CRS EPSG:4326).

        Raises:
            ImportError: If geopandas is not installed
                (``pip install earthaccess[geo]``).
            ValueError: If this item has no horizontal spatial extent.

        Examples:
            >>> granule = results[0]
            >>> gdf = granule.to_geopandas()
            >>> gdf = granule.to_geopandas(fields=["GranuleUR", "TemporalExtent"])
        """
        try:
            import geopandas as gpd
            import pandas as pd
        except ImportError as e:  # pragma: no cover - depends on environment
            raise ImportError(
                "to_geopandas() requires geopandas. Install it with: "
                "pip install earthaccess[geo]"
            ) from e
        from shapely.geometry import shape

        geometry = shape(self.__geo_interface__)  # type: ignore[attr-defined]

        umm = self.get("umm", {})
        if fields is not None:
            umm = {field: umm[field] for field in fields if field in umm}

        meta = {
            field: self["meta"][field]
            for field in self._basic_meta_fields_
            if field in self.get("meta", {})
        }

        frame = pd.json_normalize({"umm": umm, "meta": meta})
        frame["geometry"] = geometry
        return gpd.GeoDataFrame(frame, geometry="geometry", crs="EPSG:4326")


class DataCollection(CustomDict):
    """Dictionary-like object to represent a data collection from CMR."""

    __module__ = "earthaccess.search"

    @property
    def __geo_interface__(self) -> dict[str, object]:
        """A GeoJSON representation of this collection.

        This collection must contain a `dict` value at the path
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
            ValueError: If this collection does not contain a value at the path
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
            msg = "Collection has no horizontal spatial extent"
            raise ValueError(msg) from None

        return _geometry_to_geojson(geometry)

    _basic_meta_fields_ = [
        "concept-id",
        "granule-count",
        "provider-id",
    ]

    _basic_umm_fields_ = [
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
    def summary(self) -> Dict[str, Any]:
        """Summary containing short_name, concept-id, file-type, and cloud-info (if cloud-hosted).

        Returns:
            A summary of the collection metadata.
        """
        # we can print only the concept-id

        summary_dict: Dict[str, Any]
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

    def get_umm(self, umm_field: str) -> Union[str, Dict[str, Any]]:
        """Placeholder.

        Parameters:
            umm_field: Valid UMM item, i.e. `TemporalExtent`.

        Returns:
            The value of a given field inside the UMM (Unified Metadata Model).
        """
        return self["umm"].get(umm_field, "")

    @property
    def doi(self) -> str | None:
        """Retrieve the Digital Object Identifier (DOI) for this collection.

        Returns:
            This collection's DOI information, or `None`, if it has none.
        """
        doi = self["umm"].get("DOI", {})
        if isinstance(doi, dict):
            return doi.get("DOI", None)
        return None

    def citation(self, *, format: str, language: str) -> str | None:
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
            else _citation(doi=doi, format=format, language=language)
        )

    @property
    def concept_id(self) -> str:
        """Placeholder.

        Returns:
            A collection's `concept_id`.This id is the most relevant search field on granule queries.
        """
        return self["meta"]["concept-id"]

    @property
    def data_type(self) -> str:
        """Return the collection's data file type, if advertised.

        Reads the ``Format`` field from the collection's
        ``ArchiveAndDistributionInformation.FileDistributionInformation``
        (e.g. ``"HDF-EOS5"`` or ``"COG"``).

        Returns:
            The collection data type, e.g. HDF5, CSV etc., or ``""``
            if the collection does not advertise one.
        """
        file_dist = (
            self["umm"]
            .get("ArchiveAndDistributionInformation", {})
            .get("FileDistributionInformation")
        )
        if isinstance(file_dist, list) and file_dist:
            for entry in file_dist:
                if isinstance(entry, dict) and entry.get("Format"):
                    return str(entry["Format"])
        return ""

    @property
    def version(self) -> str:
        """Placeholder.

        Returns:
            The collection's version.
        """
        return self["umm"].get("Version", "")

    @property
    def abstract(self) -> str:
        """Placeholder.

        Returns:
            The abstract of a collection.
        """
        return self["umm"].get("Abstract", "")

    @property
    def landing_page(self) -> str:
        """Placeholder.

        Returns:
            The first landing page for the collection (can be many), if available.
        """
        links = self._filter_related_links("LANDING PAGE")
        return links[0] if len(links) > 0 else ""

    @property
    def data_links(self) -> List[str]:
        """Placeholder.

        Returns:
            The GET DATA links (usually a landing page link, a DAAC portal, or an FTP location).
        """
        return self._filter_related_links("GET DATA")

    @property
    def s3_bucket(self) -> Dict[str, Any]:
        """Placeholder.

        Returns:
            The S3 bucket information if the collection has it (**cloud hosted collections only**).
        """
        return self["umm"].get("DirectDistributionInformation", {})

    def virtual_collection_url(self) -> Optional[str]:
        """Return the collection's virtual-store URL, if CMR advertises one.

        Virtual collections are marked by a ``VIRTUAL COLLECTION`` subtype on a
        RelatedUrl (usually type ``GET DATA``) whose ``URL`` points at the
        virtual store (for example an icechunk repository).

        Returns:
            The store URL, or ``None`` when the collection does not advertise
            a virtual collection.
        """
        for link in self["umm"].get("RelatedUrls", []):
            kind = f"{link.get('Type', '')} {link.get('Subtype', '')}".upper()
            if "VIRTUAL COLLECTION" in kind:
                return link.get("URL") or None
        return None

    def get_s3_credentials(self) -> Dict[str, str]:
        """Return temporary S3 credentials for this collection.

        Returns:
            A dictionary with `accessKeyId`, `secretAccessKey`,
            and `sessionToken`.

        Raises:
            ValueError: If the collection has no `S3CredentialsAPIEndpoint`.
        """
        dd = self["umm"].get("DirectDistributionInformation", {})
        endpoint = dd.get("S3CredentialsAPIEndpoint")
        if not endpoint:
            msg = "This collection does not provide an S3CredentialsAPIEndpoint."
            raise ValueError(msg)
        return earthaccess.__auth__.get_s3_credentials(endpoint=endpoint)

    @cached_property
    def s3_credentials(self) -> Dict[str, str]:
        """Cached temporary S3 credentials for this collection.

        The first access exchanges the EDL token for temporary S3 credentials
        and caches the result on the instance, so repeated callers (e.g. the
        icechunk VCC authorization path) do not hit the credentials endpoint
        more than once.

        Returns:
            A dictionary with `accessKeyId`, `secretAccessKey`,
            and `sessionToken`.

        Raises:
            ValueError: If the collection has no `S3CredentialsAPIEndpoint`.
        """
        return self.get_s3_credentials()

    def get_s3_filesystem(self) -> s3fs.S3FileSystem:
        """Return an authenticated `s3fs.S3FileSystem` for this collection.

        Returns:
            An `s3fs.S3FileSystem` configured with temporary S3 credentials
            from the collection's credentials endpoint.
        """
        creds = self.get_s3_credentials()
        return s3fs.S3FileSystem(
            key=creds["accessKeyId"],
            secret=creds["secretAccessKey"],
            token=creds["sessionToken"],
        )

    @property
    def services(self) -> Dict[Any, List[Dict[str, Any]]]:
        """Return list of services available for this collection."""
        services = self.get("meta", {}).get("associations", {}).get("services", [])
        queries = (
            DataServices(auth=earthaccess.__auth__).parameters(concept_id=service)
            for service in services
        )

        return {
            service: list(query.get_all()) for service, query in zip(services, queries)
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert the collection to a plain dictionary.

        Returns:
            A dictionary representation of the collection.
        """
        return dict(self)

    def to_stac(self) -> pystac.Collection:
        """Convert the CMR UMM collection to a STAC Collection.

        Returns a pystac Collection following the STAC spec. Use
        ``pystac_client`` or pystac's serialization methods to work with it
        further.

        Returns:
            A pystac Collection.
        """
        # Extract basic metadata
        concept_id = self.concept_id
        short_name = self.get_umm("ShortName") or concept_id
        version = self.version
        abstract = self.abstract

        # Build collection ID
        collection_id = f"{short_name}_v{version}" if version else str(short_name)

        # Extract temporal extent
        temporal_extents = self.get_umm("TemporalExtents")
        temporal_extent = self._extract_temporal_extent(temporal_extents)

        # Extract spatial extent
        spatial_extents = self.get_umm("SpatialExtent")
        spatial_extent = self._extract_spatial_extent(spatial_extents)

        # Build STAC Collection
        stac_collection: Dict[str, Any] = {
            "type": "Collection",
            "stac_version": "1.0.0",
            "stac_extensions": [],
            "id": collection_id,
            "title": str(short_name),
            "description": str(abstract) if abstract else "No description available",
            "license": "proprietary",
            "extent": {
                "spatial": {"bbox": [spatial_extent]},
                "temporal": {"interval": [temporal_extent]},
            },
            "links": self._build_stac_links(),
            "providers": self._build_stac_providers(),
        }

        # Add DOI if available
        doi = self.doi
        if doi:
            stac_collection["sci:doi"] = doi
            stac_collection["stac_extensions"].append(
                "https://stac-extensions.github.io/scientific/v1.0.0/schema.json"
            )

        # Add CMR-specific properties
        stac_collection["cmr:concept_id"] = concept_id
        stac_collection["cmr:provider_id"] = self["meta"].get("provider-id", "")

        return pystac.Collection.from_dict(stac_collection)

    def _extract_temporal_extent(self, temporal_extents: Any) -> List[Optional[str]]:
        """Extract temporal extent from UMM TemporalExtents."""
        if not temporal_extents:
            return [None, None]

        if isinstance(temporal_extents, list) and len(temporal_extents) > 0:
            extent = temporal_extents[0]
            if "RangeDateTimes" in extent and extent["RangeDateTimes"]:
                range_dt = extent["RangeDateTimes"][0]
                start = range_dt.get("BeginningDateTime")
                end = range_dt.get("EndingDateTime")
                return [start, end]
            elif "SingleDateTimes" in extent and extent["SingleDateTimes"]:
                single = extent["SingleDateTimes"][0]
                return [single, single]

        return [None, None]

    def _extract_spatial_extent(self, spatial_extent: Any) -> List[float]:
        """Extract spatial extent from UMM SpatialExtent."""
        default_bbox = [-180.0, -90.0, 180.0, 90.0]

        if not spatial_extent:
            return default_bbox

        if isinstance(spatial_extent, dict):
            granule_spatial = spatial_extent.get("HorizontalSpatialDomain", {}).get(
                "Geometry", {}
            )

            bounding_rects = granule_spatial.get("BoundingRectangles", [])
            if bounding_rects:
                rect = bounding_rects[0]
                return [
                    rect.get("WestBoundingCoordinate", -180.0),
                    rect.get("SouthBoundingCoordinate", -90.0),
                    rect.get("EastBoundingCoordinate", 180.0),
                    rect.get("NorthBoundingCoordinate", 90.0),
                ]

        return default_bbox

    def _build_stac_links(self) -> List[Dict[str, str]]:
        """Build STAC links from collection metadata."""
        links: List[Dict[str, str]] = []

        # Self link
        concept_id = self.concept_id
        links.append(
            {
                "rel": "self",
                "href": f"https://cmr.earthdata.nasa.gov/search/concepts/{concept_id}",
                "type": "application/json",
            }
        )

        # Landing page
        landing = self.landing_page
        if landing:
            links.append(
                {
                    "rel": "about",
                    "href": landing,
                    "type": "text/html",
                    "title": "Landing Page",
                }
            )

        # Get data links
        for url in self.data_links:
            links.append(
                {
                    "rel": "via",
                    "href": url,
                    "title": "Data Access",
                }
            )

        return links

    def _build_stac_providers(self) -> List[Dict[str, Any]]:
        """Build STAC providers from collection metadata."""
        providers: List[Dict[str, Any]] = []

        provider_id = self["meta"].get("provider-id", "")
        if provider_id:
            providers.append(
                {
                    "name": provider_id,
                    "roles": ["producer", "host"],
                }
            )

        return providers

    def __repr__(self) -> str:
        """Return a concise, pystac-style representation of this collection.

        Shows the class, the collection's short name and version, and its
        concept ID.

        Returns:
            A single-line representation, e.g.
            ``<DataCollection short_name=HLSS30 version=2.0 concept_id=C2021957295-LPCLOUD>``
        """
        short_name = self.get("umm", {}).get("ShortName", "unknown")
        version = self.get("umm", {}).get("Version", "")
        concept_id = self.get("meta", {}).get("concept-id", "unknown")

        version_str = f" version={version}" if version else ""
        return (
            f"<{self.__class__.__name__} short_name={short_name}"
            f"{version_str} concept_id={concept_id}>"
        )

    def _repr_html_(self) -> str:
        """Return HTML representation for Jupyter notebook display.

        Returns:
            HTML string with collection metadata card.
        """
        return _repr_collection_html(self)

    def explore(self, **kwargs: Any) -> Any:
        """Display an interactive map with the spatial extent of this collection.

        Requires the [widgets] extra: pip install earthaccess[widgets]

        Parameters:
            **kwargs: Additional arguments passed to lonboard (fill_color, line_color)

        Returns:
            A lonboard Map widget for display in Jupyter

        Raises:
            ImportError: If widget dependencies are not installed
            ValueError: If collection has no spatial extent

        Examples:
            >>> collection = earthaccess.search_datasets(short_name="ATL06")[0]
            >>> collection.explore()  # Display interactive map
        """
        from earthaccess.formatting.widgets import plot_collection

        return plot_collection(self, **kwargs)

    @deprecated("Use explore() instead")
    def plot(self, **kwargs: Any) -> Any:
        """Alias for :meth:`explore`; deprecated.

        .. deprecated:: 1.0.0a2
            Use :meth:`explore` instead.
        """
        return self.explore(**kwargs)


@dataclass(frozen=True)
class GranuleFilter:
    """Immutable filter for selecting granules based on various criteria.

    This filter can be used with SearchResults.filter() to select granules
    matching specific criteria like size, cloud hosting, or temporal range.

    Attributes:
        min_size: Minimum granule size in MB
        max_size: Maximum granule size in MB
        cloud_hosted: If set, filter by cloud hosting status
        start_date: Filter granules ending after this date
        end_date: Filter granules starting before this date
        predicate: Custom filter function taking a DataGranule and returning bool

    Examples:
        >>> # Filter by size
        >>> f = GranuleFilter(min_size=10, max_size=100)

        >>> # Filter cloud-hosted granules larger than 50MB
        >>> f = GranuleFilter(cloud_hosted=True, min_size=50)

        >>> # Custom predicate
        >>> f = GranuleFilter(predicate=lambda g: "ATL06" in g["umm"]["GranuleUR"])
    """

    __module__ = "earthaccess.search"

    min_size: Optional[float] = None
    max_size: Optional[float] = None
    cloud_hosted: Optional[bool] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    predicate: Optional[Callable[["DataGranule"], bool]] = field(
        default=None, compare=False
    )

    def matches(self, granule: "DataGranule") -> bool:
        """Check if a granule passes all filter criteria.

        Parameters:
            granule: DataGranule to check

        Returns:
            True if granule passes all filter criteria, False otherwise
        """
        # Size filtering
        size = granule.size
        if size is not None:
            if self.min_size is not None and size < self.min_size:
                return False
            if self.max_size is not None and size > self.max_size:
                return False

        # Cloud hosted filtering
        if self.cloud_hosted is not None:
            if granule.cloud_hosted != self.cloud_hosted:
                return False

        # Temporal filtering
        if self.start_date is not None or self.end_date is not None:
            temporal = granule.get("umm", {}).get("TemporalExtent", {})
            range_dt = temporal.get("RangeDateTime", {})
            begin_str = range_dt.get("BeginningDateTime")
            end_str = range_dt.get("EndingDateTime")

            if begin_str and self.end_date is not None:
                begin_dt = datetime.fromisoformat(begin_str.replace("Z", "+00:00"))
                if begin_dt > self.end_date.replace(tzinfo=begin_dt.tzinfo):
                    return False

            if end_str and self.start_date is not None:
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                if end_dt < self.start_date.replace(tzinfo=end_dt.tzinfo):
                    return False

        # Custom predicate
        if self.predicate is not None:
            if not self.predicate(granule):
                return False

        return True


def _geometry_to_geojson(geometry_data: dict[str, Any]) -> dict[str, Any]:
    """Convert a UMM-G geometry into an equivalent GeoJSON structure.

    This assumes that ``geometry_data`` is the value of
    ``self["umm"]["SpatialExtent"]["HorizontalSpatialDomain"]["Geometry"]`` and
    that it contains exactly one key-value pair. The single key selects how the
    value is converted to GeoJSON, as follows:

    ==============  ======================
    Key                 Converted to GeoJSON
    ==============  ======================
    ``"Lines"``        ``"MultiLineString"``
    ``"Points"``       ``"MultiPoint"``
    ``"BoundingRectangles"``  ``"MultiPolygon"``
    ``"GPolygons"``    ``"MultiPolygon"``
    ==============  ======================

    Raises:
        ValueError: If ``geometry_data`` is not a ``dict`` containing a value
            for one of ``"Points"``, ``"Lines"``, ``"BoundingRectangles"``, or
            ``"GPolygons"``.

    See Also:
        - [`__geo_interface__` Specification](https://gist.github.com/sgillies/2217756)
        - [NASA UMM-G JSON Schema](https://git.earthdata.nasa.gov/projects/EMFD/repos/unified-metadata-model/browse/granule/v1.6.6/umm-g-json-schema.json)
        - [The GeoJSON Format](https://datatracker.ietf.org/doc/html/rfc7946)
    """
    if "GPolygons" in geometry_data:
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
                        for boundary in poly.get("ExclusiveZone", {}).get(
                            "Boundaries", []
                        )
                    ),
                ]
                for poly in geometry_data["GPolygons"]
            ],
        }

    if "BoundingRectangles" in geometry_data:
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
                for rect in geometry_data["BoundingRectangles"]
            ],
        }

    if "Points" in geometry_data:
        return {
            "type": "MultiPoint",
            "coordinates": [
                [p["Longitude"], p["Latitude"]] for p in geometry_data["Points"]
            ],
        }

    if "Lines" in geometry_data:
        return {
            "type": "MultiLineString",
            "coordinates": [
                [[p["Longitude"], p["Latitude"]] for p in line["Points"]]
                for line in geometry_data["Lines"]
            ],
        }

    msg = f"Invalid Geometry in granule's horizontal spatial extent: {geometry_data}"
    raise ValueError(msg) from None


class DataGranule(CustomDict):
    """Dictionary-like object to represent a granule from CMR."""

    __module__ = "earthaccess.search"

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

    _basic_meta_fields_ = [
        "concept-id",
        "provider-id",
    ]

    _basic_umm_fields_ = [
        "GranuleUR",
        "SpatialExtent",
        "TemporalExtent",
        "RelatedUrls",
        "DataGranule",
    ]

    def __init__(
        self,
        collection: Dict[str, Any],
        fields: Optional[List[str]] = None,
        cloud_hosted: bool = False,
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
        """Return a concise, pystac-style representation of this granule.

        Shows the class, the granule's primary identifier, its temporal
        coverage, size, and the number of data files.

        Returns:
            A single-line representation, e.g.
            ``<DataGranule granule_ur=HLS.S30...v2.0 temporal=2015-11-28 size=1.66 MB files=18>``
        """
        granule_ur = self.get("umm", {}).get(
            "GranuleUR", self["meta"].get("concept-id", "unknown")
        )
        temporal = self.get("umm", {}).get("TemporalExtent", {})
        begin = (
            temporal.get("RangeDateTime", {}).get("BeginningDateTime")
            or temporal.get("SingleDateTime")
            or ""
        )
        date_str = begin[:10] if begin else "n/a"
        size = self.size
        size_str = f"{size:.2f} MB" if size else "n/a"
        n_files = len(self.data_links())

        return (
            f"<{self.__class__.__name__} granule_ur={granule_ur} "
            f"temporal={date_str} size={size_str} files={n_files}>"
        )

    def _repr_html_(self) -> str:
        """Placeholder.

        Returns:
            A rich representation for a data granule if we are in a Jupyter notebook.
        """
        granule_html_repr = _repr_granule_html(self)
        return granule_html_repr

    def explore(self, **kwargs: Any) -> Any:
        """Display an interactive map with the bounding box for this granule.

        Requires the [widgets] extra: pip install earthaccess[widgets]

        Parameters:
            **kwargs: Additional arguments passed to lonboard (fill_color, line_color)

        Returns:
            A lonboard Map widget for display in Jupyter

        Raises:
            ImportError: If widget dependencies are not installed
            ValueError: If granule has no spatial extent

        Examples:
            >>> granule = results[0]
            >>> granule.explore()  # Display interactive map
        """
        from earthaccess.formatting.widgets import plot_granule

        return plot_granule(self, **kwargs)

    @deprecated("Use explore() instead")
    def plot(self, **kwargs: Any) -> Any:
        """Alias for :meth:`explore`; deprecated.

        .. deprecated:: 1.0.0a2
            Use :meth:`explore` instead.
        """
        return self.explore(**kwargs)

    def __hash__(self) -> int:  # type: ignore[override]
        return hash(self["meta"]["concept-id"])

    def get_s3_credentials_endpoint(self) -> Union[str, None]:
        for link in self["umm"]["RelatedUrls"]:
            if "/s3credentials" in link["URL"]:
                return link["URL"]
        return None

    @cached_property
    def s3_credentials(self) -> Dict[str, str]:
        """Cached temporary S3 credentials for this granule.

        The credentials endpoint is read from the granule's `RelatedUrls`
        and the EDL token is exchanged once; the result is cached on the
        instance.

        Returns:
            A dictionary with `accessKeyId`, `secretAccessKey`,
            and `sessionToken`.

        Raises:
            ValueError: If the granule has no `s3credentials` endpoint.
        """
        endpoint = self.get_s3_credentials_endpoint()
        if not endpoint:
            msg = "This granule does not provide an s3credentials endpoint."
            raise ValueError(msg)
        return earthaccess.__auth__.get_s3_credentials(endpoint=endpoint)

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
                ]
            )
        except Exception:
            try:
                data_granule = self["umm"]["DataGranule"]
                total_size = sum(
                    [
                        float(s["SizeInBytes"])
                        for s in data_granule["ArchiveAndDistributionInformation"]
                        if "ArchiveAndDistributionInformation" in data_granule
                    ]
                ) / (1024 * 1024)
            except Exception:
                total_size = 0
        return total_size

    @property
    def size(self) -> float:
        """Return the total granule size in MB.

        Returns:
            The total size for the granule in MB.
        """
        return self._size

    def _derive_s3_link(self, links: List[str]) -> List[str]:
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
    ) -> List[str]:
        """Return data links for the requested granule access mode.

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
                access = "s3" if in_region and self.cloud_hosted else "https"

        canonical_access = normalize_access(access)
        https_links = self._filter_related_links("GET DATA")
        s3_links = self._filter_related_links("GET DATA VIA DIRECT ACCESS")

        if canonical_access == "https":
            return https_links

        if s3_links:
            return s3_links

        if self.cloud_hosted:
            # Some cloud collections advertise only HTTPS links; derive S3
            # links from each HTTPS link when possible.
            return self._derive_s3_link(https_links)
        return []

    @property
    def dataviz_links(self) -> List[str]:
        """Placeholder.

        Returns:
            The data visualization links, usually the browse images.
        """
        links = self._filter_related_links("GET RELATED VISUALIZATION")
        return links

    @property
    def data_type(self) -> str:
        """Return the granule's data file type(s).

        Derives distinct file types from the extensions of the granule's
        ``GET DATA`` links, e.g. ``"COG"`` for HLS or ``"NetCDF"`` for EMIT.
        Browse/thumbnail images (``GET RELATED VISUALIZATION``) are appended
        with a ``(thumbs)`` suffix so multi-file granules read like
        ``"COG, JPEG(thumbs)"``. When an extension has no known type, the
        extension itself is listed (e.g. ``".jgr"``). Falls back to
        ``"Unknown"`` when no link has a recognizable extension.

        Returns:
            A comma-separated list of distinct file types for this granule.
        """
        types: List[str] = []
        for link in self.data_links():
            ftype = _type_for_extension(_extension_from_url(link))
            if ftype and ftype not in types:
                types.append(ftype)

        thumb_types: List[str] = []
        for link in self.dataviz_links:
            ftype = _type_for_extension(_extension_from_url(link))
            if ftype and ftype not in thumb_types:
                thumb_types.append(ftype)
        if thumb_types:
            types.append(f"{', '.join(thumb_types)}(thumbs)")

        return ", ".join(types) if types else "Unknown"

    def to_dict(self) -> Dict[str, Any]:
        """Convert the granule to a plain dictionary.

        This is useful for serialization, especially when shipping granule
        metadata to distributed workers.

        Returns:
            A dictionary representation of the granule.
        """
        return dict(self)

    def to_stac(self, access: str = "https") -> pystac.Item:
        """Convert the CMR UMM granule to a STAC Item.

        Returns a pystac Item following the STAC spec. Use ``pystac_client``
        or pystac's serialization methods to work with it further.

        Parameters:
            access: Which URL scheme to use for the data asset ``href``:
                - ``"https"`` (default): prefer public HTTPS URLs, so items
                  work outside AWS (e.g. with ``stac_load``); S3 is kept as
                  the asset ``alternate``.
                - ``"s3"``: prefer direct S3 URLs for in-region access.
                - ``"auto"``: prefer S3 for cloud-hosted granules, HTTPS
                  otherwise.

        Returns:
            A pystac Item.
        """
        if access not in ("auto", "https", "s3"):
            raise ValueError(
                f"Invalid access {access!r}; expected 'auto', 'https', or 's3'."
            )

        # Extract basic metadata
        concept_id = self["meta"]["concept-id"]
        granule_ur = self["umm"].get("GranuleUR", concept_id)
        collection_ref = self["umm"].get("CollectionReference", {})

        # Get collection ID from reference
        collection_id = collection_ref.get(
            "ShortName", collection_ref.get("EntryTitle", "unknown")
        )
        collection_version = collection_ref.get("Version", "")
        if collection_version:
            collection_id = f"{collection_id}_v{collection_version}"

        # Extract temporal
        temporal_extent = self["umm"].get("TemporalExtent", {})
        datetime_str = self._extract_item_datetime(temporal_extent)
        start_datetime, end_datetime = self._extract_item_datetime_range(
            temporal_extent
        )

        # Extract spatial
        spatial_extent = self["umm"].get("SpatialExtent", {})
        geometry, bbox = self._extract_item_geometry(spatial_extent)

        # Build properties
        properties: Dict[str, Any] = {}

        # Set datetime (STAC requires either datetime or start/end)
        if datetime_str:
            properties["datetime"] = datetime_str
        elif start_datetime:
            properties["datetime"] = None
            properties["start_datetime"] = start_datetime
            properties["end_datetime"] = end_datetime
        else:
            # pystac requires start/end to be present when datetime is null
            properties["datetime"] = None
            properties["start_datetime"] = None
            properties["end_datetime"] = None

        # Add size if available
        if self.size > 0:
            properties["file:size"] = int(
                self.size * 1024 * 1024
            )  # Convert MB to bytes

        # Build STAC Item
        stac_item: Dict[str, Any] = {
            "type": "Feature",
            "stac_version": "1.0.0",
            "stac_extensions": [],
            "id": granule_ur,
            "geometry": geometry,
            "bbox": bbox,
            "properties": properties,
            "links": self._build_item_links(collection_id),
            "assets": self._build_item_assets(access=access),
            "collection": collection_id,
        }

        # Add CMR-specific properties
        stac_item["properties"]["cmr:concept_id"] = concept_id
        stac_item["properties"]["cmr:provider_id"] = self["meta"].get("provider-id", "")

        return pystac.Item.from_dict(stac_item)

    def _extract_item_datetime(self, temporal_extent: Dict[str, Any]) -> Optional[str]:
        """Extract a single datetime from temporal extent."""
        if "SingleDateTime" in temporal_extent:
            return temporal_extent["SingleDateTime"]

        range_dt = temporal_extent.get("RangeDateTime", {})
        if "BeginningDateTime" in range_dt:
            return range_dt["BeginningDateTime"]

        return None

    def _extract_item_datetime_range(
        self, temporal_extent: Dict[str, Any]
    ) -> tuple[Optional[str], Optional[str]]:
        """Extract datetime range from temporal extent."""
        range_dt = temporal_extent.get("RangeDateTime", {})
        start = range_dt.get("BeginningDateTime")
        end = range_dt.get("EndingDateTime")
        return start, end

    def _extract_item_geometry(
        self, spatial_extent: Dict[str, Any]
    ) -> tuple[Optional[Dict[str, Any]], Optional[List[float]]]:
        """Extract geometry and bbox from spatial extent."""
        h_domain = spatial_extent.get("HorizontalSpatialDomain", {})
        geometry_data = h_domain.get("Geometry", {})

        # Try bounding rectangles first
        bounding_rects = geometry_data.get("BoundingRectangles", [])
        if bounding_rects:
            rect = bounding_rects[0]
            west = rect.get("WestBoundingCoordinate", -180.0)
            south = rect.get("SouthBoundingCoordinate", -90.0)
            east = rect.get("EastBoundingCoordinate", 180.0)
            north = rect.get("NorthBoundingCoordinate", 90.0)

            bbox = [west, south, east, north]
            geometry = {
                "type": "Polygon",
                "coordinates": [
                    [
                        [west, south],
                        [east, south],
                        [east, north],
                        [west, north],
                        [west, south],
                    ]
                ],
            }
            return geometry, bbox

        # Try GPolygons
        gpolygons = geometry_data.get("GPolygons", [])
        if gpolygons:
            polygon = gpolygons[0]
            boundary = polygon.get("Boundary", {}).get("Points", [])
            if boundary:
                coords = [
                    [p.get("Longitude", 0), p.get("Latitude", 0)] for p in boundary
                ]
                # Close the polygon if not closed
                if coords and coords[0] != coords[-1]:
                    coords.append(coords[0])

                # Calculate bbox
                lons = [c[0] for c in coords]
                lats = [c[1] for c in coords]
                bbox = [min(lons), min(lats), max(lons), max(lats)]

                geometry = {"type": "Polygon", "coordinates": [coords]}
                return geometry, bbox

        # Try points
        points = geometry_data.get("Points", [])
        if points:
            point = points[0]
            lon = point.get("Longitude", 0)
            lat = point.get("Latitude", 0)
            geometry = {"type": "Point", "coordinates": [lon, lat]}
            bbox = [lon, lat, lon, lat]
            return geometry, bbox

        # Default to global extent
        return None, None

    def _build_item_links(self, collection_id: str) -> List[Dict[str, str]]:
        """Build STAC links for the item."""
        links: List[Dict[str, str]] = []

        # Self link
        concept_id = self["meta"]["concept-id"]
        links.append(
            {
                "rel": "self",
                "href": f"https://cmr.earthdata.nasa.gov/search/concepts/{concept_id}",
                "type": "application/json",
            }
        )

        # Parent collection link
        links.append(
            {
                "rel": "parent",
                "href": f"#/collections/{collection_id}",
                "type": "application/json",
            }
        )

        # Collection link
        links.append(
            {
                "rel": "collection",
                "href": f"#/collections/{collection_id}",
                "type": "application/json",
            }
        )

        return links

    def _extract_asset_key(self, url: str, url_type: str = "") -> str:
        """Extract a meaningful asset key from a URL.

        This method attempts to derive a meaningful name from the URL by:
        1. Extracting the filename from the URL
        2. Removing the granule ID prefix if present (to get band/layer suffix)
        3. Handling special cases like thumbnails via the URL type

        Parameters:
            url: The URL to extract the key from
            url_type: The CMR URL type (e.g., "GET DATA", "GET RELATED VISUALIZATION")

        Returns:
            A meaningful asset key (e.g., "B02", "Fmask", "thumbnail")
        """
        # Extract filename from URL
        filename = url.split("/")[-1] if "/" in url else url

        # Remove file extension for cleaner keys
        known_extensions = (
            ".tif",
            ".tiff",
            ".nc",
            ".nc4",
            ".h5",
            ".hdf",
            ".he5",
            ".zarr",
            ".png",
            ".jpg",
            ".jpeg",
            ".xml",
            ".json",
        )
        base_name = filename
        for ext in known_extensions:
            if filename.lower().endswith(ext):
                base_name = filename[: -len(ext)]
                break

        # Get granule ID for prefix removal
        granule_id = self["umm"].get("GranuleUR", "")

        # Try to extract a suffix after the granule ID (common pattern for multi-band data)
        # E.g., "HLS.L30.T10SEG.2023001T185019.v2.0.B02.tif" -> "B02"
        if granule_id and base_name.startswith(granule_id):
            suffix = base_name[len(granule_id) :]
            # Remove leading dots or underscores
            suffix = suffix.lstrip("._-")
            if suffix:
                return suffix

        # For visualization links, use "browse" as the key when filename matches granule ID
        # This prevents browse images from being named "data"
        if url_type == "GET RELATED VISUALIZATION":
            if base_name == granule_id or not base_name:
                return "browse"
            return base_name

        # If filename matches granule ID, use "data" as the key
        if base_name == granule_id:
            return "data"

        # Otherwise, use the base filename as the key
        return base_name if base_name else "data"

    def _infer_media_type(self, url: str) -> Optional[str]:
        """Infer MIME type from file extension.

        Parameters:
            url: The URL to infer media type from

        Returns:
            MIME type string or None if unknown
        """
        url_lower = url.lower()
        if url_lower.endswith(".nc") or url_lower.endswith(".nc4"):
            return "application/x-netcdf"
        elif url_lower.endswith(".tif") or url_lower.endswith(".tiff"):
            return "image/tiff; application=geotiff"
        elif (
            url_lower.endswith(".hdf")
            or url_lower.endswith(".h5")
            or url_lower.endswith(".he5")
        ):
            return "application/x-hdf5"
        elif url_lower.endswith(".zarr"):
            return "application/vnd+zarr"
        elif url_lower.endswith(".png"):
            return "image/png"
        elif url_lower.endswith(".jpg") or url_lower.endswith(".jpeg"):
            return "image/jpeg"
        return None

    def _build_item_assets(self, access: str = "auto") -> Dict[str, Dict[str, Any]]:
        """Build STAC assets from granule data links.

        This method creates STAC-compatible asset dictionaries with meaningful
        keys derived from filenames (e.g., "B02", "Fmask" for HLS data).
        S3 and HTTPS versions of the same file are grouped together, with
        the preferred access method as primary href and the other as alternate.

        Args:
            access: Which URL scheme to prefer as the primary asset href:
                - ``"auto"``: prefer S3 for cloud-hosted granules, HTTPS otherwise
                - ``"https"``: prefer public HTTPS URLs (good for out-of-region access)
                - ``"s3"``: prefer direct S3 URLs (good for in-region AWS access)
        """
        assets: Dict[str, Dict[str, Any]] = {}

        # Group URLs by their asset key
        # Key: asset_key, Value: {"s3": url, "https": url}
        asset_groups: Dict[str, Dict[str, str]] = {}

        # Process data links
        if "RelatedUrls" in self["umm"]:
            for url_info in self["umm"]["RelatedUrls"]:
                url = url_info.get("URL", "")
                url_type = url_info.get("Type", "")

                if not url:
                    continue

                # Only process data links here
                if url_type not in ("GET DATA", "GET DATA VIA DIRECT ACCESS"):
                    continue

                asset_key = self._extract_asset_key(url, url_type)

                if asset_key not in asset_groups:
                    asset_groups[asset_key] = {}

                if url.startswith("s3://"):
                    asset_groups[asset_key]["s3"] = url
                else:
                    asset_groups[asset_key]["https"] = url

        # Build assets from groups
        for asset_key, urls in asset_groups.items():
            has_s3 = "s3" in urls
            has_https = "https" in urls

            # Select primary/alternate based on the requested access strategy
            if access == "https":
                primary_url = urls.get("https") or urls.get("s3", "")
                alternate_url = urls.get("s3") if has_s3 and has_https else None
            elif access == "s3":
                primary_url = urls.get("s3") or urls.get("https", "")
                alternate_url = urls.get("https") if has_s3 and has_https else None
            else:  # "auto": prefer S3 for cloud-hosted data, HTTPS otherwise
                if self.cloud_hosted and has_s3:
                    primary_url = urls["s3"]
                    alternate_url = urls.get("https")
                else:
                    primary_url = urls.get("https") or urls.get("s3", "")
                    alternate_url = urls.get("s3") if has_https else None

            asset: Dict[str, Any] = {
                "href": primary_url,
                "roles": ["data"],
            }

            # Add cloud-optimized role for S3 links
            if primary_url.startswith("s3://"):
                asset["roles"].append("cloud-optimized")

            # Add HTTPS as alternate if we have both
            if alternate_url:
                asset["alternate"] = {"href": alternate_url}

            # Infer media type
            media_type = self._infer_media_type(primary_url)
            if media_type:
                asset["type"] = media_type

            assets[asset_key] = asset

        # Add browse/thumbnail assets
        viz_links = self.dataviz_links
        for link in viz_links:
            asset_key = self._extract_asset_key(link, "GET RELATED VISUALIZATION")

            # Handle duplicate keys by appending number
            if asset_key in assets:
                counter = 1
                while f"{asset_key}_{counter}" in assets:
                    counter += 1
                asset_key = f"{asset_key}_{counter}"

            assets[asset_key] = {
                "href": link,
                "roles": ["thumbnail"],
                "type": "image/png" if link.endswith(".png") else "image/jpeg",
            }

        return assets

    def assets(self) -> List[Any]:
        """Get all assets from this granule as Asset objects.

        Returns a list of Asset objects representing all granule files,
        including data files, thumbnails, and metadata. Assets are created
        from STAC-style asset dictionaries built by _build_item_assets().

        Returns:
            List of Asset objects representing granule files

        Example:
            >>> granule = results[0]
            >>> all_assets = granule.assets()
            >>> data_assets = [a for a in all_assets if a.is_data()]
        """
        from earthaccess.store.assets import Asset

        assets_dict = self._build_item_assets()
        return [
            Asset(
                href=asset.get("href", ""),
                title=asset.get("title"),
                description=asset.get("description"),
                type=asset.get("type"),
                roles=asset.get("roles", []),
                size=asset.get("size"),
                alternate=(
                    asset.get("alternate", {}).get("href")
                    if isinstance(asset.get("alternate"), dict)
                    else None
                ),
            )
            for asset in assets_dict.values()
        ]

    def data_assets(self) -> List[Any]:
        """Get only data assets (those with 'data' role).

        Convenience method for filtering assets to only those with the 'data' role.
        Excludes thumbnails, browse images, and other metadata assets.

        Returns:
            List of Asset objects with 'data' role only

        Example:
            >>> granule = results[0]
            >>> data_files = granule.data_assets()
            >>> print(f"Found {len(data_files)} data files")
        """
        return [a for a in self.assets() if a.is_data()]


# =============================================================================
# SearchResults - Lazy Pagination Wrapper
# =============================================================================


def _rebuild_query_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize JSON-loaded params back to the tuple form query builders expect.

    After a save/load round-trip, multi-value params (``temporal``,
    ``bounding_box``, ``point``, ``cloud_cover``, ``orbit_number``) arrive as
    JSON lists. The query builders expect tuples (or a 2-element list for
    ``temporal``). This restores the tuple/list form so the params can be fed
    back to ``GranuleQuery().parameters(**params)``.
    """
    params = dict(params)

    temporal = params.get("temporal")
    if isinstance(temporal, list) and len(temporal) == 2:
        params["temporal"] = (temporal[0], temporal[1])

    for key in ("bounding_box", "point", "cloud_cover", "orbit_number"):
        val = params.get(key)
        if isinstance(val, list) and val:
            params[key] = tuple(val)

    return params


class SearchResults:
    """Base class for CMR search results with lazy pagination.

    This class provides an interface for iterating through large result sets
    without loading all results into memory at once. It supports both direct
    iteration and page-by-page iteration.

    SearchResults fetches data lazily from NASA's CMR (Common Metadata Repository),
    only requesting pages of results as they are accessed. This enables efficient
    processing of large result sets that may contain thousands of items.

    Attributes:
        query: The CMR query object (DataGranules or DataCollections)
        limit: Maximum number of results to fetch (None for unlimited)

    Examples:
        Iterate through results (streams; the object only retains a bounded
        window, not the whole set):

        >>> results = earthaccess.search_data(short_name="ATL06", count=100)
        >>> for granule in results:  # doctest: +SKIP
        ...     print(granule["meta"]["concept-id"])

        Materialize every result explicitly with ``all()`` or ``list()``:

        >>> all_granules = results.all()  # doctest: +SKIP

        Get total matching results in CMR (does not download them):

        >>> print(f"Total available: {results.total()}")  # doctest: +SKIP

        Access by index (lazy fetches as needed):

        >>> first = results[0]  # doctest: +SKIP
        >>> fifth = results[4]  # doctest: +SKIP

        Reset back to the initial prefetch after a long iteration:

        >>> results.reset()  # doctest: +SKIP
    """

    __module__ = "earthaccess.search"

    def __init__(
        self,
        query: Any,
        limit: Optional[int] = None,
        prefetch: int = 20,
        page_size: int = 2000,
        query_kwargs: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialize SearchResults.

        Parameters:
            query: The CMR query object that will be used to fetch results
            limit: Maximum number of results to fetch, None for unlimited
            prefetch: Number of results to fetch immediately (default: 20).
                Set to 0 to disable prefetching.
            page_size: Results per CMR page when streaming/iterating
                (default: 2000, CMR's maximum). Must be between 1 and 2000.
            query_kwargs: The original, replayable kwargs used to run the
                search (e.g. those passed to ``search_data()``). Persisted by
                :meth:`save` so the query can be re-run later.
        """
        if page_size < 1 or page_size > 2000:
            raise ValueError("page_size must be between 1 and 2000")

        self.query = query
        self.limit = limit
        self._query_kwargs: Optional[Dict[str, Any]] = query_kwargs
        self._cached_results: List[Union[DataGranule, DataCollection]] = []
        self._total_hits: Optional[int] = None
        self._exhausted = False
        self._materialized = False
        self._window: int = page_size
        self._cache_start: int = 0
        self._offset: int = 0
        self._last_search_after: Optional[str] = None
        self._initial_search_after: Optional[str] = None
        self.verification: Optional[Dict[str, Any]] = None
        self._stored_fingerprint: Optional[str] = None
        self._saved_at: Optional[str] = None
        self._source: Optional[str] = None

        # Prefetch initial results
        if prefetch > 0:
            fetch_count = prefetch if limit is None else min(prefetch, limit)
            if fetch_count > 0:
                initial_page = self._fetch_page(fetch_count)
                self._cached_results.extend(initial_page)
                # Remember the cursor after the prefetched window so a later
                # materialization/iteration can resume right after it.
                self._initial_search_after = self._last_search_after
                # Mark exhausted if we got fewer results than requested,
                # or if we've reached the limit
                if len(initial_page) < fetch_count or (
                    limit is not None and len(self._cached_results) >= limit
                ):
                    self._exhausted = True
                    # The whole requested set is present.
                    self._materialized = True

    def total(self) -> int:
        """Return the total number of results matching the query in CMR.

        This makes a request to CMR to get the hit count if not already cached.
        Use this to know how many total results exist before fetching them all.

        Returns:
            The total number of results matching the query

        Examples:
            >>> results = earthaccess.search_data(short_name="ATL06", count=100)
            >>> print(f"Total matching: {results.total()}")  # doctest: +SKIP
        """
        if self._total_hits is None:
            self._total_hits = int(self.query.hits())
        return self._total_hits

    @property
    def loaded(self) -> int:
        """Number of results currently materialized/retained in memory."""
        return len(self._cached_results)

    @property
    def offset(self) -> int:
        """Number of results already consumed from the result set.

        This is the read position of the stream: how many results have been
        iterated so far. It equals the index of the first result that has not
        been read yet, so after iterating over N results ``offset == N``.
        """
        return self._offset

    @property
    def source(self) -> str:
        """Where these results come from.

        Returns ``"CMR PROD"`` or ``"CMR UAT"`` for a live search, or the path
        of the payload file when the results were loaded with :meth:`load`.
        """
        if self._source is not None:
            return self._source
        try:
            url = str(self.query._build_url())
        except (AttributeError, TypeError):
            # Legacy/new query builders without _build_url run against PROD.
            return "CMR PROD"
        if "uat" in url.lower():
            return "CMR UAT"
        return "CMR PROD"

    def hits(self) -> int:
        """Return the total number of results matching the query in CMR.

        This is an alias for :meth:`total` for consistency with the CMR API.

        Returns:
            The total number of results matching the query
        """
        return self.total()

    def __len__(self) -> int:
        """Return the number of currently cached/materialized results.

        This returns the count of results that have been fetched so far,
        not the total number of matching results in CMR. Use `total()`
        to get the total number of matching results.

        Returns:
            The number of results currently loaded in memory
        """
        return len(self._cached_results)

    def __iter__(self) -> Iterator[Union[DataGranule, DataCollection]]:
        """Iterate through all results, fetching pages as needed.

        Iteration is a **stream**: each ``__iter__`` starts a fresh scan from
        the beginning of the result set. The cache holds at most one page of
        results (``window``, default 2000) at a time: each page is fetched in
        a single request and its items are yielded one by one, then the cache
        slides to the next page. This keeps memory bounded while still
        streaming large result sets.

        CMR may return fewer results than requested even when more matches
        exist, so a short page is *not* treated as the end of the result set:
        iteration continues until CMR returns an empty page, the total number
        of matches is reached, or ``limit`` is met.

        Fully materialized results (after :meth:`all` or a full stream that
        reached the end of a small set) are replayed from cache.

        Yields:
            DataGranule or DataCollection instances
        """
        # If the whole result set is already in memory, replay it.
        if self._materialized:
            self._offset = 0
            for i, result in enumerate(self._cached_results, start=1):
                self._offset = i
                yield result
            return

        # Fresh scan from the beginning of the result set.
        page_size = self._window
        self._cached_results = []
        self._cache_start = 0
        self._offset = 0
        self._exhausted = False
        self._last_search_after = None
        results_yielded = 0
        search_after = None

        while True:
            # Check limit (None or a negative limit means "unlimited").
            if self.limit is not None and results_yielded >= self.limit:
                self._exhausted = True
                break

            # Always fetch a full page: CMR is most efficient at page_size
            # (2000). We only enforce the limit when yielding below.
            page = self._fetch_page(page_size, search_after)
            if not page:
                self._exhausted = True
                break

            # Cache holds this page (a sliding window of at most `page_size`).
            self._cached_results = list(page)
            self._cache_start = results_yielded
            self._materialized = False

            for result in page:
                results_yielded += 1
                self._offset = results_yielded
                yield result
                if self.limit is not None and results_yielded >= self.limit:
                    self._exhausted = True
                    break
                if self._exhausted:
                    break

            if self._exhausted:
                break

            # We reached the end of the result set (total hits fetched).
            if self._total_hits is not None and results_yielded >= self._total_hits:
                self._exhausted = True
                break

            search_after = self._last_search_after

    def _materialize(self) -> List[Union["DataGranule", "DataCollection"]]:
        """Fetch and cache the complete result set (up to ``limit``).

        Used by methods that need the full loaded set (``all()``, ``save()``,
        verification) rather than the bounded streaming window. Idempotent:
        once materialized, later calls return the cached list.

        Returns:
            The fully materialized list of results.
        """
        if self._materialized:
            return self._cached_results

        # Fresh full scan from the beginning of the result set.
        page_size = self._window
        search_after: Optional[str] = None
        collected: List[Union["DataGranule", "DataCollection"]] = []
        results_fetched = 0

        while True:
            if self.limit is not None and results_fetched >= self.limit:
                break
            want = page_size
            if self.limit is not None:
                want = min(page_size, self.limit - results_fetched)
            if want <= 0:
                break

            page = self._fetch_page(want, search_after)
            if not page:
                break
            for result in page:
                results_fetched += 1
                collected.append(result)
            if len(page) < want:
                break
            search_after = self._last_search_after

        self._cached_results = collected
        self._cache_start = 0
        self._exhausted = True
        self._materialized = True
        return self._cached_results

    def all(self) -> List[Union["DataGranule", "DataCollection"]]:
        """Fetch and return all results as a list.

        This method fetches all remaining results from CMR and returns them
        as a list, caching them for subsequent use.

        Example:
            >>> results = earthaccess.search_data(short_name="ATL06", count=100)
            >>> granules = results.all()
            >>> print(f"Got {len(granules)} granules")

        Returns:
            List of DataGranule or DataCollection objects

        Note:
            For very large result sets this loads everything into memory. Use
            ``items()`` or ``pages()`` to process results incrementally without
            materializing the whole set.
        """
        return self._materialize()

    def filter(
        self,
        filter_obj: Optional["GranuleFilter"] = None,
        predicate: Optional[Callable[["DataGranule"], bool]] = None,
        **kwargs: Any,
    ) -> List["DataGranule"]:
        """Filter results and return matching granules as a list.

        This method fetches all results and applies the specified filter(s).
        You can pass a GranuleFilter object, a predicate function, or use
        keyword arguments as shortcuts for common filter criteria.

        Parameters:
            filter_obj: A GranuleFilter instance with filter criteria
            predicate: A function that takes a DataGranule and returns bool
            **kwargs: Shortcut filter criteria:
                - min_size: Minimum size in MB
                - max_size: Maximum size in MB
                - cloud_hosted: Boolean for cloud hosting status

        Returns:
            List of DataGranule objects matching the filter criteria

        Examples:
            >>> results = earthaccess.search_data(short_name="ATL06", count=100)

            >>> # Using kwargs shortcuts
            >>> large = results.filter(min_size=100)
            >>> cloud = results.filter(cloud_hosted=True)

            >>> # Using a GranuleFilter
            >>> f = GranuleFilter(min_size=50, max_size=200)
            >>> filtered = results.filter(f)

            >>> # Using a predicate function
            >>> filtered = results.filter(predicate=lambda g: "ATL06" in str(g))

            >>> # Chaining from search
            >>> granules = earthaccess.search_data(
            ...     short_name="ATL06", count=100
            ... ).filter(cloud_hosted=True, min_size=10)
        """
        # Build filter from kwargs if no filter_obj provided
        if filter_obj is None and (kwargs or predicate):
            filter_obj = GranuleFilter(
                min_size=kwargs.get("min_size"),
                max_size=kwargs.get("max_size"),
                cloud_hosted=kwargs.get("cloud_hosted"),
                start_date=kwargs.get("start_date"),
                end_date=kwargs.get("end_date"),
                predicate=predicate,
            )

        # Fetch all results
        all_results = self.all()

        # If no filter, return all
        if filter_obj is None:
            return all_results  # type: ignore[return-value]

        # Apply filter (matches() only accepts granules; collections are not
        # filterable so the result is guaranteed to be granule-only).
        matches = filter_obj.matches
        return [g for g in all_results if matches(g)]  # type: ignore[arg-type, misc]

    def items(self) -> Iterator[Union["DataGranule", "DataCollection"]]:
        """Iterate through all results one at a time.

        This method provides API parity with pystac-client's ItemSearch.items().
        Results are fetched lazily from CMR as needed.

        Example:
            >>> results = earthaccess.search_datasets(keyword="temperature")
            >>> for collection in results.items():
            ...     print(collection.concept_id)

        Yields:
            DataGranule or DataCollection: Individual result items

        Note:
            This is equivalent to iterating directly over the SearchResults object.
        """
        return iter(self)

    def pages(
        self, page_size: int = 2000
    ) -> Iterator[Sequence[Union["DataGranule", "DataCollection"]]]:
        """Iterate through results page by page.

        Each page is a list of results, allowing for batch processing.
        Pages are fetched lazily from the CMR.

        Parameters:
            page_size: Number of results per page (default: 2000, max: 2000)

        Yields:
            List[DataGranule] or List[DataCollection]: A page of results

        Example:
            >>> results = earthaccess.search_data(short_name="ATL06")
            >>> for page in results.pages(page_size=100):
            ...     print(f"Processing {len(page)} granules")
        """
        if page_size < 1 or page_size > 2000:
            raise ValueError("page_size must be between 1 and 2000")
        search_after = None
        results_fetched = 0

        while True:
            # Check if we've hit the limit
            if self.limit and results_fetched >= self.limit:
                break

            # Fetch next page
            page = self._fetch_page(
                min(page_size, self.limit - results_fetched)
                if self.limit
                else page_size,
                search_after,
            )

            if not page:
                break

            results_fetched += len(page)
            yield page

            # Check if this is a partial page
            if len(page) < page_size:
                break

            # Get search_after header for next page
            search_after = self._last_search_after

    def _fetch_page(
        self, page_size: int, search_after: Optional[str] = None
    ) -> Sequence[Union[DataGranule, DataCollection]]:
        """Fetch a single page of results from CMR.

        Parameters:
            page_size: Number of results to fetch
            search_after: Pagination token for retrieving subsequent pages

        Returns:
            A list of DataGranule or DataCollection instances
        """
        url = self.query._build_url()
        headers = dict(self.query.headers or {})

        if search_after:
            headers["cmr-search-after"] = search_after

        # Use getattr to safely access session (available on DataGranules/DataCollections subclasses)
        session = getattr(self.query, "session", requests.session())
        response = session.get(url, headers=headers, params={"page_size": page_size})

        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as ex:
            raise RuntimeError(ex.response.text) from ex

        # Store search_after for next page
        if cmr_search_after := response.headers.get("cmr-search-after"):
            self._last_search_after = cmr_search_after

        # Store total hits
        if self._total_hits is None and (hits := response.headers.get("CMR-Hits")):
            self._total_hits = int(hits)

        results_data = response.json().get("items", [])

        return self._convert_results(results_data)

    def _convert_results(
        self, results_data: List[Dict[str, Any]]
    ) -> Sequence[Union[DataGranule, DataCollection]]:
        """Convert raw CMR response data to result objects.

        Override in subclasses for type-specific conversion.

        Parameters:
            results_data: Raw JSON items from CMR response

        Returns:
            List of DataGranule or DataCollection instances
        """
        # Import here to avoid circular imports
        from earthaccess.search.queries import DataGranules, is_cloud_hosted

        if isinstance(self.query, DataGranules):
            return [
                DataGranule(item, cloud_hosted=is_cloud_hosted(item))
                for item in results_data
            ]
        else:
            return [DataCollection(item) for item in results_data]

    def __getitem__(
        self, index: Union[int, slice]
    ) -> Union[DataGranule, DataCollection, List[Union[DataGranule, DataCollection]]]:
        """Get item(s) by index or slice.

        Supports both single indexing and slicing. Results are fetched lazily
        as needed to satisfy the request.

        Parameters:
            index: Integer index or slice object

        Returns:
            Single result for integer index, list of results for slice

        Raises:
            IndexError: If index is out of range

        Examples:
            >>> results = earthaccess.search_data(short_name="ATL06", count=100)
            >>> first = results[0]  # Get first result
            >>> fifth = results[4]  # Get fifth result (fetches if needed)
            >>> batch = results[0:10]  # Get first 10 results
        """
        # Handle slices
        if isinstance(index, slice):
            # For slices, we need to ensure we have enough results
            # Use limit or a reasonable max if unbounded
            max_index = (
                index.stop if index.stop is not None else (self.limit or self.total())
            )
            self._ensure_cached(max_index)
            return self._cached_results[index]

        # Handle negative indices - need to know total to resolve
        if index < 0:
            total = self.total()
            index = total + index
            if index < 0:
                raise IndexError("Index out of range")

        # Ensure we have cached up to this index
        self._ensure_cached(index + 1)

        if index >= len(self._cached_results):
            raise IndexError(
                f"Index {index} out of range for {len(self._cached_results)} cached results"
            )

        return self._cached_results[index]

    def _ensure_cached(self, count: int) -> None:
        """Ensure at least `count` results are cached as a *prefix*.

        Random access (``[i]``, slicing, repr) needs a contiguous prefix of the
        result set starting at index 0. If the cache is currently a sliding
        window (from a streaming iteration), it is rebuilt from the beginning.

        Parameters:
            count: Minimum number of results to cache
        """
        if self._materialized:
            return

        # Apply limit constraint
        if self.limit:
            count = min(count, self.limit)

        # If the cache is a sliding window (does not start at 0), or the
        # previous scan ended mid-way, rebuild a proper prefix from the start.
        rebuild = self._cache_start != 0 or (self._exhausted and not self._materialized)
        if rebuild:
            self._cached_results = []
            self._cache_start = 0
            self._last_search_after = None
            self._initial_search_after = None
            self._exhausted = False

        if len(self._cached_results) >= count:
            return

        page_size = self._window
        search_after = self._initial_search_after

        # Fetch pages until we have enough cached
        while len(self._cached_results) < count and not self._exhausted:
            want = min(page_size, count - len(self._cached_results))
            page = self._fetch_page(want, search_after)
            if not page:
                self._exhausted = True
                break
            self._cached_results.extend(page)
            if len(page) < want or len(page) < page_size:
                self._exhausted = True
            search_after = self._last_search_after
            if self.limit and len(self._cached_results) >= self.limit:
                self._exhausted = True

    def __repr__(self) -> str:
        """String representation of SearchResults."""
        total = self._total_hits if self._total_hits is not None else "?"
        return (
            f"{self.__class__.__name__}(total={total}, loaded={self.loaded}, "
            f"offset={self.offset}, source={self.source})"
        )

    def _repr_html_(self) -> str:
        """Return HTML representation for Jupyter notebook display.

        Returns:
            HTML string with collapsible results table.
        """
        return _repr_search_results_html(self)

    def summary(self) -> dict:
        """Compute aggregated metadata for cached results.

        Only computes detailed statistics if total_hits < 10,000 to avoid
        performance issues with large result sets.

        Returns:
            Dictionary containing:
            - total: Total number of matching results in CMR
            - loaded: Number of results currently cached
            - total_size_mb: Total size of cached granules in MB (granules only)
            - cloud_count: Number of cloud-hosted results
            - temporal_range: Date range of cached results (if available)

        Examples:
            >>> results = earthaccess.search_data(short_name="ATL06", count=100)
            >>> summary = results.summary()
            >>> print(f"Total size: {summary['total_size_mb']:.1f} MB")
        """
        cached_count = len(self._cached_results)
        total_hits = self._total_hits

        # Basic info always available
        result: dict = {
            "total": total_hits,
            "loaded": cached_count,
            "total_size_mb": 0.0,
            "cloud_count": 0,
            "temporal_range": None,
        }

        # Only compute detailed stats if reasonable number of results
        if cached_count == 0 or (total_hits is not None and total_hits >= 10000):
            return result

        # Compute detailed statistics
        total_size = 0.0
        cloud_count = 0
        min_date: Optional[str] = None
        max_date: Optional[str] = None

        for item in self._cached_results:
            # Granules expose a size property; collections do not.
            size = getattr(item, "size", None)
            if isinstance(size, (int, float)):
                total_size += float(size)

            if getattr(item, "cloud_hosted", False):
                cloud_count += 1
            else:
                # For collections, check DirectDistributionInformation
                cloud_info = item.get("umm", {}).get("DirectDistributionInformation")
                if cloud_info:
                    cloud_count += 1

            # Extract temporal info
            temporal = item.get("umm", {}).get("TemporalExtent", {})
            range_dt = temporal.get("RangeDateTime", {})
            begin = range_dt.get("BeginningDateTime")
            end = range_dt.get("EndingDateTime")

            if begin:
                if min_date is None or begin < min_date:
                    min_date = begin
            if end:
                if max_date is None or end > max_date:
                    max_date = end

        # Format temporal range
        if min_date and max_date:
            min_short = min_date[:10] if len(min_date) >= 10 else min_date
            max_short = max_date[:10] if len(max_date) >= 10 else max_date
            result["temporal_range"] = f"{min_short} to {max_short}"
        elif min_date:
            result["temporal_range"] = f"{min_date[:10]} to present"

        result["total_size_mb"] = total_size
        result["cloud_count"] = cloud_count

        return result

    def to_stac(
        self, access: str = "https"
    ) -> List[Union[pystac.Item, pystac.Collection]]:
        """Convert all cached results to STAC objects.

        Converts each cached search result to its STAC representation.
        For granules, returns pystac Items. For collections, returns
        pystac Collections.

        Note: This only converts currently cached results. To convert all matching
        results, iterate over the SearchResults first to cache them.

        Parameters:
            access: Which URL scheme to use for granule data asset hrefs
                (``"https"`` by default, ``"s3"``, or ``"auto"``). Ignored for
                collections.

        Returns:
            List of pystac Item or Collection objects

        Examples:
            >>> results = earthaccess.search_data(short_name="ATL06", count=10)
            >>> stac_items = results.to_stac()
            >>> print(len(stac_items))  # 10 pystac Items
            >>> print(stac_items[0].id)  # granule ID

            >>> collections = earthaccess.search_datasets(keyword="temperature")
            >>> stac_collections = collections.to_stac()
            >>> print(stac_collections[0].id)  # collection ID
        """
        stac_items: List[Union[pystac.Item, pystac.Collection]] = []
        for item in self._cached_results:
            if isinstance(item, DataGranule):
                stac_items.append(item.to_stac(access=access))
            else:
                stac_items.append(item.to_stac())
        return stac_items

    def explore(self, max_items: int = 10000, **kwargs: Any) -> Any:
        """Display an interactive map with bounding boxes for cached results.

        This method creates a lonboard map visualization showing the spatial
        extent of cached search results. Requires the [widgets] extra.

        If the search was limited by a bounding box, that ROI is drawn as a
        thin red outline so you can contrast it with the granule geometries.

        Parameters:
            max_items: Maximum number of bounding boxes to display (default 10000)
            **kwargs: Additional arguments passed to lonboard (fill_color, line_color)

        Returns:
            A lonboard Map widget for display in Jupyter

        Raises:
            ImportError: If widget dependencies are not installed
            ValueError: If no results are cached

        Examples:
            >>> results = earthaccess.search_data(short_name="ATL06", count=100)
            >>> list(results)  # Fetch results first
            >>> results.explore()  # Display interactive map
        """
        from earthaccess.formatting.widgets import plot

        return plot(self, max_items=max_items, **kwargs)

    @deprecated("Use explore() instead")
    def plot(self, max_items: int = 10000, **kwargs: Any) -> Any:
        """Alias for :meth:`explore`; deprecated.

        .. deprecated:: 1.0.0a2
            Use :meth:`explore` instead.
        """
        return self.explore(max_items=max_items, **kwargs)

    def save(self, path: Union[str, Path], limit: int = 2000, offset: int = 0) -> Path:
        """Save this search to a compressed JSON payload.

        The payload records the replayable query parameters, how many results
        were saved, the CMR hit count, a fingerprint over the sorted
        concept-IDs, and the saved results. Reload with :meth:`load`, which by
        default is offline.

        By default only the **first page** of results (``limit=2000``) is saved
        and a warning is logged, so a huge search never materializes everything
        just to persist it. Pass ``limit=-1`` to save every match.

        Parameters:
            path: Where to write the payload (``.gz`` recommended).
            limit: How many results to save. ``2000`` (default) saves the first
                page of results; ``-1`` saves every result matching the search;
                a positive value resets the pagination and saves up to
                ``limit`` results, e.g. ``save(limit=1000)`` saves the first
                1000.
            offset: Number of results to skip before saving (default: 0). Use
                with ``limit`` to save a window, e.g. ``save(limit=1000,
                offset=2000)`` saves results 2000-2999.

        Returns:
            The path the payload was written to.

        Examples:
            >>> results = earthaccess.search_data(short_name="ATL06")
            >>> results.save("atl06_search.json.gz")        # default: first 2000
            >>> results.save("atl06_first1k.json.gz", limit=1000)  # first 1000
            >>> results.save("atl06_3k.json.gz", limit=1000, offset=2000)
        """
        from earthaccess.search.persistence import save

        return save(self, path, limit=limit, offset=offset)

    def reset(self, prefetch: int = 20) -> "SearchResults":
        """Reset this search back to its initial (prefetched) state.

        Clears any materialized/streamed results and re-fetches the first
        ``prefetch`` results, so a long iteration does not keep thousands of
        items resident in the object.

        Parameters:
            prefetch: How many results to prefetch after resetting
                (default: 20, matching the constructor).

        Returns:
            self, for chaining.

        Examples:
            >>> results = earthaccess.search_data(short_name="ATL06")
            >>> list(results)   # streams all matches
            >>> len(results)    # bounded window, not all matches
            >>> results.reset()
            >>> len(results)    # back to the initial 20 prefetched
        """
        self._cached_results = []
        self._cache_start = 0
        self._total_hits = None
        self._exhausted = False
        self._materialized = False
        self._last_search_after = None
        self._initial_search_after = None

        if prefetch > 0:
            fetch_count = prefetch if self.limit is None else min(prefetch, self.limit)
            if fetch_count > 0:
                initial_page = self._fetch_page(fetch_count)
                self._cached_results.extend(initial_page)
                self._initial_search_after = self._last_search_after
                if len(initial_page) < fetch_count or (
                    self.limit is not None and len(self._cached_results) >= self.limit
                ):
                    self._exhausted = True
                    self._materialized = True
        return self

    @classmethod
    def load(
        cls,
        path: Union[str, Path],
        verify: bool = False,
        *,
        offset: int = 0,
        limit: Optional[int] = 2000,
    ) -> "SearchResults":
        """Load a search saved with :meth:`save`.

        By default only the **first page** of saved results (``limit=2000``) is
        materialized and a warning is logged, so a large saved set is not
        loaded into memory all at once. Pass ``limit=None`` (or ``-1``) to load
        every saved result, or use ``offset``/``limit`` to page through the
        payload.

        By default no network request is made. Pass ``verify=True`` to re-run
        the saved query against CMR and compare with what was saved; the
        returned results expose a comparison report via
        ``results.verification``. Verification is only performed for a full
        load.

        Parameters:
            path: Path to the saved payload.
            verify: If True, re-run the query and compare fingerprints and hit
                counts. If False (default), load offline.
            offset: Number of saved results to skip before loading (default: 0).
            limit: Maximum number of saved results to load. ``2000`` (default)
                loads the first page; ``None`` or ``-1`` loads everything.

        Returns:
            A SearchResults instance with the requested slice of results.

        Examples:
            >>> results = SearchResults.load("atl06_search.json.gz")  # first page, offline
            >>> all_results = SearchResults.load("atl06_search.json.gz", limit=None)
            >>> page2 = SearchResults.load("atl06_search.json.gz", offset=2000, limit=2000)
        """
        from earthaccess.search.persistence import load

        return load(path, verify=verify, offset=offset, limit=limit)

    @property
    def query_params(self) -> Optional[Dict[str, Any]]:
        """The replayable kwargs this search was (or can be) run with.

        Set when the search is created from ``search_data()``/``search_datasets()``
        or restored from a saved payload. Pass them back to those functions to
        re-run the exact same query:

        .. code-block:: python

            fresh = earthaccess.search_data(**results.query_params)
        """
        return self._query_kwargs

    def rebuild_query(self) -> Any:
        """Rebuild a fresh query object for this search.

        Returns a new ``GranuleQuery``/``CollectionQuery`` instance configured
        with the same parameters, so the search can be re-run or inspected:

        .. code-block:: python

            query = loaded.rebuild_query()
            fresh = earthaccess.search_data(query=query)

        Returns:
            A query object matching this search's parameters.
        """
        from earthaccess.search.query import CollectionQuery, GranuleQuery

        params = _rebuild_query_params(self.query_params or {})
        if self._is_collections():
            return CollectionQuery().parameters(**params)
        return GranuleQuery().parameters(**params)

    def _is_collections(self) -> bool:
        """Return True if this container holds collections rather than granules."""
        if self._cached_results:
            return "GranuleUR" not in self._cached_results[0].get("umm", {})
        return isinstance(self, CollectionResults)


class GranuleResults(SearchResults):
    """Search results containing DataGranule objects.

    This subclass is returned by `earthaccess.search_data()` and provides
    granule-specific functionality. Results are fetched lazily from CMR in
    pages, so nothing is downloaded until you ask for it.

    Top-level things you can do with a `GranuleResults` object:

    - **Iterate** directly — ``for granule in results:`` yields granules,
      fetching more pages from CMR as needed. Iteration is a *stream*: the
      object only retains a bounded window (the last page), not every item.
    - **Materialize everything** — ``results.all()`` (or ``list(results)``)
      fetches every matching granule and returns them; ``all()`` also caches
      the full set on the object.
    - **Paginate through results** — ``results.pages()`` yields pages of
      granules, and ``results.items()`` yields them one at a time.
    - **See what is loaded** — ``len(results)`` is how many are currently
      retained (prefetch/window, or the full set after ``all()``);
      ``results.total()`` is the total number of matches CMR reports.
    - **Filter** — ``results.filter(...)`` keeps granules matching size,
      cloud hosting, or a custom predicate.
    - **Explore the map** — ``results.explore()`` shows an interactive map of
      the granules' spatial extents (requires the ``[widgets]`` extra;
      materialize first with ``results.all()`` to map more than the prefetched
      page).
    - **Reset** — ``results.reset()`` returns the object to its initial
      prefetch after a long iteration.

    Examples:
        >>> results = earthaccess.search_data(short_name="ATL06", count=10)
        >>> all_granules = results.all()  # materialize all 10 granules
        >>> len(results)
        10
        >>> results.explore()  # interactive map of their spatial extents
        >>> for granule in results:
        ...     print(granule.data_links())
    """

    __module__ = "earthaccess.search"

    def _convert_results(self, results_data: List[Dict[str, Any]]) -> List[DataGranule]:
        """Convert raw CMR response data to DataGranule objects."""
        from earthaccess.search.queries import is_cloud_hosted

        return [
            DataGranule(item, cloud_hosted=is_cloud_hosted(item))
            for item in results_data
        ]

    def __repr__(self) -> str:
        """String representation of GranuleResults."""
        total = self._total_hits if self._total_hits is not None else "?"
        return (
            f"GranuleResults(total={total}, loaded={self.loaded}, "
            f"offset={self.offset}, source={self.source})"
        )

    # --- ResultSet interface used by earthaccess plugins ---------------------
    # `search_data()` knows the collection short name from the query, so expose
    # it here; the granule UMM alone does not always carry it.

    @property
    def _query_params(self) -> Dict[str, Any]:
        return getattr(self.query, "params", {}) or {}

    @property
    def short_name(self) -> str:
        """Collection short name from the search query, if any."""
        return self._query_params.get("short_name", "") or ""

    @property
    def version(self) -> str:
        """Collection version from the search query, if any."""
        return self._query_params.get("version", "") or ""

    @property
    def collection_id(self) -> str:
        """Concept id of the collection the granules belong to."""
        params = self._query_params
        concept_id = params.get("concept_id") or params.get("collection_concept_id")
        if concept_id:
            return str(concept_id)
        for granule in self.all():
            return granule["meta"].get("collection-concept-id", "")
        return ""

    @property
    def ids(self) -> List[str]:
        """Granule concept ids."""
        return [granule["meta"]["concept-id"] for granule in self.all()]

    @property
    def native_ids(self) -> List[str]:
        """Granule URs (names)."""
        return [granule["umm"].get("GranuleUR", "") for granule in self.all()]

    def umm(self) -> List[Dict[str, Any]]:
        """Return the raw UMM-G records."""
        return [granule["umm"] for granule in self.all()]

    def services(self) -> List[Dict[str, Any]]:
        """UMM-S services associated with the collection (empty for granules)."""
        return []

    def list_services(self, context: Any = None) -> Any:
        """Return which installed plugins accept these results."""
        from earthaccess_plugin_spec import list_services

        return list_services(self, context)


class CollectionResults(SearchResults):
    """Search results containing DataCollection objects.

    This subclass is returned by `earthaccess.search_datasets()` and provides
    collection-specific functionality.

    Examples:
        >>> results = earthaccess.search_datasets(keyword="temperature", count=10)
        >>> for collection in results:
        ...     print(collection.concept_id)
    """

    __module__ = "earthaccess.search"

    def _convert_results(
        self, results_data: List[Dict[str, Any]]
    ) -> List[DataCollection]:
        """Convert raw CMR response data to DataCollection objects."""
        return [DataCollection(item) for item in results_data]

    def __repr__(self) -> str:
        """String representation of CollectionResults."""
        total = self._total_hits if self._total_hits is not None else "?"
        return (
            f"CollectionResults(total={total}, loaded={self.loaded}, "
            f"offset={self.offset}, source={self.source})"
        )
