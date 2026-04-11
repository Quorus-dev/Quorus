"""Universal Murmur client — works with any agent platform via HTTP.

Usage:
    from murmur.integrations.http_agent import MurmurClient

    client = MurmurClient("https://your-relay.example.com", name="my-agent", api_key="mct_...")
    client.join("dev-room")
    client.send("dev-room", "Hello from any agent!")
    messages = client.receive()
    history = client.history("dev-room", limit=20)
"""

import logging
import time

import httpx

logger = logging.getLogger("murmur.http_agent")


class MurmurClient:
    """Lightweight HTTP client for Murmur relay. No MCP dependency."""

    def __init__(
        self,
        relay_url: str,
        secret: str = "",
        name: str = "",
        timeout: float = 10.0,
        retries: int = 3,
        api_key: str = "",
    ):
        self.relay_url = relay_url.rstrip("/")
        self.name = name
        self._secret = secret
        self._api_key = api_key
        self._jwt: str | None = None
        self._timeout = timeout
        self._retries = retries
        if api_key:
            self._exchange_jwt()
        elif secret:
            logger.warning(
                "Using RELAY_SECRET for auth is deprecated. "
                "Use api_key for production deployments."
            )
        self._headers = {"Authorization": f"Bearer {self._get_bearer()}"}

    def _exchange_jwt(self) -> str:
        """Exchange api_key for a JWT, caching the result."""
        resp = httpx.post(
            f"{self.relay_url}/v1/auth/token",
            json={"api_key": self._api_key},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        self._jwt = resp.json()["token"]
        return self._jwt

    def _get_bearer(self) -> str:
        """Return JWT if using api_key auth, otherwise secret."""
        if self._api_key:
            if not self._jwt:
                self._exchange_jwt()
            return self._jwt  # type: ignore[return-value]
        return self._secret

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

    def send(
        self,
        room: str,
        content: str,
        msg_type: str = "chat",
        reply_to: str | None = None,
    ) -> dict:
        """Send a message to a room."""
        body: dict = {
            "from_name": self.name,
            "content": content,
            "message_type": msg_type,
        }
        if reply_to:
            body["reply_to"] = reply_to
        r = self._request(
            "POST", f"{self.relay_url}/rooms/{room}/messages",
            json=body,
        )
        return r.json()

    def receive(self, wait: int = 0) -> list[dict]:
        """Fetch pending messages with client-side ACK."""
        r = self._request(
            "GET", f"{self.relay_url}/messages/{self.name}",
            params={"wait": wait, "ack": "manual"},
        )
        data = r.json()
        if isinstance(data, list):
            messages = data
            ack_token = ""
        else:
            messages = data.get("messages", [])
            ack_token = data.get("ack_token", "")
        if ack_token and messages:
            try:
                self._request(
                    "POST", f"{self.relay_url}/messages/{self.name}/ack",
                    json={"ack_token": ack_token},
                )
            except Exception:
                logger.warning("Failed to ACK messages")
        return messages

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
