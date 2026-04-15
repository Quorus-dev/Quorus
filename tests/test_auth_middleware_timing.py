"""Smoke test that JWT / HMAC verification uses constant-time compare.

AST-walks ``quorus.auth.middleware`` and flags any ``==``/``!=`` comparison
whose operand identifier looks like a secret (signature, mac, digest, hmac,
secret, token). Such comparisons MUST go through ``hmac.compare_digest``.
Role/identity string comparisons are fine.
"""
from __future__ import annotations

import ast
import inspect

from quorus.auth import middleware

_SECRET_NAME_HINTS = (
    "signature",
    "mac",
    "digest",
    "hmac",
    "secret",
    "token_hash",
    "expected_hash",
)


def _name_of(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id.lower()
    if isinstance(node, ast.Attribute):
        return node.attr.lower()
    return ""


def test_no_non_constant_time_secret_comparisons():
    source = inspect.getsource(middleware)
    tree = ast.parse(source)
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
            continue
        names = [_name_of(node.left)] + [_name_of(c) for c in node.comparators]
        if any(hint in n for hint in _SECRET_NAME_HINTS for n in names):
            offenders.append((node.lineno, ast.dump(node)))
    assert not offenders, (
        f"secret-ish identifiers compared with ==/!=; use hmac.compare_digest: "
        f"{offenders}"
    )


def test_module_uses_compare_digest():
    source = inspect.getsource(middleware)
    assert "hmac.compare_digest" in source, (
        "auth middleware must use hmac.compare_digest for secret comparisons"
    )
