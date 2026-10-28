/**
 * Whether a span can be a fork point (ADR-0040), for the trace page's "Fork from here". The CLI
 * decides for real (sdk `agenttrace.eval.fork`); this mirrors its rule so the page can say why
 * before anyone runs the command. Both are checked against sdk/python/tests/fixtures/why_not.json.
 */

export type StepFacts = {
  kind: "llm" | "tool";
  status: "ok" | "error";
  interceptor: string | null;
  truncated: string[];
  outputRecorded: string | null;
  /** The recorded output is a whole response object, as an LLM step's must be. */
  hasResponse: boolean;
};

const TOOL_HOOKS = ["call_tool", "trace"];
const EXACT_OUTPUTS = ["json", "text"];

/** Why the recording can't stand in for this step, or null when it can. */
export function whyNot(s: StepFacts): string | null {
  if (s.kind === "llm") {
    if (s.interceptor !== "anthropic") return "it wasn't recorded by instrument_anthropic(), so nothing can answer it";
    if (s.truncated.length) return `the SDK cut its ${s.truncated.join(" and ")} to fit a request`;
    if (s.status === "error") return "the call failed, and a failed LLM call isn't recorded in full";
    if (!s.hasResponse) return "it has no recorded response; it may predate Phase 9's SDK";
    return null;
  }
  if (!s.interceptor || !TOOL_HOOKS.includes(s.interceptor)) {
    return "it wasn't recorded by call_tool() or @trace(kind='tool'), so nothing can answer it";
  }
  if (s.truncated.includes("output")) return "the SDK cut its output to fit a request";
  if (s.status !== "error" && !EXACT_OUTPUTS.includes(s.outputRecorded ?? "")) {
    if (s.outputRecorded === null) return "it doesn't say how its output was recorded; it may predate Phase 9's SDK";
    return "its output didn't survive recording (a tuple, an object, or another non-JSON type)";
  }
  return null;
}

/** The fields of a trace page span this needs. */
export type ForkSpan = {
  spanId: string;
  parentSpanId: string;
  kind: string;
  name: string;
  status: "ok" | "error";
  outputPreview: string;
  attributes: Record<string, string>;
};

export type Step = ForkSpan & { index: number; facts: StepFacts };

/** The trace's LLM and tool spans as a fork counts them: in start order, from 0. */
export function steps(spans: ForkSpan[]): Step[] {
  return spans
    .filter((s) => s.kind === "llm" || s.kind === "tool")
    .map((s, index) => ({
      ...s,
      index,
      facts: {
        kind: s.kind as "llm" | "tool",
        status: s.status,
        interceptor: s.attributes["agenttrace.interceptor"] ?? null,
        truncated: (s.attributes["agenttrace.truncated"] ?? "").split(",").filter((w) => w === "input" || w === "output"),
        outputRecorded: s.attributes["agenttrace.output_recorded"] ?? null,
        // A preview is the first 1 KB, so an object is told by its opening brace.
        hasResponse: s.outputPreview.trimStart().startsWith("{"),
      },
    }));
}

const label = (s: Step) => `step ${s.index} (${s.kind} ${s.name}, span ${s.spanId})`;

export type ForkCheck = { ok: true; step: Step } | { ok: false; reason: string };

/** Whether `spanId` can be a fork point, as `agenttrace replay --from` would decide. */
export function forkCheck(spans: ForkSpan[], spanId: string): ForkCheck {
  const all = steps(spans);
  const fork = all.find((s) => s.spanId === spanId);
  if (!fork) return { ok: false, reason: "only an LLM or tool span can be a fork point" };
  if (!fork.facts.interceptor) {
    return { ok: false, reason: `${label(fork)} wasn't recorded by an SDK hook, so a fork can't tell when the run gets there` };
  }
  const played = all.filter((s) => s.index < fork.index);
  for (const s of played) {
    const reason = whyNot(s.facts);
    if (reason) return { ok: false, reason: `${label(s)} can't be played back: ${reason}` };
  }
  const playedTools = new Map(played.filter((s) => s.kind === "tool").map((s) => [s.spanId, s]));
  for (const s of all) {
    const parent = playedTools.get(s.parentSpanId);
    if (parent) {
      return {
        ok: false,
        reason: `${label(s)} runs inside tool ${label(parent)}; playing back tools that make their own LLM or tool calls isn't supported yet`,
      };
    }
  }
  return { ok: true, step: fork };
}

/** How many of a run's tool calls were played back before the fork point. */
export function prefixToolCalls(spans: ForkSpan[], forkSpanId: string): number | null {
  const all = steps(spans);
  const fork = all.find((s) => s.spanId === forkSpanId);
  return fork ? all.filter((s) => s.kind === "tool" && s.index < fork.index).length : null;
}
