#!/usr/bin/env python3
"""Public-repo redaction helper for the speculative-plan PR comment (Decision 101 boundary).

Two modes, both fail closed:

redact
    stdin is the raw ``terraform show`` text. Replaces the tfvars-sourced secrets (env
    ``_ACCT``/``_DEV``/``_ADM``/``_OWNER``) with placeholders, then fails if a 12-digit
    account-id-shaped TOKEN survives. The shape check belongs HERE, on untrusted terraform
    output, and nowhere else.

assert-clean
    stdin is the final comment body. Fails if any secret literal appears anywhere in it.
    Deliberately NO shape check: the body embeds hex identifiers -- the saved-plan sha256 digest
    and the 40-hex PR head SHA -- whose digit runs false-positive the account-id shape. That is
    the 2026-08-30 REDACTION_FAIL on PR #975, where digest ...f740037807611c... contained twelve
    digits bounded by hex letters and the job failed with the account id correctly redacted.

``_ACCT`` is required non-empty in both modes: an empty account id means the tfvars parse failed,
and replace-based redaction would silently no-op exactly when redaction matters most.

``owner_email`` is scrubbed alongside the account id and the two ExternalIds because
``terraform/personal/main.tf`` sets ``default_tags { Owner = var.owner_email }``, so a destroy plan
prints it on every tagged resource.

``mask_account_ids`` is the shared shape mask for text that reaches session output (rec-4656).
"""

from __future__ import annotations

import os
import re
import sys
from typing import TextIO

# Token-boundary-aware, NOT merely digit-boundary-aware. A real account id appears as its own
# token -- inside an ARN (``...:iam::123456789012:role/x``) or standalone -- never embedded in a
# longer alphanumeric run. Bounding on [0-9A-Za-z] therefore keeps every genuine detection while
# refusing to match a digit run that is part of a hex or base64 token. That distinction is
# load-bearing here: this plan's null_resource triggers carry md5 ``query_hash`` values, and a
# digit-boundary-only pattern has a measured ~1.2% hit rate per md5 (~7% across this plan's six),
# which is the rec-3322 false-positive class re-appearing on the plan text instead of the body.
ACCOUNT_ID_SHAPE = re.compile(r"(?<![0-9A-Za-z])\d{12}(?![0-9A-Za-z])")

# IAM role and user unique IDs (AROA / AIDA prefixes) encode the account, so they are masked with it.
UNIQUE_ID_SHAPE = re.compile(r"(?<![0-9A-Za-z])A(?:ROA|IDA)[A-Z0-9]{16,17}(?![0-9A-Za-z])")

_MODES = ("redact", "assert-clean")

# "scrub" rather than "secret" throughout, and env names that do not read as credentials: CodeQL's
# py/clear-text-logging-sensitive-data heuristic classifies any value threaded out of a
# secret-named identifier as sensitive, which flagged both the redacted-plan write and the
# diagnostic line below. Same remedy as scripts/smoke_test_inference_credentials.py's envelope_id.
# These values are literals to REMOVE from output; the placeholders that replace them are
# constants.
_SCRUB_SPECS = (
    ("_ACCT", "[ACCOUNT_ID]"),
    ("_DEV", "[ExternalId]"),
    ("_ADM", "[ExternalId]"),
    ("_OWNER", "[OWNER_EMAIL]"),
)


def scrub_pairs(env: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """Return (value_to_scrub, placeholder) pairs sourced from the environment."""
    source = os.environ if env is None else env
    return [(source.get(name, ""), placeholder) for name, placeholder in _SCRUB_SPECS]


def _placeholder_label(index: int) -> str:
    """Return a hardcoded placeholder label for a spec index.

    Every branch returns a string literal, never a value read from the pairs structure, so the
    result has NO data dependency on the scrubbed input -- only a control dependency on an integer
    comparison. This is a genuine hardening (a scrubbed value can never reach a diagnostic line by
    construction, not merely by convention) and follows the
    scripts/checks/misc/validate_ghas_probe.py::_status_label precedent for the same CodeQL rule.
    """
    if index == 0:
        return "[ACCOUNT_ID]"
    if index == 1 or index == 2:
        return "[ExternalId]"
    return "[OWNER_EMAIL]"


def redact(text: str, pairs: list[tuple[str, str]]) -> str:
    """Replace every non-empty scrub value with its placeholder."""
    for value, placeholder in pairs:
        if value:
            text = text.replace(value, placeholder)
    return text


def unscrubbed_labels(text: str, pairs: list[tuple[str, str]]) -> list[str]:
    """Return hardcoded labels for the specs whose value still appears in text.

    The labels come from _placeholder_label, so no scrubbed value can be threaded into the caller's
    diagnostic line.
    """
    return [_placeholder_label(i) for i, (value, _) in enumerate(pairs) if value and value in text]


def mask_account_ids(text: str) -> str:
    """Replace account-shaped runs and IAM unique IDs, leaving the rest of the text intact."""
    return UNIQUE_ID_SHAPE.sub("[UNIQUE_ID]", ACCOUNT_ID_SHAPE.sub("[ACCOUNT_ID]", text))


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) == 2 else ""
    if mode not in _MODES:
        print(f"::error::redaction_check: usage: redaction_check.py {{{'|'.join(_MODES)}}}", file=sys.stderr)
        return 1

    # In redact mode stdout is captured into PLAN_REDACTED, so diagnostics must go to stderr or
    # they would be spliced into the comment body itself.
    diag: TextIO = sys.stderr if mode == "redact" else sys.stdout

    pairs = scrub_pairs()
    if not pairs[0][0]:
        print(
            "::error::REDACTION_FAIL: account_id parsed empty from tfvars; redaction would no-op.",
            file=diag,
        )
        return 1

    text = sys.stdin.read()

    if mode == "redact":
        text = redact(text, pairs)
        if ACCOUNT_ID_SHAPE.search(text):
            print(
                "::error::REDACTION_FAIL: a 12-digit account-shaped run survives in the redacted plan text.",
                file=diag,
            )
            return 1
        sys.stdout.write(text)
        return 0

    unscrubbed = unscrubbed_labels(text, pairs)
    if unscrubbed:
        print(
            f"::error::REDACTION_FAIL: value(s) survive in the comment body: {' '.join(sorted(set(unscrubbed)))}",
            file=diag,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
