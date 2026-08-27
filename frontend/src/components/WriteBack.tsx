/** Routine write-back: propose, review the diff, then confirm.
 *
 * The confirm step is the whole point. Hevy has no delete endpoint, so once a
 * routine is written it can only be removed by hand in the app -- the UI says
 * so before you commit, not after.
 */

import { useState } from "react";
import { api, type ExerciseSummary, type RoutineProposal } from "../api";
import { Card } from "../charts/primitives";
import { ExerciseIcon } from "./ExerciseIcon";
import { RoutineDiff, WrittenNote } from "./RoutineDiff";

export function WriteBack({ catalog }: { catalog: ExerciseSummary[] }) {
  const [picked, setPicked] = useState<string[]>([]);
  const [title, setTitle] = useState("");
  const [proposal, setProposal] = useState<RoutineProposal | null>(null);
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
      setWritten(await api.approveRoutine(proposal.id));
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

      {written && <WrittenNote hevyId={written.hevy_id} />}

      {proposal && (
        <RoutineDiff
          proposal={proposal}
          busy={busy}
          onConfirm={approve}
          onCancel={() => setProposal(null)}
        />
      )}
    </Card>
  );
}
