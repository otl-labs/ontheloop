# REPORT: W1 component 9 - Goodhart register (a counter series and a seeded drill for every failure_signal)

Plan: docs/plans/PLAN-w1-goodhart-register.yaml. Fixture item: pwi-goodhart-register (1 of 12 cap).
REPORT-ONLY (Decision 86 cl.2): nothing here is built, filed, ratified or flipped. Every table, verb, schema
change and drill named below describes a future build. The eight sibling W1 components (#1384, #1390,
#1394-#1399) are unmerged: they are read here as evidence at pinned heads, never cited as precedent.

## 0. Verdict

The charter asks that every loop component carry its own failure_signal, and every W1 item does: the pilot
schema requires one (scripts/checks/roadmap/_work_item_pilot_model.py:162). The Goodhart question is the next
one: when that signal looks good, how would we know it is the signal, and not the work, that improved?
Decision 196 clause 7 puts the same question to Tier A decisions ("how would we know we have overfit to
this?"); it binds there, and is used here only by analogy. This component answers it per detector and
measures the answers.

Measured, not argued:

- Every W1 failure_signal is lower-is-better, and every one is read as a promotion trigger by its own item.
  In 7 of 8 items the top-rung trigger (spot_check -> anomaly_triggered) names the item's own failure_signal
  metric; the eighth (#1390) reads "zero mismatches", which is its metric in other words (VP 1).
- The signals carry little protection against their own blind spots. 4 of 8 name a companion series read
  "beside" them, 1 of 8 (#1399's canary) names a seeded known-positive, and 0 of 8 top-rung triggers check
  the recall of the signal they promote on (VP 1). The maturity-ladder report (#1398, unmerged) measured the
  consequence: a monitor that sees half the errors reaches the top rung in 58 of 60 simulated components
  against 25 of 60 for a perfect one, because a signal that sees less breaches less.
- For every one of the 8 detectors there is a cheap path: a change that lowers the failure_signal without
  lowering the failures it names. Five of them come from the siblings' own reports (lost single-turn
  sessions, lumped signatures, consistent misclassification, filing less, rubber-stamp review); three are
  derived here (a common-mode harness, a regression path made blind by a stricter
  filer, a canary seeded where it cannot reach). In a model of each, the cheap path lowers the primary
  metric in 8 of 8, and in 8 of 8 it buys at least as much as a real halving of the failure rate (within
  0.001), so the primary metric alone cannot tell gaming from improvement (VP 3).
- The declared companions do not close that gap. Two of the four are computable series in the model
  (#1396's non-filing counts and #1397's unmeasurable_share); under their own item's cheap path one moves
  (#1396) and one does not (#1397). #1398's stale_rung_days and #1399's withheld share read quantities the
  cheap path does not touch (section 2.3). #1394's c2 guard is static and, as its text reads, passes under
  lumping (inferred).
- A counter per detector does. Each register row names a counter series from a source other than the
  primary's own numerator: a recall measured on seeded known-positives (five rows), a coverage share
  (capture against an out-of-band session count; the canary against the columns it must reach), or a
  consistency check the producer cannot satisfy by misclassifying (deliberation). In the model each counter falls under its floor on the cheap path in 8 of 8 and holds at
  or above its floor on a real improvement in 8 of 8 (VP 3). That split is the register's job.
- Nothing reads any of this today. No file under src/ or scripts/ other than the pilot model and its
  evaluator names failure_signal or Goodhart, and none of the six telemetry contracts carries a field that
  marks a seeded (drill) row (VP 2).

Staged, not decided: one register row per detector (section 2.2) and one deterministic SQL per daily run
(section 2.4) that reads the register, a daily (primary, counter) series per detector and a drill log, and
returns one verdict per detector: ok, dark, breach, counter_dark, counter_low, undrilled, blind,
diverging, upstream_unsound or unregistered. A detector is ok only when its own signal, counter and drill
are sound and so is every detector upstream of it; a signal or drill row with no register row reads
unregistered. It passes 159/159 vectors with one row per detector, 49 by raising on malformed input; each of
246 mutants, named or from a rule sweep by class (comparisons, clauses, rounding and precision, tolerance,
aggregates, branch order, propagation, undefined days, half splits, params), fails at least one vector,
and three more are equivalent; and it reads both sides of every boundary grid correctly (520 recall pairs,
9,800 exact-eps moves; VP 4).
The maturity-ladder controller would read the verdict as an input; what a non-ok
verdict does to a rung is k1.

What is settled is narrow: a failure_signal per item and its Goodhart reading (pilot model code; Decision 196
clause 7 by analogy, section 3), the register's place in the data plane (Decision 209 clause 2(a)), and the
measured facts above. How a verdict acts on the ladder (k1), how drills run (k2), where counters are
declared (k3), harm weights (k4), the fixture's edge home (k5) and the divergence rule (k6) all weigh
credible alternatives with no admissible precedent: they are parked as asked. None touches IAM, security
or a governed deploy, and none decides spend: k2 (a) and the reviewer drill carry recurring cost, which the
cost/egress component owns (R4).

## 1. Evidence (each row re-derivable; VP step in brackets)

| id | fact | anchor |
|---|---|---|
| e1 | The eight sibling items, read at their pinned heads, carry 8 failure_signals; the register has one row per item and each row's metric equals the item's failure_signal.metric verbatim [VP 1] | the siblings and register blocks |
| e2 | 4 of 8 failure_signals name a companion read "beside" them (#1396, #1397, #1398, #1399); 1 of 8 names a seeded, canary, drill or injected known-positive (#1399) [VP 1] | sibling fixtures |
| e3 | In 7 of 8 items the spot_check -> anomaly_triggered trigger names the item's own failure_signal metric (#1390 reads "zero mismatches"); 0 of 8 such triggers names a recall or a drill [VP 1] | sibling fixtures |
| e4 | The pilot FailureSignal model is three free-text fields (signal, metric, source): no counter, drill or recall field exists | scripts/checks/roadmap/_work_item_pilot_model.py:162 |
| e5 | No file under src/ or scripts/ other than the pilot model and its evaluator names failure_signal or goodhart; none of the six telemetry contracts has a field named for a synthetic, seeded or drill row (field keys; a prose mention is not a marker) [VP 2] | repository grep |
| e6 | In the per-detector model, each cheap path lowers the primary (8/8) by at least as much as a real halving of the failure rate (8/8 within 0.001); each counter falls under its floor on the cheap path (8/8) and holds on the real improvement (8/8); 2 declared companions are computable and 1 of them moves [VP 3] | section 2.3 |
| e7 | The staged verdict SQL passes 159 vectors with one row per detector, 49 by raising with the stated message, and reads both sides of 520 exact recall boundaries and 9,800 exact-eps moves correctly [VP 4] | sections 2.4, 2.5 |
| e8 | Each of 246 mutants of the verdict SQL (34 named, 212 from the rule sweep) replaces one unique site and fails at least one vector; three further mutants are equivalent and listed apart [VP 4] | section 2.6 |
| e9 | #1398 (unmerged) measured that a half-blind monitor reaches the top rung in 58 of 60 components at a 5% error rate, against 25 of 60 at recall 1.0, and named reviewer error (R3) and unequal harm (R6) for this register | REPORT-w1-maturity-ladder.md section 0 and Risks, at f6183430 |
| e10 | Four sibling reports carry a Goodhart paragraph naming a cheap path and a guard (#1394, #1395, #1396, #1397); #1384 section 4 names its own blind spot; five (#1394-#1398) name what they hand to this register | sibling reports at the pinned heads |
| e11 | The producer writes a hook row's severity from its exit code: 2 is error, 0 or a missing code is info, anything else warning; an info row is outside unmapped_failure_share's denominator | src/turn_capture/observations.py:361 |
| e12 | Decision 73 halt check: the reader's named verb ci_rca_open returned [] at 2026-10-03T17:44:32Z | Step 0 |

Sibling heads read by VP 1 (pinned by sha, so a later push to a sibling branch does not change the reading):

```yaml
siblings:
  - {pr: 1384, item: pwi-capture-producer-wiring, head: 1da1cc97f3522e5551ab32bcb41e3bb5b93cf685}  # pragma: allowlist secret
  - {pr: 1390, item: pwi-telemetry-reader-verbs, head: ab7aad7a36819a10069628577a55b2160cf8bd81}  # pragma: allowlist secret
  - {pr: 1394, item: pwi-friction-classifier, head: c6a47c5df5c1e50c7ab77730dadbe4456965772d}  # pragma: allowlist secret
  - {pr: 1395, item: pwi-deliberation-capture, head: 63b1a50a6926561aac5fb5a9c651c6faba581dc5}  # pragma: allowlist secret
  - {pr: 1396, item: pwi-rec-filing-dedupe, head: 51958a0ae48a700963fc5b1351fed00e6a1decec}  # pragma: allowlist secret
  - {pr: 1397, item: pwi-back-validation, head: 75ad2896d79d7d83f91ddcb619a183972a716351}  # pragma: allowlist secret
  - {pr: 1398, item: pwi-maturity-ladder-controller, head: f618343030ac07ffeab13c0ca2288577ad5d22db}  # pragma: allowlist secret
  - {pr: 1399, item: pwi-allow-list-transport, head: 03feaf12dda0bdb6ea8718ccdff85e931e83a942}  # pragma: allowlist secret
```

### 1.1 What the inventory shows

Every sibling chose its failure_signal for a good reason; four wrote a Goodhart paragraph for it and a
fifth named its own blind spot.
What none of them can do from inside its own item is measure whether its signal still sees: a signal is
read by the same item whose output it judges, it is lower-is-better, and its top-rung trigger rewards a low
value. That is Goodhart's law in its regressional and adversarial forms at once (Manheim and Garrabrant's
taxonomy): optimisation pressure on a proxy selects for the proxy's blind spots, whether anyone intends it
or not. Most of the cheap paths below are not cheats. A single-turn session lost on reclaim, a rule that
grew too broad, a reviewer who got tired: each lowers a signal by accident, and the ladder then promotes on it.

The industry answers are old and consistent: pair every optimised metric with a guardrail metric that moves
the other way when the first is gamed (A/B-testing practice), measure a detector's recall by injecting
known faults rather than by waiting for real ones (fault injection, canary tokens, honeypots, seeded defects
in software inspection), and never let a monitor's own output be the only evidence that the monitor works.
This register applies those three to each W1 detector.

## 2. Register design (what this item stages)

### 2.1 What a register row answers

For each detector, five questions:

1. What decides on it (decides): the ladder's rungs for its own component, plus whatever the component
   itself gates (a proof close, a filing, an egress batch).
2. What its cheapest improvement is that does not reduce the failure it names (cheap_path).
3. What the item already declares against that (declared_guard), read from its own fixture and report.
4. A counter series from a source other than the primary's numerator, oriented higher-is-better in
   [0, 1], with a floor (counter, counter_floor). Independence is the point: a counter computed by the same
   code from the same rows as the primary inherits its blind spot (risk R2).
5. A drill: a seeded known-positive whose detection the verdict reads as recall (drill). Five rows' counters
   are themselves drill recalls; the other three (capture, deliberation, allow-list) carry a counter series
   and a separate drill.

Plus its upstream detectors: the components whose output feeds the primary's numerator or denominator. A
detector is no sounder than its inputs.

### 2.2 The register

Counter floors are seed values (q2), the same 0.99 for every row. Each row's breach threshold is the one its
own item's top-rung trigger already names (0.05 for capture, friction, rec filing and back-validation;
0.01 for deliberation; 0 for reader verbs and the canary; the AOQL target for the ladder, its q3); the
register reads those, it does not set them. That is staged, not settled: the thresholds sit in eight unmerged
PRs, one is provisional and one is an open question, and whether breach belongs in a soundness verdict at
all is k1 (v).

```yaml
register:
  - detector: pwi-capture-producer-wiring
    pr: 1384
    metric: 'unfinalized_session_share: root sessions whose latest lifecycle row is not close 24 h after their last event'
    decides: its own rungs; and every W1 rate, because landed sessions are their denominator
    cheap_path: 'Lose the failing sessions: a single-turn session killed before SessionEnd lands no row, so it leaves numerator and denominator alike (its report, section 4)'
    declared_guard: none in the failure_signal; its report says the top rung must not promote on it until its q1 out-of-band denominator exists
    counter: 'landed_share: root sessions landed / root sessions counted out of band (the q1 denominator, from a source other than the capture runner)'
    counter_floor: 0.99
    drill: replay seeded transcripts killed at each lifecycle point, single-turn included; each must read as unfinalized
    upstream: []
  - detector: pwi-telemetry-reader-verbs
    pr: 1390
    metric: 'verb_rederivation_mismatch_rate: sampled sessions whose verb output differs from the harness re-derivation'
    decides: its own rungs; and every consumer of derived state, duration and roll-ups
    cheap_path: 'Common-mode re-derivation: a harness that shares the verb''s derivation agrees with its wrong outputs, so mismatches vanish'
    declared_guard: none for state, duration and roll-ups; the conflict stamp covers the dedupe leg only (its q2)
    counter: 'harness_kill_rate: seeded wrong derivations the shadow harness flags / seeded wrong derivations'
    counter_floor: 0.99
    drill: mutant derivations of state, duration and each roll-up injected into the shadow comparison
    upstream: [pwi-capture-producer-wiring]
  - detector: pwi-friction-classifier
    pr: 1394
    metric: 'unmapped_failure_share: non-info process_events labelled only by a generic rule, per classifier_version'
    decides: its own rungs; and the friction totals rec filing and back-validation read
    cheap_path: 'Lumping: the specific rule of one exact, registered signature absorbs failures of unrelated kinds (its q3: a name-only key merges unrelated Bash errors)'
    declared_guard: the c2 rules-file guard (no wildcard, registered signature), which is static and passes under lumping
    counter: 'drill_label_recall: seeded failure events of a kind no rule names that come out generic / seeded events'
    counter_floor: 0.99
    drill: synthetic process_events with known labels, including kinds no rule names, through session_friction_rollup
    upstream: [pwi-capture-producer-wiring, pwi-telemetry-reader-verbs]
  - detector: pwi-deliberation-capture
    pr: 1395
    metric: 'deliberation_drift_share: drifted/classified model_calls, undefined (never 0) if none; per producer, parser_version'
    decides: its own rungs; and the deliberation metrics T3.3 reads beside outcomes
    cheap_path: 'Consistent misclassification: a producer declares none and writes no thinking row while reasoning_tokens > 0 (its report names this lever)'
    declared_guard: visibility_missing drift and write-time NOT NULL; none with reasoning tokens has no read-side arm (its report)
    counter: 'visibility_consistency: 1 - (classified model_calls declaring none with reasoning_tokens > 0) / classified model_calls'
    counter_floor: 0.99
    drill: seeded model_call rows for each drift kind and for none-with-tokens, through the drift monitor
    upstream: [pwi-capture-producer-wiring, pwi-telemetry-reader-verbs]
  - detector: pwi-rec-filing-dedupe
    pr: 1396
    metric: rejected_share (operator marks covered, duplicate or reject) per 30 days per detector, beside run liveness
    decides: its own rungs; and which findings become recs (T3.3's false-positive rate, audit F-035)
    cheap_path: 'File only near-certain findings: a higher min_sessions stops spurious filings and silently drops real recurring findings'
    declared_guard: hold, suppressed and over_budget counts in every run record, read beside it; no threshold reads them
    counter: 'drill_file_recall: seeded recurring findings at the declared min_sessions that are proposed or filed / seeded findings'
    counter_floor: 0.99
    drill: a synthetic recurring finding per detector at the declared thresholds, fingerprinted under a drill detector_id
    upstream: [pwi-telemetry-reader-verbs, pwi-friction-classifier, pwi-deliberation-capture]
  - detector: pwi-back-validation
    pr: 1397
    metric: false_proof_rate (regressions within 28 days of a proof close / proof closes) beside unmeasurable_share, per 90 days
    decides: its own rungs; and whether a filer rec closes on proof (T3.4)
    cheap_path: 'Regression blindness: a stricter filer files regressions late or never, so false proofs stop being counted while unmeasurable_share holds'
    declared_guard: unmeasurable_share and the fails share beside it, which catch proving nothing but not a blind regression path
    counter: 'drill_regression_recall: seeded no-op fix closes whose re-seeded recurrence files a regression within 28 days / seeded closes'
    counter_floor: 0.99
    drill: known no-op fixes (its VP 3 kind) closed under a drill rec, with their friction re-seeded after the close
    upstream: [pwi-rec-filing-dedupe, pwi-friction-classifier]
  - detector: pwi-maturity-ladder-controller
    pr: 1398
    metric: 'est_escaped_share: audited wrong share x unreviewed share, rolling 30 days, per component above read_all'
    decides: every component's rung, so how much of each component's output a human reviews
    cheap_path: 'Rubber-stamp review: a reviewer confirms wrong outputs as correct, so the audited wrong share and the estimate fall (its R3)'
    declared_guard: stale_rung_days beside it (controller lag, not review quality); its drill gates the top rung only (its k5)
    counter: 'reviewer_drill_recall: seeded known-wrong outputs in the review stream marked wrong / seeded outputs'
    counter_floor: 0.99
    drill: known-wrong outputs mixed into each component's review sample, unmarked to the reviewer
    upstream: []
  - detector: pwi-allow-list-transport
    pr: 1399
    metric: 'canary_leaks: tokens of the seeded per-run canary row found in the egress batch; must be 0'
    decides: its own rungs; and whether an egress batch crosses to the control plane
    cheap_path: 'Seed where it cannot reach: a canary written only into columns the egress drops reads 0 whatever the egress leaks'
    declared_guard: the withheld share per column beside it (out-of-vocabulary enum values, not canary placement)
    counter: 'canary_coverage: non-denied string columns carrying the canary this run / non-denied string columns'
    counter_floor: 0.99
    drill: a positive-control run through a known-leaky egress (its mutant m01) that must report leaks above 0
    upstream: [pwi-telemetry-reader-verbs]
```

### 2.3 Cheap-path scenarios (VP 3)

Each scenario is a small deterministic world (DuckDB, `range()` only, no randomness) built from the
detector's own definition of its primary metric. It is evaluated three ways: the baseline (`cheap=0,
better=0`); the cheap path (`cheap=1`), which in five rows hides half of the failures the detector sees and in
three applies the stated change (a min_sessions of 8 instead of 3 for rec filing and back-validation; the
canary seeded only into denied columns); and a real improvement (`better=1`) that halves the true failure
rate (for the canary, one of two leaking columns fixed) and leaves detection alone. Where seeded drill
rows appear (`drill`), they are excluded from the primary, as a build must exclude them (k2). The
claim is not the numbers, which are constructed: it is the direction of each counter under each world.

```yaml
scenarios:
  params: {declared_tolerance: 0.01, as_good_as_real_tolerance: 0.001}
  cases:
    - detector: pwi-capture-producer-wiring
      world: "SELECT i, i % (20 * (1 + {better})) = 0 AS unfinalized, NOT (i % 20 = 0 AND {cheap} = 1 AND i % 40 = 0) AS landed FROM range(1000) t(i)"
      primary: "SELECT count(*) FILTER (WHERE landed AND unfinalized)::DOUBLE / count(*) FILTER (WHERE landed) FROM u"
      counter: "SELECT count(*) FILTER (WHERE landed)::DOUBLE / count(*) FROM u"
      declared: null
    - detector: pwi-telemetry-reader-verbs
      world: "SELECT i, i >= 1000 AS drill, (i >= 1000 OR i % (20 * (1 + {better})) = 0) AND NOT ({cheap} = 1 AND CASE WHEN i >= 1000 THEN i % 2 = 0 ELSE i % 40 = 0 END) AS mismatch FROM range(1040) t(i)"
      primary: "SELECT count(*) FILTER (WHERE mismatch)::DOUBLE / count(*) FROM u WHERE NOT drill"
      counter: "SELECT count(*) FILTER (WHERE mismatch)::DOUBLE / count(*) FROM u WHERE drill"
      declared: null
    - detector: pwi-friction-classifier
      world: "SELECT i, i >= 1000 AS drill, CASE WHEN (i >= 1000 OR i % (20 * (1 + {better})) = 0) AND NOT ({cheap} = 1 AND CASE WHEN i >= 1000 THEN i % 2 = 0 ELSE i % 40 = 0 END) THEN 'generic' WHEN i >= 1000 OR i % (20 * (1 + {better})) = 0 THEN 'specific:broad' ELSE 'specific:exact' END AS label FROM range(1040) t(i)"
      primary: "SELECT count(*) FILTER (WHERE label = 'generic')::DOUBLE / count(*) FROM u WHERE NOT drill"
      counter: "SELECT count(*) FILTER (WHERE label = 'generic')::DOUBLE / count(*) FROM u WHERE drill"
      declared: null
    - detector: pwi-deliberation-capture
      world: "SELECT i, CASE WHEN {cheap} = 1 AND i % 40 = 0 THEN 'none' ELSE 'summarized' END AS visibility, i % (20 * (1 + {better})) <> 0 AS has_thinking_row, 100 + i % 7 AS reasoning_tokens FROM range(1000) t(i)"
      primary: "SELECT count(*) FILTER (WHERE visibility IS NULL OR (visibility IN ('full', 'summarized') AND NOT has_thinking_row) OR (visibility = 'none' AND has_thinking_row))::DOUBLE / count(*) FROM u"
      counter: "SELECT 1 - count(*) FILTER (WHERE visibility = 'none' AND reasoning_tokens > 0)::DOUBLE / count(*) FROM u"
      declared: null
    - detector: pwi-rec-filing-dedupe
      world: "SELECT i, i >= 1000 AS drill, i >= 1000 OR i % (4 * (1 + {better})) <> 0 AS real, CASE WHEN i >= 1000 THEN 5 WHEN i % (4 * (1 + {better})) = 0 THEN 1 + i % 3 ELSE 1 + i % 10 END AS sessions, CASE WHEN {cheap} = 1 THEN 8 ELSE 3 END AS min_sessions FROM range(1040) t(i)"
      primary: "SELECT count(*) FILTER (WHERE sessions >= min_sessions AND NOT real)::DOUBLE / count(*) FILTER (WHERE sessions >= min_sessions) FROM u WHERE NOT drill"
      counter: "SELECT count(*) FILTER (WHERE sessions >= min_sessions)::DOUBLE / count(*) FROM u WHERE drill"
      declared: {series: "SELECT count(*) FILTER (WHERE sessions < min_sessions)::DOUBLE / count(*) FROM u WHERE NOT drill", worse_if: higher}
    - detector: pwi-back-validation
      world: "SELECT i, i >= 1000 AS drill, i >= 1000 OR i % (10 * (1 + {better})) = 0 AS no_op, CASE WHEN i >= 1000 THEN 5 ELSE 2 + i % 7 END AS recurring_sessions, CASE WHEN {cheap} = 1 THEN 8 ELSE 3 END AS min_sessions, i < 1000 AND i % 25 = 0 AS unmeasurable FROM range(1040) t(i)"
      primary: "SELECT count(*) FILTER (WHERE no_op AND recurring_sessions >= min_sessions)::DOUBLE / count(*) FROM u WHERE NOT drill AND NOT unmeasurable"
      counter: "SELECT count(*) FILTER (WHERE recurring_sessions >= min_sessions)::DOUBLE / count(*) FROM u WHERE drill"
      declared: {series: "SELECT count(*) FILTER (WHERE unmeasurable)::DOUBLE / count(*) FROM u WHERE NOT drill", worse_if: higher}
    - detector: pwi-maturity-ladder-controller
      world: "SELECT i, i >= 1000 AS drill, (i >= 1000 OR i % (20 * (1 + {better})) = 0) AND NOT ({cheap} = 1 AND CASE WHEN i >= 1000 THEN i % 2 = 0 ELSE i % 40 = 0 END) AS marked_wrong, 0.8 AS unreviewed_share FROM range(1040) t(i)"
      primary: "SELECT count(*) FILTER (WHERE marked_wrong)::DOUBLE / count(*) * any_value(unreviewed_share) FROM u WHERE NOT drill"
      counter: "SELECT count(*) FILTER (WHERE marked_wrong)::DOUBLE / count(*) FROM u WHERE drill"
      declared: null
    - detector: pwi-allow-list-transport
      world: "SELECT i, i < 12 AS denied, i = 40 OR (i = 60 AND {better} = 0) AS leaks, i < 12 OR {cheap} = 0 AS seeded FROM range(73) t(i)"
      primary: "SELECT count(*) FILTER (WHERE leaks AND seeded)::DOUBLE FROM u"
      counter: "SELECT count(*) FILTER (WHERE seeded)::DOUBLE / count(*) FROM u WHERE NOT denied"
      declared: null
```

| detector | primary base / cheap / real | counter base / cheap / real | declared companion |
|---|---|---|---|
| capture | 0.0500 / 0.0256 / 0.0250 | 1.000 / 0.975 / 1.000 | none |
| reader verbs | 0.0500 / 0.0250 / 0.0250 | 1.000 / 0.500 / 1.000 | none (conflict stamp: dedupe leg only) |
| friction | 0.0500 / 0.0250 / 0.0250 | 1.000 / 0.500 / 1.000 | c2 guard is static: passes under lumping |
| deliberation | 0.0500 / 0.0250 / 0.0250 | 1.000 / 0.975 / 1.000 | none for none-with-tokens |
| rec filing | 0.1215 / 0.0000 / 0.0566 | 1.000 / 0.000 / 1.000 | non-filing share 0.317 -> 0.750: moves (no threshold reads it) |
| back-validation | 0.0708 / 0.0125 / 0.0354 | 1.000 / 0.000 / 1.000 | unmeasurable_share 0.040 -> 0.040: does not move |
| maturity ladder | 0.0400 / 0.0200 / 0.0200 | 1.000 / 0.500 / 1.000 | stale_rung_days reads controller lag, not review |
| allow-list | 2 / 0 / 1 | 1.000 / 0.000 / 1.000 | withheld share reads enum values, not seeding |

Two results matter. First, in five rows the cheap path and the real improvement give the same primary
(within 0.0006), and in three (rec filing, back-validation, allow-list) the cheap path looks better than the
real improvement: a ladder promoting on the primary prefers the gamed component. Second, every counter
separates the two worlds, and no declared companion does except #1396's run-record counts, which nothing
reads with a threshold.

### 2.4 The verdict (staged)

One SQL per daily run. Inputs (all build-time objects; q1): `{register}` one row per detector (detector,
threshold, counter_floor, drill_min, drill_recall_min, upstream); `{signal}` one row per detector per day
(primary_value and counter_value, each NULL when undefined, never NaN or infinite); `{drills}` one row per
drill run (detector, day, injected, detected). Params are data, stamped on every verdict record (seeds,
q2): a 28-day window read as two 14-day halves, eps 0.01 on each half-mean. The output is one row per
detector (VP 4 fails a repeated row: unsound_reads counts rows).

One rule governs history: every read of `{signal}` or `{drills}` goes through the window (`w`, `dw`: after
today minus 28 days, up to and including today), the guards included. Rows dated after today or before the
window are never read: history older than the window, malformed or not, neither reads unregistered nor
raises (v31, v32, v39, v40, v42-v44). The one exception is a row with a NULL day, which cannot be placed in
time, so it raises wherever it is (e16, e17).

Retiring or onboarding a detector never halts the run (staged; k2). An in-window signal or drill row whose
detector has no register row reads unregistered, the same for both tables (v24, v33, v60-v62): a detector
retired today keeps reading unregistered for 27 days, until its last row leaves the window, and a drill that
lands before its register row reads unregistered until the row exists. Under k1 (iii) as staged, an
unregistered row other than the register's own drill blocks every promotion, so a retirement freezes
promotion for those 27 days. The alternatives are k2's: a retired_on marker on the register row so its
in-window history still resolves, or a halt with a stated 28-day retirement procedure.

Malformed input raises instead of deciding (fail loud, Decision 55 by analogy). Register: a duplicate or
NULL detector, a NULL field, a non-finite threshold, a counter floor outside [0, 1] (NaN and infinities
included), drill_min below 1 (every row must be drilled), a recall floor outside (0, 1] (NaN included), an
upstream that names no register row, or an upstream cycle (self-loop included). Signal, in the window: a
NULL detector, a non-finite primary, a counter outside [0, 1] (section 2.1 orients every counter in [0, 1];
NaN and infinities included) or a duplicate day. Drills, in the window: a NULL field, a negative detected
count or more detected than injected. A sentinel row makes the guard fire even when no detector row would be
returned (e13, e24). Each range edge is pinned on both sides: the edge value itself is legal (v57, v64,
v65, v80) and a value 1e-7 past it raises (e40-e44). When several classes are malformed at once, the
outer CASE order decides the message: register, upstream, cycle, signal, drill (e46-e49).

Blast radius (staged; k2): any guard firing raises the whole run. So one malformed in-window row (a NaN
primary, a counter of 1.0000001, a duplicate detector-day, more detected than injected) suppresses every
detector's verdict, every day, until the row is corrected or leaves the window, up to 27 days later (e45:
X's one bad counter on day -20 halts A too). The [0, 1] counter guard widens this: an undercounting
out-of-band denominator now halts the register rather than reading ok for one detector. On a raised day
there is no verdict at all; staged, the ladder reads that as no promotion and no demotion for any detector
(every rung holds) and the run's error is the alarm. Decision 55 says fail loud, not fail wide, and is cited
only by analogy, so this is a choice, parked under k2 with its alternatives.

```yaml
verdict:
  params: {window_days: 28, half_days: 14, eps_primary: 0.01, eps_counter: 0.01}
  sql: |
    WITH RECURSIVE
    edge AS (SELECT detector AS d, unnest(upstream) AS u FROM {register}),
    reach(d, u, depth) AS (
      SELECT d, u, 1 FROM edge
      UNION ALL
      SELECT r.d, e.u, r.depth + 1 FROM reach r JOIN edge e ON e.d = r.u
      WHERE r.depth < (SELECT count(*) FROM {register})
    ),
    w AS (SELECT * FROM {signal} WHERE day > {today} - {window_days} AND day <= {today}),
    dw AS (SELECT * FROM {drills} WHERE day > {today} - {window_days} AND day <= {today}),
    g AS (
      SELECT
        (SELECT count(*) - count(DISTINCT detector) FROM {register})
        + (SELECT count(*) FROM {register} WHERE threshold IS NULL OR counter_floor IS NULL
             OR drill_min IS NULL OR drill_recall_min IS NULL OR upstream IS NULL
             OR NOT isfinite(threshold) OR counter_floor < 0 OR counter_floor > 1
             OR drill_min < 1 OR drill_recall_min <= 0 OR drill_recall_min > 1) AS bad_register,
        (SELECT count(*) FROM edge WHERE u IS NULL OR u NOT IN (SELECT detector FROM {register})) AS bad_upstream,
        (SELECT count(*) FROM reach WHERE d = u) AS cycle,
        (SELECT count(*) FROM {signal} WHERE day IS NULL)
        + (SELECT count(*) FROM w WHERE detector IS NULL OR NOT isfinite(primary_value) OR counter_value < 0 OR counter_value > 1)
        + (SELECT count(*) FROM (SELECT detector, day FROM w GROUP BY ALL HAVING count(*) > 1)) AS bad_signal,
        (SELECT count(*) FROM {drills} WHERE day IS NULL)
        + (SELECT count(*) FROM dw WHERE detector IS NULL OR injected IS NULL OR detected IS NULL
             OR detected < 0 OR detected > injected) AS bad_drills
    ),
    s AS (
      SELECT r.detector, any_value(r.threshold) AS threshold, any_value(r.counter_floor) AS counter_floor,
             any_value(r.drill_min) AS drill_min, any_value(r.drill_recall_min) AS drill_recall_min,
             arg_max(w.primary_value, w.day) AS p_last,
             arg_max(w.counter_value, w.day) AS c_last,
             avg(w.primary_value) FILTER (WHERE w.day <= {today} - {half_days}) AS p_early,
             round(avg(w.primary_value) FILTER (WHERE w.day > {today} - {half_days}), 9) AS p_late,
             avg(w.counter_value) FILTER (WHERE w.day <= {today} - {half_days}) AS c_early,
             round(avg(w.counter_value) FILTER (WHERE w.day > {today} - {half_days}), 9) AS c_late
      FROM {register} r LEFT JOIN w ON w.detector = r.detector
      GROUP BY r.detector
    ),
    dr AS (
      SELECT r.detector, coalesce(sum(d.injected), 0) AS inj, coalesce(sum(d.detected), 0) AS det
      FROM {register} r LEFT JOIN dw d ON d.detector = r.detector
      GROUP BY r.detector
    ),
    own AS (
      SELECT s.detector,
        CASE
          WHEN s.p_last IS NULL THEN 'dark'
          WHEN s.p_last > s.threshold THEN 'breach'
          WHEN s.c_last IS NULL THEN 'counter_dark'
          WHEN s.c_last < s.counter_floor THEN 'counter_low'
          WHEN dr.inj < s.drill_min THEN 'undrilled'
          WHEN dr.det::DOUBLE / dr.inj < s.drill_recall_min THEN 'blind'
          WHEN s.p_late < round(s.p_early - {eps_primary}, 9) AND s.c_late < round(s.c_early - {eps_counter}, 9) THEN 'diverging'
          ELSE 'ok'
        END AS verdict,
        s.p_last, s.c_last, dr.inj, dr.det
      FROM s JOIN dr ON dr.detector = s.detector
    ),
    fin AS (
      SELECT o.detector,
        CASE WHEN o.verdict = 'ok' AND EXISTS (
               SELECT 1 FROM reach a JOIN own x ON x.detector = a.u WHERE a.d = o.detector AND x.verdict <> 'ok')
             THEN 'upstream_unsound' ELSE o.verdict END AS verdict
      FROM own o
      UNION ALL
      SELECT DISTINCT detector, 'unregistered' FROM (SELECT detector FROM w UNION ALL SELECT detector FROM dw)
      WHERE detector IS NOT NULL AND detector NOT IN (SELECT detector FROM {register})
      UNION ALL
      SELECT NULL, NULL FROM g WHERE g.bad_register + g.bad_upstream + g.cycle + g.bad_signal + g.bad_drills > 0
    )
    SELECT f.detector,
      CASE
        WHEN g.bad_register > 0 THEN error('malformed register: NULL field, duplicate detector, drill_min below 1, recall outside (0, 1], non-finite threshold or floor, or floor outside [0, 1]')
        WHEN g.bad_upstream > 0 THEN error('malformed register: upstream names an unregistered detector')
        WHEN g.cycle > 0 THEN error('malformed register: upstream cycle')
        WHEN g.bad_signal > 0 THEN error('malformed signal: NULL key, non-finite value, counter outside [0, 1] or duplicate detector day')
        WHEN g.bad_drills > 0 THEN error('malformed drill: NULL field, negative count or detected above injected')
        ELSE f.verdict
      END AS verdict
    FROM fin f CROSS JOIN g
```

| verdict | when (precedence top-down) | vectors |
|---|---|---|
| dark | no defined primary in the window | v02, v03, v21, v49 |
| breach | the latest defined primary exceeds the row's threshold (strictly) | v04, v23, v26, v27, v66, v90, v100 |
| counter_dark | no defined counter in the window | v06, v91 |
| counter_low | the latest defined counter is under its floor (strictly) | v07, v28, v29, v64, v67, v92 |
| undrilled | fewer than drill_min faults injected in the window | v09, v10, v13, v93, v101 |
| blind | detected / injected in the window below drill_recall_min | v11, v20, v23, v59, v68, v79, v102 |
| diverging | the primary's late-half mean fell by more than eps AND the counter's fell by more than eps | v15, v37, v46-v48, v69-v71, v73, v77, v78, v83, v84, v88, v94, v95, v98, v99, v107, v109 |
| upstream_unsound | own verdict ok, and some upstream, at any depth, is not ok (every non-ok verdict propagates) | v20, v21, v48, v49, v90-v93 |
| unregistered | a signal or drill row in the window whose detector has no register row (one row however many days) | v24, v33, v50, v60-v62 |
| ok | otherwise | v01, v05, v08, v12, v14, v16-v19, v22, v25, v30-v32, v34-v36, v38-v45, v51-v58, v63, v65, v72, v74-v76, v80-v82, v85-v87, v89, v96, v97, v103-v106, v108, v110 |

Staleness (staged; k1 (iv)): p_last and c_last are the latest defined values anywhere in the window,
however old, so a detector that last emitted 27 days ago decides on that value (v41); at 28 days it reads
dark. This fails open on the counter: a detector whose counter stops arriving keeps reading ok on its last
counter value for 27 days while its primary stays current (v63), and not emitting the counter is the
cheapest way to game it (R2). The latest value decides, not an extreme: a breach or a counter dip that
recovered inside the window does not count (v72, v76).
Each window edge is pinned: a drill dated today counts (v55) and one on the day the window opens does not
(v13); signal edges are v02 and v14.

Boundaries are exact to 9 decimal places, and each is pinned on both sides. Recall is read as detected /
injected against the floor; IEEE division is correctly rounded, so 7 of 25 at 0.28 meets it exactly (v34),
and 5e-7 under the floor is blind (v79); the legal edges drill_min 1, recall 1.0, a run that injected nothing
and one that detected nothing are pinned (v56-v59). A primary 1e-9 over its threshold breaches (v66) and a
counter 1e-9 under its floor is low (v67). For divergence, the late-half means and each eps-shifted bound
are rounded to 9 places before they are compared, so a fall of exactly eps is not diverging (v35, v38,
v51-v54), a fall larger than eps by 1e-8 is (v69, v70), and a fall larger than eps by 1e-11 is not (v81,
v82, v85, v86): that last pair is what "to 9 decimal places" means. Early means with 6- and 7-decimal
detail pin the bound's precision (v77, v78, v83, v84), and halves that are not constant pin the averages
against min, max and median (v73-v75, v87-v89). The early-half means are not rounded: the bound is rounded after the subtraction, so
rounding them changes nothing (verification r2's y12, y14 found no input that tells them apart). Each of
the four rounding sites is pinned on its own: one vector each (v51-v54) and one grid each. VP 4 checks
every (recall_min, injected) pair from 0.01-1.00 and 1-100 whose detected count is a whole number (520
pairs, 0 read blind; the multiplication form reads 13), every exact-eps fall on both series from 0.02-0.99
(9,604 detectors), and, because divergence needs both series, an exact-eps fall on one series while the
other falls clearly, from 0.02-0.99 on each side (98 and 98): 0 read diverging on all three, on every run.
Each grid has a near side too: one detected fewer reads blind for all 520 pairs, and a fall of eps + 1e-8
reads diverging for all 9,604, 98 and 98.
Without the rounding the counts are not even stable: over these grids DuckDB sums in parallel, so a
half-mean of equal values lands a few ulps either side from run to run. Removing any single rounding site
misread between 7 and 31 moves of its one-series grid across our runs, and removing all four misread 262
of the two-series grid on one run (1,450 with round 1's SQL).

The register's own drill injects one series a day whose name carries that day's date (drill-YYYY-MM-DD).
A day's drill passes only when that day's name reads unregistered. An earlier day's series still reads
unregistered inside the window (v33), so a fixed name would pass every day after the first, whether or not
the injection ran. The dated drill- series are excluded from the item's unsound_reads count. A detector's own non-ok verdict
outranks upstream_unsound (v23). breach outranks the counter, low or missing (v27, v100): a breach is
evidence the detector sees. A low counter outranks a missing drill (v28), too few faults outrank a low
recall on them (v101), and blind outranks diverging (v102), so each adjacent pair in the staged precedence
that can differ is pinned (k1 (ii)). Every non-ok upstream verdict propagates at full strength: dark,
breach, counter_dark, counter_low, undrilled, blind and diverging (v21, v90, v91, v92, v93, v20, v48; k1
(i)). Several detectors decide independently (v29). Inside a half-mean an undefined day is skipped, not read
as 0 (v96-v99), and each half starts and ends where stated (v37, v46, v94, v95). Params are read by name:
v103-v110 each override one param, so a build that reads eps_primary for eps_counter, or window_days as twice
half_days, fails a vector.

### 2.5 Vectors (VP 4)

```yaml
vectors:
  defaults:
    today: '2026-10-01'
    register_row: {counter_floor: 0.99, drill_min: 20, drill_recall_min: 0.9, threshold: 0.05, upstream: []}
  cases:
    - {id: v01, note: healthy detector over the full window, register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v02, note: signal only before the window, register: [{detector: A}], signal: [{detector: A, from: -40, to: -28, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: dark}}
    - {id: v03, note: primary undefined every day, register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: null, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: dark}}
    - {id: v04, note: latest primary above the threshold, register: [{detector: A}], signal: [{detector: A, from: -27, to: -1, primary: 0.02, counter: 1.0}, {detector: A, from: 0, to: 0, primary: 0.06, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: breach}}
    - {id: v05, note: primary equal to the threshold does not breach, register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.05, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v06, note: counter undefined every day, register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: null}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: counter_dark}}
    - {id: v07, note: latest counter below the floor, register: [{detector: A}], signal: [{detector: A, from: -27, to: -1, primary: 0.02, counter: 1.0}, {detector: A, from: 0, to: 0, primary: 0.02, counter: 0.98}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: counter_low}}
    - {id: v08, note: counter equal to the floor passes, register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 0.99}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v09, note: one fault short of drill_min, register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 19, detected: 19}], expected: {A: undrilled}}
    - {id: v10, note: no drill at all, register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [], expected: {A: undrilled}}
    - {id: v11, note: 17 of 20 injected faults detected is under the 0.9 recall floor, register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 17}], expected: {A: blind}}
    - {id: v12, note: 18 of 20 detected meets the recall floor, register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 18}], expected: {A: ok}}
    - {id: v13, note: a drill on the day the window opens is outside it, register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -28, injected: 20, detected: 20}], expected: {A: undrilled}}
    - {id: v14, note: the first day inside the window counts, register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -27, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v15, note: 'primary improves while the counter falls, both by more than eps, counter still above its floor', register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.04, counter: 1.0}, {detector: A, from: -13, to: 0, primary: 0.02, counter: 0.95}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: diverging}}
    - {id: v16, note: 'a real improvement, counter flat, is not diverging', register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.04, counter: 1.0}, {detector: A, from: -13, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v17, note: counter falls with primary flat is not diverging, register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.02, counter: 1.0}, {detector: A, from: -13, to: 0, primary: 0.02, counter: 0.95}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v18, note: moves of exactly eps are not diverging, register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.03, counter: 1.0}, {detector: A, from: -13, to: 0, primary: 0.02, counter: 0.99}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v19, note: 'only the late half has data, so no divergence is read', register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -13, to: 0, primary: 0.02, counter: 0.95}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v20, note: a healthy detector whose upstream is blind is upstream_unsound, register: [{detector: A}, {detector: B, upstream: [A]}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}, {detector: B, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 10}, {detector: B, day: -1, injected: 20, detected: 20}], expected: {A: blind, B: upstream_unsound}}
    - {id: v21, note: unsoundness propagates through a chain of upstreams, register: [{detector: A}, {detector: B, upstream: [A]}, {detector: C, upstream: [B]}], signal: [{detector: B, from: -27, to: 0, primary: 0.02, counter: 1.0}, {detector: C, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}, {detector: B, day: -1, injected: 20, detected: 20}, {detector: C, day: -1, injected: 20, detected: 20}], expected: {A: dark, B: upstream_unsound, C: upstream_unsound}}
    - {id: v22, note: sound upstreams leave a detector ok, register: [{detector: A}, {detector: B, upstream: [A]}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}, {detector: B, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}, {detector: B, day: -1, injected: 20, detected: 20}], expected: {A: ok, B: ok}}
    - {id: v23, note: a detector's own verdict outranks upstream_unsound, register: [{detector: A}, {detector: B, upstream: [A]}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}, {detector: B, from: -27, to: 0, primary: 0.09, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 10}, {detector: B, day: -1, injected: 20, detected: 20}], expected: {A: blind, B: breach}}
    - {id: v24, note: 'a series with no register row is unregistered, and the registered detector still decides', register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}, {detector: X, from: 0, to: 0, primary: 0.0, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok, X: unregistered}}
    - {id: v25, note: rows after today are ignored, register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}, {detector: A, from: 1, to: 1, primary: 0.5, counter: 0.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}, {detector: A, day: 1, injected: 20, detected: 0}], expected: {A: ok}}
    - {id: v26, note: the latest defined value is read when today's is undefined, register: [{detector: A}], signal: [{detector: A, from: -27, to: -2, primary: 0.02, counter: 1.0}, {detector: A, from: -1, to: -1, primary: 0.06, counter: 1.0}, {detector: A, from: 0, to: 0, primary: null, counter: null}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: breach}}
    - {id: v27, note: breach outranks a low counter, register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.06, counter: 0.5}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: breach}}
    - {id: v28, note: a low counter outranks a missing drill, register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 0.5}], drills: [], expected: {A: counter_low}}
    - {id: v29, note: several detectors decide independently, register: [{detector: A}, {detector: B}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}, {detector: B, from: -27, to: 0, primary: 0.02, counter: 0.9}], drills: [{detector: A, day: -1, injected: 20, detected: 20}, {detector: B, day: -1, injected: 20, detected: 20}], expected: {A: ok, B: counter_low}}
    - {id: v30, note: drills sum across the window, register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -20, injected: 10, detected: 9}, {detector: A, day: -2, injected: 10, detected: 9}], expected: {A: ok}}
    - {id: e01, register: [{detector: A}, {detector: A}], signal: [], drills: [], expected: error, raises: 'malformed register: NULL field, duplicate detector'}
    - {id: e02, register: [{detector: A, drill_min: 0}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [], expected: error, raises: drill_min below 1}
    - {id: e03, register: [{detector: A, drill_recall_min: 1.5}], signal: [], drills: [], expected: error, raises: 'recall outside (0, 1]'}
    - {id: e04, register: [{detector: A, drill_recall_min: 0}], signal: [], drills: [], expected: error, raises: 'recall outside (0, 1]'}
    - {id: e05, register: [{detector: A, threshold: null}], signal: [], drills: [], expected: error, raises: 'malformed register: NULL field'}
    - {id: e06, register: [{detector: B, upstream: [Z]}], signal: [], drills: [], expected: error, raises: upstream names an unregistered detector}
    - {id: e07, register: [{detector: A, upstream: [B]}, {detector: B, upstream: [A]}], signal: [], drills: [], expected: error, raises: upstream cycle}
    - {id: e08, register: [{detector: A, upstream: [A]}], signal: [], drills: [], expected: error, raises: upstream cycle}
    - {id: e09, register: [{detector: A}], signal: [{detector: A, from: 0, to: 0, primary: 0.02, counter: 1.0}, {detector: A, from: 0, to: 0, primary: 0.03, counter: 1.0}], drills: [], expected: error, raises: duplicate detector day}
    - {id: e10, register: [{detector: A}], signal: [{detector: null, from: 0, to: 0, primary: 0.02, counter: 1.0}], drills: [], expected: error, raises: 'malformed signal: NULL key'}
    - {id: e11, register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 10, detected: 11}], expected: error, raises: detected above injected}
    - {id: e12, register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: -1, detected: -2}], expected: error, raises: negative count}
    - {id: e13, note: a drill row with a NULL detector and no register row still raises through the sentinel, register: [], signal: [], drills: [{detector: null, day: -1, injected: 20, detected: 20}], expected: error, raises: 'malformed drill: NULL field'}
    - {id: e14, register: [{detector: A, upstream: null}], signal: [], drills: [], expected: error, raises: 'malformed register: NULL field'}
    - {id: e15, register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: null}], expected: error, raises: 'malformed drill: NULL field'}
    - {id: v31, note: a series dated after today is not read as unregistered (verifier o04), register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}, {detector: X, from: 5, to: 5, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v32, note: a retired detector's history before the window is not read as unregistered (verifier o05), register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}, {detector: X, from: -200, to: -200, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v33, note: 'yesterday''s dated drill series still reads unregistered and today''s is absent, so the day''s drill reads failed', register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}, {detector: drill-2026-09-30, from: -1, to: -1, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok, drill-2026-09-30: unregistered}}
    - {id: v34, note: detected exactly at recall_min x injected meets the floor (7 of 25 at 0.28; verifier f01), register: [{detector: A, drill_recall_min: 0.28}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 25, detected: 7}], expected: {A: ok}}
    - {id: v35, note: half-means that move by exactly eps on both series are not diverging (verifier o18), register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.05, counter: 0.96}, {detector: A, from: -13, to: 0, primary: 0.04, counter: 0.95}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v36, note: a primary fall under eps is not diverging even when the counter falls (verifier k12), register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.04, counter: 1.0}, {detector: A, from: -13, to: 0, primary: 0.035, counter: 0.95}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v37, note: the day the late half starts minus one belongs to the early half (verifier k13b), register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -15, primary: 0.02, counter: 1.0}, {detector: A, from: -14, to: -14, primary: 0.3, counter: 1.0}, {detector: A, from: -13, to: 0, primary: 0.02, counter: 0.95}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: diverging}}
    - {id: v38, note: an exact-eps move whose unrounded bound sits above the late mean on both series is not diverging, register: [{detector: A, threshold: 0.1, counter_floor: 0.1}], signal: [{detector: A, from: -27, to: -14, primary: 0.07, counter: 0.13}, {detector: A, from: -13, to: 0, primary: 0.06, counter: 0.12}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v39, note: a retired detector's drill history before the window is ignored (verifier w01), register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}, {detector: X, day: -200, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v40, note: 'a drill row dated after today is ignored, registered or not (verifier w02)', register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}, {detector: X, day: 5, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v41, note: 'staged staleness: the latest defined value anywhere in the window decides, however old (verifier w03; k6)', register: [{detector: A}], signal: [{detector: A, from: -27, to: -27, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v42, note: a malformed signal row before the window is ignored, register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}, {detector: A, from: -200, to: -200, primary: .nan, counter: .nan}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v43, note: a duplicate signal day before the window is ignored, register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}, {detector: A, from: -200, to: -200, primary: 0.02, counter: 1.0}, {detector: A, from: -200, to: -200, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v44, note: a malformed drill row before the window is ignored, register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}, {detector: A, day: -200, injected: 5, detected: 9}], expected: {A: ok}}
    - {id: v45, note: a counter fall under eps is not diverging even when the primary falls (verifier w05), register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.04, counter: 1.0}, {detector: A, from: -13, to: 0, primary: 0.02, counter: 0.995}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v46, note: the counter's early half ends on the day before the late half starts (verifier w06), register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -15, primary: 0.04, counter: 0.95}, {detector: A, from: -14, to: -14, primary: 0.04, counter: 1.0}, {detector: A, from: -13, to: 0, primary: 0.02, counter: 0.94}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: diverging}}
    - {id: v47, note: the counter's late half starts the day after the early half ends (verifier w07), register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.04, counter: 1.0}, {detector: A, from: -13, to: 0, primary: 0.02, counter: 0.9895}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: diverging}}
    - {id: v48, note: a diverging upstream propagates at full strength (staged k1 (i); verifier w09), register: [{detector: A, counter_floor: 0.9}, {detector: B, upstream: [A]}], signal: [{detector: A, from: -27, to: -14, primary: 0.04, counter: 1.0}, {detector: A, from: -13, to: 0, primary: 0.02, counter: 0.95}, {detector: B, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}, {detector: B, day: -1, injected: 20, detected: 20}], expected: {A: diverging, B: upstream_unsound}}
    - {id: v49, note: unsoundness propagates through a four-long chain (verifier w10), register: [{detector: A}, {detector: B, upstream: [A]}, {detector: C, upstream: [B]}, {detector: D, upstream: [C]}], signal: [{detector: B, from: -27, to: 0, primary: 0.02, counter: 1.0}, {detector: C, from: -27, to: 0, primary: 0.02, counter: 1.0}, {detector: D, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}, {detector: B, day: -1, injected: 20, detected: 20}, {detector: C, day: -1, injected: 20, detected: 20}, {detector: D, day: -1, injected: 20, detected: 20}], expected: {A: dark, B: upstream_unsound, C: upstream_unsound, D: upstream_unsound}}
    - {id: v50, note: an unregistered series on several days reads one row (verifier w15), register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}, {detector: X, from: -3, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok, X: unregistered}}
    - {id: v51, note: a primary late half falling exactly eps with the counter clearly falling is not diverging (verifier w20), register: [{detector: A, threshold: 1.0, counter_floor: 0.0}], signal: [{detector: A, from: -27, to: -14, primary: 0.02, counter: 0.45}, {detector: A, from: -13, to: 0, primary: 0.01, counter: 0.4}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v52, note: 'the same at 0.07, where the unrounded primary bound misreads (verifier w21)', register: [{detector: A, threshold: 1.0, counter_floor: 0.0}], signal: [{detector: A, from: -27, to: -14, primary: 0.07, counter: 0.45}, {detector: A, from: -13, to: 0, primary: 0.06, counter: 0.4}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v53, note: a counter late half falling exactly eps with the primary clearly falling is not diverging (verifier w22), register: [{detector: A, threshold: 1.0, counter_floor: 0.0}], signal: [{detector: A, from: -27, to: -14, primary: 0.45, counter: 0.02}, {detector: A, from: -13, to: 0, primary: 0.4, counter: 0.01}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v54, note: 'the same at 0.07, where the unrounded counter bound misreads (verifier w23)', register: [{detector: A, threshold: 1.0, counter_floor: 0.0}], signal: [{detector: A, from: -27, to: -14, primary: 0.45, counter: 0.07}, {detector: A, from: -13, to: 0, primary: 0.4, counter: 0.06}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v55, note: a drill dated today counts, register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: 0, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v56, note: drill_min 1 is allowed, register: [{detector: A, drill_min: 1}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 1, detected: 1}], expected: {A: ok}}
    - {id: v57, note: a recall floor of exactly 1 is allowed, register: [{detector: A, drill_recall_min: 1.0}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v58, note: a drill run that injected nothing is allowed, register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}, {detector: A, day: -2, injected: 0, detected: 0}], expected: {A: ok}}
    - {id: v59, note: 'a drill that detected nothing reads blind, not malformed', register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 0}], expected: {A: blind}}
    - {id: v60, note: 'retired today: X''s in-window drill reads unregistered and the run goes on (verifier z12)', register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}, {detector: X, day: -1, injected: 20, detected: 20}], expected: {A: ok, X: unregistered}}
    - {id: v61, note: 'retired today: X''s in-window signal reads unregistered until it leaves the window (verifier z13)', register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}, {detector: X, from: -27, to: -1, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok, X: unregistered}}
    - {id: v62, note: 'onboarding: B''s first drill lands before its register row and reads unregistered (verifier z14)', register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}, {detector: B, day: 0, injected: 20, detected: 20}], expected: {A: ok, B: unregistered}}
    - {id: v63, note: 'staged staleness on the counter: last defined 27 days ago still decides, so a silent counter reads ok (verifier z15; k1 (iv))', register: [{detector: A}], signal: [{detector: A, from: -27, to: -27, primary: 0.02, counter: 1.0}, {detector: A, from: -26, to: 0, primary: 0.02, counter: null}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v64, note: a counter of exactly 0 is in range (and low), register: [{detector: A}], signal: [{detector: A, from: -27, to: -1, primary: 0.02, counter: 1.0}, {detector: A, from: 0, to: 0, primary: 0.02, counter: 0.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: counter_low}}
    - {id: v65, note: a floor of exactly 0 and of exactly 1 are in range, register: [{detector: A, counter_floor: 0.0}, {detector: B, counter_floor: 1.0}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 0.5}, {detector: B, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}, {detector: B, day: -1, injected: 20, detected: 20}], expected: {A: ok, B: ok}}
    - {id: v66, note: primary 1e-9 above threshold breaches (verifier z01), register: [{detector: A}], signal: [{detector: A, from: -27, to: -1, primary: 0.02, counter: 1.0}, {detector: A, from: 0, to: 0, primary: 0.050000001, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: breach}}
    - {id: v67, note: counter 1e-9 under floor is low (verifier z02), register: [{detector: A}], signal: [{detector: A, from: -27, to: -1, primary: 0.02, counter: 1.0}, {detector: A, from: 0, to: 0, primary: 0.02, counter: 0.989999999}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: counter_low}}
    - {id: v68, note: recall 0.8995 under 0.9 is blind (verifier z03), register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 2000, detected: 1799}], expected: {A: blind}}
    - {id: v69, note: 'primary falls eps+1e-8 while the counter falls clearly: diverging (verifier z04)', register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.04, counter: 1.0}, {detector: A, from: -13, to: 0, primary: 0.02999999, counter: 0.95}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: diverging}}
    - {id: v70, note: 'counter falls eps+1e-8 while the primary falls clearly: diverging (verifier z05)', register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.04, counter: 1.0}, {detector: A, from: -13, to: 0, primary: 0.02, counter: 0.98999999}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: diverging}}
    - {id: v71, note: 'primary falls 0.0105, so a 3-decimal late mean would hide it: diverging (verifier z06)', register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.04, counter: 1.0}, {detector: A, from: -13, to: 0, primary: 0.0295, counter: 0.95}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: diverging}}
    - {id: v72, note: 'counter dipped under its floor mid-window and recovered: the latest value decides (verifier z07)', register: [{detector: A}], signal: [{detector: A, from: -27, to: -11, primary: 0.02, counter: 1.0}, {detector: A, from: -10, to: -10, primary: 0.02, counter: 0.5}, {detector: A, from: -9, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v73, note: 'counter late half not constant: the mean 0.971 falls, the median 1.0 would not (verifier z08)', register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.04, counter: 1.0}, {detector: A, from: -13, to: -10, primary: 0.02, counter: 0.9}, {detector: A, from: -9, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: diverging}}
    - {id: v74, note: 'primary late half not constant: the mean 0.0332 does not fall eps, the min would (verifier z09)', register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.04, counter: 1.0}, {detector: A, from: -13, to: -13, primary: 0.01, counter: 0.95}, {detector: A, from: -12, to: 0, primary: 0.035, counter: 0.95}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v75, note: 'counter early half not constant: the mean bound 0.948 is not crossed, the max bound would be (verifier z10)', register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -27, primary: 0.04, counter: 1.0}, {detector: A, from: -26, to: -14, primary: 0.04, counter: 0.955}, {detector: A, from: -13, to: 0, primary: 0.02, counter: 0.95}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v76, note: 'primary breached mid-window and recovered: the latest value decides (verifier z11)', register: [{detector: A}], signal: [{detector: A, from: -27, to: -6, primary: 0.02, counter: 1.0}, {detector: A, from: -5, to: -5, primary: 0.09, counter: 1.0}, {detector: A, from: -4, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v77, note: 'primary bound at a 6-decimal early mean: a fall of 0.010029 diverges (verifier z17)', register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.040049, counter: 1.0}, {detector: A, from: -13, to: 0, primary: 0.03002, counter: 0.95}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: diverging}}
    - {id: v78, note: 'counter bound at a 6-decimal early mean: a fall of 0.010029 diverges (verifier z18)', register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.04, counter: 0.990049}, {detector: A, from: -13, to: 0, primary: 0.02, counter: 0.98002}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: diverging}}
    - {id: v79, note: 'recall 5e-7 under its floor is blind (1,799,999 of 2,000,000 at 0.9)', register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 2000000, detected: 1799999}], expected: {A: blind}}
    - {id: v80, note: a recall floor just above 0 is allowed, register: [{detector: A, drill_recall_min: 5.0e-07}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v81, note: 'a primary fall larger than eps by 1e-11 is not diverging: the rule is exact to 9 decimal places', register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.04, counter: 1.0}, {detector: A, from: -13, to: 0, primary: 0.02999999999, counter: 0.95}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v82, note: a counter fall larger than eps by 1e-11 is not diverging, register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.04, counter: 1.0}, {detector: A, from: -13, to: 0, primary: 0.02, counter: 0.98999999999}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v83, note: 'primary bound at a 7-decimal early mean: a fall of 0.0100002 diverges', register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.0400004, counter: 1.0}, {detector: A, from: -13, to: 0, primary: 0.0300002, counter: 0.95}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: diverging}}
    - {id: v84, note: 'counter bound at a 7-decimal early mean: a fall of 0.0100002 diverges', register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.04, counter: 0.9900004}, {detector: A, from: -13, to: 0, primary: 0.02, counter: 0.9800002}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: diverging}}
    - {id: v85, note: 'primary early mean with 1e-11 detail: a fall of eps+1e-11 is not diverging', register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.04000000001, counter: 1.0}, {detector: A, from: -13, to: 0, primary: 0.03, counter: 0.95}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v86, note: 'counter early mean with 1e-11 detail: a fall of eps+1e-11 is not diverging', register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.04, counter: 0.99000000001}, {detector: A, from: -13, to: 0, primary: 0.02, counter: 0.98}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v87, note: 'primary early half not constant: the mean bound is not crossed, the max bound would be', register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -27, primary: 0.06, counter: 1.0}, {detector: A, from: -26, to: -14, primary: 0.04, counter: 1.0}, {detector: A, from: -13, to: 0, primary: 0.032, counter: 0.95}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v88, note: 'primary late half not constant: the mean falls past eps, the median and the max do not', register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.04, counter: 1.0}, {detector: A, from: -13, to: -8, primary: 0.02, counter: 0.95}, {detector: A, from: -7, to: 0, primary: 0.035, counter: 0.95}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: diverging}}
    - {id: v89, note: 'counter late half not constant: the mean does not fall eps, the min would', register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.04, counter: 1.0}, {detector: A, from: -13, to: -13, primary: 0.02, counter: 0.95}, {detector: A, from: -12, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v90, note: a breaching upstream propagates (staged k1 (i) full strength; verifier r4v01), register: [{detector: A}, {detector: B, upstream: [A]}], signal: [{detector: A, from: -27, to: 0, primary: 0.06, counter: 1.0}, {detector: B, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}, {detector: B, day: -1, injected: 20, detected: 20}], expected: {A: breach, B: upstream_unsound}}
    - {id: v91, note: a counter_dark upstream propagates (verifier r4v02), register: [{detector: A}, {detector: B, upstream: [A]}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: null}, {detector: B, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}, {detector: B, day: -1, injected: 20, detected: 20}], expected: {A: counter_dark, B: upstream_unsound}}
    - {id: v92, note: a counter_low upstream propagates (verifier r4v03), register: [{detector: A}, {detector: B, upstream: [A]}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 0.5}, {detector: B, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}, {detector: B, day: -1, injected: 20, detected: 20}], expected: {A: counter_low, B: upstream_unsound}}
    - {id: v93, note: an undrilled upstream propagates (verifier r4v04), register: [{detector: A}, {detector: B, upstream: [A]}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}, {detector: B, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: B, day: -1, injected: 20, detected: 20}], expected: {A: undrilled, B: upstream_unsound}}
    - {id: v94, note: 'the late half starts on day -13: a low primary that day carries the fall past eps (verifier r4v06)', register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.04, counter: 1.0}, {detector: A, from: -13, to: -13, primary: 0.01, counter: 0.95}, {detector: A, from: -12, to: 0, primary: 0.0305, counter: 0.95}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: diverging}}
    - {id: v95, note: 'the late half starts on day -13: a low counter that day carries the fall past eps (verifier r4v07)', register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.04, counter: 1.0}, {detector: A, from: -13, to: -13, primary: 0.02, counter: 0.9}, {detector: A, from: -12, to: 0, primary: 0.02, counter: 0.9905}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: diverging}}
    - {id: v96, note: 'primary undefined for the last 7 days: the late mean is over defined days only, so no eps fall (verifier r4v08)', register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.04, counter: 1.0}, {detector: A, from: -13, to: -7, primary: 0.035, counter: 0.95}, {detector: A, from: -6, to: 0, primary: null, counter: 0.95}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v97, note: 'counter undefined for the last 7 days: the late mean is over defined days only, so no eps fall (verifier r4v09)', register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.04, counter: 1.0}, {detector: A, from: -13, to: -7, primary: 0.02, counter: 0.995}, {detector: A, from: -6, to: 0, primary: 0.02, counter: null}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v98, note: 'primary undefined for 7 early days: the early mean is over defined days only, so the fall diverges (verifier r4v10)', register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -21, primary: 0.04, counter: 1.0}, {detector: A, from: -20, to: -14, primary: null, counter: 1.0}, {detector: A, from: -13, to: 0, primary: 0.02, counter: 0.95}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: diverging}}
    - {id: v99, note: 'counter undefined for 7 early days: the early mean is over defined days only, so the fall diverges (verifier r4v11)', register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -21, primary: 0.04, counter: 1.0}, {detector: A, from: -20, to: -14, primary: 0.04, counter: null}, {detector: A, from: -13, to: 0, primary: 0.02, counter: 0.95}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: diverging}}
    - {id: v100, note: breach outranks a missing counter (k1 (ii) as staged; verifier r4v12), register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.06, counter: null}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: breach}}
    - {id: v101, note: undrilled outranks a low recall on too few faults (k1 (ii) as staged; verifier r4v13), register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 10, detected: 5}], expected: {A: undrilled}}
    - {id: v102, note: blind outranks diverging (k1 (ii) as staged; verifier r4v14), register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.04, counter: 1.0}, {detector: A, from: -13, to: 0, primary: 0.02, counter: 0.95}], drills: [{detector: A, day: -1, injected: 20, detected: 10}], expected: {A: blind}}
    - {id: v103, note: 'eps_counter 0.02: a counter fall of 0.015 is under its own eps (params override; verifier r4p1)', params: {eps_counter: 0.02}, register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.04, counter: 1.0}, {detector: A, from: -13, to: 0, primary: 0.02, counter: 0.985}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v104, note: 'eps_primary 0.02: a primary fall of 0.015 is under its own eps (params override; verifier r4p2)', params: {eps_primary: 0.02}, register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.04, counter: 1.0}, {detector: A, from: -13, to: 0, primary: 0.025, counter: 0.95}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v105, note: 'window_days 30: a drill on day -29 is inside the window (params override; verifier r4p3)', params: {window_days: 30}, register: [{detector: A}], signal: [{detector: A, from: -29, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -29, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v106, note: 'window_days 30: a signal defined only on day -29 still decides (params override)', params: {window_days: 30}, register: [{detector: A}], signal: [{detector: A, from: -29, to: -29, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v107, note: 'half_days 10: the primary late half is days -9..0, whose fall of 0.011 diverges (params override)', params: {half_days: 10}, register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -10, primary: 0.04, counter: 1.0}, {detector: A, from: -9, to: 0, primary: 0.029, counter: 0.95}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: diverging}}
    - {id: v108, note: 'half_days 10: the primary early half is days -27..-10, whose mean 0.0411 is not crossed (params override)', params: {half_days: 10}, register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -14, primary: 0.05, counter: 1.0}, {detector: A, from: -13, to: -10, primary: 0.01, counter: 1.0}, {detector: A, from: -9, to: 0, primary: 0.035, counter: 0.95}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: v109, note: 'half_days 10: the counter late half is days -9..0, whose fall of 0.011 diverges (params override)', params: {half_days: 10}, register: [{detector: A, counter_floor: 0.9}], signal: [{detector: A, from: -27, to: -10, primary: 0.04, counter: 1.0}, {detector: A, from: -9, to: 0, primary: 0.02, counter: 0.989}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: diverging}}
    - {id: v110, note: 'half_days 10: the counter early half is days -27..-10, whose mean is not crossed (params override)', params: {half_days: 10}, register: [{detector: A, counter_floor: 0.5}], signal: [{detector: A, from: -27, to: -14, primary: 0.04, counter: 1.0}, {detector: A, from: -13, to: -10, primary: 0.04, counter: 0.6}, {detector: A, from: -9, to: 0, primary: 0.02, counter: 0.905}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: {A: ok}}
    - {id: e16, note: a signal row with a NULL day raises (verifier k01), register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}, {detector: A, from: null, to: null, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: error, raises: 'malformed signal: NULL key'}
    - {id: e17, note: a drill row with a NULL day raises (verifier k02), register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}, {detector: A, day: null, injected: 20, detected: 20}], expected: error, raises: 'malformed drill: NULL field'}
    - {id: e18, note: a drill row with a NULL injected raises (verifier k03), register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}, {detector: A, day: -2, injected: null, detected: 0}], expected: error, raises: 'malformed drill: NULL field'}
    - {id: e19, note: a negative detected count raises (verifier k04), register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}, {detector: A, day: -2, injected: 5, detected: -1}], expected: error, raises: negative count}
    - {id: e20, note: a NULL upstream entry raises (verifier k06), register: [{detector: A}, {detector: B, upstream: [null]}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}, {detector: B, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}, {detector: B, day: -1, injected: 20, detected: 20}], expected: error, raises: upstream names an unregistered detector}
    - {id: e21, note: a NULL counter_floor raises (verifier k08), register: [{detector: A, counter_floor: null}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: error, raises: 'malformed register: NULL field'}
    - {id: e22, note: a NULL drill_min raises (verifier k09), register: [{detector: A, drill_min: null}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: error, raises: 'malformed register: NULL field'}
    - {id: e23, note: a NULL drill_recall_min raises (verifier k10), register: [{detector: A, drill_recall_min: null}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: error, raises: 'malformed register: NULL field'}
    - {id: e24, note: an empty register with a NULL-detector signal row still raises through the sentinel (verifier k21), register: [], signal: [{detector: null, from: 0, to: 0, primary: 0.02, counter: 1.0}], drills: [], expected: error, raises: 'malformed signal: NULL key'}
    - {id: e25, note: a 3-cycle raises (verifier w11), register: [{detector: A, upstream: [C]}, {detector: B, upstream: [A]}, {detector: C, upstream: [B]}], signal: [], drills: [], expected: error, raises: upstream cycle}
    - {id: e26, note: a NaN counter in the window raises (verifier w17), register: [{detector: A}], signal: [{detector: A, from: -27, to: -1, primary: 0.02, counter: 1.0}, {detector: A, from: 0, to: 0, primary: 0.02, counter: .nan}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: error, raises: non-finite value}
    - {id: e27, note: a NaN primary in the window raises (verifier w18), register: [{detector: A}], signal: [{detector: A, from: -27, to: -1, primary: 0.02, counter: 1.0}, {detector: A, from: 0, to: 0, primary: .nan, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: error, raises: non-finite value}
    - {id: e28, note: an infinite counter in the window raises, register: [{detector: A}], signal: [{detector: A, from: -27, to: -1, primary: 0.02, counter: 1.0}, {detector: A, from: 0, to: 0, primary: 0.02, counter: .inf}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: error, raises: non-finite value}
    - {id: e29, note: a minus-infinite primary in the window raises, register: [{detector: A}], signal: [{detector: A, from: -27, to: -1, primary: 0.02, counter: 1.0}, {detector: A, from: 0, to: 0, primary: -.inf, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: error, raises: non-finite value}
    - {id: e30, note: a NaN threshold raises, register: [{detector: A, threshold: .nan}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: error, raises: non-finite threshold or floor}
    - {id: e31, note: a minus-infinite counter_floor raises, register: [{detector: A, counter_floor: -.inf}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: error, raises: non-finite threshold or floor}
    - {id: e32, note: a NaN recall floor raises, register: [{detector: A, drill_recall_min: .nan}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: error, raises: 'recall outside (0, 1]'}
    - {id: e33, note: a NULL threshold raises, register: [{detector: A, threshold: null}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: error, raises: 'malformed register: NULL field'}
    - {id: e34, note: a NULL register detector raises, register: [{detector: null}], signal: [], drills: [], expected: error, raises: 'malformed register: NULL field'}
    - {id: e35, note: a drill row with a NULL detector in the window raises, register: [{detector: A}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}, {detector: null, day: -2, injected: 1, detected: 1}], expected: error, raises: 'malformed drill: NULL field'}
    - {id: e36, note: a counter above 1 raises (verifier z16), register: [{detector: A}], signal: [{detector: A, from: -27, to: -1, primary: 0.02, counter: 1.0}, {detector: A, from: 0, to: 0, primary: 0.02, counter: 1.7}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: error, raises: 'counter outside [0, 1]'}
    - {id: e37, note: a counter below 0 raises, register: [{detector: A}], signal: [{detector: A, from: -27, to: -1, primary: 0.02, counter: 1.0}, {detector: A, from: 0, to: 0, primary: 0.02, counter: -0.1}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: error, raises: 'counter outside [0, 1]'}
    - {id: e38, note: a counter floor above 1 raises, register: [{detector: A, counter_floor: 1.01}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: error, raises: 'floor outside [0, 1]'}
    - {id: e39, note: a counter floor below 0 raises, register: [{detector: A, counter_floor: -0.01}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: error, raises: 'floor outside [0, 1]'}
    - {id: e40, note: a recall floor 1e-7 above 1 raises, register: [{detector: A, drill_recall_min: 1.0000001}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: error, raises: 'recall outside (0, 1]'}
    - {id: e41, note: a counter floor 1e-7 below 0 raises, register: [{detector: A, counter_floor: -1.0e-07}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: error, raises: 'floor outside [0, 1]'}
    - {id: e42, note: a counter floor 1e-7 above 1 raises, register: [{detector: A, counter_floor: 1.0000001}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: error, raises: 'floor outside [0, 1]'}
    - {id: e43, note: a counter 1e-7 below 0 raises, register: [{detector: A}], signal: [{detector: A, from: -27, to: -1, primary: 0.02, counter: 1.0}, {detector: A, from: 0, to: 0, primary: 0.02, counter: -1.0e-07}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: error, raises: 'counter outside [0, 1]'}
    - {id: e44, note: a counter 1e-7 above 1 raises, register: [{detector: A}], signal: [{detector: A, from: -27, to: -1, primary: 0.02, counter: 1.0}, {detector: A, from: 0, to: 0, primary: 0.02, counter: 1.0000001}], drills: [{detector: A, day: -1, injected: 20, detected: 20}], expected: error, raises: 'counter outside [0, 1]'}
    - {id: e45, note: 'staged blast radius: one out-of-range counter of X on day -20 halts every detector today, A included (verifier r4v05; k2)', register: [{detector: A}, {detector: X}], signal: [{detector: A, from: -27, to: 0, primary: 0.02, counter: 1.0}, {detector: X, from: -27, to: -21, primary: 0.02, counter: 1.0}, {detector: X, from: -20, to: -20, primary: 0.02, counter: 1.0000001}, {detector: X, from: -19, to: 0, primary: 0.02, counter: 1.0}], drills: [{detector: A, day: -1, injected: 20, detected: 20}, {detector: X, day: -1, injected: 20, detected: 20}], expected: error, raises: 'counter outside [0, 1]'}
    - {id: e46, note: a register defect outranks an unregistered upstream (verifier r4e01), register: [{detector: A, threshold: null}, {detector: B, upstream: [Z]}], signal: [], drills: [], expected: error, raises: 'malformed register: NULL field'}
    - {id: e47, note: an unregistered upstream outranks a cycle (verifier r4e02), register: [{detector: A, upstream: [A, Z]}], signal: [], drills: [], expected: error, raises: upstream names an unregistered detector}
    - {id: e48, note: a cycle outranks a malformed signal (verifier r4e03), register: [{detector: A, upstream: [A]}], signal: [{detector: A, from: 0, to: 0, primary: .nan, counter: 1.0}], drills: [], expected: error, raises: upstream cycle}
    - {id: e49, note: a malformed signal outranks a malformed drill (verifier r4e04), register: [{detector: A}], signal: [{detector: A, from: 0, to: 0, primary: .nan, counter: 1.0}], drills: [{detector: A, day: -1, injected: 1, detected: 2}], expected: error, raises: malformed signal}
```

### 2.6 Mutants (VP 4)

Each mutant replaces one exact substring of the verdict SQL (it must occur exactly once) and must fail at
least one vector. m01-m34 are named. m35-m246 are a rule sweep, generated by class rather than by instance
after verification rounds 1-4 found survivors on sites mirroring killed ones. Its classes:

- every comparison flipped (< and <=, > and >=, = and <>);
- every OR or AND clause dropped, every CASE branch and every guard term dropped;
- every round() removed, and each re-rounded to 2, 4, 6 and 12 places;
- a tolerance of 1e-6 and 1e-3, in both directions, on every comparison against a DOUBLE (verdict and guard);
- every aggregate swapped: arg_max for arg_min, max and min; each half-mean avg for min, max and median;
  each drill sum for max and min;
- arithmetic: the eps sign, the recall division, the recursion step;
- every window bound and guard read widened to all history, and the mirror of each named mutant on the other
  series;
- the order of every WHEN in both CASEs: each adjacent pair swapped and each line moved to the top (the
  staged precedence of k1 (ii) and the error precedence);
- the propagation predicate narrowed to skip each upstream verdict in turn (the staged full strength of k1
  (i));
- an undefined day read as 0 inside each half-mean, and each half split moved a day either way;
- each param read as its sibling (eps_primary and eps_counter, window_days as twice half_days and half_days
  as half of window_days). Under the seed params these are equivalent, so a vector may override the params
  (v103-v110), as the build's versioned params will.

Each pass's survivors were either pinned by a new vector (v55-v59 legal edges; v66-v89 near sides,
precision and non-constant halves; e40-e44 range edges; v90-v110 and e46-e49 precedence, propagation,
undefined days, half splits and params) or, where no input could tell them apart, removed from the SQL as
redundant: a NULL-detector clause the duplicate count already catches, arg_max FILTERs
DuckDB's arg_max already applies, a negative-injected clause the detected clauses imply, the early-half
rounding, and the non-finite checks on the counter and its floor that the [0, 1] range already makes. One
Three mutants stay equivalent and are listed apart. q01: reading the reach depth bound inclusively only
lets the recursion run one step further, and a path that long already contains a cycle the guard catches
(verification r3 checked it on every graph of 1-3 detectors and 400 random graphs of 4-5). q02 and q03:
swapping dark with breach, or counter_dark with counter_low, cannot change a verdict, because when the
latest value is NULL the comparison in the other branch is NULL too. Three further equivalents the
verifiers found by the same arguments are not generated by the sweep: a recursion base depth of 0, `s JOIN
dr` read as a LEFT JOIN, and the coalesce on detected dropped (detected is NULL only when injected is 0,
which reads undrilled first). VP 4 counts the kills and the unique sites; the table lists
every killing vector.

```yaml
mutants:
  - {id: m01, what: breach on equality, old: WHEN s.p_last > s.threshold, new: WHEN s.p_last >= s.threshold}
  - {id: m02, what: counter floor read as strict, old: WHEN s.c_last < s.counter_floor, new: WHEN s.c_last <= s.counter_floor}
  - {id: m03, what: recall floor read as strict, old: 'WHEN dr.det::DOUBLE / dr.inj < s.drill_recall_min', new: 'WHEN dr.det::DOUBLE / dr.inj <= s.drill_recall_min'}
  - {id: m04, what: drill window opened one day early, old: 'dw AS (SELECT * FROM {drills} WHERE day > {today}', new: 'dw AS (SELECT * FROM {drills} WHERE day >= {today}'}
  - {id: m05, what: no upstream propagation, old: o.verdict = 'ok' AND EXISTS, new: o.verdict = 'ok' AND FALSE AND EXISTS}
  - {id: m06, what: direct upstream only, old: JOIN own x ON x.detector = a.u WHERE a.d = o.detector, new: JOIN own x ON x.detector = a.u WHERE a.d = o.detector AND a.depth = 1}
  - {id: m07, what: unregistered series dropped, old: "WHERE detector IS NOT NULL AND detector NOT IN (SELECT detector FROM {register})\n  UNION ALL", new: "WHERE FALSE AND detector NOT IN (SELECT detector FROM {register})\n  UNION ALL"}
  - {id: m08, what: duplicate register rows allowed, old: '(SELECT count(*) - count(DISTINCT detector) FROM {register})', new: '0'}
  - {id: m09, what: no cycle guard, old: WHEN g.cycle > 0 THEN, new: WHEN FALSE THEN}
  - {id: m10, what: no sentinel row, so a malformed input with no detector row returns empty: null, old: 'SELECT NULL, NULL FROM g WHERE', new: 'SELECT NULL, NULL FROM g WHERE FALSE AND'}
  - {id: m11, what: only today's value read, old: 'arg_max(w.primary_value, w.day) AS p_last', new: 'max(w.primary_value) FILTER (WHERE w.day = {today}) AS p_last'}
  - {id: m12, what: divergence on the primary alone, old: 's.p_late < round(s.p_early - {eps_primary}, 9) AND s.c_late < round(s.c_early - {eps_counter}, 9)', new: 's.p_late < round(s.p_early - {eps_primary}, 9)'}
  - {id: m13, what: divergence on the counter alone, old: 's.p_late < round(s.p_early - {eps_primary}, 9) AND s.c_late < round(s.c_early - {eps_counter}, 9)', new: 's.c_late < round(s.c_early - {eps_counter}, 9)'}
  - {id: m14, what: future signal rows read, old: '{signal} WHERE day > {today} - {window_days} AND day <= {today})', new: '{signal} WHERE day > {today} - {window_days})'}
  - {id: m15, what: no drill_min, old: WHEN dr.inj < s.drill_min THEN, new: WHEN dr.inj < 1 THEN}
  - {id: m16, what: detected above injected allowed, old: OR detected > injected, new: ''}
  - {id: m17, what: upstream overrides a detector's own verdict, old: CASE WHEN o.verdict = 'ok' AND EXISTS, new: CASE WHEN EXISTS}
  - {id: m18, what: drills read without a window, old: 'dw AS (SELECT * FROM {drills} WHERE day > {today} - {window_days} AND day <= {today})', new: 'dw AS (SELECT * FROM {drills})'}
  - {id: m19, what: counter checked before breach, old: "WHEN s.p_last > s.threshold THEN 'breach'\n      WHEN s.c_last IS NULL THEN 'counter_dark'\n      WHEN s.c_last < s.counter_floor THEN 'counter_low'", new: "WHEN s.c_last IS NULL THEN 'counter_dark'\n      WHEN s.c_last < s.counter_floor THEN 'counter_low'\n      WHEN s.p_last > s.threshold THEN 'breach'"}
  - {id: m20, what: no drill-recall bounds, old: OR drill_min < 1 OR drill_recall_min <= 0 OR drill_recall_min > 1, new: OR drill_min < 1}
  - {id: m21, what: in-window drill rows of an unregistered detector not read as unregistered, old: SELECT detector FROM w UNION ALL SELECT detector FROM dw, new: SELECT detector FROM w}
  - {id: m22, what: unregistered branch reads all history, old: SELECT detector FROM w UNION ALL SELECT detector FROM dw, new: 'SELECT detector FROM {signal} UNION ALL SELECT detector FROM {drills}'}
  - {id: m23, what: signal NULL day accepted, old: '(SELECT count(*) FROM {signal} WHERE day IS NULL)', new: '0'}
  - {id: m24, what: drill NULL day accepted, old: '(SELECT count(*) FROM {drills} WHERE day IS NULL)', new: '0'}
  - {id: m25, what: drill NULL injected accepted, old: OR injected IS NULL OR detected IS NULL, new: OR detected IS NULL}
  - {id: m26, what: negative detected accepted, old: OR detected < 0 OR detected > injected, new: OR detected > injected}
  - {id: m27, what: NULL upstream entry accepted, old: WHERE u IS NULL OR u NOT IN, new: WHERE u NOT IN}
  - {id: m28, what: NULL counter_floor accepted, old: OR counter_floor IS NULL, new: ''}
  - {id: m29, what: NULL drill_min accepted, old: OR drill_min IS NULL OR drill_recall_min IS NULL, new: OR drill_recall_min IS NULL}
  - {id: m30, what: NULL drill_recall_min accepted, old: OR drill_recall_min IS NULL OR upstream IS NULL, new: OR upstream IS NULL}
  - {id: m31, what: sentinel ignores a malformed signal, old: g.cycle + g.bad_signal + g.bad_drills, new: g.cycle + g.bad_drills}
  - {id: m32, what: no eps on the primary, old: 's.p_late < round(s.p_early - {eps_primary}, 9)', new: s.p_late < s.p_early}
  - {id: m33, what: early half drops its last day, old: 'w.primary_value) FILTER (WHERE w.day <= {today} - {half_days}) AS p_early', new: 'w.primary_value) FILTER (WHERE w.day < {today} - {half_days}) AS p_early'}
  - {id: m34, what: recall floor read by multiplication, old: 'WHEN dr.det::DOUBLE / dr.inj < s.drill_recall_min', new: WHEN dr.det < s.drill_recall_min * dr.inj}
  - {id: m35, what: 'comparison = read as <> in: SELECT r.d, e.u, r.depth + 1 FROM reach r JOIN edge e ON e.d = r.u', old: e.d = r.u, new: e.d <> r.u}
  - {id: m36, what: 'comparison > read as >= in: w AS (SELECT * FROM {signal} WHERE day > {today} - {window_days} AND day <= {today}),', old: 'l} WHERE day > {today} - {w', new: 'l} WHERE day >= {today} - {w'}
  - {id: m37, what: 'comparison <= read as < in: w AS (SELECT * FROM {signal} WHERE day > {today} - {window_days} AND day <= {today}),', old: 'ays} AND day <= {today}),

    dw', new: 'ays} AND day < {today}),

    dw'}
  - {id: m38, what: 'comparison <= read as < in: dw AS (SELECT * FROM {drills} WHERE day > {today} - {window_days} AND day <= {today}),', old: 'ays} AND day <= {today}),

    g ', new: 'ays} AND day < {today}),

    g '}
  - {id: m39, what: 'comparison < read as <= in: OR NOT isfinite(threshold) OR counter_floor < 0 OR counter_floor > 1', old: oor < 0 O, new: oor <= 0 O}
  - {id: m40, what: 'comparison > read as >= in: OR NOT isfinite(threshold) OR counter_floor < 0 OR counter_floor > 1', old: "oor > 1\n ", new: "oor >= 1\n "}
  - {id: m41, what: 'comparison < read as <= in: OR drill_min < 1 OR drill_recall_min <= 0 OR drill_recall_min > 1) AS bad_register,', old: min < 1 O, new: min <= 1 O}
  - {id: m42, what: 'comparison <= read as < in: OR drill_min < 1 OR drill_recall_min <= 0 OR drill_recall_min > 1) AS bad_register,', old: min <= 0 O, new: min < 0 O}
  - {id: m43, what: 'comparison > read as >= in: OR drill_min < 1 OR drill_recall_min <= 0 OR drill_recall_min > 1) AS bad_register,', old: 'min > 1) ', new: 'min >= 1) '}
  - {id: m44, what: 'comparison = read as <> in: (SELECT count(*) FROM reach WHERE d = u) AS cycle,', old: 'E d = u) ', new: 'E d <> u) '}
  - {id: m45, what: 'comparison < read as <= in: + (SELECT count(*) FROM w WHERE detector IS NULL OR NOT isfinite(primary_value) OR counter', old: lue < 0 O, new: lue <= 0 O}
  - {id: m46, what: 'comparison > read as >= in: + (SELECT count(*) FROM w WHERE detector IS NULL OR NOT isfinite(primary_value) OR counter', old: 'lue > 1)

    ', new: 'lue >= 1)

    '}
  - {id: m47, what: 'comparison > read as >= in: + (SELECT count(*) FROM (SELECT detector, day FROM w GROUP BY ALL HAVING count(*) > 1)) AS', old: (*) > 1)), new: (*) >= 1))}
  - {id: m48, what: 'comparison < read as <= in: OR detected < 0 OR detected > injected) AS bad_drills', old: ted < 0 O, new: ted <= 0 O}
  - {id: m49, what: 'comparison > read as >= in: OR detected < 0 OR detected > injected) AS bad_drills', old: ted > inj, new: ted >= inj}
  - {id: m50, what: 'comparison > read as >= in: round(avg(w.primary_value) FILTER (WHERE w.day > {today} - {half_days}), 9) AS p_late,', old: 'ary_value) FILTER (WHERE w.day > {today} - {half_days}), 9) AS ', new: 'ary_value) FILTER (WHERE w.day >= {today} - {half_days}), 9) AS '}
  - {id: m51, what: 'comparison <= read as < in: avg(w.counter_value) FILTER (WHERE w.day <= {today} - {half_days}) AS c_early,', old: '_value) FILTER (WHERE w.day <= {today} - {half_days}) AS c', new: '_value) FILTER (WHERE w.day < {today} - {half_days}) AS c'}
  - {id: m52, what: 'comparison > read as >= in: round(avg(w.counter_value) FILTER (WHERE w.day > {today} - {half_days}), 9) AS c_late', old: 'ter_value) FILTER (WHERE w.day > {today} - {half_days}), 9) AS ', new: 'ter_value) FILTER (WHERE w.day >= {today} - {half_days}), 9) AS '}
  - {id: m53, what: 'comparison = read as <> in: FROM {register} r LEFT JOIN w ON w.detector = r.detector', old: "N w.detector = r.detector\n ", new: "N w.detector <> r.detector\n "}
  - {id: m54, what: 'comparison = read as <> in: FROM {register} r LEFT JOIN dw d ON d.detector = r.detector', old: "N d.detector = r.detector\n ", new: "N d.detector <> r.detector\n "}
  - {id: m55, what: 'comparison < read as <= in: WHEN dr.inj < s.drill_min THEN ''undrilled''', old: EN dr.inj < s.drill_m, new: EN dr.inj <= s.drill_m}
  - {id: m56, what: 'comparison < read as <= in: WHEN s.p_late < round(s.p_early - {eps_primary}, 9) AND s.c_late < round(s.c_early - {eps_', old: p_late < round(, new: p_late <= round(}
  - {id: m57, what: 'comparison < read as <= in: WHEN s.p_late < round(s.p_early - {eps_primary}, 9) AND s.c_late < round(s.c_early - {eps_', old: c_late < round(, new: c_late <= round(}
  - {id: m58, what: 'comparison = read as <> in: FROM s JOIN dr ON dr.detector = s.detector', old: tor = s.d, new: tor <> s.d}
  - {id: m59, what: 'comparison = read as <> in: CASE WHEN o.verdict = ''ok'' AND EXISTS (', old: ict = 'ok, new: ict <> 'ok}
  - {id: m60, what: 'comparison = read as <> in: SELECT 1 FROM reach a JOIN own x ON x.detector = a.u WHERE a.d = o.detector AND x.verdict ', old: tor = a.u, new: tor <> a.u}
  - {id: m61, what: 'comparison = read as <> in: SELECT 1 FROM reach a JOIN own x ON x.detector = a.u WHERE a.d = o.detector AND x.verdict ', old: a.d = o.d, new: a.d <> o.d}
  - {id: m62, what: 'comparison <> read as = in: SELECT 1 FROM reach a JOIN own x ON x.detector = a.u WHERE a.d = o.detector AND x.verdict ', old: ' <> ', new: ' = '}
  - {id: m63, what: 'comparison > read as >= in: SELECT NULL, NULL FROM g WHERE g.bad_register + g.bad_upstream + g.cycle + g.bad_signal + ', old: 'lls > 0

    )', new: 'lls >= 0

    )'}
  - {id: m64, what: 'comparison > read as >= in: WHEN g.bad_register > 0 THEN error(''malformed register: NULL field, duplicate detector, dr', old: ter > 0 T, new: ter >= 0 T}
  - {id: m65, what: 'comparison > read as >= in: WHEN g.bad_upstream > 0 THEN error(''malformed register: upstream names an unregistered det', old: eam > 0 T, new: eam >= 0 T}
  - {id: m66, what: 'comparison > read as >= in: WHEN g.cycle > 0 THEN error(''malformed register: upstream cycle'')', old: cle > 0 T, new: cle >= 0 T}
  - {id: m67, what: 'comparison > read as >= in: WHEN g.bad_signal > 0 THEN error(''malformed signal: NULL key, non-finite value, counter ou', old: nal > 0 T, new: nal >= 0 T}
  - {id: m68, what: 'comparison > read as >= in: WHEN g.bad_drills > 0 THEN error(''malformed drill: NULL field, negative count or detected ', old: lls > 0 T, new: lls >= 0 T}
  - {id: m69, what: 'clause dropped: AND day <= {today}', old: '_days} AND day <= {today}),

    g A', new: '_days}),

    g A'}
  - {id: m70, what: 'clause dropped: OR counter_floor IS NULL', old: ' OR counter_floor IS NULL', new: ''}
  - {id: m71, what: 'clause dropped: OR drill_min IS NULL', old: "\n         OR drill_min IS NULL", new: ''}
  - {id: m72, what: 'clause dropped: OR upstream IS NULL', old: ' OR upstream IS NULL', new: ''}
  - {id: m73, what: 'clause dropped: OR NOT isfinite(threshold)', old: "\n         OR NOT isfinite(threshold)", new: ''}
  - {id: m74, what: 'clause dropped: OR counter_floor < 0', old: ' OR counter_floor < 0', new: ''}
  - {id: m75, what: 'clause dropped: OR counter_floor > 1', old: ' OR counter_floor > 1', new: ''}
  - {id: m76, what: 'clause dropped: OR drill_min < 1', old: "\n         OR drill_min < 1", new: ''}
  - {id: m77, what: 'clause dropped: OR drill_recall_min <= 0', old: ' OR drill_recall_min <= 0', new: ''}
  - {id: m78, what: 'clause dropped: OR drill_recall_min > 1', old: ' OR drill_recall_min > 1', new: ''}
  - {id: m79, what: 'clause dropped: OR u NOT IN (SELECT detector FROM {register})', old: ' OR u NOT IN (SELECT detector FROM {register})', new: ''}
  - {id: m80, what: 'clause dropped: OR NOT isfinite(primary_value)', old: ' OR NOT isfinite(primary_value)', new: ''}
  - {id: m81, what: 'clause dropped: OR counter_value < 0', old: ' OR counter_value < 0', new: ''}
  - {id: m82, what: 'clause dropped: OR counter_value > 1', old: ' OR counter_value > 1', new: ''}
  - {id: m83, what: 'clause dropped: OR detected IS NULL', old: ' OR detected IS NULL', new: ''}
  - {id: m84, what: 'clause dropped: OR detected < 0', old: "\n         OR detected < 0", new: ''}
  - {id: m85, what: 'clause dropped: OR detected > injected', old: ' OR detected > injected', new: ''}
  - {id: m86, what: "clause dropped: AND EXISTS (\n           SELECT 1 FROM reach a JOIN", old: " AND EXISTS (\n           SELECT 1 FROM reach a JOIN own x ON x.detector = a.u WHERE a.d = o.detector AND x.verdict <> 'ok')", new: ''}
  - {id: m87, what: 'clause dropped: AND detector NOT IN (SELECT detector FROM {registe', old: ' AND detector NOT IN (SELECT detector FROM {register})', new: ''}
  - {id: m88, what: 'rounding removed: avg(w.primary_value) FILTER (WHERE w.day', old: 'round(avg(w.primary_value) FILTER (WHERE w.day > {today} - {half_days}), 9)', new: 'avg(w.primary_value) FILTER (WHERE w.day > {today} - {half_days})'}
  - {id: m89, what: 'rounding removed: avg(w.counter_value) FILTER (WHERE w.day', old: 'round(avg(w.counter_value) FILTER (WHERE w.day > {today} - {half_days}), 9)', new: 'avg(w.counter_value) FILTER (WHERE w.day > {today} - {half_days})'}
  - {id: m90, what: 'rounding removed: s.p_early - {eps_primary}', old: 'round(s.p_early - {eps_primary}, 9)', new: 's.p_early - {eps_primary}'}
  - {id: m91, what: 'rounding removed: s.c_early - {eps_counter}', old: 'round(s.c_early - {eps_counter}, 9)', new: 's.c_early - {eps_counter}'}
  - {id: m92, what: 'branch dropped: WHEN s.p_last IS NULL THEN ''dark''', old: "\n      WHEN s.p_last IS NULL THEN 'dark'", new: ''}
  - {id: m93, what: 'branch dropped: WHEN s.p_last > s.threshold THEN ''breach''', old: "\n      WHEN s.p_last > s.threshold THEN 'breach'", new: ''}
  - {id: m94, what: 'branch dropped: WHEN s.c_last IS NULL THEN ''counter_dark''', old: "\n      WHEN s.c_last IS NULL THEN 'counter_dark'", new: ''}
  - {id: m95, what: 'branch dropped: WHEN s.c_last < s.counter_floor THEN ''counter_low''', old: "\n      WHEN s.c_last < s.counter_floor THEN 'counter_low'", new: ''}
  - {id: m96, what: 'branch dropped: WHEN dr.inj < s.drill_min THEN ''undrilled''', old: "\n      WHEN dr.inj < s.drill_min THEN 'undrilled'", new: ''}
  - {id: m97, what: 'branch dropped: WHEN dr.det::DOUBLE / dr.inj < s.drill_recall_min', old: "\n      WHEN dr.det::DOUBLE / dr.inj < s.drill_recall_min THEN 'blind'", new: ''}
  - {id: m98, what: 'branch dropped: WHEN s.p_late < round(s.p_early - {eps_primary}, 9', old: "\n      WHEN s.p_late < round(s.p_early - {eps_primary}, 9) AND s.c_late < round(s.c_early - {eps_counter}, 9) THEN 'diverging'", new: ''}
  - {id: m99, what: 'branch dropped: WHEN g.bad_register > 0 THEN error(''malformed regi', old: "\n    WHEN g.bad_register > 0 THEN error('malformed register: NULL field, duplicate detector, drill_min below 1, recall outside (0, 1], non-finite threshold or floor, or floor outside [0, 1]')", new: ''}
  - {id: m100, what: 'branch dropped: WHEN g.bad_upstream > 0 THEN error(''malformed regi', old: "\n    WHEN g.bad_upstream > 0 THEN error('malformed register: upstream names an unregistered detector')", new: ''}
  - {id: m101, what: 'branch dropped: WHEN g.cycle > 0 THEN error(''malformed register: u', old: "\n    WHEN g.cycle > 0 THEN error('malformed register: upstream cycle')", new: ''}
  - {id: m102, what: 'branch dropped: WHEN g.bad_signal > 0 THEN error(''malformed signal', old: "\n    WHEN g.bad_signal > 0 THEN error('malformed signal: NULL key, non-finite value, counter outside [0, 1] or duplicate detector day')", new: ''}
  - {id: m103, what: 'branch dropped: WHEN g.bad_drills > 0 THEN error(''malformed drill:', old: "\n    WHEN g.bad_drills > 0 THEN error('malformed drill: NULL field, negative count or detected above injected')", new: ''}
  - {id: m104, what: 'guard term dropped: + (SELECT count(*) FROM {register} WHERE threshold', old: "\n    + (SELECT count(*) FROM {register} WHERE threshold IS NULL OR counter_floor IS NULL\n         OR drill_min IS NULL OR drill_recall_min IS NULL OR upstream IS NULL\n         OR NOT isfinite(threshold) OR counter_floor < 0 OR counter_floor > 1\n         OR drill_min < 1 OR drill_recall_min <= 0 OR drill_recall_min > 1)", new: ''}
  - {id: m105, what: 'guard term dropped: + (SELECT count(*) FROM w WHERE detector IS NULL O', old: "\n    + (SELECT count(*) FROM w WHERE detector IS NULL OR NOT isfinite(primary_value) OR counter_value < 0 OR counter_value > 1)", new: ''}
  - {id: m106, what: 'guard term dropped: + (SELECT count(*) FROM (SELECT detector, day FROM', old: "\n    + (SELECT count(*) FROM (SELECT detector, day FROM w GROUP BY ALL HAVING count(*) > 1))", new: ''}
  - {id: m107, what: 'guard term dropped: + (SELECT count(*) FROM dw WHERE detector IS NULL', old: "\n    + (SELECT count(*) FROM dw WHERE detector IS NULL OR injected IS NULL OR detected IS NULL\n         OR detected < 0 OR detected > injected)", new: ''}
  - {id: m108, what: unregistered rows not deduplicated (one row per day), old: 'SELECT DISTINCT detector, ''unregistered''', new: 'SELECT detector, ''unregistered'''}
  - {id: m109, what: upstream reach capped at one hop, old: 'WHERE r.depth < (SELECT count(*) FROM {register})', new: WHERE r.depth < 2}
  - {id: m110, what: a diverging upstream does not propagate (staged k1 (i)), old: x.verdict <> 'ok', new: 'x.verdict NOT IN (''ok'', ''diverging'')'}
  - {id: m111, what: signal window has no lower bound, old: '{signal} WHERE day > {today} - {window_days} AND day', new: '{signal} WHERE day'}
  - {id: m112, what: drill window has no lower bound, old: '{drills} WHERE day > {today} - {window_days} AND day', new: '{drills} WHERE day'}
  - {id: m113, what: NULL-detector signal row in the window accepted, old: 'FROM w WHERE detector IS NULL OR ', new: 'FROM w WHERE '}
  - {id: m114, what: NULL-detector drill row in the window accepted, old: 'FROM dw WHERE detector IS NULL OR ', new: 'FROM dw WHERE '}
  - {id: m115, what: duplicate signal days checked over all history, old: 'FROM (SELECT detector, day FROM w GROUP BY ALL', new: 'FROM (SELECT detector, day FROM {signal} GROUP BY ALL'}
  - {id: m116, what: signal value guard reads all history, old: FROM w WHERE detector IS NULL OR NOT, new: 'FROM {signal} WHERE detector IS NULL OR NOT'}
  - {id: m117, what: drill guard reads all history, old: FROM dw WHERE detector IS NULL OR injected, new: 'FROM {drills} WHERE detector IS NULL OR injected'}
  - {id: m118, what: no eps on the counter, old: 's.c_late < round(s.c_early - {eps_counter}, 9)', new: s.c_late < s.c_early}
  - {id: m119, what: tolerance s.p_last > s.threshold + 0.000001, old: s.p_last > s.threshold, new: s.p_last > s.threshold + 0.000001}
  - {id: m120, what: tolerance s.p_last > s.threshold - 0.000001, old: s.p_last > s.threshold, new: s.p_last > s.threshold - 0.000001}
  - {id: m121, what: tolerance s.p_last > s.threshold + 0.001, old: s.p_last > s.threshold, new: s.p_last > s.threshold + 0.001}
  - {id: m122, what: tolerance s.p_last > s.threshold - 0.001, old: s.p_last > s.threshold, new: s.p_last > s.threshold - 0.001}
  - {id: m123, what: tolerance s.c_last < s.counter_floor + 0.000001, old: s.c_last < s.counter_floor, new: s.c_last < s.counter_floor + 0.000001}
  - {id: m124, what: tolerance s.c_last < s.counter_floor - 0.000001, old: s.c_last < s.counter_floor, new: s.c_last < s.counter_floor - 0.000001}
  - {id: m125, what: tolerance s.c_last < s.counter_floor + 0.001, old: s.c_last < s.counter_floor, new: s.c_last < s.counter_floor + 0.001}
  - {id: m126, what: tolerance s.c_last < s.counter_floor - 0.001, old: s.c_last < s.counter_floor, new: s.c_last < s.counter_floor - 0.001}
  - {id: m127, what: 'tolerance dr.det::DOUBLE / dr.inj < s.drill_recall_min + 0.000001', old: 'dr.det::DOUBLE / dr.inj < s.drill_recall_min', new: 'dr.det::DOUBLE / dr.inj < s.drill_recall_min + 0.000001'}
  - {id: m128, what: 'tolerance dr.det::DOUBLE / dr.inj < s.drill_recall_min - 0.000001', old: 'dr.det::DOUBLE / dr.inj < s.drill_recall_min', new: 'dr.det::DOUBLE / dr.inj < s.drill_recall_min - 0.000001'}
  - {id: m129, what: 'tolerance dr.det::DOUBLE / dr.inj < s.drill_recall_min + 0.001', old: 'dr.det::DOUBLE / dr.inj < s.drill_recall_min', new: 'dr.det::DOUBLE / dr.inj < s.drill_recall_min + 0.001'}
  - {id: m130, what: 'tolerance dr.det::DOUBLE / dr.inj < s.drill_recall_min - 0.001', old: 'dr.det::DOUBLE / dr.inj < s.drill_recall_min', new: 'dr.det::DOUBLE / dr.inj < s.drill_recall_min - 0.001'}
  - {id: m131, what: 'tolerance round(s.p_early - {eps_primary}, 9) + 0.000001', old: 'round(s.p_early - {eps_primary}, 9)', new: 'round(s.p_early - {eps_primary} + 0.000001, 9)'}
  - {id: m132, what: 'tolerance round(s.p_early - {eps_primary}, 9) - 0.000001', old: 'round(s.p_early - {eps_primary}, 9)', new: 'round(s.p_early - {eps_primary} - 0.000001, 9)'}
  - {id: m133, what: 'tolerance round(s.p_early - {eps_primary}, 9) + 0.001', old: 'round(s.p_early - {eps_primary}, 9)', new: 'round(s.p_early - {eps_primary} + 0.001, 9)'}
  - {id: m134, what: 'tolerance round(s.p_early - {eps_primary}, 9) - 0.001', old: 'round(s.p_early - {eps_primary}, 9)', new: 'round(s.p_early - {eps_primary} - 0.001, 9)'}
  - {id: m135, what: 'tolerance round(s.c_early - {eps_counter}, 9) + 0.000001', old: 'round(s.c_early - {eps_counter}, 9)', new: 'round(s.c_early - {eps_counter} + 0.000001, 9)'}
  - {id: m136, what: 'tolerance round(s.c_early - {eps_counter}, 9) - 0.000001', old: 'round(s.c_early - {eps_counter}, 9)', new: 'round(s.c_early - {eps_counter} - 0.000001, 9)'}
  - {id: m137, what: 'tolerance round(s.c_early - {eps_counter}, 9) + 0.001', old: 'round(s.c_early - {eps_counter}, 9)', new: 'round(s.c_early - {eps_counter} + 0.001, 9)'}
  - {id: m138, what: 'tolerance round(s.c_early - {eps_counter}, 9) - 0.001', old: 'round(s.c_early - {eps_counter}, 9)', new: 'round(s.c_early - {eps_counter} - 0.001, 9)'}
  - {id: m139, what: tolerance OR drill_recall_min <= 0 + 0.000001, old: OR drill_recall_min <= 0, new: OR drill_recall_min <= 0 + 0.000001}
  - {id: m140, what: tolerance OR drill_recall_min <= 0 - 0.000001, old: OR drill_recall_min <= 0, new: OR drill_recall_min <= 0 - 0.000001}
  - {id: m141, what: tolerance OR drill_recall_min <= 0 + 0.001, old: OR drill_recall_min <= 0, new: OR drill_recall_min <= 0 + 0.001}
  - {id: m142, what: tolerance OR drill_recall_min <= 0 - 0.001, old: OR drill_recall_min <= 0, new: OR drill_recall_min <= 0 - 0.001}
  - {id: m143, what: tolerance OR drill_recall_min > 1 + 0.000001, old: OR drill_recall_min > 1, new: OR drill_recall_min > 1 + 0.000001}
  - {id: m144, what: tolerance OR drill_recall_min > 1 - 0.000001, old: OR drill_recall_min > 1, new: OR drill_recall_min > 1 - 0.000001}
  - {id: m145, what: tolerance OR drill_recall_min > 1 + 0.001, old: OR drill_recall_min > 1, new: OR drill_recall_min > 1 + 0.001}
  - {id: m146, what: tolerance OR drill_recall_min > 1 - 0.001, old: OR drill_recall_min > 1, new: OR drill_recall_min > 1 - 0.001}
  - {id: m147, what: tolerance OR counter_floor < 0 + 0.000001, old: OR counter_floor < 0, new: OR counter_floor < 0 + 0.000001}
  - {id: m148, what: tolerance OR counter_floor < 0 - 0.000001, old: OR counter_floor < 0, new: OR counter_floor < 0 - 0.000001}
  - {id: m149, what: tolerance OR counter_floor < 0 + 0.001, old: OR counter_floor < 0, new: OR counter_floor < 0 + 0.001}
  - {id: m150, what: tolerance OR counter_floor < 0 - 0.001, old: OR counter_floor < 0, new: OR counter_floor < 0 - 0.001}
  - {id: m151, what: tolerance OR counter_floor > 1 + 0.000001, old: OR counter_floor > 1, new: OR counter_floor > 1 + 0.000001}
  - {id: m152, what: tolerance OR counter_floor > 1 - 0.000001, old: OR counter_floor > 1, new: OR counter_floor > 1 - 0.000001}
  - {id: m153, what: tolerance OR counter_floor > 1 + 0.001, old: OR counter_floor > 1, new: OR counter_floor > 1 + 0.001}
  - {id: m154, what: tolerance OR counter_floor > 1 - 0.001, old: OR counter_floor > 1, new: OR counter_floor > 1 - 0.001}
  - {id: m155, what: tolerance OR counter_value < 0 + 0.000001, old: OR counter_value < 0, new: OR counter_value < 0 + 0.000001}
  - {id: m156, what: tolerance OR counter_value < 0 - 0.000001, old: OR counter_value < 0, new: OR counter_value < 0 - 0.000001}
  - {id: m157, what: tolerance OR counter_value < 0 + 0.001, old: OR counter_value < 0, new: OR counter_value < 0 + 0.001}
  - {id: m158, what: tolerance OR counter_value < 0 - 0.001, old: OR counter_value < 0, new: OR counter_value < 0 - 0.001}
  - {id: m159, what: tolerance OR counter_value > 1 + 0.000001, old: OR counter_value > 1, new: OR counter_value > 1 + 0.000001}
  - {id: m160, what: tolerance OR counter_value > 1 - 0.000001, old: OR counter_value > 1, new: OR counter_value > 1 - 0.000001}
  - {id: m161, what: tolerance OR counter_value > 1 + 0.001, old: OR counter_value > 1, new: OR counter_value > 1 + 0.001}
  - {id: m162, what: tolerance OR counter_value > 1 - 0.001, old: OR counter_value > 1, new: OR counter_value > 1 - 0.001}
  - {id: m163, what: 'precision 2: avg(w.primary_value) FILTER (WHERE w.day > {today}', old: 'round(avg(w.primary_value) FILTER (WHERE w.day > {today} - {half_days}), 9)', new: 'round(avg(w.primary_value) FILTER (WHERE w.day > {today} - {half_days}), 2)'}
  - {id: m164, what: 'precision 4: avg(w.primary_value) FILTER (WHERE w.day > {today}', old: 'round(avg(w.primary_value) FILTER (WHERE w.day > {today} - {half_days}), 9)', new: 'round(avg(w.primary_value) FILTER (WHERE w.day > {today} - {half_days}), 4)'}
  - {id: m165, what: 'precision 6: avg(w.primary_value) FILTER (WHERE w.day > {today}', old: 'round(avg(w.primary_value) FILTER (WHERE w.day > {today} - {half_days}), 9)', new: 'round(avg(w.primary_value) FILTER (WHERE w.day > {today} - {half_days}), 6)'}
  - {id: m166, what: 'precision 12: avg(w.primary_value) FILTER (WHERE w.day > {today}', old: 'round(avg(w.primary_value) FILTER (WHERE w.day > {today} - {half_days}), 9)', new: 'round(avg(w.primary_value) FILTER (WHERE w.day > {today} - {half_days}), 12)'}
  - {id: m167, what: 'precision 2: avg(w.counter_value) FILTER (WHERE w.day > {today}', old: 'round(avg(w.counter_value) FILTER (WHERE w.day > {today} - {half_days}), 9)', new: 'round(avg(w.counter_value) FILTER (WHERE w.day > {today} - {half_days}), 2)'}
  - {id: m168, what: 'precision 4: avg(w.counter_value) FILTER (WHERE w.day > {today}', old: 'round(avg(w.counter_value) FILTER (WHERE w.day > {today} - {half_days}), 9)', new: 'round(avg(w.counter_value) FILTER (WHERE w.day > {today} - {half_days}), 4)'}
  - {id: m169, what: 'precision 6: avg(w.counter_value) FILTER (WHERE w.day > {today}', old: 'round(avg(w.counter_value) FILTER (WHERE w.day > {today} - {half_days}), 9)', new: 'round(avg(w.counter_value) FILTER (WHERE w.day > {today} - {half_days}), 6)'}
  - {id: m170, what: 'precision 12: avg(w.counter_value) FILTER (WHERE w.day > {today}', old: 'round(avg(w.counter_value) FILTER (WHERE w.day > {today} - {half_days}), 9)', new: 'round(avg(w.counter_value) FILTER (WHERE w.day > {today} - {half_days}), 12)'}
  - {id: m171, what: 'precision 2: s.p_early - {eps_primary}', old: 'round(s.p_early - {eps_primary}, 9)', new: 'round(s.p_early - {eps_primary}, 2)'}
  - {id: m172, what: 'precision 4: s.p_early - {eps_primary}', old: 'round(s.p_early - {eps_primary}, 9)', new: 'round(s.p_early - {eps_primary}, 4)'}
  - {id: m173, what: 'precision 6: s.p_early - {eps_primary}', old: 'round(s.p_early - {eps_primary}, 9)', new: 'round(s.p_early - {eps_primary}, 6)'}
  - {id: m174, what: 'precision 12: s.p_early - {eps_primary}', old: 'round(s.p_early - {eps_primary}, 9)', new: 'round(s.p_early - {eps_primary}, 12)'}
  - {id: m175, what: 'precision 2: s.c_early - {eps_counter}', old: 'round(s.c_early - {eps_counter}, 9)', new: 'round(s.c_early - {eps_counter}, 2)'}
  - {id: m176, what: 'precision 4: s.c_early - {eps_counter}', old: 'round(s.c_early - {eps_counter}, 9)', new: 'round(s.c_early - {eps_counter}, 4)'}
  - {id: m177, what: 'precision 6: s.c_early - {eps_counter}', old: 'round(s.c_early - {eps_counter}, 9)', new: 'round(s.c_early - {eps_counter}, 6)'}
  - {id: m178, what: 'precision 12: s.c_early - {eps_counter}', old: 'round(s.c_early - {eps_counter}, 9)', new: 'round(s.c_early - {eps_counter}, 12)'}
  - {id: m179, what: 'aggregate arg_max(w.primary_value, w.day) -> arg_min(w.primary_value, w.day)', old: 'arg_max(w.primary_value, w.day) AS p_last', new: 'arg_min(w.primary_value, w.day) AS p_last'}
  - {id: m180, what: 'aggregate arg_max(w.primary_value, w.day) -> max(w.primary_value)', old: 'arg_max(w.primary_value, w.day) AS p_last', new: max(w.primary_value) AS p_last}
  - {id: m181, what: 'aggregate arg_max(w.primary_value, w.day) -> min(w.primary_value)', old: 'arg_max(w.primary_value, w.day) AS p_last', new: min(w.primary_value) AS p_last}
  - {id: m182, what: 'aggregate arg_max(w.counter_value, w.day) -> arg_min(w.counter_value, w.day)', old: 'arg_max(w.counter_value, w.day) AS c_last', new: 'arg_min(w.counter_value, w.day) AS c_last'}
  - {id: m183, what: 'aggregate arg_max(w.counter_value, w.day) -> max(w.counter_value)', old: 'arg_max(w.counter_value, w.day) AS c_last', new: max(w.counter_value) AS c_last}
  - {id: m184, what: 'aggregate arg_max(w.counter_value, w.day) -> min(w.counter_value)', old: 'arg_max(w.counter_value, w.day) AS c_last', new: min(w.counter_value) AS c_last}
  - {id: m185, what: aggregate avg -> min on w.primary_value <=, old: 'avg(w.primary_value) FILTER (WHERE w.day <= {today} - {half_days})', new: 'min(w.primary_value) FILTER (WHERE w.day <= {today} - {half_days})'}
  - {id: m186, what: aggregate avg -> max on w.primary_value <=, old: 'avg(w.primary_value) FILTER (WHERE w.day <= {today} - {half_days})', new: 'max(w.primary_value) FILTER (WHERE w.day <= {today} - {half_days})'}
  - {id: m187, what: aggregate avg -> median on w.primary_value <=, old: 'avg(w.primary_value) FILTER (WHERE w.day <= {today} - {half_days})', new: 'median(w.primary_value) FILTER (WHERE w.day <= {today} - {half_days})'}
  - {id: m188, what: aggregate avg -> min on w.primary_value >, old: 'avg(w.primary_value) FILTER (WHERE w.day > {today} - {half_days})', new: 'min(w.primary_value) FILTER (WHERE w.day > {today} - {half_days})'}
  - {id: m189, what: aggregate avg -> max on w.primary_value >, old: 'avg(w.primary_value) FILTER (WHERE w.day > {today} - {half_days})', new: 'max(w.primary_value) FILTER (WHERE w.day > {today} - {half_days})'}
  - {id: m190, what: aggregate avg -> median on w.primary_value >, old: 'avg(w.primary_value) FILTER (WHERE w.day > {today} - {half_days})', new: 'median(w.primary_value) FILTER (WHERE w.day > {today} - {half_days})'}
  - {id: m191, what: aggregate avg -> min on w.counter_value <=, old: 'avg(w.counter_value) FILTER (WHERE w.day <= {today} - {half_days})', new: 'min(w.counter_value) FILTER (WHERE w.day <= {today} - {half_days})'}
  - {id: m192, what: aggregate avg -> max on w.counter_value <=, old: 'avg(w.counter_value) FILTER (WHERE w.day <= {today} - {half_days})', new: 'max(w.counter_value) FILTER (WHERE w.day <= {today} - {half_days})'}
  - {id: m193, what: aggregate avg -> median on w.counter_value <=, old: 'avg(w.counter_value) FILTER (WHERE w.day <= {today} - {half_days})', new: 'median(w.counter_value) FILTER (WHERE w.day <= {today} - {half_days})'}
  - {id: m194, what: aggregate avg -> min on w.counter_value >, old: 'avg(w.counter_value) FILTER (WHERE w.day > {today} - {half_days})', new: 'min(w.counter_value) FILTER (WHERE w.day > {today} - {half_days})'}
  - {id: m195, what: aggregate avg -> max on w.counter_value >, old: 'avg(w.counter_value) FILTER (WHERE w.day > {today} - {half_days})', new: 'max(w.counter_value) FILTER (WHERE w.day > {today} - {half_days})'}
  - {id: m196, what: aggregate avg -> median on w.counter_value >, old: 'avg(w.counter_value) FILTER (WHERE w.day > {today} - {half_days})', new: 'median(w.counter_value) FILTER (WHERE w.day > {today} - {half_days})'}
  - {id: m197, what: 'aggregate coalesce(sum(d.injected), 0) AS inj -> coalesce(max(d.injected), 0) AS inj', old: 'coalesce(sum(d.injected), 0) AS inj', new: 'coalesce(max(d.injected), 0) AS inj'}
  - {id: m198, what: 'aggregate coalesce(sum(d.injected), 0) AS inj -> coalesce(min(d.injected), 0) AS inj', old: 'coalesce(sum(d.injected), 0) AS inj', new: 'coalesce(min(d.injected), 0) AS inj'}
  - {id: m199, what: 'aggregate coalesce(sum(d.detected), 0) AS det -> coalesce(max(d.detected), 0) AS det', old: 'coalesce(sum(d.detected), 0) AS det', new: 'coalesce(max(d.detected), 0) AS det'}
  - {id: m200, what: 'aggregate coalesce(sum(d.detected), 0) AS det -> coalesce(min(d.detected), 0) AS det', old: 'coalesce(sum(d.detected), 0) AS det', new: 'coalesce(min(d.detected), 0) AS det'}
  - {id: m201, what: 'arithmetic s.p_early - {eps_primary} -> s.p_early + {eps_primary}', old: 's.p_early - {eps_primary}', new: 's.p_early + {eps_primary}'}
  - {id: m202, what: 'arithmetic s.c_early - {eps_counter} -> s.c_early + {eps_counter}', old: 's.c_early - {eps_counter}', new: 's.c_early + {eps_counter}'}
  - {id: m203, what: 'arithmetic dr.det::DOUBLE / dr.inj -> dr.det // dr.inj', old: 'dr.det::DOUBLE / dr.inj', new: dr.det // dr.inj}
  - {id: m204, what: arithmetic r.depth + 1 -> r.depth + 2, old: r.depth + 1, new: r.depth + 2}
  - {id: m205, what: 'order verdict: swap WHEN s.p_last > s.threshold THEN ''breach / WHEN s.c_last IS NULL THEN ''counter_dark', old: "      WHEN s.p_last > s.threshold THEN 'breach'\n      WHEN s.c_last IS NULL THEN 'counter_dark'", new: "      WHEN s.c_last IS NULL THEN 'counter_dark'\n      WHEN s.p_last > s.threshold THEN 'breach'"}
  - {id: m206, what: 'order verdict: swap WHEN s.c_last < s.counter_floor THEN ''co / WHEN dr.inj < s.drill_min THEN ''undrille', old: "      WHEN s.c_last < s.counter_floor THEN 'counter_low'\n      WHEN dr.inj < s.drill_min THEN 'undrilled'", new: "      WHEN dr.inj < s.drill_min THEN 'undrilled'\n      WHEN s.c_last < s.counter_floor THEN 'counter_low'"}
  - {id: m207, what: 'order verdict: swap WHEN dr.inj < s.drill_min THEN ''undrille / WHEN dr.det::DOUBLE / dr.inj < s.drill_r', old: "      WHEN dr.inj < s.drill_min THEN 'undrilled'\n      WHEN dr.det::DOUBLE / dr.inj < s.drill_recall_min THEN 'blind'", new: "      WHEN dr.det::DOUBLE / dr.inj < s.drill_recall_min THEN 'blind'\n      WHEN dr.inj < s.drill_min THEN 'undrilled'"}
  - {id: m208, what: 'order verdict: swap WHEN dr.det::DOUBLE / dr.inj < s.drill_r / WHEN s.p_late < round(s.p_early - {eps_p', old: "      WHEN dr.det::DOUBLE / dr.inj < s.drill_recall_min THEN 'blind'\n      WHEN s.p_late < round(s.p_early - {eps_primary}, 9) AND s.c_late < round(s.c_early - {eps_counter}, 9) THEN 'diverging'", new: "      WHEN s.p_late < round(s.p_early - {eps_primary}, 9) AND s.c_late < round(s.c_early - {eps_counter}, 9) THEN 'diverging'\n      WHEN dr.det::DOUBLE / dr.inj < s.drill_recall_min THEN 'blind'"}
  - {id: m209, what: 'order verdict: move to top WHEN s.c_last IS NULL THEN ''counter_dark''', old: "      WHEN s.p_last IS NULL THEN 'dark'\n      WHEN s.p_last > s.threshold THEN 'breach'\n      WHEN s.c_last IS NULL THEN 'counter_dark'", new: "      WHEN s.c_last IS NULL THEN 'counter_dark'\n      WHEN s.p_last IS NULL THEN 'dark'\n      WHEN s.p_last > s.threshold THEN 'breach'"}
  - {id: m210, what: 'order verdict: move to top WHEN s.c_last < s.counter_floor THEN ''counter_low''', old: "      WHEN s.p_last IS NULL THEN 'dark'\n      WHEN s.p_last > s.threshold THEN 'breach'\n      WHEN s.c_last IS NULL THEN 'counter_dark'\n      WHEN s.c_last < s.counter_floor THEN 'counter_low'", new: "      WHEN s.c_last < s.counter_floor THEN 'counter_low'\n      WHEN s.p_last IS NULL THEN 'dark'\n      WHEN s.p_last > s.threshold THEN 'breach'\n      WHEN s.c_last IS NULL THEN 'counter_dark'"}
  - {id: m211, what: 'order verdict: move to top WHEN dr.inj < s.drill_min THEN ''undrilled''', old: "      WHEN s.p_last IS NULL THEN 'dark'\n      WHEN s.p_last > s.threshold THEN 'breach'\n      WHEN s.c_last IS NULL THEN 'counter_dark'\n      WHEN s.c_last < s.counter_floor THEN 'counter_low'\n      WHEN dr.inj < s.drill_min THEN 'undrilled'", new: "      WHEN dr.inj < s.drill_min THEN 'undrilled'\n      WHEN s.p_last IS NULL THEN 'dark'\n      WHEN s.p_last > s.threshold THEN 'breach'\n      WHEN s.c_last IS NULL THEN 'counter_dark'\n      WHEN s.c_last < s.counter_floor THEN 'counter_low'"}
  - {id: m212, what: 'order verdict: move to top WHEN dr.det::DOUBLE / dr.inj < s.drill_recall_min ', old: "      WHEN s.p_last IS NULL THEN 'dark'\n      WHEN s.p_last > s.threshold THEN 'breach'\n      WHEN s.c_last IS NULL THEN 'counter_dark'\n      WHEN s.c_last < s.counter_floor THEN 'counter_low'\n      WHEN dr.inj < s.drill_min THEN 'undrilled'\n      WHEN dr.det::DOUBLE / dr.inj < s.drill_recall_min THEN 'blind'", new: "      WHEN dr.det::DOUBLE / dr.inj < s.drill_recall_min THEN 'blind'\n      WHEN s.p_last IS NULL THEN 'dark'\n      WHEN s.p_last > s.threshold THEN 'breach'\n      WHEN s.c_last IS NULL THEN 'counter_dark'\n      WHEN s.c_last < s.counter_floor THEN 'counter_low'\n      WHEN dr.inj < s.drill_min THEN 'undrilled'"}
  - {id: m213, what: 'order verdict: move to top WHEN s.p_late < round(s.p_early - {eps_primary}, 9', old: "      WHEN s.p_last IS NULL THEN 'dark'\n      WHEN s.p_last > s.threshold THEN 'breach'\n      WHEN s.c_last IS NULL THEN 'counter_dark'\n      WHEN s.c_last < s.counter_floor THEN 'counter_low'\n      WHEN dr.inj < s.drill_min THEN 'undrilled'\n      WHEN dr.det::DOUBLE / dr.inj < s.drill_recall_min THEN 'blind'\n      WHEN s.p_late < round(s.p_early - {eps_primary}, 9) AND s.c_late < round(s.c_early - {eps_counter}, 9) THEN 'diverging'", new: "      WHEN s.p_late < round(s.p_early - {eps_primary}, 9) AND s.c_late < round(s.c_early - {eps_counter}, 9) THEN 'diverging'\n      WHEN s.p_last IS NULL THEN 'dark'\n      WHEN s.p_last > s.threshold THEN 'breach'\n      WHEN s.c_last IS NULL THEN 'counter_dark'\n      WHEN s.c_last < s.counter_floor THEN 'counter_low'\n      WHEN dr.inj < s.drill_min THEN 'undrilled'\n      WHEN dr.det::DOUBLE / dr.inj < s.drill_recall_min THEN 'blind'"}
  - {id: m214, what: 'order error: swap WHEN g.bad_register > 0 THEN error(''malf / WHEN g.bad_upstream > 0 THEN error(''malf', old: "    WHEN g.bad_register > 0 THEN error('malformed register: NULL field, duplicate detector, drill_min below 1, recall outside (0, 1], non-finite threshold or floor, or floor outside [0, 1]')\n    WHEN g.bad_upstream > 0 THEN error('malformed register: upstream names an unregistered detector')", new: "    WHEN g.bad_upstream > 0 THEN error('malformed register: upstream names an unregistered detector')\n    WHEN g.bad_register > 0 THEN error('malformed register: NULL field, duplicate detector, drill_min below 1, recall outside (0, 1], non-finite threshold or floor, or floor outside [0, 1]')"}
  - {id: m215, what: 'order error: swap WHEN g.bad_upstream > 0 THEN error(''malf / WHEN g.cycle > 0 THEN error(''malformed r', old: "    WHEN g.bad_upstream > 0 THEN error('malformed register: upstream names an unregistered detector')\n    WHEN g.cycle > 0 THEN error('malformed register: upstream cycle')", new: "    WHEN g.cycle > 0 THEN error('malformed register: upstream cycle')\n    WHEN g.bad_upstream > 0 THEN error('malformed register: upstream names an unregistered detector')"}
  - {id: m216, what: 'order error: swap WHEN g.cycle > 0 THEN error(''malformed r / WHEN g.bad_signal > 0 THEN error(''malfor', old: "    WHEN g.cycle > 0 THEN error('malformed register: upstream cycle')\n    WHEN g.bad_signal > 0 THEN error('malformed signal: NULL key, non-finite value, counter outside [0, 1] or duplicate detector day')", new: "    WHEN g.bad_signal > 0 THEN error('malformed signal: NULL key, non-finite value, counter outside [0, 1] or duplicate detector day')\n    WHEN g.cycle > 0 THEN error('malformed register: upstream cycle')"}
  - {id: m217, what: 'order error: swap WHEN g.bad_signal > 0 THEN error(''malfor / WHEN g.bad_drills > 0 THEN error(''malfor', old: "    WHEN g.bad_signal > 0 THEN error('malformed signal: NULL key, non-finite value, counter outside [0, 1] or duplicate detector day')\n    WHEN g.bad_drills > 0 THEN error('malformed drill: NULL field, negative count or detected above injected')", new: "    WHEN g.bad_drills > 0 THEN error('malformed drill: NULL field, negative count or detected above injected')\n    WHEN g.bad_signal > 0 THEN error('malformed signal: NULL key, non-finite value, counter outside [0, 1] or duplicate detector day')"}
  - {id: m218, what: 'order error: move to top WHEN g.cycle > 0 THEN error(''malformed register: u', old: "    WHEN g.bad_register > 0 THEN error('malformed register: NULL field, duplicate detector, drill_min below 1, recall outside (0, 1], non-finite threshold or floor, or floor outside [0, 1]')\n    WHEN g.bad_upstream > 0 THEN error('malformed register: upstream names an unregistered detector')\n    WHEN g.cycle > 0 THEN error('malformed register: upstream cycle')", new: "    WHEN g.cycle > 0 THEN error('malformed register: upstream cycle')\n    WHEN g.bad_register > 0 THEN error('malformed register: NULL field, duplicate detector, drill_min below 1, recall outside (0, 1], non-finite threshold or floor, or floor outside [0, 1]')\n    WHEN g.bad_upstream > 0 THEN error('malformed register: upstream names an unregistered detector')"}
  - {id: m219, what: 'order error: move to top WHEN g.bad_signal > 0 THEN error(''malformed signal', old: "    WHEN g.bad_register > 0 THEN error('malformed register: NULL field, duplicate detector, drill_min below 1, recall outside (0, 1], non-finite threshold or floor, or floor outside [0, 1]')\n    WHEN g.bad_upstream > 0 THEN error('malformed register: upstream names an unregistered detector')\n    WHEN g.cycle > 0 THEN error('malformed register: upstream cycle')\n    WHEN g.bad_signal > 0 THEN error('malformed signal: NULL key, non-finite value, counter outside [0, 1] or duplicate detector day')", new: "    WHEN g.bad_signal > 0 THEN error('malformed signal: NULL key, non-finite value, counter outside [0, 1] or duplicate detector day')\n    WHEN g.bad_register > 0 THEN error('malformed register: NULL field, duplicate detector, drill_min below 1, recall outside (0, 1], non-finite threshold or floor, or floor outside [0, 1]')\n    WHEN g.bad_upstream > 0 THEN error('malformed register: upstream names an unregistered detector')\n    WHEN g.cycle > 0 THEN error('malformed register: upstream cycle')"}
  - {id: m220, what: 'order error: move to top WHEN g.bad_drills > 0 THEN error(''malformed drill:', old: "    WHEN g.bad_register > 0 THEN error('malformed register: NULL field, duplicate detector, drill_min below 1, recall outside (0, 1], non-finite threshold or floor, or floor outside [0, 1]')\n    WHEN g.bad_upstream > 0 THEN error('malformed register: upstream names an unregistered detector')\n    WHEN g.cycle > 0 THEN error('malformed register: upstream cycle')\n    WHEN g.bad_signal > 0 THEN error('malformed signal: NULL key, non-finite value, counter outside [0, 1] or duplicate detector day')\n    WHEN g.bad_drills > 0 THEN error('malformed drill: NULL field, negative count or detected above injected')", new: "    WHEN g.bad_drills > 0 THEN error('malformed drill: NULL field, negative count or detected above injected')\n    WHEN g.bad_register > 0 THEN error('malformed register: NULL field, duplicate detector, drill_min below 1, recall outside (0, 1], non-finite threshold or floor, or floor outside [0, 1]')\n    WHEN g.bad_upstream > 0 THEN error('malformed register: upstream names an unregistered detector')\n    WHEN g.cycle > 0 THEN error('malformed register: upstream cycle')\n    WHEN g.bad_signal > 0 THEN error('malformed signal: NULL key, non-finite value, counter outside [0, 1] or duplicate detector day')"}
  - {id: m221, what: propagation skips dark, old: x.verdict <> 'ok', new: 'x.verdict NOT IN (''ok'', ''dark'')'}
  - {id: m222, what: propagation skips breach, old: x.verdict <> 'ok', new: 'x.verdict NOT IN (''ok'', ''breach'')'}
  - {id: m223, what: propagation skips counter_dark, old: x.verdict <> 'ok', new: 'x.verdict NOT IN (''ok'', ''counter_dark'')'}
  - {id: m224, what: propagation skips counter_low, old: x.verdict <> 'ok', new: 'x.verdict NOT IN (''ok'', ''counter_low'')'}
  - {id: m225, what: propagation skips undrilled, old: x.verdict <> 'ok', new: 'x.verdict NOT IN (''ok'', ''undrilled'')'}
  - {id: m226, what: propagation skips blind, old: x.verdict <> 'ok', new: 'x.verdict NOT IN (''ok'', ''blind'')'}
  - {id: m227, what: NULL read as 0 in primary early half, old: 'avg(w.primary_value) FILTER (WHERE w.day <= {today} - {half_days}) AS p_early', new: 'avg(coalesce(w.primary_value, 0)) FILTER (WHERE w.day <= {today} - {half_days}) AS p_early'}
  - {id: m228, what: half split + 1 day on primary early half, old: 'avg(w.primary_value) FILTER (WHERE w.day <= {today} - {half_days}) AS p_early', new: 'avg(w.primary_value) FILTER (WHERE w.day <= {today} - {half_days} + 1) AS p_early'}
  - {id: m229, what: half split - 1 day on primary early half, old: 'avg(w.primary_value) FILTER (WHERE w.day <= {today} - {half_days}) AS p_early', new: 'avg(w.primary_value) FILTER (WHERE w.day <= {today} - {half_days} - 1) AS p_early'}
  - {id: m230, what: NULL read as 0 in primary late half, old: 'avg(w.primary_value) FILTER (WHERE w.day > {today} - {half_days}), 9) AS p_late', new: 'avg(coalesce(w.primary_value, 0)) FILTER (WHERE w.day > {today} - {half_days}), 9) AS p_late'}
  - {id: m231, what: half split + 1 day on primary late half, old: 'avg(w.primary_value) FILTER (WHERE w.day > {today} - {half_days}), 9) AS p_late', new: 'avg(w.primary_value) FILTER (WHERE w.day > {today} - {half_days} + 1), 9) AS p_late'}
  - {id: m232, what: half split - 1 day on primary late half, old: 'avg(w.primary_value) FILTER (WHERE w.day > {today} - {half_days}), 9) AS p_late', new: 'avg(w.primary_value) FILTER (WHERE w.day > {today} - {half_days} - 1), 9) AS p_late'}
  - {id: m233, what: NULL read as 0 in counter early half, old: 'avg(w.counter_value) FILTER (WHERE w.day <= {today} - {half_days}) AS c_early', new: 'avg(coalesce(w.counter_value, 0)) FILTER (WHERE w.day <= {today} - {half_days}) AS c_early'}
  - {id: m234, what: half split + 1 day on counter early half, old: 'avg(w.counter_value) FILTER (WHERE w.day <= {today} - {half_days}) AS c_early', new: 'avg(w.counter_value) FILTER (WHERE w.day <= {today} - {half_days} + 1) AS c_early'}
  - {id: m235, what: half split - 1 day on counter early half, old: 'avg(w.counter_value) FILTER (WHERE w.day <= {today} - {half_days}) AS c_early', new: 'avg(w.counter_value) FILTER (WHERE w.day <= {today} - {half_days} - 1) AS c_early'}
  - {id: m236, what: NULL read as 0 in counter late half, old: 'avg(w.counter_value) FILTER (WHERE w.day > {today} - {half_days}), 9) AS c_late', new: 'avg(coalesce(w.counter_value, 0)) FILTER (WHERE w.day > {today} - {half_days}), 9) AS c_late'}
  - {id: m237, what: half split + 1 day on counter late half, old: 'avg(w.counter_value) FILTER (WHERE w.day > {today} - {half_days}), 9) AS c_late', new: 'avg(w.counter_value) FILTER (WHERE w.day > {today} - {half_days} + 1), 9) AS c_late'}
  - {id: m238, what: half split - 1 day on counter late half, old: 'avg(w.counter_value) FILTER (WHERE w.day > {today} - {half_days}), 9) AS c_late', new: 'avg(w.counter_value) FILTER (WHERE w.day > {today} - {half_days} - 1), 9) AS c_late'}
  - {id: m239, what: param eps_counter read as eps_primary, old: 'round(s.c_early - {eps_counter}, 9)', new: 'round(s.c_early - {eps_primary}, 9)'}
  - {id: m240, what: param eps_primary read as eps_counter, old: 'round(s.p_early - {eps_primary}, 9)', new: 'round(s.p_early - {eps_counter}, 9)'}
  - {id: m241, what: param signal window read as 2 x half_days, old: '{signal} WHERE day > {today} - {window_days}', new: '{signal} WHERE day > {today} - 2 * {half_days}'}
  - {id: m242, what: param drill window read as 2 x half_days, old: '{drills} WHERE day > {today} - {window_days}', new: '{drills} WHERE day > {today} - 2 * {half_days}'}
  - {id: m243, what: param half_days read as window_days / 2 on primary early half, old: 'avg(w.primary_value) FILTER (WHERE w.day <= {today} - {half_days}) AS p_early', new: 'avg(w.primary_value) FILTER (WHERE w.day <= {today} - ({window_days} // 2)) AS p_early'}
  - {id: m244, what: param half_days read as window_days / 2 on primary late half, old: 'avg(w.primary_value) FILTER (WHERE w.day > {today} - {half_days}), 9) AS p_late', new: 'avg(w.primary_value) FILTER (WHERE w.day > {today} - ({window_days} // 2)), 9) AS p_late'}
  - {id: m245, what: param half_days read as window_days / 2 on counter early half, old: 'avg(w.counter_value) FILTER (WHERE w.day <= {today} - {half_days}) AS c_early', new: 'avg(w.counter_value) FILTER (WHERE w.day <= {today} - ({window_days} // 2)) AS c_early'}
  - {id: m246, what: param half_days read as window_days / 2 on counter late half, old: 'avg(w.counter_value) FILTER (WHERE w.day > {today} - {half_days}), 9) AS c_late', new: 'avg(w.counter_value) FILTER (WHERE w.day > {today} - ({window_days} // 2)), 9) AS c_late'}
equivalent:
  - {id: q01, what: 'reach depth bound read inclusively; the recursion only runs one step further, so no path or cycle changes', old: pth < (SE, new: pth <= (SE}
  - {id: q02, what: 'dark and breach swapped; when p_last is NULL the breach comparison is NULL, so neither branch can fire before the other', old: "      WHEN s.p_last IS NULL THEN 'dark'\n      WHEN s.p_last > s.threshold THEN 'breach'", new: "      WHEN s.p_last > s.threshold THEN 'breach'\n      WHEN s.p_last IS NULL THEN 'dark'"}
  - {id: q03, what: 'counter_dark and counter_low swapped; when c_last is NULL the floor comparison is NULL, so neither branch can fire before the other', old: "      WHEN s.c_last IS NULL THEN 'counter_dark'\n      WHEN s.c_last < s.counter_floor THEN 'counter_low'", new: "      WHEN s.c_last < s.counter_floor THEN 'counter_low'\n      WHEN s.c_last IS NULL THEN 'counter_dark'"}
```

| mutant | killed by |
|---|---|
| m01 breach on equality | v05 |
| m02 counter floor read as strict | v08, v65 |
| m03 recall floor read as strict | v12, v30, v34, v57 |
| m04 drill window opened one day early | v13 |
| m05 no upstream propagation | v20, v21, v48, v49, v90, v91, v92, v93 |
| m06 direct upstream only | v21, v49 |
| m07 unregistered series dropped | v24, v33, v50, v60, v61, v62 |
| m08 duplicate register rows allowed | e01, e34 |
| m09 no cycle guard | e07, e08, e25, e48 |
| m10 no sentinel row | e13, e24, e34 |
| m11 only today's value read | v26, v41, v96, v106 |
| m12 divergence on the primary alone | v16, v45, v53, v54, v75, v82, v86, v89, v97, v103, v110 |
| m13 divergence on the counter alone | v17, v36, v51, v52, v72, v74, v81, v85, v87, v96, v104, v108 |
| m14 future signal rows read | v25, v31 |
| m15 no drill_min | v09, v101 |
| m16 detected above injected allowed | e11 |
| m17 upstream overrides a detector's own verdict | v23 |
| m18 drills read without a window | v13, v25, v39, v40, v44 |
| m19 counter checked before breach | v27, v100 |
| m20 no drill-recall bounds | e03, e04, e32, e40 |
| m21 in-window drill rows of an unregistered detector not read as unregistered | v60, v62 |
| m22 unregistered branch reads all history | v31, v32, v39, v40 |
| m23 signal NULL day accepted | e16 |
| m24 drill NULL day accepted | e17 |
| m25 drill NULL injected accepted | e18 |
| m26 negative detected accepted | e12, e19 |
| m27 NULL upstream entry accepted | e20 |
| m28 NULL counter_floor accepted | e21 |
| m29 NULL drill_min accepted | e22 |
| m30 NULL drill_recall_min accepted | e23 |
| m31 sentinel ignores a malformed signal | e24 |
| m32 no eps on the primary | v36, v51, v52, v74, v81, v85, v87, v96, v104, v108 |
| m33 early half drops its last day | v37 |
| m34 recall floor read by multiplication | v34 |
| m35 comparison = read as <> in: SELECT r.d, e.u, r.depth + 1 FROM reach r JOIN edge e ON e.d = r.u | v21, e07, v49 |
| m36 comparison > read as >= in: w AS (SELECT * FROM {signal} WHERE day > {today} - {window_days} AND day <= {today}), | v02 |
| m37 comparison <= read as < in: w AS (SELECT * FROM {signal} WHERE day > {today} - {window_days} AND day <= {today}), | v04, v07, v24, e09, e10, v64, v66, v67, e24, e26, e27, e28, e29, e36, e37, e43, e44, e49 |
| m38 comparison <= read as < in: dw AS (SELECT * FROM {drills} WHERE day > {today} - {window_days} AND day <= {today}), | v55, v62 |
| m39 comparison < read as <= in: OR NOT isfinite(threshold) OR counter_floor < 0 OR counter_floor > 1 | v51, v52, v53, v54, v65 |
| m40 comparison > read as >= in: OR NOT isfinite(threshold) OR counter_floor < 0 OR counter_floor > 1 | v65 |
| m41 comparison < read as <= in: OR drill_min < 1 OR drill_recall_min <= 0 OR drill_recall_min > 1) AS bad_register, | v56 |
| m42 comparison <= read as < in: OR drill_min < 1 OR drill_recall_min <= 0 OR drill_recall_min > 1) AS bad_register, | e04 |
| m43 comparison > read as >= in: OR drill_min < 1 OR drill_recall_min <= 0 OR drill_recall_min > 1) AS bad_register, | v57 |
| m44 comparison = read as <> in: (SELECT count(*) FROM reach WHERE d = u) AS cycle, | v20, v21, v22, v23, e08, v48, v49, v90, v91, v92, v93, e48 |
| m45 comparison < read as <= in: + (SELECT count(*) FROM w WHERE detector IS NULL OR NOT isfinite(primary_value) OR counter | v64 |
| m46 comparison > read as >= in: + (SELECT count(*) FROM w WHERE detector IS NULL OR NOT isfinite(primary_value) OR counter | v01, v03, v04, v05, v07, v09, v10, v11, v12, v13, v14, v15, v16, v17, v18, v20, v21, v22, v23, v24, v25, v26, v29, v30, e11, e12, e15, v31, v32, v33, v34, v36, v37, v39, v40, v41, v42, v43, v44, v45, v46, v47, v48, v49, v50, v55, v56, v57, v58, v59, v60, v61, v62, v63, v64, v65, v66, v67, v68, v69, v70, v71, v72, v73, v74, v75, v76, v77, v79, v80, v81, v82, v83, v85, v87, v88, v89, v90, v91, v92, v93, v94, v95, v96, v97, v98, v99, v101, v102, v103, v104, v105, v106, v107, v108, v109, v110, e17, e18, e19, e35 |
| m47 comparison > read as >= in: + (SELECT count(*) FROM (SELECT detector, day FROM w GROUP BY ALL HAVING count(*) > 1)) AS | v01, v03, v04, v05, v06, v07, v08, v09, v10, v11, v12, v13, v14, v15, v16, v17, v18, v19, v20, v21, v22, v23, v24, v25, v26, v27, v28, v29, v30, e11, e12, e15, v31, v32, v33, v34, v35, v36, v37, v38, v39, v40, v41, v42, v43, v44, v45, v46, v47, v48, v49, v50, v51, v52, v53, v54, v55, v56, v57, v58, v59, v60, v61, v62, v63, v64, v65, v66, v67, v68, v69, v70, v71, v72, v73, v74, v75, v76, v77, v78, v79, v80, v81, v82, v83, v84, v85, v86, v87, v88, v89, v90, v91, v92, v93, v94, v95, v96, v97, v98, v99, v100, v101, v102, v103, v104, v105, v106, v107, v108, v109, v110, e17, e18, e19, e35 |
| m48 comparison < read as <= in: OR detected < 0 OR detected > injected) AS bad_drills | v58, v59 |
| m49 comparison > read as >= in: OR detected < 0 OR detected > injected) AS bad_drills | v01, v02, v03, v04, v05, v06, v07, v08, v09, v14, v15, v16, v17, v18, v19, v20, v21, v22, v23, v24, v25, v26, v27, v29, v31, v32, v33, v35, v36, v37, v38, v39, v40, v41, v42, v43, v44, v45, v46, v47, v48, v49, v50, v51, v52, v53, v54, v55, v56, v57, v58, v60, v61, v62, v63, v64, v65, v66, v67, v69, v70, v71, v72, v73, v74, v75, v76, v77, v78, v80, v81, v82, v83, v84, v85, v86, v87, v88, v89, v90, v91, v92, v93, v94, v95, v96, v97, v98, v99, v100, v103, v104, v105, v106, v107, v108, v109, v110 |
| m50 comparison > read as >= in: round(avg(w.primary_value) FILTER (WHERE w.day > {today} - {half_days}), 9) AS p_late, | v37, v69, v71, v77, v83, v107 |
| m51 comparison <= read as < in: avg(w.counter_value) FILTER (WHERE w.day <= {today} - {half_days}) AS c_early, | v46, v110 |
| m52 comparison > read as >= in: round(avg(w.counter_value) FILTER (WHERE w.day > {today} - {half_days}), 9) AS c_late | v46, v47, v70, v78, v84, v109, v110 |
| m53 comparison = read as <> in: FROM {register} r LEFT JOIN w ON w.detector = r.detector | v01, v04, v05, v06, v07, v08, v09, v10, v11, v12, v13, v14, v15, v16, v17, v18, v19, v21, v23, v25, v26, v27, v28, v29, v30, v31, v32, v34, v35, v36, v37, v38, v39, v40, v41, v42, v43, v44, v45, v46, v47, v48, v49, v51, v52, v53, v54, v55, v56, v57, v58, v59, v60, v62, v63, v64, v65, v66, v67, v68, v69, v70, v71, v72, v73, v74, v75, v76, v77, v78, v79, v80, v81, v82, v83, v84, v85, v86, v87, v88, v89, v90, v91, v92, v94, v95, v96, v97, v98, v99, v100, v101, v102, v103, v104, v105, v106, v107, v108, v109, v110 |
| m54 comparison = read as <> in: FROM {register} r LEFT JOIN dw d ON d.detector = r.detector | v01, v05, v08, v11, v12, v14, v15, v16, v17, v18, v19, v20, v23, v24, v25, v30, v31, v32, v33, v34, v35, v36, v37, v38, v39, v40, v41, v42, v43, v44, v45, v46, v47, v50, v51, v52, v53, v54, v55, v56, v57, v58, v59, v61, v63, v68, v69, v70, v71, v72, v73, v74, v75, v76, v77, v78, v79, v80, v81, v82, v83, v84, v85, v86, v87, v88, v89, v93, v94, v95, v96, v97, v98, v99, v102, v103, v104, v105, v106, v107, v108, v109, v110 |
| m55 comparison < read as <= in: WHEN dr.inj < s.drill_min THEN 'undrilled' | v01, v05, v08, v11, v12, v14, v15, v16, v17, v18, v19, v20, v21, v22, v23, v24, v25, v29, v30, v31, v32, v33, v35, v36, v37, v38, v39, v40, v41, v42, v43, v44, v45, v46, v47, v48, v49, v50, v51, v52, v53, v54, v55, v56, v57, v58, v59, v60, v61, v62, v63, v65, v69, v70, v71, v72, v73, v74, v75, v76, v77, v78, v80, v81, v82, v83, v84, v85, v86, v87, v88, v89, v90, v91, v92, v93, v94, v95, v96, v97, v98, v99, v102, v103, v104, v105, v106, v107, v108, v109, v110 |
| m56 comparison < read as <= in: WHEN s.p_late < round(s.p_early - {eps_primary}, 9) AND s.c_late < round(s.c_early - {eps_ | v51, v52, v81, v85 |
| m57 comparison < read as <= in: WHEN s.p_late < round(s.p_early - {eps_primary}, 9) AND s.c_late < round(s.c_early - {eps_ | v53, v54, v82, v86 |
| m58 comparison = read as <> in: FROM s JOIN dr ON dr.detector = s.detector | v01, v02, v03, v04, v05, v06, v07, v08, v09, v10, v11, v12, v13, v14, v15, v16, v17, v18, v19, v20, v21, v23, v24, v25, v26, v27, v28, v30, v31, v32, v33, v34, v35, v36, v37, v38, v39, v40, v41, v42, v43, v44, v45, v46, v47, v49, v50, v51, v52, v53, v54, v55, v56, v57, v58, v59, v60, v61, v62, v63, v64, v66, v67, v68, v69, v70, v71, v72, v73, v74, v75, v76, v77, v78, v79, v80, v81, v82, v83, v84, v85, v86, v87, v88, v89, v93, v94, v95, v96, v97, v98, v99, v100, v101, v102, v103, v104, v105, v106, v107, v108, v109, v110 |
| m59 comparison = read as <> in: CASE WHEN o.verdict = 'ok' AND EXISTS ( | v20, v21, v23, v48, v49, v90, v91, v92, v93 |
| m60 comparison = read as <> in: SELECT 1 FROM reach a JOIN own x ON x.detector = a.u WHERE a.d = o.detector AND x.verdict  | v20, v21, v48, v49, v90, v91, v92, v93 |
| m61 comparison = read as <> in: SELECT 1 FROM reach a JOIN own x ON x.detector = a.u WHERE a.d = o.detector AND x.verdict  | v20, v48, v90, v91, v92, v93 |
| m62 comparison <> read as = in: SELECT 1 FROM reach a JOIN own x ON x.detector = a.u WHERE a.d = o.detector AND x.verdict  | v20, v21, v22, v48, v49, v90, v91, v92, v93 |
| m63 comparison > read as >= in: SELECT NULL, NULL FROM g WHERE g.bad_register + g.bad_upstream + g.cycle + g.bad_signal +  | v01, v02, v03, v04, v05, v06, v07, v08, v09, v10, v11, v12, v13, v14, v15, v16, v17, v18, v19, v20, v21, v22, v23, v24, v25, v26, v27, v28, v29, v30, v31, v32, v33, v34, v35, v36, v37, v38, v39, v40, v41, v42, v43, v44, v45, v46, v47, v48, v49, v50, v51, v52, v53, v54, v55, v56, v57, v58, v59, v60, v61, v62, v63, v64, v65, v66, v67, v68, v69, v70, v71, v72, v73, v74, v75, v76, v77, v78, v79, v80, v81, v82, v83, v84, v85, v86, v87, v88, v89, v90, v91, v92, v93, v94, v95, v96, v97, v98, v99, v100, v101, v102, v103, v104, v105, v106, v107, v108, v109, v110 |
| m64 comparison > read as >= in: WHEN g.bad_register > 0 THEN error('malformed register: NULL field, duplicate detector, dr | v01, v02, v03, v04, v05, v06, v07, v08, v09, v10, v11, v12, v13, v14, v15, v16, v17, v18, v19, v20, v21, v22, v23, v24, v25, v26, v27, v28, v29, v30, e06, e07, e08, e09, e10, e11, e12, e13, e15, v31, v32, v33, v34, v35, v36, v37, v38, v39, v40, v41, v42, v43, v44, v45, v46, v47, v48, v49, v50, v51, v52, v53, v54, v55, v56, v57, v58, v59, v60, v61, v62, v63, v64, v65, v66, v67, v68, v69, v70, v71, v72, v73, v74, v75, v76, v77, v78, v79, v80, v81, v82, v83, v84, v85, v86, v87, v88, v89, v90, v91, v92, v93, v94, v95, v96, v97, v98, v99, v100, v101, v102, v103, v104, v105, v106, v107, v108, v109, v110, e16, e17, e18, e19, e20, e24, e25, e26, e27, e28, e29, e35, e36, e37, e43, e44, e45, e47, e48, e49 |
| m65 comparison > read as >= in: WHEN g.bad_upstream > 0 THEN error('malformed register: upstream names an unregistered det | v01, v02, v03, v04, v05, v06, v07, v08, v09, v10, v11, v12, v13, v14, v15, v16, v17, v18, v19, v20, v21, v22, v23, v24, v25, v26, v27, v28, v29, v30, e07, e08, e09, e10, e11, e12, e13, e15, v31, v32, v33, v34, v35, v36, v37, v38, v39, v40, v41, v42, v43, v44, v45, v46, v47, v48, v49, v50, v51, v52, v53, v54, v55, v56, v57, v58, v59, v60, v61, v62, v63, v64, v65, v66, v67, v68, v69, v70, v71, v72, v73, v74, v75, v76, v77, v78, v79, v80, v81, v82, v83, v84, v85, v86, v87, v88, v89, v90, v91, v92, v93, v94, v95, v96, v97, v98, v99, v100, v101, v102, v103, v104, v105, v106, v107, v108, v109, v110, e16, e17, e18, e19, e24, e25, e26, e27, e28, e29, e35, e36, e37, e43, e44, e45, e48, e49 |
| m66 comparison > read as >= in: WHEN g.cycle > 0 THEN error('malformed register: upstream cycle') | v01, v02, v03, v04, v05, v06, v07, v08, v09, v10, v11, v12, v13, v14, v15, v16, v17, v18, v19, v20, v21, v22, v23, v24, v25, v26, v27, v28, v29, v30, e09, e10, e11, e12, e13, e15, v31, v32, v33, v34, v35, v36, v37, v38, v39, v40, v41, v42, v43, v44, v45, v46, v47, v48, v49, v50, v51, v52, v53, v54, v55, v56, v57, v58, v59, v60, v61, v62, v63, v64, v65, v66, v67, v68, v69, v70, v71, v72, v73, v74, v75, v76, v77, v78, v79, v80, v81, v82, v83, v84, v85, v86, v87, v88, v89, v90, v91, v92, v93, v94, v95, v96, v97, v98, v99, v100, v101, v102, v103, v104, v105, v106, v107, v108, v109, v110, e16, e17, e18, e19, e24, e26, e27, e28, e29, e35, e36, e37, e43, e44, e45, e49 |
| m67 comparison > read as >= in: WHEN g.bad_signal > 0 THEN error('malformed signal: NULL key, non-finite value, counter ou | v01, v02, v03, v04, v05, v06, v07, v08, v09, v10, v11, v12, v13, v14, v15, v16, v17, v18, v19, v20, v21, v22, v23, v24, v25, v26, v27, v28, v29, v30, e11, e12, e13, e15, v31, v32, v33, v34, v35, v36, v37, v38, v39, v40, v41, v42, v43, v44, v45, v46, v47, v48, v49, v50, v51, v52, v53, v54, v55, v56, v57, v58, v59, v60, v61, v62, v63, v64, v65, v66, v67, v68, v69, v70, v71, v72, v73, v74, v75, v76, v77, v78, v79, v80, v81, v82, v83, v84, v85, v86, v87, v88, v89, v90, v91, v92, v93, v94, v95, v96, v97, v98, v99, v100, v101, v102, v103, v104, v105, v106, v107, v108, v109, v110, e17, e18, e19, e35 |
| m68 comparison > read as >= in: WHEN g.bad_drills > 0 THEN error('malformed drill: NULL field, negative count or detected  | v01, v02, v03, v04, v05, v06, v07, v08, v09, v10, v11, v12, v13, v14, v15, v16, v17, v18, v19, v20, v21, v22, v23, v24, v25, v26, v27, v28, v29, v30, v31, v32, v33, v34, v35, v36, v37, v38, v39, v40, v41, v42, v43, v44, v45, v46, v47, v48, v49, v50, v51, v52, v53, v54, v55, v56, v57, v58, v59, v60, v61, v62, v63, v64, v65, v66, v67, v68, v69, v70, v71, v72, v73, v74, v75, v76, v77, v78, v79, v80, v81, v82, v83, v84, v85, v86, v87, v88, v89, v90, v91, v92, v93, v94, v95, v96, v97, v98, v99, v100, v101, v102, v103, v104, v105, v106, v107, v108, v109, v110 |
| m69 clause dropped: AND day <= {today} | v25, v40 |
| m70 clause dropped: OR counter_floor IS NULL | e21 |
| m71 clause dropped: OR drill_min IS NULL | e22 |
| m72 clause dropped: OR upstream IS NULL | e14 |
| m73 clause dropped: OR NOT isfinite(threshold) | e30 |
| m74 clause dropped: OR counter_floor < 0 | e31, e39, e41 |
| m75 clause dropped: OR counter_floor > 1 | e38, e42 |
| m76 clause dropped: OR drill_min < 1 | e02 |
| m77 clause dropped: OR drill_recall_min <= 0 | e04 |
| m78 clause dropped: OR drill_recall_min > 1 | e03, e32, e40 |
| m79 clause dropped: OR u NOT IN (SELECT detector FROM {register}) | e06, e47 |
| m80 clause dropped: OR NOT isfinite(primary_value) | e27, e29, e49 |
| m81 clause dropped: OR counter_value < 0 | e37, e43 |
| m82 clause dropped: OR counter_value > 1 | e26, e28, e36, e44, e45 |
| m83 clause dropped: OR detected IS NULL | e15 |
| m84 clause dropped: OR detected < 0 | e12, e19 |
| m85 clause dropped: OR detected > injected | e11 |
| m86 clause dropped: AND EXISTS (
           SELECT 1 FROM reach a JOIN | v01, v05, v08, v12, v14, v16, v17, v18, v19, v22, v24, v25, v29, v30, v31, v32, v33, v34, v35, v36, v38, v39, v40, v41, v42, v43, v44, v45, v50, v51, v52, v53, v54, v55, v56, v57, v58, v60, v61, v62, v63, v65, v72, v74, v75, v76, v80, v81, v82, v85, v86, v87, v89, v96, v97, v103, v104, v105, v106, v108, v110 |
| m87 clause dropped: AND detector NOT IN (SELECT detector FROM {registe | v01, v02, v03, v04, v05, v06, v07, v08, v09, v10, v11, v12, v13, v14, v15, v16, v17, v18, v19, v20, v21, v22, v23, v24, v25, v26, v27, v28, v29, v30, v31, v32, v33, v34, v35, v36, v37, v38, v39, v40, v41, v42, v43, v44, v45, v46, v47, v48, v49, v50, v51, v52, v53, v54, v55, v56, v57, v58, v59, v60, v61, v62, v63, v64, v65, v66, v67, v68, v69, v70, v71, v72, v73, v74, v75, v76, v77, v78, v79, v80, v81, v82, v83, v84, v85, v86, v87, v88, v89, v90, v91, v92, v93, v94, v95, v96, v97, v98, v99, v100, v101, v102, v103, v104, v105, v106, v107, v108, v109, v110 |
| m88 rounding removed: avg(w.primary_value) FILTER (WHERE w.day | v51, v81 |
| m89 rounding removed: avg(w.counter_value) FILTER (WHERE w.day | v53, v82 |
| m90 rounding removed: s.p_early - {eps_primary} | v52, v85 |
| m91 rounding removed: s.c_early - {eps_counter} | v54, v86 |
| m92 branch dropped: WHEN s.p_last IS NULL THEN 'dark' | v02, v03, v21, v49 |
| m93 branch dropped: WHEN s.p_last > s.threshold THEN 'breach' | v04, v23, v26, v27, v66, v90, v100 |
| m94 branch dropped: WHEN s.c_last IS NULL THEN 'counter_dark' | v06, v91 |
| m95 branch dropped: WHEN s.c_last < s.counter_floor THEN 'counter_low' | v07, v28, v29, v64, v67, v92 |
| m96 branch dropped: WHEN dr.inj < s.drill_min THEN 'undrilled' | v09, v10, v13, v93, v101 |
| m97 branch dropped: WHEN dr.det::DOUBLE / dr.inj < s.drill_recall_min | v11, v20, v23, v59, v68, v79, v102 |
| m98 branch dropped: WHEN s.p_late < round(s.p_early - {eps_primary}, 9 | v15, v37, v46, v47, v48, v69, v70, v71, v73, v77, v78, v83, v84, v88, v94, v95, v98, v99, v107, v109 |
| m99 branch dropped: WHEN g.bad_register > 0 THEN error('malformed regi | e01, e02, e03, e04, e05, e14, e21, e22, e23, e30, e31, e32, e33, e34, e38, e39, e40, e41, e42, e46 |
| m100 branch dropped: WHEN g.bad_upstream > 0 THEN error('malformed regi | e06, e20, e47 |
| m101 branch dropped: WHEN g.cycle > 0 THEN error('malformed register: u | e07, e08, e25, e48 |
| m102 branch dropped: WHEN g.bad_signal > 0 THEN error('malformed signal | e09, e10, e16, e24, e26, e27, e28, e29, e36, e37, e43, e44, e45, e49 |
| m103 branch dropped: WHEN g.bad_drills > 0 THEN error('malformed drill: | e11, e12, e13, e15, e17, e18, e19, e35 |
| m104 guard term dropped: + (SELECT count(*) FROM {register} WHERE threshold | e02, e03, e04, e05, e14, e21, e22, e23, e30, e31, e32, e33, e38, e39, e40, e41, e42, e46 |
| m105 guard term dropped: + (SELECT count(*) FROM w WHERE detector IS NULL O | e10, e24, e26, e27, e28, e29, e36, e37, e43, e44, e45, e49 |
| m106 guard term dropped: + (SELECT count(*) FROM (SELECT detector, day FROM | e09 |
| m107 guard term dropped: + (SELECT count(*) FROM dw WHERE detector IS NULL | e11, e12, e13, e15, e18, e19, e35 |
| m108 unregistered rows not deduplicated (one row per day) | v50, v61 |
| m109 upstream reach capped at one hop | v49, e25 |
| m110 a diverging upstream does not propagate (staged k1 (i)) | v48 |
| m111 signal window has no lower bound | v02, v32, v42, v43 |
| m112 drill window has no lower bound | v13, v39, v44 |
| m113 NULL-detector signal row in the window accepted | e10, e24 |
| m114 NULL-detector drill row in the window accepted | e13, e35 |
| m115 duplicate signal days checked over all history | v43 |
| m116 signal value guard reads all history | v42 |
| m117 drill guard reads all history | v44 |
| m118 no eps on the counter | v45, v53, v54, v75, v82, v86, v89, v97, v103, v110 |
| m119 tolerance s.p_last > s.threshold + 0.000001 | v66 |
| m120 tolerance s.p_last > s.threshold - 0.000001 | v05 |
| m121 tolerance s.p_last > s.threshold + 0.001 | v66 |
| m122 tolerance s.p_last > s.threshold - 0.001 | v05 |
| m123 tolerance s.c_last < s.counter_floor + 0.000001 | v08, v65 |
| m124 tolerance s.c_last < s.counter_floor - 0.000001 | v67 |
| m125 tolerance s.c_last < s.counter_floor + 0.001 | v08, v65 |
| m126 tolerance s.c_last < s.counter_floor - 0.001 | v67 |
| m127 tolerance dr.det::DOUBLE / dr.inj < s.drill_recall_min + 0.000001 | v12, v30, v34, v57 |
| m128 tolerance dr.det::DOUBLE / dr.inj < s.drill_recall_min - 0.000001 | v79 |
| m129 tolerance dr.det::DOUBLE / dr.inj < s.drill_recall_min + 0.001 | v12, v30, v34, v57 |
| m130 tolerance dr.det::DOUBLE / dr.inj < s.drill_recall_min - 0.001 | v68, v79 |
| m131 tolerance round(s.p_early - {eps_primary}, 9) + 0.000001 | v51, v52, v81, v85 |
| m132 tolerance round(s.p_early - {eps_primary}, 9) - 0.000001 | v69, v83 |
| m133 tolerance round(s.p_early - {eps_primary}, 9) + 0.001 | v51, v52, v81, v85, v87 |
| m134 tolerance round(s.p_early - {eps_primary}, 9) - 0.001 | v69, v71, v77, v83, v94, v107 |
| m135 tolerance round(s.c_early - {eps_counter}, 9) + 0.000001 | v53, v54, v82, v86 |
| m136 tolerance round(s.c_early - {eps_counter}, 9) - 0.000001 | v70, v84 |
| m137 tolerance round(s.c_early - {eps_counter}, 9) + 0.001 | v53, v54, v82, v86 |
| m138 tolerance round(s.c_early - {eps_counter}, 9) - 0.001 | v47, v70, v78, v84, v109 |
| m139 tolerance OR drill_recall_min <= 0 + 0.000001 | v80 |
| m140 tolerance OR drill_recall_min <= 0 - 0.000001 | e04 |
| m141 tolerance OR drill_recall_min <= 0 + 0.001 | v80 |
| m142 tolerance OR drill_recall_min <= 0 - 0.001 | e04 |
| m143 tolerance OR drill_recall_min > 1 + 0.000001 | e40 |
| m144 tolerance OR drill_recall_min > 1 - 0.000001 | v57 |
| m145 tolerance OR drill_recall_min > 1 + 0.001 | e40 |
| m146 tolerance OR drill_recall_min > 1 - 0.001 | v57 |
| m147 tolerance OR counter_floor < 0 + 0.000001 | v51, v52, v53, v54, v65 |
| m148 tolerance OR counter_floor < 0 - 0.000001 | e41 |
| m149 tolerance OR counter_floor < 0 + 0.001 | v51, v52, v53, v54, v65 |
| m150 tolerance OR counter_floor < 0 - 0.001 | e41 |
| m151 tolerance OR counter_floor > 1 + 0.000001 | e42 |
| m152 tolerance OR counter_floor > 1 - 0.000001 | v65 |
| m153 tolerance OR counter_floor > 1 + 0.001 | e42 |
| m154 tolerance OR counter_floor > 1 - 0.001 | v65 |
| m155 tolerance OR counter_value < 0 + 0.000001 | v64 |
| m156 tolerance OR counter_value < 0 - 0.000001 | e43 |
| m157 tolerance OR counter_value < 0 + 0.001 | v64 |
| m158 tolerance OR counter_value < 0 - 0.001 | e43 |
| m159 tolerance OR counter_value > 1 + 0.000001 | e44, e45 |
| m160 tolerance OR counter_value > 1 - 0.000001 | v01, v03, v04, v05, v07, v09, v10, v11, v12, v13, v14, v15, v16, v17, v18, v20, v21, v22, v23, v24, v25, v26, v29, v30, e11, e12, e15, v31, v32, v33, v34, v36, v37, v39, v40, v41, v42, v43, v44, v45, v46, v47, v48, v49, v50, v55, v56, v57, v58, v59, v60, v61, v62, v63, v64, v65, v66, v67, v68, v69, v70, v71, v72, v73, v74, v75, v76, v77, v79, v80, v81, v82, v83, v85, v87, v88, v89, v90, v91, v92, v93, v94, v95, v96, v97, v98, v99, v101, v102, v103, v104, v105, v106, v107, v108, v109, v110, e17, e18, e19, e35 |
| m161 tolerance OR counter_value > 1 + 0.001 | e44, e45 |
| m162 tolerance OR counter_value > 1 - 0.001 | v01, v03, v04, v05, v07, v09, v10, v11, v12, v13, v14, v15, v16, v17, v18, v20, v21, v22, v23, v24, v25, v26, v29, v30, e11, e12, e15, v31, v32, v33, v34, v36, v37, v39, v40, v41, v42, v43, v44, v45, v46, v47, v48, v49, v50, v55, v56, v57, v58, v59, v60, v61, v62, v63, v64, v65, v66, v67, v68, v69, v70, v71, v72, v73, v74, v75, v76, v77, v79, v80, v81, v82, v83, v85, v87, v88, v89, v90, v91, v92, v93, v94, v95, v96, v97, v98, v99, v101, v102, v103, v104, v105, v106, v107, v108, v109, v110, e17, e18, e19, e35 |
| m163 precision 2: avg(w.primary_value) FILTER (WHERE w.day > {today} | v69, v71, v87, v88, v94, v107 |
| m164 precision 4: avg(w.primary_value) FILTER (WHERE w.day > {today} | v69 |
| m165 precision 6: avg(w.primary_value) FILTER (WHERE w.day > {today} | v69 |
| m166 precision 12: avg(w.primary_value) FILTER (WHERE w.day > {today} | v81 |
| m167 precision 2: avg(w.counter_value) FILTER (WHERE w.day > {today} | v47, v70, v109 |
| m168 precision 4: avg(w.counter_value) FILTER (WHERE w.day > {today} | v70 |
| m169 precision 6: avg(w.counter_value) FILTER (WHERE w.day > {today} | v70 |
| m170 precision 12: avg(w.counter_value) FILTER (WHERE w.day > {today} | v82 |
| m171 precision 2: s.p_early - {eps_primary} | v77, v83 |
| m172 precision 4: s.p_early - {eps_primary} | v77, v83 |
| m173 precision 6: s.p_early - {eps_primary} | v83 |
| m174 precision 12: s.p_early - {eps_primary} | v85 |
| m175 precision 2: s.c_early - {eps_counter} | v46, v78, v84 |
| m176 precision 4: s.c_early - {eps_counter} | v78, v84 |
| m177 precision 6: s.c_early - {eps_counter} | v84 |
| m178 precision 12: s.c_early - {eps_counter} | v86 |
| m179 aggregate arg_max(w.primary_value, w.day) -> arg_min(w.primary_value, w.day) | v04, v26, v66, v87 |
| m180 aggregate arg_max(w.primary_value, w.day) -> max(w.primary_value) | v37, v76, v87 |
| m181 aggregate arg_max(w.primary_value, w.day) -> min(w.primary_value) | v04, v26, v66 |
| m182 aggregate arg_max(w.counter_value, w.day) -> arg_min(w.counter_value, w.day) | v07, v64, v67 |
| m183 aggregate arg_max(w.counter_value, w.day) -> max(w.counter_value) | v07, v64, v67 |
| m184 aggregate arg_max(w.counter_value, w.day) -> min(w.counter_value) | v72 |
| m185 aggregate avg -> min on w.primary_value <= | v37 |
| m186 aggregate avg -> max on w.primary_value <= | v87, v108 |
| m187 aggregate avg -> median on w.primary_value <= | v37, v108 |
| m188 aggregate avg -> min on w.primary_value > | v74 |
| m189 aggregate avg -> max on w.primary_value > | v88, v94 |
| m190 aggregate avg -> median on w.primary_value > | v88, v94 |
| m191 aggregate avg -> min on w.counter_value <= | v46 |
| m192 aggregate avg -> max on w.counter_value <= | v75, v110 |
| m193 aggregate avg -> median on w.counter_value <= | v46, v110 |
| m194 aggregate avg -> min on w.counter_value > | v89 |
| m195 aggregate avg -> max on w.counter_value > | v73, v95 |
| m196 aggregate avg -> median on w.counter_value > | v73, v95 |
| m197 aggregate coalesce(sum(d.injected), 0) AS inj -> coalesce(max(d.injected), 0) AS inj | v30 |
| m198 aggregate coalesce(sum(d.injected), 0) AS inj -> coalesce(min(d.injected), 0) AS inj | v30, v58 |
| m199 aggregate coalesce(sum(d.detected), 0) AS det -> coalesce(max(d.detected), 0) AS det | v30 |
| m200 aggregate coalesce(sum(d.detected), 0) AS det -> coalesce(min(d.detected), 0) AS det | v30, v58 |
| m201 arithmetic s.p_early - {eps_primary} -> s.p_early + {eps_primary} | v17, v36, v51, v52, v72, v74, v81, v85, v87, v96, v104, v108 |
| m202 arithmetic s.c_early - {eps_counter} -> s.c_early + {eps_counter} | v16, v45, v53, v54, v75, v82, v86, v89, v97, v103, v110 |
| m203 arithmetic dr.det::DOUBLE / dr.inj -> dr.det // dr.inj | v12, v30, v34 |
| m204 arithmetic r.depth + 1 -> r.depth + 2 | e25 |
| m205 order verdict: swap WHEN s.p_last > s.threshold THEN 'breach / WHEN s.c_last IS NULL THEN 'counter_dark | v100 |
| m206 order verdict: swap WHEN s.c_last < s.counter_floor THEN 'co / WHEN dr.inj < s.drill_min THEN 'undrille | v28 |
| m207 order verdict: swap WHEN dr.inj < s.drill_min THEN 'undrille / WHEN dr.det::DOUBLE / dr.inj < s.drill_r | v101 |
| m208 order verdict: swap WHEN dr.det::DOUBLE / dr.inj < s.drill_r / WHEN s.p_late < round(s.p_early - {eps_p | v102 |
| m209 order verdict: move to top WHEN s.c_last IS NULL THEN 'counter_dark' | v02, v21, v49, v100 |
| m210 order verdict: move to top WHEN s.c_last < s.counter_floor THEN 'counter_low' | v27 |
| m211 order verdict: move to top WHEN dr.inj < s.drill_min THEN 'undrilled' | v28 |
| m212 order verdict: move to top WHEN dr.det::DOUBLE / dr.inj < s.drill_recall_min  | v101 |
| m213 order verdict: move to top WHEN s.p_late < round(s.p_early - {eps_primary}, 9 | v102 |
| m214 order error: swap WHEN g.bad_register > 0 THEN error('malf / WHEN g.bad_upstream > 0 THEN error('malf | e46 |
| m215 order error: swap WHEN g.bad_upstream > 0 THEN error('malf / WHEN g.cycle > 0 THEN error('malformed r | e47 |
| m216 order error: swap WHEN g.cycle > 0 THEN error('malformed r / WHEN g.bad_signal > 0 THEN error('malfor | e48 |
| m217 order error: swap WHEN g.bad_signal > 0 THEN error('malfor / WHEN g.bad_drills > 0 THEN error('malfor | e49 |
| m218 order error: move to top WHEN g.cycle > 0 THEN error('malformed register: u | e47 |
| m219 order error: move to top WHEN g.bad_signal > 0 THEN error('malformed signal | e48 |
| m220 order error: move to top WHEN g.bad_drills > 0 THEN error('malformed drill: | e49 |
| m221 propagation skips dark | v21, v49 |
| m222 propagation skips breach | v90 |
| m223 propagation skips counter_dark | v91 |
| m224 propagation skips counter_low | v92 |
| m225 propagation skips undrilled | v93 |
| m226 propagation skips blind | v20 |
| m227 NULL read as 0 in primary early half | v98 |
| m228 half split + 1 day on primary early half | v69, v71, v77, v83, v94 |
| m229 half split - 1 day on primary early half | v37 |
| m230 NULL read as 0 in primary late half | v96 |
| m231 half split + 1 day on primary late half | v94 |
| m232 half split - 1 day on primary late half | v37, v69, v71, v77, v83, v107 |
| m233 NULL read as 0 in counter early half | v99 |
| m234 half split + 1 day on counter early half | v47, v70, v78, v84, v95 |
| m235 half split - 1 day on counter early half | v46, v110 |
| m236 NULL read as 0 in counter late half | v97 |
| m237 half split + 1 day on counter late half | v95 |
| m238 half split - 1 day on counter late half | v46, v47, v70, v78, v84, v109, v110 |
| m239 param eps_counter read as eps_primary | v103 |
| m240 param eps_primary read as eps_counter | v104 |
| m241 param signal window read as 2 x half_days | v106 |
| m242 param drill window read as 2 x half_days | v105 |
| m243 param half_days read as window_days / 2 on primary early half | v108 |
| m244 param half_days read as window_days / 2 on primary late half | v107, v108 |
| m245 param half_days read as window_days / 2 on counter early half | v110 |
| m246 param half_days read as window_days / 2 on counter late half | v109, v110 |

## 3. Settled / contested / risk / open

### Settled (fixture s1-s3)

- **s1.** Every pilot item carries exactly one failure_signal, and that signal is where its Goodhart
  question is answered. Precedent: the pilot model requires a failure_signal and the evaluator's L1 keeps
  it unique per item (merged code, CD.45 W0, operator-approved 2026-10-02); Decision 196 clause 7 asks the same question of
  Tier A decisions and is used only by analogy. Data plane only: the register reads the customer's own
  telemetry and review rows (Decision 209 clause 2(a)); whether any verdict crosses is #1399's k1.
- **s2.** Measured: in 8 of 8 W1 items a stated cheap path lowers the failure_signal at least as far as a
  real halving of failures, and the staged counter separates the two in 8 of 8 (VP 3).
- **s3.** Measured: 4 of 8 failure_signals name a companion, 1 a seeded known-positive, 0 a recall check on
  the top-rung trigger; the companions computable in the model catch 1 of 2 cheap paths (VP 1, VP 3).

### Contested (k1-k3 in the fixture; k4-k6 report-only, the fixture's contested list is capped at 3)

- **k1 (asked). What a non-ok verdict does on the ladder.** (a) It blocks promotion and demotes one rung,
  as #1398's staged dark-signal leg does (its k7 (a)). (b) It blocks promotion only; the rung holds. (c) Advisory: logged beside the
  transition record, nothing gates. Recommended (a) for dark, blind, undrilled, counter_dark, counter_low
  and unregistered, and (b) for diverging and upstream_unsound, which are weaker evidence. For dark, (a) is
  the action #1398's own dark-signal leg already takes, so the register adds a reading, not a second
  demotion. For breach, the recommendation is to drop the leg (v below); while it stays, (b) for the
  breaching detector only. #1398 measured that
  a half-blind monitor also lets more errors escape below the top rung (2.09% on its worst seed), so
  blocking promotion alone leaves that. No Decision or contract decides it; #1398 is unmerged and its own
  k1 and k7 are parked. Two choices inside the staged SQL belong to this fork and are not decided:
  (i) propagation strength: any non-ok upstream, diverging included, propagates upstream_unsound at full
  strength, where the alternatives are to propagate only the strong verdicts or to carry a weaker grade;
  (ii) precedence: breach before the counter, counter_low before undrilled, and a detector's own verdict
  before upstream, which decides the single verdict a detector with several faults reports, and so which
  action fires. (iii) What the ladder does with an unregistered row, which has no rung of its own to
  demote: staged, the ladder ignores rows whose detector is a dated drill- series (the register's own
  drill, as unsound_reads does) and blocks every promotion while any other unregistered row exists, so a
  retired detector freezes promotion for 27 days (v61); the alternative is to treat it as a register defect
  for the operator and block nothing. (iv) Staleness: staged, the latest defined value anywhere in the
  window decides p_last and c_last (v41), which fails open on a counter that stops arriving (v63); the
  alternative is a freshness bound (for example counter_dark when no counter arrived in the last 2 days),
  which needs a per-detector cadence. (v) The breach leg. Staged: each row's threshold is borrowed from
  its item's own top-rung trigger (section 2.2), so it rests on eight unmerged PRs, one marked provisional
  and one, #1398's AOQL target, its open q3; a latest primary above it reads breach, a non-ok verdict that
  propagates downstream at full strength (v90). As staged, the ladder's row cannot be loaded until #1398 q3
  is answered, and loaded with a NULL threshold it raises the whole run (plan-critique r1, S1). One capture
  reading of 0.06 on every day makes 7 of the 8 detectors read unsound although capture's counter and drill
  are sound (S3). A breach is component health, not a Goodhart unsoundness. Alternatives: (a) a
  register-owned threshold the operator sets per row; (b) no breach leg, so the register reads soundness
  only and component health stays with each item's own top-rung trigger; (c) keep the leg but do not
  propagate breach. Recommended (b): it removes the #1398 q3 dependency and the S3 fan-out, and the ladder
  already reads each item's own trigger. All five stay as staged until k1 is answered. Consequence to
  weigh: capture's counter needs its q1 out-of-band denominator, which
  does not exist, so under (a) or (b) capture reads counter_dark from day one and every detector downstream
  reads upstream_unsound until it lands (risk R6).
- **k2 (asked). How drills run.** (a) Live: seeded rows written through the real producers under a
  dedicated drill project_id, excluded from every consumer total by that id (the pattern #1390's report
  records for rec-4025's T3.2 assert over a synthetic project_id). (b) Replay: each detector's SQL run
  offline over seeded rows, never touching production tables. (c) Operator-labelled samples only. Recommended
  (b) for the six SQL detectors and (a) only for capture and the canary, whose failures live in the
  pipeline, not in the SQL. (a) needs every consumer to exclude the drill project_id (no contract field marks
  a drill row today, e5); (b) cannot see a live-pipeline loss, which is why capture's counter is an
  out-of-band count. The ladder's reviewer drill (known-wrong outputs in the review sample) is a choice about
  the operator's own review stream and sits inside this fork. So does how the register's own drill stays
  live: staged, one dated drill- series a day read over the 28-day window (section 2.4); the alternative is a
  today-only unregistered read. Build note: the ladder and unsound_reads should ignore only the run's own
  exact drill-YYYY-MM-DD, not every name with the drill- prefix, or a register row named drill-% should
  raise, so no real detector can hide in that namespace (plan-critique r1 N1). So does retirement and
  onboarding: staged, in-window signal and drill rows of
  a detector with no register row both read unregistered, so neither halts the run (v60-v62); the
  alternatives are a retired_on marker on the register row, so in-window history still resolves, or a halt
  with a stated 28-day retirement procedure. So does the blast radius of malformed input: staged, any guard
  firing raises the whole run, suppressing every detector's verdict for up to 27 days (e45), and the ladder
  holds every rung on a day without verdicts. The alternatives: (a) a per-detector malformed verdict for
  signal and drill defects, keeping the whole-run raise for register defects only; (b) quarantine the
  offending row and read the rest; (c) keep the halt with a stated repair procedure (correct or delete the
  row). Only Decision 55, by analogy, bears on it, and it asks for loud failure, not wide failure.
- **k3 (asked). Where counters and drills are declared.** (a) In this item's register rows only (staged).
  (b) Two new FailureSignal fields, counter and drill, on every pilot item, with the evaluator requiring them
  (a pilot-schema change, the evaluator owner's, O1). (c) In each item's prose. Recommended (a) now and (b) at
  lift, so a detector cannot be added without its counter. (b) touches the evaluator, which a design slice may
  not change.
- **k4 (asked; report-only). Harm weights** (#1398 R6). (a) Uniform: every detector's verdict counts the
  same. (b) Per-detector weights the operator sets, read by the ladder's AOQL target. (c) Derived from what the
  detector gates: an egress or a proof close above a filing above a label. Recommended (c) as the starting
  order, set by the operator. No precedent.
- **k5 (asked; report-only). The fixture item's edge home.** (a) depends_on T2.36 only (carried
  provisionally): the signals and drills are telemetry reader rows. (b) part_of T3.4, beside the maturity
  ladder, whose exit criterion 2 asks for gates with rollback. (c) part_of T3.3, whose false-positive
  threshold (audit F-035) is one of these rows. Recommended (a) until W2 places the ladder.
- **k6 (asked; report-only). The divergence rule.** (a) Half-window means with an absolute eps on each
  (staged; v15-v19). (b) A per-row statistical test on both series (for example a two-proportion test at a
  stated alpha), which needs per-day denominators. (c) No divergence leg; the counter floor alone. Recommended
  (a) for the pilot: it is one comparison and fails toward review, and (b) once denominators are recorded.
  (c) misses slow drift above the floor (v15 is above its floor). Under (a) the boundary arithmetic is
  staged exact to 9 decimal places (recall by division, 9-place rounding, section 2.4); the alternative is to
  state both rules as approximate. Recall is pooled over the window (v30: drills sum across it); the
  alternative is a per-run recall floor, which R5's small counts make noisier. Staleness moved to k1 (iv):
  it decides p_last and c_last, not the divergence rule.

### Risks

- **R1 Teaching to the drill.** A rule set, a model or a person that can tell drill rows from real ones
  can pass every drill and still miss real faults. Drills must be drawn from the real distribution and
  rotated, and no detector rule may branch on the drill marker (the exclusion happens after detection).
- **R2 Counters can be gamed too.** A counter computed from the same rows by the same code as the primary
  shares its blind spot (a second-order Goodhart). Each row's counter source is chosen to be independent of
  the primary's numerator; the build's c2 replay checks the counter moves, not that it is independent.
- **R3 Drill rows in the warehouse.** Under k2 (a), a consumer that forgets the drill project_id counts
  seeded failures as real. No contract field marks a drill row (e5).
- **R4 Cost.** Drills are runs, and the reviewer drill adds review items. Both are recurring cost, owned by
  the cost/egress component; no fork here decides spend.
- **R5 Small counts.** drill_min 20 per 28 days is under one fault a day; a recall floor of 0.9 over 20
  faults passes a 90%-recall detector only about two times in three (#1398 measured 68%). Seeds, q2.
- **R6 A dark root freezes the loop.** Propagation is fail-safe by design: when capture is not ok, nothing
  downstream promotes. Until capture's q1 denominator exists that is every day (k1). A breaching root does
  the same on an ordinary bad day for a working detector: one capture reading of 0.06 freezes 7 of 8 (k1
  (v)).
- **R7 The model is a model.** Section 2.3's worlds are deterministic and built from each sibling's own
  definitions; they show directions, not rates. The build's c2 replays each cheap path on the real metric.

### Open questions (q1-q3 in the fixture)

- **q1.** Where the out-of-band counts and drill logs live: capture's q1 session denominator, rec-4025's
  harness widened to shadow mode (#1390 q2, a queue change for the operator), the friction label review
  (#1394 q2), and the verdict log itself (shares #1398 q2 and #1396 q4).
- **q2.** Seeds are unmeasured: counter floor 0.99, drill_min 20 per 28 days at recall 0.9, eps 0.01 on
  14-day halves. Who sets per-detector values, and from what measurement?
- **q3.** Is the register's verdict one input to #1398's controller, replacing its top-rung drill with a
  drill at every rung (its k5 (a3)), or does the controller keep its own drill and read the register
  beside it? This report reads one input.

### Named for owners, not filed (Decision 67)

- **O1** The pilot FailureSignal model has no counter or drill field (e4), so no item can declare its guard
  in a form the evaluator checks (evaluator owner; k3 (b)).
- **O2** A hook row with no integer exit code is written severity info (e11), so a hook that fails without
  reporting a code leaves unmapped_failure_share's denominator. Whether Claude Code omits exitCode on a hook
  timeout is not verified here (inferred; telemetry producer owner).
- **O3** No telemetry contract carries a field that marks a seeded drill row (e5); a live drill (k2 (a))
  needs one, or a reserved project_id with an exclusion rule every consumer honours (telemetry contract owner).

## 4. Consideration register (as authored in the fixture)

- why: all 8 W1 failure_signals are lower-is-better and each has a cheap path that lowers it as far as a
  real halving of failures (VP 3); 4 name a companion, 1 a seeded positive, 0 a recall check (VP 1).
- how: one register row per detector (metric, cheap path, counter, drill, upstreams) and one daily SQL that
  returns one of ten verdicts per detector; the ladder reads it (k1).
- planes: data_plane.
- maturity (thresholds are provisional seeds; plan fork line): read_all, the operator re-derives every
  verdict; to sampled after 40 verdicts re-derived unchanged since the last overturned one; to spot_check
  after 40 more on a 1-in-5 sample; to anomaly_triggered after 30 consecutive days in which the register's
  own drill (one injected unregistered series a day, named for that day) reads unregistered. The return leg and version rule are
  #1398's k1 and k2.
- failure_signal: a detector reads sound while it is not. Metric unsound_reads: series that reached the
  ladder with no register row, plus register rows whose counter held over its floor in their own cheap-path
  replay; must be 0. Source: the verdict log (unregistered rows other than the dated drill- series) and the
  c2 replay run on every register version.
- verification: c1 vectors, c2 cheap-path replay per row, c3 operator review of every register row.
- rollback: stop the verdict schedule; the ladder reads its other inputs as before; register rows and drill
  logs stay as history.

## 5. Boundary notes for W2 synthesis

- **Maturity ladder (#1398).** The register's verdict is one series with the controller's inputs: the
  failure_signal it reads per component must be the register row's metric (that report's own boundary note).
  Contradiction for W2: #1398 stages a drill at spot_check only (its k5 (a)) and names a drill at every rung
  as a candidate cure (its k5 (a3)); this register stages a drill verdict at every rung. W2 picks one home.
  R3 (reviewer error) and R6 (harm weights) from that report are rows 7 and k4 here.
- **Capture (#1384).** Its q1 out-of-band denominator is this register's capture counter; until it exists,
  capture is counter_dark and the whole loop is upstream_unsound (R6). W2 should sequence q1 first.
- **Reader verbs (#1390).** Its q2 (rec-4025 widened to shadow mode) is the source of the reader counter;
  "no" leaves that row undrilled.
- **Friction (#1394).** Its c2 guard and this row's drill are complementary: c2 stops wildcards statically,
  the drill catches lumping under an exact signature. O2 is a producer rule in its slice 3b.
- **Deliberation (#1395).** This row adds the read-side arm its report says is missing (none with
  reasoning tokens) as a counter, not as a fourth drift kind; W2 may move it into the drift monitor.
- **Rec filing (#1396) and back-validation (#1397).** They share one cheap path: a stricter filer improves
  both metrics at once. Their drills are one seeded recurring finding read twice (filed; then, after a drill
  no-op close, re-filed as a regression).
- **Allow-list (#1399).** Canary coverage is a property of the seeding, not of the egress: the canary row
  must be read back from the data-plane input each run. Register verdicts are data-plane only; any fleet
  view of them crosses only through #1399's allow-list (its k1, always-ask).
- **Cost/egress (component 10).** Drills and the reviewer drill are recurring cost; under k2 (b) they run
  offline in the data plane.
- **Upstream graph.** The register's upstream lists are the data edges W2 should author as pilot
  depends_on edges between items; the evaluator's L4 refuses an edge to a pilot item absent from this
  branch's fixture, so none is added here.

## 6. Staged candidate decision text (for W3; not filed)

This text presumes the recommended options of parked forks k1, k2 and k3. It is not settled.

> "A loop detector's failure_signal promotes nothing on its own. Every detector whose series gates an
> automated transition carries a register row naming the cheapest way its metric improves without the work
> improving, a counter series from a source independent of the metric's numerator, and a seeded drill
> that measures its recall. A daily deterministic verdict reads the register, the series and the drill
> log; a detector is sound only when its signal is present, its counter and drill are sound, and so are its
> upstream detectors. An unsound verdict blocks promotion; a dark, blind, undrilled, counter_dark,
> counter_low or unregistered verdict also demotes one rung, while diverging and upstream_unsound only
> block. A series without a register row is unsound by definition. Whether a detector breached its own
> threshold is the ladder's concern, read from that item's own trigger, not the register's."
