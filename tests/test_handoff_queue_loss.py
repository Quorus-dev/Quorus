"""File-mode queue loss must never be reconstructed from a delivered capsule."""
import asyncio

import httpx
import pytest

from quorus.runtime import failover, handoff_completion, handoff_delivery
from quorus.runtime.handoff_hold import PublicationHold
from quorus.runtime.handoff_ownership import owns_claim
from quorus.services.work_queue_svc import WorkQueueSvc
from tests.test_handoff_completion import _continuation
from tests.test_handoff_delivery import Relay
from tests.test_unfinished_wakes import _git, reflexd

pytest_plugins = ["tests.test_handoff_delivery"]


def lookup(queue):
    relay = reflexd.RelayClient(relay_url="http://synthetic", api_key="synthetic")

    async def respond(request):
        assert request.url.path == "/v1/work_queue/build"
        tasks = await queue[0].list("t", "r", actor=request.url.params["actor"])
        return httpx.Response(200, json={"tasks": tasks})

    async def headers():
        return {}

    relay._headers = headers
    relay._client = httpx.AsyncClient(transport=httpx.MockTransport(respond),
                                      base_url="http://synthetic")
    return relay


def test_restart_before_proof_cannot_grant_a_continuation_from_the_capsule(wiring):
    _, daemon, wt, cap, _, envelope = wiring
    queue = [WorkQueueSvc()]
    relay = Relay()
    data = failover.continuation(envelope)
    before = _git(wt.path, "rev-parse", "HEAD")

    async def run():
        await queue[0].claim("t", "r", task_id="task", actor=daemon.config.participant_name)
        client = lookup(queue)
        async with client._client:
            assert await owns_claim(
                client, room="build", participant=daemon.config.participant_name, task_id="task")
            queue[0] = WorkQueueSvc()  # file mode: a new relay has no persisted queue
            relay.mission_task_for = client.mission_task_for
            decision = await handoff_delivery.receive(
                daemon, relay, envelope, data, wt, trusted=True, mode="autonomous",
                bindings=reflexd.ROOM_BINDINGS_PATH)
        assert not decision.writable and decision.turn is None
        assert "unverifiable" in relay.posted[-1]["content"]
        assert "read-only" in relay.posted[-1]["content"]

    asyncio.run(run())
    assert _git(wt.path, "rev-parse", "HEAD") == before
    assert cap.repo.head != before  # the capsule never authorizes adoption itself


def test_restart_after_launch_cannot_commit_or_release_the_hold(monkeypatch, tmp_path):
    _, daemon, wt, turn = _continuation(monkeypatch, tmp_path)
    (wt.path / "CHANGELOG.md").write_text("receiver edits before relay restart\n")
    before = _git(wt.path, "rev-parse", "HEAD")

    async def run():
        relay = lookup([WorkQueueSvc()])  # no claim after file-mode restart
        async with relay._client:
            with pytest.raises(OSError, match="claim changed"):
                await handoff_completion.finish(
                    turn, relay, participant=daemon.config.participant_name,
                    room="build", root=wt.path,
                    commit=lambda: pytest.fail("lost claim must not commit"))

    asyncio.run(run())
    assert _git(wt.path, "rev-parse", "HEAD") == before
    assert turn.journal.read(turn.capsule)["state"] == "continuing"
    assert PublicationHold(wt.path, turn.journal, turn.capsule).owned()
