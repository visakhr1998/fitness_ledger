# How the numbers work

The ledger measures two things: **effective sets per muscle group** for lifting,
and **metres per heartbeat** for running. This page explains how each number is
calculated.

## Counting sets

```
volume[muscle]    = sets where the muscle is the primary mover
                  + 0.5 × sets where it is a secondary mover

frequency[muscle] = number of separate days in the window that trained it
```

The following rules apply:

- Warm-up sets are excluded. In one real session, 27 logged sets included only
  16 working sets.
- If an exercise lists a muscle as both primary and secondary, it is counted
  once.
- Targets scale to the window. Four weeks of training is compared with four
  weeks of target.
- Sets for exercises that are not in the local catalog are reported under
  "unmapped exercises" rather than dropped.

These settings can be changed in `.env`:

| Setting | Default | Meaning |
|---|---|---|
| `SECONDARY_WEIGHT` | `0.5` | Credit for a secondary mover |
| `COUNT_WARMUP_SETS` | `false` | Whether warm-up sets count |
| `LOCAL_UTC_OFFSET_MINUTES` | `120` | Which day a late-evening session belongs to |
| `WEEK_STARTS_ON` | `0` | First day of the week (0 = Monday) |
| `REP_RANGE_LOW` / `REP_RANGE_HIGH` | `6` / `10` | Default rep range for progression |

### Muscle groups and targets

The sixteen muscle groups are chest, lats, upper_back, shoulders, quadriceps,
hamstrings, glutes, biceps, triceps, abdominals, calves, traps, lower_back,
forearms, abductors and adductors.

Each has a weekly target in sets and in training days. Targets are always stored
per week; the Gym screen scales them to the period you are viewing. To change
them, use **Edit weekly targets** on the Gym screen or `ledger targets --set
chest=16`.

## Time windows

There are two kinds of window, and they are not interchangeable:

| Window | Includes | Use for |
|---|---|---|
| `last-N-weeks` | N completed weeks, excluding the current one | Comparisons with a baseline |
| `last-N-days` | The last N days, including today | Recent activity |

A rule that compared against a partly finished week would rarely fire, which is
why baselines use completed weeks. Recent-activity panels use days so that the
latest sessions are not hidden.

## Progression

For each exercise, the ledger tracks the working weight, the reps achieved at
that weight, and whether every set reached the top of the rep range. When they
all do, it suggests adding weight. The increment depends on the equipment:

| Equipment | Increment |
|---|---|
| Barbell, plate-loaded | 2.5 kg |
| Dumbbell | 2 kg |
| Kettlebell | 4 kg |
| Machine | 5 kg |
| Bodyweight, band, suspension | none |

Rep ranges are configured, not inferred. A logged set records what you did, not
what you intended, and a heavy top set followed by a lighter back-off set looks
the same as a failed attempt. For that reason only sets at the session's
heaviest weight count towards the decision. The default range comes from
`.env`; a per-exercise range can be set through `PUT /api/rep-ranges` (there is
no screen for this yet).

## Goal progress

Progress for every goal is calculated by the ledger, not by the model. The chat
box can explain a figure but does not produce it.

| Goal type | Measured as |
|---|---|
| One-rep max | Estimated 1RM (Epley formula) of the best set in your latest session of that lift, within the last 12 weeks |
| Reps | Most reps in one working set in your latest session of that exercise, within the last 12 weeks, at any load (the load is shown) |
| Weekly distance | Kilometres run in the last completed week |
| Running efficiency | Latest AEI from a run that passed the reliability checks, within the last 12 weeks |
| Sessions per week | Workouts per week over the last 4 completed weeks |
| Race time | Not measured yet, because it requires a pace model |

A goal that cannot be measured is shown without a progress bar, so that it is
not confused with a goal where no progress has been made.

## Planning a week

The planner chooses exercises and days. All numbers in a plan are calculated
as follows:

- **Sets** come from each muscle's weekly target, divided evenly between the
  exercises chosen for that muscle. An exercise that serves two muscles gets
  the larger of the two shares, not their sum.
- **Limits**: each exercise gets 2 to 4 sets, and a session has at most 24.
  Volume that does not fit is listed in the plan as still short.
- **Returning from a break**: two or more weeks without lifting, following a
  period of training, count as a break. The first week back uses 50% of the
  target after four or more weeks off, or 70% after two or three, and the
  target returns to 100% over the following weeks.
- **Runs** divide your weekly distance target evenly between the chosen days.

Before a plan is shown, it is checked against these rules:

- Sessions fall only on days you can train.
- At least one rest day separates sessions that train the same muscle.
- No run is scheduled the day after a session that trained legs.
- Weekly rules from the Goals screen are respected (for example, no running on
  Wednesdays).

A plan that breaks a rule is sent back to the planner with the reason and
redrafted, up to three attempts in total (`COACH_MAX_PLAN_ATTEMPTS`). If every attempt breaks a rule, the attempt with
the fewest problems is shown with those problems listed. The limits are stored
in the database's `user_settings` table (`max_sets_per_session`,
`min_rest_days_same_muscle`, `allow_run_after_leg_day` and others); there is no
screen or command for them yet.

## Warning rules

The warning rules run when you open the dashboard or run `ledger insights`.
They only report findings.

| Rule | Fires when |
|---|---|
| `volume_drop` | A muscle group is more than 25% below its 4-week average |
| `coverage_gap` | A muscle group is below its frequency target for two weeks in a row |
| `stall` | A main lift gains no weight or reps over 3 sessions |
| `progression_ready` | Every working set reached the top of the rep range |
| `running_shortfall` | Weekly distance is more than 10% below target |
| `aei_trend` | Running efficiency changes by 3% or more over 3 runs, in either direction |
| `recovery_flag` | The 3-night sleep average is more than 30 minutes below your 28-night baseline, with at least 7 of those nights recorded |

A coverage gap for a muscle you trained and then stopped is shown as a warning.
A gap for a muscle you have never trained is shown as information.

The recovery rule reports a pattern in your own data. It does not tell you what
to do about it.

## Running efficiency

The **Aerobic Efficiency Index (AEI)** is grade-adjusted metres per heartbeat.
Higher is better.

Running uphill costs more energy than running the same distance on the flat, so
raw pace understates a hilly run. AEI first adjusts the distance for gradient,
then divides it by the number of heartbeats the run took.

Most runs score close to 1. The trend over a month matters more than any single
value. Keep the following in mind when comparing runs:

- **Distance is not adjusted.** Compare runs of similar length.
- **The hill adjustment is large.** At the same heart rate, a stretch with 22 m
  of climbing over 670 m scores 29% higher than the same stretch on the flat,
  and doubling the climbing adds a further 15%. Trends are most reliable on a
  route you repeat.

### How gradient is measured

Gradient is averaged over 25 m segments rather than taken from each GPS sample.
Per-sample altitude is noisy enough to show a 41% gradient on a flat run, and
because climbing costs more than descending saves, that noise inflates the
result rather than cancelling out. Averaging over 25 m reduced the error from
23% to 10%.

Because the method changes the result by about 10%, each stored value records
the method version that produced it. When the method changes, values are
recalculated from the stored segments without downloading GPS data again.

### Excluded runs

Runs the data cannot support are excluded, with the reason shown. Examples
include a 2-second accidental recording, or a run the watch summarised as 936 m
whose GPS track covered only 66 m.

Heart rate is present on about 40% of track points (about every 2.5 seconds,
against one GPS point per second). This is the watch's sampling rate, not a
fault in the export.

### Formula

```
g        = gradient as a decimal (0.05 = a 5% climb)
cost(g)  = 155.4g⁵ − 30.4g⁴ − 43.3g³ + 46.3g² + 19.5g + 3.6
adjusted = Σ segment_distance × cost(g) / cost(0)
beats    = Σ heart_rate × minutes
AEI      = adjusted_metres / beats
```

`cost(g)` is Minetti's equation for the energy cost of running at a given
gradient, normalised so that flat ground equals 1.
