# REPORT: W1 component 8 - allow-list transport (what telemetry metadata may cross from the data plane to the control plane, and how)

Plan: docs/plans/PLAN-w1-allow-list-transport.yaml. Fixture item: pwi-allow-list-transport (1 of 12 cap).
REPORT-ONLY (Decision 86 cl.2): nothing here is built, filed, ratified or flipped. Every verb, table,
contract change and transport named below is a description of a future build. rec-4141 is named by
Decision 209 clause 5(i) as the carrier of this question; it was not read for content, updated or closed
(Decision 67).

## 0. Verdict

Decision 209 settles the shape of the boundary and leaves its content open. Clause 2(a) keeps tenant data and
agent transcripts in the customer-owned data plane; clause 2(b) says what crosses is "an explicit allow-list
of metadata fields, never a deny-list: free-text telemetry (titles, paths, error messages) can leak tenant
data"; clause 5(i) leaves the concrete allow-list and the transport (the control plane pulls through a
customer-granted role, or the data plane pushes) to rec-4141. This report measures what that list would be
choosing from, stages a deterministic egress check, and parks every choice, because every one of them is a
security or IAM fork (the charter's always-ask list).

Measured, not argued:

- The four telemetry Class A contracts declare 140 columns. 62 of them hold caller-controlled strings: 30
  free text, 15 open-vocabulary strings (model, producer, provider, workflow, agent_type and the like, with
  examples but no accepted_values), 15 closed enums and two content hashes (VP 2). The 15 closed enums
  are closed on paper only: all 15 accepted_values blocks are `enforced: false`, so at the write boundary
  an enum column is a free string (VP 1).
- A deny-list fails as Decision 209 predicts. A deliberately generous deny-list of the 12 columns that name
  titles, labels, paths, URLs, messages, reasons or payloads leaves 65 of 77 non-tenancy string columns
  leaking a seeded canary token: 18 free-text, 15 open-vocabulary, 15 closed-enum, 15 derived-id and 2
  content-hash columns (VP 5).
- Derived ids are not opaque to a party that holds the tenancy ids. Every entity key and event_id is
  sha256 over (domain tag, tenant_id, project_id, ref) with no secret, and its first 48 bits are the event
  time in milliseconds (envelope identity spec). Given tenant_id, project_id and one event_id, the time
  prefix decodes exactly and a 100,000-candidate ref space ("pr-N#0/annotate") is searched in about two
  seconds to the one ref that produced the id (VP 6). A control plane needs tenant_id and project_id to
  route anything, so any derived id that crosses confirms guessable refs.
- Nothing reads a plane. The pilot schema has a `planes` field with data_plane and control_plane; all
  seven open W1 items declare `[data_plane]` only, and no file under src/ or scripts/ other than the pilot
  model names control_plane (VP 1, VP 3). This component is the first to declare both planes.

Staged, not decided: one deterministic egress, assembled mechanically from a per-column allow-list over the
133-column inventory. It emits one row per (tenant_id, project_id, UTC day of session_started_at, guarded
closed-enum values) with a row count, finite sums of allow-listed numbers, counts of true booleans and a
withheld count per guarded column; it refuses the batch on an unclassified source column, a malformed or
NULL tenancy id, a NULL day basis, or an allow-list entry whose class may not cross. On the canary probe it
leaks 0 of 77 string columns and counts 300 of 300 injected out-of-vocabulary enum values as withheld (VP 5).
It passes 22/22 vectors, ten by refusing (VP 4), and each of 17 hand-run mutants fails at least one named
vector (section 2.5). The staged membership crosses 48 of 140 columns: 15 guarded enums, 31 numbers and 2
booleans; tenant_id and project_id are the key and session_started_at only as a UTC day.

What is settled is narrow and rests on Decisions: the allow-list shape and where it runs (Decision 209
clause 2(a)-(b)), and that the egress is a named reader verb over derived-at-read state, not caller SQL
(Decision 199 clause 1; Decision 84 I-3, whose local-adapter carrier in a customer plane is T4.23), plus the
measured facts above. The membership (k1), identifier
treatment (k2), transport (k3) and grain (k4) are always-ask (security or IAM) and are parked with a
recommendation, never decided. The allow-list's home (k5) and the fixture's edge home (k6) weigh credible
alternatives with no admissible precedent and are parked as asked.

## 1. Evidence (each row re-derivable; VP step in brackets)

| id | fact | anchor |
|---|---|---|
| e1 | The pilot `Plane` literal is data_plane, control_plane; no file under src/ or scripts/ other than the pilot model names control_plane, so nothing reads or enforces a plane today [VP 1] | scripts/checks/roadmap/_work_item_pilot_model.py:50, :181 |
| e2 | The four telemetry contracts carry 15 accepted_values blocks and 0 of them are enforced [VP 1] | docs/contracts/telemetry_*.yaml |
| e3 | The inventory in section 1.1 covers all 140 contract columns (sessions 38, observations 43, agents 32, transcripts 27) with their contract types; the closed_enum class equals the columns with accepted_values and their values; derived_id equals event_id plus every identity KEY_PLANS column; tenancy_id is tenant_id and project_id [VP 2] | section 1.1; src/telemetry/identity.py KEY_PLANS |
| e4 | Class counts: number 41, free_text 30, derived_id 15, closed_enum 15, open_vocab 15, timestamp 12, tenancy_id 8, boolean 2, content_hash 2 [VP 2] | section 1.1 |
| e5 | All seven open W1 items (#1384, #1390, #1394, #1395, #1396, #1397, #1398) declare planes [data_plane] [VP 3] | the siblings block below (pinned heads) |
| e6 | A 12-column deny-list leaks canaries through 65 of 77 non-tenancy string columns; the staged allow-list leaks through 0 [VP 5] | section 2.6 |
| e7 | Identity hash: sha256 over [domain tag, tenant_id, project_id, ref], no secret; the id's 48-bit prefix is the event time in ms, decodable by `decode_time_prefix` [VP 6] | docs/contracts/telemetry-event-envelope.yaml:316; src/telemetry/identity.py |
| e8 | Given tenant_id, project_id and one event_id, a 100,000-candidate ref space yields exactly the producing ref [VP 6] | section 1.3 |
| e9 | Decision 209 clause 1 names "fleet telemetry dashboards over allow-listed Decision 199 metadata" as paid capability; no such dashboard, consumer or exporter exists in this repository (inferred from e1 and a repository grep for egress code under src/telemetry) | docs/DECISIONS.md, Decision 209 |
| e10 | Decision 73 halt check: the reader's named verb ci_rca_open returned [] at 2026-10-03T16:14:08Z | Step 0 |

Sibling heads read by VP 3 (pinned by sha, so a later push to a sibling branch does not change the reading):

```yaml
siblings:
  - {pr: 1384, item: pwi-capture-producer-wiring, head: 1da1cc97f3522e5551ab32bcb41e3bb5b93cf685}  # pragma: allowlist secret
  - {pr: 1390, item: pwi-telemetry-reader-verbs, head: ab7aad7a36819a10069628577a55b2160cf8bd81}  # pragma: allowlist secret
  - {pr: 1394, item: pwi-friction-classifier, head: c6a47c5df5c1e50c7ab77730dadbe4456965772d}  # pragma: allowlist secret
  - {pr: 1395, item: pwi-deliberation-capture, head: 63b1a50a6926561aac5fb5a9c651c6faba581dc5}  # pragma: allowlist secret
  - {pr: 1396, item: pwi-rec-filing-dedupe, head: 51958a0ae48a700963fc5b1351fed00e6a1decec}  # pragma: allowlist secret
  - {pr: 1397, item: pwi-back-validation, head: 75ad2896d79d7d83f91ddcb619a183972a716351}  # pragma: allowlist secret
  - {pr: 1398, item: pwi-maturity-ladder-controller, head: 9ccd701d15e9009e17275cc6f4e4e3d4ed8fcf83}  # pragma: allowlist secret
```

### 1.1 Field inventory (from the four Class A contracts; VP 2 re-derives the mechanical classes)

Re-derived after merging main at 3ee985e9: the telemetry write-conformance slice (#1405) added six
observation columns (output_capture, output_bytes, output_lines, output_sha256, reasoning_visibility,
reasoning_tokens) and one transcript column (content_omitted_reason), and widened transcripts' purpose
vocabulary with tool_output. The staged structural guard would have refused every batch until those columns
were classified, which is the designed behavior; they are classified here, and the four enums and three
numbers among them join the staged allow-list under k1 (a)'s rule. output_sha256 is a content hash.

Each column is `[DuckDB type, class]`, plus the vocabulary for a closed enum. Classes: `tenancy_id` (writer-minted
ULID, Decision 200), `derived_id` (identity-hash ULID, Decision 199 clause 3), `closed_enum` (has
accepted_values), `open_vocab` (a string the contract describes by examples, no accepted_values), `free_text`
(any other caller-supplied string, including refs, paths, URLs, version strings, JSON and payloads),
`content_hash`, `timestamp`, `number`, `boolean`. The split between open_vocab and free_text is a judgment
(the list is in the plan's VP 2 command); every other class is mechanical. Derived-at-read columns
(`*_total`, `duration_seconds`, `cost_usd`) are listed because the reader verb returns them; the egress reads
the verb, never storage.

```yaml
inventory:
  telemetry_sessions:
    event_id: [VARCHAR, derived_id]
    event_kind: [VARCHAR, closed_enum, [open, resume, compact, close, annotate]]
    event_timestamp: [TIMESTAMPTZ, timestamp]
    session_started_at: [TIMESTAMPTZ, timestamp]
    source_ordinal: [BIGINT, number]
    external_ref: [VARCHAR, free_text]
    entity_ref: [VARCHAR, free_text]
    producer: [VARCHAR, open_vocab]
    producer_version: [VARCHAR, free_text]
    parser_version: [BIGINT, number]
    created_timestamp: [TIMESTAMPTZ, timestamp]
    tenant_id: [VARCHAR, tenancy_id]
    project_id: [VARCHAR, tenancy_id]
    session_id: [VARCHAR, derived_id]
    parent_session_id: [VARCHAR, derived_id]
    workflow: [VARCHAR, open_vocab]
    outcome: [VARCHAR, closed_enum, [success, failed, cancelled]]
    process_event_total: [BIGINT, number]
    rework_total: [BIGINT, number]
    exception_total: [BIGINT, number]
    duration_seconds: [BIGINT, number]
    execution_attempt: [BIGINT, number]
    branch: [VARCHAR, free_text]
    rec_ids: ['VARCHAR[]', free_text]
    plan_slug: [VARCHAR, free_text]
    failure_reason: [VARCHAR, free_text]
    failure_phase: [VARCHAR, free_text]
    files_changed: [BIGINT, number]
    lines_added: [BIGINT, number]
    lines_removed: [BIGINT, number]
    steps_total: [BIGINT, number]
    steps_completed_total: [BIGINT, number]
    scope_drift_files: ['VARCHAR[]', free_text]
    pr_url: [VARCHAR, free_text]
    ci_outcome: [VARCHAR, open_vocab]
    model_primary: [VARCHAR, open_vocab]
    coverage_before: [DOUBLE, number]
    coverage_after: [DOUBLE, number]
  telemetry_observations:
    event_id: [VARCHAR, derived_id]
    event_kind: [VARCHAR, closed_enum, [open, close, point]]
    event_timestamp: [TIMESTAMPTZ, timestamp]
    session_started_at: [TIMESTAMPTZ, timestamp]
    source_ordinal: [BIGINT, number]
    external_ref: [VARCHAR, free_text]
    entity_ref: [VARCHAR, free_text]
    producer: [VARCHAR, open_vocab]
    producer_version: [VARCHAR, free_text]
    parser_version: [BIGINT, number]
    created_timestamp: [TIMESTAMPTZ, timestamp]
    tenant_id: [VARCHAR, tenancy_id]
    project_id: [VARCHAR, tenancy_id]
    session_id: [VARCHAR, derived_id]
    parent_observation_id: [VARCHAR, derived_id]
    observation_id: [VARCHAR, derived_id]
    observation_type: [VARCHAR, closed_enum, [phase, step, turn, tool_call, process_event, model_call]]
    name: [VARCHAR, free_text]
    sequence: [BIGINT, number]
    outcome: [VARCHAR, closed_enum, [success, error, blocked, interrupted]]
    severity: [VARCHAR, open_vocab]
    model: [VARCHAR, open_vocab]
    tokens_input: [BIGINT, number]
    tokens_output: [BIGINT, number]
    tokens_cache_read: [BIGINT, number]
    tokens_cache_creation: [BIGINT, number]
    cost_usd_reported: [DOUBLE, number]
    cost_usd: [DOUBLE, number]
    attempt: [BIGINT, number]
    provider: [VARCHAR, open_vocab]
    persona_backend: [VARCHAR, closed_enum, [litellm, claude_cli]]
    billing_shape: [VARCHAR, closed_enum, [metered_marginal, fixed_non_rollover_allowance]]
    acceptance_passed: [BOOLEAN, boolean]
    exit_code: [BIGINT, number]
    time_lost_seconds: [BIGINT, number]
    output_capture: [VARCHAR, closed_enum, [not_persisted, captured, omitted_oversize, unavailable, no_result]]
    output_bytes: [BIGINT, number]
    output_sha256: [VARCHAR, content_hash]
    output_lines: [BIGINT, number]
    reasoning_visibility: [VARCHAR, closed_enum, [full, summarized, omitted, redacted, none]]
    reasoning_tokens: [BIGINT, number]
    rec_id: [VARCHAR, free_text]
    metadata: [VARCHAR, free_text]
  telemetry_agents:
    event_id: [VARCHAR, derived_id]
    event_kind: [VARCHAR, closed_enum, [open, close]]
    event_timestamp: [TIMESTAMPTZ, timestamp]
    session_started_at: [TIMESTAMPTZ, timestamp]
    source_ordinal: [BIGINT, number]
    external_ref: [VARCHAR, free_text]
    entity_ref: [VARCHAR, free_text]
    producer: [VARCHAR, open_vocab]
    producer_version: [VARCHAR, free_text]
    parser_version: [BIGINT, number]
    created_timestamp: [TIMESTAMPTZ, timestamp]
    tenant_id: [VARCHAR, tenancy_id]
    project_id: [VARCHAR, tenancy_id]
    session_id: [VARCHAR, derived_id]
    observation_id: [VARCHAR, derived_id]
    agent_run_id: [VARCHAR, derived_id]
    agent_type: [VARCHAR, open_vocab]
    agent_name: [VARCHAR, free_text]
    model: [VARCHAR, open_vocab]
    provider: [VARCHAR, open_vocab]
    version: [VARCHAR, free_text]
    trigger: [VARCHAR, open_vocab]
    outcome: [VARCHAR, closed_enum, [success, failed, timeout, throttled]]
    tokens_input_total: [BIGINT, number]
    tokens_output_total: [BIGINT, number]
    duration_seconds: [BIGINT, number]
    findings_count: [BIGINT, number]
    recs_created: [BIGINT, number]
    queue_entries_written: [BIGINT, number]
    error: [VARCHAR, free_text]
    lambda_request_id: [VARCHAR, free_text]
    workflow_run_id: [VARCHAR, free_text]
  telemetry_transcripts:
    event_id: [VARCHAR, derived_id]
    event_kind: [VARCHAR, closed_enum, [point]]
    event_timestamp: [TIMESTAMPTZ, timestamp]
    session_started_at: [TIMESTAMPTZ, timestamp]
    source_ordinal: [BIGINT, number]
    external_ref: [VARCHAR, free_text]
    entity_ref: [VARCHAR, free_text]
    producer: [VARCHAR, open_vocab]
    producer_version: [VARCHAR, free_text]
    parser_version: [BIGINT, number]
    created_timestamp: [TIMESTAMPTZ, timestamp]
    tenant_id: [VARCHAR, tenancy_id]
    project_id: [VARCHAR, tenancy_id]
    observation_id: [VARCHAR, derived_id]
    session_id: [VARCHAR, derived_id]
    transcript_id: [VARCHAR, derived_id]
    purpose: [VARCHAR, closed_enum, [prompt, response, thinking, tool_input, tool_result, tool_output, system]]
    origin: [VARCHAR, closed_enum, [human, harness, agent, tool]]
    content: [VARCHAR, free_text]
    content_uri: [VARCHAR, free_text]
    content_omitted_reason: [VARCHAR, closed_enum, [oversize]]
    content_sha256: [VARCHAR, content_hash]
    content_bytes: [BIGINT, number]
    content_truncated: [BOOLEAN, boolean]
    token_count: [BIGINT, number]
    model: [VARCHAR, open_vocab]
    rec_id: [VARCHAR, free_text]
```

### 1.2 What the inventory shows

- **The named classes are the minority of the risk.** Decision 209 names titles, paths and error messages.
  In the inventory those are 12 columns (`name`, `agent_name`, `plan_slug`, `failure_reason`,
  `failure_phase`, `error`, `branch`, `scope_drift_files`, `pr_url`, `content`, `content_uri`, `metadata`).
  The other 18 free-text columns are refs, version strings, rec ids and CI run ids: `external_ref` and
  `entity_ref` are caller-chosen strings on every row of every table (grammar `<source_record_id>#...`), and
  `producer_version`, `version`, `lambda_request_id` and `workflow_run_id` are whatever the producer sends.
- **Open-vocabulary strings are free text in practice.** `model`, `producer`, `provider`, `workflow`,
  `agent_type`, `trigger`, `severity`, `ci_outcome` and `model_primary` are described by examples ("e.g.
  claude_code, litellm, ci_annotate"). A customer's self-hosted model name or a custom producer id is tenant
  data, and nothing bounds them.
- **Closed enums are closed only once enforced.** All 15 accepted_values blocks say `enforced: false` (VP 1),
  so the egress cannot trust a column's class; it has to check every value against the vocabulary itself
  (section 2.3, the `dim` guard).
- **Numbers are the bulk of what a fleet dashboard needs.** 41 number columns cover tokens, cost, durations,
  counts, diff sizes, coverage and captured-output sizes. Ten of them (`source_ordinal` and `parser_version` in all four tables,
  `sequence` and `exit_code` in observations) are ordering keys or categories whose sums mean nothing, so
  the staged membership leaves them out.

### 1.3 Derived ids as a confirmation oracle (VP 6)

The identity spec (docs/contracts/telemetry-event-envelope.yaml:316) derives every key as
`ULID(48-bit time ms + first 80 bits of sha256(len-prefixed [domain tag, tenant_id, project_id, ref]))`.
There is no key the data plane holds back. The time prefix is the event time to the millisecond, and
`src.telemetry.identity.decode_time_prefix` is the sanctioned way to read it. A party that holds tenant_id
and project_id (which a multi-tenant control plane must, to route a batch) and sees one event_id can therefore
test any candidate ref offline. VP 6 derives the event_id of a `telemetry_sessions` annotate row whose
external_ref is `pr-1398#0/annotate`, then searches the 100,000 refs `pr-0#0/annotate` to
`pr-99999#0/annotate` with the same kernel: the time prefix decodes exactly and exactly one candidate, the
true ref, matches. The search takes about two seconds. Refs drawn from a large random space (a Claude Code
session UUID) are not recoverable this way; refs built from PR numbers, run ids, file paths or step titles
are. Which refs future producers use is not fixed (rec-4026's producer is unbuilt), so the safe reading is
that a derived id carries its ref to anyone holding the tenancy ids. This is why k2 recommends that no
derived id crosses.

```yaml
oracle:
  table: telemetry_sessions
  tenant_id: 01ARZ3NDEKTSV4RRFFQ69G5FAV
  project_id: 01BRZ3NDEKTSV4RRFFQ69G5FBW
  event_timestamp: '2026-10-01T10:00:00.123+00:00'
  true_ref: 'pr-1398#0/annotate'
  candidate_template: 'pr-{n}#0/annotate'
  candidates: 100000
```

## 2. Egress design (what this item stages)

### 2.1 Where it runs and what it is

- **Before the boundary, in the data plane.** Clause 2(a) keeps tenant data in the data plane, so a filter
  applied at the control plane's ingest is already too late. Under either transport (k3) the allow-list
  runs where the data lives and only its output is eligible to cross.
- **A named reader verb, not caller SQL.** Decision 199 clause 1 derives state, duration, friction and cost
  at read by a named reader verb; Decision 84 I-3 closes the DuckLake read boundary to named verbs. The
  egress is one more verb (working name `egress_day_aggregates`) whose output schema is the allow-list. The
  SQL in section 2.3 stands in for that verb over the verb outputs of the four tables.
- **An allow-list in the strict sense.** Every crossing column is named (section 2.2). A column that is not
  named never crosses, whatever it contains; a new source column refuses the batch until it is classified,
  so a schema change cannot slip in or be silently ignored.
- **Independent of scrubbing.** CD.40's credential scrub and rec-4029's PII masking (named, not read) are
  pattern deny-lists over content. The egress does not rely on them: a scrubbed column still does not cross
  unless it is named, and none of the free-text columns is.

### 2.2 Allow-list (staged membership: k1 option (a), k4 option (a))

Crossing classes are closed_enum, number and boolean. tenant_id and project_id are the key, guarded as
canonical ULIDs; session_started_at contributes only its UTC calendar day (the partition basis, Decision 199
clause 2). Nothing else crosses: no derived id (k2), no timestamp beyond the day, no open-vocabulary string,
no free text, no content hash. Every column below is named explicitly; VP 4 refuses an entry whose
inventory class is not a crossing class (vector e05).

```yaml
allow_list:
  params_version: 1
  crossing_classes: [closed_enum, number, boolean]
  key: [tenant_id, project_id]
  day_from: session_started_at
  tables:
    telemetry_sessions: [event_kind, outcome, process_event_total, rework_total, exception_total, duration_seconds, execution_attempt, files_changed, lines_added, lines_removed, steps_total, steps_completed_total, coverage_before, coverage_after]
    telemetry_observations: [event_kind, observation_type, outcome, tokens_input, tokens_output, tokens_cache_read, tokens_cache_creation, cost_usd_reported, cost_usd, attempt, persona_backend, billing_shape, acceptance_passed, time_lost_seconds, output_capture, output_bytes, output_lines, reasoning_visibility, reasoning_tokens]
    telemetry_agents: [event_kind, outcome, tokens_input_total, tokens_output_total, duration_seconds, findings_count, recs_created, queue_entries_written]
    telemetry_transcripts: [event_kind, purpose, origin, content_omitted_reason, content_bytes, content_truncated, token_count]
```

### 2.3 The egress (templates and the assembly rule)

The verb's SQL is assembled mechanically from the inventory (1.1), the allow-list (2.2) and the templates
below; the assembly has no other input. For each table: run `structural` (a row exists only for a source
column the inventory does not classify, and `error()` refuses the batch); then run `select`, where `keys` is
the `key` template per key column plus `day` over `day_from`, `dims` is the `dim` template per allow-listed
closed enum, and `measures` is the class's `measure` template per allow-listed column. `{vocab}` is the
column's inventory vocabulary as a quoted list. Before any SQL is built, an allow-list entry whose class is
not in `crossing_classes` raises `not a crossing class: <table>.<column>`.

```yaml
egress:
  structural: >-
    SELECT error('unclassified column: {table}.' || column_name) FROM information_schema.columns
    WHERE table_name = '{table}' AND column_name NOT IN ({classified})
  key: >-
    CASE WHEN regexp_full_match({c}, '[0-7][0-9A-HJKMNP-TV-Z]{25}') THEN {c} ELSE error('malformed tenancy id: {c}') END AS {c}
  day: >-
    CASE WHEN {c} IS NULL THEN error('null day basis') ELSE CAST(timezone('UTC', {c}) AS DATE) END AS day
  dim: >-
    CASE WHEN {c} IN ({vocab}) THEN {c} END AS {c}
  measure:
    closed_enum: >-
      count(*) FILTER (WHERE {c} NOT IN ({vocab})) AS withheld_{c}
    number: >-
      sum(CAST({c} AS DOUBLE)) FILTER (WHERE isfinite(CAST({c} AS DOUBLE))) AS sum_{c},
      count(*) FILTER (WHERE NOT isfinite(CAST({c} AS DOUBLE))) AS withheld_{c}
    boolean: >-
      count(*) FILTER (WHERE {c}) AS true_{c}
  select: >-
    SELECT {keys}, {dims}, count(*) AS n_rows, {measures} FROM {table} GROUP BY ALL
```

Action and guard table:

| input | output |
|---|---|
| closed-enum value in its vocabulary | groups as itself |
| closed-enum value outside its vocabulary (a tenant string in an enum column) | groups as NULL; `withheld_<col>` counts it |
| NULL closed-enum value | groups as NULL; not counted as withheld |
| number, finite | added to `sum_<col>` |
| number, NaN or infinite | left out of the sum; `withheld_<col>` counts it |
| boolean | `true_<col>` counts true; false and NULL are not counted |
| tenant_id or project_id not exactly one canonical upper-case ULID (a full match: a valid id with a suffix, a trailing newline or a first character above 7 is refused), or NULL | the batch is refused (`malformed tenancy id: <col>`) |
| session_started_at NULL | the batch is refused (`null day basis`) |
| source column not in the inventory | the batch is refused (`unclassified column: <table>.<col>`) |
| allow-list entry whose class is not crossing | refused before any SQL is built |

Two design notes. Withholding a bad enum value rather than refusing the batch keeps one drifting producer from
stopping every fleet metric, and the withheld count is half of the failure_signal; refusing is the
alternative inside k1. The aggregate per (tenant, project, day, enum values) is k4's recommended grain: a
row-level egress would need a row identity, which reopens k2.

### 2.4 Vectors (VP 4)

`defaults` fill every row; unspecified columns are NULL. The session time zone is set to Pacific/Auckland so a
day computed in the session zone instead of UTC fails v06. `select` names the output columns compared; v09
also pins the exact output column list.

```yaml
vectors:
  defaults: {tenant_id: 01ARZ3NDEKTSV4RRFFQ69G5FAV, project_id: 01BRZ3NDEKTSV4RRFFQ69G5FBW, session_started_at: '2026-10-01 10:00:00+00'}
  timezone: Pacific/Auckland
  cases:
    - id: v01
      why: two model_call rows of one tenant, project and day fold into one row; token sums and the row count cross
      table: telemetry_observations
      rows: [{observation_type: model_call, event_kind: point, tokens_input: 100}, {observation_type: model_call, event_kind: point, tokens_input: 50}]
      select: [day, observation_type, n_rows, sum_tokens_input, withheld_observation_type]
      expected: [['2026-10-01', model_call, 2, 150.0, 0]]
    - id: v02
      why: an out-of-vocabulary enum value (a tenant string in an enum column) crosses as NULL and is counted, never as text
      table: telemetry_observations
      rows: [{observation_type: 'CNRY acme payroll export', event_kind: point}, {observation_type: tool_call, event_kind: point}]
      select: [observation_type, n_rows, withheld_observation_type]
      expected: [[null, 1, 1], [tool_call, 1, 0]]
    - id: v03
      why: a NULL enum stays NULL and is not counted as withheld
      table: telemetry_observations
      rows: [{observation_type: null, event_kind: point}]
      select: [observation_type, n_rows, withheld_observation_type]
      expected: [[null, 1, 0]]
    - id: v04
      why: NaN and infinity are withheld from the sum and counted
      table: telemetry_observations
      rows: [{event_kind: point, cost_usd: 0.25}, {event_kind: point, cost_usd: .nan}, {event_kind: point, cost_usd: .inf}, {event_kind: point, cost_usd: null}]
      select: [n_rows, sum_cost_usd, withheld_cost_usd]
      expected: [[4, 0.25, 2]]
    - id: v05
      why: a boolean crosses as a count of true; false and NULL are not counted
      table: telemetry_observations
      rows: [{event_kind: point, acceptance_passed: true}, {event_kind: point, acceptance_passed: false}, {event_kind: point, acceptance_passed: null}]
      select: [n_rows, true_acceptance_passed]
      expected: [[3, 1]]
    - id: v06
      why: day is the UTC calendar day of session_started_at whatever the session time zone (23:30 at -02:00 is 01:30 UTC next day; 12:00 UTC is 01:00 next day in Auckland)
      table: telemetry_observations
      rows: [{event_kind: point, session_started_at: '2026-10-01 23:30:00-02'}, {event_kind: point, session_started_at: '2026-10-01 12:00:00+00'}]
      select: [day, n_rows]
      expected: [['2026-10-01', 1], ['2026-10-02', 1]]
    - id: v07
      why: two tenants never share an egress row
      table: telemetry_observations
      rows: [{event_kind: point}, {event_kind: point, tenant_id: 01CRZ3NDEKTSV4RRFFQ69G5FCX}]
      select: [tenant_id, n_rows]
      expected: [[01ARZ3NDEKTSV4RRFFQ69G5FAV, 1], [01CRZ3NDEKTSV4RRFFQ69G5FCX, 1]]
    - id: v08
      why: two projects of one tenant never share an egress row
      table: telemetry_observations
      rows: [{event_kind: point}, {event_kind: point, project_id: 01DRZ3NDEKTSV4RRFFQ69G5FDY}]
      select: [project_id, n_rows]
      expected: [[01BRZ3NDEKTSV4RRFFQ69G5FBW, 1], [01DRZ3NDEKTSV4RRFFQ69G5FDY, 1]]
    - id: v09
      why: the egress columns are exactly the key, the day, the guarded enums and the measures; no free-text, open-vocabulary, id or timestamp column appears
      table: telemetry_observations
      rows: [{event_kind: point, name: CNRY step title, metadata: '{"path": "CNRY/src/app.py"}', external_ref: 'CNRY-rec#0/point', model: CNRY-model, producer: CNRY-producer, observation_id: 01CNRY0000000000000000000A}]
      columns: [tenant_id, project_id, day, event_kind, observation_type, outcome, persona_backend, billing_shape, output_capture, reasoning_visibility, n_rows, withheld_event_kind, withheld_observation_type, withheld_outcome, sum_tokens_input, withheld_tokens_input, sum_tokens_output, withheld_tokens_output, sum_tokens_cache_read, withheld_tokens_cache_read, sum_tokens_cache_creation, withheld_tokens_cache_creation, sum_cost_usd_reported, withheld_cost_usd_reported, sum_cost_usd, withheld_cost_usd, sum_attempt, withheld_attempt, withheld_persona_backend, withheld_billing_shape, true_acceptance_passed, sum_time_lost_seconds, withheld_time_lost_seconds, withheld_output_capture, sum_output_bytes, withheld_output_bytes, sum_output_lines, withheld_output_lines, withheld_reasoning_visibility, sum_reasoning_tokens, withheld_reasoning_tokens]
      select: [event_kind, n_rows]
      expected: [[point, 1]]
    - id: v10
      why: a failed session crosses its outcome and counts; its free-text failure_reason and branch do not
      table: telemetry_sessions
      rows: [{event_kind: close, outcome: failed, failure_reason: CNRY customer db password rejected, branch: CNRY/feature, files_changed: 3}]
      select: [event_kind, outcome, n_rows, sum_files_changed]
      expected: [[close, failed, 1, 3.0]]
    - id: v11
      why: transcript metadata crosses as counts and sizes; content, content_uri and content_sha256 do not
      table: telemetry_transcripts
      rows: [{event_kind: point, purpose: prompt, origin: human, content: CNRY prompt text, content_uri: CNRY/uri, content_sha256: cnry0000, content_bytes: 120, content_truncated: false}]
      select: [purpose, origin, n_rows, sum_content_bytes, true_content_truncated]
      expected: [[prompt, human, 1, 120.0, 0]]
    - id: v12
      why: an agent run's error text does not cross; its outcome does
      table: telemetry_agents
      rows: [{event_kind: close, outcome: failed, error: CNRY stack trace, agent_name: CNRY-bot, findings_count: 2}]
      select: [outcome, n_rows, sum_findings_count]
      expected: [[failed, 1, 2.0]]
    - id: e01
      why: a tenant_id that is not a canonical ULID refuses the batch
      table: telemetry_observations
      rows: [{event_kind: point, tenant_id: acme-corp}]
      expected: error
      raises: 'malformed tenancy id: tenant_id'
    - id: e02
      why: a NULL project_id refuses the batch
      table: telemetry_observations
      rows: [{event_kind: point, project_id: null}]
      expected: error
      raises: 'malformed tenancy id: project_id'
    - id: e03
      why: a source column the inventory does not classify refuses the batch, so a new column is classified before anything runs
      table: telemetry_observations
      extra_columns: [[note, VARCHAR]]
      rows: [{event_kind: point}]
      expected: error
      raises: 'unclassified column: telemetry_observations.note'
    - id: e04
      why: a NULL partition basis refuses the batch
      table: telemetry_sessions
      rows: [{event_kind: open, session_started_at: null}]
      expected: error
      raises: 'null day basis'
    - id: e05
      why: an allow-list naming a column whose class is not a crossing class is refused before any SQL is built
      table: telemetry_observations
      allow_list_patch: {telemetry_observations: [event_kind, name]}
      rows: [{event_kind: point}]
      expected: error
      raises: 'not a crossing class: telemetry_observations.name'
    - id: e06
      why: a lower-case tenant_id is not canonical and refuses the batch (the identity hash encodes the canonical upper-case form)
      table: telemetry_observations
      rows: [{event_kind: point, tenant_id: 01arz3ndektsv4rrffq69g5fav}]
      expected: error
      raises: 'malformed tenancy id: tenant_id'
    - id: e07
      why: a valid tenant_id followed by text refuses the batch; the guard is a full match, so tenant text cannot ride on the only string key that crosses
      table: telemetry_observations
      rows: [{event_kind: point, tenant_id: 01ARZ3NDEKTSV4RRFFQ69G5FAV CNRY payroll}]
      expected: error
      raises: 'malformed tenancy id: tenant_id'
    - id: e08
      why: a valid project_id with a trailing newline refuses the batch
      table: telemetry_observations
      rows: [{event_kind: point, project_id: "01BRZ3NDEKTSV4RRFFQ69G5FBW\n"}]
      expected: error
      raises: 'malformed tenancy id: project_id'
    - id: e09
      why: a 26-character Crockford string whose first character is above 7 overflows 128 bits, is not a ULID, and refuses the batch
      table: telemetry_observations
      rows: [{event_kind: point, tenant_id: ZZZZZZZZZZZZZZZZZZZZZZZZZZ}]
      expected: error
      raises: 'malformed tenancy id: tenant_id'
    - id: e10
      why: a valid tenant_id with a trailing space refuses the batch; the guard never trims, so padding cannot split one tenant into two fleet groups
      table: telemetry_observations
      rows: [{event_kind: point, tenant_id: '01ARZ3NDEKTSV4RRFFQ69G5FAV '}]
      expected: error
      raises: 'malformed tenancy id: tenant_id'
```

### 2.5 Mutants (hand-run once, reported, not a VP step)

Each mutant is one textual change to the templates, the allow-list or the assembly rule; each fails the named
vectors and no mutant survives.

| id | mutant | failed by |
|---|---|---|
| m01 | `dim` passes the raw value (no vocabulary guard) | v02 |
| m02 | enum `withheld_` counts NULLs instead of out-of-vocabulary values | v02, v03 |
| m03 | `sum_` keeps NaN and infinity | v04 |
| m04 | tenancy pattern accepts any non-empty single line | e01, e06, e07, e09, e10 |
| m05 | tenancy guard written as `WHEN NOT match THEN error ELSE col` (a NULL passes) | e02 |
| m06 | tenancy pattern case-insensitive | e06 |
| m07 | structural guard made vacuous | e03 |
| m08 | day cast in the session time zone, not UTC | v06 |
| m09 | NULL day basis passes | e04 |
| m10 | crossing-class check removed from the assembly | e05 |
| m11 | project_id dropped from the key | v08, v09, e02, e08 |
| m12 | boolean counts NULL as true | v05 |
| m13 | number `withheld_` counts NULLs | v04 |
| m14 | tenant_id dropped from the key | v07, v09, e01, e06, e07, e09, e10 |
| m15 | tenancy guard a substring match (`regexp_matches`), so a valid id with a suffix crosses verbatim (verification r1 x4) | e07, e08, e10 |
| m16 | tenancy pattern drops the first-character bound (`[0-9A-HJKMNP-TV-Z]{26}`) | e09 |
| m17 | tenancy guard matches `trim(col)` but emits `col` verbatim (verification r2 x7) | e10 |

### 2.6 Canary probe (VP 5)

The probe builds all four tables from the inventory with 40 rows each: two tenants, one project, every string
column (free text, open vocabulary, derived id, content hash, list columns as one-element lists) holding a
token `CNRY:<table>:<column>:<row>`, every closed enum holding an in-vocabulary value on even rows and a
canary on odd rows, every DOUBLE holding NaN on every tenth row. It then runs two projections:

- **Deny-list** (`SELECT * EXCLUDE (deny_list)`): the 12 columns whose contract text names a title, label,
  path, URL, message, reason, JSON or payload. 65 of 77 non-tenancy string columns leak: 18 free text, 15
  open vocabulary, 15 closed enum, 15 derived id, 2 content hash. Only the 12 denied columns do not.
- **Staged allow-list** (sections 2.2-2.3): 0 columns leak, the output is 55 rows, and the withheld counters
  total 300, exactly the 300 out-of-vocabulary enum values injected (20 odd rows x 15 enum columns).

A derived id counted as a leak here is a ULID-shaped string, not text; section 1.3 is why it is still one.
The probe seeds tenant_id and project_id only with valid ids, so it cannot see the key columns: their text
barrier is the full-match guard, pinned by e01, e02 and e06-e10 (verification r1 showed a substring match
survives the canary). The production canary has the same blind spot; criterion c1 carries those vectors.

```yaml
probe:
  rows_per_table: 40
  tenants: [01ARZ3NDEKTSV4RRFFQ69G5FAV, 01CRZ3NDEKTSV4RRFFQ69G5FCX]
  project: 01BRZ3NDEKTSV4RRFFQ69G5FBW
  deny_list: [name, agent_name, plan_slug, failure_reason, failure_phase, error, branch, scope_drift_files, pr_url, content, content_uri, metadata]
```

### 2.7 Transport (k3; always-ask, IAM: compared, not measured, not decided)

| property | (a) data plane pushes | (b) control plane pulls through a customer-granted role |
|---|---|---|
| works with the local adapter (free tier: no cloud account, Decision 184 clause 2, T4.23) | yes: an outbound HTTPS call from the customer's scheduler | no: there is no role to assume; a free-tier user would need a cloud account to be measured |
| what enforces the allow-list | the egress verb, run by the customer's code before sending | the egress verb writes a view or prefix, and an IAM policy must scope the role to it; a mis-scoped role reads raw tables, so the allow-list becomes an IAM property |
| inbound trust the customer grants | none; the control plane exposes a tenant-scoped ingest endpoint and the customer holds a revocable credential | a cross-account trust policy with an external id (Decision 209 clause 2(d) customer-revocable) |
| revocation | stop the schedule or revoke the ingest credential | delete the role or the trust |
| audit of what left | the data plane can log each batch before sending (the customer sees exactly what crossed) | read logs (CloudTrail) of what the role fetched |
| who schedules and pays the egress cost | the customer | the control plane (cost and egress budget belong to W1 component 10) |

The recommendation is (a): it is the only option that runs on the free tier's local adapter, and it keeps
the allow-list a property of code the customer runs, where Decision 209 clause 2(b) puts it, rather than of
an IAM policy. Both rows touch IAM and the control plane's attack surface, so this is parked always-ask.

## 3. Settled / contested / risk / open

### Settled (fixture s1-s3)

- **s1.** Only an explicit allow-list of metadata crosses; transcripts and tenant data never do; the filter
  runs in the data plane before anything crosses. Precedent: Decision 209 clause 2(a)-(b) (decision).
- **s2.** The egress is a named reader verb over derived-at-read state, never caller SQL. Precedent: Decision
  199 clause 1 and Decision 84 I-3 (decision).
- **s3.** Measured facts only: 62 of 140 columns hold caller-controlled strings; 0 of 15 vocabularies are
  enforced; a 12-column deny-list leaks 65 of 77 string columns; the staged allow-list leaks 0 and counts
  300 of 300 withheld values; derived ids plus tenancy ids confirm guessed refs (VP 1, 2, 5, 6).

### Contested (k1-k3 in the fixture; k4-k6 report-only, the fixture's contested list is capped at 3)

- **k1 (always-ask: security). Membership.** (a) Tenancy ids as the key, guarded closed enums, finite numbers
  and booleans, at UTC-day grain: the staged list, 48 of 140 columns. Open-vocabulary columns cross only
  after their contract enforces a vocabulary, at which point they are closed enums. (b) Also open-vocabulary
  strings matched against a vocabulary the control plane publishes (model names, producers). (c) The
  Decision 199 envelope as written ("metadata" read as every envelope field). Recommended (a): VP 5 shows
  (c) carries every ref, and (b) moves the vocabulary's authority to the party the boundary protects
  against. Inside (a): withhold an out-of-vocabulary value (staged) or refuse the batch.
- **k2 (always-ask: security). Identifiers.** (a) No derived id crosses; tenant_id and project_id only.
  (b) Re-key every crossing id with an HMAC under a key the data plane holds, so ids can be joined across
  batches without being testable. (c) Ids as written. Recommended (a) while the grain is aggregates (k4 (a)
  needs no row identity); (b) if a row-level grain is chosen. (c) is ruled out by VP 6.
- **k3 (always-ask: IAM). Transport.** (a) The data plane pushes. (b) The control plane pulls through a
  customer-granted role. (c) Both, chosen by adapter. Recommended (a) (section 2.7).
- **k4 (always-ask: security; report-only). Grain and time resolution.** (a) Day aggregates per (tenant,
  project, day, enum values) (staged). (b) Guarded rows with the day only. (c) Guarded rows with
  millisecond timestamps. Recommended (a). Inside (a): a minimum-cell rule (suppress a row with fewer than
  k sessions) is not staged; a one-session project-day still reveals that session's outcome and cost.
- **k5 (asked; report-only). Where the allow-list lives.** (a) A per-field `egress` key on each telemetry
  Class A contract field, with a check that every field carries one (AGENTS.md: collocate a definition with
  its enforcement). (b) One new Class C contract listing the crossing columns. (c) A code constant beside the
  verb. Recommended (a). No Decision or contract places it; AGENTS.md's collocation rule is guidance, and (b)
  is a credible alternative because the list spans four contracts.
- **k6 (asked; report-only). The fixture item's edge home.** (a) depends_on T2.36 only, no part_of (carried
  provisionally): no tier item owns plane egress, and the control plane itself lives outside this repository
  (Decision 209 clause 3). (b) part_of T2.36 (the telemetry rebuild). (c) part_of T4.24 (packaging, which
  carries the free tier's local adapter). Recommended (a) until W2 or the operator names an owner.

### Risks

- **R1 numbers are a channel.** A writer inside the data plane can encode anything in a number: a customer, or a
  prompt-injected or compromised agent writing lines_added, files_changed or a duration. Day aggregation bounds
  the bandwidth, but a one-session project-day carries the raw value (R2). The egress defends against
  accidental text leakage only, not against a deliberate in-plane encoder. Whether that encoder is in scope is
  part of k1 for the operator; no Decision sets the egress threat model (plan-critique r1 A1).
- **R2 small cells.** A day aggregate over one session is that session (k4 inside (a)).
- **R3 silent vocabulary drift.** With enforced: false, a producer that starts emitting a new enum value is
  visible only as a rising withheld count; the failure_signal reads it.
- **R4 wrong numbers cross.** The egress trusts the reader verbs' derivations (cost, duration, totals). A wrong
  derivation is a correctness defect in another component, not a leak.
- **R5 days still reveal activity.** The day key shows which days a project worked.
- **R6 the list is public.** This repository is public (Decision 101), so the allow-list will be too. It names
  columns, not data; that is transparency, not exposure.

### Open questions (q1-q3 in the fixture)

- **q1.** Who owns each closed vocabulary and flips `enforced` to true? Until then open-vocabulary columns
  such as `model` and `producer` cannot cross under k1 (a).
- **q2.** No consumer exists. Which aggregates does the first paid fleet dashboard need (Decision 209 clause
  1)? Membership should follow a named consumer, not the inventory.
- **q3.** Where the canary row, the egress log and the withheld counts live (data plane); shares rec-filing
  q4 and back-validation q4.

### Named for owners, not filed (Decision 67)

- **O1** All 12 telemetry accepted_values blocks are `enforced: false`. The egress guards values itself,
  but the contracts' closed enums are open at write (telemetry contract owner).
- **O2** The identity hash takes no secret, so a derived id plus the tenancy ids is a confirmation oracle
  for its ref. That is harmless inside one data plane and matters at any boundary (telemetry contract owner;
  k2 depends on it).

## 4. Consideration register (as authored in the fixture)

- why: Decision 209 clause 2(b) wants an allow-list and clause 5(i) leaves it open; 62 of 140 columns hold
  caller strings, 0 of 15 vocabularies are enforced, and nothing reads a plane.
- how: a named data-plane verb assembled from the operator-approved per-column allow-list emits only what the
  list names (staged: k1 (a), k4 (a) day aggregates) and refuses on drift or a bad tenancy id; legs k1-k3.
- planes: data_plane, control_plane (the first W1 item to declare both).
- maturity (thresholds are provisional seeds, no measurement behind them; plan fork line): read_all (the
  operator diffs every batch against the allow-list) to sampled after 30 clean
  batches since the last leak, to spot_check after 60 clean sampled batches, to anomaly_triggered after 90
  consecutive days with canary_leaks 0 and no unclassified column. The return leg and version rule are the
  maturity-ladder controller's k1 and k2 (#1398).
- failure_signal: canary_leaks, the tokens of a seeded per-run canary row found in the egress batch (must be
  0), with the withheld share per column beside it.
- verification: c1 vectors, c2 canary probe, c3 operator review of every allow-list change.
- rollback: stop the egress schedule; nothing further crosses; the control plane holds only aggregates
  already sent, deletable per tenant.

## 5. Boundary notes for W2 synthesis

- **Every other W1 component is data-plane only.** Their failure_signal metrics (unfinalized_session_share,
  verb_rederivation_mismatch_rate, unmapped_failure_share, deliberation_drift_share, rejected_share,
  false_proof_rate, est_escaped_share) are numeric shares and are natural first members of a fleet view,
  but several are keyed "per producer", "per detector" or "per classifier_version": open-vocabulary keys
  that cannot cross under k1 (a) until their vocabularies are closed and enforced.
- **Deliberation capture (#1395) is data-plane only by Decision 209.** Reasoning content is transcript-class
  and never crosses; only its derived-at-read metrics could, through this egress.
- **Cost and egress budget (component 10)** owns what the egress costs and who pays under each transport.
- **Maturity ladder (#1398)** governs this item's rungs; its k1 (return leg) and k2 (version restart) apply,
  with params_version here being the allow-list version.
- **Reader verbs (#1390)** own the verb surface this egress joins.

## 6. Staged candidate decision text (for W3; not filed)

> **Amends Decision 209 clause 5(i) (staged; requires k1-k4 answered by the operator):** The metadata
> allow-list is a per-column list of crossing columns over the telemetry Class A contracts, applied in the
> data plane by a named reader verb before anything crosses. Only closed enums (value-guarded against their
> vocabulary), finite numbers and booleans cross, aggregated per tenant_id, project_id and UTC day of
> session_started_at; no derived id, timestamp, open-vocabulary string, free text or content hash crosses. An
> unclassified source column or a malformed tenancy id refuses the batch. Transport: the data plane pushes
> to a tenant-scoped ingest endpoint under a customer-revocable credential.
