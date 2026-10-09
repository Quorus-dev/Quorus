#!/bin/bash
# Quorus dogfood setup — one laptop, always on, real agents.
#
#   scripts/dogfood.sh up <repo-path> [room]   relay + agents under launchd,
#                                              room bound to <repo-path>
#   scripts/dogfood.sh status                  health, agents, room presence
#   scripts/dogfood.sh down                    stop everything (state kept)
#   scripts/dogfood.sh logs [agent]            tail an agent's daemon log
#   scripts/dogfood.sh report [room]           what the agents shipped
#   scripts/dogfood.sh connect [room]          point YOUR Claude Code + Codex
#                                              MCP config at this relay
#   scripts/dogfood.sh claude [args]           open Claude Code with room
#                                              messages pushed in live
#
# What "up" builds (macOS launchd, restarts on crash and at login):
#   • relay   http://127.0.0.1:$PORT, local-only, state in ~/.quorus/dogfood/
#   • agents  one wake daemon per agent (default arav-claude, arav-codex);
#             each runs the real `claude` / `codex` CLI in the bound repo
#   • you     a `dogfood` profile so `quorus` (the hub) talks as @$HUMAN
#
# Env overrides: QUORUS_HUMAN (default: arav), QUORUS_AGENTS (space-separated,
# names must end in -claude/-codex/-gemini), QUORUS_DOGFOOD_PORT (8787).
# Permissions: woken Claude agents use your own Claude Code settings. Woken
# Codex agents get `-s workspace-write` (writes limited to the bound repo) —
# Arav opted in 2026-10-08; set QUORUS_CODEX_SANDBOX=read-only to undo.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_BIN="$REPO_ROOT/.venv/bin"
DIR="$HOME/.quorus/dogfood"
PORT="${QUORUS_DOGFOOD_PORT:-8787}"
URL="http://127.0.0.1:$PORT"
HUMAN="${QUORUS_HUMAN:-arav}"
AGENTS="${QUORUS_AGENTS:-$HUMAN-claude $HUMAN-codex}"
LA="$HOME/Library/LaunchAgents"
PREFIX="dev.quorus.dogfood"

die() { echo "dogfood: $*" >&2; exit 1; }
ok()  { printf "  \033[32m✓\033[0m %s\n" "$*"; }

secret() {
  mkdir -p "$DIR"; chmod 700 "$DIR"
  if [[ ! -s "$DIR/secret" ]]; then
    (umask 077; openssl rand -hex 24 > "$DIR/secret")
  fi
  cat "$DIR/secret"
}

api() { # api METHOD PATH [json]
  curl -fsS -X "$1" -H "Authorization: Bearer $(secret)" \
    -H 'Content-Type: application/json' ${3:+-d "$3"} "$URL$2"
}

xml() { python3 -c 'import sys,html;print(html.escape(sys.argv[1]))' "$1"; }

plist() { # plist LABEL LOGFILE ENV_PAIRS... -- ARGV...
  local label="$1" log="$2"; shift 2
  local envs="" args=""
  while [[ $# -gt 0 && "$1" != "--" ]]; do
    envs+="<key>${1%%=*}</key><string>$(xml "${1#*=}")</string>"; shift
  done
  shift
  for a in "$@"; do args+="<string>$(xml "$a")</string>"; done
  mkdir -p "$LA"
  (umask 077; cat > "$LA/$label.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>$label</string>
<key>ProgramArguments</key><array>$args</array>
<key>EnvironmentVariables</key><dict>$envs</dict>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
<key>ThrottleInterval</key><integer>5</integer>
<key>StandardOutPath</key><string>$log</string>
<key>StandardErrorPath</key><string>$log</string>
</dict></plist>
EOF
  )
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  # bootout returns before the job is gone; bootstrapping too early fails
  # with "Bootstrap failed: 5: Input/output error".
  for _ in $(seq 1 40); do
    launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1 || break
    sleep 0.25
  done
  launchctl bootstrap "gui/$(id -u)" "$LA/$label.plist"
}

harness_path() {
  # launchd starts with a bare PATH; the agents need claude/codex/gemini.
  local p="$VENV_BIN:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"
  for b in claude codex gemini; do
    local w; w="$(command -v "$b" 2>/dev/null || true)"
    [[ -n "$w" ]] && p="$(dirname "$w"):$p"
  done
  echo "$p"
}

cmd_up() {
  local repo="${1:-}" room="${2:-build}"
  [[ -n "$repo" ]] || die "usage: dogfood.sh up <repo-path> [room]"
  repo="$(cd "$repo" && pwd)" || die "no such directory: $1"
  [[ -x "$VENV_BIN/quorus-relay" ]] || die "missing $VENV_BIN — run setup.sh first"
  for a in $AGENTS; do
    [[ "$a" =~ -(claude|codex|gemini)$ ]] || die "agent '$a' must end in -claude/-codex/-gemini"
    command -v "${a##*-}" >/dev/null || die "'${a##*-}' CLI not found on PATH (needed by $a)"
  done
  local s; s="$(secret)"
  echo "Quorus dogfood → $URL  room=#$room  repo=$repo"

  plist "$PREFIX.relay" "$DIR/relay.log" \
    PORT="$PORT" HOST=127.0.0.1 RELAY_SECRET="$s" ALLOW_LEGACY_AUTH=1 \
    MESSAGES_FILE="$DIR/state.json" LOG_LEVEL=WARNING -- "$VENV_BIN/quorus-relay"
  for _ in $(seq 1 40); do curl -fsS -m 1 "$URL/health" >/dev/null 2>&1 && break; sleep 0.5; done
  curl -fsS -m 2 "$URL/health" >/dev/null || die "relay not healthy — see $DIR/relay.log"
  ok "relay up (local only, auto-restarts)"

  api POST /rooms "{\"name\":\"$room\",\"created_by\":\"$HUMAN\"}" >/dev/null 2>&1 || true
  for who in $HUMAN $AGENTS; do
    api POST "/rooms/$room/join" "{\"participant\":\"$who\"}" >/dev/null
  done
  ok "room #$room: $HUMAN + $AGENTS"

  python3 - "$room" "$repo" <<'PY'
import json, os, pathlib, sys
p = pathlib.Path.home() / ".quorus" / "room-bindings.json"
try:
    d = json.loads(p.read_text() or "{}")
except (OSError, ValueError):
    d = {}
d[sys.argv[1]] = sys.argv[2]
fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(d, f, indent=2)
PY
  ok "#$room bound to $repo"

  local path; path="$(harness_path)"
  for a in $AGENTS; do
    plist "$PREFIX.reflexd.$a" "$DIR/$a.log" \
      PATH="$path" RELAY_URL="$URL" API_KEY="$s" REFLEXD_PARTICIPANT="$a" \
      REFLEXD_LEGACY_BEARER=1 PYTHONUNBUFFERED=1 \
      REFLEXD_CODEX_SANDBOX="${QUORUS_CODEX_SANDBOX:-workspace-write}" -- \
      "$VENV_BIN/python3" "$REPO_ROOT/scripts/reflexd.py" start --debug \
      --participant "$a" --relay-url "$URL"
  done
  for a in $AGENTS; do
    for _ in $(seq 1 40); do grep -q "sse connected" "$DIR/$a.log" 2>/dev/null && break; sleep 0.5; done
    grep -q "sse connected" "$DIR/$a.log" && ok "$a listening" || echo "  ✗ $a not connected — $DIR/$a.log"
  done

  # Your identity for the hub: a separate profile, previous one kept.
  python3 - "$HUMAN" "$URL" "$s" <<'PY'
import json, os, pathlib, sys
q = pathlib.Path.home() / ".quorus"
prof = q / "profiles" / "dogfood.json"
prof.parent.mkdir(parents=True, exist_ok=True)
fd = os.open(prof, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump({"instance_name": sys.argv[1], "relay_url": sys.argv[2],
               "relay_secret": sys.argv[3]}, f, indent=2)
cfg_p = q / "config.json"
cfg = json.loads(cfg_p.read_text()) if cfg_p.exists() else {}
if cfg.get("current") != "dogfood":
    cfg["previous"] = cfg.get("current")
cfg["current"] = "dogfood"
cfg["profiles"] = sorted(set(cfg.get("profiles", [])) | {"dogfood"})
fd = os.open(cfg_p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(cfg, f, indent=2)
PY
  ok "hub profile 'dogfood' active (you are @$HUMAN)"
  cat <<EOF

Open the chat:     quorus            (or: quorus chat $room)
Give work:         @open <task>      → the first free agent takes it
Ask one agent:     @${AGENTS%% *} <request>
Watch an agent:    scripts/dogfood.sh logs ${AGENTS%% *}
Bind another repo: quorus room bind <room> <path>
EOF
}

cmd_connect() {
  # Point YOUR interactive Claude Code + Codex at this relay so they can read
  # and post in the room. Separate "-desktop" identities: they must not drain
  # the inboxes the background agents (arav-claude / arav-codex) wake on.
  local room="${1:-build}" s; s="$(secret)"
  local stamp; stamp="$(date +%Y%m%d-%H%M%S)"
  local cl="$HUMAN-claude-desktop" cx="$HUMAN-codex-desktop"
  for who in $cl $cx; do api POST "/rooms/$room/join" "{\"participant\":\"$who\"}" >/dev/null; done
  if command -v claude >/dev/null; then
    cp ~/.claude.json "$DIR/claude.json.bak-$stamp"
    claude mcp remove quorus -s user >/dev/null 2>&1 || true
    claude mcp add-json quorus -s user "$(python3 -c '
import json, sys
print(json.dumps({"type": "stdio", "command": sys.argv[1], "args": ["-m", "quorus_mcp.server"],
  "env": {"QUORUS_RELAY_URL": sys.argv[2], "QUORUS_INSTANCE_NAME": sys.argv[3],
          "QUORUS_RELAY_SECRET": sys.argv[4], "QUORUS_API_KEY": ""}}))' \
      "$VENV_BIN/python3" "$URL" "$cl" "$s")" >/dev/null
    ok "Claude Code → $URL as @$cl (backup: $DIR/claude.json.bak-$stamp)"
  fi
  if [[ -f ~/.codex/config.toml ]]; then
    cp ~/.codex/config.toml "$DIR/codex-config.toml.bak-$stamp"
    python3 - "$VENV_BIN/python3" "$URL" "$cx" "$s" <<'PY2'
import json, pathlib, re, sys
p = pathlib.Path.home() / ".codex" / "config.toml"
text = p.read_text()
# drop any existing [mcp_servers.quorus] / [mcp_servers.quorus.env] tables
text = re.sub(r"(?ms)^\[mcp_servers\.quorus(\.env)?\]\n.*?(?=^\[|\Z)", "", text)
q = json.dumps
block = (f"[mcp_servers.quorus]\ncommand = {q(sys.argv[1])}\nargs = [\"-m\", \"quorus_mcp.server\"]\n\n"
         f"[mcp_servers.quorus.env]\nQUORUS_RELAY_URL = {q(sys.argv[2])}\n"
         f"QUORUS_INSTANCE_NAME = {q(sys.argv[3])}\nQUORUS_RELAY_SECRET = {q(sys.argv[4])}\n"
         # blank on purpose: a shell-exported QUORUS_API_KEY (e.g. from a
         # keychain line in ~/.zshrc) would otherwise beat the relay secret
         "QUORUS_API_KEY = \"\"\n\n")
p.write_text(text.rstrip("\n") + "\n\n" + block)
PY2
    ok "Codex → $URL as @$cx (backup: $DIR/codex-config.toml.bak-$stamp)"
  fi
  echo "  Restart your open Claude Code / Codex sessions to pick this up."
}

cmd_report() { # report [room] — what the agents did, for the morning check
  local room="${1:-build}"
  api GET "/rooms/$room/history?limit=500" | python3 -c '
import json, sys, collections
msgs = json.load(sys.stdin)
by = collections.Counter(m["from_name"] for m in msgs)
pub = [m for m in msgs if "published" in (m.get("content") or "")
       and "commit(s) from quorus/" in (m.get("content") or "")]
blocked = [m for m in msgs if any(k in (m.get("content") or "") for k in
           ("needs a manual merge", "not publishing", "[reflexd]", "publishing quorus/"))]
print("messages by sender:", dict(by))
print("publishes to main:", len(pub))
print("blocked/error lines:", len(blocked))
if msgs:
    print("first:", msgs[0]["timestamp"][:19], " last:", msgs[-1]["timestamp"][:19])'
  local repo; repo="$(python3 -c 'import json,sys,pathlib;e=json.loads((pathlib.Path.home()/".quorus/room-bindings.json").read_text()).get(sys.argv[1],"");print(e.get("path","") if isinstance(e,dict) else e)' "$room")"
  if [[ -n "$repo" ]]; then
    echo "repo $repo:"; git -C "$repo" log --oneline -15 | sed 's/^/  /'
    (cd "$repo" && python3 -m pytest -q 2>&1 | tail -1 | sed 's/^/  tests: /')
  fi
}

cmd_down() {
  for f in "$LA/$PREFIX".*.plist; do
    [[ -e "$f" ]] || continue
    launchctl bootout "gui/$(id -u)/$(basename "$f" .plist)" 2>/dev/null || true
    rm -f "$f"
  done
  python3 - <<'PY'
import json, pathlib
p = pathlib.Path.home() / ".quorus" / "config.json"
if p.exists():
    c = json.loads(p.read_text())
    if c.get("current") == "dogfood" and c.get("previous"):
        c["current"] = c.pop("previous")
        p.write_text(json.dumps(c, indent=2))
PY
  ok "stopped (state kept in $DIR)"
}

cmd_status() {
  curl -fsS -m 2 "$URL/health" >/dev/null 2>&1 && ok "relay healthy at $URL" || echo "  ✗ relay down"
  launchctl list | grep "$PREFIX" || echo "  (no dogfood services loaded)"
  for r in $(api GET /rooms 2>/dev/null | python3 -c 'import sys,json;[print(r["name"]) for r in json.load(sys.stdin)]' 2>/dev/null); do
    api GET "/rooms/$r" | python3 -c '
import sys, json
r = json.load(sys.stdin)
pres = r.get("member_presence", {})
people = ", ".join("%s (%s)" % (m, v.get("presence")) for m, v in pres.items())
print("  #%s: %s" % (r["name"], people))'
  done
}

case "${1:-}" in
  up) shift; cmd_up "$@" ;;
  connect) shift; cmd_connect "$@" ;;
  claude)
    # Channels are a research preview: custom servers need this flag, and
    # Claude Code asks you to confirm "I am using this for local development".
    shift; exec claude --dangerously-load-development-channels server:quorus "$@" ;;
  down) cmd_down ;;
  status) cmd_status ;;
  report) shift; cmd_report "$@" ;;
  logs) tail -n 60 -f "$DIR/${2:-${AGENTS%% *}}.log" ;;
  *) sed -n 2,19p "$0"; exit 1 ;;
esac
