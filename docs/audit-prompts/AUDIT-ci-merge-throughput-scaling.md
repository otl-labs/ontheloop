# AUDIT: CI merge throughput, attribution and promotion timing

## 1. TASK

You are auditing, read-only, how this repository moves a change from an agent's branch to production:
the presubmit and merge path, the agent handoff path, post-merge verification and Main Canary, CI-RCA
attribution and red-main recovery, the deploy channels, the designed-but-unbuilt scale commitments, and
the GitHub Actions capacity all of these share (surfaces S1-S7, SCOPE). Many concurrent Claude Code
sessions develop this repository, and merges to main have grown fast (GROUNDING MAP G2). The requester
reports agents stranding during handoff and frequent merge conflicts. They need three things. First,
what to fix now. Second, what must change before merge volume reaches 10x and 100x the current peak.
Third, timing: what can wait, and on which measured trigger. You answer nine questions (THE QUESTIONS):
Q1 handoff liveness; Q2 conflict sources; Q3 the limit of per-commit post-merge verification and
per-PR attribution; Q4 where each Actions workload runs; Q5 deploy gating and the canary's role; Q6
industry rating at 10x and 100x; Q7 Scion relevance and Decision 217's triggers; Q8 sequencing and
timing; Q9 questions the requester did not ask. You rate the surfaces on a pinned rubric and emit the
pinned output contract. Your deliverables are exactly two files:
`audits/ci-merge-throughput-scaling-<sha>.yaml` and `audits/ci-merge-throughput-scaling-<sha>.md`,
where `<sha>` is BASE, the short origin/main SHA you derive ONCE in SETUP step 1. The ONLY files you
create or modify in the repository tree are those two deliverables. Regenerating gitignored local
caches per SETUP is expected and does not breach that boundary; never commit them. You draft; the human
disposes. You open a pull request and end your turn. You never merge, never file recommendations, and
never edit any audited surface.

## 2. CANDIDATE OBSERVATIONS vs VERDICTS

This prompt hands you FACTS (GROUNDING MAP) and CANDIDATE hypotheses (C1-C19). It hands you no
verdicts. ASSUME NO CANDIDATE IS A REAL DEFECT UNTIL YOU TRACE IT. A run that merely confirms the
candidates below has failed. Your value is in adjudication, in what you find that is not listed, and in
the timing model.

Adjudicate every candidate, and every new hypothesis you form (ids N1, N2, ...), to exactly one outcome:

| Outcome | Goes to | Classification |
|---|---|---|
| CONFIRMED-defect, no owning roadmap item or recommendation | `findings[]` | `novel` |
| Planned: an owner exists but its remedy is insufficient for the scale this audit models | `findings[]` | `planned-insufficient` |
| Planned: an owner exists, its remedy would suffice, it is unbuilt, and nothing sequences it before the trigger you compute | `findings[]` | `planned-unbuilt` |
| Planned and fully covered, with sequencing adequate to the trigger | `rejected_candidates[]` | n/a (name the owner) |
| Not a defect | `rejected_candidates[]` | n/a (name the compensating control and its property match) |
| Cannot be decided with the evidence available (for example under a degraded read) | `inconclusive_candidates[]` | n/a (name what blocked it and what would decide it) |

Every finding lists, in `candidates`, the C or N ids it adjudicates (empty if it arose from no candidate).

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
- C8. Main Canary runs the same full tier as `main-validate` and re-runs unchanged SHAs. Its observed
  start-to-start interval varies widely. Beyond its decided backstop role for plans-only merges
  (Decision 205 clause 5), what it detects that `main-validate` does not may be undefined.
- C9. Production deploy channels trigger on push in parallel with the full tier, so they may deploy a
  commit whose full tier later fails.
- C10. Decision 217's scale-readiness triggers key on executor concurrency, which is frozen at 1.
  Merges from interactive sessions may already exceed what those triggers were meant to detect.
- C11. The reserved vocabulary ("promotion" is a gated deploy between deploy tiers) and the
  single-account rule may conflict with a last-green ref inside a single tier, as rec-4874 proposes.
- C12. A head created by updating the branch on GitHub carries only fast-tier evidence, so merge-time
  verdict integrity may rest entirely on post-merge detection.
- C13. `pr-conflict-signal` polls every open agent PR on every push to main. Its API calls may grow as
  pushes times open PRs against the per-repository GITHUB_TOKEN rate limit.
- C14. The verdict personas due to run on Actions (code reviewer, plan critic, decision scout, plus the
  existing CI-RCA agent) may compete with validation for the same concurrent-job pool.
- C15. The structural fix for the `docs/DECISIONS.md` hotspot (the per-file split) is gated on a
  deferred roadmap item. That timing may be too late for current conflict rates.
- C16. Agents may strand before they ever push, so no GitHub artifact records it. Existing telemetry may
  or may not measure stranding.
- C17. Decision 205 clause 5 (a plans-only merge runs the fast tier on main) is unbuilt (rec-4114).
  Plans-only merges are a large share of merges, so building it may remove much of the post-merge
  full-tier demand without any batched model.
- C18. A plan upgrade, larger runners, or sharding the full tier may defer saturation past H10 at lower
  cost than a batched post-merge model.
- C19. The fraction of time main is red may grow with merge rate. Red-main recovery makes a handoff
  during a red wait for a green full tier, so at H10 the time in which any handoff can complete may
  shrink sharply.

## 3. READ FIRST: disambiguation traps

- "canary" names at least five things here. IN SCOPE: Main Canary (`.github/workflows/main-canary.yml`,
  a scheduled full-tier run against main). CONTEXT ONLY: the "L8 drift canary" of Decision 73 clause 2,
  the historical name of that same scheduled full tier (described as hourly; today's cron is
  three-hourly). OUT OF SCOPE: the production telemetry smoke canary (Decision 236); the security
  heartbeat canary (`terraform/bootstrap/platform_security_heartbeat_canary.tf`); the session hook
  canary (`.claude/hooks/hook_canary.sh`). Do not treat an out-of-scope canary as a CI signal.
- "TAP" names two things. IN SCOPE: Google's Test Automation Platform model (fast presubmit, batched
  postsubmit, culprit finding), which the repository says it follows (`docs/contracts/git-ops.yaml`
  `two_tier_presubmit_model.description`, "Google TAP style"). NOT THE SAME THING: "THE TAP RULE" in
  `git-ops.yaml` `trailer_acceptance_gate.source_admission`, a rule for rec-closure verdict sources
  (never re-execute a command whose outcome an authoritative record already carries). Cite it only where
  rec closure is at issue.
- "queue" names four things: a pre-merge merge queue (GitHub's native one, not adopted); the post-merge
  per-commit full-tier runs queueing on runners (the requester's interim model); GitHub runner job
  queueing under the concurrent-job cap; and `ops_priority_queue`, the warehouse work queue (out of
  scope). Name which one you mean every time.
- "merge gate" names two kinds of control. Here it means a client-side PreToolUse hook
  (`.claude/hooks/merge_gate.sh`, Decision 245) that only agent merges through two MCP tools face.
  Server-side merge controls (the ruleset's required checks, a GitHub merge queue) are different: they
  bind every merge surface. Never credit one with the other's coverage.
- "attestation" names two strengths of evidence. Local: `logs/debug/validation-result.json` written by
  `scripts/checks/validation_result.py` and checked by `--verify-head`; the agent can write it
  (rec-4085). Server-side: a verdict produced by CI for a SHA, which the subject cannot forge. EC5 and
  rec-4790 option (a) depend on which one is meant.
- "environment" names two things: a GitHub Environment (a security primitive that gates job execution
  and scopes the OIDC subject, e.g. `tf-gated-apply`, `tf-trusted-plan`, `pypi`) and a deploy tier
  (sandbox / SIT / PROD, `docs/contracts/environment-taxonomy.yaml` `axis_a`). Keep them apart.
- "promotion" is reserved vocabulary: "a gated deploy between deploy tiers, never anything else"
  (`environment-taxonomy.yaml` `general_conformance`). This prompt says "gate on a green full tier" or
  "last-green ref" for a single-tier mechanism. If you recommend "promotion" for one, recommend the
  vocabulary change explicitly (C11).
- "sandbox" names the deploy tier, Anthropic's sandbox-runtime for local persona hosts (Decision 234,
  T4.34), and the network-less probe sandbox of rec closure. Only the deploy tier is in scope.
- "concurrency" names three things: executor concurrency (the frozen executor's rec-level parallelism,
  the trigger in Decisions 217 and 245); interactive session parallelism (many operator-launched
  sessions merging today); and GitHub Actions `concurrency:` groups. C10 turns on keeping the first two
  apart.
- "Scion" names two things. IN SCOPE: Anthropic's internal source-control system, described in
  "Redesigning source control at Anthropic" (https://www.anthropic.com/engineering/redesigning-source-control,
  2026-10-13). NOT IN SCOPE: an unrelated Google agent-orchestration project of the same name. rec-4874
  cites the Anthropic post under a `/research/` path; the `/engineering/` URL above is the one Decision
  217 cites.
- "full tier" runs in two places: locally before handoff (S2), and in CI after merge (S3, `main-validate`)
  plus on the canary schedule. A finding about one is not a finding about the other.

## 4. SCOPE

| ID | Surface | State |
|---|---|---|
| S1 | Presubmit and merge path: `ci.yml` `pr-validate` (fast tier, `--pre`), `terraform-validate`, `signal-green`; `.github/workflows/pr-conflict-signal.yml`; the merge gate (`.claude/hooks/merge_gate.sh`, `.claude/hooks/merge_gate.py`, `scripts/session/merge_overlap.py`); the main ruleset (`terraform/github/repo.tf`) | built |
| S2 | Agent handoff path: `git-ops.yaml` `handoff_sequence`, `rebase_phase_distinction`, `handoff_evidence_gate`; `scripts/session/rebase_resolve.py`; `docs/contracts/mechanical-conflicts.yaml`; `scripts/checks/validation_result.py`; `.claude/hooks/handoff_evidence_gate.py`; `.claude/skills/implement/SKILL.md` ("Pre-Push Rebase", "IMPLEMENTATION Commit Flow") | built |
| S3 | Post-merge verification: `ci.yml` `main-validate` and the trailer jobs (`trailer-census`, `trailer-acceptance-evaluate`, `trailer-junit-verdict`, `trailer-closure`); `.github/workflows/main-canary.yml` | built |
| S4 | CI-RCA attribution and red-main recovery: `.github/workflows/ci-rca.yml`; `scripts/ci_rca/` (notably `vacuous_pass.py`, `red_overlap.py`, `fingerprint.py`); `docs/contracts/ci-rca-lifecycle.yaml`; `docs/contracts/red-main-recovery.yaml` | built |
| S5 | Deploy and promotion: `.github/workflows/deploy-ducklake-lambdas.yml`, `deploy-prod-lambdas.yml`, `terraform-apply-sandbox.yml`; `docs/contracts/deploy-paths.yaml`; `environment-taxonomy.yaml`; the promotion train (Decision 24, Decision 73 clause 5) | deploy-on-push built; promotion train designed-unbuilt |
| S6 | Designed-unbuilt scale commitments: rec-4874, rec-4790, rec-4873, rec-4114, Decision 217 clauses 1-8, roadmap T4.7, T4.8, T4.9a, T4.21, T4.24 | designed-unbuilt |
| S7 | Shared GitHub Actions capacity: every workflow under `.github/workflows/` that runs on push, pull_request, workflow_run or schedule; the verdict personas (roadmap T4.28-T4.30 on T4.32); the Fargate compute plane (T4.15; `docs/contracts/ports.yaml` `persona_compute_host`, `verdict_plane`) | workflows built; personas and Fargate plane designed-unbuilt |

REQUESTER DIRECTIONS (inputs recorded when this prompt was composed; you may argue against any of them
with evidence, but never ignore one):

- RD1 Interim model: keep the per-commit post-merge model (each push to main gets its own full-tier run,
  queued on runners) with per-PR attribution, and defer the batched-culprit / CI-RCA refactor until a
  measured trigger says otherwise. A pre-merge GitHub merge queue is NOT what is meant. Q3 computes that
  trigger.
- RD2 Horizons: plan for both 10x and 100x the current peak (H10, H100), not a slight increase.
- RD3 GitHub plan: the organisation is on GitHub Free, and the repository is public.
- RD4 Personas: the code reviewer, plan critic and decision scout verdict personas, plus the existing
  CI-RCA agent, will run on Actions imminently and compete with validation for minutes and concurrency.
  The requester expects them to be "fairly straightforward to put onto fargate containers alongside
  planning and implementation agents".
- RD5 Decisions: you may propose reversing or amending ANY Decision (see DEDUP DISCIPLINE for the form).

Vocabulary you use in your output:

- BASE: the commit from SETUP step 1. BASE_DATE: `git show -s --format=%cI <sha>` converted to UTC.
  "BASE_DATE minus N days" means that instant minus N x 24 hours. Every git read uses `<sha>`, never a
  moving `origin/main`.
- Merge rate: first-parent commits reachable from BASE per UTC day (all merges are squash merges).
- H0: the merge count on the busiest UTC day in the 30 days before BASE_DATE. Peak hour: the most
  merges in any 60-minute window in the 7 days before BASE_DATE. H10 = 10 x H0; H100 = 100 x H0. Scale
  the peak hour by the same multiple.
- Per-commit post-merge model: RD1. Batched post-merge model: one full-tier run covers several merges,
  and a red needs culprit finding.
- Time-to-push: wall clock from an agent's commit being ready to an accepted push. Time-to-merge: from
  PR creation to merge.
- Stranding: an agent session that stops making progress and has no pending wake.

Out of scope (one line each): the content of individual checks inside `scripts.validate`, beyond tier
membership and duration; Terraform guard classification and IAM policy content, beyond the deploy
trigger and identity question in DD-D; telemetry table design and reader code (the telemetry named
verbs ARE in scope as measurement sources, SETUP step 4); the frozen executor's code
(`scripts/executor/`); adopter products; recommendation-queue hygiene; GitHub Environment protection
settings, beyond whether a deploy gate needs a new one.

TRUST NOTHING: obtain every file, line and size by reading the file. Trust no number quoted in this
prompt: re-derive it from the repository at BASE or from the named live source, and record any anchor
that does not resolve in `meta.stale_anchors` (format `"<prompt anchor> -> <what you found>"`).
`docs/DECISIONS.md` is newest-first, so its line numbers drift: locate each Decision by its
`## Decision NNN:` header, never by line number.

## 5. SETUP

Run, in order:

1. `git fetch origin main` then `git rev-parse --short origin/main`. Record the result as `<sha>` (BASE).
   Do this ONCE. Use the literal `<sha>` everywhere afterwards: file reads (`git show <sha>:<path>`), git
   logs, the deliverable filenames, the branch and `meta.audited_commit`. If `git rev-parse
   --is-shallow-repository` prints `true`, run `git fetch --unshallow origin main`; it changes no
   tracked file.
2. Cache generation: `bin/venv-python -m scripts.session.preflight --roadmap-detail full`. It writes
   `logs/.preflight-report.json` (roadmap state, CI-RCA counts, branch freshness).
3. Recommendation reads (there is no local recommendations cache). Any ONE of these sources suffices:
   `bin/venv-python -m scripts.recs_related --terms "<space-separated terms>" --limit 20 --table`;
   `bin/venv-python -m scripts.recs_related <repo paths> --table`; or the `mcp__ducklake-reads__recs_by_ids`
   and `mcp__ducklake-reads__ci_rca_since` named verbs. Page with `--exclude <next_exclude>` when
   `complete=false`.
4. Telemetry reads (measurement sources for C16, C10 and Q7): `mcp__ducklake-reads__sessions_window`
   (one derived row per root session, at most 7 UTC days per call, arguments `day_from`, `day_to`,
   `as_of`), `mcp__ducklake-reads__session_state_and_duration` and
   `mcp__ducklake-reads__agent_run_state_and_duration` (per session id). How they derive `abandoned` is
   defined in `docs/contracts/telemetry_sessions.yaml` (around lines 158-165).
5. GitHub reads: use the `mcp__github__*` tools (`actions_list`, `actions_get`, `list_commits`,
   `list_pull_requests`, `pull_request_read`) for the EMPIRICAL PASS. Use no write tool except those in
   COMMIT / PR MECHANICS.
6. External sources: if WebFetch is available, re-read the Scion post (URL in section 3) and GitHub's
   Actions limits page (https://docs.github.com/en/actions/reference/limits).

Degraded paths. Never abort; set the flag, apply the stated effect, and proceed:

- IF step 2 fails, or IF every source in step 3 fails: set `meta.degraded_dedup=true`; set every
  finding's `confidence` to HYPOTHESIS and its `roadmap_crossref.dedup_hit_count` to null; still grep
  `docs/ROADMAP-PLATFORM.yaml` and `docs/DECISIONS.md` locally and record those searches in
  `roadmap_crossref.dedup_search_terms`. A failure of some step 3 sources while another works is not
  degraded; note it in `meta.contract_notes`.
- IF step 4 fails: set `meta.degraded_telemetry_reads=true`; adjudicate C16 from GitHub evidence and
  rec-4790 alone; cap the confidence of C16, C10 and Q7 at HYPOTHESIS.
- IF the GitHub tools are unavailable or rate-limited: set `meta.degraded_github_reads=true`; skip the
  affected EMPIRICAL PASS items; tag every rate-dependent claim `evidence_kind: static` and `confidence:
  HYPOTHESIS`; set the affected `capacity_model` fields to null and say why in its `note`.
- IF WebFetch is unavailable: set `meta.external_unverified=true`; rely on the compose-time external
  facts in G9; cap Q6 and Q7 `confidence` at HYPOTHESIS for any claim that rests only on G9.
- IF `git fetch --unshallow` fails: note it in `meta.contract_notes` and mark E1 and E2 derived values
  HYPOTHESIS.
- IF an anchor does not resolve: record it in `meta.stale_anchors`, find the current location if you
  can, and continue.
- IF any repository check fails for reasons unrelated to your two files: record it in
  `meta.contract_notes`. Never fix it (write boundary).

## 6. NORTH STAR

The bar you judge each surface against. These are not absolutes: argue each judgment from evidence, and
say when a principle should yield to cost or a deliberate constraint.

- NSP-1 No serial global step on the merge path. Throughput must not be bounded by one-at-a-time work
  whose duration is a full-tier run. Scion's post says a conventional merge queue "doesn't make much
  sense at agent scale", and Decision 73 chose forward-fix over pre-merge full-tier gating. Weigh this
  against cheap, bounded serial steps.
- NSP-2 Liveness. Every wait an agent enters is bounded, observable and wakes its waiter. When a bound is
  exceeded, the outcome is an escalation, never silence.
- NSP-3 Production consumes only trees with a passing authoritative verdict, or the exception is
  explicit, named and bounded.
- NSP-4 Verdict integrity. A verdict binds to an exact SHA, and the subject being judged cannot forge it.
- NSP-5 Attribution economy. The cost of finding the culprit grows sublinearly with merge rate, and any
  loss of precision (one red attributed to several changes) is explicit and bounded.
- NSP-6 Conflict economy. Derived data is computed, not committed. Files every change must edit are
  restructured (per-entry files, generated indexes), not serialised.
- NSP-7 Measured triggers. A scaling step fires on a measured metric crossing a stated threshold, never
  on narrative (north star NS.6, "Improvement is proven, never asserted", `docs/contracts/north-star.yaml`).
- NSP-8 Portability. The CI and deploy-gating design is expressible as a port with GitHub as one adapter,
  and works for an adopter at any scale (NS.1; NS.7, "scale is never blocked").
- NSP-9 Human gates guard intent, not mechanics (NS.9).
- NSP-10 Proportionality. CI work per merge, in runner minutes, concurrency slots and tokens, is
  proportional to the risk the merge carries. Prefer the cheapest lever that meets the trigger.

## 7. THE QUESTIONS

Each question has its own answer slot (`question_answers[]`). Pinned verdict enums are inline.

Q1. Handoff liveness. At H0, can an agent reliably get from a ready commit to an accepted push, and from
an open PR to a merge? Trace the full path: rebase, local full tier, `--verify-head`, push, PR CI, merge
gate, update-branch loop, merge. Compute expected time-to-push and time-to-merge as functions of merge
rate (DD-A). Choose the handoff remedy. rec-4790 names (a) attestation carry-over, (b) push on
fast-tier evidence with the full tier gating elsewhere, (c) a server-side merge queue; you may name
another. State which Decisions the choice amends or reverses. Verdict enum: `viable | degraded |
non_viable`. The remedy goes in `handoff_remedy`.

Q2. Conflict sources. What structurally produces textual and semantic conflicts today? Consider at
least: hotspot files (E2); committed derived numbers; rosters outside the mechanical classes; monolithic
append-heavy files; the merge gate's same-file predicate versus different-file semantic conflicts. For
each mitigation, state whether it can land now or is gated, and on what. Verdict enum (the current
design's conflict handling): `sufficient | partial | insufficient`.

Q3. The per-commit post-merge limit. Under RD1, at what merge rate does that model stop being sound?
Consider: runner saturation under the 20-job cap shared with S7; queue latency against the red-main
repair SLO (`red-main-recovery.yaml` `recovery_policy.slo_hours`); red occupancy (C19); attribution
ambiguity once queued runs overlap; and the effect of building Decision 205 clause 5 (C17). What is the
minimum change to CI-RCA evidence and rec closure (C7, DD-C) when it does stop being sound? Fill
`capacity_model`. Verdict enum: `sufficient_until_trigger | approaching_limit | exceeded`.

Q4. Workload placement. For each workload key in `workload_placement`, where should it run? Weigh the
shared Free-plan concurrent-job cap (RD3), RD4, and Decision 205: clause 1 lets an LLM persona run on
GitHub-hosted runners only on a GitHub repository event, verdict-shaped; clause 6 scopes the Fargate
compute plane to planning and implementation. Weigh also the `verdict_plane` port (a non-Actions host
may post a check run through a verdict App), and that self-hosted runners were retired (CD.21; `ci.yml`
comment around line 407). Fill `capacity_model.capacity_levers` with every lever you weighed (plan
upgrade, larger runners, sharding, risk-proportional tiers, moving workloads off Actions, fewer
per-push jobs). Verdict enum (overall): `placement_sound | placement_needs_change | placement_blocking`.

Q5. Deploy gating and the canary. For each channel in `deploy_channel_gating`: should production
consume only a commit with a green full tier now, through a last-green mechanism inside the single tier;
only after a separate pre-production environment exists; or stay on push? One option to evaluate, not a
verdict: move the Lambda deploy workflows from `on: push` to `on: workflow_run` of a successful `CI`
push run, checking out that run's head SHA, last-green-wins (DD-D). What should Main Canary be for?
Candidate roles: its decided backstop for plans-only merges (Decision 205 clause 5); a same-SHA re-run;
a detector of drift from time-dependent or live inputs; the runner of a future batched post-merge model;
retirement. Is a separate environment needed at H0, H10 or H100? Verdict enum (overall): `deploys_sound
| deploys_partially_gated | deploys_ungated`.

Q6. Industry rating at scale. Rate the whole pipeline against this EXTERNAL CHECKLIST, property by
property (`external_checklist`, rating `met | partial | missed`; `partial` requires a named,
property-matched compensating control in `evidence`). For each property, also say whether the current
design holds it at H10 and at H100.

- EC1 Presubmit/postsubmit split with a bounded fast presubmit (Google TAP).
- EC2 Batched postsubmit with automated culprit finding (TAP-style culprit finding or bisection).
- EC3 Pre-merge testing of the composed tree, batched or speculative (Uber SubmitQueue, GitHub merge queue, Bors/Mergify).
- EC4 Server-side rebase and merge, so clients never race the trunk (Scion; GitHub update-branch and merge queue).
- EC5 The verdict is bound to the exact SHA and produced and attested server-side.
- EC6 Deploy from last-known-green, with bake time or progressive promotion.
- EC7 An automated bisect-and-revert (or forward-fix) pipeline with a bounded red window (Scion; Chromium-style sheriffing).
- EC8 Conflict avoidance by construction: derived data computed, not committed; per-entry records; semantic locks.
- EC9 Agent-owned conflict repair, with escalation.
- EC10 Flake quarantine and deterministic test selection.
- EC11 Elastic CI capacity, with prioritisation under saturation.
- EC12 Pipeline observability: merge rate, queue time, red window, conflict rate, time-to-merge and stranding are measured and alarmed.

Verdict enum: `holds_at_h100 | holds_at_h10 | holds_at_h0_only | failing_at_h0`.

Q7. Scion relevance and Decision 217's triggers. Which Scion mechanisms (G9) transfer to this
repository's scale and substrate, and which do not? Decision 217 records Scion-derived commitments behind
triggers. Are those triggers keyed to a signal that tracks the concurrency the pipeline actually
experiences (C10)? Verdict enum: `triggers_correct | triggers_mis_keyed | triggers_missing`.

Q8. Sequencing and timing. Place every finding, plus every planned item you judged relevant, in
`sequencing_plan` with exactly one horizon:

- `now`: addresses pain at H0, or its trigger is already crossed or is projected to cross before the
  growth model's next doubling date.
- `next`: its trigger is projected to cross between the next doubling date and `projected_date_h10`.
- `later`: its trigger is projected to cross after `projected_date_h10`, including beyond H100.
- `not_recommended`: ONLY for a planned item (rec or roadmap id) you judged relevant and recommend not
  building. Never use it for a finding.

Each entry carries a trigger metric, threshold, current value, measurement source, dependencies and the
Decisions it amends or reverses. Compare your order against the roadmap's: rec-4874's scope, Decision
217's coverage homes, and the critical path in `docs/PROJECT_CONTEXT.md` ("Roadmap end-state map").
Verdict enum: `aligned | partially_aligned | misaligned`.

Q9. Questions the requester did not think to ask. Answer each seed below, then ADD at least two of your
own.

- Should the local pre-handoff full tier exist at all once a server-side post-merge verdict gates
  deploys?
- What does a single red cost today: merges landing on a red base during the red window, at H0 and H10?
- Do the verdict personas' Actions triggers multiply per-PR job count, and by how much?
- Which constraint binds first: the 20-job cap, the GITHUB_TOKEN rate limit, or the trigger rate?
- Is the sandbox deploy tier now production in practice (Decision 73 clause 5's premise), and what
  follows if so?
- What does an adopter on a private, minutes-billed repository inherit from this design?

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
  main. With local full-tier duration T and merge rate r per minute, the probability that main changes
  during the run is 1 - exp(-r T) under a Poisson assumption; state any better model you use. Add the
  merge-gate OVERLAP probability (from E2 and E5) and the update-branch CI wait (pr-validate duration from
  E3). Count every point where the agent ends its turn waiting on a wake, and name the wake source for
  each. Flag any wait with no wake source, or one whose source does not reach a subagent (AGENTS.md "a
  subagent never receives the completion").
- DD-B (feeds Q3, Q4). The capacity model. Use this method so two runs agree:
  - `jobs_per_push_to_main`: count every job, across all workflows, whose trigger fires on every push to
    main with no path filter and whose `if:` depends only on the event type. List separately, in the
    note, path-filtered jobs and jobs whose `if:` depends on commit content.
  - `jobs_per_pr_update`: the same rule for a pull_request synchronize event on an agent branch.
  - Open agent PRs at horizon H follow Little's law: merges per hour at H times the median time-to-merge
    in hours (E5). Assume time-to-merge stays constant unless you argue otherwise.
  - Slot demand at the peak hour: (peak-hour merges at H) x (jobs per push x mean job minutes / 60 +
    PR-side jobs per merged PR x mean PR job minutes / 60 + persona jobs per merged PR x assumed persona
    minutes / 60), plus the hourly average of scheduled jobs. State every input and assumption.
  - `saturation_merges_per_day`: the daily rate (scaled by the observed day-to-peak-hour ratio) at which
    peak-hour slot demand equals the cap. Use peak-hour demand for `binding_constraint`, and report
    daily-average demand in the note.
  - Red occupancy at H: min(1, reds per day at H x mean red window in hours / 24). Reds per day at H =
    merges per day at H x reds per merge (E4, separating flakes from real regressions).
  - GITHUB_TOKEN calls per hour for `pr-conflict-signal`: calls per open PR per push (read
    `scripts/ci/pr_conflict_signal.sh`) x pushes per hour x open agent PRs, against 1,000 per hour.
  - Recompute slot demand with plans-only merges running only the fast tier (C17). Report both values.
- DD-C (feeds Q3, Q5). The coupling map of per-push `main-validate`. List every consumer that depends on
  one full-tier run per push-to-main commit: CI-RCA's PR lookup and `HEAD^` diff; `trailer-junit-verdict`'s
  same-run junit; Decision 226's `workflow_tier` classification; anything else you find. For each, state
  what breaks under a batched post-merge model and the minimum change that preserves it.
- DD-D (feeds Q5). Deploy-versus-verdict ordering. For each deploy channel, trace the trigger, the
  concurrency group, the identity it assumes (G8), and whether a `workflow_run` trigger would keep that
  identity. Join E6's sample to determine how often a deploy preceded a red verdict on the same SHA.

## 10. GROUNDING MAP

This map spends your cognition on judgment, not grep. Verify every entry before relying on it, and
record non-resolving anchors in `meta.stale_anchors`. Facts are stated neutrally. Numbers are
compose-time observations from a tree a few commits before BASE; re-derive them.

G1. Presubmit and merge (S1)

- `.github/workflows/ci.yml`: `pr-validate` (around line 16) runs `python -m scripts.validate --pre`
  with a `concurrency` group that cancels in progress per ref. `scripts/validate.py` (around line 81):
  `_FAST_TIER_BUDGET_SECONDS = selection_budget.FLOOR_TOTAL_SECONDS`.
- `terraform/github/repo.tf`: required checks `pr-validate` and `terraform-validate` (around lines
  103-110); `strict_required_status_checks_policy = false` (around line 116); a bypass actor with
  `bypass_mode = "always"` (around lines 79-81). Decision 83 records the non-wedging intent.
- `signal-green` (`ci.yml`, around line 539) posts a CI-green comment on PRs whose head ref starts with
  `agent/`, `claude/` or `ccr-`.
- `.github/workflows/pr-conflict-signal.yml` runs on every push to main and delegates to
  `scripts/ci/pr_conflict_signal.sh`, which polls the mergeability of every open PR matching those
  prefixes and posts one wake comment per head SHA on a conflict (`git-ops.yaml`
  `push_pr_ci_merge_flow.wake_signals.merge_conflict`).
- Merge gate (Decision 245, decided 2026-11-03): refuses `merge_pull_request` and `enable_pr_auto_merge`
  when origin/main has changed, since the merge-base, any file the PR also changes
  (`scripts/session/merge_overlap.py`). Remedy: update the branch on GitHub, wait for the CI-green wake,
  merge. Clause 2: "A heuristic, not verification". Clause 5 defers strict up-to-date checks and a GitHub
  merge queue, and names rec-4874's model as the end state. Its Problem section cites a replay of 38
  merges: 22 behind main, 2 sharing a changed file. The `git-ops.yaml` `push_pr_ci_merge_flow` text
  (around line 265) states no attempt cap for the update-branch loop.

G2. Agent handoff and merge volume (S2)

- `git-ops.yaml` `handoff_sequence` (around lines 189-198), per push: commit, fetch and rebase onto
  origin/main, the full tier on the clean rebased tree, `--verify-head`, push. "Cost basis is now per
  push (every rebase or fix commit needs a fresh full-tier run)".
- `scripts/checks/validation_result.py` `attests_head` (around line 283) is true only when the
  evidence's head tree equals the checked HEAD's tree, among other rules (`ATTEST_RULES`, around line
  268).
- `handoff_evidence_gate` (`git-ops.yaml` around lines 495-513; Decision 203): a fail-closed PreToolUse
  hook with no agent override; `exempt_path_globs` include `audits/*`; `extra_destination_prefixes`
  include `audit/`.
- AGENTS.md (around lines 145-147): the full tier "runs well past the 10-min foreground cap on CC-web:
  start it backgrounded with a 2 h limit from the top-level session (a subagent never receives the
  completion)".
- rec-4790 (open, High, 2026-10-30): the full tier takes about 20-25 minutes; main then merged roughly
  every 10-15 minutes. One PR hit five rebases, four discarded full-tier runs and a final `tree_mismatch`
  push refusal; the operator pushed it by hand. It names options (a)-(c) and says the choice changes
  Decision 203 and Decision 163 point 3.
- `docs/contracts/mechanical-conflicts.yaml`: `pure_insertion` is the only hunk shape any class resolves.
  `derived`: `docs/decisions-index.json`, `config/lambda/ducklake/field_semantics.yaml`,
  `.claude/hooks/hooks.json`. `roster`: `config/ci_rca_taxonomy.yaml`, `scripts/checks/*/_manifest.py`,
  `docs/contracts/*.yaml` under `amendment_log` only. `decision_numbering`: `docs/DECISIONS.md`.
  `excluded`: `.secrets.baseline`, `.github/CODEOWNERS`, the Terraform lock files,
  `config/agent/fast_tier/module_costs.json`, `config/sloc_budgets.yaml`, `config/mypy_baseline.yaml`,
  `config/structural_size_budgets.yaml`, `config/composite_action_body_baseline.yaml`. The `validation`
  key keeps the full tier and `--verify-head` after a resolved rebase. `docs/ROADMAP-PLATFORM.yaml`
  appears in neither list.
- rec-4873 (open, High): `validate_sloc_limits` and `validate_prose_limits` are exact-equality ratchets
  on committed numbers; two concurrent PRs either conflict on the budget line or fold into a wrong
  combined value (the #1805/#1799 incident; rec-4869, closed).
- Compose-time hotspot count, first-parent merges with committer dates from 2026-10-21T00:00Z to
  2026-11-04T00:00Z (242 commits): `docs/DECISIONS.md` 27, `docs/ROADMAP-PLATFORM.yaml` 25,
  `config/ci_rca_taxonomy.yaml` 23, `docs/decisions-index.json` 13,
  `docs/contracts/account-credential-inventory.yaml` 13, `docs/contracts/file-router.yaml` 12,
  `config/composite_action_body_baseline.yaml` 11, `.secrets.baseline` 11, `docs/contracts/ports.yaml`
  10, `docs/contracts/github-org-transfer.yaml` 10 (every path with at least 10).
- Compose-time merge mix, same window: 118 of 242 merges changed only paths under `docs/plans/`; 128
  changed only paths under `docs/`.
- Compose-time merge volume (first-parent, committer date in UTC, 2026-10-04T00:00Z to
  2026-11-04T00:00Z): by ISO week 2026-W41 47, W42 116, W43 73, W44 127, and W45 70 in its first two
  days. Per UTC day the counts ranged from 0 to 41 (two days had none); 2026-11-02 had 33 and 2026-11-03
  had 37.
- Compose-time PR state: two PRs were open at composition, #1806 (opened 2026-11-02, no update after
  2026-11-02T23:18Z) and #1846 (since merged).
- Compose-time telemetry read: `sessions_window` for 2026-11-03 (as_of 2026-11-04T00:30Z) returned 3
  root-session rows, each with `idle_threshold_seconds` 86400.

G3. Post-merge verification (S3)

- `ci.yml` `main-validate` (around line 95) runs on push, executes `python -m scripts.validate` (full
  tier), and declares no `concurrency` group. Six `ci.yml` jobs have conditions that admit push events:
  `main-validate`, `trailer-census`, `trailer-acceptance-evaluate`, `trailer-junit-verdict`,
  `trailer-closure`, `terraform-validate`.
- `trailer-junit-verdict` (around line 268) declares `needs: [trailer-census, main-validate]` and reads
  the SAME push run's pytest junit. `git-ops.yaml` `trailer_acceptance_gate.source_admission`: "same-run
  binding is enforced by the artifact download step itself declaring no `run-id` input".
- `.github/workflows/main-canary.yml`: `cron: '0 */3 * * *'`; runs `python -m scripts.validate` against
  main; uploads the same junit and validation-result artifacts as `main-validate`; has no same-SHA skip.
  Compose-time, runs 950-965: start-to-start gaps from 3h02m to 10h50m; runs 955-959 all on head
  `613360920e`; runs 950 and 964 concluded failure.
- Decision 73 clause 2: "Full tier runs on push to `main` and on hourly scheduled cron (L8 drift
  canary)". Decision 205 clause 5 ends: "A merge touching only docs/plans/** runs the fast tier on main;
  Main Canary's full tier backstops it." rec-4114 (open) owns building that path; `ci.yml` at compose
  time has no path filter for it.
- Other workflows on every push to main: `codeql.yml`, `pr-conflict-signal.yml`, `rec-autoclose.yml`.
  Path-filtered on push: `deploy-ducklake-lambdas.yml`, `deploy-prod-lambdas.yml`,
  `terraform-apply-sandbox.yml`. On `schedule` (14): `backlog-health`, `ci-rca-inactivity-sweep`,
  `codeql`, `convergence-health`, `cost-reconciliation`, `dedup-probe`, `dependabot-stranded`,
  `ghas-probe`, `main-canary`, `security-detector-liveness`, `telemetry-causal-chain`,
  `telemetry-golden-replay`, `telemetry-verification`, `terraform-drift`.

G4. CI-RCA attribution and recovery (S4)

- `ci-rca.yml` triggers on `workflow_run` of `CI`, `Main Canary`, `terraform-apply-sandbox`,
  `rec-autoclose`, `deploy-ducklake-lambdas` and `deploy-prod-lambdas` (around lines 22-30). It is
  gated to failures on the default branch (around line 59). Its concurrency group keys on head SHA plus
  workflow name, with no cancellation (around lines 47-49).
- It resolves one PR per anchor commit: `PR_NUMBER=$(gh api "repos/.../commits/${ANCHOR_SHA}/pulls"
  --jq '.[0].number' ...)` (around line 260).
- `scripts/ci_rca/vacuous_pass.py` computes the merged diff with `git diff --name-only HEAD^ HEAD`
  (around lines 69-70 and 89-92).
- `red-main-recovery.yaml` `recovery_policy`: `slo_hours: 4`; `halt.rules` 0-6 (evaluator
  `scripts/ci_rca/red_overlap.py`); `slo_clock`: "A build that reaches handoff during a red still needs a
  green full tier (Decision 163), so it waits for the repair".
- `ci-rca-lifecycle.yaml` `trigger_scope.pr_branch_disposition`: PR-branch fast-tier failures file no
  recommendation by design.
- rec-4441 (class tally), rec-4428 (deferral-blind escape attribution), rec-4321 (conflict-stop record)
  and rec-4322 (red-window baseline) are open; the last two are Decision 217's measurement recs.

G5. Deploy and promotion (S5)

- `deploy-ducklake-lambdas.yml` (around lines 108-126) and `deploy-prod-lambdas.yml` (around lines
  57-79) trigger on push to main with path filters, plus `workflow_dispatch`; each has a non-cancelling
  concurrency group.
- `terraform-apply-sandbox.yml` (around lines 52-87) applies the PR-time saved plan on push (Decision 77)
  in a non-cancelling concurrency group.
- `environment-taxonomy.yaml` (a Decision 77 contract): `axis_a` rows sandbox / SIT / PROD (around lines
  141-172); `single_account_rule` (around line 396): SIT and PROD are future-state, and standing one up
  is "separately decided"; `general_conformance` reserves "promotion".
- Decision 73 clause 5: "Sandbox tolerates red `main`; SIT and PROD do not", with green-streak promotion
  of 24h and 7d. Decision 24: GitHub Environments with tag-marked single-branch promotion; never built.

G6. Designed-unbuilt (S6)

- rec-4874 (open, High, XL, 2026-11-03), TAP-style promotion model: full tier only on a batched main
  canary; automated culprit bisect; a green canary advances a ref that deploys consume; a red blocks
  promotion, not work. It says it amends Decisions 73, 226, 163 and 203, likely 77 and 126, and should
  start with a REPORT-ONLY design plan and a Decision draft.
- Decision 217 (2026-10-14) cites the Scion post. Clause 1: overlap block for red main (pulled forward by
  Decision 226). Clause 2: textual conflicts; above concurrency 1 a bounded rebase step owns every
  textual conflict. Clause 3: one non-bypass executor GitHub App. Clauses 5-7: per-file Decision split
  and generated index, home T4.24 (`deferred_post_mvp`). Its reversal conditions `concurrency-raised` and
  `red-window-growth` are `kind: manual`.
- Roadmap: T4.8 (semantic locks and leases; `deferred_post_mvp`; inert until concurrency rises above 1);
  T4.7 (plan staleness; `deferred_post_mvp`); T4.9a (verdict callback and merge authority;
  `not_started`); T4.21 (base-ref required check, non-bypass App identity; `not_started`).
- Open: rec-4451 (merge race after a green pr-validate), rec-4085 (server-side attestation), rec-4080
  (merge on red), rec-4087 (inherited-red release), rec-2827 (protected-path no-self-merge), rec-4624
  (ruleset update frozen).

G7. Shared capacity (S7)

- Roadmap T4.28 (code_reviewer), T4.29 (plan_critic), T4.30 (decision_scout): "an Actions verdict job",
  all `not_started`, on T4.32 (persona foundation, `not_started`). T4.15 (implement_agent on Fargate,
  `not_started`).
- Decision 205 clause 1 (event-origin, verdict-shaped LLM personas on GitHub-hosted runners) and clause 6
  (the compute plane covers planning and implementation only). Decision 234 clause 3 (subscription rule)
  and clause 5 (local adapters post-MVP).
- `docs/contracts/ports.yaml` `verdict_plane` (around line 243): adapters `github_actions` (implemented)
  and `verdict_app` (pending, T4.2:c9).

G8. Prior investigation (2026-11-03, a session report outside the repository; re-verify each number)

- Main pushes in about 20 hours: 30. Full-tier `CI` push runs, last 30: median 29 min, p90 37 min, max
  38 min. Red rate over the last 300 main runs (17 Oct to 3 Nov): 14 (4.7%); 5 of the last 30.
- One DuckLake deploy preceded a red verdict on the same commit: PR #1680, deploy run 40548129029 green at
  17:19Z on 27 Oct; `main-validate` run 40548128960 red at 17:44Z. No Terraform apply on push matched a
  red full tier in 21 matched runs.
- The deploy role and the routine apply role trust `ref:refs/heads/main`
  (`terraform/personal/oidc_pipeline_roles.tf`, `terraform/bootstrap/github_ci_apply.tf`). A
  `workflow_run` job runs at the default branch, so its OIDC subject would stay `ref:refs/heads/main`.

G9. External facts (compose-time; re-verify per SETUP step 6)

- Scion post (2026-10-13, updated 2026-10-15), read through a summarising fetch, so quotes are
  near-verbatim. Merges grew from about 2,500 a day (May 2026) to over 30,000 a day (September). "A proper
  merge queue, for example, doesn't make much sense at agent scale." "We haven't been able to effectively
  replicate the industry standard of a global repository lock on the merge queue, so instead, we've
  invested in post-merge and pre-deploy automated testing." "Concurrent changes can conflict, but Claude
  can typically fix these conflicts faster than humans can even identify them." It mentions "automated
  bisect and revert pipelines" without detail. Commits, merges and rebases are server-side. It says the
  concepts "only make sense for truly enormous monorepos" and that the system is not a git replacement.
  It does not describe batching, test selection or deploy promotion.
- GitHub Actions limits page: concurrent jobs on standard GitHub-hosted runners are Free 20, Pro 40, Team
  60, Enterprise 500. The GITHUB_TOKEN rate limit is 1,000 requests per hour per repository. The workflow
  trigger rate limit is 500 runs per 10 seconds.

## 11. EMPIRICAL PASS

Hard bounds. Do NOT exceed any cap. Tag each finding `evidence_kind: observed` (from a sample below) or
`static` (from reading files). An observed finding outranks a static one at equal severity. Cite samples
as `E<n>:<kind>-<id>`, for example `E3:run-41643822253` or `E5:pr-1799`.

- E1 (Q1, Q3, Q8). Merge rate and growth: `git log <sha> --first-parent --since=<BASE_DATE minus 30
  days> --until=<BASE_DATE> --format=%cI`. Compute per-UTC-day counts, H0, the peak hour, and the median
  inter-merge interval on the H0 day. Also count merges per ISO week for at least the last 4 complete
  weeks; fit a log-linear trend; report `doubling_time_days`, `projected_date_h10` and
  `projected_date_h100` (null with a reason if the fit is not meaningful).
- E2 (Q2). Hotspots: `git log <sha> --first-parent --since=<BASE_DATE minus 14 days> --until=<BASE_DATE>
  --name-only --format=`. List paths touched by at least 5 merges. Classify each against
  `mechanical-conflicts.yaml`: derived, roster, decision_numbering, excluded or unlisted.
- E3 (Q1, Q3, Q4). Durations: at most 50 of the most recent completed `CI` runs on push to main
  (`main-validate` job duration, and queue time if reported); at most 30 of the most recent `pr-validate`
  runs; at most 20 Main Canary runs (duration, conclusion, head-SHA repeats, start-to-start gap).
- E4 (Q3, Q5). Reds: CI-RCA recommendations created in the 30 days before BASE_DATE (the `ci_rca_since`
  verb, else `scripts.recs_related --terms "ci_rca"`). For at most 10 of them, record whether
  `main-validate` on the anchor commit and Main Canary both went red, the open-to-close window, and
  whether the cause was a flake or a real regression.
- E5 (Q1, Q2, Q3). Merge timing: at most 15 of the most recently merged PRs before BASE. Record commits
  behind main at merge, time from creation to merge, time from the last successful `pr-validate` to
  merge, and the number of head updates.
- E6 (Q5). Deploy versus verdict: at most 20 of the most recent push runs of each Lambda deploy workflow,
  joined by head SHA to that commit's `CI` push run (conclusion and completion time).
- E7 (Q1, Q9, C16). Stranding: all open PRs (age, last update, mergeable state), and `sessions_window`
  over the 7 UTC days before BASE_DATE (state counts, how many root sessions read `abandoned`, the
  idle threshold). Judge what share of stranding such a read can see: a session waiting on a wake that
  never comes may read `running` until the idle threshold. Never invent a rate.
- E8 (Q3, C17). Merge risk mix: for the E2 window, classify each merge by the paths it changed:
  plans-only (`docs/plans/`), other docs-only (`docs/`), code (any `scripts/`, `src/`, `tests/`,
  `.github/`, `.claude/`), terraform (`terraform/`), deploy-path (any path filter of a deploy workflow).
  A merge may fall in several classes; report counts per class.

Counterfactual tests. Apply each as an operation, and record the answer in the finding's
`compensating_controls_considered` or in the rejected candidate's `control_property_match`:

- Would the merge gate refuse a merge whose combined tree fails only through a semantic conflict across
  different files? If not, it is not a control for that defect.
- Would Main Canary go red on a SHA where `main-validate` went green, absent non-determinism,
  time-dependent inputs or live external reads? If not, name what it detects that `main-validate` cannot.
- Would any deploy workflow refuse to deploy a SHA whose `main-validate` later fails? If not, a later
  green verdict is not a control.
- Would `--verify-head` evidence still attest after a rebase whose base delta touches none of the
  branch's files? (Read `attests_head`.) If not, every such rebase discards a full-tier run.
- Would CI-RCA attribute a red correctly if one full-tier run covered three merges? Trace the PR lookup
  and the `HEAD^` diff.
- Would the conflict-wake comment reach a session that has ended its turn awaiting a different signal?

## 12. METHOD

Phases, in order:

- P1 Read: SETUP, then every S1-S7 anchor in the GROUNDING MAP.
- P2 Trace: the end-to-end path of one change (commit to production).
- P3 Deep-dives DD-A to DD-D.
- P4 Empirical pass E1-E8.
- P5 Adjudicate C1-C19 and anything new (N1, ...).
- P6 Rate the rubric.
- P7 Dedup every finding.
- P8 Fill `capacity_model` and the decision blocks.
- P9 Answer Q1-Q9.
- P10 Fill `sequencing_plan`, then compute maturity and the summary, LAST.

## 13. DEDUP DISCIPLINE

Before filing any finding, search these ownership surfaces:

- `docs/ROADMAP-PLATFORM.yaml` (`tier_items[]`, `candidate_decisions[]`): project it with
  `bin/venv-python -c` and `yaml.safe_load`; never read it whole.
- `docs/DECISIONS.md` and `docs/DECISIONS_ARCHIVE.md`: `rg` over `^## Decision` headers, then targeted
  reads.
- Open recommendations: SETUP step 3.
- Prior audits: `audits/gitops-agent-first-ddfb9cc.yaml`, `audits/ci-rca-system-a49db8f.yaml`,
  `audits/fast-tier-optimisation-3560909.md`, `audits/terraform-deploy-redesign-b67955b.yaml`,
  `audits/private-repo-self-hosted-ci-9a545d2.yaml`, `audits/planning-workflow-scaling-369a963a.yaml`,
  `audits/github-actions-log-management-c144530f02d1.yaml`,
  `audits/executor-substrate-and-billing-shape-ad02653.yaml`.

Record `dedup_search_terms` and `dedup_hit_count` on every finding. A hit means you assess the owner's
sufficiency (`planned-insufficient` or `planned-unbuilt` in findings, or `rejected_candidates` when fully
covered), never a fresh discovery. A finding without a recorded negative search is `confidence:
HYPOTHESIS`. Known owners to start from: rec-4874, rec-4790, rec-4873, rec-4451, rec-4085, rec-4080,
rec-4087, rec-2827, rec-4114, rec-4266, rec-4321, rec-4322, rec-4428, rec-4441, rec-4755, rec-3221; T4.7,
T4.8, T4.9a, T4.15, T4.21, T4.24, T4.28-T4.32; Decisions 73, 163, 203, 205, 217, 226, 245.

Deliberate constraints. These were decided on purpose. Do not file one as a defect in passing. Under RD5
you MAY propose reversing or amending any of them, only as an explicit finding with `change_type:
reverse_decision` or `amend_decision` that names the Decision, the cost of the change, what it unblocks,
and which of its reversal conditions (if any) has fired.

- Decision 67: STRATEGIC plans suspended; the executor frozen.
- Decision 73 clause 3: no automated revert. Decision 226 permits a manual revert PR outside Terraform
  and governed deploy paths.
- Decision 83: `strict_required_status_checks_policy = false` and the admin bypass (non-wedging).
- Decision 163 point 3: the local pre-handoff full tier on the committed, rebased tree.
- Decision 203: the handoff evidence gate, no agent override. Decision 245: the merge gate, no agent
  override.
- Decision 205 clause 5: plans-only merges run the fast tier on main, backstopped by Main Canary.
  Clauses 1 and 6: persona placement. Decision 234: persona hosts and credentials.
- Decision 77 (`environment-taxonomy.yaml`): the `single_account_rule` and the reserved vocabulary.
  Decision 77 and Decision 126: Terraform applies the PR-time saved plan on push; the deploy model.
- CD.21: self-hosted runners retired; GitHub-hosted runners only.
- Decision 128: the 500-SLOC file limit. Decision 84: the warehouse is the source of truth. Decision 101:
  the public-repository content boundary.

## 14. OUTPUT

Write `audits/ci-merge-throughput-scaling-<sha>.yaml` with exactly this shape. Every enum is pinned
inline.

```
audit:
  meta: {audited_commit: <sha>, base_branch: main,
         model: <your self-reported model name, free text>,
         methodology_version: 1,
         scope_surfaces: [S1, S2, S3, S4, S5, S6, S7],
         degraded_dedup: false, degraded_github_reads: false, degraded_telemetry_reads: false,
         external_unverified: false, contract_notes: "", stale_anchors: []}
  requester_directions:      # RD1-RD5 as recorded; Q3, Q8 and capacity_model read horizons from here
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
       evidence: "file:line|item-id|E<n>:<kind>-<id>", note: ""}
  capacity_model:
    jobs_per_push_to_main: <int>
    jobs_per_pr_update: <int>
    persona_jobs_per_pr_event: <int>
    mean_full_tier_minutes: <float>
    concurrent_job_cap: <int>
    growth: {weekly_counts: {<ISO week>: <int>}, doubling_time_days: <float|null>,
             projected_date_h10: <YYYY-MM-DD|null>, projected_date_h100: <YYYY-MM-DD|null>}
    open_prs_at: {h0: <float>, h10: <float>, h100: <float>}
    peak_hour_slot_demand_at: {h0: <float>, h10: <float>, h100: <float>}
    peak_hour_slot_demand_with_plans_only_fast_at: {h0: <float>, h10: <float>, h100: <float>}
    saturation_merges_per_day: <float|null>
    red_occupancy_at: {h0: <float>, h10: <float>, h100: <float>}
    pr_conflict_signal_calls_per_hour_at: {h0: <int>, h10: <int>, h100: <int>}
    github_token_limit_per_hour: <int>
    binding_constraint: concurrent_jobs|github_token_rate|trigger_rate|red_window_slo|other|undetermined
    capacity_levers:
      - {lever: plan_upgrade|larger_runners|shard_full_tier|risk_proportional_tier|move_workload_off_actions|reduce_per_push_jobs|other,
         effect: "", cost: "", horizon: now|next|later|not_recommended}
    note: ""                   # every input and assumption DD-B asks you to state
  handoff_remedy:
    verdict: attestation_carry_over|push_on_fast_tier_full_in_ci|server_side_merge_queue|other
    mechanism: ""
    what_changes: ""
    cost: ""
    decisions_amended_or_reversed: [<decision numbers>]
    rationale: ""
    confidence: CONFIRMED|HYPOTHESIS
  workload_placement:          # every key is required; each value has the fields shown for pr_fast_tier
    pr_fast_tier: {verdict: actions|fargate|actions_until_trigger|retire|other, trigger_metric: "",
                   threshold: "", mechanism: "", cost: "", rationale: "", confidence: CONFIRMED|HYPOTHESIS}
    post_merge_full_tier: {}
    main_canary: {}
    trailer_jobs: {}
    ci_rca_agent: {}
    verdict_persona_code_reviewer: {}
    verdict_persona_plan_critic: {}
    verdict_persona_decision_scout: {}
    pr_conflict_signal: {}
    scheduled_deterministic_workflows: {}
  deploy_channel_gating:       # every key is required; each value has the fields shown for the first
    deploy_ducklake_lambdas: {verdict: gate_on_green_full_tier_now|separate_environment_first|keep_on_push,
                              mechanism: "", what_changes: "", cost: "",
                              decisions_amended_or_reversed: [], rationale: "",
                              confidence: CONFIRMED|HYPOTHESIS}
    deploy_prod_lambdas: {}
    terraform_apply_sandbox_routine: {}
  sequencing_plan:             # the SOLE record of horizon, ordering and dependencies
    - {item: <finding id | rec-NNNN | T-id>, horizon: now|next|later|not_recommended,
       trigger_metric: "", threshold: "", current_value: "", measurement_source: "",
       depends_on: [<items>], decisions_amended_or_reversed: [], note: ""}
  findings:
    - {id: CMTS-01, surfaces: [S1..S7], candidates: [<C or N ids>], question: Q1..Q9, dimension: VD1..VD9,
       title: "", evidence: "file:line|item-id|E<n>:<kind>-<id>", evidence_kind: static|observed,
       current_behavior: "", ideal_behavior: "", gap: "",
       compensating_controls_considered: "",
       change_type: add|rescope|enforce|unify|persist|clarify|retune_gate|reverse_decision|amend_decision,
       proposed_change: "", acceptance: "",
       severity: critical|high|medium|low, severity_rationale: "",
       confidence: CONFIRMED|HYPOTHESIS,
       roadmap_crossref: {classification: novel|planned-insufficient|planned-unbuilt,
                          item_ids: [], dedup_search_terms: [], dedup_hit_count: 0, note: ""},
       effort: XS|S|M|L|XL}
  rejected_candidates:
    - {candidate: <C or N id>, why_dismissed: "", compensating_control: "",
       control_property_match: "", decision_or_item_id: ""}
  inconclusive_candidates:
    - {candidate: <C or N id>, what_blocked: "", what_would_decide: ""}
  summary: {total_findings: <int>, novel_count: <int>, planned_insufficient_count: <int>,
            planned_unbuilt_count: <int>, top_improvements: [<finding ids>],
            highest_leverage_change: <finding id>,
            maturity_S1: <level>, maturity_S2: <level>, maturity_S3: <level>, maturity_S4: <level>,
            maturity_S5: <level>, maturity_S6: <level>, maturity_S7: <level>}
```

Invariants (state them, and satisfy them):

- COUNTING INVARIANT: `findings[]` is the SOLE enumerated list. `total_findings = len(findings) =
  novel_count + planned_insufficient_count + planned_unbuilt_count`. Fully covered candidates live in
  `rejected_candidates`, undecidable ones in `inconclusive_candidates`; neither is a finding.
  `rubric_ratings`, `question_answers`, `capacity_model`, `handoff_remedy`, `workload_placement`,
  `deploy_channel_gating` and `sequencing_plan` are systems of record that findings reference; they are
  never re-counted. `top_improvements` and `highest_leverage_change` MUST be finding ids.
- Every finding id appears in exactly one `sequencing_plan` entry, with a horizon other than
  `not_recommended`. Every C1-C19 id appears in at least one finding's `candidates`, in
  `rejected_candidates`, or in `inconclusive_candidates`.
- `summary.maturity_S<n>` MUST equal the `per_surface_assessment` maturity for that surface.
- `control_property_match` is REQUIRED whenever a compensating control is the reason for dismissal. Name
  the property the control exercises, cite where it operates (mechanism or file:line), and state why the
  control would FAIL if the defect were real.
- CONFIRMED requires the behavior to be traced to file:line or to an observed sampled artifact; anything
  less is HYPOTHESIS.
- Meta flags have readers: `degraded_dedup` caps every finding's `confidence` at HYPOTHESIS;
  `degraded_github_reads` nulls the affected `capacity_model` fields and makes rate-dependent findings
  HYPOTHESIS; `degraded_telemetry_reads` caps C16, C10 and Q7 at HYPOTHESIS; `external_unverified` caps
  Q6 and Q7 confidence. State each applied effect in `meta.contract_notes`.

Write `audits/ci-merge-throughput-scaling-<sha>.md`, a companion report of at most 1500 words: the
executive layer a human reads first. Order: a 5-line synopsis; answers to Q1-Q9 in one or two sentences
each; the `sequencing_plan` as a now/next/later table with triggers; the Decisions you propose to amend
or reverse; open questions for the requester. Use plain ASCII and no emojis.

## 15. SEVERITY + MATURITY

Assign severity AFTER judgment, by defect class. Never inherit it from this prompt's framing.

- critical: the pipeline can produce a wrong-but-trusted outcome (a production deploy, rec closure or red
  attribution proceeds on an unsound or absent verdict), or merging to main halts for all agents with no
  wake and no escalation.
- high: a weakness that materially reduces throughput, liveness or a verdict guarantee, whose
  compensating controls you judged insufficient. This includes a per-agent wait with no wake source.
- medium: redundancy, ambiguity, inconsistency, or a scale limit expected only past H10, with a clear fix.
- low: clarity or wording.

Compensating controls must PROPERTY-MATCH: they exercise the same property AND would fail if the defect
were real (apply the counterfactual to the control). A control that cannot catch the break neither lowers
severity nor justifies dismissal.

Maturity: compute LAST, per surface, top-down; the first match wins. A finding counts against every
surface listed in its `surfaces`.

- frontier: 0 open critical or high findings on the surface, AND every external-checklist property
  mapped to the surface rated `met` or `partial` (never `missed`) in Q6's `external_checklist`, the sole
  source for this test. Mapping: S1 EC1, EC3, EC4, EC5, EC10; S2 EC4, EC5, EC8, EC9; S3 EC1, EC2, EC10,
  EC12; S4 EC2, EC7, EC10, EC12; S5 EC5, EC6; S6 EC8; S7 EC11, EC12.
- strong: 0 critical AND at most 1 high.
- solid: at most 1 critical.
- nascent: otherwise.

The top rating stays reachable when you argued a property-matched compensating control. This prompt's
framing must not foreclose it.

## 16. COMMIT / PR MECHANICS

1. Reuse BASE from SETUP step 1; do not fetch-and-derive again. `<sha>` is in both filenames, the branch
   name and `meta.audited_commit`.
2. `git switch -c audit/ci-merge-throughput-scaling-<sha> <sha>`, so the PR diff is only the two
   deliverables. This is a deliberate, documented exception to the session-branch rule: the audit needs a
   clean two-file diff off the audited base. The handoff evidence gate exempts `audits/*` and admits the
   `audit/` destination prefix (`git-ops.yaml` `handoff_evidence_gate`), so no full-tier run is needed.
   The CI-green comment wake does not fire for `audit/` branches; that does not matter, because you end
   your turn without merging.
3. Pre-push gate: both files must exist, and the YAML must parse (`bin/venv-python -c "import yaml;
   yaml.safe_load(open('audits/ci-merge-throughput-scaling-<sha>.yaml'))"`). Repository-wide validation is
   advisory outside CI. Record any unrelated `bin/venv-python -m scripts.validate --pre` failure in
   `meta.contract_notes`; never fix it.
4. `git add` the two files only, then `git -c user.name=Claude -c user.email=noreply@anthropic.com commit
   -m "audit(ci-merge-throughput-scaling): findings at <sha>"`, appending any attribution trailer lines
   your session's instructions require. Then run `git push -u origin HEAD` as its own command.
5. IF the push is refused (for example by a proxy that admits only a session-assigned branch): first add a
   line saying so to `meta.contract_notes` in both the committed YAML and the files you are about to send.
   Then call `mcp__github__create_branch` (branch `audit/ci-merge-throughput-scaling-<sha>`, from `main`)
   and `mcp__github__push_files` with the two files and the same commit message. Note that this branch
   base is the current main, not BASE; the PR diff is still the two files. IF that also fails, end the
   turn with a message naming the local commit SHA and both errors.
6. `mcp__github__create_pull_request`: owner and repo from `git remote get-url origin`,
   `head=audit/ci-merge-throughput-scaling-<sha>`, `base=main`, not a draft, title `audit: CI merge
   throughput, attribution and promotion timing (S1-S7)`. Body: a 2-3 sentence lede plus the `summary`
   block in a yaml fence, followed by any attribution lines your session's instructions require. IF it
   fails, retry once; if it fails again, end the turn with a message naming the pushed branch.
7. END THE TURN. Do not poll, do not subscribe, do not merge, do not approve, do not file
   recommendations.

## 17. GUARDRAILS

- Write boundary, a closed list: `audits/ci-merge-throughput-scaling-<sha>.yaml` and
  `audits/ci-merge-throughput-scaling-<sha>.md`. Nothing else in the tree: no fix to any audited surface,
  no roadmap edit, no Decision edit, no recommendation filed through the ops portal.
- Read-only toward GitHub and AWS, except the calls in COMMIT / PR MECHANICS steps 4-6. No workflow
  dispatch, no re-run, no comment.
- Public repository: never write AWS account ids, ARNs, ExternalIds, credentials or internal hostnames
  into the deliverables. Cite file paths, run ids and session-free aggregates only; never quote a
  telemetry session id.
- Fewer than about six surviving findings is a valid result: state it, and do not pad. Precision over
  volume. A finding that only restates a planned item's own text is a rejected candidate, not a finding.
- Severity, verdicts and maturity are yours. Nothing in this prompt pre-classifies them.
