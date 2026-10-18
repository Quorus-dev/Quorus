"""The Quorum view (2026-10-17, premium pass 2026-10-18): served same-origin from
package data, only allow-listed files, our own code (no eval, no external
assets), and it reads the board's rows rather than its own data path."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from fastapi.testclient import TestClient

from quorus import quorum_view
from quorus.board import BOARD_JS
from quorus.dashboard import DASHBOARD_HTML

WEB = Path(quorum_view.__file__).parent / "web"
OURS = ["quorum.js", "quorum-stage.js", "quorum-totems.js"]


def _src(name: str) -> str:
    return (WEB / name).read_text()


def test_served_same_origin_and_only_allow_listed() -> None:
    from quorus.relay import app

    with TestClient(app) as c:
        for name in OURS:
            r = c.get("/" + name)
            assert r.status_code == 200 and "javascript" in r.headers["content-type"], name
            assert r.headers["cache-control"] == "no-cache"
        for name in ("three.core.min.js", "three.module.min.js"):
            r = c.get("/vendor/" + name)
            assert r.status_code == 200 and "javascript" in r.headers["content-type"], name
            assert "max-age" in r.headers["cache-control"]
        font = c.get("/vendor/fonts/plus-jakarta-sans.woff2")
        assert font.status_code == 200 and font.headers["content-type"].startswith("font/woff2")
        assert font.content[:4] == b"wOF2"
        for bad in ("/vendor/../relay.py", "/vendor/README.txt", "/vendor/%2e%2e%2fboard.py",
                    "/vendor/THREE-LICENSE.txt", "/vendor/fonts/PLUS-JAKARTA-SANS-LICENSE.txt",
                    "/vendor/fonts/..%2Fthree.core.min.js", "/quorum-nope.js"):
            assert c.get(bad).status_code == 404, bad


def test_our_code_runs_under_the_csp() -> None:
    for name in OURS:
        src = _src(name)
        for sink in ("eval(", "new Function", "innerHTML", "insertAdjacentHTML", "document.write"):
            assert sink not in src, (name, sink)
        # No external URLs: every import and asset is same-origin.
        assert not re.search(r"https?://", src), name
    # The stage and the totems load only when the view opens; they bring three.js.
    entry = _src("quorum.js")
    assert 'import("/quorum-stage.js")' in entry and 'import("/quorum-totems.js")' in entry
    for name in OURS[1:]:
        assert 'from "/vendor/three.module.min.js"' in _src(name), name
    for f in ("three.core.min.js", "three.module.min.js"):
        lib = (WEB / "vendor" / f).read_text()
        assert "eval(" not in lib and "new Function" not in lib
    assert "MIT License" in (WEB / "vendor" / "THREE-LICENSE.txt").read_text()
    licence = (WEB / "vendor" / "fonts" / "PLUS-JAKARTA-SANS-LICENSE.txt").read_text()
    assert "SIL Open Font License" in licence


def test_budget_is_small_on_top_of_three() -> None:
    """Arav: ≤300 KB extra JS. Ours is a fraction of that; the font is 27 KB."""
    assert sum((WEB / n).stat().st_size for n in OURS) < 100_000
    assert (WEB / "vendor" / "fonts" / "plus-jakarta-sans.woff2").stat().st_size < 40_000


def test_quorum_reads_the_boards_rows_and_reuses_its_card() -> None:
    src = _src("quorum.js")
    assert 'addEventListener("quorus:rows"' in src
    for name in OURS:
        assert "fetch(" not in _src(name), name  # no own data path
    assert "window.QuorusBoard.rowEl(row)" in src
    assert 'new CustomEvent("quorus:rows"' in BOARD_JS
    assert "window.QuorusBoard = { rowEl }" in BOARD_JS


# The vendored files, pinned: a changed byte fails here before it reaches anyone.
THREE_SHA256 = {
    "three.core.min.js": "61ba0df005b05991361d040d8ff670e1aadfd0ce7aeebd1fdb0725957a8957de",
    "three.module.min.js": "e2b5ee6bccd38fd6d8a2428546b83c5f2426d84b152ef82be8055556e3b40eb6",
}


def test_vendored_three_is_pinned() -> None:
    for name, digest in THREE_SHA256.items():
        assert hashlib.sha256((WEB / "vendor" / name).read_bytes()).hexdigest() == digest, name


def test_seat_tags_are_html_buttons_laid_out_apart() -> None:
    """Arav's screenshot: front-seat plaques overlapped their status bubbles. Tags
    are one HTML button per seat (name, model, status; textContent only;
    keyboard-focusable), placed in screen space and nudged apart."""
    src = _src("quorum.js")
    for name in OURS:
        assert "label(" not in _src(name) and "CanvasTexture" not in _src(name), name
    assert 'createElement("button")' in src and ".textContent = " in src
    assert "function placeTags()" in src and "step above the tag it collided with" in src
    assert "r.model ||" in src  # the model names the agent; no vendor logos
    # Plaques: fixed 12px type, frosted glass, the vendor as a 6px dot, the model in
    # small caps, one self-hosted font. Never a logo or a thick bar.
    assert ".qtag::before{" in DASHBOARD_HTML and "width:6px;height:6px" in DASHBOARD_HTML
    assert "border-left:3px solid var(--seat)" not in DASHBOARD_HTML
    assert "font-size:12px;" in DASHBOARD_HTML and "backdrop-filter:blur(12px)" in DASHBOARD_HTML
    assert "font-variant-caps:all-small-caps" in DASHBOARD_HTML
    assert '@font-face{font-family:"Plus Jakarta Sans";' in DASHBOARD_HTML
    assert "src:url(/vendor/fonts/plus-jakarta-sans.woff2)" in DASHBOARD_HTML
    assert "#quorumCard .brow{" in DASHBOARD_HTML  # the seat card is frosted too
    # Status words, not pictograms: "asleep · back ~03:40 AM", not "z z z back about".
    assert "z z z" not in src and "⌛" not in src
    assert '"asleep" + (r.line.includes("back about ")' in src


def test_quorum_degrades_instead_of_going_blank() -> None:
    src = _src("quorum.js")
    # One build even when two row events race the lazy import (two canvases, seen live).
    assert "if (!Q.starting) Q.starting = boot(host);" in src
    # Phones, narrow hosts and browsers without WebGL get the flat seat grid, without
    # downloading three.js. A host measured at 0 isn't narrow, it isn't laid out yet.
    boot = src[src.index("async function boot("):]
    assert boot.index("phone.matches") < boot.index('import("/quorum-stage.js")')
    assert "host.clientWidth > 0 && host.clientWidth < NARROW" in src
    assert "if (phone.matches || narrow)" in src and "return goFlat(host);" in boot
    # Reduced motion: a static premium still. No drift, no particles, no arcs;
    # the stage renders once per data change instead of looping.
    assert "if (reduceMotion) Q.stage.once(); else Q.stage.start();" in src
    assert "if (!from || reduceMotion) return;" in src
    assert "drift: !hooks.reduceMotion" in _src("quorum-stage.js")
    assert "w = reduce ? 0 : 1" in _src("quorum-totems.js")
    # Rendering pauses while the tab is hidden.
    assert "if (!S.on || document.hidden)" in _src("quorum-stage.js")
    # Ten-plus members: a seat cap, the rest named in a note; an empty room says what to do.
    assert "MAX_SEATS = 14" in src and "No agents at this table yet" in src
    assert "#quorum canvas{position:absolute" in DASHBOARD_HTML  # can't grow its own host


def test_premium_stage_is_ours_and_studio_grade() -> None:
    """Arav (2026-10-18): studio-grade 3D, our own sculpted totems, state as
    motion, a drifting camera that dollies into a seat, no logos, nothing borrowed."""
    entry, stage, totems = (_src(n) for n in OURS)
    # Only present members get a seat: online agents, people who spoke or the viewer.
    assert "rank.get(n) !== 6" in entry and "spoke.has(n) || n === Q.me" in entry
    assert "offline or away: see List" in entry
    # Image-based light baked in-process, ACES, soft shadows, clearcoat gloss, fog edge,
    # and an in-house bloom that composites through three's own output chunks.
    for needle in ("PMREMGenerator", "ACESFilmicToneMapping", "PCFSoftShadowMap", "clearcoat: 1",
                   "new THREE.Fog(", "WebGLRenderTarget", "#include <tonemapping_fragment>",
                   "#include <colorspace_fragment>", "function frame(S)"):
        assert needle in stage, needle
    # One sculpted form per vendor family, a pillar for people, a monolith for main.
    for needle in ('vendor === "claude"', 'vendor === "codex"', 'vendor === "gemini"',
                   'vendor === "cursor"', 'vendor === "cline"', "lattice cube", "function pillar("):
        assert needle in totems, needle
    assert "monolith" in stage and "Q.pillar" not in entry and "CapsuleGeometry" not in totems
    for name in OURS:  # nothing borrowed: no images, no logos
        assert not re.search(r"\.(svg|png|jpe?g|gif)\b", _src(name)), name
    # State is motion; shipped bursts to main; mentions arc, reviews braid.
    for needle in ("function particles(", "stuckFlicker", "u.beam.material.opacity",
                   "export function arc(", "export function burst(", "export function ripple("):
        assert needle in totems, needle
    assert "const colors = review ? [colourOf(m.from_name), colourOf(tag.slice(1))]" in entry
    # Camera: slow drift, dolly to the picked seat, the card on frosted glass.
    assert "Math.PI * 2 / 46" in stage and "S.d * (1 - 0.48 * S.k)" in stage
    assert "Q.stage.focus(seat.position)" in entry
