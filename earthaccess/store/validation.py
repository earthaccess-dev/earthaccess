"""Validation helpers for requesting granule and URL access.

These checks run before any credentials are fetched or files are opened so a
request either succeeds completely or fails with an actionable error.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from earthaccess._access import CanonicalAccessType

_S3_ACCESS_HINT = (
    "Direct S3 access only works from AWS us-west-2; use access='https' elsewhere."
)


def _concept_id(granule: object) -> str:
    try:
        return granule["meta"].get("concept-id", "<unknown>")  # type: ignore[index]
    except (KeyError, TypeError, AttributeError):
        return "<unknown>"


def raise_for_missing_links(
    granules: Sequence[object],
    access: CanonicalAccessType,
) -> None:
    """Raise when any granule has no data links for the requested access mode."""
    missing: list[str] = []
    missing_https_with_s3: list[str] = []
    for granule in granules:
        if granule.data_links(access=access):  # type: ignore[attr-defined]
            continue
        concept_id = _concept_id(granule)
        if (
            access == "https" and granule.data_links(access="s3")  # type: ignore[attr-defined]
        ):
            missing_https_with_s3.append(concept_id)
        else:
            missing.append(concept_id)

    if missing_https_with_s3:
        msg = " ".join(
            f"Granule {concept_id} has no HTTPS data links; it offers S3 links, "
            "retry with access='s3'."
            for concept_id in missing_https_with_s3
        )
        raise ValueError(msg)

    if missing:
        msg = " ".join(
            f"Granule {concept_id} has no {access.upper()} data links."
            for concept_id in missing
        )
        raise ValueError(msg)


def raise_for_mixed_providers(granules: Sequence[object]) -> None:
    """Raise when an S3 request spans more than one CMR provider.

    S3 credentials are scoped to a provider/DAAC. Using the first granule's
    provider for a mixed batch would silently authenticate only part of the
    request, so reject it up front.
    """
    providers: set[str] = set()
    for granule in granules:
        try:
            provider = granule["meta"].get("provider-id")  # type: ignore[index]
        except (KeyError, TypeError, AttributeError):
            continue
        if provider:
            providers.add(provider)

    if len(providers) > 1:
        msg = (
            "Cannot use access='s3' with granules from multiple providers: "
            f"{', '.join(sorted(providers))}. Split the request by provider."
        )
        raise ValueError(msg)


def validate_granule_access(
    granules: Sequence[object],
    access: CanonicalAccessType,
) -> None:
    """Validate a granule batch for the requested access mode."""
    raise_for_missing_links(granules, access)
    if access == "s3":
        raise_for_mixed_providers(granules)


def _url_protocol(url: str) -> str:
    if url.startswith("s3://"):
        return "s3"
    if url.startswith(("http://", "https://")):
        return "https"
    msg = f"Unsupported URL scheme for {url!r}; expected s3:// or http(s)://."
    raise ValueError(msg)


def validate_url_protocols(urls: Iterable[str]) -> str:
    """Return the single protocol used by *urls*, rejecting mixed lists."""
    protocols = {_url_protocol(url) for url in urls}
    if not protocols:
        msg = "No URLs were provided."
        raise ValueError(msg)
    if len(protocols) > 1:
        msg = (
            "Cannot mix S3 and HTTPS URLs in one request; "
            "split the request by protocol."
        )
        raise ValueError(msg)
    return protocols.pop()
