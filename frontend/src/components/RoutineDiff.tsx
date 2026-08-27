/** The propose → diff → confirm surface, shared by every path that writes.
 *
 * Extracted from WriteBack so plan approval reuses it rather than growing a
 * second one. Two entry points to one flow is how `ledger sync` came to run
 * three of five steps (#16), and this is the flow where that would cost most:
 * **Hevy has no delete endpoint**, so a routine written by mistake can only be
 * removed by hand in the app. The diff is what makes the write deliberate, and
 * it is never optional.
 */

import type { RoutineProposal } from "../api";

export function RoutineDiff({
  proposal,
  busy,
  onConfirm,
  onCancel,
  confirmLabel = "Write to Hevy",
}: {
  proposal: RoutineProposal;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  confirmLabel?: string;
}) {
  return (
    <div style={{ marginTop: 14, borderTop: "1px solid var(--grid)", paddingTop: 14 }}>
      <div style={{ fontSize: 14, fontWeight: 500 }}>{proposal.summary}</div>

      <div style={{ overflowX: "auto" }}>
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
      </div>

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
          onClick={onConfirm}
          disabled={busy}
          style={{
            padding: "8px 18px", borderRadius: "var(--radius-control)", fontSize: 13,
            fontWeight: 600, background: "var(--accent)", color: "#04121a",
          }}
        >
          {busy ? "Writing…" : confirmLabel}
        </button>
        <button
          onClick={onCancel}
          style={{
            padding: "8px 18px", borderRadius: "var(--radius-control)", fontSize: 13,
            border: "1px solid var(--border)", color: "var(--text-secondary)",
          }}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

/** What a completed write looks like, wherever it was triggered from. */
export function WrittenNote({ hevyId }: { hevyId: string | null }) {
  return (
    <div
      role="status"
      style={{
        marginTop: 12, padding: "10px 14px", borderRadius: 14,
        background: "var(--surface-raised)", borderLeft: "3px solid var(--good)", fontSize: 13,
      }}
    >
      <strong style={{ color: "var(--good)" }}>✓ Written to Hevy</strong>
      {hevyId && <span style={{ color: "var(--text-muted)" }}> · id {hevyId}</span>}
      <div style={{ color: "var(--text-muted)", marginTop: 4 }}>
        Remove it in the Hevy app if you did not want it — the API cannot delete.
      </div>
    </div>
  );
}
