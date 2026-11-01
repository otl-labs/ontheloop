# On The Loop - Agentic Software Engineering Platform

Canonical Layer 2 project knowledge base, loaded on demand by workflows; keep rules in `CLAUDE.md` / `AGENTS.md`, workflow method in `.claude/commands/` and `.claude/skills/`, and machine semantics in `docs/contracts/*.yaml`.

Source stamp: ROADMAP-PLATFORM.yaml @ 8eb0d35afc9a02fa2f660b61176d12aedb395094; roadmap_tier_id_set sha256: 003307ba66a82014514bf9f23de7d69978b4af89468285b261f295d6df54b438

## Operating contract

- Repository visibility: public. Never commit credentials, API keys, AWS account IDs, IAM ExternalIds, ARNs, internal hostnames, or confidential operational data; AGENTS.md's public-content boundary (Decision 101) says what is safe.
- Runtime surface: Ubuntu 24.04 / bash / Python 3.12+, always through `bin/venv-python`, never `python` or `python3`.
- Branching: never edit or commit on `main`. Routine handoff is commit -> PR -> CI -> merge.
- Terraform and Lambda deploys: agents take the governed path `docs/contracts/deploy-paths.yaml` selects; local apply/deploy is break-glass only, after explicit human direction.

## Platform thesis

This repository is an agentic software-engineering platform. Its purpose is to let LLM agents plan, build, verify, deploy, monitor, and improve software safely and reliably with minimal human oversight.

The platform is the governed harness around model intelligence. Models, compute providers, tools, and hosted products are replaceable. The durable product is the control plane that gives agents bounded authority, typed capabilities, reliable state, independent verification, deployment controls, operational memory, and evidence-backed feedback loops.

The platform end-state is a public, agent-first automation platform with:

1. durable operational data as the source of truth;
2. swappable models and compute selected by workload;
3. typed tool and agent surfaces instead of ad hoc scripts;
4. specialised planning, critique, implementation, review, and analysis personas;
5. governed CI/CD, deployment, and progressive authority controls;
6. warehouse-backed recommendations, decisions, queue state, execution evidence, and telemetry;
7. causal verification connecting an agent action to its persisted and observed outcome;
8. a closed improvement loop that can complete one bounded iteration without a human in the critical path;
9. portability across repositories, products, infrastructure, and regulated domains.

## Recursive self-improvement model

The platform is a governed, recursively self-improving software-engineering system. Verification recursively improves, but verification is not the only improvement target.

The complete loop is:

```text
observe development and runtime behavior
  -> identify defects, friction, cost, latency, risk, and opportunity
  -> create a bounded, evidence-linked recommendation
  -> prioritise and admit work under explicit policy
  -> plan, critique, implement, and review the change
  -> obtain an independent CI/CD verdict
  -> deploy through the governed channel
  -> measure the resulting behavior
  -> retain evidence and improve the next iteration
```

Telemetry provides learning signals about the whole engineering system: agent behavior, planning quality, review churn, tool failures, context quality, model cost and latency, CI performance, deployment outcomes, monitoring gaps, and incidents. Analysis agents convert those signals into bounded recommendations that may improve verification, prompts and personas, planning and review, tools and orchestration, CI/CD and observability, data architecture, platform implementation, and hosted products. At maturity, outcome telemetry and product metrics improve both the process that produces software and the software it produces.

Recursive improvement remains bounded. A signal is not proof, a recommendation is not authority, and a metric movement is not automatically a beneficial outcome. Agents may propose and implement changes, but changes gain authority only through explicit policy and independent evidence. Verification is the safety envelope and admission mechanism for recursive self-improvement, not its scope limit.

## Commercial and product boundary

The primary category is agentic software-engineering infrastructure, adjacent to AI assurance, autonomous operations, and regulated AI control planes.

The core value proposition is not an agent that writes code but the harness that makes code-writing and operations agents governable, inspectable, testable, portable, and safe enough for production-adjacent work, traceable from work selection through agent action, verification, deployment, and observed outcome to the next improvement.

Hosted products consume but do not define the platform; none is sequenced here, and each keeps its IP, data, domain logic, and risk controls behind its product boundary, outside this platform-only repository (Decision 209 clause 3).

Open core (Decisions 184, 209, 233): the free tier is customer-owned -- a laptop by default, or the customer's own cloud through the public adapters. Scale is never blocked; the tooling that makes it painless is paid, and "free" is price only (Decision 233 clause 2, rewording 209 clause 1). Approval authority is GitHub; the free tier has no identity system of its own (233 clause 4).

Users and surface (Decision 232, whose clause 1 sets the persona and user-archetype vocabulary): the Signaller, an engineer who supervises the loop and hosts it for a team, is served first through one web UI over the named verbs and GitHub (T4.33); its headline is the milestone-and-gate dependency map (T4.35), whose grammar `docs/contracts/visual-grammar.yaml` owns. Both are deferred.

## Roadmap sources

- Platform roadmap: `docs/ROADMAP-PLATFORM.yaml`, the sole roadmap source for engineering work here.
- Marketing/comms roadmap: `docs/ROADMAP-SEMANTO.yaml` for the external brand surface, including the On The Loop brand-identity rows S1.10-S1.13. Every item is parked `deferred_post_mvp`; every brand value is a placeholder.
- Decision rationale: `docs/DECISIONS.md` plus `docs/DECISIONS_ARCHIVE.md`. Pending candidate decisions in the roadmap are binding until ratified or superseded.
- Contracts: `docs/contracts/*.yaml` and selected `.md` contracts are the preferred source for machine semantics. Do not duplicate contract truth in prose.

## Platform roadmap end-state map

### Foundation already shipped

The platform is in convergence and hardening rather than bootstrap. Its foundation includes the agent branch workflow, public-repo boundary, GitHub-hosted CI, secret guards, two-tier validation, the Single Portal Invariant, DuckLake reader/writer functions, schema-as-code, CI-RCA, governed deployment, and infrastructure guard classification. T2 is the active center of gravity because durable state and guard hardening block telemetry and executor work.

### Critical path to the autonomous loop

```text
T2.18 DuckLake maintenance
  -> T2.19/T2.26 ops-table migration tail
  -> T2.36 telemetry rebuild on DuckLake
  -> T3.2 telemetry causal-chain verifier
  -> T3.3 telemetry cloud analysis
  -> T3.4 control-plane loop closure
  -> T4.19-21 then T4.1 (definition, guard, SFN host)
  -> T4.2 Lambda Durable Function agent personas
```

Persona path (Decision 229: personas land before any loop host; the T4.2 umbrella completes with it):

```text
T4.32 persona foundation
  -> T4.28-T4.30 verdict personas on Actions
T4.19-21 + T4.32
  -> T4.15 implement_agent on Fargate
  -> T4.31 plan_agent on Fargate
```

Parallel governance path:

```text
T1.5 ops_decisions graduation
  -> T1.6 move live-reader DQ from merge gate to monitor
  -> T4.2 executor persona readiness
```

Queue-feed path:

```text
T2.26 migrated ops queue substrate
  -> T4.3 priority-queue producer repoint to DuckLake
  -> T4.12 scheduled-agent re-enable/repoint
```

Current constraints are the temporary strategic/executor freeze, telemetry blindness before T2.36/T3.2/T3.3, the queue producer gap before T4.3, and missing executor evidence. Work remains IMPLEMENTATION-only until the documented reversal conditions hold.

## Operational data architecture

### Source of truth

Warehouse state is authoritative. The decisions JSONL under `logs/` is a read cache, never a write source; recommendations have no local cache (Decision 215). Recommendations, decisions, priority queue, and execution plans use DuckLake-on-Neon. Session log retired (T3.20 c6); preflight reads recent sessions from telemetry; telemetry re-lands as the T2.36 four-table DuckLake model.

### Portal discipline

Agent-facing operations are only `file_rec`, `update_rec`, and `sync`, through `scripts.ops_data_portal`; read recs via ducklake-reads named verbs. IDs are writer-allocated; there is no write-path cache, outbox, or staging substitute, and a failed write fails loudly.

### Data modeling default

State the grain first; `docs/contracts/data-modeling-standard.yaml` governs (SCD2 for mutable entities, append_only for events, never CRUD).

## Telemetry, learning, and verification

The canonical telemetry model is `telemetry_sessions`, `telemetry_observations`, `telemetry_transcripts`, and `telemetry_agents`.

T2.36 creates storage and write/read paths. T3.2 proves PRODUCE -> TRANSPORT -> PERSIST -> QUERY -> ASSERT. T3.3 analyzes anomalies, friction, cost, and failure trends. T3.4 turns verified analysis into control-plane work. T3.22 onboards further producers without bespoke work. T3.20 routes agent-turn/session capture into the same model and coordinates retirement or rewiring of legacy session-log surfaces.

Telemetry is evidence, not merely logging. Every meaningful stage should emit enough identity and lineage to connect recommendation, plan, agent invocation, code change, CI verdict, deployment, observation, and measured outcome. Analysis must separate correlation from causal evidence.

Verification is recursively improvable. Escaped defects, weak tests, missing guards, and monitoring gaps can generate recommendations for stronger verifiers. A proposed verifier must itself demonstrate useful discrimination before becoming authoritative, including differential or mutation evidence where appropriate. Weakening verification is human-gated at entry; a designed, not yet built review-ladder lane may later earn it autonomy, never through the executor's T4.4 gate alone, and dropping a check needs out-of-loop redundancy evidence, not a clean history (Decision 235). `scripts.validate` remains the single source of truth for CI checks: PRs run `--pre`; full validation runs before handoff and on main; new CI checks enter `scripts.validate` first.

## Agent and executor architecture

### Interactive workflow today

`/orient -> /plan -> /implement`; AGENTS.md's Skills section is canonical.

### Local executor status

The older local executor is frozen pending Decision 67 reversal; `config/agent/executor/capabilities.yaml` keeps sensitive runtime, verification, infrastructure, workflow, governance, and deployment targets protected until policy delegates them.

### Executor end-state

```text
DuckLake queue
  -> pick_rec admission guard
  -> loop definition (SFN host)
  -> prepare_workspace
  -> plan_agent -> draft plan PR
  -> scout + critique verdicts (Actions)
  -> critique_gate -> plan merge
  -> implement_agent -> draft PR
  -> code-review verdict (Actions)
  -> CI verdict callback
  -> merge
  -> deploy_dispatch
  -> emit_telemetry
  -> analyze outcome
  -> autonomy gate ratchet
```

T4.19 owns the loop definition; T4.1 its SFN host and glue. T4.9a owns the MVP GitHub Actions callback handshake. T4.10a owns persona contracts. T4.13/T4.14 add prompt-injection threat modeling and offline prompt/model regression tests.

Persona execution (Decision 234): on user-owned substrate the planning, implementation and consult personas default to a per-run git clone under sandbox-runtime (T4.34, deferred); verdict personas default to Actions. A subscription runs only work its owner initiated; a verdict persona on anyone else's pull request uses a team API key through LiteLLM, or its verdict fails. New core surfaces name a generic change id (ports.yaml PC-09 is the accepted pull-request coupling); local model servers (Ollama, vLLM) use LiteLLM with billing_shape local.

Authority increases only when evidence supports it. The end-state minimises routine oversight while preserving human policy control, auditability, escape hatches, and explicit boundaries for high-consequence actions.

## Scheduled analysis and CI-RCA

Scheduled agents are currently disabled. T4.3 first repoints rec-curator/priority-queue writes to DuckLake so generated work is visible to the executor. T4.12 then re-enables or repoints the five scheduled agents.

CI-RCA is one failure-to-work bridge: red main workflows generate evidence, deduplicate, analyze the failure, and file recommendations through the ops portal; telemetry analysis should likewise turn recurring friction into prioritised work. `/orient` surfaces CI-RCA items; Decision 226 clauses 3-4 (`docs/contracts/red-main-recovery.yaml`) decide when an unresolved red blocks new work.

## Infrastructure and deployment

The platform currently uses AWS `eu-west-2`, Python 3.12 Lambdas, GitHub-hosted CI with OIDC, DuckLake-on-Neon, and LiteLLM transport, all replaceable implementation choices. Routine production changes use governed GitHub workflows; admin access and local deployment are human-gated, break-glass only.

## File routing and operational quick reference

Use `docs/contracts/file-router.yaml` as the discovery and ownership index.

Recommendations are operational work items, not local JSONL edits; `get_rec_write_guidance()` carries their field semantics (Decision 66). Canonical status values are `open`, `closed`, `failed`, `declined`, and `superseded`. Acceptance proves structural change; verification proves behavior and may be warning-only depending on tier.
