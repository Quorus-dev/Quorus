"""MCP tool registrations for the Phase-1 primitives + approval bridge.

Split out of ``server.py`` to keep that module under the repo's 500-line
cap. Implementations live in ``phase1_tools.py``; this file is purely the
``@mcp.tool()`` surface.
"""

from __future__ import annotations

from typing import Any

from quorus_mcp import phase1_tools as _p1
from quorus_mcp.server import mcp

# Implementations live in ``phase1_tools.py`` to keep this file readable.
# ---------------------------------------------------------------------------



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
async def approve(
    tool_name: str,
    input: Any = None,
    room_id: str = "",
) -> dict:
    """Permission-prompt bridge: ask a human in the room to allow a tool call.

    Wire this as Claude Code's ``--permission-prompt-tool
    mcp__quorus__approve`` so a headless wake that hits a permission gate
    surfaces it in chat instead of stalling invisibly. Fails closed.
    """
    return await _p1.approve(tool_name, input, room_id)


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


