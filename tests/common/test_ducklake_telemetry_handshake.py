"""Mirror test for src/common/ducklake_telemetry_handshake.py: describe's capabilities derive from the bundled projection."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any

import pytest

from src.common import ducklake_telemetry_handshake as handshake
from tests.fixtures.telemetry_append_fakes import specs

TABLES = {"telemetry_sessions", "telemetry_observations", "telemetry_transcripts", "telemetry_agents"}


def caps() -> dict[str, Any]:
    all_specs = specs()
    return handshake.capabilities(all_specs, all_specs["telemetry_transcripts"].rules.content)


def test_capabilities_advertise_protocol_and_schema_view() -> None:
    body = caps()
    assert body["blob_protocol"] == {
        "version": 1,
        "inline_threshold_bytes": 65536,
        "cap_bytes": 8388608,
        "max_items": 200,
        "grant_ttl_seconds": 900,
    }
    assert set(body["schema"]) == TABLES
    for entry in body["schema"].values():
        assert set(entry) == {"caller_known_columns", "not_null", "accepted_values", "fingerprint"}
        assert entry["caller_known_columns"] == sorted(entry["caller_known_columns"])
        assert len(entry["fingerprint"]) == 64
    assert "parser_version" not in body and "contract_version" not in json.dumps(body)
    assert json.loads(json.dumps(body)) == body


def test_schema_view_comes_from_the_bundled_projection() -> None:
    for table, entry in caps()["schema"].items():
        spec = specs()[table]
        assert entry["caller_known_columns"] == sorted(spec.caller_known_columns)
        assert entry["not_null"] == sorted(spec.not_null)
        assert entry["accepted_values"] == {c: sorted(v) for c, v in spec.rules.accepted_values.items()}
    transcripts = caps()["schema"]["telemetry_transcripts"]
    assert "content" in transcripts["caller_known_columns"] and "transcript_id" not in transcripts["caller_known_columns"]
    assert "prompt" in transcripts["accepted_values"]["purpose"]
    rule = specs()["telemetry_transcripts"].rules.content
    with pytest.raises(ValueError, match="no cap"):
        handshake.capabilities(specs(), replace(rule, cap=None))


def test_fingerprint_tracks_the_view() -> None:
    first = caps()
    assert first == caps()
    view = {k: v for k, v in first["schema"]["telemetry_sessions"].items() if k != "fingerprint"}
    assert (
        first["schema"]["telemetry_sessions"]["fingerprint"]
        == hashlib.sha256(json.dumps(view, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    )
    changed = dict(specs())
    base = changed["telemetry_sessions"]
    changed["telemetry_sessions"] = replace(base, not_null=base.not_null | {"workflow"})
    after = handshake.capabilities(changed, changed["telemetry_transcripts"].rules.content)
    assert after["schema"]["telemetry_sessions"]["fingerprint"] != first["schema"]["telemetry_sessions"]["fingerprint"]
    assert after["schema"]["telemetry_agents"]["fingerprint"] == first["schema"]["telemetry_agents"]["fingerprint"]
