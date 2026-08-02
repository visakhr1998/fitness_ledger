/** Gym section: muscle distribution, tonnage, and per-exercise progression. */

import { useEffect, useMemo, useState } from "react";
import {
  api, type ExerciseDetail, type ExerciseSummary, type GymSection, type Range, type Vitals,
} from "../api";
import {
  Card, Columns, fmt, LineChart, Radar, TableTwin, TargetBars, type Point,
} from "../charts/primitives";
import { MetricCard } from "../components/shell";
import { ExerciseIcon } from "../components/ExerciseIcon";
import { VitalsCard } from "../components/VitalsCard";
import { ErrorNote, Loading } from "./RunScreen";

export function GymScreen({ range, reloadKey }: { range: Range; reloadKey: number }) {
  const [data, setData] = useState<GymSection | null>(null);
  const [vitals, setVitals] = useState<Vitals | null>(null);
  const [catalog, setCatalog] = useState<ExerciseSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [radarTable, setRadarTable] = useState(false);
  const [volumeTable, setVolumeTable] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    Promise.all([api.gym(range), api.vitals(), api.exercises(true)])
      .then(([gym, v, list]) => {
        if (cancelled) return;
        setData(gym);
        setVitals(v);
        setCatalog(list);
      })
      .catch((err) => !cancelled && setError(err.message));
    return () => { cancelled = true; };
  }, [range.window, range.start, range.end, reloadKey]);

  if (error) return <ErrorNote message={error} />;
  if (!data) return <Loading />;

  const tonnagePoints: Point[] = data.tonnage.buckets.map((bucket) => ({
    label: new Date(`${bucket.bucket}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" }),
    value: bucket.total,
    meta: [bucket.above_average ? "above average" : "below average"],
  }));

  const sortedRadar = [...data.radar].sort((a, b) => b.effective_sets - a.effective_sets);

  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 300px", gap: "var(--gap)", alignItems: "start" }}
         className="gym-grid">
      <div style={{ display: "grid", gap: "var(--gap)", minWidth: 0 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(250px, 1fr))", gap: "var(--gap)" }}>
          <MetricCard
            label="Tonnage"
            value={data.tonnage.total >= 1000 ? fmt(data.tonnage.total / 1000, 1) : fmt(data.tonnage.total, 0)}
            unit={data.tonnage.total >= 1000 ? "t" : "kg"}
            caption={`${data.workouts} workouts · ${data.working_sets} working sets`}
          />
          <MetricCard
            label={`Average per ${data.bucket}`}
            value={data.tonnage.mean >= 1000 ? fmt(data.tonnage.mean / 1000, 1) : fmt(data.tonnage.mean, 0)}
            unit={data.tonnage.mean >= 1000 ? "t" : "kg"}
            caption="reference line on the chart"
            tone="muted"
          />
        </div>

        <Card
          title="Muscle group distribution"
          caption={`Effective sets, primary plus half of secondary · ${data.window}`}
          action={
            <TableTwin
              open={radarTable}
              onToggle={() => setRadarTable((value) => !value)}
              headers={["Muscle group", "Effective sets", "Target"]}
              rows={sortedRadar.map((row) => [
                row.muscle_group.replace(/_/g, " "), fmt(row.effective_sets, 1), fmt(row.target_sets, 0),
              ])}
            />
          }
        >
          {/* Radar is weak for precise comparison, so the sorted bars sit beside
              it and carry the actual reading. */}
          <div style={{ display: "grid", gridTemplateColumns: "300px minmax(0, 1fr)", gap: 16, alignItems: "center" }}
               className="radar-row">
            <Radar rows={data.radar} />
            <TargetBars
              rows={sortedRadar.map((row) => ({
                label: row.muscle_group,
                value: row.effective_sets,
                target: row.target_sets,
              }))}
            />
          </div>
        </Card>

        <Card
          title="Tonnage over time"
          caption={`Volume load per ${data.bucket}, against the period average`}
          action={
            <TableTwin
              open={volumeTable}
              onToggle={() => setVolumeTable((value) => !value)}
              headers={["Period", "Tonnage kg", "vs average"]}
              rows={data.tonnage.buckets.map((bucket) => [
                bucket.bucket, fmt(bucket.total, 0), bucket.above_average ? "above" : "below",
              ])}
            />
          }
        >
          <Columns points={tonnagePoints} mean={data.tonnage.mean} unit="kg" />
        </Card>

        <ExerciseExplorer catalog={catalog} range={range} />
      </div>

      <div style={{ display: "grid", gap: "var(--gap)", position: "sticky", top: 12 }}>
        {vitals && <VitalsCard vitals={vitals} compact />}
      </div>
    </div>
  );
}

// --- exercise explorer -----------------------------------------------------

function ExerciseExplorer({ catalog, range }: { catalog: ExerciseSummary[]; range: Range }) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<ExerciseDetail | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!selected && catalog.length) setSelected(catalog[0].id);
  }, [catalog, selected]);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setLoading(true);
    api.exercise(selected, range)
      .then((result) => !cancelled && setDetail(result))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [selected, range.window, range.start, range.end]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const list = needle
      ? catalog.filter((row) => row.title.toLowerCase().includes(needle))
      : catalog;
    return list.slice(0, 60);
  }, [catalog, query]);

  return (
    <Card title="Exercise tracker" caption="Pick a lift to see its progression">
      <div style={{ display: "grid", gridTemplateColumns: "240px minmax(0, 1fr)", gap: 16 }} className="explorer">
        <div>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search exercises"
            aria-label="Search exercises"
            style={{
              width: "100%", background: "var(--surface-raised)", color: "var(--text-primary)",
              border: "1px solid var(--border)", borderRadius: 10, padding: "8px 10px", fontSize: 13,
            }}
          />
          <div style={{ maxHeight: 380, overflow: "auto", marginTop: 10 }}>
            {filtered.map((row) => {
              const on = row.id === selected;
              return (
                <button
                  key={row.id}
                  onClick={() => setSelected(row.id)}
                  aria-pressed={on}
                  style={{
                    display: "flex", alignItems: "center", gap: 10, width: "100%",
                    padding: "8px 10px", borderRadius: 12, textAlign: "left",
                    background: on ? "var(--accent-soft)" : "transparent",
                    color: on ? "var(--text-primary)" : "var(--text-secondary)",
                  }}
                >
                  <ExerciseIcon icon={row.icon} size={24} color={on ? "var(--accent)" : "var(--text-muted)"} />
                  <span style={{ flex: "1 1 auto", fontSize: 13, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {row.title}
                  </span>
                  <span className="tabular" style={{ fontSize: 11, color: "var(--text-muted)" }}>
                    {row.logged_sets}
                  </span>
                </button>
              );
            })}
            {filtered.length === 0 && (
              <div style={{ color: "var(--text-muted)", fontSize: 13, padding: 10 }}>No match.</div>
            )}
          </div>
        </div>

        <div style={{ minWidth: 0 }}>
          {loading && !detail && <Loading />}
          {detail && <ExerciseDetailPanel detail={detail} />}
        </div>
      </div>
    </Card>
  );
}

function ExerciseDetailPanel({ detail }: { detail: ExerciseDetail }) {
  const points: Point[] = detail.one_rep_max.points.map((point) => ({
    label: point.date,
    value: point.estimated_1rm_kg,
    meta: [`${fmt(point.weight_kg, 1)} kg × ${point.reps}`],
  }));

  const state = detail.progression;
  const status = state.ready_to_progress
    ? { text: "Ready to progress", tone: "var(--good)", glyph: "▲" }
    : state.stalled
      ? { text: "Stalled", tone: "var(--serious)", glyph: "▲" }
      : { text: "Holding", tone: "var(--text-muted)", glyph: "•" };

  return (
    <div className="detail-stack" style={{ display: "grid", gap: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <ExerciseIcon icon={detail.exercise.icon} size={34} />
        <div style={{ flex: "1 1 auto", minWidth: 0 }}>
          <div style={{ fontSize: 16, fontWeight: 500 }}>{detail.exercise.title}</div>
          <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
            {detail.exercise.primary_muscle_group.replace(/_/g, " ")}
            {detail.exercise.equipment ? ` · ${detail.exercise.equipment}` : ""}
            {` · ${detail.sessions} sessions`}
          </div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: 10 }}>
        <Stat label="Est. 1RM" value={detail.one_rep_max.latest ? `${fmt(detail.one_rep_max.latest, 1)} kg` : "–"} />
        <Stat
          label="Change"
          value={detail.one_rep_max.change_kg === null ? "–" : `${detail.one_rep_max.change_kg >= 0 ? "+" : ""}${fmt(detail.one_rep_max.change_kg, 1)} kg`}
          tone={detail.one_rep_max.change_kg === null ? undefined : detail.one_rep_max.change_kg >= 0 ? "var(--good)" : "var(--serious)"}
        />
        <Stat label="Working weight" value={state.working_weight_kg ? `${fmt(state.working_weight_kg, 1)} kg` : "–"} />
        <Stat label="Total sets" value={String(detail.volume.total_sets)} />
      </div>

      <div
        style={{
          display: "flex", alignItems: "center", gap: 10, padding: "10px 14px",
          background: "var(--surface-raised)", borderRadius: 14,
          borderLeft: `3px solid ${status.tone}`,
        }}
      >
        <span aria-hidden style={{ color: status.tone }}>{status.glyph}</span>
        <div style={{ fontSize: 13 }}>
          {/* Icon plus word: status never rides on colour alone. */}
          <strong style={{ color: status.tone }}>{status.text}</strong>{" "}
          <span style={{ color: "var(--text-secondary)" }}>
            — {state.reps.length ? `${state.reps.join(", ")} reps at ${fmt(state.working_weight_kg, 1)} kg, range ${state.rep_range}` : "no working sets recorded"}
            {state.ready_to_progress && state.suggested_weight_kg ? `. Try ${fmt(state.suggested_weight_kg, 1)} kg.` : ""}
          </span>
        </div>
      </div>

      <div>
        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 4 }}>
          Estimated 1RM · {detail.window}
        </div>
        <LineChart points={points} unit="kg" height={200} yLabel="Estimated one-rep max" />
      </div>

      <div>
        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 4 }}>
          Working sets per {detail.bucket}
        </div>
        <Columns
          points={detail.volume.sets_per_bucket.map((bucket) => ({
            label: new Date(`${bucket.bucket}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" }),
            value: bucket.total,
          }))}
          height={150}
          unit="sets"
        />
      </div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div style={{ background: "var(--surface-raised)", borderRadius: 14, padding: "10px 12px" }}>
      <div style={{ fontSize: 11, color: "var(--text-muted)" }}>{label}</div>
      <div style={{ fontSize: 19, marginTop: 2, color: tone ?? "var(--text-primary)" }}>{value}</div>
    </div>
  );
}
