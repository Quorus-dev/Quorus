"""What is the agent doing right now? One short phrase per harness event.

Weekend run, 2026-10-17: the "⋯ @a-codex working 4m" line told the room an
agent was alive, not what it was doing, so people still had to trust it blindly.
Every harness streams its tool calls as JSON lines while it works (claude
stream-json, codex --json, opencode --format json, gemini stream-json). This
turns one line into a phrase like "editing relay.py" or "running tests".

Phrases are fixed words plus a file's basename. Shell arguments are never
shown, since a command line can carry a token.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import PurePath
from typing import Any

# Tool names (lowercased) across harnesses -> verb + what it acts on.
_EDIT = {"edit", "multiedit", "replace", "apply_patch", "patch", "str_replace_editor"}
_WRITE = {"write", "write_file", "create_file"}
_READ = {"read", "read_file", "read_many_files", "view", "notebookread"}
_SEARCH = {"grep", "glob", "search_file_content", "list_directory", "ls", "codesearch", "list"}
_SHELL = {"bash", "shell", "run_shell_command", "exec_command"}
_WEB = {"websearch", "web_search", "google_web_search", "webfetch", "web_fetch", "fetch"}
_SUBAGENT = {"task", "agent"}
_QUIET = {"todowrite", "todoread", "update_topic", "todo_list", "exitplanmode"}

_TESTS = re.compile(r"\b(pytest|vitest|jest|mocha|go test|cargo test|rspec|phpunit|unittest|"
                    r"(npm|pnpm|yarn|bun)( run)? test)\b")
_LINT = re.compile(r"\b(ruff|eslint|flake8|mypy|pyright|tsc|prettier|black|biome)\b")
_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SETUP = {"cd", "export", "set", "source", ".", "true", "echo", "sleep"}


def _base(path: Any) -> str:
    name = PurePath(str(path or "")).name
    return name[:40]


def _unwrap(cmd: str) -> str:
    """codex runs ``/bin/zsh -lc '<cmd>'``: the real command is inside."""
    m = re.match(r"^\S*/?(?:ba|z)?sh\s+-l?c\s+(.+)$", cmd.strip(), re.DOTALL)
    if not m:
        return cmd
    inner = m.group(1).strip()
    try:
        parts = shlex.split(inner)
    except ValueError:
        return inner.strip("'\"")
    return parts[0] if len(parts) == 1 else inner


def shell_step(cmd: str) -> str:
    cmd = _unwrap(cmd or "")
    low = cmd.lower()
    if _TESTS.search(low):
        return "running tests"
    if re.search(r"\bgit\s+commit\b", low):
        return "committing"
    if re.search(r"\bgit\s+(diff|log|show|status|blame)\b", low):
        return "reading git history"
    if _LINT.search(low):
        return "checking lint and types"
    if re.search(r"\b(rg|grep|ag|fd|find)\b", low):
        return "searching the code"
    if re.search(r"\b(pip|uv|npm|pnpm|yarn|bun|brew|cargo)\s+(install|add|i)\b", low):
        return "installing packages"
    words: list[str] = []
    # `cd repo && ls; cat x.py`: name the first real command, not `cd`.
    for segment in re.split(r"&&|\|\||[;|\n]", cmd):
        try:
            words = shlex.split(segment)
        except ValueError:
            words = segment.split()
        words = [w for w in words if not _ENV.match(w)]
        if words and PurePath(words[0]).name not in _SETUP:
            break
        words = []
    exe = PurePath(words[0]).name if words else ""
    if exe in ("cat", "head", "tail", "sed", "less", "wc", "nl") and len(words) > 1:
        return f"reading {_base(words[-1])}"
    if exe in ("ls", "pwd", "tree"):
        return "looking around the repo"
    return f"running {exe[:30]}" if exe else "running a command"


def tool_step(name: str, args: dict[str, Any]) -> str | None:
    low = (name or "").lower()
    if low.startswith("mcp__"):  # claude: mcp__<server>__<tool>
        low = low.split("__")[-1]
    path = (args.get("file_path") or args.get("filePath") or args.get("path")
            or args.get("notebook_path") or "")
    if low in _QUIET:
        return None
    if low in _EDIT:
        return f"editing {_base(path)}" if path else "editing code"
    if low in _WRITE:
        return f"writing {_base(path)}" if path else "writing a file"
    if low in _READ:
        return f"reading {_base(path)}" if path else "reading code"
    if low in _SEARCH:
        return "searching the code"
    if low in _SHELL:
        return shell_step(str(args.get("command") or args.get("cmd") or ""))
    if low in _WEB:
        return "searching the web" if "search" in low else "reading a web page"
    if low in _SUBAGENT:
        return "running a subagent"
    if low in ("send_message", "reply", "post_message"):
        return "writing to the room"
    if low in ("claim_task", "release_task", "check_messages", "room_history", "who_is_here"):
        return "checking the room"
    return f"using {low[:30]}" if low else None


def step_of(line: str) -> str | None:
    """The step a single stream line announces, or None."""
    line = line.strip()
    if not line.startswith("{") or '"' not in line:
        return None
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(ev, dict):
        return None
    kind = ev.get("type")
    # claude stream-json: assistant message content blocks
    if kind == "assistant" and isinstance(ev.get("message"), dict):
        for block in reversed(ev["message"].get("content") or []):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                inp = block.get("input") if isinstance(block.get("input"), dict) else {}
                return tool_step(str(block.get("name") or ""), inp)
        return None
    # codex --json: item.started
    item = ev.get("item")
    if kind == "item.started" and isinstance(item, dict):
        it = item.get("type")
        if it == "command_execution":
            return shell_step(str(item.get("command") or ""))
        if it == "file_change":
            changes = [c for c in item.get("changes") or [] if isinstance(c, dict)]
            if changes:
                verb = "writing" if changes[0].get("kind") == "add" else "editing"
                more = f" +{len(changes) - 1}" if len(changes) > 1 else ""
                return f"{verb} {_base(changes[0].get('path'))}{more}"
            return "editing code"
        if it == "mcp_tool_call":
            return tool_step(str(item.get("tool") or ""), {})
        if it == "web_search":
            return "searching the web"
        return None
    # opencode --format json: tool_use parts
    part = ev.get("part")
    if kind == "tool_use" and isinstance(part, dict):
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        inp = state.get("input") if isinstance(state.get("input"), dict) else {}
        return tool_step(str(part.get("tool") or ""), inp)
    # gemini stream-json: tool_use with tool_name/parameters
    if kind == "tool_use" and "tool_name" in ev:
        params = ev.get("parameters") if isinstance(ev.get("parameters"), dict) else {}
        return tool_step(str(ev.get("tool_name") or ""), params)
    return None
