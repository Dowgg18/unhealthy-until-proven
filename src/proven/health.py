"""The type. Everything else in this library exists to protect it.

A health check has three outcomes, not two:

    MEASURED   we asked, and we got an answer
    STALE      we have an answer, but it is old enough to distrust
    UNKNOWN    we could not find out

Almost every monitoring bug is the third one being quietly folded into one of the
first two. Fold it into MEASURED and a dead dependency reads as healthy; fold it
into "down" and a network blip pages someone at 3am. Neither is a rounding error:
they are different facts and the caller is the only one who can decide which risk
it wants.

So ``Health`` never collapses on its own. There is no ``__bool__``, no truthiness,
no default coercion. Turning one into a yes/no answer requires naming what
``UNKNOWN`` should count as, at the call site, every time::

    if not is_healthy(status, unknown_as=DOWN):
        stop_accepting_traffic()

That is the whole idea. The bug this library is extracted from lived at the call
site, not in the probe — so the type has to make the wrong thing unwritable
rather than merely discouraged.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class Confidence(enum.Enum):
    MEASURED = "measured"
    """Freshly observed. The value can be trusted."""

    STALE = "stale"
    """A real observation, but older than the caller's freshness policy. Useful
    for context, never for a decision — 'connected, as of two months ago' is how
    a dashboard lies with technically true data."""

    UNKNOWN = "unknown"
    """We could not find out. Not a value, not a failure of the subject — a
    failure of the observation."""

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Health(Generic[T]):
    value: T | None
    confidence: Confidence
    observed_at: float | None = None
    reason: str = ""
    """Why, in the caller's words. Required for UNKNOWN, because 'unknown' with
    no explanation is unactionable in an incident."""
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.confidence is Confidence.UNKNOWN:
            if self.value is not None:
                raise ValueError(
                    "UNKNOWN carries no value; a guess with a confidence label "
                    "attached is still a guess"
                )
            if not self.reason:
                raise ValueError("UNKNOWN requires a reason")
        elif self.observed_at is None:
            raise ValueError(f"{self.confidence} requires observed_at")

    # -- constructors -------------------------------------------------------

    @classmethod
    def measured(cls, value: T, *, at: float | None = None, **meta: Any) -> Health[T]:
        return cls(
            value=value,
            confidence=Confidence.MEASURED,
            observed_at=at if at is not None else time.time(),
            meta=meta,
        )

    @classmethod
    def unknown(cls, reason: str, **meta: Any) -> Health[T]:
        return cls(value=None, confidence=Confidence.UNKNOWN, reason=reason, meta=meta)

    @classmethod
    def stale(cls, value: T, *, at: float, reason: str = "", **meta: Any) -> Health[T]:
        return cls(
            value=value,
            confidence=Confidence.STALE,
            observed_at=at,
            reason=reason,
            meta=meta,
        )

    # -- inspection ---------------------------------------------------------

    @property
    def is_known(self) -> bool:
        return self.confidence is not Confidence.UNKNOWN

    @property
    def age(self) -> float | None:
        if self.observed_at is None:
            return None
        return max(0.0, time.time() - self.observed_at)

    def aged(self, max_age: float, *, now: float | None = None) -> Health[T]:
        """Re-evaluate freshness against a policy.

        A value read out of a database is a claim about the past. This is what
        turns 'the column says connected' into 'the column says connected, last
        written two months ago' — which is a different fact, and the one the
        dashboard should have been showing.
        """
        if self.confidence is Confidence.UNKNOWN or self.observed_at is None:
            return self
        current = now if now is not None else time.time()
        if current - self.observed_at > max_age:
            return Health(
                value=self.value,
                confidence=Confidence.STALE,
                observed_at=self.observed_at,
                reason=f"observed {current - self.observed_at:.0f}s ago, "
                f"policy allows {max_age:.0f}s",
                meta=self.meta,
            )
        return self

    def map(self, fn: Any) -> Health[Any]:
        """Transform the value, keeping the confidence. Never resurrects UNKNOWN."""
        if self.confidence is Confidence.UNKNOWN:
            return self  # type: ignore[return-value]
        return Health(
            value=fn(self.value),
            confidence=self.confidence,
            observed_at=self.observed_at,
            reason=self.reason,
            meta=self.meta,
        )

    def describe(self) -> str:
        """One line for a log or a status page. Never omits the confidence."""
        if self.confidence is Confidence.UNKNOWN:
            return f"unknown ({self.reason})"
        age = self.age
        suffix = f", {age:.0f}s ago" if age is not None else ""
        if self.confidence is Confidence.STALE:
            return f"{self.value} [stale{suffix}]"
        return f"{self.value}{suffix}"

    def __bool__(self) -> bool:
        raise TypeError(
            "Health has no truth value on purpose. Collapsing it silently is the "
            "bug this library exists to prevent — use is_healthy(h, unknown_as=...) "
            "and say what an unverifiable check should count as."
        )
