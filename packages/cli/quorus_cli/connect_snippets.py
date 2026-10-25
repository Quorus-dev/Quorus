"""`quorus connect` for platforms with no config file to write: ollama, antigravity,
aider and http print a snippet to paste instead.

The snippets never contain the relay secret (docs/plans/connect-secret-snippets.md).
Before 0.7.6 each embedded it as a literal, so a screenshot, a pasted terminal or an
agent's system prompt carried the operator credential. Now the Python and curl
snippets read ``QUORUS_RELAY_SECRET`` from the environment, and the antigravity MCP
config leaves the secret to ``quorus-mcp`` when it is the saved one.

Snippets print with ``soft_wrap``: a hard wrap at the terminal width splits a curl
line or a string literal, and the pasted copy no longer runs.
"""

from __future__ import annotations

import json

from quorus.config import DEFAULT_RELAY_URL, ConfigManager, resolve_config_file, same_relay
from quorus_cli import ui

ENV_VAR = "QUORUS_RELAY_SECRET"
PLACEHOLDER = "<paste your RELAY_SECRET>"


def secret_is_saved(relay_url: str, secret: str) -> bool:
    """Is *secret* the one the profile saves for *relay_url*? Then ``quorus-mcp``
    finds it on its own; otherwise it came from this shell's ``RELAY_SECRET``."""
    saved = ConfigManager().load(migrate=False)
    return bool(secret) and secret == saved.get("relay_secret") and same_relay(
        relay_url, saved.get("relay_url") or DEFAULT_RELAY_URL)


def _where(relay_url: str, secret: str) -> str:
    """One line naming where the secret lives, never the secret."""
    if secret_is_saved(relay_url, secret):
        return f"the relay_secret saved in {resolve_config_file()}"
    return f'the RELAY_SECRET set in this shell (export {ENV_VAR}="$RELAY_SECRET")'


def _print_env_hint(relay_url: str, secret: str) -> None:
    ui.console.print(f"\n  Before running it, set {ENV_VAR} to {_where(relay_url, secret)}.",
                     highlight=False, markup=False)


def connect_ollama(room: str, name: str, relay_url: str, secret: str) -> None:
    ui.console.print("\n[bold green]Ollama Agent Setup[/bold green]\n")
    ui.console.print(f"  Agent:  [cyan]{name}[/cyan]")
    ui.console.print(f"  Room:   [cyan]{room}[/cyan]\n")
    ui.console.print("[bold]Save this as ollama_agent.py and run it:[/bold]\n")
    script = f"""\
import os

import ollama
from quorus_sdk.http_agent import QuorusClient

quorus = QuorusClient("{relay_url}", os.environ["{ENV_VAR}"], "{name}")
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
    _print_env_hint(relay_url, secret)
    ui.console.print("\n[bold]Install dependencies:[/bold]")
    ui.console.print("  pip install quorus-ai ollama", highlight=False, markup=False)
    ui.console.print(f"\n[green]{name} joined room '{room}'. Ready to go.[/green]")


def antigravity_config(name: str, relay_url: str, secret: str) -> dict:
    """The MCP entry to paste. A separately launched ``quorus-mcp`` reads the
    saved secret itself, but never sees this shell's environment, so a secret
    that isn't saved needs a key for the user to fill in (Codex on dec213c0)."""
    env = {"INSTANCE_NAME": name, "RELAY_URL": relay_url}
    if not secret_is_saved(relay_url, secret):
        env["RELAY_SECRET"] = PLACEHOLDER
    return {"mcpServers": {"quorus": {"command": "quorus-mcp", "env": env}}}


def connect_antigravity(room: str, name: str, relay_url: str, secret: str) -> None:
    ui.heading("Antigravity Setup")
    ui.console.print(f"  Agent:  [agent]@{name}[/]")
    ui.console.print(f"  Room:   [room]#{room}[/]\n")
    cfg = antigravity_config(name, relay_url, secret)
    ui.console.print("[bold]In Antigravity: Settings → MCP Servers → paste:[/]\n")
    ui.console.print(json.dumps(cfg, indent=2), highlight=False, markup=False,
                     soft_wrap=True)
    if "RELAY_SECRET" in cfg["mcpServers"]["quorus"]["env"]:
        ui.console.print(
            f"\n  Replace {PLACEHOLDER} with the RELAY_SECRET set in this shell. Or save "
            f"this relay first (quorus init {name} --relay-url {relay_url} --secret -, "
            "which reads it from stdin) and rerun connect, which leaves the key out.",
            highlight=False, markup=False)
    ui.console.print(
        f"\n[success]{name} joined room '{room}'. Reload Antigravity to "
        "activate MCP.[/success]"
    )


def connect_aider(room: str, name: str, relay_url: str, secret: str) -> None:
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

client = QuorusClient(
    "{relay_url}",
    os.environ["{ENV_VAR}"],
    "{name}",
)
client.join("{room}")
client.send("{room}", "aider checkpoint — tests passing")"""
    ui.console.print(snippet, highlight=False, markup=False, soft_wrap=True)
    _print_env_hint(relay_url, secret)
    ui.console.print(
        f"\n[success]{name} joined room '{room}'. "
        "Point aider's shell at `quorus say/inbox` or the SDK.[/success]"
    )


def connect_http(room: str, name: str, relay_url: str, secret: str) -> None:
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
    auth = f'-H "Authorization: Bearer ${ENV_VAR}"'
    system_prompt = f"""\
You are {name} in a Quorus coordination room. Communicate via these HTTP endpoints.
${ENV_VAR} is already set in your shell; never print it.

# Join the room first (run once):
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
    ui.console.print(f"\n  Set {ENV_VAR} in the agent's shell to {_where(relay_url, secret)}.",
                     highlight=False, markup=False)
    ui.console.print(f"\n[green]{name} joined room '{room}'. Ready.[/green]")


GENERATORS = {
    "ollama": connect_ollama,
    "antigravity": connect_antigravity,
    "aider": connect_aider,
    "http": connect_http,
}
