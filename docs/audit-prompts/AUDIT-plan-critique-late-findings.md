# AUDIT PROMPT: Do late plan-critique rounds block findings that matter? A counterfactual audit of the plan-critique gate's blocking and stopping rules

Execute this prompt exactly as written. It is self-contained: do not ask clarifying questions.
Every judgment call is assigned below; where something fails, take the named degraded path and
keep going.

## 1. TASK

Audit one surface, S1: the plan-critique gate's round loop -- its blocking rule (which critic
findings block a plan) and its loop and stopping rules (when rounds continue and when they end), as
written in `.claude/skills/plan-critique/SKILL.md` and `.claude/skills/planning/SKILL.md`
(Section 10). The method is a counterfactual trace over a fixed sample of 40 critique rounds that
returned REVISE in plans merged to `main` between 2026-11-09 and 2026-11-22 (Section 11): 30 rounds
numbered 3 or later (8 of them confirming rounds, defined in Section 3 T3) and 10 control rounds
numbered 1 or 2. For each sampled round's blocking items (at most two per round, Section 9 DD-A),
determine what would have happened had the item NOT been fixed before the plan merged: which later
control would have surfaced it, whether it would have changed nothing, or whether a defect would
have reached `main`. From that, answer Q1-Q7 (Section 7): the counterfactual per item; whether late
items differ from round 1-2 items; which candidate blocking rules would have kept every item that
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
  the candidate id, classified per Section 13 (novel / planned-insufficient / planned-unbuilt).
- A possible S1 defect (from a candidate or raised by you) that you dismiss because a control
  already covers it, or because an owning item fully covers it -> a `rejected_candidates` entry,
  with the control and its property match (Section 14).
- A candidate whose evidence shows no S1 defect -> its adjudication only.

Candidates:
- C1 Flat tail. Composer-measured over the sample frame (re-derive from `gate_rounds` if a verdict
  depends on it): mean blocking findings per round are about 4.0 in round 1, 1.8 in round 2 and 1.1
  in round 3; from round 4 on, about 45% of rounds return REVISE with 0.5 to 0.8 blocking findings
  per round, with no decline by round number. Side A: the late tail is mostly items whose absence
  would change the delivered outcome. Side B: the late tail is mostly items a later control would
  catch or that change nothing.
- C2 Confirming rounds. In the sample frame, 288 plan-critique rounds immediately follow a PROCEED
  row in the same series, and 71 of them returned REVISE. Side A: confirming rounds catch items
  that matter and that nothing downstream would catch. Side B: confirming-round blocking items are
  no more consequential than other late items.
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
  ordering) by the same rule.
- C6 Plan detail. Side A: plan-level detail (named test nodes, fixture cases, VP commands) spares
  the build rework. Side B: the build redoes or departs from that detail anyway, or rework traces
  to that detail being wrong.

## 3. READ FIRST -- DISAMBIGUATION TRAPS

- T1 Who wrote the row. `gate_rounds` rows are written by the planner after fixing, not by the
  critic, and the critic's own output is not stored in the repository. A row's trailing comment
  (`# B1 ...; N1 ...`), where present, is the planner's paraphrase; most rows carry counts only
  (about 8% of the REVISE rows in the sample frame have a comment). The fix commit (T4) is
  therefore the main evidence of what a blocking item was.
- T2 Which gate. Several gates write `gate_rounds` rows (`gate: decision-scout`,
  `gate: plan-critique`, `gate: wording-check`, and others). Only `plan-critique` rows are in
  scope. A fix commit can also carry a scout or wording-check fix (sample 37's subject says
  "apply critique round 2, scout round 4"); evaluate only the critique round's items.
- T3 Series and confirming rounds. Round numbers can continue into a plan amendment PR (sample 1
  is amendment 1 of `scout-input-fingerprint`, round 4; sample 2 is a scope amendment of
  `bootstrap-ci-gated-apply`, round 9) or restart at 1 in a new series. A "confirming round" is a
  plan-critique row whose immediately preceding plan-critique row in the same plan file is a
  PROCEED with a lower round number (planning SKILL.md:412 dispatches one after a PROCEED that
  listed nits). A row with a lower number than the row before it starts a new series.
- T4 Fix commit vs round. Each sample names the commit on the plan PR's branch whose subject says
  it answers critique round N (for example "address critique round 4"). Its diff is the fix; its
  parent holds the pre-fix plan text. The same commit also applies that round's nits; DD-A step 3
  separates them. If a commit plainly answers a different round than the sample says, evaluate the
  commit as given and record the mismatch in `meta.contract_notes`.
- T5 Squash merges. A plan PR's individual commits are not on `main`; `main` holds only the squash
  commit `plan(<slug>): ... (#N)`. The commits live on the PR ref, which you fetch (Section 5
  step 4).
- T6 Plan PR vs build PR. "Build" means the implementation PR(s) for a plan: commits on `main`
  whose subject starts `feat(<slug>)` or `build(<slug>)` and so on, after the plan merged. Section
  11 lists the build commits the composer found by subject match; `none` means none matched (the
  plan may be unbuilt, or its build subject does not name the slug -- you may run one
  `git log --first-parent origin/main --grep "<rec id named in the plan>"` search to find it, no
  more).
- T7 Sample 39. PR #1933 and its fix commit cover two plans for rec-4114; the sampled plan is
  `docs/plans/PLAN-graduated-rerun-plan-readers.yaml` only.
- T8 Three meanings of "block". In this prompt, blocking and nit are the critic's finding tags
  (plan-critique SKILL.md:156). They are not a red `--pre` check and not the decision-scout's BLOCK
  verdict (Decision 262).
- T9 CI vs post-merge. "PR CI" is the `pr-validate` job of `.github/workflows/ci.yml` (the fast
  tier, `scripts.validate --pre`) plus any other workflow that runs on the PR. "Post-merge" is the
  `main-validate` job (the full tier on `main`) and anything that runs after merge (scheduled jobs,
  live runs, post-deploy VP steps).

## 4. SCOPE

S1 (built, in use): the plan-critique gate's round loop. In scope: the blocking and nit tags and
the PROCEED/REVISE rule (plan-critique SKILL.md:156-158); the loop, confirming-round, escalation
and revisit rules (planning SKILL.md:412-414); and the consequence of those rules for the sampled
items.

Context only (read to judge counterfactuals; never rated, never the subject of a finding): the
build skill (`.claude/skills/implement/SKILL.md`), PR CI and its checks, the plan lint
(`scripts/roadmap/plan_obligations.py`), the plan schema (`scripts/roadmap/plan_document.py`), the
post-merge controls, and the sampled plans and builds themselves.

Vocabulary:
- item: one blocking finding of a sampled round, identified per DD-A step 3. Item id
  `I<sample number, two digits>-<a|b>`, e.g. `I07-a`.
- late group: items from samples 1-30 (strata C and L). control group: items from samples 31-40
  (stratum K).
- control: any mechanism that would surface an item after the critique gate: the plan PR's CI,
  the build agent, the build PR's CI, a VP step, or a post-merge check.
- mattered set: items whose counterfactual (Q1) is `shipped_defect`, `caught_post_merge` or
  `needs_operator`.
- candidate rules R0-R3 and your optional RX: Q3.

Out of scope, one line each: whether critics run in a fresh context; the critique checklist's
content checks themselves (only the blocking consequence of a failed check is in scope); the
decision-scout gate and its BLOCK rule (Decision 262); the wording check and the approved-text
process (Decision 246); moving critique onto GitHub Actions personas (Decision 229); cost of any
kind.

Trust nothing quoted here: obtain every file, line, count and size by reading the repository --
re-derive from the repo and record any non-resolving anchor in `meta.stale_anchors`.

## 5. SETUP

Run these first. NEVER abort on a failure -- take the named degraded path.

0. Hook canary (repo rule, AGENTS.md): if your opening context has no `[hook_canary]` line from
   this repo's hooks, say "repo hooks not loaded" as your first status, write nothing, and end the
   turn -- the operator resolves it. This is the one sanctioned stop in this prompt.
1. `git fetch origin main` then `git rev-parse --short origin/main`. This sha IS the audited tree;
   use it in the deliverable filenames, the branch name and `meta.audited_commit`. Then
   `git switch -c audit/plan-critique-late-findings-<sha> origin/main` now, so everything you read
   is the audited tree. IF that branch already exists (locally per `git branch --list`, or on the
   remote per `git ls-remote --heads origin <name>`), append `-r2` (then `-r3`, ...) to the branch
   name only; the deliverable filenames keep `<sha>`. IF the fetch fails: use
   `git rev-parse --short HEAD`, branch off `HEAD` instead, and record "base is local HEAD" in
   `meta.contract_notes`.
2. `bin/venv-python -m scripts.session.preflight --roadmap-detail full` -- writes
   `logs/.preflight-report.json` (gitignored; expected). Recommendations have no local cache in this
   repo; rec dedup uses the live reader (step 3). IF this fails: record the error in
   `meta.contract_notes` and continue.
3. Rec dedup probe -- run it whatever step 2 did:
   `bin/venv-python -m scripts.recs_related .claude/skills/plan-critique/SKILL.md --terms "blocking nit confirming round" --limit 5 --table`.
   IF this fails: do NOT abort -- set `meta.degraded_dedup = true`, set every finding whose
   classification rests on a rec search to `confidence: HYPOTHESIS` with
   `roadmap_crossref.dedup_hit_count: null`, dedup against `docs/ROADMAP-PLATFORM.yaml` and
   `docs/DECISIONS.md` only, and proceed.
4. PR refs. For every PR number in Section 11's plan-PR column and in its Q6 list, run
   `git fetch origin pull/<N>/head:refs/remotes/pr/<N>` (one PR per command; at most 50 fetches),
   then confirm each sampled fix commit with `git cat-file -t <fix sha>` (expect `commit`). IF a
   fetch fails, retry it once; IF it still fails, or a fix commit is not found: append the PR
   number to `meta.pr_refs_unavailable`, and judge that sample's items from its `gate_rounds` row,
   the merged plan and the code, with `item_source: unavailable` and `confidence: HYPOTHESIS`.
   For a Q6 build PR whose ref cannot be fetched, record what the squash commit on `main` shows and
   mark the row `confidence: HYPOTHESIS`.
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

Q1 Counterfactual per item. For every item, suppose only that item's change was withheld (every
other change the plan received is kept) and the plan merged that way. Which outcome follows?
Per-item verdict, `counterfactual`:
- `needs_operator`: the item raised a question only the operator may settle (Decision text,
  approved wording, a change to approved scope or tier, an always-ask surface), whatever its
  engineering effect. This takes precedence over every value below.
- Otherwise the FIRST control, in lifecycle order (plan PR CI -> build agent -> build PR CI -> VP
  step -> post-merge), that would surface it:
  `caught_by_ci` (a PR CI job on the plan PR or the build PR fails because of it),
  `caught_by_build` (the build agent, following `.claude/skills/implement/SKILL.md`, hits it
  before opening its PR), `caught_by_vp` (a verification-plan step, as the plan stood without the
  item's change, fails because of it), `caught_post_merge` (only `main-validate`, a scheduled
  check or a live run after merge surfaces it).
- Otherwise: `shipped_defect` (a wrong or missing behaviour, guarantee or governed text reaches
  `main` or a deploy, and no control surfaces it); `shipped_guard_gap` (the behaviour on `main` is
  correct, but a regression guard the item asked for -- a test case, assertion or check -- is
  missing and nothing else guards that behaviour); `no_effect` (the delivered outcome is the same
  in every respect a consumer relies on).
- `undeterminable`: the evidence supports none of the above; name the missing evidence.

Pinned rules for Q1:
- Property match: a control counts only if it exercises the property the item concerns AND would
  fail if the item were left unfixed -- apply the counterfactual to the control (would that check,
  test or step go red on the pre-fix text?). A control that merely runs nearby is not a catch.
- Controls as they stood: CI checks and skills as at the parent of the plan's (first) build squash
  commit (`<build sha>^`), or at the audited base when no build merged; the VP and test obligations
  as in the plan without the item's change.
- Build agent: credit it with a catch only when a step it must run (a planned test, a VP command,
  its pytest loop, a scope check) would fail, or the code it must write cannot work without
  resolving the item. Do not credit it with noticing an omission nothing leads it to.
- Unbuilt plans: judge from the plan and the code at the audited base, set `build_merged: no`, and
  cap the item's confidence at HYPOTHESIS unless the trace needs no build.
Also record per item `catch_route`: `none` (no catch, or `no_effect`) | `in_build_fix` (fixed
inside the build PR with no plan change) | `plan_amendment` (forces a plan amendment PR: a scope or
contract deviation, implement SKILL.md deviation trigger (b)) | `forward_fix_on_main`; and
`note_would_suffice`: had the item travelled as a one-line build note instead of a plan fix, would
a build agent following the note avoid the consequence? `yes | no | unclear | n/a` (`n/a` for
`no_effect`).
Question-level verdict, over the late group: the share of late items in the mattered set --
`none` (0) | `few` (above 0, under 15%) | `some` (15% to 40%) | `many` (above 40%) |
`insufficient_evidence` (counting every `undeterminable` late item as mattered, and then as not
mattered, gives different bands).

Q2 Late vs control. Do late items differ from round 1-2 items in consequence if unfixed? For each
group, report rounds, rounds with at least one mattered item, items, mattered items, and a 95%
interval for the mattered-item share (exact binomial or Wilson; name it). Verdict:
`late_lower_value | similar_value | late_higher_value | insufficient_evidence`. Choose
`insufficient_evidence` when these sample sizes cannot separate the options, and say what size
would.

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
do not re-apply it -- it is the baseline. For each rule and each group (late, control) count:
items blocked, mattered items blocked, mattered items demoted, guard-gap items demoted, other items
demoted, and precision (mattered blocked / blocked). Per-rule verdict (late group): `safe` (demotes
no mattered item) | `unsafe` (demotes at least one mattered item whose counterfactual is CONFIRMED)
| `insufficient_evidence` (the only mattered items it demotes are HYPOTHESIS). For an `unsafe` rule
also record `safe_with_notes`: `yes` if every mattered item it demotes has
`note_would_suffice: yes`, else `no`. Question-level verdict: `safe_rule_found` (at least one of
R1-R3 or RX is `safe` for the late group) | `no_safe_rule` | `insufficient_evidence`; the prose
names the rule(s) and any rule that is safe only for a subset of rounds.

Q4 Deterministic checks. For every item, set `mechanical: true` when a deterministic check could
have caught it from the plan text plus repository state before a critic ran, and name that check.
Group mechanical items by `finding_type`; for every type with at least 2 mechanical items, record
in `deterministic_checks`: what the check asserts, its home (an existing check to extend, named by
path, or a new module under `scripts/checks/<domain>/`, or the plan lint
`scripts/roadmap/plan_obligations.py` with `docs/contracts/plan-obligations.yaml`), whether
something already checks it (`already_exists: yes | partial | no`, naming the check), and the items
it would have caught. Verdict: `some_recurring | none_recurring`.

Q5 Confirming rounds and rounds after 5. This is the planning skill's own pending revisit question
(planning SKILL.md:414: "did a confirming round ever find a blocking finding, and were findings
after round 5 real?"). Q5a: among items from the 8 confirming rounds (stratum C), did any have a
counterfactual in the mattered set? Verdict: `yes` (at least one, CONFIRMED) | `no` (none, and every
stratum C item's counterfactual is CONFIRMED) | `insufficient_evidence` (otherwise). State what the
result can and cannot rule out: with 8 rounds and none mattered, the one-sided 95% upper bound on
the true share is about 31%. Q5b, in prose and in `q5b`: among items from rounds numbered 6 or
higher (samples 2, 3, 9, 11, 14, 18, 19 and 23), report the counterfactual distribution and how
many were real (a counterfactual other than `no_effect` and `undeterminable`).

Q6 Plan detail and build rework (DD-B). Does plan-level detail prevent build rework? Verdict:
`detail_prevents_rework | detail_causes_rework | no_measurable_effect | insufficient_evidence`.
Every sampled plan is detailed, so there is no thin-plan contrast group: `detail_prevents_rework`
can be argued from the evidence but not measured; mark it HYPOTHESIS if you choose it.

Q7 Questions the operator did not think to ask. Answer these, then add your own:
- Can the gate's own yield be measured from what the repository stores (planner-written counts and
  optional paraphrases, no critic text), and what is the smallest record that would make the next
  audit of this kind cheaper and firmer?
- Do late items cluster on particular plan fields (`verification_plan`, `test_obligations`,
  `scope`, `rollback`, `context`, the approved-text sidecar), and does that differ from the control
  group?
- If, while tracing, you saw a defect in a sampled plan's merged code or plan that no round flagged,
  record it here (no extra search; never a finding, because it is outside S1).

## 8. RUBRIC

Rate S1 on each dimension: `strong | adequate | weak | absent | n/a`. `n/a` is correct and costless
where a dimension does not apply; never manufacture a rating or a finding to fill a cell.

- VD1 Late-round yield (NS1; Q1, Q5): the blocking items of late rounds are consequential (mattered
  set) rather than caught later or without effect.
- VD2 Downstream fit (NS2; Q1, Q2): the items the gate blocks are ones that no later
  property-matched control would catch.
- VD3 Blocking-rule precision (NS1; Q3, C5): the rule as written separates items that matter from
  items that can travel as notes.
- VD4 Deterministic ownership (NS3; Q4): properties a script can check are owned by scripts, not
  left to critic rounds.
- VD5 Convergence (NS4; Q5, C1, C2): later rounds yield materially fewer consequential items than
  earlier rounds, and the stopping rule ends the loop when they no longer do.
- VD6 Detail value (NS5; Q6, C6): the build-level detail the gate asks plans to carry is detail
  builds use and that prevents rework.

## 9. DEEP-DIVES

DD-A Per-item counterfactual trace (feeds Q1-Q5, C1-C5). For each sample in Section 11, in table
order:
1. Row. In `docs/plans/PLAN-<slug>.yaml` at the audited base, find the `gate: plan-critique` row
   with the sample's round number in the right series (T3). Record its blocking and nits counts and
   any trailing comment.
2. Fix. `git log -1 --format=%B <fix sha>`; `git show --stat <fix sha>`;
   `git show <fix sha> -- docs/plans/` (this includes any approved-text sidecar under
   `docs/plans/approved/`). Pre-fix plan: `git show <fix sha>^:docs/plans/PLAN-<slug>.yaml`.
3. Items: at most min(blocking count, 2) per round, from the first source that names them:
   (a) the row's trailing comment, `B1` then `B2` -> `item_source: gate_rounds_annotation`;
   (b) the commit subject or body, where it separates blocking items from nits or the round has a
   single change -> `commit_message`; (c) otherwise the diff: tag each change by plan-critique
   SKILL.md:156 (a change touching only wording, citation form or ordering answers a nit; the rest
   are blocking candidates) and take the first blocking candidates in diff order ->
   `diff_inferred`. Describe each item in at most 30 words of your own.
4. `finding_type` (pinned): `test_case_or_fixture | test_node_or_obligation_row | vp_step |
   line_pin_or_size_arithmetic | scope_row | approach_or_design | decision_conflict |
   invariant_or_safety | definition_or_wording | procedural_bookkeeping | rollback | approved_text
   | other`. `procedural_bookkeeping` covers rec closure, successor recs, waivers and
   `gate_rounds` entries.
5. Counterfactual (Q1): read what the change touched, the plan fields that consume it, the code
   the build wrote (`git show --stat <build sha>` and the relevant files at that commit) and the
   candidate controls. Record `counterfactual`, `catching_control` (file:line, check name or
   workflow job; empty when none), `control_property_match`, `catch_route`, `note_would_suffice`.
6. Origin (C3): one `git log -S"<distinctive text the item fixed>" --oneline refs/remotes/pr/<N> -- docs/plans/PLAN-<slug>.yaml`
   search. `fix_induced: yes` when an earlier round's fix commit introduced the text, `no` when it
   was in the PR's first commit, `unclear` otherwise.
7. Rules and checks: `rule_blocks` (Q3), `mechanical` and `deterministic_check` (Q4).
8. Write the row to the YAML deliverable now. The file is your working memory across any context
   compaction; re-read it rather than re-deriving finished rows.
Effort bound: there are about 55 items. Spend at most about 15 tool calls on any item; when an item
exceeds that, record what you have with `confidence: HYPOTHESIS` and move on. Grep for anchors
rather than reading whole large files.

DD-B Build rework (feeds Q6, C6). For the 10 build PRs in Section 11's Q6 list -- do NOT exceed:
1. The PR's own commits:
   `git log --oneline --no-merges $(git merge-base refs/remotes/pr/<N> <build sha>^)..refs/remotes/pr/<N>`.
2. Classify each commit from its subject, body and diff, briefly: `initial` | `ci_fix` |
   `plan_conformance` (brings the code in line with the plan) | `plan_detail_wrong` (departs from a
   plan detail because it was wrong: a renamed test node, a changed fixture, a corrected VP
   command) | `plan_detail_absent` (fills something the plan left unspecified) | `review_fix` |
   `other`.
3. Plan amendments in the build window: `plan(<slug>)` amendment commits on `main` between the
   plan's merge and the build's merge (Section 11 lists the ones the composer found).
4. Detail conformance: for the first 10 `test_obligations` rows of the plan as it stood when the
   build started, take the test function name after `::` in `test_selector` and check it at the
   build commit (`git grep -n "<name>" <build sha> -- tests/`): verbatim, renamed (same behaviour,
   other name; your judgement) or absent.
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
  unless it returns a blocking finding; each round is written to `gate_rounds`.
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
  `python -m scripts.validate --pre`; `:120` job `main-validate` (full tier on `main`); `:569` job
  `signal-green`.
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
  `scope`, `acceptance_criteria`, `verification_plan`, `test_obligations` (rows with
  `test_selector` pytest node ids), `rollback` and `gate_rounds`.
- AGENTS.md "Merge protocol": a post-merge full-tier failure on `main` gets a `ci_rca` rec and a
  forward fix, never an auto-revert (Decision 226).

Dedup pointers (returned by `scripts.recs_related` at compose time; check each one's status):
- rec-5365 "Give the three review triggers written into skills a watched home, or retire them; the
  gate_rounds one passed at 154 plans": owns scheduling the planning SKILL.md:414 revisit, not its
  answer.
- rec-3080 "Design a gate-verdict recording surface for plan-critique PROCEED/REVISE outcomes": near
  any finding about recording critic output.
- rec-3976 "Plan-critique loops on a VP step that asserts a quantity the change under test cannot
  move; no gate catches subject-misattribution": near VP-related loop findings.
- rec-3885 "No gate validates that a plan's VP commands and test_obligation selectors actually
  assert what they claim": near Q4 VP and test-selector checks.

## 11. EMPIRICAL PASS

Sample frame: the `gate: plan-critique` rows in plan files on `main` that a commit dated 2026-11-09
or later changed (152 plan files and 1,039 such rows at the composing base). Pools: REVISE rows with
at least one blocking finding, split into C (confirming round, T3; 71 rows), L (round 3 or later,
not confirming; 264 rows) and K (round 1 or 2, not confirming; 264 rows). The composer drew 8, 22
and 10 rows with a seeded random draw (seed 20261123), at most one row per plan, skipping rows whose
fix commit could not be matched by subject on the PR ref (a possible selection bias: plans whose
commit subjects did not name the round could not be drawn). You may NOT widen or replace the sample.
At most 2 items per round (DD-A step 3).

Columns: sample number | stratum | plan slug (file `docs/plans/PLAN-<slug>.yaml`) | round |
blocking count in the row | plan PR (A = an amendment PR) | fix commit | build commits on `main`
by subject match (PR numbers, or a sha).

```
 1 | C | scout-input-fingerprint                | 4  | 1 | #1999 A | 01a1530dd9e8 | #2003
 2 | C | bootstrap-ci-gated-apply               | 9  | 5 | #2029 A | 23623995c284 | #2033, #2037
 3 | C | trusted-plan-github-root               | 11 | 1 | #1890   | b8e0109354af | #1894, #1900, #2051
 4 | C | computed-reversal-conditions           | 4  | 2 | #2200   | e9f4aea433d4 | #2249
 5 | C | actions-capacity-tests-row-count       | 3  | 1 | #2161   | 9b1bbea8892d | none
 6 | C | monitor-read-budget                    | 4  | 1 | #2329   | 4db98a61a113 | #2346
 7 | C | persona-image-python-line              | 3  | 2 | #2289   | 50bfd1fbd16a | #2306
 8 | C | backlog-health-probe-reaps-descendants | 3  | 1 | #2168   | e435e6199271 | #2189
 9 | L | consolidate-decision-92-chain          | 6  | 2 | #2252   | 13dbb8631225 | #2255
10 | L | actions-sha-pin-native-policy          | 4  | 1 | #2237   | 1f64aafdc527 | 0befb87eff83
11 | L | actions-capacity-gauge                 | 8  | 1 | #1959   | b3229c1455fa | #1974
12 | L | approval-run-tldr-and-order            | 4  | 1 | #1937   | 4c9d94ce95df | #1939
13 | L | last-green-range-consumers             | 5  | 1 | #2127   | 064bc07f2efd | #2184
14 | L | ops-current-table-compaction           | 6  | 2 | #2059   | cde12b2f407b | #2068
15 | L | sit-restore-spike                      | 5  | 2 | #1940   | 2873bb3d79fa | #1947, #1950
16 | L | sandbox-reviewer-revise-route          | 3  | 1 | #2067   | c33260c09997 | #2073
17 | L | ci-claude-p-explicit-permissions       | 5  | 1 | #2363   | 4ef3df2fb6a6 | none
18 | L | telemetry-metadata-keys                | 6  | 1 | #2332   | 7cf691f0db4b | #2348
19 | L | friction-daily-detector                | 7  | 1 | #2352   | 02a79026c006 | none
20 | L | telemetry-writer-principal-registry    | 5  | 1 | #2146   | e35c33448130 | #2182
21 | L | actions-capacity-gauge-progress        | 4  | 1 | #2324   | f8deff4a2b4c | none
22 | L | persona-run-identity-producer          | 3  | 2 | #2078   | e78dcd6978e0 | #2090, #2095
23 | L | telemetry-content-edit-hook            | 7  | 1 | #2238   | d7b5245af8f0 | none
24 | L | telemetry-capture-proxy-body-cap       | 3  | 2 | #2072   | e70d54375a9e | #2074
25 | L | capture-pin-live-main-base             | 3  | 2 | #2117   | 0d451f119b54 | #2143
26 | L | contract-byte-budget                   | 4  | 1 | #2138   | 7bc708f3bc0a | #2241
27 | L | persona-trigger-policy                 | 4  | 3 | #1988   | c45acf1ba18c | #2053
28 | L | actions-sha-pin-codeowners             | 3  | 1 | #2193   | 90c78609e5e9 | #2196, #2218
29 | L | inference-tier-selection               | 3  | 1 | #1961   | df89865854ca | #1969
30 | L | persona-contracts-compute-plane        | 3  | 1 | #1976   | d842a9ab2d0a | #1977
31 | K | approved-text-verify-cli-landed        | 1  | 1 | #2320   | de8256b00b9c | #2341
32 | K | persona-contracts-verdict-slice        | 1  | 7 | #1941   | 6a9a231cc046 | #1952
33 | K | secret-read-exact-target               | 1  | 5 | #2007   | 98af97e820d7 | #2017
34 | K | persona-verdict-credential             | 1  | 3 | #1981   | 8c7adc8179fd | #1997, #2002, #2064
35 | K | approved-text-landed-words             | 2  | 1 | #2284   | 2f69f3768021 | #2312
36 | K | read-set-report                        | 2  | 1 | #2119   | 25254ca6b5e8 | none
37 | K | decision-append-only-census            | 2  | 1 | #2250   | f650af316dc0 | #2253
38 | K | telemetry-planning-cost-reads          | 2  | 1 | #2213   | eb28da37e8de | #2244
39 | K | graduated-rerun-plan-readers           | 1  | 5 | #1933   | 313a8b009781 | none
40 | K | aws-profile-test-host-config-hermetic  | 2  | 1 | #2159   | 682d2fe66832 | #2167
```

Later `plan(<slug>)` commits on `main` after the sampled plan PR (amendments and follow-ups), found
by subject match, for DD-B step 3 and for context: sample 3 #2015; 6 #2339; 10 #2259; 13 #2179;
15 #1951; 27 #2010, #2014; 28 #2210; 29 #1970; 32 #1949; 34 #1993, #2000, #2008, #2039; 39 #2317.
Samples 1 and 2: the sampled round is itself in an amendment PR (#1999, #2029).

Q6 list (DD-B), drawn with the same seed from samples that have a build; the first build PR of each
-- do NOT exceed: sample 2 #2033 (f43a1624), 8 #2189 (9f4dce72), 9 #2255 (ae78ae43), 11 #1974
(9af7f997), 13 #2184 (66949dda), 16 #2073 (b61963cf), 18 #2348 (27142164), 20 #2182 (81008f8a),
25 #2143 (1be31e65), 35 #2312 (0f7929c2). The sha in brackets is the build's squash commit on
`main`.

Tag every finding `evidence_kind: observed` when it rests on traced sample items, `static` when it
rests on skill text alone; at equal severity, observed outranks static. Do NOT call a model, run a
critic or re-run a plan's VP.

## 12. METHOD

- P1 Read: the Section 10 anchors in context (plan-critique SKILL.md around :156, planning
  SKILL.md :395-416, implement SKILL.md :60-94, ci.yml's three jobs).
- P2 DD-A for samples 1-40 in order, writing each row as you go.
- P3 DD-B for the Q6 list.
- P4 Tally: Q3 per-rule counts, Q4 grouping, Q2 and Q5 statistics.
- P5 Adjudicate C1-C6.
- P6 Rate VD1-VD6.
- P7 Write findings; dedup each (Section 13).
- P8 Synthesize: answer Q1-Q7, then assign severity and compute maturity LAST (Section 15); write
  the report.

## 13. DEDUP DISCIPLINE

Before filing any finding, search the ownership surfaces: the roadmap
(`docs/ROADMAP-PLATFORM.yaml` `tier_items[]` criteria text and `candidate_decisions[]`),
`docs/DECISIONS.md` headers and clauses, and open recs
(`bin/venv-python -m scripts.recs_related <file paths> --terms "<terms>" --table`, paging with
`--exclude` -- it takes the comma-joined rec ids the previous page printed as `next_exclude` -- at
most 3 pages per finding). Record `dedup_search_terms` and `dedup_hit_count` on the finding. A hit
means a sufficiency assessment (`planned-insufficient` / `planned-unbuilt`) or a
`rejected_candidates` row -- never a fresh discovery. A "hit" is a returned item whose text you
judge to OWN the defect (its acceptance or criterion would fix it), not merely any returned row --
`recs_related` always returns its nearest matches; record the raw returned total in
`roadmap_crossref.note`. `dedup_hit_count` counts owning hits only. A `novel` finding requires a
recorded negative search (`dedup_hit_count: 0` on the terms you list); a `novel` finding without
one is HYPOTHESIS.

DO NOT FLAG (deliberate constraints; you may assess their blocking consequence where Section 4
says so, never whether they should exist):
- Fresh-context critics (planning SKILL.md:395).
- The approved-text process and its operator approval on GitHub (Decision 246).
- A plan PR whose last decision-scout row is BLOCK fails its check (Decision 262).
- Persona-first migration of gates onto GitHub Actions (Decision 229).
- Verification weakening is human-gated (Decision 235).
- The critique checklist's content checks themselves (12c, 12c-1, 12k and the rest): only whether a
  failed check should block is in scope.
- The public-repository content boundary (AGENTS.md).

## 14. OUTPUT

Write `audits/plan-critique-late-findings-<sha>.yaml`:

```
audit:
  meta: {audited_commit: <sha>, base_branch: main, model: <your self-reported name>,
         methodology_version: 1, scope_surfaces: [S1],
         degraded_dedup: false, pr_refs_unavailable: [], contract_notes: "", stale_anchors: []}
  question_answers:
    - {q: Q1, verdict: none|few|some|many|insufficient_evidence, basis: [<item or finding ids>], prose: ""}
    - {q: Q2, verdict: late_lower_value|similar_value|late_higher_value|insufficient_evidence,
       basis: [], prose: "", interval_method: exact_binomial|wilson,
       groups: {late: {rounds: 30, rounds_with_mattered: <int>, items: <int>, items_mattered: <int>, interval_95: ""},
                control: {rounds: 10, rounds_with_mattered: <int>, items: <int>, items_mattered: <int>, interval_95: ""}}}
    - {q: Q3, verdict: safe_rule_found|no_safe_rule|insufficient_evidence, basis: [], prose: "see blocking_rules"}
    - {q: Q4, verdict: some_recurring|none_recurring, basis: [], prose: "see deterministic_checks"}
    - {q: Q5, verdict: yes|no|insufficient_evidence, basis: [], prose: "",
       q5b: {items: <int>, real: <int>, mattered: <int>, distribution: {<counterfactual value>: <int>}}}
    - {q: Q6, verdict: detail_prevents_rework|detail_causes_rework|no_measurable_effect|insufficient_evidence,
       basis: [], prose: "see build_rework"}
    - {q: Q7, answers: [{question: "", answer: "", basis: []}]}
  per_surface_assessment:
    - {surface: S1, maturity: frontier|strong|solid|nascent, strengths: "", top_gaps: [<finding ids>]}
  rubric_ratings:
    - {surface: S1, dimension: VD1..VD6, rating: strong|adequate|weak|absent|n/a,
       evidence: "file:line|item ids, or empty for n/a", note: ""}
  candidate_adjudications:
    - {candidate: C1..C6, verdict: side_a|side_b|mixed|insufficient_evidence, basis: [<item ids>], prose: ""}
  sample_items:
    - {item_id: I01-a, sample: 1, stratum: C|L|K, plan: <slug>, round: <int>, plan_pr: <int>,
       fix_commit: <sha>, item_source: gate_rounds_annotation|commit_message|diff_inferred|unavailable,
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
      - {rule: R0|R1|R2|R3|RX, group: late|control, blocked: <int>, mattered_blocked: <int>,
         mattered_demoted: <int>, guard_gaps_demoted: <int>, other_demoted: <int>, precision: "<a/b>",
         verdict: safe|unsafe|insufficient_evidence|baseline, safe_with_notes: yes|no|n/a,
         rationale: "", confidence: CONFIRMED|HYPOTHESIS}
  deterministic_checks:
    - {finding_type: "", asserts: "", home: "<path>", already_exists: yes|partial|no,
       existing_check: "", items: [<item ids>], note: ""}
  build_rework:
    - {sample: <int>, plan: <slug>, build_pr: <int>, build_commit: <sha>, commits: <int>,
       by_class: {initial: <int>, ci_fix: <int>, plan_conformance: <int>, plan_detail_wrong: <int>,
                  plan_detail_absent: <int>, review_fix: <int>, other: <int>},
       amendments_in_window: [<PR numbers>], nodes_checked: <int>, nodes_verbatim: <int>,
       nodes_renamed: <int>, nodes_absent: <int>, note: "", confidence: CONFIRMED|HYPOTHESIS}
  findings:
    - {id: PCL-01, origin: C1..C6|novel, surface: S1, question: <primary Q1..Q7>,
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
            late_mattered: <int>, control_mattered: <int>, undeterminable: <int>}
```

Rules for the contract:
- `blocking_rules.rules` has one row per rule per group; R0's rows use `verdict: baseline`,
  `safe_with_notes: n/a`; RX rows appear only if you proposed RX; `safe_with_notes` is `n/a` unless
  the verdict is `unsafe`.
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
  diff_inferred` may be CONFIRMED for its counterfactual; where treating all `diff_inferred` items
  as excluded would change a Q3 per-rule verdict, say so in that rule's `rationale`.
- A defect you find OUTSIDE S1 goes into Q7 `answers`, never into `findings[]`.

Write `audits/plan-critique-late-findings-<sha>.md`, at most 1500 words, the layer the operator
reads first: (0) one line naming any degraded mode (`degraded_dedup`, `pr_refs_unavailable`, stale
anchors) or "no degraded mode"; (1) a five-line answer: do late blocking items matter, and which
rule, if any, is safe; (2) the counterfactual distribution by group as a small table; (3) the Q3
rule table; (4) the Q5 answer, worded so it can close the planning skill's revisit clause; (5) the
deterministic checks; (6) Q6; (7) top findings (those in `summary.top_improvements`); (8) Q7
highlights. Any count it states must equal or derive from the YAML.

## 15. SEVERITY + MATURITY

Assign severity AFTER judgment, by defect class, never inherited from this prompt's framing:
- critical: S1's rules can let a wrong-but-trusted outcome merge -- for example, a class of
  consequential defect the blocking rule tags as a nit, or a stopping rule that can end the loop
  with a consequential item open -- or a loop the rules cannot end.
- high: S1 materially misallocates its blocking power (a large share of a group's blocked items lie
  outside the mattered set and are caught by property-matched later controls or change nothing),
  or a recurring consequential class surfaces only by chance in late rounds, AND the compensating
  controls you considered are insufficient.
- medium: a deterministic-check gap with a clear home, an ambiguous rule, or a recording gap with a
  clear fix.
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
   the counting invariant holds, every sampled round has at least one `sample_items` row (or a
   `meta.contract_notes` line saying why not), and the report is within its cap (`wc -w` on the
   `.md` <= 1500). Running `bin/venv-python -m scripts.validate --pre` is OPTIONAL (advisory outside
   CI); if you run it and an unrelated failure appears, record it in `meta.contract_notes` before
   committing -- never fix it.
3. `git add` exactly the two deliverables, then
   `git commit -m "audit: late plan-critique findings"` (append any attribution trailer lines your
   harness instructs for commits) using the session's configured identity and signing (do not
   override the author, do not disable or force signing, do not amend). If a pre-commit hook
   rejects the commit, fix the cause inside your two deliverables and commit again; never bypass
   the hook.
4. Run `git push -u origin HEAD` as a tool call on its own (no pipe, redirect or second command --
   a repo hook rejects other forms). On a network error retry up to 4 times (with whatever backoff
   your harness allows). IF the push still fails, or fails for a non-network reason: do not open a
   PR; end the turn with a final message naming the branch, the commit sha, the exact error and the
   `summary:` block.
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
  or update a recommendation; do NOT comment on any PR or issue; do NOT run a critic, call a model,
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
