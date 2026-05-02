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


def _srv():
    """Import the server module lazily to avoid a circular import."""
    from quorus_mcp import server as _server_module
    return _server_module


async def send_message(
    to: str, content: str, context: "Context | None" = None,
) -> str:
    s = _srv()
    await s._remember_session(context)
    try:
        resp = await s._get_http_client().post(
            f"{s.RELAY_URL}/messages",
            json={"from_name": s.INSTANCE_NAME, "to": to, "content": content},
            headers=s._auth_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        return f"Message sent (id: {data['id']})"
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return s._relay_error_message(e)


async def check_messages(context: "Context | None" = None) -> str:
    s = _srv()
    await s._remember_session(context)
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


async def list_participants(context: "Context | None" = None) -> str:
    s = _srv()
    await s._remember_session(context)
    try:
        resp = await s._get_http_client().get(
            f"{s.RELAY_URL}/participants", headers=s._auth_headers(),
        )
        resp.raise_for_status()
        ps = resp.json()
        if not ps:
            return "No participants yet."
        return "Participants: " + ", ".join(ps)
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
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
        resp = await s._get_http_client().post(
            f"{s.RELAY_URL}/rooms/{room_id}/messages",
            json={
                "from_name": s.INSTANCE_NAME,
                "content": content,
                "message_type": message_type,
            },
            headers=s._auth_headers(),
        )
        resp.raise_for_status()
        return f"Room message sent (id: {resp.json()['id']})"
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return s._relay_error_message(e)


async def join_room(room_id: str, context: "Context | None" = None) -> str:
    s = _srv()
    await s._remember_session(context)
    try:
        resp = await s._get_http_client().post(
            f"{s.RELAY_URL}/rooms/{room_id}/join",
            json={"participant": s.INSTANCE_NAME}, headers=s._auth_headers(),
        )
        resp.raise_for_status()
        return f"Joined room {room_id}"
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return s._relay_error_message(e)


async def list_rooms(context: "Context | None" = None) -> str:
    s = _srv()
    await s._remember_session(context)
    try:
        resp = await s._get_http_client().get(
            f"{s.RELAY_URL}/rooms", headers=s._auth_headers(),
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
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return s._relay_error_message(e)


async def search_room(
    room_id: str,
    q: str = "",
    sender: str = "",
    message_type: str = "",
    limit: int = 50,
) -> str:
    s = _srv()
    params: dict[str, str | int] = {"limit": limit}
    if q:
        params["q"] = q
    if sender:
        params["sender"] = sender
    if message_type:
        params["message_type"] = message_type
    resp = await s._get_http_client().get(
        f"{s.RELAY_URL}/rooms/{room_id}/search",
        params=params, headers=s._auth_headers(), timeout=10,
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
    resp = await s._get_http_client().get(
        f"{s.RELAY_URL}/rooms/{room_id}/history",
        params={"limit": 200}, headers=s._auth_headers(), timeout=10,
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
        url = f"{s.RELAY_URL}/rooms/{room_id}/lock"
        resp = await client.post(
            url, json=payload, headers=s._auth_headers(), timeout=10,
        )
        if resp.status_code == 401 and await s._refresh_jwt_on_401():
            resp = await client.post(
                url, json=payload, headers=s._auth_headers(), timeout=10,
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
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return s._relay_error_message(e)


async def release_task(room_id: str, file_path: str, lock_token: str) -> str:
    s = _srv()
    try:
        client = s._get_http_client()
        url = f"{s.RELAY_URL}/rooms/{room_id}/lock/{file_path}"
        payload = {"lock_token": lock_token}
        resp = await client.request(
            "DELETE", url, json=payload, headers=s._auth_headers(), timeout=10,
        )
        if resp.status_code == 401 and await s._refresh_jwt_on_401():
            resp = await client.request(
                "DELETE", url, json=payload,
                headers=s._auth_headers(), timeout=10,
            )
        resp.raise_for_status()
        return f"RELEASED: {file_path}"
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return s._relay_error_message(e)


async def get_room_state(room_id: str) -> str:
    s = _srv()
    try:
        client = s._get_http_client()
        state_url = f"{s.RELAY_URL}/rooms/{room_id}/state"
        resp = await client.get(
            state_url, headers=s._auth_headers(), timeout=10,
        )
        if resp.status_code == 401 and await s._refresh_jwt_on_401():
            resp = await client.get(
                state_url, headers=s._auth_headers(), timeout=10,
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
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return s._relay_error_message(e)
