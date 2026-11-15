"""Mirror test of the plan check's two re-scout legs: the newest-version rule and the barrier wiring."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from scripts.checks import _common
from scripts.checks.roadmap import _plan_rescout_barrier as barrier_module
from scripts.checks.roadmap import _plan_rescout_wiring as module
from scripts.checks.roadmap import validate_plan_documents as vpd
from scripts.roadmap import plan_document

pytestmark = pytest.mark.unit


def _doc(version: int, *rows: Any) -> SimpleNamespace:
    return SimpleNamespace(schema_version=version, gate_rounds=list(rows))


def _kw(tmp_path: Path, docs: dict[str, Any], **extra: Any) -> dict[str, Any]:
    paths = [tmp_path / name for name in docs]
    base = {"docs": docs, "plan_paths": paths, "root": tmp_path, "base": "b", "push_context": False, "event": None, "floor": 5}
    return {**base, **extra}


def test_newest_schema_version_is_the_max_supported_and_at_least_the_floor() -> None:
    assert module.newest_schema_version() == max(plan_document._SUPPORTED_VERSIONS)
    assert module.newest_schema_version() >= vpd._MIN_NEW_PLAN_SCHEMA_VERSION


def test_newest_version_findings_rules() -> None:
    newest = module.newest_schema_version()
    docs = {
        "PLAN-old.yaml": _doc(newest - 1),
        "PLAN-new.yaml": _doc(newest),
        "PLAN-hist.yaml": _doc(newest - 1),
        "PLAN-low.yaml": _doc(4),
    }
    added = {"PLAN-old.yaml", "PLAN-new.yaml", "PLAN-low.yaml"}
    findings = module.newest_version_findings(added, docs, 5)
    assert len(findings) == 1 and "PLAN-old.yaml" in findings[0]
    assert f"schema_version {newest}" in findings[0] and f"got {newest - 1}" in findings[0] and "bump it" in findings[0]


def test_seam_names_are_used_and_the_barrier_is_called_with_seam_true(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def spy(entries, docs, root, base, push, *, diff_count, seam, event):
        seen.update(entries=entries, diff_count=diff_count, seam=seam, event=event)
        return barrier_module.BarrierResult(examined=2, compared=1)

    monkeypatch.setattr(barrier_module, "barrier", spy)
    docs = {"PLAN-a.yaml": _doc(6), "PLAN-b.yaml": _doc(6)}
    kw = _kw(
        tmp_path,
        docs,
        event="pull_request",
        added_plan_names={"PLAN-a.yaml"},
        modified_plan_names={"PLAN-b.yaml", "PLAN-x.yaml"},
    )
    failures, warnings, line = module.run(**kw)
    assert seen["seam"] is True and seen["diff_count"] == 3 and seen["event"] == "pull_request"
    assert seen["entries"] == [("A", str(tmp_path / "PLAN-a.yaml")), ("M", str(tmp_path / "PLAN-b.yaml"))]
    assert (failures, warnings) == ([], []) and line == " re-scout barrier: 2 plan(s) examined, 1 compared, 0 failing"


def test_git_path_filters_plan_entries_counts_tracked_and_passes_seam_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    diff = [
        ("A", "docs/plans/PLAN-a.yaml"),
        ("M", "src/x.py"),
        ("D", "src/y.py"),
        ("??", "docs/plans/PLAN-b.yaml"),
        ("??", "z.txt"),
        ("D", "docs/plans/PLAN-c.yaml"),
    ]
    monkeypatch.setattr(_common, "get_status_aware_diff", lambda root=None: diff)
    seen: dict[str, Any] = {}

    def spy(entries, docs, root, base, push, *, diff_count, seam, event):
        seen.update(entries=entries, diff_count=diff_count, seam=seam)
        return barrier_module.BarrierResult()

    monkeypatch.setattr(barrier_module, "barrier", spy)
    docs = {"PLAN-a.yaml": _doc(5, SimpleNamespace(gate="x")), "PLAN-b.yaml": _doc(5)}
    _, _, line = module.run(**_kw(tmp_path, docs))
    assert seen["seam"] is False and seen["diff_count"] == 4
    assert seen["entries"] == [("A", "docs/plans/PLAN-a.yaml"), ("??", "docs/plans/PLAN-b.yaml")]
    assert line == " re-scout barrier: no added or modified plan in this diff"


def test_a_plan_that_failed_its_schema_load_is_not_passed(tmp_path: Path) -> None:
    kw = _kw(tmp_path, {"PLAN-a.yaml": _doc(4)}, added_plan_names={"PLAN-a.yaml", "PLAN-broken.yaml"})
    kw["plan_paths"] = [*kw["plan_paths"], tmp_path / "PLAN-broken.yaml"]
    failures, _, line = module.run(**kw)
    assert failures == [] and "1 plan(s) examined" in line


def test_barrier_findings_and_newest_version_findings_are_routed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    newest = module.newest_schema_version()
    docs = {"PLAN-a.yaml": _doc(newest - 1)}
    result = barrier_module.BarrierResult(failures=["F"], warnings=["W"], examined=1)
    monkeypatch.setattr(barrier_module, "barrier", lambda *a, **k: result)
    kw = _kw(tmp_path, docs, added_plan_names={"PLAN-a.yaml"}, modified_plan_names=set())
    failures, warnings, line = module.run(**kw)
    assert failures[0] == "F" and len(failures) == 2 and "newly-added plans must declare" in failures[1] and warnings == ["W"]
    assert line.endswith("2 failing")
    failures, warnings, _ = module.run(**{**kw, "push_context": True})
    assert failures == ["F"] and len(warnings) == 2 and "newly-added plans must declare" in warnings[1]


def test_real_barrier_over_a_fixture_tree_fails_a_stale_row(tmp_path: Path) -> None:
    (tmp_path / "docs" / "contracts").mkdir(parents=True)
    (tmp_path / "docs" / "DECISIONS.md").write_text("## Decision 217: T (Decided)\n\nb\n", encoding="utf-8")
    item = {"id": "T4.32", "tier": "T4", "name": "n", "status": "in_progress", "intent": "i", "files_in_scope": ["src/a.py"]}
    doc = {
        "document": {"id": "R", "version": 1, "status": "ratified", "filed_via": "ops_decisions:dec-108"},
        "tier_items": [item],
    }
    (tmp_path / "docs" / "ROADMAP-PLATFORM.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    plan = {
        "slug": "p",
        "intent": "i",
        "plan_type": "IMPLEMENTATION",
        "verification_tier": "V2",
        "phase": "T4.32",
        "cites": [],
        "scope": [{"file": "src/a.py", "action": "Create", "purpose": "p"}],
        "amends": [],
        "introduces": [],
        "closes_criteria": [],
    }
    (tmp_path / "PLAN-p.yaml").write_text(yaml.safe_dump(plan), encoding="utf-8")
    row = SimpleNamespace(gate="decision-scout", verdict="NO_FLAGS", scout_input_fingerprint="v1:" + "0" * 64)
    kw = _kw(tmp_path, {"PLAN-p.yaml": _doc(6, row)}, added_plan_names={"PLAN-p.yaml"}, modified_plan_names=set())
    failures, _, line = module.run(**kw)
    assert len(failures) == 1 and "re-scout required" in failures[0]
    assert line == " re-scout barrier: 1 plan(s) examined, 1 compared, 1 failing"
