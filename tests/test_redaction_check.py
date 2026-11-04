"""Guards for scripts/ci/redaction_check.py and its wiring into terraform-apply-sandbox.yml.

Regression origin (rec-3322): the speculative-plan job ran a 12-digit account-id shape check over
the WHOLE comment body, which also embeds the saved-plan sha256 digest and the 40-hex PR head SHA.
A sha256 routinely contains a run of exactly 12 digits bounded by hex letters, so the job failed
with REDACTION_FAIL on PR #975 while the account id had been correctly redacted -- suppressing the
plan comment that is the CD.35 Wave 2 review artifact.

All identifiers here are synthetic. Never put a real account id, ExternalId or owner email in this
repository (Decision 101 public-repo boundary; the never-commit hook blocks 12-digit ids).
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest
import yaml

from scripts.ci.redaction_check import ACCOUNT_ID_SHAPE, main, mask_account_ids, redact, scrub_pairs, unscrubbed_labels

WORKFLOW = Path(".github/workflows/terraform-plan-trusted.yml")
STEP_NAME = "Post the plan comment"

FAKE_ACCT = "123456789012"
FAKE_DEV = "dev/ext&id=1"
FAKE_ADM = "adm/ext&id=2"
FAKE_OWNER = "owner@example.invalid"

# The literal digest from the PR #975 incident. Its substring f740037807611c carries twelve digits
# bounded by hex letters, which is what the old whole-body shape check matched. Neither value is a
# secret: the digest is a hash of a terraform plan file that the workflow itself posts publicly as
# the terraform-plan-digest commit status, and the SHA is a public commit id. detect-secrets flags
# any high-entropy hex, so both carry the documented false-positive pragma.
INCIDENT_DIGEST = "0ddac2f56279dd3b6f740037807611c6ffc3ea6daab5ae2f08b9725a813ec6da"  # pragma: allowlist secret
INCIDENT_HEAD_SHA = "a11df67b4738c6237a2ebffafbce3dd0f0c845ad"  # pragma: allowlist secret


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    def _apply(acct: str = FAKE_ACCT, dev: str = FAKE_DEV, adm: str = FAKE_ADM, owner: str = FAKE_OWNER):
        for name, value in (("_ACCT", acct), ("_DEV", dev), ("_ADM", adm), ("_OWNER", owner)):
            monkeypatch.setenv(name, value)

    return _apply


def _run(monkeypatch: pytest.MonkeyPatch, mode: str, stdin: str) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    return main(["redaction_check.py", mode])


def test_account_shape_ignores_digit_runs_embedded_in_hex_tokens() -> None:
    """The shape is token-boundary-aware, so hex hashes are not account ids.

    The incident digest carries a bare 12-digit run (which is what the old digit-boundary-only
    pattern matched), but it is bounded by hex letters, so it is not a token and must not match.
    An md5 query_hash -- which this plan's null_resource triggers really do carry -- is the same
    class, at a measured ~1.2% hit rate each under the old pattern.
    """
    assert re.search(r"(?<!\d)\d{12}(?!\d)", INCIDENT_DIGEST), "digest no longer carries a bare 12-digit run"
    assert not ACCOUNT_ID_SHAPE.search(INCIDENT_DIGEST), "hex-embedded digit run must not read as an account id"
    assert not ACCOUNT_ID_SHAPE.search("f740037807611c"), "md5-style embedded run must not match"


@pytest.mark.parametrize(
    "text",
    [
        # Composed at runtime, never written as a literal ARN: the never-commit pygrep hook blocks
        # the arn:aws:<svc>::<12 digits>: shape on sight, synthetic account id or not.
        f"arn:aws:iam::{FAKE_ACCT}:role/agent-platform-github-ci-apply",
        FAKE_ACCT,
        f"account = {FAKE_ACCT},",
    ],
)
def test_account_shape_still_matches_a_real_account_id_token(text: str) -> None:
    assert ACCOUNT_ID_SHAPE.search(text), f"a genuine account-id token must still match: {text!r}"


def test_assert_clean_passes_on_body_embedding_the_incident_digest(
    env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """THE regression test: a body carrying the incident digest and a 40-hex SHA is clean."""
    env()
    body = f"Saved-plan sha256 digest: `{INCIDENT_DIGEST}`\nPR head SHA: `{INCIDENT_HEAD_SHA}`\n"
    assert _run(monkeypatch, "assert-clean", body) == 0
    assert "REDACTION_FAIL" not in capsys.readouterr().out


@pytest.mark.parametrize("secret", [FAKE_ACCT, FAKE_DEV, FAKE_ADM, FAKE_OWNER])
def test_assert_clean_fails_when_any_secret_literal_survives(
    env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], secret: str
) -> None:
    env()
    assert _run(monkeypatch, "assert-clean", f"plan output mentioning {secret} inline") == 1
    out = capsys.readouterr().out
    assert "REDACTION_FAIL" in out
    # The failure diagnostic must NAME the class without ECHOING the value -- otherwise the leak
    # guard becomes the leak. _placeholder_label makes this true by construction (labels are
    # literals reached by control flow), not by convention.
    assert secret not in out, "the diagnostic echoed the very value it was reporting as unscrubbed"


def test_redact_replaces_every_secret_including_sed_hostile_externalids(
    env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """ExternalIds may contain '/' and '&', which is why redaction is str.replace and not sed."""
    env()
    text = f"acct={FAKE_ACCT} dev={FAKE_DEV} adm={FAKE_ADM} owner={FAKE_OWNER}"
    assert _run(monkeypatch, "redact", text) == 0
    out = capsys.readouterr().out
    for secret in (FAKE_ACCT, FAKE_DEV, FAKE_ADM, FAKE_OWNER):
        assert secret not in out
    assert "[ACCOUNT_ID]" in out and "[ExternalId]" in out and "[OWNER_EMAIL]" in out


def test_redact_fails_closed_on_a_second_unredacted_account_shaped_id(
    env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The shape check still guards the plan text: a DIFFERENT 12-digit id must fail closed."""
    env()
    assert _run(monkeypatch, "redact", f"own={FAKE_ACCT} other=210987654321") == 1
    assert "REDACTION_FAIL" in capsys.readouterr().err


@pytest.mark.parametrize("mode", ["redact", "assert-clean"])
def test_empty_account_id_fails_closed_rather_than_no_opping(env, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    """An empty account id means the tfvars parse failed -- redaction would silently no-op."""
    env(acct="")
    assert _run(monkeypatch, mode, "anything") == 1


def test_unknown_mode_is_rejected(env, monkeypatch: pytest.MonkeyPatch) -> None:
    env()
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["redaction_check.py"]) == 1
    assert main(["redaction_check.py", "sanitize"]) == 1


def test_helpers_ignore_unset_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("_ACCT", "_DEV", "_ADM", "_OWNER"):
        monkeypatch.delenv(name, raising=False)
    pairs = scrub_pairs()
    assert [value for value, _ in pairs] == ["", "", "", ""]
    assert redact("untouched", pairs) == "untouched"
    assert unscrubbed_labels("untouched", pairs) == []


def _step_body() -> str:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = doc["jobs"]["plan"]["steps"]
    body = next((s["run"] for s in steps if s.get("name") == STEP_NAME), None)
    assert body is not None, f"step {STEP_NAME!r} not found -- its name is the R3 baseline key"
    return body


def test_workflow_step_delegates_to_the_redaction_helper() -> None:
    body = _step_body()
    assert 'redaction_check.py" assert-clean' in body, "comment body is not literal-asserted"
    assert 'redaction_check.py" redact' not in body, "the names-only comment must never carry redacted plan text"


def test_workflow_step_no_longer_shape_checks_the_whole_comment() -> None:
    """This is what fails without the rec-3322 fix."""
    body = _step_body()
    assert "grep -qP" not in body, "the whole-body shape check survives; it false-positives on hex digests"
    assert "\\d{12}" not in body, "an inline 12-digit shape regex survives in the workflow body"


def test_workflow_step_scrubs_through_the_trusted_env_wrapper() -> None:
    """The secrets reach the redaction helper only as process environment, via trusted_plan_env.sh --scrub-only."""
    body = _step_body()
    assert body.count('trusted_plan_env.sh" --scrub-only') == 2
    assert "terraform.personal.tfvars" not in body


def test_mask_account_ids() -> None:
    acct = str(10**11 + 7)
    aroa = "AROA" + "B" * 17
    aida = "AIDA" + "C" * 16
    sts = f"arn:aws:sts::{acct}:assumed-role/PlatformDev/s"
    iam = f"arn:aws:iam::{acct}:role/x"
    denied = f"AccessDenied: User: {sts} is not authorized"
    assert mask_account_ids(sts) == "arn:aws:sts::[ACCOUNT_ID]:assumed-role/PlatformDev/s"
    assert mask_account_ids(iam) == "arn:aws:iam::[ACCOUNT_ID]:role/x"
    assert (
        mask_account_ids(denied)
        == "AccessDenied: User: arn:aws:sts::[ACCOUNT_ID]:assumed-role/PlatformDev/s is not authorized"
    )
    assert mask_account_ids(f"id {aroa} and {aida}") == "id [UNIQUE_ID] and [UNIQUE_ID]"
    keep = ["a" * 40, "ab" * 20, "abc" + str(10**11 + 7) + "def"]
    for text in keep:
        assert mask_account_ids(text) == text
    once = mask_account_ids(denied)
    assert mask_account_ids(once) == once
