"""Per-client MCP config writers.

One writer per supported agent. Each ``register_*`` function:

* Is idempotent — if the ``quorus`` entry already exists in the
  target config, a different identity is kept unless force is explicit.
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
import re
import shutil
from pathlib import Path
from typing import Any, Callable

from quorus_cli.mcp_claude import register_claude_code, register_claude_desktop
from quorus_cli.mcp_config_io import atomic_text, kept_identity, read_json_snapshot, snapshot
from quorus_cli.mcp_writer_types import McpEnv, WriteResult

# ---------------------------------------------------------------------------
# Shared JSON helpers
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict[str, Any]:
    return read_json_snapshot(path)[0]


def _atomic_write_json(path: Path, data: dict[str, Any], *, original: bytes | None) -> None:
    atomic_text(path, json.dumps(data, indent=2) + "\n", original=original)


def _write_json_mcp_entry(path, key, entry, container="mcpServers", *,
                          force=False, platform="quorus"):
    existing, original = read_json_snapshot(path)
    servers = existing.setdefault(container, {})
    old = servers.get(key)
    detail = kept_identity(old, entry, platform, force=force)
    if detail:
        return "kept", detail
    servers[key] = entry
    _atomic_write_json(path, existing, original=original)
    return ("updated" if old is not None else "wrote"), ""


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
    force: bool = False,
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
        status, detail = _write_json_mcp_entry(config_path, "quorus", entry,
                                               force=force, platform=platform_key)
    except Exception as exc:  # pragma: no cover - defensive
        return WriteResult(platform, "error", config_path, str(exc))
    return WriteResult(platform, status, config_path, detail)


def register_cursor(env: McpEnv, *, force: bool = False, create: bool = False) -> WriteResult:
    return _register_json_mcpservers(
        "Cursor", "cursor",
        Path.home() / ".cursor" / "mcp.json",
        env, require_existing=not (force or create), force=force,
    )


def register_windsurf(env: McpEnv, *, force: bool = False, create: bool = False) -> WriteResult:
    """Tier-B writer (MCP-only, manual-trigger).

    Windsurf has no canonical headless CLI as of 2026-05 — Codeium's vendor
    docs (https://docs.windsurf.com/command/windsurf-overview) describe only
    IDE-embedded usage. Quorus registers the MCP server here so Windsurf can
    SEND messages via ``quorus inbox`` / ``quorus say`` MCP tools while a
    human is driving the IDE — but reflexd cannot wake Windsurf to REPLY
    on @-mention. See docs/HARNESS_TIERS.md for the full disposition memo.

    If Codeium ships an official headless ``windsurf --print``-style flag,
    promote this to tier-A by adding ``windsurf`` to ``_HARNESS_SUFFIXES``,
    a ``build_windsurf_argv`` in reflexd, and contract tests.
    """
    return _register_json_mcpservers(
        "Windsurf", "windsurf",
        Path.home() / ".codeium" / "windsurf" / "mcp_config.json",
        env, require_existing=not (force or create), force=force,
    )


def register_gemini_cli(env: McpEnv, *, force: bool = False, create: bool = False) -> WriteResult:
    return _register_json_mcpservers(
        "Gemini CLI", "gemini",
        Path.home() / ".gemini" / "settings.json",
        env, require_existing=not (force or create), force=force,
    )


def register_cline(env: McpEnv, *, force: bool = False, create: bool = False) -> WriteResult:
    """Tier-A writer (fully proactive after wave-7).

    Cline ships a standalone CLI (``npm install -g cline``, then
    ``cline "<task>"``) in preview as of 2026-05, OAuth-based via
    ``cline auth`` (https://docs.cline.bot/getting-started/installing-cline).
    Reflexd routes participants suffixed ``-cline`` through
    :func:`reflexd.build_cline_argv`. Auth flows through whatever the user
    set with ``cline auth`` — Quorus never sees provider keys.

    The MCP block is written to ``~/.cline/mcp.json``. Cline's VS Code
    extension also reads from VS Code globalStorage, but the standalone
    CLI uses this canonical path so we register it for the headless flow.
    """
    candidate = Path.home() / ".cline" / "mcp.json"
    return _register_json_mcpservers(
        "Cline", "cline", candidate, env, require_existing=not (force or create), force=force,
    )


def register_continue(env: McpEnv, *, force: bool = False, create: bool = False) -> WriteResult:
    """Continue uses ``~/.continue/config.json`` with a nested
    ``experimental.modelContextProtocolServers`` list rather than
    ``mcpServers``.  We hand-roll that shape here.
    """
    path = Path.home() / ".continue" / "config.json"
    if not path.exists() and not (force or create):
        return WriteResult("Continue", "not_installed", path)
    try:
        data, original = read_json_snapshot(path)
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
            detail = kept_identity(srv.get("transport"), entry["transport"], "continue",
                                   force=force)
            if detail:
                return WriteResult("Continue", "kept", path, detail)
            servers[i] = entry
            already = True
            break
    if not already:
        servers.append(entry)
    try:
        _atomic_write_json(path, data, original=original)
    except Exception as exc:  # pragma: no cover
        return WriteResult("Continue", "error", path, str(exc))
    return WriteResult(
        "Continue", "updated" if already else "wrote", path,
    )


def register_opencode(env: McpEnv, *, force: bool = False, create: bool = False) -> WriteResult:
    """Tier-A writer (fully proactive after wave-7).

    Opencode ships a real headless CLI (``opencode run "<prompt>"``) per the
    vendor docs (https://opencode.ai/docs/cli/). Auth is OAuth-friendly via
    ``opencode auth login``, supporting 75+ providers — Quorus never sees
    provider keys. Reflexd routes participants suffixed ``-opencode`` through
    :func:`reflexd.build_opencode_argv`.

    Schema differs from the standard ``mcpServers`` shape:
    ``mcp.<name> = {type, command[], enabled}``. Two standard paths — XDG
    first, then ``~/.opencode.json``. We write to the first that exists, or
    create the XDG path on force.
    """
    candidates = [
        Path.home() / ".config" / "opencode" / "opencode.json",
        Path.home() / ".opencode.json",
    ]
    target = next((p for p in candidates if p.exists()), None)
    if target is None:
        if not (force or create):
            return WriteResult("Opencode", "not_installed", candidates[0])
        target = candidates[0]
    try:
        data, original = read_json_snapshot(target)
        if original is None:
            data["$schema"] = "https://opencode.ai/config.json"
    except (json.JSONDecodeError, ValueError) as exc:
        return WriteResult("Opencode", "error", target, str(exc))
    mcp_block = data.setdefault("mcp", {})
    already = "quorus" in mcp_block
    detail = kept_identity(mcp_block.get("quorus"), _standard_mcp_entry(env, "opencode"),
                           "opencode", force=force)
    if detail:
        return WriteResult("Opencode", "kept", target, detail)
    mcp_block["quorus"] = {
        "type": "local",
        "command": [env.command, *env.args],
        "enabled": True,
        "env": env.env_block("opencode"),
    }
    try:
        _atomic_write_json(target, data, original=original)
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
        try:
            import tomllib
        except ModuleNotFoundError:
            import tomli as tomllib
        return tomllib.loads(repaired)


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


def register_codex(env: McpEnv, *, force: bool = False, create: bool = False) -> WriteResult:
    """Write an ``[mcp_servers.quorus]`` entry to ``~/.codex/config.toml``."""
    path = Path.home() / ".codex" / "config.toml"
    codex_installed = path.exists() or shutil.which("codex") is not None
    if not codex_installed and not (force or create):
        return WriteResult("Codex CLI", "not_installed", path)
    try:
        original = snapshot(path)
        data = _read_codex_toml(path)
    except Exception as exc:
        return WriteResult("Codex CLI", "error", path, str(exc))
    servers = data.setdefault("mcp_servers", {})
    already = "quorus" in servers
    detail = kept_identity(servers.get("quorus"), _standard_mcp_entry(env, "codex"),
                           "codex", force=force)
    if detail:
        return WriteResult("Codex CLI", "kept", path, detail)
    servers["quorus"] = {
        "command": env.command,
        "args": list(env.args),
        "env": env.env_block("codex"),
    }
    try:
        atomic_text(path, _render_codex_toml(data), original=original)
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


def register_all(env: McpEnv, *, force: bool = False) -> list[WriteResult]:
    """Run every writer in auto-detect mode. Returns one result per platform."""
    results: list[WriteResult] = []
    for _key, writer in ALL_WRITERS:
        try:
            results.append(writer(env, force=force))
        except Exception as exc:  # pragma: no cover
            # A single writer exploding should never take the whole
            # registration down — record and keep going.
            results.append(WriteResult("?", "error", None, str(exc)))
    return results


def register_one(key: str, env: McpEnv, *, force: bool = False) -> WriteResult:
    """Create a client's config, preserving a different identity unless forced."""
    writer = WRITER_BY_KEY.get(key)
    if writer is None:
        return WriteResult(
            key, "error", None,
            f"no writer for platform '{key}'",
        )
    return writer(env, force=force, create=True)


def report_kept(results: list[WriteResult], console) -> None:
    for result in results:
        if result.status in ("kept", "manual"):
            console.print(f"{result.platform}: {result.detail}", markup=False, highlight=False)
