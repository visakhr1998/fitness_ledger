/** Typed client for the FastAPI backend.
 *
 * Every number here is computed server-side by the rules engine. The frontend
 * formats and draws; it does not calculate, so the dashboard and the CLI can
 * never disagree about a figure.
 */

export type Range = { window?: string; start?: string; end?: string };

export type Bucket = { bucket: string; total: number; count: number };

export type AeiPoint = {
  date: string;
  aei: number;
  actual_distance_km: number;
  adjusted_distance_km: number;
  grade_ratio: number | null;
  avg_heart_rate: number | null;
  total_beats: number | null;
};

export type RunSection = {
  window: string;
  start: string;
  end: string;
  bucket: string;
  aei: {
    latest: number | null;
    previous_period_mean: number | null;
    delta: number | null;
    mean: number | null;
    points: AeiPoint[];
    excluded: { date: string; reason: string; tracked_m: number }[];
  };
  runs: {
    count: number;
    total_km: number;
    avg_heart_rate: number | null;
    /** One row per run, oldest first, nothing filtered. Every Run chart reads this. */
    list: {
      date: string;
      distance_km: number;
      duration_s: number | null;
      avg_heart_rate: number | null;
    }[];
  };
};

export type CoverageRow = {
  muscle_group: string;
  effective_sets: number;
  sets_per_week: number;
  target_sets: number;
  target_sets_per_week: number;
  sets_deficit: number;
  pct_of_target: number | null;
  frequency: number;
  target_frequency: number;
  tonnage_kg: number;
};

export type GymSection = {
  window: string;
  start: string;
  end: string;
  bucket: string;
  radar: { muscle_group: string; effective_sets: number; target_sets: number; share: number }[];
  coverage: CoverageRow[];
  tonnage: {
    buckets: (Bucket & { above_average: boolean })[];
    mean: number;
    total: number;
  };
  workouts: number;
  working_sets: number;
};

export type Vitals = {
  age: number | null;
  height_cm: number | null;
  weight_kg: number | null;
  resting_heart_rate: number | null;
  vo2_max: number | null;
  cardio_fitness_level: string | null;
  max_heart_rate: number | null;
  max_hr_source: string;
  sex: string | null;
  zones: { name: string; low_pct: number; high_pct: number; low_bpm: number; high_bpm: number }[];
  bmr_kcal: number | null;
  bmi: number | null;
  needs: string[];
};

export type ExerciseSummary = {
  id: string;
  title: string;
  primary_muscle_group: string;
  secondary_muscle_groups: string[];
  equipment: string | null;
  icon: string;
  logged_sets: number;
};

export type ExerciseDetail = {
  exercise: ExerciseSummary;
  window: string;
  bucket: string;
  one_rep_max: {
    points: { date: string; estimated_1rm_kg: number; weight_kg: number; reps: number }[];
    latest: number | null;
    change_kg: number | null;
  };
  progression: {
    exercise: string;
    rep_range: string;
    working_weight_kg: number | null;
    reps: number[];
    sessions_at_weight: number;
    ready_to_progress: boolean;
    suggested_weight_kg: number | null;
    verdict: string;
    stalled: boolean;
  };
  volume: {
    sets_per_bucket: Bucket[];
    tonnage_per_bucket: Bucket[];
    total_sets: number;
    total_tonnage_kg: number;
  };
  sessions: number;
};

/** A stored training week. Set counts were computed from the deficit, never
 *  proposed by the model — see planning.py. */
export type PlanSection = {
  available: boolean;
  reason?: string;
  plan: {
    id: number | null;
    week_start: string;
    generated_at: string | null;
    status: string;
    supersedes: number | null;
    rationale: string;
    trade_offs: string;
    /** Tools the model called. An empty trace is the *expected* shape: the
     *  context reader injects the deficit and the pool, so a good run has
     *  nothing left to fetch. */
    agent_trace: { tool?: string; args?: Record<string, unknown> }[];
    total_sets: number;
    sessions: {
      date: string;
      kind: string;
      focus: string;
      distance_km: number | null;
      total_sets: number;
      exercises: {
        exercise_template_id: string;
        title: string;
        sets: number;
        targets: string[];
      }[];
    }[];
  } | null;
  problems: string[];
  /** The deterministic half of why the week looks as it does. Read off
   *  planning.py's constants and your own preferences server-side, so the UI
   *  never keeps a second copy of a number that can drift. */
  rules?: {
    priority_order: string[];
    set_counts_from: string;
    limits: {
      min_sets_per_exercise: number;
      max_sets_per_exercise: number;
      max_sets_per_session: number;
      min_rest_days_same_muscle: number;
      allow_run_after_leg_day: boolean;
    };
  };
  adherence: {
    not_started: boolean;
    sessions_planned: number;
    sessions_completed: number;
    sessions_ahead: number;
    missed_days: string[];
  } | null;
};

export type Insight = {
  rule: string;
  severity: "warn" | "info";
  subject: string;
  message: string;
  detected_at: string;
  data: Record<string, unknown>;
};

/** Findings plus the scope they were found over.
 *
 *  The rules obey neither the section tabs nor the time-horizon filter, so the
 *  window comes from the backend rather than being named again here.
 */
export type InsightReport = {
  section: string | null;
  window: string;
  weeks: number;
  insights: Insight[];
};

export type SyncStatus = {
  status: "idle" | "running" | "done" | "error";
  steps: { name: string; detail: unknown }[];
  error: string | null;
};

/** Progress of a background plan generation, polled like a sync.
 *
 *  `unavailable` is separate from `error` on purpose: a missing coach extra is
 *  a setup problem, not a failure of the coach, and the callout says which.
 */
export type PlanStatus = {
  status: "idle" | "running" | "done" | "error" | "unavailable";
  week: string | null;
  plan_id: number | null;
  error: string | null;
  problems?: string[];
  planned_by?: string | null;
  fell_back?: boolean;
};

/** One row of a Hevy routine diff. Shared by the Week tab and Build a routine. */
export type DiffRow = {
  change: "add" | "change" | "remove" | "same";
  exercise: string;
  before: string | null;
  after: string | null;
  why: string;
};

export type RoutineProposal = {
  id: number;
  summary: string;
  diff: { rows: DiffRow[]; added: number; changed: number; removed: number; warning: string };
};

export type Goal = {
  id: number;
  type: string;
  subject: string | null;
  target_value: number;
  target_date: string | null;
  status: string;
};

/** A standing weekday restriction — not the same thing as a lost day.
 *
 * `Availability` records a specific date the user lost. A constraint is a rule
 * that outlives any one week, and it narrows what a day can hold rather than
 * removing the day: a knee that dislikes running is no reason to skip bench.
 */
export type RecurringConstraint = {
  id: number;
  weekday: number;
  weekday_name: string;
  kind: "no_high_impact" | "no_lifting" | "no_intervals";
  reason: string | null;
};

/** How close a goal is. Computed server-side by `queries.goal_progress` — the
 *  model is never the thing that decided this number.
 *
 *  `current` is null rather than 0 when a goal cannot be measured yet, because
 *  "no data" and "no progress" are different answers and a bar would show them
 *  identically. `measurable` says which one it is. */
export type GoalProgress = {
  goal_id: number | null;
  type: string;
  subject: string | null;
  target: number;
  current: number | null;
  fraction: number | null;
  unit: string;
  window: string;
  detail: string;
  measurable: boolean;
  /** Only on the single-goal endpoint: the prompt to hand the chat dock. */
  question?: string;
};

export type GoalsSection = {
  goals: Goal[];
  running_target: { distance_km_per_week: number; sessions_per_week: number } | null;
  constraints: RecurringConstraint[];
  /** Keyed by goal id as a string, because JSON object keys are strings. */
  progress: Record<string, GoalProgress>;
};

/** What the intake parser found. Nothing here is saved until the user confirms.
 *
 * `safety` is non-null when the text tripped the red-flag check, in which case
 * no model was called and `message` is the fixed referral. `rejected` holds
 * anything the model proposed that failed validation — reported rather than
 * silently dropped, because a vanished goal reads as the app ignoring you.
 */
export type IntakeProposal = {
  goals: Goal[];
  constraints: Omit<RecurringConstraint, "id">[];
  unclear: string[];
  rejected: string[];
  safety: string[] | null;
  message: string;
};

/** Only exceptions are stored — a day with no row is available. */
export type AvailabilitySection = {
  week_start: string;
  unavailable: { date: string; available: boolean; reason: string | null; source: string }[];
};

function qs(range: Range): string {
  const params = new URLSearchParams();
  if (range.start && range.end) {
    params.set("start", range.start);
    params.set("end", range.end);
  } else if (range.window) {
    params.set("window", range.window);
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

/** A thrown fetch error, as something a person can act on.
 *
 * `fetch` rejects with a bare `TypeError` when it cannot reach the host at
 * all, which renders as "Failed to fetch" — true, and useless: the server
 * being stopped is the commonest cause and the message never says so. This
 * lived privately in `HomeScreen`, so the intake box explained itself and the
 * chat dock, the Week tab and write-back all showed the raw string.
 *
 * An aborted request returns "" because the user caused it and there is
 * nothing to report.
 */
export function readable(exc: unknown): string {
  if (exc instanceof DOMException && exc.name === "AbortError") return "";
  if (exc instanceof TypeError) {
    return "Could not reach the server. Is it running? (ledger serve)";
  }
  return exc instanceof Error ? exc.message : String(exc);
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    // FastAPI puts the useful part in `detail`; surface it rather than "500".
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

/** Anything that changes state. Same error unwrapping as `get`: FastAPI puts
 *  the useful part in `detail`, and "500" tells nobody anything. */
async function send<T>(
  path: string,
  method: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    // Only the intake call passes one: it is the only request slow enough that
    // a person may want to stop waiting for it.
    signal,
  });
  let parsed: unknown = null;
  try {
    parsed = await response.json();
  } catch {
    /* empty body */
  }
  if (!response.ok) {
    const detail = (parsed as { detail?: string } | null)?.detail;
    throw new Error(detail ?? response.statusText);
  }
  return parsed as T;
}

export const api = {
  run: (range: Range) => get<RunSection>(`/api/run${qs(range)}`),
  gym: (range: Range) => get<GymSection>(`/api/gym${qs(range)}`),
  vitals: () => get<Vitals>("/api/vitals"),
  plan: (week?: string) => get<PlanSection>(`/api/plan${week ? `?week=${week}` : ""}`),
  insights: (section?: string) =>
    get<InsightReport>(`/api/insights${section ? `?section=${section}` : ""}`),
  exercises: (onlyLogged = true) =>
    get<ExerciseSummary[]>(`/api/exercises?only_logged=${onlyLogged}`),
  exercise: (id: string, range: Range) =>
    get<ExerciseDetail>(`/api/exercises/${id}${qs(range)}`),
  settings: () => get<Record<string, string>>("/api/settings"),
  saveSettings: async (body: Record<string, unknown>) => {
    const response = await fetch("/api/settings", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  },
  startSync: async () => {
    const response = await fetch("/api/sync", { method: "POST" });
    return response.json();
  },
  syncStatus: () => get<SyncStatus>("/api/sync/status"),
  startPlan: async (week?: string) => {
    const response = await fetch(`/api/plan${week ? `?week=${week}` : ""}`, { method: "POST" });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail ?? "could not start planning");
    return body as { status: string; detail?: string };
  },
  planStatus: () => get<PlanStatus>("/api/plan/status"),
  decidePlan: (id: number, status: "approved" | "rejected") =>
    send<{ id: number; status: string }>(`/api/plan/${id}`, "PUT", { status }),
  /** Draft a Hevy routine from one planned day. Never writes — see writeback.py. */
  planRoutine: (id: number, sessionDate: string) =>
    send<RoutineProposal>(`/api/plan/${id}/routine`, "POST", { session_date: sessionDate }),
  approveRoutine: (proposalId: number) =>
    send<{ id: number; status: string; hevy_id: string | null }>(
      `/api/writeback/${proposalId}/approve`,
      "POST",
    ),
  goals: (includeInactive = false) =>
    get<GoalsSection>(`/api/goals${includeInactive ? "?include_inactive=true" : ""}`),
  /** Progress plus the composed question for the chat dock. */
  goalProgress: (id: number) => get<GoalProgress>(`/api/goals/${id}/progress`),
  /** Edit by superseding: the old goal is archived, not overwritten. */
  reviseGoal: (id: number, body: Record<string, unknown>) =>
    send<Goal>(`/api/goals/${id}`, "PATCH", body),
  addGoal: (body: Record<string, unknown>) => send<{ id: number }>("/api/goals", "POST", body),
  closeGoal: (id: number, status: "achieved" | "abandoned") =>
    send<{ id: number }>(`/api/goals/${id}`, "PUT", { status }),
  setRunningTarget: (distance_km_per_week: number, sessions_per_week: number) =>
    send<unknown>("/api/running-target", "PUT", { distance_km_per_week, sessions_per_week }),
  /** Propose goals from a description. Writes nothing — the caller saves.
   *
   *  Takes a signal because this is the one slow call in the app: the model's
   *  response time varies from about 8 to 20 seconds and the user should be
   *  able to give up on it. */
  intake: (text: string, signal?: AbortSignal) =>
    send<IntakeProposal>("/api/intake", "POST", { text }, signal),
  addConstraint: (body: Record<string, unknown>) =>
    send<RecurringConstraint>("/api/constraints", "POST", body),
  deleteConstraint: (id: number) =>
    send<{ id: number; deleted: boolean }>(`/api/constraints/${id}`, "DELETE"),
  availability: (week: string) => get<AvailabilitySection>(`/api/availability?week=${week}`),
  markUnavailable: (date: string, reason?: string) =>
    send<unknown>("/api/availability", "PUT", { date, reason: reason || null }),
  clearUnavailable: (date: string) => send<{ cleared: boolean }>(`/api/availability/${date}`, "DELETE"),
};
