import type { ToolCall } from "./datasets";

/**
 * Lines up a sample's tool calls with the expected ones so the page can mark where they part.
 * Pass/fail and the reason come from the Python trajectory scorer (sdk agenttrace.eval.scorers);
 * this mirrors its matching rules only to place the marks: same tool, and every listed argument
 * equal, unlisted ones ignored.
 */

export type Alignment = {
  /** For each expected call, the actual call it matched, or null. */
  expected: (number | null)[];
  /** For each actual call, the expected call it matched, or null (an extra call). */
  actual: (number | null)[];
  /** Where the comparison first fails: an expected call (missing or out of place) or an actual one. */
  firstMismatch: { side: "expected" | "actual"; index: number } | null;
};

function canonical(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(canonical).join(",")}]`;
  if (v && typeof v === "object") {
    const entries = Object.entries(v as Record<string, unknown>).sort(([a], [b]) => (a < b ? -1 : 1));
    return `{${entries.map(([k, x]) => `${JSON.stringify(k)}:${canonical(x)}`).join(",")}}`;
  }
  return JSON.stringify(v);
}

export function callMatches(expected: ToolCall, actual: ToolCall): boolean {
  if (expected.tool !== actual.tool) return false;
  const got = actual.args ?? {};
  return Object.entries(expected.args ?? {}).every(
    ([k, v]) => k in got && canonical(got[k]) === canonical(v),
  );
}

function exact(expected: ToolCall[], actual: ToolCall[]): Alignment {
  const e: (number | null)[] = expected.map(() => null);
  const a: (number | null)[] = actual.map(() => null);
  let firstMismatch: Alignment["firstMismatch"] = null;
  for (let i = 0; i < Math.max(expected.length, actual.length); i++) {
    if (i < expected.length && i < actual.length && callMatches(expected[i], actual[i])) {
      e[i] = i;
      a[i] = i;
    } else if (!firstMismatch) {
      firstMismatch = i < actual.length ? { side: "actual", index: i } : { side: "expected", index: i };
    }
  }
  return { expected: e, actual: a, firstMismatch };
}

function inOrder(expected: ToolCall[], actual: ToolCall[]): Alignment {
  const e: (number | null)[] = expected.map(() => null);
  const a: (number | null)[] = actual.map(() => null);
  let j = 0;
  let firstMismatch: Alignment["firstMismatch"] = null;
  for (let i = 0; i < expected.length; i++) {
    let k = j;
    while (k < actual.length && !callMatches(expected[i], actual[k])) k++;
    if (k === actual.length) {
      firstMismatch ??= { side: "expected", index: i };
      continue; // keep marking: later expected calls may still be found after j
    }
    e[i] = k;
    a[k] = i;
    j = k + 1;
  }
  return { expected: e, actual: a, firstMismatch };
}

function anyOrder(expected: ToolCall[], actual: ToolCall[]): Alignment {
  const e: (number | null)[] = expected.map(() => null);
  const a: (number | null)[] = actual.map(() => null);
  const place = (i: number): boolean => {
    if (i === expected.length) return true;
    for (let k = 0; k < actual.length; k++) {
      if (a[k] === null && callMatches(expected[i], actual[k])) {
        a[k] = i;
        e[i] = k;
        if (place(i + 1)) return true;
        a[k] = null;
        e[i] = null;
      }
    }
    return false;
  };
  if (place(0)) return { expected: e, actual: a, firstMismatch: null };
  // No full assignment: match greedily for display, and mark the first expected call left over.
  e.fill(null);
  a.fill(null);
  expected.forEach((call, i) => {
    const k = actual.findIndex((x, n) => a[n] === null && callMatches(call, x));
    if (k >= 0) {
      a[k] = i;
      e[i] = k;
    }
  });
  const missing = e.indexOf(null);
  return { expected: e, actual: a, firstMismatch: { side: "expected", index: Math.max(missing, 0) } };
}

export function align(mode: "exact" | "in_order" | "any_order", expected: ToolCall[], actual: ToolCall[]): Alignment {
  return mode === "exact" ? exact(expected, actual) : mode === "in_order" ? inOrder(expected, actual) : anyOrder(expected, actual);
}

/**
 * Where a replay run's trajectory first differs from the original's (GLOSSARY.md "First
 * difference"): step by step, a different tool or arguments that aren't equal as JSON. Unlike
 * `callMatches`, every argument counts, since the original is a recording, not an expectation.
 * A trajectory that is a prefix of the other differs at the first missing step. Null when equal.
 */
export function firstDifference(original: ToolCall[], run: ToolCall[]): number | null {
  for (let i = 0; i < Math.max(original.length, run.length); i++) {
    const a = original[i];
    const b = run[i];
    if (!a || !b || a.tool !== b.tool || canonical(a.args ?? null) !== canonical(b.args ?? null)) {
      return i;
    }
  }
  return null;
}
