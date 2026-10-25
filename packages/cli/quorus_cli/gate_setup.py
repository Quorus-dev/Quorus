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


def _bound(state: Path | None) -> dict[str, Path] | None:
    """{room: resolved repo} from the bindings file a daemon with *state* reads;
    None when that file exists but can't be read."""
    import json

    from quorus_cli import agent_cmd

    try:
        data = json.loads(agent_cmd.bindings_path(state).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    out: dict[str, Path] = {}
    for room, raw in (data.items() if isinstance(data, dict) else []):
        raw = raw.get("path") if isinstance(raw, dict) else raw
        if isinstance(raw, str) and raw:
            out[str(room)] = Path(raw).expanduser().resolve()
    return out


def _room_repo(room: str, prep: dict[str, Any]) -> Path | None:
    """The repo *room* gates: the prepared agent's binding, else the CLI's."""
    if prep.get("repo"):
        return Path(prep["repo"]).expanduser().resolve()
    for state in (prep.get("state_dir"), None):
        view = _bound(state)
        if view and room in view:
            return view[room]
    return None


def _recorded_agents() -> list[tuple[str, Path]]:
    """(agent, --state-dir) for each daemon `agent add` started with one."""
    from quorus_cli import agent_cmd

    out = []
    for rec in sorted(agent_cmd._agents_dir().glob("*.state-dir")):
        try:
            text = rec.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            out.append((rec.name[: -len(".state-dir")], Path(text)))
    return out


def _recorded() -> list[Path]:
    return list(dict.fromkeys(state for _, state in _recorded_agents()))


def daemon_state_dirs(room: str) -> list[Path]:
    """Every recorded agent ``--state-dir`` on this machine whose daemon gates
    *room*'s repo: it binds *room*, or any room bound to the same repo (the gate
    is repo-wide). Found from ``agent add``'s records, so a builder added before
    the reviewer still gets the pin (codex, review of 6de08414). A state dir whose
    bindings can't be read is included: better a pin it may not need than a
    builder left ungated."""
    states = _recorded()
    views = {None: _bound(None), **{st: _bound(st) for st in states}}
    repos = {view[room] for view in views.values() if view and room in view}
    out = []
    for st in states:
        view = views[st]
        if view is None or room in view or any(p in repos for p in view.values()):
            out.append(st)
    return out


def room_files(room: str, extra: list[Any] | None = None) -> list[Path]:
    """Every reviewers file a daemon on this machine reads for *room*'s repo:
    the CLI's, plus each state dir that gates it (take-3 bug 3)."""
    return reviewer_files([*(extra or []), *daemon_state_dirs(room)])


def room_repo(room: str) -> Path | None:
    """The repo *room* is bound to here: the CLI's binding, else a recorded
    state dir's (a daemon started with --state-dir binds it there)."""
    for state in [None, *_recorded()]:
        view = _bound(state)
        if view and room in view:
            return view[room]
    return None


def readers(files: list[Path]) -> dict[Path, list[str]]:
    """{reviewers file: the recorded agents whose daemon reads it}."""
    out: dict[Path, list[str]] = {f: [] for f in files}
    for name, state in _recorded_agents():
        f = (state / "room-reviewers.json").resolve()
        if f in out:
            out[f].append(name)
    return out


def where_lines(files: list[Path]) -> list[str]:
    """One line per file: where the gate was written and who reads it."""
    lines = []
    for f, names in readers(files).items():
        who = ", ".join(f"@{n}" for n in names) if names else "agents without --state-dir"
        lines.append(f"  {f}  [dim](read by {who})[/]")
    return lines


def write_all(files: list[Path], change: Any) -> None:
    """``change(file)`` on every file, all or nothing: a daemon reading a file
    the change didn't reach would gate differently from what the room was told.
    On a failure what was written is put back, and IdentityError names the file."""
    before: dict[Path, bytes | None] = {}
    for f in files:
        try:
            before[f] = f.read_bytes() if f.exists() else None
        except OSError as exc:  # nothing written yet: say which file, not a traceback
            raise agent_identity.IdentityError(f"couldn't read {f} ({exc})") from exc
    for f in files:
        try:
            change(f)
        except (OSError, ValueError) as exc:
            for g, data in before.items():
                try:
                    if data is None:
                        g.unlink(missing_ok=True)
                    else:
                        from quorus.private_files import write_private_text
                        write_private_text(g, data.decode("utf-8"))
                except OSError:
                    pass
            raise agent_identity.IdentityError(f"couldn't save the gate to {f} ({exc})") \
                from exc


def current_reviewers(room: str, files: list[Path]) -> dict[str, str]:
    """{reviewer name: file} for each file that already gates *room*."""
    found: dict[str, str] = {}
    for f in files:
        pin = _gate.reviewer_for(room, f)
        if pin is not None:
            found.setdefault(pin.get("name") or "?", str(f))
    return found


def _signing_key(prep: dict[str, Any], *, flag: str = "--reviewer") -> str:
    """The key *prep*'s agent signs in with: the saved one for a running daemon
    (checked, never assumed; codex, review of 0941c028), else claimed now into
    the key dir its daemon will load."""
    cfg, name, room, where = prep["cfg"], prep["name"], prep["room"], prep["state_dir"]
    if prep["existing"]:
        from quorus import bundle_client

        key = bundle_client.load_participant_key(cfg["relay_url"], name,
                                                 agent_identity.key_dir(name, where))
        if not key:
            raise agent_identity.IdentityError(
                f"couldn't confirm which identity @{name} posts with. Run `quorus agent "
                f"remove {name}`, then `quorus agent add {name.rsplit('-', 1)[-1]} --room "
                f"{room} {flag}`")
        return key
    return agent_identity.ensure_key(cfg["relay_url"], prep["cred"], name, where)


def pin_reviewer(prep: dict[str, Any], *, replace: bool = False,
                 state_dirs: list[Any] | None = None,
                 second: dict[str, Any] | None = None) -> dict[str, Any]:
    """Pin *prep*'s agent as its room's reviewer, from the key it signs in with.
    With *second* (the lead's prep), that agent is pinned the same way to review
    the reviewer's own commits (docs/plans/reviewer-backup.md). The returned pin
    has ``second`` only if it was saved.

    Raises ``agent_identity.IdentityError`` (and saves nothing) when the first
    pin can't be established: a room that already has a reviewer (unless
    *replace*), a relay this machine doesn't run, or a running daemon whose key
    isn't saved where it signs in from (codex, review of 0e361b2d / 0941c028).
    A second that can't be established is left out: the reviewer's own commits
    then wait for the owner, as before."""
    cfg, name, room = prep["cfg"], prep["name"], prep["room"]
    files = room_files(room, [prep["state_dir"], *(state_dirs or [])])
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
    url = cfg["relay_url"]
    pin: dict[str, Any] = agent_identity.identity(url, _signing_key(prep))
    backup = None
    if second is not None and second.get("name") and second["name"] != name:
        try:
            backup = {"name": second["name"],
                      **agent_identity.identity(url, _signing_key(second))}
        except agent_identity.IdentityError:
            backup = None
    # All or nothing: a daemon that reads a file the pin didn't reach would publish
    # ungated while the room is told it's gated. Any failure keeps every old gate.
    repo = _room_repo(room, prep)
    write_all(files, lambda f: _gate.save_reviewer(
        room, name, tenant=pin["tenant"], claim_id=pin["claim_id"], path=f, repo=repo,
        second=backup))
    if backup is not None:
        pin["second"] = backup
    return pin


def pinned_as(name: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Where *name* is pinned on this machine: ``[(room, role, reviewer)]`` with
    role "reviewer" or "second" (then *reviewer* is whose commits it reviewed),
    plus the reviewers files that couldn't be read. Every file a daemon here
    reads (the CLI's and each recorded state dir's), not the agent's rooms: it
    may have left them, or the relay may be down (codex, plan review of
    edba2161). Call it before `agent remove` drops the agent's state record."""
    import json

    found: list[tuple[str, str, str]] = []
    bad: list[str] = []
    for f in reviewer_files(_recorded()):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            bad.append(str(f))
            continue
        if not isinstance(data, dict):
            bad.append(str(f))
            continue
        for room, entry in data.items():
            if not isinstance(entry, dict):
                continue
            second = entry.get("second")
            if entry.get("name") == name:
                hit = (str(room), "reviewer", name)
            elif isinstance(second, dict) and second.get("name") == name:
                hit = (str(room), "second", str(entry.get("name") or "?"))
            else:
                continue
            if hit not in found:
                found.append(hit)
    return found, bad


def removed_line(room: str, role: str, name: str, reviewer: str) -> str:
    """What `agent remove` says about a gate the removed agent was part of. The
    gate itself is kept: removing an agent never quietly turns it off."""
    if role == "second":
        return (f"@{reviewer}'s own commits in #{room} now wait for you (`quorus review "
                f"publish <sha> --unreviewed`); pin another with `quorus room reviewer "
                f"{room} {reviewer} --second <name>`.")
    return (f"#{room}'s commits now wait for a reviewer that's gone (@{name}). Pick "
            f"another: `quorus room reviewer {room} <name>`, publish one yourself: "
            f"`quorus review publish <sha> --unreviewed`, or turn the gate off: "
            f"`quorus room reviewer {room} --none`.")


def gate_line(room: str, reviewer: str, repo: Any, second: str | None = None) -> str:
    where = f"commits in {repo}" if repo else f"commits in #{room}'s repo"
    if second:
        theirs = f"@{reviewer}'s own commits wait for @{second}'s."
    else:
        theirs = (f"@{reviewer}'s own commits wait for you (`quorus review publish <sha> "
                  f"--unreviewed`).")
    return (f"Review gate on: {where} wait for @{reviewer}'s GO before they reach main. "
            f"{theirs} Turn it off: `quorus room reviewer {room} --none`.")


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
                         # A status line: it names the agents, and a chat line woke
                         # both just to say hi (first-ten-minutes.md, item 2).
                         json={"from_name": prep["me"], "content": text,
                               "message_type": "status"},
                         headers={"Authorization": f"Bearer {prep['bearer']}"}, timeout=10,
                         follow_redirects=False)
    except Exception:  # best effort: the line is printed either way
        pass
