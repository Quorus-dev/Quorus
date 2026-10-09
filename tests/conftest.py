"""Set required environment variables before any test module imports."""

import os
import re
import sys

import pytest

os.environ.setdefault("RELAY_SECRET", "test-secret")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long")
os.environ.setdefault("BOOTSTRAP_SECRET", "test-bootstrap-secret")


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
