# REPORT: W1 component 7 - maturity ladder controller (read_all -> sampled -> spot_check -> anomaly_triggered)

Plan: docs/plans/PLAN-w1-maturity-ladder.yaml. Fixture item: pwi-maturity-ladder-controller (1 of 12 cap).
REPORT-ONLY (Decision 86 cl.2): nothing here is built, filed, ratified or flipped. Every rec, table,
verb and schema change named below is a description of a future build.

## 0. Verdict

Every W1 component carries a maturity block, and none of those blocks can be run as written. The six
open items (#1384, #1390, #1394, #1395, #1396, #1397) declare 18 triggers. All 18 point upward; the
pilot schema accepts only the three upward adjacent pairs, so no item can declare a way back down (VP 1).
Every trigger metric is free text, no item states how many outputs a rung reviews, and nothing at runtime
reads a rung (VP 1, VP 2). Two consequences are measured, not argued:

- A clearance count with no return leg measures patience, not quality. "20 consecutive clean reviews" is
  reached after about 36 reviews at a 5% error rate and about 72 at 10%, and once passed nothing brings the
  component back (VP 2). In a seeded one-year simulation run through the controller SQL below, the declared
  triggers let a 20%-error component ship 11.1-12.6% of its outputs wrong and unreviewed, 13.3% on the
  worst seed (VP 5).
- A blinder monitor promotes faster. At a 5% error rate, 58 of 60 components whose failure_signal sees half
  the errors reach anomaly_triggered under the declared triggers, against 38 of 60 at recall 0.9 and 25 of
  60 at recall 1.0 (VP 5). The spot_check -> anomaly_triggered trigger is read off the same signal the top
  rung then relies on, so a weaker signal is rewarded.

The staged controller is one deterministic SQL per daily run (the charter's measurable transition triggers;
Decision 55 by analogy). It reads a review log, the
component's failure_signal series, monitor drills and operator approvals. For each component it returns
promote, propose_promote, hold, demote or restart, with one of 11 reasons and the version the component
holds next. It passes 74/74 vectors (VP 4), eighteen of them by raising on malformed input or params, and
each of 56 hand-run mutants fails at least one vector. A version restart is monotonic on an integer stamp
compared numerically, a stamp that is not a canonical integer raises, and a wrong output under any stamp
still triggers the return leg.
It is a multi-level continuous sampling plan in the Dodge (CSP-1) and Lieberman-Solomon family: a
clearance count per rung, a return leg on any wrong output found above read_all, a restart on a version
change, a demotion on a breaching or dark failure_signal, and a windowed injected-fault drill before the
top rung. Its outgoing-quality bound is simulated, not closed-form: CSP-1's closed form (1.76% at
clearance 40, f 0.2) bounds only the first sampling level. The simulation spans the peak (error rates
0.005 to 0.2, recall 1.0, 0.9 and 0.5) on three seeds of 20 components per cell, and the bound depends on
monitor recall:
- With recall at or above the drill floor (0.9), escaped-wrong share peaks at 1.73% averaged over the
  seeds and 1.85% on the worst seed (p=0.03, recall 0.9). That is under the assumed 2% AOQL target (q3).
- With a half-blind monitor it peaks at 1.99% averaged and 2.09% on the worst seed (p=0.035), so the
  2% target is not met. A weaker monitor breaches less, so the signal demotes less.
- The drill keeps a half-blind monitor off the top rung except by chance (2 of 600 components). It does not
  remove the weaker-monitor advantage inside the 0.03-0.045 band: there a 90%-recall monitor reaches the
  top in 64 of 240 components against 37 of 240 for a perfect one. Below p=0.02 the two match (60 of 60).
  Cures are named under k5, not claimed.
That is paid for in review load: a 2%-error component is reviewed on 20-21% of its outputs instead of
3.7-3.9% (VP 5, section 2.6).

What is settled is narrow: the four rungs and their order (evaluator code), a deterministic decision
(the charter's measurable transition triggers), and the measured facts above. Whether outgoing quality must
be bounded at all, and at what price, is not settled: it is the premise of k1, k4 and k5 and the AOQL target
of q3. The return leg, the version-change rule and promotion
authority are forks with credible alternatives and no admissible precedent (T3.4 and T4.4 call for per-gate
rollback, but that is roadmap text, not a Decision or contract), so they are parked (k1-k3). The decision
rule's family and seeds (k4), the drill (k5), the fixture's edge home (k6) and signal demotion (k7) are
parked report-only. Both #1396 and #1397 defer one
question to this component: whether a params_version change restarts the ladder. That is k2, and its
recommendation is restart at read_all.

## 1. Evidence (each row re-derivable; VP step in brackets)

| id | fact | anchor |
|---|---|---|
| e1 | Rungs are a closed Literal of four, and `Maturity.transitions` must be exactly the three upward adjacent pairs in order; a downward pair is refused and no other maturity field exists [VP 1] | scripts/checks/roadmap/_work_item_pilot_model.py:48, :151-158 |
| e2 | `Trigger.metric` is free text (3-120 printable characters); "anything at all" validates, so no trigger names a computable series [VP 1] | scripts/checks/roadmap/_work_item_pilot_model.py:131-133 |
| e3 | No file under src/ or scripts/ outside the pilot model and its evaluator mentions anomaly_triggered or spot_check: nothing at runtime reads or writes a rung [VP 1] | repository grep |
| e4 | The six open W1 items declare 18 transitions, 18 upward and 0 downward. First-rung clearances are 20 (five items) or 50 consecutive clean reviews; second-rung clearances are zero failures in the last 30, 50 or 200 sampled; five of six third-rung triggers are 30 days of a failure_signal at or under a threshold [VP 2; fidelity against the branch heads, VP 3] | section 1.1 |
| e5 | Zero failures in n reviews bounds the error rate at 95% only to 1 - 0.05^(1/n): 13.9% at n=20, 5.8% at n=50, 1.5% at n=200 [VP 2] | section 1.2 |
| e6 | With no return leg, a consecutive-n trigger fires eventually at any error rate; the expected number of reviews before 20 consecutive clean ones is 36 at p=0.05, 72 at p=0.1 and 429 at p=0.2 [VP 2] | section 1.2 |
| e7 | CSP-1 (Dodge 1943): 100% review until i consecutive clean outputs, then a fraction f, back to 100% on any defect found. Its average outgoing quality limit at f=0.2 is 3.44% for i=20 and 1.76% for i=40 [VP 2] | section 1.2 |
| e8 | T3.4 exit criterion 2 asks for maturity gates A0-A3 with a per-gate rollback criterion; T4.4 asks that every gate carry a numeric threshold with its denominator, window and data source, and that rollback be automatic on breach (roadmap text, not a Decision) | docs/ROADMAP-PLATFORM.yaml:6594, T4.4 exit criteria |
| e9 | Both #1396 and #1397 end their maturity text with the same deferral: "A params_version change is a new rule set; the maturity-ladder controller decides whether it restarts the ladder" | REPORT-w1-rec-filing-dedupe.md section 4; REPORT-w1-back-validation.md section 4 (unmerged) |
| e10 | Decision 73 halt check: the reader's named verb ci_rca_open returned [] at 2026-10-03T12:07:41Z | Step 0 |

### 1.1 Trigger inventory (copied from the six branch heads; VP 3 re-reads them)

`kind` and `n` are this report's classification of each trigger; `transitions` is copied byte for byte.

```yaml
inventory:
  read_at: '2026-10-03T12:15Z'
  items:
    - item: pwi-capture-producer-wiring
      pr: 1384
      branch: claude/w1-capture-producer-wiring
      head: 1da1cc97f3522e5551ab32bcb41e3bb5b93cf685
      kind: [consecutive, zero_in_last, days_clean]
      n: [20, 30, 30]
      transitions:
        - {from: read_all, to: sampled, trigger: {metric: consecutive finalized sessions whose runner-side conformance check reports zero violations, comparator: '>=', threshold: 20}}
        - {from: sampled, to: spot_check, trigger: {metric: runner-side conformance violations across the last 30 sampled sessions, comparator: ==, threshold: 0}}
        - {from: spot_check, to: anomaly_triggered, trigger: {metric: consecutive days with unfinalized_session_share <= 0.05 over the q1 out-of-band session denominator, comparator: '>=', threshold: 30}}
    - item: pwi-telemetry-reader-verbs
      pr: 1390
      branch: claude/w1-reader-verbs
      head: ab7aad7a36819a10069628577a55b2160cf8bd81
      kind: [consecutive, zero_in_last, days_clean]
      n: [50, 200, 30]
      transitions:
        - {from: read_all, to: sampled, trigger: {metric: consecutive sessions whose verb outputs match the harness re-derivation, comparator: '>=', threshold: 50}}
        - {from: sampled, to: spot_check, trigger: {metric: verb re-derivation mismatches across the last 200 sampled sessions, comparator: ==, threshold: 0}}
        - {from: spot_check, to: anomaly_triggered, trigger: {metric: consecutive days with zero mismatches and zero conflicted grain keys reported, comparator: '>=', threshold: 30}}
    - item: pwi-friction-classifier
      pr: 1394
      branch: claude/w1-friction-classifier
      head: c6a47c5df5c1e50c7ab77730dadbe4456965772d
      kind: [consecutive, zero_in_last, days_clean]
      n: [20, 200, 30]
      transitions:
        - {from: read_all, to: sampled, trigger: {metric: consecutive reviewed sessions whose every label the operator judged correct, comparator: '>=', threshold: 20}}
        - {from: sampled, to: spot_check, trigger: {metric: mislabels across the last 200 sampled labels, comparator: ==, threshold: 0}}
        - {from: spot_check, to: anomaly_triggered, trigger: {metric: consecutive days with unmapped_failure_share <= 0.05 at one classifier_version, comparator: '>=', threshold: 30}}
    - item: pwi-deliberation-capture
      pr: 1395
      branch: claude/w1-deliberation-capture
      head: 63b1a50a6926561aac5fb5a9c651c6faba581dc5
      kind: [consecutive, zero_in_last, days_clean]
      n: [20, 200, 30]
      transitions:
        - {from: read_all, to: sampled, trigger: {metric: reviewed sessions in a row per producer with >= 1 classified model_call and zero drift rows, comparator: '>=', threshold: 20}}
        - {from: sampled, to: spot_check, trigger: {metric: drift rows per producer in a full window of the last 200 sampled classified model_calls, comparator: ==, threshold: 0}}
        - {from: spot_check, to: anomaly_triggered, trigger: {metric: days in a row per producer with deliberation_drift_share defined and <= 0.01 for every parser_version present, comparator: '>=', threshold: 30}}
    - item: pwi-rec-filing-dedupe
      pr: 1396
      branch: claude/w1-rec-filing-dedupe
      head: 51958a0ae48a700963fc5b1351fed00e6a1decec
      kind: [consecutive, zero_in_last, days_clean]
      n: [20, 50, 30]
      transitions:
        - {from: read_all, to: sampled, trigger: {metric: consecutive reviewed proposals the operator accepted unchanged, comparator: '>=', threshold: 20}}
        - {from: sampled, to: spot_check, trigger: {metric: rejected filings across the last 50 sampled filer recs, comparator: ==, threshold: 0}}
        - {from: spot_check, to: anomaly_triggered, trigger: {metric: consecutive days with rejected_share <= 0.05 and no duplicate open fingerprint, comparator: '>=', threshold: 30}}
    - item: pwi-back-validation
      pr: 1397
      branch: claude/w1-back-validation
      head: 2b19380fd2b0e646760b3aca5d3d32939d8c0191
      kind: [consecutive, zero_in_last, rate_window]
      n: [20, 50, 90]
      transitions:
        - {from: read_all, to: sampled, trigger: {metric: consecutive verdicts the operator confirmed unchanged, comparator: '>=', threshold: 20}}
        - {from: sampled, to: spot_check, trigger: {metric: overturned verdicts across the last 50 sampled verdicts, comparator: ==, threshold: 0}}
        - {from: spot_check, to: anomaly_triggered, trigger: {metric: 'false_proof_rate over the last 90 days, with at least 20 proof closes', comparator: <=, threshold: 0.05}}
```

### 1.2 What the declared clearances certify

Computed in VP 2 from the inventory block. `ucb95` is the 95% upper bound on the error rate certified by n
clean reviews in a row; `reviews_to_clear` is the expected number of reviews before the first run of n
clean ones at error rate p ((1 - q^n) / (p q^n), q = 1 - p), which is how long a component with that error
rate waits before the trigger fires anyway when nothing ever sends it back.

| first-rung n | items | ucb95 | reviews_to_clear p=0.05 | p=0.10 | p=0.20 |
|---|---|---|---|---|---|
| 20 | capture, friction, deliberation, rec-filing, back-validation | 0.139 | 36 | 72 | 429 |
| 50 | reader-verbs | 0.058 | 240 | 1930 | 350320 |

The second rung's "zero in the last n sampled" (n = 30, 50 or 200) is the same statistic on sampled
outputs. With no return leg it is reachable at n=30 or 50 for error rates up to about 5%, and at n=200
almost never above 2%. A component that stalls at sampled keeps shipping p(1 - f) of its outputs
unreviewed.

CSP-1 is the textbook fix for exactly this ladder. Its clearance number is the same consecutive count, and
its guarantee (the AOQL) holds only because a defect found while sampling returns to 100% review. At f=0.2
the AOQL is 3.44% for i=20 and 1.76% for i=40 (VP 2; AOQ(p) = p(1 - f)q^i / (f + (1 - f)q^i), maximised
over p). The declared triggers keep CSP-1's clearance and drop its return leg. These closed forms describe
a single sampling level. The staged controller adds two more levels (f^2, f^3) and leaves the top only on a
found wrong output or a signal breach, so its own bound is simulated (section 2.6), not closed-form.

## 2. Controller design (what this item stages)

### 2.1 Rung semantics

| rung | routine review | effects the sibling items allow (their text) |
|---|---|---|
| read_all | every output (f = 1) | proposals only: the filer files nothing (#1396 c3), back-validation closes nothing (#1397 c3) |
| sampled | f_sampled of outputs | filer files under its per-run budget (#1396 k3 (a)); a holds verdict is close_proposed (#1397 k1) |
| spot_check | f_spot_check of outputs | as sampled |
| anomaly_triggered | an audit floor f_anomaly_triggered, plus review on a failure_signal breach | back-validation may close directly (#1397 k1 recommendation) |

Selection is per output and deterministic: an output is reviewed when the rung is read_all or when
md5(salt, component, output_id) maps below the rung's fraction. The salt is per component and version, so
a version change re-draws the sample. The levels follow multi-level plans (f, f^2, f^3 with f = 0.2), with
a non-zero audit floor at the top so the monitor is never the only witness. Effects stay the siblings'
business; the controller decides only the rung.

### 2.2 Inputs

`{state}`: one row per component: component, rung, entered_day (first day at this rung), version (the
component's rule-set stamp: classifier_version, parser_version or params_version, each an unpadded
integer in the sibling reports (#1394 classifier_version 1, #1395 parser_version 2, #1396 params_version 1);
the controller compares stamps as integers and a rollback is minted as a new, higher stamp; a rollback
redeployed under an old stamp fails safe but silently: its outputs never count toward clearance, so the
component holds at read_all with reason clearing until a new stamp is minted), signal_threshold.
`{reviews}`: one row per selected output: component, output_id, day, version, rung (at production), outcome
(correct, wrong or unsure; NULL while pending). `{signal}`: one row per component per day: the
failure_signal value, NULL when undefined (deliberation's "undefined, never 0" maps here). `{drills}`: one
row per injected-fault run: component, day, injected, detected; only runs in the last drill_window_days of
the stint count, so one old miss does not block the top rung for good (v39). `{approvals}`: one row per operator
approval: component, to_rung, day. `{today}` is the run's day. All five are build-time objects (q2).

Params are data, stamped as params_version on every transition record (seed values, unmeasured, q3):

```yaml
params_version: 1
clear_read_all: 40
clear_sampled: 40
clear_spot_check: 20
review_sla_days: 3
dark_days: 2
signal_window_days: 30
drill_min: 20
drill_window_days: 20
drill_recall_min: 0.9
on_wrong: read_all
signal_demotes: true
signal_gates_promotion: true
version_restart: true
promotion_authority: operator
f_sampled: 0.2
f_spot_check: 0.04
f_anomaly_triggered: 0.008
```

### 2.3 The decision

Malformed inputs and params raise instead of deciding (fail loud, Decision 55): an unknown rung, a NULL
state field or a duplicate component (e01, e07, e08); an on_wrong or promotion_authority outside its
vocabulary (e12, e13); a review row with a NULL component (e09) or another NULL key (e04); a duplicate output_id
(e02); an outcome outside the vocabulary (e03); a duplicate failure_signal day (e05); a signal row with a
NULL component (e14) or day (e10); a drill row with a NULL component (e15), day (e16) or count (e11), or
more detected than injected (e06); a version stamp, in the state (e17) or a review (e18), that is not a
canonical non-negative integer (no sign, no leading zero), so a stamp that breaks the ordering contract
raises instead of being misordered. Each raise vector
matches its error message, not any DuckDB error. The guards are aggregates read by the final CASE, so no optimiser can skip them.

Clearance counts correct reviews at the current rung and version since the last breaker. A breaker is a
wrong or unsure review, or a selected output still unreviewed after review_sla_days. A pending review
inside the SLA neither counts nor breaks (v07, v36). An unreviewed sample therefore resets the count:
skipping the hard cases cannot buy a promotion (Goodhart).

Every output row carries to_version, the version the component holds after the decision. On a restart it
is the highest stamp above the state's seen in the stint, compared as integers (v41, v44; 9 to 10 restarts,
v47); otherwise it is the state's. Because stamps are ordered, a restart is monotonic: a late or
overlapping output from an older stamp neither restarts the component nor rolls its stamp back (v42), and
while two stamps both produce, only the current one's reviews count toward clearance (v43). The return leg
reads every stamp: a wrong output found at the current rung under any stamp in the stint demotes, so an
older stamp still producing (an overlap, or a rollback redeployed without a new stamp) fails safe toward
more review (v48). The caller writes to_version back with the rung and entered_day, so the next run at the
new version holds instead of restarting again (v40). The alternatives to integer ordering are k2's
sub-question.

```sql
WITH st AS (
  SELECT component, rung, entered_day, version, TRY_CAST(version AS UBIGINT) AS vn, signal_threshold,
         CASE rung WHEN 'read_all' THEN 0 WHEN 'sampled' THEN 1 WHEN 'spot_check' THEN 2 WHEN 'anomaly_triggered' THEN 3 END AS ri,
         count(*) OVER (PARTITION BY component) AS n_state
  FROM {state}
),
gz AS (
  SELECT (SELECT count(*) FROM {reviews} WHERE component IS NULL)
       + (SELECT count(*) FROM {signal} WHERE component IS NULL OR day IS NULL)
       + (SELECT count(*) FROM {drills} WHERE component IS NULL OR day IS NULL OR injected IS NULL OR detected IS NULL OR detected > injected) AS bad
),
g AS (
  SELECT s.component,
         count(r.component) FILTER (WHERE r.output_id IS NULL OR r.day IS NULL OR r.version IS NULL OR r.rung IS NULL) > 0 AS has_null,
         count(r.component) > count(DISTINCT r.output_id) AS dup,
         count(r.component) FILTER (WHERE r.outcome NOT IN ('correct', 'wrong', 'unsure')) > 0 AS bad_outcome,
         count(r.component) FILTER (WHERE NOT regexp_full_match(r.version, '0|[1-9][0-9]{0,17}')) > 0 AS bad_stamp
  FROM st s LEFT JOIN {reviews} r ON r.component = s.component
  GROUP BY s.component
),
gs AS (
  SELECT s.component, count(x.component) > count(DISTINCT x.day) AS dup_day
  FROM st s LEFT JOIN {signal} x ON x.component = s.component
  GROUP BY s.component
),
rv AS (
  SELECT r.component, r.output_id, r.day, r.version, TRY_CAST(r.version AS UBIGINT) AS vn, r.rung, r.outcome
  FROM {reviews} r JOIN st s ON s.component = r.component
  WHERE r.day >= s.entered_day AND r.day <= {today}
),
vc AS (
  SELECT s.component, count(rv.component) FILTER (WHERE rv.vn > s.vn) > 0 AS changed,
         arg_max(rv.version, rv.vn) FILTER (WHERE rv.vn > s.vn) AS new_version
  FROM st s LEFT JOIN rv ON rv.component = s.component
  GROUP BY s.component
),
cur AS (
  SELECT rv.*,
         row_number() OVER (PARTITION BY rv.component ORDER BY rv.day, rv.output_id) AS pos,
         (rv.outcome IN ('wrong', 'unsure') OR (rv.outcome IS NULL AND rv.day < {today} - {review_sla_days})) AS breaker
  FROM rv JOIN st s ON s.component = rv.component AND rv.rung = s.rung AND rv.version = s.version
),
wa AS (
  SELECT s.component, count(rv.component) FILTER (WHERE rv.outcome = 'wrong') AS wrong_any
  FROM st s LEFT JOIN rv ON rv.component = s.component AND rv.rung = s.rung
  GROUP BY s.component
),
lb AS (SELECT component, max(pos) FILTER (WHERE breaker) AS last_break FROM cur GROUP BY component),
cl AS (
  SELECT s.component,
         count(c.component) FILTER (WHERE c.outcome = 'correct' AND c.pos > coalesce(lb.last_break, 0)) AS clearance,
         count(c.component) FILTER (WHERE c.outcome IS NULL AND c.day < {today} - {review_sla_days}) AS overdue_n
  FROM st s LEFT JOIN cur c ON c.component = s.component LEFT JOIN lb ON lb.component = s.component
  GROUP BY s.component
),
sg AS (
  SELECT s.component,
         max(x.value) FILTER (WHERE x.day = {today}) AS v_today,
         count(x.value) FILTER (WHERE x.day > {today} - {dark_days}) AS defined_recent,
         count(x.value) FILTER (WHERE x.day > {today} - {signal_window_days} AND x.day >= s.entered_day
                                  AND x.value <= s.signal_threshold) AS clean_days
  FROM st s LEFT JOIN {signal} x ON x.component = s.component AND x.day <= {today}
  GROUP BY s.component
),
dr AS (
  SELECT s.component, coalesce(sum(d.injected), 0) AS inj, coalesce(sum(d.detected), 0) AS det
  FROM st s LEFT JOIN {drills} d ON d.component = s.component AND d.day >= s.entered_day AND d.day <= {today}
       AND d.day > {today} - {drill_window_days}
  GROUP BY s.component
),
ap AS (
  SELECT s.component, count(a.component) > 0 AS approved
  FROM st s LEFT JOIN {approvals} a ON a.component = s.component AND a.day >= s.entered_day AND a.day <= {today}
       AND a.to_rung = ['read_all', 'sampled', 'spot_check', 'anomaly_triggered', 'none'][s.ri + 2]
  GROUP BY s.component
),
d AS (
  SELECT s.component, s.ri, CASE WHEN {version_restart} AND vc.changed THEN vc.new_version ELSE s.version END AS to_version,
    CASE
      WHEN s.ri IS NULL OR s.entered_day IS NULL OR s.version IS NULL OR s.signal_threshold IS NULL OR s.n_state > 1
      THEN error('malformed ladder state: unknown rung, NULL field or duplicate component')
      WHEN '{on_wrong}' NOT IN ('read_all', 'one_down', 'none') OR '{promotion_authority}' NOT IN ('operator', 'auto')
      THEN error('malformed ladder params: on_wrong or promotion_authority outside its vocabulary')
      WHEN gz.bad > 0 THEN error('malformed ladder input: NULL key or detected > injected')
      WHEN g.has_null THEN error('malformed review row: NULL output_id, day, version or rung')
      WHEN NOT regexp_full_match(s.version, '0|[1-9][0-9]{0,17}') OR g.bad_stamp
      THEN error('malformed version stamp: not a canonical non-negative integer')
      WHEN g.dup THEN error('duplicate output_id: one review row per output')
      WHEN g.bad_outcome THEN error('review outcome outside correct, wrong, unsure')
      WHEN gs.dup_day THEN error('duplicate failure_signal day: one value per component per day')
      WHEN {version_restart} AND vc.changed THEN 'restart:version_change'
      WHEN s.ri > 0 AND '{on_wrong}' <> 'none' AND wa.wrong_any > 0 THEN 'demote:wrong_found'
      WHEN s.ri > 0 AND {signal_demotes} AND sg.v_today > s.signal_threshold THEN 'demote:signal_breach'
      WHEN s.ri > 0 AND {signal_demotes} AND sg.defined_recent = 0 THEN 'demote:signal_dark'
      WHEN s.ri = 3 THEN 'hold:top'
      WHEN cl.overdue_n > 0 THEN 'hold:review_overdue'
      WHEN cl.clearance < [{clear_read_all}, {clear_sampled}, {clear_spot_check}][s.ri + 1] THEN 'hold:clearing'
      WHEN {signal_gates_promotion} AND (sg.v_today IS NULL OR sg.v_today > s.signal_threshold) THEN 'hold:signal_not_clean'
      WHEN s.ri = 2 AND sg.clean_days < {signal_window_days} THEN 'hold:signal_window'
      WHEN s.ri = 2 AND {drill_min} > 0 AND (dr.inj < {drill_min} OR dr.det < {drill_recall_min} * dr.inj) THEN 'hold:awaiting_drill'
      WHEN '{promotion_authority}' = 'auto' OR ap.approved THEN 'promote:cleared'
      ELSE 'propose_promote:cleared'
    END AS ar
  FROM st s
  CROSS JOIN gz
  JOIN g ON g.component = s.component
  JOIN gs ON gs.component = s.component
  JOIN vc ON vc.component = s.component
  JOIN cl ON cl.component = s.component
  JOIN wa ON wa.component = s.component
  JOIN sg ON sg.component = s.component
  JOIN dr ON dr.component = s.component
  JOIN ap ON ap.component = s.component
)
SELECT DISTINCT component,
       split_part(ar, ':', 1) AS action,
       CASE split_part(ar, ':', 1)
         WHEN 'restart' THEN 'read_all'
         WHEN 'demote' THEN CASE WHEN ar = 'demote:wrong_found' AND '{on_wrong}' = 'read_all' THEN 'read_all'
                                 ELSE ['read_all', 'sampled', 'spot_check'][ri] END
         WHEN 'hold' THEN ['read_all', 'sampled', 'spot_check', 'anomaly_triggered'][ri + 1]
         ELSE ['sampled', 'spot_check', 'anomaly_triggered'][ri + 1]
       END AS to_rung,
       split_part(ar, ':', 2) AS reason,
       to_version
FROM d
```

| action | reason | when (precedence top-down) | vectors |
|---|---|---|---|
| restart | version_change | a review in the stint carries a stamp numerically above the state's, when version_restart (k2); to_version is the highest such stamp | v18, v19, v25, v41, v44, v47 |
| demote | wrong_found | a wrong review at the current rung above read_all, under any stamp in the stint; to read_all or one rung down per on_wrong (k1); never at read_all | v08, v09, v26, v30, v31, v48 |
| demote | signal_breach | above read_all, today's failure_signal exceeds the threshold (strictly), when signal_demotes; one rung down (k7) | v11 |
| demote | signal_dark | above read_all, no defined failure_signal value in the last dark_days days; one rung down (k7; no traffic reads as dark) | v12, v32 |
| hold | top | at anomaly_triggered with nothing to demote | v13 |
| hold | review_overdue | a selected output in the stint is past the SLA unreviewed | v06 |
| hold | clearing | clearance below the rung's clear_* | v03, v04, v05, v29, v36 |
| hold | signal_not_clean | today's failure_signal undefined or above threshold, when signal_gates_promotion | v20, v21, v22 |
| hold | signal_window | at spot_check, fewer than signal_window_days clean days inside the stint | v15 |
| hold | awaiting_drill | at spot_check, when drill_min > 0: fewer than drill_min faults injected in the last drill_window_days of the stint, or fewer than drill_recall_min of them detected | v16, v17 |
| promote | cleared | an approval for the next rung dated inside the stint, or promotion_authority auto (k3) | v02, v27 |
| propose_promote | cleared | otherwise | v01, v07, v10, v14, v23, v24, v28, v33, v34, v35, v37, v38, v39, v40 |

Rows outside the stint (before entered_day, after today) and rows recorded at another rung are ignored:
reviews (v28, v29, v37, v46, v52), drills (v49, v50) and failure_signal days (v51). Several components decide independently in one run (v30).

### 2.4 Vectors (VP 4)

`reviews` rows expand to n outputs on one day; `signal` rows cover a day range with one value.
`expected: error` vectors carry the message substring they must raise.

```yaml
vectors:
  - {id: v01, today: 50, state: [[a, read_all, 10, '1', 0.05]], reviews: [[a, 20, 40, '1', read_all, correct]], signal: [[a, 49, 50, 0.0]], expected: [[a, propose_promote, sampled, cleared, '1']]}
  - {id: v02, today: 50, state: [[a, read_all, 10, '1', 0.05]], reviews: [[a, 20, 40, '1', read_all, correct]], signal: [[a, 49, 50, 0.0]], approvals: [[a, sampled, 50]], expected: [[a, promote, sampled, cleared, '1']]}
  - {id: v03, today: 50, state: [[a, read_all, 10, '1', 0.05]], reviews: [[a, 20, 39, '1', read_all, correct]], signal: [[a, 49, 50, 0.0]], expected: [[a, hold, read_all, clearing, '1']]}
  - {id: v04, today: 50, state: [[a, read_all, 10, '1', 0.05]], reviews: [[a, 20, 20, '1', read_all, correct], [a, 21, 1, '1', read_all, wrong], [a, 22, 39, '1', read_all, correct]], signal: [[a, 49, 50, 0.0]], expected: [[a, hold, read_all, clearing, '1']]}
  - {id: v05, today: 50, state: [[a, read_all, 10, '1', 0.05]], reviews: [[a, 20, 40, '1', read_all, correct], [a, 21, 1, '1', read_all, unsure]], signal: [[a, 49, 50, 0.0]], expected: [[a, hold, read_all, clearing, '1']]}
  - {id: v06, today: 50, state: [[a, read_all, 10, '1', 0.05]], reviews: [[a, 20, 40, '1', read_all, correct], [a, 46, 1, '1', read_all, null]], signal: [[a, 49, 50, 0.0]], expected: [[a, hold, read_all, review_overdue, '1']]}
  - {id: v07, today: 50, state: [[a, read_all, 10, '1', 0.05]], reviews: [[a, 20, 40, '1', read_all, correct], [a, 47, 1, '1', read_all, null]], signal: [[a, 49, 50, 0.0]], expected: [[a, propose_promote, sampled, cleared, '1']]}
  - {id: v08, today: 50, state: [[a, spot_check, 10, '1', 0.05]], reviews: [[a, 20, 30, '1', spot_check, correct], [a, 21, 1, '1', spot_check, wrong]], signal: [[a, 10, 50, 0.0]], expected: [[a, demote, read_all, wrong_found, '1']]}
  - {id: v09, today: 50, params: {on_wrong: one_down}, state: [[a, spot_check, 10, '1', 0.05]], reviews: [[a, 20, 30, '1', spot_check, correct], [a, 21, 1, '1', spot_check, wrong]], signal: [[a, 10, 50, 0.0]], expected: [[a, demote, sampled, wrong_found, '1']]}
  - {id: v10, today: 50, params: {on_wrong: none}, state: [[a, sampled, 10, '1', 0.05]], reviews: [[a, 20, 1, '1', sampled, wrong], [a, 21, 40, '1', sampled, correct]], signal: [[a, 49, 50, 0.0]], expected: [[a, propose_promote, spot_check, cleared, '1']]}
  - {id: v11, today: 50, state: [[a, spot_check, 10, '1', 0.05]], reviews: [[a, 20, 30, '1', spot_check, correct]], signal: [[a, 10, 49, 0.0], [a, 50, 50, 0.08]], expected: [[a, demote, sampled, signal_breach, '1']]}
  - {id: v12, today: 50, state: [[a, anomaly_triggered, 10, '1', 0.05]], signal: [[a, 10, 48, 0.0]], expected: [[a, demote, spot_check, signal_dark, '1']]}
  - {id: v13, today: 50, state: [[a, anomaly_triggered, 10, '1', 0.05]], reviews: [[a, 30, 2, '1', anomaly_triggered, correct]], signal: [[a, 10, 50, 0.01]], expected: [[a, hold, anomaly_triggered, top, '1']]}
  - {id: v14, today: 50, state: [[a, spot_check, 10, '1', 0.05]], reviews: [[a, 20, 20, '1', spot_check, correct]], signal: [[a, 10, 50, 0.01]], drills: [[a, 45, 20, 20]], expected: [[a, propose_promote, anomaly_triggered, cleared, '1']]}
  - {id: v15, today: 50, state: [[a, spot_check, 22, '1', 0.05]], reviews: [[a, 30, 20, '1', spot_check, correct]], signal: [[a, 1, 50, 0.01]], drills: [[a, 45, 20, 20]], expected: [[a, hold, spot_check, signal_window, '1']]}
  - {id: v16, today: 50, state: [[a, spot_check, 10, '1', 0.05]], reviews: [[a, 20, 20, '1', spot_check, correct]], signal: [[a, 10, 50, 0.01]], drills: [[a, 45, 20, 17]], expected: [[a, hold, spot_check, awaiting_drill, '1']]}
  - {id: v17, today: 50, state: [[a, spot_check, 10, '1', 0.05]], reviews: [[a, 20, 20, '1', spot_check, correct]], signal: [[a, 10, 50, 0.01]], drills: [[a, 45, 19, 19]], expected: [[a, hold, spot_check, awaiting_drill, '1']]}
  - {id: v18, today: 50, state: [[a, sampled, 10, '1', 0.05]], reviews: [[a, 20, 30, '1', sampled, correct], [a, 40, 1, '2', sampled, correct]], signal: [[a, 10, 50, 0.0]], expected: [[a, restart, read_all, version_change, '2']]}
  - {id: v19, today: 50, state: [[a, read_all, 10, '1', 0.05]], reviews: [[a, 20, 40, '1', read_all, correct], [a, 41, 1, '2', read_all, correct]], signal: [[a, 49, 50, 0.0]], expected: [[a, restart, read_all, version_change, '2']]}
  - {id: v20, today: 50, state: [[a, read_all, 10, '1', 0.05]], reviews: [[a, 20, 40, '1', read_all, correct]], signal: [[a, 40, 49, 0.0]], expected: [[a, hold, read_all, signal_not_clean, '1']]}
  - {id: v21, today: 50, state: [[a, read_all, 10, '1', 0.05]], reviews: [[a, 20, 40, '1', read_all, correct]], signal: [[a, 40, 50, 0.2]], expected: [[a, hold, read_all, signal_not_clean, '1']]}
  - {id: v22, today: 50, params: {signal_demotes: false}, state: [[a, sampled, 10, '1', 0.05]], reviews: [[a, 20, 40, '1', sampled, correct]], signal: [[a, 40, 50, 0.2]], expected: [[a, hold, sampled, signal_not_clean, '1']]}
  - {id: v23, today: 50, state: [[a, read_all, 10, '1', 0.05]], reviews: [[a, 20, 40, '1', read_all, correct]], signal: [[a, 49, 50, 0.0]], approvals: [[a, spot_check, 50]], expected: [[a, propose_promote, sampled, cleared, '1']]}
  - {id: v24, today: 50, state: [[a, read_all, 10, '1', 0.05]], reviews: [[a, 20, 40, '1', read_all, correct]], signal: [[a, 49, 50, 0.0]], approvals: [[a, sampled, 9]], expected: [[a, propose_promote, sampled, cleared, '1']]}
  - {id: v25, today: 50, state: [[a, sampled, 10, '1', 0.05]], reviews: [[a, 20, 1, '1', sampled, wrong], [a, 40, 1, '2', sampled, correct]], signal: [[a, 10, 50, 0.0]], expected: [[a, restart, read_all, version_change, '2']]}
  - {id: v26, today: 50, params: {on_wrong: one_down}, state: [[a, spot_check, 10, '1', 0.05]], reviews: [[a, 20, 1, '1', spot_check, wrong]], signal: [[a, 10, 49, 0.0], [a, 50, 50, 0.3]], expected: [[a, demote, sampled, wrong_found, '1']]}
  - {id: v27, today: 50, params: {promotion_authority: auto}, state: [[a, read_all, 10, '1', 0.05]], reviews: [[a, 20, 40, '1', read_all, correct]], signal: [[a, 49, 50, 0.0]], expected: [[a, promote, sampled, cleared, '1']]}
  - {id: v28, today: 50, state: [[a, sampled, 30, '1', 0.05]], reviews: [[a, 20, 3, '1', read_all, wrong], [a, 25, 1, '0', read_all, correct], [a, 31, 40, '1', sampled, correct]], signal: [[a, 30, 50, 0.0]], expected: [[a, propose_promote, spot_check, cleared, '1']]}
  - {id: v29, today: 50, state: [[a, read_all, 10, '1', 0.05]], reviews: [[a, 20, 39, '1', read_all, correct], [a, 51, 5, '1', read_all, correct], [a, 52, 1, '2', read_all, wrong]], signal: [[a, 49, 50, 0.0]], expected: [[a, hold, read_all, clearing, '1']]}
  - {id: v30, today: 50, state: [[a, read_all, 10, '1', 0.05], [b, sampled, 10, '7', 0.1]], reviews: [[a, 20, 40, '1', read_all, correct], [b, 20, 5, '7', sampled, correct], [b, 21, 1, '7', sampled, wrong]], signal: [[a, 49, 50, 0.0], [b, 49, 50, 0.0]], expected: [[a, propose_promote, sampled, cleared, '1'], [b, demote, read_all, wrong_found, '7']]}
  - {id: v31, today: 50, state: [[a, anomaly_triggered, 10, '1', 0.05]], reviews: [[a, 30, 1, '1', anomaly_triggered, wrong]], signal: [[a, 10, 50, 0.0]], expected: [[a, demote, read_all, wrong_found, '1']]}
  - {id: v32, today: 50, state: [[a, anomaly_triggered, 10, '1', 0.05]], signal: [[a, 10, 48, 0.0], [a, 49, 50, null]], expected: [[a, demote, spot_check, signal_dark, '1']]}
  - {id: v33, today: 50, state: [[a, sampled, 10, '1', 0.05]], reviews: [[a, 20, 40, '1', sampled, correct]], signal: [[a, 10, 50, 0.05]], expected: [[a, propose_promote, spot_check, cleared, '1']]}
  - {id: v34, today: 50, params: {signal_gates_promotion: false}, state: [[a, read_all, 10, '1', 0.05]], reviews: [[a, 20, 40, '1', read_all, correct]], signal: [[a, 40, 50, 0.2]], expected: [[a, propose_promote, sampled, cleared, '1']]}
  - {id: v35, today: 50, params: {drill_min: 0}, state: [[a, spot_check, 10, '1', 0.05]], reviews: [[a, 20, 20, '1', spot_check, correct]], signal: [[a, 10, 50, 0.01]], drills: [[a, 45, 4, 1]], expected: [[a, propose_promote, anomaly_triggered, cleared, '1']]}
  - {id: v36, today: 50, state: [[a, read_all, 10, '1', 0.05]], reviews: [[a, 20, 39, '1', read_all, correct], [a, 48, 1, '1', read_all, null]], signal: [[a, 49, 50, 0.0]], expected: [[a, hold, read_all, clearing, '1']]}
  - {id: v37, today: 50, state: [[a, sampled, 10, '1', 0.05]], reviews: [[a, 12, 1, '1', read_all, wrong], [a, 20, 40, '1', sampled, correct]], signal: [[a, 10, 50, 0.0]], expected: [[a, propose_promote, spot_check, cleared, '1']]}
  - {id: v38, today: 50, state: [[a, spot_check, 10, '1', 0.05]], reviews: [[a, 20, 20, '1', spot_check, correct]], signal: [[a, 10, 50, 0.01]], drills: [[a, 45, 20, 18]], expected: [[a, propose_promote, anomaly_triggered, cleared, '1']]}
  - {id: v39, today: 50, state: [[a, spot_check, 10, '1', 0.05]], reviews: [[a, 20, 20, '1', spot_check, correct]], signal: [[a, 10, 50, 0.01]], drills: [[a, 20, 5, 0], [a, 45, 20, 20]], expected: [[a, propose_promote, anomaly_triggered, cleared, '1']]}
  - {id: v40, today: 60, state: [[a, read_all, 51, '2', 0.05]], reviews: [[a, 40, 3, '1', read_all, correct], [a, 52, 40, '2', read_all, correct]], signal: [[a, 50, 60, 0.0]], expected: [[a, propose_promote, sampled, cleared, '2']]}
  - {id: v41, today: 50, state: [[a, sampled, 10, '1', 0.05]], reviews: [[a, 20, 30, '1', sampled, correct], [a, 30, 1, '2', sampled, correct], [a, 40, 1, '3', sampled, correct]], signal: [[a, 10, 50, 0.0]], expected: [[a, restart, read_all, version_change, '3']]}
  - {id: v42, today: 60, state: [[a, read_all, 51, '2', 0.05]], reviews: [[a, 52, 40, '2', read_all, correct], [a, 53, 1, '1', read_all, correct]], signal: [[a, 50, 60, 0.0]], expected: [[a, propose_promote, sampled, cleared, '2']]}
  - {id: v43, today: 60, state: [[a, read_all, 51, '2', 0.05]], reviews: [[a, 52, 1, '1', read_all, correct], [a, 52, 20, '2', read_all, correct], [a, 55, 1, '1', read_all, wrong], [a, 55, 20, '2', read_all, correct]], signal: [[a, 50, 60, 0.0]], expected: [[a, propose_promote, sampled, cleared, '2']]}
  - {id: v44, today: 50, state: [[a, sampled, 10, '1', 0.05]], reviews: [[a, 20, 30, '1', sampled, correct], [a, 40, 1, '3', sampled, correct], [a, 40, 1, '2', sampled, correct]], signal: [[a, 10, 50, 0.0]], expected: [[a, restart, read_all, version_change, '3']]}
  - {id: v45, today: 45, state: [[a, spot_check, 10, '1', 0.05]], reviews: [[a, 20, 20, '1', spot_check, correct]], signal: [[a, 10, 45, 0.01]], drills: [[a, 25, 1, 1], [a, 26, 19, 19]], expected: [[a, hold, spot_check, awaiting_drill, '1']]}
  - {id: v46, today: 50, state: [[a, sampled, 30, '1', 0.05]], reviews: [[a, 20, 1, '1', sampled, wrong], [a, 31, 40, '1', sampled, correct]], signal: [[a, 30, 50, 0.0]], expected: [[a, propose_promote, spot_check, cleared, '1']]}
  - {id: v47, today: 50, state: [[a, anomaly_triggered, 10, '9', 0.05]], reviews: [[a, 20, 3, '10', anomaly_triggered, wrong]], signal: [[a, 10, 50, 0.0]], expected: [[a, restart, read_all, version_change, '10']]}
  - {id: v48, today: 50, state: [[a, anomaly_triggered, 30, '2', 0.05]], reviews: [[a, 40, 1, '1', anomaly_triggered, wrong], [a, 41, 5, '2', anomaly_triggered, correct]], signal: [[a, 10, 50, 0.0]], expected: [[a, demote, read_all, wrong_found, '2']]}
  - {id: v49, today: 55, params: {signal_window_days: 10}, state: [[a, spot_check, 40, '1', 0.05]], reviews: [[a, 41, 20, '1', spot_check, correct]], signal: [[a, 30, 55, 0.01]], drills: [[a, 38, 20, 20]], expected: [[a, hold, spot_check, awaiting_drill, '1']]}
  - {id: v50, today: 55, params: {signal_window_days: 10}, state: [[a, spot_check, 40, '1', 0.05]], reviews: [[a, 41, 20, '1', spot_check, correct]], signal: [[a, 30, 55, 0.01]], drills: [[a, 56, 20, 20]], expected: [[a, hold, spot_check, awaiting_drill, '1']]}
  - {id: v51, today: 50, state: [[a, spot_check, 25, '1', 0.05]], reviews: [[a, 30, 20, '1', spot_check, correct]], signal: [[a, 25, 60, 0.01]], drills: [[a, 45, 20, 20]], expected: [[a, hold, spot_check, signal_window, '1']]}
  - {id: v52, today: 50, state: [[a, sampled, 10, '1', 0.05]], reviews: [[a, 20, 40, '1', sampled, correct], [a, 45, 1, '1', read_all, wrong]], signal: [[a, 10, 50, 0.0]], expected: [[a, propose_promote, spot_check, cleared, '1']]}
  - {id: v53, today: 50, params: {version_restart: false}, state: [[a, read_all, 10, '1', 0.05]], reviews: [[a, 20, 40, '2', read_all, correct]], signal: [[a, 49, 50, 0.0]], expected: [[a, hold, read_all, clearing, '1']]}
  - {id: v54, today: 50, state: [[a, sampled, 10, '1', 0.05]], reviews: [[a, 20, 40, '1', sampled, correct], [a, 45, 1, '1', sampled, unsure]], signal: [[a, 10, 50, 0.0]], expected: [[a, hold, sampled, clearing, '1']]}
  - {id: v55, today: 50, state: [[a, sampled, 10, '8', 0.05]], reviews: [[a, 20, 30, '8', sampled, correct], [a, 40, 1, '9', sampled, correct], [a, 41, 1, '10', sampled, correct]], signal: [[a, 10, 50, 0.0]], expected: [[a, restart, read_all, version_change, '10']]}
  - {id: v56, today: 50, state: [[a, sampled, 10, '3000000000', 0.05]], reviews: [[a, 20, 30, '3000000000', sampled, correct], [a, 40, 1, '3000000001', sampled, correct]], signal: [[a, 10, 50, 0.0]], expected: [[a, restart, read_all, version_change, '3000000001']]}
  - {id: e01, today: 50, raises: 'malformed ladder state', state: [[a, audit, 10, '1', 0.05]], expected: error}
  - {id: e02, today: 50, raises: 'duplicate output_id', dup_review: true, state: [[a, read_all, 10, '1', 0.05]], reviews: [[a, 20, 2, '1', read_all, correct]], signal: [[a, 49, 50, 0.0]], expected: error}
  - {id: e03, today: 50, raises: 'review outcome outside', state: [[a, read_all, 10, '1', 0.05]], reviews: [[a, 20, 1, '1', read_all, maybe]], signal: [[a, 49, 50, 0.0]], expected: error}
  - {id: e04, today: 50, raises: 'malformed review row', state: [[a, read_all, 10, '1', 0.05]], reviews: [[a, 20, 1, null, read_all, correct]], signal: [[a, 49, 50, 0.0]], expected: error}
  - {id: e05, today: 50, raises: 'duplicate failure_signal day', state: [[a, read_all, 10, '1', 0.05]], signal: [[a, 49, 50, 0.0], [a, 50, 50, 0.0]], expected: error}
  - {id: e06, today: 50, raises: 'detected > injected', state: [[a, spot_check, 10, '1', 0.05]], signal: [[a, 49, 50, 0.0]], drills: [[a, 30, 2, 3]], expected: error}
  - {id: e07, today: 50, raises: 'malformed ladder state', state: [[a, read_all, 10, '1', 0.05], [a, sampled, 10, '1', 0.05]], expected: error}
  - {id: e08, today: 50, raises: 'malformed ladder state', state: [[a, read_all, null, '1', 0.05]], signal: [[a, 49, 50, 0.0]], expected: error}
  - {id: e09, today: 50, raises: 'malformed ladder input', state: [[a, read_all, 10, '1', 0.05]], reviews: [[null, 20, 1, '1', read_all, correct]], signal: [[a, 49, 50, 0.0]], expected: error}
  - {id: e10, today: 50, raises: 'malformed ladder input', state: [[a, read_all, 10, '1', 0.05]], signal: [[a, 49, 50, 0.0]], extra_signal: [[a, null, 0.0]], expected: error}
  - {id: e11, today: 50, raises: 'malformed ladder input', state: [[a, spot_check, 10, '1', 0.05]], signal: [[a, 49, 50, 0.0]], drills: [[a, 45, null, 0]], expected: error}
  - {id: e12, today: 50, raises: 'malformed ladder params', params: {on_wrong: readall}, state: [[a, read_all, 10, '1', 0.05]], signal: [[a, 49, 50, 0.0]], expected: error}
  - {id: e13, today: 50, raises: 'malformed ladder params', params: {promotion_authority: Operator}, state: [[a, read_all, 10, '1', 0.05]], signal: [[a, 49, 50, 0.0]], expected: error}
  - {id: e14, today: 50, raises: 'malformed ladder input', state: [[a, read_all, 10, '1', 0.05]], signal: [[a, 49, 50, 0.0]], extra_signal: [[null, 50, 0.0]], expected: error}
  - {id: e15, today: 50, raises: 'malformed ladder input', state: [[a, spot_check, 10, '1', 0.05]], signal: [[a, 49, 50, 0.0]], drills: [[null, 45, 1, 0]], expected: error}
  - {id: e16, today: 50, raises: 'malformed ladder input', state: [[a, spot_check, 10, '1', 0.05]], signal: [[a, 49, 50, 0.0]], drills: [[a, null, 1, 0]], expected: error}
  - {id: e17, today: 50, raises: 'malformed version stamp', state: [[a, read_all, 10, v2, 0.05]], signal: [[a, 49, 50, 0.0]], expected: error}
  - {id: e18, today: 50, raises: 'malformed version stamp', state: [[a, read_all, 10, '1', 0.05]], reviews: [[a, 20, 1, '010', read_all, correct]], signal: [[a, 49, 50, 0.0]], expected: error}
```

### 2.5 Mutants (hand-run once, reported, not a VP step)

Each mutant is one textual edit of the SQL above; every one fails at least one vector:

| mutant | edit | killed by |
|---|---|---|
| m01 | on_wrong ignored (always demote on a wrong review) | v10 |
| m02 | a pending review counts toward clearance | v36 |
| m03 | a pending review inside the SLA breaks clearance | v07 |
| m04 | SLA boundary < becomes <= | v07 |
| m05 | unsure is not a breaker | v05, v54 |
| m06 | rows before entered_day read | v46 |
| m07 | rows after today read | v29 |
| m08 | wrong_found checked before version_change | v25, v47 |
| m09 | a demotion targets the current rung | v09, v11, v12, v26, v32 |
| m10 | dark window boundary > becomes >= | v12, v32 |
| m11 | a NULL signal value counts as defined | v32 |
| m12 | signal window counts days before the stint | v15 |
| m13 | an approval for any rung promotes | v23 |
| m14 | an approval from before the stint promotes | v24 |
| m15 | the drill recall floor ignored (any injection count passes) | v16 |
| m16 | the drill applies when drill_min is 0 | v35 |
| m17 | duplicate output_id guard removed | e02 |
| m18 | outcome vocabulary guard removed | e03 |
| m19 | NULL review key guard removed | e04 |
| m20 | duplicate signal day guard removed | e05 |
| m21 | detected > injected guard removed | e06 |
| m22 | duplicate component guard removed | e07 |
| m23 | breach comparator > becomes >= | v33 |
| m24 | signal_demotes ignored | v22 |
| m25 | signal_gates_promotion ignored | v34 |
| m26 | a wrong review demotes at read_all | v04, v43 |
| m27 | rows recorded at another rung count | v52 |
| m28 | hold top checked before the demotions | v12, v31, v32, v47, v48 |
| m29 | promotion_authority auto ignored | v27 |
| m30 | one clearance (clear_read_all) for every rung | v14, v15, v16, v17, v35, v38, v39, v45, v49, v50, v51 |
| m31 | signal window check removed | v15, v51 |
| m32 | signal_dark demotion removed | v12, v32 |
| m33 | the restart version not returned (to_version stays current) | v18, v19, v25, v41, v44, v47, v55, v56 |
| m34 | the drill window ignored | v39, v45 |
| m35 | zero-miss drill instead of the recall floor | v38 |
| m36 | on_wrong and promotion_authority vocabulary guard removed | e12, e13 |
| m37 | NULL component review guard removed | e09 |
| m38 | NULL signal day guard removed | e10 |
| m39 | NULL drill count guard removed | e11 |
| m40 | the lowest stamp above the state's returned on a restart | v41, v44, v55 |
| m41 | promotion_authority vocabulary guard leg removed | e13 |
| m42 | restart on any other stamp (rollback and overlap restart) | v42, v43, v48 |
| m43 | NULL component signal guard removed | e14 |
| m44 | NULL component drill guard removed | e15 |
| m45 | NULL drill day guard removed | e16 |
| m46 | drill window boundary > becomes >= | v45 |
| m47 | stamps compared as text (9 to 10 does not restart) | v47, v55 |
| m48 | only current-stamp wrong outputs trigger the return leg | v48 |
| m49 | state stamp format guard removed | e17 |
| m50 | review stamp format guard removed | e18 |
| m51 | drills before entered_day count | v49 |
| m52 | drills after today count | v50 |
| m53 | failure_signal days after today read | v51 |
| m54 | wrong outputs recorded at another rung trigger the return leg | v37, v52 |
| m55 | version_restart off ignored (restart regardless) | v53 |
| m56 | an unsure review triggers the return leg | v54 |

### 2.6 Simulation (VP 5)

One year, 12 outputs a day per component (the order of magnitude #1397 measured for sessions), 20
replicate components per (error rate p, monitor recall r) cell on each of three seeds, so 60 per cell. The
error rates are 0.005, 0.01, 0.02, 0.03, 0.035, 0.04, 0.045, 0.05, 0.1 and 0.2; the band 0.02-0.05 is where
a multi-level plan's outgoing quality peaks. Monitor recall is 1.0, 0.9 or 0.5. The failure_signal is the 7-day share of outputs that are wrong and seen by the monitor; threshold
0.05. A drill injects one fault per spot_check day, detected with probability r. The reviewer is perfect
and immediate, errors are independent at a constant p, and every proposal is approved (the most permissive
operator). Draws are md5 over the seed, the component, the day and the output, so the result is identical
on every run; the seed only re-draws, so seed-to-seed spread is sampling noise, not a rule change. Both rules use the staged review fractions, because no declared item states one (e1).

The `declared` rule is the controller with the declared shape: clearance 20 then 50, a 30-day clean signal
window to the top, no return leg, no signal demotion, no version restart, no drill. The simulation never
changes a version, so k2 is exercised by vectors only. `staged` is the params
block. Each simulated day runs the controller SQL above, unchanged, over every component.

```yaml
simulation:
  seeds: [w1-c7, w1-c7-b, w1-c7-c]
  days: 365
  n_per_day: 12
  signal_days: 7
  signal_threshold: 0.05
  reps: 20
  p: [0.005, 0.01, 0.02, 0.03, 0.035, 0.04, 0.045, 0.05, 0.1, 0.2]
  recall: [1.0, 0.9, 0.5]
  rules:
    declared: {clear_read_all: 20, clear_sampled: 50, clear_spot_check: 0, on_wrong: none, signal_demotes: false, signal_gates_promotion: false, version_restart: false, drill_min: 0, promotion_authority: auto}
    staged: {promotion_authority: auto}
```

```sql
CREATE TABLE sim_state AS
  SELECT sd || '/c:' || p || ':' || r || ':' || i AS component, 'c:' || p || ':' || r || ':' || i AS draw_key, sd AS seed,
         'read_all' AS rung, CAST(1 AS BIGINT) AS entered_day, '1' AS version,
         CAST({signal_threshold} AS DOUBLE) AS signal_threshold, p, r AS recall
  FROM (SELECT unnest({seeds}) AS sd) CROSS JOIN (SELECT unnest({p}) AS p) CROSS JOIN (SELECT unnest({recall}) AS r)
       CROSS JOIN range({reps}) t(i);
CREATE TABLE sim_out (component VARCHAR, day BIGINT, k BIGINT, wrong BOOLEAN, selected BOOLEAN, seen BOOLEAN, rung VARCHAR);
CREATE TABLE sim_rv (component VARCHAR, output_id VARCHAR, day BIGINT, version VARCHAR, rung VARCHAR, outcome VARCHAR);
CREATE TABLE sim_sg (component VARCHAR, day BIGINT, value DOUBLE);
CREATE TABLE sim_dr (component VARCHAR, day BIGINT, injected INT, detected INT);
CREATE TABLE sim_ap (component VARCHAR, to_rung VARCHAR, day BIGINT)
```

```sql
INSERT INTO sim_out
  SELECT component, {day}, k, u_w < p, rung = 'read_all' OR u_s < CASE rung WHEN 'sampled' THEN {f_sampled}
         WHEN 'spot_check' THEN {f_spot_check} ELSE {f_anomaly_triggered} END, u_d < recall, rung
  FROM (SELECT s.*, t.k,
               CAST('0x' || substr(md5(concat_ws(':', s.seed, s.draw_key, {day}, t.k, 'w')), 1, 8) AS UBIGINT) / 4294967296.0 AS u_w,
               CAST('0x' || substr(md5(concat_ws(':', s.seed, s.draw_key, {day}, t.k, 's')), 1, 8) AS UBIGINT) / 4294967296.0 AS u_s,
               CAST('0x' || substr(md5(concat_ws(':', s.seed, s.draw_key, {day}, t.k, 'd')), 1, 8) AS UBIGINT) / 4294967296.0 AS u_d
        FROM sim_state s CROSS JOIN range({n_per_day}) t(k));
INSERT INTO sim_rv
  SELECT component, component || ':' || day || ':' || k, day, '1', rung, CASE WHEN wrong THEN 'wrong' ELSE 'correct' END
  FROM sim_out WHERE day = {day} AND selected;
INSERT INTO sim_sg
  SELECT component, {day}, count(*) FILTER (WHERE wrong AND seen) / count(*)
  FROM sim_out WHERE day > {day} - {signal_days} AND day <= {day} GROUP BY component;
INSERT INTO sim_dr
  SELECT component, {day}, 1,
         CASE WHEN CAST('0x' || substr(md5(concat_ws(':', seed, draw_key, {day}, 'drill')), 1, 8) AS UBIGINT) / 4294967296.0 < recall
              THEN 1 ELSE 0 END
  FROM sim_state WHERE rung = 'spot_check';
CREATE OR REPLACE TABLE sim_act AS {controller};
UPDATE sim_state SET rung = a.to_rung, entered_day = {day} + 1, version = a.to_version
  FROM sim_act a WHERE a.component = sim_state.component AND a.action IN ('promote', 'demote', 'restart');
DELETE FROM sim_rv USING sim_state s WHERE sim_rv.component = s.component AND sim_rv.day < s.entered_day
```

```sql
WITH per_seed AS (
  SELECT s.seed, s.p, s.recall, count(*) FILTER (WHERE o.wrong AND NOT o.selected) / count(*) AS aoq
  FROM sim_out o JOIN sim_state s USING (component)
  GROUP BY s.seed, s.p, s.recall
)
SELECT s.p, s.recall,
       count(*) FILTER (WHERE o.wrong AND NOT o.selected) / count(*) AS aoq,
       (SELECT max(x.aoq) FROM per_seed x WHERE x.p = s.p AND x.recall = s.recall) AS aoq_worst_seed,
       count(*) FILTER (WHERE o.selected) / count(*) AS afi,
       count(*) FILTER (WHERE o.rung = 'anomaly_triggered') / count(*) AS top_share,
       count(DISTINCT o.component) FILTER (WHERE o.rung = 'anomaly_triggered') AS reached_top_n,
       count(DISTINCT o.component) AS components
FROM sim_out o JOIN sim_state s USING (component)
GROUP BY s.p, s.recall ORDER BY s.p, s.recall
```

Result (VP 5; aoq is wrong outputs that escaped review per output, over all 60 components, and worst is
the highest single-seed aoq; afi the share reviewed; top the components of 60 that ever reached
anomaly_triggered):

| p | recall | declared aoq | declared worst | declared afi | declared top | staged aoq | staged worst | staged afi | staged top |
|---|---|---|---|---|---|---|---|---|---|
| 0.005 | 0.5 | 0.0049 | 0.0050 | 0.030 | 60 | 0.0046 | 0.0048 | 0.081 | 1 |
| 0.005 | 0.9 | 0.0048 | 0.0049 | 0.029 | 60 | 0.0047 | 0.0049 | 0.042 | 60 |
| 0.005 | 1.0 | 0.0049 | 0.0053 | 0.030 | 60 | 0.0049 | 0.0052 | 0.043 | 60 |
| 0.01 | 0.5 | 0.0098 | 0.0100 | 0.030 | 60 | 0.0091 | 0.0093 | 0.108 | 1 |
| 0.01 | 0.9 | 0.0096 | 0.0097 | 0.032 | 60 | 0.0092 | 0.0094 | 0.071 | 60 |
| 0.01 | 1.0 | 0.0096 | 0.0100 | 0.032 | 60 | 0.0093 | 0.0097 | 0.064 | 60 |
| 0.02 | 0.5 | 0.0188 | 0.0193 | 0.039 | 60 | 0.0155 | 0.0166 | 0.209 | 0 |
| 0.02 | 0.9 | 0.0193 | 0.0203 | 0.037 | 60 | 0.0161 | 0.0166 | 0.197 | 57 |
| 0.02 | 1.0 | 0.0192 | 0.0195 | 0.037 | 60 | 0.0157 | 0.0159 | 0.213 | 58 |
| 0.03 | 0.5 | 0.0284 | 0.0289 | 0.050 | 60 | 0.0193 | 0.0203 | 0.357 | 0 |
| 0.03 | 0.9 | 0.0284 | 0.0286 | 0.042 | 60 | 0.0173 | 0.0185 | 0.413 | 37 |
| 0.03 | 1.0 | 0.0286 | 0.0291 | 0.051 | 60 | 0.0159 | 0.0164 | 0.470 | 23 |
| 0.035 | 0.5 | 0.0337 | 0.0349 | 0.046 | 60 | 0.0199 | 0.0209 | 0.428 | 0 |
| 0.035 | 0.9 | 0.0324 | 0.0333 | 0.062 | 59 | 0.0167 | 0.0183 | 0.521 | 20 |
| 0.035 | 1.0 | 0.0332 | 0.0337 | 0.057 | 60 | 0.0158 | 0.0160 | 0.554 | 10 |
| 0.04 | 0.5 | 0.0370 | 0.0371 | 0.059 | 60 | 0.0190 | 0.0195 | 0.513 | 0 |
| 0.04 | 0.9 | 0.0375 | 0.0385 | 0.065 | 56 | 0.0146 | 0.0148 | 0.632 | 5 |
| 0.04 | 1.0 | 0.0373 | 0.0381 | 0.067 | 55 | 0.0146 | 0.0155 | 0.632 | 3 |
| 0.045 | 0.5 | 0.0425 | 0.0426 | 0.070 | 59 | 0.0186 | 0.0201 | 0.595 | 0 |
| 0.045 | 0.9 | 0.0412 | 0.0418 | 0.081 | 51 | 0.0139 | 0.0143 | 0.695 | 2 |
| 0.045 | 1.0 | 0.0417 | 0.0422 | 0.077 | 45 | 0.0123 | 0.0124 | 0.723 | 1 |
| 0.05 | 0.5 | 0.0458 | 0.0463 | 0.078 | 58 | 0.0163 | 0.0171 | 0.673 | 0 |
| 0.05 | 0.9 | 0.0458 | 0.0461 | 0.091 | 38 | 0.0114 | 0.0119 | 0.772 | 0 |
| 0.05 | 1.0 | 0.0454 | 0.0462 | 0.092 | 25 | 0.0106 | 0.0110 | 0.785 | 0 |
| 0.1 | 0.5 | 0.0806 | 0.0822 | 0.197 | 5 | 0.0028 | 0.0033 | 0.973 | 0 |
| 0.1 | 0.9 | 0.0805 | 0.0820 | 0.200 | 0 | 0.0017 | 0.0019 | 0.983 | 0 |
| 0.1 | 1.0 | 0.0811 | 0.0820 | 0.192 | 0 | 0.0014 | 0.0016 | 0.987 | 0 |
| 0.2 | 0.5 | 0.1264 | 0.1326 | 0.367 | 0 | 0.0001 | 0.0001 | 1.000 | 0 |
| 0.2 | 0.9 | 0.1110 | 0.1194 | 0.443 | 0 | 0.0000 | 0.0000 | 1.000 | 0 |
| 0.2 | 1.0 | 0.1108 | 0.1166 | 0.443 | 0 | 0.0000 | 0.0000 | 1.000 | 0 |

Read across: the declared rule's escape rate tracks p (0.111-0.126 at p=0.2, 0.133 on the worst seed).
The staged rule's peaks in the 0.03-0.045 band, and the peak depends on monitor recall. With recall 0.9 or
1.0 it is 1.73% averaged and 1.85% on the worst seed (p=0.03, recall 0.9). With recall 0.5 it is 1.99%
averaged and 2.09% on the worst seed (p=0.035), above the assumed 2% target on two of three seeds. Both exceed
CSP-1's first-level 1.76% on at least one seed, which is why section 1.2's closed form is not this rule's bound.
The mechanism is the signal. It is the share of outputs that are wrong and seen, so a monitor that sees
less breaches less often and demotes less often. That raises escapes for a weak monitor, and in the
band where p times recall sits just under the 0.05 threshold it lets a 90%-recall monitor reach the top
more often than a perfect one: 64 of 240 components against 37 of 240 over p 0.03-0.045, the declared
rule's pattern, damped. Below p=0.02 both reach the top in every component. A half-blind monitor reaches
it in 2 of 600 (p <= 0.01, a chance pass of the windowed drill). Past the band, a bad component keeps
returning to read_all (afi near 1) and escapes fall toward 0. Under the declared ladder a half-blind
monitor helps (p=0.05: 58 of 60 against 25 of 60 at recall 1.0). The price of the staged rule is review
load on middling components: at p=0.02 it reviews 20-21% of outputs against 3.7-3.9%, for an escape rate
of 1.55-1.61% against 1.88-1.93%. Whether that trade is worth it is the
AOQL target, a seed (q3). Simplifications that matter: a real reviewer errs (R3), errors cluster after a
change (which version_restart addresses and the simulation does not exercise), and harm per escaped
output differs by component (R6).

## 3. Settled / contested / risk / open

### Settled (fixture s1-s3)

- s1 The rungs are read_all, sampled, spot_check and anomaly_triggered in that order (pilot model code, e1),
  and the transition decision is deterministic code over recorded reviews, never an LLM (the charter's
  "transition triggers measurable"; Decision 55, which retires LLM recovery judgement in the executor,
  only by analogy).
- s2 Measured: the 18 declared triggers are upward-only free text and no item states a review rate, so no
  controller can evaluate them as written (VP 1, VP 2).
- s3 Measured: without a return leg the declared triggers let escaped-wrong share track the error rate
  (0.11-0.13 at p=0.2) and promote half-blind monitors faster (VP 5). This row records measurements only;
  whether a rule must bound outgoing quality and not reward a weaker monitor is the premise of k1, k4 and
  k5 and the target of q3, all class asked. The staged rule does both only in part: its bound holds for
  recall at or above the drill floor, and a damped weaker-monitor advantage remains in the peak band
  (section 2.6).

### Contested (k1-k3 in the fixture; k4-k7 report-only, the fixture's contested list is capped at 3)

- k1 The return leg.
  - (a) Any wrong output found above read_all returns the component to read_all (CSP-1; v08).
  - (b) One rung down (multi-level plans; on_wrong one_down, v09).
  - (c) None, as the six items declare (on_wrong none, v10).
  Recommended: (a). It is the strictest return. In the simulation it keeps escaped-wrong share at or under
  1.85% on every seed when monitor recall is at or above the drill floor, but a half-blind monitor reaches
  2.09% on the worst seed (VP 5), so the bound is conditional on recall. The bound is simulated, not closed-form: CSP-1's 1.76% covers its
  first level only. The cost is review load on a component that just proved it errs. (c) is measured in
  VP 5, and (b) is not simulated. Precedent: none admissible; T3.4
  criterion 2 and T4.4 call for per-gate rollback (e8), but they are roadmap criteria, and the pilot schema
  today forbids a downward pair (e1). Class asked.
- k2 A version change (classifier_version, parser_version or params_version), the question #1396 and
  #1397 both defer here (e9).
  - (a) Restart at read_all with the new version, which the controller returns as to_version (v18, v19,
    v40-v44).
  - (b) Drop one rung and keep counting.
  - (c) Keep the rung; the change rides on the existing sample.
  Recommended: (a). A rule-set change is a new process, and a clearance earned by the old rules says
  nothing about the new ones (the acceptance-sampling convention for a process change). Its cost falls on
  frequent small edits, so the build may want a declared cosmetic class (for example a rules-file comment)
  that does not bump the stamp. No precedent. Class asked.
  Inside (a), how a newer version is recognised. Verification r2 showed that "newest by date" rolls the
  stamp back on a late old-version output and restarts on every run while two versions overlap.
  - (a1) An ordered integer stamp: restart only on a stamp numerically above the current one, to the
    highest seen (staged; v42-v44, v47). One comparison, no history needed; a rollback must mint a new,
    higher stamp. Verification r3 showed that comparing as text misorders the siblings' unpadded integers
    (9 to 10 did not restart, and the new stamp's wrong outputs were ignored at the top rung), so the
    staged rule compares numerically, raises on a non-integer stamp (e17, e18), and lets a wrong output
    under any stamp demote (v48). Under-stamp outputs still never count toward clearance.
  - (a2) First-seen order: newer means first seen later in the review log. No stamp format is imposed,
    but every version's first appearance must be retained (q2).
  - (a3) Ladder state per (component, version), so overlapping versions each climb on their own reviews.
    Under (a1) a wrong output from a still-live older version demotes the component as a whole (v48);
    (a3) would demote only that version.
  Recommended: (a1), and (a3) if versions overlap for long (#1395's trigger reads "for every
  parser_version present"). Under (a1) a live older stamp that keeps erring makes the component oscillate
  between read_all and sampled, and each cycle asks the operator for an approval; the transition record
  should name the triggering stamp (a reason detail for q2's log) so the operator sees why.
  The version_restart param is a kill switch for (a), not an encoding of (b) or (c): with it off, clearance
  still counts only the current stamp and to_version never advances, so after the first bump the component
  holds at clearing until its state is rewritten (v53). (b) and (c) need SQL changes, not a param flip.
- k3 Who moves a component.
  - (a) The operator approves every promotion (propose_promote until an approval exists, v01/v02);
    demotions and restarts apply automatically.
  - (b) Both automatic (promotion_authority auto, v27).
  - (c) The operator ratifies both, with only the record automatic (T4.4's last exit criterion reads this
    way for A-gates).
  Recommended: (a). A promotion loosens oversight and is rare; a demotion tightens it and must not wait for
  a human who may be away. No Decision or contract decides it. Class asked. The same split governs the
  controller's own first rung (fixture c3): while the controller is at read_all its promotions are
  proposals the operator re-derives first, and its demotions and restarts apply at once and are re-derived
  from the transition log after the fact. Under (c) c3 would gate demotions too.
- k4 (report-only) The rule family and its seeds. (a) The staged multi-level CSP: consecutive clearance per
  rung with k1's return leg (as above). (b) A sequential probability ratio test per rung (Wald), which
  decides on all reviews, not runs, with stated error rates at two quality levels. (c) A fixed-sample
  bound: promote when the 95% upper bound on the error rate since rung entry is under a target. Also inside
  k4: unsure counts as a breaker (v05), an overdue sample is a breaker (v06), and a promotion needs a clean
  signal today (signal_gates_promotion, v20-v22). Recommended: (a) for the pilot, because its first level
  has a closed-form bound, its whole-ladder bound is simulated (VP 5), and it matches what the siblings
  already wrote; revisit (b) if review volume is the
  constraint. Measurement is not precedent. Class asked.
- k5 (report-only) Monitor validation before anomaly_triggered. (a) A windowed drill: at least drill_min
  faults injected in the last drill_window_days of the spot_check stint, with at least drill_recall_min of
  them detected (v14, v16, v17, v38, v39). (b) Historical recall: the share of reviewer-found wrong outputs
  on whose day the signal breached, over at least m findings. (c) None, as declared. Recommended: (a),
  with its costs stated. The bar is a recall floor, not perfection: at 18 of 20, a 90%-recall monitor
  passes on about 68% of reads, and at p <= 0.01 it reaches the top in all 60 components, as a
  perfect one does (VP 5). Because the window
  re-reads daily, a half-blind monitor can pass it by chance (2 of 600 components). A zero-miss rule over
  the whole stint closes that, but blocks a 90%-recall monitor almost entirely, and one miss holds it until
  a demotion (verification r1 probe). The drill gates the top rung only; it does not correct the signal.
  In the 0.03-0.045 band a 90%-recall monitor still reaches the top more often than a perfect one (64 of
  240 against 37 of 240), and a half-blind one lets more errors escape below the top (2.09% worst seed),
  because a signal that sees less breaches less (verification r2). Candidate cures, not adopted or
  simulated: (a1) divide the signal by the drill-measured recall before comparing it to the threshold;
  (a2) a drill floor nearer 1.0 over a longer window; (a3) a drill at sampled and spot_check too, so a weak
  monitor is caught before the top. (b) cannot be computed for a component that rarely errs, and (c) is the half-blind result in
  VP 5. The drill for back-validation already exists in kind: #1397's VP 3 feeds known
  no-op fixes through the verdict SQL. No precedent. Class asked.
- k6 (report-only) The fixture item's edge home. (a) part_of T3.4, whose exit criterion 2 asks for maturity
  gates with a per-gate rollback criterion. (b) part_of T4.4, which owns rollback automation but depends on
  the frozen executor (T4.2, Decision 67). (c) part_of T3.3 or T3.20, the parents of the components the
  ladder governs. The fixture carries (a), with depends_on T2.36, as a reversible pilot row
  (edges_disposition pilot_only). (a)'s premise rests on roadmap text, which is not admissible precedent,
  and it is tied to q1: if this ladder is separate from T3.4's A0-A3, the criterion-2 reading weakens. No
  precedent. Class asked.

- k7 (report-only) Signal demotion, the legs demote:signal_breach and demote:signal_dark (signal_demotes,
  dark_days). (a) Demote one rung on a breach, and one rung when no defined value arrived in dark_days days
  (staged; v11, v12, v32). (b) Hold instead of demote. (c) A breach returns to read_all, as a wrong output
  does under k1 (a). (d) Treat dark as a hold, or stage an output-count input so "no outputs" is told apart
  from "monitor dark". Recommended: (a) for a breach, and (d) with an output-count input for dark. The
  staged dark leg has an unmeasured cost: a clean component above read_all whose producer emits nothing
  for two days (a quiet weekend, an unattended week) is demoted, because no traffic and a dead monitor both
  read as undefined (plan-critique probe P-c). The simulation never exercises it (12 outputs every day).
  Precedent: none; T4.4's rollback text is roadmap and #1395's "undefined, never 0" is an unmerged
  sibling. q4 is folded here. Class asked.

### Risks

- R1 Review load. At read_all the operator reviews every output: at 12 a day for each of six components,
  about 72 reviews a day, and a component that keeps returning stays there (VP 5 afi near 1 at p >= 0.1).
  Unreviewed samples hold the ladder by construction (review_overdue), so an absent operator freezes
  promotion rather than loosening review.
- R2 Predictable sampling. md5 selection over a known salt lets anyone who knows the salt see which
  outputs will be reviewed. The producers are deterministic SQL today, so the risk is a future adaptive
  producer; the salt is per component and version and can be withheld from the producer.
- R3 Reviewer error. Clearance trusts the review outcome. A reviewer who confirms everything inflates it;
  seeded known-wrong outputs in the review stream (a reviewer drill) would measure that. Named for the
  Goodhart register.
- R4 Small daily denominators. At about 12 outputs a day a daily share threshold of 0.05 is an "any error"
  alarm (one wrong output is 0.083). The declared signal triggers name thresholds but not windows; T4.4
  asks for both (e8). The simulation uses a 7-day window.
- R5 Coupled components. An upstream component at read_all (capture, reader verbs) makes a downstream
  component's review outcomes partly about its inputs. A rung ceiling by dependency is W2's question
  (section 5).
- R6 Unequal harm. One escaped wrong proof close (back-validation) costs more than one escaped mislabel.
  The AOQL target may need to be per component; the Goodhart register owns the weights.

### Open questions (q1-q3 in the fixture, q4 report-only)

- q1 Is this ladder T3.4's A0-A3 (executor autonomy gates) or a separate per-component review ladder that
  shares the controller? T3.4 and T4.4 describe autonomy of the executor; these rungs describe how much of
  a loop component's output a human reviews. Recommended reading: separate ladders, one controller, and
  T4.4's numeric pinning (threshold, denominator, window, source) applied to both. For context, Decision
  55 retired Decision 46's graduated autonomy gates as complex and untested; T3.4 and T4.4 reintroduce
  gates for the executor in roadmap text only.
- q2 Where review records, transition records and the current rung live. Nothing reads a rung today (e3).
  Shares rec-filing q4 (Decision 199 journal recommended there) and back-validation q4.
- q3 Seeds are unmeasured: AOQL target (2% assumed; the staged rule meets it on every seed only with
  monitor recall at or above the drill floor), clearances 40/40/20, fractions 0.2/0.04/0.008,
  review_sla_days 3, dark_days 2, a 30-day window, a drill of 20 faults in 20 days at recall 0.9; operator
  review capacity at read_all; and before any of these, whether outgoing quality must be bounded at all
  (k1 (c) and k5 (c) say it need not).
- q4 (report-only) Folded into k7, widened to every rung and to the dark leg.

### Named for owners, not filed (Decision 67)

- O1 Pilot schema (evaluator owner): Maturity cannot express a return leg, a review fraction or a version
  rule, and Trigger.metric is free text (e1, e2). Whatever k1, k4 and k5 decide needs a schema change
  before any item can declare it; until then the fixture records the forks, not the rule.
- O2 T3.4 exit criterion 2 grounds its rollback requirement on "Decision 92 points 6-9", but Decision 92 is
  the agent-native Terraform CI/CD ratification and no Decision mentions MED-9 (grep). The anchor looks
  stale (inferred; the roadmap owner should confirm).

## 4. Consideration register (as authored in the fixture)

- why: the six items' 18 triggers are upward-only free text with no review rate (VP 1, VP 2), so promotion
  measures patience, and a blinder monitor promotes faster (VP 5).
- how: one SQL per daily run over the review log, failure_signal series, drills and approvals per component
  returns promote, propose_promote, hold, demote or restart with a reason and the version held next; the
  rule's legs are k1-k5 and k7.
- planes: data_plane (it runs over the customer's own review and telemetry rows; rung and transition
  counts are candidates for the rec-4141 allow-list, not decided here).
- maturity: the controller is itself a component. At its read_all every promotion is a proposal the
  operator re-derives before the rung moves, and every demotion and restart applies at once and is
  re-derived after the fact (c3, k3 (a)). read_all -> sampled at >= 40 controller decisions the
  operator re-derived unchanged since the last overturned one; sampled -> spot_check at >= 40 more on a
  1-in-5 sample; spot_check -> anomaly_triggered at >= 30 consecutive days with stale_rung_days 0 and
  the escaped-share estimate at or under the AOQL target (q3). These are clearance counts; their return leg is k1,
  which the schema cannot yet express (O1).
- failure_signal: a component sits at a rung its own evidence contradicts. Metric: est_escaped_share, an
  estimator with a stated denominator and window. Per component above read_all, over a rolling 30-day
  window: (wrong among audited samples / audited samples) x (unreviewed outputs / all outputs). Beside it,
  stale_rung_days (days a demote condition held with no transition recorded). Counting only the wrong
  outputs review found would read about f x p (0.0008 at the top rung for p = 0.1) and stay green while 10%
  escape; the estimator scales the audited error rate up to the unreviewed share instead. At the top rung
  the audited sample is small (about 3 a month at 12 outputs a day), so the estimate is noisy and is read
  beside the component's own failure_signal, never instead of it. Source: the review log (audited samples
  and unreviewed counts) and the transition log (q2).
- verification: c1 (the SQL passes these vectors over the build's tables; each transition record carries
  reason, counts, to_version and params_version, and the caller writes to_version back), c2 (a seeded
  simulation through the controller, over a grid spanning the AOQ peak band (p 0.005-0.2, recall 1.0, 0.9
  and 0.5) on at least three seeds, keeps escaped-wrong share at or under the AOQL target on every seed in
  every cell whose monitor recall meets the drill floor, reports the mean and worst-seed share for the
  cells below it, and lets no low-recall monitor reach the top more than by chance),
  c3 (the operator's read_all review: promotions before they apply, demotions and restarts after). All open.
- rollback: stop the schedule and set every component to read_all; the transition log stays as history and
  review returns to 100% until a controller resumes. Failing safe means more review, never less.
- edges: part_of T3.4 (exit criterion 2: gates with a per-gate rollback criterion; the home is parked as
  k6); depends_on T2.36 (the inputs are telemetry reader rows on DuckLake, like every sibling).

## 5. Boundary notes for W2 synthesis

- Rec filing (W1-5, #1396): its k2 recommendation surfaces the suppressed-sessions trend "to the
  maturity-ladder controller"; here that trend is a failure_signal input, and a breach demotes (v11). Its
  rejected_share is the review outcome stream (rejected = wrong). Its params_version change restarts the
  ladder under k2 (a). Its read_all files nothing, so a read_all review is a review of proposals.
- Back-validation (W1-6, #1397): "close_proposed until anomaly_triggered" makes this controller the gate for
  direct proof closes. Under k5 (a) its drill is its own VP 3 simulation run on known no-op fixes: a holds
  on a drill fix is a missed detection. Its spot_check -> anomaly_triggered trigger (false_proof_rate over 90
  days with at least 20 proof closes) maps to a signal window only if the producer emits the 90-day rate
  as its daily value, or the window becomes per component; signal_window_days is one global 30 today. Same
  k2 answer.
- Friction classifier (W1-3, #1394): its version stamp is classifier_version, and every rules edit bumps
  it; under k2 (a) that restarts the ladder, which is why k2 suggests a cosmetic class. Its precision comes
  only from label review (its q2), so its review log is the controller's main input.
- Deliberation capture (W1-4, #1395): deliberation_drift_share is "undefined, never 0" when nothing is
  classified; the controller reads undefined as dark, so a silent producer demotes rather than looks clean.
- Capture producer wiring (W1-1, #1384) and reader verbs (W1-2, #1390): upstream of everything. W2 should
  decide a rung ceiling by dependency (a component's rung at most its upstream's, or one above), using the
  pilot depends_on edges W2 adds.
- Goodhart register (later W1): R3 (reviewer drift) and R6 (unequal harm) belong there, and each
  component's failure_signal is the controller's signal input, so a detector's failure_signal in that
  register and here must be one series.
- Cost/egress budget (later W1): review load is the controller's cost; afi times output volume is the
  number of human reviews a day.
- Contradiction for W2: the six items write first-rung clearances of 20 and 50 and second-rung windows of
  30 to 200. One controller needs one rule family (k4) with per-component seeds, or the inventory stays
  six dialects.

## 6. Staged candidate decision text (for W3; not filed)

This text presumes the recommended options of parked forks: k1 (a), k2 (a) with (a1), k3 (a), k5 (a), and
k7 (a) for a breach. It does not presume any dark-signal rule: that is k7, and the bracketed clause below
stays open until k7 is answered. It is not settled; a W3 stager restates it after the operator answers.

"A loop component's review rung moves only by a deterministic controller over recorded reviews. Every
upward transition is paired with a return leg: a wrong output found above read_all returns the component
to read_all, a failure_signal that breaches demotes it [a dark failure_signal: as k7 decides], and a rule-set version change
restarts it at read_all. Promotion needs operator approval; demotion and restart apply on the run that
finds them. The top rung needs a passed monitor drill with a stated recall floor. Thresholds are stated with their denominator,
window and source."
