# package imports
import os
import threading
import unittest
from pathlib import Path
from pickle import dumps, loads
from unittest.mock import MagicMock, patch

import earthaccess
import fsspec
import pytest
import responses
import s3fs
from earthaccess import Auth, DataGranule, Store
from earthaccess._access import normalize_access
from earthaccess.auth import SessionWithHeaderRedirection
from earthaccess.daac import DAACS
from earthaccess.exceptions import DownloadFailure, EulaNotAccepted
from earthaccess.store import (
    EarthAccessFile,
    _open_files,
    _sibling_tempfile,
    make_instance,
)
from pqdm.threads import pqdm


class TestEula(unittest.TestCase):
    @patch.dict(
        os.environ,
        {
            "EARTHDATA_USERNAME": "user_no_eula",
            "EARTHDATA_PASSWORD": "password",
        },
        clear=True,
    )
    @responses.activate
    def setUp(self):
        json_response = {"access_token": "EDL-token-1", "expiration_date": "12/15/2021"}
        responses.add(
            responses.GET,
            "https://urs.earthdata.nasa.gov/profile",
            json=json_response,
            status=200,
        )
        responses.add(
            responses.POST,
            "https://urs.earthdata.nasa.gov/api/users/find_or_create_token",
            json=json_response,
            status=200,
        )

        self.auth = Auth()
        self.auth.login(strategy="environment")
        assert self.auth.authenticated

    @responses.activate
    def test_eula_detects_401_errors(self):
        response = " blah blah Eula bing!"
        mocked_url = "https://example.com/protected_file.nc"
        responses.add(
            responses.GET,
            "https://urs.earthdata.nasa.gov/profile",
            json={},
            status=200,
        )
        responses.add(
            responses.GET,
            url=mocked_url,
            body=response,
            status=401,
        )
        store = Store(self.auth)
        with self.assertRaisesRegex(
            EulaNotAccepted,
            f"Eula Acceptance Failure for {mocked_url}",
        ):
            store.get([mocked_url], "/tmp")

    @responses.activate
    def test_detects_non_eula_errors(self):
        response = " blah blah error!"
        mocked_url = "https://example.com/protected_file.nc"
        responses.add(
            responses.GET,
            "https://urs.earthdata.nasa.gov/profile",
            json={},
            status=200,
        )
        responses.add(
            responses.GET,
            url=mocked_url,
            body=response,
            status=401,
        )
        store = Store(self.auth)
        with self.assertRaisesRegex(
            DownloadFailure,
            f"Download failed for {mocked_url}. Status code: 401",
        ):
            store.get([mocked_url], "/tmp")

    def tearDown(self):
        self.auth = None

    @responses.activate
    def test_store_can_create_https_fsspec_session(self):
        responses.add(
            responses.GET,
            "https://urs.earthdata.nasa.gov/profile",
            json={},
            status=200,
        )
        store = Store(self.auth)
        assert isinstance(store.auth, Auth)
        https_fs = store.get_fsspec_session()
        assert type(https_fs) is type(fsspec.filesystem("https"))


class TestStoreSessions(unittest.TestCase):
    @responses.activate
    @patch.dict(
        os.environ,
        {
            "EARTHDATA_USERNAME": "user",
            "EARTHDATA_PASSWORD": "password",
        },
        clear=True,
    )
    def setUp(self):
        json_response = {"access_token": "EDL-token-1", "expiration_date": "12/15/2021"}
        responses.add(
            responses.POST,
            "https://urs.earthdata.nasa.gov/api/users/find_or_create_token",
            json=json_response,
            status=200,
        )
        self.auth = Auth()
        self.auth.login(strategy="environment")
        self.assertEqual(self.auth.authenticated, True)
        self.assertEqual(self.auth.token, json_response)

    def tearDown(self):
        self.auth = None

    @responses.activate
    def test_store_can_create_https_fsspec_session(self):
        responses.add(
            responses.GET,
            "https://urs.earthdata.nasa.gov/profile",
            json={},
            status=200,
        )
        store = Store(self.auth)
        self.assertTrue(isinstance(store.auth, Auth))
        https_fs = store.get_fsspec_session()
        self.assertEqual(type(https_fs), type(fsspec.filesystem("https")))

    @responses.activate
    def test_store_can_create_s3_fsspec_session(self):
        custom_endpoints = [
            "https://archive.swot.podaac.earthdata.nasa.gov/s3credentials",
            "https://api.giovanni.earthdata.nasa.gov/s3credentials",
            "https://data.laadsdaac.earthdatacloud.nasa.gov/s3credentials",
        ]
        mock_creds = {
            "accessKeyId": "sure",
            "secretAccessKey": "correct",
            "sessionToken": "whynot",
        }
        expected_storage_options = {
            "key": mock_creds["accessKeyId"],
            "secret": mock_creds["secretAccessKey"],
            "token": mock_creds["sessionToken"],
        }

        for endpoint in custom_endpoints:
            responses.add(
                responses.GET,
                endpoint,
                json=mock_creds,
                status=200,
            )

        for daac in DAACS:
            if "s3-credentials" in daac:
                responses.add(
                    responses.GET,
                    daac["s3-credentials"],
                    json=mock_creds,
                    status=200,
                )
        responses.add(
            responses.GET,
            "https://urs.earthdata.nasa.gov/profile",
            json=mock_creds,
            status=200,
        )

        store = Store(self.auth)
        self.assertTrue(isinstance(store.auth, Auth))
        for daac in [
            "NSIDC",
            "PODAAC",
            "LPDAAC",
            "ORNLDAAC",
            "GES_DISC",
            "ASF",
            "OBDAAC",
            "ASDC",
        ]:
            s3_fs = store.get_s3_filesystem(daac=daac)
            assert isinstance(s3_fs, s3fs.S3FileSystem)
            assert s3_fs.storage_options == expected_storage_options

        for endpoint in custom_endpoints:
            s3_fs = store.get_s3_filesystem(endpoint=endpoint)
            assert isinstance(s3_fs, s3fs.S3FileSystem)
            assert s3_fs.storage_options == expected_storage_options

        for provider in [
            "NSIDC_CPRD",
            "POCLOUD",
            "LPCLOUD",
            "ORNL_CLOUD",
            "GES_DISC",
            "ASF",
            "OB_CLOUD",
            "LARC_CLOUD",
        ]:
            s3_fs = store.get_s3_filesystem(provider=provider)
            assert isinstance(s3_fs, s3fs.S3FileSystem)
            assert s3_fs.storage_options == expected_storage_options

        # Ensure informative error is raised
        with pytest.raises(ValueError, match="parameters must be specified"):
            store.get_s3_filesystem()

    @responses.activate
    def test_session_reuses_token_download(self):
        mock_creds = {
            "accessKeyId": "sure",
            "secretAccessKey": "correct",
            "sessionToken": "whynot",
        }
        test_cases = [
            (2, 500),  # 2 threads, 500 files
            (4, 400),  # 4 threads, 400 files
            (8, 5000),  # 8 threads, 5k files
        ]
        for n_threads, n_files in test_cases:
            with self.subTest(n_threads=n_threads, n_files=n_files):
                urls = [f"https://example.com/file{i}" for i in range(1, n_files + 1)]
                for i, url in enumerate(urls):
                    responses.add(
                        responses.GET,
                        url,
                        body=f"Content of file {i + 1}",
                        status=200,
                    )

                edl_hostname = "urs.earthdata.nasa.gov"
                mock_auth = MagicMock()
                mock_auth.authenticated = True
                mock_auth.system.edl_hostname = edl_hostname
                responses.add(
                    responses.GET,
                    "https://urs.earthdata.nasa.gov/profile",
                    json=mock_creds,
                    status=200,
                )

                original_session = SessionWithHeaderRedirection(edl_hostname)
                original_session.cookies.set("sessionid", "mocked-session-cookie")
                mock_auth.get_session.return_value = original_session

                store = Store(auth=mock_auth)
                store.thread_locals = threading.local()  # Use real thread-local storage

                # Track cloned sessions
                cloned_sessions = set()

                def mock_clone_session_in_local_thread(
                    original_session,
                    store=store,
                    edl_hostname=edl_hostname,
                    cloned_sessions=cloned_sessions,
                ):
                    """Mock session cloning to track cloned sessions."""
                    if not hasattr(store.thread_locals, "local_thread_session"):
                        session = SessionWithHeaderRedirection(edl_hostname)
                        session.cookies.update(original_session.cookies)
                        cloned_sessions.add(id(session))
                        store.thread_locals.local_thread_session = session

                with patch.object(
                    store,
                    "_clone_session_in_local_thread",
                    side_effect=mock_clone_session_in_local_thread,
                ):
                    mock_directory = Path("/mock/directory")
                    downloaded_files = []

                    def mock_download_file(
                        url,
                        store=store,
                        original_session=original_session,
                        downloaded_files=downloaded_files,
                        mock_directory=mock_directory,
                    ):
                        """Mock file download to track downloaded files."""
                        # Ensure session cloning happens before downloading
                        store._clone_session_in_local_thread(original_session)
                        downloaded_files.append(url)
                        return mock_directory / f"{url.split('/')[-1]}"

                    with patch.object(
                        store,
                        "_download_file",
                        side_effect=mock_download_file,
                    ):
                        # Test multi-threaded download
                        pqdm(urls, store._download_file, n_jobs=n_threads)

                # We make sure we reuse the token up to N threads
                self.assertTrue(len(cloned_sessions) <= n_threads)

                self.assertEqual(len(downloaded_files), n_files)  # 10 files downloaded
                self.assertCountEqual(downloaded_files, urls)  # All files accounted for


def test_earthaccess_file_getattr():
    fs = fsspec.filesystem("memory")
    with fs.open("foo", "wb") as f:
        earthaccess_file = EarthAccessFile(f, granule="foo")
        assert f.tell == earthaccess_file.tell
    fs.store.clear()


@pytest.mark.parametrize(
    "file_size, open_kwargs, expected_cache_type, expected_block_size",
    [
        # Case 1: Small file, defaults used
        (50 * 1024 * 1024, {}, "background", 4 * 1024 * 1024),
        # Case 2: Medium file, block_size scales up
        (300 * 1024 * 1024, {}, "background", 8 * 1024 * 1024),
        # Case 3: Large file, hits max block size
        (1600 * 1024 * 1024, {}, "background", 16 * 1024 * 1024),
        # Case 4: Override cache_type and block_size
        (
            600 * 1024 * 1024,
            {"cache_type": "readahead", "block_size": 1024},
            "readahead",
            1024,
        ),
        # Case 5: Override only one (block_size)
        (
            600 * 1024 * 1024,
            {"block_size": 8 * 1024 * 1024},
            "background",
            8 * 1024 * 1024,
        ),
    ],
)
def test_open_files_parametrized(
    file_size,
    open_kwargs,
    expected_cache_type,
    expected_block_size,
):
    fs = MagicMock()
    fs.info.return_value = {"size": file_size}
    fs.open = MagicMock()

    url = "https://example.com/file.nc"
    granule = MagicMock()
    url_mapping = {url: granule}

    result = _open_files(url_mapping, fs, open_kwargs=open_kwargs)

    fs.open.assert_called_once()
    args, kwargs = fs.open.call_args

    assert args[0] == url
    assert result[0] is not None
    assert kwargs["cache_type"] == expected_cache_type
    assert kwargs["block_size"] == expected_block_size or (
        isinstance(expected_block_size, float)
        and abs(kwargs["block_size"] - expected_block_size) < 1e5
    )


def test_sibling_tempfile_nominal(tmp_path):
    trg_file = tmp_path / "target.txt"
    orig_text = "Should get replaced"
    new_text = "New-fangled text"
    trg_file.write_text(orig_text)
    with _sibling_tempfile(trg_file) as temp_file:
        assert temp_file.exists()
        assert trg_file.exists()
        assert trg_file.read_text() == orig_text
        temp_file.write_text(new_text)
    assert not temp_file.exists()
    assert trg_file.exists()
    assert trg_file.read_text() == new_text


def test_sibling_tempfile_error(tmp_path):
    trg_file = tmp_path / "target.txt"
    orig_text = "Should get replaced"
    new_text = "New-fangled text"
    trg_file.write_text(orig_text)
    with (
        pytest.raises(Exception, match="Some error to trigger cleanup"),
        _sibling_tempfile(trg_file) as temp_file,
    ):
        temp_file.write_text(new_text)
        msg = "Some error to trigger cleanup"
        raise RuntimeError(msg)
    assert not temp_file.exists()
    assert trg_file.exists()
    assert trg_file.read_text() == orig_text


ACCESS_CASES = [
    pytest.param(None, "https", id="default"),
    pytest.param("https", "https", id="https"),
    pytest.param("s3", "s3", id="s3"),
    pytest.param("direct", "s3", id="legacy-direct"),
    pytest.param("indirect", "https", id="legacy-indirect"),
    pytest.param("external", "https", id="legacy-external"),
]


@pytest.mark.parametrize(("access", "expected"), ACCESS_CASES)
@pytest.mark.parametrize(
    ("api_name", "store_method"),
    [("open", "open"), ("download", "get")],
)
def test_access_normalized_for_open_and_download(
    monkeypatch,
    api_name,
    store_method,
    access,
    expected,
):
    store = MagicMock()
    monkeypatch.setattr(earthaccess, "__store__", store)

    kwargs = {} if access is None else {"access": access}
    getattr(earthaccess, api_name)([MagicMock()], **kwargs)

    method = getattr(store, store_method)
    assert method.call_args.kwargs["access"] == expected


def test_make_instance_restores_serialized_file(monkeypatch):
    fs = fsspec.filesystem("memory")
    with fs.open("foo", "wb") as f:
        f.write(b"bar")
    f = fs.open("foo", "rb")
    auth = MagicMock()
    auth.authenticated = True
    monkeypatch.setattr(earthaccess, "__auth__", auth)

    restored = make_instance(s3fs.S3File, None, auth, dumps(f))

    assert restored.read() == b"bar"
    fs.store.clear()


def test_open_s3_uses_links_without_cloud_hosted(monkeypatch):
    granule = DataGranule(
        {
            "umm": {
                "RelatedUrls": [
                    {"URL": "s3://bucket/file.nc", "Type": "GET DATA VIA DIRECT ACCESS"}
                ]
            },
            "meta": {"provider-id": "POCLOUD"},
        },
        cloud_hosted=False,
    )
    store = Store.__new__(Store)
    store.auth = MagicMock()
    store.get_s3_filesystem = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(
        "earthaccess.store._open_files",
        lambda *_args, **_kwargs: ["opened"],
    )

    assert store.open([granule], access="s3") == ["opened"]


def test_download_s3_uses_links_without_cloud_hosted(monkeypatch, tmp_path):
    granule = DataGranule(
        {
            "umm": {
                "RelatedUrls": [
                    {"URL": "s3://bucket/file.nc", "Type": "GET DATA VIA DIRECT ACCESS"}
                ]
            },
            "meta": {"provider-id": "POCLOUD"},
        },
        cloud_hosted=False,
    )
    store = Store.__new__(Store)
    store.auth = MagicMock()
    store.get_s3_filesystem = MagicMock(return_value=MagicMock())

    def fake_download(_s3_fs, file, local_path, *, force=False):
        _ = force
        path = Path(local_path) / Path(file).name
        path.write_text("data")
        return path

    monkeypatch.setattr(store, "_download_cloud_file", fake_download)

    files = store.get([granule], str(tmp_path), access="s3")

    assert files == [tmp_path / "file.nc"]


def _make_granule(*, concept_id="G1-TEST", provider="POCLOUD", https=(), s3=()):
    related_urls = [{"URL": url, "Type": "GET DATA"} for url in https] + [
        {"URL": url, "Type": "GET DATA VIA DIRECT ACCESS"} for url in s3
    ]
    return DataGranule(
        {
            "meta": {"concept-id": concept_id, "provider-id": provider},
            "umm": {"RelatedUrls": related_urls},
        },
        cloud_hosted=bool(s3),
    )


def test_open_s3_raises_for_granules_without_s3_links():
    cloud_granule = _make_granule(
        concept_id="G-CLOUD",
        https=["https://example.com/cloud.nc"],
        s3=["s3://bucket/cloud.nc"],
    )
    onprem_granule = _make_granule(
        concept_id="G-ONPREM",
        https=["https://example.com/onprem.nc"],
    )
    store = Store.__new__(Store)
    store.auth = MagicMock()
    store.get_s3_filesystem = MagicMock()

    with pytest.raises(ValueError, match="G-ONPREM has no S3 data links"):
        store.open([cloud_granule, onprem_granule], access="s3")

    store.get_s3_filesystem.assert_not_called()


def test_download_s3_raises_before_fetching_credentials_for_mixed_lists():
    onprem_granule = _make_granule(
        concept_id="G-ONPREM",
        provider="NSIDC_ECS",
        https=["https://example.com/onprem.nc"],
    )
    s3_granule = _make_granule(
        concept_id="G-S3",
        provider="PODAAC",
        s3=["s3://bucket/cloud.nc"],
    )
    store = Store.__new__(Store)
    store.auth = MagicMock()
    store.get_s3_filesystem = MagicMock()

    with pytest.raises(ValueError, match="G-ONPREM has no S3 data links"):
        store.get([onprem_granule, s3_granule], "/tmp", access="s3")

    store.get_s3_filesystem.assert_not_called()


def test_open_https_suggests_s3_for_s3_only_granule():
    granule = _make_granule(concept_id="G-S3ONLY", s3=["s3://bucket/cloud.nc"])
    store = Store.__new__(Store)
    store.auth = MagicMock()

    with pytest.raises(
        ValueError,
        match="has no HTTPS data links; it offers S3 links",
    ):
        store.open([granule])


def test_download_https_raises_for_mixed_list():
    onprem_granule = _make_granule(
        concept_id="G-ONPREM",
        https=["https://example.com/onprem.nc"],
    )
    s3_granule = _make_granule(concept_id="G-S3ONLY", s3=["s3://bucket/cloud.nc"])
    store = Store.__new__(Store)
    store.auth = MagicMock()

    with pytest.raises(
        ValueError,
        match="has no HTTPS data links; it offers S3 links",
    ):
        store.get([onprem_granule, s3_granule], "/tmp")


def test_open_s3_url_without_provider_raises():
    store = Store.__new__(Store)
    store.auth = MagicMock()

    with pytest.raises(
        ValueError,
        match="requires provider= or credentials_endpoint=",
    ):
        store.open(["s3://bucket/file.nc"])


def test_open_https_does_not_request_s3_credentials(monkeypatch):
    granule = _make_granule(https=["https://example.com/file.nc"])
    store = Store.__new__(Store)
    store.auth = MagicMock()
    store.get_fsspec_session = MagicMock(return_value=MagicMock())
    store.get_s3_filesystem = MagicMock(
        side_effect=AssertionError("S3 credentials must not be requested"),
    )

    captured = {}

    def fake_open_files(url_mapping, fs, **_kwargs):
        captured["mapping"] = url_mapping
        captured["fs"] = fs
        return ["opened"]

    monkeypatch.setattr("earthaccess.store._open_files", fake_open_files)

    assert store.open([granule]) == ["opened"]
    assert captured["fs"] is store.get_fsspec_session.return_value
    assert list(captured["mapping"]) == ["https://example.com/file.nc"]
    store.get_s3_filesystem.assert_not_called()


def test_open_s3_uses_expected_filesystem_and_urls(monkeypatch):
    granule = _make_granule(s3=["s3://bucket/file.nc"])
    store = Store.__new__(Store)
    store.auth = MagicMock()
    s3_fs = MagicMock(name="s3_filesystem")
    store.get_s3_filesystem = MagicMock(return_value=s3_fs)

    captured = {}

    def fake_open_files(url_mapping, fs, **_kwargs):
        captured["mapping"] = url_mapping
        captured["fs"] = fs
        return ["opened"]

    monkeypatch.setattr("earthaccess.store._open_files", fake_open_files)

    assert store.open([granule], access="s3") == ["opened"]
    assert captured["fs"] is s3_fs
    assert list(captured["mapping"]) == ["s3://bucket/file.nc"]


class _PicklableAuth:
    authenticated = True


def test_earthaccess_file_pickle_round_trip_preserves_second_file(monkeypatch):
    fs = fsspec.filesystem("memory")
    with fs.open("first.nc", "wb") as f:
        f.write(b"first")
    with fs.open("second.nc", "wb") as f:
        f.write(b"second")

    granule = _make_granule(
        s3=["s3://bucket/first.nc", "s3://bucket/second.nc"],
    )
    monkeypatch.setattr(earthaccess, "__auth__", _PicklableAuth())

    handle = fs.open("second.nc", "rb")
    restored = loads(dumps(EarthAccessFile(handle, granule)))

    assert restored.read() == b"second"
    assert restored.granule["meta"]["concept-id"] == granule["meta"]["concept-id"]
    handle.close()
    fs.store.clear()


@pytest.mark.parametrize(
    ("access", "expected"),
    [
        ("S3", "s3"),
        ("HTTPS", "https"),
        ("Direct", "s3"),
        ("Indirect", "https"),
    ],
)
def test_normalize_access_is_case_insensitive(access, expected):
    assert normalize_access(access) == expected


@pytest.mark.parametrize("access", ["bogus", "on_prem", 1])
def test_normalize_access_rejects_invalid_values(access):
    with pytest.raises(ValueError, match="Unknown access"):
        normalize_access(access)
