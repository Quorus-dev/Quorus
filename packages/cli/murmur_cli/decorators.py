"""Decorator-based API for building Murmur-connected agents.

Usage:
    import murmur.decorators as murmur

    agent = murmur.Agent("https://relay.example.com", "secret", "my-agent")

    @agent.on_message("dev-room")
    def handle(msg):
        print(f"{msg['from_name']}: {msg['content']}")
        agent.send("dev-room", "got it!")

    @agent.on_message("dev-room", type="claim")
    def handle_claims(msg):
        print(f"New claim: {msg['content']}")

    @agent.heartbeat("dev-room", interval=30)
    def status():
        return "idle"

    agent.run()  # blocks, polls + heartbeats forever
"""

import threading
import time
from typing import Callable

from murmur_sdk.http_agent import MurmurClient


class Agent:
    """Decorator-based agent that auto-polls and dispatches messages."""

    def __init__(
        self,
        relay_url: str,
        secret: str,
        name: str,
        poll_interval: float = 2.0,
    ):
        self.client = MurmurClient(relay_url, secret, name)
        self.name = name
        self._poll_interval = poll_interval
        self._handlers: list[tuple[str, str | None, Callable]] = []
        self._heartbeats: list[tuple[str, float, Callable]] = []
        self._claims: dict[str, str] = {}
        self._running = False
        self._joined_rooms: set[str] = set()

    def send(self, room: str, content: str, msg_type: str = "chat") -> dict:
        """Send a message to a room."""
        return self.client.send(room, content, msg_type)

    def on_message(
        self,
        room: str,
        type: str | None = None,
    ) -> Callable:
        """Register a handler for messages in a room.

        Args:
            room: Room name to listen to.
            type: Optional message_type filter (chat, claim, status, etc.).

        The decorated function receives a single dict argument (the message).
        """
        def decorator(fn: Callable) -> Callable:
            self._handlers.append((room, type, fn))
            self._joined_rooms.add(room)
            return fn
        return decorator

    def heartbeat(
        self,
        room: str,
        interval: float = 30.0,
    ) -> Callable:
        """Auto-send heartbeat presence at a fixed interval.

        The decorated function should return a status string (e.g. "idle",
        "working on auth"). It is called each interval to get the current
        status.
        """
        def decorator(fn: Callable) -> Callable:
            self._heartbeats.append((room, interval, fn))
            self._joined_rooms.add(room)
            return fn
        return decorator

    def claim(self, room: str, task: str) -> Callable:
        """Auto-claim a task, run the function, then post completion.

        Sends CLAIM message before running, STATUS message after.
        If the function raises, posts an alert.
        """
        def decorator(fn: Callable) -> Callable:
            def wrapper(*args, **kwargs):
                self.client.send(room, f"CLAIM: {task}", "claim")
                self._claims[task] = "in_progress"
                try:
                    result = fn(*args, **kwargs)
                    self._claims[task] = "done"
                    self.client.send(
                        room, f"STATUS: {task} complete", "status"
                    )
                    return result
                except Exception as e:
                    self._claims[task] = "failed"
                    self.client.send(
                        room, f"ALERT: {task} failed — {e}", "alert"
                    )
                    raise
            self._joined_rooms.add(room)
            return wrapper
        return decorator

    def _join_rooms(self) -> None:
        """Join all rooms referenced by handlers."""
        for room in self._joined_rooms:
            try:
                self.client.join(room)
            except Exception:
                pass

    def _poll_loop(self) -> None:
        """Main poll loop — fetch and dispatch messages."""
        while self._running:
            try:
                messages = self.client.receive()
                for msg in messages:
                    self._dispatch(msg)
            except Exception:
                pass
            time.sleep(self._poll_interval)

    def _dispatch(self, msg: dict) -> None:
        """Route a message to matching handlers."""
        msg_room = msg.get("room", "")
        msg_type = msg.get("message_type", "chat")
        # Skip own messages
        if msg.get("from_name") == self.name:
            return
        for room, type_filter, handler in self._handlers:
            if room != msg_room:
                continue
            if type_filter and type_filter != msg_type:
                continue
            try:
                handler(msg)
            except Exception:
                pass

    def _heartbeat_loop(self, room: str, interval: float, fn: Callable) -> None:
        """Send periodic heartbeat with status from fn."""
        while self._running:
            try:
                status = fn()
                self.client.send(room, f"STATUS: {status}", "status")
            except Exception:
                pass
            time.sleep(interval)

    def run(self) -> None:
        """Start the agent — joins rooms, polls, heartbeats. Blocks."""
        self._running = True
        self._join_rooms()

        # Start heartbeat threads
        threads: list[threading.Thread] = []
        for room, interval, fn in self._heartbeats:
            t = threading.Thread(
                target=self._heartbeat_loop,
                args=(room, interval, fn),
                daemon=True,
            )
            t.start()
            threads.append(t)

        # Run poll loop on main thread (blocks)
        try:
            self._poll_loop()
        except KeyboardInterrupt:
            pass
        finally:
            self._running = False

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
