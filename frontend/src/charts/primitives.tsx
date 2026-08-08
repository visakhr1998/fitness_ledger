/** Chart primitives.
 *
 * Hand-built SVG rather than a chart library: the mark specs and the palette
 * were validated up front, and bending a library to them costs more than
 * drawing them. Every chart here is single-series by design.
 */

import { useEffect, useRef, useState, type ReactNode } from "react";

export const fmt = (value: number | null | undefined, digits = 1): string =>
  value === null || value === undefined || Number.isNaN(value)
    ? "–"
    : value.toFixed(digits);

export const shortDate = (iso: string): string =>
  new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });

/** Nice round axis ticks covering [0, max]. */
export function ticks(max: number, count = 4): number[] {
  if (max <= 0) return [0];
  const raw = max / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? magnitude * 10;
  const out: number[] = [];
  for (let value = 0; value <= max + step * 0.001; value += step) {
    out.push(Math.round(value * 100) / 100);
  }
  return out;
}

/** Measure a container so charts render at real pixel width, not a scaled viewBox. */
export function useWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null!);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(node);
    setWidth(node.clientWidth);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

/** A bar rounded at the data end only, so the zero end still reads as zero. */
export function barPath(
  x: number, y: number, w: number, h: number, r: number, dir: "up" | "right",
): string {
  const radius = Math.max(0, Math.min(r, dir === "right" ? Math.min(w, h / 2) : Math.min(h, w / 2)));
  if (dir === "right") {
    return `M${x},${y} H${x + w - radius} A${radius},${radius} 0 0 1 ${x + w},${y + radius} V${y + h - radius} A${radius},${radius} 0 0 1 ${x + w - radius},${y + h} H${x} Z`;
  }
  return `M${x},${y + h} V${y + radius} A${radius},${radius} 0 0 1 ${x + radius},${y} H${x + w - radius} A${radius},${radius} 0 0 1 ${x + w},${y + radius} V${y + h} Z`;
}

/** Fully rounded pill bar, as the reference uses in its compact day strips. */
export function pillPath(x: number, y: number, w: number, h: number): string {
  const r = Math.min(w / 2, h / 2);
  return `M${x + r},${y} H${x + w - r} A${r},${r} 0 0 1 ${x + w},${y + r} V${y + h - r} A${r},${r} 0 0 1 ${x + w - r},${y + h} H${x + r} A${r},${r} 0 0 1 ${x},${y + h - r} V${y + r} A${r},${r} 0 0 1 ${x + r},${y} Z`;
}

// --- tooltip ---------------------------------------------------------------

type TipState = { x: number; y: number; title: string; rows: string[] } | null;

export function useTooltip() {
  const [tip, setTip] = useState<TipState>(null);
  const show = (event: React.MouseEvent, title: string, rows: string[]) =>
    setTip({ x: event.clientX, y: event.clientY, title, rows });
  const hide = () => setTip(null);
  return { tip, show, hide };
}

export function Tooltip({ tip }: { tip: TipState }) {
  if (!tip) return null;
  const style: React.CSSProperties = {
    position: "fixed",
    left: Math.min(tip.x + 14, window.innerWidth - 240),
    top: Math.min(tip.y + 14, window.innerHeight - 120),
    background: "var(--surface-raised)",
    border: "1px solid var(--border)",
    borderRadius: 10,
    padding: "8px 10px",
    fontSize: 12,
    pointerEvents: "none",
    zIndex: 50,
    boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
    maxWidth: 240,
  };
  return (
    <div style={style} role="status">
      <div style={{ fontWeight: 600, marginBottom: 2 }}>{tip.title}</div>
      {tip.rows.map((row) => (
        <div key={row} className="tabular" style={{ color: "var(--text-secondary)" }}>
          {row}
        </div>
      ))}
    </div>
  );
}

// --- table twin ------------------------------------------------------------

/** Every chart ships one: three light-mode accent steps sit under 3:1, and a
 *  table is the relief that makes that legal -- as well as the keyboard path. */
export function TableTwin({
  headers, rows, open, onToggle,
}: {
  headers: string[];
  rows: (string | number)[][];
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <>
      <button
        onClick={onToggle}
        aria-pressed={open}
        style={{
          fontSize: 12, color: "var(--text-secondary)", padding: "4px 10px",
          border: "1px solid var(--border)", borderRadius: "var(--radius-control)",
        }}
      >
        {open ? "Hide table" : "Table"}
      </button>
      {open && (
        <div style={{ maxHeight: 300, overflow: "auto", marginTop: 12 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr>
                {headers.map((header) => (
                  <th
                    key={header}
                    style={{
                      textAlign: "left", padding: "6px 10px 6px 0", fontSize: 12,
                      color: "var(--text-secondary)", borderBottom: "1px solid var(--grid)",
                    }}
                  >
                    {header}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={index}>
                  {row.map((cell, cellIndex) => (
                    <td
                      key={cellIndex}
                      className={cellIndex ? "tabular" : undefined}
                      style={{
                        padding: "6px 10px 6px 0",
                        borderBottom: "1px solid var(--grid)",
                        textAlign: cellIndex ? "right" : "left",
                      }}
                    >
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

// --- charts ----------------------------------------------------------------

export type Point = { label: string; value: number; meta?: string[] };

/** Compact line+dot spark, as the reference draws inside each metric card. */
export function Spark({ points, width = 150, height = 64 }: { points: Point[]; width?: number; height?: number }) {
  if (points.length === 0) return <svg width={width} height={height} aria-hidden />;
  const values = points.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pad = 8;
  const x = (i: number) => pad + (i * (width - pad * 2)) / Math.max(points.length - 1, 1);
  const y = (v: number) => height - pad - ((v - min) / span) * (height - pad * 2);

  return (
    <svg width={width} height={height} role="img" aria-label="trend">
      <path
        d={points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.value)}`).join(" ")}
        fill="none" stroke="var(--accent)" strokeWidth={2}
        strokeLinecap="round" strokeLinejoin="round"
      />
      {points.map((p, i) => (
        <circle key={p.label} cx={x(i)} cy={y(p.value)} r={3.5} fill="var(--accent)" />
      ))}
    </svg>
  );
}

/** Pill-bar strip with the final bar marked, mirroring the reference.
 *
 *  Carries a tooltip like every other chart here. It was the one primitive
 *  without one, which left the Distance strip showing bars of visibly different
 *  heights and no way to read what any of them meant.
 */
export function PillBars({
  points, width = 150, height = 64, target, unit = "",
}: { points: Point[]; width?: number; height?: number; target?: number; unit?: string }) {
  const { tip, show, hide } = useTooltip();
  if (points.length === 0) return <svg width={width} height={height} aria-hidden />;
  const max = Math.max(...points.map((p) => p.value), target ?? 0) || 1;
  const band = width / points.length;
  const barWidth = Math.max(Math.min(band - 6, 14), 4);
  const usable = height - 10;

  return (
    <>
      <svg width={width} height={height} role="img" aria-label="per-period totals">
        {target !== undefined && target > 0 && (
          <line
            x1={0} x2={width} y1={height - 4 - (target / max) * usable}
            y2={height - 4 - (target / max) * usable}
            stroke="var(--accent-alt)" strokeWidth={1.5}
          />
        )}
        {points.map((p, i) => {
          const h = Math.max((p.value / max) * usable, p.value > 0 ? 4 : 0);
          const x = i * band + (band - barWidth) / 2;
          return (
            <g key={`${p.label}-${i}`}>
              {h > 0 && (
                <path
                  d={pillPath(x, height - 4 - h, barWidth, h)}
                  fill={i === points.length - 1 ? "var(--accent-alt)" : "var(--accent)"}
                  opacity={i === points.length - 1 ? 1 : 0.75}
                />
              )}
              {/* Full-height hit area: the bars are ~14 px wide and a 4 px one
                  is impossible to hover. */}
              <rect
                x={i * band} y={0} width={band} height={height} fill="transparent"
                onMouseMove={(event) =>
                  show(event, p.label, [`${fmt(p.value, 2)} ${unit}`.trim(), ...(p.meta ?? [])])
                }
                onMouseLeave={hide}
              />
            </g>
          );
        })}
      </svg>
      <Tooltip tip={tip} />
    </>
  );
}

/** Where each point sits along the x axis, as a fraction from 0 to 1.
 *
 *  With `dateScale`, spacing comes from the dates themselves, so a twelve-day
 *  gap draws six times wider than a two-day one. Without it, points are evenly
 *  spaced by index, which is right for buckets (every week is a week wide) and
 *  wrong for runs.
 *
 *  Falls back to index spacing whenever dates cannot carry the scale: a label
 *  that will not parse, a single point, or every point on the same day. Each of
 *  those would otherwise divide by a zero span.
 */
export function xFractions(labels: string[], dateScale = false): number[] {
  const evenly = labels.map((_, i) => i / Math.max(labels.length - 1, 1));
  if (!dateScale || labels.length < 2) return evenly;

  const times = labels.map((label) => new Date(`${label}T00:00:00`).getTime());
  if (times.some((t) => Number.isNaN(t))) return evenly;

  const first = Math.min(...times);
  const span = Math.max(...times) - first;
  if (span <= 0) return evenly;
  return times.map((t) => (t - first) / span);
}

/** Full-width line chart with axes, gridlines and a per-point tooltip.
 *
 *  Set `dateScale` when the labels are ISO dates and the gaps between them are
 *  uneven, which is every running chart here. Without it points are spaced by
 *  array index, so a twelve-day gap and a two-day gap render the same width --
 *  the one thing a time series is supposed to show.
 */
export function LineChart({
  points, height = 240, unit = "", yLabel, dateScale = false,
}: { points: Point[]; height?: number; unit?: string; yLabel?: string; dateScale?: boolean }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const { tip, show, hide } = useTooltip();

  const margin = { top: 16, right: 56, bottom: 30, left: 46 };
  const inner = Math.max(width - margin.left - margin.right, 40);
  const innerHeight = height - margin.top - margin.bottom;

  if (points.length === 0) {
    return (
      <div ref={ref} style={{ width: "100%", color: "var(--text-muted)", padding: "24px 0", fontSize: 13 }}>
        No data in this range.
      </div>
    );
  }

  const values = points.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const lo = min - (max - min || min * 0.1 || 1) * 0.15;
  const hi = max + (max - min || min * 0.1 || 1) * 0.15;

  const fractions = xFractions(points.map((p) => p.label), dateScale);
  const at = (i: number) => fractions[i];
  const x = (i: number) => margin.left + at(i) * inner;
  const y = (v: number) => margin.top + innerHeight - ((v - lo) / (hi - lo || 1)) * innerHeight;

  const gridValues = [lo, (lo + hi) / 2, hi];

  return (
    <div ref={ref} style={{ width: "100%" }}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={yLabel ?? "trend"}>
          {gridValues.map((value) => (
            <g key={value}>
              <line
                x1={margin.left} x2={margin.left + inner} y1={y(value)} y2={y(value)}
                stroke="var(--grid)" strokeWidth={1}
              />
              <text
                x={margin.left - 8} y={y(value) + 4} textAnchor="end"
                fill="var(--text-muted)" fontSize={11} className="tabular"
              >
                {value.toFixed(value < 10 ? 2 : 0)}
              </text>
            </g>
          ))}

          <path
            d={points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.value)}`).join(" ")}
            fill="none" stroke="var(--accent)" strokeWidth={2}
            strokeLinecap="round" strokeLinejoin="round"
          />

          {points.map((p, i) => (
            <circle
              // Two runs can share a date, so the label is not a key.
              key={`${p.label}-${i}`} cx={x(i)} cy={y(p.value)} r={4}
              fill="var(--accent)" stroke="var(--surface)" strokeWidth={2}
              onMouseMove={(event) => show(event, p.label, [`${fmt(p.value, 3)} ${unit}`, ...(p.meta ?? [])])}
              onMouseLeave={hide}
              style={{ cursor: "crosshair" }}
            />
          ))}

          {/* Selective direct label: the endpoint only. */}
          <text
            x={x(points.length - 1) + 10} y={y(points[points.length - 1].value) + 4}
            fill="var(--text-secondary)" fontSize={11} className="tabular"
          >
            {fmt(points[points.length - 1].value, 2)}
          </text>

          {/* First, last, and whichever point sits nearest the middle of the
              axis -- by time when the scale is time, so the middle label lands
              where the eye expects rather than at the median run. */}
          {[0, midpointIndex(at, points.length), points.length - 1]
            .filter((i, index, all) => all.indexOf(i) === index)
            .map((i) => (
              <text
                key={i}
                x={Math.min(Math.max(x(i), margin.left + 18), margin.left + inner - 18)}
                y={height - 10} textAnchor="middle"
                fill="var(--text-muted)" fontSize={11}
              >
                {points[i].label}
              </text>
            ))}
        </svg>
      )}
      <Tooltip tip={tip} />
    </div>
  );
}

/** Index of the point nearest the halfway mark of the x axis. */
function midpointIndex(at: (i: number) => number, count: number): number {
  let best = 0;
  let bestGap = Infinity;
  for (let i = 0; i < count; i += 1) {
    const gap = Math.abs(at(i) - 0.5);
    if (gap < bestGap) {
      bestGap = gap;
      best = i;
    }
  }
  return best;
}

/** Column chart with an optional mean reference line. */
export function Columns({
  points, height = 200, mean, unit = "",
}: { points: Point[]; height?: number; mean?: number; unit?: string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const { tip, show, hide } = useTooltip();

  const margin = { top: 14, right: 12, bottom: 28, left: 52 };
  const inner = Math.max(width - margin.left - margin.right, 40);
  const innerHeight = height - margin.top - margin.bottom;

  if (points.length === 0) {
    return <div style={{ color: "var(--text-muted)", padding: "24px 0", fontSize: 13 }}>No data in this range.</div>;
  }

  const max = Math.max(...points.map((p) => p.value), mean ?? 0) * 1.1 || 1;
  const band = inner / points.length;
  const barWidth = Math.max(band - 8, 4);
  const y = (v: number) => margin.top + innerHeight - (v / max) * innerHeight;

  return (
    <div ref={ref} style={{ width: "100%" }}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label="per-period totals">
          {ticks(max, 3).map((value) => (
            <g key={value}>
              <line x1={margin.left} x2={margin.left + inner} y1={y(value)} y2={y(value)} stroke="var(--grid)" />
              <text x={margin.left - 8} y={y(value) + 4} textAnchor="end" fill="var(--text-muted)" fontSize={11} className="tabular">
                {value >= 1000 ? `${Math.round(value / 1000)}k` : value}
              </text>
            </g>
          ))}

          {points.map((p, i) => {
            const h = margin.top + innerHeight - y(p.value);
            const x = margin.left + i * band + (band - barWidth) / 2;
            return (
              <g key={p.label}>
                {h > 0 && (
                  <path
                    d={barPath(x, y(p.value), barWidth, h, 4, "up")}
                    fill={mean !== undefined && p.value < mean ? "var(--accent)" : "var(--accent)"}
                    opacity={mean !== undefined && p.value < mean ? 0.55 : 1}
                  />
                )}
                <rect
                  x={x} y={margin.top} width={barWidth} height={innerHeight} fill="transparent"
                  onMouseMove={(event) => show(event, p.label, [`${fmt(p.value, 0)} ${unit}`, ...(p.meta ?? [])])}
                  onMouseLeave={hide}
                />
              </g>
            );
          })}

          {mean !== undefined && mean > 0 && (
            <>
              <line
                x1={margin.left} x2={margin.left + inner} y1={y(mean)} y2={y(mean)}
                stroke="var(--accent-alt)" strokeWidth={1.5}
              />
              <text x={margin.left + inner} y={y(mean) - 6} textAnchor="end" fill="var(--accent-alt)" fontSize={11}>
                average
              </text>
            </>
          )}

          {points.map((p, i) =>
            points.length <= 14 || i % 2 === 0 ? (
              <text
                key={`label-${p.label}`}
                x={margin.left + i * band + band / 2} y={height - 9}
                textAnchor="middle" fill="var(--text-muted)" fontSize={11}
              >
                {p.label}
              </text>
            ) : null,
          )}
        </svg>
      )}
      <Tooltip tip={tip} />
    </div>
  );
}

/** Horizontal bars against a per-row target notch. */
export function TargetBars({
  rows,
}: { rows: { label: string; value: number; target: number; caption?: string }[] }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const { tip, show, hide } = useTooltip();

  const band = 26;
  const margin = { top: 6, right: 44, bottom: 24, left: 104 };
  const inner = Math.max(width - margin.left - margin.right, 40);
  const height = margin.top + rows.length * band + margin.bottom;
  const max = Math.max(...rows.map((r) => Math.max(r.value, r.target)), 1) * 1.05;
  const x = (v: number) => (v / max) * inner;

  return (
    <div ref={ref} style={{ width: "100%" }}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label="volume against target">
          {ticks(max, 4).map((value) => (
            <g key={value}>
              <line
                x1={margin.left + x(value)} x2={margin.left + x(value)}
                y1={margin.top} y2={margin.top + rows.length * band}
                stroke="var(--grid)"
              />
              <text
                x={margin.left + x(value)} y={height - 8} textAnchor="middle"
                fill="var(--text-muted)" fontSize={11} className="tabular"
              >
                {value}
              </text>
            </g>
          ))}

          {rows.map((row, i) => {
            const y = margin.top + i * band + 3;
            const barHeight = band - 6;
            const w = row.value > 0 ? Math.max(x(row.value), 3) : 0;
            const untouched = row.value === 0;
            return (
              <g key={row.label}>
                <text
                  x={margin.left - 10} y={y + barHeight / 2 + 4} textAnchor="end"
                  fill={untouched ? "var(--text-muted)" : "var(--text-secondary)"} fontSize={12}
                >
                  {row.label.replace(/_/g, " ")}
                </text>
                {w > 0 && (
                  <path d={barPath(margin.left, y, w, barHeight, 4, "right")} fill="var(--accent)" />
                )}
                {/* Threshold, not a series: chrome ink, drawn on the row itself. */}
                <line
                  x1={margin.left + x(row.target)} x2={margin.left + x(row.target)}
                  y1={y - 2} y2={y + barHeight + 2}
                  stroke="var(--accent-alt)" strokeWidth={2}
                />
                <text
                  x={margin.left + w + 8} y={y + barHeight / 2 + 4}
                  fill="var(--text-muted)" fontSize={11} className="tabular"
                >
                  {fmt(row.value, 1)}
                </text>
                <rect
                  x={margin.left} y={y} width={inner} height={barHeight} fill="transparent"
                  onMouseMove={(event) =>
                    show(event, row.label.replace(/_/g, " "), [
                      `${fmt(row.value, 1)} of ${fmt(row.target, 0)} sets`,
                      ...(row.caption ? [row.caption] : []),
                    ])
                  }
                  onMouseLeave={hide}
                />
              </g>
            );
          })}
        </svg>
      )}
      <Tooltip tip={tip} />
    </div>
  );
}

/** Radar of muscle-group distribution. One shape, one colour. */
export function Radar({
  rows, size = 300,
}: { rows: { muscle_group: string; effective_sets: number; share: number }[]; size?: number }) {
  const { tip, show, hide } = useTooltip();
  if (rows.length < 3) return <div style={{ color: "var(--text-muted)" }}>Not enough data.</div>;

  const cx = size / 2;
  const cy = size / 2;
  const radius = size / 2 - 54;
  const angle = (i: number) => (i / rows.length) * Math.PI * 2 - Math.PI / 2;
  const at = (i: number, r: number) => [cx + Math.cos(angle(i)) * r, cy + Math.sin(angle(i)) * r];

  const shape = rows
    .map((row, i) => {
      const [px, py] = at(i, radius * Math.max(row.share, 0.02));
      return `${i ? "L" : "M"}${px},${py}`;
    })
    .join(" ") + " Z";

  return (
    <div style={{ display: "flex", justifyContent: "center" }}>
      <svg width={size} height={size} role="img" aria-label="muscle group distribution">
        {[0.25, 0.5, 0.75, 1].map((ring) => (
          <circle key={ring} cx={cx} cy={cy} r={radius * ring} fill="none" stroke="var(--grid)" />
        ))}
        {rows.map((row, i) => {
          const [px, py] = at(i, radius);
          return <line key={row.muscle_group} x1={cx} y1={cy} x2={px} y2={py} stroke="var(--grid)" />;
        })}

        <path d={shape} fill="var(--accent-soft)" stroke="var(--accent)" strokeWidth={2} strokeLinejoin="round" />

        {rows.map((row, i) => {
          const [px, py] = at(i, radius * Math.max(row.share, 0.02));
          const [lx, ly] = at(i, radius + 22);
          return (
            <g key={`p-${row.muscle_group}`}>
              <circle
                cx={px} cy={py} r={4} fill="var(--accent)" stroke="var(--surface)" strokeWidth={2}
                onMouseMove={(event) =>
                  show(event, row.muscle_group.replace(/_/g, " "), [`${fmt(row.effective_sets, 1)} effective sets`])
                }
                onMouseLeave={hide}
              />
              <text
                x={lx} y={ly} textAnchor={lx < cx - 4 ? "end" : lx > cx + 4 ? "start" : "middle"}
                dominantBaseline="middle" fill="var(--text-muted)" fontSize={10}
              >
                {row.muscle_group.replace(/_/g, " ")}
              </text>
            </g>
          );
        })}
      </svg>
      <Tooltip tip={tip} />
    </div>
  );
}

export function Card({
  title, caption, action, children,
}: { title?: string; caption?: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section
      style={{
        background: "var(--surface)",
        borderRadius: "var(--radius-card)",
        padding: "20px 22px 22px",
        border: "1px solid var(--border)",
      }}
    >
      {(title || action) && (
        <header style={{ display: "flex", alignItems: "flex-start", gap: 12, marginBottom: caption ? 2 : 14 }}>
          <div style={{ flex: "1 1 auto" }}>
            {title && <h2 style={{ margin: 0, fontSize: 16, fontWeight: 500 }}>{title}</h2>}
            {caption && (
              <div style={{ color: "var(--text-muted)", fontSize: 12, marginTop: 2 }}>{caption}</div>
            )}
          </div>
          {action}
        </header>
      )}
      <div style={{ marginTop: caption ? 14 : 0 }}>{children}</div>
    </section>
  );
}
