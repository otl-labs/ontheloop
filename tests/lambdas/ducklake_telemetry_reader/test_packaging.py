"""Packaging scope for the telemetry reader: its own artifact, event-read closure only, never a content module."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.lambda_manifest import compute_affected_artifacts

_ROOT = Path(__file__).resolve().parents[3]
_HANDLER = "src/lambdas/ducklake_telemetry_reader/handler.py"
_PRESENT = (
    "src/common/ducklake_event_reads.py",
    "src/common/ducklake_event_read_sql.py",
    "src/common/ducklake_event_read_verbs.py",
    "src/common/ducklake_named_reads.py",
    "src/common/ducklake_read_deadline.py",
    "src/common/ducklake_runtime.py",
    "src/common/ducklake_handler_errors.py",
    "src/common/ducklake_metrics.py",
    "src/row_rules/rules.py",
    "src/telemetry/identity.py",
    "src/telemetry/timestamps.py",
    "scripts/__init__.py",
    "scripts/aws_profile.py",
)
_ABSENT = (
    "src/common/ducklake_event_content.py",
    "src/common/ducklake_telemetry_blobstore.py",
    "src/telemetry/blobs.py",
    "src/telemetry/request.py",
)


def _manifest() -> dict:
    path = _ROOT / "src" / "lambdas" / "ducklake_telemetry_reader" / "manifest.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_manifest_bundles_the_event_reads_and_no_content_module() -> None:
    manifest = _manifest()
    includes = manifest["includes"]
    for rel in _PRESENT:
        assert rel in includes, rel
    for rel in _ABSENT:
        assert rel not in includes, rel
    assert manifest["handlers"] == [_HANDLER]
    assert manifest["artifact"] == "ducklake-telemetry-reader.zip"
    assert manifest["functions"] == ["agent-platform-ducklake-telemetry-reader"]
    assert manifest["runtime_config"] == ["/agent-platform/ducklake/telemetry_reader_url"]


def test_data_pipeline_excludes_the_package() -> None:
    manifest = yaml.safe_load((_ROOT / "src" / "lambdas" / "data-pipeline" / "manifest.yaml").read_text(encoding="utf-8"))
    assert "src/lambdas/ducklake_telemetry_reader" in manifest["excludes"]


def test_handler_module_affects_only_this_artifact() -> None:
    assert sorted(compute_affected_artifacts([_HANDLER])) == ["ducklake_telemetry_reader"]
