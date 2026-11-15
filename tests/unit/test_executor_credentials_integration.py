"""Integration tests for credential distribution with executors.

Tests the execute_with_credentials function and demonstrates patterns
for using credentials with parallel executors in real-world scenarios.
"""

import datetime
from typing import Any

from earthaccess.auth.credentials import (
    AuthContext,
    HTTPHeaders,
    S3Credentials,
)
from earthaccess.store.parallel import (
    SerialExecutor,
    ThreadPoolExecutorWrapper,
    execute_with_credentials,
    get_executor,
)


class TestExecuteWithCredentialsFunction:
    """Test the execute_with_credentials helper function."""

    def test_execute_with_credentials_serial_executor(self) -> None:
        """Test execute_with_credentials with serial executor."""
        auth_context = AuthContext(
            s3_credentials=S3Credentials(
                access_key="test_key",
                secret_key="test_secret",
            ),
        )

        def process_item(item: int, context: AuthContext) -> int:
            """Process item with credentials."""
            return item * 2 if context.is_valid() else 0

        executor = SerialExecutor()
        items = [1, 2, 3, 4, 5]

        results = execute_with_credentials(executor, process_item, items, auth_context)

        assert results == [2, 4, 6, 8, 10]

    def test_execute_with_credentials_thread_pool_executor(self) -> None:
        """Test execute_with_credentials with thread pool executor."""
        auth_context = AuthContext(
            s3_credentials=S3Credentials(
                access_key="test_key",
                secret_key="test_secret",
            ),
        )

        def process_item(item: int, context: AuthContext) -> int:
            """Process item with credentials."""
            return item * 3 if context.is_valid() else 0

        executor = ThreadPoolExecutorWrapper(max_workers=2, show_progress=False)
        items = [1, 2, 3, 4]

        results = execute_with_credentials(executor, process_item, items, auth_context)
        executor.shutdown()

        assert sorted(results) == [3, 6, 9, 12]

    def test_execute_with_credentials_with_get_executor(self) -> None:
        """Test execute_with_credentials with get_executor factory."""
        auth_context = AuthContext(
            s3_credentials=S3Credentials(
                access_key="test_key",
                secret_key="test_secret",
            ),
        )

        def process_item(item: str, context: AuthContext) -> int:
            """Process item - count length if credentials valid."""
            return len(item) if context.is_valid() else 0

        executor = get_executor("serial")
        items = ["hello", "world", "test"]

        results = execute_with_credentials(executor, process_item, items, auth_context)

        assert results == [5, 5, 4]

    def test_execute_with_credentials_expired_credentials(self) -> None:
        """Test execute_with_credentials with expired credentials."""
        past_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            hours=1
        )
        auth_context = AuthContext(
            s3_credentials=S3Credentials(
                access_key="test_key",
                secret_key="test_secret",
                expiration_time=past_time,
            ),
        )

        def process_item(item: int, context: AuthContext) -> int:
            """Process item - return 0 if credentials expired."""
            return item if context.is_valid() else 0

        executor = SerialExecutor()
        items = [1, 2, 3]

        results = execute_with_credentials(executor, process_item, items, auth_context)

        # All should be 0 because credentials are expired
        assert results == [0, 0, 0]

    def test_execute_with_credentials_accesses_s3_credentials(self) -> None:
        """Test that execute_with_credentials provides S3 credentials to workers."""
        auth_context = AuthContext(
            s3_credentials=S3Credentials(
                access_key="AKIAIOSFODNN7EXAMPLE",
                secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                session_token="token_value",
                region="us-east-1",
            ),
        )

        def extract_access_key(item: int, context: AuthContext) -> str:
            """Extract access key from credentials."""
            if context.s3_credentials:
                return context.s3_credentials.access_key
            return ""

        executor = SerialExecutor()
        items = [1, 2, 3]

        results = execute_with_credentials(
            executor, extract_access_key, items, auth_context
        )

        assert all(result == "AKIAIOSFODNN7EXAMPLE" for result in results)

    def test_execute_with_credentials_accesses_http_headers(self) -> None:
        """Test that execute_with_credentials provides HTTP headers to workers."""
        auth_context = AuthContext(
            http_headers=HTTPHeaders(
                headers={"Authorization": "Bearer token123"},
                cookies={"session_id": "abc123"},
            ),
        )

        def extract_auth_header(item: int, context: AuthContext) -> str:
            """Extract Authorization header from credentials."""
            if context.http_headers:
                return context.http_headers.headers.get("Authorization", "")
            return ""

        executor = SerialExecutor()
        items = [1, 2]

        results = execute_with_credentials(
            executor, extract_auth_header, items, auth_context
        )

        assert all(result == "Bearer token123" for result in results)


class TestGranuleDownloadPattern:
    """Test credential distribution pattern for granule downloads."""

    def test_granule_download_pattern_with_credentials(self) -> None:
        """Test typical granule download pattern with credentials."""

        # Create mock granule class
        class MockGranule:
            def __init__(self, granule_id: str) -> None:
                self.granule_id = granule_id

            def download(self, auth_context: AuthContext, path: str) -> dict:
                """Mock download that uses credentials."""
                if not auth_context.is_valid():
                    raise ValueError("Credentials expired")
                return {
                    "granule_id": self.granule_id,
                    "path": path,
                    "has_s3_creds": auth_context.s3_credentials is not None,
                }

        # Create auth context
        auth_context = AuthContext(
            s3_credentials=S3Credentials(
                access_key="test_key",
                secret_key="test_secret",
            ),
        )

        # Create mock granules
        granules = [MockGranule(f"G{i}") for i in range(3)]

        # Define download operation
        def download_granule(granule: Any, context: AuthContext) -> dict:
            return granule.download(context, "/data")

        # Execute with serial executor
        executor = SerialExecutor()
        results = execute_with_credentials(
            executor, download_granule, granules, auth_context
        )

        # Verify all downloads succeeded
        assert len(results) == 3
        assert all("granule_id" in r for r in results)
        assert all(r["has_s3_creds"] is True for r in results)

    def test_granule_filter_and_download_pattern(self) -> None:
        """Test filtering granules before parallel download."""

        # Create mock granule class with role
        class MockGranule:
            def __init__(self, granule_id: str, size_gb: float) -> None:
                self.granule_id = granule_id
                self.size_gb = size_gb

            def download(self, auth_context: AuthContext) -> float:
                """Mock download returns size downloaded."""
                if not auth_context.is_valid():
                    raise ValueError("Credentials expired")
                return self.size_gb if auth_context.s3_credentials else 0

        # Create granules with different sizes
        all_granules = [
            MockGranule("G1", 2.5),
            MockGranule("G2", 0.5),
            MockGranule("G3", 3.0),
            MockGranule("G4", 0.1),
            MockGranule("G5", 2.0),
        ]

        # Filter to granules larger than 1GB
        large_granules = [g for g in all_granules if g.size_gb > 1.0]
        assert len(large_granules) == 3

        # Create auth context
        auth_context = AuthContext(
            s3_credentials=S3Credentials(
                access_key="test_key",
                secret_key="test_secret",
            ),
        )

        # Download large granules in parallel
        def download_granule(granule: Any, context: AuthContext) -> float:
            return granule.download(context)

        executor = SerialExecutor()
        results = execute_with_credentials(
            executor, download_granule, large_granules, auth_context
        )

        # Verify results
        assert len(results) == 3
        assert sum(results) == 7.5  # 2.5 + 3.0 + 2.0


class TestCredentialDistributionScenarios:
    """Test credential distribution in various scenarios."""

    def test_batch_processing_with_credentials(self) -> None:
        """Test batch processing of items with credentials."""
        auth_context = AuthContext(
            s3_credentials=S3Credentials(
                access_key="test_key",
                secret_key="test_secret",
            ),
            http_headers=HTTPHeaders(
                headers={"Authorization": "Bearer token"},
            ),
        )

        def process_batch(items: list, context: AuthContext) -> dict:
            """Process batch using both S3 and HTTP credentials."""
            result = {
                "items_count": len(items),
                "has_s3": context.s3_credentials is not None,
                "has_http": context.http_headers is not None,
            }
            return result

        # Create batches
        batches = [[1, 2, 3], [4, 5], [6, 7, 8, 9]]

        executor = SerialExecutor()
        results = execute_with_credentials(
            executor, process_batch, batches, auth_context
        )

        # Verify all batches processed with credentials
        assert len(results) == 3
        assert all(r["has_s3"] and r["has_http"] for r in results)

    def test_multiple_executor_backends_same_credentials(self) -> None:
        """Test same credential distribution works with different executors."""
        auth_context = AuthContext(
            s3_credentials=S3Credentials(
                access_key="test_key",
                secret_key="test_secret",
            ),
        )

        def process_item(item: int, context: AuthContext) -> int:
            return item * 2 if context.is_valid() else 0

        items = [1, 2, 3]

        # Test with serial executor
        executor_serial = SerialExecutor()
        results_serial = execute_with_credentials(
            executor_serial, process_item, items, auth_context
        )

        # Test with thread pool executor
        executor_threads = get_executor("threads", max_workers=2, show_progress=False)
        results_threads = execute_with_credentials(
            executor_threads, process_item, items, auth_context
        )
        executor_threads.shutdown()

        # Results should be the same regardless of executor
        assert sorted(results_serial) == sorted(results_threads) == [2, 4, 6]

    def test_credential_context_reconstruction_in_worker(self) -> None:
        """Test that worker can reconstruct Auth from AuthContext."""
        auth_context = AuthContext(
            s3_credentials=S3Credentials(
                access_key="test_key",
                secret_key="test_secret",
            ),
            urs_token="urs_token_value",
        )

        def reconstruct_and_use_auth(item: int, context: AuthContext) -> dict:
            """Reconstruct Auth and use it."""
            # Verify we can reconstruct Auth from context
            auth = context.to_auth()
            return {
                "item": item,
                "auth_reconstructed": auth is not None,
                "context_valid": context.is_valid(),
            }

        executor = SerialExecutor()
        items = [1, 2, 3]

        results = execute_with_credentials(
            executor, reconstruct_and_use_auth, items, auth_context
        )

        # Verify Auth was reconstructed in each worker
        assert len(results) == 3
        assert all(r["auth_reconstructed"] for r in results)
        assert all(r["context_valid"] for r in results)


class TestErrorHandlingWithCredentials:
    """Test error handling with credential distribution."""

    def test_expired_credentials_caught_in_worker(self) -> None:
        """Test that worker can catch and handle expired credentials."""
        past_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            hours=1
        )
        auth_context = AuthContext(
            s3_credentials=S3Credentials(
                access_key="test_key",
                secret_key="test_secret",
                expiration_time=past_time,
            ),
        )

        def safe_process(item: int, context: AuthContext) -> dict:
            """Process with error handling for expired credentials."""
            return {
                "item": item,
                "valid": context.is_valid(),
                "error": None if context.is_valid() else "Credentials expired",
            }

        executor = SerialExecutor()
        items = [1, 2, 3]

        results = execute_with_credentials(executor, safe_process, items, auth_context)

        # Verify all items processed with error message
        assert len(results) == 3
        assert all(not r["valid"] for r in results)
        assert all(r["error"] == "Credentials expired" for r in results)

    def test_missing_credentials_handled_gracefully(self) -> None:
        """Test handling when specific credentials are missing."""
        auth_context = AuthContext()  # No credentials

        def try_use_s3_credentials(item: int, context: AuthContext) -> dict:
            """Try to use S3 credentials, handle missing."""
            return {
                "item": item,
                "has_s3": context.s3_credentials is not None,
                "status": "ok" if context.s3_credentials else "no_s3_credentials",
            }

        executor = SerialExecutor()
        items = [1, 2]

        results = execute_with_credentials(
            executor, try_use_s3_credentials, items, auth_context
        )

        # Verify graceful handling of missing credentials
        assert len(results) == 2
        assert all(r["has_s3"] is False for r in results)
        assert all(r["status"] == "no_s3_credentials" for r in results)


class TestCredentialPerformance:
    """Test credential distribution performance characteristics."""

    def test_large_batch_with_credentials(self) -> None:
        """Test execute_with_credentials with large batch of items."""
        auth_context = AuthContext(
            s3_credentials=S3Credentials(
                access_key="test_key",
                secret_key="test_secret",
            ),
        )

        def simple_operation(item: int, context: AuthContext) -> int:
            """Simple operation with credentials."""
            return item + 1 if context.is_valid() else 0

        executor = SerialExecutor()
        items = list(range(100))

        results = execute_with_credentials(
            executor, simple_operation, items, auth_context
        )

        # Verify all items processed
        assert len(results) == 100
        assert results == [i + 1 for i in range(100)]

    def test_nested_credential_access(self) -> None:
        """Test accessing nested credential details in worker."""
        auth_context = AuthContext(
            s3_credentials=S3Credentials(
                access_key="test_key",
                secret_key="test_secret",
                session_token="temp_token",
                region="us-west-2",
            ),
            http_headers=HTTPHeaders(
                headers={
                    "Authorization": "Bearer token123",
                    "User-Agent": "earthaccess/1.0",
                },
                cookies={"session_id": "abc123"},
            ),
            urs_token="urs_token_value",
        )

        def complex_operation(item: int, context: AuthContext) -> dict:
            """Complex operation accessing all credential types."""
            cred_dict: dict[str, Any] = {}
            if context.s3_credentials:
                cred_dict["s3"] = {
                    "has_session_token": context.s3_credentials.session_token
                    is not None,
                    "region": context.s3_credentials.region,
                }
            if context.http_headers:
                cred_dict["http"] = {
                    "auth_header": "Authorization" in context.http_headers.headers,
                    "has_cookies": len(context.http_headers.cookies) > 0,
                }
            cred_dict["has_urs_token"] = context.urs_token is not None
            return cred_dict

        executor = SerialExecutor()
        items = [1, 2]

        results = execute_with_credentials(
            executor, complex_operation, items, auth_context
        )

        # Verify all credential types accessible
        assert len(results) == 2
        for result in results:
            assert "s3" in result
            assert result["s3"]["has_session_token"] is True
            assert result["s3"]["region"] == "us-west-2"
            assert "http" in result
            assert result["http"]["auth_header"] is True
            assert result["http"]["has_cookies"] is True
            assert result["has_urs_token"] is True
