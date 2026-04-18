"""Claude Code-specific Quorus runner.

This keeps Claude Code additive to Quorus rather than changing Codex/Gemini paths.
It supports:

- room-bound Claude Code identities
- inbox mirroring for direct messages
- room state/history context snapshots
- presence heartbeats so the runner stays visible in room state
- optional supervised autonomous mode powered by ``claude -p``
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


class ClaudeAgentError(RuntimeError):
    """Raised for recoverable Claude-agent runner failures."""


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
    return data if isinstance(data, list) else []


def send_heartbeat(
    *,
    relay_url: str,
    room: str,
    participant: str,
    api_key: str = "",
    relay_secret: str = "",
) -> None:
    _request_json(
        "POST",
        f"{relay_url}/rooms/{room}/heartbeat",
        relay_url=relay_url,
        api_key=api_key,
        relay_secret=relay_secret,
        json_body={"participant": participant},
    )


def send_room_message(
    *,
    relay_url: str,
    room: str,
    participant: str,
    content: str,
    message_type: str = "chat",
    api_key: str = "",
    relay_secret: str = "",
) -> dict:
    data = _request_json(
        "POST",
        f"{relay_url}/rooms/{room}/messages",
        relay_url=relay_url,
        api_key=api_key,
        relay_secret=relay_secret,
        json_body={
            "from_name": participant,
            "content": content,
            "message_type": message_type,
        },
    )
    return data if isinstance(data, dict) else {}


def join_room(
    *,
    relay_url: str,
    room: str,
    participant: str,
    api_key: str = "",
    relay_secret: str = "",
) -> dict:
    data = _request_json(
        "POST",
        f"{relay_url}/rooms/{room}/join",
        relay_url=relay_url,
        api_key=api_key,
        relay_secret=relay_secret,
        json_body={"participant": participant},
    )
    return data if isinstance(data, dict) else {}


def register_agent_identity(
    *,
    relay_url: str,
    parent_api_key: str,
    suffix: str,
) -> tuple[str, str]:
    """Register a child agent identity under the parent account."""
    resp = httpx.post(
        f"{relay_url}/v1/auth/register-agent",
        headers={"Authorization": f"Bearer {parent_api_key}"},
        json={"suffix": suffix},
        timeout=15,
        follow_redirects=True,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["agent_name"], data["api_key"]


def resolve_identity(
    *,
    relay_url: str,
    parent_name: str,
    parent_api_key: str,
    requested_name: str | None,
    suffix: str | None,
) -> tuple[str, str]:
    """Resolve or register the Claude agent identity."""
    if requested_name:
        cached_key = _cached_child_api_key(requested_name)
        if cached_key:
            return requested_name, cached_key
        return requested_name, parent_api_key

    agent_suffix = suffix or "claude"
    expected_name = f"{parent_name}-{agent_suffix}"
    cached_key = _cached_child_api_key(expected_name)
    if cached_key:
        return expected_name, cached_key

    try:
        agent_name, agent_key = register_agent_identity(
            relay_url=relay_url,
            parent_api_key=parent_api_key,
            suffix=agent_suffix,
        )
        _save_child_api_key(agent_name, agent_key)
        return agent_name, agent_key
    except Exception:
        # Registration endpoint unavailable — operate under parent identity.
        return expected_name, parent_api_key


def _message_id(msg: dict) -> str:
    return str(msg.get("id", "")) or str(msg.get("timestamp", ""))


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
        f"- A local inbox mirror is available at `{inbox_path}` as fallback context.",
    ]
    if context_path is not None:
        lines.append(f"- A room context snapshot is available at `{context_path}`.")
    lines.extend(
        [
            "- If a concrete task is assigned, acknowledge ownership "
            "clearly in-room before implementing.",
            "- Post concise status updates after meaningful progress, blockers, or completion.",
            f"- Do not impersonate another participant. Only send as `{participant}`.",
            "- Work autonomously on assigned tasks. Build, test, and iterate without waiting.",
            "- When you complete a task, announce it in-room and look for the next one.",
            "- If blocked, post the blocker clearly and move to other work if possible.",
        ]
    )
    return "\n".join(lines)


def _build_mcp_config(
    *,
    participant: str,
    relay_url: str,
    api_key: str = "",
    relay_secret: str = "",
) -> dict:
    """Build MCP config for Quorus server."""
    env = {
        "QUORUS_INSTANCE_NAME": participant,
        "QUORUS_RELAY_URL": relay_url,
    }
    if api_key:
        env["QUORUS_API_KEY"] = api_key
    elif relay_secret:
        env["QUORUS_RELAY_SECRET"] = relay_secret

    mcp_command = shutil.which("quorus-mcp")
    if not mcp_command:
        mcp_command = "quorus-mcp"

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
    """Build the non-interactive Claude -p command for autonomous mode."""
    base_prompt = build_prompt(room, participant, inbox_path, context_path)
    mcp_config = _build_mcp_config(
        participant=participant,
        relay_url=relay_url,
        api_key=api_key,
        relay_secret=relay_secret,
    )

    # Combine system context + task prompt into single prompt argument.
    # --append-system-prompt and --add-dir both break -p / --print mode.
    full_prompt = f"{base_prompt}\n\n{prompt}"

    cmd = [
        "claude",
        "-p",
        "--model", model,
        "--permission-mode", permission_mode,
        "--mcp-config", json.dumps(mcp_config),
        "--output-format", "text",
        full_prompt,
    ]

    return cmd


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
    """Supervise ``claude -p`` runs when room activity changes."""
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
            stop_event.wait(poll_seconds)
            continue

        new_messages = []
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
            sys.stderr.write(f"[quorus claude-agent] Spawning claude -p\n")
            sys.stderr.flush()

        rc = subprocess.call(cmd, cwd=str(cwd) if cwd else None)
        if rc != 0 and verbose:
            sys.stderr.write(f"[quorus claude-agent auto-exec] claude exited {rc}\n")
            sys.stderr.flush()
        stop_event.wait(max(5, poll_seconds))

    return 0


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
) -> int:
    """Join the room, maintain runner state, and launch Claude Code."""
    # Isolate this agent's Quorus config so it never overwrites the human's
    # shared ~/.quorus/profiles/default.json.  Each agent gets its own
    # scratch directory under /tmp; the relay connection details are passed
    # via environment variables so no file-write is needed.
    import tempfile as _tmp
    _agent_config_dir = Path(_tmp.mkdtemp(prefix=f"quorus-agent-"))
    os.environ.setdefault("QUORUS_CONFIG_DIR", str(_agent_config_dir))

    participant, agent_api_key = resolve_identity(
        relay_url=relay_url,
        parent_name=parent_name,
        parent_api_key=parent_api_key,
        requested_name=requested_name,
        suffix=suffix,
    )

    try:
        join_room(
            relay_url=relay_url,
            room=room,
            participant=participant,
            api_key=agent_api_key,
            relay_secret=relay_secret,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            # Room requires invite token for self-join; agent may already be a
            # member or can operate via API key — continue rather than abort.
            if verbose:
                sys.stderr.write(
                    f"[quorus claude-agent] join returned 403 (invite-only room); "
                    "continuing with existing credentials.\n"
                )
                sys.stderr.flush()
        else:
            raise

    if announce:
        send_room_message(
            relay_url=relay_url,
            room=room,
            participant=participant,
            content=f"`{participant}` joined via Claude Code runner.",
            message_type="system",
            api_key=agent_api_key,
            relay_secret=relay_secret,
        )

    inbox_path = Path(f"/tmp/quorus-{participant}-inbox.txt")
    context_path = Path(f"/tmp/quorus-{participant}-context.md")

    stop_event = threading.Event()

    inbox_thread = threading.Thread(
        target=poll_inbox_loop,
        kwargs={
            "relay_url": relay_url,
            "participant": participant,
            "inbox_path": inbox_path,
            "stop_event": stop_event,
            "wait_seconds": wait_seconds,
            "api_key": agent_api_key,
            "relay_secret": relay_secret,
            "verbose": verbose,
        },
        daemon=True,
    )
    inbox_thread.start()

    context_thread = threading.Thread(
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
            "relay_secret": relay_secret,
            "verbose": verbose,
        },
        daemon=True,
    )
    context_thread.start()

    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        kwargs={
            "relay_url": relay_url,
            "room": room,
            "participant": participant,
            "stop_event": stop_event,
            "heartbeat_seconds": heartbeat_seconds,
            "api_key": agent_api_key,
            "relay_secret": relay_secret,
            "verbose": verbose,
        },
        daemon=True,
    )
    heartbeat_thread.start()

    if no_launch:
        sys.stderr.write(
            f"[quorus claude-agent] Runner active for {participant} in {room}. "
            "Press Ctrl+C to stop.\n"
        )
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            stop_event.set()
        return 0

    if autonomous:
        sys.stderr.write(
            f"[quorus claude-agent] Autonomous mode active for {participant} in {room}.\n"
        )
        try:
            return run_autonomous_loop(
                room=room,
                participant=participant,
                relay_url=relay_url,
                api_key=agent_api_key,
                relay_secret=relay_secret,
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
        except KeyboardInterrupt:
            stop_event.set()
            return 0

    cmd = build_claude_command(
        room=room,
        participant=participant,
        relay_url=relay_url,
        api_key=agent_api_key,
        relay_secret=relay_secret,
        cwd=cwd,
        permission_mode=permission_mode,
        inbox_path=inbox_path,
        context_path=context_path,
        model=model,
    )

    try:
        return subprocess.call(cmd, cwd=str(cwd) if cwd else None)
    except KeyboardInterrupt:
        stop_event.set()
        return 0
    finally:
        stop_event.set()
