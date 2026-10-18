"""Vendor-only failover policy and typed continuation context for reflexd."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from datetime import datetime, timezone

from quorus.private_files import write_private_json
from quorus.protocol import SocialVerb


class VendorFailure(str):
    """An actionable diagnosis from harness stderr, never from model prose."""

    def __new__(cls, text: str, reason: str):
        obj = super().__new__(cls, text)
        obj.reason = reason
        return obj


def continuation(envelope: dict) -> dict | None:
    """Only validated, explicitly addressed typed failovers wake a teammate."""
    if envelope.get("message_type") != "social":
        return None
    try:
        sv = SocialVerb.model_validate_json(envelope.get("content") or "")
    except ValueError:
        return None
    if sv.verb != "defer" or sv.actor != envelope.get("from_name"):
        return None
    payload = sv.payload.model_dump()
    if not payload.get("failover"):
        return None
    data = payload["failover"]
    if (data["failed_agents"][-1] != sv.actor
            or len(set(data["failed_agents"])) != data["handoffs"]
            or payload["to"] in data["failed_agents"]
            or sv.ref_message_id != data["original_message_id"]):
        return None
    return {**payload["failover"], "to": payload["to"], "actor": sv.actor,
            "root": sv.ref_message_id}


def load_reason(config) -> str:
    try:
        path = config.runtime_dir / f"degraded-{config.participant_name}.json"
        value = json.loads(path.read_text())
        return value if value in ("quota", "rate_limit", "login") else ""
    except (OSError, ValueError):
        return ""


async def save_reason(config, reason):
    path = config.runtime_dir / f"degraded-{config.participant_name}.json"
    await asyncio.to_thread(write_private_json, path, reason)


def handoff_line(target: str, data: dict) -> str:
    return (
        f'/defer @{target}: vendor {data["reason"]}. Task: "{data["task"]}". '
        f'Branch {data["branch"]}, last commit {data["last_commit"]}.'
    )


def timestamp(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()
        except ValueError:
            return 0.0


def choose_candidate(entries, members, *, actor, failed, task, detect, capabilities, score):
    ranked = []
    for entry in entries:
        name = entry.get("name") or ""
        if (name not in members or name == actor or name in failed
                or not entry.get("online") or entry.get("status") != "active"
                or not name.endswith(("-claude", "-codex", "-gemini", "-cursor",
                                      "-opencode", "-cline"))
                or detect(name) == detect(actor)):
            continue
        bid, _ = score(kind="open_todo", role=None, description=task,
                       capabilities=capabilities(name), recency_seconds=0.0)
        if bid > 0:
            ranked.append((-bid, timestamp(entry.get("last_wake_at")), name))
    return min(ranked)[2] if ranked else None


def _last_commit(worktree) -> str:
    if worktree is None:
        return "unavailable"
    result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=worktree.path,
                            capture_output=True, text=True, timeout=10, check=False)
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


async def attempt(daemon, relay, envelope, *, reason, mission, worktree, mode,
                  detect, capabilities, score) -> str | None:
    """Transfer a claim, then send one typed wake. Return a fallback diagnosis suffix."""
    if mode in ("manual", "readonly"):  # read-only rooms post the diagnosis only (spec)
        return None
    actor = daemon.config.participant_name
    room = envelope.get("room") or ""
    previous = continuation(envelope) or {}
    original = (previous.get("original_message_id") or envelope.get("message_id")
                or envelope.get("id"))
    if not original:
        return None
    key = hashlib.sha256(f"{room}:{original}".encode()).hexdigest()
    path = daemon.config.runtime_dir / f"failover-{actor}.json"
    try:
        ledger = json.loads(await asyncio.to_thread(path.read_text)) if path.exists() else {}
        if not isinstance(ledger, dict):
            raise ValueError("invalid failover ledger")
    except (OSError, ValueError):
        return "Failover state unavailable; ask the owner to reassign."
    if key in ledger:
        return None  # already attempted by this agent, including across restarts
    failed = list(dict.fromkeys([*previous.get("failed_agents", []), actor]))
    hops = previous.get("handoffs", 0)
    if actor in previous.get("failed_agents", []) or hops >= 2:
        daemon._alert_owner(room, "⚠ stuck: vendor failover limit reached; needs you")
        return "Failover limit reached (2 hand-offs); owner needs to reassign."
    task = " ".join(str((mission or {}).get("summary") or previous.get("task")
                        or envelope.get("content") or "Continue the task").split())[:120]
    try:
        if mission or previous.get("task_id"):
            current = await relay.mission_task_for(room=room, participant=actor)
            if not current or current.get("task_id") != (mission or previous).get("task_id"):
                return None  # completed or reassigned while the harness was running
        entries = await relay.fetch_presence()
        members = await relay.fetch_members(room=room)
        target = choose_candidate(entries, members, actor=actor, failed=failed, task=task,
                                  detect=detect, capabilities=capabilities, score=score)
        if not target:
            return None
        data = {
            "original_message_id": original,
            "task_id": (mission or {}).get("task_id") or previous.get("task_id"),
            "task": task, "reason": reason,
            "branch": worktree.branch if worktree else f"quorus/{actor}",
            "last_commit": await asyncio.to_thread(_last_commit, worktree),
            "failed_agents": failed, "handoffs": hops + 1,
        }
        # Persist before side effects. A crash after posting cannot hand off twice.
        ledger[key] = data
        await asyncio.to_thread(write_private_json, path, dict(list(ledger.items())[-1024:]))
        if data["task_id"]:
            await relay.release_for_failover(room=room, actor=actor, target=target,
                                             task_id=data["task_id"])
        await relay.post_social_defer(room_id=room, actor=actor, target=target,
                                      ref_message_id=original, failover=data)
    except Exception as exc:
        return f"Failover could not complete ({type(exc).__name__}); owner needs to reassign."
    return "sent"
