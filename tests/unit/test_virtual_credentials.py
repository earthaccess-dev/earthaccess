"""Credential hints recorded by the vds plugin in icechunk repository metadata.

A store written by the vds plugin records *where to fetch credentials* for its
S3 virtual chunk containers (a non-secret endpoint + collection identity) under
``earthaccess:virtual_chunk_credentials``. ``open_virtual()`` reads that hint so
a plain ``asset.href`` opens without the caller knowing the provider.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import icechunk
import pytest
from earthaccess.virtual import core

KEY = core._METADATA_CREDENTIALS_KEY
ENDPOINT = "https://archive.podaac.earthdatacloud.nasa.gov/s3credentials"


def _metadata(*prefixes: str, endpoint: str = ENDPOINT) -> dict[str, Any]:
    return {
        KEY: {
            "version": 1,
            "containers": {
                prefix: {"credentials_endpoint": endpoint, "provider": "POCLOUD"}
                for prefix in prefixes
            },
        }
    }


def _vccs(*prefixes: str) -> dict[str, Any]:
    return {prefix: MagicMock() for prefix in prefixes}


def test_metadata_hints_build_wrapped_credentials() -> None:
    mapping = core._metadata_credential_mapping(
        _metadata("s3://bucket/"),
        _vccs("s3://bucket/"),
    )

    assert set(mapping) == {"s3://bucket/"}
    # icechunk rejects raw S3Credentials; the value must be a Credentials wrapper
    assert isinstance(mapping["s3://bucket/"], icechunk.credentials.Credentials.S3)


def test_metadata_hints_ignore_prefixes_without_a_container() -> None:
    mapping = core._metadata_credential_mapping(
        _metadata("s3://other/"),
        _vccs("s3://bucket/"),
    )

    assert mapping == {}


def test_metadata_hints_ignore_a_missing_endpoint() -> None:
    metadata = {
        KEY: {"version": 1, "containers": {"s3://bucket/": {"provider": "POCLOUD"}}}
    }

    assert core._metadata_credential_mapping(metadata, _vccs("s3://bucket/")) == {}


def test_metadata_hints_absent() -> None:
    assert core._metadata_credential_mapping(None, _vccs("s3://bucket/")) == {}
    assert core._metadata_credential_mapping({}, _vccs("s3://bucket/")) == {}


def test_reopen_authorizes_the_hinted_container(tmp_path: Any) -> None:
    storage = icechunk.Storage.new_local_filesystem(str(tmp_path / "s.icechunk"))
    config = icechunk.RepositoryConfig.default()
    config.set_virtual_chunk_container(
        icechunk.VirtualChunkContainer(
            "s3://bucket/", icechunk.s3_store(region="us-west-2")
        )
    )
    repo = icechunk.Repository.create(storage, config)
    repo.update_metadata(_metadata("s3://bucket/"))

    reopened = core._reopen_with_metadata_credentials(repo, {}, None)

    assert reopened.authorized_virtual_container_prefixes == {"s3://bucket/"}


def test_explicit_credentials_win_over_hints() -> None:
    repo = MagicMock()
    repo.metadata = _metadata("s3://bucket/")
    repo.config.virtual_chunk_containers = _vccs("s3://bucket/")
    explicit = {"s3://bucket/": icechunk.credentials.HttpAccess}

    core._reopen_with_metadata_credentials(repo, {}, explicit)

    mapping = repo.reopen.call_args.kwargs["authorize_virtual_chunk_access"]
    assert mapping["s3://bucket/"] is icechunk.credentials.HttpAccess


def test_no_hints_means_no_reopen() -> None:
    repo = MagicMock()
    repo.metadata = {}
    repo.config.virtual_chunk_containers = _vccs("s3://bucket/")

    assert core._reopen_with_metadata_credentials(repo, {}, None) is repo
    repo.reopen.assert_not_called()


def test_endpoint_credentials_exchange_the_edl_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = MagicMock(authenticated=True)
    auth.get_s3_credentials.return_value = {
        "accessKeyId": "AK",
        "secretAccessKey": "SK",
        "sessionToken": "ST",
    }
    monkeypatch.setattr(core.earthaccess, "__auth__", auth, raising=False)

    creds = core._endpoint_credentials(ENDPOINT)

    auth.get_s3_credentials.assert_called_once_with(endpoint=ENDPOINT)
    assert isinstance(creds, icechunk.S3StaticCredentials)
    assert creds.access_key_id == "AK"
