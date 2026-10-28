import Link from "next/link";

// Hand-rolled SVG charts for the overview (architecture.md, Phase 8): rendered on the server, one link
// per bucket to the traces behind it, and the bucket's numbers in its <title> as the hover
// readout. Colors are the theme's series tokens (globals.css), assigned in a fixed order.

export type Series = { key: string; label: string; color: string };

export type Column = {
  label: string; // the bucket's start, as the axis shows it
  href: string; // the traces behind the bucket
  values: Record<string, number | null>;
};

const W = 720;
const H = 200;
const PAD = { top: 10, right: 56, bottom: 22, left: 52 };
const PLOT_W = W - PAD.left - PAD.right;
const PLOT_H = H - PAD.top - PAD.bottom;

/** A round number at or above `max`, so the top gridline reads cleanly. */
export function niceMax(max: number): number {
  if (!(max > 0)) return 1;
  const step = 10 ** Math.floor(Math.log10(max));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * step >= max) return m * step;
  return 10 * step;
}

function Frame({
  label,
  top,
  format,
  columns,
  children,
}: {
  label: string;
  top: number;
  format: (v: number) => string;
  columns: Column[];
  children: React.ReactNode;
}) {
  const y = (v: number) => PAD.top + PLOT_H - (v / top) * PLOT_H;
  const ticks = [0, top / 2, top];
  const xs = columns.length > 1 ? [0, Math.floor((columns.length - 1) / 2), columns.length - 1] : [0];
  const slot = PLOT_W / Math.max(columns.length, 1);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={label}>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} stroke="var(--line)" strokeWidth={1} />
          <text x={PAD.left - 6} y={y(t)} dy="0.32em" textAnchor="end" fontSize={11} fill="var(--muted)">
            {t === 0 ? "0" : format(t)}
          </text>
        </g>
      ))}
      {xs.map((i) => (
        <text
          key={i}
          x={PAD.left + slot * (i + 0.5)}
          y={H - 6}
          textAnchor={i === 0 && xs.length > 1 ? "start" : i === columns.length - 1 && xs.length > 1 ? "end" : "middle"}
          fontSize={11}
          fill="var(--muted)"
        >
          {columns[i]?.label}
        </text>
      ))}
      {children}
    </svg>
  );
}

function tooltip(c: Column, series: Series[], format: (v: number) => string): string {
  const lines = series.map((s) => `${s.label}: ${c.values[s.key] === null ? "—" : format(c.values[s.key] ?? 0)}`);
  return [c.label, ...lines, "Click for these traces"].join("\n");
}

/** Bars stacked bottom to top in `series` order, one per column. */
export function StackedBars({
  label,
  columns,
  series,
  format,
}: {
  label: string;
  columns: Column[];
  series: Series[];
  format: (v: number) => string;
}) {
  const totals = columns.map((c) => series.reduce((sum, s) => sum + (c.values[s.key] ?? 0), 0));
  const top = niceMax(Math.max(0, ...totals));
  const slot = PLOT_W / Math.max(columns.length, 1);
  const bar = Math.max(2, Math.min(28, slot * 0.7));
  const scale = (v: number) => (v / top) * PLOT_H;
  return (
    <Frame label={label} top={top} format={format} columns={columns}>
      {columns.map((c, i) => {
        const x = PAD.left + slot * i + (slot - bar) / 2;
        let base = PAD.top + PLOT_H;
        return (
          <a key={i} href={c.href} data-bucket={c.label}>
            <title>{tooltip(c, series, format)}</title>
            <rect x={PAD.left + slot * i} y={PAD.top} width={slot} height={PLOT_H} fill="transparent" />
            {series.map((s) => {
              const v = c.values[s.key] ?? 0;
              if (v <= 0) return null;
              const h = Math.max(1, scale(v));
              base -= h;
              // A 1px surface-colored stroke on each segment leaves a 2px gap between them.
              return (
                <rect
                  key={s.key}
                  x={x}
                  y={base}
                  width={bar}
                  height={h}
                  rx={2}
                  fill={s.color}
                  stroke="var(--background)"
                  strokeWidth={1}
                />
              );
            })}
          </a>
        );
      })}
    </Frame>
  );
}

/** One 2px line per series, broken where a bucket has no value, labeled at its last point. */
export function Lines({
  label,
  columns,
  series,
  format,
}: {
  label: string;
  columns: Column[];
  series: Series[];
  format: (v: number) => string;
}) {
  const all = columns.flatMap((c) => series.map((s) => c.values[s.key] ?? 0));
  const top = niceMax(Math.max(0, ...all));
  const slot = PLOT_W / Math.max(columns.length, 1);
  const x = (i: number) => PAD.left + slot * (i + 0.5);
  const y = (v: number) => PAD.top + PLOT_H - (v / top) * PLOT_H;
  const ends = endLabels(series, columns, x, y);
  // Later series are drawn first, so where lines coincide the first (the headline) is on top.
  return (
    <Frame label={label} top={top} format={format} columns={columns}>
      {[...series].reverse().map((s) => {
        let d = "";
        let pen = false;
        columns.forEach((c, i) => {
          const v = c.values[s.key];
          if (v === null || v === undefined) {
            pen = false;
            return;
          }
          d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
          pen = true;
        });
        const end = ends.get(s.key);
        return (
          <g key={s.key}>
            <path d={d} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" />
            {columns.map((c, i) => {
              const v = c.values[s.key];
              // A lone point has no line to show it; mark it.
              const alone = v != null && columns[i - 1]?.values[s.key] == null && columns[i + 1]?.values[s.key] == null;
              return alone ? <circle key={i} cx={x(i)} cy={y(v)} r={3} fill={s.color} /> : null;
            })}
            {end && (
              <text x={end[0] + 6} y={end[1]} dy="0.32em" fontSize={11} fill="var(--foreground)">
                {s.label}
              </text>
            )}
          </g>
        );
      })}
      {columns.map((c, i) => (
        <a key={i} href={c.href} data-bucket={c.label}>
          <title>{tooltip(c, series, format)}</title>
          <rect x={PAD.left + slot * i} y={PAD.top} width={slot} height={PLOT_H} fill="transparent" />
        </a>
      ))}
    </Frame>
  );
}

const LABEL_GAP = 12;

/** Where each series' name goes, beside its last point, nudged apart so none overlap. */
function endLabels(
  series: Series[],
  columns: Column[],
  x: (i: number) => number,
  y: (v: number) => number,
): Map<string, [number, number]> {
  const found: { key: string; x: number; y: number }[] = [];
  for (const s of series) {
    for (let i = columns.length - 1; i >= 0; i--) {
      const v = columns[i].values[s.key];
      if (v != null) {
        found.push({ key: s.key, x: x(i), y: y(v) });
        break;
      }
    }
  }
  found.sort((a, b) => a.y - b.y);
  for (let i = 1; i < found.length; i++) found[i].y = Math.max(found[i].y, found[i - 1].y + LABEL_GAP);
  return new Map(found.map((f) => [f.key, [f.x, f.y]]));
}

/** Series names beside a swatch; text stays in text colors. */
export function Legend({ series }: { series: Series[] }) {
  return (
    <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted" aria-label="Legend">
      {series.map((s) => (
        <li key={s.key} className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: s.color }} aria-hidden />
          {s.label}
        </li>
      ))}
    </ul>
  );
}

/** A headline number, its change from the previous period, and a link to its traces. */
export function Tile({
  label,
  value,
  change,
  note,
  href,
}: {
  label: string;
  value: string;
  change?: string;
  note?: string;
  href?: string;
}) {
  const body = (
    <>
      <p className="text-xs text-muted">{label}</p>
      <p className="mt-1 font-mono text-2xl tracking-tight" data-tile-value>
        {value}
      </p>
      <p className="mt-1 text-xs text-muted" data-tile-change>
        {change ?? note ?? " "}
      </p>
    </>
  );
  return (
    <div className="rounded-md border border-line p-3" data-tile={label}>
      {href ? (
        <Link href={href} className="block hover:opacity-80">
          {body}
        </Link>
      ) : (
        body
      )}
    </div>
  );
}
