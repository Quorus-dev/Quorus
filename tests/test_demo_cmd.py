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


def test_demo_shows_each_agents_step_once(monkeypatch) -> None:
    class C:
        out: list[str] = []

        def print(self, *a, **_k) -> None:
            self.out.append(" ".join(map(str, a)))

    class R:
        def __init__(self, data) -> None:
            self.data = data

        def json(self):
            return self.data

    beat = [{"name": demo_cmd.BUILDER, "activity": "editing calc.py"},
            {"name": "someone-else", "activity": "x"}]
    monkeypatch.setattr(demo_cmd.httpx, "get", lambda *a, **k: R(beat))
    c, last = C(), {}
    demo_cmd._show_steps(c, "http://r", {}, last, 0.0)
    demo_cmd._show_steps(c, "http://r", {}, last, 0.0)
    assert len(c.out) == 1 and f"@{demo_cmd.BUILDER} editing calc.py" in c.out[0]


def test_busy_free_models_fall_back_to_your_logged_in_claude_and_codex(monkeypatch,
                                                                      tmp_path: Path) -> None:
    """Release check: with every free model rate-limited the demo just failed."""
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    have = {"claude", "codex", "opencode"}
    which = lambda b: f"/bin/{b}" if b in have else None  # noqa: E731
    # codex isn't logged in yet: claude does both jobs
    assert demo_cmd.fallback_cast(which) == [("claude", None), ("claude", None)]
    (codex_home / "auth.json").write_text("{}")
    # both: Claude builds, Codex reviews (two vendors, as in the real product)
    assert demo_cmd.fallback_cast(which) == [("claude", None), ("codex", None)]
    have = {"opencode"}
    assert demo_cmd.fallback_cast(which) == []  # nothing to fall back to: the old message


def test_fallback_agents_keep_their_login_through_links(tmp_path: Path) -> None:
    real, demo_home = tmp_path / "real", tmp_path / "demo"
    (real / ".claude").mkdir(parents=True)
    (real / ".claude.json").write_text("{}")
    (real / ".codex").mkdir()
    demo_home.mkdir()
    demo_cmd.link_auth(demo_home, {"claude"}, real_home=real)
    assert (demo_home / ".claude").resolve() == (real / ".claude").resolve()
    assert (demo_home / ".claude.json").is_symlink() and not (demo_home / ".codex").exists()
    demo_cmd.link_auth(demo_home, {"claude", "codex"}, real_home=real)  # idempotent
    assert (demo_home / ".codex").is_symlink()
    import shutil

    shutil.rmtree(demo_home)  # cleanup removes the links, never what they point to
    assert (real / ".claude").is_dir() and (real / ".claude.json").exists()


def test_the_task_and_verdict_follow_whoever_is_cast() -> None:
    task = demo_cmd.task_for("demo-claude", "demo-review-codex")
    assert task.startswith("@demo-claude ") and "@demo-review-codex" not in task
    pub = _m("demo-claude", "(quorus) published 1 commit(s) from quorus/demo-claude to main")
    verdict = _m("demo-review-codex", "Reviewed abc123: correct, test green.")
    assert demo_cmd.review_of([pub, verdict], "demo-review-codex").startswith("Reviewed")
    assert demo_cmd.review_of([pub, verdict]) is None  # not the opencode reviewer
