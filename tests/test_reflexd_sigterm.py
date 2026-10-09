"""reflexd must exit promptly on SIGTERM while idle on an SSE stream.

Regression (2026-10-08): the stop event was only checked when an SSE event
arrived; keepalive comments are skipped by the parser, so an idle daemon
ignored SIGTERM indefinitely and the relay hung on shutdown waiting for its
open stream. ``quorus reflexd stop`` and launchd restarts both depend on this.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


class _FakeRelay(BaseHTTPRequestHandler):
    """Just enough relay for reflexd to connect and sit idle on SSE."""

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
        if self.path.startswith("/messages/"):
            self._json({"messages": []})
            return
        if self.path.startswith("/stream/"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                self.wfile.write(b"event: connected\ndata: {}\n\n")
                self.wfile.flush()
                while True:  # keepalives only: the case that used to hang
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    time.sleep(0.5)
            except (BrokenPipeError, ConnectionResetError):
                return
        self._json({})


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_sigterm_stops_idle_daemon(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeRelay)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    env = {
        **os.environ, "HOME": str(tmp_path), "QUORUS_CONFIG_DIR": str(tmp_path / ".q"),
        "RELAY_URL": url, "API_KEY": "k", "REFLEXD_PARTICIPANT": "t-claude",
        "REFLEXD_LEGACY_BEARER": "1", "REFLEXD_HEARTBEAT_S": "60",
    }
    log = tmp_path / "out.log"
    with log.open("w") as out:
        proc = subprocess.Popen(
            [sys.executable, str(_REPO_ROOT / "scripts" / "reflexd.py"), "start",
             "--debug", "--participant", "t-claude", "--relay-url", url],
            env=env, stdout=out, stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.time() + 20
        while time.time() < deadline and "sse connected" not in log.read_text():
            time.sleep(0.2)
        assert "sse connected" in log.read_text(), log.read_text()[-800:]
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=5) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
        server.shutdown()
