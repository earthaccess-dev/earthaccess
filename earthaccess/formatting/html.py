"""Static HTML formatters for Jupyter notebook display.

These formatters generate static HTML representations for earthaccess objects.
They work without any optional dependencies and provide a baseline visualization.
"""

from typing import TYPE_CHECKING, Any, List, Optional
from uuid import uuid4

import importlib_resources

STATIC_FILES = ["styles.css"]

CMR_CONCEPT_BASE = "https://cmr.earthdata.nasa.gov/search/concepts"


def _concept_link(concept_id: str) -> str:
    """Render a CMR concept-id as a link to its UMM metadata record."""
    if not concept_id:
        return "N/A"
    return (
        f'<a href="{CMR_CONCEPT_BASE}/{concept_id}.umm_json" '
        f'target="_blank">{concept_id}</a>'
    )


def _load_css_files() -> List[str]:
    """Load CSS styles for HTML formatting.

    Returns:
        List of CSS file contents as strings.
    """
    return [
        importlib_resources.files("earthaccess.formatting.css")
        .joinpath(fname)
        .read_text("utf8")
        for fname in STATIC_FILES
    ]


if TYPE_CHECKING:
    from earthaccess.auth.auth import Auth
    from earthaccess.search.results import DataCollection, DataGranule, SearchResults


def _repr_auth_html(auth: "Auth") -> str:
    """Generate HTML representation for Auth status.

    Parameters:
        auth: An Auth instance

    Returns:
        HTML string for notebook display
    """
    css_styles = _load_css_files()
    css_inline = f"""<div id="{uuid4()}" style="height: 0px; display: none">
            {"".join([f"<style>{style}</style>" for style in css_styles])}
            </div>"""

    if not auth.authenticated:
        return f"""
        {css_inline}
        <div class="bootstrap ea-container">
          <div class="container-fluid border" style="padding: 15px; max-width: 500px;">
            <div class="row">
              <div class="col-12">
                <h5 style="margin-bottom: 10px; color: var(--ea-text-secondary);">
                  <span style="font-size: 1.2em;">🔒</span> Earthdata Login
                </h5>
                <div style="background: var(--ea-warning-bg); padding: 10px; border-radius: 5px; border-left: 4px solid var(--ea-warning);">
                  <p style="margin: 0; color: var(--ea-warning-text);">
                    <b>Not Authenticated</b><br>
                    <span style="font-size: 0.9em;">Use <code>earthaccess.login()</code> to authenticate.</span>
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>
        """

    # Get user info
    username = getattr(auth, "username", None)
    strategy = auth._login_strategy or "unknown"
    system_name = "Production" if "urs.earthdata" in auth.system.edl_hostname else "UAT"
    edl_host = auth.system.edl_hostname

    # Get token info
    token = auth.token or {}
    access_token = token.get("access_token", "")
    expiration = token.get("expiration_date", "")

    # Redact token (show last 4 chars)
    if access_token:
        token_display = (
            f"{'•' * 20}...{access_token[-4:]}"
            if len(access_token) > 4
            else "•" * len(access_token)
        )
    else:
        token_display = "N/A"

    # Format expiration date
    if expiration:
        # Parse and format nicely
        try:
            from datetime import datetime

            exp_dt = datetime.fromisoformat(expiration.replace("Z", "+00:00"))
            exp_display = exp_dt.strftime("%Y-%m-%d %H:%M UTC")
            # Check if expired
            now = datetime.now(exp_dt.tzinfo)
            if exp_dt < now:
                exp_status = '<span style="color: var(--ea-danger);">Expired</span>'
            else:
                days_left = (exp_dt - now).days
                if days_left > 7:
                    exp_status = f'<span style="color: var(--ea-success);">Valid ({days_left} days)</span>'
                else:
                    exp_status = f'<span style="color: var(--ea-warning);">Expires soon ({days_left} days)</span>'
        except Exception:
            exp_display = expiration
            exp_status = ""
    else:
        exp_display = "N/A (token-based auth)"
        exp_status = ""

    # Strategy icons
    strategy_icons = {
        "netrc": "📁",
        "interactive": "⌨️",
        "environment": "🌍",
    }
    strategy_icon = strategy_icons.get(strategy, "🔑")
    strategy_display = strategy.capitalize()

    # User display
    if username:
        user_display = f"<code>{username}</code>"
    else:
        user_display = '<span style="color: var(--ea-text-secondary); font-style: italic;">Token-based (no username)</span>'

    return f"""
    {css_inline}
    <div class="bootstrap ea-container">
      <div class="container-fluid border" style="padding: 15px; max-width: 550px;">
        <div class="row">
          <div class="col-12">
            <h5 style="margin-bottom: 10px;">
              <span style="font-size: 1.2em;">🌍</span> Earthdata Login
              <span class="badge" style="background: var(--ea-success); color: white; font-size: 0.7em; padding: 3px 8px; border-radius: 10px; vertical-align: middle;">Authenticated</span>
            </h5>
          </div>
        </div>
        <hr style="margin: 8px 0;">
        <div class="row">
          <div class="col-6">
            <p style="margin: 4px 0;"><b>User:</b> {user_display}</p>
            <p style="margin: 4px 0;"><b>Strategy:</b> {strategy_icon} {strategy_display}</p>
          </div>
          <div class="col-6">
            <p style="margin: 4px 0;"><b>System:</b> {system_name}</p>
            <p style="margin: 4px 0;"><b>Host:</b> <code style="font-size: 0.85em;">{edl_host}</code></p>
          </div>
        </div>
        <hr style="margin: 8px 0;">
        <div class="row">
          <div class="col-12">
            <details>
              <summary style="cursor: pointer; padding: 5px; background: var(--ea-bg-secondary); border-radius: 3px;">
                <b>Token Details</b>
              </summary>
              <div style="margin-top: 8px; padding: 10px; background: var(--ea-bg-secondary); border-radius: 3px; font-size: 0.9em;">
                <p style="margin: 3px 0;"><b>Token:</b> <code>{token_display}</code></p>
                <p style="margin: 3px 0;"><b>Expires:</b> {exp_display} {exp_status}</p>
              </div>
            </details>
          </div>
        </div>
      </div>
    </div>
    """


def _repr_granule_html(granule: "DataGranule") -> str:
    """Generate HTML representation for a single granule.

    Parameters:
        granule: A DataGranule instance

    Returns:
        HTML string for notebook display
    """
    css_styles = _load_css_files()
    css_inline = f"""<div id="{uuid4()}" style="height: 0px; display: none">
            {"".join([f"<style>{style}</style>" for style in css_styles])}
            </div>"""
    style = "max-height: 120px;"
    dataviz_img = "".join(
        [
            f'<a href="{link}"><img style="{style}" src="{link}" alt="Data Preview"/></a>'
            for link in granule.dataviz_links[:2]
            if link.startswith("http")
        ]
    )
    data_links = "".join(
        [
            f'<a href="{link}" target="_blank" class="btn btn-secondary btn-sm">{link.split("/")[-1]}</a>'
            for link in granule.data_links()
            if link.startswith("http")
        ]
    )
    granule_size = round(granule.size, 2)

    # Extract temporal info
    temporal = granule.get("umm", {}).get("TemporalExtent", {})
    temporal_str = _format_temporal_extent(temporal)

    # Get granule ID
    granule_ur = granule.get("umm", {}).get("GranuleUR", "Unknown")
    concept_id = granule.get("meta", {}).get("concept-id", "")
    concept_html = _concept_link(concept_id)

    return f"""
    {css_inline}
    <div class="bootstrap ea-container">
      <div class="container-fluid border" style="padding: 10px;">
        <div class="row">
          <div class="col-8">
            <p style="margin: 2px 0;"><b>Granule</b>: <code>{granule_ur}</code></p>
            <p style="margin: 2px 0;"><b>Concept ID</b>: <code>{concept_html}</code></p>
            <p style="margin: 2px 0;"><b>Temporal</b>: {temporal_str}</p>
            <p style="margin: 2px 0;"><b>Size</b>: {granule_size} MB</p>
            <p style="margin: 2px 0;"><b>Data</b>: {data_links if data_links else "No direct links"}</p>
          </div>
          <div class="col-4 text-right">
            {dataviz_img}
          </div>
        </div>
      </div>
    </div>
    """


def _repr_collection_html(collection: "DataCollection") -> str:
    """Generate HTML representation for a single collection.

    Parameters:
        collection: A DataCollection instance

    Returns:
        HTML string for notebook display
    """
    css_styles = _load_css_files()
    css_inline = f"""<div id="{uuid4()}" style="height: 0px; display: none">
            {"".join([f"<style>{style}</style>" for style in css_styles])}
            </div>"""

    # Extract metadata
    short_name = collection.get_umm("ShortName") or "Unknown"
    version = collection.version or ""
    title = collection.get_umm("EntryTitle") or short_name
    abstract = collection.abstract or "No description available"

    # Truncate abstract if too long
    if len(abstract) > 300:
        abstract = abstract[:297] + "..."

    # Get DOI
    doi = collection.doi
    doi_html = (
        f'<a href="https://doi.org/{doi}" target="_blank">{doi}</a>' if doi else "N/A"
    )

    # Get provider
    provider = collection.get("meta", {}).get("provider-id", "Unknown")

    # Temporal extent
    temporal_extents = collection.get_umm("TemporalExtents")
    temporal_str = _format_collection_temporal(temporal_extents)

    # Links
    landing_page = collection.landing_page
    landing_html = (
        f'<a href="{landing_page}" target="_blank" class="btn btn-primary btn-sm">Landing Page</a>'
        if landing_page
        else ""
    )

    get_data_links = collection.data_links[:2]  # First 2 data links
    get_data_html = "".join(
        [
            f'<a href="{link}" target="_blank" class="btn btn-secondary btn-sm">Get Data</a>'
            for link in get_data_links
            if link.startswith("http")
        ]
    )

    concept_id = collection.concept_id
    concept_html = _concept_link(concept_id)

    return f"""
    {css_inline}
    <div class="bootstrap ea-container">
      <div class="container-fluid border" style="padding: 10px;">
        <div class="row">
          <div class="col-12">
            <h5 style="margin-bottom: 5px;">{short_name} {f"v{version}" if version else ""}</h5>
            <p style="margin: 2px 0; color: var(--ea-text-secondary); font-style: italic;">{title}</p>
          </div>
        </div>
        <hr style="margin: 8px 0;">
        <div class="row">
          <div class="col-12">
            <p style="margin: 5px 0; font-size: 0.9em;">{abstract}</p>
          </div>
        </div>
        <hr style="margin: 8px 0;">
        <div class="row">
          <div class="col-6">
            <p style="margin: 2px 0;"><b>Provider</b>: {provider}</p>
            <p style="margin: 2px 0;"><b>Concept ID</b>: <code>{concept_html}</code></p>
            <p style="margin: 2px 0;"><b>Temporal</b>: {temporal_str}</p>
          </div>
          <div class="col-6">
            <p style="margin: 2px 0;"><b>DOI</b>: {doi_html}</p>
          </div>
        </div>
        <div class="row" style="margin-top: 10px;">
          <div class="col-12">
            {landing_html} {get_data_html}
          </div>
        </div>
      </div>
    </div>
    """


def _repr_search_results_html(
    results: "SearchResults",
    page_size: int = 20,
) -> str:
    """Generate HTML representation for SearchResults with interactive pagination.

    Parameters:
        results: A SearchResults instance
        page_size: Number of items per page (default 20)

    Returns:
        HTML string for notebook display with JavaScript pagination
    """
    css_styles = _load_css_files()
    widget_id = str(uuid4()).replace("-", "")[:12]
    css_inline = f"""<div id="css-{widget_id}" style="height: 0px; display: none">
            {"".join([f"<style>{style}</style>" for style in css_styles])}
            </div>"""

    # Auto-fetch first page if no results are cached
    if len(results._cached_results) == 0 and not results._exhausted:
        # Fetch first page by iterating until we have some results or hit limit
        results._ensure_cached(min(page_size, results.limit or page_size))

    # Get counts - call total() to ensure we have the hit count
    total_hits = results._total_hits
    if total_hits is None:
        total_hits = results.total()
    hits_str = f"{total_hits:,}" if total_hits is not None else "?"
    cached_count = len(results._cached_results)

    # Calculate total pages based on cached results
    total_pages = (
        max(1, (cached_count + page_size - 1) // page_size) if cached_count > 0 else 1
    )

    # Determine if more results are available to load
    more_available = (
        total_hits is not None and cached_count < total_hits and not results._exhausted
    )

    # Determine result type from class name or content
    result_class = results.__class__.__name__
    if "Granule" in result_class:
        result_type = "granules"
        result_title = "GranuleResults"
        is_collection_results = False
    elif "Collection" in result_class:
        result_type = "collections"
        result_title = "CollectionResults"
        is_collection_results = True
    else:
        is_collection_results = cached_count > 0 and not _is_granule(
            results._cached_results[0]
        )
        result_type = "collections" if is_collection_results else "granules"
        result_title = "SearchResults"

    # Compute summary if we have cached results and total < 10k
    summary_html = ""
    if cached_count > 0 and (total_hits is None or total_hits < 10000):
        summary = _compute_summary(results._cached_results)
        cloud_str = (
            f"☁️ {summary['cloud_count']} cloud-hosted | "
            if summary["cloud_count"] > 0
            else ""
        )
        if not is_collection_results:
            # Only show size summary for granules
            summary_html = f"""
            <div class="row" style="margin-top: 5px;">
              <div class="col-12" style="font-size: 0.85em; color: var(--ea-text-secondary);">
                <b>Summary</b>: {summary["total_size_mb"]:.1f} MB total | {cloud_str}{summary["temporal_range"] or ""}
              </div>
            </div>
            """
        else:
            # For collections, just show cloud count and temporal range
            summary_html = f"""
            <div class="row" style="margin-top: 5px;">
              <div class="col-12" style="font-size: 0.85em; color: var(--ea-text-secondary);">
                <b>Summary</b>: {cloud_str}{summary["temporal_range"] or ""}
              </div>
            </div>
            """

    # Generate all table rows (we'll paginate client-side with JS)
    all_rows = []
    for idx, item in enumerate(results._cached_results):
        if _is_granule(item):
            row = _granule_row_with_index(item, idx, widget_id)
        else:
            row = _collection_row_with_index(item, idx, widget_id)
        all_rows.append(row)

    all_rows_html = (
        "\n".join(all_rows)
        if all_rows
        else f"<tr data-idx='0'><td colspan='{6 if is_collection_results else 6}' style='text-align: center;'>No results found.</td></tr>"
    )

    # Different table headers for granules vs collections
    if is_collection_results:
        table_header = """
                <tr>
                  <th style="width: 3%;"></th>
                  <th style="width: 30%;">Short Name</th>
                  <th style="width: 15%;">Format</th>
                  <th style="width: 15%;">Granules</th>
                  <th style="width: 10%;">Cloud</th>
                  <th style="width: 10%;">Links</th>
                </tr>
        """
    else:
        table_header = """
                <tr>
                  <th style="width: 3%;"></th>
                  <th style="width: 37%;">Name</th>
                  <th style="width: 18%;">Date</th>
                  <th style="width: 12%;">Size</th>
                  <th style="width: 15%;">File Type</th>
                  <th style="width: 10%;">Assets</th>
                </tr>
        """

    # JavaScript for pagination and row expansion with dynamic page size
    js_code = f"""
    <script>
    (function() {{
        const widgetId = '{widget_id}';
        let pageSize = {page_size};
        const totalItems = {cached_count};
        let currentPage = 0;

        function getTotalPages() {{
            return Math.max(1, Math.ceil(totalItems / pageSize));
        }}

        function updatePage() {{
            const tbody = document.getElementById('tbody-' + widgetId);
            const rows = tbody.querySelectorAll('tr[data-idx]');
            const totalPages = getTotalPages();
            const startIdx = currentPage * pageSize;
            const endIdx = Math.min(startIdx + pageSize, totalItems);

            rows.forEach(row => {{
                const idx = parseInt(row.getAttribute('data-idx'));
                const detailRow = document.getElementById('detail-' + widgetId + '-' + idx);
                if (idx >= startIdx && idx < endIdx) {{
                    row.style.display = '';
                }} else {{
                    row.style.display = 'none';
                    if (detailRow) detailRow.style.display = 'none';
                }}
            }});

            // Update pagination info
            const pageInfo = document.getElementById('pageinfo-' + widgetId);
            if (totalItems > 0) {{
                pageInfo.textContent = 'Page ' + (currentPage + 1) + ' of ' + totalPages + ' (' + (startIdx + 1) + '-' + endIdx + ' of ' + totalItems + ' loaded)';
            }} else {{
                pageInfo.textContent = 'No results loaded';
            }}

            // Update button states
            document.getElementById('prev-' + widgetId).disabled = currentPage === 0;
            document.getElementById('next-' + widgetId).disabled = currentPage >= totalPages - 1;
            document.getElementById('first-' + widgetId).disabled = currentPage === 0;
            document.getElementById('last-' + widgetId).disabled = currentPage >= totalPages - 1;
        }}

        window['goFirst_' + widgetId] = function() {{ currentPage = 0; updatePage(); }};
        window['goPrev_' + widgetId] = function() {{ if (currentPage > 0) currentPage--; updatePage(); }};
        window['goNext_' + widgetId] = function() {{
            const totalPages = getTotalPages();
            if (currentPage < totalPages - 1) currentPage++;
            updatePage();
        }};
        window['goLast_' + widgetId] = function() {{
            currentPage = getTotalPages() - 1;
            updatePage();
        }};

        window['changePageSize_' + widgetId] = function(newSize) {{
            pageSize = parseInt(newSize);
            currentPage = 0;  // Reset to first page
            updatePage();
        }};

        window['toggleDetail_' + widgetId] = function(idx) {{
            const detailRow = document.getElementById('detail-' + widgetId + '-' + idx);
            const toggleBtn = document.getElementById('toggle-' + widgetId + '-' + idx);
            if (detailRow) {{
                if (detailRow.style.display === 'none') {{
                    detailRow.style.display = '';
                    toggleBtn.textContent = '▼';
                }} else {{
                    detailRow.style.display = 'none';
                    toggleBtn.textContent = '▶';
                }}
            }}
        }};

        // Initial page setup
        setTimeout(updatePage, 50);
    }})();
    </script>
    """

    # Build "load more" hint if more results available
    load_more_hint = ""
    if more_available:
        remaining = (total_hits or 0) - cached_count
        load_more_hint = f"""
        <div style="margin-top: 8px; padding: 8px; background: var(--ea-warning-bg); border-radius: 3px; font-size: 0.85em; color: var(--ea-warning-text);">
          <b>Note:</b> {remaining:,} more results available. Use <code>list(results)</code> or iterate to load all, then display again.
        </div>
        """

    # Page size options
    page_size_options = "".join(
        [
            f'<option value="{size}" {"selected" if size == page_size else ""}>{size}</option>'
            for size in [10, 20, 50, 100]
        ]
    )

    # Only show pagination controls if there's more than one page of cached results
    if total_pages > 1:
        pagination_controls = f"""
        <div style="display: flex; align-items: center; justify-content: space-between; margin-top: 8px; padding: 8px; background: var(--ea-bg-secondary, #f8f9fa); border-radius: 3px; flex-wrap: wrap; gap: 8px;">
          <div style="display: flex; align-items: center; gap: 4px;">
            <button id="first-{widget_id}" onclick="goFirst_{widget_id}()" class="btn btn-sm" title="First page">⏮</button>
            <button id="prev-{widget_id}" onclick="goPrev_{widget_id}()" class="btn btn-sm" title="Previous page">◀</button>
            <button id="next-{widget_id}" onclick="goNext_{widget_id}()" class="btn btn-sm" title="Next page">▶</button>
            <button id="last-{widget_id}" onclick="goLast_{widget_id}()" class="btn btn-sm" title="Last page">⏭</button>
          </div>
          <div style="display: flex; align-items: center; gap: 8px;">
            <label style="font-size: 0.85em; color: var(--ea-text-secondary, #555); margin: 0;">
              Per page:
              <select onchange="changePageSize_{widget_id}(this.value)" style="margin-left: 4px; padding: 2px 4px; border-radius: 3px; border: 1px solid var(--ea-border-color);">
                {page_size_options}
              </select>
            </label>
          </div>
          <div id="pageinfo-{widget_id}" style="font-size: 0.85em; color: var(--ea-text-secondary, #555);">
            Page 1 of {total_pages}
          </div>
        </div>
        {load_more_hint}
        """
    else:
        # Single page - no pagination controls needed, just show the hint if more available
        pagination_controls = load_more_hint

    return f"""
    {css_inline}
    <div class="bootstrap ea-container" id="widget-{widget_id}">
      <div class="container-fluid border" style="padding: 10px;">
        <div class="row">
          <div class="col-12">
            <h5 style="margin-bottom: 5px;">{result_title}</h5>
          </div>
        </div>
        <div class="row">
          <div class="col-6">
            <p style="margin: 2px 0;"><b>Total in CMR</b>: {hits_str}</p>
          </div>
          <div class="col-6">
            <p style="margin: 2px 0;"><b>Loaded</b>: {cached_count:,} {result_type}</p>
          </div>
        </div>
        {summary_html}
        <details style="margin-top: 10px;">
          <summary style="cursor: pointer; padding: 5px; background: var(--ea-bg-tertiary, #f5f5f5); border-radius: 3px;">
            <b>Browse Results</b>
          </summary>
          {pagination_controls}
          <div style="margin-top: 8px; max-height: 500px; overflow-y: auto;">
            <table class="table table-sm table-striped" style="font-size: 0.85em;">
              <thead style="position: sticky; top: 0; background: var(--ea-bg-primary, white); z-index: 1;">
                {table_header}
              </thead>
              <tbody id="tbody-{widget_id}">
                {all_rows_html}
              </tbody>
            </table>
          </div>
        </details>
      </div>
    </div>
    {js_code}
    """


def _granule_row_with_index(granule: "DataGranule", idx: int, widget_id: str) -> str:
    """Generate a collapsible table row for a granule with index for pagination.

    Creates a main row with summary info and a hidden detail row that expands
    to show the granule's individual asset files.
    """
    granule_ur = granule.get("umm", {}).get("GranuleUR", "Unknown")
    # Truncate name
    name_display = granule_ur[:40] + "..." if len(granule_ur) > 40 else granule_ur

    # Get temporal
    temporal = granule.get("umm", {}).get("TemporalExtent", {})
    date_str = _format_temporal_extent(temporal, short=True)

    # Size
    size = round(granule.size, 2)

    # File type
    file_type = granule.data_type

    # Concept ID
    concept_id = granule.get("meta", {}).get("concept-id", "")

    # Asset file list for the detail row
    assets = granule.assets()
    asset_rows = []
    for asset in assets:
        label = asset.title or asset.href.split("/")[-1]
        role_badge = ""
        if asset.is_data():
            role_badge = '<span style="background: var(--ea-success); color: white; padding: 1px 5px; border-radius: 8px; font-size: 0.7em;">data</span>'
        elif asset.is_thumbnail():
            role_badge = '<span style="background: var(--ea-warning); color: var(--ea-text-primary); padding: 1px 5px; border-radius: 8px; font-size: 0.7em;">thumb</span>'
        size_str = f"{asset.size / (1024 * 1024):.2f} MB" if asset.size else "—"

        # One link per access scheme: S3 gets a cloud icon, HTTPS gets a file icon.
        # When a file is reachable through both schemes, both links are shown.
        access_links: List[str] = [asset.href]
        if asset.alternate and asset.alternate != asset.href:
            access_links.append(asset.alternate)
        link_icons = []
        for url in access_links:
            icon = "☁️" if url.startswith("s3://") else "⬇️"
            link_icons.append(
                f'<a href="{url}" target="_blank" title="{url}" style="flex: 0 0 auto;">{icon}</a>'
            )
        access_html = "".join(link_icons)

        asset_rows.append(
            f"""
            <div style="display: flex; align-items: center; gap: 8px; padding: 3px 0; border-bottom: 1px solid var(--ea-border-color);">
              <span style="flex: 0 0 18px; color: var(--ea-text-muted);">{role_badge}</span>
              <code style="flex: 1; font-size: 0.8em; word-break: break-all;">{label}</code>
              <span style="flex: 0 0 auto; color: var(--ea-text-muted); font-size: 0.75em;">{size_str}</span>
              {access_html}
            </div>
            """
        )
    assets_html = (
        "".join(asset_rows)
        if asset_rows
        else "<p style='color: var(--ea-text-muted);'>No assets found.</p>"
    )

    # File count badge in the main row
    file_badge = ""
    if assets:
        file_badge = f'<span style="background: var(--ea-btn-secondary-bg); color: white; padding: 2px 6px; border-radius: 10px; font-size: 0.75em; margin-left: 4px;">{len(assets)}</span>'

    # Link column: for multi-file granules this expands the asset list; for
    # single-file granules it links directly to the (only) data file.
    link_html = ""
    if len(assets) > 1:
        link_html = (
            f'<span onclick="event.stopPropagation(); toggleDetail_{widget_id}({idx})" '
            f'title="Show {len(assets)} files" style="cursor: pointer;">📁</span>'
        )
    else:
        data_links = granule.data_links(access="https")
        if data_links:
            first_link = data_links[0]
            link_html = (
                f'<a href="{first_link}" target="_blank" title="{first_link}">📥</a>'
            )

    # Main row with toggle button
    main_row = f"""
    <tr data-idx="{idx}" style="cursor: pointer;" onclick="toggleDetail_{widget_id}({idx})">
      <td style="color: var(--ea-text-muted); text-align: center;">
        <span id="toggle-{widget_id}-{idx}" style="font-size: 0.8em; color: var(--ea-text-secondary);">▶</span>
      </td>
      <td title="{granule_ur}"><code style="font-size: 0.8em;">{name_display}</code>{file_badge}</td>
      <td>{date_str}</td>
      <td>{size} MB</td>
      <td>{file_type}</td>
      <td>{link_html}</td>
    </tr>
    """

    # Thumbnail image (browse) for the detail row, if the granule has one
    thumb_html = ""
    dataviz = granule.dataviz_links
    if dataviz:
        thumb_url = dataviz[0]
        thumb_html = f"""
            <a href="{thumb_url}" target="_blank" title="View full-size browse image">
              <img src="{thumb_url}" alt="Browse image" style="width: 100%; max-height: 140px; object-fit: cover; border-radius: 4px; border: 1px solid var(--ea-border-color); margin-bottom: 8px; background: var(--ea-bg-tertiary);" onerror="this.style.display='none';">
            </a>
        """

    # Detail row (hidden by default)
    detail_row = f"""
    <tr id="detail-{widget_id}-{idx}" style="display: none; background: var(--ea-bg-tertiary, #f9f9f9);">
      <td colspan="6" style="padding: 10px 15px;">
        <div style="display: grid; grid-template-columns: 1fr 2fr; gap: 10px;">
          <div>
            {thumb_html}
            <p style="margin: 4px 0;"><b>Concept ID:</b> <code style="font-size: 0.85em;">{_concept_link(concept_id)}</code></p>
            <p style="margin: 4px 0;"><b>File Type:</b> {file_type}</p>
            <p style="margin: 4px 0;"><b>Size:</b> {size} MB</p>
            <p style="margin: 4px 0;"><b>Temporal:</b> {date_str}</p>
          </div>
          <div>
            <p style="margin: 4px 0 8px 0;"><b>Files ({len(assets)}):</b></p>
            <div>{assets_html}</div>
          </div>
        </div>
      </td>
    </tr>
    """

    return main_row + detail_row


def _collection_row_with_index(
    collection: "DataCollection", idx: int, widget_id: str
) -> str:
    """Generate a collapsible table row for a collection with index for pagination.

    Creates a main row with summary info and a hidden detail row that expands
    to show links, temporal coverage, and spatial extent.
    """
    short_name = collection.get_umm("ShortName") or "Unknown"
    version = collection.version or ""
    name_display = f"{short_name}"
    if version:
        name_display += f" v{version}"

    # Truncate if needed
    if len(name_display) > 30:
        name_display = name_display[:27] + "..."

    # Concept ID
    concept_id = collection.concept_id

    # DOI
    doi = collection.doi
    if doi:
        doi_html = f'<a href="https://doi.org/{doi}" target="_blank" style="font-size: 0.8em;">{doi[:30]}{"..." if len(doi) > 30 else ""}</a>'
    else:
        doi_html = '<span style="color: var(--ea-text-muted);">—</span>'

    # Cloud
    cloud_info = collection.get_umm("DirectDistributionInformation")
    is_cloud = cloud_info is not None and bool(cloud_info)
    cloud_icon = "☁️" if is_cloud else "🖥️"

    # Data format and average file size from ArchiveAndDistributionInformation
    archive_info = collection.get_umm("ArchiveAndDistributionInformation")
    data_format = ""
    avg_size = ""
    if isinstance(archive_info, dict):
        file_dist = archive_info.get("FileDistributionInformation", [])
        if isinstance(file_dist, list) and file_dist:
            first_dist = file_dist[0]
            data_format = first_dist.get("Format", "")
            avg_file_size = first_dist.get("AverageFileSize")
            avg_file_unit = first_dist.get("AverageFileSizeUnit", "MB")
            if avg_file_size is not None:
                avg_size = f"{avg_file_size} {avg_file_unit}"

    # Granule count from meta
    granule_count = collection.get("meta", {}).get("granule-count")
    granule_str = f"{granule_count:,}" if granule_count else "—"

    # Links count for the main row
    landing = collection.landing_page
    get_data = collection.data_links
    link_count = (1 if landing else 0) + len(get_data)
    links_badge = f'<span style="background: var(--ea-btn-primary-bg); color: white; padding: 2px 6px; border-radius: 10px; font-size: 0.75em;">{link_count}</span>'

    # Build detail row content
    # Temporal extent
    temporal_extents = collection.get_umm("TemporalExtents")
    temporal_str = _format_collection_temporal(temporal_extents)

    # Spatial extent
    spatial_extent = collection.get_umm("SpatialExtent")
    spatial_str = _format_spatial_extent(spatial_extent)

    # Links for detail row - use RelatedUrls with proper labels
    links_html_parts = []
    if landing:
        links_html_parts.append(
            f'<a href="{landing}" target="_blank" class="btn btn-sm btn-primary" style="margin: 2px;">🏠 Landing Page</a>'
        )

    # Get RelatedUrls with their types for proper labeling
    related_urls = collection.get("umm", {}).get("RelatedUrls", [])
    data_links_with_labels = []
    for url_info in related_urls:
        url = url_info.get("URL", "")
        url_type = url_info.get("Type", "")
        subtype = url_info.get("Subtype", "")

        # Only include GET DATA type links
        if url_type == "GET DATA" and url.startswith("http"):
            # Use subtype as label, fallback to simplified type
            label = _format_link_label(subtype) if subtype else "Data"
            data_links_with_labels.append((url, label))

    # Add data links (first 5) with their proper labels
    for url, label in data_links_with_labels[:5]:
        links_html_parts.append(
            f'<a href="{url}" target="_blank" class="btn btn-sm btn-outline-secondary" style="margin: 2px;">📥 {label}</a>'
        )

    links_html = " ".join(links_html_parts)

    # Format display for data format
    format_html = (
        f'<span style="font-size: 0.8em;">{data_format}</span>'
        if data_format
        else '<span style="color: var(--ea-text-muted);">—</span>'
    )

    # Main row with toggle button
    main_row = f"""
    <tr data-idx="{idx}" style="cursor: pointer;" onclick="toggleDetail_{widget_id}({idx})">
      <td style="text-align: center;">
        <span id="toggle-{widget_id}-{idx}" style="font-size: 0.8em; color: var(--ea-text-secondary);">▶</span>
      </td>
      <td title="{short_name}"><code style="font-size: 0.85em;">{name_display}</code></td>
      <td>{format_html}</td>
      <td style="text-align: right;">{granule_str}</td>
      <td style="text-align: center;">{cloud_icon}</td>
      <td style="text-align: center;">{links_badge}</td>
    </tr>
    """

    # Detail row (hidden by default)
    detail_row = f"""
    <tr id="detail-{widget_id}-{idx}" style="display: none; background: var(--ea-bg-tertiary, #f9f9f9);">
      <td colspan="6" style="padding: 10px 15px;">
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
          <div>
            <p style="margin: 4px 0;"><b>Concept ID:</b> <code style="font-size: 0.85em;">{_concept_link(concept_id)}</code></p>
            <p style="margin: 4px 0;"><b>DOI:</b> {doi_html}</p>
            <p style="margin: 4px 0;"><b>Avg File Size:</b> {avg_size or "—"}</p>
            <p style="margin: 4px 0;"><b>Temporal:</b> {temporal_str}</p>
            <p style="margin: 4px 0;"><b>Spatial:</b> {spatial_str}</p>
          </div>
          <div>
            <p style="margin: 4px 0 8px 0;"><b>Links:</b></p>
            <div>{links_html}</div>
          </div>
        </div>
      </td>
    </tr>
    """

    return main_row + detail_row


def _generate_table_rows(items: List[Any]) -> str:
    """Generate HTML table rows for a list of results."""
    if not items:
        return "<tr><td colspan='5' style='text-align: center;'>No results cached yet. Iterate over results to populate.</td></tr>"

    rows = []
    for item in items:
        if _is_granule(item):
            row = _granule_row(item)
        else:
            row = _collection_row(item)
        rows.append(row)

    return "\n".join(rows)


def _is_granule(item: Any) -> bool:
    """Check if an item is a DataGranule (vs DataCollection)."""
    if item is None:
        return True  # Default assumption
    # Granules have GranuleUR, collections have ShortName at top level of umm
    return "GranuleUR" in item.get("umm", {})


def _granule_row(granule: "DataGranule") -> str:
    """Generate a table row for a granule."""
    granule_ur = granule.get("umm", {}).get("GranuleUR", "Unknown")
    # Truncate name
    name_display = granule_ur[:45] + "..." if len(granule_ur) > 45 else granule_ur

    # Get temporal
    temporal = granule.get("umm", {}).get("TemporalExtent", {})
    date_str = _format_temporal_extent(temporal, short=True)

    # Size
    size = round(granule.size, 2)

    # File type
    file_type = granule.data_type

    # First data link
    data_links = granule.data_links()
    link_html = ""
    if data_links:
        first_link = data_links[0]
        link_html = (
            f'<a href="{first_link}" target="_blank" title="{first_link}">📥</a>'
        )

    return f"""
    <tr>
      <td title="{granule_ur}"><code style="font-size: 0.8em;">{name_display}</code></td>
      <td>{date_str}</td>
      <td>{size} MB</td>
      <td>{file_type}</td>
      <td>{link_html}</td>
    </tr>
    """


def _collection_row(collection: "DataCollection") -> str:
    """Generate a table row for a collection."""
    short_name = collection.get_umm("ShortName") or "Unknown"
    version = collection.version or ""
    name_display = f"{short_name} v{version}" if version else short_name

    # Truncate if needed
    if len(name_display) > 40:
        name_display = name_display[:37] + "..."

    # Get temporal
    temporal_extents = collection.get_umm("TemporalExtents")
    date_str = _format_collection_temporal(temporal_extents, short=True)

    # Size - collections don't have size, show "-"
    size_str = "-"

    # Cloud
    cloud_info = collection.get_umm("DirectDistributionInformation")
    is_cloud = cloud_info is not None and bool(cloud_info)
    cloud_icon = "☁️" if is_cloud else "🖥️"

    # Landing page link
    landing = collection.landing_page
    link_html = (
        f'<a href="{landing}" target="_blank" title="{landing}">🔗</a>'
        if landing
        else ""
    )

    return f"""
    <tr>
      <td title="{short_name}"><code style="font-size: 0.8em;">{name_display}</code></td>
      <td>{date_str}</td>
      <td>{size_str}</td>
      <td>{cloud_icon}</td>
      <td>{link_html}</td>
    </tr>
    """


def _compute_summary(items: List[Any]) -> dict:
    """Compute summary statistics for cached results."""
    if not items:
        return {
            "total_size_mb": 0.0,
            "cloud_count": 0,
            "temporal_range": "N/A",
        }

    total_size = 0.0
    cloud_count = 0
    min_date: Optional[str] = None
    max_date: Optional[str] = None

    for item in items:
        if _is_granule(item):
            total_size += item.size
            if item.cloud_hosted:
                cloud_count += 1
            # Extract date
            temporal = item.get("umm", {}).get("TemporalExtent", {})
            range_dt = temporal.get("RangeDateTime", {})
            begin = range_dt.get("BeginningDateTime")
            end = range_dt.get("EndingDateTime")
            if begin:
                if min_date is None or begin < min_date:
                    min_date = begin
            if end:
                if max_date is None or end > max_date:
                    max_date = end
        else:
            # Collection
            cloud_info = item.get_umm("DirectDistributionInformation")
            if cloud_info:
                cloud_count += 1

    # Format temporal range
    if min_date and max_date:
        min_short = min_date[:10] if len(min_date) >= 10 else min_date
        max_short = max_date[:10] if len(max_date) >= 10 else max_date
        temporal_range = f"{min_short} to {max_short}"
    elif min_date:
        temporal_range = f"{min_date[:10]} to present"
    else:
        temporal_range = "N/A"

    return {
        "total_size_mb": total_size,
        "cloud_count": cloud_count,
        "temporal_range": temporal_range,
    }


def _format_temporal_extent(temporal: dict, short: bool = False) -> str:
    """Format a granule's temporal extent for display."""
    range_dt = temporal.get("RangeDateTime", {})
    begin = range_dt.get("BeginningDateTime", "")
    end = range_dt.get("EndingDateTime", "")

    if short:
        # Just show start date
        if begin:
            return begin[:10] if len(begin) >= 10 else begin
        return "N/A"

    if begin and end:
        begin_short = begin[:10] if len(begin) >= 10 else begin
        end_short = end[:10] if len(end) >= 10 else end
        return f"{begin_short} to {end_short}"
    elif begin:
        begin_short = begin[:10] if len(begin) >= 10 else begin
        return f"{begin_short}"
    return "N/A"


def _format_collection_temporal(
    temporal_extents: Optional[List[dict]], short: bool = False
) -> str:
    """Format a collection's temporal extent for display."""
    if not temporal_extents or not isinstance(temporal_extents, list):
        return "N/A"

    extent = temporal_extents[0] if temporal_extents else {}
    range_dts = extent.get("RangeDateTimes", [])

    if range_dts:
        range_dt = range_dts[0]
        begin = range_dt.get("BeginningDateTime", "")
        end = range_dt.get("EndingDateTime")

        if short:
            if begin:
                return begin[:10] if len(begin) >= 10 else begin
            return "N/A"

        begin_short = begin[:10] if len(begin) >= 10 and begin else ""
        if end:
            end_short = end[:10] if len(end) >= 10 else end
            return f"{begin_short} to {end_short}"
        else:
            return f"{begin_short} to present"

    return "N/A"


def _format_spatial_extent(spatial_extent: Optional[dict]) -> str:
    """Format a collection's spatial extent for display.

    Parameters:
        spatial_extent: The SpatialExtent from UMM metadata

    Returns:
        Human-readable spatial coverage string
    """
    if not spatial_extent:
        return "Global"

    h_domain = spatial_extent.get("HorizontalSpatialDomain", {})
    geometry = h_domain.get("Geometry", {})

    # Try bounding rectangles
    bounding_rects = geometry.get("BoundingRectangles", [])
    if bounding_rects:
        rect = bounding_rects[0]
        west = rect.get("WestBoundingCoordinate", -180)
        south = rect.get("SouthBoundingCoordinate", -90)
        east = rect.get("EastBoundingCoordinate", 180)
        north = rect.get("NorthBoundingCoordinate", 90)

        # Check if global
        if west <= -179 and east >= 179 and south <= -89 and north >= 89:
            return "Global"

        return f"Lon: {west:.1f}° to {east:.1f}°, Lat: {south:.1f}° to {north:.1f}°"

    # Check for GPolygons
    gpolygons = geometry.get("GPolygons", [])
    if gpolygons:
        return f"{len(gpolygons)} polygon(s)"

    return "Global"


def _format_link_label(subtype: str) -> str:
    """Format a UMM RelatedUrl Subtype into a human-readable label.

    Parameters:
        subtype: The Subtype field from a UMM RelatedUrl

    Returns:
        A short, human-readable label for the link
    """
    # Common subtype mappings to shorter labels
    label_map = {
        "DATA TREE": "Data Tree",
        "Earthdata Search": "Earthdata Search",
        "DIRECT DOWNLOAD": "Direct Download",
        "GET DATA": "Get Data",
        "OPENDAP DATA": "OPeNDAP",
        "THREDDS DATA": "THREDDS",
        "SUBSETTER": "Subsetter",
        "DATA CATALOG": "Data Catalog",
        "WEB MAP SERVICE (WMS)": "WMS",
        "WEB COVERAGE SERVICE (WCS)": "WCS",
        "WEB FEATURE SERVICE (WFS)": "WFS",
        "ORDER DATA": "Order Data",
        "USER'S GUIDE": "User Guide",
        "GENERAL DOCUMENTATION": "Docs",
        "DATA CITATION GUIDELINES": "Citation",
        "ALGORITHM THEORETICAL BASIS DOCUMENT (ATBD)": "ATBD",
        "PRODUCT QUALITY ASSESSMENT": "Quality",
        "PRODUCT USAGE": "Usage",
        "BROWSE": "Browse",
        "THUMBNAIL": "Thumbnail",
    }

    # Return mapped label or title-cased version of subtype
    if subtype in label_map:
        return label_map[subtype]

    # Title case and truncate if needed
    label = subtype.title()
    if len(label) > 20:
        label = label[:17] + "..."
    return label


__all__ = [
    "_repr_auth_html",
    "_repr_granule_html",
    "_repr_collection_html",
    "_repr_search_results_html",
]
