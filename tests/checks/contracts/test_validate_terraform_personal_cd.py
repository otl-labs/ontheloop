"""Tests for validate_terraform_personal_cd() -- PLAN-terraform-personal-cd-split."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.checks.contracts.validate_terraform_personal_cd import validate_terraform_personal_cd

_CONTRACT_REL_PATH = "docs/contracts/terraform-personal-cd.yaml"
_SIBLING_REL_PATH = "docs/contracts/terraform-cc-web-operations.yaml"
_TERRAFORM_CLAUDE_MD_REL_PATH = "terraform/CLAUDE.md"


def _write_repo(
    tmp_path: Path,
    *,
    claude_md_text: str = f"See `{_CONTRACT_REL_PATH}` for the CD pipeline.\n",
    referenced_repo_paths: list[str] | None = None,
    create_referenced_files: bool = True,
    write_contract: bool = True,
    write_claude_md: bool = True,
    write_sibling: bool = True,
    contract_sections: dict | None = None,
    sibling_sections: dict | None = None,
) -> Path:
    if referenced_repo_paths is None:
        referenced_repo_paths = ["some/real/file.py", "another/real-file.yaml"]
    if create_referenced_files:
        for rel in referenced_repo_paths:
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("placeholder\n", encoding="utf-8")
    contracts = tmp_path / "docs" / "contracts"
    contracts.mkdir(parents=True, exist_ok=True)
    if write_contract:
        body = {
            "contract": {"id": "terraform-personal-cd"},
            "amendment_log": [],
            "referenced_repo_paths": referenced_repo_paths,
            **(contract_sections if contract_sections is not None else {"convergence_anchor": "x"}),
        }
        (tmp_path / _CONTRACT_REL_PATH).write_text(yaml.safe_dump(body), encoding="utf-8")
    if write_sibling:
        body = {
            "contract": {"id": "terraform-cc-web-operations"},
            "amendment_log": [],
            "referenced_repo_paths": ["x"],
            **(sibling_sections if sibling_sections is not None else {"operator_only_break_glass": "y"}),
        }
        (tmp_path / _SIBLING_REL_PATH).write_text(yaml.safe_dump(body), encoding="utf-8")
    if write_claude_md:
        (tmp_path / "terraform").mkdir(parents=True, exist_ok=True)
        (tmp_path / _TERRAFORM_CLAUDE_MD_REL_PATH).write_text(claude_md_text, encoding="utf-8")
    return tmp_path


def _run(root: Path | None) -> list[str]:
    failed: list[str] = []
    validate_terraform_personal_cd(failed, repo_root=root)
    return failed


def test_pointer_paths_and_disjoint_sections_pass(tmp_path: Path) -> None:
    assert _run(_write_repo(tmp_path)) == []


def test_contract_missing_fails(tmp_path: Path) -> None:
    failed = _run(_write_repo(tmp_path, write_contract=False))
    assert len(failed) == 1 and "not found" in failed[0]
    assert failed[0].startswith("terraform-personal-cd contract:")


def test_terraform_claude_md_missing_pointer_fails(tmp_path: Path) -> None:
    failed = _run(_write_repo(tmp_path, claude_md_text="No pointer here.\n"))
    assert any("no longer points at" in f for f in failed)


def test_named_path_that_does_not_exist_fails(tmp_path: Path) -> None:
    failed = _run(_write_repo(tmp_path, create_referenced_files=False))
    assert any("do not exist" in f for f in failed)


def test_empty_referenced_repo_paths_fails(tmp_path: Path) -> None:
    failed = _run(_write_repo(tmp_path, referenced_repo_paths=[], create_referenced_files=False))
    assert any("missing or empty 'referenced_repo_paths'" in f for f in failed)


def test_section_in_both_contracts_fails(tmp_path: Path) -> None:
    root = _write_repo(
        tmp_path,
        contract_sections={"convergence_anchor": "x"},
        sibling_sections={"convergence_anchor": "y"},
    )
    failed = _run(root)
    assert any("convergence_anchor" in f and "appear in both" in f for f in failed)


def test_missing_sibling_skips_the_boundary_leg(tmp_path: Path, capsys) -> None:
    assert _run(_write_repo(tmp_path, write_sibling=False)) == []
    assert "SKIP" in capsys.readouterr().out


def test_live_repository_passes() -> None:
    assert _run(None) == []


def test_claude_md_missing_fails(tmp_path: Path) -> None:
    failed = _run(_write_repo(tmp_path, write_claude_md=False))
    assert len(failed) == 1 and _TERRAFORM_CLAUDE_MD_REL_PATH in failed[0]


def test_unparseable_contract_fails(tmp_path: Path) -> None:
    root = _write_repo(tmp_path)
    (root / _CONTRACT_REL_PATH).write_text("a: [unclosed\n", encoding="utf-8")
    assert any("could not parse" in f and _CONTRACT_REL_PATH in f for f in _run(root))


def test_unparseable_sibling_fails(tmp_path: Path) -> None:
    root = _write_repo(tmp_path)
    (root / _SIBLING_REL_PATH).write_text("a: [unclosed\n", encoding="utf-8")
    assert any("could not parse" in f and _SIBLING_REL_PATH in f for f in _run(root))


def test_non_mapping_contract_fails_on_paths(tmp_path: Path) -> None:
    root = _write_repo(tmp_path)
    (root / _CONTRACT_REL_PATH).write_text("- just\n- a list\n", encoding="utf-8")
    assert any("missing or empty 'referenced_repo_paths'" in f for f in _run(root))
