"""S3 icechunk stores default to the NASA region (us-west-2)."""

from __future__ import annotations

from typing import Any

import icechunk
import pytest
from earthaccess.virtual.core import _icechunk_storage


def _capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def _s3_storage(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(icechunk, "s3_storage", _s3_storage)
    return captured


def test_s3_store_defaults_to_us_west_2(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch)

    _icechunk_storage("s3://bucket/prefix")

    assert captured["bucket"] == "bucket"
    assert captured["prefix"] == "prefix"
    assert captured["region"] == "us-west-2"


def test_s3_store_region_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch)

    _icechunk_storage("s3://bucket/prefix", {"region": "eu-west-1"})

    assert captured["region"] == "eu-west-1"
