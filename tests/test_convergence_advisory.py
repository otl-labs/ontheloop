"""Tests for scripts/ci/convergence_advisory.py (Decision 172 PR-2).

Asserts the six-way partition VP step 5 requires: NoSuchKey (pass-on-absent), credential/
authorization failure, red record, pending_gated, green, and empty-body/unparseable JSON --
the exact conflation the pre-extraction inline body in terraform-apply-sandbox.yml's
advisory-status job exhibited (a credential failure and a genuinely absent record both folded
into pass-on-absent green; an unparseable record aborted under inherited errexit).
"""

from __future__ import annotations

import json
import os
import subprocess
from unittest.mock import MagicMock, patch

from scripts.ci.convergence_advisory import ERROR, FAILURE, SUCCESS, classify, main


def test_drift_red_does_not_blame_last_apply_commit() -> None:
    """Decision 190: a drift-caused red must not assert 'last sandbox apply RED at {commit}' --
    that commit is the last SUCCESSFUL apply, not the failing one, on a drift-flip red."""
    body = json.dumps(
        {
            "status": "red",
            "commit_sha": "lastgoodsha",
            "drift_run_url": "https://x/drift-run",
            "drift_reason": "out-of-band infra drift detected by scheduled terraform plan",
            "drift_detected_at": "2026-09-13T16:24:36Z",
        }
    )
    state, desc = classify(0, "", body)
    assert state == FAILURE
    assert "last sandbox apply RED" not in desc
    assert "out-of-band drift" in desc


def test_apply_failure_red_keeps_original_last_apply_wording() -> None:
    body = json.dumps({"status": "red", "commit_sha": "badsha"})
    state, desc = classify(0, "", body)
    assert state == FAILURE
    assert "last sandbox apply RED at badsha" in desc


class TestNoSuchKeyPassOnAbsent:
    def test_nosuchkey_is_success_pass_on_absent(self) -> None:
        state, desc = classify(1, "An error occurred (NoSuchKey) when calling ...", "")
        assert state == SUCCESS
        assert "pass-on-absent" in desc

    def test_404_variant_is_also_pass_on_absent(self) -> None:
        state, desc = classify(1, "fatal error: 404 Not Found", "")
        assert state == SUCCESS
        assert "pass-on-absent" in desc


class TestCredentialFailure:
    def test_non_nosuchkey_read_error_is_error_unverified(self) -> None:
        state, desc = classify(1, "An error occurred (AccessDenied) when calling GetObject", "")
        assert state == ERROR
        assert "UNVERIFIED" in desc

    def test_credential_failure_is_never_success(self) -> None:
        """The exact defect this extraction closes: a credential failure must never fold into
        the same pass-on-absent green outcome as a genuinely absent record."""
        state, _ = classify(1, "Unable to locate credentials", "")
        assert state != SUCCESS


class TestEmptyOrUnparseable:
    def test_empty_body_is_error_not_pass_on_absent_green(self) -> None:
        state, desc = classify(0, "", "")
        assert state == ERROR
        assert "UNVERIFIED" in desc

    def test_whitespace_only_body_is_error(self) -> None:
        state, _ = classify(0, "", "   \n  ")
        assert state == ERROR

    def test_unparseable_json_is_error_never_an_abort(self) -> None:
        state, desc = classify(0, "", "{not valid json")
        assert state == ERROR
        assert "UNVERIFIED" in desc

    def test_non_object_json_is_error(self) -> None:
        state, _ = classify(0, "", "[1, 2, 3]")
        assert state == ERROR


class TestRedRecord:
    def test_red_status_is_failure(self) -> None:
        body = json.dumps({"status": "red", "commit_sha": "abc123"})
        state, desc = classify(0, "", body)
        assert state == FAILURE
        assert "abc123" in desc
        assert "non-converged" in desc


class TestPendingGated:
    def test_pending_gated_marker_is_success_with_routed_pending_description(self) -> None:
        body = json.dumps({"status": "green", "pending_gated": {"commit_sha": "def456"}})
        state, desc = classify(0, "", body)
        assert state == SUCCESS
        assert "def456" in desc
        assert "pending-gated" in desc

    def test_pending_gated_is_distinguishable_from_plain_green(self) -> None:
        plain_state, plain_desc = classify(0, "", json.dumps({"status": "green"}))
        pending_body = json.dumps({"status": "green", "pending_gated": {"commit_sha": "x"}})
        pending_state, pending_desc = classify(0, "", pending_body)
        assert plain_state == pending_state == SUCCESS
        assert plain_desc != pending_desc

    def test_description_stays_within_github_status_char_limit_with_a_real_sha(self) -> None:
        """rec-3954: the pending_gated description must fit GitHub's 140-char commit-status
        description limit even with a realistic-length (40-char) git SHA, not just the short
        test-fixture shas used elsewhere in this file."""
        real_sha = "a" * 40
        body = json.dumps({"status": "green", "pending_gated": {"commit_sha": real_sha}})
        state, desc = classify(0, "", body)
        assert state == SUCCESS
        assert real_sha in desc
        assert len(desc) <= 140


class TestPendingCodification:
    """Decision 190: the pending_codification marker surfaces the same way pending_gated does."""

    def test_pending_codification_marker_is_success_with_distinguishing_description(self) -> None:
        body = json.dumps({"status": "green", "pending_codification": {"first_seen": "2026-09-13T00:00:00Z"}})
        state, desc = classify(0, "", body)
        assert state == SUCCESS
        assert "pending-codification" in desc
        assert "2026-09-13T00:00:00Z" in desc

    def test_pending_codification_is_distinguishable_from_plain_green(self) -> None:
        plain_state, plain_desc = classify(0, "", json.dumps({"status": "green"}))
        marker_body = json.dumps({"status": "green", "pending_codification": {"first_seen": "x"}})
        marker_state, marker_desc = classify(0, "", marker_body)
        assert plain_state == marker_state == SUCCESS
        assert plain_desc != marker_desc

    def test_malformed_pending_codification_falls_through_to_plain_green(self) -> None:
        state, desc = classify(0, "", json.dumps({"status": "green", "pending_codification": "not-a-dict"}))
        assert state == SUCCESS
        assert "converged" in desc

    def test_description_stays_within_github_status_char_limit_with_a_real_timestamp(self) -> None:
        """rec-3954: the pending_codification description must fit GitHub's 140-char commit-status
        description limit even with a full ISO-8601 timestamp including a UTC offset, not just the
        short test-fixture timestamps used elsewhere in this file."""
        real_timestamp = "2026-09-20T17:29:07+00:00"
        body = json.dumps({"status": "green", "pending_codification": {"first_seen": real_timestamp}})
        state, desc = classify(0, "", body)
        assert state == SUCCESS
        assert real_timestamp in desc
        assert len(desc) <= 140


class TestGreen:
    def test_converged_green_is_success(self) -> None:
        state, desc = classify(0, "", json.dumps({"status": "green"}))
        assert state == SUCCESS
        assert "converged" in desc

    def test_missing_status_key_defaults_to_green_shaped_success(self) -> None:
        state, _ = classify(0, "", json.dumps({"commit_sha": "abc"}))
        assert state == SUCCESS


class TestMainNeverRaises:
    def test_main_prints_state_and_description_lines(self, capsys) -> None:
        env = {"RC": "0", "S3_STDERR": "", "REC_JSON": json.dumps({"status": "green"})}
        with patch.dict(os.environ, env, clear=False):
            rc = main([])
        assert rc == 0
        out = capsys.readouterr().out.splitlines()
        assert out[0] == SUCCESS
        assert "converged" in out[1]

    def test_main_never_raises_on_malformed_env(self, capsys) -> None:
        env = {"RC": "not-an-int", "S3_STDERR": "", "REC_JSON": "{also not json"}
        with patch.dict(os.environ, env, clear=False):
            rc = main([])
        assert rc == 0
        out = capsys.readouterr().out.splitlines()
        assert out[0] in (SUCCESS, ERROR)

    def test_main_survives_an_unexpected_internal_exception(self, capsys) -> None:
        """A defensive check: even if classify() itself somehow raised, main() must still print
        a state/description pair and exit 0 rather than crash the advisory-status job."""
        with patch("scripts.ci.convergence_advisory.classify", side_effect=RuntimeError("boom")):
            rc = main([])
        assert rc == 0
        out = capsys.readouterr().out.splitlines()
        assert out[0] == ERROR
        assert "UNVERIFIED" in out[1]


_POST_ENV = {
    "REPO": "o/r",
    "HEAD_SHA": "abc",
    "SERVER_URL": "https://github.com",
    "RUN_ID": "7",
    "CONVERGENCE_BUCKET": "bkt",
    "CONVERGENCE_KEY": "key.json",
    "OIDC_FAILED": "false",
}


def _cp(rc: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout="", stderr=stderr)


def _post_fields(run: MagicMock) -> dict[str, str]:
    argv = run.call_args_list[-1].args[0]
    assert argv[:5] == ["gh", "api", "repos/o/r/statuses/abc", "--method", "POST"]
    return {kv.split("=", 1)[0]: kv.split("=", 1)[1] for kv in argv[5:] if "=" in kv}


def test_post_with_oidc_failure_skips_the_s3_read() -> None:
    with patch.dict(os.environ, {**_POST_ENV, "OIDC_FAILED": "true"}), patch("subprocess.run", return_value=_cp()) as run:
        assert main(["--post"]) == 0
    assert run.call_count == 1
    fields = _post_fields(run)
    assert fields["state"] == ERROR
    assert fields["context"] == "terraform-converged"
    assert fields["description"].startswith("UNVERIFIED -- could not read the convergence record")
    assert fields["target_url"] == "https://github.com/o/r/actions/runs/7"


def test_post_read_success_classifies_the_record() -> None:
    def fake(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if argv[0] == "aws":
            assert argv[:6] == ["aws", "s3api", "get-object", "--bucket", "bkt", "--key"]
            with open(argv[-1], "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"status": "green"}))
            return _cp()
        return _cp()

    with patch.dict(os.environ, _POST_ENV), patch("subprocess.run", side_effect=fake) as run:
        assert main(["--post"]) == 0
    fields = _post_fields(run)
    assert fields["state"] == SUCCESS
    assert fields["description"] == "main is converged (last sandbox apply GREEN). Advisory only."


def test_post_nosuchkey_and_credential_failure() -> None:
    for stderr, state in (("An error occurred (NoSuchKey)", SUCCESS), ("AccessDenied", ERROR)):
        with patch.dict(os.environ, _POST_ENV), patch("subprocess.run", side_effect=[_cp(254, stderr), _cp()]) as run:
            assert main(["--post"]) == 0
        assert _post_fields(run)["state"] == state


def test_post_aws_missing_degrades_and_failing_post_exits_nonzero() -> None:
    with patch.dict(os.environ, _POST_ENV), patch("subprocess.run", side_effect=[OSError("no aws"), _cp(1, "boom")]) as run:
        assert main(["--post"]) == 1
    assert _post_fields(run)["state"] == ERROR
    with patch.dict(os.environ, _POST_ENV), patch("subprocess.run", side_effect=[_cp(254, "NoSuchKey"), OSError("no gh")]):
        assert main(["--post"]) == 1


def test_post_classifier_exception_degrades() -> None:
    with (
        patch.dict(os.environ, _POST_ENV),
        patch("scripts.ci.convergence_advisory.classify", side_effect=RuntimeError("x")),
        patch("subprocess.run", side_effect=[_cp(0), _cp()]) as run,
    ):
        assert main(["--post"]) == 0
    assert _post_fields(run)["state"] == ERROR
