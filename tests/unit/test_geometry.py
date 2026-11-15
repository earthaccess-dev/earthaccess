"""Tests for geometry file support in the query module.

These tests verify the ability to read geometry files (GeoJSON, etc.)
and simplify complex geometries for use with CMR queries.
"""

import json
from pathlib import Path

import pytest
from earthaccess.search.query import GranuleQuery, Polygon
from earthaccess.search.query.geometry import (
    MAX_POLYGON_POINTS,
    _count_points,
    extract_polygon_coords,
    load_and_simplify_polygon,
    read_geometry_file,
    simplify_geometry,
)


class TestReadGeometryFile:
    """Tests for reading geometry files."""

    def test_read_geojson_polygon(self, tmp_path: Path) -> None:
        """Read a simple GeoJSON polygon file."""
        geojson = {
            "type": "Polygon",
            "coordinates": [[[-10, -10], [10, -10], [10, 10], [-10, 10], [-10, -10]]],
        }
        file_path = tmp_path / "test.geojson"
        file_path.write_text(json.dumps(geojson))

        result = read_geometry_file(file_path)

        assert result["type"] == "Polygon"
        assert len(result["coordinates"][0]) == 5

    def test_read_geojson_feature(self, tmp_path: Path) -> None:
        """Read a GeoJSON Feature."""
        geojson = {
            "type": "Feature",
            "properties": {"name": "test"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[-10, -10], [10, -10], [10, 10], [-10, 10], [-10, -10]]
                ],
            },
        }
        file_path = tmp_path / "test.geojson"
        file_path.write_text(json.dumps(geojson))

        result = read_geometry_file(file_path)

        assert result["type"] == "Polygon"

    def test_read_geojson_feature_collection(self, tmp_path: Path) -> None:
        """Read a GeoJSON FeatureCollection (uses first feature)."""
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [[-10, -10], [10, -10], [10, 10], [-10, 10], [-10, -10]]
                        ],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [[-20, -20], [20, -20], [20, 20], [-20, 20], [-20, -20]]
                        ],
                    },
                },
            ],
        }
        file_path = tmp_path / "test.geojson"
        file_path.write_text(json.dumps(geojson))

        result = read_geometry_file(file_path)

        # Should use first feature
        assert result["type"] == "Polygon"
        assert result["coordinates"][0][0] == [-10, -10]

    def test_read_json_extension(self, tmp_path: Path) -> None:
        """Read geometry from .json file (not just .geojson)."""
        geojson = {
            "type": "Polygon",
            "coordinates": [[[-10, -10], [10, -10], [10, 10], [-10, 10], [-10, -10]]],
        }
        file_path = tmp_path / "test.json"
        file_path.write_text(json.dumps(geojson))

        result = read_geometry_file(file_path)

        assert result["type"] == "Polygon"

    def test_read_file_not_found(self) -> None:
        """Raise error for non-existent file."""
        with pytest.raises(FileNotFoundError):
            read_geometry_file("/nonexistent/path/file.geojson")

    def test_read_unsupported_format(self, tmp_path: Path) -> None:
        """Raise error for unsupported file format."""
        file_path = tmp_path / "test.xyz"
        file_path.write_text("some content")

        with pytest.raises(ValueError, match="Unsupported geometry file format"):
            read_geometry_file(file_path)

    def test_read_empty_feature_collection(self, tmp_path: Path) -> None:
        """Raise error for empty FeatureCollection."""
        geojson = {"type": "FeatureCollection", "features": []}
        file_path = tmp_path / "test.geojson"
        file_path.write_text(json.dumps(geojson))

        with pytest.raises(ValueError, match="no features"):
            read_geometry_file(file_path)


class TestCountPoints:
    """Tests for _count_points helper function."""

    @pytest.mark.parametrize(
        "geometry,expected_count",
        [
            pytest.param(
                {
                    "type": "Polygon",
                    "coordinates": [
                        [[-10, -10], [10, -10], [10, 10], [-10, 10], [-10, -10]]
                    ],
                },
                5,
                id="simple_polygon",
            ),
            pytest.param(
                {
                    "type": "Polygon",
                    "coordinates": [
                        # Exterior ring
                        [[-10, -10], [10, -10], [10, 10], [-10, 10], [-10, -10]],
                        # Hole
                        [[-5, -5], [5, -5], [5, 5], [-5, 5], [-5, -5]],
                    ],
                },
                10,  # 5 + 5
                id="polygon_with_hole",
            ),
            pytest.param(
                {
                    "type": "MultiPolygon",
                    "coordinates": [
                        [[[-10, -10], [0, -10], [0, 0], [-10, 0], [-10, -10]]],
                        [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
                    ],
                },
                10,  # 5 + 5
                id="multipolygon",
            ),
            pytest.param(
                {"type": "Point", "coordinates": [0, 0]},
                1,
                id="point",
            ),
            pytest.param(
                {
                    "type": "LineString",
                    "coordinates": [[0, 0], [10, 10], [20, 0]],
                },
                3,
                id="linestring",
            ),
        ],
    )
    def test_count_points(self, geometry: dict, expected_count: int) -> None:
        """Count points in various geometry types."""
        assert _count_points(geometry) == expected_count


class TestSimplifyGeometry:
    """Tests for geometry simplification."""

    @pytest.fixture
    def complex_polygon(self) -> dict:
        """Create a polygon with many points (>300)."""
        import math

        # Create a circle-like polygon with 500 points
        num_points = 500
        coords = []
        for i in range(num_points):
            angle = 2 * math.pi * i / num_points
            lon = 10 * math.cos(angle)
            lat = 10 * math.sin(angle)
            coords.append([lon, lat])
        # Close the polygon
        coords.append(coords[0])

        return {"type": "Polygon", "coordinates": [coords]}

    def test_simplify_complex_geometry(self, complex_polygon: dict) -> None:
        """Simplify a complex geometry to under max_points."""
        original_count = _count_points(complex_polygon)
        assert original_count > MAX_POLYGON_POINTS

        result = simplify_geometry(complex_polygon, max_points=MAX_POLYGON_POINTS)

        final_count = _count_points(result)
        assert final_count <= MAX_POLYGON_POINTS
        assert result["type"] == "Polygon"

    def test_simplify_already_simple(self) -> None:
        """Don't modify geometry that's already under the limit."""
        simple_polygon = {
            "type": "Polygon",
            "coordinates": [[[-10, -10], [10, -10], [10, 10], [-10, 10], [-10, -10]]],
        }

        result = simplify_geometry(simple_polygon, max_points=MAX_POLYGON_POINTS)

        # Should return same coordinates
        assert result["coordinates"] == simple_polygon["coordinates"]

    def test_simplify_custom_max_points(self, complex_polygon: dict) -> None:
        """Simplify to custom max_points limit."""
        result = simplify_geometry(complex_polygon, max_points=50)

        final_count = _count_points(result)
        assert final_count <= 50

    def test_simplify_requires_shapely(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Raise ImportError if shapely is not available."""
        import sys

        # Temporarily remove shapely from modules
        shapely_modules = [key for key in sys.modules if key.startswith("shapely")]
        saved_modules = {key: sys.modules.pop(key) for key in shapely_modules}

        # Mock import to fail
        import builtins

        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name.startswith("shapely"):
                raise ImportError("No module named 'shapely'")
            return original_import(name, *args, **kwargs)

        try:
            monkeypatch.setattr("builtins.__import__", mock_import)
            # Need to reimport to get fresh module state
            from earthaccess.search.query import geometry

            with pytest.raises(ImportError, match="shapely is required"):
                geometry._check_shapely()
        finally:
            # Restore shapely modules
            sys.modules.update(saved_modules)


class TestExtractPolygonCoords:
    """Tests for extracting polygon coordinates."""

    @pytest.mark.parametrize(
        "geometry,expected_len,expected_first",
        [
            pytest.param(
                {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [-10.0, -10.0],
                            [10.0, -10.0],
                            [10.0, 10.0],
                            [-10.0, 10.0],
                            [-10.0, -10.0],
                        ]
                    ],
                },
                5,
                (-10.0, -10.0),
                id="simple_polygon",
            ),
            pytest.param(
                {
                    "type": "MultiPolygon",
                    "coordinates": [
                        [
                            [
                                [-10.0, -10.0],
                                [0.0, -10.0],
                                [0.0, 0.0],
                                [-10.0, 0.0],
                                [-10.0, -10.0],
                            ]
                        ],
                        [
                            [
                                [0.0, 0.0],
                                [10.0, 0.0],
                                [10.0, 10.0],
                                [0.0, 10.0],
                                [0.0, 0.0],
                            ]
                        ],
                    ],
                },
                5,
                (-10.0, -10.0),
                id="multipolygon_uses_first",
            ),
        ],
    )
    def test_extract_polygon_coords(
        self, geometry: dict, expected_len: int, expected_first: tuple
    ) -> None:
        """Extract coordinates from various polygon geometry types."""
        result = extract_polygon_coords(geometry)

        assert len(result) == expected_len
        assert result[0] == expected_first
        # Polygon should be closed
        assert result[0] == result[-1]

    def test_extract_from_point_raises(self) -> None:
        """Raise error when extracting from non-polygon geometry."""
        geometry = {"type": "Point", "coordinates": [0, 0]}

        with pytest.raises(ValueError, match="Cannot extract polygon coordinates"):
            extract_polygon_coords(geometry)


class TestLoadAndSimplifyPolygon:
    """Tests for the main entry point function."""

    def test_load_simple_geojson(self, tmp_path: Path) -> None:
        """Load a simple GeoJSON polygon."""
        geojson = {
            "type": "Polygon",
            "coordinates": [[[-10, -10], [10, -10], [10, 10], [-10, 10], [-10, -10]]],
        }
        file_path = tmp_path / "simple.geojson"
        file_path.write_text(json.dumps(geojson))

        result = load_and_simplify_polygon(file_path)

        assert len(result) == 5
        assert result[0] == (-10.0, -10.0)
        assert result[-1] == (-10.0, -10.0)

    def test_load_and_simplify_complex(self, tmp_path: Path) -> None:
        """Load and simplify a complex GeoJSON polygon."""
        import math

        # Create a circle-like polygon with 500 points
        num_points = 500
        coords = []
        for i in range(num_points):
            angle = 2 * math.pi * i / num_points
            lon = 10 * math.cos(angle)
            lat = 10 * math.sin(angle)
            coords.append([lon, lat])
        coords.append(coords[0])

        geojson = {"type": "Polygon", "coordinates": [coords]}
        file_path = tmp_path / "complex.geojson"
        file_path.write_text(json.dumps(geojson))

        result = load_and_simplify_polygon(file_path, max_points=100)

        assert len(result) <= 100
        # Should still be a closed polygon
        assert result[0] == result[-1]


class TestPolygonFromFile:
    """Tests for Polygon.from_file() class method."""

    def test_polygon_from_geojson_file(self, tmp_path: Path) -> None:
        """Create Polygon from GeoJSON file."""
        geojson = {
            "type": "Polygon",
            "coordinates": [[[-10, -10], [10, -10], [10, 10], [-10, 10], [-10, -10]]],
        }
        file_path = tmp_path / "boundary.geojson"
        file_path.write_text(json.dumps(geojson))

        poly = Polygon.from_file(file_path)

        assert len(poly.coordinates) == 5
        assert poly.coordinates[0] == (-10.0, -10.0)
        assert poly.coordinates[-1] == (-10.0, -10.0)

    def test_polygon_from_file_with_string_path(self, tmp_path: Path) -> None:
        """Create Polygon from file using string path."""
        geojson = {
            "type": "Polygon",
            "coordinates": [[[-10, -10], [10, -10], [10, 10], [-10, 10], [-10, -10]]],
        }
        file_path = tmp_path / "boundary.geojson"
        file_path.write_text(json.dumps(geojson))

        poly = Polygon.from_file(str(file_path))

        assert len(poly.coordinates) == 5

    def test_polygon_from_file_simplifies(self, tmp_path: Path) -> None:
        """Polygon.from_file() simplifies complex geometries."""
        import math

        num_points = 500
        coords = []
        for i in range(num_points):
            angle = 2 * math.pi * i / num_points
            lon = 10 * math.cos(angle)
            lat = 10 * math.sin(angle)
            coords.append([lon, lat])
        coords.append(coords[0])

        geojson = {"type": "Polygon", "coordinates": [coords]}
        file_path = tmp_path / "complex.geojson"
        file_path.write_text(json.dumps(geojson))

        poly = Polygon.from_file(file_path, max_points=100)

        assert len(poly.coordinates) <= 100


class TestGranuleQueryPolygonFile:
    """Tests for GranuleQuery.polygon() with file parameter."""

    def test_polygon_with_file(self, tmp_path: Path) -> None:
        """GranuleQuery.polygon(file=...) works."""
        geojson = {
            "type": "Polygon",
            "coordinates": [[[-10, -10], [10, -10], [10, 10], [-10, 10], [-10, -10]]],
        }
        file_path = tmp_path / "boundary.geojson"
        file_path.write_text(json.dumps(geojson))

        query = GranuleQuery().short_name("ATL03").polygon(file=file_path)

        # Verify the query has spatial set
        cmr_params = query.to_cmr()
        assert "polygon" in cmr_params

    def test_polygon_with_coordinates(self) -> None:
        """GranuleQuery.polygon(coordinates=...) still works."""
        coords = [(-10, -10), (10, -10), (10, 10), (-10, 10), (-10, -10)]

        query = GranuleQuery().short_name("ATL03").polygon(coords)

        cmr_params = query.to_cmr()
        assert "polygon" in cmr_params

    def test_polygon_both_raises_error(self, tmp_path: Path) -> None:
        """Raise error if both coordinates and file are provided."""
        geojson = {
            "type": "Polygon",
            "coordinates": [[[-10, -10], [10, -10], [10, 10], [-10, 10], [-10, -10]]],
        }
        file_path = tmp_path / "boundary.geojson"
        file_path.write_text(json.dumps(geojson))
        coords = [(-10, -10), (10, -10), (10, 10), (-10, 10), (-10, -10)]

        with pytest.raises(ValueError, match="Cannot specify both"):
            GranuleQuery().short_name("ATL03").polygon(coords, file=file_path)

    def test_polygon_neither_raises_error(self) -> None:
        """Raise error if neither coordinates nor file are provided."""
        with pytest.raises(ValueError, match="Must specify either"):
            GranuleQuery().short_name("ATL03").polygon()

    def test_polygon_file_simplifies_for_query(self, tmp_path: Path) -> None:
        """Complex geometry file is simplified for CMR query."""
        import math

        num_points = 500
        coords = []
        for i in range(num_points):
            angle = 2 * math.pi * i / num_points
            lon = 10 * math.cos(angle)
            lat = 10 * math.sin(angle)
            coords.append([lon, lat])
        coords.append(coords[0])

        geojson = {"type": "Polygon", "coordinates": [coords]}
        file_path = tmp_path / "complex.geojson"
        file_path.write_text(json.dumps(geojson))

        query = (
            GranuleQuery().short_name("ATL03").polygon(file=file_path, max_points=100)
        )

        # CMR polygon string should have fewer points
        cmr_params = query.to_cmr()
        polygon_str = cmr_params["polygon"]
        # Each point is "lon,lat," so count commas
        point_count = (polygon_str.count(",") + 1) // 2
        assert point_count <= 100


class TestWKTFileSupport:
    """Tests for WKT file support."""

    def test_read_wkt_polygon(self, tmp_path: Path) -> None:
        """Read a WKT polygon file."""
        wkt_content = "POLYGON ((-10 -10, 10 -10, 10 10, -10 10, -10 -10))"
        file_path = tmp_path / "test.wkt"
        file_path.write_text(wkt_content)

        result = read_geometry_file(file_path)

        assert result["type"] == "Polygon"
        # Shapely normalizes the coordinates
        assert len(result["coordinates"][0]) == 5

    def test_polygon_from_wkt_file(self, tmp_path: Path) -> None:
        """Create Polygon from WKT file."""
        wkt_content = "POLYGON ((-10 -10, 10 -10, 10 10, -10 10, -10 -10))"
        file_path = tmp_path / "boundary.wkt"
        file_path.write_text(wkt_content)

        poly = Polygon.from_file(file_path)

        assert len(poly.coordinates) == 5
