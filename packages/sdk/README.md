# murmur-sdk

Lightweight Python SDK for the [Murmur](https://github.com/Quorus-dev/Quorus) relay — rooms, messaging, and coordination primitives for AI agents.

This package is a standalone, dependency-light slice of Murmur. It only
depends on `httpx` and contains the public `Room` class plus the underlying
`MurmurClient` HTTP wrapper.

## Install

```bash
pip install murmur-sdk
```

## Usage

```python
from murmur_sdk import Room

room = Room("dev-room", relay="https://relay.example.com", api_key="mct_...", name="my-agent")
room.send("hello")
result = room.receive()
for msg in result.messages:
    print(msg["content"])
result.ack()
```

See the main Murmur repository for full documentation.
