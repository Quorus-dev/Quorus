// The board's pure logic: what each agent's row says, from presence, room history and
// pending approvals (2026-10-17). No DOM and no fetch in here, so the node tests run this
// file as-is (tests/test_board.py) and board.js only renders what it returns
// (docs/plans/board-inbox.md, slice 2).
export const enc = encodeURIComponent;  // every path segment from data goes through this
export const AGENT = /-(claude|codex|gemini|opencode|cursor|cline)$/;
export const B = { room: null, timer: null, members: [], membersAt: 0, since: {}, on: false,
                    card: {}, cardAt: 0, track: {}, trackAt: 0, brief: { at: {}, gen: 0 } };
export const token = () => sessionStorage.getItem("qt") || "";
export const me = () => sessionStorage.getItem("qn") || "";
export function trackOwner(name) {
  const matched = /^(.+?)-(claude|codex|gemini|cursor|opencode|cline)(-.*)?$/i.exec(name);
  return matched ? matched[1] : name;
}
// A participant JWT has three dot-separated parts; the shared secret doesn't.
// Approvals are never decided with the shared secret (team decision, 2026-10-17).
export const ownKey = () => token().split(".").length === 3;
// "@name" not followed by more name characters ("@arav" isn't "@arav-codex").
export const mention = (name) =>
  new RegExp("(^|[^\\w-])@" + name.replace(/[^\w-]/g, "") + "(?![\\w-])");
export function ago(sec) {
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return sec + "s";
  if (sec < 3600) return Math.floor(sec / 60) + "m";
  return Math.floor(sec / 3600) + "h " + Math.floor((sec % 3600) / 60) + "m";
}
export function clock(epoch) {
  return new Date(epoch * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
const ts = (m) => Date.parse(m.timestamp || "") / 1000 || 0;
const STALE_S = 600;  // a busy step older than this is a stuck daemon, not work
// When this wake started: the relay's last_wake_at (epoch seconds from the file
// backend, ISO text from Redis), else when this page first saw the agent busy.
function busySince(p, name, now) {
  const w = p.last_wake_at;
  const t = typeof w === "number" ? w : Date.parse(w || "") / 1000;
  return t > 0 ? t : (B.since[name] || now);
}
// Shared with inbox.js: the daemon's publish line, a verdict, a block, a question to a person.
export const PUBLISHED =
  /\(quorus\) published (\d+) commit\(s\) from quorus\/([\w-]+) to ([\w./-]+)/;
export const OK = /✅|\bapproved?\b|\bLGTM\b|lifting my block/i;
export const BLOCK = /\/disagree|\bblocking\b|\brejected\b|\bnot approved\b/i;
export const ASK_RE =
  /\?|please (approve|confirm|decide)|need (your )?(approval|ok|decision)|i'?m blocked/i;

// One agent's row, from presence + history + approvals. Pure: easy to reason about.
export function rowState(name, presence, history, approvals, now) {
  const p = presence[name] || { online: false, status: "offline" };
  const mine = history.filter((m) => m.from_name === name);
  const r = { name, vendor: (name.match(AGENT) || ["", ""])[1], rank: 5, line: "", detail: "",
              model: String(p.model || ""),  // tells agents apart without vendor logos
              asks: [], approvals: approvals.filter((a) => a.agent === name),
              // Mission control: the last few live steps, oldest first (sanitised
              // `activity` history from /presence?trail=1, never raw output).
              trail: p.online && Array.isArray(p.trail) ? p.trail.slice(-5) : [] };
  const myName = me();
  const ask = mine.slice().reverse().find((m) => myName && m.message_type !== "status"
    && mention(myName).test(m.content || "")
    && ASK_RE.test(m.content || ""));
  const answered = ask && history.some((m) => m.from_name === myName && ts(m) > ts(ask));
  if (ask && !answered) r.asks.push((ask.content || "").split("\n")[0].slice(0, 160));
  if (!p.online) { r.rank = 6; r.line = "offline"; }
  else if (p.status === "busy" && now - busySince(p, name, now) > STALE_S) {
    // A daemon stuck in a wake kept "running tests · 7h 26m" on the board for
    // hours (Arav, 2026-10-18). A step clears after 10 minutes: idle, no trail.
    r.rank = 5; r.line = "idle"; r.trail = [];
  } else if (p.status === "busy") {
    r.rank = 4;
    r.line = (p.activity || "working") + " · " + ago(now - busySince(p, name, now));
  } else if (p.waiting_on === "stuck") {
    r.rank = 2;
    const last = mine.slice().reverse().find((m) => /^⚠/.test(m.content || ""));
    r.line = "stuck" + (last ? " since " + clock(ts(last)) : "");
    if (last) r.detail = (last.content || "").split("\n")[0].slice(0, 160);
  } else if (p.waiting_on === "quota") {
    // limit_line (quorus/limit_display.py): the vendor's reset with its source,
    // or our retry marked "?", never our padded retry passed off as the reset.
    r.rank = 3;
    r.line = p.limit_line
      || "out of quota" + (p.waiting_until ? " · back about " + clock(p.waiting_until) : "");
  } else if (p.waiting_on === "pacing") {
    r.rank = 3;
    r.line = "waiting for its plan slot"
      + (p.waiting_until ? " · next turn in " + ago(p.waiting_until - now) : "");
  } else if (/^paused by Quorus/.test(p.limit_line || "")) {
    r.rank = 3; r.line = p.limit_line;  // a free-model cooldown: ours, not quota
  } else { r.rank = 5; r.line = "idle" + (p.limit_line ? " · " + p.limit_line : ""); }
  if (p.online && p.limit_hint) r.line += " → " + p.limit_hint;  // a suggestion only
  if (r.approvals.length || r.asks.length) r.rank = 1;
  const pub = mine.slice().reverse().find((m) => PUBLISHED.test(m.content || ""));
  if (pub) {
    const [, n, , to] = (pub.content || "").match(PUBLISHED);
    r.shipped = "shipped " + n + " commit(s) to " + to + " · " + ago(now - ts(pub)) + " ago";
    const verdict = history.slice().reverse().find((m) => m.from_name !== name
      && ts(m) > ts(pub) && (m.content || "").includes("@" + name)
      && (OK.test(m.content || "") || BLOCK.test(m.content || "")));
    if (verdict) {
      r.review = (BLOCK.test(verdict.content) ? "blocked by @" : "approved by @")
        + verdict.from_name;
      r.reviewBad = BLOCK.test(verdict.content);
    }
  }
  // The newest self-tuning line about this agent (quorus/runtime/track_record.py
  // posts "(quorus) t1 (shadow, not applied): would set @open bids x0.75 for @x ·
  // 2 errors, 0 clean/24h · undo: quorus tune undo t1", or "(quorus) adapted t1: …").
  // One row line per decision, with its undo: the same affordance every slice shares.
  // Only a `status` message the agent posted itself counts (its daemon always posts
  // them that way), so no room member can put a fake adaptation on someone's row.
  // Ids are "t1" or, since 0.6.4, "<owner>.t1" (two owners' agents both said "t1").
  const TUNE = /^\(quorus\) (adapted )?(?:[\w-]+\.)?t\d+\b/,
    UNDO = / undo: quorus tune undo (?:[\w-]+\.)?t\d+/;
  const tune = mine.slice().reverse().find((m) => m.message_type === "status"
    && TUNE.test(m.content || "") && UNDO.test(m.content) && mention(name).test(m.content));
  if (tune) {
    const text = tune.content,
      id = (text.match(/^\(quorus\) (?:adapted )?((?:[\w-]+\.)?t\d+)\b/) || [])[1] || "";
    const shadow = /shadow, not applied/.test(text);
    const what = text.slice(text.indexOf(": ") + 2, text.indexOf(" · undo:"))
      .replace(/^would set /, "");
    r.tuning = { id, shadow, undo: "quorus tune undo " + id,
                 text: (shadow ? "shadow, not applied: would set " : "adapted: ") + what };
  }
  const c = B.card[name];  // the outcome ledger for this agent's work here
  if (c && c.survival !== null && c.survival !== undefined) r.keptPct = c.survival;
  if (c && c.commits) {
    r.commits = c.commits;
    r.card = c.commits + " commit(s) · "
      + (c.survival === null || c.survival === undefined ? ""
         : Math.round(100 * c.survival) + "% kept · ")
      + c.approved + " approved" + (c.flagged ? " · " + c.flagged + " flagged" : "")
      + (c.reverted ? " · " + c.reverted + " reverted" : "");
  }
  return r;
}

// The room's catch-me-up for people, read with the viewer's own token (it shows only
// what they may read); room text goes in as textContent, never HTML. At most one
// request per room every 30 s, mode toggles included, and a response lands only if
// it is still for the room, credential and request that asked (Codex on c551a749).
export function briefTick(B, now, get, show) {
  if (B.mode !== "brief" || !B.room) return null;
  if (now - (B.brief.at[B.room] || 0) < 30) return null;  // per room: A→B→A included
  const asked = { room: B.room, tok: token(), gen: ++B.brief.gen };
  B.brief.at[B.room] = now;
  const live = () => asked.gen === B.brief.gen && asked.room === B.room
    && asked.tok === token();
  return get("/rooms/" + enc(asked.room) + "/brief?format=text").then(
    (r) => { if (live()) show((r && r.text) || ""); },
    (e) => { if (live()) show("No brief: " + e.message); });
}
