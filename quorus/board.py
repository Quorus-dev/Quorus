"""The "needs you" board: one row per agent, sorted by what needs a human (2026-10-17).

Arav asked for a live view of the team; the room's agents argued (and won)
that an animated office is decoration others already ship, while nobody
answers "who is blocked on me, who is stuck, what shipped?" in two seconds
with a button next to each answer. Every field comes from relay data the
daemons already send (presence ``activity`` / ``waiting_on``, room history,
pending approvals). Approve/Deny only work with the viewer's own participant
token, never the shared secret.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter()
# The script lives in quorus/web/board.js (ships in the wheel with the package),
# so ESLint and the node tests see a file, not a Python string (board-inbox.md, slice 1).
_WEB = Path(__file__).parent / "web"

BOARD_JS = "\n" + (_WEB / "board.js").read_text(encoding="utf-8")


@router.get("/board.js", include_in_schema=False)
async def board_js() -> Response:
    """The board's script, same-origin so the CSP stays ``script-src 'self'``."""
    return Response(BOARD_JS, media_type="application/javascript",
                    headers={"cache-control": "no-cache"})
