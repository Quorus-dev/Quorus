"""A rejected capsule must never open a claim for a chat takeover."""
import asyncio
import json

import httpx
import pytest

from quorus.relay import app
from quorus.runtime.handoff_capsule import build_capsule
from tests import test_social_routes as social
from tests.test_handoff_capsule import arguments
from tests.test_reflexd_inbox_drain import reflexd

client = social.client
clean_state = social.clean_state
room_id = social.room_id
real_ref_id = social.real_ref_id
pytest_plugins = ["tests.test_handoff_delivery"]


def body(room, root):
    args = arguments()
    args.update(actor="alice", root=root)
    args["repo"].update(branch="quorus/alice", unfinished="quorus/alice-unfinished")
    cap = build_capsule(**args)
    return dict(actor="alice", room_id=room, ref_message_id=root, payload=dict(to="bob",
        failover=dict(original_message_id=root, task_id="task", task="fix tests",
            reason="quota", branch=cap.repo.branch, last_commit=cap.repo.head[:12],
            failed_agents=["alice"], handoffs=1, capsule=cap.model_dump())))


async def claim(client, room):
    response = await client.post("/v1/social/claim", headers=social._user_headers("alice"),
        json=dict(actor="alice", room_id=room,
                  payload=dict(task_id="task", scope="fix tests", eta_seconds=0)))
    assert response.status_code == 200, response.text


async def owner(client, room):
    response = await client.get(f"/v1/work_queue/{room}", headers=social._user_headers("alice"))
    return response.json()["tasks"][0]["claimed_by"]


async def test_capsule_rejection_preserves_claim_and_blocks_chat_claim(
        client, room_id, real_ref_id):
    await claim(client, room_id)
    value = body(room_id, real_ref_id)
    value["payload"]["failover"]["capsule"]["unknown_old_relay_field"] = True
    response = await client.post("/v1/handoff", json=value, headers=social._user_headers("alice"))
    assert response.status_code in (400, 422)
    assert await owner(client, room_id) == "alice"
    race = await client.post("/v1/social/claim", headers=social._user_headers("carol"),
        json=dict(actor="carol", room_id=room_id,
                  payload=dict(task_id="task", scope="steal", eta_seconds=0)))
    assert race.status_code == 409


async def test_accepted_defer_precedes_transfer_and_retry_is_idempotent(
        client, room_id, real_ref_id, monkeypatch):
    await claim(client, room_id)
    queue = app.state.work_queue_service
    accepted = app.state.room_msg_service.send
    held = []

    async def send(*a, **kw):
        # Observe the in-memory owner under the transaction's own lock. Public
        # claim/get calls wait for that lock and cannot observe a gap.
        held.append(queue._bucket(social._TEST_TENANT_ID, room_id)["task"]["claimed_by"])
        return await accepted(*a, **kw)

    monkeypatch.setattr(app.state.room_msg_service, "send", send)
    value = body(room_id, real_ref_id)
    for _ in range(2):
        response = await client.post("/v1/handoff", json=value,
                                     headers=social._user_headers("alice"))
        assert response.status_code == 200, response.text
    assert held == ["alice"] and await owner(client, room_id) == "bob"
    state = (await client.get(f"/v1/social/state/{room_id}",
                             headers=social._user_headers("alice"))).json()
    assert state["claims"]["task"]["actor"] == "bob"
    inbox = (await client.get("/messages/bob", headers=social._user_headers("bob"),
                              params={"wait": 0})).json()["messages"]
    assert len([m for m in inbox if m.get("message_type") == "social"
                and json.loads(m["content"]).get("verb") == "defer"]) == 1


async def test_delivery_failure_and_concurrent_chat_claim_cannot_steal(
        client, room_id, real_ref_id, monkeypatch):
    await claim(client, room_id)
    entered, resume = asyncio.Event(), asyncio.Event()

    async def fail(*a, **kw):
        entered.set()
        await resume.wait()
        raise httpx.ConnectError("synthetic delivery failure")

    monkeypatch.setattr(app.state.room_msg_service, "send", fail)
    transfer = asyncio.create_task(client.post("/v1/handoff", json=body(room_id, real_ref_id),
                                                headers=social._user_headers("alice")))
    await asyncio.wait_for(entered.wait(), 2)
    thief = asyncio.create_task(client.post("/v1/social/claim",
        headers=social._user_headers("carol"),
        json=dict(actor="carol", room_id=room_id,
                  payload=dict(task_id="task", scope="steal", eta_seconds=0))))
    resume.set()
    assert (await transfer).status_code == 503
    assert (await thief).status_code == 409
    assert await owner(client, room_id) == "alice"


async def test_old_relay_is_detected_without_release_or_repeated_404s():
    from quorus.runtime.handoff_transport import RelayTooOld

    calls = []

    def old(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"status": "ok"})

    relay = reflexd.RelayClient(relay_url="http://old", api_key="synthetic")
    async with httpx.AsyncClient(transport=httpx.MockTransport(old), base_url="http://old") as c:
        relay._client = c

        async def headers():
            return {}

        relay._headers = headers
        for _ in range(2):
            with pytest.raises(RelayTooOld, match="relay too old for handoff"):
                await relay.post_social_defer(room_id="room", actor="alice", target="bob",
                                             failover=body("room", "root")["payload"]["failover"])
    assert calls == [("GET", "/health")]


async def test_new_client_uses_health_and_atomic_relay(client, room_id, real_ref_id):
    await claim(client, room_id)
    relay = reflexd.RelayClient(relay_url="http://test", api_key="synthetic")
    relay._client = client

    async def headers():
        return social._user_headers("alice")

    relay._headers = headers
    value = body(room_id, real_ref_id)
    accepted = await relay.post_social_defer(room_id=room_id, actor="alice", target="bob",
        ref_message_id=real_ref_id, failover=value["payload"]["failover"])
    assert accepted["ok"] and await owner(client, room_id) == "bob"


@pytest.mark.parametrize("status", [400, 404, 405, 422])
async def test_rejecting_atomic_route_never_falls_back_to_release(status):
    from quorus.runtime.handoff_transport import HandoffRejected, RelayTooOld

    calls = []

    def reject(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/health":
            return httpx.Response(200, json={"atomic_capsule_handoff": 1})
        return httpx.Response(status, json={"detail": "target left the room"})

    relay = reflexd.RelayClient(relay_url="http://reject", api_key="synthetic")
    async with httpx.AsyncClient(transport=httpx.MockTransport(reject),
                                 base_url="http://reject") as c:
        relay._client = c

        async def headers():
            return {}

        relay._headers = headers
        for _ in range(2):
            with pytest.raises(RelayTooOld if status in (404, 405) else HandoffRejected):
                await relay.post_social_defer(room_id="room", actor="alice", target="bob",
                                             failover=body("room", "root")["payload"]["failover"])
    assert calls == [("GET", "/health"), ("POST", "/v1/handoff")] + (
        [("POST", "/v1/handoff")] if status in (400, 422) else [])
    assert relay._atomic_handoff is (status in (400, 422))


@pytest.mark.parametrize("status", [400, 422])
async def test_valid_rejection_does_not_disable_next_handoff(status):
    from quorus.runtime.handoff_transport import HandoffRejected

    posts = []

    def respond(request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"atomic_capsule_handoff": 1})
        assert request.url.path == "/v1/handoff"
        posts.append(request)
        return (httpx.Response(status, json={"detail": "target left the room"})
                if len(posts) == 1 else httpx.Response(200, json={"ok": True}))

    relay = reflexd.RelayClient(relay_url="http://reject", api_key="synthetic")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond),
                                 base_url="http://reject") as c:
        relay._client = c

        async def headers():
            return {}

        relay._headers = headers
        kwargs = dict(room_id="room", actor="alice", target="bob",
                      failover=body("room", "root")["payload"]["failover"])
        with pytest.raises(HandoffRejected, match="target left the room"):
            await relay.post_social_defer(**kwargs)
        assert await relay.post_social_defer(**kwargs) == {"ok": True}
        assert relay._atomic_handoff is True


def test_validation_rejection_does_not_echo_capsule_input():
    from quorus.runtime.handoff_transport import HandoffRejected

    assert "private-input" not in str(HandoffRejected([{"input": "private-input"}]))


async def test_busy_decline_returns_claim_atomically_and_cannot_replay_original_send(
        client, room_id, real_ref_id):
    from quorus.protocol.handoff_capsule import HandoffCapsule
    from quorus.runtime.handoff_state import fingerprint

    await claim(client, room_id)
    value = body(room_id, real_ref_id)
    cap = HandoffCapsule.model_validate(value["payload"]["failover"]["capsule"])
    sent = await client.post("/v1/handoff", json=value, headers=social._user_headers("alice"))
    assert sent.status_code == 200
    returned = await client.post("/v1/handoff", headers=social._user_headers("bob"), json=dict(
        actor="bob", room_id=room_id, ref_message_id=real_ref_id, payload=dict(to="alice",
        decline=dict(capsule_id=cap.id, fingerprint=fingerprint(cap),
                     task_id="task", reason="busy"))))
    assert returned.status_code == 200, returned.text
    assert await owner(client, room_id) == "alice"
    replay = await client.post("/v1/handoff", json=value, headers=social._user_headers("alice"))
    assert replay.status_code == 200 and replay.json()["state_change"]["duplicate"]
    assert await owner(client, room_id) == "alice"


@pytest.mark.parametrize("failure", ["queue_full", "redis"])
async def test_delivery_or_persistence_refusal_keeps_sender_claim(
        client, room_id, real_ref_id, monkeypatch, failure):
    await claim(client, room_id)
    if failure == "queue_full":
        async def rejected(*a, **kw):
            return dict(id="synthetic", warning="full", skipped_recipients=["bob"])
        monkeypatch.setattr(app.state.room_msg_service, "send", rejected)
    else:
        class RedisDown:
            async def hgetall(self, *a):
                return {}

            async def hset(self, *a):
                raise OSError("synthetic persistence failure")

        monkeypatch.setattr(app.state.work_queue_service, "_redis", RedisDown())
    response = await client.post("/v1/handoff", json=body(room_id, real_ref_id),
                                 headers=social._user_headers("alice"))
    assert response.status_code == 503
    assert await owner(client, room_id) == "alice"


def test_old_relay_alerts_owner_before_checkpoint_without_claim_change(wiring, monkeypatch):
    from quorus.runtime import handoff_sender
    from quorus.runtime.handoff_transport import RelayTooOld
    from tests.test_handoff_retry import Candidates, policy

    _, daemon, wt, cap, _, _ = wiring
    relay, alerts = Candidates(), []

    async def old():
        raise RelayTooOld()

    monkeypatch.setattr(relay, "require_handoff", old)
    monkeypatch.setattr(daemon, "_alert_owner", lambda *a: alerts.append(a))
    monkeypatch.setattr(handoff_sender, "snapshot", lambda *a: pytest.fail("checkpoint ran"))
    result = asyncio.run(handoff_sender.attempt(daemon, relay,
        dict(room="build", message_id=cap.root), reason="quota", mission={"task_id": "task"},
        worktree=wt, **policy()))
    assert result == "stopped" and not relay.defers
    assert relay.holder == daemon.config.participant_name
    assert alerts and "relay too old for handoff" in alerts[-1][1]
    assert relay.posted[-1]["message_type"] == "status"
    assert "Relay too old for handoff" in relay.posted[-1]["content"]


@pytest.mark.parametrize("busy", [False, True])
def test_defer_rejection_after_probe_does_not_release_claim(wiring, monkeypatch, busy):
    from quorus.runtime import handoff_sender
    from quorus.runtime.handoff_transport import HandoffRejected
    from tests.test_handoff_retry import policy
    from tests.test_handoff_selection import Receiver
    from tests.test_unfinished_wakes import reflexd as runtime_reflexd

    _, daemon, wt, cap, _, _ = wiring
    relay, alerts = Receiver(), []
    relay.status = "busy" if busy else "active"

    async def sleep(delay):
        relay.status = "active"

    monkeypatch.setattr(asyncio, "sleep", sleep)

    async def reject(**kw):
        raise HandoffRejected("target left the room")

    async def no_release(**kw):
        pytest.fail("capsule path must never release before acceptance")

    monkeypatch.setattr(relay, "post_social_defer", reject)
    monkeypatch.setattr(relay, "release_for_failover", no_release)
    monkeypatch.setattr(daemon, "_alert_owner", lambda *a: alerts.append(a))
    async def run():
        result = await handoff_sender.attempt(daemon, relay,
            dict(room="build", message_id=cap.root), reason="quota", mission={"task_id": "task"},
            worktree=wt, bindings=runtime_reflexd.ROOM_BINDINGS_PATH, **policy())
        if daemon._bg_tasks:
            await asyncio.gather(*list(daemon._bg_tasks))
        return result

    result = asyncio.run(run())
    assert result == ("waiting" if busy else "stopped")
    assert relay.holder == daemon.config.participant_name
    assert alerts and "handoff_rejected" in alerts[-1][1]
    assert "target left the room" in relay.posted[-1]["content"]


def test_relay_rejection_detail_cannot_introduce_room_mentions():
    from quorus.runtime.handoff_transport import HandoffRejected

    detail = HandoffRejected("target @teammate left; @all retry").detail
    assert "@" not in detail and "teammate" in detail


async def test_exact_handoff_claim_lookup_ignores_an_older_held_task():
    from quorus.runtime.handoff_ownership import owns_claim

    def respond(request):
        return httpx.Response(200, json={"tasks": [
            {"task_id": "old", "claimed_by": "bob", "status": "in_progress"},
            {"task_id": "new", "claimed_by": "bob", "status": "in_progress"}]})

    relay = reflexd.RelayClient(relay_url="http://claims", api_key="synthetic")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond),
                                 base_url="http://claims") as c:
        relay._client = c
        async def headers():
            return {}
        relay._headers = headers
        assert await owns_claim(relay, room="room", participant="bob", task_id="new")
        assert not await owns_claim(relay, room="room", participant="bob", task_id="absent")


async def test_claim_read_waits_for_atomic_transfer_even_with_an_old_claim(
        client, room_id, real_ref_id, monkeypatch):
    from quorus.runtime.handoff_ownership import owns_claim

    response = await client.post("/v1/social/claim", headers=social._user_headers("bob"),
        json=dict(actor="bob", room_id=room_id,
                  payload=dict(task_id="old", scope="old task", eta_seconds=0)))
    assert response.status_code == 200
    await claim(client, room_id)
    relay = reflexd.RelayClient(relay_url="http://test", api_key="synthetic")
    relay._client = client

    async def headers():
        return social._user_headers("bob")

    relay._headers = headers
    original = app.state.room_msg_service.send
    reads = []

    async def send(*a, **kw):
        reads.append(asyncio.create_task(owns_claim(
            relay, room=room_id, participant="bob", task_id="task")))
        await asyncio.sleep(0)
        assert not reads[0].done()  # queue lock prevents an early false receipt
        return await original(*a, **kw)

    monkeypatch.setattr(app.state.room_msg_service, "send", send)
    response = await client.post("/v1/handoff", json=body(room_id, real_ref_id),
                                 headers=social._user_headers("alice"))
    assert response.status_code == 200
    assert await reads[0]
    assert await owns_claim(relay, room=room_id, participant="bob", task_id="old")


@pytest.mark.parametrize("legacy", [False, True])
async def test_owner_discard_is_exact_authorized_and_replay_safe(
        client, room_id, real_ref_id, legacy):
    await claim(client, room_id)
    response = await client.post("/v1/handoff", json=body(room_id, real_ref_id),
                                 headers=social._user_headers("alice"))
    assert response.status_code == 200
    queue = app.state.work_queue_service
    await queue.claim(social._TEST_TENANT_ID, room_id, task_id="old", actor="bob")
    capsule_fp = response.json()["state_change"]["fingerprint"]
    if legacy:
        for receipt in queue._bucket(social._TEST_TENANT_ID, room_id)["task"][
                "metadata"]["handoff_receipts"].values():
            receipt.pop("fingerprint")
    payload = dict(room_id=room_id, receiver="bob", task_id="task", resolution_id=capsule_fp)
    forbidden = await client.post("/v1/handoff/discard", json=payload,
                                  headers=social._user_headers("carol"))
    assert forbidden.status_code == 403
    response = await client.post("/v1/handoff/discard", json=payload,
                                 headers=social._user_headers("bob"))
    assert response.status_code == 200, response.text
    assert response.json()["legacy_receipt"] is legacy
    bucket = queue._bucket(social._TEST_TENANT_ID, room_id)
    assert bucket["task"]["claimed_by"] is None and bucket["task"]["status"] == "pending"
    assert bucket["old"]["claimed_by"] == "bob"
    state = (await client.get(f"/v1/social/state/{room_id}",
                             headers=social._user_headers("alice"))).json()
    assert "task" not in state["claims"]
    # A resumed local discard cannot release the next person's newer claim.
    claimed = await client.post("/v1/social/claim", headers=social._user_headers("carol"),
        json=dict(actor="carol", room_id=room_id,
                  payload=dict(task_id="task", scope="new attempt", eta_seconds=0)))
    assert claimed.status_code == 200
    repeated = await client.post("/v1/handoff/discard", json=payload,
                                 headers=social._user_headers("bob"))
    assert repeated.status_code == 200 and repeated.json()["duplicate"]
    assert bucket["task"]["claimed_by"] == "carol"
    changed = await client.post("/v1/handoff/discard",
        json={**payload, "resolution_id": "b" * 64}, headers=social._user_headers("bob"))
    assert changed.status_code == 409 and bucket["task"]["claimed_by"] == "carol"


async def test_discard_persistence_failure_keeps_the_receiver_claim(
        client, room_id, real_ref_id):
    await claim(client, room_id)
    sent = await client.post("/v1/handoff", json=body(room_id, real_ref_id),
                             headers=social._user_headers("alice"))
    fp = sent.json()["state_change"]["fingerprint"]
    queue = app.state.work_queue_service

    class BrokenRedis:
        async def hset(self, *a):
            raise OSError("full")

    queue._redis = BrokenRedis()
    response = await client.post("/v1/handoff/discard", headers=social._user_headers("bob"),
        json=dict(room_id=room_id, receiver="bob", task_id="task", resolution_id=fp))
    assert response.status_code == 503
    assert queue._bucket(social._TEST_TENANT_ID, room_id)["task"]["claimed_by"] == "bob"


async def test_callers_cannot_forge_discard_acknowledgements():
    from quorus.services.work_queue_svc import WorkQueueSvc

    queue = WorkQueueSvc()
    value = {"handoff_discards": {"fake": "bob"}, "handoff_receipts": {"fake": {}}, "note": 1}
    added = await queue.add("t", "r", task_id="task", requested_by="bob", summary="x",
                            metadata=value)
    assert added["metadata"] == {"note": 1}
    changed = await queue.update("t", "r", task_id="task", actor="bob", metadata_patch=value)
    assert changed["metadata"] == {"note": 1}
