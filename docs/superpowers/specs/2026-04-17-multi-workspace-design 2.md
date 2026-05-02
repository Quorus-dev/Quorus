# Multi-Workspace Support (Client-Side)

Date: 2026-04-17
Status: Approved for implementation

## Problem

Quorus is already fully multi-tenant on the relay side (JWTs carry `tenant_id`, all keys are namespaced `t:{tid}:*`). The client — TUI, CLI, config, and MCP server — is not. Each local install can only hold one identity: one `relay_url`, one `api_key`, one `instance_name`, in a single `~/.quorus/config.json`. A user with access to two workspaces has to manually swap config files or set env vars.

This spec adds first-class multi-workspace support on the client, with a TUI-native switcher and clean coexistence with existing single-workspace installs.

## Non-goals

- **Server/relay changes.** Tenant isolation already works. Zero relay code changes.
- **Per-room workspace mixing.** A workspace is a boundary; messages never cross. Cross-workspace invites are a separate feature (deferred).
- **Team/org management UI.** Creating or administering a workspace on the relay is still done via the signup endpoint. We're adding client switching, not account management.
- **Guest/shared profiles.** Each profile is a full identity owned by one user.

## Design shape

**Profile files + current-pointer in the existing config.json.** Each workspace is one file under `~/.quorus/profiles/<slug>.json`. The top-level `~/.quorus/config.json` becomes a small pointer:

```json
{ "current": "work", "profiles": ["work", "personal", "acme"] }
```

Each profile file holds what today's `config.json` holds:

```json
{
  "relay_url": "https://quorus-relay.fly.dev",
  "instance_name": "aarya",
  "relay_secret": "",
  "api_key": "sk_live_...",
  "workspace_label": "Work",           // optional, user-friendly display name
  "workspace_id": "…"                  // optional — server tenant_id if we learn it
}
```

### Why this shape (recap from brainstorm)

- **One file per profile** keeps a corrupt/leaked file scoped to one identity. 0o600 still applies per file.
- **Pointer file separate from data** makes "which is current" a cheap one-line write, independent of the larger profile blobs.
- **Legacy coexistence is trivial.** If we open `~/.quorus/config.json` and see the old flat shape (`relay_url` at top level), we treat it as the sole profile named `default` and migrate on next write.

## Architecture

### New module: `quorus/profiles.py`

Single source of truth for profile storage. Everything else goes through this.

```python
class ProfileManager:
    def __init__(self, config_dir: Path | None = None) -> None: ...

    # Read
    def list(self) -> list[str]: ...                  # slug names, stable order
    def current(self) -> str | None: ...              # current slug, or None
    def get(self, slug: str) -> dict | None: ...      # profile data, or None
    def current_profile(self) -> dict | None: ...     # sugar: get(current())

    # Write
    def save(self, slug: str, data: dict) -> None: ...       # atomic, 0o600
    def set_current(self, slug: str) -> None: ...            # updates pointer
    def delete(self, slug: str) -> None: ...                 # removes file; if current, unsets

    # Migration
    def migrate_legacy_if_needed(self) -> str | None: ...
        # If ~/.quorus/config.json has the old flat shape,
        # move it to profiles/default.json and rewrite config.json
        # as a pointer. Returns the migrated slug or None.
```

Storage layout under `~/.quorus/`:
```
config.json                 # {"current": "work", "profiles": [...]}
profiles/
  default.json              # legacy migration target
  work.json
  personal.json
```

Atomic write: write to `profiles/<slug>.json.tmp` with O_CREAT|0o600, then `os.replace`. Same pattern as `ConfigManager.save` today.

### `quorus/config.py` — backward-compatible facade

Keep `ConfigManager` working for any code path that currently calls `.load()` — it now returns the **current profile's data** (not the pointer file). That way the existing TUI/CLI/MCP code keeps working until we migrate call sites one at a time.

Add an optional `profile` kwarg: `ConfigManager(CONFIG_FILE, profile="acme")` returns acme's data.

### TUI changes (`packages/tui/quorus_tui/hub.py`)

**Live switching** — the key UX piece.

`run_hub()` today runs a single while-loop with threads captured at startup. Refactor so workspace identity lives inside a restart-able "session" loop:

```
run_hub():
    while True:
        profile = ProfileManager().current_profile() or _first_launch_setup()
        action = _run_session(profile)   # returns "quit" or ("switch", new_slug)
        if action == "quit":
            return
        # switch: loop continues, new profile picked up
```

`_run_session(profile)` contains today's `run_hub` body from "start threads" onward. It returns:
- `"quit"` when the user quits.
- `("switch", slug)` when `/workspace <slug>` is dispatched and approved.

Switching tears down cleanly:
1. Set `stop_event` to end SSE + poll threads.
2. `.join(timeout=2)` each thread.
3. Clear HubState.
4. Return `("switch", slug)` to `run_hub`, which picks up the new profile and calls `_run_session` again.

New slash commands:
- `/workspace` — prints the workspace list (current marked), same flavor as `/rooms`.
- `/workspace <slug>` — switches to that slug. If slug doesn't exist, status-bar error.
- `/workspace add` — runs `_first_launch_setup` to add a new profile, then switches to it.

Header update: show `@name · <workspace-label>` in the top bar so the active workspace is always visible.

### CLI changes (`packages/cli/quorus_cli/cli.py`)

New subcommands (each one short — handler bodies under ~40 lines):

- `quorus workspaces` / `quorus workspaces list` — table of profiles, current marked.
- `quorus workspaces use <slug>` — set current.
- `quorus workspaces add [name]` — run the first-launch wizard, save as a new profile, set current.
- `quorus workspaces rm <slug>` — delete. Refuses if it's the only profile; prompts if it's current.
- `quorus login` — alias for `workspaces add` (shorter, matches user mental model).
- `quorus whoami` — prints current profile's instance_name + workspace label + relay URL.

Global flag on all existing commands: `--workspace/-w <slug>` — one-shot override. Priority: flag > `QUORUS_PROFILE` env > pointer file.

Existing `quorus init` stays working — on first run it writes to the pointer model (profile `default`).

### MCP server (`packages/mcp/quorus_mcp/server.py`)

Add `QUORUS_PROFILE` env var. Priority for identity fields (relay_url, api_key, instance_name, relay_secret):

1. Direct env var (`QUORUS_RELAY_URL`, etc.) — unchanged, beats everything.
2. Selected profile: if `QUORUS_PROFILE` is set, load that profile.
3. Current profile from pointer file.
4. Legacy flat `config.json` (auto-migrated on next write).

This keeps the existing per-client env-override pattern working. A user who wants Claude Code and Cursor in different workspaces sets `QUORUS_PROFILE=work` for one and `QUORUS_PROFILE=personal` for the other.

## Data flow on workspace switch (TUI)

```
user types "/workspace acme"
  ↓
_slash_workspace:
  - ProfileManager.get("acme")? → 404 → status_bar error, stay
  - if ok: state.request_switch("acme"); return True
  ↓
main loop next tick sees pending switch request:
  - stop_event.set()
  - poller.join(2); sse.join(2)
  - console.clear()
  - return ("switch", "acme")
  ↓
run_hub outer loop:
  - ProfileManager.set_current("acme")
  - new profile loaded, _run_session starts fresh
  - new threads, new HubState, new header
```

The existing `stop_event` in hub.py already shuts down threads cleanly on quit — we reuse that path.

## Error handling

- **Corrupt profile file.** `ProfileManager.get` returns `None`; caller falls back to current or shows error. Never auto-delete — surface the problem.
- **Pointer references missing profile.** Treat current as unset; TUI falls through to first-launch wizard. CLI `whoami` says "no current workspace — run `quorus workspaces use <name>`".
- **Slug collision on add.** Wizard suffixes with `-2`, `-3`, … like the existing signup collision path.
- **Delete current profile.** TUI session exits to first-launch (no profiles left) or prompts user to pick a replacement. CLI prints and requires `--yes`.
- **Thread teardown timeout.** If SSE thread doesn't stop within 2s, log it and proceed anyway — threads are daemons, so a leaked one dies with the process. Don't block the switch on a wedged network call.

## Testing

- `tests/test_profiles.py` — unit tests for ProfileManager: create, list, switch, delete, atomic save, legacy migration, missing/corrupt files.
- `tests/test_config_compat.py` — confirm existing `ConfigManager.load()` call sites see the current profile transparently.
- `tests/test_tui_workspace_switch.py` — simulate `/workspace` dispatch; assert stop_event fires, state clears, new threads start with new profile.
- Manual: run `quorus login work`, `quorus login personal`, `quorus workspaces`, `/workspace personal` inside TUI, verify header + room list change.

## Build sequence

Split into four commits, each landing independently:

1. **`feat(profiles): ProfileManager + legacy migration`** — new module, tests, no caller changes. `ConfigManager.load()` still reads `config.json` directly for now.
2. **`feat(config): wire ConfigManager to ProfileManager facade`** — `ConfigManager.load()` starts returning current profile data; auto-migrate legacy on first call. All existing code paths keep working.
3. **`feat(cli): quorus workspaces, login, whoami`** — CLI subcommands. No TUI changes yet; user can switch workspaces by CLI + restarting TUI.
4. **`feat(tui): live workspace switching`** — refactor `run_hub` into `_run_session` + outer loop, add `/workspace` slash command, thread teardown, header update.

Each commit ships with its own tests. Commit 4 depends on 1-3; 1-3 are independent of each other.

## Out of scope / follow-ups

- **Cross-workspace invite flow** — acknowledged separately. Needs guest credentials.
- **Workspace-aware SSH-like tokens** — if we later want `quorus share work:#general` syntax.
- **Encrypted-at-rest profiles** — 0o600 is the bar today; revisit if we add secrets beyond API keys.
