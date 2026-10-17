"""Quorus web (the phone companion's page) at ``/`` (rewritten 2026-10-17).

The old page never ran: the relay CSP (`script-src 'self'`) blocked its inline
script and handlers, and it had latent XSS. These tests pin the properties that
matter: it runs under the real CSP, data never becomes HTML, and the token
stays out of URLs.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from quorus.dashboard import DASHBOARD_HTML, DASHBOARD_JS


def test_page_has_no_inline_script_or_handlers() -> None:
    # Only <script src="/web.js">, so `script-src 'self'` lets it run.
    scripts = re.findall(r"<script\b([^>]*)>", DASHBOARD_HTML)
    assert scripts == [' src="/web.js"', ' src="/board.js"',
                       ' type="module" src="/quorum.js"']  # board + Quorum view, 2026-10-17
    assert not re.search(r"\son[a-z]+\s*=", DASHBOARD_HTML), "inline event handler"


def test_message_data_is_never_inserted_as_html() -> None:
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in DASHBOARD_JS, sink
    assert "textContent" in DASHBOARD_JS
    # Path segments built from data are always URI-encoded.
    assert '"/rooms/" + enc(' in DASHBOARD_JS
    assert not re.search(r'"/rooms/"\s*\+\s*(?!\s|enc\()', DASHBOARD_JS)


def test_token_comes_from_the_fragment_and_is_stripped() -> None:
    assert "location.hash" in DASHBOARD_JS
    assert 'history.replaceState(null, "", location.pathname)' in DASHBOARD_JS
    assert "deprecated and leaks via server logs" in DASHBOARD_JS
    assert not re.search(r"\?token=\s*\"?\s*\+", DASHBOARD_JS), "token concatenated into a URL"


def test_robots_noindex_and_mobile_layout() -> None:
    for needle in ('name="robots"', 'name="googlebot"', "noindex", "nofollow", "noarchive",
                   "viewport-fit=cover", "@media (max-width:720px)",
                   "prefers-color-scheme:dark",
                   # #signin sets display:grid, which beat the hidden attribute (live check)
                   "[hidden]{display:none!important}"):
        assert needle in DASHBOARD_HTML, needle


def test_page_shows_working_line_status_lines_and_connection() -> None:
    for needle in ("renderWorking", '"busy"', "working", '"status"', "conn-ok", "conn-err",
                   "scrollTop = box.scrollHeight"):
        assert needle in DASHBOARD_JS or needle in DASHBOARD_HTML, needle


def test_relay_serves_page_and_script_under_its_csp() -> None:
    from quorus.relay import app

    with TestClient(app) as c:
        page = c.get("/")
        js = c.get("/web.js")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert "script-src 'self'" in page.headers.get("content-security-policy", "")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert js.text == DASHBOARD_JS


def test_the_app_never_grows_past_the_viewport() -> None:
    """The app grid had one implicit auto row, so the chat (height:100% of it)
    sized itself to the whole message list: a 30-message room was 3800px tall
    and the Quorum stage drew its table below the fold (2026-10-18)."""
    assert "#app{display:grid;grid-template-columns:260px 1fr;grid-template-rows:minmax(0,1fr);" \
        "height:100vh;height:100dvh;overflow:hidden}" in DASHBOARD_HTML


def test_chat_keeps_the_newest_message_in_view_when_the_box_resizes() -> None:
    """The board's split layout shrinks the message box after the first render,
    which left the newest message below the fold (review of 608f638b)."""
    assert "new ResizeObserver(() => { if (wasAtBottom) box.scrollTop = box.scrollHeight; })" \
        in DASHBOARD_JS


def test_only_the_messages_scroll_not_the_page() -> None:
    """Live 2026-10-17: the grid row grew with the chat, the whole page
    scrolled, and the room list and header scrolled out of sight."""
    assert "grid-template-rows:minmax(0,1fr)" in DASHBOARD_HTML
    assert "height:100dvh;overflow:hidden" in DASHBOARD_HTML
    assert "min-height:0;height:100%" in DASHBOARD_HTML
