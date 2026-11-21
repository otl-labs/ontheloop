"""Tests for validate_telemetry_lexicon_tables() -- PLAN-class-d-contract-enforcers.

Covers: green path against real Class A telemetry fixtures, a missing-Class-A-target red path,
a bad-collapses-target red path, ABSENT/EMPTY canonical_tables red paths, a missing-file red
path, and a malformed-YAML red path.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.checks import registry
from scripts.checks.contracts.validate_telemetry_lexicon_tables import validate_telemetry_lexicon_tables

_CONTRACT_NAME = "telemetry-lexicon.yaml"


def _write_class_a(contracts_dir: Path, table: str) -> None:
    doc = {"contract": {"id": table, "class": "A", "contract_version": 1, "status": "ratified"}}
    (contracts_dir / f"{table}.yaml").write_text(yaml.dump(doc), encoding="utf-8")


def _write_lexicon(
    contracts_dir: Path,
    canonical_tables: list[str],
    collapses: dict[str, str] | None = None,
) -> None:
    doc = {"lexicon": {"model": {"canonical_tables": canonical_tables, "collapses": collapses or {}}}}
    (contracts_dir / _CONTRACT_NAME).write_text(yaml.dump(doc), encoding="utf-8")


class TestGreenPath:
    def test_all_tables_resolve_and_collapses_are_canonical(self, tmp_path: Path) -> None:
        for table in ("telemetry_sessions", "telemetry_observations"):
            _write_class_a(tmp_path, table)
        _write_lexicon(
            tmp_path,
            ["telemetry_sessions", "telemetry_observations"],
            {"telemetry_phases": "telemetry_observations (observation_type=phase)"},
        )

        failed: list[str] = []
        validate_telemetry_lexicon_tables(failed, contracts_dir=tmp_path)

        assert failed == []


class TestMissingClassATarget:
    def test_canonical_table_with_no_contract_file_fails(self, tmp_path: Path) -> None:
        _write_lexicon(tmp_path, ["telemetry_sessions"])

        failed: list[str] = []
        validate_telemetry_lexicon_tables(failed, contracts_dir=tmp_path)

        assert any("do not resolve to an existing Class A contract" in f for f in failed)

    def test_canonical_table_resolving_to_wrong_class_fails(self, tmp_path: Path) -> None:
        doc = {"contract": {"id": "telemetry_sessions", "class": "D", "contract_version": 1, "status": "active"}}
        (tmp_path / "telemetry_sessions.yaml").write_text(yaml.dump(doc), encoding="utf-8")
        _write_lexicon(tmp_path, ["telemetry_sessions"])

        failed: list[str] = []
        validate_telemetry_lexicon_tables(failed, contracts_dir=tmp_path)

        assert any("do not resolve to an existing Class A contract" in f for f in failed)

    def test_canonical_table_with_malformed_own_contract_fails(self, tmp_path: Path) -> None:
        (tmp_path / "telemetry_sessions.yaml").write_text("contract: [unterminated", encoding="utf-8")
        _write_lexicon(tmp_path, ["telemetry_sessions"])

        failed: list[str] = []
        validate_telemetry_lexicon_tables(failed, contracts_dir=tmp_path)

        assert any("do not resolve to an existing Class A contract" in f for f in failed)

    def test_canonical_table_contract_file_missing_contract_block_fails(self, tmp_path: Path) -> None:
        (tmp_path / "telemetry_sessions.yaml").write_text(yaml.dump({"version": 1}), encoding="utf-8")
        _write_lexicon(tmp_path, ["telemetry_sessions"])

        failed: list[str] = []
        validate_telemetry_lexicon_tables(failed, contracts_dir=tmp_path)

        assert any("do not resolve to an existing Class A contract" in f for f in failed)


class TestBadCollapsesTarget:
    def test_collapses_target_not_canonical_fails(self, tmp_path: Path) -> None:
        _write_class_a(tmp_path, "telemetry_sessions")
        _write_lexicon(
            tmp_path,
            ["telemetry_sessions"],
            {"telemetry_phases": "telemetry_not_canonical (observation_type=phase)"},
        )

        failed: list[str] = []
        validate_telemetry_lexicon_tables(failed, contracts_dir=tmp_path)

        assert any("collapses target(s) do not name a canonical table" in f for f in failed)


class TestAbsentEmptyTarget:
    def test_missing_lexicon_block_fails(self, tmp_path: Path) -> None:
        (tmp_path / _CONTRACT_NAME).write_text(yaml.dump({"version": 1}), encoding="utf-8")

        failed: list[str] = []
        validate_telemetry_lexicon_tables(failed, contracts_dir=tmp_path)

        assert any("missing or empty" in f for f in failed)

    def test_empty_canonical_tables_fails(self, tmp_path: Path) -> None:
        _write_lexicon(tmp_path, [])

        failed: list[str] = []
        validate_telemetry_lexicon_tables(failed, contracts_dir=tmp_path)

        assert any("missing or empty" in f for f in failed)


class TestMissingFile:
    def test_missing_contract_file_fails(self, tmp_path: Path) -> None:
        failed: list[str] = []
        validate_telemetry_lexicon_tables(failed, contracts_dir=tmp_path)

        assert any("not found" in f for f in failed)


class TestMalformedYaml:
    def test_malformed_yaml_fails(self, tmp_path: Path) -> None:
        (tmp_path / _CONTRACT_NAME).write_text("lexicon: [unterminated", encoding="utf-8")

        failed: list[str] = []
        validate_telemetry_lexicon_tables(failed, contracts_dir=tmp_path)

        assert any("could not read/parse" in f for f in failed)


class TestWiring:
    def test_wiring_registered_in_pre_and_full(self) -> None:
        pre_names = {step.name for step in registry.pre_sequence() if step.kind == "check"}
        full_names = {step.name for step in registry.full_sequence() if step.kind == "check"}

        assert "validate_telemetry_lexicon_tables" in pre_names
        assert "validate_telemetry_lexicon_tables" in full_names

    def test_wiring_resolves_via_registry(self) -> None:
        resolved = registry.resolve("validate_telemetry_lexicon_tables")

        assert callable(resolved)
        assert resolved is validate_telemetry_lexicon_tables


class TestFactsEntries:
    """test_facts_entries_resolve / test_facts_entry_rejects_non_address (PLAN-telemetry-fact-index)."""

    @staticmethod
    def _setup(tmp_path: Path, terms: list[dict]) -> list[str]:
        field = {"type": "str", "iceberg_type": "string", "nullable": True, "description": "d"}
        doc = {
            "contract": {
                "id": "telemetry_transcripts",
                "class": "A",
                "contract_version": 1,
                "status": "draft",
                "description": "d",
                "projects_to": {"artifact": "a", "table": "telemetry_transcripts", "generator": "g"},
            },
            "fields": {
                "content": field,
                "purpose": {**field, "dq_intent": {"accepted_values": {"values": ["tool_input", "prompt"]}}},
            },
        }
        (tmp_path / "telemetry_transcripts.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
        lexicon = {"lexicon": {"model": {"canonical_tables": ["telemetry_transcripts"], "collapses": {}}, "terms": terms}}
        (tmp_path / _CONTRACT_NAME).write_text(yaml.dump(lexicon), encoding="utf-8")
        failed: list[str] = []
        validate_telemetry_lexicon_tables(failed, contracts_dir=tmp_path)
        return failed

    def test_facts_entries_resolve(self, tmp_path: Path) -> None:
        good = {
            "term": "t",
            "aka": ["a"],
            "facts": ["telemetry_transcripts.content[purpose=tool_input]", "telemetry_transcripts.content#skill"],
        }
        assert self._setup(tmp_path, [good]) == []
        cases = {
            "missing field": {"facts": ["telemetry_transcripts.nope"]},
            "missing discriminator": {"facts": ["telemetry_transcripts.content[nodisc=x]"]},
            "two discriminators": {"facts": ["telemetry_transcripts.content[purpose=prompt,content=x]"]},
            "value outside accepted_values": {"facts": ["telemetry_transcripts.content[purpose=bogus]"]},
            "aka case": {"aka": ["Hash"], "facts": []},
            "aka duplicate": {"aka": ["a", "a"], "facts": []},
        }
        for label, extra in cases.items():
            assert self._setup(tmp_path, [{"term": "t", **extra}]), label

    def test_facts_entry_rejects_non_address(self, tmp_path: Path) -> None:
        assert self._setup(tmp_path, [{"term": "t", "facts": [{"address": "telemetry_transcripts.content"}]}])
        assert self._setup(tmp_path, [{"term": "t", "facts": ["the content column, a text"]}])
