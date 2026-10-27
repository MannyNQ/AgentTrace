import { NextResponse, type NextRequest } from "next/server";
import { DEFAULT_PROJECT, PROJECT_COOKIE, PROJECT_HEADER, SESSION_COOKIE, SLUG } from "@/lib/session";

// Runs before every page (Next's proxy, formerly middleware). It only checks that a session
// cookie is present; ingest decides whether it's valid, and a page whose session has expired is
// sent to /login by the ingest client. Project pages live under /p/<slug>/ (design.md, Phase 6).

const INGEST_URL = process.env.INGEST_URL ?? "http://localhost:8000";

/** Pages for signing in, and the status page, which shows only what ingest's /health shows. */
const PUBLIC = /^\/(login|setup|status)$|^\/(invite|reset)\/[^/]+$/;
const PROJECT_PATH = /^\/p\/([^/]+)(?:\/|$)/;
/** Paths from before projects were in URLs, as PR comments and the CLI printed them. */
const OLD_PATH = /^\/(?:api\/)?(traces|datasets|experiments|replays)(?:\/([^/]+))?/;
const KIND: Record<string, string> = {
  traces: "trace",
  datasets: "dataset",
  experiments: "experiment",
  replays: "replay",
};

export async function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  // Only this proxy names the project; a copy sent by the client is dropped.
  const requestHeaders = new Headers(request.headers);
  requestHeaders.delete(PROJECT_HEADER);
  const pass = () => NextResponse.next({ request: { headers: requestHeaders } });

  if (PUBLIC.test(pathname)) return pass();
  const session = request.cookies.get(SESSION_COOKIE)?.value;
  if (!session) {
    const login = new URL("/login", request.url);
    if (pathname !== "/") login.searchParams.set("next", pathname + search);
    return NextResponse.redirect(login);
  }
  const remembered = request.cookies.get(PROJECT_COOKIE)?.value ?? "";
  const last = SLUG.test(remembered) ? remembered : DEFAULT_PROJECT;
  if (pathname === "/") return NextResponse.redirect(new URL(`/p/${last}`, request.url));

  const project = PROJECT_PATH.exec(pathname);
  if (project && !SLUG.test(project[1])) {
    // No project can have this slug; without the header, the page under /p/[slug] would fail.
    return NextResponse.rewrite(new URL("/not-found-project", request.url), { status: 404 });
  }
  if (project) {
    requestHeaders.set(PROJECT_HEADER, project[1]);
    const response = pass();
    response.cookies.set(PROJECT_COOKIE, project[1], { path: "/", sameSite: "lax", maxAge: 365 * 86400 });
    return response;
  }

  const old = OLD_PATH.exec(pathname);
  if (old) {
    const [, section, id] = old;
    const slug = (id && (await locate(KIND[section], id, session))) || last;
    return NextResponse.redirect(new URL(`/p/${slug}${pathname}${search}`, request.url));
  }
  return pass();
}

/** The slug of the project in the user's org holding this entity, or null. The answer is checked
 * like any read, so it can't reveal another org's entity. */
async function locate(kind: string, id: string, session: string): Promise<string | null> {
  try {
    const res = await fetch(`${INGEST_URL}/v1/locate/${kind}/${encodeURIComponent(id)}`, {
      headers: { Authorization: `Session ${session}` },
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    return res.ok ? ((await res.json()) as { slug: string }).slug : null;
  } catch {
    return null;
  }
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
