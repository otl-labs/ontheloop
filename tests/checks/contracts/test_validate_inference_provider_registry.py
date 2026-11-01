"""Tests for validate_inference_provider_registry() -- PLAN-class-d-contract-enforcers.

Covers: green path against a fixture matching model_registry.py, a provider-drift red path, a
retired/valid-overlap red path, ABSENT/EMPTY valid_providers red paths, a missing-file red path,
and a malformed-YAML red path.
"""

from __future__ import annotations

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
