"""Shared assertions for output the user reads.

The recovery rule has always been forbidden from prescribing: it reports the
correlation in the user's own history and stops. `test_insights.py` pins that
with an inline word list.

The coach strains the same guardrail much harder. A plan is *inherently*
directive about training -- "four sets of bench on Tuesday" is the entire point
-- so a naive word list fires on every valid plan. The line that actually
matters is narrower:

    The coach may direct **training**. It may not direct **health**.

"Do four sets of bench" is the product working. "You should rest today because
your sleep was poor" is medical advice this app does not give, however
reasonable it sounds. Health data is surfaced as the user's own history, for
them to interpret.

Kept out of the package: this is a property of what we promise users, not
behaviour any shipped code depends on.
"""

from __future__ import annotations

import re

# Phrasings that turn an observation into an instruction. Matched as whole
# words so "arrest" doesn't trip "rest".
DIRECTIVE_PHRASES = (
    r"you should",
    r"you must",
    r"you need to",
    r"you ought to",
    r"make sure you",
    r"be sure to",
    r"i recommend",
    r"i'd recommend",
    r"i suggest",
    r"my advice",
)

# Health and recovery topics the app must not issue instructions about. These
# are only a problem in the imperative -- see MECHANICAL_EXEMPTIONS.
MEDICAL_TERMS = (
    r"overtrain(?:ing|ed)?",
    r"injur(?:y|ies|ed)",
    r"see a doctor",
    r"consult a (?:doctor|physician|professional)",
    r"medical advice",
    r"deload",
)

# Words that read as prescription in prose but are ordinary mechanics in a
# plan. "Rest 120 seconds between sets" is a set-up instruction, not health
# advice; "rest day" is a schedule label. Only flag these next to a directive.
MECHANICAL_EXEMPTIONS = (
    r"rest \d+",
    r"rest period",
    r"rest between",
    r"rest day",
    r"rest seconds",
)


def directive_health_language(text: str) -> list[str]:
    """Return every phrase in `text` that instructs the user about health.

    Empty list means the text is clean. Returns the matches rather than a bool
    so a failing test says *what* tripped it.
    """
    lowered = text.lower()

    # Blank the mechanical uses so they can't be matched by anything below.
    for pattern in MECHANICAL_EXEMPTIONS:
        lowered = re.sub(pattern, " ", lowered)

    found: list[str] = []
    for pattern in DIRECTIVE_PHRASES + MEDICAL_TERMS:
        found.extend(match.group(0) for match in re.finditer(pattern, lowered))
    return found


def assert_no_directive_health_language(text: str, context: str = "output") -> None:
    """Fail with the offending phrases, not just a boolean."""
    found = directive_health_language(text)
    assert not found, (
        f"{context} contains directive health language {found!r}. "
        "The coach may direct training; it may not direct health. "
        "Report the observation and let the user interpret it."
    )
