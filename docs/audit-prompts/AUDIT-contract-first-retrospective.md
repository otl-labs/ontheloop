# AUDIT: Contract-first governance retrospective

Execute this prompt exactly as written. It is self-contained: do not ask clarifying questions; where
something is unclear, take the named degraded path and record it in `meta.contract_notes`.

## 1. TASK

You are auditing, read-only, how one governance intervention in this repository has performed since
it landed, and what would improve it further. "The intervention" means the changes made between
2026-08-07 and 2026-09-12 under roadmap item T2.56 and Decisions 167, 168, 175, 177, 178 and 179,
together with the later changes that altered it (Decisions 196, 243 and 262). Its recorded purpose
was fourfold: stop agents recording specification and change narrative as numbered Decisions; send
that content to contracts and code instead; cut the context an agent must take in to work; and make
the growth of governance records sustainable. You assess six surfaces (Section 4): `corpus`,
`routing_gates`, `contracts`, `read_path`, `lifecycle` and `adjacent_records`. You answer ten
questions (Section 7): Q1 routing effect, Q2 context cost, Q3 enforcement, Q4 which components hold,
Q5 sustainability of growth, Q6 ownership of remaining gaps, Q7 industry-practice rating, Q8
reversal-condition reviews, Q9 improvement path, Q10 questions the requester did not ask.
Deliverables are exactly two files: `audits/contract-first-retrospective-<sha>.yaml` and
`audits/contract-first-retrospective-<sha>.md`, where `<sha>` is the short commit id Section 5 step 1
derives. The ONLY files you create or modify in the repository tree are those two deliverables;
regenerating the gitignored local files Section 5 names is expected and does not breach this (never
commit them). You draft; the human disposes: you propose, you change nothing else, and the human
decides what happens to every finding.

## 2. CANDIDATE OBSERVATIONS vs VERDICTS

This prompt hands you FACTS (Section 10) and CANDIDATES (below). It hands you no verdicts. Each
candidate is an observation plus the readings it could support; some readings say a mechanism works,
some say it does not. Adjudicating them is your job.

ASSUME NO CANDIDATE IS A REAL DEFECT UNTIL YOU TRACE IT. A run that merely confirms the candidates
below has failed. A run that finds the intervention working where the evidence says so has not
failed: "not-a-defect" is a first-class outcome.

Adjudicate every candidate to exactly one of:

- `confirmed-defect` -> a `findings[]` entry, `roadmap_crossref.classification: novel` (needs a
  recorded negative dedup search, Section 13).
- `planned-insufficient-or-unbuilt` -> a `findings[]` entry, classification `planned-insufficient`
  (an owner exists and its remedy would not close the defect) or `planned-unbuilt` (an owner exists,
  its remedy would close it, and it is not built).
- `planned-covered` -> a `rejected_candidates[]` entry naming the owning item, when a live owner
  fully covers it.
- `not-a-defect` -> a `rejected_candidates[]` entry naming the evidence or the compensating control.

Every candidate C1-C19 is accounted for in the output, either as a finding's `from_candidate` or as
a rejected candidate's `candidate`, never both. A candidate may yield more than one finding only if the
defects are distinct; then each finding carries the same `from_candidate`. Findings you discover
yourself carry `from_candidate: null`.

Candidates (facts are in the G rows named; re-derive before relying on any of them):

- C1 Cap proximity (G3). Many entries first appeared just under the 6,144-byte per-entry cap.
  Readings: the cap bounds entry size as designed; or the cap operates as a target and content is
  written to the limit instead of routed elsewhere.
- C2 Growth after landing (G3). Many entries are larger now than when they first appeared; the
  channel that grows them is metered at WARN tier only. Readings: sanctioned dated annotations doing
  their job; or an unbounded second inflow channel.
- C3 Entry rate (G2). New-entry rate per day and per plan file, by period. Readings: the rate moved
  with the intervention; or it tracks work volume and the intervention did not change it.
- C4 Routing claim (G4). Every entry carrying an envelope claims `numbered_decision`. Readings: the
  claim is a recorded audit trail and the real routing happens upstream at planning time; or nothing
  observable distinguishes a correctly routed entry from a misrouted one.
- C5 Stock guards (G5). No stock limit on the decision corpus has existed since 2026-08-31.
  Readings: correct once retrieval replaced wholesale loading; or growth is unguarded.
- C6 Append-only lock (G6). The guard for append-only decision bodies was registered on 2026-08-25
  and retired on 2026-08-27; no guard file existed at 137d6533. Readings: the convention
  holds without a guard; or ratified bodies can be rewritten without any check failing.
- C7 Outflow (G7). The archive header count has not changed since before the intervention.
  Readings: outflow is not needed under retrieval by id; or the corpus only grows.
- C8 Index read (G8). The decisions index is read in full by the decision-scout sub-agent on every
  plan and grows with the live entry count. Readings: a bounded, acceptable cost; or a per-plan cost
  that scales with corpus age.
- C9 Planning read-set (G9). The files a planning run is directed to read changed size by band
  between the period boundaries: some shrank, some grew, some are new. Readings: ambient load fell
  and the rest is read on demand; or load moved from ambient files to pointer targets and total
  first-time intake did not fall.
- C10 Contract growth and density (G10). Contract count, bytes, effective lines, files near the
  500-effective-line budget, bytes per effective line and long lines, by boundary. Readings:
  content arriving at its intended home; or the growth pattern the decision corpus had is
  reappearing in contracts, shaped by a line-count budget.
- C11 Evaluator kinds (G11). An `agent_surface` evaluator resolves when the named file's text
  contains the contract's basename. Readings: an instruction an agent reads is a real evaluator; or
  a mention is not enforcement.
- C12 Amended currency (G4, G12). About half of live entries have currency `amended`, and the
  decision scout treats `amended` as `current`. Readings: annotations make each amended entry
  self-explanatory; or a reader of an amended entry must chase a chain to learn the current rule.
- C13 Kind and serves (G4). `kind` is required on new entries and gates nothing yet; `serves` is
  optional. Readings: forward groundwork with low cost; or required authoring cost with no consumer.
- C14 Enforcement binding (G13). Counts of live Decisions cited by check modules, contracts and
  agent surfaces; the clause-to-check traceability item is not started. Readings: citation shows
  binding; or citation is mention and binding is unmeasured.
- C15 Reversal-condition monitor (G14). Most monitored conditions are `kind: manual` and surface
  only at their review date; one stanza's review date is in the past and it is still surfaced. Readings:
  a working reminder system; or reviews that depend on someone acting on the reminder.
- C16 Adjacent records (G15). Sizes and flows of plans, roadmap, open recommendations, checks and
  tests. Readings: normal growth of a working system, each with its own governor; or governance text
  displaced into record types with no governor.
- C17 References to retired mechanisms (G16). Live surfaces and open recommendations that describe
  limits or designs later retired. Readings: harmless residue; or stale guidance an agent will act on.
- C18 Compaction readability (G17). Session compaction is recorded as a telemetry lifecycle event;
  no rollup verb that counts it was located. Readings: readable by another route; or an operator
  baseline metric that cannot be read.
- C19 Planning-session attribution (G17). In a dated telemetry sample, the trees that produced
  merged plans carried `workflow: claude_code`, not `plan`. Readings: attribution works by another
  key; or per-workflow cost cannot be read for planning.

## 3. READ FIRST: disambiguation traps

- T1 "Decision". A numbered Decision is a `## Decision N:` entry in `docs/DECISIONS.md` or
  `docs/DECISIONS_ARCHIVE.md`. It is NOT a candidate decision (`CD.NN`, in the roadmap's
  `candidate_decisions[]`), NOT a row of the `ops_decisions` warehouse table (a read-side
  projection), and NOT the `condition_reviews` block of your own output.
- T2 "contract". A file under `docs/contracts/` with a `contract:` block. Classes A, B and C are
  "ritual" contracts (schema-validated field, verb and identifier contracts). Class D is a
  "mechanism" contract: free-form body plus a declared `subject` and `evaluator`. Not a Lambda API,
  not a legal contract.
- T3 `status: active` on a contract does NOT mean "in force" in the ordinary sense. It is a
  grandfather-only status: in use, never ritually ratified, and not declarable by a new file
  (`docs/contracts/contract-population.yaml` `grandfather_only_forms.status_active`).
- T4 "live", "current", "active". `live: true` in the index means the entry is in
  `docs/DECISIONS.md` (not the archive). `currency: current` is one of four derived values
  (`current | amended | superseded_pointer | superseded_compacted`). Neither is a contract status.
- T5 "north star". Section 6 of this prompt is the bar you judge against. It is NOT
  `docs/contracts/north-star.yaml` (the product's NS.1-NS.9), whose content is out of scope.
- T6 "kind". A Decision's `kind` is `direction | mechanism`. A reversal-condition's `kind` is
  `manual | repo_state`. They are unrelated.
- T7 "tier" and "class". Decision tiers A/B/C (Decision 196) are not contract classes A/B/C/D, not
  verification tiers V1-V3, not roadmap tiers T-1..T5, and not the fast/full presubmit tiers.
- T8 "S1..S8". In this repository S1, S2, S3, S4 and S8 name ambient prose surface classes
  (`config/prose_budgets.yaml`). This prompt's surfaces are named by word, never by S-number. The
  earlier audit's S1-S5 are its own surfaces, different again.
- T9 "T2.56 complete" does not mean every step of the earlier audit's eight-step sequence ran as
  written. Step 6 (archival wave) ran and was recorded empty. Step 8's portal cutover was reversed
  by Decision 196 clause 1. Judge components individually (Q4), not by the item's status.
- T10 Deletion event. Decision 178 (2026-08-27) deleted product code, plans and audits. Raw counts
  of plans, tests and scripts cross that event; a fall between 2026-08-07 and 2026-09-12 in those
  counts is the deletion, not a trend. No decision entry was removed or renumbered by it (Decision
  178 clause 3).
- T11 Numbering. Entries in `docs/DECISIONS.md` are ordered newest first. Numbers have gaps (for
  example 219, 259, 260, 261 have no header in either file). The count of live entries is the count
  of headers, never the highest number.
- T12 "significance". The envelope field `significance.value` cannot say "this should not have been
  a Decision": the check rejects the routing rows whose text says "Never a numbered Decision". The
  place a routing choice is made is the planning workflow, before an entry exists.
- T13 "amendment". A Decision is amended by a newer entry's `amends` edge and by dated annotation
  lines inside the amended entry. A contract's `amendment_log` is a different thing: a per-file
  change list that Class D change-detection requires.
- T14 Repository renames. The repository was renamed twice (Decisions 171 and 218). Older text,
  rec resolutions and the earlier audit use the former names; they refer to this repository.
- T15 "uncached tokens". Under prompt caching the telemetry field `tokens_input_total` (uncached
  input) is near zero because almost all new content is written to the cache. In this prompt
  "first-time tokens" means `tokens_input_total + tokens_cache_creation_total`: content taken in
  for the first time. `tokens_cache_read_total` is content re-read from cache on later calls.
- T16 The earlier audit. `audits/contract-first-governance-33c8667.yaml` and `.md` (audited
  2026-08-07) are the baseline. Their numbers describe the tree at that date. They are evidence
  about then, not about now.

## 4. SCOPE

Surfaces and their state:

| Surface | What it is | State |
|---|---|---|
| `corpus` | `docs/DECISIONS.md`, `docs/DECISIONS_ARCHIVE.md`, the authoring grammar `docs/contracts/decision-entry.yaml` and `docs/contracts/decision-kind.yaml`, the parser `scripts/decisions_md.py`, the checks under `scripts/checks/decisions/` | built |
| `routing_gates` | The authoring-time controls that decide whether content becomes a Decision: the planning skill's Decision Significance Gate, plan-critique step 12p, `scripts/roadmap/plan_obligations.py` draft check, the envelope rules in the conformance check, the decision-scout gate and its BLOCK rule (Decision 262) | built |
| `contracts` | `docs/contracts/*.yaml` (depth 1) and the declared sub-tree `docs/contracts/personas/`, the population gate `docs/contracts/contract-population.yaml`, `scripts/checks/contracts/validate_contract_drift.py` and `_population.py`, the contracts class in `config/structural_size_budgets.yaml` | built |
| `read_path` | How an agent takes governance in: the ambient layer (`AGENTS.md`, per-directory `CLAUDE.md`, commands, skills, `docs/PROJECT_CONTEXT.md`, budgeted by `config/prose_budgets.yaml`), `docs/decisions-index.json` and its generator `scripts/decisions_index.py`, the decision-scout skill, pointer targets a plan run is sent to | built. Designed and unbuilt: a decisions query verb (T1.5 c1), the decision-scout persona (T4.30), the per-file decision split and generated index (Decision 217, T4.24) |
| `lifecycle` | What happens to records after they land: archival (Decision 146), compaction and migrate-then-rehome (Decisions 149, 175), the append-only dialect (Decision 177), the currency projection, `validate_supersession_annotations`, the reversal-condition monitor `scripts/preflight/decision_conditions.py`, `docs/decision-migration/MANIFEST.yaml` | built; one guard retired (G6) |
| `adjacent_records` | Other record types that could absorb displaced governance text: `docs/plans/`, `docs/ROADMAP-PLATFORM.yaml` and its archive, the open-recommendation backlog, the check and test fleets | built. This is a BOUNDED LEDGER: you measure stock and flow, name the governor if one exists, and rate VD4 and VD7 only. You do not audit these systems. |

Periods (pin each boundary with
`git rev-list -1 --before=<date>T23:59:59Z origin/main`):

- P0, before: 2026-06-16 to 2026-08-07.
- P1, the intervention window: 2026-08-08 to 2026-09-12.
- P2, after: 2026-09-13 to the audited commit.

Vocabulary:

- Entry: one numbered Decision, measured as the UTF-8 byte span from its `## Decision N:` header to
  the byte before the next such header (or end of file).
- Envelope: the fenced `yaml` block directly under a header, required on entries new since
  Decision 167.
- Effective lines: non-blank lines whose first non-whitespace character is not `#`
  (`config/structural_size_budgets.yaml` counting rule).
- Planning read-set: the 16 files listed in G9.
- Planning context footprint: the first-time tokens (T15) a planning run needs, the root agent and
  its gate sub-agents together, before the plan is written. It has a static form (bytes of the
  read-set at a commit; tokens estimated as bytes / 4, the repository's own convention in
  `scripts/preflight/prose_context.py`) and an observed form (telemetry, Section 11 E5).
- Governor: a mechanism that fails or blocks when a quantity grows. A flow governor bounds what one
  change may add; a stock governor bounds the total.

Out of scope (one line each; do not file findings on them):

- Whether any individual Decision's ruling is right.
- CI run time, the fast-tier budget, merge throughput and test-suite design (owned by
  `audits/fast-tier-optimisation-3560909.md`, `audits/ci-merge-throughput-scaling-269e630a.yaml`,
  `audits/validate-test-suite-4df4d48.yaml`).
- The human-decision burden of the planning workflow as a topic of its own (owned by
  `audits/planning-decision-burden-735e69fc.yaml`); you may cite it as VD6 evidence.
- Roadmap sequencing, recommendation triage quality, and the telemetry pipeline's correctness.
- The content of `docs/contracts/north-star.yaml`, Terraform, IAM and deployment.

Trust-nothing clause: obtain every file, line and size by reading the repository at the audited
commit. Trust no number quoted in this prompt; re-derive it (Section 10 gives the commands) and
record any anchor or number that does not resolve in `meta.stale_anchors` as
`{anchor, expected, found}`.

## 5. SETUP

Run these first, in order. NEVER abort on a failure: take the named degraded path.

0. Hook canary (repository rule, `AGENTS.md`). Applies only to a top-level interactive session; a
   sub-agent, CI job or scheduled agent skips it. If your opening context has no `[hook_canary]`
   line from this repository's hooks, say "repo hooks not loaded" as your first status, write
   nothing, and end the turn. This is the one sanctioned stop in this prompt.
1. `git fetch origin main` then `git rev-parse --short origin/main`. This sha IS the audited tree;
   use it in the deliverable filenames, the branch name and `meta.audited_commit`. Then
   `git switch -c audit/contract-first-retrospective-<sha> origin/main`. IF that branch already
   exists (locally per `git branch --list`, or on the remote per
   `git ls-remote --heads origin <name>`), append `-r2` (then `-r3`, ...) to the branch name only;
   the deliverable filenames keep `<sha>`. IF fetch fails: use `git rev-parse --short HEAD`, branch
   off `HEAD`, and record "base is local HEAD" in `meta.contract_notes`. IF `git switch -c` fails
   for any other reason: record the error in `meta.contract_notes`, do the audit on the current
   branch, and at Section 16 skip steps 3-5: leave the two deliverables uncommitted and end the turn
   with a final message naming their paths and the error. IF the current branch is `main`, or you
   cannot write under `audits/`, write the two deliverables to a scratch directory outside the
   repository instead, skip Section 16 and end the turn with a final message naming those paths and
   the reason.
2. History depth: `git rev-parse --is-shallow-repository`. IF it prints `true`, run
   `git fetch --unshallow origin main` once. IF it still prints `true`: set
   `meta.shallow_history = true`; every period figure you cannot derive becomes `null`; use the
   G-row figures as dated evidence; and every answer or finding resting on them is
   `confidence: HYPOTHESIS`.
3. `bin/venv-python -m scripts.session.preflight --roadmap-detail full` -- writes
   `logs/.preflight-report.json` (gitignored; expected). IF it fails: record the error in
   `meta.contract_notes` and continue; read the roadmap directly (step 5).
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
   It prints one JSON object on stdout. IF it exits non-zero: set
   `meta.degraded_telemetry = true`, skip Section 11 E5, write `null` for every observed token
   figure, use the dated samples in G17 as your only observed numbers, and give every answer or
   finding resting on observed tokens `confidence: HYPOTHESIS`. Proceed.
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
- B4 Growth is governed at the flow. A corpus is sustainable when what each change may add is
  bounded and the cost of using the corpus does not grow with its size. A limit on the total that is
  raised when reached governs nothing.
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

**Q1 Routing effect.** Has what is written as a numbered Decision changed since the intervention,
and is the content it was meant to displace reaching contracts, code or the change record?
Evidence: the content partition of the newest entries against the 2026-08-07 baseline (E1), the
bar test on the same entries (E1), the routing traces (E2), and C1-C4.
Verdict: `shifted | partially-shifted | not-shifted | cannot-determine`.

**Q2 Context cost.** What does the decision and contract system cost an agent now against before?
Answer with two pinned metrics. (a) Planning context footprint, static: for 2026-08-07, 2026-09-12
and the audited commit, the read-set bytes split into three bands that YOU assign by tracing the
workflow text -- `always_read` (loaded ambiently or the workflow says to read it in full on every
plan), `gate_subagent` (read in full by the decision-scout or plan-critique sub-agent on every
plan), `on_demand` (a pointer the run follows only for some plans or reads in part). State each
band's direction of change. (b) Planning context footprint, observed, and sessions needing
compaction, from telemetry (E5); telemetry begins about 2026-11-01, so there is no observed
"before": say so. The verdict is your judgment of the net change in first-time intake a planning
run needs, root agent and gate sub-agents together, between 2026-08-07 and the audited commit; the
prose must name the band or bands that drove it and state any band that moved the other way.
Verdict: `reduced | held | grown | cannot-determine`.

**Q3 Enforcement.** The original complaint was that Decisions were rarely enforced. For entries
landed since Decision 167 and for Class D contracts: is each bound to something that fails, or that
an agent is instructed to apply at a defined trigger, when it is violated? Evidence: the evaluator
samples (E3), the deletion counterfactual in E1, C11 and C14.
Verdict: `enforced-by-default | mixed | mostly-asserted | cannot-determine`.

**Q4 Component ledger.** For each intervention component L1-L21 (G19), what state is it in? Fill
`intervention_ledger` (Section 14). The question's verdict summarises the ledger:
`intact` (no component `eroded` or `retired-without-replacement`), `intact-with-erosion` (one to
three such components, none of them the only control for its purpose), `materially-eroded` (more
than three, or any one that was the only control for its purpose).
Verdict: `intact | intact-with-erosion | materially-eroded`.

**Q5 Sustainability.** For each record type in `growth_ledger`: its stock, its flow before and
after, what governs it (flow, stock, both, none), how agents pay for it, and whether any hard limit
or cost threshold binds within 30 days at the P2 rate. Then answer for the system as a whole: does
the cost of one planning run stay flat as stock grows, and is any quantity on a path that forces an
emergency change? The sub-answer `binds_within_30_days` is arithmetic: for each quantity that has a
hard limit (a failing check, a context window, a file-size budget), divide remaining headroom by
the P2 daily rate. A quantity with no hard limit is `n/a`, and whether having no limit is itself a
problem is your judgment under B4.
Verdict: `sustainable | sustainable-with-named-gaps | unsustainable-on-current-trajectory | cannot-determine`.

**Q6 Gap ownership.** For the gaps you confirmed: does an open recommendation, roadmap item or
Decision already own each, and is that owner live (premise still true, remedy would close the gap,
not deferred past the point the gap bites)? Evidence: Section 13 searches and G18.
Verdict: `sufficient | partial | insufficient`.

**Q7 Industry practice.** Rate the routing architecture, repository-wide, against the EXTERNAL
CHECKLIST below -- the same closed list the 2026-08-07 audit used. Rate each property from present
evidence FIRST (`met | partial | missed`; `partial` requires an argued, property-matched
compensating control in `evidence`); only then read the baseline ratings in G20 and record
`baseline` and `delta` (`up | same | down`). EX1-EX13 is a CLOSED list: do not add properties.
Verdict, from the deltas: `improved` (at least one up, none down), `regressed` (at least one down,
none up), `mixed` (both), `unchanged` (none moved).

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
conditions listed in G14, decide from evidence whether it has fired, and recommend a disposition.
Fill `condition_reviews` (Section 14). This question's entry carries no verdict of its own; its
`prose` points at the block.

**Q9 Improvement path.** Given your answers to Q1-Q8: what ordered set of changes would move each
of Q1-Q6 that is not at its first-listed verdict to that verdict, and each EX property rated
`partial` or `missed` toward `met`? Each step names the industry practice it applies (an EX id, or
another named practice with a source) and the existing repository mechanism it reuses, if any.
Separately, name what is worth doing beyond closing defects, if anything. Fill
`improvement_sequence` (Section 14): at most 10 steps -- do NOT exceed -- of which at most 4 may be
opportunities with no finding behind them. An empty sequence is a valid result when the evidence
supports it. Do not propose a step the DO NOT FLAG list (Section 13) excludes.
Verdict: `path-identified` (every below-top verdict has a step), `path-partial` (some have none;
say which and why), `no-change-warranted`.

**Q10 Questions the requester did not think to ask.** Answer each seed below AND add any you
judge the requester will wish had been asked (at most 4 additions). Shape:
`{question, answer, basis: [finding ids]}`.

- Does the 6,144-byte cap now shape how entries are written (compression, abbreviation, clause
  packing) in a way that makes them harder for an agent to read correctly?
- Is `amends` the main way the corpus grows, and what does a chain of amendments cost a reader who
  lands on the oldest entry in it?
- Do contracts now show the growth pattern the decision corpus showed in P0, and is the
  500-effective-line budget changing how they are written?
- What share of entries landed since Decision 167 are themselves about governing decisions,
  contracts or this corpus?
- At what number of live entries does one plan's decision-scout triage stop being reliable or
  affordable, and what in the repository would show it happening?
- What would a steady state look like: what must leave, or stop costing, for entries to keep
  arriving at the P2 rate indefinitely?

## 8. RUBRIC

Rate each surface on each dimension: `strong | adequate | weak | absent | n/a`. `n/a` is correct
and costless where a dimension does not structurally apply; never manufacture a rating or a
finding to fill a cell. For `adjacent_records` rate VD4 and VD7 only; every other cell is `n/a`.

| ID | Dimension | Asks | Serves |
|---|---|---|---|
| VD1 | Routing correctness | Does content land in the record type B3 names for it? | Q1, Q7 |
| VD2 | Enforcement binding | Is each rule tied to something that fails or instructs when it is broken (B2)? | Q3, Q7 |
| VD3 | Read cost per use | What does one use cost a reader, and does that grow with stock (B1)? | Q2, Q5 |
| VD4 | Growth governance | Is there a flow governor, a stock governor, neither; what is the trajectory (B4)? | Q5, Q4 |
| VD5 | Lifecycle and currency | Can a record be retired, and can a reader tell from the record whether it governs (B5)? | Q4, Q7, Q8 |
| VD6 | Authoring and operator burden | What does writing it correctly cost the author and the human (B6, B7)? | Q9, Q10 |
| VD7 | Measurability | Can the system show whether this property holds or is regressing (B7)? | Q2, Q5, Q8 |

## 9. DEEP-DIVES

- DD-A Flow arithmetic (feeds Q1, Q5, Q8). Run Section 10's two scripts. Produce the `metrics`
  block: per period, new headers per day, corpus bytes per day, plan files added, new headers per
  plan file added; the cap figures; and inflow split into bytes of entries at first appearance and
  bytes added to entries after they landed. State what the per-plan rate shows that the per-day
  rate does not.
- DD-B What the newest entries contain (feeds Q1, Q3). Section 11 E1.
- DD-C Where displaced content went (feeds Q1). Section 11 E2.
- DD-D Whether evaluators evaluate (feeds Q3). Section 11 E3.
- DD-E Planning context footprint (feeds Q2, Q5). Trace what a planning run reads: start at
  `.claude/commands/plan.md`, follow each instruction to read, load or dispatch, and assign each of
  the 16 read-set files (G9) to a band (Q2). Add any file the workflow mandates on every plan that
  G9 omits, and say so in `meta.contract_notes`. Then the observed pass (Section 11 E5).
- DD-F Contract trajectory (feeds Q5, Q10). From G10 and the files themselves: is the contracts
  class absorbing content at a rate its governor bounds; what happens when a file reaches 500
  effective lines (look at the three files over budget and at two files within 10 lines of it);
  what share of a large contract is `amendment_log`.

## 10. GROUNDING MAP

This map spends your cognition on judgment, not on search. Every row is an observed fact with its
anchor, stated without a verdict. Verify before relying: line numbers are as of commit `137d6533`
(2026-11-16) and may have moved; re-resolve by searching for the quoted text. Record any mismatch
in `meta.stale_anchors`.

Two scripts re-derive the numeric rows. Run each as
`bin/venv-python - <<'EOF'` ... `EOF` from the repository root (they only read git objects).

Script A (boundary table; feeds G2, G8, G9, G10, G15):

```python
import re, subprocess
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
for label in ["2026-06-15", "2026-08-07", "2026-09-12", "HEAD"]:
    c = sh("git", "rev-parse", "origin/main").strip() if label == "HEAD" else \
        sh("git", "rev-list", "-1", "--before", label + "T23:59:59Z", "origin/main").strip()
    size = {}
    for line in sh("git", "ls-tree", "-r", "-l", c).splitlines():
        meta, path = line.split("\t", 1)
        s = meta.split()[3]
        size[path] = int(s) if s.isdigit() else 0
    def show(p):
        return sh("git", "show", f"{c}:{p}") if p in size else ""
    heads = lambda p: len(re.findall(r"^#{2,3} Decision \d+:", show(p), re.M))
    con = [p for p in size if re.fullmatch(r"docs/contracts/[^/]+\.ya?ml", p)]
    texts = [show(p) for p in con]
    effs = [eff(t) for t in texts]
    plans = [p for p in size if p.startswith("docs/plans/")]
    amb = [p for p in size if p == "AGENTS.md" or p.endswith("CLAUDE.md") or p == "docs/PROJECT_CONTEXT.md"
           or re.fullmatch(r"\.claude/commands/[^/]+\.md", p) or re.fullmatch(r"\.claude/skills/[^/]+/SKILL\.md", p)]
    print(label, c[:8],
          "| decisions_bytes", size.get("docs/DECISIONS.md", 0), "live_headers", heads("docs/DECISIONS.md"),
          "archive_headers", heads("docs/DECISIONS_ARCHIVE.md"), "index_bytes", size.get("docs/decisions-index.json", 0),
          "| contracts", len(con), "bytes", sum(size[p] for p in con), "eff_lines", sum(effs),
          "ge475", sum(1 for e in effs if e >= 475),
          "line_gt1000", sum(1 for t in texts if any(len(l) > 1000 for l in t.splitlines())),
          "| read_set_bytes", sum(size.get(p, 0) for p in READ_SET), "agents_md", size.get("AGENTS.md", 0),
          "ambient_layer_bytes", sum(size[p] for p in amb),
          "| plans_files", len(plans), "plans_bytes", sum(size[p] for p in plans),
          "| roadmap_bytes", size.get("docs/ROADMAP-PLATFORM.yaml", 0) + size.get("docs/ROADMAP-PLATFORM-ARCHIVE.yaml", 0),
          "| check_py", sum(1 for p in size if p.startswith("scripts/checks/") and p.endswith(".py")),
          "tests_py", sum(1 for p in size if p.startswith("tests/") and p.endswith(".py")))
```

Script B (first appearance of each entry numbered 167 or higher; plan files added per period;
feeds G2, G3). It reads every first-parent version of `docs/DECISIONS.md` since 2026-08-06 and can
take several minutes:

```python
import collections, re, statistics, subprocess
def sh(*a):
    return subprocess.run(a, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
HEAD_RE = re.compile(r"^## Decision (\d+):", re.M)
def spans(text):
    hs = [(m.start(), int(m.group(1))) for m in HEAD_RE.finditer(text)]
    return {n: len(text[s:(hs[i + 1][0] if i + 1 < len(hs) else len(text))].encode("utf-8")) for i, (s, n) in enumerate(hs)}
now = spans(sh("git", "show", "origin/main:docs/DECISIONS.md"))
first = {}
log = sh("git", "log", "--first-parent", "--reverse", "--since=2026-08-06", "--format=%H %cs", "origin/main", "--", "docs/DECISIONS.md")
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
print("larger_now", sum(1 for r in rows if r[3] > r[2]), "bytes_added_after_landing", sum(r[3] - r[2] for r in rows if r[3] > r[2]))
print("smaller_now", [(r[0], r[3] - r[2]) for r in rows if r[3] < r[2]])
print("now_over_cap", sum(1 for r in rows if r[3] > cap), "all_live_over_1.2x_cap", sum(1 for b in now.values() if b > cap * 1.2))
same = collections.Counter(r[4] for r in rows)
print("commits_adding_2plus_entries", [(h, sorted(r[0] for r in rows if r[4] == h)) for h, k in same.items() if k > 1])
for lo, hi in [("2026-06-16", "2026-08-07"), ("2026-08-08", "2026-09-12"), ("2026-09-13", "2099-01-01")]:
    added = sh("git", "log", "--first-parent", "--diff-filter=A", "--name-only", "--format=", f"--since={lo}T00:00:00Z",
               f"--until={hi}T23:59:59Z", "origin/main", "--", "docs/plans/PLAN-*.yaml").split()
    print("plan_files_added", lo, hi, len(set(added)))
```

IF a script errors: record the error in `meta.contract_notes`, use the row's quoted figures as
dated evidence, and mark what rests on them `confidence: HYPOTHESIS`.

**G1 The intervention's own record.**
- Earlier audit: `audits/contract-first-governance-33c8667.yaml` and `.md` (audited 2026-08-07).
  Its blocks `migration_sequence` (8 steps), `end_state.forcing_functions` (3 mechanisms) and
  `findings` (CFG-01 to CFG-11) state what was proposed.
- Roadmap item T2.56 "Normative / contract / ADR layering" (`docs/ROADMAP-PLATFORM.yaml:8565`):
  `status: complete`, `completed_at: '2026-09-12'`, criteria c1 and c2 `met`.
- Decision headers in `docs/DECISIONS.md`: 262 at line 5; 243 at 1285; 196 at 4571; 179 at 6019;
  178 at 6117; 177 at 6226; 175 at 6420; 168 at 6818; 167 at 6854.
- Decision 167 (2026-08-07): clause 1 envelope, clause 2 required significance claim, clause 3
  per-entry cap, clause 4 routing rule homed in the contract (lines 6873-6876). Its dated update of
  2026-08-17 records that a proposed routing-consistency lint was rejected and not built (6887-6896).
- Decision 168 (2026-08-10): Class D contracts with an evaluator that must resolve (6837-6840).
- Decision 179 (2026-08-31): retires the three stock limits (clause 1, 6052-6056); clause 4 names
  what still governs: the per-entry cap for bytes per new entry, and the Decision 150 significance
  bar as "the retained lever on entry COUNT and triage quality" (6071-6076); clause 5 records two
  residuals -- the index is "a read-in-full-every-/plan surface with no bound", and in-place
  amendments are exempt from the cap (6078-6083).
- Decision 196 (2026-09-20) clause 1: decisions stay in git in every tier; the warehouse table is a
  read-side derivative; this reverses the earlier plan to retire the files behind a read portal
  (4597). Clause 5: a decision ships in the release snapshot only if a shipped mechanism cites it
  (4601). Clause 8: execution is post-MVP and rides T4.24 (4604).

**G2 Corpus flow by period** (Script A, Script B).

| Boundary | `docs/DECISIONS.md` bytes | Live headers | Archive headers |
|---|---|---|---|
| 2026-06-15 | 156,171 | 48 | 21 |
| 2026-08-07 | 573,978 | 119 | 27 |
| 2026-09-12 | 706,357 | 140 | 27 |
| 137d6533 (2026-11-16) | 1,206,632 | 211 | 27 |

Plan files (`docs/plans/PLAN-*.yaml`) first added on the first-parent history of main: P0 302,
P1 110, P2 338 (to 137d6533). Six entries moved from live to archive during P0 (archive headers
21 to 27), so live-header growth understates new entries in P0 by six.

**G3 Entry size at first appearance and now** (Script B; entries numbered 167 or higher).
- 92 such entries exist. Median size at first appearance: 5,834 bytes.
- 45 of the 92 first appeared between 5,837 and 6,144 bytes (within 5 percent under the cap).
- One first appeared over the cap: Decision 169 at 10,499 bytes on 2026-08-11. The cap became a
  hard failure on 2026-08-13 (commit `eaff8043`, "arm the per-entry decision cap").
- 69 of the 92 are larger now than at first appearance, by 65,129 bytes in total; 47 are now over
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
- 238 index rows; 211 live.
- Currency of live rows: `amended` 106, `current` 100, `superseded_compacted` 4,
  `superseded_pointer` 1.
- `kind` present on 17 live rows: `direction` 15, `mechanism` 2. `serves` non-empty on 2.
- 78 of the 92 entries numbered 167 or higher carry at least one `amends` target.
- Every one of the 92 has an envelope whose `significance.value` is `numbered_decision` (the index
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
(`docs/DECISIONS.md:6263-6286`). Decision 178 clause 3 (2026-08-27) retired that guard and states
"The append-only correction dialect REMAINS policy; re-landing the guard re-anchored to the
post-cleanse corpus is a sanctioned follow-up" (6163-6169); the same note is on Decision 177
(6246-6250). At 137d6533 no file named `validate_live_entry_immutability.py` exists under
`scripts/checks/`; the name appears in
`config/agent/verification_registry/entries/deprecated/live-entry-guard-discriminates.yaml` and in
comments. Decision 177's Problem states a census found "confirmed drift in 55 of 126" live entries
under the earlier convention-only rule (6256-6260).

**G7 Outflow.** Archive headers: 27 at 2026-08-07, 27 at 137d6533 (G2). Four live entries are
compacted stubs and one is `superseded_pointer` (G4). `docs/decision-migration/MANIFEST.yaml`
(measured 2026-08-24) lists the citation-stranded class: Decision 37 `processed`, Decision 80
`enumerated_only`, Decisions 44 and 58 `already_conforming`; its `retires_when` condition is at
lines 24-28. Decision 179's Problem records that the archival wave "ran and was recorded EMPTY"
(6043-6044).

**G8 Index read.** `docs/decisions-index.json`: 110,608 bytes at 2026-08-07; 119,191 at
2026-09-12; 181,959 at 137d6533. `.claude/skills/decision-scout/SKILL.md:26` (read
`docs/contracts/north-star.yaml` in full), `:28` (read `docs/decisions-index.json`; derive M = count
of live entries), `:48` (read only shortlisted sections of the source file), `:109` and `:125`
("Decisions triaged: N of M", N must equal M). `docs/contracts/instruction-architecture.yaml:210-`
`gate_subagent_retrieval` gives the rationale. Roadmap item T4.24 criterion c7 names T4.24 "the new
owner of Decision 179's clause 5(i) index-read residual"; T4.24 is `deferred_post_mvp`
(`docs/ROADMAP-PLATFORM.yaml:7551`).

**G9 Planning read-set** (Script A `READ_SET`; bytes at each boundary).

| File | 2026-08-07 | 2026-09-12 | 137d6533 |
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
| `docs/decisions-index.json` | 110,608 | 119,191 | 181,959 |
| `docs/contracts/instruction-architecture.yaml` | 5,871 | 12,771 | 17,795 |
| `.claude/skills/plan-critique/SKILL.md` | 16,645 | 18,621 | 18,025 |
| `docs/contracts/plan-obligations.yaml` | 0 | 8,962 | 13,796 |
| `docs/contracts/overseer-dispatch.yaml` | 28,279 | 37,711 | 41,215 |
| `docs/contracts/decision-entry.yaml` | 29,904 | 42,332 | 42,971 |
| Total | 340,247 | 387,813 | 535,713 |

A 0 means the file did not exist at that boundary. Where the workflow sends a planning run:
`.claude/commands/plan.md:10` (planning skill; `git-ops.yaml`), `:20` (read the preflight report),
`:32` (read `docs/PROJECT_CONTEXT.md` fully), `:45` (File Router), `:60` (`project-alignment.yaml`),
`:63` (decision-scout dispatch), `:68` (`overseer-dispatch.yaml` section), `:100` (plan-critique
dispatch). `logs/.preflight-report.json` was 152,341 bytes on 2026-11-16 (a runtime file; its size
is not in git). The whole ambient instruction layer (`AGENTS.md`, every `CLAUDE.md`, every
`.claude/commands/*.md`, every `.claude/skills/*/SKILL.md`, `docs/PROJECT_CONTEXT.md`; Script A
`ambient_layer_bytes`) was 359,167 bytes at 2026-08-07, 313,698 at 2026-09-12 and 306,279 at
137d6533. Ambient byte budgets: `config/prose_budgets.yaml:37` (root load-set 16,736), `:57`
(planning skill 42,994); the header comment (lines 15-17) states budgets "ratchet DOWN only" and
equal each surface's current size.

**G10 Contract population** (Script A; depth-1 `docs/contracts/*.yaml` and `*.yml`).

| Boundary | Files | Bytes | Effective lines | Files at or over 475 effective lines | Files with a line over 1,000 chars |
|---|---|---|---|---|---|
| 2026-08-07 | 37 | 467,997 | 7,417 | 2 | 1 |
| 2026-09-12 | 47 | 775,981 | 11,595 | 6 | 1 |
| 137d6533 | 77 | 1,899,953 | 22,720 | 20 | 15 |

- At 137d6533: Class D 53, A 11, C 7, B 6. Status `ratified` 44, `active` 16, `provisional_v0` 12,
  `draft` 5. 41 of the 77 were first added after 2026-08-07; 33 of those 41 are Class D.
- `amendment_log` entries summed over the 77 files: 631.
- Budget: `config/structural_size_budgets.yaml:94-95` (contracts class), `:13-16` (counting rule),
  `:35` (2,000-character long-line companion). Three contracts carry a raised budget with a
  `# raise-approved` marker (`:187-189`): `iam-simulate-fixture.yaml` 650, `ops_recommendations.yaml`
  556, `data-modeling-standard.yaml` 533. The longest line in any contract is 1,968 characters.
- The declared sub-tree `docs/contracts/personas/` holds 10 files, 131,148 bytes; its members are
  not counted as contracts (`docs/contracts/contract-population.yaml:202-209`).
- Population gate: `docs/contracts/contract-population.yaml:313-331` (ratchet;
  `grandfathered_max: 16`, `status_active_max: 16`), `:278-294` (change detection:
  amendment_log on change, conformance loss), `:265-276` (subject uniqueness).

**G11 Evaluator resolution.** `docs/contracts/contract-population.yaml:211-241` defines the two
kinds. `scripts/checks/contracts/_population.py:315-357`: a `check` evaluator resolves when the
named check is registered and sequenced and its module (or a one-hop `scripts/` import) contains
the contract's basename as a string literal in executable context. `:301-312`: an `agent_surface`
evaluator resolves when the named path is a file whose text contains the contract's basename.
At 137d6533, of the 53 Class D contracts 41 use `check` and 12 use `agent_surface`. The 12 and
their targets: `_joins.yaml` and `red-main-recovery.yaml` -> `.claude/skills/planning/SKILL.md`;
`candidate-decision-ratification.yaml` -> `.claude/skills/implement/SKILL.md`;
`overseer-dispatch.yaml` -> `.claude/skills/overseer/SKILL.md`; `project-alignment.yaml` ->
`.claude/commands/plan.md`; `deploy-paths.yaml` -> `AGENTS.md`;
`package-registry-reservation.yaml` -> `docs/CLAUDE.md`; `coordinator.yaml` ->
`docs/contracts/instruction-architecture.yaml`; `coordinator-harness-claude-projects.yaml` ->
`docs/contracts/coordinator.yaml`; `ducklake-partition-remediation.yaml` ->
`docs/contracts/deploy-paths.yaml`; `restore-rehearsal.yaml` -> `docs/DECISIONS.md`;
`visual-grammar.yaml` -> `docs/ROADMAP-SEMANTO.yaml`. Two check names each serve two contracts
(`validate_data_model_standard`, `validate_decision_entry_conformance`).

**G12 Currency consumers.** `.claude/skills/decision-scout/SKILL.md:57`: `superseded_compacted` is
the only value that demotes an entry; "`amended` is treated as `current` for now".
`docs/contracts/decision-entry.yaml:547-555` (`currency.consumer_contract`) states why
`superseded_pointer` and `amended` never filter.

**G13 Citations of live Decisions outside the corpus** (occurrences of `Decision N` or `dec-N` in
tracked files, by area; a citation is a mention, and whether it is a binding is yours to judge).
Of the 211 live entries: cited by at least one file under `scripts/checks/` 108; under
`docs/contracts/` 184; in agent surfaces (`.claude/`, `AGENTS.md`, `CLAUDE.md` files) 72; under
`config/` 135; under `tests/` 172. Of the 92 numbered 167 or higher: `scripts/checks/` 45,
`docs/contracts/` 87, agent surfaces 23. Fourteen live entries are cited by none of
`scripts/`, `src/`, `docs/contracts/`, agent surfaces, `config/`, `.github/` or `terraform/`:
24, 25, 27, 41, 106, 109, 112, 121, 140, 141, 220, 241, 258, 262. Repository-wide,
`git grep -oIE 'Decision [0-9]+' -- . | wc -l` printed 45,197 at 137d6533 (the earlier audit
recorded 19,414 on 2026-08-07, before many plan files existed). Roadmap item T2.54 "Bidirectional
clause-to-check traceability" is `not_started` with both criteria open
(`docs/ROADMAP-PLATFORM.yaml:8493`).

**G14 Monitored reversal conditions.** `docs/DECISIONS.md` holds 72 fenced
`yaml reversal-conditions` stanzas; their conditions number 219, of which 199 are `kind: manual`
and 20 `kind: repo_state`. `scripts/preflight/decision_conditions.py` reads them each session; the
preflight report key `decision_conditions.surfaced` listed one entry on 2026-11-16: Decision 133,
`manual-review-due`, `review_by: 2026-09-30`. The eight conditions Q8 reviews:

| Decision | Condition | Where | Review date |
|---|---|---|---|
| 167 | stanza id `mis-routed-past-both-gates` | `docs/DECISIONS.md:6898-6906` | 2026-11-17 |
| 167 | prose (a): more than 2 of the first 10 entries under the cap cannot fit their decision content | `:6878` | none stated |
| 167 | prose (b): two or more entries land in the same PR to evade the cap | `:6878` | none stated |
| 177 | stanza id `legitimate-shape-blocked-twice` | `:6304-6316` | 2026-11-25 |
| 177 | stanza id `t15-portal-cutover-retires-the-surface` (its note says Decision 196 clause 1 forecloses it) | `:6312-6315` | 2026-11-25 |
| 179 | stanza id `index-read-context-cost` | `:6093-6108` | 2026-12-01 |
| 179 | stanza id `inflow-unguarded` | `:6101-6103` | 2026-12-01 |
| 179 | stanza id `t15-portal-read` (its note says Decision 196 clause 1 forecloses it) | `:6104-6107` | 2026-12-01 |

**G15 Adjacent records** (Script A unless stated).

| Boundary | `docs/plans/` files | `docs/plans/` bytes | Roadmap bytes (file plus archive) | `scripts/checks/**/*.py` | `tests/**/*.py` |
|---|---|---|---|---|---|
| 2026-08-07 | 522 | 13,368,522 | 633,110 | 131 | 541 |
| 2026-09-12 | 423 | 16,758,790 | 730,610 | 187 | 679 |
| 137d6533 | 853 | 36,441,501 | 873,591 | 275 | 1,346 |

- Open recommendations (preflight report, 2026-11-16): `open_recommendations` 2,148;
  `aging_recommendations` 1,466; `non_automatable_recommendations` 1,940;
  `non_automatable_softcap_breached` true.
- Registered checks on 2026-11-16: 184 (`pre` sequence 174, `full` sequence 185); re-derive with
  `bin/venv-python -c "from scripts.checks import registry; print(len(registry.all_checks()), len(registry.pre_sequence()), len(registry.full_sequence()))"`.
- `docs/CLAUDE.md` "Plans lifecycle" states completed `.yaml` plans remain at the `docs/plans/`
  root until a lagged sweep. Roadmap item T4.5 (plan projection) is `deferred_post_mvp`.
  `docs/ROADMAP-PLATFORM-ARCHIVE.yaml` exists since Decision 227 (2026-10-18).

**G16 References to retired mechanisms.**
- `.claude/commands/plan.md:41` tells the planning agent not to read `docs/DECISIONS.md` and
  describes it as "large -- near its Decision 134 size ceiling".
- Open rec-2933's title names "the surviving 132-header / 780,000-combined-byte ceilings"; open
  rec-3251's title is "Retire the combined_max_bytes decision-corpus ceiling". Decision 179 removed
  those constants on 2026-08-31.
- Roadmap item T1.5's criterion text describes a "synced hermetic cache" for recommendations; its
  `progress_note` dated 2026-10-10 states that phrasing no longer describes the recs read path.

**G17 Telemetry** (production telemetry rows begin about 2026-11-01: `sessions_window` returned no
rows for 2026-08-05, 2026-09-15 and 2026-10-14, and rows for 2026-11-01).
- CLI: `bin/venv-python -m scripts.telemetry_usage {models|tools|startup} --from YYYY-MM-DD --to YYYY-MM-DD [--by-root] [--by-agent]`
  (`scripts/telemetry_usage.py:1-12`). `models --by-root` prints one row per session tree and
  model, keyed by `harness_session_key`, with `tokens_input_total`,
  `tokens_cache_creation_total`, `tokens_cache_read_total`, `model_call_count`, `sessions`. A tree's
  rows fall in the window containing the day its root session STARTED. The CLI output has no
  `workflow` column.
- Join key: plan merge commits on main carry a `Claude-Session: https://claude.ai/code/session_...`
  trailer; the trailing `session_...` token equals telemetry's `harness_session_key`. List them:
  `git log --since=2026-11-01 --grep='^plan(' --format='%h %cs %s | %(trailers:key=Claude-Session,valueonly,separator=%x20)' origin/main`.
- Dated sample (read 2026-11-16; trees started 2026-11-14): the tree behind merge commit
  `afe42535` -- 188 model calls, uncached input 392, cache-creation 612,227, cache-read 26,531,304,
  5 sessions; behind `52029fb9` -- 142 calls, 289, 425,304, 27,155,059, 3 sessions; behind
  `c893ba5b` and `8eff624a` (one tree, two plans) -- 214 calls, 434, 543,120, 32,585,104, 3 sessions.
  In the raw verb rows all three trees carried `workflow: claude_code`; the workflow values seen in
  that two-day sample were `claude_code`, `audit`, `implement`, `ci_rca`.
- Dated sample, first model call of a session (`startup --by-agent`, 2026-11-09): root sessions on
  `agent/` branches (n=3) 65,183 to 78,113 input tokens; `general-purpose` sub-agents (n=8) 44,363
  to 49,642. A first call includes the harness's own system prompt and tool definitions as well as
  repository files.
- Compaction: `docs/contracts/telemetry_sessions.yaml:40` lists `compact` among the session
  lifecycle `event_kind` values. The verbs `sessions_window`, `session_state_and_duration`,
  `session_process_event_rollup`, `session_friction_rollup`, `model_usage_rollup`,
  `tool_use_rollup` and `session_startup_baseline` return no count of `compact` events (field lists:
  `src/common/ducklake_reader_client.py:192-204`, `src/common/event_usage_fields.py`).
- `.claude/settings.json:4` sets `CLAUDE_CODE_AUTO_COMPACT_WINDOW` to `1000000`; that line dates
  from 2026-06-04, before P0.

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
- Roadmap: T2.54 (traceability, `not_started`); T1.5 (`ops_decisions` graduation, `not_started`,
  line 2197); T4.24 (`deferred_post_mvp`; carries Decision 196's execution and the index-read
  residual); T3.14 (context budget as a tracked metric, `deferred_post_mvp`, line 5385); T4.30
  (decision-scout persona, `not_started`, line 7914); T1.17 (`in_progress`, one criterion open).

**G19 Intervention components for the Q4 ledger.**

| ID | Component | Anchor |
|---|---|---|
| L1 | Typed metadata envelope on new entries | Decision 167 cl.1; `decision-entry.yaml:314-385` |
| L2 | Required significance routing claim | Decision 167 cl.2; `envelope_rules.py:28-48` |
| L3 | Per-entry byte cap, hard-fail for new entries | Decision 167 cl.3; `validate_decisions_size.py:66` |
| L4 | WARN-tier accretion signal (two legs) | `validate_decisions_size.py:93`, `:128`; `decision-entry.yaml:582-599` |
| L5 | Planning-skill Decision Significance Gate (three property tests) | `.claude/skills/planning/SKILL.md:214-236` |
| L6 | Plan-critique category-consistency verdict | `.claude/skills/plan-critique/SKILL.md:87-90` |
| L7 | Class D contracts with a resolving evaluator | Decision 168; `_population.py:315` |
| L8 | Subject uniqueness and topic-equals-subject | `contract-population.yaml:265-276` |
| L9 | Population ratchet pins | `contract-population.yaml:313-331` |
| L10 | Class D amendment_log-on-change and conformance-loss detection | `contract-population.yaml:278-294` |
| L11 | Skeleton index rows for archived entries | `scripts/decisions_index.py`; rec-3012 |
| L12 | Archival wave | Decision 146; earlier audit step 6 |
| L13 | Migrate-then-rehome compaction and its manifest | Decision 175; `docs/decision-migration/MANIFEST.yaml` |
| L14 | Append-only lock on ratified bodies | Decisions 177, 178 (G6) |
| L15 | Stock-limit retirement with named retained levers | Decision 179 cl.1, cl.4 |
| L16 | Ambient prose shrink and ratchet-down byte budgets | T2.56 c1; `config/prose_budgets.yaml` |
| L17 | Bounded retrieval by the decision scout | Decision 160; `decision-scout/SKILL.md:28`, `:48` |
| L18 | Change-record content rule | `docs/contracts/git-ops.yaml:175` |
| L19 | Decision `kind` and `serves` | Decision 243 cl.8; `docs/contracts/decision-kind.yaml` |
| L20 | A scout BLOCK fails the plan PR | Decision 262 cl.1 |
| L21 | Monitored reversal-condition stanzas | `scripts/preflight/decision_conditions.py` (G14) |

**G20 Baseline ratings for Q7** (read only after rating; from the earlier audit as amended on
2026-08-07): EX1 partial, EX2 partial, EX3 partial, EX4 partial, EX5 partial, EX6 partial, EX7
missed, EX8 met, EX9 met, EX10 partial, EX11 met, EX12 partial, EX13 partial. The earlier audit's
content partition of Decisions 157-166 gave mean byte shares of about 43 percent specification,
33 percent rationale, 19 percent change-record and 6 percent other, single-rater, stated as plus
or minus 10 points.

## 11. EMPIRICAL PASS

Tag every finding `evidence_kind: static` (read from files) or `observed` (from a sampled artifact
or telemetry). At equal severity an observed finding outranks a static one. The sample sizes are
hard limits -- do NOT exceed them.

**E1 Content partition and bar test -- the 12 live entries with the highest decision numbers at
the audited commit.** For each:

1. Partition its bytes with this rule, unchanged from the baseline. Unit: UTF-8 bytes per block
   (paragraph, list item or numbered clause), attributed whole to one class; the entry's span is as
   defined in Section 4. Classes, first match wins: (1) change-record -- states what changed in THIS
   landing or its state at authoring time: measurements of the installing PR or corpus, runway or
   headroom figures, sequencing or bundling narration, verification-round narrative, lists of files
   edited; (2) specification -- states what is normatively true going forward, obeyable or
   checkable without knowing why it was chosen: shapes, enums, grammars, thresholds, limits and
   their values, procedures, scope or route sets, enforcement bindings; (3) rationale -- why this
   choice over alternatives, rejected options, principles, cost and benefit, reversal conditions;
   (4) other -- header, envelope, Status, Date and Warehouse-ID markers, Related lists, amendment
   blockquotes, separators. Tie-break: a sentence doing two jobs classifies by its dominant clause;
   a specification stated only to describe a rejected alternative is rationale. Record shares to
   the nearest 5 points.
2. Bar test. Apply the three questions of `significance.routing_rule`
   (`docs/contracts/decision-entry.yaml:430-437`) to the entry's own clauses and classify:
   `clears-bar` | `bar-met-by-amends-edge-only` (it clears only because it amends an earlier
   entry) | `routable-elsewhere` (a routing row other than `numbered_decision` owns most of it;
   name the row) | `unclear`.
3. Deletion counterfactual: if this entry were deleted and citations of its number removed, would
   any check, contract, agent instruction or human approval step behave differently? Name the
   artifact and where, or write "none found".

Compute the 12-entry means and compare with G20 only after all 12 are done.

**E2 Routing traces -- at most 10 plans.** From plan files first added in P2 whose text contains
`docs/contracts/` and does not contain `## Decision `, take the 5 added earliest and the 5 added
latest (by first-parent add date; ties by file name). List candidates with
`git log --first-parent --diff-filter=A --name-only --format='@%cs' --since=2026-09-13 origin/main -- 'docs/plans/PLAN-*.yaml'`.
IF a selected file no longer exists at the audited commit, read it from the commit that added it.
For each: where did the plan send its governing content -- `contract-governance-note` |
`new-contract` | `code-or-check-only` | `decision-annotation` (a dated annotation on an existing
entry) | `pr-or-commit-body` | `unclear`? Counterfactual: judged against how comparable content
was recorded in entries numbered 140-166, would this content have been written as a new numbered
Decision before the intervention -- `yes | no | unclear`? Quote the plan line that shows the
routing choice.

**E3 Evaluator samples -- at most 12 Class D contracts.** Sort the 12 `agent_surface` contracts by
basename and take every second one starting with the first (6). Sort the `check` contracts by
basename and take every seventh starting with the first (up to 6). For each: pick the first
rule-like statement under the contract's first top-level key other than `contract` and
`amendment_log` (a statement that something must, must not or always holds). Counterfactual: if the
repository violated that statement, would the named evaluator fail, or would a reading agent be
told to act? For a `check`: read the check module -- does it parse the contract and assert that
statement, assert other content of it, read only its structure, or only name the file? For an
`agent_surface`: read the surface around the basename -- does it instruct an agent to read or apply
the contract at a defined trigger, or mention it? Classify: `asserts-this-statement` |
`asserts-other-content` | `reads-structure-only` | `instructs-agent-at-trigger` | `mention-only`.
IF the first key holds no rule-like statement, use the next top-level key; IF the contract holds
none, write `statement_tested: "none found"` and classify by what the evaluator does with the
file as a whole.

**E4 Amended chains -- at most 6 entries.** Take the 6 live entries that are the target of the
most `amends` edges in `docs/decisions-index.json` (ties by lower number; list them with
`bin/venv-python -c "import json,collections as c; d=json.load(open('docs/decisions-index.json'))['decisions']; live={r['number'] for r in d if r.get('live')}; k=c.Counter(t for r in d for t in (r.get('amends') or []) if t in live); print(sorted(k.items(), key=lambda x: (-x[1], x[0]))[:6])"`).
For each: how many
entries must be read to state the rule as it now stands; does the amended entry itself carry an
annotation naming each amender (count named against edges); could a reader of that entry alone act
on a clause a later entry changed -- `yes | no`, with the clause.

**E5 Observed planning footprint -- at most 8 plan merges and at most 6 telemetry CLI calls.**
Skip if `meta.degraded_telemetry`. The audit day is the UTC date on which you run Section 5; the
step 6 probe counts as one of the 6 calls. Run `models --by-root` (G17) for the 7 UTC days ending
on the audit day and for the 7 days before that (2 calls). From the plan merge commits in those 14
days (G17 command), take the 8 most recent whose subject contains `approved plan` and whose trailer
key matches a `harness_session_key` in the output; a tree that produced several sampled plans counts
once and is noted. For each matched tree, summing its rows across models, record first-time tokens
(T15), cache-read tokens, model calls and sessions. Report median, minimum and maximum of
first-time tokens. A tree may include work other than the plan: say so; these are upper bounds for
one plan. Then compaction: with at most 2 further calls of your choice from the CLI or the
`ducklake-reads` MCP tools if your session has them, try to read a count of `compact` events for
the same trees. IF you find no route: set `meta.compaction_unreadable = true`, write
`metrics.compaction.readable: false`, and adjudicate C18 on that evidence. Do not write telemetry
tenant ids, project ids or session ULIDs in either deliverable; identify a sample by its merge
commit short sha.

## 12. METHOD

- P1 Setup (Section 5), then read G1's sources: the earlier audit's report, Decisions 167, 168, 177,
  178, 179, 196.
- P2 Run Scripts A and B; fill `metrics` (DD-A); record mismatches with Section 10.
- P3 Trace the planning read path (DD-E) and the contract trajectory (DD-F).
- P4 Empirical pass E1-E5.
- P5 Adjudicate candidates C1-C19; fill `intervention_ledger`, `growth_ledger`,
  `condition_reviews`.
- P6 Rate the rubric (Section 8).
- P7 Dedup every prospective finding (Section 13); assign severity (Section 15).
- P8 Answer Q1-Q8. For Q7 rate first, then read G20.
- P9 Only now: Q9 (`improvement_sequence`), then Q10.
- P10 LAST: summary, per-surface maturity, the report, the pre-push gate (Section 16).

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

Record `dedup_search_terms` and `dedup_hit_count` on the finding. A hit is a returned item whose
text you judge to OWN the defect (its acceptance or criterion would fix it); `recs_related` always
returns its nearest matches, so record the raw returned total in `roadmap_crossref.note` and count
only owning hits in `dedup_hit_count`. A hit means a sufficiency assessment (`planned-insufficient`
or `planned-unbuilt`) or a rejection -- never a fresh discovery. A `novel` finding requires a
recorded negative search (`dedup_hit_count: 0`); a `novel` finding without one is
`confidence: HYPOTHESIS`. A defect that came from no candidate and that an owner already has built
or in progress is not filed at all.

DO NOT FLAG (deliberate constraints; you may assess how they are implemented and what they cost,
never whether they should exist):

- Decisions are stored in git in every tier; the warehouse table is a read-side derivative
  (Decision 196 clause 1). Do not propose retiring the files or making the warehouse the source.
- Decision numbers are never removed, reused or merged; headers always resolve (Decision 149,
  `decision-entry.yaml` `never_remove_headers`).
- `direction`-kind Decisions and north-star entries change only with explicit human approval
  (Decision 243 clauses 6 and 8).
- No standing mechanism for refactoring live, non-superseded entries (operator decision recorded on
  rec-3246, 2026-08-24).
- The routing-consistency lint as specified in the earlier audit's amendment was rejected on
  construct-validity grounds (Decision 167, update of 2026-08-17). Do not re-propose that lint. A
  different mechanism is allowed if evidence meets Decision 167's stanza condition.
- New enforcement is forward-only; entries and contracts that predate a rule are grandfathered
  (`decision-entry.yaml` scope notes; Decision 168).
- `deferred_post_mvp` roadmap statuses (Decision 93): a deferral is not a defect. Recommending that
  an item be pulled forward is allowed, as a finding with `change_type: rescope`.
- The 500-SLOC and 500-effective-line file budgets and decompose-by-default (Decisions 128, 166).
- Ambient prose byte budgets ratchet down only (`config/prose_budgets.yaml`).
- No new standing prose architecture documents and no human-readable companion to a
  machine-readable source (Decision 86; `AGENTS.md` "Agent-First Repository").
- Recommendations have no local cache; reads go through bounded named verbs (Decision 215).
- The public-repository content boundary (`AGENTS.md`; Decision 101).

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
    periods:                      # one row each for P0, P1, P2
      - {period: P0|P1|P2, from: "", to: "", days: 0, new_headers: 0, headers_per_day: 0.0,
         corpus_bytes_added: 0, bytes_per_day: 0, plan_files_added: 0, new_headers_per_plan_file: 0.0}
    cap: {entries_examined: 0, first_size_median: 0, within_5pct_under_cap: 0,
          over_cap_at_first_appearance: 0, larger_now: 0, bytes_added_after_landing: 0,
          now_over_cap: 0, commits_adding_2plus_entries: 0}
    planning_context_footprint:
      static:                     # one row each for 2026-08-07, 2026-09-12, the audited commit
        - {boundary: "", read_set_bytes: 0, always_read_bytes: 0, gate_subagent_bytes: 0,
           on_demand_bytes: 0, token_estimate: 0}
      band_assignment: []         # [{file, band: always_read|gate_subagent|on_demand, basis: "file:line"}]
      observed: []                # [{merge_commit, plans_in_tree: 1, model_calls: 0, sessions: 0,
                                  #   first_time_tokens: 0, cache_read_tokens: 0, note: ""}]
      observed_summary: {samples: 0, first_time_tokens_median: null, first_time_tokens_min: null,
                         first_time_tokens_max: null}
    compaction: {readable: true|false, sessions_examined: 0, sessions_with_compact_event: null,
                 method: ""}
  question_answers:
    - {q: Q1, verdict: shifted|partially-shifted|not-shifted|cannot-determine, basis: [], prose: ""}
    - {q: Q2, verdict: reduced|held|grown|cannot-determine, basis: [], prose: ""}
    - {q: Q3, verdict: enforced-by-default|mixed|mostly-asserted|cannot-determine, basis: [], prose: ""}
    - {q: Q4, verdict: intact|intact-with-erosion|materially-eroded, basis: [], prose: ""}
    - {q: Q5, verdict: sustainable|sustainable-with-named-gaps|unsustainable-on-current-trajectory|cannot-determine,
       any_limit_binds_within_30_days: yes|no, basis: [], prose: ""}
    - {q: Q6, verdict: sufficient|partial|insufficient, basis: [], prose: ""}
    - {q: Q7, verdict: improved|regressed|mixed|unchanged, basis: [], prose: "",
       external_checklist: [{property: EX1, rating: met|partial|missed, evidence: "",
                             baseline: met|partial|missed, delta: up|same|down}]}   # EX1..EX13, 13 rows
    - {q: Q8, basis: [], prose: ""}                 # no verdict; points at condition_reviews
    - {q: Q9, verdict: path-identified|path-partial|no-change-warranted, basis: [], prose: ""}
    - {q: Q10, answers: [{question: "", answer: "", basis: []}]}
  intervention_ledger:            # exactly 21 rows, L1..L21
    - {id: L1, component: "", state: holding|holding-with-side-effect|eroded|retired-with-replacement|retired-without-replacement|never-exercised,
       exercised_evidence: "file:line|commit|sample id", side_effect: "", only_control_for_its_purpose: yes|no,
       basis: [], confidence: CONFIRMED|HYPOTHESIS}
  growth_ledger:                  # exactly 8 rows, one per record type below, in this order; units:
                                  # decision_entries headers; decision_index, contracts,
                                  # ambient_instruction_layer and roadmap bytes; plans and
                                  # checks_and_tests files; open_recommendations count
    - {record_type: decision_entries|decision_index|contracts|ambient_instruction_layer|plans|roadmap|open_recommendations|checks_and_tests,
       surface: "", unit: "", stock_now: 0, flow_p0_per_day: null, flow_p2_per_day: null,
       governor: flow|stock|both|none, governor_anchor: "file:line|none",
       consumer_cost_model: ambient|per-plan|retrieved-by-id|on-demand|not-read-by-agents,
       binds_within_30_days: yes|no|n/a, headroom_note: "", basis: []}
  content_partition:
    entries:                      # at most 12 rows (E1)
      - {decision: 0, bytes: 0, specification_pct: 0, rationale_pct: 0, change_record_pct: 0, other_pct: 0,
         bar_test: clears-bar|bar-met-by-amends-edge-only|routable-elsewhere|unclear, routable_row: "",
         deletion_counterfactual: ""}
    means: {specification_pct: 0, rationale_pct: 0, change_record_pct: 0, other_pct: 0}
    baseline_means: {specification_pct: 43, rationale_pct: 33, change_record_pct: 19, other_pct: 6}
  samples:                        # E2, E3, E4 rows
    - {sample_set: E2, subject: "<plan file>", classification: contract-governance-note|new-contract|code-or-check-only|decision-annotation|pr-or-commit-body|unclear,
       would_have_minted_before: yes|no|unclear, evidence: ""}
    - {sample_set: E3, subject: "<contract basename>", evaluator: "", statement_tested: "",
       classification: asserts-this-statement|asserts-other-content|reads-structure-only|instructs-agent-at-trigger|mention-only,
       evidence: "file:line"}
    - {sample_set: E4, subject: "Decision <n>", entries_to_read: 0, amenders_named: 0, amend_edges: 0,
       stale_clause_actionable: yes|no, evidence: ""}
  condition_reviews:              # exactly 8 rows, the G14 table in order
    - {decision: 167, condition: "", verdict: fired|not-fired|foreclosed|cannot-determine, evidence: "",
       recommended_disposition: re-arm|re-decide|retire-condition, rationale: "",
       confidence: CONFIRMED|HYPOTHESIS}
  improvement_sequence:           # 0 to 10 rows, in the order you would do them
    - {step: 1, action: "", closes: [], opportunity: false, moves: "", practice: "", practice_source: "",
       reuses_mechanism: "", effort: XS|S|M|L, reversible: true|false, requires_human_ruling: "",
       abort_criterion: "", expected_effect: "", blocked_behind: [], confidence: CONFIRMED|HYPOTHESIS}
  per_surface_assessment:
    - {surface: "", maturity: frontier|strong|solid|nascent, strengths: "", top_gaps: []}
  rubric_ratings:
    - {surface: "", dimension: VD1, rating: strong|adequate|weak|absent|n/a, evidence: "file:line|item-id", note: ""}
  findings:
    - {id: CFR-01, from_candidate: C1|null, surface: "<surface>|shared", question: Q1, dimension: VD1,
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
    - {candidate: C1, adjudication: planned-covered|not-a-defect, why_dismissed: "",
       compensating_control: "", control_property_match: "", decision_or_item_id: ""}
  summary: {total_findings: 0, novel_count: 0, planned_insufficient_count: 0, planned_unbuilt_count: 0,
            top_improvements: [], highest_leverage_change: null,
            maturity_corpus: "", maturity_routing_gates: "", maturity_contracts: "",
            maturity_read_path: "", maturity_lifecycle: "", maturity_adjacent_records: ""}
```

Field rules:

- `condition_reviews.verdict`: `foreclosed` means a later Decision removed the trigger (the stanza's
  own note says so); `recommended_disposition`: `re-arm` keeps the condition and sets a new review
  date, `re-decide` sends it to a planning session as the stanza's `on_trigger` says,
  `retire-condition` removes a condition that can no longer fire.
- `improvement_sequence`: a step with `opportunity: false` has at least one finding id in `closes`;
  a step with `opportunity: true` has `closes: []`. `moves` names what it moves, for example
  "Q2 grown -> held" or "EX7 missed -> partial". `practice` is an EX id or a named practice;
  `practice_source` is a URL from `meta.external_sources` or "own knowledge".
- `intervention_ledger.state`: `holding` = in force and exercised with no adverse effect found;
  `holding-with-side-effect` = in force and exercised, with a measured unintended effect named in
  `side_effect`; `eroded` = in force on paper, and evidence shows it no longer constrains what it
  was built to constrain; `retired-with-replacement` / `retired-without-replacement` = removed by a
  recorded act, with or without another control taking over its purpose; `never-exercised` = built,
  and no instance of it firing or being used was found.
- `growth_ledger`: take stocks and flows from Script A's boundary rows (P0 flow = change from
  2026-06-15 to 2026-08-07 divided by 53 days; P2 flow = change from 2026-09-12 to the audited
  commit divided by the days between). Write `null` for a flow git history cannot give
  (`open_recommendations` has no history in git). Q5's `any_limit_binds_within_30_days` is `yes`
  when any row's `binds_within_30_days` is `yes`, else `no`.
- `highest_leverage_change` is a finding id, or `null` when there are no findings.

Invariants (state them as checked in the report's Intake section):

- COUNTING INVARIANT: `findings[]` is the SOLE enumerated list of defects. `total_findings =
  len(findings) = novel_count + planned_insufficient_count + planned_unbuilt_count`. Candidates that
  are fully covered or not defects live in `rejected_candidates`, NOT in findings. `metrics`,
  `question_answers`, `intervention_ledger`, `growth_ledger`, `content_partition`, `samples`,
  `condition_reviews`, `improvement_sequence` and `rubric_ratings` are systems of record referenced
  FROM findings by id, never re-counted. `top_improvements` and every `basis`, `closes`, `top_gaps`
  and `depends_on` entry MUST be finding ids.
- COVERAGE: each of C1-C19 appears either in `findings[].from_candidate` or in
  `rejected_candidates[].candidate`, never in both and never in neither; a candidate has at most
  one `rejected_candidates` row. `intervention_ledger` has 21 rows, `condition_reviews` 8,
  `external_checklist` 13.
- `control_property_match` is REQUIRED whenever a compensating control is the reason for dismissal:
  name the property the control exercises, cite where it operates (mechanism or file:line), and
  state why the control would FAIL if the defect were real.
- CONFIRMED requires the behavior traced to file:line or to a sampled artifact; anything less is
  HYPOTHESIS.

Deliverable 2, `audits/contract-first-retrospective-<sha>.md`: the executive layer a human reads
first. At most 2,000 words by `wc -w` on the whole file. Sections, in order: Intake (audited
commit, set `meta` flags and their consequences, invariants checked); Verdicts (a table of Q1-Q9
verdicts, one line of basis each); What held and what did not (from the ledger); Cost and growth
(the pinned metrics, before and after); Reviews falling due (Q8); Improvement path (Q9);
Not asked (Q10); What this audit did not file (rejected candidates in one paragraph). Plain ASCII,
no emojis.

## 15. SEVERITY + MATURITY

Assign severity AFTER judgment, by defect class, never from this prompt's framing or a candidate's
wording:

- critical = the governance system can produce a wrong-but-trusted outcome: an agent or a check
  acts on a rule that is superseded, contradicted or unenforced while the system presents it as
  current or enforced; or ratified record content can be rewritten or lost with every gate passing.
- high = a weakness that materially reduces a guarantee the intervention claims (routing,
  enforcement binding, bounded read cost, governed growth) AND whose compensating controls you
  judged insufficient; or a quantity that reaches a hard limit within 30 days at the P2 rate with
  no lever that relieves it.
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
- Live systems: only the read commands Sections 5, 11 and 13 allow, within their limits. No write
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
