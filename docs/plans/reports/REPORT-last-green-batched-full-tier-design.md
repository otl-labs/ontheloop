# REPORT: batched full tier on main, culprit finding, and a last-green ref deploys consume (rec-4874 design)

Plan: docs/plans/PLAN-last-green-batched-full-tier-design.yaml (REPORT-ONLY, V1). Written 2026-11-09 against main
70d86bef and re-anchored on 73485749 (2026-11-10). This report designs and drafts; it builds, files, closes and
flips nothing. The Decision in section 6 is a draft: it lands only through its own IMPLEMENTATION plan, with its
wording approved on GitHub (Decision 246).

Precondition for any later use of this report: re-run the plan's VP steps 1-4 (the premise probes) on the current
main. A step that no longer prints its expected line means a premise below moved; re-ground the section it names
before relying on it.

## 1. Problem and goal

- rec-4874 (operator direction 2026-11-03): the fast diff-aware tier is the only presubmit; the full tier runs on a
  batched run of main; a red batch is bisected to its culprit automatically; a green batch advances a ref every
  deploy channel consumes; a red blocks that advance, not planning or merging.
- Today three full-tier runs can cover one change: the session container before push (Decision 163 point 3,
  Decision 203), main-validate on every push to main (Decision 73 cl.2), and Main Canary on a 3-hourly cron. rec-5019
  plans a fourth, interim one: the full tier on GitHub for each ready PR head.
- Evidence: audits/ci-merge-throughput-scaling-269e630a.yaml. At H0 (41 merges on the peak day) main-validate is 59%
  of 49.4 job-minutes per merge; slots saturate near 123 merges a day as is, about 80 with the verdict personas,
  about 165 after rec-4114 (CMTS-06); red occupancy is 0.34 with a 3.6 h mean window and reaches 1.0 near 83 a day
  (CMTS-15).
- Scion (anthropic.com/engineering/redesigning-source-control, 2026-10-13), verbatim: "A proper merge queue, for
  example, doesn't make much sense at agent scale."; "we've invested in post-merge and pre-deploy automated
  testing."; "our automated bisect and revert pipelines" (curly apostrophes normalised to ASCII). The post says
  nothing about batching or culprit mechanics, so those below are this design's, not Scion's.

Goal: every check still runs on every merged tree; only its placement moves to post-merge. Production code never runs
a tree whose full tier has not passed. Merging never waits on main's colour.

## 2. Vocabulary

"Last-green ref" names the newest first-parent main commit whose own main-validate run passed the full tier. It is not
a "promotion": docs/contracts/environment-taxonomy.yaml:392-394 reserves that word for a gated deploy between deploy
tiers (audit CMTS-11). When SIT stands up (rec-5024, rec-5028, rec-5033), the last-green ref is what the merge-time
SIT deploy consumes, and the human-gated SIT-to-PROD step is the promotion. The build's first plan adds "last-green
ref" to the taxonomy's allowlisted terms. The term is fixed: the concurrent SIT Decision draft (PR #1936,
PLAN-sit-decision) already cites "rec-4874's last-green ref", so later wording keeps it verbatim.

Three terms, one meaning each:
- Covered: the last-green ref or one of its first-parent ancestors. Every full-tier check passed on a tree that
  contains the commit. A commit counts as green when any attempt of its run passed, so coverage, once earned, is never
  withdrawn (a failed re-run withdraws nothing).
- In range: inside a completed main-validate run's prev..head (3.1), whatever its conclusion.
- Head-checked: main HEAD's main-validate job is pending, or its full-tier step running or completed within one
  canary period (3 h). A plans-only run of the fast tier alone does not count, so a plans-only tail still gets the
  canary's full tier (Decision 205 cl.5).

Boundary: the last-green ref selects what may deploy to the first deploy tier (the sandbox account today, SIT once
rec-5028 lands). What is promotable to PROD (the identical artifact that ran on SIT, by CodeSha256 and saved plan) is
a stronger condition that rec-5033 defines: every promotable artifact comes from a covered commit, deployed to SIT
with every function's CodeSha256 matching and its saved plan applied, and SIT's checks passed after the pre-promotion
reset (rec-5033 scope note, 2026-11-09). The two are never the same ref. The SIT draft's approved words
(docs/plans/approved/sit-decision.yaml cl.6, PR #1936) say "promotion uses rec-4874's last-green ref"; that holds as a
necessary condition, not a sufficient one. Build plan 1 aligns the merged sentence with rec-5033's promotable
condition by annotation, or adds the SIT Decision to the amends if the merged text needs more than an annotation. (The
SIT draft's "SIT deploys keep 249 cl.2" and this design's cl.5 say the same thing once 249 cl.2's signal becomes the
last-green ref.)

## 3. Design

### 3.1 Batch vehicle

- ci.yml main-validate gains a JOB-level concurrency group, `main-full-tier` for a run's first attempt, with
  `cancel-in-progress: false` (.github/workflows/ci.yml:100-107 has none today; re-runs are keyed apart, below).
  GitHub keeps one running and at most one pending run in a group; a newer push cancels the pending one. A batch is
  therefore adaptive: at low merge rates every commit gets its own run (exact attribution, as today); at peaks one
  run covers every merge that landed while the previous run ran. GitHub's concurrency documentation (read by the
  architect review, round 2) gives a `queue` option whose default `single` keeps one pending run and whose `max`
  keeps up to 100; plan 3 pins `queue: single` explicitly, since `max` would silently turn batching off. GitHub does
  not promise FIFO order inside a group; the ancestry rule below keeps ranges correct in any order.
- The batch range is `prev..head` on first-parent history. prev is the nearest first-parent ancestor of head whose
  main-validate JOB started its full-tier step (was not cancelled while pending, and was not a plans-only run of the
  fast tier alone) and did not end timed_out or cancelled; a job still running counts as started. It is read at
  job level, because the trailer jobs keep the workflow run open after main-validate ends. Before plan 3 every push's
  job starts, so prev is HEAD^ and the range-aware consumers (3.8) are behaviour-neutral; under the group prev is the
  previous batch's head. Anchoring on the last started run, not the last green one, keeps a red's range from growing
  with every later batch; a fingerprint already red at prev is carried as known (3.5).
- A first step of main-validate resolves prev and passes it to validate in an environment variable, so validate makes
  no API call. A prev that does not resolve, or is not an ancestor of head, widens the base to the last-green ref; if
  that does not resolve either, the run fails red. It never falls back to HEAD~1, as push_context_base does today
  (scripts/checks/_common.py:83-96), because diff-aware full-tier checks (validate_weakening_candidates,
  validate_scope_boundary, validate_diff_coverage) would then see only the head's own commit while the last-green ref
  advanced over the rest. The run records the range in its evidence bundle.
- A main-validate job that times out, or is cancelled after it started, covers nothing: its commits fall into the
  next range, and it raises an alarm to the operator, so a hanging full tier never pauses deploys silently.
  Run-level conclusions mix these with batched-out cancellations (most runs conclude `cancelled` under batching, and
  ci-rca's gate, .github/workflows/ci-rca.yml:60, wakes on failure only), so both the alarm and prev's "started"
  test read the job itself: a runner assigned and a step executed. An always-run job in the CI workflow that reads
  the main-validate job's result raises the alarm; plan 3 proves with a fixture how a timeout surfaces at job level.
  A re-run of an older run (the sanctioned recovery re-run of a merge commit's push run,
  docs/contracts/git-ops.yaml:343-346; Decision 249 cl.4) must not join the batch group, or it would cancel HEAD's
  pending run: a re-run's group key includes its run id and attempt, so each re-run gets a group of its own and
  tests its own tree, and its pass counts for that commit (section 2). The canary backstop below re-checks any HEAD
  left without a run.
- Job-level, not workflow-level: the push run's other jobs (trailer-census, terraform-validate) stay per push, as does
  rec-autoclose.yml, a separate push workflow. A cancelled pending main-validate concludes `cancelled`; ci-rca wakes
  only on `failure` and the deploy channels only on `success`, so a cancelled pending run wakes neither.
- Main Canary keeps its cron and skips only when HEAD is head-checked (section 2). A HEAD whose last check is older
  than one canary period is re-run, which keeps Decision 73 cl.2's drift role and Decision 205 cl.5's backstop for a
  plans-only tail (whose push runs the fast tier once rec-4114 lands), and removes the same-SHA re-runs and
  duplicate recs of CMTS-08. (Advisor point 1 adapted: the advisor proposed dispatch-only; the scout's Decision 205
  flag and both report critiques show the backstop and the drift role must stay.)
- Plans-only filter (rec-4114, Decision 205 cl.5) is computed over the batch range, never the head commit alone, so a
  batch holding any non-plans commit runs the full tier (scout flag, Decision 205). The merged, unbuilt
  PLAN-plans-only-main-fast-tier classifies `github.event.before..github.sha` in scripts/ci/push_scope.py; under
  batching the vehicle plan passes prev as the base instead, and push_scope's own range test keeps the classifier and
  push_context_base on one base.
- Capacity (modelled, not measured): at the H0 peak (8 merges an hour, audit r_peak) and a 30-minute run, a batch
  holds about 4 merges, cutting main-validate starts at peak by about that factor; at the H0 daily average (1.7 an
  hour) nearly every commit still gets its own run. No capacity trigger gates the vehicle: below the batching rate
  it behaves as today.

### 3.2 The last-green ref

- Authoritative form: a derived query, never a stored value. scripts/ci/deploy_gate.py:92-113 (`newest_green_sha`)
  already returns the newest first-parent main commit whose own push run passed the main-validate full-tier step.
  Under batching, a commit whose main-validate was cancelled while pending has a CI run concluding `cancelled`,
  which the resolver already skips, so the newest green commit is a batch head by construction: the deploy selection
  itself needs no new rule. What changes: the resolver moves to a shared module so ci-rca, push_context_base and the
  junit binding use one definition; it answers "covered" (section 2) for consumers that ask about a specific commit,
  counting a commit green when any attempt of its run passed; and its per-commit runs query over a fixed 50-commit
  walk-back (deploy_gate.py:101-110, `limit: int = 50`) becomes one paginated listing of main-validate runs
  intersected with first-parent history, behind a hard page cap. Calls then scale with the runs in the walk-back,
  not the commits: one listing call per 100 runs plus one attempts-and-jobs call for each run whose latest attempt
  did not succeed, since a listing shows only the latest attempt (green is judged on the main-validate job, any
  attempt, so a failed re-run of a passed run never moves the ref back), which keeps the GITHUB_TOKEN budget the
  audit models; past the cap the gate fails closed and alarms, as it fails closed today.
- A git tag or GitHub deployment record may mirror it for display; it is never read as authority. A mutable ref
  would need a write credential on main and a ruleset change (frozen by rec-4624, Decision 245 cl.5) and could drift
  from the run evidence it summarises.
- rec-4874's acceptance test is renamed on build to tests/ci/test_last_green_ref.py::
  test_green_batch_advances_last_green_and_red_does_not (rec-4874's acceptance is updated by update_rec in that plan).

### 3.3 Culprit step

- On every red main-validate run, whatever its range size, a culprit job runs. It is deterministic, sits in the ci-rca
  workflow before and outside the ci-rca agent's job, and has its own timeout, so an overrun never loses the rec
  (the agent's job has 25 minutes, .github/workflows/ci-rca.yml:62). Its re-run targets are the failing checks and
  junit node ids from the run's validation result; the fingerprint (Decision 142, scripts/ci_rca/fingerprint.py) keys
  dedup only and names no check.
- Flake screen first: each failing node is re-run twice on the head. A node passing both is flaky and routed as
  today's flaky class with no culprit, and the job dispatches one fresh main-validate run on HEAD (at most one per
  red) so a green can resume the last-green advance.
- Pair second: the failing checks, live-AWS ones included, are re-run at HEAD and at prev against today's
  environment. A check that also fails at prev was not caused by the range (environment drift, an expired credential,
  an earlier red): no culprit and no revert PR; the halt stays on the failing nodes' files (3.5). The repair closes by
  dispatching a main-validate run on HEAD (a red-main-recovery.yaml procedure step, plan 4), since after an
  out-of-band fix such as a credential rotation no merge may come soon and a canary pass is not deploy evidence
  (Decision 249); until that run the last-green ref stays where it is, and the staleness alarm below catches a
  forgotten dispatch.
- Dispatch, not re-run: ci.yml gains a workflow_dispatch trigger on main (plan 3), and every "run on HEAD" in this
  design is a fresh attempt-1 run, because GitHub does not reliably fire workflow_run when a re-run completes
  (terraform-apply-sandbox.yml:824-830, run 28379330706). A re-run that does happen (attempt 2 and above) follows that
  file's precedent: its own completion step dispatches ci-rca and the deploy channels. Every filter that admits only
  push runs widens to a workflow_dispatch run on main, or a dispatched green would neither advance the last-green ref
  nor wake a deploy: main-validate's `if: github.event_name == 'push'` (ci.yml:101; plan 3), the resolver's
  `event: push` query (scripts/ci/deploy_gate.py:103) and both deploy channels' `workflow_run.event == 'push'`
  (deploy-ducklake-lambdas.yml:139-140, deploy-prod-lambdas.yml:87-88; plan 4).
- Staleness alarm: when main HEAD has moved past the last-green ref and the ref is older than a threshold (plan 3 sets
  it from the measured red window), the operator is alerted. It is the catch-all for a hung or silently cancelled
  run, an environment red nobody re-dispatched, and a missed wake.
- Then, for a range of more than one commit, the failing hermetic checks re-run on each first-parent commit, oldest
  first, stopping at the first that fails; a one-commit range is its own culprit once the pair holds. Linear, not
  binary: batches are 1-4 commits at H0; binary search is the later change if batches pass about 8. Only hermetic
  checks re-run per commit: live-AWS checks (the DQ runner, the verifier harness under the branch role) do not
  reproduce at older commits and attribute to the range.
- Whole-tree checks that do not localise to a commit (coverage floors, size ratchets, validate_graduated_rerun):
  attribute to the range commit whose diff touches the measured files; otherwise attribute to the whole range and
  file one rec naming every commit.
- Budget: about 2-8 minutes per red batch plus the two head re-runs (advisor estimate, unmeasured). Cost scales with
  reds, not merges.

### 3.4 Remediation (fork A: the operator chose the opened revert PR, 2026-11-09)

- Forward-fix stays the default (Decision 73 cl.3, Decision 226 cl.4). When the culprit is a Track B commit outside
  terraform/** and governed deploy paths, CI also OPENS a revert PR of it, never auto-merged. The repairing session
  merges it or closes it when the forward fix lands. Automated revert stays excluded: Decision 73 cl.3's reason
  (moving main under live worktrees) binds a merge, and opening a PR moves nothing. The operator's card said "any
  Track B culprit"; this design keeps Decision 226 cl.4's terraform/** and deploy-path exclusion unchanged, because
  Terraform applies on push ungated by the last-green ref (3.7), and build plan 1's wording approval (Decision 246)
  puts that exclusion in front of the operator explicitly.
- The opener is deterministic, not an LLM, and runs in its own job after the ci-rca agent files the rec, since Track
  B is known only from the filed rec's derived priority (red-main-recovery.yaml priority row), and only when the
  culprit job named a culprit by the pair (3.3). It pushes a `git revert` of the culprit SHA to a branch named
  `agent/revert-<culprit sha>`, so a repairing session may push to it and signal-green wakes on it
  (docs/contracts/git-ops.yaml:108, 518-521; plan 3 adds the exception to git-ops.yaml's branch rule that a session
  works only on its harness-assigned branch), with a commit trailer marking it as opener-made, and opens the PR,
  whose body names the rec id (the agent job's output). So Decision 205 cl.1 (an LLM persona on
  Actions is verdict-shaped, never a commit) does not bind, and the ci-rca agent's "Do not propose or execute any
  autonomous fix" (.claude/agents/scheduled/ ci-rca.md:205) stays true of the agent. The pipeline does now propose a
  fix, though, so the Decision amends Decision 72's "does NOT propose or execute any autonomous fix"
  (docs/DECISIONS.md:11539), as Decision 226 narrowed it to a manual revert PR, and Decision 226 cl.4's "manual" and
  "when the fix is not obvious" conditions (not 73 cl.3). A deterministic opener is the recovery Decision 55 already
  allows ("Deterministic recovery remains valid"), so 55 is cited, not amended.
- Weakening (Decision 235): reverting a culprit that added the failing check, assertion or budget removes a check
  that is red on main, which is weakening to obtain green. The opener files the rec only, with no PR, when the revert
  diff carries a weakening candidate (Decision 187's base-ref derivation) or touches Decision 235 cl.5's floor.
- Credential: a GitHub App installation token minted in the opener's job the way the terraform-github workflows mint
  theirs (actions/create-github-app-token, this repository only, per-permission inputs), limited to contents and
  pull-requests write, with checkout credentials not persisted and no LLM step in the job. The App is a dedicated
  one the operator creates out of band; its private key is a secret of a main-only GitHub Environment, as
  tf-trusted-plan holds the plan App's (terraform/github/environments.tf:62-89), because a repository secret is
  readable by any same-repo branch's pull_request workflow. Decision 248 rejected an Environment secret for the
  terraform/github apply App (no AWS audit or resource policy for an admin key); the opener's App holds contents and
  pull-requests write only, so an Environment secret with a main-only policy is proportionate, and plan 3 says so.
  The Environment is declared in terraform/github and applied through Decision 248 cl.3, with gate_change naming it.
  The Environment has no admin bypass and the App is not
  in the ruleset's bypass list, so only main's opener job can mint the token and it cannot skip required checks (the
  Decision 248 intent: a control an agent cannot satisfy for itself); the job never calls a merge. The key's home is
  an Actions secret (Decision 224 cl.2); plan 3 adds its project-configuration.yaml and account-credential-inventory
  rows (224 cl.6), names the operator step and its check, and records that this App is not the executor lane's
  single non-bypass App (Decision 217 cl.3). A PR opened with GITHUB_TOKEN starts no workflow runs, so the App token
  is what makes the PR's own required checks (pr-validate, terraform-validate) run. Unlike 3.2's rejected mutable
  ref, the opener writes only a new branch, never main, so no ruleset change is needed.
- Guards (consult 2 and both report critiques): a culprit is named only on a passes-at-parent, fails-at-culprit pair
  of re-runs; the opener dispatches the full tier on the revert head (rec-5019's ready-head job while it lives, a
  dispatched full-tier run otherwise), since a revert that applies cleanly can still break later commits built on the
  culprit; it never reverts a revert it opened, recognised by its trailer (a red whose culprit is such a revert files
  the rec only); when later
  merges stop the revert applying cleanly, it files the rec only. The opener writes only the revert: the repairing
  session adds the Decision 186 closure artifact (red-main-recovery.yaml:38-42, for example a test pinning the
  culprit's failure) to the PR before merging it, since the revert alone changes no artifact file.

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
  promotable (confirmed to the CI Reform project through the operator, 2026-11-09). A defect found later in a
  covered commit is fixed forward like any other.
- Merging: never blocked by main's colour.
- Planning and dispatch: Decision 226 cl.3's overlap test stays, with its fail-closed rules (an undeterminable red,
  a selector fallback or a failed read blocks) and its Track A clear inside the SLO. Its affected set is the rec's
  file plus the failing nodes' files, expanded through the dependency selector (scripts/ci_rca/red_overlap.py:41-44,
  96); no run range or manifest feeds it, so batching does not widen it (audit dd_c item 7 assumed it would). What
  the culprit job changes is the rec's file: the agent takes it from the culprit's diff once named, rather than
  choosing among the range's commits, and a red no commit caused (3.3) names its failing nodes' files only. Past
  the 4 h SLO, an alarm to the operator replaces cl.4's block on every new plan and dispatch (and the Decision 73
  cl.4 fallback that block names), since production is already protected by the deploy pause and a global halt adds
  no repair capacity. The operator also sees a count of open reds, so a pile-up stays visible without the halt's
  back-pressure (consult 2).
- Masking and dedup: a later batch failing on a fingerprint already open appends evidence to that rec through today's
  open-fingerprint bump (docs/contracts/ci-rca-lifecycle.yaml:295); a new fingerprint files a new rec. The range is
  evidence on the rec, never part of the key. The fingerprint hashes the workflow slug (scripts/ci_rca/
  fingerprint.py:34-43), so cross-slug dedup (a canary red and a main-validate red of one cause) needs a slug-free
  cause key, which plan 2 designs (CMTS-07, CMTS-08).

### 3.6 Presubmit end state and its measured retirement (fork C: the operator kept rec-5019 as the interim, 2026-11-09)

- End state: the fast tier is the only presubmit. The local pre-push full tier (Decision 163 point 3, Decision 203)
  and rec-5019's per-PR GitHub full tier both retire. Decision 203's push gate keeps its destination and
  canonical-form refusals and drops the full-tier evidence requirement.
- Order: rec-5019 lands first as the interim (validation on GitHub soonest) and itself retires the local pre-push
  full tier (operator ruling 2026-11-09 20:50 UTC: rec-5019 removes the local full tier only, never the optional
  local fast tier). Its plan therefore owns the local-tier surfaces. Decision 203's gate refuses the push itself, so
  it can never hold a ready-head check, which exists only after the push: the gate drops the local full-tier
  evidence requirement (scripts/session/handoff_evidence.py) and keeps its destination and form refusals, and the
  ready-head check gates readiness or merge, at the point rec-5019's plan decides. handoff_sequence, the implement
  skill, the AGENTS.md tier table and docs/PROJECT_CONTEXT.md:144 change with it.
  Draft cl.7 records that replacement; if rec-5019's plan merges before build plan 1 lands this Decision, that plan
  carries the Decision 163 pt.3 and 203 amendment itself and build plan 1 cites it. From then the ready-head GitHub
  run is the only pre-merge full tier, and its check runs are the server-side record the measurement below needs.
- Pre-registered measure (NS.6; scout note), read over the 30 days after rec-5019 has replaced the local full tier
  and the culprit job is live: (m1) pre-merge catches per 100 merges: ready-head GitHub full-tier failures on a
  check that passed --pre on the same head; (m2) red occupancy and mean red window on main; (m3) projected occupancy
  without the pre-merge tier: m2 + (m1 / 100) x M x W / 24, where M is merges per day and W the mean red window in
  hours over the same 30 days. With no local full tier screening in front of it, m1 sees everything the pre-merge
  tier catches; if sessions still run the full tier locally in that window, m1 and m3 are reported as lower bounds
  and cannot support retirement on their own. m1 excludes a ready-head failure on a fingerprint open as a main red
  at the time, since the head inherits that red rather than catching it; whether such a failure holds the PR during
  the interim (merging waiting on main's colour) is rec-5019's to decide. Retirement is proposed when m3 stays under
  0.5 (CMTS-15's threshold). The condition is measured, never a date; if the end state were weeks away, rec-5019
  would be throwaway and the catch rate would come from handoff evidence instead (consult 2).
- What the measure cannot show (Decision 235): a rare-regression tripwire that never fires looks redundant. No check
  is retired here; every check still runs on every merged tree post-merge. The retirement moves placement only, and it
  lands only through a later Decision the operator approves; the measure is an input, never the decider.

### 3.7 Decision 245's merge-overlap refusal (fork D: the operator chose to narrow it, 2026-11-09)

- Narrow the refusal to overlapping files under terraform/** and the governed deploy paths
  (docs/contracts/deploy-paths.yaml), where a red is not cheap to forward-fix: Terraform applies on push, ungated by
  the last-green ref (Decision 249 cl.5), and will apply to SIT on every merge. Elsewhere a stale merge's combined
  tree is tested by the next batch run and its culprit found by 3.3. Its reversal condition tap-promotion-lands fires.
- Guards (consult 2): the narrowing ships only after the culprit step has named the culprit correctly on three real
  multi-commit reds with no misattribution, so it is its own evidence-gated plan (plan 6, consult 3). The
  update-branch remedy's attempt cap with escalation (audit CMTS-03) carries no such condition and ships in plan 4.
  Cost: more same-file reds on non-infra paths (2 of 38 merges overlapped in Decision 245's replay) and shared ratchet
  lines exposed until rec-4873.
- The auto-merge residual (mcp__github__enable_pr_auto_merge checked only at enable time; Decision 245 cl.6) becomes
  harmless outside those paths for the same reason.

### 3.8 Range-aware consumers (the prerequisite slice)

Every consumer that assumes one full-tier run per push (audit CMTS-07 dd_c, lines 123-151) changes first. Each change
is correct under one run per push too (prev is HEAD^ until plan 3, 3.1), so this slice ships before batching starts.

| Consumer | Today | Change |
|---|---|---|
| ci-rca.yml anchor, PR lookup, selection manifest | head_sha; one PR via commits/{sha}/pulls (ci-rca.yml:258-281) | range prev..head; one PR per first-parent commit |
| scripts/ci_rca/vacuous_pass.py:69-92 | git diff HEAD^ HEAD | diff over the range |
| ci-rca dedup | commit statuses of one anchor per slug (ci-rca.yml:338-372) | the open-fingerprint bump; range carried as evidence; a slug-free cause key across slugs (3.5) |
| ci.yml trailer-junit-verdict and trailer census (needs main-validate, same-run artifact) | same-run provenance (Decision 201; git-ops.yaml trailer_acceptance_gate.source_admission); the census reads HEAD's message only (scripts/rec_trailer_acceptance.py:283-285) | the covering run censuses the junit-routed entries still open for merge commits in its range (not every trailer again); a verdict binds to the first completed run whose range contains the merge commit, and Decision 201's closing-sha key becomes that merge commit; the junit report measures the head tree, which contains it; closure latency grows to up to about two full-tier durations, so the merge_step readback and implement closeout window (git-ops.yaml:338-341), which assume closure inside the push run, widen to the covering run instead of routing to recover |
| scripts/ci_rca/red_overlap.py | the rec's file plus the failing nodes' files, expanded by the dependency selector (red_overlap.py:41-44, 96); no run input | unchanged; the rec's file comes from the culprit's diff once named (ci-rca agent prompt), and a red no commit caused names its failing nodes' files only |
| scripts/checks/_common.py:41 push_context_base | prefers GITHUB_EVENT_BEFORE, then HEAD~1 (Decision 159 cl.1; :83-96) | prev from the workflow's environment variable; unresolvable widens to the last-green ref or fails red, never HEAD~1 (3.1; scout flag, Decision 159) |
| validate_graduated_rerun (Decision 244 cl.1 "before handoff and on main") and validate_graduated_rerun_plan_readers (244's 2026-11-09 Update: fast tier, docs/plans/ diffs) | full tier both legs; the plan-reader leg reads the push diff | the "before handoff" leg follows 3.6; whole-tree attribution per 3.3 (scout flag, Decision 244); the plan-reader leg inherits the range through push_context_base (plan 2 lists it) |

Unaffected (audit dd_c items 3-5): Decision 226's is_track_a workflow tier (both CI and Main Canary are tier CI),
rec-autoclose.yml (per push, DECISIONS.md diff), the merge gate, signal-green and pr-conflict-signal (per PR). Items 6
(deploy gate) and 8 (plans-only filter), which the audit marks unaffected, change in 3.2 (resolver) and 3.1 (range).

### 3.9 Deploys and Terraform

- Code deploys: Decision 249 cl.2's green signal becomes the last-green ref; cl.3's pause is unchanged. Decision 73
  cl.5's future-state green-streak train (24 h on sandbox before SIT, kept by 249 cl.1) is the SIT Decision's (PR
  #1936) to change, since the merge-time SIT deploy consumes the last-green ref.
- Terraform apply stays on push behind the Decision 77 deterministic guard (Decision 249 cl.5). Its licence is the
  guard and its human-gated fail-closed set, not test colour, and a red main must not block a drift repair. SIT's
  plans (rec-5028, rec-5033) move auto-apply to SIT; this design does not.
- Config and code drift: terraform/personal owns each DuckLake Lambda's handler and environment
  (terraform/personal/ducklake_lambdas.tf:303-316, with `ignore_changes = [source_code_hash]`), and that config
  applies on push while code waits for the last-green ref. Today the gap is one full-tier run; under batching it is
  a batch slot plus any red. So a change to a Lambda's config and its code follows expand/contract: the config
  applied on push must work with the code already deployed, and code that needs a new config lands only after that
  config does, or together with it when the config change is backward-compatible. The same holds for the Lambda
  layers terraform pins (ducklake_lambdas.tf:311). Plan 4 records the rule in deploy-paths.yaml and adds a check
  that flags a PR changing both a Lambda's Terraform config or layers and its code path; if no cheap check exists, a
  child rec owns the residual (Decision 181 cl.2), so the rule is never declared without an executor (Decision 163
  pt.2).
- Capacity: rec-5028 (every merge applies terraform/personal and deploys to SIT) and rec-5032 (per-PR Lambda stacks)
  add Actions load beside this design's savings; rec-5022's gauge reads all of it.

## 4. Options considered and not taken

- GitHub merge queue or strict up-to-date (Decision 100's managed primitive): both gate merges serially, the operator
  judged that unscalable, the admin bypass skips both, and the ruleset is frozen (Decision 245 cl.5; scout note,
  Decision 100). Not taken.
- Fixed-cadence scheduled batch: GitHub cron delivers 4-7 h apart (rec-4233), and a fixed cadence adds latency at low
  rates. Not taken; the cron remains only as the head-check backstop (3.1).
- Separate batch dispatcher (EventBridge or a scheduler): added state for no gain at H0. Revisit with rec-4233.
- Mutable ref as authority: write credential on main, ruleset change, drift (3.2). Mirror only.
- Full-tier bisect: about 29 minutes per probe. Fingerprint-scoped re-run instead (3.3).
- Automated revert: excluded by Decision 73 cl.3 and 226 cl.4; an opened, never auto-merged revert PR (3.4) is the
  bounded form.
- Union attribution only: over-blocks planning and names no owner. Kept only as the fallback (3.3).
- rec-4790(a) evidence carry-over: removes one rebase race, keeps the local full tier; superseded by 3.6's end state.

## 5. Existing recs and Decisions this resolves or re-scopes (dispositions proposed for the build plans)

- rec-4874: closed by build plan 5. rec-5019: built as the interim, retiring the local full tier itself; its per-PR
  job is retired later by build plan 5.
- rec-4790: superseded by rec-5019's build (no local full tier left to race; the itinerary already pairs them);
  options (a) and (c) not taken.
- rec-4451 (main moves after the merge ref): harmless under last-green gating outside terraform/deploy paths; the
  residual inside them is the narrowed Decision 245 gate.
- rec-4085, rec-4086, rec-4087, rec-4118 (local handoff gate hardening): each re-scoped when rec-5019's build makes
  the gate accept the ready-head check instead of local full-tier evidence; that plan decides close or keep per rec.
- rec-4873 (merge-robust ratchets): independent; still needed, because a combined-tree crossing now surfaces as a
  batch red attributed by 3.3.
- rec-4902 (runner proof of full-tier-only checks): satisfied by rec-5019's ready-head run while it lives; after 3.6
  the batch run is the first runner proof, so rec-4902's obligation is re-decided then.
- rec-4114: orthogonal; computed over the range (3.1). rec-4233: the canary's cadence. rec-5022: telemetry for m1-m3
  and capacity, not a gate.
- Decision reversal conditions that fire: 245 tap-promotion-lands, 249 canary-model-decided.

## 6. Decision draft

Envelope and amendment annotations are added by the landing plan (decision-entry.yaml). The size check counts the
whole entry against 6,144 bytes, so the text below stays at or under 4,700 bytes to leave room for the envelope and
the reversal-condition YAML; the landing plan measures the full entry and maps cl.9 to the child-rec ids it files
(Decision 181 cl.2). Bracketed text marks the operator's fork answers of 2026-11-09 (A-D).

```text
## Decision N: The full tier runs post-merge on batches of main, a red batch is bisected to its culprit, and code
deploys consume the newest commit a passing run covers; a red pauses deploys, never merging (amends Decisions 72,
73, 159, 163, 201, 203, 205, 226, 244, 245 and 249)

significance: numbered_decision -- it moves every full-tier check to post-merge placement and redefines what a red
blocks; a contract note records mechanisms, and an annotation cannot change a ratified clause's meaning.
kind: direction -- clause 5 decides what production may run and clause 6 what merging waits on.

Problem: three full-tier placements cover one change (session container, every push to main, a 3-hourly canary).
The per-push run is 59% of Actions job-minutes per merge; red occupancy is 0.34 (audit
ci-merge-throughput-scaling-269e630a).

Intent: every merged tree still meets every check; production runs only covered trees; merging never waits on
main's colour.

1. Batched full tier (amends 73 cl.2, 205 cl.5). main-validate runs in one non-cancelling concurrency group, re-runs
   apart; a run covers prev..head on first-parent history, prev being the nearest ancestor whose full tier started and
   did not time out or get cancelled. An unresolvable prev widens to the last-green ref, never HEAD~1; a timed-out or
   cancelled running job alarms outside ci-rca. Plans-only detection reads the range. Main Canary skips a head whose
   full tier is pending, running or under 3 h old.
2. Last-green ref. The newest first-parent main commit whose own run passed the full tier, derived from run evidence,
   never stored as authority. It and its ancestors are covered; coverage is never withdrawn. It is not a promotion.
3. Culprit step (amends 244 cl.1). A deterministic job screens every red: a node passing two head re-runs is flaky; a
   check also failing at prev names no culprit; otherwise hermetic failing checks re-run per commit, oldest first; a
   whole-tree check that does not localise is attributed to the range. [A: After the rec is filed, a deterministic
   job outside the ci-rca agent opens, never merges, a revert PR of a Track B culprit outside terraform/** and
   governed deploy paths, never one that weakens a check (235), runs the full tier on its head and never reverts its
   own revert; the repairing session adds the Decision 186 artifact (amends 72's no-autonomous-fix rule and 226
   cl.4's "manual" and "not obvious").
   Automated revert stays excluded (73 cl.3).]
4. Range-aware evidence (amends 159 cl.1, 201). ci-rca anchors, diffs and resolves PRs over the range and dedups on
   the open fingerprint; the covering run censuses its range's open junit-routed recs, and a junit verdict binds to
   the first completed run whose range holds the merge commit; push_context_base anchors on prev.
5. Deploys (amends 249 cl.2). Code-deploy channels to the first tier consume the last-green ref; a red pauses them
   (249 cl.3) and never withdraws coverage. Terraform apply stays on push behind the Decision 77 guard. Every tier is
   fixed forward; what is promotable to PROD, and promoting an earlier record, are rec-5033's.
6. What a red blocks (amends 226 cl.3-4, 73 cl.3-4). Never a merge. Plan and dispatch keep 226 cl.3's overlap test,
   fail-closed reads and Track A clear; a named culprit's diff supplies the red's file. [B: Past the SLO an
   alarm replaces the block on all new plans and dispatch.]
7. Presubmit end state (amends 163 pt.2-3, 203). Once rec-5019's per-PR GitHub run is live, 203's push gate drops
   the local full-tier evidence, keeping its destination and form refusals, and that run's check gates readiness or
   merge. It retires in turn, leaving the fast tier the only presubmit, only through a later Decision the operator
   approves, which also amends 226 cl.2, 244 cl.1's handoff leg and 217 cl.2, citing 30 days of pre-registered
   measures taken after that (pre-merge catches per 100 merges, red occupancy, projected occupancy without the
   pre-merge tier under 0.5). The measure informs; it never decides (Decision 235).
8. Merge-overlap refusal (amends 245). [D: Narrowed to overlapping files under terraform/** and governed deploy
   paths once the culprit step has named three real culprits correctly; the update-branch remedy gains an attempt
   cap.]
9. Coverage (Decision 181 cl.2): owned by the child recs build plan 1 files for build plans 2-6.

reversal-conditions: culprit-misattribution (two wrong culprits in 30 days: revisit cl.3); red-occupancy (above 0.5
for 7 days: revisit cl.6-7); batch-size (median batch above 8: binary search or sharding); sit-stood-up (rec-5028
lands: re-key cl.5 to the SIT deploy).
```

## 7. Decomposition (Decision 247 atomic IMPLEMENTATION plans; split, order and dependencies settled by consults 1-3)

Child recs are filed by the Decision-landing plan, not now, so a change to the Decision's wording leaves no orphans.

| # | Plan | Depends on | Scope sketch | Proof |
|---|---|---|---|---|
| 1 | Land the Decision, taxonomy term, Decision 245/249 reversal annotations, SIT cl.6 alignment (section 2); file child recs for plans 2-6 | this plan merged; the SIT Decision (PR #1936) merged | docs/DECISIONS.md, environment-taxonomy.yaml, decisions-index.json | wording-check PASS; approval run (Decision 246) |
| 2 | Range-aware consumers (3.8) and the shared last-green resolver (3.2) | 1 | ci-rca.yml, scripts/ci_rca/**, trailer census and junit jobs, scripts/rec_trailer_acceptance*.py, git-ops.yaml PROVENANCE, trailer_acceptance_gate and the merge_step readback window, scripts/session/merge_step.py, ci-rca-lifecycle.yaml, scripts/checks/_common.py, the resolver module | fixture batch of three merges, one failing test: one rec naming the range's PRs, two junit-routed recs closed, the red_overlap halt set unchanged (the rec's file plus the failing nodes' files through the selector); with prev = HEAD^ every consumer's output is unchanged |
| 3 | Vehicle, prev step, timed-out alarm, culprit job, canary head-check skip, revert-PR opener [A] (3.1, 3.3, 3.4) | 2; rec-4114's PLAN-plans-only-main-fast-tier built (constraint: scripts/ci/push_scope.py exists on main, else stop) | ci.yml main-validate concurrency (`queue: single`, re-run key), workflow_dispatch trigger, prev step, attempt-2 self-dispatch, the job-level timed-out alarm and the staleness alarm, main-canary.yml, ci-rca.yml culprit and opener jobs, .claude/agents/scheduled/ci-rca.md (the rec names the culprit; the PR body names the rec), the App-token mint and its main-only Environment (terraform/github, Decision 248 cl.3), git-ops.yaml's branch-rule exception and AGENTS.md's Branching paragraph | a dispatched batch on a fixture branch: one rec naming the culprit PR, halt set equal to the culprit's affected set, a revert PR whose head runs CI; a canary run on a head-checked HEAD concludes skipped |
| 4 | Deploy gate on the paginated last-green resolver; rec-4874 acceptance test; halt and alarm rules [B]; update-branch attempt cap; expand/contract rule (3.9) | 3 | scripts/ci/deploy_gate.py, tests/ci/test_last_green_ref.py, red_overlap and red-main-recovery.yaml, merge_gate, deploy-paths.yaml | test_green_batch_advances_last_green_and_red_does_not |
| 5 | Retire the per-PR GitHub full tier (3.6) | 4 live 30 days; rec-5019 live, which retired the local full tier and owns its surfaces; the measure read | the retiring Decision, the handoff gate's ready-head evidence requirement, rec-5019's job, the AGENTS.md tier table row and docs/PROJECT_CONTEXT.md:144 again (post-merge only) | m1-m3 report and the operator-approved Decision; closes rec-4874 |
| 6 | Narrow Decision 245's refusal [D] (3.7) | 4; three real multi-commit reds whose culprit the step named correctly, none misattributed | merge_gate and merge_overlap, Decision 245 annotation | the three culprits cited; a stale same-file merge outside terraform/** and deploy paths is allowed, one inside them refused |

Shared files to serialise with other work: scripts/ci/push_scope.py and the main-validate job (plan 3; rec-4114's
PLAN-plans-only-main-fast-tier builds them first); docs/DECISIONS.md (plans 1 and 5; the SIT Decision plan, PR #1936,
adds dated notes to Decisions 73 and 249, lands first, and plan 1 re-anchors its annotations on them);
docs/contracts/git-ops.yaml (plans 2 and 5; also itinerary items A6, C1, C2, CR-flip); .claude/commands/implement.md,
the implement skill and AGENTS.md (plan 5; also C2, CR-add, CR-flip, CR-remove); .github/workflows/ci.yml and
ci-rca.yml (plans 2 and 3; also rec-4114, rec-5019 and T4.26's ci-rca lane move); the deploy workflows and
deploy-paths.yaml (plan 4; also rec-5028, rec-5033).

## 8. Known gaps and unproven points

- UNPROVEN: GitHub's pending-run replacement, `queue: single` and the re-run group key are relied on from
  documentation, not from a run in this repo, and how a job timeout surfaces at job level (a run-level `cancelled`
  hides it) is inferred; plan 3 proves all of them, the timed-out alarm and the staleness alarm with a dispatched
  fixture.
- UNPROVEN: culprit-job minutes (2-8 min plus two head re-runs) and peak batch sizes are modelled from the audit, not
  measured.
- The fast tier's own 5-minute budget (Decisions 182 and 208) carries more weight once it is the only presubmit; no
  change is proposed, and plan 5's measure reads it.
- The local catch rate of today's pre-push full tier is not recorded anywhere (audit Q9). m1 measures the GitHub-side
  pre-merge tier after rec-5019 has replaced the local one, so nothing screens in front of it.
- The opener's App token is a new write credential in CI. Its minted permissions limit it to contents and pull
  requests, and its job to new branches; a branch ruleset that refuses the App on main would make that structural,
  and plan 3 proposes it if the frozen ruleset (rec-4624) allows by then.
- Junit closure waits for the covering run, up to about two full-tier durations after a merge.
- Agent merges by gh or REST from a shell stay outside every merge gate (rec-2827), unchanged.
