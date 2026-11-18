"""Tests for validate_git_ops_contract() -- PLAN-ambient-prose-contract-relocation.

Covers: green path (AGENTS.md points at the contract, every referenced_repo_paths entry
exists), the pointer-removed red path (AGENTS.md loses its pointer), and the dangling-path red
path (the contract names a repo path that does not exist) -- the two failure modes
test_obligations names for this check -- plus missing-contract, missing-AGENTS.md,
malformed-YAML, and missing/empty referenced_repo_paths edge cases.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.checks.contracts.validate_git_ops_contract import validate_git_ops_contract

_CONTRACT_REL_PATH = "docs/contracts/git-ops.yaml"
_AGENTS_MD_REL_PATH = "AGENTS.md"


def _write_repo(
    tmp_path: Path,
    *,
    agents_md_text: str = f"See `{_CONTRACT_REL_PATH}#flow` for the full git-ops procedure.\n",
    extra_surfaces: dict[str, str] | None = None,
    referenced_repo_paths: list[str] | None = None,
    create_referenced_files: bool = True,
    write_contract: bool = True,
    write_agents_md: bool = True,
) -> Path:
    if referenced_repo_paths is None:
        referenced_repo_paths = ["some/real/file.py", "another/real-file.yaml"]

    if create_referenced_files:
        for rel in referenced_repo_paths:
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("placeholder\n", encoding="utf-8")

    if write_contract:
        contract_dir = tmp_path / "docs" / "contracts"
        contract_dir.mkdir(parents=True, exist_ok=True)
        contract_body = {
            "contract": {"id": "git-ops", "class": "D", "contract_version": 1, "status": "ratified"},
            "referenced_repo_paths": referenced_repo_paths,
            "flow": {"steps": ["x"]},
            "nested": {"inner": {"leaf": 1}},
        }
        (contract_dir / "git-ops.yaml").write_text(yaml.safe_dump(contract_body), encoding="utf-8")

    if write_agents_md:
        (tmp_path / "AGENTS.md").write_text(agents_md_text, encoding="utf-8")

    for rel, text in (extra_surfaces or {}).items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    return tmp_path


class TestGreenPath:
    def test_pointer_present_and_all_paths_exist_passes(self, tmp_path: Path) -> None:
        _write_repo(tmp_path)

        failed: list[str] = []
        validate_git_ops_contract(failed, repo_root=tmp_path)

        assert failed == []


class TestPointerRemovedRedPath:
    def test_agents_md_missing_pointer_fails(self, tmp_path: Path) -> None:
        _write_repo(tmp_path, agents_md_text="Some unrelated content with no pointer.\n")

        failed: list[str] = []
        validate_git_ops_contract(failed, repo_root=tmp_path)

        assert any("no longer points at" in f for f in failed)

    def test_agents_md_missing_file_fails(self, tmp_path: Path) -> None:
        _write_repo(tmp_path, write_agents_md=False)

        failed: list[str] = []
        validate_git_ops_contract(failed, repo_root=tmp_path)

        assert any("AGENTS.md not found" in f for f in failed)


class TestDanglingPathRedPath:
    def test_named_path_that_does_not_exist_fails(self, tmp_path: Path) -> None:
        _write_repo(
            tmp_path,
            referenced_repo_paths=["real/file.py", "ghost/does-not-exist.py"],
            create_referenced_files=False,
        )
        (tmp_path / "real").mkdir(parents=True)
        (tmp_path / "real" / "file.py").write_text("x\n", encoding="utf-8")

        failed: list[str] = []
        validate_git_ops_contract(failed, repo_root=tmp_path)

        assert any("ghost/does-not-exist.py" in f for f in failed)

    def test_all_named_paths_missing_fails_with_full_list(self, tmp_path: Path) -> None:
        _write_repo(
            tmp_path,
            referenced_repo_paths=["missing/one.py", "missing/two.yaml"],
            create_referenced_files=False,
        )

        failed: list[str] = []
        validate_git_ops_contract(failed, repo_root=tmp_path)

        assert any("missing/one.py" in f and "missing/two.yaml" in f for f in failed)


class TestContractShapeEdgeCases:
    def test_contract_missing_fails(self, tmp_path: Path) -> None:
        _write_repo(tmp_path, write_contract=False)

        failed: list[str] = []
        validate_git_ops_contract(failed, repo_root=tmp_path)

        assert any(_CONTRACT_REL_PATH in f and "not found" in f for f in failed)

    def test_malformed_yaml_fails(self, tmp_path: Path) -> None:
        _write_repo(tmp_path)
        (tmp_path / "docs" / "contracts" / "git-ops.yaml").write_text("key: [unterminated\n", encoding="utf-8")

        failed: list[str] = []
        validate_git_ops_contract(failed, repo_root=tmp_path)

        assert any("could not parse" in f for f in failed)

    def test_missing_referenced_repo_paths_fails(self, tmp_path: Path) -> None:
        _write_repo(tmp_path)
        contract_dir = tmp_path / "docs" / "contracts"
        contract_body = {"contract": {"id": "git-ops", "class": "D", "contract_version": 1, "status": "ratified"}}
        (contract_dir / "git-ops.yaml").write_text(yaml.safe_dump(contract_body), encoding="utf-8")

        failed: list[str] = []
        validate_git_ops_contract(failed, repo_root=tmp_path)

        assert any("missing or empty" in f for f in failed)

    def test_empty_referenced_repo_paths_fails(self, tmp_path: Path) -> None:
        _write_repo(tmp_path, referenced_repo_paths=[], create_referenced_files=False)

        failed: list[str] = []
        validate_git_ops_contract(failed, repo_root=tmp_path)

        assert any("missing or empty" in f for f in failed)


class TestAnchorResolution:
    def test_unanchored_agents_md_pointer_fails(self, tmp_path: Path) -> None:
        _write_repo(tmp_path, agents_md_text=f"See `{_CONTRACT_REL_PATH}` whole.\n")

        failed: list[str] = []
        validate_git_ops_contract(failed, repo_root=tmp_path)

        assert any("without a #<key> anchor" in f for f in failed)

    def test_anchor_to_missing_key_in_skill_fails(self, tmp_path: Path) -> None:
        _write_repo(tmp_path, extra_surfaces={".claude/skills/x/SKILL.md": "See `git-ops.yaml#ghost_key` here.\n"})

        failed: list[str] = []
        validate_git_ops_contract(failed, repo_root=tmp_path)

        assert any(".claude/skills/x/SKILL.md anchor git-ops.yaml#ghost_key does not resolve" in f for f in failed)

    def test_dotted_anchor_in_command_resolves(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _write_repo(tmp_path, extra_surfaces={".claude/commands/c.md": "See `git-ops.yaml#nested.inner.leaf`.\n"})

        failed: list[str] = []
        validate_git_ops_contract(failed, repo_root=tmp_path)

        assert failed == []
        assert "2 anchor(s) resolve" in capsys.readouterr().out

    def test_anchor_to_missing_nested_key_fails(self, tmp_path: Path) -> None:
        _write_repo(tmp_path, extra_surfaces={".claude/commands/c.md": "See `git-ops.yaml#nested.inner.ghost`.\n"})

        failed: list[str] = []
        validate_git_ops_contract(failed, repo_root=tmp_path)

        assert any("git-ops.yaml#nested.inner.ghost does not resolve" in f for f in failed)

    def test_sentence_final_period_is_not_part_of_the_key(self, tmp_path: Path) -> None:
        _write_repo(tmp_path, extra_surfaces={".claude/commands/c.md": "Read git-ops.yaml#flow.\n"})

        failed: list[str] = []
        validate_git_ops_contract(failed, repo_root=tmp_path)

        assert failed == []
