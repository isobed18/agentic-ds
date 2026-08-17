/**
 * Charts, drawn as inline SVG.
 *
 * No charting library. The deployment target is air-gapped and the artifact CSP
 * blocks external hosts, so a CDN import is not an option and bundling a full
 * chart library would add hundreds of kilobytes to render six shapes. These are
 * the six the backend actually emits.
 *
 * Every chart takes counts, quantiles or correlations — never rows. A local
 * investigator may inspect a read-only copy, but the browser only receives its
 * validated aggregate manifest.
 *
 * Each shape renders at two sizes. `compact` is the thumbnail in the analysis
 * strip, where the job is to be recognisable at a glance, so labels and axes are
 * dropped rather than shrunk into illegibility.
 */
import { useId } from "react";

export interface ChartSpec {
  kind: string;
  x_label?: string;
  y_label?: string;
  unit?: "percent" | "ratio";
  signed?: boolean;
  series?: {
    label: string;
    value?: number;
    secondary?: number | null;
    p25?: number | null;
    p50?: number | null;
    p75?: number | null;
    lower?: number | null;
    upper?: number | null;
    outlier_rate?: number;
  }[];
  bins?: { lower: number; upper: number; count: number }[];
  columns?: string[];
  values?: (number | null)[][];
  min?: number;
  max?: number;
}

const PALETTE = {
  bar: "var(--chart-bar, #3b82f6)",
  alt: "var(--chart-alt, #10b981)",
  warn: "var(--chart-warn, #f59e0b)",
  stop: "var(--chart-stop, #ef4444)",
  axis: "var(--chart-axis, #94a3b8)",
  grid: "var(--chart-grid, #e2e8f0)",
};

const fmtTick = (v: number) =>
  Math.abs(v) >= 1000
    ? `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k`
    : Number.isInteger(v)
      ? String(v)
      : v.toFixed(2);

export function Chart({ spec, compact = false }: { spec: ChartSpec; compact?: boolean }) {
  switch (spec.kind) {
    case "bar":
      return <BarChart spec={spec} compact={compact} />;
    case "histogram":
      return <HistogramChart spec={spec} compact={compact} />;
    case "hbar":
      return <HBarChart spec={spec} compact={compact} />;
    case "heatmap":
      return <HeatmapChart spec={spec} compact={compact} />;
    case "box":
      return <BoxChart spec={spec} compact={compact} />;
    case "donut":
      return <DonutChart spec={spec} compact={compact} />;
    default:
      return <NoChart />;
  }
}

function NoChart() {
  return (
    <div className="flex h-full w-full items-center justify-center text-[11px] text-ink-faint">
      nothing measured
    </div>
  );
}

/** Vertical bars — class counts. */
function BarChart({ spec, compact }: { spec: ChartSpec; compact?: boolean }) {
  const series = spec.series ?? [];
  if (!series.length) return <NoChart />;
  const w = 320;
  const h = compact ? 96 : 220;
  const padL = compact ? 4 : 46;
  const padB = compact ? 4 : 34;
  const padT = compact ? 6 : 16;
  const max = Math.max(...series.map((s) => s.value ?? 0), 1);
  const bw = (w - padL - 8) / series.length;

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-full w-full" preserveAspectRatio="none">
      {!compact && <Gridlines w={w} h={h} padL={padL} padB={padB} padT={padT} max={max} />}
      {series.map((s, i) => {
        const barH = ((s.value ?? 0) / max) * (h - padT - padB);
        return (
          <g key={s.label}>
            <rect
              x={padL + i * bw + bw * 0.15}
              y={h - padB - barH}
              width={bw * 0.7}
              height={Math.max(barH, 1)}
              rx={2}
              fill={i === 0 ? PALETTE.bar : PALETTE.alt}
            />
            {!compact && (
              <text
                x={padL + i * bw + bw / 2}
                y={h - padB + 13}
                textAnchor="middle"
                fontSize="10"
                fill={PALETTE.axis}
              >
                {s.label.length > 12 ? `${s.label.slice(0, 11)}…` : s.label}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

/** Contiguous bins — a numeric distribution's shape. */
function HistogramChart({ spec, compact }: { spec: ChartSpec; compact?: boolean }) {
  const bins = spec.bins ?? [];
  if (!bins.length) return <NoChart />;
  const w = 320;
  const h = compact ? 96 : 220;
  const padL = compact ? 4 : 46;
  const padB = compact ? 4 : 30;
  const padT = compact ? 6 : 16;
  const max = Math.max(...bins.map((b) => b.count), 1);
  const bw = (w - padL - 8) / bins.length;

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-full w-full" preserveAspectRatio="none">
      {!compact && <Gridlines w={w} h={h} padL={padL} padB={padB} padT={padT} max={max} />}
      {bins.map((b, i) => {
        const barH = (b.count / max) * (h - padT - padB);
        return (
          <rect
            key={i}
            x={padL + i * bw}
            y={h - padB - barH}
            width={Math.max(bw - 1, 1)}
            height={Math.max(barH, b.count > 0 ? 1 : 0)}
            fill={PALETTE.bar}
          />
        );
      })}
      {!compact && (
        <>
          <text x={padL} y={h - 8} fontSize="10" fill={PALETTE.axis}>
            {fmtTick(bins[0].lower)}
          </text>
          <text x={w - 8} y={h - 8} textAnchor="end" fontSize="10" fill={PALETTE.axis}>
            {fmtTick(bins[bins.length - 1].upper)}
          </text>
        </>
      )}
    </svg>
  );
}

/** Horizontal bars — missingness, relationship strength. Signed when values can be negative. */
function HBarChart({ spec, compact }: { spec: ChartSpec; compact?: boolean }) {
  const all = spec.series ?? [];
  if (!all.length) return <NoChart />;
  const series = compact ? all.slice(0, 6) : all;
  const rowH = compact ? 12 : 22;
  const w = 320;
  const h = series.length * rowH + (compact ? 6 : 16);
  const padL = compact ? 4 : 128;
  const track = w - padL - (compact ? 6 : 46);
  const max = Math.max(...series.map((s) => Math.abs(s.value ?? 0)), spec.signed ? 1 : 0.0001);
  const zero = spec.signed ? padL + track / 2 : padL;
  const scale = spec.signed ? track / 2 / max : track / max;

  const label = (v: number) =>
    spec.unit === "percent" ? `${(v * 100).toFixed(1)}%` : v.toFixed(3);

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-full w-full" preserveAspectRatio="none">
      {spec.signed && !compact && (
        <line x1={zero} y1={0} x2={zero} y2={h} stroke={PALETTE.grid} strokeWidth="1" />
      )}
      {series.map((s, i) => {
        const v = s.value ?? 0;
        const len = Math.abs(v) * scale;
        const y = i * rowH + (compact ? 3 : 8);
        const negative = v < 0;
        const strong = Math.abs(v) >= (spec.unit === "percent" ? 0.1 : 0.9);
        return (
          <g key={s.label}>
            {!compact && (
              <text x={padL - 6} y={y + rowH * 0.55} textAnchor="end" fontSize="10" fill={PALETTE.axis}>
                {s.label.length > 20 ? `${s.label.slice(0, 19)}…` : s.label}
              </text>
            )}
            <rect
              x={negative ? zero - len : zero}
              y={y}
              width={Math.max(len, 1)}
              height={rowH * (compact ? 0.6 : 0.55)}
              rx={2}
              fill={strong ? PALETTE.warn : PALETTE.bar}
            />
            {!compact && (
              <text x={w - 6} y={y + rowH * 0.55} textAnchor="end" fontSize="10" fill={PALETTE.axis}>
                {label(v)}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

/** Correlation matrix. Diverging scale: sign matters as much as magnitude. */
function HeatmapChart({ spec, compact }: { spec: ChartSpec; compact?: boolean }) {
  const columns = spec.columns ?? [];
  const values = spec.values ?? [];
  if (!columns.length) return <NoChart />;
  const n = columns.length;
  const cell = compact ? Math.max(96 / n, 3) : Math.max(Math.min(280 / n, 30), 8);
  const padL = compact ? 0 : 120;
  const padT = compact ? 0 : 8;
  const size = n * cell;

  const colour = (v: number | null) => {
    if (v === null) return "var(--chart-null, #f1f5f9)";
    const a = Math.min(Math.abs(v), 1);
    return v >= 0
      ? `rgba(59,130,246,${(0.12 + a * 0.88).toFixed(3)})`
      : `rgba(239,68,68,${(0.12 + a * 0.88).toFixed(3)})`;
  };

  return (
    <svg
      viewBox={`0 0 ${padL + size + (compact ? 0 : 8)} ${padT + size + (compact ? 0 : 8)}`}
      className="h-full w-full"
    >
      {values.map((row, i) =>
        row.map((v, j) => (
          <rect
            key={`${i}-${j}`}
            x={padL + j * cell}
            y={padT + i * cell}
            width={cell - (compact ? 0.5 : 1)}
            height={cell - (compact ? 0.5 : 1)}
            fill={colour(v)}
          >
            {!compact && <title>{`${columns[i]} × ${columns[j]}: ${v?.toFixed(3) ?? "n/a"}`}</title>}
          </rect>
        )),
      )}
      {!compact &&
        columns.map((c, i) => (
          <text
            key={c}
            x={padL - 6}
            y={padT + i * cell + cell * 0.7}
            textAnchor="end"
            fontSize={Math.min(cell * 0.7, 10)}
            fill={PALETTE.axis}
          >
            {c.length > 18 ? `${c.slice(0, 17)}…` : c}
          </text>
        ))}
    </svg>
  );
}

/** Box-and-fence per column, each on its own scale — these are different units. */
function BoxChart({ spec, compact }: { spec: ChartSpec; compact?: boolean }) {
  const series = (spec.series ?? []).filter((s) => s.p25 != null && s.p75 != null);
  if (!series.length) return <NoChart />;
  const rowH = compact ? 14 : 34;
  const w = 320;
  const h = series.length * rowH + 8;
  const padL = compact ? 4 : 128;
  const track = w - padL - (compact ? 6 : 16);

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-full w-full" preserveAspectRatio="none">
      {series.map((s, i) => {
        const lo = s.lower ?? s.p25!;
        const hi = s.upper ?? s.p75!;
        const span = hi - lo || 1;
        const x = (v: number) => padL + ((v - lo) / span) * track;
        const y = i * rowH + rowH / 2;
        const boxH = compact ? 6 : 14;
        const hot = (s.outlier_rate ?? 0) >= 0.05;
        return (
          <g key={s.label}>
            {!compact && (
              <text x={padL - 6} y={y + 4} textAnchor="end" fontSize="10" fill={PALETTE.axis}>
                {s.label.length > 20 ? `${s.label.slice(0, 19)}…` : s.label}
              </text>
            )}
            <line x1={x(lo)} y1={y} x2={x(hi)} y2={y} stroke={PALETTE.axis} strokeWidth="1" />
            <line x1={x(lo)} y1={y - boxH / 2} x2={x(lo)} y2={y + boxH / 2} stroke={PALETTE.axis} />
            <line x1={x(hi)} y1={y - boxH / 2} x2={x(hi)} y2={y + boxH / 2} stroke={PALETTE.axis} />
            <rect
              x={x(s.p25!)}
              y={y - boxH / 2}
              width={Math.max(x(s.p75!) - x(s.p25!), 1)}
              height={boxH}
              rx={2}
              fill={hot ? PALETTE.warn : PALETTE.bar}
              fillOpacity={0.35}
              stroke={hot ? PALETTE.warn : PALETTE.bar}
            />
            {s.p50 != null && (
              <line
                x1={x(s.p50)}
                y1={y - boxH / 2}
                x2={x(s.p50)}
                y2={y + boxH / 2}
                stroke={hot ? PALETTE.warn : PALETTE.bar}
                strokeWidth="2"
              />
            )}
          </g>
        );
      })}
    </svg>
  );
}

/** Class shares. */
function DonutChart({ spec, compact }: { spec: ChartSpec; compact?: boolean }) {
  const series = spec.series ?? [];
  const total = series.reduce((sum, s) => sum + (s.value ?? 0), 0);
  const gradientId = useId();
  if (!total) return <NoChart />;

  const size = 120;
  const r = 42;
  const stroke = 20;
  const c = 2 * Math.PI * r;
  const colours = [PALETTE.bar, PALETTE.alt, PALETTE.warn, PALETTE.stop, "#8b5cf6", "#0ea5e9"];
  let offset = 0;

  return (
    <svg viewBox={`0 0 ${size} ${size}`} className="h-full w-full" key={gradientId}>
      <g transform={`translate(${size / 2},${size / 2}) rotate(-90)`}>
        {series.map((s, i) => {
          const frac = (s.value ?? 0) / total;
          const dash = frac * c;
          const el = (
            <circle
              key={s.label}
              r={r}
              fill="none"
              stroke={colours[i % colours.length]}
              strokeWidth={stroke}
              strokeDasharray={`${dash} ${c - dash}`}
              strokeDashoffset={-offset}
            >
              <title>{`${s.label}: ${(frac * 100).toFixed(1)}%`}</title>
            </circle>
          );
          offset += dash;
          return el;
        })}
      </g>
      {!compact && (
        <text x={size / 2} y={size / 2 + 4} textAnchor="middle" fontSize="13" fontWeight="600" fill="currentColor">
          {series.length}
        </text>
      )}
    </svg>
  );
}

function Gridlines({
  w, h, padL, padB, padT, max,
}: { w: number; h: number; padL: number; padB: number; padT: number; max: number }) {
  const lines = [0, 0.25, 0.5, 0.75, 1];
  return (
    <g>
      {lines.map((f) => {
        const y = h - padB - f * (h - padT - padB);
        return (
          <g key={f}>
            <line x1={padL} y1={y} x2={w - 8} y2={y} stroke={PALETTE.grid} strokeWidth="1" />
            <text x={padL - 6} y={y + 3} textAnchor="end" fontSize="9" fill={PALETTE.axis}>
              {fmtTick(max * f)}
            </text>
          </g>
        );
      })}
    </g>
  );
}
