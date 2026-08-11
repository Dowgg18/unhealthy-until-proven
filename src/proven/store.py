"""Persisting health, and the one write that must never happen.

THE RATCHET
-----------
The bug that motivated this module is eight lines long and looks reasonable::

    state = await provider.get_state(session_id)
    if state == "open":
        await db.update(session_id, status="connected")
    return state

Read it again for what it does *not* do. Nothing is written when the answer is
anything other than "open". The column therefore only ever moves toward
"connected", and once it arrives it can never leave. A dashboard reading that
column showed a healthy session for two months after the session had died.

It is not a typo. Each half is defensible on its own: "only write when you have
good news" sounds like avoiding noise, and "read the stored status" sounds like
avoiding a round trip. Together they build a ratchet.

THE RULE
--------
``persist`` writes MEASURED and STALE, and is a **no-op for UNKNOWN**. Not
"writes null", not "writes down" — nothing. "I could not check" is not a fact
about the subject and must never be recorded as one.

The complementary rule, which the ratchet violated: a MEASURED write happens on
every outcome, including bad ones. ``persist`` takes the Health, not the value,
so there is no shape of call that writes only the good branch.
"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar, runtime_checkable

from .health import Confidence, Health

T = TypeVar("T")


@runtime_checkable
class HealthStore(Protocol):
    """Whatever you already use — a table, a KV, a file."""

    async def write(self, key: str, value: Any, observed_at: float) -> None: ...

    async def read(self, key: str) -> tuple[Any, float] | None:
        """Return ``(value, observed_at)`` or None if never written."""
        ...


class PersistResult:
    __slots__ = ("written", "skipped_reason")

    def __init__(self, written: bool, skipped_reason: str = "") -> None:
        self.written = written
        self.skipped_reason = skipped_reason

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            "PersistResult(written=True)"
            if self.written
            else f"PersistResult(written=False, {self.skipped_reason!r})"
        )


async def persist(store: HealthStore, key: str, health: Health[T]) -> PersistResult:
    """Record an observation. Silently does nothing for UNKNOWN, by design."""
    if health.confidence is Confidence.UNKNOWN:
        return PersistResult(False, health.reason or "unknown")
    assert health.observed_at is not None  # guaranteed by Health.__post_init__
    await store.write(key, health.value, health.observed_at)
    return PersistResult(True)


async def load(
    store: HealthStore, key: str, *, max_age: float, missing_reason: str = ""
) -> Health[Any]:
    """Read a stored observation back as a Health, with freshness applied.

    Two things this refuses to do:

    * Return a bare value. A row in a table is a claim about when it was written,
      and dropping the timestamp is how "connected" outlives the connection.
    * Treat "never written" as anything but UNKNOWN. An empty column is the
      absence of an observation, not a healthy one.
    """
    row = await store.read(key)
    if row is None:
        return Health.unknown(missing_reason or f"{key}: never observed", key=key)
    value, observed_at = row
    return Health(
        value=value,
        confidence=Confidence.MEASURED,
        observed_at=observed_at,
        meta={"key": key},
    ).aged(max_age)


class InMemoryStore:
    """A store for tests and small deployments."""

    def __init__(self) -> None:
        self._rows: dict[str, tuple[Any, float]] = {}
        self.writes: list[tuple[str, Any, float]] = []

    async def write(self, key: str, value: Any, observed_at: float) -> None:
        self._rows[key] = (value, observed_at)
        self.writes.append((key, value, observed_at))

    async def read(self, key: str) -> tuple[Any, float] | None:
        return self._rows.get(key)
