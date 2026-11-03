"""Tests for the scripts/ops_portal/ci_rca_runtime.py cross-check spine: _run_ci_rca_cross_check
/ _load_and_verify_bundle (c10), _EvidenceBundleRef conditional s3_uri (CIRCA-06),
why_chain_terminus_override typed sub-model (CIRCA-08), per-rule warn-mode deficiency
stamping (CIRCA-04), the version-gated why_chain length ceiling, and the 'unknown' detection-gap
enum gate (CIRCA-09).

Split out of the former tests/test_ops_data_portal.py monolith (rec-2709 Wave 3).

_CI_RCA_FIELDS and _VALID_CONTEXT_V2 are duplicated verbatim from the monolith (also used by
test_ci_rca_schema.py / test_ci_rca_dispute.py / test_ci_rca_propose_close.py) rather than
hoisted to tests/fixtures/ -- see test_ci_rca_schema.py's header note.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

duckdb = pytest.importorskip("duckdb")
from pydantic import ValidationError  # noqa: E402

from scripts.ops_portal import ci_rca_schema as _ci_rca_schema_mod  # noqa: E402
from tests.fixtures.ops_portal_records import VALID_FIELDS as _VALID_FIELDS  # noqa: E402

_CI_RCA_FIELDS = {
    **_VALID_FIELDS,
    "source": "ci_rca",
    "context": ("CI RCA test rec with sufficient length to satisfy the 80-char minimum for the legacy context column field."),
}

_VALID_CONTEXT_V2 = {
    "schema_version": 1,
    "proximate_cause": (
        "validate_sloc_limits() raised: scripts/roadmap/platform_roadmap.py is 810 SLOC, exceeds 500 limit "
        "(Decision 43, no complexity-waiver header found in first 10 lines)."
    ),
    "why_chain": [
        "The file was committed at over 500 SLOC in a single PR with no incremental breakpoint.",
        "No local --pre check fired because validate_sloc_limits() is presubmit-tier only.",
        (
            "The validate_sloc_limits() check was placed in the presubmit tier not --pre despite being O(lines); "
            "this tier placement defect is the gap at scripts/validate.py:2294."
        ),
    ],
    "detection_gap": {
        "earliest_viable_gate": "pre",
        "actual_gate_that_caught_it": "CI",
        "gap_explanation": (
            "validate_sloc_limits() gates on scope=='all' at scripts/validate.py:2294, unreachable from "
            "--pre (exits at scripts/validate.py:2284). Gap is tier-placement, not logic."
        ),
    },
    "recurrence_class": "instance_of_known_pattern",
    "corrective_action": (
        "Add a complexity-waiver header OR refactor the module below 500 SLOC to satisfy the "
        "validate_sloc_limits() check in scripts/validate.py and unblock CI."
    ),
    "preventive_action": (
        "Promote validate_sloc_limits() to the --pre tier at scripts/validate.py so the check fires "
        "during local development and prevents the same tier-placement failure mode in future PRs. "
        "Additionally gate new check additions: require a documented tier-placement rationale."
    ),
}


class TestCiRcaCrossCheckSpine:
    """c10: _run_ci_rca_cross_check() and _load_and_verify_bundle() contract tests."""

    def _make_bundle(self, tmp_path, **overrides):
        """Write a valid bundle JSON to tmp_path/logs/.ci-rca-evidence-pending/<sha>.json."""
        import hashlib as _hashlib
        import json as _json

        bundle_data = {
            "earliest_viable_gate": "pre",
            "escape_mode": "tier_misplaced",
            "vacuous_pass": False,
        }
        bundle_data.update(overrides)
        payload = {k: v for k, v in bundle_data.items() if k != "sha256"}
        sha = _hashlib.sha256(
            _json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        ).hexdigest()
        bundle_data["sha256"] = sha
        pending_dir = tmp_path / "logs" / ".ci-rca-evidence-pending"
        pending_dir.mkdir(parents=True, exist_ok=True)
        (pending_dir / f"{sha}.json").write_bytes(
            _json.dumps(bundle_data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        )
        return sha, bundle_data

    def _ctx_v2(self, **detection_gap_overrides):
        """Return a minimal valid context_v2_json dict."""
        dg = {
            "earliest_viable_gate": "pre",
            "actual_gate_that_caught_it": "CI",
            "gap_explanation": "test gap explanation with enough chars for the field",
        }
        dg.update(detection_gap_overrides)
        return {
            "schema_version": 2,
            "proximate_cause": "x" * 100,
            "why_chain": ["a " * 25, "b " * 25, "c " * 25 + "systemic scripts/validate.py:1"],
            "detection_gap": dg,
            "recurrence_class": "novel",
            "corrective_action": "x" * 100,
            "preventive_action": "x" * 100,
        }

    def test_no_evidence_bundle_ref_skips_cross_check(self, tmp_path):
        """Cross-check is skipped when evidence_bundle_ref is absent -- no issues raised."""
        import scripts.ops_data_portal as p

        ctx = self._ctx_v2()
        p._run_ci_rca_cross_check(ctx)  # should not raise

    def test_bundle_not_found_skips_cross_check(self, tmp_path):
        """Cross-check is skipped when the bundle file is not found locally."""
        import scripts.ops_data_portal as p

        ctx = self._ctx_v2()
        ctx["evidence_bundle_ref"] = {"sha256": "a" * 64, "s3_uri": "", "upload_status": "ok"}
        with patch.object(_ci_rca_schema_mod, "ROOT", tmp_path):
            p._run_ci_rca_cross_check(ctx)  # should not raise

    def test_sha256_mismatch_always_loud_fails(self, tmp_path):
        """SHA-256 mismatch raises ValueError regardless of CI_RCA_STRICT_MODE."""
        import json as _json

        import scripts.ops_data_portal as p

        sha = "a" * 64
        pending_dir = tmp_path / "logs" / ".ci-rca-evidence-pending"
        pending_dir.mkdir(parents=True, exist_ok=True)
        (pending_dir / f"{sha}.json").write_bytes(
            _json.dumps({"earliest_viable_gate": "pre", "sha256": sha}, sort_keys=True, separators=(",", ":")).encode()
        )
        ctx = self._ctx_v2()
        ctx["evidence_bundle_ref"] = {"sha256": sha, "s3_uri": "", "upload_status": "ok"}
        with patch.object(_ci_rca_schema_mod, "ROOT", tmp_path):
            with pytest.raises(ValueError, match="SHA-256 mismatch"):
                p._run_ci_rca_cross_check(ctx)

    def test_check1_bundle_undetermined_agent_must_mirror(self, tmp_path):
        """Check-1: bundle.earliest_viable_gate='undetermined' but agent set 'pre' -> warn/raise."""
        import scripts.ops_data_portal as p

        sha, _ = self._make_bundle(tmp_path, earliest_viable_gate="undetermined")
        ctx = self._ctx_v2(earliest_viable_gate="pre")
        ctx["evidence_bundle_ref"] = {"sha256": sha, "s3_uri": "", "upload_status": "ok"}

        flags_file = tmp_path / "feature_flags.yaml"
        flags_file.write_text("CI_RCA_STRICT_MODE: strict\n", encoding="utf-8")
        with (
            patch.object(_ci_rca_schema_mod, "ROOT", tmp_path),
            patch("scripts.ops_data_portal._FEATURE_FLAGS_YAML", flags_file),
        ):
            with pytest.raises(ValueError, match="check-1"):
                p._run_ci_rca_cross_check(ctx)

    def test_check2_bundle_wins_on_evg_mismatch(self, tmp_path):
        """Check-2: agent earliest_viable_gate differs from bundle -> warn/raise."""
        import scripts.ops_data_portal as p

        sha, _ = self._make_bundle(tmp_path, earliest_viable_gate="CI")
        ctx = self._ctx_v2(earliest_viable_gate="pre")
        ctx["evidence_bundle_ref"] = {"sha256": sha, "s3_uri": "", "upload_status": "ok"}

        flags_file = tmp_path / "feature_flags.yaml"
        flags_file.write_text("CI_RCA_STRICT_MODE: strict\n", encoding="utf-8")
        with (
            patch.object(_ci_rca_schema_mod, "ROOT", tmp_path),
            patch("scripts.ops_data_portal._FEATURE_FLAGS_YAML", flags_file),
        ):
            with pytest.raises(ValueError, match="check-2"):
                p._run_ci_rca_cross_check(ctx)

    def test_check3_escape_mode_bundle_wins(self, tmp_path):
        """Check-3: agent escape_mode differs from bundle -> warn/raise."""
        import scripts.ops_data_portal as p

        sha, _ = self._make_bundle(tmp_path, escape_mode="no_premerge_gate_by_design")
        ctx = self._ctx_v2()
        ctx["detection_gap"]["escape_mode"] = "tier_misplaced"
        ctx["evidence_bundle_ref"] = {"sha256": sha, "s3_uri": "", "upload_status": "ok"}

        flags_file = tmp_path / "feature_flags.yaml"
        flags_file.write_text("CI_RCA_STRICT_MODE: strict\n", encoding="utf-8")
        with (
            patch.object(_ci_rca_schema_mod, "ROOT", tmp_path),
            patch("scripts.ops_data_portal._FEATURE_FLAGS_YAML", flags_file),
        ):
            with pytest.raises(ValueError, match="check-3"):
                p._run_ci_rca_cross_check(ctx)

    def test_check3_escape_mode_abstention_mirror_agent_contradicts(self, tmp_path):
        """Check-3 abstention mirror: bundle.escape_mode='undetermined' (probe abstained) but the
        agent set a concrete value -> warn/raise. Today this raises NO issue; must raise one after."""
        import scripts.ops_data_portal as p

        sha, _ = self._make_bundle(tmp_path, escape_mode="undetermined")
        ctx = self._ctx_v2()
        ctx["detection_gap"]["escape_mode"] = "tier_misplaced"
        ctx["evidence_bundle_ref"] = {"sha256": sha, "s3_uri": "", "upload_status": "ok"}

        flags_file = tmp_path / "feature_flags.yaml"
        flags_file.write_text("CI_RCA_STRICT_MODE: strict\n", encoding="utf-8")
        with (
            patch.object(_ci_rca_schema_mod, "ROOT", tmp_path),
            patch("scripts.ops_data_portal._FEATURE_FLAGS_YAML", flags_file),
        ):
            with pytest.raises(ValueError, match="check-3"):
                p._run_ci_rca_cross_check(ctx)

    def test_check3_escape_mode_abstention_mirror_agent_omitted_field_no_issue(self, tmp_path):
        """False-positive guard: bundle.escape_mode='undetermined' with the agent's escape_mode
        OMITTED entirely (legal -- escape_mode is Optional[str]=None) must raise NO issue, before
        or after. A naive copy of check-1's `!= 'undetermined'` shape would fire here."""
        import scripts.ops_data_portal as p

        sha, _ = self._make_bundle(tmp_path, escape_mode="undetermined")
        ctx = self._ctx_v2()  # detection_gap carries no escape_mode key -- agent_escape is None
        ctx["evidence_bundle_ref"] = {"sha256": sha, "s3_uri": "", "upload_status": "ok"}

        flags_file = tmp_path / "feature_flags.yaml"
        flags_file.write_text("CI_RCA_STRICT_MODE: strict\n", encoding="utf-8")
        with (
            patch.object(_ci_rca_schema_mod, "ROOT", tmp_path),
            patch("scripts.ops_data_portal._FEATURE_FLAGS_YAML", flags_file),
        ):
            p._run_ci_rca_cross_check(ctx)  # must not raise

    def test_check4_vacuous_pass_author_discipline_rejection(self, tmp_path):
        """Check-4: vacuous_pass=true + author-discipline attribution -> warn/raise."""
        import scripts.ops_data_portal as p

        sha, _ = self._make_bundle(tmp_path, vacuous_pass=True, escape_mode="undetermined")
        ctx = self._ctx_v2()
        ctx["detection_gap"]["gap_explanation"] = "author did not run the tests before merging"
        ctx["evidence_bundle_ref"] = {"sha256": sha, "s3_uri": "", "upload_status": "ok"}

        flags_file = tmp_path / "feature_flags.yaml"
        flags_file.write_text("CI_RCA_STRICT_MODE: strict\n", encoding="utf-8")
        with (
            patch.object(_ci_rca_schema_mod, "ROOT", tmp_path),
            patch("scripts.ops_data_portal._FEATURE_FLAGS_YAML", flags_file),
        ):
            with pytest.raises(ValueError, match="check-4"):
                p._run_ci_rca_cross_check(ctx)

    def test_matching_values_no_issues(self, tmp_path):
        """When agent mirrors the bundle, no warnings or raises occur."""
        import scripts.ops_data_portal as p

        sha, _ = self._make_bundle(tmp_path, earliest_viable_gate="pre", escape_mode="tier_misplaced", vacuous_pass=False)
        ctx = self._ctx_v2(earliest_viable_gate="pre")
        ctx["detection_gap"]["escape_mode"] = "tier_misplaced"
        ctx["evidence_bundle_ref"] = {"sha256": sha, "s3_uri": "", "upload_status": "ok"}

        with patch.object(_ci_rca_schema_mod, "ROOT", tmp_path):
            p._run_ci_rca_cross_check(ctx)  # must not raise

    def test_emit_dir_reachable_when_absent_from_pending_dir(self, tmp_path, monkeypatch):
        """CIRCA-01: a bundle present ONLY in CI_RCA_BUNDLE_EMIT_DIR (absent from the pending
        dir) is still reachable -- a deliberate earliest_viable_gate mismatch stamps
        cross_check_check_2 in warn mode and raises ValueError in strict mode.
        """
        import hashlib as _hashlib
        import json as _json

        import scripts.ops_data_portal as p

        emit_dir = tmp_path / "emit"
        emit_dir.mkdir(parents=True, exist_ok=True)
        bundle_data = {"earliest_viable_gate": "CI", "escape_mode": "tier_misplaced", "vacuous_pass": False}
        payload = {k: v for k, v in bundle_data.items() if k != "sha256"}
        sha = _hashlib.sha256(
            _json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        ).hexdigest()
        bundle_data["sha256"] = sha
        (emit_dir / f"{sha}.json").write_bytes(
            _json.dumps(bundle_data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        )
        # Confirm the pending dir genuinely has no bundle -- this is the CI layout under test.
        pending_dir = tmp_path / "logs" / ".ci-rca-evidence-pending"
        assert not (pending_dir / f"{sha}.json").exists()

        monkeypatch.setenv("CI_RCA_BUNDLE_EMIT_DIR", str(emit_dir))
        ctx = self._ctx_v2(earliest_viable_gate="pre")  # deliberately disagrees with bundle's "CI"
        ctx["evidence_bundle_ref"] = {"sha256": sha, "s3_uri": "", "upload_status": "ok"}

        with patch.object(_ci_rca_schema_mod, "ROOT", tmp_path):
            p._run_ci_rca_cross_check(ctx)  # warn mode: must not raise
        assert ctx["warn_mode_reject"]["reasons"] == ["cross_check_check_2"]

        ctx2 = self._ctx_v2(earliest_viable_gate="pre")
        ctx2["evidence_bundle_ref"] = {"sha256": sha, "s3_uri": "", "upload_status": "ok"}
        flags_file = tmp_path / "feature_flags.yaml"
        flags_file.write_text("CI_RCA_STRICT_MODE: strict\n", encoding="utf-8")
        with (
            patch.object(_ci_rca_schema_mod, "ROOT", tmp_path),
            patch("scripts.ops_data_portal._FEATURE_FLAGS_YAML", flags_file),
        ):
            with pytest.raises(ValueError, match="check-2"):
                p._run_ci_rca_cross_check(ctx2)


class TestEvidenceBundleRefConditional:
    """CIRCA-06: _EvidenceBundleRef.s3_uri is required iff upload_status=='ok'."""

    def test_upload_failed_permits_empty_s3_uri(self) -> None:
        import scripts.ops_data_portal as p

        ref = p._EvidenceBundleRef(sha256="a" * 64, s3_uri="", upload_status="upload_failed")
        assert ref.s3_uri == ""

    def test_ok_with_empty_s3_uri_fails(self) -> None:
        import scripts.ops_data_portal as p

        with pytest.raises(ValidationError):
            p._EvidenceBundleRef(sha256="a" * 64, s3_uri="", upload_status="ok")

    def test_ok_with_non_s3_uri_fails(self) -> None:
        import scripts.ops_data_portal as p

        with pytest.raises(ValidationError):
            p._EvidenceBundleRef(sha256="a" * 64, s3_uri="https://example.com/x", upload_status="ok")

    def test_ok_with_valid_s3_uri_passes(self) -> None:
        import scripts.ops_data_portal as p

        ref = p._EvidenceBundleRef(sha256="a" * 64, s3_uri="s3://bucket/key.json", upload_status="ok")
        assert ref.upload_status == "ok"


class TestTerminusOverrideTyped:
    """CIRCA-08: why_chain_terminus_override is a typed sub-model (reason: str, 80-400 chars)."""

    def _ctx_v2(self, **overrides):
        dg = {
            "earliest_viable_gate": "pre",
            "actual_gate_that_caught_it": "CI",
            "gap_explanation": (
                "test gap explanation with enough chars for the field to satisfy the minimum length "
                "floor, citing scripts/validate.py:1 for reference."
            ),
        }
        ctx = {
            "schema_version": 2,
            "proximate_cause": "x" * 100,
            "why_chain": ["a " * 25, "b " * 25, "no systemic keyword or citation here at all really"],
            "detection_gap": dg,
            "recurrence_class": "novel",
            "corrective_action": "x" * 100,
            "preventive_action": "x" * 100,
        }
        ctx.update(overrides)
        return ctx

    def test_bad_dict_shape_fails(self) -> None:
        import scripts.ops_data_portal as p

        ctx = self._ctx_v2(why_chain_terminus_override={"x": 1})
        with pytest.raises(ValidationError):
            p.CiRcaContext.model_validate(ctx)

    def test_reason_too_short_fails(self) -> None:
        import scripts.ops_data_portal as p

        ctx = self._ctx_v2(why_chain_terminus_override={"reason": "x" * 79})
        with pytest.raises(ValidationError):
            p.CiRcaContext.model_validate(ctx)

    def test_reason_too_long_fails(self) -> None:
        import scripts.ops_data_portal as p

        ctx = self._ctx_v2(why_chain_terminus_override={"reason": "x" * 401})
        with pytest.raises(ValidationError):
            p.CiRcaContext.model_validate(ctx)

    def test_conformant_reason_validates_and_bypasses_terminus(self) -> None:
        """A conformant terminus override validates even though the why_chain final entry
        deliberately lacks a systemic keyword and a file:line citation (the terminus floor)."""
        import scripts.ops_data_portal as p

        ctx = self._ctx_v2(why_chain_terminus_override={"reason": "x" * 80})
        parsed = p.CiRcaContext.model_validate(ctx)
        assert parsed.why_chain_terminus_override is not None
        assert parsed.why_chain_terminus_override.reason == "x" * 80

    def test_no_override_still_enforces_terminus_floor(self) -> None:
        """Sanity: without an override, the deliberately-noncompliant final entry still fails."""
        import scripts.ops_data_portal as p

        ctx = self._ctx_v2()
        with pytest.raises(ValidationError):
            p.CiRcaContext.model_validate(ctx)


class TestWarnModePerRuleStamping:
    """CIRCA-04 (portal half): per-rule schema deficiency stamping."""

    def test_why_chain_too_long_stamps_specific_tag(self, tmp_path: Path) -> None:
        import scripts.ops_data_portal as p

        deficient_ctx = {
            **_VALID_CONTEXT_V2,
            "why_chain": [
                "a " * 25,
                "b " * 25,
                "c " * 130 + "systemic scripts/validate.py:1",  # > 250 chars, schema_version=1
            ],
            "rca_confidence": "undetermined",  # isolate from bundle-absent
        }
        recs_file = tmp_path / "recs.jsonl"
        with (
            patch("scripts.ops_data_portal._ducklake_write", return_value={"key": "rec-9301"}) as mock_write,
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
        ):
            rec_id = p.file_rec(dict(_CI_RCA_FIELDS), context_v2_json=deficient_ctx)
        assert rec_id == "rec-9301"
        entry = mock_write.call_args.args[1]
        stored = json.loads(entry["context_v2_json"])
        assert stored["warn_mode_reject"]["reasons"] == ["schema_why_chain_too_long"]

    def test_unmapped_deficiency_falls_back_to_bare_tag(self) -> None:
        import scripts.ops_data_portal as p

        assert p._classify_schema_deficiency("recurrence_class must be one of [...]") == "schema_deficiency"


class TestWhyChainCeilingVersioned:
    """CIRCA-04 ceiling: why_chain per-entry length ceiling is version-gated (v1=250, v2=400)."""

    def _ctx_v2(self, why_chain_last_entry: str, schema_version: int):
        dg = {
            "earliest_viable_gate": "pre",
            "actual_gate_that_caught_it": "CI",
            "gap_explanation": (
                "test gap explanation with enough chars for the field to satisfy the minimum length "
                "floor, citing scripts/validate.py:1 for reference."
            ),
        }
        return {
            "schema_version": schema_version,
            "proximate_cause": "x" * 100,
            "why_chain": ["a " * 25, "b " * 25, why_chain_last_entry],
            "detection_gap": dg,
            "recurrence_class": "novel",
            "corrective_action": "x" * 100,
            "preventive_action": "x" * 100,
        }

    @staticmethod
    def _entry_of_length(total_len: int) -> str:
        """Build a why_chain final entry of EXACTLY total_len chars, preserving the trailing
        systemic keyword + file:line citation the terminus check requires."""
        suffix = "this is a systemic gap at scripts/validate.py:1"
        filler_len = total_len - len(suffix) - 1  # -1 for the joining space
        assert filler_len > 0
        filler = ("c" * filler_len)[:filler_len]
        entry = f"{filler} {suffix}"
        assert len(entry) == total_len, len(entry)
        return entry

    def test_v2_accepts_300_char_entry(self) -> None:
        import scripts.ops_data_portal as p

        entry = self._entry_of_length(300)
        ctx = self._ctx_v2(entry, schema_version=2)
        parsed = p.CiRcaContext.model_validate(ctx)
        assert parsed.schema_version == 2

    def test_v1_rejects_same_300_char_entry(self) -> None:
        import scripts.ops_data_portal as p

        entry = self._entry_of_length(300)
        ctx = self._ctx_v2(entry, schema_version=1)
        with pytest.raises(ValidationError, match="max 250"):
            p.CiRcaContext.model_validate(ctx)

    def test_v1_default_250_ceiling_unaffected_for_historical_rows(self) -> None:
        """A 250-char entry (the pre-existing ceiling) still validates at schema_version=1 --
        no historical row is newly rejected by the version-gated loosening."""
        import scripts.ops_data_portal as p

        entry = self._entry_of_length(250)  # exactly the pre-existing ceiling
        ctx = self._ctx_v2(entry, schema_version=1)
        parsed = p.CiRcaContext.model_validate(ctx)
        assert parsed.schema_version == 1


class TestDetectionGapUnknownGate:
    """CIRCA-09 (enum half): 'unknown' is accepted by _DetectionGap.actual_gate_that_caught_it."""

    def test_unknown_validates(self) -> None:
        import scripts.ops_data_portal as p

        gap = p._DetectionGap(
            earliest_viable_gate="pre",
            actual_gate_that_caught_it="unknown",
            gap_explanation=(
                "not_a_gate workflow -- terraform-apply-sandbox has no CI-gate concept to report, so the "
                "bundle emits null and the agent mirrors 'unknown' at scripts/validate.py:1."
            ),
        )
        assert gap.actual_gate_that_caught_it == "unknown"

    def test_still_rejects_out_of_enum_value(self) -> None:
        import scripts.ops_data_portal as p

        with pytest.raises(ValidationError):
            p._DetectionGap(
                earliest_viable_gate="pre",
                actual_gate_that_caught_it="not_a_real_gate",
                gap_explanation=(
                    "explanation with enough chars and a citation at scripts/validate.py:1 to satisfy "
                    "the 120-character minimum length floor for this field."
                ),
            )

    def test_guidance_documents_unknown_and_mirror_instruction(self) -> None:
        from scripts.executor.rec_write_guidance import get_rec_write_guidance

        guidance = get_rec_write_guidance(source="ci_rca")
        detection_gap_doc = guidance["context_v2_json"]["schema_fields"]["detection_gap"]
        assert "unknown" in detection_gap_doc
        assert "mirror" in detection_gap_doc.lower()
