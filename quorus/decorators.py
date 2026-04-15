"""Backward-compat shim for the Quorus decorator-based agent API.

The implementation has moved to the standalone ``quorus-cli`` package
(``quorus_cli.decorators``). This module aliases itself to
``quorus_cli.decorators`` so existing ``from quorus.decorators import Agent``
imports keep working, including tests that
``patch("quorus.decorators.QuorusClient")`` — both names now resolve to the
same module object.
"""

import sys as _sys

from quorus_cli import decorators as _decorators

# Make ``quorus.decorators`` and ``quorus_cli.decorators`` the same module
# object so attribute mutations (e.g. patching module-level names like
# ``QuorusClient``) are observed by the classes defined in
# ``quorus_cli.decorators``.
_sys.modules[__name__] = _decorators
