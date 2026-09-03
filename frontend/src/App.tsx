/** Shell: two sections, one filter row scoping everything below it. */

import { useCallback, useState } from "react";
import type { Range } from "./api";
import { CoachStrip, ChatDock } from "./components/Coach";
import {
  SectionTabs, SyncControl, ThemeControl, TimeHorizonFilter, type Section,
} from "./components/shell";
import { GymScreen } from "./screens/GymScreen";
import { HomeScreen } from "./screens/HomeScreen";
import { RunScreen } from "./screens/RunScreen";
import { WeekScreen } from "./screens/WeekScreen";
import "./app.css";

export function App() {
  // Opens on Goals: the other three screens are all measured against what is
  // set here, and it is the only one that works on a fresh clone with no data.
  const [section, setSection] = useState<Section>("home");
  const [range, setRange] = useState<Range>({ window: "last-90-days" });
  const [reloadKey, setReloadKey] = useState(0);
  // A question composed by a goal card, on its way to the one chat dock.
  const [question, setQuestion] = useState<string | null>(null);

  const reload = useCallback(() => setReloadKey((value) => value + 1), []);

  return (
    <div className="wrap">
      <header className="topbar">
        <SectionTabs active={section} onChange={setSection} />
        <span className="spacer" />
        <SyncControl onDone={reload} />
        <ThemeControl />
      </header>

      {/* One filter row above everything it scopes -- never per-chart filters.
          Hidden on Week: a plan is one specific week, so a filter over it says
          nothing, and leaving a dead control on screen is worse than no
          control. Same reasoning that keeps the coach strip unfiltered. */}
      {section !== "week" && section !== "home" && (
        <div className="filterbar">
          <TimeHorizonFilter range={range} onChange={setRange} />
        </div>
      )}

      <main className="stack">
        {section === "home" && <HomeScreen reloadKey={reloadKey} />}
        {section === "run" && (
          <RunScreen range={range} reloadKey={reloadKey} onAsk={setQuestion} />
        )}
        {section === "gym" && (
          <GymScreen range={range} reloadKey={reloadKey} onAsk={setQuestion} />
        )}
        {section === "week" && <WeekScreen reloadKey={reloadKey} />}

        {/* Below the screen but scoped to it: the rules that apply to lifting
            are not the ones that apply to running. Absent on Week, where the
            plan's own rationale and trade-offs already are the coach talking --
            a strip of detection rules underneath would be a second, quieter
            voice saying something different. */}
        {section !== "week" && section !== "home" && (
          <CoachStrip section={section} reloadKey={reloadKey} />
        )}
      </main>

      {/* The dock reads cached training data whichever screen you are on, so it
          stays on Goals — only its heading needs a word that reads as English. */}
      <ChatDock
        section={section}
        label={section === "home" ? "training" : section}
        question={question}
        onQuestionSent={() => setQuestion(null)}
      />
    </div>
  );
}
