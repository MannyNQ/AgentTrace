import "server-only";
import { readFileSync } from "node:fs";
import path from "node:path";
import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";
import { CLIENT_HEADER, clientAddress, PROJECT_HEADER, proxyHops, SESSION_COOKIE } from "@/lib/session";

// ADR-0031: the web app reads and writes only through the ingest API, which checks every request
// against the user's session and the project in the URL (ADR-0032). It holds no store
// credentials and no API key.
const INGEST_URL = process.env.INGEST_URL ?? "http://localhost:8000";

/**
 * A value from the repo-root .env, which docker-compose.yml reads too. Next only loads web/.env*,
 * so without this a value set there would be missed. Empty counts as unset, like compose's
 * `${VAR:-default}`.
 */
function rootEnv(key: string): string | undefined {
  let text: string;
  try {
    text = readFileSync(path.join(process.cwd(), "..", ".env"), "utf8");
  } catch {
    return undefined;
  }
  const values = [...text.matchAll(new RegExp(`^\\s*${key}\\s*=(.*)$`, "gm"))];
  const value = values.at(-1)?.[1].trim().replace(/^(["'])(.*)\1$/, "$2");
  return value || undefined;
}

/** Where people open the web app; the session cookie is `Secure` when it's https (design.md). */
export const PUBLIC_URL =
  process.env.AGENTTRACE_PUBLIC_URL || rootEnv("AGENTTRACE_PUBLIC_URL") || "http://localhost:3000";

/** Where agents, the CLI, and CI reach ingest, for the setup shown with a new key. */
export const PUBLIC_ENDPOINT =
  process.env.AGENTTRACE_ENDPOINT || rootEnv("AGENTTRACE_ENDPOINT") || "http://localhost:8000";

/** How many reverse proxies sit in front of the web app, for reading the browser's address. */
const PROXY_HOPS = proxyHops(process.env.AGENTTRACE_PROXY_HOPS || rootEnv("AGENTTRACE_PROXY_HOPS"));

/** A refusal from the API: `error` for people, plus whatever else the API said. */
export type ApiError = {
  status: number;
  error: string;
  items?: string[]; // publish: the offending items
  item_id?: string; // from-trace: the item already holding this trace
  dataset_id?: string; // a taken name: the dataset holding it, which may be archived
  rev?: number; // a stale edit: the item's current revision
  fields?: Record<string, string>; // validation errors by top-level field
};

export type Result<T> = { ok: true; data: T } | { ok: false; error: ApiError };

function toError(status: number, detail: unknown): ApiError {
  // The API answers {"error": ..., ...}, with `fields` for validation errors.
  if (detail && typeof detail === "object" && "error" in detail) {
    return { status, ...(detail as { error: string }) };
  }
  return { status, error: typeof detail === "string" ? detail : `ingest API answered ${status}` };
}

type Options = {
  /** Sign-in and setup calls: no session is sent, and a 401 is an answer, not an expiry. */
  anonymous?: boolean;
};

async function call<T>(method: string, path: string, body?: unknown, options: Options = {}): Promise<Result<T>> {
  const auth: Record<string, string> = {};
  const incoming = await headers();
  const client = clientAddress(incoming.get("x-forwarded-for"), PROXY_HOPS);
  if (client) auth[CLIENT_HEADER] = client;
  if (!options.anonymous) {
    const session = (await cookies()).get(SESSION_COOKIE)?.value;
    const project = incoming.get(PROJECT_HEADER);
    if (session) auth.Authorization = `Session ${session}`;
    if (project) auth["AgentTrace-Project"] = project;
  }
  let res: Response;
  try {
    res = await fetch(`${INGEST_URL}${path}`, {
      method,
      cache: "no-store",
      headers: { ...auth, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(30_000),
    });
  } catch (e) {
    return { ok: false, error: { status: 0, error: `ingest API unreachable: ${e instanceof Error ? e.message : e}` } };
  }
  // An expired or revoked session: sign in again. The login page replaces the cookie.
  if (res.status === 401 && !options.anonymous) redirect("/login?expired=1");
  if (res.status === 204) return { ok: true, data: undefined as T };
  const json = await res.json().catch(() => ({}));
  return res.ok ? { ok: true, data: json as T } : { ok: false, error: toError(res.status, json.detail) };
}

/** For reads in server components: a failed read is an error to show, not a refusal to handle. */
async function read<T>(path: string): Promise<T | null> {
  const r = await call<T>("GET", path);
  if (r.ok) return r.data;
  if (r.error.status === 404) return null;
  throw new Error(r.error.error);
}

/** Calls and reads under one API prefix, like `/v1/datasets`. */
export function ingest(prefix: string, options: Options = {}) {
  return {
    call: <T>(method: string, path: string, body?: unknown) => call<T>(method, prefix + path, body, options),
    read: <T>(path: string) => read<T>(prefix + path),
  };
}
