"""Unit tests for src/common/ducklake_current_compaction.py (connection double; engine proof lives in
test_ducklake_current_compaction_engine.py and its committed evidence fixture)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from src.common import ducklake_current_compaction as cc
from src.common import ducklake_maintenance_scope as scope
from src.common.ducklake_partition_layout import PartitionLayoutError, TableLayout
from src.common.ducklake_scd2_schema import load_field_semantics

ROOT = Path(__file__).resolve().parents[2]
REC = "ops_recommendations_current"
DEC = "ops_decisions_current"
SMOKE = "ducklake_smoke_current"
SEMANTICS = load_field_semantics()
REGISTRY = scope.build_registry(SEMANTICS)
POLICY = scope.load_policy(SEMANTICS)
ROW_PROOF = re.compile(r"AT \(VERSION => (\d+)\)")


class Engine:
    """Connection double: snapshots, commit tokens, row proofs and rewrite results are configurable."""

    def __init__(self) -> None:
        self.executed: list[str] = []
        self.snapshots: dict[int, str | None] = {100: None}
        self.processed: dict[str, int] = {}
        self.delete_counts: dict[str, int] = {}
        self.rewrite_errors: dict[str, list[Exception]] = {}
        self.snapshot_on_noop = False
        self.mismatch_at: set[int] = set()
        self.proof_error_at: set[int] = set()
        self.files_after = 8
        self.rewrite_order: list[str] = []
        self.tokens: list[str] = []
        self._token = ""
        self._table = ""
        self._pending: tuple[str, int] | None = None
        self._last: Any = None
        self.on_rewrite: Any = None

    def add_commit(self, table: str, snapshot_id: int) -> None:
        self.snapshots[snapshot_id - 1] = None
        self.snapshots[snapshot_id] = f"{cc.VERB}:{table}:old"

    def execute(self, sql: str, params: Any = None) -> "Engine":
        self.executed.append(sql)
        s = sql.strip()
        self._last = None
        if "ducklake_rewrite_data_files" in s:
            table = re.search(r"'ops_catalog', '([^']+)'", s).group(1)
            self.rewrite_order.append(table)
            if self.on_rewrite:
                self.on_rewrite()
            errs = self.rewrite_errors.get(table)
            if errs:
                raise errs.pop(0) if len(errs) > 1 else errs[0]
            n = self.processed.get(table, 0)
            self._pending = (self._token, n)
            self._last = [("main", table, n, 1)]
        elif "set_commit_message" in s:
            self._token = params[1]
            self.tokens.append(params[1])
        elif s == "COMMIT":
            if self._pending and (self._pending[1] > 0 or self.snapshot_on_noop) and not self.snapshot_none_for_token:
                self.snapshots[max(self.snapshots) + 1] = self._pending[0]
            self._pending = None
        elif s == "ROLLBACK":
            self._pending = None
            if self.rollback_fails:
                raise RuntimeError("no active transaction")
        elif "current_snapshot()" in s:
            self._last = [(max(self.snapshots),)]
        elif "starts_with(commit_message" in s:
            prefix = params[0]
            hits = [i for i, m in self.snapshots.items() if m and m.startswith(prefix)]
            self._last = [(max(hits),)] if hits else []
        elif "WHERE commit_message = ?" in s:
            hits = [i for i, m in self.snapshots.items() if m == params[0]]
            self._last = [(hits[0],)] if hits else []
        elif "WHERE snapshot_id = ?" in s:
            self._last = [(1 if params[0] in self.snapshots else 0,)]
        elif "md5(" in s:
            version = int(ROW_PROOF.search(s).group(1))
            if version in self.proof_error_at:
                raise RuntimeError("time travel read failed")
            self._last = [(10, "BAD" if version in self.mismatch_at else "digest")]
        elif "ducklake_delete_file" in s:
            self._last = list(self.delete_counts.items())
        elif "ducklake_list_files" in s:
            self._last = [(self.files_after,)]
        return self

    snapshot_none_for_token = False
    rollback_fails = False

    def fetchone(self) -> Any:
        return self._last[0] if self._last else None

    def fetchall(self) -> list[Any]:
        return list(self._last or [])


def layout(table: str, *, live: int = 40, active: int | None = 1, legacy: int = 0) -> TableLayout:
    return TableLayout(table, "scd2", (), active, live, legacy)


@pytest.fixture
def emitted() -> list[tuple[str, float]]:
    return []


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch, emitted: list[tuple[str, float]], capsys: pytest.CaptureFixture[str]):
    def _run(engine: Engine, tables: list[str], layouts: dict[str, TableLayout] | None = None, **kw: Any) -> dict[str, Any]:
        lay = layouts if layouts is not None else {t: layout(t) for t in tables}
        monkeypatch.setattr(cc, "read_partition_layout", lambda *a, **k: lay)
        kw.setdefault("sleep", lambda s: None)
        return cc.compact_current_tables(
            engine,
            tables,
            semantics=SEMANTICS,
            registry=REGISTRY,
            policy=POLICY,
            catalog="ops_catalog",
            meta_schema="ducklake_ops",
            emit=lambda n, v: emitted.append((n, v)),
            **kw,
        )

    return _run


def line(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    out = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith(cc.LINE_PREFIX)]
    assert len(out) == 1
    return json.loads(out[0][out[0].index("{") :])


def test_rewrite_runs_at_explicit_threshold(run) -> None:
    eng = Engine()
    eng.processed[REC] = 4
    result = run(eng, [REC])
    sql = next(s for s in eng.executed if "ducklake_rewrite_data_files" in s)
    assert "delete_threshold=0.0" in sql
    assert eng.tokens[0].startswith(f"{cc.VERB}:{REC}:")
    assert result["per_table"][0]["files_processed"] == 4
    assert cc.CURRENT_REWRITE_DELETE_THRESHOLD == 0.0


def test_history_side_is_a_recorded_skip(run) -> None:
    eng = Engine()
    result = run(eng, ["ops_recommendations_history", REC], {REC: layout(REC)})
    assert {"table": "ops_recommendations_history", "reason": "history_side"} in result["skipped"]
    assert eng.rewrite_order == [REC]


def test_legacy_scheme_refusal_collected(run, emitted) -> None:
    eng = Engine()
    result = run(eng, [REC, DEC], {REC: layout(REC, legacy=3), DEC: layout(DEC)})
    assert result["refused"] == [{"table": REC, "legacy_scheme_files": 3}]
    assert eng.rewrite_order == [DEC]
    assert ("CurrentCompactionRefused", 1.0) in emitted


def test_unpartitioned_current_table_is_not_refused(run) -> None:
    eng = Engine()
    result = run(eng, [REC], {REC: layout(REC, live=5, active=None, legacy=5)})
    assert result["refused"] == [] and eng.rewrite_order == [REC]


def test_catalog_level_error_is_a_recorded_failure(run, monkeypatch, emitted, capsys) -> None:
    eng = Engine()

    def boom(*a: Any, **k: Any) -> Any:
        raise PartitionLayoutError("unclassified physical tables")

    monkeypatch.setattr(cc, "read_partition_layout", boom)
    result = cc.compact_current_tables(
        eng,
        [REC],
        semantics=SEMANTICS,
        registry=REGISTRY,
        policy=POLICY,
        catalog="ops_catalog",
        meta_schema="ducklake_ops",
        emit=lambda n, v: emitted.append((n, v)),
    )
    assert result["failures"] == [{"table": None, "error_class": "PartitionLayoutError"}]
    assert result["per_table"] == [] and eng.rewrite_order == []
    assert ("CurrentCompactionFailures", 1.0) in emitted
    assert line(capsys)["failures"][0]["table"] is None


def test_smoke_harness_tables_compact_first(run) -> None:
    eng = Engine()
    eng.delete_counts = {REC: 90, DEC: 50, SMOKE: 0}
    run(eng, [REC, DEC, SMOKE])
    assert eng.rewrite_order[0] == SMOKE


def test_largest_delete_count_first_after_canary(run) -> None:
    eng = Engine()
    eng.delete_counts = {REC: 10, DEC: 90, SMOKE: 1}
    run(eng, [REC, DEC, SMOKE])
    assert eng.rewrite_order == [SMOKE, DEC, REC]


def test_occ_exhaustion_isolates_table(run, emitted) -> None:
    eng = Engine()
    eng.rewrite_errors[REC] = [RuntimeError("could not serialize access")]
    result = run(eng, [REC, DEC], max_attempts=3)
    assert result["failures"] == [{"table": REC, "error_class": "RuntimeError"}]
    assert eng.rewrite_order.count(REC) == 3
    assert [r["table"] for r in result["per_table"]] == [DEC]
    assert "ROLLBACK" in eng.executed


def test_non_occ_error_isolates_table(run) -> None:
    eng = Engine()
    eng.rewrite_errors[REC] = [ValueError("bad")]
    result = run(eng, [REC, DEC])
    assert eng.rewrite_order.count(REC) == 1
    assert [f["table"] for f in result["failures"]] == [REC] and [r["table"] for r in result["per_table"]] == [DEC]


def test_rollback_failure_does_not_mask_the_original_error(run) -> None:
    eng = Engine()
    eng.rollback_fails = True
    eng.rewrite_errors[REC] = [ValueError("bad rewrite")]
    result = run(eng, [REC])
    assert result["failures"] == [{"table": REC, "error_class": "ValueError"}]


def test_proof_mismatch_raises_breaker_error(run, capsys) -> None:
    eng = Engine()
    eng.processed[REC] = 2
    eng.mismatch_at = {101}
    with pytest.raises(cc.CurrentCompactionProofError):
        run(eng, [REC])
    entry = line(capsys)["per_table"][0]
    assert entry["proof"] == "mismatch" and entry["commit_snapshot_id"] == 101


def test_proof_reads_the_commit_predecessor_snapshot(run) -> None:
    eng = Engine()
    eng.processed[REC] = 2
    result = run(eng, [REC])
    versions = [int(ROW_PROOF.search(s).group(1)) for s in eng.executed if "md5(" in s]
    assert versions == [100, 101] and result["per_table"][0]["proof"] == "equal"


def test_token_snapshot_proved_even_when_count_zero(run) -> None:
    eng = Engine()
    eng.snapshot_on_noop = True
    entry = run(eng, [REC])["per_table"][0]
    assert entry["files_processed"] == 0 and entry["proof"] == "equal" and entry["commit_snapshot_id"] == 101


def test_noop_rewrite_without_snapshot_is_not_a_failure(run) -> None:
    eng = Engine()
    result = run(eng, [REC])
    assert result["failures"] == [] and result["per_table"][0]["proof"] == "none"
    assert result["per_table"][0]["commit_snapshot_id"] is None


def test_unresolvable_token_after_rewrite_trips_breaker(run, capsys) -> None:
    eng = Engine()
    eng.processed[REC] = 3
    eng.snapshot_none_for_token = True
    with pytest.raises(cc.CurrentCompactionProofError):
        run(eng, [REC])
    assert line(capsys)["per_table"][0]["proof"] == "unresolved"


def test_prior_unproven_commit_reproved_before_rewrite(run) -> None:
    eng = Engine()
    eng.add_commit(REC, 200)
    entry = run(eng, [REC])["per_table"][0]
    assert entry["reproof"] == "equal"
    first_begin = next(i for i, s in enumerate(eng.executed) if s == "BEGIN TRANSACTION")
    assert [i for i, s in enumerate(eng.executed) if "md5(" in s][-1] < first_begin


def test_reproof_mismatch_trips_breaker(run, capsys) -> None:
    eng = Engine()
    eng.add_commit(REC, 200)
    eng.mismatch_at = {200}
    with pytest.raises(cc.CurrentCompactionProofError):
        run(eng, [REC])
    assert "BEGIN TRANSACTION" not in eng.executed
    assert line(capsys)["per_table"][0]["reproof"] == "mismatch"


def test_expired_predecessor_recorded_not_failed(run) -> None:
    eng = Engine()
    eng.snapshots[200] = f"{cc.VERB}:{REC}:old"
    result = run(eng, [REC])
    assert result["per_table"][0]["reproof"] == "expired"
    assert {"table": REC, "reason": "reproof_expired"} in result["skipped"]
    assert result["failures"] == [] and eng.rewrite_order == [REC]


def test_reproof_error_skips_rewrite(run) -> None:
    eng = Engine()
    eng.add_commit(REC, 200)
    eng.proof_error_at = {200}
    result = run(eng, [REC, DEC])
    assert [f["table"] for f in result["failures"]] == [REC]
    assert eng.rewrite_order == [DEC]


def test_commit_older_than_twelve_hours_is_still_reproved(run) -> None:
    eng = Engine()
    eng.add_commit(REC, 200)
    run(eng, [REC])
    lookup = next(s for s in eng.executed if "starts_with" in s)
    assert not re.search(r"snapshot_time|interval|now\(\)|current_timestamp", lookup, re.IGNORECASE)
    assert any("md5(" in s for s in eng.executed[: eng.executed.index("BEGIN TRANSACTION")])


def test_budget_exceeded_defers_rewrites(run, emitted) -> None:
    eng = Engine()
    eng.delete_counts = {REC: 90, DEC: 10}
    now = [0.0]
    eng.on_rewrite = lambda: now.__setitem__(0, 600.0)
    result = run(eng, [REC, DEC], clock=lambda: now[0], budget_s=450.0)
    assert [r["table"] for r in result["per_table"]] == [REC]
    assert result["deferred"] == [DEC] and result["failures"] == []
    assert eng.rewrite_order == [REC]
    assert ("CurrentCompactionDeferred", 1.0) in emitted


def test_reproof_not_started_past_budget(run) -> None:
    eng = Engine()
    eng.add_commit(REC, 200)
    result = run(eng, [REC], clock=iter([0.0, 999.0, 999.0, 999.0]).__next__, budget_s=450.0)
    assert result["deferred"] == [REC] and not any("md5(" in s for s in eng.executed)


def test_budget_stops_occ_retries(run) -> None:
    eng = Engine()
    now = [0.0]
    eng.on_rewrite = lambda: now.__setitem__(0, 1000.0)
    eng.rewrite_errors[REC] = [RuntimeError("could not serialize access")]
    result = run(eng, [REC], clock=lambda: now[0], budget_s=450.0)
    assert result["deferred"] == [REC] and result["failures"] == []
    assert eng.rewrite_order.count(REC) == 1


def test_budget_is_half_the_function_timeout() -> None:
    tf = (ROOT / "terraform" / "personal" / "ducklake_maintenance.tf").read_text(encoding="utf-8")
    block = tf[tf.index('resource "aws_lambda_function" "ducklake_maintenance"') :]
    timeout = int(re.search(r"timeout\s*=\s*(\d+)", block).group(1))
    assert cc.COMPACTION_TIME_BUDGET_S * 2 == timeout


def test_append_only_catalog_rewrites_nothing(run, monkeypatch, emitted) -> None:
    eng = Engine()

    def never(*a: Any, **k: Any) -> Any:
        raise AssertionError("layout must not be read when no current-side table is scoped")

    monkeypatch.setattr(cc, "read_partition_layout", never)
    result = cc.compact_current_tables(
        eng,
        ["telemetry_sessions", "telemetry_agents"],
        semantics=SEMANTICS,
        registry=REGISTRY,
        policy=POLICY,
        catalog="ops_catalog",
        meta_schema="ducklake_telemetry",
        emit=lambda n, v: emitted.append((n, v)),
    )
    assert result["per_table"] == [] and result["failures"] == [] and eng.rewrite_order == []


def test_compaction_line_keys_and_value_free(run, capsys) -> None:
    eng = Engine()
    eng.processed[REC] = 2
    eng.add_commit(REC, 200)
    run(eng, [REC, "ops_recommendations_history"])
    raw = capsys.readouterr().out
    assert raw.startswith(cc.LINE_PREFIX + " {")
    payload = json.loads(raw[raw.index("{") :])
    assert set(payload) == {"meta_schema", "per_table", "skipped", "refused", "deferred", "failures", "elapsed_ms"}
    assert payload["meta_schema"] == "ducklake_ops"
    assert set(payload["per_table"][0]) == {
        "table",
        "files_before",
        "delete_files_before",
        "files_processed",
        "files_after",
        "attempts",
        "duration_ms",
        "pre_snapshot_id",
        "commit_snapshot_id",
        "reproof",
        "proof",
    }
    assert "digest" not in raw


def test_engine_evidence_bounds_files_and_preserves_rows() -> None:
    evidence = json.loads(
        (ROOT / "tests" / "fixtures" / "current_compaction_engine_evidence.json").read_text(encoding="utf-8")
    )
    pinned = (ROOT / "config" / "lambda" / "ducklake" / "version.yaml").read_text(encoding="utf-8")
    assert f'duckdb_version: "{evidence["duckdb_version"]}"' in pinned, (
        "the pinned engine changed (Decision 99): re-run verification step 6 "
        "(CURRENT_COMPACTION_EVIDENCE_OUT=tests/fixtures/current_compaction_engine_evidence.json "
        "bin/venv-python -m pytest -q -m integration tests/common/test_ducklake_current_compaction_engine.py -p no:randomly)"
    )
    for backend, run_ in evidence["backends"].items():
        assert run_["files_after_merge"] <= 8, backend
        assert run_["delete_files_after_merge"] == 0, backend
        assert run_["files_processed_default_threshold"] == 0, backend
        assert run_["files_processed_explicit_threshold"] > 0, backend
        assert run_["rows_equal"] and run_["digest_equal"], backend
        assert run_["second_compaction_files_processed"] == 0, backend
