# REPORT: W1 component 3 - friction classifier (read-time rules; rec-4032 durable form)

REPORT-ONLY (Decision 86 cl.2). Planning artefact: `docs/plans/PLAN-w1-friction-classifier.yaml`.
Fixture rows: `pwi-friction-classifier` in `docs/work-item-pilot/telemetry-feedback-loop.yaml`
(CD.45 pilot, provisional_v0). Nothing is built, filed, closed, flipped or ratified here.

## 0. Verdict

- The component is real, and it is a set of RULES, not a model. Decision 199 cl.1 derives friction
  classification at read; the operator's 2026-09-24 ruling (recorded in rec-4032's context) makes the
  MVP a rules-based reader verb over observed facts, and keeps an LLM classifier off the read path for
  good. What is missing is the rule set itself: which stored facts count as friction, under which label,
  in which of the contract's two classes (rework, exception). No file, table or verb holds it today.
- The ratified formula and today's producer disagree, and a literal build over today's rows counts the
  wrong things (risks R1, R2). rework_total and exception_total count `process_event` rows whose `name`
  matches a signature, with inputs `name` and `observation_type` only (telemetry_sessions.yaml:228,
  :239). Two separate gaps:
  - Hook passes. The producer writes one process_event per hook ATTACHMENT, passes included, with the
    same name for a pass and a block; only `severity` tells them apart (VP 1). The formula reads no
    severity, so it counts passes. Nothing in the ruling below covers this half.
  - Tool errors. The operator's 2026-09-24 ruling, recorded in rec-4032's context, models the facts as
    "hook blocks, gate failures, tool errors stored as process_event rows with stable name signatures".
    The formula follows that model. The producer departs from it: slice 3a recorded a deviation and
    stores tool errors once, as the tool_call close `outcome`, never as process_events (observations.py:5;
    PLAN-telemetry-turn-capture-core.yaml:636-638). So over today's rows the formula never sees a tool
    error.
  Over section 2's vectors the literal formula disagrees with the intended count on 11 of 17 (VP 2): 1
  hook pass (v01), 7 tool facts it cannot see (v03, v06-v09, v13, v17) and 3 rows that this design
  counts through a generic rule (v04, v15, v16). Whether the contract follows the producer or the producer
  follows the ruling is k2; both have a precedent.
- Of the five patterns in the retired transcript-review prompt (repeated-tool-failure, scope-creep,
  context-confusion, workaround, missing-gotcha; VP 3), only repeated-tool-failure is fully derivable
  from stored facts, and scope-creep only where a scope hook or gate fires. The other three need
  content (edit text, file paths, a knowledge base) that telemetry_observations does not hold (VP 4).
  Where that content should be turned into facts is k3.
- The pointer "rec-4032 labels table" in both formulas names the wrong artefact for the MVP. rec-4032 is
  the DURABLE form: a silver table of per-observation labels materialized by a job, triggered by the
  first LLM classifier or a Decision 88 egress measurement, behind the same verb signature. Its contract
  file does not exist (VP 5). The read-time rules need their own home; staged as k1.
- One item fits the clause-3 grain (kind task; three criteria; part_of T3.20, depends_on T2.36). The
  rules, the verb and the guard share one failure mode (a friction total that moves for the wrong
  reason), so splitting them would spend the 12-item cap on rows with one failure signal.

## 1. Evidence (each row re-derivable; VP step in brackets)

| id | fact | anchor |
|---|---|---|
| e1 | rework_total and exception_total: "count of telemetry_observations process_event rows whose name matches a rework (exception) signature (rec-4032 labels table)"; inputs name and observation_type only [VP 5] | docs/contracts/telemetry_sessions.yaml:228, :239 |
| e2 | process_event name grammar `<source>:<signature>` is "the stable signature a friction-classification reader pattern-matches against (rec-4032's labels table)" | docs/contracts/telemetry_observations.yaml:151-153 |
| e3 | The producer emits one process_event per hook attachment (every `hook_*` attachment type), with severity error for exit 2, info for exit 0 or none, warning otherwise; the name comes from the hook script's basename, else the hookName [VP 1] | src/turn_capture/streams.py:201-202; src/turn_capture/observations.py:343-361 |
| e4 | Tool errors are the tool_call close outcome (success, error, blocked, interrupted), never process_events; a tool_use with no result gets one synthetic interrupted close [VP 1] | src/turn_capture/observations.py:1-5, :113-123 |
| e5 | No `gate:` or `precommit:` process_event is emitted; those signatures were handed on to slice 3b "or a read-side classifier over tool_result rows (rec-4032)" [VP 1] | docs/plans/PLAN-telemetry-turn-capture-core.yaml:636-642 |
| e6 | rec-4032 (status open, read through rec_by_id): silver table telemetry_friction_labels, grain (observation_id, classifier_id, classifier_version), materialized by a scheduled job; LLM classification "must never run on the read path"; same reader-verb signature as the MVP rules verb; trigger: first LLM classifier or a Decision 88 egress measurement. Its acceptance file is absent [VP 5] | rec-4032 context and acceptance (rec_by_id) |
| e7 | rec-4024 item (6) lists `friction_events rules-based` among the reader verbs; the contracts name it session_friction_rollup | rec-4024 context (rec_by_id); telemetry_sessions.yaml:228 |
| e8 | The retired transcript-review prompt's five patterns, deleted with the scheduled prompt in #969 [VP 3] | git show 7b67e21d^:.github/prompts/scheduled/transcript-review.prompt.md |
| e9 | telemetry_observations rows carry no content column (no edit text, path, tool input or Bash output); content lives in telemetry_transcripts, spilled to the blob port above 65536 bytes [VP 4]. One exception: a process_event's `metadata` JSON carries the scrubbed hook `command` and `stderr_head`, the first 512 characters of hook stderr | src/turn_capture/observations.py:32-64, :331-332; docs/contracts/telemetry_transcripts.yaml:172 |
| e10 | Of the 13 registered repo hooks, 12 resolve to a script signature; the quoted `handoff_evidence_gate.sh` command does not, and falls back to the shared hookName signature `hook:PreToolUse.Bash` [VP 4] | .claude/settings.json; src/turn_capture/observations.py:293-299 |
| e11 | Decision 209 cl.2(b): only an explicit allow-list of metadata fields crosses to a control plane; free text (titles, paths, error messages) can leak tenant data | docs/DECISIONS.md:75 |
| e12 | The `attempt` column ("a key rework signal") is never populated by the claude_code producer [VP 1] | docs/contracts/telemetry_observations.yaml:297-298; src/turn_capture/observations.py:32-64 |
| e13 | The operator's 2026-09-24 ruling: "the MVP derives friction classification AT READ with a rules-based reader verb over observed facts (hook blocks, gate failures, tool errors stored as process_event rows with stable name signatures)" | rec-4032 context (rec_by_id) |
| e14 | Slice 3a's recorded deviation: "tool errors are stored once, as the tool_call close outcome, not duplicated as process_events (the outcome is observed once and stored on the row that observes it)" | docs/plans/PLAN-telemetry-turn-capture-core.yaml:636-638 |

Measured (local only; no production catalog read):

- Rules in SQL: the sketch in section 2 passes 17 of 17 hand-written vectors on DuckDB 1.5.4, comparing
  ref, label and class, with zero wildcard signatures among the 4 specific rules [VP 2]. Eleven mutants
  were run once by hand and each is caught, failing the vectors named: dropping the hook-absorbs-tool-block
  guard (v02, v10), letting any hook severity absorb a block (v14), dropping the absorb test's tool_call
  guard so a hook also absorbs a parent process_event (v16), letting a repeat run cross turns (v08) or
  tools (v09), letting a block fall out of the run sequence (v17), threshold 2 (v07, v08, v17), dropping
  the one-label-per-fact rank or ranking generic rules first (v02, v10, v11, v16 each), dropping
  `critical` from the fact filter (v15), and a run class hard-coded in the SQL instead of read from
  run_rule (v06, v09, v13). Three survive by design, because the rules'
  disposition column, not the fact filter, is the guard: adding `info` or `interrupted` to the fact
  filter, or dropping its `event_kind = 'close'` term (open rows carry no outcome), changes nothing while
  no rule has those dispositions. v01, v12 and v14 pin that.
- The literal contract formula (count process_event rows whose name matches a rule signature, severity
  ignored, tool closes unread) disagrees with the intended label count on 11 of 17 vectors [VP 2]. Two
  more agree by count only: v05 and v14, where it counts a pass or a non-blocking error under a block's
  label.
- One real transcript (this session's own CC-web transcript, run through record_turn locally; counts
  only, not reproducible from the repo): 3 process_events, all hook passes at severity info (one of them
  under the fallback signature `hook:PostToolUse.Bash`), and 62 tool_call closes, 54 of them Bash (87%).
  The 61 tool-origin transcript rows held about 232 KB of content against those 65 typed fact rows. Two
  consequences: name-only repeat grouping is dominated by Bash (q3), and a read-side regex over
  tool_result content reads far more bytes than a rule over typed rows (about ten times, at an
  estimated few hundred bytes per typed row; k3). The session ran outside the repo's project directory, so
  none of the repo's 13 hooks ran in it and its 3 attachments come from harness-level hooks. In the
  verifier's own CC-web transcript the only hook attachments were `hook_additional_context`. So which hook
  runs the harness writes an attachment for, and the pass volume behind R1, are unmeasured.

## 2. Classifier design (what this item stages)

Facts (k2 (a)'s model: stored once, by today's producer): a process_event with severity error (a
blocking hook), warning (a hook that itself failed) or critical (in the contract's open severity set,
telemetry_observations.yaml:204-205; no producer emits it today), and a tool_call close with outcome
error or blocked. A hook pass (info), a
user or synthetic interrupt and every open row are not friction facts (q1 on interrupts).

Rules are DATA: one row per rule, an exact signature or NULL (the generic fallback), the disposition it
matches (severity for a process_event, outcome for a tool_call), a label and one of the contract's two
classes. One label per fact: an exact-signature rule beats the generic one. A blocked tool_call whose
own hook block is already a fact is not counted twice. Rework is the run rule: at least
`repeat_threshold` consecutive error closes of the same tool inside one turn (other tools between them
do not break the run) count once. A same-tool `blocked` close does break it: a block is a refusal,
not a failed attempt, so the agent's next call starts a new run (v17). A tool error inside such a run still counts as one exception: rework
counts runs, exception counts events. The seed rule set:

```yaml
classifier_version: 1
repeat_threshold: 3
run_rule: {label: repeated_tool_failure, class: rework}
rules:
  - {rule_id: r01, observation_type: process_event, signature: 'hook:never_on_main', disposition: error, label: branch_policy_block, class: exception}
  - {rule_id: r02, observation_type: process_event, signature: 'hook:fresh_branch_base', disposition: error, label: stale_branch_base, class: exception}
  - {rule_id: r03, observation_type: process_event, signature: 'hook:edit_scope_guard', disposition: error, label: scope_creep, class: exception}
  - {rule_id: r04, observation_type: process_event, signature: 'gate:validate_scope_boundary', disposition: error, label: scope_creep, class: exception}
  - {rule_id: r90, observation_type: process_event, signature: null, disposition: error, label: unmapped_block, class: exception}
  - {rule_id: r91, observation_type: process_event, signature: null, disposition: warning, label: hook_fault, class: exception}
  - {rule_id: r92, observation_type: tool_call, signature: null, disposition: error, label: tool_error, class: exception}
  - {rule_id: r93, observation_type: tool_call, signature: null, disposition: blocked, label: tool_blocked, class: exception}
  - {rule_id: r94, observation_type: process_event, signature: null, disposition: critical, label: unmapped_block, class: exception}
```

The verb body, verified against the vectors below (VP 2). `{obs}` is one session's rows AFTER the
shared R4/R5 dedupe the reader-verbs item stages (W1-2, report section 2), so the partition bound and
the generation rule are inherited, not restated; `{rules}` is the rule set and `{run_label}`,
`{run_class}` and `{repeat_threshold}` the run rule, all rendered server-side from the rules block:

```sql
WITH f AS (
  SELECT * FROM {obs}
  WHERE (observation_type = 'process_event' AND severity IN ('error', 'warning', 'critical'))
     OR (observation_type = 'tool_call' AND event_kind = 'close' AND outcome IN ('error', 'blocked'))
),
labelled AS (
  SELECT f.observation_id AS ref, r.label, r.class,
         row_number() OVER (PARTITION BY f.observation_id ORDER BY r.signature IS NULL, r.rule_id) AS rk
  FROM f JOIN {rules} r
    ON r.observation_type = f.observation_type
   AND r.disposition = coalesce(f.severity, f.outcome)
   AND (r.signature IS NULL OR r.signature = f.name)
  WHERE NOT (f.observation_type = 'tool_call' AND f.outcome = 'blocked' AND EXISTS (
    SELECT 1 FROM f h
    WHERE h.observation_type = 'process_event' AND h.severity = 'error' AND h.parent_observation_id = f.observation_id))
),
seq AS (
  SELECT observation_id, parent_observation_id, name, outcome,
         row_number() OVER (PARTITION BY parent_observation_id, name ORDER BY event_timestamp, source_ordinal) AS pos,
         pos - row_number() OVER (PARTITION BY parent_observation_id, name, outcome
                                  ORDER BY event_timestamp, source_ordinal) AS run
  FROM {obs} WHERE observation_type = 'tool_call' AND event_kind = 'close'
)
SELECT ref, label, class FROM labelled WHERE rk = 1
UNION ALL
SELECT min_by(observation_id, pos), '{run_label}', '{run_class}' FROM seq
WHERE outcome = 'error' GROUP BY parent_observation_id, name, run HAVING count(*) >= {repeat_threshold}
```

A tool_call's parent is its turn (_tool_rows) and a hook process_event's parent is the tool_call it
gated (observations.py:307-310), so "one turn" and "its own hook block" are both parent joins. The
verb's response is derived rows only (Decision 88; Decision 209 cl.2a): per session, rework_total,
exception_total, per-label counts, the unmapped count and `classifier_version`; never a raw row, a
signature list or stderr text.

Vectors (hand-written for this report; VP 2 runs every one). Each row is `[observation_id,
observation_type, event_kind, name, severity, outcome, parent_observation_id, t]`, where t is both the
event time and the source ordinal; `expected` lists `[ref, label]`:

```yaml
vectors:
  - id: v01-hook-pass-is-not-friction
    rows: [[h1, process_event, point, 'hook:never_on_main', info, null, t1, 1], [t1, tool_call, close, Edit, null, success, A, 2]]
    expected: []
  - id: v02-hook-block-absorbs-its-tool-block
    rows: [[h1, process_event, point, 'hook:never_on_main', error, null, t1, 1], [t1, tool_call, close, Edit, null, blocked, A, 2]]
    expected: [[h1, branch_policy_block]]
  - id: v03-block-without-hook-row
    rows: [[t1, tool_call, close, Edit, null, blocked, A, 1]]
    expected: [[t1, tool_blocked]]
  - id: v04-unknown-signature-is-unmapped
    rows: [[h1, process_event, point, 'hook:custom_check', error, null, A, 1]]
    expected: [[h1, unmapped_block]]
  - id: v05-non-blocking-hook-error
    rows: [[h1, process_event, point, 'hook:never_on_main', warning, null, t1, 1], [t1, tool_call, close, Edit, null, success, A, 2]]
    expected: [[h1, hook_fault]]
  - id: v06-three-errors-then-success
    rows: [[t1, tool_call, close, Bash, null, error, A, 1], [t2, tool_call, close, Bash, null, error, A, 2], [t3, tool_call, close, Bash, null, error, A, 3], [t4, tool_call, close, Bash, null, success, A, 4]]
    expected: [[t1, tool_error], [t2, tool_error], [t3, tool_error], [t1, repeated_tool_failure]]
  - id: v07-success-breaks-the-run
    rows: [[t1, tool_call, close, Bash, null, error, A, 1], [t2, tool_call, close, Bash, null, success, A, 2], [t3, tool_call, close, Bash, null, error, A, 3], [t4, tool_call, close, Bash, null, error, A, 4]]
    expected: [[t1, tool_error], [t3, tool_error], [t4, tool_error]]
  - id: v08-runs-do-not-cross-turns
    rows: [[t1, tool_call, close, Bash, null, error, A, 1], [t2, tool_call, close, Bash, null, error, A, 2], [t3, tool_call, close, Bash, null, error, B, 3]]
    expected: [[t1, tool_error], [t2, tool_error], [t3, tool_error]]
  - id: v09-other-tools-do-not-break-the-run
    rows: [[t1, tool_call, close, Bash, null, error, A, 1], [t2, tool_call, close, Read, null, success, A, 2], [t3, tool_call, close, Bash, null, error, A, 3], [t4, tool_call, close, Bash, null, error, A, 4]]
    expected: [[t1, tool_error], [t3, tool_error], [t4, tool_error], [t1, repeated_tool_failure]]
  - id: v10-scope-guard-block
    rows: [[h1, process_event, point, 'hook:edit_scope_guard', error, null, t1, 1], [t1, tool_call, close, Write, null, blocked, A, 2]]
    expected: [[h1, scope_creep]]
  - id: v11-gate-signature-once-emitted
    rows: [[g1, process_event, point, 'gate:validate_scope_boundary', error, null, A, 1]]
    expected: [[g1, scope_creep]]
  - id: v12-interrupt-and-open-rows-are-not-friction
    rows: [[t1, tool_call, open, Bash, null, null, A, 1], [t1, tool_call, close, Bash, null, interrupted, A, 2]]
    expected: []
  - id: v13-four-errors-is-one-run
    rows: [[t1, tool_call, close, Bash, null, error, A, 1], [t2, tool_call, close, Bash, null, error, A, 2], [t3, tool_call, close, Bash, null, error, A, 3], [t4, tool_call, close, Bash, null, error, A, 4]]
    expected: [[t1, tool_error], [t2, tool_error], [t3, tool_error], [t4, tool_error], [t1, repeated_tool_failure]]
  - id: v14-only-a-blocking-hook-absorbs-a-block
    rows: [[h1, process_event, point, 'hook:never_on_main', info, null, t1, 1], [h2, process_event, point, 'hook:fresh_branch_base', warning, null, t1, 2], [t1, tool_call, close, Edit, null, blocked, A, 3]]
    expected: [[h2, hook_fault], [t1, tool_blocked]]
  - id: v15-critical-severity-is-a-fact
    rows: [[h1, process_event, point, 'hook:custom_check', critical, null, A, 1]]
    expected: [[h1, unmapped_block]]
  - id: v16-a-hook-under-a-process-event-drops-neither
    rows: [[g1, process_event, point, 'gate:validate_scope_boundary', error, null, A, 1], [h1, process_event, point, 'hook:custom_check', error, null, g1, 2]]
    expected: [[g1, scope_creep], [h1, unmapped_block]]
  - id: v17-a-block-breaks-the-run
    rows: [[t1, tool_call, close, Bash, null, error, A, 1], [t2, tool_call, close, Bash, null, error, A, 2], [t3, tool_call, close, Bash, null, blocked, A, 3], [t4, tool_call, close, Bash, null, error, A, 4]]
    expected: [[t1, tool_error], [t2, tool_error], [t3, tool_blocked], [t4, tool_error]]
```

The five retired patterns against this design:

| pattern | derivable from stored facts? | where it lands |
|---|---|---|
| repeated-tool-failure | yes: tool_call close outcome, turn parent, order | rule `repeated_tool_failure` (rework) |
| scope-creep | partly: `hook:edit_scope_guard` blocks (inert until an active plan is declared) and, once emitted, `gate:validate_scope_boundary` | rules r03, r04; the "while I'm here" phrasing needs content (k3) |
| workaround (noqa, type: ignore, catch-all) | no: needs edit text | k3: a producer signature from Edit/Write input, or not in MVP |
| context-confusion (re-reads, self-contradiction) | no: needs file paths and meaning | k3: re-reads need a path hash on tool_call open; contradiction is LLM-only |
| missing-gotcha | no: needs a knowledge base and the open recs (the prompt's `.github/copilot-instructions.md` no longer exists) | not a classifier label: the rec-filing component's dedupe |

## 3. Settled / contested / risk / open

Settled (consistent with a Decision, a contract or measured; s1-s3 in the fixture):

- s1 Boundary: read-side rules only. The facts are rec-4026's producer rows (W1-1 wires them), the verb
  mechanism and dedupe are rec-4024's (W1-2), and the durable labels table is rec-4032's, built only at
  its own trigger behind the same verb signature. Decision 199 cl.1 (derived at read), the operator's
  2026-09-24 ruling in rec-4032.
- s2 The rules are one SQL statement over deduped facts: 17/17 vectors pass on ref, label and class,
  including a hook pass not counted, only a blocking hook absorbing its tool's block, and repeat runs
  bounded by turn and tool (VP 2).
- s3 Of the five retired patterns, repeated-tool-failure is fully derivable from stored facts, scope-creep
  only through a scope hook or gate signature, and the other three need content the observations table
  does not hold (VP 3, VP 4).

Contested (evidence on both sides, options listed; k1-k3 in the fixture):

- k1 Where the read-time rules live. The contract points at "rec-4032's labels table", but rec-4032 is the
  later materialized per-observation table, and the MVP needs a signature-to-label rule set now.
  Options: (a) a governed, versioned rules file bundled into the reader's named-read registry as data
  (like NAMED_READS SQL), rendered server-side into the verb; `classifier_version` is stamped in every
  response, and the verb reads one table (plus the sessions open rows the shared dedupe already reads);
  (b) a DuckLake dimension table `telemetry_friction_rules` joined in the verb: a second table to
  register, a write path for rule edits, and SCD2 history for free; (c) inline CASE arms in the verb SQL:
  no data file, but every rule edit is a registry SQL change and nothing can test the rules apart from
  the verb. For (a): rules change by PR with review, one version per deploy, no new write path. Against
  (a): a rule edit needs a reader deploy (Decision 79 per-Lambda gating). Recommended: (a). rec-4032
  stays as the materialized form for its own triggers, keyed by the same classifier_version. The
  contract's pointer wording ("rec-4032 labels table") needs a dated amendment either way. Ratified
  contract text; parked.
- k2 The derivation formula. Two halves, with different standing:
  - Pass half (no precedent either way). The formula reads no severity, so a signature match counts
    every hook pass (e3). Every live option has to add severity to the formula, or have the producer
    drop exit-0 attachments; the ruling below does not address it.
  - Tool-error half (conflicting precedents). The operator's 2026-09-24 ruling (e13) models tool errors
    as process_event rows, and the ratified formula follows that model. Slice 3a's recorded deviation
    (e14) stores them only as the tool_call close outcome, so over today's rows the formula sees none
    (R2).

  Options:
  - (a) Ratify the deviation. Amend both derivations: facts are non-info process_events plus error or
    blocked tool_call closes; inputs add severity, outcome, parent_observation_id, event_timestamp and
    source_ordinal; rework becomes the repeat-run rule. No producer change. This amends the ruling's fact
    model for tool errors.
  - (b) Bring the producer back in line with the ruling. record_turn also emits a process_event per tool
    error or block (for example `tool:<name>` at severity error), and the formula still adds severity for
    the pass half. That needs a record_turn rule change and a PARSER_VERSION bump. The tool_call close
    outcome stays (it is required on a close row), so each tool error is then stored on two rows, which is
    what the deviation was recorded to avoid. In return, every friction source lands on one fact surface
    (process_events) and the rules read one observation_type.
  - (c) Keep the formula and the producer as they are, and accept the miscount.

  Section 2's SQL and vectors are written to (a)'s fact model. Under (b), the fact filter reads
  `tool:` process_events instead of tool_call closes, and the vectors' tool rows become process_event
  rows; the label and run logic is unchanged. For (a): each fact is stored once, as observed. For (b):
  the operator's own model and a single fact surface. Recommended: (a). Correction (2026-10-04): the
  telemetry producer's owners confirm the slice-3a deviation is deliberate and recommend that the
  classifier read tool_call close outcomes directly; (b) would double-record each tool error and force a
  PARSER_VERSION bump. That firms the earlier weak recommendation. The choice still sets the operator's
  ruling against the deviation, so it is not made here. A
  ratified Class A semantic change either way; parked.
- k3 Content-bearing patterns: gate and pre-commit failures (only in Bash output), workaround edits, file
  re-reads. Options: (a) the producer extracts a small closed set of signatures at write in slice 3b:
  `gate:<check>` and `precommit:<hook>` from Bash tool_result text, `edit:suppression_added` from
  Edit/Write input, and a path hash on Read tool_call opens, so the read stays over typed rows; (b) the
  read-side classifier pattern-matches telemetry_transcripts tool_result content, as W1-1's parked P1
  recommends, which keeps the producer transcript-pure; (c) leave them out of the MVP. Against (b): the
  verb would read free-text content, about 232 KB per session here against 65 typed fact rows, and
  fetch spilled blobs above 65536 bytes (e9). That is the read-time cost rec-4032 names as its own
  materialization trigger, and Decision 88 makes it a budget. Against (a): interpretation is baked in at
  write, so a rule fix needs a PARSER_VERSION bump and a re-send (supported: the producer is
  replayable), and it adds work to the Stop-hook pass W1-1's k1 is already budgeting. Recommended: (a)
  for the gate, pre-commit and suppression signatures, (c) for re-reads and self-contradiction until
  rec-4032's LLM trigger fires (an LLM job spends, so it is never started here). This contradicts W1-1's
  P1; W2 synthesis reconciles the two, and the operator decides. Parked.

Risk (known loss modes, not choices):

- R1 Pass counting. A literal build of e1 counts every hook pass whose name matches a signature. In
  this session's transcript all 3 process_events were passes, but no repo hook ran in it, so the volume
  is unmeasured (section 1). Covered by k2's pass half and v01, v14.
- R2 Invisible tool friction. A literal build over today's producer rows never sees a tool error, harness
  block or repeat run, so those count zero forever. Either k2 option closes it, (a) in the formula and (b)
  in the producer. Covered by v03, v06-v09, v13.
- R3 Unstable signature. A hook whose command quotes its script path (handoff_evidence_gate.sh here)
  falls back to its attachment's hookName, `PreToolUse:<tool>`, so its blocks land as
  `hook:PreToolUse.<tool>` (for example `.Bash`, or `.mcp__github__create_pull_request`, since its matcher
  also covers Monitor and four GitHub write tools; inferred from the matcher and hook_name, VP 4 shows the
  Bash case) and can only ever map to `unmapped_block` (e10). The fix is a producer rule (hook_name should strip quotes),
  rec-4026's slice 3b with a PARSER_VERSION bump. Named here for that owner; no rec is filed. The gap is
  wider than quoting: any hook whose command has no script token falls back the same way. This session's
  `hook:PostToolUse.Bash` came from a hook the repo does not register (it has no PostToolUse hook), so
  harness- and user-level hooks land on shared signatures too, and c2's registered-signature guard covers
  repo hooks only. Read unmapped_failure_share with that in mind. A read-side alternative exists: the
  scrubbed `metadata.command` (e9), at the cost of reading free text.
- R4 Unpinned harness denials. HARNESS_DENIAL_PREFIXES and HARNESS_INTERRUPT_SENTINELS are empty
  (rec-4026's UNPINNED list), so a harness permission denial is a plain tool error today, never blocked.
  `tool_blocked` undercounts until those prefixes are pinned from real transcripts.
- R5 Version drift in deltas. Back-validation compares friction before and after a fix. A rule edit
  between the two windows moves the totals with no change in behaviour, and so does a producer change:
  pinning R4's denial prefixes (a PARSER_VERSION bump) turns tool errors into blocks, which moves
  `tool_blocked` and, through v17, `repeated_tool_failure`. A delta is valid only at one
  `classifier_version`, one registry_version and one parser_version per producer; the response stamps are
  load-bearing (as registry_version is for W1-2).

Open (q1-q3 in the fixture; none is answerable from the repository):

- q1 Is an interrupt friction? A human stop is arguably the strongest friction signal, but the producer
  also writes a synthetic interrupted close for any tool_use without a result in a closed turn (e4), and
  the two are not told apart in `outcome` (only `metadata.synthetic`). v12 counts neither.
- q2 Who supplies precision? `unmapped_failure_share` measures coverage, not correctness. A mislabelled
  but mapped fact is invisible to it, so the read_all rung needs a label review: an operator sheet, or
  rec-4025's harness widened to sampled real sessions (a queue change, Decision 67). Neither exists.
- q3 Repeat key and threshold. Grouping by tool name alone merges unrelated Bash failures (87% of tool
  calls in this session were Bash). A finer key (a hash of the command or input) needs a producer field.
  The threshold 3 is the retired prompt's, unmeasured.

## 4. Consideration register (as authored in the fixture)

- planes: data_plane. The rules run inside the reader. What a control-plane fleet view may see is decided
  by rec-4141's allow-list: the two class totals and the generic labels are a closed vocabulary; the
  repo-specific labels and every signature are free text (a customer's hook script names), so they stay
  in the data plane unless the allow-list names them (Decision 209 cl.2b). The same holds for the hook
  `stderr_head` and `command` in a process_event's metadata (e9): the rules never read them, and the
  response never returns them.
- failure_signal: friction totals that move for a reason other than the friction they name: a pass
  counted, a new hook or gate whose blocks fall to the generic rule, or unrelated errors merged into one
  run. Metric `unmapped_failure_share`: the share of non-info process_events (error, warning or
  critical) that only a generic rule (r90, r91, r94) labels, per classifier_version. Source: the verb's response stamp; precision only from
  the c3 review (q2). Why not latency or a count: a wrong friction count is silent and skews T3.4's
  "telemetry delta proves fix" and rec filing at once, while a slow verb is loud.
- Goodhart guard: the obvious way to drive unmapped_failure_share to zero is a wildcard rule. A specific
  rule must name one exact signature (no `*`, `%`, `?` or `[`), and every specific signature must belong
  to a registered hook script or a registered check (criterion c2), so an unregistered or catch-all rule
  fails CI. A registered but never-emitted signature does not: r04 (`gate:validate_scope_boundary`) names
  a registered check, yet no producer emits a `gate:` signature until k3 resolves (VP 1), so r04 is
  pending k3 and dead today. Catching dead rules would need c2 to check signatures against a set the
  current PARSER_VERSION declares it emits, which no producer publishes yet.
- maturity: starts at read_all, meaning the operator reviews every label of the first sessions. read_all
  -> sampled at >= 20 consecutive reviewed sessions with zero mislabels; sampled -> spot_check at 0
  mislabels across the last 200 sampled labels; spot_check -> anomaly_triggered at >= 30 consecutive days
  with unmapped_failure_share <= 0.05 at one classifier_version. A classifier_version change is a new
  rule set; the maturity-ladder controller component decides whether it restarts the ladder. Seed values
  for that component to challenge.
- verification: c1 (the rules applied by session_friction_rollup pass these vectors on DuckLake, with the
  version stamp and per-label counts), c2 (the rules-file guard: no wildcard, every specific signature
  registered, generic labels counted as unmapped), c3 (the operator's read_all label review before any
  consumer reads the totals). All open.
- rollback: revert the rules file to the previous classifier_version, or remove the verb from the
  registry. Nothing is stored, so every total re-derives on the next read.
- edges: part_of T3.20 (rec-4026's goal is per-turn agent-friction capture, and its title names the
  friction classifier); depends_on T2.36 (the reader mechanism, rec-4024).

## 5. Boundary notes for W2 synthesis

- Reader verbs (W1-2, #1390): this item composes on its shared R4/R5 dedupe and partition binding, and
  answers its open question: the labels are rules data rendered into the verb (k1 (a)), so
  session_friction_rollup reads one telemetry table, not two. On merge, W2 should add
  `pwi-friction-classifier depends_on pwi-telemetry-reader-verbs`; the evaluator's L4 refuses an edge to
  a pilot item absent from this branch's fixture, so it is not added here.
- Capture producer wiring (W1-1, #1384): this item depends on the rows that wiring writes; on merge, W2
  should add `pwi-friction-classifier depends_on pwi-capture-producer-wiring`. Contradiction for W2: W1-1's
  P1 recommends gate/precommit signatures on the read side over tool_result rows; this report's k3
  recommends producer-side signatures. R3 (quoted hook paths) and R4 (empty denial prefixes) are
  producer rules in the same slice. Sequencing (from the producer's owners): any src/turn_capture/
  change named here (k3 (a), R3, R4, and k2 (b) if chosen) lands after their next producer plan (2a-1),
  as one PARSER_VERSION bump.
- Telemetry contracts: they define the friction classes rework and exception only, with no label below
  class (telemetry_observations.yaml:429), so this item's labels duplicate nothing there.
- Rec filing with dedupe: missing-gotcha is not a classifier label; it is that component's question of
  whether a friction pattern is already known. That component should file on label counts per
  classifier_version, never on raw signatures.
- Back-validation (T3.4): a delta is meaningful only at one classifier_version, one registry_version
  and one parser_version per producer (R5); a fix that adds a rule is a classifier change, not evidence of a behaviour change.
- Maturity-ladder controller: this item's rungs need a human label review (q2) that no other component
  supplies; the controller should treat a classifier_version change as a rung event.
- Goodhart register: the wildcard ban and the registered-signature check (c2) are this item's entry.
- Cost/egress: the rules read typed fact rows only (a few hundred per session); k3 (b) would move the
  read onto transcript content and blobs. rec-4032's own trigger (a Decision 88 measurement showing
  read-time egress) is the measured switch to materialization. W2 should reconcile it with T2.52:
  Decision 199's reversal condition routes materialization to T2.52 c1, and T2.52 c3's serving-leaf rule
  means the R4/R5 dedupe is inlined shared SQL, not a verb call. rec-4032's silver table and T2.52's
  materialization mode need one owner.
- Allow-list transport (rec-4141): the closed vocabulary (classes, generic labels, classifier_version) is
  the candidate set to cross; signatures and repo-specific labels are not.

## 6. Not done here (and why)

- No rules file, verb, test or contract code: REPORT-ONLY by brief.
- No telemetry_sessions amendment for k1 or k2, and no producer change for k3, R3 or R4: ratified Class A
  semantic changes and rec-4026's producer rules, parked for the operator.
- No rec filed, updated or closed (Decision 67; the brief's never-list). rec-4024, rec-4025, rec-4026,
  rec-4032 and rec-4141 are named as owners only.
- No LLM classification run or designed in detail: it spends, and rec-4032's trigger has not fired.
- No T2.36, T3.20 or T3.4 criterion text or status change.
