"""`quorus demo`: two free agents fix a planted bug, no keys (2026-10-17)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from quorus_cli import demo_cmd


def test_sample_repo_starts_broken_and_detects_the_fix(tmp_path: Path) -> None:
    repo = demo_cmd.make_sample_repo(tmp_path)
    assert not demo_cmd.is_fixed(repo)
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    subprocess.run(["git", "-c", "user.name=a", "-c", "user.email=a@x", "commit", "-qam", "fix"],
                   cwd=repo, check=True)
    assert demo_cmd.is_fixed(repo)


def test_binding_lets_the_human_in_the_room_drive_the_agents(tmp_path: Path) -> None:
    import json

    demo_cmd.write_binding(tmp_path, tmp_path / "repo")
    entry = json.loads((tmp_path / ".quorus" / "room-bindings.json").read_text())["demo"]
    assert entry["mode"] == "autonomous" and entry["trust"] == "members"


def _m(who: str, body: str, kind: str = "chat") -> dict:
    return {"from_name": who, "content": body, "message_type": kind}


def test_a_plan_line_is_not_a_review() -> None:
    pub = _m(demo_cmd.BUILDER, "✅ fixed\n(quorus) published 1 commit(s) from quorus/demo to main")
    assert demo_cmd.review_of([pub, _m(demo_cmd.REVIEWER, "plan: reviewing ce36508")]) is None
    verdict = _m(demo_cmd.REVIEWER, "Reviewed ce36508: correct one-line fix, test green.")
    assert demo_cmd.review_of([pub, verdict]).startswith("Reviewed ce36508")
    assert demo_cmd.review_of([verdict]) is None  # nothing published yet
    early = _m(demo_cmd.REVIEWER, "Nothing to review yet; standing by")
    assert demo_cmd.review_of([pub, early]) is None


def test_task_names_the_reviewer_without_waking_it() -> None:
    import re

    mentioned = set(re.findall(r"(?<![\w-])@([A-Za-z][\w-]*)", demo_cmd.TASK))
    assert demo_cmd.BUILDER in mentioned and demo_cmd.REVIEWER not in mentioned


def test_stops_early_when_every_agent_is_out_of_quota() -> None:
    msgs = [_m(demo_cmd.BUILDER, "@demo-opencode is out of quota for about 30 min", "status")]
    assert not demo_cmd.all_out_of_quota(msgs)
    msgs.append(_m(demo_cmd.REVIEWER, "@demo-review-opencode is out of quota for about 30 min",
                   "status"))
    assert demo_cmd.all_out_of_quota(msgs)


def test_picks_the_free_models_that_answer(monkeypatch, tmp_path: Path) -> None:
    class _C:
        def print(self, *_a, **_k) -> None:
            pass

    up = {"opencode/longcat-2.5-preview-free"}
    monkeypatch.setattr(demo_cmd, "model_answers", lambda m, cwd: m in up)
    assert demo_cmd.pick_models(tmp_path, _C()) == ["opencode/longcat-2.5-preview-free"] * 2
    up.add("opencode/mimo-v2.6-flash-free")
    assert demo_cmd.pick_models(tmp_path, _C()) == ["opencode/longcat-2.5-preview-free",
                                                    "opencode/mimo-v2.6-flash-free"]
    up.clear()
    assert demo_cmd.pick_models(tmp_path, _C()) == []
    assert demo_cmd.pick_models(tmp_path, _C(), "x/y") == ["x/y", "x/y"]
