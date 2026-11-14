#!/usr/bin/env bash
# Apply job body of terraform-bootstrap-apply.yml (PLAN-bootstrap-ci-gated-apply).
# Env: REVIEWED (the preview's reviewed set as JSON), PROVE_GATE (true stops before apply), GH_TOKEN, RUNNER_TEMP,
# GITHUB_WORKSPACE. Terraform text goes only through quiet_run.sh into a log that is never printed. Prints counts and
# tokens only: APPLY_REFUSED, DEPENDENT_RUN_PENDING, APPLY_DONE, REPLAN_CLEAN, SIMULATE_OWED, PROVE_OK.
set -euo pipefail

q="$GITHUB_WORKSPACE/scripts/ci/quiet_run.sh"
log="$RUNNER_TEMP/tf.log"
gate="$GITHUB_WORKSPACE/scripts/ci/terraform_bootstrap_apply_gate.py"
plan_json="$RUNNER_TEMP/apply.json"
tfvars="$GITHUB_WORKSPACE/terraform/personal/terraform.personal.tfvars"
trap 'rm -f terraform/bootstrap/apply.bin "$plan_json"' EXIT

bash "$q" "$log" -- terraform -chdir=terraform/bootstrap init -input=false -lockfile=readonly || { echo "APPLY_REFUSED init"; exit 1; }
bash "$q" "$log" -- terraform -chdir=terraform/bootstrap plan -input=false -no-color -var-file="$tfvars" -var aws_profile= -out=apply.bin || { echo "APPLY_REFUSED plan"; exit 1; }
bash "$q" "$log" --stdout "$plan_json" -- terraform -chdir=terraform/bootstrap show -json apply.bin || { echo "APPLY_REFUSED show"; exit 1; }

if [ "${PROVE_GATE:-false}" = "true" ]; then
    python3 -I "$gate" prove --plan-json "$plan_json"
    exit 0
fi

checked=$(python3 -I "$gate" check --plan-json "$plan_json" --reviewed "${REVIEWED:-}") || { echo "$checked"; echo "APPLY_REFUSED check"; exit 1; }
printf '%s\n' "$checked" | grep -v '^SIMULATE_OWED$' || true

case "$checked" in
    *NARROWS_PRESENT*)
        python3 -I "$gate" pending || { echo "DEPENDENT_RUN_PENDING"; exit 1; }
        ;;
esac

bash "$q" "$log" -- terraform -chdir=terraform/bootstrap apply -input=false -no-color apply.bin || { echo "APPLY_REFUSED apply"; exit 1; }
echo "APPLY_DONE"

rc=0
bash "$q" "$log" -- terraform -chdir=terraform/bootstrap plan -input=false -no-color -detailed-exitcode -var-file="$tfvars" -var aws_profile= || rc=$?
[ "$rc" = 0 ] || { echo "REPLAN_DIRTY exit ${rc}"; exit 1; }
echo "REPLAN_CLEAN"

case "$checked" in
    *SIMULATE_OWED*)
        echo "SIMULATE_OWED"
        echo "::warning::SIMULATE_OWED the operator runs the procedure's POST-APPLY simulate from CloudShell"
        ;;
esac
