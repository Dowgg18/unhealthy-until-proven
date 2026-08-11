"""Turning a Health into a decision — the only place a yes/no is allowed to appear.

Every real incident in the system this came from happened here, not in a probe.
The probes were fine. Someone wrote::

    if (config?.enabled !== false) { showHealthy() }

and a query error, returned as a value and never read, became ``undefined``,
which is not ``false``, which rendered green. Three separate mistakes stacked in
one expression, and no type system objected because every step was legal.

So this module offers no default. ``unknown_as`` has no default value, ``stale_as``
has no default value, and there is no ``.ok`` property anywhere to reach for. If a
caller wants a boolean, the caller states what an unverifiable check means to
them — which is a decision only they can make, and which is exactly the decision
that kept getting skipped.
"""

from __future__ import annotations

import enum
from typing import TypeVar

from .health import Confidence, Health

T = TypeVar("T")


class Verdict(enum.Enum):
    UP = "up"
    DOWN = "down"

    def __bool__(self) -> bool:
        return self is Verdict.UP


UP = Verdict.UP
DOWN = Verdict.DOWN


class NotVerifiable(RuntimeError):
    """Raised by ``require_healthy`` when the check could not be performed.

    Distinct from "the subject is down" on purpose: an alert that cannot tell
    those apart sends the wrong team.
    """


def is_healthy(
    health: Health[bool] | Health[T],
    *,
    unknown_as: Verdict,
    stale_as: Verdict = UP,
    predicate: object = None,
) -> bool:
    """Collapse to a boolean, having named what UNKNOWN means here.

    ``stale_as`` defaults to UP because a stale reading is still a reading, and
    most callers want the last known state rather than a panic. Freshness policy
    belongs to ``Health.aged``; this only decides what to do once something has
    already been judged stale.

    ``predicate`` is how a non-boolean value becomes a verdict::

        is_healthy(session, unknown_as=DOWN, predicate=lambda s: s == "open")
    """
    if health.confidence is Confidence.UNKNOWN:
        return bool(unknown_as)
    if health.confidence is Confidence.STALE and stale_as is DOWN:
        return False

    value = health.value
    if predicate is not None:
        return bool(predicate(value))  # type: ignore[operator]
    if isinstance(value, bool):
        return value
    raise TypeError(
        f"cannot judge {type(value).__name__} without a predicate; pass "
        "predicate=lambda v: ... so the rule is visible at the call site"
    )


def require_healthy(
    health: Health[T],
    *,
    predicate: object = None,
    stale_ok: bool = True,
) -> T:
    """Return the value, or raise. For paths that must not proceed on a guess.

    Raises ``NotVerifiable`` for UNKNOWN rather than a generic error, so the
    caller can route "we could not check" differently from "it is down".
    """
    if health.confidence is Confidence.UNKNOWN:
        raise NotVerifiable(health.reason or "health could not be determined")
    if health.confidence is Confidence.STALE and not stale_ok:
        raise NotVerifiable(f"only a stale reading available: {health.reason}")
    if predicate is not None and not predicate(health.value):  # type: ignore[operator]
        raise RuntimeError(f"unhealthy: {health.describe()}")
    return health.value  # type: ignore[return-value]
