#!/usr/bin/env bash
# Provider-mirror support for .github/workflows/terraform-github-apply.yml (rec-5005, Decision 248 cl.4 Update).
#
#   fetch          copy the exact locked integrations/github package from the S3 provider mirror, check its sha256 against
#                  the committed lockfile, and export a mirror-only TF_CLI_CONFIG_FILE; every other outcome degrades to
#                  today's direct fetch. Never fails the job (Decision 55: fail-open, loud via ::warning::).
#   classify LOG   print one line naming the class of a failed init (provider_install, backend or other), never log text.
#
# No `set -e`: every path ends in an explicit `exit 0`. AWS error text is never printed (it names an ARN, Decision 101).
set -uo pipefail

PROVIDER="registry.terraform.io/integrations/github"

degrade() {
    echo "provider-mirror: DEGRADED -- $1 -- falling back to a direct fetch"
    echo "::warning::provider-mirror: DEGRADED -- $1"
    exit 0
}

fetch() {
    local prefix="${MIRROR_PREFIX:-}" runner_temp="${RUNNER_TEMP:-}" github_env="${GITHUB_ENV:-}"
    if [ -z "$prefix" ] || [ -z "$runner_temp" ] || [ -z "$github_env" ]; then
        degrade "MIRROR_PREFIX, RUNNER_TEMP or GITHUB_ENV is unset"
    fi
    if [ "$(uname -sm 2> /dev/null)" != "Linux x86_64" ]; then
        degrade "unsupported OS or CPU"
    fi
    local here lock version key dest zip_hash tfrc
    here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd) || degrade "script directory unresolved"
    lock="$here/../../terraform/github/.terraform.lock.hcl"
    version=$(awk '
        /^provider "registry.terraform.io\/integrations\/github"/ { found=1 }
        found && /version[ \t]*=/ { gsub(/[",]/, "", $3); print $3; exit }
    ' "$lock" 2> /dev/null)
    if [ -z "$version" ]; then
        degrade "locked integrations/github version unparsed"
    fi
    key="$PROVIDER/terraform-provider-github_${version}_linux_amd64.zip"
    dest="$runner_temp/tf-github-mirror/$key"
    mkdir -p "$(dirname "$dest")" 2> /dev/null || degrade "mirror directory not writable"
    if ! timeout 120 aws s3 cp "${prefix}${key}" "$dest" --only-show-errors > /dev/null 2>&1; then
        rm -f "$dest"
        degrade "copy from the mirror failed or timed out"
    fi
    if [ ! -f "$dest" ] || [ ! -s "$dest" ]; then
        rm -f "$dest"
        degrade "copied package is empty"
    fi
    zip_hash="zh:$(sha256sum "$dest" 2> /dev/null | awk '{print $1}')"
    if ! awk '
        /^provider "registry.terraform.io\/integrations\/github"/ { found=1 }
        found && /^}/ { exit }
        found { gsub(/[" ,]/, ""); print }
    ' "$lock" 2> /dev/null | grep -qxF "$zip_hash"; then
        rm -f "$dest"
        degrade "hash mismatch"
    fi
    tfrc="$runner_temp/tf-github-mirror.tfrc"
    if ! printf 'provider_installation {\n  filesystem_mirror {\n    path    = "%s"\n    include = ["%s"]\n  }\n  direct {\n    exclude = ["%s"]\n  }\n}\n' \
        "$runner_temp/tf-github-mirror" "$PROVIDER" "$PROVIDER" > "$tfrc" 2> /dev/null; then
        degrade "tfrc write failed"
    fi
    if ! echo "TF_CLI_CONFIG_FILE=$tfrc" >> "$github_env" 2> /dev/null; then
        degrade "GITHUB_ENV write failed"
    fi
    echo "provider-mirror: ACTIVE (integrations/github ${version})"
    exit 0
}

classify() {
    local log="${1:-}" class=other
    if [ -n "$log" ] && [ -r "$log" ]; then
        if grep -qE 'Error loading state|Failed to get existing workspaces|error configuring S3 Backend|Error acquiring the state lock' "$log" 2> /dev/null; then
            class=backend
        elif grep -qE 'Failed to install provider|Failed to query available provider packages|could not query provider registry|Gateway Timeout|502 Bad Gateway|checksum' "$log" 2> /dev/null; then
            class=provider_install
        fi
    fi
    echo "init failure class: $class"
    exit 0
}

case "${1:-}" in
    fetch) fetch ;;
    classify) classify "${2:-}" ;;
    *)
        echo "Usage: terraform_github_apply_mirror.sh fetch | classify LOG" >&2
        exit 0
        ;;
esac
exit 0
