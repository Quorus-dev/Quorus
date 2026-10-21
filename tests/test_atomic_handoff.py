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


@pytest.mark.parametrize("status", [400, 422])
async def test_rejecting_atomic_route_never_falls_back_to_release(status):
    from quorus.runtime.handoff_transport import RelayTooOld

    calls = []

    def reject(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/health":
            return httpx.Response(200, json={"atomic_capsule_handoff": 1})
        return httpx.Response(status)

    relay = reflexd.RelayClient(relay_url="http://reject", api_key="synthetic")
    async with httpx.AsyncClient(transport=httpx.MockTransport(reject),
                                 base_url="http://reject") as c:
        relay._client = c

        async def headers():
            return {}

        relay._headers = headers
        for _ in range(2):
            with pytest.raises(RelayTooOld):
                await relay.post_social_defer(room_id="room", actor="alice", target="bob",
                                             failover=body("room", "root")["payload"]["failover"])
    assert calls == [("GET", "/health"), ("POST", "/v1/handoff")]


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


def test_defer_rejection_after_probe_does_not_release_claim(wiring, monkeypatch):
    from quorus.runtime import handoff_sender
    from quorus.runtime.handoff_transport import RelayTooOld
    from tests.test_handoff_retry import Candidates, policy

    _, daemon, wt, cap, _, _ = wiring
    relay, alerts = Candidates(), []

    async def reject(**kw):
        raise RelayTooOld()

    async def no_release(**kw):
        pytest.fail("capsule path must never release before acceptance")

    monkeypatch.setattr(relay, "post_social_defer", reject)
    monkeypatch.setattr(relay, "release_for_failover", no_release)
    monkeypatch.setattr(daemon, "_alert_owner", lambda *a: alerts.append(a))
    monkeypatch.setattr(handoff_sender, "snapshot", lambda *a: cap)
    result = asyncio.run(handoff_sender.attempt(daemon, relay,
        dict(room="build", message_id=cap.root), reason="quota", mission={"task_id": "task"},
        worktree=wt, **policy()))
    assert result == "stopped" and relay.holder == daemon.config.participant_name
    assert alerts and "relay too old for handoff" in alerts[-1][1]
