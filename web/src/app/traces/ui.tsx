import type { SpanKind, TraceStatus } from "@/lib/traces";

const STATUS_STYLE: Record<TraceStatus, string> = {
  ok: "text-good",
  error: "text-bad",
  running: "text-muted",
};

export function StatusBadge({ status }: { status: TraceStatus }) {
  return (
    <span className={`font-mono text-xs ${STATUS_STYLE[status]}`} data-status={status}>
      {status}
    </span>
  );
}

export function KindBadge({ kind }: { kind: SpanKind }) {
  return (
    <span className="shrink-0 rounded border border-line px-1 font-mono text-[10px] uppercase text-muted">
      {kind}
    </span>
  );
}
