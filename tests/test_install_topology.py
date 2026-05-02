"""Regression tests for the Quorus monorepo install topology.

Background: at the April 23 2026 hackathon, a fresh `pipx install quorus`
produced a venv in which `import quorus_sdk` failed because hatchling's
editable shim files (`_editable_impl_quorus*.pth`) were marked macOS-hidden
and Python 3.14's site.py silently skipped them. See
`docs/HACKATHON_REGRESSION.md` for the full root cause.

These tests assert the invariants we never want to lose again:

1. Every one of the 5 packages (root + 4 in `packages/`) is importable
   from the current interpreter.
2. The `quorus_cli.cli:main` console-script entry point is a callable.
3. The root `pyproject.toml`'s wheel build manifest still lists every
   subpackage source root (so `python -m build` produces a self-contained
   wheel for pipx/PyPI users).

Run with:  pytest tests/test_install_topology.py -v

These tests intentionally use only stdlib + pytest so they work even on a
minimal `pip install -e ".[test]"` of the monorepo.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

REQUIRED_PACKAGES = (
    "quorus",
    "quorus_sdk",
    "quorus_cli",
    "quorus_mcp",
    "quorus_tui",
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- import topology -------------------------------------------------------


@pytest.mark.parametrize("pkg", REQUIRED_PACKAGES)
def test_package_imports(pkg: str) -> None:
    """Every package in the monorepo must import without exception."""
    # Drop any cached module to force re-resolution against the live sys.path.
    sys.modules.pop(pkg, None)
    mod = importlib.import_module(pkg)
    assert mod is not None
    # Sanity: every package exposes __file__, so we know it loaded from disk
    # (not a stub module).
    assert getattr(mod, "__file__", None), f"{pkg} has no __file__"


def test_quorus_reexports_room() -> None:
    """`from quorus import Room` is a public, documented import path.

    The root `quorus` package is a re-export shim over `quorus_sdk`, so this
    test catches the specific failure mode that broke the hackathon demo:
    `quorus/__init__.py` line 14 imports `from quorus.sdk import Room`,
    which itself imports from `quorus_sdk.http_agent`. If the sdk subpackage
    isn't on sys.path, this fails.
    """
    sys.modules.pop("quorus", None)
    import quorus

    assert hasattr(quorus, "Room"), "quorus.Room missing — sdk shim broken"
    assert quorus.__version__, "quorus.__version__ missing"


def test_cli_entry_point_is_callable() -> None:
    """`quorus_cli.cli:main` must be importable as a callable.

    This is the function pyproject.toml's [project.scripts] points the
    `quorus` console script at, so a regression here breaks the CLI even
    before the user types anything.
    """
    from quorus_cli.cli import main

    assert callable(main), "quorus_cli.cli.main is not callable"


# --- wheel manifest --------------------------------------------------------


def test_root_pyproject_lists_every_subpackage() -> None:
    """The root `[tool.hatch.build.targets.wheel] packages` array must
    enumerate every subpackage source dir, otherwise `python -m build`
    produces a wheel that ships only the root `quorus/` tree and pipx users
    get `ModuleNotFoundError: quorus_sdk` on first import.
    """
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib  # type: ignore[no-redef]

    pyproject = REPO_ROOT / "pyproject.toml"
    with pyproject.open("rb") as f:
        data = tomllib.load(f)

    wheel_cfg = data["tool"]["hatch"]["build"]["targets"]["wheel"]
    declared = set(wheel_cfg["packages"])

    expected = {
        "quorus",
        "packages/sdk/quorus_sdk",
        "packages/cli/quorus_cli",
        "packages/mcp/quorus_mcp",
        "packages/tui/quorus_tui",
    }
    missing = expected - declared
    assert not missing, (
        "Root pyproject.toml [tool.hatch.build.targets.wheel].packages is "
        f"missing: {sorted(missing)}. Without these, the built wheel is "
        "incomplete and pipx users get import errors."
    )


def test_every_subpackage_has_pyproject() -> None:
    """Each `packages/*/` directory we ship must be a self-contained
    hatchling project so `pip install -e packages/sdk` keeps working as an
    escape hatch when the root install is broken (which is exactly what
    happened on Apr 23 2026)."""
    for sub in ("sdk", "cli", "mcp", "tui"):
        pj = REPO_ROOT / "packages" / sub / "pyproject.toml"
        assert pj.exists(), f"missing {pj.relative_to(REPO_ROOT)}"


# --- defensive checks ------------------------------------------------------


def test_no_quorus_sdk_circular_via_root() -> None:
    """The root `quorus/sdk.py` re-export shim must NOT import from itself.

    A regression where someone makes `quorus/sdk.py` execute
    `from quorus.sdk import …` would create an infinite recursion that
    only manifests at import time. Parse the file with `ast` so we ignore
    docstrings and comments and only flag actual import statements."""
    import ast

    shim = REPO_ROOT / "quorus" / "sdk.py"
    tree = ast.parse(shim.read_text(encoding="utf-8"), filename=str(shim))

    has_quorus_sdk_import = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module != "quorus.sdk", (
                "quorus/sdk.py must re-export from quorus_sdk.* — never "
                "from itself (would infinite-recurse at import time)."
            )
            if node.module and node.module.startswith("quorus_sdk"):
                has_quorus_sdk_import = True

    assert has_quorus_sdk_import, (
        "quorus/sdk.py is supposed to be a re-export of quorus_sdk.* — "
        "no `from quorus_sdk... import` statement found."
    )
