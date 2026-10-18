"""Memory slice 1: with no Redis, persistent memory, capabilities and room
tool catalogs survive a relay restart, in a local SQLite file.

Before, ``p1_persistence`` was a no-op without Redis, so "persistent" memory
lasted only as long as the process. A restart here is a new service instance
reading the same file, which is what a new relay process does.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import time

import pytest

from quorus.services import p1_persistence, p1_sqlite
from quorus.services.capability_svc import CapabilitySvc
from quorus.services.persistent_memory_svc import PersistentMemorySvc
from quorus.services.tool_catalog_svc import ToolCatalogSvc

T, R = "t-1", "room-1"


@pytest.fixture(autouse=True)
def _no_redis_fresh_store(monkeypatch, tmp_path):
    monkeypatch.setattr(p1_persistence, "get_redis_or_none", lambda: None)
    # Pin the store to this test's tmp_path, whatever the environment says:
    # these tests must never touch a real ~/.quorus/p1_state.db.
    monkeypatch.delenv("MESSAGES_FILE", raising=False)
    monkeypatch.setenv("QUORUS_HISTORY_DB", str(tmp_path / "relay" / "history.db"))
    assert p1_sqlite.db_path().is_relative_to(tmp_path)
    p1_sqlite._broken.clear()
    yield
    p1_sqlite._broken.clear()


async def test_memory_survives_a_restart_with_owner_and_visibility_intact():
    svc = PersistentMemorySvc()
    private = await svc.set(T, "alice", R, "plan", {"step": 1})
    shared = await svc.set(T, "alice", R, "notes", "for the room", visibility="room")

    after = PersistentMemorySvc()  # the relay restarted
    assert await after.get(T, "alice", R, "plan") == private
    assert await after.get(T, "alice", R, "notes") == shared
    # The route's ACL reads these fields; they come back unchanged.
    assert (private["visibility"], shared["visibility"]) == ("private", "room")
    # Buckets stay per participant: bob's bucket doesn't gain alice's entries.
    assert await after.get(T, "bob", R, "plan") is None


async def test_a_delete_survives_a_restart():
    svc = PersistentMemorySvc()
    await svc.set(T, "alice", R, "gone", 1)
    await svc.set(T, "alice", R, "kept", 2)
    assert await svc.delete(T, "alice", R, "gone")

    after = PersistentMemorySvc()
    assert await after.get(T, "alice", R, "gone") is None
    assert (await after.get(T, "alice", R, "kept"))["value"] == 2
    # The content is really gone: what's left of "gone" is a tombstone that
    # holds only when it was deleted, so other relay processes can't undo it.
    with sqlite3.connect(str(p1_sqlite.db_path())) as conn:
        left = dict(conn.execute("SELECT field, value FROM p1_entries").fetchall())
    assert set(left) == {"gone", "kept"}
    assert set(json.loads(left["gone"])) == {"deleted", "updated_at"}


async def test_reset_drops_the_rows():
    svc = PersistentMemorySvc()
    await svc.set(T, "alice", R, "k", 1)
    await svc.reset()
    assert _rows() == []
    assert await PersistentMemorySvc().get(T, "alice", R, "k") is None


async def test_capabilities_and_tool_catalog_survive_a_restart():
    caps = CapabilitySvc()
    await caps.publish(T, "alice-claude", {"skills": ["python"], "description": "builder"})
    tools = ToolCatalogSvc()
    await tools.register(T, R, name="lint", url="https://tools.example/lint",
                         registered_by="alice")

    assert await CapabilitySvc().get(T, "alice-claude") is not None
    assert (await ToolCatalogSvc().get(T, R, "lint"))["url"] == "https://tools.example/lint"


async def test_the_file_is_owner_only():
    await PersistentMemorySvc().set(T, "alice", R, "k", 1)
    path = p1_sqlite.db_path()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


async def test_a_corrupt_file_degrades_to_memory():
    path = p1_sqlite.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"this is not a sqlite database" * 100)

    svc = PersistentMemorySvc()
    await svc.set(T, "alice", R, "k", 1)  # no exception reaches the caller
    assert (await svc.get(T, "alice", R, "k"))["value"] == 1
    # "Could not read": callers keep serving what they hold rather than
    # replacing it with an empty bucket.
    assert await p1_persistence.hydrate("p1mem:x") == ({}, False)
    assert path.read_bytes().startswith(b"this is not")  # left alone for a human


async def test_a_locked_file_degrades_to_memory_and_retries(monkeypatch):
    monkeypatch.setattr(p1_sqlite, "_BUSY_S", 0.05)
    await PersistentMemorySvc().set(T, "alice", R, "before", 1)
    holder = sqlite3.connect(str(p1_sqlite.db_path()))
    holder.execute("BEGIN EXCLUSIVE")
    try:
        failures = p1_persistence.MIRROR_FAILURES
        svc = PersistentMemorySvc()
        await svc.set(T, "alice", R, "during", 2)  # logged, not raised
        assert (await svc.get(T, "alice", R, "during"))["value"] == 2
        # A locked read is "could not read", so the bucket is retried later.
        assert await p1_persistence.hydrate("p1mem:x") == ({}, False)
        assert p1_persistence.MIRROR_FAILURES > failures
    finally:
        holder.rollback()
        holder.close()
    assert (await PersistentMemorySvc().get(T, "alice", R, "before"))["value"] == 1


async def test_redis_stays_first_when_configured(monkeypatch):
    import fakeredis

    r = fakeredis.aioredis.FakeRedis()
    monkeypatch.setattr(p1_persistence, "get_redis_or_none", lambda: r)
    assert await p1_persistence.mirror_set("p1mem:x", "k", {"v": 1, "updated_at": 1.0})
    assert json.loads(await r.hget("p1mem:x", "k")) == {"v": 1, "updated_at": 1.0}
    assert not p1_sqlite.db_path().exists()


def _rows() -> list[tuple]:
    with sqlite3.connect(str(p1_sqlite.db_path())) as conn:
        return conn.execute("SELECT ns_key, field FROM p1_entries ORDER BY field").fetchall()


async def test_route_acls_hold_after_a_restart():
    """Over HTTP: after a restart bob still reads alice's room-visible entry
    and still can't read her private one."""
    from httpx import ASGITransport, AsyncClient

    from quorus.auth.tokens import create_jwt
    from quorus.relay import _reset_state, app

    def as_(name: str) -> dict[str, str]:
        return {"Authorization": "Bearer " + create_jwt(
            sub=name, tenant_id="t-acl", tenant_slug="acl", role="user")}

    _reset_state()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            rid = (await c.post("/rooms", json={"name": "acl-room", "created_by": "alice"},
                                headers=as_("alice"))).json()["id"]
            await c.post(f"/rooms/{rid}/join", json={"participant": "bob", "role": "member"},
                         headers=as_("alice"))
            for key, vis in (("secret", "private"), ("shared", "room")):
                put = await c.put(f"/v1/memory/alice/{rid}/{key}",
                                  json={"value": key, "visibility": vis}, headers=as_("alice"))
                assert put.status_code == 200, put.text

            app.state.persistent_memory_service = PersistentMemorySvc()  # restart

            shared = await c.get(f"/v1/memory/alice/{rid}/shared", headers=as_("bob"))
            assert shared.status_code == 200 and shared.json()["entry"]["value"] == "shared"
            secret = await c.get(f"/v1/memory/alice/{rid}/secret", headers=as_("bob"))
            assert secret.status_code == 404
            mine = await c.get(f"/v1/memory/alice/{rid}/secret", headers=as_("alice"))
            assert mine.status_code == 200 and mine.json()["entry"]["value"] == "secret"
    finally:
        _reset_state()


# --- Two relay processes on one store (codex, blocking review of 2844ce20) ---


@pytest.fixture
def always_refresh(monkeypatch):
    monkeypatch.setattr(p1_persistence, "HYDRATE_TTL_S", 0.0)


async def test_a_visibility_change_elsewhere_reaches_this_cache(always_refresh):
    a, b = PersistentMemorySvc(), PersistentMemorySvc()
    await a.set(T, "alice", R, "notes", "x", visibility="room")
    assert (await b.get(T, "alice", R, "notes"))["visibility"] == "room"
    await a.set(T, "alice", R, "notes", "x", visibility="private")
    assert (await b.get(T, "alice", R, "notes"))["visibility"] == "private"


async def test_a_delete_elsewhere_reaches_this_cache(always_refresh):
    a, b = PersistentMemorySvc(), PersistentMemorySvc()
    await a.set(T, "alice", R, "gone", 1)
    assert await b.get(T, "alice", R, "gone") is not None
    assert await a.delete(T, "alice", R, "gone")
    assert await b.get(T, "alice", R, "gone") is None
    assert await b.list(T, "alice", R) == []


async def test_tools_and_capabilities_follow_the_store_too(always_refresh):
    ta, tb = ToolCatalogSvc(), ToolCatalogSvc()
    await ta.register(T, R, name="lint", url="https://t.example/l", registered_by="alice")
    assert await tb.get(T, R, "lint") is not None
    assert await ta.remove(T, R, "lint")
    assert await tb.get(T, R, "lint") is None

    ca, cb = CapabilitySvc(), CapabilitySvc()
    await ca.publish(T, "alice-claude", {"capabilities": ["python"]})
    assert (await cb.get(T, "alice-claude"))["capabilities"] == ["python"]
    await ca.publish(T, "alice-claude", {"capabilities": ["go"]})
    assert (await cb.get(T, "alice-claude"))["capabilities"] == ["go"]


async def test_writes_the_store_missed_are_kept_and_mirrored_later(always_refresh, monkeypatch):
    monkeypatch.setattr(p1_sqlite, "_BUSY_S", 0.05)
    svc = PersistentMemorySvc()
    await svc.set(T, "alice", R, "old", 1)
    holder = sqlite3.connect(str(p1_sqlite.db_path()))
    holder.execute("BEGIN EXCLUSIVE")
    try:
        await svc.set(T, "alice", R, "new", 2)  # the store missed this one
        assert await svc.delete(T, "alice", R, "old")  # and this one
    finally:
        holder.rollback()
        holder.close()
    # The next successful refresh keeps both local changes and mirrors them.
    assert (await svc.get(T, "alice", R, "new"))["value"] == 2
    assert await svc.get(T, "alice", R, "old") is None
    other = PersistentMemorySvc()
    assert (await other.get(T, "alice", R, "new"))["value"] == 2
    assert await other.get(T, "alice", R, "old") is None


class _Locked:
    """Hold an exclusive lock on the store so this process's writes miss it."""

    def __enter__(self):
        self.conn = sqlite3.connect(str(p1_sqlite.db_path()))
        self.conn.execute("BEGIN EXCLUSIVE")
        return self

    def __exit__(self, *exc):
        self.conn.rollback()
        self.conn.close()


async def test_a_missed_write_doesnt_override_a_newer_one_elsewhere(always_refresh, monkeypatch):
    """Claude's repro on 8bfb268d: A's missed 'room' write came back over B's
    later 'private' one when A refreshed."""
    monkeypatch.setattr(p1_sqlite, "_BUSY_S", 0.05)
    a, b = PersistentMemorySvc(), PersistentMemorySvc()
    await a.set(T, "alice", R, "warmup", 0)  # create the file before locking it
    with _Locked():
        await a.set(T, "alice", R, "k", "x", visibility="room")  # store misses it
    time.sleep(0.01)
    await b.set(T, "alice", R, "k", "x", visibility="private")  # lands, newer
    assert (await a.get(T, "alice", R, "k"))["visibility"] == "private"
    assert (await PersistentMemorySvc().get(T, "alice", R, "k"))["visibility"] == "private"


async def test_a_missed_delete_doesnt_remove_a_newer_set_elsewhere(always_refresh, monkeypatch):
    monkeypatch.setattr(p1_sqlite, "_BUSY_S", 0.05)
    a, b = PersistentMemorySvc(), PersistentMemorySvc()
    await a.set(T, "alice", R, "k", 1)
    with _Locked():
        assert await a.delete(T, "alice", R, "k")  # store misses the delete
    time.sleep(0.01)
    await b.set(T, "alice", R, "k", 2)  # set again after, and it lands
    assert (await a.get(T, "alice", R, "k"))["value"] == 2
    assert (await PersistentMemorySvc().get(T, "alice", R, "k"))["value"] == 2


async def test_a_newer_delete_elsewhere_beats_an_older_missed_set(always_refresh, monkeypatch):
    """Claude's repro on fbcfa2dc: k is stored, A's set to v2 misses the
    locked store, B then deletes k and that lands; A must not bring v2 back."""
    monkeypatch.setattr(p1_sqlite, "_BUSY_S", 0.05)
    a, b = PersistentMemorySvc(), PersistentMemorySvc()
    await a.set(T, "alice", R, "k", "v1")
    with _Locked():
        await a.set(T, "alice", R, "k", "v2")  # store misses it
    time.sleep(0.01)
    assert await b.delete(T, "alice", R, "k")  # newer, and it lands
    assert await a.get(T, "alice", R, "k") is None
    assert await PersistentMemorySvc().get(T, "alice", R, "k") is None
    assert await a.list(T, "alice", R) == []


async def test_old_tombstones_are_pruned(monkeypatch):
    await p1_persistence.mirror_delete("p1mem:x", "k", at=time.time() - p1_sqlite.TTL_S - 60)
    await p1_persistence.mirror_delete("p1mem:x", "fresh")
    stored, ok = await p1_persistence.hydrate("p1mem:x")
    assert ok and set(stored) == {"fresh"}
    assert [r[1] for r in _rows()] == ["fresh"]


# --- Codex, review of 6d825f38: a newer write landing between hydrate and replay ---


_NOW = time.time()


def _entry(vis: str, at: float) -> dict:
    """An entry written *at* seconds into this test run (real-time stamps, so
    nothing looks old enough to prune)."""
    return {"key": "k", "value": "x", "visibility": vis, "updated_at": _NOW + at}


@pytest.fixture(params=["sqlite", "redis"])
def store(request, monkeypatch):
    if request.param == "redis":
        import fakeredis

        r = fakeredis.aioredis.FakeRedis()
        monkeypatch.setattr(p1_persistence, "get_redis_or_none", lambda: r)
    return request.param


async def test_replay_loses_to_a_newer_write_after_the_snapshot(store):
    pending = p1_persistence.PendingWrites()
    pending.note("b", "k", "set", False, at=_NOW + 100.0)  # A's room write missed the store
    snapshot = {}  # A's hydrate ran before B's write
    assert await p1_persistence.mirror_set("p1mem:x", "k", _entry("private", 200.0))  # B
    merged = await pending.reconcile("b", "p1mem:x", {"k": _entry("room", 100.0)}, snapshot)
    assert merged["k"]["visibility"] == "private"
    assert (await p1_persistence.hydrate("p1mem:x"))[0]["k"]["visibility"] == "private"


async def test_replay_loses_to_a_newer_delete_after_the_snapshot(store):
    pending = p1_persistence.PendingWrites()
    pending.note("b", "k", "set", False, at=_NOW + 100.0)
    snapshot = {"k": _entry("room", 50.0)}
    assert await p1_persistence.mirror_delete("p1mem:x", "k", at=_NOW + 200.0)  # B deletes, lands
    merged = await pending.reconcile("b", "p1mem:x", {"k": _entry("room", 100.0)}, snapshot)
    assert "k" not in merged
    stored, _ = await p1_persistence.hydrate("p1mem:x")
    assert p1_persistence.is_tombstone(stored["k"])


async def test_an_older_write_never_replaces_a_newer_one_in_the_store(store):
    assert await p1_persistence.mirror_set("p1mem:x", "k", _entry("private", 200.0))
    assert await p1_persistence.mirror_set("p1mem:x", "k", _entry("room", 100.0))
    assert (await p1_persistence.hydrate("p1mem:x"))[0]["k"]["visibility"] == "private"


async def test_a_file_from_before_the_stamp_column_is_upgraded():
    path = p1_sqlite.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path)) as conn:
        conn.execute("CREATE TABLE p1_entries (ns_key TEXT NOT NULL, field TEXT NOT NULL,"
                     " value TEXT NOT NULL, touched REAL NOT NULL, PRIMARY KEY (ns_key, field))")
        conn.execute("INSERT INTO p1_entries VALUES (?, ?, ?, ?)",
                     ("p1mem:x", "old", json.dumps(_entry("room", 5.0)), time.time()))
    assert await p1_persistence.mirror_set("p1mem:x", "k", _entry("room", 10.0))
    stored, ok = await p1_persistence.hydrate("p1mem:x")
    assert ok and set(stored) == {"old", "k"}
