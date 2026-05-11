"""MCP tool implementations for Plan v8 Phase 1 OS primitives.

Phase 1 of the Quorus OS spec shipped three primitives over HTTP only:
capability discovery, tool catalog, and persistent memory (commit 850dbf9).
Without MCP wrappers the 6 vendor harnesses cannot reach them — this
module closes that gap.

Each function mirrors the pattern in ``tools.py``:

* Reads ``RELAY_URL``, ``INSTANCE_NAME``, helpers from the server module
  via :func:`_srv` (lazy import to avoid a circular dependency at load).
* Audits before mutating writes (``mutating=True, required=True``) so the
  audit ledger seals a receipt before the relay hears about the call.
* Refreshes the JWT on 401 once, then retries.
* Returns a human-readable string for the model; structured errors are
  surfaced through :func:`_relay_error_message`.

Tools added here are surfaced to FastMCP via thin ``@mcp.tool()``
forwarders in :mod:`quorus_mcp.server`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from quorus_mcp.tools import _audit_tool_call

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass


def _srv():
    """Lazy import of the server module to dodge circular imports."""
    from quorus_mcp import server as _server_module
    return _server_module


async def _request_with_refresh(
    method: str,
    url: str,
    *,
    json: Any = None,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    """POST/GET/PUT/DELETE with a single 401-refresh retry, like other tools."""
    s = _srv()
    client = s._get_http_client()
    kwargs: dict[str, Any] = {"headers": await s._auth_headers_async()}
    if json is not None:
        kwargs["json"] = json
    if params is not None:
        kwargs["params"] = params
    resp = await client.request(method, url, **kwargs)
    if resp.status_code == 401 and await s._refresh_jwt_on_401():
        kwargs["headers"] = await s._auth_headers_async()
        resp = await client.request(method, url, **kwargs)
    return resp


# ---------------------------------------------------------------------------
# Capability discovery (primitive A)
# ---------------------------------------------------------------------------


async def publish_capability(
    description: str = "",
    supported_languages: list[str] | None = None,
    supported_frameworks: list[str] | None = None,
    capabilities: list[str] | None = None,
) -> str:
    """Publish this instance's capability manifest.

    Writes ``POST /v1/capabilities/{INSTANCE_NAME}``. Other agents in the
    same tenant can then ``lookup_capability`` or ``search_capabilities``
    to find skill matches. Replaces any prior manifest for this agent.
    """
    s = _srv()
    body = {
        "description": description,
        "supported_languages": supported_languages or [],
        "supported_frameworks": supported_frameworks or [],
        "capabilities": capabilities or [],
    }
    try:
        await _audit_tool_call(
            "publish_capability",
            body,
            mutating=True,
            required=True,
        )
        url = f"{s.RELAY_URL}/v1/capabilities/{s.INSTANCE_NAME}"
        resp = await _request_with_refresh("POST", url, json=body)
        resp.raise_for_status()
        data = resp.json()
        caps = (data.get("manifest") or {}).get("capabilities") or []
        return f"OK: published manifest ({len(caps)} capabilities)"
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return s._relay_error_message(e)


async def lookup_capability(participant: str) -> str:
    """Fetch one participant's capability manifest from this tenant."""
    s = _srv()
    try:
        await _audit_tool_call(
            "lookup_capability",
            {"participant": participant},
            mutating=False,
        )
        url = f"{s.RELAY_URL}/v1/capabilities/{participant}"
        resp = await _request_with_refresh("GET", url)
        if resp.status_code == 404:
            return f"No manifest registered for {participant}."
        resp.raise_for_status()
        manifest = resp.json().get("manifest") or {}
        caps = manifest.get("capabilities") or []
        langs = manifest.get("supported_languages") or []
        desc = manifest.get("description") or ""
        lines = [f"{participant}: {desc or '(no description)'}"]
        if langs:
            lines.append(f"  languages: {', '.join(langs)}")
        if caps:
            lines.append(f"  capabilities: {', '.join(caps)}")
        return "\n".join(lines)
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return s._relay_error_message(e)


async def search_capabilities(has: str = "") -> str:
    """Find participants whose capabilities match all tokens in ``has``.

    Tokens are space- or comma-separated; substring match is
    case-insensitive. Empty ``has`` returns every manifest in the tenant.
    """
    s = _srv()
    try:
        await _audit_tool_call(
            "search_capabilities",
            {"has": has},
            mutating=False,
        )
        url = f"{s.RELAY_URL}/v1/capabilities/search"
        params = {"has": has} if has else None
        resp = await _request_with_refresh("GET", url, params=params)
        resp.raise_for_status()
        data = resp.json()
        matches = data.get("manifests") or []
        if not matches:
            return "No matching participants."
        lines = [
            f"Matches for {data.get('query') or '(any)'} ({len(matches)}):",
        ]
        for m in matches:
            caps = ", ".join(m.get("capabilities") or [])
            lines.append(f"  - {m.get('participant', '?')}: {caps}")
        return "\n".join(lines)
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return s._relay_error_message(e)


# ---------------------------------------------------------------------------
# Tool catalog (primitive B)
# ---------------------------------------------------------------------------


async def register_tool(
    room_id: str,
    name: str,
    url: str,
    access: str = "room",
    description: str = "",
) -> str:
    """Register an MCP server in the room (room admin only).

    Writes ``POST /v1/rooms/{room_id}/tools``. ``access`` is one of
    ``room`` (visible to room members) or ``public``.
    """
    s = _srv()
    body = {
        "name": name,
        "url": url,
        "access": access,
        "description": description,
    }
    try:
        await _audit_tool_call(
            "register_tool",
            {"room_id": room_id, **body},
            mutating=True,
            required=True,
        )
        endpoint = f"{s.RELAY_URL}/v1/rooms/{room_id}/tools"
        resp = await _request_with_refresh("POST", endpoint, json=body)
        resp.raise_for_status()
        record = resp.json().get("tool") or {}
        return f"OK: registered tool {record.get('name', name)!r}"
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return s._relay_error_message(e)


async def list_room_tools(room_id: str) -> str:
    """List MCP servers registered in the room (members only)."""
    s = _srv()
    try:
        await _audit_tool_call(
            "list_room_tools",
            {"room_id": room_id},
            mutating=False,
        )
        endpoint = f"{s.RELAY_URL}/v1/rooms/{room_id}/tools"
        resp = await _request_with_refresh("GET", endpoint)
        resp.raise_for_status()
        data = resp.json()
        tools = data.get("tools") or []
        if not tools:
            return f"No tools registered in {room_id}."
        lines = [f"Tools in {room_id} ({len(tools)}):"]
        for t in tools:
            desc = t.get("description") or ""
            tail = f" — {desc}" if desc else ""
            lines.append(
                f"  - {t.get('name', '?')} ({t.get('access', 'room')}): "
                f"{t.get('url', '?')}{tail}"
            )
        return "\n".join(lines)
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return s._relay_error_message(e)


async def unregister_tool(room_id: str, name: str) -> str:
    """Remove an MCP server registration (room admin only)."""
    s = _srv()
    try:
        await _audit_tool_call(
            "unregister_tool",
            {"room_id": room_id, "name": name},
            mutating=True,
            required=True,
        )
        endpoint = f"{s.RELAY_URL}/v1/rooms/{room_id}/tools/{name}"
        resp = await _request_with_refresh("DELETE", endpoint)
        if resp.status_code == 404:
            return f"No tool {name!r} in {room_id}."
        resp.raise_for_status()
        return f"OK: removed {name!r} from {room_id}"
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return s._relay_error_message(e)


# ---------------------------------------------------------------------------
# Persistent memory (primitive C)
# ---------------------------------------------------------------------------


async def memory_set(
    room_id: str,
    key: str,
    value: Any,
    visibility: str = "private",
) -> str:
    """Store a value in this agent's persistent memory for ``room_id``.

    ``visibility`` is one of ``private`` (owner-only), ``room`` (any
    room member), or ``public`` (any tenant member). Server caps value
    size at 16 KiB and entries per (participant, room) at 1024.
    """
    s = _srv()
    body = {"value": value, "visibility": visibility}
    try:
        await _audit_tool_call(
            "memory_set",
            {
                "room_id": room_id,
                "key": key,
                "visibility": visibility,
            },
            mutating=True,
            required=True,
        )
        endpoint = (
            f"{s.RELAY_URL}/v1/memory/{s.INSTANCE_NAME}/{room_id}/{key}"
        )
        resp = await _request_with_refresh("PUT", endpoint, json=body)
        resp.raise_for_status()
        entry = resp.json().get("entry") or {}
        size = entry.get("size_bytes", "?")
        return f"OK: stored {key!r} ({size} bytes, visibility={visibility})"
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return s._relay_error_message(e)


async def memory_get(
    room_id: str,
    key: str,
    owner: str | None = None,
) -> str:
    """Read a memory entry. ``owner`` defaults to this instance."""
    s = _srv()
    who = owner or s.INSTANCE_NAME
    try:
        await _audit_tool_call(
            "memory_get",
            {"room_id": room_id, "key": key, "owner": who},
            mutating=False,
        )
        endpoint = f"{s.RELAY_URL}/v1/memory/{who}/{room_id}/{key}"
        resp = await _request_with_refresh("GET", endpoint)
        if resp.status_code == 404:
            return f"No entry for {key!r}."
        resp.raise_for_status()
        entry = resp.json().get("entry") or {}
        vis = entry.get("visibility", "?")
        return (
            f"{key} (visibility={vis}, owner={entry.get('owner', who)}): "
            f"{entry.get('value')}"
        )
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return s._relay_error_message(e)


async def memory_list(
    room_id: str,
    owner: str | None = None,
) -> str:
    """List memory entries for ``owner`` in ``room_id`` (visibility-filtered)."""
    s = _srv()
    who = owner or s.INSTANCE_NAME
    try:
        await _audit_tool_call(
            "memory_list",
            {"room_id": room_id, "owner": who},
            mutating=False,
        )
        endpoint = f"{s.RELAY_URL}/v1/memory/{who}/{room_id}"
        resp = await _request_with_refresh("GET", endpoint)
        resp.raise_for_status()
        data = resp.json()
        entries = data.get("entries") or []
        if not entries:
            return f"No memory entries for {who} in {room_id}."
        lines = [f"Memory for {who} in {room_id} ({len(entries)}):"]
        for e in entries:
            lines.append(
                f"  - {e.get('key', '?')} "
                f"(visibility={e.get('visibility', '?')}, "
                f"{e.get('size_bytes', '?')} bytes)"
            )
        return "\n".join(lines)
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return s._relay_error_message(e)


async def memory_delete(room_id: str, key: str) -> str:
    """Delete this agent's memory entry under ``key`` (GDPR-compliant)."""
    s = _srv()
    try:
        await _audit_tool_call(
            "memory_delete",
            {"room_id": room_id, "key": key},
            mutating=True,
            required=True,
        )
        endpoint = (
            f"{s.RELAY_URL}/v1/memory/{s.INSTANCE_NAME}/{room_id}/{key}"
        )
        resp = await _request_with_refresh("DELETE", endpoint)
        if resp.status_code == 404:
            return f"No entry for {key!r}."
        resp.raise_for_status()
        return f"OK: deleted {key!r}"
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return s._relay_error_message(e)


__all__ = [
    "publish_capability",
    "lookup_capability",
    "search_capabilities",
    "register_tool",
    "list_room_tools",
    "unregister_tool",
    "memory_set",
    "memory_get",
    "memory_list",
    "memory_delete",
]
