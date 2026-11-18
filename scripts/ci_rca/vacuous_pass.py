"""Deterministic gate-escape evidence: vacuous-pass detection and merged-diff analysis.

Provides three evidence fields for the evidence bundle (schema_version 2):
  - vacuous_pass (True / False / "undetermined"): did the --pre picked-pytest step
    collect 0 items because --picked selected nothing (the defect) vs. because
    -m 'not integration' deselected an all-integration file (expected)?
  - merge_gate_test_coverage ("selected" / "not_selected" / "undetermined"): did the
    merged diff include a test file matching the --pre changed-tests selector?
  - coverage_regression (True / False / "undetermined"): were any test files deleted?

All computations are self-contained (gh-free), working from the pre-fetched CI log and
the local git checkout. HEAD^ must exist (fetch-depth: 2 in ci-rca.yml).
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
_UNDETERMINED = "undetermined"
RANGE_PREV_ENV = "CI_RCA_RANGE_PREV"

# mirrors scripts/validate.py:3871 -- keep in sync
_TEST_FILE_RE = re.compile(r"tests/.*test_[^/]+\.py$")


def parse_vacuous_pass(log_text: str) -> bool | str:
    """Parse pytest collection summary from CI log (tri-state).

    Returns:
      True           -- "0 collected, 0 deselected": --picked selected nothing
                        (the gate-escape defect; vacuous_pass=true)
      False          -- "0 ran because -m 'not integration' deselected all":
                        expected edge case (validate.py:3868)
      "undetermined" -- no parseable pytest collection summary; never silently
                        returns False (fail-loud per Decision 55)
    """
    # "collected N item(s) / M deselected" -- canonical line when -m is active
    desel_match = re.search(
        r"collected\s+(\d+)\s+items?\s*/\s*(\d+)\s+deselected",
        log_text,
        re.IGNORECASE,
    )
    if desel_match:
        if int(desel_match.group(2)) > 0:
            return False  # -m "not integration" deselection -- expected per validate.py:3868

    # "collected N item(s)" without a deselection clause
    col_match = re.search(r"collected\s+(\d+)\s+items?", log_text, re.IGNORECASE)
    if col_match:
        if int(col_match.group(1)) == 0:
            return True  # vacuous pass: 0 collected, no deselection
        return False  # tests actually ran

    # "no tests ran" as a fallback signal
    if re.search(r"no tests ran", log_text, re.IGNORECASE):
        if re.search(r"\bdeselected\b", log_text, re.IGNORECASE):
            return False
        return True

    return _UNDETERMINED


def _run_git_diff(extra_args: list[str]) -> list[str] | str:
    """Run git diff --name-only <base> HEAD and return the file list, or "undetermined" on failure.

    base is CI_RCA_RANGE_PREV when set (a sha, or HEAD^ as ci-rca passes for a non-CI slug); set but empty (an
    unresolved CI range) is "undetermined", never a HEAD^ diff; unset (a local caller) is HEAD^.
    """
    base = os.environ.get(RANGE_PREV_ENV)
    if base is not None and not base.strip():
        return _UNDETERMINED
    cmd = ["git", "diff", "--name-only"] + extra_args + [base.strip() if base else "HEAD^", "HEAD"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=ROOT,
        )
        if result.returncode != 0:
            logger.warning("git diff returned %d: %s", result.returncode, result.stderr.strip())
            return _UNDETERMINED
        return [f.strip() for f in result.stdout.splitlines() if f.strip()]
    except Exception as exc:
        logger.warning("git diff failed: %s", exc)
        return _UNDETERMINED


def merged_diff_files() -> list[str] | str:
    """Return files changed over the range (git diff CI_RCA_RANGE_PREV HEAD; HEAD^ HEAD when unset).

    Returns "undetermined" when HEAD^ is absent or git fails (fetch-depth: 2 required).
    """
    return _run_git_diff([])


def deleted_test_files() -> list[str] | str:
    """Return test files deleted in the merged commit over the range (git diff --diff-filter=D, same base rule).

    Returns "undetermined" when HEAD^ is absent or git fails.
    """
    files = _run_git_diff(["--diff-filter=D"])
    if isinstance(files, str):
        return files
    return [f for f in files if _TEST_FILE_RE.match(f)]


def compute_merge_gate_test_coverage(failed_check: str, merged_files: list[str] | str) -> str:
    """Replay the --pre changed-tests selector (validate.py:3871) on merged_files.

    Returns "selected" if the diff contains a matching test file, "not_selected" for
    source-only diffs, or "undetermined" when merged_files is the undetermined sentinel.
    """
    if merged_files == _UNDETERMINED:
        return _UNDETERMINED
    matching = [f for f in merged_files if _TEST_FILE_RE.match(f)]
    return "selected" if matching else "not_selected"


def compute_coverage_regression(deleted_files: list[str] | str) -> bool | str:
    """True if any deleted files are test files (coverage regression).

    Returns "undetermined" when the deletion diff was unavailable.
    """
    if deleted_files == _UNDETERMINED:
        return _UNDETERMINED
    return len(deleted_files) > 0


def compute_escape_mode(
    vacuous_pass: "bool | str",
    merge_gate_test_coverage: str,
    gate_is_postmerge_canary: "bool | str",
    coverage_regression: "bool | str",
    workflow_tier_raw: str = _UNDETERMINED,
) -> str:
    """Compute the gate-escape mode. Returns one of the escape_mode enum values.

    escape_mode enum: check_ran_vacuously | no_premerge_gate_by_design | tier_misplaced |
                      undetermined

    Decision tree (applied in order -- vacuous-first, not canary-first: only 2 of 22 taxonomy
    workflows carry a real tier, so a canary-first ordering would make escape_mode a restatement
    of actual_gate_that_caught_it and leave check_ran_vacuously structurally unreachable):
      1. If vacuous_pass is True and merge_gate_test_coverage == "selected" ->
         "check_ran_vacuously" (test selected, collected 0 items) -- outranks rule 2 even when
         gate_is_postmerge_canary is also True.
      2. If gate_is_postmerge_canary is True -> "tier_misplaced" (caught only post-merge),
         regardless of whether vacuous_pass or merge_gate_test_coverage is the undetermined
         sentinel -- a canary run is a determinate fact independent of those two inputs.
      3. If workflow_tier_raw == "not_a_gate" -> "no_premerge_gate_by_design", without consulting
         merge_gate_test_coverage -- the workflow was never a gate at all.
      4. If vacuous_pass or merge_gate_test_coverage is "undetermined" -> "undetermined" (abstain).
         Narrowed to these two inputs only: gate_is_postmerge_canary's undetermined case is fully
         handled by rules 2-3 above running first (canary=True wins immediately; canary=anything
         with workflow_tier_raw='not_a_gate' also wins immediately), so this guard need not (and
         must not) re-test canary.
      5. If merge_gate_test_coverage == "not_selected" and gate_is_postmerge_canary is False ->
         "no_premerge_gate_by_design" (the retained c11 proxy). DEFENSIVE DEAD CODE on the live
         taxonomy: reachable only on a taxonomy miss (20 of 22 watched workflows are not_a_gate,
         caught by rule 3; the other 2 are tier CI, caught by rule 2), kept as the honest branch
         if the taxonomy ever gains a non-CI real tier. The `gate_is_postmerge_canary is False`
         precondition is REQUIRED even though this rule's body never references the canary: rule
         2 outranks this rule, so by the time we reach here an undetermined canary has NOT been
         proven false -- answering no_premerge_gate_by_design would be a guess.
      6. Otherwise -> "undetermined"
    """
    if vacuous_pass is True and merge_gate_test_coverage == "selected":
        return "check_ran_vacuously"

    if gate_is_postmerge_canary is True:
        return "tier_misplaced"

    if workflow_tier_raw == "not_a_gate":
        return "no_premerge_gate_by_design"

    if vacuous_pass == _UNDETERMINED or merge_gate_test_coverage == _UNDETERMINED:
        return _UNDETERMINED

    if merge_gate_test_coverage == "not_selected" and gate_is_postmerge_canary is False:
        return "no_premerge_gate_by_design"

    return _UNDETERMINED
