"""Search persistence: save, load, and verify reproducible searches.

This module lets you save a ``SearchResults`` (granules or collections) to a
compressed JSON payload, reload it later, and verify that the underlying CMR
search has not changed since it was saved.

Design notes
------------
- **Fingerprint**: a SHA-256 over the *sorted* concept-IDs of the saved results.
  Sorting makes it order-insensitive, because CMR does not guarantee a stable
  result ordering between requests (so ``content-sha1``/``content-md5`` from CMR
  are not usable as fingerprints).
- **Payload**: gzipped JSON containing the replay query parameters, the number
  of loaded results, the CMR hit count at save time, the fingerprint, and the
  loaded result dictionaries.
- **Verification**: ``load()`` re-runs the saved query against CMR by default,
  recomputes the fingerprint over the freshly loaded result set, and returns a
  comparison report (fingerprint and hit-count differences). Pass
  ``verify=False`` to load entirely from disk without a network round-trip.
"""

import gzip
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional, Union

import earthaccess

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from earthaccess.search.results import SearchResults

FORMAT_VERSION = "earthaccess-search-v2"


def compute_fingerprint(results: "SearchResults") -> str:
    """Compute a stable, order-insensitive fingerprint for search results.

    The fingerprint is a SHA-256 of the sorted concept-IDs of the *currently
    materialized* results. Sorting makes it independent of CMR's result
    ordering, so the same result set always hashes to the same value.

    Parameters:
        results: A SearchResults instance (granules or collections).

    Returns:
        A hex SHA-256 digest prefixed with ``sha256:``.
    """
    return compute_fingerprint_items(results._cached_results)


def compute_fingerprint_items(items: List[Any]) -> str:
    """Compute a stable, order-insensitive fingerprint over result objects.

    Parameters:
        items: DataGranule/DataCollection objects (or dicts with a ``meta``
            ``concept-id``).

    Returns:
        A hex SHA-256 digest prefixed with ``sha256:``.
    """
    concept_ids = sorted(item.get("meta", {}).get("concept-id", "") for item in items)
    return _fingerprint_from_ids(concept_ids)


def _fingerprint_from_ids(concept_ids: List[str]) -> str:
    """Compute the SHA-256 fingerprint from a list of concept-IDs."""
    digest = hashlib.sha256("\n".join(sorted(concept_ids)).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _serialize_query_params(results: "SearchResults") -> Dict[str, Any]:
    """Extract replayable query parameters from a SearchResults query object.

    Prefers the original ``query_kwargs`` captured at search time (clean, e.g.
    ``bounding_box`` as ``(w, s, e, n)`` and ``temporal`` as ``(start, end)``).
    Falls back to the legacy ``query.params`` dict, normalizing flattened
    multi-value params (temporal, bounding_box, point, polygon, line,
    cloud_cover, orbit_number) back to the tuple form that ``search_data()`` /
    ``search_datasets()`` accept.

    Parameters:
        results: A SearchResults instance.

    Returns:
        A JSON-serializable dict of kwargs suitable for replaying the search.
    """
    if results._query_kwargs:
        return _json_safe(results._query_kwargs)

    params = dict(getattr(results.query, "params", {}))
    params = _normalize_flattened_params(params)
    return _json_safe(params)


def _json_safe(value: Any) -> Any:
    """Convert datetimes/tuples in a params dict to JSON-safe values."""
    from datetime import date, datetime

    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    return value


def _restore_query_params(params: Any) -> Optional[Dict[str, Any]]:
    """Restore tuple/list-form params after a JSON round-trip.

    Multi-value params are stored as JSON lists. Convert them back to tuples so
    they can be passed to ``search_data(**params)`` / ``search_datasets(**params)``
    (the legacy query builders expand tuples, not lists).
    """
    if not isinstance(params, dict):
        return params

    params = dict(params)

    temporal = params.get("temporal")
    if isinstance(temporal, list) and len(temporal) == 2:
        params["temporal"] = (temporal[0], temporal[1])
    elif isinstance(temporal, list) and len(temporal) > 2:
        params["temporal"] = [
            (pair[0], pair[1]) if isinstance(pair, list) else pair for pair in temporal
        ]

    for key in ("bounding_box", "point", "cloud_cover", "orbit_number"):
        val = params.get(key)
        if isinstance(val, list):
            params[key] = tuple(val)

    return params


def _normalize_flattened_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """Reverse the CMR flattening of multi-value params for replay.

    ``DataGranulesQuery``/``DataCollectionsQuery`` store e.g. ``bounding_box`` as the
    string ``"-46.5,61.0,-42.5,63.0"``, which cannot be passed back to
    ``search_data(bounding_box=...)``. Convert them back to the tuple/list form
    the API accepts.
    """
    params = dict(params)

    def _to_float_pair(s: str):
        parts = s.split(",")
        return (float(parts[0]), float(parts[1]))

    temporal = params.get("temporal")
    if temporal:
        if isinstance(temporal, str):
            temporal = [temporal]
        ranges = []
        for item in temporal:
            if isinstance(item, str) and "," in item:
                start, _, end = item.partition(",")
                ranges.append((start, end))
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                ranges.append((item[0], item[1]))
        if ranges:
            params["temporal"] = (
                ranges if len(ranges) > 1 else (ranges[0][0], ranges[0][1])
            )

    bounding_box = params.get("bounding_box")
    if isinstance(bounding_box, str):
        parts = bounding_box.split(",")
        if len(parts) == 4:
            params["bounding_box"] = tuple(float(p) for p in parts)

    point = params.get("point")
    if isinstance(point, list) and len(point) == 1 and "," in str(point[0]):
        params["point"] = _to_float_pair(str(point[0]))

    for key in ("cloud_cover", "orbit_number"):
        val = params.get(key)
        if isinstance(val, str) and ("," in val or "%2C" in val):
            try:
                low, _, high = val.replace("%2C", ",").partition(",")
                params[key] = (float(low), float(high))
            except ValueError:
                pass

    polygon = params.get("polygon")
    if isinstance(polygon, str):
        coords = polygon.split(",")
        pairs = [
            (float(coords[i]), float(coords[i + 1]))
            for i in range(0, len(coords) - 1, 2)
        ]
        params["polygon"] = pairs

    line = params.get("line")
    if isinstance(line, str):
        coords = line.split(",")
        pairs = [
            (float(coords[i]), float(coords[i + 1]))
            for i in range(0, len(coords) - 1, 2)
        ]
        params["line"] = pairs

    return params


def _serialize_results(results: "SearchResults") -> List[Dict[str, Any]]:
    """Serialize the currently loaded results as plain dictionaries."""
    return [item.to_dict() for item in results._cached_results]


def _iter_for_save(
    results: "SearchResults", limit: int, offset: int = 0
) -> Iterator[Any]:
    """Yield result objects to persist for a given ``limit`` and ``offset``.

    ``limit < 0`` means "save everything the search matches": every result is
    yielded (streamed lazily, no materialization into a list). A positive
    ``limit`` yields up to ``limit`` results starting after ``offset`` results
    are skipped. If the object is already fully materialized the cached list is
    used (no extra network); otherwise the pagination is reset and results are
    streamed fresh.

    Yields:
        DataGranule/DataCollection objects, one at a time.
    """
    if limit < 0:
        source = results._cached_results if results._materialized else results
        if offset > 0:
            for i, item in enumerate(source):
                if i >= offset:
                    yield item
            return
        yield from source
        return

    stop = offset + limit

    if results._materialized:
        yield from results._cached_results[offset:stop]
        return

    # Stream: reset and skip `offset`, then yield up to `limit` fresh.
    saved_limit = results.limit
    results.reset(prefetch=0)
    for i, result in enumerate(results):
        if i < offset:
            continue
        if i >= stop:
            break
        yield result
    # Restore the object to a clean prefetch state.
    results.reset()
    results.limit = saved_limit


def save(
    results: "SearchResults",
    path: Union[str, Path],
    limit: int = 2000,
    offset: int = 0,
) -> Path:
    """Save a SearchResults object to a compressed JSON payload.

    The payload records the replayable query parameters, how many results were
    saved, the CMR hit count at save time, a content fingerprint over the
    sorted concept-IDs, and the results themselves.

    Results are streamed to a **gzipped JSON Lines** payload one per line and
    flushed per page, so a huge search is never materialized in memory and an
    interrupted save keeps every completed page (only the in-flight line is
    lost).

    By default only the **first page** of results (``limit=2000``) is saved and
    a warning is logged, so a huge search never materializes everything just to
    persist it. Pass ``limit=-1`` to save every match, or a specific number.

    Parameters:
        results: A SearchResults instance (granules or collections).
        path: Where to write the payload (``.gz`` recommended).
        limit: How many results to save. ``2000`` (default) saves the first
            page of results. ``-1`` saves every result the search matches.
            A positive value saves up to ``limit`` results, e.g.
            ``save(limit=1000)`` saves the first 1000.
        offset: Number of results to skip before saving (default: 0). Use this
            together with ``limit`` to save a window of a large result set,
            e.g. ``save(limit=1000, offset=2000)`` saves results 2000-2999.

    Returns:
        The path the payload was written to.

    Raises:
        ValueError: If the search returns no results to persist.
    """
    if limit == 0:
        raise ValueError("limit must be -1 (save all) or a positive integer.")
    if offset < 0:
        raise ValueError("offset must be a non-negative integer.")

    if limit > 0:
        logger.warning(
            "save() will persist only %d result(s) starting at offset %d; "
            "pass limit=-1 to save every match of the search.",
            limit,
            offset,
        )

    path = Path(path)
    concept_ids: List[str] = []
    wrote_header = False

    # Progress bar: up to `limit` (or the CMR hit count when saving all).
    total = limit if limit > 0 else results.total()

    from tqdm.auto import tqdm

    with gzip.open(path, "wt", encoding="utf-8") as f:
        with tqdm(
            total=total,
            unit="granules",
            desc="Saving search",
            disable=total in (None, 0),
        ) as pbar:
            for item in _iter_for_save(results, limit, offset=offset):
                d = item.to_dict()
                if not wrote_header:
                    kind = (
                        "granules" if "GranuleUR" in d.get("umm", {}) else "collections"
                    )
                    header = {
                        "format": FORMAT_VERSION,
                        "saved_at": datetime.now(timezone.utc).isoformat(),
                        "kind": kind,
                        "query_params": _serialize_query_params(results),
                        "limit": limit if limit >= 0 else results.limit,
                        "cmr_hits": results.total(),
                    }
                    f.write(json.dumps(header) + "\n")
                    wrote_header = True
                concept_ids.append(d.get("meta", {}).get("concept-id", ""))
                f.write(json.dumps(d) + "\n")
                f.flush()
                pbar.update(1)

        if not wrote_header:
            raise ValueError("The search returned no results to save.")

        trailer = {
            "fingerprint": _fingerprint_from_ids(concept_ids),
            "count": len(concept_ids),
        }
        f.write(json.dumps(trailer) + "\n")
        f.flush()

    return path


def load(
    path: Union[str, Path],
    verify: bool = False,
    *,
    offset: int = 0,
    limit: Optional[int] = 2000,
) -> "SearchResults":
    """Load a saved search from a compressed JSON payload.

    The payload is a gzipped JSON Lines file: a header line, one line per
    result, and a trailing fingerprint line. Loading streams the file line by
    line, so by default only the **first page** of results (``limit=2000``) is
    materialized and a warning is logged. Pass ``limit=None`` (or ``-1``) to
    load every saved result, or use ``offset``/``limit`` to page through a
    large saved set without loading it all at once.

    By default no network request is made. Pass ``verify=True`` to re-run the
    saved query against CMR and compare it with what was saved; if the search
    changed (different fingerprint or hit count), the returned results expose a
    comparison report via ``results.verification``.

    Parameters:
        path: Path to the saved payload.
        verify: If True, re-run the saved query and compare. If False (default),
            load entirely from disk without a network round-trip.
        offset: Number of saved results to skip before loading (default: 0).
            Use this together with ``limit`` to page through a large saved set
            without materializing all of it.
        limit: Maximum number of saved results to load. ``2000`` (default)
            loads the first page. ``None`` or ``-1`` loads every saved result.

    Returns:
        A SearchResults instance populated with the requested slice of results.
        When ``verify=True``, ``results.verification`` holds the comparison
        report; when ``verify=False`` it is ``None``.

    Raises:
        FileNotFoundError: If the payload does not exist.
        ValueError: If the payload format is not recognized.
    """
    path = Path(path)
    # -1 means "load everything", like count=-1 in save().
    load_all = limit is None or limit < 0
    effective_limit = None if load_all else limit

    if not load_all:
        logger.warning(
            "load() will materialize only the first %d results; pass "
            "limit=None to load every saved result, or use offset/limit to "
            "page through the payload.",
            limit,
        )

    payload = _read_payload(path, offset=offset, limit=effective_limit)

    if payload.get("format") != FORMAT_VERSION:
        raise ValueError(
            f"Unrecognized search payload format: {payload.get('format')!r}"
        )

    kind = payload.get("kind", "granules")
    search_limit = payload.get("limit")
    results = _rebuild_from_payload(payload, kind, search_limit, source=str(path))

    # Verification compares the *whole* saved set against the live search. A
    # partial load (offset/limit slice) can't represent the saved set, so
    # verification is skipped for slices.
    is_partial = offset > 0 or not load_all
    if verify and not is_partial:
        results.verification = _verify(payload, kind, search_limit)
    elif verify and is_partial:
        logger.info(
            "load() loaded a slice of the saved search; verification is skipped."
        )
        results.verification = None
    else:
        results.verification = None

    return results


def _read_payload(
    path: Path, offset: int = 0, limit: Optional[int] = None
) -> Dict[str, Any]:
    """Read a gzipped JSON Lines payload, keeping only ``[offset, offset+limit)``.

    Only the requested slice of results is materialized; the file is streamed
    line by line so huge payloads are not loaded into memory.

    Returns:
        A payload dict with ``results`` holding the requested slice.
    """
    header: Optional[Dict[str, Any]] = None
    trailer: Dict[str, Any] = {}
    items: List[Dict[str, Any]] = []
    seen = 0

    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                # Interrupted save: keep the completed lines only.
                logger.warning(
                    "Search payload appears truncated; ignoring the final "
                    "incomplete line."
                )
                break
            if "format" in obj:
                header = obj
            elif "fingerprint" in obj and "count" in obj:
                trailer = obj
            else:
                # A result line.
                if seen < offset:
                    seen += 1
                    continue
                if limit is not None and len(items) >= limit:
                    continue
                items.append(obj)
                seen += 1

    if header is None:
        raise ValueError(f"Unrecognized search payload: no header line in {path}")

    payload = dict(header)
    payload["results"] = items
    payload.update(trailer)
    return payload


def _rebuild_from_payload(
    payload: Dict[str, Any],
    kind: str,
    limit: Optional[int],
    source: Optional[str] = None,
) -> "SearchResults":
    """Reconstruct a SearchResults from saved result dictionaries (offline)."""
    from earthaccess.search.results import (
        CollectionResults,
        DataCollection,
        DataGranule,
        GranuleResults,
        SearchResults,
    )

    if kind == "collections":
        cls = CollectionResults
        items: List[Any] = [DataCollection(item) for item in payload["results"]]
    else:
        cls = GranuleResults
        from earthaccess.search.queries import is_cloud_hosted

        items = [
            DataGranule(item, cloud_hosted=is_cloud_hosted(item))
            for item in payload["results"]
        ]

    results = cls.__new__(cls)  # type: ignore[call-arg]
    SearchResults.__init__(
        results,
        query=None,
        limit=limit,
        prefetch=0,
        query_kwargs=_restore_query_params(payload.get("query_params")),
    )
    results._cached_results = items  # type: ignore[assignment]
    results._exhausted = True
    results._materialized = True
    results._total_hits = payload.get("cmr_hits")
    results._stored_fingerprint = payload.get("fingerprint")
    results._saved_at = payload.get("saved_at")
    results._source = source
    return results


def _verify(payload: Dict[str, Any], kind: str, limit: Optional[int]) -> Dict[str, Any]:
    """Re-run the saved query against CMR and compare with what was saved.

    Returns:
        A comparison report dict with:
        - ``unchanged``: True if fingerprint and hit count both match
        - ``fingerprint_match``: bool
        - ``cmr_hits_match``: bool
        - ``saved_fingerprint`` / ``current_fingerprint``
        - ``saved_cmr_hits`` / ``current_cmr_hits``
        - ``added``: concept-IDs present now but not at save time
        - ``removed``: concept-IDs saved but no longer present
    """
    query_params = _restore_query_params(payload.get("query_params", {})) or {}
    saved_fingerprint = payload.get("fingerprint")
    saved_cmr_hits = payload.get("cmr_hits")
    saved_ids = {r.get("meta", {}).get("concept-id") for r in payload["results"]}

    if kind == "collections":
        fresh = earthaccess.search_datasets(**query_params, count=limit or -1)
    else:
        fresh = earthaccess.search_data(**query_params, count=limit or -1)
    fresh.all()  # materialize fully for a like-for-like comparison
    current_ids = {
        item.get("meta", {}).get("concept-id") for item in fresh._cached_results
    }

    current_fingerprint = compute_fingerprint(fresh)
    current_cmr_hits = fresh.total()

    fingerprint_match = current_fingerprint == saved_fingerprint
    cmr_hits_match = current_cmr_hits == saved_cmr_hits

    return {
        "unchanged": fingerprint_match and cmr_hits_match,
        "fingerprint_match": fingerprint_match,
        "cmr_hits_match": cmr_hits_match,
        "saved_fingerprint": saved_fingerprint,
        "current_fingerprint": current_fingerprint,
        "saved_cmr_hits": saved_cmr_hits,
        "current_cmr_hits": current_cmr_hits,
        "added": sorted(current_ids - saved_ids),
        "removed": sorted(saved_ids - current_ids),
    }


def save_search(
    results: "SearchResults",
    path: Union[str, Path],
    limit: int = 2000,
    offset: int = 0,
) -> Path:
    """Save a SearchResults object to a compressed JSON payload.

    Convenience wrapper around :func:`save`, mirroring the
    ``results.save(path, limit=..., offset=...)`` method.

    Parameters:
        results: A SearchResults instance (granules or collections).
        path: Where to write the payload (``.gz`` recommended).
        limit: How many results to save. ``2000`` (default) saves the first
            page of results; ``-1`` saves every match; a positive value saves
            up to ``limit``.
        offset: Number of results to skip before saving (default: 0).

    Returns:
        The path the payload was written to.
    """
    return save(results, path, limit=limit, offset=offset)


def load_search(
    path: Union[str, Path],
    verify: bool = False,
    *,
    offset: int = 0,
    limit: Optional[int] = 2000,
) -> "SearchResults":
    """Load a saved search from a compressed JSON payload.

    Convenience wrapper around :func:`load`, mirroring the
    ``SearchResults.load(path, offset=..., limit=...)`` class method. By default
    only the **first page** (``limit=2000``) is materialized with a warning;
    pass ``limit=None`` (or ``-1``) to load every saved result.

    Parameters:
        path: Path to the saved payload.
        verify: If True, re-run the saved query against CMR and compare. If
            False (default), load offline.
        offset: Number of saved results to skip before loading (default: 0).
        limit: Maximum number of saved results to load. ``2000`` (default)
            loads the first page; ``None`` or ``-1`` loads everything.

    Returns:
        A SearchResults instance populated with the requested slice of results.
    """
    return load(path, verify=verify, offset=offset, limit=limit)
