# Aerobic Efficiency Index

One number for whether your running is improving: **grade-adjusted metres per
heartbeat**. Higher is better.

Running uphill costs more effort than the same distance on the flat, so raw
pace makes a hilly run look worse than it was. AEI adjusts the distance for
gradient first, then divides by how many heartbeats it took.

```
g        = gradient as a decimal fraction (0.05 = a 5% climb)
cost(g)  = 155.4g⁵ − 30.4g⁴ − 43.3g³ + 46.3g² + 19.5g + 3.6   (Minetti)
adjusted = Σ segment_distance × cost(g)/cost(0)
beats    = Σ heart_rate × minutes
AEI      = adjusted_metres / beats
```

Values land a little above or below 1. The pinned reference run in
`tests/test_aei.py` scores 1.3251.

AEI removes the effect of hills. It does **not** remove the effect of distance,
so only compare runs of similar length.

## Gradient is averaged over 25 m chunks

GPS altitude is noisy. Taken sample by sample it put the 95th-percentile
gradient at 41% on a flat run. That noise doesn't cancel out, because climbing
costs more than descending saves — so random error pushes the result *up*.
Averaging over 25 m cut the error from 23% to 10%.

## Stored values record which method produced them

That chunking choice changes AEI by about 10%, so a number calculated one way
can't be compared to one calculated the other way. Every stored value carries a
`method_version`. If the method changes, values are recalculated from the saved
25 m chunks — no need to re-download 1.2 MB of GPS per run.

Change a constant, bump the version.

## Runs that get excluded

Some runs don't have usable data, and those are excluded with a reason given —
a 2-second accidental start, or a run the watch summarised as 936 m whose GPS
track only covered 66 m.

**Heart rate only appears on about 40% of track points**, roughly every 2.5
seconds against 1-per-second GPS. That's how the watch samples; it isn't a
broken export. Any code adding up time has to measure from the last point that
*had* a heart rate, not the previous point.

Getting that wrong caused the one real bug here. Fetching a run fresh counted
only the gap to the previous point, while recalculating from stored chunks
didn't — two different definitions of "beats" under the same `method_version`,
undercounting by about 59%.

A test pins a real run so a method change can't slip through unnoticed.
