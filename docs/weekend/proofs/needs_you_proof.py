"""Live proof on the release code: every vendor's reply that asks its owner
fires a "needs you" desktop alert. Harness CLIs are stubs that answer in each
vendor's real output format; everything else (relay, daemon, parsing, alert)
is the shipping code from the release worktree."""
import json
import os
import re
import secrets
import socket
import subprocess
import time
from pathlib import Path

import httpx

S = Path(__file__).resolve().parent
REL = S.parent / "release-wt"
PY = str(Path.home() / "dev/Quorus/.venv/bin/python")
ROOM, OWNER = "needs-you-proof", "arav"
AGENTS = ["arav-claude", "arav-codex", "arav-gemini", "arav-opencode"]
BINDINGS = Path.home() / ".quorus" / "room-bindings.json"

with socket.socket() as so:
    so.bind(("127.0.0.1", 0))
    port = so.getsockname()[1]
secret = secrets.token_hex(16)
url, H = f"http://127.0.0.1:{port}", {"Authorization": f"Bearer {secret}"}
base = {k: v for k, v in os.environ.items() if not k.startswith(("QUORUS_", "REFLEXD_"))
        and k != "API_KEY"}
base["PYTHONPATH"] = ":".join(str(REL / d) for d in
                              (".", "packages/cli", "packages/mcp", "packages/sdk", "packages/tui"))
base["PATH"] = f"{S / 'bin'}:{base['PATH']}"
procs, saved = [], BINDINGS.read_text()
try:
    data = json.loads(saved)
    data[ROOM] = {"path": str(S / "repo"), "mode": "default"}
    BINDINGS.write_text(json.dumps(data, indent=2))
    procs.append(subprocess.Popen([PY, "-m", "quorus.relay_cli"], env={
        **base, "RELAY_SECRET": secret, "PORT": str(port), "HOST": "127.0.0.1",
        "ALLOW_LEGACY_AUTH": "1", "MESSAGES_FILE": str(S / "relay.json"), "LOG_LEVEL": "WARNING"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    for _ in range(80):
        try:
            if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.25)
    httpx.post(f"{url}/rooms", json={"name": ROOM, "created_by": OWNER}, headers=H)
    for who in (OWNER, *AGENTS):
        httpx.post(f"{url}/rooms/{ROOM}/join", json={"participant": who}, headers=H)
    for name in AGENTS:
        env = {**base, "RELAY_URL": url, "API_KEY": secret, "REFLEXD_PARTICIPANT": name,
               "REFLEXD_LEGACY_BEARER": "1", "REFLEXD_OWNER": OWNER, "QUORUS_NOTIFY": "1",
               "PYTHONUNBUFFERED": "1", "REFLEXD_LOG_PATH": str(S / f"{name}.log")}
        procs.append(subprocess.Popen([PY, str(REL / "scripts/reflexd.py"), "start",
                                       "--participant", name, "--relay-url", url],
                                      env=env, cwd=str(S), stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL))
    time.sleep(8)
    httpx.post(f"{url}/rooms/{ROOM}/messages", headers=H, json={
        "from_name": OWNER, "content": "Status check: is the release ready?"})
    deadline, seen = time.time() + 90, {}
    while time.time() < deadline and len(seen) < len(AGENTS):
        for name in AGENTS:
            log = (S / f"{name}.log")
            text = log.read_text() if log.exists() else ""
            m = re.search(r"owner alert room=\S+: (.+)", text)
            if m and name not in seen:
                seen[name] = m.group(1)
        time.sleep(2)
    hist = httpx.get(f"{url}/rooms/{ROOM}/history", params={"limit": 50}, headers=H).json()
    for name in AGENTS:
        reply = next((m["content"] for m in hist if m["from_name"] == name), None)
        print(f"{name:15} reply={reply!r:52.52} alert={seen.get(name, 'NONE')}")
finally:
    BINDINGS.write_text(saved)
    for p in reversed(procs):
        p.terminate()
    for p in procs:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
