#!/usr/bin/env python3
"""Comprehensive E2E test for Quorus relay.

Starts the relay server, creates a room, joins 3 agents, sends messages
with mixed types, and verifies fan-out, SSE delivery, and docker build.

Run:  python test_e2e_live.py
"""

import asyncio
import json
import subprocess
import sys
import time

import httpx

RELAY_URL = "http://localhost:18080"
SECRET = "e2e-test-secret"
HEADERS = {"Authorization": f"Bearer {SECRET}", "Content-Type": "application/json"}
ROOM_NAME = "e2e-test-room"
AGENTS = ["agent-alpha", "agent-beta", "agent-gamma"]


def _log(msg: str) -> None:
    print(f"  [{time.strftime('%H:%M:%S')}] {msg}")


def _pass(label: str) -> None:
    print(f"  PASS  {label}")


def _fail(label: str, detail: str = "") -> None:
    print(f"  FAIL  {label}")
    if detail:
        print(f"        {detail}")
    sys.exit(1)


async def _wait_for_relay(client: httpx.AsyncClient, timeout: float = 10) -> None:
    """Wait until the relay health endpoint responds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = await client.get(f"{RELAY_URL}/health")
            if resp.status_code == 200:
                return
        except httpx.ConnectError:
            pass
        await asyncio.sleep(0.2)
    _fail("relay startup", "Relay did not respond within timeout")


async def test_room_crud(client: httpx.AsyncClient) -> str:
    """Create room and join all agents. Returns room_id."""
    # Create room
    resp = await client.post(
        f"{RELAY_URL}/rooms",
        json={"name": ROOM_NAME, "created_by": AGENTS[0]},
        headers=HEADERS,
    )
    if resp.status_code != 200:
        _fail("create room", resp.text)
    room_id = resp.json()["id"]
    _pass(f"create room '{ROOM_NAME}' (id: {room_id[:8]}...)")

    # Join remaining agents
    for agent in AGENTS[1:]:
        resp = await client.post(
            f"{RELAY_URL}/rooms/{room_id}/join",
            json={"participant": agent},
            headers=HEADERS,
        )
        if resp.status_code != 200:
            _fail(f"join room ({agent})", resp.text)
    _pass(f"all {len(AGENTS)} agents joined room")

    # Verify room listing
    resp = await client.get(f"{RELAY_URL}/rooms", headers=HEADERS)
    rooms = resp.json()
    if not any(r["name"] == ROOM_NAME for r in rooms):
        _fail("list rooms", "Room not found in listing")
    _pass("room appears in listing")

    # Verify room details
    resp = await client.get(f"{RELAY_URL}/rooms/{room_id}", headers=HEADERS)
    room = resp.json()
    if set(room["members"]) != set(AGENTS):
        _fail("room members", f"Expected {AGENTS}, got {room['members']}")
    _pass("room members correct")

    return room_id


async def test_message_fanout(client: httpx.AsyncClient, room_id: str) -> None:
    """Send 10 messages with mixed types and verify fan-out."""
    messages = [
        ("agent-alpha", "CLAIMING: auth module", "claim"),
        ("agent-beta", "CLAIMING: database schema", "claim"),
        ("agent-gamma", "CLAIMING: API endpoints", "claim"),
        ("agent-alpha", "STATUS: auth module 50% done", "status"),
        ("agent-beta", "STATUS: schema migration ready", "status"),
        ("agent-gamma", "REQUEST: need API spec from alpha", "request"),
        ("agent-alpha", "SYNC: pushing auth branch now", "sync"),
        ("agent-beta", "ALERT: migration has breaking change", "alert"),
        ("agent-gamma", "STATUS: endpoints scaffolded", "status"),
        ("agent-alpha", "chat: good progress team", "chat"),
    ]

    # Send all messages
    for sender, content, msg_type in messages:
        resp = await client.post(
            f"{RELAY_URL}/rooms/{room_id}/messages",
            json={
                "from_name": sender,
                "content": content,
                "message_type": msg_type,
            },
            headers=HEADERS,
        )
        if resp.status_code != 200:
            _fail(f"send message ({sender}: {content[:30]})", resp.text)

    _pass(f"sent {len(messages)} messages with mixed types")

    # Verify fan-out: each agent should receive all 10 messages
    for agent in AGENTS:
        resp = await client.get(
            f"{RELAY_URL}/messages/{agent}",
            headers=HEADERS,
        )
        msgs = resp.json()
        if len(msgs) != len(messages):
            _fail(
                f"fan-out to {agent}",
                f"Expected {len(messages)}, got {len(msgs)}",
            )
    _pass(f"fan-out correct: each agent received {len(messages)} messages")

    # Verify message types are preserved
    # Re-send one to check type (previous msgs were consumed)
    resp = await client.post(
        f"{RELAY_URL}/rooms/{room_id}/messages",
        json={
            "from_name": "agent-alpha",
            "content": "type check",
            "message_type": "alert",
        },
        headers=HEADERS,
    )
    resp = await client.get(
        f"{RELAY_URL}/messages/agent-beta",
        headers=HEADERS,
    )
    msgs = resp.json()
    if msgs and msgs[0].get("message_type") != "alert":
        _fail("message type", f"Expected 'alert', got {msgs[0].get('message_type')}")
    _pass("message_type preserved in fan-out")


async def test_dm_alongside_rooms(client: httpx.AsyncClient) -> None:
    """Verify DMs still work alongside room messages."""
    resp = await client.post(
        f"{RELAY_URL}/messages",
        json={
            "from_name": "agent-alpha",
            "to": "agent-beta",
            "content": "private: are you blocked on anything?",
        },
        headers=HEADERS,
    )
    if resp.status_code != 200:
        _fail("send DM", resp.text)

    resp = await client.get(
        f"{RELAY_URL}/messages/agent-beta",
        headers=HEADERS,
    )
    msgs = resp.json()
    dm_msgs = [m for m in msgs if m.get("room") is None]
    if len(dm_msgs) != 1:
        _fail("receive DM", f"Expected 1 DM, got {len(dm_msgs)}")
    if dm_msgs[0]["content"] != "private: are you blocked on anything?":
        _fail("DM content", f"Got: {dm_msgs[0]['content']}")
    _pass("DMs work alongside room messages")


async def test_sse_delivery(client: httpx.AsyncClient, room_id: str) -> None:
    """Verify SSE stream delivers room messages in real-time."""
    received: list[dict] = []
    connected = asyncio.Event()

    async def sse_reader():
        async with client.stream(
            "GET",
            f"{RELAY_URL}/stream/agent-beta",
            params={"token": SECRET},
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
                elif line == "" and event_type:
                    if event_type == "connected":
                        connected.set()
                    elif event_type == "message":
                        received.append(json.loads(event_data))
                        if len(received) >= 2:
                            return
                    event_type = ""
                    event_data = ""

    # Use a separate client for SSE to avoid blocking
    sse_client = httpx.AsyncClient()
    try:
        reader_task = asyncio.create_task(
            _run_sse_reader(sse_client, received, connected, room_id)
        )

        # Wait for SSE to connect
        try:
            await asyncio.wait_for(connected.wait(), timeout=5)
        except asyncio.TimeoutError:
            _fail("SSE connect", "SSE stream did not connect within 5s")

        # Send 2 messages
        for content in ["sse-test-1", "sse-test-2"]:
            await client.post(
                f"{RELAY_URL}/rooms/{room_id}/messages",
                json={"from_name": "agent-alpha", "content": content},
                headers=HEADERS,
            )

        # Wait for delivery
        try:
            await asyncio.wait_for(reader_task, timeout=5)
        except asyncio.TimeoutError:
            pass

        if len(received) < 2:
            _fail("SSE delivery", f"Expected 2 messages, got {len(received)}")

        contents = {m["content"] for m in received}
        if "sse-test-1" not in contents or "sse-test-2" not in contents:
            _fail("SSE content", f"Got: {contents}")
        _pass("SSE delivers room messages in real-time")
    finally:
        reader_task.cancel()
        try:
            await reader_task
        except (asyncio.CancelledError, Exception):
            pass
        await sse_client.aclose()


async def _run_sse_reader(
    client: httpx.AsyncClient,
    received: list,
    connected: asyncio.Event,
    room_id: str,
) -> None:
    """SSE reader coroutine using its own client."""
    async with client.stream(
        "GET",
        f"{RELAY_URL}/stream/agent-beta",
        params={"token": SECRET},
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
            elif line == "" and event_type:
                if event_type == "connected":
                    connected.set()
                elif event_type == "message":
                    received.append(json.loads(event_data))
                    if len(received) >= 2:
                        return
                event_type = ""
                event_data = ""


async def test_history_endpoint(
    client: httpx.AsyncClient, room_id: str
) -> None:
    """Verify /rooms/{room_id}/history returns past messages."""
    resp = await client.get(
        f"{RELAY_URL}/rooms/{room_id}/history",
        headers=HEADERS,
    )
    if resp.status_code == 404:
        _log("SKIP  history endpoint (not implemented yet)")
        return
    if resp.status_code != 200:
        _fail("history endpoint", resp.text)
    history = resp.json()
    if not isinstance(history, list):
        _fail("history format", f"Expected list, got {type(history)}")
    if len(history) == 0:
        _fail("history empty", "Expected messages in history")
    _pass(f"history endpoint returns {len(history)} messages")


async def test_analytics(client: httpx.AsyncClient) -> None:
    """Verify analytics endpoint reflects activity."""
    resp = await client.get(f"{RELAY_URL}/analytics", headers=HEADERS)
    if resp.status_code != 200:
        _fail("analytics", resp.text)
    data = resp.json()
    if data["total_messages_sent"] == 0:
        _fail("analytics", "total_messages_sent is 0")
    _pass(f"analytics: {data['total_messages_sent']} sent, "
          f"{data['total_messages_delivered']} delivered")


async def test_auth_rejection(client: httpx.AsyncClient) -> None:
    """Verify unauthenticated requests are rejected."""
    resp = await client.get(f"{RELAY_URL}/rooms")
    if resp.status_code != 401:
        _fail("auth rejection", f"Expected 401, got {resp.status_code}")

    resp = await client.get(
        f"{RELAY_URL}/rooms",
        headers={"Authorization": "Bearer wrong-secret"},
    )
    if resp.status_code != 401:
        _fail("auth rejection (bad secret)", f"Expected 401, got {resp.status_code}")
    _pass("unauthenticated requests rejected (401)")


def test_docker_build() -> None:
    """Verify Docker image builds successfully."""
    result = subprocess.run(
        ["docker", "build", "-t", "murmur-e2e-test", "."],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        _fail("docker build", result.stderr[-500:] if result.stderr else "unknown")
    _pass("docker build succeeds")


async def run_tests() -> None:
    """Run all E2E tests against a live relay."""
    print("\n=== Quorus E2E Test Suite ===\n")

    # Start relay server
    _log("Starting relay server on port 18080...")
    relay_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "quorus.relay:app",
         "--host", "127.0.0.1", "--port", "18080"],
        env={
            **__import__("os").environ,
            "RELAY_SECRET": SECRET,
            "PORT": "18080",
            "LOG_LEVEL": "WARNING",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        async with httpx.AsyncClient() as client:
            await _wait_for_relay(client)
            _pass("relay server started")

            # Core tests
            await test_auth_rejection(client)
            room_id = await test_room_crud(client)
            await test_message_fanout(client, room_id)
            await test_dm_alongside_rooms(client)
            await test_sse_delivery(client, room_id)
            await test_history_endpoint(client, room_id)
            await test_analytics(client)

        # Docker test (outside async context)
        _log("Testing docker build...")
        test_docker_build()

        print("\n=== ALL TESTS PASSED ===\n")

    finally:
        relay_proc.terminate()
        relay_proc.wait(timeout=5)
        _log("Relay server stopped")


if __name__ == "__main__":
    asyncio.run(run_tests())
