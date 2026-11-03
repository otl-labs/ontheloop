#!/usr/bin/env bash
# Neon provider API key handling for terraform/personal CI steps.
# Usage: neon_api_key.sh check
#        neon_api_key.sh run -- CMD [ARGS...]
#
# The key reaches Terraform only as the ephemeral input variable neon_api_key, so it is never in
# state or a saved plan (docs/contracts/secret-material-handling.yaml
# patterns.provider_credential_ephemeral_input). It is held in a shell variable and handed to one
# child process; it is never written to disk, GITHUB_ENV or GITHUB_OUTPUT, and never printed.
#
# check: pre-apply preflight run inside tf_init so a missing grant surfaces as an infra_error
#        (Decision 154) instead of failing the apply step. Skipped only on plain pull_request events,
#        which run no personal-root plan since the Decision 238 cutover; every other event, the
#        trusted plan job included, fetches.
# run:   fetch, mask, export TF_VAR_neon_api_key for CMD only, exec CMD. Safe to call repeatedly.
set -euo pipefail

PARAMETER_NAME="/secret/neon-api-key"

fetch_key() {
    local value
    if ! value=$(aws ssm get-parameter --name "$PARAMETER_NAME" --with-decryption --query Parameter.Value --output text 2>/dev/null); then
        echo "neon-api-key: fetch failed (check the parameter and the role's GetParameter grant)" >&2
        return 1
    fi
    if [ -z "$value" ] || [ "$value" = "None" ]; then
        echo "neon-api-key: parameter is empty" >&2
        return 1
    fi
    printf '%s' "$value"
}

cmd="${1:-}"
case "$cmd" in
    check)
        case "${GITHUB_EVENT_NAME:-}" in
            pull_request)
                echo "neon-api-key: skipped (no personal-root plan runs on pull_request)"
                exit 0
                ;;
        esac
        key=$(fetch_key) || exit 1
        unset key
        echo "neon-api-key: fetch ok"
        ;;
    run)
        shift
        if [ "${1:-}" != "--" ] || [ $# -lt 2 ]; then
            echo "Usage: neon_api_key.sh run -- CMD [ARGS...]" >&2
            exit 1
        fi
        shift
        key=$(fetch_key) || exit 1
        echo "::add-mask::${key}"
        TF_VAR_neon_api_key="$key"
        export TF_VAR_neon_api_key
        unset key
        exec "$@"
        ;;
    *)
        echo "Usage: neon_api_key.sh check | run -- CMD [ARGS...]" >&2
        exit 1
        ;;
esac
