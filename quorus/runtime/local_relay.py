"""Run Quorus on this computer: a local relay the user never has to babysit.

There is no public relay, so a consumer's first `quorus` must leave them with
a working relay on their own machine. Used by the first-run wizard and by the
hub when the saved local relay isn't running (e.g. after a reboot).
"""

from __future__ import annotations

import os
import secrets as _secrets
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from quorus.config import resolve_config_dir

LOCAL_HOSTS = ("127.0.0.1", "localhost", "0.0.0.0", "::1")


def is_local(relay_url: str) -> bool:
    return (urlparse(relay_url).hostname or "") in LOCAL_HOSTS


def new_secret() -> str:
    return _secrets.token_hex(16)


def _healthy(relay_url: str, timeout: float = 2.0) -> bool:
    try:
        return httpx.get(f"{relay_url.rstrip('/')}/health", timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


def _accepts(relay_url: str, secret: str) -> bool:
    try:
        r = httpx.get(f"{relay_url.rstrip('/')}/rooms", timeout=5.0,
                      headers={"Authorization": f"Bearer {secret}"})
    except httpx.HTTPError:
        return False
    return r.status_code not in (401, 403)


def ensure_local_relay(relay_url: str, secret: str) -> tuple[bool, str]:
    """Make sure a relay that accepts *secret* runs at local *relay_url*.

    Returns ``(ok, message)``. Starts a detached relay (survives this
    terminal; state + log in the config dir) when nothing is listening.
    Never kills or replaces a relay someone else started.
    """
    if not is_local(relay_url):
        return _healthy(relay_url), ""
    if _healthy(relay_url):
        if _accepts(relay_url, secret):
            return True, "local relay is running"
        return False, ("a relay is already running on this computer with a different "
                       "secret — stop it, or run `quorus init <name> --secret -` and "
                       "paste its secret")
    parsed = urlparse(relay_url)
    cfg = resolve_config_dir()
    cfg.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "RELAY_SECRET": secret, "PORT": str(parsed.port or 8080),
           "HOST": "127.0.0.1", "ALLOW_LEGACY_AUTH": "1",
           "MESSAGES_FILE": str(cfg / "relay-state.json"), "LOG_LEVEL": "WARNING"}
    log = (cfg / "relay.log").open("ab")
    try:
        proc = subprocess.Popen([sys.executable, "-m", "quorus.relay_cli"], env=env,
                                stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                start_new_session=True, cwd=str(Path.home()))
    except OSError as exc:
        return False, f"couldn't start the local relay ({exc}); run `quorus relay` yourself"
    for _ in range(40):
        if _healthy(relay_url, 0.5):
            if proc.poll() is not None:
                # Ours exited (the port was taken) and another relay answers:
                # it isn't ours to record, and maybe not ours to use.
                ok = _accepts(relay_url, secret)
                return ok, ("local relay is running" if ok else
                            "a relay is already running on this computer with a different "
                            "secret — stop it, or run `quorus init <name> --secret -` and "
                            "paste its secret")
            _record(cfg, proc.pid, parsed.port or 8080)
            return True, "started your local relay"
        time.sleep(0.25)
    # Never answered: stop the process we spawned (by its own handle) rather than
    # leave an untracked relay running (codex, review of e409ee1a).
    _stop_spawned(proc)
    return False, f"the local relay didn't start — see {cfg / 'relay.log'}"


def _stop_spawned(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


RELAY_ARGV_TAIL = " -m quorus.relay_cli"


def _record(cfg: Path, pid: int, port: int) -> None:
    """So `quorus relay --replace` can stop exactly this relay, and nothing else:
    its pid, start time and command line, read once it answers (so after exec)."""
    import json

    (cfg / "relay.pid").write_text(json.dumps({
        "pid": pid, "port": port, "started": _started(pid), "command": _ps(pid, "command")}))


def stop_started_relay(port: int, timeout: float = 15.0) -> bool:
    """Stop the detached relay that `quorus` started on *port*, and only that one.

    The pid file names the process and its start time. It is signalled only if,
    right now, that pid's command line ends with exactly ``-m quorus.relay_cli``
    and it started at the recorded time, so a reused pid or an unrelated process
    that merely mentions quorus.relay_cli is never touched (codex, review of
    857a5271). Returns True once nothing answers on *port* and the process is gone.

    Not answering isn't enough: uvicorn stops accepting at once, but saves the
    relay's state only after its open streams close (up to 5 s). A replacement
    started in between loaded the old file, and the first run's rooms were lost
    (`quorus relay --host 0.0.0.0 --replace`, the founding scenario test)."""
    import json
    import signal

    pid_file = resolve_config_dir() / "relay.pid"
    try:
        rec = json.loads(pid_file.read_text())
        pid, started_port = int(rec["pid"]), int(rec["port"])
        started, command = str(rec["started"]), str(rec["command"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if started_port != port or not _is_our_relay(pid, started, command):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # A zombie (exited, not yet reaped by the hub that spawned it) no longer
        # shows the recorded command line, so it counts as gone.
        if not _healthy(url, 0.3) and not _is_our_relay(pid, started, command):
            pid_file.unlink(missing_ok=True)
            return True
        time.sleep(0.2)
    return False


def _ps(pid: int, field: str) -> str:
    try:
        return subprocess.run(["ps", "-o", f"{field}=", "-p", str(pid)], capture_output=True,
                              text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _started(pid: int) -> str:
    return _ps(pid, "lstart")


def _is_our_relay(pid: int, started: str, command: str) -> bool:
    """*pid* is still the relay we spawned: the command line it had when we spawned
    it, compared whole (not parsed, so `python3 -c '...' -m quorus.relay_cli`
    can't pass: codex, review of 56f6b852), and the same start time."""
    if not (started and command.endswith(RELAY_ARGV_TAIL)):
        return False
    exe = command[: -len(RELAY_ARGV_TAIL)]
    if " " in exe.strip() and not os.path.exists(exe):  # one interpreter, nothing more
        return False
    if not os.path.basename(exe).lower().startswith("python"):
        return False
    return _ps(pid, "command") == command and _started(pid) == started
