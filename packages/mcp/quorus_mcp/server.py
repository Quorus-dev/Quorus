import asyncio
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

from quorus.config import load_config
from quorus_mcp.sse import SSEListener, process_sse_event_data

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO")),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("mcp_tunnel.mcp")

_config = load_config()
CONFIG_FILE = Path(_config["config_file"])
RELAY_URL = _config["relay_url"]
RELAY_SECRET = _config["relay_secret"]
API_KEY = _config["api_key"]
INSTANCE_NAME = _config["instance_name"]
SSE_ENABLED = os.environ.get("SSE_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
PUSH_NOTIFICATION_METHOD = _config["push_notification_method"]
PUSH_NOTIFICATION_CHANNEL = _config["push_notification_channel"]

_cached_jwt: str | None = None
_jwt_lock = asyncio.Lock()
_http_client: httpx.AsyncClient | None = None
_pending_messages: list[dict[str, Any]] = []
_pending_lock = asyncio.Lock()
_active_session = None
_active_session_lock = asyncio.Lock()
_heartbeat_task: asyncio.Task | None = None

def _validate_relay_url(value: str) -> str:
    p = urlparse(value)
    if not value or p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise SystemExit(f"Invalid relay_url: {value!r}. Must be an http(s) URL with a hostname.")
    return value

_validate_relay_url(RELAY_URL)
if not RELAY_SECRET and not API_KEY:
    raise SystemExit("Neither relay_secret nor api_key is set. Set RELAY_SECRET or API_KEY env var or config file value.")
logger.info("Config loaded: relay_url=%s instance=%s sse_enabled=%s", RELAY_URL, INSTANCE_NAME, SSE_ENABLED)

def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient()
    return _http_client

def _reset_runtime_state() -> None:
    global _active_session, _http_client, _heartbeat_task
    _pending_messages.clear()
    _active_session = None
    _http_client = None
    _heartbeat_task = None

async def _get_active_session():
    async with _active_session_lock:
        return _active_session

async def _set_active_session(session) -> None:
    global _active_session
    async with _active_session_lock:
        _active_session = session

async def _exchange_api_key_for_jwt() -> str:
    global _cached_jwt
    resp = await _get_http_client().post(f"{RELAY_URL}/v1/auth/token", json={"api_key": API_KEY}, timeout=10)
    resp.raise_for_status()
    _cached_jwt = resp.json()["token"]
    return _cached_jwt

async def _get_bearer_token() -> str:
    global _cached_jwt
    if not API_KEY:
        return RELAY_SECRET
    async with _jwt_lock:
        return _cached_jwt or await _exchange_api_key_for_jwt()

async def _refresh_jwt_on_401() -> str | None:
    global _cached_jwt
    if not API_KEY:
        return None
    async with _jwt_lock:
        _cached_jwt = None
        with suppress(Exception):
            return await _exchange_api_key_for_jwt()
        return None

def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_cached_jwt or RELAY_SECRET}"}

def _relay_error_message(exc: Exception) -> str:
    if isinstance(exc, httpx.ConnectError):
        return f"Error: Cannot reach relay server at {RELAY_URL}"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"Error: Relay returned {exc.response.status_code}: {exc.response.text}"
    return f"Error: {exc}"

def _format_message(msg: dict) -> str:
    return f"[{msg.get('timestamp', '')}] {msg.get('from_name', 'unknown')}: {msg.get('content', '')}"

async def _remember_session(context: Context | None) -> None:
    if context is None:
        return
    try:
        session = context.session
    except ValueError:
        return
    await _set_active_session(session)

async def _append_pending_messages(messages: list[dict]) -> None:
    if messages:
        async with _pending_lock:
            _pending_messages.extend(messages)

async def _drain_pending_messages() -> list[dict]:
    async with _pending_lock:
        msgs, _pending_messages[:] = list(_pending_messages), []
        return msgs

async def _send_push_notification(session, msg: dict) -> None:
    if not PUSH_NOTIFICATION_METHOD:
        return
    params: dict[str, str] = {"message": _format_message(msg)}
    if PUSH_NOTIFICATION_CHANNEL:
        params["channel"] = PUSH_NOTIFICATION_CHANNEL
    notif = types.JSONRPCNotification(jsonrpc="2.0", method=PUSH_NOTIFICATION_METHOD, params=params)
    await session.send_message(SessionMessage(message=types.JSONRPCMessage(notif)))

async def _notify_active_session(messages: list[dict]) -> None:
    if not PUSH_NOTIFICATION_METHOD or not messages:
        return
    session = await _get_active_session()
    if session is None:
        return
    try:
        for msg in messages:
            await _send_push_notification(session, msg)
    except Exception:
        logger.warning("Failed to deliver push notification(s)", exc_info=True)
        if await _get_active_session() is session:
            await _set_active_session(None)

async def _fetch_relay_messages(wait: int) -> tuple[list[dict], str | None, str | None]:
    try:
        resp = await _get_http_client().get(
            f"{RELAY_URL}/messages/{INSTANCE_NAME}",
            params={"wait": wait, "ack": "manual"},
            headers=_auth_headers(), timeout=max(wait + 5, 10),
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("messages", []), data.get("ack_token"), None
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return [], None, _relay_error_message(e)

async def _ack_messages(ack_token: str) -> None:
    with suppress(Exception):
        resp = await _get_http_client().post(
            f"{RELAY_URL}/messages/{INSTANCE_NAME}/ack",
            json={"ack_token": ack_token}, headers=_auth_headers(), timeout=10,
        )
        resp.raise_for_status()

async def _heartbeat_loop(stop_event: asyncio.Event, interval: int = 30) -> None:
    while not stop_event.is_set():
        with suppress(Exception):
            resp = await _get_http_client().post(
                f"{RELAY_URL}/heartbeat",
                json={"instance_name": INSTANCE_NAME, "status": "active"},
                headers=_auth_headers(), timeout=10,
            )
            resp.raise_for_status()
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            pass

async def _process_sse_event(event_type: str, data: str) -> None:
    if event_type == "message":
        msg = process_sse_event_data(data)
        if msg is not None:
            await _append_pending_messages([msg])
            await _notify_active_session([msg])

async def _get_sse_token() -> str:
    with suppress(Exception):
        bearer = await _get_bearer_token()
        resp = await _get_http_client().post(
            f"{RELAY_URL}/stream/token",
            json={"recipient": INSTANCE_NAME},
            headers={"Authorization": f"Bearer {bearer}"},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()["token"]
    return RELAY_SECRET

async def _sse_listener(stop_event: asyncio.Event) -> None:
    """Backward-compat wrapper around SSEListener.run()."""
    await SSEListener(
        relay_url=RELAY_URL, instance_name=INSTANCE_NAME,
        get_http_client=_get_http_client, get_sse_token=_get_sse_token,
        on_event=_process_sse_event,
    ).run(stop_event)

@asynccontextmanager
async def _mcp_lifespan(server: FastMCP):
    global _http_client, _heartbeat_task
    _http_client = httpx.AsyncClient()
    if API_KEY:
        try:
            await _exchange_api_key_for_jwt()
        except Exception:
            logger.warning("Initial JWT exchange failed — will retry on first request", exc_info=True)
    stop_event = asyncio.Event()
    sse_task: asyncio.Task | None = None
    if SSE_ENABLED:
        sse_task = asyncio.create_task(_sse_listener(stop_event))
        logger.info("SSE push listener started")
    _heartbeat_task = asyncio.create_task(_heartbeat_loop(stop_event))
    try:
        yield {"stop_event": stop_event}
    finally:
        stop_event.set()
        for task in filter(None, [sse_task, _heartbeat_task]):
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if _http_client is not None:
            await _http_client.aclose()
        _reset_runtime_state()

QUORUS_INSTRUCTIONS = (
    "Quorus — inter-agent messaging for distributed Claude Code instances.\n\n"
    "Tools: send_message, send_room_message, check_messages, list_participants, "
    "list_rooms, join_room, search_room, room_metrics, claim_task, release_task, get_room_state\n\n"
    "CLI: quorus inbox | quorus say <room> <msg> | quorus dm <name> <msg>"
)

mcp = FastMCP("quorus", instructions=QUORUS_INSTRUCTIONS, lifespan=_mcp_lifespan)

_orig_list_tools = mcp._mcp_server.request_handlers[types.ListToolsRequest]
async def _list_tools_wrapped(req: types.ListToolsRequest):
    await _remember_session(mcp.get_context())
    return await _orig_list_tools(req)
mcp._mcp_server.request_handlers[types.ListToolsRequest] = _list_tools_wrapped
_install_session_capture = lambda: None

if SSE_ENABLED:
    _orig_init = mcp._mcp_server.create_initialization_options
    def _patched_init(**kw):
        opts = _orig_init(**kw)
        if opts.capabilities.experimental is None:
            opts.capabilities.experimental = {}
        opts.capabilities.experimental["claude/channel"] = {"channel": PUSH_NOTIFICATION_CHANNEL}
        return opts
    mcp._mcp_server.create_initialization_options = _patched_init

async def _send_message(to: str, content: str, context: Context | None = None) -> str:
    await _remember_session(context)
    try:
        resp = await _get_http_client().post(
            f"{RELAY_URL}/messages",
            json={"from_name": INSTANCE_NAME, "to": to, "content": content},
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        return f"Message sent (id: {data['id']})"
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return _relay_error_message(e)

async def _check_messages(context: Context | None = None) -> str:
    await _remember_session(context)
    await _drain_pending_messages()
    messages, ack_token, error = await _fetch_relay_messages(wait=0)
    if error:
        return error
    valid = [m for m in messages if m.get("from_name") or m.get("content") or m.get("timestamp")]
    if not valid:
        return "No new messages."
    result = "\n".join(_format_message(m) for m in valid)
    if ack_token:
        await _ack_messages(ack_token)
    return result

async def _list_participants(context: Context | None = None) -> str:
    await _remember_session(context)
    try:
        resp = await _get_http_client().get(f"{RELAY_URL}/participants", headers=_auth_headers())
        resp.raise_for_status()
        ps = resp.json()
        return "No participants yet." if not ps else "Participants: " + ", ".join(ps)
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return _relay_error_message(e)

async def _send_room_message(room_id: str, content: str, message_type: str = "chat", context: Context | None = None) -> str:
    await _remember_session(context)
    try:
        resp = await _get_http_client().post(
            f"{RELAY_URL}/rooms/{room_id}/messages",
            json={"from_name": INSTANCE_NAME, "content": content, "message_type": message_type},
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        return f"Room message sent (id: {resp.json()['id']})"
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return _relay_error_message(e)

async def _join_room(room_id: str, context: Context | None = None) -> str:
    await _remember_session(context)
    try:
        resp = await _get_http_client().post(
            f"{RELAY_URL}/rooms/{room_id}/join",
            json={"participant": INSTANCE_NAME}, headers=_auth_headers(),
        )
        resp.raise_for_status()
        return f"Joined room {room_id}"
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return _relay_error_message(e)

async def _list_rooms(context: Context | None = None) -> str:
    await _remember_session(context)
    try:
        resp = await _get_http_client().get(f"{RELAY_URL}/rooms", headers=_auth_headers())
        resp.raise_for_status()
        rooms_list = resp.json()
        if not rooms_list:
            return "No rooms yet."
        return "Rooms:\n" + "\n".join(
            f"  {r['name']} (id: {r['id']}) — members: {', '.join(r['members'])}" for r in rooms_list
        )
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return _relay_error_message(e)

@mcp.tool()
async def send_message(to: str, content: str, context: Context) -> str:
    """Send a direct message to another agent. Args: to (recipient name), content."""
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
async def send_room_message(room_id: str, content: str, message_type: str = "chat", context: Context = None) -> str:
    """Send a message to a room (all members receive it). Types: chat/claim/status/request/alert/sync."""
    return await _send_room_message(room_id, content, message_type, context)

@mcp.tool()
async def join_room(room_id: str, context: Context = None) -> str:
    """Join a room to start receiving its messages."""
    return await _join_room(room_id, context)

@mcp.tool()
async def list_rooms(context: Context = None) -> str:
    """List all available rooms with their members."""
    return await _list_rooms(context)

@mcp.tool()
async def search_room(room_id: str, q: str = "", sender: str = "", message_type: str = "", limit: int = 50) -> str:
    """Search room history by keyword (q), sender, or message_type. Returns up to limit results."""
    params: dict[str, str | int] = {"limit": limit}
    if q:
        params["q"] = q
    if sender:
        params["sender"] = sender
    if message_type:
        params["message_type"] = message_type
    resp = await _get_http_client().get(
        f"{RELAY_URL}/rooms/{room_id}/search", params=params, headers=_auth_headers(), timeout=10
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        return "No matching messages."
    lines = []
    for msg in results:
        ts = msg.get("timestamp", "")[:19]
        mtype = msg.get("message_type", "chat")
        tag = f" [{mtype}]" if mtype != "chat" else ""
        lines.append(f"[{ts}] {msg.get('from_name', '?')}{tag}: {msg.get('content', '')}")
    return "\n".join(lines)

@mcp.tool()
async def room_metrics(room_id: str) -> str:
    """Get activity metrics: messages per agent, type breakdown, task completion rate."""
    resp = await _get_http_client().get(
        f"{RELAY_URL}/rooms/{room_id}/history", params={"limit": 200}, headers=_auth_headers(), timeout=10
    )
    resp.raise_for_status()
    messages = resp.json()
    if not messages:
        return f"No messages in {room_id}."
    agent_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    claims = completions = 0
    for msg in messages:
        sender = msg.get("from_name", "?")
        mtype = msg.get("message_type", "chat")
        agent_counts[sender] = agent_counts.get(sender, 0) + 1
        type_counts[mtype] = type_counts.get(mtype, 0) + 1
        if mtype == "claim":
            claims += 1
        elif mtype == "status" and "complete" in msg.get("content", "").lower():
            completions += 1
    lines = [f"Metrics for {room_id} ({len(messages)} messages):", "", "Agents:"]
    lines += [f"  {n}: {c}" for n, c in sorted(agent_counts.items(), key=lambda x: x[1], reverse=True)]
    lines += ["", "Message types:"]
    lines += [f"  {t}: {c}" for t, c in sorted(type_counts.items(), key=lambda x: x[1], reverse=True)]
    if claims > 0:
        lines.append(f"\nTask completion: {completions}/{claims} ({min(completions/claims*100,100):.0f}%)")
    return "\n".join(lines)

@mcp.tool()
async def claim_task(room_id: str, file_path: str, description: str = "", ttl_seconds: int = 300) -> str:
    """Acquire an optimistic file lock (Primitive B). Returns GRANTED+token or LOCKED+holder."""
    try:
        client = _get_http_client()
        payload = {"file_path": file_path, "claimed_by": INSTANCE_NAME, "description": description, "ttl_seconds": ttl_seconds}
        resp = await client.post(f"{RELAY_URL}/rooms/{room_id}/lock", json=payload, headers=_auth_headers(), timeout=10)
        if resp.status_code == 401 and await _refresh_jwt_on_401():
            resp = await client.post(f"{RELAY_URL}/rooms/{room_id}/lock", json=payload, headers=_auth_headers(), timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("locked"):
            return f"LOCKED: {file_path} is held by {data['held_by']}, expires {data['expires_at']}"
        return f"GRANTED: lock_token={data['lock_token']} expires={data['expires_at']}"
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return _relay_error_message(e)

@mcp.tool()
async def release_task(room_id: str, file_path: str, lock_token: str) -> str:
    """Release a file lock (Primitive B). Requires the lock_token from claim_task."""
    try:
        client = _get_http_client()
        url = f"{RELAY_URL}/rooms/{room_id}/lock/{file_path}"
        payload = {"lock_token": lock_token}
        resp = await client.request("DELETE", url, json=payload, headers=_auth_headers(), timeout=10)
        if resp.status_code == 401 and await _refresh_jwt_on_401():
            resp = await client.request("DELETE", url, json=payload, headers=_auth_headers(), timeout=10)
        resp.raise_for_status()
        return f"RELEASED: {file_path}"
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return _relay_error_message(e)

@mcp.tool()
async def get_room_state(room_id: str) -> str:
    """Get the Shared State Matrix (Primitive A): goal, tasks, locks, decisions, agents."""
    try:
        client = _get_http_client()
        resp = await client.get(f"{RELAY_URL}/rooms/{room_id}/state", headers=_auth_headers(), timeout=10)
        if resp.status_code == 401 and await _refresh_jwt_on_401():
            resp = await client.get(f"{RELAY_URL}/rooms/{room_id}/state", headers=_auth_headers(), timeout=10)
        resp.raise_for_status()
        data = resp.json()
        lines = [
            f"Room: {room_id} (snapshot: {data.get('snapshot_at', '')[:19]})",
            f"Schema: {data.get('schema_version', '?')}",
            f"Goal: {data.get('active_goal') or '(none)'}",
            f"Active agents ({len(data.get('active_agents', []))}): " + ", ".join(data.get("active_agents", [])),
            f"Messages: {data.get('message_count', 0)} | Last activity: {(data.get('last_activity') or '')[:19]}",
        ]
        if data.get("claimed_tasks"):
            lines.append(f"\nClaimed tasks ({len(data['claimed_tasks'])}):")
            for t in data["claimed_tasks"]:
                lines.append(f"  [{t['claimed_by']}] {t['file_path']} (expires {t.get('expires_at', '')[:19]})")
        if data.get("locked_files"):
            lines.append(f"\nLocked files ({len(data['locked_files'])}):")
            for fp, lock in data["locked_files"].items():
                lines.append(f"  {fp} \u2192 held by {lock['held_by']}")
        if data.get("resolved_decisions"):
            lines.append(f"\nDecisions ({len(data['resolved_decisions'])}):")
            for d in data["resolved_decisions"][-5:]:
                lines.append(f"  [{d.get('decided_by', '?')}] {d['decision']}")
        return "\n".join(lines)
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return _relay_error_message(e)

def main_cli() -> None:
    """Console entry point for the ``quorus-mcp`` command (stdio transport)."""
    mcp.run(transport="stdio")

if __name__ == "__main__":
    main_cli()
