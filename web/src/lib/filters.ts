// The trace filter as the web app's URLs carry it, for the trace list and the overview. Ingest
// parses the same conditions under its own names (ADR-0038), so a chart and the list it links to
// select the same traces. No server-only imports: tests run this directly.

export const STATUSES = ["ok", "error", "running"] as const;
export const KINDS = ["llm", "tool", "agent", "retrieval", "custom"] as const;
export type Status = (typeof STATUSES)[number];
export type Kind = (typeof KINDS)[number];

export type Filter = {
  since?: string; // ISO times with a zone, as ingest requires
  until?: string;
  status?: Status;
  tags: string[];
  user?: string;
  service?: string;
  kind?: Kind;
  name?: string;
  model?: string;
  errors: boolean; // only errored spans of that kind, name, or model
  evals: boolean; // include eval samples' traces
  replays: boolean; // include replay runs
};

export type Params = Record<string, string | string[] | undefined>;

const MAX_TAGS = 20;
const MAX_TEXT = 256;

function one(v: string | string[] | undefined): string | undefined {
  const s = Array.isArray(v) ? v[0] : v;
  const t = s?.trim();
  return t && t.length <= MAX_TEXT ? t : undefined;
}

function time(v: string | string[] | undefined): string | undefined {
  const s = one(v);
  // Ingest refuses a time without a zone; a hand-edited URL shouldn't fail the whole page.
  return s && /(Z|[+-]\d\d:\d\d)$/.test(s) && !Number.isNaN(Date.parse(s)) ? s : undefined;
}

function pick<T extends string>(v: string | string[] | undefined, allowed: readonly T[]): T | undefined {
  const s = one(v);
  return allowed.find((a) => a === s);
}

/** A filter from a page's search params; anything malformed is left out. */
export function parseFilter(p: Params): Filter {
  const tags = (Array.isArray(p.tag) ? p.tag : p.tag ? [p.tag] : [])
    .map((t) => t.trim())
    .filter((t) => t && t.length <= MAX_TEXT)
    .slice(0, MAX_TAGS);
  let since = time(p.since);
  let until = time(p.until);
  if (since && until && Date.parse(since) >= Date.parse(until)) [since, until] = [undefined, undefined];
  const kind = pick(p.kind, KINDS);
  const name = one(p.name);
  const model = one(p.model);
  return {
    since,
    until,
    status: pick(p.status, STATUSES),
    tags,
    user: one(p.user),
    service: one(p.service),
    kind,
    name,
    model,
    errors: p.errors === "1" && Boolean(kind || name || model),
    evals: p.evals === "1",
    replays: p.replays === "1",
  };
}

export const EMPTY: Filter = { tags: [], errors: false, evals: false, replays: false };

/** The filter as the web app's search params, in a stable order. */
export function filterParams(f: Filter): URLSearchParams {
  const q = new URLSearchParams();
  const set = (k: string, v: string | undefined) => v && q.set(k, v);
  set("since", f.since);
  set("until", f.until);
  set("status", f.status);
  for (const t of f.tags) q.append("tag", t);
  set("user", f.user);
  set("service", f.service);
  set("kind", f.kind);
  set("name", f.name);
  set("model", f.model);
  if (f.errors) q.set("errors", "1");
  if (f.evals) q.set("evals", "1");
  if (f.replays) q.set("replays", "1");
  return q;
}

/** The filter as ingest's parameters (`/v1/traces`, `/v1/analytics`). */
export function apiParams(f: Filter): URLSearchParams {
  const q = new URLSearchParams();
  const set = (k: string, v: string | undefined) => v && q.set(k, v);
  set("since", f.since);
  set("until", f.until);
  set("status", f.status);
  for (const t of f.tags) q.append("tag", t);
  set("user_id", f.user);
  set("service_name", f.service);
  set("span_kind", f.kind);
  set("span_name", f.name);
  set("span_model", f.model);
  if (f.errors) q.set("span_error", "true");
  if (f.evals) q.set("show_evals", "true");
  if (f.replays) q.set("show_replays", "true");
  return q;
}

/** What a filter narrows to, one phrase per condition, for the chips above a list. */
export function describe(f: Filter, zone = "UTC"): [keyof Filter, string][] {
  const at = (iso: string) =>
    new Intl.DateTimeFormat("en-GB", {
      timeZone: zone,
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(iso));
  const out: [keyof Filter, string][] = [];
  if (f.since) out.push(["since", `from ${at(f.since)}`]);
  if (f.until) out.push(["until", `before ${at(f.until)}`]);
  if (f.status) out.push(["status", `status ${f.status}`]);
  if (f.tags.length) out.push(["tags", `tagged ${f.tags.join(", ")}`]);
  if (f.user) out.push(["user", `user ${f.user}`]);
  if (f.service) out.push(["service", `service ${f.service}`]);
  const span = [f.errors ? "errored" : "", f.kind ?? "", "span"].filter(Boolean).join(" ");
  if (f.kind || f.name || f.model) {
    const what = [f.name && `named ${f.name}`, f.model && `on ${f.model}`].filter(Boolean).join(" ");
    out.push(["kind", `has an ${span}${what ? ` ${what}` : ""}`.replace(/^has an ([^aeiou])/, "has a $1")]);
  }
  if (f.evals) out.push(["evals", "with eval runs"]);
  if (f.replays) out.push(["replays", "with replays"]);
  return out;
}

/** The filter without one condition, for a chip's ✕. Span conditions go together. */
export function without(f: Filter, key: keyof Filter): Filter {
  if (key === "kind") return { ...f, kind: undefined, name: undefined, model: undefined, errors: false };
  if (key === "tags") return { ...f, tags: [] };
  if (key === "evals" || key === "replays" || key === "errors") return { ...f, [key]: false };
  return { ...f, [key]: undefined };
}

/** Set by the viewer's browser (overview/zone.tsx) so the overview buckets their hours and days. */
export const ZONE_COOKIE = "trayce-tz";

/** A valid IANA zone name, else UTC. */
export function zoneOr(tz: string | undefined, fallback = "UTC"): string {
  if (!tz || tz.length > 64) return fallback;
  try {
    new Intl.DateTimeFormat("en", { timeZone: tz });
    return tz;
  } catch {
    return fallback;
  }
}
