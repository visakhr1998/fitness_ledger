/** Application chrome: metric card, tabs, filter, theme and sync controls. */

import { useEffect, useState, type ReactNode } from "react";
import { api, type Range, type SyncStatus } from "../api";

// --- metric card -----------------------------------------------------------

/** The reference's card: label, big number with a small unit, caption, and a
 *  right-hand spark. Proportional figures on the hero -- tabular-nums would
 *  make the digits look loose at this size. */
export function MetricCard({
  label, value, unit, caption, visual, tone = "primary",
}: {
  label: string;
  value: string;
  unit?: string;
  caption?: ReactNode;
  visual?: ReactNode;
  tone?: "primary" | "muted";
}) {
  return (
    <section
      style={{
        background: "var(--surface)",
        borderRadius: "var(--radius-card)",
        padding: "20px 22px",
        border: "1px solid var(--border)",
        display: "flex",
        alignItems: "center",
        gap: 16,
        minHeight: 132,
      }}
    >
      <div style={{ flex: "1 1 auto", minWidth: 0 }}>
        <div style={{ fontSize: 15, color: "var(--text-primary)", fontWeight: 500 }}>{label}</div>
        <div style={{ display: "flex", alignItems: "baseline", gap: 6, marginTop: 10 }}>
          <span
            style={{
              fontSize: 44,
              lineHeight: 1.05,
              fontWeight: 400,
              letterSpacing: "-0.02em",
              color: tone === "muted" ? "var(--text-secondary)" : "var(--text-primary)",
            }}
          >
            {value}
          </span>
          {unit && <span style={{ fontSize: 18, color: "var(--text-secondary)" }}>{unit}</span>}
        </div>
        {caption && (
          <div style={{ color: "var(--text-muted)", fontSize: 13, marginTop: 6 }}>{caption}</div>
        )}
      </div>
      {visual && <div style={{ flex: "0 0 auto" }}>{visual}</div>}
    </section>
  );
}

// --- section tabs ----------------------------------------------------------

const RunGlyph = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
    <circle cx="14.5" cy="4" r="2" fill="currentColor" />
    <path
      d="M13 7.5 9.5 9.8l-1.6 3.6M13 7.5l3.4 2.3 1.1 3.4M13 7.5l-1.2 4.7 2.9 2.4.9 4.9M11.8 12.2 8 14.2l-2.4 4"
      stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"
    />
  </svg>
);

const GymGlyph = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
    <path
      d="M4 9v6M7 7v10M17 7v10M20 9v6M7 12h10"
      stroke="currentColor" strokeWidth="1.9" strokeLinecap="round"
    />
  </svg>
);

/** A calendar week: seven columns, the first one marked. */
const WeekGlyph = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
    <rect x="3" y="5" width="18" height="16" rx="2.5" stroke="currentColor" strokeWidth="1.8" />
    <path d="M3 10h18M8 3v4M16 3v4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    <rect x="6.5" y="13" width="3" height="3" rx="1" fill="currentColor" />
  </svg>
);

/** A target: what the other three sections are measured against. */
const HomeGlyph = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
    <circle cx="12" cy="12" r="8.5" stroke="currentColor" strokeWidth="1.8" />
    <circle cx="12" cy="12" r="4" stroke="currentColor" strokeWidth="1.8" />
    <circle cx="12" cy="12" r="1.4" fill="currentColor" />
  </svg>
);

export type Section = "home" | "run" | "gym" | "week";

export function SectionTabs({
  active, onChange,
}: { active: Section; onChange: (section: Section) => void }) {
  const tabs: { id: Section; label: string; glyph: ReactNode }[] = [
    { id: "home", label: "Goals", glyph: <HomeGlyph /> },
    { id: "run", label: "Run", glyph: <RunGlyph /> },
    { id: "gym", label: "Gym", glyph: <GymGlyph /> },
    { id: "week", label: "Week", glyph: <WeekGlyph /> },
  ];
  return (
    <nav style={{ display: "flex", gap: 8 }} aria-label="Section">
      {tabs.map((tab) => {
        const on = tab.id === active;
        return (
          <button
            key={tab.id}
            onClick={() => onChange(tab.id)}
            aria-current={on ? "page" : undefined}
            style={{
              display: "flex", alignItems: "center", gap: 8,
              padding: "10px 20px",
              borderRadius: "var(--radius-control)",
              background: on ? "var(--accent-soft)" : "transparent",
              color: on ? "var(--accent)" : "var(--text-secondary)",
              border: `1px solid ${on ? "transparent" : "var(--border)"}`,
              fontSize: 15,
              fontWeight: on ? 600 : 400,
            }}
          >
            {tab.glyph}
            {tab.label}
          </button>
        );
      })}
    </nav>
  );
}

// --- time horizon ----------------------------------------------------------

const PRESETS: { id: string; label: string }[] = [
  { id: "last-7-days", label: "7d" },
  { id: "last-14-days", label: "14d" },
  { id: "last-30-days", label: "30d" },
  { id: "last-90-days", label: "90d" },
  { id: "last-6-months", label: "6m" },
  { id: "last-12-months", label: "12m" },
];

export function TimeHorizonFilter({
  range, onChange,
}: { range: Range; onChange: (range: Range) => void }) {
  const [custom, setCustom] = useState(Boolean(range.start && range.end));
  const [from, setFrom] = useState(range.start ?? "");
  const [to, setTo] = useState(range.end ?? "");

  const inputStyle: React.CSSProperties = {
    background: "var(--surface-raised)",
    color: "var(--text-primary)",
    border: "1px solid var(--border)",
    borderRadius: 8,
    padding: "5px 8px",
    fontSize: 13,
    colorScheme: "inherit",
  };

  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
      {PRESETS.map((preset) => {
        const on = !custom && range.window === preset.id;
        return (
          <button
            key={preset.id}
            onClick={() => { setCustom(false); onChange({ window: preset.id }); }}
            aria-pressed={on}
            style={{
              padding: "5px 12px",
              borderRadius: "var(--radius-control)",
              fontSize: 13,
              background: on ? "var(--accent-soft)" : "transparent",
              color: on ? "var(--accent)" : "var(--text-secondary)",
              border: `1px solid ${on ? "transparent" : "var(--border)"}`,
            }}
          >
            {preset.label}
          </button>
        );
      })}

      <button
        onClick={() => setCustom((value) => !value)}
        aria-pressed={custom}
        style={{
          padding: "5px 12px", borderRadius: "var(--radius-control)", fontSize: 13,
          background: custom ? "var(--accent-soft)" : "transparent",
          color: custom ? "var(--accent)" : "var(--text-secondary)",
          border: `1px solid ${custom ? "transparent" : "var(--border)"}`,
        }}
      >
        Custom
      </button>

      {custom && (
        <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} style={inputStyle} aria-label="From" />
          <span style={{ color: "var(--text-muted)" }}>to</span>
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)} style={inputStyle} aria-label="To" />
          <button
            onClick={() => from && to && onChange({ start: from, end: to })}
            disabled={!from || !to}
            style={{
              padding: "5px 12px", borderRadius: "var(--radius-control)", fontSize: 13,
              background: "var(--accent)", color: "#04121a", opacity: from && to ? 1 : 0.4,
            }}
          >
            Apply
          </button>
        </span>
      )}
    </div>
  );
}

// --- theme -----------------------------------------------------------------

type ThemeMode = "light" | "dark" | "system";

/** Three explicit states. The previous single "Theme" button never showed which
 *  mode was active, which is what made it unfriendly. */
export function ThemeControl() {
  const [mode, setMode] = useState<ThemeMode>(
    () => (localStorage.getItem("theme") as ThemeMode) ?? "dark",
  );

  useEffect(() => {
    const root = document.documentElement;
    if (mode === "system") {
      const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      root.setAttribute("data-theme", dark ? "dark" : "light");
    } else {
      root.setAttribute("data-theme", mode);
    }
    localStorage.setItem("theme", mode);
  }, [mode]);

  const options: { id: ThemeMode; label: string }[] = [
    { id: "light", label: "Light" },
    { id: "dark", label: "Dark" },
    { id: "system", label: "Auto" },
  ];

  return (
    <div
      role="radiogroup"
      aria-label="Colour theme"
      style={{
        display: "flex", gap: 2, padding: 3,
        background: "var(--surface-raised)", borderRadius: "var(--radius-control)",
      }}
    >
      {options.map((option) => (
        <button
          key={option.id}
          role="radio"
          aria-checked={mode === option.id}
          onClick={() => setMode(option.id)}
          style={{
            padding: "4px 12px", fontSize: 12, borderRadius: "var(--radius-control)",
            background: mode === option.id ? "var(--surface)" : "transparent",
            color: mode === option.id ? "var(--text-primary)" : "var(--text-muted)",
            fontWeight: mode === option.id ? 600 : 400,
          }}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

// --- sync ------------------------------------------------------------------

/** Sync reports progress rather than blocking: a TCX download per run means a
 *  cold sync takes long enough that silence looks like a hang. */
export function SyncControl({ onDone }: { onDone: () => void }) {
  const [status, setStatus] = useState<SyncStatus | null>(null);
  const [polling, setPolling] = useState(false);

  useEffect(() => {
    if (!polling) return;
    const timer = setInterval(async () => {
      const next = await api.syncStatus();
      setStatus(next);
      if (next.status === "done" || next.status === "error") {
        setPolling(false);
        if (next.status === "done") onDone();
      }
    }, 900);
    return () => clearInterval(timer);
  }, [polling, onDone]);

  const running = status?.status === "running" || polling;

  const tone =
    status?.status === "error" ? "var(--critical)"
    : status?.status === "done" ? "var(--good)"
    : "var(--text-muted)";

  return (
    <div style={{ position: "relative", display: "flex", alignItems: "center", gap: 10 }}>
      <button
        onClick={async () => { await api.startSync(); setPolling(true); setStatus({ status: "running", steps: [], error: null }); }}
        disabled={running}
        style={{
          padding: "7px 16px", borderRadius: "var(--radius-control)",
          border: "1px solid var(--border)", fontSize: 13,
          color: running ? "var(--text-muted)" : "var(--text-primary)",
          background: "var(--surface)",
        }}
      >
        {running ? "Syncing…" : "Sync"}
      </button>

      {status && status.status !== "idle" && (
        <div
          role="status"
          style={{
            fontSize: 12, color: tone, display: "flex", alignItems: "center", gap: 6,
            maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          }}
        >
          <span aria-hidden>{status.status === "error" ? "▲" : status.status === "done" ? "✓" : "•"}</span>
          {status.status === "error"
            ? status.error
            : status.steps.length
              ? `${status.steps[status.steps.length - 1].name} · ${status.steps.length}/5`
              : "starting…"}
        </div>
      )}
    </div>
  );
}
