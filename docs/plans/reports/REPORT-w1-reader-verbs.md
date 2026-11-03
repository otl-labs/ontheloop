# REPORT: W1 component 2 - telemetry reader verbs (rec-4024, reader side)

REPORT-ONLY (Decision 86 cl.2). Planning artefact: `docs/plans/PLAN-w1-reader-verbs.yaml`.
Fixture rows: `pwi-telemetry-reader-verbs` in `docs/work-item-pilot/telemetry-feedback-loop.yaml`
(CD.45 pilot, provisional_v0). Nothing is built, filed, closed, flipped or ratified here.

## 0. Verdict

- The component is real and load-bearing. Decision 199 stores no session state, duration, roll-up,
  friction or cost: all of it is DERIVED AT READ by named reader verbs. Every other feedback-loop
  component (component 1's failure_signal, the friction classifier, back-validation, T3.3's anomaly
  agent, T2.36 c3's preflight health check) reads telemetry only through them.
- Seed boundary kept, with one sharpening. rec-4024 item (6) (its 2026-09-24 context, read through the
  `rec_by_id` named read) names the verbs: "session_state with as_of/idle threshold ->
  running/abandoned/terminal, session_rollup, friction_events rules-based, cost via static price map
  ... bounded by the session's day(session_started_at) partition; decide operational-verb vs T2.52
  c3/c6". The contracts' `populated_by: a named reader verb (rec-4024)` rows and the dedupe vectors'
  "rec-4024's reader verbs must independently pass" obligation agree with it. The component is the
  read MECHANISM (registry form, partition binding, generation-aware dedupe) plus the state, duration
  and roll-up verbs. The friction labels (rec-4032) and the price table (rec-4031) are other owners'
  inputs that compose on it.
- The mechanism does not exist, and registering verbs alone cannot reach it (VP 1). The telemetry
  tables are not registered (resolve_table_spec refuses them; registration is rec-4024's own slice-2
  obligation). A NamedRead binds exactly one table, so R4's join of the sessions open rows to the
  target table cannot be expressed through the registry's table binding (nothing mechanically refuses a
  literal second table name in verb SQL, but no verb does it and it would bypass resolve_table_spec). No verb carries a server-rendered, mandatory partition predicate.
  The `{hist}` placeholder already renders an append_only table's history, so binding one table is
  not the gap.
- Two ratified contract rules would derive wrong values if built literally (risks R1, R2): session state
  ignores resume-after-close, and session duration is measured from the ROOT start. A sub-agent session
  has neither its own start nor a close row: its open row carries the root start and the producer emits
  no child close. So its only real start and end are its telemetry_agents agent-run pair. Staged as
  contested k1. The ratified contract also has no abandoned state,
  which rec-4024 item (6) asks for and component 1's signal needs (k3). The contract is not edited here.
- One item fits the clause-3 grain (kind task; three criteria; one edge, part_of T2.36). The verbs share
  one failure mode (a wrong derived value), so splitting them would spend the 12-item cap on rows with
  one failure signal.

## 1. Evidence (each row re-derivable; VP step in brackets)

| id | fact | anchor |
|---|---|---|
| e1 | No NAMED_READS verb targets a telemetry table, and none of the four telemetry tables is registered (resolve_table_spec raises unknown ops table). NamedRead's fields are verb, table, sql, params, description, paginable: one bound table, no partition field. `{hist}` already renders an append_only history table [VP 1] | src/common/ducklake_scd2_schema.py:202 (the raise), :333-342 (NamedRead); src/common/ducklake_reads.py:139-141 (the substitution) |
| e2 | Session state rule: "absent a close row, the session is running; present, its outcome IS the session's terminal state". A resumed session "reuses session_id ... and appends a resume event" | docs/contracts/telemetry_sessions.yaml:188, 257-258 |
| e3 | Session duration formula: close.event_timestamp minus session_started_at [VP 4] | docs/contracts/telemetry_sessions.yaml:248 |
| e4 | session_started_at is the ROOT's start, carried by every row of the root AND all its sub-agent sessions [VP 4] | docs/contracts/telemetry-event-envelope.yaml:113-118 |
| e5 | Agent-run duration is paired open/close on agent_run_id, the envelope's span rule; session duration is not. The agent-run open is stamped with the spawn record's time and the close with the tool result's | docs/contracts/telemetry_agents.yaml:221; docs/contracts/telemetry-event-envelope.yaml:72; src/turn_capture/sessions.py:175-212 |
| e12 | The open row's event_timestamp "EQUALS session_started_at" (the root start), and the producer writes every open row, sub-agents included, at ctx.started with source_ordinal 0 for a child [VP 4]. Component 1 emits ONE root close per finalization, never one per session, with close ranked LAST on an (event_timestamp, source_ordinal) tie | docs/contracts/telemetry_sessions.yaml:71; src/turn_capture/sessions.py:144-146; #1384 report section 2 step 7 (head 18194019) |
| e6 | Read-side dedupe R4/R5 (generation retire, event collapse, model_call entity collapse); the conflict report is normative output | docs/contracts/telemetry-event-envelope.yaml:375-387 |
| e7 | The reader verbs must pass tests/telemetry/fixtures/dedupe_vectors.yaml independently of the stdlib oracle [VP 2] | tests/telemetry/fixtures/dedupe_vectors.yaml:5 |
| e8 | Every derivation is bounded to one session's calendar-day partition (answers Decision 81 cl.8); falsifier `derived-read-too-costly` pulls materialization forward from T2.52 c1 | Decision 199 cl.1 and reversal conditions (docs/DECISIONS.md, Decision 199 cl.1 and its reversal-conditions block) |
| e9 | A held session_id decodes to the root session_started_at (decode_time_prefix) [VP 3] | src/telemetry/identity.py:214 |
| e10 | T2.52 is the analytical-aggregate verb class (c3) with a measured response-size ceiling (c7) and the D88 egress budget (c5) | docs/ROADMAP-PLATFORM.yaml T2.52 |
| e11 | rec-4024 item (6): session_state with as_of/idle threshold -> running/abandoned/terminal; item (2)'s "max parser_version per (session_id, producer)" reader rule predates and is superseded by Decision 207's R4/R5 | rec-4024 context (rec_by_id); Decision 207 |

Measured (local only; no production catalog read):

- Dedupe in SQL: the two sketches in section 2 (survivors, conflicts) pass 17 of 17 committed vectors
  (17 table checks: 15 observations, 2 agents) on DuckDB 1.5.4, surviving event_ids and conflicted
  grain keys both exact [VP 2]. The R5 layered rule needs one join (V* from the sessions open rows) and
  two windows. VP 2 binds `{partition}` to TRUE (every vector is one partition), so the partition
  predicate itself is not tested there; VP 3 covers partition pruning. The vectors compare surviving
  event_ids, and R5b keeps the same event_id whichever parser_version wins, so a mutant that keeps the
  LOWEST version still passes 17/17: VP 2 is weaker evidence for R5b's version order than for R5a and
  R5c. The sketch is correct by inspection, and criterion c1's DuckLake test compares survivors by
  (event_id, parser_version) to close the gap. A second gap of the same kind: deriving V* from every
  telemetry_sessions row instead of only `event_kind = 'open'` also passes 17/17, yet it breaks R8 (a
  non-replayable producer emits no open marker, so a bump retires nothing): a non-replayable producer
  with close or annotate session rows at two parser versions would get a V* and lose its older rows. The
  sketch filters on open rows correctly; c1's DuckLake test carries that case. The vectors are normative
  and are not edited here; the case is a note for their owner.
- Partition binding on a local DuckLake catalog (pinned extension, UTC, inlining off), 7 calendar days x
  10 sessions x 100 rows, each day written as 5 flushes that interleave all 10 of its sessions (35
  Parquet files), session ids derived by src/telemetry/identity.py: a per-session read filtered by
  session_id alone reads its own day's 5 files, and adding the decoded year/month/day triple also
  reads 5 [VP 3]. The claim is that a session_id alone bounds the read to its own day's files, not to
  one file: the ULID time prefix carries the root start, so files from other days never match.
  Within a day, files that interleave sessions are all read. The triple is cheaper catalog filtering
  on Neon (unmeasured, q1). A 3-day window reads 15 files, linear in days. The production flush
  pattern (files per day, sessions per file) decides the real cost and belongs with q1.
- Scratch run at larger scale (not a VP step): 1.12M rows over 14 days, one 80k-row file per day: a
  per-session roll-up took about 9 ms locally and read its day's 1 file; a 7-day window read 7. Local
  latency says nothing about S3 and Neon round trips, which dominate in production (q1).

## 2. Verb design (what this item stages)

Mechanism:

1. Registry form. NamedRead gains an event-journal binding: placeholders for the four append-only
   tables (history only, no current projection; the tables are registered first, rec-4024 slice 2), more
   than one table per verb (R4's join), and a MANDATORY partition predicate rendered server-side.
   Prerequisite (Decision 128): src/common/ducklake_scd2_schema.py is near its 500-SLOC limit (rec-4024
   records 474/500), and the registry form lands there and in ducklake_reads.py, so the implementation
   plan decomposes it into a facade package first, never a budget raise. Per-session verbs take only `session_id`; the server decodes the calendar-day triple
   from its ULID prefix (e9), so no caller can widen the scan. NAMED_READS_VERSION bumps; `describe`
   lists the new class. No caller SQL crosses the boundary (Decision 84 I-3).
2. Shared dedupe. Every telemetry verb composes one rendered R4/R5 block per table it reads, then
   derives. Every response stamps `registry_version`, the V* it used per (producer, session) and the
   conflicted grain keys R5b requires as output (e6). The sketch, verified against the vectors (VP 2;
   `{partition}` is the server-rendered predicate, `{sessions}` and `{table}` the bound tables):

```sql
WITH gen AS (
  SELECT producer, session_id, max(parser_version) AS v_star
  FROM {sessions} WHERE {partition} AND event_kind = 'open' GROUP BY producer, session_id
),
live AS (
  SELECT t.* FROM {table} t LEFT JOIN gen g USING (producer, session_id)
  WHERE {partition} AND (g.v_star IS NULL OR t.parser_version = g.v_star)
),
ev AS (
  SELECT * FROM live
  QUALIFY row_number() OVER (PARTITION BY producer, event_id ORDER BY parser_version DESC, created_timestamp) = 1
)
SELECT event_id FROM ev
QUALIFY observation_id IS NULL OR row_number() OVER (
  PARTITION BY observation_id
  ORDER BY CASE producer WHEN 'claude_code' THEN 0 WHEN 'litellm' THEN 1 ELSE 2 END,
           event_timestamp DESC, created_timestamp, producer, event_id) = 1
```

   The conflict report, computed over the partition's rows BEFORE retirement (as the oracle does,
   tests/fixtures/telemetry_dedupe_reference.py:47-64):

```sql
SELECT producer, event_id, parser_version FROM {table} WHERE {partition}
GROUP BY producer, event_id, parser_version HAVING count(DISTINCT content) > 1
```

   The model_call entity collapse applies only to model_call rows (in production the guard is
   `observation_type = 'model_call'`; the vectors mark them by observation_id). `content` stands for
   every stored column except the write-time stamp and the provenance-only columns Decision 207 excludes
   from the grain's compared content (for telemetry, created_timestamp and producer_version), so a re-send
   that differs only in producer_version is never reported as conflicted. In production the compare is
   ONE row value (a struct of every non-excluded column, `count(DISTINCT row(...))`), never a
   per-column DISTINCT: DuckDB ignores NULLs in a per-column count, so rows `('x', NULL)` and `('x', 'y')`
   would count as one value and hide a NULL-versus-value conflict, which Decision 207 treats as a real
   conflict.
3. Response shape. Derived rows only: one row per session (or agent run), never raw observation rows
   and never transcript content. A 1000-turn session holds about 55k rows (component 1, section 1),
   far past a Function URL response; derived rows keep every response small (Decision 88) and keep
   free text inside the data plane (Decision 209 cl.2a; the reader runs there).

Verbs. The names come from the contracts' `derived_by`. rec-4024 item (6) uses older shorthand
(`session_state`, `session_rollup`, `friction_events`); the contract names win, because the field-level
derivation pointer is where field semantics live (Decision 86):

| verb | derives | input owner |
|---|---|---|
| session_state_and_duration | state (running, abandoned, terminal per k3), outcome, duration_seconds | this item (rules per k1, k3) |
| session_process_event_rollup | process_event_total, steps_completed_total | this item |
| session_friction_rollup | rework_total, exception_total | this item; labels from rec-4032 (friction classifier component) |
| agent_run_state_and_duration | agent-run state, duration | this item |
| agent_run_token_rollup | tokens_input_total, tokens_output_total over the child's model_calls | this item |
| model_call_cost_estimate | cost per model_call | rec-4031 (price table), composes this item's dedupe |
| telemetry_health_count | T2.36 c3's preflight probe: telemetry_sessions rows over a bounded recent lookback, or a catalog row count | this item (operational counter) |
| sessions_window (proposed, k2) | one derived row per root session over at most 7 days | T2.52 c3 if k2 (b), this item if k2 (a) |

## 3. Settled / contested / risk / open

Settled (consistent with a Decision, a contract or measured; s1-s3 in the fixture):

- s1 Boundary: read side only. Decision 199's derived-at-read verbs over the four tables; the writer verb
  and tables are T2.36 (rec-4024 slice 2); labels (rec-4032) and prices (rec-4031) compose on this
  mechanism.
- s2 The registry needs an event-journal form (e1, VP 1). The telemetry tables are unregistered, a
  NamedRead's table binding covers one table while R4 needs two (the sessions open rows and the target), and no verb
  carries a server-rendered, mandatory partition predicate.
- s3 The read model is feasible as designed: R4/R5 is one SQL statement passing 17/17 vectors (VP 2), and
  a session_id alone bounds the read to its own day's files (5 of 35 locally, VP 3).

Contested (evidence on both sides, options listed; k1-k3 in the fixture):

- k1 State and duration rule. The contract (e2, e3) says no close row means running and measures
  duration from session_started_at. Against, for roots: component 1 emits one close row per finalization
  and a resume row after it, so "a close row exists" reads terminal for a session that is running again.
  Against, for sub-agent sessions (e12): the child's open row carries the ROOT start, and no child close
  row is ever emitted, so no session-level rule can time or close a child. Its only real start and end
  are its telemetry_agents agent-run open and close (e5), which belong to the child's own session_id
  (envelope R6). Options: (a) amend telemetry_sessions. For a ROOT, state comes from the latest
  lifecycle row among open, resume, compact and close (annotate excluded; running unless it is close),
  outcome from that close, and duration = latest close minus session_started_at (the old formula,
  pinned to the latest close). For a SUB-AGENT session, state and duration come from its agent-run
  open/close pair (agent_run_state_and_duration). (b) keep the text and forbid resume after close (a
  resumed session becomes a new session with execution_attempt + 1). This contradicts the ratified
  contract itself, where a resumed session reuses session_id and appends a resume event
  (telemetry_sessions.yaml:257-258), as well as component 1's per-finalization close. (c) keep the text
  and document the child gap: child duration is NULL and child state is running forever. (d) give
  children their own start and close: amend telemetry_sessions.yaml:71 so a child's open row carries
  the child's first event time, and emit child close rows. This needs a record_turn rule change with a
  PARSER_VERSION bump (turn-capture owner). Recommended: (a); it needs no producer change. Two semantics
  inside (a) must be picked knowingly, and c2's golden fixtures pin them: (i) "latest" orders by
  event_timestamp, then source_ordinal (the envelope's read-side ordering aid), then close ranked LAST
  on a tie, then event_id. This is the same rule component 1 states (e12): a session resumed and ended
  with no new prompt anchors its close on the resume record itself, so any other tie-break reads a
  cleanly ended session as running. The order runs over the lifecycle rows of every producer that writes
  telemetry_sessions rows for the session, each after its own R5 dedupe. (ii) a root's duration is
  wall-clock and includes any idle gap between a close and a later resume; the alternative sums the
  open-to-close segments as active time. Recommended: (i) as stated; for (ii), wall-clock for
  duration_seconds, with active time left to a later field if a consumer asks for it. A ratified Class A
  semantic change, so the operator decides; parked.
- k2 Window verbs. Component 1's unfinalized_session_share, T3.3's daily anomaly baseline and T3.4's
  telemetry delta all need many sessions over a time window. Governing facts:
  - Decision 199 cl.1 bounds every state and duration derivation "to a session's single
    day(session_started_at) partition". A window verb reads up to 7 single-day partitions, one per
    session it derives. cl.1 attaches to that read shape, not to the roadmap item that hosts the verb,
    so the same reading of cl.1 is needed under every option below. The reading to confirm: "a bounded
    window of per-session derivations, each reading only its own session's single-day partition,
    satisfies cl.1". The only window routes that need no such reading are (c) below and
    materialization, and materialization needs Decision 199's `derived-read-too-costly` reversal to fire
    ("re-decide via /plan"; it names T2.52 c1 as where to pull materialization forward from).
  - T2.52 is `deferred_post_mvp`, and Decision 93 says no live item (not_started or in_progress) may
    depend_on a deferred_post_mvp item. T3.3 and T3.4 are live.
  - T2.52's progress note records its trigger: "ACTIVATION TRIGGER: a governed dataset exists that a
    human or an analysis agent actually needs to read in aggregate -- concretely T2.51 reactivates, OR
    telemetry lands on DuckLake (T2.36), OR data-quality coverage becomes non-zero ... Reactivate by
    restoring status -> not_started." Which T2.36 milestone "lands" means is the operator's reading:
    c1 (tables provisioned), c2 (telemetry flowing through the writer) or T2.36 met. T2.36's own name,
    "telemetry re-lands on DuckLake", favours c2 or T2.36 met, but that is an inference, not a settled
    fact.
  - This item is part_of T2.36 and is buildable at T2.36 c1 (plan constraints).

  Options:
  - (a) One `sessions_window` verb here, returning one derived row per root session for at most 7
    calendar days (paginable), with every share or rate computed by the consumer. It ships with this
    item, needs the cl.1 reading above, and keeps T2.52 deferred until its own trigger fires. Its
    response schema and ordering are shaped to T2.52 c3 so T2.52 can adopt the verb when it reactivates.
    No depends_on edge to T2.52 exists, so Decision 93 is not engaged.
  - (b) When the trigger fires, the operator restores T2.52 to not_started (a status change, never made
    here), and sessions_window is built as a T2.52 c3 verb. It needs the same cl.1 reading. It is later
    than (a) under every reading of "lands": T2.52 c7 ("measure before designing" the response-size
    ceiling) and c5 (D88 egress) bind sessions_window itself and come before its design, whereas under (a)
    the same measurement is this item's c3, taken after the build and before any consumer depends on the
    verb. Under the c2 reading it is later again, because (a) is buildable at c1 while (b) waits for c2
    and then c7; under the T2.36-met reading, also because this item is inside T2.36. "c3 first" would be
    a sequencing preference inside a whole-item reactivation (Decision 93: all of c1-c7 become live).
  - (c) Consumers loop the per-session verb, one round trip per session. It needs no cl.1 reading, but
    it multiplies invocations and catalog round trips (Decision 88).

  Weighing: round 2 recommended (b) on three grounds: the recorded trigger, the same timing, and no new
  annotation. Rounds 3 and 4 removed two of them. The cl.1 reading is needed under both options, and (b)
  is later than (a) under every reading of "lands". T2.52's whole-item reactivation is NOT a cost of (b)
  alone: its trigger fires at the same T2.36 milestone whichever option is chosen, so it follows from
  the operator's reading of "lands", not from k2. The one exception is if the operator treats (a) as
  already serving the trigger's aggregate need and keeps T2.52 deferred; then (a) takes T2.52's first
  recorded tenant, which is the ownership cost below. This report assumes the trigger is applied as
  written. What remains for (b) is ownership: analytical verbs belong to T2.52, and the recorded trigger
  is its sanctioned path. Recommended: (a), on timing, which holds under every reading, shaped for T2.52
  adoption, with the cl.1 reading confirmed by the operator. (b) remains the ownership route if the
  operator wants analytical verbs only inside T2.52. On the response-size question both
  share: about 40 root sessions a day x 7 days is about 280 one-row-per-session rows, far under a 6 MB
  Function URL response. That is an estimate, not c7's measurement, and this item's c3 measures response
  bytes before any consumer depends on the verb. rec-4024 item (6) leaves exactly this open ("decide
  operational-verb vs T2.52 c3/c6"). No status change is made here. Parked.
- k3 Abandoned state. rec-4024 item (6) asks for running/abandoned/terminal from an `as_of` and an idle
  threshold. The ratified contract has only running and terminal, so under both the contract and k1
  (a) a root session that never writes a close row (a crash, a killed process, a reclaimed container)
  reads running forever. Component 1's unfinalized_session_share (latest lifecycle row not close, 24 h
  after the last event) is exactly abandoned with a 24 h threshold. Options: (a) amend telemetry_sessions
  with a derived abandoned state: not terminal, and `as_of` minus the session's last event_timestamp
  above a governed idle threshold (a resume moves it back to running; nothing is stored); (b) keep
  running only, and let each consumer apply its own idle cut, so component 1 and T3.3 can disagree on
  the same session; (c) put the threshold as a caller parameter, with the same disagreement risk and an
  unbounded input. Scope: under k1 (a), abandoned applies to ROOT sessions by their lifecycle rows. A
  sub-agent session takes its state from its agent-run pair, so it is abandoned only when its agent-run
  open has no close past the same threshold. Applied to a child's session rows instead, every sub-agent
  would read abandoned 24 h after its last event, because no child close row exists (e12). For (a): one
  definition for every consumer. Against: the threshold value has no measurement yet (long-idle project
  threads, component 1 section 4). Recommended: (a), with the threshold in governed config, seeded at
  component 1's 24 h. A ratified Class A semantic change; parked.

Risk (known loss modes, not choices):

- R1 Child-session state and duration. A literal build of e2 and e3 against today's producer reads every
  sub-agent session as running forever with a NULL duration, because no child close row is emitted
  (e12). If child close rows are ever emitted, e3 overstates each child's duration by its spawn offset
  from the root start, a plausible value nothing downstream can detect. Covered by k1 and by c2's
  child-session fixture. Under k1 (a), the agent-run pair is exact for a synchronous child. For a
  background (async) child, the close is the parent-stream record carrying the completion notification
  (src/turn_capture/streams.py:410-413). I infer, without checking a real transcript, that it can lag
  the child's own last event while the parent is mid-turn, so the pair is an upper bound there. c2's
  golden fixtures include an async child, and the verb may bound an async child's end by its own last
  event.
- R2 Resume-after-close state. A literal build of e2 reads finalized-then-resumed sessions as terminal,
  although the contract itself appends a resume event under the same session_id
  (telemetry_sessions.yaml:257-258). The same ambiguity component 1 hit in its failure_signal (its G1);
  covered by k1 and c2.
- R3 Production cost unknown. Decision 199's own falsifier is a consumer latency bound, and no consumer
  has named one. c3 makes the measurement a precondition of any consumer depending on a verb.

Open (q1-q2 in the fixture; none is answerable from the repository):

- q1 Which consumer's latency bound tests Decision 199's `derived-read-too-costly` falsifier, and what are
  the verbs' p95 latency and per-call catalog egress on the production Neon catalog? Unmeasured: no
  credentials in this session, and the local run says nothing about S3 and Neon round trips.
- Closed (round 1's q2): rec-4024's own scope was read through `rec_by_id` (e11); it matches the
  contracts' populated_by set, and its abandoned state is now k3.
- q2 Is rec-4025's derived-layer harness the right shadow re-derivation source for the failure_signal,
  or should the reader own a production raw-rows path? rec-4025's recorded scope (read through
  `rec_by_id`) is a hermetic golden-fixture reconciliation, a smoke replay and a T3.2 production ASSERT
  over a synthetic project_id; it does not cover shadow re-derivation over sampled real sessions. A
  "yes" therefore means widening rec-4025, a queue change the operator decides (Decision 67). Until q2
  is resolved, the signal is partial: CI's dedupe vectors plus the conflicted-key stamp in every response
  cover the dedupe leg only, and state, duration and roll-ups have no runtime check. The stdlib oracle is a test fixture
  (tests/fixtures/telemetry_dedupe_reference.py) and cannot run in production (Decision 84 I-3).

## 4. Consideration register (as authored in the fixture)

- planes: data_plane. The reader runs in the data plane; any fleet view over these verbs crosses to a
  control plane only through rec-4141's allow-list (allow-list transport component).
- failure_signal: verb_rederivation_mismatch_rate, the share of sampled sessions whose verb output
  (state, outcome, duration, roll-ups) differs from an independent re-derivation over the same
  session's raw rows. Source: rec-4025's derived-layer harness in shadow mode, which needs rec-4025
  widened (q2); until then only the dedupe leg has a runtime read (below); CI's dedupe vectors
  cover the R4/R5 leg on every change. Why this and not latency: a slow or failing verb is loud (a 500
  or a timeout at the consumer), while a wrong derived value is silent and skews every consumer at once:
  component 1's signal, the classifier's counts and T3.4's "telemetry delta proves fix". The conflict
  count stamped in every response (section 2) is a second, cheaper runtime read of the dedupe leg.
- maturity: starts at read_all, meaning every verb response for the first sessions is shadow-compared.
  read_all -> sampled at >= 50 consecutive sessions with zero mismatches; sampled -> spot_check at 0
  mismatches across the last 200 sampled sessions; spot_check -> anomaly_triggered at >= 30
  consecutive days with zero mismatches and zero conflicted grain keys. Seed values for the
  maturity-ladder controller component to challenge.
- verification: c1 (event-journal registry form, server-decoded partition, shared dedupe passing every
  vector on DuckLake with survivors compared by (event_id, parser_version) and the conflict report), c2
  (derivation verbs on golden fixtures under the operator's k1/k3 resolution, children and
  resume-after-close included; c2's text is rewritten if option (a) is not chosen), c3 (production latency and egress measured against
  a named consumer bound before any consumer depends on a verb; review method). All open.
- rollback: remove the telemetry verbs from NAMED_READS and bump the registry version. Reads have no side
  effects and stored rows are untouched.
- edges: part_of T2.36 (rec-4024 is T2.36's slice carrier; T2.36 c3 is a reader-side criterion).

## 5. Boundary notes for W2 synthesis

- Component 1 (capture producer wiring): its failure_signal source is the sessions_window verb (k2,
  wherever it lands) with the latest-lifecycle-row state rule and close-last tie (k1), which both
  components now state identically; its unfinalized_session_share is the abandoned
  share under k3 (a) with a 24 h threshold, so the two definitions should be one. W2 (#1402, merged) staged the pilot edge the
  other way, `pwi-telemetry-reader-verbs depends_on pwi-capture-producer-wiring` (the verbs read the rows
  capture lands), and its edge set supersedes the reverse edge this report suggested earlier. No edge is
  added here.
- Ownership overlap: W2 parks P4 for the operator, the split of the reader verbs and the
  ducklake_scd2_schema.py Decision 128 decomposition between the Telemetry project's slice 2b and this
  item. This report stages the design either owner would build; it does not decide P4.
- Merge order: resolved. #1384 and components 3-10 merged first; this PR's item was appended to the
  fixture they left, re-rendered with render(parsed) and re-checked (L1-L6), so the fixture holds 10 items.
- Friction classifier (rec-4032): owns the labels table and its form. session_friction_rollup is built
  on this item's dedupe and binding; whether the labels are a DuckLake table joined in the verb or
  bundled registry config is the classifier's call, and it decides whether that verb reads one table
  or two. Component 3 (#1394, merged) chose rules data rendered into the verb (its k1 (a)), so
  session_friction_rollup reads one telemetry table. Both items are now in one fixture; the edge
  `pwi-friction-classifier depends_on pwi-telemetry-reader-verbs` is left for W2, since it changes
  component 3's item.
- Back-validation (T3.4): "telemetry delta proves fix" is a before/after comparison over windows, so it
  needs sessions_window (k2 (b) via T2.52, or k2 (a)). Deltas must be read at one registry_version; a version bump between the
  windows makes the delta meaningless, so the response stamp is load-bearing.
- Maturity-ladder controller: this item's rungs are measured by shadow re-derivation (q2), not by
  telemetry it emits itself. The controller needs the harness to report per verb.
- Decision 128: the implementation plan for this item decomposes src/common/ducklake_scd2_schema.py into
  a facade package before adding the registry form (section 2, item 1).
- Cost/egress budget: Neon catalog egress per verb is unmeasured (q1). The sessions_window range is the
  one read shape whose cost scales with the caller's parameter; it is capped at 7 days.
- T2.36 c3 (preflight telemetry health check) is a reader consumer. Its probe is an operational counter
  verb owned by this item (section 2 table), never sessions_window. Its PASS condition must be "tables
  populated", so it cannot read only today's partition: rows land in the partition of the day their ROOT
  session started (Decision 199 cl.2), so today's partition is empty after 00:00 UTC until a new root
  session starts, even while yesterday's sessions keep writing. Either a bounded recent lookback (today
  and yesterday, two partition reads, one call) or a catalog-level row count that reads no data files
  meets it; the T2.36 c3 owner picks. Under k2 (b), a sessions_window probe would make T2.36 c3 depend on a T2.52 verb
  whose trigger is T2.36 landing, which is a cycle and, while T2.52 is deferred, a Decision 93 breach.
  Per Decision 88 (ii) preflight calls the probe once, from the warm-up, never per gauge.
- One rule, rendered once: named verbs are serving leaves (T2.52's intent: "a verb never invokes
  another verb"). So sessions_window, wherever it lives, cannot call session_state_and_duration. Both
  verbs render the same SQL fragment for the R4/R5 block and the k1/k3 state rule, and both are tested
  on the same golden fixture data, so component 1's "one rule" guarantee rests on one copy. Under k2 (a)
  sessions_window is this item's verb and c2 covers it. Under k2 (b) the fixtures are shared DATA only:
  the sessions_window run belongs to T2.52's own test, and this item's c2 covers only this item's verbs,
  so no T2.36 criterion waits on a T2.52 verb.

## 6. Not done here (and why)

- No verb, registry, test or contract code: REPORT-ONLY by brief.
- No telemetry_sessions contract amendment for k1 or k3: ratified Class A semantic changes, parked for
  the operator.
- No rec filed, updated or closed (Decision 67; the brief's never-list). rec-4024, rec-4025, rec-4031
  and rec-4032 are named as owners only.
- No T2.36 or T2.52 criterion text or status change.
