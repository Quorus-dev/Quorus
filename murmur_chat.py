#!/usr/bin/env python3
"""Simple polling chat for test-room on Aarya's relay."""
import sys
import threading
import time

import httpx

RELAY   = "http://100.127.251.47:9000"
SECRET  = "medport-secret"
ROOM    = "test-room"
NAME    = sys.argv[1] if len(sys.argv) > 1 else "arav"
HEADERS = {"Authorization": f"Bearer {SECRET}"}

seen = set()

def poll():
    while True:
        try:
            r = httpx.get(f"{RELAY}/rooms/{ROOM}/history", headers=HEADERS, timeout=5)
            data = r.json()
            msgs = data if isinstance(data, list) else data.get("messages", [])
            for m in msgs:
                mid = m.get("id") or m.get("timestamp","")
                if mid not in seen:
                    seen.add(mid)
                    sender = m.get("from_name") or m.get("sender","?")
                    content = m.get("content","")
                    ts = m.get("timestamp","")[:16].replace("T"," ")
                    if sender != NAME:
                        print(f"\r\033[K[{ts}] {sender}: {content}\n{NAME}> ", end="", flush=True)
        except Exception:
            pass
        time.sleep(2)

# Join room
try:
    httpx.post(f"{RELAY}/rooms/{ROOM}/join", headers=HEADERS, json={"participant": NAME}, timeout=5)
except Exception:
    pass

print(f"=== Murmur Chat — {ROOM} (as {NAME}) ===")
print("Type a message and press Enter. Ctrl+C to quit.\n")

t = threading.Thread(target=poll, daemon=True)
t.start()

try:
    while True:
        print(f"{NAME}> ", end="", flush=True)
        msg = input()
        if msg.strip():
            try:
                httpx.post(
                    f"{RELAY}/rooms/{ROOM}/messages",
                    headers=HEADERS,
                    json={"from_name": NAME, "content": msg.strip()},
                    timeout=5,
                )
            except Exception as e:
                print(f"[send error: {e}]")
except KeyboardInterrupt:
    print("\nBye.")
