#!/usr/bin/env bash
# Bootstrap a Claude Code on the web Linux container for this repo.
#
# Idempotent. Safe to re-run. Designed to be invoked from the "Setup script"
# field of the cloud environment configuration. On a personal Claude Code plan
# that field is private to you, so it is the correct home for the static-key
# credential block below (the env-var field is "visible to anyone using this
# environment" -- never put credentials there).
#
# AWS credentials (rec-4510, Decision 113 Update, Decision 256): the agent-platform-session key never sits in a
# container, and no environment holds a role. One template serves every agent environment, and it has two parts:
#
# (1) An AWS (SigV4) network secret in the environment settings holding the agent-platform-session access key
#     (terraform/bootstrap/agent_session_user.tf), with exactly these allowed hosts: sts.eu-west-2.amazonaws.com,
#     ssm.eu-west-2.amazonaws.com, logs.eu-west-2.amazonaws.com, monitoring.eu-west-2.amazonaws.com,
#     iam.amazonaws.com and the DuckLake REST API's execute-api host (print it from the SSM parameter
#     /agent-platform/ducklake/api_url/ops/reader). The agent proxy signs requests to those hosts as the IAM user;
#     the container never sees the key. Network secrets apply to NEW sessions only. The key and host procedure is
#     docs/contracts/deploy-paths.yaml admin_out_of_band.agent_session_identity.
#
# (2) A private "Setup script" field block that writes ~/.aws/config only (placeholder credentials come from
#     bin/aws-proxy-placeholder-credentials, installed to ~/.local/bin by step 2b below) and removes any stale
#     ~/.aws/credentials. botocore does not expand $HOME or ~ in credential_process, so the block writes the
#     sections with printf and an expanded path. The env-var field (non-secret) sets
#     OTL_OPS_VERBS_TRANSPORT=execute_api and nothing else: no AWS_PROFILE.
#
# DEV environment -- paste into the "Setup script" field:
#
#     set -euo pipefail
#     mkdir -p "$HOME/.aws" "$HOME/.local/bin" && chmod 700 "$HOME/.aws"
#     rm -f "$HOME/.aws/credentials"
#
#     for p in agent_platform agent_static; do
#         printf '[profile %s]\nregion = eu-west-2\nsts_regional_endpoints = regional\noutput = json\ncredential_process = %s\n\n' \
#             "$p" "$HOME/.local/bin/aws-proxy-placeholder-credentials"
#     done > "$HOME/.aws/config"
#     chmod 600 "$HOME/.aws/config"
#
#     cd /home/user/ontheloop && bash bin/setup-cloud-env.sh
#
# boto3 then resolves the agent_platform profile: the placeholder key is only a placeholder, the proxy signs each
# call as the user, and there is no role step to refresh. agent_static is kept so scripts naming it still resolve.
#
# ADMIN environment: there is no admin identity here (Decision 256 cl.3). An admin environment is this same
# session user with terraform installed; every admin step is the operator's, run in AWS CloudShell. Its last line
# passes the flag inline instead of relying on the env-var field:
#     cd /home/user/ontheloop && INSTALL_TERRAFORM=1 bash bin/setup-cloud-env.sh
# The setup script runs once per environment snapshot, before environment variables are set, and
# the agent proxy never signs its requests, so INSTALL_TERRAFORM=1 is passed inline and the script
# makes no AWS call: the provider mirror is synced by the every-session ADMIN hook (see the split below).
#
# Identity checks: an unpinned `aws sts get-caller-identity` and the helper both report the IAM user. Use
# `bin/venv-python -m scripts.aws_identity caller-arn --profile <p>` (reads the identity from the profile's own session).
#
# IMPORTANT: the "Setup script" field must include the `cd` prefix above, because
# the field runs bash from a working directory that is NOT the repo root.
# Changing the field value is a manual step (out-of-repo).
#
# Responsibility split (post T0.2 refactor):
#   setup-cloud-env.sh -- snapshot-cached installs plus Codex bootstrap parity for Claude's SessionStart checks.
#   .claude/hooks/session_start_aws.sh -- reports the proxy-signed user via scripts.aws_identity (every session).
#   .claude/hooks/session_start_sync_deps.sh -- self-heals requirements.txt/requirements-dev.txt (and terraform binary) drift on stale snapshots (every session).
#   .claude/hooks/session_start_terraform_assets.sh -- syncs the Terraform provider mirror in ADMIN sessions (every session; bin/sync-terraform-assets.sh).
#   .claude/hooks/session_start_precommit.sh -- re-wires the pre-commit .git hook (every session; .git/hooks does not survive a fresh clone).
#
# What it does:
#   1. Creates .venv with python3.12.
#   2. Installs uv (fast pip replacement); falls back to pip if install fails.
#   2b. Installs bin/aws-proxy-placeholder-credentials to ~/.local/bin (credential_process target).
#   3. Installs AWS CLI v2.
#   4. Installs runtime + dev requirements, and (opt-in, INSTALL_TERRAFORM=1)
#      Terraform, via bin/sync-deps.sh -- also establishes the build-time
#      fingerprint so a freshly-built snapshot boots in-sync (no session-1
#      drift-install penalty). Steps 3 and 4 are parallelised: AWS CLI download
#      runs in the background while step 4 runs in the foreground.
#   5. Waits for the background AWS CLI install (step 3) to finish.
#   6. Pre-builds the pre-commit hook environments into ~/.cache/pre-commit so
#      the hooks (detect-secrets, ruff, file hygiene) run offline in later sessions,
#      then runs the remaining checkout-local SessionStart checks used by Claude.
#
# What it does NOT do:
#   - Materialise ~/.aws/config (done by the private "Setup script" field block above; this script only
#     consumes it).
#   - Install gh CLI (GitHub access is via the harness github MCP server, not gh).
#   - Install terraform unless INSTALL_TERRAFORM=1 (set only in the admin env).
#   - Sync the Terraform provider mirror or fetch the tfvars (the proxy never signs setup requests; see
#     .claude/hooks/session_start_terraform_assets.sh and `bash bin/sync-terraform-assets.sh --tfvars`).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

log() { printf '[setup] %s\n' "$*"; }

# 1. Python 3.12 venv ---------------------------------------------------------
t0=$SECONDS
if [ ! -x .venv/bin/python ]; then
    if ! command -v python3.12 >/dev/null 2>&1; then
        log "ERROR: python3.12 not found on PATH" >&2
        exit 1
    fi
    log "Creating .venv with $(python3.12 --version)"
    python3.12 -m venv .venv
else
    log ".venv already present ($(.venv/bin/python --version))"
fi
log "venv: $((SECONDS - t0))s"

# 2. uv install (idempotent) --------------------------------------------------
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
    log "Installing uv"
    t0=$SECONDS
    curl -LsSf https://astral.sh/uv/install.sh | sh || true
    log "uv-install: $((SECONDS - t0))s"
fi

# 2b. credential_process placeholder (before the first AWS call below) ---------
PLACEHOLDER_SRC="$REPO_ROOT/bin/aws-proxy-placeholder-credentials"
PLACEHOLDER_DST="$HOME/.local/bin/aws-proxy-placeholder-credentials"
mkdir -p "$HOME/.local/bin"
if ! cmp -s "$PLACEHOLDER_SRC" "$PLACEHOLDER_DST"; then
    install -m 0755 "$PLACEHOLDER_SRC" "$PLACEHOLDER_DST"
    log "Installed $PLACEHOLDER_DST"
fi

# 3. AWS CLI v2 (background) --------------------------------------------------
aws_install_pid=""
if ! command -v aws >/dev/null 2>&1; then
    (
        log "Installing AWS CLI v2 (background)"
        tmpdir="$(mktemp -d)"
        trap 'rm -rf "$tmpdir"' EXIT
        curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "$tmpdir/awscliv2.zip"
        unzip -q "$tmpdir/awscliv2.zip" -d "$tmpdir"
        if [ "$(id -u)" -eq 0 ]; then
            "$tmpdir/aws/install" --update >/dev/null
        else
            sudo "$tmpdir/aws/install" --update >/dev/null
        fi
        log "AWS CLI installed: $(aws --version 2>&1)"
    ) &
    aws_install_pid=$!
else
    log "AWS CLI already present: $(aws --version 2>&1)"
fi

# 4. Requirements + terraform binary (foreground, parallel with step 3) ------
# Delegated to bin/sync-deps.sh -- the single dependency-sync code path shared
# with .claude/hooks/session_start_sync_deps.sh (every-session self-heal for a
# snapshot venv that predates a requirements*.txt change). This call also
# writes the fingerprint, so a freshly-built snapshot boots in-sync (no
# session-1 drift-install penalty). Installs requirements always; installs the
# terraform binary too when INSTALL_TERRAFORM=1 (admin container only).
t0=$SECONDS
bash "$REPO_ROOT/bin/sync-deps.sh"
log "sync-deps: $((SECONDS - t0))s"

# 5. Wait for background AWS CLI install --------------------------------------
if [ -n "$aws_install_pid" ]; then
    t0=$SECONDS
    wait "$aws_install_pid"
    log "aws-cli-install: $((SECONDS - t0))s"
fi

# 6. pre-commit hook environments (snapshot-cached) ---------------------------
# pre-commit itself is installed via requirements-dev.txt (step 4). This pre-builds
# the hook repos (pre-commit-hooks, ruff-pre-commit, detect-secrets) into
# ~/.cache/pre-commit now -- while setup-time network is available -- so they are
# baked into the snapshot and run offline in later sessions, mirroring the AWS CLI
# caching above. Wire the setup checkout immediately as well;
# Claude session startup repeats this for fresh per-session clones.
t0=$SECONDS
if [ -x .venv/bin/pre-commit ]; then
    if .venv/bin/pre-commit install-hooks >/dev/null 2>&1; then
        log "pre-commit hook envs cached: $((SECONDS - t0))s"
    else
        log "WARNING: pre-commit install-hooks failed (non-fatal); hooks fetch lazily on first run."
    fi
else
    log "WARNING: .venv/bin/pre-commit missing (requirements-dev.txt not installed?); skipping hook cache."
fi

# Codex cloud invokes this tracked setup script instead of .claude/settings.json.
# The dependency SessionStart script delegates to the same commands
# already run above; invoke the remaining checkout-local checks here for parity.
bash .claude/hooks/session_start_aws.sh
bash .claude/hooks/session_start_github_readiness.sh
bash .claude/hooks/session_start_precommit.sh
bash .claude/hooks/session_start_sync_main.sh

log "Done. Verify with: bin/venv-python -m scripts.session.preflight"
