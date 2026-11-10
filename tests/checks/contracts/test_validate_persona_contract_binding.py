"""validate_persona_contract_binding: pending, implemented and landing-dir rules (T4.10a c4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.checks.contracts.validate_persona_contract_binding import validate_persona_contract_binding
from tests.persona_contracts.fixture_tree import CONTRACT_REL, INDEX_REL, build_tree, edit_text


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return build_tree(tmp_path)


def _run(root: Path | None = None) -> list[str]:
    failed: list[str] = []
    validate_persona_contract_binding(failed, root)
    return failed


def test_real_tree_green() -> None:
    assert _run() == []


def test_fixture_tree_green(tree: Path) -> None:
    assert _run(tree) == []


def test_pending_row_on_met_criterion_fails(tree: Path) -> None:
    edit_text(tree, INDEX_REL, 'owning_item: "T4.10a:c5"', 'owning_item: "T4.10a:c1"')
    assert any("not an open roadmap criterion" in f for f in _run(tree))


def test_pending_row_on_unknown_criterion_fails(tree: Path) -> None:
    edit_text(tree, INDEX_REL, 'owning_item: "T4.10a:c5"', 'owning_item: "T9.99:c1"')
    assert any("not an open roadmap criterion" in f for f in _run(tree))


def _implement(tree: Path, body: str) -> None:
    module = tree / "scripts" / "personas" / "fixture.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(body, encoding="utf-8")
    edit_text(
        tree,
        INDEX_REL,
        '{status: pending, owning_item: "T4.10a:c5"}',
        "{status: implemented, location: scripts/personas/fixture.py}",
    )


def test_implemented_row_without_contract_path_fails(tree: Path) -> None:
    _implement(tree, "X = 1\n")
    assert any("does not name" in f for f in _run(tree))


def test_implemented_row_naming_contract_path_is_green(tree: Path) -> None:
    _implement(tree, f'PATH = "{CONTRACT_REL}"\n')
    assert _run(tree) == []


def test_implemented_row_with_missing_location_fails(tree: Path) -> None:
    edit_text(tree, INDEX_REL, '{status: pending, owning_item: "T4.10a:c5"}', "{status: implemented, location: nope.py}")
    assert any("does not exist" in f for f in _run(tree))


def test_landing_dir_module_naming_pending_persona_fails(tree: Path) -> None:
    module = tree / "src" / "persona_backends" / "backend.py"
    module.parent.mkdir(parents=True)
    module.write_text('NAME = "fixture_verdict"\n', encoding="utf-8")
    assert any("names pending persona" in f for f in _run(tree))


def test_docstring_and_comment_mentions_do_not_fail(tree: Path) -> None:
    module = tree / "src" / "persona_backends" / "backend.py"
    module.parent.mkdir(parents=True)
    module.write_text('"""fixture_verdict"""\n# fixture_verdict\n', encoding="utf-8")
    assert _run(tree) == []


def test_malformed_implementation_fails(tree: Path) -> None:
    edit_text(tree, INDEX_REL, '{status: pending, owning_item: "T4.10a:c5"}', "{status: pending}")
    assert any("implementation must be" in f for f in _run(tree))


def test_unreadable_index_fails(tmp_path: Path) -> None:
    failed: list[str] = []
    validate_persona_contract_binding(failed, tmp_path)
    assert any("unreadable or off its schema" in f for f in failed)


def test_syntax_error_in_landing_module_is_ignored(tree: Path) -> None:
    module = tree / "scripts" / "personas" / "broken.py"
    module.parent.mkdir(parents=True)
    module.write_text("def (:\n", encoding="utf-8")
    assert _run(tree) == []


def test_missing_roadmap_is_a_failure_not_a_crash(tree: Path) -> None:
    (tree / "docs/ROADMAP-PLATFORM.yaml").unlink()
    assert any("roadmap is missing" in f for f in _run(tree))
