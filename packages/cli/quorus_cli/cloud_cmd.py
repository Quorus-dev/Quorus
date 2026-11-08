"""`quorus cloud`: get an account on Quorus Cloud, the hosted relay.

On a fresh machine it runs the usual setup (`quorus init`) against the hosted
relay, so the next commands work as they would locally. On a machine that
already has a setup, the hosted account is saved as a profile of its own
(join_config's rule) and the local setup, its agents and MCP clients stay as
they were.
"""

from __future__ import annotations

import argparse
import sys

from quorus.cloud import SignupError, public_relay, signup


def add_parser(sub) -> None:
    p = sub.add_parser(
        "cloud", help="Get an account on Quorus Cloud: rooms friends anywhere can join",
        description="Sign up on Quorus Cloud, the hosted relay. Your rooms there can take "
                    "people on any computer; your agents still run on your machine.")
    p.add_argument("--name", default=None, help="Your name (default: your current one)")
    p.add_argument("--relay", default=None,
                   help="Another hosted relay (default: $QUORUS_PUBLIC_RELAY or Quorus Cloud)")


def _existing(pm, relay_url: str, name: str) -> str | None:
    """The profile already holding a key for *name* on *relay_url*, if any."""
    from quorus.config import same_relay

    for slug in pm.list():
        data = pm.get(slug) or {}
        if (same_relay(data.get("relay_url", ""), relay_url)
                and data.get("instance_name") == name and data.get("api_key")):
            return slug
    return None


def run(args, *, console, ui, current_name: str, init) -> None:
    from quorus.profiles import ProfileManager

    relay_url = (args.relay or public_relay()).rstrip("/")
    if not relay_url:
        ui.error("QUORUS_PUBLIC_RELAY is off", hint="pass --relay <https://your-relay>")
        sys.exit(5)
    name = (args.name or current_name or "").strip()
    if not name:
        ui.error("Pick a name", hint="quorus cloud --name <your-name>")
        sys.exit(5)

    pm = ProfileManager()
    pm.migrate_legacy_if_needed()
    found = _existing(pm, relay_url, name)
    if found:
        console.print(f"[green]✓[/] You're already on {relay_url} as @{name} "
                      f"(profile '{found}').")
        if pm.current() != found:
            console.print(f"  [dim]Switch to it:[/] [accent]quorus profile use {found}[/]")
        return

    console.print(f"[dim]Creating your account on {relay_url}...[/]")
    try:
        account = signup(relay_url, name)
    except SignupError as exc:
        ui.error(f"Couldn't sign up: {exc}")
        sys.exit(2)

    if not pm.list() and not pm.pointer_path.exists():
        # Fresh machine: the full setup (identity, agent keys, MCP clients).
        init(argparse.Namespace(
            name=name, relay_url=relay_url, api_key=account.api_key, secret=None,
            config_dir=None, no_smoke=True, no_launchd=True, auto_launchd=False,
            autostart=False, no_autostart=True, force=False))
        # init just printed create / agent add; what's new here is the invite.
        console.print("\n[bold]People on any computer can join your rooms:[/]\n"
                      "  quorus share myroom        [dim]# send them the code[/]")
        return
    else:
        from quorus_cli import join_config

        saved = join_config.save({
            "relay_url": relay_url, "instance_name": name, "api_key": account.api_key,
            "push_notification_method": "notifications/claude/channel",
            "push_notification_channel": "quorus",
        })
        switch = "" if saved.current else (saved.slug or "")
        console.print(f"[green]✓[/] Account created: @{name} in workspace "
                      f"[bold]{account.workspace}[/] on {relay_url}")
        if switch:
            console.print(f"  [dim]Saved as profile '{switch}'. Your current setup is "
                          "unchanged.[/]")

    prefix = f"quorus profile use {switch}\n  " if switch else ""
    console.print(
        "\n[bold]Make a room people anywhere can join:[/]\n"
        f"  {prefix}quorus create myroom\n"
        "  quorus agent add claude --room myroom --repo <path-to-repo>\n"
        "  quorus share myroom        [dim]# send the code to a friend[/]")
