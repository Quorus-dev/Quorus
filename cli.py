"""Murmur CLI — human interface for rooms and messaging."""

import argparse
import asyncio
import json
import os
import subprocess
import sys
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
    console.print(f"Members: {', '.join(room['members'])}")
    console.print("---")

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
                                msg_type = msg.get(
                                    "message_type", "chat"
                                )
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
                                console.print(
                                    f"[dim]{ts}[/dim] "
                                    f"[bold]{sender}[/bold] "
                                    f"[{type_color}][{msg_type}]"
                                    f"[/{type_color}] "
                                    f"{content}"
                                )
                        except json.JSONDecodeError:
                            pass
                    event_type = ""
                    event_data = ""
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped watching.[/dim]")
    finally:
        await client.aclose()


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
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
