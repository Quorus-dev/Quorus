"""`quorus web`: your rooms and the "needs you" board in this computer's browser.

The web page and board already ran on laptops, but the only way in was
`quorus phone`'s link (2026-10-17). This opens the same page locally, signed
in as you with your own participant token (never the shared secret), and lands
on the board. The credential travels in the URL fragment, which browsers never
send to servers or logs, and the page strips it from history.
"""

from __future__ import annotations

import webbrowser
from typing import Any
from urllib.parse import quote, urlparse

from quorus.config import load_config


def web_url(relay_url: str, token: str, name: str, room: str | None, board: bool) -> str:
    frag = f"token={quote(token, safe='')}&name={quote(name, safe='')}"
    if room:
        frag += f"&room={quote(room, safe='')}"
    if board:
        frag += "&view=board"
    return f"{relay_url.rstrip('/')}/#{frag}"


def cmd_web(args: Any, console: Any) -> None:
    from quorus_cli.phone_cmd import _participant_token

    cfg = load_config()
    me = (cfg.get("instance_name") or "").strip()
    relay = (cfg.get("relay_url") or "").strip()
    if not me or not urlparse(relay).hostname:
        raise SystemExit("web: set up Quorus first: `quorus`")
    if cfg.get("relay_secret") and not cfg.get("api_key"):
        token = _participant_token(cfg, me, with_secret=False)
    else:
        from quorus_cli.agent_cmd import _bearer
        token = _bearer(cfg, cfg.get("api_key") or "", False)
    url = web_url(relay, token, me, getattr(args, "room", None), not getattr(args, "chat", False))
    if getattr(args, "print_url", False) or not webbrowser.open(url):
        console.print("Open this in your browser (it signs you in, so don't share it):")
        console.print(url, markup=False, highlight=False, soft_wrap=True)
        return
    console.print(f"[success]✓[/] Opened Quorus in your browser as @{me}"
                  + (f" in #{args.room}" if getattr(args, "room", None) else "")
                  + ". On your phone: [accent]quorus phone[/]")
