"""A long MCP message is receipted by hash, not refused (0.5.2).

The relay caps audit arguments at 4 KB, so every MCP send over 4 KB failed with
"arguments payload exceeds 4KB" although rooms take 50 KB (live check of 0.5.1).
"""

import hashlib
import json

from quorus_mcp.tools import _audit_arguments


def _size(d: dict) -> int:
    return len(json.dumps(d, default=str).encode("utf-8"))


def test_small_arguments_are_receipted_as_is() -> None:
    args = {"room_id": "dev", "content": "hello"}
    assert _audit_arguments(args) == args


def test_a_long_message_is_pinned_by_its_hash() -> None:
    body = "x" * 60_000
    out = _audit_arguments({"room_id": "dev", "content": body})
    assert out["room_id"] == "dev"
    assert out["content"] == {"sha256": hashlib.sha256(body.encode()).hexdigest(),
                              "bytes": 60_000}
    assert _size(out) <= 4096


def test_many_long_fields_still_fit_the_cap() -> None:
    args = {f"f{i}": "y" * 600 for i in range(60)}
    out = _audit_arguments(args)
    assert _size(out) <= 4096 and set(out) == {"digest"}
