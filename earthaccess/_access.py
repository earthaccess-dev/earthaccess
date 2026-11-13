"""Shared access-mode types and normalization."""

from typing import Literal

AccessType = Literal["https", "s3", "direct", "indirect", "external"]
CanonicalAccessType = Literal["https", "s3"]

_ACCESS_ALIASES: dict[str, CanonicalAccessType] = {
    "https": "https",
    "indirect": "https",
    "external": "https",
    "s3": "s3",
    "direct": "s3",
}


def normalize_access(access: AccessType | None) -> CanonicalAccessType:
    """Normalize legacy and current access values to ``"https"`` or ``"s3"``."""
    if access is None:
        return "https"

    try:
        return _ACCESS_ALIASES[access.lower()]
    except (AttributeError, KeyError):
        msg = (
            f"Unknown access {access!r}. "
            f"Expected one of {sorted(_ACCESS_ALIASES)} or None."
        )
        raise ValueError(msg) from None
