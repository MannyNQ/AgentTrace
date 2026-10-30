import "server-only";
import type { Item, ToolCall } from "./datasets";
import type { ExperimentStatus, ScorerSpec, StoredScore } from "./experiments";
import { ingest } from "./ingest";

// ADR-0029: `trayce replay` uploads replays to the ingest API; the UI only reads them.
const { read } = ingest("/v1/replays");

export type PassCount = { scorer: string; passed: number; scored: number };

export type SpanRef = { trace_id: string; span_id: string };

export type ReplayInfo = {
  id: string;
  /** The original trace's root span, for a fork as for a whole replay. */
  replay_of: SpanRef;
  /** ADR-0040: a fork's fork point, in the trace it forked (maybe a replay run); null when whole. */
  fork_of: SpanRef | null;
  /** `recorded` when every tool call was answered from the recording. */
  tool_mode: "live" | "recorded";
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
  /** A fork run's step where it left the recording and ran live; null when it didn't. */
  diverged_at: number | null;
  /** Prefix LLM calls whose request differed from the recording; the stub was still returned. */
  warnings: { step: number; fields: string[] }[];
};

/** `item` is null when unscored, or when the draft item was deleted after the replay ran. */
export type ReplayDetail = ReplayInfo & { item: Item | null; replay_runs: ReplayRun[] };

const id = (s: string) => encodeURIComponent(s);

/** Replays and forks of one original trace, and forks of it as the trace forked; newest first. */
export const listReplays = async (traceId: string) =>
  (await read<{ replays: ReplayInfo[] }>(`?trace_id=${id(traceId)}`))?.replays ?? [];
export const getReplay = (replay: string) => read<ReplayDetail>(`/${id(replay)}`);
