"""Backward-compat shim for the Murmur decorator-based agent API.

The implementation has moved to the standalone ``murmur-cli`` package
(``murmur_cli.decorators``). This module aliases itself to
``murmur_cli.decorators`` so existing ``from murmur.decorators import Agent``
imports keep working, including tests that
``patch("murmur.decorators.MurmurClient")`` — both names now resolve to the
same module object.
"""

import sys as _sys

from murmur_cli import decorators as _decorators

# Make ``murmur.decorators`` and ``murmur_cli.decorators`` the same module
# object so attribute mutations (e.g. patching module-level names like
# ``MurmurClient``) are observed by the classes defined in
# ``murmur_cli.decorators``.
_sys.modules[__name__] = _decorators
