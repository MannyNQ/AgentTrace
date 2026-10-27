import "server-only";
import { cache } from "react";
import { cookies } from "next/headers";
import { ingest } from "@/lib/ingest";
import { apiParams, zoneOr, ZONE_COOKIE, type Filter } from "@/lib/filters";

// The overview reads ingest's /v1/analytics (ADR-0037): approximate numbers over a range of the
// project's traces, filtered as the trace list is (ADR-0038). Field names are ingest's.

export const RANGES = ["24h", "7d", "30d"] as const;
export type Range = (typeof RANGES)[number];

export type Window = {
  range: Range;
  bucket: "hour" | "day";
  tz: string;
  start: string;
  end: string;
  buckets: string[];
  retention_days: number;
  clamped: boolean;
};

export type VolumeTotals = {
  traces: number;
  running: number;
  completed: number;
  errors: number;
  error_rate: number | null;
};

export type Volume = {
  window: Window;
  totals: VolumeTotals;
  previous: VolumeTotals | null;
  buckets: (VolumeTotals & { start: string })[];
  span_kinds: { kind: string; spans: number; errors: number; error_rate: number | null }[];
};

export type Percentiles = { completed: number; p50: number | null; p95: number | null; p99: number | null };

export type Latency = {
  window: Window;
  totals: Percentiles;
  previous: Percentiles | null;
  buckets: (Percentiles & { start: string })[];
};

export type CostTotals = {
  cost: number;
  completed: number;
  cost_per_trace: number | null;
  unpriced_llm_spans: number;
};

export type ModelCost = {
  model: string;
  calls: number;
  unpriced_calls: number;
  cost: number;
  cost_per_call: number | null;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
};

export type Cost = {
  window: Window;
  totals: CostTotals;
  previous: CostTotals | null;
  top_models: string[];
  buckets: { start: string; by_model: Record<string, number>; other: number; total: number }[];
  models: ModelCost[];
};

export type Group = {
  name: string;
  calls: number;
  errors: number;
  error_rate: number | null;
  p50: number | null;
  p95: number | null;
  ranked: boolean;
};

export type Groups = { window: Window; min_calls: number; groups: Group[]; slowest: string[] };

type Parts = { volume: Volume; latency: Latency; cost: Cost; tools: Groups; models: Groups };

const api = ingest("/v1/analytics");

/** One request per part per page render, however many panels read it. The key is a string so
 * React's cache can match it. */
const readPart = cache(async (part: keyof Parts, query: string) => {
  const data = await api.read<Parts[typeof part]>(`/${part}?${query}`);
  if (!data) throw new Error("the project wasn't found");
  return data;
});

export function analyticsQuery(range: Range, tz: string, f: Filter): string {
  const q = apiParams(f);
  q.set("range", range);
  q.set("tz", tz);
  return q.toString();
}

export async function readAnalytics<P extends keyof Parts>(part: P, query: string): Promise<Parts[P]> {
  return (await readPart(part, query)) as Parts[P];
}

/** The viewer's zone, from the cookie their browser set; UTC until it has. */
export async function viewerZone(): Promise<string> {
  return zoneOr((await cookies()).get(ZONE_COOKIE)?.value);
}

export function isRange(v: unknown): v is Range {
  return RANGES.includes(v as Range);
}
