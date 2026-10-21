"""Observational case study of a repo's agent ledger.

A ledger demo, not a ranking. Two steps:

    python scripts/case_study.py freeze --repo R --start SHA --end SHA --cutoff ISO-UTC \\
        --review-events events.jsonl --out DIR
    python scripts/case_study.py build --repo R --out DIR

`freeze` writes the frozen inputs (the provenance notes for the range, the review events
as given) and `manifest.json` with every hash. `build` refuses to run if any input no
longer matches its hash, then writes `coverage.jsonl` (every eligible commit) and
`dataset.jsonl` (every attributed commit) deterministically: same manifest, same bytes.

Survival comes from `quorus.ledger` (the scorecard's own code, so both agree), blamed at
the frozen END sha, never checkout HEAD. Each file is `counted` (blamed at END, a rename
followed to its new path), `deleted` (gone at END with no rename: its lines count as
gone) or `uncomputed` (git failed or timed out: left out of the denominator, never read
as deleted). The ratio's denominator is the lines whose fate is known, reported with
its coverage.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# The repo root holds quorus/ledger.py, whether this file runs as scripts/case_study.py
# or as the copy shipped inside a docs/case-study/<run>/ package. ledger.py is loaded by
# path: it needs only the standard library, while `import quorus` pulls in the SDK.
_ROOT = next(p for p in Path(__file__).resolve().parents
             if (p / "quorus" / "ledger.py").exists())
sys.path.insert(0, str(_ROOT))
_spec = importlib.util.spec_from_file_location("quorus_case_study_ledger",
                                               _ROOT / "quorus" / "ledger.py")
assert _spec is not None and _spec.loader is not None
ledger = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ledger  # dataclasses look their module up while defining
_spec.loader.exec_module(ledger)

NOTES_REF = "quorus"
REVERT = re.compile(r"This reverts commit ([0-9a-f]{7,40})")


def git(repo: Path, *args: str, check: bool = True) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and done.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {done.stderr.strip()[:300]}")
    return done.stdout


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def eligible(repo: Path, start: str, end: str) -> list[dict]:
    """Every non-merge commit in start..end, oldest first."""
    out = git(repo, "log", "--reverse", "--no-merges", "--format=%H%x09%cI%x09%s",
              f"{start}..{end}")
    rows = []
    for line in out.splitlines():
        sha, when, subject = (line.split("\t", 2) + ["", ""])[:3]
        rows.append({"sha": sha, "committed_at": when, "subject": subject})
    return rows


def notes_for(repo: Path, shas: list[str]) -> list[dict]:
    """The recorded provenance note of each commit that has one, as recorded."""
    listed = {}
    for line in git(repo, "notes", f"--ref={NOTES_REF}", "list", check=False).splitlines():
        parts = line.split()
        if len(parts) == 2:
            listed[parts[1]] = parts[0]
    rows = []
    for sha in shas:
        if sha in listed:
            raw = git(repo, "notes", f"--ref={NOTES_REF}", "show", sha, check=False)
            rows.append({"sha": sha, "note": raw.strip()})
    return rows


PICKED = re.compile(r"cherry picked from commit ([0-9a-f]{40})")
PUBLIC_NOTE_KEYS = ("agent", "vendor", "model", "published_at")


def _patch_id(repo: Path, sha: str) -> str | None:
    show = subprocess.run(["git", "-C", str(repo), "show", "--format=", "--no-color", sha],
                          capture_output=True).stdout
    out = subprocess.run(["git", "-C", str(repo), "patch-id", "--stable"], input=show,
                         capture_output=True).stdout.decode().split()
    return out[0] if out else None


def mapped_notes(repo: Path, shas: list[str], notes_repo: Path) -> list[dict]:
    """Notes for commits that reached *repo* under another sha (a release cherry-pick),
    read from *notes_repo*, where the notes were recorded. A commit matches its noted
    original by the same sha, by its `cherry picked from commit` trailer, or by a unique
    patch-id; anything else stays unattributed. `match` says which."""
    listed = [ln.split()[1] for ln in git(notes_repo, "notes", f"--ref={NOTES_REF}",
                                          "list", check=False).splitlines()
              if len(ln.split()) == 2]
    noted = {r["sha"]: r["note"] for r in notes_for(notes_repo, listed)}
    by_pid: dict[str, list[str]] = {}
    for orig in sorted(noted):
        pid = _patch_id(notes_repo, orig)
        if pid:
            by_pid.setdefault(pid, []).append(orig)
    rows = []
    for sha in shas:
        body = git(repo, "log", "-1", "--format=%B", sha)
        picked = [o for o in PICKED.findall(body) if o in noted]
        if sha in noted:
            match, orig = "same_sha", sha
        elif picked:
            match, orig = "cherry_pick", picked[-1]
        else:
            hits = by_pid.get(_patch_id(repo, sha) or "", [])
            if len(hits) != 1:
                continue
            match, orig = "patch_id", hits[0]
        rows.append({"sha": sha, "note": noted[orig], "match": match, "source_sha": orig})
    return rows


def scrub_note(raw: str) -> str:
    """Only who claimed the commit and when: no room names, no other fields."""
    try:
        note = json.loads(raw)
    except ValueError:
        return raw
    if not isinstance(note, dict):
        return raw
    return json.dumps({k: note[k] for k in PUBLIC_NOTE_KEYS if k in note}, sort_keys=True)


AGENT_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*-(?:claude|codex|opencode|gemini|cursor|cline)$")


def scrub_events(events: list[dict], to_public: dict[str, str]) -> list[dict]:
    """Agent ids stay; any other actor (a person) becomes "human". Targets that name a
    noted original are rewritten to the public commit they became."""
    out = []
    for e in events:
        targets = []
        for t in e.get("targets", []):
            hit = sorted(pub for orig, pub in to_public.items() if orig.startswith(t))
            targets.extend(hit or [t])
        out.append({**e, "actor": e["actor"] if AGENT_ID.match(e["actor"]) else "human",
                    "targets": sorted(set(targets))})
    return out


MSG = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d) (\S+) ?(.*)$")
TARGET = re.compile(r"\b[0-9a-f]{7,40}\b")


def review_events(history_text: str) -> tuple[list[dict], str]:
    """Typed review verbs from `quorus history` output: (events, observed window).

    The brief's parser decides (quorus/services/room_brief.py, Codex on 2f63a49d): a
    line counts only if it IS the command (`/disagree blocking|advisory`, `/vote <x>`,
    after an optional ✅ and @-mentions), outside code fences and quotes. A verb named
    mid-sentence ("Never /vote approve …", "One /disagree blocking remains") is not
    cast. A vote targets the sha right after it, as the brief reads an approval; a
    disagree targets the shas on its line. No message bodies are kept."""
    messages: list[tuple[str, str, list[str]]] = []
    for line in history_text.splitlines():
        m = MSG.match(line)
        if m:
            messages.append((m.group(1) + "Z", m.group(2), [m.group(3)]))
        elif messages:
            messages[-1][2].append(line)
    from quorus.services import room_brief  # freeze only: build never parses the room

    events = []
    for at, actor, body in messages:
        for line in room_brief.command_lines("\n".join(body)):
            m = room_brief.REVIEW_COMMAND.match(line)
            if not m:
                continue
            if m.group(1).startswith("vote"):
                targets = [m.group(2)] if m.group(2) else []
            else:
                targets = sorted(set(TARGET.findall(line)))
            events.append({"at": at, "actor": actor, "verb": m.group(1).replace(" ", "_"),
                           "targets": targets})
    window = f"{messages[0][0]}..{messages[-1][0]}" if messages else ""
    return events, window


def freeze(args: argparse.Namespace) -> None:
    repo, out = Path(args.repo), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    start = git(repo, "rev-parse", args.start).strip()
    end = git(repo, "rev-parse", args.end).strip()
    cutoff = datetime.fromisoformat(args.cutoff.replace("Z", "+00:00")).astimezone(
        timezone.utc).isoformat()
    commits = eligible(repo, start, end)
    shas = [c["sha"] for c in commits]
    notes = (mapped_notes(repo, shas, Path(args.notes_repo)) if args.notes_repo
             else notes_for(repo, shas))
    to_public = {n["source_sha"]: n["sha"] for n in notes if "source_sha" in n}
    if args.public:  # private shas and room names stay out of a published package
        notes = [{k: v for k, v in {**n, "note": scrub_note(n["note"])}.items()
                  if k != "source_sha"} for n in notes]
    dump_jsonl(out / "notes-export.jsonl", notes)
    window = args.review_window or ""
    if args.room_history:  # `quorus history <room> --limit N` output, as captured
        events, window = review_events(Path(args.room_history).read_text())
    else:
        events = load_jsonl(Path(args.review_events)) if args.review_events else []
    if args.public:
        events = scrub_events(events, to_public)
        for e in events:  # a target the public repo can't resolve is a private sha
            e["targets"] = [t for t in e["targets"] if git(
                repo, "rev-parse", "--verify", "--quiet", f"{t}^{{commit}}", check=False)]
    dump_jsonl(out / "review-events.jsonl", sorted(events, key=lambda e: (e["at"], e["actor"])))
    script = Path(__file__).resolve()
    manifest = {
        "start": start, "end": end, "cutoff_utc": cutoff,
        "review_window": window,
        "inputs": {name: sha256(out / name)
                   for name in ("notes-export.jsonl", "review-events.jsonl")},
        "extractor_sha256": sha256(script),
        "ledger_sha256": sha256(Path(ledger.__file__)),
        # The parser that produced review-events.jsonl (used by freeze only).
        "review_parser_sha256": _parser_sha256(),
        "public_scrub": bool(args.public), "notes_mapped": bool(args.notes_repo),
        "git_version": git(repo, "--version").strip(),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")


def _parser_sha256() -> str:
    from quorus.services import room_brief
    return sha256(Path(room_brief.__file__))


def verify(out: Path, script: Path) -> dict:
    manifest = json.loads((out / "manifest.json").read_text())
    for name, digest in manifest["inputs"].items():
        if sha256(out / name) != digest:
            raise SystemExit(f"refusing to build: {name} doesn't match manifest.json")
    if sha256(script) != manifest["extractor_sha256"]:
        raise SystemExit("refusing to build: this extractor isn't the one in manifest.json")
    if sha256(Path(ledger.__file__)) != manifest["ledger_sha256"]:
        raise SystemExit("refusing to build: quorus/ledger.py isn't the one in manifest.json")
    return manifest


def survival(repo: Path, sha: str, end: str) -> tuple[dict, list[str]]:
    """The ledger's survival at *end*, plus the paths the commit touched."""
    files, added, per_file = ledger._files_and_added(repo, sha)
    surviving, computed, status = ledger._surviving(repo, sha, per_file, end)
    return {"added": added, "added_in_computed_files": computed, "surviving": surviving,
            "coverage": round(computed / added, 4) if added else None,
            "files": dict(sorted(status.items()))}, files


def build(args: argparse.Namespace) -> None:
    repo, out = Path(args.repo), Path(args.out)
    manifest = verify(out, Path(__file__).resolve())
    start, end = manifest["start"], manifest["end"]
    cutoff = datetime.fromisoformat(manifest["cutoff_utc"])
    commits = eligible(repo, start, end)
    notes: dict[str, dict | None] = {}
    for row in load_jsonl(out / "notes-export.jsonl"):
        try:
            parsed = json.loads(row["note"])
        except ValueError:
            parsed = None
        notes[row["sha"]] = parsed if isinstance(parsed, dict) else None
    reverted: dict[str, list[str]] = {}
    for c in commits:
        for target in REVERT.findall(git(repo, "log", "-1", "--format=%B", c["sha"])):
            reverted.setdefault(target, []).append(c["sha"])
    events = load_jsonl(out / "review-events.jsonl")
    coverage, dataset = [], []
    for c in commits:
        note = notes.get(c["sha"])
        status = ("no_note" if c["sha"] not in notes
                  else "unparsable_note" if note is None or "published_at" not in note
                  else "noted" if note.get("model") else "noted_without_model")
        coverage.append({"sha": c["sha"], "committed_at": c["committed_at"],
                         "attribution": status})
        if status not in ("noted", "noted_without_model"):
            continue
        surv, paths = survival(repo, c["sha"], end)
        published = datetime.fromisoformat(note["published_at"])
        row = {
            "sha": c["sha"],
            # A recorded attribution claim: the publishing daemon wrote it; unverified.
            "claimed_agent": note.get("agent"), "claimed_vendor": note.get("vendor"),
            "claimed_model": note.get("model"), "published_at": note["published_at"],
            "age_hours_at_cutoff": round((cutoff - published).total_seconds() / 3600, 2),
            "tests_touched": any(p.startswith("tests/") or "/test_" in p or
                                 p.startswith("test_") for p in paths),
            "detected_explicit_revert_by": sorted(
                v for k, vs in reverted.items() if c["sha"].startswith(k) for v in vs),
            "review_events": [e for e in events
                              if any(c["sha"].startswith(t) for t in e.get("targets", []))],
            **surv,
        }
        dataset.append(row)
    dump_jsonl(out / "coverage.jsonl", coverage)
    dump_jsonl(out / "dataset.jsonl", dataset)


def _q(values: list[float]) -> str:
    if not values:
        return "n/a"
    v = sorted(values)

    def at(f: float) -> float:
        return v[min(len(v) - 1, int(f * (len(v) - 1) + 0.5))]
    return f"{at(0.25):.2f} / {at(0.5):.2f} / {at(0.75):.2f}"


def report(args: argparse.Namespace) -> None:
    """tables.md from the built files: per claimed agent and model, distributions only.
    Rows are sorted by name; nothing is ranked."""
    out = Path(args.out)
    manifest = json.loads((out / "manifest.json").read_text())
    coverage = load_jsonl(out / "coverage.jsonl")
    rows = load_jsonl(out / "dataset.jsonl")
    events = load_jsonl(out / "review-events.jsonl")
    counts: dict[str, int] = {}
    for c in coverage:
        counts[c["attribution"]] = counts.get(c["attribution"], 0) + 1
    lines = [f"Range `{manifest['start'][:12]}..{manifest['end'][:12]}`, cutoff "
             f"{manifest['cutoff_utc']}, review window {manifest['review_window'] or 'none'}.",
             "", "Attribution coverage of eligible commits:", ""]
    lines += [f"- {k}: {v}" for k, v in sorted(counts.items())]
    lines += ["", "| claimed agent | claimed model | commits | lines added | computed-line "
              "coverage | survival p25 / median / p75 | age h p25 / median / p75 | "
              "detected explicit reverts | tests touched |", "|" + "---|" * 9]
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault((r["claimed_agent"], r["claimed_model"] or "(none recorded)"),
                          []).append(r)
    for (agent, model), rs in sorted(groups.items()):
        added = sum(r["added"] for r in rs)
        counted = sum(r["added_in_computed_files"] for r in rs)
        surv = [r["surviving"] / r["added_in_computed_files"] for r in rs
                if r["added_in_computed_files"]]
        lines.append(
            f"| {agent} | {model} | {len(rs)} | {added} | "
            f"{counted / added:.3f} | {_q(surv)} | {_q([r['age_hours_at_cutoff'] for r in rs])}"
            f" | {sum(1 for r in rs if r['detected_explicit_revert_by'])} | "
            f"{sum(1 for r in rs if r['tests_touched'])} |" if added else
            f"| {agent} | {model} | {len(rs)} | 0 | n/a | n/a | n/a | 0 | 0 |")
    by_actor: dict[tuple, int] = {}
    for e in events:
        by_actor[(e["actor"], e["verb"])] = by_actor.get((e["actor"], e["verb"]), 0) + 1
    lines += ["", "Typed review events in the observed window (actor, verb, count):", ""]
    lines += [f"- {a}, {v}: {n}" for (a, v), n in sorted(by_actor.items())]
    (out / "tables.md").write_text("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    for a in ("--repo", "--start", "--end", "--cutoff", "--out"):
        f.add_argument(a, required=True)
    f.add_argument("--review-events")
    f.add_argument("--room-history", help="captured `quorus history` output to parse")
    f.add_argument("--review-window", help="the observed window, e.g. 'ISO..ISO'")
    f.add_argument("--notes-repo", help="read notes here and match commits that reached "
                   "--repo under another sha (cherry-pick trailer, patch-id)")
    f.add_argument("--public", action="store_true",
                   help="scrub for publishing: notes keep only agent/vendor/model/"
                        "published_at; people in review events become 'human'")
    b = sub.add_parser("build")
    b.add_argument("--repo", required=True)
    b.add_argument("--out", required=True)
    r = sub.add_parser("report")
    r.add_argument("--out", required=True)
    args = p.parse_args(argv)
    {"freeze": freeze, "build": build, "report": report}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
