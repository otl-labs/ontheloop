# REPORT: telemetry feedback loop staging (W3)

REPORT-ONLY deliverable of PLAN-w3-feedback-loop-staging.yaml (Decision 86 clause 2; file-router
report-deliverables route). Design stream of the CD.45 pilot fixture
docs/work-item-pilot/telemetry-feedback-loop.yaml, staging step W3 over the W2 synthesis (PR #1402) and
the ten W1 component designs. Nothing here is ratified, filed, merged, applied or flipped: the criteria
text in section 2 is staged for the operator to apply as a roadmap edit (Decisions 79/125, the
PLAN-t2-46 staged_t2_46_closeout precedent), the candidate decisions in section 3 are staged
candidate_decisions[] rows the operator files, and the list in section 4 is every question only the
operator can answer. docs/DECISIONS.md, docs/ROADMAP-PLATFORM.yaml and the fixture are not edited.

## 0. Verdict

Three things are staged and one thing is settled by the staging itself.

1. Criteria text (section 2). T3.3's second criterion, flagged unadjudicable by the 2026-06-09 audit
   (F-035) because the false-positive threshold had no formula and no home, gets both: the formula is
   the filer's rejected_share per detector per trailing 30 days as #1396 defines it, the denominator is
   filer proposals (read_all) or filed recs (later rungs), the source is the operator's verdicts joined to
   the filer run record in the loop journal, read beside run liveness, and the home is the filer's
   versioned rule set with that detector's register row as its monitor (the pilot item ids are provenance
   only; the fixture is not a roadmap and its ids are re-pointed at lift). The number stays the operator's
   (0.05 is the filer's provisional seed, unmeasured). The gap is adjudicable at read_all only: above
   read_all the operator marks just the reviewed fraction, so the share falls with the review fraction
   rather than with quality, and a detector that files nothing reads as passing; that measure question is
   parked as W3-6, so F-035 is narrowed, not closed. T3.3 c1 is re-grounded from "an
   agent files recs via log-rec" to the loop's own shape (daily detector runs, one filer, the portal
   path). T3.4 c1 is re-grounded step by step onto the pilot items it is realized by, ending in
   back-validation's verdict keyed to the fix sha under the Decision 201 amendment. T3.4 c2 keeps its
   A0-A3 meaning, states the review ladder as a separate ladder sharing one controller (the synthesis's
   reading of #1398 q1, parked), and flags its Decision 92 anchor as unverified (#1398 O2: no Decision
   names MED-9). Every status stays open; no criterion is added or flipped.
2. Candidate decisions (section 3). Six rows in the candidate_decisions[] shape (a seventh, CD.49, was
   retired by the operator's q2 ruling and its text folded into CD.50; the placeholder id is left unused).
   Four restate the synthesis's section 6 staged text, one per question it says one Decision settles: the loop review
   ladder (six contradictions, X4-X8, X16 and X19, one Decision), the loop record journal with its
   faults list (X11, six items' questions), the Decision 201 amendment for a telemetry verdict source
   (X10, P8), and the Decision 199 clause 1 reading for sessions_window (X3), for which the cheaper
   amendment_forms route is recommended over a number. Two go beyond section 6 because the component
   designs need them, and staging them was this report's own asked fork (W3-4, W3-5 in section 6): the
   cost and egress budget register (#1401, now carrying the breach route the operator chose, F13 (a)) and
   the metadata allow-list and transport amending Decision 209 clause 5(i) (#1399), a shell whose four
   always-ask forks stay blank. Five of the six rows are operator-accepted on every fork they presume
   (2026-10-04 rulings); CD.51 is not. All six stay pending until the operator files them. Each row names its significance routing, the
   contract home considered and rejected, the forks it presumes, and where a consult differs.
3. The consolidated list (section 4). Ninety-six rows ordered by effect: the capture session
   denominator first (it pins the whole loop at read_all and, under the synthesis's F1, F4 and F9, gates
   wave 2's read_all period), then F1-F14 with F13 and F14 as one question, then the operator's three
   Telemetry-project ownership calls, then every W1 fork and open question grouped by the wave its item
   builds in and, inside a wave, by what the answer binds (build before read_all run before promotion
   before close before text). Every row carries the design's recommendation and the consult's verdict
   where a consult exists, and, since 2026-10-04, the operator's ruling on it; the six places a consult
   differs outright, the two places the synthesis departs from the consults and the one converged row are in section 5 with
   both positions shown and the ruling beside them (the operator took the consult's position on every
   departure).

Settled by the staging: nothing in this report needs a Decision ratified, a status flipped or a fixture
edited to be true. Every claim is either a verbatim copy of a pinned input (checked by the plan's VP)
or a recommendation marked as such. Where the brief's inputs disagreed (consult against design,
synthesis against consult), the report carries both positions and chooses neither.

## 1. Inputs

Pinned by sha (git objects) or sha256 prefix (shared-folder files) so that a later edit to any of them
does not change what this report says. VP 1 re-reads them.

```yaml
inputs:
  synthesis: {pr: 1402, slug: w2-feedback-loop-synthesis, head: dcdfe78354203ac290c0ea35718ed7f2a7a80d6f, report: docs/plans/reports/REPORT-w2-feedback-loop-synthesis.md, plan: docs/plans/PLAN-w2-feedback-loop-synthesis.yaml}
  components:
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
  shared_folder_files:
    - {path: reports/design-fork-consults-2026-10-03.md, sha256_16: 7f95f559cc785386, role: "consult over #1384, #1390, #1394-#1400 (W1-9 re-run 2026-10-04 at fc298254)"}
    - {path: reports/design-fork-consults-w1-c10-2026-10-04.md, sha256_16: ce0d61f9dcbd4791, role: "consult over #1401"}
    - {path: reports/consult-alignment-d201-amendment-denylist-2026-10-04.md, sha256_16: b9436672d1b4595b, role: "alignment of the consults with the designs and the synthesis; Decision 201 amendment; deny-list question"}
    - {path: gates/w1-c1-parked-forks.md, sha256_16: d09aa38fa8f250e1, pr: 1384}
    - {path: gates/w1-c2-parked-forks.md, sha256_16: ef15b2e49b00b056, pr: 1390}
    - {path: gates/w1-c3-parked-forks.md, sha256_16: 6f9436be7f24c70f, pr: 1394}
    - {path: gates/w1-c4-parked-forks.md, sha256_16: 52e024b8b73f1101, pr: 1395}
    - {path: gates/w1-c5-parked-forks.md, sha256_16: 4a15e47ca0548ca8, pr: 1396}
    - {path: gates/w1-c6-parked-forks.md, sha256_16: 7ab0272b587bd6ca, pr: 1397}
    - {path: gates/w1-c7-parked-forks.md, sha256_16: 781da35525866597, pr: 1398}
    - {path: gates/w1-c8-parked-forks.md, sha256_16: aa5356a033c75649, pr: 1399, note: "re-pinned 2026-10-04 20:41Z (edited by the #1399 thread after #1405; column counts now 48 of 140 and fifteen accepted_values blocks)"}
    - {path: gates/w1-c9-parked-forks.md, sha256_16: af5579d112938ed2, pr: 1400, note: "re-pinned 2026-10-04 20:39Z (edited by the #1400 thread after #1405)"}
    - {path: gates/w1-c10-parked-forks.md, sha256_16: c202832f584d7f74, pr: 1401}
    - {path: gates/w2-parked-forks.md, sha256_16: c0259b4ed9b670b1, pr: 1402}
    - {path: reports/operator-rulings-2026-10-04-decision-sheet.md, sha256_16: 7988be184ae58813, role: "the operator's 2026-10-04 rulings on this report's section 4 (pass 1 plus fifteen questions); applied as the ruling column and the section 3 re-draft"}
    - {path: reports/decision-sheet-2026-10-04.md, sha256_16: aef608218ba69a41, role: "the decision sheet the operator answered; its row numbers are this report's section 4 ranks at c23df1f3"}
```

The ten component heads are the synthesis's inputs block verbatim (VP 1 checks equality), so a push to
a component branch after dcdfe783 changes neither document. #1384 and #1390 are read as not final, as
the synthesis reads them. After the 2026-10-04 rulings, #1394 (3ee985e9), #1395 (48955824) and #1396 (a5a4630b) merged to
main and the #1399 and #1400 threads re-pinned their parked-forks files against #1405's contracts (the
c8 and c9 rows above carry the new hashes); the component heads this report quotes are unchanged, and
the figures it quotes from #1399 (42 of 133 columns, twelve enforced accepted_values blocks) are the
pinned head's, with c8's post-#1405 figures (48 of 140, fifteen) noted where they appear; both are
re-read at the merge thread's main merge. Roadmap text is read from the working tree, which VP 7 proves equals main for
docs/ROADMAP-PLATFORM.yaml. Cross-project facts (the Telemetry project's answers of 2026-10-04 and the
coordinator's three ownership calls posted to the project chat at about 08:13-08:20Z that day) are taken
as the synthesis states them (its section 1 and X15, X20).

Vocabulary used below: "design" is the component report's or the synthesis's recommendation; "consult"
is the Fable consult's verdict (stays, refined, differs) against that recommendation, as its section 2
table records it; "asked" is the charter's fork class for a fork weighing alternatives or citing unmerged
or unanswered work, which is parked; "always-ask" is the charter's never-list class (IAM, security,
spend, governed deploys), which is parked whatever precedent exists.

## 2. Staged criteria text for T3.3 and T3.4

Both items carry bare-string exit criteria, so the ledger assigns c1 and c2 at load (exit-criteria-ledger
id semantics; VP 2 re-derives the ids and the verbatim text through scripts.platform_roadmap_models).
Each row is a text-only re-ground of an open criterion: status_today and status_staged are both open,
no criterion is added, met, rehomed or removed, and the edit is the operator's to apply (Decisions
79/125: roadmap edits are staged, never made by a design thread; the operator's 2026-10-01 ruling that a
text-only re-ground is not a flip was given for a met criterion and is not stretched here, which is why
the rows stay open and are presented rather than applied). The exit-criteria ledger's
realized-differently rule (rewrite the text to the realized mechanism) is the shape the staged text
follows. The staged text cites placeholder candidate-decision ids (CD.48, CD.50) and this report's row
numbers (section 4 row 1); the operator re-points both at application, to the filed Decision numbers and
to the fork names (capture q1, F9, F13), since neither survives a paste into the roadmap. The text
follows; it binds at the flip, which no row performs.

```yaml
staged_criteria:
  - item: T3.3
    criterion: c2
    gap: F-035
    status_today: open
    status_staged: open
    text_today: "False-positive rate below a threshold DEFINED at decomposition time (the strategic decomposition must name the number/formula and where it lives -- e.g. a governed config field; flagged unadjudicable as written, 2026-06-09 audit F-035); tracked via rec-curator"
    text_staged: >-
      False-positive rate below a threshold. Formula: rejected_share per detector per trailing 30 days,
      the share of the loop filer's proposals (at read_all) or filed recs (at later rungs) that the
      operator marks covered, duplicate or reject (pwi-rec-filing-dedupe failure_signal). Denominator:
      proposals or filed recs in the window; numerator: those marked covered, duplicate or reject.
      Source: the operator verdicts joined to the filer run record (one row per filer run, loop record
      journal), read beside run liveness (runs scheduled vs run records written, the loop_liveness_stale
      legs) so an outage never reads as a quiet queue. Home: the filer's versioned rule set (its
      params_version stamped on every filer run record), with that detector's row in the Goodhart
      register as its monitor; as designed in pwi-rec-filing-dedupe and pwi-goodhart-register, whose ids
      are provenance only (the pilot fixture is not a roadmap, CD.45, and its ids are re-pointed at
      lift). Number: the operator's; provisional seed 0.05 (the filer's own top-rung bound), unmeasured
      until friction rows exist. Adjudicable at read_all once the formula, denominator, window, source
      and home above are applied. OPEN above read_all (parked as W3-6): the operator marks only the
      reviewed fraction (sampled 0.2, spot_check 0.04, anomaly_triggered 0.008 as seeded), so an
      unreviewed rec reads as not rejected and the share falls with the review fraction rather than with
      quality; a detector with no proposals in the window reads 0/0 and must read unmeasurable, never
      below the threshold (the cheap way to a low rejected_share is to file nothing, #1396's own guard);
      covered and duplicate are dedupe misses, not false positives, so the measure is a noise share, not
      a false-positive rate in the FP/(FP+TN) sense. Until W3-6 is answered the F-035 gap is narrowed, not
      closed. Tracking surface: the operator's, rec-curator as today or the register row's daily verdict
      once it exists (#1400 k5 (c)).
    basis: "#1396 section 4 (failure_signal rejected_share, 'the T3.3 false-positive rate that audit F-035 found undefined, so W3 can stage it as that criterion's formula'); synthesis section 2.5 and section 6; #1400 k5 (c) names the F-035 threshold as one register row (the home's monitor); the filer's params_version stamp (#1396 section 2) is the home"
    consult: "#1396 rows stay or refine (k1-k6); no consult row contests rejected_share as the metric. The 2026-10-03 consult's open question 2 (review capacity) and 4 (loss asymmetry on a proof) bear on the number, not the formula."
  - item: T3.3
    criterion: c1
    gap: none (vocabulary and shape)
    status_today: open
    status_staged: open
    text_today: "Agent runs daily and files anomaly recs via log-rec"
    text_staged: >-
      Each loop detector (friction classifier, deliberation drift, Goodhart register, cost and egress
      budget) runs daily as a scheduled, deterministic read over the telemetry journal and writes its
      findings or verdict to the loop record journal; anomaly recs are filed through
      scripts.ops_data_portal file_rec and update_rec by the filer (pwi-rec-filing-dedupe) with
      fingerprint dedupe against open recs, and the budget's breach recommendation files through the cost
      reconciliation's existing rec path (F13 (a), operator ruling 2026-10-04 q2) ("log-rec" read as that portal path, Decision 84; Decision 67 boundary
      unchanged). Realized by the pilot items pwi-friction-classifier,
      pwi-deliberation-capture, pwi-goodhart-register, pwi-loop-cost-egress-budget and
      pwi-rec-filing-dedupe at their build, not by a bespoke telemetry_analysis_agent Lambda; the
      intent's "scheduled Step Functions execution in T4 per CD.27" is the schedule's home, decided at
      the build plan. Met when every detector above has run daily for its first-rung window (daily
      meaning no loop_liveness_stale leg fired in the window) with the detectors at read_all, and the
      filer, at sampled or above (read_all is propose-only, #1396 section 4), has filed at least one rec
      that traversed the queue. Under F9 (a), the operator's 2026-10-04 ruling (q1), the filer's leaving read_all waits on the
      capture session denominator (capture q1, section 4 row 1: the SessionStart hook invocation count,
      to be built), so this clause cannot be met until that count exists.
    basis: "synthesis sections 2.3 (waves 2 and 3), 3.4 (review units), 4 X18 (the rec-writing route, parked F13); #1396 section 4 (read_all is propose-only); #1396 section 2 (file_rec and update_rec through the portal); T3.3 files_in_scope names src/lambdas/telemetry_analysis_agent/ and a scheduled workflow, which the designs replace with the detectors; files_in_scope is a build-plan edit and is not staged here"
    consult: "the c10 consult's F13 row differs (file the budget's breach rec through the cost reconciliation path, not the filer); the operator took the consult's route on 2026-10-04 (q2) and the text follows it; the c10 consult's k1 shadow-month refinement (verdicts logged, nothing filed for the first measured month) would delay the 'at least one rec' clause by a month and is now CD.50's rule text (q13)"
  - item: T3.4
    criterion: c1
    gap: none (chain re-grounded onto the pilot items)
    status_today: open
    status_staged: open
    text_today: "Anomaly detected → rec filed → priority queued → /plan or executor → fix → telemetry delta proves fix"
    text_staged: >-
      Anomaly detected (a detector finding with a fingerprint: detector_id, subject key, classifier or
      parser version) -> rec filed by the loop filer with dedupe (pwi-rec-filing-dedupe; a budget breach
      rec files through the cost reconciliation's rec path, F13 (a), operator ruling 2026-10-04 q2) ->
      priority queued (the existing ops_priority_queue; Decision 67 boundary)
      -> /plan or executor -> fix (the fix PR names the rec in its Resolves: trailer; the telemetry
      verdict source stamps a fix attempt {fix sha, effective day} on the open rec at merge and supplies
      no verdict, so the rec stays open) -> telemetry delta proves fix (pwi-back-validation: exposed
      sessions in the matched baseline before the fix day against a post-fix sample at one
      classifier_version, registry_version and parser_version; holds under the back-validation decision
      rule declared in its contract, whose parameters (#1397 k4) live in that contract, not here; the
      verdict record is keyed to the fix sha and written days after the merge under the Decision 201
      amendment staged as CD.48; a holds is proposed for confirmation until the component reaches
      anomaly_triggered). Met when one real rec traverses the whole chain end to end, with
      back-validation at read_all and the filer at sampled or above (read_all is propose-only), ending in
      a holds verdict the operator confirms, with every step's record in the loop record journal. Under
      F9 (a), the operator's 2026-10-04 ruling (q1), the filer's leaving read_all waits on the capture
      session denominator (capture q1, section 4 row 1: the SessionStart hook invocation count, to be
      built), so this clause cannot be met until that count exists.
    basis: "synthesis section 2.3 (waves 3 and 4) and X10, X18; #1396 section 2; #1397 sections 2 and 5 (k1 plumbing (a), k4 decision rule (a)); CD.47 and CD.48 below"
    consult: "#1397 k1 stays (high on the amendment, medium on (a) vs (b)); k4 refined (add a partial reason; revisit (c) if overturned fails dominate); k2 refined (close_proposed only once the filer has also gone quiet). The chain text presumes k1 (a) and k4 (a); under k1 (b) the trailer clause changes to a separate trailer and job. The rec-filing step follows F13 (a), the consult's route, ruled by the operator on 2026-10-04 (q2)."
  - item: T3.4
    criterion: c2
    gap: none (relationship to the review ladder stated; stale anchor flagged)
    status_today: open
    status_staged: open
    text_today: "Autonomy maturity gates A0-A3 documented and live, WITH a per-gate rollback criterion for each (A-gate ownership boundary, EXR-06): T3.4 owns gate DEFINITION semantics for A0-A3 including rollback-from-birth, satisfying T4.4 MED-9 forward-only-autonomy-is-unsafe rationale at first-live (Decision 92 points 6-9); T4.4 extends the ladder to A4-A5 and owns rollback AUTOMATION + preflight/roadmap gate exposure -- see T4.4"
    text_staged: >-
      Autonomy maturity gates A0-A3 documented and live, WITH a per-gate rollback criterion for each
      (A-gate ownership boundary, EXR-06): T3.4 owns gate DEFINITION semantics for A0-A3 including
      rollback-from-birth, satisfying T4.4 MED-9 forward-only-autonomy-is-unsafe rationale at first-live
      (anchor unverified: no Decision names MED-9 and Decision 92 is the Terraform CI/CD ratification,
      #1398 O2; the roadmap owner re-points or drops the citation); T4.4 extends the ladder to A4-A5 and
      owns rollback AUTOMATION + preflight/roadmap gate exposure -- see T4.4. The telemetry feedback
      loop's review ladder (pwi-maturity-ladder-controller, part_of T3.4 provisionally; CD.46) is a
      SEPARATE ladder: it gates how much of each loop component's output a human reads (read_all,
      sampled, spot_check, anomaly_triggered), A0-A3 gate what the executor may do; the two share one
      controller and one transition log (the synthesis's reading of #1398 q1, parked). The review
      ladder's rungs each REQUIRE a return leg (an overturned output returns to read_all; an own-signal
      breach demotes one rung; a not-ok register verdict demotes one rung and holds; a version stamp
      bump restarts at read_all), each with its threshold's denominator, window and source stated as
      T4.4 requires, whether or not T3.4 is the ladder's home (#1398 k6 and q1, parked); A0-A3's own
      rollback criteria are not defined by the pilot.
    basis: "synthesis sections 3.2 and 3.5; #1398 k1, k3, k7 and O2 (/mnt/project-files/gates/w1-c7-parked-forks.md: 'Decision 92 is the Terraform CI/CD ratification, and no Decision mentions MED-9. The anchor looks stale (inferred; roadmap owner)'); T4.4 exit criteria 2 and 3 (numeric thresholds with denominator, window and source; a documented rollback criterion per gate); docs/DECISIONS.md carries no 'MED-9' string (VP 2)"
    consult: "#1398 k6 stays (part_of T3.4, anchored on criterion 1, medium; 'roadmap intent' is the operator's); k7 refined (demote once then hold with an output-count input and a dead-man alert, which the family carries); open question 13 of the 2026-10-03 consult (roadmap intent for T3.4 versus T3.3 and a future egress port item) is the operator's and is row F6 in section 4"
```

Two things the rows deliberately do not do. They do not add a criterion to either item: a new T3.4
criterion for the review ladder, or a T3.3 criterion for the register, would widen scope on strategic
items while Decision 67's STRATEGIC clause holds, and the pilot's items already carry those criteria in
the fixture (30 rows at the merge, synthesis VP 4). And they do not touch files_in_scope, depends_on or
status: T3.3's files_in_scope names a telemetry_analysis_agent Lambda the designs replace, which the
build plan of the first wave-2 item edits with the operator's approval, not a staging report.

## 3. Staged candidate decisions

Six rows in the candidate_decisions[] shape of scripts/platform_roadmap_models.py::CandidateDecision
(VP 3 validates each one). CD.46, CD.47, CD.48 and CD.52 restate the synthesis's section 6 staged text
(the ladder, the journal, the Decision 201 amendment, the X3 reading); CD.50 and CD.51 have no section 6
counterpart (the synthesis stages no budget-register Decision and classes #1399 k1-k4 as always-ask), so
staging them was an asked fork of this report (section 6, W3-4 and W3-5). A seventh row, CD.49 (the
route of the budget's breach recommendation), was retired by the operator's 2026-10-04 q2 ruling (F13
(a)): its option (a) text now lives in CD.50's alarm-not-gate clause, no separate Decision number is
needed, and the placeholder id is left unused so the decision sheet's references still resolve (the
operator re-numbers at filing). Since the 2026-10-04 rulings every row carries a RULINGS sentence
naming which of its presumed forks the operator answered and how; the consult refinements the operator
kept are named build refinements, never stripped and never rule text. Every row is still state pending,
filed by the operator, never by this thread; nothing is ratified. The ids are placeholders numbered from the next free id on main (CD.45 is
the highest today); the operator assigns the real numbers at filing against the then-current maximum
(the numbering-race rule of candidate-decision-ratification.yaml, applied to CD ids by analogy). Every
row is state pending, filed by the operator, never by this thread. The decision-entry significance
routing is applied per row in the cd_routing block that follows: the routing key claimed, the contract
home considered and rejected (the planning skill's contract-first test), whether a dated
amendment_forms annotation would do instead, the contradictions settled, the forks each row presumes
(all parked; the row's detail carries the recommended option and names the others), and where a
consult differs. A row whose presumed forks the operator answers differently is re-drafted at filing;
nothing here pre-empts those answers.

```yaml
staged_candidate_decisions:
  - id: CD.46
    title: Telemetry feedback loop review ladder -- one rule family over four rungs for every loop component, a dependency rung ceiling, the Goodhart register's verdict as precondition and input, automatic demotion and operator promotion (one Decision for X4-X8, X16, X19)
    detail: |
      RULE FAMILY (synthesis section 3.2, staged): every loop component carries one review ladder with rungs read_all,
      sampled, spot_check and anomaly_triggered over one named reviewable unit. Promotion: N consecutive outputs the
      operator confirmed unchanged since the last overturned one (read_all -> sampled), M consecutive sampled outputs at
      fraction f1 (sampled -> spot_check), D consecutive days with the component's own failure_signal within its bound
      at fraction f2 (spot_check -> anomaly_triggered), each with the register verdict ok for that component on that
      day. Demotion, automatic and logged: an overturned output returns to read_all (CSP-1 reset); an own-signal breach
      above read_all demotes one rung and holds until the bound holds for D days (signal_breach); a not-ok register
      verdict demotes one rung and holds, with an output-count input so no outputs is told from monitor dark and a
      dead-man alert while it holds; a drop of the ceiling demotes to the new ceiling the same day; an integer version
      stamp bump restarts at read_all. CEILING: effective rung = min(earned rung, min rung among the component's data
      upstreams); the earned rung is retained while capped and the effective rung snaps back when the upstream recovers.
      LANE: rung per component, or per producer lane where the item's triggers are per producer (deliberation); a lane
      restarts on its declared restart events. AUTHORITY: the operator approves every promotion (a standing approval
      rule for named (rung, version) pairs while the operator is away is the consult's refinement, kept as a
      named build refinement); every demotion is automatic. REGISTER (folds #1400 section 6): every detector whose series gates a
      transition carries a register row naming the cheapest way its metric improves without the work improving, a
      counter series independent of the metric's numerator and a seeded drill measuring recall; the register owns every
      drill and reads soundness only (no breach leg: component health is each item's own top-rung bound); a daily
      deterministic verdict is sound only when signal, counter, drill and upstream detectors are sound; a series
      without a row is unsound by definition; the register carries a row for the budget and one for itself. REVIEW
      LOAD: the ladder owns the review_items envelope (operator-set) and the budget reads it as a diagnostic
      line, so the review schedule and its envelope live in one place (operator ruling 2026-10-04 q7, the
      consult's reading; the synthesis's F4 (a) split, the budget owning the line, was not taken); wave 2's
      read_all period runs under about 60 reviews a day or waits. DRILL AT THE TOP RUNG: a windowed drill (20
      faults in 20 days, recall at least 0.9 as a provisional seed) plus its cure (a1), with promotion gated
      on reviewed outcomes, not the signal (#1398 k5, operator ruling q14).
      SCHEMA: the pilot schema change for a return leg, a review fraction and a version rule (P7, #1398 O1) lands with
      this Decision; until then the family is prose. Seeds stay per component and provisional (synthesis section 3.3).
      RULINGS (2026-10-04): every fork this row presumes is answered. F1 (a)+(d), F2 (a), F3 (a), F7 (a), F11
      (a), #1398 k1 (a), k2 (a)(a1), k3 (a), k7 (a) for a breach and (d) for dark, #1400 k1 (a)/(b) by verdict
      kind and k1 (v) (b) are accepted as designed (pass 1) with the consult refinements kept as named build
      refinements; #1398 k5 (a)+(a1) with reviewed-outcome gating is ruled by q14; #1400 k1 (i)-(iii) as
      staged with the consult refinements (q15); F4 is taken on the consult's reading (q7); the register's four consult
      departures (fail-closed counter staleness q6, effective-dated retirement q8, per-detector malformed
      verdict q9, a required register_row reference at lift q10) are taken on the consult's reading and are
      #1400's to fold at build (q10 binds at lift only). The row stays pending until the operator files it.
    gates: []
    affects: [T3.4, T3.3, T3.20, T2.36]
    state: pending
  - id: CD.47
    title: Telemetry feedback loop record journal -- one append-only journal in the data plane on the Decision 199 model, one table per record kind, every daily verdict record carrying a faults list (one Decision for X11)
    detail: |
      Every loop component's operational record (filer run, back-validation verdict, ladder transition and current rung,
      allow-list canary and egress log with withheld counts, register verdict, budget line verdict and attribution)
      lives in one append-only loop record journal in the data plane, modelled on Decision 199 (append-only lifecycle
      rows, write-boundary-derived identity, derived-at-read state), one table per record kind, never a new contract
      class (F8 (c)) and never one table per item chosen by each owner (F8 (b)). Every component reads and writes its
      own kind only. Every daily verdict record (the register's and the budget's) carries a
      faults list naming each leg that fired that day, with the register's leg names (dark, blind, undrilled,
      counter_dark, counter_low, unregistered, diverging, upstream_unsound, malformed) and the budget's per-line labels
      (warn, projected_breach, breach, dark, unattributed, unregistered, with dark_flag and unattributed_flag beside
      them) as the vocabulary; the invoice residual is the budget's monthly leg and not a daily fault. A verdict
      record stamps the register or budget version it read (#1400's register version, report section 4; #1401
      section 2.4; synthesis 3.2's version_change list). Grain, stated by this staging per
      the data-modeling default and confirmed at build: one row per (component, run or verdict, day). Answers #1396
      q4, #1397 q4, #1398 q2, #1399 q3, #1400 q1 and #1401 k2's record half; the budget's register values keep
      #1401 k2's home. CONSULT REFINEMENTS, named build refinements beside the design's text, never rule text: a run's record
      carries the producer tag and is excluded from the detectors it feeds (#1396 q4; carried for the build,
      not covered by pass 1 since the row is stays); each table's schema is recorded as a Class A contract
      entry the day the table exists (c10 k2). RULINGS (2026-10-04): F8 (a) accepted (pass 1); the faults list
      adopted (q11), #1400 and #1401 fold it at build; the row stays pending until the operator files it. The record kinds are the synthesis's list (section 6); the schemas are the
      build's.
    gates: []
    affects: [T2.36, T3.3, T3.4]
    state: pending
  - id: CD.48
    title: A telemetry verdict source joins Decision 201's layer -- a fix attempt stamped at merge, a verdict keyed to the fix sha evaluated later, back-validation the only proof proposer, filer closes keyed to its rung (amends Decision 201 points 1-2; one Decision for X10, P8)
    detail: |
      A third verdict source, "telemetry", joins Decision 201's verdict layer beside "static" and "junit". It claims a
      rec whose acceptance carries the back-validation assert-holds contract. At merge the fix PR names the rec in its
      Resolves: trailer as today; the census routes the acceptance to the telemetry source, which records a fix attempt
      {fix sha, effective day} on the open rec and supplies no verdict, so the rec stays open (Decision 201 point 3's
      own behaviour). A scheduled job later runs the back-validation decision rule, declared in a contract with its
      vectors, and writes the verdict record keyed to the fix sha. Points 1 and 2 are amended for the
      telemetry source only: the verdict is evaluated after the closing commit and keyed to the fix sha. Decision 103's
      oracle rule is untouched (the rec's acceptance reads the record). The late closer takes update_rec's closing sha from the recorded fix attempt, never from the verdict record, and
      always supplies a record (a non-trailer caller, rec-3999's fail-open). A holds is proposed for confirmation
      (close_proposed) until the component reaches anomaly_triggered; whether a statistical holds (designed 5 percent
      false-proof rate) may ever close directly, even at the top rung, is the operator's and if never the direct-close
      arm is struck. A fails leaves the rec open; an unmeasurable verdict at the deadline goes close_proposed with its
      reason and counts (#1397 k2 (b)). Back-validation is the only proposer of a PROOF close. The filer's own closes are rung-keyed
      (operator ruling 2026-10-04 q5, the consult's position): at read_all and sampled the filer may only
      propose a close (close_proposed) for the operator's review; from spot_check the denominator close
      applies (stale_no_recurrence after N below-floor windows each observing at least M sessions, the rec at
      least 14 days old, #1396 k4 (b)); "never" is not an option; who reviews proposals stays the operator's.
      CONSULT REFINEMENTS, named build refinements beside the design's text, never rule text (the k2 refinement
      kept by pass 1, a refined row; the two k1 additions carried for the build): the job reconstructs fix attempts from the Resolves: trailers in git history,
      so the merge-time stamp is a cache, never the source of truth (#1397 k1 addition; carried for the build, not covered by pass 1 since backval-k1 is a stays row); the fix attempt is recorded
      under a key distinct from the existing fixed-by-sha field (#1397 k1 addition; carried for the build, not covered by pass 1 since backval-k1 is a stays row); the unmeasurable close_proposed
      waits until the filer has also gone quiet (#1397 k2 refinement, kept by pass 1 on the backval-k2 row, section 4 row 91). RULINGS (2026-10-04): #1397 k1 (a) plumbing,
      k2 (b) and k4 (a) accepted as designed (pass 1) with the refinements above kept; #1396 k4 taken on the
      consult's reading (q5); the row stays pending until the operator files it.
    gates: []
    affects: [T3.4, T3.3]
    state: pending
  - id: CD.50
    title: Telemetry feedback loop cost and egress budget register -- every meter a budget line with an owner, kept in the meter's unit and priced at read, attributed per call, alarm-not-gate (one Decision for #1401's register)
    detail: |
      Every meter the loop turns is a budget line with an owner, typed budget, diagnostic or tripwire (the ten
      lines of #1401 section 2.2, k9 (a); typing per operator ruling 2026-10-04 q12); the review_items line is
      owned by the ladder (CD.46) and read here as a diagnostic line (q7); only budget and tripwire lines
      alarm, a diagnostic line never does. A
      line is kept in the unit its meter counts (bytes, requests, GB-seconds, tokens, review items), never in
      dollars; dollars are derived when read from a versioned price table and never stored or published. Each line has a
      cadence, a measure (a flow summed over the month, or a stock read at its latest level), a monthly envelope and
      a warn share; a shared line's daily value is attributed per call to the components that caused it (Decision
      199's producer field; a caller tag on the Lambda log line, k5 (a)) within a stated tolerance. A line's meter is
      read from what the loop's own code and catalog already know; a meter that needs a billed metric or an
      infrastructure change is a decision of its own (the options #1401 k4 (b) and k8 (b)/(c) name, a Neon
      consumption API key, S3 request metrics, access logs, Inventory and Storage Lens, and the c10 consult's
      dedicated telemetry bucket, are never-list for an autonomous thread under the project's instructions). A meter counted by the loop's own
      Lambdas ships through the governed code-deploy channel like any other Lambda change (always-ask at build, k5).
      A daily deterministic verdict reads the register, the ledger
      and the attribution and reports ok, warn, projected_breach, breach, dark, unattributed or unregistered per
      line with dark_flag and unattributed_flag beside the label and a faults list (CD.47). BREACH (F13 (a) and
      q13, operator ruling 2026-10-04; absorbs the retired CD.49): a breach files one recommendation per
      breaching line through the cost reconciliation's existing rec path (its build_rec_fields and
      find_open_cost_rec_for), at projected_breach and updated at breach, one rec per line per month, and gates
      nothing; F14 (a) follows, no filer edge, so the loop has two rec writers, the filer and this path, each
      with its own dedupe under the Decision 67 boundary; the first measured month runs in shadow mode,
      verdicts logged and nothing filed, re-seeding the envelopes; stopping a schedule is a human-pulled kill
      switch on the consumer, never the meter, and never unattended. A line that reports nothing on a day it
      should is a failure of the loop, not a saving. The register lives beside the monthly reconciliation's thresholds (a loop_budget block in
      config/agent/cost_reconciliation.yaml, k2 (a)) with its values carried by the pilot item until then (k2 (c)),
      versioned and stamped on every verdict record; the monthly discrepancy leg (k3 (c)) reconciles the priced
      ledger against the invoice and records the residual. Decision 88's catalog egress line is the first line, and
      its envelope is set from a measured reading, never a planning-time guess: the seeds are provisional until the
      first measured month replaces them. CONSULT REFINEMENTS, kept by the operator's 2026-10-04 pass-1 ruling
      as named build refinements beside the design's text, not as rule text: the price table effective-dated, a
      closed month never re-priced (k3); days-to-envelope as a derived read on a stock line after seven
      readings (k7); the attribution stamp mandatory at the write boundary with a reserved canonical component
      "unknown" for a missing stamp (k5); the catalog egress envelope seeded from the Neon plan's actual
      included egress rather than 1 GiB (seeds). RULINGS (2026-10-04): #1401 k1 (a) with its three refinements
      (q13), k2 (a)+(c), k3 (a)+(c), k4 (a), k5 (a), k7 (a), k8 (a)+(d), k9 (a) typed (q12) and the seeds as
      provisional are accepted; F13 (a) and F14 (a) ruled (q2). Still open, always-ask: the read-only Neon
      consumption key (k4 (b)); S3 request metrics, access logs, Inventory, Storage Lens and the dedicated
      telemetry bucket (k8); the governed Lambda deploy behind per-call stamps (k5); and the Neon-to-RDS swap,
      the operator's alone. The row stays pending until the operator files it.
    gates: []
    affects: [T2.36, T2.19, T2.26, T2.52]
    state: pending
  - id: CD.51
    title: Metadata allow-list and plane transport -- a per-column allow-list over the telemetry Class A contracts applied in the data plane, with membership, identifiers, transport and grain left blank for the operator (amends Decision 209 clause 5(i); a shell gated on four always-ask forks)
    detail: |
      NOT RULED (2026-10-04): the four always-ask forks below were excluded from the operator's agreement and
      await the operator's own answer; only k5 (a) is accepted (pass 1). The metadata allow-list is a per-column
      list of crossing columns over the telemetry Class A contracts, each field carrying an egress class with a
      completeness check (#1399 k5 (a)), applied in the data plane by a named reader
      verb before anything crosses; an unclassified source column or a malformed tenancy id refuses the batch;
      deny-list membership at the boundary is excluded by Decision 209 clause 2(b) and by measurement (#1399 VP 5).
      FOUR BLANKS, each an always-ask security or IAM fork the operator fills; nothing below chooses
      one, and the design's option and the consult's position are shown side by side. k1 MEMBERSHIP: design (a)
      tenancy ids as the key, guarded closed enums, finite numbers and booleans (42 of 133 columns at the
      pinned head; 48 of 140 after #1405 per the re-pinned c8 file), with an
      out-of-vocabulary value withheld and counted; (b) also open-vocabulary strings matched against a control-plane
      vocabulary; (c) the Decision 199 envelope as written; consult refinement of (a): value-guard enums against
      their vocabulary, refuse the batch on a structural surprise, and write the egress threat model the design
      leaves to the operator as part of k1. k2 IDENTIFIERS: design (a) no derived id
      crosses, tenant_id and project_id only; (b) re-key each crossing id with an HMAC under a data-plane key; (c)
      ids as written (a confirmation oracle, #1399 VP 6); consult refinement: (b) is the precondition of any later
      row grain. k3 TRANSPORT: design (a) the data plane pushes to a tenant-scoped ingest endpoint under a
      customer-revocable credential; (b) the control plane pulls through a customer-granted role; (c) both, by
      adapter; consult refinement of (a): a short-lived write-only credential and an ingest-side rejection of any
      column outside the published list as a second, deny-shaped check. k4 GRAIN: design (a) day aggregates per
      tenant, project, UTC day and enum values, with no minimum-cell rule staged; (b) guarded rows with the day
      only; (c) guarded rows with millisecond timestamps; consult position, shown beside and not adopted: a
      minimum-cell coarsening and marginal dimensions inside (a). This row is filed only after the operator answers
      k1-k4, and any credential, role or endpoint it needs is the operator's to grant. Presumes #1399 k5 (a) only;
      q1 (vocabulary owners) and q2 (first dashboard's needs) open.
    gates: []
    affects: [T4.24, T2.36]
    state: pending
  - id: CD.52
    title: Decision 199 clause 1 reading for sessions_window -- a bounded window of per-session derivations, each reading only its own single-day partition, satisfies the partition bound; one stated reading wherever the verb is built (X3; amendment_forms route recommended over a number)
    detail: |
      Decision 199 clause 1 bounds each derivation to one session's single-day partition. A window verb over many
      sessions (sessions_window: one derived row per root session, at most 7 days, paginable) satisfies the clause as
      a bounded window of per-session derivations, each reading only its own partition; the reading is stated once
      wherever the verb is built and the verb stamps its registry_version so a delta across a bump is never read as
      a change. Two independent questions stay the operator's: where the verb is built (#1390 k2: (a) the reader,
      shaped to T2.52 c3 for later adoption, or (b) T2.52 c3 as the ownership route) and whether "telemetry lands
      on DuckLake" reactivates T2.52 (the "lands" reading, which follows under either k2 option; a tier-item status
      change, operator-only); the consult reads "lands" as T2.36 c2. ROUTING: this is a reading of one clause at one call
      site, so a dated amendment_forms annotation on Decision 199 (decision-entry.yaml) carries it at zero header cost;
      it is listed here so the operator sees it beside the other rows and files it as a number only if the T2.52
      reactivation half is taken, which independently clears the significance bar (a tier-item lifecycle change with
      Decision 93 consequences). RULINGS (2026-10-04): #1390 k2 (a) accepted (pass 1); the consult's Update-note route is W3-1's
      recommendation and W3-1 stays open, as does the T2.52 reactivation half.
    gates: []
    affects: [T2.36, T2.52, T3.3, T3.4]
    state: pending
```

```yaml
cd_routing:
  - {id: CD.46, significance: numbered_decision, rejected_home: "a governance note on the pilot fixture (docs/work-item-pilot/) or on data-modeling-standard.yaml: the family binds ten components and a controller across two roadmap items, which no single contract owns; the pilot schema change (P7) is evaluator code and cannot carry the rule", amendment_forms: "none applies: no prior Decision owns a review ladder (T3.4 c2 is roadmap text; Decision 92 is not the anchor it is cited as)", settles: [X4, X5, X6, X7, X8, X16, X19], presumes: ["F1 (a)+(d)", "F2 (a)", "F3 (a)", "F4 (consult reading, q7)", "F7 (a)", "F11 (a)", "#1398 k1 (a)", "#1398 k2 (a)(a1)", "#1398 k3 (a)", "#1398 k5 (a)+(a1) with reviewed-outcome gating (q14)", "#1398 k7 (a)+(d)", "#1400 k1 (a)/(b)", "#1400 k1 (v) (b)"], consult: "F4 differs (consult: the ladder owns the review-items envelope; synthesis: the budget owns the line, the ladder the schedule); #1398 k3 refined (standing approval rule); #1400 k1 (iv), k2 retirement/onboarding, k2 malformed-input and k3-at-lift differ (section 5)", rulings: "2026-10-04: all presumed forks answered; F4 and the four register departures on the consult's reading (q6-q10); the rest accepted as designed with refinements kept (pass 1, q14, q15); X5 now settled here (the envelope) with CD.50 reading it"}
  - {id: CD.47, significance: numbered_decision, rejected_home: "a governance note on data-modeling-standard.yaml (owns HOW a table is designed, not WHERE six components' records live) or a new Class C loop-journal contract (F8 (c): a contract class with one occupant, Decision 86's anti-pattern)", amendment_forms: "a dated annotation on Decision 199 was considered and rejected: Decision 199 governs telemetry events, and applying its model to loop records is a new commitment, not a reading of it", settles: [X11], presumes: ["F8 (a)"], consult: "converged: both consults asked the register and the budget for a faults list; #1396 q4 stays (journal, medium-low) and its producer-tag exclusion, with the c10 k2 contract-entry note, are named in the row as refinements", rulings: "2026-10-04: F8 (a) accepted (pass 1); faults list adopted (q11); refinements kept as named build refinements"}
  - {id: CD.48, significance: numbered_decision, rejected_home: "docs/contracts/git-ops.yaml trailer_acceptance_gate governance note (owns the trailer path's instruction surfaces, not a new verdict source with its own closer and enforcement site)", amendment_forms: "a dated annotation on Decision 201 was considered and rejected: the change adds a third source, a late closer and a non-trailer update_rec caller, which binds every verdict-supplying path (Decision 201's own significance justification), so it clears the bar on its own terms; the consult rates the need for a numbered amending Decision high", settles: [X10], presumes: ["#1397 k1 (a)", "#1397 k2 (b)", "#1397 k4 (a)", "#1396 k4 (consult reading, q5)"], consult: "#1397 k1 stays (high on the amendment, medium on (a) vs (b)); its two additions (trailer-reconstructable attempts; a distinct stamp key) and the k2 'filer gone quiet' refinement are named in the row as build refinements (k2 kept by pass 1; the k1 additions carried, backval-k1 being a stays row); #1396 k4 differs (consult: self-close by rung with close_proposed interim; design: never, until back-validation exists)", rulings: "2026-10-04: #1397 k1 (a), k2 (b), k4 (a) accepted with refinements kept (pass 1); #1396 k4 on the consult's reading (q5), the no-self-close clause now rung-keyed"}
  - {id: CD.50, significance: numbered_decision, rejected_home: "a governance note in config/agent/cost_reconciliation.yaml (the register's home under #1401 k2 (a), but a config file holds thresholds, not the unit, attribution and alarm-not-gate commitments) or Decision 88's own text (the egress line only)", amendment_forms: "a dated annotation on Decision 88 was considered and rejected: Decision 88 budgets one line; this row binds ten lines, a price table and an attribution rule", settles: [X5], presumes: ["F13 (a) (q2)", "F14 (a) (q2)", "#1401 k1 (a) + refinements (q13)", "#1401 k2 (a)+(c)", "#1401 k3 (a)+(c)", "#1401 k4 (a)", "#1401 k5 (a)", "#1401 k7 (a)", "#1401 k8 (a)+(d)", "#1401 k9 (a) typed (q12)", "#1401 seeds"], consult: "all nine #1401 rows stay or refine; the shadow month and the typed lines are ruled (q13, q12) and are rule text; effective-dated prices, the unknown component, days-to-envelope and the catalog egress seed are named build refinements kept by pass 1; the projected_breach filing and per-line monthly dedupe now live in this row's BREACH clause (the retired CD.49's option (a)); the review_items line is the ladder's (q7)", rulings: "2026-10-04: F13 (a), F14 (a) (q2); k1 (a) plus refinements (q13); k9 typed (q12); k2, k3, k4, k5, k7, k8 and the seeds accepted with refinements kept (pass 1); open: the Neon key, the S3 always-ask meters and bucket, the governed Lambda deploy, the Neon-to-RDS swap"}
  - {id: CD.51, significance: numbered_decision, rejected_home: "a per-field egress key in each telemetry Class A contract (#1399 k5 (a)) is the MECHANISM and is kept; it cannot carry the boundary rule, which Decision 209 clause 2(b) and 5(i) own", amendment_forms: "a dated annotation on Decision 209 was considered and rejected: clause 5(i) names rec-4141 as the vehicle and its reversal condition allow-list-transport says 'amend clause 5(i)', which is an amending entry, and the content (membership, grain, identifiers, transport) is a security boundary with reversal-relevant consequences", settles: [], presumes: ["#1399 k5 (a)", "k1-k4 left blank (always-ask; design and consult positions shown side by side)"], consult: "all six #1399 rows stay or refine; the alignment consult's hybrid (allow-list on structure, deny-by-value inside allowed columns, ingest-side second check) is shown beside the design's option in each blank, not adopted; the #1399 k4 minimum-cell refinement likewise; k1-k4 are always-ask and the row is filed only after the operator answers them", rulings: "2026-10-04: not ruled; k1-k4 excluded from the agreement; k5 (a) accepted (pass 1)"}
  - {id: CD.52, significance: field_semantics, rejected_home: "none rejected: a dated amendment_forms annotation on Decision 199 clause 1 IS the recommended home (decision-entry.yaml amendment_forms); the row is listed as a CD only so the operator sees the T2.52 reactivation half beside it", amendment_forms: "recommended; a numbered entry only if the operator also reactivates T2.52 (#1390 k2 (b)), which is a tier-item status change under Decision 93", settles: [X3], presumes: ["#1390 k2 (a)"], consult: "#1390 k2 stays (build sessions_window in the reader; record the cl.1 reading as a Decision 199 update note; 'lands' = T2.36 c2; medium-high); the operator's reading of 'lands' decides the T2.52 half (consult open question 7); Decision 199 carries three dated Update notes on main after #1405, the accretion cap being written for the Intent section", rulings: "2026-10-04: #1390 k2 (a) accepted (pass 1); the route (W3-1) and the T2.52 reactivation half open"}
```

X5 (the review-load line) is settled by CD.46 since the operator took the consult's F4 reading on
2026-10-04 (q7): the ladder owns the review_items envelope and CD.50 reads it as a diagnostic line.

Not candidate decisions, and why. The ratified Class A contract amendments the designs need are
field_semantics by decision-entry's routing (the meaning of a field or a derivation) and go to the
owning contract as dated amendments, never to a numbered Decision; each is operator-only because it
changes ratified contract text, and each waits on the fork named: telemetry_sessions session state and
duration rule (#1390 k1 (a): latest lifecycle row, close ranked last on a tie, wall-clock duration;
#1384's failure_signal depends on it, its report's M10 asked W2 and W3 to stage it as pending);
telemetry_sessions derived abandoned state with a governed idle threshold (#1390 k3 (a)); the
telemetry_sessions rework_total and exception_total derivation (#1394 k2 (a): ratify the slice-3a
deviation, facts are non-info process_events plus error or blocked tool_call closes, inputs add
severity and outcome; X17, with the operator's 2026-09-24 ruling on the other side); the rules-file
pointer wording at telemetry_sessions.yaml:232 and :243 (#1394 k1 (a)); a drill-row marker on a
telemetry contract (#1400 O3) and the enforced flag on the twelve accepted_values blocks (#1399 O1; fifteen after #1405 per the re-pinned c8 file),
both named for the contract owner. The staged pilot edge list (F5), the merge order (F10) and the three
edge homes (F6) are fixture edits the merging thread of each wave applies with the operator's approval
of the fork, not Decisions (work_item routing: sequencing, no shape commitment). The three
Telemetry-project ownership calls are operator decisions with no Decision text to stage.

## 4. Every parked question for the operator, ordered by effect

One list. The order is the synthesis's for the first three groups (the capture session denominator,
then F1-F14 with F13 and F14 as one row, then the three Telemetry-project ownership calls), then every
W1 fork and open question grouped by the build wave of its item (synthesis section 2.3) and, inside a
wave, by what the answer binds: build, then the read_all run, then promotion, then a close, then text,
then a measurement. VP 4 checks that the groups and the binds_at values are in that order, that every
fork row of the eleven parked-forks files, and every open question their prose names, is a row here or is
covered by one, and that every row of the
two consults' recommendation tables maps to a row here (the mapping is the consult_rows block that
follows the list).

RULINGS (2026-10-04). The operator answered this list on 2026-10-04 (thread message of 20:25:53Z and
the confirming line of 20:27:54Z; the table is /mnt/project-files/reports/operator-rulings-2026-10-04-decision-sheet.md,
pinned in section 1): pass 1 accepts the design's recommended option on every row whose consult verdict
is stays, refined or n/a, keeping the consult's refinements on the refined rows as named build
refinements; fifteen questions (the gating decisions and the consult departures) were answered row by
row; the four always-ask rows, the always-ask options inside other rows, this report's own forks W3-1
to W3-6 and the Neon-to-RDS question stay open. Each row below carries a ruling column with its
answer. The rulings are recorded as text: no Decision is ratified, no status flips, and the candidate
decision rows in section 3 stay pending until the operator files them.

One warning for whoever builds. The en bloc conflict between F5 (a) and F13 (b) is removed by the q2
ruling (F13 (a)); only F5 and F10 (rows 6 and 11) gate the first merge, and every row above them binds
a promotion or a read_all run, which comes later.

Columns: ruling is the operator's 2026-10-04 answer (pass 1, a numbered question, or open); source is the fork's own id in its parked-forks file; class is asked or always-ask; design is
the recommending report's option; consult is the consult's verdict against that recommendation (stays,
refined, differs, or n/a where no consult row exists), with its position where it adds or differs;
unblocks says what the answer releases; covers lists W1 questions that this row answers at once.

```yaml
parked_questions:
  - {rank: 1, id: capture-q1, group: whole_loop, binds_at: promotion, source: "#1384 q1", class: asked, question: "What is the out-of-band session denominator for sessions that land no rows (a runner that never succeeds; a session lost before its first turn closes)?", options: "the runner's own diagnostics; an open row at the SessionStart pass (a record_turn rule change against the generation-marker rule, Decision 207 R3); hook rows' exit_code once any later pass succeeds (runner exit-1s only); the SessionStart hook's own invocation count (synthesis F9 (c), staged candidate)", design: "synthesis F9: accept the read_all pin now, SessionStart invocation count as the staged candidate answer", consult: "n/a as a row; the 2026-10-03 consult's #1400 k1 verdict sequences capture q1 first and names 'accept a frozen ladder until capture q1 lands' as the operator's call", unblocks: "every promotion above read_all for every component (X13); under F1 (a), F4 (a) and F9 (a) also wave 2's read_all period and by sequence waves 3 and 4 (P5, X22)", ruling: "operator ruling 2026-10-04 q1: (a), accept the read_all pin now; build the SessionStart hook invocation count as the independent denominator (F9 (a) now, (c) as this question's answer)"}
  - {rank: 2, id: F1, group: whole_loop, binds_at: promotion, source: "F1", class: asked, question: "Rung ceiling by dependency", options: "(a) rung <= min upstream rung; (b) <= min upstream + 1; (c) none; (d) (a) with an earned rung retained under the cap and an effective rung that snaps back", design: "(a) with (d)", consult: "n/a (post-consult); the 2026-10-03 consult's ladder rows (#1398 k1, k4) take CSP-1 and multi-level CSP as the family, consistent with (a)", unblocks: "CD.46's ceiling clause; the X13 read_all pin is its cost", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 3, id: F2, group: whole_loop, binds_at: promotion, source: "F2", class: asked, question: "One rule family and the demotion split (synthesis 3.2), including the own-signal breach leg kept as signal_breach (verification r1 B4)", options: "(a) the family as staged; (b) keep the ten dialects and add the verdict precondition only; (c) #1398's rule verbatim with one demotion leg", design: "(a)", consult: "refined at the W1 level: #1398 k4 (multi-level CSP, SPRT as an upgrade, raise the 0.008 floor) and k7 (one rung on breach; dark = demote once, hold, output-count input, dead-man alert), which the family carries", unblocks: "CD.46's rule family; the pilot schema change P7", covers: ["#1398 k7 breach half (kept as signal_breach)"], ruling: "operator-accepted 2026-10-04 (pass 1): the design's option; the consult's refinements kept as named build refinements"}
  - {rank: 4, id: F3, group: whole_loop, binds_at: promotion, source: "F3", class: asked, question: "Drill home", options: "(a) the register owns every drill and the controller reads verdict ok; (b) the controller keeps its top-rung drill, the register drills the rest; (c) both drill", design: "(a)", consult: "n/a as a row; #1398 k5 refined (windowed drill plus cure (a1); promotion gated on reviewed outcomes) and #1400 k2 stays (replay for SQL detectors, live for capture and canary; reviewer drill at a low rate with the operator's consent)", unblocks: "CD.46's register clause; X4", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 5, id: F4, group: whole_loop, binds_at: read_all_run, source: "F4", class: asked, question: "Review-load owner", options: "(a) the budget owns the line and alarm, the ladder owns the schedule, waves are the lever; (b) the ladder owns it as its control variable (#1398 R1); (c) the budget owns it and may demote (#1401 k1 (b) at lift)", design: "(a)", consult: "DIFFERS: the c10 consult's k9 and the alignment consult read it as the ladder owning the review_items envelope with the budget reading it (one number, one owner); the synthesis keeps the line in the budget because it is a cost line like the other nine and (c) is a Goodhart path", unblocks: "wave 2's read_all period (section 3.4 of the synthesis: about 48 reviews a day against a seed of 30, so an envelope of about 60 or a hold); CD.46 and CD.50's review-items line", covers: ["#1401 q3", "#1398 R1"], ruling: "operator ruling 2026-10-04 q7: the consult's position; the ladder owns the review_items envelope (operator-set) and the budget reads it as a diagnostic line; the line moves from CD.50 into CD.46 and CD.50's roster loses one alarming row; wave 2's read_all period runs under about 60 a day or waits"}
  - {rank: 6, id: F5, group: whole_loop, binds_at: build, source: "F5", class: asked, question: "Staged pilot edge list under MAX_EDGES_FROM_ITEM 3", options: "(a) drop the direct depends_on T2.36 edge where a pilot upstream carries it and the filer's direct edge to the reader (four items change); (b) raise the cap (W0 evaluator code, never chosen); (c) drop a data upstream", design: "(a)", consult: "n/a", unblocks: "the merge of waves 2-4 (synthesis VP 4: 25 edges, 405 of 420 lines, legs clean under (a))", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 7, id: F6, group: whole_loop, binds_at: text, source: "F6", class: asked, question: "Edge homes for the three depends_on-only items (allow-list, register, budget) and the ladder's provisional part_of T3.4", options: "(a) all three stay depends_on T2.36 only until the operator names homes; (b) all three part_of T3.4; (c) register and budget part_of T3.3, allow-list part_of T4.24; the ladder's T3.4 home is re-read with #1398 q1", design: "(a)", consult: "stays at the W1 level (#1398 k6 part_of T3.4 anchored on criterion 1, medium; #1399 k6 and #1400 k5 depends_on T2.36 only, high); open question 13: roadmap intent is the operator's, near-zero cost either way", unblocks: "the fixture's part_of rows and T3.4 c2's staged sentence on the ladder's home", covers: ["#1398 k6", "#1399 k6", "#1400 k5", "#1401 k6"], ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 8, id: F7, group: whole_loop, binds_at: promotion, source: "F7", class: asked, question: "The register verdict as controller input", options: "(a) input, with the staged depends_on edge; (b) beside, as an operator read", design: "(a)", consult: "n/a as a row; the 2026-10-03 consult's #1400 k1 treats the verdict as blocking promotion and demoting, which is (a)", unblocks: "CD.46's precondition clause; the ladder-to-register staged edge", covers: ["#1400 q3"], ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 9, id: F8, group: whole_loop, binds_at: build, source: "F8", class: asked, question: "Record home for six items' run, verdict, transition, canary, egress and budget records", options: "(a) one append-only loop journal in the data plane on the Decision 199 model, one table per record kind; (b) one table per item, each owner chooses; (c) a new Class C contract", design: "(a)", consult: "stays at the W1 level (#1396 q4: D199 journal with the producer tag excluded from detectors, medium-low)", unblocks: "CD.47; the record schemas of waves 3 and 4", covers: ["#1396 q4", "#1396 q2", "#1397 q4", "#1398 q2", "#1399 q3", "#1400 q1", "#1401 k2 (record half)"], ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 10, id: F9, group: whole_loop, binds_at: read_all_run, source: "F9", class: asked, question: "The read_all pin until capture q1", options: "(a) accept (nothing promotes until q1; with F1 (a) and F4 (a) wave 2's read_all period waits for q1 or an envelope of about 60 reviews a day); (b) exempt capture's counter_dark from propagation while every component is at read_all; (c) seed the denominator from the SessionStart hook invocation count; (d) verdict-ok precondition at the top rung only, so components reach sampled before q1 (X22)", design: "(a) now, (c) as the staged candidate answer to q1; (d) named, not chosen; (b) hides the forcing function", consult: "n/a as a row; #1400 k1's verdict names 'accept a frozen ladder until capture q1 lands' as the operator's context", unblocks: "with row 1, every promotion and wave 2's read_all period", ruling: "operator ruling 2026-10-04 q1: (a) now, (c) as capture q1's answer (the SessionStart hook invocation count); (b) and (d) not taken"}
  - {rank: 11, id: F10, group: whole_loop, binds_at: build, source: "F10", class: asked, question: "Merge order equals build order", options: "(a) merge the ten PRs in wave order so item edges resolve at merge time; (b) PR-number order with item edges in one later PR; (c) any order, W3 authors the edges", design: "(a)", consult: "n/a", unblocks: "the first merge; each later merge needs a merge from main and a re-render either way", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 12, id: F11, group: whole_loop, binds_at: build, source: "F11", class: asked, question: "Register rows for the budget and the register itself (X19)", options: "(a) add both rows at the register's build; (b) exempt the observers from the verdict-ok precondition; (c) the observers never leave read_all", design: "(a)", consult: "n/a", unblocks: "the two observers' promotion under CD.46", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 13, id: F12, group: whole_loop, binds_at: build, source: "F12", class: asked, question: "Batch shape between capture and the writer (X2)", options: "(a) the T2.36 writer owner fixes the request shape and capture adapts; (b) capture's per-table data-first batches are fixed in slice 3b and the writer accepts them; (c) 2a-2's one transaction per request with sessions last binds capture", design: "(a); Decision 207 no-op resends make either order safe to replay", consult: "n/a", unblocks: "wave 1 capture's writer call; rides call-1 (P3)", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 14, id: F13-F14, group: whole_loop, binds_at: build, source: "F13", class: asked, question: "Route of the budget's breach recommendation (X18) and, if routed through the filer, the filer's fourth edge (X21): one question", options: "F13 (a) #1401 k1 (a) as written, one rec per breaching line through the cost reconciliation's rec path; (b) the same breach routed through the filer as a detector source; (c) #1401 k1 (b), the owning component degrades its cadence, no rec. F14 (a) choose F13 (a) or (c); (b) carry the budget as a filer source in the register's upstream list and the ladder seeds only; (c) drop the filer's part_of T3.3; (d) raise MAX_EDGES_FROM_ITEM (W0 code, never chosen)", design: "F13 (b) only together with an F14 answer, else (a); no F14 recommendation", consult: "DIFFERS: the c10 consult prefers F13 (a) (file through the existing cost-reconciliation path, at projected_breach, after a shadow month); the synthesis prefers (b) so the loop keeps one rec writer; both positions were CD.49's options; since q2 the route is CD.50's BREACH clause and CD.49 is retired", unblocks: "CD.50's breach clause (CD.49 retired by the q2 ruling); the filer's staged edges stay as the synthesis lists them (no fourth edge under F14 (a))", covers: ["F14", "#1401 k1 (route of (a))"], ruling: "operator ruling 2026-10-04 q2: F13 (a), file through the existing cost-reconciliation rec path at projected_breach, one rec per line per month, after a shadow month; F14 (a) follows (no filer edge); CD.49 folds into CD.50's alarm-not-gate clause with no separate Decision number"}
  - {rank: 15, id: call-1, group: ownership, binds_at: build, source: "operator call 1 (P3, X15)", class: asked, question: "Who builds the capture hooks: this stream's #1384 or the Telemetry project's slice 3b?", options: "#1384's design is the input and the Telemetry project builds; this stream builds from #1384; split by file", design: "the coordinator's recommendation of 2026-10-04 (posted about 08:20Z): #1384 is design input, the Telemetry project builds", consult: "n/a", unblocks: "wave 1 capture build (P3); the owner also settles F12", ruling: "operator ruling 2026-10-04 q3.1: the Telemetry project builds slice 3b; #1384 is design input"}
  - {rank: 16, id: call-2, group: ownership, binds_at: build, source: "operator call 2 (P4, X15)", class: asked, question: "Who builds the reader verbs and the ducklake_scd2_schema.py Decision 128 split: #1390 or the Telemetry project's 2b?", options: "the Telemetry project owns both; this stream builds #1390's verbs on the Telemetry project's split; split by verb", design: "the coordinator's recommendation of 2026-10-04: the Telemetry project owns", consult: "n/a", unblocks: "wave 1 reader build (P4); every reader verb the later waves read", ruling: "operator ruling 2026-10-04 q3.2: the Telemetry project owns the verbs and the ducklake_scd2_schema.py split; #1390 is design input"}
  - {rank: 17, id: call-3, group: ownership, binds_at: build, source: "operator call 3 (X20; verification r2 B5)", class: asked, question: "Do friction's and capture's producer changes (#1394 k3 (a), R3, R4, and k2 (b) if chosen; capture's slice-3b changes) land after slice 2a-1 (plan #1403 at 9baecbf3 and implementation #1405 at 2553d69a, both merged to main on 2026-10-04 after this report's pin; parser_version is now 2, so a combined bump would be 3) as one combined PARSER_VERSION bump?", options: "one combined bump after 2a-1 (the producer owners' sequencing recommendation); separate bumps; before 2a-1 (moot since #1405 merged)", design: "the coordinator's recommendation of 2026-10-04: one bump after 2a-1", consult: "n/a; the 2026-10-03 consult records the producer owners' sequencing as a fact, not an operator answer", unblocks: "wave 2's pairing of friction and deliberation (P2); the wave placement holds either way", ruling: "operator ruling 2026-10-04 q3.3: one combined PARSER_VERSION bump (to 3) after 2a-1; friction and deliberation ship together in wave 2"}
  - {rank: 18, id: capture-k1, group: wave_1, binds_at: build, source: "#1384 k1", class: asked, question: "Synchronous Stop-hook pass or a detached pass", options: "(a) synchronous with a wall-clock budget, defer to the next pass when exceeded; (b) detached; (c) a warm pass that costs only the new lines", design: "(a)", consult: "refined: keep synchronous, but the budget is a soft service-level objective plus a high hard ceiling (a hard budget dumps the backlog onto SessionEnd's 60 s cap); (c) is about a 2x win and waits for profiling; the operator's real session lengths and latency tolerance decide", unblocks: "wave 1 capture build", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option; the consult's refinements kept as named build refinements"}
  - {rank: 19, id: capture-k2, group: wave_1, binds_at: build, source: "#1384 k2", class: asked, question: "Cursor-loss pin recovery on ephemeral CC-web containers", options: "(a) amend project-id.yaml:47-51 (always-ask); (b) the SessionStart hook writes the project_ref pin into the transcript (parser rule plus PARSER_VERSION bump, earliest attachment from the pin hook's declared command); (c) defer", design: "(b)", consult: "stays; (c) as the fallback; spike the transcript shape first; whether CC-web restores transcripts whole is the operator's fact", unblocks: "wave 1 capture build on CC-web; rides call-3's bump", ruling: "operator-accepted 2026-10-04 (pass 1): (b); the consult's spike and (c) fallback carried for the build (a stays row, so not kept by pass 1); (a), the project-id.yaml amendment, stays always-ask and unanswered"}
  - {rank: 20, id: capture-P1, group: wave_1, binds_at: build, source: "#1384 P1", class: asked, question: "Owner of the gate and pre-commit signatures on rec-4026's handed-on list", options: "the read-side friction classifier (rec-4032) over tool_result rows; producer-side structured markers in slice 3b (#1394 k3 (a))", design: "#1384: read-side; the synthesis (X1) resolves producer-side, narrowed to structured markers, riding call-3's bump", consult: "stays on #1384's own row (read-side, high), but the #1394 k3 consult sides with producer-side markers and the alignment consult records the synthesis resolving it the same way; it works only if the repo's gates become self-describing, which is the operator's call (open question 6)", unblocks: "slice 3b's scope; #1394 k3", covers: ["#1394 k3 (the same question from the classifier's side)"], ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 21, id: reader-k1, group: wave_1, binds_at: build, source: "#1390 k1", class: asked, question: "Session state and duration rule (a ratified Class A semantic change)", options: "(a) roots: state from the latest lifecycle row ordered by event_timestamp, source_ordinal, close ranked last on a tie, then event_id; duration = latest close minus session_started_at, wall-clock; sub-agents from their agent-run pair; (b) forbid resume after close; (d) child own start and close rows (contract amendment plus parser bump)", design: "(a)", consult: "stays (tie-break as lifecycle kind-rank; wall-clock duration; high)", unblocks: "wave 1 reader build; #1384's failure_signal depends on this amendment (its M10); the telemetry_sessions amendment in section 3's non-CD list", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 22, id: reader-k2, group: wave_1, binds_at: build, source: "#1390 k2", class: asked, question: "Window verbs (sessions_window) against Decision 199 clause 1 and T2.52", options: "(a) build sessions_window in the reader (one derived row per root session, at most 7 days, paginable), shaped to T2.52 c3; (b) build it in T2.52 c3 after reactivation (a tier-item status change)", design: "(a), with one stated clause 1 reading", consult: "stays (build here; record the reading as a Decision 199 update note; 'lands' = T2.36 c2; medium-high); the operator's reading of 'lands' decides the T2.52 half (open question 7)", unblocks: "capture's failure_signal source, the filer's recurrence read and back-validation's windows; CD.52", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 23, id: reader-k3, group: wave_1, binds_at: build, source: "#1390 k3", class: asked, question: "Abandoned state (a ratified Class A semantic change)", options: "(a) a derived abandoned state, as_of minus last event_timestamp above a governed idle threshold seeded at 24 h, a resume moves it back to running; roots by lifecycle rows, sub-agents by their agent-run pair", design: "(a)", consult: "stays on the shape (high), low on the number; last event across all tables; the operator's tolerance for false-abandoned noise decides the threshold", unblocks: "wave 1 reader build; capture's unfinalized_session_share is the abandoned share under (a)", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 24, id: capture-N1, group: wave_1, binds_at: text, source: "#1384 N1", class: asked, question: "Whether to add the capture report's note to rec-4026 (a rec write)", options: "yes, a short pointer note; no", design: "the operator's call (Decision 67 caution)", consult: "stays: yes, short pointer note, an operator write (high); only his rec-note convention changes it", unblocks: "nothing in the build; rec-4026's slice 3b owner's context", ruling: "operator-accepted 2026-10-04 (pass 1): the design's position, the note stays the operator's own rec write; no thread writes it"}
  - {rank: 25, id: capture-q3, group: wave_1, binds_at: text, source: "#1384 q3", class: asked, question: "billing_shape for API-key sessions (rec-4147): the default fixed_non_rollover_allowance mislabels them", options: "a per-session billing_shape from the credential kind; keep the default and label at read", design: "open; named for rec-4147's owner", consult: "n/a", unblocks: "the cost lines' token pricing for API-key sessions (CD.50)", ruling: "pass 1 (2026-10-04) carries no design option to accept here; stays open for its owner"}
  - {rank: 26, id: capture-q2, group: wave_1, binds_at: measurement, source: "#1384 q2", class: asked, question: "Does SessionEnd fire on CC-web reclaim, is a resumed session's transcript restored whole, and does a SessionEnd pass fit the 60 s cap?", options: "measure on CC-web; assume not and size R1 accordingly", design: "measure; R1's size hinges on it", consult: "n/a as a row; the #1384 k1 consult names CC-web transcript restoration as the operator's fact", unblocks: "capture k1's budget and the section 4 blind spot of #1384", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 27, id: reader-q1, group: wave_1, binds_at: measurement, source: "#1390 q1", class: asked, question: "Which consumer's latency bound tests Decision 199's derived-read-too-costly falsifier, and what are the verbs' p95 latency and per-call catalog egress on the production Neon catalog?", options: "measure on the live catalog (needs credentials); defer until T2.36 lands", design: "unmeasured; the first measurement is the calibration constant for the budget's catalog egress proxy (#1401 k4)", consult: "n/a", unblocks: "#1401 k4's calibration; Decision 88 clause 2's reading (P6)", ruling: "pass 1 (2026-10-04) carries no design option to accept here (the design says unmeasured and picks neither route); the measurement route, live catalog with credentials or after T2.36 lands, stays the operator's"}
  - {rank: 28, id: reader-q2, group: wave_1, binds_at: measurement, source: "#1390 q2", class: asked, question: "Is rec-4025's derived-layer harness the right shadow re-derivation source for the reader's failure_signal, or should the reader own a production raw-rows path? A yes widens rec-4025 (a queue change, Decision 67)", options: "widen rec-4025; a reader-owned raw-rows path; leave the signal partial (dedupe leg only)", design: "open; until resolved the signal is partial", consult: "n/a", unblocks: "the reader's own promotion (its failure_signal covers the dedupe leg only until then)", ruling: "pass 1 (2026-10-04) carries no design option to accept here; stays open for its owner"}
  - {rank: 29, id: friction-k1, group: wave_2, binds_at: build, source: "#1394 k1", class: asked, question: "Where the read-time friction rules live (telemetry_sessions.yaml:232 and :243 point at a rec-4032 labels table whose contract does not exist)", options: "(a) a governed, versioned rules file bundled into the reader's named-read registry as data, classifier_version stamped in every response; (b) a DuckLake rules table; (c) inline CASE arms", design: "(a); the contract pointer wording needs a dated amendment either way", consult: "stays (high)", unblocks: "wave 2 friction build; the contract pointer amendment in section 3's non-CD list", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 30, id: friction-k2, group: wave_2, binds_at: build, source: "#1394 k2", class: asked, question: "The derivation formula: the ratified rework_total and exception_total count process_event rows by name, so hook passes count and the slice-3a deviation (tool errors as tool_call close outcomes) disagrees with the operator's 2026-09-24 ruling on 11 of 17 vectors", options: "(a) ratify the deviation: amend both derivations (facts are non-info process_events plus error or blocked tool_call closes; inputs add severity, outcome, parent_observation_id, event_timestamp, source_ordinal); (b) bring the producer back to the ruling (a tool:<name> process_event per tool error or block, PARSER_VERSION bump) and add severity for the pass half", design: "(a); the producer owners confirmed the deviation is deliberate on 2026-10-04 (X17)", consult: "stays (medium-high); whether the 2026-09-24 wording was intent or description is the operator's (open question 5)", unblocks: "wave 2 friction build; the telemetry_sessions derivation amendment in section 3's non-CD list", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 31, id: friction-k3, group: wave_2, binds_at: build, source: "#1394 k3", class: asked, question: "Content-bearing signatures: gate and pre-commit failures, suppression edits, file re-reads", options: "(a) producer-side signatures in rec-4026 slice 3b (gate:<check>, precommit:<hook>, edit:suppression_added), classified at read by rules; re-reads and self-contradiction left until rec-4032's LLM trigger; (b) read-side regex over tool_result rows (#1384 P1)", design: "(a); the synthesis X1 resolves producer-side, narrowed to structured markers, riding call-3", consult: "refined: producer-side, narrowed to structured markers the repo's own gates emit; suppression and re-reads deferred (medium); needs self-describing gates (open question 6)", unblocks: "slice 3b's scope; row 20", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option; the consult's refinements kept as named build refinements"}
  - {rank: 32, id: friction-q1, group: wave_2, binds_at: build, source: "#1394 q1", class: asked, question: "Is an interrupt (a human stop) a friction?", options: "count it; keep it out until the producer distinguishes a human stop from a synthetic close in outcome or a typed metadata flag", design: "keep it out until the producer distinguishes them", consult: "n/a as a row; the consult's note agrees (counting both inflates friction with every unanswered tool_use)", unblocks: "the classifier's rule set at build", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 33, id: delib-k1, group: wave_2, binds_at: build, source: "#1395 k1", class: asked, question: "Claude thinking text: counts only, or showThinkingSummaries for every session, or only for sessions an RCA will read", options: "(a) counts only; (b) showThinkingSummaries: true in .claude/settings.json for every session; (c) only for RCA sessions", design: "(a) until an RCA consumer names a need", consult: "stays (high); measure (b) on one headless session before enabling; whether an RCA consumer will read reasoning text this quarter is the operator's (open question 9)", unblocks: "what every session stores; T3.4's RCA inputs", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 34, id: delib-k2, group: wave_2, binds_at: build, source: "#1395 k2", class: asked, question: "LiteLLM-lane durability of DeepSeek chain of thought (exists only in the live response and the persona's history)", options: "(a) replay from the persona's durable checkpoint (env R7); (b) non-replayable, each lost write counted in diagnostics", design: "(b) now; (a) as a T4.2 design input once the checkpoint exists", consult: "stays (high); loss counted with a named counter contract", unblocks: "rec-4028 and T4.2's producer class; moot while the executor is frozen (Decision 67)", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 35, id: delib-k3, group: wave_2, binds_at: build, source: "#1395 k3", class: asked, question: "Who reads chain-of-thought text", options: "(a) metrics only, text kept in the data plane for forensic reads; (b) a per-call text verb (egress per read, Decision 88); (c) LLM labels over CoT (spends)", design: "(a)", consult: "refined (high): metrics only, with forensic reads by session under an access log and a retention window; any transcript retention promise is the operator's (open question 8)", unblocks: "the deliberation verb's columns; the allow-list's exclusion of text", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option; the consult's refinements kept as named build refinements"}
  - {rank: 36, id: delib-k4, group: wave_2, binds_at: build, source: "#1395 k4", class: asked, question: "Read-side arms for the three writer rules other than NULL visibility", options: "(a) writer only, the build gate waits for all four rules (taken in the PR); (b) also add read arms (visibility_unknown, none_with_reasoning) as drift, one vector each", design: "(a) now; (b) a cheap add at build if the read should stand without the writer", consult: "DIFFERS (medium): add the two cheap arms now plus a third (tokens exceeding output)", unblocks: "the deliberation verb's SQL at build", ruling: "operator ruling 2026-10-04 q4: the consult's position; add the visibility_unknown, none_with_reasoning and tokens-exceed-output arms at build, one vector each"}
  - {rank: 37, id: delib-q4, group: wave_2, binds_at: build, source: "#1395 q4", class: asked, question: "Where is the multi-row roll-up's shape declared (per producer, parser_version, model, visibility rows have no Class A home)?", options: "a response-shape entry in the reader registry; a declared verb-response contract", design: "open; for the verb's build plan", consult: "n/a verdict: a declared verb-response contract in the reader registry (medium)", unblocks: "the deliberation verb's build plan", ruling: "pass 1 (2026-10-04) carries no design option to accept here; stays open for its owner"}
  - {rank: 38, id: friction-q2, group: wave_2, binds_at: read_all_run, source: "#1394 q2", class: asked, question: "Who supplies precision (unmapped_failure_share measures coverage, not correctness; a label review needs a named reviewer)?", options: "the operator reviews a label sheet at read_all; another named reviewer; none (coverage only)", design: "a human label review at read_all; no other component supplies it", consult: "n/a as a row; the consult's note: a label review sheet is the cheapest read_all mechanism and needs a named reviewer (the operator)", unblocks: "friction's read_all run and every promotion from it (review capacity, open question 2)", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 39, id: friction-q3, group: wave_2, binds_at: measurement, source: "#1394 q3", class: asked, question: "Repeat key and threshold (grouping by tool name alone merges unrelated failures; the repeat threshold of 3 is inherited and unmeasured)", options: "the input_key hash as the repeat key with the threshold stamped into the rule set; measure before choosing", design: "the input_key hash; threshold 3 provisional", consult: "n/a as a row; the consult's note: stamp the threshold with the rule set so a change is a classifier_version event (R5)", unblocks: "the classifier's seeds (re-seeded at the first measured window)", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 40, id: filer-k1, group: wave_3, binds_at: build, source: "#1396 k1", class: asked, question: "Cross-source dedupe: how a finding learns that another source's open rec already covers it", options: "(a) a typed covered_by: rec-N in the filer rec's context_v2_json, set by the operator at triage, followed one hop (needs a filer lifecycle contract); (b) title Jaccard (false duplicate at 0.82, miss at 0.00 measured); (c) an LLM judge (spends, breaks Decision 55); (d) none at filing, backlog_health afterwards", design: "(a)", consult: "refined (high): typed covered_by, with similarity as a non-deciding suggestion", unblocks: "wave 3 filer build; the filer lifecycle contract", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option; the consult's refinements kept as named build refinements"}
  - {rank: 41, id: filer-k5, group: wave_3, binds_at: build, source: "#1396 k5", class: asked, question: "Blast radius of a malformed finding", options: "(a) quarantine the offending detector's findings whole, the rest decide, a NULL detector_id aborts; (b) abort the whole run on any malformed finding", design: "(a)", consult: "stays (high); add a chronic-quarantine alarm", unblocks: "wave 3 filer build", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 42, id: filer-k6, group: wave_3, binds_at: build, source: "#1396 k6", class: asked, question: "Chain length and chronic findings", options: "(a) from the third record in a chain still file the regression but tag it chronic and forbid a proof-only close of a chronic rec; (b) ci_rca's stop-at-3 and quarantine; (c) no cap", design: "(a)", consult: "refined (medium): chronic tag as close_proposed-only; bump the head rather than re-file", unblocks: "wave 3 filer build; #1397 k5 (chronic_from is this fork's parameter)", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option; the consult's refinements kept as named build refinements"}
  - {rank: 43, id: register-k1-i, group: wave_3, binds_at: build, source: "#1400 k1 (i)", class: asked, question: "Propagation strength of a non-ok upstream verdict", options: "any non-ok upstream, diverging included, propagates upstream_unsound at full strength (staged); propagate only the strong verdicts; carry a weaker grade", design: "as staged until k1 is answered", consult: "stays (medium): keep full strength; record the originating upstream and its verdict", unblocks: "the register's SQL at build", ruling: "operator ruling 2026-10-04 q15: as staged with the consult refinement (record the originating upstream and its verdict)"}
  - {rank: 44, id: register-k1-ii, group: wave_3, binds_at: build, source: "#1400 k1 (ii)", class: asked, question: "Verdict precedence (which single verdict a detector with several faults reports)", options: "breach before counter, counter_low before undrilled, own verdict before upstream (staged); another order", design: "as staged until k1 is answered", consult: "refined (medium-high): keep the staged order minus breach; add a faults list column (CD.47)", unblocks: "the register's SQL at build", ruling: "operator ruling 2026-10-04 q15: as staged with the consult refinement (the faults list, CD.47)"}
  - {rank: 45, id: register-k1-iii, group: wave_3, binds_at: build, source: "#1400 k1 (iii)", class: asked, question: "What the ladder does with an unregistered row", options: "ignore dated drill- rows and block every promotion while any other unregistered row exists, so a retired detector freezes promotion for 27 days (staged); a register defect for the operator that blocks nothing", design: "as staged until k1 is answered", consult: "refined, conditional (high/medium): block on a genuinely unregistered series, conditional on effective dating (row 48)", unblocks: "the register's SQL at build", ruling: "operator ruling 2026-10-04 q15: as staged with the consult refinement (block only on a genuinely unregistered series, conditional on q8's effective dating)"}
  - {rank: 46, id: register-k1-iv, group: wave_3, binds_at: build, source: "#1400 k1 (iv)", class: asked, question: "Counter staleness", options: "the latest defined value anywhere in the 28-day window decides (staged; fails open: ok for 27 days on a last value); a freshness bound (counter_dark when no counter arrived within a per-detector cadence)", design: "as staged until k1 is answered", consult: "DIFFERS (medium-high): fail closed, counter_dark when nothing arrived within a per-detector cadence (default 2 days); the operator's tolerance for counter_dark on quiet days decides", unblocks: "the register's SQL at build", ruling: "operator ruling 2026-10-04 q6: the consult's position; fail closed, counter_dark when nothing arrived within a per-detector cadence (default 2 days), paired with the ladder's demote-once-then-hold"}
  - {rank: 47, id: register-k1-v, group: wave_3, binds_at: build, source: "#1400 k1 (v)", class: asked, question: "The breach leg (thresholds borrowed from eight unmerged items' top-rung triggers; one capture reading of 0.06 makes 7 of 8 detectors unsound)", options: "(a) a register-owned threshold the operator sets per row; (b) no breach leg, the register reads soundness only; (c) keep the leg, do not propagate breach", design: "(b)", consult: "stays (high): drop the leg", unblocks: "the register's SQL at build; CD.46's register clause; X8", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 48, id: register-k2, group: wave_3, binds_at: build, source: "#1400 k2", class: asked, question: "How drills run, and whether known-wrong outputs may be mixed into the operator's own review sample", options: "(a) live: seeded rows through the real producers under a drill project_id; (b) replay: each detector's SQL offline over seeded rows; (c) operator-labelled samples only", design: "(b) for the six SQL detectors, (a) for capture and the canary; (a) needs a drill marker no contract has (O3)", consult: "stays (medium): replay for SQL detectors, live for capture and canary; the reviewer drill at a low rate with the operator's consent (he is the reviewer)", unblocks: "the register's drill log; CD.46's drill clause", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 49, id: register-k2-liveness, group: wave_3, binds_at: build, source: "#1400 k2 (drill liveness)", class: asked, question: "How the register's own drill stays live and what history the run reads", options: "every read covers the 28-day window only; one drill series a day named drill-YYYY-MM-DD that passes only if that day's name reads unregistered (staged); a today-only unregistered read", design: "as staged", consult: "stays (high), with the exact dated name", unblocks: "the register's own row (F11)", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 50, id: register-k2-retirement, group: wave_3, binds_at: build, source: "#1400 k2 (retirement and onboarding)", class: asked, question: "Retirement and onboarding of a detector", options: "in-window rows of a detector with no register row read unregistered, so a retired detector reads unregistered for 27 days (staged); a retired_on marker; a halt with a 28-day procedure", design: "as staged", consult: "DIFFERS (high): a registered_from / retired_on validity interval, effective-dated, not a 27-day unregistered read", unblocks: "the register's SQL at build", ruling: "operator ruling 2026-10-04 q8: the consult's position; a registered_from / retired_on effective-dated validity interval"}
  - {rank: 51, id: register-k2-malformed, group: wave_3, binds_at: build, source: "#1400 k2 (malformed-input blast radius)", class: asked, question: "Malformed-input blast radius", options: "any guard firing raises the whole run for up to 27 days (staged); (a) a per-detector malformed verdict for signal and drill defects, whole-run raise for register defects only; (b) quarantine the row; (c) keep the halt with a repair procedure", design: "as staged", consult: "DIFFERS (high): (a) per-detector malformed for signal and drill defects, the whole-run halt for register defects only, plus a repair procedure", unblocks: "the register's SQL at build; CD.47's faults vocabulary carries malformed", ruling: "operator ruling 2026-10-04 q9: the consult's position; per-detector malformed verdict for signal and drill defects, whole-run halt for register defects only, a written repair procedure"}
  - {rank: 52, id: register-k3, group: wave_3, binds_at: build, source: "#1400 k3", class: asked, question: "Where counters and drills are declared", options: "(a) the register's rows (staged); (b) two new FailureSignal fields the evaluator requires on every item (a pilot-schema change, O1); (c) each item's prose", design: "(a) now, (b) at lift", consult: "differs at lift (medium-high): (a) now; at lift a required register_row reference with an evaluator check, not inline counter and drill fields", unblocks: "the register's build; the lift's evaluator change", ruling: "operator ruling 2026-10-04 q10: (a) now as designed; at lift the consult's position, a required register_row reference with an evaluator check, not inline counter and drill fields (binds at lift only)"}
  - {rank: 53, id: budget-k1-precedence, group: wave_3, binds_at: build, source: "#1401 k1 (precedence and windows)", class: asked, question: "Choices inside the budget verdict's SQL (precedence unregistered > breach > dark > projected_breach > unattributed > warn > ok; dark_flag and unattributed_flag beside the label; 31-day window with a NULL day raising; 7-day rate window; 9-place comparisons; blank names raise; orphan attribution on an event line ignored; an unregistered line's unattributed_flag reads its parts; a zero envelope legal)", options: "as staged; keep one label and re-source the signal from the ledger directly; read a missing event-line row as 0; gate the flag on registration", design: "as staged until k1 is answered", consult: "refined (medium-high): as staged; a faults list beside the two flags at build (CD.47); orphan attribution as a counted data-quality warning", unblocks: "the budget's SQL at build", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option; the consult's refinements kept as named build refinements"}
  - {rank: 54, id: budget-k1-projection, group: wave_3, binds_at: build, source: "#1401 k1 (event-line projection)", class: asked, question: "Event-line projection (calendar-day mean for event lines, reporting-day mean for daily lines)", options: "as staged; one mean for both cadences; no projection for event lines", design: "as staged", consult: "stays (high); one sentence that the 7-day window reaches into the previous month early in a month", unblocks: "the budget's SQL at build", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 55, id: budget-k2, group: wave_3, binds_at: build, source: "#1401 k2", class: asked, question: "Where the budget register lives", options: "(a) a loop_budget block in config/agent/cost_reconciliation.yaml (a loader schema change); (b) a new Class C contract with the ledger and attribution tables as Class A entries; (c) the pilot item's register rows only", design: "(a) plus (c); (b) once the ledger is a warehouse table", consult: "refined (medium-high): (a) plus (c); the ledger and attribution schemas go to contracts the day the tables exist (T2.36), the other half of the same build; version the register and stamp it on every verdict; whether cost_reconciliation.yaml stays the monthly monitor's file alone is the operator's", unblocks: "CD.50's home clause (the T3.3 c2 threshold's home is the filer's rule set with its Goodhart register row as monitor, not the budget register)", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option; the consult's refinements kept as named build refinements"}
  - {rank: 56, id: budget-k3, group: wave_3, binds_at: build, source: "#1401 k3", class: asked, question: "The unit the budget is kept in", options: "(a) physical units per line, priced at read; (b) invoice dollars per line from the monthly snapshot; (c) shares of the bill, as the five existing triggers use", design: "(a), with (c) as the monthly reconciliation leg", consult: "refined (high): (a) with (c); the price table effective-dated (SCD2), never re-pricing a closed month", unblocks: "CD.50's unit clause; the price table (rec-4031)", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option; the consult's refinements kept as named build refinements"}
  - {rank: 57, id: budget-k4, group: wave_3, binds_at: build, source: "#1401 k4", class: asked, question: "How catalog egress is measured", options: "(a) a proxy: catalog_stats bytes per read times reads per day (Decision 88 clause 2's path), bracketed 17x apart by the model's two cases; (b) the Neon consumption API (a new credential in the data plane, always-ask, never chosen here); (c) the monthly Neon invoice, a month late", design: "(a) now, calibrated once against (c); (b) only if they disagree beyond tolerance", consult: "stays (medium); (b) named as the best-practice lift target once a read-only Neon key is approved, never autonomously; the plan's included egress is the real envelope (the operator's)", unblocks: "the budget's first line (Decision 88); P6; rows 27 and 61", ruling: "operator-accepted 2026-10-04 (pass 1): (a) calibrated against (c); (b), a read-only Neon key, stays always-ask and unanswered"}
  - {rank: 58, id: budget-k5, group: wave_3, binds_at: build, source: "#1401 k5", class: asked, question: "How shared lines are attributed to components", options: "(a) per-call stamps (Decision 199's producer field; a caller tag on the Lambda log line), which changes writer and reader Lambda code shipped through the governed code-deploy channel (always-ask step); (b) proportional split by request share; (c) unattributed by design for Lambda lines", design: "(a)", consult: "refined (high): (a), the stamp mandatory at the write boundary with a reserved canonical component unknown; the governed deploy named, not recommended autonomously", unblocks: "CD.50's attribution clause; the two largest priced lines' owners", ruling: "operator-accepted 2026-10-04 (pass 1): (a) as the attribution design, with the consult's mandatory stamp and reserved unknown component kept as named build refinements; per-call stamps do not exist until the operator answers the governed Lambda deploy (always-ask, unanswered), so the shared lines stay unattributed (unattributed_flag) until then"}
  - {rank: 59, id: budget-k7, group: wave_3, binds_at: build, source: "#1401 k7", class: asked, question: "How a stock line (s3_storage_bytes) is read", options: "(a) latest reading compared with the envelope, nothing projected; (b) month mean (GB-month billing basis); (c) latest plus a 7-day growth trend times days remaining; (d) redefine as a flow of bytes added a day", design: "(a) for the pilot; (c) once a month of readings exists", consult: "refined (medium-high): (a) for the label; (c) as a derived days-to-envelope read after seven readings; (b) in the monthly leg only; whether GC is licensed inside the retention window is the operator's", unblocks: "the budget's storage line", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option; the consult's refinements kept as named build refinements"}
  - {rank: 60, id: budget-k8, group: wave_3, binds_at: build, source: "#1401 k8", class: asked, question: "Which meter feeds the two S3 lines", options: "s3_requests: (a) self-counted PUTs and GETs on the Lambda log line; (b) S3 request metrics in Terraform, billed (always-ask); (c) server access logs (always-ask); (d) the invoice line. s3_storage_bytes: (a) data_file_size_bytes over ducklake_list_files plus the blob prefix; (b) Inventory or Storage Lens (always-ask); (c) LIST per request (always-ask); (d) bucket-wide BucketSizeBytes as a ceiling", design: "(a) for both, the invoice as monthly calibration and (d) as the storage ceiling; a residual above tolerance revisits k8, never adds a billed metric silently", consult: "refined (medium-high): (a) for both with an explicit uncounted residual each month; the clean lift is a dedicated telemetry bucket (Terraform, never-list, named for the operator)", unblocks: "the budget's two S3 lines", ruling: "operator-accepted 2026-10-04 (pass 1): (a) for both lines with (d) as the storage ceiling, the consult's monthly invoice calibration and explicit uncounted residual kept as named build refinements; S3 request metrics, access logs, Inventory, Storage Lens and the dedicated telemetry bucket stay always-ask and unanswered"}
  - {rank: 61, id: budget-k9, group: wave_3, binds_at: build, source: "#1401 k9", class: asked, question: "The line roster", options: "(a) the ten lines of #1401 section 2.2; (b) fewer, only lines with a cap or a price; (c) more, one per table or component; (d) the invoice's own line items", design: "(a)", consult: "refined (medium-high): (a), typed budget, diagnostic or tripwire; the ladder owns the review_items envelope and the budget reads it (row 5, F4)", unblocks: "CD.50's roster", ruling: "operator ruling 2026-10-04 q12: ten lines typed budget, diagnostic or tripwire; review_items typed diagnostic and owned by the ladder per q7"}
  - {rank: 62, id: budget-k1, group: wave_3, binds_at: read_all_run, source: "#1401 k1", class: asked, question: "What a breach does", options: "(a) alarm: the daily verdict files one rec per breaching line through the cost reconciliation's rec path; (b) degrade: the owning component lowers its cadence or window; (c) stop: the breaching line's schedule is disabled", design: "(a) now, (b) at lift for single-owner lines, (c) never unattended", consult: "refined (medium-high): (a), filed at projected_breach and updated at breach, deduped per line per month; the first measured month in shadow mode; (b) at lift as pre-declared degraded modes with hysteresis; (c) a human-pulled kill switch on the consumer, never the meter; whether the Neon plan has a hard cap or bills overage is the operator's", unblocks: "the budget's first verdict run; the route of (a) is row 14 (F13)", ruling: "operator ruling 2026-10-04 q13: (a) plus the three refinements (alarm at projected_breach; first measured month in shadow mode; (c) kept as a human-pulled kill switch on the consumer); whether an RDS swap makes this moot was answered no, the swap itself unruled"}
  - {rank: 63, id: filer-k2, group: wave_3, binds_at: promotion, source: "#1396 k2", class: asked, question: "What a declined filer rec means when the finding recurs", options: "(a) suppressed until the operator acts, the run record still counting the sessions; (b) a snooze, re-file after N days; (c) re-file on k-fold growth since the decline", design: "(a), with the suppressed-sessions trend fed to the ladder controller", consult: "refined (medium): suppressed now, with a written reversal to escalation once baselines exist; a decline carries a reason; what his decline means is partly the operator's", unblocks: "the filer's sampled rung (read_all files nothing)", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option; the consult's refinements kept as named build refinements"}
  - {rank: 64, id: filer-k3, group: wave_3, binds_at: promotion, source: "#1396 k3", class: asked, question: "Filing while the queue is over its soft cap (1244 of 1419 open recs non-automatable against a cap of 250, measured 2026-10-03)", options: "(a) per-run budget only (file_budget 3); (b) no new file or regression while over the cap; (c) file with automatable false at Low priority and let /orient rank", design: "(a) from the sampled rung on", consult: "refined (medium): (a) plus a per-source open cap (new option (d)); never (b); the operator's queue tolerance decides", unblocks: "the read_all -> sampled transition of the filer", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option; the consult's refinements kept as named build refinements"}
  - {rank: 65, id: register-k1, group: wave_3, binds_at: promotion, source: "#1400 k1", class: asked, question: "What a non-ok register verdict does on the maturity ladder", options: "(a) block promotion and demote one rung; (b) block promotion only; (c) advisory, logged beside the transition record", design: "(a) for dark, blind, undrilled, counter_dark, counter_low and unregistered; (b) for diverging and upstream_unsound; breach dropped (k1 (v))", consult: "stays (medium-high); sequence capture q1 first; whether to accept a frozen ladder until capture q1 lands is the operator's", unblocks: "CD.46's demotion clause (verdict_not_ok); X7", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 66, id: register-k4, group: wave_3, binds_at: promotion, source: "#1400 k4", class: asked, question: "Harm weights across detectors (#1398 R6)", options: "(a) uniform; (b) operator-set per detector, read by the ladder's AOQL target; (c) derived from what the detector gates (egress or proof close above filing above label)", design: "(c) as the starting order, set by the operator", consult: "refined (medium): (c) as three ordinal tiers, operator-set, never multiplied; his tolerance per escape class decides", unblocks: "the ladder's AOQL target per component (CD.46 seeds)", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option; the consult's refinements kept as named build refinements"}
  - {rank: 67, id: filer-k4, group: wave_3, binds_at: close, source: "#1396 k4", class: asked, question: "Whether the filer may close its own recs", options: "(a) never: a human or back-validation's proof closes; (b) ci_rca's deterministic inactivity close adapted with a denominator (stale_no_recurrence after N below-floor windows each observing at least M sessions, rec at least 14 days old); (c) close_proposed only", design: "(a) until back-validation exists, then (b) as its fallback", consult: "DIFFERS (medium): self-close arrives by maturity rung, (c) close_proposed at read_all and sampled, (b) denominator close from spot_check, not never; who reviews is the operator's", unblocks: "CD.48's no-self-close clause; the filer's rec accumulation (up to file_budget per run until closed)", ruling: "operator ruling 2026-10-04 q5: the consult's position; close_proposed only at read_all and sampled, the denominator close from spot_check, never 'never'; CD.48's no-self-close clause rewritten rung-keyed; who reviews proposals stays the operator's"}
  - {rank: 68, id: budget-q2, group: wave_3, binds_at: text, source: "#1401 q2", class: asked, question: "On the free tier (local adapter, no bill) which lines exist, and does a budget in bytes and seconds mean anything with no price to apply at read?", options: "the same roster with no priced read; a reduced roster for the local adapter", design: "open", consult: "n/a", unblocks: "CD.50's free-tier wording (Decision 209 clause 1)", ruling: "pass 1 (2026-10-04) carries no design option to accept here; stays open for its owner"}
  - {rank: 69, id: filer-q3, group: wave_3, binds_at: measurement, source: "#1396 q3", class: asked, question: "The filer's seed thresholds are unmeasured until capture's producer wiring writes friction rows", options: "carry as provisional; measure at the first read_all window", design: "carry as provisional", consult: "n/a", unblocks: "the filer's seeds (CD.46 seeds; T3.3 c2's number)", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 70, id: register-q2, group: wave_3, binds_at: measurement, source: "#1400 q2", class: asked, question: "Every register seed is unmeasured (counter floor 0.99, drill_min 20 per 28 days at recall 0.9, eps 0.01 on 14-day halves, maturity 40/40/30)", options: "carry as provisional; measure at the first window", design: "carry as provisional", consult: "n/a as a row; k6 refined (per-day denominators from day one; rounding mode stated; pooled recall)", unblocks: "the register's seeds", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 71, id: register-k6, group: wave_3, binds_at: measurement, source: "#1400 k6", class: asked, question: "The divergence rule and its arithmetic", options: "(a) half-window means with an absolute eps on primary and counter, 9-place exact boundary arithmetic, pooled recall (staged); (b) a per-row statistical test; (c) no divergence leg, counter floor only", design: "(a) for the pilot; (b) once per-day denominators are recorded", consult: "refined (medium-high): (a), per-day denominators from day one, rounding mode stated, pooled recall; the operator's tolerance for false diverging decides", unblocks: "the register's diverging leg; revisited at the first measured window", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option; the consult's refinements kept as named build refinements"}
  - {rank: 72, id: budget-seeds, group: wave_3, binds_at: measurement, source: "#1401 seeds", class: asked, question: "The envelopes (1 GiB catalog egress, 30,000 writer and reader requests, 2 GiB writer bytes, 100,000 Lambda GB-s, 300,000 S3 requests, 20 GiB S3 storage, 900 review items, 0 for plane egress and loop tokens), warn share 0.8, tolerance 0.01, rate window 7 days, maturity 30/30/30", options: "carry as provisional, replaced by the first measured month under c3; set from the plan's real constraints", design: "carry as provisional", consult: "refined (medium): set the catalog egress seed from the Neon plan's actual included egress, not 1 GiB; a shadow month before any line alarms; review capacity is the operator's (open question 2)", unblocks: "the budget's first alarm", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option; the consult's refinements kept as named build refinements"}
  - {rank: 73, id: budget-q1, group: wave_3, binds_at: measurement, source: "#1401 q1", class: asked, question: "No measured egress exists: who runs catalog_stats against the live catalog (Decision 88 clause 2, never scheduled), and are the seed envelopes set from that reading or from the model?", options: "the operator runs it once; the Telemetry project's pre-production steps (relayed, unmerged) produce the first reading; schedule it", design: "the first reading may come from the Telemetry project's steps; the envelope is set from a measured reading (P6)", consult: "n/a as a row; the k4 and seeds verdicts say the proxy is a model until the first invoice calibrates it", unblocks: "P6: the budget's seed envelopes, not its build", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 74, id: backval-k4, group: wave_4, binds_at: build, source: "#1397 k4", class: asked, question: "The decision rule and its windows", options: "(a) the staged rule: exposed non-fix sessions in the baseline's dominant (producer, parser_version) stratum, fix day on neither side, a post-fix sample sized from the baseline, holds on one-sided Fisher p <= 0.05 and a point estimate at most half the baseline, fails on the point estimate alone; (b) test the reduction itself; (c) symmetric fails; (d) Mantel-Haenszel over every stratum", design: "(a), deliberate asymmetry, with (c) the first revisit if overturned fails dominate at read_all", consult: "refined (medium-high): (a), add a partial reason; revisit (c) if overturned fails dominate; his loss asymmetry decides (open question 4)", unblocks: "wave 4 back-validation build; CD.48's decision-rule clause", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option; the consult's refinements kept as named build refinements"}
  - {rank: 75, id: backval-q1, group: wave_4, binds_at: build, source: "#1397 q1", class: asked, question: "Who declares each detector's exposure predicate (which sessions could have shown the friction)?", options: "the detector's owner in its rules; the back-validation verb's own table; the operator per subject", design: "open; the friction verb must return per-session exposed and affected flags (its q1 to #1394)", consult: "n/a", unblocks: "wave 4 back-validation build (every verdict needs an exposure denominator)", ruling: "pass 1 (2026-10-04) carries no design option to accept here; stays open for its owner"}
  - {rank: 76, id: backval-q2, group: wave_4, binds_at: build, source: "#1397 q2", class: asked, question: "The effective day of a fix not live at merge (a Lambda deploy, a model or harness change)", options: "the deploy's own timestamp from the governed channel; the merge day; operator-stamped", design: "open; CD.48's fix attempt carries {fix sha, effective day}", consult: "n/a", unblocks: "CD.48's fix-attempt stamp", ruling: "pass 1 (2026-10-04) carries no design option to accept here; stays open for its owner"}
  - {rank: 77, id: ladder-k2, group: wave_4, binds_at: build, source: "#1398 k2", class: asked, question: "Version change (classifier_version, parser_version or params_version), and how a newer version is recognised", options: "(a) restart at read_all with the new version; (b) one rung down; (c) keep the rung. Inside (a): (a1) an integer stamp compared numerically, restart only on a higher one, a non-integer raises, a wrong output under any stamp still demotes; (a2) first-seen order; (a3) state per (component, version)", design: "(a) with (a1), paired with a declared cosmetic class that does not bump the stamp; version_restart false is a kill switch only", consult: "refined (high/medium): (a)/(a1); the cosmetic class decided by a replay test, never by declaration; how often rules change is the operator's", unblocks: "CD.46's version clause; #1396 and #1397 defer their version rule to this", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option; the consult's refinements kept as named build refinements"}
  - {rank: 78, id: ladder-k4, group: wave_4, binds_at: build, source: "#1398 k4", class: asked, question: "Rule family and seeds (unsure is a breaker, an overdue sample is a breaker, a promotion needs a clean signal today)", options: "(a) multi-level CSP: consecutive clearance per rung plus k1's return leg; (b) a per-rung Wald SPRT; (c) a fixed-sample 95 percent bound since rung entry", design: "(a) for the pilot; revisit (b) if review volume binds", consult: "refined (medium): (a) for the pilot; SPRT as a later upgrade; raise the top-rung floor above 0.008 (about three reviews a month); review capacity is the operator's", unblocks: "CD.46's family; F2", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option; the consult's refinements kept as named build refinements"}
  - {rank: 79, id: allow-k1, group: wave_4, binds_at: build, source: "#1399 k1", class: always-ask, question: "Membership: which columns cross (and whether a deliberate in-plane encoder is in scope)", options: "(a) tenancy ids as the key, guarded closed enums, finite numbers and booleans at UTC-day grain (42 of 133 columns); withhold an out-of-vocabulary value and count it; (b) also open-vocabulary strings matched against a control-plane vocabulary; (c) the Decision 199 envelope as written", design: "(a)", consult: "refined (high): (a); withhold values, refuse structure; write the threat model; only a first customer needing model or producer breakdowns on day one changes it (open question 10)", unblocks: "CD.51; wave 4 transport build; q1's vocabulary owners", ruling: "open: always-ask, not covered by the 2026-10-04 agreement; the operator's own answer is needed"}
  - {rank: 80, id: allow-k2, group: wave_4, binds_at: build, source: "#1399 k2", class: always-ask, question: "Identifiers", options: "(a) no derived id crosses, tenant_id and project_id only; (b) re-key each crossing id with an HMAC under a data-plane key; (c) ids as written (a confirmation oracle: about two seconds to recover a ref over 100,000 candidates)", design: "(a) while the grain is aggregates; (b) if a row grain is chosen", consult: "refined (high): (a); HMAC is the precondition of any row grain", unblocks: "CD.51", ruling: "open: always-ask, not covered by the 2026-10-04 agreement; the operator's own answer is needed"}
  - {rank: 81, id: allow-k3, group: wave_4, binds_at: build, source: "#1399 k3", class: always-ask, question: "Transport", options: "(a) the data plane pushes to a tenant-scoped ingest endpoint under a customer-revocable credential; (b) the control plane pulls through a customer-granted role; (c) both, by adapter", design: "(a): the only option that runs on the free tier's local adapter", consult: "refined (high): (a) with short-lived write-only tokens and ingest-side rejection; whether any target customer forbids outbound HTTPS is the operator's (open question 11)", unblocks: "CD.51; any credential or role is the operator's to grant", ruling: "open: always-ask, not covered by the 2026-10-04 agreement; the operator's own answer is needed"}
  - {rank: 82, id: allow-k4, group: wave_4, binds_at: build, source: "#1399 k4", class: always-ask, question: "Grain and time resolution", options: "(a) day aggregates per (tenant, project, day, enum values); (b) guarded rows with the day only; (c) guarded rows with millisecond timestamps", design: "(a); a minimum-cell rule is not staged (a one-session project-day still reveals that session's outcome and cost)", consult: "refined (medium): (a) plus minimum-cell coarsening and marginal dimensions; sparse-dashboard tolerance is the operator's", unblocks: "CD.51", ruling: "open: always-ask, not covered by the 2026-10-04 agreement; the operator's own answer is needed"}
  - {rank: 83, id: allow-k5, group: wave_4, binds_at: build, source: "#1399 k5", class: asked, question: "Where the allow-list lives", options: "(a) a per-field egress key on each telemetry Class A contract field with a completeness check; (b) one new Class C contract listing crossing columns; (c) a code constant beside the verb", design: "(a), per AGENTS.md's collocation rule", consult: "stays (medium): (a), with the crossing list rendered mechanically for review; the alignment consult calls this the scaling answer", unblocks: "CD.51's mechanism clause; the contract owner's edit at build", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 84, id: allow-q1, group: wave_4, binds_at: build, source: "#1399 q1", class: asked, question: "Who owns each closed vocabulary and flips enforced to true (until then open-vocabulary columns cannot cross under k1 (a))?", options: "the telemetry contract owner per field; the control plane's published vocabulary; graduate model, provider and producer first", design: "open; O1 names the twelve enforced: false blocks for the contract owner", consult: "n/a as a row; the alignment consult suggests closing model, provider and producer first since a cost dashboard needs them", unblocks: "CD.51's graduation path", ruling: "pass 1 (2026-10-04) carries no design option to accept here; stays open for its owner"}
  - {rank: 85, id: backval-k3, group: wave_4, binds_at: promotion, source: "#1397 k3", class: asked, question: "Confounds: a single-subject test proves any fix that lands during a global drop", options: "(a) single-subject rule only, the other subjects' rate ratio recorded as an advisory control; (b) a difference-in-differences gate; (c) suspend verdicts across a declared change window", design: "(a) until the sampled rung, then decide (b) on the recorded controls", consult: "refined (medium): (a) with the control ratio recorded; a confounded holds demotes to close_proposed", unblocks: "back-validation's sampled rung", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option; the consult's refinements kept as named build refinements"}
  - {rank: 86, id: ladder-k1, group: wave_4, binds_at: promotion, source: "#1398 k1", class: asked, question: "Return leg when a review finds a wrong output above read_all (the pilot schema forbids a downward pair, O1)", options: "(a) return to read_all (CSP-1); (b) one rung down; (c) none, as all six earlier items declare", design: "(a): escaped-wrong share at or under 1.85 percent on every seed when monitor recall meets the drill floor; conditional on recall", consult: "stays (high): (a), about 40 reviews per return; only a large difference in harm or capacity changes it", unblocks: "CD.46's overturned_output leg; P7", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 87, id: ladder-k3, group: wave_4, binds_at: promotion, source: "#1398 k3", class: asked, question: "Who moves a component", options: "(a) the operator approves every promotion, demotions and restarts apply automatically; (b) both automatic; (c) the operator ratifies both, with only the record automatic", design: "(a)", consult: "refined (high): (a), allowing a standing approval rule for named (rung, version) pairs; who approves when he is away is the operator's (open question 3)", unblocks: "CD.46's authority clause; every promotion during an unattended window", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option; the consult's refinements kept as named build refinements"}
  - {rank: 88, id: ladder-k5, group: wave_4, binds_at: promotion, source: "#1398 k5", class: asked, question: "Monitor validation before anomaly_triggered", options: "(a) a windowed drill: at least 20 faults injected in the last 20 days of spot_check, at least 90 percent detected; (b) historical recall over reviewer-found errors; (c) none", design: "(a), a recall floor, not perfection; candidate cures (divide the signal by drill-measured recall; a floor nearer 1.0; drills at lower rungs) not adopted", consult: "refined (medium): (a) plus cure (a1); promotion gated on reviewed outcomes, not the signal; whether 90 percent recall is acceptable is the operator's", unblocks: "CD.46's drill clause with F3; the top rung of every component", ruling: "operator ruling 2026-10-04 q14: (a) plus cure (a1) and promotion gated on reviewed outcomes; the 90 percent recall floor stays a provisional seed"}
  - {rank: 89, id: ladder-k7, group: wave_4, binds_at: promotion, source: "#1398 k7", class: asked, question: "Signal demotion (breach and dark)", options: "(a) demote one rung on a breach, and one rung when no defined value arrives in dark_days (staged); (b) hold instead; (c) a breach returns to read_all; (d) dark as a hold, or an output-count input so no traffic is told apart from a dead monitor", design: "(a) for a breach, (d) with an output-count input for dark (as staged, a quiet weekend demotes a clean component)", consult: "refined (high): one rung on breach; dark = (d) output-count input, demote once then hold, dead-man alert (as staged the dark leg cascades one rung a day during a monitor outage); tolerance for quiet weekends is the operator's", unblocks: "CD.46's signal_breach and verdict_not_ok legs", covers: ["#1398 q4 (folded into k7 by #1398)"], ruling: "operator-accepted 2026-10-04 (pass 1): the design's option; the consult's refinements kept as named build refinements"}
  - {rank: 90, id: backval-k1, group: wave_4, binds_at: close, source: "#1397 k1", class: asked, question: "A verdict evaluated after the merge, keyed to the fix sha, and how a fix reaches an open filer rec (the premise amends Decision 201 points 1-2 under every option)", options: "(a) the Resolves: trailer routes the acceptance to a new telemetry verdict source that stamps a fix attempt and supplies no verdict, a scheduled job later writes the verdict and, on holds, closes through update_rec; (b) a separate trailer and job; (c) an operator stamps the fix attempt by hand", design: "(a), with close_proposed until anomaly_triggered", consult: "stays (high on the amendment, medium on (a) vs (b)); two additions: the job reconstructs fix attempts from trailers (the stamp is a cache) and the stamp uses a distinct key; how much third-source machinery belongs in the Decision 201 gate is the operator's", unblocks: "CD.48 (P8); any proof close in wave 4", covers: ["#1396 q1 (the filer rec's acceptance probe is back-validation's verdict read under (a); parked with this row while #1397 is unmerged)"], ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 91, id: backval-k2, group: wave_4, binds_at: close, source: "#1397 k2", class: asked, question: "An unmeasurable verdict at the deadline", options: "(a) the rec stays open, listed for triage with its reason; (b) a close_proposed with the reason and counts; (c) a direct close with the stale_no_recurrence waiver when the reason is exposure or too_rare", design: "(b)", consult: "refined (medium): (b), but only once the filer has also gone quiet; noise against triage load is the operator's", unblocks: "CD.48's unmeasurable clause", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option; the consult's refinements kept as named build refinements"}
  - {rank: 92, id: backval-k5, group: wave_4, binds_at: close, source: "#1397 k5", class: asked, question: "The chronic close (a proof close becomes close_proposed from the third chain record, encoding #1396 k6 (a))", options: "(a) close_proposed from the third record; (b) no chronic arm if k6 (b) stops filing at 3; (c) no cap", design: "whatever #1396 k6 decides; chronic_from is its parameter", consult: "stays (medium): follow rec-filing k6", unblocks: "nothing on its own; row 42 decides it", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 93, id: ladder-q1, group: wave_4, binds_at: text, source: "#1398 q1", class: asked, question: "Is this ladder T3.4's A0-A3, or a separate review ladder sharing one controller?", options: "the same ladder; separate ladders sharing one controller and one transition log", design: "separate (the synthesis's section 3.5 reading); T3.4 c2's staged text states it", consult: "n/a as a row; #1398 k6's consult anchors the item on T3.4 criterion 1 and leaves roadmap intent to the operator", unblocks: "T3.4 c2's staged sentence; F6's ladder half", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 94, id: allow-q2, group: wave_4, binds_at: text, source: "#1399 q2", class: asked, question: "No consumer exists: which aggregates does the first paid fleet dashboard need (Decision 209 clause 1)?", options: "day aggregates of the staged 42 columns; model, provider and producer breakdowns on day one (pulls those vocabularies' graduation forward)", design: "open; the allow-list crosses nothing until a consumer exists", consult: "n/a as a row; open question 10: if a customer needs model or producer breakdowns on day one, open-vocabulary strings must cross, which changes k1 and q1", unblocks: "the first egress batch; CD.51's graduation order", ruling: "pass 1 (2026-10-04) carries no design option to accept here; stays open for its owner"}
  - {rank: 95, id: backval-q3, group: wave_4, binds_at: measurement, source: "#1397 q3", class: asked, question: "Seed values are unmeasured (a friction under about 3 percent of exposed sessions is unprovable within 28 days at about 12 sessions a day)", options: "carry as provisional; measure at the first window", design: "carry as provisional", consult: "n/a", unblocks: "back-validation's windows (CD.48's contract vectors)", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option"}
  - {rank: 96, id: ladder-q3, group: wave_4, binds_at: measurement, source: "#1398 q3", class: asked, question: "The ladder's seeds are unmeasured (AOQL target 2 percent, clearances 40/40/20, fractions 0.2/0.04/0.008, a drill of 20 faults in 20 days at recall 0.9, operator capacity)", options: "carry as provisional; raise the 0.008 floor now; measure", design: "carry as provisional", consult: "refined under k4: raise the top-rung floor above 0.008 (about three reviews a month); review capacity is the operator's (open question 2)", unblocks: "CD.46's seeds; the register's ladder row (#1400 k1 (v) removed the NULL-threshold dependency)", ruling: "operator-accepted 2026-10-04 (pass 1): the design's option; the consult's refinements kept as named build refinements"}
```

```yaml
answered_questions:
  - {ref: "#1395 q1", answered: "2026-10-04", how: "relayed from the Telemetry project and verified: 2a-1 is docs/plans/PLAN-telemetry-write-conformance.yaml, plan merged to main in #1403 at 9baecbf3 and its implementation in #1405 at 2553d69a (contracts, producer parser_version 2, write-time row rules), both on 2026-10-04 after this report's pin; the W1 designs #1384, #1394 and #1395 build on those contracts and are re-read against #1405 at their next revision; folded in #1395 at 4f49c55f"}
```

```yaml
consult_rows:
  - {consult: "2026-10-03", pr: 1384, cell: "k1 sync vs detached Stop pass", row: capture-k1}
  - {consult: "2026-10-03", pr: 1384, cell: "k2 cursor-loss pin recovery", row: capture-k2}
  - {consult: "2026-10-03", pr: 1384, cell: "P1 gate/precommit signature owner", row: capture-P1}
  - {consult: "2026-10-03", pr: 1384, cell: "N1 note on rec-4026", row: capture-N1}
  - {consult: "2026-10-03", pr: 1390, cell: "k1 session state and duration", row: reader-k1}
  - {consult: "2026-10-03", pr: 1390, cell: "k2 window verbs vs D199 cl.1, T2.52", row: reader-k2}
  - {consult: "2026-10-03", pr: 1390, cell: "k3 abandoned state", row: reader-k3}
  - {consult: "2026-10-03", pr: 1394, cell: "k1 rules home", row: friction-k1}
  - {consult: "2026-10-03", pr: 1394, cell: "k2 derivation formula", row: friction-k2}
  - {consult: "2026-10-03", pr: 1394, cell: "k3 content signatures", row: friction-k3}
  - {consult: "2026-10-03", pr: 1395, cell: "k1 Claude thinking text", row: delib-k1}
  - {consult: "2026-10-03", pr: 1395, cell: "k2 LiteLLM-lane durability", row: delib-k2}
  - {consult: "2026-10-03", pr: 1395, cell: "k3 who reads CoT text", row: delib-k3}
  - {consult: "2026-10-03", pr: 1395, cell: "k4 read-side arms", row: delib-k4}
  - {consult: "2026-10-03", pr: 1395, cell: "q1 where is 2a-1", row: answered}
  - {consult: "2026-10-03", pr: 1395, cell: "q4 roll-up shape home", row: delib-q4}
  - {consult: "2026-10-03", pr: 1396, cell: "k1 cross-source dedupe", row: filer-k1}
  - {consult: "2026-10-03", pr: 1396, cell: "k2 declined rec meaning", row: filer-k2}
  - {consult: "2026-10-03", pr: 1396, cell: "k3 filing over soft cap", row: filer-k3}
  - {consult: "2026-10-03", pr: 1396, cell: "k4 filer self-close", row: filer-k4}
  - {consult: "2026-10-03", pr: 1396, cell: "q4 run-record home", row: F8}
  - {consult: "2026-10-03", pr: 1396, cell: "k5 malformed finding", row: filer-k5}
  - {consult: "2026-10-03", pr: 1396, cell: "k6 chain length", row: filer-k6}
  - {consult: "2026-10-03", pr: 1397, cell: "k1 post-merge verdict, D201", row: backval-k1}
  - {consult: "2026-10-03", pr: 1397, cell: "k2 unmeasurable at deadline", row: backval-k2}
  - {consult: "2026-10-03", pr: 1397, cell: "k3 confounds", row: backval-k3}
  - {consult: "2026-10-03", pr: 1397, cell: "k4 decision rule", row: backval-k4}
  - {consult: "2026-10-03", pr: 1397, cell: "k5 chronic close", row: backval-k5}
  - {consult: "2026-10-03", pr: 1398, cell: "k1 return leg", row: ladder-k1}
  - {consult: "2026-10-03", pr: 1398, cell: "k2 version change", row: ladder-k2}
  - {consult: "2026-10-03", pr: 1398, cell: "k3 who moves a component", row: ladder-k3}
  - {consult: "2026-10-03", pr: 1398, cell: "k4 rule family", row: ladder-k4}
  - {consult: "2026-10-03", pr: 1398, cell: "k5 monitor validation", row: ladder-k5}
  - {consult: "2026-10-03", pr: 1398, cell: "k6 edge home", row: F6}
  - {consult: "2026-10-03", pr: 1398, cell: "k7 signal demotion", row: ladder-k7}
  - {consult: "2026-10-03", pr: 1399, cell: "k1 membership (always-ask)", row: allow-k1}
  - {consult: "2026-10-03", pr: 1399, cell: "k2 identifiers (always-ask)", row: allow-k2}
  - {consult: "2026-10-03", pr: 1399, cell: "k3 transport (always-ask)", row: allow-k3}
  - {consult: "2026-10-03", pr: 1399, cell: "k4 grain (always-ask)", row: allow-k4}
  - {consult: "2026-10-03", pr: 1399, cell: "k5 allow-list home", row: allow-k5}
  - {consult: "2026-10-03", pr: 1399, cell: "k6 edge home", row: F6}
  - {consult: "2026-10-03", pr: 1400, cell: "k1 ladder effect of a non-ok verdict", row: register-k1}
  - {consult: "2026-10-03", pr: 1400, cell: "k1 (i) propagation strength", row: register-k1-i}
  - {consult: "2026-10-03", pr: 1400, cell: "k1 (ii) precedence", row: register-k1-ii}
  - {consult: "2026-10-03", pr: 1400, cell: "k1 (iii) unregistered row", row: register-k1-iii}
  - {consult: "2026-10-03", pr: 1400, cell: "k1 (iv) staleness", row: register-k1-iv}
  - {consult: "2026-10-03", pr: 1400, cell: "k1 (v) breach leg", row: register-k1-v}
  - {consult: "2026-10-03", pr: 1400, cell: "k2 drills", row: register-k2}
  - {consult: "2026-10-03", pr: 1400, cell: "k2 drill liveness", row: register-k2-liveness}
  - {consult: "2026-10-03", pr: 1400, cell: "k2 retirement/onboarding", row: register-k2-retirement}
  - {consult: "2026-10-03", pr: 1400, cell: "k2 malformed-input blast radius", row: register-k2-malformed}
  - {consult: "2026-10-03", pr: 1400, cell: "k3 declaration home", row: register-k3}
  - {consult: "2026-10-03", pr: 1400, cell: "k4 harm weights", row: register-k4}
  - {consult: "2026-10-03", pr: 1400, cell: "k5 edge home", row: F6}
  - {consult: "2026-10-03", pr: 1400, cell: "k6 divergence rule", row: register-k6}
  - {consult: 2026-10-04-c10, pr: 1401, cell: "k1 breach action", row: budget-k1}
  - {consult: 2026-10-04-c10, pr: 1401, cell: "k1 precedence and windows", row: budget-k1-precedence}
  - {consult: 2026-10-04-c10, pr: 1401, cell: "k1 event-line projection", row: budget-k1-projection}
  - {consult: 2026-10-04-c10, pr: 1401, cell: "k2 budget home", row: budget-k2}
  - {consult: 2026-10-04-c10, pr: 1401, cell: "k3 unit", row: budget-k3}
  - {consult: 2026-10-04-c10, pr: 1401, cell: "k4 egress measurement", row: budget-k4}
  - {consult: 2026-10-04-c10, pr: 1401, cell: "k5 attribution", row: budget-k5}
  - {consult: 2026-10-04-c10, pr: 1401, cell: "k6 edge home", row: F6}
  - {consult: 2026-10-04-c10, pr: 1401, cell: "k7 stock line", row: budget-k7}
  - {consult: 2026-10-04-c10, pr: 1401, cell: "k8 S3 meters", row: budget-k8}
  - {consult: 2026-10-04-c10, pr: 1401, cell: "k9 roster", row: budget-k9}
  - {consult: 2026-10-04-c10, pr: 1401, cell: "seeds", row: budget-seeds}
```

Rows the consults' tables carry for other PRs (#1388 D92-a and D92-b, #1389, #1357) are outside this
stream and are not listed. The T2.19 c9 note in the 2026-10-03 consult (the sync exit-code fix waits on
the operator's go) is a hygiene item recorded in project memory, not a design question.


## 5. Where a consult differs from a design, both positions

The brief asks that where a consult differs from a design's recommendation the report show both. The
2026-10-03 consult uses the verdicts stays, refined and differs; its six differs rows are listed first,
then the two places the synthesis (and so this report's staging) departs from a consult and one converged
row, then the refinements that change a staged row's wording. Every row below is also a row in section 4 (the row
column), so the operator answers it once. No position is chosen here by the report; the operator's
2026-10-04 rulings are the ruling column (the consult's position on every departure).

```yaml
consult_departures:
  - {row: delib-k4, kind: consult_differs, strength: medium, design: "#1395 k4 (a): writer only, the build gate waits for all four writer rules", consult: "add the two cheap read arms now (visibility_unknown, none_with_reasoning) plus a third, tokens exceeding output", staged_as: "row 36 carries both; the operator took the consult's position (q4), so the three read arms are added at the deliberation verb's build", ruling: "2026-10-04: operator took the consult's position (q4)"}
  - {row: filer-k4, kind: consult_differs, strength: medium, design: "#1396 k4 (a): the filer never closes its own recs until back-validation exists, then (b) the denominator close as its fallback", consult: "self-close arrives by maturity rung: close_proposed at read_all and sampled, the denominator close from spot_check; never is not the right default", staged_as: "CD.48's self-close clause is now rung-keyed on the consult's position (q5): close_proposed at read_all and sampled, the denominator close from spot_check", ruling: "2026-10-04: operator took the consult's position (q5); CD.48 rung-keyed"}
  - {row: register-k1-iv, kind: consult_differs, strength: medium-high, design: "#1400 k1 (iv) as staged: the latest defined counter value in the 28-day window decides, failing open", consult: "fail closed: counter_dark when nothing arrived within a per-detector cadence, default 2 days", staged_as: "CD.46's precondition clause presumes a verdict; it does not fix the staleness rule, so either answer fits; the register's SQL at build (wave 3) is where it binds", ruling: "2026-10-04: operator took the consult's position (q6)"}
  - {row: register-k2-retirement, kind: consult_differs, strength: high, design: "#1400 k2 as staged: a retired detector reads unregistered for 27 days", consult: "a registered_from / retired_on validity interval, effective-dated", staged_as: "CD.47's one-table-per-record-kind clause leaves room for an effective-dated register row; the operator took the consult's interval (q8), which #1400 folds at build", ruling: "2026-10-04: operator took the consult's position (q8)"}
  - {row: register-k2-malformed, kind: consult_differs, strength: high, design: "#1400 k2 as staged: any guard firing raises the whole run for up to 27 days", consult: "(a) per-detector malformed verdict for signal and drill defects, the whole-run halt for register defects only, plus a repair procedure", staged_as: "CD.47's faults vocabulary carries malformed either way; the blast radius is the register's SQL at build", ruling: "2026-10-04: operator took the consult's position (q9)"}
  - {row: register-k3, kind: consult_differs_at_lift, strength: medium-high, design: "#1400 k3: (a) register rows now, (b) two required FailureSignal fields at lift (a pilot-schema change, O1)", consult: "(a) now; at lift a required register_row reference with an evaluator check, not inline counter and drill fields", staged_as: "both agree on now; the lift-time half is parked with the lift itself (CD.45 binds while pending) and no pilot-schema change is staged", ruling: "2026-10-04: operator took the consult's position at lift (q10)"}
  - {row: F4, kind: synthesis_departs_from_consult, strength: "c10 consult medium-high; alignment consult records the synthesis", design: "synthesis F4 (a): the budget owns the review-items line and alarm, the ladder owns the schedule, waves are the lever", consult: "the c10 consult's k9 and the alignment consult: the ladder owns the review_items envelope and the budget reads it (one number, one owner)", staged_as: "CD.46 now carries the review_items envelope and CD.50 reads it as a diagnostic line (q7, the consult's reading); the synthesis's split was not taken", ruling: "2026-10-04: operator took the consult's reading (q7); the line moved from CD.50 into CD.46"}
  - {row: F13-F14, kind: synthesis_departs_from_consult, strength: "c10 consult medium-high", design: "synthesis F13 (b): route the budget's breach through the filer so the loop keeps one rec writer, taken only with an F14 answer, else (a)", consult: "F13 (a): file through the existing cost-reconciliation rec path, at projected_breach, after a shadow month", staged_as: "was CD.49 with both routes as its options and no default; since the operator's q2 ruling (F13 (a)) the route is CD.50's BREACH clause and CD.49 is retired; the F5 (a) versus F13 (b) conflict under MAX_EDGES_FROM_ITEM 3 (X21) no longer arises", ruling: "2026-10-04: operator took the consult's route, F13 (a) with F14 (a) (q2); CD.49 retired into CD.50"}
  - {row: CD.47, kind: converged, strength: n/a, design: "synthesis 3.6: every daily verdict record carries a faults list naming each leg that fired", consult: "both consults asked the register (#1400 k1 (ii)) and the budget (#1401 k1 precedence) for a faults list beside the single verdict", staged_as: "CD.47's faults-list clause; listed here because the synthesis adopted it from the consults rather than from a design, so the design PRs do not yet carry it (#1400 and #1401 fold it only at build)", ruling: "2026-10-04: adopted (q11)"}
  - {row: budget-k9, kind: consult_refines, strength: medium-high, design: "#1401 k9 (a): the ten lines of section 2.2", consult: "(a) with each line typed budget, diagnostic or tripwire; the review_items line read from the ladder", staged_as: "CD.50's roster clause now types the ten lines budget, diagnostic or tripwire (q12); the review_items line is the ladder's (q7)", ruling: "2026-10-04: typed roster accepted (q12)"}
  - {row: budget-k1, kind: consult_refines, strength: medium-high, design: "#1401 k1 (a) alarm now, (b) degrade at lift, (c) stop never unattended", consult: "(a) filed at projected_breach and updated at breach, deduped per line per month, the first month in shadow mode; (b) at lift as pre-declared degraded modes with hysteresis; (c) a human-pulled kill switch on the consumer", staged_as: "CD.50's BREACH clause is the design's (a) with the three refinements as rule text (q13): projected_breach filing with per-line monthly dedupe, the shadow month, the kill switch; the route is F13 (a) per the q2 ruling", ruling: "2026-10-04: (a) plus the three refinements accepted (q13)"}
  - {row: ladder-k5, kind: consult_refines, strength: medium, design: "#1398 k5 (a): a windowed drill, at least 20 faults in 20 days, at least 90 percent detected", consult: "(a) plus cure (a1); promotion gated on reviewed outcomes, not the signal; whether 90 percent is acceptable is the operator's", staged_as: "CD.46's DRILL AT THE TOP RUNG clause carries (a) plus cure (a1) and reviewed-outcome gating (q14); the 0.9 recall floor stays a provisional seed", ruling: "2026-10-04: (a) plus cure (a1) and reviewed-outcome gating accepted (q14)"}
```

The c10 consult's and the alignment consult's other rows (budget-k2 through k8, the seeds row, the
#1397 k1 reading of the Decision 201 amendment) are verdict stays or refined with wording the section 4
rows already carry; VP 5 checks that the six differs rows and the three synthesis departures above are
present and that each names a section 4 row.

## 6. This report's own forks (none decided)

Every fork the staging itself met is recorded below in the project's fork-record form. A fork that
weighs alternatives, or cites unmerged work or an unanswered fork, is class asked and is parked; the
rest are consistency-only choices that follow a precedent and change nothing the operator has not
already approved. None is decided by the Step 6b stand-in. The asked rows are also written to
/mnt/project-files/gates/w3-parked-forks.md for the hand-back.

- "fork: one Decision for the ladder (CD.46) or one per component's maturity block | chosen: one row,
  CD.46 | class: consistency-only | precedent: synthesis section 3 and X4-X8, X16, X19 (one Decision
  for the family); PR #1402 at dcdfe783 | precedent_kind: unmerged_plan | reversible: yes | persisted:
  section 3". Although the precedent is an unmerged PR, the choice restates the synthesis's staged
  text rather than weighing an alternative, so it is recorded as consistency-only with its precedent
  kind shown; the operator's approval of the synthesis is what makes it hold.
- "fork: stage CD.52 as a numbered Decision or as a dated amendment note on Decision 199 | chosen:
  neither; both staged, amendment_forms recommended | class: asked | precedent: decision-entry.yaml
  significance routing (a reading of an existing clause is field_semantics, a new commitment is
  numbered_decision); #1390 k2's consult (record the reading as a Decision 199 update note) |
  precedent_kind: contract and consult | reversible: yes | persisted: section 3 cd_routing CD.52,
  section 4 row reader-k2, w3-parked-forks.md". W3-1 in the parked file. Decision 199 already carries
  three dated Update notes on main (2026-09-25, 09-28 and 10-04, the last added by #1405, which says a
  further clause 5 change goes to a numbered Decision); decision-entry.yaml's accretion cap is written
  for the Intent section, so a fourth note on clause 1 is still the cheaper route, and the operator
  weighs the count.
- "fork: stage a re-ground of T3.3 c1 (an agent files recs via log-rec) and the F-035 fill of c2 on
  the unmerged designs, or leave c1 alone and fill c2 with the formula only | chosen: both staged, with
  status open and the design basis shown | class: asked | precedent: operator ruling 2026-10-01 (3a): a
  text-only re-ground of a criterion is not a flip; but c1's re-ground cites #1396 and c2's fill cites
  #1396, #1400 and #1401 (all unmerged) for the loop's shape, source and home | precedent_kind:
  operator ruling and unmerged_plan | reversible: yes (a staged edit the operator may decline) |
  persisted: section 2 rows 1-2, w3-parked-forks.md". W3-2, which also carries the reverse-reference
  question: the staged text names pilot item ids (pwi-...) that exist on no roadmap, in a fixture CD.45
  calls not a roadmap and sunsets, so the operator says whether roadmap text may cite them as provenance
  with the mechanism named first (the staged form) or must wait for the lift's re-pointing.
- "fork: add a new criterion to T3.4 for the loop's review ladder, or carry the ladder inside c2's
  staged text | chosen: no new criterion; c2 carries one sentence and the ladder is CD.46 | class:
  consistency-only | precedent: brief (nothing is ratified; no status or criterion flips) and T1.17
  coupling (a criterion's met_by must be a plan name or sha, which no ladder has yet) | precedent_kind:
  brief and decision | reversible: yes | persisted: section 2".
- "fork: order the W1 rows by PR number, by wave, or by binds_at across the whole list | chosen: by
  wave then binds_at, the synthesis's build order | class: consistency-only | precedent: the brief's
  'ordered by effect' and synthesis section 2.3 | precedent_kind: brief and unmerged_plan |
  reversible: yes | persisted: section 4, VP 4".
- "fork: number the staged candidate decisions CD.46-CD.52 or leave them unnumbered | chosen:
  placeholder numbers in filing order, re-numbered at filing | class: consistency-only | precedent:
  CD.45 is the highest row on main (platform roadmap candidate_decisions[]); CandidateDecision.id is
  required | precedent_kind: contract | reversible: yes | persisted: section 3 and VP 3 (checks the ids
  are not on main)".
- "fork: carry the T3.4 c2 Decision 92 / MED-9 anchor as unverified, or drop the sentence that cites
  it | chosen: carried and flagged | class: asked | precedent: #1398 O2 (no Decision names MED-9) is
  an observation in an unmerged PR; docs/DECISIONS.md on main has no MED-9 (VP 2 checks) |
  precedent_kind: unmerged_plan | reversible: yes | persisted: section 2 row 4, w3-parked-forks.md".
  W3-3: the operator says which Decision the criterion meant.
- "fork: list the three ownership calls as rows (they are the coordinator's recommendations, posted
  2026-10-04, not forks in a parked-forks file) | chosen: listed, source 'operator call n' | class:
  consistency-only | precedent: the brief names them as the third group | precedent_kind: brief |
  reversible: yes | persisted: section 4 rows 15-17".
- "fork: whether CD.51 (allow-list and transport) may be staged while its four membership,
  identifier, transport and grain forks are always-ask | chosen: staged as a shell whose detail names
  the four forks as blanks the operator fills; nothing in it chooses a membership | class: asked |
  precedent: project instructions (never IAM or security changes; the allow-list is a security
  surface) and #1399's own always-ask classing | precedent_kind: project instructions and
  unmerged_plan | reversible: yes | persisted: section 3 CD.51, w3-parked-forks.md". W3-4: the
  operator says whether a shell CD is wanted before the four answers, or the row waits.
- "fork: stage CD.49 (breach-rec route) and CD.50 (budget register) beyond the synthesis's section 6,
  which stages only the ladder, the journal, the Decision 201 amendment, the F-035 formula and the X3
  reading | chosen: staged as rows with no default (CD.49) and with the consult refinements named, not
  adopted (CD.50); the operator may decline either without touching the other five; on 2026-10-04 the
  operator retired CD.49 into CD.50 (q2) and accepted CD.50's presumed forks | class: asked |
  precedent: none (the synthesis parks X18 as F13 and stages no budget Decision; #1396 and #1401 are
  unmerged) | precedent_kind: none | reversible: yes | persisted: section 3 CD.49 and CD.50,
  w3-parked-forks.md". W3-5: the operator says whether X18 and the budget register are Decisions now or
  wait for their items' merges.
- "fork: record the operator's 2026-10-04 rulings as a ruling column on the existing 96 rows plus a
  RULINGS sentence per CD row, or rewrite section 4 as an answered list | chosen: the column and the
  sentences, so the record of what was asked survives beside the answer | class: consistency-only |
  precedent: the rulings file names rows by this report's ranks; the brief (nothing ratified, no flips) |
  precedent_kind: operator ruling | reversible: yes | persisted: sections 3, 4, 5".
- "fork: retire CD.49 by deleting the row and leaving its placeholder id unused, or renumber CD.50-CD.52
  down | chosen: delete, id unused | class: consistency-only | precedent: the decision sheet and the
  rulings cite CD.49-CD.52 by these ids; the operator re-numbers at filing anyway | precedent_kind:
  operator ruling | reversible: yes | persisted: section 3, VP 3".
- "fork: the F-035 measure above read_all | chosen: none; the gap is stated in the T3.3 c2 row and the
  fill is marked adjudicable at read_all only | class: asked | precedent: none (#1396's rejected_share
  and its spot_check -> anomaly_triggered trigger carry the same review-fraction bias; amending them
  amends an unmerged design) | precedent_kind: none | reversible: yes | persisted: section 2 row 1,
  w3-parked-forks.md". W3-6: the operator says whether the denominator becomes operator-reviewed
  outputs in the window (or a sampling-weighted estimate), whether a minimum reviewed count per
  detector applies, and whether reject is split from covered and duplicate; that 0/0 reads
  unmeasurable is #1396's own guard and a correctness property, confirmed rather than asked.

## 7. Boundary

This report stages text and lists questions. It does not edit docs/ROADMAP-PLATFORM.yaml,
docs/DECISIONS.md, the pilot fixture or any contract; it files no candidate decision and ratifies
nothing; it flips no status and adds no criterion; it reads and writes no rec (Decision 67); it
decides no fork of its own or of any input, and it chooses no never-list option (the two it names,
raising MAX_EDGES_FROM_ITEM and amending project-id.yaml, are named as options the inputs carry). It
spends none of the two remaining fixture items. It merges nothing: the PR waits for the operator, and
the operator applies the staged roadmap edit and files the candidate decisions, in whatever order the
answers in section 4 allow. The operator's 2026-10-04 rulings are recorded here as text (the ruling
column, the RULINGS sentences, the CD.49 retirement): they ratify nothing, flip nothing and file
nothing; the always-ask rows and options, W3-1 to W3-6 and the Neon-to-RDS question stay open. Reading #1384 and #1390 as not final and the synthesis as unmerged, every
sentence above that cites them is pinned to the inputs block and is re-read when they settle.

## 8. Evidence

| claim | where | proof |
| --- | --- | --- |
| the synthesis and the ten component heads are the pinned ones; the three consults, eleven parked-forks files (c8 and c9 re-pinned), the rulings and the decision sheet hash as pinned | section 1 | VP 1 |
| the four criteria rows quote the roadmap text verbatim, every status is open before and after, T3.3 c2's staged text carries the F-035 formula, no Decision on main names MED-9 | section 2 | VP 2 |
| six candidate-decision rows (CD.49 retired) validate against CandidateDecision, all pending, none of their ids on main, each with a routing row | section 3 | VP 3 |
| ninety-six parked rows, unique ids, capture-q1 first, F1-F14 next with F13 and F14 as one row, three calls, groups and binds_at in order, every parked-forks fork, every prose open question and every consult table cell mapped, every row carrying a ruling | section 4 | VP 4 |
| the six consult differs rows and the three synthesis departures are present, each naming a section 4 row | section 5 | VP 5 |
| the plan validates; the branch is plan and report only; the fast tier passes | plan | VP 6, 7, 8 |
| no staged criterion or CD rule text carries a contested resolution phrase outside a sentence that names the fork, an option or a consult position | sections 2, 3 | VP 9 |
