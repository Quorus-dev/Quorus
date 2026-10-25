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

| Platform | Snippet references the secret by | Why |
|---|---|---|
| antigravity | nothing: drop `RELAY_SECRET` from the JSON | `quorus-mcp` already reads the saved secret, the same rule auto-write uses |
| ollama, aider | `os.environ["QUORUS_RELAY_SECRET"]` | a script file should not hold a credential |
| http | `$QUORUS_RELAY_SECRET` in each curl line | the system prompt shows the variable, not the value |

For the three env-var platforms, print one line telling the user where the secret
lives (`relay_secret` in `<config dir>/config.json`, the same wording as the 0.7.5
quickstart line) and that they set `QUORUS_RELAY_SECRET` from it. The secret never
appears in the output.

If the user passed an explicit secret for a relay other than the saved one, keep the
same rule: the output names the variable, never the value.

Same commit: delete the seven unreachable `_connect_*` functions and fix the docstring.

## Tests

`tests/test_connect_never_prints_secret.py`: for each of ollama, antigravity, aider and
http, run `_cmd_connect` with a known secret (HOME and `QUORUS_CONFIG_DIR` isolated),
assert the secret is absent from the output, the env var name (or for antigravity, no
`RELAY_SECRET` key) is present, and the room join still happens. Fails on 0.7.5.
Existing `test_connect_*` tests keep passing.

## Not in scope (open question for Arav)

Even after this, every `connect` agent holds the operator secret, which can approve,
mint invites, kick and delete rooms. The right long-term fix is for `connect` to claim
a participant key for the agent's name (`/v1/auth/claim-name`) and print or store that
instead. That changes what those agents can do, so it needs its own plan and decision.
