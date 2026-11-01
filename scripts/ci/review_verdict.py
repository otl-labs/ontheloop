#!/usr/bin/env python3
"""Testable verdict classifier for the sandbox subagent plan-review step (T2.39, rec-2658 forward-fix).

Classifies a `claude -p --output-format json` transcript into exactly one of three
mutually-exclusive classes:

  PROCEED -- the reviewer emitted an explicit apply-eligible verdict. Apply proceeds.
  REVISE  -- the reviewer emitted an explicit rejection. Fail closed: the convergence record
             write step sets status=red.
  STARVED -- the reviewer exhausted its turn budget, produced no parseable verdict, or the CLI
             invocation itself failed (max-turns, empty/no-token result, API error). This blocks
             auto-apply and files a labelled rec, but MUST NEVER be treated as an implicit REVISE
             (that would mask a reviewer-infrastructure failure as a plan rejection) and MUST
             NEVER be treated as an implicit PROCEED (fail-open, unsafe). The record-write step
             gates on this class via a REVIEW_STARVED marker and does not write red for it
             (Decision 55 anti-masking).

rec-2658 root cause: scripts/ci/claude_p_retry.sh redirects both stdout and stderr into the same
output file (`claude -p "$@" > "$OUTPUT_FILE" 2>&1`). A max-turns/no-verdict run -- precisely the
STARVED class this module exists to detect -- is exactly when CLI/npm diagnostic lines are most
likely to surround the JSON result object. classify() therefore never assumes the file is clean
JSON: it locates the LAST well-formed top-level JSON object in the text (tolerating leading,
trailing, or interleaved non-JSON noise) before inspecting it, and falls back to a raw-text token
scan only when no such object exists at all.

classify() is a pure function of its input string -- no I/O, no side effects -- so the
PROCEED/REVISE/STARVED partition is unit-provable (tests/test_review_verdict.py).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

PROCEED = "PROCEED"
REVISE = "REVISE"
STARVED = "STARVED"

# subtype values the Claude Code CLI's --output-format json envelope emits when the run did not
# complete a substantive turn (turn-budget exhaustion, harness-level execution failure). Both are
# reviewer starvation, not a reviewer judgment -- neither implies REVISE nor PROCEED.
_STARVED_SUBTYPES = frozenset({"error_max_turns", "error_during_execution"})

# A line consisting of exactly the REVISE token (optionally followed by a same-line reason).
_REVISE_PATTERN = re.compile(r"^[ \t]*REVISE\b", re.MULTILINE)
# A line consisting of exactly the PROCEED token (matches the original grep -qx "PROCEED" contract).
_PROCEED_PATTERN = re.compile(r"^[ \t]*PROCEED[ \t]*$", re.MULTILINE)

# Keys that mark a top-level JSON object as a claude result envelope (vs. incidental JSON noise,
# e.g. an unrelated npm warning that happens to print a JSON blob).
_ENVELOPE_MARKER_KEYS = frozenset({"type", "subtype", "result", "is_error"})


def _iter_json_objects(text: str):
    """Yield every top-level JSON value parseable via raw_decode, scanning left to right.

    Tolerant of leading/trailing/interleaved non-JSON noise (stderr lines): only attempts a
    decode at each '{' position, so it never mistakes noise for JSON and never raises.
    """
    decoder = json.JSONDecoder()
    idx = 0
    length = len(text)
    while idx < length:
        brace = text.find("{", idx)
        if brace == -1:
            return
        try:
            obj, end = decoder.raw_decode(text, brace)
            yield obj
            idx = end
        except json.JSONDecodeError:
            idx = brace + 1


def _last_result_envelope(text: str) -> Optional[dict[str, Any]]:
    """Return the LAST top-level JSON object in text that looks like a claude result envelope.

    A result envelope is any dict carrying at least one of the marker keys -- the shape
    `claude -p --output-format json` emits. Returns None if no such object is found (e.g. the
    file is empty, pure stderr noise, or a plain --output-format text transcript).
    """
    envelope: Optional[dict[str, Any]] = None
    for obj in _iter_json_objects(text):
        if isinstance(obj, dict) and (_ENVELOPE_MARKER_KEYS & obj.keys()):
            envelope = obj
    return envelope


def classify(raw_output: str) -> str:
    """Classify a claude_p_retry.sh output file's contents into PROCEED / REVISE / STARVED.

    Total and mutually exclusive by construction: every branch returns exactly one of the three
    class constants, so no input can map to more than one class.
    """
    envelope = _last_result_envelope(raw_output)

    if envelope is not None:
        if envelope.get("subtype") in _STARVED_SUBTYPES:
            return STARVED

        result_field = envelope.get("result")
        result_text = result_field if isinstance(result_field, str) else ""

        if _REVISE_PATTERN.search(result_text):
            return REVISE
        if _PROCEED_PATTERN.search(result_text):
            return PROCEED
        if envelope.get("is_error"):
            # An execution failure (API-exhausted, harness error) with no subtype we recognise
            # and no verdict token in the result -- starvation, not a rejection.
            return STARVED
        # A well-formed, non-error envelope that emitted neither token -- the no-verdict class
        # (T2.39 c1). Never treated as an implicit PROCEED (fail-open) or REVISE (masks an
        # infra issue as a plan rejection).
        return STARVED

    # No parseable result envelope at all (empty file, pure stderr noise, a non-JSON
    # --output-format text transcript, or a totally malformed file). Fall back to a raw-text
    # token scan so a plain-text verdict is still honoured; anything else is STARVED.
    if _REVISE_PATTERN.search(raw_output):
        return REVISE
    if _PROCEED_PATTERN.search(raw_output):
        return PROCEED
    return STARVED


_REASON_CAP = 500


def revise_reason(raw_output: str) -> str:
    """One redacted, newline-free line holding the reviewer's text after the REVISE token."""
    envelope = _last_result_envelope(raw_output)
    source = raw_output
    if envelope is not None:
        result_field = envelope.get("result")
        source = result_field if isinstance(result_field, str) else ""
    match = _REVISE_PATTERN.search(source)
    text = source[match.end() :] if match else ""
    text = re.sub(r"\s+", " ", text)
    text = "".join(ch for ch in text if ch.isprintable())
    text = text.lstrip(" :-").strip()
    if not text:
        return "(no reason given)"
    repo_root = str(Path(__file__).resolve().parents[2])
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from scripts.terraform_apply_guard import _redact

    text = _redact(text).lstrip(" :-")
    if len(text) > _REASON_CAP:
        text = text[: _REASON_CAP - 3] + "..."
    return text or "(no reason given)"


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entrypoint: read a claude_p_retry.sh output file, print the verdict class, exit.

    Exit codes (consumed by terraform-apply-sandbox.yml's review step):
      0 -- PROCEED
      2 -- REVISE  (fail closed; the record-write step sets status=red)
      1 -- STARVED (blocks apply; the record-write step must NOT set status=red)

    An unreadable transcript file is itself a reviewer-infrastructure failure, so it classifies
    as STARVED (exit 1) rather than raising -- never as REVISE (which would incorrectly red the
    convergence record for what is an I/O problem, not a plan rejection).
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "--reason":
        try:
            raw_text = Path(args[1]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            print("(transcript unreadable)")
            return 0
        print(revise_reason(raw_text))
        return 0
    if len(args) != 1:
        print("usage: review_verdict.py <claude-output-file>", file=sys.stderr)
        print(STARVED)
        return 1

    path = Path(args[0])
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"review_verdict: cannot read {path!r}: {exc}", file=sys.stderr)
        print(STARVED)
        return 1

    verdict = classify(raw)
    print(verdict)
    if verdict == PROCEED:
        return 0
    if verdict == REVISE:
        return 2
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
