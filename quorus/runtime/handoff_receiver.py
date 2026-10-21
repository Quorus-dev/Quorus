"""Receiver proof gate primitives; live daemon activation requires a separate review."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from quorus.protocol.handoff_capsule import HandoffCapsule
from quorus.runtime.check_config import CheckConfig
from quorus.runtime.checkpoint import run_checkpoint
from quorus.runtime.handoff_adoption import Declined, Git, adopt
from quorus.runtime.handoff_hold import PublicationHold, restore
from quorus.runtime.handoff_state import TERMINAL, Duplicate, Journal, StateError


@dataclass(frozen=True)
class Proof:
    outcome: str
    cause: str = ""

    @property
    def writable(self) -> bool:
        return self.outcome in ("reproduced", "weak")


def classify(recorded, rerun: dict) -> Proof:
    """A completed nonzero failure is required on BOTH sides, in strict order."""
    if recorded.status != "ran" or type(recorded.exit) is not int or recorded.exit <= 0:
        return Proof("unverifiable", "no recorded failing check")
    status, code = rerun.get("status"), rerun.get("exit")
    if status in ("timed_out", "error"):
        return Proof("unverifiable", f"check {status}")
    if status == "modified_tree":
        return Proof("unverifiable", "check modified tree")
    if status not in ("ran", "passing") or type(code) is not int:
        return Proof("unverifiable", "check did not complete")
    if code == 0:
        return Proof("already_passing", "owner must decide whether to publish")
    if status != "ran" or code < 0:
        return Proof("unverifiable", "no completed failure")
    if code != recorded.exit:
        return Proof("stale", "failure exit differs")
    return Proof("reproduced" if rerun.get("tail_sha256") == recorded.tail_sha256 else "weak")


def verify(root: Path, journal: Journal, capsule: HandoffCapsule, *,
           branch: str, integration: str, config: CheckConfig | None,
           claimed: bool, held: bool, env: dict[str, str],
           known_secrets: tuple[str, ...] = (), recovering: bool = False) -> Proof:
    """Validate/adopt/rerun under a cross-process lock. This never starts a harness.

    claimed is an explicit result of the caller's fresh relay lookup, not capsule
    data. The daemon must look up the claim AGAIN immediately before launch().
    A restart explicitly invokes recovery; duplicate network deliveries do not.
    """
    with journal.locked():
        hold = PublicationHold(root, journal, capsule)
        entry = journal.read(capsule)
        if entry is not None:
            if not recovering or entry["state"] in TERMINAL | {"continuing"}:
                raise Duplicate("handoff already received")
        else:
            journal.write(capsule, "received")
        if not claimed:
            result = Proof("unverifiable", "relay claim is not held by receiver")
        elif config is None or capsule.check.status != "ran":
            result = Proof("unverifiable", "no configured failing check")
        elif (capsule.check.config_id != config.config_id or any(
                getattr(capsule.check, k) != v for k, v in config.record().items())):
            result = Proof("unverifiable", "owner check configuration mismatch")
        else:
            try:
                adopt(Git(root), journal, capsule, branch=branch, integration=integration,
                      held=held, recovering=recovering)
                rerun = run_checkpoint(root, capsule.repo.head, config, env=env,
                                       known_secrets=known_secrets)
                result = classify(capsule.check, rerun)
            except Declined:
                result = Proof("declined", "receiver has unfinished or unpublished work")
            except StateError:
                # Never persist exception text (git/tool errors can echo data).
                result = Proof("unverifiable", "snapshot adoption failed; owner needed")
        restored = (not result.writable and hold.owned()
                    and restore(Git(root), journal, capsule, hold))
        state = "verified" if result.writable else result.outcome
        journal.write(capsule, state, outcome=result.outcome, cause=result.cause)
        if restored:
            hold.release()  # terminal journal is durable before publishing is unblocked
        return result


def _refuse_launch(root, journal, capsule, cause) -> bool:
    hold = PublicationHold(root, journal, capsule)
    restored = restore(Git(root), journal, capsule, hold)
    journal.write(capsule, "unverifiable", cause=cause)
    if restored:
        hold.release()
    return False


def launch(root: Path, journal: Journal, capsule: HandoffCapsule, *, claimed: bool) -> bool:
    """Write before starting the one permitted writable turn. No claim, no turn."""
    with journal.locked():
        entry = journal.read(capsule)
        if entry is None or entry["state"] != "verified":
            return False
        if not claimed:
            return _refuse_launch(root, journal, capsule, "relay claim changed before launch")
        try:
            git = Git(root)
            if (git.text("symbolic-ref", "HEAD") != f"refs/heads/{entry.get('branch')}"
                    or git.text("rev-parse", "HEAD") != capsule.repo.head
                    or git.text("rev-parse", "HEAD^{tree}") != capsule.repo.tree
                    or not git.clean() or not PublicationHold(root, journal, capsule).owned()):
                raise StateError("proof tree changed before launch")
        except StateError:
            return _refuse_launch(root, journal, capsule, "proof tree changed before launch")
        journal.write(capsule, "continuing")
        return True
