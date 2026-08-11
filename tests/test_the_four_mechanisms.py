"""One test per real mechanism, each written twice: the bug, then the guard.

These are not hypotheticals. All four shipped in the same production system, by
four different routes, and together they kept a dashboard green over a dead
dependency for two months.
"""

from __future__ import annotations

import time

import pytest

from proven import (
    DOWN,
    UP,
    Confidence,
    Health,
    InMemoryStore,
    NotVerifiable,
    all_of,
    any_of,
    from_result,
    is_healthy,
    load,
    persist,
    probe,
    require_healthy,
)

# --- Mechanism 1: the ratchet ---------------------------------------------


async def test_the_ratchet_only_ever_climbs_to_healthy():
    """The bug: a status written only on the happy path can never come back down."""
    store = InMemoryStore()

    async def buggy_check(state: str) -> None:
        if state == "open":  # the whole bug
            await store.write("session", "connected", time.time())

    await buggy_check("open")
    await buggy_check("closed")
    await buggy_check("closed")

    value, _ = await store.read("session")
    assert value == "connected", "demonstrates the ratchet: bad news was never written"


async def test_persist_writes_every_measured_outcome():
    """The guard: persist takes a Health, so there is no shape that writes only
    the good branch."""
    store = InMemoryStore()

    await persist(store, "session", Health.measured("open"))
    await persist(store, "session", Health.measured("closed"))

    value, _ = await store.read("session")
    assert value == "closed"
    assert len(store.writes) == 2


async def test_persist_refuses_to_record_an_unknown():
    """'I could not check' is not a fact about the subject."""
    store = InMemoryStore()

    result = await persist(store, "session", Health.unknown("provider timed out"))

    assert result.written is False
    assert "timed out" in result.skipped_reason
    assert await store.read("session") is None
    assert store.writes == []


# --- Mechanism 2: error returned as a value, never read --------------------


def test_an_unread_error_becomes_good_news():
    """The bug: `{data, error}` where nobody reads `error`.

    Note the `noqa` below. Ruff flags `error` as assigned-but-never-used, which
    is precisely the defect being reproduced — a linter found in one second what
    took two months to notice in production. TypeScript, where this shipped, says
    nothing at all.
    """
    data, error = None, "column config.enabled does not exist"  # noqa: F841

    # The line that shipped. `data` is None, so the optimistic default wins.
    enabled = (data or {}).get("enabled") if data else None
    assert (enabled is not False) is True, "a query failure rendered as healthy"


def test_from_result_makes_the_error_impossible_to_forget():
    """The guard: the error is an argument, not something you can skip."""
    health = from_result(None, "column config.enabled does not exist", name="config")

    assert health.confidence is Confidence.UNKNOWN
    assert "does not exist" in health.reason
    assert is_healthy(health, unknown_as=DOWN) is False


def test_empty_data_without_an_error_is_still_unknown():
    health = from_result(None, None, name="config")
    assert health.confidence is Confidence.UNKNOWN


# --- Mechanism 3: the optimistic default at the call site ------------------


def test_health_has_no_truth_value():
    """The guard for `if (x !== false)`: there is nothing to accidentally coerce."""
    with pytest.raises(TypeError, match="no truth value"):
        bool(Health.unknown("never checked"))

    with pytest.raises(TypeError):
        if Health.measured(True):  # noqa: SIM103
            pass  # pragma: no cover


def test_collapsing_requires_naming_what_unknown_means():
    unknown = Health.unknown("provider unreachable")

    assert is_healthy(unknown, unknown_as=DOWN) is False
    assert is_healthy(unknown, unknown_as=UP) is True

    with pytest.raises(TypeError):
        is_healthy(unknown)  # type: ignore[call-arg]


def test_require_healthy_separates_down_from_unverifiable():
    """An alert that cannot tell these apart pages the wrong team."""
    with pytest.raises(NotVerifiable):
        require_healthy(Health.unknown("dns failure"))

    assert require_healthy(Health.measured("open")) == "open"


# --- Mechanism 4: stale data read as current -------------------------------


async def test_a_stored_value_without_freshness_lies_by_omission():
    """The bug: the column said 'connected'. It had said so for two months."""
    store = InMemoryStore()
    two_months = time.time() - 60 * 24 * 3600
    await store.write("session", "connected", two_months)

    raw, _ = await store.read("session")
    assert raw == "connected", "technically true, and useless"


async def test_load_applies_a_freshness_policy():
    store = InMemoryStore()
    await store.write("session", "connected", time.time() - 60 * 24 * 3600)

    health = await load(store, "session", max_age=300)

    assert health.confidence is Confidence.STALE
    assert health.value == "connected"
    assert "policy allows" in health.reason
    verdict = is_healthy(
        health, unknown_as=DOWN, stale_as=DOWN, predicate=lambda v: v == "open"
    )
    assert verdict is False


async def test_a_key_never_written_is_unknown_not_healthy():
    store = InMemoryStore()
    health = await load(store, "never-seen", max_age=300)
    assert health.confidence is Confidence.UNKNOWN


# --- The probe boundary ----------------------------------------------------


async def test_a_failing_probe_reports_unknown_not_down():
    async def unreachable() -> str:
        raise ConnectionError("no route to host")

    health = await probe(unreachable, name="session")

    assert health.confidence is Confidence.UNKNOWN
    assert "no route to host" in health.reason
    assert health.value is None


async def test_a_probe_timeout_is_unknown():
    async def slow() -> str:
        import asyncio

        await asyncio.sleep(5)
        return "open"

    health = await probe(slow, timeout=0.05, name="session")

    assert health.confidence is Confidence.UNKNOWN
    assert "timed out" in health.reason


async def test_a_probe_that_answers_badly_is_a_measurement():
    async def closed() -> str:
        return "closed"

    health = await probe(closed, name="session")

    assert health.confidence is Confidence.MEASURED
    assert health.value == "closed"


# --- Rollup ----------------------------------------------------------------


def test_all_of_is_contaminated_by_one_unknown():
    """The rollup the original system got backwards."""
    members = [
        Health.measured("open"),
        Health.measured("open"),
        Health.unknown("provider unreachable"),
    ]

    verdict = all_of(members, lambda v: v == "open")

    assert verdict.confidence is Confidence.UNKNOWN, (
        "two confirmed healthy members cannot answer a question about all three"
    )


def test_any_of_is_satisfied_by_one_confirmed_pass():
    members = [Health.measured("open"), Health.unknown("unreachable")]
    verdict = any_of(members, lambda v: v == "open")

    assert verdict.confidence is Confidence.MEASURED
    assert verdict.value is True


def test_any_of_without_a_confirmed_pass_is_unknown_not_false():
    members = [Health.measured("closed"), Health.unknown("unreachable")]
    verdict = any_of(members, lambda v: v == "open")

    assert verdict.confidence is Confidence.UNKNOWN


def test_an_empty_fleet_must_be_declared():
    """'No dependencies configured' looks healthy and usually is not."""
    assert all_of([], lambda v: True).confidence is Confidence.UNKNOWN
    assert all_of([], lambda v: True, empty_is=False).value is False


def test_a_stale_member_weakens_the_whole_rollup():
    members = [
        Health.measured("open"),
        Health.stale("open", at=time.time() - 9999),
    ]
    verdict = all_of(members, lambda v: v == "open")

    assert verdict.value is True
    assert verdict.confidence is Confidence.STALE


# --- The type refuses to lie about itself ----------------------------------


def test_unknown_cannot_carry_a_value():
    with pytest.raises(ValueError, match="carries no value"):
        Health(value="open", confidence=Confidence.UNKNOWN, reason="x")


def test_unknown_requires_a_reason():
    with pytest.raises(ValueError, match="requires a reason"):
        Health(value=None, confidence=Confidence.UNKNOWN)


def test_a_measurement_requires_a_timestamp():
    with pytest.raises(ValueError, match="observed_at"):
        Health(value="open", confidence=Confidence.MEASURED)
