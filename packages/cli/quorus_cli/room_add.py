"""`quorus room add <room> <member...>`: put existing participants in a room.

Before this, adding an existing agent to a second room meant calling the relay's
join API by hand with the shared secret (friction 8, bet 1 in
docs/internal/bets/lab-top3.md). The relay decides who may add: the room's
creator or an admin, or a member adding an agent they own. A refusal exits
non-zero with the relay's reason (stranger finding 4: refusals exited 0).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx


async def add_members(room: str, members: list[str]) -> int:
    """Add each of *members* to *room*; returns how many the relay refused."""
    from quorus_cli import cli

    refused = 0
    client = cli._get_client()
    try:
        for name in dict.fromkeys(members):  # in order, once each
            try:
                resp = await client.post(f"{cli.RELAY_URL}/rooms/{room}/join",
                                         json={"participant": name},
                                         headers=cli._admin_headers())
            except httpx.ConnectError as exc:
                cli._relay_unreachable(exc=exc)
                return len(members)
            if resp.status_code == 200:
                cli.console.print(f"[success]✓[/] @{name} is in #{room}")
                continue
            refused += 1
            cli._ui.error(f"@{name}: {_detail(resp)}")
    finally:
        await client.aclose()
    return refused


def _detail(resp: httpx.Response) -> str:
    try:
        detail: Any = resp.json().get("detail")
    except ValueError:
        detail = None
    if resp.status_code == 404:
        return "no such room (list them: quorus rooms)"
    if isinstance(detail, list):  # a validation error: say what was wrong, not the JSON
        detail = "; ".join(str(d.get("msg", d)) if isinstance(d, dict) else str(d)
                           for d in detail)
    return str(detail or f"the relay refused (HTTP {resp.status_code})")


def cmd_room_add(args: Any) -> None:
    refused = asyncio.run(add_members(args.room, args.members))
    if refused:
        raise SystemExit(1)
