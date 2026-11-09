"""Mirror of scripts/checks/verification/validate_graduated_rerun_plan_readers.py (fast-tier plan-reader re-run)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.checks import registry
from scripts.checks.verification import validate_graduated_rerun as rerun
from scripts.checks.verification import validate_graduated_rerun_plan_readers as readers
from scripts.checks.verification.validate_known_red_shrinks import KNOWN_RED_REL
from scripts.verification_graduation import shard_path_for

GREEN = ["bash", "-c", "exit 0"]
PLAN_RED = ["bash", "-c", "echo docs/plans/PLAN-x.yaml; exit 1"]
PLAN_GREEN = ["bash", "-c", "echo docs/plans/PLAN-x.yaml; exit 0"]


def _record(root: Path, check_id: str, command: list[str] | None = None) -> None:
    row = {
        "check_id": check_id,
        "primitive_slot": "command_exit_zero",
        "guard_target": f"scripts/{check_id}.py",
        "check_spec": {"command": command if command is not None else GREEN},
    }
    path = shard_path_for(check_id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(row), encoding="utf-8")


def _listed(root: Path, ids: list[str]) -> None:
    path = root / KNOWN_RED_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = [{"check_id": i, "owner_rec": "rec-1", "entered_at": "2026-11-03"} for i in ids]
    path.write_text(yaml.safe_dump({"schema_version": 1, "entries": entries}), encoding="utf-8")


def _run(root: Path) -> list[str]:
    failed: list[str] = []
    readers.validate_graduated_rerun_plan_readers(failed, root=root)
    return failed


def _row(command: list[str]) -> dict:
    return {"check_spec": {"command": command}}


def test_selects_commands_that_read_plans() -> None:
    assert readers.reads_plans(_row(["cat", "docs/plans/PLAN-x.yaml"]))
    assert readers.reads_plans(_row(["python", "-c", "ROOT/'docs'/'plans'"]))
    assert not readers.reads_plans(_row(["git", "diff", "--", ":!docs/plans/"]))
    assert not readers.reads_plans(_row(["echo", "nothing"]))
    assert readers.reads_plans({"check_spec": {"command": "cat docs/plans/PLAN-x.yaml"}})
    assert not readers.reads_plans({})


def test_unlisted_red_plan_reader_fails(tmp_path: Path) -> None:
    _record(tmp_path, "rec-red", PLAN_RED)
    _listed(tmp_path, [])
    failed = _run(tmp_path)
    assert len(failed) == 1
    assert "rec-red" in failed[0]


def test_listed_red_plan_reader_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _record(tmp_path, "rec-red", PLAN_RED)
    _listed(tmp_path, ["rec-red"])
    assert _run(tmp_path) == []
    out = capsys.readouterr().out
    assert "=== Graduated plan-reader re-run ===" in out
    assert "1 plan-reading graduated_records examined, 1 red (1 listed)" in out


def test_red_non_plan_record_is_ignored(tmp_path: Path) -> None:
    _record(tmp_path, "rec-red", ["bash", "-c", "exit 1"])
    _listed(tmp_path, [])
    assert _run(tmp_path) == []


def test_listed_green_record_is_not_reported(tmp_path: Path) -> None:
    _record(tmp_path, "rec-ok", PLAN_GREEN)
    _listed(tmp_path, ["rec-ok"])
    assert _run(tmp_path) == []


def test_select_never_runs_unselected_records(tmp_path: Path) -> None:
    hit = tmp_path / "selected.sentinel"
    miss = tmp_path / "unselected.sentinel"
    _record(tmp_path, "rec-sel", ["bash", "-c", f"echo docs/plans/x; touch {hit}"])
    _record(tmp_path, "rec-unsel", ["bash", "-c", f"touch {miss}"])
    rerun.evaluate(tmp_path, select=readers.reads_plans)
    assert hit.exists()
    assert not miss.exists()


def test_no_plan_readers_is_vacuous(tmp_path: Path) -> None:
    _record(tmp_path, "rec-ok")
    _listed(tmp_path, [])
    assert _run(tmp_path) == []
    declaration = registry._CURRENT_DECLARATION
    assert declaration is not None
    assert declaration.kind == "examined"
    assert declaration.count == 0


def _would_run(pre_globs: tuple[str, ...], paths: list[str]) -> bool:
    # A subprocess keeps scripts.validate out of this worker's sys.modules (test_manifest_contracts asserts it is absent).
    code = (
        "import json, sys; from scripts.validate import _should_run_in_pre as f; "
        "a = json.loads(sys.argv[1]); print(f(tuple(a[0]), a[1], True))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code, json.dumps([list(pre_globs), paths])],
        cwd=str(rerun.HEAD_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return proc.stdout.strip() == "True"


def test_manifest_entry_is_pre_only_on_plan_diffs() -> None:
    entry = registry._ALL_ENTRIES["validate_graduated_rerun_plan_readers"]
    assert entry.pre is True
    assert entry.full_segment is None
    assert _would_run(entry.pre_globs, ["docs/plans/PLAN-x.yaml"]) is True
    assert _would_run(entry.pre_globs, ["src/x.py"]) is False


def test_evaluate_without_select_is_unchanged(tmp_path: Path) -> None:
    _record(tmp_path, "rec-a", PLAN_RED)
    _record(tmp_path, "rec-b")
    assert rerun.evaluate(tmp_path) == rerun.evaluate(tmp_path, select=None)
    assert [o.check_id for o in rerun.evaluate(tmp_path)] == ["rec-a", "rec-b"]
