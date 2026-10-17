"""Live failover proof, fully isolated: no ~/.quorus, no vendor quota.

A real relay (shared secret only) and two real wake daemons from this checkout.
@t-opencode's "opencode" is a fake that always reports a rate limit; @t-claude
runs reflexd's stub harness (no API spend). A human asks @t-opencode; the proof
passes when @t-opencode posts a typed vendor-failover /defer to @t-claude and
@t-claude then answers in the room. Then @t-opencode's daemon is restarted and
asked again: it must reuse its saved participant key (no 409, no shared-secret
fallback in its log) and hand off a second time.
Every daemon gets a temporary HOME, so runtime state, room bindings and keys
stay in a temp dir.

    python docs/weekend/proofs/failover_proof_isolated.py
"""

import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[3]
ROOM, HUMAN, A, B = "failover-check", "arav", "t-opencode", "t-claude"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="failover-proof-"))
    secret, port = secrets.token_hex(16), free_port()
    url, auth = f"http://127.0.0.1:{port}", {"Authorization": f"Bearer {secret}"}
    fakebin = tmp / "fakebin"
    fakebin.mkdir()
    fake = fakebin / "opencode"
    fake.write_text("#!/bin/sh\necho 'Error: rate limit reached for this model' >&2\nexit 1\n")
    fake.chmod(0o755)
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("QUORUS_", "REFLEXD_", "MURMUR_"))
            and k not in ("API_KEY", "RELAY_URL", "RELAY_SECRET", "INSTANCE_NAME", "JWT_SECRET")}
    dirs = (".", "packages/cli", "packages/mcp", "packages/sdk", "packages/tui")
    base["PYTHONPATH"] = os.pathsep.join(str(REPO / d) for d in dirs)
    procs: list[subprocess.Popen] = []
    try:
        (tmp / "relay").mkdir()
        procs.append(subprocess.Popen(
            [sys.executable, "-c", "from quorus.relay import main; main()"],
            env={**base, "HOME": str(tmp / "relay"), "RELAY_SECRET": secret, "PORT": str(port),
                 "HOST": "127.0.0.1", "QUORUS_HISTORY_DB": str(tmp / "relay" / "history.db"),
                 "LOG_LEVEL": "WARNING"},
            cwd=tmp / "relay", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        for _ in range(80):
            try:
                if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.25)
        httpx.post(f"{url}/rooms", json={"name": ROOM, "created_by": HUMAN}, headers=auth)
        for who in (HUMAN, A, B):
            httpx.post(f"{url}/rooms/{ROOM}/join", json={"participant": who}, headers=auth)
        daemons: dict[str, subprocess.Popen] = {}

        def start(name: str) -> None:
            extra = ({"PATH": f"{fakebin}:{base['PATH']}"} if name == A
                     else {"REFLEXD_STUB_REPLY": "1"})
            home = tmp / name
            home.mkdir(exist_ok=True)
            env = {**base, "HOME": str(home), "RELAY_URL": url, "API_KEY": secret,
                   "REFLEXD_PARTICIPANT": name, "REFLEXD_LEGACY_BEARER": "1",
                   "REFLEXD_OWNER": HUMAN, "QUORUS_NOTIFY": "0", "PYTHONUNBUFFERED": "1",
                   "REFLEXD_LOG_PATH": str(tmp / f"{name}.log"), **extra}
            daemons[name] = subprocess.Popen(
                [sys.executable, str(REPO / "scripts/reflexd.py"), "start", "--participant",
                 name, "--relay-url", url], env=env, cwd=home,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            procs.append(daemons[name])

        def ask_and_wait(question: str, label: str) -> bool:
            before = len(httpx.get(f"{url}/rooms/{ROOM}/history", params={"limit": 200},
                                   headers=auth).json())
            t0 = time.time()
            httpx.post(f"{url}/rooms/{ROOM}/messages", headers=auth,
                       json={"from_name": HUMAN, "content": f"@{A} {question}"})
            seen = before
            while time.time() - t0 < 120:
                hist = httpx.get(f"{url}/rooms/{ROOM}/history", params={"limit": 200},
                                 headers=auth).json()
                for m in hist[seen:]:
                    print(f"{time.time() - t0:5.0f}s  @{m['from_name']} "
                          f"[{m.get('message_type')}]: {(m.get('content') or '')[:200]!r}",
                          flush=True)
                seen = len(hist)
                new = hist[before:]
                defer = next((i for i, m in enumerate(new) if m["from_name"] == A
                              and m.get("message_type") == "social" and '"failover"' in
                              (m.get("content") or "") and f'"to": "{B}"' in m["content"]), None)
                if defer is not None and any(m["from_name"] == B and m.get("message_type")
                                             == "chat" for m in new[defer + 1:]):
                    print(f"{label}: HANDOFF OK in {time.time() - t0:.0f}s")
                    return True
                time.sleep(2)
            print(f"{label}: NO HANDOFF within 120s; daemon logs: {tmp}")
            return False

        start(A)
        start(B)
        time.sleep(8)
        if not ask_and_wait("what is the capital of France? Reply in one short line.", "run 1"):
            return 1
        # Restart the failing agent: it must reuse its saved participant key
        # (no second claim, no 409, no shared-secret fallback) and hand off again.
        daemons[A].terminate()
        daemons[A].wait(timeout=10)
        start(A)
        time.sleep(8)
        if not ask_and_wait("and of Italy? Reply in one short line.", "run 2 (after restart)"):
            return 1
        log = (tmp / f"{A}.log").read_text()
        bad = [line for line in log.splitlines() if "409" in line or "shared secret" in line]
        print("restart log check:", "clean (no 409, no shared-secret fallback)" if not bad
              else bad)
        return 0 if not bad else 1
    finally:
        for pr in reversed(procs):
            pr.terminate()
        for pr in procs:
            try:
                pr.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pr.kill()


if __name__ == "__main__":
    sys.exit(main())
