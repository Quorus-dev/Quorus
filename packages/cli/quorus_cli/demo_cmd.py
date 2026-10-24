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
    # The reviewer's turn is read-only (no reply chain to a person under `trust:
    # members`), so the hand-off carries what it needs to review by reading (0.7.1).
    return (f"@{builder} add() in calc.py is broken: fix it, run the test, commit, then "
            f"@-mention your reviewer ({reviewer}) to review your commit. In that "
            "message, give the commit hash, the file and line you changed, and the test "
            "result, so it can review by reading.")


TASK = task_for(BUILDER, REVIEWER)
# When every free model is busy (or opencode is missing), the demo runs on the
# Claude Code / Codex the user already has, instead of failing (release check,
# 2026-10-17). Both present: Claude builds and Codex reviews, across vendors.
FALLBACK_ORDER = ("claude", "codex")
# The demo's agents run with a throwaway HOME. Only each CLI's credential
# store is shared with the real one (codex, review of 62eface2): config,
# sessions and history are written inside the throwaway HOME and deleted with
# it. They are linked, not copied: a copy would refresh the OAuth token on its
# own and can revoke the user's real login. Claude Code on macOS keeps its
# login in the keychain (found via $HOME/Library/Keychains), on Linux in
# ~/.claude/.credentials.json; Codex in $CODEX_HOME/auth.json.
AUTH_FILES = {"claude": ("Library/Keychains", ".claude/.credentials.json"),
              "codex": (".codex/auth.json",)}
LOGIN_CHECK = {"claude": ["claude", "auth", "status"], "codex": ["codex", "login", "status"]}
# "approval needed" is the relay's notice that an agent asked a human for a tool;
# the 0.6.6 stranger pass printed it as the "Second agent's review" (2026-10-21).
_NOT_A_VERDICT = re.compile(r"nothing to review|standing by|will review|waiting for"
                            r"|approval needed", re.IGNORECASE)


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


def logged_in(harness: str) -> bool:
    """The CLI's own answer (exit 0 = logged in), not just "it's installed"
    (codex, review of 62eface2)."""
    try:
        return subprocess.run(LOGIN_CHECK[harness], capture_output=True, text=True,
                              timeout=20, stdin=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def fallback_cast(which: Any = shutil.which,
                  is_logged_in: Any = logged_in) -> list[tuple[str, None]]:
    """``[(builder_harness, None), (reviewer_harness, None)]`` from the logged-in
    Claude Code / Codex on this machine, or ``[]``. None = the CLI's own model."""
    ready = [h for h in FALLBACK_ORDER if which(h) and is_logged_in(h)]
    if not ready:
        return []
    return [(ready[0], None), (ready[-1], None)]


def share_login(home: Path, harnesses: set[str], real_home: Path | None = None) -> None:
    """Link each CLI's credential store, and nothing else, into *home*."""
    real_home = real_home or Path.home()
    codex_home = os.environ.get("CODEX_HOME")
    for h in harnesses:
        for rel in AUTH_FILES.get(h, ()):
            src = (Path(codex_home) / "auth.json" if rel == ".codex/auth.json" and codex_home
                   else real_home / rel)
            dst = home / rel
            if not src.exists() or dst.exists() or dst.is_symlink():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)  # real dirs, private to the demo
            dst.symlink_to(src)


LABELS = {"claude": "Claude Code", "codex": "Codex", "opencode": "opencode"}


def agent_env(base_env: dict[str, str], *, home: Path, root: Path, url: str, secret: str,
              name: str, model: str | None) -> dict[str, str]:
    """Environment for one demo agent's daemon (and, inherited, its harness and
    MCP server). ``QUORUS_APPROVALS=deny``: the demo room has no human who can
    approve a tool call, so the approve tool answers "no" at once instead of
    posting an approval request nobody will answer (0.6.6 stranger pass: the
    reviewer blocked 232 s on `bash`)."""
    return {**base_env, "HOME": str(home), "CODEX_HOME": str(home / ".codex"),
            "RELAY_URL": url, "API_KEY": secret,
            "REFLEXD_PARTICIPANT": name, "REFLEXD_LEGACY_BEARER": "1",
            **({"REFLEXD_MODEL": model} if model else {}), "REFLEXD_OWNER": HUMAN,
            "PYTHONUNBUFFERED": "1", "QUORUS_NOTIFY": "0", "QUORUS_APPROVALS": "deny",
            "REFLEXD_LOG_PATH": str(root / f"{name}.log"), "PWD": str(root)}


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


# A model that needs longer than this for a one-word reply makes the demo
# crawl: nemotron took 111-300s to its first token while beam took 1.2s
# (release check, 2026-10-17). Skip it rather than wait.
PROBE_MAX_S = 20.0


class FreeTierLimited(str):
    """The probe got opencode's typed free-tier limit (the error event's words).
    The limit is shared across every free model, so probing the next one would
    only spend more of it (docs/plans/keyless-answer.md §3.5)."""


def model_answers(model: str, cwd: Path,
                  timeout: float = PROBE_MAX_S) -> float | FreeTierLimited | None:
    """One tiny `pong` call: seconds this free model took to answer; a
    :class:`FreeTierLimited` when the free tier itself refused; None when it
    didn't answer for any other reason (busy, slower than *timeout*)."""
    from quorus.runtime.opencode_free import free_limit_in

    started = time.monotonic()
    try:
        out = subprocess.run(
            ["opencode", "run", "--standalone", "--format", "json", "-m", model, "--",
             "Reply with exactly: pong"], cwd=cwd, capture_output=True, text=True,
            timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    if '"type":"text"' in out.stdout.replace(" ", "") and "pong" in out.stdout.lower():
        return time.monotonic() - started
    limit = free_limit_in(out.stdout)
    return FreeTierLimited(limit) if limit else None


def pick_models(cwd: Path, console: Any, forced: str | None = None) -> list[str]:
    if forced:
        return [forced, forced]
    from quorus.runtime.opencode_free import free_models, mark_slow

    timed: list[tuple[float, str]] = []
    for model in free_models(FREE_MODELS):  # the live catalog first; it churns
        console.print(f"[dim]checking {model.split('/', 1)[1]}…[/]")
        secs = model_answers(model, cwd)
        if isinstance(secs, FreeTierLimited):
            # Shared limit: the other free models are limited too. Stop here, and
            # don't file a limit as "slow" (it would outlive the limit by an hour).
            console.print("[dim]opencode's free tier reported its limit; not probing "
                          "the other free models[/]")
            break
        if secs is None:
            mark_slow(model)  # busy or slow: last in line if we come back to it
            continue
        timed.append((secs, model))
        if len(timed) == 2:
            break
    working = [m for _, m in sorted(timed)]  # the faster one builds
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


def consented_fallback(args: Any, console: Any) -> list[tuple[str, None]]:
    """No credential sharing or model work until the person explicitly opts in."""
    cast = fallback_cast()
    if not cast:
        return []
    tools = " and ".join(dict.fromkeys(LABELS[h] for h, _ in cast))
    console.print(f"Free models are unavailable. The fallback uses your signed-in {tools} "
                  "on this computer and consumes your account's usage.")
    if getattr(args, "use_my_login", False):
        return cast
    if sys.stdin.isatty():
        from rich.prompt import Confirm

        try:
            if Confirm.ask("Use those logins for this demo?", default=False, console=console):
                return cast
        except (EOFError, KeyboardInterrupt):
            console.print()
    console.print("No signed-in fallback started. Retry later, continue in your room with "
                  "`quorus`, or explicitly opt in with `quorus demo --use-my-login`.")
    return []


def cmd_demo(args: Any, console: Any) -> None:
    problem = _opencode_ok()
    if problem and args.model:
        raise SystemExit(f"demo: {problem}")
    root = Path(tempfile.mkdtemp(prefix="quorus-demo-"))
    home, procs = root / "home", []
    console.print("[bold]Quorus demo[/]: two AI agents on a free open-source model fix a bug "
                  "together. Nothing in your own setup is touched.\n")
    try:
        repo = make_sample_repo(root)
        write_binding(home, repo)
        models = [] if problem else pick_models(root, console, args.model)
        cast = [("opencode", m) for m in models] or (
            [] if args.model else consented_fallback(args, console))
        if not cast:
            if problem:
                console.print(f"opencode isn't usable: {problem}", markup=False)
            else:
                console.print("[warning]![/] opencode's free models are all busy right now. Try "
                              "again in a few minutes, or use your own models with "
                              "`quorus agent add`.")
            raise SystemExit(1)  # scripts and CI must see the demo didn't run
        secret, port = secrets.token_hex(16), _free_port()
        url = f"http://127.0.0.1:{port}"
        H = {"Authorization": f"Bearer {secret}"}
        builder, reviewer = (BUILDER, REVIEWER) if models else (
            f"demo-{cast[0][0]}", f"demo-review-{cast[1][0]}")
        if not models:
            share_login(home, {h for h, _ in cast})
        console.print("[dim]builder: " + _label(cast[0]) + " · reviewer: " + _label(cast[1])
                      + "[/]\n")
        base_env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("QUORUS_", "REFLEXD_"))
                    and k not in ("API_KEY", "CLAUDE_CONFIG_DIR", "CODEX_HOME")}
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
            env = agent_env(base_env, home=home, root=root, url=url, secret=secret,
                            name=name, model=model)
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
                          "[accent]quorus agent add claude|codex|opencode --room general "
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
