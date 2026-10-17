"""``GET /v1/rooms/{room}/scorecard`` for the board (2026-10-17): members only,
aggregates only, and 404 when this relay has no repo bound to the room."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from quorus import ledger
from quorus.relay import _reset_state, app
from quorus.routes import room_scorecard

H = {"Authorization": "Bearer test-secret"}


def _repo(tmp: Path) -> Path:
    repo = tmp / "app"
    repo.mkdir()
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp)}
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, env=env, check=True,  # noqa: E731
                                    capture_output=True)
    run("init", "-q", "-b", "main")
    (repo / "base.py").write_text("y = 0\n")
    run("add", "-A")
    run("-c", "user.name=arav", "-c", "user.email=a@x", "commit", "-qm", "init")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                          text=True).stdout.strip()
    (repo / "a.py").write_text("x = 1\n")
    run("add", "-A")
    run("-c", "user.name=a-codex", "-c", "user.email=c@x", "commit", "-qm", "feat: a")
    # The daemon's publish records who did it; the author name alone isn't trusted.
    ledger.record_provenance(repo, base, "HEAD", {"agent": "a-codex", "vendor": "codex"})
    return repo


@pytest.fixture
async def client(tmp_path: Path, monkeypatch):
    _reset_state()
    room_scorecard._cache.clear()
    monkeypatch.setenv("HOME", str(tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/rooms", json={"name": "build", "created_by": "arav"}, headers=H)
        await c.post("/rooms/build/join", json={"participant": "arav"}, headers=H)
        yield c
    _reset_state()


async def test_404_without_a_binding(client: AsyncClient) -> None:
    r = await client.get("/v1/rooms/build/scorecard", headers=H)
    assert r.status_code == 404


async def test_aggregates_for_a_bound_repo(client: AsyncClient, tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (tmp_path / ".quorus").mkdir()
    (tmp_path / ".quorus" / "room-bindings.json").write_text(json.dumps({"build": str(repo)}))
    r = await client.get("/v1/rooms/build/scorecard", headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    card = {a["agent"]: a for a in body["agents"]}
    assert card["a-codex"]["commits"] == 1
    assert set(card["a-codex"]) == {"agent", "commits", "with_tests", "approved", "flagged",
                                      "reverted", "survival", "duels_won", "duels_lost"}
    assert str(repo) not in r.text and "feat: a" not in r.text  # no paths or commit text


async def test_rejects_odd_since_and_needs_auth(client: AsyncClient) -> None:
    r = await client.get("/v1/rooms/build/scorecard", headers=H,
                         params={"since": "--output=/etc/passwd"})
    assert r.status_code == 422
    assert (await client.get("/v1/rooms/build/scorecard")).status_code == 401
