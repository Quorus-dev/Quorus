"""Set required environment variables before any test module imports."""

import os
import re

import pytest

os.environ.setdefault("RELAY_SECRET", "test-secret")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long")
os.environ.setdefault("BOOTSTRAP_SECRET", "test-bootstrap-secret")


_OPT_IN_MARKERS = {"real_harness", "cross_harness"}


def _marker_explicitly_selected(markexpr: str, marker: str) -> bool:
    """Return True when the user deliberately selected an opt-in marker."""
    if re.search(rf"\bnot\s+{re.escape(marker)}\b", markexpr):
        return False
    return marker in set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", markexpr))


def pytest_collection_modifyitems(config, items):
    """Skip live vendor-harness tests unless their marker was explicitly selected.

    Marker registration only documents tests; it does not stop default
    ``pytest`` from running them. These E2E tests spawn real LLM CLIs and depend
    on host auth/config, so default verification must skip them.
    """
    markexpr = config.option.markexpr or ""
    for item in items:
        for marker in _OPT_IN_MARKERS:
            if item.get_closest_marker(marker) is None:
                continue
            if _marker_explicitly_selected(markexpr, marker):
                continue
            item.add_marker(
                pytest.mark.skip(reason=f"opt-in: run with -m {marker}")
            )
