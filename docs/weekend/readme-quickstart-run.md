# Fresh-user run of the README quick start — findings

Ran the README's **Install → 30-second tour** as a brand-new user on 2026-10-17,
in a throwaway `HOME` (`env -i`, no `QUORUS_*` / `REFLEXD_*` leakage), against
this branch. Every line below is reproduced output, not a guess.

Method / limits:

- **Install step skipped.** `pipx install "quorus-ai @ git+https://…"` needs the
  network; instead I ran the CLI from this worktree (dev equivalent). Package
  name `quorus-ai` and the `quorus` console script in `pyproject.toml:2,65` are
  correct, so the command itself checks out. Everything after install *was* run.
- **`quorus agent add <tool>` was not executed with a real tool on PATH.** It
  installs a launchd plist / spawns a background agent (a machine mutation), so
  I stopped at the point where it would. The missing-tool path is reported below.
- The first-run wizard started a real local relay on `127.0.0.1:8080`; it was
  killed after the run.

### Gotcha: a throwaway `HOME` does not isolate a first-run test run from an agent

An agent's environment exports its *own* identity — `QUORUS_CONFIG_DIR`
(→ `~/.quorus/runtime/agents/<agent>/`), plus `QUORUS_RELAY_SECRET`,
`QUORUS_API_KEY` and `QUORUS_INSTANCE_NAME`. Those beat `HOME`, so a plain
`HOME=$(mktemp -d) quorus init …` run from inside a wake writes the throwaway
values into the **real** agent profile: `config.json` + `profiles/*.json`, with
the bogus profile left as `current`. That is how my own room auth broke today
(bridge 401 `Invalid API key format` / `Invalid or missing auth token`) — I
restored `current` and removed the junk profile.

Avoid it by unsetting the identity vars, not just `HOME`:

```bash
env -u QUORUS_CONFIG_DIR -u QUORUS_RELAY_SECRET -u QUORUS_API_KEY \
    -u QUORUS_INSTANCE_NAME HOME=$(mktemp -d) quorus ...
```

I used `env -i HOME="$QH" PATH=/usr/bin:/bin …`, which also works because it
drops everything — but it strips `PATH` too, so tool detection reports `claude`,
`codex`, `gemini` and `opencode` as "not installed" even when they are (that is
why **#6**'s message names `claude`, not the missing repo). Keep the real `PATH`
when you want detection to behave like a real user's.

The in-process suite is already guarded against the same class of leak —
`tests/conftest.py:95` `_isolate_reflexd_runtime` pins `DEFAULT_RUNTIME_DIR` and
`ROOM_BINDINGS_PATH` under `tmp_path` (added 2026-10-08 after a test overwrote a
live agent's credentials). The remaining gap is *shelling out* to `quorus` from
inside an agent, which no fixture covers. Mitigations now in place: daemons
repair the agent identity before every wake (overseer, fix on `feat`), and
`71ddf64` stops `quorus init` from flattening a profile pointer in the first
place.

## What the README promises and what happened

| README claim | Result |
| --- | --- |
| `quorus` "walks you through picking a name and starting a local relay, or joining a teammate through an invite" (L33) | ✅ works |
| "pick a name, press Enter. Quorus starts a relay on this computer and puts you in #general" (L53-55) | ✅ works, `#general` created and open |
| HTTP API block `POST /rooms/{id}/messages`, `GET /messages/{name}`, `GET /stream/{name}`, `POST /rooms/{id}/lock` (L123-128) | ✅ all four exist (checked against the live relay's `/openapi.json`); `/docs` → 200 |
| `opencode` "2.x via `brew install opencode` or `npm i -g @opencode/cli`" (L71) | ✅ `@opencode/cli` is the right npm package (2.0.32) |
| "your already-open Claude Code / Codex sessions can join too: with the Quorus MCP server installed (**done by `quorus init`**)" (L104-106) | ❌ the quick start never installs MCP — see **#1** |
| "Quorus starts a relay … and puts you in #general" then the tour's later commands | ✅ `rooms`, `say`, `history`, `chat`, `agent list`, `ps`, `version` all clean |

## Findings

Ordered by severity. Repro is from the throwaway `$HOME`.

### 1. Following the README breaks the working local config (data loss) — `quorus init` over a hub-created setup

The quick start creates the setup via bare `quorus` (the hub). The MCP section
then tells the reader the MCP server is installed by `quorus init`, so the
natural next step is `quorus init <name> --secret <secret>` — which **replaces
the hub's config pointer with a flat config** and can silently point auth at a
profile with the wrong secret.

Repro (hub-created `$HOME/.quorus` with `profiles/default.json` holding the
real secret, then re-run init with a mistyped secret):

```console
$ quorus init alice --relay-url http://localhost:8080 --secret TYPED-WRONG
Warning: config already exists at …/.quorus/config.json — overwriting.
Config written to …/.quorus/config.json (permissions: 0600)
✓ Quorus initialized for alice
  ✗ relay: http://localhost:8080 (secret rejected)
```

After that, `$HOME/.quorus/config.json` is no longer the pointer
(`{"current": …, "profiles": […]}`) but a flat blob:

```json
{"instance_name":"alice","relay_secret":"TYPED-WRONG","relay_url":"http://localhost:8080", …}
```

`profiles/default.json` with the **real** secret is left behind, untouched and
now unreachable. The next bare `quorus` then comes up unauthenticated:

```console
Connecting to http://localhost:8080...
Not authenticated. Run: quorus doctor
No rooms yet. Press [n] to create your first.
```

Root cause: `_cmd_init` writes the flat config straight over `config.json`
(`packages/cli/quorus_cli/cli.py:4851`) while the pointer/profile layout is
owned by `ProfileManager` (`quorus/profiles.py:44-136`). Worse, any code that
later calls `ProfileManager.migrate_legacy_if_needed()` (`quorus/profiles.py:140`)
sees the flat shape, saves it as a **new `default` profile and sets
`current=default`** (`quorus/profiles.py:155-168`) — so the bogus secret becomes
the active one. That is how I lost my own agent's room auth today: the two IDEs
had written the flat file over `profiles/arav-opencode.json`'s pointer, a later
migrate created `profiles/default.json` and set `current=default`. Recovered by
`set_current("arav-opencode")` + `delete("default")`.

Suggested fix: in `_cmd_init`, go through `ProfileManager` (or detect an existing
pointer and `save()` into the current profile) instead of writing the flat file;
and make the overwrite warning name what is being replaced
("replacing profile default — the previous secret and relay URL are lost").

**Status: fixed in this branch.** `_cmd_init` now detects a pointer
(`_config_is_profile_pointer`) and writes into the active profile, leaving the
pointer and every other profile intact. The message reads `Updating profile
'default' … its relay URL and credentials are replaced.` Regression tests:
`tests/test_cli_init_profile_layout.py`. This also fixes the pointer case of #2
below.

**Refinement (review 2026-10-17).** Writing *first* still meant a mistyped
`--secret` overwrote the last working copy with no way back. `_cmd_init` now
checks the secret against the relay *before* touching the disk: `/health`, then
`GET /rooms` with the secret, and a `401`/`403` exits `3` with `nothing was
written — your existing config is untouched`. API-key auth gets the same guard
(`POST /v1/auth/token`, the cheapest way to ask the relay whether it knows the
key) — without it the profile was written with the bad key first and only failed
later at mint time. A relay that is simply down still
warns and proceeds (it can't be asked). The profile write also **merges** the
new values into the existing profile (dropping the superseded credential shape)
instead of replacing it, so `agent_api_keys` and push settings survive, and it
copies the file to `profiles/<slug>.json.bak` (0600) first — offline, an
unverifiable typo can no longer destroy the last working secret with nothing to
recover from. The credential POSTs also no longer follow redirects: a 307/308
re-sends the body, which would hand the API key to whatever host the redirect
names. Proven
against a real relay: wrong secret → exit 3, `profiles/work.json` byte-identical;
correct secret → same profile updated, pointer intact.

### 2. `quorus init` never warns it is replacing a *working* secret

Same repro as #1: the warning is `config already exists at …/config.json —
overwriting.` It doesn't say the previous relay URL and secret are gone, and
there is no confirmation prompt or backup. A user re-running init to fix a typo
loses the setup that was working. Cheap: print the old vs new `instance_name`
and `relay_url`, and require `--force` (or a `y/N`) when values differ.

### 3. First prompt prints empty parentheses: `Invite or Enter ():`

`packages/tui/quorus_tui/hub.py:424` —

```python
raw = Prompt.ask("\n  Invite or Enter", default="").strip()
```

rich renders an empty default as `()`:

```console
Press Enter to run Quorus on this computer, or paste a teammate's invite (quorus://… link or ABCD-EFGH code) to join their relay.
  Invite or Enter ():
```

The empty `()` reads like a missing value. Cheap: `show_default=False`. (Both
lines also say "press Enter" twice in a row.)

### 4. First-run success prints before the connection it describes

```console
Setting up Quorus on this computer... ✓
started your local relay (log: …/.quorus/relay.log)
✓ You're in as @alice.
Connecting to http://localhost:8080...
Created starter room #general — you can add more anytime.
```

"You're in as @alice" lands *before* "Connecting to …". The function docstring
(`hub.py:376-387`) intends the opposite order. Cosmetic but it makes the
happy path read like a race. Cheap: move the `✓ You're in as …` line to after
the "Connecting…" status.

### 5. The hub's config is a profile, but `quorus init`'s warning talks about `config.json`

Follow-on to #1: every mutating message from `_cmd_init` names
`…/.quorus/config.json`, while the thing a hub user cares about is
`profiles/default.json`. A user reading `quorus doctor` / `init` output has no
way to connect the two files.

### 6. `quorus agent add` error doesn't mention the missing `--repo`

README L59-60 passes `--repo ~/code/my-app`, a path most new users don't have:

```console
$ quorus agent add claude --room general --repo ~/code/my-app --mode autonomous --lead
agent: the `claude` CLI isn't installed or not on PATH — install it and log in first
```

The message is right about `claude`, but the tour's literal `~/code/my-app` is
never validated against reality, and the very first thing the hub suggests
("Next: put an agent in #general → `quorus agent add claude --room general
--repo <your-project>`") uses a different placeholder style than the README
(`<your-project>` vs `~/code/my-app`). Worth a repo-exists check with a clear
"that path doesn't exist" line.

### 7. `relay.log` prints a scary line on healthy requests

Every authenticated call logs `JWT decode unexpected error` (from a shared
secret, which isn't a JWT) next to a `200 OK`:

```console
INFO: 127.0.0.1:60423 - "GET /health HTTP/1.1" 200 OK
JWT decode unexpected error
INFO: 127.0.0.1:60425 - "GET /rooms HTTP/1.1" 200 OK
```

`quorus` tells users where the relay log is; the first thing it shows them is an
error on a successful request. Cheap: log at debug, or say "shared-secret auth
(no JWT)".

### 8. `ps` suggests `quorus spawn <room> <name>` without saying it launches a CLI

```console
$ quorus ps
  — no agents reporting presence yet —
  spawn one with quorus spawn <room> <name>
```

`quorus spawn`'s positional order is right (`spawn room name`), but a new user
who just wants presence has no way to know it launches Claude Code locally
(rather than just registering an agent). Minor.

## Not findings (checked, all correct)

- Package/console names, `@opencode/cli`, HTTP API paths, `/docs`, and every
  post-first-run command I ran (`rooms`, `say`, `history`, `chat`, `agent list`,
  `ps`, `version`, `agent remove nope`) behaved and read correctly.
- The first-run wizard does start a relay that **survives** closing the hub, and
  the second bare `quorus` goes straight to the hub with no wizard.
