# Design: B2C API Keys for Managed Service

> Status: Draft  
> Author: aarya (Claude agent)  
> Date: 2026-04-12

## Overview

Move from the current name+shared-secret auth model to a proper B2C API key system suitable for a managed service offering.

## Current State

- Auth: `Authorization: Bearer <RELAY_SECRET>` — single shared secret for all users
- Identity: `from_name` field in messages — user-chosen, not verified
- No account management, no key rotation, no per-user rate limits

## Goals

1. **Immutable user identities** — UUID-based account IDs that never change
2. **API key lifecycle** — create, list, revoke, rotate keys
3. **Per-key rate limits** — different tiers (free, pro, enterprise)
4. **Audit trail** — track which key made which request

## Non-Goals (v1)

- OAuth/SSO integration
- Team/org hierarchy
- Billing integration

---

## Data Model

### Account

```
accounts:
  id: UUID (immutable)
  email: string (unique, verified)
  display_name: string
  tier: free | pro | enterprise
  created_at: timestamp
  last_seen: timestamp
```

### API Key

```
api_keys:
  id: UUID
  account_id: UUID (FK → accounts)
  key_hash: string (bcrypt hash of the key)
  key_prefix: string (first 8 chars for display, e.g., "mk_abc123...")
  name: string (user-provided label)
  scopes: [string] (e.g., ["rooms:read", "rooms:write", "messages:send"])
  rate_limit: int (requests per minute, overrides account tier if set)
  created_at: timestamp
  last_used_at: timestamp
  expires_at: timestamp | null
  revoked_at: timestamp | null
```

---

## API Endpoints

### Account Management

```
POST   /v1/accounts              # Create account (email + password or magic link)
GET    /v1/accounts/me           # Get current account
PATCH  /v1/accounts/me           # Update display name, etc.
DELETE /v1/accounts/me           # Delete account (cascades to keys)
```

### API Key Management

```
POST   /v1/api-keys              # Create new key (returns full key ONCE)
GET    /v1/api-keys              # List keys (prefix only, not full key)
GET    /v1/api-keys/{id}         # Get key metadata
PATCH  /v1/api-keys/{id}         # Update name, scopes, rate_limit
DELETE /v1/api-keys/{id}         # Revoke key
POST   /v1/api-keys/{id}/rotate  # Rotate key (returns new key, old expires in 24h)
```

### Auth Header Format

```
Authorization: Bearer mk_abc123...full_key_here
```

Key format: `mk_` prefix + 32 random bytes base64url encoded = ~48 chars total.

---

## Rate Limiting

### Tiers

| Tier       | Requests/min | Rooms | Members/room | History retention |
|------------|--------------|-------|--------------|-------------------|
| Free       | 60           | 5     | 10           | 7 days            |
| Pro        | 600          | 50    | 100          | 30 days           |
| Enterprise | 6000         | 500   | 1000         | 90 days           |

### Per-Key Override

Keys can have a custom `rate_limit` that overrides the account tier. Useful for:
- CI/CD bots that need higher limits
- Testing keys with lower limits
- Temporary burst capacity

---

## Migration Path

### Phase 1: Add new auth (backward compatible)

1. Add accounts + api_keys tables
2. Accept both `Bearer <legacy_secret>` and `Bearer mk_...` 
3. Legacy requests tagged with `account_id = "legacy"`
4. New `/v1/accounts` and `/v1/api-keys` endpoints

### Phase 2: Migrate users

1. Prompt existing users to create accounts
2. Generate API keys for them
3. Update their configs via `murmur init --upgrade`

### Phase 3: Deprecate legacy

1. Warn on legacy auth usage
2. Set sunset date
3. Remove legacy auth support

---

## Security Considerations

1. **Key storage**: Only store bcrypt hash, never the raw key
2. **Key display**: Only show prefix (`mk_abc123...`) after creation
3. **Rate limit bypass**: Per-IP rate limit on auth failures (prevent brute force)
4. **Key rotation**: Old key works for 24h grace period during rotation
5. **Audit log**: Log key usage with IP, endpoint, timestamp

---

## Implementation Checklist

- [ ] Add Postgres tables: `accounts`, `api_keys`
- [ ] Add Redis keys for rate limiting: `rl:key:{key_id}:minute`
- [ ] Implement key generation: `mk_` + 32 bytes base64url
- [ ] Implement bcrypt hashing on create
- [ ] Update auth middleware to accept both legacy and new keys
- [ ] Add `/v1/accounts` CRUD endpoints
- [ ] Add `/v1/api-keys` CRUD endpoints
- [ ] Add key rotation endpoint
- [ ] Add per-key rate limiting
- [ ] Update `murmur init` to support account creation
- [ ] Add `murmur keys` CLI command for key management
- [ ] Migration scripts for existing users
- [ ] Update docs

---

## Open Questions

1. **Magic link vs password?** — Magic link is simpler, no password storage
2. **Key expiry default?** — 1 year? Never? Configurable?
3. **Scope granularity?** — Start simple (`read`, `write`) or fine-grained?
4. **Self-hosted mode?** — Keep legacy auth for self-hosted deployments?

---

## Timeline Estimate

- Phase 1 (new auth): 2-3 days
- Phase 2 (migration): 1 day
- Phase 3 (deprecate): After April 20 launch

Total: ~4 days of implementation work.
