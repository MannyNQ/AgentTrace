-- ADR-0040: a fork names its fork point, the step it went live at, in the trace it forked; that
-- trace may be a replay run, so it's stored apart from the original. A tool-stubbed replay
-- answers every tool call from the recording. Replays stored before this are whole and live.
ALTER TABLE replays
    ADD COLUMN fork_trace_id TEXT CHECK (fork_trace_id ~ '^[0-9a-f]{32}$'),  -- not pinned
    ADD COLUMN fork_span_id  TEXT CHECK (fork_span_id ~ '^[0-9a-f]{16}$'),
    ADD COLUMN tool_mode     TEXT NOT NULL DEFAULT 'live' CHECK (tool_mode IN ('live', 'recorded')),
    ADD CONSTRAINT replays_fork_point CHECK ((fork_trace_id IS NULL) = (fork_span_id IS NULL));
CREATE INDEX replays_by_fork ON replays (project_id, fork_trace_id, started_at DESC)
    WHERE fork_trace_id IS NOT NULL;

-- A fork run whose prefix left the recording names the step it went live at; its warnings are
-- the prefix LLM calls whose request differed from the recording, as [{step, fields}].
ALTER TABLE replay_runs
    ADD COLUMN diverged_at INT CHECK (diverged_at >= 0),
    ADD COLUMN warnings    JSONB NOT NULL DEFAULT '[]';
