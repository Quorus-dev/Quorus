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
# The scripts live in quorus/web/ (shipped in the wheel with the package), so ESLint and
# the node tests see files, not Python strings (board-inbox.md, slices 1-2). board.js is
# an ES module: it imports /rows.js and the vendored Preact + htm from /vendor/.
_WEB = Path(__file__).parent / "web"

BOARD_JS = "\n" + (_WEB / "board.js").read_text(encoding="utf-8")
# The pure row logic board.js imports; node tests run this file (slice 2).
ROWS_JS = (_WEB / "rows.js").read_text(encoding="utf-8")


@router.get("/board.js", include_in_schema=False)
async def board_js() -> Response:
    """The board's script, same-origin so the CSP stays ``script-src 'self'``."""
    return Response(BOARD_JS, media_type="application/javascript",
                    headers={"cache-control": "no-cache"})


@router.get("/rows.js", include_in_schema=False)
async def rows_js() -> Response:
    """The board's row logic, imported by /board.js (same origin, same CSP)."""
    return Response(ROWS_JS, media_type="application/javascript",
                    headers={"cache-control": "no-cache"})
