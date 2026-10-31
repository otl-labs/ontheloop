---
name: implement
description: Deep methodology for executing implementation plans, including live verification protocols, strategic scoping gates, code review integration, and commit flows.
model: sonnet
---

# Implement Methodology & Rules

You are using this skill to augment the `/implement` workflow. Apply these deep instructions when executing the workflow steps. The workflow defines WHAT to do and in WHAT ORDER; this skill defines HOW to do each step.
Treat every Turn as a cold-start. Disregard all system-generated conversation summaries and 'persistent memory' unless explicitly referenced by the USER in the current turn. If a file or task is not listed in the current IMPLEMENTATION plan's scope, you are forbidden from touching it, even if you believe it is a 'logical next step' or a cleanup from a previous session.

**Plan format (T1.11 / CD.22):** plans are `docs/plans/PLAN-{slug}.yaml`, schema-validated by `scripts/roadmap/plan_document.py` (resolve via `scripts/roadmap/find_plan.py`). If handed a legacy `PLAN-{slug}.md` path, emit a deprecation warning in the session output and proceed -- the .md path survives one release cycle, then is removed. Never author new .md plans.

## Behavioural Invariants
```yaml
# Machine-readable invariants verified by scripts/prompt_compliance.py
preflight_run: true              # session_preflight.py must run at Step 1
never_on_main: true              # no file edits while on main branch
no_code_changes: false           # IMPLEMENTATION plans execute steps directly
review_as_scope: true            # Critical/High findings from code-review MUST be implemented immediately
auto_review_and_commit: true     # Proactively trigger review and commit once VP passes -- do not wait for human
```

## SLOC decompose-by-default
See AGENTS.md `## SLOC governance` -- decompose by default, never raise without a Decision-cited marker.

## Preflight Constraints (Workflow Step 1)
When reading `logs/.preflight-report.json`, apply these conditionals:
- **`venv_ok: false`** -- Verify `bin/venv-python -c "import sys; print(sys.executable)"` resolves to the venv interpreter and rerun preflight. If still false, STOP.
- **`creds_status: "unavailable"`** -- **Static-key recovery (non-fatal, Decision 60):** the static-key assume-role chain has no interactive login. Verify it with `bin/venv-python -m scripts.aws_identity caller-arn --profile agent_platform`; if the `agent_static` key was rotated, update its network secret. Do NOT block -- continue in degraded mode (credential-dependent verifiers are skipped, emitting SKIPPED). Autonomous executors never attempt recovery.
- **`uncommitted_changes` non-empty** -- Ask human: resume, stash, or discard? Wait. Continue on all other conditions.
- **`main_freshness.status == "fetch_failed"`** -- Informational: surface the fetch error and note
  that Step 5 code-review will diff against the stale local main ref and the Scope-overlap check
  will be skipped. Continue.
- **`main_freshness.commits_behind > 0`** -- Retain `main_freshness.main_files_changed_since_branch` for the Step 2 Main Divergence Check (below). Non-blocking at this step.
- **`validate` (presubmit) non-zero exit** -- The gate has detected pre-existing blockers on the branch. File each failed check as a recommendation via the portal (`automatable: false`), surface to human with go/no-go, STOP if no-go. Credentials-unavailable -> skip with actionable guidance per Decision 60; do not crash.

## Main Divergence Check (Workflow Step 2 -- after plan load)
Once the PLAN-{slug}.yaml `scope` list is parsed, intersect the scope file paths with `main_freshness.main_files_changed_since_branch` from the preflight report. If any scope file overlaps:

> "Main has changed [list of overlapping files] since this branch diverged, and your plan modifies the same file(s). Implementing without rebasing will produce a merge conflict at PR time after the work is already done -- and may invalidate the verification plan if the file's shape has changed. Recommend rebasing now: `git fetch origin main && git rebase origin/main` (resolve conflicts then re-run `/implement`). Options: (1) rebase now and re-enter `/implement`, (2) proceed anyway, (3) abort."

STOP and wait. Do not auto-rebase. If the human chooses (2), proceed with a logged note in chat output: "Proceeding without rebase despite Scope/main overlap on: [files]."

If `main_freshness.status != "ok"`, this check cannot run -- surface the fetch failure to the human and continue.

## Bundled Recommendation Relevance Re-check (Workflow Step 3 -- fires after plan load)

Before writing code, re-check the plan's `bundled_recommendations` (a rec can go stale after /plan):
`bin/venv-python -m scripts.rec_relevance --implement-recheck rec-NNNN[,rec-MMMM]`. It never closes a rec
(`docs/contracts/recommendation-relevance.yaml#implement_gate`); the merged trailer does.
- **`relevant` or `unknown`** -- implement as planned.
- **`satisfied`** -- the acceptance already passes on this tree; say so in chat. The rec stays bundled.
- **Any other verdict** -- present the printed proposal command and wait for the operator. A rec the operator closes
  stays bundled and in the trailer (the closure job skips it); one the operator supersedes or declines leaves
  `bundled_recommendations` only by a plan amendment PR, never a build edit.

## Live Verification Protocol (Workflow Step 4 -- MANDATORY)
After code changes are complete and unit tests pass, the implementing agent MUST execute the Verification Plan from PLAN-{slug}.yaml before proceeding to code review.

### Why This Exists (Rationale)
Acceptance commands prove the code landed (`grep`/`pytest`). Verification commands prove the feature works end-to-end. Examples of bugs only verification catches:
- A warehouse view is created successfully but returns 0 rows due to a bad filter
- Lambda deployed successfully but times out on invocation
- CLI script passes unit tests with mocks but crashes with real input

### Protocol
1. **For each step:**
   a. Execute the action exactly as described.
   b. Compare actual outcome to expected outcome.
   c. If **PASS**: record the actual output and proceed.
   d. If **FAIL**: diagnose root cause, fix the code, re-run `pytest` to confirm no regressions, re-attempt. Maximum 3 fix attempts per step. If still failing, STOP and report to the human.
2. **All verification steps must pass** before proceeding.

### Nondeterminism rule (VF-08, Decision 55 -- RCA-first, not retry-into-green)
Track Attempts per step (see VP Compliance Gate below). This rule governs what a re-run is allowed to mean:
- If a step **FAILS** and is then re-run with an **EMPTY `git diff`** since the prior attempt (i.e. nothing in the tree changed) and the re-run goes green, the step is **NONDETERMINISTIC** -- record it as NONDETERMINISTIC in the VP compliance table, never as PASS. An empty-diff green is evidence the failure was flaky, not fixed, and a silent PASS would hide that.
- Re-runs stay capped at the existing 3-fix-attempt bound. The nondeterminism rule does not license looping toward green -- it only names what an unexplained green means. On a NONDETERMINISTIC result, STOP and surface to the human (Decision 55: RCA-first, no rescue loops).
- A genuine fix requires a real code change (a **non-empty** `git diff` since the prior attempt). After such a change, the step must pass **two consecutive times** to count as PASS. These two confirmation passes verify an ALREADY-APPLIED fix and are **not** additional fix attempts -- they do not consume the 3-attempt fix budget, which governs diagnose-and-change cycles only.
- **Interactive-era alarm (now):** while the executor is frozen (Decision 67) a human is present at every `/implement` run, so the alarm is stop-and-surface-to-human; files nothing.
- **Forward-note (T3.19):** when the executor goes live (CD.17 reversal), this alarm flips to auto-file a rec via `scripts.ops_data_portal` (Decision 84), the executor-era quarantine-that-files-a-rec pattern (CD.29). Tracked as tier_item T3.19 (`deferred_post_mvp`) -- do not implement the auto-file path now.
- **Distinguish from declared non-determinism:** a VP step's DETECTED nondeterminism (this rule -- an agent-observed flaky re-run) is not the same thing as a verifier's DECLARED `Hermeticity.NON_HERMETIC_BY_CONSTRUCTION` property (a typed, audited characteristic of the verifier itself). Never silently relabel a detected-flaky VP step as "expected non-hermetic behavior" to justify passing it -- the two are orthogonal, and only the latter is a designed exemption.

### Deviation trigger
Three branches for a decision point hit during implementation (full detail:
`docs/contracts/implement-scope-boundary.yaml` `deviation_trigger`):
(a) in-scope under-specification -- decide inline; consult `model:"fable"` per
`.claude/skills/overseer/SKILL.md` "## Fable Advice-Consult Protocol" when load-bearing or novel,
recorded either way.
(b) scope or contract deviation, or any always-ask surface -- STOP; no consult proceeds past this
branch.
(c) repeated failure -- defer to the 3-fix-attempt STOP and VF-08 rules above (cited, not
restated); T3.19's auto-file-a-rec path stays deferred.

### Tier-Specific Guidance
- **V1:** Parse configs, check doc links, confirm formatting. Quick but mandatory.
- **V2:** Run the changed code path with real (non-mocked) input. Confirm the feature works outside the test harness.
- **V3:** All V2 requirements PLUS deploy and invoke the live system. Do not merge until invocation produces correct output.
- **Anti-Patterns:** Do NOT accept "Tests pass", "File exists", "No errors on import", or "Grep found expected string" as verified. Substituting an easier command for a VP step is a protocol violation.


### VP Failure Is Not Negotiable
If a VP step fails for ANY reason (including credential/environment issues), the status is FAIL.
There is no "graceful" failure, no "local pass", no "env blocked" — only PASS or FAIL. If the
failure is due to missing credentials or infrastructure: attempt the static-key recovery (see
Preflight Constraints above), re-run the VP step, and if still failing mark FAIL and STOP — do not
proceed, do not merge.

### VP Compliance Gate
Before proceeding to code review (Step 5), produce a VP compliance table in the chat output:
```
| VP# | Command Executed | Actual Output (truncated) | Attempts | PASS/FAIL |
```
- The "Command Executed" must be the actual shell command run.
- The "Attempts" column records the number of executions (1 = first-try pass; failed-then-passed
  records 2, etc.) -- the per-step count the nondeterminism rule below references.
- Bound each "Actual Output" cell to ~200 characters (or head+tail lines) so the table stays a
  compact, pasteable artifact rather than a full transcript dump.
- PASS/FAIL is not the only status: a step whose green result is flagged by the nondeterminism
  rule is recorded as NONDETERMINISTIC, never PASS.
- If ANY row is FAIL, do NOT proceed.
- If a VP step was skipped or is awaiting a human-gated action (e.g., terraform apply), mark it BLOCKED and wait.
- Lack of AWS credentials is NOT automatically a block. Verify the static-key chain per Preflight
  Constraints; there is no interactive login to run.
- This table is the proof artifact: per the IMPLEMENTATION Commit Flow below, it is appended to the PR body verbatim (with its Attempts column and any NONDETERMINISTIC markers) so the executed evidence survives past the chat transcript.

### V3 Merge Gate
If the Verification Plan contains V3 post-deploy steps, execute the full sequence:
0. Confirm credentials are active (static-key recovery per Preflight Constraints if the chain fails).
1. Complete all pre-deploy VP steps.
2. Present the deploy output.
3. WAIT for human confirmation of deployment success.
4. Execute post-deploy VP steps.
5. **Post the post-deploy evidence to the PR** (body or comment) before merge: the live invocation output AND a run URL (e.g. a CloudWatch, Lambda or GitHub Actions run URL; Decision 103: closure needs a proof, not a self-assertion). Admin-only root: the posted pre-merge plan evidence for the PR head is this artifact and the post-merge walk is acceptance (deploy-paths.yaml admin_out_of_band.pre_merge_live_plan). Do NOT merge a V3 plan without this posted evidence artifact -- code-review checks for it (see the code-review skill's V3 Post-Deploy Evidence check).
Only when ALL steps pass can you proceed to code review.


## Code Review Protocol (Workflow Step 5 -- MANDATORY)
**You MUST trigger the code-review immediately after the Verification Plan passes. Do not wait for the human to prompt you.**

### Trigger
**Subagent-dispatch mode:** same three-signal check as planning's subagent-dispatch gates -- if you
are a subagent (`SUBAGENT CONTEXT` header, no `Agent` tool, or a nesting error), do not dispatch
below; gate-request (`gate: code-review`, inputs branch+plan_path) and resume via `SendMessage` on
verdict. Schema: `docs/contracts/overseer-dispatch.yaml#gate_request_trampoline`.

Dispatch via the `Agent` tool with `subagent_type: "general-purpose"`, instructing the subagent to invoke the `code-review` skill via the Skill tool and return its structured output verbatim (the plan.md gates' idiom) -- NOT via `bin/venv-python -m scripts.agent_development.run_skill --skill code-review`. The subagent runs in a fresh context window (anti-bias) and has full tool access (read, grep, glob, bash) to inspect the entire branch diff.

Agent prompt template:
- Pre-instruct: "Run `git fetch origin main` before any analysis so the diff base is current. The branch may have been open for hours; the local `origin/main` ref may be stale."
- Identify the branch under review (the diff `git diff origin/main...HEAD` is the artefact under critique). Use `origin/main` (not the local `main` ref, which is only updated by an explicit pull).
- Identify the plan file (`docs/plans/PLAN-{slug}.yaml`) so the subagent knows the acceptance criteria and intent.
- Instruction: "Invoke the `code-review` skill (`ontheloop:code-review` where listed) via the Skill tool against this branch. Survey the diff, read the plan to understand intent, then return its structured findings report (including the final Verdict line) verbatim. Do not edit files."
- Forbid file edits.
- Require structured output: findings grouped by severity (Critical / High / Medium / Low) with file:line references and a one-line rationale per finding.
- Cap response length (~800-900 words) to keep the report focused.

Do NOT pre-brief the subagent on what to look for -- that biases the review and defeats the anti-bias gate. The subagent applies the `code-review` skill methodology on its own.

If the gate subagent errors or returns output missing the required Verdict/Recommendation line, the gate has NOT completed -- re-dispatch; never proceed past an incomplete gate.

### Handling Findings
- **Critical and High**: You MUST implement fixes for these findings before proceeding. They are mandatory extensions of the original plan. After fixing, re-run `bin/venv-python -m scripts.validate --pre` to confirm no regressions.
- **Medium and Low**: File these as new recommendations using `bin/venv-python -m scripts.ops_data_portal`. Do not fix them inline -- they will be addressed in future sessions.

## Tier_item bookkeeping (post-verification, pre-merge)

MANDATORY read-trigger: after the verification-pass gate fires and BEFORE code-review is
dispatched (or GATE_REQUESTed), read `docs/contracts/tier-item-lifecycle.yaml#bookkeeping_walk`
in full -- the single authoritative definition of the walk (trigger, tier_item identification
precedence, executable-vs-prose criterion evaluation with a conservative bias, and the
whole-item Outcome rules), the replacement closure check
(`docs/contracts/tier-item-lifecycle.yaml#replacement_closure_check`), and the
parallel-with-code-review state machine
(`docs/contracts/tier-item-lifecycle.yaml#parallel_with_code_review_state_machine`). The
criterion-level field semantics (the closes_criteria flip and the exit-criteria truth-maintenance
rule) live in `docs/contracts/exit-criteria-ledger.yaml#fields.status` instead -- read that too.
Do not re-author any of this here.

### Decomposition-hint exemption inheritance (T-1.12 subset g)

A STRATEGIC parent tier_item that was split into atomic IMPLEMENTATION plans under the freeze
override (AGENTS.md Temporary Operational Constraints / CD.17; the T2.19 pattern) carries a
`decomposition_hints` block naming its children:
```yaml
decomposition_hints:
  split_by: <subsystem|per_lambda|phase|...>
  atomic_plans:
    - "PLAN-<child-slug> -- <description> (subset <x>)"
  rationale: |
    ...
```
When the bookkeeping walk files a status outcome for the current plan, resolve the plan's
effective `bootstrap_completion_exempt` by INHERITANCE from its parent -- do NOT read it from
the child's own (often absent) flag. The three-step inheritance body (find the parent by
matching `decomposition_hints.atomic_plans[]` against the `PLAN-{slug}` token / inherit the
PARENT's live `bootstrap_completion_exempt` value as the single source of truth / read-only
resolution that never writes the parent's `decomposition_hints` or the child's flag) lives at
`docs/contracts/tier-item-lifecycle.yaml#decomposition_hint_inheritance` -- read it in full.
Canonical examples: T-1.12 is `bootstrap_completion_exempt: true` (inherit `true`, T1.12 is
`false` (inherit `false`). This is not a license to author STRATEGIC children: the freeze stays
in force and atomic children remain IMPLEMENTATION plans.

### Parallel-with-code-review state machine

Read `docs/contracts/tier-item-lifecycle.yaml#parallel_with_code_review_state_machine` in full --
it defines how the criteria walk above stages its YAML edit WHILE the code-review subagent runs
(not after), the idempotency-on-resume check, the PROCEED/REVISE branch, and the
abandonment/staged-edit-loss detection. Do not re-author it here.

## Verification Graduation (VF-05, T3.18/T3.21 -- fires on VP Compliance Gate all-PASS, before the commit flow)

Fires in the same window as Tier_item bookkeeping (after the VP Compliance Gate shows all rows
PASS, before the commit flow in Step 7) but is a distinct action: it tries to promote this
session's own VP steps into standing regression guards, not roadmap bookkeeping. Execute each
declared `test_obligations` selector/command first; record evidence on its VP step.

**T3.21 (enforced, amends T3.18):** graduation is not optional. If this plan's `verification_plan`
steps carry `graduation` dispositions (mandatory on pre-deploy steps for any plan authored after
T3.21 -- see the planning skill), `validate_graduation_completeness`'s implement-PR leg (both
tiers) FAILS the PR unless every step declared `graduate` produced a matching registry row. A plan
authored before the field existed (no dispositions anywhere) is exempt -- treat its graduation as
the legacy best-effort walk in step 1 below.

### Protocol
1. **Enumerate candidates from the plan's declared dispositions.** Walk this plan's
   `verification_plan` steps and take every step whose `graduation == "graduate"` as a candidate;
   its declared `graduation_check_id` IS the registry row's `check_id` (do not invent a
   different slug -- the implement-PR leg matches on `plan_slug` + `graduation_check_id`
   verbatim). Steps marked `waive` or `not-applicable` are never candidates. **Legacy fallback:**
   if this plan predates the graduation field (zero dispositions anywhere in
   `verification_plan`), fall back to the pre-T3.21 walk -- identify any step whose `command` is
   expressible as one of the six canonical primitive slots in
   `scripts.verification_checks.CANONICAL_SLOTS` (command_exit_zero, command_output_matches,
   file_presence, grep_count, test_selector, metric_under_threshold) and invent a stable
   `check_id` slug for it. A step that requires multiple commands, human judgement, or live
   infrastructure (V3 deploy/invoke) is never a candidate either way -- skip it.
2. **Build a registry row** for each candidate: `check_id` (the step's `graduation_check_id`, or
   the invented slug under the legacy fallback), `primitive_slot`, `check_spec` (the primitive's
   parameters -- see `docs/contracts/verification-registry.yaml` for the per-slot shape),
   `guard_target` (the artefact it defends), `guard_symbol` (optional), `plan_slug` (this plan's
   slug), `graduated_at` (today's ISO date), `graduated_by` (this session's canonical_id per
   Decision 66, or omit if none applies).
3. **Run the REAL differential admission gate** for each candidate row via
   `scripts.verification_graduation.run_differential(row, repo_root=<repo root>)`: it materializes
   the check, runs it live (must PASS), then checks it out in a real `git worktree` at
   `origin/main` and runs it there (must FAIL). This is a genuine `git worktree add`/`remove` --
   never a simulated revert.
4. **Admitted rows** (`outcome.admitted`) get written to their own
   `config/agent/verification_registry/entries/<check_id>.yaml` shard -- never appended to a
   shared file.
5. **A declared-`graduate` candidate that is REJECTED (tautological, or fails on HEAD/live) is
   un-graduatable, not silently dropped.** Flip that VP step's disposition from `graduate` to
   `waive` in `docs/plans/PLAN-{slug}.yaml`, setting `graduation_waiver_reason` to the concrete
   rejection reason (e.g. "differential rejected -- passes even with origin/main reverted,
   tautological given this repo's fixture state"), and remove `graduation_check_id`. Commit this
   plan edit in the same PR (Step 7's `git add -A` picks it up) -- this is what satisfies
   `validate_graduation_completeness`'s implement-PR leg for that step, since a `waive`
   disposition requires no registry row. A legacy-fallback candidate (step 1's fallback path) that
   is rejected is simply dropped, unchanged from pre-T3.21 behaviour -- there is no disposition
   field to flip.
6. **Errors are fail-loud (Decision 55).** A `scripts.verification_graduation.GraduationError`
   (worktree add/remove failure, materialization error, missing check_spec key) STOPS this
   step and surfaces to the human -- never a silent "none graduated". Only a
   legitimately empty candidate set (no `graduate`-disposition steps, and -- under the legacy
   fallback -- nothing kernel-expressible) is a real "none graduated" outcome.
7. **Record the outcome ephemerally.** Whether rows were admitted, some were flipped to waive, or
   the candidate set was legitimately empty, record an explicit note in the PR body (a
   `## Verification Graduation` section: which check_ids were admitted, which were flipped to
   waive and why, which legacy-fallback candidates were rejected and dropped, or "no
   graduate-disposition VP steps this session -- none graduated"). This record is ephemeral
   PR-body evidence (Decision 115) -- NOT a numbered Decision, NOT a warehouse write. The durable
   artefacts are the new registry shard(s) and any plan disposition flips, committed in the same
   commit as the plan's other changes (Step 7's `git add -A` picks it up).

### Constraints
- Never invent a registry row for a VP step that doesn't genuinely distinguish pre/post-change
  behaviour -- a step that would pass identically on origin/main is not a candidate, regardless
  of whether it's kernel-expressible.
- `scripts/verification_checks.py` (the kernel) is never edited by this step -- it stays the pure
  six-slot vocabulary. All worktree/revert mechanics live in `scripts/verification_graduation.py`.
- Do not graduate a check whose command depends on live infrastructure, credentials, or wall-clock
  state (that's the domain of the V3 verifiers in `scripts/verifiers/`, not this kernel).

## CD Ratification Bookkeeping (Workflow Step 6 -- CONDITIONAL, fires when the plan has a ratification block)

Fires only when the approved plan carries a ratification block (see the planning skill's
"Candidate Decision Ratification" section). This step's authoritative shape is
`docs/contracts/candidate-decision-ratification.yaml`. Ratification is the user's call --
this step NEVER runs without the explicit execution-time confirmation it requires.

MANDATORY read-trigger: read
`docs/contracts/candidate-decision-ratification.yaml#lane_steps.implement.execute` in full before
executing -- the single authoritative definition of the eight-step protocol (locate the
ratification block; STOP for explicit execution-time human confirmation of the Decision text;
author the DECISIONS.md entry; ETL via the Single Portal Invariant; flip the CD to the canonical
ratified shape; re-run preflight; run `bin/venv-python -m scripts.validate --pre`; never flip a
tier_item status here), the batch-wave bundling rule (Decision 150), and the rejection-handling
rule. Do not re-author it here.

## Strategic Scoping Rules (Workflow Step 3 -- STRATEGIC Plans only)

### JIT Context Injection
When breaking a STRATEGIC plan into atomic recommendations, review `docs/PROJECT_CONTEXT.md`. Copy any relevant "Known Gotchas" or constraints directly into the recommendation's `context` field. Autonomous executors no longer read `copilot-instructions.md` by default, so they rely entirely on the JIT context you provide.

### Quality Gate Validation
Before filing each recommendation using `bin/venv-python -m scripts.ops_data_portal`, apply this gate. FAIL if any check fails:
1. **Acceptance Command:** Must be a single inline command in backticks. FAIL if: contains `python -c`, contains `--pre` flag, has trailing prose, or uses line numbers. Must be behavioural.
2. **Target File:** Verify `"file"` field exists relative to repo root.
3. **Effort Threshold:** If `L` or `XL`, REQUIRE human confirmation before filing.
4. **Context Quality:** FAIL if context is vague or < 50 characters.

### Dedup Gate
Before filing, search for open recs targeting the same file with at least 3 keyword matches. If duplicates found:
- Surface: "Found potential duplicate(s). Options: (1) supersede existing, (2) file both, (3) skip this one?" Wait for human.

## Commit Flows (Workflow Step 7 -- MANDATORY)
**Execute the commit flow autonomously once Step 6 completes -- the plan was approved during /plan.**

This workflow runs on Claude Code on the web: the harness assigns this session its own branch (e.g. `claude/...`), the `gh` CLI is NOT available, and the container hibernates between turns. All GitHub operations use the GitHub MCP tools (`mcp__github__*`). Branch protection is LIVE; the squash-merge-after-CI gate transport is the GitHub MCP `merge_pull_request` tool (Decision 76). See `docs/contracts/git-ops.yaml` as the canonical git-ops authority.

### Run the full gate locally first (in order)

1. Run `bin/venv-python -m scripts.session.github_readiness`. States are independent: `unknown` is unproven, never PASS; fetch/API PASS never authorizes push/PR.
2. Run `bin/venv-python -m scripts.test_coverage_checker --check-tests --check-coverage`. Repair failures; this is a diagnostic, not an overall gate.
3. Set `implementation_declared: true` in the plan file, now that the graduation walk above wrote its registry rows.
4. Run `bin/venv-python -m scripts.validate --pre`. The full tier `bin/venv-python -m scripts.validate` runs once per push in the Commit Flow, on the committed, rebased, clean tree: exit 0, final `Validation Summary (scope: all)` PASS, then `--verify-head` exit 0 (git-ops.yaml `handoff_sequence`). Child output, mypy, CI, and stale evidence cannot substitute. Any incomplete/mismatched result is BLOCKED; fix, commit, rerun.
5. Create `/tmp/implementation-retrospective.json` with the ten `scripts.session.postflight_evidence.CATEGORIES` keys. Values are bounded fact-string lists (`[]` if none); exclude credentials/raw output. Run `bin/venv-python -m scripts.session.postflight --evidence /tmp/implementation-retrospective.json` and confirm its gitignored output. `--auto` stops with `evidence_failed` before commit when invalid.

Report readiness, validator verdicts, evidence, review, and real push/PR outcome separately.

### Wait-for-CI: event-driven, never polled
Wait for the PR-tier CI (fast `--pre` tier; Decision 73) via subscription, never polling -- the wake mechanism is canonical in `docs/contracts/git-ops.yaml` steps 3-5, not restated here.

**On wake**, always confirm check runs via `mcp__github__pull_request_read` (`get_status` / `get_check_runs`) BEFORE merging, then branch on status:
   - **All green** -> for an IMPLEMENTATION PR, Commit Flow step 4 (premerge, squash-merge, readback); otherwise `mcp__github__merge_pull_request(owner, repo, pullNumber, merge_method="squash")`. Then `mcp__github__unsubscribe_pr_activity(...)`. Report the merge with its sha. **Carve-out:** for a PR touching `terraform/personal/**`, do NOT unsubscribe here -- defer to the "Hold subscription through apply" section below (the real outcome is the post-merge apply, not the merge).
   - **Any red** -> diagnose, fix, re-run the Commit Flow (re-triggers PR CI). Stay subscribed and end the turn. Per `docs/contracts/ci-rca-lifecycle.yaml` trigger_scope, this failure is uncovered by ci-rca by construction -- no rec is filed, nothing gates; the existing 3-fix-attempt STOP and VF-08 rules above govern the loop (Decision 55). Never weaken a check to obtain green -- `docs/contracts/implement-scope-boundary.yaml`'s CONTENT invariant is canonical; cited here, not restated. `executor-rca` is out of scope here -- Step 8's RCA-First Protocol is separate, session-end friction capture.
   - **Still running** -> end the turn; a later event wakes you.

### Hold subscription through apply (terraform/personal PRs -- CD.35 / T2.20)
For a PR that touches `terraform/personal/**`, the sandbox CD apply runs **post-merge** on `main`
(`terraform-apply-sandbox.yml`), and that apply -- not the merge -- is the real outcome (it writes the
convergence record green/red). After the squash-merge, **do not unsubscribe**: hold the PR subscription so
the apply job's best-effort post-merge SHA->PR wake (it resolves the PR from the merge commit SHA, since
`workflow_run.pull_requests[]` is empty for push) can re-engage this session. The wake is best-effort and
likely fights the `subscribe_pr_activity` contract (unsubscribe-at-merge, open-PR-scoped); **the
authoritative baseline is the next planning session's convergence-record re-check** -- poll-free, never a
`sleep`/`/loop` (Decision 76). If the apply reds the record, `ci-rca` files a `source=ci_rca` rec; clear red
only via the `workflow_dispatch` acknowledge-and-retry path (naming the red commit/rec) after the rec is
reviewed -- never an inline workaround (Decision 55). Unsubscribe once the record is green (apply converged)
or the next planning session has assumed the baseline.

**Gated set (out-of-budget IAM / trust / destroy -- CD.35 Wave 3 / T2.22 + T2.25 narrowing):** if the
change hits the guard's gated set (guard exits 2), the post-merge path routes to the `gated-apply` job rather
than auto-applying. In-budget IAM inline-policy/attachment UPDATEs on managed boundary-carrying CI roles
(T2.25 / Decision 92 point 5) now exit 0 and auto-apply; role CREATES, trust diffs, destroys, and
out-of-budget IAM still exit 2 and route here. The job declares `environment: tf-gated-apply` and **blocks
until benjamin-blake approves in GitHub Actions** (Actions tab -> select the run -> Review pending deployments
-> Approve). This is NOT a PR required status check -- the PR merges normally; the gated apply is a separate
post-merge job. After approval, the gated-apply job applies the same saved plan.bin and writes the convergence
record. The authoritative baseline is still the next planning session's convergence-record re-check (not the
wake). The gated apply gates the JOB, never from a laptop.

### Pre-Push Rebase (applies to both flows)
See `docs/contracts/git-ops.yaml` `rebase_phase_distinction.commit_flow_time`; once pushed, use
`--force-with-lease` (never `--force`).

### IMPLEMENTATION Commit Flow
```bash
git add -A
git commit -m "feat({slug}): implement {brief-description}" -m "Resolves: rec-NNNN[, rec-MMMM]"   # omit -m when nothing is bundled
git fetch origin main
bin/venv-python -m scripts.session.rebase_resolve   # STOP unless RESOLVE CLEAN
bin/venv-python -m scripts.validate
bin/venv-python -m scripts.checks.validation_result --verify-head
git push -u origin HEAD   # its own tool call
```
Then via GitHub MCP (owner/repo from `git remote get-url origin`):
1. Build the PR body, repeating the commit's `Resolves:` trailer block (`docs/contracts/git-ops.yaml#resolves_trailer`; `ci.yml`'s trailer jobs close each named rec after merge). Under a clear heading (e.g. `## VP Compliance`), append the VP compliance table to the PR body -- the same bounded table produced by the VP Compliance Gate, including the Attempts column and any NONDETERMINISTIC markers -- so the executed proof lands in the PR/merge record instead of vanishing chat (Decision 115: this PR-body table is PR-scoped ephemeral evidence, not the durable record -- the durable record is the tier_item criterion closure in the roadmap, staged by the bookkeeping walk below).
2. `mcp__github__create_pull_request(owner, repo, head=<this branch>, base="main", title="feat({slug}): {brief-description}", body=<body from step 1>)`
3. `mcp__github__subscribe_pr_activity(...)`; end the turn (see "Wait-for-CI").
4. On green wake: write the squash commit message (the implementation commit's body, trailer included) to a file and
   run `bin/venv-python -m scripts.session.merge_step premerge --slug {slug} --message-file <file>` (add
   `--expect <digest>` when the operator approved text); fix any red line before merging. Then
   `mcp__github__merge_pull_request(..., merge_method="squash", commit_message=<that message>,
   expectedHeadSha=<the HEAD premerge printed>)`, `bin/venv-python -m scripts.session.merge_step readback --slug
   {slug} --sha <merge sha>`, and unsubscribe.
5. **Post-merge closeout fallback (Decision 201, conditional).** WAIT for the
   `trailer-closure` job (`mcp__github__actions_list` by merge sha -> `get_job_logs`, never
   `$GITHUB_STEP_SUMMARY`) to CONCLUDE, then follow the conditional fallback in
   `docs/contracts/git-ops.yaml::resolves_trailer`: an acceptance-verdict refusal takes that clause's
   route, a Decision 186 refusal stays open and is reported, a merge-leg refusal or a non-refusal
   per-rec log line permits the fallback, a trailer readback found missing goes to `merge_step
   recover` (never the fallback); anything else waits.

### STRATEGIC Commit Flow
STRATEGIC plans are suspended (Decision 67). When restored, use the same MCP PR/subscribe/merge pattern, committing `docs/plans/briefings/` with a `scope({slug}): ...` message.
