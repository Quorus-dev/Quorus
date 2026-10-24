"""Quorus web: your rooms on a phone or laptop browser, served by the relay at ``/``.

Rewritten 2026-10-17. The old page never ran: the relay's own CSP
(``script-src 'self'``) blocked its inline script and every inline ``onclick``,
and it had latent XSS (``esc()`` kept quotes; ``location.hash`` went into fetch
paths). The rewrite keeps the CSP tight instead of loosening it:

* no inline script or handler anywhere: the page loads ``/web.js`` (same
  origin) and wires events with ``addEventListener`` and delegation;
* message text is only ever set with ``textContent`` (never ``innerHTML``);
* the token comes from the URL fragment (``#token=``), never the query
  string, and is stripped from history straight away;
* it's mobile first, with the "working…" line, quiet status lines and a send box.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

router = APIRouter()
# The stylesheet and script live in quorus/web/ (shipped with the package); the
# page inlines the CSS as before, so nothing a browser sees changes (slice 1).
_WEB = Path(__file__).parent / "web"

_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<!-- Admin surface (2026-05-16 audit B6): never index. -->
<meta name="robots" content="noindex, nofollow, noarchive">
<meta name="googlebot" content="noindex, nofollow, noarchive">
<meta name="referrer" content="no-referrer">
<title>Quorus</title>
<style>
@@CSS@@
</style>
</head>
<body>
<form id="signin" hidden>
  <h1>Quorus</h1>
  <p class="hint">Open the link from <code>quorus phone</code>, or sign in with your relay secret.</p>
  <input id="siName" placeholder="Your name" autocomplete="username" required>
  <input id="siToken" placeholder="Relay secret or API token" type="password"
         autocomplete="current-password" required>
  <button type="submit">Sign in</button><div id="err"></div>
</form>
<div id="app" hidden>
  <nav id="rooms"><h1><span id="connDot" title="connection"></span>Quorus</h1>
    <div id="roomList"></div><button id="newRoom" type="button">+ New room</button></nav>
  <main id="chat">
    <div id="bar"><button id="back" type="button">‹ Rooms</button><h2 id="roomName">Pick a room</h2>
      <button id="viewToggle" type="button">Board</button><span id="who"></span></div>
    <section id="board" hidden aria-live="polite">
      <div id="boardTop"><div id="boardSummary"></div>
        <div id="boardModes"><button id="modeList" class="on" type="button">List</button>
          <button id="modeQuorum" type="button">Quorum</button></div></div>
      <div id="boardRows"></div>
      <div id="quorum" hidden></div><div id="quorumCard" hidden></div></section>
    <div id="messages" aria-live="polite"></div>
    <div id="working"></div>
    <form id="composer"><input id="msgInput" placeholder="Message the room…" autocomplete="off">
      <button id="send" type="submit">Send</button></form>
  </main>
</div>
<script src="/web.js"></script>
<script src="/board.js"></script>
<script type="module" src="/quorum.js"></script>
</body>
</html>
"""
DASHBOARD_HTML = _HTML.replace("@@CSS@@", (_WEB / "web.css").read_text(encoding="utf-8").rstrip("\n"))

DASHBOARD_JS = "\n" + (_WEB / "web.js").read_text(encoding="utf-8")


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard():
    """Quorus web: rooms, live messages, the working line, a send box."""
    return HTMLResponse(DASHBOARD_HTML, headers={"cache-control": "no-cache"})


@router.get("/web.js", include_in_schema=False)
async def dashboard_js() -> Response:
    """The page's only script, same-origin so the CSP stays ``script-src 'self'``."""
    return Response(DASHBOARD_JS, media_type="application/javascript",
                    headers={"cache-control": "no-cache"})
