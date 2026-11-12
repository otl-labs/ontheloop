# REPORT: telemetry feedback-loop re-grounding against main (2026-11-12)

> REPORT-ONLY deliverable of `docs/plans/PLAN-feedback-loop-regrounding.yaml` (Decision 86 cl.2; file-router
> report-deliverables route). A dated, point-in-time reading of the October 2026 feedback-loop designs (the eight
> wave 2-4 component reports among `docs/plans/reports/REPORT-w1-*.md`, the W2 synthesis and the W3 staging report)
> against main at d75c76f3. The other two W1 reports, `REPORT-w1-reader-verbs.md` and
> `REPORT-w1-capture-producer-wiring.md`, are wave 1, built, and out of scope. It supersedes the eight by reference
> for their premises and anchors; it edits none of them, because W1 and W3 text is cited by line elsewhere
> (Decision 221 cites REPORT-w3:423 and :627). It plans no wave 2-4 work: the operator's 2026-11-10 gate holds
> (section 16). Nothing here is built, ratified, flipped or closed. Every operator ruling and settled fork recorded
> here also lives in a rec, which this report cites (section 17); the rec is the durable home, this report is the
> snapshot. Section 11's readings are not rulings: each one's home is the Decision or design it cites.

Evidence base and claim marks:
- **[repo]** read on main at d75c76f3, with path:line where it matters. Decisions are cited by number and clause,
  not DECISIONS.md line, because new Decisions are inserted at the top of that file.
- **[recs]** read from the rec store through the ducklake-reads named verbs on 2026-11-10 to 2026-11-12.
- **[review]** a finding of the 2026-10-05 feedback-loop review, which is not in this repository; each finding this
  report relies on is restated here.
- **[probe]** a W1 plan's own verification command, re-run on 2026-11-12 at d8283f55 and again at 3f8edd12 with
  identical results (section 12).
- **[inferred]** a reading not verified. **[partial]** only partly checked.

---

## 0. Synopsis

1. **Most of the review's "cheap now" list is built** (section 2): per-verb read versions, the drill namespace and
   purpose marker, the production-pinned telemetry reader, blob reclaim, the writer's `component` stamp and request
   line, content-addressed blobs, the describe handshake, the lifecycle reading (Decision 228), friction derivation
   text, the closed `severity` set, and the window-verb reading of Decision 199 cl.1 (its 2026-10-16 Update note,
   CD.52's substance). C12 (effective-dated `cost_usd`) is contract text only.
2. **Wave 2 is smaller than the handover said.** The friction per-session verb is built and serving
   (`session_friction_rollup`, `classifier_version` 1, #1496). What remains is correctness (rec-4605, rec-4959,
   rec-5097), a rules guard, a window verb for the unmapped share, and the operator's label review. The
   deliberation verb is unbuilt. As a technical dependency it does not need the content reader deploy (rec-4696),
   because it never reads transcript content; the operator's gate still holds every wave 2 plan behind that lock
   (section 16).
3. **History depth is the hardest limit on waves 3 and 4.** Production capture counts from 2026-10-28
   (config/telemetry/arrival.yaml lost_windows). Minus the two no-backfill gaps (about 4.5 days), about 11 clean days
   exist on 2026-11-12. The register's 28-day, the ladder's 30-day and back-validation's 57-day reads cannot seed
   before about late November, early December and early January. Window reach comes next: window verbs are capped at
   7 days, cold window reads time out (rec-4474), pagination truncates with no cursor (rec-4363), and responses carry
   no completeness watermark. Snapshots are kept 30 days (SNAPSHOT_RETAIN_DAYS), so no long input can be re-read as
   it was judged. Where the loop's daily series live is ruling R2 (section 17).
4. **Back-validation's join keys are still missing where they matter.** `checkout_base_sha` is not populated on
   cloud sessions (rec-4426), and all ten session-to-merge columns held under `held:feedback-loop-gate` (`rec_ids`
   among them) are unproduced; the operator ruled they stay held until back-validation is planned (R1).
5. **The ops-side homes are unbuilt** (rec-5078): five statuses, `close_proposed` only printed, no `covered_by` or
   fix-attempt stamp, no close-time read. The /implement relevance bypass is closed (rec-4592).
6. **The loop record journal (CD.47) is shared and PROD-only.** Decisions 221 cl.7, 231 and 235 and CD.47-r1 put
   loop records, init attempts (rec-4388), persona-eval runs, shadowed-check verdicts and operator decisions in one
   journal. The append kernel cannot write it (rec-5077). SIT resets weekly and event verbs refuse off the PROD data
   path (Decision 252), so the journal lives in PROD.
7. **"One filer" covers more than ci-rca.** Two machine filing paths are live: ci-rca, the only caller of the
   portal's fingerprint backstop, and `scripts/rec_episode.py`, which files, updates and closes recs for the
   convergence-health, backlog-health, cost-reconciliation, telemetry-verification and capacity alarms (section 7).
8. **The allow-list's premises moved again**: 19 closed vocabularies, not 15; no `enforced: false` flags; its column
   inventory no longer matches; Decision 221 cl.1 keeps `cost_usd` out of any crossing set.
9. **New since October and since the 2026-11-10 draft** (section 1): Decisions 249, 250, 252 and 253; two
   no-backfill capture gaps; the arrival oracle dates lost-window excuses by commit author date; the inference tier
   selection (#1969) changes the LiteLLM lane's reasoning premise.
10. **Wave order holds; prerequisites changed** (section 15). T3.4 also needs T1.13, whose owner was asked of the
    other workspaces and is not settled (section 16).

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

### 1.2 Since the 2026-11-10 draft (f866f98b to d75c76f3)

- src/, the four telemetry contracts, parser_versions.yaml, scripts/rec_episode.py, scripts/ops_data_portal.py and
  the W1, W2 and W3 reports are unchanged **[repo: git diff]**. Other probe inputs did move, so every W1 probe was
  re-run (section 12): docs/contracts/inference-provider.yaml (#1969, below), .claude/hooks/hooks.json (#1955, the
  checkout locator; section 3), docs/DECISIONS.md and docs/ROADMAP-PLATFORM.yaml.
- The arrival oracle changed: #1967 excused a re-opened outage-era session (rec-5081) and #1973 dates a lost-window
  excuse by commit author date, not PR creation time (rec-5084) **[repo]**.
- rec-5055 (hook outage) is closed and the arrival check is green again **[recs]**.
- A second no-backfill capture gap, 2026-11-09T22:33Z to 2026-11-11T10:38:16Z, joins the 3-6 November one
  (rec-4939) **[repo: config/telemetry/arrival.yaml:9-10]**. Any baseline window that spans either is short by
  design.
- **Inference tier selection** (#1969, T4.32 c1): LiteLLM tier 1 is now `deepseek/deepseek-flash`, and thinking and
  effort are per-persona settings in `litellm_persona_selection` (thinking enabled, effort high for every persona,
  operator rulings 2026-11-11), never a choice of model (docs/contracts/inference-provider.yaml:385-397) **[repo]**.
- **Decision 252** (2026-11-10): once SIT stands, every merge deploys to SIT and PROD is promote-only, the same
  artifact behind one operator approval (cl.6); SIT agents read production-derived data only through named verbs
  (cl.5); SIT resets weekly and keeps no history (cl.4). Event verbs refuse off the PROD data path
  (`require_production_pin`, src/common/ducklake_event_reads.py:97-110), so loop verbs cannot be exercised in SIT
  and the journal is PROD-only. **Decision 253**: the catalog moves to RDS PostgreSQL, SIT first; the named-verb
  surface is unchanged, and the Neon egress premise of the W1 budget's catalog-egress line goes (section 6)
  **[repo]**.
- #1977 added the plan_agent and implement_agent persona contracts and re-sequenced T4 roadmap rows; no loop
  design or telemetry contract changed **[repo: git diff]**.
- New plans on main, not yet built: PLAN-persona-run-identity-contract (#1975) adds a nullable
  `telemetry_agents.definition_sha256`, and its unplanned plan 2 is a claude_code producer change **[repo]**. The
  friction correctness work (rec-4959) is expected to take parser_version 10, and that producer the next version
  (coordinator log, 2026-11-12) **[reported]**: two more derivation changes for any series keyed on parser
  version (section 9).
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
| C3 | decide whether the journal goes through the kernel | Not decided. The kernel refuses any table not partitioned on `session_started_at`, any dedupe key other than (producer, event_id, parser_version), and any table with no KEY_PLANS entry (rec-5077) | [repo] src/telemetry/append.py:89-111 |
| C4 | pin the reader to the telemetry catalog | Built: the telemetry reader refuses every event verb unless pinned to production | [repo] src/common/ducklake_event_reads.py:97-110 |
| C5 | reclaim noncurrent blob versions | Built: `telemetry-blobs-noncurrent-reclaim` | [repo] terraform/personal/main.tf |
| C6 | request-level `component`, one log line per request | Built on the writer (`telemetry_request` line with verb, component, rows per table, bytes, status). The telemetry reader logs one `ducklake_invocation` line per request (action, status, connect_ms, connect_reused, reopened, elapsed_ms, response_bytes) but not the served verb, error_type or `component`; rec-4706 adds the first two, and its 2026-11-11 note adds `component` | [repo] src/lambdas/ducklake_telemetry_writer/telemetry_log.py:104-133; src/common/ducklake_metrics.py:77-88; the reader's handler is src/lambdas/ducklake_telemetry_reader/handler.py:212, its log call :250 |
| C7 | content-addressed blobs, skip-if-exists, log bytes | Built | [repo] src/common/ducklake_telemetry_blobs.py |
| C8 | describe advertises accepted parser versions | Built differently: describe advertises a per-table schema view and fingerprint, deliberately with no parser_version ceiling | [repo] src/common/ducklake_telemetry_handshake.py:1-7 |
| C9 | one lifecycle text | Built and amended by Decision 228 | [repo] Decision 228; telemetry_sessions.yaml governance_notes |
| C10 | friction derivation text | Built | [repo] docs/contracts/telemetry_sessions.yaml |
| C11 | close `severity` | Built: [info, warning, error, critical], required on process_event points | [repo] docs/contracts/telemetry_observations.yaml |
| C12 | `event_timestamp` and provider in `cost_usd` inputs | Contract text only: the derivation names an effective-dated price at the row's event_timestamp but is `realized: false`, and `model_call_cost_estimate` applies no price (no price table exists) | [repo] docs/contracts/telemetry_observations.yaml:256-262; src/common/ducklake_event_read_sql.py:248-262 |
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
- #1955 added two plugin hooks (the checkout locator, `.claude/hooks/locate_checkout.bash`) whose derived names are
  the fallbacks `hook:SessionStart.Bash` and `hook:UserPromptSubmit.Bash`; no specific rule names them, so their
  failures fall to the generic rules (`r90`, `r91`) **[repo: `hook_name` over .claude/hooks/hooks.json]**.

**Defects with a rec** (review G1 and G2, re-read against the live SQL, not re-probed): G1, the absorb guard skips
only a `blocked` tool_call whose hook child is an error (ducklake_event_read_sql.py:275-277), so a `gate:`
process_event at error under an `error` Bash close yields two facts; G2, the repeat-run partition is
`(parent_observation_id, name)` (:279-280, :284-285), and orphan closes carry a NULL name, so unrelated orphan errors under
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
sidecar marks those roots by workflow. Who owns producer admission (rec-4415, rec-4420) is undecided and with the
operator **[asked]**. A friction window over CI agents and interactive sessions together needs a workflow filter,
because both are `claude_code`; `tool_use_rollup` already returns per-tree, per-tool call, error and blocked counts
with a `workflow` column (src/common/ducklake_event_read_verbs.py:166-176), so the filter exists for tool counts
**[repo; inferred for friction]**. The LiteLLM lane writes no tool_call rows until rec-4028 lands **[repo: no litellm entry in
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
`content` or `content_uri` (REPORT-w1-deliberation-capture.md:122-124), so as a technical dependency it reads
through the deployed telemetry reader and does not need the content reader deploy (rec-4696); only the forensic
read (delib-k3) does (S2, noted on rec-4696). This lifts nothing: the operator's gate still holds a deliberation
plan behind the rec-4696 lock (section 16). The build keeps its check c1 that the verb selects no content column (REPORT-w1-deliberation-capture.md:435);
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
physical column), the response-shape home (delib-q4, operator), the drift check, durable per-day history (rec-4438;
its home is ruling R2), the loss counter (delib-k2, now with rec-4028), retention of stored text
(rec-4698) and erasure (rec-4029, Low) **[recs]**.

**Waits on:** the field adds and the text-bearing marker (contract amendments in this workspace's remit); delib-q4
(operator); for the LiteLLM lane, rec-4028.

**Overlap:** rec-4028 (LiteLLM telemetry producer) is the Agents in GitHub workspace's (ruling R5); its DeepSeek
verdict personas are the "full" reasoning lane and its spend guard reads rec-4028's model_call rows (rec-5021) **[recs]**.
Since #1969 that lane is one model, `deepseek/deepseek-flash`, with thinking enabled and effort high set per persona
in `litellm_persona_selection`, not a choice between a chat and a reasoner model
(docs/contracts/inference-provider.yaml:385-398). So the deliberation class for that lane reads the persona's
thinking setting and the call's reasoning fields, never the model id; `reasoning_effort` is already a
reasoning field on model_call rows (section 12) **[repo]**. A thinking or effort change is not covered by a
persona's definition hash, and rec-5072 owns the reset rule for it (:397-398); a per-persona ladder lane needs the
same restart key. This workspace
reviews the reasoning mapping when rec-4028 is planned; rec-4508 (a model that writes chain of thought into content)
sits beside it **[recs]**.

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
- The capture counter's denominator (capture-q1, SessionStart invocations) has a partial substitute, not an
  answer: Decision 250's arrival oracle checks every merged commit's `Claude-Session` trailer against
  `harness_session_key` (scripts/telemetry_verify/session_presence.py), and quiet days read as skipped, never green
  (cl.2). Its denominator is merged sessions only, by declaration (cl.5), so CI agents (never captured, rec-4415)
  and sessions that never merge (read-only, abandoned) are invisible to it. W2 calls capture-q1 the critical path
  for the whole loop (REPORT-w2-feedback-loop-synthesis.md:14-27, :125): the register reads capture as
  counter_dark until a denominator exists, and every downstream detector then stays at read_all. capture-q1 stays
  the operator's (section 16), and so does W2's review-load envelope (about 60 reviews a day so that wave 2's
  read_all period fits under the 0.8 warning line), which this report did not re-ground.
- The deliberation counter is constant (section 4).
- Smoke sessions under `repo:deploy-smoke-auto-*` derive purpose real and pass the real filter (rec-4611); a series
  read from the smoke catalog would be inflated; production windows are not affected **[inferred]**.

**Not built:** the register table, its daily series, the drill log and the verdict job.

**Waits on:** CD.46 and CD.47 (operator); capture-q1 and the review-load envelope (operator); the series home (R2,
rec-5077) and rec-5080 for its version key; history depth (28 days of clean capture, about late November) and
window reach for the seed read; rec-4233 for sensor liveness of a scheduled register job (owner not settled; it is
not among the recs the CI Reform workspace has claimed) **[recs; ownership asked]**.

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

**Still holds:** the budget is in physical units (tokens, GB-seconds, bytes, requests) with no price table (S5,
noted on rec-4031). Cloud sessions are `fixed_non_rollover_allowance`, so they derive no dollars; Decision 221 cl.1
makes "dollars derive at read and are never stored or published" Decided and names "tokens and GB-seconds per
verified merge" a PROCESS metric, and cl.5 publishes tokens and GB-seconds, never dollars.

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
  verb, error_type and `component`, all now on rec-4706. Its `response_bytes` is the size of each read's response,
  not catalog egress **[repo]**.
- Decision 253 moves the catalog off Neon to RDS PostgreSQL. The W1 design's first-breaching line was the Neon
  plan's included egress; CD.50's catalog-egress seed is "the Neon plan's actual included egress" (REPORT-w3:411,
  :606), its calibration is the monthly Neon invoice and budget-k4 (b) is a read-only Neon key (REPORT-w3:591). Those
  premises go; the envelope and its calibration need a new source once the RDS catalog runs **[repo; source not
  chosen]**.
- telemetry_observations.yaml's `cost_usd` text still says "MVP static price table, rec-4031" (:256-260), which
  S5 contradicts; rec-4031's 2026-11-12 note asks the next contract amendment to align it **[repo + recs]**.

**Not built:** the register, ledger and verdict; the price table (rec-4031, Low, post-MVP); a scheduled egress
reading; loop-owned model call marking.

**Waits on:** CD.50 (re-seeded after Decision 253) and budget-q2 (operator); rec-4706; rec-4734 (cost
reconciliation has never run, High), because c3 shows the prior month's reconciliation residual beside the register
**[recs]**. A price table returns only if a metered lane joins this loop's budget (S5).

**Overlap:** storage cost (rec-4729) is the Cost Optimisation workspace's per the operator (2026-10-29); cost
reconciliation (rec-4734) probably is too **[inferred; neither rec names an owner]**. The Agents in GitHub workspace's DeepSeek personas are the first
metered lane, with their own spend guard (rec-5021); persona-eval spend is its own line, never `loop_llm_tokens`
(Decision 231 cl.4; rec-4436). The budget reads their spend, never re-meters it.

---

## 7. Rec filer with dedupe (REPORT-w1-rec-filing-dedupe.md)

**What the design asked:** one fingerprinted rec per (detector, subject, window) finding; file, update, hold or
suppress against the open rec; covered_by links; a run record per run; T3.3's "files anomaly recs".

**Built** **[repo]**: no loop filer. Two machine filing paths are live, and every write goes through the one portal
(scripts/ops_data_portal.py; AGENTS.md Single Portal Invariant):
- ci-rca (.github/workflows/ci-rca.yml, scripts/ci_rca/), the only caller of the portal's fingerprint dedupe
  backstop, which is keyed to `source == "ci_rca"` (scripts/ops_data_portal.py:315-332).
- `scripts/rec_episode.py` (`find_recs`, `decide`, `run_episode`, :70-236): a scoped read of open recs by source,
  then a four-state file, update, close or none decision. Its callers are scripts/convergence_health/ (escalate,
  sensor_liveness, budget_ingest, code_drift, assess), scripts/backlog_health/escalate.py,
  scripts/cost_reconciliation.py, scripts/telemetry_verify/alarms.py, scripts/ci/actions_capacity_alarm.py and
  scripts/checks/_budget_recs.py, filing under machine sources such as `loop_liveness_stale` (rec-5094 is an open
  example) **[repo + recs]**. It keys on source (plus an optional sub-key), not on a finding fingerprint; a
  declined rec is not open, so the same finding is filed again; and it closes when the trigger clears, where the
  W1 filer holds (REPORT-w1-rec-filing-dedupe.md:18-20, :60-62).

**Still holds:** the decision logic **[review]**.

**Stale, with the corrected anchor** **[repo]**:
- "The loop's filer is the only machine filer" is false (above); W1 assumed ci-rca alone.
- Status is five values [open, closed, failed, declined, superseded] (docs/contracts/ops_recommendations.yaml); the
  SQL's `in_progress` and `deferred` arms are dead and `failed` falls to suppressed.
- `file` is a required rec field; the design names none.
- `close_proposed` is a printed command, never a status (docs/contracts/recommendation-relevance.yaml).
- `covered_by` has no write path and no named verb returns a rec's close time (rec-5078).
- /implement never closes a bundled rec (`implement_gate`, rec-4592 closed) **[repo + recs]**.
- Findings need a per-(label, tool) subject with exposure counts. `sessions_window` returns state, outcome,
  duration and stamps only (src/common/ducklake_event_read_sql.py:155-177), but `tool_use_rollup` already returns
  per-tree, per-tool call, error, blocked and interrupted counts with a workflow column (sql.py:501-515), a ready
  exposure source for tool subjects. A branch-keyed subject inherits rec-5061.

**Not built:** the filer, its run record, a friction-label subject window verb with exposure, the filer's source
registry entry and priority (a writer deploy under Decision 210 cl.4), the ops homes (rec-5078).

**Waits on:** CD.47 (run records) and rec-5077; rec-5078; a subject window verb (the filer's own build plan); W3-2
and W3-6 for T3.3's criteria (operator); the one-filer reading below. T3.3's exit criteria are still two bare
strings with "via log-rec" (docs/ROADMAP-PLATFORM.yaml T3.3) **[repo]**.

**Overlap, and what "one filer" means:** Decision 221 cl.7 (Decided) says the loop has one filer. With two live
machine filing paths, it needs a reading before the filer is planned. An independent advisor (2026-11-12, given the
repository facts only) read it as one finding-identity, dedupe and write contract enforced at the portal, with
several producers, not one code path; ci-rca's fingerprint chain is the model for identity and the write-time
backstop (the W1 design already generalises it as `sha256(salt, detector_id, subject_key)`,
REPORT-w1-rec-filing-dedupe.md:30-36, :111-116), and rec_episode is a model only for orchestration shape. In
practice that reading generalises the backstop from `ci_rca` to any machine source that sends a fingerprint, builds
the loop's filer on it, leaves ci-rca's code alone while T1.13 is in flight elsewhere, migrates rec_episode callers
as they are next touched, and keeps close policy per producer class. This is advice, not a ruling. Any reading that
leaves ci-rca or rec_episode outside "one filer" needs a route: either a numbered amendment of Decision 221 cl.7, or
a recorded reading that the clause covers only the loop's own detectors. The question was asked of the CI Reform and
Agents in GitHub workspaces on 2026-11-12 (section 16); the posted framing named ci-rca the only machine filer
and was corrected there the same day.

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
- `addressed` needs `rec_ids`, still unproduced under `held:feedback-loop-gate`. The same hold covers ten
  session-to-merge columns: telemetry_sessions `rec_ids`, `plan_slug`, `pr_url`, `ci_outcome`, `files_changed`,
  `lines_added`, `lines_removed`, `scope_drift_files`, and `rec_id` on observations and transcripts
  (telemetry_sessions.yaml realization.unproduced; telemetry_observations.yaml:472; telemetry_transcripts.yaml:339).
  Decision 221 cl.1's "per verified merge" metrics need the same join. Ruling R1: all ten stay held until
  back-validation is planned, which chooses a merge-time annotate producer or a client-side trailer join (one
  already walks the trailers: scripts/telemetry_verify/session_presence.py); a reader verb cannot derive them,
  because the trailers live in git. Both routes miss sessions that never merge (rec-4606 note).
- The ancestry test itself is client-side too: a fix C reaches a segment when C is an ancestor of
  `checkout_base_sha` (telemetry_sessions.yaml checkout_base_sha semantics), a git computation. A session with
  several segments needs a rule for which segment counts, and no event verb returns the checkout columns today
  **[repo: grep of ducklake_event_read_sql.py]**.
- History depth: about 11 clean production days exist on 2026-11-12 (section 0), so a 57-day read cannot seed before
  about early January 2027, later if another no-backfill gap opens.
- The 57-day span cannot be one call (cap 7 days, ducklake_event_read_verbs.py:17). Stitching nine 7-day calls
  client-side, as scripts/telemetry_usage.py does for tree rows, is exact only if no page truncates (window pages
  truncate with no cursor, rec-4363) and every page's `read_version` is equal; telemetry_usage.py does not check
  `read_version` today, so that check is new. Snapshots are kept 30 days (SNAPSHOT_RETAIN_DAYS,
  src/common/ducklake_maintenance.py:85), so days older than that cannot be re-read as they were. Where the settled
  daily series live is ruling R2.
- backval-q2 (effective day of a fix not live at merge): Decision 249 deploys only the newest full-tier-green
  commit, so today a fix's effective day is its Decision 249 deploy day. Once SIT stands (T2.57, T2.58), Decision
  252 cl.6 makes PROD promote-only, and the PROD effective day becomes the promotion day; backval-q2's "deploy's own
  timestamp" then reads the promotion record **[repo]**.
- The post-merge `deterministic_auto_close` venue remains (recommendation-relevance.yaml); whether it can close a
  telemetry-routed acceptance without a verdict record was not traced **[partial]**.

**Not built:** the verb, the module, CD.48's fix-attempt stamp (now with rec-5078) and census arm, the verdict
table (a CD.47 kind).

**Waits on:** CD.48 and CD.47 (operator); backval-q1 and backval-q2 (operator rows); rec-4426, rec-4641, rec-4963;
R1's choice at planning; rec-5056 for any `harness_session_key` join; about 57 clean days of capture and window
reach for seeding; the filer. Both capture gaps (section 1.2) sit inside any baseline that spans them.

**Overlap:** T1.13 c2 flips CI_RCA_STRICT_MODE "after Phase 4 back-validation"; this report reads that as ci-rca's
own script (scripts/ci_rca/back_validation.py, rec-2503), not this component **[inferred; asked, section 16]**.
T1.13 c2 also cites docs/INTENT-ci-rca-methodology.md, which is absent on main **[repo]**.

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
- Derivation cadence: claude_code parser versions 3 to 9 landed in 24 days (config/telemetry/parser_versions.yaml:20-57),
  and two more producer changes are expected (section 1.2). The ladder restarts a lane on every stamp bump and needs
  30 days of signal (REPORT-w1-maturity-ladder.md:721-750), so a lane keyed on parser_version (deliberation's)
  would rarely clear at that cadence. A lane's restart key needs to be the derivation it actually reads (its
  verb's integer version under S1, or a narrower declared input), not the producer's parser version
  **[repo; inferred consequence]**.
- History depth: about 11 clean days exist (section 0), so no lane can clear a 30-day window before about early
  December.
- The pilot fixture holds 10 items and 16 edges (docs/work-item-pilot/telemetry-feedback-loop.yaml) **[repo]**; the
  edges were not compared with the staged set **[partial]**. P7 (the pilot schema change) is the operator's.

**Not built:** controller, review log, transition log, settled daily series.

**Waits on:** CD.46 (rec-4581 and rec-4512 track placeholder citations) and CD.47 (review and transition kinds);
rec-5080; 30 clean days of capture; P7 and the fixture's fate (sunset is the earlier of T4.23 c6 and 2027-01-31); Decision 235 cl.7 for its
lane.

**Overlap:** Decision 231's persona lane shares the register rows and journal, and Decision 221 cl.3's control arms
are ladder lanes. Whether this review ladder is T3.4's A0-A3 or a separate ladder sharing one controller and one
transition log is ladder-q1 (REPORT-w3:627; the design says separate). Decision 235 cl.2 puts a verification
lane on this controller and makes the executor's T4.4 gate necessary, never sufficient, for it. rec-5072 restarts a verdict count on a settings change, the same
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

## 11. Readings on evidence (mechanical forks)

Readings, not rulings: each row restates what its cited Decision or design already says, so that text is its home.
The forks the operator was shown (S1-S5) have rec homes in section 17.

| Fork | Reading | Home |
|---|---|---|
| Where the corrections land | This report, superseding the eight W1 reports by reference; no in-place edit of W1 or W3 | Decision 221 cites REPORT-w3 by line; W1 unchanged since 2026-10-05 |
| Seeding a long read | Stitch 7-day windows client-side, as scripts/telemetry_usage.py does for tree rows, adding two checks it lacks: refuse the run if any page truncates (rec-4363) or any page's `read_version` differs | src/common/ducklake_event_read_verbs.py:17; ducklake_event_reads.py:264-285; rec-4363 |
| Deliberation drift monitor home | The telemetry-verification workflow | ROADMAP T3.20 c3 met; Decision 250 cl.3 |
| `cost_usd` in any crossing set | Excluded. This settles nothing in CD.51 k1, which stays always-ask; it records that Decided text already forbids publishing dollars | Decision 221 cl.1, cl.5 |
| CD.52's substance | Landed as Decision 199's 2026-10-16 Update note; later clause-1 changes need a numbered Decision | Decision 199 Update; REPORT-w3:479 |

---

## 12. Probe re-runs (each W1 plan's own hermetic VPs, 2026-11-12 at d8283f55 and 3f8edd12)

Each W1 plan's hermetic verification steps were re-run with a 200 s limit per step; steps that fetch old SHAs, run
`--pre` or render the plan document were skipped. "Pass" means exit 0 and every `expected_literals` string printed.
The 2026-11-10 run at f866f98b is superseded, because probe inputs moved in between (section 1.2). The suite ran at
d8283f55 and again at 3f8edd12, after #1977 changed probe inputs (docs/contracts/, docs/ROADMAP-PLATFORM.yaml);
every step's status and output were identical. Raw outputs are not committed. **[probe]**

| Component | Steps run | Pass | Differs from the pinned literal (what moved) | Did not finish |
|---|---|---|---|---|
| friction | VP 1, 2, 4, 5, 6 | 2, 6 | VP 1 raises `TypeError` (`build_observations` now needs `sidecars`, #1405). VP 4: registered hooks in .claude/settings.json 13 to 21; none falls back to `hook:PreToolUse.Bash` (the plugin hooks' fallbacks are in section 3). VP 5: rework and exception inputs widened to the k2 set | - |
| deliberation | VP 1-5 | 4, 5 | VP 1: parser_version 2 to 9; diagnostics also count `absent_usage_key`. VP 2: parser 9; `reasoning_effort` joined the reasoning fields; LiteLLM tier 1 is `['deepseek/deepseek-flash']`, was `['deepseek/deepseek-chat', 'deepseek/deepseek-reasoner']` (#1969). VP 3: fails in the pinned litellm 1.106.2 and mcp 1.30.0 pair, whose tool-call path imports `mcp.types.CacheableResult`, absent in mcp 1.30.0 (rec-3795) | - |
| Goodhart register | VP 2-5 | 3, 5 | VP 2: two failure_signal readers exist (was 0); `drill_markers=0` because the probe misses `ops_projects.purpose` | VP 4 (over 200 s) |
| cost and egress | VP 2-5 | 3, 5 | VP 2: a telemetry catalog-stats alarm exists (`tf_catalog_stats_lines` 0 to 1); capture hooks include SessionEnd, Stop, StopFailure and UserPromptSubmit | VP 4 (over 200 s) |
| rec filer | VP 1-5 | all | - | - |
| back-validation | VP 1-5 | 1, 2, 3, 5 | VP 4: `session_code_cols` is `[checkout_base_sha, checkout_sha]` (was empty) | - |
| maturity ladder | VP 1, 2, 4, 5, 6 | 1, 2, 4, 6 | - | VP 5 (over 200 s) |
| allow-list | VP 1, 2, 4-7 | 4, 5, 6, 7 | VP 1: 19 vocabularies (was 15). VP 2: fidelity 136/140, column order 1/4 | - |

Reading: the designs' internal logic still passes wherever it ran; what differs is where main moved under them.
Three long runs did not finish and were not retried **[partial]**.

---

## 13. The ten unowned requirements (review section 4), with owners

| # | Requirement | State | Owner |
|---|---|---|---|
| 1 | SessionStart denominator (capture-q1) | Partly substituted: Decision 250's arrival oracle covers merged sessions only; CI agents and non-merging sessions stay invisible | operator (section 16) |
| 2 | `rec_ids` and `rec_id` population, and the other eight held session-to-merge columns | Held until back-validation is planned (R1) | rec-4606 note; the back-validation plan |
| 3 | CD.47 journal writer | Operator decision first (handover B2 item 5), then design | rec-5077 |
| 4 | reader-q2 shadow re-derivation | Done in slice 4 | rec-4025 closed |
| 5 | DQ-monitor substrate | Exists (telemetry-verification workflow); drift check unbuilt; durable per-day counts are rec-4438, stored as ruling R2 says | deliberation build; rec-4438 |
| 6 | Self-describing gate markers | Done | rec-4172 closed |
| 7 | MVP price map | Not needed for this loop: `cost_source` exists, no price table; the budget is in physical units (S5) | rec-4031 (post-MVP, S5 note) |
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
| rec-4606 (note) | All ten held session-to-merge columns stay held until back-validation planning; the named gate list (rec-4914 declined; CD.46, 47, 48, 50, 51); this report's path | - | R1, S4 |
| rec-4438 (note) | Where the monitor's per-day counts are stored | - | R2 |
| rec-5077 (note) | The journal's series row kind and its grain, as R2 rules | - | R2 |
| rec-4696 (note) | Technical dependency only: the deliberation verb reads no content; the gate still holds | - | S2 |
| rec-4031 (note) | Post-MVP stands: the budget is in physical units, no price table; the contract's `cost_usd` wording to align | - | S5 |
| rec-4028 (note) | Owner the Agents in GitHub workspace; reasoning mapping reviewed here; loss counter | - | R5 |

Not filed: draft P1 (derive `rec_ids` at read), withdrawn by R1. Hygiene, not done: rec-4026's title still names
the friction classifier, which is built; rec-5077's acceptance test presumes its option (a) (a generalised kernel)
although the rec asks for a choice between (a) and (b); both are for whoever next touches those recs.

---

## 15. Waves and their prerequisites

Earliest seed dates assume no new capture gap: 28 clean days about late November, 30 about early December, 57 about
early January 2027 (section 0).

| Wave | Component | On main | Waits on (owner) |
|---|---|---|---|
| 2 | friction classifier | per-session verb, rules, classifier_version, vectors | rec-4605, rec-4959, rec-5097 (this workspace); rules guard; unmapped-share window verb; rec-4474 for windows; c3 label review (operator) |
| 2 | deliberation capture | inputs write-enforced; monitor home | field adds and text-bearing marker (this workspace); delib-q4 (operator); verb build; rec-4028 for the LiteLLM lane (Agents in GitHub) |
| 3 | Goodhart register | drill identity, real-only windows, arrival oracle (merged sessions only) | CD.46, CD.47, capture-q1, review-load envelope (operator); rec-5077 and rec-5080 for the series key; 28 clean days and seed windows; rec-4233 for liveness (owner not settled) |
| 3 | cost and egress budget | writer attribution, blob meters, token rollups, cost verb (no price) | CD.50 re-seeded after Decision 253, budget-q2 (operator); rec-4706; catalog_stats schedule and filter; rec-4734 |
| 3 | rec filer | two machine filing paths (ci-rca with the fingerprint backstop; rec_episode) | CD.47 (operator); rec-5077, rec-5078; subject window verb; W3-2, W3-6 (operator); the one-filer reading (section 7, asked in section 16) |
| 4 | back-validation | checkout columns, read_version, rec-4592 | CD.48, CD.47, backval-q1, backval-q2 (operator); rec-4426, rec-4641, rec-4963, rec-5056; R1's choice; rec-5078; the filer; 57 clean days |
| 4 | maturity ladder | classifier_version, read_version digest | CD.46, CD.47, P7, fixture fate (operator); rec-5080; settled series (R2); 30 clean days; a restart key per lane, not parser_version |
| 4 | allow-list transport | write-enforced vocabularies | CD.51, k1-k4, W3-4, allow-q2 (operator, always-ask); rec-4141 post-MVP |

T3.3 depends on T3.2, which is complete. T3.4's completion also needs T1.13 (CI-RCA methodology): in_progress, with
c2 (strict-mode flip after back-validation and c7), c3 (warn-mode rejection rate below 5 percent over 7 days), c5
(evidence bundle SHA round trip) and c9 part (a) (the dispute throttle rec-2415) open **[repo:
docs/ROADMAP-PLATFORM.yaml T1.13]**.

---

## 16. Open items for the operator (listed, not answered here)

- **The feedback-loop gate** (operator, 2026-11-10): no wave 2-4 plan or build starts until alarm triage; the
  GitHub Environment tidy (rec-4913; rec-4914 has since been declined) and the content reader lock (rec-4696); the
  friction recs rec-4605 and rec-4959; and the operator's filing of the staged candidate decisions with the open
  answers below are complete. In practice the filing set is CD.46, CD.47, CD.48, CD.50 and CD.51: CD.49 is retired
  into CD.50, and CD.52's substance landed as Decision 199's 2026-10-16 note (its T2.52 reactivation half is
  open). Read-only re-grounding (this report) may run first. S2 is a technical reading and lifts none of this.
- **File CD.46, CD.47, CD.48, CD.50, CD.51** with their REPORT-w3 section 9 amendments, through Decision 246
  approval runs; CD.45 is the highest candidate decision on main. CD.47 is filed with the numbered Decision R2 needs.
  CD.50's catalog-egress seed needs a new source after Decision 253 (section 6).
- **capture-q1** (the register's capture denominator): Decision 250's oracle sees merged sessions only (section
  5). W2 calls this the loop's critical path. With it, **W2's review-load envelope** (about 60 reviews a day,
  REPORT-w2-feedback-loop-synthesis.md:19-27), not re-grounded here.
- **W3-1 to W3-6** (REPORT-w3 section 6). W3-1 is moot in practice given C13; W3-3 (MED-9 anchor): no Decision names
  MED-9.
- **k1-k4** (always-ask), **delib-q4, budget-q2, backval-q1, backval-q2, allow-q2**; capture-q3 is built in
  practice (rec-4147 closed).
- **CD.47 journal writer** (B2 item 5), then rec-5077.
- **P7** pilot schema change and whether the fixture is kept.
- **T3.3 c1-c2 and T3.4 c1-c2 staged criteria text** (REPORT-w3 section 2); T3.3's criteria are still bare strings,
  and T3.4's title still says STRATEGIC.
- **Asked of other workspaces on 2026-11-12** (coordinator log, issue #1963): who owns T1.13 and what is planned for
  the ci-rca agent; whether T1.13 c2's back-validation is ci-rca's own script; what "one filer" covers, now that two
  machine filing paths are live (section 7; the posted framing named ci-rca alone and was corrected the same day);
  any preferred shape for the shared journal. Ownership of producer admission (rec-4415, rec-4420), rec-4233 and
  T1.13 is not settled. Answers land on the recs named in section 17, not in this report.

---

## 17. Operator rulings recorded by this re-grounding

Each ruling names its durable home. "Settled" rows (S) were mechanical forks settled on an independent advisor's
evidence and shown to the operator before the plan was written; "ruled" rows (R) were the operator's choice.

| Id | Ruling | Date | Home |
|---|---|---|---|
| R1 | All ten session-to-merge columns held under `held:feedback-loop-gate` (`rec_ids`, `plan_slug`, `pr_url`, `ci_outcome`, `files_changed`, `lines_added`, `lines_removed`, `scope_drift_files`, and `rec_id` on observations and transcripts) stay unproduced until back-validation is planned; that plan picks a merge-time annotate producer or a client-side trailer join; read-time derivation rejected (trailers live in git) | 2026-11-12 | rec-4606 note |
| R2 | The journal stores verdict records (each carrying the numbers it judged and its read version) and one settled daily observation kind as the loop's series source: one insert-only row per (tenant_id, project_id, metric_id, UTC day, read_version, integer verb version, settled_at) with numerator and denominator, never a ratio, plus the producing verb, the parser and classifier versions seen, the catalog snapshot and a lost-window coverage flag. Rows are written only from a named verb's response after a stated lateness bound; a restatement is a new row; no comparison joins across versions; a sample re-derivation inside the 30-day snapshot window reports drift as a fault. It needs a numbered Decision amending Decision 199 cl.1, filed with CD.47 | re-asked 2026-11-12 | rec-4438, rec-5077 notes |
| R3 | Friction G1: absorb the tool_error into the gate label, and report the absorbed count | 2026-11-12 | rec-5097 |
| R4 | This report is a dated snapshot; lasting rulings live in recs | 2026-11-11 | this section |
| R5 | rec-4028 (LiteLLM producer) is the Agents in GitHub workspace's; this workspace reviews its reasoning mapping | 2026-11-11 | rec-4028 note |
| S1 | Integer verb versions beside the digest | 2026-11-11 | rec-5080 |
| S2 | As a technical dependency, the content reader deploy is needed only for forensic deliberation reads; the operator's gate is unchanged | 2026-11-11 | rec-4696 note |
| S3 | Producer `input_key` at the next parser bump | 2026-11-11 | rec-5079 |
| S4 | The gate's named list is the operator's 2026-11-10 list | 2026-11-11 | rec-4606 note |
| S5 | The budget is kept in physical units (tokens, GB-seconds, bytes, requests) with no price table; rec-4031 stays post-MVP | 2026-11-11, corrected 2026-11-12 | rec-4031 note |
| Q9, Q11 | T1.13 ownership; what "one filer" covers | asked 2026-11-12 | pending (section 16) |

---

## 18. Known gaps

- The W1 VPs that need git history at old SHAs or a network fetch were skipped; three long simulations did not
  finish, and deliberation VP 3 cannot run in the pinned litellm and mcp pair (section 12).
- G1 and G2 were re-read against the live SQL, not re-probed with vectors (rec-5097 adds them).
- Which hooks rec-4959 affects, and the 17 friction vectors' exact coverage, were not re-derived.
- Whether the post-merge `deterministic_auto_close` venue can close a telemetry-routed acceptance without a verdict
  record was not traced through closure_gate.py.
- Production volumes beyond one window read on 2026-11-09, and the production `checkout_base_sha` null rate, were
  not measured; rec-4426's claim is taken from the rec.
- W2's review-load envelope (about 60 reviews a day) and the ladder's seed values were not re-grounded.
- How each rec_episode caller would carry a finding fingerprint was not assessed (section 7).
- The pilot fixture's edge set was not compared with the staged edges.
- The W3 design's pinned inputs held outside this repository were not carried.
- Overlaps with other workspaces are named from rec text, the shared coordinator log and the operator's
  description only; ownership marked asked is not settled.
