"""Declared sub-tree: a closed set whose listed members are admitted and every other nested file still fails."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks.contracts import _population
from scripts.checks.contracts.validate_contract_drift import validate_contract_drift

ROW: dict[str, Any] = {
    "path": "sub/",
    "index": "idx.yaml",
    "evaluator": "some_check",
    "extensions": [".yaml", ".txt"],
    "members_key": "members",
    "member_fields": ["contract", "rendered"],
}


def _tree(tmp_path: Path, index_overrides: dict[str, Any] | None = None) -> Path:
    cdir = tmp_path / "contracts"
    (cdir / "sub" / "rendered").mkdir(parents=True)
    index: dict[str, Any] = {
        "contract": {"id": "idx", "class": "D", "evaluator": {"check": "some_check"}},
        "members": [{"contract": "docs/contracts/sub/a.yaml", "rendered": "docs/contracts/sub/rendered/a.txt"}],
    }
    index.update(index_overrides or {})
    (cdir / "idx.yaml").write_text(yaml.safe_dump(index), encoding="utf-8")
    (cdir / "sub" / "a.yaml").write_text("persona: a\n", encoding="utf-8")
    (cdir / "sub" / "rendered" / "a.txt").write_text("text\n", encoding="utf-8")
    return cdir


def test_listed_members_are_admitted(tmp_path: Path) -> None:
    assert _population.subtree_member_failures(_tree(tmp_path), [ROW]) == []


def test_unlisted_file_fails(tmp_path: Path) -> None:
    cdir = _tree(tmp_path)
    (cdir / "sub" / "b.txt").write_text("x\n", encoding="utf-8")
    assert any("b.txt is not listed" in f for f in _population.subtree_member_failures(cdir, [ROW]))


def test_wrong_extension_fails(tmp_path: Path) -> None:
    cdir = _tree(tmp_path)
    (cdir / "sub" / "a.md").write_text("x\n", encoding="utf-8")
    index = yaml.safe_load((cdir / "idx.yaml").read_text())
    index["members"].append({"contract": "docs/contracts/sub/a.md", "rendered": "docs/contracts/sub/rendered/a.txt"})
    (cdir / "idx.yaml").write_text(yaml.safe_dump(index), encoding="utf-8")
    assert any("extension outside" in f for f in _population.subtree_member_failures(cdir, [ROW]))


def test_listed_but_missing_member_fails(tmp_path: Path) -> None:
    cdir = _tree(tmp_path)
    (cdir / "sub" / "a.yaml").unlink()
    assert any("listed member sub/a.yaml has no file" in f for f in _population.subtree_member_failures(cdir, [ROW]))


def test_member_carrying_contract_block_fails(tmp_path: Path) -> None:
    cdir = _tree(tmp_path)
    (cdir / "sub" / "a.yaml").write_text("contract: {id: x, class: D}\n", encoding="utf-8")
    assert any("carries a top-level contract block" in f for f in _population.subtree_member_failures(cdir, [ROW]))


@pytest.mark.parametrize(
    "overrides",
    [
        {"contract": {"id": "idx", "class": "A", "evaluator": {"check": "some_check"}}},
        {"contract": {"id": "idx", "class": "D", "evaluator": {"check": "other_check"}}},
    ],
)
def test_index_must_be_class_d_with_the_rows_evaluator(tmp_path: Path, overrides: dict[str, Any]) -> None:
    assert _population.subtree_member_failures(_tree(tmp_path, overrides), [ROW])


def test_nested_file_under_undeclared_directory_is_not_admitted() -> None:
    assert not _population.under_declared_subtree("other/x.yaml", [ROW])
    assert _population.under_declared_subtree("sub/x.yaml", [ROW])
    assert not _population.is_governed_extension_depth("sub/a.yaml")


def test_missing_key_means_no_subtrees(tmp_path: Path) -> None:
    path = tmp_path / "pop.yaml"
    path.write_text("contract: {id: x}\n", encoding="utf-8")
    assert _population.load_declared_subtrees(path) == []


def test_real_population_declares_the_personas_subtree() -> None:
    rows = _population.load_declared_subtrees(Path("docs/contracts/contract-population.yaml"))
    assert [r["path"] for r in rows] == ["personas/"]


def test_real_tree_passes() -> None:
    failed: list[str] = []
    validate_contract_drift(failed)
    assert failed == []
