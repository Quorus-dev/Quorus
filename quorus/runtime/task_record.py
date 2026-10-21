"""W4 task evidence. Append-only, owner/repo scoped; never a routing input.

The shared git directory lets separate daemon config directories contribute to the
same owner's record. No ask text, replies, credentials or command output is stored.
Readers never create files. Only daemon capture/settlement appends evidence.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

KINDS = ("test", "fix", "feature", "refactor", "docs", "config")
WORK_S, READ_S = 48 * 3600, 9 * 86400
MAX_BYTES = 32 * 1024 * 1024


def scope(relay: str, owner: str, repo: Path) -> str:
    return hashlib.sha256(f"{relay.rstrip('/')}|{owner}|{repo.resolve()}".encode()).hexdigest()


def path(repo: Path, key: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{64}", key):
        raise ValueError("invalid track scope")
    common = subprocess.check_output(
        ["git", "rev-parse", "--git-common-dir"], cwd=repo, timeout=5, stderr=subprocess.PIPE
    ).decode().strip()
    return (repo / common).resolve() / "quorus" / "tasks" / f"{key}.jsonl"


def _read(file: Path) -> list[dict]:
    try:
        if file.stat().st_size > MAX_BYTES:
            raise ValueError("task log needs archiving")
        raw = file.read_text()
    except FileNotFoundError:
        return []
    # Corrupt evidence is never silently counted as a successful task.
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if any(not isinstance(r, dict) for r in rows):
        raise ValueError("invalid task log")
    return rows


def read(repo: Path, key: str) -> list[dict]:
    return _read(path(repo, key))


@contextmanager
def _locked(file: Path):
    file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(file.with_suffix(".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _append(file: Path, row: dict) -> None:
    data = (json.dumps(row, sort_keys=True, allow_nan=False) + "\n").encode()
    fd = os.open(file, os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    start = os.fstat(fd).st_size
    try:
        if start + len(data) > MAX_BYTES:
            raise ValueError("task log needs archiving")
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            if n <= 0:
                raise OSError("task event not written")
            view = view[n:]
        os.fsync(fd)
    except BaseException:
        os.ftruncate(fd, start)
        raise
    finally:
        os.close(fd)
    directory = os.open(file.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def kind(text: str, *, allow_override: bool = True) -> str:
    overrides = set(re.findall(r"\bkind:(\w+)\b", text.lower()))
    if overrides and allow_override:
        valid = len(overrides) == 1 and overrides <= set(KINDS)
        return next(iter(overrides)) if valid else "unknown"
    text = re.sub(r"\bkind:\w+\b", "", text, flags=re.I)
    rules = {
        "fix": r"\b(fix|bug|crash|regression|repair)\b",
        "test": r"\b(tests?|pytest|coverage)\b|(?:^|\s)tests/",
        "feature": r"\b(add|build|implement|feature)\b",
        "refactor": r"\b(refactor|restructure|rename)\b",
        "docs": r"\b(docs?|readme|article|documentation)\b|\.md\b",
        "config": r"\b(config|configuration|settings|dependencies)\b|\.toml\b",
    }
    hits = [k for k, pattern in rules.items() if re.search(pattern, text, re.I)]
    return hits[0] if len(hits) == 1 else "unknown"


def append(repo: Path, key: str, row: dict) -> None:
    file = path(repo, key)
    with _locked(file):
        _read(file)  # refuse a partial/corrupt earlier append
        _append(file, row)


def claim(repo: Path, key: str, task: str, *, agent: str, vendor: str, model: str,
          version: str, text: str, now: float | None = None,
          allow_kind_override: bool = True) -> dict:
    at = time.time() if now is None else now
    file = path(repo, key)
    with _locked(file):
        rows = _read(file)
        old = next((r for r in rows if r.get("type") == "claim" and r.get("task") == task), None)
        if old:
            return old
        row = dict(type="claim", task=task, agent=agent, vendor=vendor, model=model,
                   version=version, kind=kind(text, allow_override=allow_kind_override),
                   claimed_at=at, deadline=at + WORK_S,
                   read_at=at + READ_S)
        _append(file, row)
        return row


def _events(rows: list[dict]) -> list[dict]:
    """A daemon crash still leaves an attempt, with unknown usage and duration."""
    ended = {r.get("attempt") for r in rows if r.get("type") == "attempt"}
    return rows + [{**r, "type": "attempt", "outcome": "errored", "wall_s": 0,
                    "tokens": None, "duration_unknown": True}
                   for r in rows if r.get("type") == "attempt_start"
                   and r.get("attempt") not in ended]


def report(repo: Path, main: str = "main", scope: str = "", now: float | None = None) -> list[dict]:
    from quorus.runtime.task_outcomes import evaluate

    at = time.time() if now is None else now
    rows = read(repo, scope)
    events = _events(rows)
    settled = {r["task"]: r["result"] for r in rows if r.get("type") == "settled"}
    return [{**r, **(settled.get(r["task"]) or evaluate(repo, main, r, events, at))}
            for r in rows if r.get("type") == "claim"]


def settle(repo: Path, key: str, main: str, now: float | None = None) -> None:
    from quorus.runtime.task_outcomes import evaluate

    at = time.time() if now is None else now
    file = path(repo, key)
    with _locked(file):
        rows = _read(file)
        done = {r["task"] for r in rows if r.get("type") == "settled"}
        for task in rows:
            if (task.get("type") == "claim" and task["task"] not in done
                    and at >= task["read_at"]):
                result = evaluate(repo, main, task, _events(rows), at)
                _append(file, {"type": "settled", "task": task["task"], "at": at,
                               "result": result})
