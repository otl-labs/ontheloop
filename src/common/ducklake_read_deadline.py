"""Read deadline for the ducklake_reader event verbs (rec-4474).

A read that cannot finish before the Lambda timeout resets the whole execution environment (both warm slots and the file
cache). The runner here interrupts the DuckDB statement a margin before that, so the read answers a typed 504
read_deadline_exceeded and the environment survives. Reader-bundle only; leaf module (stdlib plus
src.common.ducklake_handler_errors), no Lambda context: callers pass plain numbers and callables (Decision 212).

DuckDB 1.5.4 drops an interrupt that arrives before execution starts and honours one only between tasks, so the
interrupt is re-sent until the call returns, and a deadline already spent never runs the call.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from src.common.ducklake_handler_errors import TypedFault

DEADLINE_MARGIN_S = 10.0
RESEND_INTERVAL_S = 0.05
READ_DEADLINE_ERROR_TYPE = "read_deadline_exceeded"
READ_DEADLINE_REASON = "reader_deadline"

_INTERRUPT_MODULES = frozenset({"_duckdb", "duckdb"})


def fire_after_s(remaining_ms: float | None, caller_ms: float | None) -> float | None:
    """Seconds until the interrupt: the earlier of the Lambda's remaining time minus the margin and the caller's deadline."""
    candidates = []
    if remaining_ms is not None:
        candidates.append(remaining_ms / 1000.0 - DEADLINE_MARGIN_S)
    if caller_ms is not None:
        candidates.append(caller_ms / 1000.0)
    return min(candidates) if candidates else None


def _deadline_fault() -> TypedFault:
    return TypedFault(504, READ_DEADLINE_ERROR_TYPE, READ_DEADLINE_REASON)


def _is_interrupt(exc: BaseException) -> bool:
    cls = type(exc)
    return cls.__name__ == "InterruptException" and cls.__module__.split(".")[0] in _INTERRUPT_MODULES


def run(con: Any, call: Callable[[], Any], fire_after: float | None, on_fire: Callable[[], None]) -> Any:
    """Run call() under the deadline: interrupt con after fire_after seconds and answer the typed 504."""
    if fire_after is None:
        return call()
    if fire_after <= 0:
        on_fire()
        raise _deadline_fault()

    fired = threading.Event()
    done = threading.Event()

    def fire() -> None:
        fired.set()
        on_fire()
        while not done.is_set():
            con.interrupt()
            done.wait(RESEND_INTERVAL_S)

    timer = threading.Timer(fire_after, fire)
    timer.daemon = True
    timer.start()
    try:
        return call()
    except Exception as exc:
        if fired.is_set() and _is_interrupt(exc):
            raise _deadline_fault() from exc
        raise
    finally:
        done.set()
        timer.cancel()
        timer.join(timeout=2 * RESEND_INTERVAL_S)


def runner(
    remaining_ms: Callable[[], float] | None, caller_ms: float | None, on_fire: Callable[[], None]
) -> Callable[[Any, Callable[[], Any]], Any]:
    """A runner(con, call) that reads the remaining time when invoked, after the connection was acquired."""

    def run_under_deadline(con: Any, call: Callable[[], Any]) -> Any:
        remaining = remaining_ms() if remaining_ms is not None else None
        return run(con, call, fire_after_s(remaining, caller_ms), on_fire)

    return run_under_deadline
