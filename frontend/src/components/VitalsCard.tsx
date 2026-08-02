/** Vitals: measured values, clearly-labelled estimates, and what is missing. */

import { useState } from "react";
import { api, type Vitals } from "../api";
import { fmt } from "../charts/primitives";

function Row({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "5px 0" }}>
      <span style={{ color: "var(--text-secondary)", fontSize: 13 }}>{label}</span>
      <span style={{ fontSize: 13 }} className="tabular">
        {value}
        {note && <span style={{ color: "var(--text-muted)", marginLeft: 6, fontSize: 11 }}>{note}</span>}
      </span>
    </div>
  );
}

export function VitalsCard({ vitals, compact = false }: { vitals: Vitals; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const [sex, setSex] = useState(vitals.sex ?? "");
  const [maxHr, setMaxHr] = useState(vitals.max_hr_source === "user" ? String(vitals.max_heart_rate ?? "") : "");
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      await api.saveSettings({
        sex: sex || "",
        ...(maxHr ? { max_heart_rate: Number(maxHr) } : {}),
      });
      window.location.reload();
    } finally {
      setSaving(false);
    }
  };

  return (
    <section
      style={{
        background: "var(--surface)", borderRadius: "var(--radius-card)",
        padding: "18px 20px", border: "1px solid var(--border)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <h2 style={{ margin: 0, fontSize: 15, fontWeight: 500, flex: "1 1 auto" }}>You</h2>
        <button
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          style={{ fontSize: 12, color: "var(--text-secondary)" }}
        >
          {open ? "Close" : "Edit"}
        </button>
      </div>

      <div style={{ marginTop: 10 }}>
        <Row label="Age" value={vitals.age ? `${vitals.age}` : "–"} />
        <Row label="Height" value={vitals.height_cm ? `${fmt(vitals.height_cm, 0)} cm` : "–"} />
        <Row label="Weight" value={vitals.weight_kg ? `${fmt(vitals.weight_kg, 1)} kg` : "–"} />
        {vitals.bmi && <Row label="BMI" value={fmt(vitals.bmi, 1)} />}
        <Row label="Resting HR" value={vitals.resting_heart_rate ? `${vitals.resting_heart_rate} bpm` : "–"} />
        <Row
          label="VO₂ max"
          value={vitals.vo2_max ? `${fmt(vitals.vo2_max, 1)}` : "–"}
          note={vitals.cardio_fitness_level ?? undefined}
        />
        <Row
          label="Max HR"
          value={vitals.max_heart_rate ? `${vitals.max_heart_rate} bpm` : "–"}
          note={vitals.max_hr_source}
        />
        <Row
          label="BMR"
          value={vitals.bmr_kcal ? `${vitals.bmr_kcal} kcal` : "–"}
          note={vitals.bmr_kcal ? "estimated" : undefined}
        />
      </div>

      {vitals.needs.length > 0 && (
        <div
          style={{
            marginTop: 10, fontSize: 12, color: "var(--text-muted)",
            borderTop: "1px solid var(--grid)", paddingTop: 10,
          }}
        >
          Needs {vitals.needs.join(" and ")} to finish the card.{" "}
          <button onClick={() => setOpen(true)} style={{ color: "var(--accent)", fontSize: 12 }}>
            Add
          </button>
        </div>
      )}

      {!compact && vitals.zones.length > 0 && (
        <div style={{ marginTop: 14, borderTop: "1px solid var(--grid)", paddingTop: 12 }}>
          <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
            Heart-rate zones <span style={{ color: "var(--text-muted)" }}>· Karvonen, from reserve</span>
          </div>
          {vitals.zones.map((zone) => (
            <div
              key={zone.name}
              style={{ display: "flex", justifyContent: "space-between", fontSize: 12, padding: "3px 0" }}
            >
              <span style={{ color: "var(--text-secondary)" }}>{zone.name}</span>
              <span className="tabular" style={{ color: "var(--text-muted)" }}>
                {zone.low_bpm}–{zone.high_bpm}
              </span>
            </div>
          ))}
        </div>
      )}

      {open && (
        <div style={{ marginTop: 14, borderTop: "1px solid var(--grid)", paddingTop: 12, display: "grid", gap: 10 }}>
          <label style={{ fontSize: 12, color: "var(--text-secondary)", display: "grid", gap: 4 }}>
            Sex <span style={{ color: "var(--text-muted)" }}>(needed for BMR — the formula differs by 166 kcal)</span>
            <select
              value={sex}
              onChange={(event) => setSex(event.target.value)}
              style={{
                background: "var(--surface-raised)", color: "var(--text-primary)",
                border: "1px solid var(--border)", borderRadius: 8, padding: "6px 8px", fontSize: 13,
              }}
            >
              <option value="">Not set</option>
              <option value="male">Male</option>
              <option value="female">Female</option>
            </select>
          </label>

          <label style={{ fontSize: 12, color: "var(--text-secondary)", display: "grid", gap: 4 }}>
            Max heart rate <span style={{ color: "var(--text-muted)" }}>(blank uses the age estimate)</span>
            <input
              type="number" value={maxHr} min={100} max={230} placeholder="188"
              onChange={(event) => setMaxHr(event.target.value)}
              style={{
                background: "var(--surface-raised)", color: "var(--text-primary)",
                border: "1px solid var(--border)", borderRadius: 8, padding: "6px 8px", fontSize: 13,
              }}
            />
          </label>

          <button
            onClick={save}
            disabled={saving}
            style={{
              background: "var(--accent)", color: "#04121a", borderRadius: "var(--radius-control)",
              padding: "7px 14px", fontSize: 13, fontWeight: 600, justifySelf: "start",
            }}
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      )}
    </section>
  );
}
