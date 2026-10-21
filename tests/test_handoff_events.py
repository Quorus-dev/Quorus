"""Never Stuck slice 2c-3b: durable attempt events, recorded checks, and the report.

Dogfood plan §1/§4 (Codex GO on cfd69e70; Claude: events go through the journal's
fsynced write, so the report stays read-only).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from quorus.runtime import handoff_runtime, handoff_sender
from quorus.runtime.check_config import CheckConfig
from quorus.runtime.handoff_state import Journal, fingerprint
from tests.test_handoff_receiver import verify
from tests.test_handoff_retry import Candidates, policy
from tests.test_unfinished_wakes import reflexd

pytest_plugins = ["tests.test_handoff_delivery"]
ROOT = Path(__file__).resolve().parents[1]


def _events(daemon, wt) -> list[dict]:
    path = handoff_runtime.journal(daemon, "build", wt).events_path
    return [json.loads(x) for x in path.read_text().splitlines()]


def _attempt(daemon, relay, wt, cap):
    root = {"room": "build", "message_id": cap.root, "content": "finish"}
    return asyncio.run(handoff_sender.attempt(
        daemon, relay, root, reason="quota", mission={"task_id": "task"},
        worktree=wt, bindings=reflexd.ROOM_BINDINGS_PATH, **policy()))


def test_a_sent_handoff_records_attempt_capsule_and_send(wiring, monkeypatch):
    _, daemon, wt, cap, _, _ = wiring
    seen_before_check = []

    def snapshot(*a):
        seen_before_check.extend(e["event"] for e in _events(daemon, wt))
        return cap

    monkeypatch.setattr(handoff_sender, "snapshot", snapshot)
    assert _attempt(daemon, Candidates(), wt, cap) == "sent"
    assert seen_before_check == ["attempt"]  # on disk before the checkpoint runs
    events = _events(daemon, wt)
    assert [e["event"] for e in events] == ["attempt", "capsule_built", "sent"]
    assert len({e["attempt_id"] for e in events}) == 1
    built = events[1]
    assert built["fingerprint"] == fingerprint(cap)
    assert built["capsule_bytes"] == len(cap.model_dump_json().encode())
    assert built["check"]["argv"] == list(cap.check.argv)
    assert built["check"]["config_id"] == cap.check.config_id
    assert events[2]["envelope_bytes"] > built["capsule_bytes"]
    path = handoff_runtime.journal(daemon, "build", wt).events_path
    assert path.stat().st_mode & 0o777 == 0o600


def test_attempts_that_never_reach_a_capsule_are_counted(wiring, monkeypatch):
    _, daemon, wt, cap, _, _ = wiring

    class Nobody(Candidates):
        async def fetch_presence(self):
            return []

    _attempt(daemon, Nobody(), wt, cap)

    async def broken_send(*a, **k):
        raise RuntimeError("relay down, with secret detail")

    monkeypatch.setattr(handoff_sender, "snapshot", lambda *a: cap)
    monkeypatch.setattr(handoff_sender, "_send", broken_send)
    _attempt(daemon, Candidates(), wt, cap)
    failed = [e for e in _events(daemon, wt) if e["event"] == "attempt_failed"]
    assert [e["cause"] for e in failed] == ["no_target", "send_failed"]
    assert "secret" not in json.dumps(_events(daemon, wt))  # fixed causes, never text


def test_no_event_on_disk_means_no_checkpoint_and_no_send(wiring, monkeypatch):
    _, daemon, wt, cap, _, _ = wiring
    ran = []
    monkeypatch.setattr(handoff_sender, "snapshot", lambda *a: ran.append(1) or cap)

    def full(self, record):
        raise OSError("disk full")

    monkeypatch.setattr(Journal, "event", full)
    relay = Candidates()
    assert "could not complete" in _attempt(daemon, relay, wt, cap)
    assert ran == [] and relay.defers == []


@pytest.fixture
def roomy_case(tmp_path):
    """test_handoff_receiver's case, with a check timeout that survives a loaded
    machine: its 0.3 s timed out under the full suite (`timed_out` != `ran`)."""
    import os

    from quorus.runtime.checkpoint import run_checkpoint
    from quorus.runtime.handoff_capsule import build_capsule
    from tests.test_handoff_receiver import SOURCE

    root = tmp_path / "repo"
    root.mkdir()

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root).decode().strip()

    git("init", "-qb", "main")
    git("config", "user.name", "test")
    git("config", "user.email", "test@example.invalid")
    (root / "tracked").write_text("base")
    git("add", "tracked")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    git("branch", "quorus/receiver-codex")
    git("checkout", "-qb", "quorus/sender-claude")
    (root / "tracked").write_text("unfinished")
    git("commit", "-qam", "sender progress")
    head = git("rev-parse", "HEAD")
    config = CheckConfig((sys.executable, "-c", SOURCE), timeout_s=10)
    env = {"PATH": os.defpath}
    checkpoint = run_checkpoint(root, head, config, env=env)
    assert checkpoint["status"] == "ran"
    capsule = build_capsule(
        root="owner-request", hop=1, actor="sender-claude", task="finish the fix",
        repo={"main": "main", "base": base, "branch": "quorus/sender-claude", "head": head,
              "tree": git("rev-parse", "HEAD^{tree}"),
              "unfinished": "quorus/sender-claude-unfinished"},
        checkpoint=checkpoint, config=config,
        limit={"vendor": "claude", "reason": "quota", "kind": "guess",
               "source": "msg.no_time", "seen_at": "2026-10-21T14:00:00Z"},
        spent={"turns": 1, "wall_s": 10.0, "attempts": 1})
    git("checkout", "-q", "quorus/receiver-codex")
    journal = Journal(tmp_path / "runtime" / "handoffs.json", "room", root,
                      hold_dir=tmp_path / "runtime" / "unfinished-wakes")
    args = dict(branch="quorus/receiver-codex", integration="main", config=config,
                claimed=True, held=False, env=env)
    from types import SimpleNamespace

    return SimpleNamespace(root=root, capsule=capsule, config=config, journal=journal,
                           args=args)


def test_the_receiver_records_the_check_it_actually_ran(roomy_case):
    c = roomy_case
    verify(c)
    entry = c.journal.read(c.capsule)
    assert entry["check_ran"]["argv"] == list(c.config.argv)
    assert entry["check_ran"]["config_id"] == c.config.config_id
    assert entry["check_ran"]["wall_s"] is not None


# ── the read-only report ────────────────────────────────────────────────

CHECK = {"argv": ["pytest", "-q"], "cwd": ".", "timeout_s": 45.0, "max_output_bytes": 65536}
CID = CheckConfig.from_record(CHECK).config_id


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _repo(tmp_path: Path) -> tuple[Path, str, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-qb", "main")
    _git(repo, "config", "user.email", "t@x")
    _git(repo, "config", "user.name", "t")
    (repo / "a").write_text("1\n")
    _git(repo, "add", "a")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "a").write_text("2\n")
    _git(repo, "commit", "-qam", "starter work")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "revert", "--no-edit", "HEAD")  # receiver undoes...
    _git(repo, "cherry-pick", head)  # ...then redoes the starter's commit: a repeat
    done = _git(repo, "rev-parse", "HEAD")
    return repo, base, head, done


def _state(tmp_path: Path, name: str, bindings: dict) -> Path:
    state = tmp_path / name
    (state / "runtime").mkdir(parents=True)
    (state / "room-bindings.json").write_text(json.dumps(bindings))
    return state


def _report(*states: Path) -> str:
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "handoff_report.py"),
                          *[a for s in states for a in ("--state", str(s))]],
                         capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_the_report_joins_both_sides_and_keeps_rehearsals_apart(tmp_path):
    repo, base, head, done = _repo(tmp_path)
    binding = {"path": str(repo), "handoff": True, "check": CHECK}
    sender = _state(tmp_path, "claude", {"weekend-handoff": binding,
                                         "weekend-handoff-rehearsal": binding})
    receiver = _state(tmp_path, "codex", {"weekend-handoff": {**binding, "check": {
        **CHECK, "argv": ["pytest", "-x"]}}})
    seen = "2026-10-21T14:00:00+00:00"
    events = [
        {"event": "attempt", "attempt_id": "a1", "room": "weekend-handoff"},
        {"event": "capsule_built", "attempt_id": "a1", "room": "weekend-handoff",
         "fingerprint": "f1", "capsule_bytes": 2100, "seen_at": seen, "limit_kind": "observed",
         "check": {"argv": CHECK["argv"], "config_id": CID}},
        {"event": "sent", "attempt_id": "a1", "room": "weekend-handoff", "envelope_bytes": 2600},
        {"event": "attempt", "attempt_id": "a2", "room": "weekend-handoff"},
        {"event": "attempt_failed", "attempt_id": "a2", "room": "weekend-handoff",
         "cause": "no_target"},
        {"event": "attempt", "attempt_id": "r1", "room": "weekend-handoff-rehearsal"},
        {"event": "attempt_failed", "attempt_id": "r1", "room": "weekend-handoff-rehearsal",
         "cause": "send_failed"},
    ]
    (sender / "runtime" / "handoffs-s-events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events))
    (receiver / "runtime" / "handoffs-r.json").write_text(json.dumps({"k": {
        "fingerprint": "f1", "state": "done", "outcome": "reproduced", "id": "c1",
        "scope": f"weekend-handoff:{repo}", "check_finished": "2026-10-21T14:01:03.5+00:00",
        "check_ran": {"argv": CHECK["argv"], "config_id": CID}, "completed_head": done,
        "snapshot": {"repo": {"base": base, "head": head}}}}))
    out = _report(sender, receiver)
    real, _, rehearsal = out.partition("## rehearsal")
    assert "| a1 | weekend-handoff | reproduced | 63.5 | 2100 | 2600 | observed | 1 |" in real
    assert "repeated commits" in real
    assert "receiver check differs from binding" in real  # codex binding says -x
    assert "failed (no_target)" in real and "totals: failed 1, reproduced 1" in real
    assert "r1" not in real and "failed (send_failed)" in rehearsal


def test_the_report_writes_nothing(tmp_path):
    state = _state(tmp_path, "s", {})
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    assert "totals: none" in _report(state)
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before


@pytest.mark.parametrize("line", ["not json", '{"event": "attempt"}'])
def test_the_report_survives_odd_event_lines(tmp_path, line):
    state = _state(tmp_path, "s", {})
    (state / "runtime" / "handoffs-x-events.jsonl").write_text(line + "\n")
    _report(state)
