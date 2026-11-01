# Terraform — directory-scoped rules

Loaded automatically when Claude reads or edits files in this directory. Universal rules in repo-root `CLAUDE.md` still apply.

Some rules below restate root rules for proximity. Root `CLAUDE.md` is authoritative if they ever drift.

## Hard rules
- **Optional artifacts**: `try()`-wrap `filemd5()`/`file()`. Terraform never reads a build artifact (no `source_code_hash`; guard `validate_terraform_no_build_artifact_reads`; environment-taxonomy.yaml conformance).
- **Tag value charset**: Every literal tag value must stay inside the S3 TagValue charset -- letters, digits, whitespace, and `+ - = . _ : / @` only. No parentheses, no commas, no em dashes; use plain ASCII hyphens. S3 is the strictest AWS tag-value charset in this repo's tree (other services tolerate parentheses/commas), so authoring to the S3 charset everywhere is the safe default rather than tracking a per-service exception. Enforced by `validate_terraform_tag_charset` (scripts/checks/iam_tf/).
- **Plan before apply**: Plans modifying `.tf` files must present `terraform plan` output to the human before any `terraform apply`. Apply model: see `environment-taxonomy.yaml` Axis A + Guard classification subsection (sole SoT, Decision 77). Short form: sandbox auto-applies behind the deterministic guard; in-budget IAM inline-policy/attachment UPDATEs on managed boundary-carrying roles now auto-apply (T2.25); trust/destroy/out-of-budget IAM route to gated-apply. SIT/PROD remain human-gated and are future-state. See `planning` skill, Step 4 (Infrastructure Assessment).
- **IAM precedence**: If a change modifies IAM (`*.tf` IAM resources or roles attached to Lambdas), `terraform apply` must precede any Lambda code deploy.

## AWS context
- Region: `eu-west-2`
- Account: personal platform account (ID supplied via gitignored `terraform/personal/terraform.personal.tfvars`; never committed).
- Profile: `agent_platform` (PlatformDev, runtime) for agent operations; `agent_platform_admin` (PlatformAdmin) for provisioning (creates IAM + OIDC).
- Personal-account infra lives in the isolated `terraform/personal/` root module (own provider + state); it is the only live root module. The legacy work-account root's `.tf` files (retained for a while as architectural-evolution artefacts per CD.21, never applied) were removed in the platform cleanse.
- The personal account has no SCP restricting IAM users or external OIDC (Decisions 36/37 do not apply to this account). OIDC provider + CI roles are created in `terraform/personal/oidc.tf`.

## Running terraform/personal/ on CC-web
**This project runs ONLY on Claude Code on the web. There is no operator local machine.**

Read `docs/contracts/terraform-cc-web-operations.yaml` (provider-mirror init, remote-state variable
recovery, the operator-only break-glass loop) and `docs/contracts/terraform-personal-cd.yaml` (the
speculative-plan + apply-the-saved-plan pipeline, alarm-only drift detection, the convergence anchor)
before touching `terraform/personal/`. This stub carries only what the ROUTINE (non-admin) loop
needs unaided.

**Agents never run terraform apply as a self-directed, routine action; operators may always invoke
it directly. The sole agent exception is the human-gated break-glass admin tier in
`docs/contracts/terraform-cc-web-operations.yaml` -- an agent may execute `terraform apply` there
only after a human has reviewed the plan and explicitly directed it (Decision 126).**

**Deployment model (Decision 126):** the PR -> CI apply pipeline below is the default, ambient
path. Local/manual apply is operator-only break-glass, not a routine agent action -- see
`docs/contracts/terraform-cc-web-operations.yaml`'s operator-only / break-glass section. Full
intent -> trigger -> recovery wayfinding: `docs/contracts/deploy-paths.yaml`. Guard classification
(what auto-applies vs what blocks) is authoritative SOLELY in `environment-taxonomy.yaml` Axis A +
Guard classification (T2.25 / Decision 92 point 5) -- the table below names the channel, it does
not restate that classification.

| Want | Do | If blocked |
|---|---|---|
| Apply a guard-PASS (non-IAM or in-budget IAM) `terraform/personal` change | Open a PR; CD (`terraform-apply-sandbox.yml`) plans on the PR and applies the SAME reviewed plan.bin at merge (T2.21, no re-plan) | N/A -- this is the primary path |
| Apply an out-of-budget IAM / trust / destroy `terraform/personal` change | Open a PR; CD routes to the `tf-gated-apply` GitHub Environment | Approve in GitHub Actions (benjamin-blake); CD applies the same reviewed plan.bin -- never from a laptop |
| Recover from a red convergence record (failed/refused apply, or drift) | Red commit's HCL still right: dispatch Reconcile (`reconcile.yml`). Fix merged after the red commit: `terraform-apply-sandbox` `workflow_dispatch` acknowledge-and-retry. Rule + admin split-apply (never for PlatformDev/PlatformAdmin IAM, a `terraform/bootstrap` apply): `docs/contracts/deploy-paths.yaml` reconcile row | None |
| Apply `terraform/` (legacy hashicorp/*-only roots) or `terraform/github` | Same PR -> CD path where a workflow exists | See `docs/contracts/terraform-cc-web-operations.yaml`'s operator-only / break-glass section |
| Apply `terraform/bootstrap`, or apply `terraform/personal` by hand (bootstrap, reversing a manual admin change, or a guard-BLOCKed case with no CD path yet) | Operator action only | See `docs/contracts/terraform-cc-web-operations.yaml`'s operator-only / break-glass section |

## Out-of-band IAM grants (drift -- not managed by this module)

`PlatformDev`, `PlatformAdmin` and their five inline policies are codified in `terraform/bootstrap/platform_roles.tf`
and its `platform_*_policies.tf` siblings (admin-applied, Decision 126); terraform/personal keeps only their
`removed` blocks (rec-3838). The one grant applied out-of-band via the `platform_breakglass` IAM user (full admin) and codified in
neither root is PlatformDev's redundant `AgentPlatformRuntime` inline policy (slated for removal) -- re-creating infra
elsewhere will not restore it; reapply manually if needed.

- **`PlatformAdmin` (CODIFIED 2026-05-29):** `max_session_duration = 3600`; `AdminOps` (identity admin -- `iam:*` +
  admin Lambda + secretsmanager) and `PlatformDataLakeProvisioning` (the data-plane rights AdminOps lacks, so an
  `agent_platform_admin` apply can provision + manage the data lake and counters table); its other inline policies are
  `BootstrapStateProvisioning` and `DuckLakeBreakGlass`. The datalake grant is ENUMERATED least-privilege (no service
  wildcards; no legacy `bblake-platform-*` ARNs): `s3` bucket-config + object IO
  on `agent-platform-data-lake`, bucket management and object reads on `agent-platform-ducklake-catalog-dr`, and
  management of the `agent-platform-alerts` topic; DynamoDB TABLE-level actions (NOT item-level -- counter VALUES are PlatformDev
  runtime's domain) on `agent-platform-counters` only, mirroring the `github_ci_apply` CI role's data-plane statements.
  It includes refresh-time READS the AWS provider (v5.100) issues on every `plan` --
  `dynamodb:DescribeContinuousBackups`/`DescribeTimeToLive` -- which apply does not exercise but `plan` requires; do
  not prune them as "unused". The trust policy MUST show NO change in any plan (lockout guard -- the admin apply assumes
  this role). If a new data-plane action is needed, expect the FIRST `plan` after the apply to surface it as an
  AccessDenied refresh read; add it (scoped) and re-apply with `-refresh=false`, then a full `plan` converges.
- **PlatformDev runtime grant (CODIFIED 2026-05-29):** the `agent_platform` runtime role; `max_session_duration =
  36000` (the 3600 max blocked CC-web's 10h unattended sessions); `DailyOps` grants S3 read-write on
  `agent-platform-data-lake`, DynamoDB on `agent-platform-counters` and URL-only DuckLake verb invokes (Decision 213).
  Codification overwrote a stale pre-rename `DailyOps` (dead `bblake-*` targets + a Bedrock invoke-model grant,
  dropped as unused); the trust policy was verified unchanged.

Follow-up (remaining): remove the now-redundant `AgentPlatformRuntime` inline policy via `platform_breakglass`
(its grants are fully covered by the codified `DailyOps`). Decision 113 records the static-key credential model
(Pattern B: the two roles behind one static-key user, separated by ExternalId).

- **DuckLake IAM read-wildcard closure (PLAN-terraform-sandbox-convergence-closure, 2026-06-18; SSM List* completion PLAN-ci-apply-ssm-list-closure rec-2276, 2026-06-18, `github_ci_apply` inline policy, out-of-band admin apply):**
  The iterative-discovery anti-pattern for `github_ci_apply` refresh-READ grants (rec-2223 round, rec-2251 round) is
  permanently closed. Seven READ-only Sids use per-service wildcards (`Describe*/List*` or `Get*/List*`) scoped to the
  same resource ARNs as before: `CloudWatchLogsRead`, `LambdaRead`, `EventBridgeRead`, `SNSRead`, `CloudWatchAlarmsRead`,
  `SecretsManagerReadOnly`, `SSMParameterRead`. WRITE Sids (`EventBridgeWrite`,
  `CloudWatchAlarmsWrite`, `LambdaPermissionWrite`, `SSMFeatureFlagsManage`, `ConvergenceRecordWrite`,
  `IAMRoleReconcile`, `OIDCProviderReconcile`) remain enumerated and ARN-scoped (no wildcards). IAM read Sids
  (`IAMRolesRead`) remain enumerated per Decision 35 (policy defined in `terraform/bootstrap/github_ci_apply.tf`).
  `SSMParameterRead` grants `ssm:Get*/Describe*/List*` scoped to `parameter/agent-platform/*` (the original
  closure shipped `Get*/Describe*`; `ssm:ListTagsForResource` is a `List*`-class action the AWS provider calls
  on every `aws_ssm_parameter` refresh, surfaced by rec-2276 as a missed gap on the first apply-sandbox run
  under the `github_ci_apply` CI identity -- the SSM List* completion round landed with rec-2276).
  All seven READ Sids now use per-service wildcards covering all refresh-read actions (`Describe*/List*` or
  `Get*/List*` for six Sids; `Get*/Describe*/List*` for `SSMParameterRead`); no further iterative-discovery
  rounds are expected.

## Lambda interaction
- Lambda zipped deployment limit ~262144000 bytes. `scripts/build_lambda.py` asserts this.
- Lambda runtime: Python 3.12.

For Lambda deployment workflow rules, see `src/data/handlers/CLAUDE.md`.
