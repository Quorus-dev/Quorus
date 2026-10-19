"""Relay memory without Redis, beyond tests/test_p1_sqlite_persistence.py.

Memory slice 1 keeps PersistentMemorySvc entries in p1_state.db when there is no
Redis. These cover what that file doesn't: buckets and tenants stay apart on
disk, the entry cap counts what a restart loads, and a tenant reset leaves other
tenants' rows. A "restart" is a fresh service over the same file.
"""

from __future__ import annotations

import pytest

from quorus.services import p1_persistence, p1_sqlite
from quorus.services import persistent_memory_svc as mod
from quorus.services.persistent_memory_svc import PersistentMemorySvc


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    monkeypatch.setattr(p1_persistence, "get_redis_or_none", lambda: None)
    p1_sqlite._broken.clear()
    yield
    p1_sqlite._broken.clear()


async def test_buckets_stay_apart_on_disk() -> None:
    svc = PersistentMemorySvc()
    await svc.set("t", "alice", "r1", "k", "alice-r1")
    await svc.set("t", "bob", "r1", "k", "bob-r1")
    await svc.set("t", "alice", "r2", "k", "alice-r2")
    await svc.set("other", "alice", "r1", "k", "other-tenant")

    after = PersistentMemorySvc()
    assert (await after.get("t", "alice", "r1", "k"))["value"] == "alice-r1"
    assert (await after.get("t", "bob", "r1", "k"))["value"] == "bob-r1"
    assert (await after.get("t", "alice", "r2", "k"))["value"] == "alice-r2"
    assert (await after.get("other", "alice", "r1", "k"))["value"] == "other-tenant"


async def test_the_entry_cap_counts_what_is_on_disk(monkeypatch) -> None:
    monkeypatch.setattr(mod, "MAX_ENTRIES", 2)
    before = PersistentMemorySvc()
    await before.set("t", "alice", "r1", "a", 1)
    await before.set("t", "alice", "r1", "b", 2)
    with pytest.raises(mod.EntryCapError):
        await PersistentMemorySvc().set("t", "alice", "r1", "c", 3)


async def test_reset_of_one_tenant_keeps_the_others() -> None:
    svc = PersistentMemorySvc()
    await svc.set("t", "alice", "r1", "k", 1)
    await svc.set("keep", "alice", "r1", "k", 2)
    await svc.reset("t")

    after = PersistentMemorySvc()
    assert await after.get("t", "alice", "r1", "k") is None
    assert (await after.get("keep", "alice", "r1", "k"))["value"] == 2
