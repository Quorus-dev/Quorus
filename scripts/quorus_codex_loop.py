#!/usr/bin/env python3
"""Launch Codex with a Quorus polling loop bound to a room and identity.

This is a Codex-specific fallback for Quorus because Quorus' built-in
`hook enable` currently targets Claude Code, not Codex. The script:

1. Reads the existing Quorus MCP config from `~/.codex/config.toml`
2. Optionally registers a child Codex agent identity with its own API key
3. Joins the requested room using that exact identity
4. Starts a long-poll inbox loop writing to `/tmp/quorus-<name>-inbox.txt`
5. Launches Codex with per-run MCP overrides for that Quorus identity

No secrets are written to disk beyond Quorus' existing config.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import tomllib

CODEX_CONFIG = Path.home() / ".codex" / "config.toml"


class QuorusConfigError(RuntimeError):
    """Raised when the local Quorus/Codex setup is incomplete."""


def load_codex_quorus_env() -> dict[str, str]:
    if not CODEX_CONFIG.exists():
        raise QuorusConfigError(f"Missing Codex config: {CODEX_CONFIG}")

    data = tomllib.loads(CODEX_CONFIG.read_text())
    env = (
        data.get("mcp_servers", {})
        .get("quorus", {})
        .get("env", {})
    )

    relay_url = env.get("QUORUS_RELAY_URL")
    instance_name = env.get("QUORUS_INSTANCE_NAME")
    api_key = env.get("QUORUS_API_KEY")
    if not relay_url or not instance_name or not api_key:
        raise QuorusConfigError(
            "Expected `mcp_servers.quorus.env` to define "
            "`QUORUS_RELAY_URL`, `QUORUS_INSTANCE_NAME`, and `QUORUS_API_KEY`."
        )

    return {
        "relay_url": relay_url.rstrip("/"),
        "instance_name": instance_name,
        "api_key": api_key,
    }


def http_json(
    method: str,
    url: str,
    *,
    api_key: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any] | list[Any]:
    body = None
    headers = {"Authorization": f"Bearer {api_key}"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed: HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{method} {url} failed: {exc.reason}") from exc


def ensure_identity(
    *,
    relay_url: str,
    parent_name: str,
    parent_api_key: str,
    child_name: str | None,
    child_suffix: str | None,
) -> tuple[str, str]:
    if not child_name and not child_suffix:
        return parent_name, parent_api_key

    if child_name and child_suffix:
        raise QuorusConfigError("Pass either --name or --suffix, not both.")

    if child_suffix:
        suffix = child_suffix
    else:
        prefix = f"{parent_name}-"
        if not child_name.startswith(prefix):
            raise QuorusConfigError(
                f"--name must start with {prefix!r} so a suffix can be derived."
            )
        suffix = child_name[len(prefix):]
        if not suffix:
            raise QuorusConfigError("Derived suffix was empty.")

    response = http_json(
        "POST",
        f"{relay_url}/v1/auth/register-agent",
        api_key=parent_api_key,
        payload={"suffix": suffix},
    )
    agent_name = response["agent_name"]
    agent_api_key = response["api_key"]
    return agent_name, agent_api_key


def join_room(*, relay_url: str, api_key: str, room: str, participant: str) -> None:
    http_json(
        "POST",
        f"{relay_url}/rooms/{room}/join",
        api_key=api_key,
        payload={"participant": participant},
    )


def send_room_message(
    *,
    relay_url: str,
    api_key: str,
    room: str,
    participant: str,
    content: str,
    message_type: str = "status",
) -> None:
    http_json(
        "POST",
        f"{relay_url}/rooms/{room}/messages",
        api_key=api_key,
        payload={
            "from_name": participant,
            "content": content,
            "message_type": message_type,
        },
    )


def poll_inbox_loop(
    *,
    relay_url: str,
    api_key: str,
    participant: str,
    inbox_path: Path,
    stop_event: threading.Event,
    wait_seconds: int,
    verbose: bool,
) -> None:
    inbox_path.parent.mkdir(parents=True, exist_ok=True)
    while not stop_event.is_set():
        try:
            messages = http_json(
                "GET",
                f"{relay_url}/messages/{participant}?wait={wait_seconds}",
                api_key=api_key,
                timeout=wait_seconds + 10,
            )
            if not isinstance(messages, list):
                time.sleep(2)
                continue

            if messages:
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
        except Exception as exc:
            if verbose:
                sys.stderr.write(f"[quorus-loop] {exc}\n")
                sys.stderr.flush()
            time.sleep(5)


def build_prompt(room: str, participant: str, inbox_path: Path) -> str:
    return textwrap.dedent(
        f"""
        You are `{participant}` in the Quorus room `{room}`.

        Operating rules:
        - At the start of each turn, check Quorus messages and room context before acting.
        - Use the Quorus MCP tools as your primary interface for reading and replying.
        - A local inbox mirror is being written to `{inbox_path}`. Use it as
          fallback context if needed.
        - If someone assigns a concrete task, acknowledge ownership clearly
          in-room before implementing.
        - Post concise status updates back to the room after meaningful
          progress, blockers, or completion.
        - Do not impersonate another participant. Only send as `{participant}`.
        """
    ).strip()


def launch_codex(
    *,
    cwd: Path,
    participant: str,
    api_key: str,
    relay_url: str,
    prompt: str,
) -> int:
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tmp:
        tmp.write(prompt)
        prompt_path = tmp.name

    cmd = [
        "codex",
        "-C",
        str(cwd),
        "-c",
        f'mcp_servers.quorus.env.QUORUS_INSTANCE_NAME="{participant}"',
        "-c",
        f'mcp_servers.quorus.env.QUORUS_API_KEY="{api_key}"',
        "-c",
        f'mcp_servers.quorus.env.QUORUS_RELAY_URL="{relay_url}"',
        prompt,
    ]

    try:
        return subprocess.call(cmd)
    finally:
        try:
            os.unlink(prompt_path)
        except OSError:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch Codex with a Quorus inbox loop and room-bound identity.",
    )
    parser.add_argument("room", help="Quorus room id or room name")
    parser.add_argument(
        "--name",
        help=(
            "Full participant name to use. If different from the current Codex "
            "identity, it must be a child name like arav-codex-research."
        ),
    )
    parser.add_argument(
        "--suffix",
        help=(
            "Register/use a child agent derived from the current Codex identity, "
            "for example `reviewer` -> `arav-codex-reviewer`."
        ),
    )
    parser.add_argument(
        "--cwd",
        default=".",
        help="Working directory to open Codex in (default: current directory).",
    )
    parser.add_argument(
        "--wait",
        type=int,
        default=90,
        help="Long-poll wait in seconds for each inbox request (default: 90).",
    )
    parser.add_argument(
        "--announce",
        action="store_true",
        help="Post an initial online status message to the room before launching Codex.",
    )
    parser.add_argument(
        "--no-launch",
        action="store_true",
        help="Start the inbox loop and join the room, but do not launch Codex.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print mirrored inbox messages and loop errors to stderr.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base = load_codex_quorus_env()
    relay_url = base["relay_url"]
    parent_name = base["instance_name"]
    parent_api_key = base["api_key"]

    participant, api_key = ensure_identity(
        relay_url=relay_url,
        parent_name=parent_name,
        parent_api_key=parent_api_key,
        child_name=args.name,
        child_suffix=args.suffix,
    )

    join_room(
        relay_url=relay_url,
        api_key=api_key,
        room=args.room,
        participant=participant,
    )

    if args.announce:
        send_room_message(
            relay_url=relay_url,
            api_key=api_key,
            room=args.room,
            participant=participant,
            content=f"{participant} online. Inbox loop attached and ready for room work.",
        )

    inbox_path = Path(f"/tmp/quorus-{participant}-inbox.txt")
    stop_event = threading.Event()
    watcher = threading.Thread(
        target=poll_inbox_loop,
        kwargs={
            "relay_url": relay_url,
            "api_key": api_key,
            "participant": participant,
            "inbox_path": inbox_path,
            "stop_event": stop_event,
            "wait_seconds": args.wait,
            "verbose": args.verbose,
        },
        daemon=True,
    )
    watcher.start()

    def _shutdown(_signum: int, _frame: Any) -> None:
        stop_event.set()
        raise SystemExit(130)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print(f"Quorus room:   {args.room}")
    print(f"Participant:   {participant}")
    print(f"Inbox mirror:  {inbox_path}")
    print(f"Relay:         {relay_url}")

    if args.no_launch:
        print("Loop active. Press Ctrl+C to stop.")
        try:
            while not stop_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            stop_event.set()
        return 0

    prompt = build_prompt(args.room, participant, inbox_path)
    try:
        return launch_codex(
            cwd=Path(args.cwd).resolve(),
            participant=participant,
            api_key=api_key,
            relay_url=relay_url,
            prompt=prompt,
        )
    finally:
        stop_event.set()
        watcher.join(timeout=2)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except QuorusConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except RuntimeError as exc:
        print(f"Quorus error: {exc}", file=sys.stderr)
        raise SystemExit(1)
