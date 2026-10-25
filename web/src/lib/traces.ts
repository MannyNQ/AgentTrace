import { DEFAULT_PROJECT_ID, QUIET_PERIOD_S, getClickHouse } from "@/lib/stores";

export type SpanKind = "llm" | "tool" | "agent" | "retrieval" | "custom";
export type TraceStatus = "ok" | "error" | "running";

export type TraceSummary = {
  traceId: string;
  startTime: Date;
  rootName: string;
  status: TraceStatus;
  latencyMs: number;
  spanCount: number;
  costUsd: number | null;
  unpricedLlmSpans: number;
  errorCount: number;
  tags: string[];
  userId: string;
  sessionId: string;
  lastEndTime: Date;
};

export type SpanRow = {
  spanId: string;
  parentSpanId: string;
  name: string;
  kind: SpanKind;
  startTime: Date;
  endTime: Date | null;
  status: "ok" | "error";
  errorType: string;
  model: string;
  inputTokens: number | null;
  outputTokens: number | null;
  cacheReadTokens: number | null;
  cacheWriteTokens: number | null;
  costUsd: number | null;
  inputPreview: string;
  outputPreview: string;
  inputUri: string;
  outputUri: string;
  attributes: Record<string, string>;
};

// traces is an AggregatingMergeTree (ADR-0013): merge unmerged parts with the same aggregates
// the materialized view used. Aliases differ from column names so HAVING can't confuse them.
const SUMMARY_SELECT = `
  SELECT
    trace_id,
    min(start_time) AS t_start,
    max(last_end_time) AS t_last_end,
    max(last_ingested_at) AS t_last_ingested,
    sum(span_count) AS t_span_count,
    sum(cost_usd) AS t_cost,
    sum(llm_spans) AS t_llm_spans,
    sum(unpriced_llm_spans) AS t_unpriced,
    max(has_root) AS t_has_root,
    max(root_ended) AS t_root_ended,
    max(root_error) AS t_root_error,
    max(root_end_time) AS t_root_end,
    max(root_name) AS t_root_name,
    max(session_id) AS t_session_id,
    max(user_id) AS t_user_id,
    groupUniqArrayArray(tags) AS t_tags,
    sum(llm_errors) + sum(tool_errors) + sum(agent_errors) + sum(retrieval_errors)
      + sum(custom_errors) AS t_errors
  FROM traces
  WHERE project_id = {project:UUID}`;

type SummaryRow = {
  trace_id: string;
  t_start: string;
  t_last_end: string;
  t_last_ingested: string;
  t_span_count: number;
  t_cost: number;
  t_llm_spans: number;
  t_unpriced: number;
  t_has_root: number;
  t_root_ended: number;
  t_root_error: number;
  t_root_end: string;
  t_root_name: string;
  t_session_id: string;
  t_user_id: string;
  t_tags: string[];
  t_errors: number;
};

// ClickHouse returns DateTime64 in ISO form without a zone; the columns are UTC.
function utc(s: string): Date {
  return new Date(s.endsWith("Z") ? s : `${s}Z`);
}

/** Trace completion and status are decided when a trace is read (ADR-0013, GLOSSARY.md). */
function toSummary(r: SummaryRow, now: Date): TraceSummary {
  const start = utc(r.t_start);
  const lastEnd = utc(r.t_last_end);
  const rootEnded = r.t_root_ended === 1;
  const quiet = now.getTime() - utc(r.t_last_ingested).getTime() > QUIET_PERIOD_S * 1000;
  const complete = rootEnded || quiet;
  const status: TraceStatus = !complete
    ? "running"
    : r.t_root_error === 1 || !rootEnded
      ? "error"
      : "ok";
  const end = rootEnded ? utc(r.t_root_end) : lastEnd;
  return {
    traceId: r.trace_id,
    startTime: start,
    rootName: r.t_root_name || (r.t_has_root ? "(unnamed)" : "(root span missing)"),
    status,
    latencyMs: end.getTime() - start.getTime(),
    spanCount: r.t_span_count,
    // Null when nothing in the trace was priced: no LLM spans, or none with a known price.
    costUsd: r.t_llm_spans > r.t_unpriced ? r.t_cost : null,
    unpricedLlmSpans: r.t_unpriced,
    errorCount: r.t_errors,
    tags: r.t_tags,
    userId: r.t_user_id,
    sessionId: r.t_session_id,
    lastEndTime: lastEnd,
  };
}

export type Cursor = { start: string; traceId: string };

export async function listTraces(
  limit: number,
  before?: Cursor,
): Promise<{ traces: TraceSummary[]; next?: Cursor }> {
  const result = await getClickHouse().query({
    query: `${SUMMARY_SELECT}
      GROUP BY trace_id
      HAVING (t_start, trace_id) < (parseDateTime64BestEffort({before_start:String}, 6, 'UTC'),
                                    {before_trace:String})
      ORDER BY t_start DESC, trace_id DESC
      LIMIT {limit:UInt32}`,
    query_params: {
      project: DEFAULT_PROJECT_ID,
      // No cursor: start after every trace. (ClickHouse parses constants even in a dead OR branch,
      // so an empty cursor can't be special-cased in SQL.)
      before_start: before?.start ?? "2999-01-01T00:00:00Z",
      before_trace: before?.traceId ?? "f".repeat(32),
      limit: limit + 1,
    },
    format: "JSONEachRow",
  });
  const rows = await result.json<SummaryRow>();
  const now = new Date();
  const page = rows.slice(0, limit);
  const last = page.at(-1);
  return {
    traces: page.map((r) => toSummary(r, now)),
    next: rows.length > limit && last ? { start: last.t_start, traceId: last.trace_id } : undefined,
  };
}

export async function getTrace(
  traceId: string,
): Promise<{ summary: TraceSummary; spans: SpanRow[] } | null> {
  if (!/^[0-9a-f]{32}$/.test(traceId)) return null;
  const client = getClickHouse();
  const summaryResult = await client.query({
    query: `${SUMMARY_SELECT} AND trace_id = {trace:FixedString(32)} GROUP BY trace_id`,
    query_params: { project: DEFAULT_PROJECT_ID, trace: traceId },
    format: "JSONEachRow",
  });
  const [summaryRow] = await summaryResult.json<SummaryRow>();
  if (!summaryRow) return null;

  // The summary's time range bounds the scan on the (project_id, start_time, ...) sort key;
  // FINAL hides duplicates that ReplacingMergeTree hasn't merged yet (ADR-0014).
  const spansResult = await client.query({
    query: `
      SELECT span_id, parent_span_id, name, kind, start_time, end_time, status, error_type,
             model, input_tokens, output_tokens, cache_read_tokens,
             if(cache_write_5m_tokens IS NULL AND cache_write_1h_tokens IS NULL, NULL,
                ifNull(cache_write_5m_tokens, 0) + ifNull(cache_write_1h_tokens, 0))
               AS cache_write_tokens,
             cost_usd, input_preview, output_preview,
             input_uri, output_uri, attributes
      FROM spans FINAL
      WHERE project_id = {project:UUID}
        AND trace_id = {trace:FixedString(32)}
        AND start_time BETWEEN parseDateTime64BestEffort({from:String}, 6, 'UTC')
                           AND parseDateTime64BestEffort({to:String}, 6, 'UTC')
      ORDER BY start_time, span_id`,
    query_params: {
      project: DEFAULT_PROJECT_ID,
      trace: traceId,
      from: summaryRow.t_start,
      to: summaryRow.t_last_end,
    },
    format: "JSONEachRow",
  });
  type Raw = {
    span_id: string;
    parent_span_id: string;
    name: string;
    kind: SpanKind;
    start_time: string;
    end_time: string | null;
    status: "ok" | "error";
    error_type: string;
    model: string;
    input_tokens: number | null;
    output_tokens: number | null;
    cache_read_tokens: number | null;
    cache_write_tokens: number | null;
    cost_usd: number | null;
    input_preview: string;
    output_preview: string;
    input_uri: string;
    output_uri: string;
    attributes: Record<string, string>;
  };
  const spans = (await spansResult.json<Raw>()).map(
    (r): SpanRow => ({
      spanId: r.span_id,
      parentSpanId: r.parent_span_id,
      name: r.name,
      kind: r.kind,
      startTime: utc(r.start_time),
      endTime: r.end_time ? utc(r.end_time) : null,
      status: r.status,
      errorType: r.error_type,
      model: r.model,
      inputTokens: r.input_tokens,
      outputTokens: r.output_tokens,
      cacheReadTokens: r.cache_read_tokens,
      cacheWriteTokens: r.cache_write_tokens,
      costUsd: r.cost_usd,
      inputPreview: r.input_preview,
      outputPreview: r.output_preview,
      inputUri: r.input_uri,
      outputUri: r.output_uri,
      attributes: r.attributes,
    }),
  );
  return { summary: toSummary(summaryRow, new Date()), spans };
}

export type TreeNode = { span: SpanRow; depth: number };

/** Depth-first span order for the tree view. Spans whose parent hasn't arrived sit at the top. */
export function flattenTree(spans: SpanRow[]): TreeNode[] {
  const ids = new Set(spans.map((s) => s.spanId));
  const children = new Map<string, SpanRow[]>();
  for (const s of spans) {
    const parent = s.parentSpanId && ids.has(s.parentSpanId) ? s.parentSpanId : "";
    children.set(parent, [...(children.get(parent) ?? []), s]);
  }
  const out: TreeNode[] = [];
  const visit = (parent: string, depth: number) => {
    for (const s of children.get(parent) ?? []) {
      out.push({ span: s, depth });
      visit(s.spanId, depth + 1);
    }
  };
  visit("", 0);
  return out;
}
