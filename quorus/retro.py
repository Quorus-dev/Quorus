"""`quorus retro`: find what's going wrong in a room, so the team can fix it.

On the first weekend run the overseer found most product bugs by reading room
transcripts for failure patterns (2026-10-17). This module does that reading
mechanically: no model, so it's fast, free, repeatable, and safe to run often.

Each detector returns findings: what happened, how often, evidence, the likely
cause, and the fix to try. Ranked by count x weight.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

STUCK = re.compile(r"^(⚠|\[reflexd\] )")
QUOTA = re.compile(r"out of quota|rate-limited|usage limit|quota", re.IGNORECASE)
PLAN = re.compile(r"^plan:", re.IGNORECASE)
# A review verdict is delivered work too: codex's three design reviews read as
# "3 plans, 1 delivered" (live retro, 2026-10-17).
DONE = re.compile(r"✅|\(quorus\) published|\bdone\b|\bfixed\b|\bcommitted\b|\bapproved?\b|"
                  r"\bLGTM\b|/disagree|lifting my block|\bsigned off\b", re.IGNORECASE)
MENTION = re.compile(r"(?<![\w-])@([a-z0-9][a-z0-9-]*)", re.IGNORECASE)
NOTHING = re.compile(r"read it, nothing to add")
PAUSED = re.compile(r"\(quorus\) agents (paused|stopped answering)")
CONFLICT = re.compile(r"\(quorus\) conflict:")
# Explicit verdicts only: "regression test" in a normal reply is not a pushback.
FLAGGED = re.compile(r"(\bdisagree\b|\brejected\b|\bblocking\b|\bnot approved\b|"
                     r"\brequest(?:ed)? changes\b|\bno sources\b|"
                     r"\b(?:another|one more) pass\b)", re.IGNORECASE)


@dataclass
class Finding:
    title: str
    count: int
    weight: int
    cause: str
    fix: str
    evidence: list[str] = field(default_factory=list)

    @property
    def score(self) -> int:
        return self.count * self.weight


def _short(m: dict[str, Any]) -> str:
    ts = str(m.get("timestamp") or "")[11:16]
    body = (m.get("content") or "").replace("\n", " ")
    return f"{ts} @{m.get('from_name', '?')}: {body[:90]}"


def _by_agent(msgs: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for m in msgs:
        out[m.get("from_name") or "?"].append(m)
    return out


def analyze(history: list[dict[str, Any]]) -> list[Finding]:
    msgs = sorted(history, key=lambda m: m.get("timestamp") or "")
    found: list[Finding] = []

    stuck = [m for m in msgs if STUCK.match(m.get("content") or "")]
    for agent, items in _by_agent(stuck).items():
        reasons = Counter((m.get("content") or "").split("\n")[0][:70] for m in items)
        top, n = reasons.most_common(1)[0]
        quota = QUOTA.search(top)
        found.append(Finding(
            f"@{agent} failed {len(items)} times ({top!r} x{n})", len(items), 5,
            "the vendor's limit or free tier ran out" if quota else
            "the agent CLI is failing; the message says why",
            "pace it (`quorus agent pace`), switch it to a lighter --model, or give it fewer tasks"
            if quota else "check the CLI's login and version; see `quorus agent list` and its log",
            [_short(m) for m in items[:3]]))

    quiet = (m for m in msgs if NOTHING.search(m.get("content") or ""))
    for agent, items in _by_agent(quiet).items():
        if len(items) >= 3:
            found.append(Finding(
                f"@{agent} posted 'nothing to add' {len(items)} times", len(items), 1,
                "it's being woken for messages that don't need it (passing mentions, queues)",
                "mention it only when you need it; the lead can batch its requests",
                [_short(m) for m in items[:3]]))

    by_agent = _by_agent(msgs)
    for agent, items in by_agent.items():
        plans = [m for m in items if PLAN.match((m.get("content") or "").strip())]
        dones = [m for m in items if DONE.search(m.get("content") or "")
                 and not PLAN.match((m.get("content") or "").strip())]
        if len(plans) >= 2 and len(dones) < len(plans) / 2:
            found.append(Finding(
                f"@{agent} announced {len(plans)} plans but delivered {len(dones)}",
                len(plans) - len(dones), 4,
                "it starts work it can't finish (hung tool, too hard for the model, quota)",
                "give it smaller, checkable tasks, or reassign the work to a stronger agent",
                [_short(m) for m in plans[:3]]))

    pauses = [m for m in msgs if PAUSED.search(m.get("content") or "")]
    if pauses:
        found.append(Finding(
            f"agents paused or stopped chatter {len(pauses)} times", len(pauses), 2,
            "agents talk to each other without a human for long stretches",
            "give the room a lead (`--lead`) and @-mention assignments; humans reset the guard",
            [_short(m) for m in pauses[:3]]))

    conflicts = [m for m in msgs if CONFLICT.search(m.get("content") or "")]
    if conflicts:
        found.append(Finding(
            f"{len(conflicts)} merge conflicts between agents", len(conflicts), 3,
            "two agents changed the same files at once",
            "have the lead split work by file or module, not by feature",
            [_short(m) for m in conflicts[:3]]))

    # Pushback is the review process working. The signal is one agent whose
    # work keeps getting pushed back ("4 reviews pushed back" spread across
    # a design review was noise, live retro 2026-10-17).
    pushed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for m in msgs:
        body = m.get("content") or ""
        # Verdicts lead with it ("@x /disagree blocking", "I disagree that");
        # a design note saying "I rejected a shared remote" is not one.
        if not FLAGGED.search(body[:80]) or body.startswith("(quorus)"):
            continue
        targets = [t for t in MENTION.findall(body) if t != m.get("from_name")]
        if targets:
            pushed[targets[0]].append(m)
    for target, items in pushed.items():
        if len(items) >= 3:
            found.append(Finding(
                f"@{target}'s work was pushed back {len(items)} times", len(items), 3,
                "its work lands before it meets the bar, or it reports work as done "
                "before committing it",
                f"verify @{target}'s claims before building on them; give it smaller, "
                "checkable tasks; see `quorus scorecard`",
                [_short(m) for m in items[:3]]))

    return sorted(found, key=lambda f: -f.score)


def summary(findings: list[Finding], hours: float, lead: str | None = None,
            limit: int = 5) -> str:
    """One room message with the top findings, addressed to the lead if any."""
    head = (f"@{lead} " if lead else "") + f"Retro, last {hours:g}h: what to fix first"
    lines = [f"{i}. {f.title}. Likely: {f.cause}. Try: {f.fix}."
             for i, f in enumerate(findings[:limit], 1)]
    return "\n".join([head, *lines]) if lines else head + ": nothing stands out."
