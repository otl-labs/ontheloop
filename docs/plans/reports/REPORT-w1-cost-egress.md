# REPORT: W1 component 10 - Cost and egress budget (a line register in physical units, a daily attributed ledger and a daily verdict priced at read)

Plan: docs/plans/PLAN-w1-cost-egress.yaml. Fixture item: pwi-loop-cost-egress-budget (1 of 12 cap).
REPORT-ONLY (Decision 86 cl.2): nothing here is built, filed, ratified, flipped, provisioned or paid for.
Every table, block, schedule and verb named below describes a future build. The nine sibling W1 components
(#1384, #1390, #1394-#1400) are unmerged: they are read here as evidence at pinned heads, never cited as
precedent. No IAM, Terraform or spend is touched; no envelope here gates anything.

## 0. Verdict

Decision 88 made catalog egress "a FIRST-CLASS cost budget for the platform, ranked beside compute and
storage" after the Neon free tier's 5 GB/month cap was breached on 2026-06-15, and it said why a budget
needs a reading: "a budget you cannot read is a wish" (clause 2). The telemetry feedback loop adds a new
writer (every agent turn), new readers (session verbs and daily detectors), new files (one per table per
turn) and a new human cost (review items), and nine sibling components have designed it without a line
that says what any of it may cost. Seven of them hand that question here (VP 1). This component answers it
with a register of cost lines kept in physical units, a daily ledger with per-component attribution, and a
daily verdict; dollars are derived when read, as Decision 199 derives cost, never stored.

Measured, not argued:

- The repository has no budget for the loop. The roadmap's cost block calls itself "Not a budget; an
  architectural cost sanity-check"; its five re-evaluation triggers, mirrored by six thresholds in
  config/agent/cost_reconciliation.yaml, watch DeepSeek prices, the Anthropic pool, the S3 and Step
  Functions shares of the bill and the runner, and none of them Neon egress, Lambda, S3 requests or review
  load. The monthly reconciliation's telemetry leg returns None ("until T2.36 lands est_cost_usd"), the
  Decision 88 clause 2 egress figure is still "TBD" in the roadmap, no Terraform resource is a budget or a
  cost alarm, and catalog_stats, the supported measurement path, has no schedule (VP 2).
- The siblings know they cost something and say so without numbers: 7 of 9 reports hand cost or egress to
  this component in their W2 boundary notes; 1 of 9 names egress in its failure_signal or triggers (#1399,
  whose egress is the thing it governs, not a cost); 2 of 9 carry a criterion that names egress (#1390 c3
  measures per-call catalog egress before any consumer depends on a verb; #1399 c1, c2); 0 of 9 declares a
  budget line, an envelope or a price (VP 1).
- A model built from the siblings' own measurements and the deployed shapes (section 2.3) says the loop's
  catalog egress is the first line to breach and that merge cadence, not session count, is its lever. With
  5 sessions a day of 40 turns, reading only the files each query touches, the loop reads about 1.58 GB of
  catalog metadata a month, over the 1 GiB seed envelope, and 83% of it is the daily detectors (each reads
  28 days of files). Under the access pattern Decision 88 documented (the postgres scanner copies
  ducklake_file_column_stats for the whole catalog per query, ducklake #859), the same 5 sessions read
  27.6 GB a month, five times the cap that was breached in June, because the catalog holds a year of
  un-expired files and every query pays for all of them. At 20 sessions a day, dropping merge_ops from
  four windows a day to one raises touched egress from 5.8 GB to 18.5 GB (VP 3).
- Everything else is cheap at list price: Lambda, S3 requests and storage for the loop come to about
  $0.90, $3.54 and $23.33 a month at 5, 20 and 100 sessions a day, before any free tier and excluding
  egress, which has no unit price anywhere in this repository (O5). The review line is the exception that
  is not priced in dollars at all: the ladder's read_all rung reads about 72 review items a day across six
  components (#1398 R1), 2,160 a month against a seed of 900, so the operator's own time is the line that
  breaches at every scale (VP 3, q3).
- The staged verdict SQL reads the register, the ledger and the attribution rows and returns one of ok,
  warn, projected_breach, breach, dark, unattributed or unregistered per line, raising on malformed input.
  A line is a flow (summed over the month) or a stock (its latest reading is the level), and an event line
  projects from its mean over calendar days, not over the days it happened to report; a shared line that
  reports nothing on a day needs no attribution for it. Beside the one label per line it emits two
  independent flags, dark_flag and unattributed_flag, so a line that is breaching cannot hide that it
  stopped reporting or that nobody owns its value; the failure_signal and the top maturity trigger read the
  flags, never the label. It passes 146/146 vectors (37 by raising with the
  stated message), 61 of them written by independent verifiers over four rounds; each of 276 single-site
  mutants (52 named, 224 from a rule sweep over guards and their arms, comparisons, windows, boundaries,
  rounding, aggregates, dispatch, joins, calendar, operands, params, DISTINCT, flags and WHEN order) is
  killed, and 32 more are equivalent and listed apart with their reasons (VP 4).

Staged, not decided: ten register lines (section 2.2) in bytes, counts, GB-seconds and tokens, each with an
owner (a sibling item or shared), a cadence (daily or event), a measure (flow or stock), a seed monthly
envelope and a named source that needs no Terraform and no billed metric; one ledger row per line per day
and attribution rows per component for shared lines; and one daily deterministic verdict (section 2.4)
whose breach action is k1 (recommended: an alarm filed through the existing cost reconciliation path,
Decisions 55 and 62's alarm-not-gate shape, never a stop). Where the budget lives (k2), what unit it is kept
in (k3), how egress is measured (k4), how shared Lambdas are attributed (k5), the item's edge home (k6), how
a stock line is read (k7), which meter feeds the two S3 lines (k8) and the line roster itself (k9) are parked
as asked. Two meter sources a first draft named, S3 request metrics and a per-prefix storage figure, need a
Terraform change and a billed metric, so they are named always-ask inside k8 and not chosen, as is a LIST over
the prefix (priced per request). Nothing is ratified; the seed envelopes are
the model's, not a measurement's (q1).

## 1. Evidence (each row re-derivable; VP step in brackets)

| id | fact | anchor |
|---|---|---|
| e1 | Read at their pinned heads, 7 of 9 sibling reports hand cost or egress to this component in their section 5 boundary notes (#1390, #1394, #1395, #1397, #1398, #1399, #1400); #1384 and #1396 do not [VP 1] | the siblings block; sibling reports section 5 |
| e2 | 1 of 9 sibling failure_signals or maturity triggers names egress (#1399); 2 of 9 items carry a criterion naming egress (#1390 c3, #1399 c1 and c2); 0 of 9 items names a budget, an envelope or a price; 8 of 9 are data-plane only (#1399 is data and control) [VP 1] | sibling fixtures |
| e3 | The roadmap cost_projection block says "Not a budget; an architectural cost sanity-check"; its 5 reevaluation_triggers name DeepSeek, the Anthropic pool, S3 share, the runner and Step Functions share, none egress, Neon, Lambda, catalog, review or telemetry; its neon_catalog_egress line begins "TBD" [VP 2] | docs/ROADMAP-PLATFORM.yaml cost_projection |
| e4 | config/agent/cost_reconciliation.yaml has 6 thresholds mirroring those 5 triggers plus invoice_vs_telemetry_discrepancy_pct 5.0; none names egress, Neon, Lambda or catalog [VP 2] | config/agent/cost_reconciliation.yaml:14 |
| e5 | load_telemetry_cost('2026-09') returns None: the telemetry leg of the monthly reconciliation is a stub "until T2.36 lands est_cost_usd"; no contract under docs/contracts names est_cost_usd, while 2 name cost_usd, which telemetry_observations derives at read (reader_verb:model_call_cost_estimate, no stored column) beside the stored cost_usd_reported [VP 2] | scripts/cost_reconciliation.py:149, docs/contracts/telemetry_observations.yaml:290 |
| e6 | 0 Terraform files declare an AWS budget or cost-anomaly resource, and 0 declare an S3 bucket metrics configuration, inventory or Storage Lens; 0 non-comment Terraform lines name catalog_stats, so the Decision 88 clause 2 measurement path has no schedule; the reconciliation workflow runs monthly ("0 6 4 * *") [VP 2] | terraform/personal, .github/workflows/cost-reconciliation.yml:11 |
| e7 | .claude/settings.json wires 2 hook events (SessionStart, PreToolUse) and no Stop or SessionEnd hook, so no capture producer writes today and the ledger's writer lines read 0 until #1384 lands [VP 2] | .claude/settings.json |
| e8 | In the model, touched-files catalog egress per month is 1,576,200,000 bytes at 5 sessions a day (detector share 0.834), 5,779,200,000 at 20 and 96,595,200,000 at 100; whole-catalog egress is 27,621,000,000, 71,136,000,000 and 371,616,000,000; 20 sessions at one merge window a day reads 18,544,800,000 [VP 3] | section 2.3 |
| e9 | Against the seed envelopes, 3 lines are over at 5 sessions (catalog_egress_bytes, s3_requests, review_items), 5 at 20 (plus writer_requests, lambda_gb_seconds) and 7 at 100 (all but reader_requests); review_items is over at every scale (2,160 a month against 900) [VP 3] | section 2.3 |
| e10 | At list prices (eu-west-2, external, not re-verified) the loop's Lambda, S3 request and S3 storage lines cost about 0.90, 3.54 and 23.33 USD a month at 5, 20 and 100 sessions a day, before any free tier and excluding egress, which has no unit price in the repository [VP 3] | section 2.3; O5 |
| e11 | The staged verdict SQL passes 146 vectors (61 of them written by the verification rounds), 37 by raising 'loop_budget: <rule>' with the first failing rule by name; every flag pinned matches [VP 4] | sections 2.4, 2.5 |
| e12 | Each of 276 mutants of the verdict SQL (52 named, 224 from the rule sweep) replaces one unique site and fails at least one vector; 32 equivalent mutants are listed apart and pass every vector [VP 4] | section 2.6 |
| e13 | Decision 88 clause 1 ranks catalog egress beside compute and storage, with four access-pattern invariants; clause 2 names catalog_stats as the measurement path and defers the figure to a post-deploy measurement; clause 3 cut the DR dump to weekly for egress reasons | docs/DECISIONS.md, Decision 88 |
| e14 | Decision 199 derives state, friction and cost at read (its title); rec-4031's price table is the read-time price source; Decision 206 clause 7 budgets turns and wall-clock, "no dollars", before MVP | docs/DECISIONS.md, Decisions 199 and 206 |
| e15 | #1384 measured about 52 KB of wire and 55 rows per turn, and a 100-turn cold catch-up of 3.2 MB transcripts plus 2.1 MB observations; #1398 counted about 72 reviews a day at read_all across six components; #1397 R5 reads one verb call per pending candidate per run | sibling reports at the pinned heads |
| e16 | Decision 73 halt check: the reader's named verb ci_rca_open returned [] at the start of this thread | Step 0 |
| e17 | The telemetry prefix lives in the shared data_lake bucket; S3 BucketSizeBytes carries no prefix dimension and S3 request metrics need a bucket metrics configuration that AWS bills (AWS documentation as known, not re-fetched here); ducklake_list_files already returns data_file_size_bytes per live file, a catalog read with no Terraform or spend | terraform/personal/main.tf:69, src/common/ducklake_maintenance.py:133 |

Sibling heads read by VP 1 (pinned by sha, so a later push to a sibling branch does not change the reading):

```yaml
siblings:
  - {pr: 1384, item: pwi-capture-producer-wiring, slug: capture-producer-wiring, head: 1da1cc97f3522e5551ab32bcb41e3bb5b93cf685}  # pragma: allowlist secret
  - {pr: 1390, item: pwi-telemetry-reader-verbs, slug: reader-verbs, head: ab7aad7a36819a10069628577a55b2160cf8bd81}  # pragma: allowlist secret
  - {pr: 1394, item: pwi-friction-classifier, slug: friction-classifier, head: c6a47c5df5c1e50c7ab77730dadbe4456965772d}  # pragma: allowlist secret
  - {pr: 1395, item: pwi-deliberation-capture, slug: deliberation-capture, head: 63b1a50a6926561aac5fb5a9c651c6faba581dc5}  # pragma: allowlist secret
  - {pr: 1396, item: pwi-rec-filing-dedupe, slug: rec-filing-dedupe, head: 51958a0ae48a700963fc5b1351fed00e6a1decec}  # pragma: allowlist secret
  - {pr: 1397, item: pwi-back-validation, slug: back-validation, head: 75ad2896d79d7d83f91ddcb619a183972a716351}  # pragma: allowlist secret
  - {pr: 1398, item: pwi-maturity-ladder-controller, slug: maturity-ladder, head: f618343030ac07ffeab13c0ca2288577ad5d22db}  # pragma: allowlist secret
  - {pr: 1399, item: pwi-allow-list-transport, slug: allow-list-transport, head: 03feaf12dda0bdb6ea8718ccdff85e931e83a942}  # pragma: allowlist secret
  - {pr: 1400, item: pwi-goodhart-register, slug: goodhart-register, head: fc298254dd0e4a0661251217f6653c3dede840e2}  # pragma: allowlist secret
```

### 1.1 What the inventory shows

The siblings are consistent about cost in one way: each names it and hands it on. #1390 says Neon egress
per verb is unmeasured and caps its one parameter-scaled read at 7 days; #1394 reads typed facts only and
names a Decision 88 measurement as the switch to materialization; #1395 warns that reasoning tokens are
already inside tokens_output and must not be priced twice; #1397 reads one verb call per candidate; #1398
says review load is the controller's cost; #1399 asks who pays the egress under each transport; #1400 says
drills are recurring cost. None of them could put an envelope on any of it from inside its own item, and
none should have: a budget shared by ten components needs one owner, or every component sets its own and
the sum is nobody's.

The repository's cost surface was built for a different workload. CD.28's monthly reconciliation watches
the executor's inference bill and the big-ticket shares; its triggers are shares of a bill and price
factors, read once a month from an invoice snapshot (e3, e4). The loop's costs are different in kind:
metered egress with a hard cap already breached once, request counts in the hundreds of thousands, and a
human review line. A monthly share-of-bill trigger cannot see a 5 GB egress cap approaching on day 12, and
an invoice cannot be split between the components of one Lambda. So this component does not replace the
reconciliation; it feeds it (k1, k3): the ledger gives the monthly leg the telemetry side it is stubbed
for (e5), and the reconciliation's discrepancy leg is where the ledger is checked against the bill.

The industry practice applied is the FinOps one: allocate before you optimise (every line has an owner;
shared lines carry attribution), budget in the unit the meter counts and price at read (so a price change
re-prices history without rewriting it), alert on forecast rather than on actuals (projected_breach, warn),
and treat a dark meter as a failure in itself (the failure_signal is dark_line_days, not spend). The
anti-pattern it avoids is the one Decision 88 recorded: nothing measured the egress, so the first signal
was the bill.

## 2. Budget design (what this item stages)

### 2.1 What a budget line answers

A line is a meter with an owner. Each register row names: the unit the meter counts (bytes, count,
GB-seconds, tokens), never dollars; the owner, a pilot item or shared; the cadence, daily (a row is
expected every day and its absence is dark) or event (rows arrive only when the thing happens, so no row
is not dark); a seed monthly envelope in the line's unit with a warn share of 0.8; and the source the ledger
row is read from. Dollars are derived when read: a price table (rec-4031's for tokens, list prices for
Lambda and S3, none yet for Neon egress) applied to the day's quantity, as Decision 199 derives cost from
tokens and Decision 206 clause 7 budgets turns and wall-clock rather than dollars. That keeps the public
repository free of measured dollar figures (Decision 101; the reconciliation's public_summary rule), lets a
price change re-price the whole ledger without a rewrite, and makes the free tier's lines (local adapter,
no bill) meaningful as quantities even where no price applies (q2). Pricing at read honours billing_shape
(Decision 206; docs/contracts/telemetry_observations.yaml cost_usd derivation): a row under a
fixed_non_rollover_allowance derives no dollars, so the token line stays in tokens there, and a token line
counts all four classes the contract records (input, output, cache read, cache creation), since on Claude
input excludes cache reads and cache creation is billed at a premium.

A line is also a flow or a stock (its measure). A flow is consumed and summed: bytes read, requests made,
items reviewed; the month-to-date sum is what the envelope bounds. A stock is a level that is read, not
summed: bytes stored. Its latest reading is compared with the envelope, nothing is projected, and a month of
flat readings never adds up to a breach. The cost model treats S3 storage as a stock (a year's accumulation
against the envelope) and the verdict now does the same (k7; a first draft summed it, and a flat 1 GiB
against 20 GiB read breach by the 30th).

Attribution answers "who pays" for a shared line: the writer and reader Lambdas serve every component, so a
shared line's daily value must be explained by attribution rows, one per component, whose sum matches the
day's value within tolerance; a shared line with a value and no attribution reads unattributed (k5). A day on
which a shared line reports 0 has nothing to explain and needs no rows: under k5's per-call stamps a day
with no calls writes none, and it reads ok (z01). Component names are keys: a NULL or blank one raises
(r01, r08), and the build's stamp vocabulary should be canonical, since 'X' and 'x' are two components.

### 2.2 The line register

The register (staged; k2 decides its home). Envelopes are seeds from the model in section 2.3, not
measurements (q1); the first measured month replaces them (c3). Every source below is readable without a
Terraform change, a new credential or a billed metric: Lambda invocation and duration metrics are emitted
free and the dev role reads them (Decision 192); the catalog's own metadata is a reader call; review rows and
egress batches are the loop's own tables. The two S3 sources a first draft named are not (k8, e17).

```yaml
register:
  - {line: catalog_egress_bytes, unit: bytes, owner: shared, cadence: daily, measure: flow, envelope_month: 1073741824, warn_share: 0.8, source: 'catalog_stats bytes per read times reads per day (a proxy, Decision 88 cl.2; the Neon consumption API is k4 (b), always-ask, not chosen)', price_at_read: 'none in this repository (O5); the line stays in bytes'}
  - {line: writer_requests, unit: count, owner: shared, cadence: daily, measure: flow, envelope_month: 30000, warn_share: 0.8, source: 'CloudWatch Invocations on the writer Lambda, emitted free and read with GetMetricStatistics (inside the CloudWatch API free tier; GetMetricData is billed per metric), which the dev role holds since Decision 192', price_at_read: 'Lambda request list price'}
  - {line: writer_bytes, unit: bytes, owner: pwi-capture-producer-wiring, cadence: daily, measure: flow, envelope_month: 2147483648, warn_share: 0.8, source: 'request bytes per writer call summed per day (#1384 measured about 52 KB per turn)', price_at_read: 'unpriced; a volume line that explains writer_requests and s3_storage_bytes'}
  - {line: reader_requests, unit: count, owner: shared, cadence: daily, measure: flow, envelope_month: 30000, warn_share: 0.8, source: 'CloudWatch Invocations on the reader Lambda, emitted free, read with GetMetricStatistics', price_at_read: 'Lambda request list price'}
  - {line: lambda_gb_seconds, unit: GB-s, owner: shared, cadence: daily, measure: flow, envelope_month: 100000, warn_share: 0.8, source: 'CloudWatch Duration times configured memory per function (writer 3008 MB, reader 1024 MB, maintenance 1536 MB), emitted free, read with GetMetricStatistics', price_at_read: 'Lambda duration list price'}
  - {line: s3_requests, unit: count, owner: shared, cadence: daily, measure: flow, envelope_month: 300000, warn_share: 0.8, source: 'PUTs counted by the writer Lambda per call (files written) and GETs counted at the httpfs layer per reader query (several range GETs per Parquet file: the footer and each column chunk, so files scanned would under-count by a structural factor), each on the Lambda log line (k8); S3 request metrics and access logs are Terraform plus spend and are not used', price_at_read: 'S3 request list prices, PUT and GET apart'}
  - {line: s3_storage_bytes, unit: bytes, owner: pwi-capture-producer-wiring, cadence: daily, measure: stock, envelope_month: 21474836480, warn_share: 0.8, source: 'sum of data_file_size_bytes over ducklake_list_files for the four telemetry tables, one catalog read a day charged to catalog_egress_bytes (k8), plus the blob port prefix for transcripts over 64 KiB, which the telemetry stream places outside every DuckLake data path (Decision 199 cl.5; relayed design, unmerged) and the catalog sum therefore misses; bucket-wide BucketSizeBytes read as the ceiling, since it has no prefix dimension', price_at_read: 'S3 storage list price'}
  - {line: review_items, unit: count, owner: pwi-maturity-ladder-controller, cadence: daily, measure: flow, envelope_month: 900, warn_share: 0.8, source: 'rows of the ladder review table per day (#1398; about 72 a day at read_all across six components)', price_at_read: 'operator time; never priced in dollars (q3)'}
  - {line: plane_egress_bytes, unit: bytes, owner: pwi-allow-list-transport, cadence: event, measure: flow, envelope_month: 0, warn_share: 0.8, source: 'bytes of each egress batch, logged in the data plane before sending (#1399 section 2.7 audit row)', price_at_read: 'unpriced; a zero envelope reads any byte as breach until #1399 k1 is answered'}
  - {line: loop_llm_tokens, unit: tokens, owner: shared, cadence: event, measure: flow, envelope_month: 0, warn_share: 0.8, source: 'telemetry_observations tokens_input, tokens_output, tokens_cache_read and tokens_cache_creation for loop-owned producers (all four classes: on Claude input excludes cache reads and cache creation is billed at a premium; reasoning tokens are inside tokens_output, #1395; never add them twice)', price_at_read: 'cost_usd as the contract derives it at read (reader_verb:model_call_cost_estimate, Decision 199): NULL for a billing_shape of fixed_non_rollover_allowance (Decision 206), else cost_usd_reported or the static price table over the four token classes'}
```

Why these ten. The first seven are the meters the deployed shapes expose: the Neon catalog (Decision 88's
budget), the writer and reader Lambdas and the maintenance Lambda (invocations and GB-seconds), and the S3
telemetry prefix (requests and bytes). review_items is the human line every sibling defers to the ladder.
plane_egress_bytes and loop_llm_tokens are event lines with a zero envelope: today no loop component calls
a model (#1394's rules read typed facts; its k3 (b) would change that) and nothing crosses to a control
plane (#1399 k1 is always-ask), so any value on either line is a breach by construction until the operator
sets an envelope. A zero-envelope event line is the register's way of saying "this costs nothing until
someone decides it may" without a gate (v06, v07).

Why the two S3 sources changed (k8). A first draft read s3_requests from S3 request metrics on the telemetry
prefix and s3_storage_bytes from BucketSizeBytes on the prefix. Neither exists without a change this
component may not make: request metrics need a bucket metrics configuration in Terraform and AWS bills them
as custom metrics; BucketSizeBytes has only bucket and storage-class dimensions, and the telemetry prefix
sits in the shared data_lake bucket, so a per-prefix figure needs S3 Inventory or Storage Lens (Terraform
plus spend) or LIST calls (spend) (e17). Both options are named always-ask under k8 and not chosen. The
staged sources count what the loop's own code already knows: the writer knows how many files it put, a
reader query's plan knows which files it scanned, and the catalog knows every live file's size
(ducklake_list_files, already used by the maintenance module). GETs are counted where they are issued, at
the httpfs layer, because a reader query issues several range GETs per Parquet file (the footer, then each
column chunk), so "files scanned" would under-count by a structural factor every month. The staged sources
still under-count what the loop's own code does not do (a LIST, a retry inside the S3 client, a file the
catalog no longer references), which the monthly invoice line calibrates (k3 (c)). The Lambda metrics are
read with GetMetricStatistics, which sits inside the CloudWatch API free tier; GetMetricData is billed per
metric and is not the read path, so c2's "no billed metric" covers the read as well as the meter.

What is not a line. DeepSeek and Anthropic inference for the executor, the self-hosted runner, DynamoDB
and the rest of CD.28's bill stay with the monthly reconciliation; this register covers the loop's own
meters. Transcript blobs over 64 KiB (Decision 199 clause 5) land in the same S3 prefix and are inside
s3_storage_bytes, not a line of their own.

### 2.3 The cost model (VP 3)

The model is deterministic and built from the siblings' measurements and the deployed shapes; it shows
which line breaches first and what moves it, not what the bill will be. Its parameters are data in the
block below so a verifier can change one and re-run.

Shapes read from the repository (read-only; nothing provisioned): four telemetry tables (Decision 199); a
writer Lambda at 3008 MB, a reader at 1024 MB and a maintenance Lambda at 1536 MB; merge_ops every six
hours (four windows a day, raised from daily by the Neon egress work); inlining disabled (CD.34), so every
turn writes one data file per table and the catalog holds one stats row per file per column; no snapshot
expiry on production tables (Decision 88 clause 4 gates GC behind a restore drill), so files accumulate
for the retention period. Measurements read from the siblings: 52 KB of wire and 55 rows per turn (#1384);
72 review items a day (#1398); one verb call per back-validation candidate (#1397). Assumptions, each a
parameter: 40 turns a session; 3 session-verb calls per session (#1390's sessions_window, friction and
deliberation reads); 6 daily detectors over 28-day windows plus 10 back-validation candidates a day; 5,000
bytes of ducklake_file_column_stats per file (about 33 columns per table at roughly 150 bytes per stats
row); Parquet at 0.35 of wire size; 0.5 s per writer call and 1.0 s per reader call; a 365-day retention.

Two egress models, because the measurement does not exist (q1, k4). "touched" charges a query only for the
stats rows of the files it reads: the day's files for a session verb, 28 days for a detector. It is the
floor. "whole_catalog" charges every query the stats rows of every live file, which is the access pattern
Decision 88 clause 1 (D2) recorded: the postgres scanner's sequential COPY of ducklake_file_column_stats per
query (ducklake #859). Whether the scanner still does that after the warm-connection work is exactly what
catalog_stats would measure.

| sessions/day | merge windows/day | egress model | catalog egress bytes/month | detector share | files live at read (today) | lines over seed | list price ex egress, USD/month |
|---|---|---|---|---|---|---|---|
| 5 | 4 | touched | 1,576,200,000 | 0.834 | 116 | 3 | 0.90 |
| 20 | 4 | touched | 5,779,200,000 | 0.352 | 416 | 5 | 3.54 |
| 100 | 4 | touched | 96,595,200,000 | 0.061 | 2,016 | 7 | 23.33 |
| 5 | 4 | whole_catalog | 27,621,000,000 | 0.516 | 116 | 3 | 0.90 |
| 20 | 4 | whole_catalog | 71,136,000,000 | 0.211 | 416 | 5 | 3.54 |
| 100 | 4 | whole_catalog | 371,616,000,000 | 0.051 | 2,016 | 7 | 23.33 |
| 20 | 1 | touched | 18,544,800,000 | 0.222 | 1,604 | 5 | 4.61 |

What it says:

- Catalog egress breaches first at every scale and under both models, and under the Decision 88 pattern
  even the smallest scale reads 27.6 GB a month: a year of un-expired files is 5,824 merged files before
  today's are counted, and every query pays for all of them. The lever is not session count. It is how
  many files are live: merge cadence (one window a day instead of four triples the touched figure at 20
  sessions) and, under whole_catalog, snapshot expiry, which Decision 88 clause 4 keeps gated. That makes
  the GC gate (rec-2113's restore drill) a cost dependency of the loop, named for W2.
- At small scale the detectors, not the sessions, are the egress: 83% at 5 sessions a day under touched,
  because sixteen daily reads each cover 28 days of files. #1397's one-call-per-candidate rule and the
  detectors' windows are the second lever.
- Everything priced in dollars is small: under a dollar a month at 5 sessions, about $23 at 100, before the
  Lambda free tier. The lines that matter are the two without a dollar price: Neon egress (a cap, not a
  rate, with a paid plan already forced once) and review items (the operator's day; 2,160 a month at
  read_all against a seed of 900, so this line is over at every scale and is really the ladder's control
  knob, q3).
- s3_requests is over its seed at 5 sessions because each turn writes four files and each detector reads
  hundreds; at list price that is cents, so the seed is probably low rather than the loop expensive. Seeds
  are seeds (q1).

The model's SQL and parameters:

```yaml
cost_model:
  params:
    scales_sessions_per_day: [5, 20, 100]
    turns_per_session: 40
    wire_bytes_per_turn: 52000
    rows_per_turn: 55
    tables: 4
    requests_per_pass: 4
    merge_windows_per_day: 4
    stats_bytes_per_file: 5000
    per_session_verb_calls: 3
    daily_detectors: 6
    back_validation_candidates: 10
    detector_window_days: 28
    parquet_ratio: 0.35
    retention_days: 365
    writer_seconds: 0.5
    writer_gb: 2.9375
    reader_seconds: 1.0
    reader_gb: 1.0
    review_items_per_day: 72
    days_per_month: 30
  envelopes_month:
    catalog_egress_bytes: 1073741824
    writer_requests: 30000
    writer_bytes: 2147483648
    reader_requests: 30000
    lambda_gb_seconds: 100000
    s3_requests: 300000
    s3_storage_bytes: 21474836480
    review_items: 900
  sql: |
    WITH s AS (SELECT unnest({scales}) AS sessions, unnest({merges}) AS merge_windows, unnest({models}) AS egress_model),
    d AS (
      SELECT sessions, merge_windows, egress_model,
             sessions * {turns_per_session} AS turns,
             sessions * {turns_per_session} * {requests_per_pass} + sessions AS writer_requests,
             sessions * {turns_per_session} * {wire_bytes_per_turn} AS writer_bytes,
             sessions * {turns_per_session} * {rows_per_turn} AS rows_written,
             sessions * {turns_per_session} * {tables} AS files_written,
             {tables} * merge_windows AS files_per_day_merged,
             {tables} * merge_windows + {tables} * sessions * {turns_per_session} / merge_windows / 2.0 AS files_today
      FROM s
    ),
    e AS (
      SELECT *,
             CASE egress_model WHEN 'touched' THEN files_today ELSE files_per_day_merged * ({retention_days} - 1) + files_today END * {stats_bytes_per_file} AS egress_per_session_call,
             CASE egress_model WHEN 'touched' THEN ({detector_window_days} - 1) * files_per_day_merged + files_today ELSE files_per_day_merged * ({retention_days} - 1) + files_today END * {stats_bytes_per_file} AS egress_per_detector_run,
             sessions * {per_session_verb_calls} + {daily_detectors} + {back_validation_candidates} AS reader_requests
      FROM d
    ),
    f AS (
      SELECT *,
             sessions * {per_session_verb_calls} * egress_per_session_call AS egress_sessions,
             ({daily_detectors} + {back_validation_candidates}) * egress_per_detector_run AS egress_detectors,
             files_written + files_per_day_merged AS s3_puts,
             sessions * {per_session_verb_calls} * files_today + ({daily_detectors} + {back_validation_candidates}) * (({detector_window_days} - 1) * files_per_day_merged + files_today) AS s3_gets,
             writer_requests * {writer_seconds} * {writer_gb} + reader_requests * {reader_seconds} * {reader_gb} AS lambda_gb_s
      FROM e
    )
    SELECT sessions, merge_windows, egress_model,
           CAST(round((egress_sessions + egress_detectors) * {days_per_month}) AS BIGINT) AS catalog_egress_bytes,
           round(egress_detectors / (egress_sessions + egress_detectors), 3) AS detector_share,
           writer_requests * {days_per_month} AS writer_requests,
           CAST(writer_bytes AS BIGINT) * {days_per_month} AS writer_bytes,
           reader_requests * {days_per_month} AS reader_requests,
           CAST(round(lambda_gb_s * {days_per_month}) AS BIGINT) AS lambda_gb_seconds,
           CAST(round((s3_puts + s3_gets) * {days_per_month}) AS BIGINT) AS s3_requests,
           CAST(round(s3_puts * {days_per_month}) AS BIGINT) AS s3_puts,
           CAST(round(s3_gets * {days_per_month}) AS BIGINT) AS s3_gets,
           CAST(round(writer_bytes * {parquet_ratio} * {retention_days}) AS BIGINT) AS s3_storage_bytes,
           {review_items_per_day} * {days_per_month} AS review_items,
           CAST(files_today AS INTEGER) AS files_today
    FROM f ORDER BY egress_model DESC, merge_windows DESC, sessions
  prices_external:
    note: list prices, region eu-west-2, external and not re-verified here; applied at read, never stored; the loop's own catalog egress has no public unit price in this repository, so it stays in bytes
    lambda_per_gb_second_usd: 0.0000166667
    lambda_per_million_requests_usd: 0.20
    s3_put_per_thousand_usd: 0.0053
    s3_get_per_thousand_usd: 0.00042
    s3_storage_per_gb_month_usd: 0.024
```

### 2.4 The verdict (staged)

One SQL per daily run. Inputs (all build-time objects; k2): `{budget}`, one row per register line (line,
unit, owner, cadence, measure, envelope, warn_share) with envelope the month's envelope in the line's unit;
`{ledger}`, one row per line per day (line, day, value), value the day's quantity in the line's unit;
`{attribution}`, one row per line per day per component (line, day, component, value). Params are data,
stamped on every verdict record: a rate window of 7 days for the projection and a tolerance of 0.01 for
attribution. The output is one row per line that appears in any of the three tables (VP 4 fails a
repeated row): the line, one verdict label, and two independent flags, dark_flag and unattributed_flag.
The label carries precedence (one action per line, k1); the flags carry the two facts the failure_signal
counts, so a line in breach still shows that it went dark (y01, y03) or that nobody owns its value (y02,
x03), and a fully attributed breach shows neither (y04). Verification r3 found the first draft's single
label hid exactly those two facts on the loop's largest line, which the model puts over its seed at every
scale. The failure_signal source and the spot_check -> anomaly_triggered trigger read the flags.

One rule governs history: the ledger and attribution are read through a 31-day window (after today minus
31 days, up to and including today), guards included, so rows dated after today or older than the window
are never read (v18, v19) and a malformed row outside it neither raises nor decides. The one exception is a
row with a NULL day, which cannot be placed in time, so it raises wherever it is (e09, e10, e18).

Per line, in order of precedence (v13-v16):

- unregistered: a ledger or attribution line with no budget row (v08, v33). It never halts the run: a
  meter that starts reporting before its register row exists is the finding, not an error.
- breach: the line's level exceeds the envelope (v04). For a flow the level is the month-to-date sum,
  rounded to 9 places, starting on the first of the calendar month (v17, v22, n02, n15); for a stock it is
  the latest reading anywhere in the 31-day window (s02, s03, s09, r03, r04: a stale reading over the
  envelope breaches before it reads dark, and a reading from last month is still the level), never a sum
  (s06). On the last day of the month nothing is projected (v21); the days remaining are the calendar's
  (r06 February, r07 a 30-day month).
- dark: a daily-cadence line with no ledger row today (v05, v39); dark_flag says the same whatever the
  label (v13, y01, n01). An event line is never dark (v06, v41). An unregistered line's dark_flag is false,
  and its unattributed_flag reads its parts if it has any (v08, v33, x16): a value nobody registered and
  nobody explains still resets the top trigger's counter, which verification r4 judged the conservative
  reading for a failure_signal; the alternative, gating the flag on registration, is recorded under the
  precedence fork. Dark outranks projection because a meter that stopped is not evidence that spend stopped.
- projected_breach: for a flow, month-to-date plus the mean daily value over the last 7 days times the
  days remaining in the month exceeds the envelope (v03, v20, v42, n16, n17). The mean is over calendar
  days for an event line, whose days without a row are days with nothing to report (p01-p05: one 100-byte
  event projects 100/7 a day, not 100 a day), and over the days that reported for a daily line, whose
  missing days are dark, not zero (d01). A line with no rows in the window projects its month-to-date alone
  (v31, p03). A stock never projects: its level is its projection (s01, s06).
- unattributed: either the line is shared, reports a value above 0 at 9 places today and has no
  attribution parts (v09, s08, z03; x04 at 4e-10 is 0), or its parts' sum differs from today's value by
  more than tolerance times today's value (v11, v12, v26, v27, n11, n12, n26, n27, t01, z02, x05);
  unattributed_flag says the same whatever the label (v15, y02, x03). A line with no row today has no today's value, so
  neither clause can fire (an earlier draft said so with a redundant conjunct; verification r2's F6 made
  it so by construction). A shared line that reports 0 with no parts is ok: there is nothing to explain,
  and under k5's stamps a day without calls writes no rows (z01). An owned line needs no attribution (v40);
  attribution on another day does not count (v28, v43).
- warn: the projection (for a stock, the level) exceeds warn_share times the envelope (v02, v25, v34, s04,
  n19, n24).
- ok otherwise (v01, v10, v30, v32, s01).

Every comparison is strict and read at exactly 9 decimal places (v23-v26, v35-v38, n13, n20-n23, n28-n35,
t01, x04), so an envelope met exactly is ok, a sum that differs from its envelope only in floating-point
noise does not breach, and one unit in the ninth place does. That holds while values stay under about 4e6
in the line's unit, where 9 places sit above DOUBLE resolution; the seed lines are integer bytes and
counts or Lambda GB-seconds near 1e5, so none is affected, and the build should keep integer units or
DECIMAL for any line that is not (plan-critique r1 N3). The rate window and tolerance are read from the
params at every use (x06, x15, p05), never hard-coded. `{today}` is the last closed UTC day, never the
running one: CloudWatch datapoints and per-day sums are incomplete until a day closes, so a run over the
running day would read every daily line as dark or partial (N6).

Malformed input raises instead of deciding (fail loud, Decision 55 by analogy): a blank or duplicate
budget line, an envelope that is NULL, non-finite or negative, a warn share outside (0, 1], a cadence other
than daily or event, a measure other than flow or stock (e19), a NULL day anywhere (e18, n05), a blank or
NULL line name in the ledger or the attribution (e20, e21, g06, g08), a NULL or blank component (r01,
r08), a ledger or attribution value that is NULL, non-finite or negative (g07, g09-g11), a duplicate
(line, day) in the ledger or (line, day, component) in the attribution, a rate window outside 1-31 days
(n07-n09), a tolerance outside [0, 1) (n10, n11). Each guard's NULL arm is pinned on its own (g01-g11), and
a duplicate outside the window is not a duplicate (r02). The message
names the first failing rule in alphabetical order (e17), so a run with several defects is repaired in a
stable order. A zero envelope is legal (v07): it is how an event line says "nothing may happen here yet".
A line name that differs only by space characters is a different line (n30), and "blank" means NULL or
spaces only (DuckDB's trim strips spaces, not tabs); the build's register loader should normalise names
and components before they reach the table. On an event line, attribution rows on a day with no ledger
row are ignored (x01: 1000 tokens attributed against no row read ok), because a missing row has no value to
explain; the alternative, reading the missing row as 0 for event lines, is recorded under the precedence
fork, and the risk is low because both tables come from the same stamps under k5 (a). The event rate divides with true division; DuckDB's `//` happens
to agree for DOUBLE operands, and r05 pins the fraction for an engine where it would not.

What the verdict does not do: it gates nothing. A breach on a line is a reading; what follows is k1. The
verdict reads quantities only; pricing is a separate read over the same ledger, and a price change changes
no verdict.

```yaml
verdict:
  params: {window_days: 7, tolerance: 0.01}
  sql: |
    WITH p AS (
      SELECT {today} AS today, CAST(date_trunc('month', {today}) AS DATE) AS month_start,
             last_day({today}) - {today} AS days_remaining, {window_days} AS window_days, {tolerance} AS tolerance
    ),
    b AS (SELECT * FROM {budget}),
    l AS (SELECT * FROM {ledger} WHERE day IS NULL OR (day > (SELECT today FROM p) - 31 AND day <= (SELECT today FROM p))),
    a AS (SELECT * FROM {attribution} WHERE day IS NULL OR (day > (SELECT today FROM p) - 31 AND day <= (SELECT today FROM p))),
    g AS (
      SELECT 'budget_line' AS rule, count(*) AS n FROM b WHERE line IS NULL OR trim(line) = ''
      UNION ALL SELECT 'duplicate_budget', count(*) - count(DISTINCT line) FROM b
      UNION ALL SELECT 'envelope', count(*) FROM b WHERE envelope IS NULL OR NOT isfinite(envelope) OR envelope < 0
      UNION ALL SELECT 'warn_share', count(*) FROM b WHERE warn_share IS NULL OR NOT isfinite(warn_share) OR warn_share <= 0 OR warn_share > 1
      UNION ALL SELECT 'cadence', count(*) FROM b WHERE cadence IS NULL OR cadence NOT IN ('daily', 'event')
      UNION ALL SELECT 'measure', count(*) FROM b WHERE measure IS NULL OR measure NOT IN ('flow', 'stock')
      UNION ALL SELECT 'null_day', count(*) FROM (SELECT day FROM l UNION ALL SELECT day FROM a) WHERE day IS NULL
      UNION ALL SELECT 'ledger_line', count(*) FROM l WHERE line IS NULL OR trim(line) = ''
      UNION ALL SELECT 'ledger_value', count(*) FROM l WHERE value IS NULL OR NOT isfinite(value) OR value < 0
      UNION ALL SELECT 'duplicate_ledger', count(*) - count(DISTINCT (line, day)) FROM l
      UNION ALL SELECT 'attribution_line', count(*) FROM a WHERE line IS NULL OR trim(line) = ''
      UNION ALL SELECT 'attribution_component', count(*) FROM a WHERE component IS NULL OR trim(component) = ''
      UNION ALL SELECT 'attribution_value', count(*) FROM a WHERE value IS NULL OR NOT isfinite(value) OR value < 0
      UNION ALL SELECT 'duplicate_attribution', count(*) - count(DISTINCT (line, day, component)) FROM a
      UNION ALL SELECT 'window_days', CASE WHEN (SELECT window_days FROM p) BETWEEN 1 AND 31 THEN 0 ELSE 1 END
      UNION ALL SELECT 'tolerance', CASE WHEN (SELECT tolerance FROM p) >= 0 AND (SELECT tolerance FROM p) < 1 THEN 0 ELSE 1 END
    ),
    chk AS (SELECT CASE WHEN EXISTS (SELECT 1 FROM g WHERE n > 0) THEN error('loop_budget: ' || (SELECT rule FROM g WHERE n > 0 ORDER BY rule LIMIT 1)) END AS guard),
    lines AS (SELECT line FROM b UNION SELECT line FROM l UNION SELECT line FROM a),
    m AS (
      SELECT line,
             coalesce(sum(value) FILTER (WHERE day >= (SELECT month_start FROM p)), 0) AS mtd,
             avg(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)) AS day_rate,
             coalesce(sum(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)), 0) / (SELECT window_days FROM p) AS event_rate,
             arg_max(value, day) AS level,
             count(*) FILTER (WHERE day = (SELECT today FROM p)) AS today_rows,
             max(value) FILTER (WHERE day = (SELECT today FROM p)) AS today_value
      FROM l GROUP BY line
    ),
    ab AS (SELECT line, sum(value) AS attributed, count(*) AS parts FROM a WHERE day = (SELECT today FROM p) GROUP BY line),
    v AS (
      SELECT x.line,
             b.envelope, b.warn_share, b.cadence, b.owner,
             CASE WHEN b.measure = 'stock' THEN coalesce(m.level, 0) ELSE coalesce(m.mtd, 0) END AS mtd,
             CASE WHEN b.measure = 'stock' THEN coalesce(m.level, 0)
                  ELSE coalesce(m.mtd, 0) + CASE WHEN b.cadence = 'event' THEN coalesce(m.event_rate, 0) ELSE coalesce(m.day_rate, 0) END * (SELECT days_remaining FROM p) END AS projected,
             coalesce(m.today_rows, 0) AS today_rows,
             m.today_value,
             ab.attributed, coalesce(ab.parts, 0) AS parts
      FROM lines x LEFT JOIN b ON b.line = x.line LEFT JOIN m ON m.line = x.line LEFT JOIN ab ON ab.line = x.line
    ),
    w AS (
      SELECT *,
             coalesce(cadence = 'daily' AND today_rows = 0, false) AS dark_flag,
             coalesce((owner = 'shared' AND parts = 0 AND round(today_value, 9) > 0)
                    OR (parts > 0 AND abs(round(attributed - today_value, 9)) > round((SELECT tolerance FROM p) * today_value, 9)), false) AS unattributed_flag
      FROM v
    )
    SELECT line,
           CASE
             WHEN envelope IS NULL THEN 'unregistered'
             WHEN round(mtd, 9) > envelope THEN 'breach'
             WHEN dark_flag THEN 'dark'
             WHEN round(projected, 9) > envelope THEN 'projected_breach'
             WHEN unattributed_flag THEN 'unattributed'
             WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'
             ELSE 'ok'
           END AS verdict,
           dark_flag, unattributed_flag
    FROM w
    WHERE (SELECT guard FROM chk) IS NULL
    ORDER BY line
```

### 2.5 Vectors (VP 4)

Each vector builds the three tables from day offsets relative to its today (2026-10-15 unless the vector
sets one), applies the params (a vector may override window_days or tolerance), runs the SQL and compares
the full {line: verdict} map (and, where the vector carries flags, the {line: [dark_flag,
unattributed_flag]} map), or the raise message. 108 pass with a verdict and 37 by raising. Groups: the
seven verdicts (v01-v09), attribution (v10-v12, v27-v29, v40, v43, t01), precedence (v13-v16, n18),
history and windows (v17-v22, v31, v42, n02-n06, n15-n17), strict boundaries (v23-v26), independence of
lines (v30), the empty world (v32), a registered line that never reported (n01, the build's day-one state,
e7), stock lines (s01-s09), event-line projection over calendar days (p01-p05) against a daily line's gap
(d01), rounding to exactly 9 places (v35-v38, n13, n19-n24, n28, n29, n31-n35, t01), param edges (n07-n12),
the zero-value shared day (z01-z03), stale and last-month stock readings (r03, r04), the calendar (r06,
r07), the event fraction (r05), the duplicate outside the window (r02), and each guard with its
first-failing-rule order and its NULL arm (e01-e21, g01-g11, r01, r08), the flags under every label
(y01-y04, x03, x16, with flags pinned on v01, v02, v05, v08, v09, v13, v15, v33, v41, s05, z01, n01, x01, so
ok, warn, projected_breach, breach, dark, unattributed and unregistered each carry a pinned flag pair), equal
parts (x05), non-default rate windows (x06, x15) and the event line with orphan attribution (x01). n01-n35
are the 33 vectors zero-context verification round 1 wrote against the first draft, r01-r07 and g01-g11
the 18 round 2 wrote, y01, y02, x01, x03-x06 and x15 the 8 round 3 wrote, and x16 round 4; all pass on the
staged SQL (r01 now by raising since round 2's F7, x04 now ok since the zero-day test is rounded).

```yaml
vectors:
  defaults:
    today: '2026-10-15'
    budget_row: {line: catalog_egress_bytes, unit: bytes, owner: pwi-loop-cost-egress-budget, cadence: daily, measure: flow, envelope: 1000, warn_share: 0.8}
  cases:
  - id: v01-ok-daily-line
    budget:
    - {}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    expected: {catalog_egress_bytes: ok}
    flags:
      catalog_egress_bytes: [false, false]
  - id: v02-warn-on-projection
    budget:
    - {}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 40}
    expected: {catalog_egress_bytes: warn}
    flags:
      catalog_egress_bytes: [false, false]
  - id: v03-projected-breach
    budget:
    - {}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 50}
    expected: {catalog_egress_bytes: projected_breach}
  - id: v04-breach-month-to-date
    budget:
    - {}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 200}
    expected: {catalog_egress_bytes: breach}
  - id: v05-dark-daily-line-no-row-today
    budget:
    - {}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: -1, value: 10}
    expected: {catalog_egress_bytes: dark}
    flags:
      catalog_egress_bytes: [true, false]
  - id: v06-event-line-no-rows-is-ok
    budget:
    - {cadence: event, envelope: 0, line: plane_egress_bytes}
    expected: {plane_egress_bytes: ok}
  - id: v07-event-line-zero-envelope-any-value-breaches
    budget:
    - {cadence: event, envelope: 0, line: plane_egress_bytes}
    ledger:
    - {from: 0, line: plane_egress_bytes, to: 0, value: 1}
    expected: {plane_egress_bytes: breach}
  - id: v08-unregistered-ledger-line
    budget:
    - {}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    - {from: 0, line: mystery_line, to: 0, value: 1}
    expected: {catalog_egress_bytes: ok, mystery_line: unregistered}
    flags:
      catalog_egress_bytes: [false, false]
      mystery_line: [false, false]
  - id: v09-shared-line-needs-attribution
    budget:
    - {line: reader_requests, owner: shared, unit: requests}
    ledger:
    - {from: -6, line: reader_requests, to: 0, value: 10}
    expected: {reader_requests: unattributed}
    flags:
      reader_requests: [false, true]
  - id: v10-shared-line-fully-attributed
    budget:
    - {line: reader_requests, owner: shared, unit: requests}
    ledger:
    - {from: -6, line: reader_requests, to: 0, value: 10}
    attribution:
    - {component: pwi-telemetry-reader-verbs, day: 0, line: reader_requests, value: 6}
    - {component: pwi-back-validation, day: 0, line: reader_requests, value: 4}
    expected: {reader_requests: ok}
  - id: v11-attribution-off-beyond-tolerance
    budget:
    - {envelope: 100000}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 100}
    attribution:
    - {component: pwi-telemetry-reader-verbs, day: 0, line: catalog_egress_bytes, value: 90}
    expected: {catalog_egress_bytes: unattributed}
  - id: v12-attribution-within-tolerance
    budget:
    - {envelope: 100000}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 100}
    attribution:
    - {component: pwi-telemetry-reader-verbs, day: 0, line: catalog_egress_bytes, value: 99.5}
    expected: {catalog_egress_bytes: ok}
  - id: v13-breach-before-dark
    budget:
    - {}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: -1, value: 200}
    expected: {catalog_egress_bytes: breach}
    flags:
      catalog_egress_bytes: [true, false]
  - id: v14-dark-before-projected-breach
    budget:
    - {}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: -1, value: 100}
    expected: {catalog_egress_bytes: dark}
  - id: v15-projected-breach-before-unattributed
    budget:
    - {owner: shared}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 50}
    expected: {catalog_egress_bytes: projected_breach}
    flags:
      catalog_egress_bytes: [false, true]
  - id: v16-unattributed-before-warn
    budget:
    - {owner: shared}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 40}
    expected: {catalog_egress_bytes: unattributed}
  - id: v17-month-to-date-starts-on-the-first
    budget:
    - {envelope: 200}
    ledger:
    - {from: -20, line: catalog_egress_bytes, to: 0, value: 10}
    expected: {catalog_egress_bytes: projected_breach}
  - id: v18-future-rows-ignored
    budget:
    - {}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    - {from: 1, line: catalog_egress_bytes, to: 5, value: 1000}
    expected: {catalog_egress_bytes: ok}
  - id: v19-malformed-row-before-the-guard-window-ignored
    budget:
    - {}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    - {from: -40, line: catalog_egress_bytes, to: -35, value: -5}
    expected: {catalog_egress_bytes: ok}
  - id: v20-rate-reads-the-window-only
    budget:
    - {envelope: 8000}
    ledger:
    - {from: -14, line: catalog_egress_bytes, to: -8, value: 1000}
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    expected: {catalog_egress_bytes: warn}
  - id: v21-last-day-of-month-projects-nothing
    params: {today: '2026-10-31'}
    budget:
    - {envelope: 750}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 100}
    expected: {catalog_egress_bytes: warn}
  - id: v22-first-day-of-month
    params: {today: '2026-10-01'}
    budget:
    - {envelope: 320}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    expected: {catalog_egress_bytes: warn}
  - id: v23-breach-boundary-is-strict
    params: {today: '2026-10-31'}
    budget:
    - {envelope: 700}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 100}
    expected: {catalog_egress_bytes: warn}
  - id: v24-projection-boundary-is-strict
    budget:
    - {envelope: 230}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    expected: {catalog_egress_bytes: warn}
  - id: v25-warn-boundary-is-strict
    budget:
    - {envelope: 287.5}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    expected: {catalog_egress_bytes: ok}
  - id: v26-tolerance-boundary-is-strict
    budget:
    - {envelope: 100000}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 100}
    attribution:
    - {component: pwi-telemetry-reader-verbs, day: 0, line: catalog_egress_bytes, value: 99}
    expected: {catalog_egress_bytes: ok}
  - id: v27-attribution-parts-sum
    budget:
    - {envelope: 100000, owner: shared}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 100}
    attribution:
    - {component: pwi-telemetry-reader-verbs, day: 0, line: catalog_egress_bytes, value: 60}
    - {component: pwi-back-validation, day: 0, line: catalog_egress_bytes, value: 40}
    expected: {catalog_egress_bytes: ok}
  - id: v28-attribution-on-another-day-does-not-count
    budget:
    - {owner: shared}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    attribution:
    - {component: pwi-telemetry-reader-verbs, day: -1, line: catalog_egress_bytes, value: 10}
    expected: {catalog_egress_bytes: unattributed}
  - id: v29-zero-value-attributed-nonzero
    budget:
    - {}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 0}
    attribution:
    - {component: pwi-telemetry-reader-verbs, day: 0, line: catalog_egress_bytes, value: 0.001}
    expected: {catalog_egress_bytes: unattributed}
  - id: v30-two-lines-independent
    budget:
    - {}
    - {envelope: 100, line: writer_bytes}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    - {from: -6, line: writer_bytes, to: 0, value: 50}
    expected: {catalog_egress_bytes: ok, writer_bytes: breach}
  - id: v31-event-line-mtd-with-no-window-rows
    budget:
    - {cadence: event, envelope: 310, line: plane_egress_bytes}
    ledger:
    - {from: -10, line: plane_egress_bytes, to: -8, value: 100}
    expected: {plane_egress_bytes: warn}
  - id: v32-empty-world
    expected: {}
  - id: v33-attribution-only-line-is-unregistered
    budget:
    - {}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    attribution:
    - {component: pwi-back-validation, day: 0, line: ghost_line, value: 1}
    expected: {catalog_egress_bytes: ok, ghost_line: unregistered}
    flags:
      catalog_egress_bytes: [false, false]
      ghost_line: [false, false]
  - id: v34-warn-share-one
    budget:
    - {envelope: 230, warn_share: 1.0}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    expected: {catalog_egress_bytes: ok}
  - id: v35-breach-exact-to-nine-places
    params: {today: '2026-10-31'}
    budget:
    - {envelope: 0.3}
    ledger:
    - {from: -1, line: catalog_egress_bytes, to: -1, value: 0.1}
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 0.2}
    expected: {catalog_egress_bytes: warn}
  - id: v36-projection-exact-to-nine-places
    budget:
    - {envelope: 2.7}
    ledger:
    - {from: -1, line: catalog_egress_bytes, to: -1, value: 0.1}
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 0.2}
    expected: {catalog_egress_bytes: warn}
  - id: v37-attribution-exact-to-nine-places
    params: {tolerance: 0}
    budget:
    - {envelope: 100000}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 0.3}
    attribution:
    - {component: pwi-telemetry-reader-verbs, day: 0, line: catalog_egress_bytes, value: 0.1}
    - {component: pwi-back-validation, day: 0, line: catalog_egress_bytes, value: 0.2}
    expected: {catalog_egress_bytes: ok}
  - id: v38-tolerance-product-exact-to-nine-places
    budget:
    - {envelope: 100000}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 0.7}
    attribution:
    - {component: pwi-telemetry-reader-verbs, day: 0, line: catalog_egress_bytes, value: 0.693}
    expected: {catalog_egress_bytes: ok}
  - id: v39-dark-even-when-month-to-date-known
    budget:
    - {envelope: 100000}
    ledger:
    - {from: -14, line: catalog_egress_bytes, to: -1, value: 10}
    expected: {catalog_egress_bytes: dark}
  - id: v40-owned-line-no-attribution-is-fine
    budget:
    - {envelope: 100000}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 100}
    expected: {catalog_egress_bytes: ok}
  - id: v41-shared-event-line-no-rows-is-ok
    budget:
    - {cadence: event, envelope: 0, line: plane_egress_bytes, owner: shared}
    expected: {plane_egress_bytes: ok}
    flags:
      plane_egress_bytes: [false, false]
  - id: v42-rate-window-edge-is-exclusive
    budget:
    - {envelope: 1500}
    ledger:
    - {from: -7, line: catalog_egress_bytes, to: -7, value: 1000}
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    expected: {catalog_egress_bytes: warn}
  - id: v43-attribution-compares-todays-value-only
    budget:
    - {envelope: 100000}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: -2, value: 10}
    - {from: -1, line: catalog_egress_bytes, to: -1, value: 100}
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 10}
    attribution:
    - {component: pwi-telemetry-reader-verbs, day: 0, line: catalog_egress_bytes, value: 10}
    expected: {catalog_egress_bytes: ok}
  - id: e01-envelope-negative
    budget:
    - {envelope: -0.5}
    expected: error
    raises: 'loop_budget: envelope'
  - id: e02-envelope-nan
    budget:
    - {envelope: .nan}
    expected: error
    raises: 'loop_budget: envelope'
  - id: e03-warn-share-zero
    budget:
    - {warn_share: 0}
    expected: error
    raises: 'loop_budget: warn_share'
  - id: e04-warn-share-above-one
    budget:
    - {warn_share: 1.5}
    expected: error
    raises: 'loop_budget: warn_share'
  - id: e05-cadence-unknown
    budget:
    - {cadence: weekly}
    expected: error
    raises: 'loop_budget: cadence'
  - id: e06-ledger-value-negative
    budget:
    - {}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: -0.5}
    expected: error
    raises: 'loop_budget: ledger_value'
  - id: e07-ledger-value-infinite
    budget:
    - {}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: .inf}
    expected: error
    raises: 'loop_budget: ledger_value'
  - id: e08-duplicate-ledger-day
    budget:
    - {}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 1}
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 2}
    expected: error
    raises: 'loop_budget: duplicate_ledger'
  - id: e09-null-day-in-ledger
    budget:
    - {}
    ledger:
    - {from: null, line: catalog_egress_bytes, to: null, value: 1}
    expected: error
    raises: 'loop_budget: null_day'
  - id: e10-null-day-in-attribution
    budget:
    - {}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 1}
    attribution:
    - {component: pwi-back-validation, day: null, line: catalog_egress_bytes, value: 1}
    expected: error
    raises: 'loop_budget: null_day'
  - id: e11-attribution-value-negative
    budget:
    - {}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 1}
    attribution:
    - {component: pwi-back-validation, day: 0, line: catalog_egress_bytes, value: -0.5}
    expected: error
    raises: 'loop_budget: attribution_value'
  - id: e12-duplicate-attribution
    budget:
    - {}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 1}
    attribution:
    - {component: pwi-back-validation, day: 0, line: catalog_egress_bytes, value: 0.5}
    - {component: pwi-back-validation, day: 0, line: catalog_egress_bytes, value: 0.6}
    expected: error
    raises: 'loop_budget: duplicate_attribution'
  - id: e13-duplicate-budget-line
    budget:
    - {envelope: 1000}
    - {envelope: 2000}
    expected: error
    raises: 'loop_budget: duplicate_budget'
  - id: e14-blank-budget-line
    budget:
    - {line: ' '}
    expected: error
    raises: 'loop_budget: budget_line'
  - id: e15-window-days-zero
    params: {window_days: 0}
    budget:
    - {}
    expected: error
    raises: 'loop_budget: window_days'
  - id: e16-tolerance-one
    params: {tolerance: 1}
    budget:
    - {}
    expected: error
    raises: 'loop_budget: tolerance'
  - id: e17-first-failing-rule-by-name
    budget:
    - {envelope: -1}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 1}
    attribution:
    - {component: pwi-back-validation, day: 0, line: catalog_egress_bytes, value: -1}
    expected: error
    raises: 'loop_budget: attribution_value'
  - id: e18-null-day-raises-even-outside-window
    budget:
    - {}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    - {from: null, line: catalog_egress_bytes, to: null, value: 1}
    expected: error
    raises: 'loop_budget: null_day'
  - id: s01-stock-flat-level-is-ok
    params: {today: '2026-10-30'}
    budget:
    - {envelope: 20, line: s3_storage_bytes, measure: stock}
    ledger:
    - {from: -29, line: s3_storage_bytes, to: 0, value: 1}
    expected: {s3_storage_bytes: ok}
  - id: s02-stock-level-over-envelope-breaches
    budget:
    - {envelope: 20, line: s3_storage_bytes, measure: stock}
    ledger:
    - {from: -6, line: s3_storage_bytes, to: 0, value: 25}
    expected: {s3_storage_bytes: breach}
  - id: s03-stock-latest-reading-decides
    budget:
    - {envelope: 20, line: s3_storage_bytes, measure: stock}
    ledger:
    - {from: -6, line: s3_storage_bytes, to: -1, value: 25}
    - {from: 0, line: s3_storage_bytes, to: 0, value: 10}
    expected: {s3_storage_bytes: ok}
  - id: s04-stock-warn-on-level
    budget:
    - {envelope: 20, line: s3_storage_bytes, measure: stock}
    ledger:
    - {from: -6, line: s3_storage_bytes, to: 0, value: 17}
    expected: {s3_storage_bytes: warn}
  - id: s05-stock-no-row-today-is-dark
    budget:
    - {envelope: 20, line: s3_storage_bytes, measure: stock}
    ledger:
    - {from: -6, line: s3_storage_bytes, to: -1, value: 1}
    expected: {s3_storage_bytes: dark}
    flags:
      s3_storage_bytes: [true, false]
  - id: s06-stock-never-projects-or-sums
    budget:
    - {envelope: 20, line: s3_storage_bytes, measure: stock}
    ledger:
    - {from: -6, line: s3_storage_bytes, to: -6, value: 4}
    - {from: -5, line: s3_storage_bytes, to: -5, value: 5}
    - {from: -4, line: s3_storage_bytes, to: -4, value: 6}
    - {from: -3, line: s3_storage_bytes, to: -3, value: 7}
    - {from: -2, line: s3_storage_bytes, to: -2, value: 8}
    - {from: -1, line: s3_storage_bytes, to: -1, value: 9}
    - {from: 0, line: s3_storage_bytes, to: 0, value: 10}
    expected: {s3_storage_bytes: ok}
  - id: s07-stock-level-outranks-attribution
    budget:
    - {envelope: 20, line: s3_storage_bytes, measure: stock, owner: shared}
    ledger:
    - {from: -6, line: s3_storage_bytes, to: 0, value: 25}
    expected: {s3_storage_bytes: breach}
  - id: s08-stock-shared-level-needs-attribution
    budget:
    - {envelope: 20, line: s3_storage_bytes, measure: stock, owner: shared}
    ledger:
    - {from: -6, line: s3_storage_bytes, to: 0, value: 1}
    expected: {s3_storage_bytes: unattributed}
  - id: p01-event-projection-per-calendar-day
    budget:
    - {cadence: event, envelope: 400, line: plane_egress_bytes}
    ledger:
    - {from: 0, line: plane_egress_bytes, to: 0, value: 100}
    expected: {plane_egress_bytes: warn}
  - id: p02-event-six-days-ago-still-in-rate-window
    budget:
    - {cadence: event, envelope: 400, line: plane_egress_bytes}
    ledger:
    - {from: -6, line: plane_egress_bytes, to: -6, value: 100}
    expected: {plane_egress_bytes: warn}
  - id: p03-event-seven-days-ago-outside-rate-window
    budget:
    - {cadence: event, envelope: 400, line: plane_egress_bytes}
    ledger:
    - {from: -7, line: plane_egress_bytes, to: -7, value: 100}
    expected: {plane_egress_bytes: ok}
  - id: p04-event-rate-sums-the-window
    budget:
    - {cadence: event, envelope: 700, line: plane_egress_bytes}
    ledger:
    - {from: -3, line: plane_egress_bytes, to: -3, value: 100}
    - {from: 0, line: plane_egress_bytes, to: 0, value: 100}
    expected: {plane_egress_bytes: warn}
  - id: p05-event-rate-divides-by-the-window-days-param
    params: {window_days: 2}
    budget:
    - {cadence: event, envelope: 1000, line: plane_egress_bytes}
    ledger:
    - {from: 0, line: plane_egress_bytes, to: 0, value: 100}
    expected: {plane_egress_bytes: warn}
  - id: d01-daily-gap-rate-is-mean-of-present-rows
    budget:
    - {envelope: 2000}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: -6, value: 100}
    - {from: -3, line: catalog_egress_bytes, to: 0, value: 100}
    expected: {catalog_egress_bytes: projected_breach}
  - id: e19-measure-unknown
    budget:
    - {measure: level}
    expected: error
    raises: 'loop_budget: measure'
  - id: e20-null-ledger-line
    budget:
    - {}
    ledger:
    - {from: 0, line: null, to: 0, value: 1}
    expected: error
    raises: 'loop_budget: ledger_line'
  - id: e21-blank-attribution-line
    budget:
    - {}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    attribution:
    - {component: x, day: 0, line: ' ', value: 1}
    expected: error
    raises: 'loop_budget: attribution_line'
  - id: n01-daily-line-never-reported-is-dark
    budget:
    - {}
    expected: {catalog_egress_bytes: dark}
    flags:
      catalog_egress_bytes: [true, false]
  - id: n02-month-start-on-31st-reads-day-minus-30
    params: {today: '2026-10-31'}
    budget:
    - {envelope: 305}
    ledger:
    - {from: -30, line: catalog_egress_bytes, to: 0, value: 10}
    expected: {catalog_egress_bytes: breach}
  - id: n03-malformed-ledger-row-at-day-minus-31-ignored
    budget:
    - {}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    - {from: -31, line: catalog_egress_bytes, to: -31, value: -1}
    expected: {catalog_egress_bytes: ok}
  - id: n04-malformed-attribution-row-at-day-minus-31-ignored
    budget:
    - {}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    attribution:
    - {component: x, day: -31, line: catalog_egress_bytes, value: -1}
    expected: {catalog_egress_bytes: ok}
  - id: n05-malformed-attribution-row-at-day-minus-30-raises
    budget:
    - {}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    attribution:
    - {component: x, day: -30, line: catalog_egress_bytes, value: -1}
    expected: error
    raises: 'loop_budget: attribution_value'
  - id: n06-future-malformed-attribution-ignored
    budget:
    - {}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    attribution:
    - {component: x, day: 1, line: catalog_egress_bytes, value: -1}
    expected: {catalog_egress_bytes: ok}
  - id: n07-window-days-31-legal
    params: {window_days: 31}
    budget:
    - {}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    expected: {catalog_egress_bytes: ok}
  - id: n08-window-days-32-raises
    params: {window_days: 32}
    budget:
    - {}
    expected: error
    raises: 'loop_budget: window_days'
  - id: n09-window-days-1-legal
    params: {window_days: 1}
    budget:
    - {}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    expected: {catalog_egress_bytes: ok}
  - id: n10-tolerance-negative-raises
    params: {tolerance: -0.01}
    budget:
    - {}
    expected: error
    raises: 'loop_budget: tolerance'
  - id: n11-tolerance-0.9-legal
    params: {tolerance: 0.9}
    budget:
    - {envelope: 100000}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 100}
    attribution:
    - {component: x, day: 0, line: catalog_egress_bytes, value: 50}
    expected: {catalog_egress_bytes: ok}
  - id: n12-zero-attribution-part-legal
    budget:
    - {envelope: 100000, owner: shared}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    attribution:
    - {component: x, day: 0, line: catalog_egress_bytes, value: 10}
    - {component: y, day: 0, line: catalog_egress_bytes, value: 0}
    expected: {catalog_egress_bytes: ok}
  - id: n13-breach-by-one-unit-in-ninth-place
    params: {today: '2026-10-31'}
    budget:
    - {envelope: 1}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 1.000000001}
    expected: {catalog_egress_bytes: breach}
  - id: n15-month-start-excludes-last-of-previous-month
    params: {today: '2026-10-01'}
    budget:
    - {envelope: 315}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    expected: {catalog_egress_bytes: warn}
  - id: n16-rate-window-includes-day-minus-6
    budget:
    - {envelope: 1000}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: -6, value: 300}
    - {from: -5, line: catalog_egress_bytes, to: 0, value: 0}
    expected: {catalog_egress_bytes: warn}
  - id: n17-rate-is-mean-not-median
    budget:
    - {envelope: 1000}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: -1, value: 0}
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 280}
    expected: {catalog_egress_bytes: warn}
  - id: n18-unregistered-outranks-attribution-mismatch
    budget:
    - {}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    - {from: 0, line: mystery_line, to: 0, value: 10}
    attribution:
    - {component: x, day: 0, line: mystery_line, value: 5}
    expected: {catalog_egress_bytes: ok, mystery_line: unregistered}
  - id: n19-warn-rounding-both-sides
    params: {today: '2026-10-31'}
    budget:
    - {envelope: 0.5, warn_share: 0.6}
    ledger:
    - {from: -1, line: catalog_egress_bytes, to: -1, value: 0.1}
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 0.2}
    expected: {catalog_egress_bytes: ok}
  - id: n20-rounding-at-nine-not-eight-breach
    params: {today: '2026-10-31'}
    budget:
    - {envelope: 1}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 1.000000004}
    expected: {catalog_egress_bytes: breach}
  - id: n21-rounding-at-nine-not-ten-breach
    params: {today: '2026-10-31'}
    budget:
    - {envelope: 1}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 1.0000000004}
    expected: {catalog_egress_bytes: warn}
  - id: n22-rounding-at-nine-not-eight-projection
    budget:
    - {envelope: 17.000000004}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 1.0000000003}
    expected: {catalog_egress_bytes: projected_breach}
  - id: n23-rounding-at-nine-not-ten-projection
    budget:
    - {envelope: 17}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 1.00000000002}
    expected: {catalog_egress_bytes: warn}
  - id: n24-warn-rhs-nine-places
    budget:
    - {envelope: 1000, warn_share: 0.0170000000004}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 1}
    expected: {catalog_egress_bytes: ok}
  - id: n31-projection-nine-not-eight
    params: {today: '2026-10-30'}
    budget:
    - {envelope: 2}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 1.000000002}
    expected: {catalog_egress_bytes: projected_breach}
  - id: n32-warn-lhs-nine-not-eight
    params: {today: '2026-10-30'}
    budget:
    - {envelope: 4, warn_share: 0.5}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 1.000000002}
    expected: {catalog_egress_bytes: warn}
  - id: n33-warn-lhs-nine-not-ten
    params: {today: '2026-10-30'}
    budget:
    - {envelope: 4, warn_share: 0.5}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 1.0000000002}
    expected: {catalog_egress_bytes: ok}
  - id: n34-warn-rhs-nine-not-ten
    params: {today: '2026-10-30'}
    budget:
    - {envelope: 4, warn_share: 0.4999999999}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 1}
    expected: {catalog_egress_bytes: ok}
  - id: n35-warn-rhs-nine-not-eight
    params: {today: '2026-10-30'}
    budget:
    - {envelope: 4, warn_share: 0.499999999}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 1}
    expected: {catalog_egress_bytes: warn}
  - id: n26-diff-nine-places
    params: {tolerance: 0}
    budget:
    - {envelope: 100000}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 1}
    attribution:
    - {component: x, day: 0, line: catalog_egress_bytes, value: 1.0000000004}
    expected: {catalog_egress_bytes: ok}
  - id: n27-diff-nine-places-hi
    params: {tolerance: 0}
    budget:
    - {envelope: 100000}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 1}
    attribution:
    - {component: x, day: 0, line: catalog_egress_bytes, value: 1.000000004}
    expected: {catalog_egress_bytes: unattributed}
  - id: n28-fractional-overage-integer-envelope
    params: {today: '2026-10-31'}
    budget:
    - {envelope: 1000}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 1000.5}
    expected: {catalog_egress_bytes: breach}
  - id: n29-fractional-projection-integer-envelope
    budget:
    - {envelope: 17}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 1.01}
    expected: {catalog_egress_bytes: projected_breach}
  - id: n30-budget-lines-differing-by-trailing-space
    budget:
    - {}
    - {line: 'catalog_egress_bytes '}
    expected: {catalog_egress_bytes: dark, 'catalog_egress_bytes ': dark}
  - id: s09-stock-latest-high-reading-breaches
    budget:
    - {envelope: 20, line: s3_storage_bytes, measure: stock}
    ledger:
    - {from: -6, line: s3_storage_bytes, to: -1, value: 10}
    - {from: 0, line: s3_storage_bytes, to: 0, value: 25}
    expected: {s3_storage_bytes: breach}
  - id: t01-tolerance-product-rounded-to-nine-places
    params: {tolerance: 2.94e-09}
    budget:
    - {envelope: 100000}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 1}
    attribution:
    - {component: x, day: 0, line: catalog_egress_bytes, value: 1.0000000026}
    expected: {catalog_egress_bytes: ok}
  - id: z01-shared-zero-day-needs-no-attribution
    budget:
    - {envelope: 100000, owner: shared}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 0}
    expected: {catalog_egress_bytes: ok}
    flags:
      catalog_egress_bytes: [false, false]
  - id: r01-null-component-raises
    budget:
    - {envelope: 100000, owner: shared}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    attribution:
    - {component: null, day: 0, line: catalog_egress_bytes, value: 10}
    expected: error
    raises: 'loop_budget: attribution_component'
  - id: r02-duplicate-ledger-outside-window-ignored
    budget:
    - {}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    - {from: -40, line: catalog_egress_bytes, to: -40, value: 1}
    - {from: -40, line: catalog_egress_bytes, to: -40, value: 1}
    expected: {catalog_egress_bytes: ok}
  - id: r03-stock-stale-reading-breaches-before-dark
    budget:
    - {envelope: 20, line: s3_storage_bytes, measure: stock}
    ledger:
    - {from: -10, line: s3_storage_bytes, to: -10, value: 25}
    expected: {s3_storage_bytes: breach}
  - id: r04-stock-reading-from-last-month-is-the-level
    params: {today: '2026-10-02'}
    budget:
    - {cadence: event, envelope: 20, line: s3_storage_bytes, measure: stock}
    ledger:
    - {from: -2, line: s3_storage_bytes, to: -2, value: 17}
    expected: {s3_storage_bytes: warn}
  - id: r05-event-rate-keeps-its-fraction
    budget:
    - {cadence: event, envelope: 2.3, line: plane_egress_bytes}
    ledger:
    - {from: 0, line: plane_egress_bytes, to: 0, value: 1}
    expected: {plane_egress_bytes: projected_breach}
  - id: r06-february-days-remaining
    params: {today: '2027-02-27'}
    budget:
    - {envelope: 280}
    ledger:
    - {from: -26, line: catalog_egress_bytes, to: 0, value: 10}
    expected: {catalog_egress_bytes: warn}
  - id: r07-thirty-day-month-days-remaining
    params: {today: '2026-11-29'}
    budget:
    - {envelope: 300}
    ledger:
    - {from: -28, line: catalog_egress_bytes, to: 0, value: 10}
    expected: {catalog_egress_bytes: warn}
  - id: g01-null-budget-line-raises
    budget:
    - {line: null}
    expected: error
    raises: 'loop_budget: budget_line'
  - id: g02-null-envelope-raises
    budget:
    - {envelope: null}
    expected: error
    raises: 'loop_budget: envelope'
  - id: g03-null-warn-share-raises
    budget:
    - {warn_share: null}
    expected: error
    raises: 'loop_budget: warn_share'
  - id: g04-null-cadence-raises
    budget:
    - {cadence: null}
    expected: error
    raises: 'loop_budget: cadence'
  - id: g05-null-measure-raises
    budget:
    - {measure: null}
    expected: error
    raises: 'loop_budget: measure'
  - id: g06-blank-ledger-line-raises
    budget:
    - {}
    ledger:
    - {from: 0, line: ' ', to: 0, value: 1}
    expected: error
    raises: 'loop_budget: ledger_line'
  - id: g07-null-ledger-value-raises
    budget:
    - {}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: null}
    expected: error
    raises: 'loop_budget: ledger_value'
  - id: g08-null-attribution-line-raises
    budget:
    - {}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 1}
    attribution:
    - {component: x, day: 0, line: null, value: 1}
    expected: error
    raises: 'loop_budget: attribution_line'
  - id: g09-null-attribution-value-raises
    budget:
    - {}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 1}
    attribution:
    - {component: x, day: 0, line: catalog_egress_bytes, value: null}
    expected: error
    raises: 'loop_budget: attribution_value'
  - id: g10-infinite-attribution-value-raises
    budget:
    - {}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 1}
    attribution:
    - {component: x, day: 0, line: catalog_egress_bytes, value: .inf}
    expected: error
    raises: 'loop_budget: attribution_value'
  - id: g11-nan-attribution-value-raises
    budget:
    - {}
    ledger:
    - {from: 0, line: catalog_egress_bytes, to: 0, value: 1}
    attribution:
    - {component: x, day: 0, line: catalog_egress_bytes, value: .nan}
    expected: error
    raises: 'loop_budget: attribution_value'
  - id: z02-shared-zero-day-with-parts-still-checked
    budget:
    - {envelope: 100000, owner: shared}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 0}
    attribution:
    - {component: x, day: 0, line: catalog_egress_bytes, value: 1}
    expected: {catalog_egress_bytes: unattributed}
  - id: z03-shared-tiny-value-needs-attribution
    budget:
    - {envelope: 100000, owner: shared}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 1.0e-09}
    expected: {catalog_egress_bytes: unattributed}
  - id: r08-blank-component-raises
    budget:
    - {envelope: 100000, owner: shared}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    attribution:
    - {component: '  ', day: 0, line: catalog_egress_bytes, value: 10}
    expected: error
    raises: 'loop_budget: attribution_component'
  - id: y01-breach-then-silent-daily-flags-dark
    budget:
    - {envelope: 100}
    ledger:
    - {from: -14, line: catalog_egress_bytes, to: -5, value: 20}
    expected: {catalog_egress_bytes: breach}
    flags:
      catalog_egress_bytes: [true, false]
  - id: y02-shared-breach-flags-unattributed
    budget:
    - {envelope: 100, owner: shared}
    ledger:
    - {from: -14, line: catalog_egress_bytes, to: 0, value: 20}
    expected: {catalog_egress_bytes: breach}
    flags:
      catalog_egress_bytes: [false, true]
  - id: x03-projected-breach-flags-unattributed
    budget:
    - {envelope: 100, owner: shared}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    expected: {catalog_egress_bytes: projected_breach}
    flags:
      catalog_egress_bytes: [false, true]
  - id: y03-shared-breach-then-silent-flags-both
    budget:
    - {envelope: 100, owner: shared}
    ledger:
    - {from: -14, line: catalog_egress_bytes, to: -5, value: 20}
    expected: {catalog_egress_bytes: breach}
    flags:
      catalog_egress_bytes: [true, false]
  - id: y04-shared-breach-attributed-flags-clear
    budget:
    - {envelope: 100, owner: shared}
    ledger:
    - {from: -14, line: catalog_egress_bytes, to: 0, value: 20}
    attribution:
    - {component: x, day: 0, line: catalog_egress_bytes, value: 20}
    expected: {catalog_egress_bytes: breach}
    flags:
      catalog_egress_bytes: [false, false]
  - id: x04-sub-ninth-place-value-is-zero
    budget:
    - {envelope: 100000, owner: shared}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 4.0e-10}
    expected: {catalog_egress_bytes: ok}
    flags:
      catalog_egress_bytes: [false, false]
  - id: x05-two-equal-parts
    budget:
    - {envelope: 100000, owner: shared}
    ledger:
    - {from: -6, line: catalog_egress_bytes, to: 0, value: 10}
    attribution:
    - {component: a, day: 0, line: catalog_egress_bytes, value: 5}
    - {component: b, day: 0, line: catalog_egress_bytes, value: 5}
    expected: {catalog_egress_bytes: ok}
  - id: x06-window-3-daily-rate
    params: {window_days: 3}
    budget:
    - {envelope: 350}
    ledger:
    - {from: -14, line: catalog_egress_bytes, to: -3, value: 1}
    - {from: -2, line: catalog_egress_bytes, to: 0, value: 20}
    expected: {catalog_egress_bytes: projected_breach}
  - id: x15-event-window-2-ignores-day-minus-4
    params: {window_days: 2}
    budget:
    - {cadence: event, envelope: 1000, line: plane_egress_bytes}
    ledger:
    - {from: -4, line: plane_egress_bytes, to: -4, value: 100}
    expected: {plane_egress_bytes: ok}
  - id: x01-event-orphan-attribution-zero-envelope
    budget:
    - {cadence: event, envelope: 0, line: loop_llm_tokens, owner: shared}
    attribution:
    - {component: pwi-friction-classifier, day: 0, line: loop_llm_tokens, value: 1000}
    expected: {loop_llm_tokens: ok}
    flags:
      loop_llm_tokens: [false, false]
  - id: x16-unregistered-line-misattributed
    budget:
    - {}
    ledger:
    - {line: catalog_egress_bytes, from: -6, to: 0, value: 10}
    - {line: ghost_line, from: 0, to: 0, value: 10}
    attribution:
    - {line: ghost_line, day: 0, component: a, value: 3}
    expected: {catalog_egress_bytes: ok, ghost_line: unregistered}
    flags:
      catalog_egress_bytes: [false, false]
      ghost_line: [false, true]
```

### 2.6 Mutants (VP 4)

Each mutant replaces one exact substring of the verdict SQL (it must occur exactly once) and must fail at
least one vector. m01-m53 are named (m10 moved to the equivalents): each verdict comparison made
inclusive, each precedence pair swapped, each window and guard widened or dropped, the attribution rule
relaxed in each direction, each rounding removed, the first-failing-rule order reversed, and the stock and
event legs undone (a stock summed, a stock projected, an event rate over rows, the level read as the
earliest or the largest reading, the event divisor fixed at 7, the measure guard loosened), the zero-day
rule dropped, made inclusive or left unrounded, the component guard narrowed to NULL, and the flags undone
(unattributed_flag forced false, the flags swapped, the dark coalesce dropped, the dark label read from
rows instead of the flag). The sweep is generated by class, after verification rounds 1, 2 and 3 found 47,
17 and 4 killable survivors in classes earlier drafts did not sweep:

- s-guard-*: each of the sixteen guards dropped (always 0);
- s-case-*: every comparison in the verdict CASE flipped to its neighbours;
- s-window-*: each of the six history reads widened by a day (month-to-date, both rate windows, today's
  rows, today's value, today's attribution);
- s-bound-*: boundary tolerance, the verifier's class: the 31-day window read as 30 or 32 or inclusive, on
  the ledger and the attribution; future rows admitted; each param guard edge moved (window 30, 32, 2;
  tolerance strict, negative, half); each value guard edge moved; the tolerance product read against the
  envelope or the attributed sum, or signed; days remaining off by one; an epsilon added to breach and
  projection; warn read on month-to-date and breach on the projection; month start a day early; the rate
  window a day longer or shorter;
- s-round-*: each of the seven rounding sites (the zero-day test included) at 6, 8, 10 and 12 places, and
  floor, ceil and trunc at each of the six comparison sites, and the warn comparison unrounded on both
  sides;
- s-agg-*: each aggregate swapped (month-to-date sum as max, avg, min; the daily rate as sum, max, min,
  median, any_value; the event rate as avg, max, min; the level as the smallest reading; the attributed
  sum as max, avg, min), each coalesce that changes a verdict dropped (today's rows, parts), the line list
  kept with duplicates, the duplicate-budget check trimmed, the last failing rule reported instead of the
  first;
- s-when-*: each WHEN arm moved to each other position of the verdict CASE (the adjacent swaps are the named
  m25-m28);
- s-guard-arm-*: each OR arm of each guard dropped alone (the NULL arms, the blank arms, the non-finite
  arms, each bound);
- s-plumb-*: the guard filter removed, a raise only above one failure, the duplicate check run before the
  window;
- s-dispatch-*: the event rate applied to daily lines or dispatched on measure, the shared literal mis-cased
  or negated;
- s-level-*: the stock level read from this month, today or the rate window only, or as a window or month
  mean;
- s-join-* and s-lines-*: the budget joined inner, the ledger-only or attribution-only lines dropped;
- s-calendar-* and arithmetic: days remaining as 30 or 31 minus the day, month start as today minus 30, a
  28-day history, the event rate cast to an integer, the daily rate rounded;
- s-today-* and the rest: today's rows counted over the rate window, today's value read as the month sum,
  warn skipped on stock lines, the event rate summed over the month, event lines never projecting, dark on
  every daily line, the attribution window closed before today;
- s-operand-*, s-param-*, s-distinct-*: the zero-day rule keyed on rows, the tolerance difference against
  month-to-date, the shared test on owner IS NOT NULL, each rate window and the tolerance hard-coded at
  its use site, and each sum or mean over DISTINCT values (two components with equal counts);
- s-group-*, s-blank-*, s-flag-*: attribution grouped by component, the blank arm without trim, and each
  flag read from the label instead of its condition.

Thirty-two mutants are equivalent and listed apart with their reasons: `parts > 0` as `parts >= 0` (the
attributed sum is NULL when parts is 0); any aggregate of today's value, and count(value) for today's
rows and parts (the duplicate and value guards leave one non-NULL value); count(DISTINCT component) for
parts (the component guard rejects NULL and blank components, so DISTINCT counts what count(*) counts; a
NULL component was the one counterexample before round 2's F7 added that guard); each coalesce whose NULL
compares NULL and so never breaches, warns or projects (month-to-date in m and in v, the stock level, the
event and daily rates), and a missing daily rate read as 1 (a daily line with no row in the rate window has
no row today and reads dark first); min(rule) for the ordered first rule; the unregistered arm moved
anywhere before unattributed (every arm before it compares NULL when envelope and cadence are NULL); the
stock and dark dispatches read as not-flow and not-event (the measure and cadence guards admit only two
values); the warn_share
NOT isfinite arm (DuckDB orders NaN above every value, so the bounds arms catch it); `//` for the event
rate (true division for DOUBLE operands in DuckDB 1.5.4, engine-specific); the parts > 0 conjunct dropped
(the same NULL argument as parts >= 0); dark keyed on today_value IS NULL and unregistered keyed on
cadence IS NULL (the guards leave one non-NULL value per row and both fields per budget row); today's rows
as count(DISTINCT day); the blank arm as a replace of spaces (trim strips spaces only); and month start and
days remaining computed by date arithmetic instead of date_trunc and last_day. Verification r3 fuzzed the
earlier 27 over 600 random worlds with no diffs, and r4 fuzzed 34 against label and flags: the two join-key
rewrites (m or the attribution joined on the budget's line) stopped being equivalent once the flags were
emitted, because an unregistered line's unattributed_flag reads its parts; both now sit in the mutants
block, killed by x16. Two mutant kinds the verifier called
immaterial are not generated: rounding the envelope side of breach and projection (only an envelope with
a fraction below 1e-9 tells them apart, and every register envelope is an integer) and round-half-even
(only an exact binary tie at the tenth place). VP 4 counts the kills and the unique sites and checks that
every equivalent mutant replaces one site and passes every vector.

```yaml
mutants:
- {id: m01-breach-inclusive, old: 'round(mtd, 9) > envelope', new: 'round(mtd, 9) >= envelope'}
- {id: m02-dark-ignores-cadence, new: 'coalesce(today_rows = 0, false) AS dark_flag', old: 'coalesce(cadence = ''daily'' AND today_rows = 0, false) AS dark_flag'}
- {id: m03-projection-inclusive, old: 'round(projected, 9) > envelope', new: 'round(projected, 9) >= envelope'}
- {id: m04-attribution-required-everywhere, new: '(parts = 0 AND round(today_value, 9) > 0)', old: '(owner = ''shared'' AND parts = 0 AND round(today_value, 9) > 0)'}
- {id: m05-tolerance-inclusive, old: 'abs(round(attributed - today_value, 9)) > round((SELECT tolerance FROM p) * today_value, 9)', new: 'abs(round(attributed - today_value, 9)) >= round((SELECT tolerance FROM p) * today_value, 9)'}
- {id: m06-warn-inclusive, old: 'round(projected, 9) > round(warn_share * envelope, 9)', new: 'round(projected, 9) >= round(warn_share * envelope, 9)'}
- {id: m07-mtd-ignores-month-start, old: FILTER (WHERE day >= (SELECT month_start FROM p)), new: FILTER (WHERE day <= (SELECT today FROM p))}
- {id: m08-rate-reads-all-history, old: avg(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)), new: avg(value) FILTER (WHERE day > (SELECT today FROM p) - 31)}
- {id: m09-days-remaining-off-by-one, old: 'last_day({today}) - {today} AS days_remaining', new: 'last_day({today}) - {today} + 1 AS days_remaining'}
- {id: m11-guard-window-widened, old: 'FROM {ledger} WHERE day IS NULL OR (day > (SELECT today FROM p) - 31', new: 'FROM {ledger} WHERE day IS NULL OR (day > (SELECT today FROM p) - 400'}
- {id: m12-future-rows-admitted, old: 'FROM {ledger} WHERE day IS NULL OR (day > (SELECT today FROM p) - 31 AND day <= (SELECT today FROM p))', new: 'FROM {ledger} WHERE day IS NULL OR (day > (SELECT today FROM p) - 31 AND day <= (SELECT today FROM p) + 31)'}
- {id: m13-envelope-guard-loosened, old: OR envelope < 0, new: OR envelope < -1}
- {id: m14-warn-share-guard-loosened, old: OR warn_share > 1, new: OR warn_share > 2}
- {id: m15-cadence-guard-widened, old: 'cadence NOT IN (''daily'', ''event'')', new: 'cadence NOT IN (''daily'', ''event'', ''weekly'')'}
- {id: m16-ledger-value-guard-loosened, old: FROM l WHERE value IS NULL OR NOT isfinite(value) OR value < 0, new: FROM l WHERE value IS NULL OR NOT isfinite(value) OR value < -1}
- {id: m17-attribution-value-guard-loosened, old: FROM a WHERE value IS NULL OR NOT isfinite(value) OR value < 0, new: FROM a WHERE value IS NULL OR NOT isfinite(value) OR value < -1}
- {id: m18-duplicate-ledger-keyed-on-value, old: 'count(DISTINCT (line, day)) FROM l', new: 'count(DISTINCT (line, day, value)) FROM l'}
- {id: m19-duplicate-attribution-keyed-on-value, old: 'count(DISTINCT (line, day, component)) FROM a', new: 'count(DISTINCT (line, day, component, value)) FROM a'}
- {id: m20-duplicate-budget-keyed-on-envelope, old: count(DISTINCT line) FROM b, new: 'count(DISTINCT (line, envelope)) FROM b'}
- {id: m21-null-day-guard-dropped, old: 'SELECT ''null_day'', count(*)', new: 'SELECT ''null_day'', 0 * count(*)'}
- {id: m22-null-day-guard-skips-attribution, old: UNION ALL SELECT day FROM a) WHERE day IS NULL, new: UNION ALL SELECT day FROM l) WHERE day IS NULL}
- {id: m23-unregistered-reads-ok, old: WHEN envelope IS NULL THEN 'unregistered', new: WHEN envelope IS NULL THEN 'ok'}
- {id: m24-attribution-lines-not-listed, old: UNION SELECT line FROM a), new: UNION SELECT line FROM l)}
- {id: m25-dark-before-breach, new: "         WHEN dark_flag THEN 'dark'\n         WHEN round(mtd, 9) > envelope THEN 'breach'", old: "         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'"}
- {id: m26-projection-before-dark, new: "         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN dark_flag THEN 'dark'", old: "         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'"}
- {id: m27-unattributed-before-projection, new: "         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'", old: "         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'"}
- {id: m28-warn-before-unattributed, new: "         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'\n         WHEN unattributed_flag THEN 'unattributed'", old: "         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'"}
- {id: m29-attribution-any-day, old: FROM a WHERE day = (SELECT today FROM p) GROUP BY line, new: FROM a GROUP BY line}
- {id: m30-window-days-guard-admits-zero, old: BETWEEN 1 AND 31 THEN 0, new: BETWEEN 0 AND 31 THEN 0}
- {id: m31-tolerance-guard-admits-one, old: (SELECT tolerance FROM p) < 1 THEN 0, new: (SELECT tolerance FROM p) <= 1 THEN 0}
- {id: m32-first-failing-rule-reversed, old: ORDER BY rule LIMIT 1, new: ORDER BY rule DESC LIMIT 1}
- {id: m33-breach-unrounded, old: 'WHEN round(mtd, 9) > envelope', new: WHEN mtd > envelope}
- {id: m34-projection-unrounded, old: 'WHEN round(projected, 9) > envelope', new: WHEN projected > envelope}
- {id: m35-attribution-difference-unrounded, old: 'abs(round(attributed - today_value, 9))', new: abs(attributed - today_value)}
- {id: m36-tolerance-product-unrounded, old: 'round((SELECT tolerance FROM p) * today_value, 9)', new: (SELECT tolerance FROM p) * today_value}
- {id: m37-dark-when-rows-today, new: 'cadence = ''daily'' AND today_rows > 0, false) AS dark_flag', old: 'cadence = ''daily'' AND today_rows = 0, false) AS dark_flag'}
- {id: m38-shared-attribution-not-required, new: '', old: "(owner = 'shared' AND parts = 0 AND round(today_value, 9) > 0)\n                OR "}
- {id: m39-stock-reads-month-sum, old: 'CASE WHEN b.measure = ''stock'' THEN coalesce(m.level, 0) ELSE coalesce(m.mtd, 0) END AS mtd', new: 'coalesce(m.mtd, 0) AS mtd'}
- {id: m40-stock-projects-like-a-flow, old: "CASE WHEN b.measure = 'stock' THEN coalesce(m.level, 0)\n              ELSE coalesce(m.mtd, 0) +", new: "CASE WHEN b.measure = 'never' THEN coalesce(m.level, 0)\n              ELSE coalesce(m.mtd, 0) +"}
- {id: m41-event-rate-over-rows-present, old: 'CASE WHEN b.cadence = ''event'' THEN coalesce(m.event_rate, 0) ELSE coalesce(m.day_rate, 0) END', new: 'coalesce(m.day_rate, 0)'}
- {id: m42-level-reads-earliest, old: 'arg_max(value, day) AS level', new: 'arg_min(value, day) AS level'}
- {id: m43-level-reads-largest, old: 'arg_max(value, day) AS level', new: max(value) AS level}
- {id: m44-event-rate-divides-by-seven, old: / (SELECT window_days FROM p) AS event_rate, new: / 7 AS event_rate}
- {id: m45-measure-guard-accepts-anything, old: 'measure NOT IN (''flow'', ''stock'')', new: 'measure NOT IN (''flow'', ''stock'', ''level'')'}
- {id: m46-zero-day-rule-dropped, new: (owner = 'shared' AND parts = 0), old: '(owner = ''shared'' AND parts = 0 AND round(today_value, 9) > 0)'}
- {id: m47-zero-day-rule-inclusive, new: '(owner = ''shared'' AND parts = 0 AND round(today_value, 9) >= 0)', old: '(owner = ''shared'' AND parts = 0 AND round(today_value, 9) > 0)'}
- {id: m48-component-guard-null-only, old: WHERE component IS NULL OR trim(component) = '', new: WHERE component IS NULL}
- {id: m49-unattributed-flag-always-false, new: ', false) AND false AS unattributed_flag', old: ', false) AS unattributed_flag'}
- {id: m50-flags-swapped-in-output, new: '       unattributed_flag, dark_flag

    FROM w', old: '       dark_flag, unattributed_flag

    FROM w'}
- {id: m51-dark-flag-coalesce-dropped, new: (cadence = 'daily' AND today_rows = 0) AS dark_flag, old: 'coalesce(cadence = ''daily'' AND today_rows = 0, false) AS dark_flag'}
- {id: m52-dark-label-not-from-flag, new: WHEN today_rows = 0 THEN 'dark', old: WHEN dark_flag THEN 'dark'}
- {id: m53-zero-day-test-unrounded, new: AND parts = 0 AND today_value > 0), old: 'AND parts = 0 AND round(today_value, 9) > 0)'}
- {id: s-guard-budget-line, what: guard budget_line dropped (always 0), old: 'SELECT ''budget_line'' AS rule, count(*) AS n FROM b WHERE line IS NULL OR trim(line) = ''''', new: 'SELECT ''budget_line'' AS rule, 0'}
- {id: s-guard-duplicate-budget, what: guard duplicate_budget dropped (always 0), old: 'SELECT ''duplicate_budget'', count(*) - count(DISTINCT line) FROM b', new: 'SELECT ''duplicate_budget'', 0'}
- {id: s-guard-envelope, what: guard envelope dropped (always 0), old: 'SELECT ''envelope'', count(*) FROM b WHERE envelope IS NULL OR NOT isfinite(envelope) OR envelope < 0', new: 'SELECT ''envelope'', 0'}
- {id: s-guard-warn-share, what: guard warn_share dropped (always 0), old: 'SELECT ''warn_share'', count(*) FROM b WHERE warn_share IS NULL OR NOT isfinite(warn_share) OR warn_share <= 0 OR warn_share > 1', new: 'SELECT ''warn_share'', 0'}
- {id: s-guard-cadence, what: guard cadence dropped (always 0), old: 'SELECT ''cadence'', count(*) FROM b WHERE cadence IS NULL OR cadence NOT IN (''daily'', ''event'')', new: 'SELECT ''cadence'', 0'}
- {id: s-guard-measure, what: guard measure dropped (always 0), old: 'SELECT ''measure'', count(*) FROM b WHERE measure IS NULL OR measure NOT IN (''flow'', ''stock'')', new: 'SELECT ''measure'', 0'}
- {id: s-guard-null-day, what: guard null_day dropped (always 0), old: 'SELECT ''null_day'', count(*) FROM (SELECT day FROM l UNION ALL SELECT day FROM a) WHERE day IS NULL', new: 'SELECT ''null_day'', 0'}
- {id: s-guard-ledger-line, what: guard ledger_line dropped (always 0), old: 'SELECT ''ledger_line'', count(*) FROM l WHERE line IS NULL OR trim(line) = ''''', new: 'SELECT ''ledger_line'', 0'}
- {id: s-guard-ledger-value, what: guard ledger_value dropped (always 0), old: 'SELECT ''ledger_value'', count(*) FROM l WHERE value IS NULL OR NOT isfinite(value) OR value < 0', new: 'SELECT ''ledger_value'', 0'}
- {id: s-guard-duplicate-ledger, what: guard duplicate_ledger dropped (always 0), old: 'SELECT ''duplicate_ledger'', count(*) - count(DISTINCT (line, day)) FROM l', new: 'SELECT ''duplicate_ledger'', 0'}
- {id: s-guard-attribution-line, what: guard attribution_line dropped (always 0), old: 'SELECT ''attribution_line'', count(*) FROM a WHERE line IS NULL OR trim(line) = ''''', new: 'SELECT ''attribution_line'', 0'}
- {id: s-guard-attribution-component, what: guard attribution_component dropped (always 0), old: 'SELECT ''attribution_component'', count(*) FROM a WHERE component IS NULL OR trim(component) = ''''', new: 'SELECT ''attribution_component'', 0'}
- {id: s-guard-attribution-value, what: guard attribution_value dropped (always 0), old: 'SELECT ''attribution_value'', count(*) FROM a WHERE value IS NULL OR NOT isfinite(value) OR value < 0', new: 'SELECT ''attribution_value'', 0'}
- {id: s-guard-duplicate-attribution, what: guard duplicate_attribution dropped (always 0), old: 'SELECT ''duplicate_attribution'', count(*) - count(DISTINCT (line, day, component)) FROM a', new: 'SELECT ''duplicate_attribution'', 0'}
- {id: s-guard-window-days, what: guard window_days dropped (always 0), old: 'SELECT ''window_days'', CASE WHEN (SELECT window_days FROM p) BETWEEN 1 AND 31 THEN 0 ELSE 1 END', new: 'SELECT ''window_days'', 0'}
- {id: s-guard-tolerance, what: guard tolerance dropped (always 0), old: 'SELECT ''tolerance'', CASE WHEN (SELECT tolerance FROM p) >= 0 AND (SELECT tolerance FROM p) < 1 THEN 0 ELSE 1 END', new: 'SELECT ''tolerance'', 0'}
- {id: s-case-01, what: verdict comparison '>' read as '>=' at CASE offset 171, old: " AND parts = 0 AND round(today_value, 9) > 0)\n                OR (parts > 0 AND abs", new: " AND parts = 0 AND round(today_value, 9) >= 0)\n                OR (parts > 0 AND abs"}
- {id: s-case-02, what: verdict comparison '>' read as '<' at CASE offset 171, old: " AND parts = 0 AND round(today_value, 9) > 0)\n                OR (parts > 0 AND abs", new: " AND parts = 0 AND round(today_value, 9) < 0)\n                OR (parts > 0 AND abs"}
- {id: s-case-04, what: verdict comparison '>' read as '<' at CASE offset 202, old: "value, 9) > 0)\n                OR (parts > 0 AND abs(round(attributed - today_value", new: "value, 9) > 0)\n                OR (parts < 0 AND abs(round(attributed - today_value"}
- {id: s-case-05, what: verdict comparison '>' read as '>=' at CASE offset 250, old: ' abs(round(attributed - today_value, 9)) > round((SELECT tolerance FROM p) * today_', new: ' abs(round(attributed - today_value, 9)) >= round((SELECT tolerance FROM p) * today_'}
- {id: s-case-06, what: verdict comparison '>' read as '<' at CASE offset 250, old: ' abs(round(attributed - today_value, 9)) > round((SELECT tolerance FROM p) * today_', new: ' abs(round(attributed - today_value, 9)) < round((SELECT tolerance FROM p) * today_'}
- {id: s-case-07, what: verdict comparison '>' read as '>=' at CASE offset 447, old: "nregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dar", new: "nregistered'\n         WHEN round(mtd, 9) >= envelope THEN 'breach'\n         WHEN dar"}
- {id: s-case-08, what: verdict comparison '>' read as '<' at CASE offset 447, old: "nregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dar", new: "nregistered'\n         WHEN round(mtd, 9) < envelope THEN 'breach'\n         WHEN dar"}
- {id: s-case-09, what: verdict comparison '>' read as '>=' at CASE offset 542, old: "'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n       ", new: "'dark'\n         WHEN round(projected, 9) >= envelope THEN 'projected_breach'\n       "}
- {id: s-case-10, what: verdict comparison '>' read as '<' at CASE offset 542, old: "'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n       ", new: "'dark'\n         WHEN round(projected, 9) < envelope THEN 'projected_breach'\n       "}
- {id: s-case-11, what: verdict comparison '>' read as '>=' at CASE offset 663, old: "buted'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'wa", new: "buted'\n         WHEN round(projected, 9) >= round(warn_share * envelope, 9) THEN 'wa"}
- {id: s-case-12, what: verdict comparison '>' read as '<' at CASE offset 663, old: "buted'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'wa", new: "buted'\n         WHEN round(projected, 9) < round(warn_share * envelope, 9) THEN 'wa"}
- {id: s-case-13, what: verdict comparison '=' read as '<>' at CASE offset 44, old: " (\n  SELECT *,\n         coalesce(cadence = 'daily' AND today_rows = 0, false) AS da", new: " (\n  SELECT *,\n         coalesce(cadence <> 'daily' AND today_rows = 0, false) AS da"}
- {id: s-case-14, what: verdict comparison '=' read as '<>' at CASE offset 69, old: "oalesce(cadence = 'daily' AND today_rows = 0, false) AS dark_flag,\n         coalesc", new: "oalesce(cadence = 'daily' AND today_rows <> 0, false) AS dark_flag,\n         coalesc"}
- {id: s-case-15, what: verdict comparison '=' read as '<>' at CASE offset 120, old: ") AS dark_flag,\n         coalesce((owner = 'shared' AND parts = 0 AND round(today_v", new: ") AS dark_flag,\n         coalesce((owner <> 'shared' AND parts = 0 AND round(today_v"}
- {id: s-case-16, what: verdict comparison '=' read as '<>' at CASE offset 141, old: "    coalesce((owner = 'shared' AND parts = 0 AND round(today_value, 9) > 0)\n       ", new: "    coalesce((owner = 'shared' AND parts <> 0 AND round(today_value, 9) > 0)\n       "}
- {id: s-window-01, what: month-to-date excludes the first of the month, old: day >= (SELECT month_start FROM p), new: day > (SELECT month_start FROM p)}
- {id: s-window-02, what: daily rate window one day wider, old: avg(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)), new: avg(value) FILTER (WHERE day >= (SELECT today FROM p) - (SELECT window_days FROM p))}
- {id: s-window-03, what: event rate window one day wider, old: 'sum(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)), 0)', new: 'sum(value) FILTER (WHERE day >= (SELECT today FROM p) - (SELECT window_days FROM p)), 0)'}
- {id: s-window-04, what: today_rows counts the whole window, old: day = (SELECT today FROM p)) AS today_rows, new: day <= (SELECT today FROM p)) AS today_rows}
- {id: s-window-05, what: today_value reads the whole window, old: day = (SELECT today FROM p)) AS today_value, new: day <= (SELECT today FROM p)) AS today_value}
- {id: s-window-06, what: attribution read over the whole window, old: FROM a WHERE day = (SELECT today FROM p), new: FROM a WHERE day <= (SELECT today FROM p)}
- {id: s-bound-hist-ledger-30, what: 'boundary: hist-ledger-30', old: 'FROM {ledger} WHERE day IS NULL OR (day > (SELECT today FROM p) - 31', new: 'FROM {ledger} WHERE day IS NULL OR (day > (SELECT today FROM p) - 30'}
- {id: s-bound-hist-ledger-32, what: 'boundary: hist-ledger-32', old: 'FROM {ledger} WHERE day IS NULL OR (day > (SELECT today FROM p) - 31', new: 'FROM {ledger} WHERE day IS NULL OR (day > (SELECT today FROM p) - 32'}
- {id: s-bound-hist-ledger-ge31, what: 'boundary: hist-ledger-ge31', old: 'FROM {ledger} WHERE day IS NULL OR (day > (SELECT today FROM p) - 31', new: 'FROM {ledger} WHERE day IS NULL OR (day >= (SELECT today FROM p) - 31'}
- {id: s-bound-hist-attr-30, what: 'boundary: hist-attr-30', old: 'FROM {attribution} WHERE day IS NULL OR (day > (SELECT today FROM p) - 31', new: 'FROM {attribution} WHERE day IS NULL OR (day > (SELECT today FROM p) - 30'}
- {id: s-bound-hist-attr-32, what: 'boundary: hist-attr-32', old: 'FROM {attribution} WHERE day IS NULL OR (day > (SELECT today FROM p) - 31', new: 'FROM {attribution} WHERE day IS NULL OR (day > (SELECT today FROM p) - 32'}
- {id: s-bound-hist-attr-future, what: 'boundary: hist-attr-future', old: 'FROM {attribution} WHERE day IS NULL OR (day > (SELECT today FROM p) - 31 AND day <= (SELECT today FROM p))', new: 'FROM {attribution} WHERE day IS NULL OR (day > (SELECT today FROM p) - 31 AND day <= (SELECT today FROM p) + 31)'}
- {id: s-bound-hist-ledger-future, what: 'boundary: hist-ledger-future', old: 'AND day <= (SELECT today FROM p))),

    a AS', new: 'AND day <= (SELECT today FROM p) + 31)),

    a AS'}
- {id: s-bound-window-days-max-30, what: 'boundary: window-days-max-30', old: BETWEEN 1 AND 31 THEN 0, new: BETWEEN 1 AND 30 THEN 0}
- {id: s-bound-window-days-max-32, what: 'boundary: window-days-max-32', old: BETWEEN 1 AND 31 THEN 0, new: BETWEEN 1 AND 32 THEN 0}
- {id: s-bound-window-days-min-2, what: 'boundary: window-days-min-2', old: BETWEEN 1 AND 31 THEN 0, new: BETWEEN 2 AND 31 THEN 0}
- {id: s-bound-tolerance-guard-gt0, what: 'boundary: tolerance-guard-gt0', old: (SELECT tolerance FROM p) >= 0 AND, new: (SELECT tolerance FROM p) > 0 AND}
- {id: s-bound-tolerance-guard-ge-neg, what: 'boundary: tolerance-guard-ge-neg', old: (SELECT tolerance FROM p) >= 0 AND, new: (SELECT tolerance FROM p) >= -1 AND}
- {id: s-bound-tolerance-guard-lt-half, what: 'boundary: tolerance-guard-lt-half', old: (SELECT tolerance FROM p) < 1 THEN 0, new: (SELECT tolerance FROM p) < 0.5 THEN 0}
- {id: s-bound-envelope-guard-le0, what: 'boundary: envelope-guard-le0', old: OR envelope < 0, new: OR envelope <= 0}
- {id: s-bound-warn-guard-lt0, what: 'boundary: warn-guard-lt0', old: OR warn_share <= 0 OR, new: OR warn_share < 0 OR}
- {id: s-bound-warn-guard-ge1, what: 'boundary: warn-guard-ge1', old: OR warn_share > 1, new: OR warn_share >= 1}
- {id: s-bound-ledger-value-le0, what: 'boundary: ledger-value-le0', old: FROM l WHERE value IS NULL OR NOT isfinite(value) OR value < 0, new: FROM l WHERE value IS NULL OR NOT isfinite(value) OR value <= 0}
- {id: s-bound-attr-value-le0, what: 'boundary: attr-value-le0', old: FROM a WHERE value IS NULL OR NOT isfinite(value) OR value < 0, new: FROM a WHERE value IS NULL OR NOT isfinite(value) OR value <= 0}
- {id: s-bound-tolerance-uses-envelope, what: 'boundary: tolerance-uses-envelope', old: 'round((SELECT tolerance FROM p) * today_value, 9)', new: 'round((SELECT tolerance FROM p) * envelope, 9)'}
- {id: s-bound-tolerance-uses-attributed, what: 'boundary: tolerance-uses-attributed', old: 'round((SELECT tolerance FROM p) * today_value, 9)', new: 'round((SELECT tolerance FROM p) * attributed, 9)'}
- {id: s-bound-tolerance-signed, what: 'boundary: tolerance-signed', old: 'abs(round(attributed - today_value, 9))', new: 'round(attributed - today_value, 9)'}
- {id: s-bound-tolerance-signed-rev, what: 'boundary: tolerance-signed-rev', old: 'abs(round(attributed - today_value, 9))', new: 'round(today_value - attributed, 9)'}
- {id: s-bound-days-remaining-minus1, what: 'boundary: days-remaining-minus1', old: 'last_day({today}) - {today} AS days_remaining', new: 'last_day({today}) - {today} - 1 AS days_remaining'}
- {id: s-bound-breach-envelope-plus-eps, what: 'boundary: breach-envelope-plus-eps', old: 'WHEN round(mtd, 9) > envelope', new: 'WHEN round(mtd, 9) > envelope + 1e-9'}
- {id: s-bound-proj-envelope-plus-eps, what: 'boundary: proj-envelope-plus-eps', old: 'WHEN round(projected, 9) > envelope', new: 'WHEN round(projected, 9) > envelope + 1e-9'}
- {id: s-bound-warn-uses-mtd, what: 'boundary: warn-uses-mtd', old: 'WHEN round(projected, 9) > round(warn_share * envelope, 9)', new: 'WHEN round(mtd, 9) > round(warn_share * envelope, 9)'}
- {id: s-bound-breach-uses-projected, what: 'boundary: breach-uses-projected', old: 'WHEN round(mtd, 9) > envelope', new: 'WHEN round(projected, 9) > envelope'}
- {id: s-bound-month-start-prev-day, what: 'boundary: month-start-prev-day', old: 'CAST(date_trunc(''month'', {today}) AS DATE) AS month_start', new: 'CAST(date_trunc(''month'', {today}) AS DATE) - 1 AS month_start'}
- {id: s-bound-rate-window-plus1, what: 'boundary: rate-window-plus1', old: '{window_days} AS window_days', new: '{window_days} + 1 AS window_days'}
- {id: s-bound-rate-window-minus1, what: 'boundary: rate-window-minus1', old: '{window_days} AS window_days', new: '{window_days} - 1 AS window_days'}
- {id: s-round-breach-6, what: rounding at 6 places instead of 9 (breach), old: 'round(mtd, 9) > envelope', new: 'round(mtd, 6) > envelope'}
- {id: s-round-breach-8, what: rounding at 8 places instead of 9 (breach), old: 'round(mtd, 9) > envelope', new: 'round(mtd, 8) > envelope'}
- {id: s-round-breach-10, what: rounding at 10 places instead of 9 (breach), old: 'round(mtd, 9) > envelope', new: 'round(mtd, 10) > envelope'}
- {id: s-round-breach-12, what: rounding at 12 places instead of 9 (breach), old: 'round(mtd, 9) > envelope', new: 'round(mtd, 12) > envelope'}
- {id: s-round-breach-floor, what: floor instead of round (breach), old: 'round(mtd, 9) > envelope', new: floor(mtd) > envelope}
- {id: s-round-breach-ceil, what: ceil instead of round (breach), old: 'round(mtd, 9) > envelope', new: ceil(mtd) > envelope}
- {id: s-round-breach-trunc, what: trunc instead of round (breach), old: 'round(mtd, 9) > envelope', new: trunc(mtd) > envelope}
- {id: s-round-proj-6, what: rounding at 6 places instead of 9 (proj), old: 'round(projected, 9) > envelope', new: 'round(projected, 6) > envelope'}
- {id: s-round-proj-8, what: rounding at 8 places instead of 9 (proj), old: 'round(projected, 9) > envelope', new: 'round(projected, 8) > envelope'}
- {id: s-round-proj-10, what: rounding at 10 places instead of 9 (proj), old: 'round(projected, 9) > envelope', new: 'round(projected, 10) > envelope'}
- {id: s-round-proj-12, what: rounding at 12 places instead of 9 (proj), old: 'round(projected, 9) > envelope', new: 'round(projected, 12) > envelope'}
- {id: s-round-proj-floor, what: floor instead of round (proj), old: 'round(projected, 9) > envelope', new: floor(projected) > envelope}
- {id: s-round-proj-ceil, what: ceil instead of round (proj), old: 'round(projected, 9) > envelope', new: ceil(projected) > envelope}
- {id: s-round-proj-trunc, what: trunc instead of round (proj), old: 'round(projected, 9) > envelope', new: trunc(projected) > envelope}
- {id: s-round-warn-lhs-6, what: rounding at 6 places instead of 9 (warn-lhs), old: 'round(projected, 9) > round(warn_share', new: 'round(projected, 6) > round(warn_share'}
- {id: s-round-warn-lhs-8, what: rounding at 8 places instead of 9 (warn-lhs), old: 'round(projected, 9) > round(warn_share', new: 'round(projected, 8) > round(warn_share'}
- {id: s-round-warn-lhs-10, what: rounding at 10 places instead of 9 (warn-lhs), old: 'round(projected, 9) > round(warn_share', new: 'round(projected, 10) > round(warn_share'}
- {id: s-round-warn-lhs-12, what: rounding at 12 places instead of 9 (warn-lhs), old: 'round(projected, 9) > round(warn_share', new: 'round(projected, 12) > round(warn_share'}
- {id: s-round-warn-lhs-floor, what: floor instead of round (warn-lhs), old: 'round(projected, 9) > round(warn_share', new: floor(projected) > round(warn_share}
- {id: s-round-warn-lhs-ceil, what: ceil instead of round (warn-lhs), old: 'round(projected, 9) > round(warn_share', new: ceil(projected) > round(warn_share}
- {id: s-round-warn-lhs-trunc, what: trunc instead of round (warn-lhs), old: 'round(projected, 9) > round(warn_share', new: trunc(projected) > round(warn_share}
- {id: s-round-warn-rhs-6, what: rounding at 6 places instead of 9 (warn-rhs), old: 'round(warn_share * envelope, 9)', new: 'round(warn_share * envelope, 6)'}
- {id: s-round-warn-rhs-8, what: rounding at 8 places instead of 9 (warn-rhs), old: 'round(warn_share * envelope, 9)', new: 'round(warn_share * envelope, 8)'}
- {id: s-round-warn-rhs-10, what: rounding at 10 places instead of 9 (warn-rhs), old: 'round(warn_share * envelope, 9)', new: 'round(warn_share * envelope, 10)'}
- {id: s-round-warn-rhs-12, what: rounding at 12 places instead of 9 (warn-rhs), old: 'round(warn_share * envelope, 9)', new: 'round(warn_share * envelope, 12)'}
- {id: s-round-warn-rhs-floor, what: floor instead of round (warn-rhs), old: 'round(warn_share * envelope, 9)', new: floor(warn_share * envelope)}
- {id: s-round-warn-rhs-ceil, what: ceil instead of round (warn-rhs), old: 'round(warn_share * envelope, 9)', new: ceil(warn_share * envelope)}
- {id: s-round-warn-rhs-trunc, what: trunc instead of round (warn-rhs), old: 'round(warn_share * envelope, 9)', new: trunc(warn_share * envelope)}
- {id: s-round-diff-6, what: rounding at 6 places instead of 9 (diff), old: 'round(attributed - today_value, 9)', new: 'round(attributed - today_value, 6)'}
- {id: s-round-diff-8, what: rounding at 8 places instead of 9 (diff), old: 'round(attributed - today_value, 9)', new: 'round(attributed - today_value, 8)'}
- {id: s-round-diff-10, what: rounding at 10 places instead of 9 (diff), old: 'round(attributed - today_value, 9)', new: 'round(attributed - today_value, 10)'}
- {id: s-round-diff-12, what: rounding at 12 places instead of 9 (diff), old: 'round(attributed - today_value, 9)', new: 'round(attributed - today_value, 12)'}
- {id: s-round-diff-floor, what: floor instead of round (diff), old: 'round(attributed - today_value, 9)', new: floor(attributed - today_value)}
- {id: s-round-diff-ceil, what: ceil instead of round (diff), old: 'round(attributed - today_value, 9)', new: ceil(attributed - today_value)}
- {id: s-round-diff-trunc, what: trunc instead of round (diff), old: 'round(attributed - today_value, 9)', new: trunc(attributed - today_value)}
- {id: s-round-tolprod-6, what: rounding at 6 places instead of 9 (tolprod), old: '* today_value, 9))', new: '* today_value, 6))'}
- {id: s-round-tolprod-8, what: rounding at 8 places instead of 9 (tolprod), old: '* today_value, 9))', new: '* today_value, 8))'}
- {id: s-round-tolprod-10, what: rounding at 10 places instead of 9 (tolprod), old: '* today_value, 9))', new: '* today_value, 10))'}
- {id: s-round-tolprod-12, what: rounding at 12 places instead of 9 (tolprod), old: '* today_value, 9))', new: '* today_value, 12))'}
- {id: s-round-zeroday-6, what: rounding at 6 places instead of 9 (zeroday), old: 'round(today_value, 9) > 0)', new: 'round(today_value, 6) > 0)'}
- {id: s-round-zeroday-8, what: rounding at 8 places instead of 9 (zeroday), old: 'round(today_value, 9) > 0)', new: 'round(today_value, 8) > 0)'}
- {id: s-round-zeroday-10, what: rounding at 10 places instead of 9 (zeroday), old: 'round(today_value, 9) > 0)', new: 'round(today_value, 10) > 0)'}
- {id: s-round-zeroday-12, what: rounding at 12 places instead of 9 (zeroday), old: 'round(today_value, 9) > 0)', new: 'round(today_value, 12) > 0)'}
- {id: s-round-zeroday-floor, what: floor instead of round (zeroday), old: 'round(today_value, 9) > 0)', new: floor(today_value) > 0)}
- {id: s-round-zeroday-ceil, what: ceil instead of round (zeroday), old: 'round(today_value, 9) > 0)', new: ceil(today_value) > 0)}
- {id: s-round-zeroday-trunc, what: trunc instead of round (zeroday), old: 'round(today_value, 9) > 0)', new: trunc(today_value) > 0)}
- {id: s-round-warn-unrounded-both, what: warn comparison unrounded on both sides, old: 'round(projected, 9) > round(warn_share * envelope, 9)', new: projected > warn_share * envelope}
- {id: s-agg-mtd-sum-max, what: month-to-date sum read as max, old: 'coalesce(sum(value) FILTER (WHERE day >= (SELECT month_start FROM p)), 0)', new: 'coalesce(max(value) FILTER (WHERE day >= (SELECT month_start FROM p)), 0)'}
- {id: s-agg-mtd-sum-avg, what: month-to-date sum read as avg, old: 'coalesce(sum(value) FILTER (WHERE day >= (SELECT month_start FROM p)), 0)', new: 'coalesce(avg(value) FILTER (WHERE day >= (SELECT month_start FROM p)), 0)'}
- {id: s-agg-mtd-sum-min, what: month-to-date sum read as min, old: 'coalesce(sum(value) FILTER (WHERE day >= (SELECT month_start FROM p)), 0)', new: 'coalesce(min(value) FILTER (WHERE day >= (SELECT month_start FROM p)), 0)'}
- {id: s-agg-day-rate-sum, what: daily rate avg read as sum, old: avg(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)) AS day_rate, new: sum(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)) AS day_rate}
- {id: s-agg-day-rate-max, what: daily rate avg read as max, old: avg(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)) AS day_rate, new: max(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)) AS day_rate}
- {id: s-agg-day-rate-min, what: daily rate avg read as min, old: avg(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)) AS day_rate, new: min(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)) AS day_rate}
- {id: s-agg-day-rate-median, what: daily rate avg read as median, old: avg(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)) AS day_rate, new: median(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)) AS day_rate}
- {id: s-agg-day-rate-any_value, what: daily rate avg read as any_value, old: avg(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)) AS day_rate, new: any_value(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)) AS day_rate}
- {id: s-agg-event-rate-avg, what: event rate sum read as avg, old: 'coalesce(sum(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)), 0) / (SELECT window_days FROM p) AS event_rate', new: 'coalesce(avg(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)), 0) / (SELECT window_days FROM p) AS event_rate'}
- {id: s-agg-event-rate-max, what: event rate sum read as max, old: 'coalesce(sum(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)), 0) / (SELECT window_days FROM p) AS event_rate', new: 'coalesce(max(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)), 0) / (SELECT window_days FROM p) AS event_rate'}
- {id: s-agg-event-rate-min, what: event rate sum read as min, old: 'coalesce(sum(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)), 0) / (SELECT window_days FROM p) AS event_rate', new: 'coalesce(min(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)), 0) / (SELECT window_days FROM p) AS event_rate'}
- {id: s-agg-level-min, what: level read as the smallest reading, old: 'arg_max(value, day) AS level', new: min(value) AS level}
- {id: s-agg-attributed-max, what: attributed sum read as max, old: sum(value) AS attributed, new: max(value) AS attributed}
- {id: s-agg-attributed-avg, what: attributed sum read as avg, old: sum(value) AS attributed, new: avg(value) AS attributed}
- {id: s-agg-attributed-min, what: attributed sum read as min, old: sum(value) AS attributed, new: min(value) AS attributed}
- {id: s-agg-today-rows-coalesce-v, what: 'coalesce dropped on today_rows: a registered line that never reported reads ok instead of dark', old: 'coalesce(m.today_rows, 0) AS today_rows', new: m.today_rows AS today_rows}
- {id: s-agg-parts-coalesce-v, what: 'coalesce dropped on parts: a shared line with no attribution escapes unattributed', old: 'coalesce(ab.parts, 0) AS parts', new: ab.parts AS parts}
- {id: s-agg-lines-union-all, what: 'line list keeps duplicates, so a line reports more than once', old: SELECT line FROM b UNION SELECT line FROM l UNION SELECT line FROM a, new: SELECT line FROM b UNION ALL SELECT line FROM l UNION ALL SELECT line FROM a}
- {id: s-agg-dup-budget-trim, what: duplicate budget check trims the line name, old: count(*) - count(DISTINCT line) FROM b, new: count(*) - count(DISTINCT trim(line)) FROM b}
- {id: s-agg-first-rule-max, what: the last failing rule by name is reported instead of the first, old: (SELECT rule FROM g WHERE n > 0 ORDER BY rule LIMIT 1), new: (SELECT max(rule) FROM g WHERE n > 0)}
- {id: s-when-unregistered-to-5, what: WHEN arm unregistered moved to position 5, old: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'", new: "         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'"}
- {id: s-when-unregistered-to-6, what: WHEN arm unregistered moved to position 6, old: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'", new: "         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'\n         WHEN envelope IS NULL THEN 'unregistered'"}
- {id: s-when-breach-to-3, what: WHEN arm breach moved to position 3, old: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'", new: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'"}
- {id: s-when-breach-to-4, what: WHEN arm breach moved to position 4, old: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'", new: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'"}
- {id: s-when-breach-to-5, what: WHEN arm breach moved to position 5, old: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'", new: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'"}
- {id: s-when-breach-to-6, what: WHEN arm breach moved to position 6, old: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'", new: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'\n         WHEN round(mtd, 9) > envelope THEN 'breach'"}
- {id: s-when-dark-to-1, what: WHEN arm dark moved to position 1, old: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'", new: "         WHEN dark_flag THEN 'dark'\n         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'"}
- {id: s-when-dark-to-4, what: WHEN arm dark moved to position 4, old: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'", new: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'"}
- {id: s-when-dark-to-5, what: WHEN arm dark moved to position 5, old: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'", new: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'"}
- {id: s-when-dark-to-6, what: WHEN arm dark moved to position 6, old: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'", new: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'\n         WHEN dark_flag THEN 'dark'"}
- {id: s-when-projected-breach-to-1, what: WHEN arm projected_breach moved to position 1, old: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'", new: "         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'"}
- {id: s-when-projected-breach-to-2, what: WHEN arm projected_breach moved to position 2, old: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'", new: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'"}
- {id: s-when-projected-breach-to-5, what: WHEN arm projected_breach moved to position 5, old: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'", new: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'"}
- {id: s-when-projected-breach-to-6, what: WHEN arm projected_breach moved to position 6, old: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'", new: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'"}
- {id: s-when-unattributed-to-1, what: WHEN arm unattributed moved to position 1, old: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'", new: "         WHEN unattributed_flag THEN 'unattributed'\n         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'"}
- {id: s-when-unattributed-to-2, what: WHEN arm unattributed moved to position 2, old: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'", new: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'"}
- {id: s-when-unattributed-to-3, what: WHEN arm unattributed moved to position 3, old: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'", new: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'"}
- {id: s-when-unattributed-to-6, what: WHEN arm unattributed moved to position 6, old: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'", new: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'\n         WHEN unattributed_flag THEN 'unattributed'"}
- {id: s-when-warn-to-1, what: WHEN arm warn moved to position 1, old: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'", new: "         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'\n         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'"}
- {id: s-when-warn-to-2, what: WHEN arm warn moved to position 2, old: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'", new: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'"}
- {id: s-when-warn-to-3, what: WHEN arm warn moved to position 3, old: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'", new: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'"}
- {id: s-when-warn-to-4, what: WHEN arm warn moved to position 4, old: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'", new: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'"}
- {id: s-guard-arm-budget-line-1, what: 'guard budget_line: arm ''line IS NULL'' dropped', old: '  SELECT ''budget_line'' AS rule, count(*) AS n FROM b WHERE line IS NULL OR trim(line) = ''''', new: '  SELECT ''budget_line'' AS rule, count(*) AS n FROM b WHERE trim(line) = '''''}
- {id: s-guard-arm-budget-line-2, what: 'guard budget_line: arm ''trim(line) = '''''' dropped', old: '  SELECT ''budget_line'' AS rule, count(*) AS n FROM b WHERE line IS NULL OR trim(line) = ''''', new: '  SELECT ''budget_line'' AS rule, count(*) AS n FROM b WHERE line IS NULL'}
- {id: s-guard-arm-envelope-1, what: 'guard envelope: arm ''envelope IS NULL'' dropped', old: '  UNION ALL SELECT ''envelope'', count(*) FROM b WHERE envelope IS NULL OR NOT isfinite(envelope) OR envelope < 0', new: '  UNION ALL SELECT ''envelope'', count(*) FROM b WHERE NOT isfinite(envelope) OR envelope < 0'}
- {id: s-guard-arm-envelope-2, what: 'guard envelope: arm ''NOT isfinite(envelope)'' dropped', old: '  UNION ALL SELECT ''envelope'', count(*) FROM b WHERE envelope IS NULL OR NOT isfinite(envelope) OR envelope < 0', new: '  UNION ALL SELECT ''envelope'', count(*) FROM b WHERE envelope IS NULL OR envelope < 0'}
- {id: s-guard-arm-envelope-3, what: 'guard envelope: arm ''envelope < 0'' dropped', old: '  UNION ALL SELECT ''envelope'', count(*) FROM b WHERE envelope IS NULL OR NOT isfinite(envelope) OR envelope < 0', new: '  UNION ALL SELECT ''envelope'', count(*) FROM b WHERE envelope IS NULL OR NOT isfinite(envelope)'}
- {id: s-guard-arm-warn-share-1, what: 'guard warn_share: arm ''warn_share IS NULL'' dropped', old: '  UNION ALL SELECT ''warn_share'', count(*) FROM b WHERE warn_share IS NULL OR NOT isfinite(warn_share) OR warn_share <= 0 OR warn_share > 1', new: '  UNION ALL SELECT ''warn_share'', count(*) FROM b WHERE NOT isfinite(warn_share) OR warn_share <= 0 OR warn_share > 1'}
- {id: s-guard-arm-warn-share-3, what: 'guard warn_share: arm ''warn_share <= 0'' dropped', old: '  UNION ALL SELECT ''warn_share'', count(*) FROM b WHERE warn_share IS NULL OR NOT isfinite(warn_share) OR warn_share <= 0 OR warn_share > 1', new: '  UNION ALL SELECT ''warn_share'', count(*) FROM b WHERE warn_share IS NULL OR NOT isfinite(warn_share) OR warn_share > 1'}
- {id: s-guard-arm-warn-share-4, what: 'guard warn_share: arm ''warn_share > 1'' dropped', old: '  UNION ALL SELECT ''warn_share'', count(*) FROM b WHERE warn_share IS NULL OR NOT isfinite(warn_share) OR warn_share <= 0 OR warn_share > 1', new: '  UNION ALL SELECT ''warn_share'', count(*) FROM b WHERE warn_share IS NULL OR NOT isfinite(warn_share) OR warn_share <= 0'}
- {id: s-guard-arm-cadence-1, what: 'guard cadence: arm ''cadence IS NULL'' dropped', old: '  UNION ALL SELECT ''cadence'', count(*) FROM b WHERE cadence IS NULL OR cadence NOT IN (''daily'', ''event'')', new: '  UNION ALL SELECT ''cadence'', count(*) FROM b WHERE cadence NOT IN (''daily'', ''event'')'}
- {id: s-guard-arm-cadence-2, what: 'guard cadence: arm ''cadence NOT IN (''daily'', ''event'')'' dropped', old: '  UNION ALL SELECT ''cadence'', count(*) FROM b WHERE cadence IS NULL OR cadence NOT IN (''daily'', ''event'')', new: '  UNION ALL SELECT ''cadence'', count(*) FROM b WHERE cadence IS NULL'}
- {id: s-guard-arm-measure-1, what: 'guard measure: arm ''measure IS NULL'' dropped', old: '  UNION ALL SELECT ''measure'', count(*) FROM b WHERE measure IS NULL OR measure NOT IN (''flow'', ''stock'')', new: '  UNION ALL SELECT ''measure'', count(*) FROM b WHERE measure NOT IN (''flow'', ''stock'')'}
- {id: s-guard-arm-measure-2, what: 'guard measure: arm ''measure NOT IN (''flow'', ''stock'')'' dropped', old: '  UNION ALL SELECT ''measure'', count(*) FROM b WHERE measure IS NULL OR measure NOT IN (''flow'', ''stock'')', new: '  UNION ALL SELECT ''measure'', count(*) FROM b WHERE measure IS NULL'}
- {id: s-guard-arm-ledger-line-1, what: 'guard ledger_line: arm ''line IS NULL'' dropped', old: '  UNION ALL SELECT ''ledger_line'', count(*) FROM l WHERE line IS NULL OR trim(line) = ''''', new: '  UNION ALL SELECT ''ledger_line'', count(*) FROM l WHERE trim(line) = '''''}
- {id: s-guard-arm-ledger-line-2, what: 'guard ledger_line: arm ''trim(line) = '''''' dropped', old: '  UNION ALL SELECT ''ledger_line'', count(*) FROM l WHERE line IS NULL OR trim(line) = ''''', new: '  UNION ALL SELECT ''ledger_line'', count(*) FROM l WHERE line IS NULL'}
- {id: s-guard-arm-ledger-value-1, what: 'guard ledger_value: arm ''value IS NULL'' dropped', old: '  UNION ALL SELECT ''ledger_value'', count(*) FROM l WHERE value IS NULL OR NOT isfinite(value) OR value < 0', new: '  UNION ALL SELECT ''ledger_value'', count(*) FROM l WHERE NOT isfinite(value) OR value < 0'}
- {id: s-guard-arm-ledger-value-2, what: 'guard ledger_value: arm ''NOT isfinite(value)'' dropped', old: '  UNION ALL SELECT ''ledger_value'', count(*) FROM l WHERE value IS NULL OR NOT isfinite(value) OR value < 0', new: '  UNION ALL SELECT ''ledger_value'', count(*) FROM l WHERE value IS NULL OR value < 0'}
- {id: s-guard-arm-ledger-value-3, what: 'guard ledger_value: arm ''value < 0'' dropped', old: '  UNION ALL SELECT ''ledger_value'', count(*) FROM l WHERE value IS NULL OR NOT isfinite(value) OR value < 0', new: '  UNION ALL SELECT ''ledger_value'', count(*) FROM l WHERE value IS NULL OR NOT isfinite(value)'}
- {id: s-guard-arm-attribution-line-1, what: 'guard attribution_line: arm ''line IS NULL'' dropped', old: '  UNION ALL SELECT ''attribution_line'', count(*) FROM a WHERE line IS NULL OR trim(line) = ''''', new: '  UNION ALL SELECT ''attribution_line'', count(*) FROM a WHERE trim(line) = '''''}
- {id: s-guard-arm-attribution-line-2, what: 'guard attribution_line: arm ''trim(line) = '''''' dropped', old: '  UNION ALL SELECT ''attribution_line'', count(*) FROM a WHERE line IS NULL OR trim(line) = ''''', new: '  UNION ALL SELECT ''attribution_line'', count(*) FROM a WHERE line IS NULL'}
- {id: s-guard-arm-attribution-component-1, what: 'guard attribution_component: arm ''component IS NULL'' dropped', old: '  UNION ALL SELECT ''attribution_component'', count(*) FROM a WHERE component IS NULL OR trim(component) = ''''', new: '  UNION ALL SELECT ''attribution_component'', count(*) FROM a WHERE trim(component) = '''''}
- {id: s-guard-arm-attribution-component-2, what: 'guard attribution_component: arm ''trim(component) = '''''' dropped', old: '  UNION ALL SELECT ''attribution_component'', count(*) FROM a WHERE component IS NULL OR trim(component) = ''''', new: '  UNION ALL SELECT ''attribution_component'', count(*) FROM a WHERE component IS NULL'}
- {id: s-guard-arm-attribution-value-1, what: 'guard attribution_value: arm ''value IS NULL'' dropped', old: '  UNION ALL SELECT ''attribution_value'', count(*) FROM a WHERE value IS NULL OR NOT isfinite(value) OR value < 0', new: '  UNION ALL SELECT ''attribution_value'', count(*) FROM a WHERE NOT isfinite(value) OR value < 0'}
- {id: s-guard-arm-attribution-value-2, what: 'guard attribution_value: arm ''NOT isfinite(value)'' dropped', old: '  UNION ALL SELECT ''attribution_value'', count(*) FROM a WHERE value IS NULL OR NOT isfinite(value) OR value < 0', new: '  UNION ALL SELECT ''attribution_value'', count(*) FROM a WHERE value IS NULL OR value < 0'}
- {id: s-guard-arm-attribution-value-3, what: 'guard attribution_value: arm ''value < 0'' dropped', old: '  UNION ALL SELECT ''attribution_value'', count(*) FROM a WHERE value IS NULL OR NOT isfinite(value) OR value < 0', new: '  UNION ALL SELECT ''attribution_value'', count(*) FROM a WHERE value IS NULL OR NOT isfinite(value)'}
- {id: s-plumb-guard-filter-removed, what: the guard filter on the final SELECT removed, old: 'WHERE (SELECT guard FROM chk) IS NULL

    ', new: ''}
- {id: s-plumb-raise-only-above-one, what: the run raises only when a rule fails more than once, old: WHERE n > 0) THEN error, new: WHERE n > 1) THEN error}
- {id: s-plumb-dup-ledger-pre-window, what: 'duplicate_ledger counted over the raw ledger, not the window', old: 'count(*) - count(DISTINCT (line, day)) FROM l', new: 'count(*) - count(DISTINCT (line, day)) FROM {ledger}'}
- {id: s-operand-zero-day-on-rows, what: zero-day rule keyed on today's rows instead of its value, old: 'AND parts = 0 AND round(today_value, 9) > 0)', new: AND parts = 0 AND today_rows > 0)}
- {id: s-operand-diff-against-mtd, what: tolerance difference taken against month-to-date, old: 'abs(round(attributed - today_value, 9))', new: 'abs(round(attributed - mtd, 9))'}
- {id: s-operand-shared-on-owner-not-null, what: shared test read as owner IS NOT NULL, old: (owner = 'shared' AND parts = 0, new: (owner IS NOT NULL AND parts = 0}
- {id: s-param-day-rate-window-seven, what: daily rate window hard-coded to 7, old: avg(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)), new: avg(value) FILTER (WHERE day > (SELECT today FROM p) - 7)}
- {id: s-param-event-rate-window-seven, what: event rate window hard-coded to 7, old: 'coalesce(sum(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)), 0) /', new: 'coalesce(sum(value) FILTER (WHERE day > (SELECT today FROM p) - 7), 0) /'}
- {id: s-param-tolerance-hard-coded, what: tolerance hard-coded to 0.01, old: 'round((SELECT tolerance FROM p) * today_value, 9)', new: 'round(0.01 * today_value, 9)'}
- {id: s-distinct-attributed, what: attributed sum over DISTINCT values, old: sum(value) AS attributed, new: sum(DISTINCT value) AS attributed}
- {id: s-distinct-mtd, what: month-to-date sum over DISTINCT values, old: 'coalesce(sum(value) FILTER (WHERE day >= (SELECT month_start FROM p)), 0) AS mtd', new: 'coalesce(sum(DISTINCT value) FILTER (WHERE day >= (SELECT month_start FROM p)), 0) AS mtd'}
- {id: s-distinct-day-rate, what: daily rate over DISTINCT values, old: avg(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)) AS day_rate, new: avg(DISTINCT value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)) AS day_rate}
- {id: s-distinct-event-rate, what: event rate over DISTINCT values, old: 'coalesce(sum(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)), 0) / (SELECT window_days FROM p) AS event_rate', new: 'coalesce(sum(DISTINCT value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)), 0) / (SELECT window_days FROM p) AS event_rate'}
- {id: s-join-m-on-budget-line, what: 'm joined on b.line: the label still reads unregistered, but a misattributed unregistered line loses its unattributed_flag', old: LEFT JOIN m ON m.line = x.line, new: LEFT JOIN m ON m.line = b.line}
- {id: s-group-attribution-by-component, what: 'attribution grouped by line and component, so a line reports once per part', old: count(*) AS parts FROM a WHERE day = (SELECT today FROM p) GROUP BY line, new: 'count(*) AS parts FROM a WHERE day = (SELECT today FROM p) GROUP BY line, component'}
- {id: s-blank-component-without-trim, what: component blank arm without trim, old: component IS NULL OR trim(component) = '', new: component IS NULL OR component = ''}
- {id: s-flag-unattributed-from-label, what: unattributed_flag read from the label instead of the condition, old: '       dark_flag, unattributed_flag

    FROM w', new: '       dark_flag, verdict = ''unattributed'' AS unattributed_flag

    FROM w'}
- {id: s-flag-dark-from-label, what: dark_flag read from the label instead of the condition, old: '       dark_flag, unattributed_flag

    FROM w', new: '       verdict = ''dark'' AS dark_flag, unattributed_flag

    FROM w'}
- {id: s-dispatch-event-rate-on-daily, what: event rate applied to daily lines, old: 'WHEN b.cadence = ''event'' THEN coalesce(m.event_rate, 0)', new: 'WHEN b.cadence = ''daily'' THEN coalesce(m.event_rate, 0)'}
- {id: s-dispatch-event-rate-on-measure, what: event rate dispatched on measure instead of cadence, old: 'WHEN b.cadence = ''event'' THEN coalesce(m.event_rate, 0)', new: 'WHEN b.measure = ''flow'' THEN coalesce(m.event_rate, 0)'}
- {id: s-dispatch-shared-literal-case, what: the shared owner literal mis-cased, old: '(owner = ''shared'' AND parts = 0 AND round(today_value, 9) > 0)', new: '(owner = ''Shared'' AND parts = 0 AND round(today_value, 9) > 0)'}
- {id: s-dispatch-shared-negated, what: the shared test negated, old: '(owner = ''shared'' AND parts = 0 AND round(today_value, 9) > 0)', new: '(owner <> ''shared'' AND parts = 0 AND round(today_value, 9) > 0)'}
- {id: s-level-this-month-only, what: stock level read from this month only, old: 'arg_max(value, day) AS level', new: 'arg_max(value, day) FILTER (WHERE day >= (SELECT month_start FROM p)) AS level'}
- {id: s-level-today-only, what: stock level read from today only, old: 'arg_max(value, day) AS level', new: max(value) FILTER (WHERE day = (SELECT today FROM p)) AS level}
- {id: s-level-rate-window-only, what: stock level read from the rate window only, old: 'arg_max(value, day) AS level', new: 'arg_max(value, day) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)) AS level'}
- {id: s-level-window-mean, what: stock level read as the window mean, old: 'arg_max(value, day) AS level', new: avg(value) AS level}
- {id: s-level-month-mean, what: stock level read as the month mean, old: 'arg_max(value, day) AS level', new: avg(value) FILTER (WHERE day >= (SELECT month_start FROM p)) AS level}
- {id: s-join-budget-inner, what: 'budget joined inner, dropping unregistered lines', old: FROM lines x LEFT JOIN b ON, new: FROM lines x JOIN b ON}
- {id: s-lines-drop-attribution, what: line list ignores attribution-only lines, old: UNION SELECT line FROM l UNION SELECT line FROM a), new: UNION SELECT line FROM l)}
- {id: s-lines-drop-ledger, what: line list ignores ledger-only lines, old: SELECT line FROM b UNION SELECT line FROM l UNION, new: SELECT line FROM b UNION}
- {id: s-join-attribution-on-budget-line, what: 'attribution joined on b.line instead of x.line: the label still reads unregistered, but a misattributed unregistered line loses its unattributed_flag', old: LEFT JOIN ab ON ab.line = x.line, new: LEFT JOIN ab ON ab.line = b.line}
- {id: s-event-rate-cast-bigint, what: event rate cast to BIGINT, old: 'coalesce(sum(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)), 0) / (SELECT window_days FROM p) AS event_rate', new: 'CAST(coalesce(sum(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)), 0) / (SELECT window_days FROM p) AS BIGINT) AS event_rate'}
- {id: s-day-rate-rounded, what: daily rate rounded to an integer, old: avg(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)) AS day_rate, new: round(avg(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p))) AS day_rate}
- {id: s-calendar-thirty-day-month, what: days remaining as 30 minus the day of month, old: 'last_day({today}) - {today} AS days_remaining', new: '30 - day({today}) AS days_remaining'}
- {id: s-calendar-thirty-one-day-month, what: days remaining as 31 minus the day of month, old: 'last_day({today}) - {today} AS days_remaining', new: '31 - day({today}) AS days_remaining'}
- {id: s-calendar-month-start-today-minus-30, what: month start as today minus 30, old: 'CAST(date_trunc(''month'', {today}) AS DATE) AS month_start', new: '{today} - 30 AS month_start'}
- {id: s-calendar-history-28-days, what: history window 28 days, old: 'FROM {ledger} WHERE day IS NULL OR (day > (SELECT today FROM p) - 31', new: 'FROM {ledger} WHERE day IS NULL OR (day > (SELECT today FROM p) - 28'}
- {id: s-today-rows-over-rate-window, what: today_rows counted over the rate window, old: count(*) FILTER (WHERE day = (SELECT today FROM p)) AS today_rows, new: count(*) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)) AS today_rows}
- {id: s-today-value-from-mtd, what: today_value read as the month-to-date sum, old: max(value) FILTER (WHERE day = (SELECT today FROM p)) AS today_value, new: sum(value) FILTER (WHERE day >= (SELECT month_start FROM p)) AS today_value}
- {id: s-warn-skips-stock, what: warn skipped when level equals projection (stock lines), old: 'WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN ''warn''', new: 'WHEN round(projected, 9) > round(warn_share * envelope, 9) AND mtd <> projected THEN ''warn'''}
- {id: s-event-rate-over-month, what: event rate summed over the month instead of the rate window, old: 'coalesce(sum(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)), 0) / (SELECT window_days FROM p) AS event_rate', new: 'coalesce(sum(value) FILTER (WHERE day >= (SELECT month_start FROM p)), 0) / (SELECT window_days FROM p) AS event_rate'}
- {id: s-event-never-projects, what: event lines never project, old: 'THEN coalesce(m.event_rate, 0) ELSE', new: THEN 0 ELSE}
- {id: s-dark-drops-today-rows, what: dark fires on every daily line, old: 'coalesce(cadence = ''daily'' AND today_rows = 0, false) AS dark_flag', new: 'coalesce(cadence = ''daily'', false) AS dark_flag'}
- {id: s-attribution-window-excludes-today, what: attribution window excludes today, old: 'FROM {attribution} WHERE day IS NULL OR (day > (SELECT today FROM p) - 31 AND day <= (SELECT today FROM p))', new: 'FROM {attribution} WHERE day IS NULL OR (day > (SELECT today FROM p) - 31 AND day < (SELECT today FROM p))'}
```

```yaml
equivalent:
- {id: q-parts-ge-zero, what: 'parts > 0 read as parts >= 0 in the tolerance clause: when parts is 0 the attributed sum is NULL, so the comparison is NULL and the branch cannot fire either way', old: "value, 9) > 0)\n                OR (parts > 0 AND abs(round(attributed - today_value", new: "value, 9) > 0)\n                OR (parts >= 0 AND abs(round(attributed - today_value"}
- {id: q-today-value-min, what: 'today_value max read as min: duplicate_ledger guarantees one row per line per day, so every aggregate of one value is that value', old: max(value) FILTER (WHERE day = (SELECT today FROM p)) AS today_value, new: min(value) FILTER (WHERE day = (SELECT today FROM p)) AS today_value}
- {id: q-today-value-sum, what: 'today_value max read as sum: duplicate_ledger guarantees one row per line per day, so every aggregate of one value is that value', old: max(value) FILTER (WHERE day = (SELECT today FROM p)) AS today_value, new: sum(value) FILTER (WHERE day = (SELECT today FROM p)) AS today_value}
- {id: q-today-value-avg, what: 'today_value max read as avg: duplicate_ledger guarantees one row per line per day, so every aggregate of one value is that value', old: max(value) FILTER (WHERE day = (SELECT today FROM p)) AS today_value, new: avg(value) FILTER (WHERE day = (SELECT today FROM p)) AS today_value}
- {id: q-today-value-any_value, what: 'today_value max read as any_value: duplicate_ledger guarantees one row per line per day, so every aggregate of one value is that value', old: max(value) FILTER (WHERE day = (SELECT today FROM p)) AS today_value, new: any_value(value) FILTER (WHERE day = (SELECT today FROM p)) AS today_value}
- {id: q-today-rows-count-value, what: 'today_rows count(*) read as count(value): a NULL value raises ledger_value first', old: count(*) FILTER (WHERE day = (SELECT today FROM p)) AS today_rows, new: count(value) FILTER (WHERE day = (SELECT today FROM p)) AS today_rows}
- {id: q-parts-distinct-component, what: 'parts count(*) read as count(DISTINCT component): the attribution_component guard rejects NULL and blank components and duplicate_attribution guarantees distinct ones per line and day', old: count(*) AS parts, new: count(DISTINCT component) AS parts}
- {id: q-parts-count-value, what: 'parts count(*) read as count(value): a NULL value raises attribution_value first', old: count(*) AS parts, new: count(value) AS parts}
- {id: q-mtd-coalesce-m, what: 'coalesce dropped on the month-to-date sum in m: v coalesces it again', old: 'coalesce(sum(value) FILTER (WHERE day >= (SELECT month_start FROM p)), 0) AS mtd', new: sum(value) FILTER (WHERE day >= (SELECT month_start FROM p)) AS mtd}
- {id: q-mtd-coalesce-v, what: 'coalesce dropped on mtd in v: a NULL month-to-date compares NULL, which never breaches, and dark or ok follow as for 0', old: 'ELSE coalesce(m.mtd, 0) END AS mtd', new: ELSE m.mtd END AS mtd}
- {id: q-proj-mtd-coalesce-v, what: 'coalesce dropped on mtd inside projected: a NULL projection never reads projected_breach or warn, as 0 plus a 0 rate never does', old: 'ELSE coalesce(m.mtd, 0) + CASE', new: ELSE m.mtd + CASE}
- {id: q-level-coalesce-mtd, what: 'coalesce dropped on the stock level in mtd: a stock line with no rows compares NULL, which never breaches, and dark or ok follow as for 0', old: 'THEN coalesce(m.level, 0) ELSE coalesce(m.mtd, 0) END AS mtd', new: 'THEN m.level ELSE coalesce(m.mtd, 0) END AS mtd'}
- {id: q-level-coalesce-proj, what: 'coalesce dropped on the stock level in projected: a NULL projection never reads projected_breach or warn', old: "THEN coalesce(m.level, 0)\n              ELSE", new: "THEN m.level\n              ELSE"}
- {id: q-event-rate-coalesce-m, what: 'coalesce dropped inside event_rate: v coalesces the rate again', old: 'coalesce(sum(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)), 0) / (SELECT window_days FROM p) AS event_rate', new: sum(value) FILTER (WHERE day > (SELECT today FROM p) - (SELECT window_days FROM p)) / (SELECT window_days FROM p) AS event_rate}
- {id: q-event-rate-coalesce-v, what: 'coalesce dropped on event_rate in v: NULL only when the line has no ledger rows, and a NULL projection never reads projected_breach or warn', old: 'THEN coalesce(m.event_rate, 0) ELSE', new: THEN m.event_rate ELSE}
- {id: q-day-rate-missing-read-as-one, what: 'a missing daily rate read as 1: a daily line with no row in the rate window has no row today and reads dark first', old: 'coalesce(m.day_rate, 0) END *', new: 'coalesce(m.day_rate, 1) END *'}
- {id: q-day-rate-coalesce-v, what: 'coalesce dropped on day_rate in v: a daily line with month rows but none in the rate window has no row today and reads dark first', old: 'ELSE coalesce(m.day_rate, 0) END *', new: ELSE m.day_rate END *}
- {id: q-first-rule-min, what: 'min(rule) instead of ORDER BY rule LIMIT 1: the same rule', old: (SELECT rule FROM g WHERE n > 0 ORDER BY rule LIMIT 1), new: (SELECT min(rule) FROM g WHERE n > 0)}
- {id: q-when-unregistered-to-2, what: 'unregistered arm moved to position 2: every arm before unattributed is NULL when envelope and cadence are NULL, so an unregistered line still reads unregistered', old: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'", new: "         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'"}
- {id: q-when-unregistered-to-3, what: 'unregistered arm moved to position 3: every arm before unattributed is NULL when envelope and cadence are NULL, so an unregistered line still reads unregistered', old: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'", new: "         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'"}
- {id: q-when-unregistered-to-4, what: 'unregistered arm moved to position 4: every arm before unattributed is NULL when envelope and cadence are NULL, so an unregistered line still reads unregistered', old: "         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'", new: "         WHEN round(mtd, 9) > envelope THEN 'breach'\n         WHEN dark_flag THEN 'dark'\n         WHEN round(projected, 9) > envelope THEN 'projected_breach'\n         WHEN envelope IS NULL THEN 'unregistered'\n         WHEN unattributed_flag THEN 'unattributed'\n         WHEN round(projected, 9) > round(warn_share * envelope, 9) THEN 'warn'"}
- {id: q-guard-arm-warn-share-2, what: 'guard warn_share: the NOT isfinite arm dropped; DuckDB orders NaN above every value, so NaN > 1 and the infinities hit the bounds arms', old: '  UNION ALL SELECT ''warn_share'', count(*) FROM b WHERE warn_share IS NULL OR NOT isfinite(warn_share) OR warn_share <= 0 OR warn_share > 1', new: '  UNION ALL SELECT ''warn_share'', count(*) FROM b WHERE warn_share IS NULL OR warn_share <= 0 OR warn_share > 1'}
- {id: q-operand-dark-on-today-value-null, what: 'dark keyed on today_value IS NULL: duplicate_ledger and ledger_value leave one non-NULL value whenever a row exists', old: 'coalesce(cadence = ''daily'' AND today_rows = 0, false) AS dark_flag', new: 'coalesce(cadence = ''daily'' AND today_value IS NULL, false) AS dark_flag'}
- {id: q-operand-unregistered-on-cadence, what: 'unregistered keyed on cadence IS NULL: a budget row has both by guard, and no budget row has neither', old: WHEN envelope IS NULL THEN 'unregistered', new: WHEN cadence IS NULL THEN 'unregistered'}
- {id: q-distinct-today-rows, what: 'today_rows as count(DISTINCT day): duplicate_ledger leaves one row per line per day', old: count(*) FILTER (WHERE day = (SELECT today FROM p)) AS today_rows, new: count(DISTINCT day) FILTER (WHERE day = (SELECT today FROM p)) AS today_rows}
- {id: q-blank-component-replace-spaces, what: 'component blank arm as replace of spaces: DuckDB trim strips spaces only, so the two agree', old: component IS NULL OR trim(component) = '', new: 'component IS NULL OR replace(component, '' '', '''') = '''''}
- {id: q-calendar-month-start-arithmetic, what: 'month start as today minus (day - 1): the same date', old: 'CAST(date_trunc(''month'', {today}) AS DATE) AS month_start', new: '{today} - CAST(day({today}) - 1 AS INTEGER) AS month_start'}
- {id: q-calendar-days-remaining-datediff, what: 'days remaining via datediff: the same integer', old: 'last_day({today}) - {today} AS days_remaining', new: 'datediff(''day'', {today}, last_day({today})) AS days_remaining'}
- {id: q-dispatch-stock-as-not-flow, what: 'stock dispatch read as measure <> ''flow'': the measure guard admits only flow and stock, and NULL for an unregistered line either way', old: 'CASE WHEN b.measure = ''stock'' THEN coalesce(m.level, 0) ELSE coalesce(m.mtd, 0) END AS mtd', new: 'CASE WHEN b.measure <> ''flow'' THEN coalesce(m.level, 0) ELSE coalesce(m.mtd, 0) END AS mtd'}
- {id: q-dispatch-dark-as-not-event, what: 'dark read as cadence <> ''event'': the cadence guard admits only daily and event, and NULL for an unregistered line either way', old: 'coalesce(cadence = ''daily'' AND today_rows = 0, false) AS dark_flag', new: 'coalesce(cadence <> ''event'' AND today_rows = 0, false) AS dark_flag'}
- {id: q-event-rate-floor-division, what: 'event rate with // : DuckDB 1.5.4 returns true division for DOUBLE operands (100.0 // 7 = 14.2857); engine-specific, pinned by r05 on an engine that floors', old: ', 0) / (SELECT window_days FROM p) AS event_rate', new: ', 0) // (SELECT window_days FROM p) AS event_rate'}
- {id: q-unattributed-drops-parts-clause, what: 'the parts > 0 conjunct dropped: when parts is 0 the attributed sum is NULL and the comparison is NULL, as for q-parts-ge-zero', old: OR (parts > 0 AND abs(, new: OR (abs(}
```

## 3. Settled / contested / risk / open

### Settled (fixture s1-s3)

- **s1.** Catalog egress is a first-class budget with four standing invariants and a measurement
  obligation (Decision 88 clauses 1 and 2; decision). Cost is derived at read from quantities, never stored
  as dollars (Decision 199; Decision 206 clause 7's "no dollars" budgets by analogy). The ledger is the
  customer's own usage and stays in the data plane (Decision 209 clause 2(a)); whether any verdict crosses
  is #1399's k1. Measured dollars never appear in the public repository: Decision 101 bounds what is
  published, and the reconciliation's public_summary rule applies it to cost figures
  (scripts/cost_reconciliation.py:11) while keeping absolute dollars in a private sink.
- **s2.** Measured: 7 of 9 sibling reports hand cost or egress to this component; 1 of 9 names egress in
  its failure_signal or triggers; 2 of 9 carry a criterion naming egress; 0 of 9 declares a budget line, an
  envelope or a price (VP 1).
- **s3.** Measured: the repository's 5 cost triggers and 6 thresholds watch no egress, Lambda, catalog or
  review line; the telemetry leg of the monthly reconciliation returns None; the Decision 88 clause 2
  figure is still TBD; no Terraform budget or cost alarm exists and catalog_stats has no schedule (VP 2).

### Contested (k1-k3 in the fixture; k4-k9 report-only, the fixture's contested list is capped at 3)

- **k1 (asked). What a breach does.** (a) Alarm: the daily verdict files one recommendation per breaching
  line through the cost reconciliation's existing rec path (build_rec_fields, find_open_cost_rec_for), so
  the operator decides; nothing in the loop changes on its own. (b) Degrade: the owning component lowers
  its own cadence or window (a detector reads 7 days instead of 28; capture batches more turns per call)
  until the projection returns under the envelope. (c) Stop: the breaching line's schedule is disabled.
  Recommended (a) now and (b) at lift for lines with a single owner; (c) never unattended. (a) is the
  alarm-not-gate shape Decisions 55 and 62 and CD.28's reconciliation already use, and Decision 67's queue
  rule applies to the build (the design files nothing). (b) is a self-tuning loop and needs #1398's
  controller to know a component slowed itself (a cadence change is a version change under its k2). (c)
  stops the measurement with the spend, which is this item's own failure_signal. No Decision decides it;
  the reconciliation's rec path is precedent for the shape of (a), not for applying it to a daily,
  per-line verdict.
- **k2 (asked). Where the budget lives.** (a) A loop_budget block in config/agent/cost_reconciliation.yaml
  (register rows, envelopes, warn share, params), the file that already holds the thresholds and is
  "human-owned: edit when provider pricing or policy thresholds shift". (b) A new Class C contract,
  loop-budget.yaml, with the ledger and attribution tables as Class A entries. (c) This item's register
  rows only (section 2.2), until lift. Recommended (a) plus (c): the register is staged here and lands in
  (a) at build time; (b) is the right home once the ledger is a warehouse table, which is a build decision
  (T2.36 and the reader verbs own that boundary). (a) touches a config file a design slice may not edit.
- **k3 (asked). What unit the budget is kept in.** (a) Physical units per line, priced at read (staged).
  (b) Invoice dollars per line, read from the monthly snapshot. (c) Shares of the bill, as the five
  triggers use. Recommended (a), with (c) as the monthly leg: the daily verdict reads quantities, and the
  reconciliation's discrepancy leg compares the priced ledger with the invoice once a month (its
  invoice_vs_telemetry_discrepancy_pct is the right threshold, and its telemetry leg is the stub e5 names).
  (b) is a month late and cannot split a Lambda between components; (c) cannot see a cap. s1's "never
  stored" rests on Decision 199 for the ledger's rows (quantities priced at read) and on Decision 101 for
  publication; neither decides the unit an envelope is kept in, and Decision 101 does not bar a private
  table from holding invoice dollars. (b) is therefore disfavoured by Decision 199's derived-at-read rule
  by extension, not excluded: the operator sees all three. Consequence to weigh: (a) needs a price table per
  line, and Neon egress has none (O5), so that line stays in bytes until one exists.
- **k4 (asked; report-only). How catalog egress is measured.** (a) A proxy: catalog_stats bytes per read
  times reads per day, which the data plane can compute from its own metadata (Decision 88 clause 2's
  supported path) and which the model's "touched" and "whole_catalog" cases bracket. (b) The Neon
  consumption API, which reports the metered figure and needs a Neon API credential in the data plane (a
  new secret: always-ask by the charter if it is ever proposed; not proposed here). (c) The monthly Neon
  invoice, which is a month late. Recommended (a) now, calibrated once against (c) in the first measured
  month; (b) only if (a) and (c) disagree by more than the tolerance. Measurement, not precedent.
- **k5 (asked; report-only). How shared lines are attributed.** (a) Per-call stamps: every writer and reader
  call carries the calling component's id (the producer field Decision 199 already puts on every event; a
  `caller` tag on the Lambda log line), and attribution rows are summed from them. (b) Proportional: a
  shared line is split by each component's share of requests. (c) Unattributed by design for Lambda lines,
  attributed only for the lines with one owner. Recommended (a): it is what the verdict's tolerance rule
  assumes (v10-v12) and it costs one field per call; (b) hides a component whose calls are few and heavy
  (the detectors, e8); (c) leaves the two biggest lines unowned. No precedent. (a) changes writer and
  reader Lambda code, which ships through the governed code-deploy channel
  (.github/workflows/deploy-ducklake-lambdas.yml); a governed deploy is on the charter's always-ask list, so
  the build step behind (a) is always-ask even though it needs no Terraform, credential or billed metric.
  Two choices inside the staged SQL belong here: a shared line that reports 0 on a day needs no attribution rows (z01; under (a) a day with
  no calls writes none), and a NULL or blank component raises (r01, r08), so the build's stamp vocabulary
  must be canonical (case included: 'X' and 'x' are two components).
- **k6 (asked; report-only). The fixture item's edge home.** (a) depends_on T2.36 only (carried
  provisionally): the ledger is a telemetry-side table and the reconciliation's telemetry leg is stubbed
  "until T2.36 lands". (b) part_of T2.52, whose exit criterion already says aggregate verbs "honour the
  Decision 88 Neon egress budget". (c) depends_on the reader verbs and capture items, which the evaluator's
  L4 refuses while they are unmerged. Recommended (a) until W2 places the loop's items together.
- **k7 (asked; report-only). How a stock line is read.** (a) Its latest reading is the level, compared with
  the envelope; nothing is projected (staged; s01-s09). (b) Its month mean, which is what S3 bills
  (GB-month). (c) Its latest reading plus a 7-day growth trend times the days remaining, so a filling bucket
  warns before it is full. (d) Redefine the line as a flow of bytes added a day. Recommended (a) for the
  pilot: one comparison, no trend to tune, and the model already reads storage this way; (c) once a month
  of readings exists. The first draft summed the readings, so a flat 1 GiB against 20 GiB read breach by the
  30th (verification r1 F1). No precedent.
- **k8 (asked; report-only). Which meter feeds the two S3 lines.** For s3_requests: (a) counts from the
  loop's own code, PUTs per writer call and GETs per reader query from its plan, on the Lambda log line
  (staged); (b) S3 request metrics on the prefix, which need an aws_s3_bucket_metric in Terraform and are
  billed as custom metrics: always-ask, not chosen; (c) S3 server access logs to a prefix, Terraform plus
  storage cost: always-ask, not chosen; (d) the invoice's request line, a month late and bucket-wide. For
  s3_storage_bytes: (a) the catalog's own data_file_size_bytes over ducklake_list_files for the four tables,
  one read a day charged to catalog_egress_bytes (staged; src/common/ducklake_maintenance.py:133 already
  sums it); (b) S3 Inventory or Storage Lens on the prefix, Terraform plus spend: always-ask, not chosen;
  (c) a LIST over the prefix, priced per request (spend: always-ask, not chosen) and slow at scale; (d)
  bucket-wide BucketSizeBytes, free
  and readable today but with no prefix dimension, so a ceiling only. Recommended (a) for both, with the
  invoice as the monthly calibration (k3 (c)) and (d) as the storage ceiling. Criterion c2 depends on this
  fork: it is written so that every daily line's source needs no Terraform change and no billed metric.
  (a) is not free either: counting PUTs and GETs on the Lambda log line changes writer and reader Lambda
  code, which ships through the governed code-deploy channel, and a governed deploy is always-ask; what
  separates (a) from (b) and (c) is that it adds no Terraform, no credential and no billed metric. No fork
  here makes a Terraform, deploy or spend change; the always-ask options and steps are named so nobody
  stages them by accident (verification r1 F3, plan-critique r1 N1).
- **k9 (asked; report-only). The line roster.** Which lines the register carries, and each one's owner,
  cadence and measure, is a choice with no precedent. (a) The ten lines of section 2.2 (staged): every
  meter the deployed shapes expose plus the human line and two zero-envelope event lines. (b) Fewer:
  only the lines with a cap or a price (catalog egress, Lambda, S3), leaving review items to the ladder
  and the event lines until something happens. (c) More: one line per telemetry table, or per component,
  so attribution rows become lines of their own. (d) The invoice's own line items, so the ledger mirrors
  the bill. Recommended (a) for the pilot: it is the smallest roster on which every sibling's hand-off
  lands somewhere, and (c) is what attribution rows already provide without multiplying envelopes. The
  owners are the sibling items named in section 2.2 and "shared" where one Lambda serves all; the cadences
  follow whether the meter runs every day; the one stock is storage.

### Risks

- **R1 Budget Goodhart.** The cheapest ways to make every line read ok are to raise the envelope and to
  stop measuring. The first is a register change the operator approves with the prior month's residual
  beside it (c3) and every envelope is versioned with the params; the second is this item's own
  failure_signal (dark_line_days), so a silent meter reads as a failure, not as savings (#1400's register
  would carry this row).
- **R2 Shared Lambdas blur attribution.** One writer and one reader serve ten components; without per-call
  stamps (k5) the two largest priced lines are a single number nobody owns.
- **R3 The public boundary.** Quantities and envelopes are public; measured dollars and invoice figures are
  not (Decision 101; public_summary). The ledger's priced read must stay in the private sink the
  reconciliation already uses.
- **R4 Late and lumpy invoices.** The monthly leg (k3 (c)) sees a cap breach weeks after the daily verdict
  does; a daily verdict without a calibrated proxy (k4) can be wrong in either direction for a month.
- **R5 The egress proxy.** touched and whole_catalog differ by 17x at 5 sessions a day (e8). Until
  catalog_stats is run against the live catalog (q1) the envelope for the one line that matters most is a
  guess, and the GC gate (Decision 88 clause 4) decides which model the deployment is in.
- **R6 Price drift.** A list price is an external fact (the roadmap's own rate_basis is "fetched
  2026-08-01"); pricing at read means a stale table misprices every day at once. The reconciliation's
  DeepSeek price trigger is the pattern for re-reading prices; this register would need the same for
  Lambda and S3.
- **R7 Self-counted meters under-count.** The staged S3 sources (k8 (a)) count what the loop's own code
  does; a LIST, a retry inside the S3 client or a file the catalog no longer references is not counted.
  GETs are counted at the httpfs layer for that reason: a reader query issues several range GETs per
  Parquet file (the footer and each column chunk), so counting files scanned would miss a structural
  factor every month, not just retries. The monthly invoice line is the correction, and a residual above
  tolerance on either S3 line is the signal to revisit k8, not to add the billed metric silently.

### Open questions (q1-q3 in the fixture)

- **q1.** No measured egress exists. Who runs catalog_stats against the live catalog (Decision 88 clause
  2, never scheduled, e6), and are the seed envelopes set from that reading or from the model here? The
  telemetry stream says (relayed) its pre-production steps measure egress and no standing schedule is
  planned, so the answer may be "that measurement, once", with this register reading the proxy after.
  Until then every envelope in section 2.2 is a seed.
- **q2.** On the free tier (local adapter, no cloud account, Decision 184 clause 2) there is no Lambda, no
  Neon and no bill. Which lines exist there (review_items, loop_llm_tokens, local bytes?), and does a
  budget in bytes and seconds mean anything with no price to apply at read?
- **q3.** The review line is over at every scale (2,160 a month against 900). Is operator review a budget
  line here, or the ladder's own control (#1398 R1: afi times output volume is the number of human
  reviews a day), with this register only reading it?

### Named for owners, not filed (Decision 67)

- **O1** scripts/cost_reconciliation.py names est_cost_usd and TelemetryModelCalls (e5), both
  pre-Decision-199 names; the contract has cost_usd, derived at read through
  reader_verb:model_call_cost_estimate with no stored column, and cost_usd_reported, stored. The stub
  should read the derived verb when T2.36 lands (cost reconciliation owner).
- **O2** The roadmap's ducklake_catalog_neon line still reads "$0 (Neon serverless Postgres free tier ...)"
  while Decision 88 records the forced paid-plan upgrade of 2026-06-15 (roadmap cost_projection owner; a
  staged roadmap edit, Decisions 79 and 125, not made here).
- **O3** catalog_stats, Decision 88 clause 2's measurement path, has never been scheduled, and the clause 2
  figure is still TBD in the roadmap (e3, e6). The telemetry stream reports (relayed, unmerged) that no
  standing schedule is planned and that its own pre-production steps measure catalog egress; the first
  reading this register needs would come from there (maintenance and telemetry owners).
- **O4** The monthly reconciliation's telemetry leg is a stub with no owning line (e5); the ledger staged
  here is a candidate source for it (cost reconciliation owner; T2.36).
- **O5** No Neon egress unit price exists in the repository, so the one line Decision 88 ranks beside
  compute and storage cannot be priced at read (cost reconciliation owner).

## 4. Consideration register (as authored in the fixture)

- why: nothing budgets the loop: the roadmap cost block is "not a budget", its 5 triggers watch no egress,
  Lambda or review line, the telemetry cost leg returns None, Decision 88 clause 2 is TBD (VP 2); 7 of 9
  siblings hand cost here (VP 1).
- how: one register row per cost line (unit, owner, cadence, flow or stock, envelope, warn share, source),
  one ledger row per line per day plus attribution rows per component, and one daily SQL with seven
  verdicts per line; dollars derived at read.
- planes: data_plane.
- maturity (thresholds are provisional seeds; plan fork line): read_all, the operator re-derives every
  verdict run; to sampled after 30 runs re-derived unchanged since the last overturned one; to spot_check
  after 30 more on a 1-in-5 sample; to anomaly_triggered after 30 consecutive days with no dark_flag or
  unattributed_flag on any line and the last monthly invoice residual inside tolerance. The return leg and version rule
  are #1398's k1 and k2.
- failure_signal: loop spend or egress nobody reads: a daily cost line with no ledger row, a day with no
  component attribution, or an invoice the ledger cannot explain. Metric dark_line_days: lines with
  dark_flag or unattributed_flag that day; must be 0, read beside the monthly invoice-vs-ledger residual. Source: the daily
  verdict log's dark_flag and unattributed_flag per line, and the monthly cost reconciliation discrepancy
  leg.
- verification: c1 vectors, c2 ledger coverage over a month of runs from sources that need no Terraform
  change or billed metric (k8), c3 operator review of the register with the prior month's residual.
- rollback: stop the daily verdict schedule and drop the register wherever k2 places it; monthly cost
  reconciliation runs as before with its five triggers. Ledger and attribution rows stay as history; no
  envelope gates anything, so stopping unblocks nothing.

## 5. Boundary notes for W2 synthesis

- **Capture (#1384).** Its wire size per turn and its batching choice (k2 there) set writer_requests,
  writer_bytes and the file count every other line pays for. Its SessionEnd catch-up (3.2 MB plus 2.1 MB
  per 100 turns) is the one write shape with a burst; the ledger reads it as a day's value, not a spike.
- **Reader verbs (#1390).** Its q1 (per-call catalog egress, unmeasured) is this register's
  catalog_egress_bytes source under k4 (a); its c3 ("per-call catalog egress measured before any consumer
  depends on a verb") is the first ledger row this component would ever read. Its 7-day cap on
  sessions_window is the only per-call egress bound in W1.
- **Friction (#1394) and deliberation (#1395).** Both read narrow typed columns today; each names the
  option that would move its read onto transcript content (k3 (b) in both). That option changes
  catalog_egress_bytes and, for #1394, opens loop_llm_tokens; its envelope of 0 is the switch the operator
  flips, not a gate.
- **Rec filing (#1396) and back-validation (#1397).** #1397's one-call-per-candidate rule and #1396's
  file_budget of 3 per run are the detector-side egress levers; at 5 sessions a day the detectors are 83%
  of touched egress.
- **Maturity ladder (#1398).** review_items is its cost and its control knob at once (q3). If W2 makes the
  ladder the owner of review load, this register reads the line and the ladder sets the envelope.
  Contradiction for W2: the ladder's read_all rung is defined by review count, this register's by a verdict
  being re-derived; one of them owns "how many reviews a day the operator will do".
- **Allow-list transport (#1399).** plane_egress_bytes reads its audit row (each batch logged before
  sending) under transport (a); under (b) the control plane pays and the line belongs to the control
  plane's own budget, outside this fixture. Its k1 decides whether any verdict here may cross.
- **Goodhart register (#1400).** dark_line_days is this item's failure_signal and belongs in that register
  with a counter (the monthly residual) and a drill (a day withheld from one line should raise its
  dark_flag, whatever its label; the flags exist so that drill works on a line already in breach).
  Drills and the reviewer drill are review_items and reader_requests here.
- **Maintenance and GC.** Merge cadence and snapshot expiry decide the file count every catalog read pays
  for (section 2.3); Decision 88 clause 4's GC gate (rec-2113's restore drill) is therefore a cost
  dependency of the loop, and the maintenance Lambda is a reader of the same catalog. The maintenance
  module's ducklake_list_files size sum is the staged s3_storage_bytes source (k8), so the stock line is
  one more read of that catalog; transcripts over 64 KiB live in the blob port outside the catalog's data
  path, so the line adds the blob prefix by the same rule. W2 should place T2.19 and T2.26 beside the
  loop's items.
- **Monthly reconciliation (CD.28).** This register is its daily, per-line front end and its telemetry
  leg's source (k3 (c), O4); it is not a second reconciliation. k2 (a) puts the register in the same file.

## 6. Staged candidate decision text (for W3; not filed)

This text presumes the recommended options of parked forks k1, k2 and k3. It is not settled.

> "Every meter the telemetry feedback loop turns is a budget line with an owner. A line is kept in the
> unit its meter counts (bytes, requests, GB-seconds, tokens, review items), never in dollars; dollars
> are derived when read, from a versioned price table, and never stored or published. Each line has a
> cadence, a measure (a flow summed over the month or a stock read at its latest level), a monthly envelope
> and a warn share, and a shared line's daily value is attributed to the components that caused it within
> a stated tolerance. A line's meter is read from what the loop's own code and catalog already know; a
> meter that needs a billed metric or an infrastructure change is a decision of its own. A daily deterministic verdict reads the register,
> the ledger and the attribution and reports ok, warn, projected_breach, breach, dark, unattributed or
> unregistered per line; a breach files a recommendation through the cost reconciliation path and gates
> nothing. A line that reports nothing on a day it should is a failure of the loop, not a saving. The
> register lives beside the monthly reconciliation's thresholds, and the monthly discrepancy leg
> reconciles the priced ledger against the invoice. A meter counted by the loop's own Lambdas ships
> through the governed code-deploy channel like any other Lambda change. Decision 88's catalog egress line is the first line of
> the register, and its envelope is set from a measured reading, never a planning-time guess."
