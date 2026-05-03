"""Tests for signup response shape — the default response must never
leak the raw API key; it should only appear when the caller opts in via
``X-Quorus-Setup-Local: 1``.

End-to-end signup needs a Postgres session, so these tests cover:
  1. The Pydantic schema — default-None ``api_key``/``setup_command``,
     required metadata fields so a future refactor can't silently drop
     them and leak the key into a free-form field.
  2. The source of ``quorus/auth/routes.py::signup`` — grep-level
     assertion that ``api_key`` and ``setup_command`` are gated on the
     ``X-Quorus-Setup-Local`` opt-in header.
"""
from __future__ import annotations

import inspect
import re
from types import SimpleNamespace

import pytest

from quorus.admin.models import ApiKey, Participant, Tenant
from quorus.auth import routes as auth_routes
from quorus.auth.routes import RegisterAgentRequest, RegisterAgentResponse, SignupResponse
from quorus.auth.tokens import generate_api_key

_RAW_KEY_PATTERN = re.compile(r"quorus_sk_[A-Za-z0-9]{10,}")


def test_signup_response_schema_defaults_api_key_to_none():
    resp = SignupResponse(
        tenant_slug="acme",
        participant_name="alice",
        key_prefix="quorus_sk_abcd",
        key_id="kid-1",
        next_steps="copy the key from the CLI output",
        relay_url="http://localhost:8080",
    )
    data = resp.model_dump()
    assert data["api_key"] is None
    assert data["setup_command"] is None


def test_signup_response_schema_populates_when_requested():
    resp = SignupResponse(
        tenant_slug="acme",
        participant_name="alice",
        key_prefix="quorus_sk_abcd",
        key_id="kid-1",
        next_steps="see setup_command",
        relay_url="http://localhost:8080",
        api_key="quorus_sk_abcdEFGH1234ijklMNOPqrst",
        setup_command="quorus init alice --api-key quorus_sk_abcdEFGH1234ijklMNOPqrst",
    )
    data = resp.model_dump()
    assert data["api_key"].startswith("quorus_sk_")
    assert "quorus_sk_" in data["setup_command"]


def test_signup_response_requires_key_prefix_and_id_and_next_steps():
    """Regression guard: if a future refactor drops these required fields,
    responses would shrink back toward a free-form shape where a raw key
    could slip in as the only useful field."""
    with pytest.raises(Exception):
        SignupResponse(
            tenant_slug="x",
            participant_name="y",
            relay_url="http://x",
        )  # type: ignore[call-arg]


def test_signup_response_json_has_no_raw_key_by_default():
    resp = SignupResponse(
        tenant_slug="acme",
        participant_name="alice",
        key_prefix="quorus_sk_publicprefix",
        key_id="kid-1",
        next_steps="see CLI output",
        relay_url="http://localhost:8080",
    )
    body = resp.model_dump_json()
    leaks = _RAW_KEY_PATTERN.findall(body)
    # The prefix is shorter than 10+ chars of body — must not match the raw
    # pattern. Any match means a full-length key got into the default body.
    for leak in leaks:
        assert leak == "quorus_sk_publicprefix", (
            f"non-prefix key-shaped string leaked: {leak}"
        )


def test_signup_source_gates_raw_key_on_setup_local_header():
    """The signup handler's source must reference X-Quorus-Setup-Local and
    must only assign the raw key/setup_command when that flag is true."""
    source = inspect.getsource(auth_routes.signup)
    assert "X-Quorus-Setup-Local" in source or "x_quorus_setup_local" in source, (
        "signup handler must read the opt-in header"
    )
    assert "is_local_setup" in source or "x_quorus_setup_local" in source, (
        "handler must have a local-setup branch"
    )
    # The raw key must not appear unconditionally — require both appearances
    # to flow through a conditional expression.
    assert "if is_local_setup" in source or "if x_quorus_setup_local" in source, (
        "raw key must be gated on the opt-in header"
    )


def test_signup_response_model_optional_fields_default_to_none():
    """Guard against a default like `api_key: str = ""` that would serialise
    the empty string but still open the door to accidental population."""
    model_fields = SignupResponse.model_fields
    for field_name in ("api_key", "setup_command"):
        field = model_fields[field_name]
        assert field.default is None, (
            f"{field_name} must default to None, got {field.default!r}"
        )


def test_register_agent_response_includes_token_exchange_hint():
    resp = RegisterAgentResponse(
        agent_name="arav-codex",
        api_key="quorus_sk_abcdEFGH1234ijklMNOPqrst",
        tenant_slug="medbuddy",
        next_step="Exchange api_key via POST /v1/auth/token before using relay routes.",
    )

    assert "/v1/auth/token" in resp.next_step


def test_register_agent_inherit_rooms_validation():
    assert RegisterAgentRequest(suffix="codex").inherit_rooms == "public"
    assert RegisterAgentRequest(suffix="codex", inherit_rooms="all").inherit_rooms == "all"
    assert RegisterAgentRequest(suffix="codex", inherit_rooms="none").inherit_rooms == "none"
    with pytest.raises(ValueError):
        RegisterAgentRequest(suffix="codex", inherit_rooms="private")


@pytest.mark.asyncio
async def test_register_agent_creates_peer_in_parent_tenant(monkeypatch):
    parent_raw_key, parent_prefix, parent_key_hash = generate_api_key()
    parent_tenant = Tenant(id="tenant-parent", slug="medbuddy", display_name="MedBuddy")
    parent_participant = Participant(
        id="participant-parent",
        tenant_id=parent_tenant.id,
        name="arav-codex",
        role="admin",
    )
    parent_key = ApiKey(
        id="key-parent",
        participant_id=parent_participant.id,
        label="parent",
        key_prefix=parent_prefix,
        key_hash=parent_key_hash,
    )
    added = []

    class FakeResult:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class FakeSession:
        def __init__(self):
            self.execute_calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def execute(self, query):
            self.execute_calls += 1
            if self.execute_calls == 1:
                return FakeResult(parent_key)
            return FakeResult(None)

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

    fake_session = FakeSession()
    monkeypatch.setattr(auth_routes, "get_db_session", lambda: fake_session)

    resp = await auth_routes.register_agent(
        RegisterAgentRequest(suffix="claude-1m"),
        request=SimpleNamespace(),
        authorization=f"Bearer {parent_raw_key}",
    )

    created_participant = next(obj for obj in added if isinstance(obj, Participant))
    created_key = next(obj for obj in added if isinstance(obj, ApiKey))

    assert created_participant.name == "arav-codex-claude-1m"
    assert created_participant.tenant_id == parent_tenant.id
    assert created_participant.role == "agent"
    assert created_key.participant_id == created_participant.id
    assert resp.agent_name == "arav-codex-claude-1m"
    assert resp.tenant_slug == parent_tenant.slug
    assert "/v1/auth/token" in resp.next_step


@pytest.mark.asyncio
async def test_register_agent_auto_joins_public_parent_rooms(monkeypatch):
    parent_raw_key, parent_prefix, parent_key_hash = generate_api_key()
    parent_tenant = Tenant(id="tenant-parent", slug="medbuddy", display_name="MedBuddy")
    parent_participant = Participant(
        id="participant-parent",
        tenant_id=parent_tenant.id,
        name="arav-codex",
        role="admin",
    )
    parent_key = ApiKey(
        id="key-parent",
        participant_id=parent_participant.id,
        label="parent",
        key_prefix=parent_prefix,
        key_hash=parent_key_hash,
    )
    added = []
    room_members = []
    participants = []

    class FakeResult:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class FakeSession:
        def __init__(self):
            self.execute_calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def execute(self, query):
            self.execute_calls += 1
            if self.execute_calls == 1:
                return FakeResult(parent_key)
            return FakeResult(None)

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

    class FakeRoomService:
        async def list_for_member(self, tenant_id, member_name):
            assert tenant_id == parent_tenant.id
            assert member_name == parent_participant.name
            return [
                {"id": "room-1", "name": "sprint", "private": "False"},
                {"id": "room-2", "name": "demo", "private": "True"},
            ]

    class FakeRoomsBackend:
        async def add_member(self, tenant_id, room_id, name, role):
            room_members.append((tenant_id, room_id, name, role))

    class FakeParticipantsBackend:
        async def add(self, tenant_id, name):
            participants.append((tenant_id, name))

    fake_session = FakeSession()
    monkeypatch.setattr(auth_routes, "get_db_session", lambda: fake_session)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                room_service=FakeRoomService(),
                backends=SimpleNamespace(
                    rooms=FakeRoomsBackend(),
                    participants=FakeParticipantsBackend(),
                ),
            ),
        ),
    )

    resp = await auth_routes.register_agent(
        RegisterAgentRequest(suffix="claude-1m"),
        request=request,
        authorization=f"Bearer {parent_raw_key}",
    )

    assert resp.agent_name == "arav-codex-claude-1m"
    assert room_members == [
        (parent_tenant.id, "room-1", "arav-codex-claude-1m", "agent"),
    ]
    assert participants == [(parent_tenant.id, "arav-codex-claude-1m")]


@pytest.mark.asyncio
async def test_register_agent_can_explicitly_inherit_private_rooms(monkeypatch):
    parent_raw_key, parent_prefix, parent_key_hash = generate_api_key()
    parent_tenant = Tenant(id="tenant-parent", slug="medbuddy", display_name="MedBuddy")
    parent_participant = Participant(
        id="participant-parent",
        tenant_id=parent_tenant.id,
        name="arav-codex",
        role="admin",
    )
    parent_key = ApiKey(
        id="key-parent",
        participant_id=parent_participant.id,
        label="parent",
        key_prefix=parent_prefix,
        key_hash=parent_key_hash,
    )
    added = []
    room_members = []

    class FakeResult:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class FakeSession:
        def __init__(self):
            self.execute_calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def execute(self, query):
            self.execute_calls += 1
            if self.execute_calls == 1:
                return FakeResult(parent_key)
            return FakeResult(None)

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

    class FakeRoomService:
        async def list_for_member(self, tenant_id, member_name):
            return [
                {"id": "room-1", "name": "sprint", "private": False},
                {"id": "room-2", "name": "sensitive", "private": True},
            ]

    class FakeRoomsBackend:
        async def add_member(self, tenant_id, room_id, name, role):
            room_members.append((tenant_id, room_id, name, role))

    class FakeParticipantsBackend:
        async def add(self, tenant_id, name):
            return None

    monkeypatch.setattr(auth_routes, "get_db_session", lambda: FakeSession())
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                room_service=FakeRoomService(),
                backends=SimpleNamespace(
                    rooms=FakeRoomsBackend(),
                    participants=FakeParticipantsBackend(),
                ),
            ),
        ),
    )

    resp = await auth_routes.register_agent(
        RegisterAgentRequest(suffix="claude-1m", inherit_rooms="all"),
        request=request,
        authorization=f"Bearer {parent_raw_key}",
    )

    assert resp.agent_name == "arav-codex-claude-1m"
    assert room_members == [
        (parent_tenant.id, "room-1", "arav-codex-claude-1m", "agent"),
        (parent_tenant.id, "room-2", "arav-codex-claude-1m", "agent"),
    ]


@pytest.mark.asyncio
async def test_register_agent_rotates_existing_keys_without_500(monkeypatch):
    """Regression: production hit MultipleResultsFound 500 when an agent
    accumulated multiple unrevoked keys from prior register-agent calls.
    Old code used scalar_one_or_none() which crashed on duplicates. New
    code returns ALL matches, revokes them, and mints a single fresh key.

    Repro path: register-agent agent_X → mints key_1. Register-agent
    agent_X again → mints key_2 (key_1 left active). Third call →
    scalar_one_or_none() with 2 active rows → 500. Fixed by listing all
    + revoking before mint.
    """
    from datetime import timezone

    parent_raw_key, parent_prefix, parent_key_hash = generate_api_key()
    parent_tenant = Tenant(id="tenant-parent", slug="medbuddy", display_name="MedBuddy")
    parent_participant = Participant(
        id="participant-parent",
        tenant_id=parent_tenant.id,
        name="arav-codex",
        role="admin",
    )
    parent_key = ApiKey(
        id="key-parent",
        participant_id=parent_participant.id,
        label="parent",
        key_prefix=parent_prefix,
        key_hash=parent_key_hash,
    )
    existing_agent = Participant(
        id="participant-child",
        tenant_id=parent_tenant.id,
        name="arav-codex-claude-1m",
        role="agent",
    )
    # Two pre-existing unrevoked keys — exactly the state that 500'd in prod.
    old_key_1 = ApiKey(
        id="key-old-1",
        participant_id=existing_agent.id,
        label="old-1",
        key_prefix="old1prefix",
        key_hash="old1hash",
        revoked_at=None,
    )
    old_key_2 = ApiKey(
        id="key-old-2",
        participant_id=existing_agent.id,
        label="old-2",
        key_prefix="old2prefix",
        key_hash="old2hash",
        revoked_at=None,
    )
    added = []

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
                # parent key lookup
                return FakeResult(single=parent_key)
            if self.calls == 2:
                # existing agent lookup
                return FakeResult(single=existing_agent)
            # Active-key lookup — returns BOTH unrevoked keys (the
            # production state that previously crashed).
            return FakeResult(many=[old_key_1, old_key_2])

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

    fake_session = FakeSession()
    monkeypatch.setattr(auth_routes, "get_db_session", lambda: fake_session)

    # Must NOT raise MultipleResultsFound or any other exception.
    resp = await auth_routes.register_agent(
        RegisterAgentRequest(suffix="claude-1m"),
        request=SimpleNamespace(),
        authorization=f"Bearer {parent_raw_key}",
    )

    # Both old keys must now be revoked.
    assert old_key_1.revoked_at is not None
    assert old_key_2.revoked_at is not None
    assert old_key_1.revoked_at.tzinfo is timezone.utc
    # A fresh key must have been minted (it's in `added`).
    new_keys = [obj for obj in added if isinstance(obj, ApiKey)]
    assert len(new_keys) == 1
    assert new_keys[0].participant_id == existing_agent.id
    # Response carries a working raw api_key + the next_step hint.
    assert resp.agent_name == "arav-codex-claude-1m"
    assert resp.api_key.startswith("mct_") or len(resp.api_key) > 20
    assert "/v1/auth/token" in resp.next_step
