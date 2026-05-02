"""Tests for Redis operation timeouts (_with_timeout + RedisOperationTimeout)."""
from __future__ import annotations

import asyncio
import importlib

import pytest

from quorus.backends import redis_backends
from quorus.backends.redis_backends import (
    REDIS_OP_TIMEOUT_SECONDS,
    BackendError,
    RedisOperationTimeout,
    _with_timeout,
)


async def _sleep_forever() -> None:
    await asyncio.sleep(10_000)


async def _fast() -> str:
    return "ok"


@pytest.mark.asyncio
async def test_with_timeout_raises_on_slow_coro(monkeypatch):
    monkeypatch.setattr(redis_backends, "REDIS_OP_TIMEOUT_SECONDS", 0.05)
    with pytest.raises(RedisOperationTimeout):
        await _with_timeout(_sleep_forever())


@pytest.mark.asyncio
async def test_with_timeout_passthrough_on_fast_coro():
    assert await _with_timeout(_fast()) == "ok"


def test_redis_operation_timeout_is_backend_error():
    assert issubclass(RedisOperationTimeout, BackendError)


def test_env_var_configures_timeout(monkeypatch):
    monkeypatch.setenv("QUORUS_REDIS_OP_TIMEOUT", "3.5")
    reloaded = importlib.reload(redis_backends)
    assert reloaded.REDIS_OP_TIMEOUT_SECONDS == 3.5


def test_default_timeout_is_positive():
    assert REDIS_OP_TIMEOUT_SECONDS > 0


def test_every_redis_await_is_timeout_wrapped():
    """Regression guard: every ``await self._r.<op>`` and pipeline
    ``execute`` call in :mod:`quorus.backends.redis_backends` must be
    wrapped by :func:`_with_timeout` — otherwise a hung Redis would block
    the event loop indefinitely instead of raising
    :class:`RedisOperationTimeout`.
    """
    import inspect
    import re

    source = inspect.getsource(redis_backends)
    # Strip out comments + docstrings to keep grep cheap and avoid false
    # positives in the explanatory text at the top of the file.
    code_lines = []
    for line in source.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        code_lines.append(line)
    code = "\n".join(code_lines)

    # ``await self._r.<call>(...)`` not preceded by ``_with_timeout(`` on
    # the same line is a smoking gun for an unwrapped Redis call.
    pattern = re.compile(r"await\s+self\._r\.[a-zA-Z_]+\(")
    for match in pattern.finditer(code):
        # Find the start of the line containing this match.
        line_start = code.rfind("\n", 0, match.start()) + 1
        line_end = code.find("\n", match.end())
        line = code[line_start:line_end if line_end != -1 else None]
        if "_with_timeout(" in line:
            continue
        # Pipeline construction lines are not network calls — they only
        # buffer commands. Their `await pipe.execute()` IS wrapped.
        if "self._r.pipeline(" in line:
            continue
        raise AssertionError(
            f"unwrapped Redis call (not via _with_timeout): {line.strip()!r}"
        )

    # And every ``await pipe.execute()`` must also be wrapped.
    for match in re.finditer(r"await\s+pipe\.execute\(", code):
        line_start = code.rfind("\n", 0, match.start()) + 1
        line_end = code.find("\n", match.end())
        line = code[line_start:line_end if line_end != -1 else None]
        assert "_with_timeout(" in line, (
            f"unwrapped pipe.execute(): {line.strip()!r}"
        )
