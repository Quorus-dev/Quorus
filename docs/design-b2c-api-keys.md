# Design: B2C API Keys for Murmur Managed Service

**Status:** Draft  
**Date:** 2026-04-12  
**Authors:** Arav, Aarya

---

## 1. Problem

The current relay uses a single shared `RELAY_SECRET` (Bearer token) per relay instance. This is a
deployment-level secret — it cannot be scoped per user, rotated independently, or revoked without
restarting the relay. There are no user accounts, no self-serve signup, and no per-customer usage
isolation. The managed service needs proper account-based auth.

---

## 2. Goals

- Self-serve account creation (no manual provisioning)
- API keys that are: scoped per account, independently revocable, never stored in plaintext
- Three pricing tiers with enforced rate limits: Free / Pro / Enterprise
- Non-breaking migration path for existing `RELAY_SECRET` users
- Immutable account IDs (UUIDs) that survive email changes

---

## 3. Non-Goals (v1)

- OAuth / SSO (out of scope)
- Fine-grained RBAC within an account (out of scope)
- Per-room key scoping (future work)
- Billing integration (separate design doc needed)

---

## 4. Data Model

### 4.1 Account

Represents a registered user of the managed service.

```
accounts
├── id            UUID PK             — immutable, never changes
├── email         TEXT UNIQUE NOT NULL — login identity, can change later
├── tier          account_tier ENUM   — free | pro | enterprise
├── created_at    TIMESTAMPTZ NOT NULL
└── deleted_at    TIMESTAMPTZ NULL    — soft delete only, never hard DELETE
```

**Tier definitions:**

| Tier       | Rate limit (req/min) | Keys allowed | Price      |
| ---------- | -------------------- | ------------ | ---------- |
| free       | 60                   | 3            | $0         |
| pro        | 600                  | 20           | $29/mo     |
| enterprise | unlimited (NULL)     | unlimited    | negotiated |

The `tier` is an enum (not a FK) because it is a first-class domain concept with business logic
attached. Changing a tier is a billing event, not a data edit. Enterprise rate limit stored as
NULL — the auth middleware treats NULL as "no limit enforced."

### 4.2 API Key

A credential that authenticates an account to the relay. Multiple keys per account are supported
(e.g., one per agent deployment, one for CI, one for local dev).

```
api_keys
├── id                  UUID PK
├── account_id          UUID FK → accounts.id ON DELETE CASCADE
├── key_hash            TEXT NOT NULL        — bcrypt(sha256(raw_key)), never plaintext
├── key_prefix          VARCHAR(8) UNIQUE    — first 8 hex chars for display and fast lookup
├── name                TEXT NOT NULL        — user-assigned label ("prod-agent", "ci")
├── rate_limit_per_min  INTEGER NULL         — NULL = inherit from account tier
├── created_at          TIMESTAMPTZ NOT NULL
├── last_used_at        TIMESTAMPTZ NULL     — updated on each authenticated request
└── revoked_at          TIMESTAMPTZ NULL     — soft revoke, never hard DELETE
```

**Why `last_used_at` on the key (not account)?** The relay can update this atomically during
auth without a read-modify-write on the account row. It enables "show me when each key was last
active" in the dashboard without additional queries.

**Why `rate_limit_per_min` per key?** Allows an account to cap a specific key below the tier max
(e.g., cap a CI key at 30/min even on a Pro plan). When NULL, the account tier default applies.

---

## 5. Key Generation

### Format

```
murm_sk_<64 hex chars>
```

Example:

```
murm_sk_a3f9c2d1e8b04712f6a1c3d5e7f92b4d1a8c3e5f7b2d4a6c8e0f1b3d5a7c9e2f4
```

- Prefix `murm_sk_` — identifies this as a Murmur secret key (8 chars); registerable with
  GitHub Secret Scanning and GitGuardian
- 32 random bytes expressed as 64 hex chars — 256 bits of entropy, brute-force infeasible
- Total length: 72 characters
- `key_prefix` stored in DB = first 8 chars of the 64-hex portion (after `murm_sk_`)

### Generation algorithm

```python
import hashlib
import secrets

import bcrypt


def generate_key() -> tuple[str, str, str]:
    """Return (raw_key, key_prefix, key_hash)."""
    raw_key = "murm_sk_" + secrets.token_hex(32)   # 72 chars total
    key_prefix = raw_key[8:16]                      # first 8 hex chars after murm_sk_
    # Pre-hash with SHA-256 before bcrypt: bcrypt silently truncates at 72 bytes.
    # A 72-char raw key would have only 72 bytes hashed. SHA-256 ensures the
    # full key participates in the hash regardless of length.
    sha_digest = hashlib.sha256(raw_key.encode()).digest()
    key_hash = bcrypt.hashpw(sha_digest, bcrypt.gensalt(rounds=12)).decode()
    return raw_key, key_prefix, key_hash
```

### Show-once policy

The `raw_key` is returned **exactly once** — in the creation response body. It is:

- Never stored in the database (only `key_hash` and `key_prefix` are persisted)
- Never logged — not even at DEBUG level
- Never retrievable after the creation response

API response on create:

```json
{
  "id": "uuid",
  "key": "murm_sk_a3f9c2d1...",
  "prefix": "a3f9c2d1",
  "name": "prod-agent",
  "created_at": "2026-04-12T00:00:00Z"
}
```

Subsequent list/get responses return only `prefix`, `name`, and metadata — never `key`.

---

## 6. Auth Flow

```
Client                          Relay
  │                               │
  │  Authorization: Bearer murm_sk_<key>
  │──────────────────────────────►│
  │                               │  1. Check header prefix → 401 if missing/malformed
  │                               │  2. Extract key_prefix = raw_key[8:16]
  │                               │  3. SELECT id, key_hash, account_id, revoked_at,
  │                               │        rate_limit_per_min
  │                               │     FROM api_keys WHERE key_prefix = $1
  │                               │     → 401 if not found
  │                               │  4. If revoked_at IS NOT NULL → 401
  │                               │  5. bcrypt.checkpw(sha256(raw_key), key_hash) → 401 if fail
  │                               │  6. SELECT tier FROM accounts WHERE id = $account_id
  │                               │     AND deleted_at IS NULL → 401 if not found
  │                               │  7. effective_limit = key.rate_limit_per_min ?? tier_default
  │                               │     If effective_limit IS NOT NULL:
  │                               │       Redis INCR rl:{account_id}:{minute_epoch} EX 60
  │                               │       → 429 if over limit
  │                               │  8. UPDATE api_keys SET last_used_at = NOW()
  │                               │     WHERE id = $key_id  (async, fire-and-forget)
  │◄──────────────────────────────│
  │  200 OK (or 401/429/503)      │
```

**Why raw key in Bearer, not a JWT exchange?** For the managed relay, direct key verification
saves a round-trip. Customers who need JWTs for SSO integration can use the existing
`POST /v1/auth/token` endpoint (from the B2B auth module) which exchanges a key for a JWT.

**Rate limit key format:** `rl:{account_id}:{minute_epoch}` — using `account_id` (not `key_id`)
means all keys under an account share the quota. This prevents quota bypass via multiple keys.
Rate limit window: 1 minute, rolling. Redis key TTL: 60 seconds.

---

## 7. Endpoints

### POST /auth/signup

Create a new account and return a default API key.

**Request:**

```json
{ "email": "user@example.com" }
```

**Response 201:**

```json
{
  "account_id": "550e8400-e29b-41d4-a716-446655440000",
  "email": "user@example.com",
  "tier": "free",
  "api_key": {
    "id": "uuid",
    "key": "murm_sk_a3f9c2d1...",
    "prefix": "a3f9c2d1",
    "name": "default"
  }
}
```

**Notes:**

- Email must be unique. Return 409 on conflict. To prevent user enumeration, the 409 response
  body is identical in shape to 201 — only the HTTP status differs.
- Default tier is `free`. Tier upgrades happen via billing webhook, not this endpoint.
- Rate limit: 5 signups/IP/hour.

---

### POST /auth/keys

Create a new API key for the authenticated account.

**Auth:** `Authorization: Bearer murm_sk_<existing_active_key>`

**Request:**

```json
{
  "name": "ci-agent",
  "rate_limit_per_min": 30
}
```

**Response 201:**

```json
{
  "id": "uuid",
  "key": "murm_sk_b2e8f1a3...",
  "prefix": "b2e8f1a3",
  "name": "ci-agent",
  "rate_limit_per_min": 30,
  "created_at": "2026-04-12T00:00:00Z"
}
```

**Notes:**

- `rate_limit_per_min` is optional. Omit to inherit account tier default.
- `rate_limit_per_min` cannot exceed the tier maximum (enforced server-side; silently capped).
- 403 if account has reached the key limit for their tier.
- Rate limit: 20 key creations/account/hour.

---

### DELETE /auth/keys/{key_id}

Revoke an API key (soft delete — sets `revoked_at`, never hard-deletes the row).

**Auth:** `Authorization: Bearer murm_sk_<any_active_key_for_this_account>`

**Response 204:** No body.

**Notes:**

- Can only revoke keys belonging to the authenticated account. Return 404 (not 403) to avoid
  revealing whether the key_id exists under another account.
- Revocation is immediate: flush the relay's in-memory revocation cache on this endpoint.
- Cannot revoke the last active key on an account — return 409 with message:
  `"Cannot revoke last active key. Create a replacement key first."`
- Rate limit: 60 revocations/account/hour.

---

### GET /auth/keys

List all keys for the authenticated account (metadata only — no hashes, no full keys).

**Auth:** `Authorization: Bearer murm_sk_<key>`

**Response 200:**

```json
{
  "keys": [
    {
      "id": "uuid",
      "prefix": "a3f9c2d1",
      "name": "prod-agent",
      "rate_limit_per_min": null,
      "created_at": "2026-04-12T00:00:00Z",
      "last_used_at": "2026-04-12T12:00:00Z",
      "revoked_at": null
    }
  ]
}
```

---

## 8. Migration Path from Legacy Bearer Auth

### Current state

The existing relay uses `RELAY_SECRET` — a single shared Bearer token per deployment. The existing
`murmur/auth/middleware.py` already has a dual-mode `verify_auth()` that falls back from JWT to
`RELAY_SECRET`. The `ALLOW_LEGACY_AUTH` env var controls whether this fallback is active.

### Phase 1 — Dual-mode (immediate, no breaking changes)

No code changes to existing clients. Set `ALLOW_LEGACY_AUTH=true` (already the default when
`DATABASE_URL` is not set). Relay continues accepting both:

1. `Bearer <RELAY_SECRET>` — legacy path (existing behavior)
2. `Bearer murm_sk_<key>` — new B2C path (new behavior)

Log a warning on every legacy auth request:

```
[WARNING] Legacy auth (RELAY_SECRET) active for request from <ip>.
          Migrate to account-based API keys: POST /auth/signup
```

### Phase 2 — Client migration (self-serve)

Existing users:

1. `POST /auth/signup` with their email → receive `murm_sk_*` key
2. Update config: `murmur init <name> --relay-url <url> --secret murm_sk_...`
3. Verify: `murmur doctor` confirms new key works

No relay restart required. Both auth modes active simultaneously.

### Phase 3 — Cutover (operator-controlled)

When all clients confirmed migrated:

1. Set `ALLOW_LEGACY_AUTH=false`
2. Remove `RELAY_SECRET` from environment (or leave it — middleware respects the flag)
3. Relay rejects legacy tokens with 401: `"Legacy auth disabled. Use a Murmur API key."`

The existing `verify_auth()` in `middleware.py` already handles Phases 1 and 3 without code
changes. Phase 2 requires only the new `/auth/signup` and `/auth/keys` endpoints.

### Tenant model compatibility

The existing B2B schema: `tenants → participants → api_keys`
The new B2C schema: `accounts → api_keys` (no participant layer for solo users)

These are parallel hierarchies that coexist in the same database. A B2C `murm_sk_*` key carries
`account_id` in its verified context. A B2B `mct_*` key carries `tenant_id` + `participant_id`.
The relay middleware distinguishes them by key prefix (`murm_sk_` vs `mct_`).

---

## 9. Security Considerations

| Concern              | Mitigation                                                         |
| -------------------- | ------------------------------------------------------------------ |
| Plaintext key leak   | Only `key_hash` + `key_prefix` stored. Key shown once on create.   |
| Hash cracking        | bcrypt(sha256(key), rounds=12) — GPU-resistant                     |
| bcrypt 72-byte limit | SHA-256 pre-hash ensures full 256-bit key entropy feeds bcrypt     |
| Enumeration (signup) | 409 response identical in shape to 201; add timing normalization   |
| Brute force          | Per-IP rate limit on auth failures; bcrypt cost slows attempts     |
| Key prefix collision | 8 hex chars = 4 billion combinations; unique constraint in DB      |
| Secret scanning      | `murm_sk_` prefix is registerable with GitHub + GitGuardian        |
| `last_used_at` load  | Write only if current value is > 60s old (avoids hot-row churn)    |
| Deleted account keys | `ON DELETE CASCADE` on `api_keys.account_id` nullifies active keys |

---

## 10. Open Questions for the Team

1. **Email verification**: Skip for v1 (create-and-go), add in v2? Pro: faster onboarding.
   Con: abuse vector for creating throwaway accounts.

2. **Tenant model unification**: Should `accounts` eventually replace `tenants`? B2B customers
   with multi-agent workspaces need the participant layer. Long-term: an "organization" model
   could unify both. For v1: keep parallel hierarchies.

3. **`last_used_at` write strategy**: Fire-and-forget UPDATE on every request creates a hot row
   under high load. Recommended: write only if `last_used_at IS NULL OR last_used_at < NOW() - '60 seconds'::interval`.
   This cuts writes by ~60x without losing meaningful precision.

4. **Key expiry**: No `expires_at` in v1. Add if compliance customers require it. For now,
   revocation is the rotation mechanism.

5. **Billing webhook**: Tier upgrades need a billing design doc. The `accounts.tier` column is
   ready to be updated by a Stripe webhook handler — that handler is not designed here.

6. **Key prefix length**: This doc uses 8 chars (matching the `mk_` prefix in the existing draft).
   The existing `mct_` key scheme in `tokens.py` uses 12 chars. Consider standardizing to 8 for
   display friendliness, or 12 for a larger lookup namespace. Either works — decide before shipping.
