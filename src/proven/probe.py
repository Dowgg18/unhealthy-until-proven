"""Running a check without letting it lie about itself.

A probe has three ways to end and only two of them are about the subject:

  it answered            -> MEASURED
  it refused, clearly    -> MEASURED (the subject said "down")
  we never found out     -> UNKNOWN  (timeout, DNS, 500, auth, our own bug)

The third is the one that gets lost. Wrap a call in ``try/except`` and return
``False`` in the handler and you have just told the rest of the system that the
subject is down, on the authority of your own network stack.

``probe`` makes that impossible: any exception becomes UNKNOWN with the exception
in the reason, and a timeout is enforced rather than hoped for.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from .health import Health

T = TypeVar("T")

logger = logging.getLogger("proven.probe")


async def probe(
    fn: Callable[[], Awaitable[T]],
    *,
    # ASYNC109 asks callers to wrap with asyncio.timeout instead of taking a
    # timeout parameter. Declined deliberately: a probe that can hang forever is
    # a probe that reports "still checking" indefinitely, and the entire premise
    # here is that not knowing must be a first-class outcome rather than a wait.
    # Making the bound opt-out rather than opt-in would put the most dangerous
    # configuration one forgotten wrapper away.
    timeout: float | None = 5.0,  # noqa: ASYNC109
    name: str = "probe",
    expected: tuple[type[BaseException], ...] = (),
) -> Health[T]:
    """Run an async check and return a Health that cannot overstate itself.

    ``expected`` names exceptions that genuinely mean "the subject is down"
    rather than "we could not check" — a 404 from a session endpoint, say. They
    still do not become MEASURED automatically: they raise ``SubjectDown``, which
    the caller maps to a value it chooses. Everything else is UNKNOWN.
    """
    started = time.monotonic()
    try:
        if timeout is None:
            value = await fn()
        else:
            value = await asyncio.wait_for(fn(), timeout=timeout)
        return Health.measured(value, probe=name, took=round(time.monotonic() - started, 4))
    except asyncio.CancelledError:
        raise
    except SubjectDown as exc:
        return Health.measured(
            exc.value,  # type: ignore[arg-type]
            probe=name,
            took=round(time.monotonic() - started, 4),
            declared_down=True,
        )
    except TimeoutError:
        return Health.unknown(
            f"{name} timed out after {timeout}s", probe=name, kind="timeout"
        )
    except expected as exc:  # type: ignore[misc]
        # Declared as "means down" by the caller, but still surfaced explicitly.
        return Health.unknown(
            f"{name}: {type(exc).__name__}: {exc}", probe=name, kind="expected"
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("%s could not be evaluated: %s", name, exc)
        return Health.unknown(
            f"{name}: {type(exc).__name__}: {exc}", probe=name, kind="error"
        )


class SubjectDown(Exception):
    """Raise inside a probe when the subject definitively reported bad health.

    The distinction this preserves: "the API answered and told me the session is
    closed" is a measurement. "The API did not answer" is not.
    """

    def __init__(self, value: Any, reason: str = "") -> None:
        super().__init__(reason or "subject reported unhealthy")
        self.value = value


def probe_sync(
    fn: Callable[[], T],
    *,
    name: str = "probe",
) -> Health[T]:
    """Synchronous sibling. No timeout — a blocking call cannot be interrupted
    safely, and pretending otherwise would be the same class of lie."""
    started = time.monotonic()
    try:
        value = fn()
        return Health.measured(value, probe=name, took=round(time.monotonic() - started, 4))
    except SubjectDown as exc:
        return Health.measured(exc.value, probe=name, declared_down=True)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        return Health.unknown(
            f"{name}: {type(exc).__name__}: {exc}", probe=name, kind="error"
        )


def from_result(
    data: Any, error: Any, *, name: str = "query", missing_is_unknown: bool = True
) -> Health[Any]:
    """Adapter for clients that return ``(data, error)`` instead of raising.

    This exists because of a specific, extremely common bug: a query returns its
    error as a value, nobody reads it, ``data`` is None, and an optimistic default
    downstream turns the failure into good news. TypeScript will not complain.
    Neither will Python.

    Passing both halves here makes ignoring the error impossible — the error is
    an argument, not something you can forget to check.
    """
    if error is not None:
        return Health.unknown(f"{name}: {error}", probe=name, kind="result_error")
    if data is None and missing_is_unknown:
        return Health.unknown(
            f"{name}: no error, but no data either", probe=name, kind="empty"
        )
    return Health.measured(data, probe=name)
