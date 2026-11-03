# REPORT: telemetry feedback loop design synthesis (W2)

REPORT-ONLY deliverable of PLAN-w2-feedback-loop-synthesis.yaml (Decision 86 clause 2; file-router
report-deliverables route). Design stream of the CD.45 pilot fixture
docs/work-item-pilot/telemetry-feedback-loop.yaml. Nothing here is ratified, filed, merged or flipped: the
build order, the ladder and every resolution below are recommendations staged for the operator, and every
fork that weighs alternatives against unmerged or unanswered work is parked as asked.

## 0. Verdict

The ten component designs compose into one loop, but only in a fixed order and only after four things the
loop does not own: the T2.36 writer verb, the unmerged 2a-1 write-conformance plan, the two ownership splits
with the Telemetry project (hooks for the capture wiring, reader verbs and the ducklake_scd2_schema.py split
for the reader), and an answer to the capture component's out-of-band session denominator (its q1). The
last of these is the critical path for the whole loop, not just for capture: the Goodhart register's own
rule (R6) reads capture as counter_dark until that denominator exists, and under its recommended ladder
effect every downstream detector then reads upstream_unsound, so no component can leave read_all. The
honest synthesis is that the loop is built to run entirely at read_all until the operator answers capture
q1. That pin has a cost the first draft did not state: under the recommended rung ceiling (F1), review-load
lever (F4) and acceptance of the pin (F9), the review line is already at its warning level after wave 1
(two components at read_all, about 24 reviews a day against a seed of 30 and a warning line of 24), so
wave 2's read_all period waits for capture q1 or for an operator capacity decision (an envelope of about
60 reviews a day, so that wave 2's 48 sit under the 0.8 warning line; section 3.4). Capture q1 therefore
gates the read_all run of every wave from wave 2 on (waves 3 and 4 inherit the wait by sequence), not only
promotion. The one
alternative that frees that capacity, checking monitor soundness at the top rung only, is named under F9
and not chosen.

Three findings change what the operator decides next:

1. The ten items merge into one fixture as written: 10 of 12 items, 30 criteria, 405 of 420 effective
   lines with the staged edge list, and every evaluator leg passes on the simulated merge (VP 4). The
   mechanical merge is safe in any order; the design order is what section 2 fixes. This holds for the
   staged edge list as written; if the budget's breach recommendation is routed through the filer (F13 (b))
   the filer gains a fourth edge and the evaluator rejects it, which is X21 and fork F14.
2. The ten maturity blocks carry eight distinct trigger dialects in two readings, with four different
   first-rung counts, five different second-rung windows and two different top-rung day counts (VP 2). Section 3 stages one rule family with
   per-component seeds and one coupling rule (a component's rung is capped by the lowest rung among its data
   upstreams) so that no reader can sit above the producer it reads.
3. Twenty-two cross-component contradictions remain, five points are settled. Of the twenty-two, four are
   the same question asked by six items (where do the loop's records live, who owns the review load, how the
   register verdict drives the ladder, and the fixture's edge cap against three data upstreams). Each gets
   one staged resolution in section 4 so that W3 writes one Decision, not six.

Treated as not final: #1384 (capture wiring) and #1390 (reader verbs) are still gating; every reading of
them below is pinned to the head sha in the inputs block and will be re-read when they settle.

## 1. Inputs

Each component is read at the head below, pinned by sha so that a later push to a sibling does not change
what this report says. The item id, the pilot edges each item carries today and the planes are read from
the fixture at that head (VP 1); the hand-back and parked-forks files are read from /mnt/project-files/gates/.

```yaml
inputs:
  - {pr: 1384, slug: w1-capture-producer-wiring, head: 1da1cc97f3522e5551ab32bcb41e3bb5b93cf685, item: pwi-capture-producer-wiring, final: false}
  - {pr: 1390, slug: w1-reader-verbs, head: ab7aad7a36819a10069628577a55b2160cf8bd81, item: pwi-telemetry-reader-verbs, final: false}
  - {pr: 1394, slug: w1-friction-classifier, head: 427eeced7929654fff9a1d256f17ba1387b9b71f, item: pwi-friction-classifier, final: true}
  - {pr: 1395, slug: w1-deliberation-capture, head: 4f49c55f4d25e7d9d5249f165a23760c78aae528, item: pwi-deliberation-capture, final: true}
  - {pr: 1396, slug: w1-rec-filing-dedupe, head: 51958a0ae48a700963fc5b1351fed00e6a1decec, item: pwi-rec-filing-dedupe, final: true}
  - {pr: 1397, slug: w1-back-validation, head: 75ad2896d79d7d83f91ddcb619a183972a716351, item: pwi-back-validation, final: true}
  - {pr: 1398, slug: w1-maturity-ladder, head: f618343030ac07ffeab13c0ca2288577ad5d22db, item: pwi-maturity-ladder-controller, final: true}
  - {pr: 1399, slug: w1-allow-list-transport, head: 03feaf12dda0bdb6ea8718ccdff85e931e83a942, item: pwi-allow-list-transport, final: true}
  - {pr: 1400, slug: w1-goodhart-register, head: fc298254dd0e4a0661251217f6653c3dede840e2, item: pwi-goodhart-register, final: true}
  - {pr: 1401, slug: w1-cost-egress, head: 28af242709698e4a2eb97f427b743f2583cee03e, item: pwi-loop-cost-egress-budget, final: true}
```

"final: true" means all of that component's gates returned PROCEED and the PR waits only for the operator;
it does not mean merged. The fixture on main is still empty (work_items, criteria and edges are []), so
each of the ten PRs replaces the same three lines and every merge after the first needs a merge from main and
a re-render (section 2.4).

Cross-project facts taken as given (Telemetry project answers of 2026-10-04, relayed by the coordinator):
nothing writes telemetry rows today; the 2a-1 plan (docs/plans/PLAN-telemetry-write-conformance.yaml on
claude/project-thread-vmix09 at 77e56482, unmerged) moves the claude_code producer to parser_version 2;
telemetry 3b (hooks) overlaps #1384 and telemetry 2b (reader verbs, the ducklake_scd2_schema.py split)
overlaps #1390, and both ownership splits are parked for the operator. This report orders around those
splits and does not pick an owner.

## 2. Dependency graph and build order

### 2.1 Data edges between components

The register in #1400 declares, for every detector, which other detectors' rows it reads. Those lists are
the loop's data graph; the item-level edges below restate them one per pair (VP 3 checks that every edge
runs from an earlier build wave to a later one). Eleven of the fourteen edges are the register's upstream
lists verbatim; the register-to-reader, budget-to-reader and ladder-to-register edges are this report's
additions (the register lists the ladder with no upstream), and the last of them is contested (X16).

```yaml
data_edges:
  - {from: pwi-telemetry-reader-verbs, to: pwi-capture-producer-wiring, reads: "lifecycle, turn and tool rows capture lands"}
  - {from: pwi-friction-classifier, to: pwi-capture-producer-wiring, reads: "process_events and tool outcomes"}
  - {from: pwi-friction-classifier, to: pwi-telemetry-reader-verbs, reads: "session_friction_rollup is a reader verb"}
  - {from: pwi-deliberation-capture, to: pwi-capture-producer-wiring, reads: "model_call rows with visibility"}
  - {from: pwi-deliberation-capture, to: pwi-telemetry-reader-verbs, reads: "session_deliberation_rollup is a reader verb"}
  - {from: pwi-rec-filing-dedupe, to: pwi-telemetry-reader-verbs, reads: "sessions and sessions_window for recurrence"}
  - {from: pwi-rec-filing-dedupe, to: pwi-friction-classifier, reads: "friction fingerprints are the filer's subjects"}
  - {from: pwi-rec-filing-dedupe, to: pwi-deliberation-capture, reads: "deliberation roll-up as a filer source"}
  - {from: pwi-back-validation, to: pwi-rec-filing-dedupe, reads: "filer recs carry the subject and the fix sha"}
  - {from: pwi-back-validation, to: pwi-friction-classifier, reads: "exposure and affected per session"}
  - {from: pwi-allow-list-transport, to: pwi-telemetry-reader-verbs, reads: "day-grain aggregates the verbs serve"}
  - {from: pwi-goodhart-register, to: pwi-telemetry-reader-verbs, reads: "every failure_signal series"}
  - {from: pwi-loop-cost-egress-budget, to: pwi-telemetry-reader-verbs, reads: "the ten cost lines through verbs and catalog_stats"}
  - {from: pwi-maturity-ladder-controller, to: pwi-goodhart-register, reads: "the daily soundness verdict (contested, X16)"}
```

Three edges deserve a note. The ladder reads the register verdict only if #1400 q3 is answered "input";
section 4 X16 recommends that answer and the edge is staged on it. The register and the budget read
every component's series but are not upstream of anything except the ladder (and, under F13 (b), the
filer would also read the budget, a same-wave edge in wave 3 that the staged list does not carry; X21), so
they can be built beside the readers rather than after them. The reader-to-capture edge is the one edge that stays inside
a wave: the verbs are built against the writer's tables and CI vectors in parallel with the hooks, and
only the reader's serving-leaf probe (its c3) waits for live rows, which is what its staged depends_on
edge says. Thirteen of the fourteen data edges cross from an earlier wave to a later one (VP 3).

### 2.2 Prerequisites the loop does not own

```yaml
prerequisites:
  - {id: P1, what: "T2.36 writer verb, tables and project registration", owner: "T2.36 / rec-4024", blocks: "every item (all ten reach T2.36 by edge)"}
  - {id: P2, what: "2a-1 write-conformance plan merged (claude_code to parser_version 2)", owner: "Telemetry project", blocks: "deliberation cutover (its fields arrive with 2a-1 itself and rec-4028); friction's producer markers, whose sequencing after 2a-1 is the operator's unanswered call 3 of 2026-10-04 (X20)"}
  - {id: P3, what: "ownership split for hooks (telemetry 3b vs #1384)", owner: "operator", blocks: "wave 1 capture"}
  - {id: P4, what: "ownership split for reader verbs and the ducklake_scd2_schema.py Decision 128 split (telemetry 2b vs #1390)", owner: "operator", blocks: "wave 1 reader"}
  - {id: P5, what: "capture q1 out-of-band session denominator chosen", owner: operator, blocks: "every promotion above read_all (X13); under F1 (a), F4 (a) and F9 (a) also wave 2's read_all period, and by sequence every later wave's, unless the operator funds an envelope of about 60 reviews a day (section 3.4, X22)"}
  - {id: P6, what: "catalog_stats baseline on the live catalog (Decision 88 clause 2)", owner: "operator (reads the live catalog)", blocks: "budget seed envelopes (#1401 q1), not the build"}
  - {id: P7, what: "pilot schema change for return leg, review fraction and version rule (#1398 O1)", owner: "evaluator owner (W0 code)", blocks: "wave 4 ladder rules, not the ladder's read_all operation"}
  - {id: P8, what: "numbered Decision amending Decision 201 for the late verdict keyed to the fix sha (#1397 k1)", owner: "W3 / operator", blocks: "any proof close in wave 4"}
```

P1 to P4 are build gates on the first wave. P5 gates promotion, and under the recommended F1, F4 and F9 it
also gates wave 2's read_all period through the review-load line (section 0, section 3.4). P6 to P8 are
capability gates, not build gates: P6 for the budget's seed envelopes in wave 3, P7 and P8 for the ladder
rules and the proof closes in wave 4; the items build and run at read_all without them. P5 is the one
exception to that definition: it is listed as a capability gate on wave 2 because the items build without
it, but under F1 (a), F4 (a) and F9 (a) their read_all run waits for it, and waves 3 and 4 inherit the
wait by sequence. The build_order block keeps the two kinds apart. None of them is a pilot item: each is a roadmap item, a Telemetry-project plan, an evaluator change or a
Decision, and the pilot carries them as id-only edges or as open questions.

### 2.3 Build order

```yaml
build_order:
  - {wave: 0, items: [], gates: [P1, P2, P3, P4], capability_gates: [], note: "external; the loop builds nothing here"}
  - {wave: 1, items: [pwi-capture-producer-wiring, pwi-telemetry-reader-verbs], gates: [], capability_gates: [], note: "parallel; capture smoke-first (T3.20 c8) before the hooks go broad; reader vectors before the serving leaf"}
  - {wave: 2, items: [pwi-friction-classifier, pwi-deliberation-capture], gates: [P2], capability_gates: [P5], note: "read_all period waits for P5 or added review capacity under F1 (a), F4 (a), F9 (a) (section 3.4); both are reader verbs over wave-1 rows; pairing them is a recommendation that rests on the parked call 3 (X20): friction's producer changes ride after 2a-1, deliberation's fields arrive with 2a-1 itself"}
  - {wave: 3, items: [pwi-goodhart-register, pwi-loop-cost-egress-budget, pwi-rec-filing-dedupe], gates: [], capability_gates: [P6], note: "the register rows and the budget lines go live as soon as wave-2 series exist; the filer is the loop's recommended one writer of recs (Decision 67 boundary; contested at X18, parked as F13, under whose option (a) the budget in this wave would also write recs)"}
  - {wave: 4, items: [pwi-back-validation, pwi-maturity-ladder-controller, pwi-allow-list-transport], gates: [], capability_gates: [P7, P8], note: "proof closes need P8; ladder rules need P7 and the register verdict; the allow-list crosses nothing until its k1-k4 are answered and a consumer exists (#1399 q2)"}
```

Why this order and not another:

- Capture and the reader verbs come first because every other item is a reader verb or reads one, and
  because both are the items whose ownership is parked (P3, P4). Putting them alone in wave 1 means the
  operator's ownership answer changes who builds the wave, not what the later waves are.
- Friction and deliberation are the two readers whose inputs change with 2a-1. Deliberation's fields
  arrive with 2a-1 itself and rec-4028 (#1395 e1), so it has no producer change of its own to bump;
  friction's producer markers land after 2a-1 as one combined version bump only if the operator answers
  the coordinator's third ownership call of 2026-10-04 08:13Z that way (X20, parked). Building the two
  together is the recommendation that keeps that bump single if the call goes that way; the wave
  placement itself does not depend on the answer.
- The register and the budget are observers. They need series to observe (wave 2) but nothing reads
  them except the ladder, so they go live before the filer and stand guard over it: the register's R6
  and the budget's dark_line_days are the two checks that catch a filer running on dark inputs.
- The filer is in wave 3 rather than 4 because it is the first component that writes outside the
  warehouse (recs), and it should run at read_all for as long as possible before anything closes on its
  output. Its filer-rec acceptance probe (#1396 q1) is back-validation's delta verb, which is why
  back-validation follows it rather than the reverse.
- Back-validation, the ladder rules and the cross-plane transport are last because each waits on something
  that is not code: a Decision (P8), a schema change (P7) and four always-ask security forks (#1399 k1-k4).
  All three can be built and run at read_all before those arrive; what they cannot do is close a rec,
  promote a component or send a byte across the plane boundary.

Every component runs at read_all through all four waves (section 3.3). The order therefore does not
decide when anything is trusted; it decides when each component starts producing the review evidence
that the ladder will later consume.

### 2.4 Merge order and the staged pilot edges

The ten PRs each replace the fixture's three empty lists, so the mechanical merge is: merge one, then for
each of the rest merge main into the branch, append the item, criteria and edges, re-render with
model.render and re-run validate_work_item_pilot. The recommended merge order is the build order (wave 1
first), so that each later item's depends_on edge to an earlier item resolves at merge time (the evaluator
accepts an item-to-item edge only when both items are in the fixture, L4). Every staged edge points at an item in the same or an earlier wave
(the ladder's edge to the register points from wave 4 to wave 3), so in wave order no edge ever waits for
a later item; within wave 1 capture merges before the reader so the reader's edge resolves.

The edge list below is what the fixture should carry once all ten items coexist. It obeys
MAX_EDGES_FROM_ITEM 3 by dropping the direct depends_on T2.36 edge from the four items whose part_of edge
plus data upstreams would otherwise exceed it (friction, deliberation, the filer and back-validation; T2.36
is then reached transitively through an upstream, and T2.36 remains the sole external root: VP 3 checks
that every item still reaches it), and by leaving out the filer's direct data edge to the reader, which
the filer reaches through friction. The ladder keeps its three edges as authored. Those drops change four
items as authored and are parked as fork F5; the alternative is an evaluator cap change, which is W0 code.

```yaml
pilot_edges_staged:
  - {from: pwi-capture-producer-wiring, to: T3.20, kind: part_of}
  - {from: pwi-capture-producer-wiring, to: T2.36, kind: depends_on}
  - {from: pwi-telemetry-reader-verbs, to: T2.36, kind: part_of}
  - {from: pwi-telemetry-reader-verbs, to: pwi-capture-producer-wiring, kind: depends_on}
  - {from: pwi-friction-classifier, to: T3.20, kind: part_of}
  - {from: pwi-friction-classifier, to: pwi-capture-producer-wiring, kind: depends_on}
  - {from: pwi-friction-classifier, to: pwi-telemetry-reader-verbs, kind: depends_on}
  - {from: pwi-deliberation-capture, to: T3.20, kind: part_of}
  - {from: pwi-deliberation-capture, to: pwi-capture-producer-wiring, kind: depends_on}
  - {from: pwi-deliberation-capture, to: pwi-telemetry-reader-verbs, kind: depends_on}
  - {from: pwi-rec-filing-dedupe, to: T3.3, kind: part_of}
  - {from: pwi-rec-filing-dedupe, to: pwi-friction-classifier, kind: depends_on}
  - {from: pwi-rec-filing-dedupe, to: pwi-deliberation-capture, kind: depends_on}
  - {from: pwi-back-validation, to: T3.4, kind: part_of}
  - {from: pwi-back-validation, to: pwi-rec-filing-dedupe, kind: depends_on}
  - {from: pwi-back-validation, to: pwi-friction-classifier, kind: depends_on}
  - {from: pwi-maturity-ladder-controller, to: T3.4, kind: part_of}
  - {from: pwi-maturity-ladder-controller, to: T2.36, kind: depends_on}
  - {from: pwi-maturity-ladder-controller, to: pwi-goodhart-register, kind: depends_on}
  - {from: pwi-allow-list-transport, to: T2.36, kind: depends_on}
  - {from: pwi-allow-list-transport, to: pwi-telemetry-reader-verbs, kind: depends_on}
  - {from: pwi-goodhart-register, to: T2.36, kind: depends_on}
  - {from: pwi-goodhart-register, to: pwi-telemetry-reader-verbs, kind: depends_on}
  - {from: pwi-loop-cost-egress-budget, to: T2.36, kind: depends_on}
  - {from: pwi-loop-cost-egress-budget, to: pwi-telemetry-reader-verbs, kind: depends_on}
```

Simulated merge (VP 4): the ten items, thirty criteria and these twenty-five edges render to 405
effective lines against the 420 ceiling, every item carries at most three outgoing edges, every edge
target resolves (pilot item, T2.36, T3.3, T3.4 or T3.20) and legs 1, 2, 4 and 5 of the evaluator return
no error. The fixture therefore has room for two more items, which this report does not spend: T2.19 (cost
reconciliation) and T2.26 (reader cost model) are read beside the budget item as roadmap ids, not lifted
into the pilot, which is what #1401 asked W2 to decide. The register and the ladder likewise stay
single items; one row per sibling would need eight more items and the cap says no.

### 2.5 Roadmap items read beside the loop

T2.52 (aggregate verbs, deferred post-MVP) is the alternative home of sessions_window (#1390 k2; X3).
T3.20 c3 (the DQ monitor, alarm-not-gate) and c8 (smoke-first) are the two T3.20 criteria the capture and
deliberation items name; neither is closed by anything here. T3.3's false-positive threshold (F-035,
flagged unadjudicable at decomposition) has a candidate formula in the filer's rejected_share (section 6).
T3.4's A0-A3 executor autonomy gates are read as a different ladder from the review ladder here (#1398 q1;
section 3.5), sharing a controller at most.

## 3. One cross-component maturity ladder

### 3.1 The ten maturity blocks as written

Read from the ten maturity blocks at the pinned heads (VP 2). first_n is the first-rung count, second_window
the second-rung sample window, second_comparator how it is read, third_days the top-rung day count or
"rate" where the top rung reads a rate instead.

```yaml
dialects:
  - {item: pwi-capture-producer-wiring, first_n: 20, second_window: 30, second_comparator: '==', third_days: 30}
  - {item: pwi-telemetry-reader-verbs, first_n: 50, second_window: 200, second_comparator: '==', third_days: 30}
  - {item: pwi-friction-classifier, first_n: 20, second_window: 200, second_comparator: '==', third_days: 30}
  - {item: pwi-deliberation-capture, first_n: 20, second_window: 200, second_comparator: '==', third_days: 30}
  - {item: pwi-rec-filing-dedupe, first_n: 20, second_window: 50, second_comparator: '==', third_days: 30}
  - {item: pwi-back-validation, first_n: 20, second_window: 50, second_comparator: '==', third_days: rate}
  - {item: pwi-maturity-ladder-controller, first_n: 40, second_window: 40, second_comparator: '>=', third_days: 30}
  - {item: pwi-allow-list-transport, first_n: 30, second_window: 60, second_comparator: '>=', third_days: 90}
  - {item: pwi-goodhart-register, first_n: 40, second_window: 40, second_comparator: '>=', third_days: 30}
  - {item: pwi-loop-cost-egress-budget, first_n: 30, second_window: 30, second_comparator: '>=', third_days: 30}
```

Four distinct first-rung counts (20, 30, 40, 50), five distinct second-rung windows (30, 40, 50, 60, 200),
two second-rung readings (six items read zero defects across the last M sampled; four read M confirmed
since the last overturned one), and two top-rung day counts (30 and 90) plus one rate. Taken as whole
tuples (first count, second window, second reading, top-rung value), the ten blocks fall into eight
distinct dialects: friction equals deliberation and the ladder equals the register (VP 2). The six items
authored before #1398 use the "zero defects in a window" reading; the four authored after it use its
"since the last overturned" reading. Neither is wrong; they are the same acceptance-sampling idea
written twice, and the second form has the property the first lacks (one overturned output restarts the
count).

### 3.2 The one rule family

Staged, not ratified. The family is #1398's continuous-sampling design with #1400's soundness
precondition added at every rung and the demotion legs split by what the evidence is.

```yaml
ladder_family:
  unit: "one reviewable output of the component (section 3.4 names it per component)"
  rungs: [read_all, sampled, spot_check, anomaly_triggered]
  promotion:
    read_all_to_sampled: "N consecutive outputs the operator confirmed unchanged since the last overturned one, with the register verdict ok for the component on that day"
    sampled_to_spot_check: "at fraction f1, M consecutive sampled outputs confirmed unchanged since the last overturned one, verdict ok"
    spot_check_to_anomaly_triggered: "at fraction f2, D consecutive days with the component's own failure_signal within its bound and the verdict ok on every one of them"
  ceiling: "effective rung = min(earned rung, min rung of its data upstreams (section 2.1)); the earned rung is retained while capped and the effective rung snaps back when the upstream recovers (F1 (d), recommended); no upstream, no ceiling"
  lane: "rung is kept per component, or per producer lane where the item's own triggers are per producer (deliberation); a lane restarts on the item's declared restart events as well as on a version bump (deliberation: a new producer, a change in its visibility mix)"
  demotion:
    overturned_output: "back to read_all (evidence of a wrong output; CSP-1 reset)"
    signal_breach: "one rung down when the component's own failure_signal breaches its bound while it sits above read_all (#1398 k7 (a)); hold there until the bound holds again for D consecutive days"
    verdict_not_ok: "one rung down, then hold, with an output-count input so that no outputs is told apart from monitor dark (#1398 k7 (d)) and a dead-man alert while it holds (absence of evidence: dark, blind, undrilled, counter_dark, counter_low, unregistered)"
    ceiling_breach: "down to the new ceiling the same day the upstream moves"
    version_change: "restart at read_all on an integer version stamp bump (classifier_version, parser_version, register version, allow-list classification version)"
  authority: "the operator approves every promotion; every demotion is automatic and logged"
  drills: "owned by the register (one drill log, one verdict); the controller consumes verdict ok and has no drill clause of its own"
  faults: "every daily verdict record (the register's and the budget's) carries a faults list naming each leg that fired that day (register: dark, blind, undrilled, counter_dark, counter_low, unregistered, diverging, upstream_unsound, malformed; budget: the per-line labels warn, projected_breach, breach, dark, unattributed and unregistered, with dark_flag and unattributed_flag beside them, as #1401 labels its lines; the invoice residual is the monthly reconciliation leg and stays out of the daily list), so the controller's verdict_not_ok leg and the operator read the reasons, not only the verdict (the 2026-10-03 consult asked it of the register, the 2026-10-04 consult of the budget and of this synthesis to converge the two; section 3.6)"
```

What this changes against the items as written:

- Every first and second rung becomes "confirmed unchanged since the last overturned one" (the #1398
  form). The six items using "zero defects across the last M" keep their M as the window.
- Every rung gains the verdict-ok precondition. Today only the register's item trigger names a drill;
  #1398's controller adds one at every top rung (hold:awaiting_drill, k5 (a)); the other nine item
  triggers read their own signal, which is exactly the series the register exists to distrust. Putting the
  precondition at every rung is the strongest of three positions the inputs take (#1384 ties q1 to its
  top rung only, #1398 drills at the top rung only, #1400 demands soundness at every rung) and is what
  produces the whole-loop read_all pin; the top-rung-only alternative is named under F9 and X22, not
  chosen.
- The ceiling is costed. One overturned capture output resets capture to read_all (CSP-1), and the
  ceiling then drops all nine downstream components to read_all the same day; separately, any upstream
  non-ok verdict propagates as upstream_unsound (#1400) and fires verdict_not_ok downstream. If a capped
  component had to re-earn its rung under #1398's per-stint counts with fresh operator approval, F1's
  true cost would be a loop-wide return to full review on any upstream defect or parser_version bump.
  The family therefore tracks an earned rung and an effective rung (common practice for dependency-aware
  trust): the earned rung is retained while capped and the effective rung snaps back when the upstream
  recovers. That is F1 (d), recommended beside F1 (a), and parked with it.
- Deliberation keeps its lane. Its triggers are per producer and per parser_version, and its report names
  three restart events (a new producer, a parser_version bump, a change in the visibility mix); the family
  carries the first and third as lane restarts and the version bump as the general rule. Its edge to capture
  binds only the claude_code lane; the LiteLLM lane arrives with rec-4028 and T4.2 and is moot while the
  executor is frozen (Decision 67).
- The demotion legs are split by evidence. #1398 k1 (return to read_all on an overturned output) and
  #1398 k7 ((a) one rung down on a breach of the component's own failure_signal and one rung down when
  it goes dark; (d) treat dark as a hold, or stage an output-count input so that "no outputs" is told
  apart from "monitor dark", which #1398 recommends for dark) were read together with the Fable consult
  of 2026-10-03 (reset for wrong outputs; demote once and hold with a dead-man alert for dark signals).
  The family keeps all three because they answer different evidence: a wrong output is proof the rung is
  too high; a breaching signal is the component's own alarm and demotes one rung (k7 (a)'s breach half,
  kept as signal_breach); a dark signal is proof of nothing except that nobody is looking and demotes one
  rung, then holds (k7 (a)'s dark half, with the consult's dead-man alert). The output-count input of
  k7 (d) is part of the staged family: without it the dark leg demotes a quiet weekend, which #1398
  flags as unmeasured. The breach leg lives here in the controller, not in the register verdict (X8).
- The ceiling rule is new. No item states it, and without it the dialects allow a reader at sampled over a
  producer at read_all, which means one in five of the reader's outputs are reviewed while every row it
  read is still suspect. Fork F1.

### 3.3 Per-component seeds and the ceiling today

The seeds are the items' own values carried into the family (with deliberation's per-producer lane kept,
section 3.2); none is re-derived here and all stay
provisional (every item marks them so or lists them as an open question). f1 and f2 are #1398's 0.2 and
0.04 everywhere; its anomaly_triggered floor of 0.008 is the one the consult asked to raise and is parked
under #1398 q3. Two rows are not pure re-expressions and say so: back-validation's top rung is a 90-day
window read of a rate, not 90 consecutive days, so its D is the window and its bound the rate; the
register's bound is its own failure_signal (unsound_reads 0), while its item's top rung reads the daily
drill, which the family folds into the verdict-ok precondition.

```yaml
ladder_seeds:
  - {item: pwi-capture-producer-wiring, N: 20, M: 30, D: 30, bound: "unfinalized_session_share <= 0.05", upstreams: [], ceiling_today: "read_all (counter_dark until P5)"}
  - {item: pwi-telemetry-reader-verbs, N: 50, M: 200, D: 30, bound: "zero mismatches and zero conflicted grain keys", upstreams: [pwi-capture-producer-wiring], ceiling_today: "read_all"}
  - {item: pwi-friction-classifier, N: 20, M: 200, D: 30, bound: "unmapped_failure_share <= 0.05", upstreams: [pwi-capture-producer-wiring, pwi-telemetry-reader-verbs], ceiling_today: "read_all"}
  - {item: pwi-deliberation-capture, N: 20, M: 200, D: 30, bound: "deliberation_drift_share <= 0.01", upstreams: [pwi-capture-producer-wiring, pwi-telemetry-reader-verbs], ceiling_today: "read_all"}
  - {item: pwi-rec-filing-dedupe, N: 20, M: 50, D: 30, bound: "rejected_share <= 0.05 and no duplicate open fingerprint", upstreams: [pwi-telemetry-reader-verbs, pwi-friction-classifier, pwi-deliberation-capture], ceiling_today: "read_all"}
  - {item: pwi-back-validation, N: 20, M: 50, D: 90, bound: "false_proof_rate <= 0.05 over the trailing 90 days with at least 20 proof closes (a window read, not consecutive days)", upstreams: [pwi-rec-filing-dedupe, pwi-friction-classifier], ceiling_today: "read_all"}
  - {item: pwi-maturity-ladder-controller, N: 40, M: 40, D: 30, bound: "stale_rung_days 0 and est_escaped_share <= AOQL target", upstreams: [pwi-goodhart-register], ceiling_today: "read_all"}
  - {item: pwi-allow-list-transport, N: 30, M: 60, D: 90, bound: "canary_leaks 0 and no unclassified column", upstreams: [pwi-telemetry-reader-verbs], ceiling_today: "read_all"}
  - {item: pwi-goodhart-register, N: 40, M: 40, D: 30, bound: "unsound_reads 0 (its item's top rung reads the dated drill series instead; folded into verdict ok)", upstreams: [pwi-telemetry-reader-verbs], ceiling_today: "read_all"}
  - {item: pwi-loop-cost-egress-budget, N: 30, M: 30, D: 30, bound: "no dark_flag or unattributed_flag and invoice residual in tolerance", upstreams: [pwi-telemetry-reader-verbs], ceiling_today: "read_all"}
```

The ceiling_today column is the finding of section 0: with capture reading counter_dark until the
denominator of P5 exists, and with the ceiling rule, every component's ceiling is read_all. That is the
intended behaviour of the family, not a defect in it. The alternative readings are in X13.

### 3.4 Review unit and review load

The family counts outputs, so each component needs one named unit. Read from the items' first-rung
metrics: capture, a finalized session's conformance check; reader, a session's re-derived verbs; friction,
a labelled session; deliberation, a reviewed session per producer; filer, a proposal; back-validation, a
verdict; ladder, a controller decision; allow-list, an egress batch diff; register, a daily verdict; budget,
a daily verdict run. #1398 R1 and #1401 e15 count 12 reviews a day for each of the six items authored
before #1398, 72 a day in all at read_all, against #1401's seed of 30 a day (900 a month) with a warning
line at 0.8 of it (24 a day); the four later items add their own daily verdicts and batch diffs on top.
By wave: wave 1 alone (capture and reader) is about 24 a day and sits on the warning line; wave 2 adds
friction and deliberation for about 48 a day, 1.6 times the envelope; waves 3 and 4 add the rest. Under
F4 (a) a wave starts its read_all period only when the line has headroom, and headroom comes only from a
promotion or from added capacity. With the verdict-ok precondition at every rung and the ceiling (F1 (a),
F9 (a)) nothing promotes before P5, so wave 2's read_all period waits for P5 or for the operator to fund
the capacity: headroom under F4 (a) means room below the warning line at 0.8 of the envelope, so for
wave 2's 48 a day to sit under it the envelope would be about 60 reviews a day (1,800 a month), twice the
seed. That is the joint consequence section 0 states (X22).

Staged resolution (X5, fork F4): the budget item owns the review-load line and its alarm, because it is a
cost line like the other nine and the budget is where cost lines are read; the ladder owns the schedule,
which at read_all has exactly one lever, how many components are at read_all at once. The build order is
that lever: a wave starts its read_all period only when the review-load line has headroom, and the
operator decides whether to add capacity or hold the wave. Neither item changes its text; the budget's
q3 and the ladder's R1 are answered by one sentence in the staged Decision (section 6).

### 3.5 What the ladder is not

It is not T3.4's A0-A3 executor autonomy gates (#1398 q1). Those gate what the executor may do; this
ladder gates how much of each loop component's output a human reads. Both can share a controller and a
transition log, and the report recommends that they do (one log, two ladders), but the rungs, the seeds
and the authority rule above are the review ladder's only.

### 3.6 The faults list

The 2026-10-03 consult asked the register (#1400) to carry, on every daily verdict record, the list of
every leg that fired that day; the 2026-10-04 consult asked the same of the budget (#1401) and asked this
synthesis to converge the two. The family's faults key stages it: one list per verdict record, in the loop
journal (F8), with the register's leg names (dark, blind, undrilled, counter_dark, counter_low,
unregistered, diverging, upstream_unsound, malformed) and the budget's per-line labels (warn,
projected_breach, breach, dark, unattributed, unregistered, with dark_flag and unattributed_flag beside
them) as the vocabulary; the invoice residual is the budget's monthly leg and is not a daily fault. The controller's verdict_not_ok leg reads
the list to tell "dark" from "diverging" when it decides whether to hold or demote, and the operator
reads it instead of re-deriving the verdict. It is staged text under the journal Decision in section 6.

## 4. Contradictions between components

Settled rows are points on which all ten items agree with each other and with a Decision, a contract or a
relayed answer; nothing in a settled row is new. Contested rows are points where two or more items, or an
item and a Decision, say different things; each carries a staged resolution and a park reference (a W2
fork from section 5, or the W1 fork that already holds it). VP 5 checks the ids, statuses, item names and
park references of this block.

```yaml
contradictions:
  - {id: S1, status: settled, items: all, point: "planes are data_plane only (Decision 209 clause 2a) except the allow-list item, which is the one component that names both planes by design"}
  - {id: S2, status: settled, items: all, point: "every item reaches T2.36 by edge, so nothing builds before the writer verb (P1)"}
  - {id: S3, status: settled, items: all, point: "every rollback is additive, removes a hook, a schedule or a config block, and deletes no row"}
  - {id: S4, status: settled, items: all, point: "every failure_signal is lower-is-better and its own metric (register VP 1 at fc298254, 8 of 8 then; the two later items follow the same form)"}
  - {id: S5, status: settled, items: [pwi-deliberation-capture], point: "the visibility cutover binds to 2a-1 (q1 answered; location folded at 4f49c55f)"}
  - {id: X1, status: contested, items: [pwi-friction-classifier, pwi-capture-producer-wiring], point: "gate and pre-commit signatures producer-side (#1394 k3 (a)) vs read-side regex on tool_result (#1384 P1)", resolution: "producer-side, narrowed to structured markers, riding the single bump if call 3 goes that way (X20) (consult of 2026-10-03)", park: '#1394 k3'}
  - {id: X2, status: contested, items: [pwi-capture-producer-wiring, pwi-deliberation-capture], point: "capture sends byte-chunked per-table batches data-first; 2a-2 wants one transaction per request with sessions last", resolution: "the writer owner (T2.36) decides the request shape and capture adapts; Decision 207 no-op resends make either order safe to replay", park: F12}
  - {id: X3, status: contested, items: [pwi-telemetry-reader-verbs, pwi-rec-filing-dedupe], point: "sessions_window built in the reader (#1390 k2 (a)) vs in T2.52 (b); the filer and back-validation both read it", resolution: "(a), with one Decision 199 clause 1 reading stated once wherever it is built", park: '#1390 k2'}
  - {id: X4, status: contested, items: [pwi-maturity-ladder-controller, pwi-goodhart-register], point: "the ladder drills at its top rung only (20 in 20 days at 0.9); the register demands a drill at every rung (drill_min 20 per 28 days)", resolution: "the register owns drills and the verdict; the controller drops its own drill clause and reads verdict ok at every rung (section 3.2)", park: F3}
  - {id: X5, status: contested, items: [pwi-maturity-ladder-controller, pwi-loop-cost-egress-budget], point: "review load as the ladder's control (#1398 R1) vs a budget line (#1401 q3); 72 a day modelled against 30 a day seeded", resolution: "budget owns the line and alarm, ladder owns the schedule, waves are the lever (section 3.4)", park: F4}
  - {id: X6, status: contested, items: all, point: "eight trigger dialects across ten maturity blocks (section 3.1)", resolution: "one rule family with per-component seeds (section 3.2)", park: F2}
  - {id: X7, status: contested, items: [pwi-goodhart-register, pwi-maturity-ladder-controller], point: "a not-ok verdict blocks and demotes (#1400 k1 (a)) vs the controller's own demotion leg, one rung down on an own-signal breach and on dark (#1398 k7 (a)), with k7 (d)'s output-count input recommended for dark", resolution: "split by evidence; wrong output resets to read_all, an own-signal breach demotes one rung (k7 (a) kept as signal_breach), absent evidence demotes one rung and holds with the output-count input and a dead-man alert (section 3.2)", park: F2}
  - {id: X8, status: contested, items: all, point: "the register's breach leg (#1400 k1 (v)) borrows every item's top-rung threshold, so one capture reading of 0.06 makes 7 of 8 detectors unsound", resolution: "no breach leg in the register; it reads soundness only, each item's own top rung reads its bound for promotion, and the controller's signal_breach leg (section 3.2) demotes on a breach", park: '#1400 k1 (v)'}
  - {id: X9, status: contested, items: [pwi-maturity-ladder-controller, pwi-rec-filing-dedupe, pwi-back-validation], point: "the filer and back-validation defer their version rule to the ladder (#1398 k2), and the pilot schema cannot express a return leg, a review fraction or a version rule (#1398 O1)", resolution: "P7 schema change before any rule is enforced; until then the family in section 3.2 is prose", park: '#1398 k2'}
  - {id: X10, status: contested, items: [pwi-back-validation, pwi-rec-filing-dedupe], point: "who closes a rec; a late verdict keyed to the fix sha amends Decision 201 (#1397 k1), close_proposed at the deadline (#1397 k2) and the filer's self-close (#1396 k4) are three closers", resolution: "one numbered amending Decision (P8) that names back-validation as the only proposer and keeps every close a Decision 201 verdict-layer event; the filer self-closes nothing", park: '#1397 k1'}
  - {id: X11, status: contested, items: [pwi-rec-filing-dedupe, pwi-back-validation, pwi-maturity-ladder-controller, pwi-allow-list-transport, pwi-goodhart-register, pwi-loop-cost-egress-budget], point: "six items ask where their run, verdict, transition, canary or budget record lives (#1396 q4, #1397 q4, #1398 q2, #1399 q3, #1400 q1, #1401 k2)", resolution: "one append-only loop journal in the data plane on the Decision 199 model, one table per record kind, no new contract class", park: F8}
  - {id: X12, status: contested, items: [pwi-allow-list-transport, pwi-goodhart-register, pwi-loop-cost-egress-budget, pwi-maturity-ladder-controller], point: "three items carry depends_on T2.36 only and ask for a part_of home (#1399 k6, #1400 k5, #1401 k6); the ladder already carries part_of T3.4 at f6183430 with its home still parked (#1398 k6)", resolution: "the three stay depends_on only until W3 and are never lifted as-is (fixture header); the ladder's T3.4 home is re-read with #1398 q1, since section 3.5 reads this ladder as separate from T3.4's A0-A3 gates", park: F6}
  - {id: X13, status: contested, items: [pwi-capture-producer-wiring, pwi-goodhart-register, pwi-maturity-ladder-controller], point: "until capture q1 has a denominator the register reads capture counter_dark and everything downstream upstream_unsound, so under the ceiling rule nothing promotes", resolution: "accept it as the honest state and make P5 the first operator decision; the SessionStart hook's own invocation count is the staged candidate denominator", park: F9}
  - {id: X14, status: contested, items: [pwi-friction-classifier, pwi-deliberation-capture, pwi-rec-filing-dedupe, pwi-back-validation], point: "MAX_EDGES_FROM_ITEM 3 against a part_of edge, a T2.36 edge and up to three data upstreams", resolution: "drop the direct T2.36 edge where a pilot upstream carries it, and the filer's direct edge to the reader (reached through friction); four items change (section 2.4)", park: F5}
  - {id: X15, status: contested, items: [pwi-capture-producer-wiring, pwi-telemetry-reader-verbs], point: "ownership of hooks and reader verbs between this stream and the Telemetry project (3b, 2b)", resolution: "none here; wave 1 is built by whoever the operator names, and the later waves do not change", park: "operator (P3, P4)"}
  - {id: X16, status: contested, items: [pwi-goodhart-register, pwi-maturity-ladder-controller], point: "the verdict as one input to the controller vs read beside it (#1400 q3)", resolution: "input; it is the only way the verdict-ok precondition of section 3.2 is enforced rather than advised, and it is the ladder's one staged depends_on edge to a pilot item", park: F7}
  - {id: X17, status: contested, items: [pwi-friction-classifier], point: "the friction formula reads tool_call close outcomes with no severity, so hook passes count; the producer owners' 2026-10-04 confirmation firms k2 (a), but either choice sets the operator's 2026-09-24 ruling against the slice-3a deviation (a ratified Class A semantic change)", resolution: "none here; (a) ratify the producer's outcome store, as the producer owners recommend, stays parked for the operator", park: '#1394 k2'}
  - {id: X18, status: contested, items: [pwi-rec-filing-dedupe, pwi-loop-cost-egress-budget, pwi-back-validation], point: "who writes recs: the filer files (#1396); the budget's recommended breach action (#1401 k1 (a)) files one rec per breaching line through the cost reconciliation path; the filer's self-close (#1396 k4) would be a third writer", resolution: "the filer is the loop's one rec writer; the budget's breach rec routes through the filer as a detector source so dedupe and the Decision 67 boundary apply once (an amendment to #1401 k1 (a), staged as F13 (b)); no component self-closes (X10)", park: F13}
  - {id: X20, status: contested, items: [pwi-friction-classifier, pwi-capture-producer-wiring, pwi-deliberation-capture], point: "whether friction's and capture's producer changes land after the 2a-1 plan as one combined version bump is the coordinator's third ownership call to the operator of 2026-10-04 08:13Z, unanswered; #1394 carries it only as the producer owners' sequencing recommendation, and deliberation has no producer change of its own (its fields arrive with 2a-1 and rec-4028)", resolution: "none here; the wave-2 pairing and P2 are written as a recommendation resting on that call, and the wave placement holds either way", park: "operator (2026-10-04 call 3, beside P3 and P4)"}
  - {id: X21, status: contested, items: [pwi-rec-filing-dedupe, pwi-loop-cost-egress-budget], point: "F5 (a) and F13 (b), both recommended, cannot hold together: routing the budget's breach through the filer adds a filer-to-budget data edge, the filer then carries four staged edges (T3.3, friction, deliberation, budget) and leg 1 rejects it (VP 4 variant); neither the budget nor deliberation is reachable from friction, so F5's transitive drop cannot absorb it", resolution: "none; F5 (a) and F13 (b) are jointly unsatisfiable without a further fork (F14), which names the exits and never chooses the cap change", park: F14}
  - {id: X22, status: contested, items: [pwi-capture-producer-wiring, pwi-maturity-ladder-controller, pwi-goodhart-register], point: "which rungs need monitor soundness: #1384 ties its q1 denominator to its top rung only, #1398 k5 drills at the top rung only, #1400 demands soundness at every rung; the family stages every rung, which moves capture's q1 dependency from its top rung to its first and, with F1 (a) and F4 (a), makes P5 gate wave 2's read_all period (section 3.4)", resolution: "none; every-rung is staged as the strongest reading and the top-rung-only alternative is named as F9 (d), not chosen", park: F9}
  - {id: X19, status: contested, items: [pwi-goodhart-register, pwi-loop-cost-egress-budget], point: "the register at fc298254 has eight detector rows and none for the budget or for the register itself, so under the family's verdict-ok precondition both read unregistered and can never promote", resolution: "add the two rows at the register's build (the budget's line is the one #1401 says belongs there; the register's own row reads its dated drill series), so every component the ladder moves has a row", park: F11}
```

Notes on the rows that are not self-explanatory:

- X1 sits inside #1384, which is not final; if its gating changes P1 the resolution stands (it constrains
  the producer side) but the park reference moves. X2 reconciles #1384's batch shape with the 2a-2 shape
  that reached the stream through #1395; no W1 fork holds it (#1384 k1 is the sync-versus-detached hook
  question), so it is this report's F12.
- X17 and X18 were settled rows in the first draft. X17 rests on a producer team's confirmation, which is
  not an operator answer to #1394 k2; X18 was contradicted by the budget's own recommended breach action.
- X8 is already resolved inside #1400 (its own recommendation is to drop the leg); it is listed here
  because it is the one contradiction that reaches every other item, and because the staged family in
  section 3.2 depends on it being dropped: with the leg, the verdict would double-count every item's own
  top-rung bound.
- X10 names three closers. The filer's self-close (#1396 k4) was recommended against in its own report;
  listing it keeps the staged Decision honest about what it forbids.
- X11 is the largest single saving for W3: six open questions become one Decision. The alternative, one
  table per item chosen by each owner, is what the pilot would get by default and is the state the six
  questions describe.

## 5. Parked forks (this report's own)

Every fork below weighs alternatives against unmerged PRs or unanswered forks, so each is class asked and
parked for the operator; none is decided here or by the Step 6b stand-in. Each has had the industry-practice
reading the operator asked for on 2026-10-03 applied in its recommendation. The same list is written to
/mnt/project-files/gates/w2-parked-forks.md for the hand-back.

- F1 (asked) Rung ceiling by dependency. (a) rung(component) <= min rung of its data upstreams; (b)
  <= min upstream rung + 1; (c) no coupling; (d) (a) with an earned rung retained under the cap and an
  effective rung that snaps back when the upstream recovers (section 3.2). Recommendation (a) with (d): a
  reader's sample is only as good as the rows it read, (b) lets a sampled reader sit on a read_all
  producer, which is the case the ceiling exists to forbid, and without (d) one upstream defect or
  parser_version bump returns the whole loop to full review. Cost of (a): the X13 state, every component
  at read_all until P5, and with F4 (a) and F9 (a) the wave-2 gate of X22.
- F2 (asked) One rule family and the demotion split (section 3.2). (a) the family as staged; (b) keep
  the ten dialects and only add the verdict precondition; (c) adopt #1398's rule verbatim with its single
  demotion leg. Recommendation (a). Precedent: none (#1398 and #1400 are unmerged; the consult of
  2026-10-03 is advice, not a Decision).
- F3 (asked) Drill home. (a) the register owns every drill and the controller reads verdict ok; (b) the
  controller keeps its top-rung drill and the register drills the rest; (c) both drill. Recommendation
  (a): two drill logs with two seeds for the same detector is the double-count X8 removes elsewhere.
- F4 (asked) Review-load owner. (a) budget owns the line and alarm, ladder owns the schedule, waves are
  the lever; (b) the ladder owns it as its control variable (#1398 R1); (c) the budget owns it and may
  demote (#1401 k1 (b) at lift). Recommendation (a): (c) gives a cost line authority over a trust level,
  which is a Goodhart path of its own.
- F5 (asked) Staged edge list under the cap. (a) drop direct depends_on T2.36 where a pilot upstream
  carries it, and the filer's direct edge to the reader (four items change: friction, deliberation, the
  filer, back-validation); (b) raise MAX_EDGES_FROM_ITEM (W0 evaluator code, a frozen cap);
  (c) keep T2.36 and drop the second data upstream. Recommendation (a): T2.36 stays the single external
  root (VP 3 proves reachability) and the cap stays frozen.
- F6 (asked) Edge homes for the three depends_on-only items (allow-list, register, budget). (a) all three
  stay depends_on T2.36 only until W3; (b) all three part_of T3.4 beside the ladder; (c) register and
  budget part_of T3.3 (the false-positive threshold owner), allow-list part_of T4.24. Recommendation (a):
  no roadmap criterion names any of the three, so a part_of now would be a guess stamped into a fixture.
  The ladder's own part_of T3.4 (carried at f6183430, home parked under #1398 k6) is re-read with
  #1398 q1, since section 3.5 reads this ladder as separate from T3.4's A0-A3 gates.
- F7 (asked) Verdict as controller input. (a) input, with the staged depends_on edge; (b) beside, as an
  operator read. Recommendation (a), as X16 says.
- F8 (asked) Record home. (a) one append-only loop journal in the data plane on the Decision 199 model,
  one table per record kind; (b) one table per item, each owner chooses; (c) a new Class C contract.
  Recommendation (a): #1396 q4 already recommended the journal form, and five other items ask the same
  question. Precedent: none (Decision 199 governs telemetry events, not loop records; applying it is an
  analogy until W3 writes it down).
- F9 (asked) The read_all pin until P5. (a) accept; nothing promotes until capture q1 is answered, and
  under F1 (a) and F4 (a) wave 2's read_all period waits for P5 or for an envelope of about 60 reviews a day
  (so that wave 2's 48 sit under the 0.8 warning line);
  (b) exempt capture's counter_dark from upstream propagation while every component is at read_all
  anyway; (c) seed the denominator from the SessionStart hook's own invocation count and let promotion
  follow, noting what #1384 q1 already records: an open row at the SessionStart pass is a record_turn rule
  change against the generation-marker rule (Decision 207 R3) and belongs to the operator, and a count
  from the same runner is not out-of-band for a runner that never succeeds; (d) place the verdict-ok
  precondition at the top rung only, where #1384 put its q1 dependency and #1398 k5 its drill, so
  components reach sampled before P5 and the review line gains the headroom F4 (a) needs (X22).
  Recommendation (a) now, with (c) as the staged candidate answer to q1 for the operator; (d) is named
  so the operator sees the trade and is not chosen; (b) hides the only forcing function the design has
  for q1.
- F10 (asked) Merge order equals build order. (a) merge the ten PRs in wave order so item edges resolve at
  merge time; (b) merge in PR number order and author item edges in one later PR; (c) merge in any order
  with all item edges authored by W3. Recommendation (a). Precedent: none (all ten are unmerged).
- F11 (asked) Register rows for the two observers (X19). (a) the register's build adds a row for the budget
  and one for itself; (b) exempt the observers from the verdict-ok precondition; (c) the observers never
  leave read_all. Recommendation (a): (b) makes the two components that watch the loop the only ones
  nobody watches, and (c) is (a) delayed. The register's own row is not self-certification: its drill is
  an injected known-answer fault (a dated drill series that must read unregistered), which is the
  report's own principle that a monitor's output is never the only evidence that the monitor works.
- F12 (asked) Batch shape between capture and the writer (X2). (a) the T2.36 writer owner fixes the request
  shape and capture adapts; (b) capture's per-table data-first batches are fixed in slice 3b and the
  writer accepts them; (c) 2a-2's one transaction per request with sessions last binds capture.
  Recommendation (a): Decision 207 no-op resends make either order safe to replay, so the owner of the
  boundary owns the shape; the answer rides the P3 ownership split.
- F13 (asked) Route of the budget's breach recommendation (X18). (a) #1401 k1 (a) as written, one rec
  per breaching line through the cost reconciliation's rec path; (b) the same breach routed through the
  filer as a detector source, so dedupe and the Decision 67 boundary apply once (an amendment to k1 (a));
  (c) #1401 k1 (b), the owning component degrades its cadence and no rec is filed. Recommendation: (b)
  only together with an answer to F14, otherwise (a), which the consult of 2026-10-04 preferred. The case
  for (b): two rec writers in one loop is the duplicate-filing failure the filer exists to prevent, and (c)
  gives a cost line authority over a component's cadence (the F4 objection). Interaction (X21): (b) makes
  the budget a filer source, which by this report's convention is a data edge, so the filer would carry
  four staged edges and the evaluator rejects the merge (VP 4 variant). F5 (a) and F13 (b) are therefore
  jointly unsatisfiable without F14; (a) and (c) add no edge. F13 and F14 are one question for the
  operator: approving this report's recommendations en bloc would otherwise approve F5 (a) and F13 (b)
  together, which the report itself shows cannot both hold.
- F14 (asked) The filer's fourth edge under F13 (b) (X21). (a) choose F13 (a) or (c), so no edge is added;
  (b) carry the budget as a filer source in the register's upstream list and the ladder seeds only, not as
  a pilot edge (hides a real dependency from the fixture, though the ceiling rule reads the seeds);
  (c) drop the filer's part_of T3.3 home to make room; (d) raise MAX_EDGES_FROM_ITEM (W0 evaluator code, a
  frozen cap; never chosen here). No recommendation: (a) reopens F13, (b) and (c) each lose something the
  fixture states today, and (d) is W0 code. The operator answers F13 and F14 together.

Forks held by W1 items and not re-opened here: every k row of the ten items stays parked where it is; the
resolutions in section 4 cite them but do not decide them.

## 6. Staged text for W3 (not filed)

Candidate Decision and criterion text, staged only. Nothing below is in docs/DECISIONS.md, the roadmap
or a rec; W3 files what the operator approves.

- Candidate Decision: loop review ladder. One rule family (section 3.2) over four rungs for every
  telemetry feedback loop component; promotion by operator approval, demotion automatic; a component's
  rung is capped by the lowest rung among its data upstreams; the Goodhart register's daily verdict is a
  promotion precondition and a demotion input at every rung; the register owns drills; a component's
  version stamp bump restarts it at read_all; the budget owns the review-load line and the ladder owns
  the schedule. Amends nothing; the pilot schema change P7 lands with it.
- Candidate Decision: loop record journal (X11). One append-only journal in the data plane, Decision 199
  model, one table per record kind (filer run, verdict, transition, canary and egress, budget line); no
  new contract class; every loop component reads and writes its own kind only; every daily verdict record
  carries a faults list naming each leg that fired (section 3.6).
- Amending Decision for Decision 201 (P8, from #1397 k1 and X10): a late acceptance verdict keyed to the
  fix sha is a verdict-layer event; back-validation is its only proposer; close_proposed with reason and
  counts at the deadline until the component reaches anomaly_triggered; no component closes a rec on its
  own output.
- T3.3 false-positive threshold (F-035): candidate formula rejected_share per 30 days per detector as
  the filer defines it, living in the loop journal; the number stays the operator's.
- T2.52 and Decision 199 clause 1 (X3): one stated reading of the partition bound for sessions_window
  wherever it is built; the reader's (a) is recommended.

## 7. Boundary

This report does not merge, re-render or edit the fixture: the staged edge list is applied by the merging
thread of each wave, in wave order, and the simulated merge (VP 4) is run from the pinned heads, not from
main. It does not re-seed any threshold, decide any W1 fork, file or read a rec, touch the roadmap, a
contract, IAM or Terraform, or choose an owner for P3 and P4. It spends none of the two remaining fixture
items. Reading #1384 and #1390 as not final, every sentence about them above is pinned to the inputs
block and re-read when they settle.

## 8. Evidence

| claim | where | proof |
| --- | --- | --- |
| ten inputs at pinned heads, two not final, nine data-plane-only, all reaching T2.36 | sections 1, 4 S1, S2 | VP 1 |
| eight dialects as tuples: 4 first-rung counts, 5 second windows, 2 readings, 2 day counts + 1 rate | section 3.1 | VP 2 |
| 13 of 14 data edges run to a later wave, one stays inside wave 1; every item once; every item reaches T2.36 through staged edges; build gates on 2 waves, capability gates on 3 | sections 2.1, 2.2, 2.3, 2.4 | VP 3 |
| the merged fixture fails leg 1 once the F13 (b) edge is added (X21) | sections 0, 5 F13, F14 | VP 4 |
| the merged fixture passes the evaluator with 25 staged edges and 405 of 420 lines | section 2.4 | VP 4 |
| 5 settled and 22 contested rows, every contested row parked | section 4 | VP 5 |
| the plan validates; the branch is plan and report only; the fast tier passes | plan | VP 6, 7, 8 |
