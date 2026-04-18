"""Per-client MCP config writers.

One writer per supported agent. Each ``register_*`` function:

* Is idempotent — if the ``quorus`` entry already exists in the
  target config, it updates the env block in place rather than
  duplicating the server.
* Preserves every other key / server the user has configured.
* Writes atomically (temp file + rename) so a crash mid-write can't
  corrupt the user's agent.
* Returns a :class:`WriteResult` describing what happened — ``"wrote"``,
  ``"updated"``, ``"not_installed"``, or ``"error"``.

The shared input is a :class:`McpEnv` bundle: the relay URL, auth
material, and the per-agent display name the MCP server should
announce itself as. Every writer injects those into the config's env
block so each platform (Claude Code, Cursor, Codex, …) can be a
distinct participant in the same room (``@arav-claude``, ``@arav-codex``,
etc.) without a shared-identity collision.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

WriteStatus = Literal["wrote", "updated", "not_installed", "error"]


@dataclass
class WriteResult:
    """Outcome of registering the Quorus MCP server with one client."""

    platform: str
    status: WriteStatus
    path: Path | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("wrote", "updated")


@dataclass
class McpEnv:
    """Everything a writer needs to produce a working MCP entry.

    ``instance_name_base`` is the human's name (e.g. ``arav``). Each
    writer derives its own agent identity by suffixing the platform
    key (``arav-claude``, ``arav-codex``) so the relay sees each
    client as a separate participant.

    If ``per_platform_keys`` is provided, each platform gets its own
    API key (fetched from ``/v1/auth/register-agent``). Otherwise
    all platforms share the same key (legacy behavior, causes identity
    mismatch errors on the relay).
    """

    command: str
    args: list[str]
    relay_url: str
    api_key: str = ""
    relay_secret: str = ""
    instance_name_base: str = ""
    per_platform_keys: dict[str, str] | None = None  # platform_key -> api_key

    def agent_identity(self, platform_key: str) -> str:
        base = self.instance_name_base.strip()
        if not base:
            return platform_key
        if base.endswith(f"-{platform_key}"):
            return base
        return f"{base}-{platform_key}"

    def env_block(self, platform_key: str) -> dict[str, str]:
        env: dict[str, str] = {
            "QUORUS_RELAY_URL": self.relay_url,
            "QUORUS_INSTANCE_NAME": self.agent_identity(platform_key),
        }
        # Use per-platform key if available, else fall back to shared key
        platform_api_key = (
            self.per_platform_keys.get(platform_key)
            if self.per_platform_keys
            else None
        )
        if platform_api_key:
            env["QUORUS_API_KEY"] = platform_api_key
        elif self.api_key:
            env["QUORUS_API_KEY"] = self.api_key
        elif self.relay_secret:
            env["QUORUS_RELAY_SECRET"] = self.relay_secret
        return env


# ---------------------------------------------------------------------------
# Shared JSON helpers
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text() or "{}")
    except (json.JSONDecodeError, ValueError):
        # The user's config is malformed — don't silently nuke it.
        # Raise so the calling writer can report the platform as
        # "error" instead of steam-rolling hand-edited JSON.
        raise


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a tmp file in the same dir, then rename — rename is
    # atomic on POSIX, so a crash mid-write leaves the old config.
    fd, tmp_path = tempfile.mkstemp(prefix=".quorus-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, path)
    except BaseException:
        # Don't leave stray tmp files if the rename fails.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _write_json_mcp_entry(
    path: Path,
    key: str,
    entry: dict[str, Any],
    container: str = "mcpServers",
) -> WriteStatus:
    """Merge ``{container: {key: entry}}`` into an existing JSON file.

    Returns ``"updated"`` if the server was already present (we merge
    the fresh entry's env), ``"wrote"`` if it was brand-new.
    """
    try:
        existing = _read_json(path)
    except (json.JSONDecodeError, ValueError):
        return "error"
    existing.setdefault(container, {})
    already = key in existing[container]
    existing[container][key] = entry
    _atomic_write_json(path, existing)
    return "updated" if already else "wrote"


# ---------------------------------------------------------------------------
# Writers — JSON-based clients (the common shape)
# ---------------------------------------------------------------------------
#
# Every writer below takes an ``McpEnv`` and returns a ``WriteResult``.
# They're grouped by config layout, not by vendor, so the same helper
# serves Claude Code / Claude Desktop / Cursor / Windsurf / Gemini CLI
# which all converged on the ``{"mcpServers": {name: {...}}}`` schema.
#

def _standard_mcp_entry(env: McpEnv, platform_key: str) -> dict[str, Any]:
    return {
        "type": "stdio",
        "command": env.command,
        "args": list(env.args),
        "env": env.env_block(platform_key),
    }


def _register_json_mcpservers(
    platform: str,
    platform_key: str,
    config_path: Path,
    env: McpEnv,
    *,
    require_existing: bool = True,
) -> WriteResult:
    """Writer for every client using the standard ``mcpServers`` JSON shape.

    When ``require_existing`` is True (the default for auto-register), we
    only touch clients that already have a config file at the expected
    path — a signal the user has actually installed them. The explicit
    ``quorus connect <platform>`` flow flips this to False to force-create.
    """
    if require_existing and not config_path.exists():
        return WriteResult(platform, "not_installed", config_path)
    entry = _standard_mcp_entry(env, platform_key)
    try:
        status = _write_json_mcp_entry(config_path, "quorus", entry)
    except Exception as exc:  # pragma: no cover - defensive
        return WriteResult(platform, "error", config_path, str(exc))
    return WriteResult(platform, status, config_path)


def register_claude_code(env: McpEnv, *, force: bool = False) -> WriteResult:
    return _register_json_mcpservers(
        "Claude Code", "claude",
        Path.home() / ".claude" / "settings.json",
        env, require_existing=not force,
    )


def register_claude_desktop(env: McpEnv, *, force: bool = False) -> WriteResult:
    # The old `~/.claude.json` path is still the one we've been writing
    # historically — keeping for compatibility.
    return _register_json_mcpservers(
        "Claude Desktop", "claude-desktop",
        Path.home() / ".claude.json",
        env, require_existing=not force,
    )


def register_cursor(env: McpEnv, *, force: bool = False) -> WriteResult:
    return _register_json_mcpservers(
        "Cursor", "cursor",
        Path.home() / ".cursor" / "mcp.json",
        env, require_existing=not force,
    )


def register_windsurf(env: McpEnv, *, force: bool = False) -> WriteResult:
    return _register_json_mcpservers(
        "Windsurf", "windsurf",
        Path.home() / ".codeium" / "windsurf" / "mcp_config.json",
        env, require_existing=not force,
    )


def register_gemini_cli(env: McpEnv, *, force: bool = False) -> WriteResult:
    return _register_json_mcpservers(
        "Gemini CLI", "gemini",
        Path.home() / ".gemini" / "settings.json",
        env, require_existing=not force,
    )


def register_cline(env: McpEnv, *, force: bool = False) -> WriteResult:
    # Cline stores its MCP block in VS Code's globalStorage, under a
    # deeply-nested cline-extension path that also varies by VS Code
    # flavor. Rather than probing 5 paths we leave Cline to the prose
    # fallback — but expose a writer so the connect command can do a
    # force-create when the user points us at a path explicitly.
    candidate = Path.home() / ".cline" / "mcp.json"
    return _register_json_mcpservers(
        "Cline", "cline", candidate, env, require_existing=not force,
    )


def register_continue(env: McpEnv, *, force: bool = False) -> WriteResult:
    """Continue uses ``~/.continue/config.json`` with a nested
    ``experimental.modelContextProtocolServers`` list rather than
    ``mcpServers``.  We hand-roll that shape here.
    """
    path = Path.home() / ".continue" / "config.json"
    if not path.exists() and not force:
        return WriteResult("Continue", "not_installed", path)
    try:
        data = _read_json(path)
    except (json.JSONDecodeError, ValueError) as exc:
        return WriteResult("Continue", "error", path, str(exc))
    experimental = data.setdefault("experimental", {})
    servers = experimental.setdefault("modelContextProtocolServers", [])
    entry = {
        "transport": {
            "type": "stdio",
            "command": env.command,
            "args": list(env.args),
            "env": env.env_block("continue"),
        },
        "name": "quorus",
    }
    # Replace any existing quorus entry, don't duplicate.
    already = False
    for i, srv in enumerate(list(servers)):
        if isinstance(srv, dict) and srv.get("name") == "quorus":
            servers[i] = entry
            already = True
            break
    if not already:
        servers.append(entry)
    try:
        _atomic_write_json(path, data)
    except Exception as exc:  # pragma: no cover
        return WriteResult("Continue", "error", path, str(exc))
    return WriteResult(
        "Continue", "updated" if already else "wrote", path,
    )


def register_opencode(env: McpEnv, *, force: bool = False) -> WriteResult:
    """Opencode uses a different schema: ``mcp.<name> = {type, command[], enabled}``.

    Two standard paths — XDG first, then ``~/.opencode.json``. We
    write to the first that exists, or create the XDG path on force.
    """
    candidates = [
        Path.home() / ".config" / "opencode" / "opencode.json",
        Path.home() / ".opencode.json",
    ]
    target = next((p for p in candidates if p.exists()), None)
    if target is None:
        if not force:
            return WriteResult("Opencode", "not_installed", candidates[0])
        target = candidates[0]
    try:
        data = _read_json(target) if target.exists() else {
            "$schema": "https://opencode.ai/config.json",
        }
    except (json.JSONDecodeError, ValueError) as exc:
        return WriteResult("Opencode", "error", target, str(exc))
    mcp_block = data.setdefault("mcp", {})
    already = "quorus" in mcp_block
    mcp_block["quorus"] = {
        "type": "local",
        "command": [env.command, *env.args],
        "enabled": True,
        "env": env.env_block("opencode"),
    }
    try:
        _atomic_write_json(target, data)
    except Exception as exc:  # pragma: no cover
        return WriteResult("Opencode", "error", target, str(exc))
    return WriteResult(
        "Opencode", "updated" if already else "wrote", target,
    )


# ---------------------------------------------------------------------------
# Codex CLI — TOML config at ~/.codex/config.toml
# ---------------------------------------------------------------------------

def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import tomllib  # stdlib since 3.11
    except ModuleNotFoundError:
        try:
            import tomli as tomllib  # fallback for Python < 3.11
        except ModuleNotFoundError:
            return {}
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except Exception:
        raise


def _repair_codex_toml(text: str) -> str:
    """Repair the known invalid path-table form emitted by older Codex configs.

    Example:
    ``[projects./Users/arav/Desktop]`` -> ``[projects."/Users/arav/Desktop"]``
    """
    fixed_lines: list[str] = []
    changed = False
    pattern = re.compile(r'^\[projects\.(/.+)\]$')
    for line in text.splitlines():
        match = pattern.match(line.strip())
        if match:
            path_key = match.group(1).replace("\\", "\\\\").replace('"', '\\"')
            fixed_lines.append(f'[projects."{path_key}"]')
            changed = True
            continue
        fixed_lines.append(line)
    if not changed:
        return text
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(fixed_lines) + suffix


def _read_codex_toml(path: Path) -> dict[str, Any]:
    """Read Codex TOML with a targeted repair path for older invalid headers."""
    try:
        return _read_toml(path)
    except Exception:
        if not path.exists():
            raise
        raw = path.read_text()
        repaired = _repair_codex_toml(raw)
        if repaired == raw:
            raise
        path.write_text(repaired)
        return _read_toml(path)


def _toml_escape(s: str) -> str:
    """Minimal TOML-safe string escape: double quotes + backslash."""
    return s.replace("\\", "\\\\").replace("\"", "\\\"")


def _format_toml_value(v: Any) -> str:
    if isinstance(v, str):
        return f"\"{_toml_escape(v)}\""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_format_toml_value(x) for x in v) + "]"
    # Nested tables aren't used in our emission path — we render
    # them as their own [section.header] below.
    return f"\"{_toml_escape(str(v))}\""


def _toml_key(k: str) -> str:
    """Return a TOML-safe key segment — bare if safe, quoted otherwise."""
    import re as _re
    if _re.fullmatch(r"[A-Za-z0-9_-]+", k):
        return k
    escaped = k.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_codex_toml(data: dict[str, Any]) -> str:
    """Round-trip what we know about Codex config + our additions to TOML.

    Not a general TOML serializer — just enough to emit the shape
    Codex expects: top-level keys + nested ``[section]`` / ``[sub.key]``
    tables. Nested env maps render as ``[mcp_servers.quorus.env]``.
    Keys containing special characters (e.g. ``/`` in file paths) are
    quoted so the output is valid TOML.
    """
    notice = data.get("notice")
    if isinstance(notice, dict):
        migrations = notice.get("model_migrations")
        if isinstance(migrations, dict):
            def _flatten(prefix: str, value: Any, out: dict[str, Any]) -> None:
                if isinstance(value, dict):
                    for child_key, child_value in value.items():
                        next_prefix = f"{prefix}.{child_key}" if prefix else child_key
                        _flatten(next_prefix, child_value, out)
                else:
                    out[prefix] = value

            flattened: dict[str, Any] = {}
            _flatten("", migrations, flattened)
            notice["model_migrations"] = flattened

    lines: list[str] = []
    top_scalars: dict[str, Any] = {}
    top_tables: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, dict):
            top_tables[k] = v
        else:
            top_scalars[k] = v
    for k, v in top_scalars.items():
        lines.append(f"{_toml_key(k)} = {_format_toml_value(v)}")
    if top_scalars and top_tables:
        lines.append("")

    def _emit_table(prefix: str, table: dict[str, Any]) -> None:
        scalars: dict[str, Any] = {}
        subs: dict[str, Any] = {}
        for k, v in table.items():
            if isinstance(v, dict):
                subs[k] = v
            else:
                scalars[k] = v
        header = f"[{prefix}]"
        if scalars or not subs:
            lines.append(header)
            for k, v in scalars.items():
                lines.append(f"{_toml_key(k)} = {_format_toml_value(v)}")
            lines.append("")
        for k, v in subs.items():
            _emit_table(f"{prefix}.{_toml_key(k)}", v)

    for k, v in top_tables.items():
        _emit_table(_toml_key(k), v)
    return "\n".join(lines).rstrip() + "\n"


def register_codex(env: McpEnv, *, force: bool = False) -> WriteResult:
    """Write an ``[mcp_servers.quorus]`` entry to ``~/.codex/config.toml``."""
    path = Path.home() / ".codex" / "config.toml"
    codex_installed = path.exists() or shutil.which("codex") is not None
    if not codex_installed and not force:
        return WriteResult("Codex CLI", "not_installed", path)
    try:
        data = _read_codex_toml(path)
    except Exception as exc:
        return WriteResult("Codex CLI", "error", path, str(exc))
    servers = data.setdefault("mcp_servers", {})
    already = "quorus" in servers
    servers["quorus"] = {
        "command": env.command,
        "args": list(env.args),
        "env": env.env_block("codex"),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write, same tmp+rename dance as JSON.
        fd, tmp_path = tempfile.mkstemp(
            prefix=".quorus-", dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "w") as f:
                f.write(_render_codex_toml(data))
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as exc:  # pragma: no cover
        return WriteResult("Codex CLI", "error", path, str(exc))
    return WriteResult(
        "Codex CLI", "updated" if already else "wrote", path,
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

WriterFn = Callable[..., WriteResult]

# Ordered so the registration summary reads in a sensible "most common
# first" order for users.
ALL_WRITERS: list[tuple[str, WriterFn]] = [
    ("claude", register_claude_code),
    ("claude-desktop", register_claude_desktop),
    ("cursor", register_cursor),
    ("codex", register_codex),
    ("gemini", register_gemini_cli),
    ("windsurf", register_windsurf),
    ("opencode", register_opencode),
    ("continue", register_continue),
    ("cline", register_cline),
]


WRITER_BY_KEY: dict[str, WriterFn] = {k: fn for k, fn in ALL_WRITERS}


def register_all(env: McpEnv) -> list[WriteResult]:
    """Run every writer in auto-detect mode. Returns one result per platform."""
    results: list[WriteResult] = []
    for _key, writer in ALL_WRITERS:
        try:
            results.append(writer(env))
        except Exception as exc:  # pragma: no cover
            # A single writer exploding should never take the whole
            # registration down — record and keep going.
            results.append(WriteResult("?", "error", None, str(exc)))
    return results


def register_one(key: str, env: McpEnv, *, force: bool = True) -> WriteResult:
    """Force-write a specific client's config, regardless of install detection."""
    writer = WRITER_BY_KEY.get(key)
    if writer is None:
        return WriteResult(
            key, "error", None,
            f"no writer for platform '{key}'",
        )
    return writer(env, force=force)
