"""Deprecated DAAC metadata table.

This module is kept only for backwards compatibility: DAAC metadata now lives
in :mod:`earthaccess.store.daac`, and credentials should be derived from
granule/collection metadata (``get_s3_credentials(results=...)``) or from an
explicit ``credentials_endpoint=`` override instead of a DAAC lookup table.

.. deprecated::
    Importing ``earthaccess.daac`` is deprecated and will be removed in a
    future release.
"""

import warnings

from earthaccess.store.daac import (
    DAAC_TEST_URLS,
    DAACS,
    DAACConfig,
    find_provider,
    find_provider_by_shortname,
)

warnings.warn(
    "earthaccess.daac is deprecated and will be removed; derive credentials from "
    "granule/collection metadata (results=) or pass credentials_endpoint= instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "DAACS",
    "DAAC_TEST_URLS",
    "DAACConfig",
    "find_provider",
    "find_provider_by_shortname",
]
