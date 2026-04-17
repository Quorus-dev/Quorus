"""Codex-specific Quorus runner.

This launches Codex with a room-bound Quorus identity and a lightweight
background inbox loop, without changing existing Claude/Cursor flows.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx


class CodexAgentError(RuntimeError):
    """Raised for recoverable Codex-agent runner failures."""


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

    raise CodexAgentError("Codex runner needs an API key or relay secret.")


def resolve_identity(
    *,
    relay_url: str,
    parent_name: str,
    parent_api_key: str,
    requested_name: str | None,
    suffix: str | None,
) -> tuple[str, str]:
    """Resolve the participant identity and API key for the Codex run."""
    if requested_name and suffix:
        raise CodexAgentError("Pass either --name or --suffix, not both.")

    if not requested_name and not suffix:
        return parent_name, parent_api_key

    if not parent_api_key:
        raise CodexAgentError(
            "Registering child Codex identities requires API-key auth."
        )

    if suffix:
        derived_suffix = suffix.strip()
    else:
        if requested_name == parent_name:
            return parent_name, parent_api_key
        prefix = f"{parent_name}-"
        if not requested_name or not requested_name.startswith(prefix):
            raise CodexAgentError(
                f"--name must equal {parent_name!r} or start with {prefix!r}."
            )
        derived_suffix = requested_name[len(prefix):]

    if not derived_suffix:
        raise CodexAgentError("Derived Codex agent suffix cannot be empty.")

    resp = httpx.post(
        f"{relay_url}/v1/auth/register-agent",
        json={"suffix": derived_suffix},
        headers={"Authorization": f"Bearer {parent_api_key}"},
        timeout=10,
        follow_redirects=True,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["agent_name"], data["api_key"]


def join_room(
    *,
    relay_url: str,
    room: str,
    participant: str,
    api_key: str = "",
    relay_secret: str = "",
) -> None:
    """Ensure the runner identity is a member of the room."""
    resp = httpx.post(
        f"{relay_url}/rooms/{room}/join",
        json={"participant": participant},
        headers=_auth_headers(
            relay_url=relay_url,
            api_key=api_key,
            relay_secret=relay_secret,
        ),
        timeout=10,
        follow_redirects=True,
    )
    resp.raise_for_status()


def send_announcement(
    *,
    relay_url: str,
    room: str,
    participant: str,
    content: str,
    api_key: str = "",
    relay_secret: str = "",
) -> None:
    """Post a status message for the Codex runner."""
    resp = httpx.post(
        f"{relay_url}/rooms/{room}/messages",
        json={
            "from_name": participant,
            "content": content,
            "message_type": "status",
        },
        headers=_auth_headers(
            relay_url=relay_url,
            api_key=api_key,
            relay_secret=relay_secret,
        ),
        timeout=10,
        follow_redirects=True,
    )
    resp.raise_for_status()


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
    """Mirror the participant inbox into a local file for Codex context."""
    headers = _auth_headers(
        relay_url=relay_url,
        api_key=api_key,
        relay_secret=relay_secret,
    )
    inbox_path.parent.mkdir(parents=True, exist_ok=True)

    while not stop_event.is_set():
        try:
            resp = httpx.get(
                f"{relay_url}/messages/{participant}",
                params={"wait": wait_seconds},
                headers=headers,
                timeout=wait_seconds + 10,
                follow_redirects=True,
            )
            resp.raise_for_status()
            messages = resp.json()
            if not isinstance(messages, list) or not messages:
                continue

            with inbox_path.open("a", encoding="utf-8") as handle:
                for msg in messages:
                    sender = msg.get("from_name", "?")
                    room = msg.get("room", "dm")
                    content = msg.get("content", "")
                    ts = str(msg.get("timestamp", ""))[:19]
                    line = f"[{ts}] {sender} ({room}): {content}\n"
                    handle.write(line)
                    if verbose:
                        sys.stderr.write(line)
                        sys.stderr.flush()
        except Exception as exc:  # noqa: BLE001
            if verbose:
                sys.stderr.write(f"[quorus codex-agent] {exc}\n")
                sys.stderr.flush()
            time.sleep(5)


def build_prompt(room: str, participant: str, inbox_path: Path) -> str:
    """Build the initial Codex room prompt."""
    return (
        f"You are `{participant}` in the Quorus room `{room}`.\n\n"
        "Operating rules:\n"
        "- Check Quorus messages and room context before acting.\n"
        "- Use the Quorus MCP tools as the primary read/write path.\n"
        f"- A local inbox mirror is available at `{inbox_path}` as fallback context.\n"
        "- If a concrete task is assigned, acknowledge ownership clearly in-room before implementing.\n"
        "- Post concise status updates after meaningful progress, blockers, or completion.\n"
        f"- Do not impersonate another participant. Only send as `{participant}`."
    )


def build_codex_command(
    *,
    room: str,
    participant: str,
    relay_url: str,
    api_key: str,
    cwd: Path,
    sandbox: str,
    approval: str,
    inbox_path: Path,
) -> list[str]:
    """Build the Codex command line with per-run Quorus overrides."""
    prompt = build_prompt(room, participant, inbox_path)
    return [
        "codex",
        "-C",
        str(cwd),
        "-s",
        sandbox,
        "-a",
        approval,
        "-c",
        f"mcp_servers.quorus.env.QUORUS_INSTANCE_NAME={json.dumps(participant)}",
        "-c",
        f"mcp_servers.quorus.env.QUORUS_API_KEY={json.dumps(api_key)}",
        "-c",
        f"mcp_servers.quorus.env.QUORUS_RELAY_URL={json.dumps(relay_url)}",
        prompt,
    ]


def run_codex_agent(
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
) -> int:
    """Join the room, start inbox mirroring, and optionally launch Codex."""
    participant, agent_api_key = resolve_identity(
        relay_url=relay_url,
        parent_name=parent_name,
        parent_api_key=parent_api_key,
        requested_name=requested_name,
        suffix=suffix,
    )
    effective_secret = relay_secret if not agent_api_key else ""

    join_room(
        relay_url=relay_url,
        room=room,
        participant=participant,
        api_key=agent_api_key,
        relay_secret=effective_secret,
    )

    if announce:
        send_announcement(
            relay_url=relay_url,
            room=room,
            participant=participant,
            content=f"{participant} online. Codex runner attached and ready for room work.",
            api_key=agent_api_key,
            relay_secret=effective_secret,
        )

    inbox_path = Path(f"/tmp/quorus-{participant}-inbox.txt")
    stop_event = threading.Event()
    watcher = threading.Thread(
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
    )
    watcher.start()

    print(f"Quorus room:   {room}")
    print(f"Participant:   {participant}")
    print(f"Inbox mirror:  {inbox_path}")
    print(f"Relay:         {relay_url}")

    try:
        if no_launch:
            print("Inbox loop active. Press Ctrl+C to stop.")
            while not stop_event.is_set():
                time.sleep(1)
            return 0

        if not agent_api_key:
            raise CodexAgentError(
                "Launching Codex with Quorus overrides requires API-key auth."
            )

        cmd = build_codex_command(
            room=room,
            participant=participant,
            relay_url=relay_url,
            api_key=agent_api_key,
            cwd=cwd,
            sandbox=sandbox,
            approval=approval,
            inbox_path=inbox_path,
        )
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        return 130
    finally:
        stop_event.set()
        watcher.join(timeout=2)
