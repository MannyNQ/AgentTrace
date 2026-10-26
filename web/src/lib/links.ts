import "server-only";
import { headers } from "next/headers";
import { PROJECT_HEADER } from "@/lib/session";

/** Links inside the current project: `at("/traces/x")` is `/p/<slug>/traces/x`. The slug is the
 * one the proxy took from this request's URL. */
export async function projectLinks(): Promise<(path: string) => string> {
  const slug = (await headers()).get(PROJECT_HEADER);
  if (!slug) throw new Error("project links are only for pages under /p/<slug>/");
  return (path: string) => `/p/${slug}${path}`;
}
