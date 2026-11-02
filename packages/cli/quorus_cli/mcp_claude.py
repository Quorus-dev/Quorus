"""Claude MCP registration through the client-owned config surfaces.

https://code.claude.com/docs/en/mcp#add-mcp-servers-from-json-configuration
https://modelcontextprotocol.io/docs/develop/connect-local-servers
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from quorus_cli.mcp_config_io import (
    atomic_text,
    backup,
    kept_identity,
    read_json_snapshot,
    safe_relay,
    snapshot,
)
from quorus_cli.mcp_writer_types import McpEnv, WriteResult


def desktop_config_path() -> Path:
    if sys.platform == "darwin":
        return (Path.home() / "Library" / "Application Support" / "Claude"
                / "claude_desktop_config.json")
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return root / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def code_config_path() -> Path:
    root = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(root).expanduser() if root else Path.home()) / ".claude.json"


def _entry(env: McpEnv, key: str) -> dict:
    return {"type": "stdio", "command": env.command, "args": list(env.args),
            "env": env.env_block(key)}


def _manual(entry: dict) -> str:
    # Show a runnable template, never a key or relay secret in terminal logs.
    entry = {**entry, "env": dict(entry["env"])}
    for key in ("QUORUS_API_KEY", "QUORUS_RELAY_SECRET"):
        if key in entry["env"]:
            entry["env"][key] = "${" + key + "}"
    args = ["claude", "mcp", "add-json", "quorus", json.dumps(entry), "-s", "user"]
    command = subprocess.list2cmdline(args) if sys.platform == "win32" else shlex.join(args)
    return "Run this after installing Claude Code (set credential env vars if needed): " + command


def register_claude_desktop(env: McpEnv, *, force=False, create=False) -> WriteResult:
    path = desktop_config_path()
    if not path.exists() and not (force or create):
        return WriteResult("Claude Desktop", "not_installed", path)
    try:
        data, original = read_json_snapshot(path)
        servers = data.setdefault("mcpServers", {})
        entry = _entry(env, "claude-desktop")
        old = servers.get("quorus")
        detail = kept_identity(old, entry, "claude-desktop", force=force)
        if detail:
            return WriteResult("Claude Desktop", "kept", path, detail)
        servers["quorus"] = entry
        atomic_text(path, json.dumps(data, indent=2) + "\n", original=original)
        return WriteResult("Claude Desktop", "updated" if old is not None else "wrote", path)
    except (OSError, ValueError, TypeError, AttributeError):
        return WriteResult("Claude Desktop", "error", path,
                           "Could not safely update Desktop config")


def _claude_binary() -> str | None:
    """Claude Code's CLI on PATH, if installed (one place, so tests can hide it)."""
    return shutil.which("claude")


def register_claude_code(env: McpEnv, *, force=False, create=False) -> WriteResult:
    path = code_config_path()
    binary = _claude_binary()
    if not binary and not (force or create or path.exists() or (Path.home() / ".claude").exists()):
        return WriteResult("Claude Code", "not_installed", path)
    entry = _entry(env, "claude")
    try:
        data, original = read_json_snapshot(path)
        old = data.get("mcpServers", {}).get("quorus")
        detail = kept_identity(old, entry, "claude", force=force)
        if detail:
            return WriteResult("Claude Code", "kept", path, detail)
        if not binary:
            return WriteResult("Claude Code", "manual", path, _manual(entry))
        if old == entry:
            return WriteResult("Claude Code", "kept", path,
                               f"kept existing @{entry['env']['QUORUS_INSTANCE_NAME']} on "
                               f"{safe_relay(env.relay_url)} (already configured)")
        if snapshot(path) != original:
            raise ValueError("Config changed during registration")
        # Claude owns ~/.claude.json and its live state. Back it up, then let
        # its supported CLI handle locking and writes; never serialize it here.
        backup(path, original)
        def run(args):
            return subprocess.run([binary, "mcp", *args], capture_output=True, text=True,
                                  timeout=30, check=True)
        if old is not None:
            run(["remove", "quorus", "-s", "user"])
        try:
            run(["add-json", "quorus", json.dumps(entry), "-s", "user"])
        except (OSError, subprocess.SubprocessError):
            if old is not None:
                # Restore only our MCP entry, through Claude's writer, not a
                # stale whole-file snapshot that would clobber newer live state.
                run(["add-json", "quorus", json.dumps(old), "-s", "user"])
            raise
        return WriteResult("Claude Code", "updated" if old is not None else "wrote", path)
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        return WriteResult("Claude Code", "error", path,
                           "Claude Code registration failed. " + _manual(entry))
