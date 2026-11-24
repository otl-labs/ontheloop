"""CLI main() + --emit-dir concern: tests/ci_rca/evidence/test_main_emit.py (rec-2709 Wave 10).

Split from the former tests/test_ci_rca_evidence.py monolith: TestMain, TestEmitDir.
"""

import json
import runpy
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.ci_rca.evidence import main
from scripts.ci_rca.log_evidence import SCHEMA, bound_text, publish_envelope


class TestEmitDir:
    def test_emit_dir_preserves_validated_retrieval_provenance(self, log_file, taxonomy_file, tmp_path, capsys):
        body = log_file.read_text(encoding="utf-8")
        _, limits = bound_text(body, len(body.encode("utf-8")) + 1, len(body.splitlines()) + 1)
        envelope_path = tmp_path / "retrieval.json"
        publish_envelope(
            envelope_path,
            {
                "schema": SCHEMA,
                "identity": {"repository": "owner/repo", "run_id": 42},
                "failed_jobs": [
                    {
                        "job_id": 7,
                        "conclusion": "failure",
                        "failed_steps": [{"step_index": 2, "conclusion": "failure"}],
                    }
                ],
                "retrieval_path": "primary",
                "scope": [
                    {
                        "job_id": 7,
                        "steps": [
                            {"step_index": 1, "conclusion": "success", "log_retrieved": False},
                            {"step_index": 2, "conclusion": "failure", "log_retrieved": True},
                        ],
                    }
                ],
                "fallback_selection": {"queried_job_ids": [7], "unqueried_job_ids": [], "unqueried_reason": None},
                "body": body,
                "limits": limits,
                "recovery": {"url": "https://github.com/owner/repo/actions/runs/42", "state": "available"},
            },
        )
        emit_dir = tmp_path / "emit_retrieval"
        with (
            patch("scripts.ci_rca.tier_map.probe_runtime", return_value=("median=50ms", 0.05)),
            patch("scripts.ci_rca.tier_map.build_tier_membership", return_value={}),
            patch("scripts.ci_rca.evidence._upload_to_s3"),
            patch("scripts.ci_rca.evidence._resolve_bucket", return_value="test-bucket"),
        ):
            main(
                [
                    "--log-file",
                    str(log_file),
                    "--retrieval-envelope",
                    str(envelope_path),
                    "--workflow-name",
                    "CI",
                    "--workflow-run-id",
                    "42",
                    "--taxonomy-path",
                    str(taxonomy_file),
                    "--emit-dir",
                    str(emit_dir),
                ]
            )
        local_path = next(
            line.split("=", 1)[1] for line in capsys.readouterr().out.splitlines() if line.startswith("BUNDLE_LOCAL=")
        )
        retrieval = json.loads(Path(local_path).read_text(encoding="utf-8"))["retrieval_evidence"]
        assert retrieval["limits"] == limits
        assert retrieval["recovery"]["url"] == "https://github.com/owner/repo/actions/runs/42"

    def test_emit_dir_writes_local_bundle(self, log_file, taxonomy_file, tmp_path, capsys):
        """--emit-dir writes <dir>/<sha>.json independent of S3 outcome and prints BUNDLE_LOCAL=<path>."""
        emit_dir = tmp_path / "emit"
        with patch("scripts.ci_rca.tier_map.probe_runtime", return_value=("median=50ms", 0.05)):
            with patch("scripts.ci_rca.tier_map.build_tier_membership", return_value={}):
                with patch("scripts.ci_rca.evidence._upload_to_s3"):
                    with patch("scripts.ci_rca.evidence._resolve_bucket", return_value="test-bucket"):
                        main(
                            [
                                "--log-file",
                                str(log_file),
                                "--workflow-name",
                                "CI",
                                "--workflow-run-id",
                                "42",
                                "--taxonomy-path",
                                str(taxonomy_file),
                                "--emit-dir",
                                str(emit_dir),
                            ]
                        )
        out = capsys.readouterr().out
        assert "BUNDLE_LOCAL=" in out
        local_line = next(ln for ln in out.splitlines() if ln.startswith("BUNDLE_LOCAL="))
        local_path = local_line.split("=", 1)[1]
        assert Path(local_path).exists()
        parsed = json.loads(Path(local_path).read_bytes())
        assert "sha256" in parsed
        assert len(parsed["sha256"]) == 64

    def test_emit_dir_writes_on_s3_failure(self, log_file, taxonomy_file, tmp_path, capsys):
        """--emit-dir writes the local bundle even when S3 upload fails."""
        import scripts.ci_rca.evidence as ev_mod

        emit_dir = tmp_path / "emit_fail"
        original_pending = ev_mod._PENDING_DIR
        ev_mod._PENDING_DIR = tmp_path / "pending"
        try:
            with patch("scripts.ci_rca.tier_map.probe_runtime", return_value=("median=50ms", 0.05)):
                with patch("scripts.ci_rca.tier_map.build_tier_membership", return_value={}):
                    with patch("scripts.ci_rca.evidence._upload_to_s3", side_effect=Exception("S3 down")):
                        with patch("scripts.ci_rca.evidence._resolve_bucket", return_value="test-bucket"):
                            main(
                                [
                                    "--log-file",
                                    str(log_file),
                                    "--workflow-name",
                                    "CI",
                                    "--workflow-run-id",
                                    "99",
                                    "--taxonomy-path",
                                    str(taxonomy_file),
                                    "--emit-dir",
                                    str(emit_dir),
                                ]
                            )
        finally:
            ev_mod._PENDING_DIR = original_pending
        out = capsys.readouterr().out
        assert "BUNDLE_LOCAL=" in out
        local_line = next(ln for ln in out.splitlines() if ln.startswith("BUNDLE_LOCAL="))
        local_path = local_line.split("=", 1)[1]
        assert Path(local_path).exists()
        parsed = json.loads(Path(local_path).read_bytes())
        assert "sha256" in parsed


class TestEmitDirScopeDeclaration:
    """VP step 5 (AC16): the emitted bundle's retrieval_evidence carries the scope block --
    a NEW class deliberately, not TestEmitDir (VF-06's origin/main revert leg would find
    TestEmitDir already green and reject the graduated row as tautological)."""

    def test_emitted_bundle_retrieval_evidence_carries_scope(self, log_file, taxonomy_file, tmp_path, capsys):
        body = log_file.read_text(encoding="utf-8")
        _, limits = bound_text(body, len(body.encode("utf-8")) + 1, len(body.splitlines()) + 1)
        envelope_path = tmp_path / "retrieval.json"
        scope = [
            {
                "job_id": 7,
                "steps": [
                    {"step_index": 1, "conclusion": "success", "log_retrieved": False},
                    {"step_index": 2, "conclusion": "failure", "log_retrieved": True},
                ],
            }
        ]
        publish_envelope(
            envelope_path,
            {
                "schema": SCHEMA,
                "identity": {"repository": "owner/repo", "run_id": 42},
                "failed_jobs": [
                    {
                        "job_id": 7,
                        "conclusion": "failure",
                        "failed_steps": [{"step_index": 2, "conclusion": "failure"}],
                    }
                ],
                "retrieval_path": "primary",
                "scope": scope,
                "fallback_selection": {"queried_job_ids": [7], "unqueried_job_ids": [], "unqueried_reason": None},
                "body": body,
                "limits": limits,
                "recovery": {"url": "https://github.com/owner/repo/actions/runs/42", "state": "available"},
            },
        )
        emit_dir = tmp_path / "emit_scope"
        with (
            patch("scripts.ci_rca.tier_map.probe_runtime", return_value=("median=50ms", 0.05)),
            patch("scripts.ci_rca.tier_map.build_tier_membership", return_value={}),
            patch("scripts.ci_rca.evidence._upload_to_s3"),
            patch("scripts.ci_rca.evidence._resolve_bucket", return_value="test-bucket"),
        ):
            main(
                [
                    "--log-file",
                    str(log_file),
                    "--retrieval-envelope",
                    str(envelope_path),
                    "--workflow-name",
                    "CI",
                    "--workflow-run-id",
                    "42",
                    "--taxonomy-path",
                    str(taxonomy_file),
                    "--emit-dir",
                    str(emit_dir),
                ]
            )
        local_path = next(
            line.split("=", 1)[1] for line in capsys.readouterr().out.splitlines() if line.startswith("BUNDLE_LOCAL=")
        )
        retrieval = json.loads(Path(local_path).read_text(encoding="utf-8"))["retrieval_evidence"]
        assert retrieval["scope"] == scope


class TestMain:
    def test_missing_log_file_exits(self, tmp_path):
        with pytest.raises(SystemExit) as exc_info:
            main(["--log-file", str(tmp_path / "nope.log"), "--workflow-name", "CI", "--workflow-run-id", "1"])
        assert exc_info.value.code != 0

    def test_print_bundle(self, log_file, taxonomy_file, capsys):
        with patch("scripts.ci_rca.tier_map.probe_runtime", return_value=("median=50ms", 0.05)):
            with patch("scripts.ci_rca.tier_map.build_tier_membership", return_value={}):
                with patch("scripts.ci_rca.evidence._upload_to_s3"):
                    with patch("scripts.ci_rca.evidence._resolve_bucket", return_value="test-bucket"):
                        main(
                            [
                                "--log-file",
                                str(log_file),
                                "--workflow-name",
                                "CI",
                                "--workflow-run-id",
                                "1",
                                "--taxonomy-path",
                                str(taxonomy_file),
                                "--print-bundle",
                            ]
                        )
        out = capsys.readouterr().out
        assert "BUNDLE_SHA=" in out


def test_module_entrypoint_rejects_missing_log(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys,
        "argv",
        ["evidence", "--log-file", str(tmp_path / "missing"), "--workflow-name", "CI", "--workflow-run-id", "1"],
    )
    with pytest.raises(SystemExit):
        runpy.run_module("scripts.ci_rca.evidence", run_name="__main__")


def test_no_upload_never_uploads_and_reports_the_pending_path(log_file, taxonomy_file, tmp_path, capsys):
    """--no-upload (rec-5493): the S3 leg is never called, BUNDLE_S3_URI is empty, the emit file is still written."""
    import scripts.ci_rca.evidence as ev_mod

    emit_dir = tmp_path / "emit_no_upload"
    with (
        patch.object(ev_mod, "_PENDING_DIR", tmp_path / "pending"),
        patch("scripts.ci_rca.tier_map.probe_runtime", return_value=("median=50ms", 0.05)),
        patch("scripts.ci_rca.tier_map.build_tier_membership", return_value={}),
        patch("scripts.ci_rca.evidence._upload_to_s3") as upload,
        patch("scripts.ci_rca.evidence._resolve_bucket", return_value="test-bucket"),
    ):
        main(
            [
                "--no-upload",
                "--log-file",
                str(log_file),
                "--workflow-name",
                "CI",
                "--workflow-run-id",
                "5",
                "--taxonomy-path",
                str(taxonomy_file),
                "--emit-dir",
                str(emit_dir),
            ]
        )
    upload.assert_not_called()
    out = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines() if "=" in line)
    assert out["BUNDLE_S3_URI"] == ""
    assert Path(out["BUNDLE_PATH"]).parent == tmp_path / "pending"
    assert Path(out["BUNDLE_PATH"]).exists()
    assert Path(out["BUNDLE_LOCAL"]).exists()
