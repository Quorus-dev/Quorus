"""Web dashboard — rooms, live messages, send box.

Extracted from relay.py to keep it under the 500-line rule.
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

DASHBOARD_HTML = """\
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
/* Usage stats bar */
.usage-bar{
  background:var(--bg-1);border-bottom:1px solid var(--border);
  padding:6px 20px;display:flex;gap:24px;align-items:center;flex-wrap:wrap;
}
.usage-stat{display:flex;align-items:center;gap:6px;font-size:12px}
.usage-stat .us-label{color:var(--text-3)}
.usage-stat .us-value{color:var(--text);font-weight:600;font-variant-numeric:tabular-nums}
.usage-stat .us-value.us-live{color:var(--green)}
.usage-sep{width:1px;height:16px;background:var(--border)}
.container{display:flex;height:calc(100vh - 52px - 34px)}
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
.room-item:hover{background:var(--bg-hover);color:var(--text)}
.room-item.active{background:var(--accent-s);color:var(--accent)}
.room-item .ri{opacity:.5;flex-shrink:0}
.room-item .rn{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.room-item .cnt{
  font-size:11px;color:var(--text-3);background:var(--bg-2);padding:2px 6px;border-radius:10px;
}
.room-item .unread{
  background:var(--accent);color:#fff;font-size:10px;font-weight:600;
  padding:2px 7px;border-radius:10px;min-width:18px;text-align:center;
}
/* Main split: chat + swarm panel */
.main{flex:1;display:flex;flex-direction:row;background:var(--bg-0);overflow:hidden}
.chat-col{flex:1;display:flex;flex-direction:column;min-width:0}
.room-hdr{
  padding:14px 20px;border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:12px;
}
.room-hdr .rt{font-size:14px;font-weight:600}
.room-hdr .rm{font-size:12px;color:var(--text-3);margin-left:auto}
.messages{flex:1;overflow-y:auto;padding:16px 20px;display:flex;flex-direction:column;gap:2px}
.messages::-webkit-scrollbar{width:6px}
.messages::-webkit-scrollbar-track{background:transparent}
.messages::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
.msg{font-size:13px;line-height:1.6;padding:4px 8px;border-radius:var(--r);transition:background .1s}
.msg:hover{background:var(--bg-hover)}
.msg[data-reply]{margin-left:24px;border-left:2px solid var(--border);padding-left:10px}
.msg .reply-ref{font-size:10px;color:var(--text-3);margin-right:6px;cursor:pointer}
.msg .reply-ref:hover{color:var(--accent)}
.msg .reply-btn{opacity:0;font-size:10px;color:var(--text-3);margin-left:4px;cursor:pointer;transition:opacity .15s}
.msg:hover .reply-btn{opacity:1}
.msg .reply-btn:hover{color:var(--accent)}
.msg .ts{color:var(--text-3);font-size:11px;margin-right:8px;font-variant-numeric:tabular-nums}
.msg .sender{font-weight:600;color:var(--accent);margin-right:6px}
.msg .tag{font-size:10px;font-weight:600;padding:2px 6px;border-radius:4px;margin-right:6px;text-transform:uppercase;letter-spacing:.03em;vertical-align:1px}
.tag-claim{background:rgba(245,158,11,.15);color:var(--orange)}
.tag-status{background:rgba(59,130,246,.15);color:var(--accent)}
.tag-request{background:rgba(168,85,247,.15);color:var(--purple)}
.tag-alert{background:rgba(239,68,68,.15);color:var(--red)}
.tag-sync{background:rgba(16,185,129,.15);color:var(--emerald)}
.members-bar{padding:10px 20px;border-top:1px solid var(--border);background:var(--bg-1);display:flex;align-items:center;gap:4px;flex-wrap:wrap}
.members-bar .lbl{font-size:11px;color:var(--text-3);margin-right:8px;font-weight:500}
.member{display:inline-flex;align-items:center;gap:4px;font-size:12px;color:var(--text-2);padding:3px 8px;border-radius:var(--r);background:var(--bg-2);margin:2px}
.dot{width:6px;height:6px;border-radius:50%}
.dot-online{background:var(--green)}
.dot-offline{background:var(--text-3)}
.input-bar{padding:12px 20px 16px;display:flex;gap:8px}
.input-bar input{flex:1;background:var(--bg-1);border:1px solid var(--border);border-radius:var(--r);padding:10px 14px;color:var(--text);font-size:13px;outline:none;transition:border-color .2s,box-shadow .2s}
.input-bar input:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-s)}
.input-bar input::placeholder{color:var(--text-3)}
.input-bar button{background:var(--accent);color:#fff;border:none;border-radius:var(--r);padding:10px 20px;cursor:pointer;font-size:13px;font-weight:500;transition:background .15s,transform .1s}
.input-bar button:hover{background:var(--accent-h)}
.input-bar button:active{transform:scale(.97)}
.input-bar button:disabled{opacity:.4;cursor:not-allowed;transform:none}
.empty{color:var(--text-3);text-align:center;padding:60px 20px;font-size:13px;line-height:1.6}
.empty-icon{font-size:32px;margin-bottom:8px;opacity:.5}
/* Swarm panel */
.swarm-panel{
  width:300px;flex-shrink:0;
  background:var(--bg-1);border-left:1px solid var(--border);
  display:flex;flex-direction:column;overflow:hidden;
}
.swarm-hdr{
  padding:14px 16px 10px;border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:8px;
}
.swarm-hdr .sh-title{font-size:12px;font-weight:600;color:var(--text-3);text-transform:uppercase;letter-spacing:.05em}
.swarm-hdr .sh-badge{
  margin-left:auto;font-size:10px;font-weight:600;
  padding:2px 7px;border-radius:10px;
  background:rgba(34,197,94,.15);color:var(--green);
}
.swarm-body{flex:1;overflow-y:auto;padding:12px}
.swarm-body::-webkit-scrollbar{width:4px}
.swarm-body::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}
/* Goal card */
.goal-card{
  background:var(--bg-2);border:1px solid var(--border);border-radius:var(--r);
  padding:12px;margin-bottom:12px;
}
.goal-card .gc-label{font-size:10px;font-weight:600;color:var(--text-3);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px}
.goal-card .gc-text{font-size:13px;color:var(--text);line-height:1.5;font-weight:500}
.goal-card .gc-text.gc-none{color:var(--text-3);font-style:italic;font-weight:400}
/* Section headings */
.swarm-section{font-size:10px;font-weight:600;color:var(--text-3);text-transform:uppercase;letter-spacing:.05em;margin:10px 0 6px}
/* Agents */
.agent-row{display:flex;align-items:center;gap:8px;padding:5px 0}
.agent-avatar{
  width:26px;height:26px;border-radius:50%;
  background:var(--accent-s);border:1px solid var(--accent);
  display:flex;align-items:center;justify-content:center;
  font-size:11px;font-weight:700;color:var(--accent);flex-shrink:0;position:relative;
}
.agent-avatar .online-ring{
  position:absolute;bottom:-1px;right:-1px;
  width:8px;height:8px;border-radius:50%;
  background:var(--green);border:1px solid var(--bg-1);
}
.agent-name{font-size:12px;color:var(--text);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.agent-count{font-size:11px;color:var(--text-3);font-variant-numeric:tabular-nums}
/* File locks */
.lock-badge{
  display:flex;align-items:center;gap:6px;
  padding:6px 8px;border-radius:var(--r);margin-bottom:4px;
  font-size:11px;border:1px solid;
}
.lock-badge.lock-held{
  background:rgba(239,68,68,.1);border-color:rgba(239,68,68,.3);
}
.lock-badge.lock-expiring{
  background:rgba(245,158,11,.1);border-color:rgba(245,158,11,.3);
}
.lock-badge .lb-file{flex:1;color:var(--text);font-family:monospace;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.lock-badge .lb-holder{color:var(--text-3)}
.lock-badge .lb-ttl-held{color:var(--red);font-variant-numeric:tabular-nums;flex-shrink:0}
.lock-badge .lb-ttl-exp{color:var(--orange);font-variant-numeric:tabular-nums;flex-shrink:0}
.lock-none{font-size:12px;color:var(--text-3);font-style:italic;padding:4px 0}
/* Stats row */
.stats-row{display:flex;gap:8px;margin-bottom:4px}
.stat-chip{
  flex:1;background:var(--bg-2);border:1px solid var(--border);
  border-radius:var(--r);padding:8px;text-align:center;
}
.stat-chip .sc-val{font-size:18px;font-weight:700;color:var(--text)}
.stat-chip .sc-lbl{font-size:10px;color:var(--text-3);margin-top:2px}
/* Activity timestamp */
.last-activity{font-size:11px;color:var(--text-3);margin-top:8px}
@media(max-width:900px){
  .swarm-panel{display:none}
}
@media(max-width:700px){
  .sidebar{position:fixed;left:-280px;top:52px;bottom:0;z-index:10;width:280px;transition:left .2s ease}
  .sidebar.open{left:0}
  .menu-btn{display:block !important}
  .msg{font-size:12px}
  .input-bar input{font-size:16px}
  .container{height:calc(100vh - 52px - 34px)}
}
.menu-btn{display:none;background:none;border:none;color:var(--text-2);cursor:pointer;padding:4px;border-radius:4px}
.menu-btn:hover{background:var(--bg-hover)}
.sidebar-actions{padding:8px;border-top:1px solid var(--border)}
.btn-create{width:100%;padding:8px 12px;background:var(--bg-2);color:var(--text-2);border:1px dashed var(--border);border-radius:var(--r);cursor:pointer;font-size:12px;font-weight:500;transition:all .15s}
.btn-create:hover{background:var(--bg-hover);color:var(--text);border-color:var(--accent)}
.btn-share{background:none;border:1px solid var(--border);color:var(--text-2);border-radius:var(--r);padding:5px 10px;cursor:pointer;font-size:11px;font-weight:500;transition:all .15s}
.btn-share:hover{background:var(--accent-s);color:var(--accent);border-color:var(--accent)}
.modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;align-items:center;justify-content:center;z-index:100}
.modal{background:var(--bg-1);border:1px solid var(--border);border-radius:12px;padding:24px;width:420px;max-width:90vw}
.modal h3{font-size:15px;margin-bottom:16px}
.modal input{width:100%;background:var(--bg-0);border:1px solid var(--border);border-radius:var(--r);padding:10px 14px;color:var(--text);font-size:13px;outline:none;margin-bottom:12px}
.modal input:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-s)}
.modal pre{background:var(--bg-0);border:1px solid var(--border);border-radius:var(--r);padding:12px;font-size:12px;color:var(--text-2);overflow-x:auto;margin-bottom:12px;white-space:pre-wrap;word-break:break-all}
.modal-btns{display:flex;gap:8px;justify-content:flex-end}
.modal-btns button{padding:8px 16px;border-radius:var(--r);font-size:13px;font-weight:500;cursor:pointer;border:none;transition:all .15s}
.btn-primary{background:var(--accent);color:#fff}
.btn-primary:hover{background:var(--accent-h)}
.btn-secondary{background:var(--bg-2);color:var(--text-2);border:1px solid var(--border) !important}
.btn-secondary:hover{background:var(--bg-hover)}
.copied{color:var(--green) !important}
</style>
</head>
<body>
<header>
  <button class="menu-btn" onclick="toggleSidebar()">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <path d="M3 12h18M3 6h18M3 18h18"/>
    </svg>
  </button>
  <div class="logo">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>
    </svg>
    murmur
  </div>
  <div class="conn" id="status">
    <span class="conn-dot conn-err" id="connDot"></span>
    <span id="connText">connecting</span>
  </div>
</header>
<!-- Usage stats bar -->
<div class="usage-bar" id="usageBar">
  <div class="usage-stat">
    <span class="us-label">Messages today</span>
    <span class="us-value" id="uMsgsToday">—</span>
  </div>
  <div class="usage-sep"></div>
  <div class="usage-stat">
    <span class="us-label">Active rooms</span>
    <span class="us-value" id="uRooms">—</span>
  </div>
  <div class="usage-sep"></div>
  <div class="usage-stat">
    <span class="us-label">Active agents</span>
    <span class="us-value" id="uAgents">—</span>
  </div>
  <div class="usage-sep"></div>
  <div class="usage-stat">
    <span class="us-label">msg/min</span>
    <span class="us-value us-live" id="uMsgPerMin">—</span>
  </div>
</div>
<div class="container">
  <div class="sidebar" id="sidebar">
    <div class="sidebar-hdr">Rooms</div>
    <div class="rooms-list" id="rooms"><div class="empty">Loading...</div></div>
    <div class="sidebar-actions">
      <button class="btn-create" onclick="showCreate()">+ Create Room</button>
    </div>
  </div>
  <div class="main">
    <div class="chat-col">
      <div class="room-hdr" id="roomHdr" style="display:none">
        <span class="rt" id="roomTitle"></span>
        <button class="btn-share" id="shareBtn" onclick="showShare()" style="display:none">Share</button>
        <span class="rm" id="roomMeta"></span>
      </div>
      <div class="messages" id="messages">
        <div class="empty"><div class="empty-icon">&#x1f4ac;</div>Select a room to start</div>
      </div>
      <div class="members-bar" id="members" style="display:none"></div>
      <div class="input-bar">
        <input id="msgInput" placeholder="Type a message..." disabled>
        <button id="sendBtn" onclick="sendMsg()" disabled>Send</button>
      </div>
    </div>
    <!-- Swarm Activity Panel -->
    <div class="swarm-panel" id="swarmPanel">
      <div class="swarm-hdr">
        <span class="sh-title">Swarm Activity</span>
        <span class="sh-badge" id="swarmLiveBadge">LIVE</span>
      </div>
      <div class="swarm-body">
        <!-- Goal -->
        <div class="goal-card">
          <div class="gc-label">Active Goal</div>
          <div class="gc-text gc-none" id="goalText">No active goal</div>
        </div>
        <!-- Active Agents -->
        <div class="swarm-section">Active Agents</div>
        <div id="swarmAgents"><div style="font-size:12px;color:var(--text-3)">Select a room</div></div>
        <!-- Stats -->
        <div class="swarm-section">Room Stats</div>
        <div class="stats-row">
          <div class="stat-chip"><div class="sc-val" id="swarmMsgCount">—</div><div class="sc-lbl">Messages</div></div>
          <div class="stat-chip"><div class="sc-val" id="swarmAgentCount">—</div><div class="sc-lbl">Agents</div></div>
        </div>
        <div class="last-activity" id="swarmLastActivity"></div>
        <!-- Locked Files -->
        <div class="swarm-section">File Locks</div>
        <div id="swarmLocks"><div class="lock-none">No active locks</div></div>
      </div>
    </div>
  </div>
</div>
<div id="modalRoot"></div>
<script>
const API=location.origin;
const P=new URLSearchParams(location.search);

// Security: read token from URL once, store in sessionStorage, then clear URL
// This prevents token leakage via browser history, referrers, and logs
let TOKEN=sessionStorage.getItem('murmur_token')||'';
let NAME=sessionStorage.getItem('murmur_name')||'web-user';
if(P.has('token')){
  TOKEN=P.get('token');
  sessionStorage.setItem('murmur_token',TOKEN);
  if(P.has('name')){
    NAME=P.get('name');
    sessionStorage.setItem('murmur_name',NAME);
  }
  // Clear credentials from URL (replace history entry)
  const cleanUrl=location.pathname;
  history.replaceState(null,'',cleanUrl);
}
const H={'Authorization':'Bearer '+TOKEN,'Content-Type':'application/json'};
let currentRoom=null,sse=null;
const unread={};

// Rolling message counter for msg/min
const _msgTimes=[];
function _trackMsg(){
  const now=Date.now();
  _msgTimes.push(now);
  // Keep only last 5 minutes
  const cutoff=now-5*60*1000;
  while(_msgTimes.length&&_msgTimes[0]<cutoff)_msgTimes.shift();
}
function _msgPerMin(){
  const now=Date.now();
  const cutoff=now-60*1000;
  return _msgTimes.filter(t=>t>=cutoff).length;
}

function esc(s){const d=document.createElement('div');d.textContent=String(s);return d.innerHTML}
function toggleSidebar(){document.getElementById('sidebar').classList.toggle('open')}

// ── Usage Stats ──────────────────────────────────────────────────────────────
async function updateUsageStats(){
  try{
    // Rooms count
    const rr=await fetch(API+'/rooms',{headers:H});
    if(rr.ok){
      const rooms=await rr.json();
      document.getElementById('uRooms').textContent=rooms.length;
    }
    // Analytics for messages + agents
    const ar=await fetch(API+'/analytics',{headers:H});
    if(ar.ok){
      const stats=await ar.json();
      document.getElementById('uMsgsToday').textContent=(stats.total_messages_sent||0).toLocaleString();
      const agentCount=Object.keys(stats.participants||{}).length;
      document.getElementById('uAgents').textContent=agentCount;
    }
    document.getElementById('uMsgPerMin').textContent=_msgPerMin();
  }catch(e){}
}

// ── Room State Panel ─────────────────────────────────────────────────────────
function _fmtTtl(expiresAt){
  if(!expiresAt)return '';
  const diff=Math.max(0,Math.round((new Date(expiresAt)-Date.now())/1000));
  const m=Math.floor(diff/60),s=diff%60;
  return m>0?m+'m '+String(s).padStart(2,'0')+'s':s+'s';
}
function _expiringClass(expiresAt){
  if(!expiresAt)return 'lock-held';
  const diff=(new Date(expiresAt)-Date.now())/1000;
  return diff<90?'lock-expiring':'lock-held';
}
function _ttlClass(expiresAt){
  if(!expiresAt)return 'lb-ttl-held';
  const diff=(new Date(expiresAt)-Date.now())/1000;
  return diff<90?'lb-ttl-exp':'lb-ttl-held';
}
function _fmtRelative(iso){
  if(!iso)return '';
  const diff=Math.round((Date.now()-new Date(iso))/1000);
  if(diff<5)return 'just now';
  if(diff<60)return diff+'s ago';
  if(diff<3600)return Math.floor(diff/60)+'m ago';
  return Math.floor(diff/3600)+'h ago';
}

function updateStatePanel(state){
  // Goal
  const goalEl=document.getElementById('goalText');
  if(state.active_goal){
    goalEl.textContent=state.active_goal;
    goalEl.classList.remove('gc-none');
  }else{
    goalEl.textContent='No active goal';
    goalEl.classList.add('gc-none');
  }

  // Agents
  const agents=state.active_agents||[];
  document.getElementById('swarmAgentCount').textContent=agents.length;
  const agEl=document.getElementById('swarmAgents');
  if(!agents.length){
    agEl.innerHTML='<div style="font-size:12px;color:var(--text-3)">No agents online</div>';
  }else{
    agEl.innerHTML=agents.map(a=>{
      const initials=a.replace(/[^a-zA-Z0-9]/g,'-').split('-').filter(Boolean).slice(0,2).map(p=>p[0].toUpperCase()).join('');
      return '<div class="agent-row"><div class="agent-avatar">'+esc(initials||'?')+'<span class="online-ring"></span></div><span class="agent-name">'+esc(a)+'</span></div>';
    }).join('');
  }

  // Stats
  document.getElementById('swarmMsgCount').textContent=(state.message_count||0).toLocaleString();
  const actEl=document.getElementById('swarmLastActivity');
  actEl.textContent=state.last_activity?'Last activity: '+_fmtRelative(state.last_activity):'';

  // Locks
  const locks=state.locked_files||{};
  const lockKeys=Object.keys(locks);
  const lockEl=document.getElementById('swarmLocks');
  if(!lockKeys.length){
    lockEl.innerHTML='<div class="lock-none">No active locks</div>';
  }else{
    lockEl.innerHTML=lockKeys.map(fp=>{
      const lk=locks[fp];
      const holder=lk.held_by||lk.claimed_by||'?';
      const exp=lk.expires_at||'';
      const ttl=_fmtTtl(exp);
      const bCls=_expiringClass(exp);
      const tCls=_ttlClass(exp);
      return '<div class="lock-badge '+bCls+'">'
        +'<span class="lb-file" title="'+esc(fp)+'">'+esc(fp)+'</span>'
        +'<span class="lb-holder">'+esc(holder)+'</span>'
        +(ttl?'<span class="'+tCls+'">'+esc(ttl)+'</span>':'')
        +'</div>';
    }).join('');
  }
}

let _statePoller=null;
function startStatePolling(roomName){
  if(_statePoller)clearInterval(_statePoller);
  async function poll(){
    if(!roomName)return;
    try{
      const resp=await fetch(API+'/rooms/'+encodeURIComponent(roomName)+'/state',{headers:H});
      if(resp.ok){const state=await resp.json();updateStatePanel(state);}
    }catch(e){}
  }
  poll();
  _statePoller=setInterval(poll,3000);
}

// ── Rooms / connection ───────────────────────────────────────────────────────
async function loadRooms(){
  try{
    const r=await fetch(API+'/rooms',{headers:H});
    if(!r.ok){setConn(false);return}
    const rooms=await r.json();setConn(true);
    const el=document.getElementById('rooms');
    if(!rooms.length){el.innerHTML='<div class="empty">No rooms yet</div>';return}
    el.innerHTML=rooms.map(rm=>{
      const u=unread[rm.name]||0;
      const badge=u?'<span class="unread">'+esc(u)+'</span>':'<span class="cnt">'+esc(rm.members.length)+'</span>';
      const act=rm.name===currentRoom?' active':'';
      return '<div class="room-item'+act+'" onclick="selectRoom(\''+esc(rm.name)+'\')"><span class="ri">#</span><span class="rn">'+esc(rm.name)+'</span>'+badge+'</div>';
    }).join('');
  }catch(e){setConn(false)}
}
async function selectRoom(name){
  currentRoom=name;unread[name]=0;
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('msgInput').disabled=false;
  document.getElementById('sendBtn').disabled=false;
  loadRooms();
  try{const r=await fetch(API+'/rooms/'+name+'/history?limit=100',{headers:H});const msgs=await r.json();const el=document.getElementById('messages');el.innerHTML=msgs.map(formatMsg).join('');scrollToBottom()}catch(e){}
  try{
    const r=await fetch(API+'/rooms/'+name,{headers:H});const room=await r.json();
    document.getElementById('roomHdr').style.display='flex';
    document.getElementById('roomTitle').textContent='# '+name;
    document.getElementById('shareBtn').style.display='inline-block';
    document.getElementById('roomMeta').textContent=room.members.length+' members';
    const pr=await fetch(API+'/presence',{headers:H});const presence=await pr.json();
    const online=new Set(presence.filter(p=>p.online).map(p=>p.name));
    const mb=document.getElementById('members');mb.style.display='flex';
    mb.innerHTML='<span class="lbl">Members</span>'+room.members.map(m=>{
      const d=online.has(m)?'dot-online':'dot-offline';
      return '<span class="member"><span class="dot '+d+'"></span>'+esc(m)+'</span>';
    }).join('');
  }catch(e){}
  startStatePolling(name);
  connectSSE();
}
function setConn(ok){
  document.getElementById('connDot').className='conn-dot '+(ok?'conn-ok':'conn-err');
  document.getElementById('connText').textContent=ok?'connected':'reconnecting';
}
function scrollToBottom(){const el=document.getElementById('messages');requestAnimationFrame(()=>{el.scrollTop=el.scrollHeight})}
async function connectSSE(){
  if(sse)sse.close();let sseToken=TOKEN;
  try{const r=await fetch(API+'/stream/token',{method:'POST',headers:H,body:JSON.stringify({recipient:NAME})});if(r.ok){const d=await r.json();sseToken=d.token;}}catch(e){}
  sse=new EventSource(API+'/stream/'+NAME+'?token='+sseToken);
  sse.onopen=()=>setConn(true);sse.onerror=()=>setConn(false);
  sse.addEventListener('message',e=>{
    try{
      const msg=JSON.parse(e.data);
      _trackMsg();
      if(msg.room===currentRoom){document.getElementById('messages').innerHTML+=formatMsg(msg);scrollToBottom()}
      else if(msg.room){unread[msg.room]=(unread[msg.room]||0)+1;loadRooms()}
    }catch(e){}
  });
}
let replyTo=null;
function formatMsg(msg){
  const ts=esc((msg.timestamp||'').substring(11,19));const type=msg.message_type||'chat';const mid=msg.id||'';
  const safeType=['claim','status','request','alert','sync'].includes(type)?type:'chat';
  let tag='';if(safeType!=='chat'){tag='<span class="tag tag-'+safeType+'">'+esc(type)+'</span>'}
  const ra=msg.reply_to?' data-reply="'+msg.reply_to+'"':'';
  const rr=msg.reply_to?'<span class="reply-ref" onclick="scrollToMsg(\''+msg.reply_to+'\')">&#8627;</span>':'';
  const rb='<span class="reply-btn" onclick="setReply(\''+mid+'\',\''+esc(msg.from_name||'')+'\')">reply</span>';
  return '<div class="msg" id="msg-'+mid+'"'+ra+'><span class="ts">'+ts+'</span><span class="sender">'+esc(msg.from_name||'?')+'</span>'+rr+tag+esc(msg.content||'')+rb+'</div>';
}
function setReply(id,name){replyTo=id;const input=document.getElementById('msgInput');input.placeholder='Replying to '+name+'...';input.focus()}
function scrollToMsg(id){const el=document.getElementById('msg-'+id);if(el){el.style.background='var(--accent-s)';el.scrollIntoView({behavior:'smooth',block:'center'});setTimeout(()=>{el.style.background=''},1500)}}
async function sendMsg(){
  const input=document.getElementById('msgInput');const text=input.value.trim();
  if(!text||!currentRoom)return;input.value='';
  const body={from_name:NAME,content:text};if(replyTo)body.reply_to=replyTo;replyTo=null;input.placeholder='Type a message...';
  _trackMsg();
  try{await fetch(API+'/rooms/'+currentRoom+'/messages',{method:'POST',headers:H,body:JSON.stringify(body)})}catch(e){}
}
document.getElementById('msgInput').addEventListener('keydown',e=>{if(e.key==='Enter')sendMsg()});
async function refreshPresence(){
  if(!currentRoom)return;
  try{const r=await fetch(API+'/rooms/'+currentRoom,{headers:H});const room=await r.json();const pr=await fetch(API+'/presence',{headers:H});const presence=await pr.json();const online=new Set(presence.filter(p=>p.online).map(p=>p.name));const mb=document.getElementById('members');mb.innerHTML='<span class="lbl">Members</span>'+room.members.map(m=>{const d=online.has(m)?'dot-online':'dot-offline';return '<span class="member"><span class="dot '+d+'"></span>'+esc(m)+'</span>'}).join('')}catch(e){}
}
function closeModal(){document.getElementById('modalRoot').innerHTML=''}
function showCreate(){
  const m=document.getElementById('modalRoot');
  m.innerHTML='<div class="modal-bg" onclick="if(event.target===this)closeModal()"><div class="modal"><h3>Create Room</h3><input id="newRoomName" placeholder="room-name" onkeydown="if(event.key===\\'Enter\\')createRoom()"><div class="modal-btns"><button class="btn-secondary" onclick="closeModal()">Cancel</button><button class="btn-primary" onclick="createRoom()">Create</button></div></div></div>';
  document.getElementById('newRoomName').focus();
}
async function createRoom(){
  const input=document.getElementById('newRoomName');const name=input.value.trim().toLowerCase().replace(/[^a-z0-9-]/g,'-');
  if(!name)return;
  try{await fetch(API+'/rooms',{method:'POST',headers:H,body:JSON.stringify({name:name})});closeModal();await loadRooms();selectRoom(name)}catch(e){}
}
function showShare(){
  if(!currentRoom)return;
  const cmd='murmur join --name <agent-name> --relay '+location.origin+' --secret <your-secret> --room '+currentRoom;
  const m=document.getElementById('modalRoot');
  m.innerHTML='<div class="modal-bg" onclick="if(event.target===this)closeModal()"><div class="modal"><h3>Share Room</h3><p style="font-size:12px;color:var(--text-2);margin-bottom:12px">Copy this command to add an agent:</p><pre id="shareCmd">'+cmd+'</pre><div class="modal-btns"><button class="btn-secondary" onclick="closeModal()">Close</button><button class="btn-primary" id="copyBtn" onclick="copyShare()">Copy</button></div></div></div>';
}
async function copyShare(){
  const cmd=document.getElementById('shareCmd').textContent;
  try{await navigator.clipboard.writeText(cmd);const btn=document.getElementById('copyBtn');btn.textContent='Copied!';btn.classList.add('copied');setTimeout(()=>{btn.textContent='Copy';btn.classList.remove('copied')},2000)}catch(e){}
}
// Auto-select room from URL hash or first room
async function autoSelectRoom(){
  const hash=location.hash.replace('#','');
  if(hash){selectRoom(decodeURIComponent(hash));return;}
  try{
    const r=await fetch(API+'/rooms',{headers:H});
    if(r.ok){const rooms=await r.json();if(rooms.length)selectRoom(rooms[0].name);}
  }catch(e){}
}
// Boot
loadRooms();
updateUsageStats();
autoSelectRoom();
setInterval(()=>{loadRooms();refreshPresence();},30000);
setInterval(updateUsageStats,15000);
// Keep msg/min counter fresh every 10s even without new messages
setInterval(()=>{document.getElementById('uMsgPerMin').textContent=_msgPerMin();},10000);
</script>
</body>
</html>
"""


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard():
    """Web dashboard — rooms, live messages, send box."""
    return DASHBOARD_HTML
