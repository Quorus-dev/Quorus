"""Set required environment variables before any test module imports."""

import os
import re
import sys

import pytest

os.environ.setdefault("RELAY_SECRET", "test-secret")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long")
os.environ.setdefault("BOOTSTRAP_SECRET", "test-bootstrap-secret")
# No test may put a banner on the real desktop (a firstrun run did, 2026-10-19):
# forced, not defaulted, because a woken agent's own env may say 1. Spawned
# relays, daemons and pty sessions inherit it. Tests of the dispatch itself
# set it back to 1 and mock subprocess.
os.environ["QUORUS_NOTIFY"] = "0"

# An agent woken by reflexd runs the suite with its live identity in the
# environment (QUORUS_RELAY_URL etc.), and config tests then resolve the real
# relay instead of their defaults, and the daemon's REFLEXD_* settings (e.g.
# REFLEXD_CODEX_SANDBOX) change the argv tests pin (found 2026-10-17). Tests
# that need these set them with monkeypatch.
AMBIENT_IDENTITY_VARS = (
    "QUORUS_API_KEY", "QUORUS_RELAY_SECRET", "QUORUS_CONFIG_DIR", "QUORUS_RELAY_URL",
    "QUORUS_INSTANCE_NAME", "QUORUS_PROFILE", "QUORUS_PARTICIPANT", "QUORUS_APPROVAL_ROOM",
)
for _var in [*AMBIENT_IDENTITY_VARS, *(v for v in os.environ if v.startswith("REFLEXD_"))]:
    os.environ.pop(_var, None)


def _preimport_patch_targets() -> None:
    """Python 3.10's ``unittest.mock`` resolves ``patch("pkg.sub.attr")`` with
    plain getattr and does NOT import a submodule that isn't loaded yet (3.11+
    does), so on 3.10 such patches died with "module 'quorus' has no attribute
    'sdk'" depending on test order. Import every module named in a patch
    target once, up front. Failures are ignored: the test reports them."""
    import importlib
    import pathlib

    pattern = re.compile(r"""patch(?:\.object)?\(\s*["']((?:quorus|quorus_[a-z]+)[\w.]*)["']""")
    targets: set[str] = set()
    for f in pathlib.Path(__file__).parent.glob("**/*.py"):
        targets.update(pattern.findall(f.read_text(encoding="utf-8", errors="replace")))
    for target in sorted(targets):
        parts = target.split(".")
        for i in range(len(parts), 0, -1):
            name = ".".join(parts[:i])
            try:
                importlib.import_module(name)
                break
            except BaseException:  # noqa: B036 — includes the relay's import-time SystemExit
                # e.g. quorus.relay exits when DATABASE_URL is set without
                # REDIS_URL (Postgres CI job): leave it for the test to import
                # with its own env, and never cache a half-imported module.
                sys.modules.pop(name, None)
                continue


_preimport_patch_targets()


def _reset_process_global_state() -> None:
    """Clear module-global state that survives ``reset_state()``.

    ``quorus.relay.reset_state`` re-creates services on ``app.state``, but
    process-global module state is not covered by it:

    * ``relay._not_found_counts`` / ``relay._blocked_ips`` — the 404 sweeper.
      All ASGI test clients share one client IP, so 404s issued anywhere in
      the suite accumulate; 30 within a 60s wall-clock window block the shared
      IP and every later request 429s for 300s (the observed cross-file
      failures in test_triage / test_usage / test_work_queue).
    * ``quorus.routes.triage`` bid windows / fairness credit.

    Modules are looked up in ``sys.modules`` rather than imported so that
    tests which never touch the relay do not pay for (or change the timing
    of) its import.
    """
    relay = sys.modules.get("quorus.relay")
    if relay is not None:
        relay.reset_not_found_limiter()
    triage = sys.modules.get("quorus.routes.triage")
    if triage is not None:
        triage.reset_triage_state()
    # The approve bridge stops a session after its first denial; one MCP
    # process is one agent session in production, but tests share a process.
    p1 = sys.modules.get("quorus_mcp.phase1_tools")
    if p1 is not None and hasattr(p1, "_STOPPED"):
        p1._STOPPED = None


@pytest.fixture(autouse=True)
def _clean_process_global_state():
    """Autouse guard against cross-test pollution of process-global state."""
    _reset_process_global_state()
    yield
    _reset_process_global_state()


@pytest.fixture(autouse=True)
def _isolate_reflexd_runtime(request, tmp_path):
    """Keep reflexd's on-disk state out of the real ``~/.quorus/runtime``.

    Test daemons default to ``DEFAULT_RUNTIME_DIR`` and their participant is
    usually ``arav-claude`` — the same name as a real local agent. Since the
    daemon persists handled ids and writes the agent's private config dir,
    tests were overwriting a live agent's credentials with fixtures and
    leaking handled ids between runs (found 2026-10-08).
    """
    import sys as _sys

    runtime = tmp_path / "reflexd-runtime"
    # Each test file loads its OWN copy of scripts/reflexd.py by path, so
    # patch the copy the test module holds as well as sys.modules["reflexd"].
    # Manual save/restore, NOT the monkeypatch fixture: requesting it here
    # would make the shared monkeypatch outlive per-module autouse fixtures,
    # so their teardown would still see the test's own patches.
    candidates = {id(m): m for m in (_sys.modules.get("reflexd"),
                                     getattr(request.module, "reflexd", None))
                  if m is not None and hasattr(m, "DEFAULT_RUNTIME_DIR")}
    # ROOM_BINDINGS_PATH too: the daemon's startup publish sweep reads it,
    # and with the real file a test daemon named arav-claude would rebase and
    # publish the live agent's real worktree (caught 2026-10-08).
    attrs = {"DEFAULT_RUNTIME_DIR": runtime,
             "ROOM_BINDINGS_PATH": tmp_path / "room-bindings.json"}
    saved = {(k, a): getattr(m, a) for k, m in candidates.items() for a in attrs
             if hasattr(m, a)}
    for (k, a) in saved:
        setattr(candidates[k], a, attrs[a])
    yield
    for (k, a), value in saved.items():
        setattr(candidates[k], a, value)


@pytest.fixture(autouse=True)
def _os_environ_snapshot():
    """Restore ``os.environ`` after every test.

    Some code under test mutates the process environment directly — e.g.
    ``run_claude_agent`` / ``run_codex_agent`` / ``run_gemini_agent`` call
    ``os.environ.setdefault("QUORUS_CONFIG_DIR", <tmpdir>)`` for per-agent
    config isolation (harmless in production, where the process exits after
    the run). In-process, that leaks into every later test: config-resolution
    tests then resolve the agent's throwaway dir instead of the one they set
    up. Snapshotting the environment kills this entire class of pollution.
    """
    saved = os.environ.copy()
    yield
    for key in set(os.environ) - set(saved):
        del os.environ[key]
    for key, value in saved.items():
        if os.environ.get(key) != value:
            os.environ[key] = value


@pytest.fixture(autouse=True)
def _relay_state_in_tmp(tmp_path):
    """The relay's claims store and invites live in a per-test folder, never in
    the developer's real ~/.quorus (mint and redeem now open it on first use)."""
    if not (os.environ.get("QUORUS_HISTORY_DB") or os.environ.get("MESSAGES_FILE")):
        os.environ["QUORUS_HISTORY_DB"] = str(tmp_path / "relay-state" / "history.db")
    relay = sys.modules.get("quorus.relay")
    if relay is not None:
        relay.app.state.participant_keys = None
    yield


@pytest.fixture(autouse=True)
def _agent_memory_in_tmp(tmp_path):
    """Agents' private memory (quorus/runtime/memory.py) defaults to
    ~/.quorus/memory. Daemon tests that post a reply append to it, and they were
    writing test rows into the real agents' files (found 2026-10-19: 23 fake
    rows for arav-gemini). An explicit base_dir still wins."""
    from quorus.runtime import memory as mem

    real = mem.memory_dir
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(mem, "memory_dir",
                   lambda *, base_dir=None: real(base_dir=base_dir or tmp_path / "agent-memory"))
        yield


_OPT_IN_MARKERS = {"real_harness", "cross_harness"}


def _marker_explicitly_selected(markexpr: str, marker: str) -> bool:
    """Return True when the user deliberately selected an opt-in marker."""
    if re.search(rf"\bnot\s+{re.escape(marker)}\b", markexpr):
        return False
    return marker in set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", markexpr))


def pytest_collection_modifyitems(config, items):
    """Skip live vendor-harness tests unless their marker was explicitly selected.

    Marker registration only documents tests; it does not stop default
    ``pytest`` from running them. These E2E tests spawn real LLM CLIs and depend
    on host auth/config, so default verification must skip them.
    """
    markexpr = config.option.markexpr or ""
    for item in items:
        for marker in _OPT_IN_MARKERS:
            if item.get_closest_marker(marker) is None:
                continue
            if _marker_explicitly_selected(markexpr, marker):
                continue
            item.add_marker(
                pytest.mark.skip(reason=f"opt-in: run with -m {marker}")
            )


@pytest.fixture(autouse=True)
def _no_live_opencode_catalog():
    """Tests never shell out to a real `opencode models`: unless a test passes
    its own `run`, the catalog looks unavailable and the pinned tuple is used.

    Deliberately not the `monkeypatch` fixture: a conftest autouse fixture that
    requests it creates the test's `monkeypatch` first, so its undo runs AFTER
    every module's own autouse teardown. test_triage_redis's clean_state then
    tore down with the test's sentinel Redis still patched in (overseer, full
    suite on 0c2cbaf7). A private MonkeyPatch keeps the tests' one untouched.
    """
    import subprocess

    from quorus.runtime import opencode_free

    opencode_free.reset_cache()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(opencode_free, "_default_run", lambda argv, **_kw:
                   subprocess.CompletedProcess(argv, 1, stdout="", stderr=""))
        yield
    opencode_free.reset_cache()


@pytest.fixture(autouse=True, scope="session")
def _the_real_home_config_is_never_touched():
    """A test rewrote the overseer's real ``~/.quorus/config.json`` (2026-10-20:
    the hub resolved its config path at import time, so a fixture's
    ``QUORUS_CONFIG_DIR`` came too late). Snapshot the real config files before
    the session and fail loudly at the end if any changed. The real HOME is
    captured here, before any test monkeypatches ``HOME``."""
    import os as _os
    from pathlib import Path as _Path

    real = _Path(_os.path.expanduser("~")) / ".quorus"

    def snapshot() -> dict[str, tuple[int, int]]:
        files = [real / "config.json", *sorted((real / "profiles").glob("*.json"))]
        out: dict[str, tuple[int, int]] = {}
        for f in files:
            try:
                st = f.stat()
            except FileNotFoundError:
                continue
            out[str(f)] = (st.st_mtime_ns, st.st_size)
        return out

    before = snapshot()
    yield
    after = snapshot()
    if before != after:
        changed = sorted(set(before) ^ set(after)
                         | {k for k in before if k in after and before[k] != after[k]})
        pytest.fail("a test touched the real ~/.quorus config: " + ", ".join(changed)
                    + " (isolate HOME and QUORUS_CONFIG_DIR in that test)")
