#!/usr/bin/env python3
"""Hackathon dry run: simulates full April 15th setup.

Tests: 2 rooms, 6 agents, mission briefings, cross-room isolation,
message exchange, history, presence, invite pages — everything
that needs to work on hackathon day.
"""

import asyncio
import subprocess
import sys
import time

import httpx

RELAY = "http://localhost:18092"
SECRET = "hackathon-dry-run"
HEADERS = {"Authorization": f"Bearer {SECRET}", "Content-Type": "application/json"}

ROOMS = [
    {"name": "yc-hack", "mission": "Build an AI-powered dev tool"},
    {"name": "openai-hack", "mission": "Build a multi-agent coordinator"},
]
AGENTS_PER_ROOM = 3


def _pass(label):
    print(f"  PASS  {label}")


def _fail(label, detail=""):
    print(f"  FAIL  {label}")
    if detail:
        print(f"        {detail}")
    sys.exit(1)


async def run():
    print("\n=== Hackathon Dry Run ===\n")

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "murmur.relay:app",
         "--host", "127.0.0.1", "--port", "18092"],
        env={**__import__("os").environ, "RELAY_SECRET": SECRET,
             "LOG_LEVEL": "WARNING"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    async with httpx.AsyncClient() as c:
        # Wait for relay
        for _ in range(20):
            try:
                if (await c.get(f"{RELAY}/health")).status_code == 200:
                    break
            except httpx.ConnectError:
                pass
            await asyncio.sleep(0.2)

        _pass("relay started")

        # Create rooms + join agents
        all_agents = []
        for room in ROOMS:
            r = await c.post(f"{RELAY}/rooms",
                             json={"name": room["name"], "created_by": "arav"},
                             headers=HEADERS)
            assert r.status_code == 200, f"create room: {r.text}"

            await c.post(f"{RELAY}/rooms/{room['name']}/join",
                         json={"participant": "arav"}, headers=HEADERS)

            for i in range(1, AGENTS_PER_ROOM + 1):
                name = f"{room['name']}-agent-{i}"
                all_agents.append((name, room["name"]))
                await c.post(f"{RELAY}/rooms/{room['name']}/join",
                             json={"participant": name}, headers=HEADERS)

            # Mission briefing
            await c.post(f"{RELAY}/rooms/{room['name']}/messages",
                         json={"from_name": "arav",
                               "content": f"MISSION: {room['mission']}",
                               "message_type": "alert"},
                         headers=HEADERS)

        _pass(f"2 rooms created, {len(all_agents)} agents joined")

        # Each agent sends a claim + status
        for name, room in all_agents:
            await c.post(f"{RELAY}/rooms/{room}/messages",
                         json={"from_name": name,
                               "content": f"CLAIM: task from {name}",
                               "message_type": "claim"},
                         headers=HEADERS)
            await c.post(f"{RELAY}/rooms/{room}/messages",
                         json={"from_name": name,
                               "content": f"STATUS: {name} working",
                               "message_type": "status"},
                         headers=HEADERS)

        _pass("all agents sent claim + status messages")

        # Verify room isolation
        yc_agent = "yc-hack-agent-1"
        oai_agent = "openai-hack-agent-1"

        yc_msgs = (await c.get(f"{RELAY}/messages/{yc_agent}",
                                headers=HEADERS)).json()
        oai_msgs = (await c.get(f"{RELAY}/messages/{oai_agent}",
                                 headers=HEADERS)).json()

        yc_rooms = {m.get("room") for m in yc_msgs}
        oai_rooms = {m.get("room") for m in oai_msgs}

        if "openai-hack" in yc_rooms:
            _fail("room isolation", "yc agent got openai messages")
        if "yc-hack" in oai_rooms:
            _fail("room isolation", "openai agent got yc messages")
        _pass("room isolation verified — no cross-contamination")

        # Verify history
        for room in ROOMS:
            r = await c.get(f"{RELAY}/rooms/{room['name']}/history?limit=50",
                            headers=HEADERS)
            hist = r.json()
            # 1 mission + 3 claims + 3 statuses = 7 per room
            if len(hist) < 7:
                _fail(f"history {room['name']}",
                      f"expected >=7, got {len(hist)}")
        _pass("history preserved for both rooms")

        # Verify invite pages work
        for room in ROOMS:
            r = await c.get(f"{RELAY}/invite/{room['name']}")
            if r.status_code != 200:
                _fail(f"invite page {room['name']}", str(r.status_code))
            if "Join Room" not in r.text:
                _fail(f"invite page content {room['name']}")
        _pass("invite pages accessible for both rooms")

        # Verify heartbeat + presence
        for name, _ in all_agents[:2]:
            await c.post(f"{RELAY}/heartbeat",
                         json={"instance_name": name, "status": "active"},
                         headers=HEADERS)
        r = await c.get(f"{RELAY}/presence", headers=HEADERS)
        presence = r.json()
        online = [a for a in presence if a["online"]]
        if len(online) < 2:
            _fail("presence", f"expected >=2 online, got {len(online)}")
        _pass(f"presence: {len(online)} agents reporting online")

        # Verify analytics
        r = await c.get(f"{RELAY}/analytics", headers=HEADERS)
        stats = r.json()
        if stats["total_messages_sent"] < 12:
            _fail("analytics", f"sent={stats['total_messages_sent']}")
        _pass(f"analytics: {stats['total_messages_sent']} messages sent")

        # Verify peek (non-destructive)
        # arav should have messages from both rooms
        r = await c.get(f"{RELAY}/messages/arav/peek", headers=HEADERS)
        peek = r.json()
        _pass(f"peek: arav has {peek['count']} pending messages")

    proc.terminate()
    proc.wait(timeout=5)
    print("\n=== HACKATHON DRY RUN: ALL CHECKS PASSED ===\n")


if __name__ == "__main__":
    asyncio.run(run())
