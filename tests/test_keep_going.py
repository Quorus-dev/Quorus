"""An agent keeps going on its own until the task is done (Arav, 2026-11-05).

His Codex stopped mid-task ("plan: ..." and then nothing) and stayed idle until
someone posted again. A turn may now end with `/continue`; the daemon wakes it
again for the same request, up to a cap, and a person's `/stop` ends the run.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from quorus.runtime import keep_going

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("reflexd_kg", _ROOT / "scripts" / "reflexd.py")
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd_kg"] = reflexd
_spec.loader.exec_module(reflexd)


def test_the_marker_is_a_line_of_its_own() -> None:
    assert keep_going.asks_to_continue("step 1 of 3 done\n/continue")
    assert keep_going.asks_to_continue("done with the parser.\n  /continue.  \n")
    assert not keep_going.asks_to_continue("I'll /continue later")  # inside a sentence
    assert not keep_going.asks_to_continue("✅ abc123 tests green")
    assert keep_going.strip_marker("step 1 done\n/continue\n") == "step 1 done"


def test_the_next_turn_is_the_same_request(monkeypatch) -> None:
    env = {"id": "m1", "room": "build", "from_name": "arav", "content": "fix it"}
    one = keep_going.next_envelope(env, "2026-11-05T00:00:00+00:00")
    two = keep_going.next_envelope(one, "2026-11-06T00:00:00+00:00")
    assert (one["id"], one["from_name"], one["content"]) == ("m1", "arav", "fix it")
    assert (keep_going.turn_of(one), keep_going.turn_of(two)) == (1, 2)
    assert two["continue_since"] == "2026-11-05T00:00:00+00:00"  # when the run began
    monkeypatch.setenv("REFLEXD_MAX_CONTINUES", "3")
    assert keep_going.max_turns() == 3
    monkeypatch.setenv("REFLEXD_MAX_CONTINUES", "lots")
    assert keep_going.max_turns() == keep_going.DEFAULT_MAX_TURNS


def test_only_a_person_can_stop_it() -> None:
    now = datetime.now(timezone.utc)
    later = (now + timedelta(seconds=5)).isoformat()
    after = lambda ts: reflexd._ts_after(ts, now)  # noqa: E731
    is_agent = reflexd.is_agent_participant
    assert keep_going.stopped_by([{"from_name": "arav", "content": "/stop",
                                   "timestamp": later}], since=after, is_agent=is_agent) == "arav"
    assert keep_going.stopped_by([{"from_name": "arav-claude", "content": "/stop",
                                   "timestamp": later}], since=after, is_agent=is_agent) is None
    earlier = (now - timedelta(seconds=5)).isoformat()  # an old /stop ended an old run
    assert keep_going.stopped_by([{"from_name": "arav", "content": "/stop",
                                   "timestamp": earlier}], since=after, is_agent=is_agent) is None


def _daemon(monkeypatch, tmp_path, *, trusted: bool = True):
    monkeypatch.setenv("REFLEXD_OWNER", "arav")
    monkeypatch.setattr(reflexd, "mode_for", lambda room: "autonomous")
    monkeypatch.setattr(reflexd, "trust_for", lambda room: "owner")
    monkeypatch.setattr(reflexd, "lead_for", lambda room: None)
    monkeypatch.setattr(reflexd, "workspace_for", lambda room: None)
    monkeypatch.setattr(keep_going, "GAP_S", 0.0)
    d = reflexd.Reflexd(reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                              participant_name="arav-codex",
                                              runtime_dir=tmp_path / "rt"))

    async def trust(*_a, **_k) -> bool:
        return trusted

    monkeypatch.setattr(d, "_trusted_wake", trust)
    return d


def test_the_prompt_offers_continue_only_on_trusted_turns(monkeypatch, tmp_path) -> None:
    d = _daemon(monkeypatch, tmp_path)
    env = {"id": "w", "from_name": "arav", "room": "build", "content": "fix calc.py"}
    owner = d._build_prompt(env, [], has_workspace=True, trusted=True, harness="codex")
    assert "a line that says only `/continue`" in owner and "up to 10 more turn(s)" in owner
    assert "/continue" not in d._build_prompt(env, [], has_workspace=True, trusted=False,
                                              harness="codex")
    third = d._build_prompt({**env, "continue_turn": 3}, [], has_workspace=True,
                            trusted=True, harness="codex")
    assert "turn 3 of 10 on your own" in third and "up to 7 more turn(s)" in third
    last = d._build_prompt({**env, "continue_turn": 10}, [], has_workspace=True,
                           trusted=True, harness="codex")
    assert "turn 10 of 10" in last and "a line that says only" not in last


class _Relay:
    def __init__(self, stop_at: int | None = None) -> None:
        self.posted: list[dict] = []
        self.stop_at = stop_at
        self.reads = 0

    async def post_reply(self, **kw):
        self.posted.append(kw)
        return {"id": f"p{len(self.posted)}"}

    async def fetch_recent(self, **_kw):
        self.reads += 1
        if self.stop_at is not None and len(self.posted) >= self.stop_at:
            ts = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
            return [{"from_name": "arav", "content": "/stop", "timestamp": ts}]
        return []

    def __getattr__(self, _name):
        async def anything(*_a, **_k):
            return {}
        return anything


def _run(d, relay, replies: list[str], monkeypatch) -> list[str]:
    prompts: list[str] = []

    async def run(*a, **k) -> str:
        prompts.append(" ".join(str(x) for x in (*a, *k.values())))
        return replies[min(len(prompts), len(replies)) - 1]

    monkeypatch.setattr(d.adapter, "run", run)
    env = {"id": "m1", "message_id": "m1", "room": "build", "from_name": "arav",
           "content": "@arav-codex build the parser", "message_type": "chat"}

    async def scenario() -> None:
        async with d._wake_lock:
            await d._wake_and_reply(relay, env, reason="t")
        while (task := d._keep_going_tasks.get("build")) is not None and not task.done():
            await asyncio.wait_for(task, timeout=10)

    asyncio.run(scenario())
    return prompts


def _texts(relay) -> list[str]:
    return [m.get("content") or "" for m in relay.posted]


def test_an_unfinished_task_keeps_going_until_done(monkeypatch, tmp_path) -> None:
    d, relay = _daemon(monkeypatch, tmp_path), _Relay()
    prompts = _run(d, relay, ["step 1 done\n/continue", "step 2 done\n/continue",
                              "✅ parser built, tests green"], monkeypatch)
    assert len(prompts) == 3
    assert "turn 1 of 10 on your own" in prompts[1] and "turn 2 of 10" in prompts[2]
    texts = _texts(relay)
    assert any("✅ parser built" in t for t in texts)
    assert not any("/continue" in t for t in texts)  # the marker never reaches the room


def test_it_stops_at_the_cap_and_says_so(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("REFLEXD_MAX_CONTINUES", "2")
    d, relay = _daemon(monkeypatch, tmp_path), _Relay()
    prompts = _run(d, relay, ["more to do\n/continue"], monkeypatch)
    assert len(prompts) == 3  # the request, then two turns on its own
    assert any("stopped after 2 turns on its own" in t for t in _texts(relay))


def test_a_person_posting_stop_ends_the_run(monkeypatch, tmp_path) -> None:
    d, relay = _daemon(monkeypatch, tmp_path), _Relay(stop_at=1)
    prompts = _run(d, relay, ["more to do\n/continue"], monkeypatch)
    assert len(prompts) == 1
    assert any("@arav posted /stop" in t for t in _texts(relay))


@pytest.mark.parametrize("reply", ["⚠ codex exited 1 with no known cause\n/continue"])
def test_no_loop_for_an_untrusted_or_failed_turn(monkeypatch, tmp_path, reply) -> None:
    d = _daemon(monkeypatch, tmp_path, trusted=False)
    assert len(_run(d, _Relay(), ["sure\n/continue"], monkeypatch)) == 1
    d = _daemon(monkeypatch, tmp_path)
    assert len(_run(d, _Relay(), [reply], monkeypatch)) == 1


def test_disabled_with_zero(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("REFLEXD_MAX_CONTINUES", "0")
    d, relay = _daemon(monkeypatch, tmp_path), _Relay()
    assert len(_run(d, relay, ["more\n/continue"], monkeypatch)) == 1
    assert not any("stopped after" in t for t in _texts(relay))


def test_a_marker_ending_the_last_sentence_counts() -> None:
    """opencode on a free model wrote it inline and got no next turn (live run)."""
    reply = "✅ test_shout fixed. One function left (initials), so /continue."
    assert keep_going.asks_to_continue(reply)
    assert keep_going.strip_marker(reply) == "✅ test_shout fixed. One function left (initials)."
    assert not keep_going.asks_to_continue("use /continue when more is left, then stop")


def test_other_mentions_since_the_last_reply_are_named(monkeypatch, tmp_path) -> None:
    """A codex reviewer answered one review request and the second, already in its
    transcript, counted as answered: it sat 20 minutes (live run, 2026-11-05)."""
    d = _daemon(monkeypatch, tmp_path)
    history = [
        {"id": "a", "from_name": "kg-claude", "content": "@arav-codex review 1",
         "timestamp": "2026-11-05T01:55:00Z"},
        {"id": "b", "from_name": "arav-codex", "content": "GO on 1"},
        {"id": "c", "from_name": "kg-claude", "content": "@arav-codex please review 3",
         "timestamp": "2026-11-05T01:56:27Z"},
        {"id": "w", "from_name": "kg-opencode", "content": "@arav-codex review mine"},
    ]
    env = {"id": "w", "from_name": "kg-opencode", "room": "build", "content": "@arav-codex review"}
    text = d._build_prompt(env, history, has_workspace=True, trusted=False, harness="codex")
    assert "1 other message(s) that @-mention you since your last reply (@kg-claude at 01:56)" \
        in text
    assert "please review 3" not in text.split("# Reply chain depth")[0]  # text stays fenced
    assert "other message(s)" not in d._build_prompt(env, history[:2] + history[3:],
                                                     has_workspace=True, trusted=False,
                                                     harness="codex")


def test_a_bare_stop_wakes_nobody_and_ends_the_run(monkeypatch, tmp_path) -> None:
    """Woken by "/stop", agents each spent a turn saying "Stopped.", and that
    clean finish queued half-done work for review (live run, 2026-11-05)."""
    assert keep_going.is_stop_command("/stop")
    assert keep_going.is_stop_command("@kg-claude /stop.")
    assert not keep_going.is_stop_command("/stop the parser from crashing, please")
    d, relay = _daemon(monkeypatch, tmp_path), _Relay()
    monkeypatch.setattr(keep_going, "GAP_S", 0.3)
    runs: list[int] = []

    async def run(*_a, **_k) -> str:
        runs.append(1)
        return "step done\n/continue"

    monkeypatch.setattr(d.adapter, "run", run)
    env = {"id": "m1", "message_id": "m1", "room": "build", "from_name": "arav",
           "content": "@arav-codex build it", "message_type": "chat"}
    stop = {"id": "s1", "message_id": "s1", "room": "build", "from_name": "arav",
            "content": "/stop", "message_type": "chat"}

    async def scenario() -> None:
        async with d._wake_lock:
            await d._wake_and_reply(relay, env, reason="t")
        async with d._wake_lock:
            await d._wake_and_reply(relay, stop, reason="t")
        await asyncio.sleep(0.6)

    asyncio.run(scenario())
    assert runs == [1]  # no turn for the /stop, and no next turn
    assert any("@arav posted /stop" in t for t in _texts(relay))


def test_a_stop_posted_after_the_request_counts() -> None:
    env = {"id": "m", "timestamp": "2026-11-05T02:22:49Z"}  # relay style, with Z
    assert keep_going.next_envelope(env, "2026-11-05T02:23:56Z")["continue_since"] \
        == "2026-11-05T02:22:49Z"
