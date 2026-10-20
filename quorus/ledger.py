"""Outcome ledger: which agent and vendor's work actually survives in this repo.

Every commit an agent publishes gets a provenance note (git notes, ref
``refs/notes/quorus``: agent, vendor, model, room). It never changes a commit
id. ``quorus scorecard`` then reads outcomes per commit:

* tests: did the commit touch tests?
* review: did a teammate approve it or flag it in the room?
* reverted: did a later commit revert it?
* survival: how much of what it added is still in the code at the end rev? Every file
  the commit touched is blamed at one pinned end commit. A renamed file is followed to its
  new path, a deleted file's lines count as gone, and a file that can't be blamed is
  "uncomputed": it stays out of the denominator and shows in ``coverage``, never as
  deleted lines.

Why: the weekend agents' commits mostly carried the human's git identity, so
nobody could tell which model's work held up (2026-10-17 research: no tool
measures this). Commits published before notes existed are matched to the
room's "(quorus) published N commit(s) from quorus/<agent>" lines, labelled
``inferred``.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Container, Iterable

NOTES_REF = "quorus"
_HARNESSES = ("claude", "codex", "gemini", "opencode", "cursor", "cline")
_APPROVE = re.compile(
    # "✅ abc1234 passes": a ✅ alone is also how agents announce their own ships.
    r"\b(approv\w*|lgtm|looks good|holds( up)?|accepted|signed off)\b|✅ reviewed"
    r"|✅[^\n]*\bpass(es|ed)?\b",
    re.IGNORECASE)
_FLAG = re.compile(r"\b(blocking|/disagree|reject\w*|regress\w*|broken|wrong|not approved|"
                   r"request(ed)? changes)\b", re.IGNORECASE)
_PUBLISHED = re.compile(
    r"\(quorus\) published (\d+) commit\(s\) from quorus/([\w-]+) to ([\w./-]+)")
_TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]+$|_test\.\w+$|\.(test|spec)\.\w+$")


def _git(repo: Path, *args: str, check: bool = False) -> str:
    # Hooks off: the ledger must never run repo code (same rule as the daemon).
    out = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=repo,
                         capture_output=True, text=True, timeout=60)
    if check and out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:200])
    return out.stdout


def vendor_of(agent: str) -> str:
    for h in _HARNESSES:
        if agent.endswith(f"-{h}") or f"-{h}-" in agent:
            return h
    return "unknown"


# ── provenance ───────────────────────────────────────────────────────────


def record_provenance(repo: Path, old: str, new: str, meta: dict[str, Any]) -> int:
    """Attach a provenance note to every commit in ``old..new``. Best effort."""
    shas = _git(repo, "rev-list", f"{old}..{new}").split()
    note = json.dumps({**meta, "published_at": datetime.now(timezone.utc).isoformat()},
                      sort_keys=True)
    for sha in shas:
        # A notes ref is a commit, and git refuses one without an identity: on
        # a fresh machine (and on CI) every note silently failed, so the
        # scorecard saw nothing. The ledger signs its own notes.
        _git(repo, "-c", "user.name=Quorus ledger", "-c", "user.email=ledger@quorus.local",
             "notes", f"--ref={NOTES_REF}", "add", "-f", "-m", note, sha)
    return len(shas)


def read_provenance(repo: Path, sha: str) -> dict[str, Any] | None:
    raw = _git(repo, "notes", f"--ref={NOTES_REF}", "show", sha).strip()
    try:
        data = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


# ── outcomes ─────────────────────────────────────────────────────────────


@dataclass
class Outcome:
    sha: str
    subject: str
    agent: str = "unknown"
    vendor: str = "unknown"
    model: str | None = None
    inferred: bool = False
    has_tests: bool = False
    review: str = "none"  # approved | flagged | none
    reverted: bool = False
    added: int = 0
    surviving: int = 0
    computed: int = 0       # added lines in files whose fate is known (the denominator)
    files_uncomputed: int = 0
    reviewers: list[str] = field(default_factory=list)
    duel_id: str | None = None        # set on a duel winner's commits
    duel_against: str | None = None

    @property
    def survival(self) -> float | None:
        return self.surviving / self.computed if self.computed else None

    @property
    def coverage(self) -> float | None:
        """Share of the added lines whose survival was computed."""
        return self.computed / self.added if self.added else None


def _commits(repo: Path, rev_range: str) -> list[tuple[str, str, int]]:
    out = []
    log = _git(repo, "log", "--no-merges", "--format=%H%x09%ct%x09%s", rev_range)
    for line in log.splitlines():
        sha, ts, subject = (line.split("\t", 2) + ["", ""])[:3]
        if sha:
            out.append((sha, subject, int(ts or 0)))
    return out


def _files_and_added(repo: Path, sha: str) -> tuple[list[str], int, dict[str, int]]:
    files, total, per_file = [], 0, {}
    for line in _git(repo, "show", "--numstat", "--format=", sha).splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].isdigit():
            files.append(parts[2])
            per_file[parts[2]] = int(parts[0])
            total += int(parts[0])
    return files, total, per_file


MAX_BLAME_FILES = 500  # beyond this a commit's other files are reported uncomputed


def _renames(repo: Path, sha: str, end: str) -> dict[str, str]:
    """Paths a commit touched that live under another name at *end*."""
    out = {}
    for line in _git(repo, "diff", "--name-status", "-M", sha, end).splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].startswith("R"):
            out[parts[1]] = parts[2]
    return out


def _surviving(repo: Path, sha: str, per_file: dict[str, int],
               end: str) -> tuple[int, int, dict[str, str]]:
    """(lines still present at *end*, added lines whose fate is known, per-file status).

    Status is "counted" (blamed at *end*), "deleted" (no longer at *end*, so its lines
    are gone) or "uncomputed" (blame failed or the cap was hit: excluded from the
    denominator, never read as deleted)."""
    alive, computed, status = 0, 0, {}
    renames: dict[str, str] | None = None
    for i, (path, added) in enumerate(per_file.items()):
        if i >= MAX_BLAME_FILES:
            status[path] = "uncomputed"
            continue
        target = path
        if _git_ok(repo, "cat-file", "-e", f"{end}:{path}") is False:
            if renames is None:
                renames = _renames(repo, sha, end)
            target = renames.get(path, "")
            if not target:
                status[path] = "deleted"
                computed += added
                continue
        blame = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "blame", "--line-porcelain", end,
             "--", target], cwd=repo, capture_output=True, text=True, timeout=60)
        if blame.returncode != 0:
            status[path] = "uncomputed"
            continue
        alive += sum(1 for ln in blame.stdout.splitlines() if ln.startswith(sha + " "))
        computed += added
        status[path] = "counted"
    return alive, computed, status


def _git_ok(repo: Path, *args: str) -> bool:
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=repo,
                          capture_output=True, timeout=60).returncode == 0


def _reverted(repo: Path, rev_range: str) -> set[str]:
    text = _git(repo, "log", "--format=%B", rev_range)
    return set(re.findall(r"This reverts commit ([0-9a-f]{7,40})", text))


def infer_from_room(history: Iterable[dict[str, Any]],
                    commits: list[tuple[str, str, int]],
                    branches: Container[str] | None = None) -> dict[str, str]:
    """sha → agent, from the daemon's "published N commit(s)" status lines.

    Each status line at time T claims the N newest commits committed at or
    before T (the daemon publishes, then posts). Approximate; labelled inferred.
    Only lines publishing into one of *branches* count, when given.
    """
    by_time = sorted(commits, key=lambda c: c[2])
    claimed: dict[str, str] = {}
    for m in sorted(history, key=lambda m: m.get("timestamp") or ""):
        hit = _PUBLISHED.search(m.get("content") or "")
        if not hit:
            continue
        n, agent, target = int(hit.group(1)), hit.group(2), hit.group(3)
        if branches is not None and target not in branches:  # e.g. laptop 2's own branch
            continue
        try:
            t = datetime.fromisoformat(str(m["timestamp"]).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        candidates = [c for c in by_time if c[2] <= t.timestamp() + 5 and c[0] not in claimed]
        for sha, _subj, _ts in candidates[-n:]:
            claimed[sha] = agent
    return claimed


# A quote of someone else's message ("12:06 @arav-codex: /disagree blocking",
# "> ...") is not the quoter's verdict: Gemini pasting retro output was scored
# as Gemini flagging two commits (2026-10-17).
_QUOTED = re.compile(r"^\s*(>|\d{1,2}:\d{2}\s+@[\w-]+:)")


def _own_words(body: str) -> str:
    return "\n".join(ln for ln in body.splitlines() if not _QUOTED.match(ln))


def _review(history: list[dict[str, Any]], sha: str, author: str) -> tuple[str, list[str]]:
    short = sha[:7]
    verdict, who = "none", []
    for m in sorted(history, key=lambda m: m.get("timestamp") or ""):
        body, sender = _own_words(m.get("content") or ""), m.get("from_name") or ""
        if short not in body or sender == author or body.startswith("(quorus)"):
            continue
        # Judge the commit by the lines that name it: "✅ abc1234 passes.\n
        # /disagree blocking on <something else>" approved abc1234 but scored as
        # a flag (codex, 2026-10-17). Lines with no verdict: the whole message.
        own = "\n".join(ln for ln in body.splitlines() if short in ln)
        if not (_APPROVE.search(own) or _FLAG.search(own)):
            own = body
        approve, flag = _APPROVE.search(own), _FLAG.search(own)
        if flag and not approve:  # "the regression was fixed; approved" is an approval
            verdict = "flagged"
            who.append(sender)
        elif approve:
            verdict = "approved"
            who.append(sender)
    return verdict, sorted(set(who))


def _branches_in_line(repo: Path, history: list[dict[str, Any]]) -> set[str]:
    """Publish targets on HEAD's line of history: the checked-out branch, or a
    local branch that HEAD contains or is behind. Run from an agent's worktree
    (`quorus/<agent>`), the room's `weekend/quorus` lines still count; laptop
    2's diverged branch doesn't (scorecards differed by checkout, 2026-10-17)."""
    head = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    targets = {hit.group(3) for m in history
               if (hit := _PUBLISHED.search(m.get("content") or ""))}
    found = {head} if head else set()
    for target in targets - found:
        if not _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{target}").strip():
            continue
        for older, newer in ((f"refs/heads/{target}", "HEAD"), ("HEAD", f"refs/heads/{target}")):
            ancestor = subprocess.run(
                ["git", "-c", "core.hooksPath=/dev/null", "merge-base", "--is-ancestor",
                 older, newer], cwd=repo, capture_output=True, timeout=60)
            if ancestor.returncode == 0:
                found.add(target)
                break
    return found


def outcomes(repo: Path, rev_range: str,
             history: list[dict[str, Any]] | None = None,
             end: str = "HEAD") -> list[Outcome]:
    """Outcomes for *rev_range*, with survival measured at *end*, resolved once to a
    commit so a checkout moving mid-run can't change the answer."""
    history = history or []
    end = _git(repo, "rev-parse", "--verify", f"{end}^{{commit}}", check=True).strip()
    commits = _commits(repo, rev_range)
    inferred = infer_from_room(history, commits, _branches_in_line(repo, history))
    reverted = _reverted(repo, rev_range)
    out: list[Outcome] = []
    for sha, subject, _ts in commits:
        o = Outcome(sha=sha, subject=subject)
        prov = read_provenance(repo, sha)
        if prov:
            o.agent = str(prov.get("agent") or "unknown")
            o.vendor = str(prov.get("vendor") or vendor_of(o.agent))
            o.model = prov.get("model")
            duel = prov.get("duel") if isinstance(prov.get("duel"), dict) else None
            if duel and duel.get("result") == "won":
                o.duel_id = str(duel.get("id") or "") or None
                o.duel_against = str(duel.get("against") or "") or None
        elif sha in inferred:
            o.agent, o.vendor, o.inferred = inferred[sha], vendor_of(inferred[sha]), True
        files, o.added, per_file = _files_and_added(repo, sha)
        o.has_tests = any(_TEST_PATH.search(f) for f in files)
        o.reverted = any(sha.startswith(r) or r.startswith(sha) for r in reverted)
        o.surviving, o.computed, status = _surviving(repo, sha, per_file, end)
        o.files_uncomputed = sum(v == "uncomputed" for v in status.values())
        o.review, o.reviewers = _review(history, sha, o.agent)
        out.append(o)
    return out


def scorecard(rows: list[Outcome], key: str = "agent") -> list[dict[str, Any]]:
    groups: dict[str, list[Outcome]] = {}
    for o in rows:
        groups.setdefault(getattr(o, key), []).append(o)
    # One duel = one win for the winner and one loss for the other side, by duel id.
    won: dict[str, set[str]] = {}
    lost: dict[str, set[str]] = {}
    for o in rows:
        if o.duel_id and o.duel_against:
            loser = o.duel_against if key == "agent" else vendor_of(o.duel_against)
            won.setdefault(getattr(o, key), set()).add(o.duel_id)
            lost.setdefault(loser, set()).add(o.duel_id)
    for name in lost:
        groups.setdefault(name, [])
    table = []
    for name, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        added = sum(o.added for o in items)
        computed = sum(o.computed for o in items)
        if not items:  # only lost duels in this range
            table.append({key: name, "commits": 0, "with_tests": 0, "approved": 0, "flagged": 0,
                          "reverted": 0, "survival": None, "coverage": None, "inferred": 0,
                          "duels_won": 0,
                          "duels_lost": len(lost.get(name, ()))})
            continue
        table.append({
            key: name,
            "commits": len(items),
            "with_tests": sum(o.has_tests for o in items),
            "approved": sum(o.review == "approved" for o in items),
            "flagged": sum(o.review == "flagged" for o in items),
            "reverted": sum(o.reverted for o in items),
            "survival": round(sum(o.surviving for o in items) / computed, 3) if computed else None,
            "coverage": round(computed / added, 3) if added else None,
            "inferred": sum(o.inferred for o in items),
            "duels_won": len(won.get(name, ())),
            "duels_lost": len(lost.get(name, ())),
        })
    return table


def to_json(rows: list[Outcome]) -> str:
    return json.dumps([{**asdict(o), "survival": o.survival, "coverage": o.coverage}
                       for o in rows], indent=2)
