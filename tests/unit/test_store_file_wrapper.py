"""TDD Tests for store/file_wrapper.py module.

Tests the EarthAccessFile proxy class that wraps fsspec file objects
and associates them with their source granule metadata.
"""

import pickle
from io import BytesIO
from typing import Any
from unittest.mock import Mock, patch


class TestEarthAccessFileCreation:
    """Test EarthAccessFile instantiation."""

    def test_create_with_file_and_granule(self) -> None:
        """Test creating EarthAccessFile with file and granule."""
        from earthaccess.store.file_wrapper import EarthAccessFile

        mock_file = Mock()
        mock_granule = Mock()

        wrapper = EarthAccessFile(mock_file, mock_granule)

        assert wrapper.f is mock_file
        assert wrapper.granule is mock_granule

    def test_access_underlying_file(self) -> None:
        """Test that underlying file object is accessible."""
        from earthaccess.store.file_wrapper import EarthAccessFile

        mock_file = Mock()
        mock_granule = Mock()

        wrapper = EarthAccessFile(mock_file, mock_granule)

        assert wrapper.f is mock_file

    def test_access_granule_metadata(self) -> None:
        """Test that granule metadata is accessible."""
        from earthaccess.store.file_wrapper import EarthAccessFile

        mock_file = Mock()
        mock_granule: Any = {"meta": {"concept-id": "G123456"}}

        wrapper = EarthAccessFile(mock_file, mock_granule)

        assert wrapper.granule["meta"]["concept-id"] == "G123456"


class TestEarthAccessFileProxy:
    """Test EarthAccessFile proxy behavior."""

    def test_proxies_read_method(self) -> None:
        """Test that read() is proxied to underlying file."""
        from earthaccess.store.file_wrapper import EarthAccessFile

        mock_file = Mock()
        mock_file.read.return_value = b"file content"
        mock_granule = Mock()

        wrapper = EarthAccessFile(mock_file, mock_granule)
        result = wrapper.read()

        mock_file.read.assert_called_once()
        assert result == b"file content"

    def test_proxies_seek_method(self) -> None:
        """Test that seek() is proxied to underlying file."""
        from earthaccess.store.file_wrapper import EarthAccessFile

        mock_file = Mock()
        mock_file.seek.return_value = 100
        mock_granule = Mock()

        wrapper = EarthAccessFile(mock_file, mock_granule)
        result = wrapper.seek(100)

        mock_file.seek.assert_called_once_with(100)
        assert result == 100

    def test_proxies_tell_method(self) -> None:
        """Test that tell() is proxied to underlying file."""
        from earthaccess.store.file_wrapper import EarthAccessFile

        mock_file = Mock()
        mock_file.tell.return_value = 50
        mock_granule = Mock()

        wrapper = EarthAccessFile(mock_file, mock_granule)
        result = wrapper.tell()

        mock_file.tell.assert_called_once()
        assert result == 50

    def test_proxies_close_method(self) -> None:
        """Test that close() is proxied to underlying file."""
        from earthaccess.store.file_wrapper import EarthAccessFile

        mock_file = Mock()
        mock_granule = Mock()

        wrapper = EarthAccessFile(mock_file, mock_granule)
        wrapper.close()

        mock_file.close.assert_called_once()

    def test_proxies_arbitrary_attributes(self) -> None:
        """Test that arbitrary attributes are proxied."""
        from earthaccess.store.file_wrapper import EarthAccessFile

        mock_file = Mock()
        mock_file.path = "/some/path"
        mock_file.size = 1024
        mock_granule = Mock()

        wrapper = EarthAccessFile(mock_file, mock_granule)

        assert wrapper.path == "/some/path"
        assert wrapper.size == 1024

    def test_own_attributes_not_proxied(self) -> None:
        """Test that 'f' and 'granule' are not proxied."""
        from earthaccess.store.file_wrapper import EarthAccessFile

        mock_file = Mock()
        mock_file.f = "should not get this"
        mock_file.granule = "should not get this either"
        mock_granule: Any = {"id": "granule123"}

        wrapper = EarthAccessFile(mock_file, mock_granule)

        # These should return our attributes, not the mock's
        assert wrapper.f is mock_file
        assert wrapper.granule == {"id": "granule123"}


class TestEarthAccessFileRepr:
    """Test EarthAccessFile string representation."""

    def test_repr_delegates_to_file(self) -> None:
        """Test that __repr__ delegates to underlying file."""
        from earthaccess.store.file_wrapper import EarthAccessFile

        mock_file = Mock()
        setattr(mock_file, "__repr__", Mock(return_value="<MockFile>"))
        mock_granule = Mock()

        wrapper = EarthAccessFile(mock_file, mock_granule)
        result = repr(wrapper)

        assert result == "<MockFile>"


class TestEarthAccessFileSerialization:
    """Test EarthAccessFile pickle serialization."""

    def test_can_be_pickled(self) -> None:
        """Test that EarthAccessFile can be pickled."""
        from earthaccess.store.file_wrapper import EarthAccessFile

        # Create a simple file-like object that can be pickled
        mock_file = BytesIO(b"test content")
        mock_granule: Any = {"meta": {"concept-id": "G123"}}

        wrapper = EarthAccessFile(mock_file, mock_granule)

        # Should not raise
        pickled = pickle.dumps(wrapper)
        assert pickled is not None

    def test_reduce_returns_correct_structure(self) -> None:
        """Test that __reduce_ex__ returns correct unpickling info."""
        from earthaccess.store.file_wrapper import EarthAccessFile

        mock_file = BytesIO(b"test content")
        mock_granule: Any = {"meta": {"concept-id": "G123"}}

        wrapper = EarthAccessFile(mock_file, mock_granule)

        # Get the reduce tuple
        with patch("earthaccess.__auth__") as mock_auth:
            mock_auth.authenticated = True
            reduce_result = wrapper.__reduce_ex__(pickle.HIGHEST_PROTOCOL)

        # Should be a tuple (callable, args)
        assert len(reduce_result) == 2
        assert callable(reduce_result[0])


class TestMakeInstance:
    """Test the make_instance function for deserialization."""

    def test_make_instance_creates_wrapper(self) -> None:
        """Test that make_instance creates an EarthAccessFile."""
        from earthaccess.store.file_wrapper import EarthAccessFile, make_instance

        mock_file = BytesIO(b"test content")
        mock_granule: Any = {"meta": {"concept-id": "G123"}}

        # Pickle the file
        pickled_file = pickle.dumps(mock_file)

        with patch("earthaccess.__auth__") as mock_auth:
            mock_auth.authenticated = True
            result = make_instance(
                EarthAccessFile, mock_granule, mock_auth, pickled_file
            )

        assert isinstance(result, EarthAccessFile)
        assert result.granule == mock_granule


class TestOptimalBlockSize:
    """Test the optimal block size calculation."""

    def test_small_file_block_size(self) -> None:
        """Test block size for small files (<100MB)."""
        from earthaccess.store.file_wrapper import optimal_block_size

        # 50MB file
        file_size = 50 * 1024 * 1024
        block_size = optimal_block_size(file_size)

        # Should be 4MB for small files
        assert block_size == 4 * 1024 * 1024

    def test_large_file_block_size(self) -> None:
        """Test block size for large files (>100MB)."""
        from earthaccess.store.file_wrapper import optimal_block_size

        # 500MB file
        file_size = 500 * 1024 * 1024
        block_size = optimal_block_size(file_size)

        # Should be between 4MB and 16MB
        assert 4 * 1024 * 1024 <= block_size <= 16 * 1024 * 1024

    def test_very_large_file_block_size(self) -> None:
        """Test block size for very large files."""
        from earthaccess.store.file_wrapper import optimal_block_size

        # 2GB file
        file_size = 2 * 1024 * 1024 * 1024
        block_size = optimal_block_size(file_size)

        # Should cap at 16MB
        assert block_size == 16 * 1024 * 1024


class TestIsInteractive:
    """Test interactive session detection."""

    def test_is_interactive_returns_bool(self) -> None:
        """Test that is_interactive returns a boolean."""
        from earthaccess.store.file_wrapper import is_interactive

        result = is_interactive()
        assert isinstance(result, bool)


class TestOpenFilesHelper:
    """Test the open_files helper function."""

    def test_open_files_returns_list(self) -> None:
        """Test that open_files returns a list of EarthAccessFile."""
        from earthaccess.store.file_wrapper import open_files

        # Create mock filesystem
        mock_fs = Mock()
        mock_fs.info.return_value = {"size": 1024}
        mock_fs.open.return_value = BytesIO(b"content")

        # Create URL mapping
        url_mapping: Any = {"s3://bucket/file1.nc": {"meta": {"concept-id": "G1"}}}

        with patch("earthaccess.store.file_wrapper.get_executor") as mock_get_executor:
            mock_executor = Mock()
            mock_executor.map.return_value = iter([Mock()])
            mock_executor.shutdown = Mock()
            mock_get_executor.return_value = mock_executor

            result = open_files(url_mapping, mock_fs, show_progress=False)

        assert isinstance(result, list)


class TestUrlGranuleMapping:
    """Test URL to granule mapping helper."""

    def test_get_url_granule_mapping(self) -> None:
        """Test creating URL to granule mapping."""
        from earthaccess.store.file_wrapper import get_url_granule_mapping

        # Create mock granules
        mock_granule1 = Mock()
        mock_granule1.data_links.return_value = ["s3://bucket/file1.nc"]

        mock_granule2 = Mock()
        mock_granule2.data_links.return_value = [
            "s3://bucket/file2.nc",
            "s3://bucket/file3.nc",
        ]

        granules: Any = [mock_granule1, mock_granule2]

        result = get_url_granule_mapping(granules, access="direct")

        assert "s3://bucket/file1.nc" in result
        assert "s3://bucket/file2.nc" in result
        assert "s3://bucket/file3.nc" in result
        assert result["s3://bucket/file1.nc"] is mock_granule1
        assert result["s3://bucket/file2.nc"] is mock_granule2
