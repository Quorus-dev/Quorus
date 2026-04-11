"""Murmur CLI — human interface for rooms and messaging."""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import httpx
from rich.console import Console
from rich.table import Table

from tunnel_config import load_config

console = Console()
_config = load_config()
RELAY_URL = _config["relay_url"]
RELAY_SECRET = _config["relay_secret"]
INSTANCE_NAME = _config["instance_name"]


def _get_client() -> httpx.AsyncClient:
    return httpx.AsyncClient()


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {RELAY_SECRET}"}


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
        async with client.stream(
            "GET", url, params={"token": RELAY_SECRET}, timeout=None
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
        console.print(f"[bold green]Murmur Relay Status[/bold green]")
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
        console.print(f"[red]Cannot connect to relay at {RELAY_URL}[/red]")
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
    console.print(f"[bold green]Murmur Chat — {room_name}[/bold green]")
    console.print(f"[dim]Members: {', '.join(room['members'])}[/dim]")
    console.print(f"[dim]You are: {INSTANCE_NAME}[/dim]")
    console.print(f"[dim]Type a message and press Enter to send. Ctrl+C to quit.[/dim]")
    console.print("─" * 60)

    stop_event = asyncio.Event()
    chat_console = Console()

    async def _stream_messages():
        """Background task: stream SSE messages and print them."""
        client = httpx.AsyncClient()
        try:
            url = f"{RELAY_URL}/stream/{INSTANCE_NAME}"
            async with client.stream(
                "GET", url, params={"token": RELAY_SECRET}, timeout=None,
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
    rooms = asyncio.run(_list_rooms())
    if not rooms:
        console.print("[dim]No rooms yet.[/dim]")
        return
    table = Table(title="Rooms")
    table.add_column("Name", style="bold")
    table.add_column("Members")
    table.add_column("ID", style="dim")
    for r in rooms:
        table.add_row(r["name"], ", ".join(r["members"]), r["id"])
    console.print(table)


def _cmd_create(args):
    room = asyncio.run(_create_room(args.name))
    console.print(
        f"[green]Created room '{room['name']}' (id: {room['id']})[/green]"
    )


def _cmd_invite(args):
    asyncio.run(_invite(args.room, args.participants))


def _cmd_members(args):
    members = asyncio.run(_members(args.room))
    if not members:
        console.print(
            f"[dim]Room '{args.room}' not found or empty.[/dim]"
        )
        return
    for m in members:
        console.print(f"  {m}")


def _cmd_say(args):
    asyncio.run(_say(args.room, args.message))
    console.print(f"[green]Sent to {args.room}[/green]")


def _cmd_dm(args):
    asyncio.run(_dm(args.to, args.message))
    console.print(f"[green]DM sent to {args.to}[/green]")


def _cmd_watch(args):
    asyncio.run(_watch(args.room))


def _cmd_status(args):
    asyncio.run(_status())


def _cmd_init(args):
    """One-command setup: write config, register MCP server with Claude Code."""
    name = args.name
    relay_url = args.relay_url
    secret = args.secret
    murmur_dir = Path(__file__).resolve().parent

    # 1. Write config
    config_dir = Path.home() / "mcp-tunnel"
    config_dir.mkdir(exist_ok=True)
    config = {
        "relay_url": relay_url,
        "relay_secret": secret,
        "instance_name": name,
        "enable_background_polling": True,
        "push_notification_method": "notifications/claude/channel",
        "push_notification_channel": "mcp-tunnel",
    }
    config_path = config_dir / "config.json"
    config_path.write_text(json.dumps(config, indent=2))
    console.print(f"[green]Config written to {config_path}[/green]")

    # 2. Register MCP server with Claude Code
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

    claude_config["mcpServers"]["murmur"] = {
        "type": "stdio",
        "command": "uv",
        "args": [
            "run", "--directory", str(murmur_dir),
            "python", str(murmur_dir / "mcp_server.py"),
        ],
        "env": {},
    }
    claude_config_path.write_text(json.dumps(claude_config, indent=2))
    console.print("[green]MCP server registered in ~/.claude.json[/green]")

    # 3. Summary
    console.print("")
    console.print(f"[bold]Murmur initialized for: {name}[/bold]")
    console.print(f"  Relay: {relay_url}")
    console.print(f"  Config: {config_path}")
    console.print("  MCP server: registered as 'murmur'")
    console.print("")
    console.print("[yellow]Restart Claude Code to pick up the MCP server.[/yellow]")
    console.print("")
    console.print("Next steps:")
    console.print("  murmur relay                    # Start the relay server")
    console.print("  murmur create <room-name>       # Create a room")
    console.print(f"  murmur invite <room> {name}     # Join yourself")


def _cmd_relay(args):
    """Start the relay server."""
    murmur_dir = Path(__file__).resolve().parent
    port = args.port
    config_path = Path.home() / "mcp-tunnel" / "config.json"

    # Read secret from config if it exists
    secret = "murmur-hack"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text())
            secret = cfg.get("relay_secret", secret)
        except (json.JSONDecodeError, ValueError):
            pass

    console.print(f"[bold]Starting Murmur relay on port {port}[/bold]")
    console.print("  Press Ctrl+C to stop")
    console.print("")

    env = os.environ.copy()
    env["RELAY_SECRET"] = secret
    env["PORT"] = str(port)

    try:
        subprocess.run(
            [
                "uv", "run", "--directory", str(murmur_dir),
                "python", str(murmur_dir / "relay_server.py"),
            ],
            env=env,
        )
    except KeyboardInterrupt:
        console.print("\n[dim]Relay stopped.[/dim]")


def main():
    parser = argparse.ArgumentParser(
        prog="murmur", description="Murmur CLI"
    )
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="One-command setup")
    p_init.add_argument("name", help="Your participant name")
    p_init.add_argument("--relay-url", default="http://localhost:8080", help="Relay URL")
    p_init.add_argument("--secret", default="murmur-hack", help="Shared secret")

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

    p_watch = sub.add_parser("watch", help="Watch a room live")
    p_watch.add_argument("room", help="Room name")

    p_chat = sub.add_parser(
        "chat", help="Interactive chat in a room"
    )
    p_chat.add_argument("room", help="Room name")

    sub.add_parser("status", help="Show relay health and stats")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "init": _cmd_init,
        "relay": _cmd_relay,
        "rooms": _cmd_rooms,
        "create": _cmd_create,
        "invite": _cmd_invite,
        "members": _cmd_members,
        "say": _cmd_say,
        "dm": _cmd_dm,
        "watch": _cmd_watch,
        "chat": _cmd_chat,
        "status": _cmd_status,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
