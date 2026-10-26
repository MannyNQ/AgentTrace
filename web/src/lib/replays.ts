import "server-only";
import type { Item, ToolCall } from "./datasets";
import type { ExperimentStatus, ScorerSpec, StoredScore } from "./experiments";
import { ingest } from "./ingest";

// ADR-0029: `agenttrace replay` uploads replays to the ingest API; the UI only reads them.
const { read } = ingest("/v1/replays");

export type PassCount = { scorer: string; passed: number; scored: number };

export type ReplayInfo = {
  id: string;
  replay_of: { trace_id: string; span_id: string };
  status: ExperimentStatus;
  error: string | null;
  runs: number;
  // ADR-0030: a scored replay names the draft revision it was scored against.
  item_id: string | null;
  item_rev: number | null;
  scorers: ScorerSpec[];
  config_hash: string | null;
  git_commit: string | null;
  git_branch: string | null;
  git_dirty: boolean | null;
  started_at: string;
  finished_at: string | null;
  runs_uploaded: number;
  errored: number;
  cost_usd: number;
  unpriced_runs: number;
  judge_cost_usd: number;
  pass_counts: PassCount[];
};

export type ReplayRun = {
  run_index: number;
  trace_id: string | null;
  started_at: string;
  latency_ms: number;
  output: unknown;
  output_truncated: boolean;
  trajectory: ToolCall[];
  error: string | null;
  cost_usd: number | null;
  judge_cost_usd: number | null;
  scores: StoredScore[];
};

/** `item` is null when unscored, or when the draft item was deleted after the replay ran. */
export type ReplayDetail = ReplayInfo & { item: Item | null; replay_runs: ReplayRun[] };

const id = (s: string) => encodeURIComponent(s);

/** Replays of one original trace, newest first. */
export const listReplays = async (traceId: string) =>
  (await read<{ replays: ReplayInfo[] }>(`?trace_id=${id(traceId)}`))?.replays ?? [];
export const getReplay = (replay: string) => read<ReplayDetail>(`/${id(replay)}`);
