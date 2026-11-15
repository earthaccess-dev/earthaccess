"""Tests for FileSystemFactory implementations.

This module tests the factory pattern for creating authenticated filesystems.
It tests both the abstract factory interface and concrete implementations.
"""

import datetime
from typing import Any, cast
from unittest.mock import Mock, patch

import pytest
import s3fs
from earthaccess.auth.credentials import HTTPHeaders, S3Credentials
from earthaccess.store.filesystems import (
    DefaultFileSystemFactory,
    FileSystemFactory,
    MockFileSystemFactory,
)


class TestFileSystemFactoryAbstractInterface:
    """Test that FileSystemFactory defines the correct abstract interface."""

    def test_factory_is_abstract(self) -> None:
        """FileSystemFactory should be abstract and not instantiable."""
        with pytest.raises(TypeError, match="abstract"):
            FileSystemFactory()  # type: ignore

    def test_factory_requires_s3_method(self) -> None:
        """FileSystemFactory must define create_s3_filesystem."""
        assert hasattr(FileSystemFactory, "create_s3_filesystem")
        assert getattr(
            FileSystemFactory.create_s3_filesystem, "__isabstractmethod__", False
        )

    def test_factory_requires_https_method(self) -> None:
        """FileSystemFactory must define create_https_filesystem."""
        assert hasattr(FileSystemFactory, "create_https_filesystem")
        assert getattr(
            FileSystemFactory.create_https_filesystem, "__isabstractmethod__", False
        )

    def test_factory_requires_default_method(self) -> None:
        """FileSystemFactory must define create_default_filesystem."""
        assert hasattr(FileSystemFactory, "create_default_filesystem")
        assert getattr(
            FileSystemFactory.create_default_filesystem, "__isabstractmethod__", False
        )


class TestDefaultFileSystemFactoryS3:
    """Test DefaultFileSystemFactory S3 filesystem creation."""

    def test_create_s3_filesystem_with_valid_credentials(self) -> None:
        """Should create s3fs.S3FileSystem with valid credentials."""
        factory = DefaultFileSystemFactory()

        # Create credentials that are not expired (use utcnow for consistency with is_expired())
        future_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            hours=1
        )
        credentials = S3Credentials(
            access_key="test_key",
            secret_key="test_secret",
            session_token="test_token",
            expiration_time=future_time,
            region="us-west-2",
        )

        with patch("earthaccess.store.filesystems.s3fs.S3FileSystem") as mock_s3fs:
            factory.create_s3_filesystem(credentials)

            # Verify s3fs.S3FileSystem was called with correct kwargs
            mock_s3fs.assert_called_once_with(
                key="test_key",
                secret="test_secret",
                token="test_token",
                region_name="us-west-2",
            )

    def test_create_s3_filesystem_rejects_expired_credentials(self) -> None:
        """Should raise ValueError if credentials are expired."""
        factory = DefaultFileSystemFactory()

        # Create expired credentials
        past_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            hours=1
        )
        credentials = S3Credentials(
            access_key="test_key",
            secret_key="test_secret",
            session_token="test_token",
            expiration_time=past_time,
            region="us-west-2",
        )

        with pytest.raises(ValueError, match="expired"):
            factory.create_s3_filesystem(credentials)

    def test_create_s3_filesystem_with_region(self) -> None:
        """Should create s3fs.S3FileSystem with region information."""
        factory = DefaultFileSystemFactory()

        future_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            hours=1
        )
        credentials = S3Credentials(
            access_key="test_key",
            secret_key="test_secret",
            session_token="test_token",
            expiration_time=future_time,
            region="eu-west-1",
        )

        with patch("earthaccess.store.filesystems.s3fs.S3FileSystem") as mock_s3fs:
            factory.create_s3_filesystem(credentials)

            # Verify region is passed through
            mock_s3fs.assert_called_once()
            call_kwargs = mock_s3fs.call_args[1]
            assert "key" in call_kwargs
            assert "secret" in call_kwargs
            assert "token" in call_kwargs
            assert "region_name" in call_kwargs

    def test_s3_credentials_conversion_to_dict(self) -> None:
        """S3Credentials.to_dict() should include all necessary fields."""
        future_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            hours=1
        )
        credentials = S3Credentials(
            access_key="test_key",
            secret_key="test_secret",
            session_token="test_token",
            expiration_time=future_time,
            region="us-west-2",
        )

        cred_dict = credentials.to_dict()

        assert cred_dict["key"] == "test_key"
        assert cred_dict["secret"] == "test_secret"
        assert cred_dict["token"] == "test_token"
        assert cred_dict["region_name"] == "us-west-2"


class TestDefaultFileSystemFactoryHTTPS:
    """Test DefaultFileSystemFactory HTTPS filesystem creation."""

    def test_create_https_filesystem_with_headers(self) -> None:
        """Should create fsspec HTTPS filesystem with headers."""
        factory = DefaultFileSystemFactory()

        headers = HTTPHeaders(
            headers={"Authorization": "Bearer token123"},
            cookies={},
        )

        with patch("earthaccess.store.filesystems.fsspec.filesystem") as mock_fsspec:
            factory.create_https_filesystem(headers)

            mock_fsspec.assert_called_once_with(
                "https",
                client_kwargs={
                    "headers": {"Authorization": "Bearer token123"},
                    "trust_env": False,
                },
            )

    def test_create_https_filesystem_with_headers_and_cookies(self) -> None:
        """Should create HTTPS filesystem with both headers and cookies."""
        factory = DefaultFileSystemFactory()

        headers = HTTPHeaders(
            headers={"Authorization": "Bearer token123"},
            cookies={"session": "abc123"},
        )

        with patch("earthaccess.store.filesystems.fsspec.filesystem") as mock_fsspec:
            factory.create_https_filesystem(headers)

            mock_fsspec.assert_called_once()
            call_kwargs = mock_fsspec.call_args[1]
            assert (
                call_kwargs["client_kwargs"]["headers"]["Authorization"]
                == "Bearer token123"
            )
            assert call_kwargs["client_kwargs"]["cookies"]["session"] == "abc123"

    def test_create_https_filesystem_with_empty_headers(self) -> None:
        """Should create HTTPS filesystem with empty headers dict."""
        factory = DefaultFileSystemFactory()

        headers = HTTPHeaders(headers={}, cookies={})

        with patch("earthaccess.store.filesystems.fsspec.filesystem") as mock_fsspec:
            factory.create_https_filesystem(headers)

            mock_fsspec.assert_called_once_with(
                "https",
                client_kwargs={
                    "headers": {},
                    "trust_env": False,
                },
            )

    def test_create_https_filesystem_with_none_headers(self) -> None:
        """Should handle None headers gracefully."""
        factory = DefaultFileSystemFactory()

        headers = HTTPHeaders(headers=cast(Any, None), cookies=cast(Any, None))

        with patch("earthaccess.store.filesystems.fsspec.filesystem") as mock_fsspec:
            factory.create_https_filesystem(headers)

            mock_fsspec.assert_called_once()
            call_kwargs = mock_fsspec.call_args[1]
            assert call_kwargs["client_kwargs"]["headers"] == {}


class TestDefaultFileSystemFactoryDefault:
    """Test DefaultFileSystemFactory default filesystem creation."""

    def test_create_default_filesystem(self) -> None:
        """Should create default HTTPS filesystem without auth."""
        factory = DefaultFileSystemFactory()

        with patch("earthaccess.store.filesystems.fsspec.filesystem") as mock_fsspec:
            factory.create_default_filesystem()

            mock_fsspec.assert_called_once_with("https")

    def test_create_default_filesystem_no_credentials(self) -> None:
        """Default filesystem should not require credentials."""
        factory = DefaultFileSystemFactory()

        with patch("earthaccess.store.filesystems.fsspec.filesystem") as mock_fsspec:
            factory.create_default_filesystem()

            # Should complete without error
            mock_fsspec.assert_called_once()


class TestMockFileSystemFactory:
    """Test MockFileSystemFactory for testing purposes."""

    def test_mock_factory_with_all_mocks(self) -> None:
        """Should create factory with all mock filesystems."""
        mock_s3 = Mock(spec=s3fs.S3FileSystem)
        mock_https = Mock()
        mock_default = Mock()

        factory = MockFileSystemFactory(
            s3_filesystem=mock_s3,
            https_filesystem=mock_https,
            default_filesystem=mock_default,
        )

        assert factory is not None

    def test_mock_factory_s3_returns_configured_filesystem(self) -> None:
        """Mock S3 factory should return configured filesystem."""
        mock_s3 = Mock(spec=s3fs.S3FileSystem)
        factory = MockFileSystemFactory(s3_filesystem=mock_s3)

        future_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            hours=1
        )
        credentials = S3Credentials(
            access_key="test",
            secret_key="test",
            session_token="test",
            expiration_time=future_time,
            region="us-west-2",
        )

        result = factory.create_s3_filesystem(credentials)
        assert result is mock_s3

    def test_mock_factory_s3_raises_if_not_configured(self) -> None:
        """Mock S3 factory should raise if not configured."""
        factory = MockFileSystemFactory()

        future_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            hours=1
        )
        credentials = S3Credentials(
            access_key="test",
            secret_key="test",
            session_token="test",
            expiration_time=future_time,
            region="us-west-2",
        )

        with pytest.raises(RuntimeError, match="S3FileSystem not configured"):
            factory.create_s3_filesystem(credentials)

    def test_mock_factory_https_returns_configured_filesystem(self) -> None:
        """Mock HTTPS factory should return configured filesystem."""
        mock_https = Mock()
        factory = MockFileSystemFactory(https_filesystem=mock_https)

        headers = HTTPHeaders(headers={}, cookies={})
        result = factory.create_https_filesystem(headers)

        assert result is mock_https

    def test_mock_factory_https_raises_if_not_configured(self) -> None:
        """Mock HTTPS factory should raise if not configured."""
        factory = MockFileSystemFactory()

        headers = HTTPHeaders(headers={}, cookies={})

        with pytest.raises(RuntimeError, match="HTTPS filesystem not configured"):
            factory.create_https_filesystem(headers)

    def test_mock_factory_default_returns_configured_filesystem(self) -> None:
        """Mock default factory should return configured filesystem."""
        mock_default = Mock()
        factory = MockFileSystemFactory(default_filesystem=mock_default)

        result = factory.create_default_filesystem()
        assert result is mock_default

    def test_mock_factory_default_raises_if_not_configured(self) -> None:
        """Mock default factory should raise if not configured."""
        factory = MockFileSystemFactory()

        with pytest.raises(RuntimeError, match="default filesystem not configured"):
            factory.create_default_filesystem()


class TestFactoryPolymorphism:
    """Test that all factories are truly polymorphic."""

    def test_all_factories_implement_interface(self) -> None:
        """All factory implementations should implement the interface."""
        factories = [
            DefaultFileSystemFactory(),
            MockFileSystemFactory(),
        ]

        for factory in factories:
            assert isinstance(factory, FileSystemFactory)

    def test_factories_can_be_substituted(self) -> None:
        """Any factory should be usable in place of another."""

        def use_factory(factory: FileSystemFactory) -> bool:
            """Helper function that uses any factory."""
            return hasattr(factory, "create_s3_filesystem")

        default_factory = DefaultFileSystemFactory()
        mock_factory = MockFileSystemFactory()

        assert use_factory(default_factory)
        assert use_factory(mock_factory)

    def test_factory_method_signatures_are_compatible(self) -> None:
        """All factories should have compatible method signatures."""
        default_factory = DefaultFileSystemFactory()
        mock_factory = MockFileSystemFactory()

        # Check that methods exist with expected signatures
        for factory in [default_factory, mock_factory]:
            assert callable(getattr(factory, "create_s3_filesystem"))
            assert callable(getattr(factory, "create_https_filesystem"))
            assert callable(getattr(factory, "create_default_filesystem"))
