"""Private config writes: preserve identity, back up, then replace atomically."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def snapshot(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def read_json_snapshot(path: Path) -> tuple[dict, bytes | None]:
    raw = snapshot(path)
    data = json.loads(raw or b"{}")
    if not isinstance(data, dict):
        raise ValueError("MCP config must be a JSON object")
    return data, raw


def backup(path: Path, raw: bytes | None) -> None:
    if raw is None:
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = path.with_name(f"{path.name}.bak-quorus-{stamp}")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def atomic_text(path: Path, text: str, *, original: bytes | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if snapshot(path) != original:
        raise ValueError("Config changed during registration; retry instead of overwriting it")
    backup(path, original)
    fd, tmp = tempfile.mkstemp(prefix=".quorus-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if snapshot(path) != original:
            raise ValueError("Config changed during registration; retry instead of overwriting it")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def safe_relay(value: str) -> str:
    """Relay labels must not echo URL credentials or token query strings."""
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        port = f":{url.port}" if url.port else ""
        return urlunsplit((url.scheme, host + port, url.path, "", "")) or "unknown relay"
    except ValueError:
        return "unknown relay"


def kept_identity(old: object, new: dict, platform: str, *, force: bool) -> str:
    """Unknown identity fields count as different, not permission to replace."""
    if old is None or force:
        return ""
    old_env = old.get("env", {}) if isinstance(old, dict) else {}
    if not isinstance(old_env, dict):
        old_env = {}
    fresh = new.get("env", {})
    fields = ("QUORUS_INSTANCE_NAME", "QUORUS_RELAY_URL")
    if all(old_env.get(k) == fresh.get(k) for k in fields):
        return ""
    name = old_env.get("QUORUS_INSTANCE_NAME") or "unknown"
    relay = safe_relay(str(old_env.get("QUORUS_RELAY_URL") or ""))
    return (f"kept existing @{name} on {relay}; "
            f"quorus connect {platform} <room> <name> --force to replace")
