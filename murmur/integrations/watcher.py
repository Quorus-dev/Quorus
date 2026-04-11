#!/usr/bin/env python3
"""Murmur Watcher — SSE listener that writes to inbox file + macOS notifications.

Usage:
    python -m murmur.integrations.watcher --name agent-1 --relay http://localhost:8080 --secret s

Writes new messages to /tmp/murmur-{name}-inbox.txt (one JSON per line).
On macOS, also shows a system notification for each message.

Agents can read the inbox file in their CLAUDE.md:
    "Read /tmp/murmur-{name}-inbox.txt before every task."
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx


def _notify_macos(title: str, body: str) -> None:
    """Show macOS system notification."""
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{body}" with title "{title}"'],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


def watch(name: str, relay_url: str, secret: str, inbox_path: str | None = None):
    """Watch for messages via SSE and write to inbox file."""
    if inbox_path is None:
        inbox_path = f"/tmp/murmur-{name}-inbox.txt"

    inbox = Path(inbox_path)
    inbox.touch()

    print(f"Murmur watcher for '{name}'")
    print(f"  Relay: {relay_url}")
    print(f"  Inbox: {inbox}")
    print("  Ctrl+C to stop\n")

    url = f"{relay_url}/stream/{name}"
    params = {"token": secret}

    while True:
        try:
            with httpx.Client() as client:
                with client.stream("GET", url, params=params,
                                   timeout=None) as resp:
                    if resp.status_code != 200:
                        print(f"SSE returned {resp.status_code}, retrying...")
                        time.sleep(2)
                        continue

                    print("Connected to SSE stream.")
                    event_type = ""
                    event_data = ""

                    for line in resp.iter_lines():
                        line = line.strip()
                        if line.startswith("event:"):
                            event_type = line[6:].strip()
                        elif line.startswith("data:"):
                            event_data = line[5:].strip()
                        elif line == "" and event_type:
                            if event_type == "message":
                                try:
                                    msg = json.loads(event_data)
                                    sender = msg.get("from_name", "?")
                                    content = msg.get("content", "")
                                    room = msg.get("room", "DM")

                                    # Write to inbox
                                    with open(inbox, "a") as f:
                                        f.write(json.dumps(msg) + "\n")

                                    # Console output
                                    print(f"  [{room}] {sender}: {content}")

                                    # macOS notification
                                    _notify_macos(
                                        f"Murmur — {room}",
                                        f"{sender}: {content[:80]}",
                                    )
                                except json.JSONDecodeError:
                                    pass
                            event_type = ""
                            event_data = ""

        except httpx.ConnectError:
            print("Connection lost, reconnecting in 2s...")
        except KeyboardInterrupt:
            print("\nWatcher stopped.")
            break
        except Exception as e:
            print(f"Error: {e}, reconnecting in 2s...")

        time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description="Murmur message watcher")
    parser.add_argument("--name", required=True, help="Agent name")
    parser.add_argument("--relay", required=True, help="Relay URL")
    parser.add_argument("--secret", required=True, help="Relay secret")
    parser.add_argument("--inbox", help="Inbox file path (default: /tmp/murmur-{name}-inbox.txt)")
    args = parser.parse_args()
    watch(args.name, args.relay, args.secret, args.inbox)


if __name__ == "__main__":
    main()
