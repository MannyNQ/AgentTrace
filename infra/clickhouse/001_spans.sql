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
