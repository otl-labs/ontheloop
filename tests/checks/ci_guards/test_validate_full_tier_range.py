"""Mirror test for scripts/checks/ci_guards/validate_full_tier_range.py (Class D evaluator, Decision 168)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import scripts.checks._common as _common
from scripts.checks import registry
from scripts.checks.ci_guards import validate_full_tier_range as v

REAL_ROOT = Path(__file__).resolve().parents[3]
CONTRACT = Path("docs/contracts/full-tier-range.yaml")
TAXONOMY = Path("config/ci_rca_taxonomy.yaml")
LABEL = "Full-tier range contract"


def _tree(
    tmp_path: Path,
    contract_edit: tuple[str, str] | None = None,
    taxonomy_edit: tuple[str, str] | None = None,
) -> Path:
    for rel, edit in ((CONTRACT, contract_edit), (TAXONOMY, taxonomy_edit)):
        text = (REAL_ROOT / rel).read_text(encoding="utf-8")
        if edit:
            assert edit[0] in text
            text = text.replace(*edit)
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return tmp_path


def _run(root: Path) -> list[str]:
    failed: list[str] = []
    with patch.object(_common, "ROOT", root):
        v.validate_full_tier_range(failed)
    return failed


def test_live_tree_is_green() -> None:
    failed: list[str] = []
    v.validate_full_tier_range(failed)
    assert failed == []


def test_tmp_copy_is_green(tmp_path: Path) -> None:
    assert _run(_tree(tmp_path)) == []


def test_renamed_variable_is_red(tmp_path: Path) -> None:
    assert _run(_tree(tmp_path, ("FULL_TIER_PREV:", "FULL_TIER_PREVIOUS:"))) == [LABEL]


def test_fingerprint_sibling_tuple_drift_is_red(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(v.fingerprint, "FULL_TIER_SIBLING_SLUGS", ("ci",))
    assert _run(_tree(tmp_path)) == [LABEL]


def test_extra_contract_sibling_slug_is_red(tmp_path: Path) -> None:
    assert _run(_tree(tmp_path, ("slugs: [ci, main_canary]", "slugs: [ci, main_canary, rec_autoclose]"))) == [LABEL]


def test_sibling_slug_missing_from_taxonomy_is_red(tmp_path: Path) -> None:
    assert _run(_tree(tmp_path, taxonomy_edit=("  Main Canary:\n    tier: CI", "  Main Canary:\n    tier: not_a_gate"))) == [
        LABEL
    ]


def test_job_name_drift_is_red(tmp_path: Path) -> None:
    assert _run(_tree(tmp_path, ("job: main-validate", "job: main-validation"))) == [LABEL]


def test_step_name_drift_is_red(tmp_path: Path) -> None:
    assert _run(_tree(tmp_path, ('step: "Validate full tier', 'step: "Validate the full tier'))) == [LABEL]


def test_missing_contract_is_red(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / CONTRACT).unlink()
    assert _run(root) == [LABEL]


def test_registered_in_fast_tier() -> None:
    assert "validate_full_tier_range" in [step.name for step in registry.pre_sequence()]
    assert registry.resolve("validate_full_tier_range") is v.validate_full_tier_range


def test_non_mapping_contract_is_red(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / CONTRACT).write_text("- a\n- b\n", encoding="utf-8")
    assert _run(root) == [LABEL]
