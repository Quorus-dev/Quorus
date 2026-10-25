"""Version gating for episodes that run against an installed quorus-ai.

scripts/stranger_nightly.sh runs these episodes against the release on PyPI and
sets ``QUORUS_UNDER_TEST_VERSION`` to it. An episode marked
``@pytest.mark.since("X.Y.Z")`` covers a fix that ships in X.Y.Z, so against an
older release it is skipped, not failed: the nightly stays green for bugs that
are already fixed on weekend. With the variable unset (the worktree's own suite,
or scripts/wheel_first_run.sh on this checkout's wheel) nothing is skipped: the
code under test is the checkout, whatever its version string says.
"""

from __future__ import annotations

import os

import pytest
from packaging.version import InvalidVersion, Version

UNDER_TEST = "QUORUS_UNDER_TEST_VERSION"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "since(version): the episode needs quorus-ai >= version; skipped when "
        f"{UNDER_TEST} names an older installed release")


def skip_reason(marker_version: str, under_test: str | None) -> str | None:
    """Why an episode marked since(*marker_version*) is skipped against
    *under_test*, or None when it runs."""
    if not under_test:
        return None
    try:
        if Version(under_test) >= Version(marker_version):
            return None
    except InvalidVersion:
        return None  # an unknown version string: run everything
    return f"needs quorus-ai {marker_version}; testing the installed {under_test}"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    under_test = os.environ.get(UNDER_TEST)
    for item in items:
        marker = item.get_closest_marker("since")
        if marker is None:
            continue
        why = skip_reason(str(marker.args[0]), under_test)
        if why:
            item.add_marker(pytest.mark.skip(reason=why))
