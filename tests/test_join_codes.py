"""Tests for /v1/join/mint, /v1/join/resolve, /v1/join/install.

Covers the in-memory storage path (no DATABASE_URL required) so this
file runs against the same relay app fixture as the rest of the suite.
Postgres behavior is covered by the service's dual-mode design — if
the in-memory path works, the Postgres path is a thin SQLAlchemy wrap
that we'd regress via integration tests elsewhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from quorus.relay import _reset_state, app
from quorus.services.join_code_svc import normalize_code

ADMIN_HEADERS = {"Authorization": "Bearer test-secret"}


@pytest.fixture(autouse=True)
async def clean_state():
    _reset_state()
    yield
    _reset_state()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _create_room(client: AsyncClient, name: str = "general") -> None:
    resp = await client.post(
        "/rooms",
        headers=ADMIN_HEADERS,
        json={"name": name, "created_by": "admin"},
    )
    assert resp.status_code in (200, 201), resp.text


# ---------------------------------------------------------------------------
# normalize_code — pure function, no HTTP
# ---------------------------------------------------------------------------


class TestNormalizeCode:
    def test_accepts_canonical(self):
        assert normalize_code("HX4KM7ZP") == "HX4KM7ZP"

    def test_accepts_display_hyphen(self):
        assert normalize_code("HX4K-M7ZP") == "HX4KM7ZP"

    def test_uppercases(self):
        assert normalize_code("hx4k-m7zp") == "HX4KM7ZP"

    def test_strips_surrounding_quotes(self):
        # Straight + smart single + smart double.
        for wrap in ("'", "\"", "\u201c", "\u201d", "\u2018", "\u2019"):
            assert normalize_code(f"{wrap}HX4K-M7ZP{wrap}") == "HX4KM7ZP"

    def test_strips_whitespace(self):
        assert normalize_code("  HX4K-M7ZP  ") == "HX4KM7ZP"

    def test_rejects_non_crockford(self):
        # `O` and `0` should never appear in valid codes.
        assert normalize_code("HX4K-M0ZP") is None
        assert normalize_code("HX4K-MOZP") is None
        assert normalize_code("HX4K-MIZP") is None

    def test_rejects_wrong_length(self):
        assert normalize_code("HX4K") is None
        assert normalize_code("HX4K-M7ZP-EXTRA") is None

    def test_rejects_none(self):
        assert normalize_code(None) is None


# ---------------------------------------------------------------------------
# /v1/join/mint
# ---------------------------------------------------------------------------


class TestMint:
    async def test_requires_auth(self, client: AsyncClient):
        resp = await client.post("/v1/join/mint", json={"room": "general"})
        assert resp.status_code == 401

    async def test_user_role_rejected(self, client: AsyncClient):
        from quorus.auth.tokens import create_jwt

        token = create_jwt(
            sub="alice", tenant_id="t", tenant_slug="ws", role="user",
        )
        await _create_room(client)
        resp = await client.post(
            "/v1/join/mint",
            headers={"Authorization": f"Bearer {token}"},
            json={"room": "general"},
        )
        assert resp.status_code == 403

    async def test_returns_display_code(self, client: AsyncClient):
        await _create_room(client)
        resp = await client.post(
            "/v1/join/mint", headers=ADMIN_HEADERS, json={"room": "general"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        # `ABCD-EFGH` shape — 4 + hyphen + 4.
        assert len(data["code"]) == 9
        assert data["code"][4] == "-"
        # Canonical chars are Crockford-valid.
        assert normalize_code(data["code"]) is not None
        # URLs point at the test host.
        assert "/v1/join/resolve/" in data["join_url"]
        assert data["install_url"].endswith(".sh")

    async def test_rejects_unknown_room(self, client: AsyncClient):
        resp = await client.post(
            "/v1/join/mint",
            headers=ADMIN_HEADERS,
            json={"room": "does-not-exist"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /v1/join/resolve
# ---------------------------------------------------------------------------


class TestResolve:
    async def test_happy_path(self, client: AsyncClient):
        await _create_room(client)
        mint = await client.post(
            "/v1/join/mint", headers=ADMIN_HEADERS, json={"room": "general"},
        )
        code = mint.json()["code"]

        # Display form works.
        resp = await client.get(f"/v1/join/resolve/{code}")
        assert resp.status_code == 200, resp.text
        payload = resp.json()["payload"]
        assert payload["n"] == "general"
        assert payload["r"].startswith("http")

    async def test_accepts_canonical_form(self, client: AsyncClient):
        await _create_room(client)
        mint = await client.post(
            "/v1/join/mint", headers=ADMIN_HEADERS, json={"room": "general"},
        )
        canonical = mint.json()["code"].replace("-", "")
        resp = await client.get(f"/v1/join/resolve/{canonical}")
        assert resp.status_code == 200

    async def test_rejects_bad_format(self, client: AsyncClient):
        resp = await client.get("/v1/join/resolve/abc")
        assert resp.status_code == 400

    async def test_unknown_code_is_404(self, client: AsyncClient):
        # Valid Crockford, just unminted.
        resp = await client.get("/v1/join/resolve/AAAA-BBBB")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /v1/join/install/{code}.sh
# ---------------------------------------------------------------------------


class TestInstallScript:
    async def test_returns_bash_with_code_embedded(self, client: AsyncClient):
        await _create_room(client)
        mint = await client.post(
            "/v1/join/mint", headers=ADMIN_HEADERS, json={"room": "general"},
        )
        code = mint.json()["code"]

        resp = await client.get(f"/v1/join/install/{code}.sh")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/x-sh")
        body = resp.text
        # Must be a real script.
        assert body.startswith("#!/usr/bin/env bash")
        assert "set -euo pipefail" in body
        # Code must be embedded so `curl | sh` is self-sufficient.
        assert code in body
        # Must invoke the join command.
        assert f'quorus join "{code}"' in body
        # Must pin the relay it was served from, or a fresh install falls
        # back to the public default and can't resolve a self-hosted code.
        assert '--relay "http://test"' in body

    async def test_join_line_pins_served_relay(self, client: AsyncClient):
        await _create_room(client)
        mint = await client.post(
            "/v1/join/mint", headers=ADMIN_HEADERS, json={"room": "general"},
        )
        code = mint.json()["code"]

        resp = await client.get(
            f"/v1/join/install/{code}.sh",
            headers={"x-forwarded-host": "relay.example.com"},
        )
        assert resp.status_code == 200
        for line in resp.text.splitlines():
            if line.startswith("quorus join"):
                assert '--relay "http://relay.example.com"' in line
                break
        else:
            pytest.fail("installer has no `quorus join` line")

    async def _served_script(self, client: AsyncClient, tmp_path) -> tuple[str, "Path"]:
        """The installer for a fresh code, written next to stub `pipx` and `quorus`
        commands that only record their arguments."""
        await _create_room(client)
        mint = await client.post(
            "/v1/join/mint", headers=ADMIN_HEADERS, json={"room": "general"},
        )
        code = mint.json()["code"]
        resp = await client.get(f"/v1/join/install/{code}.sh")
        script = tmp_path / "install.sh"
        script.write_text(resp.text)
        stubs = tmp_path / "bin"
        stubs.mkdir()
        (stubs / "pipx").write_text("#!/bin/sh\nexit 0\n")
        (stubs / "quorus").write_text(
            '#!/bin/sh\nprintf "%s\\n" "$*" >> "$HOME/quorus-calls"\necho "quorus-stub $*"\n')
        for f in stubs.iterdir():
            f.chmod(0o755)
        return code, script

    @staticmethod
    def _env(tmp_path) -> dict[str, str]:
        return {"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin", "HOME": str(tmp_path),
                "USER": "sam", "TERM": "dumb"}

    async def test_piped_install_without_a_terminal_joins_and_says_what_is_next(
        self, client: AsyncClient, tmp_path,
    ):
        """0.6.3 audit (multi-person lane): `curl … | bash` in a context with no
        terminal used to end with "the interactive hub needs a terminal", exit 2."""
        import subprocess

        code, script = await self._served_script(client, tmp_path)
        run = subprocess.run(["/bin/bash", str(script)], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, env=self._env(tmp_path),
                             timeout=30, start_new_session=True)  # no controlling tty
        assert run.returncode == 0, run.stderr
        calls = (tmp_path / "quorus-calls").read_text().splitlines()
        assert calls == [f'join {code} --relay http://test --name sam']
        assert "Next: run  quorus" in run.stdout

    async def test_piped_install_asks_the_name_on_the_terminal_and_opens_the_hub(
        self, client: AsyncClient, tmp_path,
    ):
        """Same one-liner in a real terminal: stdin is the pipe but /dev/tty exists,
        so the script asks for the name there and execs the hub on it."""
        import os
        import pty
        import select
        import time

        code, script = await self._served_script(client, tmp_path)
        pid, fd = pty.fork()
        if pid == 0:  # child: a terminal, but stdin is not it (the curl pipe)
            try:
                null = os.open(os.devnull, os.O_RDONLY)
                os.dup2(null, 0)
                os.execve("/bin/bash", ["bash", str(script)], self._env(tmp_path))
            finally:
                os._exit(127)
        out, answered = "", False
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk.decode("utf-8", "replace")
            if "Your name" in out and not answered:
                os.write(fd, b"aarya\n")
                answered = True
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                break
        else:
            import signal

            os.kill(pid, signal.SIGKILL)  # Codex: reap the child before failing
            os.waitpid(pid, 0)
            os.close(fd)
            pytest.fail(f"installer did not finish; output:\n{out[-1500:]}")
        os.close(fd)
        assert answered, out
        calls = (tmp_path / "quorus-calls").read_text().splitlines()
        assert calls == [f'join {code} --relay http://test --name aarya', ""]
        assert "quorus-stub" in out  # the hub (stub) ran on the terminal

    async def test_404_on_unknown_code(self, client: AsyncClient):
        resp = await client.get("/v1/join/install/AAAA-BBBB.sh")
        assert resp.status_code == 404

    async def test_400_on_bad_format(self, client: AsyncClient):
        resp = await client.get("/v1/join/install/nope.sh")
        assert resp.status_code == 400


async def test_a_forged_forwarded_host_never_reaches_the_installer(client: AsyncClient):
    """The relay URL is written into a script users pipe to `sh`: header values
    that aren't a plain host[:port] / http(s) are ignored."""
    await _create_room(client)
    code = (await client.post("/v1/join/mint", headers=ADMIN_HEADERS,
                              json={"room": "general"})).json()["code"]
    resp = await client.get(f"/v1/join/install/{code}.sh", headers={
        "x-forwarded-host": 'evil"; rm -rf ~ #', "x-forwarded-proto": "javascript"})
    assert resp.status_code == 200
    assert "rm -rf" not in resp.text and "javascript" not in resp.text
    assert '--relay "http://test"' in resp.text
