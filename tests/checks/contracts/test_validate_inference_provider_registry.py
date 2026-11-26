"""Tests for validate_inference_provider_registry() -- PLAN-class-d-contract-enforcers.

Covers: green path against a fixture matching model_registry.py, a provider-drift red path, a
retired/valid-overlap red path, ABSENT/EMPTY valid_providers red paths, a missing-file red path,
and a malformed-YAML red path.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from scripts.checks import registry
from scripts.checks.contracts.validate_inference_provider_registry import validate_inference_provider_registry
from scripts.llm import model_registry

_CONTRACT_NAME = "inference-provider.yaml"

_VALID_RETIRED = [{"id": "bedrock", "reason": "RETIRED"}, {"id": "copilot-sdk", "reason": "RETIRED"}]


def _write_contract(contracts_dir: Path, valid_providers: list, default: str, retired: list) -> None:
    doc = {
        "provider_defaults": {"executor_path": {"valid_providers": valid_providers, "default": default}},
        "retired_providers": retired,
    }
    (contracts_dir / _CONTRACT_NAME).write_text(yaml.dump(doc), encoding="utf-8")


class TestGreenPath:
    def test_matching_registry_passes(self, tmp_path: Path) -> None:
        _write_contract(
            tmp_path,
            list(model_registry._VALID_PROVIDERS),
            model_registry._DEFAULT_EXECUTOR_PROVIDER,
            _VALID_RETIRED,
        )

        failed: list[str] = []
        validate_inference_provider_registry(failed, contracts_dir=tmp_path)

        assert failed == []


class TestProviderDrift:
    def test_divergent_valid_providers_fails(self, tmp_path: Path) -> None:
        _write_contract(tmp_path, ["gemini", "bedrock"], "gemini", _VALID_RETIRED)

        failed: list[str] = []
        validate_inference_provider_registry(failed, contracts_dir=tmp_path)

        assert any("declares valid_providers=" in f for f in failed)

    def test_divergent_default_fails(self, tmp_path: Path) -> None:
        _write_contract(tmp_path, list(model_registry._VALID_PROVIDERS), "bedrock", _VALID_RETIRED)

        failed: list[str] = []
        validate_inference_provider_registry(failed, contracts_dir=tmp_path)

        assert failed != []


class TestRetiredValidOverlap:
    def test_retired_provider_also_valid_fails(self, tmp_path: Path) -> None:
        _write_contract(
            tmp_path,
            list(model_registry._VALID_PROVIDERS),
            model_registry._DEFAULT_EXECUTOR_PROVIDER,
            [{"id": "gemini", "reason": "should not be retired and valid"}],
        )

        failed: list[str] = []
        validate_inference_provider_registry(failed, contracts_dir=tmp_path)

        assert any("retired_providers overlap valid providers" in f for f in failed)


class TestAbsentEmptyTarget:
    def test_missing_provider_defaults_fails(self, tmp_path: Path) -> None:
        doc = {"retired_providers": _VALID_RETIRED}
        (tmp_path / _CONTRACT_NAME).write_text(yaml.dump(doc), encoding="utf-8")

        failed: list[str] = []
        validate_inference_provider_registry(failed, contracts_dir=tmp_path)

        assert any("missing or empty top-level 'provider_defaults'" in f for f in failed)

    def test_missing_retired_providers_fails(self, tmp_path: Path) -> None:
        doc = {
            "provider_defaults": {
                "executor_path": {"valid_providers": list(model_registry._VALID_PROVIDERS), "default": "gemini"}
            }
        }
        (tmp_path / _CONTRACT_NAME).write_text(yaml.dump(doc), encoding="utf-8")

        failed: list[str] = []
        validate_inference_provider_registry(failed, contracts_dir=tmp_path)

        assert any("missing or empty top-level 'retired_providers'" in f for f in failed)

    def test_empty_valid_providers_fails(self, tmp_path: Path) -> None:
        doc = {"provider_defaults": {"executor_path": {"valid_providers": [], "default": "gemini"}}}
        (tmp_path / _CONTRACT_NAME).write_text(yaml.dump(doc), encoding="utf-8")

        failed: list[str] = []
        validate_inference_provider_registry(failed, contracts_dir=tmp_path)

        assert any("missing a non-empty valid_providers list or default string" in f for f in failed)


class TestMissingFile:
    def test_missing_contract_file_fails(self, tmp_path: Path) -> None:
        failed: list[str] = []
        validate_inference_provider_registry(failed, contracts_dir=tmp_path)

        assert any("not found" in f for f in failed)


class TestMalformedYaml:
    def test_malformed_yaml_fails(self, tmp_path: Path) -> None:
        (tmp_path / _CONTRACT_NAME).write_text("provider_defaults: [unterminated", encoding="utf-8")

        failed: list[str] = []
        validate_inference_provider_registry(failed, contracts_dir=tmp_path)

        assert any("could not read/parse" in f for f in failed)

    def test_non_mapping_yaml_fails(self, tmp_path: Path) -> None:
        (tmp_path / _CONTRACT_NAME).write_text("- a\n- b\n", encoding="utf-8")

        failed: list[str] = []
        validate_inference_provider_registry(failed, contracts_dir=tmp_path)

        assert any("is not a YAML mapping" in f for f in failed)


class TestWiring:
    def test_wiring_registered_in_pre_and_full(self) -> None:
        pre_names = {step.name for step in registry.pre_sequence() if step.kind == "check"}
        full_names = {step.name for step in registry.full_sequence() if step.kind == "check"}

        assert "validate_inference_provider_registry" in pre_names
        assert "validate_inference_provider_registry" in full_names

    def test_wiring_resolves_via_registry(self) -> None:
        resolved = registry.resolve("validate_inference_provider_registry")

        assert callable(resolved)
        assert resolved is validate_inference_provider_registry


class TestPassLineOutput:
    """The PASS summary is the ONLY test-observable consequence of the `len(failed) ==
    error_count_before` accounting branch (registry.examined() runs unconditionally above it)."""

    def test_clean_fixture_prints_the_pass_line(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _write_contract(
            tmp_path,
            list(model_registry._VALID_PROVIDERS),
            model_registry._DEFAULT_EXECUTOR_PROVIDER,
            _VALID_RETIRED,
        )

        failed: list[str] = []
        validate_inference_provider_registry(failed, contracts_dir=tmp_path)

        assert failed == []
        out = capsys.readouterr().out
        expected = "  PASS: inference-provider.yaml provider_defaults and retired_providers agree with model_registry.py."
        assert expected in out


_REAL_CONTRACTS = Path(__file__).resolve().parents[3] / "docs" / "contracts"
_PROJECT_CONFIG_NAME = "project-configuration.yaml"


def _copy_real_contracts(target: Path) -> Path:
    contracts = target / "docs" / "contracts"
    contracts.mkdir(parents=True)
    for name in (_CONTRACT_NAME, _PROJECT_CONFIG_NAME, "executor-personas.yaml"):
        shutil.copy(_REAL_CONTRACTS / name, contracts / name)
    return contracts


class TestTierSelectionLeg:
    def test_real_contracts_pass_and_print_second_pass_line(self, capsys: pytest.CaptureFixture[str]) -> None:
        failed: list[str] = []
        validate_inference_provider_registry(failed, contracts_dir=_REAL_CONTRACTS)

        assert failed == []
        out = capsys.readouterr().out
        assert "  PASS: tier selection resolves " in out
        assert " persona-role pairs." in out

    def test_copies_of_real_contracts_pass(self, tmp_path: Path) -> None:
        contracts = _copy_real_contracts(tmp_path)

        failed: list[str] = []
        validate_inference_provider_registry(failed, contracts_dir=contracts)

        assert failed == []

    def test_same_vendor_fixture_fails_naming_rule(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        contracts = _copy_real_contracts(tmp_path)
        provider = yaml.safe_load((contracts / _CONTRACT_NAME).read_text(encoding="utf-8"))
        config = yaml.safe_load((contracts / _PROJECT_CONFIG_NAME).read_text(encoding="utf-8"))
        rows = provider["model_id_formats"]["litellm_tier_models"]
        for field in ("provider", "models", "default_model"):
            rows["tier_2"][field] = rows["tier_1"][field]
        by_id = {r["id"]: r for r in config["settings"]}
        by_id["ports.run_persona.litellm.api_key.warm_fetched_escape_hatch"]["variable"] = by_id[
            "ports.run_persona.litellm.api_key.primary"
        ]["variable"]
        (contracts / _CONTRACT_NAME).write_text(yaml.safe_dump(provider), encoding="utf-8")
        (contracts / _PROJECT_CONFIG_NAME).write_text(yaml.safe_dump(config), encoding="utf-8")

        failed: list[str] = []
        validate_inference_provider_registry(failed, contracts_dir=contracts)

        assert any("2(b) vendor_independence" in f for f in failed)
        assert "PASS" not in capsys.readouterr().out

    def test_missing_project_configuration_fails_naming_file(self, tmp_path: Path) -> None:
        contracts = _copy_real_contracts(tmp_path)
        (contracts / _PROJECT_CONFIG_NAME).unlink()

        failed: list[str] = []
        validate_inference_provider_registry(failed, contracts_dir=contracts)

        assert any(_PROJECT_CONFIG_NAME in f for f in failed)

    def test_unparseable_project_configuration_fails_naming_file(self, tmp_path: Path) -> None:
        contracts = _copy_real_contracts(tmp_path)
        (contracts / _PROJECT_CONFIG_NAME).write_text("a: [unclosed\n", encoding="utf-8")

        failed: list[str] = []
        validate_inference_provider_registry(failed, contracts_dir=contracts)

        assert any(_PROJECT_CONFIG_NAME in f for f in failed)

    def test_fixture_without_tier_model_prints_no_second_line(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_contract(
            tmp_path,
            list(model_registry._VALID_PROVIDERS),
            model_registry._DEFAULT_EXECUTOR_PROVIDER,
            _VALID_RETIRED,
        )

        failed: list[str] = []
        validate_inference_provider_registry(failed, contracts_dir=tmp_path)

        assert failed == []
        assert "tier selection resolves" not in capsys.readouterr().out


def test_local_server_rows_state_how_credential_holder_applies() -> None:
    """Decision 234 clause 4: a self-hosted server may hold no credential, so the axes say how they read for it."""
    path = Path(__file__).resolve().parents[3] / "docs" / "contracts" / _CONTRACT_NAME
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    axes = doc["vocabulary"]["axes"]
    holders = axes["credential_holder"]
    assert "end_user" in holders["values"]
    meaning = " ".join(holders["reserved_meaning"].split())
    assert "litellm_local_servers" in meaning and "whether or not the server takes a key" in meaning

    rows = doc["model_id_formats"]["litellm_local_servers"]
    assert set(rows) == {"ollama", "vllm"}
    for name, phrase in (("ollama", "no key by default"), ("vllm", "--api-key")):
        notes = " ".join(rows[name]["notes"].split())
        assert rows[name]["credential_holder"] == "end_user", name
        assert "end_user" in notes and phrase in notes, name
        assert "billing_shape local" in notes and "no tier role" in notes, name

    shape = axes["billing_shape"]
    assert "local" in shape["values_ref"] or "local" in doc["tier_model"]["billing_shape_vocabulary"]
    assert "local" in " ".join(shape["reserved_meaning"].split())
    assert "local never names a local adapter" in " ".join(shape["do_not_use_for"].split())

    failed: list[str] = []
    validate_inference_provider_registry(failed, contracts_dir=path.parent)
    assert failed == []


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _run(contracts: Path) -> list[str]:
    failed: list[str] = []
    validate_inference_provider_registry(failed, contracts_dir=contracts)
    return failed


def _config_row(config: dict, row_id: str) -> dict:
    return next(r for r in config["settings"] if r.get("id") == row_id)


class TestCapabilityClass:
    def _edit(self, contracts: Path, name: str, fn) -> None:
        doc = _load(contracts / name)
        fn(doc)
        (contracts / name).write_text(yaml.safe_dump(doc), encoding="utf-8")

    def test_real_tree_passes(self) -> None:
        assert _run(_REAL_CONTRACTS) == []

    def test_copy_passes(self, tmp_path: Path) -> None:
        assert _run(_copy_real_contracts(tmp_path)) == []

    def test_registry_persona_with_no_member_fails(self, tmp_path: Path) -> None:
        contracts = _copy_real_contracts(tmp_path)

        def drop(c: dict) -> None:
            members = _config_row(c, "project.capability_class.members")["defaults"]
            del members["local"]["implement_agent"], members["cloud"]["implement_agent"]

        self._edit(contracts, _PROJECT_CONFIG_NAME, drop)
        assert any("implement_agent has no member row" in f for f in _run(contracts))

    def test_rule_a_breach_fails_and_tier_leg_refusal_still_reports(self, tmp_path: Path) -> None:
        contracts = _copy_real_contracts(tmp_path)

        def breach(c: dict) -> None:
            members = _config_row(c, "project.capability_class.members")["defaults"]
            members["local"]["plan_critic"] = members["cloud"]["plan_critic"] = "recall"

        self._edit(contracts, _PROJECT_CONFIG_NAME, breach)
        self._edit(contracts, _CONTRACT_NAME, lambda p: p["tier_model"]["tiers"].pop(0))
        failed = _run(contracts)
        assert any("rule (a)" in f for f in failed)
        assert any("tier selection refused" in f for f in failed)

    def test_platform_yaml_key_fails_while_current_home_null(self, tmp_path: Path) -> None:
        contracts = _copy_real_contracts(tmp_path)
        (tmp_path / "ontheloop").mkdir()
        (tmp_path / "ontheloop" / "platform.yaml").write_text("project: {capability_class: {}}\n", encoding="utf-8")
        assert any("platform.yaml" in f and "current_home is null" in f for f in _run(contracts))

    def test_unparseable_and_non_mapping_platform_yaml_fail(self, tmp_path: Path) -> None:
        contracts = _copy_real_contracts(tmp_path)
        (tmp_path / "ontheloop").mkdir()
        platform = tmp_path / "ontheloop" / "platform.yaml"
        platform.write_text("a: [unclosed\n", encoding="utf-8")
        assert any("platform.yaml" in f and "could not be read" in f for f in _run(contracts))
        platform.write_text("- a\n", encoding="utf-8")
        assert any("platform.yaml" in f and "not a YAML mapping" in f for f in _run(contracts))
        platform.write_text("other: 1\n", encoding="utf-8")
        assert _run(contracts) == []

    @pytest.mark.parametrize("mode", ["absent", "unparseable", "no_list", "bad_entry"])
    def test_executor_personas_problems_fail_naming_the_file(self, tmp_path: Path, mode: str) -> None:
        contracts = _copy_real_contracts(tmp_path)
        target = contracts / "executor-personas.yaml"
        if mode == "absent":
            target.unlink()
        elif mode == "unparseable":
            target.write_text("a: [unclosed\n", encoding="utf-8")
        elif mode == "no_list":
            target.write_text("personas: 3\n", encoding="utf-8")
        else:
            doc = _load(target)
            doc["personas"].append({"name": "x"})
            target.write_text(yaml.safe_dump(doc), encoding="utf-8")
        assert any("executor-personas.yaml" in f for f in _run(contracts))

    def test_non_mapping_shapes_become_findings(self, tmp_path: Path) -> None:
        contracts = _copy_real_contracts(tmp_path)

        def shapes(c: dict) -> None:
            classes = _config_row(c, "project.capability_class.classes")["defaults"]
            for side in ("local", "cloud"):
                classes[side]["recall"]["primary"] = "oops"
                classes[side]["build"] = "oops"
                classes[side]["reason"]["extra"] = {}
                classes[side]["consult"]["primary"]["model"] = "x"
            members = _config_row(c, "project.capability_class.members")["defaults"]
            members["local"]["recon"] = members["cloud"]["recon"] = 5

        self._edit(contracts, _PROJECT_CONFIG_NAME, shapes)
        failed = _run(contracts)
        for fragment in (
            "is not a mapping",
            "not primary or warm_fetched_escape_hatch",
            "not thinking or effort",
            "non-string class",
        ):
            assert any(fragment in f for f in failed), fragment

    def test_absent_block_and_duplicate_values_fail(self, tmp_path: Path) -> None:
        contracts = _copy_real_contracts(tmp_path)
        self._edit(contracts, _CONTRACT_NAME, lambda p: p.pop("capability_class"))
        assert any("no capability_class mapping" in f for f in _run(contracts))
        contracts2 = _copy_real_contracts(tmp_path / "two")
        self._edit(contracts2, _CONTRACT_NAME, lambda p: p["capability_class"]["values"].append("recall"))
        assert any("capability_class.values" in f for f in _run(contracts2))

    def test_unparseable_project_configuration_and_platform_free_tree_fail(self, tmp_path: Path) -> None:
        contracts = _copy_real_contracts(tmp_path)
        (contracts / _PROJECT_CONFIG_NAME).write_text("a: [unclosed\n", encoding="utf-8")
        assert any("capability_class leg" in f and _PROJECT_CONFIG_NAME in f for f in _run(contracts))

    def test_missing_project_configuration_reports_and_executor_checks_still_run(self, tmp_path: Path) -> None:
        contracts = _copy_real_contracts(tmp_path)
        (contracts / _PROJECT_CONFIG_NAME).unlink()
        (contracts / "executor-personas.yaml").unlink()
        failed = [f for f in _run(contracts) if "capability_class leg" in f]
        assert any(_PROJECT_CONFIG_NAME in f for f in failed)
        assert any("executor-personas.yaml" in f for f in failed)

    def test_rule_a_scout_above_critic_fails(self, tmp_path: Path) -> None:
        contracts = _copy_real_contracts(tmp_path)

        def invert(c: dict) -> None:
            members = _config_row(c, "project.capability_class.members")["defaults"]
            for side in ("local", "cloud"):
                members[side]["plan_critic"], members[side]["decision_scout"] = "recall", "reason"

        self._edit(contracts, _PROJECT_CONFIG_NAME, invert)
        assert any("rule (a)" in f for f in _run(contracts))
