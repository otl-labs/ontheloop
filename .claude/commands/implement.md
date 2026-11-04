---
description: Implements IMPLEMENTATION plans directly or scopes STRATEGIC plans into atomic recommendations for the executor. Run after /plan.
argument-hint: [docs/plans/PLAN-slug.yaml]
---

# Implement Workflow

**Intent**: For IMPLEMENTATION plans: execute the Ordered Execution Steps directly. For STRATEGIC plans: research Work Areas and produce atomic, automatable recommendations for the executor.

*Note: For detailed guidelines on how to execute each step below, invoke your `implement` skill via the Skill tool. For the canonical git-ops procedure (branching, rebase rules, PR/CI/merge flow), see `docs/contracts/git-ops.yaml`.*

## Step 1: Run Preflight
```bash
bin/venv-python -m scripts.session.preflight
```
stdout is a one-line summary; Read logs/.preflight-report.json for the full constraint surface.

Preflight runs `git fetch origin main` and emits `main_freshness` (status, commits_behind, commits_ahead, main_files_changed_since_branch). The report is slim: roadmap state carries only `next_eligible` + `strategic_pending`; `non_automatable_details` is dropped (Decision 73). Handle constraints (including the `main_freshness` cases) as defined in the **Preflight Constraints** section of your `implement` skill. After Step 2 loads the plan, apply the **Main Divergence Check** from the skill before proceeding to Step 3.

After preflight completes successfully, run the pre-implementation gate (fast `--pre` tier):
```bash
bin/venv-python -m scripts.validate --pre
```
On non-zero exit: parse each failed check from the "Failed checks:" output, file each as a recommendation via `bin/venv-python -m scripts.ops_data_portal --file-rec ...` with `automatable: false`, surface a go/no-go to the human, and STOP if no-go. SSO-related failures: skip with actionable guidance per Decision 57; do not crash. Pattern reference: `ensure_fresh_dq_results()` in `scripts/validate.py`.

## Step 2: Load PLAN File
```bash
git branch --show-current
```
If the result is `main`, STOP.

The plan path is provided as `$ARGUMENTS` from the `/plan` handoff (e.g. `docs/plans/PLAN-web-workflow-migration.yaml`). If an argument was given, resolve it:
```bash
bin/venv-python scripts/roadmap/find_plan.py <path-from-arguments>
```
If no argument was given, fall back to auto-discovery:
```bash
bin/venv-python scripts/roadmap/find_plan.py
```
If either command prints `NOT_FOUND`, list `docs/plans/PLAN-*.yaml` and ask the human which plan to implement.

Read the entire plan file. Extract Intent, Plan Type, Verification Tier, Work Areas (STRATEGIC) or Execution Steps (IMPLEMENTATION), Verification Plan, and Constraints.
**If no plan file exists, STOP.**
Also read `docs/PROJECT_CONTEXT.md`. Read only the decisions cited in the plan context (locate each via `rg "^## Decision N:" docs/DECISIONS.md`); do not load the full file.

## Step 3: Dispatch by Plan Type

### For IMPLEMENTATION Plans
1. Count Scope files and estimated steps.
2. Present summary of scope to the human, and explicitly state you are executing autonomously without breaking into recs. Include a fun titbit to confirm understanding.
3. Execute each Ordered Execution Step sequentially.
4. Proceed to Step 4.

### For STRATEGIC Plans
1. Research each Work Area by reading files/modules.
2. Identify dependencies.
3. Break into atomic recs (effort `<= M`).
4. Write full context (executor should not need to read the plan).
5. Define acceptance command and verification steps.
6. Apply the **Quality Gate Validation**, **Dedup Gate**, and **JIT Context Injection** from your `implement` skill.
7. File approved recs using `bin/venv-python -m scripts.ops_data_portal --file-rec ...`. For recs `> M` effort, create `docs/plans/briefings/BRIEFING-rec-NNN.md`.
8. Skip Steps 4-5 and proceed directly to Step 6.

## Step 4: Verification Plan (IMPLEMENTATION only -- MANDATORY)
**You MUST execute this step. Do not skip it.**
Execute the Verification Plan from the PLAN-{slug}.yaml file. Apply the strict **Live Verification Protocol** from your `implement` skill.
Produce the VP Compliance Table. If ANY row is FAIL, fix and re-verify. If BLOCKED, wait for human.

## Step 5: Code Review (IMPLEMENTATION only -- MANDATORY)
**You MUST trigger the code-review immediately after verification passes. Do not wait for the human to ask.** Dispatch per the **Code Review Protocol** in your `implement` skill (fresh-context anti-bias gate; `subagent_type: "general-purpose"` invoking the `code-review` skill via the `Skill` tool).

Read the findings output. You MUST implement fixes for all **Critical** and **High** priority findings before proceeding.
Medium and Low findings should be filed as recommendations using `bin/venv-python -m scripts.ops_data_portal --file-rec ...`.

## Step 6: Bookkeeping Re-check (fast tier)
Step 5's tier_item bookkeeping, verification-graduation, and CD-ratification walks write tracked
files (`docs/ROADMAP-PLATFORM.yaml`, `config/agent/verification_registry/entries/*.yaml`,
`docs/DECISIONS.md`, the plan YAML). If any of them wrote, re-check those writes with the
diff-aware fast tier -- they are in the diff, so their guards (`validate_plan_documents`,
`validate_plan_scope_closure`, `validate_candidate_decision_ratification`,
`validate_verification_registry`, `validate_prose_limits`) are selected:
```bash
bin/venv-python -m scripts.validate --pre
```
Must exit 0 before continuing. If the walks wrote nothing tracked and the post-code-review `--pre`
in Step 5 already passed, this step is a no-op -- proceed.

**One-full-tier rule (do not reintroduce a second full run).** The full `scripts.validate` tier
runs exactly ONCE per implementation session: Step 7's closure item 4, after
`implementation_declared: true` and after every Step 5 write, on the COMMITTED, rebased, clean
tree, followed by `--verify-head`. That run is the only one whose scope is the whole tree rather
than this diff, and it is the last one before the push, so an earlier full-tier pass is superseded
by it, never additive. (The declaration-armed checks -- `validate_vp_replay`,
`validate_graduation_completeness` -- ride the `--pre` half of that same closure step, not the full
half.) Any tracked write landing after that PASS voids it -- commit it and rerun the full tier from
the start, which is exactly what `--verify-head`'s committed-tree attestation checks. Accepted
trade-off: cost basis is now per push (every rebase or fix commit needs a fresh full-tier run), not
merely once per session.

## Step 7: Commit, PR, and Merge
**You MUST execute the commit flow autonomously once Step 6 passes. Do not stop to ask for permission.**

### Deferred half (follow-on split)
This applies only if this session deferred a half of the plan's work; if nothing was deferred it
is a no-op -- proceed to the Commit Flow. Otherwise, before the implement skill's closure item 3
(the one that sets `implementation_declared` true): run `bin/venv-python -m
scripts.ops_data_portal --guidance` first (Decision 66), then `--file-rec` naming `--title` (the
/plan prompt for the half), `--file`, `--context` (opening "Deferred half of PLAN-{slug}: <what
must land first>"), `--acceptance` (a discriminating shell oracle for the deferred half --
`lint_acceptance_command(require_discrimination=True)` rejects a thin probe at write time),
`--effort`, `--priority`, `--risk`, `--source manual` and `--tags follow-on`; then append the
returned id to this plan's `followon_recs` (a permitted bookkeeping edit per
`docs/contracts/implement-scope-boundary.yaml`) and continue with closure items 3-4. If the portal
is unreachable, the write FAILS LOUDLY (Decision 84 I-4, no offline outbox) -- restore
connectivity and re-file before declaring; never skip this step silently and never compose the id
client-side.

Apply the appropriate **Commit Flow** (STRATEGIC or IMPLEMENTATION) defined in your `implement` skill. All GitHub operations use the GitHub MCP tools (`mcp__github__*`) -- the `gh` CLI is not available on the web harness. Wait for CI event-driven via `subscribe_pr_activity`; never busy-wait with a sleep timer or a recurring scheduled re-check.

When the plan's `bundled_recommendations` list is non-empty, the implementation commit and the PR body carry a `Resolves: rec-NNNN[, rec-MMMM]` trailer, and the merge follows the implement skill's Commit Flow step 4 (premerge, squash-merge with an explicit `commit_message` and `expectedHeadSha`, readback). After the merge, execute the **post-merge closeout fallback** from the implement skill: wait for `ci.yml`'s trailer-closure job to conclude on the push run at the merge sha, then act on its per-rec log evidence -- never on elapsed wall-clock time. A rec the job's log names in a Decision 186 or Decision 201 refusal marker is never closed by a direct `--update-rec ... --status closed` call: after a Decision 201 refusal it takes the route in `docs/contracts/git-ops.yaml#resolves_trailer`, and after a Decision 186 refusal it stays open and is reported to the operator (rec-4088). A rec the log names as closed, not-found, or errored may use the direct fallback command; anything else (no log line for that rec, an unconcluded or failed job) is left alone until the job concludes.

## Step 8: Capture Friction
Record friction (parsing errors, ambiguous areas, bugs found) by filing a recommendation via `bin/venv-python -m scripts.ops_data_portal --file-rec ...` with `source=manual` (the Single Portal Invariant, Decision 84). If no friction, this step is a no-op.

**RCA-First Protocol (Decision 55):**
If the friction was a recurring gap or unrecoverable failure, you MUST invoke the `executor-rca` skill via the `Skill` tool to diagnose the root cause and file a permanent fix. Do NOT silently workaround structural issues.

Friction is filed to the warehouse through `scripts.ops_data_portal` (Decision 84) -- nothing
under `logs/` is committed.

## Step 9: Report
Output the final report:
- **STRATEGIC**: Total recs filed, breakdowns, briefing files created, and next step instructions.
- **IMPLEMENTATION**: Files changed, verification results (actual outcomes per step), code review findings fixed, bugs fixed, design decisions, and any follow-on rec ids filed for a deferred half.
