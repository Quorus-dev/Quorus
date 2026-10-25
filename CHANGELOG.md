# Changelog

All notable changes to Quorus are documented here. This project follows
[Semantic Versioning](https://semver.org/) and [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

## [0.7.5] — 2026-10-25

### Security

Two places printed your relay's secret. Both were in every release from 0.5.0 through 0.7.4.

- `invite <name>` in the terminal room (`quorus`). It printed a `quorus_join_…` token and
  told you to give it to that person. The token contained the relay's secret,
  base64-encoded, not hidden, so anyone who received one could decode it and act as your
  relay's operator: post as anyone, approve requests, mint invites, remove members and
  delete rooms. 0.7.5 removes it: `invite <name>` now tells you to use `/share`, which
  makes a join code that redeems into the invitee's own key, the way `quorus share` has
  worked since 0.6.0. Join codes from `/share` and `quorus share` never carried the secret
  and are not affected.
- `quorus quickstart`. Its "Quickstart ready" summary printed `Secret: <the secret>` for
  the demo relay it started, so a screenshot, recording or pasted log of that terminal
  carried it. 0.7.5 prints where the secret is saved (`relay_secret` in the config file)
  instead.

If you gave someone a token from `invite <name>`, or shared quickstart output from a relay
you still use, change that relay's secret and restart the relay; the old secret keeps
working until you do. For the local relay `quorus` starts, that is `relay_secret` in
`~/.quorus/config.json`; for a relay you run yourself, its `RELAY_SECRET`. Participant keys
are signed separately, so your teammates and agents stay signed in.

## [0.7.4] — 2026-10-24

### Added

- The room's status row (the line above the composer) now names one of eight states, in
  this order of precedence: not connected to the relay; no agents here yet; the agent isn't
  running; working (with the busy agents named); free tier limited; ready; waiting for a
  response; answered, when the row disappears. An agent counts as ready only when the
  relay's presence says it is online, not because it is a member. Notices, status posts,
  plans and `NO_REPLY` are never an answer, and an unanswered message from you brings
  "waiting" back. While presence is unknown, before the first poll or after a failed one,
  the row only ever says "disconnected" or "no agents": a failed poll drops the old rows,
  so a stale "ready" is never shown as live.
- A free-model agent whose free tier is limited now says so. When opencode's free tier
  refuses (a typed 429, shared across all of its free models), the agent posts one notice to
  the room: the limit is per connection or account, no free model can answer right now,
  message it again after the pause, and the exact commands that would get an answer now on
  this computer. The daemon pauses until the vendor's reset and shows `free tier limited for
  this connection or account` in `/status`, as its own pause reason, not as your quota. The
  room's status row reads "free tier limited" until a real answer arrives.
- `quorus demo` and the agent's notice share one "next command" detector. It lists every
  prerequisite in order: `quorus` once if nothing is configured, the harness CLI if it isn't
  on PATH, then the first path that works here: a running Ollama (and `ollama pull` if the
  model is missing), a signed-in Claude Code or Codex, or Gemini with a key; otherwise how to
  install Ollama. A free model is never offered as the way out of a free-tier limit.

### Changed

- `quorus demo` stops probing as soon as opencode reports the free-tier limit, instead of
  trying every free model (they share the limit) and marking each one "slow" for an hour. A
  probe that times out with the limit already in its output is read as a limit, not a slow
  model. The same rule applies to an agent rotating through free models: a limit ends the
  rotation, keeps the vendor's reset time, and restores the model it started on.
- `quorus doctor` on a fresh install no longer promises "no keys needed": it says the demo
  runs on a free model, and that if the free models are busy it asks before using your
  Claude Code or Codex login, or tells you to retry later.
- First-screen copy: a one-line wordmark instead of block letters; the ready line says which
  room you are in ("You're @you in #general. Type a message and press Enter."); tools that
  are installed but not signed in appear as one muted "Also on this computer" line after the
  result instead of a warning each. The relay's log path and the launchd label are no longer
  printed by default (`quorus agent list` still shows the label).
- Each room message is one block: a code fence no longer splits a reply into several
  messages, so there is one sender header and one timestamp per message. Sent messages no
  longer show a "Delivered" row; only "Sending…" still appears, while a message is on its way.
- Colour tokens that read on light terminals: names and text stay on the terminal's default
  foreground; hue is used only on marks: ✓ ✗ ! glyphs, sender dots, rules and borders.

### Fixed

- The 0.7.3 known limit: a first run on opencode's free tier that hit the rate limit used to
  show only "Agent joined; waiting for a response". It now shows the notice and the "free
  tier limited" row described above.

## [0.7.3] — 2026-10-24

### Added

- `/agents` in a room opens agent setup at any time. The first run still offers the agent
  tools it finds; if you decline or cancel, `/agents` brings the offer back.
- A status line above the room says where setup stands: "No agents connected", "Agent
  joined; waiting for a response", or nothing once an agent has really answered. Errors and
  notices from an agent don't count as an answer.
- `quorus demo --use-my-login` lets a script allow the signed-in fallback.

### Changed

- `quorus demo` asks before falling back to your signed-in Claude Code or Codex when
  opencode's free models are busy, and says that the fallback uses your login and your
  account's usage. Without that consent (or `--use-my-login`) nothing starts, and the demo
  exits 1. When opencode itself isn't usable, it says so instead of "free models are busy".
- The first-run agent offer says that signed-in agents use your CLI login and your
  account's usage, and that free-model agents depend on the vendor's availability.
- If agent setup fails, the room shows the error and stays usable.

### Known limits

- opencode's free tier can rate-limit. A first run with no signed-in Claude Code or Codex
  may then show only "Agent joined; waiting for a response": the agent replies with the
  vendor's usage-limit notice, which isn't counted as an answer. Retry later or add a
  signed-in agent with `/agents`.

## [0.7.2] — 2026-10-24

### Fixed

- A new user can add agents again. On a fresh install, `quorus agent add` (and the first-run
  offer to add opencode) failed with "only an invited person's own key can add agents": the
  profile points at `http://localhost:8080`, but the relay `quorus` starts bound 127.0.0.1
  only, so the owner's own relay read as a teammate's. That relay now binds both 127.0.0.1
  and ::1. Where it can only bind 127.0.0.1 (IPv6 off, or another program holds ::1 on that
  port), the wizard saves `http://127.0.0.1:8080` and `agent add` points your agents there.
  `localhost` is still trusted only when the relay holds both loopbacks, so your relay
  secret never goes to another program listening on ::1.
  Upgrading: a relay that 0.7.1 started keeps its old 127.0.0.1-only bind until it
  restarts. `agent add` works with it anyway, using 127.0.0.1. To move it to the new bind,
  run `quorus relay --replace` once: it stops the background relay and starts the new one
  in that terminal.
- `quorus demo` exits 1 when no model can run (opencode's free models busy and no signed-in
  Claude Code or Codex), instead of 0.
- A relay without a database answers 401 on `/v1/auth/token` instead of logging a 500 with a
  traceback on every token exchange.

## [0.7.1] — 2026-10-22

### Fixed

- `quorus demo` now ends on the reviewer's verdict. The reviewer's turn is read-only, but
  its instructions also told it to run the tests and commit, so a free model kept trying
  commands that were denied until the demo's wait ran out. A read-only turn now gets one
  instruction: review by reading, then post the verdict. This applies to every read-only
  turn, not just the demo. Nothing about who may edit has changed.

## [0.7.0] — 2026-10-22

### Added

- Limit status: each agent shows what Quorus knows about its vendor limit, and where it
  came from. For Claude Code, that's the usage percentage it reports for its 5-hour and
  7-day windows, with reset times. For other vendors, it's the reset time from the
  vendor's own error message when there is one, a labelled guess otherwise, and "no limit
  data" when nothing is known. A cooldown Quorus applies itself reads "paused by Quorus",
  never "out of quota". It's on the board and in a new LIMITS column in `quorus ps`.
  When a teammate on another vendor has room, you see a suggestion to give them the next
  task. The suggestion never moves work by itself.
- Never Stuck, off by default: when an agent hits its vendor limit mid-task, a teammate on
  another vendor can pick the task up from the exact commit. Before it continues, it
  reruns the failing check you configured and posts a one-line receipt saying whether the
  failure reproduced. Anything it can't verify stays held for you. Turn it on per room with
  `quorus room bind <room> <repo> --check "<test command>" --handoff on`. `quorus handoff
  holds` lists held work and `quorus handoff release` publishes or discards it.
  If the teammate is mid-turn, the agent waits for it (15 minutes by default,
  `--handoff-wait-minutes` from 1 to 60) and tells you it's waiting. Never Stuck needs a
  0.7.0 relay. On an older relay, nothing moves and you get a "relay too old for handoff"
  notice.
  A relay without Redis keeps task claims in memory: if it restarts during a handoff, the
  claim is lost, nothing moves, and the work stays held for you to resolve.
- Track record: `quorus track` and the board show, per agent, model and kind of task, how
  that agent's tasks in this repo turned out (kept, reverted, failed, escalated, lost to
  quota), with a 95% range once there are at least 5. It's labelled "observed in this
  repo, not a ranking" and never assigns work.

### Changed

- Gemini CLI: setup now says it wakes with a Google AI Studio API key in `~/.gemini/.env`
  and that Quorus doesn't use your Google sign-in.
- `quorus agent add --state-dir <dir>` gives an agent its own state directory, apart
  from your `~/.quorus`.
- Agent daemons keep their state under `QUORUS_CONFIG_DIR` when it is set, so separate
  setups on one machine don't share bindings or state.

### Fixed

- `quorus demo` no longer stalls when its reviewer asks for an approval nobody in the demo
  room can give. The request is declined at once, and an approval notice is no longer
  shown as the review's verdict.
## [0.6.6] — 2026-10-21

Bug fixes on released paths, under the release freeze. Nothing new.

### Fixed

- The Claude Code, Cursor and Gemini hooks no longer show the same room messages again on
  every prompt. They used to fetch without acknowledging, so the relay re-sent everything
  after 60 seconds, and the repeat filter let almost every message through. Now a hook
  and `quorus inbox` acknowledge only the messages they actually displayed, and remember
  what they've shown.
- A hook running inside one of your agents no longer reads that agent's queue. The agent's
  daemon owns its messages, so the hook can't take or acknowledge them and the agent
  doesn't miss a wake.
- Running more than one agent no longer fills the daemon logs with
  `notifications-state.json.tmp: [Errno 2]`. Daemons that share `~/.quorus` used to write
  the same temporary file; each save now uses its own.

## [0.6.5] — 2026-10-20

Bug fixes on released paths, under the release freeze. Nothing new.

### Security

- Room posts now mask participant keys (`quorus_pk_…`), as they already masked `mct_` and
  `quorus_sk_` keys. The owner's own key sits in `~/.quorus`, which an agent running as
  the owner can read, so an agent tricked into posting it would have shown it unmasked.

### Fixed

- On the default local setup (Enter in the wizard), the human held only the relay secret.
  As a result, every social verb in the hub (`/vote`, `/claim`, `/disagree`, …) answered
  `403 legacy auth not permitted`.
  - The wizard now claims a normal participant key for you on your own relay, and the hub
    and CLI sign in with it.
  - The secret stays the operator credential for share, invites --revoke, kick, destroy
    and rename. A hosted login keeps its own credential and never gives way to a stale
    secret.
  - A name that's already claimed is never taken over: the wizard says how to recover.
    `quorus whoami` says which credential signs in.
  - Your own relay isn't mistaken for a teammate's. The relay records the loopback
    addresses it bound, so your agents keep your relay's secret and get no teammate note
    or invitee pin.
- `quorus relay` is a wrapper around `python -m quorus.relay_cli`.
  - Killing the wrapper used to leave the relay child serving, so the port stayed bound.
    The wrapper now forwards SIGTERM and SIGINT to the child and waits for it.
  - Starting a second relay on a port something else held used to die silently with
    exit 0. The wrapper now refuses a port that's already in use before forking, naming
    the holder when `lsof` knows it, or saying it's in use when it doesn't.
  - The wrapper now reports the child's exit code.
- A wake's prompt no longer claims that every agent shares one Git object store. Reviewers
  from another owner are told how to fetch the commit instead.
- When several agents bid on one `@open`, each loser posted a `/defer` line. Losing
  bidders now stay silent.
- A wake that crashed before committing left a held window behind. The retry wake for the
  same message was then held as if the person had written mid-turn. A stale window no
  longer holds a retry.
- `quorus scorecard`'s survival column used to blame only a commit's first 25 files, at
  whatever was checked out, while counting every file's additions. It now blames every file
  at one pinned end commit and follows renames. A deleted file's lines count as gone, a git
  error leaves a file unmeasured rather than deleted, and the share measured is shown.
- The config directory and room-bindings file are now found from `HOME` when they're
  used, not when the module is imported, through one resolver.

## [0.6.4] — 2026-10-20

Bug fixes on released paths, found by the 0.6.3 audit and by running several people's
agents in one room.

### Security

- An agent's own Git commits could carry the machine's identity: in a fresh HOME with no
  gitconfig, git fell back to the human's login and hostname, which leaked them to everyone
  the repo was shared with. Woken agents now commit as their agent (author and committer),
  on first turns and resumed sessions, overriding any inherited human identity.
- Participant-key SSE tokens and open room and DM streams no longer survive a release or reclaim of the
  recipient's name or of its owner's claim, and queued room events are checked against
  current membership before delivery. Clients holding a pre-upgrade stream token for a
  claimed name must get a fresh one.

### Added

- `quorus room add <room> <member…>` puts existing people or agents in a room. The relay
  still decides who may: the room's creator or an admin, or a member adding an agent they
  own. A refusal says why and exits 1.

### Fixed

- A kicked invited member could only come back with a new invite: re-adding them was refused
  with "the name belongs to an invited member". The room's creator or an admin can now put an
  invitee back in the room their own invite was for, at that invite's role, unless the invite
  was revoked. A revoke that races the re-add takes it back. Granting their name any other
  room, or posting as them, is still refused.
- `quorus agent add` said "already running" and started nothing when a daemon with the same
  agent name ran for another relay on the same machine (an old relay, or a second profile).
  It now counts only a daemon for this config's relay.
- Rebinding a room for a new agent (`quorus agent add --repo`) dropped the room's other
  settings, such as `share`. They are kept now.
- `quorus agent add --trust members` (or `--mode`) without `--repo` was silently ignored, so
  a teammate's requests stayed read-only. It now applies to the room's existing workspace, or
  exits saying to add `--repo`.
- An `@open` task could go to an agent that may only answer the requester read-only (another
  person's agent, in a room that trusts only owners), so the task stalled. The relay now
  ranks a read-only bid below every writable one, whatever its fairness credit, and gives it
  no credit for losing. When every bid is read-only, the winner posts one notice that no
  agent here can make changes for the requester, instead of taking a turn.
- npm's opencode 1.x (`opencode-ai` 1.19) refused the whole config Quorus sent, because it
  held 2.x `permissions`, so every opencode wake on 1.x exited 1 with no known cause. A 1.x
  agent now gets the 1.x config in every permission mode, and that refusal gets a named room
  message, shown only for opencode.
- A retried agent turn could post its `plan:` line twice (seen in `quorus demo`). A wake runs
  the turn again after a failed resume or on the next free model after a rate limit. The MCP
  `send_room_message` tool now skips a message the agent already posted in the room in the
  last 10 minutes, and a second plan line while its latest post there is an unanswered plan.
- Plan, progress and Quorus status lines no longer count toward the unaddressed-chatter
  limit, which could stop agents answering one person's question with no loop at all.
- `quorus demo`'s closing hint no longer offers `gemini` (Gemini CLI now needs a Standard
  or Enterprise license or a paid API key).
- `docs/api.md` says who may add room members and lists the join endpoint's errors.
- The invite one-liner (`curl … | bash`) never asked for a name, because stdin was the
  download, so it joined as `$USER` and stopped. It now asks on the terminal and opens the
  hub there; with no terminal it joins and prints the next command.
- An observer's welcome offered `say` and `agent add`, and `agent add` blamed their
  credentials. The welcome follows the invite's role, and `agent add` names the observer
  role and points to a member invite.
- `quorus history` printed social verbs as raw JSON, and a message containing a
  closing-tag-like bracket (`see [/tmp]`) crashed `history` and `search`.
- `quorus --help` now lists `web`, `invites`, `release-name`, `members` and `whoami`.
- In the hub, a fully typed slash command plus Enter only autocompleted and needed a second
  Enter; it now runs. `/workspace` with a name the profile store refuses no longer closes the
  hub with a traceback, `/deny` with no id shows its own usage, and `/queue` takes a summary
  of more than one word.
- `quorus scorecard`'s `tests` column is now `+tests`, with a footnote: it counts commits
  that add or change a test file, not whether tests ran.
- An agent that deliberated in its reply ("NO_REPLY? No — …") posted the protocol token
  into the room. The daemon strips the token and the deliberation, keeping a meaningful
  "No …", and posts nothing when nothing is left.
- Self-tuning notes ("(quorus) t1 (shadow, not applied): would set @open bids …") were
  posted into rooms shared with other owners' people, and two owners' agents both said
  "t1". The note now goes to the owner as a DM (shown in the hub) and never to a room,
  since a member list can't prove a room is private; `quorus tune list` always has it,
  and the web board's tune row, which read those room lines, is gone with them. Ids name
  the owner (`arav.t1`), so `quorus tune undo arav.t1` is unambiguous.

## [0.6.3] — 2026-10-20

Fixes the founding use case: two people, on two computers, each bringing their own agents into
one room. The release gate now runs it with two HOMEs talking to a relay over a LAN address.

### Fixed

- An invited teammate could join and chat but couldn't add their own agents: a shared-secret
  relay refused to create an agent identity from a member's key. Members can now add agents
  named `<member>-<tool>`. They act only in rooms their owner is in, never above the owner's
  role, and are removed when the owner leaves or is revoked. On a teammate's relay, an agent
  takes write-capable turns only from its own owner: the relay stamps each message with how
  its sender signed in, and the agent checks that stamp on the request and on every agent hop
  before it. The relay's operator writes those stamps, so you're trusting them to report
  honestly who sent each message. `quorus agent add --mode autonomous` on someone else's relay
  says so first and, at a terminal, asks before giving the agent write access (default No).
- `quorus relay --host 0.0.0.0 --replace`, the step `quorus share` gives for a teammate on
  another computer, could lose the relay's rooms: the new relay loaded the state file before
  the old one finished saving it. It now waits for the old relay to exit.
- Joining from another computer used the address stored in the invite, which is
  `127.0.0.1` when the owner shared from their own machine, so the join couldn't reach the
  relay. The wizard and `quorus join --relay` now keep the address that served the code.
- The relay leaked a database connection, and its file descriptor, on every request that
  touched participant keys or invites (joins, invites, member checks). A relay serving a
  shared room for long enough could run out of file descriptors. Connections are now closed
  after each use.

## [0.6.2] — 2026-10-19

Fixes only, from a full audit of 0.6.1 installed from PyPI.

### Fixed

- A reviewer could approve a commit without seeing it. When an agent was asked to review
  a commit on a read-only turn (the demo's reviewer, or anyone who isn't the agent's
  owner), it had no shell, so it reviewed the author's description instead of the diff.
  Quorus now runs `git show` itself for each commit hash named in the request (up to 3,
  with git's external diff, textconv, pager, fsmonitor, signing, lazy fetching, replace
  refs, grafts and `GIT_*` overrides off, no transport allowed (custom helpers included), a size cap enforced
  while reading and a time limit) and puts the output in the reviewer's prompt, marked as
  untrusted.
  The reviewer's own permissions don't change.
- `QUORUS_NOTIFY=0` didn't turn off every desktop banner, as the README says: an agent's
  @-mention banner, the hub's and the watcher's ignored it. They now all check the same
  switch, and `quorus demo` no longer shows banners.

### Changed

- The Claude Code plugin and the README link the privacy policy
  (https://www.quorus.dev/privacy). The plugin pins `quorus-ai==0.6.2`.

## [0.6.1] — 2026-10-19

Fixes only, from stranger tests of 0.6.0.

### Fixed

- Agents set up by the first-run wizard could answer and review but never change a file:
  the wizard bound them without permission to edit, so a stranger's "fix this" bounced
  between agents and nothing landed (headless Codex and opencode can't edit in `default`
  mode). The wizard now names the repo and asks once `[y/N]` whether agents may edit
  files and run commands in it when you ask, with your account's permissions: y binds
  the room `autonomous`, Enter keeps each tool's own permissions. Anyone else in the room
  still gets a read-only turn. An agent that can't write says so once, with the command
  that allows it, instead of handing the task to another agent. The README says the
  same.
- An unconfigured Claude Code plugin said "Connection closed". It now starts and says what
  to set and where: `/plugin` → Quorus → Configure, or `quorus init`.
- Invites:
  - The wizard and the hub asked for a quorus:// link or a quorus_join_ token, which
    0.6.0 never makes. They now ask for the ABCD-EFGH code that `quorus share` prints
    (old links still work).
  - The wizard took any text as a relay address. It now asks again for a real
    `http(s)://host:port`, never echoes one with credentials in it, and points an
    invitee whose join fails to `quorus join <code> --relay <address>`.
  - A member or observer running `quorus share` was told to check RELAY_SECRET. They're
    now told only the relay's owner can invite people, with exit 3.
  - A key released with `quorus release-name` got "retry, relay is up". It now says the
    key no longer works and to ask for a new invite.
  - `quorus release-name` says the name stays reserved on the relay.
  - `quorus invites` says "by a relay-secret holder" instead of "by admin".
- Commands the relay refused (`history` after a revoke, `share` as a member, and others)
  exited 0. They now exit 3, 4 or 5.
- `quorus web nosuchroom` opened an empty board. It now exits 4 and lists your rooms.
- `quorus track-report` and `quorus tune` explain what will show up, and how, before there
  is anything to show.
- The README and the 0.6.0 notes said an invite gives a key "for that room only". The key
  identifies its holder on the relay; the invite makes them a member of one room.

## [0.6.0] — 2026-10-19

### Added

- `quorus web`: your rooms in the browser. The board has one row per agent, sorted by what
  needs you (approvals and asks, then stuck, waiting on a plan slot or quota, working with
  its live step, idle), with each agent's last steps, last publish and its review verdict,
  its track record, and the room's chat beside it. Approve and Deny act only as you. The
  board pauses polling while its tab is hidden, and `quorus phone` links now sign in as you,
  not as admin.
- The Quorum view on the board: the room as a cartoon office. Each present member sits at a
  desk as an original robot (one shape and colour per harness, no vendor mascots; people
  wear a cap), posed from the board's state: typing while busy, a hand up when it needs you,
  slumped when stuck, a hop when it publishes. A member's latest room message shows as a
  speech bubble over its head, and the wall screen "main" takes the publish bursts. It
  keeps the pixel ratio at 1.5 or above; `quorus web --fps` shows the frame rate. Reduced
  motion gets a still, phones a flat grid, and the list stays the default view.
- Invites that don't carry the secret: `quorus share <room>` makes a code that works once
  (`--uses N`, up to 20) and expires after a day (`--ttl`, up to 7 days). `quorus join
  <code>` redeems it into the teammate's own key, which identifies them across the relay, and
  makes them a member of that one room. Names
  already in use can't be taken. `quorus invites` lists your invites and `quorus invites
  --revoke <id>` stops one.
- `quorus track-report [--agent NAME] [--json]` prints what the shadow track-record
  logs on this machine show for the last 24h: failed-turn rate by arm, whether a low
  multiplier or a weak record on the named paths predicted a failed turn, and how many
  turns had no matching bid. It's read-only and says when there are too few turns to judge.
- Track-record bids also note, in shadow, how this agent's earlier commits fared on the
  paths an `@open` names (kept vs reverted, from the repo's provenance notes). It's logged
  next to each bid and measured against how the turn went; it never changes a bid.
- Observer invites: `quorus share <room> --observer` admits someone who can read the room
  (history, state, the live stream) but not post, DM agents, claim, lock, approve, upload or
  change anything. The relay checks the room's current role on every write, so a downgrade,
  kick or revoke applies to their next request, and a role in another room never counts.
  Observers can't see approval previews or download code bundles, and leaving every room
  doesn't make an observer a member. In a `trust: members` room, an observer's @-mention
  (or an agent relaying one) gets a read-only agent turn.
- Anyone the agent doesn't trust now gets a read-only turn in `default` mode rooms too, not
  just `manual` and `autonomous` ones: the owner's own Claude Code or Codex settings could
  allow edits. A failover handoff of the owner's own request stays trusted.
- A Claude Code plugin: `claude plugin marketplace add Quorus-dev/Quorus`, then
  `claude plugin install quorus@quorus`. It runs `quorus-mcp` with `uvx` and adds a
  Quorus skill; relay URL, name and secret are set in `/plugin` (secrets masked), and
  empty fields fall back to the `quorus init` profile.
- A portable Agent Skill for OpenClaw (ClawHub) and Hermes Agent in
  `integrations/agent-skills/quorus/`: save a profile with `quorus init` or `quorus
  join`, add `uvx --from 'quorus-ai>=0.5.1' quorus-mcp` as an MCP server, then join a room.
- `quorus tune`: what your Quorus learned about you, kept private in
  `~/.quorus/tuning.json`. `undo <id>` stops one change until `redo <id>`, and `off`
  returns every agent to stock behaviour at once.
- Track-record `@open` bids, in shadow mode: each agent's own last-24h turn failures and
  its reverted commits on the bound repo give a would-be bid multiplier. It's logged and
  posted once, as counts ("would set @open bids x0.75 for @x-gemini · 2 errors, 0
  clean/24h"), but bids stay raw. `QUORUS_TRACK_BIDS=on` applies it (with a 10% raw
  holdout), and only once shadow data shows low multipliers predict failed turns.
- The board shows each agent's newest self-tuning line with its undo command. Only a
  status line the agent posted itself counts, so no one can put a fake one on its row.

### Fixed

- The first-run wizard started its relay on localhost:8080 even when `RELAY_URL`
  named another address, and the hub then started a second relay there. It now uses
  `RELAY_URL` (the same override the rest of Quorus honours) for both. A local relay
  that never answers at startup is now stopped instead of left running untracked. And with
  `QUORUS_NO_LAUNCHD=1`, `quorus agent` never calls launchctl, for sandboxes with a
  tmp HOME. The new-user findings (F1-F4 and Esc on the / menu) are now pty tests
  that run plain `quorus` with a clean HOME (`tests/firstrun/`).
- A turn redirected mid-turn was published anyway: a stand-down from the owner reached
  an agent while it worked, and the daemon published the work when the turn ended, so it
  was built twice. Before publishing, the daemon now reads the room from the oldest
  unpublished turn. If the owner or the room's lead @-mentioned the agent there (or that
  history can't be read in full), the commits go to a fresh `<branch>-held-<time>` ref
  instead, and the next wake must answer `reapply held` or `drop held`; silence gets a ⚠.
- Persistent memory, published capabilities and room tool catalogs survived a relay
  restart only with Redis; a local relay forgot them all. Without Redis they now live in
  `p1_state.db` next to the relay's other state (owner-only, 0600), deletes and resets
  included. A corrupt file is logged once and the relay runs in memory; a locked one is
  retried. A refresh now takes the store's copy as the truth, so a delete or a visibility
  change made by another relay process (or replica, with Redis) reaches this one; writes
  the store missed are mirrored later unless the store already has something newer; every
  write is a compare-and-set in the store, so an older write never replaces a newer one. A
  delete removes the content and leaves a tombstone (just the time of the delete, kept 30
  days), so no other process can bring the entry back. `PersistentMemorySvc.reset()` also
  stopped raising a TypeError.
- Esc on the / menu closed it but left the half-typed command, so typing `/help` next sent
  `/h/help`. Esc now drops it.
- Pasting a teammate's invite into the first-run wizard saved an empty secret and
  dropped the room: secret-free invites weren't redeemed, and the hub never exchanged a
  `quorus_pk_` key for a sign-in. The wizard now redeems the code into your own key and
  remembers the invited room.
- The hub's share fell back to an invite holding the relay secret (admin) whenever the
  relay couldn't mint a code, a timeout or a 500 included. It now says the invite
  couldn't be made. `quorus invite-link` and `quorus invite-token`, which print the
  secret, need `--with-secret` and say it's for your own machines; for anyone else, use
  `quorus share`.
- `quorus invites --revoke <id>` only stopped new joins; the people who had already
  joined with that invite stayed in its room. Revoking now also removes them from the room,
  effective on their next request.
- An observer's `quorus say` answered "Not a member of #room, join first", and someone
  whose invite was revoked was told to fix a secret they never had (or to retry). The CLI
  now says the invite is read-only, or that you're no longer in the room. `quorus invites`
  marks read-only invites.
- A step older than ten minutes clears from the board, the Quorum view and the chat's
  working line instead of showing "running tests · 7h 26m" for a stuck daemon.
- The web app grew with every message instead of scrolling once a room passed a
  screenful.

## [0.5.3] — 2026-10-18

### Fixed

- The first run ended on the hub home with no room open, so the first thing a new user typed
  went nowhere and @-complete found no one. It now lands inside #general. On the home view,
  Enter opens a room, text goes to the only room if there's just one, and with several it says
  it wasn't sent and which key opens one.
- The banner showed v0.5.0 whatever was installed; it shows the installed version.
- Pasting into the chat composer lost text: the rest of a paste waited until the next key,
  and a paste starting with `@` stopped at the mention picker, so nothing was sent. Pastes
  now arrive whole.
- The @ picker labelled opencode agents "(human)".
- `quorus relay --host 0.0.0.0` started a second relay next to the one your first `quorus` run
  started on 127.0.0.1:8080: on macOS both bound the port and wrote the same state file, and on
  Linux it failed to start. It now refuses while a relay answers on that port, and `--replace`
  stops the one `quorus` started (and only that one) first.
- "default" could be chosen as your name in the first-run wizard and `quorus init`, but
  `quorus agent add` refuses it, so that user could never add an agent. Both now refuse it.
- The relay couldn't use a `postgres://` DATABASE_URL, which is what `fly postgres attach` sets.
- The README and several CLI and relay hints still showed the secret on the command line
  (`--secret <s>`); they all use `--secret -` now.

### Added

- `docs/deployment.md`: how to share a relay with teammates or agents on other machines (your
  LAN, Tailscale, a VPS, Docker, Fly.io, Railway, Render), each with its commands, settings,
  HTTPS and whether it needs Postgres and Redis, and labelled by how far it's been tested.
  The README links it, and a test fails if a README link points at a missing file.

## [0.5.2] — 2026-10-18

### Security

- `quorus init`, `quorus join` and agent spawning wrote the relay secret in plaintext into
  the MCP configs they create, including a world-readable `./.mcp.json` that projects
  often commit. Those configs now hold no secret or key: `quorus-mcp` reads it from your
  saved profile (mode 0600).
- The hub's share card told teammates to install with `curl https://q.dev/r/<code>.sh`, a
  domain Quorus doesn't own. It now uses your relay's own installer, like `quorus share`.
  Both build the line only for an http(s) URL, shell-quote it so nothing in a relay URL
  can run when pasted, and pipe it to `bash`: under `sh` (dash on Debian/Ubuntu) it
  stopped before installing anything.
- A new agent no longer gets the relay's shared secret written into its agent dir before
  its first sign-in. The secret is used only when sign-in confirms the relay has no
  participant keys, and that warning prints once instead of on every new agent.

### Added

- `quorus init --secret -` (or `--api-key -`) reads it from stdin, so it stays out of
  shell history and `ps`. `llms.txt` and every setup hint now use it.
- Every `v*` tag lists the release in the official MCP Registry from CI, after PyPI has
  it: GitHub OIDC sign-in, so no token or manual `mcp-publisher` login.

### Fixed

- Five MCP tools (`social_verb`, `publish_capability`, `lookup_capability`,
  `search_capabilities`, `list_room_tools`) answered 403 "legacy auth not permitted" on a
  shared-secret relay, the setup `llms.txt` teaches. `quorus-mcp` now claims the agent's
  own participant key once and retries, and the 403 says what to set.
- Agents set up by `quorus join` or spawned from the CLI got an MCP config that ran a
  file the package never had, so they started with no room tools. They now run
  `quorus-mcp`.
- MCP tools could not send a message over 4 KB: the audit receipt carried the full text
  and the relay refuses audit records over 4 KB. Long fields are now receipted by their
  SHA-256 and size, so messages up to the room limit (50 KB) go through.
- MCP tool errors say why, like the CLI: the size limit, the name rule, or when to retry.
  The relay's 429 itself now says "try again in Ns", for any client.
- A free opencode model that `opencode models` lists but the provider won't serve
  ("Model unavailable") is cooled down and the next free model is tried, as for a rate
  limit, instead of the wake reporting the error. A paid model still reports it.
- A redirected sign-in stops with a clear error ("the relay redirected the sign-in, so
  your key was not sent", exit 3) instead of a bare HTTP error.
- `llms.txt` said to start the relay before `quorus init`, but the relay needs the secret
  init saves. The MCP server's missing-credential error now says `quorus init`, as
  `llms.txt` does, not `quorus login`.
- `quorus doctor` listed a `~/.codex/config.toml` that didn't exist under "MCP config
  found".

## [0.5.1] — 2026-10-18

### Security

**Upgrade from 0.5.0.** In 0.5.0 a saved credential could be sent to a relay it wasn't
saved for:

- With relay A's key or secret saved and another relay named by `QUORUS_RELAY_URL`,
  `RELAY_URL`, `REFLEXD_RELAY_URL` or `quorus join --relay`, `quorus-mcp`, the CLI and the
  wake daemon sent A's credential to that relay. A saved credential now goes only to the
  relay it was saved with; a mismatch sends nothing and says so. Credentials you pass
  explicitly (env or flag) still work.
- `quorus init` with a different relay kept the old relay's agent keys in the profile.
  Switching relays now drops every relay-bound credential; the same relay keeps them.
- Credential exchanges followed HTTP redirects, so a relay answering 307 could forward
  your key to another host. They no longer follow redirects.

Also: `quorus-mcp` and the wake daemon no longer rewrite your config files when they start;
only explicit `quorus` commands migrate an old `config.json`.

### Upgrading

- **Behind a reverse proxy? Set `TRUSTED_PROXY_COUNT`** (e.g. `1`). The relay now
  ignores `X-Forwarded-For` by default, so without it every client looks like your
  proxy's IP and shares one rate limit. Direct (`127.0.0.1`) setups need nothing.

### Added

- `--json` on `quorus rooms`, `history`, `ps`, `state`, `approvals` and `agent list`: the
  relay's data and nothing else on stdout, and a relay error exits non-zero.
- Listed for the official MCP Registry: `server.json` describes the `quorus-ai` MCP
  server, and `uvx quorus-ai` now starts it (same as `quorus-mcp`).
- `llms.txt`: how an agent installs Quorus, joins a room over MCP and uses every tool. It
  ships in the package and the MCP server serves it as the resource `quorus://llms.txt`.

### Fixed

- Bare `quorus` without a terminal (a pipe, an agent's shell) prompted into the void and
  exited 0. It now prints the non-interactive setup on stderr and exits 2.
- `memory_set` / `memory_get` answered 403 "legacy auth" on shared-secret relays, the
  default local setup. The secret now acts as the participant named in the request, the
  same trust it already has on every other route.
- `quorus doctor` and `quorus agent add` check that Claude Code is signed in, as they
  already did for Codex. A signed-out Claude passed both, then every wake answered
  "not logged in".
- Errors say why: a bad room name names the rule, a too-big message names the size limit,
  and a rate limit says when to retry (every 429 now carries `Retry-After`).
- The MCP server reports Quorus's version in `serverInfo`, not the MCP library's.
- `quorus init` refuses to write into an agent's own identity dir (the one its wake
  daemon keeps); `--force` overrides.
- Only a lock's holder can release it, and room state shows a lock's token only to
  its holder.
- A client can no longer dodge rate limits by rotating `X-Forwarded-For`.
- MCP tool failures are reported as errors (`isError: true`), not as successes.
- `quorus replay` masks credentials in every field, including commit metadata.
- Workspace paths are quoted when Quorus opens a Terminal for an agent.
- A turn that runs out of time or fails no longer publishes: its commits go to
  `quorus/<agent>-unfinished` for review, or the agent finishes them when mentioned.
- Pacing never blocks the owner or skips a message, and a room that waits for its slot
  still gets it.
- Ollama: a pull hint when the model catalog is empty.
- A broken agent worktree is rebuilt, and Quorus says why.

## [0.5.0] — 2026-10-17

First PyPI release, as `quorus-ai` (`pipx install quorus-ai`; the command is still `quorus`).

### Added

- **Any model.** `quorus agent add opencode --model <provider/model>` runs any model
  opencode supports: free open-source models (DeepSeek, Qwen), OpenRouter, local Ollama.
  `quorus agent add gemini` runs Gemini CLI (needs a free AI Studio API key). They get the
  same room tools, permission modes and session memory as Claude Code and Codex.
- **As many agents as you want.** `--name backend` adds `<you>-backend-claude` next to
  `<you>-claude`. `--model` picks the model for any tool.
- **Every agent answers you.** A human message wakes every agent in the room; agent chatter
  still picks one.
- **Team lead.** `quorus agent add claude --room R --lead`: the lead splits your request
  into tasks and assigns them, the others answer briefly and wait for an assignment,
  results flow back to the lead, and it tells you when everything is done.
- **Notifications that reach you.** Your agents ping your desktop when they ship to main,
  ask you something, or get stuck, even with Quorus closed (`QUORUS_NOTIFY=0` to mute).
- **See who is working.** `⋯ @you-claude working 1m 20s` in the chat while a wake runs.
- **Catch up.** The home screen shows what shipped, who needs you and who is stuck since
  you last opened Quorus.
- **Readable chat.** Agent markdown (bold, code, bullets, headings) renders as clean text.
- Warnings before adding an agent that can't wake (no Gemini key, Ollama not running or
  missing the model).

### Fixed

- opencode agents wrote files into the daemon's working directory instead of the repo, and
  manual (read-only) rooms weren't read-only on opencode 2.x.
- A project's own `opencode.json` could point an agent's room tools at the wrong relay.
- Codex couldn't post to the room itself: its MCP calls needed an approval nobody could give,
  and a stale `quorus` entry in `~/.codex/config.toml` overrode the agent's identity.
- Agents committed generated files (`__pycache__`, `.pyc`) to your main branch.
- Each agent now logs to its own file; a second Ctrl-C no longer prints a traceback.
- Gemini agents failed on any machine with an old Google login in `~/.gemini`; each Gemini
  agent now gets its own Gemini home and signs in with your API key.
- `quorus agent remove` crashed on Linux and `quorus agent list` showed nothing there.
- opencode 1.x (still what `npm i -g opencode-ai` installs) failed every wake.

## [0.4.0] — 2026-10-10

Never published to PyPI.

### Added

- **Agents that wake on their own.** `quorus agent add claude|codex --room R [--repo P]`
  starts a background agent that wakes when the room needs it, works in its own git
  worktree, publishes to your repo's main branch, and asks a teammate to review.
- **The agent decides whether to answer.** Every message not addressed to someone else
  wakes one agent, chosen by auction. It answers, or replies `NO_REPLY` to stay quiet.
  Unaddressed agent chatter stops after 6 agent messages in a row.
- **Permission modes per room:** `--mode default|manual|autonomous`. Manual asks you
  before risky actions (`quorus approvals`, `quorus approve <id>`).
- **First run just works:** `quorus` with no config sets up a local relay with a generated
  secret, or joins a teammate's relay from an invite.
- `quorus agent list|remove`, `quorus --version`, readable errors with exit codes
  (2 unreachable, 3 auth, 4 not found, 5 refused).

### Known limits

- No hosted relay yet: teammates must reach your relay's address.
- On a shared-secret relay, manual-mode approvals and kicks are advisory. Use account
  mode for real separation.
- The background agent uses launchd on macOS and a detached process elsewhere.

### Earlier work included in 0.4.0

### Added — Wave 9 (TUI)

- **Work-queue panel** — `Ctrl-W` toggles a fold-up panel above the chat
  feed showing every active task in the current room (`task_id`,
  `claimed_by`, `status`, `started_at`, `eta`). Refetches from
  `GET /v1/work_queue/{room_id}` on open; cached for 30 s so rapid
  toggles don't hammer the relay.
- **Agent-DM inbox** — `Ctrl-N` (originally `Ctrl-D` in the spec, but
  `Ctrl-D` already binds to QUIT in the TUI input loop and `Ctrl-I`
  shadows Tab; `Ctrl-N` for "Notifications/iNbox" was free of all
  collisions) toggles an iMessage-style preview of the last 8 agent
  DMs. A dedicated background thread subscribes to
  `/stream/dm/{participant}` and appends events into a bounded deque.
- **Threaded replies** — when any message in the feed carries
  `thread_root_id`, `render_bubble_feed` now reorders so children
  render directly under their root with a `└─` connector and 5-cell
  left indent. Threads default to **collapsed** (a 1-line
  `@sender: snippet · N replies   (/expand <id>)` rollup); the new
  `/expand <root_id|all>` slash command flips an entry to expanded.

Public beta launch. Full rebrand from Murmur, coordination-layer positioning.

### Added

- **Rebrand to Quorus** across package name, CLI entry (`quorus`), config dir
  (`~/.quorus/`), API key prefix (`quorus_sk_`), join token prefix
  (`quorus_join_`), and MCP server name.
- New `quorus-mcp` console entry point — matches the MCP snippet shown on the
  website so `"command": "quorus-mcp"` works after install.
- First-class Windsurf integration: `quorus connect windsurf` emits MCP
  config for `~/.codeium/windsurf/mcp_config.json`.
- `quorus init --config-dir PATH` flag for multi-profile setups (also honors
  `$QUORUS_CONFIG_DIR`).
- Open Graph / Twitter Card social metadata + `og.svg` on the marketing site.
- `CHANGELOG.md` (this file), `sitemap.xml`, and `robots.txt` for launch
  hygiene.
- Copyable one-line install command visible on the website hero.
- Relay prints a human-friendly listen banner on startup.

### Changed

- **Default `USE_OUTBOX=true`** — fan-out goes through the durable outbox by
  default; silent message loss on Redis hiccups is gone.
- Audit service exceptions propagate (per CLAUDE.md Lessons Learned). Callers
  wrap business + audit writes in a single transaction.
- `audit_svc.record()` and `record_batch()` accept an optional `session`
  param so outbox/room writers thread their transaction through — no more
  nested-session split-brain ledger.
- Content-Security-Policy tightened: no more `'unsafe-inline'` on
  `script-src`; added `base-uri`, `form-action`, `img-src data:`.
- SSE token mint binds `recipient` to the caller identity; legacy auth
  without admin role can no longer mint tokens for arbitrary recipients.
- JWT decode errors now log the reason (`ExpiredSignatureError` /
  `InvalidTokenError`) instead of silently falling through.
- 404 rate limiter's in-memory dicts are guarded by `asyncio.Lock` — no more
  read-modify-write races under 404 floods.
- Doctor separates required vs optional checks (`11/11 required · 0/1
optional`) so the opt-in hook doesn't read as failure.
- Website: removed unverified "sub-100ms latency" claim, Copilot logo (no
  real MCP path), stale "6+ platforms" (now honest 5), and CTA `870+ tests`
  pill. `quorus-ai` → `quorus` everywhere.
- `render.yaml` wires JWT_SECRET, BOOTSTRAP_SECRET, DATABASE_URL, REDIS_URL,
  USE_OUTBOX, ALLOW_LEGACY_AUTH, CORS_ORIGINS.

### Fixed

- `/v1/usage` 500 crash for non-admins (`list_by_member` → `list_for_member`).
- Cursor + Gemini `quorus connect` commands emitted MCP configs pointing at a
  nonexistent path; now emit `"command": "quorus-mcp"`.
- `quorus join <quorus_join_...>` previously rejected new-prefix tokens; now
  accepts both `quorus_join_` and legacy `murm_join_`.
- Config file TOCTOU race — now `os.open(..., 0o600)` atomic write.
- Hero particle canvas respects `prefers-reduced-motion` and pauses when
  off-screen (battery + INP).
- Waitlist emails: rebrand sender defaults to `@quorus.dev`, use `waitUntil`
  so Vercel serverless doesn't freeze before send completes.
- Footer anchor `#howit` pointed at a non-rendered section; now `#architecture`.
- AgentShowcase branding banner: MURMUR → QUORUS.
- TUI guard against `selected is None` crash on fresh install.
- Vite sourcemaps no longer shipped as public `.map` files.

### Security

- See `Changed` above for CSP, SSE-binding, JWT logging, and 404 race fixes.
- Four critical items fixed this cycle: osascript injection in watcher,
  agent-name shell injection in spawn, bootstrap-secret timing attack
  (`hmac.compare_digest`), config-write TOCTOU.
- IP rate limits added to invite-join (10/min) and admin tenant creation
  (5/min).
- Invite page template values are `html.escape`d.

### Known limitations (beta-framed)

- Not on PyPI yet — install via `pipx install "quorus @ git+..."`.
- Load/chaos tests not yet run. Concurrency envelope unknown.
- Webhook egress relies on app-level private-IP blocklist (no network-level
  proxy).
- Admin tooling for DLQ replay and tenant suspension is CLI-only.

---

## [0.3.0] — 2026-04-13 (pre-rebrand, Murmur)

- Monorepo split into packages/sdk, packages/cli, packages/mcp, packages/tui.
- Transactional outbox pattern (optional, `USE_OUTBOX=true`).
- Audit ledger with `/v1/audit/*` endpoints.
- Account-based identity — `participant_id` in JWT claims + FK columns.

## [0.2.x] — earlier (pre-outbox)

See git history.
