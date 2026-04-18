"""Claude Code-specific Quorus runner.

Mirrors the hardened Codex runner architecture:

- isolated per-agent QUORUS_CONFIG_DIR (never overwrites the human's profile)
- exact identity resolution (no double-suffix: arav-claude stays arav-claude)
- parent-assisted join fallback on 403
- correct /heartbeat endpoint (not /rooms/{room}/heartbeat)
- proper thread cleanup in finally block
- saved autonomy defaults persisted to profile
- MCP-unavailable fallback via quorus CLI shell calls
- supervised autonomous mode via ``claude -p`` (non-interactive, bounded per turn)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

from quorus.profiles import ProfileManager

DEFAULT_HISTORY_LIMIT = 25
_DEFAULTS_KEY = "claude_autonomy"


class ClaudeAgentError(RuntimeError):
    """Raised for recoverable Claude-agent runner failures."""


# ---------------------------------------------------------------------------
# Profile helpers
# ---------------------------------------------------------------------------

def _current_profile_manager() -> tuple[ProfileManager, str | None, dict]:
    pm = ProfileManager()
    slug = pm.current()
    data = pm.get(slug) if slug else None
    return pm, slug, (data or {})


def _cached_child_api_key(agent_name: str) -> str:
    _pm, _slug, data = _current_profile_manager()
    keys = data.get("agent_api_keys", {})
    if isinstance(keys, dict):
        value = keys.get(agent_name, "")
        if isinstance(value, str):
            return value
    return ""


def _save_child_api_key(agent_name: str, api_key: str) -> None:
    pm, slug, data = _current_profile_manager()
    if not slug:
        return
    keys = data.get("agent_api_keys", {})
    if not isinstance(keys, dict):
        keys = {}
    keys[agent_name] = api_key
    data["agent_api_keys"] = keys
    pm.save(slug, data)


def load_claude_autonomy_defaults() -> dict:
    """Load saved Claude autonomy defaults from the current profile."""
    _pm, _slug, data = _current_profile_manager()
    defaults = data.get(_DEFAULTS_KEY, {})
    return defaults if isinstance(defaults, dict) else {}


def save_claude_autonomy_defaults(
    *,
    autonomous: bool | None = None,
    room_poll_seconds: int | None = None,
    heartbeat_seconds: int | None = None,
    history_limit: int | None = None,
    permission_mode: str | None = None,
    model: str | None = None,
) -> None:
    """Persist Claude autonomy defaults into the current Quorus profile."""
    pm, slug, data = _current_profile_manager()
    if not slug:
        return
    existing = data.get(_DEFAULTS_KEY, {})
    if not isinstance(existing, dict):
        existing = {}
    if autonomous is not None:
        existing["autonomous"] = autonomous
    if room_poll_seconds is not None:
        existing["room_poll_seconds"] = room_poll_seconds
    if heartbeat_seconds is not None:
        existing["heartbeat_seconds"] = heartbeat_seconds
    if history_limit is not None:
        existing["history_limit"] = history_limit
    if permission_mode is not None:
        existing["permission_mode"] = permission_mode
    if model is not None:
        existing["model"] = model
    data[_DEFAULTS_KEY] = existing
    pm.save(slug, data)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _auth_headers(
    *,
    relay_url: str,
    api_key: str = "",
    relay_secret: str = "",
) -> dict[str, str]:
    """Return bearer auth headers using API-key JWT exchange when available."""
    if api_key:
        resp = httpx.post(
            f"{relay_url}/v1/auth/token",
            json={"api_key": api_key},
            timeout=10,
            follow_redirects=True,
        )
        resp.raise_for_status()
        token = resp.json()["token"]
        return {"Authorization": f"Bearer {token}"}

    if relay_secret:
        return {"Authorization": f"Bearer {relay_secret}"}

    raise ClaudeAgentError("Claude runner needs an API key or relay secret.")


def _request_json(
    method: str,
    url: str,
    *,
    relay_url: str,
    api_key: str = "",
    relay_secret: str = "",
    params: dict | None = None,
    json_body: dict | None = None,
    timeout: int | float = 10,
) -> object:
    resp = httpx.request(
        method,
        url,
        params=params,
        json=json_body,
        headers=_auth_headers(
            relay_url=relay_url,
            api_key=api_key,
            relay_secret=relay_secret,
        ),
        timeout=timeout,
        follow_redirects=True,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Identity resolution  (mirrors codex_agent.py exactly — no double-suffix)
# ---------------------------------------------------------------------------

def resolve_identity(
    *,
    relay_url: str,
    parent_name: str,
    parent_api_key: str,
    requested_name: str | None,
    suffix: str | None,
) -> tuple[str, str]:
    """Resolve the participant identity and API key for the Claude run.

    Mirrors the Codex runner logic exactly to prevent double-suffix creation:
    - No --name, no --suffix → return (parent_name, parent_api_key) as-is.
    - Only one of --name or --suffix may be given.
    - --name must equal parent_name or start with parent_name + '-'.
    """
    if requested_name and suffix:
        raise ClaudeAgentError("Pass either --name or --suffix, not both.")

    # No override — run as the parent identity directly (prevents arav-claude-claude).
    if not requested_name and not suffix:
        return parent_name, parent_api_key

    if not parent_api_key:
        raise ClaudeAgentError(
            "Registering child Claude identities requires a Quorus workspace API key."
        )

    if suffix:
        derived_suffix = suffix.strip()
    else:
        # --name provided: validate it is this user's child, not a random name.
        if requested_name == parent_name:
            return parent_name, parent_api_key
        prefix = f"{parent_name}-"
        if not requested_name or not requested_name.startswith(prefix):
            raise ClaudeAgentError(
                f"--name must equal {parent_name!r} or start with {prefix!r}."
            )
        derived_suffix = requested_name[len(prefix):]

    if not derived_suffix:
        raise ClaudeAgentError("Derived Claude agent suffix cannot be empty.")

    agent_name = f"{parent_name}-{derived_suffix}"

    # Return cached key if already registered.
    cached_key = _cached_child_api_key(agent_name)
    if cached_key:
        return agent_name, cached_key

    # Register a new child identity via the relay.
    resp = httpx.post(
        f"{relay_url}/v1/auth/register-agent",
        json={"suffix": derived_suffix},
        headers={"Authorization": f"Bearer {parent_api_key}"},
        timeout=10,
        follow_redirects=True,
    )
    resp.raise_for_status()
    data = resp.json()
    agent_name = data["agent_name"]
    agent_api_key = data["api_key"]
    _save_child_api_key(agent_name, agent_api_key)
    return agent_name, agent_api_key


# ---------------------------------------------------------------------------
# Room operations
# ---------------------------------------------------------------------------

def join_room(
    *,
    relay_url: str,
    room: str,
    participant: str,
    api_key: str = "",
    relay_secret: str = "",
) -> None:
    """Ensure the runner identity is a member of the room."""
    _request_json(
        "POST",
        f"{relay_url}/rooms/{room}/join",
        relay_url=relay_url,
        api_key=api_key,
        relay_secret=relay_secret,
        json_body={"participant": participant},
    )


def parent_join_room(
    *,
    relay_url: str,
    room: str,
    participant: str,
    parent_api_key: str,
) -> None:
    """Add a child participant to the room using the parent/admin identity."""
    _request_json(
        "POST",
        f"{relay_url}/rooms/{room}/join",
        relay_url=relay_url,
        api_key=parent_api_key,
        json_body={"participant": participant},
    )


def send_announcement(
    *,
    relay_url: str,
    room: str,
    participant: str,
    content: str,
    api_key: str = "",
    relay_secret: str = "",
) -> None:
    """Post a status message for the Claude runner."""
    _request_json(
        "POST",
        f"{relay_url}/rooms/{room}/messages",
        relay_url=relay_url,
        api_key=api_key,
        relay_secret=relay_secret,
        json_body={
            "from_name": participant,
            "content": content,
            "message_type": "status",
        },
    )


def send_heartbeat(
    *,
    relay_url: str,
    room: str,
    participant: str,
    api_key: str = "",
    relay_secret: str = "",
) -> None:
    """Keep the Claude runner visible in room presence.

    Uses the top-level /heartbeat endpoint (mirrors codex_agent.py).
    The old /rooms/{room}/heartbeat endpoint does not exist on the relay.
    """
    _request_json(
        "POST",
        f"{relay_url}/heartbeat",
        relay_url=relay_url,
        api_key=api_key,
        relay_secret=relay_secret,
        json_body={"instance_name": participant, "status": "active", "room": room},
    )


def fetch_room_state(
    *,
    relay_url: str,
    room: str,
    api_key: str = "",
    relay_secret: str = "",
) -> dict:
    data = _request_json(
        "GET",
        f"{relay_url}/rooms/{room}/state",
        relay_url=relay_url,
        api_key=api_key,
        relay_secret=relay_secret,
    )
    return data if isinstance(data, dict) else {}


def fetch_room_history(
    *,
    relay_url: str,
    room: str,
    limit: int = DEFAULT_HISTORY_LIMIT,
    api_key: str = "",
    relay_secret: str = "",
) -> list[dict]:
    data = _request_json(
        "GET",
        f"{relay_url}/rooms/{room}/history",
        relay_url=relay_url,
        api_key=api_key,
        relay_secret=relay_secret,
        params={"limit": limit},
    )
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


# ---------------------------------------------------------------------------
# Local mirror helpers
# ---------------------------------------------------------------------------

def _message_id(msg: dict) -> str:
    value = msg.get("message_id") or msg.get("id") or msg.get("uuid") or msg.get("timestamp") or ""
    return str(value)


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _append_lines(path: Path, lines: list[str]) -> None:
    if not lines:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line)


def _format_room_context(room: str, participant: str, state: dict, history: list[dict]) -> str:
    lines = [
        f"# Quorus Room: {room}",
        f"Participant: {participant}",
        f"Snapshot: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC",
        "",
    ]

    active_agents = state.get("active_agents", [])
    if isinstance(active_agents, list) and active_agents:
        lines.append("## Active Agents")
        for agent in active_agents:
            lines.append(f"- {agent}")
        lines.append("")

    if history:
        lines.append("## Recent Room Messages")
        for message in history[-DEFAULT_HISTORY_LIMIT:]:
            sender = message.get("from_name", "?")
            message_type = message.get("message_type", "chat")
            timestamp = str(message.get("timestamp", ""))[:19]
            content = str(message.get("content", "")).strip()
            lines.append(f"- [{timestamp}] {sender} [{message_type}]: {content}")
        lines.append("")

    locks = state.get("locked_files", {})
    if isinstance(locks, dict) and locks:
        lines.append("## Locked Files")
        for file_path, meta in locks.items():
            holder = meta.get("held_by", "?") if isinstance(meta, dict) else "?"
            lines.append(f"- {file_path}: {holder}")
        lines.append("")

    goal = state.get("active_goal")
    if goal:
        lines.append("## Active Goal")
        lines.append(str(goal))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def sync_room_context(
    *,
    relay_url: str,
    room: str,
    participant: str,
    context_path: Path,
    history_limit: int,
    api_key: str = "",
    relay_secret: str = "",
) -> list[dict]:
    state = fetch_room_state(
        relay_url=relay_url,
        room=room,
        api_key=api_key,
        relay_secret=relay_secret,
    )
    history = fetch_room_history(
        relay_url=relay_url,
        room=room,
        limit=history_limit,
        api_key=api_key,
        relay_secret=relay_secret,
    )
    _write_atomic(context_path, _format_room_context(room, participant, state, history))
    return history


# ---------------------------------------------------------------------------
# MCP fallback — quorus CLI shell calls
# ---------------------------------------------------------------------------

def _quorus_cli_path() -> str | None:
    """Return path to the installed quorus CLI, or None."""
    return shutil.which("quorus")


def _fallback_read_context(context_path: Path) -> str:
    """Read the local context mirror as fallback when MCP is unavailable."""
    if context_path.exists():
        return context_path.read_text(encoding="utf-8")
    return ""


def _fallback_post_message(room: str, content: str, verbose: bool = False) -> bool:
    """Post a message via quorus CLI when MCP is unavailable."""
    cli = _quorus_cli_path()
    if not cli:
        return False
    try:
        result = subprocess.run(
            [cli, "say", room, content],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if verbose and result.returncode != 0:
            sys.stderr.write(f"[quorus claude-agent fallback-post] {result.stderr}\n")
        return result.returncode == 0
    except Exception as exc:  # noqa: BLE001
        if verbose:
            sys.stderr.write(f"[quorus claude-agent fallback-post] {exc}\n")
        return False


def _fallback_read_inbox(verbose: bool = False) -> list[str]:
    """Read new inbox messages via quorus CLI as fallback."""
    cli = _quorus_cli_path()
    if not cli:
        return []
    try:
        result = subprocess.run(
            [cli, "inbox"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return [line for line in result.stdout.splitlines() if line.strip()]
    except Exception as exc:  # noqa: BLE001
        if verbose:
            sys.stderr.write(f"[quorus claude-agent fallback-inbox] {exc}\n")
    return []


# ---------------------------------------------------------------------------
# Background loops
# ---------------------------------------------------------------------------

def poll_inbox_loop(
    *,
    relay_url: str,
    participant: str,
    inbox_path: Path,
    stop_event: threading.Event,
    wait_seconds: int,
    api_key: str = "",
    relay_secret: str = "",
    verbose: bool = False,
) -> None:
    """Mirror the participant inbox into a local file for Claude context."""
    inbox_path.parent.mkdir(parents=True, exist_ok=True)

    while not stop_event.is_set():
        try:
            data = _request_json(
                "GET",
                f"{relay_url}/messages/{participant}",
                relay_url=relay_url,
                api_key=api_key,
                relay_secret=relay_secret,
                params={"wait": wait_seconds},
                timeout=wait_seconds + 10,
            )
            messages = data if isinstance(data, list) else []
            if not messages:
                continue

            lines: list[str] = []
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                sender = msg.get("from_name", "?")
                room = msg.get("room", "dm")
                content = msg.get("content", "")
                ts = str(msg.get("timestamp", ""))[:19]
                lines.append(f"[{ts}] {sender} ({room}): {content}\n")
            _append_lines(inbox_path, lines)

            if verbose:
                for line in lines:
                    sys.stderr.write(line)
                sys.stderr.flush()
        except Exception as exc:  # noqa: BLE001
            if verbose:
                sys.stderr.write(f"[quorus claude-agent inbox] {exc}\n")
                sys.stderr.flush()
            # MCP fallback: try quorus CLI inbox
            fallback_lines = _fallback_read_inbox(verbose=verbose)
            if fallback_lines:
                _append_lines(inbox_path, [line + "\n" for line in fallback_lines])
            time.sleep(5)


def room_context_loop(
    *,
    relay_url: str,
    room: str,
    participant: str,
    context_path: Path,
    history_limit: int,
    stop_event: threading.Event,
    poll_seconds: int,
    api_key: str = "",
    relay_secret: str = "",
    verbose: bool = False,
) -> None:
    while not stop_event.is_set():
        try:
            sync_room_context(
                relay_url=relay_url,
                room=room,
                participant=participant,
                context_path=context_path,
                history_limit=history_limit,
                api_key=api_key,
                relay_secret=relay_secret,
            )
        except Exception as exc:  # noqa: BLE001
            if verbose:
                sys.stderr.write(f"[quorus claude-agent context] {exc}\n")
                sys.stderr.flush()
            # Context mirror stays on disk from last successful sync — still usable.
        stop_event.wait(poll_seconds)


def heartbeat_loop(
    *,
    relay_url: str,
    room: str,
    participant: str,
    stop_event: threading.Event,
    heartbeat_seconds: int,
    api_key: str = "",
    relay_secret: str = "",
    verbose: bool = False,
) -> None:
    while not stop_event.is_set():
        try:
            send_heartbeat(
                relay_url=relay_url,
                room=room,
                participant=participant,
                api_key=api_key,
                relay_secret=relay_secret,
            )
        except Exception as exc:  # noqa: BLE001
            if verbose:
                sys.stderr.write(f"[quorus claude-agent heartbeat] {exc}\n")
                sys.stderr.flush()
        stop_event.wait(heartbeat_seconds)


# ---------------------------------------------------------------------------
# Claude command builders
# ---------------------------------------------------------------------------

def build_prompt(
    room: str,
    participant: str,
    inbox_path: Path,
    context_path: Path | None = None,
) -> str:
    """Build the initial Claude room prompt."""
    lines = [
        f"You are `{participant}` in the Quorus room `{room}`.",
        "",
        "Operating rules:",
        "- Check Quorus messages and room context before acting.",
        "- Use the Quorus MCP tools as the primary read/write path.",
        "- If Quorus MCP is unavailable, fall back to the local room mirrors and "
        "the `quorus` CLI via shell for room reads/posts.",
        f"- A local inbox mirror is available at `{inbox_path}` as fallback context.",
    ]
    if context_path is not None:
        lines.append(f"- A room context snapshot is available at `{context_path}`.")
    lines.extend([
        "- If a concrete task is assigned, acknowledge ownership "
        "clearly in-room before implementing.",
        "- Post concise status updates after meaningful progress, blockers, or completion.",
        f"- Do not impersonate another participant. Only send as `{participant}`.",
        "- Work autonomously on assigned tasks. Build, test, iterate, and post updates.",
        "- When you complete a task, announce it in-room and look for the next one.",
        "- If blocked, post the blocker clearly and move to other work if possible.",
    ])
    return "\n".join(lines)


def _build_mcp_config(
    *,
    participant: str,
    relay_url: str,
    api_key: str = "",
    relay_secret: str = "",
) -> dict:
    """Build MCP config block for the Quorus server."""
    env: dict[str, str] = {
        "QUORUS_INSTANCE_NAME": participant,
        "QUORUS_RELAY_URL": relay_url,
    }
    if api_key:
        env["QUORUS_API_KEY"] = api_key
    elif relay_secret:
        env["QUORUS_RELAY_SECRET"] = relay_secret

    mcp_command = shutil.which("quorus-mcp") or "quorus-mcp"
    return {
        "mcpServers": {
            "quorus": {
                "command": mcp_command,
                "args": [],
                "env": env,
            }
        }
    }


def build_claude_command(
    *,
    room: str,
    participant: str,
    relay_url: str,
    api_key: str = "",
    relay_secret: str = "",
    cwd: Path,
    permission_mode: str,
    inbox_path: Path,
    context_path: Path | None = None,
    model: str = "sonnet",
) -> list[str]:
    """Build the interactive Claude command line with Quorus overrides."""
    prompt = build_prompt(room, participant, inbox_path, context_path)
    mcp_config = _build_mcp_config(
        participant=participant,
        relay_url=relay_url,
        api_key=api_key,
        relay_secret=relay_secret,
    )
    cmd = [
        "claude",
        "--model", model,
        "--permission-mode", permission_mode,
        "--mcp-config", json.dumps(mcp_config),
        "--append-system-prompt", prompt,
    ]
    if cwd:
        cmd.extend(["--add-dir", str(cwd)])
    return cmd


def build_claude_print_command(
    *,
    room: str,
    participant: str,
    relay_url: str,
    api_key: str = "",
    relay_secret: str = "",
    cwd: Path,
    permission_mode: str,
    inbox_path: Path,
    context_path: Path,
    prompt: str,
    model: str = "sonnet",
) -> list[str]:
    """Build the non-interactive ``claude -p`` command for autonomous turns.

    Equivalent to ``codex exec`` — one bounded turn per room-activity trigger.
    """
    base_prompt = build_prompt(room, participant, inbox_path, context_path)
    mcp_config = _build_mcp_config(
        participant=participant,
        relay_url=relay_url,
        api_key=api_key,
        relay_secret=relay_secret,
    )
    # --append-system-prompt and --add-dir both break -p / --print mode,
    # so we fold everything into the single prompt argument.
    full_prompt = f"{base_prompt}\n\n{prompt}"
    return [
        "claude",
        "-p",
        "--model", model,
        "--permission-mode", permission_mode,
        "--mcp-config", json.dumps(mcp_config),
        "--output-format", "text",
        full_prompt,
    ]


# ---------------------------------------------------------------------------
# Autonomous loop
# ---------------------------------------------------------------------------

def _autonomous_prompt(
    *,
    room: str,
    participant: str,
    context_path: Path,
    new_messages: list[dict],
    initial_scan: bool,
) -> str:
    if initial_scan:
        return (
            f"Review the current Quorus room `{room}` using the MCP tools first, then "
            f"use `{context_path}` only as fallback context. If there is a concrete "
            "task or blocker you should own, acknowledge it in-room and proceed. "
            "Work autonomously — build, test, iterate, and post updates."
        )

    summary_lines = []
    for message in new_messages[-5:]:
        sender = message.get("from_name", "?")
        content = str(message.get("content", "")).strip()
        summary_lines.append(f"- {sender}: {content}")
    summarized = "\n".join(summary_lines) or "- no new message text captured"
    return (
        "New Quorus room activity arrived. Read the room with MCP tools, use the "
        f"snapshot at `{context_path}` only as fallback, then respond or act if needed.\n\n"
        "Newest messages:\n"
        f"{summarized}\n\n"
        "If a task is assigned to you, own it and execute. Post updates on progress."
    )


def run_autonomous_loop(
    *,
    room: str,
    participant: str,
    relay_url: str,
    api_key: str,
    relay_secret: str,
    cwd: Path,
    permission_mode: str,
    inbox_path: Path,
    context_path: Path,
    history_limit: int,
    poll_seconds: int,
    stop_event: threading.Event,
    verbose: bool,
    model: str = "sonnet",
) -> int:
    """Supervise ``claude -p`` runs when room activity changes.

    One bounded autonomous turn per new-message batch — never one giant free-run.
    """
    seen_message_ids: set[str] = set()
    initial_scan = True

    while not stop_event.is_set():
        try:
            history = sync_room_context(
                relay_url=relay_url,
                room=room,
                participant=participant,
                context_path=context_path,
                history_limit=history_limit,
                api_key=api_key,
                relay_secret=relay_secret,
            )
        except Exception as exc:  # noqa: BLE001
            if verbose:
                sys.stderr.write(f"[quorus claude-agent auto-sync] {exc}\n")
                sys.stderr.flush()
            # MCP fallback: read from local context mirror
            context_text = _fallback_read_context(context_path)
            if context_text and not initial_scan:
                stop_event.wait(poll_seconds)
                continue
            stop_event.wait(poll_seconds)
            continue

        new_messages: list[dict] = []
        current_ids: set[str] = set()
        for message in history:
            message_id = _message_id(message)
            if message_id:
                current_ids.add(message_id)
            sender = str(message.get("from_name", ""))
            if message_id and message_id not in seen_message_ids and sender != participant:
                new_messages.append(message)

        should_run = initial_scan or bool(new_messages)
        seen_message_ids.update(current_ids)

        if not should_run:
            stop_event.wait(poll_seconds)
            continue

        prompt = _autonomous_prompt(
            room=room,
            participant=participant,
            context_path=context_path,
            new_messages=new_messages,
            initial_scan=initial_scan,
        )
        initial_scan = False

        cmd = build_claude_print_command(
            room=room,
            participant=participant,
            relay_url=relay_url,
            api_key=api_key,
            relay_secret=relay_secret,
            cwd=cwd,
            permission_mode=permission_mode,
            inbox_path=inbox_path,
            context_path=context_path,
            prompt=prompt,
            model=model,
        )

        if verbose:
            sys.stderr.write(f"[quorus claude-agent] Spawning claude -p turn\n")
            sys.stderr.flush()

        rc = subprocess.call(cmd, cwd=str(cwd) if cwd else None)
        if rc != 0 and verbose:
            sys.stderr.write(f"[quorus claude-agent auto-exec] claude exited {rc}\n")
            sys.stderr.flush()

        stop_event.wait(max(5, poll_seconds))

    return 0


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_claude_agent(
    *,
    room: str,
    relay_url: str,
    parent_name: str,
    parent_api_key: str,
    relay_secret: str,
    requested_name: str | None,
    suffix: str | None,
    cwd: Path,
    wait_seconds: int,
    announce: bool,
    no_launch: bool,
    verbose: bool,
    permission_mode: str,
    autonomous: bool,
    room_poll_seconds: int,
    heartbeat_seconds: int,
    history_limit: int,
    model: str = "sonnet",
    save_defaults: bool = False,
) -> int:
    """Join the room, maintain runner state, and launch Claude Code.

    Config isolation: each agent gets its own QUORUS_CONFIG_DIR under /tmp
    so it never overwrites the human's ~/.quorus/profiles/default.json.
    """
    import tempfile as _tmp
    _agent_config_dir = Path(_tmp.mkdtemp(prefix="quorus-agent-"))
    os.environ.setdefault("QUORUS_CONFIG_DIR", str(_agent_config_dir))

    # Optionally persist these settings for future runs.
    if save_defaults:
        save_claude_autonomy_defaults(
            autonomous=autonomous,
            room_poll_seconds=room_poll_seconds,
            heartbeat_seconds=heartbeat_seconds,
            history_limit=history_limit,
            permission_mode=permission_mode,
            model=model,
        )

    participant, agent_api_key = resolve_identity(
        relay_url=relay_url,
        parent_name=parent_name,
        parent_api_key=parent_api_key,
        requested_name=requested_name,
        suffix=suffix,
    )
    effective_secret = relay_secret if not agent_api_key else ""

    # Join with parent-assisted fallback and 403-already-member tolerance.
    try:
        join_room(
            relay_url=relay_url,
            room=room,
            participant=participant,
            api_key=agent_api_key,
            relay_secret=effective_secret,
        )
    except httpx.HTTPStatusError as exc:
        can_parent_add = (
            exc.response.status_code == 403
            and parent_api_key
            and participant != parent_name
        )
        if can_parent_add:
            # Child identity — ask the parent account to add it.
            parent_join_room(
                relay_url=relay_url,
                room=room,
                participant=participant,
                parent_api_key=parent_api_key,
            )
        elif exc.response.status_code == 403:
            # Some relays reject redundant self-join; if room state is readable
            # with current auth, treat existing membership as satisfied.
            fetch_room_state(
                relay_url=relay_url,
                room=room,
                api_key=agent_api_key,
                relay_secret=effective_secret,
            )
            if verbose:
                sys.stderr.write(
                    f"[quorus claude-agent] join returned 403; room state readable — "
                    "treating as existing member.\n"
                )
                sys.stderr.flush()
        else:
            raise

    if announce:
        send_announcement(
            relay_url=relay_url,
            room=room,
            participant=participant,
            content=f"{participant} online. Claude Code runner attached and ready.",
            api_key=agent_api_key,
            relay_secret=effective_secret,
        )

    inbox_path = Path(f"/tmp/quorus-{participant}-inbox.txt")
    context_path = Path(f"/tmp/quorus-{participant}-context.md")
    stop_event = threading.Event()

    threads = [
        threading.Thread(
            target=poll_inbox_loop,
            kwargs={
                "relay_url": relay_url,
                "participant": participant,
                "inbox_path": inbox_path,
                "stop_event": stop_event,
                "wait_seconds": wait_seconds,
                "api_key": agent_api_key,
                "relay_secret": effective_secret,
                "verbose": verbose,
            },
            daemon=True,
        ),
        threading.Thread(
            target=room_context_loop,
            kwargs={
                "relay_url": relay_url,
                "room": room,
                "participant": participant,
                "context_path": context_path,
                "history_limit": history_limit,
                "stop_event": stop_event,
                "poll_seconds": room_poll_seconds,
                "api_key": agent_api_key,
                "relay_secret": effective_secret,
                "verbose": verbose,
            },
            daemon=True,
        ),
        threading.Thread(
            target=heartbeat_loop,
            kwargs={
                "relay_url": relay_url,
                "room": room,
                "participant": participant,
                "stop_event": stop_event,
                "heartbeat_seconds": heartbeat_seconds,
                "api_key": agent_api_key,
                "relay_secret": effective_secret,
                "verbose": verbose,
            },
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()

    print(f"Quorus room:     {room}")
    print(f"Participant:     {participant}")
    print(f"Inbox mirror:    {inbox_path}")
    print(f"Context mirror:  {context_path}")
    print(f"Relay:           {relay_url}")
    print(f"Mode:            {'autonomous' if autonomous else 'interactive'}")
    print(f"Permission mode: {permission_mode}")

    try:
        if no_launch:
            print("Quorus loops active. Press Ctrl+C to stop.")
            while not stop_event.is_set():
                time.sleep(1)
            return 0

        if autonomous:
            sys.stderr.write(
                f"[quorus claude-agent] Autonomous mode active for {participant} in {room}.\n"
            )
            sys.stderr.flush()
            return run_autonomous_loop(
                room=room,
                participant=participant,
                relay_url=relay_url,
                api_key=agent_api_key,
                relay_secret=effective_secret,
                cwd=cwd,
                permission_mode=permission_mode,
                inbox_path=inbox_path,
                context_path=context_path,
                history_limit=history_limit,
                poll_seconds=room_poll_seconds,
                stop_event=stop_event,
                verbose=verbose,
                model=model,
            )

        cmd = build_claude_command(
            room=room,
            participant=participant,
            relay_url=relay_url,
            api_key=agent_api_key,
            relay_secret=effective_secret,
            cwd=cwd,
            permission_mode=permission_mode,
            inbox_path=inbox_path,
            context_path=context_path,
            model=model,
        )
        return subprocess.call(cmd, cwd=str(cwd) if cwd else None)

    except KeyboardInterrupt:
        return 130
    finally:
        stop_event.set()
        for thread in threads:
            thread.join(timeout=2)
