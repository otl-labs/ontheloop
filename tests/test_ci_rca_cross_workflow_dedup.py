"""rec-4900: one full-tier failure seen by main-validate (ci) and Main Canary files one rec (Decision 142 cl.1, cl.3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional
from unittest.mock import patch

import pytest

from scripts.ci_rca import dedup
from scripts.ci_rca.fingerprint import compute_fingerprint_v2
from scripts.ops_portal import ci_rca_runtime

CATEGORY = "test_failure"
SIGNATURE = "src.common.helper:ValueError:boom"


def bundle(workflow_name: str, signature: str = SIGNATURE) -> dict[str, Any]:
    slug = workflow_name.lower().replace(" ", "_")
    return {
        "workflow_name": workflow_name,
        "failure_category": CATEGORY,
        "error_signature": signature,
        "fingerprint": compute_fingerprint_v2(slug, CATEGORY, signature),
    }


CI_FP = compute_fingerprint_v2("ci", CATEGORY, SIGNATURE)
CANARY_FP = compute_fingerprint_v2("main_canary", CATEGORY, SIGNATURE)


class Recs:
    def __init__(self, open_by_fp: dict[str, str]) -> None:
        self.open_by_fp = open_by_fp
        self.bumped: list[str] = []

    def finder(self, fp: str) -> Optional[str]:
        return self.open_by_fp.get(fp)

    def bumper(self, rec_id: str) -> None:
        self.bumped.append(rec_id)


def test_sibling_fingerprints_own_first_then_other_sibling() -> None:
    assert dedup.sibling_fingerprints(bundle("Main Canary")) == [CANARY_FP, CI_FP]
    assert dedup.sibling_fingerprints(bundle("CI")) == [CI_FP, CANARY_FP]
    assert dedup.sibling_fingerprints(bundle("terraform-apply-sandbox")) == [bundle("terraform-apply-sandbox")["fingerprint"]]
    assert dedup.sibling_fingerprints({"fingerprint": "x"}) == ["x"]
    assert dedup.sibling_fingerprints({}) == []


def test_same_full_tier_failure_on_two_workflows_files_one_rec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recs = Recs({CI_FP: "rec-1000"})
    entries = [dedup.sibling_fingerprints(bundle("Main Canary"))]
    result = dedup.decide(entries, finder=recs.finder, bumper=recs.bumper)
    assert result.deduped and recs.bumped == ["rec-1000"]
    assert result.verdicts[0].matched_rec_id == "rec-1000"

    (tmp_path / "b.json").write_text(json.dumps(bundle("Main Canary")), encoding="utf-8")
    dedup._write_sibling_file(tmp_path, dedup._load_fingerprints(tmp_path))
    monkeypatch.setenv("CI_RCA_BUNDLE_EMIT_DIR", str(tmp_path))
    assert backstop(CANARY_FP, {CI_FP: "rec-1000"}) == "rec-1000"


def test_different_signature_still_runs_agent() -> None:
    recs = Recs({CI_FP: "rec-1000"})
    result = dedup.decide(
        [dedup.sibling_fingerprints(bundle("Main Canary", "other:Err:x"))], finder=recs.finder, bumper=recs.bumper
    )
    assert result.run_agent and not recs.bumped


def test_non_sibling_slug_never_matches_a_ci_rec() -> None:
    recs = Recs({CI_FP: "rec-1000"})
    result = dedup.decide(
        [dedup.sibling_fingerprints(bundle("terraform-apply-sandbox"))], finder=recs.finder, bumper=recs.bumper
    )
    assert result.run_agent and not recs.bumped


def test_sibling_closed_head_goes_through_classify_closed_head() -> None:
    seen: list[str] = []

    def resolver(fp: str) -> Optional[str]:
        seen.append(fp)
        return "drop" if fp == CI_FP else None

    result = dedup.decide(
        [dedup.sibling_fingerprints(bundle("Main Canary"))],
        finder=lambda fp: None,
        bumper=lambda r: None,
        closed_head_resolver=resolver,
    )
    assert result.deduped and seen == [CANARY_FP, CI_FP]


def test_one_sibling_drop_and_another_regression_runs_agent() -> None:
    readings = {CANARY_FP: "regression", CI_FP: "drop"}
    result = dedup.decide(
        [dedup.sibling_fingerprints(bundle("Main Canary"))],
        finder=lambda fp: None,
        bumper=lambda r: None,
        closed_head_resolver=lambda fp: readings[fp],
    )
    assert result.run_agent


def test_string_entries_behave_as_before() -> None:
    recs = Recs({"fp-a": "rec-1"})
    result = dedup.decide(["fp-a", "fp-b"], finder=recs.finder, bumper=recs.bumper)
    assert result.run_agent and recs.bumped == ["rec-1"]


def backstop(fingerprint: str, open_by_fp: dict[str, str]) -> Optional[str]:
    rows = [
        {"id": rec, "status": "open", "context_v2_json": json.dumps({"fingerprint": fp})} for fp, rec in open_by_fp.items()
    ]

    class Reader:
        calls = 0

        def current_state(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            Reader.calls += 1
            return rows

    with patch("src.common.ducklake_reader_client.make_reader", return_value=Reader()):
        found = ci_rca_runtime.find_open_ci_rca_rec_by_fingerprint(fingerprint)
    assert Reader.calls == 1
    return found


def test_backstop_matches_sibling_fingerprint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "sibling-fingerprints.txt").write_text(f"{CANARY_FP} {CI_FP}\n", encoding="utf-8")
    monkeypatch.setenv("CI_RCA_BUNDLE_EMIT_DIR", str(tmp_path))
    assert backstop(CANARY_FP, {CI_FP: "rec-1000"}) == "rec-1000"
    monkeypatch.setenv("CI_RCA_BUNDLE_EMIT_DIR", str(tmp_path / "absent"))
    assert backstop(CANARY_FP, {CI_FP: "rec-1000"}) is None
    monkeypatch.delenv("CI_RCA_BUNDLE_EMIT_DIR")
    assert backstop(CANARY_FP, {CANARY_FP: "rec-2000"}) == "rec-2000"


def test_sibling_file_skips_fingerprintless_bundles(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text(json.dumps({"workflow_name": "CI"}), encoding="utf-8")
    dedup._write_sibling_file(tmp_path, dedup._load_fingerprints(tmp_path))
    assert (tmp_path / "sibling-fingerprints.txt").read_text(encoding="utf-8") == ""


def test_sibling_file_write_failure_is_not_raised(tmp_path: Path) -> None:
    dedup._write_sibling_file(tmp_path / "missing-dir", ["fp"])
