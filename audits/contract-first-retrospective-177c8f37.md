# Contract-first governance retrospective - report

Audited commit `177c8f37` (origin/main, 2026-11-16). System of record:
`audits/contract-first-retrospective-177c8f37.yaml`. Eight findings (0 critical, 1 high, 7 medium)
against eight rejected candidates. Maturity: strong on all six surfaces; frontier is foreclosed
repository-wide because EX7 is still missed.

## Intake

- Flags set. `web_unavailable`: three lookups were attempted and failed (hosts outside the network
  allow-list), so practices are cited from knowledge and Q9 steps 3, 4 and 8 are HYPOTHESIS.
  `compaction_unreadable`: no reader verb returns a compact count, so the number of sessions needing
  compaction is unknown. All other flags are false: history, dedup and telemetry were complete.
- Drift: the audited commit is 23 commits after the prompt's anchor (entries 264-267 are new). No
  anchor failed to resolve.
- Judgment calls are recorded in `meta.contract_notes`. The two that move a number: three read-set
  files are assigned always-read (Q2 gives the result both ways), and the content partition uses
  the pinned strict block rule (Q1 gives the lenient value too).
- Limits: the content partition and the contract-change comparison are single-rater samples (12
  entries, 20 commits).
- Invariants checked: findings 8 = 1 novel + 5 planned-insufficient + 2 planned-unbuilt; 20
  candidates adjudicated (12 to findings, 7 not a defect, 1 covered; each of those 8 has one
  rejection row); 21 ledger, 11 baseline, 8 growth, 8 condition and 13 checklist rows; the YAML
  parses; neither file holds an account id, ARN, credential or session id.

## Verdicts

|Question|Verdict|Basis|
|---|---|---|
|Q1 routing effect|not-shifted|Specification plus change-record is 74 percent of the substantive bytes of the 12 newest entries (64 on a lenient reading) against 65 before. In 4 of 4 sampled commits the entry still carries specification, as before.|
|Q2 context cost|grown|Static planning footprint 69,048 to 106,522 tokens (plus 54 percent): root agent plus 41, gate sub-agents plus 68. Observed median 676,094 first-time tokens per plan tree; no observed before.|
|Q3 enforcement|enforced-by-default|Evaluators enforce in 80 percent of the weighted sample and 9 of the 12 newest main clauses are bound. Both sit at their thresholds.|
|Q4 components|core-holding|15 of 21 hold (8 with a side effect), 3 eroded, 2 retired without replacement, 1 never exercised.|
|Q5 sustainability|sustainable-with-named-gaps|No hard limit binds within 30 days. The cost of a planning run rises with stock, through the index.|
|Q6 gap ownership|insufficient|6 of 8 findings have no owner, or an owner whose acceptance would not close them. None is in progress.|
|Q7 industry practice|mixed|EX13 up (every contract versioned and gated). EX11 down (rationale thinning). EX7 still missed.|
|Q9 improvement path|path-identified|Seven steps close the eight findings; two opportunities.|

## What held and what did not

Held. The envelope and routing claim are on 97 of 97 entries. The cap bounds size: no entry has
landed over it since it was armed. All 77 contracts are now versioned and gated (23 were free-form
at the baseline), and the 53 mechanism contracts each name a reader that resolves. The population
ratchet sits at zero slack. The entry file is never loaded whole, and the ambient layer is down 15
percent under its ratchet. The append-only dialect held after its guard was retired: an exact-line
comparison finds no unsanctioned change to a ratified body in 81 days. Amended entries name their
amender on 153 of 155 edges added in P2.

Did not hold: routing. The gates record a claim and ask for a justification; neither changes what
an entry carries (CFR-01, high). The routing rule has no row for mechanism specification. In 40 of
97 entries the justification dismisses a contract on structural grounds (it cannot amend a ratified
clause, or it records mechanism and not authority), and so 81 of 97 entries exist to amend an
earlier one. Specification now reaches contracts, but beside the entry
and not instead of it.

Side effects. The cap works as a target: 47 of 94 entries landed within 5 percent of it, and the
share of entries with a Rationale section fell from 74 to 41 percent (CFR-02). Evaluator resolution
proves that something names a contract, not that its rules are checked: 6 of 12 sampled evaluators
would react to a breach of the sampled statement, and one resolves against a retired skill
(CFR-08).

Eroded. The WARN accretion signal (entries over 1.2 times the cap went from 23 to 37 with no
response), the archival wave (27 archived, unchanged) and the change-record rule (11 of 12 entries
open with acute state; median squash-commit bodies fell from 1,155 to 454 bytes). Retired without
a replacement: the append-only guard and the stock limits.

Earlier findings: 8 closed, CFG-09 partly closed, CFG-02 superseded, CFG-07 open. CFG-07 is the
same defect as CFR-01.

## Cost and growth

|Measure|2026-08-07|2026-09-12|Audited|
|---|---|---|---|
|Live entries|119|140|216|
|Decision file bytes|573,978|706,357|1,238,850|
|Index bytes, read whole each scout round|110,608|119,191|186,329|
|Contract files|37|47|77|
|Contract bytes|467,997|775,981|1,919,749|
|Contracts within 25 lines of budget|2|6|20|
|Ambient layer bytes|359,167|313,698|306,279|
|Planning read-set bytes, 16 files|340,247|387,813|541,865|
|Static token estimate|69,048|71,509|106,522|

New entries per day were 1.49 in P0, 0.58 in P1 and 1.17 in P2; per plan file 0.262, 0.191 and
0.221. Corpus bytes per day were 8,213, 3,681 and 8,192, and 22 percent of P2 inflow is additions
to entries that had already landed. At the P2 rate the projection is 321 live entries and 155,002
estimated tokens at 90 days, 643 and 303,133 at 365.

Two things drive the planning footprint. The index grows about 880 bytes with each entry and is
read whole (CFR-03). Content that left AGENTS.md and the planning skill went to contracts the same
run is still sent to read, with no section named, and nothing totals the result (CFR-04). The
implementing run shows the same pattern: 266,582 to 497,688 bytes over 17 files.

Contracts took in 17,596 bytes a day in P2, twice the decision corpus, under a budget that counts
lines. Files at the limit take line-neutral edits with longer lines: one contract went from 19,351
to 64,750 bytes while moving from 413 to 500 lines (CFR-05).

What a planning run costs cannot be read from the system: compaction is not counted, and 5 of 8
plan-producing session trees are labelled `claude_code`, not `plan` (CFR-07).

## Reviews falling due

|Decision|Condition|Verdict|Recommended|
|---|---|---|---|
|167|mis-routed-past-both-gates, due 2026-11-17|not-fired|re-arm, reworded to something the system can show|
|167|prose (a): content cut to fit the cap|not-fired|retire: a closed set of ten|
|167|prose (b): split to evade the cap|not-fired|keep|
|177|legitimate-shape-blocked-twice, due 2026-11-25|not-fired|re-arm: its remedy names a retired guard|
|177|portal cutover retires the surface|foreclosed|retire|
|179|index-read-context-cost, due 2026-12-01|foreclosed|re-arm with a measured threshold: the read is 56 percent larger|
|179|inflow-unguarded|fired|re-decide: entries per day doubled against P1 with no stock guard|
|179|portal read|foreclosed|retire|

The fired condition needs context: most of the rise is the volume of work, and the rate per plan
file is 16 percent above P1 and 16 percent below P0. Decision 133's review has been overdue since
2026-09-30. Of 236 monitored conditions 233 need a manual judgment, and 205 fall due on four
month-ends in 2027 (CFR-06).

## Improvement path

1. Dispose of the eight reviews, and register predicates for the two conditions this audit
   computed from git (CFR-06).
2. Slim the index read: rec-3250 pulled forward, plus skeleton rows for compacted stubs (CFR-03).
3. Name sections for the git-ops and file-router pointers, report read-set bytes by band, and
   expose a compact count (CFR-04, CFR-07).
4. Give mechanism specification a home that can change without a new Decision: a routing row and
   a delegation clause, for new entries only (CFR-01). Needs an operator ruling.
5. Change what the cap measures, so that it presses on specification and spares the rationale
   (CFR-02). Same amendment as step 4.
6. Add a byte companion to the contract budget and a log retention rule (CFR-05).
7. Make an evaluator declare what it asserts, and require its surface to be live (CFR-08).
8. Opportunity: retire long amendment chains by consolidating supersession. Needs a ruling on
   whether this is the live-entry refactor the operator declined; it writes new entries and edits
   none.
9. Opportunity: run the append-only comparison as a census, not as a merge gate.

No step is proposed for a new stock limit, for archival, or for a merge-time append-only guard:
the evidence favours bounding the read over bounding the corpus.

## Not asked

- Supersession is unused: 1 entry of 97 supersedes another, 81 amend. Nothing sheds weight because
  nothing is superseded, and the six most-amended entries need 6 to 10 entries read to state the
  current rule.
- The change narrative moved to plan files (median 26,449 to 46,222 bytes), not to commits.
- Thirteen test modules pin Decision text; one blocked a sanctioned annotation.
- Scout triage has no measure of reliability. Its verdict is "flags found" on 92 percent of rounds,
  and each round costs about 54,000 tokens of fixed reading.
- Gate cost is 6.5 critique and 1.56 scout rounds a plan; what the gates catch is recorded only as
  counts.
- Outcomes: one is measurable and improved (in-place drift, from 55 of 126 entries to none). The
  repository cannot measure the rest.

## What this audit did not file

Eight candidates were rejected, each with its evidence in the YAML. The entry rate follows the
volume of work (C3). The retired append-only guard is not missed on the measured record (C6).
Outflow from a file that nobody loads buys nothing (C7). Amended entries are annotated, and a check
fails an edge that is neither annotated nor waived (C12). `kind` gained a consumer on the audit day
(C13). Plans, the roadmap and recommendations are either governed or read one at a time (C16). The
stale references listed cannot mislead (C17). What the gates catch is unmeasured, and an owner in
progress has the remedy: T4.32 c6 with rec-4434 (C20).
