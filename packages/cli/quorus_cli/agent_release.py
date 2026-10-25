"""`quorus agent remove` frees the agent's name on the relay, when this machine
can prove it holds that relay's claims file (take-3 bug 2, docs/plans/take3-fixes.md).

The relay kept the participant-key claim after `remove`, so a later `agent add`
of the same name got 409 wherever the saved key wasn't. The claim is released
only through the record of a relay `quorus` started here and that still runs:
its own claims dir, never one guessed from the CLI's environment (codex,
review of 285e27ab). Otherwise nothing is released and `remove` says so.
"""

from __future__ import annotations

from pathlib import Path

from quorus import bundle_client
from quorus_cli import agent_identity


def held_keys(name: str, relay_url: str) -> dict[Path, str]:
    """{key dir: key} for each copy of *name*'s key saved on this machine.
    Read before `remove` drops the state record that names one of the dirs."""
    out: dict[Path, str] = {}
    for where in agent_identity.known_key_dirs(name):
        key = bundle_client.load_participant_key(relay_url, name, where)
        if key:
            out[where] = key
    return out


def release(name: str, relay_url: str, held: dict[Path, str]) -> str | None:
    """Free *name* on *relay_url* if this machine runs it; the line to print,
    or None when the agent never held a key here (nothing was claimed)."""
    if not held:
        return None
    from quorus.runtime import local_relay

    claims = local_relay.started_relay(relay_url)
    if claims is not None:
        from quorus.auth.participant_keys import ParticipantKeyStore

        freed = ParticipantKeyStore(claims).release(name)
        for where in held:
            bundle_client.forget_participant_key(relay_url, name, where)
        return (f"[success]✓[/] freed @{name}'s name on {relay_url}" if freed else None)
    # Kept: a later re-add with any --state-dir finds it in the CLI's own dir.
    default = agent_identity.key_dir(name)
    if default not in held:
        bundle_client.save_participant_key(relay_url, name, next(iter(held.values())),
                                           default)
    return (f"[dim]@{name}'s name stays claimed on {relay_url}; its owner can free it "
            f"with `quorus release-name {name} --data-dir <relay's data dir>` on that "
            "machine[/]")
