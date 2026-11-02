#!/usr/bin/env bash
# Body of the materialise-tfvars composite action, extracted (Decision 162 R2) so it is
# shellcheckable and directly testable under the literal GitHub composite argv
# `bash --noprofile --norc -e -o pipefail <file>`. That argv, and a `set -euo pipefail` caller
# like bin/setup-cloud-env.sh, both export SHELLOPTS -- a child bash inherits it and re-applies
# errexit -- so this script may run with -e inherited or (a bare `bash materialise_tfvars.sh`)
# without it. Every failure path below is an explicit `if`/exit check rather than relying on
# errexit, so behaviour does not depend on which is in force.
#
# THE single SSM Parameter Store fetch for terraform/personal/terraform.personal.tfvars -- collapses
# the three inline "Materialise tfvars" / "Mask secret-sourced tfvars values in logs" / "Assert
# tfvars file is non-empty" steps that action.yml and terraform-drift.yml used to carry
# separately, and is also called directly from bin/setup-cloud-env.sh's ADMIN bootstrap branch, so
# CI and the local break-glass loop share ONE implementation.
#
# Takes at most one argument, --stdout (Decision 238, trusted plan): same fetch and empty-secret check, the
# ::add-mask:: lines go to STDERR (so a caller's $( ) capture of stdout never swallows them and the runner
# still registers them), and the tfvars are printed to stdout instead of written to a file (no mktemp, no
# file). Default (no argument) behaviour is unchanged. Configuration is otherwise env-only ($GITHUB_ACTIONS gates ::add-mask::). Stays
# profile-free: all four CI apply call sites use OIDC ambient credentials, so hardcoding
# --profile agent_platform_admin would break them there. A PlatformDev container AccessDenies on
# this secret by design (per-parameter GetParameter grant, Decision 237) -- that is the intended
# DEP-13 fail-closed outcome, not a bug; re-run from an ADMIN container.
#
# No INSTALL_TERRAFORM gate here -- that belongs to the caller (bin/setup-cloud-env.sh's bootstrap
# branch). INSTALL_TERRAFORM appears zero times under .github/workflows/, so gating inside this
# shared unit would no-op every CI apply call site.
#
# On the success path with GITHUB_ACTIONS unset, this script is TOTALLY SILENT on both stdout and
# stderr (Decision 101: the local ADMIN shell must never print anything secret-shaped into the
# session transcript). Each failure mode (fetch failed, empty secret, write failed) emits its own
# pairwise-distinct, greppable stderr message so the three signals the collapsed CI steps used to
# carry survive in the log.

set -uo pipefail

MODE=file
case "${1:-}" in
  "") ;;
  --stdout) MODE=stdout ;;
  *)
    echo "materialise-tfvars: BAD_ARGUMENT only --stdout is accepted" >&2
    exit 2
    ;;
esac

mask_values() {
  local line key value
  while IFS= read -r line; do
    key=${line%%=*}
    value=${line#*=}
    key=$(printf '%s' "$key" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    value=$(printf '%s' "$value" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//;s/^"//;s/"$//')
    case "$key" in
      account_id | platform_dev_external_id | platform_admin_external_id)
        if [ -n "$value" ]; then
          echo "::add-mask::${value}" >&2
        fi
        ;;
    esac
  done
}

if [ "$MODE" = stdout ]; then
  if ! SECRET=$(aws ssm get-parameter --name /secret/agent-platform-terraform-personal-tfvars \
      --with-decryption --query Parameter.Value --output text); then
    echo "materialise-tfvars: FETCH_FAILED could not fetch /secret/agent-platform-terraform-personal-tfvars from SSM Parameter Store" >&2
    exit 1
  fi
  if [ -z "$SECRET" ]; then
    echo "materialise-tfvars: EMPTY_SECRET the fetched secret value was empty -- refusing to print it (fail-closed)" >&2
    exit 1
  fi
  if [ -n "${GITHUB_ACTIONS:-}" ]; then
    mask_values <<<"$SECRET"
  fi
  printf '%s\n' "$SECRET"
  exit 0
fi

DEST="terraform/personal/terraform.personal.tfvars"
# mktemp INSIDE the destination directory (never $TMPDIR) so the final `mv` is a same-filesystem
# atomic rename, not copy+unlink -- a cross-filesystem move would reopen the truncation window a
# temp-file-then-mv is meant to close. A failed fetch or empty secret therefore never touches
# $DEST at all, leaving any pre-existing file byte-identical.
TMP=$(mktemp "${DEST}.XXXXXX")
FETCH_ERR=$(mktemp)
trap 'rm -f "$TMP" "$FETCH_ERR"' EXIT

if ! aws ssm get-parameter --name /secret/agent-platform-terraform-personal-tfvars \
    --with-decryption --query Parameter.Value --output text \
    >"$TMP" 2>"$FETCH_ERR"; then
  echo "materialise-tfvars: FETCH_FAILED could not fetch /secret/agent-platform-terraform-personal-tfvars from SSM Parameter Store: $(cat "$FETCH_ERR")" >&2
  exit 1
fi

if [ ! -s "$TMP" ]; then
  echo "materialise-tfvars: EMPTY_SECRET the fetched secret value was empty -- refusing to write $DEST (fail-closed)" >&2
  exit 1
fi

if ! chmod 600 "$TMP"; then
  echo "materialise-tfvars: WRITE_FAILED could not chmod 600 the fetched tfvars file" >&2
  exit 1
fi

# rec-2214/rec-2285: emit ::add-mask:: for account_id and both ExternalId trust secrets BEFORE any
# downstream step writes plan.json / review.txt, so they are redacted in subsequent log output.
# Split on the FIRST '=' only (line%%=* / line#*=) so a value containing '=' is not truncated.
# Gated on $GITHUB_ACTIONS: ::add-mask:: is a GitHub-runner sentinel, not redaction -- emitting it
# unconditionally would print account_id and both ExternalIds into a local ADMIN shell transcript,
# breaching Decision 101.
if [ -n "${GITHUB_ACTIONS:-}" ]; then
  while IFS= read -r line; do
    key=${line%%=*}
    value=${line#*=}
    key=$(printf '%s' "$key" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    value=$(printf '%s' "$value" | sed 's/^[[:space:]]*"//;s/"[[:space:]]*$//')
    case "$key" in
      account_id | platform_dev_external_id | platform_admin_external_id)
        if [ -n "$value" ]; then
          echo "::add-mask::${value}"
        fi
        ;;
    esac
  done <"$TMP"
fi

if ! mv "$TMP" "$DEST"; then
  echo "materialise-tfvars: WRITE_FAILED could not move the fetched tfvars file into place at $DEST" >&2
  exit 1
fi

exit 0
