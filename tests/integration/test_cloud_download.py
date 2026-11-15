import logging
import shutil
from pathlib import Path
from typing import Any, cast

import earthaccess
import pytest
from earthaccess import Auth, DataGranulesQuery, Store

from .param import ProviderParam
from .sample import get_sample_granules, top_collections_for_provider

logger = logging.getLogger(__name__)


daac_list: list[ProviderParam] = [
    {
        "provider_name": "NSIDC_CPRD",
        "n_for_top_collections": 3,
        "granules_count": 100,
        "granules_sample_size": 2,
        "granules_max_size_mb": 100,
    },
    {
        "provider_name": "GES_DISC",
        "n_for_top_collections": 3,
        "granules_count": 100,
        "granules_sample_size": 2,
        "granules_max_size_mb": 150,
    },
    {
        "provider_name": "POCLOUD",
        "n_for_top_collections": 3,
        "granules_count": 100,
        "granules_sample_size": 2,
        "granules_max_size_mb": 100,
    },
    {
        "provider_name": "LPCLOUD",
        "n_for_top_collections": 3,
        "granules_count": 100,
        "granules_sample_size": 2,
        "granules_max_size_mb": 100,
    },
    {
        "provider_name": "ORNL_CLOUD",
        "n_for_top_collections": 3,
        "granules_count": 100,
        "granules_sample_size": 2,
        "granules_max_size_mb": 100,
    },
    {
        "provider_name": "LARC_CLOUD",
        "n_for_top_collections": 3,
        "granules_count": 100,
        "granules_sample_size": 2,
        "granules_max_size_mb": 100,
    },
]


@pytest.mark.parametrize("daac", daac_list)
def test_earthaccess_can_download_cloud_collection_granules(tmp_path, daac, access):
    """Tests that we can download cloud collections using HTTPS links."""
    provider = daac["provider_name"]
    n_for_top_collections = daac["n_for_top_collections"]

    granules_count = daac["granules_count"]
    granules_sample_size = daac["granules_sample_size"]
    granules_max_size = daac["granules_max_size_mb"]

    top_collections = top_collections_for_provider(
        provider,
        n=n_for_top_collections,
    )
    logger.info("On-premises collections for %s: %s", provider, len(top_collections))

    for concept_id in top_collections:
        granule_query = DataGranulesQuery().concept_id(concept_id)
        total_granules = granule_query.hits()
        granules = granule_query.get(granules_count)

        msg = f"AssertionError for {concept_id}"
        assert isinstance(granules, list), msg
        assert len(granules) > 0, msg
        assert isinstance(granules[0], earthaccess.DataGranule), msg

        granules_to_download, total_size_cmr = get_sample_granules(
            granules,
            granules_sample_size,
            granules_max_size,
        )
        if len(granules_to_download) == 0:
            logger.warning(
                "Skipping %s, granule size exceeds configured max size",
                concept_id,
            )
            continue
        logger.info(
            "Testing %s, granules in collection: %s, download size(MB): %s",
            concept_id,
            total_granules,
            total_size_cmr,
        )
        path = tmp_path / "tests" / "integration" / "data" / concept_id
        path.mkdir(parents=True)
        store = Store(Auth().login(strategy="environment"))

        try:
            # We are testing this method
            store.get(granules_to_download, local_path=path, access=access)
        except Exception as e:  # noqa: BLE001
            logger.warning(e)

        # test that we downloaded the mb reported by CMR
        total_mb_downloaded = round(
            sum(file.stat().st_size for file in path.rglob("*")) / 1024**2,
        )
        # clean the directory
        shutil.rmtree(path)
        # test that we could download the data
        if total_mb_downloaded <= 0:
            logger.warning("earthaccess could not download %s", concept_id)
        if total_mb_downloaded != total_size_cmr:
            logger.warning(
                "Warning: %s downloaded size %sMB is different from the size reported by CMR: %sMB",
                concept_id,
                total_mb_downloaded,
                total_size_cmr,
            )


def test_multi_file_granule(
    tmp_path,
    access,
):
    # Ensure granules that contain multiple files are handled correctly
    granules = earthaccess.search_data(short_name="HLSL30", count=1)
    assert len(granules) == 1
    urls = cast(Any, granules[0]).data_links(access=access)
    assert len(urls) > 1
    files = earthaccess.download(cast(Any, granules), str(tmp_path), access=access)
    assert {Path(f).name for f in urls} == {Path(f).name for f in files}

    # Make sure no temp files are left behind
    assert len(list(tmp_path.iterdir())) == len(files)

    # Downloading again is a no-op for files that already exist (no `force`
    # in this API yet), so the file set and mtimes are unchanged.
    first_mtimes = [f.stat().st_mtime for f in files]
    second_files = earthaccess.download(
        cast(Any, granules), str(tmp_path), access=access
    )
    assert {Path(f).name for f in second_files} == {Path(f).name for f in files}
    assert [f.stat().st_mtime for f in second_files] == first_mtimes
