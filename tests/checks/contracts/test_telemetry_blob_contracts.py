"""The contract surfaces PLAN-telemetry-blob-route changes say what the code does (T2.36 slice 2a-2 plan 4)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from scripts.platform_roadmap_state import load as load_roadmap
from src.common.ducklake_telemetry_blobstore import BLOB_COMMIT_MAX_AGE_DAYS

ROOT = Path(__file__).resolve().parents[3]


def load(rel: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def norm(text: str) -> str:
    return " ".join(str(text).split())


def test_transcripts_carry_no_exemption_and_name_the_route() -> None:
    contract = load("docs/contracts/telemetry_transcripts.yaml")
    fields = contract["fields"]
    intent = fields["content"]["dq_intent"]
    assert "write_time_exemptions" not in intent and intent["content_inline_threshold_bytes"] == 65536
    assert intent["full_output_cap_bytes"] == 8388608
    semantics = norm(fields["content"]["semantics"])
    assert "claim-check route" in semantics and "begin_blobs" in semantics and "HEAD-verifies" in semantics
    assert "413" not in semantics and "payload_too_large" not in semantics and "exempt until" not in semantics
    assert "HEAD" in norm(fields["content_uri"]["populated_by"]) and "blob-port put" not in norm(
        fields["content_uri"]["populated_by"]
    )
    write_path = norm(contract["governance"]["write_path"])
    assert "begin_blobs" in write_path and "claim-check" in write_path and "rec-4033" in write_path
    notes = norm(contract["governance_notes"])
    assert "rec-4033" in notes and "rec-4291" in notes and "rec-4024 (writer" not in notes
    assert "begin_blobs re-grants a missing object" in notes and "tombstone" in notes
    dated = {e["date"] for e in contract["amendment_log"]}
    assert "2026-10-15" in dated and any(e["date"] == "2026-10-15" for e in fields["content"]["amendment_log"])
    assert contract["contract"]["contract_version"] == 2


def test_writer_contract_carries_begin_blobs_and_the_claim_check() -> None:
    contract = load("docs/contracts/ducklake_telemetry_writer.yaml")
    verbs = contract["verbs"]
    assert {"begin_blobs", "append_telemetry", "describe"} <= set(verbs)
    typed = verbs["begin_blobs"]["typed_errors"]
    assert {"too_many_blobs", "blob_store_unavailable", "blob_integrity", "blob_grant_self_check", "blob_store_access"} <= set(
        typed
    )
    assert {"unmapped", "catalog_unavailable", "bad_request", "project_unregistered", "tenant_unresolved"} <= set(typed)
    assert set(verbs["begin_blobs"]["response_codes"]) == {200, 400, 403, 413, 422, 500, 503}
    assert "blob_not_verified" in typed and "size_mismatch" in verbs["begin_blobs"]["response_codes"][422]
    assert "size_mismatch" in verbs["append_telemetry"]["response_codes"][422]
    append = verbs["append_telemetry"]
    assert {"too_many_blobs", "blob_not_verified", "blob_store_unavailable", "blob_integrity"} <= set(append["typed_errors"])
    assert "blob_not_verified" in append["response_codes"][422] and "too_many_blobs" in append["response_codes"][413]
    too_large = append["response_codes"][413]
    assert "writer_derived" in append["payload_schema_ref"] and "inline_within" not in too_large
    describe = norm(verbs["describe"]["payload_schema_ref"])
    assert "blob_protocol" in describe and "fingerprint" in describe
    invariants = norm(" ".join(contract["audit_invariants"]))
    for needle in ("Claim-check rule", "single object, content-addressed", "SHA-256- and length-bound", "at most 15 minutes"):
        assert needle in invariants, needle
    for needle in (
        "rec-4033",
        "only the telemetry writer roles put to the blob root",
        "Decision 81 cl.7",
        "Decision 213 cl.2",
        "Decision 212 cl.4",
        "Decision 84 I-4",
        "never ingest",
    ):
        assert needle.replace("never ingest", "no process ever ingests") in invariants, needle
    assert "blob_route_" + "unavailable" not in yaml.safe_dump(contract) and "six data verbs" in invariants
    assert any(e["date"] == "2026-10-15" and "PLAN-telemetry-blob-route" in e["summary"] for e in contract["amendment_log"])


def test_commit_age_matches_the_contract() -> None:
    invariants = norm(" ".join(load("docs/contracts/ducklake_telemetry_writer.yaml")["audit_invariants"]))
    ages = {int(n) for n in re.findall(r"older than (\d+) days", invariants)}
    assert ages == {BLOB_COMMIT_MAX_AGE_DAYS} == {7}
    assert "commits only as the no-op of an identical stored row" in invariants and "require_stored" not in invariants


def test_ports_content_blobs_row() -> None:
    ports = load("docs/contracts/ports.yaml")
    row = next(p for p in ports["ports"] if p["id"] == "content_blobs")
    assert row["required_verbs"] == ["grant_put", "verify", "get_blob"] and row["verbs_status"] == "provisional"
    adapters = {a["id"]: a for a in row["adapters"]}
    assert (
        adapters["s3"]["status"] == "implemented"
        and adapters["s3"]["location"] == "src/common/ducklake_telemetry_blobstore.py"
    )
    assert (ROOT / adapters["s3"]["location"]).is_file()
    assert adapters["local_directory"]["status"] == "pending" and adapters["local_directory"]["owning_item"] == "T4.23:c7"
    pc05 = next(f for f in ports["coupling_findings"] if f["id"] == "PC-05")
    assert pc05["ports"] == ["content_blobs", "ops_verbs"] and pc05["status"] == "open" and pc05["owning_item"] == "T4.23:c7"
    assert pc05["location"] == "src/telemetry_client/blob_delivery.py" and (ROOT / pc05["location"]).is_file()
    pc01 = next(f for f in ports["coupling_findings"] if f["id"] == "PC-01")
    assert "rec-4024" not in pc01["description"]
    assert any(e["date"] == "2026-10-15" and "PLAN-telemetry-blob-route" in e["summary"] for e in ports["amendment_log"])


def test_t2_36_c2_c4_name_the_claim_check() -> None:
    item = next(i for i in load_roadmap(ROOT / "docs/ROADMAP-PLATFORM.yaml").tier_items if i.id == "T2.36")
    criteria = {c.id: norm(c.text) for c in item.exit_criteria}
    prefix = "[Re-grounded 2026-10-12, PLAN-telemetry-blob-route:"
    c4_tail = "rg 'ops-outbox' finds no active write callers"
    for name, original in (("c2", "no telemetry producer stages to S3"), ("c4", c4_tail)):
        assert criteria[name].startswith(prefix) and "claim-check content route, not staging" in criteria[name], name
        assert original in criteria[name], name
    assert "rec-4292" in criteria["c4"] and "no object is ever ingested into the catalog" in criteria["c4"]
    annotation = criteria["c4"].split(c4_tail, 1)[1]
    assert annotation.startswith(" [Annotated ") and "blob-root write fence" in annotation, annotation
    assert "rg finds no staging-writer callers on any telemetry path" in criteria["c2"]
    assert criteria["c1"].startswith("Four telemetry tables") and criteria["c5"].startswith("[Re-grounded 2026-09-24")
