"""Bounded checkpoint at an immutable commit; no handoff or writable wake yet."""

from __future__ import annotations

import hashlib
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from quorus.observability.redaction import redact_secrets
from quorus.runtime import commit_evidence as evidence
from quorus.runtime.check_config import CheckConfig
from quorus.runtime.checkpoint_output import capture


def _git(root: Path, *args: str) -> bytes:
    argv = ["git", "--no-replace-objects"]
    for item in evidence._SAFE_CONFIG:
        argv += ["-c", item]
    code, out, cut = evidence.collect([*argv, *args], root, 1048576)
    if code != 0 or cut:
        raise ValueError("checkpoint git validation failed")
    return out


def _state(root: Path):
    return (_git(root, "rev-parse", "HEAD").decode().strip(),
            _git(root, "rev-parse", "HEAD^{tree}").decode().strip(),
            _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all"))


def local_paths(text: str, root: Path, home: str | None) -> str:
    """Machine paths out of the check output, before it is trimmed and hashed.

    The worktree becomes ``.`` and the home directory ``~``, the same way on the
    sender and the receiver: tracebacks print absolute paths, and the tail is
    posted in receipts (dogfood plan §1, Claude on rev 1). The worktree goes
    first because it usually sits under home."""
    homes = {home, str(Path(home).resolve())} if home else set()
    for paths, short in (({str(root), str(root.resolve())}, "."), (homes, "~")):
        for path in sorted(paths, key=len, reverse=True):
            path = path.rstrip("/")
            if path:  # whole path components only: /Users/al is not /Users/alice
                text = re.sub(re.escape(path) + r"(?![^/\s:'\"),\]])", short, text)
    return text


def run_checkpoint(root: Path, head: str, config: CheckConfig | None, *,
                   env: dict[str, str], known_secrets: tuple[str, ...] = ()) -> dict:
    """Caller supplies the stripped wake environment, never the ambient daemon env.

    On changed HEAD/tracked state leave evidence intact for owner recovery. Never
    guess a reset target or delete paths after an arbitrary check changed git state.
    Such a run is unusable for proof. Runtime wiring belongs to the next slice.
    """
    result = {"status": "none", "head": head}
    if config is None:
        return result
    result.update(config.record(), config_id=config.config_id, exit=None, truncated=False,
                  ran_at=datetime.now(timezone.utc).isoformat(), tail="")
    if not evidence.FULL_SHA_RE.fullmatch(head):
        return {**result, "status": "error"}
    started = time.monotonic()
    try:
        before = _state(root)
        result["tree"] = before[1]
        if before[0] != head or before[2]:
            return {**result, "status": "skipped"}
        cwd = (root / config.cwd).resolve()
        if not cwd.is_relative_to(root.resolve()) or not cwd.is_dir():
            return {**result, "status": "error"}
        output = capture(list(config.argv), cwd, config.max_output_bytes, config.timeout_s, env)
        status = output.status
        if status == "completed":
            status = "passing" if output.code == 0 else "ran"
        result.update(exit=output.code, truncated=output.truncated)
        # Capture removed any bisected first line. Redact before the receipt cap.
        redacted = local_paths(
            redact_secrets(output.output.decode("utf-8", "replace"), known_secrets),
            root, env.get("HOME"))
        tail = "\n".join(redacted.splitlines()[-40:])
        tail = tail.encode()[-1500:].decode("utf-8", "ignore")
        result.update(tail=tail, tail_sha256=hashlib.sha256(tail.encode()).hexdigest())
        after = _state(root)
        if after != before:
            status = "modified_tree"
        result["status"] = status
    except (OSError, ValueError, subprocess.SubprocessError):
        result["status"] = "error"
    result["wall_s"] = round(time.monotonic() - started, 6)
    return result
