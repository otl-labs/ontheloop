# AGENTS.md - On The Loop Platform

Universal rules. For full project context, see `docs/PROJECT_CONTEXT.md` on demand.

## PUBLIC repository / confidential-data boundary

This repository is PUBLIC (Decisions 73, 83, 101). Public-content boundary (Decision 101):
- NEVER publish: AWS account IDs or ARNs, IAM ExternalIds, credentials or API keys, private operational data, or any internal hostname that provides an attack surface.
- SAFE to publish: platform engineering, infrastructure patterns, tooling, CI/CD design, and general LLM-agent architecture -- publish the engineering, never the operational secrets.
- Confidential data lives ONLY in: the personal AWS account (Secrets Manager, gitignored tfvars, S3 private prefixes), and gitignored local files (e.g. `terraform/personal/terraform.personal.tfvars`, `~/.aws/credentials`). Nothing confidential is ever committed.
- The pre-commit `never-commit` hook (shape-pattern) blocks 12-digit AWS account IDs, secret-like strings, and ExternalId patterns from reaching the repo.

## Role and environment
You are a Lead Software Developer writing production-quality Python. Primary dev surface: Claude Code on the web
(CC-web; Ubuntu 24.04). Bash is the primary shell.
- **Deployment principles (AWS adapter):** one region, from its `project-configuration.yaml` row (today
  `config/aws-region`, Decision 202); every cloud resource is declared in IaC and CI-applied (Decision 126) -- the
  only out-of-band acts are secret values (Decision 237) and the operator admin tier (`deploy-paths.yaml`
  `admin_out_of_band`). A console-created resource is drift to import or file a rec for, never a planning input.
- **Claims cite evidence:** a status saying something was filed, deployed, sent, approved, merged or verified names
  its read-back (returned rec id, run id, message id, merge SHA, fresh-session result); otherwise it says started or
  expected. A cause not verified this session is labelled inferred. A manual step asked of the operator ends with
  the one check that proves it worked.
- **Hook canary (top-level interactive CC-web sessions only; not subagents, CI or scheduled agents):** if
  session-start context has no `[session_start_` line from this repo's hooks, the guards did not load: say "repo
  hooks not loaded" in your first status and write nothing until the operator resolves it.

## Code style
- Python 3.12+, type hints required, `async` for I/O.
- Every behavior-changing code, script, workflow, hook, or configuration edit must add or update
  an automated test that fails without the change; running an existing suite is not a substitute.
- ruff formatting; line length 127.
- No emojis in code, scripts, or documentation. Use plain ASCII hyphens (`-`) instead of em dashes.
- Default to no comments. Only add a comment when the *why* is non-obvious.
- Use Bash syntax in scripts; never emit PowerShell unless explicitly asked.

## Shell invocations
- Always invoke `bin/venv-python` instead of `python` or `python3`.
- Each Bash tool invocation is independent -- do not rely on `source .venv/bin/activate` or `source .venv/Scripts/activate`; use `bin/venv-python` directly instead.
- GitHub access from a session is via the `github` MCP server (`mcp__github__*`). The gh CLI may ship with the cloud image; new session-side scripts, checks, and verification-plan commands still prefer mcp__github__*, and CI runner steps may use `gh`.

## Safety
- Never `eval()` or `exec()`. Parse untrusted expressions with a restricted, purpose-built parser instead.
- Never raise exceptions during module import. Defer validation to explicit calls.
- Terraform never reads a build artifact (environment-taxonomy.yaml); `try()` file reads.
- Terraform apply model: see `environment-taxonomy.yaml` (Axis A + Guard classification subsection) -- that file is the sole SoT. Short form: sandbox auto-applies behind the deterministic guard (Decision 77); in-budget IAM inline-policy/attachment UPDATEs on managed boundary-carrying roles now auto-apply (T2.25 / Decision 92 point 5); trust/destroy/out-of-budget IAM route to the `tf-gated-apply` Environment. Bootstrap root is admin-only out-of-band.
- **Deployment model (Decision 126):** three agent intents, one trigger each -- infra = open a PR touching `terraform/**`, CI plans and applies; code = the governed code-deploy channel (Terraform not involved); red/drift = the heal verb `docs/contracts/deploy-paths.yaml`'s reconcile row selects. Agents never run terraform apply as a self-directed, routine action -- the sole exception is the human-gated break-glass admin tier (below/`terraform/CLAUDE.md`), where a human reviews and explicitly directs the apply before an agent executes it; operators may always invoke it directly. See `docs/contracts/deploy-paths.yaml` for the full intent -> trigger -> recovery index.
- **Lambda deploy channel (Decision 125/126):** the DuckLake Lambdas' code is decoupled from `terraform/personal` infra apply as of #544 (see `environment-taxonomy.yaml` (conformance) and `docs/contracts/build-lambda.yaml`'s `deploy_channels`) and deploy through a governed code-deploy CD channel; `bin/venv-python -m scripts.build_lambda --ducklake-only --deploy` is break-glass-only, never the routine default. See `docs/contracts/deploy-paths.yaml` for the authoritative channel status. Heuristic: when a production action (e.g. a Lambda code deploy) is auto-denied or has no obvious in-session path, check `docs/contracts/deploy-paths.yaml` first, then grep `.github/workflows/` for a governed CD path before falling back to a local permission grant.
- Windows subprocess: pass `encoding='utf-8', errors='replace'` with `text=True`. Use `sys.executable` - not the string `'python'` or `'pip'`.
- Scope boundary (LOCATION touched-files + CONTENT never-weaken invariants, evaluator `validate_scope_boundary`) is `docs/contracts/implement-scope-boundary.yaml` (Decision 59); out-of-scope bugs become recommendations via `scripts/ops_data_portal.py`, not inline fixes.
- Executor/adapter/port code: record cross-port couplings and repo-structure assumptions in `docs/contracts/ports.yaml` (Decision 212).

## SLOC governance -- decompose by default, don't raise (Decision 128, amends Decision 102)
- The 500-SLOC-per-file limit (`config/sloc_budgets.yaml`, `validate_sloc_limits`) is load-bearing (rationale: Decision 128) -- a raise is never a frictionless edit.
- **When a change pushes a file past its budget (or past 500 for an unregistered file), decompose it** into a facade package (Decision 80/104/124 pattern: `__init__.py` facade re-exporting the full public surface, cohesive submodules each under budget) -- this is the default response, not a raise.
- A budget raise is a deliberate, reviewable exception: the entry line in `config/sloc_budgets.yaml` must carry an inline `# raise-approved: dec-NNN <reason>` marker naming a real `## Decision NNN:` header that AUTHORIZES the entry (Decision 165) -- `validate_sloc_budget_raises` fails the PR on any unmarked increase, new >500-SLOC registration, or unauthorized marker; decreases and removals are always unrestricted.
- `--update-sloc-budgets` never auto-seeds a newly-oversized, unregistered file -- decompose it, or register it deliberately with the marker.

## Branching - never edit or commit on `main`
**Hard rule: on `main`, never `Edit`/`Write`/`MultiEdit`/`NotebookEdit` a file inside the repo, or run `git commit` / `git push`.** On CC-web you are already on the harness-assigned session branch (`git branch --show-current`); agents never create their own. The PreToolUse hook `.claude/hooks/never_on_main.py` enforces this; writes outside the repo (memory, scratchpad) and read-only commands pass.

## Temporary Operational Constraints
- **STRATEGIC plans suspended (Decision 67):** during this window all plans must be
  IMPLEMENTATION type -- STRATEGIC plan-type declarations are refused (enforced by /plan,
  the planning skill, and plan-critique). The planning skill's complexity heuristic (>5 files
  or >8 steps) is also suspended -- author the work as a single larger IMPLEMENTATION plan OR
  split it into multiple atomic IMPLEMENTATION plans yourself. Restores when CD.17 / T4.2
  reverses.
- **Lambda deployment -- per-Lambda gating (Decision 79):** only `status: active` artifacts need
  build + deploy + smoke-test (V3); see the planning skill's Lambda Deployment assessment.

## Memory policy - AGENTS.md is canonical persistence
AGENTS.md is canonical: durable knowledge lives in git-tracked AGENTS.md, the per-directory CLAUDE.md files and
`docs/DECISIONS.md`, never the auto-memory system (`~/.claude/projects/.../memory/`). When you learn something durable
or are told "remember this", **propose** the edit to the right file and let the user approve; never save silently.

## Agent-First Repository

This repository is designed for agent consumption. All artefacts -- docs, configs, YAMLs,
skills, slash commands -- are optimised for agent loading efficiency, not human readability.

- Prefer machine-parseable formats (YAML, structured tables) over narrative prose.
- Collocate semantic definitions with their enforcement counterparts in a single file.
  One file is better than two files covering the same subject from different angles.
- Narrative summaries are query results, not stored artefacts. When a human wants a
  summary, they query an agent -- do not store human-readable summaries as primary artefacts.
- Anti-pattern: creating a human-readable companion document alongside a machine-readable
  source. This produces a second surface that agents must sync, which is drift by design.
- When two design approaches are equally valid and one is more machine-parseable, choose
  machine-parseable.
- Precision Context Injection: call `get_rec_write_guidance()` before `file_rec()` so
  LLM-judgment fields (title, context, acceptance) get authoritative field semantics before
  composition, not as a post-rejection error (rationale: Decision 66).
- No new standing prose-architecture docs (Decision 86): forward intent to tier_items,
  rationale to Decisions, field semantics to contracts. Creating docs/INTENT-*.md or any
  equivalent standing prose-architecture doc under docs/ is forbidden.

## Skills and slash commands

Decision 90 four-tier workflow: `/orient` (read-only; writes nothing; picks the item) -> `/plan` (writes
`docs/plans/PLAN-{slug}.yaml`) -> `/implement` -> `/develop-executor` (frozen, Decision 67). `/audit` composes
`docs/audit-prompts/AUDIT-{slug}.md` and audits nothing. `/overseer` composes `/plan` and `/implement` subagents to
drive a roadmap item or audit; it is not a fifth tier, never bypasses the workflow or the Decision 67 freeze, is
human-gated and IMPLEMENTATION-only, and does not consume the recommendation queue. Each skill's description has
the rest.

When a slash command instructs you to "apply" or "invoke" a skill, use the `Skill` tool - never `Read` a `SKILL.md`.

## Operational data governance - Single Portal Invariant
All recommendation and decision writes go through `python -m scripts.ops_data_portal`. Never `Edit` or `Write` to `logs/.decisions-index.jsonl` directly -- `validate.py` will fail CI. Recommendations have no local cache (Decision 215): read them through the ducklake-reads named verbs. Recommendation IDs are allocated BY THE WRITER atomically with the insert (`file_ops`, Decision 84 I-2) -- never client-side; decision numbering authority is `DECISIONS.md` (callers supply `decision_id`). The local decisions JSONL is a read-only cache.

Agent surface is three functions: `file_rec`, `update_rec`, `sync`. Do not call `sync.ops` or any pull CLI directly. Portal calls require the `agent_platform` (PlatformDev) assume-role profile to reach the reader/writer Function URLs. If unreachable, confirm the chain with `bin/venv-python -m scripts.aws_identity caller-arn --profile agent_platform` (the session-start hook `.claude/hooks/session_start_aws.sh` reports this each session); a rotated `agent_static` key is updated in the environment's AWS network secret. There is no SSO login in the static-key model. A write that cannot complete FAILS LOUDLY at the call site -- there is no offline outbox (Decision 84 I-4); re-file after restoring connectivity.

## Warehouse-as-source-of-truth invariant
This is an append-only lakehouse: the warehouse is the single source of truth for operational data, and local files are never upstream of it.
- `ops_recommendations`, `ops_decisions`, `ops_priority_queue` live in DuckLake-on-Neon, the SOLE backend (Decision 84; no rollback flag). Reads use the closed `ducklake_reader` NAMED VERBS (no caller SQL); writes use `ducklake_writer`. `ops_decisions` rebuilds from `DECISIONS.md` via `ops_data_portal --backfill-decisions-md`.
- A local file's only role is a read cache (`logs/.decisions-index.jsonl`, rebuilt from the reader; recs have none). **Hard rule: a read cache is never a write source** -- replaying `logs/` into the warehouse resurrects rows (Decision 84). Legitimate writes: (a) `file_rec` / `update_rec`, (b) ETL from a non-warehouse source of truth (`DECISIONS.md` -> `ops_decisions`); review any other warehouse writer for replay-from-cache.
- Stale local data: rebuild with `bin/venv-python -m scripts.sync.ops sync`; never replay the local file upstream.

## Data-modeling default
Before designing any table, decide **grain first** -- "one row per ___" -- before picking a write
mode; never design "one row per entity, mutate in place." Full rules (write-mode branch, identity,
merge-on-business-key, partitioning) live in `docs/contracts/data-modeling-standard.yaml`'s
`#design_time_walk` -- this is the ambient trigger; read the contract at design time. The
planning skill's Data-Model Assessment section is only the read-trigger stub.

## Git-ops procedure
Read `docs/contracts/git-ops.yaml` before any push/PR/CI/merge action -- it carries the full
procedure (branching topology, commit-message conventions, rebase mechanics, wake-signal detail,
and the CI-credential/OAuth-token runbook); AGENTS.md keeps only the machine-enforced norms and
one-line triggers below.

- **Presubmit tier**: see the table below -- fast `--pre` gates PRs, full tier runs pre-handoff (local) + post-merge.
- **Squash-merge**: `mcp__github__merge_pull_request(..., merge_method="squash")` once CI is green.
- **Never-poll wake**: event-driven only (`subscribe_pr_activity` plus the CI-green/merge-conflict comment signals) -- never sleep/poll for CI or merge status.
- **Resolves: trailer**: when a plan bundles recommendations, name them (`Resolves: rec-NNNN[, rec-MMMM]`) in the squash-merge commit body to trigger `ci.yml`'s closure job.
- **Decision-record routing**: whether content clears the bar for a numbered Decision, and where it routes when it does not, is governed by `docs/contracts/decision-entry.yaml`'s `significance.routing_rule` -- never restated here.

### Two-tier presubmit model
| Tier | When | Command | Gate |
|---|---|---|---|
| Fast (`--pre`) | PR / edit loop | `bin/venv-python -m scripts.validate --pre` | Authoritative pre-merge gate when run by PR CI (Decision 73); advisory only when run outside CI |
| Full | Pre-handoff (local) + post-merge on `main` | `bin/venv-python -m scripts.validate` | See `## Merge protocol` (post-merge disposition). |

The full tier runs well past the 10-min foreground cap on CC-web: start it backgrounded with a 2 h limit from the
top-level session (a subagent never receives the completion) and say it started. Its verdict is
`bin/venv-python -m scripts.checks.validation_result --verify-head`, never a pipe's exit status.

### Commit signing (CC-web: SSH-signed via harness signer)
CC-web commits are SSH-signed and GitHub shows them Verified; a local `%G?` of `N`/`B` only means this container
cannot verify SSH signatures. `session_start_commit_signing.py` quiets the Stop hook's signature check; if it still
fires, only its committer-email half (`%ce != noreply@anthropic.com`) is real. Never reset-author or `--amend -S`.

## Merge protocol
- **Post-merge full-tier failure on `main`**: ci-rca files a `source=ci_rca` rec, `priority=critical` unless tests-only (High, fast lane); else `/plan` or a manual revert PR (forward-fix, never auto-revert; no inline workaround -- Decision 226, `red-main-recovery.yaml`).
- **PR-branch `--pre` failure**: per `docs/contracts/ci-rca-lifecycle.yaml` trigger_scope, no rec is filed and nothing gates -- ci-rca watches `main` only; diagnose and fix on the branch (Git-ops step 6).
- Manual confirmation: if `validate.py` appears to skip tests, run `pytest` directly to confirm.

## Instruction architecture
The layered contract (Layer 1 universal rules through Layer 5 executor prompts) is at
`docs/contracts/instruction-architecture.yaml` -- see it for the full layer table.

The `.github/prompts/scheduled/` and `.github/agents/schedule.yaml` surfaces are retained for
live scheduled agents.
