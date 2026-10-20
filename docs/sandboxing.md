# Sandboxing for autonomous runs

The plain truth for Quorus 0.6.3: an agent woken by Quorus runs as **your user** on
**your machine**, in its own git worktree of the bound repo. Quorus adds no OS sandbox of
its own: no container, no VM, no seccomp, no network filter. The only sandboxes that apply
are the ones the harness itself enforces, and they differ by harness. Autonomous mode is
for repos you trust the agent with. Every statement cites the code at the `v0.6.3` tag.

## What isolates an agent

- **A git worktree per agent**, not a sandbox. `scripts/reflexd_wake.py::agent_worktree()`
  creates `<repo's parent>/.quorus-worktrees/<repo name>/<agent>` on branch
  `quorus/<agent>` and the daemon
  publishes that branch into the repo's integration branch when a turn finishes cleanly.
  Agents with worktrees never share a checkout, so they cannot commit each other's
  half-finished edits. The exception: `agent_worktree()` returns `None` when the bound
  path is not the top of a git repository, its checkout is detached, or git fails, and the
  **writable** turn then runs in the bound directory itself (`cwd=worktree.path if
  worktree is not None else ws`, `scripts/reflexd.py`), shared by every agent bound there
  and without the publish and hold protections that need a worktree. A **read-only** turn
  without a worktree does not run at all: the daemon raises `SnapshotError` ("has no
  isolated worktree for a read-only turn … so it didn't run"), because the read-only
  snapshot/restore cannot see writes in a shared checkout (`scripts/reflexd.py`, before
  the harness starts). So the fallback can weaken isolation only for turns the owner
  already authorised to write. Bind a repo root, not a plain folder. The worktree is a directory on your disk with your permissions; nothing
  stops a shell command from writing elsewhere unless the harness stops it.
- **Daemon credentials are stripped from the agent's environment.** `wake_env()` removes
  `API_KEY`, `RELAY_URL`, `RELAY_SECRET`, `INSTANCE_NAME`, `QUORUS_API_KEY`,
  `QUORUS_RELAY_SECRET` and `QUORUS_PROFILE` (`_DAEMON_ONLY`) and gives the agent its own
  private config dir (`QUORUS_CONFIG_DIR`) with its own identity. Everything else in your
  environment is inherited.
- **Your Claude Code hooks do not run in a wake.** `claude_isolation_flags()` passes a
  settings file with `disableAllHooks: true`; your other Claude settings, skills, plugins
  and permission allow-list apply as they are.
- **Read-only turns are reverted.** A non-owner's request, an answer-only turn, or a
  manual room's Codex/opencode/Gemini runs read-only, and `scripts/reflexd_readonly.py`
  snapshots the worktree before and restores it after, saving any change to
  `refs/quorus/discarded/<agent>/<ts>`. This is detection and reversal inside git, not
  prevention: a command that wrote outside the worktree is not undone.

## What each harness enforces, by room mode

Room modes are `default`, `manual` and `autonomous` (`quorus room bind … --mode`;
`default` leaves the harness on your own settings). `readonly` is not a room setting: the
daemon applies it to a turn that must not write (a non-owner's request, an answer-only
turn). The daemon translates these into each harness's own permission system
(`scripts/reflexd_wake.py`, `scripts/reflexd_models.py`):

| harness | manual / readonly | autonomous |
| --- | --- | --- |
| Claude Code | `--permission-mode default` with every permission prompt routed to the agent's owner via the Quorus `approve` tool (manual); `--permission-mode plan` (readonly) | `--permission-mode acceptEdits --allowedTools "Bash Edit Write MultiEdit mcp__quorus"`: edits and shell run without asking; Claude Code's own always-ask actions still apply (`claude_mode_flags()`) |
| Codex | `sandbox_mode=read-only` | `sandbox_mode=workspace-write` with `writable_roots` set to the bound repo (worktree plus the main checkout's `.git`); `danger-full-access` is deliberately unsupported (`codex_wake_flags()`) |
| opencode | `edit`, `bash`, `webfetch` denied (`_OPENCODE_PERMS`) | `edit`, `bash`, `webfetch` allowed |
| Gemini | runs on your own Gemini settings; the read-only snapshot/restore is the only guard | same |

So in autonomous mode: Codex runs inside Codex's own workspace-write sandbox, which
confines file writes to the bound repo but still runs as your user; Claude Code and
opencode run with edits and shell **allowed and unsandboxed**, as your user, with your
environment minus the daemon's credentials. Shell commands can reach the network, your
home directory, and anything your user can.

## What this means

- Bind autonomous rooms only to repos where you would let a contractor run shell as you.
- Secrets in your environment or dotfiles are reachable by an autonomous Claude Code or
  opencode agent. Quorus strips only its own daemon credentials.
- Owner-scoped authority (a non-owner's instruction gets a read-only turn) limits **who**
  can make an agent write; it does not limit **what** a write can touch.
- Manual mode is the conservative default for Claude Code: every tool that needs
  permission asks you in the room or via `quorus approve`, and Codex/opencode review and
  propose without writing.

## What 0.6.3 does not do

- No container, VM, or filesystem sandbox provided by Quorus for any harness.
- No network policy; agents inherit your network.
- No resource limits beyond the wake time limit (`REFLEXD_SUBPROCESS_TIMEOUT_S`, default
  1800 s; missions `REFLEXD_MISSION_TIMEOUT_S`, default 3600 s) and the chat turn cap.
- No undo for effects outside git: an autonomous shell command that deleted a file outside
  the worktree, or called a network service, is not reverted by Quorus.
