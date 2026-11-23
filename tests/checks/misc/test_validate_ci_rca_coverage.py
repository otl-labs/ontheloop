"""Mirror test for scripts/checks/misc/validate_ci_rca_coverage.py (rec-5537)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.checks.misc import _manifest, validate_ci_rca_coverage
from scripts.ci_rca import coverage

WORKFLOW = {"jobs": {"j": {"steps": [{"id": "a", "run": "echo hi"}, {"name": "Do B", "run": "echo config/x.yaml"}]}}}


def _tree(tmp_path: Path, rows: dict, workflow: dict = WORKFLOW, extra_text: str = "") -> None:
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/workflows/ci-rca.yml").write_text(yaml.safe_dump(workflow), encoding="utf-8")
    (tmp_path / "config/agent/ci_rca").mkdir(parents=True)
    (tmp_path / "config/x.yaml").write_text("a: 1\n", encoding="utf-8")
    table = {"schema_version": 1, "proven_by_values": {"watchdog_only": "x"}, "known_top_level": [], "steps": rows}
    (tmp_path / coverage.TABLE_REL).write_text(yaml.safe_dump(table) + extra_text, encoding="utf-8")


def _row(**kw: object) -> dict:
    return {"proven_by": "watchdog_only", "declared_inputs": [], **kw}


def _problems(tmp_path: Path) -> list[str]:
    return coverage.load(root=tmp_path).check()


def test_clean_fixture(tmp_path: Path) -> None:
    _tree(tmp_path, {"j::a": _row(), "j::do-b": _row()})
    assert _problems(tmp_path) == []


def test_missing_row(tmp_path: Path) -> None:
    _tree(tmp_path, {"j::a": _row()})
    assert any("j::do-b: no row" in p for p in _problems(tmp_path))


def test_stale_row(tmp_path: Path) -> None:
    _tree(tmp_path, {"j::a": _row(), "j::do-b": _row(), "j::gone": _row()})
    assert any("j::gone: no such step" in p for p in _problems(tmp_path))


def test_bad_proven_by(tmp_path: Path) -> None:
    _tree(tmp_path, {"j::a": _row(proven_by="nope"), "j::do-b": _row()})
    assert any("proven_by 'nope'" in p for p in _problems(tmp_path))


def test_missing_declared_input(tmp_path: Path) -> None:
    _tree(tmp_path, {"j::a": _row(declared_inputs=["config/missing.yaml"]), "j::do-b": _row()})
    assert any("declared input config/missing.yaml does not exist" in p for p in _problems(tmp_path))


def test_unresolved_reference(tmp_path: Path) -> None:
    wf = {"jobs": {"j": {"steps": [{"id": "a", "run": "cat config/missing.yaml"}]}}}
    _tree(tmp_path, {"j::a": _row()}, workflow=wf)
    assert any("config/missing.yaml resolves to no file" in p for p in _problems(tmp_path))


def test_real_tree_clean_and_examined(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    seen: list[tuple[int, str]] = []
    monkeypatch.setattr(validate_ci_rca_coverage.registry, "examined", lambda n, unit="": seen.append((n, unit)))
    failed: list[str] = []
    validate_ci_rca_coverage.validate_ci_rca_coverage(failed)
    assert failed == []
    assert seen and seen[0][0] > 20 and seen[0][1] == "ci_rca_steps"
    assert "PASS" in capsys.readouterr().out


def test_failure_appends(monkeypatch: pytest.MonkeyPatch) -> None:
    class Fake:
        steps = {"k": None}

        def check(self) -> list[str]:
            return ["boom"]

    monkeypatch.setattr(validate_ci_rca_coverage.coverage, "load", lambda: Fake())
    monkeypatch.setattr(validate_ci_rca_coverage.registry, "examined", lambda n, unit="": None)
    failed: list[str] = []
    validate_ci_rca_coverage.validate_ci_rca_coverage(failed)
    assert failed == ["CI RCA coverage table"]


def test_manifest_entry() -> None:
    entry = next(e for e in _manifest.ENTRIES if e.name == "validate_ci_rca_coverage")
    assert entry.pre is True
    assert entry.pre_globs is None
    assert entry.full_segment == "full_after_lint"
