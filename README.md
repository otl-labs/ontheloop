# On The Loop

This file is a curated projection of `CLAUDE.md`, `docs/ROADMAP-PLATFORM.yaml` and `docs/DECISIONS.md` - the canonical sources of truth for this repository. Where this file conflicts with those sources, the canonical sources win. For agent consumption, load those files directly.

## North Star

- **NS.1 - Storage is durable; compute is interchangeable.** S3 + open table format at every scale from GB to PB (DuckLake for the operational data plane per Decision 78). Engines are swappable per workload; data is not migrated to swap engines.
- **NS.2 - Account ownership reflects IP ownership.** Personal AWS account, not shared work account. AUP, IP, and business-continuity reasons; cost is a tiebreaker.
- **NS.3 - Compute lives where it economically belongs.** Hybrid by design. Cloud for orchestration and state; local rig for CPU-bound batch where home hardware otherwise idles. (cloud tier; the free tier runs orchestration and state locally, Decision 184 clause 2)
- **NS.4 - The repo is for agents.** Documentation, configuration, and tooling are optimised for agent consumption. Narrative prose is a side effect, not an output.
- **NS.5 - Self-describing typed tools over HTTPS, schema-as-code.** Agent surface is verbs (log, update, query, describe) exposed via Lambda Function URLs with AWS_IAM auth. Schema is Pydantic; YAML is generated where needed. (cloud-tier adapter; the free tier reaches the same verbs in-process, Decision 184 clause 2)

## What This Repo Is

This is a platform repo, not a product repo. It provides the control plane, automation infrastructure, and governance framework on which products are built. The platform roadmap - tier items, candidate decisions, North Star - lives in `docs/ROADMAP-PLATFORM.yaml`.

Tenancy is Decision 200's (`tenant_id` is the customer boundary, `project_id` repository identity); the planes are Decision 209 clause 2's (a multi-tenant, metadata-only control plane over customer-owned data planes).

## Product Direction

Decided, not yet built; the build items are `deferred_post_mvp`, and the Decisions and tier items are canonical.

- **The Signaller first** - an engineer who supervises the loop and hosts On The Loop for a team (Decision 232 clause 2; Decision 233 clause 3).
- **One supervision surface** - one web UI over the engine's named verbs and GitHub, CLI plus localhost first (Decision 232 clause 3; T4.33).
- **Dependency map headline** - work-item dependency mapping at the level of milestones and gates, in a visual language inspired by intuitive relationship mapping and schematic network diagrams; `docs/contracts/visual-grammar.yaml` owns its grammar (Decision 232 clause 5; T4.35).
- **Customer-owned open core** - scale is never blocked, and the tooling that makes it painless is paid; "free" is price only and sets no licence term (Decision 233 clauses 1-2).
- **Local sandboxed persona host** - the default local host: each persona run gets its own git clone under the open-source sandbox-runtime on the user's own machine (Decision 234 clause 1; T4.34).

## Platform Subsystems

| Subsystem | Status | Notes |
|-----------|--------|-------|
| Recommendation + Decision Governance | [live] | Append-only ops lakehouse; single portal invariant enforced by validate.py |
| CI / OIDC | [live] | GitHub-hosted runners; OIDC to personal AWS account; validate.py is the single gate |
| Instruction Architecture (5-layer) | [live] | CLAUDE.md -> PROJECT_CONTEXT.md -> commands -> skills -> executor prompts |
| Environment Taxonomy | [live] | Platform environments (sandbox/SIT/PROD) and their apply guards; defined by Decision 77 |
| Autonomous Executor | [partial] | repo-owned loop definition (ASL subset) hosted on Step Functions; executor freeze active pending CD.17 / T4.2 reversal |
| Scheduled Agents | [partial] | Lambda dispatcher disabled May 2026; migrating to Claude Code scheduled-agent model |
| Lambda Tooling Platform | [partial] | Per-Lambda manifests and Function URL auth live (Decision 79); the rendered loop definition on Step Functions is T4.19 / T4.1 |
| DuckLake Lakehouse | [live] | DuckDB + DuckLake catalog on Neon; sole backend for the ops tables (Decision 84) |
| Verification / Validation Kernel | [partial] | Typed checks kernel and graduation registry live (T3.1); causal-chain verifier next (T3.2) |

## Documentation Model

| Content Type | Current Form | End-state |
|--------------|-------------|-----------|
| Agent-instruction files | Markdown (CLAUDE.md, AGENTS.md) | Stays markdown per CD.20 / CD.23 - canonical agent artefacts |
| Operational decisions, recommendations, session logs | Decisions in git (docs/DECISIONS.md), projected to the lakehouse; recommendations lakehouse-only with no local cache (Decision 215) | Decisions stay in git in every tier (Decision 196 clause 1); recommendations stay lakehouse-only; the session log is retired (T3.20 c6): session history is read from telemetry through the telemetry reader |
| Plans | Schema-validated YAML (docs/plans/PLAN-*.yaml), T1.11 | Stays YAML; the retired .md format is deleted (Decision 85, amended by Decision 174) |
| Contracts | YAML (docs/contracts/) | Canonical field semantics and enforcement pointers; standing prose-architecture docs are forbidden per Decision 86 |
| Human portal files | Markdown (README.md, AGENTS.md, SECURITY.md, LICENSING.md, CONTRIBUTING.md, COMMERCIAL-LICENSE.md) | Stays markdown per CD.20 / CD.23 - portal files declare projection status; the licence-and-attribution artefacts are a named permanent prose class (Decision 171) |

## Repo Layout

| Path | Purpose |
|------|---------|
| `CLAUDE.md` / `AGENTS.md` | Universal agent rules and role definition; ambient-loaded every session |
| `SECURITY.md` | Vulnerability reporting policy |
| `src/` | Lambda handlers and shared Python modules |
| `scripts/` | Operational scripts - preflight, ops portal, validate, build-lambda |
| `config/` | Agent-consumed configuration: data quality, executor prompts, lambda manifests |
| `terraform/` | Infrastructure-as-code for personal AWS account |
| `docs/` | ROADMAP-PLATFORM.yaml, contracts, plans, decisions |
| `bin/` | Platform helper scripts including venv-python wrapper |
| `tests/` | Pytest test suite |
| `.claude/` | Harness artefacts: commands, skills, hooks, settings |
| `.github/` | CI workflows and OIDC configuration |

## Agent Workflow Entry Points

- `/orient` - defined in `.claude/commands/orient.md`; read-only orientation that ranks what to work on next
- `/plan` - defined in `.claude/commands/plan.md`; clarifies intent, runs preflight, produces `docs/plans/PLAN-{slug}.yaml` on the harness-assigned session branch
- `/implement` - defined in `.claude/commands/implement.md`; reads a completed plan file and executes all steps through verification, code review, and PR merge

## Canonical Sources

- [CLAUDE.md](CLAUDE.md) - universal agent rules and operational constraints
- [AGENTS.md](AGENTS.md) - role, environment, code style, safety, and branching rules
- [docs/ROADMAP-PLATFORM.yaml](docs/ROADMAP-PLATFORM.yaml) - tier items, candidate decisions, North Star, cost projection
- [docs/DECISIONS.md](docs/DECISIONS.md) - ratified decisions, the rationale behind the Decision numbers cited here
- [SECURITY.md](SECURITY.md) - vulnerability reporting
- [EVALUATION-PROMPTS.yaml](EVALUATION-PROMPTS.yaml) - evaluator guided-tour index: 12 architecture/governance questions with answer-loci into canonical sources

## License

Copyright 2026 Benjamin Blake.

This repository is source-available under the **Business Source License 1.1**. See [LICENSE](LICENSE).
Non-production use - evaluation, research, internal development and testing - is permitted by that licence; production use
before the Change Date (2030-08-15) requires a commercial licence, see [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md).
On the Change Date each version converts to the Apache License, Version 2.0.

The licence change is **forward-only**: every version published up to and including commit `4de9df8` remains under
Apache-2.0 irrevocably, and that text is preserved in [LICENSE-APACHE](LICENSE-APACHE). See
[LICENSING.md](LICENSING.md) for the boundary and its self-verifying rule.

BUSL-1.1 is not an OSI open-source licence. GitHub's licence detector does not recognise it, so this repository may display
as unlicensed there; that is a detector limitation, not a statement about the licence.

Contributions require an assignment of copyright or an equivalently broad grant, because dual licensing depends on sole
ownership and GitHub's inbound=outbound terms do not supply it. See [CONTRIBUTING.md](CONTRIBUTING.md).

Third-party dependencies remain subject to their respective licenses.

Project names, trademarks, service marks, and logos are not granted under this license. All associated trademark rights are
reserved.
