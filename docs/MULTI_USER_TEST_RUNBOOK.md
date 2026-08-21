# Multi-User Test Runbook — Arav + Aarya

> Written 2026-08-20 for tomorrow's first two-human autonomous-agent test.
> Everything below assumes the `feat/may4-sprint` branch at `~/dev/Quorus`.

## Step 0 — pick a relay path (one required)

**Path A — Fly (public relay, 10 min):**
1. In Arav's Claude Code session type: `! flyctl auth login` (browser opens; log in).
2. Tell Claude "deploy the relay" — it runs `flyctl launch/deploy` from the repo
   (`fly.toml` is ready), sets secrets (JWT_SECRET, DATABASE_URL optional for
   file mode, RELAY_SECRET), and verifies `/health`.
3. Both machines use `--relay-url https://<app>.fly.dev`.

**Path B — Tailscale (no cloud, 5 min):**
1. Start Tailscale on BOTH machines (menu bar icon → connect; Aarya installs
   from tailscale.com and joins Arav's tailnet via invite).
2. On Arav's Mac: `cd ~/dev/Quorus && .venv/bin/quorus relay` (note the port,
   default 8080). Find the tailnet IP: `tailscale ip -4` (e.g. 100.x.y.z).
3. Both use `--relay-url http://100.x.y.z:8080`.

## Step 1 — each human sets up (2 min each)

```bash
pipx install "quorus @ git+https://github.com/Quorus-dev/Quorus.git@feat/may4-sprint"
quorus init <yourname> --relay-url <URL from step 0> --secret <shared secret>
# reflexd-manager auto-starts your agents (<yourname>-claude etc.)
quorus doctor        # all checks green?
quorus               # opens the hub
```

Requirements per machine: Claude Code installed + `claude /login` done
(the agent replies use YOUR Claude login — no API key).

## Step 2 — the test script (in the shared room)

Create/join one room (e.g. `quorus create test-day` / `quorus join test-day`).

| # | Test | Do | PASS looks like |
|---|------|----|--------------------|
| 1 | Wake-on-mention | Arav types `@aarya-claude what files are in your home dir?` | Aarya's agent replies **by itself** within ~2 min. Aarya touches nothing. |
| 2 | Reverse | Aarya mentions `@arav-claude` | Same, other direction. |
| 3 | Offline drain | Aarya **quits reflexd / closes laptop**. Arav mentions her agent. Aarya reopens. | Agent answers the missed mention on reconnect, no prompting. |
| 4 | Broadcast auction | Either types `@open summarize this room` | **Exactly one** agent answers. The other stays silent. |
| 5 | Presence honesty | While Aarya's laptop closed, Arav checks the room | Aarya's agent shows away + queued count (TUI/`GET /rooms/{id}`). |
| 6 | Human stop | While an agent is mid-reply chatter, send `/interrupt` | Agent acknowledges stop. |

Record: reply latencies, any manual prompting needed (should be ZERO),
any spurious "[reflexd] harness timed out" after a good reply (known issue
D7, fix in flight).

## Known limits going into this test (set expectations)

- Woken agents **chat well but work amnesiac** — no repo binding / session
  memory yet (Stream D lands next; after that they resume missions).
- Permission-heavy tasks may stall silently (Stream L approval relay pending).
- One relay instance only (auction state is process-local until R3).
