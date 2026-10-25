"""Review gate, slice 4: the owner's commands (docs/plans/review-gate.md).

`quorus room reviewer <room> <name>` pins the room's reviewer to the key the
relay stamped on that participant's latest message in the room (shown before
it is saved), so a GO counts only from that key. `--none` removes the gate.
`quorus review status` lists held tips on this machine, and
`quorus review publish <sha> --unreviewed` is the owner's local override:
whoever can write this machine's runtime dir is the owner.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from quorus.runtime import review_gate as _gate

_KEYED = {"key": "claim_id", "jwt": "participant_id"}
_STAMP = {"claim_id": "sender_claim_id", "participant_id": "sender_participant_id"}


def latest_pin(history: list[dict[str, Any]], name: str) -> dict[str, str] | None:
    """The identity the relay stamped on *name*'s latest message, if it was
    sent with its own key (``key``) or a hosted login (``jwt``). A message sent
    with the relay secret proves nothing about who sent it: None."""
    for msg in reversed(history):
        if msg.get("from_name") != name:
            continue
        field = _KEYED.get(str(msg.get("sender_auth") or ""))
        value = msg.get(_STAMP[field]) if field else None
        if not field or not value:
            return None
        return {"tenant": str(msg.get("sender_tenant") or ""), field: str(value)}
    return None


def _history(room: str) -> list[dict[str, Any]] | None:
    from quorus_cli import cli

    try:
        resp = httpx.get(f"{cli.RELAY_URL}/rooms/{room}/history",
                         params={"limit": 200}, headers=cli._auth_headers(), timeout=10)
    except httpx.ConnectError as exc:
        cli._relay_unreachable(exc=exc)
        return None
    if resp.status_code != 200:
        cli._ui.error(f"The relay answered {resp.status_code} for #{room}'s history")
        return None
    data = resp.json()
    if isinstance(data, dict):
        data = next((data[k] for k in ("messages", "history", "items")
                     if isinstance(data.get(k), list)), [])
    return data if isinstance(data, list) else []


def _show(room: str, files: list[Path]) -> None:
    from quorus_cli import cli

    pins = {f: _gate.reviewer_for(room, f) for f in files}
    if len({json.dumps(p, sort_keys=True) for p in pins.values()}) > 1:
        # Daemons reading different files gate differently: say which reads what.
        cli.console.print(f"[warning]![/] #{room}'s reviewer differs between the files "
                          "its agents read:")
        for f, p in pins.items():
            who = f"@{p.get('name') or '?'}" if p is not None else "no reviewer"
            cli.console.print(f"  {f}: {who}")
        cli.console.print(f"[dim]set one everywhere: quorus room reviewer {room} <name>, "
                          f"or turn it off: quorus room reviewer {room} --none[/]")
        return
    pin = pins[files[0]]
    if pin is None:
        cli.console.print(f"[dim]#{room} has no reviewer[/]")
    elif _gate.pinned(pin):
        key = pin.get("claim_id") or pin.get("participant_id")
        cli.console.print(f"#{room} reviewer: @{pin['name']} (pinned to {key})")
    else:
        cli.console.print(f"#{room} reviewer: @{pin.get('name') or '?'} "
                          "[warning](not pinned: owner override only)[/]")


def _write(room: str, files: list[Path], change: Any) -> None:
    """*change* every file a daemon for *room*'s repo reads, or none of them:
    the pin used to reach only the CLI's own file, and agents started with
    --state-dir published ungated (take-3 bug 3)."""
    from quorus_cli import agent_identity, cli, gate_setup

    try:
        gate_setup.write_all(files, change)
    except agent_identity.IdentityError as exc:
        cli.console.print(f"[error]✗[/] #{room}'s gate is unchanged: {exc}")
        raise SystemExit(1) from exc


def cmd_room_reviewer(args: Any) -> None:
    from quorus_cli import cli, gate_setup

    room, name = args.room, getattr(args, "name", None)
    files = gate_setup.room_files(room)
    if getattr(args, "none", False):
        _write(room, files, lambda f: _gate.clear_reviewer(room, f))
        cli.console.print(f"[success]✓[/] #{room} has no reviewer: its commits publish "
                          "when a turn ends, as before")
        for line in gate_setup.where_lines(files):
            cli.console.print(line)
        return
    if not name:
        _show(room, files)
        return
    history = _history(room)
    if history is None:
        raise SystemExit(1)
    pin = latest_pin(history, name)
    if pin is None and not getattr(args, "unpinned", False):
        cli._ui.error(
            f"No message in #{room} from @{name} sent with its own key, so there is "
            "nothing to pin a GO to.",
            hint=(f"have @{name} post in #{room} with its own key (quorus agent add / "
                  "connect give one), or pass --unpinned: every publish then waits "
                  "for your override"))
        raise SystemExit(1)
    repo = gate_setup.room_repo(room)
    _write(room, files, lambda f: _gate.save_reviewer(
        room, name, tenant=(pin or {}).get("tenant", ""),
        claim_id=(pin or {}).get("claim_id", ""),
        participant_id=(pin or {}).get("participant_id", ""), path=f, repo=repo))
    if pin is None:
        cli.console.print(f"[warning]![/] #{room} reviewer: @{name}, not pinned. Its GOs "
                          "can't count; every publish waits for your override.")
    else:
        key = pin.get("claim_id") or pin.get("participant_id")
        cli.console.print(f"[success]✓[/] #{room} reviewer: @{name}, pinned to {key}. "
                          "Commits in this room's repo now wait for its GO before main.")
    for line in gate_setup.where_lines(files):
        cli.console.print(line)


def _markers() -> list[tuple[Path, dict[str, Any]]]:
    from quorus.runtime import bindings

    root = bindings.config_dir() / "runtime" / "review-gate"
    out = []
    for path in sorted(root.glob("*.json")) if root.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("tip") and data.get("repo"):
            out.append((path, data))
    return out


def cmd_review_status(_args: Any) -> None:
    from quorus_cli import cli

    held = _markers()
    if not held:
        cli.console.print("[dim]nothing is waiting for review on this machine[/]")
        return
    for _path, m in held:
        extra = " [warning](override set)[/]" if m.get("override_tip") == m["tip"] else ""
        cli.console.print(f"  {m['tip'][:12]}  {m.get('branch', '?')}  #{m.get('room', '?')}  "
                          f"waiting for @{m.get('reviewer') or '?'}{extra}")


def cmd_review_publish(args: Any) -> None:
    from quorus_cli import cli

    sha = (args.sha or "").lower()
    if not getattr(args, "unreviewed", False):
        cli._ui.error("publish needs --unreviewed: this skips the reviewer",
                      hint=f"quorus review publish {sha or '<sha>'} --unreviewed")
        raise SystemExit(2)
    if len(sha) < _gate.MIN_SHA:
        cli._ui.error(f"give at least {_gate.MIN_SHA} characters of the sha")
        raise SystemExit(2)
    hits = [(p, m) for p, m in _markers() if str(m["tip"]).startswith(sha)]
    if len(hits) != 1:
        cli._ui.error(f"{len(hits)} held tips match {sha}",
                      hint="see them: quorus review status")
        raise SystemExit(1)
    path, m = hits[0]
    m["override_tip"] = m["tip"]
    from quorus.private_files import write_private_text
    write_private_text(path, json.dumps(m, sort_keys=True) + "\n")
    cli.console.print(f"[success]✓[/] {m['tip'][:12]} will be published unreviewed by its "
                      "agent's daemon on its next check (within about 2 minutes), marked "
                      "as your override.")
