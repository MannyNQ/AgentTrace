import { test } from "node:test";
import assert from "node:assert/strict";
import { apiParams, describe, filterParams, parseFilter, without, zoneOr } from "./filters.ts";

test("a filter round-trips through the page's params", () => {
  const f = parseFilter({
    since: "2026-10-27T13:00:00Z",
    until: "2026-10-27T14:00:00+00:00",
    status: "error",
    tag: ["vip", "eu"],
    user: "u1",
    kind: "tool",
    name: "refund",
    errors: "1",
    evals: "1",
  });
  assert.deepEqual(parseFilter(Object.fromEntries(groupParams(filterParams(f)))), f);
});

test("ingest gets its own parameter names", () => {
  const f = parseFilter({ tag: "vip", service: "support", model: "m1", errors: "1", replays: "1" });
  assert.equal(
    apiParams(f).toString(),
    "tag=vip&service_name=support&span_model=m1&span_error=true&show_replays=true",
  );
});

test("malformed values are left out rather than failing the page", () => {
  const f = parseFilter({
    since: "2026-10-27T13:00:00", // no zone
    status: "maybe",
    kind: "database",
    errors: "1", // with no span condition to apply to
    until: "yesterday",
  });
  assert.deepEqual(f, { tags: [], errors: false, evals: false, replays: false,
    since: undefined, until: undefined, status: undefined, user: undefined, service: undefined,
    kind: undefined, name: undefined, model: undefined });
  const backwards = parseFilter({ since: "2026-10-28T00:00:00Z", until: "2026-10-27T00:00:00Z" });
  assert.equal(backwards.since, undefined);
});

test("chips describe each condition, and removing one keeps the rest", () => {
  const f = parseFilter({ tag: "vip", kind: "llm", model: "m1", errors: "1", status: "ok" });
  assert.deepEqual(describe(f).map(([, s]) => s), [
    "status ok",
    "tagged vip",
    "has an errored llm span on m1",
  ]);
  assert.deepEqual(without(f, "kind"), { ...f, kind: undefined, model: undefined, errors: false });
  assert.equal(describe(parseFilter({ kind: "tool" }))[0][1], "has a tool span");
});

test("a zone must be a real IANA name", () => {
  assert.equal(zoneOr("Europe/Paris"), "Europe/Paris");
  assert.equal(zoneOr("Mars/Olympus"), "UTC");
  assert.equal(zoneOr(undefined), "UTC");
});

function groupParams(q: URLSearchParams): [string, string | string[]][] {
  const out = new Map<string, string | string[]>();
  for (const [k, v] of q) {
    const had = out.get(k);
    out.set(k, had === undefined ? v : [...(Array.isArray(had) ? had : [had]), v]);
  }
  return [...out];
}
