import asyncio
import json
import logging
import os
import queue
import socket
import threading
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI as WebhookFastAPI
from mcp.server.fastmcp import FastMCP

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("claude_tunnel.mcp")

CONFIG_DIR = Path.home() / "claude-tunnel"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _load_config() -> dict:
    """Load config from ~/claude-tunnel/config.json, falling back to env vars."""
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

masked_secret = RELAY_SECRET[:4] + "***" if len(RELAY_SECRET) > 4 else "***"
logger.info(
    "Config loaded: relay_url=%s, instance=%s, secret=%s",
    RELAY_URL, INSTANCE_NAME, masked_secret,
)

# Queue for incoming webhook-pushed messages
_incoming_queue: queue.Queue = queue.Queue()


def _create_webhook_app() -> WebhookFastAPI:
    """Create the local webhook receiver FastAPI app."""
    webhook_app = WebhookFastAPI()

    @webhook_app.post("/incoming")
    async def incoming(request: dict):
        _incoming_queue.put(request)
        logger.info("Received push notification from %s", request.get("from_name", "unknown"))
        return {"status": "received"}

    return webhook_app


def _find_free_port() -> int:
    """Find a free port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_webhook_server() -> int:
    """Start the local webhook HTTP server in a background thread. Returns the port."""
    port = _find_free_port()
    webhook_app = _create_webhook_app()

    def run():
        uvicorn.run(webhook_app, host="127.0.0.1", port=port, log_level="warning")

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    logger.info("Webhook receiver started on 127.0.0.1:%d", port)
    return port


async def _register_webhook(port: int):
    """Register this instance's webhook with the relay."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{RELAY_URL}/webhooks",
                json={
                    "instance_name": INSTANCE_NAME,
                    "callback_url": f"http://127.0.0.1:{port}/incoming",
                },
                headers=_auth_headers(),
            )
            resp.raise_for_status()
            logger.info("Webhook registered with relay")
    except Exception:
        logger.warning("Failed to register webhook with relay - falling back to polling")


async def _deregister_webhook():
    """Deregister this instance's webhook from the relay."""
    try:
        async with httpx.AsyncClient() as client:
            await client.delete(
                f"{RELAY_URL}/webhooks/{INSTANCE_NAME}",
                headers=_auth_headers(),
            )
            logger.info("Webhook deregistered from relay")
    except Exception:
        logger.warning("Failed to deregister webhook")


_mcp_session = None  # Set when MCP server starts


async def _channel_notify(msg: dict):
    """Send a channel notification to Claude Code."""
    if _mcp_session is None:
        return
    try:
        formatted = f"[{msg.get('timestamp', '')}] {msg.get('from_name', 'unknown')}: {msg.get('content', '')}"
        await _mcp_session.send_notification(
            method="notifications/claude/channel",
            params={"channel": "claude-tunnel", "message": formatted},
        )
        logger.info("Channel notification sent for message from %s", msg.get("from_name"))
    except Exception:
        logger.warning("Failed to send channel notification")


async def _poll_incoming_queue():
    """Background task to drain the incoming queue and send channel notifications."""
    while True:
        try:
            msg = _incoming_queue.get_nowait()
            await _channel_notify(msg)
        except queue.Empty:
            await asyncio.sleep(0.1)


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
            logger.info("Sent message to %s (id: %s)", to, data["id"])
            return f"Message sent (id: {data['id']})"
    except httpx.ConnectError:
        logger.warning("Cannot reach relay at %s", RELAY_URL)
        return f"Error: Cannot reach relay server at {RELAY_URL}"
    except httpx.HTTPStatusError as e:
        logger.warning("Relay returned %d: %s", e.response.status_code, e.response.text)
        return f"Error: Relay returned {e.response.status_code}: {e.response.text}"


async def _check_messages() -> str:
    """Internal: fetch unread messages from the relay."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{RELAY_URL}/messages/{INSTANCE_NAME}?wait=30",
                headers=_auth_headers(),
                timeout=35,
            )
            resp.raise_for_status()
            messages = resp.json()
            logger.info("Received %d messages", len(messages))
            if not messages:
                return "No new messages."
            lines = []
            for msg in messages:
                lines.append(f"[{msg['timestamp']}] {msg['from_name']}: {msg['content']}")
            return "\n".join(lines)
    except httpx.ConnectError:
        logger.warning("Cannot reach relay at %s", RELAY_URL)
        return f"Error: Cannot reach relay server at {RELAY_URL}"
    except httpx.HTTPStatusError as e:
        logger.warning("Relay returned %d: %s", e.response.status_code, e.response.text)
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
            logger.info("Listed %d participants", len(participants))
            if not participants:
                return "No participants yet."
            return "Participants: " + ", ".join(participants)
    except httpx.ConnectError:
        logger.warning("Cannot reach relay at %s", RELAY_URL)
        return f"Error: Cannot reach relay server at {RELAY_URL}"
    except httpx.HTTPStatusError as e:
        logger.warning("Relay returned %d: %s", e.response.status_code, e.response.text)
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
