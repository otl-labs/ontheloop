"""Packaging scope for the content reader: a stub with a complete import closure; the telemetry reader bundle is unchanged."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml

from scripts.lambda_manifest import compute_affected_artifacts, load_all, stage_bundle
from src.lambdas.ducklake_telemetry_content_reader import handler

_ROOT = Path(__file__).resolve().parents[3]
_PKG = "src/lambdas/ducklake_telemetry_content_reader"
_HANDLER = f"{_PKG}/handler.py"
_VERBS = "src/common/ducklake_content_read_verbs.py"
_PRESENT = (
    "src/common/ducklake_event_content.py",
    "src/common/ducklake_telemetry_blobstore.py",
    "src/telemetry/blobs.py",
    "src/telemetry/request.py",
    _VERBS,
    "src/sensitive_values/__init__.py",
    "src/sensitive_values/classes.py",
    "src/sensitive_values/profiles.py",
    "src/sensitive_values/engine.py",
    f"{_PKG}/masked.py",
    f"{_PKG}/access_log.py",
    f"{_PKG}/handler.py",
)


def _manifest(rel: str) -> dict:
    return yaml.safe_load((_ROOT / rel / "manifest.yaml").read_text(encoding="utf-8"))


def test_manifest_is_a_stub_bundling_the_content_closure() -> None:
    manifest = _manifest(_PKG)
    for rel in _PRESENT:
        assert rel in manifest["includes"], rel
    assert manifest["status"] == "stub" and manifest["handlers"] == [_HANDLER]
    assert manifest["artifact"] == "ducklake-telemetry-content-reader.zip"
    assert manifest["functions"] == ["agent-platform-ducklake-telemetry-content"]
    assert manifest["runtime_config"] == ["/agent-platform/ducklake/telemetry_content_reader_url"]


def test_the_telemetry_reader_still_bundles_none_of_it() -> None:
    includes = _manifest("src/lambdas/ducklake_telemetry_reader")["includes"]
    for rel in _PRESENT:
        assert rel not in includes, rel


def test_data_pipeline_excludes_the_package_and_the_verbs() -> None:
    excludes = _manifest("src/lambdas/data-pipeline")["excludes"]
    assert _PKG in excludes and _VERBS in excludes


def test_stub_affects_no_artifact() -> None:
    assert compute_affected_artifacts([_HANDLER, _VERBS]) == {}


def test_staged_import_closure_is_complete(tmp_path: Path) -> None:
    stage_bundle(load_all()["ducklake_telemetry_content_reader"], tmp_path)
    code = "import src.lambdas.ducklake_telemetry_content_reader.handler as h; print(sorted(h.SERVED_VERBS))"
    done = subprocess.run(
        [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(tmp_path)!r}); {code}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=tmp_path,
    )
    assert done.returncode == 0, done.stderr[-400:]
    assert "session_transcript_masked" in done.stdout


def test_contract_names_the_served_verbs() -> None:
    text = (_ROOT / "docs/contracts/ducklake_telemetry_content_reader.yaml").read_text(encoding="utf-8")
    contract = yaml.safe_load(text)
    described = contract["contract"]["description"] + contract["verbs"]["named_read"]["payload_schema_ref"]
    named = set(re.findall(r"\b(?:transcript_row_masked|session_transcript_masked|\w+_masked)\b", described))
    assert named == set(handler.SERVED_VERBS)
    assert set(contract["verbs"]) == {"describe", "named_read"}
