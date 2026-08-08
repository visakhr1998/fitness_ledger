"""Turning a proposed week into a planned one.

**The agent never emits a set count.** It chooses exercises and days; every
number in the finished plan is computed here, from the deficit the rules engine
reported. That is the guarantee the whole coach design rests on, and it is
structural rather than asked-for: `WeekProposal` has nowhere to put a set count,
so there is nothing for this module to trust.

The original spec forbade the agent from adding up sets while also giving the
strength planner "set allocation" -- splitting 12 deficit sets across two
sessions *is* arithmetic. Allocation lives here instead. The cost, accepted
knowingly: the agent cannot weight a session for a reason the deficit does not
capture. Revisit only if that proves limiting.

Pure, like the rest of the rules engine: dataclasses in, dataclasses out, no
repository, no network, no model. It sits next to volume.py rather than inside
coach/ for exactly that reason -- coach/ imports the database, and this must
stay testable without one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .models import PlannedExercise, PlannedSession

# An exercise is worth doing properly or not at all: one set of something is
# almost never what was intended, and a plan full of singles reads as noise.
MIN_SETS_PER_EXERCISE = 2

# Above this, added sets stop buying much and start eating the session. It also
# stops one badly neglected muscle from swallowing a whole day.
MAX_SETS_PER_EXERCISE = 6

# A ceiling on the day, not a target. Sessions are trimmed to fit it and what
# could not fit is reported rather than silently dropped.
MAX_SETS_PER_SESSION = 24


@dataclass(frozen=True)
class Preferences:
    """Hard constraints the assembled week must respect.

    Stored in `user_settings` and read at the edge; defaults here are what a
    week looks like when the user has said nothing.
    """

    max_sets_per_session: int = MAX_SETS_PER_SESSION
    min_sets_per_exercise: int = MIN_SETS_PER_EXERCISE
    max_sets_per_exercise: int = MAX_SETS_PER_EXERCISE
    # Days between two sessions training the same muscle group. 1 means
    # back-to-back days are a violation; 0 disables the check.
    min_rest_days_same_muscle: int = 1
    # Whether a run may sit on the day after a session that trained legs.
    allow_run_after_leg_day: bool = True


LEG_MUSCLES = frozenset({"quadriceps", "hamstrings", "glutes", "calves"})


@dataclass(frozen=True)
class Allocation:
    """The assembled sessions, and what the week could not fit.

    `unmet` is the honest half. A week has a finite number of days and a finite
    number of sets in a day, so a large deficit routinely cannot be closed --
    reporting which muscles are still short is what lets the trade-off be
    stated rather than discovered later.
    """

    sessions: tuple[PlannedSession, ...] = ()
    unmet: dict[str, float] = field(default_factory=dict)
    unplaced: tuple[str, ...] = ()  # muscles short with no exercise serving them

    @property
    def total_sets(self) -> int:
        return sum(session.total_sets for session in self.sessions)


def allocate(
    proposal_sessions: list[dict],
    deficits: dict[str, float],
    preferences: Preferences | None = None,
) -> Allocation:
    """Compute set counts for a proposed week.

    `proposal_sessions` is the agent's output, as dicts: each has a date, a
    kind, and for lifting sessions a list of exercises carrying an id, a title
    and the muscle groups it is there to serve. No set counts -- there is no
    field for one.

    The rule is one line: **an exercise gets the sets its neediest target
    needs, shared with whatever else serves that target.** Concretely, a muscle
    short 9 sets across three exercises gives each of them 3; an exercise
    serving two muscles takes the larger of its two shares rather than the sum,
    because one set of a press serves chest and triceps at once and adding them
    would count the same set twice.
    """
    prefs = preferences or Preferences()
    short = {m: d for m, d in deficits.items() if d and d > 0}

    lift_sessions = [s for s in proposal_sessions if s.get("kind") != "run"]
    servers = _exercises_by_muscle(lift_sessions)

    # Each muscle's deficit, split evenly across the exercises chosen for it.
    share: dict[tuple[int, int], float] = {}
    unplaced = []
    for muscle, deficit in sorted(short.items()):
        serving = servers.get(muscle, [])
        if not serving:
            unplaced.append(muscle)
            continue
        per = deficit / len(serving)
        for key in serving:
            share[key] = max(share.get(key, 0.0), per)

    sessions, delivered = _build_sessions(proposal_sessions, share, prefs)

    unmet = {}
    for muscle, deficit in sorted(short.items()):
        remaining = deficit - delivered.get(muscle, 0.0)
        if remaining > 0.5:  # sub-half-set remainders are rounding, not a gap
            unmet[muscle] = round(remaining, 1)

    return Allocation(sessions=tuple(sessions), unmet=unmet, unplaced=tuple(unplaced))


def _exercises_by_muscle(lift_sessions: list[dict]) -> dict[str, list[tuple[int, int]]]:
    """Muscle -> the (session, exercise) positions that serve it."""
    servers: dict[str, list[tuple[int, int]]] = {}
    for si, session in enumerate(lift_sessions):
        for ei, exercise in enumerate(session.get("exercises") or []):
            for muscle in exercise.get("targets") or []:
                servers.setdefault(muscle, []).append((si, ei))
    return servers


def _build_sessions(
    proposal_sessions: list[dict], share: dict[tuple[int, int], float], prefs: Preferences
) -> tuple[list[PlannedSession], dict[str, float]]:
    """Round shares into whole sets, hold the per-session ceiling, and report
    how much volume each muscle actually receives."""
    delivered: dict[str, float] = {}
    built: list[PlannedSession] = []
    lift_index = -1

    for session in proposal_sessions:
        kind = "run" if session.get("kind") == "run" else "lift"
        day = date.fromisoformat(session["session_date"])

        if kind == "run":
            built.append(
                PlannedSession(
                    local_date=day,
                    kind="run",
                    focus=session.get("focus", ""),
                    distance_km=session.get("distance_km"),
                )
            )
            continue

        lift_index += 1
        counts = []
        for ei, exercise in enumerate(session.get("exercises") or []):
            raw = share.get((lift_index, ei), 0.0)
            counts.append(_clamp_sets(raw, prefs))

        counts = _fit_session(counts, prefs.max_sets_per_session)

        exercises = tuple(
            PlannedExercise(
                exercise_template_id=exercise["exercise_template_id"],
                title=exercise.get("title", exercise["exercise_template_id"]),
                sets=count,
                targets=tuple(exercise.get("targets") or []),
            )
            for exercise, count in zip(session.get("exercises") or [], counts)
            if count > 0
        )
        for exercise in exercises:
            for muscle in exercise.targets:
                delivered[muscle] = delivered.get(muscle, 0.0) + exercise.sets

        built.append(
            PlannedSession(
                local_date=day,
                kind="lift",
                focus=session.get("focus", ""),
                exercises=exercises,
            )
        )

    return built, delivered


def _clamp_sets(raw: float, prefs: Preferences) -> int:
    """Round a share to whole sets, within the per-exercise bounds.

    An exercise nobody needed (raw 0) is dropped rather than floored up to the
    minimum: it was chosen for a muscle that turned out not to be short, and
    padding the session with it would invent volume.
    """
    if raw <= 0:
        return 0
    return max(prefs.min_sets_per_exercise, min(prefs.max_sets_per_exercise, round(raw)))


def _fit_session(counts: list[int], ceiling: int) -> list[int]:
    """Trim a session to its ceiling, taking from the largest first.

    Largest-first rather than proportionally, so trimming never pushes an
    exercise below the minimum that made it worth including.
    """
    if ceiling <= 0 or sum(counts) <= ceiling:
        return counts

    counts = list(counts)
    while sum(counts) > ceiling:
        biggest = max(range(len(counts)), key=lambda i: counts[i])
        if counts[biggest] <= MIN_SETS_PER_EXERCISE:
            # Everything is at the floor; drop the last exercise entirely
            # rather than return a session that breaks its own ceiling.
            for i in range(len(counts) - 1, -1, -1):
                if counts[i] > 0:
                    counts[i] = 0
                    break
            else:
                break
        else:
            counts[biggest] -= 1
    return counts


# --- validation -------------------------------------------------------------


def validate(
    sessions: tuple[PlannedSession, ...],
    *,
    pool_ids: set[str] | None = None,
    training_days: set[str] | None = None,
    preferences: Preferences | None = None,
) -> list[str]:
    """Hard constraints. Returns one readable line per violation, empty if clean.

    Strings rather than exceptions because a violation is a reason to hand back
    to the coach, not a crash -- and because several can be true at once and
    the first is not necessarily the most useful.
    """
    prefs = preferences or Preferences()
    problems: list[str] = []

    for session in sessions:
        if training_days is not None and session.local_date.isoformat() not in training_days:
            problems.append(
                f"{session.local_date} is not a training day"
            )
        if session.total_sets > prefs.max_sets_per_session:
            problems.append(
                f"{session.local_date} has {session.total_sets} sets, over the "
                f"{prefs.max_sets_per_session} allowed in a session"
            )
        for exercise in session.exercises:
            if pool_ids is not None and exercise.exercise_template_id not in pool_ids:
                problems.append(
                    f"{exercise.title!r} ({exercise.exercise_template_id}) is not in the "
                    "exercise pool, so it cannot be written to Hevy"
                )
            if exercise.sets > prefs.max_sets_per_exercise:
                problems.append(
                    f"{exercise.title!r} has {exercise.sets} sets, over the "
                    f"{prefs.max_sets_per_exercise} allowed for one exercise"
                )

    problems += _rest_violations(sessions, prefs)
    problems += _run_adjacency_violations(sessions, prefs)
    return problems


def _rest_violations(sessions: tuple[PlannedSession, ...], prefs: Preferences) -> list[str]:
    """Two sessions training the same muscle group too close together."""
    if prefs.min_rest_days_same_muscle <= 0:
        return []

    problems = []
    lifts = sorted(
        (s for s in sessions if s.kind == "lift"), key=lambda s: s.local_date
    )
    for earlier, later in zip(lifts, lifts[1:]):
        gap = (later.local_date - earlier.local_date).days
        if gap > prefs.min_rest_days_same_muscle:
            continue
        for muscle in sorted(earlier.muscles & later.muscles):
            problems.append(
                f"{muscle.replace('_', ' ')} is trained on {earlier.local_date} and again "
                f"on {later.local_date}, {gap} day{'' if gap == 1 else 's'} apart"
            )
    return problems


def _run_adjacency_violations(
    sessions: tuple[PlannedSession, ...], prefs: Preferences
) -> list[str]:
    """A run the day after legs, when the user has said not to."""
    if prefs.allow_run_after_leg_day:
        return []

    leg_days = {
        s.local_date for s in sessions if s.kind == "lift" and s.muscles & LEG_MUSCLES
    }
    return [
        f"a run is planned for {s.local_date}, the day after a leg session"
        for s in sessions
        if s.kind == "run" and (s.local_date.toordinal() - 1) in {d.toordinal() for d in leg_days}
    ]


# --- did last week's plan actually happen? -----------------------------------


@dataclass(frozen=True)
class SessionOutcome:
    """One planned session, against what was logged that day."""

    local_date: date
    kind: str
    planned_sets: int
    logged_sets: int
    missing: tuple[str, ...] = ()  # planned exercises with nothing logged

    @property
    def completed(self) -> bool:
        """Anything at all logged on the day. Deliberately generous: a session
        done differently is still a session done, and calling it a miss would
        make the coach nag about a week that went fine."""
        return self.logged_sets > 0


@dataclass(frozen=True)
class Adherence:
    """How much of a plan was trained.

    Compared at the level the plan speaks in -- sessions and exercises -- and
    not in effective sets. A planned set and an effective set are different
    units: the plan counts a set once per exercise, while the volume engine
    credits a secondary muscle at half. Reporting a ratio across the two would
    read as precision that is not there.

    **Only days that have passed are judged.** A session still in the future is
    neither done nor missed, and counting it as missed makes an unstarted week
    look abandoned -- which matters because this goes to a model that is about
    to plan the next one.
    """

    week_start: date
    sessions: tuple[SessionOutcome, ...] = ()
    pending: tuple[date, ...] = ()

    @property
    def planned(self) -> int:
        """Sessions whose day has passed. The ones still ahead are in `pending`."""
        return len(self.sessions)

    @property
    def completed(self) -> int:
        return sum(1 for session in self.sessions if session.completed)

    @property
    def missed(self) -> tuple[date, ...]:
        return tuple(s.local_date for s in self.sessions if not s.completed)

    @property
    def not_started(self) -> bool:
        """The whole week is still ahead, so there is nothing to judge yet."""
        return not self.sessions and bool(self.pending)

    @property
    def planned_sets(self) -> int:
        return sum(session.planned_sets for session in self.sessions)

    @property
    def logged_sets(self) -> int:
        return sum(session.logged_sets for session in self.sessions)


def adherence(
    sessions: tuple[PlannedSession, ...],
    logged_by_day: dict[date, dict[str, int]],
    run_days: set[date] | None = None,
    today: date | None = None,
) -> Adherence:
    """Match a plan against what was logged.

    `logged_by_day` maps a date to the working sets logged per exercise
    template that day. `run_days` are days with a logged run. Both come from
    the cache; nothing here reads a repository.

    Sessions dated today or later are set aside as pending rather than judged.
    Plans are written for a week that has not started, so without this the
    freshly-stored plan reads as a week in which everything was skipped.
    """
    runs = run_days or set()
    cutoff = today or date.today()
    outcomes = []
    pending = []

    for session in sorted(sessions, key=lambda s: s.local_date):
        day = session.local_date
        if day >= cutoff:
            pending.append(day)
            continue
        if session.kind == "run":
            outcomes.append(
                SessionOutcome(
                    local_date=day,
                    kind="run",
                    planned_sets=0,
                    logged_sets=1 if day in runs else 0,
                )
            )
            continue

        logged = logged_by_day.get(day, {})
        outcomes.append(
            SessionOutcome(
                local_date=day,
                kind="lift",
                planned_sets=session.total_sets,
                logged_sets=sum(logged.values()),
                missing=tuple(
                    exercise.title
                    for exercise in session.exercises
                    if not logged.get(exercise.exercise_template_id)
                ),
            )
        )

    week_start = min((s.local_date for s in sessions), default=date.min)
    return Adherence(
        week_start=week_start, sessions=tuple(outcomes), pending=tuple(pending)
    )


def adherence_summary(result: Adherence) -> str:
    """One or two lines for the planner's instruction.

    Rendering, not computing -- and phrased as observation, because this text
    reaches a model that is about to write a rationale, and "you missed two
    sessions" is the sort of line that comes back out as a reprimand.
    """
    if result.not_started:
        return f"  (that week has not started yet -- {len(result.pending)} sessions ahead)"
    if not result.sessions:
        return "  (no previous plan to compare against)"

    lines = [
        f"  {result.completed} of {result.planned} planned sessions had logged training"
    ]
    if result.missed:
        days = ", ".join(day.isoformat() for day in result.missed)
        lines.append(f"  nothing logged on: {days}")

    partial = [
        s for s in result.sessions if s.completed and s.missing and s.kind == "lift"
    ]
    if partial:
        detail = "; ".join(
            f"{s.local_date}: {', '.join(s.missing)} not logged" for s in partial[:3]
        )
        lines.append(f"  planned but not trained -- {detail}")
    return "\n".join(lines)
