"""The restore rehearsal sitting's committed evidence (rec-5023): pure JSON reads, hermetic, fast tier."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.ducklake_smoke import restore_rehearsal_evidence as ev

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "restore_rehearsal"


def _load(name: str) -> tuple[str, dict[str, Any]]:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return text, json.loads(text)


def test_rewritten_data_path_reads_every_table() -> None:
    text, evidence = _load("evidence.json")
    code, lines = ev.verify_text(text)
    assert code == 0, lines
    assert lines[0] == "RESTORE_REHEARSAL_OK"
    assert ev.failed_criteria(evidence) == []
    assert evidence["rewritten"] is True
    assert evidence["absolute_paths"] == 0
    assert evidence["missing_files"] == 0
    tables = evidence["ops_tables"]
    assert tables
    for table in tables:
        assert table["read_ok"] is True, table["table"]
        assert table["restored_count"] == table["prod_count"], table["table"]
        assert table["restored_checksum"] == table["prod_checksum"], table["table"]
    assert evidence["write_probe_in_scratch"] is True
    assert evidence["write_probe_in_production"] is False
    assert evidence["torn_down"] and all(v is True for v in evidence["torn_down"].values())


def test_evidence_holds_no_forbidden_pattern() -> None:
    for name in ("evidence.json", "version_recovery.json"):
        text, _ = _load(name)
        assert ev.forbidden_hits(text) == [], name


def test_restore_rehearsal_failed_criteria() -> None:
    _, evidence = _load("evidence.json")
    broken = json.loads(json.dumps(evidence))
    broken["missing_files"] = 3
    broken["ops_tables"][0]["restored_count"] = broken["ops_tables"][0]["prod_count"] + 1
    broken["write_probe_in_production"] = True
    assert ev.failed_criteria(broken) == ["complete", "faithful", "one_way_writes"]
    code, lines = ev.verify_text(json.dumps(broken))
    assert code == 1
    assert [line for line in lines if line.startswith("FAILED")] == [
        "FAILED complete",
        "FAILED faithful",
        "FAILED one_way_writes",
    ]
    assert "FAILED faithful" in lines
    assert "FAILED one_way_writes" in lines


def test_version_recovery_evidence() -> None:
    text, evidence = _load("version_recovery.json")
    assert ev.verify_text(text) == (0, ["VERSION_RECOVERY_OK"])
    assert evidence["sha256_match"] is True
    assert evidence["deleted_after"] is True
    assert evidence["noncurrent_versions"] >= 1


def test_duckdb_copy_evidence() -> None:
    _, evidence = _load("evidence.json")
    rows = evidence["duckdb_copy_tables"]
    assert rows
    assert {"table", "match_restored", "match_median"} <= set(rows[0])
    assert evidence["duckdb_catalog_rows"] > 0
    assert evidence["duckdb_copy_ms"] >= 0
