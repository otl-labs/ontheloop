# REPORT: W1 component 1 - capture producer wiring (record_turn -> writer, rec-4026 slice 3b)

REPORT-ONLY (Decision 86 cl.2). Planning artefact: `docs/plans/PLAN-w1-capture-producer-wiring.yaml`.
Fixture rows: `pwi-capture-producer-wiring` in `docs/work-item-pilot/telemetry-feedback-loop.yaml`
(CD.45 pilot, provisional_v0). Nothing is built, filed, closed, flipped or ratified here.

## 0. Verdict

- The component is real and correctly seeded, but NARROWER than "record_turn -> writer": the writer
  half does not exist yet. It is the producer-side runner only (hooks, pass orchestration, cursor
  persistence, chunked transport client). The writer verb, telemetry tables and project registration
  stay with T2.36 / rec-4024 and enter the fixture as a `depends_on: T2.36` edge, not as a pilot item.
- The component is BLOCKED on T2.36 (writer verb unbuilt, VP 2) and gated by three pre-write
  obligations it does not own: T2.36 c5 (GC guard before the first telemetry write), rec-4101 (orphan
  monitor before production rows, PLAN-telemetry-contract-risk-amendments.yaml:352-355) and T3.20 c8
  (smoke-first).
- One item fits the clause-3 grain (kind task; three criteria; two edges). Splitting it would spend
  the 12-item cap on rows that share one failure signal.

## 1. Evidence (each row re-derivable; VP step in brackets)

| id | fact | anchor |
|---|---|---|
| e1 | record_turn is pure: "No file writes, no network, no writer call"; the caller persists next_cursor | src/turn_capture/record_turn.py:6-7 |
| e2 | No caller exists: zero record_turn references under .claude/, scripts/, src/lambdas/; no Stop, SubagentStop or SessionEnd hook is configured [VP 1] | .claude/settings.json |
| e3 | The writer has 17 actions, none telemetry; the transport sends ONE record per call [VP 2] | src/lambdas/ducklake_writer/handler.py:241; scripts/ops_portal/writer_transport.py:127 |
| e4 | append_events (kernel) exists but defers OCC retry, row caps, project_ref/tenant resolution to rec-4024 | src/telemetry/append.py:14-15 |
| e5 | Handed-on list for 3b: Stop, SessionStart catch-up, SessionEnd hooks; cursor persistence and lost-cursor runbook; writer-verb writes; project_ref registration; gate/precommit signatures; attachment system transcripts; smoke writes behind T3.20 c8 | docs/plans/PLAN-telemetry-turn-capture-core.yaml:640-642 |
| e6 | A Stop hook exiting 2 continues the turn, so the live session's latest turn lags one turn until the next prompt or session_final | docs/plans/PLAN-telemetry-turn-capture-core.yaml:629-631 |
| e7 | project_ref is PINNED once at the root's first record; never a re-read of repository config; a producer without it DEFERS | docs/contracts/project-id.yaml:47-51 |
| e8 | rec-4026 owes exact-ms session_started_at and root project_ref handoff, persists project_ref with the cursor, and should not write production rows before rec-4101's monitor | docs/plans/PLAN-telemetry-contract-risk-amendments.yaml:352-355 |
| e9 | Identical re-send under an existing grain key is a no-op; differing content is rejected loudly | Decision 207 (docs/DECISIONS.md:207) |
| e10 | Transcript content over 65,536 bytes spills to the blob port (writer's job) | docs/contracts/telemetry_transcripts.yaml:172 |

Measured (synthetic trees, no real transcript read; VP 3 reproduces the warm leg):

| turns | lines | bytes | cold pass s | warm pass s (one new turn) | rows cold | rows warm |
|---|---|---|---|---|---|---|
| 25 | 550 | 1.3 MB | 0.09 | 0.17 | 1321 | 55 |
| 100 | 2200 | 5.0 MB | 0.37 | 0.69 | 5446 | 55 |
| 400 | 8800 | 20.0 MB | 1.67 | 3.41 | 21946 | 55 |
| 1000 | 22000 | 49.8 MB | 3.84 | 8.80 | 54946 | 55 |

Each turn is a prompt, 10 Bash tool calls with 2 KB results, and a final text block. A warm pass costs
about 0.4 ms per line of the WHOLE tree to emit one turn's 55 rows: it parses the full tree and the
cursor-truncated tree (record_turn.py:130-136). Wire size (render_rows_json) is about 52 KB per such
turn; a 100-turn cold catch-up is 3.2 MB of transcript rows plus 2.1 MB of observations, so batches
must be byte-chunked under the writer's request limit (AWS documents 6 MB for a synchronous Function
URL request; external, not re-verified here).

## 2. Runner shape (the design this item stages)

1. Trigger: Stop (every completed response), SessionStart (catch-up for trees whose cursor lags their
   file) and SessionEnd (session_final=True). SubagentStop is not needed: a child's rows are emitted by
   the root pass once the parent holds its completion.
2. One pass per root tree under an exclusive per-tree advisory OS lock (flock/fcntl), which the kernel
   releases when the holding process dies, never a lockfile, so a pass killed mid-run or at its budget
   cannot wedge the next one (plan-critique r2, C8); a second concurrent Stop or SessionStart
   pass exits 0 immediately (the next Stop catches up). The SessionEnd pass never does that, because
   no Stop follows it: it waits for the lock with a bound W, and if the bound expires it exits 1
   (loud), never 0 (plan-critique r1, M2). W comes out of the same SessionEnd budget as the finalize
   pass P (W + P <= the configured SessionEnd timeout <= 60 s; see k1), so W is a fixed fraction of
   that timeout, never the whole of it (plan-critique r2, C1). The cursor write is monotonic (never replaces a later
   lines_consumed with an earlier one).
3. Send order: observations, transcripts, agents, then the single sessions batch (record_turn.py
   CaptureResult.batches), each table byte-chunked; a chunk failure stops the pass.
4. Persist next_cursor only after every chunk is acknowledged. A failed pass leaves the cursor where it
   was; the next pass recomputes the same rows from the transcript and re-sends them, and Decision 207
   makes the overlap a no-op. The transcript is the replay source, so no outbox exists (Decision 84
   I-4 holds).
5. Exit codes: 0 on success or lock contention; 1 (non-blocking, stderr names the table, chunk and
   status) on any failure. NEVER 2: on Stop, exit 2 forces the turn to continue (e6). The runner writes
   NOTHING to stdout on any event: Claude Code adds SessionStart stdout to the model's context, so any
   output from the catch-up pass would be injected into every session and captured back as rows.
   Diagnostics go to stderr or the runner's diagnostics file (plan-critique r3, D4).
6. Pins: project_ref and billing_shape are passed in from the runner's configuration ONCE, at the
   pass that creates the cursor, and travel in the cursor (record_turn checks them via check_pins).
7. Finalize marker: the SessionEnd pass (session_final=True) emits a root telemetry_sessions row with
   event_kind close. The contract already accepts it (docs/contracts/telemetry_sessions.yaml:48), but
   record_turn emits none today: a finalized tree yields only open/resume/compact rows (VP 7). Adding it
   is a record_turn rule change, so it carries a PARSER_VERSION bump. The failure_signal depends on it.
   Identity: ONE close row per finalization, never one per session. Its ref is a role_ref of the last
   root-stream record with a uuid at that finalize ('<uuid>#0/close', refs.py:23); trailing ignored or
   uuid-less records (streams.py:20, :174) never anchor it. Its event_timestamp and source_ordinal are
   that anchoring record's, and readers order lifecycle rows by (event_timestamp, source_ordinal) with
   close ranked LAST on a tie: a session resumed and ended with no new prompt anchors its close on the
   resume boundary record itself, so a close-first tie-break would flag a clean session (verification
   r3, H2, scenario I). Outcome: the contract stores success | failed | cancelled on the close row
   (telemetry_sessions.yaml:184-193). It is derived from the transcript (for example, an interrupted
   last turn reads cancelled, otherwise success), never a constant, which would make the stored column
   vacuous for back-validation and the friction classifier (plan-critique r2, C6), and never taken from
   SessionEnd hook input. SessionEnd's own reason values (clear, resume, logout, prompt_input_exit,
   other) do not map to failed or cancelled anyway, and hook input is not transcript content, so a retried
   or duplicate SessionEnd with a different reason would send the same ref with different content, which
   D207 rejects loudly, and step 8's transcript-only re-derivation could not reproduce it (plan-critique
   r1, M1). Owner: this rule and the R2 fix are both src/turn_capture rule changes owned by rec-4026; the
   close-row rule ships in slice 3b's PR (plan-critique r1, M7). A session-level ref would
   already sit in the prior set after the first finalize (record_turn.py:132-138), so a resumed and
   re-finalized session would emit no second close row and read as unfinalized (verification r2, G1,
   scenario E).
8. Runner-side conformance: at finalize the runner holds the whole transcript, so it re-derives from
   that transcript alone (no emission history; the cursor holds positions only, D84 I-4): finalize
   (session_final=True) at each SessionStart:resume boundary, a Stop cut at each later prompt, a final
   cut at EOF, then compare the union of per-cut emissions with one full parse; any ref missing is a
   violation. This is NOT the existing walk: test_real_transcript_conformance.py's incremental test
   finalizes only at its last cut (:338-352) and reports 0 missing on an R2-defective tree, so it is
   blind to R2 (verification r3, probe 2: 6 missing refs on the defective tree, 0 once patched, 0 on
   clean trees, 1.2 s on a 1,231-line tree). Running this check per session is this item's obligation
   (c2); if dropped, it is a gap the maturity-ladder component inherits.

## 3. Settled / contested / risk / open

Settled (consistent with a Decision, a contract or the 3a plan; mirrored as s1-s3 in the fixture):

- s1 Boundary: producer side only. Writer verb, tables, project registration = T2.36 / rec-4024 (e3,
  e4).
- s2 Data plane only (Decision 209 cl.2a: agent transcripts never leave the data plane). The hook
  writes to the operator-owned writer; the allow-list transport (rec-4141) never carries these rows.
- s3 Failure semantics: cursor-after-ack plus transcript replay plus Decision 207 no-op re-send (e1, e9;
  runner steps 2-5). Loud failure = non-zero non-2 exit with the cursor unadvanced.
  Precondition: record_turn's incremental emission equals one full parse. Merged 3a code breaks that for
  resumed-after-finalize sessions (R2), so s3 holds only once R2 is fixed by the turn-capture owner.

Contested (evidence on both sides, options listed; k1-k2 in the fixture):

- k1 Synchronous Stop hook vs detached pass. For: loud, attributable failure in the session (D84 I-4
  spirit). Against: per-pass cost grows linearly with the session (8.8 s warm at 22k lines, measured)
  and is paid on EVERY response. Options: (a) sync with a wall-clock budget, deferring to the next
  pass when exceeded; (b) detached pass whose failure is surfaced by the next pass; (c) make the warm
  pass O(new lines) by caching the prior emission set beside the cursor, which re-opens the 3a
  "positions only" cursor rule. Recommended: (a); (c) only if the budget is breached in practice.
  Hook budgets, verified against the Claude Code hooks reference (code.claude.com/docs/en/hooks,
  fetched 2026-10-02): "Defaults: 600 for command, http, and mcp_tool ... SessionEnd hooks share a
  1.5-second budget; if your settings set a longer per-hook timeout, Claude Code raises the budget to
  match, up to 60 seconds". So the Stop pass's constraint is user-facing latency (it is killed only at
  600 s), while SessionEnd is the binding cap: unconfigured, a finalize pass that makes any writer
  round-trip is very likely killed at 1.5 s and lands no close row, and even configured it has a hard
  60 s ceiling for the lock wait, the finalize parse and every remaining chunk. .claude/settings.json
  must therefore set the SessionEnd timeout explicitly (c1). Under option (a), a long session whose
  Stop passes all defer pushes its whole tail into that capped pass (the SessionEnd pass is warm-shaped, since the cursor
  already exists: its parse alone costs 3.84-8.80 s at 22k lines, cold to warm, section 1, before any chunk is sent,
  plus about 50 MB of rows in at least nine chunks), so the deferral-count trigger below must fire
  before the deferred tail can exceed what one SessionEnd pass can send. The cap weakens (a)'s
  defer-to-SessionEnd path and strengthens (c). The previous wording here ("60 s by default", round 1
  M3) was wrong (plan-critique r2, C1). "Breached in practice" needs a
  measurable trigger: the runner's diagnostics count deferrals per session, because under (a) a long
  enough session defers every Stop pass and all capture falls to SessionEnd, the hook q2 doubts
  (plan-critique r1, M4). Under (b), and under (a) with a slow pass, the SessionEnd lock wait of runner
  step 2 is what keeps finalize from being skipped (M2).
- k2 Cursor home. A local file is lost when a CC-web container is reclaimed. Losing it is safe for
  rows (full re-send, all no-ops) but not for the pin: re-reading project_ref from repository config
  after loss is exactly what project-id.yaml:47-51 forbids. Options: (a) accept config re-read for the
  root producer only and amend the contract (operator, always-ask); (b) make the pin a transcript
  fact by having the SessionStart hook emit it as additional context, which Claude Code writes into the
  transcript, so a lost cursor re-derives it from the tree (needs a parser rule and a PARSER_VERSION
  bump); (c) defer emission for cursor-less trees. Recommended: (b), pending q2. If the operator picks
  (b), the parser rule keys on the PIN HOOK's identity and payload, not on SessionStart output in
  general: the earliest SessionStart hook attachment (attachment type hook_*, hookEvent SessionStart)
  whose command is the pin hook's declared command AND whose content matches a declared pin grammar
  (for example a fixed prefix; the 3b plan owns the exact grammar). This repo already runs eight
  SessionStart hooks in parallel (.claude/settings.json; the hooks reference says matching hooks run in
  parallel), so the earliest SessionStart attachment is whichever hook finished first and is usually
  not the pin hook (plan-critique r3, D1). A later SessionStart:resume carrying a changed config
  value, or prompt or tool_result text that looks like a pin, never re-pins (project-id.yaml:47-51,
  "PINNED once"). Option (a) is operator-only because of D200 cl.1: re-reading config after loss can
  send a different project_ref, which auto-registers a new project_id for the same tree (plan-critique
  r1, M6).

Risk (a known loss mode, not a choice; carried in the fixture as q2 and as the failure_signal):

- R1 Final-turn loss. Emission is per CLOSED turn (e6); a session whose container is reclaimed without
  SessionEnd never closes its last turn, and none of that turn's rows (its hook rows included) ever
  reach the warehouse. It is expected to be the dominant loss on CC-web. Mitigations: runner step 7's
  close row makes it observable; SessionStart catch-up on resume recovers it only if the transcript is
  restored whole (q2).

- R2 Sticky finalized flag (a merged 3a defect, found by verification r2 G2). record_turn.py:149 keeps
  `finalized` true forever once a pass finalizes. Later passes then evaluate the cursor-truncated
  prior tree as finalized (record_turn.py:135), where finalize is a virtual prompt at EOF, so that
  prefix's open last turn counts as already emitted though it never was. Result: in a session that is
  finalized, resumed, then passed by Stop before SessionEnd (the NORMAL flow under runner step 1), the
  resumed segment's last turn is never emitted, even though SessionEnd succeeds and the close row lands.
  No session-row metric can see it. It falsifies s3's precondition (incremental capture equals one full
  parse, record_turn.py:5). The probe reports 4 of 5 turns; locally setting the flag to session_final
  alone gives 5 of 5 (gate report r2; VP 8 reproduces it from the repo). PRECONDITION of c1 and c2, owned by rec-4026
  like every src/turn_capture rule change, shipping in slice 3b's PR, routed by the operator
  (plan-critique r2, C5). Not fixed in this REPORT-ONLY PR. Detector: the
  runner-side check at finalize (step 8) with its resume-boundary cut schedule, named in c2; the
  existing conformance walk cannot see it.

Parked (a recommendation with no precedent; nothing moved):

- P1 The gate/precommit signatures on the handed-on list (e5) are recommended for the read-side friction
  classifier over tool_result rows (rec-4032), which keeps the producer transcript-pure. Operator decides.

Open (q1-q3 in the fixture; none is answerable from the repository):

- q1 Two classes land no rows at all, so no warehouse metric can see them: a runner that never
  succeeds, and a session lost before its first turn closes (section 4; plan-critique r1, B1). Seeing them needs an
  independent, out-of-band session denominator. Candidates: the runner's own diagnostics, or an open
  row at the SessionStart pass. The second is a record_turn rule change against the generation-marker
  rule (the open row is the generation commit marker, D207 R3; sessions.py:3-4), so it belongs to the
  operator. For runner exit-1s specifically, the other candidate is the hook rows'
  exit_code (observations.py:378) once any later pass succeeds. The second candidate cannot cover the
  SessionEnd run itself: a record appended after a successful finalize is withheld from every later
  non-final pass (the finalized prefix treats its open last turn as closed), so the SessionEnd hook's
  own row lands only if the session is resumed and re-finalized (verification r3, H4).
- q2 Does SessionEnd fire on CC-web reclaim, and is a resumed session's transcript restored whole (so
  catch-up sees the prefix)? And does a SessionEnd pass fit the 60 s cap (k1)? A killed SessionEnd pass on
  a multi-turn session is visible to the failure_signal; on a single-turn one it is the section 4 blind
  spot. R1's size hinges on these.
- q3 billing_shape for API-key sessions (rec-4147): the default fixed_non_rollover_allowance mislabels
  unattended API-key runs (Decision 205) as subscription spend.

## 4. Consideration register (as authored in the fixture)

- planes: data_plane.
- failure_signal: unfinalized_session_share, the share of root sessions whose LATEST lifecycle row
  (open, resume, compact or close) is not close, 24 h after their last event. Source:
  telemetry_sessions through the rec-4024 reader verbs. It sees R1 for sessions, and resumed
  segments, that already emitted an open or resume row: their last turn was lost, the session or
  segment was never finalized, so the latest lifecycle row is not close. It does NOT see R1 when the
  lost turn is the session's ONLY turn: the open row is emitted only once a closed turn holding an
  assistant record exists, or at finalize (sessions.py:3-4), so a single-turn session reclaimed before
  SessionEnd, or whose SessionEnd pass fails, lands no row in any table and drops out of both numerator
  and denominator (plan-critique r1, B1; probe scenarios Z and Y). Thread and gate sessions in this
  project are often one or two prompts long (inferred, not measured), so the share undercounts exactly
  the unattended population; the spot_check -> anomaly_triggered rung must not promote on it until
  q1's independent denominator exists. "No close row at all" was rejected in verification r2 (G1, scenario D): a session finalized
  once, then resumed and reclaimed, already holds a close row, so its lost turn would be invisible. The
  per-finalization close identity (step 7) keeps a clean re-finalized session from flagging.
  It does NOT see R2: that loss happens with SessionEnd and the close row both landing (r2 scenario
  E2), so R2's detector is the runner-side conformance check.
  Idle live sessions: a live session idle for more than 24 h counts as unfinalized while its last turn
  is pending (e6). That turn is pending, not lost; it lands when the session resumes and the session
  leaves the count once a later close lands. Long-idle sessions (this project's threads) inflate the
  share, so the spot_check -> anomaly_triggered threshold must be read with them in mind. Two more
  no-loss flags count the same way: a resumed segment with no new prompt that is then reclaimed
  (benign, like an idle session), and a finalize pass whose sessions chunk alone fails and is never
  retried (a correct flag: the pass failed).
  If SessionEnd never fires on CC-web (q2), the share reads near 1 for those sessions, which is real
  exposure, not a false positive. LIMIT: a runner that never succeeds, and a session lost before its
  first turn closes, land no rows and cannot be seen from the warehouse; that is q1 and needs an
  out-of-band denominator.
  Contract dependency: the metric reads the LATEST lifecycle row, while telemetry_sessions.yaml:186-193
  still says "absent a close row, the session is running". The reader-verbs component (W1-2) carries
  that amendment as a contested option, so this failure_signal depends on an unratified contract
  change. W2 and W3 should stage it as a pending item, not assume it (plan-critique r1, M10).
- Rejected in verification round 1 (w1-capture-producer-wiring-zero-context-verification-r1-4d1a8e63,
  F1): turn_coverage_gap (capture-hook process_events minus turn_close rows). A hook attachment joins
  its turn (streams.py:266-269), so its row and the turn_close row are emitted together or not at all
  (observations.py:391-393). The gap never opens on a lost final turn, and a clean session with
  SessionStart and SessionEnd runner rows breaches falsely.
- maturity: starts at read_all, meaning every finalized session is checked by the runner-side
  conformance check (runner step 8, verified by c2). read_all -> sampled at >= 20 consecutive finalized sessions with
  zero violations; sampled -> spot_check at 0 violations across the last 30 sampled; spot_check ->
  anomaly_triggered at >= 30 consecutive days with unfinalized_session_share <= 0.05 over the q1
  out-of-band session denominator. That last trigger is unmeetable until q1 is answered, which is the
  intent: the ladder must not promote on a share that cannot see single-turn losses (plan-critique r2,
  C2). The numbers are seed values for the maturity-ladder controller component to challenge.
- verification: c1 (hooks, data-first order, chunking, cursor-after-ack, close row, SessionEnd lock wait,
  never exit 2, SessionEnd timeout set explicitly),
  c2 (failed/killed pass and lost cursor replay as no-ops or defer; the runner-side conformance check
  at finalize proves incremental equals one full parse, resume-after-finalize included, which R2 fails
  today), c3 (T3.20 c8 smoke plus a
  rec-4101 monitor run, review method). All open. The c1 and c2 commands run one pytest invocation per
  obligation, chained with &&, so each obligation needs its own matching test (pytest exits 5 when a -k
  term matches nothing). An OR selector would be met by one test (plan-critique r2, C3; probe: a file
  with only test_hooks_and_ordering passes an OR selector but fails the chain with exit 5, while a file
  with one test per term passes). This is still a floor: one test whose name contains every term would
  pass, so the 3b plan names one red-before node id per obligation.
- rollback: remove the hook entries; rows stay (append-only); cursors hold positions only.
- edges: part_of T3.20; depends_on T2.36.

## 5. Boundary notes for W2 synthesis

- Deliberation capture (DeepSeek reasoning_content) is a transcript-less producer: it must RECEIVE the
  root's project_ref and session_started_at (epoch-ms or a decoded held key, telemetry-event-envelope.yaml
  session_started_at handoff rule). This item is that handoff's source. The deliberation item is now on
  main as pwi-deliberation-capture with no edge to this item, because this item was not on main when it
  merged and L4 fails closed on an absent target. W2 synthesis should add the edge
  `pwi-deliberation-capture depends_on pwi-capture-producer-wiring`. This PR does not edit another
  component's rows.
- Reader verbs (rec-4024 item 6) are this item's failure_signal source; back-validation (T3.4) and the
  friction classifier both read only rows this item writes.
- T3.20 c3 (an orphan/coverage DQ check in config/agent/data_quality/ops.yaml, alarm-not-gate) is the
  likely registration home for the unfinalized_session_share alarm and for step 8's conformance
  counter (plan-critique r1, M8).
- T3.20 c1's launch criterion (a pre-write credential scrub) is already met inside record_turn
  (transcripts.py:213, observations.py:381-382, sessions.py:222). The runner owes nothing there, and
  the fixture's c1 does not drop it.
- Reader-verb owner (rec-4024): the contract's derived-state rule ("absent a close row, the session is
  running", telemetry_sessions.yaml:186-193) has the same resume-after-close ambiguity as G1. The
  session-state reader verb should derive state from the LATEST lifecycle row, ordered by
  (event_timestamp, source_ordinal) with close ranked last on a tie (step 7). Boundary note only;
  the contract is not edited here.
- W0 schema note: MAX_CRITERIA_PER_ITEM (3) times Command's 200-character cap makes a many-obligation
  criterion hard to enforce by selector; c1's eight per-term invocations fit at 183 characters, a ninth
  would not. Worth carrying to W2 as a W0 schema observation (plan-critique r2, C3).
- No seed component owns the WRITER verb. It is already tracked (T2.36 c1/c2, rec-4024 slice 2), so
  the pilot should point at it by edge rather than add an item.

## 6. Not done here (and why)

- No hook, runner, test or writer code: REPORT-ONLY by brief.
- No rec filed, updated or closed (Decision 67; the brief's never-list). rec-4026 stays open; the note
  this report would add to it is the operator's call.
- No T3.20 or T2.36 criterion text or status change.
