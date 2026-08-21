"""Runtime plumbing for the Quorus MCP server.

Extracted from ``server.py`` (2026-08 file split) to keep that module under
the 500-line cap. Two kinds of things live here:

* :func:`load_runtime_config` — env/profile resolution plus the startup
  guards (URL validation, empty-credential fail-closed, header-safety).
  ``server.py`` calls it at import time and binds the resolved values as its
  own module-level constants, so ``importlib.reload(quorus_mcp.server)``
  re-reads the environment (the auth-fallback regression tests rely on this).
* The long-running loops (heartbeat, SSE listener, polling fallback) and the
  FastMCP lifespan. These resolve shared mutable state (``_active_session``,
  ``_http_client``, ``_active_sse_listener``, ...) and patchable helpers
  (``_get_http_client``, ``_fetch_relay_messages``, ...) through the server
  module via :func:`_srv` at call time — the same late-binding pattern as
  ``tools.py`` — so monkeypatching ``quorus.mcp_server.<name>`` keeps
  working exactly as it did before the split.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from quorus.config import ConfigManager, load_config
from quorus_mcp.sse import SSEListener, process_sse_event_data

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("mcp_tunnel.mcp")


def _srv():
    """Import the server module lazily to avoid a circular import.

    Resolving constants, helpers, and mutable state through the server
    module's namespace at call time keeps test monkeypatches
    (``patch.object(mcp_server, "_fetch_relay_messages", ...)``) and direct
    attribute mutations (``mcp_server._active_sse_listener = ...``) visible
    to the loops defined here.
    """
    from quorus_mcp import server as _server_module

    return _server_module


# ---------------------------------------------------------------------------
# Config resolution + startup guards
# ---------------------------------------------------------------------------

def _clean_env(name: str) -> str | None:
    """Read an env var, treating empty/whitespace-only values as UNSET.

    Codex audit (2026-05-03): a stale ``QUORUS_API_KEY=`` (empty) or
    ``QUORUS_API_KEY=" "`` (whitespace) caused the MCP module to construct
    ``Authorization: Bearer `` and the relay to reject with HTTP 400
    "Illegal header value". Whitespace-only values are now treated as
    not-set so the loader falls through to the profile JSON.
    """
    raw = os.environ.get(name)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def _resolve_auth(env_name: str, file_value: str, *, label: str) -> str:
    """Resolve an auth field with empty-env fallback + one-time stderr note."""
    env_val = _clean_env(env_name)
    if env_val is not None:
        return env_val
    # If the env var was *present* but blank, emit one stderr warning so
    # operators see why we fell back to the profile.
    if env_name in os.environ:
        logger.warning(
            "%s env var %s was empty; falling back to profile/config file. "
            "(No key value is logged.)",
            label, env_name,
        )
    return (file_value or "").strip()


# Production API keys are minted as ``mct_<hex>_<hex>`` (see
# ``quorus.auth.tokens.generate_api_key``). We log an advisory if the
# resolved key doesn't match — but we do NOT block, because tests and
# self-hosted deployments use other formats.
_API_KEY_RE = re.compile(r"^mct_[a-f0-9]+_[a-f0-9]+$")
# Reject anything that would produce an *illegal* HTTP header value
# (whitespace, CR/LF, or non-printable bytes). httpx would otherwise
# raise ``Illegal header value`` on the relay request — that was the
# original codex audit symptom (empty Bearer header).
_HEADER_UNSAFE_RE = re.compile(r"[\s\x00-\x1f\x7f]")


def _validate_header_safe(value: str, *, label: str) -> None:
    """Fail closed if ``value`` contains chars that produce an illegal header.

    Empty values are rejected by the caller's own check (see the startup
    guard in :func:`load_runtime_config`). This guard is for the
    harder-to-spot case: a non-empty token that contains a CR/LF, embedded
    whitespace, or NUL — any of which would make the relay return
    ``400 Illegal header value``.
    """
    if value and _HEADER_UNSAFE_RE.search(value):
        raise SystemExit(
            f"{label} contains whitespace or control characters that would "
            "produce an illegal HTTP Authorization header. Re-run "
            "`quorus login` or unset the offending env var."
        )


def _validate_relay_url(value: str) -> str:
    p = urlparse(value)
    if not value or p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise SystemExit(f"Invalid relay_url: {value!r}. Must be an http(s) URL with a hostname.")
    return value


def load_runtime_config() -> dict[str, Any]:
    """Resolve the MCP server's runtime settings and run the startup guards.

    Precedence per field: ``QUORUS_*`` env var > profile/config file. Honors
    ``QUORUS_PROFILE`` to pick a specific profile without touching
    "current". Empty/whitespace env values fall through to the file value,
    preventing ``Bearer `` empty auth headers (codex audit fix).

    Raises ``SystemExit`` (fail closed) on an invalid relay URL, missing
    credentials, or header-unsafe credential values.
    """
    profile_slug = _clean_env("QUORUS_PROFILE")
    if profile_slug:
        # Load just the named profile's data, then apply env-var overrides
        # on top (env still beats profile for direct overrides like
        # QUORUS_RELAY_URL below).
        profile_data = ConfigManager(profile=profile_slug).load()
        config = {
            "config_file": str(ConfigManager(profile=profile_slug).path),
            "relay_url": (
                _clean_env("RELAY_URL")
                or (profile_data.get("relay_url") or "http://localhost:8080")
            ),
            "relay_secret": (
                _clean_env("RELAY_SECRET")
                or (profile_data.get("relay_secret") or "")
            ),
            "api_key": (
                _clean_env("API_KEY")
                or (profile_data.get("api_key") or "")
            ),
            "instance_name": (
                _clean_env("INSTANCE_NAME")
                or (profile_data.get("instance_name") or "default")
            ),
            "push_notification_method": (
                _clean_env("PUSH_NOTIFICATION_METHOD")
                or profile_data.get("push_notification_method")
                or "notifications/claude/channel"
            ),
            "push_notification_channel": (
                _clean_env("PUSH_NOTIFICATION_CHANNEL")
                or (profile_data.get("push_notification_channel") or "quorus")
            ),
        }
    else:
        config = load_config()

    relay_url = _clean_env("QUORUS_RELAY_URL") or config["relay_url"]
    relay_secret = _resolve_auth(
        "QUORUS_RELAY_SECRET", config["relay_secret"], label="Relay secret",
    )
    api_key = _resolve_auth("QUORUS_API_KEY", config["api_key"], label="API key")
    instance_name = _clean_env("QUORUS_INSTANCE_NAME") or config["instance_name"]
    sse_enabled = os.environ.get("SSE_ENABLED", "true").strip().lower() not in {
        "0", "false", "no", "off",
    }

    _validate_relay_url(relay_url)
    # Both auth fields are already stripped by _resolve_auth, so an empty
    # string here means the value was missing in BOTH the env var and the
    # profile file. We fail closed with a clear message instead of letting
    # the relay receive a malformed ``Authorization: Bearer `` header.
    if not relay_secret and not api_key:
        raise SystemExit(
            "Neither relay_secret nor api_key resolved to a non-empty value.\n"
            "  - Set QUORUS_API_KEY (or RELAY_SECRET) to a non-empty token, OR\n"
            "  - Run `quorus login` to populate ~/.quorus/profiles/default.json.\n"
            "Empty/whitespace env vars are now treated as unset and fall back\n"
            "to the profile file."
        )
    # Defense in depth: even if we got a non-empty value, refuse to ship it
    # as a Bearer token if it contains characters that would make the HTTP
    # header illegal. This prevents a different shape of the same bug class.
    _validate_header_safe(api_key, label="API_KEY")
    _validate_header_safe(relay_secret, label="RELAY_SECRET")
    # Advisory: production keys are ``mct_<hex>_<hex>``. Non-matching values
    # (test fixtures, hand-typed keys) are accepted but logged once so a
    # misconfigured deployment is visible without grepping the relay logs.
    if api_key and not _API_KEY_RE.match(api_key):
        logger.warning(
            "API_KEY does not match the expected mct_<hex>_<hex> shape. "
            "(No key value is logged.) The relay may reject this credential."
        )
    logger.info(
        "Config loaded: relay_url=%s instance=%s sse_enabled=%s",
        relay_url, instance_name, sse_enabled,
    )
    return {
        "config_file": config["config_file"],
        "relay_url": relay_url,
        "relay_secret": relay_secret,
        "api_key": api_key,
        "instance_name": instance_name,
        "sse_enabled": sse_enabled,
        "push_notification_method": config["push_notification_method"],
        "push_notification_channel": config["push_notification_channel"],
    }


# ---------------------------------------------------------------------------
# Relay I/O loops (heartbeat, SSE listener, polling fallback) + lifespan
# ---------------------------------------------------------------------------

async def _fetch_relay_messages(wait: int) -> tuple[list[dict], str | None, str | None]:
    s = _srv()
    try:
        resp = await s._get_http_client().get(
            f"{s.RELAY_URL}/messages/{s.INSTANCE_NAME}",
            params={"wait": wait, "ack": "manual"},
            headers=await s._auth_headers_async(), timeout=max(wait + 5, 10),
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("messages", []), data.get("ack_token"), None
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return [], None, s._relay_error_message(e)


async def _ack_messages(ack_token: str) -> None:
    s = _srv()
    with suppress(Exception):
        resp = await s._get_http_client().post(
            f"{s.RELAY_URL}/messages/{s.INSTANCE_NAME}/ack",
            json={"ack_token": ack_token},
            headers=await s._auth_headers_async(),
            timeout=10,
        )
        resp.raise_for_status()


async def _heartbeat_loop(stop_event: asyncio.Event, interval: int = 30) -> None:
    s = _srv()
    while not stop_event.is_set():
        with suppress(Exception):
            resp = await s._get_http_client().post(
                f"{s.RELAY_URL}/heartbeat",
                json={"instance_name": s.INSTANCE_NAME, "status": "active"},
                headers=await s._auth_headers_async(), timeout=10,
            )
            resp.raise_for_status()
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            pass


async def _process_sse_event(event_type: str, data: str) -> None:
    s = _srv()
    if event_type == "message":
        msg = process_sse_event_data(data)
        if msg is not None:
            await s._append_pending_messages([msg])
            await s._notify_active_session([msg])


async def _get_sse_token() -> str:
    s = _srv()
    with suppress(Exception):
        bearer = await s._get_bearer_token()
        resp = await s._get_http_client().post(
            f"{s.RELAY_URL}/stream/token",
            json={"recipient": s.INSTANCE_NAME},
            headers={"Authorization": f"Bearer {bearer}"},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()["token"]
    return s.RELAY_SECRET


async def _sse_listener(stop_event: asyncio.Event) -> None:
    """Run the SSE listener and expose its instance for breaker diagnostics."""
    s = _srv()
    listener = SSEListener(
        relay_url=s.RELAY_URL, instance_name=s.INSTANCE_NAME,
        get_http_client=s._get_http_client, get_sse_token=s._get_sse_token,
        on_event=s._process_sse_event,
    )
    s._active_sse_listener = listener
    try:
        await listener.run(stop_event)
    finally:
        s._active_sse_listener = None


# Polling fallback: drains the relay's pull endpoint while the circuit
# breaker is tripped. Sleeps cheaply otherwise. This is automatic; there
# is no user-facing toggle (the dead poll-mode path was removed).
_FALLBACK_POLL_INTERVAL = 5
_FALLBACK_POLL_WAIT = 25


async def _polling_fallback(stop_event: asyncio.Event) -> None:
    """Poll the relay for messages while the SSE breaker is tripped."""
    s = _srv()
    while not stop_event.is_set():
        state = s._sse_breaker_state()
        if not state["tripped"]:
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=_FALLBACK_POLL_INTERVAL,
                )
                return
            except asyncio.TimeoutError:
                continue
        messages, ack_token, error = await s._fetch_relay_messages(
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
            await s._append_pending_messages(messages)
            await s._notify_active_session(messages)
        if ack_token:
            await s._ack_messages(ack_token)


@asynccontextmanager
async def _mcp_lifespan(server: "FastMCP"):
    s = _srv()
    s._http_client = httpx.AsyncClient()
    if s.API_KEY:
        try:
            await s._exchange_api_key_for_jwt()
        except Exception:
            logger.warning(
                "Initial JWT exchange failed — will retry on first request",
                exc_info=True,
            )
    stop_event = asyncio.Event()
    sse_task: asyncio.Task | None = None
    poll_task: asyncio.Task | None = None
    if s.SSE_ENABLED:
        sse_task = asyncio.create_task(s._sse_listener(stop_event))
        poll_task = asyncio.create_task(s._polling_fallback(stop_event))
        logger.info("SSE push listener started (polling fallback armed)")
    s._heartbeat_task = asyncio.create_task(s._heartbeat_loop(stop_event))
    try:
        yield {"stop_event": stop_event}
    finally:
        stop_event.set()
        for task in filter(None, [sse_task, poll_task, s._heartbeat_task]):
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if s._http_client is not None:
            await s._http_client.aclose()
        await s.reset_runtime_state_locked()
