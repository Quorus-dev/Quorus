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
from quorus_cli import agent_proc

# opencode = any model: Ollama (local), OpenRouter, DeepSeek, Qwen, Gemini...
HARNESSES = ("claude", "codex", "gemini", "opencode")
_LABEL_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
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


def _uid() -> int:
    return os.getuid()


def _launchd_labels() -> list[str]:
    if sys.platform != "darwin":
        return []
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True,
                             timeout=10, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.split()[-1] for line in out.splitlines() if "quorus" in line]


def _running_daemon_for(name: str) -> str | None:
    """An already-running wake daemon for *name*: a launchd job (ours or
    dogfood's) OR any reflexd process for it (e.g. started by the legacy
    reflexd-manager or `quorus reflexd start`). Two daemons for one agent
    answer every message twice. The pid file comes first: minimal Linux
    images ship without pgrep."""
    pid = agent_proc.live_pid(_agents_dir(), name)
    if pid is not None:
        return f"pid {pid}"
    for label in _launchd_labels():
        if label.endswith(f".{name}") and ("agent." in label or "reflexd." in label):
            return label
    try:
        out = subprocess.run(["pgrep", "-f", f"reflexd.py start.*--participant {name}( |$)"],
                             capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    pids = out.stdout.split()
    return f"pid {pids[0]}" if pids else None


def _credentials(cfg: dict[str, Any], name: str, harness: str) -> tuple[str, bool]:
    """(credential, legacy) for the agent. Account relays mint a key per agent
    (cached 0600 so re-runs reuse it); shared-secret relays use the secret."""
    if cfg.get("api_key"):
        cache = _agents_dir() / f"{name}.json"
        if cache.exists():
            key = json.loads(cache.read_text(encoding="utf-8")).get("api_key")
            if key:
                return key, False
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
    resp.raise_for_status()


def _bind(room: str, repo: Path, mode: str | None) -> str:
    path = Path.home() / ".quorus" / "room-bindings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    prev = data.get(room)
    if mode is None:  # keep the room's existing mode
        mode = prev.get("mode", "default") if isinstance(prev, dict) else "default"
    data[room] = str(repo) if mode == "default" else {"path": str(repo), "mode": mode}
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_private(path, json.dumps(data, indent=2))
    return mode


def _harness_path() -> str:
    parts = [str(Path(sys.executable).parent), "/usr/bin", "/bin", "/usr/sbin", "/sbin",
             "/opt/homebrew/bin", "/usr/local/bin"]
    for b in HARNESSES:
        w = shutil.which(b)
        if w:
            parts.insert(0, str(Path(w).parent))
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
                  model: str | None = None) -> str:
    from quorus.runtime.supervisor import _reflexd_script_path

    label = LABEL_PREFIX + name
    log = _agents_dir() / f"{name}.log"
    crash_log = _agents_dir() / f"{name}.crash.log"  # launchd stdout/stderr
    env = {"PATH": _harness_path(), "RELAY_URL": cfg["relay_url"], "API_KEY": cred,
           "REFLEXD_PARTICIPANT": name, "PYTHONUNBUFFERED": "1",
           "REFLEXD_LOG_PATH": str(log),
           # The daemon reads room bindings from the same home `agent add`
           # wrote them to (launchd would otherwise use the login HOME).
           "HOME": str(Path.home())}
    if legacy:
        env["REFLEXD_LEGACY_BEARER"] = "1"
    if model:
        env["REFLEXD_MODEL"] = model
    argv = [sys.executable, str(_reflexd_script_path()), "start",
            "--participant", name, "--relay-url", cfg["relay_url"]]
    if sys.platform != "darwin":
        # No launchd: run detached; `quorus agent remove` stops it via pid.
        with crash_log.open("ab") as out:
            proc = subprocess.Popen(argv, env={**os.environ, **env}, stdout=out,
                                    stderr=out, stdin=subprocess.DEVNULL,
                                    start_new_session=True)
        _write_private(_agents_dir() / f"{name}.pid", str(proc.pid))
        return f"pid {proc.pid}"
    _LA.mkdir(parents=True, exist_ok=True)
    plist = _LA / f"{label}.plist"
    _write_private(plist, _plist(label, argv, env, crash_log))
    subprocess.run(["launchctl", "bootstrap", f"gui/{_uid()}", str(plist)],
                   capture_output=True, check=False, timeout=15)
    return label


def cmd_agent(args: Any, console: Any) -> None:
    action = getattr(args, "agent_action", None)
    if action == "add":
        _add(args, console)
    elif action == "list":
        _list(console)
    elif action == "remove":
        _remove(args, console)
    else:
        console.print("usage: quorus agent add <claude|codex|gemini|opencode> --room <room> "
                      "[--repo PATH] [--mode default|manual|autonomous] "
                      "[--name LABEL] [--model MODEL] "
                      "| quorus agent list | quorus agent remove <name>")
        raise SystemExit(5)


def _add(args: Any, console: Any) -> None:
    cfg = load_config()
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
    cred, legacy = _credentials(cfg, name, suffix)
    # Adding members requires the room owner/admin, not the child agent
    # (self-join is deliberately forbidden even if it inherited membership).
    join_cred = cred if legacy else cfg["api_key"]
    _join(cfg, args.room, name, _bearer(cfg, join_cred, legacy))
    console.print(f"[success]✓[/] @{name} joined #{args.room}")
    if repo is not None:
        mode = _bind(args.room, repo, args.mode)
        console.print(f"[success]✓[/] #{args.room} works in {repo} [dim]({mode})[/]")
    existing = _running_daemon_for(name)
    if existing:
        console.print(f"[success]✓[/] @{name} is already running [dim]({existing})[/] "
                      "— it now also listens to this room")
        if model:
            console.print(f"[dim]--model only applies to a new agent: run `quorus agent "
                          f"remove {name}` and add it again to switch models[/]")
    else:
        where = _start_daemon(cfg, name, cred, legacy, model)
        # Only launchd brings it back; elsewhere it is a plain detached process.
        lasts = ("restarts at login" if sys.platform == "darwin" else
                 "keeps running after you close this terminal, but not after a "
                 "reboot: run this command again then")
        console.print(f"[success]✓[/] @{name} is running [dim]({where}; {lasts})[/]")
    if repo is not None or _room_bound(args.room):
        console.print(f"\n[dim]Try it:[/] quorus say {args.room} \"@{name} hello, what "
                      "can you do in this repo\"")
    else:
        console.print(f"\n[dim]Give it a repo to work in:[/] quorus room bind "
                      f"{args.room} <path>")


def _room_bound(room: str) -> bool:
    try:
        data = json.loads((Path.home() / ".quorus" / "room-bindings.json")
                          .read_text(encoding="utf-8"))
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
    out: list[str] = []
    if harness == "gemini" and not _gemini_has_key():
        out.append("Gemini CLI needs an API key: Google no longer lets it sign in with "
                   "a personal Google account. Get a free key at "
                   "https://aistudio.google.com/apikey and put GEMINI_API_KEY=<key> "
                   "in ~/.gemini/.env")
    if harness == "opencode":
        version = _opencode_version()
        if version and int(version.split(".")[0]) < 2:
            # The wake runs `opencode run --standalone`, which 1.x rejects.
            out.append(f"opencode {version} is too old: Quorus needs opencode 2, so "
                       "every wake would fail. Upgrade with `npm i -g @opencode/cli` "
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


def _list(console: Any) -> None:
    """Your agents only: <you>-<tool>. Listing every quorus launchd job showed
    other identities' agents too (review 2026-10-09). Without launchd the
    agents are pid files; a dead one is listed so a reboot isn't silent."""
    me = (load_config().get("instance_name") or "").strip()
    labels = [lb for lb in _launchd_labels()
              if ("agent." in lb or "reflexd." in lb)
              and lb.rsplit(".", 1)[-1].startswith(f"{me}-")]
    rows = [(lb.rsplit(".", 1)[-1], lb, True) for lb in labels]
    seen = {name for name, _, _ in rows}
    for pf in sorted(_agents_dir().glob(f"{me}-*.pid")) if me else []:
        if pf.stem not in seen:
            pid = agent_proc.live_pid(_agents_dir(), pf.stem)
            rows.append((pf.stem, f"pid {pid}" if pid else "", pid is not None))
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
    if sys.platform == "darwin":  # no launchctl elsewhere: it crashed `remove` on Linux
        subprocess.run(["launchctl", "bootout", f"gui/{_uid()}/{label}"],
                       capture_output=True, check=False, timeout=15)
        plist.unlink(missing_ok=True)
    agent_proc.stop(_agents_dir(), args.name)
    left = _leave_all_rooms(args.name)
    if not was_running and not left:
        console.print(f"[error]✗[/] no agent @{args.name} here — see [accent]quorus agent "
                      "list[/]")
        raise SystemExit(4)
    what = "stopped" if was_running else "was not running;"
    where = f" and left {', '.join('#' + r for r in left)}" if left else ""
    console.print(f"[success]✓[/] {what} @{args.name} on this machine{where}")


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
