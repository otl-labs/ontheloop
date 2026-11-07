# terraform/github -- directory-scoped rules

Loaded automatically when Claude reads or edits files in this directory. Universal rules in
repo-root `CLAUDE.md` and `terraform/CLAUDE.md` still apply.

This module manages GitHub repository settings for `benjamin-blake/theseus` via the
`integrations/github` Terraform provider: GitHub Advanced Security (secret scanning + push
protection), the `main` branch-protection ruleset, Actions permissions, and the
`tf-gated-apply` GitHub Environment (CD.35 Wave 3 / T2.22 / Decision 92).

## CI apply (Decision 248)

`.github/workflows/terraform-github-apply.yml` applies this root on each push to `main` that touches it, and on a
dispatch from `main`. Its preview job plans read-only and lists each change with its allowlisted values. Its apply
job waits for one approval in `tf-gated-apply` (`tf-github-apply` after rec-4965), then plans with the apply App,
applies only changes the preview listed, and re-plans clean. It refuses any destroy or replace, any ruleset change,
and any change (creates included) to an Environment, branch policy or Actions permission that the dispatch's
`gate_change` input does not name; the preview fails on such a change before asking. Read the preview before
approving. Never add this root to another apply workflow: `terraform_apply_guard.py` cannot read `github_*` diffs.

## CloudShell break-glass

Only when that workflow cannot run, and only on the operator's word: apply from AWS CloudShell (outside the session
proxy) as `platform-breakglass`, with an apply-App token (the paste in
`docs/plans/PLAN-terraform-github-org-rekey.yaml`), init
`-backend-config="region=$(tr -d '[:space:]' < config/aws-region)"` and plan/apply
`-var="admin_bypass_actor_id=5" -var="gated_apply_reviewer_user_ids=[<id>]"`. STOP on a destroy or replace of
`github_repository`, a reset of description, homepage, topics, visibility or features, or a change to the
ruleset's required status checks.

## PR-time plan (rec-4335)

Every PR touching this root runs `.github/workflows/terraform-github-plan.yml`; its header holds the rules.

## tf-gated-apply Environment

The `tf-gated-apply` GitHub Environment (defined in `environments.tf`) gates the post-merge
Terraform apply job for the guard fail-closed set (IAM/trust/destroy diffs). After a change
in that set is merged to main, the `gated-apply` job in `terraform-apply-sandbox.yml` blocks
on Environment reviewer approval -- the job does not start until benjamin-blake approves via
the GitHub Actions UI. Until rec-4965 it also gates this root's CI apply job.

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
