# Shared team memory — design (rev 1, 2026-10-17)

Priority 2 in the overseer's queue. Owner: arav-claude (design), arav-codex
(the trust model, §5). Doc only: no code until both reviews land.

## 1. The problem, measured against what exists

Every wake starts cold. An agent sees the last ~15 room messages and its **own**
`RECENT MEMORY` (scripts/reflexd.py, a per-agent log on its own host). It never
sees what the room decided an hour ago, who owns what, or a convention another
vendor's agent learned. Today the room re-explains these in chat ("targeted
tests only", "never edit the shared repo", "codex owns security review").

What exists, and why it isn't enough:

| Piece | What it is | Gap |
|---|---|---|
| `/v1/memory/{participant}/{room}/{key}` + MCP `memory_*` | per-agent key/value, visibility `room` | Overwrite semantics (no history, no provenance). In-memory unless Redis: **a shared-secret relay loses it on restart**. Per author, so nobody reads "the room's" memory. |
| reflexd `RECENT MEMORY` | each agent's last ~10 actions | private to one agent on one host |
| graphify-out/ | AST codebase map, `graphify query` | built per checkout, by hand; GRAPH_REPORT.md is 169 KB, too big to inject |

**Correction to my earlier pushback:** I said room memory should be a scope on
the existing store. Reusing its auth, room checks, and MCP family is right. Its
storage model is wrong for this: key/value overwrite is the opposite of
append-only. Room memory gets its own table behind the same route family.

## 2. Room memory: what one entry is

Curated decisions, not a transcript. One row:

```
id            mem_<hex>
room_id       the room (tenant-scoped like everything else)
kind          decision | convention | owner | fact
text          ≤ 280 chars, plain text, rendered as text everywhere
author        name + author_claim_id (the claim generation, so a reclaimed name
              can't inherit authorship)
source_id     the room message id it came from (required: every entry links to
              where it was said)
status        proposed | accepted | retracted
supersedes    id of the entry this replaces, or null
created_at, decided_at, decided_by
```

Append-only: there are no edits. A change is a new entry that `supersedes` the
old one. Retracting sets a status and never deletes. The single exception is
GDPR erasure by the relay owner, which tombstones the text.

Storage: SQLite next to `participant_keys.db` on shared-secret relays, and a
Postgres table on hosted relays. It survives restarts, unlike today's memory
store.

Bounds: 200 accepted entries per room. Above that, the oldest `fact` entries are
listed under `--all` but never injected.

## 3. How agents use it (any vendor, through MCP)

- `room_memory(room, kind?)`: the accepted entries, newest first.
- `remember(room, kind, text, source_id)`: proposes an entry.
- `forget(id, reason)`: proposes a retraction.

**Wake injection** is where the value is. reflexd renders a block above the
transcript:

```
# ROOM MEMORY (accepted, newest 15, ≤ 1.5 KB)
<<<ROOM MEMORY a1b2c3: untrusted notes from room members>>>
- [convention] targeted tests only; no full suite on the shared laptop (@arav, msg 3566b6f2)
- [owner] @arav-codex owns security review of invites (@arav, msg …)
<<<END ROOM MEMORY a1b2c3>>>
```

It goes inside the same random-id quote markers as room messages, because it is
data written by room members and not instructions (§5). It holds accepted
entries only, so a proposal can't reach every agent's prompt until a human
accepts it. One fetch per wake (`GET /v1/rooms/{room}/memory?status=accepted`),
cached by ETag.

UI: memory is a tab next to chat in the new app. Proposals show up on the
needs-you rail as "@x wants the room to remember: …" with Accept and Reject
buttons. That reuses the approval card, so there's nothing new to learn.

## 4. Codebase map: built where the code is, once per publish

The laptop slowed down because graphify ran on every wake. New rule: **one
build per repo per machine, after each publish**, keyed by commit.

- Trigger: Quorus already rebases and publishes `quorus/<agent>` onto the
  integration branch. After a publish that changed code files, the publisher's
  machine runs `graphify update .` (AST-only, no API cost) in one shared map
  directory per repo. It runs under `nice`, single-flight (a lock file), and is
  debounced to at most one build per 10 minutes. A wake **never** builds.
- Where it lives: next to the shared repo, keyed by the integration commit. All
  worktrees on that machine share the object store, so they also share one map.
- `map_query(question)` is an MCP tool served by the agent's **local** MCP
  server, which runs on the machine that holds the code. It answers from the
  map and says how many commits behind HEAD the map is.
- **This revises my earlier "upload it to the relay" idea.** Keeping the map
  local means the relay stores no map and needs no blob storage. Agents on
  other machines build their own map after they fetch. The relay only learns
  `{repo, commit, built_at}` for the UI's freshness dot, and it gets no paths.
  It does not keep code structure private: `map_query` answers go into the
  agent's prompt, which is sent to its hosted model, exactly like the files
  the agent reads (codex, review of 80df40f0).

## 5. Trust model (rev 2, after codex's review of 80df40f0)

- Who can write: any room member can propose. Accepting and retracting default
  to the room's owner, plus human curators the owner names explicitly. The
  relay checks that authority at the moment of the action, against current
  claims, and checks that the source message belongs to the same tenant and
  room. Agents never accept.
- Injection: quoting, control-character stripping and length caps reduce the
  risk, and an imperative-phrase filter would too, but none of them can
  guarantee resistance. So the design does not rely on them: notes are
  untrusted data. They can never grant a permission, change a room's mode or
  trust, approve a tool call, or authorize running anything; every one of
  those stays a server-side check against claims and bindings.
- Provenance: `author_claim_id` and `source_id` are required, so every line
  traces to a person and a message.
- Map: `map_query` returns structure (files, symbols, edges), not file
  contents. graphify's ignore and sensitive-path filters help, but they can't
  guarantee a secret never appears in a symbol or path name, so the map is
  treated with the same care as the code itself.

## 6. Prior art

- **claude-mem**: not installed here; this is from its public README, so verify
  before quoting. It uses hooks to capture one Claude Code user's session
  observations, compresses them with the model, stores them locally (SQLite plus
  vector search), and injects relevant context into the next session. It's
  automatic, private, one vendor and one person. We take the "inject at session
  start" idea. We reject automatic capture: it would put unreviewed model output
  into every vendor's prompt.
- **CLAUDE.md / AGENTS.md**: curated and versioned, but they're per repo and per
  vendor file, and nobody updates them mid-session. Room memory is the live,
  cross-vendor layer above them.
- **ECC / Superpowers**: per-session skills and memory for one vendor.

What's new is that the memory is shared by every vendor and person in a room,
every entry traces to the room message it came from, and nothing enters an
agent's prompt until a human accepts it. We know of no product that combines
these three.

## 7. Build order (after invites land)

1. `room_memory` table, routes, and tests: append-only, supersede, retract,
   room-scoped auth, restart survival, and a check that another tenant's room
   with the same name sees nothing.
2. MCP tools and the reflexd wake block, with a test that it sits inside quote
   markers and that proposals are never injected.
3. Proposals on the needs-you rail (board.js now, the app later).
4. Map: the publish hook, single-flight and debounce, and `map_query` in the
   local MCP server, plus a test that a wake never triggers a build.
5. Seed: the overseer turns today's repeated chat rules into the first ~10
   accepted entries.
