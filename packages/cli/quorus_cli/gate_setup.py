"""Gate by default, slice B: pick the reviewer, pin it from the key it actually
signs in with, and say so in the room (docs/plans/gate-by-default.md, Codex plan
GO b4b34727).

The pin is saved after every agent is prepared and before any daemon starts
(the wizard's prepare → pin → start), so no commit can publish ungated while the
gate is being set up. Any failure leaves the room's gate as it was and says so.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from quorus.runtime import review_gate as _gate
from quorus_cli import agent_identity
from quorus_cli import relay_http as _relay_http

# Who makes each harness: a reviewer from the same vendor as the lead isn't a
# second opinion.
VENDOR = {"claude": "anthropic", "codex": "openai", "gemini": "google",
          "opencode": "opencode"}


def pick_reviewer(tools: list[str], lead: str | None,
                  order: tuple[str, ...]) -> str | None:
    """The highest-ranked ready tool whose vendor differs from the lead's."""
    if not lead:
        return None
    for tool in order:
        if tool in tools and tool != lead and VENDOR.get(tool) != VENDOR.get(lead):
            return tool
    return None


def reviewer_files(state_dirs: list[Any]) -> list[Path]:
    """Every ``room-reviewers.json`` the room's daemons read: the CLI's config
    dir, plus each daemon's own ``--state-dir`` (a daemon started with one reads
    its gate there; codex, review of 1a120d72). Deduplicated, in order."""
    from quorus.runtime import bindings

    out: list[Path] = []
    for base in [bindings.config_dir(), *[d for d in state_dirs if d is not None]]:
        path = (Path(base) / "room-reviewers.json").resolve()
        if path not in out:
            out.append(path)
    return out


def current_reviewers(room: str, files: list[Path]) -> dict[str, str]:
    """{reviewer name: file} for each file that already gates *room*."""
    found: dict[str, str] = {}
    for f in files:
        pin = _gate.reviewer_for(room, f)
        if pin is not None:
            found.setdefault(pin.get("name") or "?", str(f))
    return found


def pin_reviewer(prep: dict[str, Any], *, replace: bool = False,
                 state_dirs: list[Any] | None = None) -> dict[str, str]:
    """Pin *prep*'s agent as its room's reviewer, from the key it signs in with.

    Raises ``agent_identity.IdentityError`` (and saves nothing) when that can't
    be established: a room that already has a reviewer (unless *replace*), a
    relay this machine doesn't run, or a running daemon whose key isn't saved
    where it signs in from (codex, review of 0e361b2d / 0941c028)."""
    cfg, name, room = prep["cfg"], prep["name"], prep["room"]
    files = reviewer_files([prep["state_dir"], *(state_dirs or [])])
    current = current_reviewers(room, files)
    if current and not replace:
        who = ", @".join(current)
        raise agent_identity.IdentityError(
            f"#{room} already has @{who} as its reviewer (add --replace to change it)")
    if not prep["legacy"]:
        raise agent_identity.IdentityError(
            "this relay isn't one you run, so its agents can't be pinned from here; "
            "pin one after it posts: quorus room reviewer "
            f"{room} {name}")
    url, where = cfg["relay_url"], prep["state_dir"]
    if prep["existing"]:
        from quorus import bundle_client

        key = bundle_client.load_participant_key(url, name, agent_identity.key_dir(name, where))
        if not key:
            raise agent_identity.IdentityError(
                f"couldn't confirm which identity @{name} posts with. Run `quorus agent "
                f"remove {name}`, then `quorus agent add {name.rsplit('-', 1)[-1]} --room "
                f"{room} --reviewer`")
    else:
        key = agent_identity.ensure_key(url, prep["cred"], name, where)
    pin = agent_identity.identity(url, key)
    # All or nothing: a daemon that reads a file the pin didn't reach would publish
    # ungated while the room is told it's gated. On any failure, put back what
    # was written and keep every existing gate.
    before = {f: (f.read_bytes() if f.exists() else None) for f in files}
    try:
        for f in files:
            _gate.save_reviewer(room, name, tenant=pin["tenant"], claim_id=pin["claim_id"],
                                path=f)
    except (OSError, ValueError) as exc:
        for f, data in before.items():
            try:
                if data is None:
                    f.unlink(missing_ok=True)
                else:
                    from quorus.private_files import write_private_text
                    write_private_text(f, data.decode("utf-8"))
            except OSError:
                pass
        raise agent_identity.IdentityError(f"couldn't save the reviewer to {f} ({exc})") from exc
    return pin


def gate_line(room: str, reviewer: str, repo: Any) -> str:
    where = f"commits in {repo}" if repo else f"commits in #{room}'s repo"
    return (f"Review gate on: {where} wait for @{reviewer}'s GO before they reach main. "
            f"@{reviewer}'s own commits wait for you (`quorus review publish <sha> "
            f"--unreviewed`). Turn it off: `quorus room reviewer {room} --none`.")


def failed_line(room: str, why: str, tool: str, kept: dict[str, str] | None = None) -> str:
    """The honest line after a failed setup: what still gates the room, if anything."""
    if kept:
        who = ", @".join(kept)
        return (f"Couldn't set a new reviewer ({why}); #{room} keeps its current gate: "
                f"commits wait for @{who}'s GO before main.")
    return (f"Couldn't turn on the review gate ({why}); agents publish when a turn ends, "
            f"as before. Try: `quorus agent add {tool} --room {room} --reviewer`.")


def post(prep: dict[str, Any], text: str) -> None:
    """Say *text* in the room as the owner, with the owner's join credential.
    Best effort: the terminal already shows it."""
    cfg = prep["cfg"]
    try:
        _relay_http.post(f"{cfg['relay_url'].rstrip('/')}/rooms/{prep['room']}/messages",
                         json={"from_name": prep["me"], "content": text},
                         headers={"Authorization": f"Bearer {prep['bearer']}"}, timeout=10,
                         follow_redirects=False)
    except Exception:  # best effort: the line is printed either way
        pass
