"""validate_persona_contracts: real tree green, fixture tree green, one red test per rule (T4.10a c2-c8)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.checks import registry
from scripts.checks.contracts._manifest import ENTRIES
from scripts.checks.contracts.validate_persona_contracts import validate_persona_contracts
from tests.persona_contracts.fixture_tree import (
    CONTRACT_REL,
    EXPECTED,
    MUTATORS,
    RENDERED_REL,
    build_tree,
    edit_contract,
)

_NAMES = ("validate_persona_contracts", "validate_persona_contract_binding")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return build_tree(tmp_path)


def _run(root: Path | None = None) -> list[str]:
    failed: list[str] = []
    validate_persona_contracts(failed, root)
    return failed


def test_real_tree_green_and_examined() -> None:
    registry.pop_declaration()
    assert _run() == []
    declaration = registry.pop_declaration()
    assert declaration is not None and declaration.kind == "examined" and declaration.count >= 5


def test_fixture_tree_green(tree: Path) -> None:
    assert _run(tree) == []


def test_mutators_and_expectations_cover_the_same_rules() -> None:
    assert set(MUTATORS) == set(EXPECTED)


@pytest.mark.parametrize("rule", sorted(MUTATORS))
def test_red_rule(rule: str, tree: Path) -> None:
    MUTATORS[rule](tree)
    failures = _run(tree)
    assert any(EXPECTED[rule] in f for f in failures), failures


def test_both_checks_registered_in_both_sequences() -> None:
    entries = {e.name: e for e in ENTRIES}
    for name in _NAMES:
        entry = entries[name]
        assert entry.pre is True and entry.full_segment is not None
        assert entry.pre_globs is None
        assert name in {getattr(step, "name", step) for step in registry.pre_sequence()}
        assert name in {getattr(step, "name", step) for step in registry.full_sequence()}


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def test_rendered_prompt_change_requires_version_raise(tree: Path) -> None:
    _git(tree, "init", "-q")
    _git(tree, "config", "user.email", "t@example.com")
    _git(tree, "config", "user.name", "t")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-q", "-m", "base")
    _git(tree, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert _run(tree) == []
    edit_contract(tree, lambda c: c.__setitem__("prompt_template", c["prompt_template"] + "More text.\n"), rerender=True)
    assert any("without raising contract_version" in f for f in _run(tree))
    edit_contract(tree, lambda c: c.__setitem__("contract_version", 2), rerender=True)
    assert _run(tree) == []
    assert (tree / CONTRACT_REL).is_file() and (tree / RENDERED_REL).is_file()


def test_unreadable_index_fails_and_skips(tmp_path: Path) -> None:
    registry.pop_declaration()
    failed: list[str] = []
    validate_persona_contracts(failed, tmp_path)
    assert any("unreadable or off its schema" in f for f in failed)
    declaration = registry.pop_declaration()
    assert declaration is not None and declaration.kind == "skipped"


def test_missing_source_file_is_a_failure_not_a_crash(tree: Path) -> None:
    (tree / "docs/ROADMAP-PLATFORM.yaml").unlink()
    assert any("missing or unreadable" in f for f in _run(tree))


def test_prose_containing_a_substring_is_not_a_money_budget(tree: Path) -> None:
    edit_contract(tree, lambda c: c["budget"]["provisional"].__setitem__("measure", "neuron turn counts"), rerender=True)
    assert _run(tree) == []


def test_compute_plane_contract_needs_no_skill_tether(tree: Path) -> None:
    from tests.persona_contracts.fixture_tree import INDEX_REL, edit_text

    edit_text(tree, INDEX_REL, "    kind: verdict\n    contract:", "    kind: compute_plane\n    contract:")
    edit_contract(tree, lambda c: (c.pop("skill_tether"), c.__setitem__("kind", "compute_plane")), rerender=True)
    assert _run(tree) == []
