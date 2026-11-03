# REPORT: W1 component 6 - back-validation (a telemetry delta proves a fix)

REPORT-ONLY (Decision 86 cl.2). Planning artefact: `docs/plans/PLAN-w1-back-validation.yaml`.
Fixture rows: `pwi-back-validation` in `docs/work-item-pilot/telemetry-feedback-loop.yaml` (CD.45
pilot, provisional_v0). Nothing is built, filed, updated, closed, flipped or ratified here; no rec was
read for content or written while preparing it (Decision 67). Every rec operation below is a description.

## 0. Verdict

- T3.4's loop ends at "fix -> telemetry delta proves fix" (ROADMAP-PLATFORM.yaml:6593). The component is
  a deterministic VERDICT per filer rec, not a dashboard: given the fix sha and the telemetry before and
  after it, it returns one of Decision 201's verdicts (holds, fails, unmeasurable) or pending. One SQL
  statement does it and passes 28 of 28 hand-written vectors on DuckDB (VP 2), four of them by raising on
  malformed input. Twenty-five mutants were run once by hand; each fails a named vector. The rule is
  staged and recommended, not settled: which test, which error trade-off and which windows are a parked
  fork (k4), and so is the premise every option shares, a verdict evaluated after the merge (k1).
- The obvious rule is wrong, and the evidence is measured, not argued. A filer files when a count crosses
  a floor, so the filing window is selected on a high value. Read "the count fell after the fix" as proof
  and a fix that changed nothing is proven 44% to 79% of the time, depending on the base rate (VP 3: a
  seeded simulation of 1000 filed findings per scenario with no fix effect). The staged rule proves such
  a no-op fix at most 3.0% of the time (alpha 0.05), and proves a real 75% reduction 70% to 93% of the time
  when the friction touches 5% to 20% of exposed sessions.
- What makes the difference, each with its own vector and mutant:
  - The unit is the SESSION, and the denominator is EXPOSED sessions (sessions that could have shown the
    finding, such as sessions that called the tool), so a quiet week is not a fixed week (v19). A verb
    that returns one session twice fails loud rather than counting it twice (v27).
  - The post-fix sample size is fixed from the baseline BEFORE any post-fix data is read: enough exposed
    sessions to expect 10 affected ones if nothing changed, never fewer than 60, stopping at the first
    whole day that reaches it. A daily job therefore never peeks its way to a pass (v10, v11).
  - holds needs both a one-sided Fisher exact p <= 0.05 and at least a 50% drop in the rate; a significant
    small drop fails (v25), a large drop on thin data is inconclusive, which is unmeasurable (v03).
  - The fix day, the fix's own sessions (the rec in the session's rec_ids) and every producer stratum
    other than the baseline's dominant one are on neither side (v12, v13, v13b, v16).
  - Both windows come from one verb call at one classifier_version, so a rules edit that stops labelling
    the friction empties the baseline and reads unmeasurable, never holds (v08, v22). A fix that edits
    the classifier cannot prove itself.
- The repository already has the verdict LAYER this needs, and it admits a telemetry verdict with no
  change: `assert_acceptance_verdict` accepts a holds record keyed to the rec, its acceptance sha256 and
  the fix sha whatever its `source`, and refuses an unmeasurable one (VP 1, e3). What is missing is the
  ROUTE. Today a filer rec's acceptance command is classified probeable and routed to the static
  sandbox (no network), where any command that must read telemetry exits non-zero and resolves `fails`
  at the closing commit; no arm can say "not yet" (VP 1, e4-e6). Any telemetry proof is evaluated after
  the merge, while Decision 201 judges the oracle at the closing commit and keys the record to the closing
  sha. So every way of wiring this component amends Decision 201: that is the premise of k1, parked with a
  candidate decision staged for W3, not decided.
- The existing back-validation (ci_rca, T1.13 c12(iii)) reads recurrence only: a closed ci_rca rec on a
  file plus a new open one on the same file. Its inputs are the rec cache, a window and a clock (VP 1),
  so "no recurrence" reads the same whether the fix worked or the work stopped. It stays as is; this
  component does not replace it (section 5).
- This answers the rec-filing component's q1 (the filer rec's acceptance) and supplies the proof its k4
  recommendation waits for (#1396). It agrees with that design's window boundary: a window that spans the
  fix is neither proof nor regression.
- One item fits the clause-3 grain (kind task; three criteria; part_of T3.4, depends_on T2.36).

## 1. Evidence (each row re-derivable; VP step in brackets)

| id | fact | anchor |
|---|---|---|
| e1 | T3.4 exit criterion: "Anomaly detected -> rec filed -> priority queued -> /plan or executor -> fix -> telemetry delta proves fix"; T3.4 depends_on T3.3 and T1.13 | docs/ROADMAP-PLATFORM.yaml:6585-6593 |
| e2 | Decision 103: the deterministic probe is the rec's existing acceptance oracle; deterministic satisfaction may auto-close with a recorded proof; semantic verdicts produce close_proposed | docs/DECISIONS.md:6736-6741 |
| e3 | Decision 201's verdict layer: four values {holds, fails, unmeasurable, out_of_grammar}; update_rec refuses a supplied record that is not holds or is mis-keyed on rec_id, acceptance sha256 or the closing sha; it never reads `source` [VP 1] | scripts/ops_portal/closure_gate.py:317, :330-365 |
| e4 | A filer acceptance such as `bin/venv-python -m scripts.telemetry.back_validate --rec rec-100 --assert-holds` is decidable (a `--assert-<name>` exit contract) and passes the discrimination lint; the census buckets it probeable and the grammar partition routes it static [VP 1] | scripts/executor/acceptance_lint.py:211-223; scripts/backlog_health/census.py:179; scripts/rec_trailer_acceptance_junit.py:200 |
| e5 | The static source runs every command under `unshare -rmn` (no network) and maps any non-zero exit to FAIL, which resolves `fails`; only a timeout, a budget or isolation failure resolves `unmeasurable` [VP 1] | scripts/backlog_health/probe.py:11, :130; scripts/rec_trailer_acceptance.py:60-67 |
| e6 | The trailer gate declares two verdict sources, static and junit, and the TAP rule: no source may execute a command whose outcome an authoritative record already carries [VP 4] | docs/contracts/git-ops.yaml:416-432 |
| e7 | fixed_by_sha is stamped only when the trailer job CLOSES a ci_rca rec; nothing records a fix on a rec that stays open [VP 4] | docs/contracts/ci-rca-lifecycle.yaml:179-184 |
| e8 | ci_rca back-validation: inputs are the rec cache, a window and a clock; match key is the file; a closed rec with a preventive_action plus a new open rec on the same file is a candidate; surfacing only [VP 1] | scripts/ci_rca/back_validation.py:1-24, :107 |
| e9 | ci_rca's inactivity close: 30 days quiet and 14 days old, closed with the stale_no_recurrence waiver; flake escalation stops at 3 chain records | docs/contracts/ci-rca-lifecycle.yaml:185-199 |
| e10 | telemetry_sessions carries branch (optional) and rec_ids (the recs a session addressed), and no commit, sha or code-version column, so a session's code is known only by its start time [VP 4] | docs/contracts/telemetry_sessions.yaml:268-289 |
| e11 | The friction report (#1394, R5): a delta is valid only at one classifier_version, one registry_version and one parser_version per producer; a fix that adds a rule is a classifier change, not a behaviour change | docs/plans/reports/REPORT-w1-friction-classifier.md section 3, R5 (PR #1394) |
| e12 | The rec-filing report (#1396): the filer closes nothing (k4, parked), its acceptance probe is this component's (q1), the earliest regression is a full window after the close and a window spanning it drops (v08, v13, v30), and a third chain record is tagged chronic if k6 (a) is taken | docs/plans/reports/REPORT-w1-rec-filing-dedupe.md sections 2, 3, 5 (PR #1396) |
| e13 | The grammar partition reads `python -m` as a pytest `-m` selector (arm selector_flag); harmless here, since both arms route static, and already a known finding [VP 1] | scripts/rec_trailer_acceptance_junit.py:137, :173-174 |
| e14 | Decision 186's waiver vocabulary includes stale_no_recurrence, the waiver ci_rca's inactivity close uses [VP 4] | scripts/ops_portal/closure_gate.py:46-53 |

Measured (no rec content read or written):

- Verdict table: the SQL in section 2 passes 28 of 28 vectors (four by raising), with four verdicts,
  seven reasons and four actions exercised [VP 2]. Twenty-five mutants were run once by hand, not as a VP
  step; each fails the vectors named: the fix day counted as post-fix (v13) or as baseline (v13b), the
  fix sessions counted (v12), unexposed sessions in the denominator (v19), an unbounded baseline (v14),
  an exclusive baseline lower bound (v26), strata pooled (v15, v16), a fixed post-fix sample of 60 (v09,
  v10, v13b, v14), the decision day counted (v18), the deadline day expiring (v06), significance alone
  (v02, v20, v25), reduction alone (v03), significance tested before the reduction (v25), no affected
  floor on the baseline (v08), no rarity cap (v09, v13b, v14), the chronic tag ignored or off by one
  (v17), candidates inner-joined so a rec with no sessions vanishes (v21, and v24, whose NULL-day row
  then never reaches the guard), each of the four fail-loud guards removed (v22, v23, v24, v27), the
  upper tail summed instead of the lower (the nine holds vectors), a doubled p for a two-sided test
  (v19) and the sample stopping on the last day instead of the first full one (v11).
- Null-effect simulation [VP 3], seeded and hash-driven so it is identical on every run: 12 sessions a
  day, every session exposed, a constant affected rate p0, the rec-filing seed floor (3 affected sessions
  in a 7-day window, after a 60-day warm-up), a fix 2 to 10 days after filing, and a verdict read after
  the deadline so none is pending. Per 1000 filed findings:

  | p0 | naive proves a no-op fix | staged proves a no-op fix | staged proves a 75% reduction | staged unmeasurable on a 75% reduction |
  |---|---|---|---|---|
  | 0.02 | 0.791 | 0.022 | 0.132 | 0.866 |
  | 0.05 | 0.472 | 0.025 | 0.699 | 0.248 |
  | 0.1 | 0.442 | 0.030 | 0.866 | 0.064 |
  | 0.2 | 0.464 | 0.025 | 0.932 | 0.008 |

  "naive" is the reading a detector delta invites: the first full post-fix week shows fewer affected
  sessions than the filing week. At p0 0.02 the filing floor sits above the weekly mean, so filing
  selects a peak and the naive rule proves 79% of no-op fixes; at higher rates it is still a coin flip.
  Rare frictions are honestly unprovable: at p0 0.02 the staged rule returns unmeasurable for most
  findings, real fix or not, because the sample needed exceeds 28 days of sessions.

  The other side of the error profile, from the same run [VP 3]. fails carries no significance test: any
  post-fix rate above half the baseline is fails. So a real 75% reduction reads fails 0.2%, 5.3%, 7.0%
  and 6.0% of the time (p0 0.02 to 0.2), and smaller real effects are mostly refuted:

  | p0 | true reduction | holds | fails | unmeasurable |
  |---|---|---|---|---|
  | 0.1 | 50% | 0.415 | 0.433 | 0.152 |
  | 0.1 | 30% | 0.154 | 0.759 | 0.087 |
  | 0.2 | 50% | 0.502 | 0.488 | 0.010 |
  | 0.2 | 30% | 0.180 | 0.815 | 0.005 |

  holds tests "no change" and applies the 50% bar to the point estimate, so a real 30% drop is still
  proven 15% to 18% of the time. These trade-offs are why the rule is k4, not a settled row.
- Session volume: no friction or telemetry session rows exist in production until W1-1's wiring lands
  (rec-filing q3). The nearest proxy is merges to main, a lower bound on sessions: 168 commits over the
  14 full days 2026-09-19 to 2026-10-02, mean 12 a day, median 11.5, range 0 to 31 (git log on
  origin/main, counts only, each commit dated on its own commit-local day; on UTC days it is 172, median
  10.5, range 0 to 35). The simulation's 12 sessions a day is that lower bound.

## 2. Back-validation design (what this item stages)

Candidates are filer recs (source: the rec-filing component's single filer source, #1396) that carry a
fix attempt and no verdict yet. A fix attempt is the fix sha and its effective day; how it reaches an
open rec is k1. One verb call per candidate returns one row per session in the baseline and post-fix
windows, derived flags only (Decision 88; Decision 209 cl.2a): session_id, day (the session's start
day), producer, parser_version, classifier_version, `exposed` (the session could have shown the
finding: for `tool_error:Bash`, it called Bash), `affected` (it did show it, under the classifier version
of this call) and `addressed` (the candidate rec is in the session's rec_ids, so it is a fix session).
The detector that filed the rec supplies the exposure predicate (q1).

Windows, in whole days, matching the rec-filing boundary:

- Baseline: sessions that started in the `baseline_days` before the fix day.
- The fix day: neither side, like a filer window that spans the close (rec-filing v08, v13, v30).
- Post-fix: sessions that started after the fix day plus `settle_days` and before the decision day. The
  decision day itself is never read, so a re-run on the same day sees the same sample (v18).
- Only exposed, non-fix sessions in the baseline's dominant (producer, parser_version) stratum count on
  either side, the stratum being chosen from the baseline alone (v16). A parser_version bump after the
  fix therefore starves the post-fix side and ends unmeasurable at the deadline rather than mixing two
  producer versions (v15). classifier_version must be one value across the call (v22): the verb
  derives friction at read (W1-3), so the baseline is re-derived under today's rules, and a rules edit
  moves both sides alike (v08).

Sample size is fixed before the post-fix side is read: `n_req = max(min_post_exposed,
ceil(expected_null_affected * n_pre / k_pre))`, enough exposed sessions to expect 10 affected ones if
the rate had not moved. The post-fix sample is every eligible session up to the first whole day on which
the running count reaches n_req (v11). It stops on exposure, never on outcome, so a daily job evaluates
each candidate once at a fixed sample and the per-rec false-proof rate stays at alpha (R6). If n_req
exceeds `max_post_exposed`, the finding is too rare to prove at all and the verdict is immediate (v09).

Params are data, stamped as `params_version` in every verdict record (seed values, unmeasured, q3):

```yaml
params_version: 1
baseline_days: 28
settle_days: 0
min_pre_exposed: 60
min_pre_affected: 3
expected_null_affected: 10
min_post_exposed: 60
max_post_exposed: 336
max_wait_days: 28
alpha: 0.05
min_reduction: 0.5
chronic_from: 3
```

The decision, verified against the vectors below (VP 2). `{candidates}` is one row per candidate:
rec_id, fix_day, chain_len (records in the rec's fingerprint chain, from the rec-filing heads input) and
decide_day; `{sessions}` is the verb's rows, keyed by rec_id. Placeholders render from the params block.
Four malformed inputs raise instead of deciding (fail loud, Decision 55): a NULL in any session or
candidate column (v24), two classifier versions in one call (v22), an affected session that was not
exposed (v23) and a session_id that appears on more than one row (v27). The last matters because every
count is a row count: the telemetry journal can hold several rows per session (open, annotate and late
rows, R2), and a verb that leaked them would inflate the post-fix sample toward a false holds. The guards are aggregates read by the final projection, so no optimiser can skip them by
pushing a filter below them:

```sql
WITH g AS (
  SELECT rec_id,
         bool_or(session_id IS NULL OR day IS NULL OR producer IS NULL OR parser_version IS NULL OR classifier_version IS NULL
                 OR exposed IS NULL OR affected IS NULL OR addressed IS NULL) AS has_null,
         count(*) > count(DISTINCT session_id) AS dup,
         count(DISTINCT classifier_version) AS n_cv,
         bool_or(affected AND NOT exposed) AS orphan
  FROM {sessions} GROUP BY rec_id
),
s0 AS (
  SELECT s.rec_id, s.day, s.producer, s.parser_version, s.affected,
         CASE WHEN s.day < c.fix_day AND s.day >= c.fix_day - {baseline_days} THEN 'pre'
              WHEN s.day > c.fix_day + {settle_days} AND s.day < c.decide_day THEN 'post' END AS side
  FROM {sessions} s JOIN {candidates} c ON c.rec_id = s.rec_id
  WHERE s.exposed AND NOT s.addressed
),
e AS (SELECT * FROM s0 WHERE side IS NOT NULL),
pri AS (
  SELECT rec_id, producer, parser_version FROM (
    SELECT rec_id, producer, parser_version,
           row_number() OVER (PARTITION BY rec_id ORDER BY count(*) DESC, producer, parser_version) AS rk
    FROM e WHERE side = 'pre' GROUP BY rec_id, producer, parser_version)
  WHERE rk = 1
),
ep AS (
  SELECT e.rec_id, e.day, e.side, e.affected
  FROM e JOIN pri ON pri.rec_id = e.rec_id AND pri.producer = e.producer AND pri.parser_version = e.parser_version
),
base AS (
  SELECT c.rec_id, c.fix_day, c.chain_len, c.decide_day,
         count(ep.rec_id) FILTER (WHERE ep.side = 'pre') AS n_pre,
         count(ep.rec_id) FILTER (WHERE ep.side = 'pre' AND ep.affected) AS k_pre
  FROM {candidates} c LEFT JOIN ep ON ep.rec_id = c.rec_id
  GROUP BY c.rec_id, c.fix_day, c.chain_len, c.decide_day
),
req AS (
  SELECT *, CASE WHEN k_pre > 0
                 THEN greatest({min_post_exposed}, CAST(ceil({expected_null_affected} * n_pre / k_pre) AS BIGINT)) END AS n_req
  FROM base
),
pc AS (
  SELECT rec_id, day, CAST(sum(n) OVER w AS BIGINT) AS cn, CAST(sum(k) OVER w AS BIGINT) AS ck
  FROM (SELECT rec_id, day, count(*) AS n, count(*) FILTER (WHERE affected) AS k
        FROM ep WHERE side = 'post' GROUP BY rec_id, day)
  WINDOW w AS (PARTITION BY rec_id ORDER BY day ROWS UNBOUNDED PRECEDING)
),
stop AS (
  SELECT r.rec_id, arg_min(pc.cn, pc.day) AS n_post, arg_min(pc.ck, pc.day) AS k_post
  FROM req r JOIN pc ON pc.rec_id = r.rec_id AND pc.cn >= r.n_req
  GROUP BY r.rec_id
),
t AS (SELECT req.*, stop.n_post, stop.k_post FROM req LEFT JOIN stop ON stop.rec_id = req.rec_id),
fp AS (
  SELECT rec_id, sum(exp(
           lgamma(kk + 1.0) - lgamma(x + 1.0) - lgamma(kk - x + 1.0)
         + lgamma(nn - kk + 1.0) - lgamma(n_post - x + 1.0) - lgamma(nn - kk - n_post + x + 1.0)
         - lgamma(nn + 1.0) + lgamma(n_post + 1.0) + lgamma(nn - n_post + 1.0))) AS p
  FROM (SELECT rec_id, n_post, n_pre + n_post AS nn, k_pre + k_post AS kk,
               unnest(range(greatest(0, n_post - (n_pre + n_post - k_pre - k_post)), k_post + 1)) AS x
        FROM t WHERE n_post IS NOT NULL)
  GROUP BY rec_id
),
v AS (
  SELECT t.rec_id, t.chain_len,
    CASE
      WHEN g.has_null OR t.fix_day IS NULL OR t.decide_day IS NULL OR t.chain_len IS NULL
      THEN error('malformed back-validation input: NULL session column or candidate column')
      WHEN g.n_cv > 1 THEN error('mixed classifier_version: both windows must come from one verb call')
      WHEN g.dup THEN error('duplicate session_id: one row per session per verb call')
      WHEN g.orphan THEN error('malformed back-validation input: affected without exposure')
      WHEN t.n_pre < {min_pre_exposed} OR t.k_pre < {min_pre_affected} THEN 'baseline'
      WHEN t.n_req > {max_post_exposed} THEN 'too_rare'
      WHEN t.n_post IS NULL AND t.decide_day > t.fix_day + {settle_days} + {max_wait_days} THEN 'exposure'
      WHEN t.n_post IS NULL THEN 'waiting'
      WHEN t.k_post * t.n_pre > (1 - {min_reduction}) * t.k_pre * t.n_post THEN 'no_reduction'
      WHEN fp.p <= {alpha} THEN 'proven'
      ELSE 'inconclusive'
    END AS reason
  FROM t LEFT JOIN fp ON fp.rec_id = t.rec_id LEFT JOIN g ON g.rec_id = t.rec_id
)
SELECT rec_id,
       CASE reason WHEN 'waiting' THEN 'pending' WHEN 'proven' THEN 'holds' WHEN 'no_reduction' THEN 'fails'
                   ELSE 'unmeasurable' END AS verdict,
       reason,
       CASE WHEN reason = 'waiting' THEN 'none'
            WHEN reason = 'proven' AND chain_len >= {chronic_from} THEN 'close_proposed'
            WHEN reason = 'proven' THEN 'close'
            ELSE 'record' END AS action
FROM v
```

| verdict | reason | when | action (described only) |
|---|---|---|---|
| pending | waiting | the post-fix sample has not reached n_req and the deadline (fix day + settle + max_wait_days) has not passed (v04, v06, v10, v18) | none |
| holds | proven | one-sided Fisher exact p <= alpha AND the post-fix rate is at most (1 - min_reduction) of the baseline rate (v01, v11, v12, v13, v16, v19, v26) | the proof close through update_rec with the verdict record; under k1's recommendation it is executed as close_proposed until the anomaly_triggered rung, and its sha keying is k1. close_proposed in any case when chain_len >= chronic_from (v17; k5, inheriting rec-filing k6 (a)) |
| fails | no_reduction | the post-fix rate is above (1 - min_reduction) of the baseline rate, significant or not (v02, v25) | record the verdict on the fix attempt; the rec stays open with its fix attempt marked fails |
| unmeasurable | baseline | fewer than min_pre_exposed exposed or min_pre_affected affected baseline sessions, including a subject the classifier no longer labels (v07, v08, v21) | record; disposition is k2 |
| unmeasurable | too_rare | n_req > max_post_exposed (v09, v13b, v14) | record; disposition is k2 |
| unmeasurable | exposure | the deadline passed before the sample filled (v05, v15) | record; disposition is k2 |
| unmeasurable | inconclusive | the reduction is large enough but p > alpha (v03) | record; disposition is k2 |

Every run writes one run record (counts per verdict and reason, params_version, the classifier and
parser versions read) beside the per-rec verdict records, so nothing is decided silently. A verdict
record carries {rec_id, sha (the fix sha under k1 (a)), acceptance_sha256, verdict, source: telemetry, arm: the
reason}, the shape `assert_acceptance_verdict` already checks (e3), plus n_pre, k_pre, n_post, k_post,
the stop day, p and params_version, so a later reader re-reads the record instead of re-deriving it
(R2; the TAP rule, e6). The filer rec's acceptance is a command that reads that record, `bin/venv-python
-m scripts.telemetry.back_validate --rec rec-N --assert-holds`: exit 0 only when the latest record for
rec-N is holds and keyed to its current fix sha (this answers rec-filing q1). The module does not exist;
the name is a placeholder for the build.

How the verdict meets the rec-filing decision (#1396), as the build would wire it:

- While a fix attempt is pending, the rec is open. If the finding stays fileable, the filer `update`s the
  open rec (bumping occurrence_count), which changes no input of the verdict: the verdict reads telemetry,
  not the rec's counters.
- After a proof close on day D, the filer's next window must start after D to be a regression
  (rec-filing v07, v30). A proof close followed by a regression is this item's failure signal
  (false_proof_rate, section 4).
- A fails verdict leaves the rec open with the attempt marked fails. A second fix is a new attempt with
  its own fix day; the verdict always reads the latest attempt. Chain length (rec-filing k6) counts chain
  records, not attempts (R8).

Vectors (hand-written for this report; VP 2 runs every one). candidates rows are `[rec_id, fix_day,
chain_len, decide_day]`. sessions rows are count rows `[rec_id, day, producer, parser_version,
classifier_version, sessions, exposed, affected, addressed]`, which the harness expands into one row per
session: session i is exposed when i < exposed, affected when i < affected and addressed when i <
addressed, so the affected sessions are the first ones and the addressed sessions are drawn from them.
A row may carry a tenth element, a tag that replaces the row index in its session ids, so two rows with
the same tag describe the same sessions (v27 uses it to send a session twice).
`expected` lists `[rec_id, verdict, reason, action]` or `error`:

```yaml
vectors:
  - id: v01-clear-reduction-holds
    candidates: [[rec-100, 30, 1, 60]]
    sessions: [[rec-100, 15, claude_code, 1, 1, 120, 120, 24, 0], [rec-100, 31, claude_code, 1, 1, 60, 60, 1, 0]]
    expected: [[rec-100, holds, proven, close]]
  - id: v02-no-reduction-fails
    candidates: [[rec-100, 30, 1, 60]]
    sessions: [[rec-100, 15, claude_code, 1, 1, 120, 120, 24, 0], [rec-100, 31, claude_code, 1, 1, 60, 60, 12, 0]]
    expected: [[rec-100, fails, no_reduction, record]]
  - id: v03-reduction-short-of-proof-is-inconclusive
    candidates: [[rec-100, 30, 1, 60]]
    sessions: [[rec-100, 15, claude_code, 1, 1, 60, 60, 6, 0], [rec-100, 31, claude_code, 1, 1, 100, 100, 4, 0]]
    expected: [[rec-100, unmeasurable, inconclusive, record]]
  - id: v04-pending-before-the-sample-fills
    candidates: [[rec-100, 30, 1, 40]]
    sessions: [[rec-100, 15, claude_code, 1, 1, 120, 120, 24, 0], [rec-100, 31, claude_code, 1, 1, 40, 40, 0, 0]]
    expected: [[rec-100, pending, waiting, none]]
  - id: v05-deadline-passed-is-unmeasurable
    candidates: [[rec-100, 30, 1, 59]]
    sessions: [[rec-100, 15, claude_code, 1, 1, 120, 120, 24, 0], [rec-100, 31, claude_code, 1, 1, 40, 40, 0, 0]]
    expected: [[rec-100, unmeasurable, exposure, record]]
  - id: v06-deadline-day-still-waits
    candidates: [[rec-100, 30, 1, 58]]
    sessions: [[rec-100, 15, claude_code, 1, 1, 120, 120, 24, 0], [rec-100, 31, claude_code, 1, 1, 40, 40, 0, 0]]
    expected: [[rec-100, pending, waiting, none]]
  - id: v07-thin-baseline-is-unmeasurable
    candidates: [[rec-100, 30, 1, 60]]
    sessions: [[rec-100, 15, claude_code, 1, 1, 50, 50, 20, 0], [rec-100, 31, claude_code, 1, 1, 60, 60, 0, 0]]
    expected: [[rec-100, unmeasurable, baseline, record]]
  - id: v08-relabelled-subject-cannot-prove-a-fix
    candidates: [[rec-100, 30, 1, 60]]
    sessions: [[rec-100, 15, claude_code, 1, 2, 120, 120, 0, 0], [rec-100, 31, claude_code, 1, 2, 60, 60, 0, 0]]
    expected: [[rec-100, unmeasurable, baseline, record]]
  - id: v09-too-rare-to-prove
    candidates: [[rec-100, 30, 1, 60]]
    sessions: [[rec-100, 15, claude_code, 1, 1, 300, 300, 3, 0], [rec-100, 31, claude_code, 1, 1, 400, 400, 0, 0]]
    expected: [[rec-100, unmeasurable, too_rare, record]]
  - id: v10-sample-size-follows-the-baseline-rate
    candidates: [[rec-100, 30, 1, 40]]
    sessions: [[rec-100, 15, claude_code, 1, 1, 200, 200, 10, 0], [rec-100, 31, claude_code, 1, 1, 150, 150, 0, 0]]
    expected: [[rec-100, pending, waiting, none]]
  - id: v11-sample-stops-on-the-first-full-day
    candidates: [[rec-100, 30, 1, 40]]
    sessions: [[rec-100, 15, claude_code, 1, 1, 200, 200, 10, 0], [rec-100, 31, claude_code, 1, 1, 150, 150, 0, 0], [rec-100, 32, claude_code, 1, 1, 60, 60, 1, 0], [rec-100, 33, claude_code, 1, 1, 60, 60, 30, 0]]
    expected: [[rec-100, holds, proven, close]]
  - id: v12-fix-sessions-are-excluded
    candidates: [[rec-100, 30, 1, 60]]
    sessions: [[rec-100, 15, claude_code, 1, 1, 120, 120, 24, 0], [rec-100, 31, claude_code, 1, 1, 60, 60, 1, 0], [rec-100, 31, claude_code, 1, 1, 10, 10, 10, 10]]
    expected: [[rec-100, holds, proven, close]]
  - id: v13-fix-day-is-neither-side
    candidates: [[rec-100, 30, 1, 60]]
    sessions: [[rec-100, 15, claude_code, 1, 1, 120, 120, 24, 0], [rec-100, 30, claude_code, 1, 1, 30, 30, 30, 0], [rec-100, 31, claude_code, 1, 1, 60, 60, 1, 0]]
    expected: [[rec-100, holds, proven, close]]
  - id: v13b-fix-day-cannot-buy-a-baseline
    candidates: [[rec-100, 30, 1, 60]]
    sessions: [[rec-100, 15, claude_code, 1, 1, 120, 120, 3, 0], [rec-100, 30, claude_code, 1, 1, 30, 30, 30, 0], [rec-100, 31, claude_code, 1, 1, 60, 60, 1, 0]]
    expected: [[rec-100, unmeasurable, too_rare, record]]
  - id: v14-old-evidence-outside-the-baseline-is-ignored
    candidates: [[rec-100, 30, 1, 60]]
    sessions: [[rec-100, 1, claude_code, 1, 1, 200, 200, 100, 0], [rec-100, 15, claude_code, 1, 1, 120, 120, 3, 0], [rec-100, 31, claude_code, 1, 1, 60, 60, 1, 0]]
    expected: [[rec-100, unmeasurable, too_rare, record]]
  - id: v15-parser-bump-after-the-fix-starves-the-baseline-stratum
    candidates: [[rec-100, 30, 1, 59]]
    sessions: [[rec-100, 15, claude_code, 1, 1, 120, 120, 24, 0], [rec-100, 31, claude_code, 2, 1, 60, 60, 0, 0]]
    expected: [[rec-100, unmeasurable, exposure, record]]
  - id: v16-stratum-is-chosen-from-the-baseline
    candidates: [[rec-100, 30, 1, 60]]
    sessions: [[rec-100, 15, claude_code, 1, 1, 120, 120, 24, 0], [rec-100, 15, litellm, 1, 1, 60, 60, 0, 0], [rec-100, 31, claude_code, 1, 1, 60, 60, 1, 0], [rec-100, 31, litellm, 1, 1, 60, 60, 30, 0]]
    expected: [[rec-100, holds, proven, close]]
  - id: v17-chronic-proof-is-only-proposed
    candidates: [[rec-100, 30, 3, 60]]
    sessions: [[rec-100, 15, claude_code, 1, 1, 120, 120, 24, 0], [rec-100, 31, claude_code, 1, 1, 60, 60, 1, 0]]
    expected: [[rec-100, holds, proven, close_proposed]]
  - id: v18-the-decision-day-is-not-counted
    candidates: [[rec-100, 30, 1, 40]]
    sessions: [[rec-100, 15, claude_code, 1, 1, 120, 120, 24, 0], [rec-100, 40, claude_code, 1, 1, 60, 60, 1, 0]]
    expected: [[rec-100, pending, waiting, none]]
  - id: v19-unexposed-sessions-are-not-in-the-denominator
    candidates: [[rec-100, 30, 1, 40]]
    sessions: [[rec-100, 15, claude_code, 1, 1, 240, 120, 24, 0], [rec-100, 31, claude_code, 1, 1, 60, 60, 5, 0]]
    expected: [[rec-100, holds, proven, close]]
  - id: v20-candidates-are-independent
    candidates: [[rec-100, 30, 1, 60], [rec-200, 30, 1, 60]]
    sessions: [[rec-100, 15, claude_code, 1, 1, 120, 120, 24, 0], [rec-100, 31, claude_code, 1, 1, 60, 60, 1, 0], [rec-200, 15, claude_code, 1, 1, 120, 120, 24, 0], [rec-200, 31, claude_code, 1, 1, 60, 60, 12, 0]]
    expected: [[rec-100, holds, proven, close], [rec-200, fails, no_reduction, record]]
  - id: v21-candidate-without-sessions-is-unmeasurable
    candidates: [[rec-100, 30, 1, 60]]
    sessions: []
    expected: [[rec-100, unmeasurable, baseline, record]]
  - id: v22-mixed-classifier-version-fails-loud
    candidates: [[rec-100, 30, 1, 60]]
    sessions: [[rec-100, 15, claude_code, 1, 1, 120, 120, 24, 0], [rec-100, 31, claude_code, 1, 2, 60, 60, 1, 0]]
    expected: error
  - id: v23-affected-without-exposure-fails-loud
    candidates: [[rec-100, 30, 1, 60]]
    sessions: [[rec-100, 15, claude_code, 1, 1, 120, 100, 110, 0], [rec-100, 31, claude_code, 1, 1, 60, 60, 1, 0]]
    expected: error
  - id: v24-null-session-day-fails-loud
    candidates: [[rec-100, 30, 1, 60]]
    sessions: [[rec-100, null, claude_code, 1, 1, 120, 120, 24, 0], [rec-100, 31, claude_code, 1, 1, 60, 60, 1, 0]]
    expected: error
  - id: v25-significant-but-small-reduction-fails
    candidates: [[rec-100, 30, 1, 60]]
    sessions: [[rec-100, 15, claude_code, 1, 1, 1200, 1200, 240, 0], [rec-100, 31, claude_code, 1, 1, 1200, 1200, 180, 0]]
    expected: [[rec-100, fails, no_reduction, record]]
  - id: v26-baseline-lower-bound-is-inclusive
    candidates: [[rec-100, 30, 1, 60]]
    sessions: [[rec-100, 2, claude_code, 1, 1, 120, 120, 24, 0], [rec-100, 31, claude_code, 1, 1, 60, 60, 1, 0]]
    expected: [[rec-100, holds, proven, close]]
  - id: v27-duplicate-session-row-fails-loud
    candidates: [[rec-100, 30, 1, 60]]
    sessions: [[rec-100, 15, claude_code, 1, 1, 60, 60, 6, 0, pre], [rec-100, 31, claude_code, 1, 1, 100, 100, 4, 0, post], [rec-100, 31, claude_code, 1, 1, 100, 100, 0, 0, post]]
    expected: error
```

The null-effect simulation (VP 3) builds its sessions and candidates with the SQL below and decides them
with the SQL above, unchanged. Draws are md5 over the seed, the finding, the day and the session, so the
result is identical on every machine and thread count:

```yaml
simulation:
  seed: bv1
  sims: 1000
  days: 160
  sessions_per_day: 12
  warm_days: 60
  filing_window_days: 7
  filing_min_sessions: 3
  scenarios: [[0.02, 0.0], [0.02, 0.75], [0.05, 0.0], [0.05, 0.75], [0.1, 0.0], [0.1, 0.75], [0.2, 0.0], [0.2, 0.75], [0.1, 0.5], [0.1, 0.3], [0.2, 0.5], [0.2, 0.3]]
```

```sql
CREATE TABLE sim_draws AS
  SELECT k, d, i,
         CAST('0x' || substr(md5(concat_ws(':', '{seed}', k, d, i)), 1, 8) AS UBIGINT) / 4294967296.0 AS u
  FROM range({sims}) a(k), range({days}) b(d), range({sessions_per_day}) c(i);
CREATE TABLE sim_daily AS
  SELECT k, d, count(*) FILTER (WHERE u < {p0}) AS a FROM sim_draws GROUP BY k, d;
CREATE TABLE sim_candidates AS
  SELECT 'sim-' || k AS rec_id, f + 2 + CAST(floor(9 * CAST('0x' || substr(md5(concat_ws(':', '{seed}', 'lag', k)), 1, 8) AS UBIGINT) / 4294967296.0) AS BIGINT) AS fix_day,
         1 AS chain_len, f AS filed_day
  FROM (SELECT k, min(d) AS f FROM (
          SELECT k, d, sum(a) OVER (PARTITION BY k ORDER BY d ROWS BETWEEN {filing_window_days} - 1 PRECEDING AND CURRENT ROW) AS w
          FROM sim_daily) WHERE d >= {warm_days} AND w >= {filing_min_sessions} GROUP BY k);
ALTER TABLE sim_candidates ADD COLUMN decide_day BIGINT;
UPDATE sim_candidates SET decide_day = fix_day + {max_wait_days} + 1;
CREATE TABLE sim_sessions AS
  SELECT c.rec_id, c.rec_id || '-' || x.d || '-' || x.i AS session_id, x.d AS day, 'claude_code' AS producer,
         1 AS parser_version, 1 AS classifier_version, TRUE AS exposed,
         x.u < CASE WHEN x.d > c.fix_day THEN {p0} * (1 - {r}) ELSE {p0} END AS affected, FALSE AS addressed
  FROM sim_draws x JOIN sim_candidates c ON c.rec_id = 'sim-' || x.k AND x.d < c.decide_day;
CREATE TABLE sim_naive AS
  SELECT c.rec_id,
         count(*) FILTER (WHERE s.affected AND s.day > c.fix_day AND s.day <= c.fix_day + {filing_window_days})
       < count(*) FILTER (WHERE s.affected AND s.day > c.filed_day - {filing_window_days} AND s.day <= c.filed_day) AS naive_holds
  FROM sim_candidates c JOIN sim_sessions s ON s.rec_id = c.rec_id GROUP BY c.rec_id;
```

What the simulation does not model, stated so the numbers are not over-read: every session is exposed;
sessions are independent; the daily volume is constant; the base rate does not drift; nothing else
changes at the fix (R1). It measures the decision rule's error rates under those conditions, not the
production false-proof rate, which the failure signal measures (section 4).

## 3. Settled / contested / risk / open

Settled (a Decision or contract precedent, or a measured fact every option must meet; s1-s2 in the
fixture):

- s1 The verdict vocabulary is Decision 201's (holds, fails, unmeasurable), recorded in Decision 201's
  record shape {rec_id, sha, acceptance_sha256, verdict, source, arm}, which update_rec's single
  enforcement site already admits (VP 1). pending is not a verdict; it is the absence of one. Precedent:
  Decision 201. Which sha the record carries, when it is evaluated and whether a holds closes directly
  are not settled here: they are k1.
- s2 A naive post-fix drop proves 44% to 79% of no-op fixes (VP 3), so any rule must hold alpha on exposed
  sessions with a post-fix sample fixed before it is read. This is a measured constraint on every option
  of k4, not a choice among them.

Contested (evidence on both sides, options listed; k1-k3 in the fixture, k4-k5 report-only; all parked, see
/mnt/project-files/gates/w1-c6-parked-forks.md):

- k1 A verdict evaluated after the merge, and how a fix reaches an open rec.
  Premise shared by every option: a telemetry verdict exists only after post-fix sessions accumulate, so
  it is evaluated days after the merge and keyed to the fix sha. Decision 201 points 1-2 judge the oracle
  at the closing commit and key the record to the closing sha, so the premise amends Decision 201
  whichever plumbing is chosen. Its second half is whether a holds, which is an inference with a designed
  false-proof rate (VP 3) rather than Decision 103's exact "probe passes; target present", may ever close a
  rec directly, or always goes close_proposed. Recommended for that half: close_proposed up to the
  spot_check rung, a direct close only from anomaly_triggered (section 4). The options differ in plumbing:
  - (a) The fix PR names the rec in its `Resolves:` trailer as today. The trailer census routes an
    acceptance carrying the back-validation `--assert-holds` contract to a NEW `telemetry` verdict source
    (beside static and junit, e6), which at merge records a fix attempt {sha, effective day} on the open
    rec and supplies no verdict, so the structural binding leaves the rec open (Decision 201 cl.3). The
    scheduled back-validation job later writes the verdict record and, on holds, closes through update_rec.
  - (b) A separate trailer for fixes that need telemetry proof, processed by its own job.
  - (c) No automation: an operator stamps the fix attempt by hand.
  For (a): one trailer, one census, one enforcement site; the record shape already passes (VP 1). Against
  (a): it adds a third source kind to Decision 201's gate, and today the same command resolves `fails`
  at merge (e4, e5). (b) and (c) need the same amendment with more machinery or more toil. Two build
  constraints ride with any option: the late closer passes update_rec a closing sha
  taken from the fix-attempt stamp on the rec, never from the verdict record (reading it back out of
  the record makes the sha check a self-comparison, closure_gate.py:330-365); and it is a non-trailer
  update_rec caller, the fail-open Decision 201 cl.3 leaves to rec-3999, so it must always supply a
  record. Recommended: (a). Its premise amends a Decision under every option, so it is parked, with
  candidate decision text staged in section 5 for W3.
- k2 What happens to an unmeasurable verdict at the deadline (the fallback the rec-filing k4 recommendation
  names).
  - (a) The rec stays open and is listed for triage with its reason.
  - (b) A close_proposed for the operator, carrying the reason and counts (Decision 103's route for a
    verdict that is not deterministic satisfaction).
  - (c) A direct close with the stale_no_recurrence waiver when the reason is exposure or too_rare
    (ci_rca's inactivity close adapted, e9, e14).
  For (b): the queue is already 5x over its soft cap (rec-filing measured 1244 non-automatable open
  recs against 250), and an unprovable rec left open is queue noise; a human still decides. Against (c):
  quiet is not fixed, which is the whole point of this component. Recommended: (b). No precedent covers
  an unmeasurable verdict, so it is parked.
- k3 Confounds. A single-subject test proves any fix that lands during a global drop (a model upgrade, a
  harness change, a workload shift) (R1).
  - (a) The single-subject rule only, recording the same detector's other subjects' rate ratio in the
    verdict record as an advisory control.
  - (b) A difference-in-differences gate: holds also needs the subject's rate ratio to beat the median
    ratio of the detector's other subjects over the same windows.
  - (c) Suspend verdicts across a declared change window (a model_primary or producer_version change).
  Against (b): with few subjects per detector the control is noisy and costs power; it also needs a
  second verb call per candidate. Recommended: (a) until the sampled rung, then decide (b) on the
  recorded controls. A weighed choice with no precedent, so it is parked.
- k4 (report-only: the fixture's contested list is capped at 3) The decision rule and its windows. Decision
  55 settles that the decision is code; it settles neither the test, nor the error trade-off, nor the
  windows. Options:
  - (a) The staged rule (section 2): exposed non-fix sessions in the baseline's dominant (producer,
    parser_version) stratum, the fix day on neither side, a post-fix sample sized from the baseline,
    holds on one-sided Fisher p <= alpha AND a point estimate at most half the baseline, fails on the
    point estimate alone. Evidence: 28/28 vectors, 25 mutants, the simulation (VP 2, VP 3).
  - (b) Test the reduction itself: holds only when a one-sided test rejects "post-fix rate >= half the
    baseline" (a rate-ratio null of 0.5), so a real 30% drop is never proven by luck of the point
    estimate; costs power at the bar.
  - (c) A symmetric fails: fails only when a test rejects "the drop is at least half", otherwise
    inconclusive (unmeasurable), so a real 75% fix is refuted far less often than 5-7%; more recs end
    unmeasurable and k2 decides them.
  - (d) Pool every matched (producer, parser_version) stratum with a stratified test (Mantel-Haenszel)
    instead of keeping only the dominant one, which recovers power where it is lowest (p0 0.02 reads 87%
    unmeasurable) at the cost of a model of stratum shift.
  For (a): simplest, measured, and its asymmetry is deliberate: a false fails leaves the rec open and
  shows in the overturned-verdict count (section 4), while a false holds closes it. Against (a): the
  error profile in section 1, and the asymmetry is a judgement. Recommended: (a), with (c) the first
  revisit if overturned fails dominate at read_all. No precedent decides between them, so it is parked.
- k5 (report-only) The chronic close. The SQL's chronic_from arm turns a proof close into close_proposed
  from the third chain record, which encodes rec-filing k6 (a) (#1396). That fork is parked there, as is
  the verdict record's home (rec-filing q4). Options follow k6: (a) close_proposed from the third record
  (staged, v17); (b) no chronic arm, if k6 (b) stops filing at 3, which leaves the arm dead; (c) no cap.
  Recommended: whatever k6 decides; this item takes chronic_from as k6's parameter. Parked with k6.

Risk (known loss modes, not choices):

- R1 Association, not causation, and not effect size. holds means the subject's exposure-normalised rate
  after this fix is lower than before beyond chance (one-sided Fisher against no change) and its point
  estimate is at most half the baseline. It does not mean the true drop is at least half: a real 30% drop
  is proven 15% to 18% of the time (section 1). A concurrent global change can also produce a holds (k3).
  The run record says which other fixes landed in the same windows.
- R2 Late rows. Telemetry is an append-only journal and a producer can write a session's rows after the
  fact; a re-derivation days later could see a different sample. The verdict record stores the counts
  and the stop day, the acceptance command reads the record (TAP rule), and the build should decide only
  on days whose generation markers have committed (Decision 207's envelope R3).
- R3 Exposure-removing fixes. A fix that works by no longer calling the tool leaves too few exposed
  sessions and ends unmeasurable (exposure), never holds. That is correct (the rule cannot see it) and
  k2 decides what follows.
- R4 Code version by time. Sessions carry no commit (e10), so "post-fix" means "started after the fix
  day". A session on a branch cut before the fix counts as post-fix and dilutes the effect, which biases
  toward fails, the safe direction. Fixes that are not live at merge (a Lambda behind a governed deploy,
  a harness or model change) need a different effective day (q2). Named for the telemetry owner, not
  filed: a session base-sha column would make this exact, the way ci_rca uses ancestry (e7).
- R5 Egress. One verb call per pending candidate per run, returning derived per-session flags for the
  baseline and post-fix windows of one subject. Bounded by baseline_days plus max_wait_days of one
  subject's sessions; no raw row, signature or text.
- R6 Many candidates. Each candidate is decided once, at a sample fixed in advance, so the per-rec
  false-proof rate is alpha. Across N no-op fixes about alpha x N are still proven; that is why the
  failure signal measures false proofs in production rather than trusting alpha.
- R7 The trailer partition misreads `python -m` as a pytest `-m` selector (e13). It routes static either
  way, so it changes nothing here; k1 (a) must classify the back-validation contract before that arm.
- R8 Repeated fails. A rec whose fixes keep failing accumulates fix attempts while its chain stays one
  record long, so rec-filing k6's chronic tag (chain records) never fires on it. The run record should
  count attempts per rec; whether a second fails verdict escalates is left to W2, which should weigh that a real 75% fix reads
  fails 5-7% of the time (section 1).

Open (q1-q3 in the fixture; none is answerable from the repository):

- q1 Who declares exposure? Each detector must supply, per subject, the predicate for "this session could
  have shown it" and return exposed and affected per session. No verb returns either today; the friction
  verb returns per-session label counts (W1-3) and would need tool-call presence per tool. The verb
  must also return exactly one row per session (v27) and a non-NULL rec_id on every row: a row whose
  rec_id is NULL joins to no candidate and is dropped without an error.
- q2 What is the effective day of a fix that is not live at merge? The merge day is right for repo hooks,
  skills and scripts that a new session picks up. A Lambda fix is live at its governed deploy, and a
  model or harness change has no sha in this repository.
- q3 Seeds: alpha 0.05, min_reduction 0.5, expected_null_affected 10, min_post_exposed 60, baseline 28
  days, max_wait 28 days are unmeasured. At about 12 sessions a day (the merge lower bound), a friction
  touching under about 3% of exposed sessions cannot be proven within 28 days (VP 3, p0 0.02).
- q4 (report-only: the fixture's open list is capped at 3) Where do verdict records live? The rec-filing
  run record's home is parked there as q4 (Decision 199 journal recommended); the verdict record should
  share it, with the summary also written into the rec's context_v2_json when it closes.

## 4. Consideration register (as authored in the fixture)

- planes: data_plane. The verdict reads telemetry through a reader verb and writes through the portal;
  a control plane sees per-verdict counts and the closed reason vocabulary only (rec-4141's allow-list).
- failure_signal: false proof or proof starvation. A false proof is a rec closed on a holds verdict whose
  fingerprint regresses (the filer's regression action, regression_of pointing at it) within
  max_wait_days of the close. Starvation is verdicts that stay unmeasurable, so no filer rec ever
  closes on proof. Metric: `false_proof_rate` (regressions within 28 days of a proof close, over proof
  closes) beside `unmeasurable_share`, per 90 days. Source: verdict records joined to the filer's
  regression action; per-reason verdict counts. False refutation (a real fix read fails, 5-7% of real 75%
  fixes in the simulation) is not in that metric; it is read through the overturned-verdict count the
  maturity ladder already keeps, which counts an overturned fails like an overturned holds.
- Goodhart guard: the cheap ways to a low false_proof_rate are to prove nothing (every verdict
  unmeasurable or fails) or to prove only fixes for frictions that were fading anyway. The first shows as
  unmeasurable_share and the fails share in every run record, read beside false_proof_rate. The second
  is what the fixed sample and the significance test bound (VP 3). The cheap way to many holds verdicts,
  a classifier edit that stops labelling the friction, reads unmeasurable by construction (v08).
- maturity: read_all means every verdict is a proposal: the job writes verdict records and closes
  nothing; the operator confirms each holds close and each fails (c3). read_all -> sampled at >= 20
  consecutive verdicts the operator confirmed unchanged; sampled -> spot_check at 0 overturned verdicts
  across the last 50 sampled; spot_check -> anomaly_triggered at false_proof_rate <= 0.05 over the last
  90 days with at least 20 proof closes. Until anomaly_triggered a holds is proposed, never closed
  directly (k1's second half). A params_version change is a new rule set; the maturity-ladder
  controller decides whether it restarts the ladder.
- verification: c1 (the verdict passes these vectors on DuckDB), c2 (the close path k1 picks, with the
  closing sha taken from the recorded fix attempt and a pending candidate left open), c3 (the operator's
  read_all verdict review). All open; c2's test is written once k1 is decided.
- rollback: stop the back-validation schedule. Verdict records stay as history; pending recs stay open
  for a human close; recs it closed are ordinary closed recs whose recurrence files a regression.
- edges: part_of T3.4 (its last link is this item); depends_on T2.36 (the verdict reads telemetry through
  DuckLake reader verbs).

## 5. Boundary notes for W2 synthesis

- Rec filing (W1-5, #1396): this answers its q1 (the acceptance is the back-validation `--assert-holds`
  contract, reading the verdict record) and supplies the proof its k4 (a) waits for; k2 here is the
  fallback that k4 (b) names. Both use one boundary: the fix or close day is on neither side. This item's
  chronic_from is rec-filing k6's parameter, so k6 binds both. The filer's file action must keep the
  fingerprint's detector_id and subject_key in context_v2_json (it does, s1 there), which is how a
  verdict finds its subject. On merge, W2 should add `pwi-back-validation depends_on
  pwi-rec-filing-dedupe`; the evaluator's L4 refuses an edge to a pilot item absent from this branch.
- Friction classifier (W1-3, #1394): the verb must return per-session exposed and affected flags for one
  subject at one classifier_version (q1); its R5 is honoured by re-deriving the baseline in the same call.
- Reader verbs (W1-2, #1390): back-validation is one more verb shape, per-session derived flags over two
  windows of one subject; its partition bound is baseline_days plus max_wait_days.
- Capture producer wiring (W1-1, #1384): exclusion of fix sessions needs rec_ids populated on the
  session (e10); without it the fix session's own friction counts as post-fix and biases toward fails.
- Deliberation capture (W1-4, #1395): a drift finding can be back-validated the same way, with the
  producer's parser_version as the stratum.
- ci_rca back-validation (T1.13 c12(iii)): unchanged. It reads recurrence of recs, not telemetry, and
  W2 may decide whether ci_rca later adopts an exposure denominator (CI runs are its sessions).
- Maturity-ladder controller and Goodhart register: this item's verdict counts and false_proof_rate are
  inputs to both. Cost/egress: R5.
- Contradiction for W2: rec-filing k6 (a) forbids a proof-only close of a chronic rec, which this design
  honours (v17); if W2 picks k6 (b) instead (stop filing at 3), chain_len never reaches chronic_from and
  the close_proposed arm is dead.
- Staged candidate decision text for W3 (k1; not filed, not ratified): "A telemetry verdict source joins
  Decision 201's layer. It claims a rec whose acceptance carries the back-validation assert contract. At
  merge it records a fix attempt and supplies no verdict, so the rec stays open; later it supplies a
  verdict record keyed to the fix sha, produced by a decision rule declared in a contract with its
  vectors. Amends Decision 201 points 1-2 (a verdict evaluated after the closing commit and keyed to the
  fix sha), which any wiring of telemetry proof needs, and leaves Decision 103's oracle rule intact: the
  rec's acceptance reads the record. The late closer takes update_rec's closing sha from the recorded
  fix attempt, never from the verdict record, and always supplies a record (it is a non-trailer caller).
  A holds is proposed for confirmation until the item reaches its last maturity rung."
