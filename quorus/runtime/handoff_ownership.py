"""Claim checks and starter fences, independent of harness prompts."""

from __future__ import annotations

from quorus.protocol.handoff_capsule import HandoffCapsule
from quorus.runtime.handoff_adoption import Git
from quorus.runtime.handoff_state import Journal, StateError


async def owns_claim(relay, *, room: str, participant: str, task_id: str | None) -> bool:
    """Unknown/missing/reassigned claims never grant write authority."""
    if not task_id:
        return False
    try:
        task = await relay.mission_task_for(room=room, participant=participant)
    except Exception:
        return False
    return (isinstance(task, dict) and task.get("task_id") == task_id
            and task.get("claimed_by") == participant)


def sent(journal: Journal, capsule: HandoffCapsule, target: str) -> None:
    """Persist the starter fence BEFORE moving the claim or posting the handoff."""
    with journal.locked():
        if journal.read(capsule) is not None:
            raise StateError("starter already handed off this capsule")
        journal.write(capsule, "sent", target=target, snapshot=capsule.model_dump())


def fenced(journal: Journal, git: Git, integration: str) -> bool:
    """Keep the starter's tree read-only until every sent head is integrated.

    Conservatively fence the whole tree, including a differently worded new ask:
    writing another task there could publish the unfinished work out from under
    its receiver. Claim expiry or a returning quota does not lift this fence.
    A failed lookup leaves the fence in place and its unfinished branch intact.
    """
    with journal.locked():
        blocked = False
        for entry in journal.pending_sends():
            capsule = HandoffCapsule.model_validate(entry["snapshot"])
            try:
                git.run("merge-base", "--is-ancestor", capsule.repo.head, integration)
            except StateError:
                blocked = True
            else:
                journal.write(capsule, "done")
        return blocked
