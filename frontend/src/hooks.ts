/** Shared React hooks.
 *
 * Kept out of `components/` because these are behaviour, not chrome, and a
 * screen should be able to reach one without importing a component module.
 */

import { useEffect, useState } from "react";

/** Seconds since `active` last became true; 0 whenever it is false.
 *
 * Every slow surface in this app needs one, and for the same reason: model
 * latency here is measured in tens of seconds and occasionally over a minute,
 * and a static "Reading…" over that long is indistinguishable from a hang.
 * That is not a hypothetical — a stopped server was first reported as "it's
 * stuck", and the report was right twice over.
 *
 * This was copy-pasted three times, identical but for the flag it watched, so
 * a fix to the counter reached whichever copy the reader happened to open.
 */
export function useElapsedSeconds(active: boolean): number {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!active) return;
    // Measured against a start stamp rather than incremented, so a throttled
    // background tab reports the true wait instead of the number of ticks it
    // was awake for.
    const started = Date.now();
    setElapsed(0);
    const timer = window.setInterval(
      () => setElapsed(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [active]);

  return elapsed;
}
