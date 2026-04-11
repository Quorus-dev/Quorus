#!/usr/bin/env python3
"""Stress test: 100 messages to a room with 10 members. Measures latency."""

import asyncio
import subprocess
import sys
import time

import httpx

RELAY = "http://localhost:18091"
SECRET = "stress-test-secret"
HEADERS = {"Authorization": f"Bearer {SECRET}", "Content-Type": "application/json"}
ROOM = "stress-room"
AGENTS = [f"agent-{i}" for i in range(10)]
MSG_COUNT = 100


async def run():
    print("=== Murmur Stress Test ===\n")

    # Start relay
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "murmur.relay:app",
         "--host", "127.0.0.1", "--port", "18091"],
        env={**__import__("os").environ, "RELAY_SECRET": SECRET,
             "LOG_LEVEL": "WARNING"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    async with httpx.AsyncClient() as c:
        # Wait for relay
        for _ in range(20):
            try:
                r = await c.get(f"{RELAY}/health")
                if r.status_code == 200:
                    break
            except httpx.ConnectError:
                pass
            await asyncio.sleep(0.2)

        # Create room + join 10 agents
        await c.post(f"{RELAY}/rooms",
                     json={"name": ROOM, "created_by": AGENTS[0]},
                     headers=HEADERS)
        for a in AGENTS[1:]:
            await c.post(f"{RELAY}/rooms/{ROOM}/join",
                         json={"participant": a}, headers=HEADERS)
        print(f"Room '{ROOM}' with {len(AGENTS)} members ready.\n")

        # Send 100 messages, measure per-message latency
        latencies = []
        print(f"Sending {MSG_COUNT} messages...")
        t0 = time.monotonic()
        for i in range(MSG_COUNT):
            sender = AGENTS[i % len(AGENTS)]
            start = time.monotonic()
            r = await c.post(
                f"{RELAY}/rooms/{ROOM}/messages",
                json={"from_name": sender, "content": f"stress-msg-{i}",
                      "message_type": "chat"},
                headers=HEADERS,
            )
            elapsed = (time.monotonic() - start) * 1000
            latencies.append(elapsed)
            if r.status_code != 200:
                print(f"  FAIL msg-{i}: {r.status_code} {r.text}")
        total = time.monotonic() - t0

        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]

        print(f"\n  Total: {total:.2f}s for {MSG_COUNT} messages")
        print(f"  Throughput: {MSG_COUNT / total:.1f} msg/s")
        print(f"  Latency p50: {p50:.1f}ms  p95: {p95:.1f}ms  p99: {p99:.1f}ms")

        # Verify fan-out: each agent should have 100 messages
        print(f"\nVerifying fan-out to {len(AGENTS)} agents...")
        all_ok = True
        for a in AGENTS:
            r = await c.get(f"{RELAY}/messages/{a}", headers=HEADERS)
            msgs = r.json()
            if len(msgs) != MSG_COUNT:
                print(f"  FAIL {a}: expected {MSG_COUNT}, got {len(msgs)}")
                all_ok = False

        if all_ok:
            print(f"  PASS: all {len(AGENTS)} agents received {MSG_COUNT} msgs")

        # Verify history
        r = await c.get(f"{RELAY}/rooms/{ROOM}/history?limit=200",
                        headers=HEADERS)
        hist = r.json()
        print(f"\n  History: {len(hist)} messages stored")

        # Check rate limiting doesn't break at reasonable rates
        print("\nRate limit test (rapid-fire from one sender)...")
        ok = 0
        limited = 0
        for i in range(70):
            r = await c.post(
                f"{RELAY}/rooms/{ROOM}/messages",
                json={"from_name": "rate-tester",
                      "content": f"rapid-{i}"},
                headers=HEADERS,
            )
            if r.status_code == 200:
                ok += 1
            elif r.status_code == 429:
                limited += 1
        print(f"  {ok} accepted, {limited} rate-limited (429)")

    proc.terminate()
    proc.wait(timeout=5)
    print("\n=== STRESS TEST COMPLETE ===")


if __name__ == "__main__":
    asyncio.run(run())
