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
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Codex sandbox for woken agents. Default "" = whatever the owner's
# ~/.codex/config.toml says (headless `codex exec` is read-only by default,
# so codex can review but not write). The owner opts in per daemon, e.g.
# REFLEXD_CODEX_SANDBOX=workspace-write — writes limited to the bound repo.
# Never set from code; scripts/dogfood.sh sets it when the owner asks.
CODEX_SANDBOX = os.environ.get("REFLEXD_CODEX_SANDBOX", "").strip()
_CODEX_SANDBOXES = {"read-only", "workspace-write"}

# Each agent works in its OWN git worktree of the bound repo (branch
# quorus/<agent>) and fast-forwards the repo's main branch when done. Two
# agents sharing one checkout committed each other's half-finished edits.
WORKTREES_ENABLED = os.environ.get("REFLEXD_WORKTREES", "1") not in ("0", "false", "no")

# Stop agent↔agent ping-pong: once this many consecutive room messages are
# all from agents (no human in between), agents stop waking on each other.
# Counted along the reply chain of ONE conversation (review → fix → review…),
# so an overnight backlog of independent tasks never trips it; the room-wide
# trailing run is a looser backstop for tool-posted messages without reply_to.
MAX_AGENT_CHAIN = int(os.environ.get("REFLEXD_MAX_AGENT_CHAIN", "12"))
MAX_ROOM_AGENT_RUN = int(os.environ.get("REFLEXD_MAX_ROOM_AGENT_RUN", "40"))


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


# Per-room permission modes, chosen by the owner at `quorus room bind
# --mode` time. "default" = the owner's own Claude Code / Codex settings.
MODES = ("default", "manual", "autonomous")


def claude_mode_flags(mode: str) -> list[str]:
    """manual: every tool needing permission is routed to the agent's OWNER
    via Quorus approvals (approve in the room or `quorus approve`).
    autonomous: edits and shell commands run without asking, inside the
    room's repo worktree; Claude Code's own always-ask actions still apply."""
    if mode == "manual":
        return ["--permission-prompt-tool", "mcp__quorus__approve",
                "--allowedTools", "mcp__quorus"]
    if mode == "autonomous":
        return ["--permission-mode", "acceptEdits",
                "--allowedTools", "Bash Edit Write MultiEdit mcp__quorus"]
    return []


def claude_wake_flags(config_path: Path | None, mode: str = "default") -> list[str]:
    """Extra ``claude --print`` flags: Quorus MCP server + permission mode."""
    flags = ["--mcp-config", str(config_path)] if config_path else []
    return flags + claude_mode_flags(mode)


def _toml_str(value: str) -> str:
    # JSON string escaping is valid TOML basic-string escaping for our inputs.
    return json.dumps(value)


def codex_wake_flags(
    spec: dict[str, Any] | None, writable_roots: list[Path] | None = None,
    mode: str = "default",
) -> list[str]:
    """Extra ``codex exec`` flags: the Quorus MCP server via ``-c`` overrides.

    ``-c`` overrides beat ``~/.codex/config.toml``, so a stale user entry for
    ``mcp_servers.quorus`` (dead venv, dead relay) cannot shadow ours.
    """
    flags: list[str] = []
    # Room mode wins over the daemon-wide default. Codex exec cannot ask a
    # human mid-run, so "manual" means read-only (it reviews and proposes).
    sandbox = {"manual": "read-only", "autonomous": "workspace-write"}.get(
        mode, CODEX_SANDBOX)
    if sandbox in _CODEX_SANDBOXES:  # danger-full-access deliberately unsupported
        # As config, not `-s`: `codex exec resume` has no -s flag, so every
        # RESUMED wake silently fell back to read-only and agents couldn't
        # commit (scenario gate S9, 2026-10-09). -c works for both forms.
        flags += ["-c", f"sandbox_mode={_toml_str(sandbox)}"]
        if sandbox == "workspace-write" and writable_roots:
            # A worktree's git objects live in the main repo's .git, and
            # publishing fast-forwards the main checkout: both sit outside the
            # worktree cwd. Scope stays the bound repo, nothing wider.
            flags += ["-c", "sandbox_workspace_write.writable_roots=["
                      + ",".join(_toml_str(str(r)) for r in writable_roots) + "]"]
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


def reply_chain_agent_depth(
    envelope: dict[str, Any], history: list[dict[str, Any]], is_agent: Any,
) -> int:
    """Agent-authored messages in *envelope*'s reply chain, up to the nearest
    human message. The envelope itself counts when an agent sent it."""
    by_id = {m.get("id"): m for m in history if m.get("id")}
    depth = 1 if is_agent(envelope.get("from_name") or "") else 0
    cur = envelope.get("reply_to")
    seen: set[str] = set()
    while cur and cur in by_id and cur not in seen:
        seen.add(cur)
        parent = by_id[cur]
        if not is_agent(parent.get("from_name") or ""):
            break
        depth += 1
        cur = parent.get("reply_to")
    return depth


def trailing_agent_chain(history: list[dict[str, Any]], is_agent: Any) -> int:
    """How many messages at the end of *history* came from agents in a row."""
    n = 0
    for m in reversed(history):
        if is_agent(m.get("from_name") or m.get("sender") or ""):
            n += 1
        else:
            break
    return n


@dataclass(frozen=True)
class Worktree:
    path: Path      # where the agent works
    repo: Path      # the bound repo (main checkout)
    branch: str     # quorus/<participant>
    main: str       # branch checked out in the main checkout


_FALLBACK_IDENTITY = {
    "GIT_AUTHOR_NAME": "Quorus", "GIT_AUTHOR_EMAIL": "agents@quorus.local",
    "GIT_COMMITTER_NAME": "Quorus", "GIT_COMMITTER_EMAIL": "agents@quorus.local",
}


def _has_identity(cwd: Path) -> bool:
    got = subprocess.run(["git", "config", "--get", "user.email"], cwd=str(cwd),
                         capture_output=True, text=True, timeout=10, check=False)
    return bool(got.stdout.strip())


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    # The daemon's rebases and merge commits need a committer identity. A
    # machine with none configured (CI runners, fresh servers) made every
    # publish fail silently. Fall back to "Quorus" only when git has no
    # identity — a configured user's name is always kept.
    env = None
    if args and args[0] in ("rebase", "merge", "commit") and not _has_identity(cwd):
        env = {**os.environ, **{k: v for k, v in _FALLBACK_IDENTITY.items()
                                if k not in os.environ}}
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, timeout=30, check=False, env=env)


def agent_worktree(repo: Path, participant: str) -> Worktree | None:
    """Return (creating if needed) *participant*'s worktree of *repo*.

    None when *repo* is not the top of a git repo, or git fails — callers
    then fall back to running in the bound directory itself.
    """
    top = _git("rev-parse", "--show-toplevel", cwd=repo)
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != repo.resolve():
        return None
    head = _git("symbolic-ref", "--short", "HEAD", cwd=repo)
    if head.returncode != 0:
        return None  # detached main checkout: no branch to integrate into
    main = head.stdout.strip()
    branch = f"quorus/{participant}"
    path = repo.parent / ".quorus-worktrees" / repo.name / participant
    if (path / ".git").exists():
        return Worktree(path, repo, branch, main)
    _git("worktree", "prune", cwd=repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    has_branch = _git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}",
                      cwd=repo).returncode == 0
    # Never reset an existing agent branch: it may hold unpublished work.
    add = (["worktree", "add", str(path), branch] if has_branch
           else ["worktree", "add", "-b", branch, str(path), main])
    if _git(*add, cwd=repo).returncode != 0:
        return None
    return Worktree(path, repo, branch, main)


def worktree_dirty(wt: Worktree) -> bool:
    return bool(_git("status", "--porcelain", "--untracked-files=no", cwd=wt.path).stdout.strip()
                or _git("ls-files", "--others", "--exclude-standard", cwd=wt.path).stdout.strip())


def sync_worktree(wt: Worktree) -> None:
    """Before a wake: put a clean agent branch on top of main so the agent
    starts from everyone's latest work. Agents can't rebase in Codex's
    sandbox (seen live), so the daemon does it; conflicts are left for the
    post-wake publish, which wakes the agent to resolve them."""
    if worktree_dirty(wt):
        return
    if _git("merge-base", "--is-ancestor", wt.main, wt.branch, cwd=wt.repo).returncode == 0:
        return
    if _git("rebase", "--quiet", wt.main, cwd=wt.path).returncode != 0:
        _git("rebase", "--abort", cwd=wt.path)


def worktree_instructions(wt: Worktree, *, dirty: bool = False) -> str:
    note = (" Your worktree has UNCOMMITTED changes from an earlier session: look "
            "at `git status`/`git diff`, finish or discard them, and commit before "
            "anything else." if dirty else "")
    return (
        f"You work in your own git worktree `{wt.path}` on branch `{wt.branch}`, "
        f"already synced with `{wt.main}`. The shared repo `{wt.repo}` has "
        f"`{wt.main}` checked out: never edit files there.{note} Get the tests "
        "green, then commit on your branch if git lets you; if your sandbox "
        "blocks git, just leave the changes - Quorus commits them for you "
        "(using the first line of your final message as the commit subject). "
        "Do NOT run git rebase, merge, reset or push: Quorus rebases your branch "
        f"onto `{wt.main}` and publishes it when you finish, and wakes you if "
        "there is a conflict. Reviewers: inspect a commit with `git show "
        "<hash>` (all worktrees share one object store)."
    )


STATUS_PREFIX = "(quorus)"  # not "[quorus]": the TUI renders [..] as Rich markup
CONFLICT_MARK = f"{STATUS_PREFIX} conflict:"

RESOLVE_PROMPT = (
    "Quorus tried to publish your branch but `{main}` moved and your changes "
    "conflict with it. A merge of `{main}` into `{branch}` is in progress in "
    "your worktree `{path}`. Resolve every conflict keeping BOTH sides' "
    "behaviour (no `<<<<<<<` markers left), run the full test suite until it "
    "is green, then `git add` the resolved files. Quorus completes the merge "
    "commit for you. Do not rebase, reset or abort the merge. Final message: "
    "one line on what conflicted and the test result."
)


def abort_merge(wt: Worktree) -> None:
    _git("merge", "--abort", cwd=wt.path)


def finish_merge(wt: Worktree, participant: str) -> bool:
    """Complete a merge the agent resolved but could not commit (Codex's
    sandbox blocks finishing a merge in the worktree's git dir). Stages
    conflicted files only when no conflict markers remain; True if done."""
    if not merge_in_progress(wt):
        return False
    unmerged = _git("diff", "--name-only", "--diff-filter=U", cwd=wt.path).stdout.split()
    for rel in unmerged:
        try:
            text = (wt.path / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        if "<<<<<<<" in text or ">>>>>>>" in text:
            return False  # not actually resolved
    if unmerged and _git("add", "--", *unmerged, cwd=wt.path).returncode != 0:
        return False
    done = _git("-c", f"user.name={participant}", "-c",
                f"user.email={participant}@agents.quorus.local",
                "commit", "--no-edit", "--quiet", cwd=wt.path)
    return done.returncode == 0


def commit_message_from(reply: str, participant: str) -> str:
    """A commit subject from the agent's own final reply."""
    import re as _re

    for line in (reply or "").splitlines():
        line = _re.sub(r"[`*_#>]|^\s*[-✅☑️✔️]+\s*", "", line).strip()
        if len(line) >= 8 and not line.startswith(STATUS_PREFIX):
            return line[:72]
    return f"work by {participant}"


def commit_all(wt: Worktree, participant: str, message: str) -> bool:
    """Commit the agent's uncommitted work on its branch, as the agent.

    Codex's sandbox protects every .git dir (worktree index.lock included),
    so a Codex agent often CANNOT commit; its finished work sat uncommitted
    and never published (scenario gate S9, 2026-10-09). The daemon is not
    sandboxed: it commits for the agent. Honors .gitignore."""
    if not worktree_dirty(wt) or merge_in_progress(wt):
        return False
    if _git("add", "-A", cwd=wt.path).returncode != 0:
        return False
    done = _git("-c", f"user.name={participant}", "-c",
                f"user.email={participant}@agents.quorus.local",
                "commit", "--quiet", "-m", message, cwd=wt.path)
    return done.returncode == 0


def merge_in_progress(wt: Worktree) -> bool:
    return _git("rev-parse", "-q", "--verify", "MERGE_HEAD", cwd=wt.path).returncode == 0


def publish_worktree(wt: Worktree) -> str | None:
    """Rebase the agent's branch onto main if needed, then fast-forward main.

    Done by the daemon, not the agent: Codex's sandbox blocks both the main
    checkout's .git (merge failed on ORIG_HEAD.lock) and the worktree's
    rebase state dir (seen live 2026-10-08), and a deterministic publish
    beats trusting every model to run it. Returns a one-line room status, or
    None when there is nothing to publish.
    """
    ahead = _git("rev-list", "--count", f"{wt.main}..{wt.branch}", cwd=wt.repo)
    if ahead.returncode != 0 or ahead.stdout.strip() in ("", "0"):
        return None
    n = int(ahead.stdout.strip())
    if _git("merge-base", "--is-ancestor", wt.main, wt.branch, cwd=wt.repo).returncode != 0:
        if _git("status", "--porcelain", "--untracked-files=no", cwd=wt.path).stdout.strip():
            return (f"{STATUS_PREFIX} {wt.branch} has uncommitted changes and is behind "
                    f"{wt.main}; not publishing yet")
        rebased = _git("rebase", "--quiet", wt.main, cwd=wt.path)
        if rebased.returncode != 0:
            _git("rebase", "--abort", cwd=wt.path)
            # Leave a real merge with conflict markers in the agent's own
            # worktree; the daemon wakes the agent to resolve it (see
            # CONFLICT_MARK). Agents' sandboxes can edit + commit, not rebase.
            merged_main = _git("merge", "--no-edit", wt.main, cwd=wt.path)
            if merged_main.returncode != 0:
                return (f"{CONFLICT_MARK} {wt.branch} conflicts with {wt.main}; "
                        "merge left in progress for the agent to resolve")
    dirty = _git("status", "--porcelain", "--untracked-files=no", cwd=wt.repo)
    if dirty.stdout.strip():
        return (f"{STATUS_PREFIX} not publishing {wt.branch}: the main checkout "
                f"{wt.repo} has uncommitted changes")
    if _git("symbolic-ref", "--short", "HEAD", cwd=wt.repo).stdout.strip() != wt.main:
        return None  # someone switched the main checkout's branch; leave it be
    merged = _git("merge", "--ff-only", "--quiet", wt.branch, cwd=wt.repo)
    if merged.returncode != 0:
        return f"{STATUS_PREFIX} publishing {wt.branch} failed: {merged.stderr.strip()[:160]}"
    return f"{STATUS_PREFIX} published {n} commit(s) from {wt.branch} to {wt.main}"


def existing_worktree(repo: Path, participant: str) -> Worktree | None:
    """Like :func:`agent_worktree` but never creates one (startup sweep)."""
    path = repo.parent / ".quorus-worktrees" / repo.name / participant
    return agent_worktree(repo, participant) if (path / ".git").exists() else None


def wake_instructions(
    *, participant: str, room: str, sender: str, kind: str,
    teammates: list[str], has_workspace: bool, worktree: Worktree | None = None,
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
    ws = (worktree_instructions(worktree, dirty=worktree_dirty(worktree))
          if worktree is not None else
          "You are running inside the room's bound workspace: make real changes, "
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
