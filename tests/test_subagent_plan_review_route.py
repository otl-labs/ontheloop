"""review.sh's opt-in REVISE route (rec-5205, Decision 265): REVISE_ROUTES exactly "true" exits 0 with outcome=revise."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.fixtures.review_sh_harness import ReviewShHarness, envelope

ROOT = Path(__file__).parent.parent
ACTION_YML = ROOT / ".github" / "actions" / "subagent-plan-review" / "action.yml"

_RECORDED_REVISE_REASON = (
    "`aws_iam_role_policy.github_ci_trusted_plan` is losing the `TfstateGithubRead` SID - this removes "
    "Terraform-state read access from the CI plan role."
)
_ROUTED_LINE = "Routed to the tf-gated-apply Environment: approve only after reading the reason above (rec-5205)."


@pytest.fixture()
def harness(tmp_path: Path) -> ReviewShHarness:
    return ReviewShHarness(tmp_path)


def test_revise_routes_with_opt_in(harness: ReviewShHarness) -> None:
    harness.run(envelope("REVISE too broad"), revise_routes="true")
    assert harness.rc == 0
    assert harness.outcome == "revise"
    assert harness.claude_calls == 1
    assert not harness.retry_transcript.exists()
    assert "REVISE reason: too broad" in harness.summary
    assert _ROUTED_LINE in harness.summary
    assert "::warning::[SUBAGENT-REVIEW] REVISE routed to tf-gated-apply (rec-5205)" in harness.stderr
    assert "Subagent returned REVISE; routing to tf-gated-apply for operator approval." in harness.stderr


@pytest.mark.parametrize("value", [None, "", "false", "TRUE", "yes"])
def test_revise_fails_closed_without_opt_in(harness: ReviewShHarness, value: str | None) -> None:
    harness.run(envelope("REVISE too broad"), revise_routes=value)
    assert harness.rc == 1
    assert harness.outcome == "revise"
    assert "Subagent returned REVISE; failing closed." in harness.stderr
    assert _ROUTED_LINE not in harness.summary
    assert "::warning::" not in harness.stderr


def test_starved_still_blocks_with_opt_in(harness: ReviewShHarness) -> None:
    starved = envelope("", subtype="error_max_turns", is_error=True)
    harness.run(starved, starved, revise_routes="true")
    assert harness.rc == 1
    assert harness.outcome == "starved"
    assert harness.claude_calls == 2
    assert harness.retry_transcript.exists()


def test_proceed_unchanged_with_opt_in(harness: ReviewShHarness) -> None:
    harness.run(envelope("PROCEED"), revise_routes="true")
    assert harness.rc == 0
    assert harness.outcome == "proceed"
    assert _ROUTED_LINE not in harness.summary


def test_action_maps_revise_routes_env() -> None:
    action = yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))
    assert action["runs"]["steps"][0]["env"]["REVISE_ROUTES"] == "${{ inputs.revise-routes }}"
    assert action["inputs"]["revise-routes"]["default"] == "false"
    assert action["inputs"]["revise-routes"]["required"] is False


def test_rec_5205_sid_removal_routes(harness: ReviewShHarness) -> None:
    harness.run(envelope("REVISE " + _RECORDED_REVISE_REASON), revise_routes="true")
    assert harness.rc == 0
    assert harness.outcome == "revise"
    rendered = _RECORDED_REVISE_REASON.replace("`", "'")
    assert rendered in harness.summary
    assert _ROUTED_LINE in harness.summary
