import logging
import tempfile
import unittest
from pathlib import Path

import earthaccess
import pytest
from virtualizarr.manifests.array import ManifestArray

logger = logging.getLogger(__name__)
assertions = unittest.TestCase("__init__")


auth = earthaccess.login()
logger.info(
    "earthaccess version: %s, authenticated: %s",
    earthaccess.__version__,
    auth.authenticated,
)


@pytest.fixture(
    scope="module",
    params=[
        ("MUR25-JPL-L4-GLOB-v04.2", 2),
        ("AVHRR_OI-NCEI-L4-GLOB-v2.1", 1),
        ("TEMPO_NO2_L3", 2),
        ("M2T1NXSLV", 1),
    ],
)
def granules(request):
    short_name, count = request.param
    return earthaccess.search_data(
        count=count,
        temporal=("2025"),
        short_name=short_name,
    )


def test_virtualize_materialize_indexable(granules):
    # Simply check that the dmrpp can be found, parsed, and loaded. Actual parser result is checked in virtualizarr
    vds = earthaccess.virtualize(
        granules,
        concat_dim="time",
        load=True,
        access="https",
    )
    # We can use fancy indexing
    assert vds.isel(time=0) is not None


def test_virtualize_non_materialize(granules):
    # Simply check that the dmrpp can be found, parsed, and loaded. Actual parser result is checked in virtualizarr
    vds = earthaccess.virtualize(
        granules,
        concat_dim="time",
        load=False,
        access="https",
    )
    # we are not materializing the data
    for name in vds.data_vars:
        assert isinstance(vds[name].variable.data, ManifestArray)


@pytest.mark.parametrize(
    "short_name,count",
    [
        ("TEMPO_NO2_L3", 1),  # TEMPO: HDF5, good for testing parser diversity
        ("M2T1NXSLV", 1),  # MERRA2: NetCDF4, good for testing parser diversity
    ],
)
def test_virtualize_tempo_and_merra2(short_name, count):
    """Test virtualization with specific datasets: TEMPO (HDF5) and MERRA2 (NetCDF4)."""
    granules = earthaccess.search_data(
        count=count,
        temporal=("2025"),
        short_name=short_name,
    )
    assert len(granules) > 0, f"No granules found for {short_name}"

    # Virtualize without loading (direct virtual dataset)
    vds = earthaccess.virtualize(
        granules,
        concat_dim="time" if len(granules) > 1 else None,
        load=False,
        access="https",
    )

    assert vds is not None
    assert len(vds.data_vars) > 0
    # Verify manifest arrays are present
    for name in vds.data_vars:
        assert isinstance(vds[name].variable.data, ManifestArray)


@pytest.mark.parametrize(
    "short_name",
    [
        "TEMPO_NO2_L3",  # TEMPO: HDF5
        "M2T1NXSLV",  # MERRA2: NetCDF4
    ],
)
def test_write_virtual_icechunk_roundtrip(short_name):
    """Test write_virtual and open_virtual with Icechunk persistence."""
    granules = earthaccess.search_data(
        count=1,
        temporal=("2025"),
        short_name=short_name,
    )
    assert len(granules) > 0

    # Create virtual dataset
    vds = earthaccess.virtualize(
        granules,
        access="https",
        load=False,
    )

    # Write to temporary Icechunk store
    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / f"{short_name}_icechunk"

        # Write virtual dataset to Icechunk
        earthaccess.write_virtual(vds, str(store_path))

        # Verify store was created
        assert store_path.exists()

        # Read the virtual dataset back from the Icechunk store
        ds_restored = earthaccess.open_virtual(str(store_path))

        # Verify the restored dataset has the same structure
        assert ds_restored is not None
        assert set(ds_restored.data_vars) == set(vds.data_vars)
        assert set(ds_restored.coords) == set(vds.coords)
