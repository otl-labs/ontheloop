# REPORT: telemetry feedback-loop re-grounding against main (2026-11-12)

> REPORT-ONLY deliverable of `docs/plans/PLAN-feedback-loop-regrounding.yaml` (Decision 86 cl.2; file-router
> report-deliverables route). A dated, point-in-time reading of the October 2026 feedback-loop designs (the W1
> component reports `docs/plans/reports/REPORT-w1-*.md`, the W2 synthesis and the W3 staging report) against main
> at d8283f55. It supersedes the W1 reports by reference for their premises and anchors; it edits none of them,
> because W1 and W3 text is cited by line elsewhere (Decision 221 cites REPORT-w3:423 and :627). It plans no wave
> 2-4 work: the operator's 2026-11-10 gate holds (section 16). Nothing here is built, ratified, flipped or closed.
> Every lasting ruling recorded here also lives in a rec, which this report cites (section 17); the rec is the
> durable home, this report is the snapshot.

Evidence base and claim marks:
- **[repo]** read on main at d8283f55, with path:line where it matters. Decisions are cited by number and clause,
  not DECISIONS.md line, because new Decisions are inserted at the top of that file.
- **[recs]** read from the rec store through the ducklake-reads named verbs on 2026-11-10 to 2026-11-12.
- **[review]** a finding of the 2026-10-05 feedback-loop review, which is not in this repository; each finding this
  report relies on is restated here.
- **[probe]** a command run on 2026-11-10 at f866f98b (section 12); its inputs are byte-identical at d8283f55.
- **[inferred]** a reading not verified. **[partial]** only partly checked.

---

## 0. Synopsis

1. **Most of the review's "cheap now" list is built** (section 2): per-verb read versions, the drill namespace and
   purpose marker, the production-pinned telemetry reader, blob reclaim, the writer's `component` stamp and request
   line, content-addressed blobs, the describe handshake, the lifecycle reading (Decision 228), friction derivation
   text, the closed `severity` set, effective-dated `cost_usd` inputs, and the window-verb reading of Decision 199
   cl.1 (its 2026-10-16 Update note, CD.52's substance).
2. **Wave 2 is smaller than the handover said.** The friction per-session verb is built and serving
   (`session_friction_rollup`, `classifier_version` 1, #1496). What remains is correctness (rec-4605, rec-4959,
   rec-5097), a rules guard, a window verb for the unmapped share, and the operator's label review. The
   deliberation verb is unbuilt but never reads transcript content, so it does not wait on the content reader
   deploy (rec-4696); only forensic reads do.
3. **Window reach is the main telemetry blocker for waves 3 and 4.** Window verbs are capped at 7 days, cold window
   reads time out (rec-4474), pagination truncates (rec-4363), and responses carry no completeness watermark. The
   register reads 28 days, the ladder 30, back-validation about 57. The operator ruled that daily series are settled
   rows in the loop record journal (section 17, R2), so long reads stitch windows only to seed rows.
4. **Back-validation's join keys are still missing where they matter.** `checkout_base_sha` is not populated on
   cloud sessions (rec-4426) and `rec_ids` is unproduced; the operator ruled it stays held until back-validation
   is planned (R1).
5. **The ops-side homes are unbuilt** (rec-5078): five statuses, `close_proposed` only printed, no `covered_by` or
   fix-attempt stamp, no close-time read. The /implement relevance bypass is closed (rec-4592).
6. **The loop record journal (CD.47) is shared.** Decisions 221 cl.7, 231 and 235 put loop, persona-run and
   shadow-verdict records in one journal; the append kernel cannot write it (rec-5077).
7. **The allow-list's premises moved again**: 19 closed vocabularies, not 15; no `enforced: false` flags; its column
   inventory no longer matches; Decision 221 cl.1 keeps `cost_usd` out of any crossing set.
8. **New since October and since the 2026-11-10 draft** (section 1): Decisions 249, 250, 252 and 253; two
   no-backfill capture gaps; the arrival oracle now dates lost-window excuses by commit author date.
9. **Wave order holds; prerequisites changed** (section 15). T3.4 also needs T1.13, which is another workspace's
   (ownership asked, section 16).

---

## 1. What moved

### 1.1 Since the October designs (pinned at parser version 2, 2026-10-03 to 10-05)

| Change | Effect on the loop designs | Evidence |
|---|---|---|
| claude_code parser versions 3-9 (2026-10-14 to 11-07) | Gate and precommit markers, quoted hook paths, status process events (v6), host-run sidecars marking CI roots (v8), capture identity off "command hook N" (v9); each version declares its `signature_prefixes` | [repo] config/telemetry/parser_versions.yaml |
| Separate production-pinned telemetry reader; named event verbs | All loop reads go through `src/common/ducklake_event_read_verbs.py` verbs; caller SQL is never allowed (Decision 84) | [repo] src/common/ducklake_event_reads.py:97-110 |
| Decision 221 (feedback loop) | cl.1 dollars derive at read, never stored or published; cl.3 one ladder lane per control arm; cl.5 public receipts read through the allow-list; cl.7 one ladder, allow-list, journal and filer | [repo] Decision 221 |
| Decisions 231 and 235 | Persona-eval run records (231) and shadowed-check verdicts (235 cl.3) join the one loop record journal; 235 cl.2 binds a verification-weakening ladder lane at CD.46's ratification; 235 cl.7 holds its build until rec-4413's re-measure (from 2027-01-18) and an out-of-scope evidence producer (T3.7) | [repo] Decisions 231, 235 |
| Decision 228 | A root session's end is read from its turns | [repo] Decision 228 |
| Decision 239 | Agents read production transcript text only through the masked content reader; its deploy lock is rec-4696 | [repo] Decision 239 cl.1, cl.5 |
| Decision 249 | Lambda channels deploy only the newest main commit whose full tier passed, so a deploy day differs from the merge day | [repo] Decision 249 cl.2 |
| Decision 250 | Arrival expectations: telemetry_sessions is checked against every merged Claude-Session trailer (cl.5); an empty denominator is skipped, never green (cl.2); every check is watched (cl.3) | [repo] Decision 250; scripts/telemetry_verify/session_presence.py |

### 1.2 Since the 2026-11-10 draft (f866f98b to d8283f55)

- src/, the telemetry contracts, parser_versions.yaml and the W1, W2 and W3 reports are unchanged, so the 2026-11-10
  code reading and probe results carry **[repo: git diff]**. The arrival oracle changed: #1967 excused a re-opened
  outage-era session (rec-5081) and #1973 dates a lost-window excuse by commit author date, not PR creation time
  (rec-5084) **[repo]**.
- rec-5055 (hook outage) is closed and the arrival check is green again **[recs]**.
- A second no-backfill capture gap, 2026-11-09T22:33Z to 2026-11-11T10:38:16Z, joins the 3-6 November one
  (rec-4939) **[repo: config/telemetry/arrival.yaml lost_windows]**. Any baseline window that spans either is
  short by design.
- **Decision 252** (2026-11-10): once SIT stands, every merge deploys to SIT and PROD is promote-only, the same
  artifact behind one operator approval (cl.6); SIT agents read production-derived data only through named verbs
  (cl.5). For back-validation the effective day of a fix in PROD is its promotion day, not its merge day or SIT
  deploy day (section 8). **Decision 253**: the catalog moves to RDS PostgreSQL, SIT first; the named-verb surface
  is unchanged **[repo]**.
- New related recs: rec-5056 (High; harness_key_missing alarm, 2 sessions; any join on `harness_session_key`
  inherits it); rec-5061 (High; threads started in the checkout's parent store branch `HEAD`, so a branch-prefix
  read miscounts them); rec-5042 (DuckLake Lambda cold connect 11-14 s on any backend, beside rec-4474) **[recs]**.
- T3.4 is still titled "(STRATEGIC)" (docs/ROADMAP-PLATFORM.yaml, T3.4 name), but Decision 247 retired that plan
  type; the plan that next scopes T3.4 re-grounds the title **[repo]**.

---

## 2. The review's cheap-now items (C1-C13) against main

| Item | Review's ask | State on main | Evidence |
|---|---|---|---|
| C1 | per-verb monotonic read versions | Built differently: each event verb stamps `read_version`, a 16-hex digest of its SQL, data block, params, max_window_days and compared columns. Not ordered: a revert restores an old digest (ruling S1, rec-5080) | [repo] src/common/ducklake_event_reads.py:264-285 |
| C2 | purpose marker, reserved drill namespace | Built: `ops_projects.purpose` (real, drill, synthetic) from reserved prefixes, never auto-registered | [repo] docs/contracts/ops_projects.yaml; project-id.yaml |
| C3 | decide whether the journal goes through the kernel | Not decided. The kernel refuses any table not partitioned on `session_started_at` (rec-5077) | [repo] src/telemetry/append.py:89-107 |
| C4 | pin the reader to the telemetry catalog | Built: the telemetry reader refuses every event verb unless pinned to production | [repo] src/common/ducklake_event_reads.py:97-110 |
| C5 | reclaim noncurrent blob versions | Built: `telemetry-blobs-noncurrent-reclaim` | [repo] terraform/personal/main.tf |
| C6 | request-level `component`, one log line per request | Built on the writer (`telemetry_request` line with verb, component, rows per table, bytes, status). The telemetry reader logs one `ducklake_invocation` line per request with action and status only; rec-4706 adds the served verb and error_type, and its 2026-11-11 note adds `component` | [repo] src/lambdas/ducklake_telemetry_writer/telemetry_log.py:104-133; src/lambdas/ducklake_telemetry_reader/handler.py:212, :250 |
| C7 | content-addressed blobs, skip-if-exists, log bytes | Built | [repo] src/common/ducklake_telemetry_blobs.py |
| C8 | describe advertises accepted parser versions | Built differently: describe advertises a per-table schema view and fingerprint, deliberately with no parser_version ceiling | [repo] src/common/ducklake_telemetry_handshake.py:1-7 |
| C9 | one lifecycle text | Built and amended by Decision 228 | [repo] Decision 228; telemetry_sessions.yaml governance_notes |
| C10 | friction derivation text | Built | [repo] docs/contracts/telemetry_sessions.yaml |
| C11 | close `severity` | Built: [info, warning, error, critical], required on process_event points | [repo] docs/contracts/telemetry_observations.yaml |
| C12 | `event_timestamp` and provider in `cost_usd` inputs | Built: effective-dated price at the row's event_timestamp | [repo] docs/contracts/telemetry_observations.yaml |
| C13 | Decision 199 cl.1 reading without "7 days" | Built as Decision 199's 2026-10-16 Update note: window verbs are capped per verb in the registry. REPORT-w3:479 named that note as CD.52's home; only CD.52's T2.52 reactivation half remains the operator's | [repo] Decision 199 Update (2026-10-16); REPORT-w3:479 |

Production rows exist: on 2026-11-09 one `model_usage_rollup` window read answered, for example, 8,109 model calls
for one model over 16 trees **[probe]**.

---

## 3. Friction classifier (REPORT-w1-friction-classifier.md, PLAN-w1-friction-classifier.yaml)

**What the design asked:** rules as data, applied at read by `session_friction_rollup` over deduped rows; one label
per fact; a repeat-run rule; `classifier_version` stamped; a rules guard (c2); the operator reviews every label of
the first sessions at read_all (c3).

**Built** **[repo]**: `session_friction_rollup` serves on the telemetry reader with the design's rules block
(`r01`-`r04`, generic `r90`-`r94`), `repeat_threshold` 3, `classifier_version` 1 and 17 committed vectors
(src/common/ducklake_event_read_verbs.py:21-38, :96-106; src/common/ducklake_event_read_sql.py:264-302;
tests/common/test_ducklake_event_read_verbs.py). Parser v3 shipped `gate:<check>` and `precommit:<hook>` events
from structured markers (rec-4172 closed), quoted hook paths, scrub-before-truncate, and the denial and interrupt
classification.

**Still holds:** the decision logic and the label vocabulary. The per-session verb is the shape T3.3 and the filer
read.

**Stale, with the corrected anchor:**
- PLAN-w1-friction-classifier VP 1's three-argument `build_observations` call raises (`missing ... 'sidecars'`)
  since #1405 **[probe; review]**. Every pre-#1405 anchor is stale; the live anchors are the verb module and SQL
  lines above.
- "Reads the rec-4032 labels table": main reads the rules block from the verb registry; rec-4032 (silver labels
  table) stays open, Low, triggered only by an LLM classifier or a measured egress problem **[recs]**.
- Parser v6 added a `status` process-event source (`status:<source>.<result>`) that no rule maps, so a reported skip
  at warning falls to generic `r91 hook_fault` (rec-4605, High) **[repo + recs]**.
- Since Claude Code 2.1.317 cloud hooks are recorded as "command hook N"; parser v9 moved capture identity off that
  field, but other hooks' blocks still label `unmapped_block` (rec-4959, Medium) **[recs; affected hooks not
  re-derived, partial]**.

**Defects with a rec** (review G1 and G2, re-read against the live SQL, not re-probed): G1, the absorb guard skips
only a `blocked` tool_call whose hook child is an error (ducklake_event_read_sql.py:273-275), so a `gate:`
process_event at error under an `error` Bash close yields two facts; G2, the repeat-run partition is
`(parent_observation_id, name)` (:276-279), and orphan closes carry a NULL name, so unrelated orphan errors under
one turn can form one run. rec-5097 carries both, with the operator's ruling R3 (absorb, and report the absorbed
count).

**Not built:** the c2 rules guard (a rule names a registered hook or check; no ties) beyond the per-version prefix
declaration a test pins (tests/telemetry/test_parser_version_registry.py) **[repo; partial grep]**; a window verb
for `unmapped_failure_share` per classifier_version (the register and ladder input); per-session exposed and
affected flags (backval-q1); the c3 operator label review; the repeat key `input_key` (rec-5079, ruling S3).

**Waits on:** rec-4605, rec-4959 and rec-5097 (one rules PR, one classifier_version bump); rec-4691 (phantom turn
before each project-thread wake) and rec-4720 (missed sub-agent usage) only for counts that read turns **[recs,
inferred relevance]**; rec-4474 for any window read; the c3 review itself (operator).

**Overlap:** the Agents in GitHub workspace runs verdict agents as `claude -p` in Actions; parser v8's host-run
sidecar marks those roots by workflow, and producer admission (rec-4415, rec-4420) is this workspace's. A friction
window over CI agents and interactive sessions together needs a workflow filter, because both are `claude_code`
**[inferred]**. The LiteLLM lane writes no tool_call rows until rec-4028 lands **[repo: no litellm entry in
parser_versions.yaml]**.

---

## 4. Deliberation capture (REPORT-w1-deliberation-capture.md)

**What the design asked:** a per-call `deliberation_class` and a session roll-up, derived at read from model_call
rows and transcript purpose only; a drift monitor (c2); operator review at read_all (c3).

**Built** **[repo]**: the inputs. `reasoning_visibility` (five values, required on model_call points) and
`reasoning_tokens` (at most `tokens_output`; zero or NULL when visibility is none) are write-enforced
(docs/contracts/telemetry_observations.yaml). No `deliberation_class`, session totals or
`session_deliberation_rollup` exists **[repo: grep]**.

**Still holds:** the derivation projects transcripts to observation_id and purpose only and never selects
`content` or `content_uri` (REPORT-w1-deliberation-capture.md:122-124), so it reads through the deployed telemetry
reader and does not wait on the content reader deploy (rec-4696); only the forensic read (delib-k3) does (ruling
S2). The build keeps its check c1 that the verb selects no content column (REPORT-w1-deliberation-capture.md:435);
choosing delib-k3 (b) or (c), or a read-side chain-of-thought regex, would bring the dependency back.

**Stale, with the corrected anchor:**
- The SQL, its 16 vectors and the four-value class vocabulary predate the delib-k4 ruling (three added arms); the
  report has not changed since 2026-10-05 **[repo: git log]**.
- No machine-readable text-bearing marker exists on `reasoning_visibility`; the design's "read from the contract,
  never restated" needs one, or a pinned literal **[repo]**.
- The register counter built on this component (`visibility_consistency`) is constant 1.0 on stored rows, because
  the writer rejects none-with-tokens **[repo; review]**.
- The drift monitor's substrate exists: the telemetry-verification workflow runs reader verbs daily, alarm-not-gate,
  watched under Decision 250 cl.3 (ROADMAP T3.20 c3 met; .github/workflows/telemetry-verification.yml). The drift
  check rides it **[repo]**.

**Not built:** the verb, the five derived session fields and the observation field (Class A field adds with no
physical column), the response-shape home (delib-q4, operator), the drift check, durable per-day history (rec-4438,
now a journal row kind under R2), the loss counter (delib-k2, now with rec-4028), retention of stored text
(rec-4698) and erasure (rec-4029, Low) **[recs]**.

**Waits on:** the field adds and the text-bearing marker (contract amendments in this workspace's remit); delib-q4
(operator); for the LiteLLM lane, rec-4028.

**Overlap:** rec-4028 (LiteLLM telemetry producer) is the Agents in GitHub workspace's (ruling R5); its DeepSeek
verdict personas are the "full" reasoning lane and its spend guard reads rec-4028's model_call rows (rec-5021). This
workspace reviews the reasoning mapping when rec-4028 is planned; rec-4508 (a model that writes chain of thought
into content) sits beside it **[recs]**.

---

## 5. Goodhart register (REPORT-w1-goodhart-register.md)

**What the design asked:** one counter series and one seeded drill per failure_signal, read daily as a verdict; the
operator approves each row before the ladder reads above read_all.

**Built** **[repo]**: the drill identity half. Drill and synthetic projects are reserved and never auto-registered;
the real-purpose window excludes them structurally, and `sessions_window_all_purposes` is the opt-in
(src/common/ducklake_event_read_sql.py:187-199; ducklake_event_read_verbs.py:190-200).

**Still holds:** the counter and verdict logic for offline replay; drills mint fresh record uuids, refs and
timestamps (Decision 207 grain); seeds that break a write rule can only be replayed **[review; rules unchanged]**.

**Stale, with the corrected anchor:**
- The capture counter's denominator (capture-q1, SessionStart invocations) is superseded by Decision 250's arrival
  oracle: every merged commit's `Claude-Session` trailer against `harness_session_key`
  (scripts/telemetry_verify/session_presence.py). The capture row re-sources to that oracle; its denominator covers
  merged sessions only, by declaration (Decision 250 cl.5), and quiet days read as skipped, never green (cl.2).
- The deliberation counter is constant (section 4).
- Smoke sessions under `repo:deploy-smoke-auto-*` derive purpose real and pass the real filter (rec-4611); a series
  read from the smoke catalog would be inflated; production windows are not affected **[inferred]**.

**Not built:** the register table, its daily series, the drill log and the verdict job.

**Waits on:** CD.46 and CD.47 (operator); the series home, now the journal (R2, rec-5077); window reach for the seed
read; rec-4233 for sensor liveness of a scheduled register job (the CI Reform workspace's) **[recs]**.

**Overlap:** a register alarm route ends at the alert address (rec-3904, the Property Transfer workspace's)
**[inferred]**.

---

## 6. Cost and egress budget (REPORT-w1-cost-egress.md)

**What the design asked:** a line register in physical units (tokens, GB-seconds, bytes, requests), a daily
attributed ledger, a verdict per line; dollars derived at read.

**Built** **[repo]**: writer attribution (C6), blob counts and declared bytes, content-addressed blobs, blob reclaim
(C5), effective-dated `cost_usd` inputs (C12), `model_call_cost_estimate` with a `cost_source` of reported,
allowance, local or unpriced (ducklake_event_read_verbs.py:127-139; ducklake_event_read_sql.py:250-262), and the
token rollups (`model_usage_rollup`, `agent_run_token_rollup`).

**Still holds:** tokens are the budget's unit; cloud sessions are `fixed_non_rollover_allowance`, so they derive no
dollars; Decision 221 cl.1 makes "dollars derive at read and are never stored or published" Decided. The budget is
tokens-only for now (ruling S5).

**Stale, with the corrected anchor:**
- The model inputs (52 KB per turn, four requests per pass) predate parser v2's single-request append; the writer
  line now gives measured request bytes and rows per table, so seeds come from logs **[repo; not re-measured]**.
- `cost_usd_reported` is unproduced (owner rec-4600), so `model_call_cost_estimate` returns allowance or unpriced for
  every claude_code row today **[repo; inferred]**.
- `catalog_stats` defaults its table filter to `ops_%` and nothing schedules it (src/common/ducklake_maintenance.py);
  the production telemetry catalog emits a 6-hourly `CatalogFileColumnStatsRows` metric with an alarm
  (terraform/personal/ducklake_maintenance_telemetry.tf). Both measure footprint, not bytes per read, so the
  catalog-egress line has no per-read source **[repo; inferred]**.
- The 2026-11-10 draft said the telemetry reader writes no request line. It does (C6); what it lacks is the served
  verb, error_type and `component`, all now on rec-4706 **[repo]**.

**Not built:** the register, ledger and verdict; the price table (rec-4031, Low, post-MVP); a scheduled egress
reading; loop-owned model call marking.

**Waits on:** CD.50 and budget-q2 (operator); rec-4706; rec-4734 (cost reconciliation has never run, High), because
c3 shows the prior month's reconciliation residual beside the register **[recs]**. A price table returns only if a
metered lane joins this loop's budget (S5).

**Overlap:** storage cost (rec-4729) and probably cost reconciliation (rec-4734) are the Cost Optimisation
workspace's **[inferred ownership for rec-4734]**. The Agents in GitHub workspace's DeepSeek personas are the first
metered lane, with their own spend guard (rec-5021); persona-eval spend is its own line, never `loop_llm_tokens`
(Decision 231 cl.4; rec-4436). The budget reads their spend, never re-meters it.

---

## 7. Rec filer with dedupe (REPORT-w1-rec-filing-dedupe.md)

**What the design asked:** one fingerprinted rec per (detector, subject, window) finding; file, update, hold or
suppress against the open rec; covered_by links; a run record per run; T3.3's "files anomaly recs".

**Built:** nothing of the filer. The fingerprint backstop is ci_rca-only (scripts/ops_data_portal.py:315-332)
**[repo]**.

**Still holds:** the decision logic **[review]**.

**Stale, with the corrected anchor** **[repo]**:
- Status is five values [open, closed, failed, declined, superseded] (docs/contracts/ops_recommendations.yaml); the
  SQL's `in_progress` and `deferred` arms are dead and `failed` falls to suppressed.
- `file` is a required rec field; the design names none.
- `close_proposed` is a printed command, never a status (docs/contracts/recommendation-relevance.yaml).
- `covered_by` has no write path and no named verb returns a rec's close time (rec-5078).
- /implement never closes a bundled rec (`implement_gate`, rec-4592 closed) **[repo + recs]**.
- Findings need a per-(label, tool) subject with exposure counts; `sessions_window` returns state, outcome, duration
  and stamps only (src/common/ducklake_event_read_sql.py:155-177). A branch-keyed subject inherits rec-5061.

**Not built:** the filer, its run record, the subject window verb with exposure, the filer's source registry entry
and priority (a writer deploy under Decision 210 cl.4), the ops homes (rec-5078).

**Waits on:** CD.47 (run records) and rec-5077; rec-5078; a subject window verb (the filer's own build plan); W3-2
and W3-6 for T3.3's criteria (operator). T3.3's exit criteria are still two bare strings with "via log-rec"
(docs/ROADMAP-PLATFORM.yaml T3.3) **[repo]**.

**Overlap:** ci-rca is the one live machine filer and the only caller of the fingerprint backstop; Decision 221 cl.7
says one filer. The recommended reading (Q11) keeps ci-rca a separate filer as a dated exception, conditional on the
loop's filer calling the same backstop; the operator asked the CI Reform and Agents in GitHub workspaces first
(section 16).

---

## 8. Back-validation (REPORT-w1-back-validation.md)

**What the design asked:** one call per candidate returning one row per session over about 57 day partitions at one
classifier_version; exposed, affected and addressed flags; a verdict that proves or refutes a fix; CD.48 amends
Decision 201 for telemetry acceptances.

**Built** **[repo]**: per-segment `checkout_sha`, `checkout_base_sha`, `checkout_dirty` (Decision 222, parser v4),
per-row `generations` and `conflicted_grain_keys` stamps, per-verb `read_version`. The /implement bypass is closed.

**Still holds:** the statistics and their guards; Decision 201's verdict keying accepts a fix-sha keyed telemetry
record unchanged **[review; closure_gate.py not re-read]**.

**Stale, with the corrected anchor:**
- e10 and R4 are superseded by Decision 222's checkout columns **[repo]**.
- The key is NULL where it is needed: `checkout_base_sha` is never populated on cloud starts (rec-4426), a pin
  written after the first prompt never reaches the open row (rec-4641), and cloud checkout facts depend on record
  order (rec-4963) **[recs]**.
- `addressed` needs `rec_ids`, still unproduced under `held:feedback-loop-gate` (telemetry_sessions.yaml rec_ids).
  Ruling R1: it stays held until back-validation is planned, which chooses a merge-time annotate producer or a
  client-side trailer join; a reader verb cannot derive it, because the trailers live in git. Both routes miss
  sessions that never merge (rec-4606 note).
- The 57-day span cannot be one call (cap 7 days, ducklake_event_read_verbs.py:17). Seeding nine stitched calls is
  exact for tree rows (scripts/telemetry_usage.py) if every page's `read_version` is equal; daily settled rows live
  in the journal (R2).
- backval-q2 (effective day of a fix not live at merge): Decision 249 deploys only the newest full-tier-green
  commit, and Decision 252 cl.6 makes PROD promote-only, so a fix's PROD effective day is its promotion day.
  backval-q2's "deploy's own timestamp" reads the promotion record **[repo]**.
- The post-merge `deterministic_auto_close` venue remains (recommendation-relevance.yaml); whether it can close a
  telemetry-routed acceptance without a verdict record was not traced **[partial]**.

**Not built:** the verb, the module, CD.48's fix-attempt stamp (now with rec-5078) and census arm, the verdict
table (a CD.47 kind).

**Waits on:** CD.48 and CD.47 (operator); backval-q1 and backval-q2 (operator rows); rec-4426, rec-4641, rec-4963;
R1's choice at planning; rec-5056 for any `harness_session_key` join; window reach for seeding; the filer. Both
capture gaps (section 1.2) sit inside any baseline that spans them.

**Overlap:** T1.13 c2 flips CI_RCA_STRICT_MODE "after Phase 4 back-validation"; this report reads that as ci-rca's
own script (scripts/ci_rca_back_validation.py, rec-2503), not this component **[inferred; asked, section 16]**.

---

## 9. Maturity ladder controller (REPORT-w1-maturity-ladder.md)

**What the design asked:** a daily deterministic promote, hold, demote or restart per lane (read_all, sampled,
spot_check, anomaly_triggered); a stamp bump restarts a lane; one review log; CD.46.

**Built:** none of the controller **[repo: grep]**. Its inputs moved: `classifier_version` exists (section 3);
per-verb `read_version` exists as a digest (C1); Decision 235 binds a verification-weakening lane onto the same
controller and transition log, entry rung live when CD.46 ratifies (T3.4 c3); Decision 221 cl.3 asks one lane per
control arm.

**Still holds:** parser_version fits the never-decrement rule; lanes are per producer; the review output id
includes parser_version **[review]**.

**Stale, with the corrected anchor:**
- The design's SQL computes `TRY_CAST(version AS UBIGINT)` and its restart and overlap vectors (v41-v48) compare
  stamps as integers (REPORT-w1-maturity-ladder.md:243-256). A hex digest casts to NULL, so no restart would fire,
  and first-seen ordering mis-orders a revert. Ruling S1: an integer version per verb in its data block beside the
  digest, with a check that a digest change needs an integer bump (rec-5080).
- The pilot fixture holds 10 items and 16 edges (docs/work-item-pilot/telemetry-feedback-loop.yaml) **[repo]**; the
  edges were not compared with the staged set **[partial]**. P7 (the pilot schema change) is the operator's.

**Not built:** controller, review log, transition log, settled daily series.

**Waits on:** CD.46 (rec-4581 and rec-4512 track placeholder citations) and CD.47 (review and transition kinds);
rec-5080; P7 and the fixture's fate (sunset is the earlier of T4.23 c6 and 2027-01-31); Decision 235 cl.7 for its
lane.

**Overlap:** Decision 231's persona lane shares the register rows and journal, and Decision 221 cl.3's control arms
are ladder lanes. A persona's autonomy rung (T4.4 gates) is not this ladder's to set (Decision 235 cl.2: the
executor's gate is necessary, never sufficient). rec-5072 restarts a verdict count on a settings change, the same
restart-on-change shape **[recs]**.

---

## 10. Allow-list transport (REPORT-w1-allow-list-transport.md; CD.51)

**What the design asked:** a data-plane verb applies an operator-approved allow-list before telemetry crosses to the
control plane; out-of-vocabulary values withheld; push transport; a canary proves no string leaks.

**Built:** none of the verb. Every accepted_values set is write-enforced (Decision 210) **[repo]**.

**Still holds:** allow-list over deny-list, data-plane verb, refusal on structural surprise, push transport
(Decision 209 cl.2(b); rec-4141).

**Stale, with the corrected anchor** **[repo, counted with a yaml load of the four contracts; probe]**:
- The plan's VP 2 still counts 140 projected columns (38/43/32/27), but inventory fidelity is 136/140 and column
  order 1/4 tables. The four contracts declare 155 fields (sessions 44, observations 49, transcripts 27, agents 35),
  13 read-derived. VP 1 counts 19 closed vocabularies, not 15. The crossing set must be re-derived.
- No `enforced: false` flag remains, so VP 1's "enforced count above 0" measures nothing.
- `cost_usd` cannot cross: Decision 221 cl.1 forbids publishing dollars; receipts carry no financial figure (cl.5).
- The egress SQL reads physical tables and raw sums; it must be specified over reader outputs with the shared dedupe
  (rec-4063).
- Decision 221 cl.5's public receipts depend on this allow-list; the receipts consumer is rec-4386, which depends on
  rec-4141.

**Waits on:** CD.51 and k1-k4 (operator, always-ask); W3-4; allow-q2 (no consumer exists); rec-4141's post-MVP
sequencing.

---

## 11. Settled on evidence (mechanical forks)

| Fork | Settled as | Evidence |
|---|---|---|
| Where the corrections land | This report, superseding the W1 reports by reference; no in-place edit of W1 or W3 | Decision 221 cites REPORT-w3 by line; W1 unchanged since 2026-10-05 |
| Seeding a 57-day read | Stitch 7-day windows client-side as scripts/telemetry_usage.py does, refusing the run if any page's `read_version` differs | telemetry_usage.py header; `read_version` stamped per response |
| Deliberation drift monitor home | The telemetry-verification workflow | ROADMAP T3.20 c3 met; Decision 250 cl.3 |
| Register capture denominator | Decision 250's arrival oracle, not SessionStart counts | Decision 250 cl.5; session_presence.py |
| `cost_usd` in any crossing set | Excluded | Decision 221 cl.1 |
| CD.52's substance | Landed as Decision 199's 2026-10-16 Update note; later clause-1 changes need a numbered Decision | Decision 199 Update; REPORT-w3:479 |
| S1 read_version for the ladder | Integer per verb beside the digest, lockstep check (rec-5080) | REPORT-w1-maturity-ladder.md:243-256 |
| S2 content reader and deliberation | Prerequisite only for forensic reads | REPORT-w1-deliberation-capture.md:122-124, :435 |
| S3 repeat key | Producer `input_key` hash at the next parser bump; (turn, tool) runs until then; read-side key rejected (tool_input rows carry no tool_use id; withheld rows share an empty digest from v8) (rec-5079) | src/turn_capture/transcripts.py:305-323; telemetry_transcripts.yaml |
| S4 gate list | The operator's 2026-11-10 named list (section 16); rec-4606 takes it as input | rec-4606 note |
| S5 price table | Tokens-only; rec-4031 stays post-MVP; revisit when a metered lane joins this loop's budget | telemetry_observations.yaml billing_shape; Decision 221 cl.1 |

---

## 12. Probe re-runs (each W1 plan's own hermetic VPs, 2026-11-10 at f866f98b)

Each W1 plan's verification_plan was re-run with a 200 s limit per step; steps that fetch old SHAs, run `--pre` or
render the plan document were skipped. "Pass" means exit 0 and every `expected_literals` string printed. Their
inputs are byte-identical at d8283f55. Raw outputs are not committed. **[probe]**

| Component | Steps run | Pass | Differs from the pinned literal (what moved) | Did not finish |
|---|---|---|---|---|
| friction | VP 1, 2, 4, 5, 6 | 2, 6 | VP 1 raises `TypeError` (missing `sidecars`). VP 4: registered hooks 13 to 21; no hook falls back to `hook:PreToolUse.Bash`. VP 5: rework and exception inputs widened to the k2 set | - |
| deliberation | VP 1-5 | 4, 5 | VP 1: parser_version 2 to 9; diagnostics also count `absent_usage_key`. VP 2: parser 9; `reasoning_effort` joined the reasoning fields. VP 3: environment failure in the pinned litellm and mcp pair (owned by rec-3795) | - |
| Goodhart register | VP 1-5 | 3, 5 | VP 2: two failure_signal readers exist (was 0); `drill_markers=0` because the probe misses `ops_projects.purpose` | VP 4 (over 200 s) |
| cost and egress | VP 1-4 | 3 | VP 2: a telemetry catalog-stats alarm exists; capture hooks include SessionEnd, Stop, StopFailure and UserPromptSubmit | VP 4 (over 200 s) |
| rec filer | VP 1-5 | all | - | - |
| back-validation | VP 1-5 | 1, 2, 3, 5 | VP 4: `session_code_cols` is `[checkout_base_sha, checkout_sha]` (was empty) | - |
| maturity ladder | VP 1-5 | 1, 2, 4; VP 3 prints its expected `fidelity=6/6` | - | VP 5 (over 200 s) |
| allow-list | VP 1-7 | 4, 5, 6, 7 | VP 1: 19 vocabularies (was 15). VP 2: fidelity 136/140, column order 1/4 | - |

Reading: the designs' internal logic still passes wherever it ran; what differs is where main moved under them.
Three long runs did not finish and were not retried **[partial]**.

---

## 13. The ten unowned requirements (review section 4), with owners

| # | Requirement | State | Owner |
|---|---|---|---|
| 1 | SessionStart denominator | Superseded by Decision 250's arrival oracle | none needed |
| 2 | `rec_ids` and `rec_id` population | Held until back-validation is planned (R1) | rec-4606 note; the back-validation plan |
| 3 | CD.47 journal writer | Operator decision first (handover B2 item 5), then design | rec-5077 |
| 4 | reader-q2 shadow re-derivation | Done in slice 4 | rec-4025 closed |
| 5 | DQ-monitor substrate | Exists (telemetry-verification workflow); drift check unbuilt; durable history becomes a journal row kind | deliberation build; rec-4438 |
| 6 | Self-describing gate markers | Done | rec-4172 closed |
| 7 | MVP price map | Partly: effective-dated derivation and `cost_source`; no price table | rec-4031 (post-MVP, S5) |
| 8 | `input_key` | Producer field at the next parser bump (S3) | rec-5079 |
| 9 | Ops homes (close_proposed, covered_by, close time, fix attempt) | Unbuilt | rec-5078 |
| 10 | Producer diagnostics storage; deliberation loss counter; transcript-read access log and retention | Diagnostics storage, rejection record and halt signal on rec-4026; access log and 90-day window built (Decision 239 cl.4); text retention rec-4698; loss counter with the LiteLLM producer | rec-4026; rec-4698; rec-4028 note |

rec-4606 asks for an in-repo record of `held:feedback-loop-gate` and a check that each unproduced column's owner is
live; its 2026-11-12 note carries R1 and the named gate list as input.

---

## 14. Recs filed and noted by this re-grounding

Duplicate search: `scripts.recs_related --terms` over the draft's 48 terms (2026-11-10) and seven targeted queries
plus a read of rec-5040 to rec-5089 (2026-11-11) **[recs]**. Each id was read back after its write.

| Rec | What | Priority | Origin |
|---|---|---|---|
| rec-5077 | Design the loop record journal's writer: generalised kernel or dedicated verb; covers the daily-series row kind | Medium | draft P2; R2 note |
| rec-5078 | Ops homes: covered_by and fix_attempt stamps, close-time read, durable close_proposed | Medium | draft P3 |
| rec-5079 | `input_key` on claude_code tool_call rows at the next parser bump | Low | draft P6; S3 |
| rec-5080 | Integer version per event verb beside the digest, lockstep check | Low | S1 |
| rec-5097 | Friction G1 absorb with absorbed count; G2 NULL-name closes out of repeat runs; classifier_version 2 | Medium | draft P5; R3 |
| rec-4706 (note) | `component` stamp on the telemetry reader's request line | - | draft P4, folded |
| rec-4606 (note) | rec_ids hold ends at back-validation planning; the named gate list | - | R1, S4 |
| rec-4438 (note) | Monitor per-day counts become a journal row kind | - | R2 |
| rec-4028 (note) | Owner the Agents in GitHub workspace; reasoning mapping reviewed here; loss counter | - | R5 |

Not filed: draft P1 (derive `rec_ids` at read), withdrawn by R1. Hygiene, not done: rec-4026's title still names
the friction classifier, which is built; retitle it when next touched.

---

## 15. Waves and their prerequisites

| Wave | Component | On main | Waits on (owner) |
|---|---|---|---|
| 2 | friction classifier | per-session verb, rules, classifier_version, vectors | rec-4605, rec-4959, rec-5097 (this workspace); rules guard; unmapped-share window verb; rec-4474 for windows; c3 label review (operator) |
| 2 | deliberation capture | inputs write-enforced; monitor home | field adds and text-bearing marker (this workspace); delib-q4 (operator); verb build; rec-4028 for the LiteLLM lane (Agents in GitHub) |
| 3 | Goodhart register | drill identity, real-only windows, arrival oracle | CD.46, CD.47 (operator); rec-5077; seed windows; rec-4233 for liveness (CI Reform) |
| 3 | cost and egress budget | writer attribution, blob meters, token rollups, cost verb | CD.50, budget-q2 (operator); rec-4706; catalog_stats schedule and filter; rec-4734 |
| 3 | rec filer | fingerprint backstop for ci_rca | CD.47 (operator); rec-5077, rec-5078; subject window verb; W3-2, W3-6 (operator); the ci-rca filer answer (section 16) |
| 4 | back-validation | checkout columns, read_version, rec-4592 | CD.48, CD.47, backval-q1, backval-q2 (operator); rec-4426, rec-4641, rec-4963, rec-5056; R1's choice; rec-5078; the filer |
| 4 | maturity ladder | classifier_version, read_version digest | CD.46, CD.47, P7, fixture fate (operator); rec-5080; settled series; Decision 235 cl.7 for its lane |
| 4 | allow-list transport | write-enforced vocabularies | CD.51, k1-k4, W3-4, allow-q2 (operator, always-ask); rec-4141 post-MVP |

T3.4's completion also needs T1.13 (CI-RCA methodology): in_progress, with c2 (strict-mode flip after back-validation
and c7), c3 (warn-mode rejection rate below 5 percent over 7 days), c5 (evidence bundle SHA round trip) and c9
part (a) (the dispute throttle rec-2415) open **[repo: docs/ROADMAP-PLATFORM.yaml T1.13]**.

---

## 16. Open items for the operator (listed, not answered here)

- **The feedback-loop gate** (operator, 2026-11-10): no wave 2-4 plan or build starts until alarm triage; the
  GitHub Environment tidy (rec-4913, rec-4914) and the content reader lock (rec-4696); the friction recs rec-4605
  and rec-4959; and the operator's filing of CD.46-CD.52 with the open answers below are complete. Read-only
  re-grounding (this report) may run first.
- **File CD.46, CD.47, CD.48, CD.50, CD.51** with their REPORT-w3 section 9 amendments, through Decision 246
  approval runs; CD.45 is the highest candidate decision on main. CD.49 is retired into CD.50. CD.52: substance
  landed (C13); its T2.52 reactivation half is open.
- **W3-1 to W3-6** (REPORT-w3 section 6). W3-1 is moot in practice given C13; W3-3 (MED-9 anchor): no Decision names
  MED-9.
- **k1-k4** (always-ask), **delib-q4, budget-q2, backval-q1, backval-q2, allow-q2**; capture-q3 is built in
  practice (rec-4147 closed).
- **CD.47 journal writer** (B2 item 5), then rec-5077.
- **P7** pilot schema change and whether the fixture is kept.
- **T3.3 c1-c2 and T3.4 c1-c2 staged criteria text** (REPORT-w3 section 2); T3.3's criteria are still bare strings,
  and T3.4's title still says STRATEGIC.
- **Asked of other workspaces on 2026-11-12** (coordinator log, issue #1963): who owns T1.13 and what is planned for
  the ci-rca agent; whether T1.13 c2's back-validation is ci-rca's own script; one filer versus ci-rca (Q11); any
  preferred shape for the shared journal. Their answers land on the recs named in section 17, not in this report.

---

## 17. Operator rulings recorded by this re-grounding

Each ruling names its durable home. "Settled" rows (S) were mechanical forks settled on an independent advisor's
evidence and shown to the operator before the plan was written; "ruled" rows (R) were the operator's choice.

| Id | Ruling | Date | Home |
|---|---|---|---|
| R1 | `rec_ids` and `rec_id` stay unproduced until back-validation is planned; that plan picks a merge-time annotate producer or a client-side trailer join; read-time derivation rejected (trailers live in git) | 2026-11-12 | rec-4606 note |
| R2 | The loop's daily series are settled rows in the loop record journal (CD.47); the verification monitor's per-day counts are a journal row kind; stitched windows only seed | 2026-11-12 | rec-4438, rec-5077 notes |
| R3 | Friction G1: absorb the tool_error into the gate label, and report the absorbed count | 2026-11-12 | rec-5097 |
| R4 | This report is a dated snapshot; lasting rulings live in recs | 2026-11-11 | this section |
| R5 | rec-4028 (LiteLLM producer) is the Agents in GitHub workspace's; this workspace reviews its reasoning mapping | 2026-11-11 | rec-4028 note |
| S1 | Integer verb versions beside the digest | 2026-11-11 | rec-5080 |
| S2 | Content reader deploy gates only forensic deliberation reads | 2026-11-11 | section 4; the deliberation build plan's check c1 |
| S3 | Producer `input_key` at the next parser bump | 2026-11-11 | rec-5079 |
| S4 | The gate's named list is the operator's 2026-11-10 list | 2026-11-11 | rec-4606 note |
| S5 | Tokens-only budget; rec-4031 post-MVP | 2026-11-11 | section 6; rec-4031 unchanged |
| Q9, Q11 | T1.13 ownership; ci-rca and the one-filer rule | asked 2026-11-12 | pending (section 16) |

---

## 18. Known gaps

- The W1 VPs that need git history at old SHAs or a network fetch were skipped; three long simulations did not
  finish (section 12).
- G1 and G2 were re-read against the live SQL, not re-probed with vectors (rec-5097 adds them).
- Which hooks rec-4959 affects, and the 17 friction vectors' exact coverage, were not re-derived.
- Whether the post-merge `deterministic_auto_close` venue can close a telemetry-routed acceptance without a verdict
  record was not traced through closure_gate.py.
- Production volumes beyond one window read on 2026-11-09, and the production `checkout_base_sha` null rate, were
  not measured; rec-4426's claim is taken from the rec.
- The pilot fixture's edge set was not compared with the staged edges.
- The W3 design's pinned inputs held outside this repository were not carried.
- Overlaps with other workspaces are named from rec text, the shared coordinator log and the operator's
  description only.
