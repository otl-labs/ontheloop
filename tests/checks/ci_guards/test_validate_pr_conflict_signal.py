"""Tests for validate_pr_conflict_signal() -- pr-conflict-signal.yml structural guard."""

import copy
from pathlib import Path
from unittest.mock import patch

from scripts.checks.ci_guards.validate_pr_conflict_signal import (
    _join_continuations,
    _unguarded_gh_call_sites,
    validate_pr_conflict_signal,
)

_MODULE = "scripts.checks.ci_guards.validate_pr_conflict_signal"
_SCRIPT_REL_PATH = "scripts/ci/pr_conflict_signal.sh"

_CHECKOUT_STEP = {"uses": "actions/checkout@v7"}
_DELEGATION_STEP = {"continue-on-error": True, "run": f"bash {_SCRIPT_REL_PATH}"}


def _workflow_with_steps(steps: list[dict]) -> dict:
    return {
        "on": {"push": {"branches": ["main"]}, "workflow_dispatch": {}},
        "jobs": {
            "detect-conflicts": {
                "permissions": {"contents": "read", "pull-requests": "write"},
                "steps": steps,
            }
        },
    }


_VALID_WORKFLOW = _workflow_with_steps([_CHECKOUT_STEP, _DELEGATION_STEP])

# A minimal, self-consistent delegate script carrying every marker the guard's semantic
# assertions look for. Every fail-path fixture below mutates exactly ONE property of this base so
# each test isolates exactly one guard assertion (never two failures at once).
_VALID_SCRIPT = """\
#!/usr/bin/env bash
set -uo pipefail
set +e

_WAKE_HEAD_PREFIXES=("agent/" "claude/")
_WAKE_HEAD_PREFIXES_TRANSITIONAL=("claude/")

_gh_bounded_retry() {
  local max="$1" sleep_s="$2" retry_val="$3"
  shift 3
  "$@"
}

prs=$(gh pr list --base main --state open --limit 200 --json number,headRefName,headRefOid \\
  --jq '.[] | [.number, .headRefName, .headRefOid] | @tsv')
prs_rc=$?

for prefix in "${_WAKE_HEAD_PREFIXES[@]}"; do
  case "$2" in
    "$prefix"*) : ;;
  esac
done

mergeable=$(_gh_bounded_retry 5 5 "UNKNOWN" gh pr view "$1" --json mergeable --jq '.mergeable')
mergeable_rc=$?

if [ "$mergeable" = "CONFLICTING" ]; then
  marker="<!-- conflict-wake:$2 -->"
  comments=$(_gh_bounded_retry 3 5 "" gh pr view "$1" --json comments --jq '.comments[].body')
  comments_rc=$?

  _gh_bounded_retry 3 5 "" gh pr comment "$1" --body "$marker" > /dev/null
  comment_rc=$?
fi
"""


def _write_script(tmp_path: Path, content: str, rel_path: str = _SCRIPT_REL_PATH) -> None:
    full_path = tmp_path / rel_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")


class TestValidatePrConflictSignalPassPath:
    """Pass-path: the real workflow file, and a valid mocked _load, both leave failed empty."""

    def test_passes_against_real_workflow_file(self) -> None:
        failed: list[str] = []
        validate_pr_conflict_signal(failed)
        assert failed == []

    def test_passes_with_well_formed_mocked_data(self) -> None:
        with patch(f"{_MODULE}._load", return_value=_VALID_WORKFLOW):
            failed: list[str] = []
            validate_pr_conflict_signal(failed)
        assert failed == []

    def test_passes_with_mocked_data_and_isolated_script(self, tmp_path: Path) -> None:
        """Fully isolated from the real repo script -- pins the minimal valid shape on its own."""
        _write_script(tmp_path, _VALID_SCRIPT)
        with patch(f"{_MODULE}._load", return_value=_VALID_WORKFLOW), patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_pr_conflict_signal(failed)
        assert failed == []


class TestValidatePrConflictSignalFailPath:
    """Fail-path: mocked data missing each asserted property appends a distinct failure."""

    def test_load_failure_records_failure_no_propagation(self) -> None:
        with patch(f"{_MODULE}._load", side_effect=OSError("no such file")):
            failed: list[str] = []
            validate_pr_conflict_signal(failed)
        assert len(failed) == 1
        assert "unreadable" in failed[0]

    def test_wrong_push_branches_fails(self) -> None:
        data = copy.deepcopy(_VALID_WORKFLOW)
        data["on"]["push"]["branches"] = ["main", "develop"]
        with patch(f"{_MODULE}._load", return_value=data):
            failed: list[str] = []
            validate_pr_conflict_signal(failed)
        assert any("push trigger" in f for f in failed)

    def test_missing_workflow_dispatch_fails(self) -> None:
        data = copy.deepcopy(_VALID_WORKFLOW)
        del data["on"]["workflow_dispatch"]
        with patch(f"{_MODULE}._load", return_value=data):
            failed: list[str] = []
            validate_pr_conflict_signal(failed)
        assert any("workflow_dispatch" in f for f in failed)

    def test_no_jobs_fails(self) -> None:
        data = copy.deepcopy(_VALID_WORKFLOW)
        data["jobs"] = {}
        with patch(f"{_MODULE}._load", return_value=data):
            failed: list[str] = []
            validate_pr_conflict_signal(failed)
        assert any("no jobs defined" in f for f in failed)

    def test_missing_pull_requests_write_permission_fails(self) -> None:
        data = copy.deepcopy(_VALID_WORKFLOW)
        data["jobs"]["detect-conflicts"]["permissions"] = {"contents": "read"}
        with patch(f"{_MODULE}._load", return_value=data):
            failed: list[str] = []
            validate_pr_conflict_signal(failed)
        assert any("pull-requests: write" in f for f in failed)

    def test_missing_continue_on_error_fails(self) -> None:
        data = copy.deepcopy(_VALID_WORKFLOW)
        data["jobs"]["detect-conflicts"]["steps"][1]["continue-on-error"] = False
        with patch(f"{_MODULE}._load", return_value=data):
            failed: list[str] = []
            validate_pr_conflict_signal(failed)
        assert any("continue-on-error" in f for f in failed)

    # --- New assertions: delegate resolution (VP4 -k selector: "delegate") ---

    def test_unresolvable_delegate_fails(self) -> None:
        data = _workflow_with_steps([_CHECKOUT_STEP, {"continue-on-error": True, "run": "echo hello"}])
        with patch(f"{_MODULE}._load", return_value=data):
            failed: list[str] = []
            validate_pr_conflict_signal(failed)
        assert any("could not resolve delegate script" in f for f in failed)

    def test_delegate_script_missing_on_disk_fails(self, tmp_path: Path) -> None:
        data = _workflow_with_steps([_CHECKOUT_STEP, {"continue-on-error": True, "run": "bash scripts/ci/does_not_exist.sh"}])
        with patch(f"{_MODULE}._load", return_value=data), patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_pr_conflict_signal(failed)
        assert any("delegate script missing on disk" in f for f in failed)

    # --- New assertion: checkout precedes delegation (VP4 -k selector: "checkout_order") ---

    def test_checkout_order_missing_fails(self, tmp_path: Path) -> None:
        _write_script(tmp_path, _VALID_SCRIPT)
        data = _workflow_with_steps([_DELEGATION_STEP])
        with patch(f"{_MODULE}._load", return_value=data), patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_pr_conflict_signal(failed)
        assert any("checkout does not precede delegation" in f for f in failed)

    def test_checkout_order_after_delegation_fails(self, tmp_path: Path) -> None:
        _write_script(tmp_path, _VALID_SCRIPT)
        data = _workflow_with_steps([_DELEGATION_STEP, _CHECKOUT_STEP])
        with patch(f"{_MODULE}._load", return_value=data), patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_pr_conflict_signal(failed)
        assert any("checkout does not precede delegation" in f for f in failed)

    # --- New assertion: errexit clearing (VP4 -k selector: "errexit") ---

    def test_errexit_not_cleared_fails(self, tmp_path: Path) -> None:
        _write_script(tmp_path, _VALID_SCRIPT.replace("set +e\n", ""))
        with patch(f"{_MODULE}._load", return_value=_VALID_WORKFLOW), patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_pr_conflict_signal(failed)
        assert any("does not clear errexit" in f for f in failed)

    # --- New assertion: every gh call site handles its exit status (VP4 -k selector: "exit_status") ---

    def test_gh_call_missing_exit_status_handling_fails(self, tmp_path: Path) -> None:
        _write_script(tmp_path, _VALID_SCRIPT + '\ngh pr comment "$1" --body "oops"\n')
        with patch(f"{_MODULE}._load", return_value=_VALID_WORKFLOW), patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_pr_conflict_signal(failed)
        assert any("missing explicit exit-status handling" in f for f in failed)

    # --- Semantic marker assertions, now sourced from the delegate script's own content ---

    def test_missing_mergeable_poll_fails(self, tmp_path: Path) -> None:
        _write_script(tmp_path, _VALID_SCRIPT.replace("mergeable", "mrg"))
        with patch(f"{_MODULE}._load", return_value=_VALID_WORKFLOW), patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_pr_conflict_signal(failed)
        assert any("mergeable poll" in f for f in failed)

    def test_missing_unknown_skip_fails(self, tmp_path: Path) -> None:
        _write_script(tmp_path, _VALID_SCRIPT.replace("UNKNOWN", "PENDING"))
        with patch(f"{_MODULE}._load", return_value=_VALID_WORKFLOW), patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_pr_conflict_signal(failed)
        assert any("UNKNOWN-skip handling" in f for f in failed)

    def test_missing_conflicting_gate_fails(self, tmp_path: Path) -> None:
        _write_script(tmp_path, _VALID_SCRIPT.replace("CONFLICTING", "CLASHING"))
        with patch(f"{_MODULE}._load", return_value=_VALID_WORKFLOW), patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_pr_conflict_signal(failed)
        assert any("CONFLICTING-only comment gate" in f for f in failed)

    def test_missing_dedup_marker_fails(self, tmp_path: Path) -> None:
        _write_script(tmp_path, _VALID_SCRIPT.replace("conflict-wake:", "conflict-seen:"))
        with patch(f"{_MODULE}._load", return_value=_VALID_WORKFLOW), patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_pr_conflict_signal(failed)
        assert any("head-SHA dedup marker" in f for f in failed)


class TestBranchPrefixDeclaration:
    """The declaration replacing the old existence-only claude/* filter row (VP2 -k selector:
    "declaration"): absent, malformed, unconsumed, or prose-only must all FAIL, and none of them
    pass on prose alone -- the shape the retired existence-only check could never distinguish."""

    def _run(self, tmp_path: Path, script: str) -> list[str]:
        _write_script(tmp_path, script)
        with patch(f"{_MODULE}._load", return_value=_VALID_WORKFLOW), patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_pr_conflict_signal(failed)
        return failed

    def test_declaration_absent_fails(self, tmp_path: Path) -> None:
        script = _VALID_SCRIPT.replace('_WAKE_HEAD_PREFIXES=("agent/" "claude/")\n', "")
        failed = self._run(tmp_path, script)
        assert any("branch-prefix declaration absent" in f for f in failed)

    def test_declaration_prose_only_fails(self, tmp_path: Path) -> None:
        """The case the retired existence-only check could not catch: the declaration line is
        gone, but a log/comment string still mentions a prefix literal in prose."""
        script = _VALID_SCRIPT.replace(
            '_WAKE_HEAD_PREFIXES=("agent/" "claude/")\n',
            "# handles agent/ and claude/ branches\n",
        )
        assert "agent/" in script and "claude/" in script  # prose survives the removal
        failed = self._run(tmp_path, script)
        assert any("branch-prefix declaration absent" in f for f in failed)

    def test_declaration_empty_fails(self, tmp_path: Path) -> None:
        script = _VALID_SCRIPT.replace('_WAKE_HEAD_PREFIXES=("agent/" "claude/")', "_WAKE_HEAD_PREFIXES=()")
        failed = self._run(tmp_path, script)
        assert any("branch-prefix declaration empty" in f for f in failed)

    def test_declaration_malformed_missing_slash_fails(self, tmp_path: Path) -> None:
        script = _VALID_SCRIPT.replace('_WAKE_HEAD_PREFIXES=("agent/" "claude/")', '_WAKE_HEAD_PREFIXES=("agent" "claude/")')
        failed = self._run(tmp_path, script)
        assert any("missing trailing /" in f for f in failed)

    def test_declaration_hyphen_terminated_token_passes(self, tmp_path: Path) -> None:
        script = _VALID_SCRIPT.replace(
            '_WAKE_HEAD_PREFIXES=("agent/" "claude/")', '_WAKE_HEAD_PREFIXES=("agent/" "claude/" "ccr-")'
        )
        failed = self._run(tmp_path, script)
        assert not any("branch-prefix declaration" in f for f in failed)
        validator_source = Path(_MODULE.replace(".", "/") + ".py").read_text(encoding="utf-8")
        assert "ccr-" not in validator_source

    def test_declaration_bare_harness_token_fails(self, tmp_path: Path) -> None:
        script = _VALID_SCRIPT.replace('_WAKE_HEAD_PREFIXES=("agent/" "claude/")', '_WAKE_HEAD_PREFIXES=("agent/" "ccr")')
        failed = self._run(tmp_path, script)
        assert any("missing trailing" in f for f in failed)

    def test_declaration_duplicate_tokens_fails(self, tmp_path: Path) -> None:
        script = _VALID_SCRIPT.replace('_WAKE_HEAD_PREFIXES=("agent/" "claude/")', '_WAKE_HEAD_PREFIXES=("agent/" "agent/")')
        failed = self._run(tmp_path, script)
        assert any("duplicate tokens" in f for f in failed)

    def test_declaration_unconsumed_fails(self, tmp_path: Path) -> None:
        script = _VALID_SCRIPT.replace(
            'for prefix in "${_WAKE_HEAD_PREFIXES[@]}"; do\n  case "$2" in\n    "$prefix"*) : ;;\n  esac\ndone\n',
            "",
        )
        assert "_WAKE_HEAD_PREFIXES[@]" not in script
        failed = self._run(tmp_path, script)
        assert any("branch-prefix declaration is unconsumed" in f for f in failed)

    def test_transitional_declaration_absent_fails(self, tmp_path: Path) -> None:
        script = _VALID_SCRIPT.replace('_WAKE_HEAD_PREFIXES_TRANSITIONAL=("claude/")\n', "")
        failed = self._run(tmp_path, script)
        assert any("transitional branch-prefix declaration absent" in f for f in failed)

    def test_transitional_declaration_not_subset_fails(self, tmp_path: Path) -> None:
        script = _VALID_SCRIPT.replace(
            '_WAKE_HEAD_PREFIXES_TRANSITIONAL=("claude/")', '_WAKE_HEAD_PREFIXES_TRANSITIONAL=("other/")'
        )
        failed = self._run(tmp_path, script)
        assert any("not a subset of" in f for f in failed)

    def test_declaration_well_formed_passes(self, tmp_path: Path) -> None:
        """Pass-path pin, isolated from the real repo script (mirrors the class-level pass tests
        above): the valid fixture's declaration alone leaves failed empty."""
        failed = self._run(tmp_path, _VALID_SCRIPT)
        assert failed == []


class TestGhListLimit:
    """The gh pr list call site must carry an explicit --limit (VP step 4's cross-check target)."""

    def test_missing_limit_fails(self, tmp_path: Path) -> None:
        script = _VALID_SCRIPT.replace("--limit 200 ", "")
        _write_script(tmp_path, script)
        with patch(f"{_MODULE}._load", return_value=_VALID_WORKFLOW), patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_pr_conflict_signal(failed)
        assert any("gh pr list call site missing --limit" in f for f in failed)

    def test_no_call_site_found_fails(self, tmp_path: Path) -> None:
        """No `gh pr list` call site at all (the enumeration was replaced with something else
        entirely) -- distinct from the present-but-unlimited case above."""
        script = _VALID_SCRIPT.replace("gh pr list", "gh api repos/example/pulls")
        _write_script(tmp_path, script)
        with patch(f"{_MODULE}._load", return_value=_VALID_WORKFLOW), patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_pr_conflict_signal(failed)
        assert any("gh pr list call site not found" in f for f in failed)


class TestJoinContinuations:
    """_join_continuations() -- the backslash-continuation joiner every gh-call-site scan runs
    against first."""

    def test_dangling_trailing_continuation_is_not_dropped(self) -> None:
        """A final physical line ending in `\\` with nothing to join onto still surfaces its
        buffered content, rather than silently losing it."""
        assert _join_continuations("a=1 \\") == ["a=1"]

    def test_two_line_continuation_joins_into_one_logical_line(self) -> None:
        assert _join_continuations("a=$(cmd \\\n  --flag)") == ["a=$(cmd --flag)"]


class TestUnguardedGhCallSitesRetryHelperRouting:
    """_unguarded_gh_call_sites() -- a gh call passed AS AN ARGUMENT to the shared retry helper
    (so "gh" never sits at line-start or immediately after "$(") must still be recognised as a
    call site and exempted via the helper, not silently invisible to the scan."""

    def test_retry_helper_wrapped_call_is_recognised_and_exempted(self) -> None:
        line = 'mergeable=$(_gh_bounded_retry 5 5 "UNKNOWN" gh pr view "$1" --json mergeable --jq \'.mergeable\')'
        assert _unguarded_gh_call_sites(line) == []

    def test_bare_retry_helper_statement_call_is_exempted(self) -> None:
        line = '_gh_bounded_retry 3 5 "" gh pr comment "$1" --body "$marker" > /dev/null'
        assert _unguarded_gh_call_sites(line) == []
