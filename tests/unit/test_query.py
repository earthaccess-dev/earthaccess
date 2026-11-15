"""Unit tests for the new query package.

Tests consolidated using parametrization where appropriate.
"""

import datetime as dt

import pytest
from earthaccess.search.query import (
    BoundingBox,
    CollectionQuery,
    DateRange,
    GranuleQuery,
    Point,
    Polygon,
    ValidationResult,
)

# =============================================================================
# Geometry Types Tests (Parametrized)
# =============================================================================


class TestBoundingBox:
    """Tests for BoundingBox type."""

    def test_valid_bounding_box(self):
        """Test creating a valid bounding box."""
        bbox = BoundingBox(west=-180, south=-90, east=180, north=90)
        assert bbox.west == -180
        assert bbox.south == -90
        assert bbox.east == 180
        assert bbox.north == 90

    @pytest.mark.parametrize(
        "west,south,east,north,match",
        [
            (-200, 0, 0, 10, "west must be between"),
            (0, 50, 10, 10, "south.*must be less than or equal to north"),
        ],
        ids=["invalid_west", "south_greater_than_north"],
    )
    def test_bounding_box_validation(self, west, south, east, north, match):
        """Test that invalid bounding boxes raise errors."""
        with pytest.raises(ValueError, match=match):
            BoundingBox(west=west, south=south, east=east, north=north)

    def test_bounding_box_to_cmr(self):
        """Test CMR format conversion."""
        bbox = BoundingBox(west=-10, south=20, east=30, north=40)
        assert bbox.to_cmr() == "-10,20,30,40"

    def test_bounding_box_to_stac(self):
        """Test STAC format conversion."""
        bbox = BoundingBox(west=-10, south=20, east=30, north=40)
        assert bbox.to_stac() == [-10, 20, 30, 40]

    def test_bounding_box_from_coords(self):
        """Test creating from coordinate values."""
        bbox = BoundingBox.from_coords("-10", "20", "30", "40")
        assert bbox == BoundingBox(west=-10, south=20, east=30, north=40)


class TestPoint:
    """Tests for Point type."""

    def test_valid_point(self):
        """Test creating a valid point."""
        pt = Point(lon=-122.4, lat=37.8)
        assert pt.lon == -122.4
        assert pt.lat == 37.8

    @pytest.mark.parametrize(
        "lon,lat,match",
        [
            (200, 0, "lon must be between"),
            (0, -100, "lat must be between"),
        ],
        ids=["invalid_lon", "invalid_lat"],
    )
    def test_point_validation(self, lon, lat, match):
        """Test that invalid points raise errors."""
        with pytest.raises(ValueError, match=match):
            Point(lon=lon, lat=lat)

    def test_point_to_cmr(self):
        """Test CMR format conversion."""
        pt = Point(lon=-122.4, lat=37.8)
        assert pt.to_cmr() == "-122.4,37.8"

    def test_point_to_stac(self):
        """Test STAC format conversion."""
        pt = Point(lon=-122.4, lat=37.8)
        assert pt.to_stac() == [-122.4, 37.8]


class TestPolygon:
    """Tests for Polygon type."""

    def test_valid_polygon(self):
        """Test creating a valid polygon."""
        coords = ((0, 0), (10, 0), (10, 10), (0, 10), (0, 0))
        poly = Polygon(coordinates=coords)
        assert len(poly.coordinates) == 5

    @pytest.mark.parametrize(
        "coords,match",
        [
            (((0, 0), (10, 0), (10, 10)), "at least 4 points"),
            (((0, 0), (10, 0), (10, 10), (0, 10)), "must be closed"),
        ],
        ids=["too_few_points", "not_closed"],
    )
    def test_polygon_validation(self, coords, match):
        """Test that invalid polygons raise errors."""
        with pytest.raises(ValueError, match=match):
            Polygon(coordinates=coords)

    def test_polygon_to_cmr(self):
        """Test CMR format conversion."""
        poly = Polygon.from_coords([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)])
        assert poly.to_cmr() == "0.0,0.0,10.0,0.0,10.0,10.0,0.0,10.0,0.0,0.0"

    def test_polygon_to_stac(self):
        """Test STAC format conversion."""
        poly = Polygon.from_coords([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)])
        expected = [[[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]]]
        assert poly.to_stac() == expected


# =============================================================================
# DateRange Tests
# =============================================================================


class TestDateRange:
    """Tests for DateRange type."""

    @pytest.mark.parametrize(
        "start,end,expected_start,expected_end",
        [
            (
                "2020-01-01",
                "2020-12-31",
                dt.datetime(2020, 1, 1, 0, 0, 0),
                dt.datetime(2020, 12, 31, 23, 59, 59),
            ),
            (
                "2020-01",
                "2020-02",
                dt.datetime(2020, 1, 1, 0, 0, 0),
                dt.datetime(2020, 2, 29, 23, 59, 59),  # 2020 is leap year
            ),
        ],
        ids=["full_date", "month_format"],
    )
    def test_date_range_from_strings(self, start, end, expected_start, expected_end):
        """Test creating from date strings."""
        dr = DateRange.from_dates(start, end)
        assert dr.start == expected_start
        assert dr.end == expected_end

    def test_date_range_from_datetime(self):
        """Test creating from datetime objects."""
        start = dt.datetime(2020, 1, 1, 12, 0, 0)
        end = dt.datetime(2020, 12, 31, 12, 0, 0)
        dr = DateRange.from_dates(start, end)
        assert dr.start == start
        assert dr.end == end

    def test_date_range_validation(self):
        """Test that invalid date ranges raise errors."""
        with pytest.raises(ValueError, match="must be before"):
            DateRange.from_dates("2020-12-31", "2020-01-01")

    def test_date_range_to_cmr(self):
        """Test CMR format conversion."""
        dr = DateRange.from_dates("2020-01-01", "2020-01-31")
        assert "2020-01-01" in dr.to_cmr()
        assert "2020-01-31" in dr.to_cmr()

    def test_date_range_to_stac(self):
        """Test STAC format conversion."""
        dr = DateRange.from_dates("2020-01-01", "2020-01-31")
        stac_str = dr.to_stac()
        assert "/" in stac_str


# =============================================================================
# Validation Tests
# =============================================================================


class TestValidation:
    """Tests for validation utilities."""

    def test_validation_result_success(self):
        """Test successful validation."""
        result = ValidationResult()
        assert result.is_valid
        assert len(result.errors) == 0

    def test_validation_result_add_error(self):
        """Test adding errors."""
        result = ValidationResult()
        result.add_error("field", "is required")
        assert not result.is_valid
        assert len(result.errors) == 1
        assert result.errors[0].field == "field"

    def test_validation_result_raise_if_invalid(self):
        """Test raising on invalid."""
        result = ValidationResult()
        result.add_error("field", "is required")
        with pytest.raises(ValueError, match="field"):
            result.raise_if_invalid()


# =============================================================================
# GranuleQuery Tests (Parametrized)
# =============================================================================


class TestGranuleQuery:
    """Tests for GranuleQuery class."""

    @pytest.mark.parametrize(
        "method,args,key,expected",
        [
            ("short_name", ("ATL03",), "short_name", "ATL03"),
            ("version", ("006",), "version", "006"),
            ("concept_id", (["C123", "C456"],), "concept_id", ["C123", "C456"]),
            ("cloud_cover", (0, 50), "cloud_cover", "0.0,50.0"),
            ("point", (-122.4, 37.8), "point", "-122.4,37.8"),
            (
                "bounding_box",
                (-180, -90, 180, 90),
                "bounding_box",
                "-180.0,-90.0,180.0,90.0",
            ),
        ],
        ids=[
            "short_name",
            "version",
            "concept_id",
            "cloud_cover",
            "point",
            "bounding_box",
        ],
    )
    def test_granule_query_methods(self, method, args, key, expected):
        """Test GranuleQuery filter methods set correct CMR parameters."""
        q = getattr(GranuleQuery(), method)(*args)
        assert q.to_cmr()[key] == expected

    def test_temporal(self):
        """Test temporal filter."""
        q = GranuleQuery().temporal("2020-01", "2020-02")
        cmr = q.to_cmr()
        assert "temporal" in cmr
        assert "2020-01" in cmr["temporal"]

    def test_polygon(self):
        """Test polygon filter."""
        coords = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
        q = GranuleQuery().polygon(coords)
        assert "polygon" in q.to_cmr()

    def test_method_chaining(self):
        """Test that methods can be chained."""
        q = (
            GranuleQuery()
            .short_name("ATL03")
            .version("006")
            .temporal("2020-01", "2020-02")
            .bounding_box(-180, -90, 180, 90)
        )
        cmr = q.to_cmr()
        assert cmr["short_name"] == "ATL03"
        assert cmr["version"] == "006"
        assert "temporal" in cmr
        assert "bounding_box" in cmr

    def test_named_parameters(self):
        """Test construction with named parameters."""
        q = GranuleQuery(
            short_name="ATL03",
            version="006",
            temporal=("2020-01", "2020-02"),
        )
        cmr = q.to_cmr()
        assert cmr["short_name"] == "ATL03"
        assert cmr["version"] == "006"
        assert "temporal" in cmr

    @pytest.mark.parametrize(
        "method,args,stac_key,expected",
        [
            ("short_name", ("ATL03",), "collections", ["ATL03"]),
            ("bounding_box", (-10, 20, 30, 40), "bbox", [-10.0, 20.0, 30.0, 40.0]),
        ],
        ids=["collections", "bbox"],
    )
    def test_to_stac_conversions(self, method, args, stac_key, expected):
        """Test STAC conversion for various methods."""
        q = getattr(GranuleQuery(), method)(*args)
        stac = q.to_stac()
        assert stac[stac_key] == expected

    def test_to_stac_datetime(self):
        """Test STAC conversion for temporal."""
        q = GranuleQuery().temporal("2020-01-01", "2020-12-31")
        stac = q.to_stac()
        assert "datetime" in stac
        assert "/" in stac["datetime"]

    def test_to_stac_polygon(self):
        """Test STAC conversion for polygon."""
        coords = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
        q = GranuleQuery().polygon(coords)
        stac = q.to_stac()
        assert "intersects" in stac
        assert stac["intersects"]["type"] == "Polygon"

    def test_copy(self):
        """Test query copying."""
        q1 = GranuleQuery().short_name("ATL03")
        q2 = q1.copy()
        q2.short_name("ATL06")
        assert q1.to_cmr()["short_name"] == "ATL03"
        assert q2.to_cmr()["short_name"] == "ATL06"

    @pytest.mark.parametrize(
        "query_builder,is_valid",
        [
            (
                lambda: (
                    GranuleQuery().short_name("ATL03").bounding_box(-180, -90, 180, 90)
                ),
                True,
            ),
            (lambda: GranuleQuery().bounding_box(-180, -90, 180, 90), False),
        ],
        ids=["valid_with_collection", "invalid_spatial_only"],
    )
    def test_validation(self, query_builder, is_valid):
        """Test query validation."""
        q = query_builder()
        result = q.validate()
        assert result.is_valid == is_valid

    def test_repr(self):
        """Test string representation."""
        q = GranuleQuery().short_name("ATL03")
        repr_str = repr(q)
        assert "GranuleQuery" in repr_str
        assert "ATL03" in repr_str

    def test_equality(self):
        """Test query equality."""
        q1 = GranuleQuery().short_name("ATL03")
        q2 = GranuleQuery().short_name("ATL03")
        q3 = GranuleQuery().short_name("ATL06")
        assert q1 == q2
        assert q1 != q3


# =============================================================================
# CollectionQuery Tests (Parametrized)
# =============================================================================


class TestCollectionQuery:
    """Tests for CollectionQuery class."""

    @pytest.mark.parametrize(
        "method,args,key,expected",
        [
            (
                "keyword",
                ("sea surface temperature",),
                "keyword",
                "sea surface temperature",
            ),
            ("cloud_hosted", (True,), "cloud_hosted", True),
            ("has_granules", (True,), "has_granules", True),
            ("doi", ("10.5067/AQR50-3Q7CS",), "doi", "10.5067/AQR50-3Q7CS"),
            ("project", ("EMIT",), "project", "EMIT"),
            ("instrument", ("GEDI",), "instrument", "GEDI"),
        ],
        ids=["keyword", "cloud_hosted", "has_granules", "doi", "project", "instrument"],
    )
    def test_collection_query_methods(self, method, args, key, expected):
        """Test CollectionQuery filter methods set correct CMR parameters."""
        q = getattr(CollectionQuery(), method)(*args)
        assert q.to_cmr()[key] == expected

    def test_to_stac_keyword(self):
        """Test STAC conversion maps keyword to q."""
        q = CollectionQuery().keyword("temperature")
        stac = q.to_stac()
        assert stac["q"] == "temperature"

    def test_method_chaining(self):
        """Test that methods can be chained."""
        q = (
            CollectionQuery()
            .keyword("temperature")
            .cloud_hosted(True)
            .has_granules(True)
        )
        cmr = q.to_cmr()
        assert cmr["keyword"] == "temperature"
        assert cmr["cloud_hosted"] is True
        assert cmr["has_granules"] is True


# =============================================================================
# Query Interoperability Tests
# =============================================================================


class TestQueryInteroperability:
    """Test interoperability between query types."""

    def test_granule_query_with_all_params(self):
        """Test GranuleQuery with many parameters."""
        q = (
            GranuleQuery()
            .short_name("ATL03")
            .version("006")
            .provider("NSIDC_CPRD")
            .temporal("2020-01", "2020-02")
            .bounding_box(-180, -90, 180, 90)
            .cloud_cover(0, 50)
            .day_night_flag("day")
        )

        cmr = q.to_cmr()
        for key in [
            "short_name",
            "version",
            "provider",
            "temporal",
            "bounding_box",
            "cloud_cover",
            "day_night_flag",
        ]:
            assert key in cmr

        stac = q.to_stac()
        assert "collections" in stac
        assert "datetime" in stac
        assert "bbox" in stac
        assert "query" in stac
        assert "eo:cloud_cover" in stac["query"]

    def test_collection_query_with_all_params(self):
        """Test CollectionQuery with many parameters."""
        q = (
            CollectionQuery()
            .keyword("temperature")
            .short_name("SST")
            .cloud_hosted(True)
            .has_granules(True)
            .temporal("2020-01", "2020-12")
            .bounding_box(-180, -90, 180, 90)
        )

        cmr = q.to_cmr()
        for key in [
            "keyword",
            "short_name",
            "cloud_hosted",
            "has_granules",
            "temporal",
            "bounding_box",
        ]:
            assert key in cmr
