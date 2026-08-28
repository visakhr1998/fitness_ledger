# How the numbers work

Two things get counted: **sets per muscle group** for lifting, and
**metres per heartbeat** for running.

## Counting sets

```
volume[muscle] = sets where the muscle is the main mover
               + 0.5 × sets where it's a secondary mover

frequency[muscle] = how many separate days in the window worked it
```

Settings, all in `.env`:

| Setting | Default | Meaning |
|---|---|---|
| `SECONDARY_WEIGHT` | `0.5` | Credit when the muscle is a secondary mover |
| `COUNT_WARMUP_SETS` | `false` | Whether warmups count |
| `LOCAL_UTC_OFFSET_MINUTES` | `120` | Which day a late-evening session lands on |
| `WEEK_STARTS_ON` | `0` | 0 = Monday |
| `REP_RANGE_LOW` / `HIGH` | `6` / `10` | Default rep range |

Decisions already made:

- **Warmups don't count.** One real session logged 27 sets, of which 16 were
  working sets.
- **An exercise listing a muscle as both main and secondary counts it once.**
- **Targets scale to the window.** Four weeks of training is compared against
  four weeks of target.
- **Unknown exercises are reported, not dropped.** A set whose exercise isn't in
  the local catalog shows up under "unmapped exercises".

The sixteen muscle groups: chest, lats, upper_back, shoulders, quadriceps,
hamstrings, glutes, biceps, triceps, abdominals, calves, traps, lower_back,
forearms, abductors, adductors. `ledger targets` lists them with your current
numbers.

## Two kinds of time window

Not interchangeable, and mixing them once caused a real bug.

- **`last-N-weeks` counts only finished weeks**, ignoring the current part-done
  one. Use it for comparisons against a baseline — a rule measuring against a
  half-finished week would never fire.
- **`last-N-days` includes today.** Use it for "what happened recently".

A dashboard panel once used week windows and so hid the last five days. It
looked exactly like a broken sync.

## Adding weight

For each exercise the app tracks the working weight, the reps you hit at it, and
whether every set reached the top of the rep range. When they all do, it
suggests going up. Steps come from the equipment: barbell 2.5 kg, dumbbell 2 kg,
machine 5 kg.

**Rep ranges are a setting, not a guess.** A logged set tells you what you did,
not what you were aiming for — a heavy top set followed by a lighter back-off
set looks identical to a failed attempt at the range. So only sets at the
session's heaviest weight count towards the decision, and lighter follow-up sets
never hold you back. Change the range per exercise from the dashboard.

## Warning rules

These run when you ask — `ledger insights`, or the dashboard. They only report.

| Rule | Fires when |
|---|---|
| `volume_drop` | A muscle group is >25% below its 4-week average |
| `coverage_gap` | Below the frequency target two weeks running |
| `stall` | No extra weight or reps on a main lift across 3 sessions |
| `progression_ready` | Every working set hit the top of the rep range |
| `running_shortfall` | More than 10% under your weekly distance target |
| `aei_trend` | Running efficiency moved 3% or more across 3 runs, either way |
| `recovery_flag` | 3-night sleep average more than 30 min below your 28-night baseline, with at least 7 of those 28 nights recorded |

Coverage gaps are graded: a muscle you trained and then stopped is a warning, a
muscle that never appears is information. Without that split the panel fills
with identical complaints and stops being read.

The recovery rule never tells you what to do. It reports the pattern from your
own history and stops.

## Running efficiency

**Aerobic Efficiency Index — grade-adjusted metres per heartbeat.** Higher is
better.

Running uphill costs more than the same distance flat, so raw pace makes a hilly
run look worse than it was. AEI adjusts distance for gradient first, then divides
by the heartbeats it took.

Most runs land somewhere near 1. What matters is the direction over a month, not
the number. AEI removes the effect of hills; it does **not** remove the effect of
distance, so only compare runs of similar length.

Gradient is averaged over **25 m chunks**, never per GPS sample. Sample-by-sample
altitude noise put the 95th-percentile gradient at 41% on a flat run — and that
error doesn't cancel out, because climbing costs more than descending saves, so
random noise pushes the result *up*. Averaging cut the error from 23% to 10%.

Because that choice moves the number by about 10%, a value calculated one way
can't be compared with one calculated the other. Every stored value records the
method that produced it, and changing the method recalculates from the saved
chunks rather than re-downloading GPS.

Runs the data can't support are excluded with a reason — a 2-second accidental
start, or a run the watch summarised as 936 m whose GPS track covered 66 m.

**Heart rate only appears on about 40% of track points**, roughly every 2.5
seconds against 1-per-second GPS. That's the watch's sampling rate, not a broken
export. Anything summing over time must measure from the last point that *had* a
heart rate, not the previous point.

<details>
<summary>The gradient formula, if you want it</summary>

```
g        = gradient as a decimal fraction (0.05 = a 5% climb)
cost(g)  = 155.4g⁵ − 30.4g⁴ − 43.3g³ + 46.3g² + 19.5g + 3.6
adjusted = Σ segment_distance × cost(g)/cost(0)
beats    = Σ heart_rate × minutes
AEI      = adjusted_metres / beats
```

`cost(g)` is Minetti's equation for the energy cost of running at a given
gradient, normalised so flat ground is 1.
</details>
