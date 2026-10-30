-- The base schema, which infra/clickhouse init SQL created before ingest applied it (ADR-0043).
-- Every statement is IF NOT EXISTS, so an install from the init SQL reruns it as a no-op.

-- Spans: append-only, one row per span. Dedup: ADR-0014. Retention: ADR-0016.
CREATE TABLE IF NOT EXISTS trayce.spans
(
    project_id      UUID,                       -- ADR-0002: every row is project-scoped
    trace_id        FixedString(32),            -- ADR-0003: OTel-format IDs from the SDK
    span_id         FixedString(16),
    parent_span_id  String,                     -- '' for the root span
    name            String,
    kind            Enum8('llm' = 1, 'tool' = 2, 'agent' = 3, 'retrieval' = 4, 'custom' = 5),
    start_time      DateTime64(6, 'UTC'),
    end_time        Nullable(DateTime64(6, 'UTC')),
    status          Enum8('ok' = 1, 'error' = 2),
    error_type      String,
    model           String,
    input_tokens    Nullable(UInt32),
    output_tokens   Nullable(UInt32),
    cache_read_tokens      Nullable(UInt32),   -- prompt-cache tokens, not included in input_tokens
    cache_write_5m_tokens  Nullable(UInt32),
    cache_write_1h_tokens  Nullable(UInt32),
    cost_usd        Nullable(Float64),          -- ADR-0010: computed once, null when unpriced
    input_preview   String,                     -- ADR-0009: the whole value when small, else the first 1 KB
    output_preview  String,
    input_uri       String,                     -- '' when the input is inline
    output_uri      String,
    session_id      String,                     -- trace metadata, set on the root span only
    user_id         String,
    tags            Array(String),
    metadata        Map(String, String),
    attributes      Map(String, String),
    retention_days  UInt16 DEFAULT 30,          -- ADR-0016: stamped from the project at ingestion
    ingested_at     DateTime64(6, 'UTC') DEFAULT now64(6)
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY (project_id, start_time, trace_id, span_id)
TTL toDateTime(start_time) + toIntervalDay(retention_days);

-- Trace summaries, one aggregate row per trace, filled by traces_mv (ADR-0013).
-- Read with GROUP BY trace_id and the matching aggregate (min/max/sum) to merge unmerged parts.
-- Root-only fields hold '' or 0 on non-root rows, so max() picks the root's value.
CREATE TABLE IF NOT EXISTS trayce.traces
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

-- Source columns are renamed (s_*) in the subquery so that output aliases, which must match the
-- traces columns, don't shadow them: ClickHouse resolves a name to an alias before a column.
CREATE MATERIALIZED VIEW IF NOT EXISTS trayce.traces_mv TO trayce.traces AS
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
    max(s_retention_days) AS retention_days
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
        retention_days AS s_retention_days
    FROM trayce.spans
)
GROUP BY s_project_id, s_trace_id;
