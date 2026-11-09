# REPORT: batched full tier on main, culprit finding, and a last-green ref deploys consume (rec-4874 design)

Plan: docs/plans/PLAN-last-green-batched-full-tier-design.yaml (REPORT-ONLY, V1). Written 2026-11-09 against main
70d86bef. This report designs and drafts; it builds, files, closes and flips nothing. The Decision in section 6 is a
draft: it lands only through its own IMPLEMENTATION plan, with its wording approved on GitHub (Decision 246).

Precondition for any later use of this report: re-run the plan's VP steps 1-7 on the current main. A step that no
longer prints its expected line means a premise below moved; re-ground the section it names before relying on it.

## 1. Problem and goal

- rec-4874 (operator direction 2026-11-03): the fast diff-aware tier is the only presubmit; the full tier runs on a
  batched run of main; a red batch is bisected to its culprit automatically; a green batch advances a ref every
  deploy channel consumes; a red blocks that advance, not planning or merging.
- Today three full-tier runs can cover one change: the session container before push (Decision 163 point 3,
  Decision 203), main-validate on every push to main (Decision 73 cl.2), and Main Canary on a 3-hourly cron. rec-5019
  plans a fourth, interim one: the full tier on GitHub for each ready PR head.
- Evidence: audits/ci-merge-throughput-scaling-269e630a.yaml. At H0 (41 merges on the peak day) main-validate is 59%
  of 49.4 job-minutes per merge; slots saturate near 123 merges a day as is, about 80 with the verdict personas, about
  165 after rec-4114 (CMTS-06); red occupancy is 0.34 with a 3.6 h mean window and reaches 1.0 near 83 a day (CMTS-15).
- Scion (anthropic.com/engineering/redesigning-source-control, 2026-10-13), verbatim: "A proper merge queue, for
  example, doesn't make much sense at agent scale."; "we've invested in post-merge and pre-deploy automated
  testing."; "our automated bisect and revert pipelines" (curly apostrophes normalised to ASCII). The post says
  nothing about batching or culprit mechanics, so those below are this design's, not Scion's.

Goal: every check still runs on every merged tree; only its placement moves to post-merge. Production code never runs
a tree whose full tier has not passed. Merging never waits on main's colour.

## 2. Vocabulary

"Last-green ref" names the newest main commit covered by a passing full-tier run. It is not a "promotion":
docs/contracts/environment-taxonomy.yaml:392-394 reserves that word for a gated deploy between deploy tiers (audit
CMTS-11). When SIT stands up (rec-5024, rec-5028, rec-5033), the last-green ref is what the merge-time SIT deploy
consumes, and the human-gated SIT-to-PROD step is the promotion. The build's first plan adds "last-green ref" to the
taxonomy's allowlisted terms. The term is fixed: the concurrent SIT Decision draft (PR #1936, PLAN-sit-decision)
already cites "rec-4874's last-green ref", so later wording keeps it verbatim.

Boundary: the last-green ref selects what may deploy to the first deploy tier (the sandbox account today, SIT once
rec-5028 lands). What is promotable to PROD (the identical artifact that ran on SIT, by CodeSha256 and saved plan) is
a stronger condition that rec-5033 defines: every promotable artifact comes from a commit the last-green ref has
covered, deployed to SIT with every function's CodeSha256 matching and its saved plan applied, and SIT's checks
passed after the pre-promotion reset (rec-5033 scope note, 2026-11-09). The two are never the same ref. (The SIT
draft's "SIT deploys keep 249 cl.2" and this design's cl.5 say the same thing once 249 cl.2's signal becomes the
last-green ref.)

## 3. Design

### 3.1 Batch vehicle

- ci.yml main-validate gains `concurrency: {group: main-full-tier, cancel-in-progress: false}` at JOB level
  (.github/workflows/ci.yml:100-107 has none today). GitHub keeps one running and at most one pending run in a group;
  a newer push cancels the pending one. A batch is therefore adaptive: at low merge rates every commit gets its own
  run (exact attribution, as today); at peaks one run covers every merge that landed while the previous run ran.
- The batch range is `prev..head` on first-parent history, where prev is the head of the newest earlier COMPLETED
  full-tier run, green or red (resolved by the shared function of 3.2). Anchoring on the last completed run, not the
  last green one, keeps a red's range from growing with every later batch; a fingerprint already red at prev is
  carried as known (3.5). The run records the range in its evidence bundle.
- Job-level, not workflow-level: the push run's other jobs (trailer-census, terraform-validate, rec-autoclose
  triggers) stay per push. A cancelled pending main-validate concludes `cancelled`; ci-rca wakes only on `failure`
  (.github/workflows/ci-rca.yml:60) and the deploy channels only on `success`, so a cancelled run wakes neither.
- Main Canary keeps its cron and gains a covered-head skip: it runs only when main HEAD has no completed full-tier
  run (green or red). That keeps Decision 205 cl.5's backstop for a plans-only tail (whose push runs the fast tier
  once rec-4114 lands) and removes the same-SHA re-runs and duplicate recs of CMTS-08. (Advisor point 1 adapted: the
  advisor proposed dispatch-only; the scout's Decision 205 flag shows the backstop must stay.)
- Plans-only filter (rec-4114, Decision 205 cl.5) is computed over the batch range, never the head commit alone, so a
  batch holding any non-plans commit runs the full tier (scout flag, Decision 205). The merged, unbuilt
  PLAN-plans-only-main-fast-tier classifies `github.event.before..github.sha` in scripts/ci/push_scope.py; under
  batching the vehicle plan passes prev as the base instead, and push_scope's own range test keeps the classifier and
  push_context_base on one base.
- Capacity (modelled, not measured): at a 30-minute run and the audit's peak hour, batches of 2-3 cut main-validate
  starts at peak by roughly that factor. No capacity trigger gates the vehicle: below the batching rate it behaves as
  today.

### 3.2 The last-green ref

- Authoritative form: a derived query, never a stored value. scripts/ci/deploy_gate.py:92-113 (`newest_green_sha`)
  already returns the newest first-parent main commit whose own push run passed the main-validate full-tier step.
  Under batching, a commit whose main-validate was cancelled while pending has a CI run concluding `cancelled`,
  which the resolver already skips, so the newest green commit is a batch head by construction: the deploy selection
  itself needs no new rule. What changes: the resolver moves to a shared module so ci-rca, push_context_base and the
  junit binding use one definition; it learns "covered by" (ancestry: a commit is covered when it lies in the range
  of a passing batch run) for consumers that ask about a specific commit; and its fixed 50-commit walk-back
  (deploy_gate.py `limit: int = 50`) becomes unbounded or cursor-based, so a red longer than 50 merges never makes
  the last-green ref unresolvable (monotone coverage, 3.5).
- A git tag or GitHub deployment record may mirror it for display; it is never read as authority. A mutable ref
  would need a write credential and a ruleset change (frozen by rec-4624, Decision 245 cl.5) and could drift from the
  run evidence it summarises.
- rec-4874's acceptance test is renamed on build to tests/ci/test_last_green_ref.py::
  test_green_batch_advances_last_green_and_red_does_not (rec-4874's acceptance is updated by update_rec in that plan).

### 3.3 Culprit step

- On a red batch run with more than one commit in its range, a culprit step runs in ci-rca: for each first-parent
  commit in the range, oldest first, re-run only the failing checks named by the fingerprint (Decision 142,
  scripts/ci_rca/fingerprint.py), stopping at the first commit that fails. Linear, not binary: batches are 1-3
  commits at H0; binary search is the later change if batches pass about 8.
- Flakes: a failing node is re-run twice on the batch head first; if it passes both, the red is classified flaky and
  routed as today's flaky class, with no culprit.
- Whole-tree checks that do not localise to a commit (coverage floors, size ratchets, validate_graduated_rerun):
  attribute to the range commit whose diff touches the measured files; otherwise attribute to the whole range and
  file one rec naming every commit.
- Budget: about 2-8 minutes per red batch (advisor estimate, unmeasured). Cost scales with reds, not merges.

### 3.4 Remediation (fork A: the operator chose the opened revert PR, 2026-11-09)

- Forward-fix stays the default (Decision 73 cl.3, Decision 226 cl.4). When the culprit is a Track B commit outside
  terraform/** and governed deploy paths, the culprit step also OPENS a revert PR of it, never auto-merged. The
  repairing session merges it or closes it when the forward fix lands. Automated revert stays excluded: Decision 73
  cl.3's reason (moving main under live worktrees) binds a merge, and opening a PR moves nothing. Opening one for
  every named culprit drops Decision 226 cl.4's "when the fix is not obvious" condition, so the Decision amends cl.4
  (not 73 cl.3). Guards (consult 2): a culprit is named only on a passes-at-parent, fails-at-culprit pair of re-runs;
  the revert PR carries the Decision 186 closure artifact (red-main-recovery.yaml:38-42); its head re-runs the
  failing checks, since the fast tier may not select them; when later merges stop the revert applying cleanly, the
  step files the rec only.

- Per tier, once SIT stands (operator, 2026-11-09; rec-5033 scope note dated 2026-11-09, read back 21:53:46Z): every
  tier is fixed forward. A PROD fault is fixed on main and reaches PROD through SIT and a promotion; nothing reverts
  main for PROD and nothing rolls PROD back. "Revert PROD" means only promoting an earlier promotion record through
  the same workflow, behind the same single operator approval (an agent may dispatch it). The promotion record, the
  promotable condition and the PROD checks are rec-5033's. Consequence the operator accepted: a PROD fix waits behind
  a red on main, which makes short red windows (3.3, 3.4, 3.5) matter more once SIT stands.

### 3.5 What a red blocks (fork B: the operator chose the overlap halt plus an SLO alarm, 2026-11-09)

- Deploys: the last-green ref does not advance past a red, so code deploys pause (Decision 249 cl.3, unchanged).
  Once SIT stands this pauses SIT code deploys and the creation of new promotable artifacts; PROD keeps running its
  last promoted artifact, so a red on main never reaches PROD.
- Coverage is monotone: once a passing run covers a commit, no later run withdraws it. A red main stops the
  last-green ref advancing; it never moves it back and never withdraws an artifact rec-5033 already counts as
  promotable (confirmed to the CI Reform project, 2026-11-09). A defect found later in a covered commit is fixed
  forward like any other.
- Merging: never blocked by main's colour.
- Planning: Decision 226 cl.3's overlap halt stays, computed on the culprit's affected set once the culprit step
  names it, and on the union of the batch range until then (over-blocking bounded by batch size). Past
  the 4 h SLO, an alarm to the operator replaces cl.4's block on every new plan, since production is already
  protected by the deploy pause and a global halt adds no repair capacity. The operator also sees a count of open
  reds, so a pile-up stays visible without the halt's back-pressure (consult 2).
- Masking: a later batch that fails on a fingerprint already open as a red appends evidence to that rec; a new
  fingerprint files a new rec. Dedup keys on fingerprint plus range, across workflow slugs (CMTS-07, CMTS-08).

### 3.6 Presubmit end state and its measured retirement (fork C: the operator kept rec-5019 as the interim, 2026-11-09)

- End state: the fast tier is the only presubmit. The local pre-push full tier (Decision 163 point 3, Decision 203)
  and rec-5019's per-PR GitHub full tier retire. Decision 203's push gate keeps its destination and canonical-form
  refusals and drops the full-tier evidence requirement.
- Order: rec-5019 lands first as the interim (validation on GitHub soonest); its ready-head check runs are the
  server-side record the measurement below needs.
- Pre-registered measure (NS.6; scout note), read over the 30 days after both rec-5019 and the culprit step are live:
  (m1) pre-merge catches per 100 merges: ready-head GitHub full-tier failures on a check that passed --pre on the same
  head; (m2) red occupancy and mean red window on main; (m3) projected occupancy without the pre-merge tier:
  m2 + (m1 / 100) x M x W / 24, where M is merges per day and W the mean red window in hours over the same 30 days.
  Retirement is proposed when m3 stays under 0.5 (CMTS-15's threshold). The condition is measured, never a date; if
  the end state were weeks away, rec-5019 would be throwaway and the catch rate would come from handoff evidence
  instead (consult 2).
- What the measure cannot show (Decision 235): a rare-regression tripwire that never fires looks redundant. No check
  is retired here; every check still runs on every merged tree post-merge. The retirement moves placement only, and it
  rests on the operator's Decision; the measure is an input, never the decider.

### 3.7 Decision 245's merge-overlap refusal (fork D: the operator chose to narrow it, 2026-11-09)

- Narrow the refusal to overlapping files under terraform/** and the governed deploy paths
  (docs/contracts/deploy-paths.yaml), where a red is not cheap to forward-fix: Terraform applies on push, ungated by
  the last-green ref (Decision 249 cl.5), and will apply to SIT on every merge. Elsewhere a stale merge's combined
  tree is tested by the next batch run and its culprit found by 3.3. Its reversal condition tap-promotion-lands fires.
- Guards (consult 2): the narrowing ships only after the culprit step has named the culprit correctly on three real
  multi-commit reds with no misattribution, and the update-branch remedy gains an attempt cap with escalation
  (audit CMTS-03). Cost: more same-file reds on non-infra paths (2 of 38 merges overlapped in Decision 245's replay)
  and shared ratchet lines exposed until rec-4873.
- The auto-merge residual (mcp__github__enable_pr_auto_merge checked only at enable time; Decision 245 cl.6) becomes
  harmless outside those paths for the same reason.

### 3.8 Range-aware consumers (the prerequisite slice)

Every consumer that assumes one full-tier run per push (audit CMTS-07 dd_c, lines 123-151) changes first. Each change
is correct under one run per push too, so this slice ships before batching starts.

| Consumer | Today | Change |
|---|---|---|
| ci-rca.yml anchor, PR lookup, selection manifest | head_sha; one PR via commits/{sha}/pulls (ci-rca.yml:258-281) | range prev..head; one PR per first-parent commit |
| scripts/ci_rca/vacuous_pass.py:69-92 | git diff HEAD^ HEAD | diff over the range |
| ci-rca dedup | commit statuses of one anchor per slug (ci-rca.yml:338-372) | fingerprint plus range, across slugs |
| ci.yml trailer-junit-verdict (needs main-validate, same-run artifact) | same-run provenance (Decision 201; git-ops.yaml trailer_acceptance_gate.source_admission) | bind to the first full-tier run whose range covers the merge commit (ancestry plus conclusion), posted from that run |
| scripts/ci_rca/red_overlap.py | the red's affected set (one commit) | union of the range until the culprit narrows it |
| scripts/checks/_common.py:41 push_context_base | prefers GITHUB_EVENT_BEFORE (Decision 159 cl.1) | anchor on prev when it is an ancestor, so diff-aware full-tier checks see the whole batch (scout flag, Decision 159) |
| validate_graduated_rerun (Decision 244 cl.1 "before handoff and on main") | full tier both legs | the "before handoff" leg follows 3.6; whole-tree attribution per 3.3 (scout flag, Decision 244) |

Unaffected (audit dd_c items 3-6, 8): Decision 226's is_track_a workflow tier (both CI and Main Canary are tier CI),
rec-autoclose.yml (per push, DECISIONS.md diff), the merge gate, signal-green and pr-conflict-signal (per PR).

### 3.9 Deploys and Terraform

- Code deploys: Decision 249 cl.2's green signal becomes the last-green ref; cl.3's pause is unchanged.
- Terraform apply stays on push behind the Decision 77 deterministic guard (Decision 249 cl.5). Its licence is the
  guard and its human-gated fail-closed set, not test colour, and a red main must not block a drift repair. SIT's
  plans (rec-5028, rec-5033) move auto-apply to SIT; this design does not.
- Capacity: rec-5028 (every merge applies terraform/personal and deploys to SIT) and rec-5032 (per-PR Lambda stacks)
  add Actions load beside this design's savings; rec-5022's gauge reads all of it.

## 4. Options considered and not taken

- GitHub merge queue or strict up-to-date (Decision 100's managed primitive): both gate merges serially, the operator
  judged that unscalable, the admin bypass skips both, and the ruleset is frozen (Decision 245 cl.5; scout note,
  Decision 100). Not taken.
- Fixed-cadence scheduled batch: GitHub cron delivers 4-7 h apart (rec-4233), and a fixed cadence adds latency at low
  rates. Not taken; the cron remains only as the covered-head backstop (3.1).
- Separate batch dispatcher (EventBridge or a scheduler): added state for no gain at H0. Revisit with rec-4233.
- Mutable ref as authority: write credential, ruleset change, drift (3.2). Mirror only.
- Full-tier bisect: about 29 minutes per probe. Fingerprint-scoped re-run instead (3.3).
- Automated revert: excluded by Decision 73 cl.3 and 226 cl.4; an opened revert PR (3.4) is the bounded form.
- Union attribution only: over-blocks planning and names no owner. Kept only as the fallback (3.3).
- rec-4790(a) evidence carry-over: removes one rebase race, keeps the local full tier; superseded by 3.6's end state.

## 5. Existing recs and Decisions this resolves or re-scopes (dispositions proposed for the build plans)

- rec-4874: closed by the last build plan. rec-5019: built as the interim, then its job retired by 3.6's plan.
- rec-4790: superseded at 3.6 (no local full tier left to race); options (a) and (c) not taken.
- rec-4451 (main moves after the merge ref): harmless under last-green gating outside terraform/deploy paths; the
  residual inside them is the narrowed Decision 245 gate.
- rec-4085, rec-4086, rec-4087, rec-4118 (local handoff gate hardening): each re-scoped by 3.6, since the gate stops
  requiring full-tier evidence; the build's retirement plan decides close or keep per rec.
- rec-4873 (merge-robust ratchets): independent; still needed, because a combined-tree crossing now surfaces as a
  batch red attributed by 3.3.
- rec-4902 (runner proof of full-tier-only checks): satisfied by rec-5019's ready-head run while it lives; after 3.6
  the batch run is the first runner proof, so rec-4902's obligation is re-decided then.
- rec-4114: orthogonal; computed over the range (3.1). rec-4233: the canary's cadence. rec-5022: telemetry for m1-m3
  and capacity, not a gate.
- Decision reversal conditions that fire: 245 tap-promotion-lands, 249 canary-model-decided.

## 6. Decision draft

Envelope and amendment annotations are added by the landing plan (decision-entry.yaml). Target <= 6,144 bytes.
Bracketed text marks the operator's fork answers of 2026-11-09 (A-D).

```text
## Decision N: The full tier runs post-merge on batches of main, a red batch is bisected to its culprit, and code
deploys consume the newest commit a passing run covers; a red pauses deploys, never merging (amends Decisions 73,
159, 163, 201, 203, 205, 226, 244, 245 and 249)

significance: numbered_decision -- durable and reversal-relevant: it moves every full-tier check from pre-merge to
post-merge placement and redefines what a red blocks. Not a git-ops.yaml or red-main-recovery.yaml note: those record
mechanisms and values, not where a check runs or what a red may halt. Not amendment_forms lines: an annotation cannot
change a ratified clause's meaning, and one model spans ten entries.
kind: direction -- clause 5 decides what production may run and clause 6 what merging waits on.

Problem: three full-tier placements cover one change (session container, every push to main, a 3-hourly canary) and
rec-5019 adds a fourth. The per-push run is 59% of Actions job-minutes per merge; the local run races a main that
moves every 10-15 minutes (rec-4790); red occupancy is 0.34 (audit ci-merge-throughput-scaling-269e630a).

Intent: every merged tree still meets every check; production runs only covered trees; merging never waits on
main's colour.

1. Batched post-merge full tier (amends 73 cl.2). main-validate runs in one non-cancelling concurrency group; each run
   covers prev..head on first-parent history, prev being the previous completed run's head. The fast tier is the
   only presubmit. Plans-only detection reads the range (amends 205 cl.5); Main Canary runs only on an uncovered
   head.
2. Last-green ref. The newest first-parent main commit whose own run passed the full tier, derived from run evidence
   and never stored as authority. It is not a promotion (environment-taxonomy.yaml).
3. Culprit step. A red run covering more than one commit re-runs only its fingerprinted failing checks per commit,
   oldest first; a node passing two re-runs on the head is flaky; a whole-tree check that does not localise is
   attributed to the range. [A: The step opens, never merges, a revert PR for a Track B culprit outside terraform/**
   and governed deploy paths, named only on a passes-at-parent, fails-at-culprit pair (amends 226 cl.4's "not obvious"
   condition); automated revert stays excluded (73 cl.3).]
4. Range-aware evidence (amends 159 cl.1, 201, 244 cl.1). ci-rca anchors, diffs, dedups (fingerprint plus range) and
   resolves PRs over the range; a junit acceptance verdict binds to the first passing or failing run whose range
   covers the merge commit; push_context_base anchors on prev.
5. Deploys (amends 249 cl.2). Code-deploy channels to the first tier consume the last-green ref; a red pauses them
   (249 cl.3) and never withdraws coverage already earned. Terraform apply stays on push behind the Decision 77
   guard. Every tier is fixed forward; what is promotable to PROD, and promoting an earlier record, are rec-5033's.
6. What a red blocks (amends 226 cl.3-4). Never a merge. A plan is blocked only on overlap with the culprit's
   affected set, or the range's until the culprit is named. [B: Past the SLO an alarm replaces the block on all new
   plans.]
7. Pre-merge full tier retires on a Decision (amends 163 pt.3, 203). The local pre-push full tier and the interim
   per-PR GitHub run [C: rec-5019] retire when a later plan, approved by the operator, cites 30 days of the
   pre-registered measures (pre-merge catches per 100 merges, red occupancy, projected occupancy without the
   pre-merge tier under 0.5). The measure informs; it never decides (Decision 235). Decision 203's gate then keeps
   its destination and form refusals only.
8. Merge-overlap refusal (amends 245). [D: Narrowed to overlapping files under terraform/** and governed deploy
   paths, once the culprit step has named three real culprits correctly; the update-branch remedy gains an attempt
   cap.]
9. Coverage (Decision 181 cl.2): owned by build plans 2-5 in REPORT-last-green-batched-full-tier-design.md
   section 7; residuals named there.

reversal-conditions: culprit-misattribution (two wrong culprits in 30 days: revisit cl.3); red-occupancy (above 0.5
for 7 days: revisit cl.6-7); batch-size (median batch above 8: binary search or sharding); sit-stood-up (rec-5028
lands: re-key cl.5 to the SIT deploy).
```

## 7. Decomposition (Decision 247 atomic IMPLEMENTATION plans; order settled on advisor evidence)

Child recs are filed by the Decision-landing plan, not now, so a change to the Decision's wording leaves no orphans.

| # | Plan | Depends on | Scope sketch | Proof |
|---|---|---|---|---|
| 1 | Land the Decision, taxonomy term, Decision 245/249 reversal annotations; file child recs for plans 2-5 | this plan merged; the SIT Decision (PR #1936) merged | docs/DECISIONS.md, environment-taxonomy.yaml, decisions-index.json | wording-check PASS; approval run (Decision 246) |
| 2 | Range-aware consumers (3.8) | 1 | ci-rca.yml, scripts/ci_rca/**, trailer-junit job and its contract text, scripts/checks/_common.py, shared last-green resolver | fixture batch of three merges, one failing test: one rec naming the culprit PR, two junit-routed recs closed, halt set equal to the culprit's affected set (CMTS-07 acceptance) |
| 3 | Vehicle, culprit step, canary covered-head skip, revert-PR opener [A] (3.1, 3.3, 3.4) | 2 | ci.yml main-validate concurrency, main-canary.yml, ci-rca culprit step | a dispatched batch on a fixture branch; a canary run on a covered head concludes skipped |
| 4 | Deploy gate on the last-green ref; rec-4874 acceptance test; halt and alarm rules [B]; Decision 245 narrowing [D] | 3 | scripts/ci/deploy_gate.py, tests/ci/test_last_green_ref.py, red_overlap and red-main-recovery.yaml, merge_gate | test_green_batch_advances_last_green_and_red_does_not |
| 5 | Retire the pre-merge full tier (3.6) | 4 live 30 days; rec-5019 live; the measure read; an operator-approved Decision citation | handoff gate, git-ops.yaml handoff_sequence, implement skill, rec-5019's job | m1-m3 report; closes rec-4874 |

Shared files to serialise with other work: scripts/ci/push_scope.py and the main-validate job (plan 3; rec-4114's
PLAN-plans-only-main-fast-tier builds them first); docs/DECISIONS.md (plan 1; the SIT Decision plan, PR #1936, adds
dated notes to Decisions 73 and 249, lands first, and plan 1 re-anchors its annotations on them);
docs/contracts/git-ops.yaml (plans 2 and 5; also itinerary items A6, C1, C2, CR-flip); .claude/commands/implement.md and the implement skill (plan 5; also C2, CR-add, CR-flip, CR-remove),
.github/workflows/ci.yml (plan 3; also rec-4114 and rec-5019), the deploy workflows (plan 4; also rec-5028, rec-5033).

## 8. Known gaps and unproven points

- UNPROVEN: GitHub's pending-run replacement is relied on from its documentation, not from a run in this repo; plan 3
  proves it with a dispatched fixture.
- UNPROVEN: culprit-step minutes (2-8 min) and peak batch sizes are modelled from the audit, not measured.
- The fast tier's own 5-minute budget (Decisions 182 and 208) carries more weight once it is the only presubmit; no
  change is proposed, and plan 5's measure reads it.
- The local catch rate of today's pre-push full tier is not recorded anywhere (audit Q9); m1 measures the GitHub-side
  equivalent instead.
- Agent merges by gh or REST from a shell stay outside every merge gate (rec-2827), unchanged.
