-- Phase 6: users, sessions, invite and reset links, setup tokens, key scopes, and project slugs
-- (ADR-0032, ADR-0033, ADR-0034).

-- A slug never changes once set; the name stays editable. The default project is `default`.
ALTER TABLE projects ADD COLUMN slug TEXT;
UPDATE projects SET slug = 'default' WHERE id = '00000000-0000-0000-0000-000000000001';
UPDATE projects
SET slug = coalesce(nullif(trim(BOTH '-' FROM regexp_replace(lower(name), '[^a-z0-9]+', '-', 'g')), ''),
                    'project') || '-' || left(id::text, 8)
WHERE slug IS NULL;
ALTER TABLE projects
    ALTER COLUMN slug SET NOT NULL,
    ADD CONSTRAINT projects_slug_format CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    ADD CONSTRAINT projects_slug_unique UNIQUE (org_id, slug);

-- A user has one org membership, so the role lives on the user. Deactivated, never deleted.
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES orgs(id),
    email           TEXT NOT NULL UNIQUE CHECK (email = lower(email)),
    password_hash   TEXT NOT NULL,                      -- argon2id
    role            TEXT NOT NULL CHECK (role IN ('owner', 'member')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deactivated_at  TIMESTAMPTZ
);
CREATE INDEX users_by_org ON users (org_id);

-- The token itself is never stored. Logging out deletes the row.
CREATE TABLE sessions (
    token_hash  TEXT PRIMARY KEY,                       -- SHA-256 hex
    user_id     UUID NOT NULL REFERENCES users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX sessions_by_user ON sessions (user_id);

-- Owner-created links: an invite creates a user with `role`; a reset sets `user_id`'s password.
CREATE TABLE links (
    token_hash  TEXT PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('invite', 'reset')),
    org_id      UUID NOT NULL REFERENCES orgs(id),
    role        TEXT CHECK (role IN ('owner', 'member')),
    user_id     UUID REFERENCES users(id),
    created_by  UUID NOT NULL REFERENCES users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ,
    CHECK ((kind = 'invite') = (role IS NOT NULL)),
    CHECK ((kind = 'reset') = (user_id IS NOT NULL))
);

-- Tokens that may claim an unclaimed install. Every ingest process adds the one it printed;
-- claiming deletes them all.
CREATE TABLE setup_tokens (
    token_hash  TEXT PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Existing keys become `full` keys, so current setups keep working (ADR-0033).
ALTER TABLE api_keys
    ADD COLUMN id           UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    ADD COLUMN name         TEXT NOT NULL DEFAULT '',
    ADD COLUMN scope        TEXT NOT NULL DEFAULT 'full' CHECK (scope IN ('ingest', 'full')),
    ADD COLUMN hint         TEXT,                       -- the key's last 4 characters, for display
    ADD COLUMN from_env     BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN created_by   UUID REFERENCES users(id),
    ADD COLUMN last_used_at TIMESTAMPTZ,
    ADD COLUMN revoked_at   TIMESTAMPTZ;
CREATE INDEX api_keys_by_project ON api_keys (project_id, created_at);
