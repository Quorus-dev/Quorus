"""Wake contract for reflexd — what a woken agent gets so it can actually work.

Proven missing in a real two-agent run (2026-10-08, claude 2.1.295 + codex
0.160.1) — every gap below made a woken agent unable to act like a teammate:

1. **No Quorus tools.** The woken harness inherited whatever MCP config the
   user had (on the founder's laptop: a dead venv + a dead relay), so the
   agent could not post a plan, hand off to a teammate, or report progress.
   :func:`quorus_mcp_spec` builds an MCP server entry bound to the SAME relay
   and identity as the daemon, injected per spawn.
2. **Permissions are deliberately NOT widened here.** A woken agent runs with
   exactly the permissions its owner configured for that CLI (Claude Code
   settings, ``~/.codex/config.toml``). Granting more is the owner's call.
3. **Codex replies were always dropped.** Real ``codex exec --json`` nests the
   text in ``{"type":"item.completed","item":{"type":"agent_message",
   "text":...}}``; the old parser read top-level keys only.
4. **The prompt forbade work.** Mentions said "reply in 1-3 lines and do not
   run tools" — a mention asking for a fix could never be done.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# Codex sandbox for woken agents. Default "" = whatever the owner's
# ~/.codex/config.toml says (headless `codex exec` is read-only by default,
# so codex can review but not write). The owner opts in per daemon, e.g.
# REFLEXD_CODEX_SANDBOX=workspace-write — writes limited to the bound repo.
# Never set from code; scripts/dogfood.sh sets it when the owner asks.
CODEX_SANDBOX = os.environ.get("REFLEXD_CODEX_SANDBOX", "").strip()
_CODEX_SANDBOXES = {"read-only", "workspace-write"}

# Stop agent↔agent ping-pong: once this many consecutive room messages are
# all from agents (no human in between), agents stop waking on each other.
MAX_AGENT_CHAIN = int(os.environ.get("REFLEXD_MAX_AGENT_CHAIN", "8"))


def quorus_mcp_spec(
    *, relay_url: str, api_key: str, participant: str, legacy: bool,
    config_dir: Path | None = None,
) -> dict[str, Any]:
    """MCP server entry giving the woken agent Quorus tools as *participant*.

    Runs the MCP server from the daemon's own interpreter, so it is the same
    installed version and never depends on PATH or the user's global config.
    SSE is off: the woken agent must not consume the daemon's push stream.
    """
    env = {
        "QUORUS_RELAY_URL": relay_url,
        "QUORUS_INSTANCE_NAME": participant,
        "SSE_ENABLED": "false",
    }
    env["QUORUS_RELAY_SECRET" if legacy else "QUORUS_API_KEY"] = api_key
    if config_dir is not None:
        env["QUORUS_CONFIG_DIR"] = str(config_dir)
    return {
        "command": sys.executable,
        "args": ["-m", "quorus_mcp.server"],
        "env": env,
    }


_SECRET_KEYS = ("QUORUS_API_KEY", "QUORUS_RELAY_SECRET")


def write_claude_mcp_config(spec: dict[str, Any], path: Path) -> Path:
    """Write the ``--mcp-config`` file (0600). A file, not inline JSON, so the
    agent's API key never appears in ``ps`` output."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"mcpServers": {"quorus": spec}}, f)
    return path


def claude_wake_flags(config_path: Path | None) -> list[str]:
    """Extra ``claude --print`` flags: the Quorus MCP server for this identity."""
    return ["--mcp-config", str(config_path)] if config_path else []


def _toml_str(value: str) -> str:
    # JSON string escaping is valid TOML basic-string escaping for our inputs.
    return json.dumps(value)


def codex_wake_flags(spec: dict[str, Any] | None) -> list[str]:
    """Extra ``codex exec`` flags: the Quorus MCP server via ``-c`` overrides.

    ``-c`` overrides beat ``~/.codex/config.toml``, so a stale user entry for
    ``mcp_servers.quorus`` (dead venv, dead relay) cannot shadow ours.
    """
    flags: list[str] = []
    if CODEX_SANDBOX in _CODEX_SANDBOXES:  # danger-full-access deliberately unsupported
        flags += ["-s", CODEX_SANDBOX]
    if spec is None:
        return flags
    flags += [
        "-c", f"mcp_servers.quorus.command={_toml_str(spec['command'])}",
        "-c", "mcp_servers.quorus.args="
        + "[" + ",".join(_toml_str(a) for a in spec["args"]) + "]",
    ]
    for key, val in spec["env"].items():
        if key in _SECRET_KEYS:
            continue  # forwarded from the process env, never put in argv
        flags += ["-c", f"mcp_servers.quorus.env.{key}={_toml_str(val)}"]
    secrets = [k for k in _SECRET_KEYS if k in spec["env"]]
    if secrets:
        flags += ["-c", "mcp_servers.quorus.env_vars=["
                  + ",".join(_toml_str(k) for k in secrets) + "]"]
    return flags


# Daemon-private env that must NOT leak into the woken agent: the CLI and
# MCP config loader read unprefixed API_KEY/RELAY_SECRET first, so the
# daemon's legacy secret arrived as an "API key" and every room post from
# the agent 401'd "Invalid API key format" (live run, 2026-10-08).
_DAEMON_ONLY = ("API_KEY", "RELAY_URL", "RELAY_SECRET", "INSTANCE_NAME",
                "QUORUS_API_KEY", "QUORUS_RELAY_SECRET", "QUORUS_PROFILE")


def _write_private_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def write_agent_config_dir(
    root: Path, *, relay_url: str, api_key: str, participant: str, legacy: bool,
) -> Path:
    """A config dir holding ONLY this agent's identity (0600 files).

    Pointing the woken agent's ``QUORUS_CONFIG_DIR`` here means ``quorus say``
    and the MCP server post as the agent, never as whatever profile the
    human has active.
    """
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    profile = {"instance_name": participant, "relay_url": relay_url}
    profile["relay_secret" if legacy else "api_key"] = api_key
    _write_private_json(root / "profiles" / f"{participant}.json", profile)
    _write_private_json(root / "config.json",
                        {"current": participant, "profiles": [participant]})
    return root


def wake_env(
    spec: dict[str, Any] | None, config_dir: Path | None = None,
) -> dict[str, str]:
    """Environment for the woken harness.

    Puts the daemon's venv ``bin`` first on PATH so QOD's ``quorus say`` works
    inside the agent; strips daemon-only credentials; binds the agent's own
    identity via its private config dir plus ``QUORUS_*`` vars.
    """
    env = {k: v for k, v in os.environ.items() if k not in _DAEMON_ONLY}
    venv_bin = str(Path(sys.executable).parent)
    env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")
    if spec is not None:
        env.update(spec["env"])
        env.pop("SSE_ENABLED", None)
    if config_dir is not None:
        env["QUORUS_CONFIG_DIR"] = str(config_dir)
    return env


def parse_codex_stream(out: str) -> tuple[str, str | None]:
    """Parse ``codex exec --json`` NDJSON → ``(reply_text, thread_id)``.

    Current shape (verified codex-cli 0.160.1)::

        {"type":"thread.started","thread_id":"..."}
        {"type":"item.completed","item":{"type":"agent_message","text":"..."}}

    The reply is the LAST agent message (earlier ones are progress narration).
    Older top-level ``delta``/``content``/``text`` events still concatenate as
    a fallback for pre-0.130 CLIs.
    """
    messages: list[str] = []
    legacy: list[str] = []
    thread_id: str | None = None
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        tid = ev.get("thread_id")
        if isinstance(tid, str) and tid:
            thread_id = tid
        item = ev.get("item")
        if ev.get("type") == "item.completed" and isinstance(item, dict):
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                messages.append(item["text"])
            continue
        delta = ev.get("delta") or ev.get("content") or ev.get("text")
        if isinstance(delta, str):
            legacy.append(delta)
    if messages:
        return messages[-1].strip(), thread_id
    return "".join(legacy).strip(), thread_id


def trailing_agent_chain(history: list[dict[str, Any]], is_agent: Any) -> int:
    """How many messages at the end of *history* came from agents in a row."""
    n = 0
    for m in reversed(history):
        if is_agent(m.get("from_name") or m.get("sender") or ""):
            n += 1
        else:
            break
    return n


def wake_instructions(
    *, participant: str, room: str, sender: str, kind: str,
    teammates: list[str], has_workspace: bool,
) -> str:
    """The Wake Intent block — tells the agent it may (and should) do work."""
    others = ", ".join(f"@{t}" for t in teammates if t != participant) or "(none)"
    if kind == "open_todo":
        opener = (f"You picked up an open task posted by `{sender}` in room "
                  f"`{room}`. You own it now - do it end to end.")
    elif kind == "mention":
        opener = f"`{sender}` @-mentioned you in room `{room}`."
    else:
        opener = f"`{sender}` asked something in room `{room}` and you won the pick."
    ws = ("You are running inside the room's bound workspace: make real changes, "
          "run the tests, and commit when the work is done."
          if has_workspace else
          "No workspace is bound to this room on this host: answer and plan, but "
          "for code changes ask a human to run `quorus room bind "
          f"{room} <repo-path>`.")
    return (
        f"You are `{participant}`, an autonomous teammate. {opener}\n"
        "- If the message asks for work, do the work now rather than stopping "
        "at a plan. If a tool you need is not permitted, say exactly what is "
        "blocked so a human can decide.\n"
        "- If it is just a question, answer it in 1-3 sentences.\n"
        f"- {ws}\n"
        "- For anything longer than a few minutes, post a 1-line plan first: "
        f"`quorus say {room} \"plan: ...\"` (on your PATH, posts as you; "
        "plan lines never wake anyone) or "
        f"mcp__quorus__send_room_message(room=\"{room}\").\n"
        f"- Teammates in this room: {others}. To hand work to one, @-mention "
        "them by name with a concrete request (e.g. review a commit, take a "
        "subtask). They wake automatically. Never @-mention someone just to say "
        "thanks or acknowledge.\n"
        "- Your FINAL message is posted to the room for you automatically. Do not "
        "also post it with tools. Make it the result: what changed (files, commit "
        "hash, test status), or the answer. Keep it under 8 lines.\n"
    )
