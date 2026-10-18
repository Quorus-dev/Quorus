"""Say why the relay refused, not just its status code (U1, 0.5.1-0.5.2).

The CLI printed "Could not create room · server returned HTTP 422", "hint:
HTTP 413" and "hint: HTTP 429": the user had to guess the name rule, the size
limit and when to retry (overseer's agent sweep of quorus-ai 0.5.0). The MCP
tools did the same for agents ("Relay returned 429: {...}"), so both the CLI
and the MCP server use this one wording.
"""

from __future__ import annotations

from typing import Any

_MAX_DETAIL = 200


def _detail(resp: Any) -> str:
    """The relay's own reason: FastAPI's ``detail`` string, or the messages
    of a validation error (``[{"msg": ...}]``). Empty when there is none."""
    try:
        body = resp.json()
    except ValueError:
        return ""
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, list):
        msgs = [str(d.get("msg", "")) for d in detail if isinstance(d, dict)]
        detail = "; ".join(m.removeprefix("Value error, ") for m in msgs if m)
    return str(detail or "").strip()[:_MAX_DETAIL]


def hint(resp: Any) -> str:
    """One line saying why *resp* failed and what to do about it."""
    code = resp.status_code
    detail = _detail(resp)
    if code == 429:
        wait = (resp.headers.get("retry-after") or "").strip()
        if wait.isdigit():
            return f"the relay is rate-limiting you: try again in {wait}s"
        return f"the relay is rate-limiting you: {detail or 'try again in a minute'}"
    if code == 413:
        return detail or ("too large for this relay (MAX_MESSAGE_SIZE, 50 KB by default)")
    if code in (400, 422) and detail:
        return detail
    return f"HTTP {code}: {detail}" if detail else f"HTTP {code}"
