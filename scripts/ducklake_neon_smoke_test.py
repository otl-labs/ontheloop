#!/usr/bin/env python3
"""DuckLake Neon catalog smoke test (T2.16b / T2.18 FP-B / CD.34).

Live gates, run post-deploy from a network-permitted context (egress to the Neon endpoint AND,
for a fresh extension install, to extensions.duckdb.org):

  --attach        ATTACH the Neon catalog over TLS (sslmode=require, SNI) on the pinned DuckDB and run
                  SELECT 1 against the DIRECT (unpooled) endpoint. Prints `ATTACH OK rows=1`.
  --churn-gate    Connection-churn / OCC-collision gate: a concurrent-writer burst on the direct
                  endpoint against a scale-to-zero Neon compute. Pass = OCC-collision rate AND commit
                  latency (including cold-resume) within CD.33's OCC budget. Prints `CHURN_GATE PASS`.
  --restore-drill Consistent pg_dump (--serializable-deferrable, engine-version-tagged) -> scratch
                  Neon database -> DuckDB read-your-write. Prints `RESTORE_OK read-your-write verified`.
                  The DR proof, run before any production write.

Reuses src/common/ducklake_spike.py for the duckdb-require + S3-credential helpers and fetches the Neon
DSN JSON from SSM Parameter Store (docs/contracts/secret-material-handling.yaml RUNTIME-FETCH pattern,
Decision 175 -- rehomed from the Decision 37 precedent). The churn gate and the restore drill LOUD-FAIL (Decision
55): a failed gate raises SmokeTestFailure and is a stop-and-RCA signal -- never silently relax a
threshold or degrade to pass.
"""

from __future__ import annotations

import argparse
import subprocess  # noqa: F401 -- re-exported: smoke.subprocess (module-object patch target)
import sys
from typing import Callable, Optional

# Consolidated package re-exports (Decision 104 facade convention): one block per owning
# submodule, never split across two blocks (ruff format silently drops symbols from a second
# block for the same module). Most names are re-exported for the public/test-referenced surface
# only (not used directly below), hence the block-level noqa: F401.
from scripts.ducklake_smoke.canary import (  # noqa: F401 -- re-exported package surface
    _CANARY_FUNCTION_NAMES,
    _CANARY_HANDLERS,
    _CANARY_PROD_FUNCTION_NAMES,
    _CANARY_SCRATCH_META,
    _CANARY_ZIP_KEYS,
    _aws_cmd,
    _canary_create_function,
    _canary_delete_function,
    _get_function_role_arn,
    _lambda_invoke_cli,
    _publish_candidate_layers,
    _wait_function_active,
    canary_rehearsal,
)
from scripts.ducklake_smoke.core import (  # noqa: F401 -- re-exported package surface
    CATALOG_ALIAS,
    CATALOG_DR_URL_ENV,
    DSN_PARAMETER_NAME,
    MAINTENANCE_SMOKE_URL_ENV,
    MAINTENANCE_URL_ENV,
    META_SCHEMA,
    READER_URL_ENV,
    SMOKE_DATA_PATH,
    WRITER_URL_ENV,
    SmokeTestFailure,
    _function_url,
    _libpq_conninfo,
    _ok_json,
    _p95,
    _sigv4_invoke,
    fetch_dsn,
)
from scripts.ducklake_smoke.direct_gates import (  # noqa: F401 -- re-exported package surface
    _consistent_pg_dump,
    _derive_scratch_dsn,
    _dsn_uri,
    _engine_tag,
    _evaluate_churn,
    _is_occ_collision,
    _open_attached,
    _restore_dump,
    _run,
    _run_churn_burst,
    _single_writer_commit,
    _verify_probe,
    _write_probe,
    attach_roundtrip,
    churn_gate,
    restore_drill,
)
from scripts.ducklake_smoke.lambda_ec_gates import (  # noqa: F401 -- re-exported package surface
    _assert_warm_reuse,
    _warm_reuse_probe,
    lambda_attach,
    lambda_churn,
    lambda_churn_incontainer,
    lambda_idempotency,
    lambda_ingress,
    lambda_inlining,
    lambda_loudfail,
    lambda_partition,
    lambda_reader,
    lambda_warm_reuse,
    lambda_warm_reuse_writer,
)
from scripts.ducklake_smoke.lambda_maintenance_gates import (  # noqa: F401 -- re-exported package surface
    lambda_catalog_dr,
    lambda_maintenance_breaker,
    lambda_maintenance_create_telemetry,
    lambda_maintenance_gc,
    lambda_maintenance_hot_merge,
    lambda_maintenance_merge,
)
from scripts.ducklake_smoke.lambda_ops_gates import (  # noqa: F401 -- re-exported package surface
    catalog_restore_drill,
    connect_probe,
    lambda_append_only,
    lambda_row_rules,
    migrate_ops_recs_columns,
    ops_churn_regate,
    ops_read_your_write,
)
from scripts.ducklake_smoke.lambda_telemetry_gates import (  # noqa: F401 -- re-exported package surface
    lambda_telemetry_production,
    lambda_telemetry_smoke,
)
from scripts.ducklake_smoke.lambda_telemetry_reader_gates import (  # noqa: F401 -- re-exported package surface
    lambda_telemetry_reader,
)
from src.common import catalog_dr as _catalog_dr  # noqa: F401 -- re-exported: smoke._catalog_dr
from src.common import ducklake_runtime, ducklake_spike, neon_api  # noqa: F401 -- re-exported module aliases
from src.common.ducklake_runtime import (  # noqa: F401 -- re-exported runtime budget constants
    CHURN_WRITERS,
    CHURN_WRITES_PER_WRITER,
    COMMIT_LATENCY_BUDGET_MS,
    OCC_COLLISION_RATE_BUDGET,
)

_LAMBDA_GATES: dict[str, Callable[..., None]] = {
    "lambda_attach": lambda_attach,
    "lambda_ingress": lambda_ingress,
    "lambda_idempotency": lambda_idempotency,
    "lambda_partition": lambda_partition,
    "lambda_inlining": lambda_inlining,
    "lambda_loudfail": lambda_loudfail,
    "lambda_churn": lambda_churn,
    "lambda_churn_incontainer": lambda_churn_incontainer,
    "lambda_reader": lambda_reader,
    "lambda_row_rules": lambda_row_rules,
    "lambda_warm_reuse": lambda_warm_reuse,
    "lambda_warm_reuse_writer": lambda_warm_reuse_writer,
    "lambda_maintenance_merge": lambda_maintenance_merge,
    "lambda_maintenance_gc": lambda_maintenance_gc,
    "lambda_maintenance_breaker": lambda_maintenance_breaker,
    "lambda_catalog_dr": lambda_catalog_dr,
    "lambda_maintenance_hot_merge": lambda_maintenance_hot_merge,
    "lambda_maintenance_create_telemetry": lambda_maintenance_create_telemetry,
    "connect_probe": connect_probe,
    "lambda_append_only": lambda_append_only,
    "lambda_telemetry_production": lambda_telemetry_production,
    "lambda_telemetry_smoke": lambda_telemetry_smoke,
    "lambda_telemetry_reader": lambda_telemetry_reader,
}


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entrypoint. Returns the process exit code (0 ok; 1 on a loud-fail gate or usage error)."""
    parser = argparse.ArgumentParser(
        prog="ducklake_neon_smoke_test", description="DuckLake Neon catalog smoke test (T2.16b / T2.17 / T2.18)."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--attach", action="store_true", help="ATTACH + SELECT 1 over TLS")
    group.add_argument("--churn-gate", action="store_true", help="connection-churn / OCC gate (loud-fail)")
    group.add_argument("--restore-drill", action="store_true", help="pg_dump -> scratch Neon -> read-your-write")
    group.add_argument(
        "--ops-read-your-write",
        action="store_true",
        dest="ops_read_your_write",
        help="[post-deploy] T2.19 VP11: write_ops via writer -> read via reader; absent update loud-fails 409",
    )
    group.add_argument(
        "--ops-churn-regate",
        action="store_true",
        dest="ops_churn_regate",
        help="[post-deploy] T2.19 VP12: Decision-82 EC8 churn/OCC re-gate at production scope (loud-fail)",
    )
    group.add_argument(
        "--catalog-restore-drill",
        action="store_true",
        dest="catalog_restore_drill",
        help="[post-deploy] T2.19 VP11: invoke maintenance restore_drill (pg_dump->pg_restore + read-your-write)",
    )
    group.add_argument("--lambda-attach", action="store_true", help="[post-deploy] in-Lambda ATTACH proof (EC1)")
    group.add_argument(
        "--lambda-ingress", action="store_true", help="[post-deploy] AWS_IAM ingress unsigned=403/signed=200 (EC4)"
    )
    group.add_argument("--lambda-idempotency", action="store_true", help="[post-deploy] idempotent ULID append (EC10)")
    group.add_argument("--lambda-partition", action="store_true", help="[post-deploy] partition prune (EC6)")
    group.add_argument("--lambda-inlining", action="store_true", help="[post-deploy] inlining disabled (EC11)")
    group.add_argument("--lambda-loudfail", action="store_true", help="[post-deploy] schema/OCC loud-fail (EC7)")
    group.add_argument("--lambda-churn", action="store_true", help="[post-deploy] invocation fan-out churn/latency gate (EC8)")
    group.add_argument(
        "--lambda-churn-incontainer",
        action="store_true",
        help="[opt-in diagnostic] in-container 8-thread burst (legacy action_churn); NOT an EC8 gate",
    )
    group.add_argument("--lambda-reader", action="store_true", help="[post-deploy] closed reader path (EC1/boundary)")
    group.add_argument(
        "--lambda-row-rules",
        action="store_true",
        help="[post-deploy] rec-4158 plan B: the writer rejects each contract row-rule class (422 row_rule), nothing stored",
    )
    group.add_argument(
        "--lambda-warm-reuse",
        action="store_true",
        dest="lambda_warm_reuse",
        help="[post-deploy] D2 VP8: reader warm-connection reuse (2nd connect near-zero) + forced cold reconnect",
    )
    group.add_argument(
        "--lambda-warm-reuse-writer",
        action="store_true",
        dest="lambda_warm_reuse_writer",
        help="[post-deploy] D2 VP9: writer warm reuse + write-under-reuse commits + cold/warm latency (rec-2096)",
    )
    group.add_argument(
        "--lambda-maintenance-merge",
        action="store_true",
        help="[post-deploy] T2.18 daily merge gate: write small files, invoke merge, assert file count (VP9)",
    )
    group.add_argument(
        "--lambda-maintenance-gc",
        action="store_true",
        help="[post-deploy] T2.18 weekly GC gate: invoke GC, assert storage stable and breaker not tripped (VP10)",
    )
    group.add_argument(
        "--lambda-maintenance-breaker",
        action="store_true",
        help="[post-deploy] T2.18 breaker probe: forced-threshold trip, assert 5xx + breaker_tripped=True (VP11)",
    )
    group.add_argument(
        "--lambda-catalog-dr",
        action="store_true",
        help="[post-deploy] T2.18 FP-B DR gate: invoke DR Lambda, assert dump object + engine-version tag + metric (VP11)",
    )
    group.add_argument(
        "--lambda-maintenance-hot-merge",
        action="store_true",
        help="[post-deploy] T2.18 FP-B hot_merge gate: invoke hot_merge, assert files merged, nothing deleted (VP12)",
    )
    group.add_argument(
        "--lambda-maintenance-create-telemetry",
        action="store_true",
        help="[post-deploy] Decision 216: owner-login create_telemetry_tables on maintenance-smoke, single-owner report",
    )
    group.add_argument(
        "--connect-probe",
        action="store_true",
        dest="connect_probe",
        help="[post-deploy] T2.19 RCA: SigV4-invoke reader+writer connect_probe; print per-phase timings",
    )
    group.add_argument(
        "--migrate-ops-recs-columns",
        action="store_true",
        dest="migrate_ops_recs_columns",
        help="[post-deploy] T1.13 VP8: reconcile_columns SERVER-SIDE via maintenance Lambda; "
        "assert context_v2_json present on history+current (idempotent)",
    )
    group.add_argument(
        "--lambda-append-only",
        action="store_true",
        help="[post-deploy] T1.14 VP gate: write_ops on ops_smoke_events (append_only), "
        "assert 1 history row + no current projection",
    )
    group.add_argument(
        "--lambda-telemetry-production",
        action="store_true",
        help="[post-deploy] production telemetry writer light gate: URL-only, production-bound, writes no test rows",
    )
    group.add_argument(
        "--lambda-telemetry-smoke",
        action="store_true",
        help="[post-deploy] smoke telemetry writer twin URL-only proof and disposable vectors",
    )
    group.add_argument(
        "--lambda-telemetry-reader",
        action="store_true",
        help="[post-deploy] telemetry reader proof: production-pinned, READ_ONLY, no content, no ops verbs",
    )
    group.add_argument(
        "--canary-rehearsal",
        action="store_true",
        dest="canary_rehearsal",
        help="[pre-deploy] OQ.12 full canary rehearsal from CC-web (no TCP/5432): publish candidate layers, "
        "create ephemeral canaries, prove attach + RYW + real-prod read-clone, teardown.",
    )
    parser.add_argument(
        "--project-id",
        default=None,
        dest="project_id",
        help="[--canary-rehearsal] Neon project id; skips the org-level project listing a project-scoped key cannot make "
        "(default: env NEON_PROJECT_ID)",
    )
    parser.add_argument("--profile", default=None, help="AWS profile override for Secrets Manager / S3 creds")
    parser.add_argument("--region", default="eu-west-2", help="AWS region for SigV4 / metrics")
    parser.add_argument(
        "--json", action="store_true", dest="json_output", help="emit machine-readable JSON (warm-reuse gates)"
    )
    args = parser.parse_args(argv)

    try:
        if args.attach:
            rows = attach_roundtrip(profile=args.profile)
            print(f"ATTACH OK rows={rows}")
        elif args.churn_gate:
            m = churn_gate(profile=args.profile)
            print(f"CHURN_GATE PASS collision_rate={m['collision_rate']:.3f} p95_latency_ms={m['p95_latency_ms']:.1f}")
        elif args.restore_drill:
            restore_drill(profile=args.profile)
            print("RESTORE_OK read-your-write verified")
        elif args.ops_read_your_write:
            ops_read_your_write(profile=args.profile, region=args.region)
        elif args.migrate_ops_recs_columns:
            migrate_ops_recs_columns(profile=args.profile, region=args.region)
        elif args.ops_churn_regate:
            ops_churn_regate(profile=args.profile, region=args.region)
        elif args.catalog_restore_drill:
            catalog_restore_drill(profile=args.profile, region=args.region)
        elif args.connect_probe:
            connect_probe(profile=args.profile, region=args.region)
        elif args.lambda_warm_reuse:
            lambda_warm_reuse(profile=args.profile, region=args.region, json_output=args.json_output)
        elif args.lambda_warm_reuse_writer:
            lambda_warm_reuse_writer(profile=args.profile, region=args.region, json_output=args.json_output)
        elif args.canary_rehearsal:
            extra = {"project_id": args.project_id} if args.project_id else {}
            canary_rehearsal(profile=args.profile, region=args.region, json_output=args.json_output, **extra)
        else:
            gate = _selected_lambda_gate(args)
            gate(profile=args.profile, region=args.region)
    except SmokeTestFailure as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def _selected_lambda_gate(args: argparse.Namespace) -> Callable[..., None]:
    """Map the chosen --lambda-* flag to its gate function (resolved live so tests can patch it)."""
    for flag in _LAMBDA_GATES:
        if getattr(args, flag, False):
            return globals()[flag]
    raise SmokeTestFailure("no gate selected")  # pragma: no cover -- argparse mutually-exclusive guard


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
