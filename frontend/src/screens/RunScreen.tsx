/** Run section: AEI as the single progress metric, with supporting volume. */

import { useEffect, useState } from "react";
import { api, type Range, type RunSection, type Vitals } from "../api";
import { Card, fmt, LineChart, PillBars, shortDate, Spark, TableTwin, type Point } from "../charts/primitives";
import { MetricCard } from "../components/shell";
import { VitalsCard } from "../components/VitalsCard";

/** "38:12" from seconds, or null when the source did not record a duration. */
function runDuration(seconds: number | null): string | null {
  if (!seconds) return null;
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(secs)}` : `${minutes}:${pad(secs)}`;
}

export function RunScreen({ range, reloadKey }: { range: Range; reloadKey: number }) {
  const [data, setData] = useState<RunSection | null>(null);
  const [vitals, setVitals] = useState<Vitals | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showAeiTable, setShowAeiTable] = useState(false);
  const [showHrTable, setShowHrTable] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    Promise.all([api.run(range), api.vitals()])
      .then(([run, v]) => {
        if (cancelled) return;
        setData(run);
        setVitals(v);
      })
      .catch((err) => !cancelled && setError(err.message));
    return () => { cancelled = true; };
  }, [range.window, range.start, range.end, reloadKey]);

  if (error) return <ErrorNote message={error} />;
  if (!data) return <Loading />;

  const aeiPoints: Point[] = data.aei.points.map((point) => ({
    label: point.date,
    value: point.aei,
    meta: [
      `${fmt(point.actual_distance_km, 2)} km actual`,
      `${fmt(point.adjusted_distance_km, 2)} km grade-adjusted`,
      point.avg_heart_rate ? `${point.avg_heart_rate} bpm average` : "no heart rate",
    ],
  }));

  // Runs with no recorded heart rate are counted under the chart rather than
  // silently dropped, so a gap in the line has a stated cause.
  const heartRatePoints: Point[] = data.runs.list
    .filter((run) => run.avg_heart_rate !== null)
    .map((run) => ({
      label: run.date,
      value: run.avg_heart_rate as number,
      meta: [
        `${fmt(run.distance_km, 2)} km`,
        runDuration(run.duration_s) ? `${runDuration(run.duration_s)} moving` : "no duration recorded",
      ],
    }));

  const delta = data.aei.delta;
  const deltaText =
    delta === null
      ? "no earlier period to compare"
      : `${delta >= 0 ? "+" : ""}${fmt(delta, 3)} vs previous period`;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 300px", gap: "var(--gap)", alignItems: "start" }}
         className="run-grid">
      <div style={{ display: "grid", gap: "var(--gap)", minWidth: 0 }}>
        <MetricCard
          label="Aerobic Efficiency Index"
          value={data.aei.latest === null ? "–" : fmt(data.aei.latest, 3)}
          unit="m/beat"
          caption={
            <span style={{ color: delta === null ? undefined : delta >= 0 ? "var(--good)" : "var(--serious)" }}>
              {deltaText}
            </span>
          }
          visual={<Spark points={aeiPoints} />}
        />

        <Card
          title="AEI over time"
          caption={`Grade-adjusted metres per heart beat · ${data.window}`}
          action={
            <TableTwin
              open={showAeiTable}
              onToggle={() => setShowAeiTable((value) => !value)}
              headers={["Date", "AEI", "Actual km", "Adjusted km", "Ratio", "Avg HR"]}
              rows={data.aei.points.map((p) => [
                p.date, fmt(p.aei, 3), fmt(p.actual_distance_km, 2),
                fmt(p.adjusted_distance_km, 2), fmt(p.grade_ratio, 3), p.avg_heart_rate ?? "–",
              ])}
            />
          }
        >
          <LineChart points={aeiPoints} unit="m/beat" yLabel="Aerobic Efficiency Index" dateScale />
          {data.aei.excluded.length > 0 && (
            <div style={{ marginTop: 12, fontSize: 12, color: "var(--text-muted)" }}>
              {data.aei.excluded.length} run{data.aei.excluded.length === 1 ? "" : "s"} excluded:{" "}
              {data.aei.excluded.map((row) => `${row.date} (${row.reason})`).join("; ")}
            </div>
          )}
        </Card>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: "var(--gap)" }}>
          {/* Both are period totals. They used to be captioned "per <bucket>",
              which on a window of 31 days or less read "3 this period / per
              day". No sparkline on Runs: one bar per run, all the same height,
              says nothing the number has not already said. */}
          <MetricCard
            label="Runs"
            value={String(data.runs.count)}
            unit="this period"
            caption={data.window}
          />
          <MetricCard
            label="Distance"
            value={fmt(data.runs.total_km, 1)}
            unit="km"
            caption={data.window}
            visual={
              <PillBars
                points={data.runs.list.map((run) => ({
                  label: shortDate(run.date),
                  value: run.distance_km,
                  meta: [runDuration(run.duration_s)].filter(Boolean) as string[],
                }))}
                unit="km"
              />
            }
          />
        </div>

        <Card
          title="Average heart rate per run"
          caption={data.window}
          action={
            <TableTwin
              open={showHrTable}
              onToggle={() => setShowHrTable((value) => !value)}
              headers={["Date", "Avg HR", "Distance km", "Time"]}
              rows={data.runs.list.map((run) => [
                run.date,
                run.avg_heart_rate ?? "–",
                fmt(run.distance_km, 2),
                runDuration(run.duration_s) ?? "–",
              ])}
            />
          }
        >
          {/* A line, not bars: average heart rate is a level, and bars anchored
              at zero flatten the 130-180 range that carries all the signal. */}
          <LineChart points={heartRatePoints} unit="bpm" yLabel="Average heart rate" dateScale />
          {data.runs.list.length > heartRatePoints.length && (
            <div style={{ marginTop: 12, fontSize: 12, color: "var(--text-muted)" }}>
              {data.runs.list.length - heartRatePoints.length} run
              {data.runs.list.length - heartRatePoints.length === 1 ? "" : "s"} without a
              recorded heart rate, not plotted.
            </div>
          )}
        </Card>
      </div>

      <div style={{ display: "grid", gap: "var(--gap)", position: "sticky", top: 12 }}>
        {vitals && <VitalsCard vitals={vitals} />}
      </div>
    </div>
  );
}

export function Loading() {
  return <div style={{ color: "var(--text-muted)", padding: 40, textAlign: "center" }}>Loading…</div>;
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <div
      role="alert"
      style={{
        background: "var(--surface)", border: "1px solid var(--critical)",
        borderRadius: "var(--radius-card)", padding: 20, color: "var(--text-primary)",
      }}
    >
      <strong style={{ color: "var(--critical)" }}>Could not load.</strong>
      <div style={{ color: "var(--text-secondary)", marginTop: 6, fontSize: 13 }}>{message}</div>
    </div>
  );
}
