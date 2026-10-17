"""Vendor-only failover, bounded typed continuation and recovery."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from quorus.protocol import SocialVerb
from quorus.runtime import failover
from tests.test_reflexd_inbox_drain import _D7Relay, _wake_envelope, reflexd
from tests.test_reflexd_wake_contract import _git_repo


class Relay(_D7Relay):
    def __init__(self):
        super().__init__([])
        self.entries = [
            {"name": "b-codex", "status": "active", "online": True, "last_wake_at": 20},
            {"name": "c-gemini", "status": "active", "online": True, "last_wake_at": 10},
            {"name": "d-claude", "status": "active", "online": True},
            {"name": "e-cursor", "status": "busy", "online": True},
            {"name": "f-opencode", "status": "degraded", "online": True},
            {"name": "g-cline", "status": "active", "online": False},
        ]
        self.handoffs, self.releases, self.heartbeats = [], [], []
        self.mission = {"task_id": "task-1", "summary": "Continue the work"}

    async def fetch_presence(self):
        return self.entries

    async def fetch_members(self, **kw):
        return [e["name"] for e in self.entries]

    async def mission_task_for(self, **kw):
        return self.mission

    async def post_social_defer(self, **kw):
        self.handoffs.append(kw)

    async def release_for_failover(self, **kw):
        self.releases.append(kw)

    async def post_heartbeat(self, **kw):
        self.heartbeats.append(kw)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(reflexd, "workspace_for", lambda room: None)
    monkeypatch.setattr(reflexd, "mode_for", lambda room: "default")
    monkeypatch.setattr(reflexd, "lead_for", lambda room: None)

    def make(name="a-claude", error="quota exceeded"):
        d = reflexd.Reflexd(reflexd.ReflexdConfig(
            relay_url="http://test", participant_name=name, api_key="test",
            runtime_dir=tmp_path / name,
        ))
        async def run(*args, **kwargs):
            return reflexd.diagnose_harness_failure(reflexd.detect_harness(name), error)
        monkeypatch.setattr(d.adapter, "run", run)
        return d
    return make


def run(d, relay, envelope=None):
    asyncio.run(d._wake_and_reply(relay, envelope or _wake_envelope(), reason="test"))


@pytest.mark.parametrize("error,reason", [("quota exceeded", "quota"),
                                        ("rate limit reached", "rate_limit"),
                                        ("not logged in", "login")])
def test_vendor_stderr_hands_off_and_stays_degraded(setup, error, reason):
    d, relay = setup(error=error), Relay()
    run(d, relay)
    assert d._presence == ("degraded", "r")
    assert relay.heartbeats[-1]["reason"] == reason
    assert len(relay.handoffs) == 1
    handoff = relay.handoffs[0]
    assert handoff["target"] == "c-gemini"  # least recently woken eligible harness
    assert handoff["ref_message_id"] == "wake-msg-1"
    data = handoff["failover"]
    assert data["failed_agents"] == ["a-claude"] and data["handoffs"] == 1
    assert data["branch"] == "quorus/a-claude" and data["task_id"] == "task-1"
    assert relay.releases[0]["target"] == "c-gemini"
    assert relay.posted == []  # one typed handoff, no duplicate prose


@pytest.mark.parametrize("reply", ["[reflexd] harness errored", "[reflexd] harness timed out",
                                  "⚠ claude: quota exceeded"])
def test_generic_or_model_prose_is_not_a_vendor_trigger(setup, monkeypatch, reply):
    d, relay = setup(), Relay()
    async def adapter(*args, **kw):
        return reply
    monkeypatch.setattr(d.adapter, "run", adapter)
    run(d, relay)
    assert not relay.handoffs and not relay.releases
    assert d._presence == ("active", "")


@pytest.mark.parametrize("mode", ["default", "manual", "readonly"])
def test_no_candidate_or_permission_mode_posts_diagnosis(setup, monkeypatch, mode):
    d, relay = setup(), Relay()
    monkeypatch.setattr(reflexd, "mode_for", lambda room: mode)
    if mode == "default":
        relay.entries = []
    run(d, relay)
    assert not relay.handoffs and not relay.releases
    assert "quota" in relay.posted[0]["content"]


def as_envelope(handoff, actor, message_id):
    sv = SocialVerb(verb="defer", actor=actor, room_id="r",
                    ref_message_id=handoff["ref_message_id"],
                    payload={"to": handoff["target"], "failover": handoff["failover"]})
    return {"id": message_id, "message_id": message_id, "from_name": actor,
            "room": "r", "message_type": "social", "content": sv.model_dump_json()}


def test_failed_agents_never_reselected_and_two_hops_stop(setup):
    first, relay = setup(), Relay()
    run(first, relay)
    env = as_envelope(relay.handoffs[-1], "a-claude", "hop1")
    second = setup("c-gemini")
    relay.entries.append({"name": "a-claude", "status": "active", "online": True})
    relay.entries[2]["status"] = "busy"
    run(second, relay, env)
    assert relay.handoffs[-1]["target"] == "b-codex"
    assert relay.handoffs[-1]["failover"]["failed_agents"] == ["a-claude", "c-gemini"]
    env2 = as_envelope(relay.handoffs[-1], "c-gemini", "hop2")
    run(setup("b-codex"), relay, env2)
    assert len(relay.handoffs) == 2
    assert "Failover limit reached" in relay.posted[-1]["content"]


def test_restart_does_not_duplicate_handoff(setup):
    relay = Relay()
    run(setup(), relay)
    run(setup(), relay)
    assert len(relay.handoffs) == 1


def test_success_clears_degraded(setup, monkeypatch):
    d, relay = setup(), Relay()
    run(d, relay)
    async def succeed(*args, **kw):
        return "Completed the work"
    monkeypatch.setattr(d.adapter, "run", succeed)
    run(d, relay, {**_wake_envelope(), "message_id": "recovery"})
    assert d._presence == ("active", "") and not d._degraded_reason


def test_typed_handoff_wakes_only_target_once(setup, monkeypatch):
    relay = Relay()
    run(setup(), relay)
    env = as_envelope(relay.handoffs[0], "a-claude", "hop1")
    target = setup("c-gemini")
    wakes = []
    async def wake(*args, **kw):
        wakes.append(args[1])
    monkeypatch.setattr(target, "_wake_and_reply", wake)
    # Avoid OS notifications in this unit test.
    monkeypatch.setattr("quorus.notifications.notify", lambda *a, **kw: None)
    async def deliver():
        await target._dispatch_event(relay, "message", env)
        await target._dispatch_event(relay, "message", env)
    asyncio.run(deliver())
    assert len(wakes) == 1
    result = reflexd.classify_message(content=env["content"], sender="a-claude",
                                     self_name="b-codex", message_type="social")
    assert result.action == "IGNORE"
    prompt = target._build_prompt(env, [], has_workspace=False)
    assert '/defer @c-gemini:' in prompt and 'Branch quorus/a-claude' in prompt


def test_branch_commit_is_real(tmp_path):
    repo = _git_repo(tmp_path / "repo")
    assert len(failover._last_commit(SimpleNamespace(path=repo))) >= 7


def test_real_quota_stderr_reaches_failover(setup, monkeypatch):
    from tests.test_reflexd_adapters import _FakeProc

    d, relay = setup(), Relay()
    async def spawn(*args, **kw):
        return _FakeProc(stderr=b"quota exceeded", returncode=1)
    monkeypatch.setattr(reflexd.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(reflexd.shutil, "which", lambda name: "/bin/harness")
    d.adapter = reflexd.HeadlessAdapter()
    run(d, relay)
    assert relay.handoffs[0]["failover"]["reason"] == "quota"


def test_degraded_survives_restart_and_recovery(setup, monkeypatch):
    relay = Relay()
    run(setup(), relay)
    restarted = setup()
    assert restarted._presence[0] == "degraded"
    async def success(*args, **kwargs):
        return "NO_REPLY"
    monkeypatch.setattr(restarted.adapter, "run", success)
    run(restarted, relay)
    assert setup()._presence[0] == "active"


def test_capability_match_precedes_idle_recency(setup):
    d, relay = setup(), Relay()
    relay.mission["summary"] = "fix backend relay"
    run(d, relay)
    assert relay.handoffs[0]["target"] == "b-codex"


def test_failed_transfer_posts_diagnosis_without_waking_target(setup):
    d, relay = setup(), Relay()
    async def cannot_release(**kwargs):
        raise RuntimeError("private details")
    relay.release_for_failover = cannot_release
    run(d, relay)
    assert not relay.handoffs
    assert "Failover could not complete (RuntimeError)" in relay.posted[0]["content"]
    assert "private details" not in relay.posted[0]["content"]


def test_completed_task_is_not_resurrected_by_late_vendor_failure(setup):
    d, relay = setup(), Relay()
    calls = []
    async def mission(**kwargs):
        calls.append(True)
        return relay.mission if len(calls) == 1 else None
    relay.mission_task_for = mission
    run(d, relay)
    assert not relay.releases and not relay.handoffs


def test_handoff_records_published_branch_and_commit(setup, tmp_path):
    d, relay = setup(), Relay()
    repo = _git_repo(tmp_path / "bound")
    wt = reflexd.reflexd_wake.agent_worktree(repo, "a-claude")
    result = asyncio.run(failover.attempt(
        d, relay, _wake_envelope(), reason="quota", mission=relay.mission,
        worktree=wt, mode="default", detect=reflexd.detect_harness,
        capabilities=reflexd.capabilities_for, score=reflexd.compute_bid_v2,
    ))
    assert result == "sent"
    payload = relay.handoffs[0]["failover"]
    assert payload["branch"] == wt.branch
    assert payload["last_commit"] == failover._last_commit(wt)


def test_ordinary_defer_does_not_wake():
    sv = SocialVerb(verb="defer", actor="a-claude", room_id="r", payload={"to": "b-codex"})
    assert failover.continuation({"message_type": "social", "from_name": "a-claude",
                                  "content": json.dumps(sv.to_envelope())}) is None
