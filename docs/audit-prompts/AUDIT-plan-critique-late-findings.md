# AUDIT PROMPT: Do late plan-critique rounds block findings that matter? A counterfactual audit of the plan-critique gate's blocking and stopping rules

Execute this prompt exactly as written. It is self-contained: do not ask clarifying questions.
Every judgment call is assigned below; where something fails, take the named degraded path and
keep going.

## 1. TASK

Audit one surface, S1: the plan-critique gate's round loop -- its blocking rule (which critic
findings block a plan) and its loop and stopping rules (when rounds continue and when they end), as
written in `.claude/skills/plan-critique/SKILL.md` and `.claude/skills/planning/SKILL.md`
(Section 10). The method is a counterfactual trace over a fixed sample of 40 plan-critique rounds
that returned REVISE, drawn from plan files changed on `main` from 2026-11-09 (frame and strata in
Section 11): 8 confirming rounds (CONF), 22 other rounds numbered 3 or later (LATE) and 10 control
rounds numbered 1 or 2 (CTRL). For ONE blocking item per sampled round (Section 9 DD-A), determine
what would have happened had the item NOT been fixed before the plan merged: which later control
would have surfaced it, whether it would have changed nothing, or whether a defect would have
reached `main`. From that, answer Q1-Q7 (Section 7): the counterfactual per item; whether late items
differ from round 1-2 items; which candidate blocking rules would have kept every item that
mattered blocking; which recurring item types a deterministic check could catch before a critic
runs; whether confirming rounds and rounds after round 5 caught anything that mattered; and whether
plan-level detail prevents build rework. You judge defect consequence only: you are not asked
about, and must not estimate, token, time or money cost. Deliverables:
`audits/plan-critique-late-findings-<sha>.yaml` and `audits/plan-critique-late-findings-<sha>.md`
(`<sha>` per Section 5). The ONLY files you create or modify in the repository tree are those two
deliverables; fetching PR refs and regenerating gitignored local caches per Section 5 is expected
and is not a breach (never commit them). You draft; the human disposes. You change no skill,
contract, plan or code, file no recommendation, and dispatch no critic or other agent to re-review
any plan.

## 2. CANDIDATE OBSERVATIONS vs VERDICTS -- THE EPISTEMIC CONTRACT

This prompt hands you FACTS (Section 10), a SAMPLE (Section 11) and CANDIDATE hypotheses (below).
It never hands you a verdict. ASSUME NO CANDIDATE IS A REAL DEFECT UNTIL YOU TRACE IT. A run that
merely confirms the candidates below has failed. Each candidate is a pair of opposing hypotheses,
side A and side B; neither side is the expected answer, and the order of the sides means nothing.

Adjudicate every candidate in `candidate_adjudications` (Section 14) with a verdict from
`side_a | side_b | mixed | insufficient_evidence`, citing the item ids that decide it. An
adjudication is not a finding. Mapping to the output:
- An adjudication that shows a defect in S1's rules -> a `findings[]` entry with `origin` set to
  the candidate id, classified per Section 13.
- A possible S1 defect (from a candidate or raised by you) that you dismiss because a control
  already covers it, or because a built owning item fully covers it -> a `rejected_candidates`
  entry, with the control and its property match (Section 14).
- A candidate whose evidence shows no S1 defect -> its adjudication only.

Candidates:
- C1 Late tail. Composer-measured over the sample frame (re-derive from `gate_rounds` if a verdict
  depends on it, at composing base `79a6cd09` with the Section 11 frame): mean blocking findings
  per round are about 4.0 in round 1, 1.8 in round 2 and 1.1
  in round 3. Over rounds 4 and later, 41% of rounds return REVISE with 0.65 blocking findings per
  round; per round the REVISE share is 48% at round 4, 42-43% at rounds 5-7, 35% at round 8 and 27%
  at round 9, and rounds 10 and later have under 31 rows each. Side A: the late tail is mostly
  items whose absence would change the delivered outcome. Side B: the late tail is mostly items a
  later control would catch or that change nothing.
- C2 Confirming rounds. In the sample frame, 201 rounds are confirming rounds as defined in T3, and
  32 of them returned REVISE. Side A: confirming rounds catch items that matter and that nothing
  downstream would catch. Side B: confirming-round blocking items are no more consequential than
  other late items.
- C3 Origin of late items. Side A: late blocking items are mostly defects that an earlier round's
  fix introduced. Side B: late blocking items are mostly in text that predates the earlier fixes
  and that earlier rounds did not flag.
- C4 Mechanical share. Side A: a large share of late blocking items are mechanical (a
  deterministic check could catch them from the plan text and repository state); state the share
  you call large. Side B: late blocking items are mostly critic judgement.
- C5 Blocking tag fit. The critique skill tags a finding blocking for correctness, safety, scope,
  a Decision conflict or a failed check, and "when unsure" (Section 10). Side A: late items tagged
  blocking mostly fit one of the named classes on their merits. Side B: a material share are
  blocking only by the "when unsure" default and would read as nits (wording, citation form,
  ordering) by the same rule. Judge C5 only on items whose `item_source` is
  `gate_rounds_annotation` or `commit_message` (DD-A step 3 selects `diff_inferred` items from
  non-nit-looking changes, so they cannot test C5); state how many items that is.
- C6 Plan detail. Side A: plan-level detail (named test nodes, fixture cases, VP commands) spares
  the build rework. Side B: the build redoes or departs from that detail anyway, or rework traces
  to that detail being wrong.

Pinned reading of the sides: "mostly" means more than half of the items relevant to the candidate.
Choose `side_a` or `side_b` when more than half of the relevant items support that side (for C5
side B, at least a quarter suffices, as it claims a "material share"); `mixed` when neither side
reaches its share; `insufficient_evidence` when fewer than 5 relevant items exist. Name the
relevant items and the count.

## 3. READ FIRST -- DISAMBIGUATION TRAPS

- T1 Who wrote the row. `gate_rounds` rows are written by the planner after fixing, not by the
  critic, and the critic's own output is not stored in the repository. A row's trailing comment
  (`# B1 ...; N1 ...`), where present, is the planner's paraphrase; most rows carry counts only
  (5 of the 40 sampled rows have a comment: samples 6, 14, 16, 31, 34). The fix commit (T4) is
  therefore the main evidence of what a blocking item was.
- T2 Which gate. Several gates write `gate_rounds` rows (`gate: decision-scout`,
  `gate: plan-critique`, `gate: wording-check`, and others). Only `plan-critique` rows are in
  scope. A fix commit can also carry a scout or wording-check fix (samples 17 and 33 name one in
  their subjects); evaluate only the critique round's items.
- T3 Confirming rounds and series. The planning skill dispatches one confirming round after a
  PROCEED that listed nits (planning SKILL.md:412). In this prompt a confirming round is a
  plan-critique row whose immediately preceding plan-critique row in the same plan file (a) is a
  PROCEED with a lower round number and at least one nit, (b) was added by the same PR, and (c) is
  not itself preceded by a PROCEED. A round that follows a PROCEED but fails (a)-(c) is not a
  confirming round: it is the opening round of an amendment PR reviewing new text (sample 30 is
  the opening round of amendment PR #1999 for `scout-input-fingerprint`), or a round run after the
  loop had ended; such rounds are in LATE when numbered 3 or later, else in CTRL. Round numbers can
  continue into an amendment PR or restart at 1; a row with a lower number than the row before it
  starts a new series.
- T4 Fix commit vs round. Each sample names the commit on the plan PR's branch whose subject says
  it answers critique round N (for example "address critique round 4", "critique r3 fixes"). Its
  diff is the fix; its parent holds the pre-fix plan text. The same commit also applies that
  round's nits; DD-A step 3 separates them. A PR can carry two plans (samples 4 and 15), and one
  commit can answer both plans' rounds (sample 4's commit edits both plans and both approved-text
  sidecars): evaluate only the changes to the sampled plan file and its own sidecar. If a
  commit plainly answers a different round than the sample says, evaluate the commit as given and
  record the mismatch in `meta.contract_notes`.
- T5 Squash merges. A plan PR's individual commits are not on `main`; `main` holds only its squash
  commit, whose subject ends `(#N)` but does not always name the slug (sample 9's is
  "plan: content reader edit hook and masked read-back (plan 2B) (#2238)"). The PR's commits live
  on the PR ref, which you fetch (Section 5 step 4).
- T6 Plan PR vs build. "Build" means the implementation commit(s) for a plan. Section 11 lists the
  build commits the composer found: commits on `main` after the plan PR, other than `plan(...)` or
  `plan:` commits, that change the plan's `implementation_declared` field or whose subject names
  the slug. Later `plan(...)` commits (amendments, closure declarations) are listed separately.
  Some builds have no PR number (a sha only); their PR ref cannot be fetched, so read the commit on
  `main`, and assume it went through PR CI like other builds (record "PR CI assumed" in the item's
  `evidence`). "none found" means neither test matched: the plan may be unbuilt, or its build used
  another route. For each such plan you may run ONE search,
  `git log --first-parent origin/main --oneline --grep "<rec id>"`, with the first rec id in the
  plan's `bundled_recommendations` (else the first rec id in its `intent` or `context`; if it names
  none, skip the search). A hit counts as the build only if it changes that plan's
  `implementation_declared` field or its message names the plan; otherwise the plan is unbuilt.
- T7 Three meanings of "block". In this prompt, blocking and nit are the critic's finding tags
  (plan-critique SKILL.md:156). They are not a red `--pre` check and not the decision-scout's BLOCK
  verdict (Decision 262).
- T8 Two meanings of "mechanical". The critic tags each finding mechanical or judgement
  (plan-critique SKILL.md:156), but those tags are not stored anywhere you can read. Q4's
  `mechanical` is your own judgement.
- T9 CI vs post-merge. "PR CI" is the `pr-validate` job of `.github/workflows/ci.yml` (the fast
  tier, `scripts.validate --pre`) plus any other workflow that runs on the PR. "Post-merge" is
  everything that runs after a merge: the `main-validate` job, scheduled jobs, live runs and
  post-deploy VP steps. `main-validate` runs the full tier only when the push is not plans-only; a
  plans-only push (such as a plan PR's squash merge) gets the fast tier (Section 10). A VP step's
  `phase` field is `pre-deploy` or `post-deploy` (`scripts/roadmap/plan_document.py:25`).
- T10 Two gates write `gate: plan-critique` rows. REPORT-ONLY plans run a Report Critique Gate
  (planning SKILL.md:418) whose rows also read `gate: plan-critique`, so the frame counts include
  a few such rows. All 40 sampled plans are IMPLEMENTATION plans; the rules in scope are the
  IMPLEMENTATION critique gate's (Section 10).

## 4. SCOPE

S1 (built, in use): the plan-critique gate's round loop. In scope: the blocking and nit tags and
the PROCEED/REVISE rule (plan-critique SKILL.md:156-158); the loop, confirming-round, escalation
and revisit rules (planning SKILL.md:412-414); and the consequence of those rules for the sampled
items, including whether a property the gate leaves to critic rounds could be owned by a script.

Context only (read to judge counterfactuals; never rated): the build skill
(`.claude/skills/implement/SKILL.md`), PR CI and its checks, the plan lint
(`scripts/roadmap/plan_obligations.py`), the plan schema (`scripts/roadmap/plan_document.py`), the
post-merge controls, and the sampled plans and builds themselves. An S1 finding may propose a
change whose home is one of these (for example a new plan-lint check that would own a property
critics now own). A defect IN one of these surfaces (a bug in a check, in the build skill, or in a
sampled plan's code) is outside S1 and goes to Q7, never `findings[]`.

Vocabulary:
- item: the one blocking finding of a sampled round that DD-A step 3 selects. Item id
  `I<sample number, two digits>`, e.g. `I07`.
- late group: items from samples 1-30 (CONF and LATE). control group: samples 31-40 (CTRL).
- control: any mechanism that would surface an item after the critique gate (Q1 lists them).
- core mattered set: items whose counterfactual (Q1) is `shipped_defect`, `caught_post_merge` or
  `needs_operator`. extended mattered set: the core set plus items whose counterfactual is
  `shipped_guard_gap` or whose `catch_route` is `plan_amendment`. "Mattered" alone means core.
- candidate rules R0-R3 and your optional RX: Q3.

Out of scope, one line each: whether critics run in a fresh context; whether a critique checklist
check verifies the right thing (whether its failure should block, and whether the plan detail it
asks for pays off in the build, ARE in scope); the decision-scout gate and its BLOCK rule
(Decision 262); the wording check and the approved-text process (Decision 246); moving critique
onto GitHub Actions personas (Decision 229); cost of any kind.

Trust nothing quoted here: obtain every file, line, count and size by reading the repository --
re-derive from the repo and record any non-resolving anchor in `meta.stale_anchors`.

## 5. SETUP

Run these first. NEVER abort on a failure -- take the named degraded path.

0. Hook canary (repo rule, AGENTS.md; it applies to a top-level interactive session, which is how
   this prompt is meant to run): if your opening context has no `[hook_canary]` line from this
   repo's hooks, say "repo hooks not loaded" as your first status, write nothing, and end the turn
   -- the operator resolves it. This is the one sanctioned stop in this prompt.
1. Record your current branch name (`git branch --show-current`; the harness-assigned session
   branch, needed by Section 16 step 4). `git fetch origin main` then
   `git rev-parse --short origin/main`. This sha IS the audited tree; use it in the deliverable
   filenames, the branch name and `meta.audited_commit`. Then
   `git switch -c audit/plan-critique-late-findings-<sha> origin/main` now, so everything you read
   is the audited tree. IF that branch already exists (locally per `git branch --list`, or on the
   remote per `git ls-remote --heads origin <name>`), append `-r2` (then `-r3`, ...) to the branch
   name only; the deliverable filenames keep `<sha>`. IF the switch is refused (for example by a
   hook), stay on the session branch, read files at the audited sha with `git show <sha>:<path>`
   where the session branch differs from it, and record "audited on the session branch" in
   `meta.contract_notes`. IF the fetch fails: use `git rev-parse --short HEAD`, branch off `HEAD`
   instead, and record "base is local HEAD" in `meta.contract_notes`.
2. `bin/venv-python -m scripts.session.preflight --roadmap-detail full` -- writes
   `logs/.preflight-report.json` (gitignored; expected), which gives roadmap item statuses (open,
   in progress, blocked) for Section 13. Criteria text and `candidate_decisions[]` are read from
   `docs/ROADMAP-PLATFORM.yaml` itself. Recommendations have no local cache in this repo; rec dedup
   uses the live reader (step 3). IF this fails: record the error in `meta.contract_notes`, take
   statuses from `docs/ROADMAP-PLATFORM.yaml` too, and continue.
3. Rec dedup probe -- run it whatever step 2 did:
   `bin/venv-python -m scripts.recs_related .claude/skills/plan-critique/SKILL.md --terms "blocking nit confirming round" --limit 5 --table`.
   IF this fails: do NOT abort -- set `meta.degraded_dedup = true`, set every finding whose
   classification rests on a rec search to `confidence: HYPOTHESIS` with
   `roadmap_crossref.dedup_hit_count: null`, dedup against `docs/ROADMAP-PLATFORM.yaml` and
   `docs/DECISIONS.md` only, and proceed.
4. PR refs. For every PR number in Section 11's plan-PR column and in its Q6 list, run
   `git fetch origin pull/<N>/head:refs/remotes/pr/<N>` (one PR per command; at most 60 fetches in
   total, retries included), then confirm each sampled fix commit with `git cat-file -t <fix sha>`
   (expect `commit`). IF a fetch fails, retry it once; IF it still fails, or a fix commit is not
   found: append the PR number to `meta.pr_refs_unavailable`, and judge that sample's item from its
   `gate_rounds` row, the merged plan and the code, with `item_source: unavailable` and
   `confidence: HYPOTHESIS`. For a Q6 build PR whose ref cannot be fetched, record what the commit
   on `main` shows and mark the row `confidence: HYPOTHESIS`.
5. No web access is needed or permitted for this audit.

Repo-wide `validate` is advisory outside CI; running it is optional and never a gate (Section 16).
You may use read-only search subagents to locate code; every counterfactual verdict, rule
application and rating is yours, made in this session.

## 6. NORTH STAR

Principles every rating references. NS1, NS2, NS4 and NS5 are judgment-bearing: a bar you judge
each item and the surface against, not a checklist to pattern-match. NS3 is a design principle.

- NS1 Blocking means consequence: a finding should block a plan when leaving it unfixed would let
  a wrong outcome reach `main` undetected, or when only the operator may settle it; otherwise it
  can travel to the build as a note.
- NS2 Best-placed control: each class of error is caught by the control that has the context to
  see it and that would fail if the error were present, as early as that control can see it.
- NS3 Deterministic before judgement: a property a script can check (presence, arithmetic, path
  existence, cross-reference resolution) is checked by a script before an LLM reads the plan.
- NS4 Convergence: later review rounds yield materially fewer consequential items than earlier
  rounds, and the loop stops when further rounds no longer pay in consequence.
- NS5 Detail earns its place: plan-level detail is justified by the build rework or defects it
  prevents.

## 7. THE QUESTIONS

Every answer cites item ids. Item-level records live in `sample_items`; question answers point at
them.

Q1 Counterfactual per item. Suppose only the item's change was withheld (every other change the
plan received is kept) and the plan merged that way. Which outcome follows? Per-item
`counterfactual`:
- `needs_operator`: settling the item required a fresh operator act, shown by evidence in the fix
  itself: the fix changes the approved-text sidecar (`docs/plans/approved/`) or Decision text; or
  it changes the plan's scope, tier or approach where the plan records that choice as the
  operator's (an `escalation_choice` on a `gate_rounds` row, or a `context` line naming an operator
  answer); or it touches an item of `docs/contracts/coordinator.yaml` `always_ask.items` (line
  161: IAM and security, spend, gated wording, public-surface files, manual or admin steps,
  non-CI deploys, loosened tests, secret-publishing runs). An edit inside already-approved text
  that needs no new approval is not `needs_operator`. Without such evidence, trace the stages
  below instead. This value takes precedence over all below.
- Otherwise find the control that would fail EARLIEST, in this stage order: (1) plan PR CI;
  (2) the build agent, following `.claude/skills/implement/SKILL.md`, before it opens its PR
  (including its own run of the plan's pre-deploy VP steps); (3) build PR CI (including
  `validate_vp_replay`); (4) post-merge (T9). Label it by what fails:
  `caught_by_vp` = a pre-deploy VP step fails, at stage 2 or 3 (when a VP step and another part of
  the build agent's work would both fail at stage 2, the VP step wins);
  `caught_by_ci` = any other CI check or job fails, at stage 1 or 3;
  `caught_by_build` = the build agent's own work fails or stops at stage 2 (its tests, its pytest
  loop, code that cannot work, a deviation-trigger STOP);
  `caught_post_merge` = the first failure is at stage 4 (including a post-deploy VP step).
- Otherwise: `shipped_defect` = a wrong or missing behaviour, guarantee or governed text reaches
  `main` in a delivered artifact (code, configuration, workflow, contract, Decision, or other text a
  consumer reads) or a deploy, and no control surfaces it -- the plan file itself counts only
  where something other than the build reads it (a check, a later plan, an operator decision);
  `shipped_guard_gap` = the behaviour on `main` is correct, but a regression guard the item asked
  for (a test case, assertion or check) is missing and nothing else guards that behaviour;
  `no_effect` = the delivered outcome is the same in every respect a consumer relies on.
- `undeterminable`: the evidence supports none of the above; name the missing evidence.

Pinned rules for Q1:
- Property match: a control counts only if it exercises the property the item concerns AND would
  fail if the item were left unfixed -- apply the counterfactual to the control (would that check,
  test or step go red on the pre-fix text?). A control that merely runs nearby is not a catch.
- The build commit: where Section 11 lists several, use the one that implements what the item
  concerns (the first that touches the item's subject; the first listed if unclear). Read its code
  for the trace.
- Controls as they stood: stage 1 as at the fix commit's tree; stages 2 and 3 as at the parent of
  that build commit (`<build sha>^`); stage 4 as at the build commit; for an unbuilt plan, as at
  the audited base. The VP and test obligations are as in the plan without the item's change.
- Later rewrites: when a later change edited, superseded or depends on the text the item's fix
  touched, apply the counterfactual to the merged text by removing the item's semantic content
  (what its fix added, or the correction it made) from it. If that content cannot be separated from
  later changes, the counterfactual is `undeterminable`; say why.
- Build agent: credit it with a catch only when a step it must run (a planned test, a VP command,
  its pytest loop, a scope check) would fail, or the code it must write cannot work without
  resolving the item. Do not credit it with noticing an omission nothing leads it to.
- Unbuilt plans (no build after the T6 search): judge from the plan and the code at the audited
  base, set `build_merged: no`, and cap the item's confidence at HYPOTHESIS unless the trace needs
  no build.
Also record per item:
- `catch_route`: `none` (no catch: `no_effect`, `needs_operator`, `shipped_defect`,
  `shipped_guard_gap`, `undeterminable`) | `in_build_fix` (fixed inside the build PR
  with no plan change) | `plan_amendment` (the catch forces a plan amendment PR: a scope or
  contract deviation, implement SKILL.md deviation trigger (b)) | `forward_fix_on_main`.
- `note_would_suffice`: had the item travelled as a one-line build note instead of a plan fix,
  would a build agent following the note avoid the consequence? `yes | no | unclear | n/a` (`n/a`
  for `no_effect`).
Question-level verdict, over the late group: the share of late items in the core mattered set --
`none` (0) | `few` (above 0, under 15%) | `some` (15% to 40%) | `many` (above 40%) |
`insufficient_evidence` (counting every `undeterminable` late item as mattered, and then as not
mattered, gives different bands). The prose also gives the extended-set share.

Q2 Late vs control. Do late items differ from round 1-2 items in consequence if unfixed? For each
group report items, core-mattered items, extended-mattered items, undeterminable items, and a 95%
interval for the core-mattered share (exact binomial or Wilson; name it; undeterminable items
count as not mattered). Verdict: `late_lower_value | similar_value | late_higher_value |
insufficient_evidence`. Choose `insufficient_evidence` when either group's point estimate lies
inside the other group's 95% interval and the two estimates differ by more than 10 percentage
points (a difference the sample cannot confirm); `similar_value` when they differ by 10 points or
less; otherwise the direction. Say what group size would settle an `insufficient_evidence` call.

Q3 Candidate blocking rules. Rules, listed in no order:
- R0 (today's rule): blocking = correctness, safety, scope, a Decision conflict or a failed
  checklist check; when unsure, blocking.
- R1: an item blocks only if the build would ship wrong behaviour.
- R2: an item blocks only if the build would ship a wrong outcome that CI and the verification
  plan would not catch.
- R3: an item blocks only if it is in this closed list: (i) the approach is wrong or conflicts
  with a Decision; (ii) the scope cannot deliver the intent; (iii) an acceptance criterion has no
  falsifiable verification; (iv) a safety or never-weaken invariant is at stake; (v) it needs an
  operator decision.
- RX (optional): a better rule you propose, in at most two sentences, applied the same way.
Apply R1, R2, R3 and RX to every item EX ANTE: as a careful critic reading the pre-fix plan and
the repository of that time would apply the rule, without hindsight from your Q1 trace. Record
`rule_blocks` per item. R0 blocks every sampled item by construction (each was tagged blocking);
do not re-apply it -- it is the baseline. A demoted item is judged in two worlds: the Q1 world (the
item is dropped; this is the worst case, since today the planner fixes nits too, planning
SKILL.md:412) and the note world (`note_would_suffice`). For each rule and each group count: items
blocked, core-mattered blocked, core-mattered demoted, extended-only items demoted (guard gaps and
plan amendments), undeterminable items demoted, other items demoted, and precision (core-mattered
blocked / blocked), plus `core_tested` (the core-mattered items in the group, which is what "safe"
was tested against) and `demoted_upper_95` (the one-sided 95% upper bound on the core-mattered
share among the items the rule demotes, from core demoted out of all demoted). Per-rule verdict, late group: `no_change` if it demotes no
item; else `unsafe` if it demotes at least one core-mattered item whose counterfactual is
CONFIRMED; else `insufficient_evidence` if it demotes a core-mattered HYPOTHESIS item or an
undeterminable item; else `safe`. Also record `safe_extended` (`yes` if it
demotes no extended-mattered item, else `no`) and, for an `unsafe` rule, `safe_with_notes` (`yes`
if every core-mattered item it demotes has `note_would_suffice: yes`). Question-level verdict:
`safe_rule_found` if any of R1-R3 or RX is `safe` for the late group; else `insufficient_evidence`
if any is `insufficient_evidence`; else `no_safe_rule`. The prose names the rule(s), and any rule
that is safe only for a subset of rounds (for example from round 2, or from round 3).

Q4 Deterministic checks. For every item, set `mechanical: true` when a deterministic check could
have caught it from the plan text plus repository state before a critic ran, and name that check.
Group mechanical items, across both groups, by `finding_type`; for every type with at least 2
mechanical items, record in `deterministic_checks`: what the check asserts, its home (an existing
check to extend, named by path, or a new module under `scripts/checks/<domain>/`, or the plan lint
`scripts/roadmap/plan_obligations.py` with `docs/contracts/plan-obligations.yaml`), whether
something already checks it (`already_exists: yes | partial | no`, naming the check), and the items
it would have caught. Anti-vacuity: for each check, state that it would be red on the pre-fix text
and green on the post-fix text of every item it lists, and whether it would stay silent on the
other sampled plans' merged text (`red_green: yes | partial | no`, with a line of evidence).
Verdict: `some_recurring | none_recurring`.

Q5 Confirming rounds and rounds after 5. This is the planning skill's own pending revisit question
(planning SKILL.md:414: "did a confirming round ever find a blocking finding, and were findings
after round 5 real?"). Q5a: among the 8 CONF items, did any have a core-mattered counterfactual?
Verdict: `yes` (at least one, CONFIRMED) | `no` (none, and every CONF item's counterfactual is
CONFIRMED) | `insufficient_evidence` (otherwise). State what the result can and cannot rule out:
with 8 rounds and none mattered, the one-sided 95% upper bound on the true share is about 31%.
The prose also gives the frame-level fact from C2 (how often confirming rounds return REVISE).
Q5b, in prose and in `q5b`: among items from rounds numbered 6 or higher (samples 1, 8, 11, 12,
19, 23 and 25), report the counterfactual distribution and how many were real (a counterfactual
other than `no_effect` and `undeterminable`).

Q6 Plan detail and build rework (DD-B). Does plan-level detail prevent build rework? Verdict:
`detail_prevents_rework | detail_causes_rework | no_measurable_effect | insufficient_evidence`,
with a `confidence`. Every sampled plan is detailed, so there is no thin-plan contrast group:
`detail_prevents_rework` can be argued from the evidence but not measured; its confidence is
HYPOTHESIS.

Q7 Questions the operator did not think to ask. Answer these, then add your own:
- Recall: while doing DD-A step 3, glance at each sampled round's nit changes (no full trace) and
  list any that look consequential; trace at most 5 of them in total. Did any nit-tagged change
  carry a core-mattered consequence? Are the nit fixes applied after a confirming round, which
  are "applied, not re-read" (planning SKILL.md:412), ever a source of defects in the sample?
- Does the escalation rule (planning SKILL.md:414) ever fire? Count the `escalated: true` rows
  (`scripts/roadmap/plan_gate_rounds.py:55`) among plan-critique rows in the sampled plans, and
  name the longest series with none.
- Can the gate's own yield be measured from what the repository stores (planner-written counts and
  optional paraphrases, no critic text), and what is the smallest record that would make the next
  audit of this kind cheaper and firmer?
- Do late items cluster on particular plan fields (`verification_plan`, `test_obligations`,
  `scope`, `rollback`, `context`, the approved-text sidecar), and does that differ from the control
  group?
- If, while tracing, you saw a defect in a sampled plan's merged code or plan that no round flagged,
  or a defect in a context-only surface, record it here (no extra search).

## 8. RUBRIC

Rate S1 on each dimension: `strong | adequate | weak | absent | n/a`. `n/a` is correct and costless
where a dimension does not apply; never manufacture a rating or a finding to fill a cell.

- VD1 Late-round yield (NS1; Q1, Q5): the blocking items of late rounds are consequential (core
  mattered set) rather than caught later or without effect.
- VD2 Downstream fit (NS2; Q1, Q2): the items the gate blocks are ones that no later
  property-matched control would catch.
- VD3 Blocking-rule precision (NS1; Q3, C5): the rule as written separates items that matter from
  items that can travel as notes.
- VD4 Deterministic ownership (NS3; Q4): properties a script can check are owned by scripts, not
  left to critic rounds.
- VD5 Convergence (NS4; Q5, C1, C2): later rounds yield materially fewer consequential items than
  earlier rounds, and the stopping rule ends the loop when they no longer do.
- VD6 Detail value (NS5; Q6, C6): the build-level detail the gate asks plans to carry is detail
  builds use and that prevents rework. The evidence is asymmetric: rework caused by wrong detail
  can be observed, rework prevented by detail can only be argued (Q6); weigh it accordingly.

## 9. DEEP-DIVES

DD-A Per-item counterfactual trace (feeds Q1-Q5, Q7, C1-C5). Process samples in this order, so
that a truncated run keeps every group represented: 1-8, then 9, 10, 31, 11, 12, 32, 13, 14, 33,
15, 16, 34, 17, 18, 35, 19, 20, 36, 21, 22, 37, 23, 24, 38, 25, 26, 39, 27, 28, 40, 29, 30. For each
sample:
1. Row. In `docs/plans/PLAN-<slug>.yaml` at the audited base, find the `gate: plan-critique` row
   with the sample's round number in the right series (T3). Record its blocking and nits counts and
   any trailing comment (`row_blocking`, `row_nits`, `row_comment`). If the row differs from
   Section 11 (round, verdict or blocking count), use the repository row and record the difference
   in `meta.contract_notes`.
2. Fix. `git log -1 --format=%B <fix sha>`; `git show --stat <fix sha>`;
   `git show <fix sha> -- docs/plans/` (this includes any approved-text sidecar under
   `docs/plans/approved/`). Pre-fix plan: `git show <fix sha>^:docs/plans/PLAN-<slug>.yaml`.
3. Item. List the round's blocking items, n of them, from the first source that names them, then
   evaluate exactly one: item number k = ((sample number - 1) mod n) + 1.
   (a) The row's trailing comment names B-items: they are the items in B order ->
       `item_source: gate_rounds_annotation`.
   (b) Else, the commit subject or body names the round's changes and separates blocking ones from
       nits, or the round's diff (after the exclusions in (c)) holds a single change ->
       `commit_message`.
   (c) Else, the diff -> `diff_inferred`. A change is one edited plan row: the top-level key plus
       its list index where the key holds a list (e.g. `verification_plan[3]`, `scope[2]`,
       `rollback`); several edited subkeys of one row are one change. An edit to the approved-text
       sidecar is one change per top-level key of the sidecar file. Exclude edits to `gate_rounds`,
       and any change the commit body or a decision-scout or wording-check `gate_rounds` row added
       in the same commit attributes to another gate. Tag the remaining changes by plan-critique
       SKILL.md:156: a change touching only wording, citation form or ordering answers a nit; the
       rest are blocking candidates. The blocking candidates in diff order are the items.
   n is the number of items the source yields, even when it differs from the row's blocking count
   (record both; note the difference in `item_note`). If the source yields none, set
   `item_source: unavailable`, `item_number: 0`, `finding_type: other`, `plan_field: ""`,
   describe the round's whole change, and continue. Describe the item in at most 30 words of your
   own. Note any consequential-looking nit change for Q7.
4. `finding_type` (pinned): `test_case_or_fixture | test_node_or_obligation_row | vp_step |
   line_pin_or_size_arithmetic | scope_row | approach_or_design | decision_conflict |
   invariant_or_safety | definition_or_wording | procedural_bookkeeping | rollback | approved_text
   | other`. `procedural_bookkeeping` covers rec closure, successor recs and waivers.
5. Counterfactual (Q1): read what the change touched, the plan fields that consume it, the code
   the build wrote (`git show --stat <build sha>` and the relevant files at that commit) and the
   candidate controls. Record `counterfactual`, `catching_control` (file:line, check name or
   workflow job; empty when none), `control_property_match`, `catch_route`, `note_would_suffice`.
6. Origin (C3): one
   `git log -S"<distinctive pre-fix text>" --oneline refs/remotes/pr/<N> -- docs/plans/PLAN-<slug>.yaml`
   search, where the pre-fix text is the faulty text the fix removed or changed; for an omission
   (the fix only added text), use the distinctive text of the row the addition completes.
   `fix_induced: yes` when a commit on the PR before the fix commit (a fix for any gate)
   introduced that text; `no` when it was in the PR's first commit or, for an amendment PR,
   already on `main` before it; `unclear` when there is no distinctive text or the search is
   inconclusive.
7. Rules and checks: `rule_blocks` (Q3), `mechanical` and `deterministic_check` (Q4).
8. Write the row to the YAML deliverable now. The file is your working memory across any context
   compaction; re-read it rather than re-deriving finished rows.
Effort bound: spend at most about 12 tool calls on any item; when an item exceeds that, record what
you have with `confidence: HYPOTHESIS` and move on. Grep for anchors rather than reading whole
large files. IF you judge you cannot finish (context or tool budget), finish the current sample,
list the unprocessed sample numbers in `meta.unprocessed_samples`, skip DD-B (Q6
`insufficient_evidence`), and answer every question from the processed samples, saying so.

DD-B Build rework (feeds Q6, C6). For the 10 build PRs in Section 11's Q6 list -- do NOT exceed:
1. The PR's own commits:
   `git log --oneline --no-merges $(git merge-base refs/remotes/pr/<N> <build sha>^)..refs/remotes/pr/<N>`.
2. Classify each commit from its subject, body and diff, briefly: `initial` | `ci_fix` |
   `plan_conformance` (brings the code in line with the plan) | `plan_detail_wrong` (departs from a
   plan detail because it was wrong: a renamed test node, a changed fixture, a corrected VP
   command) | `plan_detail_absent` (fills something the plan left unspecified) | `review_fix` |
   `other`.
3. Plan amendments in the build window: `plan(<slug>)` commits on `main` between the plan PR's
   merge and the build's merge (Section 11 lists the later plan commits the composer found).
4. Detail conformance: take the plan as it stood when the build started
   (`git show <build sha>^:docs/plans/PLAN-<slug>.yaml`) and its first 10 `test_obligations` rows.
   A row carries either a `test_selector` or a `command`. For a selector containing `::`, the node
   name is its last `::` segment with any `[...]` suffix removed; check it at the build commit
   (`git grep -n "<name>" <build sha> -- tests/`): verbatim, renamed (same behaviour, other name;
   your judgement) or absent. Count rows with a `command` or a file-only selector as
   `nodes_not_named`.
5. Record one `build_rework` row per PR.

## 10. GROUNDING MAP

This map spends your cognition on judgment, not grep. Verify each anchor before relying on it:
anchors are as of the composing base and may have moved; a non-resolving anchor goes in
`meta.stale_anchors`. Facts carry no verdict.

S1 rule text:
- `.claude/skills/plan-critique/SKILL.md:156`: "blocking (correctness, safety, scope, a Decision
  conflict or failed check above; when unsure) or nit (wording, citation form, ordering; never a
  Frame Challenge contradiction); mechanical (a deterministic check could catch it -- name it) /
  critic judgement -- tag each finding, with a stable anchor (plan field path or VP step id)".
- `.claude/skills/plan-critique/SKILL.md:158`: PROCEED (no blocking finding; list nits) / REVISE
  (any blocking finding).
- `.claude/skills/plan-critique/SKILL.md:55, 57, 64, 75, 79`: checklist items 12c (VP executable
  command), 12c-1 (test-obligation adequacy), 12k (closure obligation), 12l (closes_criteria) and
  12n (size headroom), examples of checks whose failure the critic reports.
- `.claude/skills/planning/SKILL.md:395`: the planner dispatches the critic as a zero-context
  subagent.
- `.claude/skills/planning/SKILL.md:412`: every round is a full fresh read and no count caps them;
  REVISE means at least one blocking finding; the planner fixes every finding, records a
  disposition each, and runs `bin/venv-python -m scripts.validate --pre` before every re-dispatch;
  a PROCEED listing nits -> apply them and dispatch one fresh confirming round, which ends the loop
  unless it returns a blocking finding (its own nits are applied, not re-read); each round is
  written to `gate_rounds`.
- `.claude/skills/planning/SKILL.md:414`: escalation only on an oscillating finding, a disputed
  blocking finding, or a fix that would change approved scope, tier or a cited Decision; ends with
  the revisit clause quoted in Q5.
- `.claude/skills/planning/SKILL.md:382`: the planner runs the plan lint
  `bin/venv-python -m scripts.roadmap.plan_obligations --plan <plan>` before the critique gate.
- `scripts/roadmap/plan_gate_rounds.py:53-54` and `:69-70`: a row's `blocking` and `nits` are
  integer counts; a plan-critique REVISE carries at least one blocking finding and a PROCEED none.

Controls (context):
- `.claude/skills/implement/SKILL.md:72`: at most 3 fix attempts per step, then STOP.
  `:84-94`: deviation trigger (a) in-scope under-specification is decided inline, (b) a scope or
  contract deviation or an always-ask surface is a STOP, (c) repeated failure is a STOP; full
  detail at `docs/contracts/implement-scope-boundary.yaml:489` (`deviation_trigger:`).
- `.github/workflows/ci.yml:18` job `pr-validate`, whose step at `:84` runs
  `python -m scripts.validate --pre`; `:120` job `main-validate`, which classifies the push at
  `:133` (`scripts/ci/push_scope.py classify`, output `plans_only`), runs the full tier at
  `:183-185` only when `plans_only` is not true, and runs the fast tier at `:203-207` when it is;
  `:569` job `signal-green`.
- A check runs in the fast tier when its entry in `scripts/checks/<domain>/_manifest.py` has
  `pre=True` (optionally narrowed by `pre_globs`). Examples:
  `scripts/checks/verification/_manifest.py:174` `validate_vp_replay`, `:181`
  `validate_scope_boundary`; `scripts/checks/roadmap/_manifest.py:88` `validate_plan_documents`.
- `scripts/checks/verification/validate_vp_replay.py` (module docstring): replays a declared plan's
  hermetic pre-deploy VP steps green-after on the build PR (implement leg) and certain graduated
  steps red-before on the plan PR (plan-only leg); outcome rules in
  `docs/contracts/vp-red-before.yaml`.
- `scripts/roadmap/plan_obligations.py` (module docstring): the plan lint, deriving
  registration-closure obligations from `docs/contracts/plan-obligations.yaml`; its registered
  check is `scripts/checks/roadmap/validate_plan_scope_closure.py`.
- `scripts/roadmap/plan_document.py`: the PlanDocument schema; a plan's top-level fields include
  `scope`, `acceptance_criteria`, `verification_plan`, `test_obligations`, `rollback`,
  `implementation_declared` and `gate_rounds`; at `:138-160` a `test_obligations` row carries
  exactly one of `test_selector` or `command`.
- AGENTS.md "Merge protocol": a post-merge full-tier failure on `main` gets a `ci_rca` rec and a
  forward fix, never an auto-revert (Decision 226).

Dedup pointers (returned by `scripts.recs_related` at compose time, all open then):
- rec-5365 "Give the three review triggers written into skills a watched home, or retire them; the
  gate_rounds one passed at 154 plans": owns scheduling the planning SKILL.md:414 revisit, not its
  answer.
- rec-3080 "Design a gate-verdict recording surface for plan-critique PROCEED/REVISE outcomes": near
  any finding about recording critic output.
- rec-3976 "Plan-critique loops on a VP step that asserts a quantity the change under test cannot
  move; no gate catches subject-misattribution": near VP-related loop findings.
- rec-3885 "No gate validates that a plan's VP commands and test_obligation selectors actually
  assert what they claim -- three permanently-vacuous checks nearly merged": near Q4 VP and
  test-selector checks.
- Decision 87 "Plans, plan-critiques, and plan-revisions as first-class warehouse entities;
  authority-flip deferred to the autonomous producer (T4.x)": near Q7's smallest-record question.

## 11. EMPIRICAL PASS

Sample frame: the `gate: plan-critique` rows, at composing base `79a6cd09`, of the plan files that
a commit with committer date on or after 2026-11-09 had changed, as selected by
`git log --since=2026-11-09`, which git resolved at the composing time of day: 152 plan files,
1,039 rows. That cutoff fell after 00:03:31 UTC on 2026-11-09, so it leaves out
`PLAN-coordinator-harness-profile.yaml` (changed at that minute); a strict 00:00 UTC cutoff gives
153 files and 1,043 rows. Later bases have more rows as new plans land. Pools of REVISE rows: CONF (confirming rounds per T3; 32 rows),
LATE (round 3 or later and not CONF; 303 rows, of which 39 follow a PROCEED without being
confirming rounds) and CTRL (round 1 or 2 and not CONF; 264 rows). The composer drew 8, 22 and 10
rows at random within each pool, at most one row per plan, keeping only rows whose fix commit could
be isolated: a commit on the PR ref that touches the sampled plan file and whose subject names that
critique round alone (not a range such as "rounds 2-4" and not a PROCEED record). Rows that failed
that test were skipped (3 in CONF, 3 in LATE, 1 in CTRL), a possible selection bias toward plans
whose commits name one round at a time. The draw itself is not re-derivable; each row's
eligibility is. You may NOT widen or replace the sample. One item per round (DD-A step 3).

Columns: sample number | stratum | plan slug (file `docs/plans/PLAN-<slug>.yaml`) | round |
blocking count in the row | plan PR (A = an amendment PR) | fix commit | build commits per T6
(PR number and sha, or sha only).

```
 1 | CONF | actions-sha-pin-codeowners             | 6  | 1 | #2193   | 2cc38b9f87da | #2196 b2e60819, #2218 c39dcdeb
 2 | CONF | decision-census-rec-filing             | 3  | 1 | #2254   | 382fbcfe35da | #2258 eb04c62a
 3 | CONF | monitor-read-budget                    | 4  | 1 | #2329   | 4db98a61a113 | #2346 9bbbda01
 4 | CONF | merge-annotate-contract-reader         | 3  | 1 | #2150   | 68e4eb9ad798 | #2228 bc29d718
 5 | CONF | backlog-health-probe-reaps-descendants | 5  | 1 | #2168   | 6820b98f30e7 | #2189 9f4dce72
 6 | CONF | persona-image-python-line              | 3  | 2 | #2289   | 50bfd1fbd16a | #2306 ff94388a
 7 | CONF | computed-reversal-conditions           | 4  | 2 | #2200   | e9f4aea433d4 | #2249 65fbd17b
 8 | CONF | arrival-lost-window-author-date        | 12 | 1 | #1971   | b24cb07a20ae | #1973 c379bc46
 9 | LATE | telemetry-content-edit-hook            | 3  | 1 | #2238   | 4287cc1523b6 | none found
10 | LATE | persona-verdict-egress                 | 3  | 3 | #2187   | c17b2f85e61b | #2191 b12809af
11 | LATE | decision-delegation-and-cap-measure    | 7  | 1 | #2153   | ad65f42218b9 | #2198 619c7e99
12 | LATE | sit-restore-spike                      | 9  | 1 | #1940   | 996794bbbc10 | #1947 98e84cf0, #1950 ef631f28
13 | LATE | bootstrap-ci-gated-apply               | 4  | 3 | #2026   | 1eb17d0bb71a | #2033 f43a1624, #2037 0d51ede0
14 | LATE | telemetry-session-ref-stability        | 4  | 1 | #2328   | a66a488902be | #2335 9689890a
15 | LATE | sit-organization                       | 5  | 2 | #1968   | f415d93dbbee | #2296 ae53954b, #2310 4ba820b3
16 | LATE | weakening-dispositions-per-diff        | 3  | 2 | #2287   | cffe3620f4c7 | 05b2815d
17 | LATE | decision-chain-consolidation-rule      | 5  | 1 | #2260   | e0c8756a5129 | 5d5bd40c
18 | LATE | bootstrap-trusted-pr-plan              | 5  | 3 | #2235   | 9bf027918a14 | none found
19 | LATE | friction-signature-coverage            | 6  | 1 | #2248   | b054f590f295 | 24757464
20 | LATE | terraform-company-emails-closing       | 3  | 2 | #1859   | 7c3eb6fdc16d | none found
21 | LATE | telemetry-content-deploy-channel       | 4  | 1 | #2165   | 2bd4647cb10d | #2203 6bbd7f5c
22 | LATE | agent-proxy-signed-access              | 3  | 3 | #2022   | f549a9c7cd4a | none found
23 | LATE | actions-capacity-gauge                 | 9  | 1 | #1959   | 436eff815046 | #1974 9af7f997
24 | LATE | persona-image-pin                      | 4  | 1 | #2256   | d69597954054 | 2faa7054
25 | LATE | secret-read-exact-target               | 6  | 2 | #2007   | 0701fbdb1f7c | #2017 fdc9f86b
26 | LATE | actions-sha-pin-native-policy          | 3  | 2 | #2237   | 4ea02a92917c | 0befb87e
27 | LATE | friction-daily-detector                | 3  | 2 | #2352   | f43f0c7f22dc | none found
28 | LATE | run-persona-mcp-bridge                 | 3  | 1 | #2149   | 85cdc5027b10 | #2174 e644a11f
29 | LATE | telemetry-planning-cost-reads          | 3  | 1 | #2213   | 63c93937c727 | #2244 324d2fc7
30 | LATE | scout-input-fingerprint                | 4  | 1 | #1999 A | 01a1530dd9e8 | #2003 755944d2
31 | CTRL | graduation-rows-on-main                | 2  | 1 | #2291   | c75ecd15fd4e | 3e19f461
32 | CTRL | capture-pin-live-main-base             | 1  | 2 | #2117   | b93ebffc3178 | #2143 1be31e65
33 | CTRL | litellm-call-record                    | 1  | 4 | #2154   | fb5db833188f | #2175 b7210a3e
34 | CTRL | rec-writer-session-tripwire            | 2  | 2 | #2141   | 9c12761bdec2 | #2157 f9a50132
35 | CTRL | sensor-liveness-delivery-floor         | 2  | 2 | #1983   | d31f2b140c74 | #1995 a72dd152
36 | CTRL | persona-check-origin                   | 1  | 3 | #2115   | ca598d762dd9 | #2144 bc0f0714
37 | CTRL | plugin-hooks-locate-checkout           | 1  | 3 | #1954   | 60d13256abf3 | #1955 6d595bb0, #1956 33fe369d
38 | CTRL | persona-verdict-app-grant              | 1  | 8 | #2207   | 8fc146b64ae8 | #2214 1658ce1e, #2221 9b549344, #2239 37d66d4d, #2273 589f2e30
39 | CTRL | telemetry-hook-block-markers           | 1  | 2 | #2230   | 5d208935407d | none found
40 | CTRL | bridge-teardown-end-record             | 1  | 1 | #2294   | fca6a7ac1397 | #2304 c05772ba
```

Later `plan(<slug>)` commits on `main` that touch the sampled plan after its plan PR (amendments
and follow-ups), for DD-B step 3 and for context: sample 1 #2210; 3 #2339; 10 #2347; 11 #2205,
#2211; 12 #1951; 13 #2029; 15 #2055, #2069, #2226, #2240, #2272; 20 #2265; 21 #2190, #2195;
22 #2027; 24 #2280, #2299, #2325; 26 #2259; 38 #2231, #2290.

Q6 list (DD-B): 10 samples drawn at random from the 28 whose first build has a PR number, using
that first build -- do NOT exceed: sample 4 #2228 (bc29d718), 5 #2189 (9f4dce72), 8 #1973
(c379bc46), 10 #2191 (b12809af), 11 #2198 (619c7e99), 13 #2033 (f43a1624), 25 #2017 (fdc9f86b),
30 #2003 (755944d2), 37 #1955 (6d595bb0), 40 #2304 (c05772ba). The sha in brackets is the build
commit on `main`.

Tag every finding `evidence_kind: observed` when it rests on traced sample items, `static` when it
rests on skill text alone; at equal severity, observed outranks static. Do NOT call a model API,
run a critic or re-run a plan's VP (read-only search subagents per Section 5 are allowed).

## 12. METHOD

- P1 Read: the Section 10 anchors in context (plan-critique SKILL.md around :156, planning
  SKILL.md :395-416, implement SKILL.md :60-94, ci.yml's three jobs).
- P2 DD-A for all 40 samples in the order DD-A gives, writing each row as you go.
- P3 DD-B for the Q6 list.
- P4 Tally: Q3 per-rule counts, Q4 grouping, Q2 and Q5 statistics.
- P5 Adjudicate C1-C6.
- P6 Rate VD1-VD6.
- P7 Write findings; dedup each (Section 13).
- P8 Synthesize: answer Q1-Q7, then assign severity and compute maturity LAST (Section 15); write
  the report.

## 13. DEDUP DISCIPLINE

Before filing any finding, search the ownership surfaces: the roadmap (`tier_items[]` criteria text
and `candidate_decisions[]` in `docs/ROADMAP-PLATFORM.yaml`, with statuses from step 2's report),
`docs/DECISIONS.md` headers and clauses, and open recs
(`bin/venv-python -m scripts.recs_related <file paths> --terms "<terms>" --table`, paging with
`--exclude` -- it takes the comma-joined rec ids the previous page printed as `next_exclude` -- at
most 3 pages per finding). Record `dedup_search_terms` and `dedup_hit_count` on the finding. A
"hit" is a returned item whose text you judge to OWN the defect (its acceptance or criterion would
fix it), not merely any returned row -- `recs_related` always returns its nearest matches; record
the raw returned total in `roadmap_crossref.note`. `dedup_hit_count` counts owning hits only.
Classification:
- `novel`: no owning hit; requires a recorded negative search (`dedup_hit_count: 0` on the terms
  you list), else the finding is HYPOTHESIS.
- `planned-unbuilt`: an owning item exists whose remedy would fix the defect, but it is not built.
- `planned-insufficient`: an owning item exists but its remedy would not fully fix the defect
  (say what it misses).
- An owning item that is built and fully fixes the defect means no finding: write a
  `rejected_candidates` row instead.

DO NOT FLAG (deliberate constraints; you may assess their blocking consequence where Section 4
says so, never whether they should exist):
- Fresh-context critics (planning SKILL.md:395; a skill rule, no Decision).
- The approved-text process and its operator approval on GitHub (Decision 246).
- A plan PR whose last decision-scout row is BLOCK fails its check (Decision 262).
- Persona-first migration of gates onto GitHub Actions (Decision 229).
- Verification weakening is human-gated (Decision 235).
- Whether a critique checklist check verifies the right thing (12c, 12c-1, 12k and the rest; skill
  text, no Decision). Whether a failed check should block, and whether the plan detail a check asks
  for pays off in the build (Q6, VD6), ARE in scope, as Section 4 says: a finding may propose that
  a class of detail stop blocking or move to the build.
- The public-repository content boundary (Decision 101; AGENTS.md).

## 14. OUTPUT

Write `audits/plan-critique-late-findings-<sha>.yaml`:

```
audit:
  meta: {audited_commit: <sha>, base_branch: main, model: <your self-reported name>,
         methodology_version: 1, scope_surfaces: [S1],
         degraded_dedup: false, pr_refs_unavailable: [], unprocessed_samples: [],
         contract_notes: "", stale_anchors: []}
  question_answers:
    - {q: Q1, verdict: none|few|some|many|insufficient_evidence, basis: [<item or finding ids>], prose: ""}
    - {q: Q2, verdict: late_lower_value|similar_value|late_higher_value|insufficient_evidence,
       basis: [], prose: "", interval_method: exact_binomial|wilson,
       groups: {late: {items: <int>, core_mattered: <int>, extended_mattered: <int>, undeterminable: <int>, interval_95: ""},
                control: {items: <int>, core_mattered: <int>, extended_mattered: <int>, undeterminable: <int>, interval_95: ""}}}
    - {q: Q3, verdict: safe_rule_found|no_safe_rule|insufficient_evidence, basis: [], prose: "see blocking_rules"}
    - {q: Q4, verdict: some_recurring|none_recurring, basis: [], prose: "see deterministic_checks"}
    - {q: Q5, verdict: yes|no|insufficient_evidence, basis: [], prose: "",
       q5b: {items: <int>, real: <int>, core_mattered: <int>, distribution: {<counterfactual value>: <int>}}}
    - {q: Q6, verdict: detail_prevents_rework|detail_causes_rework|no_measurable_effect|insufficient_evidence,
       confidence: CONFIRMED|HYPOTHESIS, basis: [], prose: "see build_rework"}
    - {q: Q7, answers: [{question: "", answer: "", basis: []}]}
  per_surface_assessment:
    - {surface: S1, maturity: frontier|strong|solid|nascent, strengths: "", top_gaps: [<finding ids>]}
  rubric_ratings:
    - {surface: S1, dimension: VD1..VD6, rating: strong|adequate|weak|absent|n/a,
       evidence: "file:line|item ids, or empty for n/a", note: ""}
  candidate_adjudications:
    - {candidate: C1..C6, verdict: side_a|side_b|mixed|insufficient_evidence, basis: [<item ids>], prose: ""}
  sample_items:
    - {item_id: I01, sample: 1, stratum: CONF|LATE|CTRL, plan: <slug>, round: <int>, plan_pr: <int>,
       fix_commit: <sha>, row_blocking: <int>, row_nits: <int>, row_comment: "",
       blocking_items_found: <int n>, item_number: <int k, 0 if unavailable>, item_note: "",
       item_source: gate_rounds_annotation|commit_message|diff_inferred|unavailable,
       item: "<= 30 words", plan_field: "<field path the fix touched>",
       finding_type: <DD-A step 4 enum>,
       counterfactual: needs_operator|caught_by_ci|caught_by_build|caught_by_vp|caught_post_merge|shipped_defect|shipped_guard_gap|no_effect|undeterminable,
       catching_control: "", control_property_match: "",
       catch_route: none|in_build_fix|plan_amendment|forward_fix_on_main,
       note_would_suffice: yes|no|unclear|n/a, fix_induced: yes|no|unclear,
       rule_blocks: {R1: yes|no, R2: yes|no, R3: yes|no, RX: yes|no|n/a},
       mechanical: true|false, deterministic_check: "<what it asserts and its home, or empty>",
       build_merged: yes|no, evidence: "<file:line, sha or command>", confidence: CONFIRMED|HYPOTHESIS}
  blocking_rules:
    rx_text: "<your RX rule, or empty>"
    rules:
      - {rule: R0|R1|R2|R3|RX, group: late|control, blocked: <int>, core_blocked: <int>,
         core_demoted: <int>, extended_only_demoted: <int>, undeterminable_demoted: <int>,
         other_demoted: <int>, precision: "<core_blocked>/<blocked>", core_tested: <int>,
         demoted_upper_95: "<percent>",
         verdict: safe|unsafe|insufficient_evidence|no_change|baseline, safe_extended: yes|no|n/a,
         safe_with_notes: yes|no|n/a, rationale: "", confidence: CONFIRMED|HYPOTHESIS}
  deterministic_checks:
    - {finding_type: "", asserts: "", home: "<path>", already_exists: yes|partial|no,
       existing_check: "", items: [<item ids>], red_green: yes|partial|no, note: ""}
  build_rework:
    - {sample: <int>, plan: <slug>, build_pr: <int>, build_commit: <sha>, commits: <int>,
       by_class: {initial: <int>, ci_fix: <int>, plan_conformance: <int>, plan_detail_wrong: <int>,
                  plan_detail_absent: <int>, review_fix: <int>, other: <int>},
       amendments_in_window: [<PR numbers>], rows_checked: <int>, nodes_verbatim: <int>,
       nodes_renamed: <int>, nodes_absent: <int>, nodes_not_named: <int>, note: "",
       confidence: CONFIRMED|HYPOTHESIS}
  findings:
    - {id: PCL-01, origin: C1..C6|executor, surface: S1, question: <primary Q1..Q7>,
       dimension: <primary VD1..VD6>, title, evidence: "file:line|item ids|sha",
       evidence_kind: static|observed, current_behavior, ideal_behavior, gap,
       compensating_controls_considered: "",
       change_type: add|rescope|enforce|unify|persist|clarify|retune_gate,
       proposed_change: "", acceptance: "", severity: critical|high|medium|low,
       severity_rationale, confidence: CONFIRMED|HYPOTHESIS,
       roadmap_crossref: {classification: novel|planned-insufficient|planned-unbuilt,
                          item_ids: [], dedup_search_terms: [], dedup_hit_count: <int|null>, note: ""},
       effort: XS|S|M|L, depends_on: [finding ids],
       sequencing: {safe_to_queue_now: true|false, blocked_behind: [finding or roadmap ids], note: ""}}
  rejected_candidates:
    - {candidate: "C1..C6, or a short label for a hypothesis you raised", why_dismissed,
       compensating_control, control_property_match, decision_or_item_id}
  summary: {total_findings, novel_count, planned_insufficient_count, planned_unbuilt_count,
            top_improvements: [finding ids], highest_leverage_change: <finding id or null>,
            maturity_S1: <value>, items_total: <int>, items_late: <int>, items_control: <int>,
            late_core_mattered: <int>, control_core_mattered: <int>,
            late_undeterminable: <int>, control_undeterminable: <int>}
```

Rules for the contract:
- `blocking_rules.rules` has one row per rule per group. R0's rows use `verdict: baseline` and
  `n/a` for `safe_extended` and `safe_with_notes`. RX rows appear only if you proposed RX.
  `safe_with_notes` is `n/a` unless the verdict is `unsafe`. The control-group rows carry the same
  verdict rule applied to the control group.
- `origin: executor` marks a finding you raised yourself; `classification: novel` is a dedup
  result (Section 13). The two are independent.
- `change_type`: `add` = a new rule, check or record; `rescope` = change what a rule covers;
  `enforce` = make an existing rule mechanically enforced; `unify` = merge duplicate rules;
  `persist` = store something now lost; `clarify` = reword without changing behaviour;
  `retune_gate` = change what blocks or when the loop stops.
- effort scale: XS = a one-line or config change; S = a small plan; M = one standard plan; L = one
  large plan near the split threshold.
- COUNTING INVARIANT: `findings[]` is the SOLE enumerated list of defects; `total_findings =
  len(findings) = novel_count + planned_insufficient_count + planned_unbuilt_count`; fully covered
  candidates live in `rejected_candidates`, never in findings; `sample_items`, `blocking_rules`,
  `deterministic_checks`, `build_rework`, `candidate_adjudications`, `rubric_ratings` and
  `question_answers` are systems of record referenced FROM findings and never counted as findings;
  `top_improvements` and `highest_leverage_change` MUST be finding ids (`[]` and `null` when there
  are no findings). `items_total = len(sample_items) = items_late + items_control`.
- `control_property_match` is REQUIRED whenever a control is the reason for a catch verdict or a
  dismissal: name the property the control exercises, cite where it operates (file:line or
  mechanism), and state why it would FAIL if the item were left unfixed.
- CONFIRMED requires the behavior traced to file:line or an observed sampled artifact (a commit,
  a diff, a check's source); anything less is HYPOTHESIS. An item with `item_source:
  diff_inferred` may be CONFIRMED for its counterfactual; where excluding all `diff_inferred` items
  would change a Q3 per-rule verdict, say so in that rule's `rationale`.

Write `audits/plan-critique-late-findings-<sha>.md`, at most 1500 words, the layer the operator
reads first: (0) one line naming any degraded mode (`degraded_dedup`, `pr_refs_unavailable`,
`unprocessed_samples`, stale anchors, or "base is local HEAD" in `contract_notes`) or "no degraded
mode"; (1) a five-line answer: do late blocking items matter, and which rule, if any, is safe;
(2) the counterfactual distribution by group as a small table; (3) the Q3 rule table; (4) the Q5
answer, worded so it can close the planning skill's revisit clause; (5) the deterministic checks;
(6) Q6; (7) top findings (those in `summary.top_improvements`); (8) Q7 highlights. Any count it
states must equal or derive from the YAML.

## 15. SEVERITY + MATURITY

Assign severity AFTER judgment, by defect class, never inherited from this prompt's framing:
- critical: S1's rules can let a wrong-but-trusted outcome merge -- for example, a class of
  core-mattered defect the blocking rule tags as a nit, or a stopping rule that can end the loop
  with a core-mattered item open -- or a loop the rules cannot end.
- high: either (a) mis-sorting: more than half of a group's blocked items have CONFIRMED
  counterfactuals outside the extended mattered set, so a REVISE verdict usually predicts no
  consequence; or (b) a core-mattered class that S1 catches only by chance (it surfaces in late
  rounds only, with no rule or check aimed at it) AND no property-matched control compensates.
- medium: mis-sorting affecting between a quarter and a half of a group's blocked items; a
  deterministic-check gap with a clear home; an ambiguous rule; a recording gap with a clear fix.
- low: clarity or wording.

Compensating controls must PROPERTY-MATCH: the control exercises the same property AND would fail
if the defect were real (apply the counterfactual to the control). A control that cannot catch the
break neither lowers severity nor justifies dismissal.

Maturity for S1, computed LAST, top-down, first match wins:
- frontier = 0 critical and 0 high findings.
- strong = 0 critical AND <= 1 high.
- solid = <= 1 critical.
- nascent = otherwise.
The top tier remains reachable when you argued a property-matched compensating control; this
prompt's framing does not foreclose it.

## 16. COMMIT / PR MECHANICS

1. The base was derived ONCE in Section 5 step 1 and you are on
   `audit/plan-critique-late-findings-<sha>` created from it. This is a deliberate exception to the
   harness-assigned session branch (`docs/contracts/git-ops.yaml` `agent_branch_prefixes`): the
   audit needs a clean two-file diff off the audited base, and `audit/` is a registered extra push
   destination in the same file (`extra_destination_prefixes`). The branch base MUST equal
   `meta.audited_commit`; the branch name carries any `-rN` suffix Section 5 step 1 added.
2. Pre-push gate: the YAML parses
   (`bin/venv-python -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" audits/plan-critique-late-findings-<sha>.yaml`),
   the counting invariant holds, every sample has a `sample_items` row or is listed in
   `meta.unprocessed_samples`, and the report is within its cap (`wc -w` on the `.md` <= 1500).
   Running `bin/venv-python -m scripts.validate --pre` is OPTIONAL (advisory outside CI); if you run
   it and an unrelated failure appears, record it in `meta.contract_notes` before committing --
   never fix it.
3. `git add` exactly the two deliverables, then
   `git commit -m "audit: late plan-critique findings"` (append any attribution trailer lines your
   harness instructs for commits) using the session's configured identity and signing (do not
   override the author, do not disable or force signing, do not amend). If a pre-commit hook
   rejects the commit, fix the cause inside your two deliverables and commit again; never bypass
   the hook.
4. Run `git push -u origin HEAD` as a tool call on its own (no pipe, redirect or second command --
   a repo hook rejects other forms). On a network error retry up to 4 times (with whatever backoff
   your harness allows). IF the push is refused for a non-network reason (for example a branch-name
   policy), push the same commit to your harness-assigned session branch instead
   (`git push -u origin HEAD:<that branch name>`, again as a tool call on its own), record this in
   the PR body, and open the PR from that branch. IF that also fails: do not open a PR; end the turn
   with a final message naming the branch, the commit sha, the exact error and the `summary:` block.
5. Open the PR via `mcp__github__create_pull_request` (owner `otl-labs`, repo `ontheloop`, base
   `main`, head your branch, ready for review, NOT draft), title
   `audit: late plan-critique findings (plan-critique gate)`, body = a 2-3 sentence lede plus the
   `summary:` block in a yaml fence, plus any PR footer your harness instructs. IF the tool is
   unavailable or the call fails: end the turn with a final message naming the pushed branch, the
   sha, the error and the intended title and body, so the human can open it. Then END THE TURN --
   do not poll, do not merge, do not subscribe, do not self-approve, do not comment on any PR. The
   human disposes of the PR.

## 17. GUARDRAILS

- Write boundary (closed list): the ONLY repository files you create or modify are
  `audits/plan-critique-late-findings-<sha>.yaml` and `audits/plan-critique-late-findings-<sha>.md`.
  You do NOT edit skills, plans, contracts, Decisions, the roadmap, code or workflows; do NOT file
  or update a recommendation; do NOT comment on any PR or issue; do NOT run a critic, call a model API,
  dispatch a workflow or re-run a plan's VP.
- Never write an AWS account id, ARN, ExternalId, credential, secret value or internal hostname in
  either deliverable (public repository).
- Precision over volume. Fewer than ~5 surviving findings is a valid result -- state it plainly; do
  not pad. `sample_items` will be long by design; its rows are classifications, not findings.
- A run that merely restates the candidates in Section 2 has failed. Every finding survives its
  dedup search (Section 13) and rests on traced items or quoted rule text.
- Hindsight discipline: Q1 uses everything you can see; Q3's rule application uses only what a
  critic could see before the fix. Do not let one leak into the other.
- If an anchor does not resolve, record it in `meta.stale_anchors` and downgrade dependent items to
  HYPOTHESIS -- never invent, never abort.
