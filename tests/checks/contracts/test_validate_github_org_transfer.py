"""Tests for validate_github_org_transfer(): the github-org-transfer contract's registered evaluator."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from scripts.checks import registry
from scripts.checks.ci_guards.validate_trusted_plan_environment import TRUSTED_WORKFLOWS
from scripts.checks.contracts import _manifest, _retired_identity
from scripts.checks.contracts import validate_github_org_transfer as module
from scripts.checks.contracts.validate_github_org_transfer import validate_github_org_transfer

_CUR = "alpha@1/proj@99"
_TGT = "beta@2/proj2@99"
_PHASES = ["p0", "p1", "p2", "p3"]
_PHASE_ROWS = [
    {"id": "settle", "exit_sets_phase": "p0", "trusted_segments": ["current"]},
    {"id": "stage", "exit_sets_phase": "p1", "trusted_segments": ["current", "target"]},
    {"id": "move", "exit_sets_phase": "p2", "trusted_segments": ["current", "target"]},
    {"id": "remove", "exit_sets_phase": "p3", "trusted_segments": ["target"]},
]
_PERSONAL = "terraform/personal/oidc.tf"
_BOOTSTRAP = "terraform/bootstrap/github_ci_apply.tf"
_CONTRACT = "docs/contracts/github-org-transfer.yaml"
_REAL_ROOT = Path(__file__).resolve().parents[3]


def _contract(phase: str = "p0", staged: str | None = None, **overrides: Any) -> dict[str, Any]:
    identity: dict[str, Any] = {
        "current": {"owner": "alpha", "repository": "proj"},
        "target": {"owner": "beta", "repository": "proj2"},
        "phase": phase,
        "phase_values": list(_PHASES),
    }
    if staged is not None:
        identity["staged_phase"] = staged
    contract: dict[str, Any] = {
        "identity": identity,
        "phases": _PHASE_ROWS,
        "breakage_map": [
            {"id": "oidc_trust_segment", "detection": "static", "rec": "rec-1"},
            {"id": "pending_row", "detection": "static", "rec": "rec-2"},
            {"id": "live_row", "detection": "live", "rec": "rec-3"},
        ],
    }
    contract.update(overrides)
    return contract


def _tf(entries: list[str]) -> str:
    body = "".join(f'    "{e}",\n' for e in entries)
    return f"locals {{\n  github_repos = [\n{body}  ]\n}}\n"


def _write(root: Path, contract: dict[str, Any] | str | None, personal: list[str], bootstrap: list[str] | None = None) -> None:
    (root / "docs/contracts").mkdir(parents=True, exist_ok=True)
    (root / "terraform/personal").mkdir(parents=True, exist_ok=True)
    (root / "terraform/bootstrap").mkdir(parents=True, exist_ok=True)
    if contract is not None:
        text = contract if isinstance(contract, str) else yaml.safe_dump(contract)
        (root / _CONTRACT).write_text(text, encoding="utf-8")
    (root / _PERSONAL).write_text(_tf(personal), encoding="utf-8")
    (root / _BOOTSTRAP).write_text(_tf(personal if bootstrap is None else bootstrap), encoding="utf-8")


_OIDC_ONLY = {"oidc_trust_segment": module._evaluate_oidc_trust_segment}


def _run(root: Path, monkeypatch: pytest.MonkeyPatch, evaluators: dict[str, Any] | None = None) -> list[str]:
    monkeypatch.setattr(module, "ROW_EVALUATORS", evaluators or _OIDC_ONLY)
    monkeypatch.setattr(module, "PENDING_STATIC_ROWS", {"pending_row": "rec-2"})
    failed: list[str] = []
    with patch("scripts.checks._common.ROOT", root):
        validate_github_org_transfer(failed)
    return failed


class TestValidateGithubOrgTransfer:
    @pytest.mark.parametrize(
        ("phase", "staged", "entries", "ok"),
        [
            ("p0", None, [_CUR], True),
            ("p0", None, [_CUR, _TGT], False),
            ("p0", "p1", [_CUR, _TGT], True),
            ("p0", "p1", [_CUR], False),
            ("p0", "p2", [_CUR, _TGT], False),
            ("p0", "p0", [_CUR], False),
            ("p1", "p0", [_CUR], False),
            ("p3", "p3", [_TGT], False),
            ("p2", "p3", [_TGT], True),
            ("p3", None, [_TGT], True),
            ("p2", None, [_TGT], False),
            ("p0", "p1", [_TGT, _CUR], True),
        ],
    )
    def test_phase_matrix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str, staged: str | None, entries: list[str], ok: bool
    ) -> None:
        _write(tmp_path, _contract(phase, staged), entries)
        assert (_run(tmp_path, monkeypatch) == []) is ok

    @pytest.mark.parametrize(
        "entries",
        [
            [_CUR, "gamma@3/proj@99"],
            [_CUR, "beta@2/proj2@100"],
            [_CUR, _CUR],
            ["alpha/proj"],
            [_CUR, "repo:beta@2/proj2@99"],
        ],
    )
    def test_bad_entries_fail(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entries: list[str]) -> None:
        _write(tmp_path, _contract("p0", "p1"), entries)
        assert _run(tmp_path, monkeypatch)

    def test_cross_root_mismatch_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, _contract("p0", "p1"), [_CUR, _TGT], [_CUR])
        assert any("differ" in f for f in _run(tmp_path, monkeypatch))

    def test_unknown_phase_value_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, _contract("nope"), [_CUR])
        assert _run(tmp_path, monkeypatch)

    def test_governing_value_no_phase_names_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        contract = _contract("p0", "p1", phases=[r for r in _PHASE_ROWS if r["exit_sets_phase"] != "p1"])
        _write(tmp_path, contract, [_CUR, _TGT])
        assert _run(tmp_path, monkeypatch)

    def test_missing_contract_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, None, [_CUR])
        assert _run(tmp_path, monkeypatch)

    def test_unparseable_contract_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, "a: [unclosed\n", [_CUR])
        assert _run(tmp_path, monkeypatch)

    def test_non_mapping_contract_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, "- just\n- a list\n", [_CUR])
        assert any("missing or unparseable" in f for f in _run(tmp_path, monkeypatch))

    def test_missing_tf_file_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, _contract(), [_CUR])
        (tmp_path / _BOOTSTRAP).unlink()
        assert _run(tmp_path, monkeypatch)

    def test_tf_file_without_list_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, _contract(), [_CUR])
        (tmp_path / _PERSONAL).write_text("locals {}\n", encoding="utf-8")
        assert any("github_repos" in f for f in _run(tmp_path, monkeypatch))

    def test_static_row_neither_evaluated_nor_pending_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        contract = _contract()
        contract["breakage_map"].append({"id": "stray", "detection": "static", "rec": "rec-4"})
        _write(tmp_path, contract, [_CUR])
        assert any("stray" in f for f in _run(tmp_path, monkeypatch))

    def test_row_in_both_maps_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, _contract(), [_CUR])
        evaluators = {**_OIDC_ONLY, "pending_row": lambda c, r, f: None}
        assert any("pending_row" in f for f in _run(tmp_path, monkeypatch, evaluators))

    def test_named_row_absent_from_contract_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, _contract(), [_CUR])
        monkeypatch.setattr(module, "ROW_EVALUATORS", _OIDC_ONLY)
        monkeypatch.setattr(module, "PENDING_STATIC_ROWS", {"pending_row": "rec-2", "ghost": "rec-9"})
        failed: list[str] = []
        with patch("scripts.checks._common.ROOT", tmp_path):
            validate_github_org_transfer(failed)
        assert any("ghost" in f for f in failed)

    def test_pending_rec_differing_from_row_rec_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, _contract(), [_CUR])
        monkeypatch.setattr(module, "ROW_EVALUATORS", _OIDC_ONLY)
        monkeypatch.setattr(module, "PENDING_STATIC_ROWS", {"pending_row": "rec-77"})
        failed: list[str] = []
        with patch("scripts.checks._common.ROOT", tmp_path):
            validate_github_org_transfer(failed)
        assert any("rec-77" in f for f in failed)

    def test_green_on_the_real_tree(self) -> None:
        failed: list[str] = []
        with patch("scripts.checks._common.ROOT", _REAL_ROOT):
            validate_github_org_transfer(failed)
        assert failed == []

    def test_extension_interface_names_and_shapes(self, tmp_path: Path) -> None:
        _write(tmp_path, _contract(), [_CUR])
        loaded = module.load_contract(tmp_path)
        assert isinstance(loaded, dict) and loaded["identity"]["phase"] == "p0"
        assert module.load_contract(tmp_path / "absent") is None
        assert set(module.ROW_EVALUATORS) == {"oidc_trust_segment", "trusted_plan_workflow_ref", *_retired_identity.ROWS}
        assert all(callable(fn) for fn in module.ROW_EVALUATORS.values())
        assert all(rec.startswith("rec-") for rec in module.PENDING_STATIC_ROWS.values())
        assert not set(module.ROW_EVALUATORS) & set(module.PENDING_STATIC_ROWS)

    def test_is_registered_and_dispatched(self) -> None:
        assert "validate_github_org_transfer" in registry._REGISTRY
        entry = next(e for e in _manifest.ENTRIES if e.name == "validate_github_org_transfer")
        assert entry.pre is True


def test_rec_3903_rows_are_evaluated_not_pending() -> None:
    rows = set(_retired_identity.ROWS)
    assert rows == {"dependabot_repository_guard", "owner_literal_defaults", "codeowners", "descriptive_literals"}
    assert rows <= set(module.ROW_EVALUATORS)
    assert not rows & set(module.PENDING_STATIC_ROWS)
    assert module.PENDING_STATIC_ROWS == {"terraform_github_root": "rec-4355"}
    assert all(module.ROW_EVALUATORS[rid] is _retired_identity.ROW_EVALUATORS[rid] for rid in rows)


_ROLE = "terraform/personal/oidc_trusted_plan_role.tf"


def _refs(*slugs: str, files: frozenset[str] = TRUSTED_WORKFLOWS) -> list[str]:
    return [f"{slug}/.github/workflows/{name}@refs/heads/main" for slug in slugs for name in sorted(files)]


def _role_tf(refs: list[str]) -> str:
    body = "".join(f'    "{r}",\n' for r in refs)
    return f"locals {{\n  # trusted_plan_workflow_refs = []\n  trusted_plan_workflow_refs = [\n{body}  ]\n}}\n"


def _evaluate_refs(tmp_path: Path, refs: list[str] | None, phase: str = "p0", staged: str | None = None) -> list[str]:
    if refs is not None:
        (tmp_path / "terraform/personal").mkdir(parents=True, exist_ok=True)
        (tmp_path / _ROLE).write_text(_role_tf(refs), encoding="utf-8")
    failed: list[str] = []
    module._evaluate_trusted_plan_workflow_ref(_contract(phase, staged), tmp_path, failed)
    return failed


class TestTrustedPlanWorkflowRef:
    @pytest.mark.parametrize(
        ("phase", "staged", "slugs", "ok"),
        [
            ("p0", None, ["alpha/proj"], True),
            ("p0", None, ["beta/proj2"], False),
            ("p0", "p1", ["alpha/proj", "beta/proj2"], True),
            ("p0", "p1", ["alpha/proj"], False),
            ("p2", None, ["beta/proj2"], True),
            ("p2", None, ["alpha/proj"], False),
            ("p3", None, ["alpha/proj", "beta/proj2"], False),
        ],
    )
    def test_phase_matrix(self, tmp_path: Path, phase: str, staged: str | None, slugs: list[str], ok: bool) -> None:
        assert (_evaluate_refs(tmp_path, _refs(*slugs), phase, staged) == []) is ok

    def test_missing_trusted_file_value_fails(self, tmp_path: Path) -> None:
        refs = _refs("alpha/proj", files=frozenset(sorted(TRUSTED_WORKFLOWS)[:1]))
        assert _evaluate_refs(tmp_path, refs)

    def test_wildcard_fails(self, tmp_path: Path) -> None:
        refs = [*_refs("alpha/proj"), "alpha/proj/.github/workflows/*@refs/heads/main"]
        assert any("wildcard" in f for f in _evaluate_refs(tmp_path, refs))

    def test_non_main_ref_fails(self, tmp_path: Path) -> None:
        refs = [r.replace("refs/heads/main", "refs/heads/dev") for r in _refs("alpha/proj")]
        assert _evaluate_refs(tmp_path, refs)

    def test_missing_file_fails(self, tmp_path: Path) -> None:
        assert any("unreadable" in f for f in _evaluate_refs(tmp_path, None))

    def test_absent_local_fails(self, tmp_path: Path) -> None:
        (tmp_path / "terraform/personal").mkdir(parents=True)
        (tmp_path / _ROLE).write_text("locals {\n  # trusted_plan_workflow_refs = []\n}\n", encoding="utf-8")
        failed: list[str] = []
        module._evaluate_trusted_plan_workflow_ref(_contract(), tmp_path, failed)
        assert any("declares no trusted_plan_workflow_refs" in f for f in failed)

    def test_bad_phase_fails_without_comparing(self, tmp_path: Path) -> None:
        failed = _evaluate_refs(tmp_path, _refs("alpha/proj"), "nope")
        assert len(failed) == 1 and "phase_values" in failed[0]

    def test_row_without_evaluator_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        contract = _contract()
        contract["breakage_map"].append({"id": "trusted_plan_workflow_ref", "detection": "static", "rec": "rec-4636"})
        _write(tmp_path, contract, [_CUR])
        assert any("trusted_plan_workflow_ref" in f for f in _run(tmp_path, monkeypatch))

    def test_real_row_is_evaluated(self) -> None:
        contract = module.load_contract(_REAL_ROOT)
        assert contract is not None
        row = next(r for r in contract["breakage_map"] if r["id"] == "trusted_plan_workflow_ref")
        assert (row["detection"], row["rec"], row["locations"]) == ("static", "rec-4636", [_ROLE])
        failed: list[str] = []
        module._evaluate_trusted_plan_workflow_ref(contract, _REAL_ROOT, failed)
        assert failed == []
