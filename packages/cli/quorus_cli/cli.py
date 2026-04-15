"""Quorus CLI — coordination layer for AI agent swarms."""

import argparse
import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from quorus.config import load_config

console = Console()
_config = load_config()
RELAY_URL = _config["relay_url"]
RELAY_SECRET = _config["relay_secret"]
API_KEY = _config.get("api_key", "")
INSTANCE_NAME = _config["instance_name"]

# JWT cache for API key auth
_cached_jwt: str | None = None


def _relay_unreachable(url: str = "") -> None:
    """Print a helpful error when the relay is not reachable."""
    from quorus_cli import ui

    target = url or RELAY_URL
    ui.error(
        f"Cannot connect to relay at [primary]{target}[/]",
        hint="start it with [accent]quorus relay[/] — or run [accent]quorus doctor[/]",
    )


def _config_dir() -> Path:
    """Return the config dir, honoring QUORUS_CONFIG_DIR / MCP_TUNNEL_CONFIG_DIR.

    Falls back to ``~/.quorus``. Used by commands that WRITE config (init,
    connect, join, add-agent) so they stay in sync with ``resolve_config_file``
    which is used to READ config.
    """
    override = (
        os.environ.get("QUORUS_CONFIG_DIR")
        or os.environ.get("MCP_TUNNEL_CONFIG_DIR")
    )
    if override:
        return Path(override).expanduser()
    return Path.home() / ".quorus"


def _write_sensitive_json(path: Path, data: dict) -> None:
    """Write JSON config atomically with 0600 perms (no TOCTOU window).

    Using `path.write_text` then `chmod(0600)` leaves a window where the
    file is readable by other users on shared hosts. Create with the
    right mode from the start using os.open.
    """
    import os as _os
    flags = _os.O_WRONLY | _os.O_CREAT | _os.O_TRUNC
    fd = _os.open(str(path), flags, 0o600)
    try:
        with _os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
    except BaseException:
        try:
            _os.close(fd)
        except OSError:
            pass
        raise


def _get_client() -> httpx.AsyncClient:
    return httpx.AsyncClient()


def _auth_headers() -> dict[str, str]:
    """Get auth headers — JWT from API key, or legacy secret for local dev."""
    global _cached_jwt
    if API_KEY:
        if not _cached_jwt:
            resp = httpx.post(
                f"{RELAY_URL}/v1/auth/token",
                json={"api_key": API_KEY},
                timeout=10,
            )
            resp.raise_for_status()
            _cached_jwt = resp.json()["token"]
        return {"Authorization": f"Bearer {_cached_jwt}"}
    return {"Authorization": f"Bearer {RELAY_SECRET}"}


def _get_sse_token(recipient: str) -> str:
    """Get a short-lived SSE stream token."""
    headers = _auth_headers()
    try:
        resp = httpx.post(
            f"{RELAY_URL}/stream/token",
            json={"recipient": recipient},
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()["token"]
    except Exception:
        pass
    # Fallback: use the bearer token directly
    return headers["Authorization"][7:]  # strip "Bearer "


async def _list_rooms() -> list[dict]:
    client = _get_client()
    try:
        resp = await client.get(f"{RELAY_URL}/rooms", headers=_auth_headers())
        resp.raise_for_status()
        return resp.json()
    finally:
        await client.aclose()


async def _create_room(name: str) -> dict:
    client = _get_client()
    try:
        resp = await client.post(
            f"{RELAY_URL}/rooms",
            json={"name": name, "created_by": INSTANCE_NAME},
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        return resp.json()
    finally:
        await client.aclose()


async def _invite(room_name: str, participants: list[str]) -> None:
    client = _get_client()
    try:
        rooms = await _list_rooms_with_client(client)
        room = next((r for r in rooms if r["name"] == room_name), None)
        if not room:
            console.print(f"[red]Room '{room_name}' not found[/red]")
            return
        for p in participants:
            resp = await client.post(
                f"{RELAY_URL}/rooms/{room['id']}/join",
                json={"participant": p},
                headers=_auth_headers(),
            )
            resp.raise_for_status()
            console.print(f"  [green]+ {p}[/green] joined {room_name}")
    finally:
        await client.aclose()


async def _list_rooms_with_client(client: httpx.AsyncClient) -> list[dict]:
    resp = await client.get(f"{RELAY_URL}/rooms", headers=_auth_headers())
    resp.raise_for_status()
    return resp.json()


async def _members(room_name: str) -> list[str]:
    rooms = await _list_rooms()
    room = next((r for r in rooms if r["name"] == room_name), None)
    if not room:
        return []
    return room["members"]


async def _say(room_name: str, message: str) -> dict:
    client = _get_client()
    try:
        rooms_resp = await client.get(
            f"{RELAY_URL}/rooms", headers=_auth_headers()
        )
        rooms_resp.raise_for_status()
        rooms = rooms_resp.json()
        room = next((r for r in rooms if r["name"] == room_name), None)
        if not room:
            raise ValueError(f"Room '{room_name}' not found")
        resp = await client.post(
            f"{RELAY_URL}/rooms/{room['id']}/messages",
            json={"from_name": INSTANCE_NAME, "content": message},
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        return resp.json()
    finally:
        await client.aclose()


async def _dm(to: str, message: str) -> dict:
    client = _get_client()
    try:
        resp = await client.post(
            f"{RELAY_URL}/messages",
            json={"from_name": INSTANCE_NAME, "to": to, "content": message},
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        return resp.json()
    finally:
        await client.aclose()


async def _watch(room_name: str) -> None:
    """Stream messages from a room via SSE."""
    rooms = await _list_rooms()
    room = next((r for r in rooms if r["name"] == room_name), None)
    if not room:
        console.print(f"[red]Room '{room_name}' not found[/red]")
        return

    console.print(f"[bold]Watching room: {room_name}[/bold]")
    console.print(f"[dim]Members: {', '.join(room['members'])}[/dim]")
    console.print("─" * 60)

    client = httpx.AsyncClient()
    try:
        url = f"{RELAY_URL}/stream/{INSTANCE_NAME}"
        sse_token = _get_sse_token(INSTANCE_NAME)
        async with client.stream(
            "GET", url, params={"token": sse_token}, timeout=None
        ) as resp:
            event_type = ""
            event_data = ""
            async for line in resp.aiter_lines():
                line = line.strip()
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    event_data = line[5:].strip()
                elif line == "" and event_type:
                    if event_type == "message":
                        try:
                            msg = json.loads(event_data)
                            if msg.get("room") == room_name:
                                console.print(_format_chat_msg(msg))
                        except json.JSONDecodeError:
                            pass
                    event_type = ""
                    event_data = ""
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped watching.[/dim]")
    finally:
        await client.aclose()


async def _status() -> None:
    """Show relay health, room stats, and participant info."""
    client = _get_client()
    try:
        # Fetch health
        health_resp = await client.get(
            f"{RELAY_URL}/health", headers=_auth_headers()
        )
        health_resp.raise_for_status()
        health = health_resp.json()

        # Fetch analytics
        analytics_resp = await client.get(
            f"{RELAY_URL}/analytics", headers=_auth_headers()
        )
        analytics_resp.raise_for_status()
        stats = analytics_resp.json()

        # Fetch rooms
        rooms_resp = await client.get(
            f"{RELAY_URL}/rooms", headers=_auth_headers()
        )
        rooms_resp.raise_for_status()
        room_list = rooms_resp.json()

        # Fetch participants
        parts_resp = await client.get(
            f"{RELAY_URL}/participants", headers=_auth_headers()
        )
        parts_resp.raise_for_status()
        participants = parts_resp.json()

        # Display
        console.print("[bold green]Quorus Relay Status[/bold green]")
        console.print(f"  URL: {RELAY_URL}")
        console.print(f"  Health: [green]{health['status']}[/green]")
        uptime_s = stats.get("uptime_seconds", 0)
        uptime_m = uptime_s // 60
        uptime_h = uptime_m // 60
        if uptime_h > 0:
            console.print(f"  Uptime: {uptime_h}h {uptime_m % 60}m")
        else:
            console.print(f"  Uptime: {uptime_m}m {uptime_s % 60}s")
        console.print()

        # Messages stats
        console.print("[bold]Messages[/bold]")
        console.print(f"  Sent: {stats['total_messages_sent']}")
        console.print(f"  Delivered: {stats['total_messages_delivered']}")
        console.print(f"  Pending: {stats['messages_pending']}")
        console.print()

        # Rooms table
        if room_list:
            table = Table(title="Rooms")
            table.add_column("Name", style="bold")
            table.add_column("Members", style="cyan")
            table.add_column("Count", justify="right")
            for r in room_list:
                table.add_row(
                    r["name"],
                    ", ".join(r["members"]),
                    str(len(r["members"])),
                )
            console.print(table)
        else:
            console.print("[dim]No rooms.[/dim]")

        console.print()

        # Participants
        if participants:
            console.print(f"[bold]Participants[/bold] ({len(participants)})")
            for p in participants:
                console.print(f"  {p}")
        else:
            console.print("[dim]No participants.[/dim]")

    except httpx.ConnectError:
        _relay_unreachable()
    finally:
        await client.aclose()


def _format_chat_msg(msg: dict) -> str:
    """Format a single chat message for display."""
    msg_type = msg.get("message_type", "chat")
    sender = msg.get("from_name", "?")
    content = msg.get("content", "")
    ts = msg.get("timestamp", "")[:19]
    type_color = {
        "claim": "yellow",
        "status": "cyan",
        "request": "magenta",
        "alert": "red",
        "sync": "green",
    }.get(msg_type, "white")
    tag = f" [{type_color}][{msg_type}][/{type_color}]" if msg_type != "chat" else ""
    return f"[dim]{ts}[/dim] [bold]{sender}[/bold]{tag} {content}"


async def _chat(room_name: str) -> None:
    """Interactive chat mode: SSE messages stream in, user types to send."""
    rooms = await _list_rooms()
    room = next((r for r in rooms if r["name"] == room_name), None)
    if not room:
        console.print(f"[red]Room '{room_name}' not found[/red]")
        return

    room_id = room["id"]
    console.print(f"[bold green]Quorus Chat — {room_name}[/bold green]")
    console.print(f"[dim]Members: {', '.join(room['members'])}[/dim]")
    console.print(f"[dim]You are: {INSTANCE_NAME}[/dim]")
    console.print("[dim]Type a message and press Enter to send. Ctrl+C to quit.[/dim]")
    console.print("─" * 60)

    stop_event = asyncio.Event()
    chat_console = Console()

    async def _stream_messages():
        """Background task: stream SSE messages and print them."""
        client = httpx.AsyncClient()
        try:
            url = f"{RELAY_URL}/stream/{INSTANCE_NAME}"
            sse_token = _get_sse_token(INSTANCE_NAME)
            async with client.stream(
                "GET", url, params={"token": sse_token}, timeout=None,
            ) as resp:
                event_type = ""
                event_data = ""
                async for line in resp.aiter_lines():
                    if stop_event.is_set():
                        break
                    line = line.strip()
                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        event_data = line[5:].strip()
                    elif line == "" and event_type:
                        if event_type == "message":
                            try:
                                msg = json.loads(event_data)
                                if msg.get("room") == room_name:
                                    chat_console.print(
                                        _format_chat_msg(msg)
                                    )
                            except json.JSONDecodeError:
                                pass
                        event_type = ""
                        event_data = ""
        except (httpx.RemoteProtocolError, httpx.ReadError):
            if not stop_event.is_set():
                chat_console.print("[red]Connection lost. Reconnecting...[/red]")
        finally:
            await client.aclose()

    async def _send_message(text: str):
        """Send a message to the room."""
        client = _get_client()
        try:
            await client.post(
                f"{RELAY_URL}/rooms/{room_id}/messages",
                json={"from_name": INSTANCE_NAME, "content": text},
                headers=_auth_headers(),
            )
        finally:
            await client.aclose()

    async def _input_loop():
        """Read user input in a thread, send messages async."""
        loop = asyncio.get_event_loop()
        while not stop_event.is_set():
            try:
                text = await loop.run_in_executor(None, sys.stdin.readline)
                text = text.strip()
                if not text:
                    continue
                await _send_message(text)
            except (EOFError, KeyboardInterrupt):
                stop_event.set()
                break

    stream_task = asyncio.create_task(_stream_messages())
    input_task = asyncio.create_task(_input_loop())

    try:
        await asyncio.gather(stream_task, input_task)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        stop_event.set()
        stream_task.cancel()
        input_task.cancel()
        console.print("\n[dim]Left chat.[/dim]")


def _cmd_chat(args):
    try:
        asyncio.run(_chat(args.room))
    except KeyboardInterrupt:
        console.print("\n[dim]Left chat.[/dim]")


def _cmd_rooms(args):
    from quorus_cli import ui
    try:
        with ui.spinner("Fetching rooms"):
            rooms = asyncio.run(_list_rooms())
    except httpx.ConnectError:
        _relay_unreachable()
        sys.exit(1)

    if not rooms:
        ui.info("No rooms yet")
        ui.console.print("  [muted]Create one: [accent]quorus create <name>[/][/]")
        return

    table = Table(
        title="[heading]Rooms[/]",
        border_style=ui.PRIMARY,
        header_style=f"bold {ui.PRIMARY}",
        title_justify="left",
        show_edge=True,
    )
    table.add_column("Room", style=f"bold {ui.ROOM}")
    table.add_column("Members", style=ui.MUTED)
    table.add_column("ID", style="dim")
    for r in rooms:
        members_list = r.get("members", [])
        member_str = ", ".join(f"@{m}" for m in members_list) if members_list else "—"
        table.add_row(
            f"#{r['name']}",
            member_str,
            r["id"][:8],
        )
    ui.console.print(table)


def _cmd_create(args):
    from quorus_cli import ui
    try:
        with ui.spinner(f"Creating room #{args.name}"):
            room = asyncio.run(_create_room(args.name))
        ui.success(f"Created room [room]#{room['name']}[/]", hint=f"id: {room['id'][:8]}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 409:
            ui.error(f"Room '{args.name}' already exists", hint="pick a different name")
        elif e.response.status_code == 401:
            ui.error("Not authenticated", hint="run: quorus init <name> --secret <s>")
        else:
            ui.error("Could not create room", hint=f"server returned HTTP {e.response.status_code}")
        sys.exit(1)
    except httpx.ConnectError:
        _relay_unreachable()
        sys.exit(1)


def _cmd_invite(args):
    from quorus_cli import ui
    try:
        with ui.spinner(f"Inviting to #{args.room}"):
            asyncio.run(_invite(args.room, args.participants))
        ui.success(
            f"Invited {len(args.participants)} agent(s) to [room]#{args.room}[/]"
        )
    except httpx.HTTPStatusError as e:
        ui.error("Invite failed", hint=f"HTTP {e.response.status_code}")
        sys.exit(1)
    except httpx.ConnectError:
        _relay_unreachable()
        sys.exit(1)


def _cmd_members(args):
    from quorus_cli import ui
    members = asyncio.run(_members(args.room))
    if not members:
        ui.warn(f"Room '{args.room}' not found or empty", hint="list rooms: quorus rooms")
        return
    ui.heading(f"Members of #{args.room}")
    for m in members:
        ui.console.print(f"  [agent]@{m}[/]")


def _cmd_say(args):
    from quorus_cli import ui
    try:
        with ui.spinner(f"Sending to #{args.room}"):
            asyncio.run(_say(args.room, args.message))
        ui.success(f"Message sent to [room]#{args.room}[/]")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            ui.error(f"Room '{args.room}' not found", hint="list rooms: quorus rooms")
        elif e.response.status_code == 403:
            ui.error(f"Not a member of #{args.room}", hint=f"join first: quorus join {args.room}")
        else:
            ui.error("Send failed", hint=f"HTTP {e.response.status_code}")
        sys.exit(1)
    except httpx.ConnectError:
        _relay_unreachable()
        sys.exit(1)


def _cmd_dm(args):
    from quorus_cli import ui
    try:
        with ui.spinner(f"Sending DM to @{args.to}"):
            asyncio.run(_dm(args.to, args.message))
        ui.success(f"DM sent to [agent]@{args.to}[/]")
    except httpx.HTTPStatusError as e:
        ui.error("DM failed", hint=f"HTTP {e.response.status_code}")
        sys.exit(1)
    except httpx.ConnectError:
        _relay_unreachable()
        sys.exit(1)


def _cmd_inbox(args):
    """Check for pending messages (for hooks or manual use)."""
    try:
        resp = httpx.get(
            f"{RELAY_URL}/messages/{INSTANCE_NAME}/peek",
            headers=_auth_headers(),
            timeout=5,
        )
        resp.raise_for_status()
    except httpx.ConnectError:
        # Silently exit if relay unreachable (don't block Claude)
        return
    except httpx.HTTPStatusError:
        return

    data = resp.json()
    count = data.get("count", 0)
    if count == 0:
        return  # Silent exit, no messages

    # Fetch actual messages to display
    try:
        resp = httpx.get(
            f"{RELAY_URL}/messages/{INSTANCE_NAME}",
            params={"ack": "manual"},
            headers=_auth_headers(),
            timeout=5,
        )
        resp.raise_for_status()
        result = resp.json()
        messages = result.get("messages", [])
        ack_token = result.get("ack_token")
    except (httpx.ConnectError, httpx.HTTPStatusError):
        return

    if not messages:
        return

    if getattr(args, "json", False):
        print(json.dumps(messages))
    else:
        prefix = "" if getattr(args, "quiet", False) else "[quorus] "
        msg_count = len(messages)
        print(f"{prefix}{msg_count} new message{'s' if msg_count != 1 else ''}:")

        for msg in messages:
            ts = msg.get("timestamp", "")[:16].replace("T", " ")  # "2026-04-12 12:34"
            time_part = ts.split(" ")[1] if " " in ts else ts
            sender = msg.get("from_name", "unknown")
            content = msg.get("content", "")
            # Truncate long messages
            if len(content) > 100:
                content = content[:97] + "..."
            print(f"- {sender} ({time_part}): {content}")

    # ACK messages after successfully printing (prevents redelivery)
    if ack_token:
        try:
            httpx.post(
                f"{RELAY_URL}/messages/{INSTANCE_NAME}/ack",
                json={"ack_token": ack_token},
                headers=_auth_headers(),
                timeout=5,
            )
        except (httpx.ConnectError, httpx.HTTPStatusError):
            pass  # Best effort — messages may redeliver but output succeeded


CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

QUORUS_HOOK_CONFIG = {
    "matcher": ".*",
    "hooks": [
        {
            "type": "command",
            "command": "quorus inbox --quiet && quorus context --quiet",
            "timeout": 10,
        }
    ],
}


def _cmd_hook(args):
    """Manage Claude Code message hook."""
    action = args.action

    # Read existing settings
    if CLAUDE_SETTINGS_PATH.exists():
        try:
            settings = json.loads(CLAUDE_SETTINGS_PATH.read_text())
        except (json.JSONDecodeError, ValueError):
            settings = {}
    else:
        settings = {}

    # Ensure structure exists
    if "hooks" not in settings:
        settings["hooks"] = {}
    if "UserPromptSubmit" not in settings["hooks"]:
        settings["hooks"]["UserPromptSubmit"] = []

    hooks_list = settings["hooks"]["UserPromptSubmit"]

    # Check if quorus hook already exists
    def is_quorus_hook(h):
        for inner in h.get("hooks", []):
            cmd = inner.get("command", "")
            if "quorus inbox" in cmd:
                return True
        return False

    quorus_exists = any(is_quorus_hook(h) for h in hooks_list)

    if action == "status":
        if quorus_exists:
            console.print("[green]Hook: enabled[/green]")
        else:
            console.print("[dim]Hook: not configured[/dim]")
        return

    if action == "enable":
        if quorus_exists:
            console.print("[yellow]Hook already enabled.[/yellow]")
            return
        hooks_list.append(QUORUS_HOOK_CONFIG)
        CLAUDE_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        CLAUDE_SETTINGS_PATH.write_text(json.dumps(settings, indent=2))
        console.print("[green]Hook enabled.[/green]")
        console.print("[yellow]Restart Claude Code to activate.[/yellow]")
        return

    if action == "disable":
        if not quorus_exists:
            console.print("[dim]Hook not configured.[/dim]")
            return
        settings["hooks"]["UserPromptSubmit"] = [
            h for h in hooks_list if not is_quorus_hook(h)
        ]
        CLAUDE_SETTINGS_PATH.write_text(json.dumps(settings, indent=2))
        console.print("[green]Hook disabled.[/green]")


async def _history(room_name: str, limit: int = 50):
    """Fetch and display room message history."""
    client = httpx.AsyncClient()
    try:
        resp = await client.get(
            f"{RELAY_URL}/rooms/{room_name}/history",
            params={"limit": limit},
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        messages = resp.json()
        if not messages:
            console.print(f"[dim]No messages in {room_name}[/dim]")
            return
        n = len(messages)
        console.print(
            f"[bold]History for {room_name}[/bold] "
            f"({n} message{'s' if n != 1 else ''})\n"
        )
        for msg in messages:
            console.print(_format_chat_msg(msg))
    except httpx.ConnectError:
        _relay_unreachable()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            console.print(f"[red]Room '{room_name}' not found[/red]")
        else:
            console.print(f"[red]Error: {e.response.status_code}[/red]")
    finally:
        await client.aclose()


async def _export(
    room_name: str,
    fmt: str = "json",
    output: str | None = None,
    limit: int = 1000,
) -> None:
    """Export room history as JSON or Markdown."""
    client = _get_client()
    try:
        resp = await client.get(
            f"{RELAY_URL}/rooms/{room_name}/history",
            params={"limit": limit},
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        messages = resp.json()
        if not messages:
            console.print(f"[dim]No messages in {room_name}[/dim]")
            return

        if fmt == "json":
            body = json.dumps(messages, indent=2)
        elif fmt == "md":
            lines = [f"# Room: {room_name}", ""]
            for msg in messages:
                ts = msg.get("timestamp", "")[:19]
                sender = msg.get("from_name", "?")
                msg_type = msg.get("message_type", "chat")
                content = msg.get("content", "")
                tag = f" **[{msg_type}]**" if msg_type != "chat" else ""
                lines.append(f"- `{ts}` **{sender}**{tag} {content}")
            body = "\n".join(lines) + "\n"
        else:
            console.print(f"[red]Unknown format: {fmt} (use json or md)[/red]")
            return

        if output:
            Path(output).write_text(body, encoding="utf-8")
            console.print(
                f"[green]Exported {len(messages)} messages → {output}[/green]"
            )
        else:
            console.print(body, highlight=False, markup=False)
    except httpx.ConnectError:
        _relay_unreachable()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            console.print(f"[red]Room '{room_name}' not found[/red]")
        else:
            console.print(f"[red]Error: {e.response.status_code}[/red]")
    finally:
        await client.aclose()


async def _ps() -> None:
    """Show all agents with online/offline status from heartbeat presence."""
    client = _get_client()
    try:
        resp = await client.get(
            f"{RELAY_URL}/presence", headers=_auth_headers()
        )
        resp.raise_for_status()
        agents = resp.json()

        if not agents:
            console.print("[dim]No agents reporting presence yet.[/dim]")
            return

        table = Table(title="Agent Presence")
        table.add_column("Name", style="bold")
        table.add_column("Status")
        table.add_column("Room", style="dim")
        table.add_column("Last Heartbeat", style="dim")
        table.add_column("Uptime", justify="right")

        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        for a in agents:
            online = a["online"]
            status_str = a["status"]
            if online:
                status_display = f"[green]{status_str}[/green]"
            else:
                status_display = "[red]offline[/red]"

            # Calculate uptime
            try:
                start = datetime.fromisoformat(a["uptime_start"])
                delta = now - start
                hours = int(delta.total_seconds()) // 3600
                minutes = (int(delta.total_seconds()) % 3600) // 60
                if hours > 0:
                    uptime = f"{hours}h {minutes}m"
                else:
                    uptime = f"{minutes}m"
            except (ValueError, KeyError, TypeError):
                uptime = "—"

            # Format last heartbeat as relative time
            try:
                hb = datetime.fromisoformat(a["last_heartbeat"])
                ago = now - hb
                secs = int(ago.total_seconds())
                if secs < 60:
                    hb_str = f"{secs}s ago"
                elif secs < 3600:
                    hb_str = f"{secs // 60}m ago"
                else:
                    hb_str = f"{secs // 3600}h ago"
            except (ValueError, KeyError, TypeError):
                hb_str = "—"

            table.add_row(
                a["name"],
                status_display,
                a.get("room", ""),
                hb_str,
                uptime,
            )

        console.print(table)
    except httpx.ConnectError:
        _relay_unreachable()
    except httpx.HTTPStatusError as e:
        console.print(f"[red]Error: {e.response.status_code}[/red]")
    finally:
        await client.aclose()


def _cmd_ps(args):
    asyncio.run(_ps())


def _cmd_history(args):
    asyncio.run(_history(args.room, args.limit))


def _cmd_export(args):
    asyncio.run(
        _export(args.room, args.format, args.output, args.limit)
    )


async def _search(
    room_name: str,
    query: str = "",
    sender: str = "",
    message_type: str = "",
    limit: int = 50,
) -> None:
    """Search room history by keyword, sender, or message type."""
    client = _get_client()
    try:
        params: dict[str, str | int] = {"limit": limit}
        if query:
            params["q"] = query
        if sender:
            params["sender"] = sender
        if message_type:
            params["message_type"] = message_type
        resp = await client.get(
            f"{RELAY_URL}/rooms/{room_name}/search",
            params=params,
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        messages = resp.json()
        if not messages:
            console.print("[dim]No matching messages[/dim]")
            return
        console.print(
            f"[bold]Search results in {room_name}[/bold] "
            f"({len(messages)} matches)\n"
        )
        for msg in messages:
            console.print(_format_chat_msg(msg))
    except httpx.ConnectError:
        _relay_unreachable()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            console.print(f"[red]Room '{room_name}' not found[/red]")
        else:
            console.print(f"[red]Error: {e.response.status_code}[/red]")
    finally:
        await client.aclose()


def _cmd_search(args):
    asyncio.run(
        _search(
            args.room, args.query, args.sender,
            args.type, args.limit,
        )
    )


async def _metrics(room_name: str) -> None:
    """Show rich activity metrics for a room."""
    client = _get_client()
    try:
        # Fetch room history and analytics in parallel
        hist_resp = await client.get(
            f"{RELAY_URL}/rooms/{room_name}/history",
            params={"limit": 1000},
            headers=_auth_headers(),
        )
        hist_resp.raise_for_status()
        messages = hist_resp.json()

        analytics_resp = await client.get(
            f"{RELAY_URL}/analytics",
            headers=_auth_headers(),
        )
        analytics_resp.raise_for_status()
        analytics_data = analytics_resp.json()

        if not messages:
            console.print(f"[dim]No messages in {room_name}[/dim]")
            return

        # Compute per-agent stats
        agent_stats: dict[str, dict[str, int]] = {}
        type_counts: dict[str, int] = {}
        hour_counts: dict[str, int] = {}
        claims: set[str] = set()
        completions: set[str] = set()

        for msg in messages:
            sender = msg.get("from_name", "?")
            msg_type = msg.get("message_type", "chat")
            content = msg.get("content", "")
            ts = msg.get("timestamp", "")

            if sender not in agent_stats:
                agent_stats[sender] = {"total": 0, "chat": 0, "claim": 0, "status": 0, "other": 0}
            agent_stats[sender]["total"] += 1
            if msg_type in ("chat", "claim", "status"):
                agent_stats[sender][msg_type] += 1
            else:
                agent_stats[sender]["other"] += 1

            type_counts[msg_type] = type_counts.get(msg_type, 0) + 1

            if len(ts) >= 13:
                hour_counts[ts[:13]] = hour_counts.get(ts[:13], 0) + 1

            if msg_type == "claim":
                claims.add(content.lower())
            elif msg_type == "status" and "complete" in content.lower():
                completions.add(content.lower())

        # Header
        console.print(
            f"\n[bold]Metrics for {room_name}[/bold] "
            f"({len(messages)} messages)\n"
        )

        # Agent activity table
        table = Table(title="Agent Activity")
        table.add_column("Agent", style="bold")
        table.add_column("Total", justify="right")
        table.add_column("Chat", justify="right", style="white")
        table.add_column("Claims", justify="right", style="yellow")
        table.add_column("Status", justify="right", style="cyan")
        table.add_column("Other", justify="right", style="dim")

        for name, stats in sorted(
            agent_stats.items(), key=lambda x: x[1]["total"], reverse=True
        ):
            table.add_row(
                name,
                str(stats["total"]),
                str(stats["chat"]),
                str(stats["claim"]),
                str(stats["status"]),
                str(stats["other"]),
            )
        console.print(table)
        console.print()

        # Message types breakdown
        type_table = Table(title="Message Types")
        type_table.add_column("Type", style="bold")
        type_table.add_column("Count", justify="right")
        type_table.add_column("Share", justify="right")

        total = len(messages)
        for mtype, count in sorted(
            type_counts.items(), key=lambda x: x[1], reverse=True
        ):
            pct = f"{count / total * 100:.0f}%"
            bar = "█" * int(count / total * 20)
            type_table.add_row(mtype, str(count), f"{bar} {pct}")
        console.print(type_table)
        console.print()

        # Task completion rate
        claim_count = len(claims)
        completion_count = len(completions)
        if claim_count > 0:
            rate = min(completion_count / claim_count * 100, 100)
            console.print(
                f"[bold]Task Completion:[/bold] "
                f"{completion_count}/{claim_count} "
                f"({rate:.0f}%)\n"
            )

        # Hourly activity
        if hour_counts:
            max_count = max(hour_counts.values())
            console.print("[bold]Activity Timeline[/bold]")
            for hour, count in sorted(hour_counts.items())[-12:]:
                bar_len = int(count / max_count * 30) if max_count > 0 else 0
                bar = "█" * bar_len
                label = hour[5:13] if len(hour) >= 13 else hour
                console.print(f"  {label} │ {bar} {count}")
            console.print()

        # Global stats from analytics
        console.print("[bold]Relay Summary[/bold]")
        console.print(
            f"  Total sent:      {analytics_data.get('total_messages_sent', 0)}"
        )
        console.print(
            f"  Total delivered:  {analytics_data.get('total_messages_delivered', 0)}"
        )
        console.print(
            f"  Pending:         {analytics_data.get('messages_pending', 0)}"
        )
        uptime = analytics_data.get("uptime_seconds", 0)
        hours, remainder = divmod(uptime, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            console.print(f"  Uptime:          {hours}h {minutes}m")
        else:
            console.print(f"  Uptime:          {minutes}m {secs}s")
        console.print()

    except httpx.ConnectError:
        _relay_unreachable()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            console.print(f"[red]Room '{room_name}' not found[/red]")
        else:
            console.print(f"[red]Error: {e.response.status_code}[/red]")
    finally:
        await client.aclose()


def _cmd_metrics(args):
    asyncio.run(_metrics(args.room))


def _cmd_connect(args):
    """Generate platform-specific onboarding for an agent type."""
    platform = args.platform
    room = args.room
    name = args.name
    relay_url = RELAY_URL
    secret = RELAY_SECRET
    quorus_dir = str(Path(__file__).resolve().parent.parent)

    generators = {
        "codex": _connect_codex,
        "cursor": _connect_cursor,
        "ollama": _connect_ollama,
        "claude": _connect_claude,
        "gemini": _connect_gemini,
        "windsurf": _connect_windsurf,
        "opencode": _connect_opencode,
        "cline": _connect_cline,
        "continue": _connect_continue,
        "antigravity": _connect_antigravity,
        "aider": _connect_aider,
        "http": _connect_http,
    }

    if platform not in generators:
        console.print(f"[red]Unknown platform: {platform}[/red]")
        console.print(
            f"[dim]Supported: {', '.join(generators)}[/dim]"
        )
        return

    # Auto-join room
    async def _auto_join():
        client = _get_client()
        try:
            resp = await client.post(
                f"{relay_url}/rooms/{room}/join",
                json={"participant": name},
                headers=_auth_headers(),
            )
            resp.raise_for_status()
        except Exception:
            pass
        finally:
            await client.aclose()

    asyncio.run(_auto_join())
    generators[platform](room, name, relay_url, secret, quorus_dir)


def _connect_codex(
    room: str, name: str, relay_url: str, secret: str, quorus_dir: str,
) -> None:
    """Generate Codex onboarding with curl commands."""
    console.print("\n[bold green]Codex Agent Setup[/bold green]\n")
    console.print(f"  Agent:  [cyan]{name}[/cyan]")
    console.print(f"  Room:   [cyan]{room}[/cyan]")
    console.print(f"  Relay:  [cyan]{relay_url}[/cyan]\n")

    console.print("[bold]1. Add to your Codex system prompt:[/bold]\n")

    system_prompt = f"""You are {name} in a Quorus dev room. Use curl to communicate:

# Check messages
curl -s "{relay_url}/messages/{name}" -H "Authorization: Bearer {secret}"

# Send to room
curl -s -X POST "{relay_url}/rooms/{room}/messages" \\
  -H "Authorization: Bearer {secret}" -H "Content-Type: application/json" \\
  -d '{{"from_name":"{name}","content":"your message","message_type":"chat"}}'

# Join room (first time)
curl -s -X POST "{relay_url}/rooms/{room}/join" \\
  -H "Authorization: Bearer {secret}" -H "Content-Type: application/json" \\
  -d '{{"participant":"{name}"}}'

Check messages after every task. Claim work with CLAIM:. Post STATUS: when done."""

    console.print(system_prompt, highlight=False, markup=False)

    console.print("\n[bold]2. Or use the Python client:[/bold]\n")
    python_snippet = f"""\
from quorus_sdk.http_agent import QuorusClient

client = QuorusClient("{relay_url}", "{secret}", "{name}")
client.join("{room}")
messages = client.receive()
client.send("{room}", "STATUS: online", message_type="status")"""

    console.print(python_snippet, highlight=False, markup=False)
    console.print(f"\n[green]{name} joined room '{room}'. Ready to go.[/green]")


def _connect_cursor(
    room: str, name: str, relay_url: str, secret: str, quorus_dir: str,
) -> None:
    """Generate Cursor MCP config."""
    console.print("\n[bold green]Cursor Agent Setup[/bold green]\n")
    console.print(f"  Agent:  [cyan]{name}[/cyan]")
    console.print(f"  Room:   [cyan]{room}[/cyan]\n")

    mcp_config = json.dumps({
        "mcpServers": {
            "quorus": {
                "command": "quorus-mcp",
                "env": {
                    "INSTANCE_NAME": name,
                    "RELAY_URL": relay_url,
                    "RELAY_SECRET": secret,
                },
            }
        }
    }, indent=2)

    console.print("[bold]1. Add to .cursor/mcp.json:[/bold]\n")
    console.print(mcp_config, highlight=False, markup=False)

    console.print("\n[bold]2. Add to .cursorrules:[/bold]\n")
    rules = f"""\
You are {name} in the {room} Quorus room. Use MCP tools:
- check_messages() — read new messages
- send_room_message(room_id="{room}", content="...", message_type="chat")
- list_rooms() — see available rooms

Check messages after every task. Claim with CLAIM:. Post STATUS: when done."""

    console.print(rules, highlight=False, markup=False)
    console.print(f"\n[green]{name} joined room '{room}'. Ready to go.[/green]")


def _connect_ollama(
    room: str, name: str, relay_url: str, secret: str, quorus_dir: str,
) -> None:
    """Generate Ollama agent script."""
    console.print("\n[bold green]Ollama Agent Setup[/bold green]\n")
    console.print(f"  Agent:  [cyan]{name}[/cyan]")
    console.print(f"  Room:   [cyan]{room}[/cyan]\n")

    console.print("[bold]Save this as ollama_agent.py and run it:[/bold]\n")

    script = f"""\
import ollama
from quorus_sdk.http_agent import QuorusClient

quorus = QuorusClient("{relay_url}", "{secret}", "{name}")
quorus.join("{room}")
quorus.send("{room}", "{name} online (ollama). Ready for tasks.")

while True:
    messages = quorus.receive(wait=30)
    if not messages:
        continue

    context = "\\n".join(f"{{m['from_name']}}: {{m['content']}}" for m in messages)

    response = ollama.chat(
        model="llama3.1",
        messages=[
            {{"role": "system", "content": (
                "You are {name} in a Quorus dev room. "
                "Respond to messages. Claim tasks with CLAIM:. "
                "Post status with STATUS:. Keep responses short."
            )}},
            {{"role": "user", "content": context}},
        ],
    )

    quorus.send("{room}", response["message"]["content"])"""

    console.print(script, highlight=False, markup=False)

    console.print("\n[bold]Install dependencies:[/bold]")
    console.print("  pip install quorus-ai ollama", highlight=False, markup=False)
    console.print(f"\n[green]{name} joined room '{room}'. Ready to go.[/green]")


def _connect_claude(
    room: str, name: str, relay_url: str, secret: str, quorus_dir: str,
) -> None:
    """Generate Claude Code workspace setup."""
    console.print("\n[bold green]Claude Code Agent Setup[/bold green]\n")
    console.print(f"  Agent:  [cyan]{name}[/cyan]")
    console.print(f"  Room:   [cyan]{room}[/cyan]\n")

    console.print(
        "[bold]Run this to create and launch the agent:[/bold]\n"
    )
    console.print(
        "  quorus add-agent",
        highlight=False, markup=False,
    )
    console.print(
        "\n[dim]Or use quorus spawn to skip the wizard:[/dim]\n"
    )
    console.print(
        f"  quorus spawn {room} {name}",
        highlight=False, markup=False,
    )
    console.print(f"\n[green]{name} joined room '{room}'. Ready to go.[/green]")


def _connect_gemini(
    room: str, name: str, relay_url: str, secret: str, quorus_dir: str,
) -> None:
    """Generate Gemini CLI MCP config and system prompt."""
    console.print("\n[bold green]Gemini CLI Agent Setup[/bold green]\n")
    console.print(f"  Agent:  [cyan]{name}[/cyan]")
    console.print(f"  Room:   [cyan]{room}[/cyan]\n")

    mcp_config = json.dumps({
        "mcpServers": {
            "quorus": {
                "command": "quorus-mcp",
                "env": {
                    "INSTANCE_NAME": name,
                    "RELAY_URL": relay_url,
                    "RELAY_SECRET": secret,
                },
            }
        }
    }, indent=2)

    console.print("[bold]1. Add to ~/.gemini/settings.json:[/bold]\n")
    console.print(mcp_config, highlight=False, markup=False)

    console.print("\n[bold]2. Add to your GEMINI.md system prompt:[/bold]\n")
    rules = f"""\
You are {name} in the {room} Quorus coordination room. Use MCP tools to coordinate:
- check_messages() — read pending messages from other agents
- send_room_message(room_id="{room}", content="...", message_type="chat") — broadcast
- claim_task(room_id="{room}", task_id="...", agent_id="{name}") — claim exclusive work
- release_task(room_id="{room}", task_id="...") — release when done
- get_room_state(room_id="{room}") — full coordination snapshot

Check messages after every task. Use CLAIM: prefix for file/task locks. Post STATUS: when done."""

    console.print(rules, highlight=False, markup=False)
    console.print(
        f"\n[green]{name} joined room '{room}'. "
        "Restart Gemini CLI to activate MCP.[/green]"
    )


def _connect_windsurf(
    room: str, name: str, relay_url: str, secret: str, quorus_dir: str,
) -> None:
    """Generate Windsurf (Codeium) MCP config.

    Windsurf reads MCP servers from ``~/.codeium/windsurf/mcp_config.json``.
    Same shape as Cursor — just a different file location.
    """
    from quorus_cli import ui

    ui.heading("Windsurf Agent Setup")
    ui.console.print(f"  Agent:  [agent]@{name}[/]")
    ui.console.print(f"  Room:   [room]#{room}[/]")
    ui.console.print()

    mcp_config = json.dumps(
        {
            "mcpServers": {
                "quorus": {
                    "command": "quorus-mcp",
                    "env": {
                        "INSTANCE_NAME": name,
                        "RELAY_URL": relay_url,
                        "RELAY_SECRET": secret,
                    },
                }
            }
        },
        indent=2,
    )

    ui.console.print(
        "[bold]1. Add to ~/.codeium/windsurf/mcp_config.json:[/bold]\n"
    )
    ui.console.print(mcp_config, highlight=False, markup=False)

    ui.console.print("\n[bold]2. Add to your Windsurf rules:[/bold]\n")
    rules = (
        f"You are {name} in Quorus room #{room}. Use MCP tools:\n"
        "- check_messages() — read new messages\n"
        f'- send_room_message(room_id="{room}", content="...", '
        'message_type="chat")\n'
        "- list_rooms() — see available rooms\n"
        "- claim_task(...) / release_task(...) — coordinate on files\n"
        "Check messages after every task. Claim with CLAIM:. "
        "Post STATUS: when done."
    )
    ui.console.print(rules, highlight=False, markup=False)
    ui.console.print(
        f"\n[success]{name} joined room '{room}'. Reload Windsurf to "
        "activate MCP.[/success]"
    )


def _connect_cline(
    room: str, name: str, relay_url: str, secret: str, quorus_dir: str,
) -> None:
    """Cline (VS Code extension) — uses standard mcpServers schema.

    Cline's config lives inside VS Code's per-extension globalStorage, which
    varies by OS and VS Code install. We print the snippet and the two
    common paths for the user to paste into.
    """
    from quorus_cli import ui

    ui.heading("Cline (VS Code) Setup")
    ui.console.print(f"  Agent:  [agent]@{name}[/]")
    ui.console.print(f"  Room:   [room]#{room}[/]\n")

    cfg = json.dumps(
        {
            "mcpServers": {
                "quorus": {
                    "command": "quorus-mcp",
                    "env": {
                        "INSTANCE_NAME": name,
                        "RELAY_URL": relay_url,
                        "RELAY_SECRET": secret,
                    },
                }
            }
        },
        indent=2,
    )
    ui.console.print(
        "[bold]1. In Cline: click the MCP Servers icon → Edit Settings → "
        "paste:[/]\n"
    )
    ui.console.print(cfg, highlight=False, markup=False)
    ui.console.print(
        "\n  [muted]Cline stores this at "
        "~/Library/Application Support/Code/User/globalStorage/"
        "saoudrizwan.claude-dev/settings/cline_mcp_settings.json[/]\n"
        "  [muted](Linux: ~/.config/Code/User/globalStorage/…)[/]"
    )
    ui.console.print(
        f"\n[success]{name} joined room '{room}'. Reload VS Code to activate "
        "MCP.[/success]"
    )


def _connect_continue(
    room: str, name: str, relay_url: str, secret: str, quorus_dir: str,
) -> None:
    """Continue (VS Code / JetBrains extension) — standard mcpServers."""
    from quorus_cli import ui

    ui.heading("Continue Setup")
    ui.console.print(f"  Agent:  [agent]@{name}[/]")
    ui.console.print(f"  Room:   [room]#{room}[/]\n")
    cfg = json.dumps(
        {
            "experimental": {
                "modelContextProtocolServers": [
                    {
                        "transport": {
                            "type": "stdio",
                            "command": "quorus-mcp",
                            "env": {
                                "INSTANCE_NAME": name,
                                "RELAY_URL": relay_url,
                                "RELAY_SECRET": secret,
                            },
                        }
                    }
                ]
            }
        },
        indent=2,
    )
    ui.console.print("[bold]Merge into ~/.continue/config.json:[/]\n")
    ui.console.print(cfg, highlight=False, markup=False)
    ui.console.print(
        f"\n[success]{name} joined room '{room}'. Reload Continue.[/success]"
    )


def _connect_antigravity(
    room: str, name: str, relay_url: str, secret: str, quorus_dir: str,
) -> None:
    """Google Antigravity — MCP-compatible agent IDE."""
    from quorus_cli import ui

    ui.heading("Antigravity Setup")
    ui.console.print(f"  Agent:  [agent]@{name}[/]")
    ui.console.print(f"  Room:   [room]#{room}[/]\n")
    cfg = json.dumps(
        {
            "mcpServers": {
                "quorus": {
                    "command": "quorus-mcp",
                    "env": {
                        "INSTANCE_NAME": name,
                        "RELAY_URL": relay_url,
                        "RELAY_SECRET": secret,
                    },
                }
            }
        },
        indent=2,
    )
    ui.console.print(
        "[bold]In Antigravity: Settings → MCP Servers → paste:[/]\n"
    )
    ui.console.print(cfg, highlight=False, markup=False)
    ui.console.print(
        f"\n[success]{name} joined room '{room}'. Reload Antigravity to "
        "activate MCP.[/success]"
    )


def _connect_aider(
    room: str, name: str, relay_url: str, secret: str, quorus_dir: str,
) -> None:
    """Aider — doesn't speak MCP, so we wire via the HTTP SDK."""
    from quorus_cli import ui

    ui.heading("Aider Setup")
    ui.console.print(f"  Agent:  [agent]@{name}[/]")
    ui.console.print(f"  Room:   [room]#{room}[/]\n")
    ui.console.print(
        "Aider doesn't use MCP. Use the Quorus HTTP SDK from a sidecar "
        "script that aider calls out to, or have aider read/write via "
        "`quorus say` / `quorus inbox` in its shell output.\n"
    )
    snippet = f"""\
# In aider, run this in the shell to post a status update:
#   /run quorus say {room} "finished the auth refactor"
#
# Or, from a Python helper:
from quorus_sdk.http_agent import QuorusClient

client = QuorusClient(
    "{relay_url}",
    "{secret}",
    "{name}",
)
client.join("{room}")
client.send("{room}", "aider checkpoint — tests passing")"""
    ui.console.print(snippet, highlight=False, markup=False)
    ui.console.print(
        f"\n[success]{name} joined room '{room}'. "
        "Point aider's shell at `quorus say/inbox` or the SDK.[/success]"
    )


def _connect_opencode(
    room: str, name: str, relay_url: str, secret: str, quorus_dir: str,
) -> None:
    """Generate Opencode MCP config (sst/opencode, opencode.ai).

    Opencode's MCP schema differs from the common ``mcpServers`` shape —
    it uses ``mcp.<name> = {type, command[], enabled}``.
    """
    from quorus_cli import ui

    ui.heading("Opencode Agent Setup")
    ui.console.print(f"  Agent:  [agent]@{name}[/]")
    ui.console.print(f"  Room:   [room]#{room}[/]")
    ui.console.print()

    oc_cfg = json.dumps(
        {
            "$schema": "https://opencode.ai/config.json",
            "mcp": {
                "quorus": {
                    "type": "local",
                    "command": ["quorus-mcp"],
                    "enabled": True,
                    "environment": {
                        "INSTANCE_NAME": name,
                        "RELAY_URL": relay_url,
                        "RELAY_SECRET": secret,
                    },
                }
            },
        },
        indent=2,
    )

    ui.console.print(
        "[bold]1. Merge into [primary]~/.config/opencode/opencode.json[/]:[/]\n"
    )
    ui.console.print(oc_cfg, highlight=False, markup=False)

    ui.console.print("\n[bold]2. Add to your AGENTS.md:[/bold]\n")
    rules = (
        f"You are {name} in Quorus room #{room}. Use MCP tools:\n"
        "- check_messages() — read new messages from other agents and humans\n"
        f'- send_room_message(room_id="{room}", content="...", '
        'message_type="chat")\n'
        "- list_rooms() / list_participants() — discover who's in the room\n"
        "- claim_task(...) / release_task(...) — coordinate file ownership"
    )
    ui.console.print(rules, highlight=False, markup=False)
    ui.console.print(
        f"\n[success]{name} joined room '{room}'. Reload Opencode to "
        "activate MCP.[/success]"
    )


def _connect_http(
    room: str, name: str, relay_url: str, secret: str, quorus_dir: str,
) -> None:
    """Generic HTTP/curl integration for any agent that can make HTTP calls."""
    console.print("\n[bold green]Generic HTTP Agent Setup[/bold green]\n")
    console.print(f"  Agent:  [cyan]{name}[/cyan]")
    console.print(f"  Room:   [cyan]{room}[/cyan]")
    console.print(f"  Relay:  [cyan]{relay_url}[/cyan]\n")
    console.print(
        "[dim]Works with any agent: Codex, Antigravity, Open Interpreter,"
        " Windsurf, and more.[/dim]\n"
    )

    console.print("[bold]System prompt to give the agent:[/bold]\n")
    system_prompt = f"""\
You are {name} in a Quorus coordination room. Communicate via these HTTP endpoints:

# Join the room first (run once):
curl -sX POST "{relay_url}/rooms/{room}/join" \\
  -H "Authorization: Bearer {secret}" \\
  -H "Content-Type: application/json" \\
  -d '{{"participant":"{name}"}}'

# Check for messages from other agents:
curl -s "{relay_url}/rooms/{room}/history?limit=20" \\
  -H "Authorization: Bearer {secret}"

# Send a message to all agents in the room:
curl -sX POST "{relay_url}/rooms/{room}/messages" \\
  -H "Authorization: Bearer {secret}" -H "Content-Type: application/json" \\
  -d '{{"from_name":"{name}","content":"your message","message_type":"chat"}}'

# Claim exclusive access to a file/task (distributed mutex):
curl -sX POST "{relay_url}/rooms/{room}/lock" \\
  -H "Authorization: Bearer {secret}" -H "Content-Type: application/json" \\
  -d '{{"resource":"{name}_task","locked_by":"{name}","ttl_seconds":300}}'

# Get full room state (goal, tasks, decisions):
curl -s "{relay_url}/rooms/{room}/state" -H "Authorization: Bearer {secret}"

Check messages before each task. Claim with CLAIM:. Post STATUS: when done."""

    console.print(system_prompt, highlight=False, markup=False)
    console.print(f"\n[green]{name} joined room '{room}'. Ready.[/green]")


async def _kick(room_name: str, participant: str) -> None:
    """Kick a participant from a room (admin only)."""
    client = _get_client()
    try:
        rooms_list = await _list_rooms_with_client(client)
        room = next((r for r in rooms_list if r["name"] == room_name), None)
        if not room:
            console.print(f"[red]Room '{room_name}' not found[/red]")
            return
        resp = await client.post(
            f"{RELAY_URL}/rooms/{room['id']}/kick",
            json={"participant": participant, "requested_by": INSTANCE_NAME},
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        console.print(f"[green]Kicked '{participant}' from {room_name}[/green]")
    except httpx.ConnectError:
        _relay_unreachable()
    except httpx.HTTPStatusError as e:
        detail = e.response.json().get("detail", str(e.response.status_code))
        console.print(f"[red]{detail}[/red]")
    finally:
        await client.aclose()


async def _destroy(room_name: str) -> None:
    """Destroy a room and its history (admin only)."""
    client = _get_client()
    try:
        rooms_list = await _list_rooms_with_client(client)
        room = next((r for r in rooms_list if r["name"] == room_name), None)
        if not room:
            console.print(f"[red]Room '{room_name}' not found[/red]")
            return
        resp = await client.request(
            "DELETE",
            f"{RELAY_URL}/rooms/{room['id']}",
            json={"requested_by": INSTANCE_NAME},
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        console.print(f"[green]Room '{room_name}' destroyed[/green]")
    except httpx.ConnectError:
        _relay_unreachable()
    except httpx.HTTPStatusError as e:
        detail = e.response.json().get("detail", str(e.response.status_code))
        console.print(f"[red]{detail}[/red]")
    finally:
        await client.aclose()


async def _rename(room_name: str, new_name: str) -> None:
    """Rename a room (admin only)."""
    client = _get_client()
    try:
        rooms_list = await _list_rooms_with_client(client)
        room = next((r for r in rooms_list if r["name"] == room_name), None)
        if not room:
            console.print(f"[red]Room '{room_name}' not found[/red]")
            return
        resp = await client.patch(
            f"{RELAY_URL}/rooms/{room['id']}",
            json={"new_name": new_name, "requested_by": INSTANCE_NAME},
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        console.print(f"[green]Room renamed: {room_name} -> {new_name}[/green]")
    except httpx.ConnectError:
        _relay_unreachable()
    except httpx.HTTPStatusError as e:
        detail = e.response.json().get("detail", str(e.response.status_code))
        console.print(f"[red]{detail}[/red]")
    finally:
        await client.aclose()


def _cmd_kick(args):
    asyncio.run(_kick(args.room, args.name))


def _cmd_destroy(args):
    asyncio.run(_destroy(args.room))


def _cmd_rename(args):
    asyncio.run(_rename(args.room, args.new_name))


def _cmd_watch(args):
    asyncio.run(_watch(args.room))


async def _watch_daemon(name: str) -> None:
    """SSE listener that writes new messages to a file for agent pickup."""
    inbox_path = Path(f"/tmp/quorus-{name}-inbox.txt")
    console.print(f"[bold]Watch daemon for: {name}[/bold]")
    console.print(f"  Inbox: {inbox_path}")
    console.print("  Ctrl+C to stop\n")

    client = httpx.AsyncClient()
    try:
        url = f"{RELAY_URL}/stream/{name}"
        sse_token = _get_sse_token(name)
        async with client.stream(
            "GET", url, params={"token": sse_token}, timeout=None,
        ) as resp:
            event_type = ""
            event_data = ""
            async for line in resp.aiter_lines():
                line = line.strip()
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    event_data = line[5:].strip()
                elif line == "" and event_type:
                    if event_type == "message":
                        try:
                            msg = json.loads(event_data)
                            sender = msg.get("from_name", "?")
                            room = msg.get("room", "dm")
                            content = msg.get("content", "")
                            ts = msg.get("timestamp", "")[:19]
                            line_out = f"[{ts}] {sender} ({room}): {content}\n"
                            with open(inbox_path, "a") as f:
                                f.write(line_out)
                            console.print(f"[dim]{line_out.strip()}[/dim]")
                        except json.JSONDecodeError:
                            pass
                    event_type = ""
                    event_data = ""
    except KeyboardInterrupt:
        console.print("\n[dim]Daemon stopped.[/dim]")
    finally:
        await client.aclose()


def _cmd_watch_daemon(args):
    try:
        asyncio.run(_watch_daemon(args.name))
    except KeyboardInterrupt:
        console.print("\n[dim]Daemon stopped.[/dim]")


async def _watch_context(room_name: str, context_path: Path | None = None) -> None:
    """Background watcher: write room context to .quorus/context.md via SSE events."""
    from quorus_cli.watcher import Watcher

    if context_path is None:
        context_path = Path.cwd() / ".quorus" / "context.md"

    console.print(f"[bold]Watching room context: {room_name}[/bold]")
    console.print(f"  Context file: {context_path}")
    console.print("  SSE event-driven (push, not poll)")
    console.print("  Ctrl+C to stop\n")

    # Get SSE token for this agent
    sse_token = _get_sse_token(INSTANCE_NAME)

    watcher = Watcher(
        relay_url=RELAY_URL,
        auth_headers=_auth_headers(),
        room_name=room_name,
        agent_name=INSTANCE_NAME,
        sse_token=sse_token,
        context_path=context_path,
    )

    try:
        await watcher.start()
        # Keep running until interrupted
        while True:
            await asyncio.sleep(0.5)
    except KeyboardInterrupt:
        console.print("\n[dim]Context watcher stopped.[/dim]")
    finally:
        await watcher.stop()


def _cmd_watch_context(args):
    context_path = Path(args.output) if args.output else None
    try:
        asyncio.run(_watch_context(args.room, context_path))
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")


def _cmd_status(args):
    asyncio.run(_status())


# ---------------------------------------------------------------------------
# quorus state <room>
# ---------------------------------------------------------------------------

async def _room_state(room_name: str) -> None:
    """Show the Shared State Matrix for a room (goal, agents, locks, stats)."""
    client = _get_client()
    try:
        resp = await client.get(
            f"{RELAY_URL}/rooms/{room_name}/state",
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        state = resp.json()

        console.print(f"[bold]Room:[/bold] {room_name}\n")

        # Active goal
        goal = state.get("active_goal") or "[dim]No active goal[/dim]"
        console.print(f"[bold]Active Goal:[/bold] {goal}")

        # Active agents
        agents = state.get("active_agents", [])
        if agents:
            agent_list = ", ".join(agents)
            console.print(
                f"\n[bold]Active Agents:[/bold] {agent_list} "
                f"[dim]({len(agents)} online)[/dim]"
            )
        else:
            console.print("\n[bold]Active Agents:[/bold] [dim]None[/dim]")

        # Locked files
        locked = state.get("locked_files", {})
        if locked:
            console.print("\n[bold]Locked Files:[/bold]")
            for fp, info in locked.items():
                holder = info.get("held_by") or info.get("claimed_by") or "?"
                exp = info.get("expires_at", "")
                ttl_str = ""
                if exp:
                    import datetime as _dt
                    try:
                        diff = max(
                            0,
                            round(
                                (
                                    _dt.datetime.fromisoformat(
                                        exp.replace("Z", "+00:00")
                                    )
                                    - _dt.datetime.now(_dt.timezone.utc)
                                ).total_seconds()
                            ),
                        )
                        m, s = divmod(diff, 60)
                        ttl_str = f" (expires in {m}m {s:02d}s)"
                    except ValueError:
                        pass
                console.print(f"  [cyan]{fp}[/cyan]  ->  [yellow]{holder}[/yellow]{ttl_str}")
        else:
            console.print("\n[bold]Locked Files:[/bold] [dim]None[/dim]")

        # Decisions
        decisions = state.get("resolved_decisions", [])
        console.print(f"\n[bold]Decisions Made:[/bold] {len(decisions)}")

        # Messages / last activity
        msg_count = state.get("message_count", 0)
        last_ts = state.get("last_activity", "")
        last_str = ""
        if last_ts:
            import datetime as _dt
            try:
                diff = round(
                    (
                        _dt.datetime.now(_dt.timezone.utc)
                        - _dt.datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                    ).total_seconds()
                )
                if diff < 60:
                    last_str = f" (last: {diff} seconds ago)"
                elif diff < 3600:
                    last_str = f" (last: {diff // 60} minutes ago)"
                else:
                    last_str = f" (last: {diff // 3600} hours ago)"
            except ValueError:
                last_str = f" (last: {last_ts})"
        console.print(f"[bold]Messages:[/bold] {msg_count}{last_str}")

    except httpx.ConnectError:
        _relay_unreachable()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            console.print(f"[red]Room '{room_name}' not found[/red]")
        else:
            console.print(f"[red]Error: {e.response.status_code}[/red]")
    finally:
        await client.aclose()


def _cmd_room_state(args):
    asyncio.run(_room_state(args.room))


# ---------------------------------------------------------------------------
# quorus locks <room>
# ---------------------------------------------------------------------------

async def _room_locks(room_name: str) -> None:
    """Show all active file locks for a room."""
    client = _get_client()
    try:
        resp = await client.get(
            f"{RELAY_URL}/rooms/{room_name}/state",
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        state = resp.json()
        locked = state.get("locked_files", {})

        console.print(f"[bold]File Locks in {room_name}:[/bold]\n")
        if not locked:
            console.print("  [dim]No active locks.[/dim]")
            return

        table = Table(show_header=True, header_style="bold")
        table.add_column("File Path", style="cyan")
        table.add_column("Held By", style="yellow")
        table.add_column("TTL", justify="right")
        table.add_column("Lock Token")

        import datetime as _dt
        for fp, info in locked.items():
            holder = info.get("held_by") or info.get("claimed_by") or "?"
            exp = info.get("expires_at", "")
            token = info.get("lock_token", "")
            token_short = (token[:8] + "...") if len(token) > 8 else token
            ttl_str = ""
            if exp:
                try:
                    diff = max(
                        0,
                        round(
                            (
                                _dt.datetime.fromisoformat(exp.replace("Z", "+00:00"))
                                - _dt.datetime.now(_dt.timezone.utc)
                            ).total_seconds()
                        ),
                    )
                    m, s = divmod(diff, 60)
                    ttl_str = f"{m}m {s:02d}s"
                except ValueError:
                    ttl_str = exp
            table.add_row(fp, holder, ttl_str, token_short)

        console.print(table)

    except httpx.ConnectError:
        _relay_unreachable()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            console.print(f"[red]Room '{room_name}' not found[/red]")
        else:
            console.print(f"[red]Error: {e.response.status_code}[/red]")
    finally:
        await client.aclose()


def _cmd_room_locks(args):
    asyncio.run(_room_locks(args.room))


# ---------------------------------------------------------------------------
# quorus usage
# ---------------------------------------------------------------------------

async def _usage() -> None:
    """Show global relay usage stats."""
    client = _get_client()
    try:
        resp = await client.get(
            f"{RELAY_URL}/analytics",
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        stats = resp.json()

        # Count rooms
        rooms_resp = await client.get(
            f"{RELAY_URL}/rooms",
            headers=_auth_headers(),
        )
        room_count = len(rooms_resp.json()) if rooms_resp.is_success else 0

        # Count active agents (with presence info)
        presence_resp = await client.get(
            f"{RELAY_URL}/presence",
            headers=_auth_headers(),
        )
        active_agents = 0
        if presence_resp.is_success:
            active_agents = sum(
                1 for p in presence_resp.json() if p.get("online", False)
            )

        console.print("[bold]Usage Stats:[/bold]\n")
        console.print(
            f"  Total messages:  [bold]{stats.get('total_messages_sent', 0):,}[/bold]"
        )
        console.print(f"  Active rooms:    [bold]{room_count}[/bold]")
        console.print(f"  Active agents:   [bold]{active_agents}[/bold]")

        # Top senders
        participants = stats.get("participants", {})
        if participants:
            console.print("\n  [bold]Top senders:[/bold]")
            top = sorted(participants.items(), key=lambda kv: kv[1].get("sent", 0), reverse=True)
            for name, data in top[:10]:
                sent = data.get("sent", 0)
                console.print(f"    [cyan]{name}[/cyan]: {sent:,} messages")

    except httpx.ConnectError:
        _relay_unreachable()
    except httpx.HTTPStatusError as e:
        console.print(f"[red]Error: {e.response.status_code}[/red]")
    finally:
        await client.aclose()


def _cmd_usage(args):
    asyncio.run(_usage())


def _cmd_init(args):
    """One-command setup: write config, auto-register MCP with every installed
    MCP-compatible client (Claude Code, Claude Desktop, Cursor, Windsurf,
    Gemini CLI). Falls back to a project-level .mcp.json when no client is
    detected. For OpenAI Codex or other HTTP agents use `quorus connect`."""
    from quorus_cli import ui

    ui.banner()

    name = args.name.strip()
    relay_url = (args.relay_url or "").strip().rstrip("/")
    secret = getattr(args, "secret", None) or ""
    api_key = getattr(args, "api_key", None) or ""
    # 0. Validate inputs
    import re as _re
    if not name:
        ui.error("Name cannot be empty", hint="pass your agent name as the first argument")
        sys.exit(1)
    if len(name) > 64:
        ui.error("Name must be 64 characters or fewer")
        sys.exit(1)
    if not _re.match(r"^[A-Za-z0-9_\-]+$", name):
        ui.error(
            f"Name '{name}' contains invalid characters",
            hint="use only letters, numbers, hyphens, and underscores",
        )
        sys.exit(1)

    parsed = urlparse(relay_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        ui.error(
            f"Invalid relay URL '{relay_url}'",
            hint="expected http://localhost:8080 or https://relay.example.com",
        )
        sys.exit(1)

    if not api_key and not secret:
        ui.error(
            "Either --secret or --api-key is required",
            hint="see: quorus init --help",
        )
        sys.exit(1)

    # 1. Write config (warn if overwriting). Prefer explicit --config-dir arg
    # over env var, which in turn beats the default ~/.quorus location.
    cli_override = getattr(args, "config_dir", None)
    # isinstance check: MagicMock-based tests set args.config_dir to a mock
    # via attribute access, and `if cli_override:` would be truthy on a mock.
    if isinstance(cli_override, str) and cli_override:
        config_dir = Path(cli_override).expanduser()
    else:
        config_dir = _config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.json"
    if config_path.exists():
        console.print(
            f"[yellow]Warning: config already exists at {config_path} — overwriting.[/yellow]"
        )

    config = {
        "relay_url": relay_url,
        "instance_name": name,
        "poll_mode": "sse",
        "push_notification_method": "notifications/claude/channel",
        "push_notification_channel": "quorus",
    }
    if api_key:
        config["api_key"] = api_key
    else:
        config["relay_secret"] = secret
    _write_sensitive_json(config_path, config)
    # (atomic 0600 write via _write_sensitive_json)
    console.print(f"[green]Config written to {config_path} (permissions: 0600)[/green]")

    # 2. Build MCP server command — prefer `uv run`, fall back to `python -m`
    if shutil.which("uv"):
        mcp_command = "uv"
        mcp_args = ["run", "python", "-m", "quorus.mcp_server"]
    else:
        mcp_command = sys.executable
        mcp_args = ["-m", "quorus.mcp_server"]
        console.print(
            "[yellow]Note: 'uv' not found on PATH — using Python interpreter directly. "
            "Install uv for faster startup: https://docs.astral.sh/uv/[/yellow]"
        )

    # 3. Register MCP server with every detected MCP-compatible client.
    # Most agents use the same JSON `mcpServers` shape. Opencode uses a
    # different schema (`mcp.<name> = {type, command[], enabled}`), so we
    # wire it with a separate code path.
    mcp_entry = {
        "type": "stdio",
        "command": mcp_command,
        "args": mcp_args,
        "env": {},
    }
    # Opencode's schema variant
    opencode_entry = {
        "type": "local",
        "command": [mcp_command, *mcp_args],
        "enabled": True,
    }

    # (label, config_path) pairs for agents using the standard mcpServers shape
    mcp_targets = [
        ("Claude Code", Path.home() / ".claude" / "settings.json"),
        ("Claude Desktop", Path.home() / ".claude.json"),
        ("Cursor", Path.home() / ".cursor" / "mcp.json"),
        ("Windsurf", Path.home() / ".codeium" / "windsurf" / "mcp_config.json"),
        ("Gemini CLI", Path.home() / ".gemini" / "settings.json"),
    ]

    # Opencode candidate paths (XDG standard first, then home fallback)
    opencode_candidates = [
        Path.home() / ".config" / "opencode" / "opencode.json",
        Path.home() / ".opencode.json",
    ]

    registered_labels: list[str] = []

    for label, cfg_path in mcp_targets:
        if not cfg_path.exists():
            continue
        try:
            existing = json.loads(cfg_path.read_text())
        except (json.JSONDecodeError, ValueError):
            existing = {}
        existing.setdefault("mcpServers", {})["quorus"] = mcp_entry
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(existing, indent=2))
        registered_labels.append(label)

    for oc_path in opencode_candidates:
        if not oc_path.exists():
            continue
        try:
            existing = json.loads(oc_path.read_text())
        except (json.JSONDecodeError, ValueError):
            existing = {"$schema": "https://opencode.ai/config.json"}
        existing.setdefault("mcp", {})["quorus"] = opencode_entry
        oc_path.parent.mkdir(parents=True, exist_ok=True)
        oc_path.write_text(json.dumps(existing, indent=2))
        registered_labels.append("Opencode")
        break  # only write to the first one we find

    if registered_labels:
        ui.success(
            "MCP registered with: [primary]"
            + ", ".join(registered_labels)
            + "[/]"
        )
    else:
        # None of the known agents are installed — write project-level .mcp.json
        mcp_json_path = Path.cwd() / ".mcp.json"
        existing = {}
        if mcp_json_path.exists():
            try:
                existing = json.loads(mcp_json_path.read_text())
            except (json.JSONDecodeError, ValueError):
                pass
        existing.setdefault("mcpServers", {})["quorus"] = mcp_entry
        mcp_json_path.write_text(json.dumps(existing, indent=2))
        ui.info(f"MCP config written to project-level [dim]{mcp_json_path}[/]")
        ui.console.print(
            "  [muted]No MCP client detected — use this config with "
            "any MCP-compatible agent.[/]"
        )

    # 4. Verify relay is reachable (best-effort, non-blocking)
    try:
        resp = httpx.get(f"{relay_url}/health", timeout=3.0)
        if resp.status_code == 200:
            console.print(f"[green]Relay reachable at {relay_url}[/green]")
        else:
            console.print(
                f"[yellow]Warning: relay returned HTTP {resp.status_code} — "
                "is it running?[/yellow]"
            )
    except Exception:
        console.print(
            f"[yellow]Warning: could not reach relay at {relay_url}. "
            "Start it with: quorus relay[/yellow]"
        )

    # 5. Summary
    ui.console.print()
    ui.success(f"Quorus initialized for [agent]{name}[/]")
    ui.console.print(f"  [muted]Relay:[/]      [primary]{relay_url}[/]")
    ui.console.print(f"  [muted]Config:[/]     [dim]{config_path}[/]")
    ui.console.print(f"  [muted]MCP server:[/] [dim]{mcp_command} {' '.join(mcp_args)}[/]")
    if registered_labels:
        ui.warn(
            f"Restart {' / '.join(registered_labels)} to pick up the MCP server"
        )
    next_steps = [
        "quorus create <room-name>   — create a coordination room",
        "quorus begin                — open the interactive hub",
        "quorus doctor               — verify everything is wired up",
    ]
    # Tell users about other agents we didn't auto-wire.
    auto_wired = set(registered_labels)
    manual_hints = []
    if "Cursor" not in auto_wired:
        manual_hints.append("quorus connect cursor    — set up Cursor")
    if "Windsurf" not in auto_wired:
        manual_hints.append("quorus connect windsurf  — set up Windsurf")
    if "Gemini CLI" not in auto_wired:
        manual_hints.append("quorus connect gemini    — set up Gemini CLI")
    if "Opencode" not in auto_wired:
        manual_hints.append("quorus connect opencode  — set up Opencode")
    manual_hints.append("quorus connect codex     — set up OpenAI Codex CLI")
    manual_hints.append("quorus connect http      — any HTTP-speaking agent")
    next_steps.extend(manual_hints[:3])  # keep the panel tight
    ui.hint_next_steps(next_steps)


def _cmd_invite_link(args):
    """Generate a one-liner join command to share with others."""
    room = args.room
    console.print(f"[bold]Share this command to invite someone to '{room}':[/bold]")
    console.print("")
    console.print(
        f"  quorus join --name YOUR_NAME "
        f"--relay {RELAY_URL} --secret {RELAY_SECRET} --room {room}"
    )
    console.print("")
    console.print("[dim]Replace YOUR_NAME with the agent/user's name.[/dim]")


_JOIN_TOKEN_PREFIX = "quorus_join_"
_LEGACY_JOIN_PREFIX = "murm_join_"  # accept legacy tokens for migration


def _make_join_token(room: str, relay_url: str, secret: str) -> str:
    """Encode room join details into a portable quorus_join_ token."""
    payload = json.dumps({"relay_url": relay_url, "secret": secret, "room": room})
    b64 = base64.urlsafe_b64encode(payload.encode()).decode()
    return f"{_JOIN_TOKEN_PREFIX}{b64}"


def _parse_join_token(token: str) -> dict:
    """Decode a quorus_join_ (or legacy murm_join_) token."""
    if token.startswith(_JOIN_TOKEN_PREFIX):
        b64 = token[len(_JOIN_TOKEN_PREFIX):]
    elif token.startswith(_LEGACY_JOIN_PREFIX):
        b64 = token[len(_LEGACY_JOIN_PREFIX):]
    else:
        raise ValueError("Not a valid join token (expected quorus_join_ prefix)")
    try:
        payload = base64.urlsafe_b64decode(b64.encode()).decode()
        data = json.loads(payload)
    except Exception as exc:
        raise ValueError(f"Failed to decode join token: {exc}") from exc
    for key in ("relay_url", "secret", "room"):
        if key not in data:
            raise ValueError(f"Join token missing required field: {key}")
    return data


def _cmd_invite_token(args):
    """Generate a compact, copy-pasteable join token for a room."""
    room = args.room
    token = _make_join_token(room, RELAY_URL, RELAY_SECRET)
    console.print(f"[bold]Join token for room '{room}':[/bold]")
    console.print("")
    console.print(f"  {token}")
    console.print("")
    console.print("[dim]Share this token with any agent. They run:[/dim]")
    console.print("[dim]  quorus join <token> --name THEIR_NAME[/dim]")


def _cmd_join(args):
    """One-liner to join a room: writes config, registers MCP, joins the room.

    Accepts either explicit flags (--relay, --secret, --room) OR a
    quorus_join_ (or legacy murm_join_) token as the first positional argument
    for instant, zero-config onboarding.

    If no flags are provided and config already exists, just joins the room
    without rewriting config or re-registering MCP.
    """
    # --- Token-based join path (always rewrites config) ---
    token = getattr(args, "token", None)
    rewrite_config = False

    if token and (
        token.startswith(_JOIN_TOKEN_PREFIX)
        or token.startswith(_LEGACY_JOIN_PREFIX)
    ):
        try:
            decoded = _parse_join_token(token)
        except ValueError as exc:
            console.print(f"[red]Invalid join token: {exc}[/red]")
            return
        relay_url = decoded["relay_url"]
        secret = decoded["secret"]
        room = decoded["room"]
        api_key = ""
        rewrite_config = True  # Token join always sets up fresh config
    else:
        # Fall back to existing config when flags are not provided
        arg_relay = getattr(args, "relay_url", None)
        arg_secret = getattr(args, "secret", None)
        arg_api_key = getattr(args, "api_key", None)
        arg_room = getattr(args, "room", None)

        relay_url = arg_relay if arg_relay else RELAY_URL
        secret = arg_secret if arg_secret else RELAY_SECRET
        api_key = arg_api_key if arg_api_key else API_KEY
        room = arg_room if arg_room else ""

        # Only rewrite config if explicit flags were provided
        rewrite_config = bool(arg_relay or arg_secret or arg_api_key)

    if not room:
        console.print("[red]Room is required. Use --room or provide a join token.[/red]")
        return

    if not relay_url:
        console.print("[red]Relay URL is required. Use --relay or run 'quorus init' first.[/red]")
        return

    name = args.name
    repo_dir = Path(__file__).resolve().parent.parent
    quorus_dir = Path(__file__).resolve().parent
    config_dir = _config_dir()
    config_path = config_dir / "config.json"

    # 1. Write config only if needed (token join, explicit flags, or no config exists)
    if rewrite_config or not config_path.exists():
        config_dir.mkdir(exist_ok=True)
        config = {
            "relay_url": relay_url,
            "instance_name": name,
            "poll_mode": "sse",
            "push_notification_method": "notifications/claude/channel",
            "push_notification_channel": "quorus",
        }
        if api_key:
            config["api_key"] = api_key
        else:
            config["relay_secret"] = secret
        _write_sensitive_json(config_path, config)
        # (atomic 0600 write via _write_sensitive_json)
        console.print(f"[green]Config written to {config_path} (permissions: 0600)[/green]")

    # 2. Register MCP server with Claude Code (only if setting up fresh)
    if rewrite_config or not config_path.exists():
        claude_config_path = Path.home() / ".claude.json"
        if claude_config_path.exists():
            try:
                claude_config = json.loads(claude_config_path.read_text())
            except (json.JSONDecodeError, ValueError):
                claude_config = {}
        else:
            claude_config = {}

        if "mcpServers" not in claude_config:
            claude_config["mcpServers"] = {}

        claude_config["mcpServers"]["quorus"] = {
            "type": "stdio",
            "command": "uv",
            "args": [
                "run", "--directory", str(repo_dir),
                "python", str(quorus_dir / "mcp_server.py"),
            ],
            "env": {},
        }
        claude_config_path.write_text(json.dumps(claude_config, indent=2))
        console.print("[green]MCP server registered in ~/.claude.json[/green]")

    # 3. Join the room
    async def _do_join():
        client = _get_client()
        try:
            # Resolve room by name
            rooms_resp = await client.get(
                f"{relay_url}/rooms",
                headers=_auth_headers(),
            )
            rooms_resp.raise_for_status()
            rooms_list = rooms_resp.json()
            target = next((r for r in rooms_list if r["name"] == room), None)
            if not target:
                console.print(f"[red]Room '{room}' not found on relay.[/red]")
                return
            resp = await client.post(
                f"{relay_url}/rooms/{target['id']}/join",
                json={"participant": name},
                headers=_auth_headers(),
            )
            resp.raise_for_status()
            console.print(f"[green]Joined room '{room}' as '{name}'[/green]")
        except httpx.ConnectError:
            console.print(f"[red]Cannot connect to relay at {relay_url}[/red]")
        finally:
            await client.aclose()

    asyncio.run(_do_join())

    console.print("")
    if rewrite_config:
        console.print(f"[bold]Setup complete for: {name}[/bold]")
        console.print(f"  Relay: {relay_url}")
        console.print(f"  Room: {room}")
        console.print("")
        console.print("[yellow]Restart Claude Code to pick up the MCP server.[/yellow]")
    else:
        console.print(f"[bold]Joined room: {room}[/bold]")
    console.print("")
    console.print("Start chatting:")
    console.print(f"  quorus chat {room}")


# ---------------------------------------------------------------------------
# quorus share <room> — generate a portable join token
# ---------------------------------------------------------------------------


def _encode_join_token(relay_url: str, room: str, secret: str = "", api_key: str = "") -> str:
    """Create a portable join token (base64-encoded JSON)."""
    import base64
    import time

    payload = {
        "r": relay_url,
        "n": room,
        "e": int(time.time()) + 86400 * 7,  # expires in 7 days
    }
    if api_key:
        payload["k"] = api_key
    else:
        payload["s"] = secret
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    return f"quorus://{encoded}"


def _decode_join_token(token: str) -> dict | None:
    """Decode a join token. Returns None if invalid."""
    import base64
    import time

    if not token.startswith("quorus://"):
        return None
    try:
        encoded = token[9:]  # strip "quorus://"
        payload = json.loads(base64.urlsafe_b64decode(encoded).decode())
        # Check expiry
        if payload.get("e", 0) < time.time():
            return None
        return payload
    except (ValueError, json.JSONDecodeError):
        return None


def _cmd_share(args):
    """Generate a portable join token for a room."""
    room = args.room
    ttl_days = getattr(args, "ttl", 7)

    if not RELAY_URL:
        console.print("[red]Relay URL not configured.[/red]")
        console.print("[dim]Run: quorus init <name> --secret <secret>[/dim]")
        return

    if not RELAY_SECRET and not API_KEY:
        console.print("[red]No auth configured.[/red]")
        console.print("[dim]Run: quorus init <name> --secret <secret>[/dim]")
        return

    # Verify room exists
    try:
        resp = httpx.get(
            f"{RELAY_URL}/rooms",
            headers=_auth_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        rooms = resp.json()
        target = next((r for r in rooms if r["name"] == room), None)
        if not target:
            console.print(f"[red]Room '{room}' not found.[/red]")
            return
    except httpx.ConnectError:
        _relay_unreachable()
        return
    except httpx.HTTPStatusError as e:
        console.print(f"[red]Error: {e.response.status_code}[/red]")
        return

    # Generate token
    import base64
    import time

    payload = {
        "r": RELAY_URL,
        "n": room,
        "e": int(time.time()) + 86400 * ttl_days,
    }
    if API_KEY:
        payload["k"] = API_KEY
    else:
        payload["s"] = RELAY_SECRET
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    token = f"quorus://{encoded}"

    console.print(f"[bold green]Join token for '{room}'[/bold green]")
    console.print("")
    console.print(f"[cyan]{token}[/cyan]")
    console.print("")
    console.print(f"[dim]Expires in {ttl_days} days. Share with agents to join.[/dim]")
    console.print("")
    console.print("Recipient runs:")
    console.print(f"  [bold]quorus quickjoin {token[:50]}...[/bold]")


def _cmd_quickjoin(args):
    """Join a room using a portable token (zero config)."""
    token = args.token
    name = args.name

    # Decode token
    payload = _decode_join_token(token)
    if not payload:
        console.print("[red]Invalid or expired token.[/red]")
        return

    relay_url = payload.get("r", "")
    room = payload.get("n", "")
    secret = payload.get("s", "")
    api_key = payload.get("k", "")

    if not relay_url or not room:
        console.print("[red]Token missing required fields.[/red]")
        return

    console.print(f"[bold]Joining room: {room}[/bold]")
    console.print(f"  Relay: {relay_url}")
    console.print(f"  Name: {name}")
    console.print("")

    # Reuse join logic
    repo_dir = Path(__file__).resolve().parent.parent
    quorus_dir = Path(__file__).resolve().parent

    # 1. Write config
    config_dir = _config_dir()
    config_dir.mkdir(exist_ok=True)
    config = {
        "relay_url": relay_url,
        "instance_name": name,
        "poll_mode": "sse",
        "push_notification_method": "notifications/claude/channel",
        "push_notification_channel": "quorus",
    }
    if api_key:
        config["api_key"] = api_key
    else:
        config["relay_secret"] = secret
    config_path = config_dir / "config.json"
    _write_sensitive_json(config_path, config)
    # (atomic 0600 write via _write_sensitive_json)
    console.print(f"[green]Config written to {config_path}[/green]")

    # 2. Register MCP server
    claude_config_path = Path.home() / ".claude.json"
    if claude_config_path.exists():
        try:
            claude_config = json.loads(claude_config_path.read_text())
        except (json.JSONDecodeError, ValueError):
            claude_config = {}
    else:
        claude_config = {}

    if "mcpServers" not in claude_config:
        claude_config["mcpServers"] = {}

    claude_config["mcpServers"]["quorus"] = {
        "type": "stdio",
        "command": "uv",
        "args": [
            "run", "--directory", str(repo_dir),
            "python", str(quorus_dir / "mcp_server.py"),
        ],
        "env": {},
    }
    claude_config_path.write_text(json.dumps(claude_config, indent=2))
    console.print("[green]MCP server registered[/green]")

    # 3. Join the room
    async def _do_join():
        headers = {"Authorization": f"Bearer {api_key or secret}"}
        async with httpx.AsyncClient() as client:
            try:
                rooms_resp = await client.get(f"{relay_url}/rooms", headers=headers)
                rooms_resp.raise_for_status()
                rooms_list = rooms_resp.json()
                target = next((r for r in rooms_list if r["name"] == room), None)
                if not target:
                    console.print(f"[red]Room '{room}' not found.[/red]")
                    return
                resp = await client.post(
                    f"{relay_url}/rooms/{target['id']}/join",
                    json={"participant": name},
                    headers=headers,
                )
                resp.raise_for_status()
                console.print(f"[green]Joined '{room}' as '{name}'[/green]")
            except httpx.ConnectError:
                console.print(f"[red]Cannot connect to {relay_url}[/red]")

    asyncio.run(_do_join())

    console.print("")
    console.print("[yellow]Restart Claude Code to pick up MCP server.[/yellow]")
    console.print("")
    console.print("Start chatting:")
    console.print(f"  quorus chat {room}")


def _spawn_agent(room: str, name: str, relay_url: str, secret: str):
    """Create agent workspace and launch Claude Code in a new terminal tab."""
    # Validate name: letters, digits, hyphen, underscore — defense against
    # AppleScript/shell injection in Terminal spawning below.
    import re as _re
    if not _re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
        from quorus_cli import ui
        ui.error(
            f"Invalid agent name '{name}'",
            hint="use only letters, digits, hyphen, underscore (1-64 chars)",
        )
        sys.exit(1)
    if not _re.fullmatch(r"[A-Za-z0-9_-]{1,64}", room):
        from quorus_cli import ui
        ui.error(
            f"Invalid room name '{room}'",
            hint="use only letters, digits, hyphen, underscore (1-64 chars)",
        )
        sys.exit(1)

    agents_dir = Path.home() / "quorus-agents"
    workspace = agents_dir / name
    quorus_dir = Path(__file__).resolve().parent

    if workspace.exists():
        console.print(f"[yellow]Workspace {workspace} already exists[/yellow]")
    else:
        workspace.mkdir(parents=True)

    # .mcp.json — MCP server config for this agent
    mcp_config = {
        "mcpServers": {
            "quorus": {
                "type": "stdio",
                "command": "uv",
                "args": [
                    "run", "--directory", str(quorus_dir.parent),
                    "python", str(quorus_dir / "mcp_server.py"),
                ],
                "env": {
                    "INSTANCE_NAME": name,
                    "RELAY_URL": relay_url,
                    "RELAY_SECRET": secret,
                },
            }
        }
    }
    (workspace / ".mcp.json").write_text(json.dumps(mcp_config, indent=2))

    # .claude/ directory with settings
    claude_dir = workspace / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings = {
        "permissions": {
            "allow": [
                "Bash(*)",
                "Read(*)",
                "Write(*)",
                "Edit(*)",
                "Glob(*)",
                "Grep(*)",
                "mcp__quorus__*",
            ],
            "deny": [],
        }
    }
    (claude_dir / "settings.json").write_text(json.dumps(settings, indent=2))

    # Auto-join the room so agent is a member on startup
    async def _auto_join():
        client = _get_client()
        try:
            resp = await client.post(
                f"{relay_url}/rooms/{room}/join",
                json={"participant": name},
                headers={"Authorization": f"Bearer {secret}"},
            )
            resp.raise_for_status()
            console.print(f"[green]{name} joined room '{room}'[/green]")
        except Exception:
            console.print(
                "[yellow]Could not auto-join room"
                " (agent will join on first message)[/yellow]"
            )
        finally:
            await client.aclose()

    asyncio.run(_auto_join())

    # CLAUDE.md — auto-activates the agent with full protocol
    claude_md = f"""You are {name} in the {room} group chat. \
You communicate ONLY through the room — never reply in terminal.

  Tools:
  - check_messages() — read messages NOW and every 30 seconds
  - send_room_message(room_id="{room}", content="...", message_type="chat") — talk
  - list_rooms() — see rooms

  Rules:
  1. Check messages immediately, then introduce yourself to the room
  2. Read the room history to understand what's been built so far
  3. Pick up tasks from the room — don't wait to be assigned
  4. Commit and push after every change
  5. Post STATUS updates to the room constantly
  6. If stuck, ask the room — don't guess

  Start now: check_messages(), then send_room_message to check in.
"""
    (workspace / "CLAUDE.md").write_text(claude_md)

    # Symlink to quorus source for imports
    quorus_link = workspace / "quorus"
    if not quorus_link.exists():
        quorus_link.symlink_to(quorus_dir.parent)

    console.print(f"[green]Workspace created: {workspace}[/green]")

    # Open a new terminal tab (macOS)
    if sys.platform == "darwin":
        cmd = f"cd {workspace} && claude"
        applescript = (
            f'tell application "Terminal" to do script "{cmd}"'
        )
        subprocess.run(["osascript", "-e", applescript])
        console.print(f"[green]Launched {name} in new Terminal tab[/green]")
    else:
        console.print(f"[yellow]Run manually: cd {workspace} && claude[/yellow]")


def _cmd_add_agent(args):
    """Interactive wizard to create and launch an agent."""
    console.print("\n[bold green]Quorus Add Agent Wizard[/bold green]\n")

    # 1. Agent name
    name = Prompt.ask("[bold]Agent name[/bold]", default="agent-1")

    # 2. Room selection — list existing rooms or create new
    try:
        rooms = asyncio.run(_list_rooms())
        room_names = [r["name"] for r in rooms]
    except Exception:
        rooms = []
        room_names = []

    if room_names:
        console.print(f"\n[dim]Available rooms:[/dim] {', '.join(room_names)}")
        room = Prompt.ask(
            "[bold]Which room?[/bold]",
            choices=[*room_names, "new"],
            default=room_names[0],
        )
        if room == "new":
            room = Prompt.ask("[bold]New room name[/bold]")
            try:
                asyncio.run(_create_room(room))
                console.print(f"[green]Created room '{room}'[/green]")
            except Exception:
                console.print(
                    f"[yellow]Could not create room '{room}'"
                    " — it may already exist[/yellow]"
                )
    else:
        room = Prompt.ask("[bold]Room name[/bold]", default="quorus-dev")

    # 3. Model preference
    model = Prompt.ask(
        "[bold]Model preference[/bold]",
        choices=["opus", "sonnet", "haiku"],
        default="sonnet",
    )

    # 4. Effort level
    effort = Prompt.ask(
        "[bold]Effort level[/bold]",
        choices=["low", "medium", "high"],
        default="high",
    )

    # Summary and confirm
    console.print("\n[bold]Summary:[/bold]")
    console.print(f"  Name:   [cyan]{name}[/cyan]")
    console.print(f"  Room:   [cyan]{room}[/cyan]")
    console.print(f"  Model:  [cyan]{model}[/cyan]")
    console.print(f"  Effort: [cyan]{effort}[/cyan]")

    if not Confirm.ask("\n[bold]Launch agent?[/bold]", default=True):
        console.print("[dim]Cancelled.[/dim]")
        return

    # Build workspace using existing _spawn_agent infra
    agents_dir = Path.home() / "quorus-agents"
    workspace = agents_dir / name
    quorus_dir = Path(__file__).resolve().parent

    if workspace.exists():
        if not Confirm.ask(
            f"[yellow]Workspace {workspace} exists. Overwrite config?[/yellow]",
            default=False,
        ):
            console.print("[dim]Cancelled.[/dim]")
            return

    workspace.mkdir(parents=True, exist_ok=True)

    # .mcp.json
    mcp_config = {
        "mcpServers": {
            "quorus": {
                "type": "stdio",
                "command": "uv",
                "args": [
                    "run", "--directory", str(quorus_dir.parent),
                    "python", str(quorus_dir / "mcp_server.py"),
                ],
                "env": {
                    "INSTANCE_NAME": name,
                    "RELAY_URL": RELAY_URL,
                    "RELAY_SECRET": RELAY_SECRET,
                },
            }
        }
    }
    (workspace / ".mcp.json").write_text(json.dumps(mcp_config, indent=2))

    # .claude/ settings with model preference
    claude_dir = workspace / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings = {
        "permissions": {
            "allow": [
                "Bash(*)",
                "Read(*)",
                "Write(*)",
                "Edit(*)",
                "Glob(*)",
                "Grep(*)",
                "mcp__quorus__*",
            ],
            "deny": [],
        }
    }
    (claude_dir / "settings.json").write_text(json.dumps(settings, indent=2))

    # CLAUDE.md with model/effort hints
    claude_md = f"""# Quorus Agent

You are {name} in the {room} group chat. ALL communication goes through the room.

## On Startup
1. Call check_messages() immediately
2. Call send_room_message(room_id="{room}", content="{name} online", message_type="status")
3. Read any messages and respond

## Config
- Model: {model}
- Effort: {effort}

## Rules
- Communicate ONLY through the room — never just reply in terminal
- Call check_messages() after completing each task
- Post STATUS after every commit
- If stuck, post REQUEST to the room
- Short messages — every token counts
- Commit and push after every feature

## Tools
- check_messages() — read inbox
- send_room_message(room_id="{room}", content="...", message_type="chat") — talk
- list_rooms() — see rooms
- All standard Claude Code tools (Read, Write, Edit, Bash, etc.)
"""
    (workspace / "CLAUDE.md").write_text(claude_md)

    # Symlink to quorus source
    quorus_link = workspace / "quorus"
    if not quorus_link.exists():
        quorus_link.symlink_to(quorus_dir.parent)

    # Auto-join room
    async def _auto_join():
        client = _get_client()
        try:
            resp = await client.post(
                f"{RELAY_URL}/rooms/{room}/join",
                json={"participant": name},
                headers=_auth_headers(),
            )
            resp.raise_for_status()
        except Exception:
            pass
        finally:
            await client.aclose()

    asyncio.run(_auto_join())

    console.print(f"\n[green]Workspace ready: {workspace}[/green]")

    # Build claude launch command with model flag
    model_flag = f"--model {model}" if model != "sonnet" else ""
    claude_cmd = f"cd {workspace} && claude {model_flag}".strip()

    # Launch in new terminal tab (macOS)
    if sys.platform == "darwin":
        applescript = (
            f'tell application "Terminal" to do script "{claude_cmd}"'
        )
        subprocess.run(["osascript", "-e", applescript])
        console.print(f"[bold green]{name} launched in new Terminal tab![/bold green]")
    else:
        console.print(f"[yellow]Run manually: {claude_cmd}[/yellow]")

    console.print("[dim]Agent will auto-connect to the room on startup.[/dim]")


def _cmd_spawn(args):
    _spawn_agent(args.room, args.name, RELAY_URL, RELAY_SECRET)


def _cmd_spawn_multiple(args):
    room = args.room
    count = args.count
    prefix = args.prefix
    for i in range(1, count + 1):
        name = f"{prefix}-{i}"
        console.print(f"\n[bold]Spawning {name}...[/bold]")
        _spawn_agent(room, name, RELAY_URL, RELAY_SECRET)
    console.print(f"\n[bold green]Spawned {count} agents.[/bold green]")


def _cmd_quickstart(args):
    """One-command demo: start relay, create room, spawn agents, watch them talk."""
    import secrets as secrets_mod
    import time

    repo_dir = Path(__file__).resolve().parent.parent
    secret = secrets_mod.token_urlsafe(16)
    port = 8080
    room_name = "demo"
    agent_count = args.agents

    console.print("[bold green]Quorus Quickstart[/bold green]")
    console.print("Starting relay, creating room, spawning agents...\n")

    # 1. Start relay in background
    env = os.environ.copy()
    env["RELAY_SECRET"] = secret
    env["PORT"] = str(port)
    relay_proc = subprocess.Popen(
        [
            "uv", "run", "--directory", str(repo_dir),
            "python", "-m", "uvicorn", "quorus.relay:app",
            "--host", "127.0.0.1", "--port", str(port),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    relay_url = f"http://127.0.0.1:{port}"
    console.print(f"  [green]Relay started[/green] (pid {relay_proc.pid})")

    # Wait for relay to be ready
    import httpx as httpx_mod
    for _ in range(20):
        try:
            r = httpx_mod.get(f"{relay_url}/health", timeout=1)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.25)
    else:
        console.print("[red]Relay failed to start.[/red]")
        relay_proc.terminate()
        sys.exit(1)

    # 2. Write config for the human user
    config_dir = _config_dir()
    config_dir.mkdir(exist_ok=True)
    config = {
        "relay_url": relay_url,
        "relay_secret": secret,
        "instance_name": "human",
        "enable_background_polling": True,
        "push_notification_method": "notifications/claude/channel",
        "push_notification_channel": "quorus",
    }
    (config_dir / "config.json").write_text(json.dumps(config, indent=2))

    # 3. Create room
    headers = {"Authorization": f"Bearer {secret}"}
    r = httpx_mod.post(
        f"{relay_url}/rooms",
        json={"name": room_name, "created_by": "human"},
        headers=headers,
    )
    if r.status_code == 200:
        r.json()  # validate response
        console.print(f"  [green]Room '{room_name}' created[/green]")
    elif r.status_code == 409:
        console.print(f"  [yellow]Room '{room_name}' already exists[/yellow]")
    else:
        console.print(f"[red]Failed to create room: {r.text}[/red]")
        relay_proc.terminate()
        sys.exit(1)

    # 4. Spawn agents
    for i in range(1, agent_count + 1):
        name = f"agent-{i}"
        # Join agent to room
        httpx_mod.post(
            f"{relay_url}/rooms/{room_name}/join",
            json={"participant": name},
            headers=headers,
        )
        _spawn_agent(room_name, name, relay_url, secret)

    console.print("\n[bold green]Quickstart ready![/bold green]")
    console.print(f"  Relay: {relay_url}")
    console.print(f"  Room: {room_name}")
    console.print(f"  Agents: {agent_count}")
    console.print(f"  Secret: {secret}")
    console.print("\n[dim]Press Ctrl+C to stop the relay.[/dim]")
    console.print(f"[dim]Watch the room: quorus chat {room_name}[/dim]\n")

    # 5. Open interactive chat so user sees agents talking
    try:
        asyncio.run(_chat(room_name))
    except KeyboardInterrupt:
        pass
    finally:
        relay_proc.terminate()
        console.print("\n[dim]Relay stopped. Quickstart finished.[/dim]")


def _cmd_hackathon(args):
    """Set up a complete hackathon workspace with rooms and agents."""
    import httpx as httpx_mod

    agents_per_room = args.agents
    rooms_config = [
        {"name": args.room1, "mission": args.mission1 or "Build the product. Ship fast."},
        {"name": args.room2, "mission": args.mission2 or "Build the product. Ship fast."},
    ]

    headers = _auth_headers()

    console.print("[bold green]Quorus Hackathon Setup[/bold green]\n")

    for room_cfg in rooms_config:
        room_name = room_cfg["name"]
        mission = room_cfg["mission"]

        # Create room
        r = httpx_mod.post(
            f"{RELAY_URL}/rooms",
            json={"name": room_name, "created_by": INSTANCE_NAME},
            headers=headers,
        )
        if r.status_code == 200:
            console.print(f"  [green]Room '{room_name}' created[/green]")
        elif r.status_code == 409:
            console.print(f"  [yellow]Room '{room_name}' already exists[/yellow]")
        else:
            console.print(f"  [red]Failed to create room: {r.text}[/red]")
            continue

        # Join human to room
        httpx_mod.post(
            f"{RELAY_URL}/rooms/{room_name}/join",
            json={"participant": INSTANCE_NAME},
            headers=headers,
        )

        # Spawn agents with mission-specific CLAUDE.md
        for i in range(1, agents_per_room + 1):
            agent_name = f"{room_name}-agent-{i}"
            console.print(f"  Spawning {agent_name}...")
            _spawn_agent(room_name, agent_name, RELAY_URL, RELAY_SECRET)

        # Send mission briefing to room
        httpx_mod.post(
            f"{RELAY_URL}/rooms/{room_name}/messages",
            json={
                "from_name": INSTANCE_NAME,
                "content": f"HACKATHON MISSION: {mission}\n\n"
                f"Team: {agents_per_room} agents. Coordinate here. "
                "Claim tasks, post status, don't duplicate work. Ship it.",
                "message_type": "alert",
            },
            headers=headers,
        )

    console.print("\n[bold green]Hackathon ready![/bold green]")
    console.print(f"  Rooms: {args.room1}, {args.room2}")
    console.print(f"  Agents: {agents_per_room} per room ({agents_per_room * 2} total)")
    console.print(f"  You: {INSTANCE_NAME} (in both rooms)")
    console.print(f"\n  Watch room 1: quorus watch {args.room1}")
    console.print(f"  Watch room 2: quorus watch {args.room2}")
    console.print(f"  Chat in room: quorus chat {args.room1}")


def _cmd_doctor(args):
    """Diagnose common setup issues."""
    from quorus.config import resolve_config_file
    from quorus_cli import ui

    checks_passed = 0
    checks_total = 0
    optional_passed = 0
    optional_total = 0
    verbose = args.verbose

    def check(name: str, ok: bool, detail: str = "", fix: str = "", optional: bool = False):
        nonlocal checks_passed, checks_total, optional_passed, optional_total
        if optional:
            optional_total += 1
            if ok:
                optional_passed += 1
        else:
            checks_total += 1
            if ok:
                checks_passed += 1
        if ok:
            ui.console.print(f"  [success]{ui.ICON_OK}[/]  {name}")
        else:
            icon_style = "warning" if optional else "error"
            icon = ui.ICON_WARN if optional else ui.ICON_FAIL
            suffix = " [dim](optional)[/]" if optional else ""
            name_style = "warning" if optional else "error"
            ui.console.print(f"  [{icon_style}]{icon}[/]  [{name_style}]{name}[/]{suffix}")
            if detail:
                ui.console.print(f"       [muted]{detail}[/]")
            if fix:
                ui.console.print(f"       [muted]fix:[/] [accent]{fix}[/]")
        if verbose and detail and ok:
            ui.console.print(f"       [dim]{detail}[/]")

    ui.heading("Quorus Doctor")

    # 1. Config file
    config_file = resolve_config_file()
    check(
        "Config file exists",
        config_file.exists(),
        detail=str(config_file),
        fix="Run: quorus init <name> --secret <secret>",
    )

    # 2. Relay URL configured
    check(
        "Relay URL configured",
        bool(RELAY_URL),
        detail=RELAY_URL or "(unset)",
        fix="Run: quorus init <name> --secret <secret>",
    )

    # 3. Secret configured
    check(
        "Relay secret configured",
        bool(RELAY_SECRET),
        fix="Set RELAY_SECRET env var or add relay_secret to config",
    )

    # 4. Instance name configured
    check(
        "Instance name configured",
        INSTANCE_NAME != "default",
        detail=INSTANCE_NAME,
        fix="Set INSTANCE_NAME env var or run: quorus init <name>",
    )

    # 5. Relay reachable
    relay_ok = False
    try:
        r = httpx.get(f"{RELAY_URL}/health", timeout=5)
        relay_ok = r.status_code == 200
    except Exception:
        pass
    check(
        "Relay reachable",
        relay_ok,
        detail=f"{RELAY_URL}/health",
        fix="Is the relay running? Try: quorus relay",
    )

    # 6. Auth works
    auth_ok = False
    if relay_ok:
        try:
            r = httpx.get(
                f"{RELAY_URL}/rooms",
                headers=_auth_headers(),
                timeout=5,
            )
            auth_ok = r.status_code == 200
        except Exception:
            pass
    check(
        "Authentication valid",
        auth_ok,
        fix="Check RELAY_SECRET or API_KEY matches the relay configuration",
    )

    # 7. MCP config
    mcp_json = Path.cwd() / ".mcp.json"
    home_claude = Path.home() / ".claude.json"
    mcp_found = mcp_json.exists() or home_claude.exists()
    check(
        "MCP config found",
        mcp_found,
        detail=str(mcp_json) if mcp_json.exists() else str(home_claude),
        fix="Run: quorus init <name> --secret <secret>",
    )

    # 7b. MCP server registered (quorus/claude-tunnel in mcpServers)
    mcp_registered = False
    mcp_server_name = None
    if mcp_found:
        try:
            config_path = mcp_json if mcp_json.exists() else home_claude
            config_data = json.loads(config_path.read_text())
            servers = config_data.get("mcpServers", {})
            # Look for quorus or claude-tunnel server
            for name, server in servers.items():
                args = server.get("args", [])
                args_str = " ".join(str(a) for a in args)
                if "quorus" in name.lower() or "quorus" in args_str.lower():
                    mcp_registered = True
                    mcp_server_name = name
                    break
                if "tunnel" in name.lower() or "mcp_server" in args_str:
                    mcp_registered = True
                    mcp_server_name = name
                    break
        except Exception:
            pass
    check(
        "MCP server registered",
        mcp_registered,
        detail=f"Server: {mcp_server_name}" if mcp_server_name else "",
        fix="Run: quorus init <name> --secret <secret>",
    )

    # 8. Rooms exist
    room_list = []
    if auth_ok:
        try:
            r = httpx.get(
                f"{RELAY_URL}/rooms",
                headers=_auth_headers(),
                timeout=5,
            )
            room_list = r.json()
            check(
                "Rooms exist",
                len(room_list) > 0,
                detail=f"{len(room_list)} room(s)",
                fix="Create one: quorus create <room-name>",
            )
        except Exception:
            check("Rooms exist", False)

    # 9. Room membership (is agent in any rooms?)
    if auth_ok and room_list:
        my_rooms = [r for r in room_list if INSTANCE_NAME in r.get("members", [])]
        check(
            "Room membership",
            len(my_rooms) > 0,
            detail=f"Member of {len(my_rooms)} room(s)"
            if my_rooms
            else "Not a member of any room",
            fix="Join one: quorus join <room-name>",
        )

    # 10. Relay version
    if relay_ok:
        try:
            r = httpx.get(f"{RELAY_URL}/health", timeout=5)
            health = r.json()
            version = health.get("version", "unknown")
            uptime = health.get("uptime_seconds", 0)
            uptime_str = f"{uptime // 3600}h {(uptime % 3600) // 60}m" if uptime else ""
            check(
                "Relay version",
                True,
                detail=f"v{version}" + (f" (up {uptime_str})" if uptime_str else ""),
            )
        except Exception:
            pass

    # 11. Hook status
    hook_config = Path.home() / ".claude" / "settings.json"
    hook_enabled = False
    if hook_config.exists():
        try:
            settings = json.loads(hook_config.read_text())
            hooks = settings.get("hooks", {})
            hook_enabled = any(
                "quorus" in h.get("command", "")
                for h in hooks.get("UserPromptSubmit", [])
            )
        except Exception:
            pass
    hook_detail = (
        "quorus inbox + context injected on every prompt"
        if hook_enabled
        else "opt-in feature"
    )
    check(
        "Hook auto-inject",
        hook_enabled,
        detail=hook_detail,
        fix="Run: quorus hook enable",
        optional=True,
    )

    # 12. Pending messages
    if auth_ok:
        try:
            r = httpx.get(
                f"{RELAY_URL}/messages",
                headers=_auth_headers(),
                timeout=5,
            )
            if r.status_code == 200:
                msgs = r.json()
                msg_count = len(msgs.get("messages", []))
                check(
                    "Pending messages",
                    True,
                    detail=f"{msg_count} unread" if msg_count else "Inbox empty",
                )
        except Exception:
            pass

    ui.console.print()
    pct = int(checks_passed * 100 / checks_total) if checks_total else 0
    color = "success" if pct == 100 else ("warning" if pct >= 70 else "error")
    optional_line = (
        f" [dim]· {optional_passed}/{optional_total} optional[/]"
        if optional_total else ""
    )
    ui.console.print(
        f"  [{color}]{checks_passed}/{checks_total} required checks passed[/] "
        f"[muted]({pct}%)[/]{optional_line}"
    )
    if checks_passed == checks_total:
        ui.success("You're all set — try: quorus begin")
    else:
        ui.warn("Fix the required issues above to get started")

    # Show web console link
    ui.console.print(
        "\n  [muted]Tip: Monitor your swarm at [/][accent][link=https://quorus.dev]quorus.dev[/link][/]"
    )


def _cmd_relay(args):
    """Start the relay server."""
    repo_dir = Path(__file__).resolve().parent.parent
    port = args.port
    config_path = Path.home() / ".quorus" / "config.json"

    # Read secret from config if it exists
    secret = ""
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text())
            secret = cfg.get("relay_secret", "")
        except (json.JSONDecodeError, ValueError):
            pass

    if not secret:
        secret = os.environ.get("RELAY_SECRET", "")
    if not secret:
        console.print("[red]Error: No relay_secret configured.[/red]")
        console.print("Run: quorus init <name> --secret <your-secret>")
        sys.exit(1)

    console.print(f"[bold]Starting Quorus relay on port {port}[/bold]")
    console.print("  Press Ctrl+C to stop")
    console.print("")

    env = os.environ.copy()
    env["RELAY_SECRET"] = secret
    env["PORT"] = str(port)

    try:
        subprocess.run(
            [
                "uv", "run", "--directory", str(repo_dir),
                "python", "-m", "uvicorn", "quorus.relay:app",
                "--host", "0.0.0.0", "--port", str(port),
            ],
            env=env,
        )
    except KeyboardInterrupt:
        console.print("\n[dim]Relay stopped.[/dim]")


def _cmd_version(args):
    from quorus import __version__
    from quorus_cli import ui

    ui.banner(version=__version__)
    ui.info(f"Relay URL: [primary]{RELAY_URL}[/]")
    ui.info(f"Instance:  [agent]{INSTANCE_NAME}[/]")
    ui.info(f"Config:    [muted]{_config.get('config_file', '~/.quorus/config.json')}[/]")


def _cmd_logs(args):
    """Tail relay analytics and recent activity."""
    import httpx as httpx_mod

    try:
        r = httpx_mod.get(
            f"{RELAY_URL}/analytics",
            headers=_auth_headers(),
            timeout=5,
        )
        r.raise_for_status()
        stats = r.json()

        console.print("[bold]Quorus Relay Logs[/bold]\n")
        console.print(f"  Uptime: {stats['uptime_seconds'] // 60}m {stats['uptime_seconds'] % 60}s")
        console.print(f"  Messages sent: {stats['total_messages_sent']}")
        console.print(f"  Messages delivered: {stats['total_messages_delivered']}")
        console.print(f"  Messages pending: {stats['messages_pending']}")

        per_participant = stats.get("participants", {})
        if per_participant:
            console.print("\n[bold]Per Participant[/bold]")
            table = Table()
            table.add_column("Name", style="bold")
            table.add_column("Sent", justify="right")
            table.add_column("Received", justify="right")
            for name, data in sorted(per_participant.items()):
                table.add_row(name, str(data["sent"]), str(data["received"]))
            console.print(table)

        hourly = stats.get("hourly_volume", [])
        if hourly:
            console.print("\n[bold]Hourly Volume[/bold]")
            for entry in hourly[-12:]:  # last 12 hours
                hour = entry["hour"][:16]
                count = entry["count"]
                bar = "[green]" + "#" * min(count, 50) + "[/green]"
                console.print(f"  {hour}  {bar} {count}")

    except httpx_mod.ConnectError:
        _relay_unreachable()
    except httpx_mod.HTTPStatusError as e:
        console.print(f"[red]Error: {e.response.status_code}[/red]")


def _cmd_brief(args):
    """Drop a task brief into a room for agents to claim subtasks from."""
    import uuid

    room = args.room
    task = " ".join(args.task) if isinstance(args.task, list) else args.task

    if not task or not task.strip():
        console.print("[red]Error: task text cannot be empty[/red]")
        sys.exit(1)

    _MAX_BRIEF_LEN = 4000
    if len(task) > _MAX_BRIEF_LEN:
        console.print(
            f"[yellow]Warning: task text exceeds {_MAX_BRIEF_LEN} chars "
            f"({len(task)} chars) — truncating[/yellow]"
        )
        task = task[:_MAX_BRIEF_LEN]

    brief_id = str(uuid.uuid4())
    headers = _auth_headers()

    try:
        resp = httpx.post(
            f"{RELAY_URL}/rooms/{room}/messages",
            json={
                "from_name": INSTANCE_NAME,
                "content": task,
                "message_type": "brief",
                "brief_id": brief_id,
            },
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
    except httpx.ConnectError:
        _relay_unreachable()
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            console.print(f"[red]Room '{room}' not found[/red]")
        else:
            console.print(f"[red]Error: {e.response.status_code} {e.response.text}[/red]")
        sys.exit(1)

    console.print(f"[bold green]Brief posted to '{room}'[/bold green]")
    console.print(f"  brief_id: [dim]{brief_id}[/dim]")
    console.print(f"  task:     {task}")

    # Auto-decompose into subtasks via Claude API
    subtasks = _decompose_brief(task, brief_id)
    if subtasks:
        # Post each subtask as a separate message
        for subtask in subtasks:
            try:
                httpx.post(
                    f"{RELAY_URL}/rooms/{room}/messages",
                    json={
                        "from_name": INSTANCE_NAME,
                        "content": subtask,
                        "message_type": "subtask",
                        "brief_id": brief_id,
                    },
                    headers=headers,
                    timeout=10,
                ).raise_for_status()
            except (httpx.ConnectError, httpx.HTTPStatusError):
                console.print(f"[yellow]Warning: failed to post subtask: {subtask}[/yellow]")

        # Display summary table
        table = Table(title=f"Brief Decomposition — {brief_id[:8]}", show_lines=True)
        table.add_column("Brief ID", style="dim", no_wrap=True)
        table.add_column("Task", style="bold")
        table.add_column("Subtasks", style="cyan")
        table.add_row(brief_id[:8], task, "\n".join(f"{i+1}. {s}" for i, s in enumerate(subtasks)))
        console.print(table)


def _decompose_brief(task: str, brief_id: str) -> list[str]:
    """Call Claude to break a brief into subtasks. Returns [] on any failure."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        console.print("[yellow]ANTHROPIC_API_KEY not set — skipping subtask decomposition[/yellow]")
        return []
    try:
        import anthropic
    except ImportError:
        console.print("[yellow]anthropic not installed — skipping subtask decomposition[/yellow]")
        return []

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": (
                    "Break this task into 3-5 specific, independently claimable subtasks "
                    "for AI agents working in parallel on a software project. "
                    "Return ONLY a numbered list, one subtask per line, no explanation.\n\n"
                    f"Task: {task}"
                ),
            }],
        )
        raw = response.content[0].text.strip() if response.content else ""
        if not raw:
            console.print(
                "[yellow]Claude returned an empty response — skipping decomposition[/yellow]"
            )
            return []
        subtasks = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            # Strip leading "1. " / "1) " / "- " prefixes
            import re
            cleaned = re.sub(r"^[\d]+[.)]\s*|-\s*", "", line).strip()
            # Require at least 10 chars to filter out garbled/fragment output
            if cleaned and len(cleaned) >= 10:
                subtasks.append(cleaned)
        return subtasks
    except Exception as exc:
        console.print(f"[yellow]Subtask decomposition failed: {exc}[/yellow]")
        return []


async def _show_board(room_filter: str | None = None) -> None:
    """Fetch all rooms + their state in parallel, then render the board."""
    import datetime as _dt

    client = _get_client()
    try:
        # 1. List all rooms
        resp = await client.get(f"{RELAY_URL}/rooms", headers=_auth_headers())
        resp.raise_for_status()
        rooms = resp.json()
    except httpx.ConnectError:
        _relay_unreachable()
        await client.aclose()
        return
    except httpx.HTTPStatusError as e:
        console.print(f"[red]Error listing rooms: {e.response.status_code}[/red]")
        await client.aclose()
        return

    if room_filter:
        rooms = [r for r in rooms if r["name"] == room_filter]
        if not rooms:
            console.print(f"[red]Room '{room_filter}' not found[/red]")
            await client.aclose()
            return

    if not rooms:
        console.print("[dim]No rooms yet.[/dim]")
        await client.aclose()
        return

    # 2. Fetch all room states in parallel
    async def fetch_state(room: dict) -> dict:
        try:
            r = await client.get(
                f"{RELAY_URL}/rooms/{room['id']}/state",
                headers=_auth_headers(),
                timeout=10,
            )
            r.raise_for_status()
            return r.json()
        except Exception:
            return {}

    states = await asyncio.gather(*[fetch_state(r) for r in rooms])

    # 3. Render summary table
    table = Table(title="Quorus Swarm Board", show_lines=True)
    table.add_column("Room", style="bold cyan", no_wrap=True)
    table.add_column("Active Brief / Goal", style="white")
    table.add_column("Agents", style="green", justify="center")
    table.add_column("Claimed Tasks", style="yellow", justify="center")

    room_details: list[tuple[str, list[dict]]] = []

    for room, state in zip(rooms, states):
        goal = state.get("active_goal") or "[dim](none)[/dim]"
        agents = state.get("active_agents") or room.get("members", [])
        claimed = state.get("claimed_tasks", [])
        table.add_row(
            room["name"],
            goal,
            str(len(agents)),
            str(len(claimed)),
        )
        room_details.append((room["name"], claimed, agents))

    console.print(table)

    # 4. Per-room claimed task detail
    any_claimed = any(claimed for _, claimed, _ in room_details)
    if any_claimed:
        console.print()
        for room_name, claimed, _agents in room_details:
            if not claimed:
                continue
            console.print(f"[bold]{room_name}:[/bold]")
            for task in claimed:
                agent = task.get("claimed_by") or task.get("agent") or "?"
                content = task.get("content") or task.get("task") or str(task)
                ts = task.get("claimed_at") or task.get("timestamp") or ""
                age_str = ""
                if ts:
                    try:
                        diff = round(
                            (
                                _dt.datetime.now(_dt.timezone.utc)
                                - _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            ).total_seconds()
                        )
                        if diff < 60:
                            age_str = f" (claimed {diff}s ago)"
                        else:
                            age_str = f" (claimed {diff // 60}m ago)"
                    except ValueError:
                        pass
                console.print(f"  [cyan]•[/cyan] [{agent}] {content}{age_str}")

    await client.aclose()


def _cmd_board(args):
    asyncio.run(_show_board(getattr(args, "room", None)))


def _cmd_setup_swarm(args):
    """Create N rooms each with a purpose and M agents."""
    rooms_raw = args.rooms
    agents_per_room = args.agents
    headers = _auth_headers()

    # Parse "name:purpose,name:purpose,..." into list of dicts
    rooms_config = []
    for pair in rooms_raw.split(","):
        pair = pair.strip()
        if ":" in pair:
            name, _, purpose = pair.partition(":")
            rooms_config.append({"name": name.strip(), "purpose": purpose.strip()})
        else:
            rooms_config.append({"name": pair, "purpose": "Build and ship."})

    if not rooms_config:
        console.print("[red]No rooms specified. Use --rooms 'name:purpose,...'[/red]")
        sys.exit(1)

    console.print("[bold green]Quorus Swarm Setup[/bold green]\n")

    created_rooms = []
    for room_cfg in rooms_config:
        room_name = room_cfg["name"]
        purpose = room_cfg["purpose"]

        r = httpx.post(
            f"{RELAY_URL}/rooms",
            json={"name": room_name, "created_by": INSTANCE_NAME},
            headers=headers,
        )
        if r.status_code == 200:
            console.print(f"  [green]Room '{room_name}' created[/green]")
        elif r.status_code == 409:
            console.print(f"  [yellow]Room '{room_name}' already exists[/yellow]")
        else:
            console.print(f"  [red]Failed to create room '{room_name}': {r.text}[/red]")
            continue

        # Join the human to this room
        httpx.post(
            f"{RELAY_URL}/rooms/{room_name}/join",
            json={"participant": INSTANCE_NAME},
            headers=headers,
        )

        # Spawn agents
        for i in range(1, agents_per_room + 1):
            agent_name = f"{room_name}-agent-{i}"
            console.print(f"  Spawning {agent_name}...")
            _spawn_agent(room_name, agent_name, RELAY_URL, RELAY_SECRET)

        # Send purpose briefing
        httpx.post(
            f"{RELAY_URL}/rooms/{room_name}/messages",
            json={
                "from_name": INSTANCE_NAME,
                "content": f"SWARM MISSION: {purpose}\n\n"
                f"Team: {agents_per_room} agents. Coordinate here. "
                "Claim tasks, post status, don't duplicate work. Ship it.",
                "message_type": "alert",
            },
            headers=headers,
        )

        created_rooms.append(room_name)

    # Summary table
    console.print()
    table = Table(title="Swarm Summary")
    table.add_column("Room", style="bold")
    table.add_column("Purpose")
    table.add_column("Agents", justify="right")
    for room_cfg in rooms_config:
        if room_cfg["name"] in created_rooms:
            table.add_row(room_cfg["name"], room_cfg["purpose"], str(agents_per_room))
    console.print(table)

    total = agents_per_room * len(created_rooms)
    console.print("\n[bold green]Swarm ready![/bold green]")
    console.print(f"  {len(created_rooms)} rooms, {total} total agents")
    for rn in created_rooms:
        console.print(f"  Watch: quorus watch {rn}")


def _cmd_resolve(args):
    """Resolve git merge conflicts using room history as context."""
    import re

    # Check for anthropic
    try:
        import anthropic
    except ImportError:
        console.print("[red]anthropic package not installed.[/red]")
        console.print("[dim]Run: pip install anthropic[/dim]")
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        console.print("[red]ANTHROPIC_API_KEY not set.[/red]")
        console.print("[dim]Export ANTHROPIC_API_KEY=<your-key> and retry.[/dim]")
        sys.exit(1)

    # 1. Find conflicted files
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=U"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        console.print("[red]git diff failed — are you in a git repo?[/red]")
        console.print(f"[dim]{result.stderr.strip()}[/dim]")
        sys.exit(1)

    conflicted_files = [f for f in result.stdout.strip().splitlines() if f]
    if not conflicted_files:
        console.print("[green]No merge conflicts found.[/green]")
        return

    console.print(f"[bold]Found {len(conflicted_files)} conflicted file(s)[/bold]")

    room = getattr(args, "room", None)
    model = getattr(args, "model", "claude-sonnet-4-6")

    # 2. Fetch room history for context (optional)
    room_context_msgs: list[str] = []
    if room:
        try:
            resp = httpx.get(
                f"{RELAY_URL}/rooms/{room}/history",
                params={"limit": 100},
                headers=_auth_headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                history = resp.json()
                for msg in history:
                    content = msg.get("content", "")
                    sender = msg.get("from_name", "?")
                    # Only include messages that mention any conflicted filename
                    if any(Path(f).name in content for f in conflicted_files):
                        ts = msg.get("timestamp", "")[:16]
                        room_context_msgs.append(f"[{ts}] {sender}: {content}")
        except Exception:
            pass  # Room context is optional

    # 3. Process each conflicted file
    import httpx as _httpx
    client = anthropic.Anthropic(
        api_key=api_key,
        http_client=_httpx.Client(
            timeout=_httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0),
        ),
    )

    for filepath in conflicted_files:
        console.print(f"\n[bold cyan]Resolving: {filepath}[/bold cyan]")

        try:
            file_content = Path(filepath).read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            console.print(f"[red]File not found: {filepath}[/red]")
            continue

        # Extract conflict blocks
        conflict_pattern = re.compile(
            r"(<{7} .+?\n[\s\S]*?={7}\n[\s\S]*?>{7} .+?\n)",
            re.MULTILINE,
        )
        conflicts = conflict_pattern.findall(file_content)
        if not conflicts:
            console.print(f"[dim]No conflict markers in {filepath}, skipping[/dim]")
            continue

        # Build prompt
        context_section = ""
        if room_context_msgs:
            ctx = "\n".join(room_context_msgs[-20:])
            context_section = f"\n\nTeam chat context from room '{room}':\n{ctx}"

        prompt = (
            "You are a conflict resolution agent. "
            f"Here is a git merge conflict in `{filepath}` "
            "and the context from the team's chat history. "
            "Propose a clean resolution that honors both agents' intentions.\n\n"
            f"Full file with conflict markers:\n```\n{file_content}\n```"
            f"{context_section}\n\n"
            "Respond with:\n"
            "1. A brief explanation of what each side changed and why the conflict occurred.\n"
            "2. The complete resolved file content (no conflict markers) in a fenced code block.\n"
            "Keep it concise."
        )

        console.print("[dim]Calling Claude...[/dim]")
        try:
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as e:
            console.print(f"[red]Claude API error: {e}[/red]")
            continue

        reply = response.content[0].text
        console.print()
        console.print(reply)

        # Extract resolved file from fenced code block
        code_match = re.search(r"```(?:\w+)?\n([\s\S]+?)```", reply)
        if not code_match:
            console.print("[yellow]Could not extract resolved file from response.[/yellow]")
            continue

        resolved = code_match.group(1)

        console.print()
        apply = Confirm.ask(f"Apply this resolution to {filepath}?", default=False)
        if apply:
            Path(filepath).write_text(resolved, encoding="utf-8")
            console.print(f"[green]Written: {filepath}[/green]")
            console.print(f"[dim]Run 'git add {filepath}' to stage.[/dim]")
        else:
            console.print("[dim]Skipped.[/dim]")


# ---------------------------------------------------------------------------
# quorus context [--room <room>] [--limit N] [--quiet] [--json]
# Summary Cascade v1 — inject a briefing of everything relevant in a room
# ---------------------------------------------------------------------------

_HIGH_SIGNAL_TYPES = {"brief", "subtask", "claim", "status", "alert", "sync", "decision"}


def _relative_time(iso_ts: str) -> str:
    """Return a human-readable relative time string (e.g. '2h ago')."""
    import datetime as _dt
    try:
        ts = _dt.datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        diff = round((_dt.datetime.now(_dt.timezone.utc) - ts).total_seconds())
        if diff < 60:
            return f"{diff}s ago"
        elif diff < 3600:
            return f"{diff // 60}m ago"
        elif diff < 86400:
            return f"{diff // 3600}h ago"
        else:
            return f"{diff // 86400}d ago"
    except (ValueError, TypeError):
        return iso_ts[:16] if iso_ts else ""


async def _context(
    room_name: str | None,
    limit: int = 200,
    quiet: bool = False,
    json_output: bool = False,
    summarize: bool = False,
    model: str = "claude-sonnet-4-6",
) -> None:
    """Fetch and display a Summary Cascade briefing for a room.

    With --summarize (v2), calls Claude to produce a concise 2-3 paragraph
    brief instead of the raw context dump.
    """
    import datetime as _dt

    # Resolve room: use provided name, fall back to first room the agent is in
    target_room = room_name
    client = _get_client()
    try:
        if not target_room:
            rooms_resp = await client.get(
                f"{RELAY_URL}/rooms", headers=_auth_headers()
            )
            rooms_resp.raise_for_status()
            all_rooms = rooms_resp.json()
            # Pick the first room this instance is a member of
            for r in all_rooms:
                if INSTANCE_NAME in r.get("members", []):
                    target_room = r["name"]
                    break
            if not target_room and all_rooms:
                target_room = all_rooms[0]["name"]
            if not target_room:
                if not quiet:
                    console.print("[red]No rooms found.[/red]")
                return

        # Fetch state and history in parallel
        state_resp, hist_resp = await asyncio.gather(
            client.get(
                f"{RELAY_URL}/rooms/{target_room}/state",
                headers=_auth_headers(),
            ),
            client.get(
                f"{RELAY_URL}/rooms/{target_room}/history",
                params={"limit": limit},
                headers=_auth_headers(),
            ),
        )
        state_resp.raise_for_status()
        hist_resp.raise_for_status()

        state = state_resp.json()
        history = hist_resp.json()

        # Filter history to high-signal message types only
        seen_content: set[str] = set()
        high_signal: list[dict] = []
        for msg in history:
            mtype = msg.get("message_type", "chat")
            if mtype not in _HIGH_SIGNAL_TYPES:
                continue
            content_key = f"{mtype}:{msg.get('content', '')}"
            if content_key in seen_content:
                continue
            seen_content.add(content_key)
            high_signal.append(msg)

        # Bucket filtered messages by type
        briefs = [m for m in high_signal if m.get("message_type") == "brief"]
        decisions = [m for m in high_signal if m.get("message_type") == "decision"]
        status_updates = [m for m in high_signal if m.get("message_type") == "status"]
        # subtasks and claims are surfaced through room state claimed_tasks

        if json_output:
            output = {
                "room": target_room,
                "active_goal": state.get("active_goal"),
                "claimed_tasks": state.get("claimed_tasks", []),
                "locked_files": state.get("locked_files", {}),
                "resolved_decisions": state.get("resolved_decisions", []),
                "recent_briefs": briefs[-10:],
                "recent_decisions": decisions[-10:],
                "recent_status_updates": status_updates[-10:],
            }
            print(json.dumps(output, indent=2))
            return

        # Build the context block as plain text (for piping / injection)
        lines: list[str] = []

        if not quiet:
            lines.append(f"=== Room Context: {target_room} ===")
            lines.append("")

        # Active goal
        goal = state.get("active_goal") or "(none set)"
        lines.append(f"Active Goal: {goal}")
        lines.append("")

        # Recent briefs
        if briefs:
            lines.append("Recent Briefs:")
            for b in briefs[-5:]:
                bid = b.get("brief_id") or b.get("id", "")
                bid_short = bid[:8] if bid else "?"
                content = b.get("content", "")[:80]
                age = _relative_time(b.get("timestamp", ""))
                lines.append(f"  - [{bid_short}] {content} ({age})")
            lines.append("")

        # Claimed tasks (from room state)
        claimed = state.get("claimed_tasks", [])
        if claimed:
            lines.append("Claimed Tasks:")
            for task in claimed:
                agent = task.get("claimed_by") or task.get("agent") or "?"
                desc = task.get("description") or task.get("content") or task.get("task") or ""
                if not desc:
                    desc = task.get("file_path", "")
                lines.append(f"  - [{agent}] {desc}")
            lines.append("")

        # Recent decisions (from resolved_decisions in state + decision messages)
        resolved = state.get("resolved_decisions", [])
        all_decisions = resolved[-5:] + [
            {"decision": d.get("content", ""), "decided_at": d.get("timestamp", "")}
            for d in decisions[-5:]
            if d.get("content", "")
            not in {r.get("decision", "") for r in resolved[-5:]}
        ]
        if all_decisions:
            lines.append("Recent Decisions:")
            for dec in all_decisions[-5:]:
                text = dec.get("decision") or dec.get("content") or ""
                ts = dec.get("decided_at") or dec.get("timestamp") or ""
                age = _relative_time(ts) if ts else ""
                suffix = f" ({age})" if age else ""
                lines.append(f"  - {text}{suffix}")
            lines.append("")

        # Recent status updates
        if status_updates:
            lines.append("Recent Status Updates:")
            for su in status_updates[-5:]:
                sender = su.get("from_name", "?")
                content = su.get("content", "")[:100]
                age = _relative_time(su.get("timestamp", ""))
                lines.append(f"  - [{sender}] {content} ({age})")
            lines.append("")

        # Locked files
        locked = state.get("locked_files", {})
        if locked:
            lines.append("Locked Files:")
            now = _dt.datetime.now(_dt.timezone.utc)
            for fp, info in locked.items():
                holder = info.get("held_by") or info.get("claimed_by") or "?"
                exp = info.get("expires_at", "")
                ttl_str = ""
                if exp:
                    try:
                        diff = max(
                            0,
                            round(
                                (
                                    _dt.datetime.fromisoformat(exp.replace("Z", "+00:00"))
                                    - now
                                ).total_seconds()
                            ),
                        )
                        m, s = divmod(diff, 60)
                        ttl_str = f" (expires in {m}m {s:02d}s)"
                    except ValueError:
                        pass
                lines.append(f"  - {fp} -> {holder}{ttl_str}")
            lines.append("")

        block = "\n".join(lines)
        # Strip trailing blank lines for clean injection
        block = block.rstrip()

        # Summary Cascade v2: LLM summarization
        if summarize and block:
            try:
                import anthropic
            except ImportError:
                console.print("[red]anthropic package not installed.[/red]")
                console.print("[dim]Run: pip install anthropic[/dim]")
                return

            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                console.print("[red]ANTHROPIC_API_KEY not set.[/red]")
                console.print("[dim]Export ANTHROPIC_API_KEY=<your-key> and retry.[/dim]")
                return

            # Build context for summarization
            summary_prompt = f"""\
You are a swarm coordinator briefing an agent joining a room.

Room: {target_room}

Raw context data:
{block}

Write a concise 2-3 paragraph brief that:
1. States the active goal and current priorities
2. Summarizes who is working on what (claimed tasks, locked files)
3. Notes any recent decisions or blockers the agent should know about

Be direct and actionable. No pleasantries. This will be injected into the agent's context.
"""
            if not quiet:
                console.print("[dim]Generating summary...[/dim]")

            try:
                llm_client = anthropic.Anthropic(api_key=api_key)
                response = llm_client.messages.create(
                    model=model,
                    max_tokens=1024,
                    messages=[{"role": "user", "content": summary_prompt}],
                )
                summary = response.content[0].text

                if not quiet:
                    console.print(f"\n[bold cyan]=== Summary: {target_room} ===[/bold cyan]\n")
                print(summary)
            except Exception as e:
                console.print(f"[red]LLM error: {e}[/red]")
                console.print("[dim]Falling back to raw context:[/dim]")
                print(block)
            return

        if block:
            print(block)

    except httpx.ConnectError:
        if not quiet:
            _relay_unreachable()
    except httpx.HTTPStatusError as e:
        if not quiet:
            if e.response.status_code == 404:
                console.print(f"[red]Room '{target_room}' not found[/red]")
            else:
                console.print(f"[red]Error: {e.response.status_code}[/red]")
    finally:
        await client.aclose()


def _cmd_context(args):
    asyncio.run(
        _context(
            room_name=getattr(args, "room", None),
            limit=getattr(args, "limit", 200),
            quiet=getattr(args, "quiet", False),
            json_output=getattr(args, "json_output", False),
            summarize=getattr(args, "summarize", False),
            model=getattr(args, "model", "claude-sonnet-4-6"),
        )
    )


# ---------------------------------------------------------------------------
# quorus decision <room> <decision...>
# Record an architectural/coordination decision into the room
# ---------------------------------------------------------------------------


def _cmd_decision(args):
    """Record a decision into the room via the state decisions endpoint."""
    room = args.room
    decision_text = " ".join(args.decision) if isinstance(args.decision, list) else args.decision

    if not decision_text.strip():
        console.print("[red]Decision text cannot be empty.[/red]")
        return

    headers = _auth_headers()
    try:
        resp = httpx.post(
            f"{RELAY_URL}/rooms/{room}/state/decisions",
            json={"decision": decision_text},
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.ConnectError:
        _relay_unreachable()
        return
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            console.print(f"[red]Room '{room}' not found[/red]")
        else:
            console.print(f"[red]Error: {e.response.status_code} {e.response.text}[/red]")
        return

    did = result.get("id", "")
    did_short = did[:8] if did else "?"
    console.print(f"[bold green]Decision recorded in '{room}'[/bold green]")
    console.print(f"  id:       [dim]{did_short}[/dim]")
    console.print(f"  decision: {decision_text}")


def _cmd_begin(args):
    """Open the Quorus interactive TUI hub."""
    from quorus_tui.hub import run_hub
    run_hub()


def _print_grouped_help():
    """Custom help output with commands grouped by category."""
    from quorus import __version__
    from quorus_cli import ui

    ui.banner(version=__version__)

    groups = [
        ("GET STARTED", [
            ("init <name>",        "One-command setup (name + relay + secret)"),
            ("begin",              "Open the interactive hub (default if no command)"),
            ("doctor",             "Diagnose setup issues"),
            ("version",            "Show version, relay, and config"),
        ]),
        ("ROOMS & MESSAGING", [
            ("rooms",              "List all rooms"),
            ("create <name>",      "Create a room"),
            ("join <room>",        "Join a room"),
            ("chat <room>",        "Interactive chat in a room"),
            ("say <room> <msg>",   "Send a message to a room"),
            ("dm <name> <msg>",    "Direct message another agent"),
            ("inbox",              "Check pending messages"),
            ("history <room>",     "Show room message history"),
            ("search <room> <q>",  "Search room history"),
        ]),
        ("COORDINATION", [
            ("state <room>",       "Show shared state (goal, locks, tasks)"),
            ("locks <room>",       "Show active file locks"),
            ("context [--room R]", "Get a briefing of current room context"),
            ("decision <room> <t>","Record an architectural decision"),
            ("resolve",            "AI-powered merge conflict resolution"),
            ("board",              "Live swarm task board"),
        ]),
        ("AGENTS & SWARMS", [
            ("ps",                 "Show agent presence (online/offline)"),
            ("spawn <name>",       "Create agent workspace + launch Claude Code"),
            ("add-agent",          "Interactive wizard to add an agent"),
            ("quickstart",         "One-command demo (solo swarm)"),
            ("hackathon",          "Two-room hackathon setup"),
            ("share <room>",       "Generate a portable join token"),
            ("quickjoin <token>",  "Join with zero config from token"),
        ]),
        ("OPS & ADMIN", [
            ("relay",              "Start the relay server locally"),
            ("status",             "Relay health and stats"),
            ("logs",               "Per-participant activity log"),
            ("metrics <room>",     "Room activity analytics"),
            ("usage",              "Tenant-wide usage metrics"),
            ("export <room>",      "Export room history (json/md)"),
            ("kick <room> <who>",  "Remove a member from a room"),
            ("rename <room> <new>","Rename a room"),
            ("destroy <room>",     "Delete a room"),
            ("hook enable",        "Auto-inject inbox on UserPromptSubmit"),
        ]),
    ]

    for title, items in groups:
        ui.console.print(f"\n[heading]{title}[/]")
        for cmd, desc in items:
            ui.console.print(f"  [accent]{cmd:<22}[/] [muted]{desc}[/]")

    ui.console.print(
        "\n[dim]Run 'quorus <command> --help' for command-specific options.[/]"
    )
    ui.console.print(
        "[dim]Docs: https://quorus.dev[/]\n"
    )


def main():
    # Intercept top-level --help / -h for custom grouped output
    if len(sys.argv) == 2 and sys.argv[1] in ("-h", "--help", "help"):
        _print_grouped_help()
        return

    parser = argparse.ArgumentParser(
        prog="quorus",
        description="Quorus — Coordination layer for AI agent swarms",
        add_help=True,
    )
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="One-command setup")
    p_init.add_argument("name", help="Your participant name")
    p_init.add_argument("--relay-url", default="http://localhost:8080", help="Relay URL")
    p_init.add_argument(
        "--config-dir",
        default=None,
        help="Override config dir (default: ~/.quorus; also honors $QUORUS_CONFIG_DIR)",
    )
    p_init_auth = p_init.add_mutually_exclusive_group(required=True)
    p_init_auth.add_argument("--secret", help="Shared secret (legacy auth)")
    p_init_auth.add_argument("--api-key", help="API key (production auth)")

    p_relay = sub.add_parser("relay", help="Start the relay server")
    p_relay.add_argument("--port", type=int, default=8080, help="Port")

    sub.add_parser("rooms", help="List all rooms")

    p_create = sub.add_parser("create", help="Create a room")
    p_create.add_argument("name", help="Room name")

    p_invite = sub.add_parser(
        "invite", help="Invite participants to a room"
    )
    p_invite.add_argument("room", help="Room name")
    p_invite.add_argument(
        "participants", nargs="+", help="Participant names"
    )

    p_members = sub.add_parser("members", help="List room members")
    p_members.add_argument("room", help="Room name")

    p_say = sub.add_parser("say", help="Send message to a room")
    p_say.add_argument("room", help="Room name")
    p_say.add_argument("message", help="Message content")

    p_dm = sub.add_parser("dm", help="Send direct message")
    p_dm.add_argument("to", help="Recipient name")
    p_dm.add_argument("message", help="Message content")

    p_inbox = sub.add_parser("inbox", help="Check for pending messages")
    p_inbox.add_argument("--quiet", "-q", action="store_true", help="Minimal output")
    p_inbox.add_argument("--json", action="store_true", help="JSON output")

    p_hook = sub.add_parser("hook", help="Manage Claude Code message hook")
    p_hook.add_argument(
        "action",
        choices=["enable", "disable", "status"],
        help="enable/disable/status",
    )

    p_watch = sub.add_parser("watch", help="Watch a room live")
    p_watch.add_argument("room", help="Room name")

    p_chat = sub.add_parser(
        "chat", help="Interactive chat in a room"
    )
    p_chat.add_argument("room", help="Room name")

    p_history = sub.add_parser("history", help="Show room message history")
    p_history.add_argument("room", help="Room name")
    p_history.add_argument(
        "--limit", type=int, default=50, help="Max messages (default 50)"
    )

    p_search = sub.add_parser(
        "search", help="Search room history by keyword, sender, or type"
    )
    p_search.add_argument("room", help="Room name")
    p_search.add_argument("query", nargs="?", default="", help="Search keyword")
    p_search.add_argument(
        "--sender", default="", help="Filter by sender name"
    )
    p_search.add_argument(
        "--type", default="", help="Filter by message type (chat, claim, status, etc.)"
    )
    p_search.add_argument(
        "--limit", type=int, default=50, help="Max results (default 50)"
    )

    p_metrics = sub.add_parser(
        "metrics", help="Show room activity stats and analytics"
    )
    p_metrics.add_argument("room", help="Room name")

    p_export = sub.add_parser(
        "export", help="Export room history as JSON or Markdown"
    )
    p_export.add_argument("room", help="Room name")
    p_export.add_argument(
        "--format", default="json", choices=["json", "md"],
        help="Output format (default: json)"
    )
    p_export.add_argument(
        "--output", "-o", default=None, help="Write to file instead of stdout"
    )
    p_export.add_argument(
        "--limit", type=int, default=1000,
        help="Max messages to export (default 1000)"
    )

    sub.add_parser("ps", help="Show agent presence (online/offline)")
    sub.add_parser("status", help="Show relay health and stats")

    p_doctor = sub.add_parser("doctor", help="Diagnose setup issues")
    p_doctor.add_argument("--verbose", "-v", action="store_true", help="Show extra details")
    sub.add_parser("version", help="Show quorus-ai version")
    sub.add_parser("logs", help="Show relay activity and per-participant stats")

    p_invite_link = sub.add_parser(
        "invite-link", help="Generate a join command to share"
    )
    p_invite_link.add_argument("room", help="Room name")

    p_spawn = sub.add_parser(
        "spawn", help="Create agent workspace and launch Claude Code"
    )
    p_spawn.add_argument("room", help="Room name")
    p_spawn.add_argument("name", help="Agent name")

    p_spawn_multi = sub.add_parser(
        "spawn-multiple", help="Spawn N agents at once"
    )
    p_spawn_multi.add_argument("room", help="Room name")
    p_spawn_multi.add_argument("count", type=int, help="Number of agents")
    p_spawn_multi.add_argument(
        "--prefix", default="agent", help="Name prefix (default: agent)"
    )

    p_quickstart = sub.add_parser(
        "quickstart", help="One-command demo: relay + room + agents"
    )
    p_quickstart.add_argument(
        "--agents", type=int, default=2, help="Number of agents (default 2)"
    )

    p_watch_daemon = sub.add_parser(
        "watch-daemon", help="Background SSE listener writing to inbox file"
    )
    p_watch_daemon.add_argument("name", help="Agent/participant name to watch")

    p_watch_context = sub.add_parser(
        "watch-context", help="Background watcher writing room context to .quorus/context.md"
    )
    p_watch_context.add_argument("room", help="Room name to watch")
    p_watch_context.add_argument(
        "--output", "-o", default=None, help="Context file path (default: .quorus/context.md)"
    )

    p_hack = sub.add_parser(
        "hackathon", help="Set up hackathon: 2 rooms + agents with missions"
    )
    p_hack.add_argument("--room1", default="yc-hack", help="First room (default: yc-hack)")
    p_hack.add_argument("--room2", default="openai-hack", help="Second room (default: openai-hack)")
    p_hack.add_argument("--agents", type=int, default=3, help="Agents per room (default: 3)")
    p_hack.add_argument("--mission1", default=None, help="Mission for room 1")
    p_hack.add_argument("--mission2", default=None, help="Mission for room 2")

    p_state = sub.add_parser("state", help="Show room state: goal, agents, locks, stats")
    p_state.add_argument("room", help="Room name")

    p_locks = sub.add_parser("locks", help="Show active file locks in a room")
    p_locks.add_argument("room", help="Room name")

    sub.add_parser("usage", help="Show global relay usage stats")

    p_join = sub.add_parser(
        "join",
        help="Join a room — pass a quorus_join_ (or legacy murm_join_) token or explicit flags",
    )
    p_join.add_argument("--name", required=True, help="Your participant name")
    p_join.add_argument(
        "token",
        nargs="?",
        default=None,
        help="Join token (quorus_join_…). When provided, --relay/--secret/--room are not needed.",
    )
    p_join.add_argument("--relay", dest="relay_url", default=None, help="Relay URL")
    p_join_auth = p_join.add_mutually_exclusive_group(required=False)
    p_join_auth.add_argument("--secret", help="Shared secret (legacy auth)")
    p_join_auth.add_argument("--api-key", help="API key (production auth)")
    p_join.add_argument("--room", default=None, help="Room to join")

    p_invite_token = sub.add_parser(
        "invite-token",
        help="Generate a one-line join token to share with agents",
    )
    p_invite_token.add_argument("room", help="Room name")

    p_share = sub.add_parser("share", help="Generate a portable join token for a room")
    p_share.add_argument("room", help="Room name")
    p_share.add_argument("--ttl", type=int, default=7, help="Token expiry in days (default: 7)")

    p_quickjoin = sub.add_parser(
        "quickjoin", help="Join a room using a portable token (zero config)"
    )
    p_quickjoin.add_argument("token", help="Join token (quorus://...)")
    p_quickjoin.add_argument("--name", required=True, help="Your participant name")

    p_kick = sub.add_parser("kick", help="Kick a participant from a room (admin)")
    p_kick.add_argument("room", help="Room name")
    p_kick.add_argument("name", help="Participant to kick")

    p_destroy = sub.add_parser("destroy", help="Delete a room and its history (admin)")
    p_destroy.add_argument("room", help="Room name")

    p_rename = sub.add_parser("rename", help="Rename a room (admin)")
    p_rename.add_argument("room", help="Current room name")
    p_rename.add_argument("new_name", help="New room name")

    sub.add_parser(
        "add-agent", help="Interactive wizard to create and launch an agent"
    )

    p_connect = sub.add_parser(
        "connect",
        help="Generate setup for any agent platform (codex, cursor, ollama, claude)",
    )
    p_connect.add_argument(
        "platform",
        choices=[
            "claude",
            "cursor",
            "gemini",
            "windsurf",
            "opencode",
            "cline",
            "continue",
            "antigravity",
            "codex",
            "aider",
            "ollama",
            "http",
        ],
        help="Agent platform (http = generic curl/HTTP for any AI that can make requests)",
    )
    p_connect.add_argument("room", help="Room name")
    p_connect.add_argument("name", help="Agent name")

    p_brief = sub.add_parser("brief", help="Drop a task brief into a room")
    p_brief.add_argument("room", help="Room name")
    p_brief.add_argument("task", nargs=argparse.REMAINDER, help="Task description")

    p_board = sub.add_parser("board", help="Show swarm status board")
    p_board.add_argument("--room", default=None, help="Filter to specific room")

    p_swarm = sub.add_parser(
        "setup-swarm", help="Create multiple rooms with agents (swarm setup)"
    )
    p_swarm.add_argument(
        "--rooms", required=True,
        help="Comma-separated name:purpose pairs, e.g. 'builders:build features,testers:test bugs'",
    )
    p_swarm.add_argument(
        "--agents", type=int, default=3, help="Agents per room (default: 3)"
    )

    p_resolve = sub.add_parser(
        "resolve", help="Resolve git merge conflicts using Claude + room history"
    )
    p_resolve.add_argument(
        "--room", default=None, help="Room name for history context (optional)"
    )
    p_resolve.add_argument(
        "--model", default="claude-sonnet-4-6", help="Claude model (default: claude-sonnet-4-6)"
    )

    p_context = sub.add_parser(
        "context",
        help="Show swarm context for current room (Summary Cascade v1)",
    )
    p_context.add_argument(
        "--room", default=None, help="Room name (default: auto-detect from membership)"
    )
    p_context.add_argument(
        "--limit", type=int, default=200, help="Max history messages to scan (default: 200)"
    )
    p_context.add_argument(
        "--quiet", action="store_true", help="Suppress header, output raw context block only"
    )
    p_context.add_argument(
        "--json", action="store_true", dest="json_output", help="Machine-readable JSON output"
    )
    p_context.add_argument(
        "--summarize",
        action="store_true",
        help="Use LLM to generate concise briefing (Summary Cascade v2)",
    )
    p_context.add_argument(
        "--model",
        default="claude-sonnet-4-6",
        help="Claude model for summarization (default: claude-sonnet-4-6)",
    )

    p_decision = sub.add_parser(
        "decision", help="Record an architectural decision into a room"
    )
    p_decision.add_argument("room", help="Room name")
    p_decision.add_argument(
        "decision", nargs=argparse.REMAINDER, help="Decision text"
    )

    sub.add_parser("begin", help="Open the Quorus hub (interactive TUI)")

    args = parser.parse_args()
    if not args.command:
        # Launch TUI by default (like `claude` or `gemini`)
        _cmd_begin(args)
        return

    commands = {
        "init": _cmd_init,
        "relay": _cmd_relay,
        "rooms": _cmd_rooms,
        "create": _cmd_create,
        "invite": _cmd_invite,
        "members": _cmd_members,
        "say": _cmd_say,
        "dm": _cmd_dm,
        "inbox": _cmd_inbox,
        "hook": _cmd_hook,
        "watch": _cmd_watch,
        "history": _cmd_history,
        "export": _cmd_export,
        "search": _cmd_search,
        "metrics": _cmd_metrics,
        "chat": _cmd_chat,
        "ps": _cmd_ps,
        "status": _cmd_status,
        "doctor": _cmd_doctor,
        "version": _cmd_version,
        "logs": _cmd_logs,
        "join": _cmd_join,
        "share": _cmd_share,
        "quickjoin": _cmd_quickjoin,
        "kick": _cmd_kick,
        "destroy": _cmd_destroy,
        "rename": _cmd_rename,
        "add-agent": _cmd_add_agent,
        "connect": _cmd_connect,
        "invite-link": _cmd_invite_link,
        "invite-token": _cmd_invite_token,
        "spawn": _cmd_spawn,
        "spawn-multiple": _cmd_spawn_multiple,
        "quickstart": _cmd_quickstart,
        "hackathon": _cmd_hackathon,
        "watch-daemon": _cmd_watch_daemon,
        "watch-context": _cmd_watch_context,
        "state": _cmd_room_state,
        "locks": _cmd_room_locks,
        "usage": _cmd_usage,
        "brief": _cmd_brief,
        "board": _cmd_board,
        "setup-swarm": _cmd_setup_swarm,
        "resolve": _cmd_resolve,
        "context": _cmd_context,
        "decision": _cmd_decision,
        "begin": _cmd_begin,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
