"""Joining an existing workspace via signup needs proof of invitation.

Pre-launch audit (2026-10-16): ``POST /v1/auth/signup`` with a tenant UUID
(public: it is in every join code and JWT) and an EXISTING name minted and
returned a fresh API key for that participant, the workspace admin included.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from quorus.admin.models import ApiKey, Participant, Tenant
from quorus.auth import routes as auth_routes
from quorus.auth.routes import SignupRequest
from quorus.services.invite_svc import InviteService

TENANT_ID = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"
INVITE_SECRET = "i" * 40


def _setup(monkeypatch, existing: Participant | None):
    tenant = Tenant(id=TENANT_ID, slug="victimco", display_name="VictimCo")
    added: list = []

    class Result:
        def scalar_one_or_none(self):
            return existing

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, model, oid):
            return tenant if model is Tenant and oid == TENANT_ID else None

        async def execute(self, q):
            return Result()

        def add(self, o):
            added.append(o)

        async def flush(self):
            return None

    class RateLimit:
        async def check_with_limit(self, *a, **k):
            return True

    monkeypatch.setattr(auth_routes, "get_db_session", lambda: Session())
    invites = InviteService(secret=INVITE_SECRET)
    state = SimpleNamespace(rate_limit_service=RateLimit(), invite_service=invites)

    def request(headers: dict | None = None):
        return SimpleNamespace(
            headers=headers or {}, url=SimpleNamespace(scheme="https", netloc="relay"),
            client=SimpleNamespace(host="6.6.6.6"), app=SimpleNamespace(state=state),
        )

    return added, invites, request


def _admin() -> Participant:
    return Participant(id="p-admin", tenant_id=TENANT_ID, name="arav", role="admin")


async def test_unauthenticated_join_is_refused(monkeypatch) -> None:
    added, _inv, request = _setup(monkeypatch, _admin())
    with pytest.raises(HTTPException) as exc:
        await auth_routes.signup(
            SignupRequest(name="arav", workspace="ws", join_tenant_id=TENANT_ID),
            request(), x_quorus_setup_local="1",
        )
    assert exc.value.status_code == 403
    assert not any(isinstance(o, ApiKey) for o in added)


async def test_invite_cannot_rekey_existing_participant(monkeypatch) -> None:
    added, invites, request = _setup(monkeypatch, _admin())
    token = invites.create_token(TENANT_ID, "room-1", issuer="arav")
    with pytest.raises(HTTPException) as exc:
        await auth_routes.signup(
            SignupRequest(name="arav", workspace="ws", join_tenant_id=TENANT_ID,
                          invite_token=token),
            request(), x_quorus_setup_local="1",
        )
    assert exc.value.status_code == 409
    assert not any(isinstance(o, ApiKey) for o in added)


async def test_invite_for_other_tenant_is_refused(monkeypatch) -> None:
    _added, invites, request = _setup(monkeypatch, None)
    token = invites.create_token(OTHER_TENANT, "room-1", issuer="eve")
    with pytest.raises(HTTPException) as exc:
        await auth_routes.signup(
            SignupRequest(name="newbie", workspace="ws", join_tenant_id=TENANT_ID,
                          invite_token=token),
            request(), x_quorus_setup_local="1",
        )
    assert exc.value.status_code == 403


async def test_valid_invite_creates_new_member(monkeypatch) -> None:
    added, invites, request = _setup(monkeypatch, None)
    token = invites.create_token(TENANT_ID, "room-1", issuer="arav")
    resp = await auth_routes.signup(
        SignupRequest(name="newbie", workspace="ws", join_tenant_id=TENANT_ID,
                      invite_token=token),
        request(), x_quorus_setup_local="1",
    )
    assert resp.participant_name == "newbie" and resp.api_key
    member = next(o for o in added if isinstance(o, Participant))
    assert member.role == "member"


async def test_workspace_admin_jwt_may_rekey(monkeypatch) -> None:
    from quorus.auth.tokens import create_jwt

    _added, _inv, request = _setup(monkeypatch, _admin())
    jwt = create_jwt(sub="arav", tenant_id=TENANT_ID, tenant_slug="victimco", role="admin")
    resp = await auth_routes.signup(
        SignupRequest(name="arav", workspace="ws", join_tenant_id=TENANT_ID),
        request({"Authorization": f"Bearer {jwt}"}), x_quorus_setup_local="1",
    )
    assert resp.participant_name == "arav"
