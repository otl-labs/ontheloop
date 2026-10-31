"""validate_mechanical_conflicts: each FAIL arm on a tmp git tree, the real-repo PASS and the git-ops pointer."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.checks import registry
from scripts.checks.contracts.validate_mechanical_conflicts import validate_mechanical_conflicts

REAL = Path(__file__).resolve().parents[3]
CONTRACT = "docs/contracts/mechanical-conflicts.yaml"
GIT_OPS = "docs/contracts/git-ops.yaml"
FILES = {
    "docs/decisions-index.json": "[]\n",
    "config/lambda/ducklake/field_semantics.yaml": "a: 1\n",
    ".claude/hooks/hooks.json": "{}\n",
    "scripts/decisions_index.py": "",
    "scripts/schema_to_field_semantics.py": "",
    ".claude/hooks/plugin_hooks.py": "",
    "config/ci_rca_taxonomy.yaml": "a: 1\n",
    "scripts/checks/one/_manifest.py": "",
    "docs/contracts/foo.yaml": "amendment_log: []\n",
    "docs/DECISIONS.md": "# D\n",
    GIT_OPS: (
        "mechanical_conflict_classes: >-\n  docs/contracts/mechanical-conflicts.yaml declares the classes\n"
        "rebase_phase_distinction:\n  commit_flow_time: >-\n    runs scripts.session.rebase_resolve\n"
    ),
}


def _tree(tmp_path: Path, contract: dict | None = None, overrides: dict[str, str | None] | None = None) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    files: dict[str, str | None] = {**FILES, **(overrides or {})}
    files[CONTRACT] = yaml.safe_dump(contract) if contract is not None else (REAL / CONTRACT).read_text(encoding="utf-8")
    for rel, text in files.items():
        if text is None:
            continue
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def _contract() -> dict:
    return yaml.safe_load((REAL / CONTRACT).read_text(encoding="utf-8"))


def _run(root: Path) -> list[str]:
    failed: list[str] = []
    validate_mechanical_conflicts(failed, repo_root=root)
    return failed


def test_valid_tree_passes(tmp_path: Path) -> None:
    assert _run(_tree(tmp_path)) == []


def test_real_repo_passes() -> None:
    assert _run(REAL) == []


def test_missing_contract_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / CONTRACT).unlink()
    assert any("unreadable" in f for f in _run(root))


def test_unexpected_top_level_key_fails(tmp_path: Path) -> None:
    contract = _contract() | {"extra": 1}
    assert any("top-level keys" in f for f in _run(_tree(tmp_path, contract)))


def test_wrong_class_set_fails(tmp_path: Path) -> None:
    contract = _contract()
    del contract["classes"]["roster"]
    assert any("classes are not exactly" in f for f in _run(_tree(tmp_path, contract)))


def test_derived_path_not_tracked_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    subprocess.run(["git", "rm", "-q", "--cached", "docs/decisions-index.json"], cwd=root, check=True)
    assert any("not a tracked file" in f for f in _run(root))


@pytest.mark.parametrize("generator", ["make index", "bin/venv-python -m scripts.nope", "bin/venv-python missing.py"])
def test_bad_generator_fails(tmp_path: Path, generator: str) -> None:
    contract = _contract()
    contract["classes"]["derived"]["files"][0]["generator"] = generator
    assert any("generator" in f for f in _run(_tree(tmp_path, contract)))


def test_roster_matching_nothing_fails(tmp_path: Path) -> None:
    contract = _contract()
    contract["classes"]["roster"]["files"].append("nothing/here/*.yaml")
    assert any("matches no tracked file" in f for f in _run(_tree(tmp_path, contract)))


def test_only_under_must_be_a_string(tmp_path: Path) -> None:
    contract = _contract()
    contract["classes"]["roster"]["files"][-1]["only_under"] = 3
    assert any("only_under must be a string" in f for f in _run(_tree(tmp_path, contract)))


def test_missing_decision_file_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / "docs/DECISIONS.md").unlink()
    assert any("decision_numbering file" in f for f in _run(root))


def test_excluded_path_overlapping_a_classified_file_fails(tmp_path: Path) -> None:
    contract = _contract()
    contract["excluded"].append({"path": "config/ci_rca_taxonomy.yaml", "reason": "x"})
    assert any("excluded path" in f for f in _run(_tree(tmp_path, contract)))


def test_malformed_table_entry_fails(tmp_path: Path) -> None:
    contract = _contract()
    del contract["classes"]["derived"]["files"][0]["generator"]
    assert any("table unreadable" in f for f in _run(_tree(tmp_path, contract)))


def test_declares_accounting(tmp_path: Path) -> None:
    registry.pop_declaration()
    _run(_tree(tmp_path))
    declaration = registry.pop_declaration()
    assert declaration is not None and declaration.kind == "examined" and (declaration.count or 0) > 0


class TestGitOpsPointer:
    def test_real_git_ops_carries_the_key_and_the_command(self) -> None:
        data = yaml.safe_load((REAL / GIT_OPS).read_text(encoding="utf-8"))
        assert CONTRACT in data["mechanical_conflict_classes"]
        assert "scripts.session.rebase_resolve" in data["rebase_phase_distinction"]["commit_flow_time"]

    def test_missing_git_ops_fails(self, tmp_path: Path) -> None:
        root = _tree(tmp_path)
        (root / GIT_OPS).unlink()
        assert any("unreadable" in f for f in _run(root))

    def test_dropping_the_key_fails(self, tmp_path: Path) -> None:
        root = _tree(
            tmp_path, overrides={GIT_OPS: "rebase_phase_distinction:\n  commit_flow_time: scripts.session.rebase_resolve\n"}
        )
        assert any("mechanical_conflict_classes" in f for f in _run(root))

    def test_dropping_the_command_fails(self, tmp_path: Path) -> None:
        text = FILES[GIT_OPS].replace("scripts.session.rebase_resolve", "git rebase origin/main")
        assert any("commit_flow_time" in f for f in _run(_tree(tmp_path, overrides={GIT_OPS: text})))
