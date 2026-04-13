"""Backward-compat shim for the Murmur CLI.

The implementation has moved to the standalone ``murmur-cli`` package
(``murmur_cli.cli``). This module aliases itself to ``murmur_cli.cli`` so
existing ``from murmur.cli import ...`` imports keep working, including
tests that ``monkeypatch.setattr("murmur.cli.RELAY_URL", ...)`` — both names
now resolve to the same module object.

The console script entry point ``murmur = "murmur_cli.cli:main"`` points
directly at the new location; this shim exists for programmatic imports
and legacy invocations like ``python -m murmur.cli``.
"""

import sys as _sys

from murmur_cli import cli as _cli

# Preserve ``python -m murmur.cli`` entry point. The check must happen BEFORE
# we alias this module so ``__name__`` is still ``__main__`` when relevant.
if __name__ == "__main__":
    _cli.main()
else:
    # Make ``murmur.cli`` and ``murmur_cli.cli`` the same module object so
    # attribute mutations (e.g. monkeypatching module-level constants like
    # ``RELAY_URL``, ``RELAY_SECRET``, ``_cached_jwt``) are observed by the
    # functions defined in ``murmur_cli.cli``.
    _sys.modules[__name__] = _cli
