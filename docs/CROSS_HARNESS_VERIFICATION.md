# Cross-Harness Verification Matrix

**Last verified:** 2026-05-03  
**Branch:** `feat/may4-sprint`  
**Host:** macOS (Darwin 25.3.0), Python 3.14.4

This is the ground-truth verification status for the 6 vendor harnesses
Quorus ships adapters for. Notification verification (column 5) is the
**desktop banner** path described in `quorus/notifications/native.py` —
unit-tested in `tests/test_notification_dispatch.py` and `tests/test_notifications_native.py`.

Per-cell semantics:

- **Real-LLM PASS** — `tests/e2e/test_real_harness_e2e.py` spawns the binary
  and gets a sane reply within the latency budget. Single-trial.
- **Notification PASS** — `tests/test_notification_dispatch.py` proves the
  envelope→notify call shape is correct for that vendor. The OS-layer
  (osascript / notify-send) is covered separately in `test_notifications_native.py`.
- **p50 latency** — single-trial wall clock. Treat as a p99 proxy until CI
  runs N trials and emits a real percentile.
- **TBD** — binary not on this host or auth missing; argv contract is correct
  per vendor docs but cold path is unverified.

| Harness     | Binary         | Version | Real-LLM PASS       | Notification PASS | p50 latency | Last verified |
| ----------- | -------------- | ------- | ------------------- | ----------------- | ----------- | ------------- |
| Claude Code | `claude`       | 2.1.132 | yes                 | yes               | 14.28s      | 2026-05-03    |
| Codex CLI   | `codex`        | 0.128.0 | yes                 | yes               | 5.16s       | 2026-05-03    |
| Gemini CLI  | `gemini`       | 0.40.1  | yes                 | yes               | 7.49s       | 2026-05-03    |
| Opencode    | `opencode`     | 1.14.33 | yes                 | yes               | 5.68s       | 2026-05-03    |
| Cline       | `cline`        | 2.18.0  | TBD (no auth)       | yes               | n/a         | 2026-05-03    |
| Cursor      | `cursor-agent` | n/a     | TBD (not installed) | yes               | n/a         | 2026-05-03    |

## Notification dispatch verification (per OS layer)

| OS layer | Mechanism                                    | Status         | Evidence                                                                                                                 |
| -------- | -------------------------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| macOS    | `osascript` argv with `display notification` | PASS           | `test_notifications_native.py::test_macos_uses_osascript_argv` + 14 sibling tests                                        |
| Linux    | `notify-send` argv                           | PASS (mocked)  | `test_notifications_native.py::test_linux_uses_notify_send_argv`. Live notify-send not exercised on this macOS host.     |
| Windows  | (fallback log only)                          | PASS by design | `test_notifications_native.py::test_windows_logs_to_file`. No native toast implementation; log-only fallback documented. |

## Honest verdict

**Fully verified (real LLM + notification dispatch shape):**

- Claude Code, Codex CLI, Gemini CLI, Opencode

**Argv-correct but unverified end-to-end:**

- Cline (binary present, auth missing — `cline auth` would unblock)
- Cursor (binary not on PATH for this run)

**Broken:** none.

The notification dispatch contract test (`tests/test_notification_dispatch.py`)
proves the envelope shape Quorus passes to `notify()` is identical for all
6 vendors — that path is independent of whether the LLM actually replies,
so it is verified for all 6.

## How to regenerate this matrix

```bash
QUORUS_HARNESS_LATENCY_FILE=/tmp/quorus-harness-latency.json \
  python -m pytest -m real_harness -v -s
cat /tmp/quorus-harness-latency.json   # transcribe the values into the table
```

For the cross-harness aggregate (also writes to the same sidecar):

```bash
QUORUS_HARNESS_LATENCY_FILE=/tmp/quorus-harness-latency.json \
  python -m pytest -m cross_harness -v -s
```
