-- Murmur Managed Service — B2C API Keys Schema
-- Revision: 003 (extends existing 001/002 migrations)
-- Date: 2026-04-12
--
-- Prerequisites: migrations 001 (tenants/participants/api_keys) and
--               002 (rooms/messages/webhooks/presence) must already be applied.
--
-- This schema adds the B2C account layer alongside the existing B2B tenant
-- layer. Both coexist in the same database. B2C keys use the murm_sk_ prefix;
-- B2B keys use the existing mct_ prefix. The relay middleware distinguishes
-- them by key prefix.

-- ---------------------------------------------------------------------------
-- Enum: account_tier
-- ---------------------------------------------------------------------------
-- Using a Postgres native enum rather than a CHECK constraint so that:
-- (a) the valid set is self-documenting in pg_type,
-- (b) adding a new tier is a single ALTER TYPE ... ADD VALUE (online, no rewrite).

CREATE TYPE account_tier AS ENUM ('free', 'pro', 'enterprise');

-- ---------------------------------------------------------------------------
-- Table: accounts
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS accounts (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email       TEXT        NOT NULL,
    tier        account_tier NOT NULL DEFAULT 'free',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at  TIMESTAMPTZ NULL       -- soft delete; never hard-DELETE
);

-- Unique constraint on email scoped to non-deleted accounts.
-- A partial unique index lets a deleted email be re-registered.
CREATE UNIQUE INDEX uq_accounts_email_active
    ON accounts (email)
    WHERE deleted_at IS NULL;

-- Support soft-delete queries: WHERE deleted_at IS NULL
CREATE INDEX ix_accounts_deleted_at
    ON accounts (deleted_at)
    WHERE deleted_at IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Table: api_keys
-- ---------------------------------------------------------------------------
-- Stores one row per API key. The raw key is NEVER stored here.
-- Only a bcrypt(sha256(raw_key)) hash and the first 8 hex chars (prefix)
-- for fast lookup.
--
-- Rate limit precedence (evaluated in auth middleware):
--   1. key.rate_limit_per_min  (if NOT NULL — per-key override)
--   2. tier default            (60 / 600 / NULL for free/pro/enterprise)

CREATE TABLE IF NOT EXISTS api_keys (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id          UUID        NOT NULL
                            REFERENCES accounts(id) ON DELETE CASCADE,
    key_hash            TEXT        NOT NULL,  -- bcrypt(sha256(raw_key))
    key_prefix          VARCHAR(8)  NOT NULL,  -- first 8 hex chars; used for lookup + display
    name                TEXT        NOT NULL,  -- user-assigned label, e.g. "prod-agent"
    rate_limit_per_min  INTEGER     NULL       -- NULL = inherit tier default
                            CHECK (rate_limit_per_min IS NULL OR rate_limit_per_min > 0),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at        TIMESTAMPTZ NULL,      -- updated async on each authenticated request
    revoked_at          TIMESTAMPTZ NULL       -- soft revoke; never hard-DELETE
);

-- Primary lookup path: prefix → fetch hash → verify.
-- Unique because prefix is part of the key identity.
CREATE UNIQUE INDEX uq_api_keys_prefix
    ON api_keys (key_prefix);

-- List all active keys for an account (dashboard, revocation guard).
CREATE INDEX ix_api_keys_account_active
    ON api_keys (account_id)
    WHERE revoked_at IS NULL;

-- Hash lookup for the verify-only code path (rare, but needs to be fast).
-- The hash itself is not searchable (bcrypt output varies per call),
-- so this index only helps equality on key_prefix; the hash verification
-- is always done in application code after the prefix lookup.

-- Revocation cache refresh: SELECT key_prefix WHERE revoked_at IS NOT NULL.
CREATE INDEX ix_api_keys_revoked
    ON api_keys (key_prefix)
    WHERE revoked_at IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Tier rate-limit reference (informational — not enforced by the DB)
-- ---------------------------------------------------------------------------
-- free:       60  req/min, max 3  active keys
-- pro:        600 req/min, max 20 active keys
-- enterprise: NULL (unlimited), unlimited active keys
--
-- Rate limit enforcement uses Redis: key rl:{account_id}:{minute_epoch}
-- with a 60-second TTL, INCR + EX atomic operation.
-- The DB is only consulted to resolve the effective limit on cache miss.

-- ---------------------------------------------------------------------------
-- View: active_api_keys
-- ---------------------------------------------------------------------------
-- Convenience view for application queries that only care about non-revoked keys.

CREATE OR REPLACE VIEW active_api_keys AS
SELECT
    k.id,
    k.account_id,
    k.key_hash,
    k.key_prefix,
    k.name,
    k.rate_limit_per_min,
    k.created_at,
    k.last_used_at,
    a.tier               AS account_tier,
    a.email              AS account_email,
    CASE a.tier
        WHEN 'free'       THEN 60
        WHEN 'pro'        THEN 600
        WHEN 'enterprise' THEN NULL
    END                  AS tier_rate_limit_per_min,
    COALESCE(k.rate_limit_per_min,
        CASE a.tier
            WHEN 'free'       THEN 60
            WHEN 'pro'        THEN 600
            WHEN 'enterprise' THEN NULL
        END
    )                    AS effective_rate_limit_per_min
FROM api_keys k
JOIN accounts a ON a.id = k.account_id
WHERE k.revoked_at IS NULL
  AND a.deleted_at IS NULL;

-- ---------------------------------------------------------------------------
-- Downgrade (for reference — run in reverse order)
-- ---------------------------------------------------------------------------
-- DROP VIEW  IF EXISTS active_api_keys;
-- DROP TABLE IF EXISTS api_keys;
-- DROP TABLE IF EXISTS accounts;
-- DROP TYPE  IF EXISTS account_tier;
