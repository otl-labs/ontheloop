#!/usr/bin/env bash
# SessionStart hook: self-heal snapshot-baked dependency drift every session.
#
# bin/setup-cloud-env.sh installs requirements.txt + requirements-dev.txt (and,
# on the admin container, the terraform binary) only at snapshot-build time. A
# session booting from a snapshot built before a requirements*.txt change would
# have a stale venv missing (or mismatched on) a declared dependency -- and the
# repo is cloned fresh each session while the venv is baked into the snapshot,
# so a fresh checkout alone cannot supply it. This hook closes that gap by
# running the same idempotent sync every session: an in-sync venv is a
# millisecond fingerprint-compare no-op; otherwise it self-heals with a
# reinstall.
#
# Advisory only, always exits 0 so a
# failed or network-blocked sync never blocks the session. Delegates to
# bin/sync-deps.sh (the source of truth shared with bin/setup-cloud-env.sh).

set -uo pipefail

log() { printf '[session_start_sync_deps] %s\n' "$*"; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if out="$(bash "$REPO_ROOT/bin/sync-deps.sh" 2>&1)"; then
    log "$out"
else
    log "WARNING: dependency sync failed (non-fatal); a stale venv may be missing"
    log "  a declared dependency until this succeeds -- retries next session. Detail:"
    printf '%s\n' "$out" | while IFS= read -r line; do log "  $line"; done
fi

exit 0
