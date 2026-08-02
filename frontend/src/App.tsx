/** Shell: two sections, one filter row scoping everything below it. */

import { useCallback, useState } from "react";
import type { Range } from "./api";
import { CoachStrip, ChatDock } from "./components/Coach";
import {
  SectionTabs, SyncControl, ThemeControl, TimeHorizonFilter, type Section,
} from "./components/shell";
import { GymScreen } from "./screens/GymScreen";
import { RunScreen } from "./screens/RunScreen";
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

      {/* One filter row above everything it scopes -- never per-chart filters. */}
      <div className="filterbar">
        <TimeHorizonFilter range={range} onChange={setRange} />
      </div>

      <main className="stack">
        {section === "run" ? (
          <RunScreen range={range} reloadKey={reloadKey} />
        ) : (
          <GymScreen range={range} reloadKey={reloadKey} />
        )}
        <CoachStrip reloadKey={reloadKey} />
      </main>

      <ChatDock section={section} />
    </div>
  );
}
