---
name: plan-critique
description: "Use when: critique a plan, challenge assumptions, review docs/plans/PLAN-{slug}.yaml before implementation. Mandatory gate between planning and implementation."
---

## Intent

Challenge plans independently.

This BLOCKING gate assesses strategic soundness and bounds; formatting-only review is insufficient.

---

## Steps

### Phase 1: Load Context (MANDATORY - Do Not Skip)

Reject a plan that invents a third validation tier, treats mapped coverage or readiness as authoritative, or permits required full validation to time out, remain incomplete, or become a warning/CI-only handoff. A schema-v3 IMPLEMENTATION plan must carry the exact blocking handoff policy.

1. Read the ENTIRE plan file provided by the caller; absent a path, use the newest `PLAN-*.yaml` in `docs/plans/`.

2. Read `docs/PROJECT_CONTEXT.md` in full (for rules); read `docs/contracts/north-star.yaml` when a Phase 2b answer turns on a north star.

3. **Targeted roadmap extraction, not a full-file read:** extract only the `tier_items[]` (from `docs/ROADMAP-PLATFORM.yaml`) the plan's `phase` and `context` fields name, via a `bin/venv-python -c` yaml.safe_load projection -- do not `Read` the file in full.

4. **Targeted decision extraction + conflict-sweep, not a full-file read:** read only the decision sections named in the plan's context `Decision-scout verdict + CITE list` (locate each via `rg "^## Decision N:" docs/DECISIONS.md`), plus a conflict-sweep: `rg` 2-3 keywords drawn from the plan's approach over `^## Decision` headers in `docs/DECISIONS.md` to catch a contradiction the scout's CITE list omitted. Do not Read the full file; the decision-scout gate already paid that cost.

5. **For IMPLEMENTATION plans:** Read the files listed in the plan's `scope` list to verify the plan's accuracy.

5b. For IMPLEMENTATION plans, run `bin/venv-python -m scripts.roadmap.plan_obligations --plan <plan path>` and fold its report (companions, size headroom, unbuilt plans on the same files, undecidable acceptance) into the scope check above -- arithmetic is the tool's, adequacy is yours. With an approved-text sidecar, read the last `wording-check` gate row (never re-run it): REVISE when it is absent, not PASS, its digest differs from `approved_text record`, or its selection was degraded.

5c. **Guard input check (PCL-01, from round 1):** if the plan adds or changes a hook, a `scripts/checks` check, an IAM or boundary statement, a sandbox or a governance cap, list every input form the guard decides on (unknown, malformed, substituted, grouped or indirect, a present key with a bad value) with its outcome, and for each live VP step the identity and limits it runs under. A path that passes unexamined, or a live step that cannot run as written, is blocking.

### Phase 2: Strategic Analysis

6. **Check for decision and constraint conflicts:** Does the plan, its Constraints included, contradict or re-decide anything resolved in `docs/DECISIONS.md` or a rule in `docs/PROJECT_CONTEXT.md`? Cite the decision or rule and the conflicting plan section.

7. **North Star alignment is the decision scout's (Decision 243 clause 8):** quote its Serves line from the plan's `context`; never re-score it. Absent: a nit.

8. **Evaluate Scope breadth (IMPLEMENTATION; one verifiable, revertible outcome, `/plan` Step 4):**
   - For IMPLEMENTATION: Are all scope entries necessary? Too large (suggest split)? Does the Scope extend beyond the stated phase?

9. **Check phase dependencies:** Does this plan depend on work from a prior phase that is not yet complete? Cite specific ROADMAP phase entries.

10. **Assess strategic risks:** Are there risks that could invalidate the approach without execution? Examples: external dependency not yet available, contradicts a pending decision, architectural assumption that hasn't been verified.

11. **Check Acceptance Criteria:** Are all criteria verifiable and testable? Flag any that are vague, subjective, or untestable (e.g., "improved performance" without a metric).

12b. **Lambda deployment completeness (IMPLEMENTATION only):** VERIFY, do not re-derive -- planning
Step 4 ran this. Recompute `compute_affected_artifacts(<scope files>)` ONCE and diff it against the
artifacts the plan's `infrastructure_dependencies`/VP steps name; a set mismatch, or an artifact
`active` per `src/lambdas/<slug>/manifest.yaml` missing a build, deploy, smoke-test or model-ID
step => REVISE naming it. Stubs need no deploy (Decision 79).

12c. **Verification Plan executable command check (IMPLEMENTATION plans only):** Every `verification_plan` entry MUST have a `command` field containing a literal executable shell command or Python one-liner; judge whether it exercises the feature rather than checking structure. FAIL any prose-only step. For V3 plans, every VP step's `phase` must be `pre-deploy` or `post-deploy`. If either check fails, recommend REVISE with the specific VP steps that need commands or tags added. Admin-only root in scope: REVISE unless deploy-paths.yaml admin_out_of_band.pre_merge_live_plan is met.

12c-1. **Test-obligation adequacy:** For schema-version-4 IMPLEMENTATION plans, recommend REVISE
when a behavior-changing scope file lacks a linked obligation, its test is structural-only or
does not assert the named behavior, its red/green expectation is absent, or its waiver is thin.
Presence/link validators are deterministic; adequacy is this fresh-context judgement.

12k. **Closure obligation check (CONDITIONAL -- IMPLEMENTATION plans only):** VERIFY the plan's own declarations against its scope and VP; do not re-derive the obligation. Additive plans that trip neither condition are exempt.

Trigger condition 1 -- **Rec-resolving plan**: `bundled_recommendations` is non-empty, OR the plan's `intent`, `context`, `scope`, or `acceptance_criteria` names an open rec id as the motivation.
- Required: every named rec id also appears in `bundled_recommendations` (a named rec with an empty list is the exact miss this catches).
- Required: at least one VP step verifies each listed rec closed (e.g. ducklake-reads `recs_by_ids` returns the rec with status closed).
- If either is missing, recommend REVISE: "Rec-resolving plan omits closure obligation: add bundled_recommendations list and a VP step to verify each rec closed."

Trigger condition 2 -- **Surface-retiring plan**: `scope` carries an `action: Delete` row OR the plan states an X->Y migration/cutover (old path deleted, Lambda retired, write path swapped, config flag removed, backend superseded).
- Required: a VP step proving the old surface dead; for a deleted writer, each caller disposed per `tier-item-lifecycle.yaml#replacement_closure_check` (5).
- If missing, recommend REVISE: "Surface-retiring plan omits stale-reference sweep VP step: add a VP step that verifies the old surface is dead."

12l. **closes_criteria check (follow-on plans, IMPLEMENTATION only):** If the plan's phase/context names an `in_progress` tier_item, `closes_criteria` MUST be non-empty; each ref must name a criterion carrying `status: open` in `docs/ROADMAP-PLATFORM.yaml`; and `acceptance_criteria` must map 1:1 onto the chosen open criteria. If any of these fail, recommend REVISE: "Follow-on plan omits or misdeclares closes_criteria: [specifics]." A plan targeting no in_progress tier_item is exempt from this check.

12m. **Tier fitness check:** `verification_tier` must satisfy the planning skill's Verification Tier Guidelines, narrowed by an intentional refinement (Decision 48/79) so comment-only `.tf` or docstring-only Python edits are not force-escalated: a **resource-affecting** `.tf` scope file (not comment-only) OR an **active-manifest** Lambda scope file (`status: active` in its manifest -- Decision 79; `status: stub` does not trigger this) => `V3`; any Python source scope file => `>= V2`. A lower declared tier than the qualifying scope requires => recommend REVISE citing the specific scope file and the tier it demands.

12n. **Size headroom check (Decisions 128, 166):** For each scope row the 5b report marks over budget or NEAR_CAP, or whose planned change crosses its limit, the plan MUST include a decomposition step (facade package, Decision 80/104/124), a class relief-valve step measured at build time, or a justified raise step citing `# raise-approved: dec-NNN`. With none => REVISE: "Scope file [path] crosses or nears its size limit with no decomposition, relief or cited raise step -- decompose by default."

12o. **Graduation-classification honesty (T3.21, VF-05):** each `phase: pre-deploy` VP step's `graduation` (three-way classification: docs/contracts/vp-red-before.yaml) is REVISE-worthy when **missing** (exempt only for a pre-T3.21 plan declaring no disposition anywhere); **suspect `not-applicable`** on a plainly single command_exit_zero / grep_count / file_presence / test_selector / command_output_matches / metric_under_threshold shape (`scripts.verification_checks.CANONICAL_SLOTS`), the gaming shape, since `not-applicable` carries no registry obligation; or a **thin waiver** whose `graduation_waiver_reason` is generic ("not needed", "skip", "later") rather than a concrete reason. Cite the step and sub-check; else report "graduation dispositions complete and honest."

12p. **Category-consistency verdict (Decision 167 clause 2 -- fresh Decisions and CD ratifications alike):** No other check here asks whether a drafted numbered Decision's claimed significance row, kind and serves match its body.
   - **Trigger detection.** Fires when EITHER the plan carries a ratification block (Workflow Step 5b above) OR the plan's `scope` includes a `docs/DECISIONS.md` row AND the plan's drafted text contains a numbered `"## Decision NNN:"` heading. A row adding only a dated amendment trailer does not fire it: an amendment carries no envelope.
   - **Verdict.** When the trigger fires, locate the drafted entry's claimed `significance.value` (envelope field or narrative Significance marker) and quote VERBATIM the body sentence that satisfies or fails `docs/contracts/decision-entry.yaml` `significance.routing_rule`'s three questions for the claimed row, the own clause deciding `decision-kind.yaml`'s `test` for `kind.value`, and for each `serves` id the sentence `decision-entry.yaml`'s `serves.verification` requires (`plan_obligations` prints the north star). Any mismatch is REVISE-worthy on its own -- a mandatory gate, never surface-and-defer.
   - **N/A branch.** Untriggered, emit the Phase-3 row's N/A form, so a silent omission is distinguishable from a pass.

### Phase 2b: Frame Challenge (MANDATORY)

Phase 2 checks the plan's *details* against the existing frame. This phase challenges the *frame itself*. Failure mode: Decision 75 (frame lock).

Ask the following five questions against the plan's chosen approach. For each, write a one-sentence answer. Where the answer surfaces a concrete contradiction with a Decision, a Roadmap item, or a North Star principle, recommend REVISE. Otherwise surface the answer informationally -- the human or downstream planner decides whether to pivot.

12e. **Question 1 -- Is the chosen primitive the right primitive?** What if the orchestrator / runtime / data store / interface in this plan wasn't this kind of thing? Could the role be filled by a cloud-native primitive (Step Functions, EventBridge, SQS, DynamoDB Streams, Lambda) already in production in this codebase? If the plan introduces a custom orchestrator, scheduler, retry loop, state machine, or queue, name the cloud primitive it could be replaced by and explain why it isn't.

12f. **Question 2 -- Is the decomposition boundary right?** If the plan treats X as a single unit, could X be decomposed into smaller independently-deployable, independently-observable, independently-retryable units? If the plan proposes N independent units, should they collapse into one? The frame-lock case (Decision 75) was a monolithic Python loop that should have been decomposed into per-step Lambdas orchestrated by Step Functions; the corresponding question would have caught it.

12g. **Question 3 -- What existing capability could absorb this custom code?** Enumerate the capabilities ratified in `docs/DECISIONS.md` and shipped in `terraform/` that are conceptually adjacent to what the plan proposes. For each, ask why the plan is not using it. Plans that propose custom logic where AWS-native or codebase-native primitives exist must justify the divergence explicitly.

12h. **Question 4 -- Are inherited assumptions still valid?** When the plan cites a constraint of the form "we can't do X because Y," and Y references a Decision older than 30 days (or one that predates a more recent capability-ratifying Decision), surface that Decision and ask whether its premise still holds. CD.11's premise ("Fargate replaces Lambda dispatcher because executor exceeds 15 min") is the canonical example -- it carried forward an assumption that newer decomposition options invalidate.

12i. **Question 5 -- What tools have been acquired since this approach was conceived?** When the plan extends, refactors, or revives an existing approach, enumerate the Decisions, infrastructure primitives, and skills added to the codebase since the approach's original design. Ask whether any of them retroactively change the right shape of the work. Decision 39 (Step Functions over Airflow) is the canonical retroactive trigger that was not applied to the executor architecture; this question would have caught the gap.

12j. **Recommendation impact:** If any of 12e-12i surfaced a concrete contradiction with a Decision, a Roadmap item, or a North Star principle, recommend REVISE and cite the contradiction. If the challenges surface real questions but no concrete contradiction, surface them in the Frame Challenge output field for human consideration and do not, on this basis alone, recommend REVISE.

### Phase 3: Structured Output

13. Produce this structured output:

```
## Plan Critique

**Files Read:** [plan path; docs/PROJECT_CONTEXT.md; docs/contracts/north-star.yaml (if read); targeted roadmap items: <ids>; targeted decision sections: <ids>; scope files (IMPLEMENTATION only)] -- count must match the scope entry count

**Plan Type:** IMPLEMENTATION / REPORT-ONLY

**Decision Conflicts:** None / [list with decision numbers]

**Frame Challenge (Decision 75):**
- Q1 (right primitive?): [one-sentence answer, or "no frame issue"]
- Q2 (right decomposition?): [one-sentence answer, or "no frame issue"]
- Q3 (existing capability could absorb?): [one-sentence answer, or "no frame issue"]
- Q4 (inherited assumptions still valid?): [one-sentence answer, or "no frame issue"]
- Q5 (tools acquired since approach was conceived?): [one-sentence answer, or "no frame issue"]
- Concrete contradiction surfaced: yes / no
  [if yes, cite the contradiction]

**North Star Alignment:** [the scout's Serves line, quoted] / absent (nit)

**Scope Evaluation:**
- [Area or file 1]: appropriately scoped / too large (more PRs, or child recs X, Y; not a deferred half filed as a follow-on rec) / too small (merge with: Z)
- [Area or file 2]: ...

**Phase Dependencies:** Aligned / [describe issue and which phase must complete first]

**Strategic Risks:** [list or "none identified"]

**Acceptance Criteria Issues:** [list or "none"]

**Lambda Deployment Completeness:** Complete / Missing [list missing steps]

**VP Executable Commands:** Complete / Missing commands for VP rows [list] / Missing pre/post-deploy tags [list]

**Guard Input Check (5c):** N/A -- no guard in scope / each input form -> outcome; each live VP step -> identity, limits / REVISE -- [unexamined path or unrunnable step]

**Closure Obligation (12k) / closes_criteria (12l):** each N/A / Compliant / Missing [specifics]

**Tier Fitness (12m):** Compliant / REVISE -- [scope file] requires [tier] but plan declares [lower tier]

**Graduation Dispositions (12o):** Complete and honest / REVISE -- [step number(s) and sub-check failed]

**Category Consistency (12p):** one line per drafted entry, title first: N/A -- no drafted Decision / PASS -- "[verbatim body quote]" supports claimed [row, kind, NS.n] / REVISE -- claimed [row, kind, NS.n] but body reads: "[verbatim quote]" or no sentence supports it

**Finding-Origin Attribution and severity:** blocking only when the finding states what reaches main if left unfixed and which control fails first, and (a) wrong behaviour, a wrong guarantee or a wrong operator-read instruction would reach main, or the first control that could catch it runs only after merge; (b) the build could not finish inside the plan's scope, approved text or VP commands without a plan amendment; or (c) it needs an operator decision. A check above that says REVISE or FAIL raises a finding this rule grades; 5c and 12p findings stay blocking. Every other finding is a nit carried to the build as a note (closure-step restatement of the build skill is never blocking); a Frame Challenge contradiction is never a nit; mechanical (a deterministic check could catch it -- name it) / critic judgement -- tag each finding, with a stable anchor (plan field path or VP step id)

**Recommendation:** PROCEED (no blocking finding; list nits) / REVISE (any blocking finding) [with specific suggestions if REVISE]
```

14. If REVISE, list the changes needed before implementation. State each measured figure as a hypothesis with the command that produced it: the reviser will re-measure it and dispute, with evidence, any it cannot reproduce.

---


## Quality Gate

Before output, confirm you read EVERY scope file (not just .md), can cite line numbers for the call sites and mocks your findings name, and your Files Read count matches the scope entry count; else read the missing files first.
