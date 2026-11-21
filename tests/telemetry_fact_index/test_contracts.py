"""Contract projection over synthetic contracts and over the real five telemetry contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.contracts import load_contract, resolve_refs
from scripts.telemetry_fact_index import project
from scripts.telemetry_fact_index.contracts import TABLES, project_contracts
from tests.telemetry_fact_index import _contract, _field, make_root

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def synthetic_rows(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    root = make_root(tmp_path_factory.mktemp("facts"))
    rows = project_contracts(root, {"session_surface"})
    return {r.address: r for r in rows}


def test_required_when_rows_name_every_key_sorted(synthetic_rows: dict[str, Any]) -> None:
    assert "telemetry_observations.name[event_kind=point,observation_type=process_event]" in synthetic_rows
    assert "telemetry_observations.output_sha256[output_capture=captured|omitted_oversize]" in synthetic_rows
    assert "telemetry_observations.name" in synthetic_rows


def test_unproduced_row_names_its_owner(synthetic_rows: dict[str, Any]) -> None:
    row = synthetic_rows["telemetry_observations.legacy"]
    assert (row.kind, row.owner) == ("unproduced", "rec-1")
    assert synthetic_rows["telemetry_sessions.workflow"].kind == "stored"


def test_retired_row_is_its_own_kind_owned_by_its_drop_rec(synthetic_rows: dict[str, Any]) -> None:
    row = synthetic_rows["telemetry_observations.dropped"]
    assert (row.kind, row.owner, row.producers) == ("retired", "rec-2", [])


def test_read_derived_served_from_registry_not_realized(synthetic_rows: dict[str, Any]) -> None:
    served = synthetic_rows["telemetry_sessions.surface"]
    assert served.kind == "read_derived" and [s.verb for s in served.serving] == ["session_surface"]
    assert synthetic_rows["telemetry_persona_findings.verdict"].serving == []


def test_read_derived_unserved_when_verb_not_registered(tmp_path: Path) -> None:
    rows = {r.address: r for r in project_contracts(make_root(tmp_path), set())}
    assert rows["telemetry_sessions.surface"].serving == []


def test_ref_field_keeps_its_local_derivation(synthetic_rows: dict[str, Any]) -> None:
    row = synthetic_rows["telemetry_persona_findings.tenant_id"]
    assert row.kind == "read_derived" and row.derived_by == "library:scripts.x.y"


def test_field_with_no_kind_raises_naming_it(tmp_path: Path) -> None:
    broken = _contract(
        "telemetry_agents", {"agent_type": _field(), "orphan": _field()}, {"produced": {"claude_code": ["agent_type"]}}
    )
    with pytest.raises(ValueError, match="telemetry_agents.orphan"):
        project_contracts(make_root(tmp_path, {"telemetry_agents": broken}), set())


def test_added_field_appears_with_no_other_file_changed(tmp_path: Path) -> None:
    base = make_root(tmp_path)
    path = base / "docs" / "contracts" / "telemetry_agents.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["fields"]["fresh_fact"] = _field()
    doc["realization"]["produced"]["claude_code"].append("fresh_fact")
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert "telemetry_agents.fresh_fact" in {r.address for r in project_contracts(base, set())}


def test_every_resolved_real_field_has_a_row() -> None:
    addresses = {r.address for r in project(REPO)}
    for table in TABLES:
        for name in resolve_refs(load_contract(REPO / "docs" / "contracts" / f"{table}.yaml"), REPO / "docs" / "contracts"):
            assert f"{table}.{name}" in addresses
