#!/usr/bin/env bash
# Re-wire the pre-commit .git hook every session.
#
# The hook ENVIRONMENTS are snapshot-cached by bin/setup-cloud-env.sh
# (pre-commit install-hooks). This hook only writes .git/hooks/pre-commit, which
# lives inside .git and therefore does NOT survive the fresh per-session clone --
# so it must be re-applied each session (cheap, offline). Mirrors the self-heal
# pattern of session_start_sync_deps.sh.
#
# Once installed, `git commit` runs detect-secrets, the shape-based never-commit
# identifier denylist, ruff, and the file-hygiene hooks before the commit lands --
# blocking a bad commit at source rather than only at CI.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

if [ ! -x .venv/bin/pre-commit ]; then
    echo "[session_start_precommit] .venv/bin/pre-commit not found; run bin/setup-cloud-env.sh"
    exit 0
fi

if .venv/bin/pre-commit install >/dev/null 2>&1; then
    echo "[session_start_precommit] pre-commit git hook installed"
else
    echo "[session_start_precommit] WARNING: pre-commit install failed (non-fatal)"
fi
