# Lambda handlers — directory-scoped rules

Loaded automatically when Claude reads or edits files in this directory. Universal rules in repo-root `CLAUDE.md` still apply.

## Lambda packaging contract
Files here are bundled into Lambda zip artefacts via `scripts/build_lambda.py`. Plans modifying any handler must include the build, deploy, and post-deploy verification sequence — not just code edits. The dispatcher and findings-processor are the two Lambda functions whose code is updated.

**Deploy channel class (Decision 125/126, T2.43):** the dispatcher and findings-processor
are `terraform/personal`-managed (`terraform/personal/prod_lambdas.tf`, the
`decoupled_build_pipeline` class) but code/infra-DECOUPLED from day one via
`lifecycle { ignore_changes = [source_code_hash] }` — distinct from the four
`terraform/personal`-managed DuckLake Lambdas (`ducklake_writer`/`ducklake_reader`/
`ducklake_maintenance`/`ducklake_catalog_dr`, under `src/lambdas/`), which decoupled later (#544)
after an initial coupled period. Do not conflate the two classes — see `src/lambdas/CLAUDE.md` and
`environment-taxonomy.yaml` (conformance).

**Routine deploy channel: `.github/workflows/deploy-prod-lambdas.yml` (T2.43)** — CI-woken, ships
the latest green commit (Decision 249) or `workflow_dispatch`. It assumes the merged
`agent-platform-github-ci-deploy` OIDC role (T2.49: UpdateFunctionCode-only on
`function:agent-platform-*`, shared with the DuckLake channel; no invoke, no terraform, no iam)
and runs `build_lambda --deploy`, then smoke-invokes both functions. The local
`bin/venv-python -m scripts.build_lambda --deploy` invocation below is now **admin break-glass
only** (mirrors the DuckLake class's break-glass posture) — it remains available as a
non-default fallback (see `docs/contracts/build-lambda.yaml` deploy_channels), not the routine
agent path. **Profile correction:** `agent_platform` (PlatformDev, routine dev/runtime) does NOT
hold `lambda:UpdateFunctionCode` on these functions — that lives on `agent_platform_admin`
(PlatformAdmin) and the merged `github-ci-deploy` OIDC role only. A break-glass local deploy must
use `--profile agent_platform_admin`.

### Required steps for Lambda-touching plans
1. **Build**: `bin/venv-python -m scripts.build_lambda`
2. **Deploy**: routine path is the governed workflow above. Break-glass (admin only): `bin/venv-python -m scripts.build_lambda --deploy --profile agent_platform_admin` uploads to S3 and updates Lambda function code.
3. **Smoke-test (post-deploy)**: `bin/venv-python -m scripts.run_scheduled_agent --smoke-test NAME` when the runner exposes it (grep for `_smoke_test` or `--smoke-test`). Otherwise an explicit `--trigger-lambda NAME` invocation with expected observable output.

If any of these are missing from a plan that touches handlers here, the plan is incomplete — flag it during `/plan` Step 4 (Lambda Deployment Assessment).

## Pipeline plumbing
- All handlers must accept a `force_{param}` event field for plan-driven re-runs.
- IAM-modifying plans: `terraform apply` must precede Lambda code deploy. See `terraform/CLAUDE.md` for the IAM precedence rule.
- Scheduled-agent handlers route by the `provider` field in `.github/agents/schedule.yaml` — not by `LLM_PROVIDER` env var.
- `agent-platform-data-lake` is the agent log bucket for cron workflows. Don't write to other buckets unless the plan explicitly says so.

## Model ID format reminder
Model IDs differ by provider -- e.g., legacy Copilot SDK IDs (`claude-haiku-4.5`, `claude-sonnet-4.6`) vs. GitHub Models IDs (e.g., `gpt-5-mini`). Do not interchange — see `docs/contracts/inference-provider.yaml` and Decision 116 (supersedes Decision 49).
