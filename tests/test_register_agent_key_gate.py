"""F1.3 — register-agent must not return the raw api_key by default.

The raw key is gated on the same ``X-Quorus-Setup-Local: 1`` opt-in header
as signup (A4 pattern). The default response carries only ``key_prefix``,
``key_id``, and ``next_step`` metadata.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from quorus.admin.models import ApiKey, Participant, Tenant
from quorus.auth import routes as auth_routes
from quorus.auth.routes import RegisterAgentRequest, RegisterAgentResponse
from quorus.auth.tokens import generate_api_key

# Raw keys look like mct_{prefix}_{secret} — a bare prefix never matches.
_RAW_KEY_PATTERN = re.compile(r"mct_[0-9a-f]+_[0-9a-f]+")


def _fixtures():
    parent_raw_key, parent_prefix, parent_key_hash = generate_api_key()
    tenant = Tenant(id="tenant-gate", slug="medbuddy", display_name="MedBuddy")
    participant = Participant(
        id="participant-gate", tenant_id=tenant.id, name="arav", role="admin",
    )
    key = ApiKey(
        id="key-gate",
        participant_id=participant.id,
        label="parent",
        key_prefix=parent_prefix,
        key_hash=parent_key_hash,
    )
    return parent_raw_key, tenant, participant, key


def _make_session(parent_key, parent_participant, parent_tenant, extra_keys=None):
    """FakeSession for the create path (or rotation path via extra_keys)."""
    added: list = []

    class FakeScalars:
        def __init__(self, items):
            self.items = list(items)

        def __iter__(self):
            return iter(self.items)

    class FakeResult:
        def __init__(self, single=None, many=None):
            self.single = single
            self.many = many

        def scalar_one_or_none(self):
            return self.single

        def scalars(self):
            return FakeScalars(self.many or [])

    class FakeSession:
        def __init__(self):
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def execute(self, query):
            self.calls += 1
            if self.calls == 1:
                return FakeResult(single=parent_key)
            if extra_keys is not None:
                if self.calls == 2:
                    return FakeResult(single=extra_keys["agent"])
                return FakeResult(many=extra_keys["keys"])
            return FakeResult(single=None)

        async def get(self, model, obj_id):
            if model is Participant and obj_id == parent_participant.id:
                return parent_participant
            if model is Tenant and obj_id == parent_tenant.id:
                return parent_tenant
            return None

        def add(self, obj):
            added.append(obj)

        async def flush(self):
            return None

    return FakeSession(), added


@pytest.mark.asyncio
async def test_default_response_withholds_raw_key(monkeypatch):
    parent_raw_key, tenant, participant, key = _fixtures()
    session, added = _make_session(key, participant, tenant)
    monkeypatch.setattr(auth_routes, "get_db_session", lambda: session)

    resp = await auth_routes.register_agent(
        RegisterAgentRequest(suffix="claude"),
        request=SimpleNamespace(),
        authorization=f"Bearer {parent_raw_key}",
        x_quorus_setup_local=None,
    )

    assert resp.api_key is None
    assert resp.key_prefix, "default response must include key_prefix"
    assert resp.key_id, "default response must include key_id"
    assert "/v1/auth/token" in resp.next_step
    # No raw-key-shaped string anywhere in the serialized body.
    body = resp.model_dump_json()
    assert not _RAW_KEY_PATTERN.search(body), (
        f"raw key leaked into default response body: {body}"
    )


@pytest.mark.asyncio
async def test_setup_local_header_returns_raw_key(monkeypatch):
    parent_raw_key, tenant, participant, key = _fixtures()
    session, added = _make_session(key, participant, tenant)
    monkeypatch.setattr(auth_routes, "get_db_session", lambda: session)

    resp = await auth_routes.register_agent(
        RegisterAgentRequest(suffix="claude"),
        request=SimpleNamespace(),
        authorization=f"Bearer {parent_raw_key}",
        x_quorus_setup_local="1",
    )

    assert resp.api_key is not None
    assert resp.api_key.startswith("mct_")
    # Metadata still present alongside the opt-in raw key.
    assert resp.key_prefix
    assert resp.key_id


@pytest.mark.asyncio
async def test_rotation_path_is_gated_too(monkeypatch):
    """The existing-agent (key rotation) branch must apply the same gate."""
    parent_raw_key, tenant, participant, key = _fixtures()
    existing_agent = Participant(
        id="participant-child", tenant_id=tenant.id,
        name="arav-claude", role="agent",
    )
    old_key = ApiKey(
        id="key-old",
        participant_id=existing_agent.id,
        label="old",
        key_prefix="oldprefix",
        key_hash="oldhash",
        revoked_at=None,
    )
    session, added = _make_session(
        key, participant, tenant,
        extra_keys={"agent": existing_agent, "keys": [old_key]},
    )
    monkeypatch.setattr(auth_routes, "get_db_session", lambda: session)

    resp = await auth_routes.register_agent(
        RegisterAgentRequest(suffix="claude"),
        request=SimpleNamespace(),
        authorization=f"Bearer {parent_raw_key}",
        x_quorus_setup_local=None,
    )

    assert resp.api_key is None
    assert resp.key_prefix
    assert resp.key_id
    assert not _RAW_KEY_PATTERN.search(resp.model_dump_json())
    # Rotation still happened even though the raw key was withheld.
    assert old_key.revoked_at is not None


def test_response_model_api_key_defaults_to_none():
    """Guard against a default like ``api_key: str = ""`` regressing."""
    field = RegisterAgentResponse.model_fields["api_key"]
    assert field.default is None


def test_register_agent_source_gates_raw_key_on_setup_local_header():
    """Grep-level parity with the signup A4 guard."""
    import inspect

    source = inspect.getsource(auth_routes.register_agent)
    assert "x_quorus_setup_local" in source
    assert "is_local_setup" in source
    assert "if is_local_setup" in source
