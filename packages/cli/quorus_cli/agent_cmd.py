"""`quorus agent add|list|remove` — put your own AI agents in a room.

The user-facing way to do what scripts/dogfood.sh did by hand (2026-10-09):

    quorus create build
    quorus agent add claude --room build --repo ~/dev/myapp --mode autonomous
    quorus agent add codex  --room build

Each agent is ``<you>-<tool>`` (arav-claude). It joins the room and gets ONE
always-on wake daemon (launchd on macOS; a detached process elsewhere,
see agent_proc) that listens to every room the
agent is in and runs your locally logged-in ``claude`` / ``codex`` CLI in the
room's repo when work arrives. Adding the same agent to another room only
joins it; the daemon is shared. An existing daemon for the same agent
(including the dogfood one) is reused, never duplicated — two daemons for
one agent would answer every message twice.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

from quorus.config import load_config, resolve_config_dir
from quorus.private_files import write_private_text
from quorus_cli import agent_proc, agent_release, agent_start

# opencode = any model: Ollama (local), OpenRouter, DeepSeek, Qwen, Gemini...
HARNESSES = ("claude", "codex", "gemini", "opencode")
_LABEL_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
# Shown before an invited member gives an agent write access on a teammate's
# relay, in the wizard and in `quorus agent add` (Codex's trust call on 3212878c,
# review of 0f9a90b7): key stamps keep other members out, not the relay's owner.
TEAMMATE_RELAY_NOTE = ("This room is on a teammate's relay. Your agents act only on requests "
                       "sent with your own key, and its owner runs the relay, so you're "
                       "trusting it to report who sent what.")
LABEL_PREFIX = "dev.quorus.agent."
_LA = Path.home() / "Library" / "LaunchAgents"
MODES = ("default", "manual", "autonomous")


def _agents_dir() -> Path:
    d = resolve_config_dir() / "agents"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def _write_private(path: Path, text: str) -> None:
    write_private_text(path, text)


def _state_record(name: str) -> Path:
    """Where `agent add` notes the --state-dir it started *name*'s daemon with."""
    return _agents_dir() / f"{name}.state-dir"


def _daemon_state_dir(name: str) -> Path | None:
    """The --state-dir *name*'s daemon was started with; None for the owner's own dir
    (and for daemons started before the record existed)."""
    try:
        text = _state_record(name).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return Path(text) if text else None


def _uid() -> int:
    return os.getuid()


def _launchd_ok() -> bool:
    """launchd is macOS-only, and QUORUS_NO_LAUNCHD=1 refuses it outright: a test
    with a tmp HOME would otherwise register agents in the real login session,
    which launchd doesn't scope to HOME (loop C, first-run pty tests)."""
    return sys.platform == "darwin" and not os.environ.get("QUORUS_NO_LAUNCHD")


def _launchd_jobs() -> dict[str, int | None]:
    """{quorus launchd label: its pid, or None while the job isn't running}."""
    if not _launchd_ok():
        return {}
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True,
                             timeout=10, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    jobs: dict[str, int | None] = {}
    for line in out.splitlines():
        cols = line.split()
        if "quorus" in line and cols:
            jobs[cols[-1]] = int(cols[0]) if cols[0].isdigit() else None
    return jobs


def _launchd_labels() -> list[str]:
    return list(_launchd_jobs())


def _running_daemon_for(name: str, relay_url: str | None = None) -> str | None:
    """An already-running wake daemon for *name*: a launchd job (ours or
    dogfood's) OR any reflexd process for it (e.g. started by the legacy
    reflexd-manager or `quorus reflexd start`). Two daemons for one agent
    answer every message twice. The pid file comes first: minimal Linux
    images ship without pgrep. With *relay_url*, a reflexd process for the same
    name on another relay doesn't count: an orphan from an old run on a dead
    relay made every later `agent add` of that name start nothing. Our own
    launchd job with no live pid doesn't count either: a crashed job blocked the
    re-add that would fix it, and `_start_daemon` replaces it (take-3 bug 1).
    Another job's label (dogfood's) still counts: KeepAlive may restart it."""
    pid = agent_proc.live_pid(_agents_dir(), name)
    if pid is not None:
        return f"pid {pid}"
    for label, job_pid in _launchd_jobs().items():
        if label.endswith(f".{name}") and ("agent." in label or "reflexd." in label):
            if job_pid is None and label == LABEL_PREFIX + name:
                continue
            return label
    try:
        out = subprocess.run(["pgrep", "-f", f"reflexd.py start.*--participant {name}( |$)"],
                             capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    pids = out.stdout.split()
    if relay_url:
        pids = [p for p in pids if _serves_relay(p, relay_url)]
    return f"pid {pids[0]}" if pids else None


def _serves_relay(pid: str, relay_url: str) -> bool:
    """Whether reflexd *pid* serves *relay_url*. If its command line can't be
    read, count it: a duplicate daemon is worse than a missed one."""
    try:
        out = subprocess.run(["ps", "-ww", "-o", "command=", "-p", pid],
                             capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return True
    argv = out.stdout.split()
    if out.returncode != 0 or "--relay-url" not in argv:
        return True
    i = argv.index("--relay-url")
    return i + 1 < len(argv) and argv[i + 1].rstrip("/") == relay_url.rstrip("/")


def _credentials(cfg: dict[str, Any], name: str, harness: str) -> tuple[str, bool]:
    """(credential, legacy) for the agent. Account relays mint a key per agent
    (cached 0600 so re-runs reuse it); shared-secret relays use the secret."""
    if (cfg.get("relay_secret") and (cfg.get("api_key") or "").startswith("quorus_pk_")
            and not _on_teammates_relay(cfg)):
        # H4: on their own relay the owner holds a participant key for who they
        # are and the secret for running the relay. Their agents keep the secret,
        # as before H4: the key path is for invited members' agents, and the
        # relay refused it here ("only an invited person's own key can add agents").
        return cfg["relay_secret"], True
    if cfg.get("api_key"):
        cache = _agents_dir() / f"{name}.json"
        if cache.exists():
            key = json.loads(cache.read_text(encoding="utf-8")).get("api_key")
            if key:
                return key, False
        if cfg["api_key"].startswith("quorus_pk_"):
            key = _claim_member_agent(cfg, name)
        else:
            from quorus_cli.cli import _register_agent_identity

            key = _register_agent_identity(cfg["relay_url"], cfg["api_key"], harness)
            if not key:
                raise SystemExit("agent: the relay refused to create an agent identity "
                                 "(run `quorus doctor`)")
        _write_private(cache, json.dumps({"api_key": key}))
        return key, False
    if cfg.get("relay_secret"):
        return cfg["relay_secret"], True
    raise SystemExit("agent: not configured — run `quorus init` or `quorus join` first")


def _on_owned_address(cfg: dict[str, Any]) -> dict[str, Any]:
    """A `localhost` profile on this machine's own relay that holds only one loopback
    (IPv6 off, ::1 taken): the owner's agents use the address the relay did bind, so
    they keep the owner's trust there, rather than being refused as a teammate's
    agents (0.7.1 stranger path)."""
    from quorus.runtime import own_relays

    url = cfg.get("relay_url") or ""
    alias = own_relays.owned_alias(url)
    return cfg if alias == url else {**cfg, "relay_url": alias}


def _on_teammates_relay(cfg: dict[str, Any]) -> bool:
    """This profile signs in with a participant key to a relay this machine
    didn't start. The key alone no longer says so: since H4 the relay's own
    owner signs in with one too, and was shown the teammate note and pinned
    as an invitee on their own relay (0.6.3 audit replay)."""
    if not (cfg.get("api_key") or "").startswith("quorus_pk_"):
        return False
    from quorus.runtime import own_relays

    mine = own_relays.normalize_relay(cfg.get("relay_url") or "")
    return not (mine and mine in own_relays.own_relays(own_relays.own_relays_path()))


def _claim_member_agent(cfg: dict[str, Any], name: str) -> str:
    """An invited member's own key claims *name* as their agent on a teammate's
    shared-secret relay (quorus/auth/member_agents.py). Before this, an invitee
    couldn't add an agent at all (bet 1's invitee persona)."""
    headers = {"Authorization": f"Bearer {_bearer(cfg, cfg['api_key'], False)}"}
    resp = httpx.post(f"{cfg['relay_url']}/v1/auth/claim-name", json={"name": name},
                      headers=headers, timeout=10)
    if resp.status_code == 200:
        return resp.json()["participant_key"]
    try:
        detail = resp.json().get("detail") or resp.text
    except ValueError:
        detail = resp.text
    # A relay older than 0.6.3 refuses every non-secret claim with this detail.
    if resp.status_code == 404 or detail == "only the relay's shared secret can claim":
        raise SystemExit("agent: this relay doesn't let invited members add agents yet "
                         "(it runs a Quorus older than 0.6.3). Ask its owner to update it, "
                         "or add your agents to a relay you run yourself.")
    raise SystemExit(f"agent: the relay refused @{name}: {detail}")


def _pin_owner(cfg: dict[str, Any], name: str) -> None:
    """On a teammate's relay, the agent trusts its owner's own key, not the name
    (quorus/runtime/owner_pin.py): pin the claim this member's key signs in as."""
    import base64

    from quorus.runtime.owner_pin import save_pin

    token = _bearer(cfg, cfg["api_key"], False)
    body = token.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    if not claims.get("pk_claim") or not claims.get("tenant_id"):
        raise SystemExit("agent: couldn't read your own key's identity from the relay")
    save_pin(name, claims["tenant_id"], claims["pk_claim"])


def _bearer(cfg: dict[str, Any], cred: str, legacy: bool) -> str:
    if legacy:
        return cred
    resp = httpx.post(f"{cfg['relay_url']}/v1/auth/token", json={"api_key": cred}, timeout=10)
    resp.raise_for_status()
    return resp.json()["token"]


def _join(cfg: dict[str, Any], room: str, name: str, bearer: str) -> None:
    resp = httpx.post(f"{cfg['relay_url']}/rooms/{room}/join",
                      json={"participant": name},
                      headers={"Authorization": f"Bearer {bearer}"}, timeout=10)
    if resp.status_code == 404:
        raise SystemExit(f"agent: room '{room}' not found — create it with "
                         f"`quorus create {room}`")
    if resp.status_code == 403 and _role_in(cfg, room, bearer) == "observer":
        # 0.6.3 audit: this said "the relay rejected your credentials, re-run init".
        raise SystemExit(f"agent: you joined #{room} as an observer, so you can read it "
                         "but not add agents. Ask the room's owner for a member invite.")
    resp.raise_for_status()


def _role_in(cfg: dict[str, Any], room: str, bearer: str) -> str | None:
    """This login's role in *room* (member, observer, ...), or None if unknown."""
    try:
        resp = httpx.get(f"{cfg['relay_url']}/rooms/{room}",
                         headers={"Authorization": f"Bearer {bearer}"}, timeout=10)
        if resp.status_code != 200:
            return None
        roles = resp.json().get("member_roles") or {}
    except (httpx.HTTPError, ValueError, AttributeError):
        return None
    me = cfg.get("instance_name")
    role = roles.get(me) if isinstance(roles, dict) else None
    return role if isinstance(role, str) else None


def _pace(args: Any, console: Any) -> None:
    """`quorus agent pace <name> <turns-per-hour|off>`: keep an agent inside its
    plan. Over the cap it waits for a slot; a human's message always goes first."""
    path = Path.home() / ".quorus" / "pacing.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    value = str(args.per_hour).strip().lower()
    if value in ("off", "0", "none"):
        data.pop(args.name, None)
        msg = f"@{args.name} is no longer paced"
    else:
        try:
            cap = int(value)
        except ValueError:
            raise SystemExit("agent pace: give a number of turns per hour, or `off`") from None
        if not 1 <= cap <= 120:
            raise SystemExit("agent pace: turns per hour must be 1-120")
        data[args.name] = {"per_hour": cap}
        msg = (f"@{args.name} takes at most {cap} turns per hour from teammates; "
               "your own messages always go first")
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_private(path, json.dumps(data, indent=2))
    console.print(f"[success]✓[/] {msg} [dim](applies from its next turn)[/]")


def _leads_path() -> Path:
    return Path.home() / ".quorus" / "room-leads.json"


def _read_leads() -> dict[str, str]:
    try:
        data = json.loads(_leads_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _set_lead(room: str, name: str | None) -> None:
    """One lead per room; None clears it. Read by reflexd's lead_for."""
    data = _read_leads()
    if name:
        data[room] = name
    else:
        data.pop(room, None)
    _leads_path().parent.mkdir(parents=True, exist_ok=True)
    _write_private(_leads_path(), json.dumps(data, indent=2))


def _drop_lead_everywhere(name: str) -> None:
    data = _read_leads()
    for room in [r for r, lead in data.items() if lead == name]:
        _set_lead(room, None)


def bindings_path(state_dir: Path | None = None) -> Path:
    """Where this machine's room → workspace bindings live: the shared resolver in
    ``quorus.runtime.bindings``, looked up at call time so a test's isolation of it
    (tests/conftest.py) covers every reader in this module too. With *state_dir*
    (`agent add --state-dir`), that agent's own bindings file instead."""
    from quorus.runtime import bindings

    return state_dir / bindings.FILENAME if state_dir else bindings.bindings_path()


def _state_dir(raw: str | None) -> Path | None:
    """`agent add --state-dir DIR`: an absolute, private dir for the daemon's own state
    (created 0700). Never the owner's own config dir: that is the default anyway."""
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise SystemExit("agent: --state-dir must be an absolute path")
    path = path.resolve()
    from quorus.runtime.bindings import config_dir

    if path == config_dir().resolve():
        raise SystemExit("agent: --state-dir is for a separate dir; leave it off to use "
                         f"{path}")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _bound_path(room: str, state_dir: Path | None = None) -> Path | None:
    """The workspace *room* is bound to on this machine, if it still exists."""
    try:
        prev = json.loads(bindings_path(state_dir).read_text(encoding="utf-8")).get(room)
    except (OSError, ValueError, AttributeError):
        return None
    raw = prev.get("path") if isinstance(prev, dict) else prev
    if not isinstance(raw, str) or not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_dir() else None


def _bind(room: str, repo: Path, mode: str | None, trust: str | None = None,
          state_dir: Path | None = None) -> str:
    path = bindings_path(state_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    prev = data.get(room)
    if mode is None:  # keep the room's existing mode
        mode = prev.get("mode", "default") if isinstance(prev, dict) else "default"
    if trust is None and isinstance(prev, dict):
        trust = prev.get("trust")
    # Keep the room's other settings (listen, judge, share, ...): rebinding to add
    # an agent silently turned an ambient room back to the rules (bug bash).
    entry: dict[str, Any] = {k: v for k, v in prev.items()
                             if k not in ("path", "mode", "trust")} \
        if isinstance(prev, dict) else {}
    entry.update(path=str(repo), mode=mode)
    if trust == "members":  # anyone in the room may trigger write-capable turns
        entry["trust"] = "members"
    data[room] = str(repo) if entry == {"path": str(repo), "mode": "default"} else entry
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_private(path, json.dumps(data, indent=2))
    return mode


def _harness_path(own: str | None = None) -> str:
    parts = [str(Path(sys.executable).parent), "/usr/bin", "/bin", "/usr/sbin", "/sbin",
             "/opt/homebrew/bin", "/usr/local/bin"]
    for b in HARNESSES:
        w = shutil.which(b)
        if w:
            parts.insert(0, str(Path(w).parent))
    mine = shutil.which(own) if own else None
    if mine:
        # The agent's own tool, exactly as `agent add` resolved it, comes first: another
        # harness's dir can hold a different binary of the same name. The dogfood
        # rehearsal's fakebin `claude` lost to the real one that way (Codex on 826faa27).
        parts.insert(0, str(Path(mine).parent))
    return os.pathsep.join(dict.fromkeys(parts))


def _plist(label: str, argv: list[str], env: dict[str, str], log: Path) -> str:
    from xml.sax.saxutils import escape as x

    args = "".join(f"<string>{x(a)}</string>" for a in argv)
    envs = "".join(f"<key>{x(k)}</key><string>{x(v)}</string>" for k, v in env.items())
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC '
        '"-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        f'<plist version="1.0"><dict><key>Label</key><string>{x(label)}</string>'
        f"<key>ProgramArguments</key><array>{args}</array>"
        f"<key>EnvironmentVariables</key><dict>{envs}</dict>"
        "<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>"
        "<key>ThrottleInterval</key><integer>5</integer>"
        f"<key>StandardOutPath</key><string>{x(str(log))}</string>"
        f"<key>StandardErrorPath</key><string>{x(str(log))}</string>"
        "</dict></plist>\n"
    )


def _start_daemon(cfg: dict[str, Any], name: str, cred: str, legacy: bool,
                  model: str | None = None, state_dir: Path | None = None) -> str:
    from quorus.runtime.supervisor import _reflexd_script_path

    label = LABEL_PREFIX + name
    log = _agents_dir() / f"{name}.log"
    crash_log = _agents_dir() / f"{name}.crash.log"  # launchd stdout/stderr
    own = name.rsplit("-", 1)[-1]
    env = {"PATH": _harness_path(own if own in HARNESSES else None),
           "RELAY_URL": cfg["relay_url"], "API_KEY": cred,
           "REFLEXD_PARTICIPANT": name, "PYTHONUNBUFFERED": "1",
           "REFLEXD_LOG_PATH": str(log),
           # The daemon reads room bindings from the same home `agent add`
           # wrote them to (launchd would otherwise use the login HOME).
           "HOME": str(Path.home()),
           # Whose desktop gets "shipped / needs you / stuck" banners.
           "REFLEXD_OWNER": (cfg.get("instance_name") or "").strip()}
    if legacy:
        env["REFLEXD_LEGACY_BEARER"] = "1"
    if model:
        env["REFLEXD_MODEL"] = model
    if state_dir is not None:
        # The daemon's own bindings, runtime, journals and holds live here, apart from
        # the owner's ~/.quorus (dogfood plan §2). The CLI keeps the owner's config.
        env["QUORUS_CONFIG_DIR"] = str(state_dir)
        _write_private(_state_record(name), str(state_dir))
    else:
        _state_record(name).unlink(missing_ok=True)
    argv = [sys.executable, str(_reflexd_script_path()), "start",
            "--participant", name, "--relay-url", cfg["relay_url"]]
    # Only a daemon that stays up is "running"; otherwise say why (take-3 bug 1).
    offset, secrets = agent_start.size(crash_log), [cred, cfg.get("relay_secret") or ""]
    if not _launchd_ok():
        # No launchd: run detached; `quorus agent remove` stops it via pid.
        with crash_log.open("ab") as out:
            proc = subprocess.Popen(argv, env={**os.environ, **env}, stdout=out,
                                    stderr=out, stdin=subprocess.DEVNULL,
                                    start_new_session=True)
        # Inside a test episode only (explicit env opt-in): register the detached daemon
        # before `agent add` returns, so the episode's teardown owns it.
        from quorus.runtime.episode_hook import register_spawned

        register_spawned(proc)
        pidfile = _agents_dir() / f"{name}.pid"
        _write_private(pidfile, str(proc.pid))
        if not agent_start.stays_up(lambda: proc.pid if proc.poll() is None else None):
            pidfile.unlink(missing_ok=True)
            raise agent_start.not_started(name, crash_log, offset, secrets)
        return f"pid {proc.pid}"
    _LA.mkdir(parents=True, exist_ok=True)
    plist = _LA / f"{label}.plist"
    target = f"gui/{_uid()}/{label}"
    # A crashed job of ours is still loaded, and bootstrap refuses a loaded label.
    subprocess.run(["launchctl", "bootout", target], capture_output=True, check=False,
                   timeout=15)
    _write_private(plist, _plist(label, argv, env, crash_log))
    done = subprocess.run(["launchctl", "bootstrap", f"gui/{_uid()}", str(plist)],
                          capture_output=True, text=True, check=False, timeout=15)
    if done.returncode != 0:
        plist.unlink(missing_ok=True)  # or it loads, and fails, at every login
        said = next((ln.strip() for ln in (done.stderr or "").splitlines() if ln.strip()),
                    f"exit {done.returncode}")
        raise SystemExit(f"agent: @{name} didn't start: launchctl said {said}")
    if not agent_start.stays_up(lambda: _launchd_jobs().get(label)):
        # KeepAlive would restart it every few seconds, forever, and at every login.
        subprocess.run(["launchctl", "bootout", target], capture_output=True, check=False,
                       timeout=15)
        plist.unlink(missing_ok=True)
        raise agent_start.not_started(name, crash_log, offset, secrets)
    return label


def cmd_agent(args: Any, console: Any) -> None:
    action = getattr(args, "agent_action", None)
    if action == "add":
        _add(args, console)
    elif action == "list":
        _list(console, as_json=bool(getattr(args, "json", False)))
    elif action == "remove":
        _remove(args, console)
    elif action == "pace":
        _pace(args, console)
    else:
        console.print("usage: quorus agent add <claude|codex|gemini|opencode> --room <room> "
                      "[--repo PATH] [--mode default|manual|autonomous] "
                      "[--name LABEL] [--model MODEL] [--lead] "
                      "| quorus agent list | quorus agent remove <name>")
        raise SystemExit(5)


def _add(args: Any, console: Any) -> None:
    """`quorus agent add`: prepare (name, credentials, join, binding), then start.
    With ``--reviewer`` the agent is pinned as the room's reviewer in between, so
    its gate exists before it runs (docs/plans/gate-by-default.md)."""
    prep = _prepare(args, console)
    if getattr(args, "reviewer", False):
        from quorus_cli import agent_identity, gate_setup

        try:
            gate_setup.pin_reviewer(prep, replace=getattr(args, "replace", False))
        except agent_identity.IdentityError as exc:
            raise SystemExit(f"agent: no review gate: {exc}") from exc
    _start(prep, console)
    if getattr(args, "reviewer", False):
        from quorus_cli import gate_setup

        line = gate_setup.gate_line(prep["room"], prep["name"], prep["repo"])
        console.print(f"[success]✓[/] {line}")
        for where in gate_setup.where_lines(gate_setup.room_files(prep["room"],
                                                                  [prep["state_dir"]])):
            console.print(where)
        gate_setup.post(prep, line)


def _prepare(args: Any, console: Any) -> dict[str, Any]:
    """Everything before the daemon starts: name, credentials, join, binding and
    lead. Split from :func:`_start` so the first-run wizard can pin a reviewer
    between the two, before any agent can publish (docs/plans/gate-by-default.md)."""
    cfg = _on_owned_address(load_config())
    me = (cfg.get("instance_name") or "").strip()
    if not me or me == "default":
        raise SystemExit("agent: set your name first — `quorus init <your-name>`")
    harness = args.tool
    if shutil.which(harness) is None:
        raise SystemExit(f"agent: the `{harness}` CLI isn't installed or not on PATH — "
                         f"install it and log in first")
    label = (getattr(args, "name", None) or "").strip().lower()
    if label and (len(label) > 20 or not _LABEL_RE.fullmatch(label)):
        raise SystemExit("agent: --name must be 1-20 lowercase letters, digits or "
                         "hyphens, e.g. backend")
    # The tool stays last: the daemon routes by the name's suffix.
    suffix = f"{label}-{harness}" if label else harness
    name = f"{me}-{suffix}"
    model = (getattr(args, "model", None) or "").strip() or None
    for warning in _readiness_warnings(harness, model):
        console.print(f"[warning]![/] {warning}")
    repo = Path(args.repo).expanduser().resolve() if args.repo else None
    if repo is not None and not repo.is_dir():
        raise SystemExit(f"agent: not a directory: {repo}")
    state_dir = _state_dir(getattr(args, "state_dir", None))
    existing = _running_daemon_for(name, cfg.get("relay_url"))
    running_in = _daemon_state_dir(name) if existing else None
    if existing and running_in != state_dir:
        # A running daemon keeps the state dir it started with: binding into another
        # one would report success while the daemon never reads it (Codex on 246acab9).
        raise SystemExit(f"agent: @{name} is already running ({existing}) with its state "
                         f"in {running_in or 'your own config dir'}; run `quorus agent "
                         f"remove {name}` first, then add it again")
    wants = getattr(args, "trust", None) or args.mode is not None
    if repo is None and wants:
        # --trust/--mode live on the room's workspace binding: without --repo they
        # were dropped silently, so a friend's asks stayed read-only (audit 0.6.3,
        # bug 9). Use the room's existing workspace, or say what's missing.
        repo = _bound_path(args.room, state_dir)
        if repo is None:
            flag = "--trust" if getattr(args, "trust", None) else "--mode"
            raise SystemExit(f"agent: {flag} applies to the room's workspace, and "
                             f"#{args.room} has none on this machine yet; add "
                             f"--repo <path>")
    member = _on_teammates_relay(cfg)
    mode = args.mode
    if (member and repo is not None and mode == "autonomous"
            and not getattr(args, "disclosed", False)):
        console.print(f"[dim]{TEAMMATE_RELAY_NOTE}[/]")
        if sys.stdin.isatty():
            from rich.prompt import Confirm

            if not Confirm.ask(f"Let @{name} edit files and run commands in {repo} when "
                               "you ask?", default=False):
                mode = None  # Enter keeps each tool's own permissions
        # Not a terminal: the explicit --mode autonomous is the consent.
    cred, legacy = _credentials(cfg, name, suffix)
    if member:
        _pin_owner(cfg, name)
    # Adding members requires the room owner/admin, not the child agent
    # (self-join is deliberately forbidden even if it inherited membership).
    join_cred = cred if legacy else cfg["api_key"]
    bearer = _bearer(cfg, join_cred, legacy)
    _join(cfg, args.room, name, bearer)
    console.print(f"[success]✓[/] @{name} joined #{args.room}")
    if repo is not None:
        mode = _bind(args.room, repo, mode, getattr(args, "trust", None), state_dir)
        console.print(f"[success]✓[/] #{args.room} works in {repo} [dim]({mode})[/]")
    if getattr(args, "lead", False):
        previous = _read_leads().get(args.room)
        _set_lead(args.room, name)
        swap = f" [dim](was @{previous})[/]" if previous and previous != name else ""
        console.print(f"[success]✓[/] @{name} leads #{args.room}: it splits your requests "
                      f"into tasks and assigns them{swap}")
    return {"cfg": cfg, "me": me, "name": name, "room": args.room, "repo": repo,
            "model": model, "state_dir": state_dir, "existing": existing,
            "cred": cred, "legacy": legacy, "bearer": bearer}


def _start(prep: dict[str, Any], console: Any) -> None:
    """Start (or reuse) the prepared agent's daemon."""
    cfg, name, room = prep["cfg"], prep["name"], prep["room"]
    repo, model, state_dir = prep["repo"], prep["model"], prep["state_dir"]
    existing, cred, legacy = prep["existing"], prep["cred"], prep["legacy"]
    if existing:
        console.print(f"[success]✓[/] @{name} is already running [dim]({existing})[/] "
                      "— it now also listens to this room")
        if model:
            console.print(f"[dim]--model only applies to a new agent: run `quorus agent "
                          f"remove {name}` and add it again to switch models[/]")
    else:
        if legacy:  # its key may be saved in another dir: reuse it, don't claim again
            from quorus_cli import agent_identity

            agent_identity.adopt_key(cfg["relay_url"], name, state_dir)
        _start_daemon(cfg, name, cred, legacy, model, state_dir)
        # Only launchd brings it back; elsewhere it is a plain detached process.
        lasts = ("restarts at login" if sys.platform == "darwin" else
                 "keeps running after you close this terminal, but not after a "
                 "reboot: run this command again then")
        console.print(f"[success]✓[/] @{name} is running [dim]({lasts})[/]")
    if repo is not None or _room_bound(room, state_dir):
        console.print(f"\n[dim]Try it:[/] quorus say {room} \"@{name} hello, what "
                      "can you do in this repo\"")
    else:
        console.print(f"\n[dim]Give it a repo to work in:[/] quorus room bind "
                      f"{room} <path>")


def _room_bound(room: str, state_dir: Path | None = None) -> bool:
    try:
        data = json.loads(bindings_path(state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and bool(data.get(room))


def _gemini_has_key() -> bool:
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return True
    try:
        text = (Path.home() / ".gemini" / ".env").read_text(encoding="utf-8")
    except OSError:
        return False
    return any(line.split("=", 1)[0].strip() in ("GEMINI_API_KEY", "GOOGLE_API_KEY")
               for line in text.splitlines() if "=" in line)


def _opencode_version() -> str | None:
    exe = shutil.which("opencode")
    if exe is None:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True,
                             timeout=20, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"\d+\.\d+(\.\d+)?", out)
    return m.group(0) if m else None


def _ollama_models() -> list[str] | None:
    try:
        resp = httpx.get("http://127.0.0.1:11434/api/tags", timeout=3)
        return [m.get("name", "") for m in resp.json().get("models", [])]
    except (httpx.HTTPError, ValueError, AttributeError):
        return None


def _readiness_warnings(harness: str, model: str | None) -> list[str]:
    """Problems that would make every wake fail, said up front instead of in
    the room later."""
    from quorus_cli.quickstart import _claude_logged_in, _codex_logged_in

    out: list[str] = []
    if harness == "claude" and _claude_logged_in() is False:
        out.append("Claude Code isn't signed in, so every wake would answer \"not "
                   "logged in\". Run `claude /login` first")
    if harness == "codex" and not _codex_logged_in():
        out.append("Codex isn't signed in, so every wake would fail. Run `codex login` first")
    if harness == "gemini" and not _gemini_has_key():
        out.append("Gemini CLI needs an API key: Quorus runs it in its own Gemini home and "
                   "doesn't use your Google sign-in. Get a key at "
                   "https://aistudio.google.com/apikey and put GEMINI_API_KEY=<key> "
                   "in ~/.gemini/.env")
    if harness == "gemini" and not model:
        out.append("Free Gemini API keys have a low daily request limit, and one agent "
                   "turn can use a dozen requests. For an always-on agent, add "
                   "`--model gemini-3.5-flash-lite`")
    if harness == "opencode":
        version = _opencode_version()
        if version and int(version.split(".")[0]) < 2:
            # 1.x works (the daemon drops --standalone) but is what Quorus was
            # least tested on, and its old permission format is the fallback.
            out.append(f"opencode {version} is old: Quorus is tested on opencode 2. "
                       "Upgrade with `npm i -g @opencode/cli` or `brew install opencode` "
                       "(the `opencode-ai` npm package is still opencode 1)")
    if harness == "opencode" and not model:
        out.append("no --model given, so opencode uses its own default. Pick one with "
                   "--model, e.g. ollama/qwen3:4b (local) or "
                   "openrouter/qwen/qwen3-coder (see `opencode models`)")
    if model and model.startswith("ollama/"):
        if harness != "opencode":
            out.append("ollama/ models run through opencode: use "
                       "`quorus agent add opencode --model " + model + "`")
        names = _ollama_models()
        want = model.split("/", 1)[1]
        if names is None:
            out.append("Ollama isn't running: start it with "
                       "`OLLAMA_CONTEXT_LENGTH=32768 ollama serve`")
        elif want not in names and f"{want}:latest" not in names:
            out.append(f"Ollama doesn't have {want} yet: run `ollama pull {want}`")
        out.append("local models need Ollama's context raised to 32k+ "
                   "(OLLAMA_CONTEXT_LENGTH=32768) and run slowly on laptops")
    return out


def _list(console: Any, *, as_json: bool = False) -> None:
    """Your agents only: <you>-<tool>. Listing every quorus launchd job showed
    other identities' agents too (review 2026-10-09). Without launchd the
    agents are pid files; a dead one is listed so a reboot isn't silent."""
    me = (load_config().get("instance_name") or "").strip()
    jobs = {lb: pid for lb, pid in _launchd_jobs().items()
            if ("agent." in lb or "reflexd." in lb)
            and lb.rsplit(".", 1)[-1].startswith(f"{me}-")}
    rows = [(lb.rsplit(".", 1)[-1], lb, pid is not None) for lb, pid in jobs.items()]
    seen = {name for name, _, _ in rows}
    for pf in sorted(_agents_dir().glob(f"{me}-*.pid")) if me else []:
        if pf.stem not in seen:
            pid = agent_proc.live_pid(_agents_dir(), pf.stem)
            rows.append((pf.stem, f"pid {pid}" if pid else "", pid is not None))
    if as_json:
        sys.stdout.write(json.dumps(
            [{"name": name, "running": up, "where": where}
             for name, where, up in sorted(rows)], indent=2) + "\n")
        return
    if not rows:
        console.print(f"[dim]no agents for @{me} running on this machine — add one: "
                      "quorus agent add claude --room <room>[/]")
        return
    for name, where, up in sorted(rows):
        if up:
            console.print(f"  [success]●[/] @{name}  [dim]{where}[/]")
        else:
            console.print(f"  [error]○[/] @{name}  [dim]stopped (reboot or crash): start "
                          "it with the same `quorus agent add` command[/]")


def _remove(args: Any, console: Any) -> None:
    label = LABEL_PREFIX + args.name
    plist = _LA / f"{label}.plist"
    was_running = (plist.exists()
                   or agent_proc.live_pid(_agents_dir(), args.name) is not None)
    relay_url = (load_config().get("relay_url") or "").strip()
    # Before the state record goes: it names one of the dirs a key may be saved in.
    held = agent_release.held_keys(args.name, relay_url) if relay_url else {}
    if _launchd_ok():  # no launchctl elsewhere: it crashed `remove` on Linux
        subprocess.run(["launchctl", "bootout", f"gui/{_uid()}/{label}"],
                       capture_output=True, check=False, timeout=15)
        plist.unlink(missing_ok=True)
    agent_proc.stop(_agents_dir(), args.name)
    _state_record(args.name).unlink(missing_ok=True)
    _drop_lead_everywhere(args.name)
    left = _leave_all_rooms(args.name)
    freed = agent_release.release(args.name, relay_url, held) if relay_url else None
    if not was_running and not left:
        console.print(f"[error]✗[/] no agent @{args.name} here — see [accent]quorus agent "
                      "list[/]")
        raise SystemExit(4)
    rooms = ", ".join("#" + r for r in left)
    if was_running:
        where = f" and left {rooms}" if left else ""
        console.print(f"[success]✓[/] stopped @{args.name} on this machine{where}")
    else:  # bug bash: "✓ was not running; @x on this machine and left #lab"
        console.print(f"[success]✓[/] @{args.name} wasn't running on this machine; "
                      f"it left {rooms}")
    if freed:
        console.print(freed)


def _leave_all_rooms(name: str) -> list[str]:
    """A stopped agent must not stay in rooms: teammates would keep handing
    it work nobody will ever pick up (seen live 2026-10-09)."""
    cfg = load_config()
    me = (cfg.get("instance_name") or "").strip()
    suffix = name[len(me) + 1:] if me and name.startswith(f"{me}-") else name.rsplit("-", 1)[-1]
    try:
        cred, legacy = _credentials(cfg, name, suffix)
        headers = {"Authorization": f"Bearer {_bearer(cfg, cred, legacy)}"}
        rooms = httpx.get(f"{cfg['relay_url']}/rooms", headers=headers, timeout=10).json()
    except (httpx.HTTPError, SystemExit, ValueError):
        return []
    left = []
    for room in rooms if isinstance(rooms, list) else []:
        if name in (room.get("members") or []):
            resp = httpx.post(f"{cfg['relay_url']}/rooms/{room['id']}/leave",
                              json={"participant": name}, headers=headers, timeout=10)
            if resp.status_code < 400:
                left.append(room.get("name") or room["id"])
    return left
