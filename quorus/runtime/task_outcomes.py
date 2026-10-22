"""Fixed-horizon task evidence, with no assignment or routing decisions.

Precedence: pending before read_at; escalation belongs to the original assignee;
then published work is reverted, unknown, or kept. Without published work the
last attempt determines quota/failure, otherwise abandonment. Rescue attempts
contribute usage but never create another task. Callers persist settled reads.

Git observations exclude commits newer than read_at, even on a late read. A
bounded/incomplete history or any git error is unknown, never presumed kept.
"""

from __future__ import annotations

import math
import re
import subprocess
import time
from contextvars import ContextVar
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{40}$")
REVERT = re.compile(r"(?:This reverts commit|Reverts?:)\s+([0-9a-f]{7,40})", re.I)
MAX_COMMITS = 2000
MAX_BYTES = 4 * 1024 * 1024
_DEADLINE = ContextVar("task_evidence_deadline", default=float("inf"))
FAILED = {"errored", "error", "timed_out", "failed", "cannot", "could_not"}
QUOTA = {"quota", "vendor_limit", "rate_limited"}


class ObservationError(Exception):
    """Evidence could not be read completely."""


def _git(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    left = _DEADLINE.get() - time.monotonic()
    if left <= 0:
        raise ObservationError("task evidence budget exhausted")
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(repo), *args],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=min(10, left),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ObservationError("git unavailable") from exc
    if result.returncode or len(result.stdout) > MAX_BYTES:
        raise ObservationError("git evidence unavailable or exceeds bound")
    return result.stdout


def _number(value: object) -> bool:
    return type(value) in (int, float) and -1e15 < value < 1e15 and math.isfinite(value)


def _patch(repo: Path, sha: str) -> str | None:
    patch = _git(repo, "show", "--pretty=format:", "--no-ext-diff", "--no-renames", sha)
    result = _git(repo, "patch-id", "--stable", data=patch).decode().split()
    return result[0] if result else None


def _history(repo: Path, main: str, horizon: float) -> list[dict]:
    tip = (
        _git(repo, "rev-parse", "--verify", "--end-of-options", main + "^{commit}").decode().strip()
    )
    # Inspect timestamps ourselves: --before can prune history with skewed dates.
    raw = _git(
        repo,
        "log",
        f"--max-count={MAX_COMMITS + 1}",
        "--format=%H%x00%ct%x00%an%x00%B%x00%x1e",
        tip,
    ).decode(errors="replace")
    rows = []
    chunks = [chunk.strip("\n") for chunk in raw.split("\x1e") if chunk.strip()]
    if len(chunks) > MAX_COMMITS:
        raise ObservationError("history exceeds bound")
    for chunk in chunks:
        sha, at, author, body, _ = chunk.split("\x00", 4)
        if int(at) <= horizon:
            rows.append({"sha": sha, "at": int(at), "author": author, "reason": body.strip()})
    return rows


def _reconcile(repo: Path, published: list[str], history: list[dict]) -> tuple[dict, list[dict]]:
    present = {row["sha"] for row in history}
    matches = {sha: sha for sha in published if sha in present}
    missing = [sha for sha in published if sha not in present]
    if missing:
        patches: dict[str, list[str]] = {}
        for row in history:
            pid = _patch(repo, row["sha"])
            if pid:
                patches.setdefault(pid, []).append(row["sha"])
        unavailable = False
        for sha in missing:
            try:
                pid = _patch(repo, sha)
            except ObservationError:
                # The original object may have been pruned. An explicit revert
                # still names it; missing objects alone cannot mean reverted.
                unavailable = True
                continue
            candidates = patches.get(pid, []) if pid else []
            if len(candidates) == 1:
                matches[sha] = candidates[0]
        if not unavailable and len(matches) < len(published) and len(published) > 1:
            # Combined squash reconciliation is safe only for a contiguous chain;
            # including another task's interleaved change would fabricate evidence.
            parents = {
                sha: _git(repo, "rev-list", "--parents", "-n", "1", sha).decode().split()[1:]
                for sha in published
            }
            children = {
                parent: sha for sha, ps in parents.items() for parent in ps if parent in parents
            }
            starts = [sha for sha, ps in parents.items() if not any(p in parents for p in ps)]
            chain = []
            current = starts[0] if len(starts) == 1 else None
            while current and current not in chain:
                chain.append(current)
                current = children.get(current)
            if len(chain) == len(published) and all(len(parents[sha]) == 1 for sha in chain):
                diff = _git(
                    repo, "diff", "--no-ext-diff", "--no-renames", parents[chain[0]][0], chain[-1]
                )
                pid = _git(repo, "patch-id", "--stable", data=diff).decode().split()
                candidates = patches.get(pid[0], []) if pid else []
                if len(candidates) == 1:
                    matches = dict.fromkeys(published, candidates[0])
    reverts = []
    for row in history:
        targets = REVERT.findall(row["reason"])
        hit = [
            sha
            for sha in published
            if any(
                sha.startswith(target) or matches.get(sha, "!").startswith(target)
                for target in targets
            )
        ]
        if hit:
            reverts.append({**row, "commits": hit})
    return matches, reverts


def evaluate(repo: Path, main: str, task: dict, events: list[dict], now: float) -> dict:
    token = _DEADLINE.set(time.monotonic() + 2.0)
    try:
        return _evaluate(repo, main, task, events, now)
    finally:
        _DEADLINE.reset(token)


def _evaluate(repo: Path, main: str, task: dict, events: list[dict], now: float) -> dict:
    """Return this task's fixed-horizon evidence. No disk state is mutated.

    All task work events must be timestamped inside its claim/deadline window.
    Approval events may arrive until read_at; another vendor or human can approve.
    Unknown token usage is an attempt count, and makes total tokens unknown.
    """
    task_id = task.get("task", task.get("id"))
    claimed, deadline, horizon = (task[k] for k in ("claimed_at", "deadline", "read_at"))
    if (
        not all(_number(v) for v in (claimed, deadline, horizon, now))
        or not claimed <= deadline <= horizon
    ):
        raise ValueError("invalid task horizon")
    rows = [
        e
        for e in events
        if isinstance(e, dict)
        and e.get("task") == task_id
        and _number(e.get("at"))
        and claimed <= e["at"] <= min(now, horizon)
    ]
    work = [e for e in rows if e["at"] <= deadline]
    attempts = sorted((e for e in work if e.get("type") == "attempt"), key=lambda e: e["at"])
    unknown = sum(type(e.get("tokens")) is not int or e["tokens"] < 0 for e in attempts)
    if not attempts:
        unknown = 1
    duration_unknown = (
        sum(
            bool(e.get("duration_unknown")) or not _number(e.get("wall_s")) or e["wall_s"] < 0
            for e in attempts
        )
        if attempts
        else 1
    )
    published = sorted(
        {
            sha
            for e in work
            if e.get("type") == "publish"
            for sha in e.get("commits", [])
            if isinstance(sha, str) and SHA.fullmatch(sha)
        }
    )
    result = {
        **task,
        "outcome": "pending",
        "commits_published": len(published),
        "commits_kept": 0,
        "approved": False,
        "reverts": [],
        "wall_s": sum(
            e["wall_s"] for e in attempts if _number(e.get("wall_s")) and e["wall_s"] >= 0
        ),
        "tokens": None if unknown else sum(e["tokens"] for e in attempts),
        "unknown_usage": unknown,
        "attempt_count": len(attempts),
        "duration_unknown": duration_unknown,
        "evidence": {"published": published, "matches": {}, "read_at": horizon},
    }
    if now < horizon:
        return result
    escalated = any(
        e.get("type") == "escalation" and e.get("to") != task.get("agent") for e in work
    )
    if not published:
        last = attempts[-1].get("outcome") if attempts else None
        result["outcome"] = (
            "escalated"
            if escalated
            else "quota"
            if last in QUOTA
            else "failed"
            if last in FAILED
            else "abandoned"
        )
        return result
    try:
        matches, reverts = _reconcile(repo, published, _history(repo, main, horizon))
        result["evidence"]["matches"] = matches
        result["reverts"] = reverts
        reverted = {sha for row in reverts for sha in row["commits"]}
        result["commits_kept"] = len(set(matches) - reverted)
        result["outcome"] = (
            "escalated"
            if escalated
            else "reverted"
            if reverts
            else "kept"
            if len(matches) == len(published)
            else "unknown"
        )
        approved_shas = set(published) | set(matches.values())
        result["approved"] = any(
            e.get("type") == "approval"
            and e.get("sha") in approved_shas
            and (
                e.get("human") is True
                or (
                    e.get("actor") != task.get("agent")
                    and e.get("vendor")
                    and e["vendor"] != task.get("vendor")
                )
            )
            for e in rows
        )
    except (ObservationError, ValueError):
        result["outcome"] = "escalated" if escalated else "unknown"
        result["evidence"]["error"] = "git evidence unavailable"
    return result
