"""Gate by default, slice B: pick the reviewer, pin it from the key it actually
signs in with, and say so in the room (docs/plans/gate-by-default.md, Codex plan
GO b4b34727).

The pin is saved after every agent is prepared and before any daemon starts
(the wizard's prepare → pin → start), so no commit can publish ungated while the
gate is being set up. Any failure leaves the room's gate as it was and says so.
"""

from __future__ import annotations

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


def pin_reviewer(prep: dict[str, Any], *, replace: bool = False) -> dict[str, str]:
    """Pin *prep*'s agent as its room's reviewer, from the key it signs in with.

    Raises ``agent_identity.IdentityError`` (and saves nothing) when that can't
    be established: a room that already has a reviewer (unless *replace*), a
    relay this machine doesn't run, or a running daemon whose key isn't saved
    where it signs in from (codex, review of 0e361b2d / 0941c028)."""
    cfg, name, room = prep["cfg"], prep["name"], prep["room"]
    current = _gate.reviewer_for(room)
    if current is not None and not replace:
        who = current.get("name") or "a reviewer"
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
    try:
        _gate.save_reviewer(room, name, tenant=pin["tenant"], claim_id=pin["claim_id"])
    except (OSError, ValueError) as exc:
        raise agent_identity.IdentityError(f"couldn't save the reviewer ({exc})") from exc
    return pin


def gate_line(room: str, reviewer: str, repo: Any) -> str:
    where = f"commits in {repo}" if repo else f"commits in #{room}'s repo"
    return (f"Review gate on: {where} wait for @{reviewer}'s GO before they reach main. "
            f"@{reviewer}'s own commits wait for you (`quorus review publish <sha> "
            f"--unreviewed`). Turn it off: `quorus room reviewer {room} --none`.")


def failed_line(room: str, why: str, tool: str) -> str:
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
