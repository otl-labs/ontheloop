# terraform/bootstrap -- directory-scoped rules

Loaded automatically when Claude reads or edits files in this directory. Universal rules in
repo-root `CLAUDE.md` and `terraform/CLAUDE.md` still apply.

This module owns the `github_ci_apply` IAM role and its permissions boundary (authority budget),
isolating the apply role's own IAM from `terraform/personal/` (CD.35 Wave 4 / T2.23). This breaks
the self-grant cycle: the CD pipeline can no longer write the policy that governs the pipeline.
It also owns the platform-security-* IAM-change detector (Decision 202) and, since
PLAN-platform-iam-bootstrap-adopt, PlatformDev and PlatformAdmin with their five inline policies
-- see `docs/contracts/deploy-paths.yaml`'s `admin_out_of_band.procedure`.

## NEVER auto-apply this module

The `terraform-apply-sandbox.yml` path filter is `terraform/personal/**`. This module
(`terraform/bootstrap/**`) is intentionally excluded and must NEVER be added to any auto-apply
workflow or the `terraform_apply_guard.py` guard path. Apply this module manually, by hand, every
time, using the `agent_platform_admin` profile (PlatformAdmin, `iam:*`).

## Prerequisites

- Terraform 1.10+ (`cat config/terraform-version`)
- AWS credentials for the `agent_platform_admin` profile (PlatformAdmin, `iam:*`).
- `account_id`, `owner_email`, `platform_dev_external_id` and `platform_admin_external_id`
  (sensitive, no default) via `-var` or a gitignored tfvars file; recovery:
  `docs/contracts/terraform-cc-web-operations.yaml`.

## One-time provisioning

### 1. Create the dedicated state bucket

The bootstrap state bucket must exist before `terraform init` can succeed. Create it once:

```bash
aws s3api create-bucket \
  --bucket agent-platform-bootstrap-tfstate \
  --region eu-west-2 \
  --create-bucket-configuration LocationConstraint=eu-west-2 \
  --profile agent_platform_admin

aws s3api put-bucket-versioning \
  --bucket agent-platform-bootstrap-tfstate \
  --versioning-configuration Status=Enabled \
  --profile agent_platform_admin

aws s3api put-bucket-encryption \
  --bucket agent-platform-bootstrap-tfstate \
  --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' \
  --profile agent_platform_admin

aws s3api put-public-access-block \
  --bucket agent-platform-bootstrap-tfstate \
  --public-access-block-configuration \
    'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true' \
  --profile agent_platform_admin
```

### 2. Initialise

```bash
terraform -chdir=terraform/bootstrap init \
  -backend-config="region=$(tr -d '[:space:]' < config/aws-region)"
```

Region and re-init rule: `docs/contracts/terraform-cc-web-operations.yaml` `provider_init_and_mirror.backend_region`.

### 3. Prepare variables

Create a gitignored tfvars file (NEVER commit this file):

```bash
# terraform/bootstrap/terraform.bootstrap.tfvars  (gitignored -- NEVER commit real values)
account_id  = "<12-digit-account-id>"  # aws sts get-caller-identity --query Account --output text --profile agent_platform_admin
owner_email = "<owner-email>"
platform_dev_external_id   = "<value>"
platform_admin_external_id = "<value>"
```

### 4. Verify the import plan -- STOP if plan shows replace or destroy

The `import {}` blocks in `github_ci_apply.tf` adopt the live role and inline policy without
recreating them. Before applying, verify the plan shows no `replace` or `destroy` for
`aws_iam_role.github_ci_apply`, `aws_iam_role_policy.github_ci_apply`,
`aws_iam_policy.github_ci_apply_reads` or `aws_iam_role_policy_attachment.github_ci_apply_reads`:

```bash
terraform -chdir=terraform/bootstrap plan \
  -var-file=terraform.bootstrap.tfvars
```

STOP on any `destroy`/`replace` for those four. Destroying the role breaks all in-flight CI jobs;
dropping the reads policy or its attachment strips the role's whole refresh-read surface, so every
later CD plan -- reconcile included -- AccessDenies before the guard runs (Decision 156 point 2).

### 5. Apply

```bash
terraform -chdir=terraform/bootstrap apply \
  -var-file=terraform.bootstrap.tfvars
```

## Releasing a dual-declared address from terraform/personal

After a bootstrap import succeeds (no replace/destroy), release the address from personal with
`removed` blocks, never `state rm` -- see `docs/contracts/deploy-paths.yaml`.

## Ongoing apply runbook

Any change to `github_ci_apply.tf` (e.g. adding a new IAM action to the inline policy or
adjusting the permissions boundary) must be applied manually under `agent_platform_admin`:

PRE-APPLY: if this apply NARROWS the grant surface, first drain any dependent in-flight
terraform/personal destroy or gated apply (`deploy-paths.yaml#admin_out_of_band.procedure`).

```bash
# 1. Plan -- review all IAM diffs carefully.
terraform -chdir=terraform/bootstrap plan \
  -var-file=terraform.bootstrap.tfvars

# 2. Apply -- only after plan is confirmed safe.
terraform -chdir=terraform/bootstrap apply \
  -var-file=terraform.bootstrap.tfvars
```

Never add this module to `terraform-apply-sandbox.yml` or any other auto-apply workflow.
