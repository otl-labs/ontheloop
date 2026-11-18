"""Unit tests for scripts.ci_rca.dedup (100% coverage).

All tests inject finder/bumper callables -- no live DuckLake reader or portal writes.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.ci_rca.dedup import BundleVerdict, DedupResult, _load_fingerprints, decide, main


class TestDecide:
    def test_all_bundles_matched_dedupes(self):
        finder = MagicMock(side_effect=lambda fp: {"fp-a": "rec-1", "fp-b": "rec-2"}[fp])
        bumper = MagicMock()
        result = decide(["fp-a", "fp-b"], finder=finder, bumper=bumper)

        assert result.run_agent is False
        assert result.deduped is True
        assert bumper.call_count == 2
        bumper.assert_any_call("rec-1")
        bumper.assert_any_call("rec-2")

    def test_novel_fingerprint_always_runs_agent(self):
        """Decision 55 guard: a genuinely-novel fingerprint always results in run-agent."""
        finder = MagicMock(return_value=None)
        bumper = MagicMock()
        result = decide(["fp-novel"], finder=finder, bumper=bumper)

        assert result.run_agent is True
        assert result.deduped is False
        bumper.assert_not_called()

    def test_mixed_set_runs_agent_but_still_bumps_matched(self):
        """A matched bundle is skipped+bumped independently of a co-occurring novel bundle --
        the fix for the all-or-nothing bug (a single miss no longer skips bumping the rest)."""
        finder = MagicMock(side_effect=lambda fp: {"fp-matched": "rec-1", "fp-novel": None}[fp])
        bumper = MagicMock()
        result = decide(["fp-matched", "fp-novel"], finder=finder, bumper=bumper)

        assert result.run_agent is True
        bumper.assert_called_once_with("rec-1")
        verdicts = {v.fingerprint: v for v in result.verdicts}
        assert verdicts["fp-matched"].run_agent is False
        assert verdicts["fp-matched"].matched_rec_id == "rec-1"
        assert verdicts["fp-novel"].run_agent is True
        assert verdicts["fp-novel"].matched_rec_id is None

    def test_force_rca_bypasses_all_lookups(self):
        finder = MagicMock()
        bumper = MagicMock()
        result = decide(["fp-a", "fp-b"], force_rca=True, finder=finder, bumper=bumper)

        assert result.run_agent is True
        assert result.force_rca is True
        finder.assert_not_called()
        bumper.assert_not_called()

    def test_empty_fingerprint_list_fails_closed(self):
        """Zero evidence bundles at all -- fail closed (Decision 55), run the agent."""
        finder = MagicMock()
        result = decide([], finder=finder)

        assert result.run_agent is True
        assert result.verdicts == []
        finder.assert_not_called()

    def test_missing_fingerprint_string_treated_as_novel(self):
        finder = MagicMock()
        bumper = MagicMock()
        result = decide([""], finder=finder, bumper=bumper)

        assert result.run_agent is True
        finder.assert_not_called()
        bumper.assert_not_called()
        assert result.verdicts[0].matched_rec_id is None

    def test_default_finder_and_bumper_used_when_not_injected(self):
        """No finder/bumper injected -- falls back to the real ci_rca_runtime helpers."""
        import scripts.ci_rca.dedup as dedup_mod

        with (
            pytest.MonkeyPatch.context() as mp,
        ):
            mock_find = MagicMock(return_value="rec-9")
            mock_bump = MagicMock()
            mp.setattr(dedup_mod, "_default_finder", lambda fp, profile=None: mock_find(fp))
            mp.setattr(dedup_mod, "_default_bumper", lambda rec_id, profile=None: mock_bump(rec_id))
            result = decide(["fp-a"])

        assert result.deduped is True
        mock_find.assert_called_once_with("fp-a")
        mock_bump.assert_called_once_with("rec-9")


class TestStatusAwareChainDedup:
    """ci-rca-identity-lifecycle: closed_head_resolver seam -- a closed head is never bumped;
    'drop' skips the agent; 'regression' (or no closed head at all) runs it same as novel."""

    def test_closed_head_drop_skips_agent_without_bump(self):
        finder = MagicMock(return_value=None)
        bumper = MagicMock()
        closed_head_resolver = MagicMock(return_value="drop")

        result = decide(["fp-a"], finder=finder, bumper=bumper, closed_head_resolver=closed_head_resolver)

        assert result.run_agent is False
        assert result.deduped is True
        bumper.assert_not_called()
        closed_head_resolver.assert_called_once_with("fp-a")
        assert result.verdicts[0].matched_rec_id is None
        assert result.verdicts[0].run_agent is False

    def test_closed_head_regression_runs_agent(self):
        finder = MagicMock(return_value=None)
        bumper = MagicMock()
        closed_head_resolver = MagicMock(return_value="regression")

        result = decide(["fp-a"], finder=finder, bumper=bumper, closed_head_resolver=closed_head_resolver)

        assert result.run_agent is True
        bumper.assert_not_called()

    def test_no_closed_head_at_all_runs_agent(self):
        """closed_head_resolver returning None (no chain match whatsoever) is genuinely novel."""
        finder = MagicMock(return_value=None)
        closed_head_resolver = MagicMock(return_value=None)

        result = decide(["fp-a"], finder=finder, closed_head_resolver=closed_head_resolver)

        assert result.run_agent is True

    def test_open_match_never_consults_closed_head_resolver(self):
        """Mutually exclusive: an open match returns before the closed-head seam is touched."""
        finder = MagicMock(return_value="rec-1")
        bumper = MagicMock()
        closed_head_resolver = MagicMock()

        result = decide(["fp-a"], finder=finder, bumper=bumper, closed_head_resolver=closed_head_resolver)

        assert result.deduped is True
        bumper.assert_called_once_with("rec-1")
        closed_head_resolver.assert_not_called()

    def test_closed_head_resolver_not_injected_preserves_prior_behaviour(self):
        """Omitting closed_head_resolver entirely (the pre-lifecycle call shape) never consults
        it -- a finder miss is treated as plain novel, exactly as before this change."""
        finder = MagicMock(return_value=None)
        bumper = MagicMock()

        result = decide(["fp-a"], finder=finder, bumper=bumper)

        assert result.run_agent is True
        bumper.assert_not_called()

    def test_default_closed_head_resolver_no_head_returns_none(self):
        import scripts.ci_rca.dedup as dedup_mod

        with patch("scripts.ops_portal.ci_rca_lifecycle.closed_head_of_chain", return_value=None) as mock_head:
            result = dedup_mod._default_closed_head_resolver("fp-a")

        assert result is None
        mock_head.assert_called_once_with("fp-a", profile=None)

    def test_default_closed_head_resolver_delegates_to_classify(self):
        import scripts.ci_rca.dedup as dedup_mod
        from scripts.ops_portal.ci_rca_lifecycle import ChainRecord

        head = ChainRecord(rec_id="rec-1", status="closed", fixed_by_sha="abc123", last_touched="2026-01-01T00:00:00Z")
        with (
            patch("scripts.ops_portal.ci_rca_lifecycle.closed_head_of_chain", return_value=head),
            patch("scripts.ops_portal.ci_rca_lifecycle.current_commit_sha", return_value="abc123"),
            patch("scripts.ops_portal.ci_rca_lifecycle.classify_closed_head", return_value="drop") as mock_classify,
        ):
            result = dedup_mod._default_closed_head_resolver("fp-a")

        assert result == "drop"
        mock_classify.assert_called_once_with("abc123", head)


class TestLoadFingerprints:
    def test_reads_fingerprint_from_each_bundle(self, tmp_path: Path):
        (tmp_path / "a.json").write_text(json.dumps({"fingerprint": "fp-a"}))
        (tmp_path / "b.json").write_text(json.dumps({"fingerprint": "fp-b"}))

        fps = _load_fingerprints(tmp_path)

        assert sorted(fps) == ["fp-a", "fp-b"]

    def test_missing_fingerprint_key_yields_empty_string(self, tmp_path: Path):
        (tmp_path / "a.json").write_text(json.dumps({"failure_category": "unknown"}))

        fps = _load_fingerprints(tmp_path)

        assert fps == [""]

    def test_malformed_json_yields_empty_string(self, tmp_path: Path):
        (tmp_path / "a.json").write_text("not-json{")

        fps = _load_fingerprints(tmp_path)

        assert fps == [""]

    def test_no_bundle_files_yields_empty_list(self, tmp_path: Path):
        assert _load_fingerprints(tmp_path) == []


class TestMain:
    def test_deduped_true_when_all_matched(self, tmp_path: Path, capsys):
        import scripts.ci_rca.dedup as dedup_mod

        (tmp_path / "a.json").write_text(json.dumps({"fingerprint": "fp-a"}))

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(dedup_mod, "_default_finder", lambda fp, profile=None: "rec-1")
            mp.setattr(dedup_mod, "_default_bumper", lambda rec_id, profile=None: None)
            rc = main(["--bundles-dir", str(tmp_path)])

        out = capsys.readouterr().out
        assert rc == 0
        assert "deduped=true" in out
        assert "matches open rec-1" in out

    def test_deduped_false_on_novel(self, tmp_path: Path, capsys):
        import scripts.ci_rca.dedup as dedup_mod

        (tmp_path / "a.json").write_text(json.dumps({"fingerprint": "fp-novel"}))

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(dedup_mod, "_default_finder", lambda fp, profile=None: None)
            mp.setattr(dedup_mod, "_default_closed_head_resolver", lambda fp, profile=None: None)
            rc = main(["--bundles-dir", str(tmp_path)])

        out = capsys.readouterr().out
        assert rc == 0
        assert "deduped=false" in out
        assert "has no open match" in out

    def test_force_rca_flag(self, tmp_path: Path, capsys):
        (tmp_path / "a.json").write_text(json.dumps({"fingerprint": "fp-a"}))

        rc = main(["--bundles-dir", str(tmp_path), "--force-rca"])

        out = capsys.readouterr().out
        assert rc == 0
        assert "deduped=false" in out
        assert "force_rca=true" in out

    def test_missing_bundles_dir_treated_as_no_evidence(self, tmp_path: Path, capsys):
        rc = main(["--bundles-dir", str(tmp_path / "nonexistent")])

        out = capsys.readouterr().out
        assert rc == 0
        assert "deduped=false" in out
        assert "No evidence bundles found" in out

    def test_bundle_with_no_fingerprint_prints_marker(self, tmp_path: Path, capsys):
        (tmp_path / "a.json").write_text(json.dumps({"failure_category": "unknown"}))

        rc = main(["--bundles-dir", str(tmp_path)])

        out = capsys.readouterr().out
        assert rc == 0
        assert "deduped=false" in out
        assert "Bundle has no fingerprint" in out


class TestDataclasses:
    def test_dedup_result_deduped_property(self):
        assert DedupResult(run_agent=True).deduped is False
        assert DedupResult(run_agent=False).deduped is True

    def test_bundle_verdict_fields(self):
        v = BundleVerdict(fingerprint="fp-a", matched_rec_id="rec-1", run_agent=False)
        assert v.fingerprint == "fp-a"
        assert v.matched_rec_id == "rec-1"
        assert v.run_agent is False


class TestDefaultsAndSiblings:
    def test_default_finder_and_bumper_delegate_to_the_runtime_helpers(self):
        import scripts.ci_rca.dedup as dedup_mod

        with (
            patch("scripts.ops_portal.ci_rca_lifecycle.newest_open_in_chain", return_value="rec-3") as find,
            patch("scripts.ops_portal.ci_rca_runtime.bump_ci_rca_occurrence") as bump,
        ):
            assert dedup_mod._default_finder("fp", profile="p") == "rec-3"
            dedup_mod._default_bumper("rec-3", profile="p")
        find.assert_called_once_with("fp", profile="p")
        bump.assert_called_once_with("rec-3", profile="p")

    def test_sibling_fingerprints_for_ci_canary_and_other_workflows(self):
        import scripts.ci_rca.dedup as dedup_mod
        from scripts.ci_rca.fingerprint import compute_fingerprint_v2

        ci = compute_fingerprint_v2("ci", "c", "s")
        canary = compute_fingerprint_v2("main_canary", "c", "s")
        base = {"failure_category": "c", "error_signature": "s"}
        assert dedup_mod.sibling_fingerprints({**base, "workflow_name": "CI", "fingerprint": ci}) == [ci, canary]
        assert dedup_mod.sibling_fingerprints({**base, "workflow_name": "Main Canary", "fingerprint": canary}) == [canary, ci]
        assert dedup_mod.sibling_fingerprints({**base, "workflow_name": "notice-digest", "fingerprint": "x"}) == ["x"]
        assert dedup_mod.sibling_fingerprints({"workflow_name": "CI", "fingerprint": "x"}) == ["x"]
        assert dedup_mod.sibling_fingerprints({}) == []

    def test_sibling_file_lists_each_bundle_and_survives_a_write_error(self, tmp_path: Path):
        import scripts.ci_rca.dedup as dedup_mod

        dedup_mod._write_sibling_file(tmp_path, ["fp-a", ["fp-b", "fp-c"], ""])
        assert (tmp_path / "sibling-fingerprints.txt").read_text(encoding="utf-8") == "fp-a\nfp-b fp-c\n"
        dedup_mod._write_sibling_file(tmp_path / "missing", ["fp-a"])

    def test_list_entry_closed_head_drop_unless_a_regression_is_read(self):
        readings = {"fp-a": "drop", "fp-b": "drop"}
        result = decide([["fp-a", "fp-b"]], finder=lambda fp: None, closed_head_resolver=lambda fp: readings[fp])
        assert result.deduped is True
        readings["fp-b"] = "regression"
        result = decide([["fp-a", "fp-b"]], finder=lambda fp: None, closed_head_resolver=lambda fp: readings[fp])
        assert result.run_agent is True

    def test_main_reports_a_closed_head_drop(self, tmp_path: Path, capsys):
        import scripts.ci_rca.dedup as dedup_mod

        (tmp_path / "a.json").write_text(json.dumps({"fingerprint": "fp-a"}))
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(dedup_mod, "_default_finder", lambda fp, profile=None: None)
            mp.setattr(dedup_mod, "_default_closed_head_resolver", lambda fp, profile=None: "drop")
            assert main(["--bundles-dir", str(tmp_path)]) == 0
        assert "closed head" in capsys.readouterr().out
        assert (tmp_path / "sibling-fingerprints.txt").read_text(encoding="utf-8") == "fp-a\n"

    def test_list_entry_open_match_on_any_member_is_bumped_once(self):
        bumper = MagicMock()
        result = decide([["fp-a", "fp-b"]], finder=lambda fp: "rec-7" if fp == "fp-b" else None, bumper=bumper)
        assert result.deduped is True
        bumper.assert_called_once_with("rec-7")
