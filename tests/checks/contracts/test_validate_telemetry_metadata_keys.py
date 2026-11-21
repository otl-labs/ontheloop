"""validate_telemetry_metadata_keys: the real registry matches every golden key set, and one red fixture per rule K1-K5."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from scripts.checks import registry
from scripts.checks.contracts import validate_telemetry_metadata_keys as check
from scripts.checks.contracts._manifest import ENTRIES

ROOT = Path(__file__).resolve().parents[3]


def _row(**over: Any) -> dict[str, Any]:
    base = {"producer": "p", "observation_type": "tool_call", "event_kind": "open", "key": "k", "since": 1, "meaning": "m"}
    return {**base, **over}


def _world() -> tuple[list[dict[str, Any]], dict[str, Any], list[str], dict[str, int]]:
    corpus = {
        "p": {
            "rows": {
                "telemetry_observations": [
                    {"observation_type": "tool_call", "event_kind": "open", "metadata": json.dumps({"k": None})}
                ]
            }
        }
    }
    return [_row()], corpus, ["p"], {"p": 3}


def _errors(world: tuple[Any, ...]) -> list[str]:
    return check.metadata_key_errors(*copy.deepcopy(world))[0]


def test_registry_matches_every_golden_key_set() -> None:
    failed: list[str] = []
    check.validate_telemetry_metadata_keys(failed, root=ROOT)
    assert failed == []
    rows, golden, _, _ = check.load_registry_inputs(ROOT)
    found = set().union(*check.golden_key_rows(golden).values())
    declared = {(r["producer"], r["observation_type"], r["event_kind"], r["key"]) for r in rows if "until" not in r}
    assert declared == found
    litellm = [r for r in rows if r["producer"] == "litellm"]
    assert len(litellm) == 2 and all(r["since"] == 1 for r in litellm)


def test_synthetic_world_is_green() -> None:
    assert _errors(_world()) == []


def test_k1_undeclared_key() -> None:
    rows, corpus, order, cur = _world()
    corpus["p"]["rows"]["telemetry_observations"][0]["metadata"] = json.dumps({"k": 1, "extra": 2})
    assert any("p tool_call open extra" in e and "(K1)" in e for e in _errors((rows, corpus, order, cur)))


def test_k2_declared_key_no_golden_row_carries() -> None:
    rows, corpus, order, cur = _world()
    rows.append(_row(key="ghost"))
    assert any("ghost" in e and "(K2)" in e for e in _errors((rows, corpus, order, cur)))


def test_k2_row_with_until_still_carried() -> None:
    rows, corpus, order, cur = _world()
    rows[0]["until"] = 2
    assert any("still carries" in e and "(K2)" in e for e in _errors((rows, corpus, order, cur)))


def test_k2_retired_row_not_carried_is_green() -> None:
    rows, corpus, order, cur = _world()
    rows.append(_row(key="old", until=2))
    assert _errors((rows, corpus, order, cur)) == []


def test_k3_duplicate_and_unregistered_producer() -> None:
    rows, corpus, order, cur = _world()
    rows.append(_row())
    rows.append(_row(producer="zzz"))
    errs = _errors((rows, corpus, order, cur))
    assert any("duplicate" in e and "(K3)" in e for e in errs)
    assert any("zzz" in e and "not registered" in e and "(K3)" in e for e in errs)


def test_k4_since_and_until_ranges() -> None:
    for bad in (0, 4, True, "<=0", "<=4", "<=x", "2"):
        rows, corpus, order, cur = _world()
        rows[0]["since"] = bad
        assert any("(K4)" in e for e in _errors((rows, corpus, order, cur))), bad
    for until in (3, 4, 1.5):
        rows, corpus, order, cur = _world()
        rows.append(_row(key="old", since=2, until=until))
        assert any("(K4)" in e for e in _errors((rows, corpus, order, cur))), until
    rows, corpus, order, cur = _world()
    rows.append(_row(key="old", since=2, until=1))
    assert any("(K4)" in e for e in _errors((rows, corpus, order, cur)))
    rows, corpus, order, cur = _world()
    rows[0]["since"] = "<=2"
    assert _errors((rows, corpus, order, cur)) == []


def test_k5_class_a_descriptor_key() -> None:
    rows, corpus, order, cur = _world()
    rows[0]["description"] = "x"
    assert any("description" in e and "(K5)" in e for e in _errors((rows, corpus, order, cur)))


def test_null_metadata_is_skipped() -> None:
    rows, corpus, order, cur = _world()
    corpus["p"]["rows"]["telemetry_observations"].append(
        {"observation_type": "model_call", "event_kind": "point", "metadata": None}
    )
    assert _errors((rows, corpus, order, cur)) == []


def test_check_runs_in_the_fast_and_full_tiers() -> None:
    pre = {s.name for s in registry.pre_sequence() if s.kind == "check"}
    full = {s.name for s in registry.full_sequence() if s.kind == "check"}
    assert "validate_telemetry_metadata_keys" in pre and "validate_telemetry_metadata_keys" in full
    entry = next(e for e in ENTRIES if e.name == "validate_telemetry_metadata_keys")
    globs = set(entry.pre_globs or ())
    for needed in (
        "docs/contracts/telemetry-metadata-keys.yaml",
        "config/telemetry/parser_versions.yaml",
        "tests/fixtures/turn_capture/golden/**",
        "config/lambda/ducklake/field_semantics.yaml",
        "docs/ROADMAP-PLATFORM.yaml",
        "scripts/contracts_schema.py",
    ):
        assert needed in globs


def test_wrapper_reports_a_failing_registry(tmp_path: Path) -> None:
    failed: list[str] = []
    check.validate_telemetry_metadata_keys(failed, root=tmp_path)
    assert len(failed) == 1 and "inputs unreadable" in failed[0]


def test_wrapper_reports_row_errors(monkeypatch) -> None:
    rows, corpus, order, cur = _world()
    rows.append(_row(key="ghost"))
    monkeypatch.setattr(check, "load_registry_inputs", lambda root: (rows, corpus, order, cur))
    failed: list[str] = []
    check.validate_telemetry_metadata_keys(failed, root=ROOT)
    assert len(failed) == 1 and "ghost" in failed[0] and "(K2)" in failed[0]
