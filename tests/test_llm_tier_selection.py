"""Tests for scripts/llm/tier_selection.py and the two contracts it reads."""

from __future__ import annotations

import copy
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.llm import tier_selection as ts

_ROOT = Path(__file__).resolve().parents[1]
_CONTRACTS = ("docs/contracts/inference-provider.yaml", "docs/contracts/project-configuration.yaml")
_PRIMARY_KEY_ROW = "ports.run_persona.litellm.api_key.primary"
_FALLBACK_KEY_ROW = "ports.run_persona.litellm.api_key.warm_fetched_escape_hatch"


@pytest.fixture
def real() -> tuple[dict[str, Any], dict[str, Any]]:
    provider, config = ts.load(_ROOT)
    return copy.deepcopy(provider), copy.deepcopy(config)


def _resolve(pair: tuple[dict[str, Any], dict[str, Any]], persona: str, role: str, **kw: Any) -> ts.Selection:
    return ts.resolve(persona, role, inference_provider=pair[0], project_configuration=pair[1], **kw)


def _tiers(provider: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {t["role"]: t for t in provider["tier_model"]["tiers"]}


def _models(provider: dict[str, Any]) -> dict[str, Any]:
    return provider["model_id_formats"]["litellm_tier_models"]


def _key_row(config: dict[str, Any], row_id: str) -> dict[str, Any]:
    return next(r for r in config["settings"] if r.get("id") == row_id)


def _same_vendor(provider: dict[str, Any], config: dict[str, Any]) -> None:
    rows = _models(provider)
    for field in ("provider", "models", "default_model"):
        rows["tier_2"][field] = copy.deepcopy(rows["tier_1"][field])
    _key_row(config, _FALLBACK_KEY_ROW)["variable"] = _key_row(config, _PRIMARY_KEY_ROW)["variable"]


def test_real_tree_resolves_every_persona_and_role(real) -> None:
    provider, config = real
    persona_block = provider["model_id_formats"]["litellm_persona_selection"]["personas"]
    selections = ts.resolve_all(inference_provider=provider, project_configuration=config)
    assert len(selections) >= 6
    seen = {(s.persona, s.role) for s in selections}
    for persona in ("decision_scout", "plan_critic", "code_reviewer"):
        for role in ("primary", "warm_fetched_escape_hatch"):
            assert (persona, role) in seen
    for s in selections:
        tier = _tiers(provider)[s.role]
        row = _models(provider)[tier["selection_ref"].rsplit(".", 1)[-1]]
        assert s.model == row["default_model"]
        assert s.thinking == persona_block[s.persona][s.role]["thinking"]
        assert s.effort == persona_block[s.persona][s.role]["effort"]
        assert s.billing_shape == tier["billing_shape"]
        assert s.cold_start_assertion == tier["cold_start_assertion"]
        assert s.env_name == _key_row(config, ts._ENV_ROW_PREFIX + s.role)["variable"]


def test_end_user_resolves_primary_alone(real) -> None:
    provider, config = real
    selections = ts.resolve_all(inference_provider=provider, project_configuration=config, credential_holder="end_user")
    assert selections and {s.role for s in selections} == {"primary"}
    with pytest.raises(ts.TierSelectionError, match="end_user_primary_only"):
        _resolve(real, "decision_scout", "warm_fetched_escape_hatch", credential_holder="end_user")


def test_unknown_role_refused(real) -> None:
    with pytest.raises(ts.TierSelectionError, match="unknown_role"):
        _resolve(real, "decision_scout", "aggregator")


def test_unknown_persona_refused(real) -> None:
    with pytest.raises(ts.TierSelectionError, match="unknown_persona"):
        _resolve(real, "nobody", "primary")


def test_credential_holder_outside_vocabulary_refused(real) -> None:
    with pytest.raises(ts.TierSelectionError, match="credential_holder_vocabulary"):
        _resolve(real, "decision_scout", "primary", credential_holder="stranger")


def test_deferred_tier_refused(real) -> None:
    tier = _tiers(real[0])["primary"]
    tier["state"] = "Deferred"
    tier["cold_start_assertion"] = "not_applicable"
    with pytest.raises(ts.TierSelectionError, match="tier_not_committed"):
        _resolve(real, "decision_scout", "primary")


def test_missing_tier_row_refused(real) -> None:
    real[0]["tier_model"]["tiers"] = [t for t in real[0]["tier_model"]["tiers"] if t["role"] != "primary"]
    with pytest.raises(ts.TierSelectionError, match="tier_not_committed"):
        _resolve(real, "decision_scout", "primary")


def test_default_model_missing_refused(real) -> None:
    del _models(real[0])["tier_1"]["default_model"]
    with pytest.raises(ts.TierSelectionError, match="tier_models_row"):
        _resolve(real, "decision_scout", "primary")


def test_default_model_not_in_models_refused(real) -> None:
    _models(real[0])["tier_1"]["models"] = ["other/model"]
    with pytest.raises(ts.TierSelectionError, match="tier_models_row"):
        _resolve(real, "decision_scout", "primary")


def test_mixed_prefixes_refused(real) -> None:
    row = _models(real[0])["tier_1"]
    row["models"] = [row["default_model"], "other/model"]
    with pytest.raises(ts.TierSelectionError, match="tier_models_row"):
        _resolve(real, "decision_scout", "primary")


def test_retired_provider_refused(real) -> None:
    provider, config = real
    row = _models(provider)["tier_2"]
    prefix = row["default_model"].split("/", 1)[0]
    provider["retired_providers"].append({"id": prefix, "reason": "test"})
    with pytest.raises(ts.TierSelectionError, match=r"2\(a\) retired_provider"):
        _resolve(real, "decision_scout", "warm_fetched_escape_hatch")


def test_provider_prefix_mismatch_refused(real) -> None:
    _models(real[0])["tier_1"]["provider"] = "Elsewhere-direct"
    with pytest.raises(ts.TierSelectionError, match="provider_prefix_mismatch"):
        _resolve(real, "decision_scout", "primary")


def test_same_vendor_refused(real) -> None:
    _same_vendor(*real)
    with pytest.raises(ts.TierSelectionError, match=r"2\(b\) vendor_independence"):
        _resolve(real, "decision_scout", "primary")


def test_primary_billing_shape_refused(real) -> None:
    tier = _tiers(real[0])["primary"]
    tier["billing_shape"] = "fixed_non_rollover_allowance"
    tier["cold_start_assertion"] = "remaining_allowance"
    with pytest.raises(ts.TierSelectionError, match=r"2\(d\) primary_billing_shape"):
        _resolve(real, "decision_scout", "primary")


def test_local_billing_shape_refused(real) -> None:
    _tiers(real[0])["warm_fetched_escape_hatch"]["billing_shape"] = "local"
    with pytest.raises(ts.TierSelectionError, match="local_billing_shape"):
        _resolve(real, "decision_scout", "warm_fetched_escape_hatch")


def test_cold_start_assertion_refused(real) -> None:
    _tiers(real[0])["warm_fetched_escape_hatch"]["cold_start_assertion"] = "credential_liveness"
    with pytest.raises(ts.TierSelectionError, match="clause4 cold_start_assertion"):
        _resolve(real, "decision_scout", "warm_fetched_escape_hatch")


def test_persona_missing_role_refused(real) -> None:
    del real[0]["model_id_formats"]["litellm_persona_selection"]["personas"]["plan_critic"]["primary"]
    with pytest.raises(ts.TierSelectionError, match="persona_settings"):
        _resolve(real, "plan_critic", "primary")


@pytest.mark.parametrize("key", ["thinking", "effort"])
def test_setting_outside_vocabulary_refused(real, key: str) -> None:
    real[0]["model_id_formats"]["litellm_persona_selection"]["personas"]["plan_critic"]["primary"][key] = "bogus"
    with pytest.raises(ts.TierSelectionError, match="persona_settings"):
        _resolve(real, "plan_critic", "primary")


def test_missing_api_key_row_refused(real) -> None:
    real[1]["settings"] = [r for r in real[1]["settings"] if r.get("id") != _PRIMARY_KEY_ROW]
    with pytest.raises(ts.TierSelectionError, match="api_key_row"):
        _resolve(real, "decision_scout", "primary")


def test_wrong_env_name_refused(real) -> None:
    _key_row(real[1], _PRIMARY_KEY_ROW)["variable"] = "WRONG_VARIABLE"
    with pytest.raises(ts.TierSelectionError, match="api_key_row"):
        _resolve(real, "decision_scout", "primary")


@pytest.mark.parametrize("missing", [0, 1])
def test_load_missing_file_names_path(tmp_path: Path, missing: int) -> None:
    for i, rel in enumerate(_CONTRACTS):
        if i != missing:
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(_ROOT / rel, tmp_path / rel)
    with pytest.raises(ts.TierSelectionError, match=_CONTRACTS[missing].rsplit("/", 1)[-1]):
        ts.load(tmp_path)


def test_load_unparseable_file_names_path(tmp_path: Path) -> None:
    for rel in _CONTRACTS:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(_ROOT / rel, tmp_path / rel)
    (tmp_path / _CONTRACTS[1]).write_text("a: [unclosed\n", encoding="utf-8")
    with pytest.raises(ts.TierSelectionError, match="project-configuration.yaml"):
        ts.load(tmp_path)


def test_cli_check_real_tree(capsys: pytest.CaptureFixture[str]) -> None:
    assert ts.main(["--check"]) == 0
    out = capsys.readouterr().out
    assert "decision_scout primary " in out
    assert out.rstrip().endswith("TIER-SELECTION OK")


def test_cli_check_same_vendor_fixture(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    provider, config = ts.load(_ROOT)
    _same_vendor(provider, config)
    for rel, data in zip(_CONTRACTS, (provider, config), strict=True):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(yaml.safe_dump(data), encoding="utf-8")
    assert ts.main(["--check", "--root", str(tmp_path)]) == 1
    assert "2(b) vendor_independence" in capsys.readouterr().out


def test_module_runs_as_main(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import runpy

    monkeypatch.setattr("sys.argv", ["tier_selection", "--check"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("scripts.llm.tier_selection", run_name="__main__")
    assert exit_info.value.code == 0
    assert "TIER-SELECTION OK" in capsys.readouterr().out


def test_load_non_mapping_file_names_path(tmp_path: Path) -> None:
    for rel in _CONTRACTS:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(_ROOT / rel, tmp_path / rel)
    (tmp_path / _CONTRACTS[0]).write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ts.TierSelectionError, match="inference-provider.yaml"):
        ts.load(tmp_path)
