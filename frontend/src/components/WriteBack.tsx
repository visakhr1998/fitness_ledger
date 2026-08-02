/** Routine write-back: propose, review the diff, then confirm.
 *
 * The confirm step is the whole point. Hevy has no delete endpoint, so once a
 * routine is written it can only be removed by hand in the app -- the UI says
 * so before you commit, not after.
 */

import { useState } from "react";
import type { ExerciseSummary } from "../api";
import { Card } from "../charts/primitives";
import { ExerciseIcon } from "./ExerciseIcon";

type DiffRow = {
  change: "add" | "change" | "remove" | "same";
  exercise: string;
  before: string | null;
  after: string | null;
  why: string;
};

type Proposal = {
  id: number;
  summary: string;
  diff: { rows: DiffRow[]; added: number; changed: number; removed: number; warning: string };
};

export function WriteBack({ catalog }: { catalog: ExerciseSummary[] }) {
  const [picked, setPicked] = useState<string[]>([]);
  const [title, setTitle] = useState("");
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [written, setWritten] = useState<{ hevy_id: string | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = (id: string) =>
    setPicked((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  const propose = async () => {
    setBusy(true);
    setError(null);
    setWritten(null);
    try {
      const response = await fetch("/api/writeback/propose", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ title: title || "New routine", exercise_ids: picked }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "could not build a proposal");
      setProposal(body);
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    if (!proposal) return;
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(`/api/writeback/${proposal.id}/approve`, { method: "POST" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "write failed");
      setWritten(body);
      setProposal(null);
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  };

  const top = catalog.slice(0, 14);

  return (
    <Card
      title="Build a routine"
      caption="Weights come from your double-progression state, not from a guess"
    >
      <input
        value={title}
        onChange={(event) => setTitle(event.target.value)}
        placeholder="Routine name"
        aria-label="Routine name"
        style={{
          width: "100%", maxWidth: 320, background: "var(--surface-raised)",
          color: "var(--text-primary)", border: "1px solid var(--border)",
          borderRadius: 10, padding: "8px 10px", fontSize: 13, marginBottom: 12,
        }}
      />

      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 12 }}>
        {top.map((row) => {
          const on = picked.includes(row.id);
          return (
            <button
              key={row.id}
              onClick={() => toggle(row.id)}
              aria-pressed={on}
              style={{
                display: "flex", alignItems: "center", gap: 6,
                padding: "5px 10px", borderRadius: "var(--radius-control)", fontSize: 12,
                background: on ? "var(--accent-soft)" : "transparent",
                color: on ? "var(--accent)" : "var(--text-secondary)",
                border: `1px solid ${on ? "transparent" : "var(--border)"}`,
              }}
            >
              <ExerciseIcon icon={row.icon} size={16} color={on ? "var(--accent)" : "var(--text-muted)"} />
              {row.title}
            </button>
          );
        })}
      </div>

      <button
        onClick={propose}
        disabled={picked.length === 0 || busy}
        style={{
          padding: "7px 16px", borderRadius: "var(--radius-control)", fontSize: 13,
          border: "1px solid var(--border)",
          color: picked.length ? "var(--text-primary)" : "var(--text-muted)",
        }}
      >
        {busy && !proposal ? "Building…" : `Preview routine (${picked.length})`}
      </button>

      {error && (
        <div role="alert" style={{ marginTop: 12, fontSize: 13, color: "var(--critical)" }}>
          {error}
        </div>
      )}

      {written && (
        <div
          role="status"
          style={{
            marginTop: 12, padding: "10px 14px", borderRadius: 14,
            background: "var(--surface-raised)", borderLeft: "3px solid var(--good)", fontSize: 13,
          }}
        >
          <strong style={{ color: "var(--good)" }}>✓ Written to Hevy</strong>
          {written.hevy_id && (
            <span style={{ color: "var(--text-muted)" }}> · id {written.hevy_id}</span>
          )}
          <div style={{ color: "var(--text-muted)", marginTop: 4 }}>
            Remove it in the Hevy app if you did not want it — the API cannot delete.
          </div>
        </div>
      )}

      {proposal && (
        <div style={{ marginTop: 14, borderTop: "1px solid var(--grid)", paddingTop: 14 }}>
          <div style={{ fontSize: 14, fontWeight: 500 }}>{proposal.summary}</div>

          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, marginTop: 10 }}>
            <thead>
              <tr>
                {["", "Exercise", "Now", "Proposed", "Why"].map((header) => (
                  <th
                    key={header}
                    style={{
                      textAlign: "left", fontSize: 11, color: "var(--text-secondary)",
                      padding: "4px 8px 4px 0", borderBottom: "1px solid var(--grid)",
                    }}
                  >
                    {header}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {proposal.diff.rows.map((row) => (
                <tr key={row.exercise}>
                  {/* Symbol plus wording: the change type never rides on colour. */}
                  <td style={{ padding: "6px 8px 6px 0", color: "var(--good)", width: 18 }}>
                    {row.change === "add" ? "+" : row.change === "remove" ? "−" : "~"}
                  </td>
                  <td style={{ padding: "6px 8px 6px 0" }}>{row.exercise}</td>
                  <td className="tabular" style={{ padding: "6px 8px 6px 0", color: "var(--text-muted)" }}>
                    {row.before ?? "—"}
                  </td>
                  <td className="tabular" style={{ padding: "6px 8px 6px 0" }}>{row.after ?? "—"}</td>
                  <td style={{ padding: "6px 8px 6px 0", color: "var(--text-muted)", fontSize: 12 }}>
                    {row.why}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <div
            style={{
              marginTop: 12, padding: "10px 14px", borderRadius: 14,
              background: "var(--surface-raised)", borderLeft: "3px solid var(--warning)",
              fontSize: 12, color: "var(--text-secondary)",
            }}
          >
            <span aria-hidden style={{ color: "var(--warning)" }}>▲ </span>
            <strong>This writes to your real Hevy account.</strong> {proposal.diff.warning}
          </div>

          <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
            <button
              onClick={approve}
              disabled={busy}
              style={{
                padding: "8px 18px", borderRadius: "var(--radius-control)", fontSize: 13,
                fontWeight: 600, background: "var(--accent)", color: "#04121a",
              }}
            >
              {busy ? "Writing…" : "Write to Hevy"}
            </button>
            <button
              onClick={() => setProposal(null)}
              style={{
                padding: "8px 18px", borderRadius: "var(--radius-control)", fontSize: 13,
                border: "1px solid var(--border)", color: "var(--text-secondary)",
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </Card>
  );
}
