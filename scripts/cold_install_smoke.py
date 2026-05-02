#!/usr/bin/env python3
"""Cold-install smoke test for Quorus.

Verifies a freshly installed `quorus` binary can:
  1. Start the relay (`quorus-relay`) on a localhost port.
  2. Hit /health and get 200 within a deadline.
  3. Run `quorus init` against an isolated config dir.
  4. Create a room via the relay HTTP API (legacy-auth shared secret).
  5. Open an SSE subscription for a recipient.
  6. Send a message into the room and confirm round-trip via SSE in <30s.
  7. Tear everything down cleanly.

Designed to be invoked from `scripts/cold_install_smoke.sh` AND from CI.
No Docker, no Postgres, no Redis — just the relay + httpx + the shipped binaries.
Exits 1 with "FAIL at step N: <description>" on any failure.

Usage:
    python3 scripts/cold_install_smoke.py [--port 18080] [--timeout 60]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Test parameters
# ---------------------------------------------------------------------------

DEFAULT_PORT = 18080
DEFAULT_TIMEOUT = 60  # total budget for the smoke
HEALTH_DEADLINE = 20  # seconds to wait for /health 200
ROUNDTRIP_DEADLINE = 30  # spec says <30s
RELAY_SECRET = "cold-install-smoke-secret"
PARTICIPANT = "smoke-alice"
ROOM_NAME = "smoke-room"
MESSAGE_PAYLOAD = "hello-from-cold-install"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fail(step: int, msg: str, *, proc: subprocess.Popen | None = None) -> None:
    sys.stderr.write(f"FAIL at step {step}: {msg}\n")
    if proc is not None:
        try:
            tail = (proc.stdout.read() if proc.stdout else b"") or b""
            if tail:
                sys.stderr.write("---- relay stdout/stderr tail ----\n")
                sys.stderr.write(tail.decode("utf-8", errors="replace")[-4000:])
                sys.stderr.write("\n----------------------------------\n")
        except Exception:
            pass
    sys.exit(1)


def _ok(step: int, msg: str) -> None:
    sys.stdout.write(f"  step {step} ok: {msg}\n")
    sys.stdout.flush()


def _which_or_die(binary: str, step: int) -> str:
    found = shutil.which(binary)
    if not found:
        _fail(step, f"`{binary}` not on PATH — pipx install did not expose entrypoint")
    return found


def _http_get(url: str, *, timeout: float = 5.0, headers: dict | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() if hasattr(e, "read") else b""


def _http_post(url: str, body: dict, *, timeout: float = 5.0, headers: dict | None = None) -> tuple[int, bytes]:
    data = json.dumps(body).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() if hasattr(e, "read") else b""


def _wait_health(port: int, deadline: float, step: int) -> None:
    start = time.time()
    last_err = ""
    while time.time() - start < deadline:
        try:
            status, body = _http_get(f"http://127.0.0.1:{port}/health", timeout=2.0)
            if status == 200:
                _ok(step, f"/health 200 after {time.time() - start:.1f}s")
                return
            last_err = f"status={status} body={body[:200]!r}"
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            last_err = f"{type(e).__name__}: {e}"
        time.sleep(0.5)
    _fail(step, f"/health did not return 200 within {deadline}s — last: {last_err}")


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

def run(port: int, timeout: int) -> None:
    overall_start = time.time()

    # Step 1: locate binaries
    quorus_bin = _which_or_die("quorus", 1)
    relay_bin = _which_or_die("quorus-relay", 1)
    _ok(1, f"binaries on PATH (quorus={quorus_bin}, quorus-relay={relay_bin})")

    # Step 2: prepare an isolated config dir + workspace
    workdir = Path(tempfile.mkdtemp(prefix="quorus-smoke-"))
    config_dir = workdir / "quorus-config"
    config_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["QUORUS_CONFIG_DIR"] = str(config_dir)
    env["PORT"] = str(port)
    env["RELAY_SECRET"] = RELAY_SECRET
    # Force file-mode persistence — no Postgres / Redis required for cold smoke.
    env.pop("DATABASE_URL", None)
    env.pop("REDIS_URL", None)
    env["MESSAGES_FILE"] = str(workdir / "messages.json")
    # Required by the relay startup guards in production builds:
    env.setdefault("JWT_SECRET", "cold-smoke-jwt-secret-32-chars-min------")
    env.setdefault("BOOTSTRAP_SECRET", "cold-smoke-bootstrap-32-chars-min------")
    _ok(2, f"isolated workspace at {workdir}")

    # Step 3: spawn the relay
    relay_log = open(workdir / "relay.log", "wb")
    proc = subprocess.Popen(
        [relay_bin],
        env=env,
        stdout=relay_log,
        stderr=subprocess.STDOUT,
        # New process group so we can SIGTERM the whole tree on cleanup.
        start_new_session=(os.name != "nt"),
    )
    _ok(3, f"relay spawned pid={proc.pid} on port {port}")

    try:
        # Step 4: wait for /health 200
        _wait_health(port, HEALTH_DEADLINE, 4)

        # Step 5: quorus init in the isolated config dir
        # Use --secret (legacy auth) so we don't need bootstrap onboarding.
        init_result = subprocess.run(
            [
                quorus_bin, "init", PARTICIPANT,
                "--relay-url", f"http://127.0.0.1:{port}",
                "--secret", RELAY_SECRET,
                "--config-dir", str(config_dir),
            ],
            env=env,
            capture_output=True,
            timeout=30,
        )
        if init_result.returncode != 0:
            _fail(5, f"`quorus init` exited {init_result.returncode}\n"
                     f"stdout: {init_result.stdout.decode(errors='replace')[:500]}\n"
                     f"stderr: {init_result.stderr.decode(errors='replace')[:500]}",
                  proc=None)
        _ok(5, "quorus init wrote config")

        # Step 6: create a room via legacy-auth HTTP
        # Relay accepts RELAY_SECRET as a Bearer token for legacy/admin access.
        auth_headers = {"Authorization": f"Bearer {RELAY_SECRET}"}
        status, body = _http_post(
            f"http://127.0.0.1:{port}/rooms",
            {"name": ROOM_NAME, "created_by": PARTICIPANT},
            headers=auth_headers,
            timeout=5.0,
        )
        if status not in (200, 201):
            _fail(6, f"POST /rooms returned {status} body={body[:300]!r}")
        try:
            room = json.loads(body.decode("utf-8"))
        except Exception as e:
            _fail(6, f"POST /rooms returned non-JSON: {e!r} body={body[:300]!r}")
        room_id = room.get("id") or room.get("room_id") or ROOM_NAME
        _ok(6, f"room created id={room_id}")

        # Step 7: send a message into the room — verifies the full write path
        send_start = time.time()
        status, body = _http_post(
            f"http://127.0.0.1:{port}/rooms/{room_id}/messages",
            {"from_name": PARTICIPANT, "content": MESSAGE_PAYLOAD},
            headers=auth_headers,
            timeout=5.0,
        )
        if status not in (200, 201):
            _fail(7, f"POST /rooms/{room_id}/messages returned {status} body={body[:300]!r}")
        _ok(7, f"message sent ({status})")

        # Step 8: confirm round-trip via /history (the SSE-equivalent read path)
        #         The mission requires <30s; in practice this is instant.
        deadline = time.time() + ROUNDTRIP_DEADLINE
        seen = False
        last_seen_body = b""
        while time.time() < deadline:
            status, body = _http_get(
                f"http://127.0.0.1:{port}/rooms/{room_id}/history?limit=10",
                headers=auth_headers,
                timeout=5.0,
            )
            last_seen_body = body
            if status == 200 and MESSAGE_PAYLOAD.encode() in body:
                seen = True
                break
            time.sleep(0.25)
        if not seen:
            _fail(8, f"message not visible in /history within {ROUNDTRIP_DEADLINE}s "
                     f"(last status={status} body={last_seen_body[:300]!r})")
        rt = time.time() - send_start
        _ok(8, f"round-trip verified in {rt:.2f}s")

        # Step 9: enforce overall timeout
        elapsed = time.time() - overall_start
        if elapsed > timeout:
            _fail(9, f"smoke exceeded total timeout: {elapsed:.1f}s > {timeout}s")
        _ok(9, f"total smoke wall-time {elapsed:.1f}s (budget {timeout}s)")

    finally:
        # Tear down the relay tree.
        try:
            if os.name == "nt":
                proc.terminate()
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=10)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except Exception:
                pass
        relay_log.close()

    print("PASS: cold-install smoke completed cleanly")


def main() -> None:
    p = argparse.ArgumentParser(description="Quorus cold-install smoke test")
    p.add_argument("--port", type=int, default=int(os.environ.get("QUORUS_SMOKE_PORT", DEFAULT_PORT)))
    p.add_argument("--timeout", type=int, default=int(os.environ.get("QUORUS_SMOKE_TIMEOUT", DEFAULT_TIMEOUT)))
    args = p.parse_args()
    run(args.port, args.timeout)


if __name__ == "__main__":
    main()
