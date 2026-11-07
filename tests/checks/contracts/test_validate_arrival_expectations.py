"""Tests for scripts/checks/contracts/validate_arrival_expectations.py (Decision 250, rec-4949)."""

from __future__ import annotations

import copy
import datetime as dt
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks import registry
from scripts.checks.contracts import validate_arrival_expectations as check
from src.common.ducklake_control_tables import control_table_names
from src.common.ducklake_scd2_schema import ops_table_names

pytestmark = pytest.mark.unit

_KINDS = {
    "enforced": {"required": ["denominator", "join_key", "semantics", "sla", "check", "alarm_route"]},
    "residual": {"required": ["denominator", "owner", "review_by"]},
    "derived": {"required": ["parent", "cardinality_check"]},
    "waived": {"required": ["reason", "authorized_by"], "optional": ["binds_when"]},
}
_ENFORCED = {
    "kind": "enforced",
    "denominator": "d",
    "join_key": "k",
    "semantics": "set_inclusion",
    "sla": "60m",
    "check": {"workflow": "arrive", "module": "scripts/mod.py"},
    "alarm_route": "ci_rca",
}
_TABLES = {
    "t_enf": _ENFORCED,
    "t_res": {"kind": "residual", "denominator": "d", "owner": "rec-4950", "review_by": "2027-02-28"},
    "t_der": {"kind": "derived", "parent": "t_enf", "cardinality_check": "arrive"},
    "t_wav": {"kind": "waived", "reason": "r", "authorized_by": "Decision 250"},
}
_WORKFLOW = {"name": "arrive", True: {"schedule": [{"cron": "17 */12 * * *"}]}}
_TAXONOMY = {"workflows": {"arrive": {"ci_rca": "watched"}, "self": {"ci_rca": "excluded"}}}
_CI_RCA = {True: {"workflow_run": {"workflows": ["CI", "arrive"]}}}


def _put(root: Path, rel: str, data: Any) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data) if not isinstance(data, str) else data, encoding="utf-8")


def _tree(tmp_path: Path, tables: dict | None = None, **over: Any) -> Path:
    standard = over.pop("standard", {"arrival_expectations": {"kinds": _KINDS, "tables": copy.deepcopy(tables or _TABLES)}})
    _put(tmp_path, check._STANDARD, standard)
    _put(tmp_path, ".github/workflows/arrive.yml", over.pop("workflow", _WORKFLOW))
    _put(tmp_path, check._TAXONOMY, over.pop("taxonomy", _TAXONOMY))
    _put(tmp_path, check._CI_RCA, over.pop("ci_rca", _CI_RCA))
    _put(tmp_path, "scripts/mod.py", "")
    return tmp_path


def _judge(root: Path, known: set[str] | None = None) -> list[str]:
    return check.judge(root, known_tables=known if known is not None else set(_TABLES))[0]


def _with(table: str, **fields: Any) -> dict:
    tables = copy.deepcopy(_TABLES)
    tables[table].update(fields)
    return tables


def test_complete_declarations_pass(tmp_path: Path) -> None:
    failures, examined = check.judge(_tree(tmp_path), known_tables=set(_TABLES))
    assert failures == [] and examined == 4


def test_table_contract_without_expectation_fails(tmp_path: Path) -> None:
    failures = _judge(_tree(tmp_path), known=set(_TABLES) | {"t_new"})
    assert failures == ["t_new: registry table has no arrival declaration"]


def test_named_waiver_passes(tmp_path: Path) -> None:
    tables = {**_TABLES, "t_new": {"kind": "waived", "reason": "no stream", "authorized_by": "rec-4949"}}
    assert _judge(_tree(tmp_path, tables), known=set(tables)) == []


def test_entry_naming_no_registry_table_fails(tmp_path: Path) -> None:
    assert _judge(_tree(tmp_path), known=set(_TABLES) - {"t_wav"}) == ["t_wav: declaration names no registry table"]


@pytest.mark.parametrize(
    ("table", "fields", "expected"),
    [
        ("t_wav", {"kind": "bogus"}, "unknown kind"),
        ("t_wav", {"extra": 1}, "field extra is not allowed"),
        ("t_res", {"owner": "x"}, "owner must match"),
        ("t_res", {"review_by": "soon"}, "review_by must be an ISO date"),
        ("t_wav", {"authorized_by": "me"}, "authorized_by must be"),
        ("t_der", {"parent": "t_wav"}, "parent must be a registered table"),
        ("t_der", {"parent": "nope"}, "parent must be a registered table"),
        ("t_der", {"cardinality_check": "missing"}, "workflow missing.yml does not exist"),
        ("t_enf", {"semantics": "vibes"}, "semantics must be one of"),
        ("t_enf", {"sla": "1.5h"}, "sla must match"),
        ("t_enf", {"alarm_route": "pager"}, "alarm_route must be one of"),
        ("t_enf", {"check": "arrive"}, "check must be a mapping"),
        ("t_enf", {"check": {"workflow": "arrive", "module": "scripts/gone.py"}}, "module scripts/gone.py does not exist"),
        ("t_enf", {"check": {"workflow": "gone", "module": "scripts/mod.py"}}, "workflow gone.yml does not exist"),
    ],
)
def test_grammar_violations_fail(tmp_path: Path, table: str, fields: dict, expected: str) -> None:
    failures = _judge(_tree(tmp_path, _with(table, **fields)))
    assert any(expected in f and f.startswith(f"{table}:") for f in failures), failures


def test_missing_required_field_and_non_mapping_entry_fail(tmp_path: Path) -> None:
    tables = copy.deepcopy(_TABLES)
    del tables["t_wav"]["reason"]
    tables["t_res"] = "text"
    failures = _judge(_tree(tmp_path, tables))
    assert "t_wav: missing required field reason" in failures and "t_res: entry is not a mapping" in failures


@pytest.mark.parametrize(
    ("over", "expected"),
    [
        ({"workflow": {"name": "arrive", True: {"workflow_dispatch": None}}}, "has no on.schedule cron"),
        (
            {"workflow": {"name": "arrive", True: {"schedule": [{"cron": "0 3 * * *"}]}}},
            "is not one the liveness sweep accepts",
        ),
        ({"workflow": "- a\n- b\n"}, "is not a mapping"),
        ({"workflow": "a: [unclosed\n"}, "does not parse"),
        ({"taxonomy": {"workflows": {}}}, "is not a config/ci_rca_taxonomy.yaml workflows key"),
        ({"taxonomy": {"workflows": {"arrive": {"ci_rca": "excluded"}}}}, "must be ci_rca: watched"),
        ({"ci_rca": {True: {"workflow_run": {"workflows": ["CI"]}}}}, "is not in .github/workflows/ci-rca.yml"),
        ({"ci_rca": "a: [unclosed\n"}, "ci-rca.yml is unreadable"),
        ({"taxonomy": "a: [unclosed\n"}, "ci_rca_taxonomy.yaml is unreadable"),
    ],
)
def test_enforced_wiring_failures(tmp_path: Path, over: dict, expected: str) -> None:
    failures = _judge(_tree(tmp_path, **over))
    assert any(expected in f for f in failures), failures


def test_self_filed_needs_an_excluded_row(tmp_path: Path) -> None:
    wired = _tree(
        tmp_path / "ok", _with("t_enf", alarm_route="self_filed"), taxonomy={"workflows": {"arrive": {"ci_rca": "excluded"}}}
    )
    assert _judge(wired) == []
    watched = _tree(tmp_path / "bad", _with("t_enf", alarm_route="self_filed"))
    assert any("must be ci_rca: excluded for self_filed" in f for f in _judge(watched))


@pytest.mark.parametrize(
    "standard",
    [
        {},
        {"arrival_expectations": "x"},
        {"arrival_expectations": {"kinds": {}, "tables": {}}},
        {"arrival_expectations": {"kinds": _KINDS, "tables": []}},
        {"arrival_expectations": {"kinds": {"enforced": {"required": "x"}}, "tables": {}}},
    ],
)
def test_absent_or_malformed_grammar_fails_loud(tmp_path: Path, standard: Any) -> None:
    failures, examined = check.judge(_tree(tmp_path, standard=standard), known_tables=set(_TABLES))
    assert len(failures) == 1 and examined == 0


def test_unreadable_standard_fails_loud(tmp_path: Path) -> None:
    failures, examined = check.judge(tmp_path, known_tables=set())
    assert failures and examined == 0
    (tmp_path / check._STANDARD).parent.mkdir(parents=True)
    (tmp_path / check._STANDARD).write_text("a: [unclosed\n", encoding="utf-8")
    assert check.judge(tmp_path, known_tables=set())[1] == 0


def test_enforced_telemetry_sessions_needs_its_oracle(tmp_path: Path) -> None:
    tables = {"telemetry_sessions": _ENFORCED}
    root = _tree(tmp_path, tables)
    failures = _judge(root, known={"telemetry_sessions"})
    assert failures == [f"telemetry_sessions: oracle {check._ORACLE} does not exist"]
    _put(root, check._ORACLE, "")
    assert _judge(root, known={"telemetry_sessions"}) == []


def test_residual_past_review_by_passes_fast_and_fails_review(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    assert _judge(root) == []
    failures, count = check.residual_failures(root, dt.date(2027, 3, 1))
    assert count == 1 and len(failures) == 1 and "t_res" in failures[0] and "rec-4950" in failures[0]
    assert check.residual_failures(root, dt.date(2027, 2, 28)) == ([], 1)


def test_residual_review_tolerates_a_malformed_date_and_unreadable_standard(tmp_path: Path) -> None:
    root = _tree(tmp_path, _with("t_res", review_by="soon"))
    assert check.residual_failures(root, dt.date(2030, 1, 1)) == ([], 1)
    assert check.residual_failures(tmp_path / "none", dt.date(2030, 1, 1))[1] == 0


def test_real_repository_passes_with_every_registry_table_examined() -> None:
    root = Path(__file__).resolve().parents[3]
    failures, examined = check.judge(root)
    assert failures == []
    assert examined == len(set(ops_table_names()) | set(control_table_names())) and examined > 0
    assert check.residual_failures(root, dt.date(2026, 11, 6))[0] == []


def test_registered_checks_report_through_the_registry(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    failed: list[str] = []
    check.validate_arrival_expectations(failed, root=root, known_tables=set(_TABLES))
    check.validate_arrival_residual_review(failed, root=root, today=dt.date(2026, 11, 6))
    assert failed == []
    check.validate_arrival_expectations(failed, root=root, known_tables=set(_TABLES) | {"t_new"})
    check.validate_arrival_residual_review(failed, root=root, today=dt.date(2027, 3, 1))
    assert failed == ["Arrival expectations", "Arrival residual review"]


def test_residual_review_defaults_to_the_calendar() -> None:
    failed: list[str] = []
    check.validate_arrival_residual_review(failed)
    assert isinstance(failed, list)


class TestWiring:
    def test_both_checks_are_registered_in_the_full_tier_and_only_one_in_pre(self) -> None:
        pre = {s.name for s in registry.pre_sequence() if s.kind == "check"}
        full = {s.name for s in registry.full_sequence() if s.kind == "check"}
        assert "validate_arrival_expectations" in pre and "validate_arrival_expectations" in full
        assert "validate_arrival_residual_review" in full
        assert "validate_arrival_residual_review" not in pre

    def test_both_resolve_through_the_registry(self) -> None:
        assert registry.resolve("validate_arrival_expectations") is check.validate_arrival_expectations
        assert registry.resolve("validate_arrival_residual_review") is check.validate_arrival_residual_review


def test_population_resolves_from_a_foreign_root_without_leaking_sys_path(tmp_path: Path) -> None:
    import sys

    population = check._population(tmp_path)
    assert population == set(ops_table_names()) | set(control_table_names())
    assert str(tmp_path) not in sys.path
