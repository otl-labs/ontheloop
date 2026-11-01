# terraform/github -- directory-scoped rules

Loaded automatically when Claude reads or edits files in this directory. Universal rules in
repo-root `CLAUDE.md` and `terraform/CLAUDE.md` still apply.

This module manages GitHub repository settings for `benjamin-blake/theseus` via the
`integrations/github` Terraform provider: GitHub Advanced Security (secret scanning + push
protection), the `main` branch-protection ruleset, Actions permissions, and the
`tf-gated-apply` GitHub Environment (CD.35 Wave 3 / T2.22 / Decision 92).

## NEVER auto-apply this module

The `terraform-apply-sandbox.yml` workflow path filter is `terraform/personal/**`. This module
(`terraform/github/**`) matches nothing in that filter and is intentionally excluded. Adding it
to any auto-apply workflow is FORBIDDEN (Decision 77 / `environment-taxonomy.yaml`):
`terraform_apply_guard.py` is AWS-IAM-only and cannot inspect `github_*` resource diffs; a
branch-protection change applied ungated could lock out the push-to-main flow the workflow depends
on. Apply this module by hand from AWS CloudShell, every time.

## Prerequisites

- Terraform 1.10+ (`cat config/terraform-version`)
- AWS CloudShell, as an identity that can read the PAT secret and write `tfstate/github/` (a Claude
  Code cloud session cannot: its proxy swaps the PAT for a token that gets 403).
- A GitHub admin PAT stored in Secrets Manager. Required scopes: `repo`, `admin:repo_hook`, `read:org`.

## Apply runbook

```bash
# 1. Export the GitHub PAT from Secrets Manager.
export TF_VAR_github_token=$(aws secretsmanager get-secret-value \
  --region eu-west-2 \
  --secret-id <GITHUB_PAT_SECRET_ARN> \
  --query SecretString \
  --output text)

# 2. Resolve the numeric GitHub user ID for the tf-gated-apply reviewer.
#    benjamin-blake's numeric ID (NOT the login string):
REVIEWER_ID=$(curl -s -H "Authorization: Bearer $TF_VAR_github_token" https://api.github.com/user | jq .id)
#    Or use the GitHub MCP get_me tool in Claude Code on the web.

# 3. Initialise (S3 backend in agent-platform-data-lake; region and re-init rule: see
#    docs/contracts/terraform-cc-web-operations.yaml backend_region).
terraform -chdir=terraform/github init \
  -backend-config="region=$(tr -d '[:space:]' < config/aws-region)"

# 4. Plan -- review carefully before applying.
#    STOP if plan shows any destroy/replace of github_repository, any unintended
#    in-place reset of description/homepage/topics/visibility/features, or any
#    change to the main-protection ruleset required_status_checks.
terraform -chdir=terraform/github plan \
  -var="admin_bypass_actor_id=5" \
  -var="gated_apply_reviewer_user_ids=[${REVIEWER_ID}]"

# 5. Apply -- only after plan is confirmed safe.
terraform -chdir=terraform/github apply \
  -var="admin_bypass_actor_id=5" \
  -var="gated_apply_reviewer_user_ids=[${REVIEWER_ID}]"
```

## PR-time plan (rec-4335)

Every PR touching this root runs `.github/workflows/terraform-github-plan.yml`; its header holds the rules.

## tf-gated-apply Environment

The `tf-gated-apply` GitHub Environment (defined in `environments.tf`) gates the post-merge
Terraform apply job for the guard fail-closed set (IAM/trust/destroy diffs). After a change
in that set is merged to main, the `gated-apply` job in `terraform-apply-sandbox.yml` blocks
on Environment reviewer approval -- the job does not start until benjamin-blake approves via
the GitHub Actions UI.

Key constraints:
- `protected_branches = true` may not restrict to main under the ruleset-only main.
- The reviewer ID comes from `gated_apply_reviewer_user_ids` at apply time, never a literal default.

deployment-approval-probe: inert rec-4695 probe Environment, created in the UI and imported; see its block in `environments.tf` and the workflow header.

## Lockout recovery

If the ruleset is applied with incorrect required-status-check names and merges are blocked:
1. Use the bypass actor (repo Admin role, actor_id=5) to merge a fix branch directly, OR
2. Disable the ruleset via the GitHub UI: repo Settings -> Rules -> Rulesets -> main-protection -> Disable.

## State reconcile (rec-3136)

State is keyed by repository name. After a rename or transfer, never apply first: run
`terraform state rm github_repository.this` and every other address, then apply the
`imports.tf` re-import (one block per resource, 0 to change). Plan: `docs/plans/PLAN-terraform-github-state-reconcile.yaml`.

## Ruleset check-run names

The required status checks are pinned to the LIVE check-run names (`pr-validate`,
`terraform-validate`) from `.github/workflows/ci.yml` jobs. Do not change these to the
placeholder `validate (pre)`. A wrong name silently provides no gate or locks out all merges.

## Fork pull request approval (manual step post-apply)

The `github_workflow_repository_permissions` resource sets the default workflow token to read-only
and disables GitHub Actions PR self-approval. The strict "fork PR approval = all outside
collaborators" policy is NOT exposed by the Terraform provider v6 and must be set manually
after apply:
- Repo Settings -> Actions -> General -> Fork pull request workflows from outside collaborators
- Select: "Require approval for all outside collaborators"
