"""Episode registration for Quorus's own launch paths (Codex's bounded host-tier design).

A test episode (tests/episodes) runs Quorus with two environment variables set: a run
nonce and a registration directory. A launch path that starts a long-lived child (the
`quorus relay` wrapper's relay_cli, a detached agent daemon) calls
:func:`register_spawned` with its own ``Popen``: the child's pid, start identity and the
nonce are registered under a fresh id, and the launch path waits for the episode's ack
before it goes on. Without an ack the child is killed and the launch fails, so nothing
Quorus starts can outlive an episode untracked.

Outside an episode (either variable unset) :func:`register_spawned` returns False at
once: no file is read or written and nothing waits. Standard library only, so a normal
install never imports test code.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Proc:
    pid: int
    pgid: int
    started: str  # `ps -o lstart=`: with the pid, identifies one process for its lifetime
    command: str


def _ps(pid: int) -> tuple[str, int, str] | None:
    """(start time, pgid, command) of a live process, or None if it's gone."""
    out = subprocess.run(["ps", "-o", "lstart=", "-o", "pgid=", "-o", "command=", "-p", str(pid)],
                         capture_output=True, text=True, check=False, timeout=10)
    line = out.stdout.strip()
    if out.returncode != 0 or not line:
        return None
    # lstart is a fixed 24-char date ("Tue Oct 20 04:25:24 2026"), then pgid, then command.
    started, rest = line[:24], line[24:].split(None, 1)
    if not rest:
        return None
    return started, int(rest[0]), rest[1] if len(rest) > 1 else ""


NONCE_VAR = "QUORUS_EPISODE_NONCE"
REGISTRY_VAR = "QUORUS_EPISODE_REGISTRY"
ACK_TIMEOUT_VAR = "QUORUS_EPISODE_ACK_S"


def in_episode() -> bool:
    """True only inside a test episode (both variables set). Launch paths then keep the
    episode's PATH as is instead of adding the host's tool directories, so a vendor CLI
    resolves to the episode's stub or to nothing, never to a real install (Codex on
    ada406a3)."""
    return bool(os.environ.get(REGISTRY_VAR) and os.environ.get(NONCE_VAR))


def ack_body(reg_id: str, nonce: str, p: Proc) -> str:
    """What an ack must say, exactly: this registration, this run, this process."""
    return json.dumps({"reg": reg_id, "nonce": nonce, "pid": p.pid, "pgid": p.pgid,
                       "started": p.started, "command": p.command}, sort_keys=True)


def register_spawned(child: subprocess.Popen | int) -> bool:
    """Called by a controlled spawner right after it starts *child*: registers the child
    under a fresh registration id with its pid, start identity and the run nonce, then
    waits for an ack that repeats all of them before the spawner may go on (and exit).
    Without that ack the child is killed and this raises, so nothing a controlled spawner
    starts outlives the episode untracked. Outside an episode (no registry in the
    environment) it does nothing and returns False.

    Pass the ``Popen`` when you have it: killing through it can never hit a reused pid,
    because Popen won't signal a child it has already reaped. With a bare pid, the
    cleanup signals only after re-checking the process is still the one registered
    (Codex on b8eeca17)."""
    registry, nonce = os.environ.get(REGISTRY_VAR), os.environ.get(NONCE_VAR)
    if not registry or not nonce:
        return False
    pid = child.pid if isinstance(child, subprocess.Popen) else int(child)
    folder, reg_id = Path(registry), uuid.uuid4().hex
    entry, ack = folder / f"{reg_id}.json", folder / f"{reg_id}.ack"
    snap = None
    try:
        for _ in range(50):
            snap = snapshot(pid)
            if snap is not None and snap.command:
                break
            time.sleep(0.01)
        if snap is None:
            raise RuntimeError(f"child {pid} exited before it could be registered")
        tmp = folder / f"{reg_id}.json.tmp"
        tmp.write_text(json.dumps({"reg": reg_id, "pid": snap.pid, "pgid": snap.pgid,
                                   "started": snap.started, "command": snap.command,
                                   "nonce": nonce}), encoding="utf-8")
        tmp.replace(entry)
        want = ack_body(reg_id, nonce, snap)
        deadline = time.monotonic() + float(os.environ.get(ACK_TIMEOUT_VAR, "10"))
        while time.monotonic() < deadline:
            try:
                if ack.read_text(encoding="utf-8") == want:
                    return True
            except OSError:
                pass
            time.sleep(0.005)
        raise RuntimeError(f"the episode never acknowledged child {pid}")
    except BaseException:
        _kill_unacknowledged(child, snap)
        raise


def _kill_unacknowledged(child: subprocess.Popen | int, snap: Proc | None) -> None:
    if isinstance(child, subprocess.Popen):
        try:
            if snap is not None and snap.pgid == child.pid and same_process(snap):
                try:  # a setsid child leads its own group: take the group with it
                    os.killpg(snap.pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        except Exception:  # never let cleanup's own inspection hide the real error
            pass
        finally:  # the owned child dies even if that inspection raised (Codex on f2af046c)
            child.kill()  # a no-op once reaped, so never a reused pid
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        return
    if snap is None or not same_process(snap):
        return  # unverifiable or no longer that process: never guess
    try:
        if snap.pgid == snap.pid:
            os.killpg(snap.pgid, signal.SIGKILL)
        else:
            os.kill(snap.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def snapshot(pid: int) -> Proc | None:
    info = _ps(pid)
    return None if info is None else Proc(pid, info[1], info[0], info[2])


def same_process(p: Proc) -> bool:
    """Still the process recorded: same start time and command, not a reused pid."""
    info = _ps(p.pid)
    return info is not None and info[0] == p.started and info[2] == p.command
