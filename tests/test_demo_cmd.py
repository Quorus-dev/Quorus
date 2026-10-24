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


def test_an_approval_request_is_never_the_verdict() -> None:
    """0.6.6 stranger pass: the reviewer asked for `bash`, and the demo's closing
    line read "Second agent's review: 🔐 approval needed: `bash` …"."""
    pub = _m(demo_cmd.BUILDER, "(quorus) published 1 commit(s) from quorus/demo to main")
    ask = _m(demo_cmd.REVIEWER, "🔐 approval needed: `bash` (details: `quorus approvals`)\n"
                                "approve with `quorus approve apr_1` or deny with `quorus deny`")
    assert demo_cmd.review_of([pub, ask]) is None
    verdict = _m(demo_cmd.REVIEWER, "Reviewed ce36508 by reading the diff: correct fix.")
    assert demo_cmd.review_of([pub, ask, verdict]).startswith("Reviewed ce36508")


def test_demo_agents_cannot_block_on_an_approval(tmp_path: Path) -> None:
    """The demo room has no human who can approve, so every agent's environment
    tells the approve tool to deny at once (see phase1_tools.approve)."""
    env = demo_cmd.agent_env({"PATH": "/bin"}, home=tmp_path / "home", root=tmp_path,
                             url="http://127.0.0.1:1", secret="s", name=demo_cmd.REVIEWER,
                             model=None)
    assert env["QUORUS_APPROVALS"] == "deny"
    assert env["REFLEXD_PARTICIPANT"] == demo_cmd.REVIEWER and "REFLEXD_MODEL" not in env
    with_model = demo_cmd.agent_env({}, home=tmp_path, root=tmp_path, url="u", secret="s",
                                    name=demo_cmd.BUILDER, model="opencode/x-free")
    assert with_model["REFLEXD_MODEL"] == "opencode/x-free"


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


_LIMIT_429 = ('{"type":"error","error":{"type":"FreeUsageLimitError","message":"Free usage '
              'limit reached. Subscribe to OpenCode Go to keep going: https://opencode.ai/go"}}')


def test_a_probe_tells_a_free_tier_limit_from_a_slow_model(monkeypatch, tmp_path: Path) -> None:
    """keyless-answer.md §3.5: the limit is typed evidence (an error event), never a
    timeout or a reply that happens to mention quotas."""
    import subprocess as sp

    outputs = {"limited": _LIMIT_429,
               "pong": '{"type":"text","part":{"text":"pong","messageID":"m"}}',
               "chatty": '{"type":"text","part":{"text":"the quota is fine","messageID":"m"}}',
               "older": '{"type":"error","error":{"name":"APIError","data":{"message":'
                        '"Rate limit exceeded. Please try again later."}}}'}

    def run(argv, **_kw):
        model = argv[argv.index("-m") + 1]
        return sp.CompletedProcess(argv, 1, stdout=outputs[model.split("/")[-1]], stderr="")

    monkeypatch.setattr(demo_cmd.subprocess, "run", run)
    assert isinstance(demo_cmd.model_answers("x/limited", tmp_path), demo_cmd.FreeTierLimited)
    assert isinstance(demo_cmd.model_answers("x/older", tmp_path), demo_cmd.FreeTierLimited)
    assert isinstance(demo_cmd.model_answers("x/pong", tmp_path), float)
    assert demo_cmd.model_answers("x/chatty", tmp_path) is None  # not pong, not a limit


def test_a_limit_followed_by_the_clis_own_retry_countdown_is_still_a_limit(
        monkeypatch, tmp_path: Path) -> None:
    """Codex on 94a791db: opencode retries a 429 itself ("retrying in 6h 56m"), so the
    probe times out with the limit event already in its partial output. That is a
    limit, not a slow model, and must be read from TimeoutExpired.output."""
    import subprocess as sp

    partial = _LIMIT_429 + '\n{"type":"status","text":"retrying in 6h 56m attempt #1"}\n'

    def run(argv, **kw):
        out = partial if argv[argv.index("-m") + 1].endswith("limited") else ""
        raise sp.TimeoutExpired(argv, kw["timeout"], output=out.encode() if "bytes" in argv
                                else out)

    monkeypatch.setattr(demo_cmd.subprocess, "run", run)
    assert isinstance(demo_cmd.model_answers("x/limited", tmp_path), demo_cmd.FreeTierLimited)
    assert demo_cmd.model_answers("x/silent", tmp_path) is None  # a plain timeout stays slow


def test_a_free_tier_limit_stops_the_probes_and_marks_nothing_slow(monkeypatch,
                                                                     tmp_path: Path) -> None:
    """Claude on 339b0e10: under the shared quota one demo run probed every free
    model and recorded the limit as "slow" for an hour."""
    from quorus.runtime import opencode_free

    class _C:
        out: list[str] = []

        def print(self, *a, **_k) -> None:
            self.out.append(" ".join(map(str, a)))

    import subprocess as sp

    opencode_free.reset_cache()
    monkeypatch.setattr(opencode_free, "_default_run", lambda argv, **_k: sp.CompletedProcess(
        argv, 0, stdout="\n".join(demo_cmd.FREE_MODELS), stderr=""))
    probed: list[str] = []

    def answers(model, cwd):
        probed.append(model)
        return demo_cmd.FreeTierLimited("FreeUsageLimitError Free usage limit reached.")

    monkeypatch.setattr(demo_cmd, "model_answers", answers)
    try:
        assert demo_cmd.pick_models(tmp_path, _C()) == []
        assert probed == [demo_cmd.FREE_MODELS[0]]  # one probe, not one per free model
        assert not any(opencode_free.is_slow(m) for m in demo_cmd.FREE_MODELS)
        assert any("free tier reported its limit" in line for line in _C.out)
    finally:
        opencode_free.reset_cache()


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


def test_no_usable_model_exits_nonzero(monkeypatch, tmp_path) -> None:
    """Free models busy and no logged-in Claude Code or Codex: the demo didn't run, so
    it must not exit 0 (stranger-path test of 0.7.1, 2026-10-24)."""
    import types

    import pytest

    monkeypatch.setattr(demo_cmd, "_opencode_ok", lambda: None)
    monkeypatch.setattr(demo_cmd, "pick_models", lambda *a, **k: [])
    monkeypatch.setattr(demo_cmd, "fallback_cast", lambda *a, **k: [])
    monkeypatch.setattr(demo_cmd.tempfile, "mkdtemp", lambda prefix="": str(tmp_path / "demo"))
    said = []
    console = types.SimpleNamespace(print=lambda *a, **k: said.append(" ".join(map(str, a))))
    args = types.SimpleNamespace(model=None, timeout=1, keep=False)
    with pytest.raises(SystemExit) as exc:
        demo_cmd.cmd_demo(args, console)
    assert exc.value.code == 1
    assert any("all busy" in line for line in said)
    assert not (tmp_path / "demo").exists()  # the throwaway folder is still removed
