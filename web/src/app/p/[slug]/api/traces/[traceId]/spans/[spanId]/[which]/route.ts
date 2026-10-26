import { unstable_rethrow } from "next/navigation";
import { readPayload } from "@/lib/traces";

/**
 * A span's full input or output, fetched only when asked for. Ingest reads it from object
 * storage (ADR-0009) after checking the session and the project in this URL (ADR-0031).
 */
export async function GET(
  _req: Request,
  ctx: RouteContext<"/p/[slug]/api/traces/[traceId]/spans/[spanId]/[which]">,
) {
  const { traceId, spanId, which } = await ctx.params;
  if (which !== "input" && which !== "output") {
    return new Response("not found", { status: 404 });
  }
  try {
    const text = await readPayload(traceId, spanId, which);
    if (text === null) return new Response("span not found", { status: 404 });
    return new Response(text, { headers: { "Content-Type": "application/json; charset=utf-8" } });
  } catch (e) {
    unstable_rethrow(e); // an expired session's redirect to /login
    return new Response(e instanceof Error ? e.message : String(e), { status: 503 });
  }
}
