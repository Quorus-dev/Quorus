"""Universal Murmur client — works with any agent platform via HTTP.

Usage:
    from murmur.integrations.http_agent import MurmurClient

    client = MurmurClient("https://your-relay.example.com", "secret", "my-agent")
    client.join("dev-room")
    client.send("dev-room", "Hello from any agent!")
    messages = client.receive()
    history = client.history("dev-room", limit=20)
"""

import time

import httpx


class MurmurClient:
    """Lightweight HTTP client for Murmur relay. No MCP dependency."""

    def __init__(
        self,
        relay_url: str,
        secret: str,
        name: str,
        timeout: float = 10.0,
        retries: int = 3,
    ):
        self.relay_url = relay_url.rstrip("/")
        self.name = name
        self._headers = {"Authorization": f"Bearer {secret}"}
        self._timeout = timeout
        self._retries = retries

    def _request(self, method: str, url: str, **kwargs):
        """Make HTTP request with retry on transient errors."""
        kwargs.setdefault("timeout", self._timeout)
        kwargs.setdefault("headers", self._headers)
        last_err = None
        for attempt in range(self._retries):
            try:
                r = httpx.request(method, url, **kwargs)
                r.raise_for_status()
                return r
            except (httpx.ConnectError, httpx.ReadTimeout) as e:
                last_err = e
                if attempt < self._retries - 1:
                    time.sleep(min(2 ** attempt, 5))
        raise last_err  # type: ignore[misc]

    def join(self, room: str) -> dict:
        """Join a room by name."""
        r = self._request(
            "POST", f"{self.relay_url}/rooms/{room}/join",
            json={"participant": self.name},
        )
        return r.json()

    def send(self, room: str, content: str, msg_type: str = "chat") -> dict:
        """Send a message to a room."""
        r = self._request(
            "POST", f"{self.relay_url}/rooms/{room}/messages",
            json={"from_name": self.name, "content": content,
                  "message_type": msg_type},
        )
        return r.json()

    def receive(self, wait: int = 0) -> list[dict]:
        """Fetch pending messages for this agent."""
        r = self._request(
            "GET", f"{self.relay_url}/messages/{self.name}",
            params={"wait": wait},
        )
        return r.json()

    def peek(self) -> dict:
        """Check pending message count without consuming them."""
        r = self._request(
            "GET", f"{self.relay_url}/messages/{self.name}/peek",
        )
        return r.json()

    def history(self, room: str, limit: int = 50) -> list[dict]:
        """Get room message history."""
        r = self._request(
            "GET", f"{self.relay_url}/rooms/{room}/history",
            params={"limit": limit},
        )
        return r.json()

    def rooms(self) -> list[dict]:
        """List all rooms."""
        r = self._request("GET", f"{self.relay_url}/rooms")
        return r.json()

    def dm(self, to: str, content: str) -> dict:
        """Send a direct message."""
        r = self._request(
            "POST", f"{self.relay_url}/messages",
            json={"from_name": self.name, "to": to, "content": content},
        )
        return r.json()
