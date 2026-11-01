"""Tests for scripts/ci/review_verdict.py (T2.39 / rec-2658 forward-fix).

Asserts the PROCEED/REVISE/STARVED partition is mutually exclusive and total, and that
classification survives the claude_p_retry.sh 2>&1 stderr-merge shape (leading/trailing/
interleaved CLI or npm diagnostic noise around the JSON result envelope) -- the exact
contamination pattern rec-2658's max-turns runs produce, which a naive json.load would break on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.ci.review_verdict import PROCEED, REVISE, STARVED, classify, main, revise_reason

# ---------------------------------------------------------------------------
# Envelope builders
# ---------------------------------------------------------------------------


def _success_envelope(result: str) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": result,
            "session_id": "sess-1",
            "num_turns": 3,
            "usage": {"input_tokens": 100, "output_tokens": 20},
        }
    )


def _max_turns_envelope() -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "error_max_turns",
            "is_error": True,
            "result": "",
            "session_id": "sess-2",
            "num_turns": 10,
        }
    )


def _api_error_envelope() -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "result": "",
            "session_id": "sess-3",
        }
    )


_NPM_NOISE_PREFIX = (
    "npm warn deprecated some-package@1.0.0: no longer maintained\n"
    "npm warn config also Please replace usage of config flag --also...\n"
)
_CLI_TRACE_SUFFIX = (
    "\n[claude] session ended after reaching the configured turn limit\n[claude] exiting with non-zero status\n"
)


# ---------------------------------------------------------------------------
# Clean JSON classification
# ---------------------------------------------------------------------------


def test_proceed_json_classifies_proceed() -> None:
    assert classify(_success_envelope("PROCEED")) == PROCEED


def test_proceed_json_with_trailing_newline_classifies_proceed() -> None:
    assert classify(_success_envelope("PROCEED\n")) == PROCEED


def test_revise_json_classifies_revise() -> None:
    assert classify(_success_envelope("REVISE this touches a destroy on the prod bucket")) == REVISE


def test_max_turns_classifies_starved() -> None:
    assert classify(_max_turns_envelope()) == STARVED


def test_api_exhausted_error_classifies_starved() -> None:
    assert classify(_api_error_envelope()) == STARVED


def test_empty_result_no_verdict_token_classifies_starved() -> None:
    # A "success" envelope that nonetheless emitted no PROCEED/REVISE token -- the no-verdict
    # class (T2.39 c1). Never an implicit PROCEED, never an implicit REVISE.
    assert classify(_success_envelope("")) == STARVED


def test_success_with_prose_but_no_token_classifies_starved() -> None:
    assert classify(_success_envelope("I looked at the plan and it seems fine overall.")) == STARVED


# ---------------------------------------------------------------------------
# Stderr-interleaved input (the real claude_p_retry.sh 2>&1-merged shape).
# rec-2658's max-turns runs are exactly when this noise appears -- REQUIRED cases per the plan.
# ---------------------------------------------------------------------------


def test_proceed_survives_leading_and_trailing_stderr_noise() -> None:
    contaminated = _NPM_NOISE_PREFIX + _success_envelope("PROCEED") + _CLI_TRACE_SUFFIX
    assert classify(contaminated) == PROCEED


def test_max_turns_survives_leading_and_trailing_stderr_noise() -> None:
    contaminated = _NPM_NOISE_PREFIX + _max_turns_envelope() + _CLI_TRACE_SUFFIX
    assert classify(contaminated) == STARVED


def test_revise_survives_leading_and_trailing_stderr_noise() -> None:
    contaminated = _NPM_NOISE_PREFIX + _success_envelope("REVISE unsafe destroy") + _CLI_TRACE_SUFFIX
    assert classify(contaminated) == REVISE


def test_last_of_multiple_json_objects_wins() -> None:
    # A stray unrelated JSON blob (e.g. an npm --json warning) appears before the real result --
    # the LAST well-formed envelope must be the one classified, not the first.
    noise_json = json.dumps({"npm_warning": "deprecated", "level": "warn"})
    contaminated = noise_json + "\n" + _success_envelope("PROCEED")
    assert classify(contaminated) == PROCEED


def test_last_of_multiple_json_objects_wins_starved_case() -> None:
    contaminated = _success_envelope("PROCEED") + "\n" + _max_turns_envelope()
    # A retried transcript concatenated in order -- the LAST envelope (the actual final outcome)
    # governs, not an earlier one.
    assert classify(contaminated) == STARVED


def test_json_object_embedded_mid_stderr_line_does_not_break_scan() -> None:
    # A stderr diagnostic line that itself contains an unbalanced or partial '{' must not abort
    # the scan for the real envelope later in the text.
    contaminated = 'npm warn odd-line: {"partial\n' + _success_envelope("PROCEED")
    assert classify(contaminated) == PROCEED


# ---------------------------------------------------------------------------
# No parseable JSON at all -- raw-text fallback.
# ---------------------------------------------------------------------------


def test_no_json_at_all_with_revise_token_classifies_revise() -> None:
    assert classify("Reviewing the plan...\nREVISE the IAM policy is too broad\n") == REVISE


def test_no_json_at_all_with_proceed_token_classifies_proceed() -> None:
    assert classify("Reviewing the plan...\nPROCEED\n") == PROCEED


def test_totally_empty_input_classifies_starved() -> None:
    assert classify("") == STARVED


def test_pure_noise_no_tokens_classifies_starved() -> None:
    assert classify(_NPM_NOISE_PREFIX + _CLI_TRACE_SUFFIX) == STARVED


def test_malformed_json_fragment_classifies_starved() -> None:
    assert classify('{"result": "PROC') == STARVED


# ---------------------------------------------------------------------------
# Mutual exclusivity / totality (T2.39 c3): every input maps to exactly one class.
# ---------------------------------------------------------------------------

_ALL_SAMPLE_INPUTS = [
    _success_envelope("PROCEED"),
    _success_envelope("REVISE nope"),
    _success_envelope(""),
    _max_turns_envelope(),
    _api_error_envelope(),
    "",
    "no json here, no tokens either",
    "PROCEED",
    "REVISE bad",
    _NPM_NOISE_PREFIX + _success_envelope("PROCEED") + _CLI_TRACE_SUFFIX,
    _NPM_NOISE_PREFIX + _max_turns_envelope() + _CLI_TRACE_SUFFIX,
]


@pytest.mark.parametrize("raw", _ALL_SAMPLE_INPUTS)
def test_classify_always_returns_exactly_one_known_class(raw: str) -> None:
    result = classify(raw)
    assert result in (PROCEED, REVISE, STARVED)


def test_partition_is_mutually_exclusive_across_samples() -> None:
    # Sanity: PROCEED-tagged samples never classify as REVISE/STARVED and vice versa, given the
    # curated expectations below.
    expectations = {
        _success_envelope("PROCEED"): PROCEED,
        _success_envelope("REVISE nope"): REVISE,
        _max_turns_envelope(): STARVED,
        _api_error_envelope(): STARVED,
        _success_envelope(""): STARVED,
    }
    for raw, expected in expectations.items():
        got = classify(raw)
        assert got == expected
        others = {PROCEED, REVISE, STARVED} - {expected}
        assert got not in others


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, content: str) -> str:
    target = tmp_path / "review.txt"
    target.write_text(content, encoding="utf-8")
    return str(target)


def test_main_proceed_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    path = _write(tmp_path, _success_envelope("PROCEED"))
    assert main([path]) == 0
    assert capsys.readouterr().out.strip() == PROCEED


def test_main_revise_exits_two(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    path = _write(tmp_path, _success_envelope("REVISE too risky"))
    assert main([path]) == 2
    assert capsys.readouterr().out.strip() == REVISE


def test_main_starved_exits_one(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    path = _write(tmp_path, _max_turns_envelope())
    assert main([path]) == 1
    assert capsys.readouterr().out.strip() == STARVED


def test_main_missing_file_classifies_starved_not_revise(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    # An unreadable transcript is a reviewer-infra failure, not a rejection -- must never exit 2
    # (which would red the convergence record for an I/O problem).
    assert main([str(tmp_path / "does_not_exist.txt")]) == 1
    assert capsys.readouterr().out.strip() == STARVED


def test_main_usage_error_no_args() -> None:
    assert main([]) == 1


def test_main_usage_error_too_many_args() -> None:
    assert main(["a", "b"]) == 1


def test_main_stderr_interleaved_transcript_proceed(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    contaminated = _NPM_NOISE_PREFIX + _success_envelope("PROCEED") + _CLI_TRACE_SUFFIX
    path = _write(tmp_path, contaminated)
    assert main([path]) == 0
    assert capsys.readouterr().out.strip() == PROCEED


# ---------------------------------------------------------------------------
# revise_reason / --reason
# ---------------------------------------------------------------------------


def test_revise_reason_collapses_multiline() -> None:
    raw = _success_envelope("REVISE: first line\n\nsecond   line\tthird")
    assert revise_reason(raw) == "first line second line third"


def test_revise_reason_redacts_arn_and_account_id() -> None:
    acct_a, acct_b = "1" * 12, "2" * 12
    arn = f"arn:aws:iam::{acct_a}:role/x"
    out = revise_reason(_success_envelope(f"REVISE role {arn} in account {acct_b}"))
    assert acct_a not in out and acct_b not in out
    assert "[ARN]" in out and "[ACCOUNT_ID]" in out


@pytest.mark.parametrize("reason", ["::error::boom", "\n::warning::x", "- ::error::y"])
def test_revise_reason_never_yields_workflow_command_line(reason: str) -> None:
    out = revise_reason(_success_envelope("REVISE " + reason))
    assert "\n" not in out
    assert not out.startswith("::")


def test_revise_reason_caps_at_500_with_ellipsis() -> None:
    out = revise_reason(_success_envelope("REVISE " + "a" * 900))
    assert len(out) == 500 and out.endswith("...")


def test_revise_reason_empty() -> None:
    assert revise_reason(_success_envelope("REVISE")) == "(no reason given)"


def test_revise_reason_raw_text_fallback() -> None:
    assert revise_reason("noise\nREVISE too broad\n") == "too broad"


def test_reason_cli_revise_proceed_and_unreadable(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    rev = _write(tmp_path, _success_envelope("REVISE digest truncated"))
    assert main(["--reason", rev]) == 0
    assert capsys.readouterr().out.strip() == "digest truncated"
    pro = tmp_path / "p.json"
    pro.write_text(_success_envelope("PROCEED"))
    assert main(["--reason", str(pro)]) == 0
    assert capsys.readouterr().out.strip() == "(no reason given)"
    assert main(["--reason", str(tmp_path / "nope")]) == 0
    assert capsys.readouterr().out.strip() == "(transcript unreadable)"


def test_one_argument_contract_unchanged_with_reason_as_filename(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    path = _write(tmp_path, _success_envelope("REVISE nope"))
    assert main([path]) == 2
    assert capsys.readouterr().out.strip() == REVISE
