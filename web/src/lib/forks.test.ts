import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { forkCheck, prefixToolCalls, whyNot, type ForkSpan } from "./forks.ts";

type Case = {
  step: { kind: "llm" | "tool"; status: "ok" | "error"; interceptor: string | null; truncated: string[]; output_recorded: string | null; output: unknown };
  reason: string | null;
};
// The same cases the SDK's rule is tested against, so the page and the CLI agree.
const cases: Case[] = JSON.parse(
  readFileSync(new URL("../../../sdk/python/tests/fixtures/why_not.json", import.meta.url), "utf8"),
);

test("the page's rule agrees with the CLI's on every shared case", () => {
  for (const { step, reason } of cases) {
    const o = step.output;
    const facts = {
      kind: step.kind,
      status: step.status,
      interceptor: step.interceptor,
      truncated: step.truncated,
      outputRecorded: step.output_recorded,
      hasResponse: typeof o === "object" && o !== null && !Array.isArray(o),
    };
    assert.equal(whyNot(facts), reason, JSON.stringify(step));
  }
});

const span = (spanId: string, kind: string, over: Partial<ForkSpan> = {}): ForkSpan => ({
  spanId,
  parentSpanId: "root",
  kind,
  name: kind === "llm" ? "anthropic.messages.create" : "lookup",
  status: "ok",
  outputPreview: kind === "llm" ? '{"id": "msg_1"}' : '{"a": 1}',
  attributes:
    kind === "llm"
      ? { "agenttrace.interceptor": "anthropic" }
      : { "agenttrace.interceptor": "call_tool", "agenttrace.output_recorded": "json" },
  ...over,
});

const trace = [span("root", "agent", { parentSpanId: "" }), span("l1", "llm"), span("t1", "tool"), span("l2", "llm")];

test("a fork point is a step whose prefix can be played back", () => {
  const check = forkCheck(trace, "l2");
  assert.ok(check.ok && check.step.index === 2);
  assert.equal(prefixToolCalls(trace, "l2"), 1);
  assert.equal(prefixToolCalls(trace, "t1"), 0);
});

test("the first step that can't be played back is named", () => {
  const raw = trace.map((s) => (s.spanId === "t1" ? { ...s, attributes: {} } : s));
  const check = forkCheck(raw, "l2");
  assert.ok(!check.ok && check.reason.startsWith("step 1 (tool lookup, span t1) can't be played back: it wasn't recorded by call_tool()"));
  assert.ok(forkCheck(raw, "l1").ok); // nothing before it to play back
  assert.deepEqual(forkCheck(trace, "root"), { ok: false, reason: "only an LLM or tool span can be a fork point" });
});

test("a step inside a played-back tool refuses the fork", () => {
  const nested = [...trace.slice(0, 3), span("inner", "llm", { parentSpanId: "t1" }), span("l2", "llm")];
  const check = forkCheck(nested, "l2");
  assert.ok(!check.ok && check.reason.includes("runs inside tool step 1"));
});
