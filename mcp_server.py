import os

import httpx
from mcp.server.fastmcp import FastMCP

RELAY_URL = os.environ.get("RELAY_URL", "http://localhost:8080")
RELAY_SECRET = os.environ.get("RELAY_SECRET", "test-secret")
INSTANCE_NAME = os.environ.get("INSTANCE_NAME", "default")

mcp = FastMCP("claude-tunnel")


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {RELAY_SECRET}"}


async def _send_message(to: str, content: str) -> str:
    """Internal: send a message via the relay."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{RELAY_URL}/messages",
                json={"from_name": INSTANCE_NAME, "to": to, "content": content},
                headers=_auth_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return f"Message sent (id: {data['id']})"
    except httpx.ConnectError:
        return f"Error: Cannot reach relay server at {RELAY_URL}"
    except httpx.HTTPStatusError as e:
        return f"Error: Relay returned {e.response.status_code}: {e.response.text}"


async def _check_messages() -> str:
    """Internal: fetch unread messages from the relay."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{RELAY_URL}/messages/{INSTANCE_NAME}",
                headers=_auth_headers(),
            )
            resp.raise_for_status()
            messages = resp.json()
            if not messages:
                return "No new messages."
            lines = []
            for msg in messages:
                lines.append(f"[{msg['timestamp']}] {msg['from_name']}: {msg['content']}")
            return "\n".join(lines)
    except httpx.ConnectError:
        return f"Error: Cannot reach relay server at {RELAY_URL}"
    except httpx.HTTPStatusError as e:
        return f"Error: Relay returned {e.response.status_code}: {e.response.text}"


async def _list_participants() -> str:
    """Internal: list all known participants."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{RELAY_URL}/participants",
                headers=_auth_headers(),
            )
            resp.raise_for_status()
            participants = resp.json()
            if not participants:
                return "No participants yet."
            return "Participants: " + ", ".join(participants)
    except httpx.ConnectError:
        return f"Error: Cannot reach relay server at {RELAY_URL}"
    except httpx.HTTPStatusError as e:
        return f"Error: Relay returned {e.response.status_code}: {e.response.text}"


@mcp.tool()
async def send_message(to: str, content: str) -> str:
    """Send a message to another Claude Code instance.

    Args:
        to: The name of the recipient instance (e.g., "bob")
        content: The message content to send
    """
    return await _send_message(to, content)


@mcp.tool()
async def check_messages() -> str:
    """Check for new messages sent to this instance. Returns all unread messages and clears them."""
    return await _check_messages()


@mcp.tool()
async def list_participants() -> str:
    """List all known participants who have sent messages through the relay."""
    return await _list_participants()


if __name__ == "__main__":
    mcp.run(transport="stdio")
