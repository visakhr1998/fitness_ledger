/** The x-axis scale.
 *
 * Run dates are irregular. Spacing points by array index draws a twelve-day gap
 * and a two-day gap the same width, which is the one thing a time series exists
 * to show (#13). These pin the scale itself, so it can be checked without a
 * browser measuring anything.
 */

import { describe, expect, it } from "vitest";

import { ticks, xFractions } from "./primitives";

describe("xFractions", () => {
  it("spaces points evenly by index when no date scale is asked for", () => {
    expect(xFractions(["2026-05-01", "2026-05-03", "2026-06-14"])).toEqual([0, 0.5, 1]);
  });

  it("spaces points by elapsed time when the date scale is on", () => {
    // 2 days, then 6: the second gap should draw three times wider.
    const [a, b, c] = xFractions(["2026-05-01", "2026-05-03", "2026-05-09"], true);
    expect(a).toBe(0);
    expect(c).toBe(1);
    expect(b).toBeCloseTo(0.25, 6);
    expect(c - b).toBeCloseTo(3 * (b - a), 6);
  });

  it("puts the first and last point at the ends either way", () => {
    const dated = xFractions(["2026-01-01", "2026-03-05", "2026-12-31"], true);
    expect(dated[0]).toBe(0);
    expect(dated[dated.length - 1]).toBe(1);
  });

  it("falls back to index spacing when every point is the same day", () => {
    // Two runs on one date would otherwise divide by a zero span.
    expect(xFractions(["2026-05-01", "2026-05-01"], true)).toEqual([0, 1]);
  });

  it("falls back to index spacing when a label is not a date", () => {
    expect(xFractions(["week 1", "week 2", "week 3"], true)).toEqual([0, 0.5, 1]);
  });

  it("handles a single point without dividing by zero", () => {
    expect(xFractions(["2026-05-01"], true)).toEqual([0]);
    expect(xFractions([], true)).toEqual([]);
  });
});

describe("ticks", () => {
  it("covers the range with round steps", () => {
    expect(ticks(10)).toEqual([0, 2.5, 5, 7.5, 10]);
    expect(ticks(0)).toEqual([0]);
  });
});
