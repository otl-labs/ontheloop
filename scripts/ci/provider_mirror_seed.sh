#!/usr/bin/env bash
# Seed the S3 terraform provider filesystem mirror from both Terraform roots (rec-2514, rec-4638).
# Usage: provider_mirror_seed.sh MIRROR_DIR S3_PREFIX   (run from the repo root, after AWS credentials and terraform exist)
#
# Mirrors terraform/personal (already initialized by the workflow) and terraform/github (initialized here, because
# `providers mirror` reads the lock file only in an initialized directory) into the SAME directory, asserts a
# linux_amd64 package of the exact locked version exists for every provider either lockfile names, and only then
# publishes with one `aws s3 sync --delete`. The seed role holds bucket-wide DeleteObject, so the prefix must be exactly
# tf-provider-mirror: a --delete sync anywhere else could erase tfstate/ or tfplan/. Provider bytes are public.
set -euo pipefail

if [ "$#" -ne 2 ] || [ "$2" != "tf-provider-mirror" ] || [ -z "$1" ]; then
  echo "SEED_BAD_ARGS usage: provider_mirror_seed.sh MIRROR_DIR tf-provider-mirror" >&2
  exit 2
fi
dir=$1
prefix=$2

mkdir -p "$dir"
terraform -chdir=terraform/personal providers mirror -platform=linux_amd64 "$dir"
terraform -chdir=terraform/github init -backend=false -input=false -lockfile=readonly
terraform -chdir=terraform/github providers mirror -platform=linux_amd64 "$dir"
echo "Mirrored tree:"
find "$dir" -type f | sort

missing=0
asserted=0
for lock in terraform/personal/.terraform.lock.hcl terraform/github/.terraform.lock.hcl; do
  if [ ! -r "$lock" ]; then
    echo "MIRROR_MISSING lockfile-unreadable $lock"
    exit 1
  fi
  providers=$(awk -F'"' '/^provider /{a=$2} /^  version /{print a, $2}' "$lock")
  while read -r address version; do
    [ -n "$address" ] || continue
    asserted=$((asserted + 1))
    ns=${address#registry.terraform.io/}
    name=${ns##*/}
    found=$(find "$dir/$address" -type f -name "terraform-provider-${name}_${version}_linux_amd64*" -print -quit 2>/dev/null || true)
    if [ -z "$found" ]; then
      echo "MIRROR_MISSING $ns $version"
      missing=1
    fi
  done <<<"$providers"
done
if [ "$missing" -ne 0 ] || [ "$asserted" -lt 3 ]; then
  echo "not publishing an incomplete mirror" >&2
  exit 1
fi

aws s3 sync "$dir" "s3://agent-platform-data-lake/${prefix}/" --delete
echo "Published mirror to s3://agent-platform-data-lake/${prefix}/"
