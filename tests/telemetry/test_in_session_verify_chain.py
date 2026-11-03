"""rec-4025's in-session leg end to end: the check over the golden chain (PLAN-telemetry-in-session-source-check).

Every producer golden tree and every slice 4 verification tree is captured through the real producer (record_turn) and the real
append kernel on an in-memory DuckDB telemetry catalog, with rows above the inline threshold stored the way the writer's blob
route stores them; run_verify then reads the stored tree through the real session_tree_rows and telemetry_root_sessions verbs
(src.common.ducklake_event_reads.execute on the same catalog) and compares it with a fresh re-parse of the transcript at the
capture cursor. Module-level importorskip in the assignment form scripts/verification_graduation.py recognises.
"""

from __future__ import annotations

import pytest

duckdb = pytest.importorskip("duckdb")

import json  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

from scripts.telemetry_capture import verify  # noqa: E402
from scripts.telemetry_capture.state import CaptureState, state_dir  # noqa: E402
from scripts.telemetry_capture.transport import Budget  # noqa: E402
from scripts.telemetry_verify import ledger, shape, stored_ledger  # noqa: E402
from src.turn_capture.record_turn import PARSER_VERSION, CaptureResult, record_turn  # noqa: E402
from src.turn_capture.transcript import FsTree  # noqa: E402
from tests.fixtures import telemetry_golden_chain as chain  # noqa: E402
from tests.fixtures import telemetry_read_db as db  # noqa: E402
from tests.fixtures.turn_capture_corpus import PROJECT_REF, SID, case_names, fork_boundary, root_lines, with_root  # noqa: E402

ALL_TREES = [f"corpus:{n.removesuffix('.json')}" for n in case_names()] + [
    f"verification:{n}" for n in chain.verification.TREES
]
COMPARE = ("missing_in_store", "unexpected_in_store", "column_mismatch", "key_mismatch", "duplicate_ref_rows")
DAYS: list[float] = []


class Portal:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, action: str, fields: dict[str, Any]) -> str:
        self.calls.append((action, fields))
        return "rec-9000"

    def classes(self) -> list[str]:
        return sorted(c[1]["title"].rsplit(": ", 1)[1] for c in self.calls if c[0] == "file")


class ChainReader:
    """The reader's named() contract executed through the real verb SQL on the chain's catalog."""

    def __init__(self, con: Any) -> None:
        self.con = con
        self.calls = 0

    def named(self, verb: str, **params: Any) -> list[dict[str, Any]]:
        self.calls += 1
        return chain.run_verb(self.con, verb, **params)

    def current_state(self, table: str, **kw: Any) -> list[dict[str, Any]]:
        return []


class Session:
    """One growing tree on disk, its capture state, a catalog and a reader: the pass loop of the runner without the writer."""

    def __init__(self, tree: str, tmp_path: Path, final_files: dict[str, str]) -> None:
        self.tree, self.dir, self.files = tree, tmp_path, final_files
        self.path = tmp_path / f"{SID}.jsonl"
        self.state = CaptureState(state_dir(self.path, SID))
        self.con = chain.open_catalog()
        self.portal = Portal()
        self.cursor_root = 0

    def write(self, root_count: int | None) -> None:
        files = self.files if root_count is None else with_root(self.files, "".join(root_lines(self.files)[:root_count]))
        for rel, body in files.items():
            (self.dir / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.dir / rel).write_text(body, encoding="utf-8")

    def capture(self, root_count: int | None, *, final: bool) -> CaptureResult | None:
        self.write(root_count)
        root = self.path.read_bytes()
        loaded = self.state.load_cursor(root)
        _, cap = chain.tree_files(self.tree)
        with cap:
            result = record_turn(FsTree(self.path), loaded.cursor, project_ref=PROJECT_REF, session_final=final)
        if result.next_cursor is None:
            return None
        chain.append_batches(self.con, chain.hold_back(result, store_held_back_as_blob=True))
        assert self.state.save_cursor(result.next_cursor, root, loaded)
        return result

    def verify(self, **kw: Any) -> verify.VerifyResult:
        _, cap = chain.tree_files(self.tree)
        args: dict[str, Any] = {
            "trigger": "operator",
            "scheduling_record": None,
            "reader": ChainReader(self.con),
            "portal": self.portal,
            "budget": Budget.start(600),
            "alarms": False,
            "credentials": lambda: True,
            "lock_wait_s": 0.0,
        }
        with cap:
            return verify.run_verify(self.path, SID, **{**args, **kw})

    def stored(self) -> dict[str, list[dict[str, Any]]]:
        return chain.stored_rows(self.con)

    def stored_count(self) -> int:
        return sum(len(rows) for rows in self.stored().values())

    def has_root_row(self) -> bool:
        return any(row["entity_ref"] == SID for row in self.stored()["telemetry_sessions"])


def _check_point(session: Session, label: str) -> None:
    result = session.verify()
    assert result.completed, (label, result.status)
    counts = result.counts
    assert all(counts.get(name, 0) == 0 for name in COMPARE), (label, {n: counts.get(n) for n in COMPARE}, result.columns)
    assert all(counts.get(name, 0) == 0 for name in stored_ledger.ALARMS), (label, counts)
    if session.has_root_row():
        assert result.compare_skipped == "none" and counts["root_checked"] == 1, label
        assert counts["rows_equal"] == session.stored_count(), (label, counts["rows_equal"], session.stored_count())
    else:
        assert result.compare_skipped == "root_not_due" and counts.get("root_checked", 0) == 0, label


@pytest.mark.parametrize("tree", ALL_TREES)
def test_verify_finds_no_compare_alarm_on_any_golden_tree_at_any_point(tree: str, tmp_path: Path) -> None:
    started = time.monotonic()
    files, _ = chain.tree_files(tree)
    root_total = len(root_lines(files))
    boundary = fork_boundary(files) if tree.startswith("corpus:") else 0
    incremental = Session(tree, tmp_path / "incremental", files)
    try:
        for count in range(boundary, root_total + 1):
            if incremental.capture(count, final=False) is not None:
                _check_point(incremental, f"{tree} cut {count}")
        assert incremental.capture(None, final=True) is not None
        _check_point(incremental, f"{tree} final")
    finally:
        incremental.con.close()
    resumed = Session(tree, tmp_path / "resumed", files)
    try:
        half = max(root_total // 2, boundary)
        if resumed.capture(half, final=True) is not None:
            _check_point(resumed, f"{tree} finalized at {half}")
            resumed.capture(None, final=False)
            _check_point(resumed, f"{tree} resumed to the end")
    finally:
        resumed.con.close()
    DAYS.append(time.monotonic() - started)
    assert DAYS[-1] < 120


def _raw_classes(files: dict[str, str], closes: dict[str, str]) -> tuple[dict[str, int], dict[str, int]]:
    from collections import Counter  # noqa: PLC0415

    from tests.fixtures.turn_capture_corpus import mem_tree  # noqa: PLC0415

    tree = mem_tree(files)
    ledger_counts = Counter(f.cls for f in ledger.reconcile_ledger(tree))
    shape_counts = Counter(f.name for f in shape.check_shape(tree, closes=closes).findings)
    return dict(ledger_counts), dict(shape_counts)


@pytest.mark.parametrize("tree", ALL_TREES)
def test_final_pass_ledger_and_shape_classes_match_the_raw_tree(tree: str, tmp_path: Path) -> None:
    files, _ = chain.tree_files(tree)
    session = Session(tree, tmp_path, files)
    try:
        result = session.capture(None, final=True)
        assert result is not None
        outcome = session.verify()
        assert outcome.completed, outcome.status
        closes = verify.closes_of(result, SID)
        want_ledger, want_shape = _raw_classes(files, closes)
        counts = outcome.counts
        moved = counts.get("ledger_fork_unmeasured", 0)
        for cls, n in want_ledger.items():
            if cls in ledger.ALARMS:
                assert counts.get(cls, 0) <= n, (tree, cls)
            else:
                assert counts.get(cls, 0) == n, (tree, cls)
        assert moved + sum(counts.get(c, 0) for c in ledger.ALARMS) == sum(
            n for c, n in want_ledger.items() if c in ledger.ALARMS
        )
        for cls, n in want_shape.items():
            assert counts.get(cls, 0) == n, (tree, cls)
        for cls in shape.DECLARED.keys() | set(shape.ALARMS):
            assert counts.get(cls, 0) == want_shape.get(cls, 0), (tree, cls)
        assert not [c for c in outcome.counts if c in stored_ledger.ALARMS and outcome.counts[c]], tree
    finally:
        session.con.close()


def _final_session(tmp_path: Path, tree: str = "corpus:subagent_sync") -> Session:
    files, _ = chain.tree_files(tree)
    session = Session(tree, tmp_path, files)
    assert session.capture(None, final=True) is not None
    return session


def _first(session: Session, table: str, **where: Any) -> dict[str, Any]:
    return next(row for row in session.stored()[table] if all(row.get(k) == v for k, v in where.items()))


def _edit(session: Session, sql: str, *params: Any) -> None:
    session.con.execute(sql.format(alias=db.ALIAS), list(params))


def _only(result: verify.VerifyResult, name: str) -> None:
    assert result.completed, result.status
    assert result.counts.get(name, 0) == 1, (name, result.counts)
    assert [c for c in COMPARE if c != name and result.counts.get(c, 0)] == [], result.counts


def test_planted_store_defects_are_reported_by_class(tmp_path: Path) -> None:
    clean = _final_session(tmp_path / "clean")
    baseline = clean.verify()
    assert baseline.completed and not [c for c in COMPARE if baseline.counts.get(c, 0)]
    total = baseline.counts["rows_equal"]
    clean.con.close()

    deleted = _final_session(tmp_path / "deleted")
    victim = _first(deleted, "telemetry_observations")
    _edit(deleted, "DELETE FROM {alias}.telemetry_observations WHERE external_ref = ?", victim["external_ref"])
    result = deleted.verify()
    _only(result, "missing_in_store")
    assert result.counts["rows_equal"] == total - 1
    deleted.con.close()

    altered = _final_session(tmp_path / "altered")
    target = _first(altered, "telemetry_sessions")
    _edit(
        altered, "UPDATE {alias}.telemetry_sessions SET branch = ? WHERE external_ref = ?", "planted", target["external_ref"]
    )
    result = altered.verify()
    _only(result, "column_mismatch")
    assert result.columns == {"branch": 1}
    altered.con.close()

    extra = _final_session(tmp_path / "extra")
    row = dict(_first(extra, "telemetry_transcripts"))
    row.update(
        external_ref=row["external_ref"] + "-planted",
        event_id=row["event_id"][:-1] + ("0" if row["event_id"][-1] != "0" else "1"),
    )
    db.insert_rows(extra.con, "telemetry_transcripts", [row])
    _only(extra.verify(), "unexpected_in_store")
    extra.con.close()

    rekeyed = _final_session(tmp_path / "rekeyed")
    node = _first(rekeyed, "telemetry_observations")
    new_id = node["observation_id"][:-1] + ("0" if node["observation_id"][-1] != "0" else "1")
    _edit(
        rekeyed,
        "UPDATE {alias}.telemetry_observations SET observation_id = ? WHERE external_ref = ?",
        new_id,
        node["external_ref"],
    )
    _only(rekeyed.verify(), "key_mismatch")
    rekeyed.con.close()

    older = _final_session(tmp_path / "older")
    stale = dict(_first(older, "telemetry_sessions"))
    stale.update(parser_version=PARSER_VERSION + 1, branch="a different generation")
    db.insert_rows(older.con, "telemetry_sessions", [stale])
    result = older.verify()
    assert result.completed and result.counts["other_generation_rows"] == 1
    assert not [c for c in COMPARE if result.counts.get(c, 0)], "another parser_version is counted, never compared"
    older.con.close()


def test_ledger_classes_on_the_resume_trap_and_a_planted_input_mismatch(tmp_path: Path) -> None:
    tree = "verification:resume_with_cost_state"
    session = _final_session(tmp_path / "trap", tree)
    result = session.verify(alarms=True)
    assert result.completed, result.status
    for want in chain.verification.EXPECTED_LEDGER.values():
        assert result.counts.get(want, 0) == 1, (want, result.counts)
    assert session.portal.calls == [] and result.alarms == [], "the trap's classes are declared, filed nothing"
    models = {model: cls for _, model, cls in result.stored_models}
    assert set(models) == set(chain.verification.EXPECTED_LEDGER) | {chain.verification.SUB_MODEL}
    if PARSER_VERSION < 4:
        assert set(models.values()) <= {"stored_ledger_untested", "stored_ledger_not_compared"}, models
    else:
        assert set(models.values()) <= set(stored_ledger.DECLARED), models
        assert models[chain.verification.SUB_MODEL] not in ("stored_ledger_untested", "stored_ledger_not_compared"), models
    session.con.close()

    files, _ = chain.tree_files(tree)
    lines = files[f"{SID}.jsonl"].split("\n")
    index = next(i for i, line in enumerate(lines) if '"cost-state"' in line)
    record = json.loads(lines[index])
    record["modelUsage"][chain.verification.MAIN_REQUESTED]["inputTokens"] += 1
    lines[index] = json.dumps(record, sort_keys=True)
    planted = Session(tree, tmp_path / "planted", {**files, f"{SID}.jsonl": "\n".join(lines)})
    assert planted.capture(None, final=True) is not None
    outcome = planted.verify(alarms=True)
    assert outcome.completed and "ledger_input_class_mismatch" in outcome.alarms
    assert planted.portal.classes() == ["ledger_input_class_mismatch"]
    assert [c[0] for c in planted.portal.calls] == ["file"]
    planted.con.close()
