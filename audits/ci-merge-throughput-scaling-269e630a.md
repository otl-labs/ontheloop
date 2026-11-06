# CI merge throughput, attribution and promotion timing: audit at 269e630a

## Synopsis

The merge path works at today's peak (41 merges on the busiest day, 9.5 min median time-to-merge) but strands agents in four places: a local full tier that every rebase discards, subagents that can never run it, a red main that blocks handoffs with no wake, and a merge-gate loop with no cap.
Red main is the first constraint to bind: 8% of pushes open a red window of mean 3.6 h, observed occupancy is 0.34 with a 12.6 h window open at BASE against a 4 h SLO, and occupancy reaches 1.0 at about 83 merges a day.
The Lambda deploy channels ship before any full-tier verdict exists (42 of 42 sampled deploys; two shipped a later-red commit) under a sandbox-tolerance premise that no longer covers the ops plane.
Actions capacity saturates at about 123 merges a day as is and about 80 once the verdict personas land; the per-repository token limit binds at 160-300.
Fifteen findings: 5 novel, 8 planned-insufficient, 2 planned-unbuilt; one critical (deploy gating), nine high.

## Answers

**Q1 handoff liveness: degraded.** The common case is fast and no merged head lacked a green fast tier, but the local full tier (20-32 min) has a 0.51 chance of main moving during it at the day average and 0.96 at the peak hour; only a textual conflict forces a rebase, yet the procedure never says a behind-main branch may be pushed, so agents re-rebase on every move (rec-4790: five rebases, four discarded runs, a hand push). Remedy: affected-set carry-over of the attestation plus a rebase-only-on-conflict rule, amending Decisions 163 and 203; retire the local run for interactive handoffs at about 220 merges a day.

**Q2 conflict sources: partial.** Two monolithic files take 11% of merges each; ROADMAP-PLATFORM.yaml is outside every mechanical class; committed ratchet numbers collide (rec-4873); 21 of 45 hotspots stop for the operator; no conflict rate exists (rec-4321 unbuilt). Roster additions and delta ratchets can land now; the per-entry split and the bounded agent-owned rebase are gated on deferred items and a mis-keyed trigger.

**Q3 per-commit limit: approaching_limit.** Zero runner queueing today. The model stops being sound on red occupancy (1.0 at about 83 merges a day), then concurrent jobs (80-123), then the token limit (160-300). The minimum change when batching starts is range-based CI-RCA evidence with a fingerprinted culprit step and an ancestry-bound junit verdict (Decision 201).

**Q4 placement: placement_needs_change.** Deterministic jobs stay on Actions. The verdict personas add about 27 job-minutes per merge and need a placement rule, a capacity trigger and the verdict App before they go live, which needs Decision 205 clause 6 amended. pr-conflict-signal must stop polling PR by PR.

**Q5 deploys: deploys_ungated.** Gate both Lambda channels on a green full tier now through a workflow_run trigger that checks out the successful run's head (last-green-wins inside the single tier; the OIDC subject is unchanged). Terraform's routine apply stays on push. Main Canary keeps three roles and gains a same-SHA skip; every canary red in the sample duplicated a CI red. No separate environment is needed at H0 or H10.

**Q6 industry rating: holds_at_h0_only.** EC6, EC11 and EC12 are missed at H0; six properties are partial and fail by H10; EC1, EC5 and EC10 hold at every horizon.

**Q7 Scion and Decision 217: triggers_mis_keyed.** No merge lock, post-merge testing and mechanical conflict repair transfer; pre-deploy testing and bisect do not exist yet. Decision 217 watches executor concurrency, frozen at 1, while interactive sessions already produce the load; its other conditions are unmeasured.

**Q8 sequencing: partially_aligned.** rec-4874 is the right end state; the cheap now items (rec-4114, rec-4087, rec-4873, rec-4790) are open recs with no plan, the deploy gate waits on an XL redesign although its per-commit form is M, and the personas land on Actions with no capacity trigger.

**Q9 unasked.** The local full tier's catch rate is unmeasured, so the decision to keep it is narrative; the sandbox is production for the ops plane; every operator-escalation figure is a model because one login performs every merge; the comment-based wake transport may hit a secondary limit at H100 (inconclusive).

## Sequencing plan

| Horizon | Item | Trigger (metric, threshold, current) |
|---|---|---|
| now | CMTS-01 carry-over + rebase only on conflict (rec-4790) | forced re-rebase at peak 0.15 per push (crossed) |
| now | CMTS-15 inherited-red release, green-main wake, SLO alarm (rec-4087, rec-4322) | red occupancy 0.5; now 0.34-0.49, open window 12.6 h |
| now | CMTS-09 Lambda deploys on workflow_run of CI success | any deploy before a red verdict; 2 of 22 observed |
| now | CMTS-06 plans-only fast tier (rec-4114), job consolidation, queue probe | peak slot demand 16 of 20; now 6.9 (10.4 with personas) |
| now | CMTS-13 persona placement rule and verdict App before T4.28 goes live | personas declared imminent |
| now | CMTS-02 overseer runs the full tier for its subagents (rec-3039) | every implementation dispatch stalls |
| now | CMTS-04 roster additions, build rec-4321, un-gate Decision 217 cl.2 | 1 conflict stop a day at about 70 merges a day |
| now | CMTS-05 delta ratchets (rec-4873) | any budget-line collision; two reds 2026-11-03 |
| now | CMTS-08 canary same-SHA skip, cross-slug dedup (rec-4233, rec-4114) | 6 of 20 re-runs, 2 duplicate recs |
| now | CMTS-10 re-key Decision 217 triggers | before the next scaling lever |
| now | CMTS-11 vocabulary (last-green ref) | with CMTS-09 |
| now | CMTS-14 waiting_on_wake state, verify-head and gate-refusal observations | every other trigger needs it |
| now | rec-4080 green-check predicate on the merge gate | any merge on red |
| next | CMTS-03 merge-gate attempt cap and fallback | re-refusal 0.3 per iteration at peak, about 220 merges a day |
| next | CMTS-12 conflict-signal rewrite (one query per push) | 500 token calls an hour, about 160-300 merges a day |
| next | CMTS-07 batching prerequisites (Decisions 201, 226) | first batched run, about 165 merges a day |
| next | Team plan and larger runners (levers) | p90 main-validate queue above 300 s |
| next | rec-4451, T4.21 non-bypass identity, rec-4266 self-registration | events named in the YAML |
| later | rec-4874 batched post-merge model; rec-4085; T4.8; T4.7; rec-3221 | Decision reversals and the batching trigger |

## Decisions proposed for amendment

- Decision 163 point 3 and Decision 203 clause 1: evidence identity up to a disjoint base delta; later, retire the local full tier for interactive handoffs.
- Decision 73 clause 5 and Decision 126: red tolerance scoped to the product sandbox; code deploys consume only a green verdict.
- Decision 205 clause 6: verdict personas may run on the compute plane behind the verdict App.
- Decision 217: reversal conditions re-keyed to merges per day, red occupancy, conflict stops, open PRs and token calls.
- Decision 245 clause 1: an attempt cap on the update-branch remedy.
- Decision 201 and Decision 226 clause 3: ancestry-bound junit verdicts and a narrowed halt set, only when batching starts.
- Decision 228: a waiting_on_wake session state.
- Decision 77's taxonomy: allowlist "last-green ref".

## Open questions for the requester

1. Is a 30-40 min deploy delay, and a hold during every red window, acceptable for the DuckLake and prod-class channels? If not, the gate needs Track A reds released by classification.
2. Will the organisation take a Team plan before personas go live, or should personas start on the compute plane?
3. Should the local full tier be retired for interactive handoffs at the 220-merges-a-day trigger, or only once its catch rate has been measured for a month?
4. Can a non-bypass merge identity (T4.21) be pulled forward so escalations become measurable?
5. Does GitHub's secondary limit on content creation apply to the wake comments (inconclusive N2)?
