# Volume and progression

## Counting sets

```
volume[muscle] = sets where the muscle is the main mover
               + 0.5 × sets where it's a secondary mover

frequency[muscle] = how many separate days in the window worked it
```

Settings live in `.env`:

| Setting | Default | Meaning |
|---|---|---|
| `SECONDARY_WEIGHT` | `0.5` | Credit when the muscle is a secondary mover |
| `COUNT_WARMUP_SETS` | `false` | Whether warmups count |
| `LOCAL_UTC_OFFSET_MINUTES` | `120` in code, `0` in `.env.example` | Which day a late-evening session lands on. Set it to your own offset — the two defaults disagree, and the copied file wins |
| `WEEK_STARTS_ON` | `0` | 0 = Monday |
| `REP_RANGE_LOW` / `HIGH` | `6` / `10` | Default rep range |

Decisions already made:

- **Warmups don't count.** One real session logged 27 sets, but only 16 were
  working sets.
- **An exercise that lists a muscle as both main and secondary counts it once.**
- **Targets scale to the window.** Four weeks of training is compared against
  four weeks of target.
- **Unknown exercises are reported, not dropped.** If a set's exercise isn't in
  the local catalog it shows up under "unmapped exercises".

## Two kinds of time window

These are not interchangeable, and mixing them once caused a real bug.

- **`last-N-weeks` counts only finished weeks.** It ignores the current
  part-done week. Use it for comparisons against a baseline — otherwise a rule
  measuring against a half-finished week would never fire.
- **`last-N-days` includes today.** Use it for "what happened recently" panels.

Once, a dashboard panel used week windows and so hid the last five days. It
looked like a broken sync. `tests/test_recency.py` covers both.

## Adding weight (double progression)

For each exercise the app tracks the working weight, the reps you hit at it, and
whether every set reached the top of the rep range. When they all do, it
suggests going up. Steps depend on equipment: barbell 2.5 kg, dumbbell 2 kg,
machine 5 kg.

**Rep ranges are a setting, not a guess.** A logged set tells you what you did,
not what you were aiming for — a heavy top set followed by a lighter back-off
set looks identical to a failed attempt at the range. So only sets at the
session's heaviest weight count towards the decision, and lighter follow-up
sets ("back-off" sets) never hold you back.

Rep ranges are set per exercise from the dashboard, or over the API:

```bash
curl -X PUT localhost:8000/api/rep-ranges \
  -H 'content-type: application/json' \
  -d '{"exercise_template_id": "BENCH", "rep_low": 4, "rep_high": 6}'
```

## Warning rules

These run when you ask (`insights`, `GET /api/insights`, or the dashboard).
They only report; they never change anything.

| Rule | Fires when |
|---|---|
| `volume_drop` | A muscle group is >25% below its 4-week average |
| `coverage_gap` | Below the frequency target two weeks running |
| `stall` | No extra weight or reps on a main lift across 3 sessions |
| `progression_ready` | Every working set hit the top of the rep range |
| `running_shortfall` | More than 10% under your weekly distance target |
| `aei_trend` | Running efficiency moved 3% or more across 3 runs, either way |
| `recovery_flag` | 3-night sleep average more than 30 min below your 28-night baseline |

Coverage gaps are graded. A muscle you trained and then stopped is a warning; a
muscle that never appears is just information. Without that split the panel
fills with identical complaints and people stop reading it.

`recovery_flag` needs at least 7 of the last 28 nights recorded before it
will fire, so a sparse week can't trigger it.

The recovery rule never tells you what to do. It reports the pattern from your
own history and stops there. A test checks the output contains no instructions.

`drift` — comparing what you planned against what you did — isn't written yet.
It needed plans and availability to exist, which they now do.

## Checked by hand

When this was first written the maths was checked against a real session small
enough to do on paper: 27 logged sets, 11 warmups, 16 working.

| Muscle | Reported | By hand |
|---|---|---|
| chest | 2.0 | 2 main (chest press) |
| shoulders | 5.0 | 4 main (press + raises) + 0.5×2 secondary |
| triceps | 4.0 | 2 main + 0.5×2 + 0.5×2 secondary |
| lats | 3.0 | 2 main + 0.5×2 secondary |
| upper_back | 3.0 | 2 main + 0.5×2 secondary |
| biceps | 6.0 | 4 main + 0.5×2 + 0.5×2 secondary |
| forearms | 2.0 | 0.5×4 secondary |

That was a one-off manual check, not a test in the suite. Any change to the
volume maths should come with one like it: numbers you can work out yourself, no
database, no network.
