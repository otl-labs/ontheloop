#!/usr/bin/env bash
# SessionStart hook: sync the Terraform provider mirror in CC-web ADMIN sessions (rec-4747, Decision 120).
#
# bin/setup-cloud-env.sh runs once per environment snapshot, before environment variables are set, and the agent proxy
# never signs its requests, so it cannot fetch the mirror; this hook runs every session, when the proxy signs. An ADMIN
# session is one whose ~/.aws/config holds the agent_platform_admin profile. Mirror only: the tfvars are fetched on
# demand through `bash bin/sync-terraform-assets.sh --tfvars`, never here (Decision 126).
#
# Advisory only, always exits 0 so a failed or network-blocked sync never blocks the session.

set -uo pipefail

log() { printf '[session_start_terraform_assets] %s\n' "$*"; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if ! grep -qE '^\[profile agent_platform_admin\]' "$HOME/.aws/config" 2>/dev/null; then
    log "skipped (no agent_platform_admin profile: not an ADMIN session)"
    exit 0
fi

if ! command -v terraform >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/terraform" ]; then
    log "WARNING: no terraform binary on PATH or in ~/.local/bin (set INSTALL_TERRAFORM=1 in the setup script); syncing the mirror anyway"
fi

out="$(bash "$REPO_ROOT/bin/sync-terraform-assets.sh" 2>&1)"
printf '%s\n' "$out" | while IFS= read -r line; do
    [ -n "$line" ] && log "$line"
done

exit 0
