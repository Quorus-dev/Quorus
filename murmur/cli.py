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

from murmur.config import load_config

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
        console.print("[bold green]Murmur Relay Status[/bold green]")
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
    console.print("[dim]Type a message and press Enter to send. Ctrl+C to quit.[/dim]")
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
        console.print(
            f"[bold]History for {room_name}[/bold] "
            f"({len(messages)} messages)\n"
        )
        for msg in messages:
            console.print(_format_chat_msg(msg))
    except httpx.ConnectError:
        console.print(f"[red]Cannot connect to relay at {RELAY_URL}[/red]")
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
            except (ValueError, KeyError):
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
            except (ValueError, KeyError):
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
        console.print(f"[red]Cannot connect to relay at {RELAY_URL}[/red]")
    except httpx.HTTPStatusError as e:
        console.print(f"[red]Error: {e.response.status_code}[/red]")
    finally:
        await client.aclose()


def _cmd_ps(args):
    asyncio.run(_ps())


def _cmd_history(args):
    asyncio.run(_history(args.room, args.limit))


def _cmd_watch(args):
    asyncio.run(_watch(args.room))


async def _watch_daemon(name: str) -> None:
    """SSE listener that writes new messages to a file for agent pickup."""
    inbox_path = Path(f"/tmp/murmur-{name}-inbox.txt")
    console.print(f"[bold]Watch daemon for: {name}[/bold]")
    console.print(f"  Inbox: {inbox_path}")
    console.print("  Ctrl+C to stop\n")

    client = httpx.AsyncClient()
    try:
        url = f"{RELAY_URL}/stream/{name}"
        async with client.stream(
            "GET", url, params={"token": RELAY_SECRET}, timeout=None,
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


def _cmd_status(args):
    asyncio.run(_status())


def _cmd_init(args):
    """One-command setup: write config, register MCP server with Claude Code."""
    name = args.name
    relay_url = args.relay_url
    secret = args.secret
    repo_dir = Path(__file__).resolve().parent.parent
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
            "run", "--directory", str(repo_dir),
            "python", str(murmur_dir / "mcp.py"),
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


def _cmd_invite_link(args):
    """Generate a one-liner join command to share with others."""
    room = args.room
    console.print(f"[bold]Share this command to invite someone to '{room}':[/bold]")
    console.print("")
    console.print(
        f"  murmur join --name YOUR_NAME "
        f"--relay {RELAY_URL} --secret {RELAY_SECRET} --room {room}"
    )
    console.print("")
    console.print("[dim]Replace YOUR_NAME with the agent/user's name.[/dim]")


def _cmd_join(args):
    """One-liner to join a room: writes config, registers MCP, joins the room."""
    name = args.name
    relay_url = args.relay_url
    secret = args.secret
    room = args.room
    repo_dir = Path(__file__).resolve().parent.parent
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
            "run", "--directory", str(repo_dir),
            "python", str(murmur_dir / "mcp.py"),
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
                headers={"Authorization": f"Bearer {secret}"},
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
                headers={"Authorization": f"Bearer {secret}"},
            )
            resp.raise_for_status()
            console.print(f"[green]Joined room '{room}' as '{name}'[/green]")
        except httpx.ConnectError:
            console.print(f"[red]Cannot connect to relay at {relay_url}[/red]")
        finally:
            await client.aclose()

    asyncio.run(_do_join())

    console.print("")
    console.print(f"[bold]Setup complete for: {name}[/bold]")
    console.print(f"  Relay: {relay_url}")
    console.print(f"  Room: {room}")
    console.print("")
    console.print("[yellow]Restart Claude Code to pick up the MCP server.[/yellow]")
    console.print("")
    console.print("Start chatting:")
    console.print(f"  murmur chat {room}")


def _spawn_agent(room: str, name: str, relay_url: str, secret: str):
    """Create agent workspace and launch Claude Code in a new terminal tab."""
    agents_dir = Path.home() / "murmur-agents"
    workspace = agents_dir / name
    murmur_dir = Path(__file__).resolve().parent

    if workspace.exists():
        console.print(f"[yellow]Workspace {workspace} already exists[/yellow]")
    else:
        workspace.mkdir(parents=True)

    # .mcp.json — MCP server config for this agent
    mcp_config = {
        "mcpServers": {
            "murmur": {
                "type": "stdio",
                "command": "uv",
                "args": [
                    "run", "--directory", str(murmur_dir.parent),
                    "python", str(murmur_dir / "mcp.py"),
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
                "mcp__murmur__*",
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

    # Symlink to murmur source for imports
    murmur_link = workspace / "murmur"
    if not murmur_link.exists():
        murmur_link.symlink_to(murmur_dir.parent)

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

    console.print("[bold green]Murmur Quickstart[/bold green]")
    console.print("Starting relay, creating room, spawning agents...\n")

    # 1. Start relay in background
    env = os.environ.copy()
    env["RELAY_SECRET"] = secret
    env["PORT"] = str(port)
    relay_proc = subprocess.Popen(
        [
            "uv", "run", "--directory", str(repo_dir),
            "python", "-m", "uvicorn", "murmur.relay:app",
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
    config_dir = Path.home() / "mcp-tunnel"
    config_dir.mkdir(exist_ok=True)
    config = {
        "relay_url": relay_url,
        "relay_secret": secret,
        "instance_name": "human",
        "enable_background_polling": True,
        "push_notification_method": "notifications/claude/channel",
        "push_notification_channel": "mcp-tunnel",
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
    console.print(f"[dim]Watch the room: murmur chat {room_name}[/dim]\n")

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

    headers = {"Authorization": f"Bearer {RELAY_SECRET}"}

    console.print("[bold green]Murmur Hackathon Setup[/bold green]\n")

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

    console.print(f"\n[bold green]Hackathon ready![/bold green]")
    console.print(f"  Rooms: {args.room1}, {args.room2}")
    console.print(f"  Agents: {agents_per_room} per room ({agents_per_room * 2} total)")
    console.print(f"  You: {INSTANCE_NAME} (in both rooms)")
    console.print(f"\n  Watch room 1: murmur watch {args.room1}")
    console.print(f"  Watch room 2: murmur watch {args.room2}")
    console.print(f"  Chat in room: murmur chat {args.room1}")


def _cmd_relay(args):
    """Start the relay server."""
    repo_dir = Path(__file__).resolve().parent.parent
    port = args.port
    config_path = Path.home() / "mcp-tunnel" / "config.json"

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
        console.print("Run: murmur init <name> --secret <your-secret>")
        sys.exit(1)

    console.print(f"[bold]Starting Murmur relay on port {port}[/bold]")
    console.print("  Press Ctrl+C to stop")
    console.print("")

    env = os.environ.copy()
    env["RELAY_SECRET"] = secret
    env["PORT"] = str(port)

    try:
        subprocess.run(
            [
                "uv", "run", "--directory", str(repo_dir),
                "python", "-m", "uvicorn", "murmur.relay:app",
                "--host", "0.0.0.0", "--port", str(port),
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
    p_init.add_argument("--secret", required=True, help="Shared secret")

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

    p_history = sub.add_parser("history", help="Show room message history")
    p_history.add_argument("room", help="Room name")
    p_history.add_argument(
        "--limit", type=int, default=50, help="Max messages (default 50)"
    )

    sub.add_parser("ps", help="Show agent presence (online/offline)")
    sub.add_parser("status", help="Show relay health and stats")

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

    p_hack = sub.add_parser(
        "hackathon", help="Set up hackathon: 2 rooms + agents with missions"
    )
    p_hack.add_argument("--room1", default="yc-hack", help="First room (default: yc-hack)")
    p_hack.add_argument("--room2", default="openai-hack", help="Second room (default: openai-hack)")
    p_hack.add_argument("--agents", type=int, default=3, help="Agents per room (default: 3)")
    p_hack.add_argument("--mission1", default=None, help="Mission for room 1")
    p_hack.add_argument("--mission2", default=None, help="Mission for room 2")

    p_join = sub.add_parser("join", help="One-liner to join a room")
    p_join.add_argument("--name", required=True, help="Your participant name")
    p_join.add_argument("--relay", dest="relay_url", required=True, help="Relay URL")
    p_join.add_argument("--secret", required=True, help="Shared secret")
    p_join.add_argument("--room", required=True, help="Room to join")

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
        "history": _cmd_history,
        "chat": _cmd_chat,
        "ps": _cmd_ps,
        "status": _cmd_status,
        "join": _cmd_join,
        "invite-link": _cmd_invite_link,
        "spawn": _cmd_spawn,
        "spawn-multiple": _cmd_spawn_multiple,
        "quickstart": _cmd_quickstart,
        "hackathon": _cmd_hackathon,
        "watch-daemon": _cmd_watch_daemon,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
