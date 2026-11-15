"""TDD Tests for store/download.py module.

Tests the download operations extracted from the Store class,
following SOLID principles with single responsibility for downloads.
"""

import tempfile
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

import pytest


class TestDownloadFile:
    """Test the download_file function for HTTP downloads."""

    def test_download_file_creates_file(self) -> None:
        """Test that download_file creates a file in the target directory."""
        from earthaccess.store.download import download_file

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Mock the session
            mock_session = Mock()
            mock_response = Mock()
            mock_response.iter_content.return_value = [b"test content"]
            mock_response.__enter__ = Mock(return_value=mock_response)
            mock_response.__exit__ = Mock(return_value=False)
            mock_session.get.return_value = mock_response

            result = download_file(
                url="https://example.com/test.nc",
                directory=temp_path,
                session=mock_session,
            )

            assert result.name == "test.nc"
            mock_session.get.assert_called_once()

    def test_download_file_skips_existing(self) -> None:
        """Test that download_file skips files that already exist."""
        from earthaccess.store.download import download_file

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create the file first
            existing_file = temp_path / "test.nc"
            existing_file.write_bytes(b"existing content")

            mock_session = Mock()

            result = download_file(
                url="https://example.com/test.nc",
                directory=temp_path,
                session=mock_session,
            )

            # Should return the existing file without making HTTP request
            assert result == existing_file
            mock_session.get.assert_not_called()

    def test_download_file_handles_opendap_urls(self) -> None:
        """Test that download_file strips .html from OpenDAP URLs."""
        from earthaccess.store.download import download_file

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            mock_session = Mock()
            mock_response = Mock()
            mock_response.iter_content.return_value = [b"data"]
            mock_response.__enter__ = Mock(return_value=mock_response)
            mock_response.__exit__ = Mock(return_value=False)
            mock_session.get.return_value = mock_response

            result = download_file(
                url="https://opendap.example.com/data.nc.html",
                directory=temp_path,
                session=mock_session,
            )

            # URL should have .html stripped
            call_args = mock_session.get.call_args
            assert ".html" not in call_args[0][0]
            assert result.name == "data.nc"


class TestDownloadCloudFile:
    """Test the download_cloud_file function for S3 downloads."""

    def test_download_cloud_file_creates_file(self) -> None:
        """Test that download_cloud_file downloads from S3."""
        from earthaccess.store.download import download_cloud_file

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Mock the S3 filesystem
            mock_s3_fs = Mock()
            mock_s3_fs.get = Mock()

            result = download_cloud_file(
                s3_fs=mock_s3_fs,
                file="s3://bucket/path/to/file.nc",
                path=temp_path,
            )

            assert result.name == "file.nc"
            mock_s3_fs.get.assert_called_once()

    def test_download_cloud_file_skips_existing(self) -> None:
        """Test that download_cloud_file skips existing files."""
        from earthaccess.store.download import download_cloud_file

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create the file first
            existing_file = temp_path / "file.nc"
            existing_file.write_bytes(b"existing")

            mock_s3_fs = Mock()

            result = download_cloud_file(
                s3_fs=mock_s3_fs,
                file="s3://bucket/path/to/file.nc",
                path=temp_path,
            )

            assert result == existing_file
            mock_s3_fs.get.assert_not_called()


class TestDownloadGranules:
    """Test the download_granules function for batch downloads."""

    def test_download_granules_returns_list(self) -> None:
        """Test that download_granules returns a list of paths."""
        from earthaccess.store.download import download_granules

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            mock_session = Mock()
            mock_response = Mock()
            mock_response.iter_content.return_value = [b"data"]
            mock_response.__enter__ = Mock(return_value=mock_response)
            mock_response.__exit__ = Mock(return_value=False)
            mock_session.get.return_value = mock_response

            urls = [
                "https://example.com/file1.nc",
                "https://example.com/file2.nc",
            ]

            with patch("earthaccess.store.download.get_executor") as mock_get_executor:
                mock_executor = Mock()
                # Simulate executor.map returning paths
                mock_executor.map.return_value = iter(
                    [temp_path / "file1.nc", temp_path / "file2.nc"]
                )
                mock_executor.shutdown = Mock()
                mock_get_executor.return_value = mock_executor

                result = download_granules(
                    urls=urls,
                    directory=temp_path,
                    session=mock_session,
                )

            assert isinstance(result, list)
            assert len(result) == 2

    def test_download_granules_creates_directory(self) -> None:
        """Test that download_granules creates the target directory."""
        from earthaccess.store.download import download_granules

        with tempfile.TemporaryDirectory() as temp_dir:
            new_dir = Path(temp_dir) / "subdir" / "data"

            mock_session = Mock()
            mock_response = Mock()
            mock_response.iter_content.return_value = [b"data"]
            mock_response.__enter__ = Mock(return_value=mock_response)
            mock_response.__exit__ = Mock(return_value=False)
            mock_session.get.return_value = mock_response

            with patch("earthaccess.store.download.get_executor") as mock_get_executor:
                mock_executor = Mock()
                mock_executor.map.return_value = iter([new_dir / "file.nc"])
                mock_executor.shutdown = Mock()
                mock_get_executor.return_value = mock_executor

                download_granules(
                    urls=["https://example.com/file.nc"],
                    directory=new_dir,
                    session=mock_session,
                )

            assert new_dir.exists()

    def test_download_granules_validates_urls(self) -> None:
        """Test that download_granules validates URL list."""
        from earthaccess.store.download import download_granules

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            mock_session = Mock()

            with pytest.raises(ValueError, match="URLs"):
                download_granules(
                    urls=None,  # type: ignore
                    directory=temp_path,
                    session=mock_session,
                )


class TestDownloadWithTargetLocation:
    """Test downloads with TargetLocation for cloud storage."""

    def test_download_file_with_target_location(self) -> None:
        """Test download_file works with TargetLocation."""
        from earthaccess.store.download import download_file
        from earthaccess.store.target import TargetLocation

        # Create a mock TargetLocation with mock filesystem
        mock_filesystem = Mock()
        mock_filesystem.exists.return_value = False
        mock_filesystem.basename.return_value = "test.nc"
        mock_filesystem.join.return_value = "s3://bucket/test.nc"
        mock_filesystem.open.return_value.__enter__ = Mock(return_value=BytesIO())
        mock_filesystem.open.return_value.__exit__ = Mock(return_value=False)

        with patch.object(
            TargetLocation, "get_filesystem", return_value=mock_filesystem
        ):
            target = TargetLocation("s3://bucket/")

            mock_session = Mock()
            mock_response = Mock()
            mock_response.iter_content.return_value = [b"data"]
            mock_response.__enter__ = Mock(return_value=mock_response)
            mock_response.__exit__ = Mock(return_value=False)
            mock_session.get.return_value = mock_response

            result = download_file(
                url="https://example.com/test.nc",
                directory=target,
                session=mock_session,
            )

            assert result is not None


class TestRetryBehavior:
    """Test retry behavior for download functions."""

    def test_download_file_has_retry_decorator(self) -> None:
        """Test that download_file has retry behavior."""
        from earthaccess.store.download import download_file

        # Check that the function has retry metadata (from tenacity)
        assert hasattr(download_file, "retry")

    def test_download_cloud_file_has_retry_decorator(self) -> None:
        """Test that download_cloud_file has retry behavior."""
        from earthaccess.store.download import download_cloud_file

        # Check that the function has retry metadata (from tenacity)
        assert hasattr(download_cloud_file, "retry")


class TestSessionCloning:
    """Test session cloning behavior for parallel downloads."""

    def test_clone_session_copies_headers(self) -> None:
        """Test that session cloning copies headers."""
        from earthaccess.store.download import clone_session

        original = Mock()
        original.headers = {"Authorization": "Bearer token123"}
        original.cookies = {"session": "abc123"}
        original.auth = ("user", "pass")
        original.hooks = {"response": []}

        cloned = clone_session(original)

        assert cloned.headers["Authorization"] == "Bearer token123"
        assert cloned.cookies["session"] == "abc123"
        assert cloned.auth == ("user", "pass")

    def test_clone_session_creates_new_instance(self) -> None:
        """Test that clone_session creates a new session instance."""
        from earthaccess.store.download import clone_session

        original = Mock()
        original.headers = {}
        original.cookies = {}
        original.auth = None
        original.hooks = {"response": []}

        cloned = clone_session(original)

        assert cloned is not original


class TestChunkSize:
    """Test chunk size constants and behavior."""

    def test_default_chunk_size(self) -> None:
        """Test that default chunk size is 1MB."""
        from earthaccess.store.download import DEFAULT_CHUNK_SIZE

        assert DEFAULT_CHUNK_SIZE == 1024 * 1024  # 1MB


class TestStoreForce:
    """`force=True` re-downloads files that already exist."""

    @staticmethod
    def _store():
        import threading

        from earthaccess.store import Store

        store = Store.__new__(Store)
        store.auth = Mock(authenticated=True)
        store._s3_credentials = {}
        store.thread_locals = threading.local()
        store._current_executor_type = "threads"
        return store

    def test_download_cloud_file_skips_existing_without_force(self, tmp_path) -> None:
        store = self._store()
        s3_fs = Mock()
        (tmp_path / "a.nc").write_text("old")

        result = store.download_cloud_file(s3_fs, "s3://bucket/a.nc", tmp_path)

        assert result == tmp_path / "a.nc"
        s3_fs.get.assert_not_called()

    def test_download_cloud_file_force_redownloads(self, tmp_path) -> None:
        store = self._store()
        s3_fs = Mock()
        (tmp_path / "a.nc").write_text("old")

        store.download_cloud_file(s3_fs, "s3://bucket/a.nc", tmp_path, force=True)

        s3_fs.get.assert_called_once_with(
            ["s3://bucket/a.nc"], str(tmp_path), recursive=False
        )

    def _mock_https_session(self, store, payload: bytes):
        session = Mock()
        response = Mock()
        response.iter_content.return_value = [payload]
        response.raise_for_status.return_value = None
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        session.get.return_value = response
        store.get_requests_session = Mock(return_value=session)
        store._clone_session_in_local_thread = Mock()
        store.thread_locals.local_thread_session = session
        return session

    def test_download_file_skips_existing_without_force(self, tmp_path) -> None:
        store = self._store()
        session = self._mock_https_session(store, b"new")
        (tmp_path / "a.nc").write_text("old")

        result = store._download_file("https://example.com/a.nc", tmp_path)

        assert result.read_text() == "old"
        session.get.assert_not_called()

    def test_download_file_force_redownloads(self, tmp_path) -> None:
        store = self._store()
        session = self._mock_https_session(store, b"new")
        (tmp_path / "a.nc").write_text("old")

        result = store._download_file("https://example.com/a.nc", tmp_path, force=True)

        assert result.read_text() == "new"
        session.get.assert_called_once()
