"""Owner-authenticated exact-task resolution; no agent impersonation."""
from __future__ import annotations

import httpx

from quorus.runtime.handoff_release import Refused


def resolve_claim(*, room: str, agent: str, task_id: str, resolution_id: str) -> None:
    from quorus_cli import cli

    try:
        response = httpx.post(f"{cli.RELAY_URL}/v1/handoff/discard",
            headers=cli._auth_headers(), timeout=20,
            json=dict(room_id=room, receiver=agent, task_id=task_id,
                      resolution_id=resolution_id))
        if response.status_code in (404, 405):
            raise Refused("relay lacks owner handoff discard; upgrade it; local hold retained")
        response.raise_for_status()
        result = response.json()
        if result.get("task_id") != task_id or result.get("resolved") is not True:
            raise Refused("relay did not confirm exact claim resolution; local hold retained")
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        raise Refused("relay claim resolution failed; local hold retained. Use the owner's "
                      "relay config and --runtime-dir for the receiver's runtime") from exc
