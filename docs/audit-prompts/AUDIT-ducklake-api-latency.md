# AUDIT PROMPT: Latency of the agent-facing DuckLake verb path -- where the time goes, what would cut it, and how the design process treated latency

You are executing this prompt in a fresh session with no other context. It is self-contained. Do not
ask clarifying questions; where something is unclear, take the named degraded path and record it.

## 1. TASK

Audit the latency of the path an agent session uses to read and write operational data: session-side
Python clients, the session egress proxy, one API Gateway REST API, four AWS Lambda functions (ops
reader, ops writer, telemetry reader, telemetry writer), the DuckDB/DuckLake runtime inside them, and
the Postgres catalog plus S3 Parquet data behind that. The operator had to raise the API Gateway
integration timeout quota from the default 29 s to 120 s to make this path work, and suspects that
points to inefficient design. That is the operator's hypothesis, not a finding. The operator wants
three things: (a) a measured account of where the time goes; (b) an inventory of what would reduce
it, each item with its estimated saving, effort, monthly cost and risk, so the operator can weigh
cost against benefit afterwards; and (c) an account of how and why the system came to be built this
way and, if that account shows a gap in the design process, what would close it.

There is NO target latency in this audit. You quantify; the operator decides. Do not optimise toward
a threshold and do not recommend replacing a component in order to reach a number.

Questions (full text in Section 7): Q1 where does the time go; Q2 is the 120 s timeout inherent or
avoidable; Q3 what would cut latency and do the remedies already owned cover it; Q4 does the
architecture fit the workload against industry practice, including the engine and the Lambda
language; Q5 how and why was it built this way; Q6 does present governance catch what Q5 found; Q7
questions the operator did not ask.

Deliverables, both under the repository-root `audits/` directory:
- `audits/ducklake-api-latency-<sha>.yaml` -- the audit record (Section 14).
- `audits/ducklake-api-latency-<sha>.md` -- a prose report, at most 1500 words (Section 14).

Write boundary: the ONLY files you create or modify in the repository tree are those two
deliverables. Regenerating gitignored local caches per Section 5 is expected and does not breach the
boundary (never commit them). Scratch files outside the repository tree are allowed. You may call the
live system only as Section 11 permits. You draft; the human disposes.

## 2. CANDIDATE OBSERVATIONS vs VERDICTS -- THE EPISTEMIC CONTRACT

This prompt hands you FACTS (Section 10) and CANDIDATE hypotheses (below). It hands you no verdicts.
ASSUME NO CANDIDATE IS A REAL DEFECT UNTIL YOU TRACE IT. A run that merely confirms the candidates
below has failed. Candidates may be deliberate trade-offs, already owned, false, or irrelevant to the
latency agents experience. C19 and C20 state the opposite reading on purpose; weigh them as
seriously as the rest.

Two different things are recorded, and they are not the same:
- A FINDING is a defect: something in the system or its governance that is wrong against the North
  Star (Section 6) and that an owner would be expected to fix whatever the operator's appetite for
  spend. Findings carry severity.
- An OPPORTUNITY is a change that would reduce latency. It lives in `optimisation_inventory`
  (Section 14) and carries saving, effort, cost and risk, never severity. An opportunity may point
  at a finding or stand alone: a trade-off that costs money or effort to buy speed (for example
  provisioned capacity) is an opportunity with no finding.

Adjudicate every candidate C1-C20 in `candidate_adjudication` (Section 14), one row each, with one
outcome:
- `finding`: the candidate holds and is a defect. Name the finding id(s). The finding's
  `roadmap_crossref.classification` is then one of:
  - `novel`: no roadmap item, Decision or open rec owns it.
  - `planned-insufficient`: an owner exists, and in your judgment its stated acceptance or criterion
    would not remove the defect.
  - `planned-unbuilt`: an owner exists with an adequate remedy, and the remedy is neither built nor
    in progress.
- `rejected`: name the reason in a `rejected_candidates` row. Reasons: the candidate is false; it
  holds but is not a defect (name the compensating control or the trade-off); or it is owned,
  the remedy is adequate, and it is built or in progress (name the owner).
- `undetermined`: you could not confirm or refute it with the data available. Say what data would
  settle it. No finding and no rejection row.

"In progress" is pinned: a roadmap item or criterion whose status is `in_progress` or `complete`; or
a rec whose id appears in a plan file under `docs/plans/` (grep the id); or a rec whose own text
records a merged implementing pull request. An open rec with none of these is not in progress.

A compound candidate may be split: give the outcome that fits its main claim and describe the rest
in `note`. One candidate may feed several findings, and one finding may list several candidates.
Findings and opportunities that come from no candidate are expected.

Candidates (each is a hypothesis; the facts behind them are in Section 10):
- C1. The first statement on a freshly opened catalog connection costs far more than the connection
  itself, and is the largest single contributor to calls that exceed 29 s.
- C2. That cost is not tied to the connection: a reused connection also pays tens of seconds the
  first time a container touches a table's data files.
- C3. The number of data files a read must open (about 930 remain in the ops catalog after each merge
  pass) drives C1 and C2, and the partition layout or merge policy keeps that number from falling.
- C4. Work that could run once per execution environment during Lambda init (imports, extension
  loads, credential and DSN fetch, ATTACH) runs inside the first request instead.
- C5. `SET threads=1` and the 1024 MB reader memory size lengthen cold reads.
- C6. Fresh connections are frequent because traffic is bursty with idle gaps over five minutes, and
  nothing keeps an execution environment warm.
- C7. The ops reader has no read deadline, so a slow read runs into the 120 s Lambda timeout and the
  execution environment is reset.
- C8. Each client call pays fixed overhead that does no work for the caller: an SSM lookup of the
  route URL, a new credential session and a new HTTPS connection.
- C9. Client retry policy (3 attempts, 180 s each, on 502/503/504) multiplies the wall time of a slow
  or failed read and can repeat a deterministic failure.
- C10. Some callers fetch more than they use or issue single-row calls in a loop where a batch or
  aggregate verb exists.
- C11. On the reader, writer and telemetry reader, latency cannot be attributed to a verb, a caller
  or a transport from the data recorded today; on all four, not to a phase.
- C12. Latency budgets that exist on paper (2,000 ms warm, 3 s cold attach) are not enforced by any
  gate or alarm, and their reversal conditions depend on someone noticing.
- C13. The 120 s values (Lambda timeout, then the API integration timeout) were inherited or fitted
  to observed behaviour rather than derived from a requirement.
- C14. The planning, critique and review method does not ask for a latency assessment at design
  time.
- C15. Writes on a reused connection take over a second at the median and several seconds at p95.
- C16. About a dozen open recommendations each own a slice of this problem, and no item owns the
  end-to-end latency of a route.
- C17. The decided move of the catalog from Neon to RDS PostgreSQL will not change the dominant
  costs.
- C18. Session flows (preflight, MCP server start, a telemetry capture pass) make more round trips
  than their purpose needs, including `describe` calls whose answer rarely changes.
- C19. The path is already adequate for most calls: the reused-connection median is well under a
  second on the read routes, slow calls are a small tail, and removing the tail would cost more than
  it returns.
- C20. Raising the timeout to 120 s was a deliberate, evidence-based trade-off made under time
  pressure to restore agent access, with the follow-up work recorded as recs; the process worked as
  intended.

## 3. READ FIRST -- DISAMBIGUATION TRAPS

- "Cold start" names five different things here. Keep them apart in every number you report:
  (1) Lambda init (the REPORT line's `Init Duration`, about 0.2 s);
  (2) connect: opening the DuckDB connection, loading three extensions and ATTACHing the catalog
      (`connect_ms`, about 11-20 s on a new environment);
  (3) first statement on a fresh connection (`elapsed_ms - connect_ms`);
  (4) first touch of a table's data files on a connection that is already open (C2);
  (5) Neon scale-to-zero wake of the Postgres catalog.
- Paths are mutually exclusive (definitions in Section 4): `new_env`, `fresh_connection`,
  `reused_connection`, `connectionless`. A new environment also opens a connection, but it is
  counted ONLY as `new_env`.
- "The 120 s" is two separate limits with different histories: the Lambda function timeout on all
  four functions (120 s since each was created, the first in June 2026) and the API Gateway
  integration timeout quota (raised from 29 s to 120 s in November 2026). The quota raise is what the
  operator is asking about; the Lambda timeout is older.
- "The 110 s read deadline" exists only on the telemetry reader. The ops reader has none. A planning
  note calls the ops reader's observed p99 of 109.7 s "its read deadline"; check that against the
  code before repeating it.
- Two transports reach the same four functions: the REST API (`OTL_OPS_VERBS_TRANSPORT=execute_api`,
  agent sessions) and AWS_IAM Lambda Function URLs (CI and anything else). The REST API is the newer
  path and is NOT the subject by itself: the latency lives in the functions both transports share.
- Two log line formats exist. The reader, writer and telemetry reader print `ducklake_invocation`,
  which has timings but no verb, caller or transport. The telemetry writer prints
  `telemetry_request`, which has `verb` and `caller_role` (the signed caller's role or user name, so
  agent-session calls and CI calls can be told apart on that one function) but no `connect_ms` and
  no `elapsed_ms`. Section 10 (G4) lists both field sets.
- The REST API had been live for a few hours when this prompt was written (first agent traffic
  2026-11-14). Its metrics are thin. Treat any REST-specific percentile as low confidence unless you
  reproduce it; build the latency picture from the Lambda logs, which hold 14 days across both
  transports.
- CI callers run on a schedule; time matters less to them. Rank interactive agent-session impact
  above CI impact. Outside the telemetry writer you cannot separate the two in the logs; say so
  where it limits a conclusion.
- Four latency measures appear in the record. Never compare across them without saying which you
  used: `CommitLatencyMs` (a CloudWatch metric, the catalog commit only), `elapsed_ms` (the handler's
  own wall time), the REPORT line's `Duration` (the Lambda runtime's wall time), and API Gateway
  `Latency` / `IntegrationLatency`. Unless a rule here says otherwise, "a call over 29 s" means
  REPORT `Duration` above 29,000 ms.
- "Reader" names three functions: the ops reader (`agent-platform-ducklake-reader`), the telemetry
  reader (`agent-platform-ducklake-telemetry-reader`) and a telemetry content reader that has no API
  route and is OUT of scope.
- `reset_warm_connection` drops the cached connection in the one container that receives the call.
  The process, its imported modules and its file cache survive. A reconnect after a reset is a
  `fresh_connection`, never a `new_env`; do not report one as the other.
- The catalog backend is Neon today. Decision 253 moves it to RDS PostgreSQL; that is decided and
  not built. "RDS" in the record is a plan, not the running system.
- The smoke catalog (`ducklake_smoke`), the telemetry writer's smoke twin, the maintenance function
  and the catalog DR function are context only.

## 4. SCOPE

Surfaces (rate each; state in brackets):
- S1 Session-side clients and their callers [built]: `src/common/ducklake_reader_client.py`,
  `scripts/ops_portal/writer_transport.py`, `scripts/telemetry_capture/transport.py`,
  `scripts/telemetry_capture/read_route.py`, `src/telemetry_client/`, `scripts/agent_sdk/mcp_server.py`,
  and the flows that call them (preflight, the ops portal, sync, the telemetry capture runner).
- S2 Transport [built]: the session egress proxy (a black box you can only time) and the REST API in
  `terraform/bootstrap/agent_ducklake_api.tf`.
- S3 Lambda runtime [built]: the four handlers under `src/lambdas/ducklake_reader/`,
  `ducklake_writer/`, `ducklake_telemetry_reader/`, `ducklake_telemetry_writer/`; the connection
  authority `src/common/ducklake_runtime.py`; function sizing and timeouts in
  `terraform/personal/ducklake_lambdas.tf`, `ducklake_telemetry_reader.tf`, `ducklake_telemetry_writer.tf`.
- S4 Data layer [built; the RDS move is designed-unbuilt]: verb SQL, table partition layout, data
  file counts, the merge cadence, and the catalog.
- S5 Latency governance [partly built; roadmap item T1.9 is designed-unbuilt]: budgets, gates,
  alarms, observability, and the planning, critique and review method.

Vocabulary:
- Route: one of `ops/reader`, `ops/writer`, `telemetry/reader`, `telemetry/writer`.
- Verb: a named read or write operation a function serves. Action: the request's `action` field.
- Invocation line: the one JSON log line a handler prints per request (Section 3, two formats).
- Execution environment: one Lambda container. Each has its own CloudWatch log stream, so
  `logStreamName` identifies it. Within a stream, a request's invocation line is the last one before
  that request's `REPORT RequestId` line.
- Path, per invocation, mutually exclusive:
  - `new_env`: its REPORT line carries `Init Duration` (the first request of an environment).
  - `fresh_connection`: not `new_env`, and its invocation line has `connect_reused: false`.
  - `reused_connection`: its invocation line has `connect_reused: true`.
  - `connectionless`: its invocation line has `connect_reused: null` (for example `describe`).
- Phase (used in `latency_decomposition`):
  - `client_overhead`: time in the calling process before the HTTP request is sent (URL resolution,
    credential resolution, signing). Measured client-side.
  - `transport`: client wall time of the HTTP exchange minus the function's REPORT `Duration`
    (proxy, TLS, API Gateway).
  - `lambda_init`: REPORT `Init Duration` (`new_env` only).
  - `connect`: `connect_ms`.
  - `first_statement`: `elapsed_ms - connect_ms` on `new_env` and `fresh_connection` invocations.
  - `first_file_touch`: on `reused_connection`, the excess of a statement over the same verb's
    steady time when it is the first use of that table's data in that environment (cold-start kind
    4). Estimated from pairs of calls.
  - `steady_statement`: statement time on `reused_connection` for repeat use; estimated by the
    route's `reused_connection` p50.
  - `catalog_wake`: extra connect or statement time attributable to the Postgres catalog resuming
    (cold-start kind 5). Write `null` with a note if you cannot separate it.
- Tuning and redesign (used for opportunities): a `tuning` change keeps the four functions, their
  engine, their runtime language, their compute substrate and the synchronous request-and-response
  contract of each verb. Configuration, code, data layout, compaction, caching inside or beside the
  functions, moving work to the init phase, snapshot restore, provisioned capacity and keep-warm
  schedules are all tuning. A `redesign` change alters one of those five things (a different engine
  or language, a different substrate, an asynchronous or paged contract for a verb, or serving a read
  from a store other than the catalog).
- Rec: a recommendation, the repository's work-item record, id `rec-NNNN`.

Out of scope, one line each: IAM and resource-policy correctness of the API (security is not the
subject); telemetry data content and schema; the ops data model itself; CI pipeline duration; the
maintenance function's own runtime except where it sets file counts; cost of the catalog backend
except as an input to a remedy's cost.

Trust nothing quoted here: obtain every file, line, size and number by reading the file or running
the query yourself. Re-derive from the repository and the live system, and record any anchor that
does not resolve in `meta.stale_anchors`.

## 5. SETUP

Run these first. NEVER abort on a failure -- take the named degraded path.

0. Hook canary (repository rule, AGENTS.md). This step applies only if you are a top-level
   interactive session; a subagent, CI job or scheduled agent skips it. If your opening context has
   no `[hook_canary]` line from this repository's hooks, say "repo hooks not loaded" as your first
   status, write nothing, and end the turn. This is the one sanctioned stop in this prompt.
1. `git fetch origin main` then `git rev-parse --short origin/main`. This sha IS the audited tree;
   use it in the deliverable filenames, the branch name and `meta.audited_commit`. Then
   `git switch -c audit/ducklake-api-latency-<sha> origin/main`. IF that branch already exists
   (locally per `git branch --list`, or on the remote per `git ls-remote --heads origin <name>`),
   append `-r2` (then `-r3`, ...) to the branch name only; the deliverable filenames keep `<sha>`.
   IF fetch fails: use `git rev-parse --short HEAD`, branch off `HEAD`, and record "base is local
   HEAD" in `meta.contract_notes`. IF `git switch -c` fails for any other reason: record the error
   in `meta.contract_notes`, do the audit on the current branch, and at Section 16 skip steps 3-5:
   leave the two deliverables uncommitted and end the turn with a final message naming their paths
   and the error.
2. `bin/venv-python -m scripts.session.preflight --roadmap-detail full` -- writes
   `logs/.preflight-report.json` (gitignored; expected). Recommendations have no local cache in this
   repository; rec dedup uses the live reader (step 3). IF this fails: record the error in
   `meta.contract_notes` and continue.
3. Rec dedup probe -- run it whatever step 2 did:
   `bin/venv-python -m scripts.recs_related --terms "cold start latency lambda" --limit 5 --table`.
   IF this fails: do NOT abort -- set `meta.degraded_dedup = true`, set every finding and inventory
   row whose ownership rests on a rec search to `confidence: HYPOTHESIS` (findings also get
   `roadmap_crossref.dedup_hit_count: null`), dedup against the roadmap and `docs/DECISIONS.md`
   only, and use the rec descriptions in Section 10 (G11) as the only rec content. Proceed.
4. Roadmap parse check:
   `bin/venv-python -c "import yaml; d=yaml.safe_load(open('docs/ROADMAP-PLATFORM.yaml')); print(len(d['tier_items']))"`.
   IF it errors, record it in `meta.contract_notes` and read the YAML as text.
5. AWS read check. In every shell call that uses the `aws` CLI, first set:
   `export AWS_PROFILE=agent_platform AWS_REGION=$(tr -d '[:space:]' < config/aws-region); export AWS_DEFAULT_REGION=$AWS_REGION`
   (each shell call is independent). Then run
   `aws cloudwatch list-metrics --namespace DuckLakeWriter --query 'length(Metrics)' --output text`.
   IF it fails: set `meta.degraded_empirical = true`, skip Section 11 parts A and B, use the dated
   samples in Section 10 (G10) as your only observed numbers, and give every finding, inventory row
   and `latency_decomposition` row that rests on them `confidence: HYPOTHESIS`. Proceed.
6. Live probe check: run the Section 11 part C recipe once with route `ops/reader` and payload
   `{"action":"describe"}` (this call counts toward the part C limit). IF it does not print
   `"http": 200`: set `meta.live_probe_unavailable = true`, skip part C, and proceed.
7. External lookups: you MAY use web search or fetch, at most 12 lookups in total (each search and
   each page fetch counts as one) -- do NOT exceed -- solely to confirm current external facts that an
   opportunity or a Q4 checklist row depends on (for example Lambda SnapStart support for the Python
   runtime, provisioned concurrency pricing, API Gateway timeout rules, DuckDB or DuckLake settings).
   Record each URL in the evidence or basis field that uses it. IF web access is unavailable: set
   `meta.web_unavailable = true`, answer from your own knowledge, and mark each dependent item
   `confidence: HYPOTHESIS`.

Repository-wide `validate` is advisory outside CI; running it is optional and never a gate
(Section 16).

## 6. NORTH STAR

The bar you judge each surface against. These are judgment bars, not absolutes: argue each case.

- NS1 Pay for work, not overhead. A routine read or write should cost roughly what the work costs.
  Time spent on connecting, discovering an endpoint, re-fetching known data or retrying a
  deterministic failure does no work for the caller.
- NS2 Latency is a designed property. It is budgeted when a path is designed, measured in
  production, and enforced by something that fails or alarms. (The project's own loop names latency
  as a signal to observe and improve: `docs/PROJECT_CONTEXT.md`, "Recursive self-improvement model".)
- NS3 Slow paths fail fast and typed. A request that cannot finish answers a typed error before any
  platform timeout does it for it, and a retry is spent only where it can change the outcome.
- NS4 Mechanism fits workload. The cheapest mechanism that serves the access pattern is the right
  one. An engine, a runtime language or a compute substrate is challenged only on a measured share
  of the latency and only with a named alternative (the operator's stated bar).
- NS5 Every second is attributable to a route, a verb, a phase and a caller class.
- NS6 The operator owns the trade-off. The audit states savings, effort, cost and risk; it does not
  pick a threshold or assume a budget.

## 7. THE QUESTIONS

Each question gets its own entry in `question_answers`. Verdict rules are pinned here; where a rule
depends on a table, fill the table first.

Q1. Where does the time go? For each route and each path, decompose a request's wall time into the
phases of Section 4, and state what share of calls over 29 s each phase explains. Fill
`latency_decomposition`; the prose points to it.
Verdict: `decomposed` if, for every route, the phases you could measure account for the wall time on
every path that holds calls over 29 s; `partially-decomposed` if at least one such phase could not
be separated (name the missing instrumentation); `not-decomposable` if no route could be decomposed.

Q2. Is the 120 s integration timeout inherent to this workload, or avoidable? Fill `timeout_outlook`,
one row per route, by the tier rules in Section 14. Report the numbers as information; recommend no
target. Per-route verdict:
- `within_default`: the route's observed p99 `Duration` is already at or under 29 s.
- `avoidable-by-tuning`: `fits_29s_after` is `tier_1` or `tier_2` (tuning rows only).
- `avoidable-by-redesign`: `fits_29s_after` is `tier_3` (reached only with a redesign row).
- `inherent`: `fits_29s_after` is `none`, and you can name the work the request needs that takes
  the time.
- `undetermined`: `fits_29s_after` is `unknown`, or it is `none` and you cannot name that work.
The Q2 verdict is taken over the routes that are not `within_default`, as the first of this list
that any of them has: `undetermined`, `inherent`, `avoidable-by-redesign`, `avoidable-by-tuning`. If
every route is `within_default`, the Q2 verdict is `avoidable-by-tuning` and the prose says the tail
above p99 is what needed the quota.

Q3. What would reduce latency, and do the remedies already owned by open recs and roadmap items
cover it? Build `optimisation_inventory`: one row per opportunity, including alternative ways to
remove the same cost and options that are pure trade-offs. For each row that has an owner, judge in
`owner_delivers_saving` whether the owner's stated acceptance would deliver the row's saving.
Verdict, from `est_daily_seconds_saved` summed over tuning rows (within an `alternative_group` count
only the row with the largest value): let OWNED be the sum over rows whose `ownership` starts with
`owned` (`owner_delivers_saving: yes` counts in full, `partly` counts half, `no` counts zero) and
ALL be the sum over every tuning row. `sufficient` if OWNED / ALL is at least 0.8; `partial` if at
least 0.3; `insufficient` below that. If more than half the tuning rows have a null estimate, answer
`partial` and say the estimate base is too thin to rank.

Q4. Does the architecture fit the workload when rated against industry practice? Rate each row of
the EXTERNAL CHECKLIST below in `external_checklist` (`met` | `partial` | `missed`; `partial`
requires an argued, property-matched compensating control in `evidence`). Then fill
`architecture_challenges` with one row for each of the two subjects the operator named: the engine
(DuckDB with DuckLake) and the Lambda runtime language (Python). Rows for `compute_substrate` and
`sync_request_model` are optional. Row verdicts:
- `challenge_with_alternative`: requires a measured share of latency attributable to the subject AND
  a concrete named alternative with its migration cost, expected gain and what would be lost.
- `keep_and_tune`: the subject carries a measured share, and tuning rows in your inventory address
  it.
- `keep`: you measured the subject's share and it is not a material contributor.
- `undetermined`: you could not measure the subject's share (always the case under
  `meta.degraded_empirical`).
"It would be faster" without a measured share is never `challenge_with_alternative`.
Verdict for Q4, from the checklist: `sufficient` if no row is `missed` and at most two are
`partial`; `partial` if one to three rows are `missed`; `insufficient` if four or more are.

EXTERNAL CHECKLIST (the single source for the maturity top tier, Section 15). The surfaces in
brackets are the ones each row counts against.
- EC-1 [S5] Latency objectives per endpoint, stated as percentiles, measured and alarmed.
- EC-2 [S2, S3] The synchronous request path finishes well inside the gateway's default timeout;
  longer work uses pagination or an asynchronous submit-and-poll pattern.
- EC-3 [S3] Cold-start mitigation for latency-sensitive serverless functions: heavy initialisation
  runs in the init phase, or the function uses snapshot restore, provisioned capacity or a keep-warm
  schedule; package and extension size is kept small.
- EC-4 [S1, S3] Connection and discovery reuse on both sides: keep-alive HTTP sessions, cached
  endpoint discovery and credentials on the client; connection reuse on the server.
- EC-5 [S4] Storage layout matched to the access pattern: point lookups are served by a keyed or
  indexed structure or a serving cache; lake tables are kept compacted so a query opens a bounded
  number of files.
- EC-6 [S1, S2, S3] Tiered timeouts with typed failures: query deadline, then function timeout, then
  gateway timeout, then client timeout, each shorter than the next and each answering a typed error.
- EC-7 [S1] A retry budget with idempotency: bounded retries with backoff and jitter, no retry of a
  deterministic failure, idempotency keys on writes, no retry amplification.
- EC-8 [S1, S2, S3, S5] Request-level observability: per-operation attribution, phase timers, and
  one request id that follows a call through client, gateway and function.
- EC-9 [S1, S4] Payload discipline: pagination, projection, server-side filtering and aggregation;
  batch operations instead of N single-row calls.
- EC-10 [S5] Performance regression control: latency is measured in the deploy pipeline or by a
  standing canary against a budget that fails or alarms on regression.

Q5. How and why was the system built this way? Reconstruct the decision points in `process_trace`:
at each, what latency evidence existed and what the process did with it. Classify each row `kind` as
`chosen-tradeoff` (someone weighed the latency cost with evidence in hand and accepted it),
`unexamined-consequence` (the latency effect was not examined at that point) or `unclear`. Mark
`bears_on_dominant_cost: yes` on rows that led to a phase your Q1 answer shows as dominant.
Verdict, over rows with `bears_on_dominant_cost: yes`, ignoring `unclear` rows: `accepted-tradeoff`
if all are `chosen-tradeoff`; `process-gap` if all are `unexamined-consequence`; `mixed` otherwise
(including when no row qualifies; say so).

Q6. Does present governance catch what Q5 found, and if not, what is the smallest change that
would? For each `process_trace` row of kind `unexamined-consequence`, fill `caught_today_by` with an
existing, enforced mechanism that would catch the same thing now (cite it), or leave it empty.
Proposed mechanisms are S5 findings (listed in this answer's `basis`); a mechanism that would not
have changed any row of your trace is not an answer.
Verdict (the present governance): `sufficient` if there are no `unexamined-consequence` rows or
every one has a non-empty `caught_today_by`; `partial` if at least one has; `insufficient` if none
has.

Q7. Questions the operator did not think to ask. Answer each seed below AND add your own. This entry
uses the `answers` shape (Section 14), not a verdict.
- Does the decided catalog move to RDS PostgreSQL (Decision 253) change any phase you measured?
- What does this latency cost per day? Method: per route, sum over your sampled calls of
  `max(0, Duration - that route's reused_connection p50)`, divided by the sample's days. Report
  seconds per day per route and say that it mixes agent and CI calls except on the telemetry writer.
- What does the present path cost per month to run (Lambda and API Gateway at sampled traffic), as
  the baseline against which an opportunity's added cost should be read?
- Do client retries turn a slow dependency into a longer outage for the caller, and can a retried
  write duplicate?
- With reads that can run 110-120 s, what bounds the number of concurrent catalog connections?
- Did the quota raise itself carry a cost or a risk? (A planning note records, as an inference, that
  AWS may lower the account's Region-level throttle quota when it raises the integration timeout;
  another records as unproven whether the egress proxy holds a request open for 120 s.)
- Which of the session flows could be answered without calling a Lambda at all?
- Is anything in the record measuring the wrong thing (a gate or metric whose subject is not the
  latency an agent experiences)?

## 8. RUBRIC

Rate every surface S1-S5 on every dimension. Enum: `strong` | `adequate` | `weak` | `absent` | `n/a`.
`n/a` is correct and costless where a dimension does not structurally apply -- never manufacture a
rating or a finding to fill a cell.

- VD1 Budget: a latency budget is stated for this surface and something enforces it. (NS2; Q5, Q6)
- VD2 Attribution: time spent in this surface can be attributed to route, verb, phase and caller
  class from recorded data. (NS5; Q1)
- VD3 Cold-path efficiency: work done on `new_env` and `fresh_connection` paths is the work those
  paths need, done at the cheapest point. (NS1; Q1, Q3)
- VD4 Warm-path efficiency: steady-state work per request. (NS1; Q1, Q3)
- VD5 Round-trip and payload discipline: number of calls and bytes per unit of caller purpose.
  (NS1; Q3, Q7)
- VD6 Failure-path cost: deadlines, timeouts, retries and typed errors. (NS3; Q2, Q7)
- VD7 Fit of mechanism to workload. (NS4; Q4)
- VD8 Design-time treatment: latency was considered, with evidence, when this surface was designed
  or changed. (NS2; Q5, Q6)

## 9. DEEP-DIVES

- DD-A Cold path, end to end (feeds Q1, Q3, Q4). Trace one request on a new execution environment
  from handler entry to response: lazy imports, S3 credential resolution, DSN fetch, the three
  extension LOADs, ATTACH, the first statement. Reconcile the code path with `Init Duration`,
  `connect_ms` and `elapsed_ms`. Say which steps could move to init, which are repeated per
  connection and which per environment.
- DD-B First statement and first file touch (feeds Q1, Q2, Q3, Q4). Explain the gap between
  `connect_ms` and `elapsed_ms` on fresh connections, and the tens-of-seconds statements seen on
  reused connections. Test the competing explanations against each other: data file count and serial
  file opens; catalog metadata loading over the Postgres scanner; per-column or per-file caching;
  CPU share at the configured memory size; the single DuckDB thread. Use the differential across
  functions (the ops reader, the telemetry reader and the writer differ in memory, catalog and file
  count) and the live probes in Section 11 part C. State what you could not separate.
- DD-C Client round trip and flow fan-out (feeds Q1, Q3, Q7). For each flow in Section 10 (G7),
  count round trips, classify each as needed or avoidable for the flow's purpose, and estimate the
  flow's wall time on the reused and new-environment paths.
- DD-D Failure path (feeds Q2, Q7). Lay out every timeout and deadline from client to statement, in
  order, with its value and what the caller receives when it fires; include the egress proxy's own
  limit if you can observe one, and say so if you cannot. Compute the worst-case wall time of one
  logical read and one logical write under the retry policies. Identify which failures are retried
  although a retry cannot change the outcome, and whether a retried write can duplicate.
- DD-E Decision trace (feeds Q5, Q6). Build the `process_trace` from Section 10 (G9) and your own
  reading of the cited Decisions, plans and recs.

## 10. GROUNDING MAP

This map spends your cognition on judgment, not grep. Verify each anchor before relying on it; line
numbers are from the tree at commit `421b360b` and will drift. Facts are stated without verdict.

G1 Transport
- `terraform/bootstrap/agent_ducklake_api.tf:12-18` four routes map to four functions; `:24`
  `ducklake_api_timeout_ms = 120000`; `:140-146` the service quota resource (quota code
  `L-E5AE38E3`, value 120000); `:183` the integration's `timeout_milliseconds`; `:228-244` the access
  log format (includes `integrationLatency` and `responseLatency`, no verb or body); `:250-264`
  method settings: `metrics_enabled = false`, `logging_level = "OFF"`, throttle 20 rps / burst 40.
- The session identity cannot read the access log group (denied by design) and cannot run
  CloudWatch Logs Insights queries; `filter-log-events` on the four Lambda log groups and
  CloudWatch metric reads work.
- `docs/DECISIONS.md:257` Decision 255. Clause 3 (`:303-304`): "The integration timeout is 120 s
  under the account quota". Problem statement (`:280-282`): "Reads run up to 110 s, past an HTTP
  API's 30 s cap". Reversal condition `quota-below-deadline` (`:335-337`), `kind: manual`.
- `docs/DECISIONS.md:167` Decision 256: sessions reach AWS only as a proxy-signed IAM user; the proxy
  signs only `amazonaws.com` hosts, which is why Function URLs are unreachable from sessions.

G2 Function configuration
- `terraform/personal/ducklake_lambdas.tf:305-306` writer: `timeout = 120`, `memory_size = 3008`;
  `:350-351` reader: `timeout = 120`, `memory_size = 1024`; `:73-114` two layers (dependencies;
  three baked DuckDB extensions); `:122,132` log retention 14 days; `:463` the writer's only alarm
  (errors). No reserved concurrency is declared on the ops reader or writer.
- `terraform/personal/ducklake_telemetry_reader.tf:84-85,89`: 120 s, 1024 MB, reserved concurrency 5.
- `terraform/personal/ducklake_telemetry_writer.tf:128-129,133`: 120 s, 3008 MB, reserved
  concurrency 5; `:255` comment: the warm-wall p95 "is unmeasured for this writer".
- No provisioned concurrency, snapshot-restore setting or keep-warm schedule for these four
  functions was found in `terraform/`.
- All four use `architectures = ["x86_64"]`, runtime `python3.12`.

G3 Runtime cold path
- `src/common/ducklake_spike.py:49-67` `_require_duckdb` imports `duckdb` on first call, not at
  module import; `:81-102` `_set_s3_credentials` imports `boto3`, builds a session and writes the
  frozen credentials into the connection with `SET` statements.
- `src/common/ducklake_runtime.py:154-158` three baked extensions (`ducklake`, `httpfs`,
  `postgres`); `:207-227` `fetch_dsn` reads an SSM SecureString on each (re)open; `:237-251`
  `libpq_conninfo`, connect timeout default 10 s; `:259-335` `open_connection`: `:296`
  `SET threads=1`, `:303-304` one `LOAD` per extension, `:331-334` the `ATTACH`.
- `src/common/ducklake_runtime.py:353-498` the per-container warm connection cache;
  `get_warm_connection` at `:420`, liveness probe at `:466`; `reset_warm_connection` at `:405`.
- `src/common/ducklake_event_reads.py:32-34,332-334` the telemetry slot sets `memory_limit` 300MB
  and a spill directory with a 300MB cap.

G4 Handlers and log lines
- `src/lambdas/ducklake_reader/handler.py:63-77` open and warm-acquire; `:80` `connect_probe`;
  `:241` `reset_warm_connection`; `:304` connectionless actions; `:323` 6,000,000-byte response cap;
  `:350-381` dispatch with one reopen-and-retry on a dead connection; `:406-456` entrypoint.
  `_handle` receives no remaining-time callable.
  `tests/lambdas/ducklake_reader/test_event_routing.py:104` `test_ops_verbs_run_without_a_deadline`.
  `docs/contracts/ducklake_reader.yaml:314-316` records the deadline rows leaving this function.
- `src/lambdas/ducklake_telemetry_reader/handler.py:176` `deadline_guard`; `:233` passes the Lambda
  context's remaining time. `src/common/ducklake_read_deadline.py:19` `DEADLINE_MARGIN_S = 10.0`.
  Its actions (`:107-112`) are `describe`, `attach_check`, `named_read`; it has no reset action.
- `src/lambdas/ducklake_writer/handler.py:60-75,284,303-336`; diagnostic actions in
  `src/lambdas/ducklake_writer/smoke_actions.py`: `connect_probe` (`:29`), `attach_check` (`:55`,
  runs BEGIN / SELECT 1 / COMMIT), `reset_warm_connection` (`:97`).
- `src/lambdas/ducklake_telemetry_writer/handler.py:156-176,192-216,226-229`.
- `ducklake_invocation` line (`src/common/ducklake_metrics.py:60-89`), fixed fields: `function`,
  `action`, `status`, `connect_ms`, `connect_reused`, `reopened`, `elapsed_ms`, optional
  `response_bytes`. No verb, table, caller or transport.
- `telemetry_request` line (`src/lambdas/ducklake_telemetry_writer/telemetry_log.py:104-141`),
  fields include `verb`, `status`, `error_type`, `request_bytes`, `occ_retries`, `commit_ms`,
  `connect_reused`, `caller_role`. `caller_role` comes from the signed caller's ARN
  (`src/lambdas/ducklake_telemetry_writer/handler.py:96-100`). No `connect_ms`, no `elapsed_ms`: on
  this route use REPORT `Duration` for wall time, and write `null` with a note for the `connect` and
  `first_statement` phases.

G5 Data layout and maintenance
- `config/lambda/ducklake/field_semantics.yaml:105-107` default partition transforms: history tables
  by `year/month/day(created_timestamp)`, current tables by `bucket(8, <key>)`; the ops tables
  declare the same shapes (`:184-186`, `:463-465`).
- `src/common/ducklake_named_reads.py:101-250` the ops read verbs; most read the current
  projection, `rec_history` (`:224-226`) reads the history table. Read primitives:
  `src/common/ducklake_reads.py:25-125`.
- Merge schedule: `terraform/personal/ducklake_maintenance.tf:282` ops merge every 6 h at :30;
  `terraform/personal/ducklake_maintenance_telemetry.tf:36` telemetry merge every 6 h at :50. Verb
  semantics: `docs/contracts/ducklake_maintenance.yaml` (`merge_ops`).
- `docs/DECISIONS.md:11453` Decision 88 (dated 2026-06-16): access-pattern invariants (i) reuse
  warm connections, (ii) never re-query data already in the local read cache, (iii) keep the catalog
  compacted, (iv) size the DR dump cadence to measured egress; an amendment at `:11490` (from
  Decision 214 clause 2, `:3295`) adds (v) the catalog stats index is present and valid on every
  catalog schema, reported as the `StatsIndexValid` metric.
- `docs/DECISIONS.md:443` Decision 253, `:471-472`: "The 11-14 s cold start is DuckDB extension
  loading and ATTACH, the same on either backend (rec-5042)."

G6 Clients
- `src/common/ducklake_reader_client.py`: `:45-58` transport switch; `:73-75` 3 attempts, statuses
  502/503/504, backoff 2 s then 4 s; `:97-113` one SSM `get_parameter` per resolution with a new
  session and client; `:134-142` `api_route_url`; `:407-449` `_reader_url` runs on every call (env
  override first); `:451-493` `_invoke`: `:462-463` new `boto3.Session` and credentials, `:473`
  `requests.post(..., timeout=180)` with no shared HTTP session; `:478-481` 413 and a typed 504
  `read_deadline_exceeded` are not retried.
- `scripts/ops_portal/writer_transport.py:38-40` 3 attempts, backoff 2 s and 5 s; `:134` URL
  resolved per write; `:141-142` session per write; `:144` retries only when an idempotency ULID is
  present or the action is `update_ops`; `:152` `timeout=180`.
- `scripts/telemetry_capture/transport.py:35` `MAX_TIMEOUT_S = 130.0`; `:39-41` retry statuses
  0/429/503, backoff base 0.5 s, cap 8 s; `:174-190` the writer URL is cached in an on-disk state
  file per working tree.
- `scripts/telemetry_capture/read_route.py:93` the telemetry reader route; `:117` the content reader
  has no API route.
- `scripts/agent_sdk/mcp_server.py:133-137` two `describe` calls when the server process starts;
  `:78` one reader call per tool call.

G7 Session flows (round trips as read from the code; re-count them)
- Preflight (`scripts/session/preflight.py`): `:208` primes the reader URL into the process
  environment once (`scripts/preflight/aws_infra.py:67`); `:211` one telemetry window read on a
  thread with a 60 s deadline (`scripts/preflight/session_history.py:19`); `:224` `warm_sync`, three
  sequential table pulls (`scripts/sync/ops.py:467-468`, `:84-85`); `:238-239` two named reads and
  one telemetry health read. Preflight derives the latest decision timestamp from the full
  `ops_decisions` pull (`:247`, `:261`); a verb `decisions_max_updated` exists.
- Ops portal: `update_rec` is one read plus one write; `file_rec` is one write plus one `rec_by_id`
  read per dependency (`scripts/ops_portal/write_validators.py:87,129`); a batch verb `recs_by_ids`
  exists (`src/common/ducklake_reader_client.py:575`).
- `scripts/convergence_health/budget_ingest.py:397-400` one `rec_by_id` call per row in a loop.
- `scripts/ops_portal/ci_rca_runtime.py:72,84-85` reads all rows of one source, then filters on
  status in Python.
- Telemetry capture: a detached runner per turn with a 600 s budget
  (`scripts/telemetry_capture/runner.py:59`); each pass that has rows calls `describe` first
  (`:102`, `:326`), then one request per chunk.
- Telemetry verification monitor: `scripts/telemetry_verify/monitor.py:34-37` budgets one reader
  call at a worst case of 450 s.

G8 Budgets, gates and alarms
- `src/common/ducklake_writes.py:66` `COMMIT_LATENCY_BUDGET_MS = 2000.0`; Decision 82
  (`docs/DECISIONS.md:11724`) makes the gate term per-invocation wall p95 on warm invocations. The
  gate is a post-deploy smoke run (`scripts/ducklake_smoke/lambda_ec_gates.py:155`) that pre-warms
  its containers (`:106-116`).
- Decision 214 clause 4 (`docs/DECISIONS.md:3297`): the writer's warm wall p95 against 2,000 ms and
  cold attach p95 against 3 s; its reversal condition (`:3310-3312`) is `kind: manual`.
- Alarms that name a latency: the telemetry writer's commit-latency alarm
  (`terraform/personal/ducklake_telemetry_writer.tf:283-293`, p95 of `CommitLatencyMs`) and the
  telemetry merge duration alarm (threshold at `terraform/personal/ducklake_maintenance_telemetry.tf:30`).
  No alarm on Lambda `Duration` or on API Gateway latency was found for the four functions.
- Roadmap `T1.9` (`docs/ROADMAP-PLATFORM.yaml:2438`) "Lambda surface SLOs + CloudWatch alarms wired
  to log-rec", status `deferred_post_mvp`.
- Method: `.claude/skills/planning/SKILL.md` has four assessment sections -- Infrastructure and
  Lambda (`:126`), Complexity (`:140`), Data-Model (`:147`), Main Divergence (`:157`). A search of
  that file and of `.claude/commands/plan.md` for latency, performance, SLO or cold start finds no
  assessment that asks about them. `.claude/skills/plan-critique/SKILL.md:45` mentions performance
  once, as an example of a vague acceptance criterion. `.claude/skills/code-review/SKILL.md:95-102`
  lists "Performance considerations" as one review bullet.

G9 History (dates and artifacts; the reading is yours)
- 2026-06-06, commit `e9edc810`: `ducklake_lambdas.tf` is created with `timeout = 120` on both
  functions; the line carries no comment giving a reason.
- 2026-06-15 a catalog egress breach; 2026-06-16 Decision 88 responds with warm connection reuse.
- Decision 82: the 2,000 ms gate measures warm invocations; cold start is assigned to a separate
  attach gate.
- 2026-10-09, Decision 214 (`docs/DECISIONS.md:3291`): "Lambda p95 26-55 s and commit p95 5.5 s
  against a 2,000 ms budget"; a stats index is created instead of a backend move.
- 2026-10-18, rec-4170 and rec-4220 record the seven-day result: reader warm p95 37.2 s -> 1.88 s;
  writer warm p95 3.79 s; cold attach p95 unchanged at 13-21 s; reader cold elapsed p95 90 s.
- 2026-10-19, commit `441b774a` (#1578): the read deadline (Lambda remaining time minus 10 s) ships
  for telemetry reads together with 8 DuckDB threads on the telemetry slot; the same day commit
  `1c41dc19` (#1582) withdraws the 8 threads and keeps the deadline (rec-4474 records why).
- 2026-10-10 and 2026-10-25, commits `c21ba7da` and `a4e6a409`: the telemetry writer and telemetry
  reader are created with `timeout = 120`.
- 2026-11-09, rec-5042 is filed: cold connect 11-14 s, independent of the catalog backend.
- 2026-11-10, Decision 253: the catalog moves to RDS PostgreSQL.
- 2026-11-12/13: the egress proxy stops allowing role assumption; Decisions 255 and 256 follow.
  `docs/plans/PLAN-agent-ducklake-rest-api.yaml:880`: "reader 14,015 calls, max 120 s, p99 109.7 s
  ... So the 29 s default is too short and 120 s fits the reader's deadline." `:266` the quota
  request was filed 2026-11-13. `:353-355` a grant below 120000 stops the plan ("a lower timeout
  with 504 retries of slow reads is not this plan's design"). `:877` the route choice (REST over an
  HTTP API's 30 s cap and over direct invoke). `:882` reserved concurrency is deferred to a rec, and
  the note that AWS may lower the Region-level throttle quota (marked inferred). `:883` owed
  follow-on recs, including reads that run to the deadline. Neither that plan nor
  `docs/plans/PLAN-agent-proxy-signed-access.yaml` contains a step that measures request latency;
  the latter (`:1139`, `:1149`) lists as unproven whether the proxy holds a request open for 120 s.
- 2026-11-14: agent sessions cut over to the REST API (commits `4bdf58a9`, `c4c41b7f`).

G10 Measured samples (taken 2026-11-14 between 16:25Z and 17:40Z; small windows; re-derive)
Lambda invocation lines, 24 h, both transports mixed (ms). In this table "Fresh" means every line
with `connect_reused: false`, new environments and reconnects together:

| Function | Lines | Reused: p50 / p90 / p99 / max | Fresh n | connect_ms p50 / p90 | Fresh elapsed p50 / p90 / max | Fresh elapsed minus connect p50 / p90 |
|---|---|---|---|---|---|---|
| ops reader | 981 | 127 / 768 / 42,970 / 101,991 | 36 | 11,405 / 12,206 | 68,506 / 114,983 / 117,036 | 57,492 / 103,739 |
| ops writer | 121 | 1,192 / 3,214 / 9,621 / 9,621 | 31 | 13,330 / 20,086 | 18,151 / 25,426 / 32,171 | 4,592 / 5,708 |
| telemetry reader | 1,374 | 362 / 1,343 / 14,580 / 35,053 | 33 | 11,605 / 13,228 | 22,916 / 31,407 / 51,572 | 11,530 / 23,002 |

- REPORT lines, 24 h: `Init Duration` p50 195 ms (ops reader), 190 ms (writer), 197 ms (telemetry
  reader), 242 ms (telemetry writer). Max memory used: 312 of 1024 MB (ops reader), 371 of 3008 MB
  (writer), 630 of 1024 MB (telemetry reader), 468 of 3008 MB (telemetry writer). Two ops reader
  invocations ended `Status: timeout` at 120,000 ms. Telemetry writer `Duration`: p50 763, p90
  2,029, p99 21,550, max 27,554 over 412 invocations.
- REPORT lines carrying `Init Duration`, 24 h: 40, 31, 36, 21 (ops reader, writer, telemetry
  reader, telemetry writer). Gaps over five minutes between consecutive invocations: 36, 28, 30, 15.
  Maximum concurrent executions: 3, 1, 3, 4.
- Ops reader response size: p50 8.5 kB, p90 18 kB, max 2.3 MB. The slowest reused-connection read
  (102 s) returned 300 bytes.
- API Gateway, 48 h (the API's whole life at that point): 438 calls; `Latency` p50 733, p90 13,846,
  p95 29,823, p99 115,660, max 120,018; `IntegrationLatency` p50 738, p90 14,012, p95 30,542, p99
  115,701, max 120,000; 9 4XX, 5 5XX.
- Maintenance metrics, 72 h: ops `MergeOpsFilesBeforeTotal` 928-1,090 and `MergeOpsFilesAfterTotal`
  918 rising to 936; telemetry `MergeOpsFilesAfterTotal` 114-128; `StatsIndexValid` 1 for
  `ducklake_ops`. Ops writer `CommitLatencyMs`, 7 days: p50 2,379, p95 7,694.
- Client-side timings from one session: route URL resolution through SSM 718 ms then 450 ms on two
  consecutive calls in one process; credential resolution 9 ms; a `describe` round trip
  (connectionless) 430-590 ms on each of the four routes; `count_by_status` 950-1,010 ms three times
  in a row.
- Twenty-five minutes later, same verb, on a connection the function reported as reused:
  `count_by_status` took 72.7 s server-side, then 0.37 s when repeated at once; `open_recs_tally`
  on the same route immediately afterwards took 22.7 s.
- `connect_probe` sent to an ops reader environment that was already running returned in 1.9 s
  client wall time and reported `attach_ms` of about 1,006.
- A telemetry writer line read the same day showed `"caller_role":"agent-platform-session"` on an
  agent-session call.
- Earlier measurements in the record: rec-4474 (about 0.135 s per data file on a cold read, files
  read one after another; a local run went from 40.7 s to 7.9 s when threads went from 1 to 8);
  rec-5042 (a local run from a cold disk cache: 4.6 s for the three LOADs and 1.0 s for ATTACH,
  against 0.14 s and 0.03 s warm; cold connect was the same whether Neon was awake or asleep).

G11 Ownership already on record (dedup pointers; read each before judging it)
- rec-5042 (open): cold connect 11-14 s; add per-step timers, then cut it; names Lambda SnapStart
  as the leading candidate.
- rec-4474 (open): telemetry window reads time out cold; small files read serially at one thread;
  compaction is its durable fix.
- rec-5157 (open): stop reads running to the integration timeout (paging or submit-and-poll).
- rec-5155 (open): reserved concurrency on the four functions. rec-5156 (open): idempotent writes
  under 504 retries.
- rec-4170 (open): writer latency above the 2,000 ms budget. rec-4220 (open): pin the Neon compute;
  owns the question of disabling scale-to-zero.
- rec-4296 (open): the ops reader's daily process exits (untyped 502s). rec-4493 (open): size
  DuckDB threads against memory. rec-4706 (open): name the served verb on the telemetry reader's
  invocation line. rec-2867 (open): point reads should be point reads. rec-4237 (open): claim-check
  export for large reads. rec-4221 (open): telemetry ingestion shape. rec-4482 (open): telemetry
  writer warm wall time metric and a warm-wall p95 alarm.
- rec-5158 (open): move CI callers onto the REST API. rec-5159 (open): ask whether the proxy can
  sign Function URL hosts.
- Roadmap (`docs/ROADMAP-PLATFORM.yaml`): T1.9 (`:2438`, SLO alarms, deferred); T2.18 (`:3442`,
  maintenance; the telemetry small-file criterion near `:3541`); T2.19 (`:3635`, the commit-latency
  headroom criterion near `:3717`); T2.52 (`:4729`, response-size ceiling near `:4800`); T4.16
  (`:6982`, cold-resume criterion near `:7045`); T2.58 (`:8604`, the RDS move).

## 11. EMPIRICAL PASS

Three parts, in this order: A (baseline logs), B (metrics), C (live probes). Pull the part A
baseline BEFORE any part C call so your own probes are not in it. Tag every finding
`evidence_kind: observed` when it rests on something you measured here and `static` when it rests
on reading code or configuration. At equal severity an observed finding outranks a static one.
Write command output to scratch files outside the repository tree and summarise with a script; do
not page raw log output through your context.

Counterfactual tests, applied to every sample you use:
- For a claim that phase P causes slow calls: "if P cost zero, would this call's `Duration` have
  been at or under 29 s?" Count the calls for which the answer is yes.
- For a claim that cause X explains a cost: find a pair of observations that differ in X and little
  else (the same verb on a reused and a fresh connection; the same verb before and after a merge
  pass; a reconnect after a reset against a new execution environment; the ops reader against the
  telemetry reader) and report whether the cost moves with X. A cause with no such pair is
  HYPOTHESIS.

Part A -- Lambda logs (read-only). Log groups `/aws/lambda/agent-platform-ducklake-reader`,
`-writer`, `-telemetry-reader`, `-telemetry-writer`. Baseline window: the 7 days ending when you
start (use the full 14-day retention only to check a trend). At most 40 `aws logs filter-log-events`
commands in total -- do NOT exceed (one command may paginate; splitting a window by day uses one
command per day). One command per function fetches both line types with the stream name, which is
the join key (Section 4):
`aws logs filter-log-events --log-group-name /aws/lambda/agent-platform-ducklake-reader --start-time <epoch-ms> --end-time <epoch-ms> --filter-pattern '?"REPORT RequestId" ?"ducklake_invocation"' --query 'events[].[logStreamName,timestamp,message]' --output json`
(for the telemetry writer use `'?"REPORT RequestId" ?"telemetry_request"'`). Your own Section 5
reads fall inside the baseline; they are a handful of calls and need no exclusion.

Part B -- CloudWatch metrics (read-only). At most 60 `get-metric-statistics` or `list-metrics`
commands in total -- do NOT exceed. Namespaces: `AWS/Lambda` (dimension `FunctionName`),
`AWS/ApiGateway` (dimension `ApiName=agent-platform-ducklake-api`; per-route metrics are off),
`DuckLakeMaintenance`, `DuckLakeWriter`, `DuckLakeTelemetryWriter`.

Part C -- Live probes. The operator has authorised you to call the live functions as needed,
including forcing reconnects. At most 60 live calls in total, of which at most 10 may be
`reset_warm_connection` -- do NOT exceed. Every call made with the recipe below counts, including
the Section 5 step 6 check and any retry; preflight, `recs_related` and the Section 13 rec reads do
not count. Record the counts in `meta.live_calls_used` and `meta.resets_used`. Allowed actions, a
closed list:
- `ops/reader`: `describe`, `attach_check`, `connect_probe`, `reset_warm_connection`, `named_read`
  (any registered verb), `read_ops_current` (with a `filter` or a `limit`), `read_ops_history` (with
  a `key` or a `limit`), `dq_check`.
- `ops/writer`: `describe`, `attach_check`, `connect_probe`, `reset_warm_connection`.
- `telemetry/reader`: `describe`, `attach_check`, `named_read`.
- `telemetry/writer`: `describe`, `attach_check`.
Any other action is forbidden, in particular every write verb, `create_ops_tables`, `write_probe`
and the writer's smoke, churn and probe actions other than those listed.

Example payloads:
- `{"action":"named_read","verb":"count_by_status","params":{}}` (`describe` on a reader lists each
  verb with its parameters).
- `{"action":"read_ops_current","table":"ops_recommendations","filter":{"column":"id","value":"rec-5042"}}`
- `{"action":"read_ops_history","table":"ops_recommendations","key":"rec-5042","limit":3}`
- `{"action":"dq_check","check":"row_count_min","table":"ops_recommendations","params":{"min":1}}`
  (kinds and their parameters: `DQ_KINDS` in `src/common/ducklake_dq_checks.py`).
- `{"action":"attach_check"}`, `{"action":"connect_probe"}`, `{"action":"reset_warm_connection"}`.

Recipe (run from the repository root; prints one JSON line with the HTTP status and wall time):

```
bin/venv-python - ops/reader '{"action":"describe"}' <<'PY'
import json, sys, time
import boto3, requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from scripts.aws_profile import resolve_aws_profile
from src.common.ducklake_reader_client import api_route_url, signing_scope

route, payload = sys.argv[1], json.loads(sys.argv[2])
region = open("config/aws-region").read().strip()
url = api_route_url(route, None, region)
creds = boto3.Session(profile_name=resolve_aws_profile(None, default="agent_platform")).get_credentials().get_frozen_credentials()
body = json.dumps(payload)
req = AWSRequest(method="POST", url=url, data=body, headers={"Content-Type": "application/json"})
SigV4Auth(creds, *signing_scope(url, region)).add_auth(req)
t0 = time.perf_counter()
resp = requests.post(url, data=body, headers=dict(req.headers), timeout=180)
wall_ms = round((time.perf_counter() - t0) * 1000)
try:
    out = resp.json()
except ValueError:
    out = {}
keep = {k: out[k] for k in ("ok", "error_type", "row_count", "connect_ms", "connect_reused", "reused", "reset", "phase_reached", "attach_ms") if k in out}
print(json.dumps({"route": route, "action": payload.get("action"), "verb": payload.get("verb"), "http": resp.status_code, "wall_ms": wall_ms, "bytes": len(resp.content), **keep}))
PY
```

Rules for probes:
- The printed `wall_ms` excludes URL and credential resolution. Never print or record the URL.
- A `named_read` response does not say which path it took. After a batch of probes on one route,
  read that route's log once (a part A command over the last few minutes, with `logStreamName`) and
  match each probe to its line by order, action, and `elapsed_ms` close to `wall_ms` minus the
  connectionless round trip you measured. Note the UTC time of each probe to make the match. Where a
  match is not unique, discard the probe.
- After every `reset_warm_connection`, send your own measured call on the same route at once, so
  that your call pays the reconnect and no other caller inherits it. The reset may land on one
  environment and the next call on another; confirm from the stream names.
- You cannot create a new execution environment on demand. `new_env` data comes from the baseline
  logs. Do not idle-wait for one.
- The reset is the one state change this prompt sanctions; it changes no AWS resource or setting.

Degraded paths: Section 5 steps 5 and 6. A throttled or failed command is retried once, then
recorded in `meta.contract_notes` and skipped.

## 12. METHOD

- P1 Read: Section 10 anchors in full for S1-S4; Decisions 82, 88, 214, 253, 255, 256; the recs in
  G11; the two plans cited in G9.
- P2 Trace: work C1-C20 against the code and the record.
- P3 Deep-dives DD-A to DD-E.
- P4 Empirical pass (Section 11), applying the counterfactual tests.
- P5 Rate the rubric (Section 8) and the external checklist (Q4).
- P6 Dedup every finding and every inventory row's ownership (Section 13).
- P7 Build `candidate_adjudication`, `latency_decomposition`, `optimisation_inventory`,
  `timeout_outlook`, `architecture_challenges` and `process_trace` (Section 14).
- P8 Synthesize: answer Q1-Q7 by their pinned rules, then compute severity and maturity LAST
  (Section 15).

## 13. DEDUP DISCIPLINE

Before filing any finding, and before setting `ownership` on any inventory row, search the ownership
surfaces: the roadmap (`tier_items[]` criteria text and `candidate_decisions[]`), `docs/DECISIONS.md`
headers and clauses, and open recs
(`bin/venv-python -m scripts.recs_related --terms "<terms>" --table`, paging with `--exclude`, which
takes the comma-joined rec ids the previous page printed as `next_exclude`; at most 3 pages per
search). To read a rec in full:
`bin/venv-python -c "from src.common.ducklake_reader_client import make_reader, fetch_recs_by_ids; import json,sys; print(json.dumps(fetch_recs_by_ids(make_reader(), sys.argv[1].split(','))[0], default=str, indent=1))" rec-5042,rec-4474`
(at most 50 ids per call).

IF a dedup command fails mid-run: retry it once; if it still fails, set
`meta.degraded_dedup = true` and apply Section 5 step 3's rule to every finding and inventory row
not yet deduped.

Record `dedup_search_terms` and `dedup_hit_count` on the finding. A hit is a returned item whose
text you judge to OWN the defect (its acceptance or criterion would fix it), not merely any returned
row -- `recs_related` always returns its nearest matches; record the raw returned total in
`roadmap_crossref.note`. `dedup_hit_count` counts owning hits only. A hit means a sufficiency
assessment (`planned-insufficient` or `planned-unbuilt`) or a rejection (Section 2) -- never a fresh
discovery. A `novel` finding requires a recorded negative search (`dedup_hit_count: 0`); a `novel`
finding without one is HYPOTHESIS.

DO NOT FLAG (deliberate constraints; you may assess how they are implemented and what they cost in
latency, never whether they should exist):
- The closed named-verb boundary: no caller SQL crosses it (Decision 84 I-3).
- Warehouse state is the single source of truth and writes are synchronous: no offline outbox, no
  write buffering, no replay from a local cache (Decision 84 I-4). A read cache or read model fed
  FROM the warehouse is not excluded by this.
- Agent sessions hold no AWS credential and never invoke a Lambda directly; they reach the
  functions only through the signed REST API (Decisions 213, 255 clauses 1-2, 256). The API's IAM
  authorisation, resource policy and operator-only apply are fixed. Decision 255 clause 3 (the
  timeout) IS in scope.
- One AWS region (Decision 202).
- The public-repository content boundary (AGENTS.md "PUBLIC repository / confidential-data
  boundary", which cites Decisions 73, 83 and 101).
- Code deploys are decoupled from Terraform and go through the governed channel (Decisions 125,
  126).
- The catalog move to RDS PostgreSQL (Decision 253) is decided: assess its effect on latency; do
  not re-argue the move.
- Scheduled maintenance is agent-free (Decision 81 clause 6).
- `deferred_post_mvp` roadmap statuses (Decision 93): a deferral is not a defect. Recommending that
  an item be pulled forward is allowed, as a finding with `change_type: rescope`.
- The 500-SLOC file budget (Decision 128).
The engine (DuckDB with DuckLake, Decision 84 I-1) and the Lambda language (Python) are NOT on this
list: the operator opened both to challenge, under the Q4 rules.

## 14. OUTPUT

Write `audits/ducklake-api-latency-<sha>.yaml`:

```
audit:
  meta: {audited_commit: <sha>, base_branch: main,
         model: <your self-reported model name, free text>,
         methodology_version: 1, scope_surfaces: [S1, S2, S3, S4, S5],
         degraded_dedup: false, degraded_empirical: false, live_probe_unavailable: false,
         web_unavailable: false, live_calls_used: 0, resets_used: 0,
         sample_windows: [{source: "", window_start: "", window_end: "", n: 0}],
         contract_notes: "", stale_anchors: []}
  question_answers:
    - {q: Q1, verdict: decomposed|partially-decomposed|not-decomposable, basis: [<finding ids>], prose: ""}
    - {q: Q2, verdict: inherent|avoidable-by-tuning|avoidable-by-redesign|undetermined, basis: [], prose: ""}
    - {q: Q3, verdict: sufficient|partial|insufficient, basis: [], prose: ""}
    - {q: Q4, verdict: sufficient|partial|insufficient, basis: [], prose: "",
       external_checklist: [{property: EC-1, rating: met|partial|missed, evidence: ""}]}   # EC-1..EC-10, all ten
    - {q: Q5, verdict: process-gap|accepted-tradeoff|mixed, basis: [], prose: ""}
    - {q: Q6, verdict: sufficient|partial|insufficient, basis: [], prose: ""}
    - {q: Q7, answers: [{question: "", answer: "", basis: [<finding ids>]}]}
  candidate_adjudication:
    - {candidate: C1, outcome: finding|rejected|undetermined, findings: [<finding ids>], note: ""}   # C1..C20, all twenty
  latency_decomposition:
    - {route: ops/reader|ops/writer|telemetry/reader|telemetry/writer,
       path: new_env|fresh_connection|reused_connection|connectionless,
       phase: client_overhead|transport|lambda_init|connect|first_statement|first_file_touch|steady_statement|catalog_wake,
       p50_ms: 0, p95_ms: 0, n: 0, source: "", share_of_calls_over_29s: 0.0,
       confidence: CONFIRMED|HYPOTHESIS, note: ""}
  optimisation_inventory:
    - {id: OPT-01, finding: <finding id or null>, mechanism: "", kind: tuning|redesign,
       alternative_group: <label or null>, routes: [],
       path_affected: new_env|fresh_connection|reused_connection|connectionless|failure|all,
       est_saving: {p50_ms: 0, p95_ms: 0, basis: ""}, calls_affected_share: 0.0,
       est_daily_seconds_saved: 0,
       effort: XS|S|M|L, monthly_cost_usd: 0, cost_basis: "", risk: low|medium|high, risk_note: "",
       prerequisites: [<OPT ids>],
       ownership: unowned|owned-in-progress|owned-unbuilt|owned-insufficient,
       owned_by: [<rec or roadmap ids>], owner_delivers_saving: yes|partly|no|n/a,
       confidence: CONFIRMED|HYPOTHESIS}
  timeout_outlook:
    - {route: "", current_timeout_s: 120, observed_p99_s: 0, observed_max_s: 0,
       after_tier_1_p99_s: 0, after_tier_2_p99_s: 0, after_tier_3_p99_s: 0,
       fits_29s_after: tier_1|tier_2|tier_3|none|unknown,
       verdict: within_default|inherent|avoidable-by-tuning|avoidable-by-redesign|undetermined,
       confidence: CONFIRMED|HYPOTHESIS, note: ""}
  architecture_challenges:
    - {subject: engine|runtime_language|compute_substrate|sync_request_model,
       measured_share: "", verdict: keep|keep_and_tune|challenge_with_alternative|undetermined,
       alternative: "", migration_cost: "", expected_gain: "", what_is_lost: "",
       rationale: "", confidence: CONFIRMED|HYPOTHESIS}
  process_trace:
    - {date: "", artifact: "", latency_evidence_available: "", what_the_process_did: "",
       kind: chosen-tradeoff|unexamined-consequence|unclear, bears_on_dominant_cost: yes|no,
       caught_today_by: "", note: ""}
  per_surface_assessment:
    - {surface: S1, maturity: frontier|strong|solid|nascent, strengths: "", top_gaps: [<finding ids>]}
  rubric_ratings:
    - {surface: S1, dimension: VD1, rating: strong|adequate|weak|absent|n/a,
       evidence: "file:line|item-id|sample", note: ""}
  findings:
    - {id: LAT-01, surface: S1|S2|S3|S4|S5|shared, affected_surfaces: [], candidates: [<C ids>],
       question: Q1, dimension: VD1, title: "", evidence: "file:line|item-id|sample",
       evidence_kind: static|observed, current_behavior: "", ideal_behavior: "", gap: "",
       compensating_controls_considered: "",
       change_type: add|remove|relocate|tune|rescope|enforce|unify|persist|clarify|retune_gate,
       proposed_change: "", acceptance: "", severity: critical|high|medium|low,
       severity_rationale: "", confidence: CONFIRMED|HYPOTHESIS,
       roadmap_crossref: {classification: novel|planned-insufficient|planned-unbuilt,
                          item_ids: [], dedup_search_terms: [], dedup_hit_count: 0, note: ""},
       effort: XS|S|M|L, depends_on: [<finding ids>],
       sequencing: {safe_to_queue_now: true, blocked_behind: [], note: ""}}
  rejected_candidates:
    - {candidate: "", why_dismissed: "", compensating_control: "", control_property_match: "",
       decision_or_item_id: ""}
  summary: {total_findings: 0, novel_count: 0, planned_insufficient_count: 0,
            planned_unbuilt_count: 0, top_improvements: [<finding ids>],
            highest_leverage_change: <finding id>,
            top_opportunities: [<OPT ids>],
            maturity_S1: "", maturity_S2: "", maturity_S3: "", maturity_S4: "", maturity_S5: ""}
```

Rules, binding:
- COUNTING INVARIANT: `findings[]` is the SOLE enumerated list of defects. `total_findings =
  len(findings) = novel_count + planned_insufficient_count + planned_unbuilt_count`. Candidates
  rejected under Section 2 live in `rejected_candidates`, NOT in findings. Every other block
  (`question_answers`, `candidate_adjudication`, `latency_decomposition`, `optimisation_inventory`,
  `timeout_outlook`, `architecture_challenges`, `process_trace`, `rubric_ratings`,
  `per_surface_assessment`) is a system of record, referenced from findings where relevant and never
  counted as findings. `top_improvements` (at most 5) and `highest_leverage_change` MUST be finding
  ids; with zero findings, write `[]` and `null`. `top_opportunities` (at most 5) MUST be inventory
  ids, the five largest `est_daily_seconds_saved`.
- `candidate_adjudication` has exactly one row per candidate C1-C20. `findings` is non-empty only
  when `outcome` is `finding`. Every `rejected` row has a matching `rejected_candidates` row whose
  `candidate` field starts with the candidate id.
- `surface: shared` requires a non-empty `affected_surfaces`; otherwise `affected_surfaces` is `[]`.
  `question` and `dimension` on a finding hold the ONE question and ONE dimension it serves most.
- `change_type` meanings: `add` a missing mechanism; `remove` avoidable work; `relocate` work to a
  cheaper point (for example to the init phase or the server); `tune` a setting or a layout;
  `rescope` an existing item; `enforce` a rule that exists on paper; `unify` duplicated mechanisms;
  `persist` data that is discarded; `clarify` wording; `retune_gate` a threshold.
- Inventory rows and findings: an inventory row need not have a finding (`finding: null`). Every
  finding whose `proposed_change` would reduce latency has at least one inventory row pointing at
  it. Alternative ways to remove the same cost are separate rows sharing one `alternative_group`
  label; rows that can be combined have `alternative_group: null`.
- `ownership`: `unowned` if no owner; `owned-in-progress` if the owner is in progress (Section 2);
  `owned-unbuilt` if an adequate owner exists and is not in progress; `owned-insufficient` if the
  owner's acceptance would not deliver this row. `owner_delivers_saving` is `n/a` when `unowned`.
- Scales. `effort`: XS = one small pull request, no infrastructure change, under half a day; S = one
  pull request with tests, about a day; M = one plan of several pull requests or an infrastructure
  apply, days; L = several plans, a migration or a new Decision, weeks. `risk`: low = reverted by a
  revert, touches no data, layout or auth path; medium = touches the request path or infrastructure,
  with a rollback; high = touches data layout, the catalog or auth, or is hard to reverse.
- `est_saving` is the saving per affected call: the p50 and p95 of what an affected call would
  save. State the arithmetic in `basis`. `calls_affected_share` is the fraction of the sampled calls
  on the listed routes (taken together) that the row would speed up. `est_daily_seconds_saved` is
  computed over your sample: for each sampled call the row affects, the saving for that call (never
  more than the measured cost of the phase it removes), summed and divided by the sample's days and
  by 1000. Where you cannot estimate, write `null` for the numbers, say why in `basis`, and set
  `confidence: HYPOTHESIS`. `monthly_cost_usd` is the added running cost at sampled traffic (0 when
  none); `cost_basis` states the price and quantity assumed.
- Tiers for `timeout_outlook`: tier 1 = every `tuning` row of effort XS or S; tier 2 = every
  `tuning` row; tier 3 = every row. Within an `alternative_group` a tier applies only the available
  row with the largest `est_saving.p95_ms`. To estimate a route's p99 after a tier, re-compute over
  your sampled calls on that route: subtract from each call's `Duration` the saving of every applied
  row whose `path_affected` matches the call's path (bounded as above, and never taking a call below
  the route's `reused_connection` p50), then take the p99 of the adjusted sample. `observed_p99_s`
  and `observed_max_s` are REPORT `Duration` over your baseline window, all paths together.
  `fits_29s_after` names the first tier whose estimated p99 is at or under 29 s, `none` if no tier
  reaches it, `unknown` if the rows that matter have null estimates (then the tier fields are
  `null`). One row per route. The per-route `verdict` follows Q2's rule.
- `architecture_challenges` has at least the `engine` and `runtime_language` rows. `alternative`,
  `migration_cost`, `expected_gain` and `what_is_lost` are required when `verdict` is
  `challenge_with_alternative` and are empty strings otherwise.
- `process_trace` has at most 14 rows, in date order. `caught_today_by` is filled only on
  `unexamined-consequence` rows (Q6).
- `latency_decomposition`: include a row only where you have a number or a stated reason for none
  (`p50_ms: null` with `note`). `share_of_calls_over_29s` is the fraction of that route's calls with
  `Duration` over 29 s for which this phase passes the first counterfactual test in Section 11.
- `control_property_match` is REQUIRED whenever a compensating control is the reason for a
  dismissal: name the property the control exercises, cite where it operates (mechanism or
  file:line), and state why the control would FAIL if the defect were real. Otherwise it is an empty
  string.
- CONFIRMED requires the behaviour traced to file:line or to a sample you took; anything less is
  HYPOTHESIS. Under `meta.degraded_empirical`, numbers taken from Section 10 are HYPOTHESIS.
- Readers of the meta flags: the report's Intake section (below) prints every `meta` flag that is
  true, both call counters and `sample_windows`, so a reader knows what the numbers rest on.

Write `audits/ducklake-api-latency-<sha>.md`. It begins with a section headed `## Intake`; the word
cap of 1500 applies to everything after that section (from the next `## ` heading to the end).
1. `## Intake`: audited commit, model, the true meta flags, `live_calls_used`, `resets_used`, the
   sample windows.
2. Where the time goes, in one short table (route by path, dominant phase and its cost).
3. Is 120 s inherent (Q2), in a paragraph, with the per-route outlook.
4. The optimisation inventory as a table: grouped by effort (XS, S, M, L in that order) and, within
   a group, ordered by `est_daily_seconds_saved` descending. Columns: mechanism, saving per call
   (p50 / p95), seconds per day, monthly cost, risk, owner.
5. Engine and language: the two verdicts and their basis, in a paragraph each.
6. How it came to be built this way (Q5) and whether present governance catches it (Q6).
7. What you could not determine, and what data would settle it.
No finding or opportunity appears in the report that is not in the YAML.

## 15. SEVERITY + MATURITY

Severity applies to findings only, never to inventory rows. Assign it AFTER judgment, by defect
class, never inherited from this prompt's framing:
- critical: the latency behaviour can produce a wrong-but-trusted outcome (for example a timed-out
  write that is retried and duplicated, or a caller that treats a timeout as an empty result); or a
  call made by one of the session flows in Section 10 (G7) fails deterministically, or still fails
  after the client's own retries, on a path those flows take.
- high: calls made by those session flows are lengthened by seconds to minutes on a path taken
  daily, or a regression of that size would pass unseen, AND the compensating controls you
  considered are insufficient. A timeout that a retry recovers is at most high.
- medium: avoidable overhead, redundancy or inconsistency with a clear fix, on a rare path or worth
  under about a second per call.
- low: clarity or wording.

Compensating controls must PROPERTY-MATCH: the control exercises the same property AND would fail if
the defect were real (apply the counterfactual to the control). A control that cannot catch the
break neither lowers severity nor justifies dismissal.

Maturity, computed LAST, per surface, top-down, first match wins. A finding counts against its
`surface`, or, when `surface` is `shared`, against every surface in `affected_surfaces`.
- frontier = 0 critical or high findings on the surface AND no Q4 `external_checklist` row that
  lists this surface in its brackets (Section 7) is rated `missed`.
- strong = 0 critical AND at most 1 high.
- solid = at most 1 critical.
- nascent = otherwise.
The top tier remains reachable where you argued a property-matched compensating control; this
prompt's framing does not foreclose it. For the designed-unbuilt parts of S4 and S5, rate the design
as written.

## 16. COMMIT / PR MECHANICS

1. The base was derived ONCE in Section 5 step 1 and you are on `audit/ducklake-api-latency-<sha>`
   created from it. This is a deliberate exception to the harness-assigned session branch
   (`docs/contracts/git-ops.yaml`): the audit needs a clean two-file diff off the audited base, and
   `audit/` is a registered extra push destination in that file. The branch base MUST equal
   `meta.audited_commit`; the branch name carries any `-rN` suffix Section 5 step 1 added.
2. Pre-push gate: the YAML parses
   (`bin/venv-python -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" audits/ducklake-api-latency-<sha>.yaml`),
   the counting invariant and the C1-C20 coverage rule hold, the report is within its cap (count
   the words after the Intake section), and neither deliverable contains an AWS account id, an ARN,
   an API id, a URL of the API or a Function URL. Running
   `bin/venv-python -m scripts.validate --pre` is OPTIONAL (advisory outside CI); if you run it and
   an unrelated failure appears, record it in `meta.contract_notes` -- never fix it.
3. `git add` exactly the two deliverables, then
   `git commit -m "audit: DuckLake verb API latency"` (append any attribution trailer lines your
   harness instructs for commits) using the session's configured identity and signing (do not
   override the author, do not disable or force signing, do not amend). If a pre-commit hook rejects
   the commit, fix the cause inside your two deliverables and commit again; never bypass the hook.
4. Run `git push -u origin HEAD` as a tool call on its own (no pipe, redirect or second command).
   On a network error retry up to 4 times. IF the push still fails, or fails for a non-network
   reason: do not open a PR; end the turn with a final message naming the branch, the commit sha,
   the exact error and the `summary:` block.
5. Open the PR via `mcp__github__create_pull_request` (owner `otl-labs`, repo `ontheloop`, base
   `main`, head your branch, ready for review, NOT draft), title
   `audit: DuckLake verb API latency (S1-S5)`, body = a 2-3 sentence lede plus the `summary:` block
   in a yaml fence, plus any PR footer your harness instructs. IF the tool is unavailable or the
   call fails: end the turn with a final message naming the pushed branch, the sha, the error and
   the intended title and body. Then END THE TURN -- do not poll, do not merge, do not subscribe, do
   not self-approve. The human disposes of the PR.

## 17. GUARDRAILS

- Write boundary (closed list): the ONLY repository files you create or modify are
  `audits/ducklake-api-latency-<sha>.yaml` and `audits/ducklake-api-latency-<sha>.md`. You do NOT
  edit code, Terraform, the roadmap, Decisions, contracts, skills, commands or workflows; do NOT
  file or update a recommendation; do NOT deploy, apply Terraform, dispatch a workflow, or change
  any AWS resource or setting.
- Live system: only the calls Section 11 allows, within its limits. No write verb, ever.
- Never write an AWS account id, ARN, API id, API or Function URL, ExternalId, credential or secret
  value in either deliverable or in the PR (public repository). Function names, log group names,
  route names and metric names are fine.
- Precision over volume. Fewer than about 8 surviving findings is a valid result -- state it
  plainly; do not pad. Do not manufacture a finding to carry an opportunity: an opportunity with no
  defect behind it is an inventory row with `finding: null`.
- A run that merely restates the candidates in Section 2 has failed. Every finding survives its
  dedup search (Section 13) and, where sampled, its counterfactual (Section 11).
- Do not recommend replacing the engine, the language or the substrate to reach a number. A
  challenge needs a measured share and a named alternative (Q4).
- If an anchor does not resolve, record it in `meta.stale_anchors` and downgrade dependent items to
  HYPOTHESIS -- never invent, never abort.
