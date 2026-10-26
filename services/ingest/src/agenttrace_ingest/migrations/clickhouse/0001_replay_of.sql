-- Phase 5: the original trace a replay run re-ran, from its root span's `agenttrace.replay_of`
-- attribute (`<trace_id>:<span_id>`, ADR-0028), so "replays of this trace" and hiding replays from
-- the trace list are each one query on `traces` (ADR-0029). Traces stored before this get ''.
ALTER TABLE agenttrace.traces ADD COLUMN IF NOT EXISTS replay_of SimpleAggregateFunction(max, String);

-- MODIFY QUERY swaps the view's query atomically, so no insert is summarized by neither query.
ALTER TABLE agenttrace.traces_mv MODIFY QUERY
SELECT
    s_project_id AS project_id,
    s_trace_id AS trace_id,
    min(s_start) AS start_time,
    max(coalesce(s_end, s_start)) AS last_end_time,
    max(s_ingested_at) AS last_ingested_at,
    toUInt64(count()) AS span_count,
    sum(ifNull(s_cost, 0)) AS cost_usd,
    toUInt64(countIf(s_kind = 'llm')) AS llm_spans,
    toUInt64(countIf(s_kind = 'llm' AND s_cost IS NULL)) AS unpriced_llm_spans,
    max(toUInt8(s_is_root)) AS has_root,
    max(toUInt8(s_is_root AND s_end IS NOT NULL)) AS root_ended,
    max(toUInt8(s_is_root AND s_status = 'error')) AS root_error,
    max(if(s_is_root, coalesce(s_end, s_start), toDateTime64(0, 6, 'UTC'))) AS root_end_time,
    max(if(s_is_root, s_name, '')) AS root_name,
    max(s_session_id) AS session_id,
    max(s_user_id) AS user_id,
    groupUniqArrayArray(s_tags) AS tags,
    toUInt64(countIf(s_kind = 'llm' AND s_status = 'error')) AS llm_errors,
    toUInt64(countIf(s_kind = 'tool' AND s_status = 'error')) AS tool_errors,
    toUInt64(countIf(s_kind = 'agent' AND s_status = 'error')) AS agent_errors,
    toUInt64(countIf(s_kind = 'retrieval' AND s_status = 'error')) AS retrieval_errors,
    toUInt64(countIf(s_kind = 'custom' AND s_status = 'error')) AS custom_errors,
    max(s_retention_days) AS retention_days,
    max(if(s_is_root, splitByChar(':', s_replay_of)[1], '')) AS replay_of
FROM
(
    SELECT
        project_id AS s_project_id,
        trace_id AS s_trace_id,
        start_time AS s_start,
        end_time AS s_end,
        ingested_at AS s_ingested_at,
        cost_usd AS s_cost,
        kind AS s_kind,
        status AS s_status,
        name AS s_name,
        parent_span_id = '' AS s_is_root,
        session_id AS s_session_id,
        user_id AS s_user_id,
        tags AS s_tags,
        retention_days AS s_retention_days,
        attributes['agenttrace.replay_of'] AS s_replay_of
    FROM agenttrace.spans
)
GROUP BY s_project_id, s_trace_id;
