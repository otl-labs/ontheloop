# src/lambdas/ — directory-scoped rules

Loaded automatically when Claude reads or edits files in this directory. Universal rules in repo-root `CLAUDE.md` still apply.

## Deploy channel (Decision 125/126)

The DuckLake Lambdas here (`ducklake_writer`, `ducklake_reader`, `ducklake_maintenance`,
`ducklake_catalog_dr`) are `terraform/personal`-managed and now code/infra-DECOUPLED (#544:
`lifecycle { ignore_changes = [source_code_hash] }`). The governed code-deploy workflow is
`.github/workflows/deploy-ducklake-lambdas.yml`; see `docs/contracts/deploy-paths.yaml` for the
authoritative channel status. Local `bin/venv-python -m
scripts.build_lambda --ducklake-only --deploy` is break-glass only, not the routine channel.

No standing rationale here (Decision 86) — see `environment-taxonomy.yaml` for the
classification SoT and `docs/contracts/build-lambda.yaml`'s `deploy_channels` for the
artifact->channel mapping.

`data-pipeline` (dispatcher, findings-processor, workflow dispatcher) is `terraform/personal`-managed
(T2.43, `decoupled_build_pipeline`), decoupled; its code-deploy channel is
`.github/workflows/deploy-prod-lambdas.yml` — see `src/data/handlers/CLAUDE.md`.
