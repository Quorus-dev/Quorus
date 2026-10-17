"""`quorus demo`: watch two AI agents fix a bug together, with no accounts or keys.

Trying Quorus used to need Claude Code or Codex accounts, so most visitors left
before they saw anything happen (weekend research, 2026-10-17). The demo runs everything
in a throwaway folder: a sample repo with a planted bug, a private relay, and
two agents on a free open-source model through opencode. It posts one message,
streams the room live, and cleans up afterwards. Nothing in your own Quorus setup
is touched.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

# opencode's free open-source models. Their limits vary hour to hour (night 1:
# DeepSeek and MiMo rate-limited while LongCat answered), so probe and use the
# first two that respond: one builds, the other reviews.
FREE_MODELS = ("opencode/deepseek-v4.1-flash-free", "opencode/longcat-2.5-preview-free",
               "opencode/mimo-v2.6-flash-free")
BUILDER, REVIEWER, HUMAN, ROOM = "demo-opencode", "demo-review-opencode", "you", "demo"
# The reviewer is named WITHOUT an @: mentioning it here woke it early to say
# "nothing to review yet" (live run, 2026-10-17). The builder's hand-off wakes it.


def task_for(builder: str, reviewer: str) -> str:
    return (f"@{builder} add() in calc.py is broken: fix it, run the test, commit, then "
            f"@-mention your reviewer ({reviewer}) to review your commit.")


TASK = task_for(BUILDER, REVIEWER)
# When every free model is busy (or opencode is missing), the demo runs on the
# Claude Code / Codex the user already has, instead of failing (release check,
# 2026-10-17). Both present: Claude builds and Codex reviews, across vendors.
FALLBACK_ORDER = ("claude", "codex")
# What each CLI needs from the real home to stay logged in; the demo's agents
# run with a throwaway HOME, so these are linked into it (never copied).
AUTH_PATHS = {"claude": (".claude", ".claude.json"), "codex": (".codex",)}
_NOT_A_VERDICT = re.compile(r"nothing to review|standing by|will review|waiting for",
                            re.IGNORECASE)


def make_sample_repo(root: Path) -> Path:
    repo = root / "sample-app"
    repo.mkdir(parents=True)
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    (repo / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n")
    env = {**os.environ, "GIT_AUTHOR_NAME": "you", "GIT_AUTHOR_EMAIL": "you@demo.local",
           "GIT_COMMITTER_NAME": "you", "GIT_COMMITTER_EMAIL": "you@demo.local"}
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                ["git", "commit", "-qm", "init: sample app with a bug"]):
        subprocess.run(cmd, cwd=repo, env=env, check=True, capture_output=True)
    return repo


def write_binding(home: Path, repo: Path) -> None:
    import json

    q = home / ".quorus"
    q.mkdir(parents=True, exist_ok=True)
    # trust: members, because "you" are a human in the room, not the agents' owner.
    (q / "room-bindings.json").write_text(json.dumps(
        {ROOM: {"path": str(repo), "mode": "autonomous", "trust": "members"}}))


def is_fixed(repo: Path) -> bool:
    out = subprocess.run(["git", "show", "main:calc.py"], cwd=repo, capture_output=True,
                         text=True).stdout
    return "a + b" in out


def review_of(history: list[dict[str, Any]], reviewer: str = REVIEWER) -> str | None:
    """The reviewer's verdict after the fix was published (a plan line isn't one)."""
    pub = [i for i, m in enumerate(history) if "(quorus) published" in (m.get("content") or "")]
    if not pub:
        return None
    for m in history[pub[0] + 1:]:
        body = (m.get("content") or "").strip()
        if (m.get("from_name") == reviewer and m.get("message_type") != "status"
                and not body.lower().startswith("plan:") and not _NOT_A_VERDICT.search(body)):
            return body.splitlines()[0][:160]
    return None


def all_out_of_quota(history: list[dict[str, Any]],
                     agents: tuple[str, str] = (BUILDER, REVIEWER)) -> bool:
    """Both agents posted 'out of quota': waiting longer won't help."""
    out = {m.get("from_name") for m in history if "is out of quota" in (m.get("content") or "")}
    return set(agents) <= out


def fallback_cast(which: Any = shutil.which) -> list[tuple[str, None]]:
    """``[(builder_harness, None), (reviewer_harness, None)]`` from the logged-in
    Claude Code / Codex on this machine, or ``[]``. None = the CLI's own model."""
    from quorus_cli.quickstart import detect

    ready = [f.harness for f in detect(which) if f.ready and f.harness in FALLBACK_ORDER]
    ready.sort(key=FALLBACK_ORDER.index)
    if not ready:
        return []
    return [(ready[0], None), (ready[-1], None)]


def link_auth(home: Path, harnesses: set[str], real_home: Path | None = None) -> None:
    real_home = real_home or Path.home()
    for h in harnesses:
        for name in AUTH_PATHS.get(h, ()):
            src, dst = real_home / name, home / name
            if src.exists() and not dst.exists() and not dst.is_symlink():
                dst.symlink_to(src)


LABELS = {"claude": "Claude Code", "codex": "Codex", "opencode": "opencode"}


def _label(member: tuple[str, str | None]) -> str:
    harness, model = member
    return model.split("/", 1)[-1] if model else LABELS.get(harness, harness)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _opencode_ok() -> str | None:
    exe = shutil.which("opencode")
    if not exe:
        return "the demo uses opencode (free models, no key): `brew install opencode` " \
               "or `npm i -g @opencode/cli`"
    out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30).stdout
    m = re.search(r"(\d+)\.\d+", out)
    if m and int(m.group(1)) < 2:
        return f"opencode {m.group(0)} is too old for the free models: `npm i -g @opencode/cli`"
    return None


def model_answers(model: str, cwd: Path, timeout: float = 45) -> bool:
    """One tiny `pong` call: does this free model take requests right now?"""
    try:
        out = subprocess.run(
            ["opencode", "run", "--standalone", "--format", "json", "-m", model, "--",
             "Reply with exactly: pong"], cwd=cwd, capture_output=True, text=True,
            timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    return '"type":"text"' in out.stdout.replace(" ", "") and "pong" in out.stdout.lower()


def pick_models(cwd: Path, console: Any, forced: str | None = None) -> list[str]:
    if forced:
        return [forced, forced]
    from quorus.runtime.opencode_free import free_models

    working: list[str] = []
    for model in free_models(FREE_MODELS):  # the live catalog first; it churns
        console.print(f"[dim]checking {model.split('/', 1)[1]}…[/]")
        if model_answers(model, cwd):
            working.append(model)
            if len(working) == 2:
                break
    return working + working[:1] if len(working) == 1 else working


def _show(console: Any, m: dict[str, Any], start: float) -> None:
    who, body = m.get("from_name", "?"), (m.get("content") or "").strip()
    secs = f"{time.time() - start:4.0f}s"
    if m.get("message_type") == "status" or body.startswith("(quorus)"):
        console.print(f"[dim]{secs}  · {body}[/]", highlight=False)
    else:
        style = "success" if who == HUMAN else "accent"
        console.print(f"[dim]{secs}[/]  [{style}]@{who}[/] {body[:600]}", highlight=False)


def _show_steps(console: Any, url: str, headers: dict[str, str], last: dict[str, str],
                start: float, agents: tuple[str, str] = (BUILDER, REVIEWER)) -> None:
    """Each agent's live step ("editing calc.py"), once per change."""
    try:
        presence = httpx.get(f"{url}/presence", headers=headers, timeout=5).json()
    except (httpx.HTTPError, ValueError):
        return
    for p in presence if isinstance(presence, list) else []:
        name, step = p.get("name"), p.get("activity") or ""
        if name in agents and step and last.get(name) != step:
            last[name] = step
            console.print(f"[dim]{time.time() - start:4.0f}s  ⋯ @{name} {step}[/]",
                          highlight=False)


def cmd_demo(args: Any, console: Any) -> None:
    problem = _opencode_ok()
    if problem and not fallback_cast():
        raise SystemExit(f"demo: {problem}")
    root = Path(tempfile.mkdtemp(prefix="quorus-demo-"))
    home, secret, port = root / "home", secrets.token_hex(16), _free_port()
    url, procs = f"http://127.0.0.1:{port}", []
    H = {"Authorization": f"Bearer {secret}"}
    console.print("[bold]Quorus demo[/]: two AI agents on a free open-source model fix a bug "
                  "together. Nothing in your own setup is touched.\n")
    try:
        repo = make_sample_repo(root)
        write_binding(home, repo)
        models = [] if problem else pick_models(root, console, args.model)
        cast = [("opencode", m) for m in models] or ([] if args.model else fallback_cast())
        if not cast:
            console.print("[warning]![/] opencode's free models are all busy right now. Try "
                          "again in a few minutes, or use your own models with `quorus agent add`.")
            return
        builder, reviewer = (BUILDER, REVIEWER) if models else (
            f"demo-{cast[0][0]}", f"demo-review-{cast[1][0]}")
        if not models:
            link_auth(home, {h for h, _ in cast})
            tools = " and ".join(dict.fromkeys(LABELS[h] for h, _ in cast))
            why = "opencode isn't usable here" if problem else "opencode's free models are busy"
            console.print(f"{why}, so the demo runs on your own logged-in {tools} "
                          "(their login and their usage).")
        console.print("[dim]builder: " + _label(cast[0]) + " · reviewer: " + _label(cast[1])
                      + "[/]\n")
        base_env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("QUORUS_", "REFLEXD_")) and k not in ("API_KEY",)}
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "quorus.relay_cli"],
            env={**base_env, "RELAY_SECRET": secret, "PORT": str(port), "HOST": "127.0.0.1",
                 "ALLOW_LEGACY_AUTH": "1", "MESSAGES_FILE": str(root / "relay.json"),
                 "LOG_LEVEL": "WARNING"},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        for _ in range(60):
            try:
                if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.25)
        httpx.post(f"{url}/rooms", json={"name": ROOM, "created_by": HUMAN}, headers=H)
        for who in (HUMAN, builder, reviewer):
            httpx.post(f"{url}/rooms/{ROOM}/join", json={"participant": who}, headers=H)
        from quorus.runtime.supervisor import _reflexd_script_path

        for name, (_h, model) in ((builder, cast[0]), (reviewer, cast[1])):
            env = {**base_env, "HOME": str(home), "RELAY_URL": url, "API_KEY": secret,
                   "REFLEXD_PARTICIPANT": name, "REFLEXD_LEGACY_BEARER": "1",
                   **({"REFLEXD_MODEL": model} if model else {}), "REFLEXD_OWNER": HUMAN,
                   "PYTHONUNBUFFERED": "1", "QUORUS_NOTIFY": "0",
                   "REFLEXD_LOG_PATH": str(root / f"{name}.log"), "PWD": str(root)}
            procs.append(subprocess.Popen(
                [sys.executable, str(_reflexd_script_path()), "start", "--participant", name,
                 "--relay-url", url], env=env, cwd=str(root),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        time.sleep(6)
        start = time.time()
        httpx.post(f"{url}/rooms/{ROOM}/messages", json={"from_name": HUMAN,
                                                            "content": task_for(builder, reviewer)},
                   headers=H)
        seen, history, fixed_at, steps = 0, [], None, {}
        while time.time() - start < args.timeout:
            history = httpx.get(f"{url}/rooms/{ROOM}/history", params={"limit": 200},
                                headers=H, timeout=10).json()
            for m in history[seen:]:
                _show(console, m, start)
            seen = len(history)
            _show_steps(console, url, H, steps, start, (builder, reviewer))
            if fixed_at is None and is_fixed(repo):
                fixed_at = time.time() - start
            if fixed_at is not None and review_of(history, reviewer):
                break
            if all_out_of_quota(history, (builder, reviewer)):
                break
            time.sleep(2)
        if fixed_at is not None or is_fixed(repo):
            took = fixed_at if fixed_at is not None else time.time() - start
            console.print(f"\n[success]✓[/] The fix was on main {took:.0f}s after your message.")
            verdict = review_of(history, reviewer)
            console.print(f"  Second agent's review: {verdict}" if verdict else
                          "  [dim](the second agent's review was still running)[/]",
                          highlight=False)
            console.print(f"[dim]{subprocess.run(['git', 'log', '--oneline', '-3', 'main'], cwd=repo, capture_output=True, text=True).stdout.strip()}[/]")  # noqa: E501
            console.print("\nYour turn: run [accent]quorus[/] to make a room, then "
                          "[accent]quorus agent add claude|codex|gemini|opencode --room general "
                          "--repo <your project>[/].")
        else:
            console.print(f"\n[warning]![/] The agents didn't finish in {args.timeout}s. "
                          "opencode's free tier may be busy; try again in a few minutes, or "
                          "use your own models with `quorus agent add`.")
    finally:
        for p in reversed(procs):
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)
        else:
            console.print(f"[dim]kept the demo folder: {root}[/]")
