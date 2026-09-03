/** Active goals, on the screen that measures them.
 *
 * Run and Gym are where you look at the numbers, so a goal that lives only on
 * the Goals tab is a goal you never see while reading the data it is about.
 *
 * The progress figure is computed by `queries.goal_progress` and arrives with
 * the goals list — no model is involved in producing it. "Am I close?" hands
 * that figure to the chat dock so the model explains a number it was given,
 * which is the same division `chat.py` enforces everywhere else.
 */

import { useEffect, useState } from "react";
import { api, type GoalProgress, type GoalsSection } from "../api";
import { Card } from "../charts/primitives";
import { describeGoal, describeProgress, goalsFor, percentOf, statusOf } from "../goalText";

function ProgressBar({ percent }: { percent: number }) {
  return (
    <div
      role="img"
      aria-label={`${percent}% of target`}
      style={{
        height: 6,
        borderRadius: 3,
        background: "var(--surface-raised)",
        overflow: "hidden",
        marginTop: 8,
      }}
    >
      <div
        style={{
          width: `${percent}%`,
          height: "100%",
          background: "var(--accent-alt)",
          borderRadius: 3,
        }}
      />
    </div>
  );
}

export function GoalStrip({
  section, reloadKey, onAsk,
}: {
  section: "run" | "gym";
  reloadKey: number;
  onAsk: (question: string) => void;
}) {
  const [data, setData] = useState<GoalsSection | null>(null);
  const [asking, setAsking] = useState<number | null>(null);

  useEffect(() => {
    let live = true;
    api.goals().then((body) => live && setData(body)).catch(() => live && setData(null));
    return () => {
      live = false;
    };
  }, [reloadKey]);

  const goals = data ? goalsFor(section, data.goals) : [];
  // Nothing to say beats an empty card: the Goals tab is where you add one.
  if (goals.length === 0) return null;

  const ask = async (id: number) => {
    setAsking(id);
    try {
      const detail = await api.goalProgress(id);
      // The endpoint always composes one; guard rather than send "undefined".
      if (detail.question) onAsk(detail.question);
    } finally {
      setAsking(null);
    }
  };

  return (
    <Card
      title={section === "run" ? "Running goals" : "Lifting goals"}
      caption="What you are chasing. Progress is measured from your logged data, not estimated by the model."
    >
      <div style={{ display: "grid", gap: 14 }}>
        {goals.map((goal) => {
          const progress: GoalProgress | undefined = data?.progress?.[String(goal.id)];
          const percent = percentOf(progress?.fraction);
          const summary = describeProgress(progress);
          const status = statusOf(goal.status);

          return (
            <div key={goal.id}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
                <span
                  aria-hidden
                  style={{
                    width: 8, height: 8, borderRadius: 4,
                    background: status.token, flex: "0 0 auto",
                  }}
                />
                <span style={{ fontSize: 14, color: "var(--text-primary)" }}>
                  {describeGoal(goal)}
                </span>
                {goal.target_date && (
                  <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                    by {goal.target_date}
                  </span>
                )}
                <span style={{ flex: "1 1 auto" }} />
                <button
                  onClick={() => ask(goal.id)}
                  disabled={asking === goal.id}
                  style={{
                    border: "1px solid var(--border)",
                    background: "transparent",
                    color: "var(--text-secondary)",
                    borderRadius: "var(--radius-control)",
                    padding: "4px 12px",
                    fontSize: 12,
                    cursor: asking === goal.id ? "default" : "pointer",
                  }}
                >
                  {asking === goal.id ? "asking…" : "Am I close?"}
                </button>
              </div>

              <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 4 }}>
                {summary
                  ? `${summary}${percent !== null ? ` · ${percent}%` : ""}`
                  : progress?.detail || "not measurable yet"}
              </div>
              {percent !== null && <ProgressBar percent={percent} />}
            </div>
          );
        })}
      </div>
    </Card>
  );
}
