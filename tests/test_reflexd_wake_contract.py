"""Regression tests for the wake contract (live two-agent run, 2026-10-08).

Each test pins a failure observed with real claude 2.1.295 + codex 0.160.1
woken by reflexd against a local relay. See scripts/reflexd_wake.py.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("reflexd", _REPO_ROOT / "scripts" / "reflexd.py")
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd"] = reflexd
_spec.loader.exec_module(reflexd)
wake = reflexd.reflexd_wake

# Verbatim shape of `codex exec --json` on codex-cli 0.160.1.
CODEX_REAL_OUTPUT = "\n".join([
    '{"type":"thread.started","thread_id":"01a11e3c-e1b6-7fc2-9686-00460ca4d259"}',
    '{"type":"turn.started"}',
    '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"looking"}}',
    '{"type":"item.completed","item":{"id":"item_1","type":"command_execution","text":"ls"}}',
    '{"type":"item.completed","item":{"id":"item_2","type":"agent_message","text":"pong"}}',
    '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}',
])


def test_codex_parser_reads_real_agent_message_shape() -> None:
    text, tid = reflexd._parse_codex_stream(CODEX_REAL_OUTPUT)
    # The old parser returned "" here, so every codex reply became the
    # "[reflexd] (no reply)" sentinel in the room.
    assert text == "pong"  # last agent message, not narration or tool items
    assert tid == "01a11e3c-e1b6-7fc2-9686-00460ca4d259"


def test_codex_parser_legacy_delta_fallback() -> None:
    out = '{"thread_id":"t1"}\n{"delta":"he"}\n{"delta":"llo"}'
    assert reflexd._parse_codex_stream(out) == ("hello", "t1")


def test_agent_question_mark_does_not_wake_others() -> None:
    # Live: claude's reply ended "...post this for me?" and woke codex.
    res = reflexd.classify_message(
        content="couldn't reach the room. Can someone post this for me?",
        sender="qt-claude", self_name="qt-codex",
    )
    assert res.action == "IGNORE"
    human = reflexd.classify_message(
        content="can anyone check the build?", sender="arav", self_name="qt-codex",
    )
    assert human.action == "RESPOND"


def test_agent_mention_still_hands_off() -> None:
    res = reflexd.classify_message(
        content="Fixed in 31a3ce0. @qt-codex please review that commit.",
        sender="qt-claude", self_name="qt-codex",
    )
    assert res.action == "RESPOND" and res.kind == "mention"


def test_trailing_agent_chain_counts_until_human() -> None:
    hist = [{"from_name": n} for n in ("qt-claude", "arav", "qt-claude", "qt-codex", "qt-claude")]
    assert wake.trailing_agent_chain(hist, reflexd.is_agent_participant) == 3
    assert wake.trailing_agent_chain([], reflexd.is_agent_participant) == 0


def test_wake_flags_bind_identity_without_leaking_secret(tmp_path: Path) -> None:
    spec = wake.quorus_mcp_spec(
        relay_url="http://127.0.0.1:1", api_key="sk-SECRET", participant="qt-codex",
        legacy=False,
    )
    codex = wake.codex_wake_flags(spec)
    assert not any("sk-SECRET" in a for a in codex), "secret must never be in argv"
    assert any("QUORUS_INSTANCE_NAME" in a and "qt-codex" in a for a in codex)
    assert any("env_vars" in a and "QUORUS_API_KEY" in a for a in codex)

    cfg = wake.write_claude_mcp_config(spec, tmp_path / "m.json")
    claude = wake.claude_wake_flags(cfg)
    assert claude == ["--mcp-config", str(cfg)]
    assert (cfg.stat().st_mode & 0o777) == 0o600
    server = json.loads(cfg.read_text())["mcpServers"]["quorus"]
    assert server["env"]["QUORUS_API_KEY"] == "sk-SECRET"
    assert server["env"]["SSE_ENABLED"] == "false"

    env = wake.wake_env(spec)
    assert env["QUORUS_INSTANCE_NAME"] == "qt-codex"
    assert env["PATH"].split(":")[0] == str(Path(sys.executable).parent)


def test_codex_sandbox_is_owner_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wake, "CODEX_SANDBOX", "workspace-write")
    flags = wake.codex_wake_flags(None)
    assert flags[:2] == ["-c", 'sandbox_mode="workspace-write"']
    monkeypatch.setattr(wake, "CODEX_SANDBOX", "danger-full-access")
    assert not any("sandbox_mode" in f for f in wake.codex_wake_flags(None))


def test_wake_flags_do_not_widen_permissions_by_default() -> None:
    spec = wake.quorus_mcp_spec(relay_url="u", api_key="k", participant="a-claude", legacy=True)
    flags = " ".join(wake.codex_wake_flags(spec) + wake.claude_wake_flags(Path("/x")))
    for forbidden in ("--permission-mode", "--sandbox", "-s ", "bypass", "danger",
                      "--allowedTools", "--dangerously"):
        assert forbidden not in flags


def test_codex_flags_follow_resume_subcommand() -> None:
    # `codex exec resume` only applies options given to the subcommand.
    argv = reflexd.build_codex_argv("hi", resume="tid", extra=["-c", "x=1"])
    assert argv.index("resume") < argv.index("-c") < argv.index("tid") < argv.index("--")


def _daemon(tmp_path: Path, name: str = "qt-claude") -> Any:
    cfg = reflexd.ReflexdConfig(
        relay_url="http://test", api_key="k", participant_name=name,
        runtime_dir=tmp_path, log_path=tmp_path / "l.log",
    )
    return reflexd.Reflexd(cfg)


def test_prompt_lets_mentioned_agent_work(tmp_path: Path) -> None:
    d = _daemon(tmp_path)
    env = {"from_name": "arav", "room": "r", "content": "@qt-claude fix the bug"}
    tri = reflexd.classify_message(content=env["content"], sender="arav", self_name="qt-claude")
    p = d.build_prompt(env, [], triage=tri, teammates=["qt-claude", "qt-codex"])
    assert "do not run any tools" not in p.lower()
    assert "do the work now" in p.lower()
    assert "@qt-codex" in p  # teammates named for handoffs


def test_daemon_wires_wake_spec_into_adapter(tmp_path: Path) -> None:
    d = _daemon(tmp_path, "qt-codex")
    assert d.adapter.wake_spec["env"]["QUORUS_INSTANCE_NAME"] == "qt-codex"
    assert d.adapter.mcp_config_path.parent == tmp_path


def test_busy_agent_bids_low_on_open_work(tmp_path: Path) -> None:
    d = _daemon(tmp_path)
    bids: list[float] = []

    class Relay:
        async def submit_bid(self, **kw: Any) -> None:
            bids.append(kw["bid"])

        async def claim(self, **kw: Any) -> dict[str, Any]:
            return {"claimed": True, "winner": "someone-else"}

        async def post_social_defer(self, **kw: Any) -> None:
            return None

    env = {"from_name": "arav", "room": "r", "message_id": "m1",
           "content": "@open add a docs page", "message_type": "chat"}

    async def go() -> None:
        await d._wake_lock.acquire()  # simulate a wake in progress
        try:
            await d.handle_room_message(Relay(), env)
        finally:
            d._wake_lock.release()

    asyncio.run(go())
    assert bids and bids[0] < 0.3  # an idle teammate (>=0.3) must outbid us


def test_sse_dispatch_does_not_block_on_handler(tmp_path: Path) -> None:
    d = _daemon(tmp_path)
    gate = asyncio.Event()
    started: list[str] = []

    async def slow(relay: Any, data: dict[str, Any]) -> bool:
        started.append(data["id"])
        await gate.wait()
        return True

    d.handle_room_message = slow  # type: ignore[method-assign]

    async def go() -> None:
        for i in ("a", "b"):
            await asyncio.wait_for(
                d._dispatch_event(None, "message", {"id": i, "content": "x"}, background=True),
                timeout=0.5,
            )
        await asyncio.sleep(0)
        assert started == ["a", "b"]  # both running; reader never blocked
        gate.set()
        await asyncio.gather(*d._bg_tasks)

    asyncio.run(go())


@pytest.mark.parametrize("posted,reply,same", [
    ("✅ fixed add() in 31a3ce0", "✅ fixed add(); tests pass", True),
    ("plan: fix add()", "Fixed add() in 31a3ce0", False),
    ("Fixed add() in 31a3ce0", "Fixed  add() in 31a3ce0", True),
])
def test_same_result_dedupe(posted: str, reply: str, same: bool) -> None:
    assert reflexd._same_result(posted, reply) is same


def test_open_task_naming_a_reviewer_stays_open_work() -> None:
    msg = ("@open build a todo app with pytest tests. Commit, then ask "
           "@arav-codex to review the commit.")
    for agent in ("arav-codex", "arav-claude"):
        res = reflexd.classify_message(content=msg, sender="arav", self_name=agent)
        assert res.kind == "open_todo", agent
    bids = {
        h: reflexd.compute_bid_v2(
            kind="open_todo", role=None, description=msg,
            capabilities=reflexd.capabilities_for(f"arav-{h}"), recency_seconds=0.0,
        )[0]
        for h in ("claude", "codex")
    }
    assert bids["claude"] > bids["codex"]  # "tests" routes the build to claude


def test_wake_env_strips_daemon_secret_and_binds_agent_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Live: the daemon's API_KEY (a legacy relay secret) leaked into the
    # agent, so `quorus say` and the MCP server 401'd "Invalid API key format".
    monkeypatch.setenv("API_KEY", "daemon-secret")
    monkeypatch.setenv("RELAY_SECRET", "daemon-secret")
    d = wake.write_agent_config_dir(
        tmp_path / "a", relay_url="http://r", api_key="s3", participant="x-claude",
        legacy=True,
    )
    env = wake.wake_env(None, d)
    assert "API_KEY" not in env and "RELAY_SECRET" not in env
    assert env["QUORUS_CONFIG_DIR"] == str(d)
    prof = json.loads((d / "profiles" / "x-claude.json").read_text())
    assert prof == {"instance_name": "x-claude", "relay_url": "http://r", "relay_secret": "s3"}
    assert json.loads((d / "config.json").read_text())["current"] == "x-claude"
    assert (d / "profiles" / "x-claude.json").stat().st_mode & 0o777 == 0o600


def test_handled_ids_survive_restart(tmp_path: Path) -> None:
    d1 = _daemon(tmp_path)

    async def first() -> None:
        async def handler(relay: Any, data: dict[str, Any]) -> bool:
            return True
        d1.handle_room_message = handler  # type: ignore[method-assign]
        await d1._dispatch_event(None, "message", {"message_id": "m-1", "content": "x"})

    asyncio.run(first())
    d2 = _daemon(tmp_path)  # "restart": fresh process state, same runtime dir
    seen: list[str] = []

    async def second() -> None:
        async def handler(relay: Any, data: dict[str, Any]) -> bool:
            seen.append(data["message_id"])
            return True
        d2.handle_room_message = handler  # type: ignore[method-assign]
        await d2._dispatch_event(None, "message", {"message_id": "m-1", "content": "x"})
        await d2._dispatch_event(None, "message", {"message_id": "m-2", "content": "y"})

    asyncio.run(second())
    assert seen == ["m-2"]  # m-1 was handled before the restart


def test_agent_plan_line_naming_reviewer_does_not_wake_them() -> None:
    plan = "plan: add count command + tests; commit, then @arav-codex review."
    res = reflexd.classify_message(content=plan, sender="arav-claude", self_name="arav-codex")
    assert res.action == "IGNORE"
    # The real handoff (a result line) still wakes the reviewer.
    done = "✅ 1a2b3c count command, 9 tests pass. @arav-codex please review 1a2b3c."
    res = reflexd.classify_message(content=done, sender="arav-claude", self_name="arav-codex")
    assert res.action == "RESPOND"
    # A human typing "plan: ... @arav-codex" is still a request.
    res = reflexd.classify_message(content="plan: @arav-codex do x", sender="arav",
                                   self_name="arav-codex")
    assert res.action == "RESPOND"


def _git_repo(path: Path) -> Path:
    import subprocess as sp
    path.mkdir(parents=True)
    for cmd in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "init"]):
        sp.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *cmd], cwd=path, check=True)
    return path


def test_agent_worktree_created_once_and_never_reset(tmp_path: Path) -> None:
    import subprocess as sp
    repo = _git_repo(tmp_path / "proj")
    wt = wake.agent_worktree(repo, "a-claude")
    assert wt is not None and wt.branch == "quorus/a-claude" and wt.main == "main"
    assert wt.path == tmp_path / ".quorus-worktrees" / "proj" / "a-claude"
    # unpublished work on the agent branch must survive a lost worktree dir
    sp.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
            "--allow-empty", "-m", "wip"], cwd=wt.path, check=True)
    tip = sp.run(["git", "rev-parse", "HEAD"], cwd=wt.path, capture_output=True,
                 text=True).stdout.strip()
    sp.run(["rm", "-rf", str(wt.path)], check=True)
    again = wake.agent_worktree(repo, "a-claude")
    assert again is not None
    tip2 = sp.run(["git", "rev-parse", "HEAD"], cwd=again.path, capture_output=True,
                  text=True).stdout.strip()
    assert tip2 == tip
    assert wake.agent_worktree(repo, "a-claude") == again  # idempotent


def test_agent_worktree_skips_non_repos(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert wake.agent_worktree(plain, "a-claude") is None
    repo = _git_repo(tmp_path / "proj")
    sub = repo / "sub"
    sub.mkdir()
    assert wake.agent_worktree(sub, "a-claude") is None  # only the repo top


def test_worktree_prompt_and_codex_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _git_repo(tmp_path / "proj")
    wt = wake.agent_worktree(repo, "a-codex")
    text = wake.wake_instructions(participant="a-codex", room="r", sender="h",
                                  kind="open_todo", teammates=[], has_workspace=True,
                                  worktree=wt)
    assert str(wt.path) in text and "quorus/a-codex" in text
    assert "Do NOT run git rebase" in text  # codex's sandbox can't; daemon does it
    monkeypatch.setattr(wake, "CODEX_SANDBOX", "workspace-write")
    flags = wake.codex_wake_flags(None, [repo])
    assert any("writable_roots" in f and str(repo) in f for f in flags)
    monkeypatch.setattr(wake, "CODEX_SANDBOX", "")
    assert not any("writable_roots" in f for f in wake.codex_wake_flags(None, [repo]))


def test_publish_fast_forwards_only_when_safe(tmp_path: Path) -> None:
    import subprocess as sp
    g = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    repo = _git_repo(tmp_path / "proj")
    wt = wake.agent_worktree(repo, "a-claude")
    assert wake.publish_worktree(wt) is None  # nothing ahead
    (wt.path / "f.txt").write_text("x")
    sp.run([*g, "add", "f.txt"], cwd=wt.path, check=True)
    sp.run([*g, "commit", "-q", "-m", "f"], cwd=wt.path, check=True)
    (repo / "dirty.txt").write_text("d")
    sp.run([*g, "add", "dirty.txt"], cwd=repo, check=True)  # staged change in main
    assert "uncommitted" in wake.publish_worktree(wt)
    sp.run([*g, "reset", "-q"], cwd=repo, check=True)
    (repo / "dirty.txt").unlink()
    assert "published 1 commit" in wake.publish_worktree(wt)
    assert (repo / "f.txt").read_text() == "x"
    # main moves on: a stale agent branch must not publish
    other = wake.agent_worktree(repo, "b-codex")
    sp.run([*g, "rebase", "-q", "main"], cwd=other.path, check=True)
    sp.run([*g, "commit", "-q", "--allow-empty", "-m", "b"], cwd=other.path, check=True)
    assert "published" in wake.publish_worktree(other)
    sp.run([*g, "commit", "-q", "--allow-empty", "-m", "a2"], cwd=wt.path, check=True)
    # behind main: the daemon rebases (agents' sandboxes can't) and publishes
    assert "published 1 commit" in wake.publish_worktree(wt)
    log = sp.run(["git", "log", "--format=%s", "main"], cwd=repo, capture_output=True,
                 text=True).stdout.split()
    assert log[:3] == ["a2", "b", "f"]


def _conflicting_pair(tmp_path: Path) -> tuple[Any, Any, Path]:
    import subprocess as sp
    g = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    repo = _git_repo(tmp_path / "proj")
    a = wake.agent_worktree(repo, "a-claude")
    b = wake.agent_worktree(repo, "b-codex")
    for wt, body in ((a, "from a"), (b, "from b")):
        (wt.path / "same.txt").write_text(body)
        sp.run([*g, "add", "same.txt"], cwd=wt.path, check=True)
        sp.run([*g, "commit", "-q", "-m", body], cwd=wt.path, check=True)
    assert "published" in wake.publish_worktree(a)
    return a, b, repo


class _ResolvingAdapter:
    def __init__(self, resolve: bool) -> None:
        self.resolve, self.prompts = resolve, []

    async def run(self, harness: str, *, context: str, cwd: Path, **kw: Any) -> str:
        import subprocess as sp
        self.prompts.append(context)
        if self.resolve:
            (cwd / "same.txt").write_text("from a\nfrom b\n")
            g = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
            sp.run([*g, "add", "same.txt"], cwd=cwd, check=True)
            sp.run([*g, "commit", "-q", "--no-edit"], cwd=cwd, check=True)
            return "kept both lines in same.txt; tests green"
        return "could not resolve"


def _resolve(tmp_path: Path, b: Any, adapter: _ResolvingAdapter) -> str | None:
    d = _daemon(tmp_path / "rt", "b-codex")
    d.adapter = adapter
    return asyncio.run(d._publish_with_resolution(
        b, harness="codex", resume=None, on_session=lambda _s: None,
        timeout_s=None, max_turns=None, writable_root=None))


def test_conflict_wakes_agent_to_resolve_then_publishes(tmp_path: Path) -> None:
    _a, b, repo = _conflicting_pair(tmp_path)
    adapter = _ResolvingAdapter(resolve=True)
    status = _resolve(tmp_path, b, adapter)
    assert "conflict" in adapter.prompts[0] and str(b.path) in adapter.prompts[0]
    assert "resolved: kept both lines" in status and "published" in status
    assert (repo / "same.txt").read_text() == "from a\nfrom b\n"


def test_unresolved_conflict_aborts_and_keeps_commits(tmp_path: Path) -> None:
    import subprocess as sp
    _a, b, repo = _conflicting_pair(tmp_path)
    status = _resolve(tmp_path, b, _ResolvingAdapter(resolve=False))
    assert "still conflicts" in status
    state = sp.run(["git", "status"], cwd=b.path, capture_output=True, text=True).stdout
    assert "merging" not in state.lower()  # merge aborted cleanly
    assert (b.path / "same.txt").read_text() == "from b"  # agent's work kept
    assert (repo / "same.txt").read_text() == "from a"   # main untouched


def test_reply_chain_depth_counts_one_conversation() -> None:
    hist = [
        {"id": "h1", "from_name": "arav"},
        {"id": "a1", "from_name": "x-claude", "reply_to": "h1"},
        {"id": "b1", "from_name": "x-codex", "reply_to": "a1"},
        {"id": "h2", "from_name": "arav"},                       # unrelated task
        {"id": "a2", "from_name": "x-claude", "reply_to": "h2"},
    ]
    is_agent = reflexd.is_agent_participant
    env = {"from_name": "x-claude", "reply_to": "b1"}  # fix after review
    assert wake.reply_chain_agent_depth(env, hist, is_agent) == 3
    env2 = {"from_name": "x-codex", "reply_to": "a2"}  # other task: own chain
    assert wake.reply_chain_agent_depth(env2, hist, is_agent) == 2
    loop = [{"id": "c", "from_name": "x-claude", "reply_to": "c"}]
    assert wake.reply_chain_agent_depth({"from_name": "x-codex", "reply_to": "c"},
                                        loop, is_agent) == 2  # cycle-safe


def test_dedupe_keeps_handoff_and_publish_lines() -> None:
    reply = ("✅ ee39ddf add edit command, 65 tests pass\n"
             "@x-codex please review ee39ddf\n"
             "(quorus) published 1 commit(s) from quorus/x-claude to main")
    posted = ["✅ ee39ddf add edit command, 65 tests pass"]
    assert reflexd._undelivered_lines(reply, posted) == [
        "@x-codex please review ee39ddf",
        "(quorus) published 1 commit(s) from quorus/x-claude to main",
    ]
    assert reflexd._undelivered_lines(reply, posted + [reply]) == [
        "(quorus) published 1 commit(s) from quorus/x-claude to main",
    ]
    assert reflexd._undelivered_lines("✅ done", ["✅ done"]) == []


def _bid_for(tmp_path: Path, last_wake_ago: float | None) -> float:
    import time as _t
    d = _daemon(tmp_path, "x-codex")
    if last_wake_ago is not None:
        d._last_wake_at = _t.time() - last_wake_ago
    bids: list[float] = []

    class Relay:
        async def submit_bid(self, **kw: Any) -> None:
            bids.append(kw["bid"])

        async def claim(self, **kw: Any) -> dict[str, Any]:
            return {"claimed": True, "winner": "someone-else"}

        async def post_social_defer(self, **kw: Any) -> None:
            return None

    env = {"from_name": "arav", "room": "r", "message_id": "m",
           "content": "@open add a csv export", "message_type": "chat"}
    asyncio.run(d.handle_room_message(Relay(), env))
    return bids[0] if bids else 0.0


def test_idle_agent_still_bids_on_open_work_long_after_a_job(tmp_path: Path) -> None:
    # Live bug: the penalty grew with idle time, so after its first job an
    # agent bid 0 on every @open forever.
    assert _bid_for(tmp_path, None) >= 0.3
    assert _bid_for(tmp_path, 3600) >= 0.3
    assert _bid_for(tmp_path, 0.5) < _bid_for(tmp_path, 3600)  # just won: pays


def test_job_not_persisted_as_handled_until_it_finishes(tmp_path: Path) -> None:
    # A hard kill mid-job must leave the job redeliverable: the id may only
    # hit disk after handling completes.
    d = _daemon(tmp_path)
    path = tmp_path / "handled-qt-claude.json"
    during: list[bool] = []

    async def handler(relay: Any, data: dict[str, Any]) -> bool:
        on_disk = json.loads(path.read_text()) if path.exists() else []
        during.append("job-1" in on_disk)
        return True

    d.handle_room_message = handler  # type: ignore[method-assign]
    asyncio.run(d._dispatch_event(None, "message", {"message_id": "job-1", "content": "x"}))
    assert during == [False]
    assert "job-1" in json.loads(path.read_text())


def test_queue_or_defer_verb_naming_me_is_a_handoff() -> None:
    msg = "/queue @x-claude please review bc3dd4d"
    res = reflexd.classify_message(content=msg, sender="x-codex", self_name="x-claude")
    assert res.action == "RESPOND" and res.kind == "mention"
    other = reflexd.classify_message(content="/vote approve bc3dd4d @x-claude",
                                     sender="x-codex", self_name="x-claude")
    assert other.action == "IGNORE"
    not_me = reflexd.classify_message(content=msg, sender="x-codex", self_name="y-gemini")
    assert not_me.action == "IGNORE"


def test_startup_sweep_never_leaves_a_merge_in_progress(tmp_path: Path) -> None:
    import subprocess as sp
    _a, b, repo = _conflicting_pair(tmp_path)
    bindings = tmp_path / "room-bindings.json"
    bindings.write_text(json.dumps({"r1": str(repo), "r2": str(repo)}))
    d = _daemon(tmp_path / "rt", "b-codex")
    posts: list[str] = []

    class Relay:
        async def post_reply(self, **kw: Any) -> None:
            posts.append(kw["content"])

    reflexd.ROOM_BINDINGS_PATH, saved = bindings, reflexd.ROOM_BINDINGS_PATH
    try:
        asyncio.run(d._publish_sweep(Relay()))
    finally:
        reflexd.ROOM_BINDINGS_PATH = saved
    assert len(posts) == 1 and "next wake" in posts[0]  # one repo, swept once
    state = sp.run(["git", "status"], cwd=b.path, capture_output=True, text=True).stdout
    assert "merging" not in state.lower()


def test_sync_puts_clean_branch_on_latest_main(tmp_path: Path) -> None:
    import subprocess as sp
    g = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    repo = _git_repo(tmp_path / "proj")
    a = wake.agent_worktree(repo, "a-claude")
    sp.run([*g, "commit", "-q", "--allow-empty", "-m", "on main"], cwd=repo, check=True)
    wake.sync_worktree(a)
    head = sp.run(["git", "log", "-1", "--format=%s"], cwd=a.path, capture_output=True,
                  text=True).stdout.strip()
    assert head == "on main"
    (a.path / "wip.txt").write_text("x")  # dirty: leave it alone
    sp.run([*g, "commit", "-q", "--allow-empty", "-m", "main again"], cwd=repo, check=True)
    wake.sync_worktree(a)
    assert (a.path / "wip.txt").exists()
    assert "UNCOMMITTED" in wake.worktree_instructions(a, dirty=wake.worktree_dirty(a))


def test_daemon_commits_finished_but_uncommitted_work(tmp_path: Path) -> None:
    # Codex's sandbox blocks git: its finished work sat uncommitted forever.
    repo = _git_repo(tmp_path / "proj")
    wt = wake.agent_worktree(repo, "b-codex")
    (wt.path / "feature.txt").write_text("done")

    class NeverCalled:
        async def run(self, *a: Any, **k: Any) -> str:
            raise AssertionError("no extra wake needed")

    d = _daemon(tmp_path / "rt", "b-codex")
    d.adapter = NeverCalled()
    status = asyncio.run(d._publish_with_resolution(
        wt, harness="codex", resume=None, on_session=lambda _s: None, timeout_s=None,
        max_turns=None, writable_root=None,
        summary="✅ Added `feature.txt` with the done marker; tests green"))
    assert "published 1 commit" in status
    assert (repo / "feature.txt").read_text() == "done"
    import subprocess as sp
    log = sp.run(["git", "log", "-1", "--format=%an|%s", "main"], cwd=repo,
                 capture_output=True, text=True).stdout.strip()
    assert log == "b-codex|Added feature.txt with the done marker; tests green"


def test_commit_message_from_reply() -> None:
    assert wake.commit_message_from("✅ **Shipped** `x`", "a") == "Shipped x"
    assert wake.commit_message_from("", "a-codex") == "work by a-codex"

def test_cancelled_job_is_not_marked_handled(tmp_path: Path) -> None:
    d = _daemon(tmp_path)

    async def go() -> None:
        async def handler(relay: Any, data: dict[str, Any]) -> bool:
            await asyncio.sleep(3600)  # queued behind another job
            return True
        d.handle_room_message = handler  # type: ignore[method-assign]
        await d._dispatch_event(None, "message", {"message_id": "q-1", "content": "x"},
                                background=True)
        await asyncio.sleep(0)
        for task in list(d._bg_tasks):  # daemon stopping
            task.cancel()
        await asyncio.gather(*d._bg_tasks, return_exceptions=True)

    asyncio.run(go())
    restarted = _daemon(tmp_path)
    assert "q-1" not in restarted._handled_ids  # will be redelivered


def test_daemon_completes_merge_the_agent_resolved_but_could_not_commit(
    tmp_path: Path,
) -> None:
    _a, b, repo = _conflicting_pair(tmp_path)

    class EditOnly:  # Codex-like: may edit files, cannot touch git metadata
        async def run(self, harness: str, *, context: str, cwd: Path, **kw: Any) -> str:
            (cwd / "same.txt").write_text("from a\nfrom b\n")
            return "resolved same.txt, tests green"

    status = _resolve(tmp_path, b, EditOnly())
    assert "published" in status
    assert (repo / "same.txt").read_text() == "from a\nfrom b\n"


def test_finish_merge_refuses_leftover_markers(tmp_path: Path) -> None:
    _a, b, _repo = _conflicting_pair(tmp_path)
    assert wake.publish_worktree(b).startswith(wake.CONFLICT_MARK)
    assert wake.finish_merge(b, "b-codex") is False  # markers still there
    assert wake.merge_in_progress(b)
    wake.abort_merge(b)


def test_periodic_drain_refetches_while_idle_and_skips_while_busy(tmp_path: Path) -> None:
    d = _daemon(tmp_path)
    calls: list[str] = []

    async def fake_drain(relay: Any) -> None:
        calls.append("busy" if d._wake_lock.locked() else "idle")

    d._drain_inbox = fake_drain  # type: ignore[method-assign]

    async def go() -> None:
        task = asyncio.create_task(d._periodic_drain(None, interval=0.01))
        await asyncio.sleep(0.05)
        async with d._wake_lock:
            n = len(calls)
            await asyncio.sleep(0.05)
            assert len(calls) == n  # never drains mid-wake
        await asyncio.sleep(0.05)
        d.stop()
        await asyncio.wait_for(task, 1)

    asyncio.run(go())
    assert calls and set(calls) == {"idle"}


def test_recency_penalty_never_zeroes_open_work() -> None:
    bid, _ = reflexd.compute_bid_v2(kind="open_todo", role=None, description="add docs",
                                    capabilities=reflexd.CAPABILITIES_CODEX,
                                    recency_seconds=5.0)  # just finished a job
    assert bid > 0.0


def test_room_modes_map_to_harness_flags(tmp_path: Path) -> None:
    b = tmp_path / "b.json"
    b.write_text(json.dumps({"auto": {"path": str(tmp_path), "mode": "autonomous"},
                             "safe": {"path": str(tmp_path), "mode": "manual"},
                             "plain": str(tmp_path),
                             "bogus": {"path": str(tmp_path), "mode": "yolo"}}))
    assert reflexd.mode_for("auto", bindings_path=b) == "autonomous"
    assert reflexd.mode_for("safe", bindings_path=b) == "manual"
    assert reflexd.mode_for("plain", bindings_path=b) == "default"
    assert reflexd.mode_for("bogus", bindings_path=b) == "default"
    assert reflexd.workspace_for("auto", bindings_path=b) == tmp_path  # dict form

    manual = " ".join(wake.claude_wake_flags(None, "manual"))
    assert "--permission-prompt-tool mcp__quorus__approve" in manual  # asks owner
    assert "--permission-mode default" in manual  # beats an owner's "auto" default
    auto = wake.claude_wake_flags(None, "autonomous")
    assert "acceptEdits" in auto and not any("bypass" in f or "dangerous" in f for f in auto)
    assert wake.claude_wake_flags(None, "default") == []

    assert wake.codex_wake_flags(None, None, "manual")[:2] == ["-c", 'sandbox_mode="read-only"']
    assert wake.codex_wake_flags(None, None, "autonomous")[:2] == [
        "-c", 'sandbox_mode="workspace-write"']


@pytest.mark.parametrize("msg,action", [
    ("hello guys", "RESPOND"),                                    # live 2026-10-09
    ("please respond with hello if you guys are receiving this message", "RESPOND"),
    ("ok", "IGNORE"), ("thanks!", "IGNORE"), ("👍", "IGNORE"),     # acknowledgements
    ("fix the login bug in auth.py", "RESPOND"),                  # instruction
    ("can someone run the tests", "RESPOND"),
    ("just a status update", "IGNORE"),                           # statement
    ("i'm heading out for lunch", "IGNORE"),
    ("@aarya can you check the deploy", "IGNORE"),                # another human
    ("@arav-codex run the tests", "IGNORE"),                      # another agent
])
def test_human_talking_to_the_room_gets_an_answer(msg: str, action: str) -> None:
    res = reflexd.classify_message(content=msg, sender="arav", self_name="arav-claude")
    assert res.action == action, res.reason


def test_agent_chatter_still_needs_a_mention() -> None:
    res = reflexd.classify_message(content="hello guys", sender="arav-codex",
                                   self_name="arav-claude")
    assert res.action == "IGNORE"


def test_publish_rebases_on_a_machine_with_no_git_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CI runners and fresh servers have no user.email: the daemon's rebase
    # (needs a committer) failed and nothing ever published.
    import subprocess as sp
    g = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    repo = _git_repo(tmp_path / "proj")
    wt = wake.agent_worktree(repo, "a-claude")
    (wt.path / "f.txt").write_text("x")
    sp.run([*g, "add", "f.txt"], cwd=wt.path, check=True)
    sp.run([*g, "commit", "-q", "-m", "agent work"], cwd=wt.path, check=True)
    sp.run([*g, "commit", "-q", "--allow-empty", "-m", "main moved"], cwd=repo, check=True)
    for k, v in {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
                 "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.useConfigOnly",
                 "GIT_CONFIG_VALUE_0": "true"}.items():
        monkeypatch.setenv(k, v)
    for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
              "GIT_COMMITTER_EMAIL", "EMAIL"):
        monkeypatch.delenv(k, raising=False)
    assert "published 1 commit" in wake.publish_worktree(wt)


def test_manual_mode_tells_the_approve_tool_which_room(tmp_path: Path, monkeypatch) -> None:
    d = _daemon(tmp_path, "qa-claude")
    seen: dict[str, Any] = {}

    async def fake_sub(argv, *, parser, cwd=None, timeout_s=None, env=None):
        seen["argv"] = argv
        return parser('{"result": "ok", "session_id": "s"}')

    monkeypatch.setattr(d.adapter, "_run_subprocess", fake_sub)
    asyncio.run(d.adapter.run("claude", context="x", mode="manual", approval_room="r9"))
    cfg = json.loads(d.adapter.mcp_config_path.read_text())
    assert cfg["mcpServers"]["quorus"]["env"]["QUORUS_APPROVAL_ROOM"] == "r9"
    assert "mcp__quorus__approve" in seen["argv"]


def test_drain_never_acks_a_job_still_in_flight(tmp_path: Path) -> None:
    # A queued (in-flight) job acked by a drain was lost on the next restart.
    d = _daemon(tmp_path)
    d._inflight.add("job-q")
    acks: list[str] = []

    class Relay:
        def __init__(self) -> None:
            self.batches = [([{"message_id": "job-q", "content": "x"}], "tok-1")]

        async def fetch_inbox(self, **kw: Any):
            return self.batches.pop(0) if self.batches else ([], None)

        async def ack_inbox(self, **kw: Any) -> None:
            acks.append(kw["ack_token"])

    asyncio.run(d._drain_inbox(Relay()))
    assert acks == []  # stays on the relay; redelivered after a restart

    d2 = _daemon(tmp_path / "b")
    relay = Relay()
    relay.batches = [([{"message_id": "done-1", "content": "ok"}], "tok-2")]

    async def handled(r: Any, data: dict[str, Any]) -> bool:
        return False

    d2.handle_room_message = handled  # type: ignore[method-assign]

    async def two_passes() -> None:
        # pass 1 dispatches in the background (the reader is never blocked)
        # and leaves the batch unacked while the job runs ...
        await d2._drain_inbox(relay)
        await asyncio.gather(*d2._bg_tasks)
        assert acks == []
        # ... the next drain sees it handled and acks it.
        relay.batches = [([{"message_id": "done-1", "content": "ok"}], "tok-2")]
        await d2._drain_inbox(relay)

    asyncio.run(two_passes())
    assert acks == ["tok-2"]  # finished work is still acked


# ── 2026-10-09 code-review regressions ─────────────────────────────────────

def test_human_switching_branch_never_gets_main_published_into_it(tmp_path: Path) -> None:
    import subprocess as sp
    g = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    repo = _git_repo(tmp_path / "proj")
    wt = wake.agent_worktree(repo, "a-claude")  # records main as the target
    sp.run([*g, "checkout", "-q", "-b", "feature"], cwd=repo, check=True)
    (wt.path / "f.txt").write_text("agent work")
    sp.run([*g, "add", "f.txt"], cwd=wt.path, check=True)
    sp.run([*g, "commit", "-q", "-m", "agent"], cwd=wt.path, check=True)
    wt2 = wake.agent_worktree(repo, "a-claude")
    assert wt2.main == "main"                      # not the human's branch
    assert wake.publish_worktree(wt2) is None       # waits until they're back
    feature_log = sp.run(["git", "log", "--format=%s", "feature"], cwd=repo,
                         capture_output=True, text=True).stdout.split()
    assert "agent" not in feature_log


def test_staged_conflict_markers_are_never_committed(tmp_path: Path) -> None:
    import subprocess as sp
    _a, b, repo = _conflicting_pair(tmp_path)
    assert wake.publish_worktree(b).startswith(wake.CONFLICT_MARK)
    sp.run(["git", "add", "-A"], cwd=b.path, check=True)  # staged WITH markers
    assert wake.finish_merge(b, "b-codex") is False
    wake.abort_merge(b)
    (b.path / "x.txt").write_text("<<<<<<< HEAD\na\n=======\nb\n>>>>>>> main\n")
    assert wake.commit_all(b, "b-codex", "msg") is False
    assert "<<<<<<<" not in (repo / "same.txt").read_text()


def test_merge_stranded_by_a_restart_is_recovered(tmp_path: Path) -> None:
    _a, b, repo = _conflicting_pair(tmp_path)
    assert wake.publish_worktree(b).startswith(wake.CONFLICT_MARK)
    # daemon dies here; on the next publish the half-done merge is resolved
    # (agent fixed the file) or aborted — never left stuck forever
    (b.path / "same.txt").write_text("from a\nfrom b\n")
    status = wake.publish_worktree(b, "b-codex")
    assert status and "published" in status
    assert not wake.merge_in_progress(b)


def test_mcp_server_launch_ignores_the_repo_cwd() -> None:
    spec = wake.quorus_mcp_spec(relay_url="u", api_key="k", participant="a-claude",
                                legacy=True)
    assert spec["args"] == ["-I", "-m", "quorus_mcp.server"]  # isolated: no cwd on path
