/** What the user reads before saving a goal.
 *
 * These strings are the confirm step. If they are wrong the user approves
 * something other than what they said, which is the one failure the
 * propose → confirm shape exists to prevent — so they are pinned here rather
 * than checked by eye.
 */

import { describe, expect, it } from "vitest";
import { clock, describeConstraint, describeGoal } from "./goalText";

describe("clock", () => {
  it("renders a marathon target as hours, not seconds", () => {
    // Storage keeps seconds because that is what arithmetic needs. Nobody can
    // confirm "14400" is the goal they meant.
    expect(clock(14400)).toBe("4:00:00");
  });

  it("drops the hour when there isn't one", () => {
    expect(clock(1320)).toBe("22:00");
    expect(clock(1325)).toBe("22:05");
  });

  it("pads minutes and seconds so a time never reads as a decimal", () => {
    expect(clock(3605)).toBe("1:00:05");
    expect(clock(3661)).toBe("1:01:01");
  });
});

describe("describeGoal", () => {
  it("describes a race goal by distance and clock time", () => {
    expect(
      describeGoal({ type: "race_time", subject: "marathon", target_value: 14400 }),
    ).toBe("marathon in 4:00:00");
  });

  it("unslugs the distance so it reads as English", () => {
    expect(
      describeGoal({ type: "half_marathon" as string, subject: null, target_value: 0 }),
    ).toContain("half_marathon");
    expect(
      describeGoal({ type: "race_time", subject: "half_marathon", target_value: 7200 }),
    ).toBe("half marathon in 2:00:00");
  });

  it("names the lift for a strength goal", () => {
    expect(
      describeGoal({ type: "strength_1rm", subject: "Bench Press", target_value: 100 }),
    ).toBe("Bench Press one-rep max of 100 kg");
  });

  it("falls back rather than rendering blank for a type it does not know", () => {
    // A goal type added on the server must not silently disappear from the UI.
    expect(
      describeGoal({ type: "future_type", subject: "thing", target_value: 5 }),
    ).toBe("future_type thing 5");
  });
});

describe("describeConstraint", () => {
  it("says which day and what is ruled out", () => {
    expect(
      describeConstraint({ weekday: 2, kind: "no_high_impact", reason: "knee" }),
    ).toBe("Wednesdays: no running or jumping (knee)");
  });

  it("omits the reason when there isn't one", () => {
    expect(describeConstraint({ weekday: 0, kind: "no_lifting", reason: null })).toBe(
      "Mondays: no lifting",
    );
  });

  it("counts weekdays from Monday, matching the backend", () => {
    // date.weekday() is 0=Monday on the Python side. An off-by-one here would
    // silently move every constraint a day.
    expect(describeConstraint({ weekday: 6, kind: "no_intervals", reason: null })).toBe(
      "Sundays: easy running only",
    );
  });
});
