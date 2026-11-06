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
import queue
import subprocess
import sys
import threading
import time
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
    proc = subprocess.Popen([sys.executable, "-m", "quorus_mcp.server"], env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    lines: queue.Queue[str] = queue.Queue()
    threading.Thread(target=lambda: [lines.put(ln) for ln in proc.stdout],
                     daemon=True).start()
    err_chunks: list[str] = []  # drained alongside, so a chatty stderr can't block
    err_reader = threading.Thread(target=lambda: err_chunks.append(proc.stderr.read()),
                                  daemon=True)
    err_reader.start()
    replies: dict = {}
    out = ""

    def send_and_wait(msg: str, want: int | None) -> None:
        # Wait for each reply before closing stdin: the server stops reading at
        # EOF and could exit before it answered the tool call (CI flake, 0.7.16).
        nonlocal out
        proc.stdin.write(msg + "\n")
        proc.stdin.flush()
        deadline = time.monotonic() + 60
        while want is not None and want not in replies and time.monotonic() < deadline:
            try:
                ln = lines.get(timeout=0.5)
            except queue.Empty:
                if proc.poll() is not None:
                    return
                continue
            out += ln
            if ln.strip():
                m = json.loads(ln)
                if "id" in m:
                    replies[m["id"]] = m

    try:
        send_and_wait(_rpc(1, "initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "t", "version": "1"}}), 1)
        send_and_wait(_rpc(None, "notifications/initialized"), None)
        send_and_wait(_rpc(2, "tools/call", {"name": "list_rooms", "arguments": {}}), 2)
        proc.stdin.close()
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    err_reader.join(timeout=10)
    err = "".join(err_chunks)
    assert 1 in replies, f"no initialize reply; stderr: {err[-600:]}"
    instructions = replies[1]["result"]["instructions"]
    assert instructions.startswith("Quorus isn't set up for this session yet")
    assert "/plugin, then Quorus, then Configure" in instructions
    assert 2 in replies, f"the tool call got no reply; stderr: {err[-600:]}"
    text = replies[2]["result"]["content"][0]["text"]
    assert replies[2]["result"].get("isError") is True
    assert "Relay URL" in text and "quorus init" in text
    assert "Connection closed" not in out + err
