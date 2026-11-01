"""append_telemetry live vectors for the --lambda-telemetry-smoke gate, run against the smoke twin (T2.36, Decision 236).

Called by lambda_telemetry_smoke after part 1's registration vectors with part 1's freshly registered synthetic project_ref, so
every data vector writes under an explicitly registered synthetic project and the gate never mints a real-looking one.
The one exception is the auto-registration vector, which sends concurrent first-sight appends to a fresh NON-reserved ref
and therefore runs only against the smoke twin (Decision 236 cl.1), never the production writer.

Each vector is a loud SmokeTestFailure (Decision 55). Output carries markers, ids-free counts and bounded-precision
measurements only: every *_ms is an integer, every ratio has three decimals, and no project_ref, URL, host, account id or
response body is ever printed, failure text included.
"""

from __future__ import annotations

import copy
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from scripts.ducklake_smoke import core
from scripts.ducklake_smoke import telemetry_append_vectors as vec
from scripts.ducklake_smoke.lambda_telemetry_gates import _ULID, _body, _diagnostics, _error_type, _expect_ok, _fail

_CONCURRENT_APPENDS = 3
_EVENT_TABLES = tuple(vec.GOLDEN_ROWS)


class _Gate:
    def __init__(self, url: str, profile: str | None, region: str) -> None:
        self.url, self.profile, self.region = url, profile, region
        moment = datetime.now(timezone.utc) - timedelta(seconds=120)
        self.started = moment.replace(microsecond=(moment.microsecond // 1000) * 1000)

    def post(self, payload: dict[str, Any]) -> Any:
        return core._sigv4_invoke(self.url, payload, profile=self.profile, region=self.region)

    def probe(self, project_ref: str, tag: str) -> dict[str, Any]:
        payload = {
            "action": "telemetry_probe",
            "project_ref": project_ref,
            "session_ref": vec.session_ref(tag),
            "session_started_at": vec.wire_ts(self.started),
        }
        return _expect_ok(self.post(payload), "telemetry_probe grain_counts")


def _counts(probe: dict[str, Any], check: str) -> dict[str, dict[str, int]]:
    counts = probe.get("grain_counts")
    if not isinstance(counts, dict):
        raise _fail(f"{check}: telemetry_probe grain_counts missing")
    return counts


def _files(probe: dict[str, Any]) -> int:
    files = probe.get("data_files")
    if not isinstance(files, dict):
        return 0
    return sum(int(v.get("files", 0)) for t, v in files.items() if t in _EVENT_TABLES and isinstance(v, dict))


def _commit_ms(body: dict[str, Any], check: str) -> int:
    """The response's commit_ms as an integer (a float is rounded: a raw float repr can carry a 12-digit run)."""
    value = body.get("commit_ms")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(f"{check}: commit_ms is not a number")
    return int(round(value))


def _tables(body: dict[str, Any], check: str) -> dict[str, dict[str, int]]:
    tables = body.get("tables")
    if not isinstance(tables, dict) or set(tables) != set(_EVENT_TABLES):
        raise _fail(f"{check}: response tables are not the four event tables")
    return tables


def _golden(gate: _Gate, project_ref: str, tag: str) -> dict[str, Any]:
    return vec.golden_body(project_ref, tag, gate.started)


def _check_golden(gate: _Gate, project_ref: str, tag: str) -> dict[str, Any]:
    golden = _golden(gate, project_ref, tag)
    before = _files(gate.probe(project_ref, tag))
    body = _expect_ok(gate.post(golden), "append_telemetry")
    tables = _tables(body, "append_telemetry")
    if body.get("project_registration") != "existing":
        raise _fail("append_telemetry: the explicitly registered synthetic project was not found as existing")
    for table, want in vec.GOLDEN_ROWS.items():
        got = tables[table]
        if got.get("submitted") != want or got.get("inserted") != want or got.get("collapsed") != 0:
            raise _fail(f"append_telemetry: {table} submitted/collapsed/inserted differ from the golden payload")
    after = gate.probe(project_ref, tag)
    for table, want in vec.GOLDEN_ROWS.items():
        entry = _counts(after, "append_telemetry").get(table) or {}
        if entry.get("rows") != want or entry.get("keys") != want:
            raise _fail(f"append_telemetry: {table} grain_counts differ from the golden payload")
    print("TELEMETRY_APPEND_OK")
    print(f"append_files_per_commit={max(_files(after) - before, 0)}")
    print(f"append_commit_ms={_commit_ms(body, 'append_telemetry')}")
    return golden


def _check_resend(gate: _Gate, golden: dict[str, Any]) -> None:
    body = _expect_ok(gate.post(golden), "append_telemetry re-send")
    if any(t.get("inserted") != 0 for t in _tables(body, "re-send").values()):
        raise _fail("re-send inserted rows (an identical re-send must be a no-op)")
    print("TELEMETRY_RESEND_NOOP")


def _check_conflict(gate: _Gate, project_ref: str, tag: str, golden: dict[str, Any]) -> None:
    before = _counts(gate.probe(project_ref, tag), "grain conflict")
    resp = gate.post(vec.conflicting_body(golden))
    body = _body(resp)
    if resp.status_code != 409 or _error_type(resp) != "grain_conflict":
        raise _fail(f"grain conflict status={resp.status_code} error_type={_error_type(resp)} {_diagnostics(resp)} (want 409)")
    if body.get("table") != vec.CONFLICT_TABLE or body.get("row") != vec.CONFLICT_ROW:
        raise _fail("grain conflict did not name the conflicting table and row")
    if _counts(gate.probe(project_ref, tag), "grain conflict") != before:
        raise _fail("grain conflict changed the stored rows")
    print("TELEMETRY_GRAIN_CONFLICT_OK")


def _check_rejects(gate: _Gate, project_ref: str, tag: str, golden: dict[str, Any]) -> None:
    before = _counts(gate.probe(project_ref, tag), "rejection vectors")
    for vector in vec.negative_vectors(tag):
        resp = gate.post(vector.mutate(copy.deepcopy(golden)))
        if resp.status_code != vector.status or _error_type(resp) != vector.error_type:
            raise _fail(
                f"{vector.name} status={resp.status_code} error_type={_error_type(resp)} {_diagnostics(resp)} "
                f"(want {vector.status} {vector.error_type})"
            )
        if vector.field is not None and _body(resp).get(vector.field) != vector.value:
            raise _fail(f"{vector.name} did not name {vector.field}={vector.value}")
    if _counts(gate.probe(project_ref, tag), "rejection vectors") != before:
        raise _fail("a rejected request changed the stored rows")
    print("TELEMETRY_REJECTS_OK")


def _check_omission(gate: _Gate, project_ref: str) -> None:
    _expect_ok(gate.post(vec.omission_body(project_ref, uuid.uuid4().hex, gate.started)), "omission row")


def _check_near_cap(gate: _Gate, project_ref: str) -> None:
    near = vec.size_body(project_ref, uuid.uuid4().hex, gate.started, vec.MAX_INVOKE_BODY_BYTES)
    body = _expect_ok(gate.post(near), "near-cap append")
    raw = len(json.dumps(near).encode("utf-8"))
    print(f"near_cap_commit_ms={_commit_ms(body, 'near-cap append')}")
    print(f"near_cap_inflation={vec.measure(near) / raw:.3f}")
    over = vec.size_body(project_ref, uuid.uuid4().hex, gate.started, vec.OVER_CAP_TARGET)
    resp = gate.post(over)
    if resp.status_code != 413 or _error_type(resp) != "payload_too_large" or _body(resp).get("measure") != "invoke_bytes":
        raise _fail(
            f"over-cap request status={resp.status_code} error_type={_error_type(resp)} {_diagnostics(resp)} (want 413)"
        )
    print("TELEMETRY_NEAR_CAP_OK")


def _check_row_cap(gate: _Gate, project_ref: str) -> None:
    body = _expect_ok(gate.post(vec.row_cap_body(project_ref, uuid.uuid4().hex, gate.started)), "row-cap append")
    tables = body.get("tables")
    table = tables.get(vec.CONFLICT_TABLE) if isinstance(tables, dict) else None
    table = table if isinstance(table, dict) else {}
    if table.get("inserted") != vec.MAX_ROWS_PER_TABLE:
        raise _fail("row-cap append did not insert the 2000 submitted rows")
    print(f"rowcap_commit_ms={_commit_ms(body, 'row-cap append')}")
    print("TELEMETRY_ROWCAP_OK")


def _check_autoregister(gate: _Gate) -> None:
    probe = _expect_ok(gate.post({"action": "telemetry_probe"}), "telemetry_probe")
    if probe.get("data_path_is_smoke") is not True:
        raise _fail("auto-registration vector requires the smoke binding (2c must replace it before production)")
    tag = uuid.uuid4().hex
    ref = f"repo:deploy-smoke-auto-{tag}"
    golden = _golden(gate, ref, tag)
    with ThreadPoolExecutor(max_workers=_CONCURRENT_APPENDS) as pool:
        responses = list(pool.map(lambda _i: gate.post(copy.deepcopy(golden)), range(_CONCURRENT_APPENDS)))
    bodies = [_expect_ok(r, "concurrent first-sight append") for r in responses]
    ids = {b.get("project_id") for b in bodies}
    if len(ids) != 1 or not all(isinstance(i, str) and _ULID.match(i) for i in ids):
        raise _fail(f"concurrent first-sight appends returned {len(ids)} project ids (Decision 200 reversal condition)")
    created = sum(1 for b in bodies if b.get("project_registration") == "created")
    if created != 1:
        raise _fail(f"concurrent first-sight appends created {created} projects (want exactly 1)")
    counts = _counts(gate.probe(ref, tag), "auto-registration")
    for table, want in vec.GOLDEN_ROWS.items():
        entry = counts.get(table) or {}
        if entry.get("keys") != want or not isinstance(entry.get("rows"), int) or entry["rows"] < want:
            raise _fail(f"auto-registration: {table} distinct grain keys differ from one copy of the payload")
    print("TELEMETRY_AUTOREGISTER_OK")


def lambda_telemetry_append(url: str, project_ref: str, *, profile: str | None = None, region: str = "eu-west-2") -> None:
    """Run part 2's append vectors against the registered synthetic project_ref (never printed)."""
    gate = _Gate(url, profile, region)
    tag = uuid.uuid4().hex
    golden = _check_golden(gate, project_ref, tag)
    _check_resend(gate, golden)
    _check_conflict(gate, project_ref, tag, golden)
    _check_rejects(gate, project_ref, tag, golden)
    _check_omission(gate, project_ref)
    _check_near_cap(gate, project_ref)
    _check_row_cap(gate, project_ref)
    _check_autoregister(gate)
