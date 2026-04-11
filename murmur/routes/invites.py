"""Invite route handlers — HTML invite page and join-via-invite endpoint.

Uses JWT-based invite tokens via InviteService (replaces legacy HMAC tokens).
"""

from __future__ import annotations

import os
import string

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from murmur.auth.middleware import AuthContext, verify_auth
from murmur.routes.models import InviteJoinRequest

router = APIRouter()
_LEGACY_TENANT = "_legacy"
MAX_ROOM_MEMBERS = int(os.environ.get("MAX_ROOM_MEMBERS", "50"))


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


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
    invite_svc = request.app.state.invite_service
    tid = _tid(auth)
    # Resolve room by name within the caller's tenant
    try:
        room_id, room_data = await room_svc.get(tid, room_name)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Room not found")

    # Require room membership to generate invites
    issuer = auth.sub or "legacy"
    if not auth.is_legacy:
        members = await room_svc.get_members(tid, room_id)
        if issuer not in members:
            raise HTTPException(
                status_code=403,
                detail="Must be a room member to generate invites",
            )

    relay_url = str(request.base_url).rstrip("/")
    invite_token = invite_svc.create_token(
        tenant_id=tid, room_id=room_id, issuer=issuer, role="member",
    )
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
    invite_svc = request.app.state.invite_service
    # Verify the JWT invite token — raises HTTPException(403) on failure
    claims = invite_svc.verify_token(req.token)
    tenant_id = claims["tenant_id"]
    room_id = claims["room_id"]
    role = claims.get("role", "member")

    room_svc = request.app.state.room_service
    # Resolve room by ID (not name) to prevent cross-tenant collision
    try:
        rid, _ = await room_svc.get(tenant_id, room_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Room not found")
    await room_svc.join(tenant_id, rid, req.participant, role, MAX_ROOM_MEMBERS)
    await request.app.state.backends.participants.add(tenant_id, req.participant)
    return {"status": "joined"}
