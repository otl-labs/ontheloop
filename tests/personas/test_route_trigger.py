"""The trigger upstream's model-route gate: kill switch, one verdict per head, the closed keyed list, fixture bypass."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.personas import route
from scripts.session import merge_checks

ROOT = Path(__file__).resolve().parents[2]
APP = 4242
SCOUT = "decision_scout"
FIXTURE = "canary_persona"
ENABLED = 'defaults: {local: "enabled", cloud: "enabled"}'


def _root(tmp_path: Path, advisory: str | None = None) -> Path:
    main = tmp_path / "main"
    (main / "docs/contracts").mkdir(parents=True)
    for rel in (route.PERSONAS_CONTRACT, route.SCOPE_CONTRACT):
        shutil.copy(ROOT / rel, main / rel)
    doc = yaml.safe_load((ROOT / route.CONFIG).read_text(encoding="utf-8"))
    for row in doc["settings"]:
        if row["id"] == "gates.verdict_app.app_id":
            row["defaults"] = {"local": APP, "cloud": APP}
        if row["id"] == f"gates.{SCOUT}.advisory_runs" and advisory is not None:
            row["defaults"] = (
                yaml.safe_load("{" + advisory.removeprefix("defaults: {")) if advisory.startswith("defaults") else {}
            )
    (main / route.CONFIG).write_text(yaml.safe_dump(doc), encoding="utf-8")
    return main


def _filter(root: Path, routes: list[str]) -> tuple[list[str], bool]:
    return route.filter_upstream("trigger", "success", routes, route.TRIGGER_PATH, root, "o/r", "t", "h")


def _runs(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]]) -> None:
    monkeypatch.setattr(route.resolve_pr, "get_json", lambda url, token: {"check_runs": rows})


def _scout_check(title: str, app: int = APP) -> dict[str, Any]:
    return {"name": f"persona-verdict/{SCOUT}", "app": {"id": app}, "output": {"title": title}}


def test_decision_scout_keyed_on_trigger_upstream_once_implemented(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path)
    _runs(monkeypatch, [])
    assert route.advisory_state(SCOUT, ROOT) == ("enabled", "")
    assert _filter(root, [SCOUT]) == ([SCOUT], True)
    assert route.is_keyed([SCOUT], "trigger", root)
    assert not route.is_keyed([SCOUT, FIXTURE], "trigger", root)
    plan = "docs/plans/PLAN-x.yaml"
    files = [{"filename": plan, "status": "added"}]
    assert route.route(files, False, False, "o/r", "h", "m", "t", root) == [SCOUT]
    doc = yaml.safe_load((root / route.PERSONAS_CONTRACT).read_text(encoding="utf-8"))
    for row in doc["personas"]:
        if row["persona"] == SCOUT:
            row["implementation"] = {"status": "pending", "owning_item": "T4.30:c3"}
    (root / route.PERSONAS_CONTRACT).write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert route.route(files, False, False, "o/r", "h", "m", "t", root) == []


def test_plan_critic_not_keyed_on_trigger_even_when_implemented(tmp_path: Path) -> None:
    root = _root(tmp_path)
    doc = yaml.safe_load((root / route.PERSONAS_CONTRACT).read_text(encoding="utf-8"))
    for row in doc["personas"]:
        if row["persona"] == "plan_critic":
            row["implementation"] = {"status": "implemented", "location": "x"}
    (root / route.PERSONAS_CONTRACT).write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert "plan_critic" in route.bound_personas(root)
    assert not route.is_keyed(["plan_critic"], "trigger", root)
    assert not route.is_keyed([SCOUT], "ci", root)
    assert merge_checks.TRIGGER_KEYED_PERSONAS == frozenset({SCOUT})


def test_trigger_advisory_runs_off_or_bad_skips_a_model_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _runs(monkeypatch, [])
    row_ids = [r["id"] for r in yaml.safe_load((ROOT / route.CONFIG).read_text(encoding="utf-8"))["settings"]]
    assert f"gates.{SCOUT}.advisory_runs" in row_ids
    cases = [
        ('defaults: {local: "disabled", cloud: "disabled"}', f"PERSONA-VERDICT skip {SCOUT}: advisory_runs disabled"),
        ("defaults: {local: on, cloud: on}", f"PERSONA-VERDICT skip {SCOUT}: advisory_runs bad value"),
        ('defaults: {local: "enabled", cloud: "disabled"}', f"PERSONA-VERDICT skip {SCOUT}: advisory_runs bad value"),
    ]
    for index, (advisory, line) in enumerate(cases):
        assert _filter(_root(tmp_path / str(index), advisory), [SCOUT]) == ([], True)
        assert line in capsys.readouterr().out


def test_trigger_rerun_with_existing_check_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _root(tmp_path)
    _runs(monkeypatch, [_scout_check("verdict NO_FLAGS")])
    assert _filter(root, [SCOUT]) == ([], True)
    assert "already carries a verdict check" in capsys.readouterr().out
    _runs(monkeypatch, [_scout_check("failure budget_exhausted")])
    assert _filter(root, [SCOUT])[0] == [SCOUT]
    _runs(monkeypatch, [_scout_check("verdict NO_FLAGS", app=1)])
    assert _filter(root, [SCOUT])[0] == [SCOUT]


def test_trigger_fixture_routes_bypass_the_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path, 'defaults: {local: "disabled", cloud: "disabled"}')
    _runs(monkeypatch, [_scout_check("verdict NO_FLAGS")])
    assert _filter(root, [FIXTURE]) == ([FIXTURE], True)
    assert _filter(root, [FIXTURE, SCOUT]) == ([FIXTURE], True)
    assert _filter(root, ["code_reviewer", FIXTURE])[0] == [FIXTURE]
