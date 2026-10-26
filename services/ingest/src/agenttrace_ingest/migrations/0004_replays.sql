-- ADR-0029: replays and their runs, uploaded by `agenttrace replay`. IDs come from the CLI.
CREATE TABLE replays (
    id                  UUID PRIMARY KEY,
    project_id          UUID NOT NULL REFERENCES projects(id),
    original_trace_id   TEXT NOT NULL CHECK (original_trace_id ~ '^[0-9a-f]{32}$'),  -- not pinned
    original_span_id    TEXT NOT NULL CHECK (original_span_id ~ '^[0-9a-f]{16}$'),
    status              TEXT NOT NULL DEFAULT 'running'
                        CHECK (status IN ('running', 'completed', 'failed')),
    error               TEXT,                       -- why the CLI failed
    runs                INT NOT NULL CHECK (runs >= 1),
    -- ADR-0030: a scored replay names the draft revision it was scored against.
    item_id             UUID,
    item_rev            INT,
    scorers             JSONB NOT NULL,             -- [] when unscored
    config              JSONB,                      -- the parsed eval.yaml, when there is one
    config_hash         TEXT CHECK (config_hash ~ '^[0-9a-f]{64}$'),
    git_commit          TEXT,
    git_branch          TEXT,
    git_dirty           BOOLEAN,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    FOREIGN KEY (item_id, item_rev) REFERENCES item_revisions (item_id, rev),
    CHECK ((item_id IS NULL) = (item_rev IS NULL))
);
CREATE INDEX replays_by_original ON replays (project_id, original_trace_id, started_at DESC);

CREATE TABLE replay_runs (
    replay_id         UUID NOT NULL REFERENCES replays(id),
    run_index         INT NOT NULL CHECK (run_index >= 0),
    trace_id          TEXT CHECK (trace_id ~ '^[0-9a-f]{32}$'),  -- not pinned (ADR-0021)
    started_at        TIMESTAMPTZ NOT NULL,
    latency_ms        DOUBLE PRECISION NOT NULL,
    output            JSONB,
    output_truncated  BOOLEAN NOT NULL DEFAULT false,
    trajectory        JSONB NOT NULL,
    error             TEXT,                       -- set on an errored run
    usage             JSONB NOT NULL,             -- token counts per model
    cost_usd          DOUBLE PRECISION,           -- priced on upload; null when unknown
    judge_usage       JSONB NOT NULL DEFAULT '[]',
    judge_cost_usd    DOUBLE PRECISION,
    uploaded_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (replay_id, run_index)
);

CREATE TABLE replay_scores (
    replay_id  UUID NOT NULL,
    run_index  INT NOT NULL,
    scorer     TEXT NOT NULL,
    score      DOUBLE PRECISION NOT NULL CHECK (score >= 0 AND score <= 1),
    passed     BOOLEAN NOT NULL,              -- score >= the scorer's pass threshold
    reason     TEXT,
    PRIMARY KEY (replay_id, run_index, scorer),
    FOREIGN KEY (replay_id, run_index)
        REFERENCES replay_runs (replay_id, run_index) ON DELETE CASCADE
);
