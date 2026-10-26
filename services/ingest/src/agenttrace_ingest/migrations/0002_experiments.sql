-- ADR-0023: experiments and their samples, uploaded by the eval runner. IDs come from the runner.
CREATE TABLE experiments (
    id                UUID PRIMARY KEY,
    project_id        UUID NOT NULL REFERENCES projects(id),
    dataset_id        UUID NOT NULL,
    dataset_version   INT NOT NULL,
    name              TEXT,
    status            TEXT NOT NULL DEFAULT 'running'
                      CHECK (status IN ('running', 'completed', 'failed')),
    error             TEXT,                       -- why the runner failed
    samples_per_item  INT NOT NULL CHECK (samples_per_item >= 1),
    scorers           JSONB NOT NULL,             -- [{name, type, threshold, config}]
    config            JSONB NOT NULL,             -- the parsed eval.yaml
    config_hash       TEXT NOT NULL CHECK (config_hash ~ '^[0-9a-f]{64}$'),
    git_commit        TEXT,
    git_branch        TEXT,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ,
    FOREIGN KEY (dataset_id, dataset_version) REFERENCES dataset_versions (dataset_id, version)
);
CREATE INDEX experiments_by_project ON experiments (project_id, started_at DESC);

CREATE TABLE experiment_samples (
    experiment_id     UUID NOT NULL REFERENCES experiments(id),
    item_id           UUID NOT NULL REFERENCES dataset_items(item_id),
    sample_index      INT NOT NULL CHECK (sample_index >= 0),
    trace_id          TEXT CHECK (trace_id ~ '^[0-9a-f]{32}$'),  -- not pinned (ADR-0021)
    started_at        TIMESTAMPTZ NOT NULL,
    latency_ms        DOUBLE PRECISION NOT NULL,
    output            JSONB,
    output_truncated  BOOLEAN NOT NULL DEFAULT false,
    trajectory        JSONB NOT NULL,
    error             TEXT,                       -- set on an errored sample
    usage             JSONB NOT NULL,             -- token counts per model
    cost_usd          DOUBLE PRECISION,           -- priced on upload; null when unknown
    uploaded_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (experiment_id, item_id, sample_index)
);

CREATE TABLE experiment_scores (
    experiment_id  UUID NOT NULL,
    item_id        UUID NOT NULL,
    sample_index   INT NOT NULL,
    scorer         TEXT NOT NULL,
    score          DOUBLE PRECISION NOT NULL CHECK (score >= 0 AND score <= 1),
    passed         BOOLEAN NOT NULL,              -- score >= the scorer's pass threshold
    reason         TEXT,
    PRIMARY KEY (experiment_id, item_id, sample_index, scorer),
    FOREIGN KEY (experiment_id, item_id, sample_index)
        REFERENCES experiment_samples (experiment_id, item_id, sample_index) ON DELETE CASCADE
);

-- ADR-0021: when the item's source trace was pinned. Unpinned items are retried on startup, which
-- also backfills items created before Phase 3.
ALTER TABLE dataset_items ADD COLUMN pinned_at TIMESTAMPTZ;
CREATE INDEX dataset_items_unpinned ON dataset_items (created_at)
    WHERE source_trace_id IS NOT NULL AND pinned_at IS NULL;
