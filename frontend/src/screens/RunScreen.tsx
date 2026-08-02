/** Run section: AEI as the single progress metric, with supporting volume. */

import { useEffect, useState } from "react";
import { api, type Range, type RunSection, type Vitals } from "../api";
import { Card, Columns, fmt, LineChart, PillBars, Spark, TableTwin, type Point } from "../charts/primitives";
import { MetricCard } from "../components/shell";
import { VitalsCard } from "../components/VitalsCard";

export function RunScreen({ range, reloadKey }: { range: Range; reloadKey: number }) {
  const [data, setData] = useState<RunSection | null>(null);
  const [vitals, setVitals] = useState<Vitals | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showAeiTable, setShowAeiTable] = useState(false);

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
          <LineChart points={aeiPoints} unit="m/beat" yLabel="Aerobic Efficiency Index" />
          {data.aei.excluded.length > 0 && (
            <div style={{ marginTop: 12, fontSize: 12, color: "var(--text-muted)" }}>
              {data.aei.excluded.length} run{data.aei.excluded.length === 1 ? "" : "s"} excluded:{" "}
              {data.aei.excluded.map((row) => `${row.date} (${row.reason})`).join("; ")}
            </div>
          )}
        </Card>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: "var(--gap)" }}>
          <MetricCard
            label="Runs"
            value={String(data.runs.count)}
            unit={data.bucket === "day" ? "this period" : ""}
            caption={`per ${data.bucket}`}
            visual={<PillBars points={data.runs.per_bucket.map((b) => ({ label: b.bucket, value: b.total }))} />}
          />
          <MetricCard
            label="Distance"
            value={fmt(data.runs.total_km, 1)}
            unit="km"
            caption={`per ${data.bucket}`}
            visual={<PillBars points={data.runs.km_per_bucket.map((b) => ({ label: b.bucket, value: b.total }))} />}
          />
        </div>

        <Card title="Average heart rate per run" caption={data.window}>
          <Columns
            points={data.runs.heart_rate_per_run.map((run) => ({
              label: new Date(`${run.date}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" }),
              value: run.avg_heart_rate,
              meta: [`${fmt(run.distance_km, 2)} km`],
            }))}
            unit="bpm"
          />
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
