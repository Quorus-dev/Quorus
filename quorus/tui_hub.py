"""Backward-compat shim for the Quorus TUI hub.

The implementation has moved to the standalone ``quorus-tui`` package
(``quorus_tui.hub``). This module aliases itself to ``quorus_tui.hub`` so
existing ``from quorus.tui_hub import ...`` imports keep working, including
tests that ``monkeypatch.setattr(tui_hub, "CONFIG_FILE", ...)`` — both names
now resolve to the same module object.
"""

import sys as _sys

from quorus_tui import hub as _hub

# Make ``quorus.tui_hub`` and ``quorus_tui.hub`` the same module object so
# attribute mutations (e.g. monkeypatching module-level constants like
# ``CONFIG_FILE``) are observed by the functions defined in ``quorus_tui.hub``.
_sys.modules[__name__] = _hub
