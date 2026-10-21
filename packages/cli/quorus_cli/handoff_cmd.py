"""`quorus handoff holds` and `quorus handoff release` (Never Stuck slice 2c-2).

Work a handoff holds back from publishing stays held until a proof passes or its
owner decides. These commands are that decision: list what's held, then publish or
discard one hold after looking at the exact commit. See quorus.runtime.handoff_release
for every refusal.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from rich.markup import escape

from quorus.runtime import handoff_release
from quorus_cli import ui as _ui

console = _ui.console


def _runtime_dir(args) -> Path:
    from quorus.runtime.bindings import config_dir

    return Path(getattr(args, "runtime_dir", None) or config_dir() / "runtime")


def cmd_holds(args) -> None:
    held = handoff_release.holds(_runtime_dir(args))
    if not held:
        console.print("  [muted]no handoff holds: nothing is waiting on you[/]")
        return
    for h in held:
        try:
            head = handoff_release.Git(h.worktree).text("rev-parse", "HEAD")
        except Exception:  # a missing worktree is still worth listing
            head = "unreadable"
        console.print(
            f"  [primary]{escape(h.room or '?')}[/] @{escape(h.agent)} capsule "
            f"{escape(h.capsule_id or '?')} [dim]({escape(h.state)})[/]\n"
            f"    branch {escape(h.branch)} at {escape(head)}\n    {escape(str(h.worktree))}")
    console.print("\n  [dim]review the head, stop the agent's daemon, then:[/] "
                  "quorus handoff release --room R --agent A --capsule ID --head SHA "
                  "--publish|--discard")


def daemon_running(agent: str) -> bool | None:
    """True, False, or None when it can't be told. None refuses the release: a
    failed lookup is never read as "stopped" (Codex on ac0bc90b)."""
    from quorus_cli.agent_cmd import _running_daemon_for

    try:
        if _running_daemon_for(agent) is not None:
            return True
        # _running_daemon_for also returns None when pgrep itself failed; ask again
        # and read its exit code: 1 is "no match", anything else is "don't know".
        out = subprocess.run(["pgrep", "-f", f"reflexd.py start.*--participant {agent}( |$)"],
                             capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return {0: True, 1: False}.get(out.returncode)


def cmd_release(args) -> None:
    if not sys.stdin.isatty():
        _ui.error("quorus handoff release must be typed at a terminal")
        raise SystemExit(2)
    action = "publish" if args.publish else "discard"
    console.print(f"[warning]{action}[/] @{escape(args.agent)}'s held work in "
                  f"[primary]{escape(args.room)}[/] at {escape(args.head[:12])}")
    if input(f"  type the room name ({args.room}) to go on: ").strip() != args.room:
        _ui.error("nothing changed: the name didn't match")
        raise SystemExit(2)
    running = daemon_running(args.agent)
    try:
        note = handoff_release.release(
            _runtime_dir(args), agent=args.agent, room=args.room, capsule_id=args.capsule,
            head=args.head, action=action, daemon_running=running)
    except handoff_release.Refused as exc:
        _ui.error(f"nothing changed: {exc}")
        raise SystemExit(2) from exc
    console.print(f"[success]done[/] {escape(note)}")


def add_parser(sub, help_block) -> None:
    p = sub.add_parser("handoff", **help_block(
        synopsis="quorus handoff holds | release",
        description="List or release work a handoff is holding back from publishing.",
        example="quorus handoff holds",
        help_text="Handoff holds: list, then publish or discard"))
    actions = p.add_subparsers(dest="handoff_action")
    holds_p = actions.add_parser("holds", help="List held handoff work (read-only)")
    holds_p.add_argument("--runtime-dir", help=argparse.SUPPRESS)
    rel = actions.add_parser("release", help="Publish or discard one held handoff")
    rel.add_argument("--room", required=True)
    rel.add_argument("--agent", required=True)
    rel.add_argument("--capsule", required=True, help="capsule id, from quorus handoff holds")
    rel.add_argument("--head", required=True, help="the full commit you reviewed")
    which = rel.add_mutually_exclusive_group(required=True)
    which.add_argument("--publish", action="store_true",
                       help="lift the hold; the agent's daemon publishes on its next start")
    which.add_argument("--discard", action="store_true",
                       help="move the branch back to before the handoff (a backup ref stays)")
    rel.add_argument("--runtime-dir", help=argparse.SUPPRESS)


def cmd_handoff(args) -> None:
    action = getattr(args, "handoff_action", None)
    if action == "holds":
        cmd_holds(args)
    elif action == "release":
        cmd_release(args)
    else:
        _ui.error("usage: quorus handoff holds | release")
        raise SystemExit(5)
