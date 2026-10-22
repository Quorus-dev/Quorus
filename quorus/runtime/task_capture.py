"""Daemon capture for W4. Observational only; a failure never changes a wake."""
from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from pathlib import Path

from quorus.runtime import task_record as records

MAX_COMMITS = 20  # quorus.services.provenance.MAX_COMMITS


def head(repo: Path, ref: str) -> str:
    """The commit *ref* points at in *repo*, or "" when git can't say."""
    try:
        out = subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref], cwd=repo,
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def mine(repo: Path, before: str, ref: str, me: str) -> list[str]:
    """Commits *ref* gained since *before* that the publish noted as *me*'s.

    Another agent's daemon can publish to the same main in between, so the range
    alone would claim its commits too; the ledger's provenance note on each
    commit (quorus/ledger.py, written at publish) says whose it is."""
    from quorus.ledger import read_provenance

    after = head(repo, ref)
    if not before or not after or before == after:
        return []
    try:
        out = subprocess.run(["git", "rev-list", "--max-count=200", f"{before}..{after}"],
                             cwd=repo, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    return [sha for sha in out.stdout.split()
            if (read_provenance(repo, sha) or {}).get("agent") == me][:MAX_COMMITS]


def begin(repo: Path, key: str, envelope: dict, history: list[dict], *, agent: str,
          vendor: str, model: str, version: str, writable: bool, handoff: dict | None = None,
          now: float | None = None, authorized: bool = True) -> dict | None:
    if not authorized:
        return None
    at = time.time() if now is None else now
    rows = records.read(repo, key)
    claims = {r["task"]: r for r in rows if r.get("type") == "claim"}
    mid = envelope.get("message_id") or envelope.get("id")
    if not mid:
        return None
    candidates = [envelope.get("thread_root_id"), envelope.get("reply_to"),
                  (handoff or {}).get("original_message_id"), (handoff or {}).get("root"), mid]
    messages = {m.get("id") or m.get("message_id"): m for m in history}
    for parent in list(candidates):
        for _ in range(32):
            if not parent or parent in claims:
                break
            msg = messages.get(parent) or {}
            parent = msg.get("thread_root_id") or msg.get("reply_to")
            candidates.append(parent)
    task = next((x for x in candidates if x in claims), None)
    new = task is None
    if task is None:
        if envelope.get("message_type") in {"status", "social"}:
            return None
        task = mid
        from quorus.services.approval_svc import agent_owner

        owner = agent_owner(agent)
        claims[task] = records.claim(repo, key, task, agent=agent, vendor=vendor, model=model,
                      version=version, text=envelope.get("content") or "", now=at,
                      allow_kind_override=bool(owner and envelope.get("from_name") == owner))
    attempt = uuid.uuid4().hex
    records.append(repo, key, dict(type="attempt_start", task=task, attempt=attempt,
                                   at=at, agent=agent))
    return dict(task=task, attempt=attempt, started=at, new=new, kind=claims[task]["kind"],
                handoff=bool(handoff or claims.get(task, {}).get("agent", agent) != agent))


def finish(repo: Path, key: str, turn: dict, *, agent: str, outcome: str,
           tokens: int | None, now: float | None = None) -> None:
    at = time.time() if now is None else now
    records.append(repo, key, dict(type="attempt", task=turn["task"], attempt=turn["attempt"],
                                  at=at, agent=agent, outcome=outcome, tokens=tokens,
                                  wall_s=max(0, at - turn["started"])))


def published(repo: Path, key: str, turn: dict, commits: list[str],
              now: float | None = None) -> None:
    if commits:
        import subprocess

        paths = "\n".join(subprocess.check_output(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", sha],
            cwd=repo, timeout=5).decode("utf-8", "replace") for sha in commits)
        records.append(repo, key, dict(type="publish", task=turn["task"], commits=commits,
                                      commit_kind=records.kind(paths),
                                      at=time.time() if now is None else now))


def escalation(repo: Path, key: str, task: str, target: str) -> None:
    if any(r.get("type") == "claim" and r.get("task") == task for r in records.read(repo, key)):
        records.append(repo, key, dict(type="escalation", task=task, to=target, at=time.time()))


def approvals(repo: Path, key: str, history: list[dict], owner: str) -> None:
    """Record typed owner/own-agent approvals; never quoted prose or code fences."""
    from quorus.ledger import vendor_of
    from quorus.services.approval_svc import agent_owner

    rows = records.read(repo, key)
    seen = {r.get("message") for r in rows if r.get("type") in {"approval", "escalation"}}
    for msg in history:
        mid, actor = msg.get("id") or msg.get("message_id"), msg.get("from_name") or ""
        if not mid or mid in seen or not (actor == owner or agent_owner(actor) == owner):
            continue
        body = msg.get("content") or ""
        if "```" in body:
            continue
        if msg.get("message_type") == "social":
            from quorus.protocol import SocialVerb

            try:
                verb = SocialVerb.model_validate_json(body)
                data = verb.payload.model_dump()
                if verb.verb == "defer" and verb.actor == actor and data.get("to"):
                    if any(r.get("type") == "claim" and r.get("task") == verb.ref_message_id
                           for r in rows):
                        records.append(repo, key, dict(type="escalation", message=mid,
                            task=verb.ref_message_id, to=data["to"], at=_timestamp(msg)))
                        seen.add(mid)
            except ValueError:
                pass
            continue
        if actor == owner and re.search(r"\b(reassign|take over)\b", body, re.I):
            root = msg.get("thread_root_id") or msg.get("reply_to")
            target = re.search(r"@([\w-]+)", body)
            if target and any(r.get("type") == "claim" and r.get("task") == root for r in rows):
                records.append(repo, key, dict(type="escalation", message=mid, task=root,
                    to=target[1], at=_timestamp(msg)))
                seen.add(mid)
            continue
        match = re.fullmatch(r"\s*/vote\s+approve\s+([a-f0-9]{40})\s*", body)
        if not match:
            continue
        for event in rows:
            if event.get("type") == "publish" and match[1] in event.get("commits", []):
                records.append(repo, key, dict(type="approval", task=event["task"],
                    message=mid, sha=match[1], actor=actor, vendor=vendor_of(actor),
                    human=actor == owner, at=_timestamp(msg)))
                seen.add(mid)


def _timestamp(msg: dict) -> float:
    from datetime import datetime

    raw = msg.get("timestamp") or msg.get("created_at")
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()


def tokens(stdout: bytes, vendor: str) -> int | None:
    """Reported per-invocation usage only. Rate-limit percentages are not tokens."""
    total, found = 0, False
    for line in stdout.decode("utf-8", "replace").splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict) or not (
                (vendor == "codex" and obj.get("type") == "turn.completed")
                or (vendor == "claude" and obj.get("type") == "result")):
            continue
        usage = obj.get("usage")
        if not isinstance(usage, dict) or not {"input_tokens", "output_tokens"} <= usage.keys():
            return None
        names = ["input_tokens", "output_tokens"]
        if vendor == "claude":
            names += ["cache_creation_input_tokens", "cache_read_input_tokens"]
        values = [usage.get(n, 0) for n in names]
        if all(type(x) is int and 0 <= x <= 10**15 for x in values):
            total += sum(values)
            found = True
        else:
            return None
    return total if found else None


def outcome(reply: str, classified: str) -> str:
    if classified == "clean" and re.match(
            r"^(?:I (?:cannot|can't|couldn't|am unable to)|Unable to|Cannot complete)\b",
            reply.strip(), re.I):
        return "could_not"
    return classified
