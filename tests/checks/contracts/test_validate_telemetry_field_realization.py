"""Mirror test for validate_telemetry_field_realization: a synthetic world passes, then one planted defect per rule fails
closed naming table and field, plus the live repository, in-memory mutations of it, and registry accounting."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from scripts.checks import registry
from scripts.checks.contracts import validate_telemetry_field_realization as check
from scripts.checks.contracts._manifest import ENTRIES

ROOT = Path(__file__).resolve().parents[3]
T = "telemetry_sessions"
_ROADMAP = (
    "  - id: T1.1\n    exit_criteria:\n      - id: c1\n        text: x\n        status: open\n"
    "      - id: c2\n        text: y\n        status: met\n"
    "  - id: T2.2\n    exit_criteria:\n      - id: c1\n        status: open\n"
)


def _table() -> dict[str, Any]:
    return {
        "fields": {
            "event_id": {"$ref": "envelope#/event_id"},
            "a": {"populated_by": "the claude_code producer, at the open event."},
            "d": {"populated_by": "the claude_code and second producers, at the open event."},
            "h": {"populated_by": "the claude_code producer."},
            "u": {"populated_by": "unproduced: rec-9 (no producer writes it)"},
            "g": {"populated_by": "unproduced: T1.1:c1 (no producer writes it)"},
            "r": {"derivation": {"timing": "read"}, "populated_by": "a named reader verb"},
        },
        "realization": {
            "produced": {"write_boundary": ["event_id"], "claude_code": ["a", "d", "h"], "second": ["d"]},
            "unproduced": {"rec-9": ["u"], "T1.1:c1": ["g"]},
        },
    }


def _world() -> dict[str, Any]:
    row = {"event_id": "E", "a": 1, "d": 2, "table": T}
    return {
        "contracts": {t: _table() for t in check.TABLES},
        "producers": ["claude_code", "second"],
        "golden": {
            "claude_code": {"normalised_out": ["h"], "rows": {t: [dict(row, table=t)] for t in check.TABLES}},
            "second": {"normalised_out": [], "rows": {t: [{"event_id": "E", "d": 2}] for t in check.TABLES}},
        },
        "field_semantics": {
            "ops_tables": {t: {"columns": {"event_id": {"role": "derived"}, "a": {"role": "input"}}} for t in check.TABLES}
        },
        "roadmap_text": _ROADMAP,
    }


def _block(w: dict[str, Any]) -> dict[str, Any]:
    return w["contracts"][T]["realization"]


def _field(w: dict[str, Any], name: str) -> dict[str, Any]:
    return w["contracts"][T]["fields"][name]


def _drop_field(w: dict[str, Any]) -> None:
    _block(w)["produced"]["claude_code"].remove("a")


def _dup_field(w: dict[str, Any]) -> None:
    _block(w)["produced"]["claude_code"].append("a")


def _both(w: dict[str, Any]) -> None:
    _block(w)["unproduced"]["rec-9"].append("a")


def _wb_and_producer(w: dict[str, Any]) -> None:
    _block(w)["produced"]["claude_code"].append("event_id")


def _two_owners(w: dict[str, Any]) -> None:
    _block(w)["unproduced"]["T1.1:c1"].append("u")


def _unknown(w: dict[str, Any]) -> None:
    _block(w)["produced"]["claude_code"].append("nope")


def _read_derived(w: dict[str, Any]) -> None:
    _block(w)["produced"]["claude_code"].append("r")


def _no_block(w: dict[str, Any]) -> None:
    del w["contracts"][T]["realization"]


def _unregistered(w: dict[str, Any]) -> None:
    _block(w)["produced"]["ghost"] = ["a"]


def _wb_missing(w: dict[str, Any]) -> None:
    w["field_semantics"]["ops_tables"][T]["columns"]["tenant_id"] = {"role": "derived"}


def _wb_extra(w: dict[str, Any]) -> None:
    w["field_semantics"]["ops_tables"][T]["columns"]["event_id"]["role"] = "input"


def _absent_from_corpus(w: dict[str, Any]) -> None:
    w["golden"]["claude_code"]["rows"][T] = [{"event_id": "E", "d": 2, "table": T}]


def _no_golden(w: dict[str, Any]) -> None:
    del w["golden"]["claude_code"]


def _stale_unproduced(w: dict[str, Any]) -> None:
    w["golden"]["claude_code"]["rows"][T][0]["u"] = 5


def _uncredited(w: dict[str, Any]) -> None:
    _block(w)["produced"]["claude_code"].remove("a")
    _block(w)["unproduced"]["rec-9"].append("a")
    w["golden"]["claude_code"]["rows"][T][0].pop("u", None)
    _field(w, "a")["populated_by"] = "unproduced: rec-9 (no producer writes it)"


def _uncredited_column(w: dict[str, Any]) -> None:
    w["contracts"][T]["fields"]["z"] = {"populated_by": "the claude_code producer."}
    _block(w)["produced"]["claude_code"].append("z")
    w["golden"]["claude_code"]["rows"][T][0]["z"] = 1
    _block(w)["produced"]["claude_code"].remove("z")
    _block(w)["produced"]["second"].append("z")


def _bad_owner(w: dict[str, Any]) -> None:
    _block(w)["unproduced"]["soon"] = []
    _block(w)["unproduced"]["soon"].append("u")
    _block(w)["unproduced"]["rec-9"].remove("u")


def _missing_criterion(w: dict[str, Any]) -> None:
    _block(w)["unproduced"]["T9.9:c1"] = ["u"]
    _block(w)["unproduced"]["rec-9"].remove("u")


def _met_criterion(w: dict[str, Any]) -> None:
    _block(w)["unproduced"]["T1.1:c2"] = ["u"]
    _block(w)["unproduced"]["rec-9"].remove("u")


def _unproduced_names_producer(w: dict[str, Any]) -> None:
    _field(w, "u")["populated_by"] = "the claude_code producer."


def _produced_says_unproduced(w: dict[str, Any]) -> None:
    _field(w, "a")["populated_by"] = "unproduced: rec-9 (no producer writes it)"


def _producer_says_boundary(w: dict[str, Any]) -> None:
    _field(w, "a")["populated_by"] = "the write boundary at the open event."


def _wrong_owner(w: dict[str, Any]) -> None:
    _field(w, "u")["populated_by"] = "unproduced: rec-10 (no producer writes it)"


def _one_of_two(w: dict[str, Any]) -> None:
    _field(w, "d")["populated_by"] = "the claude_code producer."


DEFECTS: list[tuple[str, Callable[[dict[str, Any]], None], str]] = [
    ("R1-missing", _drop_field, f"{T}.a: stored field is in neither"),
    ("R1-duplicate", _dup_field, "lists a field more than once"),
    ("R1-both", _both, f"{T}.a: stored field is both produced and unproduced"),
    ("R1-wb-and-producer", _wb_and_producer, f"{T}.event_id: write_boundary is credited together"),
    ("R1-two-owners", _two_owners, f"{T}.u: stored field has more than one unproduced owner"),
    ("R1-unknown", _unknown, f"{T}.nope: listed in realization but is not a contract field"),
    ("R1-read-derived", _read_derived, f"{T}.r: read-derived field listed"),
    ("R1-no-block", _no_block, f"{T}: no realization block"),
    ("R2-unregistered", _unregistered, "produced key 'ghost'"),
    ("R3-missing", _wb_missing, "missing ['tenant_id']"),
    ("R3-extra", _wb_extra, "extra ['event_id']"),
    ("R4-absent", _absent_from_corpus, f"{T}.a: credited to claude_code but never non-null"),
    ("R4-no-golden", _no_golden, "registers no golden_index"),
    ("R5-stale", _stale_unproduced, f"{T}.u: marked unproduced but claude_code's golden rows emit it"),
    ("R6-uncredited", _uncredited_column, f"{T}.z: claude_code's golden rows emit it but the block does not credit it"),
    ("R7-malformed", _bad_owner, "owner 'soon' is malformed"),
    ("R7-missing-criterion", _missing_criterion, "owner T9.9:c1 is not an open roadmap criterion"),
    ("R7-met-criterion", _met_criterion, "criterion met: move these columns to produced"),
    ("R8-unproduced-names-producer", _unproduced_names_producer, f"{T}.u: populated_by must start with 'unproduced: rec-9'"),
    (
        "R8-produced-says-unproduced",
        _produced_says_unproduced,
        f"{T}.a: populated_by must start with 'the claude_code producer'",
    ),
    ("R8-producer-says-boundary", _producer_says_boundary, f"{T}.a: populated_by must start with 'the claude_code producer'"),
    ("R8-wrong-owner", _wrong_owner, f"{T}.u: populated_by must start with 'unproduced: rec-9'"),
    ("R8-one-of-two", _one_of_two, "must start with 'the claude_code and second producers'"),
]


def test_clean_synthetic_world_passes() -> None:
    errors, examined = check.realization_errors(_world())
    assert errors == []
    assert examined == 6 * len(check.TABLES)


@pytest.mark.parametrize(("name", "mutate", "expected"), DEFECTS, ids=[d[0] for d in DEFECTS])
def test_each_rule_fails_closed(name: str, mutate: Callable[[dict[str, Any]], None], expected: str) -> None:
    world = copy.deepcopy(_world())
    mutate(world)
    errors, _ = check.realization_errors(world)
    assert any(expected in e for e in errors), (name, errors)


def test_normalised_out_column_passes_r4_by_membership() -> None:
    errors, _ = check.realization_errors(_world())
    assert not any(".h:" in e for e in errors)


def test_criterion_status_lookup() -> None:
    assert check.criterion_status(_ROADMAP, "T1.1:c1") == "open"
    assert check.criterion_status(_ROADMAP, "T1.1:c2") == "met"
    assert check.criterion_status(_ROADMAP, "T1.1:c3") is None
    assert check.criterion_status(_ROADMAP, "T9.9:c1") is None
    assert check.criterion_status(_ROADMAP, "rec-9") is None


def _live() -> dict[str, Any]:
    return check.load_inputs(ROOT)


def test_live_repository_passes() -> None:
    errors, examined = check.realization_errors(_live())
    assert errors == []
    counted = 0
    for table in check.TABLES:
        spec = yaml.safe_load((ROOT / "docs" / "contracts" / f"{table}.yaml").read_text(encoding="utf-8"))
        counted += sum(1 for f in spec["fields"].values() if ((f or {}).get("derivation") or {}).get("timing") != "read")
    assert examined == counted


def test_registered_wrapper_accounts_for_every_stored_field() -> None:
    failed: list[str] = []
    check.validate_telemetry_field_realization(failed, root=ROOT)
    assert failed == []
    errors, examined = check.realization_errors(_live())
    assert examined > 0 and errors == []
    assert any(e.name == "validate_telemetry_field_realization" for e in ENTRIES)
    assert registry.resolve("validate_telemetry_field_realization") is check.validate_telemetry_field_realization


def test_live_contract_mutations_are_caught() -> None:
    base = _live()
    removed = copy.deepcopy(base)
    removed["contracts"]["telemetry_sessions"]["realization"]["produced"]["claude_code"].remove("branch")
    errors, _ = check.realization_errors(removed)
    assert any("telemetry_sessions.branch" in e and "(R1)" in e for e in errors)
    moved = copy.deepcopy(base)
    block = moved["contracts"]["telemetry_sessions"]["realization"]
    block["produced"]["claude_code"].remove("harness_session_key")
    block["unproduced"]["rec-4599"].append("harness_session_key")
    errors, _ = check.realization_errors(moved)
    assert any("telemetry_sessions.harness_session_key" in e and "(R5)" in e for e in errors)


def _copy_inputs(dest: Path) -> None:
    import shutil

    for rel in (
        "config/telemetry/parser_versions.yaml",
        "config/lambda/ducklake/field_semantics.yaml",
        "docs/ROADMAP-PLATFORM.yaml",
        *(f"docs/contracts/{t}.yaml" for t in check.TABLES),
    ):
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, dest / rel)
    shutil.copytree(ROOT / "tests/fixtures/turn_capture/golden", dest / "tests/fixtures/turn_capture/golden")


def test_producer_without_golden_index_loads_without_a_corpus(tmp_path: Path) -> None:
    _copy_inputs(tmp_path)
    path = tmp_path / "config/telemetry/parser_versions.yaml"
    versions = yaml.safe_load(path.read_text(encoding="utf-8"))
    versions["producers"]["bare"] = {"current": 1, "history": []}
    path.write_text(yaml.safe_dump(versions), encoding="utf-8")
    loaded = check.load_inputs(tmp_path)
    assert "bare" in loaded["producers"] and "bare" not in loaded["golden"]


def test_wrapper_reports_unreadable_inputs(tmp_path: Path) -> None:
    failed: list[str] = []
    check.validate_telemetry_field_realization(failed, root=tmp_path)
    assert len(failed) == 1 and "inputs unreadable" in failed[0]


def test_wrapper_reports_rule_failures(tmp_path: Path) -> None:
    _copy_inputs(tmp_path)
    path = tmp_path / "docs/contracts/telemetry_agents.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("\nrealization:\n", "\nrealization_off:\n"), encoding="utf-8")
    failed: list[str] = []
    check.validate_telemetry_field_realization(failed, root=tmp_path)
    assert any("telemetry_agents" in f for f in failed)


def test_duplicate_unproduced_field_is_refused() -> None:
    world = _world()
    _block(world)["unproduced"]["rec-9"].append("u")
    errors, _ = check.realization_errors(world)
    assert any("unproduced.rec-9 lists a field more than once" in e for e in errors)


def test_wrapper_reports_a_malformed_block_instead_of_crashing(tmp_path: Path) -> None:
    _copy_inputs(tmp_path)
    path = tmp_path / "docs/contracts/telemetry_agents.yaml"
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    spec["realization"] = ["not", "a", "mapping"]
    path.write_text(yaml.safe_dump(spec), encoding="utf-8")
    failed: list[str] = []
    check.validate_telemetry_field_realization(failed, root=tmp_path)
    assert len(failed) == 1 and "inputs unreadable" in failed[0]
