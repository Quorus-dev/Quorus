"""The invite page: ``GET /j/{code}``, a link an inviter can send anyone.

A short code is something you paste into a terminal. A link is something you
send in a chat, and the person opening it may not have Quorus yet, so the page
says who invited them, to which room, and the commands that get them there.

Opening the page spends nothing: chat apps fetch links to preview them, so a
GET must never use up an invite. The page shows only what the code's holder
could already read from ``/v1/join/resolve``, minus the invite token itself.
"""

from __future__ import annotations

import html
import re
import secrets
import shlex
import string

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from quorus.config import get_real_ip
from quorus.routes.join_codes import _request_relay_url
from quorus.services.join_code_svc import normalize_code

router = APIRouter(tags=["join-page"])

_NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

_CSS = """
:root{--bg:#f4f5f2;--ink:#0b0f0d;--muted:#55605a;--line:#d9dcd6;--night:#0f1412;
--night-2:#171d1a;--night-ink:#e8ece9;--mint:#3ccf9e;--mint-ink:#0a7552}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:640px;margin:0 auto;padding:48px 16px 64px}
.brand{display:inline-flex;align-items:center;gap:8px;font-weight:700;color:var(--ink);
text-decoration:none}
.dot{width:10px;height:10px;border-radius:50%;background:var(--mint)}
h1{font-size:clamp(28px,6vw,40px);line-height:1.1;letter-spacing:-.02em;margin:40px 0 12px;
text-wrap:balance}
h1 b{color:var(--mint-ink)}
.lede{color:var(--muted);margin:0 0 32px;max-width:52ch}
ol{list-style:none;margin:0;padding:0;display:grid;gap:22px;counter-reset:s}
li{counter-increment:s}
h2{font-size:16px;margin:0 0 8px}
h2::before{content:counter(s) ". ";color:var(--mint-ink)}
p{margin:8px 0 0}
.small{color:var(--muted);font-size:14px}
.cmd{display:flex;align-items:center;gap:8px;background:var(--night);border-radius:10px;
padding:6px 6px 6px 14px}
.cmd code{flex:1;min-width:0;overflow-x:auto;white-space:nowrap;color:var(--night-ink);
font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;padding:8px 0}
button{flex:none;border:0;border-radius:7px;background:var(--mint);color:#06281c;
font:600 13px/1 inherit;padding:10px 12px;cursor:pointer}
button:focus-visible,input:focus-visible,a:focus-visible{outline:2px solid var(--mint-ink);
outline-offset:2px}
label{display:block;font-weight:600;font-size:14px;margin:0 0 6px}
input{width:100%;max-width:280px;font:inherit;color:var(--ink);background:#fff;
border:1px solid var(--line);border-radius:8px;padding:9px 12px}
.or{margin:28px 0 0;padding-top:20px;border-top:1px solid var(--line)}
.foot{margin-top:40px;color:var(--muted);font-size:14px}
a{color:var(--mint-ink)}
code.i{font:14px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:#e8ebe5;
border-radius:4px;padding:1px 5px}
@media (prefers-color-scheme:dark){:root{--bg:#0b0f0d;--ink:#e8ece9;--muted:#9aa59f;
--line:#26302b;--night:#171d1a;--mint-ink:#3ccf9e}
input{background:#0f1412}code.i{background:#1c2420}}
"""

_JS = """
(function(){
var input=document.getElementById('name'),re=/^[A-Za-z0-9_-]{1,64}$/;
function fill(){var v=input.value.trim(),ok=re.test(v);
document.querySelectorAll('[data-name]').forEach(function(e){
e.textContent=ok?v:'<your-name>';});}
if(input){input.addEventListener('input',fill);fill();}
document.querySelectorAll('button[data-copy]').forEach(function(b){
b.addEventListener('click',function(){
var t=document.getElementById(b.getAttribute('data-copy')).textContent;
navigator.clipboard.writeText(t).then(function(){
b.textContent='Copied';setTimeout(function(){b.textContent='Copy';},1400);});});});
})();
"""

_PAGE = string.Template("""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta name="referrer" content="no-referrer">
<title>$title</title>
<meta property="og:title" content="$title">
<meta property="og:description" content="$description">
<meta property="og:type" content="website">
<style nonce="$nonce">$css</style>
</head>
<body>
<main>
<a class="brand" href="https://www.quorus.dev/"><span class="dot" aria-hidden="true"></span>
Quorus</a>
$body
<p class="foot">Quorus is free and open source. Your agents run on your own computer with
your own logins; the room carries the messages.
<a href="https://www.quorus.dev/">What is Quorus?</a></p>
</main>
<script nonce="$nonce">$js</script>
</body>
</html>
""")

_INVITE = string.Template("""<h1>$lead <b>#$room</b></h1>
<p class="lede">It's a Quorus room: people and their coding agents (Claude Code, Codex,
opencode) working together in one chat. Three commands in a terminal get you in.</p>
<ol>
<li><h2>Install Quorus</h2>
<div class="cmd"><code id="c1">pipx install quorus-ai</code>
<button type="button" data-copy="c1">Copy</button></div>
<p class="small">macOS or Linux, Python 3.10 or newer. No pipx?
<code class="i">brew install pipx</code></p></li>
<li><h2>Join the room</h2>
<label for="name">Your name in the room</label>
<input id="name" autocomplete="off" autocapitalize="off" spellcheck="false" maxlength="64"
placeholder="letters, digits, - or _">
<p></p>
<div class="cmd"><code id="c2">quorus join $code --name <span
data-name>&lt;your-name&gt;</span> --relay $relay_arg</code>
<button type="button" data-copy="c2">Copy</button></div>
<p class="small">This invite expires $expires.</p></li>
<li><h2>Open it</h2>
<div class="cmd"><code id="c3">quorus</code>
<button type="button" data-copy="c3">Copy</button></div>
<p class="small">Type <code class="i">/agents</code> in the room to add your own agents.
Other people's messages can't make your agents change code unless you allow it.</p></li>
</ol>
<div class="or">
<p class="small">Or install and join in one go:</p>
<div class="cmd"><code id="c4">$install_cmd</code>
<button type="button" data-copy="c4">Copy</button></div>
</div>""")

_GONE = """<h1>This invite isn't valid any more</h1>
<p class="lede">It expired, or the link is mistyped. Ask the person who sent it for a fresh
one: they run <code class="i">quorus share &lt;room&gt;</code>.</p>"""


def _render(*, title: str, description: str, body: str, status: int) -> HTMLResponse:
    nonce = secrets.token_urlsafe(16)
    page = _PAGE.substitute(title=title, description=description, body=body,
                            css=_CSS, js=_JS, nonce=nonce)
    return HTMLResponse(page, status_code=status, headers={
        "Content-Security-Policy": (
            f"default-src 'none'; style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
    })


@router.get("/j/{code}", response_class=HTMLResponse, include_in_schema=False)
async def join_page(code: str, request: Request):
    """The invite as a web page. Public and rate-limited, like resolve."""
    client_ip = get_real_ip(request)
    allowed = await request.app.state.rate_limit_service.check_with_limit(
        "global", f"join-page:{client_ip}", 60, window=60)
    if not allowed:
        raise HTTPException(status_code=429, detail="rate_limited")

    canonical = normalize_code(code)
    info = (await request.app.state.join_code_service.describe(canonical)
            if canonical else None)
    if info is None:
        return _render(title="Invite not found | Quorus",
                       description="This Quorus invite isn't valid any more.",
                       body=_GONE, status=404)

    display = f"{canonical[:4]}-{canonical[4:]}" if len(canonical) == 8 else canonical
    relay_url = _request_relay_url(request).rstrip("/")
    room = str(info.get("room_name") or "")
    inviter = str(info.get("created_by") or "")
    # A shared-secret relay's owner mints as "admin", which names nobody.
    named = bool(_NAME_RE.fullmatch(inviter)) and inviter != "admin"
    lead = f"@{inviter} invited you to" if named else "You're invited to"
    exp = info["expires_at"]
    expires = f"{exp.day} {exp:%B %Y} at {exp:%H:%M} UTC"
    install_cmd = f"curl -sSL {shlex.quote(f'{relay_url}/v1/join/install/{display}.sh')} | bash"
    body = _INVITE.substitute(
        lead=html.escape(lead), room=html.escape(room), code=html.escape(display),
        relay_arg=html.escape(shlex.quote(relay_url)), expires=html.escape(expires),
        install_cmd=html.escape(install_cmd))
    return _render(
        title=html.escape(f"Join #{room} on Quorus", quote=True),
        description=html.escape(
            f"{lead} #{room}, a room where people and their coding agents work "
            "together.", quote=True),
        body=body, status=200)
