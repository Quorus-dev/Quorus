"""Murmur Relay — FastAPI app setup, lifespan, middleware, and router wiring.

Route handlers live in relay_routes.py. Auth/admin routes in their own packages.
"""

import asyncio
import json
import logging
import os
import tempfile
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from prometheus_fastapi_instrumentator import Instrumentator
from starlette.middleware.base import BaseHTTPMiddleware

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()
        if LOG_LEVEL == "DEBUG"
        else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(logging, LOG_LEVEL, logging.INFO)
    ),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger("murmur.relay")

RELAY_SECRET = os.environ.get("RELAY_SECRET", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
MESSAGES_FILE = os.environ.get("MESSAGES_FILE", "messages.json")

# Fail fast only if neither auth mechanism is configured
if not RELAY_SECRET and not DATABASE_URL:
    raise SystemExit(
        "Neither RELAY_SECRET nor DATABASE_URL is set. "
        "Set at least one to start the relay."
    )


# ---------------------------------------------------------------------------
# Persistence helpers (JSON file — used when Postgres is not configured)
# ---------------------------------------------------------------------------


def _write_atomic(path: str, data: bytes) -> None:
    dir_name = os.path.dirname(os.path.abspath(path))
    try:
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp_path, path)
    except OSError:
        logger.error("Failed to save state to %s", path)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _save_to_file():
    from murmur.relay_routes import snapshot_state
    data = snapshot_state()
    encoded = json.dumps(data, indent=2).encode("utf-8")
    _write_atomic(MESSAGES_FILE, encoded)


async def _persist_state():
    from murmur.relay_routes import persistence_lock
    async with persistence_lock:
        await asyncio.to_thread(_save_to_file)


def _load_from_file():
    from murmur.relay_routes import apply_loaded_state
    if not os.path.exists(MESSAGES_FILE):
        return
    try:
        with open(MESSAGES_FILE) as f:
            data = json.load(f)
    except (json.JSONDecodeError, ValueError, OSError):
        logger.warning("Corrupt persistence file %s, starting fresh", MESSAGES_FILE)
        return
    apply_loaded_state(data)
    logger.info("Loaded state from %s", MESSAGES_FILE)


async def _run_migrations():
    """Run Alembic migrations to head (auto-upgrade on startup)."""
    try:
        from alembic import command
        from alembic.config import Config

        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", "murmur/migrations")
        alembic_cfg.set_main_option("sqlalchemy.url", DATABASE_URL)

        await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
        logger.info("Alembic migrations applied successfully")
    except Exception:
        logger.error("Failed to run migrations", exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app):
    from murmur.relay_routes import set_persistence_hooks, snapshot_state

    logger.info("Relay server starting up")

    # Initialize Postgres if configured
    if DATABASE_URL:
        from murmur.storage.postgres import init_engine
        await init_engine(DATABASE_URL)
        await _run_migrations()
        logger.info("Postgres initialized")

    # Set up persistence hooks for relay_routes
    set_persistence_hooks(_persist_state, snapshot_state)

    # Load JSON state (works alongside Postgres for in-memory caches)
    await asyncio.to_thread(_load_from_file)
    if _expire_stale_messages():
        await _persist_state()

    yield

    logger.info("Relay server shutting down — saving state")
    await _persist_state()

    # Close Postgres if it was initialized
    if DATABASE_URL:
        from murmur.storage.postgres import close_engine
        await close_engine()

    if _webhook_http_client and not _webhook_http_client.is_closed:
        await _webhook_http_client.aclose()


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Murmur Relay",
    description=(
        "Real-time messaging relay for AI agent coordination. "
        "Any agent that speaks HTTP can join rooms, send messages, "
        "and coordinate work through this API."
    ),
    version="0.3.0",
    lifespan=lifespan,
)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Inject request_id, security headers, and structlog context."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["x-frame-options"] = "DENY"
        if "text/html" in response.headers.get("content-type", ""):
            response.headers["content-security-policy"] = (
                "default-src 'self'; "
                "script-src 'unsafe-inline'; "
                "style-src 'unsafe-inline'; "
                "connect-src 'self'; "
                "frame-ancestors 'none'"
            )
        return response


app.add_middleware(RequestContextMiddleware)
Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

# CORS
_cors_origins = os.environ.get("CORS_ORIGINS", "")
if _cors_origins:
    from starlette.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in _cors_origins.split(",")],
        allow_methods=["GET", "POST", "DELETE", "PATCH"],
        allow_headers=["Authorization", "Content-Type", "Bootstrap-Secret"],
    )

# ---------------------------------------------------------------------------
# Wire up routers
# ---------------------------------------------------------------------------

from murmur.admin.routes import router as admin_router  # noqa: E402
from murmur.auth.routes import router as auth_router  # noqa: E402
from murmur.relay_routes import router as relay_router  # noqa: E402

app.include_router(relay_router)
app.include_router(auth_router)
app.include_router(admin_router)


# ---------------------------------------------------------------------------
# Dashboard (HTML served at GET /)
# ---------------------------------------------------------------------------

_DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Murmur</title>
<style>
:root{
  --bg-0:#09090b;--bg-1:#111113;--bg-2:#18181b;
  --bg-hover:#1e1e22;--border:#27272a;
  --text:#fafafa;--text-2:#a1a1aa;--text-3:#52525b;
  --accent:#3b82f6;--accent-h:#2563eb;
  --accent-s:rgba(59,130,246,.1);
  --green:#22c55e;--red:#ef4444;--orange:#f59e0b;
  --purple:#a855f7;--emerald:#10b981;
  --r:8px;
}
*{box-sizing:border-box;margin:0;padding:0}
body{
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',
  'Inter',system-ui,sans-serif;
  background:var(--bg-0);color:var(--text);
  -webkit-font-smoothing:antialiased;
}
header{
  background:var(--bg-1);
  border-bottom:1px solid var(--border);
  padding:0 20px;height:52px;
  display:flex;align-items:center;gap:12px;
}
.logo{
  font-size:15px;font-weight:600;
  letter-spacing:-.02em;
  display:flex;align-items:center;gap:8px;
}
.logo svg{width:20px;height:20px;opacity:.9}
.conn{
  margin-left:auto;display:flex;
  align-items:center;gap:6px;
  font-size:12px;color:var(--text-2);
}
.conn-dot{
  width:7px;height:7px;border-radius:50%;
  transition:background .3s;
}
.conn-ok{background:var(--green)}
.conn-err{background:var(--red);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.container{display:flex;height:calc(100vh - 52px)}
.sidebar{
  width:260px;background:var(--bg-1);
  border-right:1px solid var(--border);
  display:flex;flex-direction:column;flex-shrink:0;
}
.sidebar-hdr{
  padding:16px 16px 12px;font-size:11px;
  font-weight:600;color:var(--text-3);
  text-transform:uppercase;letter-spacing:.05em;
}
.rooms-list{flex:1;overflow-y:auto;padding:0 8px}
.room-item{
  padding:10px 12px;cursor:pointer;
  border-radius:var(--r);font-size:13px;
  font-weight:500;color:var(--text-2);
  display:flex;align-items:center;gap:8px;
  transition:all .15s;margin-bottom:2px;
}
.room-item:hover{
  background:var(--bg-hover);color:var(--text);
}
.room-item.active{
  background:var(--accent-s);color:var(--accent);
}
.room-item .ri{opacity:.5;flex-shrink:0}
.room-item .rn{
  flex:1;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;
}
.room-item .cnt{
  font-size:11px;color:var(--text-3);
  background:var(--bg-2);
  padding:2px 6px;border-radius:10px;
}
.room-item .unread{
  background:var(--accent);color:#fff;
  font-size:10px;font-weight:600;
  padding:2px 7px;border-radius:10px;
  min-width:18px;text-align:center;
}
.main{
  flex:1;display:flex;flex-direction:column;
  background:var(--bg-0);
}
.room-hdr{
  padding:14px 20px;
  border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:12px;
}
.room-hdr .rt{font-size:14px;font-weight:600}
.room-hdr .rm{
  font-size:12px;color:var(--text-3);
  margin-left:auto;
}
.messages{
  flex:1;overflow-y:auto;padding:16px 20px;
  display:flex;flex-direction:column;gap:2px;
}
.messages::-webkit-scrollbar{width:6px}
.messages::-webkit-scrollbar-track{background:transparent}
.messages::-webkit-scrollbar-thumb{
  background:var(--border);border-radius:3px;
}
.msg{
  font-size:13px;line-height:1.6;
  padding:4px 8px;border-radius:var(--r);
  transition:background .1s;
}
.msg:hover{background:var(--bg-hover)}
.msg .ts{
  color:var(--text-3);font-size:11px;
  margin-right:8px;
  font-variant-numeric:tabular-nums;
}
.msg .sender{
  font-weight:600;color:var(--accent);
  margin-right:6px;
}
.msg .tag{
  font-size:10px;font-weight:600;
  padding:2px 6px;border-radius:4px;
  margin-right:6px;text-transform:uppercase;
  letter-spacing:.03em;vertical-align:1px;
}
.tag-claim{
  background:rgba(245,158,11,.15);color:var(--orange);
}
.tag-status{
  background:rgba(59,130,246,.15);color:var(--accent);
}
.tag-request{
  background:rgba(168,85,247,.15);color:var(--purple);
}
.tag-alert{
  background:rgba(239,68,68,.15);color:var(--red);
}
.tag-sync{
  background:rgba(16,185,129,.15);color:var(--emerald);
}
.members-bar{
  padding:10px 20px;
  border-top:1px solid var(--border);
  background:var(--bg-1);
  display:flex;align-items:center;gap:4px;
  flex-wrap:wrap;
}
.members-bar .lbl{
  font-size:11px;color:var(--text-3);
  margin-right:8px;font-weight:500;
}
.member{
  display:inline-flex;align-items:center;gap:4px;
  font-size:12px;color:var(--text-2);
  padding:3px 8px;border-radius:var(--r);
  background:var(--bg-2);margin:2px;
}
.dot{width:6px;height:6px;border-radius:50%}
.dot-online{background:var(--green)}
.dot-offline{background:var(--text-3)}
.input-bar{
  padding:12px 20px 16px;display:flex;gap:8px;
}
.input-bar input{
  flex:1;background:var(--bg-1);
  border:1px solid var(--border);
  border-radius:var(--r);padding:10px 14px;
  color:var(--text);font-size:13px;outline:none;
  transition:border-color .2s,box-shadow .2s;
}
.input-bar input:focus{
  border-color:var(--accent);
  box-shadow:0 0 0 3px var(--accent-s);
}
.input-bar input::placeholder{color:var(--text-3)}
.input-bar button{
  background:var(--accent);color:#fff;
  border:none;border-radius:var(--r);
  padding:10px 20px;cursor:pointer;
  font-size:13px;font-weight:500;
  transition:background .15s,transform .1s;
}
.input-bar button:hover{background:var(--accent-h)}
.input-bar button:active{transform:scale(.97)}
.input-bar button:disabled{
  opacity:.4;cursor:not-allowed;transform:none;
}
.empty{
  color:var(--text-3);text-align:center;
  padding:60px 20px;font-size:13px;line-height:1.6;
}
.empty-icon{font-size:32px;margin-bottom:8px;opacity:.5}
@media(max-width:700px){
  .sidebar{
    position:fixed;left:-280px;top:52px;
    bottom:0;z-index:10;width:280px;
    transition:left .2s ease;
  }
  .sidebar.open{left:0}
  .menu-btn{display:block !important}
  .msg{font-size:12px}
  .input-bar input{font-size:16px}
}
.menu-btn{
  display:none;background:none;border:none;
  color:var(--text-2);cursor:pointer;
  padding:4px;border-radius:4px;
}
.menu-btn:hover{background:var(--bg-hover)}
.sidebar-actions{padding:8px;border-top:1px solid var(--border)}
.btn-create{
  width:100%;padding:8px 12px;
  background:var(--bg-2);color:var(--text-2);
  border:1px dashed var(--border);
  border-radius:var(--r);cursor:pointer;
  font-size:12px;font-weight:500;
  transition:all .15s;
}
.btn-create:hover{
  background:var(--bg-hover);color:var(--text);
  border-color:var(--accent);
}
.btn-share{
  background:none;border:1px solid var(--border);
  color:var(--text-2);border-radius:var(--r);
  padding:5px 10px;cursor:pointer;font-size:11px;
  font-weight:500;transition:all .15s;
}
.btn-share:hover{
  background:var(--accent-s);color:var(--accent);
  border-color:var(--accent);
}
.modal-bg{
  position:fixed;inset:0;background:rgba(0,0,0,.6);
  display:flex;align-items:center;
  justify-content:center;z-index:100;
}
.modal{
  background:var(--bg-1);border:1px solid var(--border);
  border-radius:12px;padding:24px;width:420px;
  max-width:90vw;
}
.modal h3{font-size:15px;margin-bottom:16px}
.modal input{
  width:100%;background:var(--bg-0);
  border:1px solid var(--border);
  border-radius:var(--r);padding:10px 14px;
  color:var(--text);font-size:13px;
  outline:none;margin-bottom:12px;
}
.modal input:focus{
  border-color:var(--accent);
  box-shadow:0 0 0 3px var(--accent-s);
}
.modal pre{
  background:var(--bg-0);border:1px solid var(--border);
  border-radius:var(--r);padding:12px;
  font-size:12px;color:var(--text-2);
  overflow-x:auto;margin-bottom:12px;
  white-space:pre-wrap;word-break:break-all;
}
.modal-btns{display:flex;gap:8px;justify-content:flex-end}
.modal-btns button{
  padding:8px 16px;border-radius:var(--r);
  font-size:13px;font-weight:500;cursor:pointer;
  border:none;transition:all .15s;
}
.btn-primary{background:var(--accent);color:#fff}
.btn-primary:hover{background:var(--accent-h)}
.btn-secondary{
  background:var(--bg-2);color:var(--text-2);
  border:1px solid var(--border) !important;
}
.btn-secondary:hover{background:var(--bg-hover)}
.copied{color:var(--green) !important}
</style>
</head>
<body>
<header>
  <button class="menu-btn" onclick="toggleSidebar()">
    <svg width="18" height="18" viewBox="0 0 24 24"
      fill="none" stroke="currentColor" stroke-width="2">
      <path d="M3 12h18M3 6h18M3 18h18"/>
    </svg>
  </button>
  <div class="logo">
    <svg viewBox="0 0 24 24" fill="none"
      stroke="currentColor" stroke-width="2">
      <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2
        2 0 012-2h14a2 2 0 012 2z"/>
    </svg>
    murmur
  </div>
  <div class="conn" id="status">
    <span class="conn-dot conn-err" id="connDot"></span>
    <span id="connText">connecting</span>
  </div>
</header>
<div class="container">
  <div class="sidebar" id="sidebar">
    <div class="sidebar-hdr">Rooms</div>
    <div class="rooms-list" id="rooms">
      <div class="empty">Loading...</div>
    </div>
    <div class="sidebar-actions">
      <button class="btn-create" onclick="showCreate()">
        + Create Room
      </button>
    </div>
  </div>
  <div class="main">
    <div class="room-hdr" id="roomHdr" style="display:none">
      <span class="rt" id="roomTitle"></span>
      <button class="btn-share" id="shareBtn"
        onclick="showShare()" style="display:none">
        Share
      </button>
      <span class="rm" id="roomMeta"></span>
    </div>
    <div class="messages" id="messages">
      <div class="empty">
        <div class="empty-icon">&#x1f4ac;</div>
        Select a room to start
      </div>
    </div>
    <div class="members-bar" id="members"
      style="display:none"></div>
    <div class="input-bar">
      <input id="msgInput"
        placeholder="Type a message..." disabled>
      <button id="sendBtn" onclick="sendMsg()"
        disabled>Send</button>
    </div>
  </div>
</div>
<div id="modalRoot"></div>
<script>
const API=location.origin;
const P=new URLSearchParams(location.search);
const TOKEN=P.get('token')||'';
const NAME=P.get('name')||'web-user';
const H={
  'Authorization':'Bearer '+TOKEN,
  'Content-Type':'application/json'
};
let currentRoom=null,sse=null;
const unread={};
function esc(s){const d=document.createElement('div');d.textContent=String(s);return d.innerHTML}

function toggleSidebar(){
  document.getElementById('sidebar').classList.toggle('open');
}

async function loadRooms(){
  try{
    const r=await fetch(API+'/rooms',{headers:H});
    if(!r.ok){setConn(false);return}
    const rooms=await r.json();
    setConn(true);
    const el=document.getElementById('rooms');
    if(!rooms.length){
      el.innerHTML='<div class="empty">No rooms yet</div>';
      return;
    }
    el.innerHTML=rooms.map(rm=>{
      const u=unread[rm.name]||0;
      const badge=u
        ?'<span class="unread">'+esc(u)+'</span>'
        :'<span class="cnt">'+esc(rm.members.length)+'</span>';
      const act=rm.name===currentRoom?' active':'';
      return '<div class="room-item'+act
        +'" onclick="selectRoom(\''+esc(rm.name)+'\')">'+
        '<span class="ri">#</span>'+
        '<span class="rn">'+esc(rm.name)+'</span>'+
        badge+'</div>';
    }).join('');
  }catch(e){setConn(false)}
}

async function selectRoom(name){
  currentRoom=name;
  unread[name]=0;
  document.getElementById('sidebar')
    .classList.remove('open');
  document.getElementById('msgInput').disabled=false;
  document.getElementById('sendBtn').disabled=false;
  loadRooms();
  try{
    const r=await fetch(
      API+'/rooms/'+name+'/history?limit=100',
      {headers:H}
    );
    const msgs=await r.json();
    const el=document.getElementById('messages');
    el.innerHTML=msgs.map(formatMsg).join('');
    scrollToBottom();
  }catch(e){}
  try{
    const r=await fetch(
      API+'/rooms/'+name,{headers:H}
    );
    const room=await r.json();
    const hdr=document.getElementById('roomHdr');
    hdr.style.display='flex';
    document.getElementById('roomTitle')
      .textContent='# '+name;
    document.getElementById('shareBtn')
      .style.display='inline-block';
    document.getElementById('roomMeta')
      .textContent=room.members.length+' members';
    const pr=await fetch(API+'/presence',{headers:H});
    const presence=await pr.json();
    const online=new Set(
      presence.filter(p=>p.online).map(p=>p.name)
    );
    const mb=document.getElementById('members');
    mb.style.display='flex';
    mb.innerHTML='<span class="lbl">Members</span>'+
      room.members.map(m=>{
        const d=online.has(m)?'dot-online':'dot-offline';
        return '<span class="member">'+
          '<span class="dot '+d+'"></span>'+
          esc(m)+'</span>';
      }).join('');
  }catch(e){}
  connectSSE();
}

function setConn(ok){
  document.getElementById('connDot').className=
    'conn-dot '+(ok?'conn-ok':'conn-err');
  document.getElementById('connText').textContent=
    ok?'connected':'reconnecting';
}

function scrollToBottom(){
  const el=document.getElementById('messages');
  requestAnimationFrame(
    ()=>{el.scrollTop=el.scrollHeight}
  );
}

async function connectSSE(){
  if(sse)sse.close();
  let sseToken=TOKEN;
  try{
    const r=await fetch(API+'/stream/token',{method:'POST',headers:H,
      body:JSON.stringify({recipient:NAME})});
    if(r.ok){const d=await r.json();sseToken=d.token;}
  }catch(e){}
  sse=new EventSource(
    API+'/stream/'+NAME+'?token='+sseToken
  );
  sse.onopen=()=>setConn(true);
  sse.onerror=()=>setConn(false);
  sse.addEventListener('message',e=>{
    try{
      const msg=JSON.parse(e.data);
      if(msg.room===currentRoom){
        const el=document.getElementById('messages');
        el.innerHTML+=formatMsg(msg);
        scrollToBottom();
      }else if(msg.room){
        unread[msg.room]=(unread[msg.room]||0)+1;
        loadRooms();
      }
    }catch(e){}
  });
}

function formatMsg(msg){
  const ts=esc((msg.timestamp||'').substring(11,19));
  const type=msg.message_type||'chat';
  const safeType=['claim','status','request','alert','sync'].includes(type)?type:'chat';
  let tag='';
  if(safeType!=='chat'){
    tag='<span class="tag tag-'+safeType+'">'+
      esc(type)+'</span>';
  }
  return '<div class="msg"><span class="ts">'+ts+
    '</span><span class="sender">'+
    esc(msg.from_name||'?')+'</span>'+
    tag+esc(msg.content||'')+'</div>';
}

async function sendMsg(){
  const input=document.getElementById('msgInput');
  const text=input.value.trim();
  if(!text||!currentRoom)return;
  input.value='';
  try{await fetch(
    API+'/rooms/'+currentRoom+'/messages',{
      method:'POST',headers:H,
      body:JSON.stringify({from_name:NAME,content:text})
    }
  )}catch(e){}
}

document.getElementById('msgInput').addEventListener(
  'keydown',e=>{if(e.key==='Enter')sendMsg()}
);

async function refreshPresence(){
  if(!currentRoom)return;
  try{
    const r=await fetch(
      API+'/rooms/'+currentRoom,{headers:H}
    );
    const room=await r.json();
    const pr=await fetch(API+'/presence',{headers:H});
    const presence=await pr.json();
    const online=new Set(
      presence.filter(p=>p.online).map(p=>p.name)
    );
    const mb=document.getElementById('members');
    mb.innerHTML='<span class="lbl">Members</span>'+
      room.members.map(m=>{
        const d=online.has(m)?'dot-online':'dot-offline';
        return '<span class="member">'+
          '<span class="dot '+d+'"></span>'+
          esc(m)+'</span>';
      }).join('');
  }catch(e){}
}
function closeModal(){
  document.getElementById('modalRoot').innerHTML='';
}

function showCreate(){
  const m=document.getElementById('modalRoot');
  m.innerHTML='<div class="modal-bg" onclick="'+
    'if(event.target===this)closeModal()">'+
    '<div class="modal"><h3>Create Room</h3>'+
    '<input id="newRoomName" placeholder="room-name"'+
    ' onkeydown="if(event.key===\\'Enter\\')createRoom()">'+
    '<div class="modal-btns">'+
    '<button class="btn-secondary" onclick="closeModal()">'+
    'Cancel</button>'+
    '<button class="btn-primary" onclick="createRoom()">'+
    'Create</button></div></div></div>';
  document.getElementById('newRoomName').focus();
}

async function createRoom(){
  const input=document.getElementById('newRoomName');
  const name=input.value.trim().toLowerCase()
    .replace(/[^a-z0-9-]/g,'-');
  if(!name)return;
  try{
    await fetch(API+'/rooms',{
      method:'POST',headers:H,
      body:JSON.stringify({name:name})
    });
    closeModal();
    await loadRooms();
    selectRoom(name);
  }catch(e){}
}

function showShare(){
  if(!currentRoom)return;
  const cmd='murmur join --name <agent-name>'+
    ' --relay '+location.origin+
    ' --secret <your-secret>'+
    ' --room '+currentRoom;
  const m=document.getElementById('modalRoot');
  m.innerHTML='<div class="modal-bg" onclick="'+
    'if(event.target===this)closeModal()">'+
    '<div class="modal"><h3>Share Room</h3>'+
    '<p style="font-size:12px;color:var(--text-2);'+
    'margin-bottom:12px">'+
    'Copy this command to add an agent:</p>'+
    '<pre id="shareCmd">'+cmd+'</pre>'+
    '<div class="modal-btns">'+
    '<button class="btn-secondary" onclick="closeModal()">'+
    'Close</button>'+
    '<button class="btn-primary" id="copyBtn"'+
    ' onclick="copyShare()">'+
    'Copy</button></div></div></div>';
}

async function copyShare(){
  const cmd=document.getElementById('shareCmd')
    .textContent;
  try{
    await navigator.clipboard.writeText(cmd);
    const btn=document.getElementById('copyBtn');
    btn.textContent='Copied!';
    btn.classList.add('copied');
    setTimeout(()=>{
      btn.textContent='Copy';
      btn.classList.remove('copied');
    },2000);
  }catch(e){}
}

loadRooms();
setInterval(()=>{loadRooms();refreshPresence()},30000);
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard():
    """Web dashboard — rooms, live messages, send box."""
    return _DASHBOARD_HTML


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the relay server as a CLI entrypoint."""
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)


# ---------------------------------------------------------------------------
# Backward-compatible re-exports (used by existing tests)
# ---------------------------------------------------------------------------

from murmur.relay_routes import (  # noqa: E402, I001
    INVITE_SECRET as INVITE_SECRET,  # noqa: F401
    MAX_MESSAGE_SIZE as MAX_MESSAGE_SIZE,  # noqa: F401
    MAX_MESSAGES as MAX_MESSAGES,  # noqa: F401
    RATE_LIMIT_MAX as RATE_LIMIT_MAX,  # noqa: F401
    RATE_LIMIT_WINDOW as RATE_LIMIT_WINDOW,  # noqa: F401
    _expire_stale_messages as _expire_stale_messages,  # noqa: F401
    _make_invite_token as _make_invite_token,  # noqa: F401
    _verify_invite_token as _verify_invite_token,  # noqa: F401
    _webhook_http_client as _webhook_http_client,  # noqa: F401
    apply_loaded_state as _apply_loaded_state,  # noqa: F401
    locks as locks,  # noqa: F401
    message_events as message_events,  # noqa: F401
    message_queues as message_queues,  # noqa: F401
    participants as participants,  # noqa: F401
    presence as presence,  # noqa: F401
    reset_state as _reset_state,  # noqa: F401
    room_history as room_history,  # noqa: F401
    rooms as rooms,  # noqa: F401
    snapshot_state as _snapshot_state,  # noqa: F401
    sse_queues as sse_queues,  # noqa: F401
    stream_messages as stream_messages,  # noqa: F401
    webhooks as webhooks,  # noqa: F401
)

if __name__ == "__main__":
    main()
