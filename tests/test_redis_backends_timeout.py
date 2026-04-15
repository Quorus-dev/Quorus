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
