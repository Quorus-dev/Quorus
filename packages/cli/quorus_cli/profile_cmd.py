"""``quorus profile [list | use <name> | show [name]]``: switch saved relays.

Each profile in ~/.quorus/profiles is one relay and identity; config.json's
``current`` picks the one every command uses. Switching used to mean editing
config.json by hand or exporting env vars (FRICTION 2026-11-02).
"""
from __future__ import annotations

import sys
from urllib.parse import urlsplit, urlunsplit

from quorus.profiles import ProfileManager
from quorus_cli import ui as _ui

# Never printed, only reported as set or not.
_SECRET_FIELDS = ("relay_secret", "api_key", "agent_api_keys")


def _relay_label(url: object) -> str:
    """Scheme, host and port only: never URL credentials, paths or query tokens."""
    try:
        parts = urlsplit(str(url or ""))
        host = parts.hostname
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        return "?"
    if not host:
        return "?"
    host = f"[{host}]" if ":" in host else host
    return urlunsplit((parts.scheme, host + port, "", "", "")) or "?"


def _list(pm: ProfileManager) -> int:
    names = pm.list()
    if not names:
        _ui.error("No saved profiles", hint="set one up: quorus init <name>")
        return 1
    current = pm.current()
    for name in names:
        data = pm.get(name) or {}
        mark = "*" if name == current else " "
        who = data.get("instance_name") or "?"
        _ui.console.print(f" {mark} {name:<16} @{who:<20} {_relay_label(data.get('relay_url'))}",
                          markup=False, highlight=False)
    return 0


def _use(pm: ProfileManager, name: str | None) -> int:
    if not name:
        _ui.error("Which profile?", hint="quorus profile use <name> (see: quorus profile list)")
        return 2
    try:
        pm.set_current(name)
    except (FileNotFoundError, ValueError):
        _ui.error(f"No profile named '{name}'", hint="see: quorus profile list")
        return 1
    data = pm.get(name) or {}
    _ui.console.print(f"✓ Using profile {name}: @{data.get('instance_name') or '?'} on "
                      f"{_relay_label(data.get('relay_url'))}", markup=False, highlight=False)
    return 0


def _show(pm: ProfileManager, name: str | None) -> int:
    name = name or pm.current()
    if not name:
        _ui.error("No current profile", hint="quorus profile use <name>")
        return 1
    try:
        data = pm.get(name)
    except ValueError:
        data = None
    if data is None:
        _ui.error(f"No profile named '{name}'", hint="see: quorus profile list")
        return 1
    current = " (current)" if name == pm.current() else ""
    lines = [f"profile        {name}{current}",
             f"name           @{data.get('instance_name') or '?'}",
             f"relay          {_relay_label(data.get('relay_url'))}"]
    for field in _SECRET_FIELDS:
        lines.append(f"{field:<14} {'set' if data.get(field) else 'not set'}")
    lines.append(f"file           {pm.profiles_dir / (name + '.json')}")
    for line in lines:
        _ui.console.print(line, markup=False, highlight=False)
    return 0


def run(args) -> None:
    pm = ProfileManager()
    action = getattr(args, "action", None) or "list"
    name = getattr(args, "name", None)
    if action == "list":
        code = _list(pm)
    elif action == "use":
        code = _use(pm, name)
    else:
        code = _show(pm, name)
    if code:
        sys.exit(code)


def add_parser(sub) -> None:
    p = sub.add_parser("profile", help="List, switch or show saved relay profiles")
    p.add_argument("action", nargs="?", choices=("list", "use", "show"), default="list")
    p.add_argument("name", nargs="?", help="Profile name (for use / show)")
