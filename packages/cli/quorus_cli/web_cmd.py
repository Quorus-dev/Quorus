"""`quorus web`: your rooms and the "needs you" board in this computer's browser.

The web page and board already ran on laptops, but the only way in was
`quorus phone`'s link (2026-10-17). This opens the same page locally, signed
in as you with your own participant token (never the shared secret), and lands
on the board. The credential travels in the URL fragment, which browsers never
send to servers or logs, and the page strips it from history.
"""

from __future__ import annotations

import sys
import webbrowser
from typing import Any
from urllib.parse import quote, urlparse

from quorus.config import load_config


def web_url(relay_url: str, token: str, name: str, room: str | None, board: bool,
            fps: bool = False, quorum: bool = False) -> str:
    frag = f"token={quote(token, safe='')}&name={quote(name, safe='')}"
    if room:
        frag += f"&room={quote(room, safe='')}"
    if quorum:  # the council table is opt-in (board-inbox.md decision 5)
        frag += "&view=quorum"
    elif board:
        frag += "&view=board"  # the board lands on its needs-you inbox
    if fps:  # the Quorum view shows its fps / DPR / quality line (the M1 proof)
        frag += "&fps=1"
    return f"{relay_url.rstrip('/')}/#{frag}"


def _check_room(relay: str, token: str, room: str) -> None:
    """Fail before opening a browser on a room that doesn't exist: the page used
    to open on an empty board and exit 0 (stranger test, 2026-10-19). If the
    relay can't be asked, open anyway and let the page say what it can."""
    import httpx

    try:
        resp = httpx.get(f"{relay.rstrip('/')}/rooms", timeout=10,
                         headers={"Authorization": f"Bearer {token}"})
        rooms = resp.json() if resp.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return
    if not isinstance(rooms, list):
        return
    names = sorted(str(r.get("name")) for r in rooms if isinstance(r, dict) and r.get("name"))
    if room.lstrip("#") in names:
        return
    have = ", ".join(f"#{n}" for n in names) if names else "none yet (quorus create <name>)"
    print(f"web: no room {room.lstrip('#')!r}; your rooms: {have}", file=sys.stderr)
    raise SystemExit(4)


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
    room = getattr(args, "room", None)
    if room:
        _check_room(relay, token, room)
    url = web_url(relay, token, me, room, not getattr(args, "chat", False),
                  fps=bool(getattr(args, "fps", False)),
                  quorum=bool(getattr(args, "quorum", False)))
    if getattr(args, "print_url", False) or not webbrowser.open(url):
        console.print("Open this in your browser (it signs you in, so don't share it):")
        console.print(url, markup=False, highlight=False, soft_wrap=True)
        return
    console.print(f"[success]✓[/] Opened Quorus in your browser as @{me}"
                  + (f" in #{args.room}" if getattr(args, "room", None) else "")
                  + ". On your phone: [accent]quorus phone[/]")
