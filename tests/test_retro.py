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
    _m("a-claude", "/disagree blocking: test_kit.md:137 still publishes the count", "03:13"),
    _m("a-claude", "rejected: zero source links", "03:14"),
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
    assert "2 reviews pushed back" in joined  # the regression-test reply is not a pushback


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
