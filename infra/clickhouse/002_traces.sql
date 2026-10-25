-- Trace summaries, one aggregate row per trace, filled by traces_mv (ADR-0013).
-- Read with GROUP BY trace_id and the matching aggregate (min/max/sum) to merge unmerged parts.
-- Root-only fields hold '' or 0 on non-root rows, so max() picks the root's value.
CREATE TABLE IF NOT EXISTS agenttrace.traces
(
    project_id          UUID,
    trace_id            FixedString(32),
    start_time          SimpleAggregateFunction(min, DateTime64(6, 'UTC')),
    last_end_time       SimpleAggregateFunction(max, DateTime64(6, 'UTC')),
    last_ingested_at    SimpleAggregateFunction(max, DateTime64(6, 'UTC')),
    span_count          SimpleAggregateFunction(sum, UInt64),
    cost_usd            SimpleAggregateFunction(sum, Float64),
    llm_spans           SimpleAggregateFunction(sum, UInt64),
    unpriced_llm_spans  SimpleAggregateFunction(sum, UInt64),  -- ADR-0010: cost is a lower bound when > 0
    has_root            SimpleAggregateFunction(max, UInt8),
    root_ended          SimpleAggregateFunction(max, UInt8),
    root_error          SimpleAggregateFunction(max, UInt8),
    root_end_time       SimpleAggregateFunction(max, DateTime64(6, 'UTC')),
    root_name           SimpleAggregateFunction(max, String),
    session_id          SimpleAggregateFunction(max, String),
    user_id             SimpleAggregateFunction(max, String),
    tags                SimpleAggregateFunction(groupUniqArrayArray, Array(String)),
    llm_errors          SimpleAggregateFunction(sum, UInt64),
    tool_errors         SimpleAggregateFunction(sum, UInt64),
    agent_errors        SimpleAggregateFunction(sum, UInt64),
    retrieval_errors    SimpleAggregateFunction(sum, UInt64),
    custom_errors       SimpleAggregateFunction(sum, UInt64),
    retention_days      SimpleAggregateFunction(max, UInt16)
)
ENGINE = AggregatingMergeTree
ORDER BY (project_id, trace_id)
TTL toDateTime(start_time) + toIntervalDay(retention_days);
