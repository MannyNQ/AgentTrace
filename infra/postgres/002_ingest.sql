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
    effective_from    TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (model, effective_from)
);

-- Anthropic first-party list prices as of 2026-10-06.
INSERT INTO model_prices (model, input_per_mtok, output_per_mtok, effective_from) VALUES
    ('claude-fable-5-1',  10.00, 50.00, '2026-01-01T00:00:00Z'),
    ('claude-fable-5',    10.00, 50.00, '2026-01-01T00:00:00Z'),
    ('claude-opus-5-5',    4.00, 20.00, '2026-01-01T00:00:00Z'),
    ('claude-opus-5',      5.00, 25.00, '2026-01-01T00:00:00Z'),
    ('claude-opus-4-8',    5.00, 25.00, '2026-01-01T00:00:00Z'),
    ('claude-opus-4-7',    5.00, 25.00, '2026-01-01T00:00:00Z'),
    ('claude-opus-4-6',    5.00, 25.00, '2026-01-01T00:00:00Z'),
    ('claude-sonnet-5-5',  2.00, 10.00, '2026-01-01T00:00:00Z'),
    ('claude-sonnet-5',    2.00, 10.00, '2026-01-01T00:00:00Z'),
    ('claude-sonnet-4-6',  3.00, 15.00, '2026-01-01T00:00:00Z'),
    ('claude-haiku-5-5',   0.10,  0.50, '2026-01-01T00:00:00Z'),
    ('claude-haiku-4-5',   1.00,  5.00, '2026-01-01T00:00:00Z')
ON CONFLICT DO NOTHING;
