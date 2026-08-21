import asyncio
import logging
import os
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx
from mcp import types
from mcp.server.fastmcp import Context, FastMCP
from mcp.shared.message import SessionMessage

from quorus.operating_discipline import render_qod_for_mcp
from quorus_mcp import runtime, tools
from quorus_mcp.sse import SSEListener

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO")),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("mcp_tunnel.mcp")

# Config resolution + startup guards live in ``runtime.py``. Calling the
# loader here keeps ``importlib.reload(quorus_mcp.server)`` re-reading the
# environment (auth-fallback tests rely on this). tools/phase1_tools/runtime
# read these constants back through THIS module's namespace at call time,
# so monkeypatching ``quorus.mcp_server.RELAY_URL`` etc. works unchanged.
_settings = runtime.load_runtime_config()
CONFIG_FILE = Path(_settings["config_file"])
RELAY_URL = _settings["relay_url"]
RELAY_SECRET = _settings["relay_secret"]
API_KEY = _settings["api_key"]
INSTANCE_NAME = _settings["instance_name"]
SSE_ENABLED = _settings["sse_enabled"]
PUSH_NOTIFICATION_METHOD = _settings["push_notification_method"]
PUSH_NOTIFICATION_CHANNEL = _settings["push_notification_channel"]

# Back-compat re-exports: these helpers moved to ``runtime.py`` (2026-08
# split). Tests/callers still reach them via ``quorus.mcp_server.<name>``,
# and the runtime loops resolve them through this namespace so patches apply.
_clean_env = runtime._clean_env
_resolve_auth = runtime._resolve_auth
_validate_header_safe = runtime._validate_header_safe
_validate_relay_url = runtime._validate_relay_url
_fetch_relay_messages = runtime._fetch_relay_messages
_ack_messages = runtime._ack_messages
_heartbeat_loop = runtime._heartbeat_loop
_process_sse_event = runtime._process_sse_event
_get_sse_token = runtime._get_sse_token
_sse_listener = runtime._sse_listener
_polling_fallback = runtime._polling_fallback
_mcp_lifespan = runtime._mcp_lifespan

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


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient()
    return _http_client


def _reset_runtime_state() -> None:
    """Reset module-level runtime state WITHOUT taking any locks.

    TEST-ONLY helper: it is synchronous so fixtures can call it outside a
    running event loop, which also means it cannot acquire
    ``_active_session_lock``. Production teardown paths (the FastMCP
    lifespan) must use :func:`reset_runtime_state_locked` instead so the
    reset cannot interleave with a concurrent session read/write.
    """
    global _active_session, _http_client, _heartbeat_task, _active_sse_listener
    _pending_messages.clear()
    _active_session = None
    _http_client = None
    _heartbeat_task = None
    _active_sse_listener = None


async def reset_runtime_state_locked() -> None:
    """Reset runtime state while holding ``_active_session_lock``.

    Async, lock-held counterpart to the test-only
    :func:`_reset_runtime_state`. Used by the lifespan teardown so a
    concurrent ``_get_active_session`` / ``_set_active_session`` cannot
    observe a half-reset module.
    """
    async with _active_session_lock:
        _reset_runtime_state()


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
    """Return the Bearer token to send on a relay request.

    Defensive guard: if both ``API_KEY`` and ``RELAY_SECRET`` are empty
    (would normally be caught at startup) we raise rather than silently
    emit ``Authorization: Bearer `` which the relay rejects as an
    illegal header value.
    """
    global _cached_jwt
    if not API_KEY:
        if not RELAY_SECRET:
            raise RuntimeError(
                "Quorus MCP has no auth credential at request time "
                "(API_KEY and RELAY_SECRET both empty)."
            )
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

# This module is a facade: tool implementations live in ``tools.py`` /
# ``phase1_tools.py``; runtime plumbing lives in ``runtime.py`` — keeping
# every file in this package within the 500-line cap. The forwarders below
# keep existing tests (``mcp_server._send_message`` etc.) working.
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


# ---------------------------------------------------------------------------
# Plan v8 Phase 1 OS primitives — capability discovery, tool catalog, memory.
# Implementations live in ``phase1_tools.py`` to keep this file readable.
# ---------------------------------------------------------------------------

from quorus_mcp import phase1_tools as _p1  # noqa: E402


@mcp.tool()
async def publish_capability(
    description: str = "",
    supported_languages: list[str] | None = None,
    supported_frameworks: list[str] | None = None,
    capabilities: list[str] | None = None,
) -> str:
    """Publish this agent's capability manifest so peers can find it."""
    return await _p1.publish_capability(
        description=description,
        supported_languages=supported_languages,
        supported_frameworks=supported_frameworks,
        capabilities=capabilities,
    )


@mcp.tool()
async def lookup_capability(participant: str) -> str:
    """Fetch a participant's capability manifest in this tenant."""
    return await _p1.lookup_capability(participant)


@mcp.tool()
async def search_capabilities(has: str = "") -> str:
    """Find participants whose capabilities match every token in ``has``."""
    return await _p1.search_capabilities(has)


@mcp.tool()
async def register_tool(
    room_id: str,
    name: str,
    url: str,
    access: str = "room",
    description: str = "",
) -> str:
    """Register an MCP server in the room. Room admin only."""
    return await _p1.register_tool(
        room_id, name, url, access=access, description=description,
    )


@mcp.tool()
async def list_room_tools(room_id: str) -> str:
    """List MCP servers registered in the room. Members only."""
    return await _p1.list_room_tools(room_id)


@mcp.tool()
async def unregister_tool(room_id: str, name: str) -> str:
    """Remove an MCP server registration. Room admin only."""
    return await _p1.unregister_tool(room_id, name)


@mcp.tool()
async def memory_set(
    room_id: str,
    key: str,
    value: object,
    visibility: str = "private",
) -> str:
    """Store a value in this agent's per-room persistent memory."""
    return await _p1.memory_set(room_id, key, value, visibility=visibility)


@mcp.tool()
async def memory_get(
    room_id: str,
    key: str,
    owner: str | None = None,
) -> str:
    """Read a memory entry. ``owner`` defaults to this agent."""
    return await _p1.memory_get(room_id, key, owner=owner)


@mcp.tool()
async def memory_list(
    room_id: str,
    owner: str | None = None,
) -> str:
    """List readable memory entries for ``owner`` in ``room_id``."""
    return await _p1.memory_list(room_id, owner=owner)


@mcp.tool()
async def memory_delete(room_id: str, key: str) -> str:
    """Delete this agent's memory entry under ``key`` (GDPR)."""
    return await _p1.memory_delete(room_id, key)


def main_cli() -> None:
    """Console entry point for the ``quorus-mcp`` command (stdio transport)."""
    mcp.run(transport="stdio")

if __name__ == "__main__":
    main_cli()
