"""`quorus demo`: watch two AI agents fix a bug together, with no accounts or keys.

Trying Quorus used to need Claude Code or Codex accounts, so most visitors left
before they saw anything happen (weekend research, 2026-10-17). The demo runs everything
in a throwaway folder: a sample repo with a planted bug, a private relay, and
two agents on a free open-source model through opencode. It posts one message,
streams the room live, and cleans up afterwards. Nothing in your own Quorus setup
is touched.
"""

from __future__ import annotations

import base64
import json
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

from quorus.runtime import review_gate as _gate

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
    # The room is gated (review-gate slice 4): the builder's commit is held off main
    # until the reviewer's GO, so the task says exactly what a GO looks like.
    return (f"@{builder} fix the bug in calc.py: add() returns a - b. Fix it, run the test, "
            f"commit, then @-mention your reviewer ({reviewer}) to review your commit. In "
            "that message, give the commit hash, the file and line you changed, and the "
            f"test result, so it can review by reading. {reviewer}: nothing lands on main "
            "until you answer. Read the commit; if it is right, reply on one line "
            "`GO on <hash>`; if not, `/disagree blocking on <hash>: <why>`.")


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


def agent_key_dir(home: Path, name: str) -> Path:
    """Where *name*'s daemon looks for its own participant key: the daemon's
    ``config_dir()/runtime/agents/<name>`` (scripts/reflexd.py, ``key_dir``), under the
    demo's throwaway HOME."""
    return home / ".quorus" / "runtime" / "agents" / name


def mint_agent_keys(url: str, secret: str, home: Path, names: tuple[str, ...]) -> dict[str, str]:
    """Claim a participant key for each demo agent before its daemon starts, and save
    it where that daemon loads it, so every message the agent posts is stamped ``key``
    (its own identity) rather than ``secret``. A GO sent with the relay secret can never
    approve anything (review-gate.md), so this is what lets the reviewer's GO count."""
    from quorus import bundle_client

    keys: dict[str, str] = {}
    for name in names:
        resp = httpx.post(f"{url}/v1/auth/claim-name", json={"name": name},
                                headers={"Authorization": f"Bearer {secret}"}, timeout=10,
                                follow_redirects=False)
        if resp.status_code != 200:
            raise RuntimeError(f"the demo relay wouldn't claim {name}: HTTP {resp.status_code}")
        keys[name] = resp.json()["participant_key"]
        bundle_client.save_participant_key(url, name, keys[name], agent_key_dir(home, name))
    return keys


def claim_id_of(url: str, key: str) -> str:
    """The claim generation the relay puts in *key*'s JWT (``pk_claim``), which it also
    stamps on that agent's messages as ``sender_claim_id``: the thing a reviewer pin is
    bound to. Read from our own token; the relay verifies it, we only decode it."""
    resp = httpx.post(f"{url}/v1/auth/token", json={"api_key": key}, timeout=10,
                            follow_redirects=False)
    if resp.status_code != 200:
        raise RuntimeError(f"the demo relay wouldn't sign the agent in: HTTP {resp.status_code}")
    payload = resp.json()["token"].split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    return str(claims["pk_claim"])


def reviewers_file(home: Path) -> Path:
    return home / ".quorus" / "room-reviewers.json"


def pin_reviewer(url: str, home: Path, reviewer: str, key: str) -> dict[str, str]:
    """Record *reviewer* as #demo's reviewer, pinned to its own key's claim, in the
    file the builder's daemon reads (``room-reviewers.json`` next to the bindings).
    From then on that daemon holds its commits off main until this reviewer's GO."""
    _gate.save_reviewer(ROOM, reviewer, tenant="_legacy", claim_id=claim_id_of(url, key),
                        path=reviewers_file(home))
    pin = _gate.reviewer_for(ROOM, reviewers_file(home))
    assert pin and _gate.pinned(pin)
    return pin


_AWAITING = re.compile(r"awaiting @[\w-]+'s review of ([0-9a-f]{7,40})")


def held_tip(history: list[dict[str, Any]], repo: Path) -> str | None:
    """The full sha of the commit the builder's daemon is holding for review, from its
    latest "awaiting … review of <sha>" status in the room (None until it posts one)."""
    for m in reversed(history):
        found = _AWAITING.search(m.get("content") or "")
        if not found:
            continue
        out = subprocess.run(["git", "rev-parse", "--verify", "--quiet", "--end-of-options",
                              found.group(1) + "^{commit}"], cwd=repo,
                             capture_output=True, text=True)
        return out.stdout.strip() or None
    return None


def published_after_review(history: list[dict[str, Any]], reviewer: str,
                           go_id: str | None) -> bool:
    """The daemon's publish note names this reviewer and came after its GO."""
    if not go_id:
        return False
    ids = [m.get("id") for m in history]
    note = next((i for i, m in enumerate(history)
                 if "(quorus) published" in (m.get("content") or "")
                 and f"reviewed by @{reviewer}" in (m.get("content") or "")), None)
    return note is not None and go_id in ids and ids.index(go_id) < note


def review_standing(fetch: Any, pin: dict[str, str], tip: str | None, reviewer: str,
                    repo: Path, history: list[dict[str, Any]] | None = None,
                    ) -> tuple[str, list[dict[str, Any]], str | None, str | None]:
    """Where the gate stands: ``("reviewed" | "receipt_pending" | "unreviewed" | "blocked"
    | "waiting", history, tip, go_id)``. ``reviewed``: the fix is on main and the
    reviewer's GO precedes the daemon's "reviewed by" publish note. ``receipt_pending``:
    main has the fix and the GO is there, but the note isn't yet; the daemon fast-forwards
    main before it posts the receipt (codex on 1182b513), so keep polling. ``unreviewed``:
    main moved and there is no GO at all, after a second read of the room, because the
    room is read before main is checked and a GO can land in between (codex on 09dd399a)."""
    history = fetch() if history is None else history
    tip = tip or held_tip(history, repo)
    state, go_id = _gate.decide(history, pin, [tip]) if tip else ("waiting", None)
    if state == "blocked":
        return "blocked", history, tip, go_id
    if not is_fixed(repo):
        return "waiting", history, tip, go_id
    if state != "go":
        history = fetch()
        tip = tip or held_tip(history, repo)
        state, go_id = _gate.decide(history, pin, [tip]) if tip else ("waiting", None)
    if state == "blocked":
        return "blocked", history, tip, go_id
    if state != "go":
        return "unreviewed", history, tip, go_id
    if published_after_review(history, reviewer, go_id):
        return "reviewed", history, tip, go_id
    return "receipt_pending", history, tip, go_id


def is_fixed(repo: Path) -> bool:
    out = subprocess.run(["git", "show", "main:calc.py"], cwd=repo, capture_output=True,
                         text=True).stdout
    return "a + b" in out


def review_of(history: list[dict[str, Any]], reviewer: str = REVIEWER) -> str | None:
    """The reviewer's verdict (a plan line isn't one). With the gate the review comes
    while the commit is held, before the publish note, so look from the first
    "awaiting … review" status; an ungated room's verdict follows its publish note."""
    marks = [i for i, m in enumerate(history)
             if "(quorus) awaiting" in (m.get("content") or "")
             or "(quorus) published" in (m.get("content") or "")]
    if not marks:
        return None
    for m in history[marks[0] + 1:]:
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
    except subprocess.TimeoutExpired as exc:
        # The CLI retries a 429 itself with an hours-long countdown, so a limited
        # probe times out with the limit event already in its partial output
        # (codex, review of 94a791db). Read it: this is a limit, not a slow model.
        partial = exc.output or b""
        stdout = partial.decode("utf-8", errors="replace") if isinstance(partial, bytes) \
            else str(partial)
        limit = free_limit_in(stdout)
        return FreeTierLimited(limit) if limit else None
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
                from quorus_cli.quickstart import print_next_command

                print_next_command(console, "general")  # the same line `quorus doctor` prints
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
        # Review before main: each agent gets its own key, and the reviewer is pinned to
        # its key's claim in the file the builder's daemon reads, so the fix is held off
        # main until a GO that provably came from the reviewer (review-gate slice 4).
        keys = mint_agent_keys(url, secret, home, (builder, reviewer))
        pin = pin_reviewer(url, home, reviewer, keys[reviewer])
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
        tip, go_at, go_id, standing = None, None, None, "waiting"

        def fetch() -> list[dict[str, Any]]:
            return httpx.get(f"{url}/rooms/{ROOM}/history", params={"limit": 200},
                                   headers=H, timeout=10).json()

        while time.time() - start < args.timeout:
            standing, history, tip, go_id = review_standing(fetch, pin, tip, reviewer, repo)
            for m in history[seen:]:
                _show(console, m, start)
            seen = len(history)
            _show_steps(console, url, H, steps, start, (builder, reviewer))
            if go_at is None and go_id:
                go_at = time.time() - start
            if fixed_at is None and standing in ("reviewed", "receipt_pending", "unreviewed"):
                fixed_at = time.time() - start
            if standing not in ("waiting", "receipt_pending"):
                break
            if all_out_of_quota(history, (builder, reviewer)):
                break
            time.sleep(2)
        fixed = fixed_at is not None or is_fixed(repo)
        took = fixed_at if fixed_at is not None else time.time() - start
        if standing == "reviewed":
            console.print(f"\n[success]✓[/] @{reviewer} read the commit and said GO "
                          f"{go_at:.0f}s after your message; the fix landed on main at "
                          f"{took:.0f}s, after the review, not before.")
            verdict = review_of(history, reviewer)
            if verdict:
                console.print(f"  Second agent's review: {verdict}", highlight=False)
            console.print(f"[dim]{subprocess.run(['git', 'log', '--oneline', '-3', 'main'], cwd=repo, capture_output=True, text=True).stdout.strip()}[/]")  # noqa: E501
            console.print("\nYour turn: run [accent]quorus[/] to make a room, then "
                          "[accent]quorus agent add claude|codex|opencode --room general "
                          "--repo <your project>[/].")
        elif standing == "receipt_pending":
            # The GO is there and main has the fix, but the daemon's publish receipt
            # never arrived within the deadline, so the order can't be shown from the room.
            console.print(f"\n[warning]![/] @{reviewer} said GO and the fix is on main, but "
                          "the publish receipt that proves the order didn't arrive in "
                          f"{args.timeout}s. Try again; the agents may be slow.")
            raise SystemExit(1)
        elif standing == "unreviewed" or (fixed and standing != "blocked"):
            # main moved without the reviewer's GO: the gate didn't hold. Say so and
            # fail; the demo's promise is review before main, and this run can't make it.
            console.print(f"\n[warning]![/] The fix reached main {took:.0f}s in without "
                          f"@{reviewer}'s GO. The review gate didn't hold it; this run "
                          "doesn't show review before main.")
            raise SystemExit(1)
        elif standing == "blocked":
            console.print(f"\n[warning]![/] @{reviewer} blocked {(tip or '')[:7]}: nothing "
                          "landed on main. That is the gate working; the fix itself was "
                          "not accepted this run.")
            raise SystemExit(1)
        elif tip:
            console.print(f"\n[warning]![/] {(tip or '')[:7]} is held for @{reviewer}'s "
                          f"review and the review didn't arrive in {args.timeout}s, so "
                          "nothing is on main. opencode's free tier may be busy; try again "
                          "in a few minutes, or use your own models with `quorus agent add`.")
            raise SystemExit(1)
        else:
            console.print(f"\n[warning]![/] The agents didn't finish in {args.timeout}s. "
                          "opencode's free tier may be busy; try again in a few minutes, or "
                          "use your own models with `quorus agent add`.")
            raise SystemExit(1)
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
