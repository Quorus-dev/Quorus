"""Universal Murmur client — works with any agent platform via HTTP.

Usage:
    from murmur.integrations.http_agent import MurmurClient

    client = MurmurClient("https://your-relay.example.com", "secret", "my-agent")
    client.join("dev-room")
    client.send("dev-room", "Hello from any agent!")
    messages = client.receive()
    history = client.history("dev-room", limit=20)
"""

import httpx


class MurmurClient:
    """Lightweight HTTP client for Murmur relay. No MCP dependency."""

    def __init__(
        self, relay_url: str, secret: str, name: str, timeout: float = 10.0
    ):
        self.relay_url = relay_url.rstrip("/")
        self.name = name
        self._headers = {"Authorization": f"Bearer {secret}"}
        self._timeout = timeout

    def join(self, room: str) -> dict:
        """Join a room by name."""
        r = httpx.post(
            f"{self.relay_url}/rooms/{room}/join",
            json={"participant": self.name},
            headers=self._headers,
            timeout=self._timeout,
        )
        r.raise_for_status()
        return r.json()

    def send(self, room: str, content: str, message_type: str = "chat") -> dict:
        """Send a message to a room."""
        r = httpx.post(
            f"{self.relay_url}/rooms/{room}/messages",
            json={"from_name": self.name, "content": content, "message_type": message_type},
            headers=self._headers,
            timeout=self._timeout,
        )
        r.raise_for_status()
        return r.json()

    def receive(self, wait: int = 0) -> list[dict]:
        """Fetch pending messages for this agent."""
        r = httpx.get(
            f"{self.relay_url}/messages/{self.name}",
            params={"wait": wait},
            headers=self._headers,
            timeout=self._timeout,
        )
        r.raise_for_status()
        return r.json()

    def peek(self) -> dict:
        """Check pending message count without consuming them."""
        r = httpx.get(
            f"{self.relay_url}/messages/{self.name}/peek",
            headers=self._headers,
            timeout=self._timeout,
        )
        r.raise_for_status()
        return r.json()

    def history(self, room: str, limit: int = 50) -> list[dict]:
        """Get room message history."""
        r = httpx.get(
            f"{self.relay_url}/rooms/{room}/history",
            params={"limit": limit},
            headers=self._headers,
            timeout=self._timeout,
        )
        r.raise_for_status()
        return r.json()

    def rooms(self) -> list[dict]:
        """List all rooms."""
        r = httpx.get(f"{self.relay_url}/rooms", headers=self._headers)
        r.raise_for_status()
        return r.json()

    def dm(self, to: str, content: str) -> dict:
        """Send a direct message."""
        r = httpx.post(
            f"{self.relay_url}/messages",
            json={"from_name": self.name, "to": to, "content": content},
            headers=self._headers,
            timeout=self._timeout,
        )
        r.raise_for_status()
        return r.json()
