/** Shell: two sections, one filter row scoping everything below it. */

import { useCallback, useState } from "react";
import type { Range } from "./api";
import { CoachStrip, ChatDock } from "./components/Coach";
import {
  SectionTabs, SyncControl, ThemeControl, TimeHorizonFilter, type Section,
} from "./components/shell";
import { GymScreen } from "./screens/GymScreen";
import { RunScreen } from "./screens/RunScreen";
import { WeekScreen } from "./screens/WeekScreen";
import "./app.css";

export function App() {
  const [section, setSection] = useState<Section>("run");
  const [range, setRange] = useState<Range>({ window: "last-90-days" });
  const [reloadKey, setReloadKey] = useState(0);

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
      {section !== "week" && (
        <div className="filterbar">
          <TimeHorizonFilter range={range} onChange={setRange} />
        </div>
      )}

      <main className="stack">
        {section === "run" && <RunScreen range={range} reloadKey={reloadKey} />}
        {section === "gym" && <GymScreen range={range} reloadKey={reloadKey} />}
        {section === "week" && <WeekScreen reloadKey={reloadKey} />}

        {/* Below the screen but scoped to it: the rules that apply to lifting
            are not the ones that apply to running. Absent on Week, where the
            plan's own rationale and trade-offs already are the coach talking --
            a strip of detection rules underneath would be a second, quieter
            voice saying something different. */}
        {section !== "week" && <CoachStrip section={section} reloadKey={reloadKey} />}
      </main>

      <ChatDock section={section} />
    </div>
  );
}
