"""Pure helper functions used by relay route handlers.

These are stateless utilities with no dependency on in-memory relay state.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict

_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
MAX_NAME_LENGTH = 64


def _validate_name(value: str) -> str:
    if not value or len(value) > MAX_NAME_LENGTH:
        raise ValueError(f"Name must be 1-{MAX_NAME_LENGTH} characters")
    if not _NAME_RE.match(value):
        raise ValueError("Name must contain only alphanumeric characters, hyphens, and underscores")
    return value


def _chunk_content(content: str, max_size: int) -> list[str]:
    """Split *content* into UTF-8 safe chunks of at most *max_size* bytes each."""
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for char in content:
        char_size = len(char.encode("utf-8"))
        if current and current_size + char_size > max_size:
            chunks.append("".join(current))
            current = [char]
            current_size = char_size
        else:
            current.append(char)
            current_size += char_size
    if current:
        chunks.append("".join(current))
    return chunks


def _reassemble_chunks(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """Reassemble chunked messages into complete messages.

    Returns ``(ready, held_back)`` where *ready* contains fully reassembled
    (and non-chunked) messages and *held_back* contains incomplete chunk groups.
    """
    non_chunked: list[dict] = []
    chunk_groups: dict[str, list[dict]] = defaultdict(list)
    for msg in messages:
        if "chunk_group" in msg:
            chunk_groups[msg["chunk_group"]].append(msg)
        else:
            non_chunked.append(msg)
    ready = list(non_chunked)
    held_back: list[dict] = []
    for _group_id, chunks in chunk_groups.items():
        expected = chunks[0]["chunk_total"]
        if len(chunks) == expected:
            chunks.sort(key=lambda c: c["chunk_index"])
            reassembled = {
                "id": chunks[0]["id"],
                "from_name": chunks[0]["from_name"],
                "to": chunks[0]["to"],
                "content": "".join(c["content"] for c in chunks),
                "timestamp": chunks[0]["timestamp"],
            }
            # Preserve delivery IDs from all chunks for ACK.
            # _delivery_id is a compound JSON token so per-message ACK
            # covers every chunk.  _chunk_delivery_ids is kept as a
            # plain list for internal use (ack_token rebuilding, etc.).
            chunk_delivery_ids = [
                c["_delivery_id"] for c in chunks if "_delivery_id" in c
            ]
            if chunk_delivery_ids:
                reassembled["_delivery_id"] = json.dumps(chunk_delivery_ids)
                reassembled["_chunk_delivery_ids"] = chunk_delivery_ids
            ready.append(reassembled)
        else:
            held_back.extend(chunks)
    ready.sort(key=lambda m: m["timestamp"])
    return ready, held_back
