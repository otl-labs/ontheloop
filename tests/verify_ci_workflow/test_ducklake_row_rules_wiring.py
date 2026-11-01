"""Packaging and deploy wiring for the shared row-rule engine and the writer adapter (rec-4158 plan B): the DuckLake
bundles carry them, data-pipeline never does, and the governed deploy workflow redeploys on an engine edit and runs the
--lambda-row-rules gate after the reader gate."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.lambda_manifest import compute_affected_artifacts

_ROOT = Path(__file__).resolve().parents[2]
_FILES = (
    "src/common/ducklake_write_rules.py",
    "src/row_rules/__init__.py",
    "src/row_rules/rules.py",
)
_DUCKLAKE = (
    "ducklake_writer",
    "ducklake_reader",
    "ducklake_maintenance",
    "ducklake_maintenance_smoke",
    "ducklake_catalog_dr",
    "ducklake_telemetry_writer",
    "ducklake_telemetry_reader",
)
_WORKFLOW = _ROOT / ".github" / "workflows" / "deploy-ducklake-lambdas.yml"


def _manifest(slug: str) -> dict:
    return yaml.safe_load((_ROOT / "src" / "lambdas" / slug / "manifest.yaml").read_text(encoding="utf-8"))


def test_ducklake_manifests_include_the_engine_files() -> None:
    for slug in _DUCKLAKE:
        includes = _manifest(slug)["includes"]
        for rel in _FILES:
            assert rel in includes, (slug, rel)


def test_data_pipeline_excludes_the_engine_and_is_never_marked_affected_by_it() -> None:
    excludes = _manifest("data-pipeline")["excludes"]
    assert "src/common/ducklake_write_rules.py" in excludes and "src/row_rules" in excludes
    for rel in _FILES:
        affected = compute_affected_artifacts([rel])
        assert sorted(affected) == sorted(_DUCKLAKE), (rel, sorted(affected))


def test_workflow_redeploys_on_engine_edits_and_smokes_the_row_rules_gate() -> None:
    doc = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    triggers = doc.get("on") or doc.get(True)
    assert "src/row_rules/**" in triggers["push"]["paths"]
    steps = [step.get("run", "") for step in doc["jobs"]["smoke"]["steps"]]
    chained = [run for run in steps if "--lambda-row-rules" in run]
    assert len(chained) == 1 and "\n" not in chained[0].strip()  # one line: the R3 body baseline stays untouched
    assert chained[0].index("--lambda-reader") < chained[0].index("--lambda-row-rules")
    assert chained[0].count("&&") == 1
