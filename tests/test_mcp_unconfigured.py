"""A Claude Code plugin that isn't configured yet must say what to set and where,
not "Connection closed" (overseer, 0.6.0 stranger test).

The plugin starts `quorus-mcp` with every QUORUS_* field empty. The server used to
exit at import, so the host showed only that the connection closed. Now it serves:
its instructions open with the setup steps, every tool answers with them, and it
sends nothing to any relay. A program importing the module still fails closed as
before (tests/test_mcp_auth_fallback.py).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _rpc(id_: int | None, method: str, params: dict | None = None) -> str:
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        msg["id"] = id_
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


def test_an_unconfigured_plugin_explains_the_setup_instead_of_closing(tmp_path) -> None:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path / "home"),
        "QUORUS_CONFIG_DIR": str(tmp_path / "home" / ".quorus"),
        "PYTHONPATH": os.pathsep.join(str(ROOT / p) for p in
                                      (".", "packages/mcp", "packages/cli", "packages/sdk")),
        # exactly what plugin.json passes when nothing is filled in
        "QUORUS_RELAY_URL": "", "QUORUS_INSTANCE_NAME": "",
        "QUORUS_RELAY_SECRET": "", "QUORUS_API_KEY": "",
    }
    (tmp_path / "home").mkdir()
    requests = "\n".join([
        _rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "t", "version": "1"}}),
        _rpc(None, "notifications/initialized"),
        _rpc(2, "tools/call", {"name": "list_rooms", "arguments": {}}),
    ]) + "\n"
    proc = subprocess.Popen([sys.executable, "-m", "quorus_mcp.server"], env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    try:
        out, err = proc.communicate(requests, timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
    replies = {m["id"]: m for m in (json.loads(ln) for ln in out.splitlines() if ln.strip())
               if "id" in m}
    assert 1 in replies, f"no initialize reply; stderr: {err[-600:]}"
    instructions = replies[1]["result"]["instructions"]
    assert instructions.startswith("Quorus isn't set up for this session yet")
    assert "/plugin, then Quorus, then Configure" in instructions
    assert 2 in replies, f"the tool call got no reply; stderr: {err[-600:]}"
    text = replies[2]["result"]["content"][0]["text"]
    assert replies[2]["result"].get("isError") is True
    assert "Relay URL" in text and "quorus init" in text
    assert "Connection closed" not in out + err
