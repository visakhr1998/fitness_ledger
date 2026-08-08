/** Week section: the plan the coach proposed, as one week.
 *
 * Not split across Run and Gym, and not scoped by the time-horizon filter. A
 * plan spans both disciplines, and filtering it into the two section screens
 * would mean no screen ever showed what the week *traded away* — which is the
 * most useful thing a plan says, and why `trade_offs` is its own field.
 *
 * Read-only. Generating and approving land with day 10.
 */

import { useEffect, useState } from "react";
import { api, type PlanSection } from "../api";
import { Card, fmt } from "../charts/primitives";
import { MetricCard } from "../components/shell";
import { ErrorNote, Loading } from "./RunScreen";

const DAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** "Mon 10" — the weekday matters more than the month in a one-week view. */
function dayLabel(iso: string): string {
  const at = new Date(`${iso}T00:00:00`);
  return `${DAY[(at.getDay() + 6) % 7]} ${at.getDate()}`;
}

/** Add days to a YYYY-MM-DD, staying in that calendar.
 *
 *  Via UTC deliberately. Parsing as local and formatting with toISOString()
 *  shifts every day back one in any zone ahead of UTC, which rendered the week
 *  as Sun–Sat instead of Mon–Sun and silently dropped anything planned on the
 *  seventh day.
 */
function addDays(iso: string, days: number): string {
  const at = new Date(`${iso}T00:00:00Z`);
  at.setUTCDate(at.getUTCDate() + days);
  return at.toISOString().slice(0, 10);
}

export function WeekScreen({ reloadKey }: { reloadKey: number }) {
  const [data, setData] = useState<PlanSection | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    api
      .plan()
      .then((plan) => !cancelled && setData(plan))
      .catch((err) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  if (error) return <ErrorNote message={error} />;
  if (!data) return <Loading />;

  if (!data.available || !data.plan) {
    return (
      <Card title="No plan yet">
        <p style={{ color: "var(--text-secondary)", fontSize: 14, margin: 0 }}>
          {data.reason ?? "Nothing has been planned."} Generate one with{" "}
          <code style={{ color: "var(--text-primary)" }}>ledger plan</code>.
        </p>
        <p style={{ color: "var(--text-muted)", fontSize: 13, marginBottom: 0 }}>
          Planning from the dashboard arrives with the approval flow.
        </p>
      </Card>
    );
  }

  const { plan, adherence, problems } = data;
  // Fill the gaps: a week with three sessions has four rest days, and they are
  // part of the shape. Showing only the trained days hides how the week sits.
  const days = Array.from({ length: 7 }, (_, offset) => {
    const iso = addDays(plan.week_start, offset);
    return { iso, sessions: plan.sessions.filter((s) => s.date === iso) };
  });

  return (
    <div style={{ display: "grid", gap: "var(--gap)" }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
          gap: "var(--gap)",
        }}
      >
        <MetricCard
          label="Week of"
          value={plan.week_start}
          unit={plan.status}
          caption={plan.generated_at ? `planned ${plan.generated_at.slice(0, 10)}` : ""}
        />
        <MetricCard
          label="Planned volume"
          value={String(plan.total_sets)}
          unit="sets"
          caption={`${plan.sessions.filter((s) => s.kind === "lift").length} lifting · ${
            plan.sessions.filter((s) => s.kind === "run").length
          } running`}
        />
        <MetricCard
          label="Trained"
          value={
            adherence?.not_started
              ? "–"
              : `${adherence?.sessions_completed ?? 0}/${adherence?.sessions_planned ?? 0}`
          }
          unit={adherence?.not_started ? "not started" : "sessions"}
          caption={
            adherence?.not_started
              ? `${adherence.sessions_ahead} sessions ahead`
              : adherence?.missed_days.length
                ? `nothing logged on ${adherence.missed_days.join(", ")}`
                : "on track"
          }
        />
      </div>

      <Card title="The week" caption={`${plan.total_sets} sets across seven days`}>
        <div style={{ display: "grid", gap: 10 }}>
          {days.map(({ iso, sessions }) => (
            <div
              key={iso}
              style={{
                display: "grid",
                gridTemplateColumns: "72px minmax(0, 1fr)",
                gap: 14,
                alignItems: "start",
                padding: "10px 12px",
                borderRadius: 12,
                background: sessions.length ? "var(--surface-raised)" : "transparent",
                border: `1px solid ${sessions.length ? "transparent" : "var(--grid)"}`,
              }}
            >
              <div
                style={{
                  fontSize: 13,
                  fontWeight: 600,
                  color: sessions.length ? "var(--text-primary)" : "var(--text-muted)",
                }}
              >
                {dayLabel(iso)}
              </div>

              {sessions.length === 0 ? (
                <div style={{ fontSize: 13, color: "var(--text-muted)" }}>rest</div>
              ) : (
                <div style={{ display: "grid", gap: 8, minWidth: 0 }}>
                  {sessions.map((session, index) => (
                    <div key={`${session.kind}-${index}`} style={{ minWidth: 0 }}>
                      <div
                        style={{
                          display: "flex",
                          gap: 10,
                          alignItems: "baseline",
                          fontSize: 13,
                          color: "var(--text-secondary)",
                        }}
                      >
                        <span style={{ fontWeight: 600, color: "var(--accent)" }}>
                          {session.focus || session.kind}
                        </span>
                        <span className="tabular" style={{ color: "var(--text-muted)" }}>
                          {session.kind === "run"
                            ? `${fmt(session.distance_km, 1)} km`
                            : `${session.total_sets} sets`}
                        </span>
                      </div>
                      {session.exercises.length > 0 && (
                        <ul style={{ margin: "6px 0 0", padding: 0, listStyle: "none", display: "grid", gap: 3 }}>
                          {session.exercises.map((exercise) => (
                            <li
                              key={exercise.exercise_template_id}
                              style={{
                                display: "flex",
                                gap: 10,
                                fontSize: 13,
                                color: "var(--text-primary)",
                                minWidth: 0,
                              }}
                            >
                              <span className="tabular" style={{ flex: "0 0 34px", color: "var(--accent-alt)" }}>
                                {exercise.sets} ×
                              </span>
                              <span style={{ flex: "1 1 auto", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                {exercise.title}
                              </span>
                              <span style={{ flex: "0 0 auto", fontSize: 12, color: "var(--text-muted)" }}>
                                {exercise.targets.map((t) => t.replace(/_/g, " ")).join(", ")}
                              </span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </Card>

      <Card title="Why this week">
        <p style={{ margin: 0, fontSize: 14, color: "var(--text-primary)", lineHeight: 1.55 }}>
          {plan.rationale || "No rationale recorded."}
        </p>
        {plan.trade_offs && (
          <>
            {/* Its own block, not a sentence inside the rationale: when the week
                is squeezed something loses, and it should be readable at a glance. */}
            <h3 style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)", margin: "16px 0 6px", textTransform: "uppercase", letterSpacing: 0.4 }}>
              What it gave up
            </h3>
            <p style={{ margin: 0, fontSize: 14, color: "var(--text-secondary)", lineHeight: 1.55 }}>
              {plan.trade_offs}
            </p>
          </>
        )}
      </Card>

      {problems.length > 0 && (
        /* Shown beside the plan rather than instead of it: a week that breaks a
           constraint is still the most useful thing to look at while deciding
           what to do about it. */
        <Card title="Constraints not met" caption="checked against your current preferences">
          <ul style={{ margin: 0, paddingLeft: 20, display: "grid", gap: 6 }}>
            {problems.map((problem) => (
              <li key={problem} style={{ fontSize: 13, color: "var(--serious)" }}>
                {problem}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
