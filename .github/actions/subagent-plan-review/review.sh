#!/usr/bin/env bash
# Body of the subagent-plan-review composite action, extracted from action.yml (rec-2924) so the
# logic is shellcheckable and directly testable. tests/test_subagent_review_wiring.py drives this
# file under the EXACT invocation GitHub uses for a composite `shell: bash` step --
# `bash --noprofile --norc -e -o pipefail <file>` -- because the inherited -e is the whole bug.
#
# Contract with action.yml (all supplied via the step's env: block):
#   CLAUDE_CODE_OAUTH_TOKEN  caller-passed secret (composite actions cannot read secrets.* directly)
#   CONTEXT_DESCRIPTION      plan-under-review context phrase, interpolated into the reviewer prompt
#   WORKSPACE_ROOT           ${{ github.workspace }} -- repo root for scripts/ lookups
#   GITHUB_OUTPUT            step-output file (GitHub-provided)
# cwd is the terraform root module directory containing plan.json (step `working-directory`).
#
# Emits exactly one outcome=proceed|revise|starved, preserving the STARVED-no-latch anti-masking
# (Decision 55) -- never an implicit PROCEED or REVISE on starvation.

set -uo pipefail

# GitHub runs composite `shell: bash` step bodies as
#     bash --noprofile --norc -e -o pipefail {0}
# so errexit is INHERITED here, and the `set -uo pipefail` above does NOT clear it -- `set` only
# touches the options it names. This script is DESIGNED for a no-errexit world: every failure path
# below writes an explicit outcome and exits deliberately, and scripts/ci/review_verdict.py exits
# 0/2/1 BY CONTRACT for PROCEED/REVISE/STARVED (see its main() docstring; proven by
# tests/test_review_verdict.py). Under inherited -e the `VERDICT=$(... review_verdict.py ...)`
# assignment aborts the shell the instant the classifier reports REVISE or STARVED, so the
# same-budget retry and the outcome-writing case block below become dead code, the caller reads an
# EMPTY outputs.outcome, misses its `== 'starved'` anti-masking carve-out, and latches a FALSE RED
# convergence record for a run in which terraform apply never executed (rec-2924). Clear it.
set +e

# The output channel is TOTAL by construction: the fail-closed anti-masking value is written FIRST,
# so an outcome is defined on EVERY exit path -- including an unexpected abort that never reaches
# the case block below. GITHUB_OUTPUT is last-write-wins, so each deliberate write further down
# overrides this pre-write. The property whose absence produced the false red is "an outcome is
# defined on every exit path"; it is encoded structurally here rather than left to control flow
# reaching the right branch.
echo "outcome=starved" >> "$GITHUB_OUTPUT"

# Decision 155 marker shape (mirrors the DCG-03 orphan-guard skip marker): distinct, greppable,
# and mirrored to $GITHUB_STEP_SUMMARY when set, so an accumulating STARVED rate is
# operator-observable rather than buried in a per-run job log. Never changes the outcome or exit
# status this script has already decided -- pure observability.
_starved_marker() {
  local reason="$1"
  local msg="[SUBAGENT-REVIEW] STARVED: ${reason}"
  echo "$msg" >&2
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    printf '\n## Subagent review STARVED\n\n%s\n' "$msg" >> "$GITHUB_STEP_SUMMARY"
  fi
}

if [ -z "${CLAUDE_CODE_OAUTH_TOKEN}" ]; then
  _starved_marker "no CLAUDE_CODE_OAUTH_TOKEN available; failing closed before any reviewer invocation."
  echo "outcome=starved" >> "$GITHUB_OUTPUT"
  exit 1
fi
if ! command -v claude &> /dev/null; then
  sudo npm install -g @anthropic-ai/claude-code@2.1.148 --omit=dev --omit=optional && sudo npm cache clean --force
fi

CONTEXT_RE='^[A-Za-z0-9 ().,:_/-]{1,300}$'
if [[ ! "${CONTEXT_DESCRIPTION:-}" =~ $CONTEXT_RE ]]; then
  _starved_marker "CONTEXT_DESCRIPTION is empty, too long or has characters outside [A-Za-z0-9 ().,:_/-]; failing closed before any reviewer invocation."
  echo "outcome=starved" >> "$GITHUB_OUTPUT"
  exit 1
fi

python3 "${WORKSPACE_ROOT}/scripts/ci/terraform_plan_digest.py" --literals-from . plan.json > plan_digest.txt
DIGEST_RC=$?
if [ "$DIGEST_RC" -eq 3 ]; then
  _starved_marker "digest overflow: actionable content exceeds the reviewer digest cap; apply blocked, convergence record NOT overwritten (Decision 55). Split the change or raise DIGEST_SIZE_CAP by PR; after a PR touching only scripts/ci/, drain with a blank workflow_dispatch of terraform-apply-sandbox."
  echo "outcome=starved" >> "$GITHUB_OUTPUT"
  exit 1
elif [ "$DIGEST_RC" -ne 0 ]; then
  _starved_marker "plan digest build failed (infra failure, not a plan rejection)."
  echo "outcome=starved" >> "$GITHUB_OUTPUT"
  exit 1
fi

# The reviewer runs with no tools, from an empty directory outside the checkout (so it loads no
# CLAUDE.md, .claude/settings.json hooks or .mcp.json), under a curated environment (no AWS_ keys).
STEP_DIR=$(pwd)
REVIEW_CWD=$(mktemp -d)
trap 'rm -rf "$REVIEW_CWD"' EXIT
case "$(cd "$REVIEW_CWD" && pwd -P)/" in
  "$(cd "$WORKSPACE_ROOT" && pwd -P)/"*)
    _starved_marker "reviewer scratch directory is inside the checkout; failing closed."
    echo "outcome=starved" >> "$GITHUB_OUTPUT"
    exit 1
    ;;
esac

MAX_TURNS=5
PROMPT="Review the terraform plan digest below for ${CONTEXT_DESCRIPTION}. It has already passed the deterministic guard (no destroy, no out-of-budget IAM/trust change; may include in-budget IAM inline-policy or attachment UPDATEs on managed boundary-carrying CI roles per T2.25). The digest is names-only by design: it lists addresses, types, actions and changed attribute names, and for IAM policy statements, Lambda permissions and function URLs it shows a value only when that value is a literal in the public Terraform source (anything else reads <non-literal>). You have no tools and no file access. Judge whether the shape of the change is a safe, expected sandbox infrastructure change. Reply with exactly the single token PROCEED on its own line if safe to auto-apply, or REVISE followed by the reason if not."

_review() {
  local out="$1"
  local -a keep=(PATH="$PATH" HOME="$HOME" CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_CODE_OAUTH_TOKEN")
  [ -n "${CLAUDE_P_RETRY_BACKOFF_BASE:-}" ] && keep+=(CLAUDE_P_RETRY_BACKOFF_BASE="$CLAUDE_P_RETRY_BACKOFF_BASE")
  [ -n "${TMPDIR:-}" ] && keep+=(TMPDIR="$TMPDIR")
  [ -n "${LANG:-}" ] && keep+=(LANG="$LANG")
  (
    cd "$REVIEW_CWD" || exit 1
    env -i "${keep[@]}" "${WORKSPACE_ROOT}/scripts/ci/claude_p_retry.sh" "${STEP_DIR}/${out}" -- \
      --output-format json --tools "" --strict-mcp-config --max-turns "$MAX_TURNS" "$PROMPT" < "${STEP_DIR}/plan_digest.txt"
  ) || true
}

_stats() {
  python3 - "$1" >&2 <<'PYEOF' || echo "[SUBAGENT-REVIEW] stats num_turns=unknown permission_denials=unknown" >&2
import json, sys

turns = denials = "unknown"
try:
    text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
except OSError:
    text = ""
decoder = json.JSONDecoder()
last = None
pos = text.find("{")
while pos != -1:
    try:
        obj, end = decoder.raw_decode(text, pos)
    except ValueError:
        pos = text.find("{", pos + 1)
        continue
    if isinstance(obj, dict) and obj.get("type") == "result":
        last = obj
    pos = text.find("{", end)
if last is not None:
    if isinstance(last.get("num_turns"), int):
        turns = last["num_turns"]
    if isinstance(last.get("permission_denials"), list):
        denials = len(last["permission_denials"])
print(f"[SUBAGENT-REVIEW] stats num_turns={turns} permission_denials={denials}")
PYEOF
}

_review review.json
_stats review.json
VERDICT_FILE=review.json
VERDICT=$(python3 "${WORKSPACE_ROOT}/scripts/ci/review_verdict.py" review.json)

if [ "$VERDICT" = "STARVED" ]; then
  echo "Reviewer STARVED on first attempt; retrying once at the SAME turn budget (${MAX_TURNS} -- never larger, T2.39 c2)." >&2
  _review review_retry.json
  _stats review_retry.json
  VERDICT_FILE=review_retry.json
  VERDICT=$(python3 "${WORKSPACE_ROOT}/scripts/ci/review_verdict.py" review_retry.json)
fi

echo "Reviewer verdict: ${VERDICT}"
case "$VERDICT" in
  PROCEED)
    echo "outcome=proceed" >> "$GITHUB_OUTPUT"
    exit 0
    ;;
  REVISE)
    echo "Subagent returned REVISE; failing closed." >&2
    REASON=$(python3 "${WORKSPACE_ROOT}/scripts/ci/review_verdict.py" --reason "$VERDICT_FILE" 2> /dev/null | tr '\r\n' '  ') || true
    [ -n "${REASON// /}" ] || REASON="(no reason given)"
    echo "[SUBAGENT-REVIEW] REVISE reason: ${REASON}" >&2
    if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
      printf '\n## Subagent review REVISE\n\n`[SUBAGENT-REVIEW] REVISE reason: %s`\n' "$(printf '%s' "$REASON" | tr '`' "'")" >> "$GITHUB_STEP_SUMMARY"
    fi
    echo "outcome=revise" >> "$GITHUB_OUTPUT"
    exit 1
    ;;
  *)
    echo "Subagent STARVED (max-turns/no-verdict/API-exhausted) after the same-budget retry; blocking apply WITHOUT latching the convergence record red." >&2
    _starved_marker "max-turns/no-verdict/API-exhausted after the same-budget retry (T2.39 c2); apply blocked, convergence record NOT overwritten (Decision 55 anti-masking)."
    echo "outcome=starved" >> "$GITHUB_OUTPUT"
    exit 1
    ;;
esac
