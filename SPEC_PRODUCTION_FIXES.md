# Quorus Production Fixes — Implementation Spec

This document is the authoritative spec for three parallel work streams addressing
production-readiness findings. Each stream has a **scope**, **required changes**,
**tests to add/update**, and **acceptance criteria**. The judge agent uses the
acceptance criteria to verify.

Ground rules for all streams:
- Python 3.10+, async-first, conventional commits (imperative, <50 chars).
- Files stay under 500 lines. If a file would exceed that, split it.
- All new code has unit tests. Use `pytest -v`.
- Lint must pass: `ruff check .`.
- Do not log secrets. Do not introduce new global mutable state.
- Stay strictly within your stream's file list. If something outside your scope
  needs to change, leave a TODO comment referencing the other stream.

---

## Stream A — Reliability & Security

### Files in scope
- `quorus/backends/redis_backends.py`
- `quorus/services/message_svc.py`
- `quorus/services/webhook_svc.py`
- `quorus/services/rate_limit_svc.py` (if it exists; otherwise the rate-limit code in `quorus/relay.py`)
- `quorus/auth/routes.py`
- `quorus/auth/middleware.py`
- `quorus/relay.py` (ONLY the 404 rate-limit section and the signup rate-limit
  wiring — do not touch service initialization)
- Tests under `tests/`

### Required changes

**A1. Redis operation timeouts** (`quorus/backends/redis_backends.py`)
- Add a module-level constant `REDIS_OP_TIMEOUT_SECONDS = float(os.getenv("QUORUS_REDIS_OP_TIMEOUT", "10"))`.
- Wrap every outbound Redis call (including `xadd`, `xread`, `xreadgroup`,
  `eval`, `pipeline.execute`, `get`, `set`, `del`, etc.) with
  `asyncio.wait_for(..., timeout=REDIS_OP_TIMEOUT_SECONDS)`.
- On timeout, raise a new typed exception `RedisOperationTimeout(BackendError)`.
- Add a helper `_with_timeout(coro)` to avoid repetition.

**A2. Narrow the broad exception in MessageService**
- In `quorus/services/message_svc.py` around the idempotency retry path
  (previous line ~200 — search for `idempotency_cache_key` + `except Exception`),
  replace `except Exception` with `except (HTTPException, BackendError)`.
- Wrap the `await backends.idempotency.delete(...)` cleanup in its own
  `try/except` that logs and swallows — cleanup failure must not mask the
  original exception.

**A3. Webhook failure logging** (`quorus/services/webhook_svc.py`)
- Every failed delivery must log at `WARNING` with fields: `tenant_id`,
  `webhook_url` (host only — strip path + query), `status_code` (if any),
  `attempt`, `error_type`. Never log the request body or secrets.

**A4. Scrub API keys from signup response** (`quorus/auth/routes.py`)
- The signup endpoint currently returns the raw API key / `setup_command`
  containing the key. Change the response shape to:
  ```json
  { "key_prefix": "quorus_sk_abcd", "key_id": "...", "next_steps": "Copy the key printed by the CLI now — it will not be shown again." }
  ```
- The CLI (Stream C) is **not** updated here; Stream A must keep a
  backwards-compatible field `setup_command` ONLY when the request includes
  header `X-Quorus-Setup-Local: 1`. Document the header in a comment.
- Ensure the full key is still printed exactly once to the server log at
  `INFO` with redaction (prefix only), and returned once in the HTTP response
  body only when the local-setup header is present.

**A5. Constant-time JWT comparison** (`quorus/auth/middleware.py`)
- Audit every `==` or `!=` used on secrets, signatures, or HMACs. Replace with
  `hmac.compare_digest(...)`. This includes JWT signature verification if it
  currently uses `==`.

**A6. Signup rate limiting**
- Add per-IP rate limit on `/v1/auth/signup`: **5 requests per 60 seconds**.
- If a Redis backend is available, use it (shared across replicas). Otherwise
  fall back to the existing in-memory limiter.
- 429 response body: `{"error": "rate_limited", "retry_after": <seconds>}`.
- Include `Retry-After` header.

**A7. 404 spam limiter uses Redis when available**
- The `_not_found_counts` dict in `quorus/relay.py` (around line 526) is
  per-replica. Refactor to call `RateLimitService.check()` (or equivalent)
  so when Redis is configured it is shared across replicas. Leave the
  in-memory fallback for `in_memory` mode.

### Tests
- `tests/test_redis_backends_timeout.py` — use a mocked Redis client that
  sleeps longer than the timeout; verify `RedisOperationTimeout` is raised.
- `tests/test_auth_signup.py` — assert the default response does NOT include
  the raw key; assert header-gated response does.
- `tests/test_auth_signup_rate_limit.py` — fire 6 signups from same IP, assert
  the 6th gets 429 + Retry-After.
- `tests/test_webhook_logging.py` — monkeypatch logger, trigger a 500 from a
  fake webhook target, assert warning logged with expected fields.
- `tests/test_auth_middleware_timing.py` — smoke test that JWT verification
  uses `hmac.compare_digest` (assert by grep/ast scan or mock).

### Acceptance criteria
- `pytest -v` passes with all new tests green.
- `ruff check .` passes.
- `grep -n "== " quorus/auth/middleware.py` shows no secret comparisons.
- Signup response (no special header) contains no field matching
  `quorus_sk_[A-Za-z0-9]{20,}`.
- Redis timeout constant is tunable via `QUORUS_REDIS_OP_TIMEOUT`.
- No file grew past 500 lines.

---

## Stream B — MCP Resilience

### Files in scope
- `packages/mcp/quorus_mcp/server.py` (the ONLY file this stream modifies)
- Tests under `tests/`

### Required changes

**B1. SSE reconnect circuit breaker**
- Locate `_sse_listener()` around line 339. It currently retries forever with
  capped backoff.
- Add a circuit breaker:
  - After **10 consecutive failures**, log at `ERROR` and set
    `_sse_breaker_tripped = True`.
  - While tripped, skip SSE entirely and rely on polling (the existing
    `poll_mode` lazy path — but see B2 — polling will remain, just not called
    "lazy").
  - Attempt a probe reconnect every **60 seconds**; on success, reset the
    failure counter and clear the breaker.
  - Expose `_sse_breaker_state()` returning `{"tripped": bool, "failures": int, "last_error": str|None}` for diagnostics.
- Max backoff must be **30 seconds** (existing cap — keep it).

**B2. Remove dead `poll_mode="lazy"` path**
- Search for `poll_mode` in the file. There are two modes today: `sse` and
  `lazy`. All deployments use `sse`. Remove the `lazy` branch entirely —
  but keep a simple polling fallback that activates ONLY when the circuit
  breaker from B1 is tripped. Rename the config flag / internal constants to
  make this explicit: `SSE_ENABLED` (bool, default True) and fallback polling
  is automatic, not user-selectable.

**B3. Session capture race fix**
- `_active_session` (line 84) is read in tool handlers without holding
  `_active_session_lock`. Audit all reads and writes; every access must go
  through an `async with _active_session_lock:` block, OR wrap access in a
  small helper `_get_active_session()` / `_set_active_session()` that takes
  the lock internally. Prefer the helper approach.

### Tests
- `tests/test_mcp_sse_breaker.py` — simulate 10 consecutive SSE failures,
  assert breaker trips; simulate a successful probe, assert it clears.
- `tests/test_mcp_session_race.py` — launch N concurrent coroutines that
  call `_set_active_session` + `_get_active_session`; assert no
  `RuntimeError` / torn reads.

### Acceptance criteria
- `pytest -v tests/test_mcp_*` passes.
- `ruff check packages/mcp/quorus_mcp/server.py` passes.
- `grep -n '"lazy"' packages/mcp/quorus_mcp/server.py` returns nothing.
- File stays under 500 lines after changes (if it would grow past, extract a
  new `packages/mcp/quorus_mcp/sse.py` module).

---

## Stream C — Config Consolidation

### Files in scope
- `quorus/config.py`
- `packages/cli/quorus_cli/cli.py` (only the config-loading helpers
  `_load_config`, `_write_config`, and the legacy path resolution at top of
  file — roughly lines 48–130. Do not refactor commands.)
- `packages/tui/quorus_tui/hub.py` (only `_load_config`/`_write_config` and
  related helpers, lines 98–129)
- `packages/sdk/` (any config-loading code)
- `quorus/watcher.py` (delete — it's an unused shim)
- Tests under `tests/`

### Required changes

**C1. Single source of truth for config path resolution**
- In `quorus/config.py`, add:
  ```python
  def resolve_config_dir() -> Path: ...
  def resolve_config_file() -> Path: ...
  ```
  Priority order (top wins):
  1. `QUORUS_CONFIG_DIR` env var
  2. `MCP_TUNNEL_CONFIG_DIR` env var (legacy)
  3. `~/.quorus/`
  4. `~/.murmur/` (legacy read-only; log deprecation warning if found)
  5. `~/mcp-tunnel/` (legacy read-only; log deprecation warning if found)
- Writes **only ever** go to priority 1, 2, or 3 (never to legacy paths).
- Add `ConfigManager` class with `load() -> dict`, `save(data: dict) -> None`,
  `path -> Path` property. Both CLI and TUI use this.

**C2. Remove duplicate loaders**
- Delete `_load_config` / `_write_config` from `packages/cli/quorus_cli/cli.py`
  and `packages/tui/quorus_tui/hub.py`. Replace call sites with
  `ConfigManager` usage.
- If the SDK has its own loader, point it at `quorus.config.ConfigManager`.

**C3. Delete the watcher shim**
- `quorus/watcher.py` has no importers. Verify with
  `grep -rn "from quorus.watcher" .` and `grep -rn "import quorus.watcher" .`.
- If confirmed unused, delete the file and any `__init__.py` re-export
  (`relay.py:688` previously re-exported it — remove that line).

**C4. Legacy path handling**
- Reading from legacy dirs must emit a single deprecation warning per
  process start (use a module-level `_warned_paths: set[Path]`).
- Writes to legacy dirs are prohibited. If a caller somehow passes a legacy
  path explicitly, raise `ValueError`.

### Tests
- `tests/test_config_resolution.py`:
  - With only `QUORUS_CONFIG_DIR` set → uses it.
  - With only legacy env var → uses it + warns.
  - With `~/.quorus/` present → uses it.
  - With only `~/.murmur/` → uses it + warns.
  - Writes always go to the winning modern path.
- `tests/test_config_manager.py` — round-trip load/save.

### Acceptance criteria
- `pytest -v` passes with new config tests.
- `ruff check .` passes.
- `grep -rn "def _load_config" packages/cli packages/tui` returns nothing.
- `grep -rn "from quorus.watcher" .` returns nothing.
- `quorus/watcher.py` does not exist.
- Config writes never land in `~/.murmur/` or `~/mcp-tunnel/` (regression
  test asserts this).

---

## Judge verification checklist

For each stream, the judge must:

1. Confirm the scope was respected (no files outside the declared list were
   modified — `git diff --name-only main...HEAD`).
2. Run `pytest -v` in each worktree — report pass/fail counts + any new
   flakes.
3. Run `ruff check .` — report violations.
4. Grep the acceptance-criteria assertions above and report pass/fail.
5. Read each stream's diff and flag: logic bugs, logging of secrets, new
   global mutable state, functions over 80 lines, files over 500 lines.
6. **Cross-stream merge check**: dry-merge all three branches into a single
   integration branch. Report conflicts and any runtime integration risks
   (interface drift between config loader and its callers, etc.).
7. Produce a go/no-go verdict per stream and an overall verdict.
