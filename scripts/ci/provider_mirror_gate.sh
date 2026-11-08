#!/usr/bin/env bash
# Provider-mirror gate chain for terraform-validate (Decision 55/83: fail-open, never wedges the
# required check). Moved out of ci.yml's inline mirror_gate body (Decision 162 R3).
#
# Three gates, evaluated in order; ANY failure degrades to direct-fetch with a printed reason:
#   gate 1/3 OIDC credentials (env OIDC_FAILED must equal exactly "false")
#   gate 2/3 non-empty S3 mirror sync, limited to the namespaces config/terraform/cc-web.tfrc includes
#   gate 3/3 EXACT-LOCKED-VERSION presence for every provider the mirror serves (kislerdm/neon from
#            terraform/personal, integrations/github from terraform/github), never mere directory
#            non-emptiness: the tfrc excludes those namespaces from `direct` and provider_installation
#            has no method-chaining, so a tfrc over a version-incomplete mirror would red init.
# Activation is all-or-nothing. Never `set -e`: every degradation exits 0.
set -uo pipefail

reason=""
active=false
NEON_VERSION=""
GITHUB_VERSION=""
TFRC="config/terraform/cc-web.tfrc"
BUCKET_PREFIX="s3://agent-platform-data-lake/tf-provider-mirror/"

if [ "${OIDC_FAILED:-}" != "false" ]; then
  reason="gate 1/3 (OIDC credentials) failed: could not assume the mirror-read role -- degrading to direct-fetch"
elif [ -z "${RUNNER_TEMP:-}" ]; then
  reason="gate 2/3 (mirror sync) failed: RUNNER_TEMP is unset -- degrading to direct-fetch"
else
  MIRROR_DIR="${RUNNER_TEMP}/tf-provider-mirror"
  NAMESPACES=()
  if [ -f "$TFRC" ]; then
    while IFS= read -r ns; do
      if [ -n "$ns" ]; then NAMESPACES+=("$ns"); fi
    done < <(grep -E '^[[:space:]]*include[[:space:]]*=' "$TFRC" | grep -oE '"[^"]+"' | tr -d '"')
  fi
  if [ "${#NAMESPACES[@]}" -eq 0 ]; then
    reason="gate 2/3 (mirror sync) failed: no mirror namespaces parsed from cc-web.tfrc -- degrading to direct-fetch"
  else
    INCLUDES=()
    for ns in "${NAMESPACES[@]}"; do INCLUDES+=(--include "$ns"); done
    mkdir -p "$MIRROR_DIR"
    timeout 300 aws s3 sync "$BUCKET_PREFIX" "$MIRROR_DIR" --exclude "*" "${INCLUDES[@]}" --only-show-errors
    sync_rc=$?
    if [ "$sync_rc" -eq 124 ]; then
      reason="gate 2/3 (mirror sync) failed: aws s3 sync timed out after 300s -- degrading to direct-fetch"
    elif [ "$sync_rc" -ne 0 ]; then
      reason="gate 2/3 (mirror sync) failed: aws s3 sync errored -- degrading to direct-fetch"
    elif [ -z "$(ls -A "$MIRROR_DIR" 2>/dev/null)" ]; then
      reason="gate 2/3 (mirror sync) failed: synced tree is empty -- degrading to direct-fetch"
    else
      for ns in "${NAMESPACES[@]}"; do
        if [ -z "$(ls -A "$MIRROR_DIR/${ns%/\*}" 2>/dev/null)" ]; then
          reason="gate 2/3 (mirror sync) failed: synced namespace ${ns%/\*} is empty -- degrading to direct-fetch"
          break
        fi
      done
      if [ -z "$reason" ]; then
        NEON_VERSION=$(awk '
          /^provider "registry.terraform.io\/kislerdm\/neon"/ { found=1 }
          found && /version[ \t]*=/ { gsub(/[",]/, "", $3); print $3; exit }
        ' terraform/personal/.terraform.lock.hcl 2>/dev/null)
        GITHUB_VERSION=$(awk '
          /^provider "registry.terraform.io\/integrations\/github"/ { found=1 }
          found && /version[ \t]*=/ { gsub(/[",]/, "", $3); print $3; exit }
        ' terraform/github/.terraform.lock.hcl 2>/dev/null)
        if [ -z "$NEON_VERSION" ]; then
          reason="gate 3/3 (exact-locked-version) failed: could not parse kislerdm/neon version from terraform/personal/.terraform.lock.hcl -- degrading to direct-fetch"
        elif ! ls "$MIRROR_DIR/registry.terraform.io/kislerdm/neon/terraform-provider-neon_${NEON_VERSION}_"*.zip >/dev/null 2>&1; then
          reason="gate 3/3 (exact-locked-version) failed: synced mirror lacks registry.terraform.io/kislerdm/neon terraform-provider-neon_${NEON_VERSION}_*.zip (locked version) -- degrading to direct-fetch (re-seed via terraform-provider-mirror-seed.yml)"
        elif [ -z "$GITHUB_VERSION" ]; then
          reason="gate 3/3 (exact-locked-version) failed: could not parse integrations/github version from terraform/github/.terraform.lock.hcl -- degrading to direct-fetch"
        elif ! ls "$MIRROR_DIR/registry.terraform.io/integrations/github/terraform-provider-github_${GITHUB_VERSION}_"*.zip >/dev/null 2>&1; then
          reason="gate 3/3 (exact-locked-version) failed: synced mirror lacks registry.terraform.io/integrations/github terraform-provider-github_${GITHUB_VERSION}_*.zip (locked version) -- degrading to direct-fetch (re-seed via terraform-provider-mirror-seed.yml)"
        elif [ -z "${GITHUB_ENV:-}" ]; then
          reason="export failed: GITHUB_ENV is unset, cannot export TF_CLI_CONFIG_FILE -- degrading to direct-fetch"
        elif [ -z "${GITHUB_OUTPUT:-}" ]; then
          reason="export failed: GITHUB_OUTPUT is unset, cannot write active=true so the fail-closed invariant step could not run -- degrading to direct-fetch"
        else
          RESOLVED_TFRC="${RUNNER_TEMP}/cc-web.tfrc"
          if sed "s|__TF_MIRROR_DIR__|${MIRROR_DIR}|g" "$TFRC" > "$RESOLVED_TFRC"; then
            echo "TF_CLI_CONFIG_FILE=${RESOLVED_TFRC}" >> "$GITHUB_ENV"
            active=true
          else
            reason="render failed: could not write the resolved tfrc -- degrading to direct-fetch"
          fi
        fi
      fi
    fi
  fi
fi

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "active=${active}" >> "$GITHUB_OUTPUT"
fi

if [ "$active" = "true" ]; then
  echo "provider-mirror: ACTIVE (kislerdm/neon ${NEON_VERSION}, integrations/github ${GITHUB_VERSION})"
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    echo "provider-mirror: ACTIVE (kislerdm/neon ${NEON_VERSION}, integrations/github ${GITHUB_VERSION})" >> "$GITHUB_STEP_SUMMARY"
  fi
else
  echo "provider-mirror: DEGRADED -- ${reason}"
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    echo "provider-mirror: DEGRADED -- ${reason}" >> "$GITHUB_STEP_SUMMARY"
  fi
fi
exit 0
