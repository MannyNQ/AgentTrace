-- The base schema, which infra/postgres init SQL created before ingest applied it (ADR-0043).
-- It runs only on an empty database. An install from the init SQL already has it, and later
-- migrations changed it (0005 made projects.slug NOT NULL), so the inserts would fail there.
DO $base$
BEGIN
    IF to_regclass('projects') IS NOT NULL THEN
        RETURN;
    END IF;

    -- ADR-0002: orgs and projects exist from day one; auth waits for Phase 6.
    CREATE TABLE IF NOT EXISTS orgs (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name        TEXT NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE TABLE IF NOT EXISTS projects (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id          UUID NOT NULL REFERENCES orgs(id),
        name            TEXT NOT NULL,
        retention_days  INT NOT NULL DEFAULT 30,   -- ADR-0011
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (org_id, name)
    );

    -- Fixed IDs so a self-hosted install always has the same default org and project.
    INSERT INTO orgs (id, name)
    VALUES ('00000000-0000-0000-0000-000000000001', 'default')
    ON CONFLICT DO NOTHING;

    INSERT INTO projects (id, org_id, name)
    VALUES ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'default')
    ON CONFLICT DO NOTHING;

    -- ADR-0017: API keys resolve a request to a project. Ingest upserts the dev key on startup.
    CREATE TABLE IF NOT EXISTS api_keys (
        key_hash    TEXT PRIMARY KEY,               -- SHA-256 hex of the key; the key itself is never stored
        project_id  UUID NOT NULL REFERENCES projects(id),
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    -- ADR-0010: the worker prices an LLM span with the newest row in effect at its start time.
    CREATE TABLE IF NOT EXISTS model_prices (
        model             TEXT NOT NULL,
        input_per_mtok    NUMERIC(12, 6) NOT NULL,  -- USD per million input tokens
        output_per_mtok   NUMERIC(12, 6) NOT NULL,
        -- Prompt-cache prices. Null means unknown: a span that used the cache then gets a null cost.
        cache_read_per_mtok      NUMERIC(12, 6),
        cache_write_5m_per_mtok  NUMERIC(12, 6),
        cache_write_1h_per_mtok  NUMERIC(12, 6),
        effective_from    TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (model, effective_from)
    );

    -- Anthropic first-party list prices as of 2026-10-06. Cache writes are 1.25x input (5-minute TTL)
    -- and 2x input (1-hour TTL); cache reads are 0.1x input, except Opus 5.5 (0.05x) and Fable 5.1
    -- (0.025x).
    INSERT INTO model_prices (model, input_per_mtok, output_per_mtok, cache_read_per_mtok,
                              cache_write_5m_per_mtok, cache_write_1h_per_mtok, effective_from) VALUES
        ('claude-fable-5-1',  10.00, 50.00, 0.25,  12.50,  20.00, '2026-01-01T00:00:00Z'),
        ('claude-fable-5',    10.00, 50.00, 1.00,  12.50,  20.00, '2026-01-01T00:00:00Z'),
        ('claude-opus-5-5',    4.00, 20.00, 0.20,   5.00,   8.00, '2026-01-01T00:00:00Z'),
        ('claude-opus-5',      5.00, 25.00, 0.50,   6.25,  10.00, '2026-01-01T00:00:00Z'),
        ('claude-opus-4-8',    5.00, 25.00, 0.50,   6.25,  10.00, '2026-01-01T00:00:00Z'),
        ('claude-opus-4-7',    5.00, 25.00, 0.50,   6.25,  10.00, '2026-01-01T00:00:00Z'),
        ('claude-opus-4-6',    5.00, 25.00, 0.50,   6.25,  10.00, '2026-01-01T00:00:00Z'),
        ('claude-sonnet-5-5',  2.00, 10.00, 0.20,   2.50,   4.00, '2026-01-01T00:00:00Z'),
        ('claude-sonnet-5',    2.00, 10.00, 0.20,   2.50,   4.00, '2026-01-01T00:00:00Z'),
        ('claude-sonnet-4-6',  3.00, 15.00, 0.30,   3.75,   6.00, '2026-01-01T00:00:00Z'),
        ('claude-haiku-5-5',   0.10,  0.50, 0.01,  0.125,   0.20, '2026-01-01T00:00:00Z'),
        ('claude-haiku-4-5',   1.00,  5.00, 0.10,   1.25,   2.00, '2026-01-01T00:00:00Z')
    ON CONFLICT DO NOTHING;
END
$base$;
