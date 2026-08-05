"""The guardrail's own tests.

Written before the coach exists, deliberately. The plan calls for this on day
one rather than day nine, because a check added after the agent is producing
plausible output gets calibrated to whatever the agent already says -- which is
exactly backwards.

So these pin the boundary while there is nothing to be tempted by.
"""

from __future__ import annotations

import pytest

from guardrails import assert_no_directive_health_language, directive_health_language


# --- what must be caught ---------------------------------------------------


@pytest.mark.parametrize(
    "rationale",
    [
        "Your sleep is down, so you should take today off.",
        "Resting heart rate is elevated — I recommend an easy week.",
        "You need to deload before this becomes an injury.",
        "Make sure you see a doctor about that knee.",
        "Three short nights running; my advice is to back off.",
        "You must not train legs while this sore.",
    ],
)
def test_health_instructions_are_caught(rationale):
    assert directive_health_language(rationale), f"missed: {rationale}"


def test_the_failure_names_the_phrase():
    # A bare assertion error would send someone hunting through a paragraph.
    with pytest.raises(AssertionError, match="you should"):
        assert_no_directive_health_language("You should rest today.", "rationale")


# --- what must NOT be caught -----------------------------------------------
# A plan is inherently directive about training. If the guardrail fires on
# ordinary planning output it will be deleted within a week, so these matter
# as much as the cases above.


@pytest.mark.parametrize(
    "rationale",
    [
        "Bench Press, 4 sets of 6 at 80 kg on Tuesday.",
        "Back is 5 sets short of target, so it gets the extra volume this week.",
        "Rest 120 seconds between sets.",
        "Squats moved to Thursday to keep a rest day before the long run.",
        "Sleep averaged 5h40 across three nights, against a 7h baseline.",
        "Resting heart rate is 8 bpm above your 30-day average.",
        "You averaged 14 working sets after short nights, against 15 otherwise.",
        "Lateral Raise is at the top of its rep range, so the load steps to 11 kg.",
    ],
)
def test_ordinary_planning_output_passes(rationale):
    assert_no_directive_health_language(rationale, "rationale")


def test_reporting_a_health_observation_is_allowed():
    """The recovery rule's actual shape: state the correlation, stop there."""
    assert_no_directive_health_language(
        "On your 9 previous training days after a short night you averaged "
        "14 working sets, against 15 otherwise."
    )


def test_mechanical_rest_is_not_prescription():
    # "rest" is a set-up field in every routine this app writes. If it tripped
    # the guardrail, no valid plan could ever pass.
    assert not directive_health_language("Rest 90 seconds. Rest between sets. Rest day Sunday.")
