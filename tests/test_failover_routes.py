"""Real relay contracts for durable typed handoffs and degraded presence."""

import json

from quorus.relay import app
from tests import test_social_routes as social

_user_headers = social._user_headers
clean_state = social.clean_state
client = social.client
real_ref_id = social.real_ref_id
room_id = social.room_id


def data(ref):
    return {"original_message_id": ref, "task_id": "task-1", "task": "repair tests",
            "reason": "quota", "branch": "quorus/alice", "last_commit": "abc1234",
            "failed_agents": ["alice"], "handoffs": 1}


async def test_failover_is_one_durable_threaded_message(client, room_id, real_ref_id, monkeypatch):
    pushes = []
    monkeypatch.setattr(app.state.sse_service, "push", lambda *args: pushes.append(args[-1]))
    resp = await client.post("/v1/social/defer", headers=_user_headers("alice"), json={
        "actor": "alice", "room_id": room_id, "ref_message_id": real_ref_id,
        "payload": {"to": "bob", "failover": data(real_ref_id)},
    })
    assert resp.status_code == 200, resp.text
    social = [p for p in pushes if p.get("message_type") == "social" and p.get("to") == "bob"]
    assert len(social) == 1
    message = social[0]
    assert message["reply_to"] == real_ref_id
    assert message["message_id"]
    assert json.loads(message["content"])["payload"]["failover"]["branch"] == "quorus/alice"
    inbox = await client.get("/messages/bob", headers=_user_headers("bob"), params={"wait": 0})
    assert inbox.status_code == 200, inbox.text
    pending = [m for m in inbox.json()["messages"] if m.get("message_type") == "social"]
    assert len(pending) == 1
    assert pending[0]["message_id"] == message["message_id"]


async def test_degraded_reason_and_last_wake_survive_heartbeats(client):
    headers = _user_headers("alice")
    async def beat(status, **extras):
        resp = await client.post("/heartbeat", headers=headers, json={
            "instance_name": "alice", "status": status, **extras,
        })
        assert resp.status_code == 200, resp.text
        entries = (await client.get("/presence", headers=headers)).json()
        return next(e for e in entries if e["name"] == "alice")
    busy = await beat("busy")
    assert busy["last_wake_at"]
    degraded = await beat("degraded", reason="quota")
    assert degraded["reason"] == "quota" and degraded["status"] == "degraded"
    assert degraded["last_wake_at"] == busy["last_wake_at"]
    active = await beat("active")
    assert active["reason"] == "" and active["status"] == "active"


async def test_release_moves_real_queue_claim_before_defer(client, room_id):
    from tests.test_reflexd_inbox_drain import reflexd

    headers = _user_headers("alice")
    claimed = await client.post("/v1/social/claim", headers=headers, json={
        "actor": "alice", "room_id": room_id,
        "payload": {"task_id": "task-1", "scope": "repair tests", "eta_seconds": 0},
    })
    assert claimed.status_code == 200, claimed.text
    relay = reflexd.RelayClient(relay_url="http://test", api_key="test")
    relay._client = client
    async def auth():
        return headers
    relay._headers = auth
    await relay.release_for_failover(room=room_id, actor="alice", target="bob", task_id="task-1")
    tasks = (await client.get(f"/v1/work_queue/{room_id}", headers=headers,
                              params={"actor": "bob"})).json()["tasks"]
    assert tasks[0]["claimed_by"] == "bob"
    assert len(tasks[0]["metadata"]["handoff_history"]) == 1
