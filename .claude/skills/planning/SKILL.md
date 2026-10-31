---
name: planning
description: Deep methodology and rules for software planning, complexity assessment, and verification tier design. Use this when running the /plan workflow or when architecting new features.
model: opus[1m]
---

# Planning Methodology & Rules

You are using this skill to augment the `/plan` workflow. Apply these deep instructions when executing the workflow steps. You must NEVER initiate modifications to source code or global instructions (docs/PROJECT_CONTEXT.md, skills) during a planning session. SOLE EXCEPTION: roadmap-bookkeeping edits to `docs/ROADMAP-PLATFORM.yaml` proposed by the Tier Item Freshness Gate (status closeouts, criteria re-grounding) -- and only after explicit human confirmation; these reconcile roadmap DATA with reality, they implement nothing. The planning phase ends with the commitment of the PLAN artifact. Implementation only begins after an explicit /implement trigger with ANOTHER agent.

## Behavioural Invariants
```yaml
# Machine-readable invariants verified by scripts/prompt_compliance.py
preflight_run: true                # session_preflight.py must run at Step 1
harness_branch: true               # work on the harness-assigned session branch; do NOT create agent/ branches
decision_scout_gate: true          # @decision-scout must be invoked at Step 6 (Decision Scout) before sign-off
critique_gate: true                # @plan-critique must be invoked before completion
report_critique_gate: report-only  # REPORT-ONLY plans must run Step 10 multi-perspective deliverable critique
never_on_main: true                # no file edits while on main branch
```

## Preflight Constraints (Workflow Step 1)
When reading `logs/.preflight-report.json`, apply these conditionals:
- **`venv_ok: false`** -- Verify `bin/venv-python -c "import sys; print(sys.executable)"` resolves to the venv interpreter and rerun preflight. If still false, STOP.
- **`creds_status: "unavailable"`** -- **Static-key recovery (non-fatal, Decision 60):** the static-key assume-role chain has no interactive login. Verify it with `bin/venv-python -m scripts.aws_identity caller-arn --profile agent_platform`; if the `agent_static` key was rotated, update its network secret. Do NOT block (except the CI-RCA signal, next bullet) -- continue in degraded mode (credential-dependent verifiers are skipped, emitting SKIPPED). Autonomous executors never attempt recovery.
- **`recs_read_status != "ok"` or `ci_rca_unresolved_recs` null** -- HARD BLOCK at commitment (fail-closed, Decision 215): every recs-derived field is null (never `[]` or 0) when the reader was unreachable, so the CI-RCA hard block cannot be evaluated. This supersedes the `creds_status` "Do NOT block" bullet for the CI-RCA signal. Surface it; proceed only if the human explicitly overrides.
- **`log_sync_result.status == "committed"`** -- Print a one-line confirmation naming the file
  count committed to main. Continue.
- **`log_sync_result.status == "conflict"`** -- STOP. Print error and require human resolution.
- **`uncommitted_changes` non-empty** -- Ask human: resume, stash, or discard? Wait.
- **`main_freshness.status == "fetch_failed"`** -- Informational: surface the fetch error and note
  that Critique and Scope-overlap checks will use the stale local main ref. Continue.
- **`main_freshness.commits_behind > 20`** -- Non-blocking planning-context warning: N commits
  behind `origin/main`; Step 9 plan-critique reads PROJECT_CONTEXT/DECISIONS/ROADMAP-PLATFORM from
  the working tree, so a stale tree means a stale critique -- recommend rebasing, let the human decide.
- **`main_freshness.commits_behind > 0`** -- Retain `main_freshness.main_files_changed_since_branch` for the Step 4 Main Divergence Assessment. Non-blocking at this step.
- **`cron_review_fresh: false`** -- Note to human (non-blocking).
- **`open_recommendations > 0`** -- Surface counts and ask whether to address. Wait.
- **`non_automatable_recommendations > 0`** -- Informational. Surface counts; do not require per-rec discussion. Individual review is suspended per Decision 73 until CD.17 / T4.2 reverses (Decision 67's Lambda-deploy clause was lifted by Decision 79; the STRATEGIC clause survives).
  - If `non_automatable_softcap_breached` is true (count > 250), surface as a planning context note.
- **`friction_patterns`, `metrics_anomalies` non-empty** -- Surface as planning context.
- **`data_quality.last_run.verdict == "FAIL"`** -- Surface as planning context: N failures across
  named tables; run `bin/venv-python -m scripts.data_quality_runner` for details. Non-blocking but
  relevant if the plan touches data pipelines or table schemas.
- **`data_quality.last_run` is null** -- Note that DQ checks have never run; after fixing the
  pipeline, run the same command to establish a baseline. Non-blocking.
- **`data_quality.checks_defined == 0`** -- none defined; a plan touching data write paths should
  add YAML checks under `config/agent/data_quality/`. See scripts/data_quality_runner.py's module
  docstring.
- **`ci_rca_unresolved_recs` non-empty** -- **HARD BLOCK** at commitment time for overlapping work only (Decision 226): the Step 8 Related-Work Check always runs `scripts.ci_rca.red_overlap`, which blocks work that overlaps a red, and every plan once a red is past its repair SLO. (Legacy: if the report only has `ci_rca_recs` and no `ci_rca_unresolved_recs`, treat all entries as HARD BLOCK.) Full triage surfacing and the SOFT PROMPT / HARD ALERT classification is `/orient`'s responsibility -- run `/orient` for the full ci-rca visibility layer.
- **`ci_rca_likely_resolved_recs`, `ci_rca_liveness_alert`, `forward_fix_recursion_alert`** -- Full triage (SOFT PROMPT, HARD ALERT, forward-fix recursion) is surfaced by `/orient`. If still unresolved when `/plan` runs, apply the close or triage guidance from the orient skill.
- **`budget_bypass_alert` non-null** -- **Informational.** Surface the bypass count and recent
  reasons as planning context. Repeated `--ignore-budget` use indicates fast-tier drift and likely
  warrants revisiting the budget or identifying the slow check.

## Follow-on /plan mode (in_progress items with open criteria)

Conditional read-trigger: when intent targets an `in_progress` tier_item (open criteria remain),
read `docs/contracts/tier-item-lifecycle.yaml#follow_on_plan_mode` in full before scoping -- the
single authoritative definition of the trigger, the four-step protocol, and the
Status-Trusted-Never-Inferred constraint. Do not re-author it here.

### closes_criteria / followon_recs field format
Field format and write-time validation for both plan-linkage fields: see
docs/contracts/exit-criteria-ledger.yaml (plan_closes_criteria, plan_followon_recs). Only
followon_recs carries a template line below; closes_criteria stays prose-only by design.

## Platform Roadmap Eligibility (Workflow Step 2)

Broad orientation -- surfacing `next_eligible`, `strategic_pending`, the eligibility summary, the soft-warn exception category list, and ci-rca triage -- is the responsibility of `/orient`. Run `/orient` before `/plan` to choose what to work on; `/plan` assumes a specific item (or ci-rca rec) has already been selected. The orient skill (`.claude/skills/orient/SKILL.md`) holds the full eligibility display and triage rules.

Retained here: the per-item **Tier Item Freshness Gate** (below), which fires at commitment time once intent resolves to a specific tier_item id. `/orient` references that gate; it does not re-author it.

**Soft-warn exception categories** (used by the Tier Item Freshness Gate firing condition): `ci_rca`, `hotfix`, `security_advisory`, `ad_hoc_rec`, `user_explicit_out_of_scope`. When the human's stated intent matches one of these and names no tier_item, the Freshness Gate is skipped. Do NOT reject the session -- issue a soft warning and proceed. Reject only when the intent *claims* tier_item alignment but the referenced id does not exist or its depends_on are not satisfied.

## Clarification (Workflow Step 3)
Decompose the human's input into structured components:
| Component | Question to answer |
|-----------|-------------------|
| **Goal** | What outcome does the human actually want? (not just what they said) |
| **Constraints** | What explicit limits were stated? (time, scope, technology, cost) |
| **Acceptance criteria** | How will the human know when this is done? |
| **Affected areas** | Which files, modules, or infrastructure will be touched? |
| **Phase alignment** | Does this fall within the current roadmap phase? Does it depend on something not yet built? |

If the request is vague or missing key information, ask between 2 and 5 questions -- ranked by impact, no padding questions. Wait for answers before continuing.

## Tier Item Freshness Gate (Workflow Step 3, fires once intent resolves to tier_items)

MANDATORY read-trigger: fires AFTER intent maps to `tier_items[].id` values and BEFORE any Step 4
assessment or Step 8 Scope is written (skipped for a soft-warn exception category naming no
tier_item). Read `docs/contracts/tier-item-lifecycle.yaml#tier_item_freshness_gate` in full before
scoping ANY tier_item -- the single authoritative definition of the four checks and the output
discipline rule. Do not re-author it here.

## Suggest Aligned Recommendations
No local recs cache exists. Run `bin/venv-python -m scripts.recs_related <scope files>` (ranked hits with why and a completeness envelope; `--terms` for keyword search; bodies via `recs_by_ids`), plus `recs_by_title_prefix`:
1. Extract keywords and scope file paths from the task description
2. Match the returned open recs against the task's `title`, `file`, and `context`
3. Present top 3-5 matches (if any):
> "These open recommendations may align with your task:
> - **rec-XXX**: [title] (effort: [effort], priority: [priority])
>
> Want to bundle any into this session? Say 'include rec-XXX' or 'skip'."

## Recommendation Relevance Gate (Workflow Step 3, fires before bundling any rec)

Conditional read-trigger: before adding any open recommendation to the plan's
`bundled_recommendations` list, read
`docs/contracts/recommendation-relevance.yaml#planning_gate_protocol` in full -- it is the single
authoritative definition of the re-check protocol (the `recs_by_ids` fetch and `evaluate_rec_relevance` call), the verdict handling, and the `run_acceptance_probe=False`
constraint. Do not re-author it here.

## Documentation Artefact Design

When a plan creates or modifies documentation artefacts, apply these rules:

- Canonical field documentation pattern: ops.yaml extended contract. Add `description`
  and `semantics` metadata fields directly to the column entry in ops.yaml. These fields are
  ignored by the DQ runner and consumed by agents.
  Do not create a separate briefing doc for the same information.
- Decision 86 routing rule -- no new standing prose-architecture docs under docs/: route forward
  intent to tier_items, rationale to Decisions, field semantics to contracts. A new
  docs/INTENT-*.md or equivalent is forbidden.

## Infrastructure & Lambda Assessment (Workflow Step 4)
**Infrastructure:** If `.tf` files are in scope, add an "Infrastructure Dependencies table" to the plan. Lambda handlers must accept a `force_{param}` event field. Pre-merge vs post-deploy timing must be specified.

**Admin-only roots:** if scope touches a non-Markdown file under a root whose `environment-taxonomy.yaml` axis_a apply_gating is admin-only (today `terraform/bootstrap`), the plan MUST meet `docs/contracts/deploy-paths.yaml` `admin_out_of_band.pre_merge_live_plan`: a pre-merge read-only ADMIN plan step, the credential check, and the V3 resolution in `context:`.

**Speculative-plan pipeline (CD.35 Wave 2 / T2.21, active):** when `terraform/personal/` `.tf` files are in scope, name the required checks and point to `environment-taxonomy.yaml`, the sole SoT for the pipeline (PR-time saved plan re-applied at merge with no re-plan, Decision 77; IAM/trust/destroy diffs route to the human-gated path; a stale saved plan recovers only via acknowledge-and-retry). Do not re-derive the mechanics here.

**Lambda Deployment:** Use the manifest-derived file patterns (`bin/venv-python -m scripts.lambda_manifest --list-patterns`) to determine which scope files are Lambda-packaged, and `compute_affected_artifacts(changed_files)` to identify which active artifact(s) are affected. For each affected active artifact (status: active in its `src/lambdas/<slug>/manifest.yaml`), the plan MUST include per-Lambda build, deploy, smoke-test, and model ID validation steps (V3). Stub artifacts (status: stub) require no deploy step -- V1 suffices. Note: `config/agent/` is NOT Lambda-packaged and does NOT trigger this assessment. If `.tf` modifies IAM, terraform apply must precede Lambda deploy. (CD.16 + Decision 79)

**Deploy channel by artifact class (Decision 125):** `docs/contracts/build-lambda.yaml` `deploy_channels` names each affected artifact's channel (`environment-taxonomy.yaml` (conformance) is the classification SoT):
- **`terraform_personal_filemd5_coupled`** (the four `terraform/personal`-managed DuckLake Lambdas): emit the governed code-deploy CD channel VP steps, grep-only / CI-delegated per Decision 119 (name the CI check or workflow as verifier; no local `terraform init`/`plan`). Local `scripts.build_lambda --ducklake-only --deploy` is break-glass only, never the default VP step; if the governed channel does not exist for the artifact, file a follow-on rec instead.
- **`decoupled_build_pipeline`** (`data-pipeline` in `src/data/handlers/`): local `bin/venv-python -m scripts.build_lambda --deploy` stays the routine deploy step (not `terraform/personal`-managed, so Decision 125's gating does not apply).


## Complexity Assessment (Workflow Step 4)
- **Scope files > 5** OR **estimated steps > 8** suggests **STRATEGIC** (a heuristic). **Freeze override (active, AGENTS.md Temporary Operational Constraints):** STRATEGIC is suspended -- author one larger IMPLEMENTATION plan or split it into atomic IMPLEMENTATION plans now.
- If STRATEGIC (only valid when freeze is lifted), Work Areas must have precise lists, clear order, and concrete names.
- If IMPLEMENTATION (and complex), execution steps must have explicit pre/post-conditions.
- **Presentation Rule:** The classification MUST be presented to the human and confirmed, not assumed.
- **Size headroom (Decisions 128, 166):** `plan_obligations --plan` reports each scope file's headroom (SLOC, structural lines, prose bytes) and the unbuilt plans editing the same file. Plan a crossing as a decomposition (facade package, Decision 80/104/124) or the class relief valve, never a default raise: a raise is a Decision-cited exception (`# raise-approved: dec-NNN`), never a bare `--update-sloc-budgets` re-seed. A NEAR_CAP row carries a relief step measured against main at build time, never a fixed line count.


## Data-Model Assessment (Workflow Step 4)

MANDATORY read-trigger: conduct this assessment if a table (DDL/schema), a `field_semantics`
entry, or a warehouse write path is in scope. Read
`docs/contracts/data-modeling-standard.yaml#design_time_walk` in full before the plan commits to
a schema -- the single authoritative definition of the eight-step walk order, including
consulting `docs/contracts/_joins.yaml` for the join/correlation-key obligation, and the
append-only/SCD2 framing reminder. Do not re-author it here.


## Main Divergence Assessment (Workflow Step 4)

Conditional read-trigger: after Scope is identified, read
`docs/contracts/git-ops.yaml#rebase_phase_distinction.assessment_time` in full -- the single
authoritative definition of the detection rule, the surfacing text, and the deferral-recording
rule. Do not re-author it here. Do NOT auto-rebase at this step -- that happens only at
commit-flow time (the implement skill's Pre-Push Rebase step).

## Verification Tier Guidelines (Workflow Step 5)
Classify deterministically. Highest tier wins.
- **V1 (Static):** Docs, configs, markdown.
- **V2 (Unit):** Python source with no external integration. Must exercise real code paths.
- **V3 (Integration):** External systems, Terraform, Lambdas. Must tag steps as `[pre-deploy]` or `[post-deploy]`.

**Provider-init egress (terraform roots only):** CC-web cannot fetch third-party provider checksums.
Use grep and provider-free `terraform fmt -check` locally; delegate validate/plan to CI's required
`terraform-validate` check and the speculative-plan job as the authoritative verifiers. Never
author a local init/validate/plan VP command. See `terraform/CLAUDE.md`, Decision 119.

**VP Design Rationale:**
When writing Verification Plan steps, ask: "If this feature had a subtle bug (wrong column name, missing permission, off-by-one filter), would this step catch it?" If no, the step is too shallow.

**Assertion carrier (schema_version 5):** never quote a backtick literal inside a hermetic step's
`expected` free-prose field -- validate_vp_replay's replay-time check reads it, but nothing at
authoring time confirms the literal is emittable by the step's own `command`. See
`docs/contracts/vp-red-before.yaml#carrier_rule` for the full walk (MANDATORY read-trigger --
read this before writing any hermetic step's `expected`/`expected_literals`): declare
`verification_plan[].expected_literals` instead, which `scripts/roadmap/plan_document.py` guards
at schema-validation time.

**Test Obligation Assessment:** new IMPLEMENTATION plans are schema 4 with one `test_obligations`
row per behavior-capable scope file (shape below); waivers are per-source, never plan-wide.
Graduate rows reuse the linked VP step and existing registry slots; adequacy is plan-critique's
judgement, not schema inference.

**Anti-patterns to reject:** structural-only (`grep -q "def my_function" src/module.py` proves
existence, not function); test-only ("Run pytest" proves mocked paths, not real integration);
existence-only ("Confirm the table was created" doesn't confirm correct data); import-only
("Confirm `import module` succeeds" isn't verification); terraform-only ("Confirm `terraform
apply` succeeded" -- infra existing isn't enough); prose-only (no executable command -- the
implement agent will substitute a weaker check).

## Hermetic authoring (T3.15 / VF-01, amended by Decision 148/plan-resolution-content-keyed)

See `docs/contracts/vp-red-before.yaml#hermetic_authoring` for the full walk (MANDATORY
read-trigger -- read this before setting `hermetic:` on any pre-deploy step): the two replay
polarities a pre-deploy step is now subject to, `bin/venv-python`'s venv-less-CI safety, and the
scripts.validate recursion hazard. Also see `docs/contracts/vp-red-before.yaml#replay_bound` for
where the step's replay cost is bound.

## Graduation disposition authoring (T3.21, enforced VF-05)

See `docs/contracts/vp-red-before.yaml#graduation_disposition_authoring` for the full
three-way classification rubric (MANDATORY read-trigger -- read this before leaving any
pre-deploy step's `graduation` field unset): `graduate` / `waive` / `not-applicable`, their
required companion fields, and which disposition to prefer when unsure.

## Decision Significance Gate (before drafting any numbered Decision -- fresh or CD ratification)

Check `docs/contracts/decision-entry.yaml`'s `significance:` section before drafting ANY numbered
`## Decision NNN:` text (a fresh governance Decision, or a CD ratification below): only a durable
architectural commitment with reversal-relevant consequences clears the bar. A CD state-flip,
operational fact, or field-semantics change routes per that section's routing rows instead (the
batch-wave clause below, a rec/tier_item note, or a contract governance note).

Three property tests gate the draft -- checkable assertions on the drafted text, never an
open-ended "consider whether" prompt:
1. **Envelope marker cited.** The drafted entry's fenced ```yaml metadata envelope carries a
   `significance` field naming one of the routing keys plus a non-empty justification
   (`decision-entry.yaml`'s `metadata_envelope.required_fields`, Decision 167 clause 2) -- a
   Significance claim made only in narrative prose, with no envelope field, fails this test.
2. **Contract-first justification (rec-3015).** The envelope justification names, in one clause,
   why not a contract: the specific `docs/contracts/*.yaml` governance-note home (or in-place
   amendment) considered for this content and the concrete reason it was rejected in favor of a
   fresh number. A justification that never names a rejected alternative fails this test.
3. **amendment_forms routing alternative (rec-3016).** Before minting a number to amend exactly
   one prior entry at a single call site, check whether `decision-entry.yaml`'s `amendment_forms`
   (a dated in-place annotation on the amended entry) already carries the same commitment at zero
   header cost -- draft the amendment_forms shape instead unless the content independently clears
   the significance bar on its own terms.

## Candidate Decision Ratification (Workflow Step 5b, when the plan realizes/ratifies a CD)

MANDATORY read-trigger: fires when the plan's scope realizes the work a pending
`candidate_decision` (CD.NN) gates, OR the CD was surfaced by `/orient`'s "Ratifiable CDs"
subsection. Read `docs/contracts/candidate-decision-ratification.yaml#lane_steps.plan.draft` in
full before drafting -- the single authoritative definition of the draft protocol and the
numbering-race note. Do not re-author it here. Ratification is a first-class lane shared across
`/orient` (surface), `/plan` (this step), and `/implement` (execute).

## Decision Scout Gate (Workflow Step 6, project alignment recon)

This gate fires BEFORE the alignment sign-off presentation to the human. Its job is to surface any active decisions the proposed approach must cite, contradict, or pivot around -- without paying the cost of loading the full DECISIONS.md (large -- near its Decision 134 size ceiling) into the planning agent.

**Example prompt body:**
> "You are running the decision-scout gate in a fresh context window. Invoke the `decision-scout` skill via the Skill tool. The skill needs the following inputs (use them in your scout analysis):
> - Intent: [1-2 sentences from clarification]
> - Proposed approach: [paragraph synthesis]
> - Scope files: [list from Step 4]
> - Verification Tier: [V1 | V2 | V3]
> - Explicitly cited decisions: [list of IDs the human mentioned, or 'none']
>
> Return the skill's `## Decision Scout Report` output verbatim, including the final `Verdict:` line. Do not edit any files."

**Subagent-dispatch mode:** if you are a subagent (`SUBAGENT CONTEXT` header, or no `Agent` tool, or
a nesting error), do not dispatch inline -- gate-request (status `GATE_REQUEST`, `gate:
decision-scout`) and resume via `SendMessage` on verdict. See the overseer skill's
subagent-dispatch division of labor; schema: `docs/contracts/overseer-dispatch.yaml#gate_request_trampoline`.

**Dispatch shape (non-subagent runs):**
- `subagent_type: "general-purpose"` (needs `Skill`, `Read` access)
- `description: "Decision scout gate"`
- `prompt:` self-contained per the Example body above -- Intent: Step 3; approach: Steps 3-5;
  scope: Step 4; tier: Step 5; cited decisions: the human.

**Verdict handling** (add a `gate_rounds` row per scout round):
- **NO_FLAGS** -> Proceed to the alignment sign-off. Include the scout's CITE list in the presentation as "Decisions this plan must reference." Record in the plan template's `context:` list: "Decision-scout verdict + CITE list (verbatim decision ids)".
- **FLAGS_FOUND** -> Surface each WARN/NOTE flag to the human in the sign-off presentation under a "Decision Flags" section. Human chooses per-flag: pivot, defer with note, or accept-as-is. If the human pivots on any flag in a way that changes the proposed approach materially, re-dispatch this gate against the revised approach before re-presenting.
- **BLOCK** -> STOP. Do NOT present the original approach for confirmation. Surface the BLOCK contradiction and propose pivots, then re-dispatch the gate against the revised approach. Confirming a known-blocking approach would invite the human to ratify a plan that contradicts an active decision.
- **Gate-error:** if the gate subagent errors or returns output missing the required `Verdict:` line, the gate has NOT completed -- re-dispatch; never proceed past an incomplete gate. Likewise, if the report's "Decisions triaged: N of M" line is missing, or N != M, treat the gate as incomplete and re-dispatch -- a mismatch means the scout truncated its read of DECISIONS.md.

**Why a subagent and not inline grep, and the Lambda migration contract:** see the `decision-scout` skill -- it owns the isolation rationale and the stable-interface contract for when DECISIONS.md is replaced by a Lambda-backed tool query.

**Convergence rule -- findings, not rounds:** re-dispatch after each pivot; escalate at once when a pivot is re-BLOCKed on the same decision id, naming whether the intent contradicts it (re-scope) or it needs revisiting (file a rec; record accept-with-deferral). Carrier (AskUserQuestion, chat or GATE_REQUEST) and stop as in Step 9.

## Alignment Sign-off (Workflow Step 6, project alignment)
Wait for explicit 'write the plan'; anything else is feedback -- incorporate it, re-run the Decision Scout if material to decision alignment, re-present, ask again.
IT IS **CRITICAL** THAT YOU DO NOT PROCEED UNTIL THE HUMAN CONFIRMS THE PLAN.
Class each design fork: consistency-only is decided-with-notice, else ask in the pinned shape --
`docs/contracts/overseer-dispatch.yaml#autonomy_tiers.plan_fork_classification`.
**If you are a subagent**: use `AskUserQuestion` if you hold it; an overseer-dispatched author does
not -- forks and confirmation ride a post-verdict `GATE_REQUEST` (`gate: project-alignment`) the
overseer relays.

## Create Branch (Workflow Step 7)
See `docs/contracts/git-ops.yaml` as the canonical git-ops authority for branching topology (DEV vs ADMIN containers, AWS profiles, harness branch vs never agent/).

Verify you are on the harness branch and not on `main`:
```bash
git branch --show-current
```
If the result is `main`, STOP.

Derive the plan slug from the task, not the branch. Author only `docs/plans/PLAN-{slug}.yaml`; legacy `.md` plans are deprecated. Merge the approved plan before implementation.

## PLAN-{slug}.yaml Template (Workflow Step 8)
The plan is a YAML document validated against the `PlanDocument` Pydantic schema (`scripts/roadmap/plan_document.py`, enforced by `validate.py` in both tiers). Unknown keys FAIL validation (`extra="forbid"`). Use exactly this structure -- comments document field semantics:
```yaml
schema_version: 5 # required on every NEW plan; historical versions 1-4 stay valid
handoff_policy:
  full_validation_required_before_commit: true # exact literal; commit/PR waits for completed full exit 0
  timeout_disposition: blocked # exact literal; resume later and rerun full from the start
slug: "{slug}" # must match the filename PLAN-{slug}.yaml
intent: >- # 1-2 sentences: how this work contributes toward the North Star
  ...
plan_type: IMPLEMENTATION # IMPLEMENTATION | STRATEGIC | REPORT-ONLY
verification_tier: V2 # V1 | V2 | V3
plan_path: docs/plans/PLAN-{slug}.yaml # must match the filename slug exactly
phase: >- # platform tier_item id from docs/ROADMAP-PLATFORM.yaml
  ...
scope: # min 1 entry; only files listed here may be modified
  - file: path/to/file.py
    action: Create # Create | Modify | Delete
    purpose: why this file changes
    new_imports: [] # optional: first-party modules this file newly imports (Step 8b pre_globs lint)
bundled_recommendations: [] # included open recs (list of str), or []
followon_recs: [] # deferred halves as follow-on rec ids, or []
infrastructure_dependencies: [] # list of str; populate when .tf files appear in scope
acceptance_criteria: # min 1; each independently verifiable
  - verifiable condition 1
verification_plan: # min 1 step; step ids must be unique
  - step: 1
    phase: pre-deploy # pre-deploy | post-deploy
    action: exercise the feature
    command: executable shell command # REQUIRED non-empty -- prose-only VP steps fail the schema
    expected: specific expected result # v5 hermetic steps: NEVER a backtick literal here -- see below
    fix_if: what failure looks like
    hermetic: true # a hermetic step's expected assertions are programmatically checked
    expected_literals: [] # v5 ONLY, hermetic steps: the sole assertion carrier (empty/absent both fine)
test_obligations: # v4 IMPLEMENTATION: one row per behavior-capable scope file
  - source: path/to/file.py # must equal a scope[].file
    behavior: what must stay guarded
    verification_step: 1 # an existing step id that runs the evidence
    test_selector: tests/test_x.py::test_y # exactly one of test_selector | command
    red_green_expectation: red before, green after # or waiver_reason (>=20 chars)
constraints:
  - limits from PROJECT_CONTEXT.md, DECISIONS.md
  - No rescue agents or workaround loops (Decision 55)
context: # <= 40 rendered lines; link evidence (ids/paths), never restate it
  - Relevant decisions, phase dependencies, known gotchas
  - "Decision-scout verdict + CITE list (verbatim decision ids)" # REQUIRED ITEM (WF-04a)
gate_rounds: # REQUIRED on new plans; one row per gate round (+gate_run_id if dispatched)
  - {gate: decision-scout, round: 1, verdict: NO_FLAGS} # critique rows add blocking, nits; escalated: true + escalation_choice when asked
pre_implementation_checklist:
  - Branch confirmed not on main
  - docs/PROJECT_CONTEXT.md read
  - only the plan-cited decision sections
  - All files in scope located and readable
  - Acceptance criteria understood and verifiable
execution_steps: # REQUIRED non-empty for IMPLEMENTATION plans
  - Specific file to create/modify -- what it must do
  - Execute Verification Plan -- run each step; loop until pass; on unrecoverable V3 failure stop and RCA (Decision 55)
  - 'Report: what was implemented, verification results'
work_areas: [] # STRATEGIC plans only (required there, forbidden otherwise);
  # entry shape: {area, scope, rationale, complexity: XS|S|M|L|XL}
rollback: optional rollback note # optional str; omit if not applicable
# fallback_reevaluation: OPTIONAL, only if this plan names a CD.27-gated tier item
# (validate_fallback_reevaluation); never on an ordinary plan. 4 fields, extra=forbid:
# {reevaluated_on, substrate_status, verdict: continue_on_current_substrate|fallback_triggered|obligation_lapsed, basis}
```

After writing, validate before committing:
```bash
bin/venv-python -m scripts.roadmap.plan_document docs/plans/PLAN-{slug}.yaml
```

**Platform compatibility:** commands must be Linux/bash-compatible (AGENTS.md `## Shell invocations`).

## Related-Work Check (Workflow Step 8)

When writing the PLAN file, always run `bin/venv-python -m scripts.ci_rca.red_overlap --plan docs/plans/PLAN-{slug}.yaml` (Decision 226; rules in `docs/contracts/red-main-recovery.yaml`). Exit 0: proceed and record its verdict line in Context. Exit 2 (unavailable): HARD BLOCK; proceed only if the human explicitly overrides (Decision 215). Exit 1: a plan that repairs the red bundles it (red_overlap then clears it); otherwise the plan must log a deferral rationale naming the blocking red in its Context section, or write is refused.

## Closure Obligation (Workflow Step 8, CONDITIONAL)

Apply this check when writing the PLAN file. It is CONDITIONAL -- additive plans that neither resolve recs nor retire a surface are exempt.

**Trigger conditions (either/both):**
1. **Rec-resolving plan**: the plan's scope or intent explicitly fixes one or more open recommendations. The plan MUST declare them in `bundled_recommendations` AND include a VP step that verifies each rec closed (e.g. `recs_by_ids` shows status closed).
2. **Surface-retiring plan**: the plan's scope includes a `Delete` row OR an explicit `X -> Y` migration/cutover (file, Lambda, write path, config flag, or backend). The plan MUST include a stale-reference sweep VP step that confirms the old surface is unreachable or deleted. For a deleted writer (a function, CLI mode or flag that records state), the plan lists every caller as removed, rewired or fail-loud; a stub keeping the old name and success output fails.

**Enforcement:** a plan that trips a trigger but omits its closure obligation fails plan-critique 12k. Surface it during Step 6 presentation; the missing declaration or VP step must be added before writing.

## Plan Lint and Wording Check (Workflow Step 8b, before critique)
Run `bin/venv-python -m scripts.roadmap.plan_obligations --plan <plan>` on the written plan: fold every gating finding (a derived companion) into scope, and act on each advisory one -- a NEAR_CAP row gets a relief step, an unbuilt plan editing the same file is named in `context:`, an undecidable bundled-rec acceptance is fixed or the rec dropped. Before asking approval of always-ask wording, write it as the approved-text sidecar, place it in an uncommitted scratch worktree of origin/main, and run `bin/venv-python -m scripts.roadmap.plan_wording_check --slug <slug> --tree <worktree>`: it checks the tree holds exactly the sidecar's edits (`approved_text.verify`), then runs `validate --pre` there, whose selector runs every test and registered check that reads a changed file. Revise until PASS; record each run as a `gate: wording-check` row with the digest, base and selection status it printed. Each live-infrastructure assumption (grants, cadence, concurrency, memory, provider values) carries a read-only probe result in `context:` or the word UNPROVEN. The handoff names this planning session as the plan's owner.

**Amendment mode** (a plan-defect report, implement-scope-boundary.yaml `deviation_trigger.escalation`): fix the plan in a plan-text-only PR (`plan({slug}): scope amendment -- <reason>`), re-run this step and a fresh critique, merge as any plan PR merges, and report the merge SHA to the coordinator. An approved_text or contract defect, or any loosened assertion, goes to the human first; nothing is loosened to obtain green (Decisions 181, 235).

Scope rows name hand-edited files only: generator-touched files are sanction_rows-covered (a graduate step's graduated_registry_shard, docs/decisions-index.json, .secrets.baseline) and need no row. A VP touched-set step derives its allowed set from sanction_rows through validate_scope_boundary's own seam, never a restated list.

## Critique Gate (Workflow Step 9)
**DO NOT output the completion message until this step completes.**

**Subagent-dispatch mode:** same three-signal check as the Decision Scout Gate above -- if
triggered, do not invoke `plan-critique` inline (same-context bias); return `GATE_REQUEST`
(`gate: plan-critique`) and resume via `SendMessage` on verdict.

Launch a zero-context Claude subagent via the `Agent` tool to run the `plan-critique` skill in a fresh context window. The fresh-context requirement is non-negotiable: it eliminates the cognitive bias the planning agent has from authoring the artefact. Do NOT invoke the `plan-critique` skill in the current session via the `Skill` tool (same context = same bias). Do not use `scripts.agent_development.run_skill` -- it is broken per rec-568; dispatch the gate via the `Agent` tool + `Skill` instead.

**Invocation shape:**
- `subagent_type: "general-purpose"` (needs `Skill`, `Read`, and `Grep` access)
- `description: "Plan critique gate"`
- `prompt:` self-contained per the Example body below; also state a `.md` plan path is
  deprecated -- warn and proceed.

**Example prompt body:**
> "You are running the plan-critique gate in a fresh context window. **First, run `git fetch origin main --quiet`** so the local `origin/main` ref is current. Then invoke the `plan-critique` skill via the Skill tool to critique `/abs/path/to/docs/plans/PLAN-{slug}.yaml`. Read `docs/PROJECT_CONTEXT.md` in full; extract only the roadmap items and decision sections the plan names per the skill's Phase 1 (do not load full ROADMAP-PLATFORM.yaml/DECISIONS.md). For IMPLEMENTATION plans, also read every file in the plan's Scope table. If `git diff origin/main -- docs/DECISIONS.md docs/ROADMAP-PLATFORM.yaml` shows divergence, note that the critique evaluates against the branch's (possibly stale) view of these docs. Return the skill's structured critique output verbatim, including the final `Recommendation:` verdict line. Do not edit any files."

Read the critique output returned by the subagent.
If it suggests revisions, update the plan with these fixes and re-launch the same subagent invocation against the revised plan. Each Agent call is a fresh window, so the re-launch genuinely re-evaluates.
Loop per the Convergence rule below.
If the gate subagent errors or returns output missing the required `Recommendation:` line, the gate has NOT completed -- re-dispatch; never proceed past an incomplete gate.

**Convergence rule -- findings, not rounds:**

Every round is a full fresh read and no count caps them. The critic tags each finding blocking or nit (plan-critique's Finding-Origin Attribution); REVISE means at least one blocking finding. Fix every finding, record one disposition each (fixed, or disputed with evidence -- re-measure a figure before applying it), and run `bin/venv-python -m scripts.validate --pre` before EVERY re-dispatch (a pre-check; Decision 73 makes `--pre` authoritative only via PR CI). REVISE -> re-dispatch. Clean PROCEED -> done. PROCEED listing nits -> apply them and dispatch ONE fresh confirming round, no card; it ends the loop unless it returns a blocking finding (its own nits are applied, not re-read). Write each round to `gate_rounds`.

**Escalate** only on: a finding recurring at a tag+anchor you recorded fixed (oscillation interrupts, not the count); a disputed blocking finding; or a fix that would change approved scope, tier or a cited Decision. Present, per finding, the finding verbatim with tags and anchor; why it did not converge; your recommendation; and, when re-escalating, a clause that starts a new series naming the predecessor rounds -- plus the five choices: accept-with-deferral / re-scope / split / abandon / one more round. Carrier: AskUserQuestion if held, else chat; dispatched, GATE_REQUEST `open_questions`. An open escalation is a stop: no round, no plan edit, until the human answers; record the answer as the row's `escalation_choice` (accept-with-deferral is human-authored only). Revisit when 20 plans carry `gate_rounds`: did a confirming round ever find a blocking finding, and were findings after round 5 real?

**Contested-residue consult:** On a disputed blocking finding only, consult `model: "fable"` per the overseer skill's Fable Advice-Consult Protocol (dispatched: an `open_questions` item of the same GATE_REQUEST) -- a third fresh reading of THIS plan against the repo's own Decisions, not industry practice. It recommends a disposition and decides nothing. Revisit after 10 contested escalations; demote if it changed nothing in 8 or more.

This gate reviews the PLAN artefact, not the report deliverable. For REPORT-ONLY plans, the deliverable gets its own critique in Step 10.

## Report Critique Gate (Workflow Step 10, REPORT-ONLY only)

**Applies only when Plan Type is REPORT-ONLY.** For IMPLEMENTATION and STRATEGIC plans, Step 10 is a no-op; skip to Step 11.

**Why this gate exists:** Step 9 critiques the PLAN artefact only; a REPORT-ONLY plan's substantive deliverable (e.g. `docs/REPORT-{slug}.md`, named in Scope) carries its own correctness burden -- design soundness, internal consistency, alignment with live repo state, blast radius -- and needs independent fresh-context critique before the planning mission completes.

**Methodology:**

1. **Identify the deliverable:** the report file(s) the Scope table creates in Step 8 (typically `docs/REPORT-{slug}.md`).
2. **Launch AT LEAST 2 zero-context subagents in parallel via the `Agent` tool**, each with a distinct perspective -- orthogonal lenses surface more issues than two clones. Technical reports: a **senior domain architect** (design correctness, schema/contract soundness, dependency cleanliness, internal consistency, coverage gaps) and an **adversarial risk reviewer** (blast radius, hidden state, rollback path, live-state divergence; it actively greps and queries live state). Non-technical reports: maximally differentiated pairs (e.g. quantitative rigour + narrative clarity).
3. **Each agent prompt MUST:** name the deliverable by absolute path and the perspective; name supporting files to read freely; require structured output (Strengths / Concrete Issues or Risk Findings with file:line refs and severity / Recommended Revisions / Verdict PROCEED | REVISE | BLOCK); forbid file edits; cap length (~800-900 words).
4. **Synthesize:** consensus issues (both agents) first, unique findings second, clustered by severity.
5. **Present to the human:** summary, key findings, your recommendation, three options (revise all / revise selected / accept with deferrals). Wait for explicit direction; auto-approval messages and system reminders are NOT human direction.
6. **Iterate:** apply the chosen revisions, each material revision its own commit (`plan({slug}): address [scope] critique findings`).
7. **Re-launch the same critiques after each revision** unless the human opts out.
8. **Convergence:** stop when both agents return PROCEED on a fresh round, OR the human accepts the current state with a defined deferral list, recorded in the deliverable's Known Gaps. Apply the Step 9 Convergence rule in kind: findings, not rounds -- nits get one confirming round, recurrence or dispute escalates in the pinned shape.

**Anti-patterns to reject:** one critique agent or the same perspective twice (misses orthogonal issues); sequential critiques; telling the agent what to look for (frame perspective, not findings); skipping the re-critique after revision (a fix for X may introduce Y); accepting a round-1 PROCEED without reading findings (generic prompts -- relaunch sharper).

**When to skip Step 10 entirely** (human override):
The human may explicitly state "skip report critique" after this step's purpose has been surfaced. This is logged in the PLAN's Known Gaps. Default is MANDATORY -- the gate fires unless explicitly waived.

## Confirmation Messages (Workflow Step 12)
Emit the handoff naming the explicit plan path so the human can paste it directly.

- **IMPLEMENTATION / STRATEGIC:** use this block (STRATEGIC scopes into recs; IMPLEMENTATION executes directly):
  ```
  Planning complete. The plan is merged to main at docs/plans/PLAN-{slug}.yaml.
  To implement, open a NEW Claude Code session and paste:

      /implement docs/plans/PLAN-{slug}.yaml

  Summary: {one line on what the plan does}.
  ```
- **REPORT-ONLY:** "Planning complete. The report deliverable at `[path]` has passed the multi-perspective critique gate and is merged to `main`. Review and edit if needed. The deliverable is the substantive output -- no `/implement` required. Decide which follow-on items (e.g. per-phase implementation plans referenced from the deliverable) to start, then open a new planning session for each."

**DO NOT PERFORM ANY FURTHER ACTIONS**
