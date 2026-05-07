"""MCP tool implementations for the Quorus relay.

Extracted from ``server.py`` to keep that module under the 500-line cap.
Each function here is a plain coroutine; the ``@mcp.tool()``-decorated
shells live in ``server.py`` and forward to these helpers. Module state
(``RELAY_URL``, ``INSTANCE_NAME``, ``_get_http_client``, ``_auth_headers``,
``_refresh_jwt_on_401``) is read lazily from ``quorus_mcp.server`` so that
tests which monkeypatch ``quorus.mcp_server`` attributes still work — the
two module names alias to the same object.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context

_AUDIT_TIMEOUT_SECONDS = 3.0


def _srv():
    """Import the server module lazily to avoid a circular import."""
    from quorus_mcp import server as _server_module
    return _server_module


async def _audit_tool_call(
    tool_name: str,
    arguments: dict,
    *,
    mutating: bool,
    required: bool = False,
) -> None:
    """Ask the relay to seal an audit receipt before executing an MCP tool."""
    s = _srv()
    client = s._get_http_client()
    url = f"{s.RELAY_URL}/v1/audit/mcp-tool-call"
    payload = {
        "tool_name": tool_name,
        "arguments": arguments,
        "mutating": mutating,
    }
    try:
        resp = await client.post(
            url,
            json=payload,
            headers=await s._auth_headers_async(),
            timeout=_AUDIT_TIMEOUT_SECONDS,
        )
    except httpx.RequestError:
        if required:
            raise
        return
    if resp.status_code == 401 and await s._refresh_jwt_on_401():
        try:
            resp = await client.post(
                url,
                json=payload,
                headers=await s._auth_headers_async(),
                timeout=_AUDIT_TIMEOUT_SECONDS,
            )
        except httpx.RequestError:
            if required:
                raise
            return
    if resp.status_code in {404, 501} and not required:
        return
    resp.raise_for_status()


async def send_message(
    to: str, content: str, context: "Context | None" = None,
) -> str:
    s = _srv()
    await s._remember_session(context)
    try:
        await _audit_tool_call(
            "send_message",
            {"to": to, "content": content},
            mutating=True,
            required=True,
        )
        resp = await s._get_http_client().post(
            f"{s.RELAY_URL}/messages",
            json={"from_name": s.INSTANCE_NAME, "to": to, "content": content},
            headers=await s._auth_headers_async(),
        )
        resp.raise_for_status()
        data = resp.json()
        return f"Message sent (id: {data['id']})"
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return s._relay_error_message(e)


async def check_messages(context: "Context | None" = None) -> str:
    s = _srv()
    await s._remember_session(context)
    try:
        await _audit_tool_call("check_messages", {}, mutating=True, required=True)
        await s._drain_pending_messages()
        messages, ack_token, error = await s._fetch_relay_messages(wait=0)
        if error:
            return error
        valid = [
            m for m in messages
            if m.get("from_name") or m.get("content") or m.get("timestamp")
        ]
        if not valid:
            return "No new messages."
        result = "\n".join(s._format_message(m) for m in valid)
        if ack_token:
            await s._ack_messages(ack_token)
        return result
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return s._relay_error_message(e)


async def list_participants(context: "Context | None" = None) -> str:
    s = _srv()
    await s._remember_session(context)
    try:
        await _audit_tool_call("list_participants", {}, mutating=False)
        resp = await s._get_http_client().get(
            f"{s.RELAY_URL}/participants", headers=await s._auth_headers_async(),
        )
        resp.raise_for_status()
        ps = resp.json()
        if not ps:
            return "No participants yet."
        return "Participants: " + ", ".join(ps)
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return s._relay_error_message(e)


async def send_room_message(
    room_id: str,
    content: str,
    message_type: str = "chat",
    context: "Context | None" = None,
) -> str:
    s = _srv()
    await s._remember_session(context)
    try:
        await _audit_tool_call(
            "send_room_message",
            {
                "room_id": room_id,
                "content": content,
                "message_type": message_type,
            },
            mutating=True,
            required=True,
        )
        resp = await s._get_http_client().post(
            f"{s.RELAY_URL}/rooms/{room_id}/messages",
            json={
                "from_name": s.INSTANCE_NAME,
                "content": content,
                "message_type": message_type,
            },
            headers=await s._auth_headers_async(),
        )
        resp.raise_for_status()
        return f"Room message sent (id: {resp.json()['id']})"
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return s._relay_error_message(e)


async def join_room(room_id: str, context: "Context | None" = None) -> str:
    s = _srv()
    await s._remember_session(context)
    try:
        await _audit_tool_call(
            "join_room",
            {"room_id": room_id},
            mutating=True,
            required=True,
        )
        resp = await s._get_http_client().post(
            f"{s.RELAY_URL}/rooms/{room_id}/join",
            json={"participant": s.INSTANCE_NAME},
            headers=await s._auth_headers_async(),
        )
        resp.raise_for_status()
        return f"Joined room {room_id}"
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return s._relay_error_message(e)


async def list_rooms(context: "Context | None" = None) -> str:
    s = _srv()
    await s._remember_session(context)
    try:
        await _audit_tool_call("list_rooms", {}, mutating=False)
        resp = await s._get_http_client().get(
            f"{s.RELAY_URL}/rooms", headers=await s._auth_headers_async(),
        )
        resp.raise_for_status()
        rooms_list = resp.json()
        if not rooms_list:
            return "No rooms yet."
        return "Rooms:\n" + "\n".join(
            f"  {r['name']} (id: {r['id']}) — "
            f"members: {', '.join(r['members'])}"
            for r in rooms_list
        )
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return s._relay_error_message(e)


async def search_room(
    room_id: str,
    q: str = "",
    sender: str = "",
    message_type: str = "",
    limit: int = 50,
) -> str:
    s = _srv()
    try:
        await _audit_tool_call(
            "search_room",
            {
                "room_id": room_id,
                "q": q,
                "sender": sender,
                "message_type": message_type,
                "limit": limit,
            },
            mutating=False,
        )
    except (httpx.HTTPStatusError, httpx.RequestError):
        pass
    params: dict[str, str | int] = {"limit": limit}
    if q:
        params["q"] = q
    if sender:
        params["sender"] = sender
    if message_type:
        params["message_type"] = message_type
    resp = await s._get_http_client().get(
        f"{s.RELAY_URL}/rooms/{room_id}/search",
        params=params, headers=await s._auth_headers_async(), timeout=10,
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
        lines.append(
            f"[{ts}] {msg.get('from_name', '?')}{tag}: {msg.get('content', '')}"
        )
    return "\n".join(lines)


async def room_metrics(room_id: str) -> str:
    s = _srv()
    try:
        await _audit_tool_call(
            "room_metrics",
            {"room_id": room_id},
            mutating=False,
        )
    except (httpx.HTTPStatusError, httpx.RequestError):
        pass
    resp = await s._get_http_client().get(
        f"{s.RELAY_URL}/rooms/{room_id}/history",
        params={"limit": 200}, headers=await s._auth_headers_async(), timeout=10,
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
    lines += [
        f"  {n}: {c}"
        for n, c in sorted(agent_counts.items(), key=lambda x: x[1], reverse=True)
    ]
    lines += ["", "Message types:"]
    lines += [
        f"  {t}: {c}"
        for t, c in sorted(type_counts.items(), key=lambda x: x[1], reverse=True)
    ]
    if claims > 0:
        pct = min(completions / claims * 100, 100)
        lines.append(f"\nTask completion: {completions}/{claims} ({pct:.0f}%)")
    return "\n".join(lines)


async def claim_task(
    room_id: str,
    file_path: str,
    description: str = "",
    ttl_seconds: int = 300,
) -> str:
    s = _srv()
    try:
        client = s._get_http_client()
        payload = {
            "file_path": file_path,
            "claimed_by": s.INSTANCE_NAME,
            "description": description,
            "ttl_seconds": ttl_seconds,
        }
        await _audit_tool_call(
            "claim_task",
            payload,
            mutating=True,
            required=True,
        )
        url = f"{s.RELAY_URL}/rooms/{room_id}/lock"
        resp = await client.post(
            url, json=payload, headers=await s._auth_headers_async(), timeout=10,
        )
        if resp.status_code == 401 and await s._refresh_jwt_on_401():
            resp = await client.post(
                url, json=payload,
                headers=await s._auth_headers_async(),
                timeout=10,
            )
        resp.raise_for_status()
        data = resp.json()
        if data.get("locked"):
            return (
                f"LOCKED: {file_path} is held by {data['held_by']}, "
                f"expires {data['expires_at']}"
            )
        return (
            f"GRANTED: lock_token={data['lock_token']} "
            f"expires={data['expires_at']}"
        )
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return s._relay_error_message(e)


async def release_task(room_id: str, file_path: str, lock_token: str) -> str:
    s = _srv()
    try:
        client = s._get_http_client()
        url = f"{s.RELAY_URL}/rooms/{room_id}/lock/{file_path}"
        payload = {"lock_token": lock_token}
        await _audit_tool_call(
            "release_task",
            {"room_id": room_id, "file_path": file_path},
            mutating=True,
            required=True,
        )
        resp = await client.request(
            "DELETE", url, json=payload,
            headers=await s._auth_headers_async(),
            timeout=10,
        )
        if resp.status_code == 401 and await s._refresh_jwt_on_401():
            resp = await client.request(
                "DELETE", url, json=payload,
                headers=await s._auth_headers_async(), timeout=10,
            )
        resp.raise_for_status()
        return f"RELEASED: {file_path}"
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return s._relay_error_message(e)


async def get_room_state(room_id: str) -> str:
    s = _srv()
    try:
        await _audit_tool_call(
            "get_room_state",
            {"room_id": room_id},
            mutating=False,
        )
        client = s._get_http_client()
        state_url = f"{s.RELAY_URL}/rooms/{room_id}/state"
        resp = await client.get(
            state_url, headers=await s._auth_headers_async(), timeout=10,
        )
        if resp.status_code == 401 and await s._refresh_jwt_on_401():
            resp = await client.get(
                state_url, headers=await s._auth_headers_async(), timeout=10,
            )
        resp.raise_for_status()
        data = resp.json()
        lines = [
            f"Room: {room_id} (snapshot: {data.get('snapshot_at', '')[:19]})",
            f"Schema: {data.get('schema_version', '?')}",
            f"Goal: {data.get('active_goal') or '(none)'}",
            f"Active agents ({len(data.get('active_agents', []))}): "
            + ", ".join(data.get("active_agents", [])),
            f"Messages: {data.get('message_count', 0)} | "
            f"Last activity: {(data.get('last_activity') or '')[:19]}",
        ]
        if data.get("claimed_tasks"):
            lines.append(f"\nClaimed tasks ({len(data['claimed_tasks'])}):")
            for t in data["claimed_tasks"]:
                lines.append(
                    f"  [{t['claimed_by']}] {t['file_path']} "
                    f"(expires {t.get('expires_at', '')[:19]})"
                )
        if data.get("locked_files"):
            lines.append(f"\nLocked files ({len(data['locked_files'])}):")
            for fp, lock in data["locked_files"].items():
                lines.append(f"  {fp} → held by {lock['held_by']}")
        if data.get("resolved_decisions"):
            lines.append(f"\nDecisions ({len(data['resolved_decisions'])}):")
            for d in data["resolved_decisions"][-5:]:
                lines.append(f"  [{d.get('decided_by', '?')}] {d['decision']}")
        return "\n".join(lines)
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return s._relay_error_message(e)


async def social_verb(
    verb: str,
    room_id: str,
    payload: dict,
    ref_message_id: str | None = None,
    context: "Context | None" = None,
) -> str:
    """POST a Quorus Social Protocol v1 verb to ``/v1/social/{verb}``.

    Submits as the configured ``INSTANCE_NAME``. Returns a human-readable
    confirmation string for the model.
    """
    s = _srv()
    await s._remember_session(context)
    body = {
        "actor": s.INSTANCE_NAME,
        "room_id": room_id,
        "ref_message_id": ref_message_id,
        "payload": payload,
    }
    try:
        await _audit_tool_call(
            "social_verb",
            {
                "verb": verb,
                "room_id": room_id,
                "payload": payload,
                "ref_message_id": ref_message_id,
            },
            mutating=True,
        )
        client = s._get_http_client()
        resp = await client.post(
            f"{s.RELAY_URL}/v1/social/{verb}",
            json=body,
            headers=await s._auth_headers_async(),
        )
        if resp.status_code == 401 and await s._refresh_jwt_on_401():
            resp = await client.post(
                f"{s.RELAY_URL}/v1/social/{verb}",
                json=body,
                headers=await s._auth_headers_async(),
            )
        resp.raise_for_status()
        return f"OK: {verb} accepted"
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return s._relay_error_message(e)
