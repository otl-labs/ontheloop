"""Mirror tests for scripts/telemetry_capture/verify_due.py: the due rule and the producer fingerprint."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.telemetry_capture import verify_due as vd
from src.turn_capture.cursor import CaptureCursor

NOW = 1_800_000_000.0
DAY = vd.utc_day(NOW)


def _root(*types: str) -> bytes:
    return b"".join(json.dumps({"type": t, "uuid": f"u{i}"}).encode() + b"\n" for i, t in enumerate(types))


def _cursor(lines: int) -> CaptureCursor:
    return CaptureCursor("claude_code", 3, "sid", "a/b", "fixed_non_rollover_allowance", 1, lines_consumed={"root": lines})


def _due(root: bytes, state: dict[str, Any] | None = None, **over: Any) -> bool:
    args: dict[str, Any] = {"event": "Stop", "final": False, "outcome": "sent", "now": NOW}
    lines = root.count(b"\n")
    return vd.verify_due(over.pop("cursor", _cursor(lines)), root, state or {}, **{**args, **over})


PLAIN = _root("user", "assistant", "user")
LEDGER = _root("user", "assistant", "cost-state", "user")


def test_due_on_the_first_pass_after_a_new_cost_state_record() -> None:
    assert _due(LEDGER, {"sent_passes": 2})
    assert _due(LEDGER, {"sent_passes": 2}, outcome="no_rows")
    assert not _due(PLAIN, {"sent_passes": 2})
    assert not _due(LEDGER, {"sent_passes": 2, "last_ledger_line": 2}), "a covered record is not new"
    assert _due(LEDGER, {"sent_passes": 2, "last_ledger_line": 1})
    assert not _due(LEDGER, {"sent_passes": 2}, cursor=_cursor(2)), "a record beyond the consumed lines is not seen"
    forked = b'{"type":"cost-state","forkedFrom":"x"}\n' + PLAIN
    assert not _due(forked, {"sent_passes": 2}), "a cost-state record the fork copied is not new"


def test_due_only_on_the_sent_pass_that_advances_the_counter() -> None:
    for passes, expected in (
        (9, False),
        (10, True),
        (11, False),
        (49, False),
        (50, True),
        (51, False),
        (100, True),
        (0, False),
    ):
        assert _due(PLAIN, {"sent_passes": passes}) is expected, passes
    assert not _due(PLAIN, {"sent_passes": 10}, outcome="no_rows"), "only a sent pass advances the counter"


@pytest.mark.parametrize("outcome", ["halted", "stopped", "busy", "deferred", "off", "short_budget"])
def test_never_due_on_final_halted_stopped_busy_or_deferred_passes(outcome: str) -> None:
    assert not _due(LEDGER, {"sent_passes": 10}, outcome=outcome)
    assert not _due(LEDGER, {"sent_passes": 10}, final=True)
    assert not _due(LEDGER, {"sent_passes": 10}, event="SessionEnd")
    assert not _due(LEDGER, {"sent_passes": 10}, cursor=None), "a pass whose cursor was not persisted"
    assert _due(LEDGER, {"sent_passes": 10}, event="StopFailure")


def test_daily_cap_and_backoff() -> None:
    capped = {"sent_passes": 10, "checks_by_day": {DAY: vd.MAX_CHECKS_PER_TREE_PER_DAY}}
    assert not _due(LEDGER, capped)
    assert _due(LEDGER, {"sent_passes": 10, "checks_by_day": {DAY: vd.MAX_CHECKS_PER_TREE_PER_DAY - 1}})
    assert _due(LEDGER, {"sent_passes": 10, "checks_by_day": {"2000-01-01": 99}}), "yesterday's checks do not count"
    assert not _due(LEDGER, {"sent_passes": 10, "next_attempt_at": NOW + 1})
    assert _due(LEDGER, {"sent_passes": 10, "next_attempt_at": NOW})
    assert not _due(LEDGER, {"sent_passes": 10, "scheduled_at": NOW - 1, "next_attempt_at": NOW + 5}), (
        "scheduling holds the guard"
    )


def test_an_unsettled_check_is_retried_after_its_backoff() -> None:
    unsettled = {"sent_passes": 3, "scheduled_at": NOW - 4000, "settled_at": NOW - 9000, "next_attempt_at": NOW - 10}
    assert _due(PLAIN, unsettled), "a scheduled check that has not settled is due again past next_attempt_at"
    assert not _due(PLAIN, {**unsettled, "next_attempt_at": NOW + 10})
    settled = {**unsettled, "settled_at": unsettled["scheduled_at"]}
    assert not _due(PLAIN, settled), "a settled per-tree skip is not retried"
    assert not _due(PLAIN, {**unsettled, "checks_by_day": {DAY: vd.MAX_CHECKS_PER_TREE_PER_DAY}})
    assert vd.VERIFY_RETRY_S < vd.VERIFY_RETRY_MAX_S and vd.VERIFY_GUARD_S > 600


def test_producer_fingerprint_covers_the_emitting_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "src" / "turn_capture").mkdir(parents=True)
    (tmp_path / "src" / "telemetry").mkdir()
    (tmp_path / "config" / "lambda" / "ducklake").mkdir(parents=True)
    files = {
        "src/turn_capture/parse.py": "A = 1\n",
        "src/telemetry/timestamps.py": "B = 1\n",
        "src/telemetry/identity.py": "C = 1\n",
        "src/telemetry/append.py": "D = 1\n",
        "scripts/other.py": "E = 1\n",
    }
    semantics = (
        "tables:\n  x: 1\nops_tables:\n  ops_recommendations:\n    columns:\n      source:\n        accepted_values:\n"
        "        - one\n  telemetry_sessions:\n    columns:\n      event_kind:\n        role: input\n"
        "        accepted_values:\n"
        "        - open\n        sql_type: VARCHAR\n  ops_projects:\n    columns: {}\nother: 1\n"
    )

    def fingerprint() -> str:
        for rel, body in files.items():
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_text(body, encoding="utf-8")
        (tmp_path / vd._FIELD_SEMANTICS).write_text(semantics, encoding="utf-8")
        vd.producer_fingerprint.cache_clear()
        monkeypatch.setattr(vd, "_repo_root", lambda: tmp_path)
        return vd.producer_fingerprint()

    base = fingerprint()
    assert fingerprint() == base
    for rel in ("src/turn_capture/parse.py", "src/telemetry/timestamps.py", "src/telemetry/identity.py"):
        files[rel] += "# edit\n"
        assert fingerprint() != base, rel
        files[rel] = files[rel].removesuffix("# edit\n")
    files["src/telemetry/append.py"] += "# edit\n"
    files["scripts/other.py"] += "# edit\n"
    assert fingerprint() == base, "an unrelated file does not move it"
    files["src/turn_capture/new.py"] = "F = 1\n"
    assert fingerprint() != base, "a new emitting module does"
    del files["src/turn_capture/new.py"]
    (tmp_path / "src" / "turn_capture" / "new.py").unlink()
    assert fingerprint() == base
    semantics = semantics.replace("role: input", "role: derived")
    assert fingerprint() != base, "a telemetry table's column role moves it"
    semantics = semantics.replace("role: derived", "role: input")
    semantics = semantics.replace("        - one\n", "        - one\n        - two\n").replace(
        "- open\n", "- open\n        - annotate\n"
    )
    assert fingerprint() == base, "an accepted_values list never moves it"
    semantics = semantics.replace(
        "ops_recommendations:\n    columns:\n", "ops_recommendations:\n    columns:\n      extra: 1\n"
    )
    semantics = semantics.replace("columns: {}", "columns: {x: 1}")
    assert fingerprint() == base, "a non-telemetry block never moves it"
    (tmp_path / vd._FIELD_SEMANTICS).unlink()
    files["src/turn_capture/parse.py"] = "A = 1\n"
    (tmp_path / "src" / "telemetry" / "identity.py").unlink()
    vd.producer_fingerprint.cache_clear()
    assert vd.producer_fingerprint() != base, "an unreadable file is part of the fingerprint, never an exception"
    vd.producer_fingerprint.cache_clear()


def test_a_pending_record_does_not_retrigger_every_pass() -> None:
    held = {"sent_passes": 3, "held_ledger_line": 2}
    assert not _due(LEDGER, held)
    assert not _due(LEDGER, {**held, "sent_passes": 4}), "still held on the next pass"
    assert _due(LEDGER, {**held, "sent_passes": 10}), "a sample re-reads it"
    two = _root("user", "cost-state", "assistant", "cost-state", "user")
    assert _due(two, {"sent_passes": 3, "held_ledger_line": 1, "last_ledger_line": 1}), "a new record above both is due"
    assert not _due(two, {"sent_passes": 3, "held_ledger_line": 3, "last_ledger_line": 1})


def test_imports_only_the_stdlib_and_transcript() -> None:
    tree = ast.parse(Path(vd.__file__).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    foreign = {m for m in modules if m.split(".")[0] not in sys.stdlib_module_names and m != "src.turn_capture.transcript"}
    assert foreign == set()
