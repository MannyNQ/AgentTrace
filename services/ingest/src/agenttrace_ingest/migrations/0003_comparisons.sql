-- ADR-0026: what a pull request's experiment branched from, so a baseline can be found for it.
ALTER TABLE experiments ADD COLUMN git_merge_base TEXT;
ALTER TABLE experiments ADD COLUMN git_base_branch TEXT;
-- Baseline lookups: the newest completed experiment with a hash, at a commit or on a branch.
CREATE INDEX experiments_by_hash ON experiments (project_id, config_hash, started_at DESC)
    WHERE status = 'completed';

-- ADR-0027: LLM judge token counts per model, priced on upload apart from the agent's cost.
ALTER TABLE experiment_samples ADD COLUMN judge_usage JSONB NOT NULL DEFAULT '[]';
ALTER TABLE experiment_samples ADD COLUMN judge_cost_usd DOUBLE PRECISION;
