# Late plan-critique findings: a counterfactual audit of the critique gate's blocking and stopping rules

Audited commit `ea25b5bb4` (main). Records: `audits/plan-critique-late-findings-ea25b5bb4.yaml`. Surface S1 only: the
blocking tag and the loop, confirming-round and escalation rules. Method: for one blocking item in each of 40 REVISE
rounds (8 confirming, 22 other late, 10 from rounds 1-2), what would have happened had it not been fixed.

## 0. Degraded modes

No degraded mode. All 40 samples and all 10 build PRs were processed, and dedup ran against the live rec store.

## 1. Answer

- Late blocking items matter in a minority of cases: 11 of 30 late items are in the core mattered set (pool-weighted
  39.4 percent, band "some", just under "many"). 15 of the 30 changed nothing.
- The 11 are unlike. Four are defects that would have shipped with no control, three would have failed only after
  merge and needed a real repair, one is approved wording, one is a refused closing call the session re-issues, two
  are rollback instructions that bite only on a rollback. Without the last three the weighted share is 27.1 percent.
- Late items are not shown to matter less than round 1-2 items: 9 of 22 core against 0 of 10 (difference +8.0 to
  +61.3 points). The verdict is fragile: one control item reverses it, and half the control items are single diff
  rows from large round-1 commits.
- No candidate rule is safe. R1, R1v, R2 and R2v each demote several CONFIRMED core items. R3 demotes one (I26),
  which a build note covers.
- The gate's weakness is not empty late rounds. It is that the blocking tag does not predict consequence (22 of 40
  items traced CONFIRMED to no effect) and that the one class with no later control, fail-open holes in gate code,
  was caught only at rounds 5 to 7.

## 2. Counterfactuals by group

| Counterfactual | CONF (8) | LATE (22) | Control (10) |
|---|---|---|---|
| shipped_defect | 1 | 5 | 0 |
| caught_post_merge | 0 | 4 | 0 |
| needs_operator | 1 | 0 | 0 |
| shipped_guard_gap | 1 | 1 | 0 |
| caught_by_vp (plan amendment) | 0 | 1 | 1 |
| caught_by_build | 0 | 1 | 0 |
| no_effect | 5 | 10 | 9 |
| Core mattered | 2 | 9 | 0 |

No item is undeterminable; 6 of 40 are HYPOTHESIS. Caveat on the unit: 17 items are single plan rows drawn from a
diff, where one finding spans several rows and nit rows cannot be told apart. Core items are 8 of 18 where a commit
message named the item and 2 of 17 where a diff row was drawn, so the shares above understate named findings.

## 3. Candidate rules (late group: 30 items, 11 core)

| Rule | Blocks | Core kept | Core demoted | Verdict |
|---|---|---|---|---|
| R0 (today) | 30 | 11 | 0 | baseline |
| R1 (ships wrong behaviour) | 8 | 6 | 5 | unsafe |
| R1v (R1 or operator) | 9 | 7 | 4 | unsafe |
| R2 (wrong outcome CI and VP miss) | 5 | 4 | 7 | unsafe |
| R2v (R2 or operator) | 6 | 5 | 6 | unsafe |
| R3 (closed list) | 13 | 10 | 1 | unsafe; safe with notes |
| RX (fitted afterwards) | 13 | 11 | 0 | safe on this sample only |

Question verdict: `no_safe_rule`. R1 misses what is not build behaviour: rollback text (I14, I20), a post-deploy
step that cannot pass (I22), a closing call the portal refuses (I26), approved wording (I04). R2 fails worst because
"the verification plan would catch it" is true of I09 and I15 only after merge. R3 keeps 10 of 11; on a narrow
reading of its clause (iii) it also loses I22. Every rule also demotes the two guard gaps. RX (in the YAML) blocks
on what reaches main, on post-merge-only controls, on forced plan amendments and on operator decisions; it was
written after the traces and must be tested on a fresh sample.

## 4. Q5: the revisit clause

"Did a confirming round ever find a blocking finding, and were findings after round 5 real?" Yes to both.

- In the frame, 32 of 201 confirming rounds returned REVISE (16 percent); 159 returned PROCEED with nits again.
- Of the 8 traced, 2 carried a core item, both CONFIRMED: a sandbox defect that would have shipped (I05) and a change
  to approved wording (I04). One left a guard gap; five changed nothing.
- Two of the 8 (I01, I06) were defects written by the nit fixes applied after the preceding PROCEED. The confirming
  round is doing the job it was given.
- Of 7 traced items from rounds 6 to 12, 4 were real and 2 core (I11, I25), both fail-open holes in gate code.
- Estimated core items per round: about 0.05 for a confirming round, 0.73 at round 3, 0.25 at rounds 4-5, 0.29 from
  round 6. These rest on 5 to 9 items each.

The clause can be closed with: confirming rounds and rounds after 5 both find real defects at a low rate; neither
should be removed until a check is aimed at the class they catch (PCL-01). The sample holds only REVISE rounds, so
it cannot show a loop that stopped too early. One incidental case exists (section 8); the absence of more is not
evidence that the stopping rule is safe.

## 5. Deterministic checks

7 of 40 items were script-checkable (2 late, 5 control); none is core. Three types recur:

- `scope_row` (I18, I33): the test file a build must touch has no scope row; home is the plan lint; exists only for
  a new check module. Red and green on both items, but red on 10 of 40 merged sampled plans, so it needs exemptions.
- `vp_step` (I32, I39): a command that masks its exit status; a plain pytest step marked not-applicable. No check
  exists. The masking shape is still in three merged sampled plans.
- `line_pin_or_size_arithmetic` (I17, I31): NEAR_CAP without a build-time step (an advisory today); a stale
  approved-text digest pin.

## 6. Q6: plan detail and build rework

`no_measurable_effect`. Of 10 build PRs, 2 carry a commit correcting wrong plan detail; 6 are a single commit;
3 needed a plan amendment before merge (a size limit, a ledger pairing, a slow verification step). Every test node
the plans named in their first ten obligation rows exists verbatim in the build (40 of 40). Detail is used; that
it prevents rework can be argued, not measured, without thin plans to compare.

## 7. Top findings

Five findings: 1 high, 4 medium; 3 novel, 2 planned-insufficient. Maturity of S1: strong.

- **PCL-01 (high, novel).** No checklist item asks which inputs a new or changed guard lets through. Three such
  holes (I05, I11, I25) would have shipped with no control and were caught at rounds 5, 7 and 6. Add a round-1 check
  for gate-building plans, and keep the uncapped loop until it exists.
- **PCL-04 (medium, planned-insufficient: rec-3080, Decision 87).** Rows keep counts; the critic's tags and anchors
  are discarded, so yield cannot be measured. Add one line per finding to the gate row.
- **PCL-02 (medium, novel).** The tag follows class membership, not consequence. 15 of 18 named late items fit a
  named class, yet 6 of those 15 had no core consequence. Have the critic state what reaches main and which control
  fails first; stop blocking on closure steps that restate the build skill (I35).

Also PCL-03 (the three checks above; rec-5407 and rec-3885 own parts) and PCL-05 (the last nit batch goes unread;
escalation never fires).

## 8. Q7 highlights

- Escalation fired on 1 of 363 plan-critique rows in the sampled plans and 2 of 1,039 in the frame, all in round 1.
  The longest series with none is 21 rounds (`sit-organization`).
- The tail is partly self-inflicted: 17 of 30 late items sit in text an earlier fix wrote, against 1 of 10 control
  items. Blocking findings stop falling after round 4 and nits stay near 4 to 5 per round.
- One traced nit changed behaviour (`bridge-teardown-end-record` round 5 N1). Nits are fixed anyway; what a
  mis-tagged nit loses is a re-read.
- Under-blocking, one case: `scout-input-fingerprint` ended its loop with a ledger defect open; the build's full
  tier caught it and forced amendment #1999 and a second approval.
- The roadmap's OE-02 assumes 2 critique cycles on average; the frame's median is 5.5 and its maximum 21.
