"""Reachability regression guard for scripts/ops/drain_glue_orphan/_github.py (rec-3381).

Both defects previously found in this module were invisible because its tests asserted argv
shapes against invented payloads while mocking subprocess.run; there is no rehearsal subject left
after the drain. These tests drive the four COMMITTED, REAL mcp__github__ payloads
(tests/fixtures/drain_glue_orphan_payloads/) through the normalizer and pure oracles, with paired
NEGATIVES proving the four gh-shaped assumptions the predecessor module made are provably dead:

  1. `id` is read; `databaseId` (the gh CLI's own --json field name) is not.
  2. `created_at` is read; a payload carrying only `createdAt` correlates ZERO candidates -- the
     silent-forever failure this drain exists to close, not a raised exception.
  3. list_workflow_jobs' real envelope double-nests as {"jobs": {"jobs": [...]}}; a single-nested
     {"jobs": [...]} dict (the gh CLI's own shape) does not resolve the job list.
  4. get_job_logs' real envelope is flat ({job_id, logs_content, message, original_length}) with
     no logs[] array and no job_name key; a truncated log (fewer returned lines than
     original_length) raises rather than silently licensing a destruction/no-destroy verdict.
"""

from __future__ import annotations

import importlib

import pytest

from scripts.ops.drain_glue_orphan import _github
from scripts.ops.drain_glue_orphan._github import (
    _APPLY_SANDBOX_JOB_NAME,
    _PLAN_STEP_NAME,
    _REVIEW_STEP_NAME,
    assert_log_matches_job,
    assert_plan_step_ran,
    converge_guard_review_facts,
    destruction_complete,
    find_in_flight_dispatch,
    find_job,
    is_terminal,
    job_log_lines,
    no_remaining_glue_delete,
    normalize_run,
    normalize_runs,
    plan_shows_zero_destroys,
    resolve_apply_sandbox_job,
    select_dispatch_candidate,
    unwrap_jobs_payload,
)
from scripts.ops.drain_glue_orphan._world import WorldMovedError
from tests.fixtures.drain_glue_orphan import load_payload


class TestNormalizeRunsHazard1And2:
    """Real reconcile.yml workflow_dispatch listing: 7 runs, newest 2026-08-15T11:57:39Z."""

    def test_real_payload_normalizes_id_and_created_at(self) -> None:
        payload = load_payload("reconcile_runs.json")
        runs = normalize_runs(payload)
        assert len(runs) == 7
        newest = max(runs, key=lambda r: r["created_at"])
        assert newest["id"] == 31883378929
        assert newest["created_at"] == "2026-08-15T11:57:39Z"
        assert newest["status"] == "completed"
        assert newest["conclusion"] == "success"

    def test_real_payload_second_newest_is_the_failed_run(self) -> None:
        """Run 31883298153 -- named in this module's own planning context as the resolve-target
        failure whose jobs this test suite also captures."""
        payload = load_payload("reconcile_runs.json")
        runs = normalize_runs(payload)
        target = next(r for r in runs if r["id"] == 31883298153)
        assert target["conclusion"] == "failure"
        assert target["created_at"] == "2026-08-15T11:55:41Z"

    def test_gh_shaped_databaseId_is_not_read(self) -> None:
        """HAZARD 1 negative: a gh-CLI-shaped row (databaseId, no id) must not be silently
        accepted -- normalize_runs raises rather than reading databaseId as if it were id."""
        gh_shaped = {"workflow_runs": [{"databaseId": 999, "status": "completed", "createdAt": "2026-01-01T00:00:00Z"}]}
        with pytest.raises(KeyError):
            normalize_runs(gh_shaped)

    def test_gh_shaped_createdAt_correlates_zero_candidates_forever(self) -> None:
        """HAZARD 2 negative: the SILENT failure mode -- a row shaped like the gh CLI's own
        `createdAt` (not `created_at`) normalizes to created_at="" (the .get(..., "") default),
        which never sorts >= any real dispatch timestamp. This is not a crash; it is the exact
        "correlates zero forever" defect rec-3381 exists to close, reproduced here to prove the
        field-name fix (not a raise) is what actually closes it."""
        gh_shaped_row = {"id": 555, "status": "completed", "conclusion": "success", "createdAt": "2099-01-01T00:00:00Z"}
        normalized = normalize_runs({"workflow_runs": [gh_shaped_row]})
        assert normalized[0]["created_at"] == ""
        candidate = select_dispatch_candidate(normalized, "2026-01-01T00:00:00Z")
        assert candidate is None, "gh-shaped createdAt must not be read as created_at -- zero candidates, not a match"

    def test_bare_list_payload_also_normalizes(self) -> None:
        already_unwrapped = load_payload("reconcile_runs.json")["workflow_runs"]
        assert len(normalize_runs(already_unwrapped)) == 7


class TestNormalizeRun:
    def test_actions_get_shaped_row_normalizes_same_as_a_list_row(self) -> None:
        row = load_payload("reconcile_runs.json")["workflow_runs"][0]
        assert normalize_run(row) == normalize_runs({"workflow_runs": [row]})[0]


class TestFindInFlightDispatchAndIsTerminal:
    def test_real_payload_has_no_in_flight_run_today(self) -> None:
        """Ground truth at planning time: all 7 captured runs are terminal (completed)."""
        runs = normalize_runs(load_payload("reconcile_runs.json"))
        assert all(is_terminal(r) for r in runs)
        assert find_in_flight_dispatch(runs) is None

    def test_non_terminal_status_is_found_in_flight(self) -> None:
        runs = [{"id": 1, "status": "in_progress", "conclusion": None, "created_at": "2026-01-01T00:00:00Z"}]
        found = find_in_flight_dispatch(runs)
        assert found is not None and found["id"] == 1
        assert is_terminal(runs[0]) is False

    def test_waiting_on_tf_gated_apply_is_non_terminal(self) -> None:
        assert is_terminal({"status": "waiting"}) is False


class TestSelectDispatchCandidate:
    def test_empty_timestamp_raises(self) -> None:
        with pytest.raises(WorldMovedError, match="unbound window"):
            select_dispatch_candidate([{"id": 1, "created_at": "2026-01-01T00:00:00Z"}], "")

    def test_multiple_candidates_fail_closed(self) -> None:
        runs = [
            {"id": 1, "created_at": "2026-09-01T00:00:01Z"},
            {"id": 2, "created_at": "2026-09-01T00:00:02Z"},
        ]
        with pytest.raises(WorldMovedError, match="more than one"):
            select_dispatch_candidate(runs, "2026-09-01T00:00:00Z")

    def test_single_candidate_selected(self) -> None:
        runs = [{"id": 1, "created_at": "2020-01-01T00:00:00Z"}, {"id": 2, "created_at": "2026-09-01T00:00:01Z"}]
        candidate = select_dispatch_candidate(runs, "2026-09-01T00:00:00Z")
        assert candidate is not None and candidate["id"] == 2

    def test_zero_candidates_returns_none(self) -> None:
        assert select_dispatch_candidate([{"id": 1, "created_at": "2020-01-01T00:00:00Z"}], "2026-09-01T00:00:00Z") is None


class TestUnwrapJobsPayloadHazard3:
    def test_real_reconcile_jobs_payload_double_nested(self) -> None:
        payload = load_payload("reconcile_run_jobs.json")
        jobs = unwrap_jobs_payload(payload)
        assert len(jobs) == 4
        assert {j["name"] for j in jobs} == {
            "resolve-target",
            "gated-apply-reconcile",
            "apply-reconcile",
            "file-reconcile-starved-rec",
        }

    def test_real_apply_sandbox_jobs_payload_double_nested(self) -> None:
        payload = load_payload("apply_sandbox_run_jobs.json")
        jobs = unwrap_jobs_payload(payload)
        assert len(jobs) == 4
        assert find_job(jobs, _APPLY_SANDBOX_JOB_NAME) is not None

    def test_naive_single_nested_read_would_return_the_wrong_type_on_the_real_payload(self) -> None:
        """HAZARD 3 negative, in the same documented-naive-check shape as
        TestWorkflowInvariants::test_invariant_c_naive_substring_check_would_invert (the OLD test
        file's established pattern): a normalizer written against the gh CLI's own single-nested
        `--json jobs` shape reads `payload.get("jobs", [])` directly. Against the REAL,
        double-nested mcp__github__ payload that returns the INNER ENVELOPE DICT
        ({"jobs": [...], "total_count": N}), not a job list -- the naive read is wrong-typed, not
        merely empty. unwrap_jobs_payload (what this module actually calls) correctly resolves
        the real payload's job list instead, proven by the two tests above."""
        real_payload = load_payload("apply_sandbox_run_jobs.json")
        naive_read = real_payload.get("jobs", [])
        assert isinstance(naive_read, dict), "the naive single-nested read returns the envelope dict, not a job list"
        assert not isinstance(naive_read, list)
        assert unwrap_jobs_payload(real_payload) == naive_read["jobs"]

    def test_bare_list_payload_also_unwraps(self) -> None:
        already_unwrapped = load_payload("apply_sandbox_run_jobs.json")["jobs"]["jobs"]
        assert unwrap_jobs_payload(already_unwrapped) == already_unwrapped

    def test_payload_with_no_jobs_key_at_all_returns_empty(self) -> None:
        assert unwrap_jobs_payload({}) == []


class TestJobLogTruncationHazard4:
    def test_real_captured_envelope_is_truncated_and_raises(self) -> None:
        """The committed job_logs_envelope.json is a deliberately BOUNDED excerpt (80 of 898
        real lines) -- the exact shape the truncation guard exists to catch, not a hand-written
        stand-in."""
        envelope = load_payload("job_logs_envelope.json")
        with pytest.raises(WorldMovedError, match="truncated"):
            job_log_lines(envelope)
        with pytest.raises(WorldMovedError, match="truncated"):
            destruction_complete(envelope)
        with pytest.raises(WorldMovedError, match="truncated"):
            no_remaining_glue_delete(envelope)

    def test_real_envelope_shape_has_no_logs_array_and_no_job_name_key(self) -> None:
        """Documents the measured real shape against this module's own planning-time prediction
        (logs[].logs_content, job_name) -- neither survived contact with the live payload."""
        envelope = load_payload("job_logs_envelope.json")
        assert "logs" not in envelope
        assert "job_name" not in envelope
        assert set(envelope) == {"job_id", "logs_content", "message", "original_length"}

    def test_untruncated_envelope_with_destruction_line_reports_drained(self) -> None:
        envelope = {
            "job_id": 1,
            "logs_content": "2026-09-01T00:00:00Z aws_glue_catalog_database.ops: Destruction complete",
            "message": "ok",
            "original_length": 1,
        }
        assert destruction_complete(envelope) is True

    def test_untruncated_envelope_without_destruction_line_reports_not_drained(self) -> None:
        envelope = {
            "job_id": 1,
            "logs_content": "2026-09-01T00:00:00Z some other output",
            "message": "ok",
            "original_length": 1,
        }
        assert destruction_complete(envelope) is False

    def test_no_remaining_glue_delete_true_when_orphan_absent_from_plan(self) -> None:
        envelope = {
            "job_id": 1,
            "logs_content": "Plan: 0 to add, 0 to change, 0 to destroy.",
            "message": "ok",
            "original_length": 1,
        }
        assert no_remaining_glue_delete(envelope) is True

    def test_no_remaining_glue_delete_false_when_orphan_still_in_plan(self) -> None:
        envelope = {
            "job_id": 1,
            "logs_content": "  # aws_glue_catalog_database.ops will be destroyed",
            "message": "ok",
            "original_length": 1,
        }
        assert no_remaining_glue_delete(envelope) is False

    def test_original_length_none_is_not_treated_as_truncated(self) -> None:
        envelope = {"job_id": 1, "logs_content": "one line", "message": "ok"}
        assert job_log_lines(envelope) == ["one line"]


class TestConvergeGuardReviewFacts:
    def test_real_apply_sandbox_jobs_payload_resolves_guard_routed_and_review_skipped(self) -> None:
        """Ground truth in the captured run 33323201848: the review step's own conclusion is
        "skipped" (the guard routed this episode to gated-apply), never inferred from a log."""
        payload = load_payload("apply_sandbox_run_jobs.json")
        facts = converge_guard_review_facts(payload)
        assert facts == {"guard_routed": True, "review_approving": False}

    def test_review_step_success_reads_guard_passed_and_review_approving(self) -> None:
        jobs = [{"name": _APPLY_SANDBOX_JOB_NAME, "steps": [{"name": _REVIEW_STEP_NAME, "conclusion": "success"}]}]
        facts = converge_guard_review_facts({"jobs": {"jobs": jobs}})
        assert facts == {"guard_routed": False, "review_approving": True}

    def test_missing_apply_sandbox_job_fails_closed(self) -> None:
        with pytest.raises(WorldMovedError, match="no job named"):
            converge_guard_review_facts({"jobs": {"jobs": [{"name": "some-other-job", "steps": []}]}})

    def test_review_step_absent_from_steps_list_fails_closed(self) -> None:
        jobs = [{"name": _APPLY_SANDBOX_JOB_NAME, "steps": [{"name": "Some Renamed Step", "conclusion": "success"}]}]
        with pytest.raises(WorldMovedError, match="carries no step named"):
            converge_guard_review_facts({"jobs": {"jobs": jobs}})

    def test_job_with_no_steps_key_at_all_fails_closed_not_inferred(self) -> None:
        """HAZARD 4's own case: a skipped job can carry NO steps[] key at all (not merely an
        empty list) -- must still raise, never infer guard_routed from the absence."""
        jobs = [{"name": _APPLY_SANDBOX_JOB_NAME}]
        with pytest.raises(WorldMovedError, match="carries no step named"):
            converge_guard_review_facts({"jobs": {"jobs": jobs}})


def _log(*lines: str, job_id: int = 99288847073) -> dict[str, object]:
    """A get_job_logs envelope in the real flat shape, sized so the truncation guard passes."""
    return {"job_id": job_id, "logs_content": "\n".join(lines), "original_length": len(lines)}


class TestPlanShowsZeroDestroysIsWidenedAndPresenceRequired:
    """CONVERGE fact 1. VP19 specifies ZERO destroys of ANY address, widened from the orphan-only
    scan: a destroy of any other remaining retired address would route the guard and make this
    phase unreachable, so the oracle tests that premise rather than assuming it."""

    def test_zero_destroy_summary_passes(self) -> None:
        assert plan_shows_zero_destroys(_log("Plan: 0 to add, 0 to change, 0 to destroy.")) is True

    def test_no_changes_verdict_passes(self) -> None:
        assert plan_shows_zero_destroys(_log("No changes. Your infrastructure matches the configuration.")) is True

    def test_destroy_of_a_NON_glue_address_fails_the_fact(self) -> None:
        """The widening this fix exists for: the orphan-only scan passed this log, because the
        address it looked for is absent while a DIFFERENT retired address is being destroyed."""
        envelope = _log(
            "  # aws_athena_workgroup.production will be destroyed",
            "Plan: 0 to add, 0 to change, 1 to destroy.",
        )
        assert plan_shows_zero_destroys(envelope) is False
        assert no_remaining_glue_delete(envelope) is True

    def test_nonzero_destroy_count_alone_fails_the_fact(self) -> None:
        assert plan_shows_zero_destroys(_log("Plan: 0 to add, 0 to change, 2 to destroy.")) is False

    def test_log_with_no_plan_verdict_raises_rather_than_passing(self) -> None:
        """The inherited fail-open: the pre-split module defaulted plan_log to "" whenever the
        plan step was missing, so `address not in ""` scored as no-destroys. An empty or
        wrong-job log must RAISE here, never license converge."""
        with pytest.raises(WorldMovedError, match="no terraform plan verdict"):
            plan_shows_zero_destroys(_log("Post job cleanup.", "Cleaning up orphan processes"))

    def test_unparseable_summary_raises_rather_than_counting_zero(self) -> None:
        with pytest.raises(WorldMovedError, match="unparseable terraform plan summary"):
            plan_shows_zero_destroys(_log("Plan: some to add, none to change, many to destroy."))

    def test_truncated_log_still_raises_before_any_destroy_read(self) -> None:
        envelope = {"job_id": 1, "logs_content": "Plan: 0 to add, 0 to change, 0 to destroy.", "original_length": 900}
        with pytest.raises(WorldMovedError, match="job log truncated"):
            plan_shows_zero_destroys(envelope)


class TestJobLogEnvelopeIsCorrelatedToItsJob:
    """Nothing else in the chain ties the agent-supplied log to the correlated run, so a verdict
    about one job could otherwise be read out of another job's log entirely."""

    def test_matching_job_id_returns_it(self) -> None:
        job = {"name": _APPLY_SANDBOX_JOB_NAME, "id": 4242}
        assert assert_log_matches_job({"job_id": 4242}, job) == 4242

    def test_mismatched_job_id_fails_closed(self) -> None:
        job = {"name": _APPLY_SANDBOX_JOB_NAME, "id": 4242}
        with pytest.raises(WorldMovedError, match="another job's log"):
            assert_log_matches_job({"job_id": 9999}, job)

    def test_envelope_without_a_job_id_fails_closed(self) -> None:
        with pytest.raises(WorldMovedError, match="another job's log"):
            assert_log_matches_job({"logs_content": ""}, {"name": _APPLY_SANDBOX_JOB_NAME, "id": 4242})

    def test_job_without_an_id_fails_closed(self) -> None:
        with pytest.raises(WorldMovedError, match="another job's log"):
            assert_log_matches_job({"job_id": 4242}, {"name": _APPLY_SANDBOX_JOB_NAME})


class TestPlanStepMustHaveActuallyRun:
    def test_successful_plan_step_passes(self) -> None:
        job = {"name": _APPLY_SANDBOX_JOB_NAME, "steps": [{"name": _PLAN_STEP_NAME, "conclusion": "success"}]}
        assert assert_plan_step_ran(job) is None

    def test_absent_plan_step_fails_closed(self) -> None:
        """_PLAN_STEP_NAME gated the plan-log fetch in the pre-split module; a missing step left
        plan_log empty, which then read as "no destroys"."""
        with pytest.raises(WorldMovedError, match="carries no step named"):
            assert_plan_step_ran({"name": _APPLY_SANDBOX_JOB_NAME, "steps": [{"name": "Renamed"}]})

    def test_job_with_no_steps_key_fails_closed(self) -> None:
        with pytest.raises(WorldMovedError, match="carries no step named"):
            assert_plan_step_ran({"name": _APPLY_SANDBOX_JOB_NAME})

    def test_SKIPPED_plan_step_fails_closed_even_though_it_is_present(self) -> None:
        """The step is gated `if: github.event_name == 'workflow_dispatch'`, so a push-triggered
        run carries it present-but-skipped having produced NO plan output. A name-only check
        passed that job and handed fact 1 a log with nothing to find."""
        job = {"name": _APPLY_SANDBOX_JOB_NAME, "steps": [{"name": _PLAN_STEP_NAME, "conclusion": "skipped"}]}
        with pytest.raises(WorldMovedError, match="not 'success'"):
            assert_plan_step_ran(job)

    def test_failed_plan_step_fails_closed(self) -> None:
        job = {"name": _APPLY_SANDBOX_JOB_NAME, "steps": [{"name": _PLAN_STEP_NAME, "conclusion": "failure"}]}
        with pytest.raises(WorldMovedError, match="not 'success'"):
            assert_plan_step_ran(job)


class TestDegenerateSummaryFailsClosedThroughWorldMovedError:
    def test_summary_with_no_count_prefix_raises_world_moved_not_index_error(self) -> None:
        """A bare ' to destroy' prefix has no token to read; it must reach the CLI's
        WorldMovedError handler rather than escaping as an IndexError traceback."""
        with pytest.raises(WorldMovedError, match="unparseable terraform plan summary"):
            plan_shows_zero_destroys(_log("Plan: to destroy"))


class TestResolveApplySandboxJob:
    def test_resolves_the_real_double_nested_payload(self) -> None:
        job = resolve_apply_sandbox_job(load_payload("apply_sandbox_run_jobs.json"))
        assert job["name"] == _APPLY_SANDBOX_JOB_NAME

    def test_missing_job_fails_closed(self) -> None:
        with pytest.raises(WorldMovedError, match="no job named"):
            resolve_apply_sandbox_job({"jobs": {"jobs": [{"name": "other"}]}})


def test_repo_identity_follows_github_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "example-org/example-repo")
    try:
        reloaded = importlib.reload(_github)
        assert (reloaded._REPO_OWNER, reloaded._REPO_NAME) == ("example-org", "example-repo")
    finally:
        monkeypatch.undo()
        importlib.reload(_github)
