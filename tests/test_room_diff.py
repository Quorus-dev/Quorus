"""``GET /v1/rooms/{room}/commits/{sha}/diff`` (docs/plans/board-inbox.md, slice 5): the
diff beside a vote. Own JWT only, members only, this machine's bindings only (the
scorecard's tenant rule), a byte cap, and a cache that is keyed by tenant and repo."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from quorus.auth.tokens import create_jwt
from quorus.relay import _reset_state, app
from quorus.routes import room_diff

SECRET = {"Authorization": "Bearer test-secret"}


async def _member(client: AsyncClient, name: str) -> dict[str, str]:
    """A participant JWT the way `quorus web` gets one: claim the name with the secret,
    then exchange the key (quorus/auth/participant_routes.py)."""
    claimed = await client.post("/v1/auth/claim-name", json={"name": name}, headers=SECRET)
    assert claimed.status_code == 200, claimed.text
    token = await client.post("/v1/auth/token", json={"api_key": claimed.json()["participant_key"]})
    assert token.status_code == 200, token.text
    return {"Authorization": "Bearer " + token.json()["token"]}


def _git(repo: Path, home: Path, *args: str) -> str:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home)}
    return subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True,
                          text=True).stdout.strip()


def _repo(tmp: Path, name: str = "app", lines: int = 3) -> tuple[Path, str]:
    repo = tmp / name
    repo.mkdir()
    _git(repo, tmp, "init", "-q", "-b", "main")
    (repo / "a.py").write_text("x = 0\n")
    _git(repo, tmp, "add", "-A")
    _git(repo, tmp, "-c", "user.name=a", "-c", "user.email=a@x", "commit", "-qm", "init")
    (repo / "a.py").write_text("".join(f"x{i} = {i}\n" for i in range(lines)))
    _git(repo, tmp, "add", "-A")
    _git(repo, tmp, "-c", "user.name=a-codex", "-c", "user.email=c@x", "commit", "-qm",
         f"feat: {lines} lines")
    return repo, _git(repo, tmp, "rev-parse", "HEAD")


def _bind(tmp: Path, room: str, repo: Path) -> None:
    (tmp / ".quorus").mkdir(exist_ok=True)
    (tmp / ".quorus" / "room-bindings.json").write_text(json.dumps({room: str(repo)}))


@pytest.fixture
async def client(tmp_path: Path, monkeypatch):
    _reset_state()
    room_diff._diffs.items.clear()
    room_diff._aliases.items.clear()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("QUORUS_CONFIG_DIR", raising=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/rooms", json={"name": "build", "created_by": "arav"}, headers=SECRET)
        await c.post("/rooms/build/join", json={"participant": "arav"}, headers=SECRET)
        c.arav = await _member(c, "arav")  # type: ignore[attr-defined]
        yield c
    _reset_state()


async def test_the_shared_secret_and_non_members_get_403(client: AsyncClient,
                                                         tmp_path: Path) -> None:
    repo, sha = _repo(tmp_path)
    _bind(tmp_path, "build", repo)
    assert (await client.get(f"/v1/rooms/build/commits/{sha}/diff", headers=SECRET)
            ).status_code == 403  # the secret names nobody: it reads no diff
    eve = await _member(client, "eve")  # a real identity, but not a member of #build
    assert (await client.get(f"/v1/rooms/build/commits/{sha}/diff", headers=eve)
            ).status_code == 403
    assert (await client.get(f"/v1/rooms/build/commits/{sha}/diff")).status_code == 401


async def test_404_without_a_binding_or_for_a_bad_sha(client: AsyncClient,
                                                      tmp_path: Path) -> None:
    repo, sha = _repo(tmp_path)
    r = await client.get(f"/v1/rooms/build/commits/{sha}/diff", headers=client.arav)
    assert r.status_code == 404 and "no repo bound" in r.text
    _bind(tmp_path, "build", repo)
    for bad in ("deadbeef", "HEAD", "main", "..%2F..", "a" * 6, "g" * 7, sha[:7] + "zz"):
        r = await client.get(f"/v1/rooms/build/commits/{bad}/diff", headers=client.arav)
        assert r.status_code == 404, (bad, r.status_code)
        assert str(repo) not in r.text


async def test_a_member_reads_the_diff_as_text(client: AsyncClient, tmp_path: Path) -> None:
    repo, sha = _repo(tmp_path)
    _bind(tmp_path, "build", repo)
    r = await client.get(f"/v1/rooms/build/commits/{sha[:8]}/diff", headers=client.arav)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/plain")
    assert r.headers["cache-control"] == "private, max-age=60"
    assert "feat: 3 lines" in r.text and "+x2 = 2" in r.text and "-x = 0" in r.text
    assert " a.py | " in r.text  # --stat, then the patch


async def test_a_long_commit_is_cut_at_the_cap(client: AsyncClient, tmp_path: Path,
                                               monkeypatch) -> None:
    repo, sha = _repo(tmp_path, lines=400)
    _bind(tmp_path, "build", repo)
    monkeypatch.setattr(room_diff, "DIFF_BYTES", 2_000)
    r = await client.get(f"/v1/rooms/build/commits/{sha}/diff", headers=client.arav)
    assert r.status_code == 200
    assert r.text.endswith("[cut by Quorus at 2000 bytes]") and len(r.text) < 2_200


async def test_the_cache_is_keyed_by_tenant_and_repo(client: AsyncClient, tmp_path: Path,
                                                     monkeypatch) -> None:
    repo, sha = _repo(tmp_path)
    _bind(tmp_path, "build", repo)
    calls: list[str] = []
    real = room_diff._render

    def counted(r: Path, s: str) -> str:
        calls.append(str(r))
        return real(r, s)

    monkeypatch.setattr(room_diff, "_render", counted)
    resolves: list[str] = []
    real_resolve = room_diff._resolve
    monkeypatch.setattr(room_diff, "_resolve",
                        lambda r, s: resolves.append(s) or real_resolve(r, s))
    first = await client.get(f"/v1/rooms/build/commits/{sha}/diff", headers=client.arav)
    second = await client.get(f"/v1/rooms/build/commits/{sha}/diff", headers=client.arav)
    assert first.status_code == second.status_code == 200 and first.text == second.text
    assert len(calls) == 1 and len(resolves) == 1  # the second answer spawned no git at all
    # Codex on f3ba7b4a: a short sha is the same commit, so it shares the one rendered entry.
    short = await client.get(f"/v1/rooms/build/commits/{sha[:7]}/diff", headers=client.arav)
    assert short.status_code == 200 and short.text == first.text
    assert len(calls) == 1 and len(resolves) == 2  # resolved once more, rendered never again
    assert len(room_diff._diffs.items) == 1 and len(room_diff._aliases.items) == 2
    # Re-bound to another repo (different content, so a different history): a different
    # repo is a different key, so the cache is missed and git runs again, and finds nothing.
    other, _ = _repo(tmp_path, name="other", lines=4)
    _bind(tmp_path, "build", other)
    r = await client.get(f"/v1/rooms/build/commits/{sha}/diff", headers=client.arav)
    # A different repo is a different key: resolved again (and not found), never served
    # from the other repo's entry.
    assert r.status_code == 404 and len(resolves) == 3 and len(calls) == 1
    assert str(repo) not in r.text and str(other) not in r.text


async def test_another_tenants_room_with_the_same_name_gets_nothing(
        client: AsyncClient, tmp_path: Path) -> None:
    """The scorecard's rule (codex on 1fae77f7): bindings belong to the shared-secret
    relay's own rooms; a hosted tenant naming its room `build` reads no local repo."""
    repo, sha = _repo(tmp_path)
    _bind(tmp_path, "build", repo)
    eve = {"Authorization": "Bearer " + create_jwt(sub="eve", tenant_id="t-other",
                                                   tenant_slug="other")}
    made = await client.post("/rooms", json={"name": "build", "created_by": "eve"}, headers=eve)
    assert made.status_code == 200, made.text
    r = await client.get(f"/v1/rooms/{made.json()['id']}/commits/{sha}/diff", headers=eve)
    assert r.status_code == 404 and "feat:" not in r.text
    # The local member still reads it, and the two answers never share a cache entry.
    assert (await client.get(f"/v1/rooms/build/commits/{sha}/diff", headers=client.arav)
            ).status_code == 200
    assert all(k[0] == "_legacy" for k in room_diff._diffs.items)


async def test_the_cache_is_bounded_and_expires(client: AsyncClient, tmp_path: Path,
                                                monkeypatch) -> None:
    """Codex on f3ba7b4a: without a cap, members could park unlimited 64 KB diffs in relay
    memory. Oldest entries leave past the cap; every entry leaves after CACHE_S."""
    repo, _ = _repo(tmp_path)
    shas = []
    for i in range(4):  # four more commits, each a different diff
        (repo / "a.py").write_text(f"x = {i}\n")
        _git(repo, tmp_path, "add", "-A")
        _git(repo, tmp_path, "-c", "user.name=a", "-c", "user.email=a@x", "commit", "-qm", f"c{i}")
        shas.append(_git(repo, tmp_path, "rev-parse", "HEAD"))
    _bind(tmp_path, "build", repo)
    monkeypatch.setattr(room_diff._diffs, "cap", 2)
    monkeypatch.setattr(room_diff._aliases, "cap", 2)
    for sha in shas:
        assert (await client.get(f"/v1/rooms/build/commits/{sha}/diff", headers=client.arav)
                ).status_code == 200
    kept = [k[2] for k in room_diff._diffs.items]
    assert kept == shas[-2:] and len(room_diff._aliases.items) == 2  # the two newest only
    # Expiry: an entry older than CACHE_S is dropped on the next touch and rendered again.
    calls: list[str] = []
    real = room_diff._render
    monkeypatch.setattr(room_diff, "_render", lambda r, f: calls.append(f) or real(r, f))
    monkeypatch.setattr(room_diff, "_now", lambda: time.time() + room_diff.CACHE_S + 1)
    assert (await client.get(f"/v1/rooms/build/commits/{shas[-1]}/diff", headers=client.arav)
            ).status_code == 200
    assert calls == [shas[-1]] and len(room_diff._diffs.items) == 1
