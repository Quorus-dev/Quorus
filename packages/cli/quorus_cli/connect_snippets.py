"""`quorus connect` for platforms with no config file to write: ollama, antigravity,
aider and http print a snippet to paste instead.

The snippets never contain a credential (docs/plans/connect-secret-snippets.md).
Before 0.7.6 each embedded the relay secret as a literal, so a screenshot, a pasted
terminal or an agent's system prompt carried the operator credential. Now the Python
and curl snippets read it from the environment, and the antigravity MCP config leaves
the secret to ``quorus-mcp`` when it is the saved one. On relays that use API keys
the agent gets its own child key (``connect_agent_key``), never the user's.

Snippets print with ``soft_wrap``: a hard wrap at the terminal width splits a curl
line or a string literal, and the pasted copy no longer runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from quorus.config import DEFAULT_RELAY_URL, ConfigManager, resolve_config_file, same_relay
from quorus.profiles import ProfileManager
from quorus_cli import ui

ENV_VAR = "QUORUS_RELAY_SECRET"
KEY_ENV_VAR = "QUORUS_API_KEY"
PLACEHOLDER = "<paste your RELAY_SECRET>"
KEY_PLACEHOLDER = "<paste the agent key>"


@dataclass(frozen=True)
class Cred:
    """How a snippet reaches the credential, never the credential itself."""

    kind: str     # "secret" or "api_key"
    env_var: str  # what the Python and curl snippets read
    where: str    # where the user finds the value
    saved: bool   # quorus-mcp finds it on its own (antigravity leaves it out)


def secret_is_saved(relay_url: str, secret: str) -> bool:
    """Is *secret* the one the profile saves for *relay_url*? Then ``quorus-mcp``
    finds it on its own; otherwise it came from this shell's ``RELAY_SECRET``."""
    saved = ConfigManager().load(migrate=False)
    return bool(secret) and secret == saved.get("relay_secret") and same_relay(
        relay_url, saved.get("relay_url") or DEFAULT_RELAY_URL)


def saved_credential_file() -> Path:
    """The file ``ConfigManager().load(migrate=False)`` reads credentials from: the
    current profile (``profiles/<name>.json``), or a legacy flat ``config.json``
    (Codex on 09807d75: the hint named ``config.json`` for profile layouts too)."""
    pm = ProfileManager()
    if pm.legacy_flat() is not None:
        return pm.pointer_path
    slug = pm.current()
    return pm.profiles_dir / f"{slug}.json" if slug else resolve_config_file()


def secret_cred(relay_url: str, secret: str) -> Cred:
    if secret_is_saved(relay_url, secret):
        return Cred("secret", ENV_VAR, f"the relay_secret saved in {saved_credential_file()}",
                    True)
    return Cred("secret", ENV_VAR,
                f'the RELAY_SECRET set in this shell (export {ENV_VAR}="$RELAY_SECRET")', False)


def agent_key_cred(agent: str) -> Cred:
    return Cred("api_key", KEY_ENV_VAR,
                f'the key saved as agent_api_keys["{agent}"] in {saved_credential_file()}', False)


def _client_args(relay_url: str, name: str, cred: Cred) -> str:
    if cred.kind == "api_key":
        return f'"{relay_url}", name="{name}", api_key=os.environ["{cred.env_var}"]'
    return f'"{relay_url}", os.environ["{cred.env_var}"], "{name}"'


def _print_env_hint(cred: Cred) -> None:
    ui.console.print(f"\n  Before running it, set {cred.env_var} to {cred.where}.",
                     highlight=False, markup=False)


def connect_ollama(room: str, name: str, relay_url: str, cred: Cred) -> None:
    ui.console.print("\n[bold green]Ollama Agent Setup[/bold green]\n")
    ui.console.print(f"  Agent:  [cyan]{name}[/cyan]")
    ui.console.print(f"  Room:   [cyan]{room}[/cyan]\n")
    ui.console.print("[bold]Save this as ollama_agent.py and run it:[/bold]\n")
    script = f"""\
import os

import ollama
from quorus_sdk.http_agent import QuorusClient

quorus = QuorusClient({_client_args(relay_url, name, cred)})
quorus.join("{room}")
quorus.send("{room}", "{name} online (ollama). Ready for tasks.")

while True:
    messages = quorus.receive(wait=30)
    if not messages:
        continue

    context = "\\n".join(f"{{m['from_name']}}: {{m['content']}}" for m in messages)

    response = ollama.chat(
        model="llama3.1",
        messages=[
            {{"role": "system", "content": (
                "You are {name} in a Quorus dev room. "
                "Respond to messages. Claim tasks with CLAIM:. "
                "Post status with STATUS:. Keep responses short."
            )}},
            {{"role": "user", "content": context}},
        ],
    )

    quorus.send("{room}", response["message"]["content"])"""
    ui.console.print(script, highlight=False, markup=False, soft_wrap=True)
    _print_env_hint(cred)
    ui.console.print("\n[bold]Install dependencies:[/bold]")
    ui.console.print("  pip install quorus-ai ollama", highlight=False, markup=False)
    ui.console.print(f"\n[green]{name} joined room '{room}'. Ready to go.[/green]")


def antigravity_config(name: str, relay_url: str, cred: Cred) -> dict:
    """The MCP entry to paste. A separately launched ``quorus-mcp`` reads the
    saved secret itself, but never sees this shell's environment, so a secret
    that isn't saved needs a key for the user to fill in (Codex on dec213c0).
    An agent key always does: left out, ``quorus-mcp`` would sign in with the
    profile's own key, as the user rather than the agent."""
    env = {"INSTANCE_NAME": name, "RELAY_URL": relay_url}
    if cred.kind == "api_key":
        env["API_KEY"] = KEY_PLACEHOLDER
    elif not cred.saved:
        env["RELAY_SECRET"] = PLACEHOLDER
    return {"mcpServers": {"quorus": {"command": "quorus-mcp", "env": env}}}


def connect_antigravity(room: str, name: str, relay_url: str, cred: Cred) -> None:
    ui.heading("Antigravity Setup")
    ui.console.print(f"  Agent:  [agent]@{name}[/]")
    ui.console.print(f"  Room:   [room]#{room}[/]\n")
    cfg = antigravity_config(name, relay_url, cred)
    ui.console.print("[bold]In Antigravity: Settings → MCP Servers → paste:[/]\n")
    ui.console.print(json.dumps(cfg, indent=2), highlight=False, markup=False,
                     soft_wrap=True)
    env = cfg["mcpServers"]["quorus"]["env"]
    if "API_KEY" in env:
        ui.console.print(f"\n  Replace {KEY_PLACEHOLDER} with {cred.where}.",
                         highlight=False, markup=False)
    elif "RELAY_SECRET" in env:
        ui.console.print(
            f"\n  Replace {PLACEHOLDER} with the RELAY_SECRET set in this shell. Or save "
            f"this relay first (quorus init {name} --relay-url {relay_url} --secret -, "
            "which reads it from stdin) and rerun connect, which leaves the key out.",
            highlight=False, markup=False)
    ui.console.print(
        f"\n[success]{name} joined room '{room}'. Reload Antigravity to "
        "activate MCP.[/success]"
    )


def connect_aider(room: str, name: str, relay_url: str, cred: Cred) -> None:
    """Aider doesn't speak MCP, so it goes through the HTTP SDK."""
    ui.heading("Aider Setup")
    ui.console.print(f"  Agent:  [agent]@{name}[/]")
    ui.console.print(f"  Room:   [room]#{room}[/]\n")
    ui.console.print(
        "Aider doesn't use MCP. Use the Quorus HTTP SDK from a sidecar "
        "script that aider calls out to, or have aider read/write via "
        "`quorus say` / `quorus inbox` in its shell output.\n"
    )
    snippet = f"""\
# In aider, run this in the shell to post a status update:
#   /run quorus say {room} "finished the auth refactor"
#
# Or, from a Python helper:
import os

from quorus_sdk.http_agent import QuorusClient

client = QuorusClient({_client_args(relay_url, name, cred)})
client.join("{room}")
client.send("{room}", "aider checkpoint — tests passing")"""
    ui.console.print(snippet, highlight=False, markup=False, soft_wrap=True)
    _print_env_hint(cred)
    ui.console.print(
        f"\n[success]{name} joined room '{room}'. "
        "Point aider's shell at `quorus say/inbox` or the SDK.[/success]"
    )


def _http_auth(relay_url: str, cred: Cred) -> tuple[str, str, str]:
    """``(curl header, intro lines, token step)`` for the system prompt. An agent
    key goes through ``/v1/auth/token`` first; a relay secret is the bearer."""
    if cred.kind != "api_key":
        return (f'-H "Authorization: Bearer ${cred.env_var}"',
                f"${cred.env_var} is already set in your shell; never print it.", "")
    token = (
        "# Get a token first, and again whenever a call returns 401 (it expires):\n"
        f'QUORUS_TOKEN=$(curl -s -X POST "{relay_url}/v1/auth/token" '
        '-H "Content-Type: application/json" '
        f'-d "{{\\"api_key\\":\\"${cred.env_var}\\"}}" '
        "| python3 -c 'import json,sys; print(json.load(sys.stdin)[\"token\"])')\n\n")
    return ('-H "Authorization: Bearer $QUORUS_TOKEN"',
            f"${cred.env_var} is already set in your shell; never print it or "
            "$QUORUS_TOKEN.", token)


def connect_http(room: str, name: str, relay_url: str, cred: Cred) -> None:
    """Generic HTTP/curl integration for any agent that can make HTTP calls."""
    ui.console.print("\n[bold green]Generic HTTP Agent Setup[/bold green]\n")
    ui.console.print(f"  Agent:  [cyan]{name}[/cyan]")
    ui.console.print(f"  Room:   [cyan]{room}[/cyan]")
    ui.console.print(f"  Relay:  [cyan]{relay_url}[/cyan]\n")
    ui.console.print(
        "[dim]Works with any agent: Codex, Antigravity, Open Interpreter,"
        " Windsurf, and more.[/dim]\n"
    )
    ui.console.print("[bold]System prompt to give the agent:[/bold]\n")
    auth, intro, token = _http_auth(relay_url, cred)
    system_prompt = f"""\
You are {name} in a Quorus coordination room. Communicate via these HTTP endpoints.
{intro}

{token}# Join the room first (run once):
curl -sX POST "{relay_url}/rooms/{room}/join" \\
  {auth} \\
  -H "Content-Type: application/json" \\
  -d '{{"participant":"{name}"}}'

# Check for messages from other agents:
curl -s "{relay_url}/rooms/{room}/history?limit=20" \\
  {auth}

# Send a message to all agents in the room:
curl -sX POST "{relay_url}/rooms/{room}/messages" \\
  {auth} -H "Content-Type: application/json" \\
  -d '{{"from_name":"{name}","content":"your message","message_type":"chat"}}'

# Claim exclusive access to a file/task (distributed mutex):
curl -sX POST "{relay_url}/rooms/{room}/lock" \\
  {auth} -H "Content-Type: application/json" \\
  -d '{{"resource":"{name}_task","locked_by":"{name}","ttl_seconds":300}}'

# Get full room state (goal, tasks, decisions):
curl -s "{relay_url}/rooms/{room}/state" {auth}

Check messages before each task. Claim with CLAIM:. Post STATUS: when done."""
    ui.console.print(system_prompt, highlight=False, markup=False, soft_wrap=True)
    ui.console.print(f"\n  Set {cred.env_var} in the agent's shell to {cred.where}.",
                     highlight=False, markup=False)
    ui.console.print(f"\n[green]{name} joined room '{room}'. Ready.[/green]")


GENERATORS = {
    "ollama": connect_ollama,
    "antigravity": connect_antigravity,
    "aider": connect_aider,
    "http": connect_http,
}


def run(platform: str, room: str, name: str, *, relay_url: str, secret: str,
        api_key: str, parent: str, register) -> None:
    """Print *platform*'s snippet. On a relay that uses API keys, give the agent
    its own key first; if that can't be done, print no snippet and exit 1."""
    if api_key and not secret:
        from quorus_cli.connect_agent_key import AgentKeyError, agent_key

        try:
            name = agent_key(relay_url=relay_url, name=name, parent=parent,
                             parent_key=api_key, platform=platform, register=register,
                             credential_file=saved_credential_file())
        except AgentKeyError as exc:
            ui.error(str(exc), hint=exc.hint)
            raise SystemExit(1) from None
        cred = agent_key_cred(name)
    else:
        cred = secret_cred(relay_url, secret)
    GENERATORS[platform](room, name, relay_url, cred)
