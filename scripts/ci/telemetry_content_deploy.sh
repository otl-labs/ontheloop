#!/usr/bin/env bash
# Telemetry content reader deploy steps (Decision 239 cl.5, 249, 270 and the content deploy channel Decision).
# Subcommands: summary (no credential; the approval screen) and deploy (holds the locked role; aws calls only).
# Every error names a step, never a value: no ARN, account id or environment value is printed.
set -euo pipefail

FUNCTION_NAME="agent-platform-ducklake-telemetry-content"
CONFIG="src/lambdas/ducklake_telemetry_content_reader/deploy_config.json"
FIRST_BASE="ddb28a40"
TOOLING=(scripts/build_lambda.py scripts/build_lambda_packaging.py scripts/build_lambda_config.py scripts/lambda_manifest.py)
DEPLOY_PATH=(
  .github/workflows/deploy-telemetry-content.yml scripts/ci/telemetry_content_deploy.sh
  scripts/ci/telemetry_content_record.py scripts/build_lambda_deploy.py scripts/ci/deploy_gate.py
  .github/workflows/deploy-gate.yml scripts/ci/environment_reviewer_preflight.py .github/actions/aws-region
  scripts/checks/ops_governance/validate_reconcile_pending_gate.py
)

fail() {
  echo "::error::TELEMETRY_CONTENT_DEPLOY step=$1 failed"
  exit 1
}

summary() {
  local out="${GITHUB_STEP_SUMMARY:-/dev/stdout}" base protective=() bundle=()
  mapfile -t protective <"${PROTECTIVE_PATHS_FILE:?}" || fail "read-protective-paths"
  mapfile -t bundle <"${BUNDLE_PATHS_FILE:?}" || fail "read-bundle-paths"
  base="${RECORD_SHA:-}"
  if [ -z "$base" ] || [ "$base" = "rebaseline" ]; then
    base="$FIRST_BASE"
  fi
  git rev-parse --verify --quiet "${base}^{commit}" >/dev/null || fail "resolve-range-start"
  {
    echo "## Telemetry content reader deploy: approve only if this is what you expect"
    echo
    echo "### Why this asks you, and the evidence"
    if [ -z "${RECORD_SHA:-}" ]; then
      echo "- First deploy of the reader: the range starts at ${FIRST_BASE}."
    elif [ "${RECORD_SHA}" = "rebaseline" ]; then
      echo "- Rebaseline dispatch: the range starts at ${FIRST_BASE} and the baseline checks were skipped."
    else
      echo "- Last recorded deploy: ${RECORD_SHA}"
    fi
    echo "- Protective-set commits since then:"
    git log --oneline "${base}..${GATE_SHA:?}" -- "${protective[@]}" | sed 's/^/  - /' || true
    echo "- Gate commit: ${GATE_SHA}"
    echo "- Head commit: ${HEAD_SHA:?}"
    echo "- Waking CI run: ${RUN_URL:?}"
    echo "- Built digest (sha256): ${CONTENT_SHA256:?}"
    echo "- Telemetry hold: held=${HELD:-unknown}"
    echo "- Layer pins in ${CONFIG}:"
    jq -r '.layers[] | "  - \(.name) version \(.version) digest \(.code_sha256)"' "$CONFIG" || fail "read-layer-pins"
    echo
    echo "### Every bundled commit going out"
    git log --oneline "${base}..${GATE_SHA}" -- "${bundle[@]}" | sed 's/^/- /' || true
    echo
    echo "### Build tooling going out (does not wake this channel)"
    git log --oneline "${base}..${GATE_SHA}" -- "${TOOLING[@]}" | sed 's/^/- /' || true
    echo
    echo "### Deploy path going out (does not wake this channel)"
    git log --oneline "${base}..${GATE_SHA}" -- "${DEPLOY_PATH[@]}" | sed 's/^/- /' || true
    echo
    echo "### deploy_config.json over the range"
    git diff "${base}..${GATE_SHA}" -- "$CONFIG" | sed 's/^/    /' || true
    echo
    echo "CI publishes the layers: a pin proves which bytes run, not who built them (rec-4742)"
  } >>"$out"
}

live_code_sha() {
  aws lambda get-function --function-name "$FUNCTION_NAME" --query Configuration.CodeSha256 --output text
}

deploy() {
  local zip="${ZIP:?}" want="${CONTENT_SHA256:?}" got acct live arns=() layer name version pin arn actual b64 readback
  got=$(sha256sum "$zip" | cut -d' ' -f1) || fail "digest"
  [ "$got" = "$want" ] || fail "digest"

  acct="${ACCOUNT_ID:-}"
  [ -n "$acct" ] || fail "mask-account"
  echo "::add-mask::${acct}"

  if [ -n "${RECORD_CODE_SHA256:-}" ] && [ "${REBASELINE:-}" != "true" ]; then
    live=$(live_code_sha) || fail "baseline"
    [ "$live" = "$RECORD_CODE_SHA256" ] || fail "baseline"
  fi

  jq -e '(.layers | type == "array" and length > 0) and (.handler | type == "string" and length > 0) and (.environment | type == "object")' \
    "$CONFIG" >/dev/null || fail "config-shape"

  while IFS= read -r layer; do
    name=$(jq -r '.name' <<<"$layer")
    version=$(jq -r '.version' <<<"$layer")
    pin=$(jq -r '.code_sha256' <<<"$layer")
    arn=$(aws lambda get-layer-version --layer-name "$name" --version-number "$version" --query LayerVersionArn --output text) ||
      fail "layer-pin"
    actual=$(aws lambda get-layer-version --layer-name "$name" --version-number "$version" --query Content.CodeSha256 --output text) ||
      fail "layer-pin"
    [ "$actual" = "$pin" ] || fail "layer-pin"
    arns+=("$arn")
  done < <(jq -c '.layers[]' "$CONFIG")

  aws lambda update-function-configuration --function-name "$FUNCTION_NAME" \
    --handler "$(jq -r '.handler' "$CONFIG")" --layers "${arns[@]}" \
    --environment "$(jq -c '{Variables: .environment}' "$CONFIG")" \
    --query LastUpdateStatus --output text >/dev/null || fail "update-configuration"
  aws lambda wait function-updated-v2 --function-name "$FUNCTION_NAME" || fail "wait-configuration"
  aws lambda update-function-code --function-name "$FUNCTION_NAME" --zip-file "fileb://${zip}" \
    --query CodeSha256 --output text >/dev/null || fail "update-code"
  aws lambda wait function-updated-v2 --function-name "$FUNCTION_NAME" || fail "wait-code"

  b64=$(printf "$(printf '%s' "$want" | sed 's/\(..\)/\\x\1/g')" | base64) || fail "digest-encode"
  live=$(live_code_sha) || fail "readback-code"
  [ "$live" = "$b64" ] || fail "readback-code"

  readback=$(aws lambda get-function-configuration --function-name "$FUNCTION_NAME" \
    --query '{handler: Handler, layers: Layers[].Arn, environment: Environment.Variables}' --output json) ||
    fail "readback-configuration"
  jq -e --argjson cfg "$(cat "$CONFIG")" --args '
    (.handler == $cfg.handler) and (.environment == $cfg.environment) and
    ((.layers | sort) == ($ARGS.positional | sort))' <<<"$readback" "${arns[@]}" >/dev/null || fail "readback-configuration"

  echo "OK readback: live CodeSha256 equals the built digest; handler, layers and environment equal deploy_config.json"
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    echo "code_sha256=${b64}" >>"$GITHUB_OUTPUT"
  fi
}

case "${1:-}" in
summary) summary ;;
deploy) deploy ;;
*) fail "usage" ;;
esac
