"""First value in under 2 minutes: find the agent CLIs already installed and add
them to the starter room with one keypress (2026-10-17).

Before, a new user finished setup in an empty room and had to know
`quorus agent add <tool> --room general --repo <path>` for each agent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

ORDER = ("claude", "codex", "gemini", "opencode")
LABEL = {"claude": "Claude Code", "codex": "Codex", "gemini": "Gemini CLI",
         "opencode": "opencode"}
# Models that keep an always-on agent inside a free tier.
FREE_MODEL = {"gemini": "gemini-3.5-flash-lite",
              "opencode": "opencode/deepseek-v4.1-flash-free"}
# The lead splits work so agents don't duplicate it: the strongest one present.
LEAD_ORDER = ("claude", "codex", "gemini", "opencode")


@dataclass
class Found:
    harness: str
    ready: bool
    note: str = ""


def _codex_logged_in() -> bool:
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    return bool(os.environ.get("OPENAI_API_KEY")) or (home / "auth.json").exists()


_run = subprocess.run  # tests swap this so they never call a real claude


def _claude_logged_in() -> bool | None:
    """`claude auth status`: False when Claude Code is signed out, None when
    it can't tell (an old CLI without the command). A signed-out Claude
    passed `doctor`, then every wake answered "not logged in" (0.5.0 sweep)."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return True
    try:
        out = _run(["claude", "auth", "status", "--json"], capture_output=True, text=True,
                   timeout=10, stdin=subprocess.DEVNULL)
        status = json.loads(out.stdout or "")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    logged_in = status.get("loggedIn") if isinstance(status, dict) else None
    return logged_in if isinstance(logged_in, bool) else None


def detect(which: Callable[[str], str | None] = shutil.which) -> list[Found]:
    """Installed agent CLIs, and whether each can run a turn right now."""
    from quorus_cli.agent_cmd import _gemini_has_key, _opencode_version

    found: list[Found] = []
    for h in ORDER:
        if which(h) is None:
            continue
        if h == "claude" and _claude_logged_in() is False:
            found.append(Found(h, False, "not logged in: run `claude /login`"))
        elif h == "codex" and not _codex_logged_in():
            found.append(Found(h, False, "not logged in: run `codex login`"))
        elif h == "gemini" and not _gemini_has_key():
            found.append(Found(h, False, "needs an API key from aistudio.google.com/apikey "
                                         "in ~/.gemini/.env (GEMINI_API_KEY=...); Quorus "
                                         "doesn't use your Google sign-in"))
        elif h == "opencode" and int((_opencode_version() or "2").split(".")[0]) < 2:
            found.append(Found(h, False, "too old: `npm i -g @opencode/cli`"))
        else:
            found.append(Found(h, True))
    return found


def git_root(path: Path) -> Path | None:
    try:
        out = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None


def plan(found: list[Found]) -> tuple[list[str], str | None]:
    """Which harnesses to add, and which one leads (only with 2+ agents)."""
    ready = [f.harness for f in found if f.ready]
    lead = next((h for h in LEAD_ORDER if h in ready), None) if len(ready) > 1 else None
    return ready, lead


def offer(console: Any, room: str, cwd: Path | None = None,
          ask: Callable[[str], bool] | None = None,
          add: Callable[[Any, Any], None] | None = None,
          which: Callable[[str], str | None] = shutil.which,
          ask_write: Callable[[str], bool] | None = None,
          invitee: bool = False) -> list[str]:
    """Offer to add every ready agent CLI to *room*; return the tools added.

    With a repo, a second question decides the room's permission mode. The
    wizard used to bind in `default` mode, where `codex exec` runs in its own
    read-only sandbox and opencode has no edit or shell tools, so a stranger
    who then asked for a fix got "I cannot apply the fix" and two agents handing
    the task back and forth (0.6.0 stranger test). The choice is explicit, names
    the repo, and Enter keeps the existing permissions (codex, review of the
    plan). Yes binds `autonomous`: the owner's own requests get full turns in the
    agent's worktree; anyone else's stay read-only, as in 0.6.0.

    *invitee*: the room is on a teammate's relay. The question also says who runs
    it: the agent acts only on requests sent with the invitee's own key, and the
    relay's owner is trusted to report senders honestly (Codex's trust call on
    3212878c)."""
    found = detect(which)
    if not found:
        console.print("  [muted]No agent CLIs found. Install one (Claude Code, Codex, "
                      "Gemini CLI or opencode), then: quorus agent add <tool> --room "
                      f"{room} --repo <your project>[/]")
        return []
    for f in found:
        if not f.ready:
            console.print(f"  [warning]![/] {LABEL[f.harness]} found but {f.note}")
    tools, lead = plan(found)
    if not tools:
        return []
    repo = git_root(cwd or Path.cwd())
    names = ", ".join(LABEL[t] for t in tools)
    where = f"working in [accent]{repo}[/]" if repo else "(no repo yet: they'll chat only)"
    console.print(f"\n  Found {names} on this computer.")
    if ask is None:
        from rich.prompt import Confirm

        def ask(q: str) -> bool:
            return Confirm.ask(q, default=True)
    console.print("  Signed-in agents use your CLI login and your account's usage; "
                  "free-model agents depend on the vendor's current availability.")
    if not ask(f"  Add them to [room]#{room}[/] {where}?"):
        console.print(f"  [dim]Later: quorus agent add <tool> --room {room} --repo <path>[/]")
        return []
    mode = None  # default: whatever each tool's own settings allow
    if repo is not None:
        if ask_write is None:
            from rich.prompt import Confirm

            def ask_write(q: str) -> bool:
                return Confirm.ask(q, default=False)
        console.print(f"\n  They'd work in [accent]{repo}[/].")
        if invitee:
            from quorus_cli.agent_cmd import TEAMMATE_RELAY_NOTE

            console.print(f"  [dim]{TEAMMATE_RELAY_NOTE}[/]")
        if ask_write("  Let them edit files and run commands there when you ask? Commands "
                     "run with your account's permissions. Others in the room still only "
                     "get answers."):
            mode = "autonomous"
            console.print("  [dim]OK: your requests get full turns; anyone else's stay "
                          "read-only.[/]")
        else:
            # Not "read-only": default mode keeps each tool's own settings, and
            # Claude Code's may already allow edits (codex, review of a5b0f90e).
            console.print("  [dim]OK: permissions stay as they are; each tool keeps its "
                          f"own settings. Later: quorus agent add <tool> --room {room} "
                          f"--repo {repo} --mode autonomous[/]")
    if add is None:
        from quorus_cli.agent_cmd import _add as add
    added: list[str] = []
    for t in tools:
        args = SimpleNamespace(tool=t, room=room, repo=str(repo) if repo else None,
                               mode=mode, name=None, model=FREE_MODEL.get(t),
                               lead=(t == lead), trust=None,
                               disclosed=invitee)  # asked above, after the note
        try:
            add(args, console)
            added.append(t)
        except SystemExit as exc:  # one agent failing must not stop the others
            console.print(f"  [warning]![/] {LABEL[t]}: {exc}")
    if added:
        console.print(f"\n  [success]✓[/] Say something in [room]#{room}[/]: every agent "
                      "hears you" + (f", and your {LABEL[lead]} agent leads: it splits "
                                     "the work." if lead in added else "."))
    return added


# ── the one honest next command (docs/plans/keyless-answer.md §3.4) ──────────
#
# When no free model can answer, `quorus demo` and a fresh-install `quorus doctor`
# print the same command for the machine's state, from this one function, so the
# two can never disagree. Ollama is the only keyless path (local, no account, no
# shared quota); a signed-in Claude Code or Codex uses the person's own account;
# a Gemini key file is only checked for existence, never read.

OLLAMA_MODEL = "qwen3:4b"  # the README's documented local model
OLLAMA_AGENT = "local"  # distinct from the room's `<you>-opencode` free-model agent:
# `quorus agent add` without --name re-targets that agent and keeps its old model.
OLLAMA_SERVE = "OLLAMA_CONTEXT_LENGTH=32768 ollama serve"  # the daemon's own line


@dataclass
class MachineState:
    ollama: str  # "absent" | "stopped" | "no_model" | "serving"
    ollama_model: str | None = None
    claude: bool = False  # signed in
    codex: bool = False
    gemini_env: bool = False  # ~/.gemini/.env exists (contents never read)


def ollama_state(which: Callable[[str], str | None] = shutil.which,
                 timeout: float = 2.0) -> tuple[str, str | None]:
    """Ollama as the daemon reads it (`ollama_problem()`): one local GET /api/tags."""
    if not which("ollama"):
        return "absent", None
    import httpx

    host = os.environ.get("OLLAMA_HOST") or "127.0.0.1:11434"
    base = host if host.startswith("http") else f"http://{host}"
    try:
        resp = httpx.get(f"{base.rstrip('/')}/api/tags", timeout=timeout)
        resp.raise_for_status()
        names = [m.get("name") for m in resp.json().get("models", [])
                 if isinstance(m, dict) and isinstance(m.get("name"), str)]
    except (httpx.HTTPError, ValueError, AttributeError):
        return "stopped", None
    return ("serving", names[0]) if names else ("no_model", None)


def machine_state(which: Callable[[str], str | None] = shutil.which,
                  home: Path | None = None) -> MachineState:
    ollama, model = ollama_state(which)
    home = home or Path.home()
    return MachineState(
        ollama=ollama, ollama_model=model,
        claude=bool(which("claude")) and _claude_logged_in() is True,
        codex=bool(which("codex")) and _codex_logged_in(),
        gemini_env=(home / ".gemini" / ".env").exists(),
    )


def next_command_lines(room: str, state: MachineState) -> list[str]:
    """The single honest path to a real answer, as 1–3 shell lines in order."""
    add = f"quorus agent add opencode --room {room} --name {OLLAMA_AGENT} --model ollama/"
    if state.ollama == "serving":
        return [add + (state.ollama_model or OLLAMA_MODEL)]
    if state.ollama == "no_model":
        return [f"ollama pull {OLLAMA_MODEL} && {add}{OLLAMA_MODEL}"]
    if state.ollama == "stopped":
        return [OLLAMA_SERVE, f"ollama pull {OLLAMA_MODEL} && {add}{OLLAMA_MODEL}"]
    if state.claude:
        return [f"quorus agent add claude --room {room}"]
    if state.codex:
        return [f"quorus agent add codex --room {room}"]
    if state.gemini_env:
        return [f"quorus agent add gemini --room {room} --model {FREE_MODEL['gemini']}"]
    return ["brew install ollama   # or the installer at ollama.com/download",
            OLLAMA_SERVE, f"ollama pull {OLLAMA_MODEL} && {add}{OLLAMA_MODEL}"]


def print_next_command(console: Any, room: str, state: MachineState | None = None) -> None:
    """Same lines from `quorus demo` and `quorus doctor`; a free model is never offered."""
    state = state or machine_state()
    console.print(f"  To get an answer in #{room} now:")
    for line in next_command_lines(room, state):
        console.print(f"    {line}", markup=False, highlight=False)


def doctor_agents(check: Callable[..., None],
                  which: Callable[[str], str | None] = shutil.which) -> None:
    """`quorus doctor` lines for each agent CLI (optional: you need just one)."""
    found = {f.harness: f for f in detect(which)}
    for h in ORDER:
        f = found.get(h)
        if f is None:
            check(f"{LABEL[h]} installed", False, optional=True,
                  fix=f"install it, then `quorus agent add {h} --room <room>`")
        else:
            check(f"{LABEL[h]} ready", f.ready, optional=True, fix=f.note)
