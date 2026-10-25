-- HIPPO-E06-F0: interim per-collection access control (static bearer tokens), replaced by an external JWT identity provider in E06.
-- Existing collections stay 'shared', so behaviour is unchanged until a collection is marked restricted.
ALTER TABLE hippocampus.collections
    ADD COLUMN visibility VARCHAR(16) NOT NULL DEFAULT 'shared'
    CONSTRAINT ck_collections_visibility CHECK (visibility IN ('shared', 'restricted'));

CREATE TABLE hippocampus.api_clients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(64) UNIQUE NOT NULL,
    token_sha256 CHAR(64) UNIQUE NOT NULL,   -- sha256 of the bearer token; the token itself is never stored
    read_grants TEXT[] NOT NULL DEFAULT '{}', -- collection slugs or globs such as 'private-*'
    write_grants TEXT[] NOT NULL DEFAULT '{}',-- a write grant implies read
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ
);
