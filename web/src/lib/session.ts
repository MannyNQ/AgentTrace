// The names the proxy, the ingest client, and the auth actions share (ADR-0032). Kept free of
// server-only imports so the proxy can use them.

/** The session token from ingest, in an httpOnly cookie the browser never reads. */
export const SESSION_COOKIE = "agenttrace_session";
/** The last project opened, so `/` and old links can go back to it. Not a secret. */
export const PROJECT_COOKIE = "agenttrace_project";
/** Set by the proxy from the `/p/<slug>/` URL, and only by it: it overwrites any copy a client
 * sends, so the ingest client can trust it as the project a request acts on. */
export const PROJECT_HEADER = "x-agenttrace-project";
export const DEFAULT_PROJECT = "default";
/** A project slug as ingest makes them (design.md, Phase 6). */
export const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

const ORIGIN = "http://same.invalid";

/** A same-site path to return to after signing in, or null. It's parsed the way a browser will
 * parse the redirect, so tricks like `//host`, `/\host`, or `/<tab>/host` (browsers drop tabs
 * and newlines) resolve to another origin and are refused. */
export function safeNext(next: string | null | undefined): string | null {
  if (!next || !next.startsWith("/")) return null;
  let url: URL;
  try {
    url = new URL(next, ORIGIN);
  } catch {
    return null;
  }
  return url.origin === ORIGIN ? url.pathname + url.search + url.hash : null;
}
