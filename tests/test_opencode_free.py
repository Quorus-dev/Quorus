"""The free opencode models come from the live catalog (release check, 2026-10-17):
every model in the old fixed tuple was rate-limited or renamed while
nemotron-3.5-lightning-free answered."""

from __future__ import annotations

import subprocess

from quorus.runtime import opencode_free

TUPLE = ("opencode/deepseek-v4.1-flash-free", "opencode/longcat-2.5-preview-free")
CATALOG = """ollama/qwen3:4b
opencode/big-pickle
opencode/longcat-2.5-preview-free
opencode/nemotron-3.5-lightning-free
opencode/glm-5.3-flash-free
"""


def _run(stdout: str = CATALOG, rc: int = 0, calls: list | None = None):
    def run(argv, **_kw):
        if calls is not None:
            calls.append(argv)
        return subprocess.CompletedProcess(argv, rc, stdout=stdout, stderr="")
    return run


def test_live_free_models_known_first_then_the_rest() -> None:
    models = opencode_free.free_models(TUPLE, run=_run())
    # deepseek left the catalog; longcat (known-good) leads; local and non-free ones never appear
    assert models == ("opencode/longcat-2.5-preview-free",
                      "opencode/nemotron-3.5-lightning-free", "opencode/glm-5.3-flash-free")


def test_cached_for_an_hour() -> None:
    calls: list = []
    t = [1000.0]
    opencode_free.free_models(TUPLE, run=_run(calls=calls), now=lambda: t[0])
    opencode_free.free_models(TUPLE, run=_run(calls=calls), now=lambda: t[0])
    assert len(calls) == 1
    t[0] += opencode_free.CACHE_S + 1
    opencode_free.free_models(TUPLE, run=_run(calls=calls), now=lambda: t[0])
    assert len(calls) == 2


def test_falls_back_to_the_tuple_when_the_catalog_is_unavailable() -> None:
    assert opencode_free.free_models(TUPLE, run=_run(rc=1)) == TUPLE
    opencode_free.reset_cache()
    assert opencode_free.free_models(TUPLE, run=_run(stdout="ollama/qwen3:4b\n")) == TUPLE
    opencode_free.reset_cache()

    def missing(argv, **_kw):
        raise FileNotFoundError("opencode")
    assert opencode_free.free_models(TUPLE, run=missing) == TUPLE


def test_recently_slow_models_go_last_until_they_recover() -> None:
    t = [5000.0]
    models = opencode_free.free_models(TUPLE, run=_run(), now=lambda: t[0])
    opencode_free.mark_slow(models[0], now=lambda: t[0])
    again = opencode_free.free_models(TUPLE, run=_run(), now=lambda: t[0])
    assert again[-1] == models[0] and again[:-1] == models[1:]
    t[0] += opencode_free.SLOW_FOR_S + 1
    assert opencode_free.free_models(TUPLE, run=_run(), now=lambda: t[0])[0] == models[0]
