"""The Quorum view: a room as a round council table, live (2026-10-17).

Arav asked for an animated view of the agents that is Quorus's own rather
than another "agents in an office" clone. A quorum is an assembly that meets
to decide, so a room is a round table: every member (agent or person) has a
seat, the room is the glowing core in the middle, @-mentions arc between
seats, a publish rises into the centre pillar (the integration branch), and a
failover hops across the table. It reads exactly the data the board reads
(``board.js`` hands it the rows), so it never has its own data path.

Static files only, served same-origin so the relay CSP stays
``script-src 'self'``. three.js is vendored (MIT); see ``web/vendor``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

router = APIRouter()
WEB = Path(__file__).resolve().parent / "web"
# Only these files, never a path from the request.
FILES = {
    "quorum.js": WEB / "quorum.js",
    "vendor/three.module.min.js": WEB / "vendor" / "three.module.min.js",
    "vendor/three.core.min.js": WEB / "vendor" / "three.core.min.js",
}


def _js(name: str, cache: str) -> Response:
    path = FILES.get(name)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404)
    return Response(path.read_bytes(), media_type="application/javascript",
                    headers={"cache-control": cache})


@router.get("/quorum.js", include_in_schema=False)
async def quorum_js() -> Response:
    return _js("quorum.js", "no-cache")


@router.get("/vendor/{name}", include_in_schema=False)
async def vendor_js(name: str) -> Response:
    # The vendored library never changes for a given release: cache it a day.
    return _js(f"vendor/{name}", "public, max-age=86400")
