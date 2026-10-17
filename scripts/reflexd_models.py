"""Wake parity for any-model harnesses: Gemini CLI and opencode.

Claude Code and Codex get the Quorus room tools, the room's permission mode
and session resume through reflexd_wake. Before 2026-10-16 gemini and
opencode were woken bare: no room tools, no permissions (headless runs
could not edit), no model choice. This module gives them the same wake.

opencode is the any-model path: one harness for 75+ providers, including
local Ollama models, OpenRouter, DeepSeek, Qwen and Gemini API keys.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

OLLAMA_PREFIX = "ollama/"
OLLAMA_URL = "http://127.0.0.1:11434/v1"


_SECRET_KEYS = ("QUORUS_API_KEY", "QUORUS_RELAY_SECRET")


def _mcp_env(spec: dict[str, Any]) -> dict[str, str]:
    # SSE off: the agent must not drain the daemon's push stream.
    return {**spec["env"], "SSE_ENABLED": "false"}


def _mcp_env_no_secrets(spec: dict[str, Any]) -> dict[str, str]:
    """For config that travels in an env var. Blank, not absent: config layers
    merge key by key, and the global entry `quorus connect` wrote holds the
    HUMAN's secret (the codex bug of 2026-10-16). The MCP server treats blank
    as unset and reads the agent's own 0600 profile from QUORUS_CONFIG_DIR."""
    env = _mcp_env(spec)
    if "QUORUS_CONFIG_DIR" in env:
        env.update({k: "" for k in _SECRET_KEYS})
    return env


# ── opencode ─────────────────────────────────────────────────────────────

# manual: opencode can't route an approval to the owner mid-run, so it
# reviews and proposes (read-only), the same rule as codex's manual mode.
_OPENCODE_PERMS = {
    "manual": {"edit": "deny", "bash": "deny", "webfetch": "deny"},
    "autonomous": {"edit": "allow", "bash": "allow", "webfetch": "allow"},
}
_OPENCODE_PERMS["readonly"] = _OPENCODE_PERMS["manual"]
# opencode 2.x ignores the old `permission` map (a "manual" room still wrote
# files and ran shell, verified on 2.0.30) and reads an ordered `permissions`
# ruleset; its tools are named write/shell as well as edit/bash.
_OPENCODE_V2_ACTIONS = {"edit": ("edit", "write"), "bash": ("bash", "shell"),
                        "webfetch": ("webfetch",)}


def opencode_config(
    spec: dict[str, Any] | None, *, mode: str = "default", model: str | None = None,
) -> dict[str, Any]:
    """Passed as OPENCODE_CONFIG_CONTENT, which outranks every config file. A
    project or parent-folder opencode.json beats OPENCODE_CONFIG: a stale
    `quorus` entry there pointed the agent's room tools at a dead relay."""
    cfg: dict[str, Any] = {"$schema": "https://opencode.ai/config.json"}
    if spec is not None:
        cfg["mcp"] = {"quorus": {
            "type": "local",
            "command": [spec["command"], *spec["args"]],
            "environment": _mcp_env_no_secrets(spec),
            "enabled": True,
        }}
    if mode in _OPENCODE_PERMS:
        perms = _OPENCODE_PERMS[mode]
        cfg["permission"] = dict(perms)  # opencode 1.x
        cfg["permissions"] = [  # opencode 2.x
            {"action": action, "resource": "*", "effect": effect}
            for key, effect in perms.items() for action in _OPENCODE_V2_ACTIONS[key]
        ]
    if model and model.startswith(OLLAMA_PREFIX):
        # opencode has no built-in Ollama provider: declare the local server.
        name = model[len(OLLAMA_PREFIX):]
        cfg["provider"] = {"ollama": {
            "npm": "@ai-sdk/openai-compatible",
            "name": "Ollama (local)",
            "options": {"baseURL": OLLAMA_URL},
            "models": {name: {"name": name}},
        }}
    return cfg


def is_opencode_v1(version: str | None) -> bool:
    """``opencode v2.0.30`` / ``1.19.3`` → major < 2. Unknown → False."""
    m = re.search(r"(\d+)\.\d+", version or "")
    return bool(m) and int(m.group(1)) < 2


def opencode_flags(
    *, model: str | None, resume: str | None, standalone: bool = True,
) -> list[str]:
    # --standalone (2.x only): a private server reads OUR config. The shared
    # background service keeps whatever config it started with. 1.x has no
    # shared service and rejects the flag.
    flags = (["--standalone"] if standalone else []) + ["--format", "json"]
    if model:
        flags += ["--model", model]
    if resume:
        flags += ["--session", resume]
    return flags


# opencode's free models (no key). Each one's limit comes and goes on its own,
# so a limited agent moves down this list. Keep in sync with `quorus demo`.
OPENCODE_FREE_MODELS = (
    "opencode/deepseek-v4.1-flash-free", "opencode/longcat-2.5-preview-free",
    "opencode/mimo-v2.6-flash-free", "opencode/nemotron-3.5-lightning-free",
)


def is_free_opencode(model: str | None) -> bool:
    """No model (opencode's own free default) or one of its free models."""
    return model is None or (model.startswith("opencode/") and model.endswith("-free"))


def parse_opencode_stream(out: str) -> tuple[str, str | None, str | None]:
    """``opencode run --format json`` NDJSON → ``(reply, session_id, error)``.

    Shape (verified opencode 2.0.30)::

        {"type":"step_start","sessionID":"ses_..","part":{...}}
        {"type":"text","sessionID":"ses_..","part":{"messageID":"msg_..","text":".."}}
        {"type":"error","error":{"name":"APIError","data":{"message":".."}}}

    The reply is the text of the LAST assistant message: earlier messages are
    narration between tool calls.
    """
    sid: str | None = None
    error: str | None = None
    texts: dict[str, list[str]] = {}
    order: list[str] = []
    events = 0
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        events += 1
        sid = ev.get("sessionID") if isinstance(ev.get("sessionID"), str) else sid
        part = ev.get("part") if isinstance(ev.get("part"), dict) else {}
        if ev.get("type") == "text" and isinstance(part.get("text"), str):
            mid = str(part.get("messageID") or "")
            if mid not in texts:
                texts[mid] = []
                order.append(mid)
            texts[mid].append(part["text"])
        elif ev.get("type") == "error":
            err = ev.get("error") if isinstance(ev.get("error"), dict) else {}
            data = err.get("data") if isinstance(err.get("data"), dict) else {}
            error = str(data.get("message") or err.get("name") or "error")[:300]
    if not events:  # plain-text output (no --format json): the text is the reply
        return out.strip(), None, None
    reply = "".join(texts[order[-1]]).strip() if order else ""
    return reply, sid, error


# ── Gemini CLI ───────────────────────────────────────────────────────────

_GEMINI_APPROVAL = {"manual": "plan", "readonly": "plan", "autonomous": "yolo"}


def gemini_api_key(env: dict[str, str]) -> str | None:
    """The user's Gemini key: env first, then ~/.gemini/.env (where the
    Gemini CLI itself reads it). The agent's own Gemini home has no .env."""
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if env.get(name):
            return env[name]
    home = Path(env.get("HOME") or Path.home())
    try:
        text = (home / ".gemini" / ".env").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        name, _, value = line.partition("=")
        if name.strip() in ("GEMINI_API_KEY", "GOOGLE_API_KEY") and value.strip():
            return value.strip().strip("'\"")
    return None


def gemini_settings(spec: dict[str, Any] | None, *, api_key: bool = False) -> dict[str, Any]:
    """settings.json for the agent's own Gemini home: Quorus tools, and
    API-key sign-in when the user has a key (a retired Google login in the
    user's own settings made every wake fail)."""
    cfg: dict[str, Any] = {}
    if api_key:
        cfg["security"] = {"auth": {"selectedType": "gemini-api-key"}}
    if spec is not None:
        cfg["mcpServers"] = {"quorus": {
            "command": spec["command"],
            "args": list(spec["args"]),
            "env": _mcp_env(spec),
            "trust": True,  # headless runs can't confirm a tool call
        }}
    return cfg


def gemini_flags(*, mode: str = "default", model: str | None = None) -> list[str]:
    # --skip-trust: every agent worktree is a folder Gemini has never seen,
    # and headless runs fail its "trust this folder?" check.
    # stream-json: tool calls arrive while it works (the live step line).
    flags = ["--skip-trust", "--output-format", "stream-json"]
    if mode in _GEMINI_APPROVAL:
        flags += ["--approval-mode", _GEMINI_APPROVAL[mode]]
    if model:
        flags += ["--model", model]
    return flags


def parse_gemini_json(out: str) -> tuple[str, str | None, str | None]:
    """``gemini --output-format json`` → ``(reply, session_id, error)``.

    Shape: ``{"response": "..", "session_id": "..", "stats": {..}}`` or
    ``{"error": {"type": "..", "message": ".."}}``. Banner lines (e.g. "MCP
    issues detected") may precede the JSON object. ``stream-json`` output is
    one event per line; see :func:`_parse_gemini_stream`.
    """
    if '"type":"init"' in out.replace(" ", "") or '"type":"result"' in out.replace(" ", ""):
        return _parse_gemini_stream(out)
    start = out.find("{")
    try:
        data = json.loads(out[start:]) if start >= 0 else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        return out.strip(), None, None
    sid = data.get("session_id") if isinstance(data.get("session_id"), str) else None
    err = data.get("error")
    error = None
    if isinstance(err, dict):
        error = str(err.get("message") or err.get("type") or "error")[:300]
    reply = data.get("response") if isinstance(data.get("response"), str) else ""
    return reply.strip(), sid, error


def _parse_gemini_stream(out: str) -> tuple[str, str | None, str | None]:
    """``gemini --output-format stream-json`` (verified 0.64)::

        {"type":"init","session_id":"..","model":".."}
        {"type":"tool_use","tool_name":"read_file","parameters":{..}}
        {"type":"message","role":"assistant","content":"..","delta":true}
        {"type":"result","status":"success","stats":{..}}

    The reply is the assistant text after the last tool call: earlier text is
    narration between tools.
    """
    sid: str | None = None
    error: str | None = None
    reply: list[str] = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        kind = ev.get("type")
        if kind == "init" and isinstance(ev.get("session_id"), str):
            sid = ev["session_id"]
        elif kind == "tool_use":
            reply = []
        elif (kind == "message" and ev.get("role") == "assistant"
              and isinstance(ev.get("content"), str)):
            reply.append(ev["content"])
        elif kind == "error" or (kind == "result" and ev.get("status") not in (None, "success")):
            err = ev.get("error") if isinstance(ev.get("error"), dict) else {}
            error = str(err.get("message") or ev.get("message") or ev.get("status")
                        or "error")[:300]
    return "".join(reply).strip(), sid, error
