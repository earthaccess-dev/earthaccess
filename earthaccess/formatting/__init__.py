"""Output formatting and display utilities.

This module provides formatters for rendering earthaccess objects in Jupyter
notebooks and other environments that support rich display.

Submodules:
    - html: Static HTML formatters (always available)
    - widgets: Interactive anywidget-based formatters (optional, requires [widgets] extra)
"""

from typing import Any, List

import importlib_resources

from earthaccess.formatting.html import (  # noqa: E402
    _repr_collection_html,
    _repr_granule_html,
    _repr_search_results_html,
)

STATIC_FILES = ["styles.css"]


def _load_static_files() -> List[str]:
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


def has_widget_support() -> bool:
    """Check if anywidget and lonboard are available for interactive widgets.

    Returns:
        True if widget dependencies are installed, False otherwise.
    """
    try:
        import anywidget  # noqa: F401
        import lonboard  # noqa: F401

        return True
    except ImportError:
        return False


__all__ = [
    # Core utilities
    "STATIC_FILES",
    "_load_static_files",
    "has_widget_support",
    # HTML formatters (from html.py)
    "_repr_collection_html",
    "_repr_granule_html",
    "_repr_search_results_html",
    # Widget functions (from widgets.py) - require [widgets] extra
    "plot",
    "plot_granule",
    "plot_collection",
]


def __getattr__(name: str) -> Any:
    """Lazy import for widget functions to avoid import errors when deps missing."""
    if name in ("plot", "plot_granule", "plot_collection"):
        from earthaccess.formatting.widgets import (
            plot,
            plot_collection,
            plot_granule,
        )

        return {
            "plot": plot,
            "plot_granule": plot_granule,
            "plot_collection": plot_collection,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
