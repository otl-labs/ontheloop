"""Mirror test for scripts/checks/contracts/_rule_ratchet.py: each loosening or narrowing arm on a fixture base and head
(git-free -- the module compares two materialised trees), with and without its marker; tightenings stay free."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from scripts.checks.contracts import _rule_ratchet as ratchet

_CENSUS = "census: 0 violators (2026-10-15)"
_TEMP = {"class": "temporary", "reason": "staged", "owner": "rec-1", "review_by": "2026-12-31"}


def _field(**dq: Any) -> dict[str, Any]:
    return {"type": "str", "dq_intent": dq}


def _base_spec() -> dict[str, Any]:
    return {
        "registry": ["alpha", "beta"],
        "tables": {
            "ops_demo": {
                "governance": {"table_class": "SCD2"},
                "fields": {
                    "status": _field(not_null=True, accepted_values={"values": ["open", "closed", "dropped"]}),
                    "title": _field(not_null=True, min_length={"value": 5, "exclude_before": "2026-06-01"}),
                    "tags": _field(array_element_format={"pattern": "^a$", "exclude_before": "2026-06-01"}),
                    "plain": _field(pattern={"value": "^x$"}),
                    "staged": _field(min_length={"value": 2}, write_time_exemptions={"min_length": dict(_TEMP)}),
                    "link": _field(
                        acceptance_lint={"require_discrimination": True},
                        write_time_exemptions={
                            "acceptance_lint": {"class": "repository_state", "reason": "r", "owner": "x.py"}
                        },
                    ),
                    "ref": _field(
                        array_element_reference={},
                        write_time_exemptions={
                            "array_element_reference": {"class": "cross_row", "reason": "r", "owner": "rec-2"}
                        },
                    ),
                    "marked": _field(
                        array_element_reference={}, write_time_exemptions={"array_element_reference": dict(_TEMP)}
                    ),
                    "blob": _field(
                        content_inline_threshold_bytes=10, write_time_exemptions={"inline_within_threshold": dict(_TEMP)}
                    ),
                    "classless": _field(
                        min_length={"value": 1}, write_time_exemptions={"min_length": {"reason": "r", "owner": "rec-3"}}
                    ),
                },
            },
            "ev_demo": {
                "governance": {"table_class": "event"},
                "fields": {
                    "body": _field(
                        content_inline_threshold_bytes=10,
                        full_output_cap_bytes=100,
                        write_time_exemptions={"inline_within_threshold": dict(_TEMP)},
                    ),
                    "kind": _field(accepted_values={"values": ["a", "b"]}),
                },
            },
        },
    }


def _materialise(root: Path, spec: dict[str, Any]) -> Path:
    contracts = root / "docs" / "contracts"
    contracts.mkdir(parents=True)
    for name, doc in spec["tables"].items():
        (contracts / f"{name}.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    fs = root / ratchet.FIELD_SEMANTICS_REL
    fs.parent.mkdir(parents=True)
    fs.write_text(yaml.safe_dump({"ops_tables": {name: {} for name in spec["tables"]}}), encoding="utf-8")
    registry = root / ratchet.REGISTRY_REL
    registry.parent.mkdir(parents=True)
    registry.write_text(yaml.safe_dump({"entries": [{"canonical_id": i} for i in spec["registry"]]}), encoding="utf-8")
    return root


def _run(
    tmp_path: Path, head: Callable[[dict[str, Any]], None] | None, base: Callable[[dict[str, Any]], None] | None = None
) -> list[str]:
    base_spec = _base_spec()
    if base:
        base(base_spec)
    head_spec = copy.deepcopy(base_spec)
    if head:
        head(head_spec)
    return ratchet.ratchet_errors(_materialise(tmp_path / "base", base_spec), _materialise(tmp_path / "head", head_spec))


def _dq(spec: dict[str, Any], column: str, table: str = "ops_demo") -> dict[str, Any]:
    return spec["tables"][table]["fields"][column]["dq_intent"]


def _entry(spec: dict[str, Any], column: str, table: str = "ops_demo", **entry: Any) -> None:
    spec["tables"][table]["fields"][column].setdefault("amendment_log", []).append({"date": "2026-10-15", **entry})


def _narrow_entry(story: str = f"values closed and dropped retired; {_CENSUS}", semantic_break: bool = True) -> dict[str, Any]:
    return {"change_class": "accepted_values_narrow", "semantic_break": semantic_break, "migration_story": story}


def _census_entry() -> dict[str, Any]:
    return {"change_class": "prose_improvement", "semantic_break": True, "migration_story": f"narrowed; {_CENSUS}"}


# (name, mutation, expected message substring or None when it must pass)
_ARMS: list[tuple[str, Callable[[dict[str, Any]], None], str | None]] = [
    ("value-removed", lambda s: _dq(s, "status")["accepted_values"]["values"].remove("closed"), "lost value(s)"),
    (
        "value-removed-with-entry",
        lambda s: (
            _dq(s, "status")["accepted_values"]["values"].remove("closed"),
            _dq(s, "status")["accepted_values"]["values"].remove("dropped"),
            _entry(s, "status", **_narrow_entry()),
        ),
        None,
    ),
    (
        "value-removed-entry-misses-a-value",
        lambda s: (
            _dq(s, "status")["accepted_values"]["values"].remove("closed"),
            _dq(s, "status")["accepted_values"]["values"].remove("dropped"),
            _entry(s, "status", **_narrow_entry(f"closed retired; {_CENSUS}")),
        ),
        "lost value(s)",
    ),
    (
        "value-removed-entry-lacks-census",
        lambda s: (
            _dq(s, "status")["accepted_values"]["values"].remove("closed"),
            _entry(s, "status", **_narrow_entry("closed retired")),
        ),
        "lost value(s)",
    ),
    (
        "value-removed-entry-not-a-break",
        lambda s: (
            _dq(s, "status")["accepted_values"]["values"].remove("closed"),
            _entry(s, "status", **_narrow_entry("closed retired; " + _CENSUS, semantic_break=False)),
        ),
        "lost value(s)",
    ),
    ("value-added", lambda s: _dq(s, "status")["accepted_values"]["values"].append("held"), None),
    ("min-length-raised", lambda s: _dq(s, "title")["min_length"].update(value=8), "params narrowed"),
    (
        "min-length-raised-with-entry",
        lambda s: (_dq(s, "title")["min_length"].update(value=8), _entry(s, "title", **_census_entry())),
        None,
    ),
    ("min-length-lowered", lambda s: _dq(s, "title")["min_length"].update(value=2), "params narrowed"),
    ("pattern-changed", lambda s: _dq(s, "plain").update(pattern={"value": "^y$"}), "params narrowed"),
    (
        "pattern-changed-with-entry",
        lambda s: (_dq(s, "plain").update(pattern={"value": "^y$"}), _entry(s, "plain", **_census_entry())),
        None,
    ),
    ("element-format-changed", lambda s: _dq(s, "tags")["array_element_format"].update(pattern="^b$"), "params narrowed"),
    ("date-earlier", lambda s: _dq(s, "title")["min_length"].update(exclude_before="2026-05-15"), "moved earlier"),
    (
        "date-earlier-with-entry",
        lambda s: (_dq(s, "title")["min_length"].update(exclude_before="2026-05-15"), _entry(s, "title", **_census_entry())),
        None,
    ),
    ("date-later", lambda s: _dq(s, "title")["min_length"].update(exclude_before="2026-09-01"), "moved later"),
    (
        "date-later-even-with-entry",
        lambda s: (_dq(s, "title")["min_length"].update(exclude_before="2026-09-01"), _entry(s, "title", **_census_entry())),
        "moved later",
    ),
    (
        "date-added-to-live-leg",
        lambda s: _dq(s, "plain")["pattern"].update(exclude_before="2026-09-01"),
        "never added to a live leg",
    ),
    (
        "date-added-even-with-entry",
        lambda s: (_dq(s, "plain")["pattern"].update(exclude_before="2026-09-01"), _entry(s, "plain", **_census_entry())),
        "never added to a live leg",
    ),
    ("date-removed", lambda s: _dq(s, "tags")["array_element_format"].pop("exclude_before"), "was removed"),
    (
        "date-removed-even-with-entry",
        lambda s: (_dq(s, "tags")["array_element_format"].pop("exclude_before"), _entry(s, "tags", **_census_entry())),
        "was removed",
    ),
    (
        "undated-new-scd2-leg",
        lambda s: _dq(s, "plain").update(min_length={"value": 1}),
        "new SCD2 leg without an exclude_before",
    ),
    ("dated-new-scd2-leg", lambda s: _dq(s, "plain").update(min_length={"value": 1, "exclude_before": "2026-10-15"}), None),
    ("undated-new-event-leg", lambda s: _dq(s, "kind", "ev_demo").update(min_length={"value": 1}), None),
    (
        "new-exemption-on-existing-rule",
        lambda s: _dq(s, "title").update(write_time_exemptions={"min_length": dict(_TEMP)}),
        "new exemption on an existing rule",
    ),
    (
        "new-exemption-with-entry",
        lambda s: (
            _dq(s, "title").update(write_time_exemptions={"min_length": dict(_TEMP)}),
            _entry(s, "title", **_census_entry()),
        ),
        None,
    ),
    (
        "new-exemption-on-a-new-rule",
        lambda s: _dq(s, "plain").update(min_length={"value": 1}, write_time_exemptions={"min_length": dict(_TEMP)}),
        None,
    ),
    (
        "content-leg-exemption-added",
        lambda s: _dq(s, "body", "ev_demo")["write_time_exemptions"].update(within_cap=dict(_TEMP)),
        "new exemption on an existing rule",
    ),
    (
        "content-leg-exemption-added-with-entry",
        lambda s: (
            _dq(s, "body", "ev_demo")["write_time_exemptions"].update(within_cap=dict(_TEMP)),
            _entry(s, "body", "ev_demo", **_census_entry()),
        ),
        None,
    ),
    (
        "content-leg-class-changed",
        lambda s: _dq(s, "body", "ev_demo")["write_time_exemptions"]["inline_within_threshold"].update({"class": "cross_row"}),
        "class changed",
    ),
    (
        "exemption-class-changed",
        lambda s: _dq(s, "link")["write_time_exemptions"]["acceptance_lint"].update(
            {"class": "temporary", "review_by": "2027-01-01"}
        ),
        "class changed",
    ),
    (
        "exemption-class-changed-with-entry",
        lambda s: (
            _dq(s, "link")["write_time_exemptions"]["acceptance_lint"].update(
                {"class": "temporary", "review_by": "2027-01-01"}
            ),
            _entry(s, "link", **_census_entry()),
        ),
        None,
    ),
    (
        "review-by-later",
        lambda s: _dq(s, "staged")["write_time_exemptions"]["min_length"].update(review_by="2027-06-30"),
        "review_by moved later",
    ),
    (
        "review-by-later-with-entry",
        lambda s: (
            _dq(s, "staged")["write_time_exemptions"]["min_length"].update(review_by="2027-06-30"),
            _entry(
                s,
                "staged",
                change_class="governance_note_add",
                semantic_break=False,
                summary="renewed to 2027-06-30: owner needs a quarter",
            ),
        ),
        None,
    ),
    (
        "review-by-earlier",
        lambda s: _dq(s, "staged")["write_time_exemptions"]["min_length"].update(review_by="2026-11-30"),
        None,
    ),
    (
        "first-review-by-on-a-classless-exemption",
        lambda s: _dq(s, "classless")["write_time_exemptions"]["min_length"].update(
            {"class": "temporary", "review_by": "2027-01-31"}
        ),
        None,
    ),
    ("leg-removed-without-exemption", lambda s: _dq(s, "plain").pop("pattern"), "was removed"),
    ("leg-removed-under-cross-row", lambda s: _dq(s, "ref").pop("array_element_reference"), "was removed"),
    ("leg-removed-under-repository-state", lambda s: _dq(s, "link").pop("acceptance_lint"), "was removed"),
    (
        "leg-removed-under-temporary",
        lambda s: (_dq(s, "staged").pop("min_length"), _dq(s, "staged").pop("write_time_exemptions")),
        None,
    ),
    (
        "producer-side-leg-removed-under-temporary",
        lambda s: (_dq(s, "marked").pop("array_element_reference"), _dq(s, "marked").pop("write_time_exemptions")),
        None,
    ),
    (
        "content-leg-removed-under-temporary",
        lambda s: (
            _dq(s, "body", "ev_demo").pop(ratchet.CONTENT_THRESHOLD),
            _dq(s, "body", "ev_demo").pop("write_time_exemptions"),
        ),
        None,
    ),
    ("temporary-exemption-dropped-from-an-undated-rule", lambda s: _dq(s, "staged").pop("write_time_exemptions"), "re-arms"),
    (
        "temporary-exemption-dropped-with-entry",
        lambda s: (_dq(s, "staged").pop("write_time_exemptions"), _entry(s, "staged", **_census_entry())),
        None,
    ),
    (
        "temporary-exemption-dropped-from-a-dated-rule",
        lambda s: (
            _dq(s, "staged")["min_length"].update(exclude_before="2026-10-15"),
            _dq(s, "staged").pop("write_time_exemptions"),
        ),
        "never added to a live leg",
    ),
    (
        "content-leg-exemption-dropped-from-an-scd2-rule",
        lambda s: _dq(s, "blob").pop("write_time_exemptions"),
        "re-arms",
    ),
    ("temporary-exemption-dropped-on-an-event-table", lambda s: _dq(s, "body", "ev_demo").pop("write_time_exemptions"), None),
    ("registry-removed", lambda s: s["registry"].remove("beta"), "removed from the source registry"),
    ("registry-added", lambda s: s["registry"].append("gamma"), None),
    ("table-and-contract-removed", lambda s: s["tables"].pop("ops_demo"), "was removed"),
    (
        "new-contract-founding-legs-are-free",
        lambda s: s["tables"].update(
            ops_new={"governance": {"table_class": "SCD2"}, "fields": {"c": _field(not_null=True, min_length={"value": 1})}}
        ),
        None,
    ),
    (
        "tightening-a-pattern-is-not-free",
        lambda s: _dq(s, "tags").update(pattern={"value": "^z$"}),
        "new SCD2 leg without an exclude_before",
    ),
]


@pytest.mark.parametrize(("name", "mutate", "message"), _ARMS, ids=[a[0] for a in _ARMS])
def test_loosening_and_narrowing_need_their_marker(tmp_path: Path, name: str, mutate: Callable, message: str | None) -> None:
    errors = _run(tmp_path, mutate)
    if message is None:
        assert errors == [], (name, errors)
    else:
        assert any(message in e for e in errors), (name, errors)


def test_the_unchanged_tree_passes(tmp_path: Path) -> None:
    assert _run(tmp_path, None) == []


def test_legacy_spelled_base_contracts_read_alike(tmp_path: Path) -> None:
    def legacy_base(spec: dict[str, Any]) -> None:
        _dq(spec, "status")["not_null"] = {"enforced": True}
        _dq(spec, "title")["not_null"] = {"enforced": True}
        spec["tables"]["ops_demo"]["fields"]["optional_old"] = _field(not_null={"enforced": False})
        _dq(spec, "kind", "ev_demo")["accepted_values"] = {"values": ["a", "b"], "enforced": False}

    def respelled(spec: dict[str, Any]) -> None:
        _dq(spec, "status")["not_null"] = True
        _dq(spec, "title")["not_null"] = True
        spec["tables"]["ops_demo"]["fields"]["optional_old"] = _field()
        _dq(spec, "kind", "ev_demo")["accepted_values"] = {"values": ["a", "b"]}

    assert _run(tmp_path, respelled, legacy_base) == []

    def dropped(spec: dict[str, Any]) -> None:
        respelled(spec)
        _dq(spec, "status").pop("not_null")

    errors = _run(tmp_path / "dropped", dropped, legacy_base)
    assert any("ops_demo.status.not_null was removed" in e for e in errors), errors


def test_registry_sourced_value_sets_follow_the_registry_arm(tmp_path: Path) -> None:
    def sourced(spec: dict[str, Any]) -> None:
        spec["tables"]["ops_demo"]["fields"]["src"] = {"$ref": "source-lineage.yaml#/fields/registry_key"}
        spec["tables"]["source-lineage"] = {
            "governance": {"table_class": "SCD2"},
            "fields": {"registry_key": _field()},
            "allowed_values": {"registry": ratchet.REGISTRY_REL, "key": "canonical_id"},
        }

    errors = _run(tmp_path, lambda s: s["registry"].remove("beta"), sourced)
    assert any("removed from the source registry" in e for e in errors)
    assert not any("lost value" in e for e in errors)  # the registry arm owns a sourced field's values


def test_missing_trees_have_no_scope(tmp_path: Path) -> None:
    assert ratchet.in_scope_tables(tmp_path) == [] and ratchet.registry_ids(tmp_path) == set()
