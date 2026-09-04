/** The coach strip and the chat dock.
 *
 * Both are observation surfaces. The coach reports what the rules engine found
 * and stops there -- no prescriptions -- which is the same discipline the
 * recovery rule follows on the backend.
 */

import { useEffect, useState } from "react";
import { api, type Insight, type InsightReport } from "../api";
import { CoachMascot } from "./ExerciseIcon";

const RULE_LABELS: Record<string, string> = {
  coverage_gap: "Coverage gap",
  volume_drop: "Volume drop",
  stall: "Stalled lift",
  progression_ready: "Ready to progress",
  recovery_flag: "Recovery",
  running_shortfall: "Running short",
  aei_trend: "Aerobic efficiency",
};

export function CoachStrip({ section, reloadKey }: { section: string; reloadKey: number }) {
  const [report, setReport] = useState<InsightReport | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    // Drop the previous section's findings while the next load is in flight,
    // so switching tabs never shows Gym findings under a Run heading.
    setReport(null);
    setExpanded(null);
    api.insights(section).then(setReport).catch(() => setReport(null));
  }, [section, reloadKey]);

  const insights: Insight[] = report?.insights ?? [];
  if (insights.length === 0) return null;

  // Group by rule: four identical coverage gaps are one finding, not four.
  const groups = new Map<string, Insight[]>();
  for (const insight of insights) {
    groups.set(insight.rule, [...(groups.get(insight.rule) ?? []), insight]);
  }
  const ordered = [...groups.entries()].sort(
    (a, b) => Number(b[1][0].severity === "warn") - Number(a[1][0].severity === "warn"),
  );

  return (
    <section
      style={{
        background: "var(--surface)", borderRadius: "var(--radius-card)",
        padding: "18px 22px", border: "1px solid var(--border)",
      }}
    >
      <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
        <CoachMascot />
        <div style={{ flex: "1 1 auto", minWidth: 0 }}>
          <h2 style={{ margin: 0, fontSize: 15, fontWeight: 500 }}>What I noticed</h2>
          {/* Say what this actually covers. It now follows the section tabs but
              still not the filter above -- each rule defines its own window and
              a seven-day filter would silence them rather than rescope them.
              The window comes from the backend so the label cannot drift. */}
          <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
            {section === "run" ? "Running" : "Lifting"} and recovery, {report?.window} — not
            scoped by the filter above. Nothing here changes your training.
          </div>

          <div className="coach-list" style={{ display: "grid", gap: 8, marginTop: 12 }}>
            {ordered.map(([rule, items]) => {
              const warn = items[0].severity === "warn";
              const open = expanded === rule;
              const tone = warn ? "var(--serious)" : "var(--text-muted)";
              return (
                <div key={rule}>
                  <button
                    onClick={() => setExpanded(open ? null : rule)}
                    aria-expanded={open}
                    style={{
                      display: "flex", alignItems: "center", gap: 10, width: "100%",
                      textAlign: "left", padding: "8px 12px", borderRadius: 12,
                      background: "var(--surface-raised)", borderLeft: `3px solid ${tone}`,
                    }}
                  >
                    <span aria-hidden style={{ color: tone, fontSize: 12 }}>{warn ? "▲" : "•"}</span>
                    <span
                      style={{
                        fontSize: 12, fontWeight: 600, color: "var(--text-secondary)",
                        flex: "0 0 auto", whiteSpace: "nowrap",
                      }}
                    >
                      {RULE_LABELS[rule] ?? rule.replace(/_/g, " ")}
                    </span>
                    <span
                      style={{
                        // min-width:0 is what lets the ellipsis engage: a flex
                        // item defaults to min-width:auto and will not shrink
                        // below its own content, pushing the row past the page.
                        flex: "1 1 auto", minWidth: 0,
                        fontSize: 13, color: "var(--text-primary)",
                        overflow: "hidden", textOverflow: "ellipsis",
                        whiteSpace: open ? "normal" : "nowrap",
                      }}
                    >
                      {items.length > 1 && !open
                        ? `${items.length} findings — ${items.map((i) => i.subject.replace(/_/g, " ")).join(", ")}`
                        : items[0].message}
                    </span>
                    <span style={{ color: "var(--text-muted)", fontSize: 11 }}>{open ? "−" : "+"}</span>
                  </button>

                  {open && (
                    <ul style={{ margin: "6px 0 0", padding: "0 0 0 30px", display: "grid", gap: 4 }}>
                      {items.map((item) => (
                        <li key={item.subject} style={{ fontSize: 13, color: "var(--text-secondary)" }}>
                          {item.message}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}

// --- chat dock -------------------------------------------------------------

type Message = { role: "user" | "assistant"; text: string };

/** Collapsed to a floating affordance, as in the reference. Expands into a
 *  side panel.
 *
 *  Deliberately *not* scoped to the section, though it used to say it was and
 *  to post a `section` the server accepted and never read. The dock holds the
 *  same tools whichever tab is open, so a scope in the request would have been
 *  a promise nothing kept. The prop survives as the fallback for `label`. */
export function ChatDock({
  section, label, question, onQuestionSent,
}: {
  /** Only names the placeholder -- "Ask about your run" -- when no `label`. */
  section: string;
  label?: string;
  /** A question composed elsewhere -- the goal cards' "Am I close?". Arrives
   *  already carrying the measured numbers, so the model explains a figure it
   *  was given rather than deriving one. */
  question?: string | null;
  onQuestionSent?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  // The dock is slower than the intake box: `answer` loops up to six turns and
  // each is a model request. Measured at 83s on DeepSeek for a goal question.
  // A static "Thinking..." over that long is indistinguishable from a hang.
  const [elapsed, setElapsed] = useState(0);

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

  const send = async () => {
    const question = draft.trim();
    if (!question || busy) return;
    setDraft("");
    setMessages((prev) => [...prev, { role: "user", text: question }]);
    setBusy(true);
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question }),
      });
      const body = await response.json();
      setMessages((prev) => [
        ...prev,
        { role: "assistant", text: body.reply ?? body.detail ?? "No answer." },
      ]);
    } catch (error) {
      setMessages((prev) => [...prev, { role: "assistant", text: `Failed: ${String(error)}` }]);
    } finally {
      setBusy(false);
    }
  };

  // One chat surface, driven from two places. A second dock for goal questions
  // would be the `ledger sync` mistake again: two entry points to one flow.
  useEffect(() => {
    if (!question) return;
    setOpen(true);
    setMessages((prev) => [...prev, { role: "user", text: question }]);
    setBusy(true);
    fetch("/api/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ question }),
    })
      .then((response) => response.json())
      .then((body) =>
        setMessages((prev) => [
          ...prev,
          { role: "assistant", text: body.reply ?? body.detail ?? "No answer." },
        ]),
      )
      .catch((error) =>
        setMessages((prev) => [...prev, { role: "assistant", text: `Failed: ${String(error)}` }]),
      )
      .finally(() => {
        setBusy(false);
        onQuestionSent?.();
      });
    // Only re-run when a new question arrives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [question]);

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        aria-label="Open assistant"
        style={{
          position: "fixed", right: 22, bottom: 22, width: 60, height: 60,
          borderRadius: 20, background: "var(--assistant)", color: "var(--assistant-ink)",
          display: "grid", placeItems: "center", boxShadow: "0 10px 28px rgba(0,0,0,0.45)", zIndex: 40,
        }}
      >
        <CoachMascot size={34} />
      </button>
    );
  }

  return (
    <aside
      style={{
        position: "fixed", right: 18, bottom: 18, width: 360, maxWidth: "calc(100vw - 36px)",
        height: 520, maxHeight: "calc(100vh - 36px)", background: "var(--surface)",
        border: "1px solid var(--border)", borderRadius: 24, zIndex: 40,
        display: "flex", flexDirection: "column", boxShadow: "0 18px 48px rgba(0,0,0,0.5)",
      }}
    >
      <header style={{ display: "flex", alignItems: "center", gap: 10, padding: "14px 16px", borderBottom: "1px solid var(--grid)" }}>
        <CoachMascot size={28} />
        <div style={{ flex: "1 1 auto" }}>
          <div style={{ fontSize: 14, fontWeight: 500 }}>Ask about your {label ?? section}</div>
          <div style={{ fontSize: 11, color: "var(--text-muted)" }}>Reads your cached data</div>
        </div>
        <button onClick={() => setOpen(false)} aria-label="Close assistant" style={{ color: "var(--text-muted)", fontSize: 18 }}>
          ×
        </button>
      </header>

      <div style={{ flex: "1 1 auto", overflow: "auto", padding: 14, display: "grid", gap: 10, alignContent: "start" }}>
        {messages.length === 0 && (
          <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
            Try “is my AEI improving?” or “what should I train next?”
          </div>
        )}
        {messages.map((message, index) => (
          <div
            key={index}
            style={{
              justifySelf: message.role === "user" ? "end" : "start",
              maxWidth: "85%", padding: "8px 12px", borderRadius: 14, fontSize: 13,
              background: message.role === "user" ? "var(--accent-soft)" : "var(--surface-raised)",
              color: "var(--text-primary)", whiteSpace: "pre-wrap",
            }}
          >
            {message.text}
          </div>
        ))}
        {busy && (
          <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
            Thinking… {elapsed}s
            {elapsed >= 15 && (
              <div style={{ fontSize: 11, marginTop: 4 }}>
                Answering can take a minute or more: each tool the model calls is
                another request.
              </div>
            )}
          </div>
        )}
      </div>

      <div style={{ display: "flex", gap: 8, padding: 12, borderTop: "1px solid var(--grid)" }}>
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && send()}
          placeholder="Ask a question"
          aria-label="Ask a question"
          style={{
            flex: "1 1 auto", background: "var(--surface-raised)", color: "var(--text-primary)",
            border: "1px solid var(--border)", borderRadius: 12, padding: "9px 12px", fontSize: 13,
          }}
        />
        <button
          onClick={send}
          disabled={busy}
          style={{
            background: "var(--assistant)", color: "var(--assistant-ink)", borderRadius: 12,
            padding: "0 16px", fontSize: 13, fontWeight: 600,
          }}
        >
          Send
        </button>
      </div>
    </aside>
  );
}
