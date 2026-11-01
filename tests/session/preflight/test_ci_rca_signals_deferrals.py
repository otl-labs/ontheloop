"""ci-rca deferral instrumentation (PLAN-ci-rca-deferral-instrumentation, PDB-06): the scan rule,
the annotate short-circuit and default `plans_dir` resolution, the HARD BLOCK render, and the
single-home anti-drift contract between .claude/commands/orient.md and .claude/skills/orient/SKILL.md.
"""

from __future__ import annotations

import json
import subprocess
import sys
from unittest.mock import patch

import pytest
import yaml

from scripts.preflight import _common, ci_rca_signals

try:
    import boto3  # noqa: F401

    _HAS_BOTO3 = True
except ImportError:
    _HAS_BOTO3 = False


def _write_plan(tmp_path, slug: str, context_entries: list[str]) -> None:
    (tmp_path / f"PLAN-{slug}.yaml").write_text(
        yaml.safe_dump({"slug": slug, "context": context_entries}, sort_keys=False),
        encoding="utf-8",
    )


class TestScanPriorDeferrals:
    """_scan_prior_deferrals: context-entry-only counting, case-insensitive "defer" matching,
    per-plan de-duplication, owner-phrase extraction, sorted-walk owner determinism, and
    never-raise degradation on an unreadable or malformed plan."""

    def test_counts_only_a_context_entry_naming_both_the_rec_and_defer(self, tmp_path) -> None:
        _write_plan(
            tmp_path,
            "same-entry",
            ["rec-3292 is being deferred by the operator this session.", "unrelated entry"],
        )
        _write_plan(
            tmp_path,
            "different-entries",
            ["rec-3292 is mentioned here with no keyword.", "This plan defers a different concern."],
        )
        result = ci_rca_signals._scan_prior_deferrals({"rec-3292"}, tmp_path)
        assert result["rec-3292"]["count"] == 1
        assert result["rec-3292"]["plan_slugs"] == ["same-entry"]

    def test_case_insensitive_defer_in_mixed_case_entry_is_counted(self, tmp_path) -> None:
        # Neither "DEFERRAL" nor "Deferred" contains a lowercase "defer" substring literally --
        # only .lower()-ing the text surfaces it. Matches the live corpus's own phrasing.
        _write_plan(
            tmp_path,
            "mixed-case",
            ["ci-rca DEFERRAL: rec-9001 is being handled. Deferred by operator-direction."],
        )
        result = ci_rca_signals._scan_prior_deferrals({"rec-9001"}, tmp_path)
        assert result["rec-9001"]["count"] == 1
        assert result["rec-9001"]["owner_named"] == "operator-direction"

    def test_per_plan_deduplication_counts_once_even_with_two_qualifying_entries(self, tmp_path) -> None:
        _write_plan(
            tmp_path,
            "dup",
            [
                "rec-3292 deferred here, owner another agent.",
                "rec-3292 deferred again in a second entry, owner human deferred.",
            ],
        )
        result = ci_rca_signals._scan_prior_deferrals({"rec-3292"}, tmp_path)
        assert result["rec-3292"]["count"] == 1
        assert result["rec-3292"]["plan_slugs"] == ["dup"]
        assert result["rec-3292"]["owner_named"] == "another agent"

    def test_owner_regex_miss_leaves_owner_named_null(self, tmp_path) -> None:
        _write_plan(tmp_path, "no-owner", ["rec-3292 deferred with no owner phrase at all."])
        result = ci_rca_signals._scan_prior_deferrals({"rec-3292"}, tmp_path)
        assert result["rec-3292"]["count"] == 1
        assert result["rec-3292"]["owner_named"] is None

    def test_owner_named_comes_from_the_earliest_sorting_plan(self, tmp_path) -> None:
        # Later-sorting filename WRITTEN FIRST -- the result must depend on sorted() filename
        # order, not creation order (round-4 critique finding F1).
        _write_plan(tmp_path, "zzz-later-owner", ["rec-8001 deferred -- human deferred."])
        _write_plan(tmp_path, "aaa-earlier-owner", ["rec-8001 deferred -- another agent."])
        result = ci_rca_signals._scan_prior_deferrals({"rec-8001"}, tmp_path)
        assert result["rec-8001"]["owner_named"] == "another agent"
        assert result["rec-8001"]["plan_slugs"] == ["aaa-earlier-owner", "zzz-later-owner"]

    def test_a_matching_plan_naming_no_owner_is_passed_over(self, tmp_path) -> None:
        _write_plan(tmp_path, "aaa-no-owner", ["rec-8002 deferred with no owner phrase."])
        _write_plan(tmp_path, "bbb-has-owner", ["rec-8002 deferred -- operator-directed."])
        result = ci_rca_signals._scan_prior_deferrals({"rec-8002"}, tmp_path)
        assert result["rec-8002"]["count"] == 2
        assert result["rec-8002"]["owner_named"] == "operator-directed"

    def test_owner_phrase_outside_the_proximity_window_is_not_attributed(self, tmp_path) -> None:
        # Round-1 code-review High finding: the owner leg must search a bounded window around the
        # rec id's OWN mention, never the whole qualifying entry. Reproduces the fabrication shape
        # (this plan's own census-methodology entry) at 1/4 scale of _OWNER_PROXIMITY_CHARS: the
        # owner phrase sits well past the window on both sides of the rec id, in the SAME entry
        # that also satisfies the rec-id-and-"defer" test. A whole-entry search (the pre-fix
        # behaviour) would still find it and return "another agent"; the windowed search must not.
        assert ci_rca_signals._OWNER_PROXIMITY_CHARS == 100, "window recalibrated -- update the padding below"
        padding = "x" * 200
        entry = f"rec-8005 deferred here with no nearby owner. {padding} another agent is mentioned far away."
        _write_plan(tmp_path, "far-owner", [entry])
        result = ci_rca_signals._scan_prior_deferrals({"rec-8005"}, tmp_path)
        assert result["rec-8005"]["count"] == 1
        assert result["rec-8005"]["plan_slugs"] == ["far-owner"]
        assert result["rec-8005"]["owner_named"] is None

    def test_owner_named_near_returns_none_outside_its_own_window(self) -> None:
        # Direct unit test on the helper itself, independent of the scan's file-walk plumbing.
        padding = "y" * 200
        entry_lower = f"rec-8006 deferred. {padding} operator-directed decision, unrelated to rec-8006."
        assert ci_rca_signals._owner_named_near(entry_lower, "rec-8006") is None

    def test_unreadable_plan_degrades_rather_than_raises(self, tmp_path) -> None:
        (tmp_path / "PLAN-isdir.yaml").mkdir()
        result = ci_rca_signals._scan_prior_deferrals({"rec-1"}, tmp_path)
        assert result == {"rec-1": {"count": 0, "plan_slugs": [], "owner_named": None}}

    def test_malformed_yaml_plan_degrades_rather_than_raises(self, tmp_path) -> None:
        (tmp_path / "PLAN-malformed.yaml").write_text("rec-3292 defer: [unterminated", encoding="utf-8")
        result = ci_rca_signals._scan_prior_deferrals({"rec-3292"}, tmp_path)
        assert result == {"rec-3292": {"count": 0, "plan_slugs": [], "owner_named": None}}

    def test_returns_zeroed_payload_when_plans_dir_is_not_a_directory(self, tmp_path) -> None:
        result = ci_rca_signals._scan_prior_deferrals({"rec-1"}, tmp_path / "does-not-exist")
        assert result == {"rec-1": {"count": 0, "plan_slugs": [], "owner_named": None}}


class TestAnnotatePriorDeferrals:
    """annotate_prior_deferrals: the empty-unresolved short-circuit (zero plan-file reads),
    in-place mutation, likely_resolved isolation, and the production `plans_dir=None` default."""

    def test_empty_unresolved_performs_no_plans_walk(self, monkeypatch) -> None:
        def _raise(*args: object, **kwargs: object) -> None:
            raise RuntimeError("_scan_prior_deferrals must not be called when unresolved is empty")

        monkeypatch.setattr(ci_rca_signals, "_scan_prior_deferrals", _raise)
        correlation = {"unresolved": [], "likely_resolved": [{"id": "rec-1"}]}
        result = ci_rca_signals.annotate_prior_deferrals(correlation)
        assert result is correlation
        assert correlation["likely_resolved"][0] == {"id": "rec-1"}

    def test_annotation_is_in_place_and_returns_the_same_object(self, tmp_path) -> None:
        _write_plan(tmp_path, "x", ["rec-3292 deferred by operator-directed action."])
        rec = {"id": "rec-3292"}
        correlation = {"unresolved": [rec], "likely_resolved": []}
        result = ci_rca_signals.annotate_prior_deferrals(correlation, plans_dir=tmp_path)
        assert result is correlation
        assert correlation["unresolved"][0] is rec
        assert rec["prior_deferrals"]["count"] == 1

    def test_likely_resolved_entries_are_not_annotated(self, tmp_path) -> None:
        resolved_rec = {"id": "rec-7"}
        correlation = {"unresolved": [{"id": "rec-3292"}], "likely_resolved": [resolved_rec]}
        ci_rca_signals.annotate_prior_deferrals(correlation, plans_dir=tmp_path)
        assert "prior_deferrals" not in resolved_rec

    def test_default_plans_dir_resolves_to_the_repo_corpus(self, monkeypatch, tmp_path) -> None:
        # Chdir FIRST -- a cwd-relative default would return count 0 from here; the correct
        # `_common.ROOT`-anchored default does not (round-3 critique finding F1).
        monkeypatch.chdir(tmp_path)
        known_present = {
            "ambient-prose-contract-relocation",
            "cfg-migration-closeout",
            "drain-glue-orphan-mcp-transport",
            "glue-delete-database-grant",
            "inline-defer-boundary-contract",
            "reader-projection-substrate",
            "roadmap-blocking-edge-semantics",
            "sync-deps-test-hermeticity",
        }
        plans = _common.ROOT / "docs" / "plans"
        present = known_present & {p.stem[len("PLAN-") :] for p in plans.glob("PLAN-*.yaml")}

        correlation = {"unresolved": [{"id": "rec-3292"}], "likely_resolved": []}
        result = ci_rca_signals.annotate_prior_deferrals(correlation)
        assert result["unresolved"][0]["prior_deferrals"]["count"] >= len(present)


class TestPrintPriorDeferralsRender:
    """print_ci_rca_recs HARD BLOCK render: the deferral line shape, the 5-slug overflow, and
    the null-owner label -- and no extra line when there is nothing to report."""

    def _render(self, capsys, rec: dict) -> str:
        correlation = {"unresolved": [rec], "likely_resolved": []}
        ci_rca_signals.print_ci_rca_recs([rec], correlation=correlation)
        return capsys.readouterr().out

    def _rec(self, **prior_deferrals: object) -> dict:
        rec = {"id": "rec-3292", "title": "t", "priority": "Critical", "created_timestamp": "2026-01-01"}
        if prior_deferrals:
            rec["prior_deferrals"] = prior_deferrals
        return rec

    def test_render_shape_with_owner_named(self, capsys) -> None:
        out = self._render(capsys, self._rec(count=3, plan_slugs=["a", "b", "c"], owner_named="operator-directed"))
        assert "deferred 3 times (plans: a, b, c; owner named: operator-directed)" in out

    def test_overflow_past_five_slugs(self, capsys) -> None:
        out = self._render(capsys, self._rec(count=7, plan_slugs=list("abcdefg"), owner_named="another agent"))
        assert "plans: a, b, c, d, e, +2 more" in out

    def test_owner_named_none_when_null(self, capsys) -> None:
        out = self._render(capsys, self._rec(count=1, plan_slugs=["a"], owner_named=None))
        assert "owner named: none" in out

    def test_no_extra_line_when_prior_deferrals_absent(self, capsys) -> None:
        out = self._render(capsys, self._rec())
        assert "deferred" not in out

    def test_no_extra_line_when_count_is_zero(self, capsys) -> None:
        out = self._render(capsys, self._rec(count=0, plan_slugs=[], owner_named=None))
        assert "deferred" not in out


class TestOrientDeferralSurfacingContract:
    """Anti-drift, within AND across surfaces: the CI-RCA triage rendering has exactly one home
    (.claude/commands/orient.md Step 3); .claude/skills/orient/SKILL.md points at it, restates no
    row, and MIRRORS its probe carve-out (round-2 critique finding F1) rather than contradicting
    it in three places."""

    def _read(self, relpath: str) -> str:
        return (_common.ROOT / relpath).read_text(encoding="utf-8")

    def test_command_carries_the_single_home_rendering(self) -> None:
        text = self._read(".claude/commands/orient.md")
        assert "prior_deferrals" in text
        assert "**HARD BLOCK**" in text
        assert "**SOFT PROMPT**" in text
        assert "**HARD ALERT**" in text
        assert ">= 3" in text
        assert "run_acceptance_probe=True" in text
        assert "acceptance_timeout=" in text
        assert "-p no:cacheprovider" in text
        assert "--randomly-seed=0" in text
        assert "did not complete" in text
        assert "stamp_fixed_by_sha" in text
        assert "recent_main_commits" in text
        assert "acceptance" in text and "read-only" in text
        assert "except Section 2's CI-RCA triage rendering" in text
        assert "Sole exception: the Step 3 relevance probe" in text
        subsection = text.split("### CI-RCA Triage rendering", 1)[1]
        assert "git log" not in subsection

    def test_skill_points_at_the_command_and_restates_no_row(self) -> None:
        text = self._read(".claude/skills/orient/SKILL.md")
        assert ".claude/commands/orient.md" in text
        assert "**HARD BLOCK**" not in text
        assert "**SOFT PROMPT**" not in text

    def test_skill_mirrors_the_probe_carveout_and_reads_acceptance_from_the_report(self) -> None:
        text = self._read(".claude/skills/orient/SKILL.md")
        assert "relevance probe" in text and ".claude/commands/orient.md" in text
        assert "ci_rca_unresolved_recs[].acceptance" in text
        assert "logs/.recommendations-log.jsonl" not in text
        assert "Read-Only Contract" in text
        # The two remaining absolutes each carry a qualifier pointing at Read-Only Contract.
        assert "writes nothing (one named probe exception -- Read-Only Contract)" in text
        assert "**strictly read-only** (one named exception, in Read-Only Contract)" in text


class TestPytestProbeGuardClauseIsRunnable:
    """Round-2 code-review High finding: the guard clause's prescribed pytest flags must actually
    run against THIS repo's own pyproject.toml addopts (`--randomly-seed=last`), not merely read
    as plausible prose. `-p no:cacheprovider` alone hard-errors here because pytest-randomly
    asserts the cacheprovider plugin is present whenever the seed resolves to the literal string
    "last" -- the guard clause must also override the seed to a concrete value."""

    _TARGET = (
        "tests/session/preflight/test_ci_rca_signals_deferrals.py"
        "::TestPrintPriorDeferralsRender::test_render_shape_with_owner_named"
    )

    def _run(self, extra_args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "pytest", self._TARGET, "-q", *extra_args],
            cwd=_common.ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_no_cacheprovider_alone_hard_errors_against_this_repos_addopts(self) -> None:
        # Reproduces the code-review's observed failure verbatim: exit 3, INTERNALERROR, because
        # pytest-randomly's --randomly-seed=last (pyproject.toml addopts) asserts config.cache.
        result = self._run(["-p", "no:cacheprovider"])
        assert result.returncode != 0
        assert "cacheprovider plugin is required" in (result.stdout + result.stderr)

    def test_guard_clauses_prescribed_flag_pair_runs_clean(self) -> None:
        # The fix: pairing the seed override with the cacheprovider exclusion runs clean, so a
        # /orient-session probe of a real pytest acceptance selector does not silently read as
        # "not satisfied" (subprocess exit != 0) for every pytest-selector rec in the corpus.
        result = self._run(["-p", "no:cacheprovider", "--randomly-seed=0"])
        assert result.returncode == 0, result.stdout + result.stderr
        assert "INTERNALERROR" not in (result.stdout + result.stderr)


@pytest.mark.skipif(not _HAS_BOTO3, reason="main()-integration test requires boto3 (absent in pr-validate's fast tier)")
class TestPreflightReportWiring:
    """End-to-end: `_preflight.main()` writes the prior_deferrals annotation into the report JSON
    the /orient and /plan sessions actually read -- not just the in-process correlation dict
    (round-3 critique finding F1's unexercised-integration-path concern)."""

    def test_unresolved_entries_carry_prior_deferrals(self, tmp_path) -> None:
        from tests.fixtures.session_preflight_module import preflight as _preflight  # noqa: PLC0415

        preflight_report = tmp_path / ".preflight-report.json"
        rec = {"id": "rec-3292", "title": "t", "priority": "Critical", "created_timestamp": "2026-01-01T00:00:00Z"}
        canned = {"count": 4, "plan_slugs": ["a", "b"], "owner_named": "another agent"}

        with (
            patch("session_preflight.PREFLIGHT_REPORT", preflight_report),
            patch("scripts.preflight.ci_rca_signals._fetch_ci_rca_recs", return_value=[rec]),
            patch("scripts.preflight.ci_rca_signals._scan_prior_deferrals", return_value={"rec-3292": canned}),
            patch("scripts.preflight.env_git.check_venv", return_value=True),
            patch("scripts.preflight.env_git.get_git_status", return_value=("claude/test", False, [])),
            patch("scripts.preflight.aws_infra.check_terraform_pending", return_value=False),
            patch("scripts.preflight.aws_infra.check_credentials", return_value="ok"),
            patch(
                "scripts.preflight.session_history.read_session_history",
                return_value={"status": "empty", "window_days": 2, "recent_sessions": [], "last_session": ""},
            ),
            patch("scripts.preflight.context_docs.read_context_files", return_value={}),
            patch("scripts.preflight.ci_rca_signals._check_ci_rca_liveness", return_value=None),
            patch("builtins.print"),
        ):
            _preflight.main()

        assert preflight_report.exists()
        report = json.loads(preflight_report.read_text(encoding="utf-8"))
        assert report["ci_rca_unresolved_recs"], "expected a non-empty unresolved list"
        assert report["ci_rca_unresolved_recs"][0]["prior_deferrals"] == canned
