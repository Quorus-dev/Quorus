"""``python -m quorus_mcp.server`` must deliver channel pushes.

Regression (2026-10-08): running the server with ``-m`` created a second
module object (``__main__``). The live MCP session was stored in that copy
while runtime.py looked it up in ``quorus_mcp.server``, so every room
message pushed to an open Claude session was silently discarded. Every
config Quorus writes (wake daemons, ``dogfood.sh connect``) uses ``-m``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ME = "t-claude-desktop"
_MSG = {"id": "e1", "message_id": "m1", "from_name": "arav", "room": "build",
        "content": f"@{ME} hello from the room", "message_type": "chat"}


class _Relay(BaseHTTPRequestHandler):
    def log_message(self, *_a: object) -> None:
        return

    def _json(self, body: object) -> None:
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self._json({"token": "t"} if self.path == "/stream/token" else {"ok": True})

    def do_GET(self) -> None:
        if not self.path.startswith("/stream/"):
            self._json({"messages": []})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            time.sleep(2.0)  # let the client finish initialize + tools/list
            self.wfile.write(f"event: message\ndata: {json.dumps(_MSG)}\n\n".encode())
            self.wfile.flush()
            while True:
                time.sleep(0.5)
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return


def test_module_entry_point_pushes_to_session(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Relay)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {k: v for k, v in os.environ.items() if not k.startswith("QUORUS_")}
    env.update(
        HOME=str(tmp_path), QUORUS_CONFIG_DIR=str(tmp_path / ".q"),
        QUORUS_RELAY_URL=f"http://127.0.0.1:{server.server_address[1]}",
        QUORUS_INSTANCE_NAME=ME, QUORUS_RELAY_SECRET="s",
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "quorus_mcp.server"], env=env, cwd=str(tmp_path),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    lines: list[str] = []
    threading.Thread(target=lambda: lines.extend(proc.stdout), daemon=True).start()

    def send(obj: dict) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"}}})
        time.sleep(0.5)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        deadline = time.time() + 15
        pushes: list[dict] = []
        while time.time() < deadline and not pushes:
            time.sleep(0.2)
            pushes = [json.loads(x) for x in list(lines)
                      if '"notifications/claude/channel"' in x]
        assert pushes, "no channel push reached the MCP client"
        assert pushes[0]["params"]["content"] == _MSG["content"]
        assert pushes[0]["params"]["meta"]["room"] == "build"
    finally:
        proc.kill()
        server.shutdown()
