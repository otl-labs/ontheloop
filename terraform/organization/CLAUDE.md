# terraform/organization -- directory-scoped rules

Loaded automatically in this directory; `terraform/CLAUDE.md` and the repo-root rules still apply. This root declares the
company AWS Organization (OUs, SCPs, root features) and its management account's admin lane, trail and alarms
(rec-5026, Decision 252 cl.7). `docs/contracts/deploy-paths.yaml` `admin_out_of_band.organization_provisioning` names it.

## Operator lane only (Decision 256 cl.3)
- Never auto-apply, never CI-apply, never run by an agent. No agent environment, key, profile or CI role holds a
  management-account identity. The operator signs in to the management account's console with MFA as the IAM user
  org-admin-operator, switches role to OrganizationAdmin, opens AWS CloudShell in that role and pastes a block an agent
  prepared. An agent plans and reviews; the operator's paste is the direction.
- Announce each sitting in the thread first: the role switch, the apply's policy writes and the seed alarm by design.
- A root-only task in a member account runs as a centralized root session from this account, never with root credentials.
- Emergency detach: `aws organizations detach-policy` from a role session, then a PR to match. Never edit a template to
  match a hand edit.

## Sitting order (steps 4-8 of PLAN-sit-organization)
1. Console acts: sign up the account (company root mailbox, root MFA, company payment) and create the SecureString
   `/secret/agent-platform-terraform-organization-tfvars` (alerts_email) in the config/aws-region region, typed in the console.
2. Step 4, once, as root with MFA: the seed block fetches the PR head by full sha, checks the three policy templates against the
   sha256 values the agent wrote into it, renders `${account_id}` and `${region}` with sed and creates user org-admin-operator,
   group agent-platform-org-admin-operators (policy AssumeOrganizationAdmin, with the user as a member) and role OrganizationAdmin
   (trust and OrganizationAdminOps). If CloudShell will not open for root, create the same entities in the IAM console from the
   head's templates. Then set the user's console password and
   a virtual MFA device in the console.
3. Step 5, pre-merge plan in the role session: identity, root hygiene, the state bucket's one CLI write, the plan counts and
   every metric filter pattern checked live against tests/fixtures/organization_security_filter_events.json.
4. Step 7, after merge: apply only the saved plan whose PLAN_DIGEST, root tree, terraform version and tfvars Version equal
   step 5's, then read back. Step 8: confirm the SNS subscription, switch role for real and read each alarm's metric.
- Stop rules: any line other than a block's expected ones, a delete or replace in a plan, or a refused verb is STOP and report;
  never widen a grant or attach a managed policy inline.

## Block conventions
- Public repo fetched at a full commit sha into `${TMPDIR:-/tmp}`, terraform checked against HashiCorp's SHA256SUMS,
  `TF_DATA_DIR` and the plugin cache under that folder, an EXIT trap removing it, errors to a file under /tmp read in
  CloudShell only, printed lines only.
- Every placeholder is filled by the agent before posting; an unfilled block prints BLOCK_NOT_FILLED and is never worked around.
- A role-session block prints `caller-arn check: ADMIN_CHAIN_OK`, the marker validate_admin_root_premerge_gate reads.
- Never print or commit an account id, e-mail, key or rendered policy.
