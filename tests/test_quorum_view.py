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
