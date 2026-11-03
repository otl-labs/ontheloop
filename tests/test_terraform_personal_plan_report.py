"""Names-only PR comment for the trusted terraform/personal plan (Decision 238 cl.4, PLAN-trusted-plan-cutover PR 2)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "ci" / "terraform_personal_plan_report.py"
HEAD = "a" * 40
RUN = "https://github.com/otl-labs/ontheloop/actions/runs/123"
SECRETS = ("hunter2-key-value", "tag-from-state-value", "nonsensitive-output-value", "before-secret", "after-secret")


def _plan() -> dict:
    return {
        "resource_changes": [
            {
                "address": 'aws_iam_role.r["hunter2-key-value"]',
                "type": "aws_iam_role",
                "mode": "managed",
                "change": {
                    "actions": ["update"],
                    "before": {"name": "before-secret", "tags": {"Owner": "tag-from-state-value"}},
                    "after": {"name": "after-secret", "tags": {"Owner": "tag-from-state-value"}, "path": "/"},
                    "after_unknown": {},
                },
            },
            {
                "address": 'module.m["hunter2-key-value"].aws_s3_bucket.b',
                "type": "aws_s3_bucket",
                "mode": "managed",
                "change": {
                    "actions": ["create"],
                    "before": None,
                    "after": {"bucket": "after-secret"},
                    "after_unknown": {"arn": True},
                },
            },
            {
                "address": "aws_sns_topic.old",
                "type": "aws_sns_topic",
                "mode": "managed",
                "change": {
                    "actions": ["delete", "create"],
                    "before": {"name": "a"},
                    "after": {"name": "b"},
                    "after_unknown": {},
                },
            },
            {"address": "data.aws_iam_policy_document.d", "type": "x", "mode": "data", "change": {"actions": ["read"]}},
            {"address": "aws_s3_bucket.same", "type": "aws_s3_bucket", "mode": "managed", "change": {"actions": ["no-op"]}},
        ],
        "output_changes": {"shown": {"actions": ["update"], "before": "nonsensitive-output-value", "after": "x"}},
        "resource_drift": [{"address": 'aws_iam_role.d["hunter2-key-value"]'}],
    }


def run(tmp_path: Path, *args: str, plan: dict | None = None) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, "-I", str(SCRIPT), "--head-sha", HEAD, "--run-url", RUN, *args]
    if plan is not None:
        (tmp_path / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        argv += ["--plan-json", str(tmp_path / "plan.json")]
    return subprocess.run(argv, capture_output=True, text=True)


def test_values_keys_and_tags_never_reach_the_comment(tmp_path: Path) -> None:
    out = run(tmp_path, "--verdict", "PASS", plan=_plan())
    assert out.returncode == 0, out.stderr
    for secret in SECRETS:
        assert secret not in out.stdout + out.stderr
    assert "[" not in out.stdout.replace("[run]", "")


def test_counts_rows_names_outputs_and_drift_render(tmp_path: Path) -> None:
    out = run(tmp_path, "--verdict", "BLOCKED", plan=_plan()).stdout
    assert "State: CHANGES" in out
    assert "Predicted guard verdict: BLOCKED" in out
    assert "1 to import" not in out
    assert "0 to import, 2 to add, 1 to change, 1 to destroy (1 replaced)." in out
    assert "| aws_iam_role.r | update | name, path |" in out
    assert "| module.m.aws_s3_bucket.b | create | arn, bucket |" in out
    assert "| aws_sns_topic.old | create,delete | name |" in out
    assert "Changed outputs: shown" in out
    assert "`aws_iam_role.d`" in out
    assert "data.aws_iam_policy_document" not in out


def test_no_changes_state(tmp_path: Path) -> None:
    out = run(tmp_path, plan={"resource_changes": [], "output_changes": {}}).stdout
    assert "State: NO_CHANGES" in out
    assert "|" not in out


@pytest.mark.parametrize("category", ["INIT_FAILED", "PLAN_FAILED", "BACKSTOP_REFUSED", "GUARD_ERROR", "PERSIST_FAILED"])
def test_failure_categories_render_fixed_state_and_no_saved_plan(tmp_path: Path, category: str) -> None:
    out = run(tmp_path, "--category", category).stdout
    assert f"State: {category}" in out
    assert "No saved plan: after merge, apply by a blank workflow_dispatch of terraform-apply-sandbox." in out


def test_unreadable_plan_is_plan_failed(tmp_path: Path) -> None:
    (tmp_path / "plan.json").write_text("not json at all with secret-text", encoding="utf-8")
    out = run(tmp_path, "--plan-json", str(tmp_path / "plan.json"))
    assert "State: PLAN_FAILED" in out.stdout
    assert "secret-text" not in out.stdout + out.stderr


def test_fit_line_is_exactly_one_word(tmp_path: Path) -> None:
    fits = tmp_path / "fit1"
    fits.write_text("Reviewer digest: 3 actionable of 9 resource change(s), 1234 of 8000 bytes.\n", encoding="utf-8")
    over = tmp_path / "fit2"
    over.write_text(
        "Reviewer digest OVERFLOW: actionable content is 9999 bytes, over the 7000-byte budget\n", encoding="utf-8"
    )
    assert "Reviewer digest fit: FITS\n" in run(tmp_path, "--fit-file", str(fits), plan=_plan()).stdout
    out = run(tmp_path, "--fit-file", str(over), plan=_plan()).stdout
    assert "Reviewer digest fit: OVERFLOW\n" in out
    assert "9999" not in out and "1234" not in out


def test_saved_plan_note_and_sha(tmp_path: Path) -> None:
    sha = "b" * 64
    out = run(tmp_path, "--saved-plan", "yes", "--plan-sha256", sha, plan=_plan()).stdout
    assert f"Saved-plan sha256: `{sha}`" in out
    assert "applied at merge" in out
    assert "No saved plan" not in out
    bad = run(tmp_path, "--saved-plan", "yes", "--plan-sha256", "not-a-sha", plan=_plan()).stdout
    assert "Saved-plan sha256" not in bad


def test_github_output(tmp_path: Path) -> None:
    gh = tmp_path / "out"
    run(tmp_path, "--verdict", "PASS", "--github-output", str(gh), plan=_plan())
    assert gh.read_text(encoding="utf-8") == "state=CHANGES\nverdict=PASS\n"


def test_bad_head_sha_or_run_url_fail_closed(tmp_path: Path) -> None:
    bad = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--head-sha", "zz", "--run-url", RUN, "--category", "PLAN_FAILED"],
        capture_output=True,
        text=True,
    )
    assert bad.returncode == 2 and bad.stdout == ""
    bad = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--head-sha", HEAD, "--run-url", "http://evil/x", "--category", "PLAN_FAILED"],
        capture_output=True,
        text=True,
    )
    assert bad.returncode == 2 and bad.stdout == ""


def _refusal(tmp_path: Path, category: str, overlay: str = "", screen: str = "") -> subprocess.CompletedProcess[str]:
    (tmp_path / "overlay.txt").write_text(overlay, encoding="utf-8")
    (tmp_path / "screen.txt").write_text(screen, encoding="utf-8")
    return run(
        tmp_path,
        "--refusal",
        category,
        "--overlay-file",
        str(tmp_path / "overlay.txt"),
        "--screen-file",
        str(tmp_path / "screen.txt"),
    )


def test_refusal_renders_screen_rule_lines_without_tfvars(tmp_path: Path) -> None:
    out = _refusal(tmp_path, "SCREEN_REFUSED", screen="REFUSED heredoc main.tf:12\nREFUSED resource_type x.tf:3\n")
    assert out.returncode == 0, out.stderr
    assert "State: SCREEN_REFUSED" in out.stdout
    assert "REFUSED heredoc main.tf:12" in out.stdout
    assert "REFUSED resource_type x.tf:3" in out.stdout
    assert "No saved plan" in out.stdout


def test_refusal_renders_overlay_rule_with_checked_path(tmp_path: Path) -> None:
    out = _refusal(tmp_path, "OVERLAY_REFUSED", overlay="OVERLAY_REFUSED file_mode link.tf\n")
    assert "REFUSED overlay:file_mode link.tf:0" in out.stdout
    out = _refusal(tmp_path, "OVERLAY_REFUSED", overlay="OVERLAY_REFUSED merge_ref_absent\n")
    assert "REFUSED overlay:merge_ref_absent -:0" in out.stdout


def test_refusal_drops_lines_outside_the_grammar(tmp_path: Path) -> None:
    out = _refusal(
        tmp_path,
        "SCREEN_REFUSED",
        screen="REFUSED heredoc main.tf:12\nREFUSED evil `rm -rf` x.tf:1\nplain secret text\nREFUSED a b/c.tf:1\n",
    )
    assert out.returncode == 0
    assert "secret text" not in out.stdout and "rm -rf" not in out.stdout and "b/c.tf" not in out.stdout
    assert "REFUSED heredoc main.tf:12" in out.stdout


def test_assume_failed_refusal_needs_no_files(tmp_path: Path) -> None:
    out = run(tmp_path, "--refusal", "ASSUME_FAILED")
    assert out.returncode == 0
    assert "State: ASSUME_FAILED" in out.stdout


def test_refusal_rejects_post_credential_category(tmp_path: Path) -> None:
    out = run(tmp_path, "--refusal", "PLAN_FAILED")
    assert out.returncode == 2 and out.stdout == ""


def test_closed_grammar_validator_fails_closed() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.ci import terraform_personal_plan_report as rep

    with pytest.raises(ValueError):
        rep.validate_refusal_body("### terraform/personal speculative plan (trusted, Decision 238)\nsome free text\n")


def test_overlay_refusal_for_a_config_file_path_is_rendered(tmp_path: Path) -> None:
    out = _refusal(tmp_path, "OVERLAY_REFUSED", overlay="OVERLAY_REFUSED main_only_file_changed config/feature_flags.yaml\n")
    assert out.returncode == 0
    assert "REFUSED overlay:main_only_file_changed config/feature_flags.yaml:0" in out.stdout


def test_verdict_not_shown_next_to_a_failure_state(tmp_path: Path) -> None:
    out = run(tmp_path, "--category", "PLAN_FAILED", "--verdict", "PASS").stdout
    assert "Predicted guard verdict" not in out


OPAQUE_TEXT = (
    "Reviewer digest OPAQUE: policies unknown at plan time: aws_iam_role_policy.ci_a[*], aws_iam_role_policy.ci_b; "
    "deferred documents: data.aws_iam_policy_document.a. "
    "The merge-time review cannot read them: build ARNs from names or split the PR."
)


def test_opaque_fit_line_is_one_word_and_lists_addresses(tmp_path: Path) -> None:
    fit = tmp_path / "fit"
    fit.write_text(OPAQUE_TEXT + "\n", encoding="utf-8")
    out = run(tmp_path, "--fit-file", str(fit), plan=_plan()).stdout
    assert "Reviewer digest fit: OPAQUE\n" in out
    expected = (
        "Unknown at plan time: `aws_iam_role_policy.ci_a`, `aws_iam_role_policy.ci_b`, `data.aws_iam_policy_document.a`\n"
    )
    assert expected in out
    assert "build ARNs" not in out and "deferred documents" not in out


def test_opaque_word_wins_over_overflow_in_an_address(tmp_path: Path) -> None:
    fit = tmp_path / "fit"
    fit.write_text(OPAQUE_TEXT.replace("ci_b", "OVERFLOW"), encoding="utf-8")
    assert "Reviewer digest fit: OPAQUE\n" in run(tmp_path, "--fit-file", str(fit), plan=_plan()).stdout


def test_malformed_opaque_text_renders_word_without_address_line(tmp_path: Path) -> None:
    fit = tmp_path / "fit"
    fit.write_text("Reviewer digest OPAQUE: policies unknown at plan time: a b c; extra text 9999", encoding="utf-8")
    out = run(tmp_path, "--fit-file", str(fit), plan=_plan()).stdout
    assert "Reviewer digest fit: OPAQUE\n" in out
    assert "Unknown at plan time" not in out and "9999" not in out


def test_fits_and_overflow_render_no_address_line(tmp_path: Path) -> None:
    for text in ("Reviewer digest: 1 actionable of 2 resource change(s), 5 of 8 bytes.", "Reviewer digest OVERFLOW: x"):
        fit = tmp_path / "fit"
        fit.write_text(text, encoding="utf-8")
        assert "Unknown at plan time" not in run(tmp_path, "--fit-file", str(fit), plan=_plan()).stdout


def test_opaque_addresses_round_trip_the_producer_line() -> None:
    from scripts.ci.terraform_personal_plan_report import opaque_addresses
    from scripts.ci.terraform_plan_digest import _opaque_line

    line = _opaque_line(["aws_iam_role_policy.a[*]", "module.m[*].aws_iam_policy.b"], ["data.aws_iam_policy_document.c"])
    assert opaque_addresses(line) == [
        "aws_iam_role_policy.a[*]",
        "module.m[*].aws_iam_policy.b",
        "data.aws_iam_policy_document.c",
    ]
