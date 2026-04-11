"""Murmur Watcher — background daemon that writes room context to .murmur/context.md.

Context includes:
- Room state (active agents, claimed tasks, locked files, decisions)
- Last N messages in the room
- Snapshot timestamp and metadata

The watcher writes atomically every 10s (or event-driven via SSE).
Each agent maintains its own local .murmur/context.md (repo-scoped, not shared).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import structlog

logger = structlog.get_logger("murmur.watcher")


class Watcher:
  """Background task that writes room context to .murmur/context.md every N seconds."""

  def __init__(
      self,
      relay_url: str,
      auth_headers: dict[str, str],
      room_name: str,
      agent_name: str,
      interval_seconds: int = 10,
      context_path: Path | None = None,
  ) -> None:
    self.relay_url = relay_url
    self.auth_headers = auth_headers
    self.room_name = room_name
    self.agent_name = agent_name
    self.interval_seconds = interval_seconds
    self.context_path = context_path or Path.cwd() / ".murmur" / "context.md"
    self._running = False
    self._task: asyncio.Task | None = None

  async def start(self) -> None:
    """Start the watcher background task."""
    if self._running:
      return
    self._running = True
    self._task = asyncio.create_task(self._run())
    logger.info(f"Watcher started for room={self.room_name}, context={self.context_path}")

  async def stop(self) -> None:
    """Stop the watcher background task."""
    if not self._running:
      return
    self._running = False
    if self._task:
      self._task.cancel()
      try:
        await self._task
      except asyncio.CancelledError:
        pass
    logger.info(f"Watcher stopped for room={self.room_name}")

  async def _run(self) -> None:
    """Main watcher loop: fetch state and write context.md every N seconds."""
    client = httpx.AsyncClient()
    try:
      while self._running:
        try:
          context = await self._fetch_context(client)
          await self._write_context(context)
        except Exception as e:
          logger.error("Watcher update failed", error=str(e))

        # Sleep before next update
        await asyncio.sleep(self.interval_seconds)
    finally:
      await client.aclose()

  async def _fetch_context(self, client: httpx.AsyncClient) -> dict:
    """Fetch room state and message history."""
    context = {
        "room": self.room_name,
        "agent": self.agent_name,
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": "1.0",
    }

    # Fetch room state from Primitive A (GET /rooms/{room}/state)
    try:
      resp = await client.get(
          f"{self.relay_url}/rooms/{self.room_name}/state",
          headers=self.auth_headers,
          timeout=5,
      )
      if resp.status_code == 200:
        context["room_state"] = resp.json()
      else:
        context["room_state"] = None
    except Exception as e:
      logger.warning("Failed to fetch room state", error=str(e))
      context["room_state"] = None

    # Fetch message history (last 20 messages)
    try:
      resp = await client.get(
          f"{self.relay_url}/rooms/{self.room_name}/history?limit=20",
          headers=self.auth_headers,
          timeout=5,
      )
      if resp.status_code == 200:
        context["messages"] = resp.json()
      else:
        context["messages"] = []
    except Exception as e:
      logger.warning("Failed to fetch message history", error=str(e))
      context["messages"] = []

    return context

  async def _write_context(self, context: dict) -> None:
    """Write context to .murmur/context.md atomically."""
    # Ensure .murmur/ directory exists
    self.context_path.parent.mkdir(parents=True, exist_ok=True)

    # Format as Markdown
    md = self._format_context_md(context)

    # Atomic write (write to temp file, then rename)
    import tempfile
    import os

    try:
      fd, tmp_path = tempfile.mkstemp(dir=self.context_path.parent, suffix=".tmp")
      try:
        os.write(fd, md.encode("utf-8"))
        os.fsync(fd)
      finally:
        os.close(fd)
      os.replace(tmp_path, self.context_path)
    except OSError as e:
      logger.error("Failed to write context file", error=str(e), path=str(self.context_path))

  def _format_context_md(self, context: dict) -> str:
    """Format context dict as Markdown."""
    lines = []

    # Header
    lines.append(f"# {context['room']}")
    lines.append(f"**Agent**: {context['agent']} | **Snapshot**: {context['snapshot_at'][:19]}")
    lines.append("")

    # Room State Section
    room_state = context.get("room_state", {})
    if room_state:
      lines.append("## Room State")
      lines.append(f"**Active agents**: {', '.join(room_state.get('active_agents', []))}")
      lines.append(f"**Message count**: {room_state.get('message_count', 0)}")
      lines.append("")

      # Claimed tasks
      if room_state.get("claimed_tasks"):
        lines.append("### Claimed Tasks")
        for task in room_state["claimed_tasks"]:
          lines.append(
              f"- `{task.get('file_path')}` — claimed by {task.get('claimed_by')} "
              f"({task.get('claimed_at', '')[:16]})"
          )
        lines.append("")

      # Locked files
      if room_state.get("locked_files"):
        lines.append("### Locked Files")
        for path, lock_info in room_state["locked_files"].items():
          lines.append(f"- `{path}` — locked by {lock_info.get('held_by')}")
        lines.append("")

    # Messages Section
    messages = context.get("messages", [])
    if messages:
      lines.append("## Recent Messages")
      for msg in messages:
        ts = msg.get("timestamp", "")[:16]
        sender = msg.get("from_name", "?")
        msg_type = msg.get("type", "chat")
        content = msg.get("content", "")
        lines.append(f"**{ts}** `{sender}` [{msg_type}]: {content}")
      lines.append("")

    return "\n".join(lines)
