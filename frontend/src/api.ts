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

export const api = {
  run: (range: Range) => get<RunSection>(`/api/run${qs(range)}`),
  gym: (range: Range) => get<GymSection>(`/api/gym${qs(range)}`),
  vitals: () => get<Vitals>("/api/vitals"),
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
};
