"""The Quorum view (2026-10-17): served same-origin from package data, only
allow-listed files, our own code (no eval, no external assets), and it reads
the board's rows rather than its own data path."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from quorus import quorum_view
from quorus.board import BOARD_JS

WEB = Path(quorum_view.__file__).parent / "web"


def test_served_same_origin_and_only_allow_listed() -> None:
    from quorus.relay import app

    with TestClient(app) as c:
        js = c.get("/quorum.js")
        core = c.get("/vendor/three.core.min.js")
        mod = c.get("/vendor/three.module.min.js")
        assert js.status_code == core.status_code == mod.status_code == 200
        assert "javascript" in js.headers["content-type"]
        for bad in ("/vendor/../relay.py", "/vendor/README.txt", "/vendor/%2e%2e%2fboard.py",
                    "/vendor/THREE-LICENSE.txt"):
            assert c.get(bad).status_code == 404, bad


def test_our_code_runs_under_the_csp() -> None:
    src = (WEB / "quorum.js").read_text()
    for sink in ("eval(", "new Function", "innerHTML", "insertAdjacentHTML", "document.write"):
        assert sink not in src, sink
    # No external URLs: every import and asset is same-origin.
    assert not re.search(r"https?://", src)
    assert 'import("/vendor/three.module.min.js")' in src  # loaded only when opened
    for f in ("three.core.min.js", "three.module.min.js"):
        lib = (WEB / "vendor" / f).read_text()
        assert "eval(" not in lib and "new Function" not in lib
    assert "MIT License" in (WEB / "vendor" / "THREE-LICENSE.txt").read_text()


def test_quorum_reads_the_boards_rows_and_reuses_its_card() -> None:
    src = (WEB / "quorum.js").read_text()
    assert 'addEventListener("quorus:rows"' in src and "fetch(" not in src  # no own data path
    assert "window.QuorusBoard.rowEl(row)" in src
    assert 'new CustomEvent("quorus:rows"' in BOARD_JS
    assert "window.QuorusBoard = { rowEl }" in BOARD_JS


# The vendored files, pinned: a changed byte fails here before it reaches anyone.
THREE_SHA256 = {
    "three.core.min.js": "61ba0df005b05991361d040d8ff670e1aadfd0ce7aeebd1fdb0725957a8957de",
    "three.module.min.js": "e2b5ee6bccd38fd6d8a2428546b83c5f2426d84b152ef82be8055556e3b40eb6",
}


def test_vendored_three_is_pinned() -> None:
    import hashlib

    for name, digest in THREE_SHA256.items():
        assert hashlib.sha256((WEB / "vendor" / name).read_bytes()).hexdigest() == digest, name


def test_seat_tags_are_html_buttons_laid_out_apart() -> None:
    """Arav's screenshot: front-seat plaques overlapped their status bubbles. Tags
    are now one HTML button per seat (name, model, status; textContent only;
    keyboard-focusable), placed in screen space and nudged apart."""
    src = (WEB / "quorum.js").read_text()
    assert "label(" not in src and "CanvasTexture" not in src  # no text drawn into the scene
    assert 'createElement("button")' in src and ".textContent = " in src
    assert "function placeTags()" in src and "step above the tag it collided with" in src
    assert "r.model ||" in src  # the model names the agent; no vendor logos


def test_quorum_degrades_instead_of_going_blank() -> None:
    src = (WEB / "quorum.js").read_text()
    # One build even when two row events race the lazy import (two canvases, seen live).
    assert "if (!Q.starting) Q.starting = boot(host);" in src
    # Phones and browsers without WebGL get the flat seat grid, without downloading three.js.
    boot = src[src.index("async function boot("):]
    assert boot.index("phone.matches") < boot.index('import("/vendor/three.module.min.js")')
    assert "return goFlat(host);" in boot
    # Reduced motion: nothing flies and nothing oscillates.
    assert "if (reduceMotion) return;" in src and "const wave = reduceMotion ? 0 : 1;" in src
    # Ten-plus members: a seat cap, the rest named in a note; an empty room says what to do.
    assert "MAX_SEATS = 14" in src and "No agents at this table yet" in src
    from quorus.dashboard import DASHBOARD_HTML

    assert "#quorum canvas{position:absolute" in DASHBOARD_HTML  # can't grow its own host
