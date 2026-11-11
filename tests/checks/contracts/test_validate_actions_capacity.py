"""validate_actions_capacity (rec-5022): the contract's cross-reference and closed-set rules."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from scripts.checks import registry
from scripts.checks.contracts.validate_actions_capacity import validate_actions_capacity

LABEL = "Actions capacity contract"
REAL = Path(__file__).resolve().parents[3]


def _tree(tmp_path: Path, edit: Callable[[dict[str, Any]], None] | None = None, workflows: bool = True) -> Path:
    for rel in (
        "docs/ROADMAP-PLATFORM.yaml",
        "docs/ROADMAP-PLATFORM-ARCHIVE.yaml",
        "config/agent/data_quality/source_registry.yaml",
        ".github/workflows/ci.yml",
        ".github/workflows/deploy-ducklake-lambdas.yml",
        ".github/workflows/deploy-prod-lambdas.yml",
        ".github/workflows/terraform-apply-sandbox.yml",
    ):
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REAL / rel, dest)
    raw = yaml.safe_load((REAL / "docs/contracts/actions-capacity.yaml").read_text(encoding="utf-8"))
    if edit:
        edit(raw)
    out = tmp_path / "docs/contracts/actions-capacity.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return tmp_path


def _run(root: Path) -> list[str]:
    failed: list[str] = []
    validate_actions_capacity(failed, root)
    return failed


def test_real_contract_passes_and_declares_examined(tmp_path: Path) -> None:
    failed: list[str] = []
    validate_actions_capacity(failed)
    assert failed == [] and registry._CURRENT_DECLARATION.kind == "examined" and registry._CURRENT_DECLARATION.count == 4
    assert _run(_tree(tmp_path)) == []


def test_rejects_unknown_unit(tmp_path: Path) -> None:
    assert _run(_tree(tmp_path, lambda r: r["ledger"]["rows"][0].__setitem__("unit", "per_hour"))) == [LABEL]
    assert _run(_tree(tmp_path / "b", lambda r: r["ledger"]["rows"][0].__setitem__("source", "guess"))) == [LABEL]


def test_rejects_missing_analog_job(tmp_path: Path) -> None:
    assert _run(_tree(tmp_path, lambda r: r["ledger"]["rows"][1].__setitem__("analog", []))) == [LABEL]
    assert _run(_tree(tmp_path / "b", lambda r: r["ledger"]["rows"][0].__setitem__("job_minutes_per_unit", None))) == [LABEL]


def test_rejects_missing_workflow_or_job(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(
        _tree(tmp_path, lambda r: r["trigger"]["queue_p90"]["job"].__setitem__("workflow", ".github/workflows/nope.yml"))
    )
    assert _run(_tree(tmp_path / "b", lambda r: r["trigger"]["queue_p90"]["job"].__setitem__("job", "renamed-job"))) == [LABEL]
    root = _tree(tmp_path / "c")
    ci = root / ".github/workflows/ci.yml"
    doc = yaml.safe_load(ci.read_text(encoding="utf-8"))
    doc["jobs"]["main-validate"]["name"] = "Main validate"
    ci.write_text(yaml.safe_dump(doc), encoding="utf-8")
    capsys.readouterr()
    assert _run(root) == [LABEL] and "display name" in capsys.readouterr().out


def test_rejects_threshold_at_or_above_cap(tmp_path: Path) -> None:
    assert _run(_tree(tmp_path, lambda r: r["trigger"]["slot_demand"].__setitem__("threshold", 20))) == [LABEL]


def test_rejects_duplicate_ids(tmp_path: Path) -> None:
    assert _run(_tree(tmp_path, lambda r: r["ledger"]["rows"][1].__setitem__("id", r["ledger"]["rows"][0]["id"]))) == [LABEL]


def test_rejects_bad_item_id(tmp_path: Path) -> None:
    assert _run(_tree(tmp_path, lambda r: r["ledger"]["rows"][0].__setitem__("items", ["T99.99"]))) == [LABEL]


def test_rejects_unregistered_alarm_source(tmp_path: Path) -> None:
    assert _run(_tree(tmp_path, lambda r: r["alarm"].__setitem__("source", "not_a_registered_source"))) == [LABEL]
