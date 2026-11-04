# AUDIT: CI merge throughput, attribution and promotion timing

## 1. TASK

You are auditing, read-only, how this repository moves a change from an agent's branch to production:
the presubmit and merge path, the agent handoff path, post-merge verification and Main Canary, CI-RCA
attribution and red-main recovery, the deploy channels, the designed-but-unbuilt scale commitments, and
the GitHub Actions capacity all of these share (surfaces S1-S7, SCOPE). The repository is developed by
many concurrent Claude Code sessions. Merges to main grew roughly fivefold in the four weeks before this
prompt was composed. The requester needs three things. First, what to fix now: agents strand during
handoff, and conflicts are constant. Second, what must change before merge volume reaches 10x and 100x
the current peak. Third, timing: what can wait, and on which measured trigger. You answer nine questions
(Q1-Q9, THE QUESTIONS). In brief: Q1 handoff liveness; Q2 conflict sources; Q3 the limit of per-commit
post-merge verification and per-PR attribution; Q4 where each Actions workload runs; Q5 deploy gating
and the canary's role; Q6 industry rating at 10x and 100x; Q7 Scion relevance and Decision 217's
triggers; Q8 sequencing and timing; Q9 questions the requester did not ask. You rate the surfaces on a
pinned rubric and emit the pinned output contract. Your deliverables are exactly two files:
`audits/ci-merge-throughput-scaling-<sha>.yaml` and `audits/ci-merge-throughput-scaling-<sha>.md`,
where `<sha>` is the short origin/main SHA you derive in COMMIT / PR MECHANICS step 1. The ONLY files
you create or modify in the repository tree are those two deliverables. Regenerating gitignored local
caches per SETUP is expected and does not breach that boundary; never commit them. You draft; the human
disposes. You open a pull request and end your turn. You never merge, never file recommendations, and
never edit any audited surface.

## 2. CANDIDATE OBSERVATIONS vs VERDICTS

This prompt hands you FACTS (GROUNDING MAP) and CANDIDATE hypotheses (C1-C16 below). It hands you no
verdicts. ASSUME NO CANDIDATE IS A REAL DEFECT UNTIL YOU TRACE IT. A run that merely confirms the
candidates below has failed. Your value is in adjudication, in what you find that is not listed, and in
the timing model.

Adjudicate every candidate to exactly one outcome. Each outcome maps to the output contract as follows:

| Outcome | Goes to | Classification |
|---|---|---|
| CONFIRMED-defect, no owning roadmap item or recommendation | `findings[]` | `novel` |
| Planned: an owning item or recommendation exists but its remedy is insufficient for the scale this audit models | `findings[]` | `planned-insufficient` |
| Planned: an owning item or recommendation exists, its remedy would suffice, and it is unbuilt with no sequencing that lands it before the trigger you compute | `findings[]` | `planned-unbuilt` |
| Planned and fully covered, with sequencing adequate to the trigger | `rejected_candidates[]` | n/a (name the owner in `decision_or_item_id`) |
| Not a defect | `rejected_candidates[]` | n/a (name the compensating control and its property match) |

Candidates (neutral hypotheses; the anchors are in the GROUNDING MAP):

- C1. Every push requires a local full-tier run on the rebased tree. That run may take longer than the
  interval between merges to main at peak, which may leave an agent's time-to-push unbounded.
- C2. A backgrounded full-tier run's completion may never reach a subagent session, for example an
  implementer an orchestrator dispatched. That would leave the subagent without a wake.
- C3. The merge gate's remedy (update the branch, wait for the CI-green wake, merge) has no stated
  attempt cap. It may repeat without bound while main keeps changing an overlapping file.
- C4. Files that many merges touch fall outside the conflict classes the rebase helper resolves
  mechanically. They may produce human-stop conflicts at a rate that grows with merge volume.
- C5. Exact-equality size ratchets with committed numbers may make concurrent PRs collide on one line.
- C6. `main-validate` runs a full tier on every push with no concurrency group. Under the Free-plan
  concurrent-job cap, queueing may grow non-linearly with merge rate.
- C7. CI-RCA attribution and rec closure may both depend structurally on one full-tier run per
  push-to-main commit. Batching that run would then break both.
- C8. Main Canary runs the same full tier as `main-validate`. It re-runs unchanged SHAs, and its observed
  start-to-start interval varies widely. What it detects that `main-validate` does not may be undefined.
- C9. Production deploy channels trigger on push in parallel with the full tier, so they may deploy a
  commit whose full tier later fails.
- C10. Decision 217's scale-readiness triggers key on executor concurrency, which is frozen at 1. Merges
  from interactive sessions may already exceed what those triggers were meant to detect.
- C11. The reserved vocabulary ("promotion" is a gated deploy between deploy tiers) and the
  single-account rule may conflict with a promotion ref inside a single tier, as recommendation rec-4874
  proposes.
- C12. A head created by updating the branch on GitHub carries only fast-tier evidence, so merge-time
  verdict integrity may rest entirely on post-merge detection.
- C13. `pr-conflict-signal` polls every open agent PR on every push to main. Its API calls may grow as
  pushes times open PRs against the per-repository GITHUB_TOKEN rate limit.
- C14. The verdict personas due to run on Actions (code reviewer, plan critic, decision scout, plus the
  existing CI-RCA agent) may compete with validation for the same concurrent-job pool.
- C15. The structural fix for the `docs/DECISIONS.md` hotspot (the per-file split) is gated on a
  deferred roadmap item. That timing may be too late for current conflict rates.
- C16. Agents may strand before they ever push, so no GitHub artifact records it. The pipeline may then
  have no measurement of stranding at all.

## 3. READ FIRST: disambiguation traps

- "canary" names at least five things here. IN SCOPE: Main Canary (`.github/workflows/main-canary.yml`,
  a scheduled full-tier run against main). CONTEXT ONLY: the "L8 drift canary" of Decision 73 clause 2,
  which is the historical name of that same scheduled full tier (described as hourly; the cron today is
  three-hourly). OUT OF SCOPE: the production telemetry smoke canary (Decision 236); the security
  heartbeat canary (`terraform/bootstrap/platform_security_heartbeat_canary.tf`); the session hook
  canary (`.claude/hooks/hook_canary.sh`, AGENTS.md "Hook canary"). The misread to avoid is treating any
  of the out-of-scope canaries as a CI promotion signal.
- "TAP" names two things. IN SCOPE: Google's Test Automation Platform model (fast presubmit, batched
  postsubmit, culprit finding), which the repository says it follows (`docs/contracts/git-ops.yaml`
  `two_tier_presubmit_model.description`, "Google TAP style"). NOT THE SAME THING: "THE TAP RULE" in
  `git-ops.yaml` `trailer_acceptance_gate.source_admission`. That rule is about rec-closure verdict
  sources (never re-execute a command whose outcome an authoritative record already carries). It is not
  a CI tiering rule. Cite it only where Q3 or Q5 touches rec closure.
- "queue" names four things: a pre-merge merge queue (GitHub's native one, not adopted); the post-merge
  per-commit full-tier runs, which queue on runners (the requester's chosen interim model, Q3); GitHub
  runner job queueing under the concurrent-job cap; and `ops_priority_queue`, the warehouse work queue
  (out of scope). Name which one you mean every time.
- "environment" names two things: a GitHub Environment (a security primitive that gates job execution
  and scopes the OIDC subject, e.g. `tf-gated-apply`, `tf-trusted-plan`, `pypi`) and a deploy tier
  (sandbox / SIT / PROD, `docs/contracts/environment-taxonomy.yaml` `axis_a`). A new deploy tier may or
  may not need a new GitHub Environment. Keep the two apart.
- "promotion" is reserved vocabulary: "a gated deploy between deploy tiers, never anything else"
  (`environment-taxonomy.yaml` `general_conformance`). If you recommend a "last-green ref" inside one
  tier, either call it something else or recommend the vocabulary change explicitly (C11).
- "sandbox" names the deploy tier (which today hosts production telemetry, per a prior investigation
  summarised in the GROUNDING MAP), Anthropic's sandbox-runtime for local persona hosts (Decision 234,
  T4.34), and the network-less probe sandbox of rec closure. Only the deploy tier is in scope.
- "concurrency" names three things: executor concurrency (the frozen executor's rec-level parallelism,
  the trigger in Decisions 217 and 245); interactive session parallelism (many operator-launched Claude
  Code sessions merging today); and GitHub Actions `concurrency:` groups. C10 turns on keeping the first
  two apart.
- "Scion" names two things. IN SCOPE: Anthropic's internal source-control system, described in
  "Redesigning source control at Anthropic" (https://www.anthropic.com/engineering/redesigning-source-control,
  2026-10-13). NOT IN SCOPE: an unrelated Google experimental agent-orchestration project of the same
  name. Recommendation rec-4874 cites the Anthropic post under a `/research/` path; the URL above, with
  `/engineering/`, is the one Decision 217 cites.
- "full tier" runs in two places: locally before handoff (S2), and in CI after merge (S3, as
  `main-validate`) plus on the canary schedule. A finding about one is not a finding about the other.

## 4. SCOPE

Surfaces (state as of the audited commit; verify every one):

| ID | Surface | State |
|---|---|---|
| S1 | Presubmit and merge path: `ci.yml` `pr-validate` (fast tier, `--pre`), `terraform-validate`, `signal-green`; `.github/workflows/pr-conflict-signal.yml`; the merge gate (`.claude/hooks/merge_gate.sh`, `.claude/hooks/merge_gate.py`, `scripts/session/merge_overlap.py`, Decision 245); the main ruleset (`terraform/github/repo.tf`) | built |
| S2 | Agent handoff path: `git-ops.yaml` `handoff_sequence`, `rebase_phase_distinction`, `handoff_evidence_gate`; `scripts/session/rebase_resolve.py`; `docs/contracts/mechanical-conflicts.yaml`; `scripts/checks/validation_result.py` (`--verify-head`); `.claude/hooks/handoff_evidence_gate.py`; the implement skill's Commit Flow (`.claude/skills/implement/SKILL.md`, "Pre-Push Rebase" and "IMPLEMENTATION Commit Flow") | built |
| S3 | Post-merge verification: `ci.yml` `main-validate` and the trailer jobs (`trailer-census`, `trailer-acceptance-evaluate`, `trailer-junit-verdict`, `trailer-closure`); `.github/workflows/main-canary.yml` | built |
| S4 | CI-RCA attribution and red-main recovery: `.github/workflows/ci-rca.yml`; `scripts/ci_rca/` (notably `vacuous_pass.py`, `red_overlap.py`, `fingerprint.py`); `docs/contracts/ci-rca-lifecycle.yaml`; `docs/contracts/red-main-recovery.yaml` (Decision 226) | built |
| S5 | Deploy and promotion: `.github/workflows/deploy-ducklake-lambdas.yml`, `deploy-prod-lambdas.yml`, `terraform-apply-sandbox.yml`; `docs/contracts/deploy-paths.yaml`; `environment-taxonomy.yaml`; the promotion train (Decision 24, Decision 73 clause 5) | deploy-on-push built; promotion train designed-unbuilt |
| S6 | Designed-unbuilt scale commitments: rec-4874 (promotion model), rec-4790 (handoff remedy options), rec-4873 (merge-robust ratchets), Decision 217 clauses 1-8, roadmap T4.7, T4.8, T4.9a, T4.21, T4.24 (per-file Decision split, Decision 217 clause 5) | designed-unbuilt |
| S7 | Shared GitHub Actions capacity: every workflow under `.github/workflows/` that runs on push, pull_request, workflow_run or schedule; the verdict personas (roadmap T4.28 code reviewer, T4.29 plan critic, T4.30 decision scout, on T4.32's foundation); the Fargate compute plane (T4.15; `docs/contracts/ports.yaml` `persona_compute_host`, `verdict_plane`) | workflows built; personas and Fargate plane designed-unbuilt |

Vocabulary you use in your output:

- Merge rate: first-parent commits on origin/main per day (all merges are squash merges).
- Peak: the busiest calendar day, and the busiest 60-minute window, in your E1 sample.
- Horizons: H0 = the current peak day's merge rate as you measure it in E1. H10 = 10x H0. H100 = 100x
  H0. The requester pinned 10x and 100x as the planning horizons.
- Per-commit post-merge model: each push to main gets its own full-tier run, and runs queue on the
  available runners. The requester's chosen interim model (REQUESTER DIRECTIONS).
- Batched post-merge model: one full-tier run covers several merges, and a red needs culprit finding.
- Time-to-push: wall clock from an agent's commit being ready to an accepted push. Time-to-merge: from
  PR creation to merge.
- Stranding: an agent session that stops making progress and has no pending wake.

Out of scope (one line each): the content of individual checks inside `scripts.validate`, beyond tier
membership and duration; Terraform guard classification and IAM policy content (beyond C9's trigger
question); telemetry tables and readers; the frozen executor's code (`scripts/executor/`); adopter
products; recommendation-queue hygiene; GitHub Environment protection settings, beyond whether a deploy
gate needs a new one.

TRUST NOTHING: obtain every file, line and size by reading the file. Trust no number quoted in this
prompt: re-derive it from the repository or the named live source, and record any anchor that does not
resolve in `meta.stale_anchors` (format `"<prompt anchor> -> <what you found>"`). `docs/DECISIONS.md`
is newest-first, so its line numbers drift: locate each Decision by its `## Decision NNN:` header,
never by line number.

## 5. SETUP

Run, in order:

1. `git fetch origin main` then `git rev-parse --short origin/main`. This is BASE; it is the audited
   tree. Read every file from that tree (`git show origin/main:<path>` where your checkout differs).
2. Cache generation for dedup: `bin/venv-python -m scripts.session.preflight --roadmap-detail full`. It
   writes `logs/.preflight-report.json` (roadmap state, CI-RCA counts, branch freshness).
3. Recommendation reads (there is no local recommendations cache): `bin/venv-python -m scripts.recs_related
   --terms "<space-separated terms>" --limit 20 --table`, `bin/venv-python -m scripts.recs_related <repo
   paths> --table`, and, where the tool is available, the `mcp__ducklake-reads__recs_by_ids` and
   `mcp__ducklake-reads__ci_rca_since` named verbs. Page with `--exclude <next_exclude>` when `complete=false`.
4. GitHub reads: use the `mcp__github__*` tools (`actions_list`, `actions_get`, `list_commits`,
   `list_pull_requests`, `pull_request_read`) for the EMPIRICAL PASS. Do not use any write tool except
   `create_pull_request` in COMMIT / PR MECHANICS.
5. External sources: if WebFetch is available, re-read the Scion post (URL in section 3) and GitHub's
   Actions limits page (https://docs.github.com/en/actions/reference/limits).

Degraded paths. Never abort; set the flag, apply the stated effect, and proceed:

- IF step 2 or step 3 fails (credentials or egress down): set `meta.degraded_dedup=true`; set every
  finding's `confidence` to HYPOTHESIS and its `roadmap_crossref.dedup_hit_count` to null; still grep
  `docs/ROADMAP-PLATFORM.yaml` and `docs/DECISIONS.md` locally and record those searches in
  `roadmap_crossref.dedup_search_terms`.
- IF the GitHub tools are unavailable or rate-limited: set `meta.degraded_github_reads=true`; skip the
  affected EMPIRICAL PASS items; tag every rate-dependent claim `evidence_kind: static`,
  `confidence: HYPOTHESIS`; set the affected `capacity_model` fields to null and explain in its `note`.
- IF WebFetch is unavailable: set `meta.external_unverified=true`; rely on the compose-time external
  facts in GROUNDING MAP section G9, which are labelled as such; cap Q6 and Q7 `confidence` at
  HYPOTHESIS for any claim that rests only on G9.
- IF an anchor does not resolve: record it in `meta.stale_anchors`, find the current location if you
  can, and continue.
- IF any repository check or validation fails for reasons unrelated to your two files: record it in
  `meta.contract_notes`. Never fix it (write boundary).

## 6. NORTH STAR

The bar you judge each surface against. These are not absolutes: argue each judgment from evidence, and
say when a principle should yield to cost or a deliberate constraint.

- NSP-1 No serial global step on the merge path. Throughput must not be bounded by one-at-a-time work
  whose duration is a full-tier run. Scion's post says a conventional merge queue "doesn't make much
  sense at agent scale", and Decision 73 chose forward-fix over pre-merge full-tier gating. Weigh this
  against cheap, bounded serial steps.
- NSP-2 Liveness. Every wait an agent enters is bounded, observable and wakes its waiter. When a bound
  is exceeded, the outcome is an escalation, never silence.
- NSP-3 Production consumes only trees with a passing authoritative verdict, or the exception is
  explicit, named and bounded.
- NSP-4 Verdict integrity. A verdict binds to an exact SHA, and the subject being judged cannot forge it.
  An agent-writable record is weaker than one produced server-side.
- NSP-5 Attribution economy. The cost of finding the culprit grows sublinearly with merge rate, and any
  loss of precision (one red attributed to several changes) is explicit and bounded.
- NSP-6 Conflict economy. Derived data is computed, not committed. Files every change must edit are
  restructured (per-entry files, generated indexes), not serialised.
- NSP-7 Measured triggers. A scaling step fires on a measured metric crossing a stated threshold, never
  on narrative (north star NS.6, "Improvement is proven, never asserted", `docs/contracts/north-star.yaml`).
- NSP-8 Portability. The CI and promotion design is expressible as a port with GitHub as one adapter, and
  works for an adopter at any scale (NS.1, NS.7 "scale is never blocked").
- NSP-9 Human gates guard intent, not mechanics (NS.9). Where a human approves a promotion, the approval
  carries intent and evidence; mechanical steps do not wait on a human.
- NSP-10 Proportionality. CI work per merge, in runner minutes, concurrency slots and tokens, is
  proportional to the risk the merge carries.

## 7. THE QUESTIONS

Each question has its own answer slot (`question_answers[]`). Pinned verdict enums are inline.

Q1. Handoff liveness. At H0, can an agent reliably get from a ready commit to an accepted push, and from
an open PR to a merge? Trace the full path: rebase, local full tier, `--verify-head`, push, PR CI, merge
gate, update-branch loop, merge. Compute expected time-to-push and time-to-merge as functions of merge
rate, using your E1 and E3 measurements. Then choose the remedy for the handoff path. Recommendation
rec-4790 names options (a) attestation carry-over, (b) push on fast-tier evidence with the full tier
gating elsewhere, (c) a server-side merge queue; you may name another. State which Decisions the choice
amends or reverses. Verdict enum: `viable | degraded | non_viable`. The remedy goes in the
`handoff_remedy` block.

Q2. Conflict sources. What structurally produces textual and semantic conflicts today? Consider at
least: hotspot files (E2); committed derived numbers; rosters outside the mechanical classes; monolithic
append-heavy files; the merge gate's same-file predicate versus different-file semantic conflicts. For
each mitigation you recommend, state whether it can land now or is gated, and on what. Verdict enum
(for the current design's conflict handling): `sufficient | partial | insufficient`.

Q3. The per-commit post-merge limit. Under the requester's direction (per-commit post-merge model, with
per-PR attribution kept), at what merge rate does that model stop being sound? Consider runner
saturation under the 20-job cap shared with S7, queue latency growing past the red-window SLO
(Decision 226, `red-main-recovery.yaml` `slo_hours`), and attribution ambiguity once queued runs
overlap. What is the minimum change to CI-RCA evidence and rec closure (C7) when it does stop being
sound? Fill `capacity_model`. Verdict enum: `sufficient_until_trigger | approaching_limit | exceeded`.

Q4. Workload placement. For each workload in the pinned list (OUTPUT, `workload_placement`), where
should it run, given the shared Free-plan concurrent-job cap, Decision 205 clause 1 (an LLM persona runs
on GitHub-hosted runners only on a GitHub repository event, verdict-shaped) and clause 6 (the Fargate
compute plane covers planning and implementation only), and the `verdict_plane` port (a non-Actions host
may post a check run through a verdict App)? Verdict enum (overall): `placement_sound |
placement_needs_change | placement_blocking`. Per-workload verdicts go in `workload_placement`.

Q5. Deploy gating and the canary. For each deploy channel in the pinned list (OUTPUT,
`deploy_channel_gating`): should production consume only a commit with a green full tier now, through
a last-green ref inside the single tier; only after a separate pre-production environment exists; or stay
on push? What should Main Canary be for? Candidates include a pure same-SHA re-run, a time-dependent
drift detector, the batched-postsubmit runner of the future model, or retirement. Is a separate
environment needed at H0, H10 or H100? Verdict enum (overall): `deploys_sound | deploys_partially_gated
| deploys_ungated`.

Q6. Industry rating at scale. Rate the whole pipeline against this EXTERNAL CHECKLIST, property by
property (`external_checklist`, rating `met | partial | missed`; `partial` requires a named,
property-matched compensating control in `evidence`). For each property, also say whether the current
design holds it at H10 and at H100.

- EC1 Presubmit/postsubmit split with a bounded fast presubmit (Google TAP).
- EC2 Batched postsubmit with automated culprit finding (TAP-style culprit finding or bisection).
- EC3 Pre-merge testing of the composed tree, batched or speculative (Uber SubmitQueue, GitHub merge queue, Bors/Mergify).
- EC4 Server-side rebase and merge, so clients never race the trunk (Scion server-side merges; GitHub update-branch and merge queue).
- EC5 The verdict is bound to the exact SHA and produced and attested server-side.
- EC6 Deploy from last-known-green, with bake time or progressive promotion.
- EC7 An automated bisect-and-revert (or forward-fix) pipeline with a bounded red window (Scion's bisect and revert pipelines; Chromium-style sheriffing).
- EC8 Conflict avoidance by construction: derived data computed, not committed; per-entry records; semantic locks.
- EC9 Agent-owned conflict repair, with escalation.
- EC10 Flake quarantine and deterministic test selection.
- EC11 Elastic CI capacity, with prioritisation under saturation.
- EC12 Pipeline observability: merge rate, queue time, red window, conflict rate, time-to-merge and stranding are measured and alarmed.

Verdict enum: `holds_at_h100 | holds_at_h10 | holds_at_h0_only | failing_at_h0`.

Q7. Scion relevance and Decision 217's triggers. Which Scion mechanisms (G9) transfer to this
repository's scale and substrate, and which do not? Decision 217 already recorded Scion-derived
commitments behind triggers. Are those triggers keyed to a signal that tracks the concurrency the
pipeline actually experiences (C10)? Verdict enum: `triggers_correct | triggers_mis_keyed |
triggers_missing`.

Q8. Sequencing and timing. Put every finding, plus every planned item you judged relevant, on one
sequence: `now` (start immediately: current pain, or needed before the next doubling), `next` (must land
before a named trigger expected within H10), `later` (gated on a trigger expected only between H10 and
H100), or `not_recommended`. Each entry carries a measurable trigger metric, a threshold, the current
value, its measurement source, its dependencies and the Decisions it amends or reverses. Compare that
order against the roadmap's existing order: rec-4874's scope, Decision 217's coverage homes, and the
critical path in `docs/PROJECT_CONTEXT.md` ("Roadmap end-state map"). Verdict enum: `aligned |
partially_aligned | misaligned`.

Q9. Questions the requester did not think to ask. Answer each seed below, then ADD at least two of your
own.

- Should the local pre-handoff full tier exist at all once a server-side post-merge verdict gates
  deploys?
- What does a single red cost today: merges landing on a red base during the red window, at H0 and H10?
- Do the verdict personas' Actions triggers multiply per-PR job count, and by how much?
- Is the 20-job Free-plan cap the binding constraint, or is the GITHUB_TOKEN rate limit or workflow
  trigger rate hit first?
- What does an adopter on a private repository (minutes-billed) inherit from this design?

## 8. RUBRIC

Rate every surface S1-S7 on every dimension. Enum: `strong | adequate | weak | absent | n/a`. `n/a` is
correct and costless where a dimension does not structurally apply. Never manufacture a rating or a
finding to fill a cell.

| Dim | Name | Principle | Served questions |
|---|---|---|---|
| VD1 | Throughput scalability | NSP-1, NSP-10 | Q3, Q6 |
| VD2 | Liveness | NSP-2 | Q1, Q9 |
| VD3 | Verdict integrity | NSP-4 | Q1, Q5 |
| VD4 | Attribution fidelity | NSP-5 | Q3 |
| VD5 | Conflict economy | NSP-6 | Q2 |
| VD6 | Deploy safety | NSP-3, NSP-9 | Q5 |
| VD7 | Portability | NSP-8 | Q4, Q6 |
| VD8 | Pipeline observability and trigger readiness | NSP-7 | Q7, Q8 |
| VD9 | Capacity and cost proportionality | NSP-10 | Q3, Q4 |

## 9. DEEP-DIVES

- DD-A (feeds Q1, Q9). The handoff timing trace. Model one agent's path at H0 and H10 as a race against
  main. P(main changes during a local full tier of duration T) depends on merge rate. Add the merge-gate
  OVERLAP probability (from E2 and E5) and the update-branch CI wait (pr-validate duration). Count every
  point where the agent ends its turn waiting on a wake, and name the wake source for each. Flag any
  wait with no wake source, or one whose source does not reach a subagent (AGENTS.md "a subagent never
  receives the completion").
- DD-B (feeds Q3, Q4). The capacity model. Count jobs per push to main and per PR update, at the BASE
  workflow set, by reading each workflow's triggers and job `if:` conditions; record path-filtered
  workflows separately. Multiply by measured job durations (E3) to get concurrent slot demand at H0,
  H10 and H100. Add the planned persona jobs: assume one job per persona per qualifying PR event, and
  state the assumption. Compare with the 20-job cap, and find the merge rate at which demand saturates it.
  Separately, estimate GITHUB_TOKEN calls per hour for `pr-conflict-signal` (calls per open PR per push,
  times pushes per hour, times open agent PRs) against 1,000 per hour per repository.
- DD-C (feeds Q3, Q5). The coupling map of per-push `main-validate`. List every consumer that depends on
  one full-tier run per push-to-main commit: CI-RCA's PR lookup and `HEAD^` diff; `trailer-junit-verdict`'s
  same-run junit; Decision 226's `workflow_tier` classification; anything else you find. For each,
  state what breaks under a batched post-merge model and the minimum change that preserves it.
- DD-D (feeds Q5). Deploy-versus-verdict ordering. For each deploy channel, trace the trigger, the
  concurrency group, the identity it assumes (trust is on `ref:refs/heads/main` per the prior
  investigation in G8), and whether a `workflow_run`-on-green-CI trigger would keep that identity.
  Join E6's sample to determine how often a deploy preceded a red verdict on the same SHA.

## 10. GROUNDING MAP

This map spends your cognition on judgment, not grep. Verify every entry before relying on it, and
record non-resolving anchors in `meta.stale_anchors`. Facts are stated neutrally. Numbers are
compose-time observations; re-derive them.

G1. Presubmit and merge (S1)

- `.github/workflows/ci.yml`: `pr-validate` (around line 16) runs `python -m scripts.validate --pre`
  with a `concurrency` group that cancels in progress per ref. The fast-tier budget constant is
  `_FAST_TIER_BUDGET_SECONDS = selection_budget.FLOOR_TOTAL_SECONDS` in `scripts/validate.py` (around
  line 81).
- `terraform/github/repo.tf`: required checks `pr-validate` and `terraform-validate` (around lines
  103-110); `strict_required_status_checks_policy = false` (around line 116); a bypass actor with
  `bypass_mode = "always"` (around lines 79-81). Decision 83 records the non-wedging intent.
- `signal-green` (`ci.yml`, around line 539) posts a CI-green comment on PRs whose head ref starts with
  `agent/`, `claude/` or `ccr-`.
- `.github/workflows/pr-conflict-signal.yml` runs on every push to main and delegates to
  `scripts/ci/pr_conflict_signal.sh`, which polls the mergeability of every open PR matching those
  prefixes and posts one wake comment per head SHA on a conflict (`git-ops.yaml`
  `push_pr_ci_merge_flow.wake_signals.merge_conflict`).
- Merge gate (Decision 245, decided 2026-11-03). It refuses `merge_pull_request` and
  `enable_pr_auto_merge` when origin/main has changed, since the merge-base, any file the PR also
  changes (`scripts/session/merge_overlap.py`). Remedy: update the branch on GitHub, wait for the
  CI-green wake, merge. Clause 2 calls it "a heuristic, not verification". Clause 5 defers strict
  up-to-date checks and a GitHub merge queue, and names rec-4874's promotion model as the end state. In
  the replay its Problem section cites, 22 of 38 merges were behind main and 2 shared a changed file.
  The `git-ops.yaml` text on `push_pr_ci_merge_flow` (around line 265) states no attempt cap for the
  update-branch loop.

G2. Agent handoff (S2)

- `git-ops.yaml` `handoff_sequence` (around lines 189-198), per push: commit, then fetch and rebase onto
  origin/main, then the full tier on the clean rebased tree, then `--verify-head`, then push. "Cost basis
  is now per push (every rebase or fix commit needs a fresh full-tier run)".
- `scripts/checks/validation_result.py` `attests_head` (around line 283) is true only when the evidence's
  head tree equals the checked HEAD's tree, among other rules (`ATTEST_RULES`, around line 268).
- `handoff_evidence_gate` (`git-ops.yaml` around lines 495-513; Decision 203) is a fail-closed PreToolUse
  hook with no agent override. `exempt_path_globs` include `audits/*`; `extra_destination_prefixes`
  include `audit/`.
- AGENTS.md (around lines 145-147): the full tier "runs well past the 10-min foreground cap on CC-web:
  start it backgrounded with a 2 h limit from the top-level session (a subagent never receives the
  completion)".
- Recommendation rec-4790 (open, High, created 2026-10-30): the full tier takes about 20-25 minutes of
  wall clock; main merged roughly every 10-15 minutes at that time. One PR hit five rebases, four
  discarded full-tier runs and a final `tree_mismatch` push refusal, and the operator pushed it by hand.
  It names options (a)-(c) and says the choice changes Decisions 203 and 163 point 3.
- `docs/contracts/mechanical-conflicts.yaml`: `pure_insertion` is the only hunk shape any class resolves.
  Classes: `derived` (`docs/decisions-index.json`, `config/lambda/ducklake/field_semantics.yaml`,
  `.claude/hooks/hooks.json`); `roster` (`config/ci_rca_taxonomy.yaml`, `scripts/checks/*/_manifest.py`,
  `docs/contracts/*.yaml` under `amendment_log` only); `decision_numbering` (`docs/DECISIONS.md`).
  `excluded` lists `.secrets.baseline`, `.github/CODEOWNERS`, the Terraform lock files,
  `config/agent/fast_tier/module_costs.json`, `config/sloc_budgets.yaml`, `config/mypy_baseline.yaml`,
  `config/structural_size_budgets.yaml` and `config/composite_action_body_baseline.yaml`. Its
  `validation` key keeps the full tier and `--verify-head` after a resolved rebase.
  `docs/ROADMAP-PLATFORM.yaml` appears in neither list.
- Recommendation rec-4873 (open, High): `validate_sloc_limits` and `validate_prose_limits` are
  exact-equality ratchets on committed numbers; two concurrent PRs either conflict on the budget line or
  fold into a wrong combined value (the #1805/#1799 incident; closed rec-4869).
- Compose-time observation (re-derive in E2): over the first-parent merges from 2026-10-20 to
  2026-11-03 (242 commits), the most-touched paths were `docs/DECISIONS.md` (27),
  `docs/ROADMAP-PLATFORM.yaml` (25), `config/ci_rca_taxonomy.yaml` (23), `docs/decisions-index.json`
  (13), `docs/contracts/account-credential-inventory.yaml` (13), `docs/contracts/file-router.yaml` (12),
  `config/composite_action_body_baseline.yaml` (11), `.secrets.baseline` (11) and
  `docs/contracts/ports.yaml` (10).
- Compose-time observation: two PRs were open at composition time (#1846, opened 2026-11-03; #1806,
  opened 2026-11-02 with no update after 2026-11-02T23:18Z), while main took 33-37 merges a day.

G3. Post-merge verification (S3)

- `ci.yml` `main-validate` (around line 95) runs on push, executes `python -m scripts.validate` (full
  tier), and declares no `concurrency` group. Six jobs in `ci.yml` have conditions that admit push
  events (`main-validate`, `trailer-census`, `trailer-acceptance-evaluate`, `trailer-junit-verdict`,
  `trailer-closure`, `terraform-validate`). Re-derive this count.
- `trailer-junit-verdict` (around line 268) declares `needs: [trailer-census, main-validate]` and reads
  the SAME push run's pytest junit. `git-ops.yaml` `trailer_acceptance_gate.source_admission`: "same-run
  binding is enforced by the artifact download step itself declaring no `run-id` input".
- `.github/workflows/main-canary.yml`: `cron: '0 */3 * * *'`; runs `python -m scripts.validate` against
  main; uploads the same junit and validation-result artifacts as `main-validate`; no same-SHA skip.
  Compose-time observation from runs 950-965: start-to-start gaps ranged from 3h02m to 10h50m; runs
  955-959 all ran on head `613360920e`; two of the sixteen concluded failure (runs 950 and 964).
- Decision 73 clause 2: "Full tier runs on push to `main` and on hourly scheduled cron (L8 drift canary)".
  Decision 205 clause 5 (amending 73) and recommendation rec-4114 (open) plan a fast-tier-only path for
  `docs/plans/**`-only merges; `ci.yml` at compose time has no such path filter.
- Other workflows that run on every push to main: `codeql.yml`, `pr-conflict-signal.yml`,
  `rec-autoclose.yml`. Path-filtered on push: `deploy-ducklake-lambdas.yml`, `deploy-prod-lambdas.yml`,
  `terraform-apply-sandbox.yml`. About a dozen more run on `schedule`; re-derive the set.

G4. CI-RCA attribution and recovery (S4)

- `ci-rca.yml` triggers on `workflow_run` of `CI`, `Main Canary`, `terraform-apply-sandbox`,
  `rec-autoclose`, `deploy-ducklake-lambdas` and `deploy-prod-lambdas` (around lines 22-30). It is
  gated to failures on the default branch (around line 59). Its concurrency group keys on head SHA plus
  workflow name, with no cancellation (around lines 47-49).
- It resolves one PR per anchor commit: `PR_NUMBER=$(gh api "repos/.../commits/${ANCHOR_SHA}/pulls"
  --jq '.[0].number' ...)` (around line 260).
- `scripts/ci_rca/vacuous_pass.py` computes the merged diff as `git diff --name-only HEAD^ HEAD` (around
  lines 69-70 and 89-92).
- `red-main-recovery.yaml`: `slo_hours: 4`; `halt.rules` 0-6 (`red_overlap.py`); `slo_clock`: "A build
  that reaches handoff during a red still needs a green full tier (Decision 163), so it waits for the
  repair".
- `ci-rca-lifecycle.yaml` `trigger_scope.pr_branch_disposition`: PR-branch fast-tier failures file no
  recommendation by design.
- Recommendations rec-4441 (class tally), rec-4428 (deferral-blind escape attribution), rec-4321
  (conflict-stop record) and rec-4322 (red-window baseline) are open; the last two are Decision 217's
  measurement recs.

G5. Deploy and promotion (S5)

- `deploy-ducklake-lambdas.yml` (around lines 108-126) and `deploy-prod-lambdas.yml` (around lines
  57-79) trigger on push to main with path filters, plus `workflow_dispatch`. Each has a non-cancelling
  concurrency group.
- `terraform-apply-sandbox.yml` (around lines 52-87) applies the PR-time saved plan on push (Decision 77),
  in a non-cancelling concurrency group on push.
- `environment-taxonomy.yaml`: `axis_a` rows sandbox / SIT / PROD (around lines 141-172);
  `single_account_rule` (around line 396): SIT and PROD are future-state, and standing one up is
  "separately decided"; `general_conformance` reserves "promotion".
- Decision 73 clause 5: "Sandbox tolerates red `main`; SIT and PROD do not", with green-streak promotion
  of 24h and 7d. Decision 24: GitHub Environments with tag-marked single-branch promotion, never built.

G6. Designed-unbuilt (S6)

- rec-4874 (open, High, XL, created 2026-11-03). TAP-style promotion model: full tier only on a batched
  main canary; automated culprit bisect; a green canary advances a ref that deploys consume; a red blocks
  promotion, not work. It says it amends Decisions 73, 226, 163 and 203, likely 77 and 126, and says to
  start with a REPORT-ONLY design plan and a Decision draft.
- Decision 217 (2026-10-14) cites the Scion post. Clause 1 (overlap block for red main; pulled forward
  by Decision 226), clause 2 (textual conflicts; above concurrency 1 a bounded rebase step owns every
  textual conflict), clause 3 (a non-bypass executor GitHub App), clauses 5-7 (per-file Decision split,
  generated index; home T4.24, `deferred_post_mvp`). Reversal conditions `concurrency-raised` and
  `red-window-growth` are `kind: manual`.
- Roadmap: T4.8 (semantic locks and leases; `deferred_post_mvp`, inert until concurrency rises above 1);
  T4.7 (plan staleness; `deferred_post_mvp`); T4.9a (verdict callback and merge authority; `not_started`);
  T4.21 (base-ref required check, non-bypass App identity; `not_started`).
- rec-4451 (merge race after a green pr-validate), rec-4085 (server-side attestation), rec-4080 (merge on
  red), rec-4087 (inherited-red release), rec-2827 (protected-path no-self-merge), rec-4624 (ruleset
  update frozen): all open.

G7. Shared capacity (S7)

- Roadmap T4.28 (code_reviewer), T4.29 (plan_critic), T4.30 (decision_scout): "an Actions verdict job",
  all `not_started`, on T4.32 (persona foundation, `not_started`). T4.15 (implement_agent on Fargate,
  `not_started`).
- Decision 205 clause 1 (event-origin, verdict-shaped LLM personas on GitHub-hosted runners) and clause 6
  (the compute plane covers planning and implementation only). Decision 234 clause 3 (subscription rule)
  and clause 5 (local adapters post-MVP).
- `docs/contracts/ports.yaml` `verdict_plane` (around line 243): adapters `github_actions` (implemented)
  and `verdict_app` (pending, T4.2:c9).
- Requester statement at composition: the verdict personas and the existing CI-RCA agent will run on
  Actions "fairly imminently", will compete with validation for minutes and concurrency, and "should
  also be fairly straightforward to put onto fargate containers alongside planning and implementation
  agents".

G8. Prior investigation (2026-11-03, a session report; not in the repository; re-verify each number)

- Main pushes in about 20 hours: 30. Full-tier `CI` push runs, last 30: median 29 min, p90 37 min, max
  38 min. Red rate over the last 300 main runs (17 Oct to 3 Nov): 14 (4.7%), with 5 of the last 30.
- One DuckLake deploy preceded a red verdict on the same commit: PR #1680, deploy run 40548129029 green at
  17:19Z on 27 Oct, `main-validate` run 40548128960 red at 17:44Z. No Terraform apply on push matched a
  red full tier in 21 matched runs.
- The deploy role and the routine apply role trust `ref:refs/heads/main` (`terraform/personal/oidc_pipeline_roles.tf`,
  `terraform/bootstrap/github_ci_apply.tf`). A `workflow_run` job runs at the default branch, so its
  OIDC subject would stay `ref:refs/heads/main`.
- The report recommended moving the two Lambda deploy workflows to `workflow_run` on a successful `CI`
  push run (last-green-wins), leaving Terraform apply on push, and amending Decision 73 clause 5. It
  asked whether the sandbox tier is now production.

G9. External facts (compose-time; re-verify per SETUP step 5)

- Scion post (2026-10-13; updated 2026-10-15), read through a summarising fetch, so quotes are
  near-verbatim. Merges grew from about 2,500 a day (May 2026) to over 30,000 a day (September); "A proper
  merge queue, for example, doesn't make much sense at agent scale"; "We haven't been able to effectively
  replicate the industry standard of a global repository lock on the merge queue, so instead, we've
  invested in post-merge and pre-deploy automated testing"; "concurrent changes can conflict, but Claude
  can typically fix these conflicts faster than humans can even identify them"; it mentions "automated
  bisect and revert pipelines" without detail. Commits, merges and rebases are server-side; the post says
  the concepts "only make sense for truly enormous monorepos" and the system is not a git replacement.
  The post does not describe batching, test selection or deploy promotion.
- GitHub Actions limits page: concurrent jobs on standard GitHub-hosted runners are Free 20, Pro 40,
  Team 60, Enterprise 500; the GITHUB_TOKEN rate limit is 1,000 requests per hour per repository; the
  workflow trigger rate limit is 500 runs per 10 seconds. The requester confirmed the organisation is on
  the Free plan, and the repository is public.

## 11. EMPIRICAL PASS

Hard bounds. Do NOT exceed any cap. Tag each finding `evidence_kind: observed` (from a sample below) or
`static` (from reading files). An observed finding outranks a static one at equal severity.

- E1 (Q1, Q3, Q8). Merge rate: `git log origin/main --first-parent --since=<BASE date minus 30 days>
  --format=%cI`. Compute per-day counts, the peak day (H0), the busiest 60-minute window in the last 7
  days, and the median inter-merge interval on the peak day.
- E2 (Q2). Hotspots: `git log origin/main --first-parent --since=<BASE date minus 14 days> --name-only
  --format=`. List paths touched by at least 5 merges. Classify each against `mechanical-conflicts.yaml`
  as derived, roster, decision_numbering, excluded or unlisted.
- E3 (Q1, Q3, Q4). Durations: at most 50 of the most recent completed `CI` runs on push to main
  (`main-validate` job duration) and at most 30 of the most recent `pr-validate` runs; at most 20 Main
  Canary runs (duration, conclusion, head SHA repeats, start-to-start gap).
- E4 (Q3, Q5). Reds: CI-RCA recommendations created in the 30 days before BASE (the `ci_rca_since` verb,
  or `scripts.recs_related --terms "ci_rca"` if the verb is unavailable). For at most 10 of them, record
  whether `main-validate` on the anchor commit and Main Canary both went red, and the open-to-close
  window.
- E5 (Q1, Q2). Merge timing: at most 15 of the most recently merged PRs. Record commits behind main at
  merge, time from the last successful `pr-validate` to merge, and the number of head updates.
- E6 (Q5). Deploy versus verdict: at most 20 of the most recent push runs of each Lambda deploy workflow,
  joined by head SHA to that commit's `CI` push run (conclusion and completion time).
- E7 (Q1, Q9). Stranding: all open PRs (age, last update, mergeable state). If none is stranded, record
  that and lean on rec-4790's documented case; never invent a rate.

Counterfactual tests. Apply each as an operation, and record the answer in the finding's
`compensating_controls_considered` or in the rejected candidate's `control_property_match`:

- Would the merge gate refuse a merge whose combined tree fails only through a semantic conflict across
  different files? If not, it is not a control for that defect.
- Would Main Canary go red on a SHA where `main-validate` went green, absent non-determinism,
  time-dependent inputs or live external reads? If not, name what it detects that `main-validate` cannot.
- Would any deploy workflow refuse to deploy a SHA whose `main-validate` later fails? If not, a green
  later verdict is not a control.
- Would `--verify-head` evidence still attest after a rebase whose base delta touches none of the branch's
  files? (Read `attests_head`.) If not, every such rebase discards a full-tier run.
- Would CI-RCA attribute a red correctly if a single full-tier run covered three merges? Trace the PR
  lookup and the `HEAD^` diff.
- Would the conflict-wake comment reach a PR whose session has ended its turn awaiting a different
  signal? (Read the wake contract.)

## 12. METHOD

Phases, in order:

- P1 Read: SETUP, then every S1-S7 anchor in the GROUNDING MAP.
- P2 Trace: the end-to-end path of one change (commit to production).
- P3 Deep-dives DD-A to DD-D.
- P4 Empirical pass E1-E7.
- P5 Adjudicate C1-C16 and anything new you found.
- P6 Rate the rubric.
- P7 Dedup every finding (DEDUP DISCIPLINE).
- P8 Fill the decision blocks and `capacity_model`.
- P9 Answer Q1-Q9.
- P10 Compute maturity, then summary, LAST.

## 13. DEDUP DISCIPLINE

Before filing any finding, search these ownership surfaces:

- `docs/ROADMAP-PLATFORM.yaml` (`tier_items[]` and `candidate_decisions[]`; project with `bin/venv-python
  -c` and `yaml.safe_load`, never read whole).
- `docs/DECISIONS.md` and `docs/DECISIONS_ARCHIVE.md` (`rg` over `^## Decision` headers, then targeted
  reads).
- Open recommendations (`scripts.recs_related` with terms and files; `recs_by_ids`).
- Prior audits: `audits/gitops-agent-first-ddfb9cc.yaml`, `audits/ci-rca-system-a49db8f.yaml`,
  `audits/fast-tier-optimisation-3560909.md`, `audits/terraform-deploy-redesign-b67955b.yaml`,
  `audits/private-repo-self-hosted-ci-9a545d2.yaml`, `audits/planning-workflow-scaling-369a963a.yaml`.

Record `dedup_search_terms` and `dedup_hit_count` on every finding. A hit means you assess the owner's
sufficiency (`planned-insufficient` or `planned-unbuilt` in findings, or `rejected_candidates` when fully
covered), never a fresh discovery. A finding without a recorded negative search is `confidence:
HYPOTHESIS`. Known owners to start from: rec-4874, rec-4790, rec-4873, rec-4451, rec-4085, rec-4080,
rec-4087, rec-2827, rec-4114, rec-4266, rec-4321, rec-4322, rec-4428, rec-4441, rec-4755, rec-3221; T4.7,
T4.8, T4.9a, T4.21, T4.24, T4.28-T4.32, T4.15; Decisions 73, 163, 203, 205, 217, 226, 245.

Deliberate constraints. These were decided on purpose. Do not file one as a defect in passing. The
requester explicitly permits you to propose REVERSING or AMENDING any of them. Do so only as an explicit
finding with `change_type: reverse_decision` or `amend_decision` that names the Decision, the cost of
reversal, what it unblocks, and which of its reversal conditions (if any) has fired.

- Decision 67: STRATEGIC plans suspended; the executor frozen.
- Decision 73 clause 3: no automated revert. Decision 226 permits a manual revert PR outside Terraform
  and governed deploy paths.
- Decision 83: `strict_required_status_checks_policy = false` and the admin bypass (non-wedging).
- Decision 203: the handoff evidence gate, no agent override. Decision 245: the merge gate, no agent
  override.
- Decision 163 point 3: the local pre-handoff full tier on the committed, rebased tree.
- `environment-taxonomy.yaml` `single_account_rule` and the reserved vocabulary.
- Decision 77 and Decision 126: Terraform applies the PR-time saved plan on push; the deploy model.
- Decision 128: the 500-SLOC file limit. Decision 84: the warehouse is the source of truth. Decision
  101: the public-repository content boundary.
- Decision 205 clauses 1 and 6; Decision 234: persona placement and credentials.

## 14. OUTPUT

Write `audits/ci-merge-throughput-scaling-<sha>.yaml` with exactly this shape. Every enum is pinned
inline.

```
audit:
  meta: {audited_commit: <BASE short sha>, base_branch: main,
         model: <your self-reported model name, free text>,
         methodology_version: 1,
         scope_surfaces: [S1, S2, S3, S4, S5, S6, S7],
         degraded_dedup: false, degraded_github_reads: false, external_unverified: false,
         contract_notes: "", stale_anchors: []}
  requester_directions:
    per_commit_post_merge_until_trigger: true
    horizons: {h0_merges_per_day: <E1 value>, h10: <10 x h0>, h100: <100 x h0>}
    github_plan: free_public
    decisions_reversible: true
  question_answers:
    - {q: Q1, verdict: viable|degraded|non_viable, basis: [<finding ids>], prose: ""}
    - {q: Q2, verdict: sufficient|partial|insufficient, basis: [], prose: ""}
    - {q: Q3, verdict: sufficient_until_trigger|approaching_limit|exceeded, basis: [], prose: ""}
    - {q: Q4, verdict: placement_sound|placement_needs_change|placement_blocking, basis: [], prose: ""}
    - {q: Q5, verdict: deploys_sound|deploys_partially_gated|deploys_ungated, basis: [], prose: ""}
    - {q: Q6, verdict: holds_at_h100|holds_at_h10|holds_at_h0_only|failing_at_h0, basis: [], prose: "",
       external_checklist: [{property: EC1..EC12, rating: met|partial|missed,
                             holds_at_h10: true|false, holds_at_h100: true|false, evidence: ""}]}
    - {q: Q7, verdict: triggers_correct|triggers_mis_keyed|triggers_missing, basis: [], prose: ""}
    - {q: Q8, verdict: aligned|partially_aligned|misaligned, basis: [], prose: ""}
    - {q: Q9, answers: [{question: "", answer: "", basis: [<finding ids>]}]}
  per_surface_assessment:
    - {surface: S1..S7, maturity: frontier|strong|solid|nascent, strengths: "", top_gaps: [<finding ids>]}
  rubric_ratings:
    - {surface: S1..S7, dimension: VD1..VD9, rating: strong|adequate|weak|absent|n/a,
       evidence: "file:line|item-id|sample-id", note: ""}
  capacity_model:
    jobs_per_push_to_main: <int>          # unconditional jobs; path-filtered listed in note
    jobs_per_pr_update: <int>
    persona_jobs_per_pr_event: <int>      # assumption stated in note
    mean_full_tier_minutes: <float>
    concurrent_job_cap: <int>
    saturation_merges_per_day: <float>    # merge rate at which slot demand reaches the cap
    pr_conflict_signal_calls_per_hour_at: {h0: <int>, h10: <int>, h100: <int>}
    github_token_limit_per_hour: <int>
    binding_constraint: concurrent_jobs|github_token_rate|trigger_rate|red_window_slo|other
    note: ""
  handoff_remedy:
    verdict: attestation_carry_over|push_on_fast_tier_full_in_ci|server_side_merge_queue|other
    mechanism: ""
    what_changes: ""
    cost: ""
    decisions_amended_or_reversed: [<decision numbers>]
    rationale: ""
    confidence: CONFIRMED|HYPOTHESIS
  workload_placement:                     # every key below is required
    pr_fast_tier: {verdict: actions|fargate|actions_until_trigger, trigger_metric: "", threshold: "",
                   mechanism: "", cost: "", rationale: "", confidence: CONFIRMED|HYPOTHESIS}
    post_merge_full_tier: {...same fields...}
    main_canary: {...}
    trailer_jobs: {...}
    ci_rca_agent: {...}
    verdict_persona_code_reviewer: {...}
    verdict_persona_plan_critic: {...}
    verdict_persona_decision_scout: {...}
    pr_conflict_signal: {...}
    scheduled_deterministic_workflows: {...}
  deploy_channel_gating:                  # every key below is required
    deploy_ducklake_lambdas: {verdict: promote_last_green_now|separate_environment_first|keep_on_push,
                              mechanism: "", what_changes: "", cost: "",
                              decisions_amended_or_reversed: [], rationale: "",
                              confidence: CONFIRMED|HYPOTHESIS}
    deploy_prod_lambdas: {...same fields...}
    terraform_apply_sandbox_routine: {...}
  sequencing_plan:
    - {item: <finding id | rec-NNNN | T-id>, horizon: now|next|later|not_recommended,
       trigger_metric: "", threshold: "", current_value: "", measurement_source: "",
       depends_on: [], decisions_amended_or_reversed: [], note: ""}
  findings:
    - {id: CMTS-01, surface: S1..S7|shared, question: Q1..Q9, dimension: VD1..VD9,
       title: "", evidence: "file:line|item-id|sample-id", evidence_kind: static|observed,
       current_behavior: "", ideal_behavior: "", gap: "",
       compensating_controls_considered: "",
       change_type: add|rescope|enforce|unify|persist|clarify|retune_gate|reverse_decision|amend_decision,
       proposed_change: "", acceptance: "",
       severity: critical|high|medium|low, severity_rationale: "",
       confidence: CONFIRMED|HYPOTHESIS,
       roadmap_crossref: {classification: novel|planned-insufficient|planned-unbuilt,
                          item_ids: [], dedup_search_terms: [], dedup_hit_count: 0, note: ""},
       effort: XS|S|M|L|XL, depends_on: [<finding ids>],
       sequencing: {safe_to_queue_now: true|false, blocked_behind: [<finding or roadmap ids>], note: ""}}
  rejected_candidates:
    - {candidate: C1..C16|<new>, why_dismissed: "", compensating_control: "",
       control_property_match: "", decision_or_item_id: ""}
  summary: {total_findings: <int>, novel_count: <int>, planned_insufficient_count: <int>,
            planned_unbuilt_count: <int>, top_improvements: [<finding ids>],
            highest_leverage_change: <finding id>,
            maturity_S1: <level>, maturity_S2: <level>, maturity_S3: <level>, maturity_S4: <level>,
            maturity_S5: <level>, maturity_S6: <level>, maturity_S7: <level>}
```

Invariants (state them, and satisfy them):

- COUNTING INVARIANT: `findings[]` is the SOLE enumerated list. `total_findings = len(findings) =
  novel_count + planned_insufficient_count + planned_unbuilt_count`. Fully covered candidates live in
  `rejected_candidates`, NOT in findings. `rubric_ratings`, `question_answers`, `capacity_model`,
  `handoff_remedy`, `workload_placement`, `deploy_channel_gating` and `sequencing_plan` are systems of
  record that findings reference; they are never re-counted. `top_improvements` and
  `highest_leverage_change` MUST be finding ids. Every finding id appears in exactly one
  `sequencing_plan` entry.
- `control_property_match` is REQUIRED whenever a compensating control is the reason for dismissal. Name
  the property the control exercises, cite where it operates (mechanism or file:line), and state why the
  control would FAIL if the defect were real.
- CONFIRMED requires the behavior to be traced to file:line or to an observed sampled artifact; anything
  less is HYPOTHESIS.
- Meta flags have readers: `degraded_dedup` caps every finding's `confidence` at HYPOTHESIS; `degraded_github_reads`
  nulls `capacity_model` fields and makes rate-dependent findings HYPOTHESIS; `external_unverified` caps
  Q6 and Q7 confidence. State each applied effect in `meta.contract_notes`.

Write `audits/ci-merge-throughput-scaling-<sha>.md`, a companion report of at most 1500 words: the
executive layer a human reads first. Order: a 5-line synopsis; answers to Q1-Q8 in one or two sentences
each; the `sequencing_plan` as a now/next/later table; the decisions you propose to amend or reverse; open
questions for the requester. Use plain ASCII and no emojis.

## 15. SEVERITY + MATURITY

Assign severity AFTER judgment, by defect class. Never inherit it from this prompt's framing.

- critical: the pipeline can produce a wrong-but-trusted outcome (a production deploy, rec closure or
  red attribution proceeds on an unsound or absent verdict), or merging to main halts for all agents with
  no wake and no escalation.
- high: a weakness that materially reduces throughput, liveness or a verdict guarantee, whose
  compensating controls you judged insufficient. This includes a per-agent wait with no wake source.
- medium: redundancy, ambiguity, inconsistency, or a scale limit expected only past H10, with a clear fix.
- low: clarity or wording.

Compensating controls must PROPERTY-MATCH: they exercise the same property AND would fail if the defect
were real (apply the counterfactual to the control). A control that cannot catch the break neither lowers
severity nor justifies dismissal.

Maturity: compute LAST, per surface, top-down; the first match wins.

- frontier: 0 open critical or high findings on the surface, AND every Q6 `external_checklist` property
  relevant to the surface rated `met` or `partial` (never `missed`). The `external_checklist` field is
  the sole source for this test.
- strong: 0 critical AND at most 1 high.
- solid: at most 1 critical.
- nascent: otherwise.

The top rating stays reachable when you argued a property-matched compensating control. This prompt's
framing must not foreclose it.

## 16. COMMIT / PR MECHANICS

1. Derive the base ONCE: `git fetch origin main` then `git rev-parse --short origin/main`. That SHA is
   `<sha>` in both filenames, in the branch name and in `meta.audited_commit`.
2. `git switch -c audit/ci-merge-throughput-scaling-<sha> origin/main`, so the PR diff is only the two
   deliverables. This is a deliberate, documented exception to the session-branch rule: the audit needs a
   clean two-file diff off the audited base. The handoff evidence gate exempts `audits/*` and admits the
   `audit/` destination prefix (`git-ops.yaml` `handoff_evidence_gate`), so no full-tier run is needed.
   The CI-green comment wake does not fire for `audit/` branches; that does not matter, because you end
   your turn without merging.
3. Pre-push gate: both files must exist, and the YAML must parse (`bin/venv-python -c "import yaml;
   yaml.safe_load(open('audits/ci-merge-throughput-scaling-<sha>.yaml'))"`). Repository-wide validation
   is advisory outside CI. Record any unrelated `bin/venv-python -m scripts.validate --pre` failure in
   `meta.contract_notes`; never fix it.
4. `git add` the two files only, then `git -c user.name=Claude -c user.email=noreply@anthropic.com commit
   -m "audit(ci-merge-throughput-scaling): findings at <sha>"`, then `git push -u origin HEAD`.
5. `mcp__github__create_pull_request` with owner and repo from `git remote get-url origin`,
   `head=audit/ci-merge-throughput-scaling-<sha>`, `base=main`, not a draft, title `audit: CI merge
   throughput, attribution and promotion timing (S1-S7)`, and a body made of a 2-3 sentence lede plus the
   `summary` block in a yaml fence.
6. END THE TURN. Do not poll, do not subscribe, do not merge, do not approve, do not file
   recommendations.

## 17. GUARDRAILS

- Write boundary, a closed list: `audits/ci-merge-throughput-scaling-<sha>.yaml` and
  `audits/ci-merge-throughput-scaling-<sha>.md`. Nothing else in the tree, including no fix to any
  audited surface, no roadmap edit, no Decision edit, and no recommendation filed through the ops portal.
- Read-only toward GitHub and AWS, except the single `create_pull_request` call. No workflow dispatch, no
  re-run, no comment.
- Public repository: never write AWS account ids, ARNs, ExternalIds, credentials or internal hostnames
  into the deliverables. Cite file paths and run ids only.
- Fewer than about six surviving findings is a valid result: state it, and do not pad. Precision over
  volume. A finding that only restates a planned item's own text is a rejected candidate, not a finding.
- Severity, verdicts and maturity are yours. Nothing in this prompt pre-classifies them.
