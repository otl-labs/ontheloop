"""Data quality models: Check/CheckResult/RunResult dataclasses and shared path constants."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_DQ_DIR = _ROOT / "config" / "agent" / "data_quality"
_TOMBSTONES_PATH = _DQ_DIR / "dq_tombstones.yaml"


@dataclass
class Check:
    """A single compiled data quality check."""

    table: str
    column: str | None
    test_type: str
    kind: str | None  # a ducklake_dq_checks.DQ_KINDS key; None = not runnable on the reader (SKIP)
    description: str
    severity: str = "error"
    enforced: bool = True
    exclude_before: str | None = None
    backend: str = "ducklake"  # sole backend: the DuckLake closed reader (Decision 84 I-1)
    params: dict[str, Any] = field(default_factory=dict)


def dq_request(check: Check) -> dict[str, Any]:
    """The dq_check request a check sends to the reader (printed by --dry-run; no SQL exists client-side)."""
    return {"action": "dq_check", "check": check.kind, "table": check.table, "params": check.params}


@dataclass
class CheckResult:
    """Result of executing a single check."""

    check: Check
    verdict: str  # PASS | FAIL | WARN | ERROR | SKIP
    violation_count: int = 0
    detail: str = ""
    duration_seconds: float = 0.0


@dataclass
class RunResult:
    """Aggregate result of a full run."""

    results: list[CheckResult] = field(default_factory=list)
    verdict: str = "PASS"
    duration_seconds: float = 0.0

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.verdict == "PASS")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.verdict == "FAIL")

    @property
    def unenforced_fail(self) -> int:
        return sum(1 for r in self.results if r.verdict == "UNENFORCED_FAIL")

    @property
    def warned(self) -> int:
        return sum(1 for r in self.results if r.verdict == "WARN")

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.verdict == "SKIP")

    @property
    def errored(self) -> int:
        return sum(1 for r in self.results if r.verdict == "ERROR")

    @property
    def hard_gated(self) -> int:
        return sum(1 for r in self.results if r.verdict == "HARD_GATE")

    @property
    def unavailable(self) -> int:
        return sum(1 for r in self.results if r.verdict == "UNAVAILABLE")
