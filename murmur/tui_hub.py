"""Backward-compat shim for the Murmur TUI hub.

The implementation has moved to the standalone ``murmur-tui`` package
(``murmur_tui.hub``). This module aliases itself to ``murmur_tui.hub`` so
existing ``from murmur.tui_hub import ...`` imports keep working, including
tests that ``monkeypatch.setattr(tui_hub, "CONFIG_FILE", ...)`` — both names
now resolve to the same module object.
"""

import sys as _sys

from murmur_tui import hub as _hub

# Make ``murmur.tui_hub`` and ``murmur_tui.hub`` the same module object so
# attribute mutations (e.g. monkeypatching module-level constants like
# ``CONFIG_FILE``) are observed by the functions defined in ``murmur_tui.hub``.
_sys.modules[__name__] = _hub
