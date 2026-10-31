"""Tests for validate_pre_glob_closure.uncovered_after_edges (PLAN-plan-lint plan-time seam)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from scripts.checks import registry
from scripts.checks._schema import Entry
from scripts.checks.deps import validate_pre_glob_closure as vpgc
from tests.fixtures.plan_lint_fixtures import write, write_closure_repo

_ENTRY = Entry(
    name="validate_thing",
    module="scripts.checks.fake.validate_thing",
    attr="validate_thing",
    pre=True,
    pre_globs=("scripts/checks/fake/**",),
)
_IMPORTER = "scripts.checks.fake.validate_thing"


def _run(root: Path, edges: list[tuple[str, str]], new_paths: dict[str, str] | None = None) -> dict[str, list[str]]:
    with patch.object(registry, "_ALL_ENTRIES", {_ENTRY.name: _ENTRY}):
        return vpgc.uncovered_after_edges(edges, new_paths or {}, root)


def test_edge_that_gains_an_uncovered_path_is_reported(tmp_path: Path) -> None:
    write_closure_repo(tmp_path)
    assert _run(tmp_path, [(_IMPORTER, "scripts.hublib")]) == {"validate_thing": ["scripts/hublib.py"]}


def test_edge_outside_every_gated_closure_gives_nothing(tmp_path: Path) -> None:
    write_closure_repo(tmp_path)
    write(tmp_path, "scripts/other.py", "X = 1\n")
    assert _run(tmp_path, [("scripts.other", "scripts.hublib")]) == {}


def test_nothing_the_unmodified_view_already_reports(tmp_path: Path) -> None:
    write_closure_repo(tmp_path)
    write(
        tmp_path,
        "scripts/checks/fake/validate_thing.py",
        "from scripts.hublib import hub\n\n\ndef validate_thing(failed):\n    hub()\n",
    )
    write(tmp_path, "scripts/second.py", "Y = 2\n")
    assert _run(tmp_path, [(_IMPORTER, "scripts.second")]) == {"validate_thing": ["scripts/second.py"]}


def test_target_absent_from_the_tree_resolves_through_new_paths(tmp_path: Path) -> None:
    write_closure_repo(tmp_path)
    assert _run(tmp_path, [(_IMPORTER, "scripts.newlib")], {"scripts.newlib": "scripts/newlib.py"}) == {
        "validate_thing": ["scripts/newlib.py"]
    }
    assert _run(tmp_path, [(_IMPORTER, "scripts.newlib")]) == {}


def test_verdict_on_the_live_tree_is_unchanged() -> None:
    failed: list[str] = []
    vpgc.validate_pre_glob_closure(failed)
    assert failed == []
