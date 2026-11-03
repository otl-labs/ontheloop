# DuckLake Catalog Operations Runbook

```yaml
runbook: ducklake-catalog-operations
tier: T2.17-T2.18
decisions: [84, 81, 82, 78, 39, 37, 35, 77, 79, 62, 55]
exit_criteria: [EC3, EC12]
sections:
  - id: 1
    title: PlatformAdmin break-glass catalog-attach (inspect/repair)
    control: CD.33 O-1
  - id: 2
    title: OQ.12 DuckLake/DuckDB version-bump clone-rehearsal policy
    control: OQ.12
  - id: 3
    title: Maintenance pipeline -- cadences, guardrails, fail-closed guard set, manual invoke
    control: CD.33 clause 5-6 / Decision 81 clause 6
  - id: 4
    title: Catalog disaster-recovery (CD.34) -- DR Lambda, SNS alert wiring, co-tuning knobs
    control: CD.34 O-2 / Decision 82
  - id: 5
    title: Catalog restore drill + closed-boundary break-glass
    control: CD.33 O-2 as amended by CD.34 / Decision 81 clause 7
catalog:
  backend: Neon serverless Postgres (DIRECT endpoint, sslmode=require)
  dsn_parameter: /secret/ducklake-neon-catalog-dsn
  meta_schema: ducklake_ops
  catalog_alias: ops_catalog
  data_path: s3://agent-platform-data-lake/ducklake-neon-smoke/
  data_path_relocation: |
    Fixed at catalog-init and stored in the Neon metadata. DuckLake's OVERRIDE_DATA_PATH
    is a per-session override ONLY -- it does NOT persist the stored value (verified live).
    Relocating the catalog therefore requires reinitialising it (drop the meta_schema and
    re-ATTACH at the new path), not an override. Code aligns to the stored path, never the reverse.
  pinned_duckdb_version: "1.5.4"  # SSOT: config/lambda/ducklake/version.yaml
  pinned_ducklake_version: "v1.0"
  extension_platform: linux_amd64
```

## Section 1 -- PlatformAdmin break-glass catalog-attach (CD.33 O-1)

The closed reader/writer boundary (Decision 81) means every routine ops read transits `ducklake_reader`
and every write transits `ducklake_writer`. The ONLY sanctioned out-of-band access to the catalog is an
**audited PlatformAdmin break-glass**: a read-only ATTACH for inspect/repair when a Lambda path is wedged
(e.g. a stuck snapshot, an orphaned-file question, a schema drift investigation).

Authority for the break-glass read is the explicit, named `DuckLakeBreakGlass` inline policy on the
`PlatformAdmin` role (`terraform/bootstrap/platform_admin_data_policies.tf`): `s3:GetObject`/`s3:ListBucket` on the
`ducklake-*` prefixes; the DSN parameter read is PlatformAdmin's `SecretParameterAdmin` Sid on parameter/secret/*. It grants READ only -- a break-glass
session cannot mutate the catalog data.

### Preconditions

- Assume the `agent_platform_admin` (PlatformAdmin) role. Verify:
  `aws sts get-caller-identity --profile agent_platform_admin`
- DuckDB at the SSOT-pinned version locally. Check:
  `bin/venv-python -c "import duckdb; from src.common.ducklake_version import pinned_duckdb_version; assert duckdb.__version__ == pinned_duckdb_version(), (duckdb.__version__, pinned_duckdb_version())"`.
  The break-glass ATTACH uses the SAME pinned version as the runtime (OQ.12 lockstep).

### Inspect (read-only ATTACH)

The runtime's own connection authority backs the break-glass attach -- there is one ATTACH implementation
(`src/common/ducklake_runtime.py::open_connection`). Use the dev-mode opener (network INSTALL) from an
egress-permitted host, or point `extension_directory` at a local baked copy.

```bash
# 1. Confirm the credential is reachable (read the DSN parameter under agent_platform_admin).
aws ssm get-parameter \
  --name /secret/ducklake-neon-catalog-dsn --with-decryption \
  --profile agent_platform_admin --region eu-west-2 \
  --query Parameter.Value --output text >/dev/null && echo "DSN_READABLE"

# 2. Read-your-write inspect via the smoke test's restore drill (ATTACHes the catalog, verifies a probe).
bin/venv-python -m scripts.ducklake_neon_smoke_test --restore-drill --profile agent_platform_admin
```

For an ad-hoc inspect from a Python REPL (read-only):

```python
from src.common import ducklake_runtime as rt

dsn = rt.fetch_dsn(profile="agent_platform_admin")
con = rt.open_connection(dsn=dsn, data_path="s3://agent-platform-data-lake/ducklake-neon-smoke/")
# Inspect catalog metadata (snapshots, files, schema) -- READ ONLY.
print(con.execute("SELECT * FROM ducklake_snapshots('ops_catalog')").fetchall())
print(con.execute("SELECT count(*) FROM ducklake_list_files('ops_catalog', 'ducklake_smoke_history')").fetchone())
con.close()
```

### Repair

Repairs are deliberately NOT automated. Any mutation (snapshot expiry, orphan cleanup, schema fix) is a
human decision recorded as a Decision/recommendation, then executed via the maintenance pipeline (T2.18)
or a one-off reviewed script -- never silently from a break-glass REPL. If a repair is unavoidable in the
moment (incident), record exactly what was run in the incident log and file a follow-up recommendation to
encode the fix into the maintenance pipeline (Decision 55: no silent workarounds).

### Drill (EC12)

The break-glass path is drilled by the V3 verification step:
`bin/venv-python -m scripts.ducklake_neon_smoke_test --restore-drill` under `agent_platform_admin`, which
performs a consistent `pg_dump` -> scratch Neon restore -> DuckDB read-your-write verification. A green
drill proves the credential grant, the ATTACH, and the read path end-to-end.

## Section 2 -- OQ.12 DuckLake/DuckDB version-bump clone-rehearsal policy

DuckLake `v1.0` is lockstep with the DuckDB version pinned in
`config/lambda/ducklake/version.yaml` (the single source of truth / SSOT for all derive surfaces).
The catalog schema, the DuckLake extension, and the DuckDB engine move together. The runtime asserts
this at every connection (`src/common/ducklake_runtime.py::assert_duckdb_version`, loud-fail
`VersionMismatchError`). All derive surfaces load the pin through the shared loader
(`src/common/ducklake_version.py::pinned_duckdb_version()`); the ducklake-version-lockstep
`validate.py` gate enforces that no derive surface diverges from the SSOT.

**First exercised bump:** `1.5.3` → `1.5.4` (PLAN-duckdb-pin-bump-1-5-4, 2026-06-26).
A 1.5.4 engine successfully reads a 1.5.3-authored catalog clone (no on-disk migration needed).

```yaml
version_bump_surfaces:
  - config/lambda/ducklake/version.yaml          # SOLE EDITABLE -- change ONLY this value
  - requirements.txt                              # DERIVED -- sync via scripts/sync/ducklake_version.py
  - s3://agent-platform-data-lake/ducklake-extensions/<new-version>/  # re-seeded baked extensions
  # (src/common/ducklake_runtime.py and scripts/build_lambda.py derive via pinned_duckdb_version() --
  #  no edits needed when version.yaml is the sole change point)
```

### Environment constraint (where the rehearsal runs)

**Canonical pre-deploy path (CC-web compatible): `--canary-rehearsal`.**
All TCP/5432 happens server-side inside ephemeral Lambda canaries; the orchestrator communicates
over HTTPS/443 only (PLAN-oq12-clone-rehearsal-lambda / rec-2357). The direct-connection gates
(`--churn-gate`, `--restore-drill`) are privileged-env-only break-glass paths for non-CC-web
hosts with outbound TCP/5432 (a laptop, a dedicated compute node, or an eu-west-2 bastion).

**Direct-gate guard (Decision-88 egress acknowledgement):** `--churn-gate` and `--restore-drill`
refuse to run unless `DUCKLAKE_ALLOW_DIRECT_GATE=1` is set in the environment. Setting it
acknowledges that the caller has TCP/5432 access and accepts that the direct path is NOT
available from CC-web. DO NOT set this inside CC-web to work around a 5432 timeout -- that
is a structural block, not a transient network error.

### Clone-rehearsal gate (mandatory before any production bump)

1. **Pin candidate.** Edit `duckdb_version` in `config/lambda/ducklake/version.yaml` on a branch.
   Run `bin/venv-python -m scripts.sync.ducklake_version` to sync `requirements.txt`.
   Run `bin/venv-python -m scripts.validate --pre` to confirm the ducklake-version-lockstep gate passes.
2. **Re-seed extensions.** Fetch `ducklake`/`httpfs`/`postgres_scanner` for `vX.Y.Z/linux_amd64` and upload to
   `s3://agent-platform-data-lake/ducklake-extensions/vX.Y.Z/` (the build's S3 fallback). Confirm the local
   DuckDB `X.Y.Z` can LOAD all three from a baked `extension_directory` with autoload/autoinstall OFF.
3. **Build and upload candidate layers.**
   ```
   bin/venv-python -m scripts.build_lambda --ducklake-only --profile agent_platform
   ```
   This uploads `ducklake-deps-layer.zip`, `ducklake-extensions-layer.zip`, and
   `ducklake-pgclient-layer.zip` plus the three function zips to S3.
4. **Run the canary rehearsal from CC-web (canonical, no TCP/5432).**
   ```
   bin/venv-python -m scripts.ducklake_neon_smoke_test --canary-rehearsal --profile agent_platform [--json]
   ```
   The orchestrator: publishes candidate layers, creates ephemeral writer/reader/maintenance canaries
   pointed at a scratch meta-schema + scratch S3 prefix, proves ATTACH + churn (unchanged CD.33 budgets)
   + RYW + real-prod read-clone (`action_clone_catalog`), then tears down all ephemeral resources.
   For the read-clone step, the orchestrator creates a Neon native copy-on-write branch (HTTPS/443 REST
   API, `neon_api.create_branch`), passes the branch endpoint host to `action_clone_catalog` in the event,
   and deletes the branch in its finally block (Decision 100). The Lambda action ATTACHes the candidate
   DuckDB engine to the branch DSN at `meta_schema=ducklake_ops` and reads real catalog metadata read-only
   via `information_schema` -- it invokes no pg_dump, pg_restore, CREATE DATABASE, or DROP DATABASE.
   Passes all EC gates on the candidate engine. NEVER touches `ducklake_ops` or the production DATA_PATH.
5. **Compatibility decision.** If the canary rehearsal is green, file a Decision recording the bump
   and the rehearsal evidence, then roll the production layer (`--deploy`). If it regresses, STOP --
   do not bump; RCA the regression (Decision 55). Never relax the runtime version-assert to paper
   over a mismatch.

### Rollback

The pinned-version assert means a half-applied bump fails CLOSED (the runtime refuses to attach on a
mismatch) rather than silently writing with an incompatible engine. To roll back, revert the four surfaces
to the prior pin and redeploy the prior layer; the cloned catalog used for rehearsal is discarded.

## Section 3 -- Maintenance pipeline (T2.18 / CD.33 clause 5-6 / Decision 81 clause 6)

### Cadences

```yaml
maintenance_cadences:
  daily_merge:
    schedule: "cron(0 4 * * ? *)"    # 04:00 UTC
    action: merge
    description: "Non-destructive: flush_inlined_data + merge_adjacent_files only"
    destructive: false
    mechanism: EventBridge rule -> agent-platform-ducklake-maintenance Lambda (action=merge)
  weekly_gc:
    schedule: "cron(0 5 ? * SUN *)"  # 05:00 UTC Sunday
    action: gc
    description: "Guarded destructive: full 5-step sequence behind the G1-G4 fail-closed guard set"
    destructive: true
    mechanism: EventBridge rule -> agent-platform-ducklake-maintenance Lambda (action=gc)
```

Cadence mechanism rationale: EventBridge-scheduled Lambda per Decision 62 / CD.29 -- a single
deterministic Lambda needs no Step Functions state machine (Decision 39), and the Lambda posture
is consistent with T2.17 (writer/reader). A new GH Actions surface for non-CI scheduled work
would be a new ingress surface without benefit (CD.29).

Table scope (T2.18 FP-A): `ducklake_smoke_history` + `ducklake_smoke_current`. Expansion to the
full `ducklake_ops` catalog and all `ops_*` business tables is a T2.19 exit criterion -- the code
carries an explicit forward-pointer constant (`src/common/ducklake_maintenance.py::MAINTENANCE_SCOPE_NOTE`).

### Guardrail constants (CD.33 H1/R-3/O-3/M-3 / Decision 81 clause 6)

```yaml
guardrails:
  snapshot_retain_days: 30        # expire_snapshots: older_than = now - 30 days
  file_cleanup_grace_days: 7      # cleanup_old_files + delete_orphaned_files: older_than = now - 7 days
  snapshot_floor: 2               # never expire below the 2 most-recent snapshots
  cleanup_all_in_scheduled_runs: never  # NEVER pass cleanup_all=True in scheduled runs
  g4_max_delete_files: 20_000        # G4: absolute per-pass file-count bound; over-budget defers the whole pass
  g4_max_delete_bytes: 10_737_418_240  # G4: absolute per-pass byte bound (10*1024^3 bytes)
```

These are module-level constants in `src/common/ducklake_maintenance.py` (guardrails) and
`src/common/ducklake_maintenance_ops.py` (G3/G4 bounds). They are tunable knobs but NEVER relaxed
to make a gate pass (Decision 55). Changing them requires a Decision superseding CD.33.

### Fail-closed guard set (T2.18, Decision 188 amending Decision 81 clause 6 / CD.33 H1; Decision 193 amends G4)

Four guards run PRE- and POST-destructive (src/common/ducklake_maintenance_ops.py), replacing the
retired file-fraction/byte-budget circuit breaker:

1. **G1 reachability** -- the would-delete set (dry-run `ducklake_cleanup_old_files` +
   `ducklake_delete_orphaned_files`, re-probed AFTER `expire_snapshots`) and the live set
   (`ducklake_list_files`) must be disjoint. The pre-check runs against the POST-expiry candidate
   set (checked against the reused pre-expiry live read); the post-destructive re-check runs
   against the ADMITTED (post-drain) set intersected with a fresh live read -- i.e. what was
   actually deleted.
2. **G2 retention floor** -- re-asserts, from a fresh post-expiry read, that `expire_snapshots`
   actually left at least the configured floor of snapshots.
3. **G3 catalog sanity** -- aborts on an empty live set, or a live-byte drop past an absolute
   bound, after the pass.
4. **G4 deletion bound** -- an absolute per-pass file-count/byte cap (g4_max_delete_files /
   g4_max_delete_bytes above), sized STRICTLY from the caller's declared size source (a storage
   listing, never the catalog live set -- an unsized candidate raises rather than sizing to 0, on
   every destructive path). Evaluated against the POST-expiry candidate set. An over-budget pass
   DRAINS at the youngest cutoff (in days, never below `file_cleanup_grace_days`) that admits a
   positive count under both caps; if no cutoff does, the pass defers the WHOLE pass instead
   (nothing is deleted this run) and reports the deferred count/bytes.

A G1/G2/G3 violation, or a G4 strict-sizing raise (an unsized would-delete candidate), raises
`DuckLakeMaintenanceError` and aborts (no destructive call is issued beyond what already ran); on
abort, emit `MaintenanceBreakerTrip=1` to the `DuckLakeMaintenance` CloudWatch namespace. A G4
over-budget outcome is different: the pass drains partially or defers wholesale (nothing extra is
deleted beyond the admitted set) but the response is still a 200 -- `MaintenanceBreakerTrip` stays
0, and the outcome is visible in the response body's `guard_stats.g4_bounded` /
`guard_stats.g4_deferred_files` / `guard_stats.g4_drain_cutoff_days` fields (`g4_drain_cutoff_days`
is `null` on a wholesale defer, and the cutoff in days -- always `>= file_cleanup_grace_days` --
on a drain or a within-budget pass). `gc_ops`'s `GcDrainCutoffDays` CloudWatch metric mirrors this
as a float, with `-1.0` as the null sentinel (never a real cutoff, since a real one is always `>=
7`; `0.0` would misread as a cutoff below the floor).

**Reading the alarm (FP-B):** the `ducklake-maintenance-circuit-breaker` CloudWatch alarm fires
when `MaintenanceBreakerTrip >= 1` in a 5-minute window. As of T2.18 FP-B, `alarm_actions` is
wired to the shared personal-account SNS topic (`agent-platform-alerts`). An email notification
is sent on alarm state entry. Confirm the SNS subscription is active (not `PendingConfirmation`)
before relying on the page-out; check with:
```bash
aws sns list-subscriptions-by-topic \
  --topic-arn $(terraform -chdir=terraform/personal output -raw alerts_topic_arn) \
  --profile agent_platform
```

The admin alarm declares no dimensions, so it sees only `agent-platform-ducklake-maintenance`'s
datum; the smoke function emits `MaintenanceBreakerTrip` with `Catalog=smoke` and trips only
`ducklake-maintenance-smoke-circuit-breaker` (the per-deploy `--lambda-maintenance-breaker` gate
trips it on purpose). A scheduled run discards its response, so the cause is in the function's
CloudWatch log as a `MAINTENANCE_FAILURE status=500 body=...` line (`MAINTENANCE_SMOKE_FAILURE` on
the smoke function):
```bash
aws logs filter-log-events --log-group-name /aws/lambda/agent-platform-ducklake-maintenance \
  --filter-pattern '"MAINTENANCE_FAILURE"' --start-time <epoch-ms> --profile agent_platform
```

**Triage the trip by cause, not just by the metric.** Four causes exist, and only three are the
"RCA the file accumulation" case below:
- **G1/G2/G3 violation** -- RCA the file accumulation: is the expiry cutoff too recent (< 30
  days)? did a previous cleanup run fail silently, leaving many expired-but-not-cleaned files? is
  there a bug in the orphan path producing large volumes of orphaned files?
- **G4 strict-sizing raise (the fourth cause, Decision 193)** -- the error names the unsized path.
  This is NOT an over-reclaim breach and NOT the file-accumulation triage above: it means the
  catalog scheduled a path for deletion that the STORAGE listing could not size -- a
  catalog/storage inconsistency (rec-3892 is the standing suspect for `gc_ops`'s smoke prefix).
  RCA the inconsistency; never widen the size source to make the raise stop (Decision 55/163) --
  sizing an unmeasurable candidate as 0 is exactly the silent behaviour this guard replaces. The
  `gc_ops` dry_run measurement path reports the same condition non-destructively, before this
  cause can ever fire live: its top-level `unsized_candidates` count (from a non-strict
  `size_candidates` call, since a dry-run deletes nothing) should read 0 before the weekly
  destructive schedule is ever enabled -- a non-zero reading is this same catalog/storage
  inconsistency, caught in advance.

Never relax a guard to pass, on any of the four causes.

### Manual invoke runbook

All recipes below invoke `agent-platform-ducklake-maintenance`, an admin-gated singleton --
PlatformDev's `DuckLakeInvokeUrl` and `DuckLakeInvokeViaUrlOnly` grants are scoped to the writer/reader/telemetry-writer Lambdas only, so every
recipe here runs under `agent_platform_admin` (Decision 143 blast radius; maintenance stays
break-glass, never widened to remove the operator step).

```bash
# Invoke daily merge manually (safe, non-destructive):
aws lambda invoke \
  --function-name agent-platform-ducklake-maintenance \
  --payload '{"action":"merge"}' \
  --profile agent_platform_admin \
  --region eu-west-2 \
  /tmp/merge-response.json && cat /tmp/merge-response.json

# Invoke weekly GC manually (guarded, emits metrics):
aws lambda invoke \
  --function-name agent-platform-ducklake-maintenance \
  --payload '{"action":"gc"}' \
  --profile agent_platform_admin \
  --region eu-west-2 \
  /tmp/gc-response.json && cat /tmp/gc-response.json

# Invoke the PRODUCTION gc_ops dry_run (read-only measurement, no destructive call -- the
# pre-enablement baseline canary, and its rehearsal-before-any-future-re-enable equivalent).
# data_path is resolved via `terraform output -raw`, never a literal bucket name.
DATA_PATH=$(terraform -chdir=terraform/personal output -raw ducklake_prod_data_path)
# OPERATOR WORKSTATION ONLY: the `terraform output` below needs a terraform binary and remote-state
# access, which no agent container has (Decision 119). An agent taking this reading substitutes the
# literal production data_path instead; everything else in the recipe is identical.
# --cli-read-timeout 900: the default 60s client read-timeout is too short for this probe (the
# ladder walks multiple cutoffs, each re-listing the storage prefix) -- see the same fix already
# applied to run_scheduled_agent.py's own lambda invoke (docs/SESSION_LOG_ARCHIVE.md).
# Invoke clear of the singleton's :15/:30 6-hourly cadences (control_health/merge_ops) -- a
# concurrent invoke throttles with a 429 under reserved_concurrent_executions=1.
aws lambda invoke \
  --function-name agent-platform-ducklake-maintenance \
  --payload "{\"action\":\"gc_ops\",\"data_path\":\"$DATA_PATH\",\"meta_schema\":\"ducklake_ops\",\"dry_run\":true}" \
  --cli-binary-format raw-in-base64-out \
  --cli-read-timeout 900 \
  --profile agent_platform_admin \
  --region eu-west-2 \
  /tmp/gc-ops-dryrun-response.json && cat /tmp/gc-ops-dryrun-response.json
# Expect: {"ok": true, "action": "gc_ops", "dry_run": true, "referenced_missing": 0,
# "unsized_candidates": 0, "drain_probe_counts": {...}, "tables": [...], ...}. A non-zero
# referenced_missing or unsized_candidates is an incident to RCA, never a gate to work around.

# Forced G1-trip probe, via the G1_PROBE_FORCE_CONFLICT constant (VP step 11 / diagnostic):
aws lambda invoke \
  --function-name agent-platform-ducklake-maintenance \
  --payload '{"action":"breaker_probe"}' \
  --profile agent_platform_admin \
  --region eu-west-2 \
  /tmp/breaker-response.json && cat /tmp/breaker-response.json
# Expect: statusCode=500, breaker_tripped=true.

# Check singleton concurrency (reserved_concurrent_executions must be 1):
aws lambda get-function-concurrency \
  --function-name agent-platform-ducklake-maintenance \
  --profile agent_platform_admin

# Check an EventBridge rule's state (events:ListRules is granted to NO identity in this account --
# describe-rule by exact name instead; example is the production gc_ops rule):
aws events describe-rule \
  --name agent-platform-ducklake-maintenance-gc-ops \
  --profile agent_platform_admin
```

**Live cadence (gc-ops-baseline-gate-and-schedule-enable):** `gc_ops` runs weekly,
`cron(45 3 ? * SUN *)` (03:45 UTC Sunday), 45 minutes after the catalog-DR dump
(`cron(0 3 ? * SUN *)`, Section 4) so backup precedes destruction. The
`ducklake-maintenance-gc-ops-liveness` alarm sits in ALARM from the moment production GC has never
run until the first scheduled pass emits `GcDeletedSnapshots` -- expected, not an incident, until
the first Sunday firing after enablement.

**Delete-marker recovery** (over-reclaim rollback -- UNREHEARSED, rec-3907 carries the rehearsal;
rehearse on a scratch prefix before running this against production). The data-lake bucket is
versioned with no lifecycle rule, so a deleted object retains its bytes as a noncurrent version
behind a zero-byte delete marker; removing the marker by version id restores the object:

```bash
BUCKET=$(terraform -chdir=terraform/personal output -raw ducklake_prod_data_path | sed -E 's#^s3://([^/]+)/.*#\1#')
PREFIX="ducklake/"   # narrow this to the affected sub-prefix before running

# List delete markers under the affected prefix:
aws s3api list-object-versions \
  --bucket "$BUCKET" \
  --prefix "$PREFIX" \
  --query 'DeleteMarkers[].{Key:Key,VersionId:VersionId}' \
  --profile agent_platform_admin

# Restore one object by removing its delete marker (repeat per marker -- there is no bulk API):
aws s3api delete-object \
  --bucket "$BUCKET" \
  --key "<key-from-above>" \
  --version-id "<marker-version-id-from-above>" \
  --profile agent_platform_admin
```

### Singleton constraint (Decision 81 clause 6)

`reserved_concurrent_executions = 1` is set on the maintenance Lambda. This is intentional: the
maintenance pipeline must not run concurrently with itself (overlapping GC passes on the same
catalog could corrupt the cleanup state). This differs from the ducklake_writer Lambda (Decision
81 clause 3), which has NO reserved concurrency so its OCC model is not artificially constrained.
The distinction is documented in `terraform/personal/ducklake_maintenance.tf` to pre-empt a
reviewer keyword-collision flag.

### T2.19 expansion forward pointer

The maintenance scope for T2.18 FP-A/FP-B is `ducklake_smoke_*`. At T2.19, the scope GENERALISES
to the full `ducklake_ops` catalog and all real `ops_*` business tables. The code carries an
explicit constant (`MAINTENANCE_SCOPE_NOTE` in `src/common/ducklake_maintenance.py`) with the
expansion instructions. No code change is needed in the primitives themselves -- the table list is
passed in at call time.

The hot_merge cadence (`HOT_TABLE_SCOPE`) follows the same expansion pattern: currently scoped to
`ducklake_smoke_*`, expanding to the real high-write-rate `ops_recommendations`/`ops_decisions`
tables at T2.19 when the DuckLake writer is the live write path and real dead-file rates exist to
tune against.

## Section 4 -- Catalog disaster-recovery (T2.18 FP-B / CD.34 / Decision 82)

### Cadence and format

```yaml
catalog_dr:
  lambda: agent-platform-ducklake-catalog-dr
  schedule: "cron(0 3 * * ? *)"    # 03:00 UTC daily
  pg_dump_flags: "--format=custom --serializable-deferrable"
  format_note: >-
    --format=custom is compressed and supports selective/parallel restore via pg_restore.
    Diverges from the T2.16b restore-drill format (plain SQL; restores via psql).
    T2.19 carry: the T2.19 restore-drill gate must be updated to use pg_restore for the
    custom-format dump (the existing _restore_dump / restore_drill in the smoke test uses psql).
  key_format: "catalog-dr/{YYYY}/{MM}/{DD}/ducklake-catalog-pg{PG}-duckdb{DUCKDB}-{ts}.dump"
  retention_days: 30     # tunable to 7 (S3 lifecycle rule); re-baseline on engine bump (OQ.12)
  engine_version_tag: "PG16 + duckdb {pin} in both S3 key and object metadata; pin derived from config/lambda/ducklake/version.yaml at Lambda boot"
  metric: "CatalogDumpSuccess=1 to CloudWatch DuckLakeCatalogDR namespace on success"
  loud_fail: "Non-zero pg_dump exit raises CatalogDrError BEFORE emitting the metric (Decision 55)"
```

### Freshness alarm

The `ducklake-catalog-dr-freshness` CloudWatch alarm uses evaluation-period math to implement a
>25h lookback (CloudWatch's `period` ceiling is 86400s/24h):

```yaml
freshness_alarm:
  metric: CatalogDumpSuccess
  namespace: DuckLakeCatalogDR
  period: 3600           # 1-hour buckets
  evaluation_periods: 25
  datapoints_to_alarm: 25  # ALL 25 hourly periods must have < 1 success to alarm
  comparison_operator: LessThanThreshold
  threshold: 1
  treat_missing_data: breaching  # a missed invocation counts as failing
  alarm_actions: [aws_sns_topic.alerts.arn]  # shared SNS topic (Decision 39)
```

When the freshness alarm fires, the DR Lambda has not produced a successful dump in over 25 hours.
Investigate:
1. Check CloudWatch Logs for `/aws/lambda/agent-platform-ducklake-catalog-dr`.
2. Check EventBridge rule state: `aws events list-rules --name-prefix agent-platform-ducklake-catalog-dr --profile agent_platform`
3. If the rule is ENABLED but no invocation occurred, check Lambda concurrency + throttle metrics.
4. If the dump failed: check the error in logs; if the Neon endpoint is unreachable, check Neon
   console; if pg_dump exited non-zero, the error string is in the Lambda log + in the 5xx response.

### SNS alert wiring (Decision 39)

One shared personal-account SNS topic (`agent-platform-alerts`) is the `alarm_actions` target
for both maintenance alarms:

| Alarm | Triggers on |
|---|---|
| `ducklake-maintenance-circuit-breaker` | `MaintenanceBreakerTrip >= 1` in a 5-minute window |
| `ducklake-catalog-dr-freshness` | No `CatalogDumpSuccess` in any of the last 25 hourly periods |

The SNS email subscription endpoint is set in `terraform/personal/terraform.personal.tfvars`
(`alerts_email = "..."`). The file is gitignored -- no email address is committed to source.

**Activating page-out:** after the first `terraform apply` that creates the SNS resources, AWS
sends a subscription-confirmation email. The recipient must click the confirmation link before
the page-out is live. Verify subscription status:
```bash
aws sns list-subscriptions-by-topic \
  --topic-arn $(terraform -chdir=terraform/personal output -raw alerts_topic_arn) \
  --profile agent_platform
# Subscription SubscriptionArn must be a real ARN (not "PendingConfirmation").
```

### Manual invoke

```bash
# Invoke the DR Lambda manually (initiates pg_dump -> S3 -> metric):
aws lambda invoke \
  --function-name agent-platform-ducklake-catalog-dr \
  --profile agent_platform \
  --region eu-west-2 \
  /tmp/dr-response.json && cat /tmp/dr-response.json
# Expect: {"ok": true, "s3_key": "catalog-dr/...", "dump_bytes": ..., "pg_version": "16", ...}

# Invoke hot_merge manually (merge-only, non-destructive):
aws lambda invoke \
  --function-name agent-platform-ducklake-maintenance \
  --payload '{"action":"hot_merge"}' \
  --profile agent_platform \
  --region eu-west-2 \
  /tmp/hot-merge-response.json && cat /tmp/hot-merge-response.json
# Expect: {"ok": true, "action": "hot_merge", "files_before": N, "files_after": M}

# Check DR schedule and freshness alarm:
aws events list-rules --name-prefix agent-platform-ducklake-catalog-dr --profile agent_platform
aws cloudwatch describe-alarms \
  --alarm-names ducklake-maintenance-circuit-breaker ducklake-catalog-dr-freshness \
  --query 'MetricAlarms[].{Name:AlarmName,Actions:AlarmActions,State:StateValue}' \
  --profile agent_platform
```

### T2.19 restore-drill carry item -- resolved (rec-2113)

The T2.16b restore drill (`restore_drill` / `_restore_dump` in `ducklake_neon_smoke_test.py`)
restores a PLAIN-SQL dump via `psql` and is unaffected by this change. The FP-B scheduled DR uses
`--format=custom` which requires `pg_restore`. The T2.19 restore-drill gate ("catalog rebuilt from
the daily S3 pg_dump passes read-your-write before cutover") required updating that separate
restore mechanism to `pg_restore`.

Resolved by rec-2113 (T2.26 c1): the pgclient Lambda layer now ships a fail-closed, PG16-asserted
`pg_restore` binary (`build_pgclient_layer` in `scripts/build_lambda.py`), enabling the
`--catalog-restore-drill` custom-format `pg_dump` -> `pg_restore` -> read-your-write round-trip.
Closure is confirmed by the plan's post-deploy Verification Plan step invoking
`ducklake_neon_smoke_test --catalog-restore-drill` against the deployed layer; see the PR/rec-2113
record for the passing run.

## Section 5 -- catalog restore drill and closed-boundary break-glass

### Restore drill (CD.33 O-2 as amended by CD.34) -- DEFERRED at sign-off (Decision 81 cl.7)

**The `pg_restore` restore drill was NOT executed at the 2026-06-09 recs sign-off.** The pgclient
Lambda layer ships `pg_dump` but NOT `pg_restore` (adding it needs a non-CC-web operator AL2023-ABI
layer rebuild), so `--catalog-restore-drill` / the maintenance `restore_drill` action fail with
`FileNotFoundError: /opt/bin/pg_restore`. This deviation is ACCEPTED (human-directed) with compensating
controls:

1. The daily `pg_dump`-to-S3 export (`--format=custom`, `src/common/catalog_dr.run_catalog_dump`) runs.
2. A `>25h` freshness CloudWatch alarm fires if the daily export stops.
3. Neon's own native PITR / branch backups provide an independent restore path.

**HARD GATE (follow-up rec filed in this cutover's PHASE 3):** the `pg_restore` restore drill MUST pass
before the NEXT ops table migrates to DuckLake. When the pgclient layer ships `pg_restore`:

```bash
bin/venv-python -m scripts.ducklake_neon_smoke_test --catalog-restore-drill
# RESTORE helper: src/common/catalog_dr.build_pg_restore_cmd / run_pg_restore (--clean --exit-on-error).
```

### Closed boundary / break-glass (OQ.7, Decision 81 cl.7)

Every ops read transits `ducklake_reader`; every write transits `ducklake_writer`. There is no second
read or write path -- `ops_data_portal` has no fallback transport. The only break-glass is the audited
PlatformAdmin principal reading the Neon catalog credential (Secrets Manager) + S3, plus the
catalog-DR PITR export -- see Section 1.
