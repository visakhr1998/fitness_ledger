/** Week section: the plan the coach proposed, as one week.
 *
 * Not split across Run and Gym, and not scoped by the time-horizon filter. A
 * plan spans both disciplines, and filtering it into the two section screens
 * would mean no screen ever showed what the week *traded away* — which is the
 * most useful thing a plan says, and why `trade_offs` is its own field.
 *
 * Three things happen here, and the order they appear in is the order they
 * happen: generate a week, decide on it, then write a day to Hevy. Only the
 * last leaves this app, and it goes through the same propose → diff → confirm
 * surface as every other write.
 */

import { useCallback, useEffect, useState } from "react";
import {
  api,
  type AvailabilitySection,
  type PlanSection,
  type PlanStatus,
  type RoutineProposal,
} from "../api";
import { Card, fmt } from "../charts/primitives";
import { MetricCard } from "../components/shell";
import { RoutineDiff, WrittenNote } from "../components/RoutineDiff";
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

const BUTTON: React.CSSProperties = {
  padding: "7px 16px",
  borderRadius: "var(--radius-control)",
  fontSize: 13,
  border: "1px solid var(--border)",
  background: "var(--surface)",
  color: "var(--text-primary)",
};

/** Generate a week in the background, polling like a sync does.
 *
 *  Generation is ~3 model requests and tens of seconds, so it cannot be a plain
 *  request. `unavailable` is rendered differently from `error`: a missing coach
 *  extra is a setup problem, and telling someone their plan failed when they
 *  never installed the planner sends them debugging the wrong thing.
 */
function GenerateControl({ onDone, label }: { onDone: () => void; label: string }) {
  const [status, setStatus] = useState<PlanStatus | null>(null);
  const [polling, setPolling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!polling) return;
    const timer = setInterval(async () => {
      const next = await api.planStatus();
      setStatus(next);
      if (next.status !== "running") {
        setPolling(false);
        if (next.status === "done") onDone();
      }
    }, 1200);
    return () => clearInterval(timer);
  }, [polling, onDone]);

  const running = status?.status === "running" || polling;

  const start = async () => {
    setError(null);
    try {
      const body = await api.startPlan();
      if (body.status === "running") {
        setError(body.detail ?? "a plan is already being generated");
        return;
      }
      setStatus({ status: "running", week: null, plan_id: null, error: null });
      setPolling(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const note =
    error ??
    (status?.status === "unavailable"
      ? `${status.error} — the dashboard works without it; planning does not.`
      : status?.status === "error"
        ? status.error
        : null);

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
      <button onClick={start} disabled={running} style={{ ...BUTTON, color: running ? "var(--text-muted)" : "var(--text-primary)" }}>
        {running ? "Planning…" : label}
      </button>

      {running && (
        <span role="status" style={{ fontSize: 12, color: "var(--text-muted)" }}>
          three model requests, usually under a minute
        </span>
      )}

      {note && (
        <span role="alert" style={{ fontSize: 12, color: "var(--critical)" }}>
          ▲ {note}
        </span>
      )}

      {status?.status === "done" && status.fell_back && (
        /* A week produced by the backstop is never silent — the primary
           refused, and which model answered changes how much to trust it. */
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          planned by {status.planned_by} (fallback)
        </span>
      )}
    </div>
  );
}

export function WeekScreen({ reloadKey }: { reloadKey: number }) {
  const [data, setData] = useState<PlanSection | null>(null);
  const [days, setDays] = useState<AvailabilitySection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);

  const [proposal, setProposal] = useState<(RoutineProposal & { day: string }) | null>(null);
  const [written, setWritten] = useState<{ hevy_id: string | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const reload = useCallback(() => setRefresh((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    api
      .plan()
      .then((plan) => {
        if (cancelled) return;
        setData(plan);
        if (plan.plan) {
          api
            .availability(plan.plan.week_start)
            .then((entries) => !cancelled && setDays(entries))
            .catch(() => undefined);
        }
      })
      .catch((err) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, [reloadKey, refresh]);

  const act = async (work: () => Promise<void>) => {
    setBusy(true);
    setActionError(null);
    try {
      await work();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  if (error) return <ErrorNote message={error} />;
  if (!data) return <Loading />;

  if (!data.available || !data.plan) {
    return (
      <Card title="No plan yet">
        <p style={{ color: "var(--text-secondary)", fontSize: 14, marginTop: 0 }}>
          {data.reason ?? "Nothing has been planned."}
        </p>
        <GenerateControl onDone={reload} label="Plan next week" />
      </Card>
    );
  }

  const { plan, adherence, problems } = data;
  const lost = new Map((days?.unavailable ?? []).map((entry) => [entry.date, entry]));

  // Fill the gaps: a week with three sessions has four rest days, and they are
  // part of the shape. Showing only the trained days hides how the week sits.
  const week = Array.from({ length: 7 }, (_, offset) => {
    const iso = addDays(plan.week_start, offset);
    return { iso, sessions: plan.sessions.filter((s) => s.date === iso) };
  });

  const decided = plan.status !== "proposed";

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

      <Card
        title="This week"
        caption={
          decided
            ? `${plan.status} — regenerating stores a new week, it does not edit this one`
            : "Nothing here has touched Hevy yet"
        }
      >
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          {!decided && (
            <>
              <button
                onClick={() => act(async () => { await api.decidePlan(plan.id!, "approved"); reload(); })}
                disabled={busy}
                style={{ ...BUTTON, background: "var(--accent)", color: "#04121a", fontWeight: 600, border: "none" }}
              >
                Accept this week
              </button>
              <button
                onClick={() => act(async () => { await api.decidePlan(plan.id!, "rejected"); reload(); })}
                disabled={busy}
                style={{ ...BUTTON, color: "var(--text-secondary)" }}
              >
                Reject
              </button>
            </>
          )}
          <GenerateControl onDone={reload} label={decided ? "Plan again" : "Regenerate"} />
        </div>

        {/* Accepting is a decision about the week, not a write. Saying so is
            the difference between an advisor and an autopilot. */}
        <p style={{ fontSize: 12, color: "var(--text-muted)", margin: "10px 0 0" }}>
          Accepting records the week here. Each lifting day is written to Hevy
          separately, with a diff, from the buttons below.
        </p>

        {actionError && (
          <div role="alert" style={{ marginTop: 10, fontSize: 13, color: "var(--critical)" }}>
            ▲ {actionError}
          </div>
        )}
        {written && <WrittenNote hevyId={written.hevy_id} />}
      </Card>

      <Card title="The week" caption={`${plan.total_sets} sets across seven days`}>
        <div style={{ display: "grid", gap: 10 }}>
          {week.map(({ iso, sessions }) => {
            const off = lost.get(iso);
            return (
              <div
                key={iso}
                style={{
                  display: "grid",
                  gridTemplateColumns: "72px minmax(0, 1fr) auto",
                  gap: 14,
                  alignItems: "start",
                  padding: "10px 12px",
                  borderRadius: 12,
                  background: sessions.length && !off ? "var(--surface-raised)" : "transparent",
                  border: `1px solid ${sessions.length && !off ? "transparent" : "var(--grid)"}`,
                  opacity: off ? 0.55 : 1,
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

                {off ? (
                  <div style={{ fontSize: 13, color: "var(--warning)" }}>
                    unavailable{off.reason ? ` — ${off.reason}` : ""}
                    <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
                      Plan again to rebalance the week around it.
                    </div>
                  </div>
                ) : sessions.length === 0 ? (
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

                <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  {!off && sessions.some((s) => s.kind === "lift") && (
                    <button
                      onClick={() =>
                        act(async () => {
                          setWritten(null);
                          setProposal({ ...(await api.planRoutine(plan.id!, iso)), day: iso });
                        })
                      }
                      disabled={busy}
                      style={{ ...BUTTON, padding: "4px 10px", fontSize: 12 }}
                    >
                      Send to Hevy
                    </button>
                  )}
                  {/* Declaring a day lost is the thing meant to trigger a
                      replan, and it used to be a terminal command. */}
                  <button
                    onClick={() =>
                      act(async () => {
                        if (off) await api.clearUnavailable(iso);
                        else await api.markUnavailable(iso);
                        setDays(await api.availability(plan.week_start));
                      })
                    }
                    disabled={busy}
                    aria-label={off ? `Mark ${iso} available` : `Mark ${iso} unavailable`}
                    style={{ ...BUTTON, padding: "4px 10px", fontSize: 12, color: "var(--text-secondary)" }}
                  >
                    {off ? "Got it back" : "Can’t train"}
                  </button>
                </div>
              </div>
            );
          })}
        </div>

        {proposal && (
          <RoutineDiff
            proposal={proposal}
            busy={busy}
            confirmLabel={`Write ${dayLabel(proposal.day)} to Hevy`}
            onConfirm={() =>
              act(async () => {
                setWritten(await api.approveRoutine(proposal.id));
                setProposal(null);
              })
            }
            onCancel={() => setProposal(null)}
          />
        )}
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
