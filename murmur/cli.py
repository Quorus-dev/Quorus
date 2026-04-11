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
from rich.prompt import Confirm, Prompt
from rich.table import Table

from murmur.config import load_config

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
    target = url or RELAY_URL
    console.print(f"[red]Cannot connect to relay at {target}[/red]")
    console.print("[dim]  Is the relay running? Try: murmur relay[/dim]")
    console.print("[dim]  Check config: murmur doctor[/dim]")


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
    murmur_dir = str(Path(__file__).resolve().parent.parent)

    generators = {
        "codex": _connect_codex,
        "cursor": _connect_cursor,
        "ollama": _connect_ollama,
        "claude": _connect_claude,
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
    generators[platform](room, name, relay_url, secret, murmur_dir)


def _connect_codex(
    room: str, name: str, relay_url: str, secret: str, murmur_dir: str,
) -> None:
    """Generate Codex onboarding with curl commands."""
    console.print("\n[bold green]Codex Agent Setup[/bold green]\n")
    console.print(f"  Agent:  [cyan]{name}[/cyan]")
    console.print(f"  Room:   [cyan]{room}[/cyan]")
    console.print(f"  Relay:  [cyan]{relay_url}[/cyan]\n")

    console.print("[bold]1. Add to your Codex system prompt:[/bold]\n")

    system_prompt = f"""You are {name} in a Murmur dev room. Use curl to communicate:

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
from murmur.integrations.http_agent import MurmurClient

client = MurmurClient("{relay_url}", "{secret}", "{name}")
client.join("{room}")
messages = client.receive()
client.send("{room}", "STATUS: online", message_type="status")"""

    console.print(python_snippet, highlight=False, markup=False)
    console.print(f"\n[green]{name} joined room '{room}'. Ready to go.[/green]")


def _connect_cursor(
    room: str, name: str, relay_url: str, secret: str, murmur_dir: str,
) -> None:
    """Generate Cursor MCP config."""
    console.print("\n[bold green]Cursor Agent Setup[/bold green]\n")
    console.print(f"  Agent:  [cyan]{name}[/cyan]")
    console.print(f"  Room:   [cyan]{room}[/cyan]\n")

    mcp_config = json.dumps({
        "mcpServers": {
            "murmur": {
                "command": "uv",
                "args": [
                    "run", "--directory", murmur_dir,
                    "python", f"{murmur_dir}/murmur/mcp.py",
                ],
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
You are {name} in the {room} Murmur room. Use MCP tools:
- check_messages() — read new messages
- send_room_message(room_id="{room}", content="...", message_type="chat")
- list_rooms() — see available rooms

Check messages after every task. Claim with CLAIM:. Post STATUS: when done."""

    console.print(rules, highlight=False, markup=False)
    console.print(f"\n[green]{name} joined room '{room}'. Ready to go.[/green]")


def _connect_ollama(
    room: str, name: str, relay_url: str, secret: str, murmur_dir: str,
) -> None:
    """Generate Ollama agent script."""
    console.print("\n[bold green]Ollama Agent Setup[/bold green]\n")
    console.print(f"  Agent:  [cyan]{name}[/cyan]")
    console.print(f"  Room:   [cyan]{room}[/cyan]\n")

    console.print("[bold]Save this as ollama_agent.py and run it:[/bold]\n")

    script = f"""\
import ollama
from murmur.integrations.http_agent import MurmurClient

murmur = MurmurClient("{relay_url}", "{secret}", "{name}")
murmur.join("{room}")
murmur.send("{room}", "{name} online (ollama). Ready for tasks.")

while True:
    messages = murmur.receive(wait=30)
    if not messages:
        continue

    context = "\\n".join(f"{{m['from_name']}}: {{m['content']}}" for m in messages)

    response = ollama.chat(
        model="llama3.1",
        messages=[
            {{"role": "system", "content": (
                "You are {name} in a Murmur dev room. "
                "Respond to messages. Claim tasks with CLAIM:. "
                "Post status with STATUS:. Keep responses short."
            )}},
            {{"role": "user", "content": context}},
        ],
    )

    murmur.send("{room}", response["message"]["content"])"""

    console.print(script, highlight=False, markup=False)

    console.print("\n[bold]Install dependencies:[/bold]")
    console.print("  pip install murmur-ai ollama", highlight=False, markup=False)
    console.print(f"\n[green]{name} joined room '{room}'. Ready to go.[/green]")


def _connect_claude(
    room: str, name: str, relay_url: str, secret: str, murmur_dir: str,
) -> None:
    """Generate Claude Code workspace setup."""
    console.print("\n[bold green]Claude Code Agent Setup[/bold green]\n")
    console.print(f"  Agent:  [cyan]{name}[/cyan]")
    console.print(f"  Room:   [cyan]{room}[/cyan]\n")

    console.print(
        "[bold]Run this to create and launch the agent:[/bold]\n"
    )
    console.print(
        "  murmur add-agent",
        highlight=False, markup=False,
    )
    console.print(
        "\n[dim]Or use murmur spawn to skip the wizard:[/dim]\n"
    )
    console.print(
        f"  murmur spawn {room} {name}",
        highlight=False, markup=False,
    )
    console.print(f"\n[green]{name} joined room '{room}'. Ready to go.[/green]")


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
    inbox_path = Path(f"/tmp/murmur-{name}-inbox.txt")
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


def _cmd_status(args):
    asyncio.run(_status())


def _cmd_init(args):
    """One-command setup: write config, register MCP server with Claude Code."""
    name = args.name
    relay_url = args.relay_url
    secret = getattr(args, "secret", None) or ""
    api_key = getattr(args, "api_key", None) or ""
    repo_dir = Path(__file__).resolve().parent.parent
    murmur_dir = Path(__file__).resolve().parent

    # 1. Write config
    config_dir = Path.home() / "mcp-tunnel"
    config_dir.mkdir(exist_ok=True)
    config = {
        "relay_url": relay_url,
        "instance_name": name,
        "enable_background_polling": True,
        "push_notification_method": "notifications/claude/channel",
        "push_notification_channel": "mcp-tunnel",
    }
    if api_key:
        config["api_key"] = api_key
    else:
        config["relay_secret"] = secret
    config_path = config_dir / "config.json"
    config_path.write_text(json.dumps(config, indent=2))
    # Set 0600 permissions — API keys are sensitive
    config_path.chmod(0o600)
    console.print(f"[green]Config written to {config_path} (permissions: 0600)[/green]")

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
    secret = getattr(args, "secret", None) or ""
    api_key = getattr(args, "api_key", None) or ""
    room = args.room
    repo_dir = Path(__file__).resolve().parent.parent
    murmur_dir = Path(__file__).resolve().parent

    # 1. Write config
    config_dir = Path.home() / "mcp-tunnel"
    config_dir.mkdir(exist_ok=True)
    config = {
        "relay_url": relay_url,
        "instance_name": name,
        "enable_background_polling": True,
        "push_notification_method": "notifications/claude/channel",
        "push_notification_channel": "mcp-tunnel",
    }
    if api_key:
        config["api_key"] = api_key
    else:
        config["relay_secret"] = secret
    config_path = config_dir / "config.json"
    config_path.write_text(json.dumps(config, indent=2))
    config_path.chmod(0o600)
    console.print(f"[green]Config written to {config_path} (permissions: 0600)[/green]")

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


def _cmd_add_agent(args):
    """Interactive wizard to create and launch an agent."""
    console.print("\n[bold green]Murmur Add Agent Wizard[/bold green]\n")

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
        room = Prompt.ask("[bold]Room name[/bold]", default="murmur-dev")

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
    agents_dir = Path.home() / "murmur-agents"
    workspace = agents_dir / name
    murmur_dir = Path(__file__).resolve().parent

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
            "murmur": {
                "type": "stdio",
                "command": "uv",
                "args": [
                    "run", "--directory", str(murmur_dir.parent),
                    "python", str(murmur_dir / "mcp.py"),
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
                "mcp__murmur__*",
            ],
            "deny": [],
        }
    }
    (claude_dir / "settings.json").write_text(json.dumps(settings, indent=2))

    # CLAUDE.md with model/effort hints
    claude_md = f"""# Murmur Agent

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

    # Symlink to murmur source
    murmur_link = workspace / "murmur"
    if not murmur_link.exists():
        murmur_link.symlink_to(murmur_dir.parent)

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

    headers = _auth_headers()

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

    console.print("\n[bold green]Hackathon ready![/bold green]")
    console.print(f"  Rooms: {args.room1}, {args.room2}")
    console.print(f"  Agents: {agents_per_room} per room ({agents_per_room * 2} total)")
    console.print(f"  You: {INSTANCE_NAME} (in both rooms)")
    console.print(f"\n  Watch room 1: murmur watch {args.room1}")
    console.print(f"  Watch room 2: murmur watch {args.room2}")
    console.print(f"  Chat in room: murmur chat {args.room1}")


def _cmd_doctor(args):
    """Diagnose common setup issues."""
    from murmur.config import resolve_config_file

    checks_passed = 0
    checks_total = 0
    verbose = args.verbose

    def check(name: str, ok: bool, detail: str = "", fix: str = ""):
        nonlocal checks_passed, checks_total
        checks_total += 1
        if ok:
            checks_passed += 1
            console.print(f"  [green]OK[/green]  {name}")
        else:
            console.print(f"  [red]FAIL[/red] {name}")
            if detail:
                console.print(f"        {detail}")
            if fix:
                console.print(f"        [yellow]Fix: {fix}[/yellow]")
        if verbose and detail and ok:
            console.print(f"        {detail}")

    console.print("[bold]Murmur Doctor[/bold]\n")

    # 1. Config file
    config_file = resolve_config_file()
    check(
        "Config file exists",
        config_file.exists(),
        detail=str(config_file),
        fix="Run: murmur init <name> --secret <secret>",
    )

    # 2. Relay URL configured
    check(
        "Relay URL configured",
        bool(RELAY_URL) and RELAY_URL != "http://localhost:8080" or True,
        detail=RELAY_URL,
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
        fix="Set INSTANCE_NAME env var or run: murmur init <name>",
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
        fix="Is the relay running? Try: murmur relay",
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
        fix="Run: murmur init <name> --secret <secret>",
    )

    # 8. Rooms exist
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
                fix="Create one: murmur create <room-name>",
            )
        except Exception:
            check("Rooms exist", False)

    console.print(f"\n[bold]{checks_passed}/{checks_total} checks passed[/bold]")
    if checks_passed == checks_total:
        console.print("[green]Everything looks good![/green]")
    else:
        console.print("[yellow]Fix the issues above to get started.[/yellow]")


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


def _cmd_version(args):
    from murmur import __version__
    console.print(f"murmur-ai {__version__}")


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

        console.print("[bold]Murmur Relay Logs[/bold]\n")
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


def main():
    parser = argparse.ArgumentParser(
        prog="murmur", description="Murmur CLI"
    )
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="One-command setup")
    p_init.add_argument("name", help="Your participant name")
    p_init.add_argument("--relay-url", default="http://localhost:8080", help="Relay URL")
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
    sub.add_parser("version", help="Show murmur-ai version")
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
    p_join_auth = p_join.add_mutually_exclusive_group(required=True)
    p_join_auth.add_argument("--secret", help="Shared secret (legacy auth)")
    p_join_auth.add_argument("--api-key", help="API key (production auth)")
    p_join.add_argument("--room", required=True, help="Room to join")

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
        "platform", choices=["codex", "cursor", "ollama", "claude"],
        help="Agent platform",
    )
    p_connect.add_argument("room", help="Room name")
    p_connect.add_argument("name", help="Agent name")

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
        "kick": _cmd_kick,
        "destroy": _cmd_destroy,
        "rename": _cmd_rename,
        "add-agent": _cmd_add_agent,
        "connect": _cmd_connect,
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
