"""Live failover proof: a rate-limited opencode agent hands its task to Claude."""
import json
import os
import secrets
import socket
import subprocess
import time
from pathlib import Path

import httpx

S = Path(__file__).resolve().parent / "failover-proof"
WK = Path.home() / "dev/weekend/Quorus"
PY = str(Path.home() / "dev/Quorus/.venv/bin/python")
ROOM, HUMAN = "failover-check", "arav"
A, B = "t-opencode", "t-claude"
BINDINGS = Path.home() / ".quorus" / "room-bindings.json"


def port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


secret, p = secrets.token_hex(16), port()
url, H = f"http://127.0.0.1:{p}", {"Authorization": f"Bearer {secret}"}
base = {k: v for k, v in os.environ.items()
        if not k.startswith(("QUORUS_", "REFLEXD_")) and k not in ("API_KEY",)}
base["PYTHONPATH"] = ":".join(str(WK / d) for d in
                              (".", "packages/cli", "packages/mcp", "packages/sdk", "packages/tui"))
procs = []
saved = BINDINGS.read_text()
try:
    data = json.loads(saved)
    data[ROOM] = {"path": str(S / "repo"), "mode": "default"}
    BINDINGS.write_text(json.dumps(data, indent=2))
    procs.append(subprocess.Popen([PY, "-m", "quorus.relay_cli"], env={
        **base, "RELAY_SECRET": secret, "PORT": str(p), "HOST": "127.0.0.1",
        "ALLOW_LEGACY_AUTH": "1", "MESSAGES_FILE": str(S / "relay.json"),
        "LOG_LEVEL": "WARNING"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    for _ in range(80):
        try:
            if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.25)
    httpx.post(f"{url}/rooms", json={"name": ROOM, "created_by": HUMAN}, headers=H)
    for who in (HUMAN, A, B):
        httpx.post(f"{url}/rooms/{ROOM}/join", json={"participant": who}, headers=H)
    for name, extra in ((A, {"PATH": f"{S / 'fakebin'}:{base['PATH']}"}),
                        (B, {"REFLEXD_MODEL": "claude-haiku-4-5-20251001"})):
        env = {**base, "RELAY_URL": url, "API_KEY": secret, "REFLEXD_PARTICIPANT": name,
               "REFLEXD_LEGACY_BEARER": "1", "REFLEXD_OWNER": HUMAN, "QUORUS_NOTIFY": "0",
               "PYTHONUNBUFFERED": "1", "REFLEXD_LOG_PATH": str(S / f"{name}.log"), **extra}
        procs.append(subprocess.Popen(
            [PY, str(WK / "scripts/reflexd.py"), "start", "--participant", name,
             "--relay-url", url], env=env, cwd=str(S),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    time.sleep(8)
    t0 = time.time()
    httpx.post(f"{url}/rooms/{ROOM}/messages", headers=H, json={
        "from_name": HUMAN,
        "content": f"@{A} what is the capital of France? Reply in one short line."})
    seen = 0
    while time.time() - t0 < 240:
        hist = httpx.get(f"{url}/rooms/{ROOM}/history", params={"limit": 50}, headers=H).json()
        for m in hist[seen:]:
            print(f"{time.time() - t0:5.0f}s  @{m['from_name']} [{m.get('message_type')}]: "
                  f"{(m.get('content') or '')[:260]!r}", flush=True)
        seen = len(hist)
        if any(m["from_name"] == B and m.get("message_type") != "status"
               and "paris" in (m.get("content") or "").lower() for m in hist):
            print(f"HANDOFF OK in {time.time() - t0:.0f}s")
            break
        time.sleep(2)
    else:
        print("NO HANDOFF within 240s")
finally:
    BINDINGS.write_text(saved)
    for pr in reversed(procs):
        pr.terminate()
    for pr in procs:
        try:
            pr.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pr.kill()
