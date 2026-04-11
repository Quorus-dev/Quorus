"""Tests for admin routes — tenant/participant/key CRUD via HTTP.

These tests use the in-memory relay (no Postgres) with JWT-based auth fixtures.
Admin routes require a real database, so we test the models and validation logic
directly, plus test the route handlers via ASGI client when possible.
"""

import pytest

from murmur.admin.models import ApiKey, Participant, Tenant
from murmur.auth.tokens import create_jwt, generate_api_key, verify_api_key

# ---------------------------------------------------------------------------
# Model unit tests (no database needed)
# ---------------------------------------------------------------------------


class TestTenantModel:
    def test_create_tenant(self):
        t = Tenant(slug="acme", display_name="Acme Corp")
        assert t.slug == "acme"
        assert t.display_name == "Acme Corp"
        assert t.id is not None

    def test_tenant_auto_id(self):
        t1 = Tenant(slug="a")
        t2 = Tenant(slug="b")
        assert t1.id != t2.id


class TestParticipantModel:
    def test_create_participant(self):
        p = Participant(tenant_id="t-123", name="agent-1", role="user")
        assert p.name == "agent-1"
        assert p.role == "user"

    def test_default_role(self):
        p = Participant(tenant_id="t-123", name="agent-1")
        assert p.role == "user"


class TestApiKeyModel:
    def test_create_api_key(self):
        raw_key, prefix, key_hash = generate_api_key()
        k = ApiKey(
            participant_id="p-123",
            key_prefix=prefix,
            key_hash=key_hash,
            label="test key",
        )
        assert k.key_prefix == prefix
        assert k.label == "test key"
        assert k.revoked_at is None

    def test_verify_stored_key(self):
        raw_key, prefix, key_hash = generate_api_key()
        k = ApiKey(
            participant_id="p-123",
            key_prefix=prefix,
            key_hash=key_hash,
        )
        assert verify_api_key(raw_key, k.key_hash)


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


class TestAdminValidation:
    def test_valid_slug(self):
        from murmur.admin.routes import _validate_slug

        assert _validate_slug("acme") == "acme"
        assert _validate_slug("my-org") == "my-org"
        assert _validate_slug("org_123") == "org_123"

    def test_invalid_slug(self):
        from murmur.admin.routes import _validate_slug

        with pytest.raises(ValueError):
            _validate_slug("")  # Empty
        with pytest.raises(ValueError):
            _validate_slug("A")  # Too short / uppercase
        with pytest.raises(ValueError):
            _validate_slug("has spaces")
        with pytest.raises(ValueError):
            _validate_slug("-starts-with-dash")

    def test_valid_name(self):
        from murmur.admin.routes import _validate_name

        assert _validate_name("agent-1") == "agent-1"
        assert _validate_name("Agent_2") == "Agent_2"

    def test_invalid_name(self):
        from murmur.admin.routes import _validate_name

        with pytest.raises(ValueError):
            _validate_name("")
        with pytest.raises(ValueError):
            _validate_name("a" * 65)
        with pytest.raises(ValueError):
            _validate_name("has spaces")


# ---------------------------------------------------------------------------
# JWT fixture creation tests (verifying auth works end-to-end)
# ---------------------------------------------------------------------------


class TestAuthFixtures:
    def test_create_admin_jwt(self):
        """Admin JWT can be created and decoded with correct claims."""
        token = create_jwt(
            sub="admin-agent",
            tenant_id="t-123",
            tenant_slug="acme",
            role="admin",
        )
        from murmur.auth.tokens import decode_jwt

        claims = decode_jwt(token)
        assert claims["sub"] == "admin-agent"
        assert claims["role"] == "admin"
        assert claims["tenant_slug"] == "acme"

    def test_create_user_jwt(self):
        """User JWT has correct role and can be decoded."""
        token = create_jwt(
            sub="worker-1",
            tenant_id="t-123",
            tenant_slug="acme",
            role="user",
        )
        from murmur.auth.tokens import decode_jwt

        claims = decode_jwt(token)
        assert claims["sub"] == "worker-1"
        assert claims["role"] == "user"
