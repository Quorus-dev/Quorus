"""Desktop banners for your @-mentions and DMs while the hub is closed.

The hub raises a banner only while it runs, so a teammate's agent that
@-mentioned you with the hub closed went unseen (live run, 2026-11-04). This
listener reads the same stream as a small background job (`quorus notify on`).
Both may run at once: the banner rate limit in quorus.notifications.native
keeps it to one banner per sender and room.

It never posts, acks or changes anything on the relay; it only reads.
"""

from __future__ import annotations

import json
import signal
import threading
from typing import Any

import httpx

RECONNECT_S = 5.0


def should_notify(msg: dict[str, Any], me: str, chat_identity: str = "") -> bool:
    """An @-mention of you or a DM, from someone else and not your own agent.
    Your agents' daemons alert you themselves (agent_events), so a banner here
    would double each of theirs."""
    sender = msg.get("from_name") or ""
    human = chat_identity or me
    if not sender or sender in (me, human) or sender.startswith(f"{human}-"):
        return False
    content = (msg.get("content") or "").lower()
    tokens = {f"@{me.lower()}", f"@{human.lower()}"} - {"@"}
    is_dm = bool(msg.get("is_dm")) or msg.get("message_type") == "dm"
    return is_dm or any(t in content for t in tokens)


def _identity(profile: dict[str, Any]) -> tuple[str, str]:
    from quorus_tui import hub

    me = profile.get("instance_name") or ""
    chat = profile.get("chat_identity") or ""
    if not chat and hub._name_has_agent_keyword(me):
        chat = hub._strip_agent_suffix(me)
    return me, chat


def run(profile: dict[str, Any], stop: threading.Event) -> None:
    """Stream messages for *profile* and raise a banner for each one that
    :func:`should_notify` allows, until *stop* is set. Reconnects on any drop."""
    from quorus.notifications import notify
    from quorus_tui import hub

    relay = (profile.get("relay_url") or "").rstrip("/")
    me, chat = _identity(profile)
    raw, _admin = hub.session_credentials(profile)
    while not stop.is_set():
        token = hub._mint_sse_token(relay, hub._get_auth_token(relay, raw), me)
        if not token:
            stop.wait(RECONNECT_S)
            continue
        try:
            with httpx.stream("GET", f"{relay}/stream/{me}?token={token}",
                              timeout=None) as resp:
                if resp.status_code != 200:
                    stop.wait(RECONNECT_S)
                    continue
                event, data = "", []
                for line in resp.iter_lines():
                    if stop.is_set():
                        return
                    if line.startswith("event:"):
                        event = line[len("event:"):].strip()
                    elif line.startswith("data:"):
                        data.append(line[len("data:"):].lstrip())
                    elif not line:
                        if event == "message" and data:
                            try:
                                msg = json.loads("\n".join(data))
                            except ValueError:
                                msg = None
                            if isinstance(msg, dict) and should_notify(msg, me, chat):
                                room = msg.get("room") or ""
                                notify(f"Quorus — {room or 'DM'}", msg.get("content") or "",
                                       sender=msg.get("from_name") or "", room=room)
                        event, data = "", []
        except Exception:  # a dropped stream reconnects; never crash the job
            pass
        stop.wait(RECONNECT_S)


def main() -> None:
    from quorus.config import load_config

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_a: stop.set())
    run(load_config(), stop)


if __name__ == "__main__":
    main()
