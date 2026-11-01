"""TestProseBudgetsRatchetRow (PLAN-prose-budget-ratchet, rec-4779): the prose_budgets_ratchet_down
sanction row's scope_file_is_prose_gated trigger kind.

One arm per branch of _prose_ratchet_sanction; neither validate_scope_boundary.py nor this module
carries a config/coverage_baseline.yaml entry, so validate_test_coverage holds both to the 100%
per-file floor.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from .conftest import (
    _DEFAULT_SANCTION_ROWS,
    _commit_all,
    _git,
    _init_repo,
    _write_contract,
    _write_plan,
    validate_scope_boundary,
)

_REGISTRY = "config/prose_budgets.yaml"
_S4_PATH = ".claude/skills/planning/SKILL.md"
_ROW = {
    "implementing_plan_bookkeeping": _DEFAULT_SANCTION_ROWS["implementing_plan_bookkeeping"],
    "prose_budgets_ratchet_down": {
        "trigger": {"kind": "scope_file_is_prose_gated", "registry_path": _REGISTRY},
        "sanctions": {"path_template": _REGISTRY},
        "prohibited_field_edits": [],
    },
}
_BASE = f"S1:\n  root_ambient_load_set: 100\nS4:\n  {_S4_PATH}: 200\n"


def _scope(*files: str) -> list[dict]:
    return [{"file": f, "action": "Modify", "purpose": "x"} for f in files]


def _build(
    tmp_path: Path,
    slug: str,
    scope: list[str],
    base_registry: str | None,
    working_registry: str | None,
    claude_md: str = "x",
) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_contract(repo, _ROW)
    (repo / "CLAUDE.md").write_text(claude_md, encoding="utf-8")
    (repo / "AGENTS.md").write_text("y", encoding="utf-8")
    _write_plan(repo, slug, _scope(*scope), declared=False)
    (repo / "config").mkdir()
    if base_registry is not None:
        (repo / _REGISTRY).write_text(base_registry, encoding="utf-8")
    base_sha = _commit_all(repo, "base")
    _git(repo, ["update-ref", "refs/remotes/origin/main", base_sha])

    rel = _write_plan(repo, slug, _scope(*scope), declared=True)
    if working_registry is not None:
        (repo / _REGISTRY).write_text(working_registry, encoding="utf-8")
    elif (repo / _REGISTRY).exists():
        (repo / _REGISTRY).unlink()
    _commit_all(repo, "declare implementation")
    return repo, rel


def _run(repo: Path, rel: str, scope: list[str]) -> list[str]:
    failed: list[str] = []
    validate_scope_boundary(failed, changed_files=[rel, *scope, _REGISTRY], root=repo)
    return failed


def _stops_on_registry(failed: list[str]) -> bool:
    return any(_REGISTRY in f and "outside declared scope" in f for f in failed)


class TestProseBudgetsRatchetRow:
    def test_s4_scope_with_lowered_value_sanctioned(self, tmp_path: Path) -> None:
        repo, rel = _build(tmp_path, "pb-a", [_S4_PATH], _BASE, _BASE.replace("200", "150"))
        assert _run(repo, rel, [_S4_PATH]) == []

    def test_raised_value_stops(self, tmp_path: Path) -> None:
        repo, rel = _build(tmp_path, "pb-b", [_S4_PATH], _BASE, _BASE.replace("200", "250"))
        assert _stops_on_registry(_run(repo, rel, [_S4_PATH]))

    def test_comment_edit_stops(self, tmp_path: Path) -> None:
        repo, rel = _build(tmp_path, "pb-c", [_S4_PATH], _BASE, "# note\n" + _BASE.replace("200", "150"))
        assert _stops_on_registry(_run(repo, rel, [_S4_PATH]))

    def test_trailing_comment_edit_stops(self, tmp_path: Path) -> None:
        repo, rel = _build(tmp_path, "pb-c2", [_S4_PATH], _BASE, _BASE.replace("200", "150  # sneaky"))
        assert _stops_on_registry(_run(repo, rel, [_S4_PATH]))

    @pytest.mark.parametrize("member", ["CLAUDE.md", "AGENTS.md"])
    def test_s1_member_in_scope_with_lowered_value_sanctioned(self, tmp_path: Path, member: str) -> None:
        repo, rel = _build(tmp_path, "pb-d", [member], _BASE, _BASE.replace("100", "90"), claude_md="@AGENTS.md\n")
        assert _run(repo, rel, [member]) == []

    def test_no_gated_surface_in_scope_stops(self, tmp_path: Path) -> None:
        repo, rel = _build(tmp_path, "pb-e", ["scripts/foo.py"], _BASE, _BASE.replace("200", "150"))
        assert _stops_on_registry(_run(repo, rel, ["scripts/foo.py"]))

    def test_registry_absent_at_base_stops_without_crash(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        repo, rel = _build(tmp_path, "pb-f", [_S4_PATH], None, _BASE)
        assert _stops_on_registry(_run(repo, rel, [_S4_PATH]))
        assert "absent at base ref" in capsys.readouterr().out

    def test_registry_declared_in_scope_is_inert(self, tmp_path: Path) -> None:
        repo, rel = _build(tmp_path, "pb-g", [_S4_PATH, _REGISTRY], _BASE, _BASE.replace("200", "250"))
        assert _run(repo, rel, [_S4_PATH, _REGISTRY]) == []

    def test_real_contract_row_shape(self) -> None:
        contract = yaml.safe_load(
            (Path(__file__).resolve().parents[4] / "docs/contracts/implement-scope-boundary.yaml").read_text()
        )
        row = contract["sanction_rows"]["prose_budgets_ratchet_down"]
        assert row["trigger"]["kind"] == "scope_file_is_prose_gated"
        assert row["trigger"]["registry_path"] == _REGISTRY
        assert row["sanctions"]["path_template"] == _REGISTRY

    def test_s1_member_outside_root_skipped_with_note(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        (tmp_path / "outside.md").write_text("z", encoding="utf-8")
        repo, rel = _build(tmp_path, "pb-i", ["CLAUDE.md"], _BASE, _BASE.replace("100", "90"), claude_md="@../outside.md\n")
        assert _run(repo, rel, ["CLAUDE.md"]) == []
        assert "resolves outside" in capsys.readouterr().out

    def test_registry_absent_in_working_tree_stops_without_crash(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo, rel = _build(tmp_path, "pb-j", [_S4_PATH], _BASE, None)
        assert _stops_on_registry(_run(repo, rel, [_S4_PATH]))
        assert "absent in the working tree" in capsys.readouterr().out

    def test_new_key_line_stops(self, tmp_path: Path) -> None:
        repo, rel = _build(tmp_path, "pb-k", [_S4_PATH], _BASE, _BASE + "  .claude/skills/new/SKILL.md: 5\n")
        assert _stops_on_registry(_run(repo, rel, [_S4_PATH]))
