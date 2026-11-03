"""Ops-schema end-to-end Function-URL gates for the DuckLake Neon smoke suite (T2.19 / T1.13 / T1.14).

Closed-boundary read-your-write proof on the real ops schema, the EC8 churn re-gate at production
scope, the Lambda-mediated catalog restore drill, the ops_recommendations column migration, the
connect-probe RCA diagnostic, and the append-only write-mode gate. Gates call core._function_url /
core._sigv4_invoke / core._ok_json through the module object (Decision 104); ops_churn_regate
delegates to lambda_ec_gates.lambda_churn the same way (one canonical patch target per symbol).
"""

from __future__ import annotations

import subprocess
from uuid import uuid4

from scripts.ducklake_smoke import core, lambda_ec_gates


def ops_read_your_write(*, profile: str | None = None, region: str = "eu-west-2") -> None:
    """T2.19 VP11: write via the writer (write_ops) -> read via the reader (read_ops_current).

    Proves the closed boundary end-to-end on the real ops schema: a write_ops lands and read_ops_current
    returns it; an update_ops is reflected; an update_ops on an ABSENT key loud-fails 409 (referential,
    CD.33 cl.8). Uses a `test-` probe id so the production counter is untouched.
    """
    writer_url = core._function_url("writer")
    reader_url = core._function_url("reader")
    table = "ops_recommendations"
    probe_id = f"test-ryw-{uuid4().hex[:12]}"
    base = {
        "id": probe_id,
        "status": "open",
        "title": "ops read-your-write probe",
        "source": "manual",
        "effort": "XS",
        "priority": "Low",
        "risk": "low",
        # DQ-required NOT-NULL columns: populated so the probe row is data-quality-clean while it
        # persists (the writer has no delete verb -- postmortem-DELETE deferred). Without these the
        # probe trips the ops_recommendations not_null DQ checks and reds the verifier harness.
        "automatable": False,
        "file": "scripts/ducklake_neon_smoke_test.py",
        "context": (
            "Read-your-write smoke probe written by ducklake_neon_smoke_test --ops-read-your-write "
            "to prove the closed DuckLake writer/reader boundary end-to-end on the real ops schema."
        ),
        "acceptance": "grep -q ops_read_your_write scripts/ducklake_neon_smoke_test.py",
    }
    core._ok_json(
        core._sigv4_invoke(writer_url, {"action": "write_ops", "table": table, "record": base}, profile=profile, region=region)
    )
    read1 = core._ok_json(
        core._sigv4_invoke(
            reader_url, {"action": "read_ops_current", "table": table, "key": probe_id}, profile=profile, region=region
        )
    )
    if read1.get("row_count") != 1 or read1["rows"][0].get("status") != "open":
        raise core.SmokeTestFailure(f"OPS_RYW FAIL: write_ops not read back: {read1}")

    updated = {**base, "status": "closed"}
    core._ok_json(
        core._sigv4_invoke(
            writer_url, {"action": "update_ops", "table": table, "record": updated}, profile=profile, region=region
        )
    )
    read2 = core._ok_json(
        core._sigv4_invoke(
            reader_url, {"action": "read_ops_current", "table": table, "key": probe_id}, profile=profile, region=region
        )
    )
    if read2["rows"][0].get("status") != "closed":
        raise core.SmokeTestFailure(f"OPS_RYW FAIL: update_ops not reflected: {read2}")

    absent = {**base, "id": f"test-absent-{uuid4().hex[:8]}", "status": "closed"}
    resp = core._sigv4_invoke(
        writer_url, {"action": "update_ops", "table": table, "record": absent}, profile=profile, region=region
    )
    if resp.status_code != 409:
        raise core.SmokeTestFailure(
            f"OPS_RYW FAIL: update_ops on absent rec returned {resp.status_code} (expected 409 referential)"
        )

    superseded = {
        **updated,
        "status": "superseded",
        "resolution": "Superseded by --ops-read-your-write on successful read-back.",
    }
    core._ok_json(
        core._sigv4_invoke(
            writer_url, {"action": "update_ops", "table": table, "record": superseded}, profile=profile, region=region
        )
    )
    print(f"OPS_RYW OK write+read+update reflected; absent-update referential=409 probe_id={probe_id} superseded=true")


_ROW_RULES_TABLE = "ops_recommendations"
ROW_RULES_BASE: dict[str, object] = {
    "status": "open",
    "title": "row-rules gate probe",
    "source": "manual",
    "effort": "XS",
    "priority": "Low",
    "risk": "low",
    "automatable": False,
    "file": "scripts/ducklake_neon_smoke_test.py",
    "context": (
        "Row-rules gate probe written by ducklake_neon_smoke_test --lambda-row-rules to prove the deployed "
        "writer rejects a record that breaks a contract-declared row rule."
    ),
    "acceptance": "grep -q lambda_row_rules scripts/ducklake_neon_smoke_test.py",
}
# (expected rule, record overrides, expected column): each case breaks exactly one rule of an otherwise clean record
ROW_RULES_CASES: tuple[tuple[str, dict[str, object], str], ...] = (
    ("accepted_values", {"effort": "XXL"}, "effort"),
    ("min_length", {"title": "short"}, "title"),
    ("array_element_format", {"dependencies": ["not-a-rec-id"]}, "dependencies"),
    ("pattern", {"file": "/absolute/path.py"}, "file"),
)


DORMANT_TABLES = ("ops_priority_queue", "ops_execution_plans")
DORMANT_VERBS = ("write_ops", "file_ops", "update_ops")
# every (table, verb) pair the writer must refuse until the table's Class A contract lands (Decision 210 cl.4)
DORMANT_REFUSAL_CASES: tuple[tuple[str, str], ...] = tuple((t, v) for t in DORMANT_TABLES for v in DORMANT_VERBS)


def lambda_dormant_refusal(writer_url: str, *, profile: str | None = None, region: str = "eu-west-2") -> int:
    """Each dormant (table, verb) pair must answer 400 naming dormant; any other answer fails the gate.

    The probe record carries an unknown field, so a regressed boundary still cannot write: write_ops and update_ops fail
    the schema gate first (422 schema_gate) and file_ops fails before it on the missing writer-owned keyspace (500
    runtime), so a failed probe never leaves a row.
    """
    for table, verb in DORMANT_REFUSAL_CASES:
        record = {"id": f"test-dormant-{uuid4().hex[:12]}", "unknown_probe_field": "x"}
        resp = core._sigv4_invoke(
            writer_url, {"action": verb, "table": table, "record": record}, profile=profile, region=region
        )
        error = str(resp.json().get("error", "")) if resp.status_code == 400 else ""
        if resp.status_code != 400 or "dormant" not in error:
            raise core.SmokeTestFailure(
                f"LAMBDA_ROW_RULES FAIL: {verb} on dormant {table} answered {resp.status_code} instead of 400 naming dormant: "
                f"{resp.text[:200]}"
            )
    return len(DORMANT_REFUSAL_CASES)


def lambda_row_rules(*, profile: str | None = None, region: str = "eu-west-2") -> None:
    """rec-4158 plan B per-deploy gate: the deployed writer rejects each rule class and writes nothing.

    From a rule-clean test-rr- base record, sends one write_ops per ROW_RULES_CASES entry; each must answer 422
    row_rule naming the expected rule and column, then read_ops_current on the key must return zero rows. On an
    unexpected answer it first supersedes any row it wrote (update_ops to the rule-clean base) so a failed probe never
    leaves a census violator in current, then raises SmokeTestFailure.
    """
    writer_url = core._function_url("writer")
    reader_url = core._function_url("reader")
    key = f"test-rr-{uuid4().hex[:12]}"
    base = {**ROW_RULES_BASE, "id": key}

    def stored_rows() -> int:
        body = core._ok_json(
            core._sigv4_invoke(
                reader_url,
                {"action": "read_ops_current", "table": _ROW_RULES_TABLE, "key": key},
                profile=profile,
                region=region,
            )
        )
        return int(body.get("row_count", 0))

    def repair_and_fail(message: str) -> None:
        if stored_rows():
            superseded = {
                **base,
                "status": "superseded",
                "resolution": f"Superseded by a failed --lambda-row-rules gate: {message}",
            }
            core._ok_json(
                core._sigv4_invoke(
                    writer_url,
                    {"action": "update_ops", "table": _ROW_RULES_TABLE, "record": superseded},
                    profile=profile,
                    region=region,
                )
            )
        raise core.SmokeTestFailure(f"LAMBDA_ROW_RULES FAIL: {message}")

    for rule, overrides, column in ROW_RULES_CASES:
        resp = core._sigv4_invoke(
            writer_url,
            {"action": "write_ops", "table": _ROW_RULES_TABLE, "record": {**base, **overrides}},
            profile=profile,
            region=region,
        )
        body = resp.json() if resp.status_code == 422 else {}
        if resp.status_code != 422 or body.get("error_type") != "row_rule":
            repair_and_fail(f"{rule} case answered {resp.status_code} instead of 422 row_rule: {resp.text[:200]}")
        if body.get("rule") != rule or body.get("column") != column:
            repair_and_fail(
                f"{rule} case named rule={body.get('rule')!r} column={body.get('column')!r}, expected {rule}/{column}"
            )
    if stored_rows():
        repair_and_fail("a rejected write left a row in current")
    dormant = lambda_dormant_refusal(writer_url, profile=profile, region=region)
    print(
        f"LAMBDA_ROW_RULES OK {len(ROW_RULES_CASES)} cases rejected 422 row_rule, nothing stored key={key}; "
        f"{dormant} dormant (table, verb) cases refused 400"
    )


def ops_churn_regate(*, profile: str | None = None, region: str = "eu-west-2") -> None:
    """T2.19 VP12: re-run the Decision-82 EC8 churn/OCC gate at production scope (post-cutover catalog).

    Delegates to the EC8 fan-out (CHURN_WRITERS=4, per-invocation wall p95<=2000ms, collision<=0.20 --
    the single-source budgets in ducklake_runtime). Production scope = the post-cutover production data
    path; the contention measured is catalog-commit-level (table-independent). Loud-fail on breach
    (Decision 55 -- never relax the budget to commit_ms).
    """
    lambda_ec_gates.lambda_churn(profile=profile, region=region)
    print("OPS_CHURN_REGATE OK (EC8 fan-out within CD.33 budget at production scope)")


def catalog_restore_drill(*, profile: str | None = None, region: str = "eu-west-2") -> None:
    """T2.19 VP11: invoke the maintenance `restore_drill` action (pg_dump -> pg_restore + read-your-write).

    Lambda-mediated over 443 (there is NO Neon 5432 egress from CC-web): the maintenance Lambda runs the
    custom-format pg_dump -> pg_restore into a scratch meta-schema and verifies read-your-write INSIDE
    AWS, version-matched to the pinned engine. Loud-fail on a non-ok response (Decision 55).
    """
    maint_url = core._function_url("maintenance")
    body = core._ok_json(core._sigv4_invoke(maint_url, {"action": "restore_drill"}, profile=profile, region=region))
    if not body.get("restored"):
        raise core.SmokeTestFailure(f"CATALOG_RESTORE_DRILL FAIL: maintenance restore_drill did not restore: {body}")
    print(
        f"CATALOG_RESTORE_DRILL OK maintenance restore_drill read-your-write verified "
        f"probe={body.get('probe_id')} pg={body.get('pg_version')}"
    )


def migrate_ops_recs_columns(*, profile: str | None = None, region: str = "eu-west-2") -> None:
    """T1.13 VP step 8: invoke maintenance reconcile_columns SERVER-SIDE and assert context_v2_json is present.

    Uses the Lambda-mediated pattern (same as ops-read-your-write) because CC-web has no Neon 5432
    egress -- the DDL runs server-side inside the maintenance Lambda against the production catalog.
    Asserts the response reports context_v2_json present on BOTH history and current tables.
    Idempotent: a second run reports added_history=[] / added_current=[] (no-op).
    """
    import os  # noqa: PLC0415

    maint_url = core._function_url("maintenance")
    data_path_env = os.environ.get("DUCKLAKE_DATA_PATH")
    try:
        tf_result = subprocess.run(
            ["terraform", "-chdir=terraform/personal", "output", "-raw", "ducklake_writer_data_path"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        tf_data_path = tf_result.stdout.strip() if tf_result.returncode == 0 else None
    except FileNotFoundError:
        tf_data_path = None

    data_path = data_path_env or tf_data_path or "s3://agent-platform-data-lake/ducklake/"
    payload = {
        "action": "reconcile_columns",
        "data_path": data_path,
        "meta_schema": "ducklake_ops",
        "table": "ops_recommendations",
    }
    body = core._ok_json(core._sigv4_invoke(maint_url, payload, profile=profile, region=region))
    if not body.get("ok"):
        raise core.SmokeTestFailure(f"MIGRATE_OPS_RECS_COLUMNS FAIL: maintenance reconcile_columns returned ok=False: {body}")
    added_h = body.get("added_history", [])
    added_c = body.get("added_current", [])
    pre_existing = body.get("columns_pre_existing", {})
    # After reconcile, context_v2_json must be present on both tables.
    # If the column was just added, it's in added_*. If it was already there, added_* is empty
    # but columns_pre_existing shows True (no-op run). Check both: newly added OR already present.
    history_ok = "context_v2_json" in added_h or pre_existing.get("history") is True
    current_ok = "context_v2_json" in added_c or pre_existing.get("current") is True
    if not history_ok or not current_ok:
        raise core.SmokeTestFailure(
            f"MIGRATE_OPS_RECS_COLUMNS FAIL: context_v2_json not confirmed on "
            f"history={history_ok} current={current_ok}. Response: {body}"
        )
    print(
        f"MIGRATE_OPS_RECS_COLUMNS OK context_v2_json present on history+current "
        f"added_history={added_h} added_current={added_c}"
    )


# NOTE: the seed_ops_recommendations payload emitter (emit_recs_seed_payload) and its
# --emit-recs-seed-payload flag were REMOVED at the 2026-06-09 recs sign-off alongside the maintenance
# seed action (closed boundary, Decision 81 cl.7). Re-seeding is now a break-glass operation: git-revert
# the removal commit (restores BOTH the maintenance action and this emitter), redeploy, re-seed, then
# re-remove. See docs/runbooks/ducklake-catalog-operations.md Section 6.


def connect_probe(*, profile: str | None = None, region: str = "eu-west-2") -> None:
    """T2.19 RCA: SigV4-invoke the reader AND writer connect_probe actions; print the phased results.

    This is a diagnostic driver, NOT a pass/fail gate -- it reports the failing phase even on a
    diagnosed failure (ok=False). Both the reader and writer are probed so the failing phase is
    captured from the load-bearing read path (reader) AND the write path (writer).
    """
    reader_resp = core._sigv4_invoke(core._function_url("reader"), {"action": "connect_probe"}, profile=profile, region=region)
    writer_resp = core._sigv4_invoke(core._function_url("writer"), {"action": "connect_probe"}, profile=profile, region=region)
    reader_body = core._ok_json(reader_resp)
    writer_body = core._ok_json(writer_resp)
    print(
        f"CONNECT_PROBE reader=phase_reached:{reader_body.get('phase_reached')} "
        f"failed_phase:{reader_body.get('failed_phase')} ok:{reader_body.get('ok')} "
        f"dns_ms:{reader_body.get('dns_ms')} tcp_ms:{reader_body.get('tcp_ms')} "
        f"auth_ms:{reader_body.get('auth_ms')} attach_ms:{reader_body.get('attach_ms')} "
        f"error:{reader_body.get('error')!r}"
    )
    print(
        f"CONNECT_PROBE writer=phase_reached:{writer_body.get('phase_reached')} "
        f"failed_phase:{writer_body.get('failed_phase')} ok:{writer_body.get('ok')} "
        f"dns_ms:{writer_body.get('dns_ms')} tcp_ms:{writer_body.get('tcp_ms')} "
        f"auth_ms:{writer_body.get('auth_ms')} attach_ms:{writer_body.get('attach_ms')} "
        f"error:{writer_body.get('error')!r}"
    )


def lambda_append_only(*, profile: str | None = None, region: str = "eu-west-2") -> None:
    """T1.14 VP gate: append-only write mode -- fully Lambda-mediated (no direct Neon egress required).

    Four assertions (writer + reader Lambda; no direct Neon port-5432 egress needed from CC-web):
    1. create_ops_tables reports tables=[history, None] -- no current_table in the ScdTableSpec.
    2. write_ops on ops_smoke_events succeeds (ok=True, ulid minted) -- history MERGE fired.
    2b. read_ops_history confirms one history row written (plan acceptance: 'one history row read back').
    3. update_ops returns AppendOnlyUpdateError (5xx) -- write-once enforcement (Decision 70).
    """
    import uuid  # noqa: PLC0415

    writer_url = core._function_url("writer")
    reader_url = core._function_url("reader")
    table = "ops_smoke_events"

    # Assertion 1: create_ops_tables reports tables=[history, None] (no current projection).
    create_resp = core._sigv4_invoke(
        writer_url, {"action": "create_ops_tables", "table": table}, profile=profile, region=region
    )
    create_body = create_resp.json()
    tables_list = create_body.get("tables", [])
    if not create_body.get("ok") or len(tables_list) < 2 or tables_list[1] is not None:
        raise core.SmokeTestFailure(
            f"LAMBDA_APPEND_ONLY FAIL (assert 1): expected tables=[history, null], got: {tables_list}. body={create_body}"
        )

    # Assertion 2: write_ops succeeds on append_only table (history MERGE fired, ULID minted).
    event_id = f"test-ao-{uuid.uuid4().hex[:12]}"
    write_body = core._ok_json(
        core._sigv4_invoke(
            writer_url,
            {"action": "write_ops", "table": table, "record": {"event_id": event_id, "event_type": "smoke"}},
            profile=profile,
            region=region,
        )
    )
    if not write_body.get("ok") or not write_body.get("ulid"):
        raise core.SmokeTestFailure(
            f"LAMBDA_APPEND_ONLY FAIL (assert 2): write_ops returned ok=False or no ulid: {write_body}"
        )

    # Assertion 2b: read_ops_history confirms exactly one row in ops_smoke_events_history (plan: 'one history row read back').
    read_body = core._ok_json(
        core._sigv4_invoke(
            reader_url,
            {"action": "read_ops_history", "table": table, "key": event_id},
            profile=profile,
            region=region,
        )
    )
    if read_body.get("row_count", 0) != 1:
        raise core.SmokeTestFailure(
            f"LAMBDA_APPEND_ONLY FAIL (assert 2b read-back): expected row_count=1 in "
            f"ops_smoke_events_history for event_id={event_id!r}, got: {read_body}"
        )

    # Assertion 3: update_ops loud-fails with AppendOnlyUpdateError (write-once, Decision 70).
    guard_resp = core._sigv4_invoke(
        writer_url,
        {"action": "update_ops", "table": table, "record": {"event_id": event_id, "event_type": "update-attempt"}},
        profile=profile,
        region=region,
    )
    guard_body = guard_resp.json()
    if guard_body.get("ok") is not False or "append_only" not in guard_body.get("error", ""):
        raise core.SmokeTestFailure(
            f"LAMBDA_APPEND_ONLY FAIL (assert 3): update_ops should fail with append_only guard: {guard_body}"
        )

    print(
        f"LAMBDA_APPEND_ONLY OK no_current_table=true write_ops=ok ulid={write_body.get('ulid')!r} "
        f"read_back=1_row append_only_guard=raised event_id={event_id}"
    )
