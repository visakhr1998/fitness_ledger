/** Rendering a goal as something a person can check.
 *
 * Split out of `HomeScreen` so it can be unit-tested without a DOM, the same
 * reason `primitives.ts` holds the chart scales: this repo has no jsdom stack
 * and pins behaviour on pure functions instead.
 *
 * These mirror `intake.describe_goal` and `describe_constraint` on the server.
 * The duplication is deliberate — the intake proposal is rendered *before*
 * anything is saved, and a round trip to format a string the user is already
 * reviewing would make the review slower than the typing was.
 */

export const CONSTRAINT_LABELS: Record<string, string> = {
  no_high_impact: "no running or jumping",
  no_lifting: "no lifting",
  no_intervals: "easy running only",
};

export const WEEKDAYS = [
  "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
];

/** Seconds to a clock. Nobody can confirm that "14400" is the right goal. */
export function clock(seconds: number): string {
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

export function describeGoal(goal: {
  type: string;
  subject: string | null;
  target_value: number;
}): string {
  switch (goal.type) {
    case "race_time":
      return `${(goal.subject ?? "").replace(/_/g, " ")} in ${clock(goal.target_value)}`;
    case "strength_1rm":
      return `${goal.subject} one-rep max of ${goal.target_value} kg`;
    case "running_volume":
      return `${goal.target_value} km a week`;
    case "consistency":
      return `${goal.target_value} sessions a week`;
    case "running_aei":
      return `aerobic efficiency index of ${goal.target_value}`;
    default:
      return `${goal.type} ${goal.subject ?? ""} ${goal.target_value}`.trim();
  }
}

export function describeConstraint(constraint: {
  weekday: number;
  kind: string;
  reason: string | null;
}): string {
  const label = CONSTRAINT_LABELS[constraint.kind] ?? constraint.kind;
  const weekday = WEEKDAYS[constraint.weekday] ?? "?";
  return `${weekday}s: ${label}${constraint.reason ? ` (${constraint.reason})` : ""}`;
}


/** Which screen a goal belongs on.
 *
 * Consistency sits on both: it is a fact about the training week, not about
 * one discipline. A goal type nothing claims would silently disappear, so the
 * default is to show it rather than hide it.
 */
export function goalSection(type: string): "run" | "gym" | "both" {
  switch (type) {
    case "race_time":
    case "running_volume":
    case "running_aei":
      return "run";
    case "strength_1rm":
      return "gym";
    default:
      return "both";
  }
}

export function goalsFor<T extends { type: string; status: string }>(
  section: "run" | "gym",
  goals: T[],
): T[] {
  return goals.filter(
    (goal) =>
      goal.status === "active" &&
      (goalSection(goal.type) === section || goalSection(goal.type) === "both"),
  );
}

/** How a status is shown.
 *
 * `token` colours a dot, never the text. Status colour is reserved and must
 * never be the only signal, and `--good` measures 3.35:1 on the light surface —
 * fine for a mark, below AA for small text. The word carries the meaning; the
 * dot carries the glance.
 */
export const GOAL_STATUS: Record<string, { label: string; token: string; faded: boolean }> = {
  active: { label: "active", token: "var(--accent-alt)", faded: false },
  achieved: { label: "achieved", token: "var(--good)", faded: false },
  abandoned: { label: "archived", token: "var(--text-muted)", faded: true },
};

export function statusOf(status: string) {
  return GOAL_STATUS[status] ?? GOAL_STATUS.active;
}

/** A progress figure as something readable, or null when there is nothing to say. */
export function describeProgress(progress: {
  measurable?: boolean;
  current?: number | null;
  target?: number;
  unit?: string;
} | undefined): string | null {
  if (!progress || progress.measurable === false) return null;
  if (progress.current === null || progress.current === undefined) return null;
  const unit = progress.unit ? ` ${progress.unit}` : "";
  return `${progress.current}${unit} of ${progress.target}${unit}`;
}

/** Percent for a progress bar, or null when the goal cannot be measured. */
export function percentOf(fraction: number | null | undefined): number | null {
  if (fraction === null || fraction === undefined) return null;
  return Math.round(fraction * 100);
}
