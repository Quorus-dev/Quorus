"""Gemini-specific Quorus runner.

This keeps Gemini additive to Quorus rather than changing Claude/Gemini paths.
It launches the user's installed Gemini CLI locally and lets Quorus provide
room identity, inbox mirroring, and coordination.

It supports:

- room-bound Gemini identities
- inbox mirroring for direct messages
- room state/history context snapshots
- presence heartbeats so the runner stays visible in room state
- optional supervised autonomous mode powered by ``gemini exec``
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

from quorus.profiles import ProfileManager

DEFAULT_HISTORY_LIMIT = 25


class GeminiAgentError(RuntimeError):
    """Raised for recoverable Gemini-agent runner failures."""


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

    raise GeminiAgentError(
        "Gemini runner needs Quorus relay auth (workspace API key or relay secret)."
    )


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


def resolve_identity(
    *,
    relay_url: str,
    parent_name: str,
    parent_api_key: str,
    requested_name: str | None,
    suffix: str | None,
) -> tuple[str, str]:
    """Resolve the participant identity and API key for the Gemini run."""
    if requested_name and suffix:
        raise GeminiAgentError("Pass either --name or --suffix, not both.")

    if not requested_name and not suffix:
        return parent_name, parent_api_key

    if not parent_api_key:
        raise GeminiAgentError(
            "Registering child Gemini identities requires Quorus workspace auth."
        )

    if suffix:
        derived_suffix = suffix.strip()
    else:
        if requested_name == parent_name:
            return parent_name, parent_api_key
        prefix = f"{parent_name}-"
        if not requested_name or not requested_name.startswith(prefix):
            raise GeminiAgentError(
                f"--name must equal {parent_name!r} or start with {prefix!r}."
            )
        derived_suffix = requested_name[len(prefix):]

    if not derived_suffix:
        raise GeminiAgentError("Derived Gemini agent suffix cannot be empty.")

    agent_name = f"{parent_name}-{derived_suffix}"
    cached_key = _cached_child_api_key(agent_name)
    if cached_key:
        return agent_name, cached_key

    try:
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
    except Exception:
        # Registration endpoint unavailable — operate under parent identity.
        return agent_name, parent_api_key


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
    """Post a status message for the Gemini runner."""
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
    """Keep the Gemini runner visible in room presence."""
    _request_json(
        "POST",
        f"{relay_url}/heartbeat",
        relay_url=relay_url,
        api_key=api_key,
        relay_secret=relay_secret,
        json_body={"instance_name": participant, "status": "online", "room": room},
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
    limit: int,
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


def _message_id(message: dict) -> str:
    value = message.get("message_id") or message.get("id") or message.get("uuid") or ""
    return str(value)


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


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
    """Mirror the participant inbox into a local file for Gemini context."""
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
                sys.stderr.write(f"[quorus gemini-agent inbox] {exc}\n")
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
                sys.stderr.write(f"[quorus gemini-agent context] {exc}\n")
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
                sys.stderr.write(f"[quorus gemini-agent heartbeat] {exc}\n")
                sys.stderr.flush()
        stop_event.wait(heartbeat_seconds)


def build_prompt(
    room: str,
    participant: str,
    inbox_path: Path,
    context_path: Path | None = None,
) -> str:
    """Build the initial Gemini room prompt."""
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
        ]
    )
    return "\n".join(lines)


def _gemini_base_env(
    participant: str,
    relay_url: str,
    api_key: str = "",
    relay_secret: str = "",
) -> dict:
    import os
    env = os.environ.copy()
    env["QUORUS_INSTANCE_NAME"] = participant
    env["QUORUS_RELAY_URL"] = relay_url
    if api_key:
        env["QUORUS_API_KEY"] = api_key
    elif relay_secret:
        env["QUORUS_RELAY_SECRET"] = relay_secret
    else:
        raise GeminiAgentError(
            "Gemini launch needs Quorus relay auth (workspace API key or relay secret)."
        )
    return env


def build_gemini_command(
    *,
    room: str,
    participant: str,
    relay_url: str,
    api_key: str = "",
    relay_secret: str = "",
    cwd: Path,
    sandbox: str,
    approval: str,
    inbox_path: Path,
    context_path: Path | None = None,
) -> tuple[list[str], dict]:
    """Build the interactive Gemini command line with Quorus overrides."""
    prompt = build_prompt(room, participant, inbox_path, context_path)
    env = _gemini_base_env(participant, relay_url, api_key, relay_secret)
    cmd = ["gemini", "--approval-mode", "default", "-i", prompt]
    return cmd, env


def build_gemini_exec_command(
    *,
    room: str,
    participant: str,
    relay_url: str,
    api_key: str = "",
    relay_secret: str = "",
    cwd: Path,
    sandbox: str,
    inbox_path: Path,
    context_path: Path,
    prompt: str,
) -> tuple[list[str], dict]:
    """Build a non-interactive Gemini command for supervised autonomous turns."""
    env = _gemini_base_env(participant, relay_url, api_key, relay_secret)
    full_prompt = build_prompt(room, participant, inbox_path, context_path) + "\n\n" + prompt
    cmd = ["gemini", "--approval-mode", "yolo", "-p", full_prompt]
    return cmd, env


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
            "task or blocker you should own, acknowledge it in-room and proceed."
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
        f"{summarized}"
    )


def run_autonomous_loop(
    *,
    room: str,
    participant: str,
    relay_url: str,
    api_key: str,
    relay_secret: str,
    cwd: Path,
    sandbox: str,
    inbox_path: Path,
    context_path: Path,
    history_limit: int,
    poll_seconds: int,
    stop_event: threading.Event,
    verbose: bool,
) -> int:
    """Supervise ``gemini exec`` runs when room activity changes."""
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
                sys.stderr.write(f"[quorus gemini-agent auto-sync] {exc}\n")
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
        cmd, env = build_gemini_exec_command(
            room=room,
            participant=participant,
            relay_url=relay_url,
            api_key=api_key,
            relay_secret=relay_secret,
            cwd=cwd,
            sandbox=sandbox,
            inbox_path=inbox_path,
            context_path=context_path,
            prompt=prompt,
        )
        rc = subprocess.call(cmd, env=env, cwd=cwd)
        if rc != 0 and verbose:
            sys.stderr.write(f"[quorus gemini-agent auto-exec] gemini exited {rc}\n")
            sys.stderr.flush()
        stop_event.wait(max(5, poll_seconds))

    return 0


def run_gemini_agent(
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
    sandbox: str,
    approval: str,
    autonomous: bool,
    room_poll_seconds: int,
    heartbeat_seconds: int,
    history_limit: int,
) -> int:
    """Join the room, maintain runner state, and launch Gemini."""
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
    effective_secret = relay_secret if not agent_api_key else ""

    try:
        join_room(
            relay_url=relay_url,
            room=room,
            participant=participant,
            api_key=agent_api_key,
            relay_secret=effective_secret,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 403:
            raise
        # Room requires an invite token for self-join. Try adding the child
        # participant via the parent identity; if that also fails (or participant
        # is the parent itself), continue with existing credentials rather than
        # aborting — the agent may still be able to operate.
        if parent_api_key and participant != parent_name:
            try:
                parent_join_room(
                    relay_url=relay_url,
                    room=room,
                    participant=participant,
                    parent_api_key=parent_api_key,
                )
            except Exception:
                if verbose:
                    sys.stderr.write(
                        "[quorus gemini-agent] parent join also failed; "
                        "continuing with existing credentials.\n"
                    )
                    sys.stderr.flush()
        else:
            if verbose:
                sys.stderr.write(
                    "[quorus gemini-agent] join returned 403 (invite-only room); "
                    "continuing with existing credentials.\n"
                )
                sys.stderr.flush()

    if announce:
        send_announcement(
            relay_url=relay_url,
            room=room,
            participant=participant,
            content=f"{participant} online. Gemini runner attached and ready for room work.",
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

    try:
        if no_launch:
            print("Quorus loops active. Press Ctrl+C to stop.")
            while not stop_event.is_set():
                time.sleep(1)
            return 0

        if autonomous:
            return run_autonomous_loop(
                room=room,
                participant=participant,
                relay_url=relay_url,
                api_key=agent_api_key,
                relay_secret=effective_secret,
                cwd=cwd,
                sandbox=sandbox,
                inbox_path=inbox_path,
                context_path=context_path,
                history_limit=history_limit,
                poll_seconds=room_poll_seconds,
                stop_event=stop_event,
                verbose=verbose,
            )

        cmd, env = build_gemini_command(
            room=room,
            participant=participant,
            relay_url=relay_url,
            api_key=agent_api_key,
            relay_secret=effective_secret,
            cwd=cwd,
            sandbox=sandbox,
            approval=approval,
            inbox_path=inbox_path,
            context_path=context_path,
        )
        return subprocess.call(cmd, env=env, cwd=cwd)
    except KeyboardInterrupt:
        return 130
    finally:
        stop_event.set()
        for thread in threads:
            thread.join(timeout=2)
