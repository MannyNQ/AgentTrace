import "server-only";
import type { Item, ToolCall } from "./datasets";
import { ingest } from "./ingest";

// ADR-0023: the eval runner uploads experiments to the ingest API; the UI only reads them.
const { read, call } = ingest("/v1/experiments");

export type ExperimentStatus = "running" | "completed" | "failed";

export type ScorerSpec = {
  name: string;
  type: string;
  threshold: number;
  max_drop: number; // the largest pass-rate drop the CI gate tolerates (ADR-0025)
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
  git_merge_base: string | null;
  git_base_branch: string | null;
  started_at: string;
  finished_at: string | null;
  item_count: number;
  samples_uploaded: number;
  cost_usd: number;
  unpriced_samples: number;
  judge_cost_usd: number;
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
  judge_cost_usd: number | null;
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

export type ScorerComparison = {
  scorer: string;
  items: number; // scored in both runs
  baseline_rate: number | null;
  current_rate: number | null;
  change: number | null;
  ci_low: number | null;
  ci_high: number | null;
  max_drop: number;
  regressed: boolean;
  underpowered: boolean;
};

export type ItemRegression = {
  item_id: string;
  scorer: string;
  baseline_passed: number;
  baseline_scored: number;
  current_passed: number;
  current_scored: number;
};

export type RunStats = { cost_per_sample: number | null; p50_latency_ms: number | null };

/** ADR-0024: computed by ingest, so this page and the PR comment always agree. */
export type Comparison = {
  experiment: ExperimentInfo;
  baseline: ExperimentInfo | null;
  baseline_reason: "merge_base" | "base_branch" | "chosen" | null;
  scorers: ScorerComparison[];
  regressions: number;
  item_regressions: ItemRegression[];
  added_items: string[];
  removed_items: string[];
  current_stats: RunStats;
  baseline_stats: RunStats | null;
};

/** The comparison with `baseline`, or the one ingest selects. A refusal (the experiment isn't
 * completed, or the hashes differ) comes back as the error to show. */
export const getComparison = (experiment: string, baseline?: string) =>
  call<Comparison>("GET", `/${id(experiment)}/comparison${baseline ? `?baseline=${id(baseline)}` : ""}`);

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
