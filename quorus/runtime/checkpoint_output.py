"""Drain a checkpoint to completion while retaining only its bounded output tail."""

from __future__ import annotations

import os
import selectors
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from quorus.runtime.commit_evidence import kill_group


@dataclass(frozen=True)
class Capture:
    status: str
    code: int | None
    output: bytes
    truncated: bool


def capture(argv: list[str], cwd: Path, max_bytes: int, timeout: float,
            env: dict[str, str]) -> Capture:
    """Output volume never decides success. Timeout kills/reaps the entire group.

    Once the byte window rolls, discard its first (possibly bisected) line before
    returning anything for redaction. A single line larger than the cap yields no
    retained text, rather than an unrecognizable fragment of a credential.
    """
    deadline = time.monotonic() + timeout
    buf = bytearray()
    truncated = False
    status, code = "error", None
    try:
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=True)
    except OSError:
        return Capture(status, code, b"", False)
    assert proc.stdout is not None
    sel = selectors.DefaultSelector()
    try:
        sel.register(proc.stdout, selectors.EVENT_READ)
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise subprocess.TimeoutExpired(argv, timeout)
            if not sel.select(left):
                continue
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) > max_bytes:
                del buf[:-max_bytes]
                truncated = True
        code = proc.wait(timeout=max(0, deadline - time.monotonic()))
        status = "completed" if code >= 0 else "error"
        if status == "error":
            code = None
    except subprocess.TimeoutExpired:
        status = "timed_out"
    except OSError:
        status = "error"
    finally:
        sel.close()
        kill_group(proc)
        proc.stdout.close()
    if truncated:
        boundary = buf.find(b"\n")
        buf = buf[boundary + 1:] if boundary >= 0 else bytearray()
    return Capture(status, code, bytes(buf), truncated)
