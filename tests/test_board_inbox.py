"""The "needs you" inbox model (docs/plans/board-inbox.md, slice 3): `quorus/web/inbox.js`
is pure, so it runs here under node against the shapes the relay really returns: room
history (chat, status and `social` envelopes, routes/social.py) and pending approvals.
Wire blocks resolve the way services/social_svc.py resolves them; prose never does."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quorus import board
from quorus.board import INBOX_JS, ROWS_JS

NODE = shutil.which("node")
ME = "arav"
NOW = 1792260000  # 2026-10-17T12:00:00Z
T0 = "2026-10-17T11:00:00Z"


def _iso(minute: int) -> str:
    return f"2026-10-17T11:{minute:02d}:00Z"


def msg(mid: str, sender: str, content: str, minute: int = 0, kind: str = "chat") -> dict:
    return {"id": mid, "from_name": sender, "content": content, "message_type": kind,
            "timestamp": _iso(minute)}


def verb(mid: str, actor: str, name: str, payload: dict, ref: str | None = None,
         minute: int = 0) -> dict:
    env = {"kind": "social", "verb": name, "actor": actor, "room_id": "r",
           "ref_message_id": ref, "ts": _iso(minute), "payload": payload}
    return msg(mid, actor, json.dumps(env), minute, kind="social")


def _inbox(tmp_path: Path, history: list, approvals: list, members: int | None = 3,
           me: str = ME) -> list[dict]:
    for name in ("rows.js", "inbox.js"):
        shutil.copy(Path(board.__file__).parent / "web" / name, tmp_path / name)
    (tmp_path / "run.mjs").write_text(
        "import { inboxItems } from './inbox.js';\n"
        "globalThis.sessionStorage = { getItem: () => null };\n"
        f"const items = inboxItems({json.dumps(history)}, {json.dumps(approvals)}, "
        f"{json.dumps(me)}, {NOW}, {json.dumps(members)});\n"
        "console.log(JSON.stringify(items));\n")
    out = subprocess.run([NODE, "run.mjs"], cwd=tmp_path, capture_output=True, text=True,
                         timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _kinds(items: list[dict]) -> list[tuple[str, str]]:
    return [(i["kind"], i["ref"]) for i in items]


BLOCK_ON_CLAUDE = [  # arav-claude ships; eli-codex blocks it on the wire
    msg("m1", "arav-claude", "✅ abc1234 done\n(quorus) published 1 commit(s) from "
        "quorus/arav-claude to main", 1),
    verb("v1", "eli-codex", "disagree", {"ref_message_id": "m1", "reason": "abc1234 breaks "
         "the relay", "mode": "blocking"}, ref="m1", minute=2),
]

pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def test_approvals_outrank_blocks_and_each_kind_is_oldest_first(tmp_path: Path) -> None:
    history = BLOCK_ON_CLAUDE + [
        msg("q2", "arav-codex", "@arav should I take the legal memo next?", 9),
        msg("q1", "arav-claude", "@arav may I delete the old branch?", 5),
        msg("r1", "eli-codex", "@arav please review `deadbee` before I merge", 7),
        msg("h1", "arav-codex", "(quorus) couldn't hold quorus/arav-codex's commits after a "
            "mid-turn message; kept unpublished", 8, kind="status"),
    ]
    approvals = [{"id": "ap1", "agent": "arav-codex", "tool_name": "Bash",
                  "input_preview": "git push", "created_at": _iso(10)}]
    items = _inbox(tmp_path, history, approvals)
    assert _kinds(items) == [("approval", "ap1"), ("block", "v1"), ("review", "r1"),
                             ("hold", "h1"), ("question", "q1"), ("question", "q2")]
    approval, block, review = items[0], items[1], items[2]
    assert approval["what"] == "wants to run Bash: git push" and approval["actions"] == [
        "approve", "deny"]
    assert block["who"] == "eli-codex" and block["on"] == "arav-claude"
    assert block["sha"] == "abc1234" and block["poll"] == "vote_m1" and block["prose"] is False
    assert review["sha"] == "deadbee" and review["who"] == "eli-codex"
    assert all(i["age"] >= 0 and i["at"] > 0 for i in items)


def test_a_wire_block_stays_open_across_plain_release_defer_and_minority_vote(
        tmp_path: Path) -> None:
    history = BLOCK_ON_CLAUDE + [
        verb("v2", "eli-codex", "release", {"task_id": "t1", "reason": "done"}, minute=3),
        verb("v3", "arav-claude", "defer", {"to": "eli-codex", "reason": "yours"}, minute=4),
        verb("v4", "arav-claude", "vote", {"option": "approve"}, ref="m1", minute=5),
        verb("v5", "arav-claude", "vote", {"option": "approve"}, ref="m1", minute=6),  # again
        msg("c1", "eli-codex", "lifting my block on @arav-claude, LGTM", 7),  # prose only
    ]
    assert _kinds(_inbox(tmp_path, history, [], members=3)) == [("block", "v1")]


def test_a_wire_block_closes_on_a_handoff_release_or_a_majority_vote(tmp_path: Path) -> None:
    handoff = BLOCK_ON_CLAUDE + [
        verb("v2", "arav-claude", "release", {"task_id": "t1", "reason": "take it",
                                              "handoff_to": "eli-codex"}, minute=3)]
    assert _inbox(tmp_path, handoff, [], members=3) == []
    majority = BLOCK_ON_CLAUDE + [
        verb("v2", "arav-claude", "vote", {"option": "approve"}, ref="m1", minute=3),
        verb("v3", "arav", "vote", {"option": "approve", "poll_id": "vote_m1"}, minute=4),
    ]
    assert _inbox(tmp_path, majority, [], members=3) == []  # 2 of ceil(3/2)=2 voters, 100%
    # Five members: two voters are a majority of the weight but short of the quorum.
    assert _kinds(_inbox(tmp_path, majority, [], members=5)) == [("block", "v1")]
    # No member count known: the relay's legacy strict-majority rule, so it clears.
    assert _inbox(tmp_path, majority, [], members=None) == []


def test_advisory_disagrees_and_unrelated_polls_never_touch_a_block(tmp_path: Path) -> None:
    history = BLOCK_ON_CLAUDE + [
        verb("v2", "eli-codex", "disagree", {"ref_message_id": "m1", "reason": "nit on "
             "abc1234", "mode": "advisory"}, ref="m1", minute=3),
        verb("v3", "arav-claude", "vote", {"option": "approve", "poll_id": "vote_other"},
             minute=4),
        verb("v4", "arav", "vote", {"option": "approve", "poll_id": "vote_other"}, minute=5),
    ]
    items = _inbox(tmp_path, history, [], members=3)
    assert _kinds(items) == [("block", "v1")]  # the advisory isn't one; the votes went elsewhere
    # A block on someone else's agent is not the viewer's.
    other = [msg("m9", "eli-codex", "✅ 9999999 done", 1),
             verb("v9", "arav-claude", "disagree", {"ref_message_id": "m9", "reason": "no",
                                                    "mode": "blocking"}, ref="m9", minute=2)]
    assert _inbox(tmp_path, other, [], members=3) == []


def test_a_prose_block_is_marked_prose_and_never_clears_a_wire_block(tmp_path: Path) -> None:
    history = BLOCK_ON_CLAUDE + [
        msg("p1", "eli-codex", "@arav-codex /disagree blocking on `feedbac`: tests fail", 3),
        msg("p2", "eli-codex", "@arav-claude lifting my block, ✅ GO on abc1234", 4),
    ]
    items = _inbox(tmp_path, history, [], members=3)
    assert _kinds(items) == [("block", "v1"), ("block", "p1")]
    wire, prose = items
    assert wire["prose"] is False  # eli's prose "lifting" didn't clear the wire block
    assert prose["prose"] is True and prose["on"] == "arav-codex" and prose["sha"] == "feedbac"
    lifted = history + [msg("p3", "eli-codex", "@arav-codex LGTM, lifting my block", 5)]
    assert _kinds(_inbox(tmp_path, lifted, [], members=3)) == [("block", "v1")]


def test_a_review_request_closes_on_the_viewers_verdict(tmp_path: Path) -> None:
    ask = [msg("r1", "arav-claude", "@arav please review 1a2b3c4 for GO", 1),
           msg("r2", "eli", "@arav can you review cafef00 today?", 2)]
    assert _kinds(_inbox(tmp_path, ask, [])) == [("review", "r1"), ("review", "r2")]
    by_sha = ask + [msg("a1", "arav", "✅ GO on 1a2b3c4", 3)]
    assert _kinds(_inbox(tmp_path, by_sha, [])) == [("review", "r2")]
    by_name = ask + [msg("a2", "arav", "@eli /disagree blocking: not yet", 3)]
    assert _kinds(_inbox(tmp_path, by_name, [])) == [("review", "r1")]
    chatter = ask + [msg("a3", "arav", "looking now", 3)]  # no verdict: both stay
    assert len(_inbox(tmp_path, chatter, [])) == 2


def test_a_hold_lasts_until_the_agents_next_publish_line(tmp_path: Path) -> None:
    held = [msg("h1", "arav-codex", "(quorus) couldn't check quorus/arav-codex before "
                "publishing; kept unpublished", 1, kind="status")]
    assert _kinds(_inbox(tmp_path, held, [])) == [("hold", "h1")]
    published = held + [msg("h2", "arav-codex", "✅ fixed\n(quorus) published 2 commit(s) from "
                            "quorus/arav-codex to main", 2)]
    assert _inbox(tmp_path, published, []) == []
    not_mine = [msg("h3", "eli-codex", "(quorus) ... kept unpublished", 1, kind="status")]
    assert _inbox(tmp_path, not_mine, []) == []


def test_a_question_closes_when_the_viewer_answers(tmp_path: Path) -> None:
    asked = [msg("q1", "arav-codex", "@arav should I take the legal memo next?", 1),
             msg("s1", "arav-codex", "@arav plan: ...?", 2, kind="status")]  # status: never
    items = _inbox(tmp_path, asked, [])
    assert _kinds(items) == [("question", "q1")] and items[0]["actions"] == ["reply"]
    assert _inbox(tmp_path, asked + [msg("a1", "arav", "yes", 3)], []) == []
    assert _inbox(tmp_path, [msg("q2", "eli", "@arav lunch?", 1)], []) == []  # not an agent


def test_empty_or_odd_inputs_give_an_empty_inbox_not_a_throw(tmp_path: Path) -> None:
    assert _inbox(tmp_path, [], []) == []
    odd = [msg("x1", "arav-codex", "", 1), {"id": "x2"}, msg("x3", "eli", "{not json", 2,
                                                                 kind="social")]
    assert _inbox(tmp_path, odd, [], members=None) == []
    assert _inbox(tmp_path, odd, [], me="") == []


def test_inbox_js_is_served_under_the_csp_and_stays_pure() -> None:
    from quorus.relay import app

    for sink in ("innerHTML", "document.", "fetch(", "window.", "eval("):
        assert sink not in INBOX_JS, sink
    assert 'from "./rows.js"' in INBOX_JS and "ASK_RE" in ROWS_JS
    with TestClient(app) as c:
        r = c.get("/inbox.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    assert r.text == INBOX_JS and r.headers["cache-control"] == "no-cache"


def test_a_block_whose_ref_is_only_in_the_payload_still_counts(tmp_path: Path) -> None:
    """Codex on 884b9152: the relay reads payload.ref_message_id (social_svc.py:307); the
    envelope's top-level ref may be null."""
    history = [msg("m1", "arav-claude", "✅ abc1234 done", 1),
               verb("v1", "eli-codex", "disagree", {"ref_message_id": "m1", "reason": "no",
                                                   "mode": "blocking"}, ref=None, minute=2)]
    items = _inbox(tmp_path, history, [], members=3)
    assert _kinds(items) == [("block", "v1")] and items[0]["poll"] == "vote_m1"


def test_the_relay_keeps_one_active_block_and_the_newest_supersedes(tmp_path: Path) -> None:
    """Codex on 884b9152: two blocks, then a majority on the newest, must leave nothing;
    the relay has one blocking_disagree_ref, so the older block is not retained."""
    two = BLOCK_ON_CLAUDE + [
        msg("m2", "arav-codex", "✅ feedbac shipped", 3),
        verb("v2", "eli-codex", "disagree", {"ref_message_id": "m2", "reason": "feedbac "
             "regresses", "mode": "blocking"}, ref="m2", minute=4),
    ]
    items = _inbox(tmp_path, two, [], members=3)
    assert _kinds(items) == [("block", "v2")] and items[0]["on"] == "arav-codex"
    resolved = two + [
        verb("v3", "arav-claude", "vote", {"option": "approve"}, minute=5),  # active poll
        verb("v4", "arav", "vote", {"option": "approve", "poll_id": "vote_m2"}, minute=6),
    ]
    assert _inbox(tmp_path, resolved, [], members=3) == []


def test_poll_tallies_persist_across_events_like_the_relays_state(tmp_path: Path) -> None:
    """A vote cast in vote_m1 survives m1 being superseded and re-blocked (social_svc.py
    setdefault keeps the tally), so one more voter then reaches the majority."""
    history = BLOCK_ON_CLAUDE + [
        verb("v2", "arav-claude", "vote", {"option": "approve"}, minute=3),  # 1 of 2 needed
        msg("m2", "arav-codex", "✅ feedbac shipped", 4),
        verb("v3", "eli-codex", "disagree", {"ref_message_id": "m2", "reason": "no",
                                             "mode": "blocking"}, ref="m2", minute=5),
        verb("v4", "eli-codex", "disagree", {"ref_message_id": "m1", "reason": "again",
                                             "mode": "blocking"}, ref="m1", minute=6),
    ]
    assert _kinds(_inbox(tmp_path, history, [], members=3)) == [("block", "v4")]
    second = history + [verb("v5", "arav", "vote", {"option": "approve"}, minute=7)]
    assert _inbox(tmp_path, second, [], members=3) == []  # the earlier vote still counts
    repeat = history + [verb("v6", "arav-claude", "vote", {"option": "approve"}, minute=7)]
    assert _kinds(_inbox(tmp_path, repeat, [], members=3)) == [("block", "v4")]  # refused


def test_a_prose_block_is_reply_only_and_a_wire_block_votes_in_its_poll(tmp_path: Path) -> None:
    """Codex on bb78c474: the card and the keys follow the item's own actions, so a prose
    block (no poll) never sends a vote into `vote_`."""
    history = BLOCK_ON_CLAUDE + [
        msg("p1", "eli-codex", "@arav-codex /disagree blocking on `feedbac`: tests fail", 3)]
    wire, prose = _inbox(tmp_path, history, [], members=3)
    assert wire["actions"] == ["vote", "reply"] and wire["poll"] == "vote_m1"
    assert prose["actions"] == ["reply"] and prose["poll"] == "" and prose["prose"] is True
    assert "if (!what) return;" in board.BOARD_JS  # a key the item lacks does nothing
    assert "(it.actions || []).map((what) => BUTTON[what])" in board.BOARD_JS


def test_a_review_closes_on_the_viewers_wire_vote_or_disagree(tmp_path: Path) -> None:
    """Codex on bb78c474: the inbox's `a` posts a vote in the request's own poll; that must
    end the request, as must a wire disagree that references it."""
    ask = [msg("r1", "arav-claude", "@arav please review 1a2b3c4 for GO", 1)]
    items = _inbox(tmp_path, ask, [])
    assert _kinds(items) == [("review", "r1")] and items[0]["actions"] == [
        "vote", "disagree", "reply"]
    voted = ask + [verb("v1", "arav", "vote", {"option": "approve", "poll_id": "vote_r1"},
                        ref="r1", minute=2)]
    assert _inbox(tmp_path, voted, []) == []
    disagreed = ask + [verb("v2", "arav", "disagree", {"ref_message_id": "r1", "reason": "no",
                                                       "mode": "advisory"}, minute=2)]
    assert _inbox(tmp_path, disagreed, []) == []
    elsewhere = ask + [verb("v3", "arav", "vote", {"option": "approve", "poll_id": "vote_zz"},
                            minute=2)]  # a vote in another poll is no verdict on r1
    assert _kinds(_inbox(tmp_path, elsewhere, [])) == [("review", "r1")]
    # Codex on fc4ed6ee: an explicit poll_id wins over the envelope's ref, as in the relay
    # (social_svc.py:389-404), so this vote went to vote_other and r1 stays open.
    explicit = ask + [verb("v5", "arav", "vote", {"option": "approve", "poll_id": "vote_other"},
                           ref="r1", minute=2)]
    assert _kinds(_inbox(tmp_path, explicit, [])) == [("review", "r1")]
    # No poll_id while a block is active: the relay puts the vote in the block's poll, so
    # it is no verdict on r1 either (and that vote alone doesn't clear the block).
    with_block = BLOCK_ON_CLAUDE + ask + [verb("v6", "arav", "vote", {"option": "approve"},
                                               ref="r1", minute=3)]
    assert _kinds(_inbox(tmp_path, with_block, [], members=3)) == [("block", "v1"),
                                                                   ("review", "r1")]
    someone_else = ask + [verb("v4", "arav-codex", "vote", {"option": "approve",
                                                           "poll_id": "vote_r1"}, minute=2)]
    assert _kinds(_inbox(tmp_path, someone_else, [])) == [("review", "r1")]
