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
    monkeypatch.setattr(demo_cmd, "model_answers", lambda m, cwd: 3.0 if m in up else None)
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


def test_busy_free_models_fall_back_to_your_logged_in_claude_and_codex() -> None:
    """Release check: with every free model rate-limited the demo just failed."""
    have, logged = {"claude", "codex", "opencode"}, {"claude"}
    which = lambda b: f"/bin/{b}" if b in have else None  # noqa: E731
    ok = lambda h: h in logged  # noqa: E731
    # codex installed but not logged in: claude does both jobs
    assert demo_cmd.fallback_cast(which, ok) == [("claude", None), ("claude", None)]
    logged.add("codex")
    # both: Claude builds, Codex reviews (two vendors, as in the real product)
    assert demo_cmd.fallback_cast(which, ok) == [("claude", None), ("codex", None)]
    logged.clear()  # codex on 62eface2: installed is not logged in
    assert demo_cmd.fallback_cast(which, ok) == []
    have = {"opencode"}
    logged.update({"claude", "codex"})
    assert demo_cmd.fallback_cast(which, ok) == []  # nothing installed: the old message


def test_login_check_asks_the_cli_itself(monkeypatch) -> None:
    import subprocess as sp

    seen: list = []

    def run(argv, **_kw):
        seen.append(argv)
        return sp.CompletedProcess(argv, 0 if argv[0] == "codex" else 1)

    monkeypatch.setattr(demo_cmd.subprocess, "run", run)
    assert demo_cmd.logged_in("codex") and not demo_cmd.logged_in("claude")
    assert seen == [["codex", "login", "status"], ["claude", "auth", "status"]]


def test_fallback_shares_only_the_login_not_the_cli_state(tmp_path: Path,
                                                         monkeypatch) -> None:
    """Codex on 62eface2: whole-directory links let a config write in the demo
    reach the real HOME. Only the credential stores are linked now."""
    monkeypatch.delenv("CODEX_HOME", raising=False)
    real, demo_home = tmp_path / "real", tmp_path / "demo"
    (real / "Library" / "Keychains").mkdir(parents=True)
    (real / ".claude").mkdir()
    (real / ".claude" / ".credentials.json").write_text("{}")
    (real / ".claude" / "settings.json").write_text("REAL")
    (real / ".claude.json").write_text("REAL")
    (real / ".codex").mkdir()
    (real / ".codex" / "auth.json").write_text("{}")
    (real / ".codex" / "config.toml").write_text("REAL")
    demo_home.mkdir()
    demo_cmd.share_login(demo_home, {"claude", "codex"}, real_home=real)
    assert (demo_home / "Library" / "Keychains").is_symlink()
    assert (demo_home / ".codex" / "auth.json").is_symlink()
    assert (demo_home / ".claude" / ".credentials.json").is_symlink()
    for d in (demo_home / ".claude", demo_home / ".codex"):
        assert d.is_dir() and not d.is_symlink()  # real folders inside the demo
    # what the CLIs write while the demo runs stays in the demo
    (demo_home / ".codex" / "config.toml").write_text("DEMO")
    (demo_home / ".claude" / "settings.json").write_text("DEMO")
    (demo_home / ".claude.json").write_text("DEMO")
    assert (real / ".codex" / "config.toml").read_text() == "REAL"
    assert (real / ".claude" / "settings.json").read_text() == "REAL"
    assert (real / ".claude.json").read_text() == "REAL"
    demo_cmd.share_login(demo_home, {"claude", "codex"}, real_home=real)  # idempotent
    import shutil

    shutil.rmtree(demo_home)  # cleanup removes the links, never what they point to
    assert (real / ".codex" / "auth.json").exists() and (real / "Library" / "Keychains").is_dir()


def test_codex_home_points_at_the_users_auth(tmp_path: Path, monkeypatch) -> None:
    custom = tmp_path / "codexhome"
    custom.mkdir()
    (custom / "auth.json").write_text("{}")
    monkeypatch.setenv("CODEX_HOME", str(custom))
    demo_home = tmp_path / "demo"
    demo_home.mkdir()
    demo_cmd.share_login(demo_home, {"codex"}, real_home=tmp_path / "nohome")
    assert (demo_home / ".codex" / "auth.json").resolve() == (custom / "auth.json").resolve()


def test_the_task_and_verdict_follow_whoever_is_cast() -> None:
    task = demo_cmd.task_for("demo-claude", "demo-review-codex")
    assert task.startswith("@demo-claude ") and "@demo-review-codex" not in task
    pub = _m("demo-claude", "(quorus) published 1 commit(s) from quorus/demo-claude to main")
    verdict = _m("demo-review-codex", "Reviewed abc123: correct, test green.")
    assert demo_cmd.review_of([pub, verdict], "demo-review-codex").startswith("Reviewed")
    assert demo_cmd.review_of([pub, verdict]) is None  # not the opencode reviewer


def test_the_fastest_free_model_builds_and_slow_ones_are_skipped(monkeypatch,
                                                                tmp_path: Path) -> None:
    """Release check: the nemotron reviewer sat 216s between its first steps."""
    from quorus.runtime import opencode_free

    class _Quiet:
        def print(self, *_a, **_k) -> None:
            pass

    speed = {"opencode/deepseek-v4.1-flash-free": None,  # over 20s: skipped
             "opencode/longcat-2.5-preview-free": 9.0,
             "opencode/mimo-v2.6-flash-free": 1.2}
    monkeypatch.setattr(demo_cmd, "model_answers", lambda m, cwd: speed.get(m))
    assert demo_cmd.pick_models(tmp_path, _Quiet()) == ["opencode/mimo-v2.6-flash-free",
                                                       "opencode/longcat-2.5-preview-free"]
    assert opencode_free.is_slow("opencode/deepseek-v4.1-flash-free")


def test_a_probe_slower_than_the_limit_counts_as_no_answer(monkeypatch, tmp_path: Path) -> None:
    import subprocess as sp

    def run(argv, **kw):
        assert kw["timeout"] == demo_cmd.PROBE_MAX_S
        raise sp.TimeoutExpired(argv, kw["timeout"])

    monkeypatch.setattr(demo_cmd.subprocess, "run", run)
    assert demo_cmd.model_answers("opencode/nemotron-3.5-lightning-free", tmp_path) is None


def test_closing_hint_offers_only_tested_wakes():
    """Stranger pass of 0.6.2: the hint still offered gemini, which needs an AI Studio
    API key; the README calls it a note, not a tested wake."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "packages/cli/quorus_cli/demo_cmd.py").read_text()
    assert "quorus agent add claude|codex|opencode --room general" in src
    assert "claude|codex|gemini|opencode" not in src
