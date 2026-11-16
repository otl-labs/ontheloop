# AUDIT: Contract-first governance retrospective

Execute this prompt exactly as written. It is self-contained: do not ask clarifying questions.
Where a step fails, take the degraded path named for it. Where something is unclear and no path is
named, choose the reading that keeps the output inside the pinned schema (Section 14), carry on,
and record the point, the reading you chose and the alternative in `meta.contract_notes`. Never
stop to ask.

## 1. TASK

You are auditing, read-only, how one governance intervention in this repository has performed since
it landed, and what would improve it further. "The intervention" means the changes made between
2026-08-07 and 2026-09-12 under roadmap item T2.56 and Decisions 167, 168, 175, 177, 178 and 179,
together with the later changes that altered it (Decisions 196, 243 and 262) and the earlier
mechanisms it relies on (Decisions 146 and 160). Its recorded purpose was fourfold: stop agents
recording specification and change narrative as numbered Decisions; send that content to contracts
and code instead; cut the context an agent must take in to work; and make the growth of governance
records sustainable. You assess six surfaces (Section 4): `corpus`, `routing_gates`, `contracts`,
`read_path`, `lifecycle` and `adjacent_records`. You answer ten questions (Section 7): Q1 routing
effect, Q2 context cost, Q3 enforcement, Q4 which components hold, Q5 sustainability of growth, Q6
ownership of remaining gaps, Q7 industry-practice rating, Q8 reversal-condition reviews, Q9
improvement path, Q10 questions the requester did not ask. Deliverables are exactly two files:
`audits/contract-first-retrospective-<sha>.yaml` and `audits/contract-first-retrospective-<sha>.md`,
where `<sha>` is the short commit id Section 5 step 1 derives. The ONLY files you create or modify
in the repository tree are those two deliverables; regenerating the gitignored local files Section 5
names is expected and does not breach this (never commit them). You draft; the human disposes: you
propose, you change nothing else, and the human decides what happens to every finding.

## 2. CANDIDATE OBSERVATIONS vs VERDICTS

This prompt hands you FACTS (Section 10) and CANDIDATES (below). It hands you no verdicts. Each
candidate is an observation plus the readings it could support; some readings say a mechanism
works, some say it does not. Adjudicating them is your job.

ASSUME NO CANDIDATE IS A REAL DEFECT UNTIL YOU TRACE IT. A run that merely confirms the candidates
below has failed. A run that finds the intervention working where the evidence says so has not
failed: `not-a-defect` is a first-class outcome.

Adjudicate every candidate C1-C20 in `candidate_adjudication` (Section 14), one row each, with one
outcome:

- `finding`: the adverse reading is supported and is a defect. Name the finding id or ids. Each
  finding's `roadmap_crossref.classification` is then one of:
  - `novel`: no roadmap item, Decision or open recommendation owns it (needs a recorded negative
    dedup search, Section 13).
  - `planned-insufficient`: an owner exists, and in your judgment its stated acceptance or criterion
    would not remove the defect.
  - `planned-unbuilt`: an owner exists with an adequate remedy, and the remedy is neither built nor
    in progress.
- `not-a-defect`: the evidence supports the benign reading, or the adverse reading holds and is a
  sound trade-off. Add a `rejected_candidates` row naming the evidence, the compensating control or
  the trade-off.
- `false`: the observation itself does not hold at the audited commit. Add a `rejected_candidates`
  row naming the evidence. If what refutes it is itself a defect, file the finding and name its id.
- `covered`: the adverse reading holds and is a defect, and an owner with an adequate remedy has it
  built or in progress. Add a `rejected_candidates` row naming the owner.
- `undetermined`: you could not settle it with the data available. Say what data would settle it.
  No finding and no `rejected_candidates` row.

"In progress" is pinned: a roadmap item whose status is `in_progress` or `complete`; a roadmap exit
criterion whose status is `met`; a recommendation whose id is listed under `bundled_recommendations:`
in a plan file under `docs/plans/` (a mention elsewhere in a plan, or under `followon_recs:`, does
not count); or a recommendation whose own text records a merged implementing pull request. Anything
else is not in progress.

A deferred owner (a roadmap item with status `deferred_post_mvp`) is an owner that is not in
progress. Whether the defect should wait for it is your judgment: if it should not wait, file a
`planned-unbuilt` finding with `change_type: rescope`; if waiting is sound, the outcome is
`not-a-defect` and the rejection row names the deferral (Decision 93).

One candidate may feed several findings, and one finding may list several candidates.

Candidates (facts are in the G rows named; re-derive before relying on any of them):

- C1 Cap proximity (G3). Many entries first appeared just under the 6,144-byte per-entry cap.
  Readings: the cap bounds entry size as designed; or the cap operates as a target and content is
  written to the limit instead of routed elsewhere.
- C2 Growth after landing (G3). Many entries are larger now than when they first appeared; the
  channel that grows them is metered at WARN tier only. Readings: sanctioned dated annotations doing
  their job; or an unbounded second inflow channel.
- C3 Entry rate (G2, G23). New entries per day and per plan file, and the share of
  contract-changing commits that also change the decision file, by period. Readings: the rates
  moved with the intervention; or they track work volume and the intervention did not change them.
- C4 Routing claim (G4). Every entry carrying an envelope claims `numbered_decision`. Readings: the
  claim is a recorded audit trail and the real routing happens upstream at planning time; or nothing
  observable distinguishes a correctly routed entry from a misrouted one.
- C5 Stock guards (G5). No stock limit on the decision corpus has existed since 2026-08-31.
  Readings: correct once retrieval replaced wholesale loading; or growth is unguarded.
- C6 Append-only lock (G6). The guard for append-only decision bodies was registered on 2026-08-25
  and retired on 2026-08-27; no guard file existed at `197c800c`. Readings: the convention holds
  without a guard; or ratified bodies can change without any check noticing.
- C7 Outflow (G7). The archive header count has not changed since before the intervention.
  Readings: outflow is not needed under retrieval by id; or the corpus only grows.
- C8 Index read (G8). The decisions index is read in full by the decision-scout sub-agent on every
  plan and grows with the live entry count. Readings: a bounded, acceptable cost; or a per-plan cost
  that scales with corpus age.
- C9 Planning read-set (G9). The files a planning run is directed to read changed size between the
  period boundaries: some shrank, some grew, some are new. Readings: ambient load fell and the rest
  is read on demand; or load moved from ambient files to pointer targets and total first-time
  intake did not fall.
- C10 Contract growth and density (G10). Contract count, bytes, effective lines, files near the
  500-effective-line budget and long lines, by boundary. Readings: content arriving at its intended
  home; or the growth pattern the decision corpus had is reappearing in contracts, shaped by a
  line-count budget.
- C11 Evaluator kinds (G11). An `agent_surface` evaluator resolves when the named file's text
  contains the contract's basename. Readings: an instruction an agent reads is a real evaluator; or
  a mention is not enforcement.
- C12 Amended currency (G4, G12). Half of live entries have currency `amended`, and the decision
  scout treats `amended` as `current`. Readings: annotations make each amended entry
  self-explanatory; or a reader of an amended entry must chase a chain to learn the current rule.
- C13 Kind and serves (G4). `kind` is required on new entries and gates nothing yet; `serves` is
  optional. Readings: forward groundwork with low cost; or required authoring cost with no consumer.
- C14 Enforcement binding (G13). Counts of live Decisions cited by check modules, contracts and
  agent surfaces; the clause-to-check traceability item is not started. Readings: citation shows
  binding; or citation is mention and binding is unmeasured.
- C15 Reversal-condition monitor (G14). Most monitored conditions are `kind: manual` and surface
  only at their review date; one stanza's review date is in the past and it is still surfaced.
  Readings: a working reminder system; or reviews that depend on someone acting on the reminder.
- C16 Adjacent records (G15). Sizes and flows of plans, roadmap, open recommendations, checks and
  tests. Readings: normal growth of a working system, each with its own governor; or governance text
  displaced into record types with no governor.
- C17 References to retired mechanisms (G16). Live surfaces and open recommendations that describe
  limits or designs later retired. Readings: harmless residue; or stale guidance an agent will act
  on. Limit this candidate to the instances G16 lists plus at most 5 more you come across; do not
  sweep for more.
- C18 Compaction readability (G17). Session compaction is recorded as a telemetry lifecycle event;
  no rollup verb that counts it was located. Readings: readable by another route; or an operator
  baseline metric that cannot be read.
- C19 Planning-session attribution (G17). Some session trees carry `workflow: plan`; the three
  trees sampled for merged plans carried `workflow: claude_code`. Readings: attribution follows how
  the session was started and both are correct; or per-workflow cost cannot be read for planning.
- C20 Gate activity (G22). In the plan files that record gate rounds and contain a Decision
  header, plan-critique returned REVISE more often than PROCEED and the decision scout returned
  flags on all but one recorded round. Readings: the gates catch real problems at a cost worth
  paying; or rounds are a standing cost whose catch is unmeasured. Stay with the routing and
  alignment gates; the planning workflow's burden as a whole is out of scope.

## 3. READ FIRST: disambiguation traps

- Trap 1, "Decision". A numbered Decision is a `## Decision N:` entry in `docs/DECISIONS.md` or
  `docs/DECISIONS_ARCHIVE.md`. It is NOT a candidate decision (`CD.NN`, in the roadmap's
  `candidate_decisions[]`), NOT a row of the `ops_decisions` warehouse table (a read-side
  projection), and NOT the `condition_reviews` block of your own output.
- Trap 2, "contract". A file under `docs/contracts/` with a `contract:` block. Classes A, B and C
  are "ritual" contracts (schema-validated field, verb and identifier contracts). Class D is a
  "mechanism" contract: free-form body plus a declared `subject` and `evaluator`. Not a Lambda API,
  not a legal contract.
- Trap 3, `status: active` on a contract does NOT mean "in force" in the ordinary sense. It is a
  grandfather-only status: in use, never ritually ratified, and not declarable by a new file
  (`docs/contracts/contract-population.yaml` `grandfather_only_forms.status_active`).
- Trap 4, "live", "current", "active". `live: true` in the index means the entry is in
  `docs/DECISIONS.md` (not the archive). `currency: current` is one of four derived values
  (`current | amended | superseded_pointer | superseded_compacted`). Neither is a contract status.
- Trap 5, "north star". Section 6 of this prompt is the bar you judge against. It is NOT
  `docs/contracts/north-star.yaml` (On The Loop's own NS.1-NS.9), whose content is out of scope.
- Trap 6, "kind". A Decision's `kind` is `direction | mechanism`. A reversal-condition's `kind` is
  `manual | repo_state`. They are unrelated.
- Trap 7, "tier" and "class". Decision tiers A/B/C (Decision 196) are not contract classes A/B/C/D,
  not verification tiers V1-V3, not roadmap tiers T-1..T5, and not the fast/full presubmit tiers.
- Trap 8, "S1..S8". In this repository S1, S2, S3, S4 and S8 name ambient prose surface classes
  (`config/prose_budgets.yaml`). This prompt's surfaces are named by word, never by S-number. The
  earlier audit's S1-S5 are its own surfaces, different again.
- Trap 9, "T2.56 complete" does not mean every step of the earlier audit's eight-step sequence ran
  as written. Step 6 (archival wave) ran and was recorded empty. Step 8's portal cutover was
  reversed by Decision 196 clause 1. Judge components individually (Q4), not by the item's status.
- Trap 10, deletion event. Decision 178 (2026-08-27) deleted adopter product code, plans and
  audits. Raw counts of plans, tests and scripts cross that event; a fall between 2026-08-07 and
  2026-09-12 in those counts is the deletion, not a trend. No decision entry was removed or
  renumbered by it (Decision 178 clause 3). The work mix changed too: after it the repository
  holds no adopter product work, so P0 and P2 rates are not like-for-like in what the plans were
  about; say so wherever you compare rates across that date.
- Trap 11, numbering and order. Entries in `docs/DECISIONS.md` are mostly, not strictly, newest
  first; select entries by number, never by position. Numbers have gaps (for example 219, 259, 260
  and 261 have no header in either file). The count of live entries is the count of headers, never
  the highest number.
- Trap 12, "significance". The envelope field `significance.value` cannot say "this should not have
  been a Decision": the check rejects the routing rows whose text says "Never a numbered Decision".
  The place a routing choice is made is the planning workflow, before an entry exists.
- Trap 13, "amendment". A Decision is amended by a newer entry's `amends` edge and by dated
  annotation lines inside the amended entry. A contract's `amendment_log` is a different thing: a
  per-file change list that Class D change-detection requires.
- Trap 14, repository renames. The repository was renamed twice (Decisions 171 and 218). Older
  text, recommendation resolutions and the earlier audit use the former names; they refer to this
  repository.
- Trap 15, "uncached tokens". Under prompt caching the telemetry field `tokens_input_total`
  (uncached input) is near zero because almost all new content is written to the cache. In this
  prompt "first-time tokens" means `tokens_input_total + tokens_cache_creation_total`: content
  taken in for the first time. `tokens_cache_read_total` is content re-read from cache on later
  calls.
- Trap 16, the earlier audit. `audits/contract-first-governance-33c8667.yaml` and `.md` (audited
  2026-08-07) are the baseline. Their numbers describe the tree at that date. They are evidence
  about then, not about now.
- Trap 17, the 2026-06-15 boundary. At that date several record types did not exist in their
  present form: contracts were mostly Markdown files outside Script A's pattern, the decisions
  index did not exist, and checks were not yet split into modules under `scripts/checks/`. Script
  A's 2026-06-15 figures for contracts, index and checks are format artifacts, not a baseline.
- Trap 18, periods and phases. P0, P1 and P2 are time periods (Section 4). The steps of Section 12
  are called Phase 1 to Phase 10. Roadmap items are written `T2.56`, `T1.5` and so on; traps are
  written "Trap N".
- Trap 19, two index files. `docs/decisions-index.json` is committed, generated from the two
  decision files, and is what the decision scout reads. `logs/.decisions-index.jsonl` is a
  gitignored local cache of the warehouse table. "The index" in this prompt always means the
  first.

## 4. SCOPE

Surfaces and their state:

| Surface | What it is | State |
|---|---|---|
| `corpus` | `docs/DECISIONS.md`, `docs/DECISIONS_ARCHIVE.md`, the authoring grammar `docs/contracts/decision-entry.yaml` and `docs/contracts/decision-kind.yaml`, the parser `scripts/decisions_md.py`, the checks under `scripts/checks/decisions/` | built |
| `routing_gates` | The authoring-time controls that decide whether content becomes a Decision: the planning skill's Decision Significance Gate, plan-critique step 12p, the `scripts/roadmap/plan_obligations.py` draft check, the envelope rules in the conformance check, the decision-scout gate and its BLOCK rule (Decision 262) | built |
| `contracts` | `docs/contracts/*.yaml` (depth 1) and the declared sub-tree `docs/contracts/personas/`, the population gate `docs/contracts/contract-population.yaml`, `scripts/checks/contracts/validate_contract_drift.py` and `_population.py`, the contracts class in `config/structural_size_budgets.yaml` | built |
| `read_path` | How an agent takes governance in: the ambient layer (`AGENTS.md`, per-directory `CLAUDE.md`, skills and `docs/PROJECT_CONTEXT.md` are byte-budgeted by `config/prose_budgets.yaml`; commands are measured only), `docs/decisions-index.json` and its generator `scripts/decisions_index.py`, the decision-scout skill, pointer targets a plan run is sent to | built. Built and not used by the scout: the reader verbs `decision_by_id` and `decisions_max_updated` (`src/common/ducklake_reader_client.py:249-250`). Designed and unbuilt: moving the scout's triage onto a queried verb (T1.5 c1), the decision-scout persona (T4.30), the per-file decision split and generated index (Decision 217, T4.24) |
| `lifecycle` | What happens to records after they land: archival (Decision 146), compaction and migrate-then-rehome (Decisions 149, 175), the append-only dialect (Decision 177), the currency projection, `validate_supersession_annotations`, the reversal-condition monitor `scripts/preflight/decision_conditions.py`, `docs/decision-migration/MANIFEST.yaml` | built; one guard retired (G6) |
| `adjacent_records` | Other record types that could absorb displaced governance text: `docs/plans/`, `docs/ROADMAP-PLATFORM.yaml` and its archive, the open-recommendation backlog, the check and test fleets | built. This is a BOUNDED LEDGER: you measure stock and flow, name the governor if one exists, and rate VD4 and VD7 only. You do not audit these systems. |

Periods:

- P0, before: 2026-06-16 to 2026-08-07.
- P1, the intervention window: 2026-08-08 to 2026-09-12.
- P2, after: 2026-09-13 to the audited commit.

Boundaries are the commits Script A pins: the last commit on main on or before 2026-06-15,
2026-08-07 and 2026-09-12 (UTC, end of day), and the audited commit.

Vocabulary:

- Entry: one numbered Decision, measured as the UTF-8 byte span from its `## Decision N:` header to
  the byte before the next such header (or end of file). Older trees wrote three archived entries
  with a third `#`; Script A's header pattern `#{2,3}` counts those. At the audited commit every
  header is `##`.
- Envelope: the fenced `yaml` block directly under a header, required on entries new since
  Decision 167.
- Effective lines: non-blank lines whose first non-whitespace character is not `#`
  (`config/structural_size_budgets.yaml` counting rule).
- Planning read-set: exactly the 16 files listed in G9.
- Planning context footprint: the first-time tokens (Trap 15) a planning run needs, the root agent
  and its gate sub-agents together, before the plan is written. Its static form is bytes of the
  read-set at a boundary, with tokens estimated as bytes / 4 (the repository's own convention in
  `scripts/preflight/prose_context.py`). Its observed form comes from telemetry (Section 11 E5).
- Governor: a mechanism that fails or blocks when a quantity grows. A flow governor bounds what one
  change or one item may add (500 effective lines per contract file, 6,144 bytes per new entry). A
  stock governor bounds a record type's total.
- Hard limit: a registered check that FAILS when a record type's total crosses a bound (a count, or
  the size of the one file that holds the whole record type). G21 lists the ones known. A per-item
  budget is a flow governor, not a hard limit: reaching it forces a split or a trim, which is its
  designed response.
- Audit day: the UTC date on which you run Section 5.

Out of scope (do not file findings on these):

- Whether any individual Decision's ruling is right, except the items Section 13 lists as OPEN TO
  CHALLENGE.
- CI run time, the fast-tier budget, merge throughput and test-suite design (owned by
  `audits/fast-tier-optimisation-3560909.md`, `audits/ci-merge-throughput-scaling-269e630a.yaml`,
  `audits/validate-test-suite-4df4d48.yaml`).
- The human-decision burden of the planning workflow as a topic of its own (owned by
  `audits/planning-decision-burden-735e69fc.yaml`); you may cite it as VD6 evidence.
- Roadmap sequencing in general. In scope: recommending that an item G18 names be pulled forward.
- The quality of recommendation triage. In scope: what C16 and C17 state about the backlog.
- Whether telemetry captures sessions correctly. In scope: whether a metric this prompt needs can
  be read (C18, C19).
- The content of `docs/contracts/north-star.yaml`, Terraform, IAM and deployment.

Trust-nothing clause: obtain every file, line and size by reading the repository at the audited
commit. Trust no number quoted in this prompt; re-derive it (Section 10 gives a command for every
numeric row) and record any anchor or number that does not resolve in `meta.stale_anchors` as
`{anchor, expected, found}`. Ordinary drift between `197c800c` and the audited commit is expected:
record the new value, not a stale anchor, when the difference is explained by commits in between.

## 5. SETUP

Run these first, in order. NEVER abort on a failure: take the named degraded path.

0. Hook canary (repository rule, `AGENTS.md`). Applies only to a top-level interactive session; a
   sub-agent, CI job or scheduled agent skips it. If your opening context has no `[hook_canary]`
   line from this repository's hooks, say "repo hooks not loaded" as your first status, write
   nothing, and end the turn. This is the one sanctioned stop in this prompt.
1. `git fetch origin main` then `git rev-parse --short origin/main`. This sha IS the audited tree;
   use it in the deliverable filenames, the branch name and `meta.audited_commit`. Then
   `git switch -c audit/contract-first-retrospective-<sha> origin/main`. After this your working
   tree is the audited commit. IF that branch already exists (locally per `git branch --list`, or
   on the remote per `git ls-remote --heads origin <name>`), append `-r2` (then `-r3`, ...) to the
   branch name only; the deliverable filenames keep `<sha>`. IF fetch fails: use
   `git rev-parse --short HEAD`, branch off `HEAD`, record "base is local HEAD" in
   `meta.contract_notes`, set `REF = "HEAD"` at the top of Scripts A, B and C, and read
   `origin/main` as `HEAD` in every later command. IF `git switch -c` fails for any other reason:
   record the error in `meta.contract_notes`, stay on the current branch, read every repository
   file with `git show <sha>:<path>` instead of from the working tree, and at Section 16 skip steps
   3-5: leave the two deliverables uncommitted and end the turn with a final message naming their
   paths and the error. IF the switch failed and the branch you are left on is `main`, or at any
   point you cannot write under `audits/`: write the two deliverables to
   `/tmp/contract-first-retrospective/` instead, skip Section 16 and end the turn with a final
   message naming those paths and the reason.
2. History depth: `git rev-parse --is-shallow-repository`. IF it prints `true`, run
   `git fetch --unshallow origin main` once. IF it still prints `true`: set
   `meta.shallow_history = true`; every period figure you cannot derive becomes `null`; use the
   G-row figures as dated evidence; and every answer or finding resting on them is
   `confidence: HYPOTHESIS`.
3. `bin/venv-python -m scripts.session.preflight --roadmap-detail full` -- writes
   `logs/.preflight-report.json` (gitignored; expected). IF it fails: record the error in
   `meta.contract_notes` and continue; read the roadmap directly (step 5); use the preflight
   figures quoted in G14 and G15 as dated evidence; write `null` for the `open_recommendations`
   stock; leave the report's size out of Q2; and mark what rests on them `confidence: HYPOTHESIS`.
4. Recommendation dedup probe (recommendations have no local cache; dedup reads the live reader):
   `bin/venv-python -m scripts.recs_related --terms "decision corpus significance contract" --limit 5 --table`.
   IF it fails: do NOT abort -- set `meta.degraded_dedup = true`, set every finding whose owner
   rests on a rec search to `confidence: HYPOTHESIS` with `roadmap_crossref.dedup_hit_count: null`,
   dedup against the roadmap and `docs/DECISIONS.md` only, and use the rec descriptions in G18 as
   the only rec content. Proceed.
5. Roadmap parse check:
   `bin/venv-python -c "import yaml; d=yaml.safe_load(open('docs/ROADMAP-PLATFORM.yaml')); print(len(d['tier_items']))"`.
   IF it errors, record it in `meta.contract_notes` and read the YAML as text.
6. Telemetry probe:
   `D=$(date -u -d yesterday +%F); bin/venv-python -m scripts.telemetry_usage models --from "$D" --to "$D"`.
   Success is exit code 0 and one JSON object on stdout that has a `rows` key (an empty `rows` list
   is success). Otherwise: set `meta.degraded_telemetry = true`, skip Section 11 E5, write `null`
   for every observed token figure, use the dated samples in G17 as your only observed numbers, and
   give every answer or finding resting on observed tokens `confidence: HYPOTHESIS`. Proceed.
7. External lookups: you MAY use web search or fetch, at most 10 lookups in total (each search and
   each page fetch counts as one) -- do NOT exceed -- solely to confirm a named industry practice
   that a Q7 rating or a Q9 step cites. Record each as `{title, url}` in `meta.external_sources`.
   IF web access is unavailable: set `meta.web_unavailable = true`, cite practices from your own
   knowledge by name, and mark each Q9 step that depends on one `confidence: HYPOTHESIS`.

Every `meta` flag you set is read by the human reviewer: list each set flag and its consequence in
the report's Intake section (Section 14). Repository-wide `validate` is advisory outside CI;
running it is optional and never a gate (Section 16).

## 6. NORTH STAR

The bar. Each principle is one you JUDGE each surface against, weighing evidence both ways; none is
a pattern to match. Where two pull against each other, say so and argue the trade.

- B1 Governance is read by agents on a budget. A record's cost is what it costs each reader on each
  use. Measured first-time intake per planning run should not rise as the repository ages.
- B2 Enforced beats asserted. A rule that matters has something that fails when it is broken; a
  rule with no evaluator is advice. The repository states this itself (Decision 163; the earlier
  audit's finding CFG-03).
- B3 One home per fact. Specification lives where its enforcer reads it; the reason for a choice
  lives in the Decision; what changed in one landing lives in the commit or PR. The same fact stated
  in two places will drift.
- B4 Growth is governed. A record type is sustainable when something bounds what it costs to use
  as it grows: a bound on what each change may add, a bound on the total, or a read path whose cost
  does not depend on the total. Which of these fits a record type is for you to argue.
- B5 The why survives. A reader can recover, by identifier, why a choice was made and what would
  reverse it, and can tell from the record itself whether it still governs.
- B6 Human attention is spent where it decides something. Direction and approval stay with the
  human; mechanism should not need them.
- B7 The cure costs less than the disease. Governance of governance is itself a cost; a control
  whose own upkeep exceeds what it saves is a defect, and so is a control that cannot show whether
  it is working.

## 7. THE QUESTIONS

Each question gets its own entry in `question_answers`. Answer Q1-Q8 from evidence before you
write Q9. Verdict options are pinned; choose one. `cannot-determine` requires naming what was
missing.

Verdict rules. Where a question gives a default rule, apply it; you may depart from it only by
stating in `prose` the evidence that outweighs it. Where a question says the verdict is your
judgment, `prose` states the threshold you applied, so a reader can re-apply it. The verdicts of
Q2, Q4, Q6 and Q7 are measurements or summaries computed from your own tables: say so in `prose`.
A computed verdict is not a finding; whether what it measures is a defect is decided through the
candidates. A `basis` list may be empty.

**Q1 Routing effect.** Has what is written as a numbered Decision changed since the intervention,
and is the content it was meant to displace reaching contracts, code or the change record?
Evidence: the content partition of the newest entries against the 2026-08-07 baseline (E1), the
bar test on the same entries (E1), the contract-change samples (E2), G23, and C1-C4. Default rule.
For each E1 entry compute `spec_share` = (specification + change-record) / (specification +
rationale + change-record), as a percentage: class (4) is left out so that envelopes and
annotations, which the baseline entries did not carry, cannot move it. `mean_spec_share` is the
plain mean of the 12 values. The baseline's value, from its means, is 65 (62 of 95), stated as
plus or minus 10. `minted_share` is the share of E2 group (a) samples with governing content (any
classification other than `housekeeping`) that are `would_have_minted_before: yes`. `shifted` when
`mean_spec_share` is 45 or lower AND `minted_share` is at least one half; `not-shifted` when
`mean_spec_share` is 55 or higher AND `minted_share` is under one quarter; otherwise
`partially-shifted`.
Verdict: `shifted | partially-shifted | not-shifted | cannot-determine`.

**Q2 Context cost.** What does the decision and contract system cost an agent now against before?
Answer with two pinned metrics. (a) Planning context footprint, static. Assign each of the 16
read-set files to one band by tracing the workflow text AT THE AUDITED COMMIT, and apply that one
assignment to all three boundaries (a file absent at a boundary contributes 0): `always_read`
(loaded ambiently, or the workflow tells the root planning agent to read it in full on every plan),
`gate_subagent` (read in full by the decision-scout or plan-critique sub-agent on every plan),
`on_demand` (a pointer the run follows only for some plans, or reads in part). Precedence when a
file fits two bands: `always_read`, then `gate_subagent`, then `on_demand`. Say in `prose` if you
can see that a file's role changed between boundaries (for example content that left `AGENTS.md`
for a contract). A file the workflow mandates on every plan that is not among the 16 goes in
`extra_files` and in `prose`, never in a total. `logs/.preflight-report.json` is not in git and is
excluded from every static figure; give its current size in `prose`. `token_estimate` is
`(always_read_bytes + gate_subagent_bytes) // 4`. (b) Planning context footprint, observed, and
sessions needing compaction, from telemetry (E5); telemetry begins about 2026-11-01, so there is no
observed "before": say so. Default rule, applied to `always_read_bytes + gate_subagent_bytes` at
the audited commit against 2026-08-07: `reduced` if lower by more than 10 percent, `grown` if
higher by more than 10 percent, `held` otherwise. Apply the same three-way rule to `always_read`
alone and record it as `root_agent_direction` (the root agent's context is the one compaction acts
on), and to `gate_subagent` alone as `gate_subagent_direction`. These three directions are
arithmetic on your band assignment; the judgment in Q2 is the assignment itself and what `prose`
says about which intake a planning run needs and which it could fetch on demand.
Verdict: `reduced | held | grown | cannot-determine`.

**Q3 Enforcement.** The original complaint was that Decisions were rarely enforced. For entries
landed since Decision 167 and for Class D contracts: is each bound to something that fails, or that
an agent is instructed to apply at a defined trigger, when it is violated? Evidence: the evaluator
samples (E3), the binding test in E1, C11 and C14. Default rule. Call an E3 sample "enforcing" when
it is classified `asserts-this-statement`, `asserts-other-content` or `instructs-agent-at-trigger`.
`enforcing_share` weights the two evaluator kinds by their population: (N_check x the enforcing
share among your `check` samples + N_surface x the enforcing share among your `agent_surface`
samples) / (N_check + N_surface), where N_check and N_surface are the Class D counts Script C
prints (41 and 12 at `197c800c`). `bound_count` is the number of E1 entries (of 12) whose binding
test is `bound`. `enforced-by-default` when `enforcing_share` is at least three quarters AND
`bound_count` is at least 9; `mostly-asserted` when `enforcing_share` is under one half OR
`bound_count` is under 5; otherwise `mixed`.
Verdict: `enforced-by-default | mixed | mostly-asserted | cannot-determine`.

**Q4 Component ledger.** For each intervention component L1-L21 (G19), what state is it in? Fill
`intervention_ledger` (Section 14); one piece of exercised evidence per row is enough, taken from
where G19's hint points. The verdict is a summary computed from the ledger. The core purposes are
the four with the tags `routing`, `entry-size`, `contract-enforcement` and `read-cost` in G19.
`all-holding`: no row is `eroded` or `retired-without-replacement`. `core-gap`: for at least one
core purpose, no row carrying that purpose tag is `holding` or `holding-with-side-effect`.
`core-holding`: anything else.
Verdict: `all-holding | core-holding | core-gap`.

**Q5 Sustainability.** Fill `growth_ledger`: for each record type its stock, its flow per period,
what governs it, how agents pay for it, and whether a hard limit (Section 4) binds within 30 days.
`binds_within_30_days` is arithmetic: remaining headroom divided by the P2 daily rate; `yes` when
the result is under 30 days; `no` when it is 30 days or more, or the P2 rate is zero or negative;
`n/a` when the record type has no hard limit. A ratchet that by design equals the current size
(`config/prose_budgets.yaml`) is recorded as `governor: stock` with `binds_within_30_days: n/a`.
Whether having no limit is itself a problem is your judgment under B4. Then answer for the system
as a whole. Fill `projections` at 90, 180 and 365 days from the audited commit, linearly: live
entries = Script A `live_headers` at the audited commit plus the P2 `entries_per_day` times the
horizon; `token_estimate` = its value at the audited commit plus its P2 daily change (its value at
the audited commit minus its value at 2026-09-12, divided by the P2 days) times the horizon. Say
whether the cost of one planning run stays flat as stock grows. The verdict is your judgment, with
one pinned floor that is arithmetic: `unsustainable-on-current-trajectory` is required when any
ledger row binds within 30 days.
Verdict: `sustainable | sustainable-with-named-gaps | unsustainable-on-current-trajectory | cannot-determine`.

**Q6 Gap ownership.** For the defects you filed: does an owner already exist for each, and would
it close the defect? Rule: `sufficient` when no finding is classified `novel` or
`planned-insufficient` (also when there are no findings); `insufficient` when more than half of
findings are; otherwise `partial`.
Verdict: `sufficient | partial | insufficient`.

**Q7 Industry practice.** Rate the routing architecture, repository-wide, against the EXTERNAL
CHECKLIST below -- the same closed list the 2026-08-07 audit used. For each property write the
rating (`met | partial | missed`) and its present-day evidence before you fill `baseline` and
`delta` (`up | same | down`) from G20. The baseline is never evidence: a rating equal to its
baseline still needs its own evidence line. Use the baseline's rule: `partial` requires an argued,
property-matched compensating control in `evidence`; a property only partly satisfied with no such
control is `missed`. EX1-EX13 is a CLOSED list: do not add properties. Verdict, from the deltas:
`improved` (at least one up, none down), `regressed` (at least one down, none up), `mixed` (both),
`unchanged` (none moved).

| ID | Property |
|---|---|
| EX1 | ADR discipline (Nygard, MADR): a decision record is immutable, covers one decision, and carries context, decision, status and consequences -- it records why this choice, and does not double as the specification of the thing chosen. |
| EX2 | Normative versus informative separation (IETF RFC 2119, ISO/IEC directives): normative requirements live in a specification surface and are labelled as such; rationale is explicitly non-normative and cannot be cited as a requirement. |
| EX3 | Contract-first or spec-first authoring (OpenAPI, Protobuf, JSON Schema): the machine-readable specification is authored with or before the implementation, and the implementation and its tests bind to it as the single source. |
| EX4 | Policy-as-code (OPA/Rego, Conftest): governance rules are executable, versioned artifacts, not prose. A rule with no evaluator is not a rule. |
| EX5 | Bidirectional traceability: requirement to implementing artifact to verifying check, with orphan detection on both sides. |
| EX6 | Change-record granularity: the commit or PR message carries what changed and why now; the specification carries what is true; the decision record carries why this choice. Three questions, three homes. |
| EX7 | Deprecation and supersession lifecycle: superseded records are marked, discoverable, and shed weight; the corpus is not monotonically growing. |
| EX8 | Bounded retrieval: the governance corpus is indexed and retrieved by identifier, not loaded wholesale into every consumer. |
| EX9 | Stable identifiers independent of presentation and storage: an identifier survives a move between file, index and table without rewriting inbound references. |
| EX10 | Single semantic authority with generated projections: derived surfaces are generated from one authority, not maintained as parallel authorities. |
| EX11 | Preservation of rejected alternatives and reversal conditions: what was considered and rejected, and what would reverse the choice, survive as first-class recoverable content. |
| EX12 | Governed retention: archival, compaction and deletion are defined lifecycle operations with stated triggers, not ad-hoc relief actions. |
| EX13 | Versioned, testable contracts: a specification artifact carries a version, and a test or gate can fail against it -- the contract is exercisable, not merely declarative. |

**Q8 Reversal-condition reviews.** Three intervention Decisions carry monitored reversal
conditions whose review dates fall between 2026-11-17 and 2026-12-01 (G14). For each of the eight
conditions listed in G14, decide from evidence whether it has fired, and recommend a disposition
(G14 pins how to test the two prose conditions). Fill `condition_reviews` (Section 14). This question's entry carries no verdict of its own; its
`prose` points at the block.

**Q9 Improvement path.** Given your answers to Q1-Q8: which changes, in what order, are worth
their cost? For each of Q1-Q6 that is not at its first-listed verdict (a `cannot-determine` needs
nothing), either give a step that would move it, or say in `prose` why no step is worth its cost:
a verdict can be acceptable as it stands. Each step names the industry practice it applies (an EX
id, or another named practice with a source) and the existing repository mechanism it reuses, if
any. Separately, name what is worth doing beyond closing defects, if anything. Fill
`improvement_sequence` (Section 14): at most 10 steps -- do NOT exceed -- of which at most 4 may be
opportunities with no finding behind them. You need not have a step for every EX property; list in
`prose` each EX property rated `partial` or `missed` that no step advances, with one line on why.
An empty sequence is a valid result when the evidence supports it. Do not propose a step the DO
NOT FLAG list (Section 13) excludes.
Verdict: `path-identified` (the sequence has at least one step), `no-change-warranted` (it has
none; `prose` says why).

**Q10 Questions the requester did not think to ask.** Answer each seed below AND add any you
judge the requester will wish had been asked (at most 4 additions). Shape:
`{question, answer, basis: [finding ids]}`. Answer from the evidence you gathered for Q1-Q9 unless
the seed names its own method; where the repository cannot answer a seed, say so in one line.

- Does the 6,144-byte cap now shape how entries are written (compression, abbreviation, clause
  packing) in a way that makes them harder for an agent to read correctly?
- Is `amends` the main way the corpus grows, and what does a chain of amendments cost a reader who
  lands on the oldest entry in it?
- Do contracts now show the growth pattern the decision corpus showed in P0, and is the
  500-effective-line budget changing how they are written?
- What share of entries landed since Decision 167 are themselves about governing decisions,
  contracts or this corpus? Judge from the titles in `docs/decisions-index.json` only.
- At what number of live entries does one plan's decision-scout triage stop being reliable or
  affordable, and what in the repository would show it happening?
- What would a steady state look like: what must leave, or stop costing, for entries to keep
  arriving at the P2 rate indefinitely?
- What do the routing and alignment gates cost per plan (rounds, sub-agent runs) against what the
  record shows they catch (G22)?
- Q2 measures a planning run. For an implementing run: list the repository files that
  `.claude/commands/implement.md` and `.claude/skills/implement/SKILL.md` name as things to read
  (contracts, skills, commands), sum their bytes at 2026-08-07, 2026-09-12 and the audited commit
  with `git cat-file -s <commit>:<path>` (0 when the file is absent), and give the three totals. No
  bands.
- Did outcomes change, as well as cost and routing? Say what evidence exists here that agents make
  fewer governance mistakes since the intervention (a contradiction merged, rework, a red main with
  a governance cause), and whether the repository can measure that at all.

## 8. RUBRIC

Rate each surface on the dimensions the second table assigns to it: `strong | adequate | weak |
absent`. Every other cell is `n/a`: `n/a` is correct and costless where a dimension does not
structurally apply; never manufacture a rating or a finding to fill a cell.

- `strong`: a mechanism answers the dimension's question and you found it working; no finding of
  medium severity or higher lands on the cell.
- `adequate`: the question is mostly answered; findings on the cell are low or medium.
- `weak`: a high or critical finding lands on the cell, or the property rests on convention alone.
- `absent`: nothing on the surface addresses the dimension.

A finding lands on the cell named by its `surface` and `dimension`; a `shared` finding lands on its
dimension's cell for every surface it names in `title` or `gap`.

| ID | Dimension | Asks | Serves |
|---|---|---|---|
| VD1 | Routing correctness | Does content land in the record type B3 names for it? | Q1, Q7 |
| VD2 | Enforcement binding | Is each rule tied to something that fails or instructs when it is broken (B2)? | Q3, Q7 |
| VD3 | Read cost per use | What does one use cost a reader, and does that grow with stock (B1)? | Q2, Q5 |
| VD4 | Growth governance | Is there a flow governor, a stock governor, neither; what is the trajectory (B4)? | Q5, Q4 |
| VD5 | Lifecycle and currency | Can a record be retired, and can a reader tell from the record whether it governs (B5)? | Q4, Q7, Q8 |
| VD6 | Authoring and operator burden | What does writing it correctly cost the author and the human (B6, B7)? | Q9, Q10 |
| VD7 | Measurability | Can the system show whether this property holds or is regressing (B7)? | Q2, Q5, Q8 |

| Surface | Dimensions to rate |
|---|---|
| `corpus` | VD1, VD2, VD3, VD4, VD5, VD6, VD7 |
| `routing_gates` | VD1, VD2, VD6, VD7 |
| `contracts` | VD1, VD2, VD3, VD4, VD5, VD6, VD7 |
| `read_path` | VD3, VD4, VD6, VD7 |
| `lifecycle` | VD2, VD4, VD5, VD6, VD7 |
| `adjacent_records` | VD4, VD7 |

## 9. DEEP-DIVES

- DD-A Flow arithmetic (feeds Q1, Q5, Q8). Run Scripts A and B. Produce the `metrics` block with
  these derivations, pinned. `new_entries` for a period is the change in Script A's
  `distinct_numbers` between the period's two boundaries (a move to the archive is not outflow).
  `corpus_bytes_added` is the change in `decisions_bytes + archive_bytes`. `days` is 53 for P0, 36
  for P1, and for P2 the whole days from 2026-09-12 to the audited commit's committer date.
  `plan_files_added` comes from Script B. Also split inflow into bytes of entries at first
  appearance and bytes added to entries after they landed (Script B's
  `first_appearance_bytes_total` and `bytes_added_after_landing`; both go in `metrics.cap`). State
  what the per-plan rate shows that the per-day rate does not.
- DD-B What the newest entries contain (feeds Q1, Q3). Section 11 E1.
- DD-C Where displaced content went (feeds Q1). Section 11 E2.
- DD-D Whether evaluators evaluate (feeds Q3). Section 11 E3.
- DD-E Planning context footprint (feeds Q2, Q5). Trace what a planning run reads: start at
  `.claude/commands/plan.md`, follow each instruction to read, load or dispatch, and assign each of
  the 16 read-set files to a band (Q2). Then the observed pass (Section 11 E5).
- DD-F Contract trajectory (feeds Q5, Q10). For five files -- the three with a raised budget (G10)
  and the two with the highest effective-line count at or under 500 (ties by basename) -- record
  effective lines, budget, and the share of effective lines that sit under `amendment_log`, in
  `metrics.contracts_near_budget`. Then say, in Q5's `prose`, whether the contracts class is
  absorbing content at a rate its governor bounds and what a file does when it reaches its budget.

## 10. GROUNDING MAP

This map spends your cognition on judgment, not on search. Every row is an observed fact with its
anchor, stated without a verdict. Verify before relying. Figures are as of commit `197c800c`
(2026-11-16). `docs/DECISIONS.md` and `docs/ROADMAP-PLATFORM.yaml` change daily, so this map
anchors them by Decision number and clause, or by roadmap item id, never by line: locate a Decision
with `grep -n '^## Decision 167:' docs/DECISIONS.md` and a roadmap item with
`grep -n 'id: T2.56$' docs/ROADMAP-PLATFORM.yaml`. Each line anchor into another file names the
symbol, key or text it points at; if the line has moved, find it by searching for that name. A
moved line is drift, not a stale anchor: record a `meta.stale_anchors` row only when the named
symbol, key or text is not in the file at all.

Three scripts re-derive the numeric rows. Run each from the repository root as
`bin/venv-python - <<'EOF'` ... `EOF`. They only read.

Script A (boundary table; feeds G2, G8, G9, G10, G15, G21). The second line it prints for each
boundary is the 16 read-set sizes in the order of `READ_SET`:

```python
import re, subprocess
REF = "origin/main"  # change to "HEAD" only if Section 5 step 1 fell back to local HEAD
def sh(*a):
    return subprocess.run(a, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
READ_SET = ["CLAUDE.md", "AGENTS.md", ".claude/commands/plan.md", ".claude/skills/planning/SKILL.md",
            "docs/PROJECT_CONTEXT.md", "docs/contracts/file-router.yaml", "docs/contracts/git-ops.yaml",
            "docs/contracts/project-alignment.yaml", ".claude/skills/decision-scout/SKILL.md",
            "docs/contracts/north-star.yaml", "docs/decisions-index.json",
            "docs/contracts/instruction-architecture.yaml", ".claude/skills/plan-critique/SKILL.md",
            "docs/contracts/plan-obligations.yaml", "docs/contracts/overseer-dispatch.yaml",
            "docs/contracts/decision-entry.yaml"]
def eff(text):
    return sum(1 for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#"))
for label in ["2026-06-15", "2026-08-07", "2026-09-12", "AUDITED"]:
    c = sh("git", "rev-parse", REF).strip() if label == "AUDITED" else \
        sh("git", "rev-list", "-1", "--before", label + "T23:59:59Z", REF).strip()
    size = {}
    for line in sh("git", "ls-tree", "-r", "-l", c).splitlines():
        meta, path = line.split("\t", 1)
        s = meta.split()[3]
        size[path] = int(s) if s.isdigit() else 0
    def show(p):
        return sh("git", "show", f"{c}:{p}") if p in size else ""
    nums = lambda p: re.findall(r"^#{2,3} Decision (\d+):", show(p), re.M)
    live, arch = nums("docs/DECISIONS.md"), nums("docs/DECISIONS_ARCHIVE.md")
    con = [p for p in size if re.fullmatch(r"docs/contracts/[^/]+\.ya?ml", p)]
    texts = [show(p) for p in con]
    effs = [eff(t) for t in texts]
    plans = [p for p in size if p.startswith("docs/plans/")]
    amb = [p for p in size if p == "AGENTS.md" or p.endswith("CLAUDE.md") or p == "docs/PROJECT_CONTEXT.md"
           or re.fullmatch(r"\.claude/commands/[^/]+\.md", p) or re.fullmatch(r"\.claude/skills/[^/]+/SKILL\.md", p)]
    print(label, c[:8],
          "| decisions_bytes", size.get("docs/DECISIONS.md", 0), "archive_bytes", size.get("docs/DECISIONS_ARCHIVE.md", 0),
          "live_headers", len(live), "archive_headers", len(arch), "distinct_numbers", len(set(live) | set(arch)),
          "index_bytes", size.get("docs/decisions-index.json", 0),
          "| contracts", len(con), "bytes", sum(size[p] for p in con), "eff_lines", sum(effs),
          "ge475", sum(1 for e in effs if e >= 475),
          "line_gt1000", sum(1 for t in texts if any(len(l) > 1000 for l in t.splitlines())),
          "| read_set_bytes", sum(size.get(p, 0) for p in READ_SET), "ambient_layer_bytes", sum(size[p] for p in amb),
          "| plans_files", len(plans), "plans_bytes", sum(size[p] for p in plans),
          "| roadmap_bytes", size.get("docs/ROADMAP-PLATFORM.yaml", 0) + size.get("docs/ROADMAP-PLATFORM-ARCHIVE.yaml", 0),
          "roadmap_lines", show("docs/ROADMAP-PLATFORM.yaml").count("\n"),
          "| check_py", sum(1 for p in size if p.startswith("scripts/checks/") and p.endswith(".py")),
          "tests_py", sum(1 for p in size if p.startswith("tests/") and p.endswith(".py")))
    print("  read_set", [size.get(p, 0) for p in READ_SET])
```

Script B (first appearance of each entry numbered 167 or higher; plan files added per period;
feeds G2, G3). It reads every first-parent version of `docs/DECISIONS.md` since 2026-08-06:

```python
import collections, re, statistics, subprocess
REF = "origin/main"  # change to "HEAD" only if Section 5 step 1 fell back to local HEAD
def sh(*a):
    return subprocess.run(a, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
HEAD_RE = re.compile(r"^## Decision (\d+):", re.M)
def spans(text):
    hs = [(m.start(), int(m.group(1))) for m in HEAD_RE.finditer(text)]
    return {n: len(text[s:(hs[i + 1][0] if i + 1 < len(hs) else len(text))].encode("utf-8")) for i, (s, n) in enumerate(hs)}
now = spans(sh("git", "show", f"{REF}:docs/DECISIONS.md"))
first = {}
log = sh("git", "log", "--first-parent", "--reverse", "--since=2026-08-06T00:00:00Z", "--format=%H %cs", REF, "--", "docs/DECISIONS.md")
for line in log.splitlines():
    h, day = line.split()
    for n, b in spans(sh("git", "show", f"{h}:docs/DECISIONS.md")).items():
        if n >= 167 and n not in first:
            first[n] = (b, day, h[:8])
rows = [(n, first[n][1], first[n][0], now.get(n, 0), first[n][2]) for n in sorted(first)]
cap = 6144
print("entries_ge_167", len(rows), "median_first_size", statistics.median(r[2] for r in rows))
print("first_size_over_cap", [(r[0], r[2], r[1]) for r in rows if r[2] > cap])
print("first_size_within_5pct_under_cap", sum(1 for r in rows if cap * 0.95 <= r[2] <= cap))
print("first_appearance_bytes_total", sum(r[2] for r in rows))
print("larger_now", sum(1 for r in rows if r[3] > r[2]), "bytes_added_after_landing", sum(r[3] - r[2] for r in rows if r[3] > r[2]))
print("smaller_now", [(r[0], r[3] - r[2]) for r in rows if r[3] < r[2]])
print("now_over_cap", sum(1 for r in rows if r[3] > cap), "all_live_over_1.2x_cap", sum(1 for b in now.values() if b > cap * 1.2))
same = collections.Counter(r[4] for r in rows)
print("commits_adding_2plus_entries", [(h, sorted(r[0] for r in rows if r[4] == h)) for h, k in same.items() if k > 1])
print("first_appearance", [(r[0], r[1], r[2]) for r in rows])
for lo, hi in [("2026-06-16", "2026-08-07"), ("2026-08-08", "2026-09-12"), ("2026-09-13", "2099-01-01")]:
    added = sh("git", "log", "--first-parent", "--diff-filter=A", "--name-only", "--format=", f"--since={lo}T00:00:00Z",
               f"--until={hi}T23:59:59Z", REF, "--", "docs/plans/PLAN-*.yaml").split()
    print("plan_files_added", lo, hi, len(set(added)))
```

Script C (census of the working tree, which is the audited commit after Section 5 step 1; feeds
G10, G11, G13, G14, G22, G23):

```python
import collections, json, pathlib, re, subprocess, yaml
REF = "origin/main"  # change to "HEAD" only if Section 5 step 1 fell back to local HEAD
def sh(*a):
    return subprocess.run(a, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
# contracts census
rows = []
for p in sorted(pathlib.Path("docs/contracts").glob("*.y*ml")):
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    c = d.get("contract") if isinstance(d.get("contract"), dict) else {}
    ev = c.get("evaluator") if isinstance(c.get("evaluator"), dict) else {}
    log = d.get("amendment_log") if isinstance(d.get("amendment_log"), list) else []
    born = sh("git", "log", "--diff-filter=A", "--follow", "--format=%cs", REF, "--", str(p)).split()[-1]
    rows.append((p.name, c.get("class"), c.get("status"), next(iter(ev), None), next(iter(ev.values()), None), len(log), born))
print("contracts", len(rows), "class", dict(collections.Counter(r[1] for r in rows)), "status", dict(collections.Counter(r[2] for r in rows)))
print("class_D_evaluator_kind", dict(collections.Counter(r[3] for r in rows if r[1] == "D")), "amendment_log_rows", sum(r[5] for r in rows))
late = [r for r in rows if r[6] > "2026-08-07"]
print("first_added_after_2026-08-07", len(late), "of_which_class_D", sum(1 for r in late if r[1] == "D"))
print("agent_surface", sorted((r[0], r[4]) for r in rows if r[3] == "agent_surface"))
print("checks_serving_2plus_contracts", [k for k, v in collections.Counter(r[4] for r in rows if r[3] == "check").items() if v > 1])
# citations of live Decisions by area
live = sorted(r["number"] for r in json.load(open("docs/decisions-index.json"))["decisions"] if r.get("live"))
cited = collections.defaultdict(set)
for line in sh("git", "grep", "-IoE", r"(Decision |dec-)[0-9]+", REF, "--", ".").splitlines():
    path, hit = line.split(":", 1)[1].rsplit(":", 1)
    n = int(re.search(r"(\d+)$", hit).group(1))
    if path.startswith("scripts/checks/"): area = "checks"
    elif path.startswith("docs/contracts/"): area = "contracts"
    elif path.startswith(".claude/") or path == "AGENTS.md" or path.endswith("CLAUDE.md"): area = "agent_surfaces"
    elif path.startswith("config/"): area = "config"
    elif path.startswith("tests/"): area = "tests"
    elif path.startswith(("scripts/", "src/", ".github/", "terraform/")): area = "other_mechanism"
    else: continue
    cited[area].add(n)
for label, nums in [("live", live), ("live_ge_167", [n for n in live if n >= 167])]:
    print(label, len(nums), {a: sum(1 for n in nums if n in cited[a]) for a in ("checks", "contracts", "agent_surfaces", "config", "tests")})
mech = set().union(*(cited[a] for a in ("checks", "contracts", "agent_surfaces", "config", "other_mechanism")))
print("live_cited_by_no_mechanism_area", [n for n in live if n not in mech])
# reversal-condition stanzas
text = open("docs/DECISIONS.md", encoding="utf-8").read()
fence = "`" * 3
stanzas = re.findall("^" + fence + r"yaml reversal-conditions\n(.*?)^" + fence, text, re.M | re.S)
print("stanzas", len(stanzas), "condition_kinds", dict(collections.Counter(k for s in stanzas for k in re.findall(r"^\s+kind:\s*(\S+)", s, re.M))))
# gate rounds recorded in plan files; the subset whose text contains a Decision header
gates, sub, with_rounds, sub_plans, plans = collections.Counter(), collections.Counter(), 0, 0, 0
for p in sorted(pathlib.Path("docs/plans").glob("PLAN-*.yaml")):
    plans += 1
    body = p.read_text(encoding="utf-8")
    try:
        gr = (yaml.safe_load(body) or {}).get("gate_rounds")
    except Exception:
        continue
    if isinstance(gr, list) and gr:
        with_rounds += 1
        pairs = [(str(r.get("gate")), str(r.get("verdict"))) for r in gr if isinstance(r, dict)]
        gates.update(pairs)
        if "## Decision " in body:
            sub_plans += 1
            sub.update(pairs)
print("plan_files", plans, "with_gate_rounds", with_rounds, "rows", sum(gates.values()))
print("gate_rounds", sorted(gates.items(), key=lambda kv: -kv[1]))
print("with_decision_header", sub_plans, sorted(sub.items(), key=lambda kv: -kv[1]))
# contract-changing commits that also changed the decision file, by period
for label, lo, hi in [("P0", "2026-06-16", "2026-08-07"), ("P1", "2026-08-08", "2026-09-12"), ("P2", "2026-09-13", "2099-01-01")]:
    out = sh("git", "log", "--first-parent", "--name-only", "--format=@%h", f"--since={lo}T00:00:00Z", f"--until={hi}T23:59:59Z", REF)
    total = both = 0
    for block in out.split("@")[1:]:
        files = block.splitlines()[1:]
        if any(f.startswith("docs/contracts/") for f in files):
            total += 1
            both += any(f == "docs/DECISIONS.md" for f in files)
    print("contract_commits", label, total, "also_changing_decisions", both)
```

IF a script errors: record the error in `meta.contract_notes`, use the row's quoted figures as
dated evidence, and mark what rests on them `confidence: HYPOTHESIS`. IF Section 5 step 1 left you
off the audit branch, Script C reads the wrong working tree: say so and treat its output the same
way.

**G1 The intervention's own record.**
- Earlier audit: `audits/contract-first-governance-33c8667.yaml` and `.md` (audited 2026-08-07).
  Its blocks `migration_sequence` (8 steps), `end_state.forcing_functions` (3 mechanisms) and
  `findings` (CFG-01 to CFG-11) state what was proposed.
- Roadmap item T2.56 "Normative / contract / ADR layering": `status: complete`,
  `completed_at: '2026-09-12'`, criteria c1 and c2 `met`.
- Decision 167 (2026-08-07): clause 1 envelope, clause 2 required significance claim, clause 3
  per-entry cap, clause 4 routing rule homed in the contract. Its blockquote update dated
  2026-08-17 records that a proposed routing-consistency lint was rejected and not built.
- Decision 168 (2026-08-10): Class D contracts with an evaluator that must resolve (clauses 1-4).
- Decision 179 (2026-08-31): clause 1 retires the three stock limits; clause 4 names what still
  governs: the per-entry cap for bytes per new entry, and the Decision 150 significance bar as
  "the retained lever on entry COUNT and triage" quality; clause 5 records two residuals -- the
  index is a "read-in-full-every-/plan surface with no bound", and in-place amendments are exempt
  from the cap.
- Decision 196 (2026-09-20): clause 1, decisions stay in git in every tier and the warehouse table
  is a read-side derivative, reversing the earlier plan to retire the files behind a read portal;
  clause 5, a decision ships in the release snapshot only if a shipped mechanism cites it; clause
  8, execution is post-MVP and rides T4.24.

**G2 Corpus flow by period** (Script A, Script B).

| Boundary | `docs/DECISIONS.md` bytes | Archive bytes | Live headers | Archive headers | Distinct numbers |
|---|---|---|---|---|---|
| 2026-06-15 | 156,171 | 93,598 | 48 | 21 | 67 |
| 2026-08-07 | 573,978 | 111,070 | 119 | 27 | 146 |
| 2026-09-12 | 706,357 | 111,192 | 140 | 27 | 167 |
| 197c800c (2026-11-16) | 1,212,174 | 111,193 | 212 | 27 | 239 |

New entries by distinct number: P0 79, P1 21, P2 72 (to `197c800c`). Plan files
(`docs/plans/PLAN-*.yaml`) first added on the first-parent history of main: P0 302, P1 110, P2 338.
The 2026-06-15 live file held one duplicated header and one entry that was also in the archive,
which is why `distinct_numbers` is the count to difference, not header counts.

**G3 Entry size at first appearance and now** (Script B; entries numbered 167 or higher).
- 93 such entries exist. Median size at first appearance: 5,798 bytes; together they were 511,737
  bytes at first appearance.
- 45 of the 93 first appeared between 5,837 and 6,144 bytes (within 5 percent under the cap).
- One first appeared over the cap: Decision 169 at 10,499 bytes on 2026-08-11. The cap became a
  hard failure on 2026-08-13 (commit `eaff8043`, "arm the per-entry decision cap").
- 69 of the 93 are larger now than at first appearance, by 65,129 bytes in total; 47 are now over
  6,144 bytes. One (Decision 239) is 2 bytes smaller.
- 37 live entries of any age exceed 7,372 bytes (1.2 times the cap).
- Eight commits each added two entries: 184+185, 196+197, 199+200, 211+212, 220+221, 232+233,
  252+253, 257+258.
- The cap and its tiers: `scripts/checks/decisions/validate_decisions_size.py:39`
  (`_PER_ENTRY_CAP_BYTES = 6_144`), `:66` (hard-fail for new entries only), `:93` and `:128` (two
  WARN-only legs that never append to `failed`). Contract text:
  `docs/contracts/decision-entry.yaml:571-599`.

**G4 Envelope fields, from `docs/decisions-index.json`** (re-derive:
`bin/venv-python -c "import json,collections as c; d=json.load(open('docs/decisions-index.json'))['decisions']; L=[r for r in d if r.get('live')]; print(len(d), len(L), c.Counter(r.get('currency') for r in L), c.Counter((r.get('kind') or {}).get('value') for r in L), sum(1 for r in L if r.get('serves')), sum(1 for r in L if r['number']>=167 and r.get('amends')))"`).
- 239 index rows; 212 live.
- Currency of live rows: `amended` 106, `current` 101, `superseded_compacted` 4,
  `superseded_pointer` 1.
- `kind` present on 18 live rows: `direction` 15, `mechanism` 3. `serves` non-empty on 2.
- 78 of the 93 entries numbered 167 or higher carry at least one `amends` target.
- Every one of the 93 has an envelope whose `significance.value` is `numbered_decision` (the index
  does not carry this field; read the envelopes in `docs/DECISIONS.md`).
- The check's rule: `scripts/checks/decisions/envelope_rules.py:28-48` rejects a
  `significance.value` whose routing row text contains "Never a numbered Decision".
- `docs/contracts/decision-kind.yaml:47-51`: `kind` "grants and gates nothing" until a later
  Decision defines a ladder; an entry without `kind` reads as `direction` (`:54`).
- Routing rule text: `docs/contracts/decision-entry.yaml:387-441` (`significance.bar`, five
  `routing` rows, `routing_rule`'s three questions, `standing_commitment`).

**G5 Stock limits.** `scripts/checks/decisions/validate_decisions_size.py:1-3` states the three
stock ceilings are retired by Decision 179. `config/prose_budgets.yaml:9-13` states the decision
files are deliberately absent from the prose byte-budget registry. At 2026-08-07 the limits were
120 live headers and 700,000 combined bytes (earlier audit report, table under "Arc A").

**G6 Append-only lock.** Decision 177 (2026-08-25) registered `validate_live_entry_immutability`
(its Decision section, clauses 1-5). Decision 178 clause 3 (2026-08-27) retired that guard, says
the append-only "dialect REMAINS policy", and calls re-landing the guard a "sanctioned follow-up";
the blockquote at the top of Decision 177 says the same. At `197c800c` no
file named `validate_live_entry_immutability.py` exists under `scripts/checks/`; outside the
decision files, plans and audits the name appears in
`config/agent/verification_registry/entries/deprecated/live-entry-guard-discriminates.yaml`, in a
contract value at `docs/contracts/decision-entry.yaml:588`, and in two docstrings
(`scripts/checks/decisions/validate_decisions_size.py:97` and its test module). Decision 177's Problem states a census "found confirmed drift in 55 of 126" live
entries under the earlier convention-only rule.

**G7 Outflow.** Archive headers: 27 at 2026-08-07, 27 at `197c800c` (G2). Four live entries are
compacted stubs and one is `superseded_pointer` (G4). `docs/decision-migration/MANIFEST.yaml`
(measured 2026-08-24) lists the citation-stranded class: Decision 37 `processed`, Decision 80
`enumerated_only`, Decisions 44 and 58 `already_conforming`; its `retires_when` condition is at
lines 24-28. Decision 179's Problem records that the archival wave "ran and was recorded EMPTY".

**G8 Index read.** `docs/decisions-index.json`: 110,608 bytes at 2026-08-07; 119,191 at
2026-09-12; 182,969 at `197c800c`. `.claude/skills/decision-scout/SKILL.md:26` (read
`docs/contracts/north-star.yaml` in full), `:28` (read `docs/decisions-index.json`; derive M = count
of live entries), `:48` (read only shortlisted sections of the source file), `:109` and `:125`
("Decisions triaged: N of M", N must equal M). `docs/contracts/instruction-architecture.yaml:210`
onward (`gate_subagent_retrieval`) gives the rationale. Roadmap item T4.24, criterion c7, names
T4.24 the owner of Decision 179's "clause 5(i) index-read residual"; T4.24 is `deferred_post_mvp`.

**G9 Planning read-set** (Script A, the `read_set` line; bytes at each boundary).

| File | 2026-08-07 | 2026-09-12 | 197c800c |
|---|---|---|---|
| `CLAUDE.md` | 11 | 11 | 11 |
| `AGENTS.md` | 33,190 | 18,931 | 16,725 |
| `.claude/commands/plan.md` | 10,775 | 14,130 | 15,490 |
| `.claude/skills/planning/SKILL.md` | 55,810 | 44,097 | 42,994 |
| `docs/PROJECT_CONTEXT.md` | 13,996 | 13,946 | 13,909 |
| `docs/contracts/file-router.yaml` | 23,833 | 31,482 | 44,878 |
| `docs/contracts/git-ops.yaml` | 0 | 14,335 | 52,970 |
| `docs/contracts/project-alignment.yaml` | 0 | 0 | 4,975 |
| `.claude/skills/decision-scout/SKILL.md` | 11,325 | 11,293 | 11,285 |
| `docs/contracts/north-star.yaml` | 0 | 0 | 16,715 |
| `docs/decisions-index.json` | 110,608 | 119,191 | 182,969 |
| `docs/contracts/instruction-architecture.yaml` | 5,871 | 12,771 | 17,795 |
| `.claude/skills/plan-critique/SKILL.md` | 16,645 | 18,621 | 18,025 |
| `docs/contracts/plan-obligations.yaml` | 0 | 8,962 | 13,796 |
| `docs/contracts/overseer-dispatch.yaml` | 28,279 | 37,711 | 41,215 |
| `docs/contracts/decision-entry.yaml` | 29,904 | 42,332 | 42,971 |
| Total | 340,247 | 387,813 | 536,723 |

A 0 means the file did not exist at that boundary. Where the workflow sends a planning run:
`.claude/commands/plan.md:10` (planning skill; `git-ops.yaml`), `:20` (read the preflight report),
`:32` (read `docs/PROJECT_CONTEXT.md` fully), `:45` (File Router), `:60` (`project-alignment.yaml`),
`:63` (decision-scout dispatch), `:68` (`overseer-dispatch.yaml` section), `:100` (plan-critique
dispatch). `logs/.preflight-report.json` was 152,341 bytes on 2026-11-16 (a runtime file, not in
git). The whole ambient instruction layer (`AGENTS.md`, every `CLAUDE.md`, every
`.claude/commands/*.md`, every `.claude/skills/*/SKILL.md`, `docs/PROJECT_CONTEXT.md`; Script A
`ambient_layer_bytes`) was 359,167 bytes at 2026-08-07, 313,698 at 2026-09-12 and 306,279 at
`197c800c`. Ambient byte budgets: `config/prose_budgets.yaml:37` (root load-set 16,736), `:57`
(planning skill 42,994); the header comment (lines 15-17) states budgets "ratchet DOWN only" and
equal each surface's current size; line 8 states commands are measured only and have no entry.

**G10 Contract population** (Script A for the table; Script C for the census).

| Boundary | Files | Bytes | Effective lines | Files at or over 475 effective lines | Files with a line over 1,000 chars |
|---|---|---|---|---|---|
| 2026-08-07 | 37 | 467,997 | 7,417 | 2 | 1 |
| 2026-09-12 | 47 | 775,981 | 11,595 | 6 | 1 |
| 197c800c | 77 | 1,900,857 | 22,722 | 20 | 15 |

- At `197c800c`: Class D 53, A 11, C 7, B 6. Status `ratified` 44, `active` 16, `provisional_v0`
  12, `draft` 5. 41 of the 77 were first added after 2026-08-07; 33 of those 41 are Class D.
- `amendment_log` rows summed over the 77 files: 633.
- Budget: `config/structural_size_budgets.yaml:94-95` (contracts class), `:13-16` (counting rule),
  `:35` (2,000-character long-line companion). Three contracts carry a raised budget with a
  `# raise-approved` marker (`:187-189`): `iam-simulate-fixture.yaml` 650 (649 effective lines at
  `197c800c`), `ops_recommendations.yaml` 556 (554), `data-modeling-standard.yaml` 533 (533).
- The declared sub-tree `docs/contracts/personas/` holds 10 files, 131,148 bytes. Its row is at
  `docs/contracts/contract-population.yaml:202-209`; the rule that its members are not counted as
  contracts is in `shape_grammar.extension_and_depth` (`:182-188`).
- Population gate: `docs/contracts/contract-population.yaml:313-331` (ratchet;
  `grandfathered_max: 16`, `status_active_max: 16`), `:278-294` (change detection:
  amendment_log on change, conformance loss), `:265-276` (subject uniqueness).

**G11 Evaluator resolution** (Script C). `docs/contracts/contract-population.yaml:211-241` defines
the two kinds. `scripts/checks/contracts/_population.py:315-357`: a `check` evaluator resolves when
the named check is registered and sequenced and its module (or a one-hop `scripts/` import)
contains the contract's basename as a string literal in executable context. `:301-312`: an
`agent_surface` evaluator resolves when the named path is a file whose text contains the contract's
basename. At `197c800c`, of the 53 Class D contracts 41 use `check` and 12 use `agent_surface`. The
12 and their targets: `_joins.yaml` and `red-main-recovery.yaml` ->
`.claude/skills/planning/SKILL.md`; `candidate-decision-ratification.yaml` ->
`.claude/skills/implement/SKILL.md`; `overseer-dispatch.yaml` ->
`.claude/skills/overseer/SKILL.md`; `project-alignment.yaml` -> `.claude/commands/plan.md`;
`deploy-paths.yaml` -> `AGENTS.md`; `package-registry-reservation.yaml` -> `docs/CLAUDE.md`;
`coordinator.yaml` -> `docs/contracts/instruction-architecture.yaml`;
`coordinator-harness-claude-projects.yaml` -> `docs/contracts/coordinator.yaml`;
`ducklake-partition-remediation.yaml` -> `docs/contracts/deploy-paths.yaml`;
`restore-rehearsal.yaml` -> `docs/DECISIONS.md`; `visual-grammar.yaml` ->
`docs/ROADMAP-SEMANTO.yaml`. Two check names each serve two contracts
(`validate_data_model_standard`, `validate_decision_entry_conformance`).

**G12 Currency consumers.** `.claude/skills/decision-scout/SKILL.md:57`: `superseded_compacted` is
the only value that demotes an entry; "`amended` is treated as `current` for now".
`docs/contracts/decision-entry.yaml:547-555` (`currency.consumer_contract`) states why
`superseded_pointer` and `amended` never filter.

**G13 Citations of live Decisions outside the corpus** (Script C; occurrences of `Decision N` or
`dec-N` in tracked files, by area; a citation is a mention, and whether it is a binding is yours to
judge). Of the 212 live entries: cited by at least one file under `scripts/checks/` 108; under
`docs/contracts/` 185; in agent surfaces (anything under `.claude/`, `AGENTS.md`, any `CLAUDE.md`)
76; under `config/` 134; under `tests/` 172. Of the 93 numbered 167 or higher: `scripts/checks/`
45, `docs/contracts/` 88, agent surfaces 25. Fourteen live entries are cited in none of
`scripts/`, `src/`, `docs/contracts/`, agent surfaces, `config/`, `.github/` or `terraform/`:
24, 25, 27, 41, 106, 109, 112, 121, 140, 141, 220, 241, 258, 262. Repository-wide,
`git grep -oIE 'Decision [0-9]+' 197c800c -- . | wc -l` prints 45,231 (the earlier audit
recorded 19,414 on 2026-08-07, before many plan files existed). Roadmap item T2.54 "Bidirectional
clause-to-check traceability" is `not_started` with both criteria open.

**G14 Monitored reversal conditions** (Script C). `docs/DECISIONS.md` holds 73 fenced
`yaml reversal-conditions` stanzas; their conditions number 223, of which 203 are `kind: manual`
and 20 `kind: repo_state`. `scripts/preflight/decision_conditions.py` reads them each session; the
preflight report key `decision_conditions.surfaced` listed one entry on 2026-11-16: Decision 133,
`manual-review-due`, `review_by: 2026-09-30`. The eight conditions Q8 reviews:

| Decision | Condition | Where in the entry | Review date |
|---|---|---|---|
| 167 | stanza id `mis-routed-past-both-gates` | its `yaml reversal-conditions` stanza | 2026-11-17 |
| 167 | prose (a): more than 2 of the first 10 entries under the cap cannot fit their decision content | its "Reversal conditions" paragraph | none stated |
| 167 | prose (b): two or more entries land in the same PR to evade the cap | same paragraph | none stated |
| 177 | stanza id `legitimate-shape-blocked-twice` | its stanza | 2026-11-25 |
| 177 | stanza id `t15-portal-cutover-retires-the-surface` (its note says Decision 196 clause 1 forecloses it) | its stanza | 2026-11-25 |
| 179 | stanza id `index-read-context-cost` | its stanza | 2026-12-01 |
| 179 | stanza id `inflow-unguarded` | its stanza | 2026-12-01 |
| 179 | stanza id `t15-portal-read` (its note says Decision 196 clause 1 forecloses it) | its stanza | 2026-12-01 |

Decision 167's prose conditions (c) and (d) are not reviewed: (c) is recorded as fired by the note
dated 2026-09-20 under that paragraph, and (d) concerned migration step 3 never landing, which
landed on 2026-08-13 (G3).

How to test the two prose conditions. (a): take the first 10 entries that first appeared on or
after 2026-08-13 (Script B's `first_appearance` list, by number). `fired` when for more than 2 of
them the entry, its envelope justification, or the message of the commit that added it says that
decision content (ruling, rationale or reversal conditions) was cut or moved out to fit the cap;
`not-fired` when that is so for at most 2. (b): look at the two-entry commits Script B lists.
`fired` when in at least one of them the two entries share a subject (one amends the other, or
both rule on the same mechanism) and their combined size at first appearance exceeds 6,144 bytes;
`not-fired` when none does.

Decision 179's `index-read-context-cost` is worded as a problem arising "before T2.56 c1 or T1.5 c1
lands"; T2.56 c1 is `met` (G1).

**G15 Adjacent records** (Script A unless stated).

| Boundary | `docs/plans/` files | `docs/plans/` bytes | Roadmap bytes (file plus archive) | `scripts/checks/**/*.py` | `tests/**/*.py` |
|---|---|---|---|---|---|
| 2026-08-07 | 522 | 13,368,522 | 633,110 | 131 | 541 |
| 2026-09-12 | 423 | 16,758,790 | 730,610 | 187 | 679 |
| 197c800c | 853 | 36,441,531 | 873,591 | 275 | 1,347 |

- Open recommendations (preflight report, 2026-11-16): `open_recommendations` 2,148;
  `aging_recommendations` 1,466; `non_automatable_recommendations` 1,940;
  `non_automatable_softcap_breached` true.
- Registered checks at `197c800c`: 184 (`pre` sequence 174, `full` sequence 185); re-derive with
  `bin/venv-python -c "from scripts.checks import registry; print(len(registry.all_checks()), len(registry.pre_sequence()), len(registry.full_sequence()))"`.
- `docs/CLAUDE.md` "Plans lifecycle" states completed `.yaml` plans remain at the `docs/plans/`
  root until a lagged sweep. Roadmap item T4.5 (plan projection) is `deferred_post_mvp`.
  `docs/ROADMAP-PLATFORM-ARCHIVE.yaml` exists since Decision 227 (2026-10-18).

**G16 References to retired mechanisms.**
- `.claude/commands/plan.md:41` tells the planning agent not to read `docs/DECISIONS.md` and
  describes it as "large -- near its Decision 134 size ceiling".
- Open rec-2933's title names "the surviving 132-header / 780,000-combined-byte ceilings"; open
  rec-3251's title begins "Retire the combined_max_bytes decision-corpus ceiling". Decision 179
  removed those constants on 2026-08-31.
- Roadmap item T1.5's criterion text describes a "synced hermetic cache" for recommendations; its
  `progress_note` dated 2026-10-10 states that phrasing no longer describes the recs read path.

**G17 Telemetry** (rows begin about 2026-11-01: `sessions_window` returned no rows for 2026-08-05,
2026-09-15 and 2026-10-14, and rows for 2026-11-01).
- CLI: `bin/venv-python -m scripts.telemetry_usage {models|tools|startup} --from YYYY-MM-DD --to YYYY-MM-DD [--by-root] [--by-agent]`
  (`scripts/telemetry_usage.py:1-12`). `models --by-root` prints one row per session tree and
  model, keyed by `harness_session_key`, with `tokens_input_total`,
  `tokens_cache_creation_total`, `tokens_cache_read_total`, `model_call_count`, `sessions`. A tree's
  rows fall in the window containing the day its root session STARTED. The CLI output has no
  `workflow` column; the underlying verb `model_usage_rollup` returns one.
- Join key: plan merge commits on main carry a `Claude-Session: https://claude.ai/code/session_...`
  trailer; the trailing `session_...` token equals telemetry's `harness_session_key`. List them:
  `git log --since=2026-11-01T00:00:00Z --grep='^plan(' --format='%h %cs %s | %(trailers:key=Claude-Session,valueonly,separator=%x20)' origin/main`.
- Dated sample (read 2026-11-16; trees started 2026-11-14): the tree behind merge commit
  `afe42535` -- 188 model calls, uncached input 392, cache-creation 612,227, cache-read 26,531,304,
  5 sessions; behind `52029fb9` -- 142 calls, 289, 425,304, 27,155,059, 3 sessions; behind
  `c893ba5b` and `8eff624a` (one tree, two plans) -- 214 calls, 434, 543,120, 32,585,104, 3
  sessions. All three trees carried `workflow: claude_code` in the verb's rows.
- Workflow values by session tree, for trees started 2026-11-10 to 2026-11-16 (read 2026-11-16;
  126 trees): `claude_code` 83, `implement` 20, `plan` 13, `ci_rca` 9, `audit` 1. Re-derive for any
  window of at most 7 days (one telemetry read):
  `bin/venv-python -c "import collections,sys; from scripts.aws_profile import resolve_aws_profile; from scripts.telemetry_capture.read_route import TelemetryReader; r=TelemetryReader(profile=resolve_aws_profile('agent_platform')); rows=r.named('model_usage_rollup', day_from=sys.argv[1], day_to=sys.argv[2]); print(dict(collections.Counter({x['root_session_id']: x.get('workflow') for x in rows}.values())))" 2026-11-10 2026-11-16`
- Dated sample, first model call of a session (`startup --by-agent`, 2026-11-09): root sessions on
  `agent/` branches (n=3) 65,183 to 78,113 input tokens; `general-purpose` sub-agents (n=8) 44,363
  to 49,642. A first call includes the harness's own system prompt and tool definitions as well as
  repository files.
- Compaction: `docs/contracts/telemetry_sessions.yaml:40` lists `compact` among the session
  lifecycle `event_kind` values. The verbs `sessions_window`, `session_state_and_duration`,
  `session_process_event_rollup`, `session_friction_rollup`, `model_usage_rollup`,
  `tool_use_rollup` and `session_startup_baseline` return no count of `compact` events (field lists:
  `src/common/ducklake_reader_client.py:192-204`, `src/common/event_usage_fields.py`).
- `.claude/settings.json:4` sets `CLAUDE_CODE_AUTO_COMPACT_WINDOW` to `1000000`; the setting was
  introduced on 2026-06-04 (commit `d576de4a`), before P0.

**G18 Owners already on record** (statuses read 2026-11-16; re-read before relying).
- Open recommendations: rec-3023 (bound and enforce Decision body content; acceptance names a
  `body_content` contract key and a `validate_decision_entry_shape` check); rec-3682 (Goodhart
  signature on the per-entry byte cap); rec-3250 (split the decision-scout triage read into slim
  index rows plus per-entry excerpts); rec-3264 (drift flow-control: stale-referent index
  projection, rolling drift census); rec-3265 (annotation sweep for 32 confirmed-drift live
  entries); rec-4119 (contract `amendment_log` accretion refills the 500-line cap; declare a
  retention rule); rec-3077 (wire decision-scout step 7 to the `amended` currency token); rec-3499
  (decision-scout 40-plan demotion condition has no accumulator); rec-4744 and rec-4008
  (`decision-entry.yaml` authoring gaps); rec-3048, rec-3049, rec-3050 (extractor grammar and
  projection residue); rec-3354 (two contracts disagree on one table's liveness); rec-3179
  (Decision 167 amendment exceeds the cap it installed); rec-5137 (route wording approvals by
  decision kind); rec-2933 and rec-3251 (G16).
- Closed or declined, for context: rec-3243 (closed; built the WARN accretion legs); rec-3249
  (closed; the one-off restructuring wave and the Decision 177 guard); rec-3246 (declined by the
  operator 2026-08-24: no standing mechanism for refactoring live entries); rec-3015 and rec-3016
  (closed; the planning skill's contract-first and amendment-form tests); rec-3059 (closed;
  evaluators built for grandfathered Class D contracts); rec-3012 (closed; index skeleton rows).
- Roadmap: T2.54 (traceability, `not_started`); T1.5 (`ops_decisions` graduation, `not_started`);
  T4.24 (`deferred_post_mvp`; carries Decision 196's execution and the index-read residual); T3.14
  (context budget as a tracked metric, `deferred_post_mvp`); T4.30 (decision-scout persona,
  `not_started`); T1.17 (`in_progress`, one criterion open).

**G19 Intervention components for the Q4 ledger.** L12 and L17 predate the window (Decisions 146
and 160, July 2026); they are listed because the intervention relies on them.

| ID | Component | Purpose tag | Anchor | Where exercised evidence would be |
|---|---|---|---|---|
| L1 | Typed metadata envelope on new entries | typed-metadata | Decision 167 cl.1; `decision-entry.yaml:314-385` | envelopes of entries 167 and up (G4) |
| L2 | Required significance routing claim | routing | Decision 167 cl.2; `envelope_rules.py:28-48` | G4; E1 bar test |
| L3 | Per-entry byte cap, hard-fail for new entries | entry-size | Decision 167 cl.3; `validate_decisions_size.py:66` | Script B first-appearance sizes after 2026-08-13 |
| L4 | WARN-tier accretion signal (two legs) | entry-size | `validate_decisions_size.py:93`, `:128`; `decision-entry.yaml:582-599` | G3 growth after landing; whether anything acted on the WARN |
| L5 | Planning-skill Decision Significance Gate (three property tests) | routing | `.claude/skills/planning/SKILL.md:214-236` | envelope justifications naming a rejected contract home (E1); E2 |
| L6 | Plan-critique category-consistency verdict | routing | `.claude/skills/plan-critique/SKILL.md:87-90` | plan-critique rounds in G22 for plan files with a Decision header; step 12p's own verdicts are not recorded separately |
| L7 | Class D contracts with a resolving evaluator | contract-enforcement | Decision 168; `_population.py:315` | G10, G11; E3 |
| L8 | Subject uniqueness and topic-equals-subject | contract-integrity | `contract-population.yaml:265-276` | `honesty_clauses` in the same file; rec-3354 |
| L9 | Population ratchet pins | contract-integrity | `contract-population.yaml:313-331` | pin values against Script C's status counts |
| L10 | Class D amendment_log-on-change and conformance-loss detection | contract-integrity | `contract-population.yaml:278-294` | G10 amendment_log rows; E2 samples |
| L11 | Skeleton index rows for archived entries | read-cost | `scripts/decisions_index.py`; rec-3012 | keys of `live: false` rows in the index |
| L12 | Archival wave | outflow | Decision 146; earlier audit step 6 | G7 |
| L13 | Migrate-then-rehome compaction and its manifest | outflow | Decision 175; `docs/decision-migration/MANIFEST.yaml` | the manifest's dispositions (G7) |
| L14 | Append-only lock on ratified bodies | record-integrity | Decisions 177, 178 (G6) | G6; Script B `smaller_now` |
| L15 | Stock limits on the corpus, retired with named retained levers | corpus-stock | Decision 179 cl.1, cl.4 | G2, G5 |
| L16 | Ambient prose shrink and ratchet-down byte budgets | read-cost | T2.56 c1; `config/prose_budgets.yaml` | G9 ambient layer bytes |
| L17 | Bounded retrieval by the decision scout | read-cost | Decision 160; `decision-scout/SKILL.md:28`, `:48` | G8; decision-scout rounds in G22 |
| L18 | Change-record content rule | routing | `docs/contracts/git-ops.yaml:175` | E1 change-record share; commit bodies of E2 samples |
| L19 | Decision `kind` and `serves` | typed-metadata | Decision 243 cl.8; `docs/contracts/decision-kind.yaml` | G4 |
| L20 | A scout BLOCK fails the plan PR | alignment | Decision 262 cl.1 | decision-scout verdicts in G22 |
| L21 | Monitored reversal-condition stanzas | reversal-monitoring | `scripts/preflight/decision_conditions.py` | G14 |

**G20 Baseline ratings and partition means** (from the earlier audit as amended on 2026-08-07):
EX1 partial, EX2 partial, EX3 partial, EX4 partial, EX5 partial, EX6 partial, EX7 missed, EX8 met,
EX9 met, EX10 partial, EX11 met, EX12 partial, EX13 partial. Its content partition of Decisions
157-166 gave mean byte shares of about 43 percent specification, 33 percent rationale, 19 percent
change-record and 6 percent other, single-rater, stated as plus or minus 10 points.

**G21 Hard limits on a record type's total** (Section 4 definition), as located at `197c800c`.
- Decision entries: none (G5).
- Decisions index: none; Decision 179 clause 1 removed its byte pin.
- Contracts: none on the total; the per-file budget is a flow governor (G10).
- Ambient instruction layer: per-surface byte budgets that equal the current size and only ratchet
  down (`config/prose_budgets.yaml`); commands have no budget.
- Plans: none (`config/structural_size_budgets.yaml:143-146`, class `workflow_outputs`,
  `governed: false`).
- Roadmap: `docs/ROADMAP-PLATFORM.yaml` may not exceed 10,000 lines
  (`scripts/checks/roadmap/validate_platform_roadmap.py:15`). It was 9,847 lines at 2026-09-12 and
  8,898 at `197c800c` (Script A `roadmap_lines`); retired items moved to the archive file under
  Decision 227.
- Open recommendations: none; `non_automatable_softcap_breached` is a preflight flag, not a
  failing check.
- Checks and tests: none on their count (the fast-tier time budget is out of scope).

**G22 Gate rounds recorded in plan files** (Script C). Of 754 `docs/plans/PLAN-*.yaml` files, 105
carry a `gate_rounds` list (a field newer plans have); the lists hold 1,075 rows. By gate and
verdict: plan-critique REVISE 378, plan-critique PROCEED 303, wording-check PASS 224,
decision-scout FLAGS_FOUND 136, wording-check FAIL 25, decision-scout NO_FLAGS 9. Sixteen of the
105 contain the text `## Decision ` (they draft or quote a Decision header); their rows:
plan-critique REVISE 54, wording-check PASS 50, plan-critique PROCEED 42, decision-scout
FLAGS_FOUND 34, wording-check FAIL 5, decision-scout NO_FLAGS 1. What a row records differs by
gate: plan-critique rows carry counts of blocking findings and nits; decision-scout rows carry
only gate, round and verdict (some also a `scout_input_fingerprint`); wording-check rows carry
`base`, `digest` and `selection`. No row records what a finding was, or which critique step
raised it.

**G23 Contract changes and the decision file** (Script C). First-parent commits on main that
changed any file under `docs/contracts/`, and how many of those also changed `docs/DECISIONS.md`:
P0 81 and 27; P1 59 and 27; P2 237 and 90 (to `197c800c`).

## 11. EMPIRICAL PASS

Tag every finding `evidence_kind`: `observed` when it rests on a sample row from E1-E5, a telemetry
reading, or a measurement of git history (Scripts A and B); `static` when it rests only on reading
files at the audited commit. When you order `top_improvements`, an observed finding outranks a
static one of equal severity. The sample sizes are hard limits -- do NOT exceed them. Give each
sample row an id: the decision number for E1, and `E2-01`, `E3-01`, `E4-01` onward for the others.

**E1 Content partition and bar test -- the 12 live entries with the highest decision numbers at
the audited commit.** For each:

1. Partition its bytes with the baseline's rule, quoted here exactly:

   > Unit: UTF-8 bytes per block (paragraph, list item, or numbered clause), attributed whole to one
   > class; an entry's span is its header line through the byte before the next '## Decision'
   > header. Classes, first match wins: (1) change-record -- states what changed in THIS landing or
   > its acute authoring-time state: measurements of the installing PR/corpus, runway or headroom
   > figures, sequencing/bundling narration, verification-round narrative, lists of files edited;
   > (2) specification -- states what is normatively true going forward, obeyable or checkable
   > without knowing why it was chosen: shapes, enums, grammars, thresholds, ceilings and their
   > values, procedures, scope/route sets, enforcement bindings; (3) rationale -- why this choice
   > over alternatives, rejected options, principles, cost/benefit, and reversal conditions; (4)
   > other -- header, Status/Date/Warehouse-ID markers, Related lists, amendment blockquotes,
   > separators. Tie-break: a sentence doing two jobs classifies by its dominant clause; a
   > specification stated only to describe a rejected alternative is rationale.

   Three additions for entries written after the baseline, none of which changes the rule for
   body text. The envelope (which did not exist at the baseline) is class (4). The structural
   elements class (4) lists are class (4) whatever they say, including dated annotations:
   blockquote lines beginning `> **` and trailing `[Amendment ...]` brackets (these are what C2
   counts); "first match wins" orders classes (1) to (3) for the remaining body text. A fenced
   `yaml reversal-conditions` stanza is class (3): the rule names reversal conditions there. Record
   the four shares to the nearest 5 points, and `spec_share` (Q1) from those recorded shares.
2. Bar test. Apply the three questions of `significance.routing_rule`
   (`docs/contracts/decision-entry.yaml:430-437`) to the entry's own clauses and classify:
   `clears-bar` | `bar-met-by-amends-edge-only` (it clears only because it amends an earlier
   entry) | `routable-elsewhere` (a routing row other than `numbered_decision` owns most of it;
   name the row) | `unclear`.
3. Binding test. Take the entry's main clause (its first numbered clause, or its Decision
   paragraph when it has no numbered clauses). Is there a check, a contract rule, an agent
   instruction or a human approval step that would fail, block or tell an agent to act if the
   repository violated that clause? `bound`: yes; name the artifact and where (`file:line`).
   `cited-only`: artifacts cite the entry's number, and none would react to a violation.
   `none-found`: neither.

Finish all 12 before computing the means; each mean is the plain mean of the 12 recorded values,
every entry weighted equally. Then set them beside G20.

**E2 Contract changes with and without a Decision -- at most 10 commits.** List the P2 `feat(`
commits on main that changed a contract, newest first, each marked by whether it also changed
`docs/DECISIONS.md`:

```bash
for c in $(git log --first-parent --since=2026-09-13T00:00:00Z --format=%h origin/main -- 'docs/contracts/*.yaml'); do if git show --name-only --format= "$c" | grep -q '^docs/DECISIONS.md$'; then m=with-decision; else m=no-decision; fi; git log -1 --format="$m %h %cs %s" "$c"; done | grep ' feat('
```

At `197c800c` this prints 214 commits: 130 `no-decision` and 84 `with-decision`. Group (a): the 3
newest and the 3 oldest `no-decision` commits. Group (b): the 2 newest and the 2 oldest
`with-decision` commits. For each sample read what the commit added to the contract or contracts
(`git show <sha> -- docs/contracts/`) and classify the main addition: `mechanism-rule` (a rule,
threshold, procedure or enforcement binding), `field-semantics` (the meaning or constraints of a
field or key), `operational-fact` (what is currently true, a status or a pointer), or
`housekeeping` (wording, renames, pin or count updates with no governing content). List any second
kind of addition in `also`. Quote the added line that best shows what kind of content it is.

Group (a) counterfactual. Read at most 6 of Decisions 157-166 (the entries the baseline
partitioned) as the comparison set, once, for all six samples. `would_have_minted_before` is `yes`
when a comparison entry recorded the same kind of content as a numbered Decision (name the entry
and clause in `evidence`), `no` when no comparison entry holds content of that kind, `unclear`
when the likeness is arguable. Write `overlap: n/a`.

Group (b) overlap. Read the Decision entry the same commit added or changed. `overlap` is `none`
when the entry gives the reason and points at the contract, `partial` when it restates some of
the contract's specification, `restated` when the same specification is in both. Write
`would_have_minted_before: n/a`.

**E3 Evaluator samples -- at most 12 Class D contracts.** Sort the 12 `agent_surface` contracts by
basename and take every second one starting with the first (6). Sort the `check` contracts by
basename and take every seventh starting with the first (up to 6). For each: pick the first
rule-like statement under the contract's first top-level key other than `contract` and
`amendment_log` (a statement that something must, must not or always holds). IF that key holds no
such statement, use the next top-level key; IF the contract holds none, write
`statement_tested: "none found"` and classify by what the evaluator does with the file as a whole.
Counterfactual: if the repository violated that statement, would the named evaluator fail, or would
a reading agent be told to act? For a `check`: read the check module -- does it parse the contract
and assert that statement, assert other content of it, read only its structure, or only name the
file? For an `agent_surface`: read the surface around the basename -- does it instruct an agent to
read or apply the contract at a defined trigger, or mention it? Classify: `asserts-this-statement`
| `asserts-other-content` | `reads-structure-only` | `instructs-agent-at-trigger` | `mention-only`.

**E4 Amended chains -- at most 6 entries.** Take the 6 live entries that are the target of the
most `amends` edges in `docs/decisions-index.json` (ties by lower number; list them with
`bin/venv-python -c "import json,collections as c; d=json.load(open('docs/decisions-index.json'))['decisions']; live={r['number'] for r in d if r.get('live')}; k=c.Counter(t for r in d for t in (r.get('amends') or []) if t in live); print(sorted(k.items(), key=lambda x: (-x[1], x[0]))[:6])"`).
For each: how many entries must be read to state the rule as it now stands, counting the entry
itself plus each entry with a direct `amends` edge to it (do not follow amenders of amenders);
does the amended entry itself carry an annotation naming each amender (count named against
edges); could a reader of that entry alone act on a clause a later entry changed -- `yes | no`,
with the clause.

**E5 Observed planning footprint -- at most 8 plan merges and at most 8 telemetry reads.** A
telemetry read is any call that reaches the telemetry reader: the CLI, the G17 one-liner, or a
`ducklake-reads` MCP tool. The Section 5 step 6 probe is the first of the 8. Skip the rest if
`meta.degraded_telemetry`. Run `models --by-root` (G17) for the 7 UTC days ending on the audit day
and for the 7 days before that (2 reads), and the G17 workflow one-liner for the 7 days ending on
the audit day (1 read; evidence for C19). From the
plan merge commits in those 14 days (G17 command), take the 8 most recent whose subject contains
`approved plan` and whose trailer key matches a `harness_session_key` in the output; a tree that
produced several sampled plans counts once and is noted. For each matched tree, summing its rows
across models, record first-time tokens (Trap 15), cache-read tokens, model calls and sessions.
Report median, minimum and maximum of first-time tokens. A tree may include work other than the
plan: say so; these are upper bounds for one plan. IF fewer than 3 trees match: report those that
did (none: `observed: []` and `null` summary fields), note it in `meta.contract_notes`, and do not
widen the window. Then compaction: with at most 2 further reads of your choice, try to read a
count of `compact` events for the same trees. IF you find no route: set `meta.compaction_unreadable = true`, write
`metrics.compaction.readable: false`, and adjudicate C18 on that evidence. Do not write telemetry
tenant ids, project ids or session ULIDs in either deliverable; identify a sample by its merge
commit short sha.

## 12. METHOD

- Phase 1: setup (Section 5), then read G1's sources: the earlier audit's report, Decisions 167,
  168, 177, 178, 179, 196.
- Phase 2: run Scripts A, B and C; fill `metrics` (DD-A); record mismatches with Section 10.
- Phase 3: trace the planning read path (DD-E) and the contract trajectory (DD-F).
- Phase 4: empirical pass E1-E5.
- Phase 5: fill `intervention_ledger`, `growth_ledger` and `condition_reviews`.
- Phase 6: dedup every prospective finding (Section 13); assign severity (Section 15); then
  adjudicate candidates C1-C20 (Section 2).
- Phase 7: rate the rubric (Section 8).
- Phase 8: answer Q1-Q8.
- Phase 9: only now, Q9 (`improvement_sequence`), then Q10.
- Phase 10, LAST: summary, per-surface maturity, the report, the pre-push gate (Section 16).

## 13. DEDUP DISCIPLINE

Before filing any finding, search the ownership surfaces: the roadmap (`tier_items[]` criteria and
`candidate_decisions[]`, by a `yaml.safe_load` projection or text search), `docs/DECISIONS.md`
headers and clauses, and open recommendations
(`bin/venv-python -m scripts.recs_related --terms "<terms>" --table [files...]`, paging with
`--exclude <comma-joined ids the previous page printed as next_exclude>`; at most 3 pages per
search). To read recs in full:
`bin/venv-python -c "from src.common.ducklake_reader_client import make_reader, fetch_recs_by_ids; import json,sys; print(json.dumps(fetch_recs_by_ids(make_reader(), sys.argv[1].split(','))[0], default=str, indent=1))" rec-3023,rec-3682`
(at most 50 ids per call). Run at most 40 of these two commands in total -- do NOT exceed.

IF a dedup command fails mid-run: retry once; if it still fails, set `meta.degraded_dedup = true`
and apply Section 5 step 4's rule to every finding not yet deduped.

Record `dedup_search_terms` and `dedup_hit_count` on the finding. An owner is a returned item (an
open recommendation, a roadmap criterion or a Decision clause) whose subject is this defect,
whether or not its remedy would close it; for a Decision clause the remedy is the rule it states.
`recs_related` always returns its nearest matches, so record the raw returned total in
`roadmap_crossref.note` and count only owners in `dedup_hit_count`. An owner means
`planned-insufficient` (its remedy would not close the defect), `planned-unbuilt` (it would, and
is not in progress) or a `covered` outcome (it would, and is in progress; Section 2) -- never a
fresh discovery. A `novel`
finding requires a recorded negative search (`dedup_hit_count: 0`); a `novel` finding without one
is `confidence: HYPOTHESIS`. A defect that came from no candidate and that an owner already has
built or in progress (Section 2's pinned meaning) is not filed at all.

DO NOT FLAG (deliberate constraints; you may assess how they are implemented and what they cost,
never whether they should exist):

- Decisions are stored in git in every tier; the warehouse table is a read-side derivative
  (Decision 196 clause 1). Do not propose retiring the files or making the warehouse the source.
- Decision numbers are never removed, reused or merged; headers always resolve (Decision 149,
  `decision-entry.yaml` `never_remove_headers`).
- `direction`-kind Decisions and north-star entries change only with explicit human approval
  (Decision 243 clauses 6 and 8).
- No standing mechanism for refactoring live, non-superseded entries (the operator's decline
  recorded on rec-3246, 2026-08-24, which applies Decision 163).
- The routing-consistency lint as specified in the earlier audit's amendment was rejected on
  construct-validity grounds (Decision 167, update of 2026-08-17). Do not re-propose it unless Q8
  finds Decision 167's stanza condition `mis-routed-past-both-gates` `fired`; that stanza's
  `on_trigger` then reopens the question. A different mechanism may be proposed on its merits.
- New enforcement is forward-only; entries and contracts that predate a rule are grandfathered
  (Decisions 167 and 168; `decision-entry.yaml` scope notes).
- `deferred_post_mvp` roadmap statuses (Decision 93): a deferral is not a defect. Recommending that
  an item be pulled forward is allowed, as a finding with `change_type: rescope`.
- The 500-SLOC and 500-effective-line file budgets and decompose-by-default (Decisions 128, 166).
- Ambient prose byte budgets ratchet down only (Decisions 43, 127 and 128;
  `config/prose_budgets.yaml`).
- No new standing prose architecture documents and no human-readable companion to a
  machine-readable source (Decision 86; `AGENTS.md` "Agent-First Repository").
- Recommendations have no local cache; reads go through bounded named verbs (Decision 215).
- The public-repository content boundary (Decision 101; `AGENTS.md`).

OPEN TO CHALLENGE (the requester opened these; a finding or Q9 step may propose changing them, with
evidence): the value of the per-entry cap and whether a byte cap is the right instrument; the
retirement of stock limits under Decision 179, whose own reversal conditions invite re-decision;
when the per-file decision split and release-snapshot trimming (Decisions 196 and 217, T4.24) are
executed; re-landing a guard for the append-only dialect (Decision 178 calls it a sanctioned
follow-up).

## 14. OUTPUT

Deliverable 1, `audits/contract-first-retrospective-<sha>.yaml`. Enums are pinned inline; `|`
separates allowed values.

```yaml
audit:
  meta:
    audited_commit: <origin/main short sha>
    base_branch: main
    model: <your self-reported model name, free text>
    methodology_version: 1
    scope_surfaces: [corpus, routing_gates, contracts, read_path, lifecycle, adjacent_records]
    degraded_dedup: false
    degraded_telemetry: false
    shallow_history: false
    web_unavailable: false
    compaction_unreadable: false
    external_sources: []          # [{title, url}], one per web lookup used
    contract_notes: ""
    stale_anchors: []             # [{anchor, expected, found}]
  metrics:
    periods:                      # exactly 3 rows: P0, P1, P2
      - {period: P0|P1|P2, from: "", to: "", days: 0, new_entries: 0, entries_per_day: 0.0,
         corpus_bytes_added: 0, bytes_per_day: 0, plan_files_added: 0, new_entries_per_plan_file: 0.0}
    cap: {entries_examined: 0, first_size_median: 0, first_appearance_bytes_total: 0,
          within_5pct_under_cap: 0, over_cap_at_first_appearance: 0, larger_now: 0,
          bytes_added_after_landing: 0, now_over_cap: 0, commits_adding_2plus_entries: 0}
    contracts_near_budget: []     # 5 rows (DD-F): {file, effective_lines, budget, amendment_log_share_pct}
    planning_context_footprint:
      band_assignment: []         # 16 rows: {file, band: always_read|gate_subagent|on_demand, basis: "file:line"}
      static:                     # exactly 3 rows: 2026-08-07, 2026-09-12, the audited commit
        - {boundary: "", read_set_bytes: 0, always_read_bytes: 0, gate_subagent_bytes: 0,
           on_demand_bytes: 0, token_estimate: 0}
      extra_files: []             # [{file, bytes_now, band, basis}] mandated every plan, outside the 16
      observed: []                # [{merge_commit, plans_in_tree: 1, model_calls: 0, sessions: 0,
                                  #   first_time_tokens: 0, cache_read_tokens: 0, note: ""}]
      observed_summary: {samples: 0, first_time_tokens_median: null, first_time_tokens_min: null,
                         first_time_tokens_max: null}
    compaction: {readable: true|false, sessions_examined: 0, sessions_with_compact_event: null,
                 method: ""}
  question_answers:
    - {q: Q1, verdict: shifted|partially-shifted|not-shifted|cannot-determine, basis: [], prose: ""}
    - {q: Q2, verdict: reduced|held|grown|cannot-determine,
       root_agent_direction: reduced|held|grown, gate_subagent_direction: reduced|held|grown,
       basis: [], prose: ""}
    - {q: Q3, verdict: enforced-by-default|mixed|mostly-asserted|cannot-determine, basis: [], prose: ""}
    - {q: Q4, verdict: all-holding|core-holding|core-gap, basis: [], prose: ""}
    - {q: Q5, verdict: sustainable|sustainable-with-named-gaps|unsustainable-on-current-trajectory|cannot-determine,
       any_limit_binds_within_30_days: yes|no,
       projections: [{horizon_days: 90, live_entries: 0, token_estimate: 0}],   # 3 rows: 90, 180, 365
       basis: [], prose: ""}
    - {q: Q6, verdict: sufficient|partial|insufficient, basis: [], prose: ""}
    - {q: Q7, verdict: improved|regressed|mixed|unchanged, basis: [], prose: "",
       external_checklist: [{property: EX1, rating: met|partial|missed, evidence: "",
                             baseline: met|partial|missed, delta: up|same|down}]}   # 13 rows, EX1..EX13
    - {q: Q8, basis: [], prose: ""}                 # no verdict; points at condition_reviews
    - {q: Q9, verdict: path-identified|no-change-warranted, basis: [], prose: ""}
    - {q: Q10, answers: [{question: "", answer: "", basis: []}]}
  candidate_adjudication:         # exactly 20 rows, C1..C20
    - {candidate: C1, outcome: finding|not-a-defect|false|covered|undetermined, findings: [], note: ""}
  intervention_ledger:            # exactly 21 rows, L1..L21
    - {id: L1, component: "", purpose: "<purpose tag from G19>",
       state: holding|holding-with-side-effect|eroded|retired-with-replacement|retired-without-replacement|never-exercised,
       exercised_evidence: "file:line|commit|sample id|G row", side_effect: "", basis: [],
       confidence: CONFIRMED|HYPOTHESIS}
  growth_ledger:                  # exactly 8 rows, in the order of the record_type enum
    - {record_type: decision_entries|decision_index|contracts|ambient_instruction_layer|plans|roadmap|open_recommendations|checks_and_tests,
       unit: "", stock_now: 0, flow_p0_per_day: null, flow_p1_per_day: null, flow_p2_per_day: null,
       governor: flow|stock|both|none, governor_anchor: "file:line|none",
       consumer_cost_model: ambient|per-plan|retrieved-by-id|on-demand|not-read-by-agents,
       hard_limit: "", binds_within_30_days: yes|no|n/a, headroom_note: "", basis: []}
  content_partition:
    entries:                      # at most 12 rows (E1)
      - {decision: 0, bytes: 0, specification_pct: 0, rationale_pct: 0, change_record_pct: 0, other_pct: 0,
         spec_share: 0, bar_test: clears-bar|bar-met-by-amends-edge-only|routable-elsewhere|unclear,
         routable_row: "", binding: bound|cited-only|none-found, binding_artifact: ""}
    means: {specification_pct: 0, rationale_pct: 0, change_record_pct: 0, other_pct: 0, mean_spec_share: 0}
    baseline_means: {specification_pct: 43, rationale_pct: 33, change_record_pct: 19, other_pct: 6,
                     mean_spec_share: 65}
  samples:                        # E2, E3, E4 rows
    - {id: E2-01, sample_set: E2, group: a|b, subject: "<commit short sha>",
       classification: mechanism-rule|field-semantics|operational-fact|housekeeping, also: [],
       would_have_minted_before: yes|no|unclear|n/a, overlap: none|partial|restated|n/a, evidence: ""}
    - {id: E3-01, sample_set: E3, subject: "<contract basename>", evaluator: "", statement_tested: "",
       classification: asserts-this-statement|asserts-other-content|reads-structure-only|instructs-agent-at-trigger|mention-only,
       evidence: "file:line"}
    - {id: E4-01, sample_set: E4, subject: "Decision <n>", entries_to_read: 0, amenders_named: 0,
       amend_edges: 0, stale_clause_actionable: yes|no, evidence: ""}
  condition_reviews:              # exactly 8 rows, the G14 table in order
    - {decision: 167, condition: "", verdict: fired|not-fired|foreclosed|cannot-determine, evidence: "",
       recommended_disposition: re-arm|keep-as-is|re-decide|retire-condition, rationale: "",
       confidence: CONFIRMED|HYPOTHESIS}
  improvement_sequence:           # 0 to 10 rows, in the order you would do them
    - {step: 1, action: "", closes: [], opportunity: false, moves: "", practice: "", practice_source: "",
       reuses_mechanism: "", effort: XS|S|M|L, reversible: true|false, requires_human_ruling: "",
       abort_criterion: "", expected_effect: "", blocked_behind: [], confidence: CONFIRMED|HYPOTHESIS}
  per_surface_assessment:         # exactly 6 rows
    - {surface: "", maturity: frontier|strong|solid|nascent, strengths: "", top_gaps: []}
  rubric_ratings:                 # 42 rows: 6 surfaces x VD1..VD7; cells outside Section 8's second table are n/a
    - {surface: "", dimension: VD1, rating: strong|adequate|weak|absent|n/a, evidence: "file:line|item-id", note: ""}
  findings:
    - {id: CFR-01, surface: "<surface>|shared", question: Q1, dimension: VD1,
       title: "", evidence: "file:line|item-id|sample id", evidence_kind: static|observed,
       current_behavior: "", ideal_behavior: "", gap: "", compensating_controls_considered: "",
       change_type: add|rescope|enforce|unify|persist|clarify|retune_gate|retire, proposed_change: "",
       acceptance: "", severity: critical|high|medium|low, severity_rationale: "",
       confidence: CONFIRMED|HYPOTHESIS,
       roadmap_crossref: {classification: novel|planned-insufficient|planned-unbuilt, item_ids: [],
                          dedup_search_terms: [], dedup_hit_count: 0, note: ""},
       effort: XS|S|M|L, depends_on: [],
       sequencing: {safe_to_queue_now: true|false, blocked_behind: [], note: ""}}
  rejected_candidates:
    - {candidate: C1, why_dismissed: "", compensating_control: "", control_property_match: "",
       decision_or_item_id: ""}
  summary: {total_findings: 0, novel_count: 0, planned_insufficient_count: 0, planned_unbuilt_count: 0,
            top_improvements: [], highest_leverage_change: null,
            maturity_corpus: "", maturity_routing_gates: "", maturity_contracts: "",
            maturity_read_path: "", maturity_lifecycle: "", maturity_adjacent_records: ""}
```

Field rules:

- `condition_reviews.verdict`: `foreclosed` means the condition can no longer fire as written: a
  later Decision removed its trigger, or an event it is conditioned on has passed; cite what
  foreclosed it. `recommended_disposition` follows the verdict: `fired` -> `re-decide` (send it to
  a planning session, as the stanza's `on_trigger` says); `foreclosed` -> `retire-condition`;
  `not-fired` or `cannot-determine` -> `re-arm` for a stanza condition (keep it and propose a new
  review date in `rationale`) or `keep-as-is` for a prose condition. For `cannot-determine`, name
  in `rationale` the evidence a reviewer would need.
- `improvement_sequence`: a step with `opportunity: false` has at least one finding id in `closes`;
  a step with `opportunity: true` has `closes: []`. `moves` names what it moves, for example
  "Q2 grown -> held" or "EX7 missed -> partial". `practice` is an EX id or a named practice;
  `practice_source` is a URL from `meta.external_sources` or "own knowledge".
- `intervention_ledger.state`: `holding` = in force and exercised with no adverse effect found;
  `holding-with-side-effect` = in force and exercised, with a measured unintended effect named in
  `side_effect`; `eroded` = in force on paper, and evidence shows it no longer constrains what it
  was built to constrain; `retired-with-replacement` / `retired-without-replacement` = removed by a
  recorded act, with or without another control taking over its purpose; `never-exercised` = built,
  and no instance of it firing or being used was found where G19's hint points. Where the place
  G19 points to cannot show a component firing (L6), do not use `never-exercised`: choose the state
  the other evidence supports and set `confidence: HYPOTHESIS`.
- `growth_ledger`: units are entries for `decision_entries` (Script A `distinct_numbers`); bytes for
  `decision_index`, `contracts` and `ambient_instruction_layer`; lines of
  `docs/ROADMAP-PLATFORM.yaml` for `roadmap` (Script A `roadmap_lines`, the unit its hard limit
  uses); files for `plans` and for `checks_and_tests` (Script A `check_py + tests_py`); a count
  for `open_recommendations` (the preflight report). `consumer_cost_model`: `ambient` = loaded
  into every session; `per-plan` = read once in every planning run; `retrieved-by-id` = read only
  when something cites it; `on-demand` = read when a task touches it; `not-read-by-agents` = no
  workflow sends an agent to it. A flow is the change between a period's two Script A boundaries divided by the
  period's days (Section 9 DD-A). Write `null` for `flow_p0_per_day` on `decision_index`,
  `contracts` and `checks_and_tests` (Trap 17), and for every flow of `open_recommendations` (no
  history in git). `hard_limit` names the limit from G21 or "none". Q5's
  `any_limit_binds_within_30_days` is `yes` when any row's `binds_within_30_days` is `yes`, else
  `no`.
- `findings`: `surface` is one of the six surfaces, or `shared` when the defect spans more than
  one (name them in `gap`). `question` is the one of Q1-Q10 the finding bears on most, and
  `dimension` the one of VD1-VD7. `change_type`: `add` a new mechanism; `rescope` move, split or
  re-time an existing item; `enforce` give an existing rule an evaluator; `unify` merge duplicate
  statements; `persist` record something not recorded; `clarify` wording only; `retune_gate`
  change a threshold or tier; `retire` remove a mechanism. `effort`: `XS` a wording or one-line
  change; `S` one small plan; `M` one plan touching several files and a check; `L` more than one
  plan. `sequencing.blocked_behind` holds finding ids or roadmap item ids;
  `improvement_sequence.blocked_behind` holds step numbers.
- `top_improvements` holds at most 5 finding ids. `highest_leverage_change` is a finding id, or
  `null` when there are no findings.

Invariants (state them as checked in the report's Intake section):

- COUNTING INVARIANT: `findings[]` is the SOLE enumerated list of defects. `total_findings =
  len(findings) = novel_count + planned_insufficient_count + planned_unbuilt_count`. Candidates that
  are covered, false or not defects live in `rejected_candidates`, NOT in findings. `metrics`,
  `question_answers`, `candidate_adjudication`, `intervention_ledger`, `growth_ledger`,
  `content_partition`, `samples`, `condition_reviews`, `improvement_sequence` and `rubric_ratings`
  are systems of record referenced FROM findings by id, never re-counted. `top_improvements` and
  every `basis`, `closes`, `top_gaps`, `depends_on` and `candidate_adjudication.findings` entry MUST
  be finding ids.
- COVERAGE: `candidate_adjudication` has exactly 20 rows. A row with outcome `finding` names at
  least one finding id. A row with outcome `not-a-defect`, `false` or `covered` has exactly one
  `rejected_candidates` row with the same `candidate`. A row with outcome `undetermined` has
  neither. `intervention_ledger` has 21 rows, `growth_ledger` 8, `condition_reviews` 8,
  `external_checklist` 13.
- `control_property_match` is REQUIRED whenever a compensating control is the reason for dismissal:
  name the property the control exercises, cite where it operates (mechanism or file:line), and
  state why the control would FAIL if the defect were real.
- CONFIRMED requires the behavior traced to file:line or to a sampled artifact; anything less is
  HYPOTHESIS.

Deliverable 2, `audits/contract-first-retrospective-<sha>.md`: the executive layer a human reads
first. At most 2,000 words by `wc -w` on the whole file. Sections, in order: Intake (audited
commit, set `meta` flags and their consequences, invariants checked); Verdicts (a table of the Q1-Q7
and Q9 verdicts, one line of basis each); What held and what did not (from the ledger); Cost and
growth (the pinned metrics, before and after); Reviews falling due (Q8's eight condition verdicts);
Improvement path (Q9); Not asked (Q10); What this audit did not file (rejected candidates in one
paragraph). Plain ASCII, no emojis.

## 15. SEVERITY + MATURITY

Assign severity AFTER judgment, by defect class, never from this prompt's framing or a candidate's
wording. The absence of a control is not by itself a severity: severity follows the consequence you
traced.

- critical = the governance system produces, on a path you traced, a wrong-but-trusted outcome: an
  agent, a check or a human approval acts on a rule that is superseded, contradicted or unenforced
  while the system presents it as current or enforced; or an irreversible act proceeds on an
  unsound verdict.
- high = a weakness that materially reduces a guarantee the intervention claims (routing,
  enforcement binding, bounded read cost, governed growth) AND whose compensating controls you
  judged insufficient; or a record type that reaches a hard limit within 30 days at the P2 rate
  with no lever that relieves it.
- medium = redundancy, ambiguity, inconsistency or unmetered growth with a clear fix.
- low = clarity or wording.

Compensating controls must PROPERTY-MATCH: the control exercises the same property AND would fail
if the defect were real (apply the counterfactual to the control itself). A control that cannot
catch the break neither lowers severity nor justifies dismissal.

Maturity is computed LAST, per surface, top-down, first match wins, counting that surface's
findings plus `shared` findings:

- `frontier` = 0 critical and 0 high findings AND every EX1-EX13 property in Q7's
  `external_checklist` rated `met` or `partial` -- never `missed`. The EX clause is repository-wide:
  one `missed` forecloses `frontier` on every surface.
- `strong` = 0 critical AND at most 1 high.
- `solid` = at most 1 critical.
- `nascent` = otherwise.

`frontier` stays reachable where you argued a property-matched compensating control; nothing in
this prompt forecloses it.

## 16. COMMIT / PR MECHANICS

1. The base was derived ONCE in Section 5 step 1 and you are on
   `audit/contract-first-retrospective-<sha>` created from it. This is a deliberate exception to the
   harness-assigned session branch (`docs/contracts/git-ops.yaml`): the audit needs a clean two-file
   diff off the audited base, and `audit/` is a registered extra push destination in that file. The
   branch base MUST equal `meta.audited_commit`; the branch name carries any `-rN` suffix Section 5
   step 1 added.
2. Pre-push gate: the YAML parses
   (`bin/venv-python -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" audits/contract-first-retrospective-<sha>.yaml`);
   the counting invariant and the coverage rule hold; the report is within 2,000 words; neither
   deliverable contains an AWS account id, an ARN, a credential, or a telemetry tenant id, project
   id or session ULID. Running `bin/venv-python -m scripts.validate --pre` is OPTIONAL (advisory
   outside CI); if you run it and an unrelated failure appears, record it in `meta.contract_notes`
   -- never fix it.
3. `git add` exactly the two deliverables, then
   `git commit -m "audit: contract-first governance retrospective"` (append any attribution trailer
   lines your harness instructs for commits) using the session's configured identity and signing
   (do not override the author, do not disable or force signing, do not amend). If a pre-commit hook
   rejects the commit, fix the cause inside your two deliverables and commit again; never bypass the
   hook. IF the cause lies outside your two deliverables: make no commit and end the turn with a
   final message naming the deliverable paths and the hook's output.
4. Run `git push -u origin HEAD` as a tool call on its own (no pipe, redirect or second command).
   On a network error retry up to 4 times. IF the push still fails, or fails for a non-network
   reason: do not open a PR; end the turn with a final message naming the branch, the commit sha,
   the exact error and the `summary:` block.
5. Open the PR via `mcp__github__create_pull_request` (owner `otl-labs`, repo `ontheloop`, base
   `main`, head your branch, ready for review, NOT draft), title
   `audit: contract-first governance retrospective (corpus, routing gates, contracts, read path, lifecycle, adjacent records)`,
   body = a 2-3 sentence lede plus the `summary:` block in a yaml fence, plus any PR footer your
   harness instructs. IF the tool is unavailable or the call fails: end the turn with a final
   message naming the pushed branch, the sha, the error and the intended title and body. Then END
   THE TURN -- do not poll, do not merge, do not subscribe, do not self-approve. The human disposes
   of the PR.

## 17. GUARDRAILS

- Write boundary (closed list): the ONLY repository files you create or modify are
  `audits/contract-first-retrospective-<sha>.yaml` and `audits/contract-first-retrospective-<sha>.md`.
  You do NOT edit Decisions, contracts, the roadmap, skills, commands, checks, code or workflows;
  do NOT file or update a recommendation; do NOT re-arm, edit or annotate any reversal-condition
  stanza (Q8 recommends; the human acts); do NOT deploy or change any cloud resource.
- Live systems: only the read commands Sections 5, 10, 11 and 13 allow, within their limits. No write
  verb, ever.
- Never write an AWS account id, ARN, credential, ExternalId, or telemetry tenant id, project id or
  session ULID in either deliverable or in the PR (public repository). Commit shas, file paths,
  Decision numbers, rec ids and aggregate token counts are fine.
- Precision over volume. Fewer than about 8 surviving findings is a valid result -- state it
  plainly; do not pad. "The intervention is working here" is a valid conclusion for any component
  the evidence supports, and belongs in the ledger and `rejected_candidates`, not in findings.
- A run that merely restates the candidates in Section 2 has failed. Every finding survives its
  dedup search (Section 13) and, where sampled, its counterfactual (Section 11).
- Do not let Q9 reach back into Q1-Q8: a verdict is not softened or hardened to make an
  improvement step look more or less needed.
- Propose no change the DO NOT FLAG list excludes, and no human-readable companion document.
- If an anchor does not resolve, record it in `meta.stale_anchors` and downgrade dependent items to
  HYPOTHESIS -- never invent, never abort.
