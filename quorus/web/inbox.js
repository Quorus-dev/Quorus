// The "needs you" inbox (docs/plans/board-inbox.md, slice 3): everything in a room that
// waits on the viewer, as data. Pure: room history and the pending approvals go in,
// ordered items come out; no DOM and no fetch, so tests/test_board_inbox.py runs this
// file under node as-is. The view (slice 4) only renders what this returns.
import { AGENT, ASK_RE, BLOCK, OK, PUBLISHED, mention, trackOwner } from "./rows.js";

// Approvals first (they stall a running agent), then blocks on the viewer's agents,
// review requests to the viewer, holds, questions; within a kind, oldest first.
export const KINDS = ["approval", "block", "review", "hold", "question"];
export const HISTORY_LIMIT = 300;  // what the board asks for; a full window hides older items
const SHA = /\b[0-9a-f]{7,40}\b/;
const REVIEW = /\breview/i;
// The daemon's publish guard (scripts/reflexd.py): work held back, not published.
const HOLD = /kept unpublished|publication held/;
const ts = (m) => Date.parse(m.timestamp || "") / 1000 || 0;
const firstLine = (s) => String(s || "").split("\n")[0].slice(0, 160);
const names = (text) => [...String(text || "").matchAll(/(^|[^\w-])@([\w-]+)/g)].map((m) => m[2]);

// The parsed verb of a `social` message (routes/social.py persists the envelope as the
// message content), or null for anything else.
export function envelope(m) {
  if (!m || m.message_type !== "social") return null;
  try {
    const e = JSON.parse(m.content);
    return e && e.kind === "social" && typeof e.verb === "string" ? e : null;
  } catch {
    return null;
  }
}

// services/social_svc.py `_has_majority`: one option strictly over half the weight, and
// when the room's size is known and above one, at least ceil(N/2) distinct voters.
export function hasMajority(tally, voters, members) {
  const weights = Object.values(tally);
  const total = weights.reduce((a, b) => a + b, 0);
  if (total <= 0 || Math.max(...weights) <= total * 0.5) return false;
  if (!members || members <= 1) return true;
  return voters.size >= Math.floor((members + 1) / 2);
}

// Wire blocks, replayed the way the relay keeps them (services/social_svc.py): one active
// block per room. A `disagree` with payload.mode "blocking" makes its payload.ref_message_id
// the active block (a newer one supersedes the older, which the relay forgets); a later
// `release` carrying handoff_to clears it (any actor, social_svc.py:276-300); a majority in
// the active block's poll `vote_<ref>` clears it (social_svc.py:420-433). Poll tallies and
// voters persist across events, as the relay's state does, so a re-blocked message keeps
// its earlier votes. A vote lands in its explicit poll_id, else the active block's poll,
// else `vote_<its ref>`; a second vote by the same actor is refused. A plain release, a
// defer, an advisory or prose never changes any of this.
export function wireBlocks(history, members) {
  const polls = new Map();  // poll_id -> { tally: {option: weight}, voters: Set }
  const poll = (id) => {
    if (!polls.has(id)) polls.set(id, { tally: {}, voters: new Set() });
    return polls.get(id);
  };
  let active = null;  // { msg, env, ref, poll, closedBy? }
  const history_ = [];
  const landed = new Map();  // vote message id -> the poll the relay put it in
  history.forEach((m) => {
    const e = envelope(m);
    if (!e) return;
    const p = e.payload || {};
    if (e.verb === "disagree" && p.mode === "blocking") {
      const ref = String(p.ref_message_id || e.ref_message_id || "");
      if (active) history_.push({ ...active, closedBy: "superseded" });
      active = { msg: m, env: e, ref, poll: "vote_" + ref };
      poll(active.poll);
    } else if (e.verb === "release" && p.handoff_to) {
      if (active) history_.push({ ...active, closedBy: "handoff" });
      active = null;
    } else if (e.verb === "vote") {
      const id = p.poll_id || (active ? active.poll
        : (e.ref_message_id ? "vote_" + e.ref_message_id : ""));
      if (!id) return;
      landed.set(String(m.id || ""), id);
      const tally = poll(id);
      if (tally.voters.has(e.actor)) return;
      tally.voters.add(e.actor);
      const option = String(p.option || "");
      tally.tally[option] = (tally.tally[option] || 0) + (Number(p.weight) || 1);
      if (active && id === active.poll && hasMajority(tally.tally, tally.voters, members)) {
        history_.push({ ...active, closedBy: "vote" });
        active = null;
      }
    }
  });
  return { active, closed: history_, polls, landed };
}

function isMine(name, me) {
  return !!me && !!name && (name === me || trackOwner(name) === me);
}

export function inboxItems(history, approvals, me, now, members) {
  history = Array.isArray(history) ? history : [];
  approvals = Array.isArray(approvals) ? approvals : [];
  const byId = new Map(history.filter((m) => m && m.id).map((m) => [String(m.id), m]));
  const items = [];
  const item = (kind, m, extra) => items.push({
    kind, who: m.from_name || "", what: firstLine(m.content), ref: m.id ? String(m.id) : "",
    sha: (String(m.content || "").match(SHA) || [""])[0], at: ts(m),
    age: Math.max(0, now - ts(m)), ...extra });

  approvals.forEach((a) => {
    const at = Date.parse(a.created_at || "") / 1000 || now;
    items.push({ kind: "approval", who: a.agent || "", ref: String(a.id || ""), sha: "",
                 what: "wants to run " + (a.tool_name || "a tool") + ": "
                   + String(a.input_preview || "").slice(0, 120),
                 at, age: Math.max(0, now - at), actions: ["approve", "deny"] });
  });

  const wire = wireBlocks(history, members);
  const block = wire.active;
  if (block) {
    const b = block;
    const target = byId.get(b.ref);
    const reason = b.env.payload && b.env.payload.reason;
    const onMine = target ? isMine(target.from_name, me)
      : names(reason).some((n) => isMine(n, me));
    if (onMine) {
      item("block", b.msg, { who: b.env.actor || b.msg.from_name || "", what: firstLine(reason),
                             sha: (String(reason || "").match(SHA) || [""])[0],
                             prose: false, poll: b.poll,
                             on: target ? target.from_name : "", actions: ["vote", "reply"] });
    }
  }

  history.forEach((m, i) => {
    if (!m || envelope(m) || !m.content) return;
    const from = m.from_name || "";
    const later = history.slice(i + 1);
    const text = String(m.content);
    if (from !== me && mention(me || "\u0000").test(text) && REVIEW.test(text) && SHA.test(text)) {
      // A review request: closed by the viewer's own verdict, in prose on the sha or the
      // asker, or on the wire: a `vote` that landed in this request's poll (resolved the
      // relay's way in wireBlocks: explicit poll_id first, else the active block's poll,
      // else vote_<ref>; Codex on fc4ed6ee) or a `disagree` that references it.
      const sha = text.match(SHA)[0];
      const id = String(m.id || "");
      const verdict = later.some((x) => {
        if (x.from_name !== me) return false;
        const e = envelope(x);
        if (e) {
          const p = e.payload || {};
          return (e.verb === "vote" && wire.landed.get(String(x.id || "")) === "vote_" + id)
            || (e.verb === "disagree" && (p.ref_message_id || e.ref_message_id) === id);
        }
        return (OK.test(x.content || "") || BLOCK.test(x.content || ""))
          && (String(x.content).includes(sha) || mention(from).test(x.content || ""));
      });
      if (!verdict) item("review", m, { actions: ["vote", "disagree", "reply"] });
      return;
    }
    if (!isMine(from, me) && BLOCK.test(text) && m.message_type !== "status") {
      // A block in prose (no verb): on one of the viewer's agents, lifted only by the same
      // author's later OK that names that agent. It never clears a wire block.
      const target = names(text).find((n) => n !== me && isMine(n, me));
      if (!target) return;
      const lifted = later.some((x) => x.from_name === from && OK.test(x.content || "")
        && mention(target).test(x.content || ""));
      if (!lifted) item("block", m, { prose: true, on: target, poll: "", actions: ["reply"] });
      return;
    }
    if (isMine(from, me) && from !== me && HOLD.test(text)) {
      // The daemon kept the agent's commits back; the next publish line from it ends it.
      const published = later.some((x) => x.from_name === from && PUBLISHED.test(x.content || ""));
      if (!published) item("hold", m, { actions: ["reply"] });
      return;
    }
    if (AGENT.test(from) && me && m.message_type !== "status" && mention(me).test(text)
        && ASK_RE.test(text)) {
      // A question to the viewer, answered by anything the viewer posts after it
      // (today's board rule, rows.js rowState).
      if (!later.some((x) => x.from_name === me)) item("question", m, { actions: ["reply"] });
    }
  });

  return items.sort((a, b) => KINDS.indexOf(a.kind) - KINDS.indexOf(b.kind) || a.at - b.at);
}

export const historyWindowFull = (history, limit = HISTORY_LIMIT) =>
  Array.isArray(history) && history.length >= limit;
