import asyncio
import json as json_module
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from mcp import types
from mcp.server.fastmcp import Context, FastMCP
from mcp.shared.message import SessionMessage

from murmur.config import load_config

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("mcp_tunnel.mcp")


_config = load_config()
CONFIG_FILE = Path(_config["config_file"])
RELAY_URL = _config["relay_url"]
RELAY_SECRET = _config["relay_secret"]
INSTANCE_NAME = _config["instance_name"]
ENABLE_BACKGROUND_POLLING = _config["enable_background_polling"]
POLL_MODE = _config["poll_mode"]
PUSH_NOTIFICATION_METHOD = _config["push_notification_method"]
PUSH_NOTIFICATION_CHANNEL = _config["push_notification_channel"]


def _validate_relay_url(value: str) -> str:
    """Validate relay_url eagerly so bad config fails at startup."""
    parsed = urlparse(value)
    if (
        not value
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise SystemExit(
            f"Invalid relay_url: {value!r}. Must be an http(s) URL with a hostname."
        )
    return value


_validate_relay_url(RELAY_URL)
if not RELAY_SECRET:
    raise SystemExit("relay_secret is empty. Set RELAY_SECRET env var or config file value.")

masked_secret = RELAY_SECRET[:4] + "***" if len(RELAY_SECRET) > 4 else "***"
logger.info(
    (
        "Config loaded: path=%s relay_url=%s instance=%s "
        "poll_mode=%s push_method=%s secret=%s"
    ),
    CONFIG_FILE,
    RELAY_URL,
    INSTANCE_NAME,
    POLL_MODE,
    PUSH_NOTIFICATION_METHOD or "disabled",
    masked_secret,
)

_http_client: httpx.AsyncClient | None = None
_pending_messages: list[dict[str, Any]] = []
_pending_lock = asyncio.Lock()
_active_session = None
_active_session_lock = asyncio.Lock()
_auto_poll_task: asyncio.Task | None = None
_auto_poll_stop: asyncio.Event | None = None
_heartbeat_task: asyncio.Task | None = None


def _get_http_client() -> httpx.AsyncClient:
    """Return the shared HTTP client, creating one if needed (e.g. in tests)."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient()
    return _http_client


def _reset_runtime_state():
    """Reset in-memory MCP runtime state between tests."""
    global _active_session, _http_client, _auto_poll_task, _auto_poll_stop, _heartbeat_task
    _pending_messages.clear()
    _active_session = None
    _http_client = None
    _auto_poll_task = None
    _auto_poll_stop = None
    _heartbeat_task = None


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {RELAY_SECRET}"}


def _relay_error_message(exc: Exception) -> str:
    """Convert a relay communication error to a user-facing string."""
    if isinstance(exc, httpx.ConnectError):
        logger.warning("Cannot reach relay at %s", RELAY_URL)
        return f"Error: Cannot reach relay server at {RELAY_URL}"
    if isinstance(exc, httpx.HTTPStatusError):
        logger.warning("Relay returned %d: %s", exc.response.status_code, exc.response.text)
        return f"Error: Relay returned {exc.response.status_code}: {exc.response.text}"
    return f"Error: {exc}"


def _format_message(msg: dict) -> str:
    ts = msg.get("timestamp", "")
    sender = msg.get("from_name", "unknown")
    content = msg.get("content", "")
    return f"[{ts}] {sender}: {content}"


async def _remember_session(context: Context | None) -> None:
    """Track the current client session so background notifications can use it."""
    if context is None:
        return

    try:
        session = context.session
    except ValueError:
        return

    global _active_session
    async with _active_session_lock:
        if _active_session is not session:
            logger.info("Updated active MCP session")
        _active_session = session


async def _append_pending_messages(messages: list[dict]) -> None:
    if not messages:
        return
    async with _pending_lock:
        _pending_messages.extend(messages)


async def _drain_pending_messages() -> list[dict]:
    async with _pending_lock:
        messages = list(_pending_messages)
        _pending_messages.clear()
        return messages


async def _send_push_notification(session, msg: dict) -> None:
    if not PUSH_NOTIFICATION_METHOD:
        return

    params = {"message": _format_message(msg)}
    if PUSH_NOTIFICATION_CHANNEL:
        params["channel"] = PUSH_NOTIFICATION_CHANNEL

    notification = types.JSONRPCNotification(
        jsonrpc="2.0",
        method=PUSH_NOTIFICATION_METHOD,
        params=params,
    )
    await session.send_message(
        SessionMessage(message=types.JSONRPCMessage(notification)),
    )


async def _notify_active_session(messages: list[dict]) -> None:
    global _active_session

    if not PUSH_NOTIFICATION_METHOD or not messages:
        return

    async with _active_session_lock:
        session = _active_session

    if session is None:
        return

    try:
        for msg in messages:
            await _send_push_notification(session, msg)
        logger.info("Sent %d push notification(s)", len(messages))
    except Exception:
        logger.warning("Failed to deliver push notification(s)", exc_info=True)
        async with _active_session_lock:
            if _active_session is session:
                _active_session = None


async def _fetch_relay_messages(wait: int) -> tuple[list[dict], str | None]:
    timeout = max(wait + 5, 10)
    try:
        client = _get_http_client()
        resp = await client.get(
            f"{RELAY_URL}/messages/{INSTANCE_NAME}",
            params={"wait": wait},
            headers=_auth_headers(),
            timeout=timeout,
        )
        resp.raise_for_status()
        messages = resp.json()
        logger.info("Received %d messages from relay", len(messages))
        return messages, None
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return [], _relay_error_message(e)


async def _background_poll(stop_event: asyncio.Event) -> None:
    """Long-poll the relay and optionally forward results via client notifications."""
    while not stop_event.is_set():
        async with _active_session_lock:
            has_session = _active_session is not None

        if not has_session:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            continue

        messages, error = await _fetch_relay_messages(wait=30)
        if error:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=2)
            except asyncio.TimeoutError:
                continue
            continue

        if not messages:
            continue

        await _append_pending_messages(messages)
        await _notify_active_session(messages)


async def _auto_poll_loop(stop_event: asyncio.Event, interval: int = 10) -> None:
    """Poll the relay every `interval` seconds and push new messages as notifications."""
    logger.info("Auto-poll started (interval=%ds)", interval)
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            break  # stop_event was set
        except asyncio.TimeoutError:
            pass  # interval elapsed, time to poll

        messages, error = await _fetch_relay_messages(wait=0)
        if error:
            logger.warning("Auto-poll fetch error: %s", error)
            continue

        if messages:
            await _append_pending_messages(messages)
            await _notify_active_session(messages)
            logger.info("Auto-poll delivered %d message(s)", len(messages))

    logger.info("Auto-poll stopped")


async def _heartbeat_loop(stop_event: asyncio.Event, interval: int = 30) -> None:
    """Send periodic heartbeats to the relay to report this agent is alive."""
    logger.info("Heartbeat loop started (interval=%ds)", interval)
    while not stop_event.is_set():
        try:
            client = _get_http_client()
            resp = await client.post(
                f"{RELAY_URL}/heartbeat",
                json={"instance_name": INSTANCE_NAME, "status": "active"},
                headers=_auth_headers(),
                timeout=10,
            )
            resp.raise_for_status()
            logger.debug("Heartbeat sent")
        except Exception:
            logger.warning("Heartbeat failed", exc_info=True)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            pass
    logger.info("Heartbeat loop stopped")


async def _process_sse_event(event_type: str, data: str) -> None:
    if event_type != "message":
        return
    try:
        msg = json_module.loads(data)
        await _append_pending_messages([msg])
        await _notify_active_session([msg])
    except (json_module.JSONDecodeError, KeyError):
        logger.warning("Failed to parse SSE event data: %s", data[:200])


async def _sse_listener(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            client = _get_http_client()
            url = f"{RELAY_URL}/stream/{INSTANCE_NAME}"
            params = {"token": RELAY_SECRET}
            async with client.stream("GET", url, params=params, timeout=None) as resp:
                if resp.status_code != 200:
                    logger.warning("SSE stream returned %d, retrying", resp.status_code)
                    await asyncio.sleep(2)
                    continue

                logger.info("SSE stream connected for %s", INSTANCE_NAME)
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
                        await _process_sse_event(event_type, event_data)
                        event_type = ""
                        event_data = ""

        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError):
            logger.warning("SSE connection lost, reconnecting in 2s")
        except Exception:
            logger.warning("SSE listener error, reconnecting in 2s", exc_info=True)

        if not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass


@asynccontextmanager
async def _mcp_lifespan(server: FastMCP):
    global _http_client, _heartbeat_task
    _http_client = httpx.AsyncClient()

    stop_event = asyncio.Event()
    poll_task = None

    if POLL_MODE == "sse":
        poll_task = asyncio.create_task(_sse_listener(stop_event))
        logger.info("SSE background listener enabled (poll_mode=sse)")
    elif POLL_MODE == "lazy":
        logger.info("Lazy poll mode — no background polling, agent checks manually")

    # Always start heartbeat so the relay knows this agent is alive
    _heartbeat_task = asyncio.create_task(_heartbeat_loop(stop_event))

    try:
        yield {"stop_event": stop_event}
    finally:
        stop_event.set()
        if poll_task is not None:
            poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await poll_task
        if _heartbeat_task is not None:
            _heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await _heartbeat_task
        if _auto_poll_stop is not None:
            _auto_poll_stop.set()
        if _auto_poll_task is not None:
            _auto_poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await _auto_poll_task
        if _http_client is not None:
            await _http_client.aclose()
        _reset_runtime_state()


mcp = FastMCP("mcp-tunnel", lifespan=_mcp_lifespan)


def _install_session_capture() -> None:
    """Capture the active session as soon as the client enumerates tools.

    This intentionally wraps FastMCP internals because the current SDK does not
    expose a public session-connected hook for stdio servers. Re-check this on
    SDK upgrades and replace it with an official hook when one exists.
    """
    original_handler = mcp._mcp_server.request_handlers[types.ListToolsRequest]

    async def wrapped(req: types.ListToolsRequest):
        await _remember_session(mcp.get_context())
        return await original_handler(req)

    mcp._mcp_server.request_handlers[types.ListToolsRequest] = wrapped


_install_session_capture()


def _declare_channel_capability() -> None:
    """Declare the experimental claude/channel capability.

    This tells Claude Code that our server can push notifications via
    the `notifications/claude/channel` method, enabling instant message
    delivery without polling when launched with --channels.
    """
    original_create = mcp._mcp_server.create_initialization_options

    def patched_create(**kwargs):
        opts = original_create(**kwargs)
        if opts.capabilities.experimental is None:
            opts.capabilities.experimental = {}
        opts.capabilities.experimental["claude/channel"] = {
            "channel": PUSH_NOTIFICATION_CHANNEL,
        }
        return opts

    mcp._mcp_server.create_initialization_options = patched_create


if POLL_MODE == "sse":
    _declare_channel_capability()


async def _send_message(to: str, content: str, context: Context | None = None) -> str:
    """Internal: send a message via the relay."""
    await _remember_session(context)
    try:
        client = _get_http_client()
        resp = await client.post(
            f"{RELAY_URL}/messages",
            json={"from_name": INSTANCE_NAME, "to": to, "content": content},
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info("Sent message to %s (id: %s)", to, data["id"])
        return f"Message sent (id: {data['id']})"
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return _relay_error_message(e)


async def _check_messages(context: Context | None = None) -> str:
    """Internal: fetch unread messages from the local buffer or the relay."""
    await _remember_session(context)

    messages = await _drain_pending_messages()
    if not messages:
        # lazy/sse = no blocking wait; poll = short wait for efficiency
        wait = 0 if POLL_MODE in ("lazy", "sse") else 30
        messages, error = await _fetch_relay_messages(wait=wait)
        if error:
            return error

    if not messages:
        return "No new messages."

    return "\n".join(_format_message(msg) for msg in messages)


async def _list_participants(context: Context | None = None) -> str:
    """Internal: list all known participants."""
    await _remember_session(context)
    try:
        client = _get_http_client()
        resp = await client.get(
            f"{RELAY_URL}/participants",
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        participants = resp.json()
        logger.info("Listed %d participants", len(participants))
        if not participants:
            return "No participants yet."
        return "Participants: " + ", ".join(participants)
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return _relay_error_message(e)


async def _send_room_message(
    room_id: str, content: str, message_type: str = "chat", context: Context | None = None
) -> str:
    await _remember_session(context)
    try:
        client = _get_http_client()
        resp = await client.post(
            f"{RELAY_URL}/rooms/{room_id}/messages",
            json={"from_name": INSTANCE_NAME, "content": content, "message_type": message_type},
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info("Sent room message to %s (id: %s)", room_id, data["id"])
        return f"Room message sent (id: {data['id']})"
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return _relay_error_message(e)


async def _join_room(room_id: str, context: Context | None = None) -> str:
    await _remember_session(context)
    try:
        client = _get_http_client()
        resp = await client.post(
            f"{RELAY_URL}/rooms/{room_id}/join",
            json={"participant": INSTANCE_NAME},
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        logger.info("Joined room %s", room_id)
        return f"Joined room {room_id}"
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return _relay_error_message(e)


async def _list_rooms(context: Context | None = None) -> str:
    await _remember_session(context)
    try:
        client = _get_http_client()
        resp = await client.get(f"{RELAY_URL}/rooms", headers=_auth_headers())
        resp.raise_for_status()
        rooms_list = resp.json()
        logger.info("Listed %d rooms", len(rooms_list))
        if not rooms_list:
            return "No rooms yet."
        lines = []
        for r in rooms_list:
            members = ", ".join(r["members"])
            lines.append(f"  {r['name']} (id: {r['id']}) — members: {members}")
        return "Rooms:\n" + "\n".join(lines)
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return _relay_error_message(e)


async def _start_auto_poll(interval: int = 10, context: Context | None = None) -> str:
    """Internal: start the auto-poll background loop."""
    global _auto_poll_task, _auto_poll_stop
    await _remember_session(context)

    if _auto_poll_task is not None and not _auto_poll_task.done():
        return f"Auto-poll already running (interval={interval}s). Call stop_auto_poll first."

    interval = max(2, min(interval, 300))
    _auto_poll_stop = asyncio.Event()
    _auto_poll_task = asyncio.create_task(_auto_poll_loop(_auto_poll_stop, interval))
    return f"Auto-poll started (every {interval}s). New messages arrive as notifications."


async def _stop_auto_poll(context: Context | None = None) -> str:
    """Internal: stop the auto-poll background loop."""
    global _auto_poll_task, _auto_poll_stop
    await _remember_session(context)

    if _auto_poll_task is None or _auto_poll_task.done():
        return "Auto-poll is not running."

    _auto_poll_stop.set()
    _auto_poll_task.cancel()
    with suppress(asyncio.CancelledError):
        await _auto_poll_task
    _auto_poll_task = None
    _auto_poll_stop = None
    return "Auto-poll stopped."


@mcp.tool()
async def start_auto_poll(interval: int = 10, context: Context = None) -> str:
    """Start auto-polling for new messages every `interval` seconds.

    Messages are delivered as push notifications without needing to call
    check_messages manually. Call stop_auto_poll to stop.

    Args:
        interval: Seconds between polls (default 10, min 2, max 300)
    """
    return await _start_auto_poll(interval, context)


@mcp.tool()
async def stop_auto_poll(context: Context = None) -> str:
    """Stop the auto-poll background loop started by start_auto_poll."""
    return await _stop_auto_poll(context)


@mcp.tool()
async def send_message(to: str, content: str, context: Context) -> str:
    """Send a message to another connected MCP client.

    Args:
        to: The name of the recipient instance (e.g., "bob")
        content: The message content to send
    """
    return await _send_message(to, content, context)


@mcp.tool()
async def check_messages(context: Context) -> str:
    """Check for new messages sent to this instance."""
    return await _check_messages(context)


@mcp.tool()
async def list_participants(context: Context) -> str:
    """List all known participants who have sent messages through the relay."""
    return await _list_participants(context)


@mcp.tool()
async def send_room_message(
    room_id: str, content: str, message_type: str = "chat", context: Context = None
) -> str:
    """Send a message to a room. All room members will receive it.

    Args:
        room_id: The room name or ID (e.g., "murmur-dev" or a UUID)
        content: The message content
        message_type: Optional type: chat, claim, status, request, alert, sync
    """
    return await _send_room_message(room_id, content, message_type, context)


@mcp.tool()
async def join_room(room_id: str, context: Context = None) -> str:
    """Join a room to start receiving messages from it.

    Args:
        room_id: The room name or ID (e.g., "murmur-dev" or a UUID)
    """
    return await _join_room(room_id, context)


@mcp.tool()
async def list_rooms(context: Context = None) -> str:
    """List all available rooms with their members."""
    return await _list_rooms(context)


if __name__ == "__main__":
    mcp.run(transport="stdio")
