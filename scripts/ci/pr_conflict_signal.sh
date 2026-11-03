#!/usr/bin/env bash
# Body of pr-conflict-signal.yml's poll step, extracted from the workflow (rec-2735) so the logic
# is directly testable and every gh call handles its own exit status explicitly -- a gh failure on
# one PR must never truncate the sweep or be mistaken for a legitimate empty result.
#
# ERREXIT, PRECISELY (verified empirically; see this repo's plan history for the full derivation).
# The workflow step that invokes this script declares no `shell:` key, so GitHub runs the step's
# own run: body as "bash --noprofile --norc -e -o pipefail {0}" -- but that body is now just
# `bash scripts/ci/pr_conflict_signal.sh`, which spawns a CHILD bash process. A child bash does
# NOT inherit its parent's shell options (SHELLOPTS is unexported), so THIS script runs WITHOUT
# inherited errexit: an unhandled gh failure here does not abort the script, it yields an empty
# string that silently falls through to "no wake needed" unless every call site explicitly checks
# its own exit status -- which is the fix below. `set +e` is retained near the top purely as
# defence-in-depth against a future re-inlining of this body back into the workflow step (where -e
# WOULD apply); it is not the load-bearing mechanism.
#
# Contract with pr-conflict-signal.yml: cwd is the repo root (actions/checkout precedes this
# step); GH_TOKEN / GH_REPO are set in the step's env: block for the gh CLI to read.

set -uo pipefail
set +e

_MERGEABLE_POLL_ATTEMPTS=5
_MERGEABLE_POLL_SLEEP="${PR_CONFLICT_SIGNAL_POLL_SLEEP:-5}"
_GH_RETRY_ATTEMPTS=3
_GH_RETRY_SLEEP="${PR_CONFLICT_SIGNAL_RETRY_SLEEP:-5}"

# _WAKE_HEAD_PREFIXES: the declared branch-prefix universe this sweep enumerates. See
# docs/contracts/git-ops.yaml branching_topology.agent_branch_prefixes, which must name the same
# set (validate_pr_conflict_signal cross-checks this declaration structurally; a sibling contract
# assertion cross-checks the two files against each other). Decision 191 discharge: prefix
# matching is retained because it is the only discriminator the producer stamps today -- the tally
# below is the substitute for an enumerated universe, and is what would have caught the outage
# that motivated this declaration on the first push after a harness prefix rename.
_WAKE_HEAD_PREFIXES=("agent/" "claude/" "ccr-")
# Prefixes inside _WAKE_HEAD_PREFIXES that are retirement candidates: one matching ZERO open PRs
# this run gets a GITHUB_STEP_SUMMARY notice (observability only, never fails the job). Must stay
# a subset of _WAKE_HEAD_PREFIXES. Reversal condition: see
# docs/contracts/git-ops.yaml branching_topology.agent_branch_prefixes_reversal.
_WAKE_HEAD_PREFIXES_TRANSITIONAL=("claude/")

_FAILURE_COUNT=0

# Decision 155 marker shape (mirrors .github/actions/subagent-plan-review/review.sh's
# _starved_marker): distinct, greppable, and mirrored to $GITHUB_STEP_SUMMARY when set, so a
# per-PR failure is operator-observable instead of buried in a per-run job log. Never changes
# control flow -- pure observability plus the counter the final exit code reads.
_signal_failure() {
  local reason="$1"
  _FAILURE_COUNT=$((_FAILURE_COUNT + 1))
  local msg="[PR-CONFLICT-SIGNAL] FAILURE: ${reason}"
  echo "$msg" >&2
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    printf '\n## pr-conflict-signal FAILURE\n\n%s\n' "$msg" >> "$GITHUB_STEP_SUMMARY"
  fi
}

# Bounded retry shared by every retried gh call site (the mergeable poll, the comments read, and
# the comment post) so retry policy and exit-status handling live in exactly one place -- an
# unhandled call is what created rec-2735's defect class. retry_on_value: a value that, when the
# call SUCCEEDS but its captured stdout equals it, is ALSO retried (the mergeable poll passes
# "UNKNOWN"); pass "" to retry on call failure only. Prints the last observed stdout (possibly
# empty or stale) and returns 0 only when the final attempt both succeeded AND (retry_on_value is
# "" or stdout != retry_on_value) -- so a caller can always distinguish "call failed" from
# "call succeeded but returned the retry sentinel" by inspecting the printed value.
_gh_bounded_retry() {
  local max_attempts="$1" sleep_secs="$2" retry_on_value="$3"
  shift 3
  local attempt out rc
  for ((attempt = 1; attempt <= max_attempts; attempt++)); do
    out=$("$@")
    rc=$?
    if [ "$rc" -eq 0 ] && { [ -z "$retry_on_value" ] || [ "$out" != "$retry_on_value" ]; }; then
      printf '%s' "$out"
      return 0
    fi
    if [ "$attempt" -lt "$max_attempts" ]; then
      sleep "$sleep_secs"
    fi
  done
  printf '%s' "$out"
  return 1
}

prs=$(gh pr list --base main --state open --limit 200 --json number,headRefName,headRefOid \
  --jq '.[] | [.number, .headRefName, .headRefOid] | @tsv')
prs_rc=$?

if [ "$prs_rc" -ne 0 ]; then
  _signal_failure "gh pr list failed (exit $prs_rc); cannot enumerate open PRs -- sweep cannot proceed this run."
elif [ -z "$prs" ]; then
  echo "No open PRs against main."
else
  declare -A _matched_counts=()
  for prefix in "${_WAKE_HEAD_PREFIXES[@]}"; do
    _matched_counts["$prefix"]=0
  done
  _unmatched_heads=()

  while IFS=$'\t' read -r number head_ref head_sha; do
    [ -z "$number" ] && continue

    matched_prefix=""
    for prefix in "${_WAKE_HEAD_PREFIXES[@]}"; do
      case "$head_ref" in
        "$prefix"*)
          matched_prefix="$prefix"
          break
          ;;
      esac
    done

    if [ -z "$matched_prefix" ]; then
      _unmatched_heads+=("$head_ref")
      continue
    fi
    _matched_counts["$matched_prefix"]=$((_matched_counts["$matched_prefix"] + 1))

    mergeable=$(_gh_bounded_retry "$_MERGEABLE_POLL_ATTEMPTS" "$_MERGEABLE_POLL_SLEEP" "UNKNOWN" \
      gh pr view "$number" --json mergeable --jq '.mergeable')
    mergeable_rc=$?

    if [ "$mergeable_rc" -ne 0 ]; then
      if [ "$mergeable" = "UNKNOWN" ]; then
        echo "PR #$number: mergeability still UNKNOWN after poll bound, skipping (never false-wake on UNKNOWN)."
      else
        _signal_failure "PR #$number: gh pr view --json mergeable failed after $_MERGEABLE_POLL_ATTEMPTS attempts; skipping this PR (sweep continues)."
      fi
      continue
    fi

    if [ "$mergeable" != "CONFLICTING" ]; then
      echo "PR #$number: mergeable=$mergeable, no wake needed."
      continue
    fi

    marker="<!-- conflict-wake:${head_sha} -->"
    comments=$(_gh_bounded_retry "$_GH_RETRY_ATTEMPTS" "$_GH_RETRY_SLEEP" "" \
      gh pr view "$number" --json comments --jq '.comments[].body')
    comments_rc=$?

    if [ "$comments_rc" -ne 0 ]; then
      _signal_failure "PR #$number: gh pr view --json comments failed after $_GH_RETRY_ATTEMPTS attempts; cannot verify the idempotency marker, posting the wake anyway (a duplicate wake is cheap; a missed wake strands a session)."
      comments=""
    fi

    existing=$(printf '%s\n' "$comments" | grep -F -- "$marker" || true)
    if [ -n "$existing" ]; then
      echo "PR #$number: wake comment for head $head_sha already posted, skipping (idempotent)."
      continue
    fi

    echo "PR #$number: CONFLICTING at head $head_sha, posting wake comment."
    comment_body="$marker
Merge conflict: this PR now conflicts with main and must be rebased/resolved before it can merge. Automated wake signal for a watching session; no action needed until you pick this back up."
    _gh_bounded_retry "$_GH_RETRY_ATTEMPTS" "$_GH_RETRY_SLEEP" "" \
      gh pr comment "$number" --body "$comment_body" > /dev/null
    comment_rc=$?

    if [ "$comment_rc" -ne 0 ]; then
      _signal_failure "PR #$number: gh pr comment failed after $_GH_RETRY_ATTEMPTS attempts; wake comment NOT posted for a CONFLICTING PR at head $head_sha."
    fi
  done <<< "$prs"

  _tally=""
  for prefix in "${_WAKE_HEAD_PREFIXES[@]}"; do
    _tally="${_tally}${prefix}=${_matched_counts[$prefix]} "
  done
  echo "[PR-CONFLICT-SIGNAL] prefix tally: ${_tally}unmatched=${#_unmatched_heads[@]}"

  if [ "${#_unmatched_heads[@]}" -gt 0 ]; then
    _unmatched_list=$(printf '%s, ' "${_unmatched_heads[@]}")
    _unmatched_list="${_unmatched_list%, }"
    echo "[PR-CONFLICT-SIGNAL] unmatched heads (skipped, no declared prefix matched): ${_unmatched_list}"
    if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
      printf '\n## pr-conflict-signal unmatched heads\n\nSkipped -- no declared prefix matched: %s\n' \
        "$_unmatched_list" >> "$GITHUB_STEP_SUMMARY"
    fi
  fi

  for prefix in "${_WAKE_HEAD_PREFIXES_TRANSITIONAL[@]}"; do
    if [ "${_matched_counts[$prefix]:-0}" -eq 0 ]; then
      echo "[PR-CONFLICT-SIGNAL] transitional prefix '$prefix' matched zero open PRs this run -- retirement candidate."
      if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
        printf '\n## pr-conflict-signal transitional prefix retirement notice\n\nTransitional prefix `%s` matched zero open PRs this run and is a candidate for retirement from the declared set.\n' \
          "$prefix" >> "$GITHUB_STEP_SUMMARY"
      fi
    fi
  done
fi

if [ "$_FAILURE_COUNT" -gt 0 ]; then
  echo "[PR-CONFLICT-SIGNAL] completed with $_FAILURE_COUNT failure(s); see markers above." >&2
  exit 1
fi
exit 0
