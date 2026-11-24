#!/usr/bin/env bash
# CI RCA step bodies moved out of .github/workflows/ci-rca.yml (rec-5500, Decision 162 cl.3).
# Subcommands: prompt [BUNDLES_DIR], check [OUTPUT], mark [OUTPUT] [FALLBACK_FILED].
# -e only: the runner runs these steps with its default `bash -e`, so pipefail and nounset stay off as they were inline.
set -e

prompt() {
  local BUNDLES="${1:-/tmp/ci-rca-bundles}"
  EVIDENCE_CONTEXT=""
  if ls "$BUNDLES"/*.json 2>/dev/null >/dev/null 2>&1; then
    EVIDENCE_CONTEXT=" Evidence bundle(s) are at ${BUNDLES}/ -- bundle files (one JSON per failed check): $(ls "$BUNDLES"/*.json | tr '\n' ' '). Primary bundle SHA: ${BUNDLE_SHA}. Primary S3 URI: ${BUNDLE_S3_URI}. Pending path: ${BUNDLE_PATH}. Local bundle path: ${BUNDLE_LOCAL}."
  fi
  if [ "$BUNDLE_ABSENT" = "true" ]; then
    EVIDENCE_CONTEXT=" No evidence bundle was generated for this run (bundle_absent=true) -- you have zero deterministic evidence. You MUST set detection_gap.earliest_viable_gate='undetermined', rca_confidence='undetermined', and file the rec anyway so it routes to mandatory human review (preflight 'CI-RCA Mandatory Human Review'). Do NOT guess earliest_viable_gate/escape_mode without a bundle."
  fi
  PROMPT="Apply the .claude/agents/scheduled/ci-rca.md instructions to failed CI run ${RUN_ID}. The failed log is at /tmp/ci-rca-failed.log.${EVIDENCE_CONTEXT}${RANGE_NOTE}"
  printf '%s' "$PROMPT"
}

check() {
  local output="${1:-/tmp/ci-rca-output.json}" filed_pairs rec_id
  cat "$output"
  filed_pairs=$(bin/venv-python -m scripts.ci_rca.filing --all "$output" || true)
  if [ -z "$filed_pairs" ]; then
    echo "::error::ci-rca agent filed no recommendation (no FILED: marker in output)"
    exit 1
  fi
  rec_id=$(bin/venv-python -m scripts.ci_rca.filing "$output" || true)
  if [ -z "$rec_id" ]; then
    rec_id=$(echo "$filed_pairs" | tail -1 | cut -d, -f1)
  fi
  echo "rec_id=${rec_id}" >> "$GITHUB_OUTPUT"
}

mark() {
  local output="${1:-/tmp/ci-rca-output.json}" fallback_filed="${2:-/tmp/ci-rca-fallback-filed.txt}"
  if [ -n "$ANCHOR_SHA" ]; then
    { bin/venv-python -m scripts.ci_rca.fallback pairs "$output" "$fallback_filed" || bin/venv-python -m scripts.ci_rca.filing --all "$output"; } | while IFS=',' read -r REC_ID CAT; do
      [ -z "$REC_ID" ] && continue
      [ -z "$CAT" ] && CAT="unknown"
      CONTEXT="ci-rca/${WORKFLOW_SLUG}/${CAT}"
      gh api "repos/${GITHUB_REPOSITORY}/statuses/${ANCHOR_SHA}" \
        --method POST \
        --field state=success \
        --field context="${CONTEXT}" \
        --field description="ci-rca filed ${REC_ID} for this failure"
    done
  fi
}

case "${1:-}" in
  prompt | check | mark)
    cmd="$1"
    shift
    "$cmd" "$@"
    ;;
  *)
    echo "usage: ci_rca_steps.sh prompt [BUNDLES_DIR] | check [OUTPUT] | mark [OUTPUT] [FALLBACK_FILED]" >&2
    exit 2
    ;;
esac
