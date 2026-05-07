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

from quorus.config import ConfigManager, load_config
from quorus.operating_discipline import render_qod_for_mcp
from quorus_mcp import tools
from quorus_mcp.sse import SSEListener, process_sse_event_data

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO")),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("mcp_tunnel.mcp")

# Honor QUORUS_PROFILE env var: pick a specific profile without touching
# "current". Falls back to the current-profile pointer / legacy migration
# path inside load_config().
_profile_slug = os.environ.get("QUORUS_PROFILE")
if _profile_slug:
    # Load just the named profile's data, then let load_config() apply
    # env-var overrides on top (env still beats profile for direct
    # overrides like QUORUS_RELAY_URL).
    _profile_data = ConfigManager(profile=_profile_slug).load()
    # load_config() reads from resolve_config_file() — temporarily point
    # it at the profile file by setting QUORUS_CONFIG_DIR is too invasive,
    # so instead we build the config dict by hand using the same priority
    # rules load_config() uses (env > file > default).
    _config = {
        "config_file": str(ConfigManager(profile=_profile_slug).path),
        "relay_url": (
            os.environ.get("RELAY_URL")
            or _profile_data.get("relay_url", "http://localhost:8080")
        ),
        "relay_secret": (
            os.environ.get("RELAY_SECRET")
            or _profile_data.get("relay_secret", "")
        ),
        "api_key": (
            os.environ.get("API_KEY") or _profile_data.get("api_key", "")
        ),
        "instance_name": (
            os.environ.get("INSTANCE_NAME")
            or _profile_data.get("instance_name", "default")
        ),
        "enable_background_polling": True,
        "push_notification_method": (
            os.environ.get("PUSH_NOTIFICATION_METHOD")
            or _profile_data.get("push_notification_method")
            or "notifications/claude/channel"
        ),
        "push_notification_channel": (
            os.environ.get("PUSH_NOTIFICATION_CHANNEL")
            or _profile_data.get("push_notification_channel", "quorus")
        ),
    }
else:
    _config = load_config()
CONFIG_FILE = Path(_config["config_file"])
# Per-agent identity + connection overrides. Each client the CLI wires
# (Claude Code, Codex, Cursor, …) gets its own QUORUS_INSTANCE_NAME via
# env so the relay sees them as distinct participants even though they
# share `~/.quorus/config.json`. Env beats config for all four fields.
RELAY_URL = os.environ.get("QUORUS_RELAY_URL") or _config["relay_url"]
RELAY_SECRET = os.environ.get("QUORUS_RELAY_SECRET") or _config["relay_secret"]
API_KEY = os.environ.get("QUORUS_API_KEY") or _config["api_key"]
INSTANCE_NAME = os.environ.get("QUORUS_INSTANCE_NAME") or _config["instance_name"]
SSE_ENABLED = os.environ.get("SSE_ENABLED", "true").strip().lower() not in {
    "0", "false", "no", "off",
}
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
# Active SSEListener instance — set during lifespan, used to surface
# circuit-breaker state for diagnostics and to gate the polling fallback.
_active_sse_listener: SSEListener | None = None

def _validate_relay_url(value: str) -> str:
    p = urlparse(value)
    if not value or p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise SystemExit(f"Invalid relay_url: {value!r}. Must be an http(s) URL with a hostname.")
    return value

_validate_relay_url(RELAY_URL)
if not RELAY_SECRET and not API_KEY:
    raise SystemExit(
        "Neither relay_secret nor api_key is set. "
        "Set RELAY_SECRET or API_KEY env var or config file value."
    )
logger.info(
    "Config loaded: relay_url=%s instance=%s sse_enabled=%s",
    RELAY_URL, INSTANCE_NAME, SSE_ENABLED,
)

def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient()
    return _http_client

def _reset_runtime_state() -> None:
    global _active_session, _http_client, _heartbeat_task, _active_sse_listener
    _pending_messages.clear()
    _active_session = None
    _http_client = None
    _heartbeat_task = None
    _active_sse_listener = None


def _sse_breaker_state() -> dict[str, Any]:
    """Return SSE circuit-breaker diagnostics.

    Shape: ``{"tripped": bool, "failures": int, "last_error": str | None}``.
    Returns the default cleared state when no listener is active (e.g.
    SSE disabled or before lifespan startup).
    """
    if _active_sse_listener is None:
        return {"tripped": False, "failures": 0, "last_error": None}
    return _active_sse_listener.breaker_state()

async def _get_active_session():
    async with _active_session_lock:
        return _active_session

async def _set_active_session(session) -> None:
    global _active_session
    async with _active_session_lock:
        _active_session = session

async def _exchange_api_key_for_jwt() -> str:
    global _cached_jwt
    resp = await _get_http_client().post(
        f"{RELAY_URL}/v1/auth/token",
        json={"api_key": API_KEY}, timeout=10,
    )
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

async def _auth_headers_async() -> dict[str, str]:
    """Return auth headers, lazily minting a JWT when API-key auth is active.

    MCP tools can be exercised directly in tests or by hosts that invoke a
    tool before the lifespan's initial JWT exchange completes. In that case
    the old synchronous helper emitted ``Bearer `` from an empty cache. Keep
    the sync helper for legacy call sites, but prefer this async helper for
    real network requests.
    """
    return {"Authorization": f"Bearer {await _get_bearer_token()}"}

def _relay_error_message(exc: Exception) -> str:
    if isinstance(exc, httpx.ConnectError):
        return f"Error: Cannot reach relay server at {RELAY_URL}"
    if isinstance(exc, httpx.TimeoutException):
        return f"Error: Relay request timed out at {RELAY_URL}"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"Error: Relay returned {exc.response.status_code}: {exc.response.text}"
    return f"Error: {exc}"

def _format_message(msg: dict) -> str:
    return (
        f"[{msg.get('timestamp', '')}] "
        f"{msg.get('from_name', 'unknown')}: {msg.get('content', '')}"
    )

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
            headers=await _auth_headers_async(), timeout=max(wait + 5, 10),
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
            json={"ack_token": ack_token},
            headers=await _auth_headers_async(),
            timeout=10,
        )
        resp.raise_for_status()

async def _heartbeat_loop(stop_event: asyncio.Event, interval: int = 30) -> None:
    while not stop_event.is_set():
        with suppress(Exception):
            resp = await _get_http_client().post(
                f"{RELAY_URL}/heartbeat",
                json={"instance_name": INSTANCE_NAME, "status": "active"},
                headers=await _auth_headers_async(), timeout=10,
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
    """Run the SSE listener and expose its instance for breaker diagnostics."""
    global _active_sse_listener
    listener = SSEListener(
        relay_url=RELAY_URL, instance_name=INSTANCE_NAME,
        get_http_client=_get_http_client, get_sse_token=_get_sse_token,
        on_event=_process_sse_event,
    )
    _active_sse_listener = listener
    try:
        await listener.run(stop_event)
    finally:
        _active_sse_listener = None


# Polling fallback: drains the relay's pull endpoint while the circuit
# breaker is tripped. Sleeps cheaply otherwise. This is automatic; there
# is no user-facing toggle (the dead poll-mode path was removed).
_FALLBACK_POLL_INTERVAL = 5
_FALLBACK_POLL_WAIT = 25


async def _polling_fallback(stop_event: asyncio.Event) -> None:
    """Poll the relay for messages while the SSE breaker is tripped."""
    while not stop_event.is_set():
        state = _sse_breaker_state()
        if not state["tripped"]:
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=_FALLBACK_POLL_INTERVAL,
                )
                return
            except asyncio.TimeoutError:
                continue
        messages, ack_token, error = await _fetch_relay_messages(
            wait=_FALLBACK_POLL_WAIT,
        )
        if error:
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=_FALLBACK_POLL_INTERVAL,
                )
                return
            except asyncio.TimeoutError:
                continue
        if messages:
            await _append_pending_messages(messages)
            await _notify_active_session(messages)
        if ack_token:
            await _ack_messages(ack_token)

@asynccontextmanager
async def _mcp_lifespan(server: FastMCP):
    global _http_client, _heartbeat_task
    _http_client = httpx.AsyncClient()
    if API_KEY:
        try:
            await _exchange_api_key_for_jwt()
        except Exception:
            logger.warning(
                "Initial JWT exchange failed — will retry on first request",
                exc_info=True,
            )
    stop_event = asyncio.Event()
    sse_task: asyncio.Task | None = None
    poll_task: asyncio.Task | None = None
    if SSE_ENABLED:
        sse_task = asyncio.create_task(_sse_listener(stop_event))
        poll_task = asyncio.create_task(_polling_fallback(stop_event))
        logger.info("SSE push listener started (polling fallback armed)")
    _heartbeat_task = asyncio.create_task(_heartbeat_loop(stop_event))
    try:
        yield {"stop_event": stop_event}
    finally:
        stop_event.set()
        for task in filter(None, [sse_task, poll_task, _heartbeat_task]):
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if _http_client is not None:
            await _http_client.aclose()
        _reset_runtime_state()

# MCP `instructions` is the cross-harness on-ramp for the Quorus Operating
# Discipline (QOD). Hosts that surface server instructions to their model
# (Claude Desktop, Claude Code, Cursor, Gemini CLI, Cline, Continue,
# Windsurf, Codex via mcp_servers config) pick this up automatically. The
# canonical QOD lives in ``quorus.operating_discipline`` so the agent-loop
# sysprompt prepend and the skill module render the exact same six rules.
_QOD = render_qod_for_mcp()
_QOD_TAIL = (
    "Quorus — coordination layer for AI agent swarms.\n\n"
    "CLI: quorus inbox | quorus say <room> <msg> | quorus dm <name> <msg> | "
    "quorus heartbeat"
)
QUORUS_INSTRUCTIONS = f"{_QOD}\n\n---\n\n{_QOD_TAIL}"

mcp = FastMCP("quorus", instructions=QUORUS_INSTRUCTIONS, lifespan=_mcp_lifespan)

_orig_list_tools = mcp._mcp_server.request_handlers[types.ListToolsRequest]
async def _list_tools_wrapped(req: types.ListToolsRequest):
    await _remember_session(mcp.get_context())
    return await _orig_list_tools(req)
mcp._mcp_server.request_handlers[types.ListToolsRequest] = _list_tools_wrapped


def _install_session_capture() -> None:
    """Placeholder — session capture is installed via the list_tools wrap above."""

if SSE_ENABLED:
    _orig_init = mcp._mcp_server.create_initialization_options
    def _patched_init(**kw):
        opts = _orig_init(**kw)
        if opts.capabilities.experimental is None:
            opts.capabilities.experimental = {}
        opts.capabilities.experimental["claude/channel"] = {"channel": PUSH_NOTIFICATION_CHANNEL}
        return opts
    mcp._mcp_server.create_initialization_options = _patched_init

# Tool implementations live in ``tools.py`` to keep this module under the
# 500-line cap. The underscore-prefixed names below are kept as forwarders
# so existing tests (``mcp_server._send_message`` etc.) keep working.
_send_message = tools.send_message
_check_messages = tools.check_messages
_list_participants = tools.list_participants
_send_room_message = tools.send_room_message
_join_room = tools.join_room
_list_rooms = tools.list_rooms


@mcp.tool()
async def send_message(to: str, content: str, context: Context) -> str:
    """Send a direct message to another agent. Args: to, content."""
    return await tools.send_message(to, content, context)


@mcp.tool()
async def check_messages(context: Context) -> str:
    """Check for new messages sent to this instance."""
    return await tools.check_messages(context)


@mcp.tool()
async def list_participants(context: Context) -> str:
    """List all known participants who have sent messages through the relay."""
    return await tools.list_participants(context)


@mcp.tool()
async def send_room_message(
    room_id: str,
    content: str,
    message_type: str = "chat",
    context: Context = None,
) -> str:
    """Send a message to a room. Types: chat/claim/status/request/alert/sync."""
    return await tools.send_room_message(room_id, content, message_type, context)


@mcp.tool()
async def join_room(room_id: str, context: Context = None) -> str:
    """Join a room to start receiving its messages."""
    return await tools.join_room(room_id, context)


@mcp.tool()
async def list_rooms(context: Context = None) -> str:
    """List all available rooms with their members."""
    return await tools.list_rooms(context)


@mcp.tool()
async def search_room(
    room_id: str,
    q: str = "",
    sender: str = "",
    message_type: str = "",
    limit: int = 50,
) -> str:
    """Search room history by keyword (q), sender, or message_type."""
    return await tools.search_room(room_id, q, sender, message_type, limit)


@mcp.tool()
async def room_metrics(room_id: str) -> str:
    """Activity metrics: messages per agent, type breakdown, task completion."""
    return await tools.room_metrics(room_id)


@mcp.tool()
async def claim_task(
    room_id: str,
    file_path: str,
    description: str = "",
    ttl_seconds: int = 300,
) -> str:
    """Acquire an optimistic file lock. Returns GRANTED+token or LOCKED+holder."""
    return await tools.claim_task(room_id, file_path, description, ttl_seconds)


@mcp.tool()
async def release_task(room_id: str, file_path: str, lock_token: str) -> str:
    """Release a file lock. Requires the lock_token from claim_task."""
    return await tools.release_task(room_id, file_path, lock_token)


@mcp.tool()
async def get_room_state(room_id: str) -> str:
    """Get the Shared State Matrix: goal, tasks, locks, decisions, agents."""
    return await tools.get_room_state(room_id)


@mcp.tool()
async def social_verb(
    verb: str,
    room_id: str,
    payload: dict,
    ref_message_id: str | None = None,
    context: Context = None,
) -> str:
    """Submit a Quorus Social Protocol v1 verb to a room.

    Verbs: claim, release, disagree, defer, queue, vote, interrupt.
    See docs/SOCIAL_PROTOCOL_v1.md for the per-verb payload schema.
    """
    return await tools.social_verb(verb, room_id, payload, ref_message_id, context)


def main_cli() -> None:
    """Console entry point for the ``quorus-mcp`` command (stdio transport)."""
    mcp.run(transport="stdio")

if __name__ == "__main__":
    main_cli()
