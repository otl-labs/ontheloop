"""Guards for the telemetry table-registration contract amendments (slice 2a-2 plan 1): the two dimension
contracts, the identity-contract wording, the writer/reader contract amendments, the status vocabulary and the
Decision 200 annotation. A failing guard names the missing contract text -- fix the document, not the test.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[3]
_CONTRACTS = _ROOT / "docs" / "contracts"


def _load(name: str) -> dict:
    return yaml.safe_load((_CONTRACTS / f"{name}.yaml").read_text(encoding="utf-8"))


def _text(name: str) -> str:
    return (_CONTRACTS / f"{name}.yaml").read_text(encoding="utf-8")


def test_dimension_contracts_shape() -> None:
    tenants, projects = _load("ops_tenants"), _load("ops_projects")
    for doc, merge_key, partition_key in ((tenants, "tenant_id", "tenant_id"), (projects, "project_id", "tenant_id")):
        assert doc["contract"]["class"] == "A" and doc["contract"]["status"] == "ratified"
        gov = doc["governance"]
        assert gov["merge_key"] == merge_key and gov["table_class"] == "SCD2"
        assert gov["partition_by"] == (
            "history=year(created_timestamp), month(created_timestamp), day(created_timestamp); "
            f"current=bucket(8, {partition_key})"
        )
        assert "rec-4121" in doc["governance_notes"]  # the history MERGE-on-ULID residual is named
        assert "rec-4024" in doc["governance_notes"] and "Decision 181" in doc["governance_notes"]  # per-invariant coverage
        assert any("write_boundary=registration" in line for line in doc["audit_invariants"])
    t_fields, p_fields = tenants["fields"], projects["fields"]
    assert t_fields["tenant_id"]["$ref"].startswith("tenant-id.yaml")
    assert t_fields["principal_kind"]["dq_intent"]["accepted_values"]["values"] == ["aws_account", "configured"]
    assert t_fields["status"]["dq_intent"]["accepted_values"]["values"] == ["active", "suspended"]
    for name in ("principal_ref", "name", "plan", "canonical_tenant_id"):
        assert name in t_fields
    assert p_fields["project_id"]["$ref"].startswith("project-id.yaml") and p_fields["tenant_id"]["$ref"].startswith(
        "tenant-id.yaml"
    )
    assert p_fields["registration"]["dq_intent"]["accepted_values"]["values"] == ["unregistered", "registered"]
    assert p_fields["purpose"]["dq_intent"]["accepted_values"]["values"] == ["real", "drill", "synthetic"]
    for name in ("project_ref", "name", "remote", "canonical_project_id", "status"):
        assert name in p_fields
    assert "Decision 101" in t_fields["principal_ref"]["semantics"]  # placeholders only
    assert not re.search(r"\b\d{12}\b", _text("ops_tenants") + _text("ops_projects"))  # never a 12-digit id


def test_status_vocabulary_names_pre_production() -> None:
    vocab = _load("data-modeling-standard")["vocabulary"]
    assert "provisioned in production" in vocab["status_pre_production"]["reserved_meaning"]
    assert "status_smoke" in vocab["status_pre_production"]["do_not_use_for"]
    assert "table_class" in vocab["registry_table_class"]["reserved_meaning"]
    assert "maintenance class" in vocab["registry_table_class"]["do_not_use_for"]
    assert "kernel-proof" in vocab["status_smoke"]["reserved_meaning"]  # smoke stays reserved for kernel-proof tables


def test_identity_contracts_name_counter_lock() -> None:
    project = _load("project-id")["fields"]["project_id"]
    tenant = _load("tenant-id")["fields"]["tenant_id"]
    for field in (project, tenant):
        validation = field["write_time_validation"]
        assert "counter-row lock" in validation and "ops_entity_counters" in validation
        assert "race test" in validation and "reversal condition" in validation
        assert "FUTURE (rec-4024)" not in validation
    assert "committed in-repo" not in _text("project-id")
    assert "code committed in this repository" in project["description"]
    assert "rec-4026" in project["description"]  # ids are catalog-local; residual owner named
    assert "deployment obligation" in tenant["write_time_validation"] and "rec-4063" in tenant["write_time_validation"]
    for name in ("project-id", "tenant-id"):
        assert "_joins.yaml" in _load(name)["governance_notes"]
        assert any(e["date"] == "2026-10-05" for e in _load(name)["amendment_log"])


def test_writer_contract_idempotency_corrected() -> None:
    doc = _load("ducklake_writer")
    invariants = " ".join(doc["audit_invariants"])
    assert "MERGE-idempotent" not in invariants
    assert "fresh write ULID per call" in invariants and "rec-4121" in invariants
    assert "write_boundary" in invariants and "telemetry_append" in invariants and "registration" in invariants
    assert any(e["date"] == "2026-10-05" and "PLAN-telemetry-table-registration" in e["summary"] for e in doc["amendment_log"])


def test_dimension_joins_declared() -> None:
    joins = _load("_joins")["joins"]
    for table in ("sessions", "observations", "transcripts", "agents"):
        assert joins[f"tenant_id_join_telemetry_{table}_to_tenants"]["to"] == "ops_tenants.tenant_id"
        assert joins[f"project_id_join_telemetry_{table}_to_projects"]["to"] == "ops_projects.project_id"
        assert joins[f"project_id_join_telemetry_{table}_to_projects"]["from"] == f"telemetry_{table}.project_id"
    assert joins["tenant_id_join_ops_projects_to_tenants"]["from"] == "ops_projects.tenant_id"
    assert "DEFERRED, not yet forward-declared" not in _text("_joins")


def test_named_reads_pointers_moved() -> None:
    for name in ("ducklake_reader", "read-engine"):
        text = _text(name)
        assert "src/common/ducklake_named_reads.py" in text, name
        assert "ducklake_scd2_schema.py NAMED_READS" not in text and "ducklake_scd2_schema.py (the NAMED_READS" not in text, (
            name
        )
        assert "ducklake_scd2_schema.py);" not in text, name


def test_reader_contracts_document_read_version() -> None:
    reader = _load("ducklake_reader")
    verbs = reader["verbs"]
    assert (
        "read_version" in verbs["named_read"]["response_codes"][200]
        and "equality only" in verbs["named_read"]["response_codes"][200]
    )
    assert "read_version" in verbs["describe"]["response_codes"][200]
    invariants = " ".join(reader["audit_invariants"])
    assert (
        "write_boundary" in invariants
        and "No reader action accepts caller-supplied SQL" in invariants
        and "rec-3771" in invariants
        and "rec-4024" in invariants
    )
    assert reader["amendment_log"][0]["change_class"] == "field_add" and reader["amendment_log"][0]["date"] == "2026-10-05"
    engine = _text("read-engine")
    assert "read_version" in engine and "equality only" in engine


def test_ops_entity_counters_catalog_aware_grain() -> None:
    doc = _load("ops_entity_counters")
    assert "PRESENT in the catalog under check" in doc["fields"]["counter_name"]["semantics"]
    assert "ulid" in doc["fields"]["current_value"]["description"]
    invariants = " ".join(doc["audit_invariants"])
    assert "absent_tables" in invariants and "ulid -- current_value >= the owning current table's row count" in invariants
    assert any(e["date"] == "2026-10-05" and "PLAN-telemetry-table-registration" in e["summary"] for e in doc["amendment_log"])


def test_reserved_project_namespace_declared() -> None:
    project = _load("project-id")["fields"]["project_id"]
    assert "NEVER auto-registered" in project["semantics"] and "register_project" in project["semantics"]
    assert "ops_projects.yaml" in project["semantics"] and "Decision 210" in project["semantics"]
    purpose = _load("ops_projects")["fields"]["purpose"]["derivation"]
    assert purpose["realized"] is True and purpose["inputs"] == ["project_ref"]
    assert purpose["prefix_map"] == {"drill:": "drill", "synthetic:": "synthetic"} and purpose["default"] == "real"
    assert "project-id.yaml" in _load("ops_projects")["fields"]["project_ref"]["semantics"]


def test_decision_200_reserved_namespace_annotation() -> None:
    text = (_ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
    start = text.index("## Decision 200:")
    end = text.index("\n## Decision ", start + 1)
    section = text[start:end]
    notes = re.findall(r"^\[Amendment 2026-10-05[^\n]*\]$", section, flags=re.M)
    assert len(notes) == 1 and "project-id.yaml" in notes[0] and "drill:" in notes[0] and "synthetic:" in notes[0]
    assert section.rstrip().endswith("---")  # a trailing annotation, never a mid-body splice
    assert section.index(notes[0]) > section.index("```yaml reversal-conditions")


_TELEMETRY_WRITER = "agent-platform-ducklake-telemetry-writer"


def test_dimension_write_paths_name_the_telemetry_writer() -> None:
    for name, verb in (("ops_tenants", "register_tenant"), ("ops_projects", "register_project")):
        doc = _load(name)
        write_path = " ".join(doc["governance"]["write_path"].split())
        assert (
            verb in write_path
            and _TELEMETRY_WRITER in write_path
            and "ducklake_writer Lambda, in the telemetry" not in write_path
        )
        assert "write_boundary=registration" in write_path  # the generic-verb refusal sentence is kept
        assert "outside the retry loop" in " ".join(doc["governance"]["id_allocator"].split())
        assert any(
            e["date"] == "2026-10-12" and e["change_class"] == "prose_improvement" and e["semantic_break"] is False
            for e in doc["amendment_log"]
        )
    assert "derivations.purpose" in " ".join(_load("ops_projects")["governance"]["write_path"].split())
    assert "rec-4210" in _text("ops_tenants") and "Plan 3's register_tenant verb (attribute update)" not in _text(
        "ops_tenants"
    )
    counters = _load("ops_entity_counters")
    path = " ".join(counters["governance"]["write_path"].split())
    for needle in (
        "lock-only UPDATE",
        "provision_telemetry seeds",
        "Decision 213",
        "telemetry catalog only",
        "exactly one row",
    ):
        assert needle in path, needle
    assert "control_health runs on production only" in " ".join(counters["governance_notes"].split())
    dates = [e["date"] for e in counters["amendment_log"] if e["date"] in ("2026-10-05", "2026-10-12")]
    assert dates == ["2026-10-05", "2026-10-12"]
    assert any(
        e["date"] == "2026-10-12" and e["semantic_break"] is True and e["change_class"] == "governance_note_add"
        for e in counters["amendment_log"]
    )


def test_substrate_rows_name_the_telemetry_writer() -> None:
    tables = _load("storage-substrate")["tables"]
    telemetry = tables["telemetry_tables"]
    assert _TELEMETRY_WRITER in telemetry["write_boundary"] and "ducklake_writer" not in telemetry["write_boundary"].replace(
        "shared ducklake_writer", ""
    )
    assert "created and owned in the production telemetry catalog ducklake_telemetry" in " ".join(telemetry["note"].split())
    assert "year(session_started_at), month(session_started_at), day(session_started_at)" in telemetry["disposition"]
    assert "telemetry-event-envelope.yaml" in " ".join(telemetry["contract_ref"]) and "blob port" in _text("storage-substrate")
    counters = tables["ops_entity_counters"]["write_boundary"]
    assert "telemetry writer" in " ".join(counters.split()) and "ducklake_smoke" in counters
    substrate_log = _load("storage-substrate")["amendment_log"]
    assert any(e["date"] == "2026-10-14" and "PLAN-telemetry-owner-provisioning" in e["summary"] for e in substrate_log)
    assert any(e["date"] == "2026-10-12" for e in substrate_log)  # the registration-verbs entry is still present
    assert "create_telemetry_tables" in " ".join(telemetry["note"].split())


def test_substrate_row_names_the_production_boundaries() -> None:
    telemetry = _load("storage-substrate")["tables"]["telemetry_tables"]
    assert telemetry["status"] == "live"
    assert "ducklake_telemetry" in telemetry["write_boundary"]
    for reader in ("agent-platform-ducklake-telemetry-reader", "agent-platform-ducklake-telemetry-content"):
        assert reader in telemetry["read_boundary"], reader
    text = " ".join(" ".join(str(telemetry[k]) for k in ("note", "reason", "write_boundary", "read_boundary")).split())
    assert "empty until" not in text and "no writer emits" not in text


def test_ports_pc01_names_the_telemetry_writer_edge() -> None:
    ports = _load("ports")
    pc01 = next(f for f in ports["coupling_findings"] if f["id"] == "PC-01")
    text = " ".join(pc01["description"].split())
    assert "src/lambdas/ducklake_telemetry_writer/handler.py" in text and "Decision 213" in text
    assert "function_url adapter" in text and "rec-4024" not in text and "no port row is added" in text
    entry = next(e for e in ports["amendment_log"] if "PLAN-telemetry-registration-verbs" in e["summary"])
    assert entry["date"] == "2026-10-12" and entry["semantic_break"] is False
    assert pc01["status"] == "accepted" and pc01["ports"] == ["ops_verbs", "persona_compute_host"]
