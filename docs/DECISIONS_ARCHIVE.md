# Decisions Archive

Superseded, historical empirical, and old infrastructure decisions archived from DECISIONS.md.

---

## Decision 69: Ops Pipeline Consolidation -- Single-Portal Invariant Enforced at Primitive Level (Superseded by Decision 78)

**Status:** Superseded by Decision 78
**Superseded by:** Decision 78
**Date:** 2026-05-09

**Problem:**
Five-CLI choreography (`update_rec`, `sync_ops drain`, `ops_writer --compact`, `ops_writer --refresh-views`, `sync_ops pull`) leaked internal pipeline layers to agents, enabling silent-failure composition. Root-cause analysis in `docs/INTENT-ops-pipeline-consolidation.md`. Three architectural failures composed into the 2026-05-09 incident: (1) `update_rec` read the existing record from JSONL (destructible cache) rather than from the warehouse (source of truth); (2) `OpsWriter.compact` swallowed credential errors as `return 0`, making failure indistinguishable from "no staging files"; (3) `sync_ops pull` overwrote the local cache destructively, silently discarding uncommitted writes.

**Decision:**
Three architectural fixes, enforced at the primitive level:
1. `update_rec` reads existing record from the warehouse `ops_recommendations_current` view (source of truth). Raises `RuntimeError` if the warehouse is unreachable; write path retains outbox for offline resilience.
2. `OpsWriter.compact` raises `RuntimeError` on infrastructure failures (credential errors, network errors, schema mismatches). Returns `int` only for the "no staging files" success case.
3. `ops_data_portal.sync()` is the single flush primitive -- compacts, refreshes views, pulls local cache. Agents call this instead of managing the pipeline steps.
CLI hard-removal (`--drain` from ops_data_portal, `drain` and `pull` from sync_ops) enforces the boundary at the build level. `sync_ops.pull` renamed to `_rebuild_local_cache` (private) with a staging-file guard that refuses to run when unstaged writes exist.

**Rationale:**
Silent failures compose multiplicatively. Each individual silent-failure was benign in isolation; together they produced partial-record Iceberg writes that went undetected until the DQ runner ran. The fix must be at the primitive layer, not just in documentation or wrapper scripts.

**Related:** Decision 50 (Iceberg ops data store), Decision 51 (local-first outbox), Decision 57 (SSO recovery), Decision 67 (Lambda deployment deferred)

---

## Decision 56: SCD Type 2 Schema Simplification for Ops Tables (Superseded by Decision 78)

**Status:** Superseded by Decision 78
**Superseded by:** Decision 78
**Date:** 2026-04-30

**Problem:**
The ops Iceberg tables (ops_recommendations, ops_session_log, ops_execution_plans, ops_decisions, ops_priority_queue) had a proliferation of confusing date/timestamp columns: `ingested_at` (pipeline ingestion time), `trade_date` (partition key derived from ingest date, misnamed since these are ops records not trades), and `date`/`string` columns on some tables (creation date for recs, session date). This caused three problems:
1. Callers had to know which timestamp field to use for SCD2 ordering (`ingested_at`) vs querying (`date`).
2. Views explicitly listed all columns, drifting from the underlying tables whenever new columns were added.
3. `trade_date` as a partition key is semantically wrong for operational metadata.

**Decision:**
Replace the old timestamp/partition scheme with clear SCD Type 2 semantics:
- **`created_timestamp timestamp`** -- when the record was first created (maps from the caller's `date` field for recs/sessions, or from `ingested_at` for tables without a creation date field).
- **`last_updated_timestamp timestamp`** -- when this specific version was written (replaces `ingested_at` as the SCD2 ordering column).
- **Partition by `day(last_updated_timestamp)`** -- uses Iceberg partition transforms (spec v2), semantically correct (partition by when the version was last updated).
- **Remove `date`/`trade_date`** columns entirely from all 5 ops tables.
- **Views use `SELECT *`** with `ROW_NUMBER() OVER (PARTITION BY {pk} ORDER BY last_updated_timestamp DESC)` -- prevents view-table drift on schema evolution.
- **`ops_priority_queue_current`** retains its correlated-subquery pattern (returns all entries from the latest curator run, not one row per entity).
- **Callers are NOT modified** -- the write path maps the incoming `date` field from callers (ops_data_portal etc.) to `created_timestamp` transparently.

**Rationale:**
- Single developer context makes `SELECT *` in views acceptable (no risk of exposing unexpected columns to unknown consumers).
- `last_updated_timestamp` is a universally understood SCD2 version key; `ingested_at` implies pipeline-specific semantics.
- Partition transform `day(last_updated_timestamp)` is correct Iceberg v2 syntax; `trade_date` as a plain column partition was a leftover from the market_data table pattern.
- `created_timestamp` makes the creation date queryable as a proper timestamp (timezone-aware), replacing `date string` which required string-to-date parsing.

**Supersedes:** Timestamp and partition aspects of Decision 50. Decision 50 core (append-only Iceberg, ROW_NUMBER views, local-first dual write) remains in effect.

**Related:** Decision 50 (Iceberg ops data store), Decision 51 (local-first outbox)

---

## Decision 51: Local-First Outbox + Bidirectional Sync for Ops Data (Superseded by Decision 78)

**Status:** Superseded by Decision 78
**Superseded by:** Decision 78
**Date:** 2026-04-23

**Problem:** Agent sessions lose operational writes when SSO expires (`OpsWriter.write()`
silently no-ops) and start with stale local JSONL data because nothing pulls from the warehouse.
The self-improvement loop cannot function if the system cannot reliably read its own
history or persist new observations.

**Decision:** Adopt a local-first outbox pattern:
- **Writes:** All writes go through OpsWriter.write(). On S3 failure, entries are written
  to a local outbox (`logs/.ops-outbox/{table}/{uuid}.jsonl`).
- **Reads:** Agents always read local JSONL files. A `sync_ops.py` script pulls the latest
  state from the warehouse `_current` views and overwrites local files.
- **Sync:** `sync_ops.py` runs drain-then-pull. Integrated into preflight (session start),
  postflight (session end), and executor between-rec checkpoints (drain only).
- **Enforcement:** validate.py warns on stale outbox entries (> 24h).

**Rationale:** Deterministic local reads (no network dependency for reads), no data loss
on SSO expiry (outbox persists until drain succeeds), idempotent flush (Iceberg deduplicates
via ingested_at), and structurally-enforced freshness via hooks in every session lifecycle phase.
Between-rec hooks call drain() only (not full sync()) to avoid 5x warehouse query cost per rec.

**Supersedes:** Nothing -- additive layer on top of Decision 50.
**Related:** Decision 50 (Iceberg ops data store), `docs/contracts/ops-data-store.md`

---

## Decision 50: Append-Only Ops Data Store via Iceberg (Superseded by Decision 78)

**Status:** Superseded by Decision 78
**Superseded by:** Decision 78

**Decision:** All operational structured logs (recommendations, execution plans, session telemetry,
decisions, priority queue) are stored as append-only Iceberg tables in the legacy query engine. Current state is exposed
via ROW_NUMBER() views. Parquet + gzip, partitioned by `trade_date`. Located in
`agent-platform-agent-logs/iceberg/`. The `OpsWriter` class in `scripts/ops_writer.py` handles
staging uploads and warehouse compaction. INSERT-only semantics (no MERGEs). Supersedes Decision 45.

**Problem:**
The dual-source JSONL+S3 pattern (Decision 45) causes: (1) merge conflicts on JSONL files when both
local and agent branches write concurrently, (2) no structured query capability -- recommendations
can only be analysed by parsing JSONL line by line, (3) no audit trail -- overwrite_jsonl() destroys
prior state, losing the history of priority queue runs and recommendation status changes, (4) schema
drift across write sites with no enforcement mechanism.

**Why append-only Iceberg over alternatives:**
- **Iceberg vs Delta Lake:** Iceberg was natively supported by the then-current query engine (already
  provisioned). Delta Lake requires additional dependencies and a separate engine configuration.
- **Iceberg vs direct SQL INSERT:** Append-only Iceberg avoids MERGE complexity and matches the
  existing table pattern. MERGE would require primary-key enforcement that Iceberg did not natively
  provide on that engine.
- **ROW_NUMBER() views vs MERGE:** Views are read-time deduplication -- zero write overhead, no
  locking, fully compatible with INSERT-only semantics. MERGE would require engine v3 MERGE DML
  which has higher failure risk and is slower.
- **Local JSONL retained in parallel:** Local JSONL files remain the source of truth for git-tracked
  artefacts and local development. OpsWriter write-through is best-effort and does not replace local
  writes. This allows gradual migration without breaking existing tooling.

**Write architecture:**
1. `s3_log_store.append_jsonl()` / `overwrite_jsonl()` complete their existing local/S3 writes
2. Write-through to `OpsWriter.write(table, entry)` staged at `staging/{table}/trade_date=.../batch-{uuid}.jsonl`
3. `session_postflight.run_auto()` calls `OpsWriter.compact_all()` at session close
4. `compact_all()` reads staging files, builds DataFrame, calls the append-mode Iceberg writer
5. Views (`ops_*_current`) provide always-fresh current state via ROW_NUMBER() deduplication

**Constraints:**
- `awswrangler` is a Lambda-only dependency (via AWSSDKPandas layer). Local `compact()` gracefully
  returns 0 when `awswrangler` is unavailable.
- `OpsWriter` never raises exceptions to callers -- all failures are logged as warnings.
- `ops_decisions` has no automated write-through yet -- write site deferred to Phase 2.
- Local JSONL files continue to be written in parallel (no breaking change to existing tooling).

**Supersedes:** Decision 45 (S3 as Authoritative Source for Cloud-Produced Logs)

**Related:** Decision 48 (V3 Verification Tier), Decision 49 (Copilot SDK inference),
`docs/contracts/ops-data-store.md`

**Decision status:** Decided -- April 2026

---

## Decision 47: Bedrock as Single Inference Provider for Lambda Agents (Superseded by Decision 49)

**Decision:** AWS Bedrock (`bedrock-runtime` `converse` API) is the single inference provider for all Lambda-executed agent code. GitHub Models API (`github_models_client.py`) is retained for local/executor use but must not be used in Lambda. All model IDs in `schedule.yaml` must use Bedrock format (e.g., `anthropic.claude-3-5-haiku-20241022-v1:0`), not GitHub Models format (e.g., `gpt-5-mini`, `openai/gpt-4.1`).

**Problem:**
On 19 April 2026, the priority queue pipeline implementation session revealed three systemic Lambda failures: (a) no Lambda deployment step in the plan, (b) model name `claude-opus-4.6` is invalid on the GitHub Models API — the correct name is `claude-opus-4-5`, causing all Lambda-dispatched agent calls to fail, and (c) the Lambda had stale `schedule.yaml` and a missing layer. The GitHub Models API requires a PAT from Secrets Manager (network egress, secret rotation risk, external provider dependency). Bedrock is IAM-native, available within the same AWS account and region, supports the same Claude models at comparable cost, and removes two failure modes (invalid GitHub model names, PAT retrieval failures).

**Provider field schema:**
All agents in `schedule.yaml` must include a `provider` field:

```yaml
provider: bedrock        # Uses bedrock_client.converse()
provider: github-models  # Uses github_models_client.chat_completion() -- local/executor only
```

**Model ID format (Bedrock):**
`{provider}.{model-family}-{date}-{revision}:{version}` — e.g.:
- `anthropic.claude-3-5-haiku-20241022-v1:0` — lightweight agents (replaces `gpt-5-mini`)
- `anthropic.claude-3-5-sonnet-20241022-v2:0` — rec-curator (replaces `openai/gpt-4.1`)

Model IDs are validated at plan time via the inference provider contract at `docs/contracts/inference-provider.md`.

**IAM requirements:**
Lambda execution role must include `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream` on `arn:aws:bedrock:eu-west-2::foundation-model/*`. Added to `terraform/scheduled_agents.tf`.

**Migration strategy:** Migrate all 6 agents in one batch. Canary verification via `run_scheduled_agent.py --smoke-test doc-freshness` before full cron promotion.

**Constraints:**
- Decision 40 (Copilot SDK + Bedrock BYOK for the executor) remains deferred and is a separate concern. This decision applies to Lambda-executed agents only.
- `github_models_client.py` is NOT deleted — it remains for local `run_scheduled_agent.py` execution via Copilot CLI.
- No Docker on company VM — Bedrock client (`bedrock_client.py`) is packaged in the Lambda zip via `build_lambda.py`.

**Related:** Decision 37 (Lambda + Secrets Manager — Bedrock partially supersedes the inference PAT dependency), Decision 40 (Copilot SDK BYOK — deferred, separate concern), `docs/contracts/inference-provider.md`, `scripts/bedrock_client.py`, `terraform/scheduled_agents.tf`

**Decision status:** Decided — April 2026

---


## Decision 46: Rescue Agent Architecture (Superseded by Decision 55)

> **Supersession note:** Decision 55 (RCA-First Autonomous Executor Architecture, 2026-04-28) supersedes this decision. The rescue agent layer, three-outcome contract, graduated autonomy gates, and `scripts/executor/rescue.py` are cancelled. The executor now stops cleanly on unrecoverable failure and invokes an RCA agent that diagnoses the root cause and files a recommendation.

**Decision:** Introduce a structured rescue agent layer to handle executor failures that cannot be resolved deterministically. All rescue agents comply with a three-outcome contract. Graduated autonomy gates control when a supervisor responsibility is considered absorbed. Recursive rescue is structurally prevented.

**Problem:**
The executor handles recs via a plan-critique-implement-postflight cycle. When a rec fails (acceptance challenge, postflight errors, unresolvable conflicts), the current path is: file a friction rec and return to the human. This creates backlog accumulation and slow feedback loops. A rescue agent layer can resolve a defined class of failures autonomously — reducing human interruptions — while the graduated autonomy gate ensures rescue agents earn trust incrementally before full responsibility handoff.

**Three-outcome contract:**
Every rescue agent must produce exactly one of:

| Outcome | Meaning | Next action |
|---------|---------|-------------|
| `RESOLVED` | Issue fixed; execution can continue | Resume executor from failure point |
| `CANNOT_RESOLVE` | Issue understood but requires human decision | File rec, notify human, stop |
| `TIMEOUT` | Agent exceeded budget or time limit | Same as CANNOT_RESOLVE |

No rescue agent may produce partial results or modify state without a declared outcome.

**Graduated autonomy success metric:**
A supervisor responsibility is marked "absorbed" by a rescue agent only when:
- `>= 80%` of observed rescue invocations produce `RESOLVED`
- Across `>= 5` consecutive observed runs
- Without any `TIMEOUT` outcome in the last 3 runs

Until the gate passes, rescue agents run in shadow mode (output logged, human retains decision authority).

**Three-level degradation maximum:**
```
Failure
  -> Level 1: Deterministic fix (retry with adjusted parameters)
  -> Level 2: Rescue agent invocation (RESOLVED / CANNOT_RESOLVE / TIMEOUT)
  -> Level 3: Draft PR + file rec -> stop
Never: recursive rescue (rescue agent calling rescue agent)
```

**Structural enforcement:**
`RescueDispatcher` in `scripts/executor/rescue.py` sets `_in_rescue: bool` context flag. Any re-entrant call (rescue agent triggering another rescue) raises `ExecutorError` immediately. Budget cap: configurable per rescue invocation, default 5 premium requests.

**Boundary table extension (Decision 44):**
Rescue agent prompts and `scripts/executor/rescue.py` are added to the executor self-modification boundary. The executor cannot modify its own rescue logic. Changes go through `/plan` -> `/implement`.

**Supervisor responsibility map:**
| Supervisor responsibility | Target rescue agent | Notes |
|--------------------------|---------------------|-------|
| Acceptance challenge re-planning | `acceptance-rescue` | Rewrites acceptance command using grep pattern |
| Postflight schema validation fix | `schema-fix-rescue` | Patches JSONL schema violations |
| Git conflict resolution | `conflict-rescue` | Resolves JSONL merge conflicts using --ours strategy |

This map is populated incrementally as rescue agents earn graduated autonomy.

**Related:** Decision 42 (Three-Tier Workflow), Decision 43 (Directed Growth Governance), Decision 44 (Executor Self-Modification Boundary)

**Decision status:** Decided — April 2026

---


## Decision 45: S3 as Authoritative Source for Cloud-Produced Logs (Superseded by Decision 50)

**Supersession note:** Decision 45's 3-pattern log ownership model is replaced by the unified
append-only Iceberg pipeline introduced in Decision 50. The `OpsWriter` class provides a single
write gateway for all operational structured logs, replacing the implicit dual-source pattern.
Decision 45 remains valid during the migration period -- local JSONL files continue to be written
in parallel. Once all consumers migrate to warehouse queries against `ops_*_current` views,
Decision 45 is fully retired.

**Decision:** S3 is the single source of truth for all cloud-produced logs. Local log files are read-only copies pulled from S3. Locally-produced logs write locally first and push to S3 on git push. Shared-mutable logs use `log_writer.py` (future) for atomic writes.

**Problem:**
The system produces logs from two distinct environments: cloud (Lambda agents, scheduled agents) and local (session scripts, executor). Without a clear ownership model, conflicts arise when both environments write to the same log files. Race conditions, stale reads, and merge conflicts on JSONL files are recurring friction sources.

**Log ownership model:**

| Log origin | Primary store | Secondary store | Write pattern |
|---|---|---|---|
| Cloud-produced (Lambda agents, scheduled agents) | S3 (`agent-platform-agent-logs`) | Local (read-only pull) | Lambda writes to S3; local pulls on session start |
| Locally-produced (session scripts, executor) | Local filesystem | S3 (push on `git push`) | Scripts write locally; `session_postflight.py` pushes to S3 |
| Shared-mutable (priority queue, recommendations) | Local + S3 | N/A | `log_writer.py` (future) handles atomic read-modify-write |

**Key design principles:**
1. **Cloud-produced logs write to S3 as primary** -- Lambda agents and scheduled agents write directly to S3 via `s3_log_store.py`. Local environments pull these logs read-only during session preflight.
2. **Locally-produced logs write locally first** -- Session scripts, executor telemetry, and retro-lite write to `logs/` on disk. These are pushed to S3 during `session_postflight.py` or manual `git push`.
3. **Shared-mutable logs require atomic writes** -- Files like `logs/.recommendations-log.jsonl` and `logs/.priority-queue.jsonl` that both environments may modify need a future `log_writer.py` module to handle atomic read-modify-write with S3 as the merge authority.
4. **Priority queue canonical key** -- The priority queue file lives at `priority-queue/.priority-queue.jsonl` in S3, distinct from local log paths.

**No code changes -- docs only.** This decision codifies the existing implicit pattern and sets the contract for future `log_writer.py` implementation.

**Related:** Decision 42 (Three-Tier Workflow Architecture), `scripts/s3_log_store.py`, `scripts/session_postflight.py`

**Decision status:** Decided -- April 2026

---


## Decision 36: GitHub Actions OIDC Federation for AWS Access (Superseded)

> **Status:** Superseded by Decision 37 (April 2026). The OIDC approach was blocked by a
> corporate SCP denying `sts:AssumeRoleWithWebIdentity` from external IP ranges.
> The scheduled agents workflow (`.github/workflows/scheduled-agents.yml`) has been deleted.
> All infrastructure references (OIDC provider, IAM role) have been removed from Terraform.

**Original Decision:** Use GitHub Actions OIDC federation (via `aws-actions/configure-aws-credentials@v4`
with `role-to-assume`) instead of static IAM user credentials for the scheduled agents workflow.

**Why it failed:**
- CloudTrail showed `AccessDenied` on `AssumeRoleWithWebIdentity` with no Condition detail —
  the signature of an org-level SCP denial (not a trust-policy misconfiguration)
- SCP blocks `sts:AssumeRoleWithWebIdentity` from external IP ranges (GitHub Actions runners)
- Static IAM users cannot be created due to a separate SCP (`iam:CreateUser` denied)

**Superseded by:** Decision 37

**Decision status:** Superseded — April 2026

---


## Decision 34: Unified Cross-Workflow Session Telemetry

**Context:** The repository has two development workflows: manual (`/plan` + `/implement` via VS Code Copilot Chat) and automated (`execute_recommendation.py` via CLI). Each workflow produced its own telemetry in different formats to different files:

| Data type | Manual writes to | Executor writes to |
|-----------|-----------------|-------------------|
| Friction | `.retro-lite-log.jsonl` | Nothing |
| Session metrics | `.session-metrics-log.jsonl` | Nothing |
| Step telemetry | Nothing | `.execution-step-telemetry.jsonl` |
| Plan revisions | `docs/plans/PLAN-{slug}.md` | `.execution-plans.jsonl` |

This fragmentation meant that no single log could answer "how many sessions ran this week?", "what is the overall friction rate?", or "what is the total premium request consumption across workflows?". Cron agents reading friction data saw only manual session friction, missing all executor runs.

**Decision:** Introduce a unified session envelope (`logs/.session-telemetry.jsonl`) written by both workflows. Additionally, the executor now writes friction entries to `.retro-lite-log.jsonl` using the same schema as manual sessions. Cross-referencing uses the `branch` field present in all log entries.

**What was NOT consolidated (and why):**
- Step-level telemetry remains separate (`.execution-step-telemetry.jsonl` vs per-step retro-lite entries) because they serve different purposes (cost tracking vs friction narrative).
- Plan storage remains separate (`PLAN-{slug}.md` vs `.execution-plans.jsonl`) because manual plans are human-reviewed markdown while executor plans need machine-readable step parsing and revision tracking.
- `.recommendations-log.jsonl` was already shared across both workflows.

**Trade-offs:**
- Minor write overhead: each session now writes one additional JSONL line (~200 bytes).
- The executor writes friction even on clean runs (`"friction": "clean"`), enabling friction rate calculation across all workflow types.

**Alternatives considered:**
- Merging all logs into a single file: rejected because step-level and session-level data have different cardinalities and query patterns.
- Separate dashboarding layer: rejected as premature; the JSONL-based approach is sufficient until log volume requires a database.

**Decision status:** Accepted. Implemented on branch `agent/infra-s3-logs`.

---


## Decision 33: S3 Log Append Read-Modify-Write Race Condition (Accepted Risk)

**Context:** `scripts/s3_log_store._append_jsonl_s3()` uses a read-modify-write pattern: read the existing object, append the new entry, write the full object back to S3. S3 does not support atomic append (unlike a POSIX file or DynamoDB conditional write), so two concurrent appends could result in the second write overwriting the first.

**Why this is acceptable (Phase 1):**
1. Cron agents run sequentially via GitHub Actions scheduled workflows (one job at a time per workflow).
2. Each cron job writes to a different log file (retro-lite vs. session-metrics vs. plan-audit), so even near-simultaneous cron runs write to disjoint keys.
3. Log files are observability artifacts, not business-critical transaction records. Occasional data loss (a single missing friction entry) is tolerable.
4. All writes that are critical (recommendations, execution state) use atomically-written local files during executor sessions.

**Mitigation if concurrency increases (Phase 3+):**
- Use DynamoDB conditional writes for coordination, or
- Write each log entry as a separate S3 object with a UUID key (list + compose pattern), or
- Use an SQS queue with a Lambda writer to serialize all appends.

**Decision:** Accept the race condition for Phase 1. Revisit when parallel cron agents are introduced in Phase 3.

---


## Decision 32: API Specification Precision in Implementation Plans (Empirical — from rec-042 retrospective)

**Context:** Implementation session `agent/rec-042-status-writeback` (2026-03-31) achieved zero friction and all steps completed successfully. However, the session's code review phase identified a critical gap: the original plan specified "track cost across all phases" (Step 3) but did not explicitly state that the `implement_step()` function signature would need to change from `-> bool` to `-> tuple[bool, float]`. This was discovered during code review when the cost was not being propagated to the caller, requiring a post-implementation signature change.

**Problem:** Plan specifications that say "add feature X" or "track metric Y" create ambiguity about which function signatures must change. During implementation, this ambiguity results in:
1. Functions having incomplete return types
2. Code review discovering the gap after implementation is complete
3. Post-review refactoring of call sites and API contracts
4. Unnecessary rework cycles in what should be a clean implementation

**Observation (from session metrics):** Despite the API gap, the session had:
- 0 friction reported (all steps completed successfully on first attempt)
- All 289 tests passing
- Code review found 2 bugs (Critical: missing cost propagation; High: Windows line-end normalization)
- Both bugs were fixed in-session with no extended friction

The session succeeded mechanically (steps executed, tests passed) but had a spec completeness gap that caused post-implementation rework.

**Decision:** Implement API specification precision pattern in all implementation plans.

**The Pattern:**

When a plan step says "track/add/propagate/accumulate X":
1. **Explicit signature change** — List the function and show the before/after signature
   - Example ✓: "Modify `implement_step()` return type from `-> bool` to `-> tuple[bool, float]` to expose cost to caller"
   - Example ✗: "Track cost from implement_step"

2. **Cascade documentation** — List all call sites that must be updated
   - Example ✓: "Update 3 call sites in `_execute_recommendation_inner()` to unpack tuple"
   - Example ✗: "Callers will need changes" (vague)

3. **Acceptance criterion includes API checks** — Make testing include signature validation
   - Example ✓: "Test: `implement_step()` returns `tuple[bool, float]` with non-zero cost for successful steps"
   - Example ✗: "Test: cost is tracked" (doesn't specify how)

4. **Before/After dataflow diagram** — If adding a new accumulator or propagating a value, sketch the data flow
   - Example: Diagram showing cost flowing from `copilot_call()` → `implement_step()` → `_execute_recommendation_inner()` → `update_recommendation_status()`

**Rationale:**
- Plan specs with concrete before/after signatures prevent implementation ambiguity
- Call site enumeration prevents "surprise" post-review refactoring
- Dataflow diagrams catch multi-layer propagation issues (like missing intermediate handlers)
- API-level acceptance criteria catch signature gaps before code review
- Reduces rework cycles and keeps "zero friction" sessions truly friction-free

**Validation (rec-042 retrospective):**
- Original plan Step 3: "Track cost after each CLI call" (vague)
- Discovered ambiguity: "Track" did not specify `implement_step()` must return cost, only that it should be accumulated
- Code review finding: "implement_step cost not propagated" (signature issue)
- Fix: Changed return type to `tuple[bool, float]` + updated 3 call sites
- Lesson: Explicit signature in plan would have caught this in step 3 itself

**Implementation:**

1. **Update `.github/prompts/plan.prompt.md` Step 7** (Write Plan Structure):
   - Add requirement: "If step says 'add/track/propagate X', include before/after function signature"
   - Template example: `def func(args) -> old_type: ...` → `def func(args) -> new_type: ...`
   - Add checklist: "List all call sites that need signature updates"

2. **Create checklist in `docs/GETTING_STARTED.md`** under "Plan Writing":
   - "For API changes: show before/after signatures"
   - "For data propagation: show 3-level dataflow (source → intermediate → final)"
   - "For new fields: enumerate all places they're read/written"

3. **Retrospective validation** (this session):
   - Rec-042 would have benefited from explicit signature change in Step 3
   - Cost flow: `copilot_call() -> cost_usd` → `implement_step() -> tuple[bool, cost_usd]` → `_execute_recommendation_inner() -> accumulate` → `update_recommendation_status() -> write`
   - All 5 levels would have been in the plan's Step 3 explicitly

**Constraints:**
- Pattern applies to **code changes that alter function contracts** (signatures, return types, field additions)
- Pattern less critical for **pure logic changes** (refactoring, algorithmic improvements) where signatures remain unchanged
- Does not increase plan size substantially (signatures are concise)

**Relation to Decision 29 (Friction-Free Pattern):**
- Decision 29 addresses **scope ambiguity** ("improve X" is too vague)
- Decision 32 addresses **API specification completeness** (concrete changes must have concrete before/after signatures)
- Both are complementary: defined scope + precise specs = zero friction

**Status:** Empirical finding from rec-042 retrospective. Implementation recommended: update plan.prompt.md checklist and create template in GETTING_STARTED.md. No code changes required (pattern is planning methodology).

---


## Decision 31: Subagent CLI Invocation Capability (Empirical — from rec-027 validation session)

**Context:** Phase B of the CLI migration roadmap (rec-002) depends on whether subagents can invoke CLI commands. If subagents can execute `copilot -p "..."` via terminal, the architecture becomes: VS Code agent → CLI invocation → nested subagent result. This would enable the "minimal invocation cost" design where each session makes 2-5 deterministic CLI calls instead of 2N+3 expensive VS Code agent calls. Validation session `agent/infra-cli-subagent-validation` (2026-03-30) tested this capability.

**Experiment 1: Subagent Search Tool Invocation**

**Test:** Invoke Explore subagent with task: "Run copilot --version via terminal and report success/error"

**Observed Result:** Subagent ran file search and code pattern matching instead of terminal execution. Returned hits for subprocess examples in `setup.py`, `validate.py`, and setup instructions from documentation — did not attempt `copilot --version` invocation.

**Interpretation:** Subagents have access to read/search tools only (`read`, `search`, `execute/grep`, `execute/file_search`), not shell execution tools. Reference: `code-review.agent.md` metadata declares: `'tools': ['read', 'search', 'execute/runInTerminal', 'execute/getTerminalOutput']` — but **no actual terminal execution capability** despite tool list including `runInTerminal`. The tool list appears to be capability specifications for the caller, not permissions granted to the agent.

**Impact on rec-027:** Subagents **cannot directly invoke CLI commands**. The architecture path "VS Code agent → CLI call → subagent result" is not viable. Subagents remain bound to read/search/analysis operations.

**Alternative Architecture (unfolds from this finding):**

instead of:
```
/implement
  → step
  → @step-validator (invokes copilot CLI)
  → next step
```

Must be:
```
/implement
  → step
  → (parent runs: python scripts/validate_step.py)
  → @step-validator (only analyzes YES/NO from script)
  → next step
```

Where the parent prompt has **direct terminal access** (since it's a primary VS Code agent) and runs CLI calls directly, then passes deterministic results to subagents for semantic analysis only.

**Implication for Phase B:** CLI invocation must happen in the parent `/implement` prompt, not in subagent context. This changes the architecture from "distributed CLI calls" to "centralized parent calls CLI, subagents analyze results."

**Validation Artifacts:**
- Explore subagent search results: returned file references, no terminal activity
- Copilot CLI v1.0.13 confirmed available on local system
- Baseline verification: `which copilot` → `$APPDATA/npm/copilot`, `copilot --version` → "GitHub Copilot CLI 1.0.13"

**Decision:** Proceed with rec-027 treating this as a **negative finding** (subagents cannot invoke CLI). Update Phase B architecture plan in [docs/plans/PLAN-infra-cli-migration-plan.md](../docs/plans/PLAN-infra-cli-migration-plan.md) to reflect:
- Parent prompt has direct CLI access (VS Code agent context)
- Subagents used only for semantic analysis of deterministic script output
- No nested CLI invocation attempts
- Implications for rec-002 (phase B scope change: more logic in parent, less in subagents)

**Rationale:**
- Tool sandboxing is intentional (security): subagents must not execute arbitrary code
- Parent context (VS Code) has broader permissions; use it for I/O-bound work
- Hybrid architecture (parent CLI + subagent analysis) is actually **cheaper**: parent only invokes CLI when needed, subagents only called for semantic judgment

**Constraints:**
- Deterministic validation scripts (replace-in-place, coverage checks) must be idempotent (run anywhere safely)
- Results must be machine-readable JSON so subagents can analyze without terminal access
- Parent prompt must handle: git commands, subprocess execution, file creation/modification

**Status:** Empirical finding from rec-027 validation. Recommendation: update Phase B plan file with revised architecture and merge into main after rec-029 (transcript storage infrastructure) is also complete.

---


## Decision 30: GitHub Copilot CLI Session Feature Validation (Empirical — from direct validation session)

**Context:** CLI migration roadmap (PLAN-infra-cli-migration-plan.md) identifies several CLI features for integration: OTel telemetry (rec-005), `--share` transcripts (rec-006), `/chronicle improve` (rec-007), `/chronicle tips` (rec-008), session resume (rec-008), session history queries (rec-009). Before integrating these into prompts, the features were validated empirically using copilot CLI v1.0.12 in the session `agent/infra-cli-otel-telemetry` (2026-03-30).

**Findings:**

### OTel Telemetry (rec-005) — CONFIRMED WORKING

Environment variable: `COPILOT_OTEL_FILE_EXPORTER_PATH=<path>` exports spans as JSONL.

**Observed span schema** (5 spans per non-interactive `-p` invocation):

| # | `name` | `type` | Key fields |
|---|--------|--------|-----------|
| 1 | `chat claude-sonnet-4.6` | span | `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `github.copilot.cost` (int), `github.copilot.server_duration` (ms), `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.conversation.id` |
| 2 | `invoke_agent` | span | Same token/cost fields, `github.copilot.turn_count` |
| 3 | `gen_ai.client.operation.duration` | metric | Histogram of operation duration in seconds |
| 4 | `gen_ai.client.token.usage` | metric | Histogram of input/output token counts |
| 5 | `github.copilot.agent.turn.count` | metric | Number of LLM round-trips |

**Key schema notes:**
- `github.copilot.cost` is an integer in the span `attributes` (units: Copilot AIU — 1 per small request observed)
- `github.copilot.aiu` field does NOT appear directly; cost is under `github.copilot.cost`
- `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens` are in span attributes
- `service.name` is controllable via `OTEL_SERVICE_NAME` env var
- Each record has `traceId`, `spanId`, `parentSpanId`, `startTime`, `endTime` (nanosecond epoch arrays)
- Events include `github.copilot.session.usage_info` (token_limit, current_tokens, messages_length)
- Tool calls produce additional child spans (not observed in minimal test)

**Integration path:** `scripts/cost_report.py` (rec-026) should parse `.copilot-otel.jsonl` summing `github.copilot.cost` from `invoke_agent` spans filtered by `gen_ai.conversation.id`.

### Session Transcript Export (rec-006) — CONFIRMED WORKING

Flag: `--share=<path>` exports session to Markdown after non-interactive (`-p`) completion.

**Observed transcript format:**
```markdown
# 🤖 Copilot CLI Session
> Session ID, Started, Duration, Exported

### ⚠️ Warning   (MCP policy notices, if any)
### 👤 User       (user prompt text)
### 💬 Copilot    (model response)
```

**Limitations:**
- `--share` only works in non-interactive mode (with `-p`). Interactive sessions require in-session `/share` slash command.
- Timestamps are human-readable locale strings (not ISO-8601) in the transcript Markdown.
- Tool call details are not visible in the exported Markdown (hidden in rendering).

**Integration path:** Add `--share="logs/transcripts/session-${SLUG}-$(date +%s).md"` to CLI session invocations in `run_session.py` (rec-001).

### `/chronicle standup` — WORKS (LLM interpretation, not built-in slash command)

`/chronicle` is NOT a built-in slash command in v1.0.12. However, passing `/chronicle standup` as prompt text produces a high-quality standup summary because the LLM interprets it semantically and reads local files (SESSION_LOG.md, git log, RECOMMENDATIONS.md).

**Observed output quality:** Excellent. Produced branch-by-branch summary covering infra-testing-enforcement, infra-cli-migration-plan, and infra-eliminate-retro-lite-subagent sessions with test counts, friction rates, and durations.

**Mechanism:** LLM reads `docs/SESSION_LOG.md` and git history — not a "session history database". Same information available by asking directly: "Summarize recent work from SESSION_LOG.md."

**Integration note:** Can be invoked as a regular `-p` prompt with `--experimental`. No special slash command needed. Recommended pattern: `copilot --experimental -p "/chronicle standup" -s --no-ask-user`.

### `/chronicle tips` — RETURNS GENERIC CLI TIPS (not personalized)

Passing `/chronicle tips` as prompt returns hardcoded CLI usage tips (keyboard shortcuts, slash commands, productivity features). Not personalized analysis of session patterns.

**Impact on rec-008:** `friction_analysis.py` should NOT be replaced by `/chronicle tips`. The custom JSONL-based friction analysis produces session-specific insights; `/chronicle tips` does not. rec-008 assumption was incorrect.

### `/chronicle improve` — NOT AVAILABLE in v1.0.12

`/chronicle improve` is not a recognized slash command. When passed as prompt text, the LLM responds that it is unknown. Cannot propose instruction changes without session context in `-s` mode.

**Impact on rec-007:** Cannot automate instruction improvement via `/chronicle improve`. Alternative: invoke `copilot --experimental --continue -p "Review .github/copilot_instructions.md and propose improvements based on recent session patterns in SESSION_LOG.md"` — achieves equivalent outcome via explicit prompting.

### Session Resume (`--continue` / `--resume`) — CONFIRMED WORKING

- `copilot --continue` — resumes the most recent session with full conversation history intact
- `copilot --resume` — opens interactive session picker
- `copilot --resume=<session-id>` — resumes specific session by UUID

**Validated:** `--continue` resumes context correctly. Tested by storing a marker fact, exiting, re-invoking with `--continue`, and successfully retrieving the fact.

**Limitation:** No cross-session memory. Each new session starts fresh. `--continue` only appends to the most recent session thread. Cannot query "all sessions" as a searchable memory store.

### Session History Queries — PARTIALLY WORKS (via file access, not session memory)

"What did I work on recently?" queries work if the LLM is given access to tools and reads `docs/SESSION_LOG.md` or git log. There is NO built-in session memory database that stores work history across sessions.

**Observed behaviour in `-s` mode:** Queries that trigger tool calls (git, file reads) can time out in non-interactive mode. Queries in `--continue` mode produce accurate answers from within-session context only.

**Impact on rec-009:** Recommendation to use "free-form session history queries" needs to be reframed as "prompt the LLM to read SESSION_LOG.md/git log" rather than expecting a native session memory store.

**Decision:** Proceed with CLI integration based on confirmed features. Update recommendations that assumed non-existent features:
- rec-007 (`/chronicle improve`): Replace assumption with explicit prompt pattern
- rec-008 (`/chronicle tips` for friction): Mark assumption incorrect; retain `friction_analysis.py`
- rec-009 (session history queries): Reframe as file-based prompting pattern

**Validation artifacts:**
- OTel span data: `logs/.copilot-otel.jsonl` (5 spans from 2026-03-30 test invocation)
- Test transcript: `logs/transcripts/session-infra-cli-otel-telemetry-test.md`
- CLI version validated: `GitHub Copilot CLI 1.0.12`

---


## Decision 28: Execution State Checkpoint for Session Resumption (Agent-decided — approved)

**Context:** Implementation sessions can be interrupted by overnight context overflow, timeouts, or manual pausing. When resuming after interruption, agents must know which step was completed last to avoid re-running completed steps or skipping work. Previous approach (manual notes) was error-prone and depended on human memory.

**Decision:** Implement execution state checkpoint management:

**Checkpoint Design:**
```json
{
  "branch": "agent/feature-name",
  "plan_file": "PLAN-feature-name.md",
  "current_step": 5,
  "total_steps": 13,
  "status": "IN_PROGRESS",
  "last_updated": "2026-03-29T10:15:30+00:00"
}
```

- **Persisted to:** `logs/.execution-state.json` (alongside other session JSONL logs for consistency)
- **Schema:** TypedDict with required fields (branch, plan_file, current_step, total_steps, status, last_updated)
- **Lifecycle:**
  - Created/updated by `save_checkpoint()` after each Ordered Execution Step completes
  - Loaded by `load_checkpoint()` at Step 1 of `/implement` prompt
  - Human chooses: resume from step N+1 or restart from step 1
  - Cleared by `clear_checkpoint()` on return to main after successful completion

**Integration Points:**
1. **`.github/prompts/implement.prompt.md` — Step 1a:** Check for checkpoint before reading plan. If IN_PROGRESS, offer resume option.
2. **`.github/prompts/implement.prompt.md` — Step 6:** After each step completes, save checkpoint with current progress.
3. **`.github/prompts/implement.prompt.md` — Step 23:** Clear checkpoint before returning to main.
4. **`scripts/execution_state.py`:** Standalone utility with save/load/clear/age functions. CLI interface for testing.
5. **`.github/agents/retro-lite.agent.md`:** Added verification gate; prevents false "clean session" claims when friction occurred.

**Constraint:** Checkpoint state must be JSON-serializable and recoverable after process interruption (no in-memory caches, all state on disk).

**Rationale:**
- **Explicit vs. implicit:** Unlike tracking step number in comment text, checkpoint is machine-readable and versioned (schema validated on load).
- **Atomic operations:** No partial writes; Python atomic replace on save, no corruption risk.
- **No external dependencies:** Uses stdlib datetime, json, pathlib only.
- **Human-friendly resumption:** "/implement" presents checkpoint interactively; human decides resume/restart to avoid confusion.
- **Supports parallel workflows:** Each branch has its own plan file (Decision 23); checkpoint is branch-scoped.

**Validation:**
- 10 unit tests covering all functions (save, load, clear, age, error cases)
- 100% code coverage for `execution_state.py`
- Test isolation via mock patching of STATE_FILE path
- Integration tests in `.github/prompts/implement.prompt.md` defined as acceptance criteria

**Friction Verification Gate (Nested Decision):**
Added explicit verification gate to `@retro-lite` before claiming "clean session":
- Requires invoking agent to explicitly state: "No tool failures, no mismatches, no unexpected states"
- Rejects "clean" claims if context mentions retries, rework, or surprises
- Prevents false clean sessions (which break self-improvement feedback loop)
- Enforces: friction must be recorded and analyzed, not hidden

**Status:** Agent-decided — approved by test suite (180/180 tests pass globally) and validation checks (validate.py exit 0).

---


## Decision 29: Friction-Free Implementation Pattern (Empirical — from successful session retrospective)

**Context:** Session `agent/infra-haiku-batch-fixes` (2026-03-29) achieved zero friction status: all 15 execution steps completed on first attempt, 186 tests passing, 9/9 pre-commit hooks passing, 0 scope drift. Comparative analysis against 12 prior sessions (in `logs/.retro-lite-log.jsonl`) reveals why this session succeeded while others experienced retries and rework. The difference was not agent quality but **plan specificity**.

**Observation:** Friction clusters:
- Vague scope ("improve code quality") → exploration, rework, mid-session discoveries
- Specific scope ("change `if self.get(key) is None` to `if not self.get(key)`") → linear execution, no backtrack

**Decision:** Establish and document the "Ordered Execution Steps + Acceptance Criteria" pattern for all future implementation plans.

**The Pattern:**

1. **Ordered Execution Steps** (numbered 1..N):
   - Each step targets 1–3 specific files
   - Every step includes a concrete, verifiable action (not "improve X")
   - Example ✓: "In `src/common/config.py`, modify the `validate()` method…change the check from `if self.get(key) is None` to `if not self.get(key)`"
   - Example ✗: "Fix code quality issues in config.py"

2. **Paired Acceptance Criteria** (one per step or per file):
   - Measurable, testable, observable within 1–2 minutes after step completion
   - Example ✓: "`config.validate()` raises `ValueError` when a required field is empty string `""`"
   - Example ✗: "Code quality improved"

3. **Scope Verification at 50% Completion** (ceil(N/2)):
   - Invoke @scope-guard agent to verify: "Files planned, files changed, scope creep count"
   - Prevents mid-session expansion (observed in 4 prior sessions: +4–6 files added without explicit approval)

4. **Test Files Pre-planned in Scope Table:**
   - Do not discover tests mid-session ("what should we test?")
   - Enumerate in plan: which untested functions need coverage

**Empirical Evidence (agent/infra-haiku-batch-fixes):**
- 15 ordered steps → 15 files changed (0 additions)
- 14 acceptance criteria → all verifiable within minutes
- Step 9 scope guard → "15 planned, 15 changed, 0 creep"
- Test scope pre-identified → 16 tests written, all for currently-untested functions (no redundant coverage)
- Outcome: 186 tests passing, 9/9 pre-commit hooks, zero friction

**Contrast: Prior Sessions with Friction**

Session `agent/infra-parallel-workflow` (2026-03-27):
- Plan: "Fix hardcoded PLAN.md references in agent files" (vague scope)
- Result: Discovered 4 agents, then 5 agents, then discovered critical issues in scripts → 11 files unplanned
- Friction: Multiple scope expansions, mid-session discoveries

Session `agent/infra-workflow-cost-optimisation` (2026-03-28):
- Plan: "Restructure to 2-chat model and eliminate serialization overhead"
- Result: Discovered step-by-step refactoring needs as implementation progressed; 28 ordered steps, many with compound objectives
- Friction: Pre-commit linting failures, multi_replace chain issues, multiple code fixes after first run

Session `agent/infra-haiku-batch-fixes` (2026-03-29):
- Plan: "Fix 6 code quality items from recommendations + 1 prompt change"
- Result: 15 concrete steps, each with 1–2 files, each with verifiable acceptance criteria
- Friction: None. Linear execution.

**Implementation:**

1. **Update [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)** with "Plan Writing" subsection:
   - Template: Ordered Execution Steps format
   - Checklist: "Every file in Scope table must have ≥1 acceptance criterion"
   - Contrast: vague example vs. specific example

2. **Create plan template** at `docs/plans/PLAN-template.md`:
   - Annotated example with Ordered Execution Steps
   - Guidance on writing testable acceptance criteria
   - Scope Guard instruction at Step ceil(N/2)

3. **Update [.github/prompts/plan.prompt.md](plan.prompt.md)** Step 7 (Write Plan Structure):
   - Add validation: "Reject plans with <3 acceptance criteria" (signals incompleteness)
   - Add language: "Use Ordered Execution Steps format from `docs/GETTING_STARTED.md`"

4. **Update [.github/prompts/implement.prompt.md](implement.prompt.md)** Step 9 (Scope Guard):
   - Add explicit reference: "Invoke @scope-guard to verify 'Files planned == files changed'"
   - Clarify: "If scope creep detected, STOP and ask human whether to absorb new items or defer to future plan"

**Validation:**
- Retrospective of this session (2026-03-29) documents pattern explicitly
- Pattern reproducible: future plans can adopt same structure
- Prior session analysis shows friction would have been prevented with this pattern

**Rationale:**
- Explicit scope prevents exploration-driven rework
- Acceptance criteria enable early failure detection (step 3 broken → fix before step 4)
- Scope guard prevents silent scope creep
- Pre-planned tests eliminate mid-session "what should we test?"
- Linear execution reduces token consumption and session duration

**Constraints:**
- Pattern works best for **defined-scope work** (code fixes, refactoring, infrastructure updates)
- Pattern less suited for **exploratory work** (prototyping, research sessions where outcomes are unknown)
- Distinguish: tag plans as "DEFINED_SCOPE" vs "EXPLORATORY" in the `## Intent` section

**Status:** Empirical decision from retrospective analysis. Implementation recommended: update docs, create template, add validation to plan.prompt.md. No code changes required (pattern is purely planning methodology).

---


## Decision 22: Cron Review System Architecture (Decided)

**Context:** The repository has no mechanism to autonomously review prompts and agents for quality, consistency, or drift from declared conventions. Code review runs once per session, but prompt/agent quality issues accumulate silently between sessions.

**Decision:** Hybrid manifest-driven cron review system:
- `scripts/list_customizations.py` generates a machine-readable `.customizations-manifest.json` from `.github/prompts/` and `.github/agents/` on demand
- `@prompt-reviewer` agent (GPT-4.1, free tier) reviews each file against declared criteria (clarity, completeness, consistency, actionability, North Star alignment)
- `.recommendations-log.jsonl` is the single machine-readable store for all findings; `RECOMMENDATIONS.md` is retained as legacy human-readable reference
- `.rejected-suggestions.jsonl` provides cron-session memory: patterns that were reviewed and rejected, to prevent re-surfacing of known-acceptable deviations
- `.decisions-index.jsonl` provides cron agents a queryable index of DECISIONS.md without requiring full prose ingestion
- Scheduling: Windows Task Scheduler on local VM at 05:00 UTC — no AWS infrastructure needed for a 0-cost monitoring job
- `CRON_REVIEW_SUMMARY.md` is generated (overwritten each run) as a human-readable audit trail of the most recent run

**Rejected alternatives:**
- Full AWS Lambda scheduling: overkill for a job that requires VS Code agent capabilities (not a headless compute task)
- Agent self-discovery only (no manifest): unstructured, relies on search which is slower and misses newly created files not yet indexed
- Full JSON DECISIONS.md: loses human-readable rationale; prose context is critical for downstream agents to understand the "why", not just "what"

**Status:** Decided — March 2026

---


## Decision 21: Per-Step Retro-Lite Retention Despite Token Cost (Decided)

**Context:** The `implement.prompt.md` workflow interleaves retro-lite invocations after every Ordered Execution Step. This was added to the workflow but retro-lite was returning "Clean session / none" for every step because the subagent had no visibility into the parent conversation — it was answering from an empty context window.

**Decision:** Retain per-step retro-lite invocations AND add a mandatory context-passing requirement: the invoking agent must explicitly pass (1) the step just completed, (2) tool failures, (3) file replacement mismatches, (4) unexpected file states. If no context is provided, retro-lite returns an error instead of silently reporting "none".

**Implementation:**
- `.github/agents/retro-lite.agent.md`: Added `## Required Context` section (mandatory fields) and `## No-Context Error` section (error behavior)
- `.github/prompts/implement.prompt.md` Step 6: Updated to explicitly list required context and instruct agent to state "No tool failures, no mismatches, no unexpected states" if none apply

**Rationale:**
- Conversation compaction loses detail before the full retrospective runs; per-step capture is the only defensive mechanism against this loss
- Friction data not captured at occurrence is lost forever — the full retrospective only sees summary-level information
- ~300 tokens × N steps per session is acceptable overhead for feedback loop fidelity
- "None / clean" answers from retro-lite without context are false negatives — they undercount friction and degrade the learning signal over time
- The fix is behavioral (require context from invoker), not structural (remove per-step retro-lite)

**Rejected alternatives:**
- Remove per-step retro-lite and rely only on end-of-session retrospective: loses fine-grained step-level friction data permanently
- Use a richer system prompt for retro-lite: does not solve the context problem; retro-lite sees no conversation history from the parent agent

**Status:** Decided — March 2026

---

## GitHub MCP + CI Feedback Loop (Decided)

**Context:** After pushing a branch there was no feedback from GitHub Actions — CI failures were discovered hours later via manual GitHub checks. This breaks the self-improving loop: the system cannot learn from CI failures it cannot see.

**Decision:** Configure GitHub MCP server in VS Code (`.vscode/mcp.json`) and add a CI triage protocol to the Session Close Phase of `implement.prompt.md` (Steps 19/20).

**Key design choices:**
- Primary CI polling via MCP `list_workflow_runs`/`get_workflow_run_logs`; `gh` CLI as fallback
- Human-gated fixes: Phase 2 postmortem is presented to human before any fix is applied (auto-fix is a documented future toggle)
- Fix the `validate.py` gap FIRST, then fix the CI failure — so the same class of error is caught locally in future
- Maximum 2 triage retry cycles before escalating to human
- Zero additional cost: MCP server runs locally via npx, auth via `gh auth token` (user pastes token when VS Code prompts on first MCP server connection)

**Rejected alternatives:**
- GitHub webhooks to localhost — complex, fragile, requires network exposure
- GitHub Actions bot comments — one-directional, no agent integration
- Server-side Copilot coding agent MCP (GitHub repo settings) — different product, requires organisation-level setup

**Status:** Decided — March 2026

---

## Python-Only Scripting (Decided)

**Context:** Repository automation used a mix of PowerShell and bash scripts. PowerShell 5.1 has known gotchas (Join-Path, em-dashes, curly braces in JSON). Agents reading/writing scripts face inconsistent syntax across languages.

**Decision:** Standardise all repository automation on Python:
- All scripts in `scripts/` are `.py` files
- Setup scripts are `.py` files
- Use `subprocess` for external commands (git, terraform, pytest)
- Use `argparse` for CLI arguments
- Use `pathlib` for cross-platform path handling

**Rationale:**
- Python is the best language for LLMs to read and write (consistent, well-documented, type-hintable)
- Eliminates PowerShell-specific gotchas from workflow documentation
- Aligns scripting layer with application code (all Python)
- Subprocess calls are explicit and auditable

**Status:** Decided -- March 2026

---

## Multi-Model Workflow Architecture (Decided)

**Context:** The agent workflow uses a single model family (Anthropic) for planning, implementation, review, and retrospective. When the planner (Opus) skipped its own branch creation instruction, no independent check caught it -- the same model family's blind spots propagated through the pipeline.

**Decision:** Use three model families in the workflow pipeline:
- **Anthropic (Claude):** Planning (Opus), code review (Sonnet), retrospective (Haiku), session close integrated into implement
- **Google (Gemini 2.5 Pro):** Plan critique -- mandatory gate between planning and implementation
- **OpenAI (GPT-4.1):** Free monitoring agents (retro-lite, step-validator, scope-guard) -- invoked liberally throughout every session at 0x cost

**Rationale:**
- Different model families have different blind spots -- cross-family review catches errors that same-family review misses
- Free (0x) models enable high-frequency monitoring without cost concern
- Quantitative checks (scope audit, metrics) moved to Python scripts (deterministic, free, reliable)
- Qualitative checks (plan critique, friction capture) remain with agents (judgment required)

**Status:** Decided -- March 2026

---

## Documentation Prompt Architecture

**Context:** The repository has two documentation prompts with overlapping but distinct scopes: `documentation_update` (feature branch changes only) and `documentation_full_audit` (whole-repository health check). Without guidance, the user had to choose between them manually on each invocation.

**Decision:** Introduce a router prompt (`documentation.prompt.md`) as the single entry point. The router uses `git diff origin/main` as the primary signal — non-empty diff routes to `documentation_update`; empty diff falls back to keyword matching on the user's message; ambiguous cases ask a single clarifying question. Both sub-prompts remain directly invocable if the router's routing would be wrong for a specific case.

**Model selection:** All three documentation prompts are pinned to `Claude Haiku 4.5 (copilot)`. Documentation tasks are text-heavy and structured rather than reasoning-intensive, making Haiku the cost-appropriate choice. The model name must match the VS Code model picker display name exactly — discoverable via `get_errors` on the prompt file, then searching the Copilot Chat extension bundle (`github.copilot-chat-*/dist/extension.js`) for the string. As of Copilot Chat 0.40.1 the identifier is `Claude Haiku 4.5 (copilot)`.

---

## Coverage Ratchet Pattern

**Context:** Test coverage can drift downward as new code is added without corresponding tests. Manual enforcement is unreliable.

**Decision:** Use `pytest-cov` with `fail_under` threshold in `pyproject.toml [tool.coverage.report]`. The threshold is a ratchet — it can only increase, never decrease. Current baseline: 40%. When coverage rises (e.g. to 45% after adding tests), bump the floor to lock in the gain. This is enforced in `scripts/validate.ps1` (local CI) and `.github/workflows/ci.yml` (PR gate).

**Rationale:** Prevents regression without blocking forward progress. A hard 80% floor would halt all work on the current codebase (41% coverage). The ratchet allows incremental improvement while ensuring no new untested code merges.

---

## mypy Informational-Only Status

**Context:** Adding mypy to an existing untyped codebase (30 pre-existing errors across 10 files) creates a choice: block all work until errors are resolved, or run informational-only.

**Decision:** Run mypy in both `scripts/validate.ps1` and `.github/workflows/ci.yml`, but do not fail on errors. A TODO comment in both files marks this as temporary. Once the error count reaches zero, remove the `|| true` (ci.yml) and try/catch (validate.ps1) to promote mypy to a hard gate.

**Path forward:** Fix type errors progressively during normal feature work (when touching a file, resolve its type errors). Track remaining error count via `mypy src/ | grep "Found"`. When zero, flip the switch.

---

## 1. A/B Test Duration Trade-off

**Context:** New formulas discovered by SageMaker must be A/B tested before promotion to production.

**Options:**
- **7 days**: Enables faster iteration and deployment velocity
  - Risk: Insufficient data for statistical significance, potential false positives
  - Use case: High-frequency trading with abundant daily samples

- **14 days (current recommendation)**: Balance between confidence and velocity
  - Provides ~2 weeks of market conditions (various volatility regimes)
  - Sufficient trades for t-test significance with p < 0.05

- **30 days**: Maximum statistical confidence
  - Risk: Slow iteration, market conditions may change during test
  - Use case: Conservative deployment for high-stakes production

**Current State:** Not yet implemented

**Decision Required By:** Phase 4 (A/B Testing Framework)

**Recommendation:** Start with 14 days, make configurable per formula based on expected trade frequency and volatility

---

## 2. Formula Capacity Management

**Context:** Unlimited formulas in production creates computational overhead and meta-learner overfitting risk.

**Options:**
- **Fixed cap (50 formulas)**: Simple, predictable resource usage
  - When limit reached, auto-demote lowest-performing 10% to deprecated state
  - Prevents unbounded growth, forces quality bar

- **Dynamic cap based on compute resources**: Scale formula count with available CPU/memory
  - Monitor p99 latency; if exceeds 100ms, pause new formula additions
  - Allows growth but risks unexpected resource exhaustion

- **Tiered allocation**: Different limits per formula source
  - SageMaker PySR: 30 formulas
  - Manual expert formulas: 10 formulas
  - External research: 10 formulas

**Current State:** Not yet implemented

**Decision Required By:** Phase 3 (Formula Integration)

**Recommendation:** Start with fixed cap of 50, monitor meta-learner performance and latency, adjust based on data

---

## 3. Disaster Recovery for Personal Computer Offline

**Context:** If personal trading computer goes offline for extended period, formulas stop receiving outcome data which breaks A/B tests, circuit breakers, and performance tracking.

**Options:**
- **Auto-pause with alerts**: GitHub Actions detects no outcomes for 24 hours
  - Pauses all running A/B tests (resume when computer online)
  - Sends Slack alert to investigate
  - Formulas remain in current state (production/staging)
  - Risk: Tests delayed, but no data corruption

- **Auto-demote to staging**: After 24 hours offline, move production formulas to staging bucket
  - Prevents relying on formulas with stale performance data
  - Requires re-testing when computer recovers
  - Risk: Aggressive, may unnecessarily disrupt production

- **Graceful degradation**: Switch to backup trading computer or AWS failover
  - Requires redundant infrastructure (additional cost)
  - Ensures continuous operation
  - Risk: Complex setup, may not be cost-effective for personal use

**Current State:** Not yet implemented

**Decision Required By:** Phase 5 (Circuit Breakers)

**Recommendation:** Start with auto-pause + alerts, evaluate need for failover based on actual downtime frequency

---

## 4. Formula Evaluation Safety (Decided)

**Decision:** Use sympy `sympify` + `lambdify` for safe formula evaluation. Never use `eval()` or `exec()`.

**Rationale:** Prevents code injection attacks from malicious formulas.

**Status:** Approved - Implement in Phase 3

---

## 5. Storage Format (Decided)

**Decision:** Use JSONL (JSON Lines) format with S3 partitioning by date (`YYYY-MM-DD/formulas-{timestamp}.jsonl`)

**Rationale:**
- Append-friendly for versioning
- Human-readable for debugging
- Compatible with the query engine, pandas, Python
- Easy partitioning for cost optimization

**Status:** Approved - Implement in Phase 1

---

## 6. Formula Integration Strategy (Decided)

**Decision:** Implement Strategy A (formulas as individual RAT models) initially, with documented upgrade path to Strategy C (hybrid model + feature engineering)

**Rationale:**
- Strategy A provides clean separation, easy performance tracking, supports versioning
- Strategy C can be added later without breaking existing architecture
- Meta-learner automatically handles weighting

**Status:** Approved - Implement in Phase 3

---

## 7. Legacy Warehouse Write Strategy (Decided)

**Decision:** Use the awswrangler Iceberg writer with MERGE upsert, replacing PyIceberg.

**Rationale:**
- PyIceberg required packaging native binaries (pyarrow, etc.) as a custom Lambda layer — exceeded 262 MB limit
- awswrangler is available via the AWSSDKPandas managed Lambda layer (zero custom binaries)
- MERGE INTO via `merge_cols` provides atomic upsert: matched rows updated, new rows inserted
- Single engine query per write — more efficient than DELETE + INSERT (no full partition scan)
- Idempotent: re-running for the same day safely updates existing data

**Trade-offs:**
- Slightly slower than direct PyIceberg writes (goes through the engine SQL layer)
- Depends on query-engine v3 availability
- MERGE produces copy-on-write (COW) commits — small write amplification acceptable for daily pipeline

**Status:** Decided - March 2026

---

## 8. Legacy Warehouse Commit Mode (Decided)

**Decision:** Copy-on-write (COW) - the only mode the legacy engine's Iceberg support offered.

**Rationale:**
- The legacy engine did not support merge-on-read (MOR) for Iceberg tables
- COW is optimal for this use case: reads outnumber writes ~1000:1
- Each snapshot is self-contained — no reconciliation overhead at query time
- Parquet files are clean after rewrite — scans are always efficient
- Write amplification is negligible for a once-daily pipeline

**Status:** Documented - Not a choice, but important architectural constraint

---

## 9. Schema Flattening Strategy (Decided)

**Decision:** Promote stable features from `features map<string,double>` to native Iceberg columns. Keep the map as a landing zone for experimental sources.

**Context:** PySR formula discovery tests millions of combinations (e.g., `(feat_a / delta_b) * log(feat_c)`). Accessing a Map index for every operation is significantly slower than accessing a native column. The query engine can predicate-push on native columns but not map values.

**Rationale:**
- Native columns enable columnar predicate pushdown (Parquet statistics)
- Formula discovery reads vastly outnumber writes — optimise for read performance
- Iceberg schema evolution (`ALTER TABLE ADD COLUMNS`) is non-breaking
- The `features` map remains for experimental data sources that haven't earned a dedicated column
- Promotes a clear lifecycle: experimental data enters via `features` map → proves value → promoted to column

**Implementation:** Phase 2 -- add ~18 technical + fundamental + sentiment columns and ~8 delta columns

**Status:** Approved - Implement in Phase 2

---

## 10. Pre-Calculated Delta Strategy (Decided)

**Decision:** Store pre-calculated deltas (momentum, volatility, z-scores, sentiment velocity) as native columns alongside base features.

**Context:** Every feature can have standard "delta" transformations. Computing these at query time during formula discovery means N rows × M formula candidates of redundant computation.

**Categories:**
- **Price Momentum**: 1-day, 5-day, 20-day percentage changes in close price
- **Rolling Volatility**: 10-day realised volatility (annualised std of returns)
- **Z-Scores**: 30-day normalised values for cross-stock comparison (e.g., £100 move in AZN vs penny stock)
- **Sentiment Velocity**: Rate of change of sentiment scores (Neutral→Positive stronger than static Positive)

**Trade-offs:**
- More columns = wider rows = marginally more S3 storage
- Delta computation requires multi-day lookback — feature engine must read historical data
- Early rows in backfill will have NULL deltas until lookback window fills

**Status:** Approved - Implement in Phase 2

---

## 11. Backfill Strategy (Decided)

**Decision:** Phased backfill — 1 month hourly first (validation), then 20 years daily (production).

**Rationale:**
- 1-month hourly validates the new schema + delta calculations without committing to a large data load
- yfinance supports hourly data for the last 730 days — sufficient for validation phase
- 20-year daily data is well-supported by yfinance for all FTSE 100 symbols
- Hourly data beyond ~2 years would require a different provider (Alpha Vantage, Polygon)
- Batched processing (by month or symbol) prevents Lambda timeouts and yfinance rate limits

**Implementation plan:**
1. Validate schema with 1-month hourly backfill (~13k rows)
2. Verify delta accuracy with manual spot-checks
3. Confirm daily pipeline still works alongside hourly data
4. Run 20-year daily backfill (~435k rows) in monthly batches
5. OPTIMIZE table after bulk load to consolidate files

**Status:** Approved - Implement in Phase 2

---

## 13. Optional Dependency Import Pattern with Sentinel Class (Decided)

**Decision:** When importing optional packages (e.g., `awswrangler` in Lambda layer, `requests` for external APIs), use try/except ImportError at module level with a sentinel exception class fallback.

**Pattern:**
```python
try:
    import awswrangler as wr
    from awswrangler.exceptions import ServiceApiError as _WranglerServiceApiError
except ImportError:
    wr = None
    class _WranglerServiceApiError(Exception):
        pass
```

**Rationale:**
- Prevents `NameError` at use-site if the package isn't installed
- Allows except clauses to reference the exception type without guards
- Libraries provide runtime through managed Lambda layers (AWSSDKPandas); local dev may skip them
- Sentinel class enables graceful degradation: code referencing the exception doesn't crash if the library is absent

**Context:** Discovered during code review when `ServiceApiError` was initially missing from an optional-dependency except clause in a since-retired writer module. Local validation (pytest, ruff, mypy) did not catch the incomplete exception type, flagging a static analysis blind spot for optional dependencies.

**Applies to:** AWS integrations (boto3, awswrangler), external API providers (requests)

**Status:** Decided - March 2026, applied to a since-retired writer module

---

## 14. Subsystem-Aware Exception Hierarchy (Decided)

**Decision:** Narrow bare `except Exception` to subsystem-specific exceptions rather than catch-all patterns. Each subsystem has predictable, known failure modes.

**Categories:**
- **AWS**: `botocore.exceptions.ClientError`, `awswrangler.exceptions.ServiceApiError`
- **Network**: `ConnectionError`, `TimeoutError`, `requests.RequestException`
- **Asyncio**: `asyncio.CancelledError` (must re-raise before other handlers)
- **Data Validation**: `ValueError`, `TypeError`, `sympy.SympifyError`

**Rationale:**
- Specific catches enable targeted recovery logic (e.g., exponential retry for transient network errors, circuit breaker for cascading failures)
- Maintains observability: logs can distinguish between AWS quota exhaustion, network flakiness, and data corruption
- Avoids false negatives: uncaught exceptions are easier to debug than swallowed ones
- Signals intent: explicit exception list is self-documenting

**Context:** Applied across `src/lab/pysr_factory.py` (AWS), `src/execution/async_engine.py` (asyncio + network), and `src/data/feature_engine.py` (data validation + network in retry logic).

**Status:** Decided — March 2026

---

## 15. Process-Local Caching with Monotonic Time (Decided)

**Decision:** For in-process memory caches (suitable for Lambda/script contexts with isolated invocations), use `time.monotonic()` for TTL calculation instead of `time.time()`.

**Cache TTL Pattern:**
```python
_cache: dict = {}
_CACHE_TTL = 300  # seconds

if key in _cache:
    cached_value, cached_time = _cache[key]
    if time.monotonic() - cached_time < _CACHE_TTL:
        return cached_value
```

**Rationale:**
- `time.monotonic()` is immune to system clock adjustments (NTP, time zone changes)
- Prevents cache invalidation during daylight saving time transitions
- Correctly handles suspended processes (Lambda containers resumed after pause)

**Architectural Limits:** Document as a comment: "In-process memory cache suitable for Lambda/script contexts where multiple invocations are isolated. Long-running services (APIs, consumers) need external cache (Redis, memcached)."

**Use case:** Fear & Greed Index fetch (low volume, slow change, accessed once per trading session)

**Non-use case:** Market data cache (high-frequency, accessed per-formula, requires external cache if running long-lived service)

**Context:** Applied to `src/data/feature_engine.py:_fetch_fear_greed_index()` with 5-minute TTL and 3x retries.

**Status:** Decided — March 2026

---

## 16. Consecutive-Failure Circuit Breaker Pattern (Decided)

**Decision:** Count consecutive failures (reset to 0 on success) and stop loop after N threshold. Do not count cumulative failures or transient single errors.

**Pattern:**
```python
consecutive_failures = 0
while True:
    try:
        # perform work
        consecutive_failures = 0  # reset on success
    except SpecificException:
        consecutive_failures += 1
        if consecutive_failures >= 5:
            logger.critical("Circuit breaker: stopping after %d consecutive failures", consecutive_failures)
            break
        time.sleep(backoff)
```

**Rationale:**
- Gentler than fail-fast: single transient error doesn't stop the loop
- Catches cascading failures: if the system is degraded (5+ sequential failures), stop rather than thrashing
- Single counter variable is simpler than tracking timestamps or moving window
- Must reset on success to distinguish "cascading failure" from "occasional transients"

**Applies to:** Production loops (trading, synchronized data loads, A/B test runners) where transient errors are common but sustained failure indicates deeper issues.

**Context:** Applied to `src/execution/async_engine.py:trading_loop()` with threshold of 5 consecutive failures.

**Status:** Decided — March 2026

---

## 17. Module-Level Logger Setup (Decided)

**Decision:** Initialize loggers at module level (`logger = logging.getLogger(__name__)`) and use module-level constants for log levels. Do not instantiate loggers inside functions or loops.

**Anti-pattern to avoid:** `import logging` inside a loop (per-row processing) incurs import overhead every iteration.

**Rationale:**
- Logger instantiation at module load is idiomatic Python (matches `logging` best practices)
- Enables structured logging across the codebase (consistent logger names, hierarchy)
- Removes per-function or per-loop overhead
- Facilitates testing: loggers are easily configurable (e.g., setting level in test fixtures)

**Context:** Applied to `src/lab/pysr_factory.py` and `src/execution/async_engine.py` as part of error handling cleanup.

**Logging Hierarchy (for observability):**
- DEBUG: Retry attempts, recovery actions
- WARNING: Transient failures, warning-level degradation
- ERROR: Failure after retries exhausted, recoverable errors
- CRITICAL: Circuit breaker stops, unrecoverable failures

**Status:** Decided — March 2026

---

## 18. Config Loading: Lazy Validation vs. Eager Failure (Decided)

**Decision:** `Config._load_config()` logs a warning and returns an empty dict when the config file is missing. The `validate()` method raises `FileNotFoundError` (or other validation errors) only when explicitly called.

**Rationale:**
- **Import-time success:** Config module can be imported in environments that lack config files (CI, Lambda cold start,  ephemeral test containers). This allows pytest collection, import validation (mypy), and code analysis to proceed without environment setup.
- **Explicit validation boundaries:** `validate()` is called only by setup scripts, main entry points, or test fixtures that explicitly require configuration. Errors surface at clear boundaries, not buried in pytest output or import tracebacks.
- **Multi-environment design:** Discovered during CI triage (session agent/infra-recommendations-cleanup): config import was breaking pytest collection because CI runners have no config files. This two-phase design supports:
  - **CI:** Import succeeds; validation skipped.
  - **Lambda:** Import succeeds; validation skipped (IAM credentials via role).
  - **Docker:** Import succeeds; validation called by startup scripts after volumes mounted.
  - **Local dev:** Import succeeds; validation called by setup.py.

**Trade-off:** Original PLAN.md spec stated "raise FileNotFoundError when config missing", but this broke CI. Agent interpreted intent (fail fast on missing config) and implemented superior design (fail at explicit validation, not import) based on multi-environment reality.

**Status:** Decided — March 2026, implemented in session agent/infra-recommendations-cleanup

---

## 19. Terraform File-Optional Operations: Graceful try/catch Pattern (Decided)

**Decision:** Terraform modules use `try()` functions to wrap file operations that may not exist in CI or test environments. Example:
```hcl
source_code_hash = try(
    filemd5("build/lambda.zip"),
    md5(file("terraform/data_pipeline.tf"))  # fallback
)
```

**Rationale:**
- **CI safety:** In CI, Terraform planning happens before build artifacts are generated. The `try()` allows Terraform to plan safely using a fallback hash; production applies use the actual artifact hash (computed when file exists).
- **Idempotent IaC:** Terraform should declare intent (use artifact hash to detect changes), not assume artifact preconditions. This aligns with declarative infrastructure-as-code principles.
- **Prevents silent divergence:** Without the `try()`, Terraform would fail in CI and succeed locally, creating environment-specific behaviours that are hard to debug.

**Pattern:** Always wrap `filemd5()`, `file()` calls on build artifacts with `try()` + sensible fallback (static hash, contents of a metadata file, empty string).

**Context:** Discovered during CI triage when `terraform validate` failed on missing `lambda-packages/data-pipeline.zip`.

**Status:** Decided — March 2026, implemented in session agent/infra-recommendations-cleanup

---

## 20. CI-Safe Error Handling: Defer Validation to Explicit Boundaries (Decided)

**Decision:** Validation that is specific to certain environments (e.g., config file existence, AWS credentials, binary tool availability) should not raise exceptions during module import. Errors are raised during explicit `validate()` calls or setup functions, allowing import-time to succeed. This decouples environment setup from code loading.

**Rationale:**
- **Pytest collection:** Import-time errors break pytest collection in CI, preventing syntax checks (mypy, import resolution) before environment setup is complete.
- **Signal-to-noise:** Deferring validation to explicit boundaries improves CI logs: import validation (fast) and syntax checks (mypy) pass; only integration tests that require the missing resource fail.
- **Multi-phase startup:** Many environments (Lambda, containers, CI) have distinct phases: (1) import/parse code, (2) configure/validate dependencies, (3) run business logic. Protocol errors and missing binaries should fail in phase (2), not phase (1).

**Examples:**
- `config.py:_load_config()` warns and returns empty dict on missing file; `validate()` raises `FileNotFoundError`.
- `setup.py:check_postgres()` checks exit code explicitly and logs `[WARNING]`; doesn't prevent script execution.
- `plan_audit.py:file_existed_on_main()` logs debug and returns False gracefully when remote is missing; doesn't raise.

**Anti-patterns to avoid:**
- Raising `FileNotFoundError` at module import time (breaks pytest collection).
- `import binary_tool as tool` at module level without a fallback (breaks on missing binary).
- Silent failures with no logging (makes debugging impossible).

**Status:** Decided — March 2026, documented as systemic principle from CI triage findings

---


## Decision-Making Process

When a decision is ready:
1. Gather data from production metrics (if available)
2. Document pros/cons with team input
3. Make decision and update this document with APPROVED status
4. Create implementation ticket linked to roadmap phase
5. Archive to `docs/decisions/YYYY-MM-decision-name.md` when implemented

---

## Future Decisions (Not Yet Scoped)

- Multi-asset formula generalization (works across different symbols?)
- Formula explainability (SHAP values for regulatory compliance)
- Real-time feature drift detection
- Cross-formula correlation analysis (avoid redundant strategies)
- Formula licensing/IP management (if sourcing from external researchers)
- Intraday data provider for hourly data beyond 730 days (Alpha Vantage vs Polygon)
- Additional alternative data sources (Reddit sentiment, news NLP, satellite imagery)
- Lambda vs local script for large backfills (cost vs convenience trade-off)

---

## 12. Decision-Grain Table Architecture (Decided)

**Context:** Phase 2 introduces hourly backfill data. The original design used `trade_date` as the temporal MERGE key, implicitly assuming one row per symbol per day. With multiple granularities (hourly, daily, eventually 15-minute), the table design needed to scale without sentinel values or joins at query time.

**Options considered:**
- `trade_hour` integer sentinel (hour=0 for daily) — opaque, breaks for midnight-trading assets, wrong abstraction
- `is_daily` boolean — self-documenting but still mixes granularities, requiring `WHERE is_daily = true` everywhere
- Separate tables per granularity (`market_data_daily`, `market_data_hourly`) — clean keys but forces JOINs when a formula uses cross-grain features (e.g., `daily_sma20 * intraday_vwap`)
- Flatten sub-daily into daily columns — duplicates data, creates massive nulls for hourly-only features

**Decision:** Single decision-grain `market_data` table with an `interval` string column (`'1d'`, `'1h'`, `'15m'`, etc.) plus a separate append-only `market_data_raw_hourly` archive for provenance.

- **MERGE keys**: `(symbol, timestamp, interval, source)` — `timestamp` is bar-precise; `interval` disambiguates grain
- **Partitioning**: `trade_date` — still efficient at any sub-daily frequency
- **New granularity = new feature columns**, not new tables; intraday data is aggregated into features on the decision-grain row by `feature_engine.py`
- **`market_data_raw_hourly`**: append-only raw OHLCV archive written by the backfill handler; never read by PySR or live engine directly
- **`trade_date` retained as partition column** on both tables — efficient predicate pushdown for time-range scans

**Rationale:**
- Zero joins at PySR training time — all features on one flat row
- `interval = '1d'` filter in historical lookback and discovery queries ensures daily-only data for current pipeline
- Adding hourly trading in future = one new PySR job with `WHERE interval = '1h'` + new hourly feature columns via schema evolution
- Single schema to maintain; Iceberg schema evolution handles new columns non-breakingly

**Status:** Decided — March 2026

---

## Code Review Exemptions

Findings from code-review marked as intentional, not-applicable, or accepted-risk. The code-review agent checks this section and skips matching issues.

| Date Added | File/Location | Issue Type | Reason | Expires |
|------------|---------------|------------|--------|---------|

**To add an exemption:** After a code review, say "This is intentional because [reason]" and the agent will add an entry.

**Expiration:** Leave blank for permanent. Use Expires for temporary exemptions (e.g., "until Phase 2").

**Removal:** When the underlying issue is fixed, remove the row.

---

## Rejected Cron Suggestions

Suggestions surfaced by the cron review system that were reviewed by a human and explicitly rejected. Entries here inform .rejected-suggestions.jsonl so the cron reviewer does not re-surface the same category of suggestion.

*(No rejected cron suggestions yet.)*

---


## Decision 26: Workflow Cost Optimisation (Decided)

**Reconciliation note (2026-07-21):** a divergent live `docs/DECISIONS.md` duplicate ("Decision 26: Workflow Cost Optimisation via 2-Chat Model and Automation", Agent-decided -- pending review; two supersessions stale via Decision 42->90, its artefacts deleted at T-1.13) was reconciled into this canonical archived entry and removed from the live file (DPI-05). This archived entry is now the sole Decision 26 across both files.

**Context:** The 3-chat workflow (/plan, /implement, /session_close) reloaded context files 3 times per cycle. Deterministic steps (venv check, git status, validation, CI polling) consumed expensive LLM tokens. Analysis scripts output to stdout only, preventing trend analysis over time.

**Decision:** Merge to 2-chat workflow with deterministic offloading:
- `scripts/session_preflight.py`: All pre-session checks (venv, git status, SSO, cron freshness, recommendations, friction patterns, anomalies), outputs JSON to `logs/.preflight-report.json`
- `scripts/session_postflight.py`: Validation, commit, push, CI polling, auto-merge
- `/implement` absorbs all `/session_close` steps (Session Close Phase, Steps 11-23)
- Retrospective model changed from Sonnet (1x) to Haiku (0.33x) — full context available, no reconstruction needed
- Analysis scripts (`friction_analysis.py`, `metrics_analysis.py`, `plan_audit.py`, `north_star_tracker.py`) persist JSONL records for trending
- Clean sessions recorded with `friction="clean"` (not skipped) for friction rate calculation

**Estimated savings:** 30-40% reduction in Opus/Sonnet tokens per workflow cycle

**Rationale:**
- Context reload eliminated by merging chats (saves ~5-10K tokens per cycle)
- Deterministic work in Python is free; equivalent LLM work is expensive
- Haiku sufficient for retrospective when full session context is already available in the same chat (3x cheaper than Sonnet)
- Persisted analysis enables recursive self-improvement measurement via JSONL trending

**Rejected alternatives:**
- Keep 3-chat workflow: preserves status quo but wastes 30-40% tokens on needless context reloads
- Fully automated postflight without any human review: removes the code review gate that catches Critical/High findings

**Status:** Decided — March 2026

---

## Decision 54: Lambda Scheduled Agents Revert to Copilot SDK (Supersedes Decision 52 for Lambda)

**Trigger:** Bedrock on-demand token quotas throttled to 0 on personal account. Company account Bedrock revoked by AI Steering Group. Gemini CLI is local-only and cannot run in Lambda. Data residency concerns prevent using Gemini API from company-adjacent infrastructure.

**Decision:** Lambda scheduled agents revert to GitHub Copilot SDK. `claude-haiku-4.5` for 5 lightweight agents, `claude-sonnet-4.6` for rec-curator (per Decision 49 -- highest reasoning demand). This was the pre-Decision-52 configuration and is documented as the swap-back plan in `docs/contracts/inference-provider.md`. Code change required: re-enable Copilot SDK pip install in `scripts/build_lambda.py`.

**Key details:**
- Provider: `copilot-sdk` (via `scripts/copilot_sdk_client.py`)
- Models: `claude-haiku-4.5` for 5 lightweight agents, `claude-sonnet-4.6` for rec-curator (per Decision 49 -- highest reasoning demand)
- Auth: GitHub OAuth token (`gho_` prefix from `gh auth token`) stored in Secrets Manager as `agent-platform-github-pat`
- Premium requests: uses the small remaining allocation of GitHub Copilot premium requests
- Executor path unchanged: still uses Gemini CLI locally (Decision 53)
- Bedrock path: dormant, retained for rollback
- Lambda build: `scripts/build_lambda.py` re-enables `github-copilot-sdk==0.2.2` pip install

**Supersedes:** Decision 52 for Lambda agents only. Decision 53 (Gemini CLI for executor) is unaffected.

**Status:** Decided -- April 2026

---

## Decision 53: Gemini CLI as Executor Inference Provider (Partially Supersedes Decision 52)

**Trigger:** Bedrock on-demand token quotas (L-F1541587, L-C43703DE, L-3AE31EFC) throttled to 0 on personal account REDACTED-PERSONAL-ACCOUNT across all regions. Company account Bedrock access revoked by AI Steering Group pending governance review. Copilot CLI signups paused. Decision 52 Bedrock architecture cannot execute after only 1 successful call (666 input + 550 output tokens).

**Decision:** Executor pipeline (`scripts/execute_recommendation.py`) uses Gemini CLI (Google Pro plan) for all LLM calls via `scripts/llm_client._gemini_call()`. Model selection via `scripts/model_registry.py` reading `config/copilot_model_routing.yaml`. Lambda scheduled agents unchanged (still use Bedrock). Interactive sessions unchanged (VS Code Copilot Chat).

**Key details:**
- Active provider: `gemini-cli` (Google Pro plan, 1,500 req/day + 1,000 AI credits/month)
- CLI: `gemini -p "prompt" --output-format json` (preview version 0.40.0+ required for Gemini 3)
- Models: `gemini-3-pro-preview` (pro tier), `gemini-3-flash-preview` (flash tier), null=auto
- Model routing: `config/copilot_model_routing.yaml` → `scripts/model_registry.resolve_model()`
- Resolver: effort-band → tier → model ID; file-pattern floors for executor/prompt files → pro tier
- Escalation: flash → auto → pro → None (human intervention)
- Default in `llm_client._resolve_provider()`: delegates to `model_registry.resolve_provider()` which defaults to `"gemini"`. Lambda handlers do not call this path -- they route by the `provider` field in `schedule.yaml`.
- Dormant Bedrock: reactivate via `LLM_PROVIDER=bedrock` when/if AWS Support resolves quota throttling
- Known SPOF: personal Google Pro subscription; Bedrock is the dormant fallback
- Data residency: executor runs locally on personal machine with personal Google account; NOT subject to company sandbox data residency constraints (re-assessment needed if executor moves to company infra)
- GEMINI.md context file: imports `.github/copilot-instructions.md` so all project rules apply to Gemini executor calls
- Lambda migration: deferred to a separate session (session token export / OAuth to Secrets Manager)

**Supersedes:** Decision 52 for executor path only. Decision 52 still applies to Lambda agents and the general Bedrock architecture (which remains dormant but functional).

**Related:** Decision 44 (executor boundary), rec-379 (model routing config), rec-380 (model_registry.py), rec-381 (wire executor through resolver)

**Status:** Decided -- April 2026

---

## Decision 52: Bedrock Migration -- DeepSeek V3.2 Default (Supersedes Decisions 37, 40, 49)

**Trigger:** GitHub premium request restrictions eliminated the Copilot CLI as a viable inference backend. Gemini BYOK (Decision 49) violated UK data residency requirements for the company sandbox account.

**Decision:** Migrate all LLM inference from GitHub Copilot CLI / Copilot SDK / Gemini BYOK to AWS Bedrock using DeepSeek V3.2 as the single default model.

**Key details:**
- Model: `deepseek.v3.2` via Bedrock `converse()` API
- Region: eu-west-2 (personal account REDACTED-PERSONAL-ACCOUNT)
- Auth: IAM credentials via `~/.aws/credentials` profile `personal-bedrock-profile` (local) or Secrets Manager (Lambda cross-account)
- Single-model hierarchy: no escalation chain, no model multipliers
- 128K context window drives executor eligibility (800 SLOC gate on target files)
- Cost model: per-token Bedrock pricing, tracked via `LLMResult.cost_usd`
- New modules: `scripts/llm_client.py` (transport), `scripts/llm_utils.py` (parsing/errors), `scripts/tool_runtime.py` (tool execution), `scripts/bedrock_client.py` (Bedrock Converse API)
- Deprecated (retained for reference): `scripts/copilot_wrapper.py`, `scripts/copilot_sdk_client.py`, `scripts/copilot_multipliers_refresher.py`, `config/copilot_model_multipliers.yaml`
- Lambda deployment: Copilot SDK removed from build; `llm_client.py`, `llm_utils.py`, `tool_runtime.py` added to `_LAMBDA_SCRIPTS`
- DeepSeek quirks: chain-of-thought `<think>...</think>` blocks stripped from responses; Chinese character artifacts cleaned for Windows compatibility

**Rationale:**
- DeepSeek V3.2 128K context at ~$0.90/M input, $2.61/M output (Bedrock on-demand)
- Single model eliminates multiplier complexity and model selection logic
- Bedrock converse() API returns structured token counts (no regex parsing)
- UK data residency satisfied (eu-west-2 Bedrock endpoint)
- Rollback: revert to Copilot CLI by restoring `copilot_wrapper.py` imports

**Rejected alternatives:**
- Continue with Copilot CLI: premium request quotas make this unsustainable
- Gemini BYOK: data residency violation for company sandbox
- Multi-model Bedrock: unnecessary complexity when single model meets all use cases

**Status:** Decided -- April 2026

---

## Decision 71: cc-scheduled-agents Cron Mechanism is GitHub Actions on Self-Hosted Runner (Superseded by Decision 73)

**Status:** Superseded by Decision 73
**Superseded by:** Decision 73

**Status:** Decided
**Date:** 2026-05-09

**Problem:**
Parent plan PLAN-cc-scheduled-agents.md D15 specified the Anthropic-hosted `schedule` skill (CronCreate) as the cron mechanism for cc-scheduled-agents to avoid OIDC complexity and GitHub Actions billing. Decision 68 (self-hosted EC2 runner) resolved both concerns: CI minutes are free on the self-hosted runner, and `GITHUB_TOKEN` auto-injection by the GitHub Actions platform solves the credential problem that was the core unresolved risk (parent plan Q9).

**Decision:**
The cron mechanism for cc-scheduled-agents Phase 4 is a GitHub Actions scheduled workflow (`on: schedule: - cron: '0 8 * * *'`) running on `[self-hosted, linux]`. Claude Code CLI is invoked headlessly via `claude -p`. Auth uses `CLAUDE_CODE_OAUTH_TOKEN` (Max subscription, zero marginal API cost per invocation). The `schedule` skill (CronCreate) is not used for this project.

**Consequences:**
- Phase 3 (this plan) designs the agent for headless invocability via `claude -p --output-format json`.
- Phase 4 writes `.github/workflows/scheduled-agents.yml`. That file is not in Phase 3 scope.
- No Anthropic-hosted cron billing -- the scheduled workflow runs on the self-hosted EC2 runner.
- `CLAUDE_CODE_OAUTH_TOKEN` must be stored as a GitHub Actions repository secret. Setup walkthrough added to `CLAUDE.md`.

**Reverses:** Parent plan D15 (Anthropic-hosted `schedule` skill as cron mechanism).
**Closes Open Questions:** Q9 (GitHub credentials in GH Actions = `GITHUB_TOKEN` auto-injected by platform).
**Remaining Open Questions:** Q6, Q7, Q8, Q10 (deferred to Phases 4-5).
**Related:** Decision 68 (Self-hosted EC2 runner), `docs/plans/PLAN-cc-scheduled-agents.md`

> **2026-05 migration update:** The self-hosted runner substrate referenced here was retired 2026-05-28 (CD.21). cc-scheduled-agents migrated to Claude Code scheduled agents on GitHub-hosted runners; see Decision 73.

---

## Decision 68: Self-Hosted EC2 Runner as Canonical CI Execution Environment (Superseded by Decision 112)

**Status:** Superseded by Decision 112
**Superseded by:** Decision 112

**Status:** Decided
**Date:** 2026-05-08

**Problem:**
2000 min/month free tier exhausted at current PR velocity; branch protection disabled as workaround; cc-scheduled-agents Phase 4 blocked by per-run GitHub Actions billing (~23 CI minutes per agent PR).

**Decision:**
EC2 self-hosted runner (`t3.medium`, Ubuntu 22.04, `eu-west-2`) is the canonical CI execution environment. IAM instance role with `Ec2InstanceMetadata` credential delegation replaces the SSO profile requirement in CI. Cold-start only for Phase 1 (full checkout + pip install per job).

Note: initially planned as `t3.small` (2 GB RAM) but upgraded to `t3.medium` (4 GB RAM) during initial deployment after mypy exhausted available memory and OOM-killed the runner process mid-job. The 2 GB headroom on `t3.medium` accommodates mypy + pytest + runner process without swap pressure.

Warm runner is a named future phase requiring its own risk assessment: (a) hash-gate the venv against `requirements.txt` on every job pickup to prevent stale-dependency false-greens, (b) workspace reset on branch switch, (c) concurrency-safe workspace locking. Do not implement without this plan.

SCD data transfer boundary: code execution moves to the project's EC2 instance. AWS credentials never leave the instance (instance metadata; no env var injection into GitHub). Job logs stream to GitHub's log storage -- equivalent posture to GitHub-hosted runners. Tests must never print classified data values (symbol lists, strategy names) to keep log content non-classified.

**Consequences:**
- Branch protection (`required_status_checks`) can be re-enabled on `main` after a 1-week runner stability observation window.
- cc-scheduled-agents Phase 4 (daily cron auto-merge PRs) is unblocked.
- Zero billed CI minutes for all branch builds and scheduled agent merges.

**Related:** Decision 36 (AWS Auth -- no IAM users, no OIDC), Decision 37 (Lambda scheduled agents), Decision 60 (Two-tier validation architecture), `terraform/ec2_runner.tf`, `CLAUDE.md` runner ops runbook.

> **2026-05 migration update:** The self-hosted EC2 runner described here was retired 2026-05-28 per CD.21. CI migrated to GitHub-hosted runners (`ubuntu-latest`) with OIDC to the personal account; see Decision 73. The EC2 Terraform definition is retained in `terraform/ec2_runner.tf` as an architectural artefact (no longer applied).

---

## Decision 49: Copilot SDK as Lambda Inference Provider (Superseded by Decision 116)

**Status:** Superseded by Decision 116
**Superseded by:** Decision 116

**Decision:** The GitHub Copilot SDK (`github-copilot-sdk` v0.2.2) replaces AWS Bedrock as the inference provider for all Lambda-executed scheduled agents. Model IDs use Copilot SDK format (e.g., `claude-haiku-4.5`, `claude-sonnet-4.6`). Auth uses the existing Secrets Manager GitHub PAT.

**Problem:**
On April 2026, AWS Bedrock access was revoked in the sandbox account (REDACTED-ACCOUNT-ID) because the models were accepted without proper AI Steering Group approval. All 6 scheduled agents stopped functioning. The GitHub Models API (the previous fallback) lacks Claude and Gemini models -- only OpenAI, DeepSeek, and Grok models are available -- making it inadequate for quality-sensitive agents like rec-curator.

**Why Copilot SDK over alternatives:**
- **GitHub Models API:** No Claude, no Gemini. GPT-4.1-mini quality too low for rec-curator.
- **Bedrock (restored):** Would require AI Steering Group re-approval. Timeline unknown.
- **Copilot SDK:** Provides Claude Haiku 4.5 (0.33x multiplier), Sonnet 4.6 (1x), and other high-quality models. Uses existing GitHub PAT from Secrets Manager. Fits in Lambda zip (69 MB total). Confirmed working via live tests.

**SDK architecture:**
The SDK spawns a Copilot CLI subprocess via JSON-RPC. The CLI binary (~58 MB) is bundled in the pip wheel. Auth is via `SubprocessConfig(github_token=...)`. Sessions are created per-inference call with `tools=[]` to disable agent tool use.

**Model mapping:**
| Agent | Model | Multiplier |
|-------|-------|------------|
| doc-freshness, orphan-code, transcript-review, code-smell, prompt-quality | `claude-haiku-4.5` | 0.33x |
| rec-curator | `claude-sonnet-4.6` | 1x |

**Lambda packaging:**
SDK is pip-installed into `data-pipeline.zip` (not the deps layer) using `--platform manylinux_2_28_x86_64`. The CLI binary at `copilot/bin/copilot` must have executable permissions (0o755) in the zip. Total SDK footprint: ~69 MB (binary 58 MB + Python 1.2 MB + pydantic 9 MB + dateutil 0.6 MB).

**Constraints:**
- SDK is Public Preview (v0.2.2) -- pin version in `build_lambda.py` to prevent breakage
- `bedrock_client.py` is retained as dormant code -- available if Bedrock access is restored
- Lambda memory may need increase from 512 MB to 1024 MB if CLI subprocess causes OOM
- `_preload_rec_curator_context()` still required -- SDK `tools=[]` disables file reading

**Related:** Decision 47 (superseded), Decision 40 (Copilot SDK for executor -- still deferred, separate concern), Decision 48 (V3 verification tier), `docs/contracts/inference-provider.md`

**Decision status:** Decided -- April 2026

**Superseded by: Decision 116**

---

## Decision 42: Three-Tier Workflow Architecture (Superseded by Decision 90)

**Status:** Superseded by Decision 90
**Superseded by:** Decision 90

**Decision:** Separate the human-agent workflow into three tiers with distinct responsibilities: `/plan` (strategic), `/implement` (scoping), `/develop-executor` (autonomous execution). Non-automatable recommendations must be surfaced and discussed in `/plan`, not accumulated silently.

**Problem:**
- `/plan` was overloaded: produced strategic decisions AND detailed execution steps
- `/implement` followed execution steps but had no scoping authority
- Non-automatable recs accumulated without resolution path
- Executor defaulted to single-rec mode, leaving throughput on the table

**Architecture (three-tier):**
```
Human Intent
     |
     v
/plan (STRATEGIC)
  - Decisions + Work Areas
  - Mandatory non-automatable rec discussion
  - Output: PLAN-{slug}.md with Work Areas table
     |
     v
/implement (SCOPING)
  - Research each Work Area
  - Break into atomic recs (effort <= M)
  - Create briefing files for complex recs
  - Output: Populated recommendations log
     |
     v
/develop-executor (AUTONOMOUS)
  - Compound execution (3-4 recs, effort <= M total)
  - Files friction recs on failure
  - Output: Code changes + PR
     |
     v
Friction recs (automatable: false)
     |
     v
Back to /plan preflight (mandatory discussion)
```

**Key design principles:**
1. **Separation of concerns** -- each agent has one job, can be tuned independently
2. **No open loops** -- every friction point has a resolution path
3. **Non-automatable recs surface** -- preflight shows them, `/plan` must discuss before proceeding
4. **Compound execution default** -- executor picks 3-4 recs (effort <= M, max 4) unless overridden
5. **Stale rec detection** -- rec-curator flags `automatable: false` recs older than 30 days

**Compound execution bounds:**
- Effort weights: XS=0.5, S=1, M=2, L=4, XL=8
- Max total effort per compound batch: M (=2)
- Max recs per batch: 4
- Prefer same-file recs (reduces merge conflicts)
- Prefer recs with shared dependencies

**Trade-offs accepted:**
- `/plan` sessions may be longer due to mandatory non-automatable discussion
- Compound execution may have harder-to-attribute failures (mitigated by per-rec telemetry)

**Related:** Decision 38 (workflow consolidation), Decision 40 (Copilot SDK deferred)

**Decision status:** Decided -- April 2026 (Superseded by Decision 90)

---

## Decision 40: Executor Platform Migration — Copilot SDK + Bedrock Planning (Superseded by Decision 122)

**Status:** Superseded by Decision 122
**Superseded by:** Decision 122

**Decision:** Migrate the executor's LLM interface from raw Copilot CLI subprocess calls to the GitHub Copilot SDK, and adopt AWS Bedrock as the planning backend via the SDK's BYOK (Bring Your Own Key) capability. Implementation is deferred until the Copilot SDK reaches stable/v1.0 or a trigger condition is met.

**Problem:**
The executor (`scripts/execute_recommendation.py`) invokes the Copilot CLI via `subprocess.Popen` through `copilot_wrapper.py`. This works but has known friction:
- ~2-5s subprocess startup overhead per call (no persistent server)
- Plan output is prose, parsed by regex (`parse_steps_from_plan`), causing ~30% parsing failures that trigger costly retries
- No prompt caching -- each call (plan, critique, refine) pays full context cost
- No structured output enforcement -- the model can return any format
- `_PLAN_EXCLUDED_TOOLS` is a workaround for agentic models treating `@file` context as implementation tasks
- Sequential rec processing is slow; parallelisation is blocked by the subprocess model

**Options considered:**
- **AWS Bedrock direct (boto3 `converse` API):** Provides structured JSON output via `outputConfig.textFormat.jsonSchema`, prompt caching via `cachePoint` blocks in system prompts (5min/1h TTL), and multi-turn conversation in a single API call. Available in eu-west-2 with Claude Opus/Sonnet/Haiku. However, Bedrock is text-in/text-out -- no file system access, no tool use for implementation. Would require maintaining two separate LLM interfaces (Bedrock for planning, Copilot CLI for implementation).
- **GitHub Copilot SDK (`github-copilot-sdk`, Python):** Released post-project-start, currently Public Preview (v0.2.2, 39 releases in 3 months). Replaces subprocess management with async JSON-RPC to a persistent CLI server. Provides session hooks (`on_pre_tool_use`, `on_post_tool_use`), custom tools, permission handling, and streaming. Crucially, supports BYOK with Anthropic provider type -- meaning Bedrock models can be accessed through the SDK, combining SDK session management with Bedrock's structured output and prompt caching. This eliminates the need for two separate interfaces.
- **Stay on raw Copilot CLI:** Current approach. Works, is resilient, self-monitoring. Slow but stable. Compound execution and acceptance pre-flight (rec-186) mitigate the worst friction points.

**Decision:**
Adopt a single migration path: Copilot SDK with Bedrock BYOK. Do not implement Bedrock directly (avoids maintaining two LLM interfaces). Do not migrate now (SDK is unstable, API surface volatile). The current CLI-based system is adequate for the current phase of the project.

**Cost analysis:** Opus via Copilot CLI has a 3x premium request multiplier. A plan+critique+refine cycle costs ~$0.36-0.90 in premium requests, comparable to Bedrock's ~$0.40 direct cost. Cost is not a driver for migration.

**Trigger conditions for implementation (any one):**
1. Copilot SDK reaches v1.0 or "stable" designation
2. Executor retry rate exceeds 40% sustained over 2 weeks (structured output would eliminate parsing failures)
3. Executor throughput becomes the bottleneck for North Star progress (i.e., Phase 2/3 are complete and rec velocity is the constraint)

**Three-phase incremental migration (when triggered):**
1. **P1: SDK adoption** -- Replace `copilot_wrapper.py` subprocess management with `CopilotClient` async context managers. Persistent server mode eliminates startup overhead. Session hooks replace `_PLAN_EXCLUDED_TOOLS` and `validate_response()`. Implementation stays on Copilot CLI tools.
2. **P2: Bedrock planning backend** -- Configure BYOK with Anthropic provider for planning calls. Structured JSON output via `outputConfig.jsonSchema` eliminates `parse_steps_from_plan` regex. Prompt caching for system prompt + repo conventions. Critique loop runs as multi-turn conversation with cached prefix.
3. **P3: Unified multi-rec planning** -- Bedrock planner receives rec clusters from rec-curator, produces single optimised plan with per-rec step tagging. Parallel planning via `asyncio` + SDK async sessions.

**Trade-offs accepted:**
- Deferring means continued ~30% plan parsing failure rate (mitigated by existing retry + escalation logic)
- Deferring means no prompt caching (mitigated by the CLI's `--resume` session reuse)
- Single migration path (SDK+BYOK) creates a dependency on GitHub shipping BYOK for Anthropic/Bedrock -- if this feature is dropped, fall back to direct Bedrock for planning only

**Related:** rec-186 (acceptance pre-flight), rec-184 (compound critique), Decision 39 (Step Functions orchestration)

**Decision status:** Decided, deferred -- April 2026

> **Update (2026-07-21):** Both migration triggers are now moot -- the Copilot SDK path was retired via Decision 116 (scheduled-agent provider routing) and Bedrock BYOK was retired via CD.28, ratified by Decision 122. Formal supersession of this entry is pending.

---

## Decision 23: Parallel Workflow with Branch-Specific Plans (Superseded by Decision 76)

**Status:** Superseded by Decision 76
**Superseded by:** Decision 76

**Context:** The planning-implement-close workflow required sequential execution: PLAN.md was gitignored and persisted across branches, causing wrong-plan-loaded bugs. Log files written during session_close were left uncommitted after the PR was already created.

**Decision:** Move branch creation to the planning phase and use branch-specific tracked plan files:
- `/plan` creates `agent/{slug}` branch and writes `PLAN-{slug}.md` (tracked)
- `/implement` finds the plan file for the current branch (slug derived from branch name)
- Session Close Phase within `/implement` auto-merges after CI passes, with tiered conflict resolution
- Always branch from main (not from feature branches)

**Conflict resolution tiers (simplified):**
1. Auto-resolve: Append-only logs (SESSION_LOG.md, *.jsonl)
2. Auto-resolve: Structured docs (RECOMMENDATIONS.md, DECISIONS.md) — merge rows/sections
3. Escalate immediately: Code/config files (`.py`, `.tf`, `.prompt.md`) — human resolves

**Rationale:**
- Parallel features can now be planned while implementation is in-flight
- Plan files are tracked per-branch, eliminating cross-branch contamination
- Auto-merge on CI pass is safe because the Session Close Phase of `/implement` is reached only after all implementation steps and code review are complete
- Tiered conflict resolution handles expected concurrent doc edits without human intervention
- Always branching from main prevents dependency chains and isolates conflicts

**Rejected alternatives:**
- Branch from feature branches: creates dependency chains, must merge in order
- Single PLAN.md with branch name in content: still gitignored, still cross-contaminates
- Manual merge only: adds friction, delays integration, provides no additional safety (CI is the gate)

**Status:** Decided — March 2026

**Superseded by: Decision 76**
