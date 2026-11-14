# DuckLake verb API latency audit (S1-S5)

Structured record: `audits/ducklake-api-latency-442ff085.yaml` (12 findings, 19 opportunities, 21 candidates
adjudicated). This report is the short layer over it.

## Intake

- Audited commit: `442ff085` (origin/main); branch `audit/ducklake-api-latency-442ff085`.
- Model: Claude (Anthropic); exact model id withheld from committed artifacts per harness policy.
- Meta flags that are true: none (`degraded_dedup`, `degraded_empirical`, `live_probe_unavailable` and
  `web_unavailable` are all false).
- `live_calls_used`: 50 of 60. `resets_used`: 3 of 10.
- Sample windows:
  - Lambda logs, four functions, baseline: 2026-11-07T21:46Z to 2026-11-14T21:46Z, 19,460 calls.
  - Lambda logs, trend only: 2026-10-31T21:30Z to 2026-11-07T21:46Z, 16,926 calls.
  - Lambda logs read back to match probes: 2026-11-14T21:46Z to 22:13Z, 50 lines.
  - API Gateway metrics (agent-session traffic only, the API's whole life): 2026-11-13 to 2026-11-14T22:00Z, 661
    calls.
  - Maintenance metrics (file counts): 2026-10-31 to 2026-11-14, 60 merge passes per catalog.
  - Writer CommitLatencyMs: 7 days, 518 samples.
  - Live probes: 2026-11-14T21:45Z to 22:13Z, 50 calls.
  - This session's own preflight: 2026-11-14T21:39Z to 21:44Z, one run, matched to log lines.
- Contract notes:
  - Budgets used: 14 of 40 log commands, 28 of 60 metric commands, 16 of 40 rec reads, 9 of 12 web lookups.
  - Preflight's first attempt used a timing wrapper this image lacks (nothing ran); the re-run passed in 322 s
    with one live HTTP 504 retried.
  - The AWS documentation host does not resolve from this session; external facts come from web searches, and
    London-region prices are unconfirmed, so every price is the published US East example rate.
  - Section 3 calls CommitLatencyMs the catalog commit only; the code times the whole write transaction.
  - Per-table file counts cannot be read by a session; they are inferred from timings and marked HYPOTHESIS.
  - Agent and CI calls cannot be separated in the logs outside the telemetry writer; 2026-11-12, 2026-11-13 and
    2026-11-14 before 12:30Z are CI-only.
- Stale anchors: none.

## Where the time goes

Seven days, REPORT `Duration`: 19,460 calls, 533 over 29 s.

| Route | Path (calls) | Main phase | Cost, p50 / p95 |
|---|---|---|---|
| ops/reader | new_env (300) | first statement | 87.0 s / 104.8 s, after 11.3 s connect |
| ops/reader | reused (4,711) | first file touch | 0.26 s / 1.8 s steady; 17-96 s on first touch |
| ops/writer | new_env (201) | connect | 14.0 s / 20.6 s, then 4.3 s |
| ops/writer | reused (361) | statement | 1.4 s / 7.0 s |
| telemetry/reader | new_env (284) | first statement | 11.5 s / 82.9 s, after 11.4 s connect |
| telemetry/reader | reused (6,180) | steady | 0.38 s / 7.4 s |
| telemetry/writer | new_env (232) | connect | about 15.9 s / 22.1 s |
| telemetry/writer | reused (3,535) | commit | 0.52 s / 3.6 s |
| every route | client | lookup, transport | 0.5 s plus 0.45 s per call |

Calls over 29 s: ops/reader 359 (241 new_env, 105 reused, 12 timeouts, 1 reconnect), telemetry/reader 167,
telemetry/writer 5, ops/writer 2.

The slow phase is not query work. A first read opens the table's Parquet files on S3 one at a time on a single
DuckDB thread and keeps the bytes in that connection only. Probes, same connection state: a metadata-only count
81 ms; one recommendation by id 5.6 s; the same row through the batch verb 84.1 s; `count_by_status` 96.4 s, then
0.58 s repeated. Connect is process loading, not the catalog: 15.5 s on a cold process, 0.49 s seconds later,
catalog 2-4 ms away. Environments live three minutes at the median.

## Is 120 s inherent

No. It is avoidable by tuning, and it is not holding.

- ops/reader: 359 over 29 s; about 0 if the current tables are kept to a bounded number of files (OPT-01), about
  178 with parallel threads alone.
- ops/writer: 2; 0 with any cold-connect row.
- telemetry/reader: 167; about 30 with more frequent compaction and parallel reads (verb-blind estimate).
- telemetry/writer: 5; 1 (an unexplained 41.7 s commit).

The answers are a few hundred bytes; the time is files opened. The ops catalog's after-merge file count rose from
807 to 944 in 14 days and never fell; the slowest first statements rose from 103.5 s to 109.0 s against a ceiling
of 120 s minus 11.3 s of connect. Twelve reads hit the function timeout in 7 days, all since 2026-11-11; the nine
immediate retries seen all succeeded, in 102-118 s. This session's preflight took 322 s with one. At 1 percent
more files a day the typical first read reaches the ceiling in about two weeks (HYPOTHESIS); then retries fail
too.

## Findings

| Id | Severity | Title |
|---|---|---|
| LAT-01 | high | Ops current tables not compact: first full read 84-106 s, at the limit, growing |
| LAT-02 | high | Ops reader has no read deadline; function and gateway timeouts equal |
| LAT-03 | high | `recs_by_ids` cannot prune: 84.1 s against 5.6 s for one row |
| LAT-05 | high | Each new environment spends 11-15 s loading the engine |
| LAT-08 | high | No latency objective, alarm or owner on the agent path |
| LAT-09 | high | The design method never asks what a call will cost |
| LAT-04 | medium | DuckDB held to one thread for a lapsed reason |
| LAT-06 | medium | Route URL looked up in SSM on every call |
| LAT-07 | medium | Retries have no time budget; a retried update can duplicate history |
| LAT-10 | medium | No verb, caller or step on the invocation line |
| LAT-11 | medium | Session flows make unneeded round trips |
| LAT-12 | medium | Warm ops writes at 3.5 times budget, unattributed |

## Optimisation inventory

| Mechanism | Saving per call, p50 / p95 | s/day | Slow calls removed | USD/month | Risk | Owner |
|---|---|---|---|---|---|---|
| XS: parallel threads on ops reads (OPT-02) | 50 s / 79 s | 2,991 | 181 | 0 | medium | rec-4493 |
| S: keep-warm schedule (OPT-08) | 10 s / 20 s | 1,143 | 53 | 0.2 | low | none |
| M: bound current-table files (OPT-01) | 64 s / 102 s | 3,996 | 359 | 0 | high | T2.18 |
| M: SnapStart (OPT-07) | 10.5 s / 20 s | 1,621 | 67 | 32 | medium | rec-5042 |
| M: provisioned concurrency (OPT-09) | 10.5 s / 20 s | 1,450 | 60 | 85 | medium | rec-5042 |
| M: telemetry compaction, parallel reads (OPT-10) | 8.5 s / 47 s | 1,192 | 126 | 0 | medium | rec-4474 |
| L: read model for ops reads (OPT-15, redesign) | 64 s / 102 s | 3,996 | 359 | 0 | medium | none |

OPT-01, OPT-02 and OPT-15 remove the same cost; so do OPT-07, OPT-08 and OPT-09: pick one of each. Smaller rows
are in the YAML: one route lookup per process (OPT-05), a typed ops-read deadline (OPT-06) and cached describes
(OPT-12), 180-210 s a day each; OPT-04 (a prunable `recs_by_ids`) saves 79 s per first-touch lookup but has no
daily figure, the verb being unlogged. Built to their stated acceptance the owned remedies deliver little: the
largest row has no adequate owner, rec-5042's acceptance is a timer test, and rec-5157's paging does not shorten
a 100 s wait.

## Engine and language

Engine (DuckDB with DuckLake): keep and tune. It holds 99 percent of function time (9,440 of 9,531 s a day) and
tuning removes about 70 percent of that. The table format as used (a file per write, a current projection kept by
delete plus insert, compaction that does not bound files) produces first touch; the engine as deployed (99 MB of
extensions per process, one thread, synchronous file I/O in 1.5.4) produces cold connect and serial opens. About
0.9 s of statement work per call remains. A row store would answer in milliseconds, but the tuned path sits far
inside any gateway limit; that gain does not justify leaving the single backend.

Language (Python): keep. It is 0.8 to 1.7 percent of function time. The 11-15 s is native extension loading
that any language would pay.

## How it came to be built this way

Mixed (Q5). Four decisions behind the main costs never examined read latency: the June storage design (a file
per write, inlining off, small files left to a merge); one DuckDB thread, added for a churn gate that Decision 82
replaced the same day; Decision 88's compaction rule, written for egress; and the 2026-10-18 measurement that
recorded the reader's cold p95 at 90 s, blamed the catalog waking and opened no item. Two were examined and
accepted: the telemetry cold-read diagnosis (fixed for telemetry only) and rec-5042 (deferred).

The quota raise was a deliberate trade: agents had no AWS access, durations were read, the operator chose,
follow-ups were filed. But the reader's p99 of 109.7 s was taken for a read deadline it does not have, and the
plan needed no step to check. The raise accommodated a cost nobody had traced.

Present governance catches none of this (Q6 insufficient): planning and critique have no latency step, the
2,000 ms gate runs on pre-warmed smoke tables, the ops file count and reader errors have no alarm, and the
reversal condition is manual. Smallest changes: LAT-08 (alarms, a file ceiling, one owner), LAT-09 (a latency
assessment; no limit raised without its cause traced), LAT-10 (verb and phase on the log line).

## Open points

- Cost: 8,761 excess seconds a day over each route's warm median (ops/reader 4,774). Running cost: about
  6.6 USD a month.
- Agents: 6.5 percent of REST calls exceeded 29 s on the cutover day (p95 38.0 s; 8 of 661 answered 5XX). A
  session starting after five quiet minutes meets three in preflight.
- RDS (Decision 253) changes no measured phase.
- Retries: 366 s worst case for one read; a retried `update_ops` can add a duplicate history version.
- The proxy does hold a request for 120 s. AWS documents that the raise may lower the account's throttle quota;
  not checked.
- rec-4240 would move point reads onto the slow batch verb: hold it until LAT-03 is fixed.
- Undetermined, and what settles it: the steps inside connect (rec-5042's timers); which tables hold the files
  (the read-only catalog_stats action); the telemetry reader's slow verbs (rec-4706); the effect of threads and
  memory (one measured trial each).
