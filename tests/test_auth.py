"""Tests for auth layer — JWT create/decode, API key generate/hash/verify, middleware."""

from unittest.mock import MagicMock

import pytest

from quorus.auth.tokens import (
    create_jwt,
    decode_jwt,
    extract_key_prefix,
    generate_api_key,
    hash_api_key,
    verify_api_key,
)

# ---------------------------------------------------------------------------
# JWT tests
# ---------------------------------------------------------------------------


class TestJWT:
    @pytest.fixture(autouse=True)
    def _pin_jwt_secret(self, monkeypatch):
        """Ensure JWT_SECRET is deterministic regardless of test ordering."""
        monkeypatch.setattr(
            "quorus.auth.tokens.JWT_SECRET",
            "test-jwt-secret-that-is-at-least-32-bytes-long",
        )

    def test_create_and_decode(self):
        token = create_jwt(
            sub="agent-1",
            tenant_id="t-123",
            tenant_slug="acme",
            role="user",
        )
        claims = decode_jwt(token)
        assert claims["sub"] == "agent-1"
        assert claims["tenant_id"] == "t-123"
        assert claims["tenant_slug"] == "acme"
        assert claims["role"] == "user"

    def test_admin_role(self):
        token = create_jwt(
            sub="admin-agent",
            tenant_id="t-123",
            tenant_slug="acme",
            role="admin",
        )
        claims = decode_jwt(token)
        assert claims["role"] == "admin"

    def test_custom_ttl(self):
        token = create_jwt(
            sub="agent-1",
            tenant_id="t-123",
            tenant_slug="acme",
            ttl=60,
        )
        claims = decode_jwt(token)
        assert claims["exp"] - claims["iat"] == 60

    def test_extra_claims(self):
        token = create_jwt(
            sub="agent-1",
            tenant_id="t-123",
            tenant_slug="acme",
            extra={"custom": "value"},
        )
        claims = decode_jwt(token)
        assert claims["custom"] == "value"

    def test_expired_token_fails(self):
        import jwt

        token = create_jwt(
            sub="agent-1",
            tenant_id="t-123",
            tenant_slug="acme",
            ttl=-1,  # Already expired
        )
        with pytest.raises(jwt.ExpiredSignatureError):
            decode_jwt(token)

    def test_tampered_token_fails(self):
        import jwt

        token = create_jwt(
            sub="agent-1",
            tenant_id="t-123",
            tenant_slug="acme",
        )
        # Corrupt the signature portion (after last dot)
        parts = token.rsplit(".", 1)
        tampered = parts[0] + ".AAAA_tampered_signature_AAAA"
        with pytest.raises(jwt.InvalidTokenError):
            decode_jwt(tampered)

    def test_iss_aud_jti_present(self):
        token = create_jwt(
            sub="agent-1",
            tenant_id="t-123",
            tenant_slug="acme",
        )
        claims = decode_jwt(token)
        assert claims["iss"] == "quorus"
        assert claims["aud"] == "quorus-relay"
        assert "jti" in claims
        assert len(claims["jti"]) == 32  # hex(16 bytes)

    def test_wrong_issuer_rejected(self):
        import jwt as pyjwt

        # Craft a token with wrong issuer
        from quorus.auth.tokens import JWT_AUDIENCE, _get_jwt_secret

        payload = {
            "sub": "agent-1",
            "iss": "not-quorus",
            "aud": JWT_AUDIENCE,
        }
        token = pyjwt.encode(payload, _get_jwt_secret(), algorithm="HS256")
        with pytest.raises(pyjwt.InvalidIssuerError):
            decode_jwt(token)

    def test_wrong_audience_rejected(self):
        import jwt as pyjwt

        from quorus.auth.tokens import JWT_ISSUER, _get_jwt_secret

        payload = {
            "sub": "agent-1",
            "iss": JWT_ISSUER,
            "aud": "wrong-audience",
        }
        token = pyjwt.encode(payload, _get_jwt_secret(), algorithm="HS256")
        with pytest.raises(pyjwt.InvalidAudienceError):
            decode_jwt(token)

    def test_garbage_token_fails(self):
        import jwt

        with pytest.raises(jwt.InvalidTokenError):
            decode_jwt("not-a-jwt")


# ---------------------------------------------------------------------------
# API key tests
# ---------------------------------------------------------------------------


class TestAPIKey:
    def test_generate_format(self):
        raw_key, prefix, key_hash = generate_api_key()
        assert raw_key.startswith("mct_")
        parts = raw_key.split("_", 2)
        assert len(parts) == 3
        assert parts[1] == prefix
        assert len(prefix) <= 16

    def test_verify_correct_key(self):
        raw_key, prefix, key_hash = generate_api_key()
        assert verify_api_key(raw_key, key_hash)

    def test_verify_wrong_key(self):
        _, _, key_hash = generate_api_key()
        assert not verify_api_key("mct_wrong_key12345678901234567890", key_hash)

    def test_hash_deterministic_per_key(self):
        raw_key, _, _ = generate_api_key()
        hash1 = hash_api_key(raw_key)
        # bcrypt hashes differ each time (different salt), but both verify
        assert verify_api_key(raw_key, hash1)

    def test_extract_prefix(self):
        raw_key, prefix, _ = generate_api_key()
        assert extract_key_prefix(raw_key) == prefix

    def test_extract_prefix_invalid_format(self):
        with pytest.raises(ValueError, match="Invalid API key format"):
            extract_key_prefix("not-a-valid-key")

    def test_extract_prefix_wrong_prefix(self):
        with pytest.raises(ValueError, match="Invalid API key format"):
            extract_key_prefix("xyz_abc_def")

    def test_unique_keys(self):
        keys = [generate_api_key() for _ in range(10)]
        raw_keys = {k[0] for k in keys}
        prefixes = {k[1] for k in keys}
        assert len(raw_keys) == 10
        assert len(prefixes) == 10


# ---------------------------------------------------------------------------
# Middleware tests
# ---------------------------------------------------------------------------


class TestAuthMiddleware:
    @pytest.fixture
    def jwt_token(self):
        return create_jwt(
            sub="test-agent",
            tenant_id="t-123",
            tenant_slug="acme",
            role="user",
        )

    @pytest.fixture
    def admin_jwt_token(self):
        return create_jwt(
            sub="admin-agent",
            tenant_id="t-123",
            tenant_slug="acme",
            role="admin",
        )

    def _make_request(self, auth_header: str = "") -> MagicMock:
        request = MagicMock()
        request.headers = {"Authorization": auth_header} if auth_header else {}
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        return request

    @pytest.mark.asyncio
    async def test_jwt_auth(self, jwt_token):
        from quorus.auth.middleware import verify_auth

        request = self._make_request(f"Bearer {jwt_token}")
        ctx = await verify_auth(request)
        assert ctx.sub == "test-agent"
        assert ctx.tenant_id == "t-123"
        assert ctx.role == "user"
        assert not ctx.is_legacy

    @pytest.mark.asyncio
    async def test_legacy_auth(self):
        from quorus.auth.middleware import verify_auth

        request = self._make_request("Bearer test-secret")
        ctx = await verify_auth(request)
        assert ctx.sub is None
        assert ctx.role == "admin"
        assert ctx.is_legacy

    @pytest.mark.asyncio
    async def test_no_auth_fails(self):
        from fastapi import HTTPException

        from quorus.auth.middleware import verify_auth

        request = self._make_request("")
        with pytest.raises(HTTPException) as exc_info:
            await verify_auth(request)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_token_fails(self):
        from fastapi import HTTPException

        from quorus.auth.middleware import verify_auth

        request = self._make_request("Bearer invalid-token")
        with pytest.raises(HTTPException) as exc_info:
            await verify_auth(request)
        assert exc_info.value.status_code == 401


class TestIdentityEnforcement:
    def test_require_identity_matches(self):
        from quorus.auth.middleware import AuthContext, require_identity

        ctx = AuthContext(sub="agent-1", role="user")
        require_identity(ctx, "agent-1")  # Should not raise

    def test_require_identity_mismatch(self):
        from fastapi import HTTPException

        from quorus.auth.middleware import AuthContext, require_identity

        ctx = AuthContext(sub="agent-1", role="user")
        with pytest.raises(HTTPException) as exc_info:
            require_identity(ctx, "agent-2")
        assert exc_info.value.status_code == 403

    def test_require_identity_legacy_skips(self):
        from quorus.auth.middleware import AuthContext, require_identity

        ctx = AuthContext(sub=None, is_legacy=True, role="admin")
        require_identity(ctx, "any-name")  # Should not raise

    def test_require_role_admin(self):
        from quorus.auth.middleware import AuthContext, require_role

        ctx = AuthContext(sub="admin", role="admin")
        require_role(ctx, "admin")  # Should not raise

    def test_require_role_mismatch(self):
        from fastapi import HTTPException

        from quorus.auth.middleware import AuthContext, require_role

        ctx = AuthContext(sub="user", role="user")
        with pytest.raises(HTTPException) as exc_info:
            require_role(ctx, "admin")
        assert exc_info.value.status_code == 403

    def test_require_role_legacy_skips(self):
        from quorus.auth.middleware import AuthContext, require_role

        ctx = AuthContext(sub=None, is_legacy=True, role="admin")
        require_role(ctx, "admin")  # Should not raise
