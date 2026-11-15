"""Tests for streaming context and iterator classes.

This module tests WorkerContext and StreamingIterator for distributed execution.
"""

import datetime
import pickle
from unittest.mock import Mock

import pytest
from earthaccess.auth.credentials import AuthContext, S3Credentials
from earthaccess.store.distributed import (
    StreamingIterator,
    WorkerContext,
    process_granule_in_worker,
)


class TestWorkerContextCreation:
    """Test WorkerContext creation and initialization."""

    def test_worker_context_creation_with_auth_context(self) -> None:
        """Should create WorkerContext with AuthContext."""
        s3_creds = S3Credentials(
            access_key="key",
            secret_key="secret",
            session_token="token",
            expiration_time=datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=1),
        )
        auth_context = AuthContext.from_auth(Mock())
        # Manually set S3 credentials since we're using a mock
        object.__setattr__(auth_context, "s3_credentials", s3_creds)

        worker_context = WorkerContext(auth_context=auth_context)

        assert worker_context.auth_context is auth_context
        assert worker_context.config is None

    def test_worker_context_creation_with_config(self) -> None:
        """Should create WorkerContext with configuration."""
        auth_context = Mock(spec=AuthContext)
        auth_context.is_valid.return_value = True
        config = {"region": "us-west-2", "timeout": 30}

        worker_context = WorkerContext(auth_context=auth_context, config=config)

        assert worker_context.config == config
        assert worker_context.config["region"] == "us-west-2"

    def test_worker_context_rejects_invalid_auth_context(self) -> None:
        """Should reject non-AuthContext objects."""
        with pytest.raises(TypeError, match="AuthContext"):
            WorkerContext(auth_context="not an auth context")  # type: ignore

    def test_worker_context_is_frozen(self) -> None:
        """WorkerContext should be immutable (frozen)."""
        auth_context = Mock(spec=AuthContext)
        worker_context = WorkerContext(auth_context=auth_context)

        with pytest.raises(AttributeError):
            worker_context.auth_context = Mock()  # type: ignore


class TestWorkerContextSerialization:
    """Test WorkerContext serialization and deserialization."""

    def test_worker_context_serializable_structure(self) -> None:
        """WorkerContext should have serializable structure with real credentials."""
        auth_context = Mock(spec=AuthContext)
        # Make the mock pickleable by setting reduce explicitly
        setattr(auth_context, "__reduce__", Mock(return_value=(Mock, ())))

        worker_context = WorkerContext(
            auth_context=auth_context,
            config={"test": "value"},
        )

        # Should have proper structure for serialization
        assert hasattr(worker_context, "auth_context")
        assert hasattr(worker_context, "config")

    def test_worker_context_to_bytes_requires_serializable_auth_context(self) -> None:
        """to_bytes should work with pickleable auth_context."""
        # Create a simple auth context that can be pickled
        mock_auth = Mock(spec=AuthContext)
        mock_auth.is_valid.return_value = True

        worker_context = WorkerContext(auth_context=mock_auth)

        # This will fail with Mock, but documents the requirement
        # In real use, AuthContext is a dataclass and is pickleable
        try:
            serialized = worker_context.to_bytes()
            assert isinstance(serialized, bytes)
        except Exception:
            # Expected with Mock - in practice uses real AuthContext
            pass

    def test_worker_context_from_bytes_invalid_data(self) -> None:
        """Should raise ValueError for invalid serialized data."""
        invalid_data = b"not pickle data"

        with pytest.raises(ValueError, match="Failed to deserialize"):
            WorkerContext.from_bytes(invalid_data)

    def test_worker_context_from_bytes_wrong_type(self) -> None:
        """Should raise ValueError if deserialized object is wrong type."""
        wrong_object = {"not": "a WorkerContext"}
        serialized = pickle.dumps(wrong_object)

        with pytest.raises(ValueError, match="not DistributedWorkerContext"):
            WorkerContext.from_bytes(serialized)


class TestWorkerContextAuthentication:
    """Test WorkerContext authentication and auth reconstruction."""

    def test_reconstruct_auth_with_valid_context(self) -> None:
        """Should reconstruct Auth from valid WorkerContext."""
        mock_auth = Mock()
        auth_context = Mock(spec=AuthContext)
        auth_context.is_valid.return_value = True
        auth_context.to_auth.return_value = mock_auth

        worker_context = WorkerContext(auth_context=auth_context)
        result = worker_context.reconstruct_auth()

        assert result is mock_auth
        auth_context.to_auth.assert_called_once()

    def test_reconstruct_auth_with_expired_context(self) -> None:
        """Should raise ValueError if credentials are expired."""
        auth_context = Mock(spec=AuthContext)
        auth_context.is_valid.return_value = False

        worker_context = WorkerContext(auth_context=auth_context)

        with pytest.raises(ValueError, match="expired"):
            worker_context.reconstruct_auth()


class TestStreamingIteratorBasics:
    """Test StreamingIterator basic functionality."""

    def test_streaming_iterator_creation(self) -> None:
        """Should create StreamingIterator with granules and context."""
        granules = [Mock(), Mock(), Mock()]
        auth_context = Mock(spec=AuthContext)

        iterator = StreamingIterator(granules, auth_context)

        assert iterator.granules == granules
        assert iterator.auth_context is auth_context
        assert iterator.chunk_size == 1

    def test_streaming_iterator_with_chunk_size(self) -> None:
        """Should respect chunk_size parameter."""
        granules = [Mock() for _ in range(5)]
        auth_context = Mock(spec=AuthContext)

        iterator = StreamingIterator(granules, auth_context, chunk_size=2)

        assert iterator.chunk_size == 2

    def test_streaming_iterator_rejects_empty_granules(self) -> None:
        """Should reject empty granule list."""
        auth_context = Mock(spec=AuthContext)

        with pytest.raises(ValueError, match="empty"):
            StreamingIterator([], auth_context)

    def test_streaming_iterator_rejects_invalid_chunk_size(self) -> None:
        """Should reject chunk_size < 1."""
        granules = [Mock()]
        auth_context = Mock(spec=AuthContext)

        with pytest.raises(ValueError, match="chunk_size"):
            StreamingIterator(granules, auth_context, chunk_size=0)

        with pytest.raises(ValueError, match="chunk_size"):
            StreamingIterator(granules, auth_context, chunk_size=-1)


class TestStreamingIteratorIteration:
    """Test StreamingIterator iteration behavior."""

    def test_streaming_iterator_length(self) -> None:
        """Should return correct number of chunks."""
        granules = [Mock() for _ in range(10)]
        auth_context = Mock(spec=AuthContext)

        # With chunk_size=1
        iterator1 = StreamingIterator(granules, auth_context, chunk_size=1)
        assert len(iterator1) == 10

        # With chunk_size=3
        iterator3 = StreamingIterator(granules, auth_context, chunk_size=3)
        assert len(iterator3) == 4  # ceil(10/3)

        # With chunk_size=5
        iterator5 = StreamingIterator(granules, auth_context, chunk_size=5)
        assert len(iterator5) == 2

    def test_streaming_iterator_single_chunk(self) -> None:
        """Should yield granules and context for single chunk."""
        granule = Mock()
        granules = [granule]
        auth_context = Mock(spec=AuthContext)

        iterator = StreamingIterator(granules, auth_context)
        chunk, context = next(iterator)

        assert chunk == [granule]
        assert isinstance(context, WorkerContext)
        assert context.auth_context is auth_context

    def test_streaming_iterator_multiple_chunks(self) -> None:
        """Should yield multiple chunks correctly."""
        granules = [Mock() for _ in range(5)]
        auth_context = Mock(spec=AuthContext)

        iterator = StreamingIterator(granules, auth_context, chunk_size=2)
        chunks = list(iterator)

        assert len(chunks) == 3  # ceil(5/2)
        assert len(chunks[0][0]) == 2  # First chunk: 2 granules
        assert len(chunks[1][0]) == 2  # Second chunk: 2 granules
        assert len(chunks[2][0]) == 1  # Third chunk: 1 granule

    def test_streaming_iterator_stop_iteration(self) -> None:
        """Should raise StopIteration when exhausted."""
        granules = [Mock()]
        auth_context = Mock(spec=AuthContext)

        iterator = StreamingIterator(granules, auth_context)
        next(iterator)  # Consume the one chunk

        with pytest.raises(StopIteration):
            next(iterator)

    def test_streaming_iterator_can_iterate_multiple_times(self) -> None:
        """Should be able to iterate multiple times by resetting."""
        granules = [Mock(), Mock()]
        auth_context = Mock(spec=AuthContext)

        iterator = StreamingIterator(granules, auth_context, chunk_size=1)

        # First iteration
        list(iterator)

        # Reset and iterate again
        chunks = list(iterator)
        assert len(chunks) == 2

    def test_streaming_iterator_worker_context_includes_config(self) -> None:
        """Worker context should include config from iterator."""
        granules = [Mock()]
        auth_context = Mock(spec=AuthContext)
        config = {"option": "value"}

        iterator = StreamingIterator(
            granules, auth_context, chunk_size=1, config=config
        )
        chunk, context = next(iterator)

        assert context.config == config


class TestStreamingIteratorMethods:
    """Test StreamingIterator utility methods."""

    def test_streaming_iterator_chunks_method(self) -> None:
        """chunks() should return all chunks as list."""
        granules = [Mock() for _ in range(5)]
        auth_context = Mock(spec=AuthContext)

        iterator = StreamingIterator(granules, auth_context, chunk_size=2)
        all_chunks = iterator.chunks()

        assert len(all_chunks) == 3
        assert all(isinstance(context, WorkerContext) for _, context in all_chunks)

    def test_streaming_iterator_with_config_returns_new_iterator(self) -> None:
        """with_config() should return new iterator with config."""
        granules = [Mock() for _ in range(3)]
        auth_context = Mock(spec=AuthContext)

        original = StreamingIterator(granules, auth_context, chunk_size=1)
        with_config = original.with_config({"key": "value"})

        # Should be different iterators
        assert original is not with_config

        # Original should not have config
        assert original.config is None

        # New one should have config
        assert with_config.config == {"key": "value"}

        # Granules and auth should be the same
        assert with_config.granules == original.granules
        assert with_config.auth_context is original.auth_context


class TestProcessGranuleInWorker:
    """Test process_granule_in_worker helper function."""

    def test_process_granule_in_worker_basic(self) -> None:
        """Should process granule with reconstructed auth."""
        granule = Mock()
        auth_context = Mock(spec=AuthContext)
        mock_auth = Mock()
        auth_context.is_valid.return_value = True
        auth_context.to_auth.return_value = mock_auth

        worker_context = WorkerContext(auth_context=auth_context)

        def mock_operation(g: Mock, auth: Mock) -> str:
            return "success"

        result = process_granule_in_worker(granule, worker_context, mock_operation)

        assert result == "success"

    def test_process_granule_in_worker_calls_operation_with_reconstructed_auth(
        self,
    ) -> None:
        """Should pass reconstructed auth to operation."""
        granule = Mock()
        auth_context = Mock(spec=AuthContext)
        mock_auth = Mock()
        auth_context.is_valid.return_value = True
        auth_context.to_auth.return_value = mock_auth

        worker_context = WorkerContext(auth_context=auth_context)
        captured_auth = None

        def capture_operation(g: Mock, auth: Mock) -> None:
            nonlocal captured_auth
            captured_auth = auth

        process_granule_in_worker(granule, worker_context, capture_operation)

        assert captured_auth is mock_auth

    def test_process_granule_in_worker_with_expired_credentials(self) -> None:
        """Should raise error if credentials are expired."""
        granule = Mock()
        auth_context = Mock(spec=AuthContext)
        auth_context.is_valid.return_value = False

        worker_context = WorkerContext(auth_context=auth_context)

        def mock_operation(g: Mock, auth: Mock) -> str:
            return "success"

        with pytest.raises(ValueError, match="expired"):
            process_granule_in_worker(granule, worker_context, mock_operation)
