"""Tests for DataGranule/DataCollection.to_geopandas() and the bulk variants
on DataGranulesQuery/DataCollectionsQuery.
"""

import pytest
from earthaccess.search.queries import DataCollectionsQuery, DataGranulesQuery
from earthaccess.search.results import DataCollection, DataGranule

pytest.importorskip("geopandas")

POINT_GEOMETRY = {"Points": [{"Longitude": 112.19, "Latitude": 42.54}]}

BBOX_GEOMETRY = {
    "BoundingRectangles": [
        {
            "WestBoundingCoordinate": -98,
            "SouthBoundingCoordinate": 19,
            "EastBoundingCoordinate": -82,
            "NorthBoundingCoordinate": 31,
        }
    ]
}


def _granule(geometry, *, granule_ur="granule"):
    return DataGranule(
        {
            "meta": {"concept-id": "G123-TEST", "provider-id": "TEST"},
            "umm": {
                "GranuleUR": granule_ur,
                "CollectionReference": {"ShortName": "TEST", "Version": "1"},
                "TemporalExtent": {
                    "RangeDateTime": {
                        "BeginningDateTime": "2025-01-01T00:00:00Z",
                        "EndingDateTime": "2025-01-01T01:00:00Z",
                    }
                },
                "SpatialExtent": {"HorizontalSpatialDomain": {"Geometry": geometry}},
            },
        }
    )


def test_granule_to_geopandas_flattens_umm_by_default():
    gdf = _granule(POINT_GEOMETRY).to_geopandas()

    assert len(gdf) == 1
    # Full UMM record flattened into dotted columns.
    assert "umm.GranuleUR" in gdf.columns
    assert "umm.CollectionReference.ShortName" in gdf.columns
    assert "umm.TemporalExtent.RangeDateTime.BeginningDateTime" in gdf.columns
    # Basic meta block always present.
    assert gdf["meta.concept-id"].iloc[0] == "G123-TEST"
    assert gdf["meta.provider-id"].iloc[0] == "TEST"
    # Geometry is the active column.
    assert gdf.geometry.iloc[0].geom_type == "MultiPoint"
    assert gdf.crs.to_epsg() == 4326


def test_granule_to_geopandas_fields_subset():
    gdf = _granule(POINT_GEOMETRY).to_geopandas(fields=["GranuleUR"])

    assert "umm.GranuleUR" in gdf.columns
    assert "umm.TemporalExtent.RangeDateTime.BeginningDateTime" not in gdf.columns
    assert (
        "umm.SpatialExtent.HorizontalSpatialDomain.Geometry.Points" not in gdf.columns
    )
    assert "meta.concept-id" in gdf.columns  # meta is always kept
    assert len(gdf) == 1


def test_collection_to_geopandas():
    collection = DataCollection(
        {
            "meta": {"concept-id": "C123-TEST", "provider-id": "TEST"},
            "umm": {
                "ShortName": "TEST",
                "Version": "1",
                "SpatialExtent": {
                    "HorizontalSpatialDomain": {"Geometry": BBOX_GEOMETRY}
                },
            },
        }
    )

    gdf = collection.to_geopandas(fields=["ShortName", "Version"])

    assert len(gdf) == 1
    assert gdf["umm.ShortName"].iloc[0] == "TEST"
    assert gdf["umm.Version"].iloc[0] == "1"
    assert gdf.geometry.iloc[0].geom_type == "MultiPolygon"


def test_to_geopandas_missing_spatial_extent_raises():
    granule = DataGranule({"meta": {"concept-id": "G1"}, "umm": {"GranuleUR": "x"}})

    with pytest.raises(ValueError):
        granule.to_geopandas()


def test_data_granules_to_geopandas_bulk():
    granules = [
        _granule(POINT_GEOMETRY, granule_ur="g1"),
        _granule(POINT_GEOMETRY, granule_ur="g2"),
    ]

    gdf = DataGranulesQuery.to_geopandas(granules)

    assert len(gdf) == 2
    assert list(gdf["umm.GranuleUR"]) == ["g1", "g2"]
    assert all(geom.geom_type == "MultiPoint" for geom in gdf.geometry)
    assert gdf.crs is not None
    assert gdf.crs.to_epsg() == 4326


def test_data_collections_to_geopandas_bulk():
    def _collection(short_name):
        return DataCollection(
            {
                "meta": {"concept-id": f"C-{short_name}", "provider-id": "TEST"},
                "umm": {
                    "ShortName": short_name,
                    "Version": "1",
                    "SpatialExtent": {
                        "HorizontalSpatialDomain": {"Geometry": BBOX_GEOMETRY}
                    },
                },
            }
        )

    collections = [_collection("A"), _collection("B")]

    gdf = DataCollectionsQuery.to_geopandas(collections)

    assert len(gdf) == 2
    assert list(gdf["umm.ShortName"]) == ["A", "B"]
    assert all(geom.geom_type == "MultiPolygon" for geom in gdf.geometry)


def test_data_granules_to_geopandas_missing_spatial_extent_raises():
    granules = [DataGranule({"meta": {"concept-id": "G1"}, "umm": {"GranuleUR": "x"}})]

    with pytest.raises(ValueError):
        DataGranulesQuery.to_geopandas(granules)


def test_data_granules_to_geopandas_without_geopandas_raises(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "geopandas":
            raise ImportError("No module named 'geopandas'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ImportError, match="pip install earthaccess\\[geo\\]"):
        DataGranulesQuery.to_geopandas([_granule(POINT_GEOMETRY)])
