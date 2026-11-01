"""Hermetic proof that the live append vectors are right before they ship: the golden payload clears the kernel, each
negative vector fails exactly as expected through the real handler, edge, grammar and adapter over an in-memory store."""

from __future__ import annotations

import copy
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.ducklake_smoke import telemetry_append_vectors as vec
from src.common.ducklake_scd2_schema import load_field_semantics
from src.lambdas.ducklake_telemetry_writer import telemetry_edge as edge
from src.telemetry.append import EventTableSpec, prepare_batch
from src.telemetry.request import parse_append_request
from tests.fixtures.telemetry_append_fakes import TENANT, LiveFake

REF = "synthetic:deploy-smoke-hermetic"
PROJECT = "01BX5ZZKBKACTAV9WEVGEMMVRY"
TAG = "tagfortest"
STARTED = (datetime.now(timezone.utc) - timedelta(seconds=120)).replace(microsecond=123000)


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> LiveFake:
    fake = LiveFake().activate(monkeypatch)
    fake.register(REF, PROJECT)
    return fake


def send(live: LiveFake, body: dict[str, Any]) -> Any:
    return live.invoke("https://fake.invalid", body)


def test_golden_body_carries_an_accepted_status_process_event() -> None:
    rows = vec._rows(vec.golden_body(REF, TAG, STARTED), "telemetry_observations")
    status = [r for r in rows if r.get("name", "").startswith("status:")]
    assert len(status) == 1 and status[0]["observation_type"] == "process_event" and status[0]["severity"] == "warning"
    path = Path(__file__).resolve().parents[2] / "docs/contracts/telemetry_observations.yaml"
    contract = yaml.safe_load(path.read_text(encoding="utf-8"))
    pattern = contract["fields"]["name"]["dq_intent"]["pattern_when"][0]["pattern"]
    assert re.fullmatch(pattern, status[0]["name"])
    assert vec.GOLDEN_ROWS["telemetry_observations"] == len(rows)


def test_golden_payload_clears_the_kernel() -> None:
    semantics = load_field_semantics()
    specs, shapes = edge._specs_and_shapes(semantics)
    golden = vec.golden_body(REF, TAG, STARTED)
    request = parse_append_request(golden, shapes)
    assert [t for t, _ in request.batches] == list(vec.GOLDEN_ROWS) and (request.producer, request.parser_version) == (
        vec.PRODUCER,
        vec.PARSER_VERSION,
    )
    for table, rows in request.batches:
        prepared = prepare_batch(
            specs[table], rows, tenant_id=TENANT, project_id=PROJECT, now=STARTED + timedelta(seconds=120)
        )
        assert (prepared.submitted, prepared.collapsed, len(prepared.rows)) == (
            vec.GOLDEN_ROWS[table],
            0,
            vec.GOLDEN_ROWS[table],
        )
    assert all(isinstance(specs[t], EventTableSpec) for t in vec.GOLDEN_ROWS)
    assert specs["telemetry_transcripts"].rules.content.threshold == vec.INLINE_THRESHOLD_BYTES


def test_each_negative_vector_fails_as_expected(live: LiveFake) -> None:
    golden = vec.golden_body(REF, TAG, STARTED)
    assert send(live, golden).status_code == 200
    stored = dict(live.stored)
    seen = set()
    for vector in vec.negative_vectors(TAG):
        resp = send(live, vector.mutate(copy.deepcopy(golden)))
        body = resp.json()
        assert (resp.status_code, body["error_type"]) == (vector.status, vector.error_type), vector.name
        if vector.field is not None:
            assert body[vector.field] == vector.value, vector.name
        assert "smoke prompt" not in str(body) and "\x00" not in str(body)
        seen.add(vector.name)
    assert len(seen) == len(vec.negative_vectors(TAG)) and live.stored == stored


def test_stored_row_conflict_resend_and_omission(live: LiveFake) -> None:
    golden = vec.golden_body(REF, TAG, STARTED)
    first = send(live, golden).json()
    assert first["ok"] is True and first["project_registration"] == "existing"
    assert {t: v["inserted"] for t, v in first["tables"].items()} == vec.GOLDEN_ROWS
    again = send(live, golden).json()
    assert all(v["inserted"] == 0 for v in again["tables"].values())
    stored = dict(live.stored)
    conflict = send(live, vec.conflicting_body(golden))
    body = conflict.json()
    assert (conflict.status_code, body["error_type"], body["table"], body["row"]) == (
        409,
        "grain_conflict",
        vec.CONFLICT_TABLE,
        vec.CONFLICT_ROW,
    )
    assert live.stored == stored
    assert send(live, vec.omission_body(REF, "omit", STARTED)).status_code == 200


def test_size_vectors_hit_their_measures(live: LiveFake) -> None:
    near = vec.size_body(REF, "near", STARTED, vec.MAX_INVOKE_BODY_BYTES)
    assert vec.measure(near) == vec.MAX_INVOKE_BODY_BYTES
    assert all(len(r["content"].encode()) <= vec.INLINE_THRESHOLD_BYTES for r in near["batches"][0]["rows"])
    assert send(live, near).status_code == 200
    over = vec.size_body(REF, "over", STARTED, vec.OVER_CAP_TARGET)
    assert vec.OVER_CAP_TARGET > vec.MAX_INVOKE_BODY_BYTES and vec.measure(over) == vec.OVER_CAP_TARGET
    resp = send(live, over)
    assert (resp.status_code, resp.json()["error_type"], resp.json()["measure"]) == (413, "payload_too_large", "invoke_bytes")
    capped = send(live, vec.row_cap_body(REF, "rows", STARTED))
    assert capped.status_code == 200 and capped.json()["tables"][vec.CONFLICT_TABLE]["inserted"] == vec.MAX_ROWS_PER_TABLE
    assert vec.MAX_ROWS_PER_TABLE == edge.MAX_ROWS_PER_TABLE and vec.MAX_INVOKE_BODY_BYTES == edge.MAX_INVOKE_BODY_BYTES


def test_unregistered_ref_and_concurrent_first_sight(live: LiveFake) -> None:
    golden = vec.golden_body("repo:fresh", TAG, STARTED)
    with ThreadPoolExecutor(max_workers=3) as pool:
        bodies = [r.json() for r in pool.map(lambda _i: send(live, copy.deepcopy(golden)), range(3))]
    assert all(b["ok"] for b in bodies) and len({b["project_id"] for b in bodies}) == 1
    assert sum(b["project_registration"] == "created" for b in bodies) == 1
    drill = send(live, vec.negative_vectors(TAG)[-1].mutate(copy.deepcopy(golden)))
    assert (drill.status_code, drill.json()["error_type"]) == (403, "project_unregistered")
