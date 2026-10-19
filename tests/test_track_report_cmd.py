"""`quorus track-report` reads loop A's shadow logs and prints the agreed numbers.

Read-only: it must never write a log or change a bid, and it says so when there
are too few turns to judge."""

from __future__ import annotations

import json
import sys
import time

import pytest
from quorus_cli import track_report_cmd as tr


def _logs(runtime, name: str, *, n: int, fail_low: bool) -> None:
    now = time.time()
    bids, turns = [], []
    for i in range(n):
        low = i % 2 == 0
        bids.append({"ts": now - 100, "prediction_id": f"p{i}", "model": "m", "arm": "shadow",
                     "multiplier": 0.5 if low else 1.2, "paths": ["quorus/"],
                     "path_kept": 4, "path_reverted": 0, "path_factor": 0.6 if low else 1.3})
        turns.append({"ts": now - 50, "started": now - 90, "kind": "open_todo",
                      "prediction_id": f"p{i}", "model": "m",
                      "outcome": "errored" if (low and fail_low) else "clean"})
    turns.append({"ts": now - 10, "started": now - 20, "kind": "open_todo",
                  "prediction_id": "nobid", "model": "m", "outcome": "clean"})
    (runtime / f"track-bids-{name}.json").write_text(json.dumps(bids))
    (runtime / f"track-turns-{name}.json").write_text(json.dumps(turns))


def _run(monkeypatch, capsys, *argv: str) -> str:
    from quorus_cli.cli import main

    monkeypatch.setattr(sys, "argv", ["quorus", "track-report", *argv])
    with pytest.raises(SystemExit) as done:
        main()
    assert done.value.code == 0
    return capsys.readouterr().out


def test_report_shows_arms_prediction_and_unmatched(tmp_path, monkeypatch, capsys) -> None:
    _logs(tmp_path, "arav-claude", n=12, fail_low=True)
    out = _run(monkeypatch, capsys, "--runtime-dir", str(tmp_path))
    assert "arav-claude: @open turns, last 24h (12 matched, 1 unmatched)" in out
    assert "shadow   12 turns, 50% failed" in out
    assert "multiplier <1.0: 6 turns, 100% failed; >=1.0: 6 turns, 0% failed" in out
    assert "path record <1.0: 6 turns, 100% failed" in out
    assert "too few" not in out


def test_few_turns_are_called_too_few(tmp_path, monkeypatch, capsys) -> None:
    _logs(tmp_path, "arav-codex", n=3, fail_low=False)
    out = _run(monkeypatch, capsys, "--runtime-dir", str(tmp_path), "--agent", "arav-codex")
    assert "too few to say yet" in out


def test_json_is_the_report_per_agent(tmp_path, monkeypatch, capsys) -> None:
    _logs(tmp_path, "a", n=2, fail_low=False)
    _logs(tmp_path, "b", n=2, fail_low=False)
    data = json.loads(_run(monkeypatch, capsys, "--runtime-dir", str(tmp_path), "--json"))
    assert sorted(data) == ["a", "b"]
    assert data["a"]["unknown"] == 1 and data["a"]["arms"]["shadow"]["turns"] == 2


def test_no_logs_says_so_and_writes_nothing(tmp_path, monkeypatch, capsys) -> None:
    out = _run(monkeypatch, capsys, "--runtime-dir", str(tmp_path))
    assert "Nothing to report yet" in out and "@open <task>" in out
    assert list(tmp_path.iterdir()) == []


def test_reading_never_rewrites_the_logs(tmp_path, monkeypatch, capsys) -> None:
    _logs(tmp_path, "arav-claude", n=4, fail_low=False)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    _run(monkeypatch, capsys, "--runtime-dir", str(tmp_path))
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_listed_in_grouped_help(capsys) -> None:
    from quorus_cli.cli import _print_grouped_help

    _print_grouped_help()
    assert "track-report" in capsys.readouterr().out
    assert tr.agents(tr.DEFAULT_RUNTIME_DIR.parent / "no-such-dir") == []
