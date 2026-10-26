import "server-only";
import type { Item, ToolCall } from "./datasets";
import { ingest } from "./ingest";

// ADR-0023: the eval runner uploads experiments to the ingest API; the UI only reads them.
const { read } = ingest("/v1/experiments");

export type ExperimentStatus = "running" | "completed" | "failed";

export type ScorerSpec = {
  name: string;
  type: string;
  threshold: number;
  config: Record<string, unknown>;
};

export type ExperimentInfo = {
  id: string;
  dataset_id: string;
  dataset_version: number;
  name: string | null;
  status: ExperimentStatus;
  error: string | null;
  samples: number; // per item
  scorers: ScorerSpec[];
  config: Record<string, unknown>;
  config_hash: string;
  git_commit: string | null;
  git_branch: string | null;
  started_at: string;
  finished_at: string | null;
  item_count: number;
  samples_uploaded: number;
  cost_usd: number;
  unpriced_samples: number;
};

export type ScorerStats = {
  scorer: string;
  scored: number;
  passed: number;
  pass_rate: number | null;
  mean_score: number | null;
  pass_rate_by_sample: (number | null)[];
};

export type ItemSummary = {
  item_id: string;
  rev: number;
  samples_uploaded: number;
  errored: number;
  mean_latency_ms: number | null;
  scores: Record<string, { passed: number; scored: number }>;
};

export type ExperimentDetail = ExperimentInfo & { scorer_stats: ScorerStats[]; items: ItemSummary[] };

export type StoredScore = { scorer: string; score: number; passed: boolean; reason: string | null };

export type Sample = {
  item_id: string;
  sample_index: number;
  trace_id: string | null;
  started_at: string;
  latency_ms: number;
  output: unknown;
  output_truncated: boolean;
  trajectory: ToolCall[];
  error: string | null;
  usage: { model: string; input_tokens: number; output_tokens: number }[];
  cost_usd: number | null;
  scores: StoredScore[];
};

export type ItemDetail = { item: Item; samples: Sample[] };

const id = (s: string) => encodeURIComponent(s);

export const listExperiments = async (dataset?: string) =>
  (await read<{ experiments: ExperimentInfo[] }>(dataset ? `?dataset_id=${id(dataset)}` : ""))
    ?.experiments ?? [];
export const getExperiment = (experiment: string) => read<ExperimentDetail>(`/${id(experiment)}`);
export const getExperimentItem = (experiment: string, item: string) =>
  read<ItemDetail>(`/${id(experiment)}/items/${id(item)}`);

/** Trajectory modes that compare against the item's expected trajectory (GLOSSARY.md). */
export const COMPARING_MODES = ["exact", "in_order", "any_order"] as const;
export type ComparingMode = (typeof COMPARING_MODES)[number];

/** The first trajectory scorer that compares against the expected trajectory, if any. */
export function comparingScorer(scorers: ScorerSpec[]): { name: string; mode: ComparingMode } | null {
  for (const s of scorers) {
    const mode = s.config.mode;
    if (s.type === "trajectory" && COMPARING_MODES.includes(mode as ComparingMode)) {
      return { name: s.name, mode: mode as ComparingMode };
    }
  }
  return null;
}
