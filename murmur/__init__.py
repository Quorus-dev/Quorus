"""Murmur — real-time inter-agent communication for AI agents.

Quick start:
    from murmur import Room

    room = Room("dev-room", relay="http://localhost:8080", secret="xxx", name="my-agent")
    room.send("Hello team!", type="chat")
    room.claim("auth module")
    messages = room.receive()
"""

__version__ = "0.3.0"

from murmur.sdk import Room

__all__ = ["Room", "__version__"]
