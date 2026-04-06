import json
import os
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

CONFIG_FILE = Path.home() / ".claude-tunnel.json"


def _load_config() -> dict:
    """Load config from ~/.claude-tunnel.json, falling back to env vars."""
    file_config = {}
    if CONFIG_FILE.exists():
        try:
            file_config = json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    def get(env_key: str, file_key: str, default: str) -> str:
        return os.environ.get(env_key) or file_config.get(file_key, default)

    return {
        "relay_url": get("RELAY_URL", "relay_url", "http://localhost:8080"),
        "relay_secret": get("RELAY_SECRET", "relay_secret", "test-secret"),
        "instance_name": get("INSTANCE_NAME", "instance_name", "default"),
    }


_config = _load_config()
RELAY_URL = _config["relay_url"]
RELAY_SECRET = _config["relay_secret"]
INSTANCE_NAME = _config["instance_name"]

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
