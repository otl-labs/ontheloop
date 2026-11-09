"""SIT restore rehearsal orchestrator (rec-5023, Decision 251): docs/contracts/restore-rehearsal.yaml sitting.order.

One human-directed ADMIN sitting: dump the PROD catalog, restore it into a throwaway Neon branch under its own role,
rewrite data_path to a scratch prefix, verify every table against PROD and tear everything down in a finally block.
Everything is redacted before it is printed or written; evidence is counts only (Decision 101). Subcommands: run,
version-recovery, verify-evidence FILE.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from typing import Any

from scripts.ducklake_smoke import restore_rehearsal_aws as aws
from scripts.ducklake_smoke import restore_rehearsal_evidence as ev
from src.common import neon_api

META_SCHEMA = "ducklake_ops"
PROBE_EXPECTED_KEYS = ("write_probe_key", "write_probe_in_scratch", "catalog_format_version")


class StepError(aws.RehearsalError):
    """A mechanical step failure: printed as step, error class and code only; writes no evidence."""

    def __init__(self, step: str, error_class: str, code: Any) -> None:
        super().__init__(f"{step} {error_class} {code}")
        self.step, self.error_class, self.code = step, error_class, code


class _State:
    def __init__(self) -> None:
        self.branch_id: str | None = None
        self.restore_fn = False
        self.checksum_fn = False
        self.scratch_started = False


def say(text: str) -> None:
    print(ev.redact(text))


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%S") + "_" + secrets.token_hex(3)


def dry_run_lines(args: argparse.Namespace, run_id: str) -> list[str]:
    scratch = aws.scratch_prefix(run_id)
    return [
        f"RUN_ID {run_id}",
        f"NEON_PROJECT_ID {args.project_id or 'ABSENT'}",
        (
            f"1. [{args.profile}] read /secret/neon-api-key; look up functions, branches and the catalog-dr URL; "
            f"[{args.dev_profile}] list restore-rehearsal/ and read the deploy record (stops if anything is left over)"
        ),
        f"2. [{args.profile}] invoke {aws.CATALOG_DR_FN} through its Function URL: one on-demand catalog dump",
        f"3. [{args.profile}] Neon API: create branch, role and empty database, all named restore_rehearsal_{run_id}",
        (
            f"4. [{args.profile}] create {aws.RESTORE_FN} (role of {aws.SMOKE_MAINTENANCE_FN}) and {aws.CHECKSUM_FN} "
            f"(role of {aws.READER_FN}), 10240 MB /tmp, 900 s, reserved concurrency 1, from the deployed zip"
        ),
        f"5. [{args.dev_profile}] copy ducklake/ into {scratch}ducklake/ and list the three prefixes for sizes",
        (
            f"6. [{args.profile}] presign the dump (15 min) and invoke restore, rewrite, verify, prod_checksum, duckdb_copy, "
            "write_probe"
        ),
        f"7. [{args.dev_profile}] head-object the probe key under ducklake/ (must be absent)",
        (
            f"8. teardown in finally: [{args.profile}] teardown_scratch via the restore function, delete both functions, "
            f"delete the branch; fallback [{args.dev_profile}] scratch deletion through the key builder"
        ),
    ]


def blank_evidence(run_id: str) -> dict[str, Any]:
    e: dict[str, Any] = dict.fromkeys(ev.EVIDENCE_KEYS)
    e.update(run_id=run_id, measured_at=datetime.now(timezone.utc).isoformat(), meta_schemas=[META_SCHEMA], torn_down={})
    return e


class _Sitting:
    def __init__(self, args: argparse.Namespace, run_id: str) -> None:
        self.args, self.run_id, self.state = args, run_id, _State()
        self.admin = aws.session(args.profile, args.region)
        self.dev = aws.session(args.dev_profile, args.region)
        self.token = secrets.token_hex(8)
        ev.register_secret(self.token)
        self.evidence = blank_evidence(run_id)
        self.handles: dict[str, Any] = {}
        self.client: Any = None
        self.data_path = f"s3://{aws.BUCKET}/{aws.scratch_key(run_id, 'ducklake/')}"

    def resolve_handles(self) -> None:
        """Resolve every teardown handle before anything is created; stop on a leftover from an earlier run."""
        if not self.args.project_id:
            raise aws.RehearsalError("NEON_PROJECT_ID is required (--project-id); nothing was created")
        api_key = neon_api.fetch_api_key(self.args.profile)
        ev.register_secret(api_key)
        if aws.leftover_branches(api_key, self.args.project_id):
            raise aws.RehearsalError("a restore_rehearsal branch is left over; clean it by hand first")
        for name in (aws.RESTORE_FN, aws.CHECKSUM_FN):
            if aws.function_exists(self.admin, name):
                raise aws.RehearsalError(f"{name} is left over; clean it by hand first")
        if aws.scratch_objects(self.dev):
            raise aws.RehearsalError("a restore-rehearsal/ scratch prefix is left over; clean it by hand first")
        record = aws.read_deploy_record(self.dev)
        self.handles = {
            "api_key": api_key,
            "dr_url": aws.function_url(self.admin, aws.CATALOG_DR_FN),
            "record": record,
            "zip_key": f"lambda-packages/{record['source_git_sha']}/ducklake-maintenance.zip",
            "prod": aws.function_config(self.admin, aws.PROD_MAINTENANCE_FN),
            "smoke_role": aws.function_config(self.admin, aws.SMOKE_MAINTENANCE_FN)["Role"],
            "reader_role": aws.function_config(self.admin, aws.READER_FN)["Role"],
        }
        self.client = aws.invoker(self.admin)

    def step(self, fn: str, name: str, **fields: Any) -> dict[str, Any]:
        started = time.monotonic()
        payload = {"action": "restore_rehearsal", "step": name, "confirm": self.token, "run_id": self.run_id, **fields}
        body = aws.invoke_step(self.client, fn, payload)
        if body.get("ok") is not True:
            code = body.get("code")
            if code == 28:
                raise aws.LimitHit(name, "enospc_tmp", time.monotonic() - started)
            raise StepError(name, str(body.get("error_class")), code)
        return body

    def dump_and_target(self) -> None:
        started = time.monotonic()
        self.dump = aws.dump_catalog(self.admin, self.handles["dr_url"], self.args.region)
        self.evidence.update(dump_bytes=self.dump.get("dump_bytes"), dump_ms=int((time.monotonic() - started) * 1000))
        target = aws.create_target(
            self.handles["api_key"], self.args.project_id, self.run_id, on_branch=lambda b: setattr(self.state, "branch_id", b)
        )
        self.dsn_uri = target["dsn_uri"]

    def create_functions(self) -> None:
        h, prod = self.handles, self.handles["prod"]
        common = {
            "zip_key": h["zip_key"],
            "layers": prod["Layers"],
            "memory": prod["MemorySize"],
            "profile": self.args.profile,
        }
        env = {"DUCKLAKE_RESTORE_REHEARSAL": self.token}
        self.state.restore_fn = True
        sha_a = aws.create_function(
            self.admin,
            aws.RESTORE_FN,
            role=h["smoke_role"],
            env={**env, "DUCKLAKE_RESTORE_TARGET_DSN": self.dsn_uri},
            region=self.args.region,
            **common,
        )
        self.state.checksum_fn = True
        sha_b = aws.create_function(
            self.admin, aws.CHECKSUM_FN, role=h["reader_role"], env=env, region=self.args.region, **common
        )
        expected = {h["record"]["code_sha256"], prod["CodeSha256"]}
        if {sha_a, sha_b} != expected or len(expected) != 1:
            raise aws.RehearsalError("CodeSha256 of an ephemeral function differs from the deploy record or production")
        self.evidence["maintenance_zip_sha256"] = sha_a

    def copy_data(self) -> None:
        self.state.scratch_started = True
        copied = aws.copy_prefix(self.dev, self.run_id)
        self.evidence.update(copy_objects=copied["objects"], copy_bytes=copied["bytes"], copy_ms=copied["ms"])
        self.evidence["prefix_sizes"] = {p: aws.prefix_size(self.dev, p) for p in aws.COPY_SOURCES}

    def steps(self) -> None:
        e = self.evidence
        e["write_probe_in_production"] = None
        url = self._presign()
        try:
            restored = self.step(aws.RESTORE_FN, "restore", dump_url=url)
        except StepError as exc:
            if not (self.args.restore_final and exc.error_class == "CatalogDrError"):
                raise
            e.update(restore_ok=False, restore_exit_status=exc.code)
            return
        e.update(
            restore_ok=True,
            restore_ms=restored["restore_ms"],
            snapshot_ids={"restored": restored["snapshot_id"]},
            absolute_paths=restored["absolute_paths"],
            foreign_owned=restored["foreign_owned"],
            foreign_acl=restored["foreign_acl"],
            foreign_acl_grantees=restored["foreign_acl_grantees"],
        )
        rewrite = self.step(aws.RESTORE_FN, "rewrite", data_path=self.data_path)
        e.update(rewritten=rewrite["rewritten"], rewrite_ms=rewrite["rewrite_ms"])
        if not rewrite["rewritten"]:
            return
        snapshot = restored["snapshot_id"]
        verified = self.step(aws.RESTORE_FN, "verify", data_path=self.data_path, snapshot_id=snapshot)
        e.update(attach_ok=verified["attach_ok"], missing_files=verified["missing_files"])
        e["ops_tables"] = [
            {
                "table": f"{t['schema']}.{t['table']}",
                "read_ok": t["read_ok"],
                "restored_count": t["count"],
                "restored_checksum": t["checksum"],
                "prod_count": None,
                "prod_checksum": None,
            }
            for t in verified["tables"]
        ]
        if not verified["attach_ok"] or verified["missing_files"] != 0 or not all(t["read_ok"] for t in verified["tables"]):
            return
        tables = [[t["schema"], t["table"]] for t in verified["tables"]]
        prod = self.step(aws.CHECKSUM_FN, "prod_checksum", snapshot_id=snapshot, tables=tables)
        by_name = {f"{t['schema']}.{t['table']}": t for t in prod["tables"]}
        for row in e["ops_tables"]:
            row["prod_count"], row["prod_checksum"] = (by_name[row["table"]]["count"], by_name[row["table"]]["checksum"])
        copied = self.step(aws.RESTORE_FN, "duckdb_copy", data_path=self.data_path, snapshot_id=snapshot, tables=tables)
        e.update(
            duckdb_copy_ms=copied["copy_ms"],
            duckdb_catalog_rows=copied["catalog_rows"],
            duckdb_file_bytes=copied["file_bytes"],
            duckdb_copy_tables=copied["tables"],
        )
        probe = self.step(aws.RESTORE_FN, "write_probe", data_path=self.data_path)
        e.update({k: probe[k] for k in PROBE_EXPECTED_KEYS})
        if probe["write_probe_key"]:
            e["write_probe_in_production"] = aws.head_exists(self.dev, f"ducklake/{probe['write_probe_key']}")

    def _presign(self) -> str:
        neon_api.wait_operations(self.handles["api_key"], self.args.project_id, self.state.branch_id or "")
        return aws.presign_dump(self.admin, self.dump["bucket"], self.dump["s3_key"])

    def teardown(self) -> dict[str, bool]:
        """Scratch first, then functions, then the branch; each flag is true only when its resource is confirmed gone."""
        torn: dict[str, bool] = {}
        torn["scratch"] = self._guarded(self._teardown_scratch)
        for key, flag, name in (
            ("restore_function", self.state.restore_fn, aws.RESTORE_FN),
            ("checksum_function", self.state.checksum_fn, aws.CHECKSUM_FN),
        ):
            torn[key] = (
                self._guarded(lambda n=name: aws.delete_function_confirmed(self.admin, n, profile=self.args.profile))
                if flag
                else True
            )
        torn["branch"] = self._guarded(lambda: self._delete_branch(self.state.branch_id)) if self.state.branch_id else True
        return torn

    def _delete_branch(self, branch_id: str | None) -> bool:
        neon_api.delete_branch(self.handles["api_key"], self.args.project_id, branch_id or "")
        return True

    def _teardown_scratch(self) -> bool:
        if not self.state.scratch_started and not self.state.restore_fn:
            return not aws.scratch_objects(self.dev, self.run_id)
        try:
            if not self.state.restore_fn:
                raise aws.RehearsalError("no function to delete scratch")
            self.step(aws.RESTORE_FN, "teardown_scratch", scratch_prefix=self.data_path)
        except Exception:  # noqa: BLE001 -- fall back to agent_platform through the key builder
            aws.delete_scratch(self.dev, self.run_id)
        return not aws.scratch_objects(self.dev, self.run_id)

    @staticmethod
    def _guarded(fn: Any) -> bool:
        try:
            return bool(fn())
        except Exception as exc:  # noqa: BLE001
            say(f"teardown call failed: {type(exc).__name__}")
            return False


def run(args: argparse.Namespace) -> int:
    run_id = args.run_id or new_run_id()
    if args.dry_run:
        for line in dry_run_lines(args, run_id):
            say(line)
        return 0
    if not args.project_id:
        say("NEON_PROJECT_ID is required (--project-id or env); nothing was created")
        return 1
    sitting = _Sitting(args, run_id)
    result, failure = True, None
    try:
        sitting.resolve_handles()
        sitting.dump_and_target()
        sitting.create_functions()
        sitting.copy_data()
        sitting.steps()
    except aws.LimitHit as hit:
        sitting.evidence["host_limit"] = {"step": hit.step, "limit": hit.limit, "elapsed_s": hit.elapsed_s}
    except StepError as exc:
        result, failure = False, f"STEP_ERROR step={exc.step} error_class={exc.error_class} code={exc.code}"
    except Exception as exc:  # noqa: BLE001 -- redacted: a message can name a host
        result, failure = False, f"RUN_ERROR {type(exc).__name__}: {ev.redact(str(exc))}"
    finally:
        torn = sitting.teardown() if sitting.handles or sitting.state.branch_id else {}
    sitting.evidence["torn_down"] = torn
    if failure:
        say(failure)
    if not result:
        say(f"TORN_DOWN {json.dumps(torn)}")
        return 2
    return _write_and_verify(sitting.evidence, args.evidence_out)


def _write_and_verify(evidence: dict[str, Any], path: str | None) -> int:
    text = json.dumps(ev.redact_value(evidence), indent=1, sort_keys=True)
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    code, lines = ev.verify_text(text)
    for line in lines:
        say(line)
    return code


def version_recovery(args: argparse.Namespace) -> int:
    """Versioning rehearsal as agent_platform, S3 calls only (contract sitting.version_recovery)."""
    if args.profile:
        say("version-recovery runs as the dev profile only; --profile is refused")
        return 1
    dev = aws.session(args.dev_profile, args.region)
    s3 = dev.client("s3")
    run_id = args.run_id or new_run_id()
    key = aws.scratch_key(run_id, "versioning/object.bin")
    data = os.urandom(64)
    digest = hashlib.sha256(data).hexdigest()
    s3.put_object(Bucket=aws.BUCKET, Key=key, Body=data)
    s3.delete_object(Bucket=aws.BUCKET, Key=key)
    versions = s3.list_object_versions(Bucket=aws.BUCKET, Prefix=key).get("Versions", [])
    noncurrent = [v for v in versions if not v.get("IsLatest")]
    if noncurrent:
        s3.copy_object(
            Bucket=aws.BUCKET, Key=key, CopySource={"Bucket": aws.BUCKET, "Key": key, "VersionId": noncurrent[0]["VersionId"]}
        )
    restored = hashlib.sha256(s3.get_object(Bucket=aws.BUCKET, Key=key)["Body"].read()).hexdigest() if noncurrent else ""
    s3.delete_object(Bucket=aws.BUCKET, Key=key)
    evidence = {
        "run_id": run_id,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "object_under_versioning_prefix": key.startswith(f"{aws.scratch_prefix(run_id)}versioning/"),
        "noncurrent_versions": len(noncurrent),
        "sha256_match": restored == digest,
        "deleted_after": not aws.head_exists(dev, key),
    }
    return _write_and_verify(evidence, args.evidence_out)


def verify_evidence(args: argparse.Namespace) -> int:
    with open(args.file, encoding="utf-8") as fh:
        code, lines = ev.verify_text(fh.read())
    for line in lines:
        say(line)
    return code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="restore_rehearsal")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "version-recovery"):
        p = sub.add_parser(name)
        p.add_argument("--profile", default=None, help="admin AWS profile (run only)")
        p.add_argument("--dev-profile", default="agent_platform")
        p.add_argument("--region", default=aws.REGION)
        p.add_argument("--run-id", default=None)
        p.add_argument("--evidence-out", default=None)
        p.add_argument("--project-id", default=os.environ.get("NEON_PROJECT_ID"))
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--restore-final", action="store_true")
    v = sub.add_parser("verify-evidence")
    v.add_argument("file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = {"run": run, "version-recovery": version_recovery, "verify-evidence": verify_evidence}[args.command]
    return handler(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
