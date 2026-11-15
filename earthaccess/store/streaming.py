"""Streaming execution utilities for earthaccess.

This module provides streaming/lazy execution capabilities for processing
large result sets without loading everything into memory.

Key components:
- AuthContext: Serializable credential container for distributed workers
- WorkerContext: Thread-local state management
- StreamingExecutor: Iterator-based executor with backpressure support
"""

from __future__ import annotations

import queue
import threading
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import (
    Any,
    Callable,
    Dict,
    Generic,
    Iterable,
    Iterator,
    List,
    Optional,
    Tuple,
    TypeVar,
    Union,
)

__all__ = [
    "AuthContext",
    "WorkerContext",
    "StreamingExecutor",
    "StreamingIterator",
]

T = TypeVar("T")
R = TypeVar("R")


# =============================================================================
# Auth Context - Serializable credentials for distributed workers
# =============================================================================


@dataclass(frozen=True)
class AuthContext:
    """Serializable authentication context for shipping credentials to workers.

    This dataclass captures all the necessary authentication information
    that workers need to access NASA Earthdata resources. It's designed
    to be picklable for distributed computing frameworks.

    Attributes:
        username: Earthdata Login username
        password: Earthdata Login password (optional if using tokens)
        token: Bearer token for API access
        s3_credentials: Dictionary of S3 credentials by provider/endpoint
        token_expiry: When the token expires (UTC)
        created_at: When this context was created (UTC)

    Example:
        >>> from earthaccess.streaming import AuthContext
        >>> ctx = AuthContext.from_auth(auth)
        >>> # Ship to worker
        >>> worker_auth = ctx.to_auth()
    """

    username: Optional[str] = None
    password: Optional[str] = None
    token: Optional[str] = None
    s3_credentials: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    token_expiry: Optional[datetime] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_auth(cls, auth: Any) -> "AuthContext":
        """Create an AuthContext from an earthaccess Auth instance.

        Parameters:
            auth: An earthaccess.Auth instance

        Returns:
            A new AuthContext with credentials copied from the Auth instance

        Example:
            >>> import earthaccess
            >>> auth = earthaccess.login()
            >>> ctx = AuthContext.from_auth(auth)
        """
        # Import here to avoid circular imports
        from earthaccess.auth import Auth

        if not isinstance(auth, Auth):
            raise TypeError(f"Expected Auth instance, got {type(auth)}")

        username = getattr(auth, "username", None)
        password = getattr(auth, "password", None)
        token = None

        # Try to get the token if available
        token_data = getattr(auth, "_token", None)
        if token_data and isinstance(token_data, dict):
            token = token_data.get("access_token")

        return cls(
            username=username,
            password=password,
            token=token,
            s3_credentials={},
            token_expiry=None,  # Could be extracted if available
        )

    def to_auth(self) -> Any:
        """Create an Auth instance from this context.

        Returns:
            A new earthaccess.Auth instance initialized with these credentials

        Note:
            The returned Auth instance may have limited functionality
            compared to one created via normal login flow.
        """
        from earthaccess.auth import Auth

        auth = Auth()
        auth.authenticated = True

        if self.username:
            auth.username = self.username
        if self.password:
            auth.password = self.password
        if self.token:
            # Use setattr to avoid type checking issues with dynamic attributes
            setattr(auth, "_token", {"access_token": self.token})

        return auth

    def is_expired(self) -> bool:
        """Check if the token has expired.

        Returns:
            True if the token has expired, False otherwise
        """
        if self.token_expiry is None:
            return False
        return datetime.now(timezone.utc) > self.token_expiry

    def with_s3_credentials(
        self, endpoint: str, credentials: Dict[str, Any]
    ) -> "AuthContext":
        """Return a new AuthContext with additional S3 credentials.

        Parameters:
            endpoint: The S3 endpoint URL (e.g., "https://data.lpdaac.earthdatacloud.nasa.gov/s3credentials")
            credentials: The S3 credentials dict (accessKeyId, secretAccessKey, sessionToken, expiration)

        Returns:
            A new AuthContext with the additional credentials
        """
        new_creds = dict(self.s3_credentials)
        new_creds[endpoint] = credentials
        return AuthContext(
            username=self.username,
            password=self.password,
            token=self.token,
            s3_credentials=new_creds,
            token_expiry=self.token_expiry,
            created_at=self.created_at,
        )

    def get_s3_credentials(self, endpoint: str) -> Optional[Dict[str, Any]]:
        """Get cached S3 credentials for an endpoint.

        Parameters:
            endpoint: The S3 endpoint URL

        Returns:
            The cached credentials or None if not available
        """
        return self.s3_credentials.get(endpoint)


# =============================================================================
# Worker Context - Thread-local state management
# =============================================================================


class WorkerContext:
    """Thread-local context manager for worker state.

    This class manages thread-local storage for authentication and
    other state that needs to be available within worker threads.

    Example:
        >>> ctx = WorkerContext()
        >>> with ctx.activate(auth_context):
        ...     # Worker code here - auth is available via WorkerContext.current()
        ...     current = WorkerContext.current()
    """

    _local = threading.local()
    _instances: Dict[int, "WorkerContext"] = {}

    def __init__(
        self,
        auth_context: Optional[AuthContext] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Initialize worker context.

        Parameters:
            auth_context: Optional authentication context
            metadata: Optional metadata dictionary
        """
        self.auth_context = auth_context
        self.metadata = metadata or {}
        self._active = False

    def activate(self, auth_context: Optional[AuthContext] = None) -> "WorkerContext":
        """Activate this context in the current thread.

        Parameters:
            auth_context: Optional auth context to use (overrides existing)

        Returns:
            Self for use as context manager
        """
        if auth_context is not None:
            self.auth_context = auth_context
        return self

    def __enter__(self) -> "WorkerContext":
        """Enter the context manager, making this context current."""
        self._active = True
        thread_id = threading.get_ident()
        WorkerContext._instances[thread_id] = self
        WorkerContext._local.context = self
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit the context manager."""
        self._active = False
        thread_id = threading.get_ident()
        if thread_id in WorkerContext._instances:
            del WorkerContext._instances[thread_id]
        if hasattr(WorkerContext._local, "context"):
            del WorkerContext._local.context

    @classmethod
    def current(cls) -> Optional["WorkerContext"]:
        """Get the current thread's WorkerContext.

        Returns:
            The active WorkerContext for this thread, or None
        """
        return getattr(cls._local, "context", None)

    @classmethod
    def get_auth_context(cls) -> Optional[AuthContext]:
        """Get the current thread's AuthContext.

        Returns:
            The AuthContext from the current WorkerContext, or None
        """
        ctx = cls.current()
        return ctx.auth_context if ctx else None


# =============================================================================
# Streaming Iterator - Lazy result consumption
# =============================================================================


class StreamingIterator(Generic[T]):
    """An iterator that lazily consumes results from a producer.

    This iterator implements backpressure by using a bounded queue,
    preventing memory blowup when processing large result sets.

    Attributes:
        queue: The internal queue for results
        sentinel: Marker object for end of iteration
        error: Any error that occurred during production
    """

    _SENTINEL = object()

    def __init__(
        self,
        producer: Callable[["queue.Queue[Union[T, object]]"], None],
        maxsize: int = 100,
    ):
        """Initialize the streaming iterator.

        Parameters:
            producer: A callable that puts items into the queue
            maxsize: Maximum queue size (for backpressure)
        """
        self._queue: queue.Queue[Union[T, object, Tuple[Exception]]] = queue.Queue(
            maxsize=maxsize
        )
        self._producer = producer
        self._error: Optional[Exception] = None
        self._thread: Optional[threading.Thread] = None
        self._started = False
        self._exhausted = False

    def _run_producer(self) -> None:
        """Run the producer in a background thread."""
        try:
            self._producer(self._queue)
        except Exception as e:
            # Put error tuple to signal error
            self._queue.put((e,))
        finally:
            # Signal end of iteration
            self._queue.put(self._SENTINEL)

    def _start(self) -> None:
        """Start the producer thread if not already started."""
        if not self._started:
            self._started = True
            self._thread = threading.Thread(target=self._run_producer, daemon=True)
            self._thread.start()

    def __iter__(self) -> Iterator[T]:
        """Return self as iterator."""
        return self

    def __next__(self) -> T:
        """Get the next item from the stream.

        Raises:
            StopIteration: When the stream is exhausted
            Exception: If the producer encountered an error
        """
        if self._exhausted:
            raise StopIteration

        self._start()

        item = self._queue.get()

        # Check for sentinel
        if item is self._SENTINEL:
            self._exhausted = True
            raise StopIteration

        # Check for error tuple
        if (
            isinstance(item, tuple)
            and len(item) == 1
            and isinstance(item[0], Exception)
        ):
            self._exhausted = True
            raise item[0]

        return item  # type: ignore

    def close(self) -> None:
        """Close the iterator and clean up resources."""
        self._exhausted = True
        # Drain the queue to allow producer to finish
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass


# =============================================================================
# Streaming Executor - Lazy parallel execution
# =============================================================================


class StreamingExecutor(Generic[T, R]):
    """An executor that returns results as a lazy iterator.

    Unlike standard executors that eagerly compute all results,
    StreamingExecutor processes items on-demand and implements
    backpressure to prevent memory exhaustion.

    Example:
        >>> executor = StreamingExecutor(max_workers=4)
        >>> results = executor.map(process_granule, granules)
        >>> for result in results:  # Lazily consumed
        ...     print(result)
    """

    def __init__(
        self,
        max_workers: int = 4,
        buffer_size: int = 100,
        auth_context: Optional[AuthContext] = None,
        executor: Optional[Executor] = None,
    ):
        """Initialize the streaming executor.

        Parameters:
            max_workers: Number of parallel workers
            buffer_size: Size of the result buffer (for backpressure)
            auth_context: Optional auth context for workers
            executor: Optional custom executor to use
        """
        self._max_workers = max_workers
        self._buffer_size = buffer_size
        self._auth_context = auth_context
        self._executor = executor
        self._own_executor = executor is None

    def map(
        self,
        fn: Callable[[T], R],
        items: Iterable[T],
        *,
        ordered: bool = True,
        on_error: str = "raise",
    ) -> Iterator[R]:
        """Apply a function to items lazily.

        Parameters:
            fn: Function to apply to each item
            items: Iterable of items to process
            ordered: If True, maintain input order (may buffer results)
            on_error: Error handling: "raise", "skip", or "log"

        Returns:
            A lazy iterator of results
        """

        def producer(result_queue: queue.Queue[Union[R, object]]) -> None:
            """Producer function that runs in background thread."""
            executor = self._executor or ThreadPoolExecutor(
                max_workers=self._max_workers
            )
            futures: List[Tuple[int, Future[R]]] = []

            try:
                # Submit all tasks
                for i, item in enumerate(items):
                    if self._auth_context:
                        # Wrap function to inject auth context
                        wrapped_fn = self._wrap_with_auth(fn)
                        future = executor.submit(wrapped_fn, item)
                    else:
                        future = executor.submit(fn, item)
                    futures.append((i, future))

                if ordered:
                    # Return results in order
                    for i, future in futures:
                        try:
                            result = future.result()
                            result_queue.put(result)
                        except Exception as e:
                            if on_error == "raise":
                                raise
                            elif on_error == "log":
                                import logging

                                logging.warning(f"Error processing item {i}: {e}")
                else:
                    # Return results as they complete
                    from concurrent.futures import as_completed

                    for future in as_completed([f for _, f in futures]):
                        try:
                            result = future.result()
                            result_queue.put(result)
                        except Exception as e:
                            if on_error == "raise":
                                raise
                            elif on_error == "log":
                                import logging

                                logging.warning(f"Error processing item: {e}")
            finally:
                if self._own_executor and executor:
                    executor.shutdown(wait=False)

        return StreamingIterator(producer, maxsize=self._buffer_size)

    def _wrap_with_auth(self, fn: Callable[[T], R]) -> Callable[[T], R]:
        """Wrap a function to inject auth context.

        Parameters:
            fn: The function to wrap

        Returns:
            A wrapped function that sets up WorkerContext
        """
        auth_ctx = self._auth_context

        def wrapped(item: T) -> R:
            with WorkerContext(auth_context=auth_ctx):
                return fn(item)

        return wrapped

    def map_with_progress(
        self,
        fn: Callable[[T], R],
        items: Iterable[T],
        *,
        desc: Optional[str] = None,
        total: Optional[int] = None,
        ordered: bool = True,
        on_error: str = "raise",
    ) -> Iterator[R]:
        """Apply a function to items with a progress bar.

        Parameters:
            fn: Function to apply to each item
            items: Iterable of items to process
            desc: Description for progress bar
            total: Total number of items (for progress bar)
            ordered: If True, maintain input order
            on_error: Error handling strategy

        Returns:
            A lazy iterator of results with progress tracking
        """
        results = self.map(fn, items, ordered=ordered, on_error=on_error)

        try:
            from tqdm.auto import tqdm

            wrapped = tqdm(results, desc=desc, total=total)
        except ImportError:
            wrapped = results

        return iter(wrapped)

    def __enter__(self) -> "StreamingExecutor[T, R]":
        """Enter context manager."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit context manager and cleanup."""
        if self._own_executor and self._executor:
            self._executor.shutdown(wait=True)


# =============================================================================
# Utility Functions
# =============================================================================


def stream_process(
    fn: Callable[[T], R],
    items: Iterable[T],
    *,
    max_workers: int = 4,
    buffer_size: int = 100,
    auth_context: Optional[AuthContext] = None,
    ordered: bool = True,
) -> Iterator[R]:
    """Convenience function for streaming parallel processing.

    Parameters:
        fn: Function to apply to each item
        items: Iterable of items to process
        max_workers: Number of parallel workers
        buffer_size: Size of the result buffer
        auth_context: Optional auth context for workers
        ordered: If True, maintain input order

    Returns:
        A lazy iterator of results

    Example:
        >>> from earthaccess.streaming import stream_process, AuthContext
        >>> ctx = AuthContext.from_auth(earthaccess.login())
        >>> results = stream_process(download_granule, granules, auth_context=ctx)
        >>> for path in results:
        ...     print(f"Downloaded: {path}")
    """
    executor: Any = StreamingExecutor(
        max_workers=max_workers,
        buffer_size=buffer_size,
        auth_context=auth_context,
    )
    return executor.map(fn, items, ordered=ordered)
