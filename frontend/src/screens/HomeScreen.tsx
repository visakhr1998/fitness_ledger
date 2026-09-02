/** Goals section: what the other three screens are measured against.
 *
 * Goals existed as an API and a typed client before this screen did, and
 * nothing called them — setting one meant knowing that `strength_1rm` needs a
 * subject and that a race time is stored in seconds. That is a schema, not a
 * question anyone asks themselves, so the top of this screen takes a paragraph
 * instead.
 *
 * What comes back is a *proposal*. It is shown for confirmation and saved only
 * on a click, through the same goal and constraint endpoints the CLI uses — the
 * same propose → confirm shape as Hevy write-back, because a parser that
 * quietly persisted would put the model between the user and their own record.
 *
 * Not scoped by the time-horizon filter, for the reason Week is not: a goal is
 * not a window over past training.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type GoalsSection,
  type IntakeProposal,
  type RecurringConstraint,
} from "../api";
import { Card } from "../charts/primitives";
import { describeConstraint, describeGoal } from "../goalText";
import { ErrorNote, Loading } from "./RunScreen";

const chipStyle = {
  display: "inline-flex",
  alignItems: "center",
  gap: 8,
  padding: "6px 12px",
  borderRadius: "var(--radius-control)",
  border: "1px solid var(--border)",
  fontSize: 13,
} as const;

/** A fetch that never reached the server throws a bare TypeError whose message
 *  is "Failed to fetch" — true, and useless. The overwhelmingly likely cause is
 *  that the backend is not running, so say that instead. */
function readable(exc: unknown): string {
  if (exc instanceof DOMException && exc.name === "AbortError") return "";
  if (exc instanceof TypeError) {
    return "Could not reach the server. Is it running? (ledger serve)";
  }
  return exc instanceof Error ? exc.message : String(exc);
}

/** The messy-input box and whatever it produced. */
function Intake({ onSaved }: { onSaved: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [proposal, setProposal] = useState<IntakeProposal | null>(null);
  // Seconds spent waiting. The model takes roughly 8-20s and occasionally far
  // longer, which behind a static "Reading…" is indistinguishable from a hang —
  // that is exactly how a stopped server was first reported as a bug.
  const [elapsed, setElapsed] = useState(0);
  const pending = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!busy) return;
    const started = Date.now();
    setElapsed(0);
    const timer = window.setInterval(
      () => setElapsed(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [busy]);

  // Abort in flight if the screen goes away, so a slow reply cannot land on an
  // unmounted component.
  useEffect(() => () => pending.current?.abort(), []);

  const parse = async () => {
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setError(null);
    setProposal(null);
    try {
      setProposal(await api.intake(text, controller.signal));
    } catch (exc) {
      // An abort is the user's own doing, so it is not an error to report.
      const message = readable(exc);
      if (message) setError(message);
    } finally {
      pending.current = null;
      setBusy(false);
    }
  };

  const cancel = () => pending.current?.abort();

  const save = async () => {
    if (!proposal) return;
    setBusy(true);
    setError(null);
    try {
      for (const goal of proposal.goals) {
        await api.addGoal({
          type: goal.type,
          subject: goal.subject,
          target_value: goal.target_value,
          target_date: goal.target_date,
        });
      }
      for (const c of proposal.constraints) {
        await api.addConstraint({ weekday: c.weekday, kind: c.kind, reason: c.reason });
      }
      setProposal(null);
      setText("");
      onSaved();
    } catch (exc) {
      setError(readable(exc));
    } finally {
      setBusy(false);
    }
  };

  const found = proposal ? proposal.goals.length + proposal.constraints.length : 0;

  return (
    <Card
      title="What are you training for?"
      caption="Say it however you like — a race and a time, a lift and a number, days that never work."
    >
      <textarea
        value={text}
        onChange={(event) => setText(event.target.value)}
        rows={4}
        placeholder="e.g. sub-4 marathon in November, want my bench at 100kg, knee hurts on Wednesdays"
        aria-label="Describe your goals"
        style={{
          width: "100%",
          boxSizing: "border-box",
          background: "var(--bg)",
          color: "var(--text-primary)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-control)",
          padding: "12px 14px",
          fontSize: 14,
          fontFamily: "inherit",
          resize: "vertical",
        }}
      />

      <div style={{ display: "flex", gap: 10, marginTop: 12, alignItems: "center" }}>
        <button
          onClick={parse}
          disabled={busy || text.trim().length === 0}
          style={{
            padding: "9px 18px",
            borderRadius: "var(--radius-control)",
            border: "1px solid transparent",
            background: "var(--accent-soft)",
            color: "var(--accent)",
            fontSize: 14,
            fontWeight: 600,
            cursor: busy || !text.trim() ? "default" : "pointer",
            opacity: busy || !text.trim() ? 0.5 : 1,
          }}
        >
          {busy ? `Reading… ${elapsed}s` : "Read this"}
        </button>
        {busy ? (
          <button
            onClick={cancel}
            style={{
              padding: "9px 14px",
              borderRadius: "var(--radius-control)",
              border: "1px solid var(--border)",
              background: "transparent",
              color: "var(--text-secondary)",
              fontSize: 13,
              cursor: "pointer",
            }}
          >
            Cancel
          </button>
        ) : null}
        <span style={{ color: "var(--text-muted)", fontSize: 12 }}>
          {busy && elapsed >= 8
            ? "The model is thinking. This usually takes 8-20 seconds."
            : "Nothing is saved until you confirm."}
        </span>
      </div>

      {error && (
        <div role="alert" style={{ marginTop: 14, color: "var(--critical)", fontSize: 13 }}>
          {error}
        </div>
      )}

      {proposal?.safety && (
        <div
          role="alert"
          style={{
            marginTop: 16,
            padding: "14px 16px",
            borderRadius: "var(--radius-card)",
            border: "1px solid var(--critical)",
            color: "var(--text-secondary)",
            fontSize: 13,
            whiteSpace: "pre-wrap",
          }}
        >
          {proposal.message}
        </div>
      )}

      {proposal && !proposal.safety && (
        <div style={{ marginTop: 18 }}>
          {found === 0 && (
            <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
              {proposal.message || "Nothing in that mapped to a goal."}
            </div>
          )}

          {proposal.goals.length > 0 && (
            <>
              <h3 style={{ fontSize: 13, fontWeight: 600, margin: "0 0 8px" }}>Goals found</h3>
              <ul style={{ margin: "0 0 14px", paddingLeft: 18, fontSize: 14 }}>
                {proposal.goals.map((goal, index) => (
                  <li key={index} style={{ marginBottom: 4 }}>
                    {describeGoal(goal)}
                    {goal.target_date && (
                      <span style={{ color: "var(--text-muted)" }}> by {goal.target_date}</span>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}

          {proposal.constraints.length > 0 && (
            <>
              <h3 style={{ fontSize: 13, fontWeight: 600, margin: "0 0 8px" }}>Constraints found</h3>
              <ul style={{ margin: "0 0 14px", paddingLeft: 18, fontSize: 14 }}>
                {proposal.constraints.map((c, index) => (
                  <li key={index} style={{ marginBottom: 4 }}>{describeConstraint(c)}</li>
                ))}
              </ul>
            </>
          )}

          {/* Reported rather than dropped: a goal that silently vanished reads
              as the app ignoring you. */}
          {proposal.unclear.length > 0 && (
            <div style={{ color: "var(--text-muted)", fontSize: 12, marginBottom: 10 }}>
              Not turned into a goal: {proposal.unclear.join("; ")}
            </div>
          )}
          {proposal.rejected.length > 0 && (
            <div style={{ color: "var(--warn, var(--text-muted))", fontSize: 12, marginBottom: 10 }}>
              Could not be saved: {proposal.rejected.join("; ")}
            </div>
          )}

          {found > 0 && (
            <button
              onClick={save}
              disabled={busy}
              style={{
                padding: "9px 18px",
                borderRadius: "var(--radius-control)",
                border: "1px solid transparent",
                background: "var(--accent)",
                color: "var(--bg)",
                fontSize: 14,
                fontWeight: 600,
                cursor: busy ? "default" : "pointer",
              }}
            >
              {busy ? "Saving…" : `Save ${found} item${found === 1 ? "" : "s"}`}
            </button>
          )}
        </div>
      )}
    </Card>
  );
}

function Constraints({
  constraints, onChange,
}: { constraints: RecurringConstraint[]; onChange: () => void }) {
  const remove = async (id: number) => {
    await api.deleteConstraint(id);
    onChange();
  };

  return (
    <Card
      title="Standing constraints"
      caption="Rules that apply every week — not the same as a single day you lose, which you declare on Week."
    >
      {constraints.length === 0 ? (
        <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
          None. A constraint narrows what a day can hold rather than removing it.
        </div>
      ) : (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          {constraints.map((c) => (
            <span key={c.id} style={chipStyle}>
              {describeConstraint(c)}
              <button
                onClick={() => remove(c.id)}
                aria-label={`Remove ${describeConstraint(c)}`}
                style={{
                  border: "none", background: "transparent", cursor: "pointer",
                  color: "var(--text-muted)", fontSize: 16, lineHeight: 1, padding: 0,
                }}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
    </Card>
  );
}

export function HomeScreen({ reloadKey }: { reloadKey: number }) {
  const [data, setData] = useState<GoalsSection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [localKey, setLocalKey] = useState(0);

  const reload = useCallback(() => setLocalKey((key) => key + 1), []);

  useEffect(() => {
    let live = true;
    api
      .goals()
      .then((body) => live && setData(body))
      .catch((exc) => live && setError(exc instanceof Error ? exc.message : String(exc)));
    return () => {
      live = false;
    };
  }, [reloadKey, localKey]);

  if (error) return <ErrorNote message={error} />;
  if (!data) return <Loading />;

  const active = data.goals.filter((goal) => goal.status === "active");

  return (
    <div style={{ display: "grid", gap: "var(--gap)" }}>
      <Intake onSaved={reload} />

      <Card
        title="Active goals"
        caption="What the coach plans toward. Targets are the weekly maintenance level; these are what you are chasing."
      >
        {active.length === 0 ? (
          <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
            No goals yet. Describe one above.
          </div>
        ) : (
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 14 }}>
            {active.map((goal) => (
              <li key={goal.id} style={{ marginBottom: 8 }}>
                {describeGoal(goal)}
                {goal.target_date && (
                  <span style={{ color: "var(--text-muted)" }}> by {goal.target_date}</span>
                )}
                <button
                  onClick={async () => {
                    await api.closeGoal(goal.id, "achieved");
                    reload();
                  }}
                  style={{
                    marginLeft: 10, border: "1px solid var(--border)", background: "transparent",
                    color: "var(--text-secondary)", borderRadius: "var(--radius-control)",
                    padding: "2px 10px", fontSize: 12, cursor: "pointer",
                  }}
                >
                  achieved
                </button>
              </li>
            ))}
          </ul>
        )}

        {data.running_target && (
          <div style={{ marginTop: 16, color: "var(--text-secondary)", fontSize: 13 }}>
            Weekly running target: {data.running_target.distance_km_per_week} km over{" "}
            {data.running_target.sessions_per_week} sessions.
          </div>
        )}
      </Card>

      <Constraints constraints={data.constraints} onChange={reload} />
    </div>
  );
}
