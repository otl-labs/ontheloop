"""Tests for validate_reconcile_pending_gate() -- hotfix follow-up to the 2026-07-24 ops_decisions
incident. Covers the merge-time check (fail-closed classification, subset/append_only rejection,
diff-added-column gap, origin/main advisory-skip) and the --deploy-gate CLI entrypoint, which
share one predicate (_pending_reconcile_map / _has_pending_reconcile)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import yaml

from scripts.checks.ops_governance import validate_reconcile_pending_gate as gate


def _sidecar(contract_table_ops: dict) -> dict:
    return {"contract_table_ops": contract_table_ops, "dormant_ops_tables": {}, "smoke_ops_tables": {}}


class TestUnclassifiedTable:
    def test_unclassified_table_fails(self) -> None:
        sidecar = _sidecar({"ops_recommendations": {"reconcile_scope": "ducklake", "pending_reconcile": {}}})
        with (
            mock.patch.object(gate, "_load_sidecar", return_value=sidecar),
            mock.patch.object(gate, "_ops_table_ids", return_value=["ops_recommendations", "ops_decisions"]),
            mock.patch.object(gate, "_declared_columns", return_value=set()),
            mock.patch.object(gate, "_write_mode", return_value="scd2"),
            mock.patch.object(gate, "_diff_added_columns", return_value=None),
        ):
            failed: list[str] = []
            gate.validate_reconcile_pending_gate(failed)
        assert any("ops_decisions" in f and "reconcile_scope" in f for f in failed)

    def test_iterates_generators_authoritative_table_set_not_contract_table_ops_keys(self) -> None:
        """generate()['ops_tables'] is authoritative (VP step 1 fix_if) -- a table present ONLY
        in contract_table_ops (never in the generator's set) must never be checked, and a table
        present in the generator's set but ABSENT from contract_table_ops must still fail closed."""
        sidecar = _sidecar({"ops_ghost": {"reconcile_scope": "ducklake", "pending_reconcile": {}}})
        with (
            mock.patch.object(gate, "_load_sidecar", return_value=sidecar),
            mock.patch.object(gate, "_ops_table_ids", return_value=["ops_recommendations"]),
            mock.patch.object(gate, "_declared_columns", return_value=set()),
            mock.patch.object(gate, "_write_mode", return_value="scd2"),
            mock.patch.object(gate, "_diff_added_columns", return_value=None),
        ):
            failed: list[str] = []
            gate.validate_reconcile_pending_gate(failed)
        assert any("ops_recommendations" in f for f in failed)
        assert not any("ops_ghost" in f for f in failed)

    def test_generator_exception_is_caught_and_appended(self) -> None:
        with mock.patch.object(gate, "_ops_table_ids", side_effect=RuntimeError("boom")):
            failed: list[str] = []
            gate.validate_reconcile_pending_gate(failed)
        assert any("boom" in f for f in failed)


class TestUndeclaredColumnSubset:
    def test_pending_reconcile_column_not_declared_fails(self) -> None:
        sidecar = _sidecar(
            {
                "ops_decisions": {
                    "reconcile_scope": "ducklake",
                    "pending_reconcile": {"history": ["typo_column"], "current": []},
                    "reconciled": {"history": {}, "current": {}},
                }
            }
        )
        with (
            mock.patch.object(gate, "_load_sidecar", return_value=sidecar),
            mock.patch.object(gate, "_ops_table_ids", return_value=["ops_decisions"]),
            mock.patch.object(gate, "_declared_columns", return_value={"intent", "id"}),
            mock.patch.object(gate, "_write_mode", return_value="scd2"),
            mock.patch.object(gate, "_diff_added_columns", return_value=None),
        ):
            failed: list[str] = []
            gate.validate_reconcile_pending_gate(failed)
        assert any("typo_column" in f and "not a declared column" in f for f in failed)

    def test_reconciled_column_not_declared_fails(self) -> None:
        sidecar = _sidecar(
            {
                "ops_decisions": {
                    "reconcile_scope": "ducklake",
                    "pending_reconcile": {"history": [], "current": []},
                    "reconciled": {"history": {"stale_column": {"at": "x", "evidence": "y"}}, "current": {}},
                }
            }
        )
        with (
            mock.patch.object(gate, "_load_sidecar", return_value=sidecar),
            mock.patch.object(gate, "_ops_table_ids", return_value=["ops_decisions"]),
            mock.patch.object(gate, "_declared_columns", return_value={"intent", "id"}),
            mock.patch.object(gate, "_write_mode", return_value="scd2"),
            mock.patch.object(gate, "_diff_added_columns", return_value=None),
        ):
            failed: list[str] = []
            gate.validate_reconcile_pending_gate(failed)
        assert any("stale_column" in f and "not a declared column" in f for f in failed)

    def test_declared_column_passes(self) -> None:
        sidecar = _sidecar(
            {
                "ops_decisions": {
                    "reconcile_scope": "ducklake",
                    "pending_reconcile": {"history": ["intent"], "current": ["intent"]},
                    "reconciled": {"history": {}, "current": {}},
                }
            }
        )
        with (
            mock.patch.object(gate, "_load_sidecar", return_value=sidecar),
            mock.patch.object(gate, "_ops_table_ids", return_value=["ops_decisions"]),
            mock.patch.object(gate, "_declared_columns", return_value={"intent", "id"}),
            mock.patch.object(gate, "_write_mode", return_value="scd2"),
            mock.patch.object(gate, "_diff_added_columns", return_value=None),
        ):
            failed: list[str] = []
            gate.validate_reconcile_pending_gate(failed)
        assert failed == []


class TestAppendOnlyCurrentRejection:
    def test_current_side_pending_entry_on_append_only_table_fails(self) -> None:
        sidecar = _sidecar(
            {
                "ops_smoke_events": {
                    "reconcile_scope": "ducklake",
                    "pending_reconcile": {"history": [], "current": ["event_type"]},
                    "reconciled": {"history": {}, "current": {}},
                }
            }
        )
        with (
            mock.patch.object(gate, "_load_sidecar", return_value=sidecar),
            mock.patch.object(gate, "_ops_table_ids", return_value=["ops_smoke_events"]),
            mock.patch.object(gate, "_declared_columns", return_value={"event_type"}),
            mock.patch.object(gate, "_write_mode", return_value="append_only"),
            mock.patch.object(gate, "_diff_added_columns", return_value=None),
        ):
            failed: list[str] = []
            gate.validate_reconcile_pending_gate(failed)
        assert any("append_only" in f for f in failed)

    def test_history_only_on_append_only_table_passes(self) -> None:
        sidecar = _sidecar(
            {
                "ops_smoke_events": {
                    "reconcile_scope": "ducklake",
                    "pending_reconcile": {"history": ["event_type"], "current": []},
                    "reconciled": {"history": {}, "current": {}},
                }
            }
        )
        with (
            mock.patch.object(gate, "_load_sidecar", return_value=sidecar),
            mock.patch.object(gate, "_ops_table_ids", return_value=["ops_smoke_events"]),
            mock.patch.object(gate, "_declared_columns", return_value={"event_type"}),
            mock.patch.object(gate, "_write_mode", return_value="append_only"),
            mock.patch.object(gate, "_diff_added_columns", return_value=None),
        ):
            failed: list[str] = []
            gate.validate_reconcile_pending_gate(failed)
        assert failed == []


class TestDiffAddedColumnGap:
    def test_diff_added_column_without_pending_entry_fails_and_names_reconcile_columns(self) -> None:
        sidecar = _sidecar(
            {
                "ops_decisions": {
                    "reconcile_scope": "ducklake",
                    "pending_reconcile": {"history": [], "current": []},
                    "reconciled": {"history": {}, "current": {}},
                }
            }
        )
        with (
            mock.patch.object(gate, "_load_sidecar", return_value=sidecar),
            mock.patch.object(gate, "_ops_table_ids", return_value=["ops_decisions"]),
            mock.patch.object(gate, "_declared_columns", return_value={"new_col"}),
            mock.patch.object(gate, "_write_mode", return_value="scd2"),
            mock.patch.object(gate, "_diff_added_columns", return_value={"ops_decisions": {"new_col"}}),
        ):
            failed: list[str] = []
            gate.validate_reconcile_pending_gate(failed)
        assert any("new_col" in f and "reconcile_columns" in f for f in failed)

    def test_diff_added_column_with_matching_pending_entry_passes(self) -> None:
        sidecar = _sidecar(
            {
                "ops_decisions": {
                    "reconcile_scope": "ducklake",
                    "pending_reconcile": {"history": ["new_col"], "current": ["new_col"]},
                    "reconciled": {"history": {}, "current": {}},
                }
            }
        )
        with (
            mock.patch.object(gate, "_load_sidecar", return_value=sidecar),
            mock.patch.object(gate, "_ops_table_ids", return_value=["ops_decisions"]),
            mock.patch.object(gate, "_declared_columns", return_value={"new_col"}),
            mock.patch.object(gate, "_write_mode", return_value="scd2"),
            mock.patch.object(gate, "_diff_added_columns", return_value={"ops_decisions": {"new_col"}}),
        ):
            failed: list[str] = []
            gate.validate_reconcile_pending_gate(failed)
        assert failed == []

    def test_append_only_table_only_requires_history_side_declared(self) -> None:
        sidecar = _sidecar(
            {
                "ops_smoke_events": {
                    "reconcile_scope": "ducklake",
                    "pending_reconcile": {"history": ["new_col"], "current": []},
                    "reconciled": {"history": {}, "current": {}},
                }
            }
        )
        with (
            mock.patch.object(gate, "_load_sidecar", return_value=sidecar),
            mock.patch.object(gate, "_ops_table_ids", return_value=["ops_smoke_events"]),
            mock.patch.object(gate, "_declared_columns", return_value={"new_col"}),
            mock.patch.object(gate, "_write_mode", return_value="append_only"),
            mock.patch.object(gate, "_diff_added_columns", return_value={"ops_smoke_events": {"new_col"}}),
        ):
            failed: list[str] = []
            gate.validate_reconcile_pending_gate(failed)
        assert failed == []


def test_generated_ops_tables_injects_and_restores_sys_path(tmp_path: Path) -> None:
    """_generated_ops_tables inserts the given root into sys.path only if absent, and always
    removes it again afterward -- it must not leave a residual sys.path entry."""
    import sys

    root_str = str(tmp_path)
    assert root_str not in sys.path
    generated = gate._generated_ops_tables(tmp_path)
    assert "ops_entity_counters" in generated  # the real generator is importable regardless of root
    assert root_str not in sys.path  # removed again in the finally block


def test_control_class_needs_no_reconcile_sides() -> None:
    """A control-class contract (ops_entity_counters, T2.26) needs no history/current reconcile
    sides. Exercises the REAL _write_mode/generated wiring (not a mocked _write_mode): before the
    fix, _write_mode defaulted to scd2 for a contract-backed table absent from the dormant/smoke
    sidecar blocks, and _check_diff_added_column_gap demanded BOTH sides -- which the new
    contract's empty pending_reconcile could never satisfy, so it would fail --pre on arrival."""
    sidecar = gate._load_sidecar(gate._common.ROOT)
    generated = gate._generated_ops_tables(gate._common.ROOT)
    assert generated["ops_entity_counters"]["write_mode"] == "control"
    assert gate._write_mode("ops_entity_counters", sidecar, generated) == "control"

    with mock.patch.object(
        gate,
        "_diff_added_columns",
        return_value={"ops_entity_counters": {"counter_name", "current_value"}},
    ):
        failed: list[str] = []
        gate._check_diff_added_column_gap(gate._common.ROOT, sidecar, "reconcile-pending-gate:", failed, generated=generated)
    assert failed == []


def _gap(status: str, scope: str = "exempt") -> list[str]:
    sidecar = _sidecar(
        {"ops_new": {"status": status, "reconcile_scope": scope, "pending_reconcile": {"history": [], "current": []}}}
    )
    generated = {"ops_new": {"write_mode": "scd2"}}
    with mock.patch.object(gate, "_diff_added_columns", return_value={"ops_new": {"a", "b"}}):
        failed: list[str] = []
        gate._check_diff_added_column_gap(gate._common.ROOT, sidecar, "k:", failed, generated=generated)
    return failed


def test_preproduction_exempt_table_owes_no_alters_and_is_reported(capsys) -> None:
    failed = _gap("pre_production")
    assert failed == []
    assert "['ops_new']" in capsys.readouterr().out  # the leg names the skipped table, never a vacuous pass


def test_live_exempt_table_still_owes_alters() -> None:
    failed = _gap("live")
    assert len(failed) == 4  # two columns x history+current, none skipped
    failed = _gap("pre_production", scope="ducklake")
    assert len(failed) == 4  # reconcile_scope ducklake is never skipped


class TestOriginMainAdvisorySkip:
    def test_unreachable_origin_main_advisory_skips_without_failing(self, capsys) -> None:
        sidecar = _sidecar(
            {
                "ops_decisions": {
                    "reconcile_scope": "ducklake",
                    "pending_reconcile": {"history": [], "current": []},
                    "reconciled": {"history": {}, "current": {}},
                }
            }
        )
        with (
            mock.patch.object(gate, "_load_sidecar", return_value=sidecar),
            mock.patch.object(gate, "_ops_table_ids", return_value=["ops_decisions"]),
            mock.patch.object(gate, "_declared_columns", return_value=set()),
            mock.patch.object(gate, "_write_mode", return_value="scd2"),
            mock.patch.object(gate, "_diff_added_columns", return_value=None),
        ):
            failed: list[str] = []
            gate.validate_reconcile_pending_gate(failed)
        assert failed == []
        assert "ADVISORY-SKIP" in capsys.readouterr().out


class TestDiffAddedColumnsRealGit:
    """Exercises _diff_added_columns against a real (tmp_path) git repo -- never the real
    repository's own sidecar/contracts (Decision 55: tests never mutate real state)."""

    def _repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        (repo / "docs" / "contracts").mkdir(parents=True)
        (repo / "config" / "lambda" / "ducklake").mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        (repo / "docs" / "contracts" / "ops_decisions.yaml").write_text("fields:\n  id: {}\n", encoding="utf-8")
        (repo / "config" / "lambda" / "ducklake" / "field_semantics.static.yaml").write_text(
            "dormant_ops_tables:\n  ops_priority_queue:\n    columns:\n      rec_id: {}\nsmoke_ops_tables: {}\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)
        subprocess.run(["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=repo, check=True)
        return repo

    def test_new_contract_field_detected(self, tmp_path: Path) -> None:
        repo = self._repo(tmp_path)
        (repo / "docs" / "contracts" / "ops_decisions.yaml").write_text(
            "fields:\n  id: {}\n  new_field: {}\n", encoding="utf-8"
        )
        added = gate._diff_added_columns(repo)
        assert added == {"ops_decisions": {"new_field"}}

    def test_new_sidecar_dormant_column_detected(self, tmp_path: Path) -> None:
        repo = self._repo(tmp_path)
        (repo / "config" / "lambda" / "ducklake" / "field_semantics.static.yaml").write_text(
            "dormant_ops_tables:\n  ops_priority_queue:\n    columns:\n      rec_id: {}\n      new_col: {}\n"
            "smoke_ops_tables: {}\n",
            encoding="utf-8",
        )
        added = gate._diff_added_columns(repo)
        assert added == {"ops_priority_queue": {"new_col"}}

    def test_no_changes_returns_empty_dict(self, tmp_path: Path) -> None:
        repo = self._repo(tmp_path)
        added = gate._diff_added_columns(repo)
        assert added == {}

    def test_origin_main_unreachable_returns_none(self, tmp_path: Path) -> None:
        repo = tmp_path / "no_origin_repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        assert gate._diff_added_columns(repo) is None

    def test_new_file_absent_from_origin_main_counts_all_fields_as_added(self, tmp_path: Path) -> None:
        repo = self._repo(tmp_path)
        (repo / "docs" / "contracts" / "ops_recommendations.yaml").write_text(
            "fields:\n  rec_id: {}\n  status: {}\n", encoding="utf-8"
        )
        added = gate._diff_added_columns(repo)
        assert added.get("ops_recommendations") == {"rec_id", "status"}

    def test_diff_added_telemetry_column_needs_pending_entry(self, tmp_path: Path) -> None:
        contract = self._repo(tmp_path) / "docs" / "contracts" / "telemetry_agents.yaml"
        contract.write_text("fields:\n  host_run_ref: {}\n  surface:\n    derivation: {timing: read}\n", encoding="utf-8")
        assert gate._diff_added_columns(contract.parents[2]) == {"telemetry_agents": {"host_run_ref"}}  # surface owes none
        for history, expect_failure in (([], True), (["host_run_ref"], False)):
            failed: list[str] = []
            generated = {"telemetry_agents": {"write_mode": "append_only"}}
            sidecar = _telemetry_sidecar(history)
            gate._check_diff_added_column_gap(contract.parents[2], sidecar, "k:", failed, generated=generated)
            assert bool(failed) is expect_failure
            if failed:
                assert "provision_telemetry_catalog" in failed[0] and "reconcile_columns" not in failed[0]

    def test_old_yaml_invalid_on_origin_main_treated_as_absent(self, tmp_path: Path) -> None:
        repo = tmp_path / "badyaml_repo"
        (repo / "docs" / "contracts").mkdir(parents=True)
        (repo / "config" / "lambda" / "ducklake").mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        (repo / "docs" / "contracts" / "ops_decisions.yaml").write_text("fields: [unterminated", encoding="utf-8")
        (repo / "config" / "lambda" / "ducklake" / "field_semantics.static.yaml").write_text(
            "dormant_ops_tables: {}\nsmoke_ops_tables: {}\n", encoding="utf-8"
        )
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "base-broken"], cwd=repo, check=True)
        subprocess.run(["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=repo, check=True)

        (repo / "docs" / "contracts" / "ops_decisions.yaml").write_text("fields:\n  id: {}\n  intent: {}\n", encoding="utf-8")
        added = gate._diff_added_columns(repo)
        assert added == {"ops_decisions": {"id", "intent"}}


class TestSharedPredicate:
    """_has_pending_reconcile / _pending_reconcile_map / _blocking_tables -- the SAME accessor
    the merge-time check and --deploy-gate both read, so the two can never drift apart."""

    def test_has_pending_reconcile_true_when_either_side_nonempty(self) -> None:
        assert gate._has_pending_reconcile({"history": ["x"], "current": []})
        assert gate._has_pending_reconcile({"history": [], "current": ["x"]})
        assert not gate._has_pending_reconcile({"history": [], "current": []})

    def test_blocking_tables_only_ducklake_scope_with_nonempty_pending(self) -> None:
        sidecar = _sidecar(
            {
                "ops_decisions": {
                    "reconcile_scope": "ducklake",
                    "pending_reconcile": {"history": ["intent"], "current": []},
                },
                "ops_recommendations": {
                    "reconcile_scope": "ducklake",
                    "pending_reconcile": {"history": [], "current": []},
                },
                "ops_synthetic_exempt": {
                    "reconcile_scope": "exempt",
                    "pending_reconcile": {"history": ["whatever"], "current": []},
                },
            }
        )
        assert gate._blocking_tables(sidecar) == ["ops_decisions"]


class TestDeployGateCli:
    def test_deploy_gate_nonzero_when_blocked(self, capsys) -> None:
        sidecar = _sidecar(
            {
                "ops_decisions": {
                    "reconcile_scope": "ducklake",
                    "pending_reconcile": {"history": ["intent"], "current": []},
                }
            }
        )
        with mock.patch.object(gate, "_load_sidecar", return_value=sidecar):
            exit_code = gate.main(["--deploy-gate"])
        captured = capsys.readouterr()
        assert exit_code == 1
        assert "DEPLOY REFUSED" in captured.err
        assert "ops_decisions" in captured.err
        assert "telemetry-writer" in captured.err

    def test_deploy_gate_zero_when_clear(self) -> None:
        sidecar = _sidecar(
            {
                "ops_decisions": {
                    "reconcile_scope": "ducklake",
                    "pending_reconcile": {"history": [], "current": []},
                }
            }
        )
        with mock.patch.object(gate, "_load_sidecar", return_value=sidecar):
            exit_code = gate.main(["--deploy-gate"])
        assert exit_code == 0

    def test_main_without_deploy_gate_runs_merge_time_check_against_real_repo(self) -> None:
        exit_code = gate.main([])
        assert exit_code == 0

    def test_main_without_deploy_gate_prints_failures(self, capsys) -> None:
        sidecar = _sidecar({"ops_recommendations": {"reconcile_scope": "bogus"}})
        with (
            mock.patch.object(gate, "_load_sidecar", return_value=sidecar),
            mock.patch.object(gate, "_ops_table_ids", return_value=["ops_recommendations"]),
            mock.patch.object(gate, "_declared_columns", return_value=set()),
            mock.patch.object(gate, "_write_mode", return_value="scd2"),
            mock.patch.object(gate, "_diff_added_columns", return_value=None),
        ):
            exit_code = gate.main([])
        assert exit_code == 1
        assert "reconcile_scope" in capsys.readouterr().out


class TestRealRepoIntegration:
    """Real (committed) repo state -- read-only, never mutated."""

    def test_real_repo_passes_with_zero_failures(self) -> None:
        failed: list[str] = []
        gate.validate_reconcile_pending_gate(failed)
        assert failed == [], failed

    def test_ops_table_ids_real(self) -> None:
        ids = gate._ops_table_ids(gate._common.ROOT)
        assert set(ids) == {
            "ops_recommendations",
            "ops_decisions",
            "ops_entity_counters",
            "ops_priority_queue",
            "ops_execution_plans",
            "ops_smoke_events",
            "telemetry_sessions",
            "telemetry_observations",
            "telemetry_transcripts",
            "telemetry_agents",
            "ops_tenants",
            "ops_projects",
        }

    def test_declared_columns_real_contract_table(self) -> None:
        sidecar = gate._load_sidecar(gate._common.ROOT)
        cols = gate._declared_columns("ops_decisions", sidecar, gate._common.ROOT)
        assert cols is not None and "intent" in cols

    def test_declared_columns_real_dormant_table(self) -> None:
        sidecar = gate._load_sidecar(gate._common.ROOT)
        cols = gate._declared_columns("ops_priority_queue", sidecar, gate._common.ROOT)
        assert cols is not None and "rec_id" in cols

    def test_declared_columns_real_smoke_table(self) -> None:
        sidecar = gate._load_sidecar(gate._common.ROOT)
        cols = gate._declared_columns("ops_smoke_events", sidecar, gate._common.ROOT)
        assert cols is not None and "event_id" in cols

    def test_declared_columns_unknown_table_returns_none(self) -> None:
        sidecar = gate._load_sidecar(gate._common.ROOT)
        assert gate._declared_columns("ops_does_not_exist", sidecar, gate._common.ROOT) is None

    def test_write_mode_real(self) -> None:
        sidecar = gate._load_sidecar(gate._common.ROOT)
        assert gate._write_mode("ops_smoke_events", sidecar) == "append_only"
        assert gate._write_mode("ops_decisions", sidecar) == "scd2"

    def test_pending_reconcile_map_real(self) -> None:
        """ops_decisions.intent was reconciled (VP step 10, 2026-07-26): pending_reconcile is
        empty and _blocking_tables reports no reconcile_scope=ducklake table as blocked."""
        sidecar = gate._load_sidecar(gate._common.ROOT)
        pending = gate._pending_reconcile_map(sidecar)
        assert pending["ops_decisions"]["history"] == []
        assert pending["ops_decisions"]["current"] == []
        assert gate._blocking_tables(sidecar) == []

    def test_diff_added_columns_real_repo_returns_dict_or_none(self) -> None:
        result = gate._diff_added_columns(gate._common.ROOT)
        assert result is None or isinstance(result, dict)


def _telemetry_sidecar(history: list[str]) -> dict:
    entry = {"reconcile_scope": "telemetry_catalog", "pending_reconcile": {"history": history}}
    return _sidecar({"telemetry_agents": entry, "ops_decisions": {"reconcile_scope": "ducklake", "pending_reconcile": {}}})


def test_telemetry_hold_reports_pending_production_columns(capsys) -> None:
    ducklake_only = _telemetry_sidecar([])
    ducklake_only["contract_table_ops"]["ops_decisions"]["pending_reconcile"] = {"history": ["intent"]}
    marker = "TELEMETRY_PRODUCTION_HOLD pending=1 columns=['telemetry_agents.host_run_ref']"
    for sidecar, line, marked in (
        (_telemetry_sidecar(["host_run_ref"]), "held=true", True),
        (_telemetry_sidecar([]), "held=false", False),
        (ducklake_only, "held=false", False),
    ):
        with mock.patch.object(gate, "_load_sidecar", return_value=sidecar):
            assert gate.main(["--telemetry-hold"]) == 0
        captured = capsys.readouterr()
        assert captured.out.splitlines() == [line] and (marker in captured.err) is marked
    with mock.patch.object(gate, "_load_sidecar", return_value=_telemetry_sidecar(["host_run_ref"])):
        assert gate.main(["--deploy-gate"]) == 0  # the ops serving gate ignores a telemetry column
    capsys.readouterr()
    broken = [OSError("x"), yaml.YAMLError("x"), {"contract_table_ops": []}, {}]
    with mock.patch.object(gate, "_load_sidecar", side_effect=broken):
        assert [gate.main(["--telemetry-hold"]) for _ in broken] == [1, 1, 1, 1]
    assert "held=" not in capsys.readouterr().out
