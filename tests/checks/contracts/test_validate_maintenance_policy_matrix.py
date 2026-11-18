"""Tests for scripts/checks/contracts/validate_maintenance_policy_matrix.py (exhaustiveness gate,
compaction-scope-policy-matrix, Decision 191)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.checks.contracts import validate_maintenance_policy_matrix as check

pytestmark = pytest.mark.unit


def _write_sidecar(tmp_path: Path, matrix: dict) -> Path:
    path = tmp_path / "field_semantics.static.yaml"
    path.write_text(yaml.dump({"maintenance_policy": matrix}), encoding="utf-8")
    return path


_ALL_VERBS_APPLY_TRUE = {
    "merge_ops": {"apply": True, "reason": "standard"},
    "reconcile_partitions": {"apply": True, "reason": "standard"},
    "rewrite_partition_layout": {"apply": True, "reason": "standard"},
    "rewrite_current_deletes": {"apply": True, "reason": "standard"},
}
_EXHAUSTIVE_MATRIX = {
    "scd2": dict(_ALL_VERBS_APPLY_TRUE),
    "append_only": dict(_ALL_VERBS_APPLY_TRUE),
    "control": dict(_ALL_VERBS_APPLY_TRUE),
}
_UNIVERSE = {"scd2", "append_only", "control"}
_VERBS = ("merge_ops", "reconcile_partitions", "rewrite_partition_layout", "rewrite_current_deletes")

# The live registry's class universe includes smoke_harness (field_semantics.yaml declares a
# `tables:` block, Decision 191 amendment) -- a matrix exhaustive against the LIVE default
# universe (never overridden with class_universe=) needs this row too.
_EXHAUSTIVE_MATRIX_WITH_SMOKE_HARNESS = {
    **_EXHAUSTIVE_MATRIX,
    "smoke_harness": dict(_ALL_VERBS_APPLY_TRUE),
}


def test_exhaustive_matrix_passes(tmp_path: Path) -> None:
    path = _write_sidecar(tmp_path, _EXHAUSTIVE_MATRIX)
    failed: list[str] = []
    check.validate_maintenance_policy_matrix(failed, sidecar_path=path, class_universe=_UNIVERSE, verb_universe=_VERBS)
    assert failed == []


def test_missing_cell_fails(tmp_path: Path) -> None:
    matrix = {k: v for k, v in _EXHAUSTIVE_MATRIX.items() if k != "control"}
    path = _write_sidecar(tmp_path, matrix)
    failed: list[str] = []
    check.validate_maintenance_policy_matrix(failed, sidecar_path=path, class_universe=_UNIVERSE, verb_universe=_VERBS)
    assert failed == ["Maintenance policy matrix exhaustiveness"]


def test_missing_apply_key_fails(tmp_path: Path) -> None:
    matrix = dict(_EXHAUSTIVE_MATRIX)
    matrix["control"] = {"merge_ops": {"reason": "no apply key here"}}
    path = _write_sidecar(tmp_path, matrix)
    failed: list[str] = []
    check.validate_maintenance_policy_matrix(failed, sidecar_path=path, class_universe=_UNIVERSE, verb_universe=_VERBS)
    assert failed == ["Maintenance policy matrix exhaustiveness"]


def test_apply_false_missing_reason_fails(tmp_path: Path) -> None:
    matrix = dict(_EXHAUSTIVE_MATRIX)
    matrix["control"] = {"merge_ops": {"apply": False}}
    path = _write_sidecar(tmp_path, matrix)
    failed: list[str] = []
    check.validate_maintenance_policy_matrix(failed, sidecar_path=path, class_universe=_UNIVERSE, verb_universe=_VERBS)
    assert failed == ["Maintenance policy matrix exhaustiveness"]


def test_apply_false_with_reason_passes(tmp_path: Path) -> None:
    matrix = dict(_EXHAUSTIVE_MATRIX)
    matrix["control"] = {
        **dict(_ALL_VERBS_APPLY_TRUE),
        "merge_ops": {"apply": False, "reason": "deliberately excluded"},
    }
    path = _write_sidecar(tmp_path, matrix)
    failed: list[str] = []
    check.validate_maintenance_policy_matrix(failed, sidecar_path=path, class_universe=_UNIVERSE, verb_universe=_VERBS)
    assert failed == []


def test_unknown_class_fails(tmp_path: Path) -> None:
    """A matrix row for a class outside the DECLARED universe is a drift signal, not a free pass."""
    matrix = dict(_EXHAUSTIVE_MATRIX)
    matrix["bogus_class"] = {"merge_ops": {"apply": True, "reason": "should never appear"}}
    path = _write_sidecar(tmp_path, matrix)
    failed: list[str] = []
    check.validate_maintenance_policy_matrix(failed, sidecar_path=path, class_universe=_UNIVERSE, verb_universe=_VERBS)
    assert failed == ["Maintenance policy matrix exhaustiveness"]


def test_self_referential_universe_is_vacuously_exhaustive(tmp_path: Path) -> None:
    """Documents the exact failure mode this gate exists to close: deriving the class universe
    FROM the matrix's own keys makes any matrix trivially exhaustive against itself, even one
    missing a real declared class -- proving why class_universe must come from an independent
    source, never a default computed from the matrix under test."""
    matrix = {k: v for k, v in _EXHAUSTIVE_MATRIX.items() if k != "control"}  # missing "control"
    self_referential_universe = set(matrix.keys())  # the anti-pattern: universe == matrix.keys()
    path = _write_sidecar(tmp_path, matrix)
    failed: list[str] = []
    check.validate_maintenance_policy_matrix(
        failed, sidecar_path=path, class_universe=self_referential_universe, verb_universe=_VERBS
    )
    assert failed == [], "a self-referential universe hides the missing 'control' class -- this is the bug being documented"


def test_default_universe_rejects_what_a_self_referential_universe_would_hide(tmp_path: Path) -> None:
    """The counterpart to the self-referential test: the SAME matrix missing 'control', checked
    against the true declared universe, is correctly rejected."""
    matrix = {k: v for k, v in _EXHAUSTIVE_MATRIX.items() if k != "control"}
    path = _write_sidecar(tmp_path, matrix)
    failed: list[str] = []
    check.validate_maintenance_policy_matrix(failed, sidecar_path=path, class_universe=_UNIVERSE, verb_universe=_VERBS)
    assert failed == ["Maintenance policy matrix exhaustiveness"]


def test_default_class_universe_reads_the_live_registry(tmp_path: Path) -> None:
    """Without an override, class_universe is derived from the live field_semantics registry
    (never from the matrix under test) -- exercises _declared_class_universe()'s real body.
    The live registry's universe now includes smoke_harness (Decision 191 amendment), so the
    matrix checked here must carry that row too."""
    path = _write_sidecar(tmp_path, _EXHAUSTIVE_MATRIX_WITH_SMOKE_HARNESS)
    failed: list[str] = []
    check.validate_maintenance_policy_matrix(failed, sidecar_path=path, verb_universe=_VERBS)
    assert failed == []


def test_missing_smoke_harness_row_fails(tmp_path: Path) -> None:
    """The live registry's class universe includes smoke_harness -- a matrix exhaustive only over
    the pre-amendment three-class universe now fails against the live-registry default (never a
    universe self-referentially derived from the matrix's own keys)."""
    path = _write_sidecar(tmp_path, _EXHAUSTIVE_MATRIX)  # no smoke_harness row
    failed: list[str] = []
    check.validate_maintenance_policy_matrix(failed, sidecar_path=path, verb_universe=_VERBS)
    assert failed == ["Maintenance policy matrix exhaustiveness"]


def test_unreadable_sidecar_path_fails(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.yaml"
    failed: list[str] = []
    check.validate_maintenance_policy_matrix(failed, sidecar_path=missing, class_universe=_UNIVERSE, verb_universe=_VERBS)
    assert len(failed) == 1
    assert "could not read" in failed[0]


def test_real_sidecar_is_exhaustive() -> None:
    """Sanity: the committed sidecar's real matrix passes against the real live registry."""
    failed: list[str] = []
    check.validate_maintenance_policy_matrix(failed)
    assert failed == []


def test_missing_rewrite_current_deletes_cell_fails(tmp_path: Path) -> None:
    scd2 = {k: v for k, v in _ALL_VERBS_APPLY_TRUE.items() if k != "rewrite_current_deletes"}
    path = _write_sidecar(tmp_path, {**_EXHAUSTIVE_MATRIX, "scd2": scd2})
    failed: list[str] = []
    check.validate_maintenance_policy_matrix(failed, sidecar_path=path, class_universe=_UNIVERSE, verb_universe=_VERBS)
    assert failed == ["Maintenance policy matrix exhaustiveness"]
