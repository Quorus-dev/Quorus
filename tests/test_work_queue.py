"""Tests for the Stream B work-queue — service unit tests + route integration.

12 cases per the sprint plan:
1. add → list happy path
2. claim transitions task to in_progress
3. claim race — second actor on same task gets ClaimRaceError (409)
4. release without handoff cancels
5. release with handoff transfers ownership
6. complete marks done
7. complete with success=False marks failed
8. social-verb mirror — claim verb propagates into the queue
9. social-verb mirror — release verb closes the queue entry
10. social-verb mirror — queue verb appends a new task
11. list status filter
12. concurrent claim race via the HTTP route returns 409
"""
from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from quorus.auth.tokens import create_jwt
from quorus.relay import _reset_state, app
from quorus.services.work_queue_svc import (
    ClaimRaceError,
    TaskNotFoundError,
    WorkQueueSvc,
)

HEADERS = {"Authorization": "Bearer test-secret"}

_TEST_TENANT_ID = "t-wq"
_TEST_TENANT_SLUG = "wq-test"


def _user_headers(name: str) -> dict[str, str]:
    """Return a Bearer-JWT header for ``name`` in the test tenant."""
    token = create_jwt(
        sub=name,
        tenant_id=_TEST_TENANT_ID,
        tenant_slug=_TEST_TENANT_SLUG,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
async def _clean_state():
    _reset_state()
    yield
    _reset_state()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def room_id(client: AsyncClient) -> str:
    """Create a room via real JWT (alice). H6 hardening rejects legacy
    bearer on /v1/work_queue and /v1/social, so the room must live in the
    same tenant the JWT identifies (``_TEST_TENANT_ID``).
    """
    resp = await client.post(
        "/rooms",
        json={"name": "wq-test-room", "created_by": "alice"},
        headers=_user_headers("alice"),
    )
    assert resp.status_code == 200, resp.text
    rid = resp.json()["id"]
    for who in ("bob", "carol"):
        join = await client.post(
            f"/rooms/{rid}/join",
            json={"participant": who, "role": "member"},
            headers=_user_headers("alice"),
        )
        assert join.status_code == 200, join.text
    return rid


# ---------------------------------------------------------------------------
# Service unit tests
# ---------------------------------------------------------------------------


class TestWorkQueueSvc:

    async def test_add_then_list(self):
        svc = WorkQueueSvc()
        t = await svc.add(
            "tid", "rid",
            summary="ship the relay", requested_by="alice",
            eta_seconds=600,
        )
        assert t["status"] == "pending"
        assert t["summary"] == "ship the relay"
        items = await svc.list("tid", "rid")
        assert len(items) == 1
        assert items[0]["task_id"] == t["task_id"]

    async def test_claim_transitions_to_in_progress(self):
        svc = WorkQueueSvc()
        t = await svc.add(
            "tid", "rid",
            summary="task", requested_by="alice", eta_seconds=60,
        )
        claimed = await svc.claim(
            "tid", "rid", task_id=t["task_id"], actor="alice",
        )
        assert claimed["status"] == "in_progress"
        assert claimed["claimed_by"] == "alice"
        assert claimed["started_at"] is not None

    async def test_claim_race_raises_409(self):
        svc = WorkQueueSvc()
        await svc.add(
            "tid", "rid",
            summary="contested", requested_by="alice",
            eta_seconds=60, task_id="t1",
        )
        await svc.claim("tid", "rid", task_id="t1", actor="alice")
        with pytest.raises(ClaimRaceError):
            await svc.claim("tid", "rid", task_id="t1", actor="bob")

    async def test_release_without_handoff_cancels(self):
        svc = WorkQueueSvc()
        await svc.add(
            "tid", "rid",
            summary="cancellable", requested_by="alice",
            task_id="t1",
        )
        await svc.claim("tid", "rid", task_id="t1", actor="alice")
        out = await svc.release(
            "tid", "rid", task_id="t1", actor="alice",
        )
        assert out["status"] == "cancelled"
        assert out["completed_at"] is not None

    async def test_release_with_handoff_transfers_ownership(self):
        svc = WorkQueueSvc()
        await svc.add(
            "tid", "rid",
            summary="handoff", requested_by="alice",
            task_id="t1",
        )
        await svc.claim("tid", "rid", task_id="t1", actor="alice")
        out = await svc.release(
            "tid", "rid", task_id="t1", actor="alice",
            handoff_to="bob",
        )
        assert out["status"] == "in_progress"
        assert out["claimed_by"] == "bob"
        history = out["metadata"].get("handoff_history") or []
        assert history and history[-1]["from"] == "alice"

    async def test_complete_marks_done(self):
        svc = WorkQueueSvc()
        await svc.add(
            "tid", "rid",
            summary="finishable", requested_by="alice",
            task_id="t1",
        )
        await svc.claim("tid", "rid", task_id="t1", actor="alice")
        out = await svc.complete(
            "tid", "rid", task_id="t1", actor="alice",
            outcome="ok",
        )
        assert out["status"] == "done"
        assert out["outcome"] == "ok"

    async def test_complete_failure_marks_failed(self):
        svc = WorkQueueSvc()
        await svc.add(
            "tid", "rid",
            summary="failable", requested_by="alice",
            task_id="t1",
        )
        await svc.claim("tid", "rid", task_id="t1", actor="alice")
        out = await svc.complete(
            "tid", "rid", task_id="t1", actor="alice",
            outcome="err: timeout", success=False,
        )
        assert out["status"] == "failed"

    async def test_apply_social_event_claim(self):
        svc = WorkQueueSvc()
        out = await svc.apply_social_event(
            "tid", "rid",
            verb="claim", actor="alice",
            payload={
                "task_id": "t-shipping", "eta_seconds": 600, "scope": "x",
            },
        )
        assert out is not None
        assert out["status"] == "in_progress"
        assert out["claimed_by"] == "alice"
        assert out["task_id"] == "t-shipping"

    async def test_apply_social_event_release_after_claim(self):
        svc = WorkQueueSvc()
        await svc.apply_social_event(
            "tid", "rid",
            verb="claim", actor="alice",
            payload={"task_id": "t1", "eta_seconds": 60, "scope": "x"},
        )
        out = await svc.apply_social_event(
            "tid", "rid",
            verb="release", actor="alice",
            payload={"task_id": "t1", "reason": "done"},
        )
        assert out is not None
        assert out["status"] == "cancelled"

    async def test_apply_social_event_queue_appends(self):
        svc = WorkQueueSvc()
        out = await svc.apply_social_event(
            "tid", "rid",
            verb="queue", actor="alice",
            payload={
                "after": "t-prev", "task_summary": "review PR",
                "eta_seconds": 120,
            },
        )
        assert out is not None
        assert out["status"] == "pending"
        items = await svc.list("tid", "rid")
        assert len(items) == 1
        assert items[0]["summary"] == "review PR"

    async def test_list_status_filter(self):
        svc = WorkQueueSvc()
        await svc.add(
            "tid", "rid", summary="t1", requested_by="alice", task_id="t1",
        )
        await svc.add(
            "tid", "rid", summary="t2", requested_by="alice", task_id="t2",
        )
        await svc.claim("tid", "rid", task_id="t1", actor="alice")
        only_progress = await svc.list(
            "tid", "rid", status="in_progress",
        )
        assert {t["task_id"] for t in only_progress} == {"t1"}
        only_pending = await svc.list("tid", "rid", status="pending")
        assert {t["task_id"] for t in only_pending} == {"t2"}

    async def test_update_unknown_task_raises(self):
        svc = WorkQueueSvc()
        with pytest.raises(TaskNotFoundError):
            await svc.update(
                "tid", "rid",
                task_id="ghost", actor="alice", status="done",
            )


# ---------------------------------------------------------------------------
# Route integration tests
# ---------------------------------------------------------------------------


class TestWorkQueueRoutes:

    async def test_post_add_then_get_lists(
        self, client: AsyncClient, room_id: str,
    ):
        resp = await client.post(
            f"/v1/work_queue/{room_id}",
            json={
                "op": "add",
                "actor": "alice",
                "summary": "build /v1/healthz",
                "eta_seconds": 600,
            },
            headers=_user_headers("alice"),
        )
        assert resp.status_code == 200, resp.text
        get_resp = await client.get(
            f"/v1/work_queue/{room_id}", headers=_user_headers("alice"),
        )
        assert get_resp.status_code == 200
        body = get_resp.json()
        assert body["count"] == 1
        assert body["tasks"][0]["summary"] == "build /v1/healthz"

    async def test_concurrent_claim_route_returns_409(
        self, client: AsyncClient, room_id: str,
    ):
        # Add a task first
        add = await client.post(
            f"/v1/work_queue/{room_id}",
            json={
                "op": "add", "actor": "alice",
                "summary": "race", "eta_seconds": 60,
                "task_id": "race-1",
            },
            headers=_user_headers("alice"),
        )
        assert add.status_code == 200, add.text

        # Race two claims — only one should win.
        async def _claim(actor: str):
            return await client.post(
                f"/v1/work_queue/{room_id}",
                json={"op": "claim", "actor": actor, "task_id": "race-1"},
                headers=_user_headers(actor),
            )

        r1, r2 = await asyncio.gather(_claim("alice"), _claim("bob"))
        codes = sorted([r1.status_code, r2.status_code])
        assert codes == [200, 409], (
            f"expected one win one race, got r1={r1.status_code} r2={r2.status_code}"
        )

    async def test_verb_driven_mutation_via_social_route(
        self, client: AsyncClient, room_id: str,
    ):
        # Posting a social claim verb should also populate the work queue.
        resp = await client.post(
            "/v1/social/claim",
            json={
                "actor": "alice", "room_id": room_id,
                "payload": {
                    "task_id": "verb-task-1",
                    "eta_seconds": 600,
                    "scope": "ship the demo",
                },
            },
            headers=_user_headers("alice"),
        )
        assert resp.status_code == 200, resp.text

        # The work queue should now reflect the claim
        get_resp = await client.get(
            f"/v1/work_queue/{room_id}", headers=_user_headers("alice"),
        )
        assert get_resp.status_code == 200
        body = get_resp.json()
        ids = [t["task_id"] for t in body["tasks"]]
        assert "verb-task-1" in ids
        verb_task = next(
            t for t in body["tasks"] if t["task_id"] == "verb-task-1"
        )
        assert verb_task["status"] == "in_progress"
        assert verb_task["claimed_by"] == "alice"

    async def test_list_filter_by_status(
        self, client: AsyncClient, room_id: str,
    ):
        # Two tasks: one pending, one claimed.
        await client.post(
            f"/v1/work_queue/{room_id}",
            json={
                "op": "add", "actor": "alice",
                "summary": "pending one",
                "task_id": "p1",
            },
            headers=_user_headers("alice"),
        )
        await client.post(
            f"/v1/work_queue/{room_id}",
            json={
                "op": "add", "actor": "alice",
                "summary": "claimable",
                "task_id": "p2",
            },
            headers=_user_headers("alice"),
        )
        await client.post(
            f"/v1/work_queue/{room_id}",
            json={"op": "claim", "actor": "alice", "task_id": "p2"},
            headers=_user_headers("alice"),
        )
        # Filter for in_progress only
        resp = await client.get(
            f"/v1/work_queue/{room_id}?status=in_progress",
            headers=_user_headers("alice"),
        )
        ids = [t["task_id"] for t in resp.json()["tasks"]]
        assert ids == ["p2"]


# ---------------------------------------------------------------------------
# Wave-2 regression — H6 legacy bearer rejection on /v1/work_queue/*
# ---------------------------------------------------------------------------


async def test_legacy_bearer_rejected_on_work_queue(
    client: AsyncClient, room_id: str,
):
    """H6 — POST /v1/work_queue/{room_id} must reject ``Bearer test-secret``."""
    resp = await client.post(
        f"/v1/work_queue/{room_id}",
        json={
            "op": "add", "actor": "alice",
            "summary": "rejected", "eta_seconds": 60,
        },
        headers=HEADERS,  # legacy bearer
    )
    assert resp.status_code == 403, resp.text
    assert "legacy auth not permitted" in resp.text


# ---------------------------------------------------------------------------
# M13 — per-actor active-task quota
# ---------------------------------------------------------------------------


async def test_work_queue_per_actor_quota_50():
    """M13 — the 51st distinct active claim by the same actor raises QuotaExceededError.

    Drives the service directly (no HTTP) so we don't have to fight the
    rate limiter at 60/min while also asserting the 50-claim cap.
    """
    from quorus.services.work_queue_svc import (
        MAX_ACTIVE_PER_ACTOR,
        QuotaExceededError,
    )

    svc = WorkQueueSvc()
    tid, rid = "t-quota", "r-quota"
    actor = "spam-agent"

    # Burn the quota — each claim is a distinct task_id.
    for i in range(MAX_ACTIVE_PER_ACTOR):
        await svc.claim(tid, rid, task_id=f"task-{i}", actor=actor)

    # 51st must trip.
    with pytest.raises(QuotaExceededError) as excinfo:
        await svc.claim(
            tid, rid, task_id=f"task-{MAX_ACTIVE_PER_ACTOR}", actor=actor,
        )
    assert str(MAX_ACTIVE_PER_ACTOR) in str(excinfo.value)


async def test_work_queue_quota_releases_on_complete():
    """M13 — completing a task frees a slot under the quota.

    Without this, an agent that legitimately churned 50 tasks could
    never claim again — quota must reflect *active* state, not lifetime.
    """
    from quorus.services.work_queue_svc import (
        MAX_ACTIVE_PER_ACTOR,
        QuotaExceededError,
    )

    svc = WorkQueueSvc()
    tid, rid = "t-q2", "r-q2"
    actor = "pacer"

    for i in range(MAX_ACTIVE_PER_ACTOR):
        await svc.claim(tid, rid, task_id=f"task-{i}", actor=actor)
    # Free one.
    await svc.complete(tid, rid, task_id="task-0", actor=actor)

    # The next claim must succeed.
    task = await svc.claim(
        tid, rid, task_id=f"task-{MAX_ACTIVE_PER_ACTOR}", actor=actor,
    )
    assert task["claimed_by"] == actor

    # Hitting the cap again must still fail.
    with pytest.raises(QuotaExceededError):
        await svc.claim(
            tid, rid, task_id=f"task-{MAX_ACTIVE_PER_ACTOR + 1}", actor=actor,
        )


async def test_work_queue_quota_idempotent_reclaim():
    """M13 — re-claiming an existing in-flight task by the same actor is
    idempotent and does NOT count against the quota again."""
    from quorus.services.work_queue_svc import MAX_ACTIVE_PER_ACTOR

    svc = WorkQueueSvc()
    tid, rid = "t-q3", "r-q3"
    actor = "idem"

    for i in range(MAX_ACTIVE_PER_ACTOR):
        await svc.claim(tid, rid, task_id=f"task-{i}", actor=actor)

    # Re-claim one that's already owned — should not raise.
    task = await svc.claim(tid, rid, task_id="task-0", actor=actor)
    assert task["claimed_by"] == actor


async def test_work_queue_quota_route_returns_429(
    client: AsyncClient, room_id: str,
):
    """M13 — the HTTP route maps QuotaExceededError to 429.

    Asserts the integration with routes/work_queue.py: 429 + a detail
    string containing the actor name and the cap so the client can
    surface a useful error.
    """
    from quorus.services.work_queue_svc import MAX_ACTIVE_PER_ACTOR

    # Seed the in-memory work-queue service with 50 active claims for
    # alice in this room. Bypassing HTTP for the seed step keeps us
    # inside the per-minute rate limit.
    svc = WorkQueueSvc()
    app.state.work_queue_service = svc
    for i in range(MAX_ACTIVE_PER_ACTOR):
        await svc.claim(
            _TEST_TENANT_ID, room_id,
            task_id=f"seed-{i}", actor="alice",
        )

    resp = await client.post(
        f"/v1/work_queue/{room_id}",
        json={"op": "claim", "actor": "alice", "task_id": "overflow"},
        headers=_user_headers("alice"),
    )
    assert resp.status_code == 429, resp.text
    assert "active tasks" in resp.text or "max" in resp.text


# ---------------------------------------------------------------------------
# Wave-5 Fix 3 — audit trail on every successful work-queue mutation
# ---------------------------------------------------------------------------


class _StubAudit:
    def __init__(self):
        self.captured: list[dict] = []

    async def record(self, **kwargs):
        self.captured.append(kwargs)


async def test_audit_recorded_on_claim(
    client: AsyncClient, room_id: str,
):
    """Wave-5 Fix 3 — claim ops must produce a work_queue:claim audit row."""
    audit = _StubAudit()
    app.state.audit_service = audit
    try:
        # add then claim
        add = await client.post(
            f"/v1/work_queue/{room_id}",
            json={
                "op": "add", "actor": "alice",
                "summary": "audit-claim-task",
                "task_id": "audit-claim",
            },
            headers=_user_headers("alice"),
        )
        assert add.status_code == 200
        cl = await client.post(
            f"/v1/work_queue/{room_id}",
            json={
                "op": "claim", "actor": "alice",
                "task_id": "audit-claim", "eta_seconds": 60,
            },
            headers=_user_headers("alice"),
        )
        assert cl.status_code == 200, cl.text
        # Two audit rows: one for add, one for claim.
        events = [e["event_type"] for e in audit.captured]
        assert "work_queue:add" in events
        assert "work_queue:claim" in events
        claim_row = next(e for e in audit.captured if e["event_type"] == "work_queue:claim")
        assert claim_row["actor"] == "alice"
        assert claim_row["details"]["task_id"] == "audit-claim"
        assert claim_row["details"]["op"] == "claim"
    finally:
        app.state.audit_service = None


async def test_audit_recorded_on_release(
    client: AsyncClient, room_id: str,
):
    """Wave-5 Fix 3 — release ops must produce a work_queue:release audit row."""
    audit = _StubAudit()
    app.state.audit_service = audit
    try:
        await client.post(
            f"/v1/work_queue/{room_id}",
            json={
                "op": "add", "actor": "alice",
                "summary": "audit-release-task",
                "task_id": "audit-release",
            },
            headers=_user_headers("alice"),
        )
        await client.post(
            f"/v1/work_queue/{room_id}",
            json={
                "op": "claim", "actor": "alice",
                "task_id": "audit-release",
            },
            headers=_user_headers("alice"),
        )
        rel = await client.post(
            f"/v1/work_queue/{room_id}",
            json={
                "op": "release", "actor": "alice",
                "task_id": "audit-release", "handoff_to": "bob",
            },
            headers=_user_headers("alice"),
        )
        assert rel.status_code == 200, rel.text
        rel_row = next(
            e for e in audit.captured if e["event_type"] == "work_queue:release"
        )
        assert rel_row["details"]["handoff_to"] == "bob"
    finally:
        app.state.audit_service = None


async def test_audit_recorded_on_complete(
    client: AsyncClient, room_id: str,
):
    """Wave-5 Fix 3 — complete ops must produce a work_queue:complete audit row."""
    audit = _StubAudit()
    app.state.audit_service = audit
    try:
        await client.post(
            f"/v1/work_queue/{room_id}",
            json={
                "op": "add", "actor": "alice",
                "summary": "audit-complete-task",
                "task_id": "audit-complete",
            },
            headers=_user_headers("alice"),
        )
        await client.post(
            f"/v1/work_queue/{room_id}",
            json={
                "op": "claim", "actor": "alice",
                "task_id": "audit-complete",
            },
            headers=_user_headers("alice"),
        )
        comp = await client.post(
            f"/v1/work_queue/{room_id}",
            json={
                "op": "complete", "actor": "alice",
                "task_id": "audit-complete", "outcome": "shipped",
                "success": True,
            },
            headers=_user_headers("alice"),
        )
        assert comp.status_code == 200, comp.text
        comp_row = next(
            e for e in audit.captured if e["event_type"] == "work_queue:complete"
        )
        assert comp_row["details"]["success"] is True
        # outcome string contains free text and must NOT leak into details.
        for k, v in comp_row["details"].items():
            assert "shipped" not in str(v), (
                f"complete outcome leaked into audit.details.{k}"
            )
    finally:
        app.state.audit_service = None
