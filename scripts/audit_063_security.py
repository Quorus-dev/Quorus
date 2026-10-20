"""Adversarial checks against an installed release, without a network listener.

Run with the published wheel's interpreter and -I, from an isolated HOME:
  /path/to/release-venv/bin/python -I scripts/audit_063_security.py \
    --state DIR --out FILE --wheel WHEEL
Only statuses and booleans are written; credentials and invitation codes stay in memory.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import hashlib
import io
import json
import logging
import os
import secrets
import socket
import zipfile
from importlib.metadata import distribution
from pathlib import Path


async def run(state: Path, wheel: Path) -> dict:
    # Callers must supply a clean environment; never load the team's profile.
    state.mkdir(parents=True, exist_ok=True)
    os.environ.update(RELAY_SECRET=secrets.token_hex(24), JWT_SECRET=secrets.token_hex(32),
                      BOOTSTRAP_SECRET=secrets.token_hex(24), QUORUS_NOTIFY="0",
                      QUORUS_CONFIG_DIR=str(state), MESSAGES_FILE=str(state / "messages.json"))
    logging.disable(logging.CRITICAL)
    import structlog
    from fastapi import HTTPException
    from httpx import ASGITransport, AsyncClient
    from starlette.requests import Request

    from quorus.auth.participant_keys import ParticipantKeyStore
    from quorus.relay import _reset_state, app
    from quorus.routes.sse import stream_messages
    from quorus.runtime.owner_pin import pinned_owner_request

    structlog.configure(processors=[structlog.processors.JSONRenderer()],
                        logger_factory=structlog.ReturnLoggerFactory())
    dist = distribution("quorus-ai")
    assert dist.version == "0.6.3", "this audit targets published 0.6.3"
    wheel_sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert wheel_sha == "a5617dbac318a47d1febb46b799cbafbde045ccdef69aacf9f5721df137d0b9f"
    import quorus
    assert "site-packages" in str(Path(quorus.__file__).resolve()), "not a wheel install"
    checked, mismatches = 0, []
    for name, digest, _size in csv.reader(io.StringIO(dist.read_text("RECORD") or "")):
        if not digest:
            continue
        algorithm, expected = digest.split("=", 1)
        actual = base64.urlsafe_b64encode(hashlib.new(
            algorithm, dist.locate_file(name).read_bytes()).digest()).decode().rstrip("=")
        checked += 1
        if actual != expected:
            mismatches.append(name)
    rows = []

    def check(command, passed, evidence):
        rows.append({"command": command, "result": "PASS" if passed else "FAIL",
                     "evidence": evidence})

    with zipfile.ZipFile(wheel) as archive:
        sensitive = {".env", ".aws", ".ssh", ".quorus", "id_rsa", "id_ed25519"}
        suspicious = [n for n in archive.namelist() if sensitive & set(Path(n).parts)]
    check("wheel: accidental credential-file packaging", not suspicious,
          f"{len(suspicious)} sensitive-path matches; file contents not logged")
    _reset_state()
    app.state.participant_keys = ParticipantKeyStore(state / "claims")
    admin = {"Authorization": "Bearer " + os.environ["RELAY_SECRET"]}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://isolated-audit") as c:
        async def token(key):
            r = await c.post("/v1/auth/token", json={"api_key": key})
            assert r.status_code == 200
            return {"Authorization": "Bearer " + r.json()["token"]}

        async def join(name, role="member"):
            r = await c.post("/v1/join/mint", json={"room": "audit", "role": role}, headers=admin)
            assert r.status_code == 200
            inv = r.json()["invite_id"]
            payload = (await c.get("/v1/join/resolve/" + r.json()["code"])).json()["payload"]
            check(f"share ({role}): resolved payload omits relay secret", "s" not in payload,
                  "resolved payload inspected in memory; no credential values recorded")
            r = await c.post("/v1/join/redeem", json={"code": payload["c"], "name": name})
            assert r.status_code == 200
            key = r.json()["participant_key"]
            replay = await c.post("/v1/join/redeem",
                                  json={"code": payload["c"], "name": name + "-replay"})
            check(f"join ({role}): replay single-use invitation", replay.status_code != 200,
                  f"HTTP {replay.status_code}")
            return inv, await token(key)

        r = await c.post("/rooms", json={"name": "audit", "created_by": "alice"}, headers=admin)
        assert r.status_code == 200
        inv, bob = await join("bob")
        _, observer = await join("observer", "observer")
        r = await c.get("/rooms/audit/history")
        check("unauthenticated: room history", r.status_code == 401, f"HTTP {r.status_code}")
        r = await c.post("/v1/auth/token", json={"api_key": "not-a-valid-credential"})
        check("invalid credential: exchange", r.status_code == 401, f"HTTP {r.status_code}")
        check("invalid credential: response does not echo input",
              "not-a-valid-credential" not in r.text, "response scanned without recording it")
        r = await c.post("/rooms/audit/messages",
                         json={"from_name": "observer", "content": "probe"},
                         headers=observer)
        check("observer: post room message", r.status_code == 403, f"HTTP {r.status_code}")
        r = await c.post("/v1/join/mint", json={"room": "audit"}, headers=bob)
        check("member: mint invite", r.status_code == 403, f"HTTP {r.status_code}")
        r = await c.post("/stream/token", json={"recipient": "alice"}, headers=bob)
        check("member: mint another participant's stream", r.status_code == 403,
              f"HTTP {r.status_code}")
        r = await c.post("/rooms/audit/messages", json={"from_name": "alice", "content": "probe"},
                         headers=bob)
        check("member: impersonate owner", r.status_code == 403, f"HTTP {r.status_code}")
        r = await c.post("/v1/auth/claim-name", json={"name": "bob-codex"}, headers=bob)
        assert r.status_code == 200
        agent = await token(r.json()["participant_key"])
        r = await c.post("/v1/join/mint", json={"room": "audit"}, headers=agent)
        check("agent: mint invitation", r.status_code == 403, f"HTTP {r.status_code}")
        r = await c.post("/v1/auth/claim-name", json={"name": "alice-codex"}, headers=bob)
        check("member: claim another owner's agent", r.status_code in (400, 403, 409),
              f"HTTP {r.status_code}")
        r = await c.post("/v1/auth/claim-name", json={"name": "bob-codex-child"}, headers=agent)
        check("agent: mint descendant identity", r.status_code == 403, f"HTTP {r.status_code}")
        await c.post("/rooms/audit/join", json={"participant": "bob-codex"}, headers=bob)
        r = await c.post("/rooms/audit/messages", json={"from_name": "bob", "content": "probe"},
                         headers=admin)
        check("secret: impersonate claimed member", r.status_code == 409, f"HTTP {r.status_code}")
        await c.post("/rooms/audit/messages", json={"from_name": "bob", "content": "owner request"},
                     headers=bob)
        history = (await c.get("/rooms/audit/history", headers=admin)).json()
        root = history[-1]
        forged = {"id": "forged", "from_name": "bob-claude", "reply_to": root["id"],
                  "sender_auth": "secret", "sender_tenant": "_legacy"}
        pin = {"tenant": root["sender_tenant"], "claim_id": root["sender_claim_id"]}
        check("owner pin: secret descendant replying to genuine root",
              not pinned_owner_request(forged, [root, forged], "bob", pin), "read-only")
        r = await c.post(f"/v1/join/invites/{inv}/revoke", headers=bob)
        check("member: revoke invite", r.status_code == 403, f"HTTP {r.status_code}")

        async def stream_token():
            r = await c.post("/stream/token", json={"recipient": "bob"}, headers=bob)
            assert r.status_code == 200
            flags = r.headers.get("set-cookie", "").lower()
            check("stream token: browser cookie flags",
                  all(flag in flags for flag in ("httponly", "secure", "samesite=strict")),
                  "HttpOnly/Secure/SameSite inspected; cookie value not recorded")
            return r.json()["token"]

        unused = await stream_token()
        connected_token = await stream_token()
        request = Request({"type": "http", "app": app, "headers": []})
        opened = await stream_messages("bob", request, connected_token)
        iterator = opened.body_iterator
        await anext(iterator)  # connected, before the identity is released
        store = app.state.participant_keys
        store.release("bob")
        successor = await token(store.claim("bob", kind="human"))
        r = await c.get("/rooms/audit/history", headers=bob)
        check("released key: existing JWT reads history", r.status_code == 401,
              f"HTTP {r.status_code}")
        r = await c.get("/rooms/audit/history", headers=agent)
        check("released parent: child JWT reads history", r.status_code == 401,
              f"HTTP {r.status_code}")
        r = await c.get("/rooms/audit/history", headers=successor)
        assert r.status_code == 200  # membership is by name; stream must be generation-safe
        try:
            replayed = await stream_messages("bob", request, unused)
        except HTTPException as exc:
            check("released key: pre-issued SSE token reconnects", exc.status_code == 401,
                  f"HTTP {exc.status_code}")
        else:
            check("released key: pre-issued SSE token reconnects", False, "stream accepted")
            await anext(replayed.body_iterator)
            await replayed.body_iterator.aclose()
        marker = "audit-successor-only"
        await c.post("/rooms/audit/messages", json={"from_name": "alice", "content": marker},
                     headers=admin)
        try:
            frame = await asyncio.wait_for(anext(iterator), 1)
            leaked = marker in frame
        except (StopAsyncIteration, asyncio.TimeoutError):
            leaked = False
        check("released key: old open SSE reads successor traffic", not leaked,
              "successor canary delivered" if leaked else "canary not delivered")
        await iterator.aclose()
        r = await c.post(f"/v1/join/invites/{inv}/revoke", headers=admin)
        check("invite revoke", r.status_code == 200, f"HTTP {r.status_code}")
    _reset_state()
    try:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
    except OSError as exc:
        rows.append({"command": "live relay / LAN exposure", "result": "BLOCKED",
                     "evidence": f"sandbox socket bind: {type(exc).__name__}, errno {exc.errno}"})
    return {"version": dist.version, "package_path": str(Path(quorus.__file__).resolve()),
            "wheel_sha256": wheel_sha,
            "record_hashes_checked": checked, "record_mismatches": mismatches,
            "transport": "ASGI plus direct StreamingResponse iteration; no live socket",
            "matrix": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(run(args.state.resolve(), args.wheel.resolve()))
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"version": result["version"], "checks": len(result["matrix"]),
                      "failed": [r["command"] for r in result["matrix"]
                                 if r["result"] == "FAIL"],
                      "record_mismatches": len(result["record_mismatches"])}))


if __name__ == "__main__":
    main()
