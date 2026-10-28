import "server-only";
import type { ToolCall } from "@/lib/datasets";
import { ingest } from "@/lib/ingest";
import { apiParams, type Filter } from "@/lib/filters";

// ADR-0031: traces are read through ingest's /v1/traces, which decides completion and status
// (ADR-0013) and checks the project. These map its records to the shapes the pages use.

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
  /** The original trace this one re-ran, when it's a replay run (ADR-0028); else "". */
  replayOf: string;
  /** The root span's resource.service.name, else "". */
  serviceName: string;
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
  /** The full payload is in object storage and the preview is its first 1 KB (ADR-0009). */
  inputOffloaded: boolean;
  outputOffloaded: boolean;
  attributes: Record<string, string>;
};

type ApiSummary = {
  trace_id: string;
  start_time: string;
  root_name: string;
  status: TraceStatus;
  latency_ms: number;
  span_count: number;
  cost_usd: number | null;
  unpriced_llm_spans: number;
  error_count: number;
  tags: string[];
  user_id: string;
  session_id: string;
  last_end_time: string;
  replay_of: string;
  service_name: string;
};

type ApiSpan = {
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
  input_offloaded: boolean;
  output_offloaded: boolean;
  attributes: Record<string, string>;
};

const api = ingest("/v1/traces");

function toSummary(r: ApiSummary): TraceSummary {
  return {
    traceId: r.trace_id,
    startTime: new Date(r.start_time),
    rootName: r.root_name,
    status: r.status,
    latencyMs: r.latency_ms,
    spanCount: r.span_count,
    costUsd: r.cost_usd,
    unpricedLlmSpans: r.unpriced_llm_spans,
    errorCount: r.error_count,
    tags: r.tags,
    userId: r.user_id,
    sessionId: r.session_id,
    lastEndTime: new Date(r.last_end_time),
    replayOf: r.replay_of,
    serviceName: r.service_name,
  };
}

function toSpan(r: ApiSpan): SpanRow {
  return {
    spanId: r.span_id,
    parentSpanId: r.parent_span_id,
    name: r.name,
    kind: r.kind,
    startTime: new Date(r.start_time),
    endTime: r.end_time ? new Date(r.end_time) : null,
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
    inputOffloaded: r.input_offloaded,
    outputOffloaded: r.output_offloaded,
    attributes: r.attributes,
  };
}

/** A trace read failure as a page shows it. Ingest says which store failed and why, including
 * when a ClickHouse migration hasn't run yet. */
export function clickHouseError(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export type Cursor = { start: string; traceId: string };

/** The traces passing `filter` (ADR-0038): production traffic unless it includes eval or replay
 * runs (ADR-0029). */
export async function listTraces(
  limit: number,
  before: Cursor | undefined,
  filter: Filter,
): Promise<{ traces: TraceSummary[]; next?: Cursor }> {
  const query = apiParams(filter);
  query.set("limit", String(limit));
  if (before) {
    query.set("before_start", before.start);
    query.set("before_trace", before.traceId);
  }
  const page = await api.read<{ traces: ApiSummary[]; next: { start_time: string; trace_id: string } | null }>(
    `?${query}`,
  );
  if (!page) return { traces: [] };
  return {
    traces: page.traces.map(toSummary),
    next: page.next ? { start: page.next.start_time, traceId: page.next.trace_id } : undefined,
  };
}

export async function getTrace(traceId: string): Promise<{ summary: TraceSummary; spans: SpanRow[] } | null> {
  if (!/^[0-9a-f]{32}$/.test(traceId)) return null;
  const trace = await api.read<{ summary: ApiSummary; spans: ApiSpan[] }>(`/${encodeURIComponent(traceId)}`);
  return trace && { summary: toSummary(trace.summary), spans: trace.spans.map(toSpan) };
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

/** Which of these traces still exist, or null when they can't be read. A dataset item's source
 * trace can expire with retention (GLOSSARY.md "Source trace expired"). Datasets live in
 * Postgres, so a ClickHouse outage mustn't take their pages down. */
export async function liveTraces(traceIds: string[]): Promise<Set<string> | null> {
  const ids = traceIds.filter((t) => /^[0-9a-f]{32}$/.test(t));
  if (ids.length === 0) return new Set();
  const r = await api.call<{ trace_ids: string[] }>("POST", "/live", { trace_ids: ids });
  return r.ok ? new Set(r.data.trace_ids) : null;
}

/** A span's full input or output: the preview when it's stored whole, else ingest fetches the
 * object storage copy (ADR-0009). Null when the trace or span is gone. */
export async function readPayload(traceId: string, spanId: string, which: "input" | "output"): Promise<string | null> {
  const path = `/${encodeURIComponent(traceId)}/spans/${encodeURIComponent(spanId)}/${which}`;
  return (await api.read<{ text: string }>(path))?.text ?? null;
}

/** What a trace did, in the shape a replay run records (sdk `agenttrace.eval.runner`): its tool
 * calls in start order, and the root span's output. Null when the trace has expired. */
export async function traceOutcome(
  traceId: string,
): Promise<{ trajectory: ToolCall[]; output: unknown; rootError: string | null } | null> {
  const outcome = await api.read<{ trajectory: ToolCall[]; output: unknown; root_error: string | null }>(
    `/${encodeURIComponent(traceId)}/outcome`,
  );
  return outcome && { trajectory: outcome.trajectory, output: outcome.output, rootError: outcome.root_error };
}
