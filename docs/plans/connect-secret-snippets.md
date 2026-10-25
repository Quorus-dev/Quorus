# 0.7.6: `connect` snippets stop printing the relay secret

Status: plan, waiting on Codex GO. Follows the 0.7.5 security cut (PR #25), which
fixed `invite <name>` and `quorus quickstart`.

## Problem

`quorus connect <platform>` has two paths (`_cmd_connect`, `cli.py` ~3100):

- Auto-write platforms (claude, claude-desktop, cursor, codex, gemini, windsurf,
  opencode, continue, cline) write the client's MCP config. When the secret
  is the one already saved in the profile, it is left out of that file, because
  `quorus-mcp` reads it from `config.json` itself (`cli.py` ~3177). This path is
  fine.
- `prose_generators` (ollama, antigravity, aider, http) print a snippet to
  paste, and every one embeds `RELAY_SECRET` as a literal: `QuorusClient(url,
  "<secret>", name)` for ollama and aider, `"RELAY_SECRET": "<secret>"` in the
  antigravity MCP JSON, and `Authorization: Bearer <secret>` in all five curl lines of the http
  system prompt. A screenshot or pasted terminal carries the operator credential,
  and the http one is meant to be pasted into an agent's system prompt, so it also
  ends up in that vendor's logs.

Also dead: `_connect_codex/cursor/gemini/windsurf/cline/continue/opencode` are
defined but never called (those platforms auto-write), and each still templates
the secret. The `_cmd_connect` docstring says prose snippets are copied to the
clipboard; nothing copies them.

## Change

Where the secret comes from decides what the snippet can say. `load_config` gives
`relay_secret`, `relay_url`, `saved_relay_url` and `relay_secret_from_env`. Call the secret
"saved" when it equals the profile's `relay_secret` and `same_relay(relay_url,
saved_relay_url)` holds; otherwise it came from this shell's `RELAY_SECRET` for a relay
the profile doesn't hold.

| Platform | Saved secret | Secret from this shell only |
|---|---|---|
| antigravity | drop `RELAY_SECRET` from the JSON | `"RELAY_SECRET": "<paste your RELAY_SECRET>"` |
| ollama, aider | `os.environ["QUORUS_RELAY_SECRET"]` | same |
| http | `"$QUORUS_RELAY_SECRET"` in each curl line | same |

- antigravity, saved: Antigravity launches `quorus-mcp` on its own, and `quorus-mcp`
  resolves the saved secret through `load_config` because the JSON's `RELAY_URL` is the
  saved relay. Same rule as auto-write (`cli.py` ~3177).
- antigravity, shell only (Codex on dec213c0): that process never sees this shell's
  environment, so omitting the key would leave it with no secret, or a withheld one if
  the profile is for another relay. The JSON keeps the key with a placeholder, and the
  output says to paste the `RELAY_SECRET` this shell has set, or to save the relay
  first (`quorus init <name> --relay-url <url> --secret -`, which reads it from stdin) and rerun `connect` to drop the key.
- Every platform prints one line naming where the value lives: `relay_secret` in
  `<config dir>/config.json` when saved (the 0.7.5 quickstart wording), or "the
  `RELAY_SECRET` set in this shell" otherwise. The value itself never appears.

Same commit: delete the seven unreachable `_connect_*` functions and fix the docstring.

## Tests

`tests/test_connect_never_prints_secret.py`, HOME and `QUORUS_CONFIG_DIR` isolated, a known
secret, `_auto_join` stubbed. Each test checks the generated setup, not that `connect` ran:

1. Every platform, both cases: the secret is absent from the output (raw, and with
   whitespace stripped, since Rich wraps).
2. antigravity, saved: parse the printed JSON; with only its `env` block in the
   environment, `load_config()` returns the saved secret for that relay.
3. antigravity, saved profile for relay A, `connect` run with `RELAY_URL=B` and
   `RELAY_SECRET=S`: the JSON keeps the placeholder key. With the placeholder replaced
   by S, `load_config()` returns S for B. Without the key it returns "" with
   `credentials_withheld`, which is the failure Codex named, kept as an assertion so
   the case can't silently regress.
4. ollama, aider: the snippet parses (`ast.parse`) and reads
   `os.environ["QUORUS_RELAY_SECRET"]`; the client gets that value when it's set.
5. http: every curl line carries `"$QUORUS_RELAY_SECRET"` in double quotes (so the shell
   expands it) and none has a literal bearer value.

All five fail on 0.7.5; the existing `test_connect_*` tests keep passing.

## Not in scope (open question for Arav)

Even after this, every `connect` agent holds the operator secret, which can approve,
mint invites, kick and delete rooms. The right long-term fix is for `connect` to claim
a participant key for the agent's name (`/v1/auth/claim-name`) and print or store that
instead. That changes what those agents can do, so it needs its own plan and decision.
