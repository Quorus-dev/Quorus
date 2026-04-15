"""Backward-compat shim for the Quorus CLI.

The implementation has moved to the standalone ``quorus-cli`` package
(``quorus_cli.cli``). This module aliases itself to ``quorus_cli.cli`` so
existing ``from quorus.cli import ...`` imports keep working, including
tests that ``monkeypatch.setattr("quorus.cli.RELAY_URL", ...)`` — both names
now resolve to the same module object.

The console script entry point ``quorus = "quorus_cli.cli:main"`` points
directly at the new location; this shim exists for programmatic imports
and legacy invocations like ``python -m quorus.cli``.
"""

import sys as _sys

from quorus_cli import cli as _cli

# Preserve ``python -m quorus.cli`` entry point. The check must happen BEFORE
# we alias this module so ``__name__`` is still ``__main__`` when relevant.
if __name__ == "__main__":
    _cli.main()
else:
    # Make ``quorus.cli`` and ``quorus_cli.cli`` the same module object so
    # attribute mutations (e.g. monkeypatching module-level constants like
    # ``RELAY_URL``, ``RELAY_SECRET``, ``_cached_jwt``) are observed by the
    # functions defined in ``quorus_cli.cli``.
    _sys.modules[__name__] = _cli
