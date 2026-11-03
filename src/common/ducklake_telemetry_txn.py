"""The telemetry writer's one transaction loop and generic request error (T2.36, slice 2a-2, plan 3 part 1).

run_telemetry_txn owns BEGIN, the body, COMMIT, rollback and classification for every telemetry write verb. Terminal
errors are classified BEFORE ducklake_writes.is_occ_collision is consulted: that predicate matches the substring
"conflict", and a kernel GrainConflictError message carries "TELEMETRY_GRAIN_CONFLICT: conflicting row", so a terminal
error retried as a collision would loop on a deterministic refusal.

The writer role has no s3:DeleteObject by design (orphan deletion belongs to the maintenance role), so when DuckLake aborts
a commit it cannot remove the files the transaction wrote and COMMIT raises duckdb's TransactionException "Failed to commit:
Could not remove file ...: Forbidden" in place of the original cause (a lost race on the counter-row lock). That failure is
retried only when COMMIT raised it, inside the same attempt budget and backoff as an OCC collision, because every telemetry
body is idempotent (insert-only MERGE, lookup-first registration, caches written after commit). The masked text names the
bucket and is never logged, formatted or returned: only the count travels.

Imports ducklake_writes, ducklake_scd2_schema and src.telemetry.gate only: never the
ducklake_runtime facade (Decision 80), and nothing in ducklake_runtime's closure imports this module (the
scripts-never-import-telemetry-append fence).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from src.common.ducklake_scd2_schema import DuckLakeRuntimeError
from src.common.ducklake_writes import (
    OCC_MAX_ATTEMPTS,
    OCCRetryExhaustedError,
    _emit_write_metrics,
    _occ_backoff,
    _safe_rollback,
    is_occ_collision,
)
from src.telemetry.gate import AppendError

_SURROGATES = range(0xD800, 0xE000)


class TelemetryRequestError(Exception):
    """A refusal the handler maps to its own status and error_type. Carries value-free fields only."""

    def __init__(self, status: int, error_type: str, fields: Mapping[str, Any] | None = None, message: str = "") -> None:
        self.status = status
        self.error_type = error_type
        self.fields: dict[str, Any] = dict(fields or {})
        super().__init__(message or error_type)


@dataclass(frozen=True)
class TxnOutcome:
    result: Any
    occ_retries: int
    commit_ms: float
    masked_commit_retries: int = 0  # a subset of occ_retries


class TelemetryOCCRetryExhaustedError(OCCRetryExhaustedError):
    """The retry budget ran out. reason is the last attempt's class ("occ" or "masked_commit"); only the count and a fixed
    message travel, never the masked commit text."""

    def __init__(self, message: str, *, reason: str, masked_commit_retries: int) -> None:
        super().__init__(message)
        self.reason = reason
        self.masked_commit_retries = masked_commit_retries


def is_masked_commit_failure(exc: BaseException) -> bool:
    """True for the TransactionException DuckLake raises when an aborted commit cannot remove its own files.

    Matched by class name (no duckdb import) and the exact message prefix; never a bare "forbidden" match and never every
    TransactionException, so DuckLake's own retry-exhaustion and metadata failures stay loud.
    """
    return type(exc).__name__ == "TransactionException" and "failed to commit: could not remove file" in str(exc).lower()


def invalid_string_field(values: Mapping[str, object]) -> str | None:
    """The name of the first entry that is not a str, holds U+0000 or holds a lone surrogate; None if all are clean.

    Never returns, formats or logs a value. json.loads joins an escaped surrogate pair into one code point, so only a
    lone surrogate can remain; str.encode("utf-8") succeeds on everything this accepts.
    """
    for name, value in values.items():
        if not isinstance(value, str) or any(ch == "\x00" or ord(ch) in _SURROGATES for ch in value):
            return name
    return None


_BASE_TERMINAL: tuple[type[BaseException], ...] = (TelemetryRequestError, DuckLakeRuntimeError, AppendError)


def run_telemetry_txn(
    con: Any,
    body: Callable[[Any], Any],
    *,
    terminal: tuple[type[BaseException], ...] = (),
    max_attempts: int = OCC_MAX_ATTEMPTS,
    metric_sink: Callable[[str, float], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> TxnOutcome:
    """Run body(con) in one transaction, retrying an OCC collision with the shared backoff (Decision 81 cl.3).

    Terminal classes (TelemetryRequestError, DuckLakeRuntimeError and its subclasses incl. SchemaGateError,
    RowRuleViolationError and ReferentialError, the telemetry AppendError family, and caller extras) roll back and
    re-raise at once. Exhaustion raises OCCRetryExhaustedError; any other error propagates (Decision 55).
    """
    terminal_classes = _BASE_TERMINAL + terminal
    occ_retries = 0
    masked_retries = 0
    start = time.perf_counter()
    attempt = 0
    while True:
        attempt += 1
        committing = False
        try:
            con.execute("BEGIN TRANSACTION")
            result = body(con)
            committing = True
            con.execute("COMMIT")
        except terminal_classes:
            _safe_rollback(con)
            raise
        except Exception as exc:  # noqa: BLE001 -- classify, then retry-or-raise
            _safe_rollback(con)
            masked = committing and is_masked_commit_failure(exc)
            if not masked and not is_occ_collision(exc):
                raise
            if attempt < max_attempts:
                occ_retries += 1
                masked_retries += int(masked)
                _occ_backoff(attempt, sleep=sleep)
                continue
            _emit_write_metrics(metric_sink, occ_retries, (time.perf_counter() - start) * 1000.0)
            if not masked:
                raise _exhausted(attempt, "occ", masked_retries) from exc
        else:
            commit_ms = (time.perf_counter() - start) * 1000.0
            _emit_write_metrics(metric_sink, occ_retries, commit_ms)
            return TxnOutcome(
                result=result, occ_retries=occ_retries, commit_ms=commit_ms, masked_commit_retries=masked_retries
            )
        # Reached only for a masked failure on the last attempt: raised outside the except block so neither __cause__ nor
        # __context__ can reach the bucket-naming text.
        raise _exhausted(attempt, "masked_commit", masked_retries)


def _exhausted(attempt: int, reason: str, masked_retries: int) -> TelemetryOCCRetryExhaustedError:
    return TelemetryOCCRetryExhaustedError(
        f"OCC retry budget exhausted after {attempt} attempts in a telemetry transaction. "
        "Stop and RCA the contention (Decision 55) -- do NOT relax the budget.",
        reason=reason,
        masked_commit_retries=masked_retries,
    )
