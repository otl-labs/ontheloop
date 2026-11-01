"""rec-4218's acceptance: the ingestion shape and the production column set are recorded in the substrate (Decision 230)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[3]
_CONTRACTS = _ROOT / "docs" / "contracts"


def _telemetry_tables() -> dict:
    doc = yaml.safe_load((_CONTRACTS / "storage-substrate.yaml").read_text(encoding="utf-8"))
    return doc["tables"]["telemetry_tables"]


def test_ingestion_shape_is_per_request_commit_with_three_triggers() -> None:
    shape = _telemetry_tables()["ingestion_shape"]
    assert shape["shape"] == "per_request_commit" and shape["rec"] == "rec-4218"
    triggers = shape["reversal_triggers"]
    assert len(triggers) == 3
    assert all(t["threshold"].strip() and "never staging" in t["alternative_then"] for t in triggers)
    sizing = shape["writer_sizing"]
    assert sizing["reserved_concurrency"] == 5 and sizing["connection_limit"] == 10
    assert "Throttles > 0" in sizing["resize_when"] and "OccRetryCount p95 >= 2" in sizing["resize_when"]

    writer = yaml.safe_load((_CONTRACTS / "ducklake_telemetry_writer.yaml").read_text(encoding="utf-8"))
    notes = " ".join(writer["governance_notes"].split())
    assert notes.startswith("ingestion_shape: per_request_commit")
    assert "storage-substrate.yaml tables.telemetry_tables.ingestion_shape" in notes


def test_production_column_set_is_recorded() -> None:
    column_set = _telemetry_tables()["production_column_set"]
    assert column_set["plan"] == "PLAN-telemetry-production-catalog"
    assert set(column_set["typed_by_decision_222"]) == {"cache_creation_1h_tokens", "cache_creation_5m_tokens"}
    assert column_set["metadata_only"] == ["prefix_hash"]
    rule = " ".join(column_set["rule"].split())
    for needle in ("pending_reconcile", "telemetry_catalog", "--telemetry-hold"):
        assert needle in rule, needle

    registry = yaml.safe_load((_ROOT / "config/lambda/ducklake/field_semantics.yaml").read_text(encoding="utf-8"))
    columns = registry["ops_tables"]["telemetry_observations"]["columns"]
    for name in column_set["typed_by_decision_222"]:
        assert columns[name]["sql_type"] == "BIGINT", name
    assert "prefix_hash" not in columns


def test_triggers_read_the_production_meta_schema_series() -> None:
    shape = _telemetry_tables()["ingestion_shape"]
    triggers = {t["id"]: t["threshold"] for t in shape["reversal_triggers"]}
    for threshold in triggers.values():
        assert "MetaSchema=ducklake_telemetry" in threshold
    assert "ducklake-telemetry-merge-duration" in triggers["merge_pass_duration"]
    assert "37 days" in triggers["stats_rows_growth"] and "SNAPSHOT_RETAIN_DAYS + 7" in triggers["stats_rows_growth"]
    assert "MetaSchema=ducklake_telemetry" in shape["writer_sizing"]["resize_when"]
    assert all("undimensioned" not in text for text in [*triggers.values(), shape["writer_sizing"]["resize_when"]])
