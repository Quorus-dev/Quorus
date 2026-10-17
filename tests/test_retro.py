"""`quorus retro`: the self-improvement loop's eyes (2026-10-17).

Proof bar from the weekend brief: on the weekend rooms it must rediscover the
failure patterns the overseer fixed by hand. These fixtures are those patterns.
"""

from __future__ import annotations

from quorus import retro


def _m(who: str, body: str, t: str, kind: str = "chat") -> dict:
    return {"from_name": who, "content": body, "timestamp": f"2026-10-17T{t}:00Z",
            "message_type": kind}


HISTORY = [
    _m("a-opencode", "⚠ opencode: the vendor rate-limited this account; the wake was dropped.",
       "04:00"),
    _m("a-opencode", "⚠ opencode: the vendor rate-limited this account; the wake was dropped.",
       "04:01"),
    _m("a-gemini", "plan: redo paperclip.md with sources", "05:20"),
    _m("a-gemini", "plan: redo paperclip.md with sources", "06:50"),
    _m("a-gemini", "plan: redo paperclip.md with sources", "07:16"),
    *[_m("a-claude", "@a-claude read it, nothing to add", f"03:0{i}", "status") for i in range(4)],
    _m("a-codex", "(quorus) agents paused after 40 agent messages in a row.", "03:11", "status"),
    _m("a-codex", "(quorus) conflict: quorus/a-codex conflicts with main", "03:12"),
    _m("a-claude", "@a-gemini /disagree blocking: test_kit.md:137 still publishes the count",
       "03:13"),
    _m("a-claude", "@a-gemini rejected: zero source links", "03:14"),
    _m("a-claude", "@a-gemini I disagree that it's done: nothing is committed", "03:16"),
    _m("a-codex", "added a regression test for the blank score bug", "03:15"),
]


def test_rediscovers_the_weekend_failure_patterns() -> None:
    titles = [f.title for f in retro.analyze(HISTORY)]
    joined = " | ".join(titles)
    assert "@a-opencode failed 2 times" in joined          # vendor storm
    assert "@a-gemini announced 3 plans but delivered 0" in joined  # plan loop
    assert "@a-claude posted 'nothing to add' 4 times" in joined   # silence spam
    assert "paused or stopped chatter 1 times" in joined
    assert "1 merge conflicts" in joined
    assert "@a-gemini's work was pushed back 3 times" in joined  # not the regression-test reply


def test_ranking_puts_failures_above_noise_and_quota_gets_the_pacing_fix() -> None:
    findings = retro.analyze(HISTORY)
    top2 = {f.title.split()[0] for f in findings[:2]}
    assert top2 == {"@a-gemini", "@a-opencode"}  # stalls and failures before chatter
    storm = next(f for f in findings if f.title.startswith("@a-opencode failed"))
    assert "quorus agent pace" in storm.fix


def test_summary_addresses_the_lead_and_quiet_rooms_say_so() -> None:
    text = retro.summary(retro.analyze(HISTORY), 6, lead="a-claude")
    assert text.startswith("@a-claude Retro, last 6h") and "\n1. " in text
    assert retro.summary([], 6).endswith("nothing stands out.")
    assert retro.analyze([_m("a-codex", "✅ fixed and committed", "01:00")]) == []


def test_a_review_that_ends_in_a_block_counts_as_delivered() -> None:
    # Live 2026-10-17: codex's two design reviews ended in /disagree blocking,
    # and retro reported it as "announced 3 plans but delivered 1".
    history = [
        _m("a-codex", "plan: On it — /claim 6a947e63 design review", "12:06"),
        _m("a-codex", "@a-claude /disagree blocking on 6a947e63: legacy auth bypass", "12:07"),
        _m("a-codex", "plan: On it — /claim ffe25d3b review", "12:13"),
        _m("a-codex", "@a-claude /disagree blocking on ffe25d3b: shared dir", "12:14"),
        _m("a-codex", "plan: re-review f457fb94", "12:20"),
        _m("a-codex", "approved f457fb94; lifting my block", "12:22"),
    ]
    assert not [f for f in retro.analyze(history) if "announced" in f.title]


def test_review_verdicts_are_delivery_and_design_pushback_is_not_a_problem() -> None:
    """Live retro, 2026-10-17: codex's three reviews read as "3 plans, 1
    delivered", and a healthy design review read as "4 reviews pushed back"."""
    room = [
        _m("a-codex", "plan: review the design", "12:06"),
        _m("a-codex", "@a-claude /disagree blocking: legacy auth bypasses membership", "12:06"),
        _m("a-codex", "plan: review ffe25d3b", "12:13"),
        _m("a-codex", "✅ @a-claude approved f457fb94; lifting my block", "12:19"),
        _m("a-codex", "plan: review 7ebd213d", "12:40"),
        _m("a-codex", "@a-claude LGTM on 7ebd213d", "12:45"),
        _m("a-claude", "@a-gemini rejected: no sources", "12:50"),
        _m("a-claude", "The design is up in 6a947e63 with every alternative written out. "
           "I rejected a shared remote. @a-codex please review", "12:05"),
    ]
    titles = " | ".join(f.title for f in retro.analyze(room))
    assert "a-codex announced" not in titles
    assert "pushed back" not in titles  # one pushback each: the process working


def test_cli_shows_room_text_verbatim(monkeypatch) -> None:
    """Rich ate "[reflexd]" as a markup tag: the title read ' harness timed out'."""
    from types import SimpleNamespace

    from quorus_cli import retro_cmd
    from rich.console import Console

    room = [_m("a-gemini", "[reflexd] harness timed out", "12:42")] * 2
    monkeypatch.setattr(retro_cmd, "_history", lambda r: room)
    monkeypatch.setattr(retro_cmd, "_since", lambda h, hours: h)
    console = Console(record=True, width=200)
    retro_cmd.cmd_retro(SimpleNamespace(room="r", since=6.0, top=3, post=False), console)
    assert "[reflexd] harness timed out" in console.export_text()
