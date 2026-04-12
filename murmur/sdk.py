"""Murmur SDK — 3 lines to add agent coordination to any project.

Usage:
    from murmur import Room

    room = Room("dev-room", relay="https://relay.example.com", api_key="mct_...", name="my-agent")
    room.send("CLAIM: auth module", type="claim")
    messages = room.receive()

Async usage:
    async with Room("dev-room", ...) as room:
        await room.send("hello")
        async for msg in room.stream():
            print(msg)
"""

import json
import logging
import threading
from typing import Callable

import httpx

from murmur.integrations.http_agent import MurmurClient

logger = logging.getLogger("murmur.sdk")


class Room:
    """High-level interface to a Murmur room.

    Designed to be as simple as possible — Stripe-like DX.
    """

    def __init__(
        self,
        room: str,
        *,
        relay: str = "http://localhost:8080",
        secret: str = "",
        api_key: str = "",
        name: str = "sdk-agent",
    ):
        self.room = room
        self.relay = relay.rstrip("/")
        self.secret = secret
        self.api_key = api_key
        self.name = name
        self._jwt: str | None = None
        if api_key:
            self._exchange_jwt()
        elif secret:
            logger.warning(
                "Using secret= for auth is deprecated. "
                "Use api_key= for production deployments."
            )
        self._client = MurmurClient(relay, secret, name, api_key=api_key)
        self._listeners: list[Callable] = []
        self._stream_task: threading.Thread | None = None
        self._stop_event = threading.Event()

    def _exchange_jwt(self) -> str:
        """Exchange api_key for a JWT, caching the result."""
        resp = httpx.post(
            f"{self.relay}/v1/auth/token",
            json={"api_key": self.api_key},
            timeout=10,
        )
        resp.raise_for_status()
        self._jwt = resp.json()["token"]
        return self._jwt

    def _get_bearer(self) -> str:
        """Return JWT if using api_key auth, otherwise secret."""
        if self.api_key:
            if not self._jwt:
                self._exchange_jwt()
            return self._jwt  # type: ignore[return-value]
        return self.secret

    def _get_auth_headers(self) -> dict[str, str]:
        """Get auth headers."""
        return {"Authorization": f"Bearer {self._get_bearer()}"}

    def _get_sse_token(self) -> str:
        """Get a short-lived SSE stream token."""
        try:
            resp = httpx.post(
                f"{self.relay}/stream/token",
                json={"recipient": self.name},
                headers=self._get_auth_headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json()["token"]
        except Exception:
            pass
        return self._get_bearer()

    def join(self) -> dict:
        """Join the room."""
        return self._client.join(self.room)

    def send(
        self,
        content: str,
        *,
        type: str = "chat",
        reply_to: str | None = None,
    ) -> dict:
        """Send a message to the room."""
        return self._client.send(
            self.room, content, msg_type=type, reply_to=reply_to,
        )

    def receive(self, wait: int = 0):
        """Get pending messages. Returns a ReceiveResult — call .ack() after processing.

        Usage::

            result = room.receive()
            for msg in result.messages:
                process(msg)
            result.ack()
        """
        return self._client.receive(wait=wait)

    def history(self, limit: int = 50) -> list[dict]:
        """Get room message history."""
        return self._client.history(self.room, limit=limit)

    def members(self) -> list[str]:
        """Get room members."""
        rooms = self._client.rooms()
        for r in rooms:
            if r["name"] == self.room or r["id"] == self.room:
                return r.get("members", [])
        return []

    def peek(self) -> int:
        """Check how many messages are waiting."""
        return self._client.peek().get("count", 0)

    def dm(self, to: str, content: str) -> dict:
        """Send a direct message to a specific agent."""
        return self._client.dm(to, content)

    def claim(self, task: str) -> dict:
        """Claim a task in the room."""
        return self.send(f"CLAIM: {task}", type="claim")

    def status(self, update: str) -> dict:
        """Post a status update."""
        return self.send(f"STATUS: {update}", type="status")

    def alert(self, message: str) -> dict:
        """Post an alert."""
        return self.send(f"ALERT: {message}", type="alert")

    def sync(self, message: str) -> dict:
        """Post a git sync message."""
        return self.send(f"SYNC: {message}", type="sync")

    def request(self, message: str) -> dict:
        """Post a request for help."""
        return self.send(f"REQUEST: {message}", type="request")

    def reply(self, message_id: str, content: str) -> dict:
        """Reply to a specific message."""
        return self.send(content, reply_to=message_id)

    def on_message(self, callback: Callable[[dict], None]) -> None:
        """Register a callback for incoming messages.

        Usage:
            room.on_message(lambda msg: print(msg["content"]))
            room.listen()  # blocks
        """
        self._listeners.append(callback)

    def listen(self, poll_interval: int = 5) -> None:
        """Block and listen for messages, calling registered callbacks.

        ACKs messages after all callbacks complete successfully.
        Uses long-polling. For SSE streaming, use stream() instead.
        """
        from murmur.integrations.http_agent import ReceiveResult

        while not self._stop_event.is_set():
            try:
                result = self.receive(wait=poll_interval)
                msgs = result.messages if isinstance(result, ReceiveResult) else result
                for msg in msgs:
                    for cb in self._listeners:
                        cb(msg)
                if isinstance(result, ReceiveResult):
                    result.ack()
            except (httpx.ConnectError, httpx.ReadTimeout):
                self._stop_event.wait(2)

    def listen_async(self, poll_interval: int = 5) -> None:
        """Start listening in a background thread."""
        self._stop_event.clear()
        self._stream_task = threading.Thread(
            target=self.listen, args=(poll_interval,), daemon=True
        )
        self._stream_task.start()

    def stop(self) -> None:
        """Stop the background listener."""
        self._stop_event.set()
        if self._stream_task:
            self._stream_task.join(timeout=10)
            self._stream_task = None

    # Async interface

    async def asend(
        self,
        content: str,
        *,
        type: str = "chat",
        reply_to: str | None = None,
    ) -> dict:
        """Async send."""
        body: dict = {
            "from_name": self.name,
            "content": content,
            "message_type": type,
        }
        if reply_to:
            body["reply_to"] = reply_to
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self.relay}/rooms/{self.room}/messages",
                json=body,
                headers=self._get_auth_headers(),
            )
            r.raise_for_status()
            return r.json()

    async def areceive(self, wait: int = 0):
        """Async receive. Returns messages and ack_token for deferred ACK.

        Call ``await room.a_ack(ack_token)`` after processing.
        """
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self.relay}/messages/{self.name}",
                params={"wait": wait, "ack": "manual"},
                headers=self._get_auth_headers(),
            )
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list):
                messages = data
                ack_token = ""
            else:
                messages = data.get("messages", [])
                ack_token = data.get("ack_token", "")
            return {"messages": messages, "ack_token": ack_token}

    async def a_ack(self, ack_token: str) -> None:
        """Acknowledge messages from a previous areceive() call."""
        if not ack_token:
            return
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{self.relay}/messages/{self.name}/ack",
                json={"ack_token": ack_token},
                headers=self._get_auth_headers(),
                timeout=5,
            )

    async def astream(self):
        """Async generator that yields messages via SSE.

        Usage:
            async for msg in room.astream():
                print(msg["from_name"], msg["content"])
        """
        sse_token = self._get_sse_token()
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "GET",
                f"{self.relay}/stream/{self.name}",
                params={"token": sse_token},
                timeout=None,
            ) as resp:
                event_type = ""
                event_data = ""
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        event_data = line[5:].strip()
                    elif line == "" and event_type == "message":
                        try:
                            yield json.loads(event_data)
                        except json.JSONDecodeError:
                            pass
                        event_type = ""
                        event_data = ""

    # Context manager

    async def __aenter__(self):
        self.join()
        return self

    async def __aexit__(self, *args):
        self.stop()

    def __enter__(self):
        self.join()
        return self

    def __exit__(self, *args):
        self.stop()
