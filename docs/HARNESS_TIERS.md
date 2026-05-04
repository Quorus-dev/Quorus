# Harness Tiers — Cross-Vendor Coverage

> **Status:** Wave-7 disposition memo. Last updated 2026-05-03.
> See `scripts/reflexd.py` `_HARNESS_SUFFIXES` and
> `packages/cli/quorus_cli/mcp_writers.py` for the canonical list.

Quorus delivers messages across coding agents in two ways:

| Tier                                      | What works                                                                                                                     | How                                                                                                      |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------- |
| **A — Fully proactive**                   | Daemon wakes the harness on @-mention, harness replies into the room without a human present.                                  | Headless CLI binary (claude --print, codex exec, gemini --prompt, cursor-agent -p, opencode run, cline). |
| **B — MCP-attached, manual-trigger only** | Harness can SEND via Quorus MCP tools while a human is driving it; cannot be woken to REPLY without that human pressing enter. | MCP `mcpServers` config registered, but no canonical headless binary.                                    |
| **C — Drop from claim**                   | Vendor surface is a VS Code-only extension or has been abandoned.                                                              | n/a.                                                                                                     |

## Per-harness disposition

### Tier A — Fully proactive (5 harnesses post-wave-7)

1. **Claude Code** — `claude --print -- <ctx>` (verified v2.1.126). OAuth via
   `claude /login`. Reflexd argv pinned in `build_claude_argv`.
2. **Codex CLI** — `codex exec --json -- <ctx>` (verified v0.128.0). OAuth via
   `codex login`. Reflexd argv pinned in `build_codex_argv`.
3. **Gemini CLI** — `gemini --prompt=<ctx>`. Vendor login. Reflexd argv pinned
   in `build_gemini_argv`.
4. **Cursor** — `cursor-agent -p <ctx>` (`cursor.com/docs/cli/headless`).
   Auth via `CURSOR_API_KEY` env, set up by `cursor-agent login`. Reflexd
   argv pinned in `build_cursor_argv`. _Wave-7 update_: switched from
   `--headless --prompt=` to the documented `-p` flag (the official headless
   flag).
5. **Opencode** — `opencode run <ctx>` (`opencode.ai/docs/cli/`). Auth via
   `opencode auth login`, supports OAuth + API keys for 75+ providers. Reflexd
   argv pinned in `build_opencode_argv`. **NEW IN WAVE 7.**

### Tier B — MCP-attached, manual-trigger only (1 harness post-wave-7)

1. **Windsurf** — Codeium's IDE. Vendor docs do **not** publish a standalone
   headless CLI as of 2026-05; only the IDE-embedded `Cmd/Ctrl+I` Cascade
   surface and third-party hacks (`wsc` AppleScript, `windsurfinabox` Docker)
   exist. Quorus registers the MCP server (`~/.codeium/windsurf/mcp_config.json`)
   so Windsurf can SEND messages via `quorus inbox`/`quorus say` MCP tools, but
   reflexd cannot wake the IDE to REPLY. Documented as tier-B in
   `mcp_writers.register_windsurf` docstring.

   _Cited:_ https://docs.windsurf.com/command/windsurf-overview — describes only
   IDE-embedded usage. https://github.com/staronelabs/windsurf-cli (`wsc`) — third
   party, AppleScript, macOS-only. Not Codeium-supported.

### Tier A — Newly verified (Cline)

**Cline** has a real standalone CLI (`npm install -g cline`, then `cline "<task>"`)
in **preview** as of 2026-05. macOS + Linux only; OAuth-based via `cline auth`.
This unlocks tier-A for Cline. Reflexd argv pinned in `build_cline_argv`.
**Verified:** https://docs.cline.bot/getting-started/installing-cline.

## Net result

**Wave 7 advances Cline + Opencode from MCP-only to fully proactive.**
Windsurf remains MCP-attached (vendor has not shipped a headless CLI). The
Quorus website hero changes from "4 fully proactive harnesses" to:

> "Quorus is fully proactive on 6 harnesses (Claude Code, Codex CLI, Gemini
> CLI, Cursor, Opencode, Cline) and MCP-attached on 1 (Windsurf)."

## Updating this doc

When a new harness ships a real headless mode (or an existing one breaks):

1. Re-run `WebSearch` against the vendor docs page.
2. Update the table above with the cited evidence.
3. If moving to tier A, add an argv builder + adapter dispatch in `reflexd.py`,
   add a suffix to `_HARNESS_SUFFIXES`, and add contract tests in
   `tests/test_reflexd_adapters.py`.
4. If dropping a harness to tier C, remove from `mcp_writers.ALL_WRITERS`,
   `_HARNESS_SUFFIXES`, and the website tabs.
