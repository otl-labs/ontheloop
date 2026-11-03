#!/usr/bin/env bash
# Body of the aws-region composite action, extracted (Decision 162 R2) so it is shellcheckable and
# directly testable under the literal GitHub composite argv `bash --noprofile --norc -e -o
# pipefail <file>`. That argv, and a `set -euo pipefail` caller, both export SHELLOPTS -- a child
# bash inherits it and re-applies errexit -- so this script may run with -e inherited or (a bare
# `bash resolve.sh`) without it. Every failure path below is an explicit `if`/exit check rather
# than relying on errexit, as materialise_tfvars.sh does.
#
# THE single reader of config/aws-region (rec-4067): every workflow sign-in and every Terraform
# S3 backend takes the project's one region through this action rather than a hardcoded literal,
# so the platform stays shippable as a tool whose users set exactly one region (Decision 202
# clause 4).
#
# Reads "$GITHUB_ACTION_PATH/../../../config/aws-region" -- the checkout the action was loaded
# from -- never the invoking job's working-directory, so a job with working-directory set
# elsewhere still resolves the same file.

set -uo pipefail

if [ -z "${GITHUB_ACTION_PATH:-}" ]; then
  FILE="$(dirname "$0")/../../../config/aws-region"
else
  FILE="${GITHUB_ACTION_PATH}/../../../config/aws-region"
fi

if [ ! -f "$FILE" ] || [ ! -s "$FILE" ]; then
  echo "::error::REGION_MISSING ${FILE} is missing or empty -- the project region could not be resolved" >&2
  exit 1
fi

REGION=$(tr -d '[:space:]' < "$FILE")

if [ -z "$REGION" ]; then
  echo "::error::REGION_MISSING ${FILE} is whitespace-only -- the project region could not be resolved" >&2
  exit 1
fi

if ! printf '%s' "$REGION" | grep -qE '^[a-z]{2}(-gov)?-[a-z]+-[0-9]$'; then
  echo "::error::REGION_INVALID ${FILE} holds '${REGION}', which is not a valid AWS region id" >&2
  exit 1
fi

if [ -n "${GITHUB_ENV:-}" ]; then
  echo "AWS_REGION=${REGION}" >> "$GITHUB_ENV"
  echo "AWS_DEFAULT_REGION=${REGION}" >> "$GITHUB_ENV"
fi

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "aws_region=${REGION}" >> "$GITHUB_OUTPUT"
fi

echo "REGION_OK ${REGION}"
exit 0
