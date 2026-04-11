# Build Your First Multi-Agent Project in 5 Minutes

Two Python agents coordinate through a Murmur room. Agent 1 researches a topic and posts findings. Agent 2 reads those findings and writes code. They talk in real time.

By the end you will have a working script you can adapt to any multi-agent workflow.

## Prerequisites

- Python 3.10+
- Two terminals open

## Step 1: Install Murmur

```bash
pip install murmur-ai
```

## Step 2: Start the Relay

The relay is the central message hub. Every agent connects to it.

```bash
export RELAY_SECRET=my-secret-token
murmur relay --port 8080
```

Keep this terminal open. Use a second terminal for everything else.

## Step 3: Create a Room

```bash
export RELAY_SECRET=my-secret-token
murmur create my-project
```

Rooms are group chats for agents. One room per project.

## Step 4: Run the Agents

Save this as `duo.py` and run it. Both agents execute in the same script using threads.

````python
"""Two agents coordinate through a Murmur room.

Agent 1 (researcher): picks a topic, investigates it, posts findings.
Agent 2 (coder): waits for findings, then writes code based on them.
"""

import threading
import time

from murmur import Room

RELAY = "http://localhost:8080"
SECRET = "my-secret-token"
ROOM = "my-project"


def researcher():
    """Research agent — investigates a topic and shares findings."""
    room = Room(ROOM, relay=RELAY, secret=SECRET, name="researcher")
    room.join()
    print("[researcher] Joined the room.")

    # Pick a topic
    room.send("Starting research on: building a CLI weather tool in Python.")

    # Phase 1: gather requirements
    time.sleep(1)
    room.status("Researching weather APIs...")
    time.sleep(2)

    # Post findings
    room.send(
        "FINDINGS:\n"
        "1. OpenMeteo API is free, no key required.\n"
        "2. Endpoint: https://api.open-meteo.com/v1/forecast"
        "?latitude={lat}&longitude={lon}&current_weather=true\n"
        "3. Returns JSON with temperature, windspeed, weathercode.\n"
        "4. Use httpx for HTTP calls, click for the CLI.\n"
        "5. Accept city name as argument, geocode with OpenMeteo geocoding API.",
        type="chat",
    )
    print("[researcher] Findings posted.")

    # Wait for coder to acknowledge
    time.sleep(3)
    messages = room.receive()
    for msg in messages:
        print(f"[researcher] Got: {msg['from_name']}: {msg['content'][:80]}")

    room.sync("Research phase complete. Coder has the findings.")
    print("[researcher] Done.")


def coder():
    """Coder agent — waits for research, then writes code."""
    room = Room(ROOM, relay=RELAY, secret=SECRET, name="coder")
    room.join()
    print("[coder] Joined the room. Waiting for research...")

    room.claim("writing the CLI weather tool")

    # Poll until findings arrive
    findings = None
    for _ in range(15):
        messages = room.receive(wait=2)
        for msg in messages:
            print(f"[coder] Got: {msg['from_name']}: {msg['content'][:80]}")
            if "FINDINGS:" in msg.get("content", ""):
                findings = msg["content"]
        if findings:
            break

    if not findings:
        room.alert("No findings received after 30s. Stopping.")
        return

    # Build code based on findings
    room.status("Writing weather CLI based on research findings...")
    time.sleep(2)

    code = '''
import httpx
import click

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

@click.command()
@click.argument("city")
def weather(city):
    """Get current weather for CITY."""
    geo = httpx.get(GEOCODE_URL, params={"name": city, "count": 1}).json()
    if not geo.get("results"):
        click.echo(f"City not found: {city}")
        return
    loc = geo["results"][0]
    lat, lon = loc["latitude"], loc["longitude"]
    data = httpx.get(WEATHER_URL, params={
        "latitude": lat, "longitude": lon, "current_weather": True,
    }).json()["current_weather"]
    click.echo(f"{loc['name']}: {data['temperature']}C, wind {data['windspeed']}km/h")

if __name__ == "__main__":
    weather()
'''

    room.send(f"CODE COMPLETE:\n```python{code}```")
    room.status("CLI weather tool written. Ready for review.")
    print("[coder] Done.")


# Run both agents in parallel
t1 = threading.Thread(target=researcher)
t2 = threading.Thread(target=coder)
t1.start()
t2.start()
t1.join()
t2.join()

print("\nBoth agents finished. Check the room history below.")
````

Run it:

```bash
python duo.py
```

You will see both agents printing their progress as they coordinate:

```
[researcher] Joined the room.
[coder] Joined the room. Waiting for research...
[researcher] Findings posted.
[coder] Got: researcher: FINDINGS:
[coder] Got: researcher: Starting research on: building a CLI weather tool in Py
[researcher] Got: coder: STATUS: Writing weather CLI based on research findings...
[researcher] Done.
[coder] Done.

Both agents finished. Check the room history below.
```

## Step 5: Inspect with the CLI

With the relay still running, use the CLI to inspect what happened.

### See who is online

```bash
murmur ps
```

```
AGENT       STATUS    LAST SEEN
researcher  online    2s ago
coder       online    1s ago
```

### Read the full conversation

```bash
murmur history my-project
```

Shows every message in order — claims, status updates, findings, code, sync.

### Export for documentation

```bash
# Markdown (great for READMEs and post-mortems)
murmur export my-project --format md --output session.md

# JSON (great for pipelines and analysis)
murmur export my-project --format json --output session.json
```

## What Just Happened

1. **Room** gave both agents a shared communication channel.
2. **Researcher** posted structured findings. **Coder** polled until it received them.
3. **Message types** (claim, status, sync, alert) kept coordination structured.
4. The relay persisted everything — you can replay the full session with `murmur history`.

## The SDK in 30 Seconds

```python
from murmur import Room

room = Room("my-room", relay="http://localhost:8080", secret="token", name="agent-1")
room.join()

room.send("hello")              # chat message
room.claim("auth module")       # claim a task
room.status("50% done")         # post progress
room.alert("tests failing")     # raise an alert
room.sync("merged to main")     # coordinate a handoff

messages = room.receive(wait=5) # long-poll for new messages
history = room.history(limit=50)# read room history
count = room.peek()             # check inbox without consuming
room.dm("agent-2", "heads up")  # direct message
```

## Next Steps

- **Spawn Claude Code agents** with MCP tools: `murmur spawn my-project agent-1`
- **Watch live**: `murmur watch my-project` streams messages as they arrive.
- **Deploy the relay**: `docker compose up -d` or push to Railway/Render.
- **API docs**: open `http://localhost:8080/docs` for the full OpenAPI spec.

---

Built with [Murmur](https://github.com/Aarya2004/murmur) — group chat for AI agents.
