"""`quorus agent pace`: keep an agent inside its owner's plan (2026-10-17).

Night one: Codex burned its 5-hour plan window in ~24 turns. Pacing delays
(never drops) teammate-triggered turns past the cap; humans always go first.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from quorus_cli import agent_cmd

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("reflexd", _ROOT / "scripts" / "reflexd.py")
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd"] = reflexd
_spec.loader.exec_module(reflexd)


def test_pace_wait_math() -> None:
    assert reflexd.pace_wait_s([], 2, 1000) == 0
    assert reflexd.pace_wait_s([100, 200], 3, 1000) == 0
    # Two turns in the last hour at cap 2: wait until the older one ages out.
    assert reflexd.pace_wait_s([100, 200], 2, 1000) == 100 + 3600 - 1000
    assert reflexd.pace_wait_s([-5000, 200], 2, 1000) == 0  # old turn doesn't count


def test_pace_config_round_trip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(agent_cmd.Path, "home", staticmethod(lambda: tmp_path))

    class _C:
        def print(self, *_a: Any, **_k: Any) -> None:
            pass

    class _A:
        name, per_hour = "a-codex", "3"

    agent_cmd._pace(_A(), _C())
    path = tmp_path / ".quorus" / "pacing.json"
    assert json.loads(path.read_text()) == {"a-codex": {"per_hour": 3}}
    assert reflexd.pace_for("a-codex", path=path) == 3
    assert reflexd.pace_for("a-claude", path=path) is None
    _A.per_hour = "off"
    agent_cmd._pace(_A(), _C())
    assert reflexd.pace_for("a-codex", path=path) is None


class _Relay:
    def __init__(self) -> None:
        self.posted: list[dict] = []

    async def post_reply(self, **kw):
        self.posted.append(kw)
        return {"id": "p"}


def test_over_cap_parks_and_says_so_once_but_humans_go_first(monkeypatch) -> None:
    d = reflexd.Reflexd(reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                              participant_name="a-codex"))
    monkeypatch.setattr(reflexd, "pace_for", lambda name: 1)
    released: list[float] = []

    async def fake_release(relay: Any, wait: float) -> None:
        released.append(wait)

    monkeypatch.setattr(d, "_release_paced", fake_release)
    relay = _Relay()
    agent_msg = {"room": "build", "from_name": "a-claude", "message_id": "m"}

    async def go() -> None:
        assert await d._paced(relay, agent_msg)  # first turn: free
        d._record_turn()  # ...and it ran
        assert not await d._paced(relay, agent_msg)  # second: parked for the slot
        await asyncio.sleep(0)
        assert len(released) == 1 and released[0] > 3500
        assert d._pace_parked["build"][0] is agent_msg
        assert len(relay.posted) == 1
        assert "pacing itself (1 turns/hour" in relay.posted[0]["content"]
        human = {"room": "build", "from_name": "arav", "message_id": "h"}
        assert await d._paced(relay, human)  # boss never waits
    asyncio.run(go())
    assert d._waiting[0] == "pacing"  # the board shows "pacing", not "working"


def test_skipped_messages_use_no_pace_turns(monkeypatch) -> None:
    """Overseer, fix 16: three already-answered envelopes filled the 3/h window
    and the next real message waited an hour."""
    d = reflexd.Reflexd(reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                              participant_name="a-codex"))
    monkeypatch.setattr(reflexd, "pace_for", lambda name: 3)
    d._answered["build"] = {"m1": None, "m2": None, "m3": None}

    async def no_limit(*_a: Any) -> bool:
        return False
    monkeypatch.setattr(d, "_limited", no_limit)
    for mid in ("m1", "m2", "m3"):
        env = {"room": "build", "from_name": "a-claude", "message_id": mid, "id": mid}
        asyncio.run(d._wake_and_reply_inner(_Relay(), env))
    assert d._turns == [] and d._pace_parked == {}


def test_an_owner_mention_during_a_pacing_wait_wakes_at_once(monkeypatch) -> None:
    """The pacing wait held the wake lock, so the owner's direct @mentions sat
    queued for an hour. A parked message no longer holds anything."""
    d = reflexd.Reflexd(reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                              participant_name="a-codex"))
    monkeypatch.setattr(reflexd, "pace_for", lambda name: 1)
    woke: list[str] = []
    gate = asyncio.Event()

    async def fake_sleep(_s: float) -> None:
        await gate.wait()  # the slot opens only when the test says so
    monkeypatch.setattr(d, "_sleep_or_stop", fake_sleep)

    async def fake_wake(relay: Any, env: dict, *, triage: Any = None, **_k: Any) -> None:
        if await d._paced(relay, env, triage):
            d._record_turn()
            woke.append(env["from_name"])

    monkeypatch.setattr(d, "_wake_and_reply", fake_wake)

    async def go() -> None:
        relay = _Relay()
        d._record_turn()  # the hour's one turn is used
        async with d._wake_lock:
            await d._wake_and_reply(relay, {"room": "build", "from_name": "a-claude",
                                            "message_id": "agent"})
        assert woke == [] and not d._wake_lock.locked()
        async def boss() -> None:
            async with d._wake_lock:
                await d._wake_and_reply(relay, {"room": "build", "from_name": "arav",
                                                "message_id": "boss"})
        await asyncio.wait_for(boss(), timeout=2)  # the owner doesn't wait for the slot
        assert woke == ["arav"]
        d._turns = []  # the slot frees up
        gate.set()
        await d._pace_task
        assert woke == ["arav", "a-claude"]  # the parked message still runs
    asyncio.run(go())



def test_a_room_re_parked_during_release_still_gets_its_slot(monkeypatch) -> None:
    """Codex on 10d8a8e0: two parked rooms, cap 1/hour. The first wake took the
    slot, the second re-parked while the release worker was still running, and
    the worker then exited, stranding the second room."""
    d = reflexd.Reflexd(reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                              participant_name="a-codex"))
    monkeypatch.setattr(reflexd, "pace_for", lambda name: 1)
    woke: list[str] = []
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)
        d._turns = []  # time passes: the hour's slot opens again

    async def fake_wake(relay: Any, env: dict, *, triage: Any = None, **_k: Any) -> None:
        if await d._paced(relay, env, triage):
            d._record_turn()
            woke.append(env["room"])

    monkeypatch.setattr(d, "_sleep_or_stop", fake_sleep)
    monkeypatch.setattr(d, "_wake_and_reply", fake_wake)

    async def go() -> None:
        relay = _Relay()
        d._record_turn()  # the slot is used
        for room in ("alpha", "beta"):
            await d._wake_and_reply(relay, {"room": room, "from_name": "a-claude",
                                            "message_id": room})
        assert woke == [] and set(d._pace_parked) == {"alpha", "beta"}
        await asyncio.wait_for(d._pace_task, timeout=5)
    asyncio.run(go())
    assert woke == ["alpha", "beta"] and d._pace_parked == {}
    assert len(sleeps) == 2  # one wait per slot, then the worker ends


# ── FRICTION #95: a NO_REPLY turn that changed nothing gives its slot back ──
# (docs/plans/pacing-fixes.md, plan GO 6b0ec23d)


class _Room:
    """A relay whose history the daemon reads for the agent's own posts."""

    def __init__(self, *, fail: bool = False) -> None:
        self.history: list[dict] = []
        self.posted: list[dict] = []
        self.fail = fail

    async def fetch_recent(self, *, room: str, limit: int = 10):
        if self.fail:
            raise RuntimeError("relay unreachable")
        return list(self.history)[-limit:]

    async def fetch_history(self, *, room: str, limit: int):
        return list(self.history)[-limit:]

    async def post_reply(self, **kw):
        self.posted.append(kw)
        return {"id": f"p{len(self.posted)}"}

    async def post_record(self, **_kw):
        return {"ok": True}


def _paced_daemon(monkeypatch, cap: int = 2, name: str = "a-codex"):
    d = reflexd.Reflexd(reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                              participant_name=name))
    monkeypatch.setattr(reflexd, "pace_for", lambda _n: cap)
    return d


def _turn(monkeypatch, d, relay, i: int, *, reply: str = "NO_REPLY", during=None,
          sender: str = "a-claude") -> None:
    async def fake_run(harness, **_kw: Any) -> str:
        if during is not None:
            during(relay)
        return reply

    monkeypatch.setattr(d.adapter, "run", fake_run)
    env = {"id": f"e{i}", "message_id": f"m{i}", "room": "build", "from_name": sender,
           "content": f"@{d.config.participant_name} thoughts?", "message_type": "chat"}
    relay.history.append({**env, "timestamp": _now()})  # the message that wakes it
    asyncio.run(d._wake_and_reply(relay, env, reason="t"))


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def test_no_reply_turns_give_their_slot_back_up_to_the_cap(monkeypatch) -> None:
    d, relay = _paced_daemon(monkeypatch, cap=2), _Room()
    ran: list[int] = []
    for i in range(4):
        _turn(monkeypatch, d, relay, i, during=lambda _r, i=i: ran.append(i))
    # Two refunds (the cap), then the third counts; the fourth still fits.
    assert ran == [0, 1, 2, 3]
    assert len(d._refunds) == 2 and len(d._turns) == 2


def test_a_turn_that_replies_counts(monkeypatch) -> None:
    d, relay = _paced_daemon(monkeypatch), _Room()
    _turn(monkeypatch, d, relay, 0, reply="I'd keep it as is.")
    assert len(d._turns) == 1 and d._refunds == []


def test_a_no_reply_turn_that_posted_through_its_tools_counts(monkeypatch) -> None:
    from datetime import datetime, timezone

    def posts(relay: _Room) -> None:
        relay.history.append({"id": "own", "from_name": "a-codex", "content": "✅ done",
                              "timestamp": datetime.now(timezone.utc).isoformat()})

    d, relay = _paced_daemon(monkeypatch), _Room()
    _turn(monkeypatch, d, relay, 0, during=posts)
    assert len(d._turns) == 1 and d._refunds == []


def test_an_undated_own_post_counts(monkeypatch) -> None:
    d, relay = _paced_daemon(monkeypatch), _Room()
    _turn(monkeypatch, d, relay, 0, during=lambda r: r.history.append(
        {"id": "own", "from_name": "a-codex", "content": "hi", "timestamp": "soon"}))
    assert len(d._turns) == 1


def test_a_failed_own_post_check_counts(monkeypatch) -> None:
    d, relay = _paced_daemon(monkeypatch), _Room()
    # The relay goes away during the turn, so the end-of-wake check can't read it.
    _turn(monkeypatch, d, relay, 0, during=lambda r: setattr(r, "fail", True))
    assert len(d._turns) == 1 and d._refunds == []


def test_a_no_reply_turn_that_committed_counts(tmp_path, monkeypatch) -> None:
    """The commit is published and the publish line becomes the reply."""
    import subprocess

    def git(cwd: Path, *args: str) -> str:
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", *args],
                              cwd=cwd, check=True, capture_output=True, text=True).stdout
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = tmp_path / "app"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "notes.md").write_text("start\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    bindings = tmp_path / "bindings.json"
    bindings.write_text(json.dumps({"build": {"path": str(repo), "mode": "autonomous"}}))
    monkeypatch.setattr(reflexd, "ROOM_BINDINGS_PATH", bindings)
    leads = tmp_path / "leads.json"
    leads.write_text("{}")
    monkeypatch.setattr(reflexd, "ROOM_LEADS_PATH", leads)
    monkeypatch.setenv("REFLEXD_OWNER", "arav")
    monkeypatch.setattr(reflexd.reflexd_wake, "WORKTREES_ENABLED", True)
    d = reflexd.Reflexd(reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                              participant_name="a-codex",
                                              runtime_dir=tmp_path / "runtime"))
    monkeypatch.setattr(reflexd, "pace_for", lambda _n: 2)

    async def fake_run(harness, *, cwd=None, **_kw: Any) -> str:
        (Path(cwd) / "notes.md").write_text("done\n")
        git(Path(cwd), "commit", "-qam", "docs: notes")
        return "NO_REPLY"

    monkeypatch.setattr(d.adapter, "run", fake_run)
    relay = _Room()
    env = {"id": "e", "message_id": "m", "room": "build", "from_name": "arav",
           "content": "@a-codex write the notes", "message_type": "chat"}
    triage = reflexd.classify_message(content=env["content"], sender="arav",
                                      self_name="a-codex")

    async def go() -> None:
        await d._wake_and_reply(relay, env, triage=triage)
        if d._bg_tasks:
            await asyncio.gather(*list(d._bg_tasks))
    asyncio.run(go())
    assert "done" in git(repo, "show", "main:notes.md")  # it really published
    assert any("published" in p["content"] for p in relay.posted)
    assert len(d._turns) == 1 and d._refunds == []


def test_a_refund_removes_only_its_own_turn(monkeypatch) -> None:
    import time
    from datetime import datetime, timezone

    d, relay = _paced_daemon(monkeypatch), _Room()
    first, second = time.time() - 20, time.time() - 10
    d._turns = [first, second]
    relay.history.append({"id": "ask", "from_name": "a-claude", "timestamp": _now()})
    started = datetime.now(timezone.utc)
    assert asyncio.run(d._refund_turn(relay, "build", second, started))
    assert d._turns == [first]
    assert not asyncio.run(d._refund_turn(relay, "build", second, started))  # once only
    assert d._turns == [first]


# ── FRICTION #95: a parked wake re-reads the cap while it waits ──


def _parked_release(monkeypatch, caps: list[int | None]) -> tuple[Any, list[float], list[str]]:
    """Cap 1 with the hour's turn used, one wake parked. Each sleep step reads
    the next cap from *caps*. Returns the daemon, the steps slept and the wakes."""
    d = reflexd.Reflexd(reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                              participant_name="a-codex"))
    monkeypatch.setattr(reflexd, "PACE_RECHECK_S", 0.01)
    current = {"cap": 1}
    monkeypatch.setattr(reflexd, "pace_for", lambda _n: current["cap"])
    steps: list[float] = []
    plan = iter(caps)

    async def step(s: float) -> None:
        steps.append(s)
        try:
            current["cap"] = next(plan)
        except StopIteration:
            d._stop.set()  # out of script: end the wait

    woke: list[str] = []

    async def fake_wake(relay: Any, env: dict, *, triage: Any = None, **_k: Any) -> None:
        woke.append(env["room"])

    monkeypatch.setattr(d, "_sleep_or_stop", step)
    monkeypatch.setattr(d, "_wake_and_reply", fake_wake)
    d._record_turn()
    d._pace_parked["build"] = ({"room": "build", "from_name": "a-claude"}, None)
    asyncio.run(d._release_paced(_Relay(), 50 * 60))
    return d, steps, woke


def test_raising_the_cap_releases_a_parked_wake_at_once(monkeypatch) -> None:
    _d, steps, woke = _parked_release(monkeypatch, [10])
    assert woke == ["build"] and len(steps) == 1  # one short step, not 50 minutes


def test_removing_the_cap_releases_it(monkeypatch) -> None:
    _d, steps, woke = _parked_release(monkeypatch, [None])
    assert woke == ["build"] and len(steps) == 1


def test_a_cap_still_full_keeps_it_parked(monkeypatch) -> None:
    """Re-read every step, the cap of 1 still has no slot: it stays parked (the
    stop at the end of the script is the only way out here)."""
    _d, steps, woke = _parked_release(monkeypatch, [1, 1, 1])
    assert woke == [] and len(steps) == 4


def test_a_stop_during_a_step_returns_at_once(monkeypatch) -> None:
    d, steps, woke = _parked_release(monkeypatch, [])
    assert woke == [] and len(steps) == 1


def test_a_post_hidden_behind_a_busy_room_counts(monkeypatch) -> None:
    """codex, review of dd79fc65: the agent's own post, then 50 newer messages.
    The read never reaches back to the wake's start, so it can't refund."""
    def busy(relay: _Room) -> None:
        relay.history.append({"id": "own", "from_name": "a-codex", "content": "✅",
                              "timestamp": _now()})
        relay.history.extend({"id": f"x{n}", "from_name": "a-claude", "content": "...",
                              "timestamp": _now()} for n in range(reflexd.REFUND_LOOKBACK))

    d, relay = _paced_daemon(monkeypatch), _Room()
    _turn(monkeypatch, d, relay, 0, during=busy)
    assert len(d._turns) == 1 and d._refunds == []


def test_an_empty_read_counts(monkeypatch) -> None:
    """RelayClient.fetch_recent answers [] when the read fails: no proof, no refund."""
    d, relay = _paced_daemon(monkeypatch), _Room()
    _turn(monkeypatch, d, relay, 0, during=lambda r: r.history.clear())
    assert len(d._turns) == 1 and d._refunds == []
