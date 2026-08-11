"""Combining many observations into one, without laundering the gaps.

The interesting part is that ``all`` and ``any`` behave differently under
UNKNOWN, and getting it backwards is easy.

**all_of — UNKNOWN contaminates.**
"Is every dependency up?" cannot be answered while one of them is unreachable.
One unknown makes the whole statement unknown, no matter how many confirmed
healthy ones sit next to it. This is the one systems get wrong, because the
natural implementation counts the good ones and compares to the total.

**any_of — a confirmed success wins.**
"Is at least one replica serving?" is answered the moment one replica says yes.
An unreachable second replica does not undo a confirmed first. But with *no*
confirmed success and at least one unknown, the answer is unknown rather than
no — absence of evidence again.

The system this was extracted from wrote the rollup by hand as

    connected > 0 ? "ok" : unreachable ? "unknown" : "down"

which is ``any_of`` semantics applied to an ``all_of`` question. It reported a
healthy fleet whenever a single member was reachable and healthy, while the rest
were unverifiable.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

from .health import Confidence, Health

T = TypeVar("T")


def _weakest(confidences: Iterable[Confidence]) -> Confidence:
    seen = set(confidences)
    if Confidence.UNKNOWN in seen:
        return Confidence.UNKNOWN
    if Confidence.STALE in seen:
        return Confidence.STALE
    return Confidence.MEASURED


def _oldest(items: Iterable[Health[T]]) -> float | None:
    stamps = [h.observed_at for h in items if h.observed_at is not None]
    return min(stamps) if stamps else None


def all_of(
    items: Iterable[Health[T]],
    predicate: Callable[[T | None], bool],
    *,
    empty_is: bool | None = None,
) -> Health[bool]:
    """Every member must satisfy the predicate. Any UNKNOWN makes the whole
    answer UNKNOWN, even if every known member passes.

    ``empty_is`` decides what an empty collection means, and has no default,
    because "no dependencies configured" is a suspiciously healthy-looking state
    that is usually a misconfiguration.
    """
    members = list(items)
    if not members:
        if empty_is is None:
            return Health.unknown("nothing to evaluate; empty_is was not specified")
        return Health.measured(empty_is, rollup="all_of", members=0)

    unknowns = [h for h in members if h.confidence is Confidence.UNKNOWN]
    if unknowns:
        return Health.unknown(
            f"{len(unknowns)} of {len(members)} could not be verified: "
            + "; ".join(h.reason for h in unknowns[:3]),
            rollup="all_of",
            members=len(members),
            unknown=len(unknowns),
        )

    failing = [h for h in members if not predicate(h.value)]
    return Health(
        value=not failing,
        confidence=_weakest(h.confidence for h in members),
        observed_at=_oldest(members),
        reason=f"{len(failing)} of {len(members)} failing" if failing else "",
        meta={"rollup": "all_of", "members": len(members), "failing": len(failing)},
    )


def any_of(
    items: Iterable[Health[T]],
    predicate: Callable[[T | None], bool],
    *,
    empty_is: bool | None = None,
) -> Health[bool]:
    """At least one member must satisfy the predicate.

    A confirmed pass short-circuits: an unverifiable sibling cannot undo an
    observation you actually made. With no confirmed pass and at least one
    UNKNOWN, the answer is UNKNOWN rather than False.
    """
    members = list(items)
    if not members:
        if empty_is is None:
            return Health.unknown("nothing to evaluate; empty_is was not specified")
        return Health.measured(empty_is, rollup="any_of", members=0)

    known = [h for h in members if h.confidence is not Confidence.UNKNOWN]
    passing = [h for h in known if predicate(h.value)]

    if passing:
        return Health(
            value=True,
            confidence=_weakest(h.confidence for h in passing),
            observed_at=_oldest(passing),
            meta={"rollup": "any_of", "members": len(members), "passing": len(passing)},
        )

    unknowns = [h for h in members if h.confidence is Confidence.UNKNOWN]
    if unknowns:
        return Health.unknown(
            f"none of {len(known)} verified members passed, and "
            f"{len(unknowns)} could not be verified",
            rollup="any_of",
            members=len(members),
            unknown=len(unknowns),
        )

    return Health(
        value=False,
        confidence=_weakest(h.confidence for h in members),
        observed_at=_oldest(members),
        reason=f"all {len(members)} members failing",
        meta={"rollup": "any_of", "members": len(members)},
    )


def summarise(items: dict[str, Health[T]]) -> dict[str, str]:
    """A status page rendering that never omits confidence.

    ``{"database": "up"}`` and ``{"database": "up [stale, 4000s ago]"}`` are
    different claims, and a status page that renders both as a green dot is the
    original bug with nicer styling.
    """
    return {name: health.describe() for name, health in items.items()}
