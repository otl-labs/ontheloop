"""Tests for validate_read_engine_verbs() -- PLAN-class-d-contract-enforcers (rec-3059/rec-3095).

Covers: green path against a fixture matching the live NAMED_READS grouping, a drift red path
(a verb removed), an ABSENT/EMPTY-target red path (the whole read_surface block missing), a
missing-file red path, and a malformed-YAML red path.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.checks import registry
from scripts.checks.contracts.validate_read_engine_verbs import _live_verbs, validate_read_engine_verbs

_CONTRACT_NAME = "read-engine.yaml"


def _write_contract(contracts_dir: Path, verbs: dict[str, list[str]]) -> None:
    doc = {"read_surface": {"named_verbs": {"verbs": verbs}}}
    (contracts_dir / _CONTRACT_NAME).write_text(yaml.dump(doc), encoding="utf-8")


class TestGreenPath:
    def test_matching_grouping_passes(self, tmp_path: Path) -> None:
        _write_contract(tmp_path, _live_verbs())

        failed: list[str] = []
        validate_read_engine_verbs(failed, contracts_dir=tmp_path)

        assert failed == []


class TestDriftRedPath:
    def test_removed_verb_fails(self, tmp_path: Path) -> None:
        live = _live_verbs()
        declared = {table: list(verbs) for table, verbs in live.items()}
        declared["ops_recommendations"] = [v for v in declared["ops_recommendations"] if v != "rec_history"]
        _write_contract(tmp_path, declared)

        failed: list[str] = []
        validate_read_engine_verbs(failed, contracts_dir=tmp_path)

        assert any("NAMED_READS, EVENT_READS and CONTENT_READS derive" in f for f in failed)

    def test_mistabled_verb_fails(self, tmp_path: Path) -> None:
        live = _live_verbs()
        declared = {table: list(verbs) for table, verbs in live.items()}
        declared["ops_decisions"].append("open_recs")
        declared["ops_recommendations"] = [v for v in declared["ops_recommendations"] if v != "open_recs"]
        _write_contract(tmp_path, declared)

        failed: list[str] = []
        validate_read_engine_verbs(failed, contracts_dir=tmp_path)

        assert failed != []


def test_fails_when_an_event_verb_is_undeclared(tmp_path: Path) -> None:
    live = _live_verbs()
    assert "telemetry_health_count" in live["telemetry_sessions"]
    declared = {table: [v for v in verbs if v != "telemetry_health_count"] for table, verbs in live.items()}
    _write_contract(tmp_path, declared)

    failed: list[str] = []
    validate_read_engine_verbs(failed, contracts_dir=tmp_path)

    assert any("EVENT_READS" in f for f in failed)


def test_fails_when_an_event_verb_is_extra(tmp_path: Path) -> None:
    declared = {table: list(verbs) for table, verbs in _live_verbs().items()}
    declared["telemetry_sessions"].append("not_a_registered_event_verb")
    _write_contract(tmp_path, declared)

    failed: list[str] = []
    validate_read_engine_verbs(failed, contracts_dir=tmp_path)

    assert failed != []


class TestAbsentEmptyTarget:
    def test_missing_read_surface_block_fails(self, tmp_path: Path) -> None:
        (tmp_path / _CONTRACT_NAME).write_text(yaml.dump({"version": 3}), encoding="utf-8")

        failed: list[str] = []
        validate_read_engine_verbs(failed, contracts_dir=tmp_path)

        assert any("missing or empty" in f for f in failed)

    def test_empty_verbs_mapping_fails(self, tmp_path: Path) -> None:
        _write_contract(tmp_path, {})

        failed: list[str] = []
        validate_read_engine_verbs(failed, contracts_dir=tmp_path)

        assert any("missing or empty" in f for f in failed)

    def test_non_list_verb_group_fails(self, tmp_path: Path) -> None:
        doc = {"read_surface": {"named_verbs": {"verbs": {"ops_recommendations": "not-a-list"}}}}
        (tmp_path / _CONTRACT_NAME).write_text(yaml.dump(doc), encoding="utf-8")

        failed: list[str] = []
        validate_read_engine_verbs(failed, contracts_dir=tmp_path)

        assert any("must all be lists" in f for f in failed)


class TestRealContractRoster:
    def test_real_contract_matches_registry_and_names_v4_verbs(self) -> None:
        contracts_dir = Path(__file__).resolve().parents[3] / "docs" / "contracts"
        doc = yaml.safe_load((contracts_dir / _CONTRACT_NAME).read_text(encoding="utf-8"))
        declared = doc["read_surface"]["named_verbs"]["verbs"]["ops_recommendations"]

        assert {"recs_by_ids", "recs_for_files", "open_recs_tally", "preflight_signal_rows", "rec_by_id"} <= set(declared)
        failed: list[str] = []
        validate_read_engine_verbs(failed, contracts_dir=contracts_dir)
        assert failed == []

    def test_real_contract_names_recs_related(self) -> None:
        contracts_dir = Path(__file__).resolve().parents[3] / "docs" / "contracts"
        doc = yaml.safe_load((contracts_dir / _CONTRACT_NAME).read_text(encoding="utf-8"))
        named_verbs = doc["read_surface"]["named_verbs"]

        assert "recs_related" in named_verbs["verbs"]["ops_recommendations"]
        assert {"recs_for_files", "open_recs"} <= set(named_verbs["deprecated_verbs"])


class TestMissingFile:
    def test_missing_contract_file_fails(self, tmp_path: Path) -> None:
        failed: list[str] = []
        validate_read_engine_verbs(failed, contracts_dir=tmp_path)

        assert any("not found" in f for f in failed)


class TestMalformedYaml:
    def test_malformed_yaml_fails(self, tmp_path: Path) -> None:
        (tmp_path / _CONTRACT_NAME).write_text("read_surface: [unterminated", encoding="utf-8")

        failed: list[str] = []
        validate_read_engine_verbs(failed, contracts_dir=tmp_path)

        assert any("could not read/parse" in f for f in failed)


class TestWiring:
    def test_wiring_registered_in_pre_and_full(self) -> None:
        pre_names = {step.name for step in registry.pre_sequence() if step.kind == "check"}
        full_names = {step.name for step in registry.full_sequence() if step.kind == "check"}

        assert "validate_read_engine_verbs" in pre_names
        assert "validate_read_engine_verbs" in full_names

    def test_wiring_resolves_via_registry(self) -> None:
        resolved = registry.resolve("validate_read_engine_verbs")

        assert callable(resolved)
        assert resolved is validate_read_engine_verbs


def test_fails_when_a_content_verb_is_undeclared(tmp_path: Path) -> None:
    live = _live_verbs()
    assert "session_transcript_masked" in live["telemetry_transcripts"]
    declared = {table: [v for v in verbs if v != "session_transcript_masked"] for table, verbs in live.items()}
    _write_contract(tmp_path, declared)

    failed: list[str] = []
    validate_read_engine_verbs(failed, contracts_dir=tmp_path)

    assert any("CONTENT_READS" in f for f in failed)
