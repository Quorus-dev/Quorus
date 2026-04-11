"""Invite route handlers — HTML invite page and join-via-invite endpoint."""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import os
import string
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from murmur.auth.middleware import AuthContext, verify_auth
from murmur.routes.models import InviteJoinRequest

router = APIRouter()
_LEGACY_TENANT = "_legacy"
MAX_ROOM_MEMBERS = int(os.environ.get("MAX_ROOM_MEMBERS", "50"))
RELAY_SECRET = os.environ.get("RELAY_SECRET", "")
INVITE_SECRET = os.environ.get("INVITE_SECRET", "") or RELAY_SECRET
INVITE_TTL = int(os.environ.get("INVITE_TTL", str(24 * 60 * 60)))


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


def _make_invite_token(room_name: str) -> str:
    expires = int(time.time()) + INVITE_TTL
    payload = f"{room_name}:{expires}"
    sig = hmac_mod.new(
        INVITE_SECRET.encode(), payload.encode(), hashlib.sha256,
    ).hexdigest()
    return f"{payload}:{sig}"


def _verify_invite_token(token: str, room_name: str) -> bool:
    parts = token.rsplit(":", 2)
    if len(parts) != 3:
        return False
    claimed_room, expires_str, sig = parts
    if claimed_room != room_name:
        return False
    try:
        expires = int(expires_str)
    except ValueError:
        return False
    if time.time() > expires:
        return False
    expected_payload = f"{claimed_room}:{expires_str}"
    expected_sig = hmac_mod.new(
        INVITE_SECRET.encode(), expected_payload.encode(), hashlib.sha256,
    ).hexdigest()
    return hmac_mod.compare_digest(sig, expected_sig)


_INVITE_TMPL = string.Template("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Join $room_name — Murmur</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:#0d1117;color:#e6edf3;
display:flex;justify-content:center;align-items:center;min-height:100vh}
.card{background:#161b22;border:1px solid #30363d;border-radius:12px;
padding:2.5rem;max-width:420px;width:90%;text-align:center}
h1{font-size:1.5rem;margin-bottom:.5rem}
.room{color:#58a6ff;font-size:1.2rem;margin-bottom:1.5rem}
input{width:100%;padding:.75rem 1rem;border:1px solid #30363d;
border-radius:8px;background:#0d1117;color:#e6edf3;font-size:1rem;
margin-bottom:1rem}
input:focus{outline:none;border-color:#58a6ff}
button{width:100%;padding:.75rem;border:none;border-radius:8px;
background:#238636;color:#fff;font-size:1rem;cursor:pointer;
font-weight:600}
button:hover{background:#2ea043}
button:disabled{opacity:.5;cursor:not-allowed}
.msg{margin-top:1rem;padding:.75rem;border-radius:8px;font-size:.9rem}
.ok{background:#0d2818;border:1px solid #238636;color:#3fb950}
.err{background:#2d1117;border:1px solid #f85149;color:#f85149}
.cli{margin-top:1.5rem;text-align:left;font-size:.8rem;color:#8b949e}
code{background:#0d1117;padding:.2rem .4rem;border-radius:4px;
font-size:.85rem;color:#e6edf3}
</style>
</head>
<body>
<div class="card">
<h1>You've been invited to</h1>
<div class="room">$room_name</div>
<form id="f">
<input id="name" placeholder="Your name (e.g. aarya-agent-1)"
 required autocomplete="off">
<button type="submit">Join Room</button>
</form>
<div id="result"></div>
<div class="cli">
<p>Or join via CLI:</p>
<p><code>murmur join --name YOUR_NAME --relay $relay_url
 --secret YOUR_SECRET --room $room_name</code></p>
</div>
</div>
<script>
function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML}
const f=document.getElementById('f'),r=document.getElementById('result');
f.onsubmit=async e=>{
e.preventDefault();
const name=document.getElementById('name').value.trim();
if(!name)return;
const btn=f.querySelector('button');
btn.disabled=true;btn.textContent='Joining...';
try{
const res=await fetch('/invite/$room_name/join',{
method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify({participant:name,token:'$token'}
});
if(res.ok){
r.innerHTML='<div class="msg ok">Joined <b>'+esc('$room_name')+'</b> as <b>'
+esc(name)+'</b>!</div>';
}else{
const d=await res.json();
r.innerHTML='<div class="msg err">'+esc(d.detail||'Failed')+'</div>';
btn.disabled=false;btn.textContent='Join Room';
}
}catch(err){
r.innerHTML='<div class="msg err">'+esc(err.message)+'</div>';
btn.disabled=false;btn.textContent='Join Room';
}
};
</script>
</body></html>""")


@router.get("/invite/{room_name}", response_class=HTMLResponse)
async def invite_page(
    room_name: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    room_svc = request.app.state.room_service
    tid = _tid(auth)
    # Verify room exists (by name or ID)
    try:
        await room_svc.get(tid, room_name)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Room not found")
    relay_url = str(request.base_url).rstrip("/")
    invite_token = _make_invite_token(room_name)
    html = _INVITE_TMPL.substitute(
        room_name=room_name, relay_url=relay_url, token=invite_token,
    )
    return HTMLResponse(content=html)


@router.post("/invite/{room_name}/join")
async def invite_join(
    room_name: str,
    req: InviteJoinRequest,
    request: Request,
):
    if not _verify_invite_token(req.token, room_name):
        raise HTTPException(
            status_code=403, detail="Invalid or expired invite token",
        )
    room_svc = request.app.state.room_service
    # Find the room by name — use _LEGACY_TENANT since invites are unauthenticated
    try:
        rid, _ = await room_svc.get(_LEGACY_TENANT, room_name)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Room not found")
    await room_svc.join(_LEGACY_TENANT, rid, req.participant, "member", MAX_ROOM_MEMBERS)
    request.app.state.backends.participants.add(req.participant)
    return {"status": "joined"}
