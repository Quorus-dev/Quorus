"""First value in under 2 minutes: find the agent CLIs already installed and add
them to the starter room with one keypress (2026-10-17).

Before, a new user finished setup in an empty room and had to know
`quorus agent add <tool> --room general --repo <path>` for each agent.
"""

from __future__ import annotations

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


def detect(which: Callable[[str], str | None] = shutil.which) -> list[Found]:
    """Installed agent CLIs, and whether each can run a turn right now."""
    from quorus_cli.agent_cmd import _gemini_has_key, _opencode_version

    found: list[Found] = []
    for h in ORDER:
        if which(h) is None:
            continue
        if h == "codex" and not _codex_logged_in():
            found.append(Found(h, False, "not logged in: run `codex login`"))
        elif h == "gemini" and not _gemini_has_key():
            found.append(Found(h, False, "needs a free key from aistudio.google.com/apikey "
                                         "in ~/.gemini/.env (GEMINI_API_KEY=...)"))
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
          which: Callable[[str], str | None] = shutil.which) -> list[str]:
    """Offer to add every ready agent CLI to *room*; return the tools added."""
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
    if not ask(f"  Add them to [room]#{room}[/] {where}?"):
        console.print(f"  [dim]Later: quorus agent add <tool> --room {room} --repo <path>[/]")
        return []
    if add is None:
        from quorus_cli.agent_cmd import _add as add
    added: list[str] = []
    for t in tools:
        args = SimpleNamespace(tool=t, room=room, repo=str(repo) if repo else None,
                               mode=None, name=None, model=FREE_MODEL.get(t),
                               lead=(t == lead), trust=None)
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
