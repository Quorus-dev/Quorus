#!/usr/bin/env python3
"""Hackathon readiness test — 6 agents, 2 isolated rooms, stress test.

Simulates the April 15th hackathon: yc-hack and openai-hack rooms running
simultaneously with 3 agents each, heavy message traffic, verifying isolation.

Run:  python test_hackathon_ready.py
"""

import asyncio
import json
import subprocess
import sys
import time

import httpx

RELAY_URL = "http://localhost:18081"
SECRET = "hackathon-test-secret"
HEADERS = {"Authorization": f"Bearer {SECRET}"}

ROOMS = ["yc-hack", "openai-hack"]
YC_AGENTS = ["yc-agent-1", "yc-agent-2", "yc-agent-3"]
OAI_AGENTS = ["oai-agent-1", "oai-agent-2", "oai-agent-3"]
ALL_AGENTS = YC_AGENTS + OAI_AGENTS

passed = 0
failed = 0


def _log(msg: str) -> None:
    print(f"  [{time.strftime('%H:%M:%S')}] {msg}")


def _pass(label: str) -> None:
    global passed
    passed += 1
    print(f"  PASS  {label}")


def _fail(label: str, detail: str = "") -> None:
    global failed
    failed += 1
    print(f"  FAIL  {label}: {detail}")


async def test_hackathon():
    print("\n=== Murmur Hackathon Readiness Test ===")
    print(f"  6 agents, 2 rooms, isolation + stress\n")

    # Start relay
    _log("Starting relay on port 18081...")
    relay_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "murmur.relay:app",
         "--host", "127.0.0.1", "--port", "18081"],
        env={**__import__("os").environ, "RELAY_SECRET": SECRET, "LOG_LEVEL": "WARNING"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        async with httpx.AsyncClient() as client:
            # Wait for relay
            for _ in range(20):
                try:
                    r = await client.get(f"{RELAY_URL}/health")
                    if r.status_code == 200:
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.25)
            else:
                _fail("relay startup", "timed out")
                return

            _pass("relay started")

            # Create both rooms
            for room in ROOMS:
                r = await client.post(
                    f"{RELAY_URL}/rooms",
                    json={"name": room, "created_by": "coordinator"},
                    headers=HEADERS,
                )
                if r.status_code != 200:
                    _fail(f"create {room}", r.text)
                    return
            _pass("both rooms created")

            # Join agents to their rooms
            for agent in YC_AGENTS:
                await client.post(
                    f"{RELAY_URL}/rooms/yc-hack/join",
                    json={"participant": agent}, headers=HEADERS,
                )
            for agent in OAI_AGENTS:
                await client.post(
                    f"{RELAY_URL}/rooms/openai-hack/join",
                    json={"participant": agent}, headers=HEADERS,
                )
            _pass("all 6 agents joined their rooms")

            # Verify room isolation — members don't overlap
            r1 = await client.get(f"{RELAY_URL}/rooms/yc-hack", headers=HEADERS)
            r2 = await client.get(f"{RELAY_URL}/rooms/openai-hack", headers=HEADERS)
            yc_members = set(r1.json()["members"])
            oai_members = set(r2.json()["members"])
            overlap = yc_members & oai_members - {"coordinator"}
            if overlap:
                _fail("room isolation (members)", f"overlap: {overlap}")
            else:
                _pass("room members isolated")

            # Send messages to both rooms simultaneously
            msg_types = ["chat", "claim", "status", "request", "alert", "sync"]
            for i in range(20):
                room_idx = i % 2
                room = ROOMS[room_idx]
                agents = YC_AGENTS if room_idx == 0 else OAI_AGENTS
                agent = agents[i % 3]
                mtype = msg_types[i % len(msg_types)]
                r = await client.post(
                    f"{RELAY_URL}/rooms/{room}/messages",
                    json={"from_name": agent, "content": f"msg-{i}", "message_type": mtype},
                    headers=HEADERS,
                )
                if r.status_code != 200:
                    _fail(f"send msg-{i}", r.text)
                    return
            _pass("sent 20 messages across both rooms (mixed types)")

            # Verify fan-out isolation — yc agents only see yc messages
            for agent in YC_AGENTS:
                r = await client.get(f"{RELAY_URL}/messages/{agent}", headers=HEADERS)
                msgs = r.json()
                rooms_seen = {m.get("room") for m in msgs}
                if "openai-hack" in rooms_seen:
                    _fail(f"isolation ({agent})", "saw openai-hack messages")
                    return
            _pass("yc agents see only yc-hack messages")

            for agent in OAI_AGENTS:
                r = await client.get(f"{RELAY_URL}/messages/{agent}", headers=HEADERS)
                msgs = r.json()
                rooms_seen = {m.get("room") for m in msgs}
                if "yc-hack" in rooms_seen:
                    _fail(f"isolation ({agent})", "saw yc-hack messages")
                    return
            _pass("oai agents see only openai-hack messages")

            # Verify history per room
            r = await client.get(
                f"{RELAY_URL}/rooms/yc-hack/history", headers=HEADERS
            )
            yc_history = r.json()
            r = await client.get(
                f"{RELAY_URL}/rooms/openai-hack/history", headers=HEADERS
            )
            oai_history = r.json()
            if len(yc_history) != 10:
                _fail("yc history count", f"expected 10, got {len(yc_history)}")
            else:
                _pass(f"yc-hack history: {len(yc_history)} messages")
            if len(oai_history) != 10:
                _fail("oai history count", f"expected 10, got {len(oai_history)}")
            else:
                _pass(f"openai-hack history: {len(oai_history)} messages")

            # Stress test — 100 rapid messages to one room
            _log("Stress test: 100 rapid messages to yc-hack...")
            t0 = time.time()
            for i in range(100):
                agent = YC_AGENTS[i % 3]
                r = await client.post(
                    f"{RELAY_URL}/rooms/yc-hack/messages",
                    json={"from_name": agent, "content": f"stress-{i}"},
                    headers=HEADERS,
                )
                if r.status_code == 429:
                    _log(f"  Rate limited at message {i} (expected)")
                    break
                if r.status_code != 200:
                    _fail(f"stress msg-{i}", f"status {r.status_code}")
                    break
            elapsed = time.time() - t0
            _pass(f"stress test: {i + 1} messages in {elapsed:.2f}s")

            # SSE test — verify real-time delivery
            _log("SSE test: real-time delivery...")
            sse_msgs = []
            async with client.stream(
                "GET", f"{RELAY_URL}/stream/yc-agent-1",
                params={"token": SECRET}, timeout=5,
            ) as stream:
                event_type = ""
                event_data = ""
                # Send a message while streaming
                await client.post(
                    f"{RELAY_URL}/rooms/yc-hack/messages",
                    json={"from_name": "yc-agent-2", "content": "sse-live-test"},
                    headers=HEADERS,
                )
                async for line in stream.aiter_lines():
                    line = line.strip()
                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        event_data = line[5:].strip()
                    elif line == "" and event_type:
                        if event_type == "message":
                            sse_msgs.append(json.loads(event_data))
                            break
                        event_type = ""
                        event_data = ""

            if sse_msgs and sse_msgs[0]["content"] == "sse-live-test":
                _pass("SSE real-time delivery works")
            else:
                _fail("SSE delivery", f"got {sse_msgs}")

            # Verify coordinator can see analytics
            r = await client.get(f"{RELAY_URL}/analytics", headers=HEADERS)
            if r.status_code == 200:
                stats = r.json()
                _pass(f"analytics: {stats['total_messages_sent']} sent")
            else:
                _fail("analytics", r.text)

            # pip install verification
            _log("Verifying package import...")
            try:
                from murmur.integrations.http_agent import MurmurClient
                c = MurmurClient(RELAY_URL, SECRET, "test-client")
                rooms = c.rooms()
                if len(rooms) >= 2:
                    _pass("MurmurClient integration works")
                else:
                    _fail("MurmurClient", f"expected 2+ rooms, got {len(rooms)}")
            except Exception as e:
                _fail("MurmurClient import", str(e))

    finally:
        relay_proc.terminate()
        relay_proc.wait(timeout=5)
        _log("Relay stopped")

    print(f"\n=== RESULTS: {passed} passed, {failed} failed ===")
    if failed:
        print("  HACKATHON NOT READY — fix failures above")
        sys.exit(1)
    else:
        print("  HACKATHON READY!")


if __name__ == "__main__":
    asyncio.run(test_hackathon())
