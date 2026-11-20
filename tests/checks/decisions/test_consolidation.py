"""Mirror test for scripts/checks/decisions/consolidation.py (Decision 278), on tmp roots with a fake origin/main."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from scripts.checks.decisions import consolidation as c

ROOT = Path(__file__).resolve().parents[3]
DEC = "docs/DECISIONS.md"
CT = "docs/contracts/c.yaml"
DT = "docs/contracts/d.yaml"
MREL = f"{c.MANIFEST_DIR}/m.yaml"
THING, SUB, OTHER, TB = f"{CT}#thing", f"{CT}#thing.sub", f"{CT}#thing.other", f"{CT}#text_bound"
HELD = f"{DT}#held"
CHECK = "tests/t.py::test_x"

BASE_1 = """## Decision 1: Root rule (Decided)

```yaml
number: 1
status: Decided
```

**Status:** Decided
**Date:** 2026-01-01
**Warehouse ID:** dec-001 (per Decision 84 backfill)

**Problem:** The old problem text.

**Decision:**
1. First ruling about alpha.
2. Second ruling about beta
   with a continuation line.

**Rationale:** Because reasons.

**Reversal conditions:** (a) alpha fails; (b) beta fails.

```yaml reversal-conditions
decision: 1
conditions:
  - id: c1
    kind: manual
    description: "x"
```

**Related:** Decision 2.

---

> **Amended by Decision 2 (2026-02-01):** note text.

"""
BASE_2 = """## Decision 2: Amender (amends Decision 1) (Decided)

```yaml
number: 2
status: Decided
amends: [1]
```

**Status:** Decided

**Decision:**
1. alpha beta

"""
ENTRY_4 = """## Decision 4: Consolidating (Decided)

```yaml
number: 4
status: Decided
kind:
  value: direction
  justification: j
supersedes: [1, 2]
delegates:
  - key: docs/contracts/c.yaml#thing
    change_rule: operator_approval
    bounds: {text: "x"}
  - key: docs/contracts/c.yaml#text_bound
    change_rule: contract_amendment
    bounds: {text: "y"}
```

**Status:** Decided

**Decision:**
1. Clause one states the ruling.
2. Clause two names Decision 3 as carried forward.

```yaml reversal-conditions
decision: 4
conditions:
  - id: c1
    kind: manual
    description: "d"
```

---

"""
ENTRY_3 = "## Decision 3: Carried (amends Decision 1) (Decided)\n\n**Status:** Decided\n\n---\n\n"
ENTRY_7 = "## Decision 7: Holder (Decided)\n\n**Status:** Decided\n\nThe rule says iota here.\n\n---\n\n"
ENTRY_9 = (
    "## Decision 9: Other delegator (Decided)\n\n```yaml\nnumber: 9\nstatus: Decided\ndelegates:\n"
    f"  - key: {HELD}\n    change_rule: contract_amendment\n    bounds: {{text: z}}\n```\n\n**Status:** Decided\n\n---\n\n"
)


def _stub(n: int, point_map: str) -> str:
    return (
        f"## Decision {n}: Old (Decided)\n\n**Status:** Superseded\n**Date:** 2026-01-01\n\n**Decision:** Superseded by "
        f"Decision 4, which restates it. Point map: {point_map}. Decision 3 stays live.\n\n"
        "**Superseded by: Decision 4**\n\n---\n\n"
    )


def _rows() -> list[dict]:
    def row(n: int, currency: str, amends: list[int]) -> dict:
        return {"number": n, "live": True, "currency": currency, "amends": amends}

    return [
        row(1, "superseded_compacted", []),
        row(2, "superseded_compacted", [1]),
        row(3, "current", [1]),
        row(4, "current", []),
        row(7, "current", []),
        row(9, "current", []),
    ]


def _units() -> list[dict]:
    retired = "this unit is history only and sets no live ruling at all today"
    return [
        {"id": "1 problem p1", "retired": retired},
        {"id": "1 pt1", "restated": [1]},
        {"id": "1 pt2", "delegated": [THING], "moved": [SUB], "token": "alpha"},
        {"id": "1 rationale p1", "delegated": [TB], "check": CHECK},
        {"id": "1 reversal a", "held": ["Decision 7"], "token": "iota"},
        {"id": "1 reversal b", "held": [HELD], "token": "eta", "check": CHECK},
        {"id": "1 stanza c1", "stanza": ["c1"]},
        {"id": "1 note 1", "restated": [2]},
        {"id": "2 pt1", "moved": [SUB], "token": "alpha"},
    ]


def _spec() -> dict[str, Any]:
    return {
        "head": {1: _stub(1, "pt1 -> cl.1; pt2 -> thing and sub"), 2: _stub(2, "pt1 -> sub"), 3: ENTRY_3, 4: ENTRY_4}
        | {7: ENTRY_7, 9: ENTRY_9},
        "base": {DEC: BASE_1 + BASE_2, MREL: None},
        "rows": _rows(),
        "manifest": {"supersedes": [1, 2], "carried_forward": [3], "base_sha": "abc", "units": _units()},
        "files": {
            CT: yaml.safe_dump({"thing": {"sub": "alpha beta gamma", "other": "delta epsilon"}, "text_bound": "zeta"}),
            DT: yaml.safe_dump({"held": "eta theta"}),
            "tests/t.py": "def test_x():\n    pass\n",
        },
    }


def _write(root: Path, spec: dict[str, Any]) -> None:
    files = dict(spec["files"])
    files[DEC] = "".join(spec["head"][n] for n in sorted(spec["head"]))
    files["docs/decisions-index.json"] = json.dumps({"decisions": spec["rows"]})
    if spec["manifest"] is not None:
        files[MREL] = yaml.safe_dump(spec["manifest"])
    files["docs/contracts/decision-compaction.yaml"] = yaml.safe_dump({"consolidation": {"threshold": 5}})
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


def _problems(root: Path, spec: dict[str, Any]) -> list[str]:
    _write(root, spec)
    return c.manifest_problems(root, lambda r, rel: spec["base"].get(rel))


def _unit(spec: dict[str, Any], uid: str) -> dict:
    return next(u for u in spec["manifest"]["units"] if u["id"] == uid)


def _set(uid: str, **fields: Any) -> Callable[[dict], None]:
    def mutate(spec: dict) -> None:
        unit = _unit(spec, uid)
        for key in [k for k in unit if k != "id"]:
            del unit[key]
        unit.update(fields)

    return mutate


def _drop(spec: dict) -> None:
    spec["manifest"]["units"] = [u for u in spec["manifest"]["units"] if u["id"] != "1 pt1"]


def _drop_check(uid: str) -> Callable[[dict], None]:
    return lambda spec: _unit(spec, uid).pop("check")


def _add_extra(spec: dict) -> None:
    spec["manifest"]["units"].append({"id": "1 pt9", "retired": _unit(spec, "1 problem p1")["retired"]})


def _victim_full(spec: dict) -> None:
    spec["head"][2] = BASE_2


def _stub_text(spec: dict) -> None:
    spec["head"][1] = _stub(1, "pt1 -> cl.9")


def _base_text(spec: dict) -> None:
    spec["base"][DEC] = BASE_1 + BASE_2.replace("1. alpha beta", "1. omega")


def _no_clause(spec: dict) -> None:
    spec["head"][4] = ENTRY_4.replace("Decision 3", "the other one")


def _merged_drop(spec: dict) -> None:
    old = copy.deepcopy(spec["manifest"])
    old["units"].append({"id": "1 pt9", "retired": "x"})
    spec["base"][MREL] = yaml.safe_dump(old)


def _duplicate(spec: dict) -> None:
    spec["manifest"]["units"].append(dict(_unit(spec, "1 pt1")))


def _not_mapping(spec: dict) -> None:
    spec["manifest"]["units"].append("oops")


def _set_manifest(**fields: Any) -> Callable[[dict], None]:
    return lambda spec: spec["manifest"].update(fields)


def _no_manifest(spec: dict) -> None:
    spec["manifest"] = None


def _held_decision(n: int) -> Callable[[dict], None]:
    return _set("1 reversal a", held=[f"Decision {n}"], token="iota")


def _token_gone(spec: dict) -> None:
    spec["head"][7] = ENTRY_7.replace("iota", "kappa")


def _carried_gone(spec: dict) -> None:
    spec["rows"] = [r for r in spec["rows"] if r["number"] != 3]


def _carried_no_edge(spec: dict) -> None:
    spec["rows"][2]["amends"] = []


def _consolidator_superseded(spec: dict) -> None:
    spec["rows"][3]["currency"] = "superseded_pointer"
    _drop(spec)


DEFECTS: dict[str, tuple[Callable[[dict], None], str]] = {
    "missing unit": (_drop, "is missing from the manifest"),
    "extra unit": (_add_extra, "is not a unit of any victim body"),
    "unresolved key": (_set("1 pt1", moved=[f"{CT}#thing.zzz"], token="a"), "does not resolve"),
    "absent token": (_set("1 reversal b", held=[HELD], token="nothere", check=CHECK), "token is absent from"),
    "absent stanza": (_set("1 stanza c1", stanza=["zz"]), "stanza zz is not in"),
    "short retirement": (_set("1 problem p1", retired="too short"), "under ten words"),
    "unchecked text-bound": (_drop_check("1 rationale p1"), "names no resolving check"),
    "unresolving check": (_set("1 rationale p1", delegated=[TB], check="tests/t.py::nope"), "names no resolving check"),
    "victim not a stub": (_victim_full, "is not a compacted stub"),
    "point map mismatch": (_stub_text, "point map"),
    "moved outside delegated": (_set("2 pt1", moved=[HELD], token="eta"), "sits outside every delegated key"),
    "held outside delegated": (_set("2 pt1", held=[OTHER], token="delta"), "sits outside every delegated key"),
    "held text-bound without check": (_drop_check("1 reversal b"), "names no resolving check"),
    "held target compacted": (_held_decision(1), "absent, superseded or a victim"),
    "held target absent": (_held_decision(99), "absent, superseded or a victim"),
    "held target not a number": (_set("1 reversal a", held=["Decision x"], token="a"), "absent, superseded or a victim"),
    "held body lacks token": (_token_gone, "token is absent from Decision 7"),
    "moved alone base text": (_base_text, "its base text is not in its target"),
    "retired beside another": (_set("1 pt1", restated=[1], retired="x " * 12), "retired beside another disposition"),
    "no disposition": (_set("1 pt1"), "has no disposition"),
    "unit not a mapping": (_not_mapping, "is not a mapping"),
    "duplicate unit": (_duplicate, "duplicate unit id"),
    "restated clause absent": (_set("1 pt1", restated=[9]), "restated clause 9"),
    "delegated key not delegated": (_set("1 pt1", delegated=[SUB], token="a"), "not a key the entry delegates"),
    "carried_forward no clause": (_no_clause, "carried_forward Decision 3"),
    "carried_forward absent": (_carried_gone, "carried_forward Decision 3"),
    "carried_forward lost edge": (_carried_no_edge, "carried_forward Decision 3"),
    "merged manifest drops a unit": (_merged_drop, "lost unit"),
    "manifest deleted": (_no_manifest, "no manifest lists it"),
    "no consolidating entry": (_set_manifest(supersedes=[1]), "no entry's envelope supersedes exactly"),
    "bad shape": (_set_manifest(units="x"), "must be lists"),
}


def test_fixture_consolidation_is_accepted_and_each_defect_is_caught(tmp_path: Path) -> None:
    clean = tmp_path / "clean"
    assert _problems(clean, _spec()) == []
    for name, (mutate, expected) in DEFECTS.items():
        spec = _spec()
        mutate(spec)
        found = _problems(tmp_path / name.replace(" ", "_"), spec)
        assert any(expected in p for p in found), (name, found)
    spec = _spec()
    _consolidator_superseded(spec)
    assert _problems(tmp_path / "history", spec) == []


def test_a_manifest_unchanged_on_main_is_not_repartitioned(tmp_path: Path) -> None:
    spec = _spec()
    spec["base"][MREL] = yaml.safe_dump(spec["manifest"])
    spec["base"][DEC] = "changed parser input"
    assert _problems(tmp_path, spec) == []


def test_freeze_skips_with_a_marker_when_origin_main_is_unreachable(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    spec = _spec()
    spec["base"] = {}
    _write(tmp_path, spec)
    assert c.manifest_problems(tmp_path, lambda r, rel: None) == []
    assert "SKIP" in capsys.readouterr().out


def test_examined_counts_units_or_stubs_outside_decision_277(tmp_path: Path) -> None:
    spec = _spec()
    _write(tmp_path, spec)
    assert c.scan(tmp_path, lambda r, rel: spec["base"].get(rel)) == ([], 9)
    spec["manifest"] = None
    (tmp_path / MREL).unlink()
    assert c.examined(tmp_path, lambda r, rel: None) == 2
    assert c.examined(tmp_path / "empty", lambda r, rel: None) == 0


def test_a_stub_pointing_at_277_or_a_missing_or_superseded_entry_needs_no_manifest(tmp_path: Path) -> None:
    spec = _spec()
    spec["manifest"] = None
    spec["head"][1] = _stub(1, "pt1 -> cl.1").replace("Decision 4", "Decision 277")
    spec["head"][2] = _stub(2, "pt1 -> cl.1").replace("Decision 4", "Decision 55")
    assert _problems(tmp_path, spec) == []
    spec["head"][2] = _stub(2, "pt1 -> cl.1")
    spec["rows"][3]["currency"] = "superseded_pointer"
    assert _problems(tmp_path / "b", spec) == []
    spec["head"][2] = _stub(2, "pt1 -> cl.1").replace("Decision 4", "Decision x")
    assert _problems(tmp_path / "c", spec) == []


def test_load_manifests_reads_a_malformed_file_as_empty(tmp_path: Path) -> None:
    (tmp_path / c.MANIFEST_DIR).mkdir(parents=True)
    (tmp_path / c.MANIFEST_DIR / "x.yaml").write_text("- a\n- b\n", encoding="utf-8")
    (tmp_path / c.MANIFEST_DIR / "y.yaml").write_text(": : :\n\t-", encoding="utf-8")
    assert c.load_manifests(tmp_path) == [(f"{c.MANIFEST_DIR}/x.yaml", {}), (f"{c.MANIFEST_DIR}/y.yaml", {})]
    assert c.manifest_problems(tmp_path, lambda r, rel: None)[0].endswith("must be lists")


def test_partition_ids_cover_both_body_shapes() -> None:
    lead = """## Decision 5: Lead-in (Decided)

**Decision:** A lead sentence sharing the paragraph.
1. First point
   continued
2. Second point

   indented continuation paragraph

**Options:**
1. Another list starts again

[Amendment 2026-01-01: a trailing note.]

**Reversal conditions:** lead (a) one; (b) two.

**Related:** Decision 1.

---
"""
    units = c.partition(5, lead)
    assert sorted(units) == sorted(
        ["5 decision p1", "5 pt1", "5 pt2", "5 options pt1", "5 note 1", "5 reversal a", "5 reversal b"]
    )
    assert "indented continuation" in units["5 pt2"] and units["5 reversal a"].startswith("**Reversal")
    assert c.partition(5, "## Decision 5: T (Decided)\n\n**Problem:**\nbare text\n\n**Other:** a\n**More:** b\n") == {
        "5 problem p1": "bare text",
        "5 other p1": "**Other:** a",
        "5 more p1": "**More:** b",
    }
    assert sorted(c.partition(5, "## Decision 5: T (Decided)\n\n**Decision:**\nintro\n1. only\nmore")) == [
        "5 decision p1",
        "5 pt1",
    ]
    assert list(c.partition(5, "## Decision 5: T (Decided)\n\n**Decision:**\n1. one\n\nplain after\n")) == [
        "5 pt1",
        "5 decision p1",
    ]
    assert list(c.partition(5, "## Decision 5: T (Decided)\n\n```yaml reversal-conditions\n- bad\n```\n")) == []


def _row(n: int, currency: str = "current", amends: list[int] | None = None, live: bool = True) -> dict:
    return {"number": n, "currency": currency, "amends": amends or [], "live": live}


def test_live_counts_exclude_stubs_and_a_compacted_root_is_no_candidate() -> None:
    rows = [_row(1)] + [_row(n, amends=[1]) for n in range(2, 7)] + [_row(7, "superseded_compacted", [1])]
    rows += [_row(8, live=False, amends=[1]), _row(20, "superseded_compacted")]
    rows += [_row(n, amends=[20]) for n in range(21, 27)] + [_row(30, amends=[404])]
    assert c.live_amender_counts(rows)[1] == 5
    assert c.candidates(rows, 5) == {1: 5}
    assert c.candidates(rows, 6) == {}


def test_nomination_counts_the_draft_once_and_names_the_chain() -> None:
    rows = [_row(1)] + [_row(n, amends=[1]) for n in range(2, 6)]
    sizes = {1: 100, 2: 10, 3: 10, 4: 10, 5: 10}
    assert c.nomination_info("N", [1, 99], rows, 5, sizes)[0].startswith("NOMINATION: Decision N amends Decision 1")
    assert "5 live amenders" in c.nomination_info("N", [1], rows, 5, sizes)[0]
    assert "chain 140 bytes" in c.nomination_info("N", [1], rows, 5, sizes)[0]
    assert c.nomination_info(5, [1], rows, 5, sizes) == []
    assert c.nomination_info("N", [1], rows, 6, sizes) == []
    assert c.nomination_info("N", [2], rows, 1, sizes) == []


def test_warn_line_names_the_largest_by_count() -> None:
    rows = [_row(1), _row(10)] + [_row(n, amends=[1]) for n in range(2, 5)] + [_row(n, amends=[10]) for n in range(11, 16)]
    line = c.candidates_warn_line(rows, 3)
    assert line is not None and "2 Decision root(s)" in line and "largest Decision 10 with 5" in line
    assert "consolidation threshold" in line and "WARN" in line
    assert c.candidates_warn_line(rows, 9) is None


def test_threshold_and_row_readers_are_total(tmp_path: Path) -> None:
    assert c.load_threshold(tmp_path) is None and c.load_rows(tmp_path) == [] and c.entry_sizes(tmp_path) == {}
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/decisions-index.json").write_text('{"decisions": "x"}', encoding="utf-8")
    assert c.load_rows(tmp_path) == []
    (tmp_path / "docs/decisions-index.json").write_text("not json", encoding="utf-8")
    assert c.load_rows(tmp_path) == []
    (tmp_path / "docs/contracts").mkdir()
    (tmp_path / c.CONTRACT).write_text("consolidation: x\n", encoding="utf-8")
    assert c.load_threshold(tmp_path) is None
    (tmp_path / c.CONTRACT).write_text("consolidation: {threshold: true}\n", encoding="utf-8")
    assert c.load_threshold(tmp_path) is None
    (tmp_path / DEC).write_text(BASE_1 + BASE_2, encoding="utf-8")
    assert set(c.entry_sizes(tmp_path)) == {1, 2}


def test_render_point_map_joins_targets_and_units() -> None:
    units = [
        {"id": "7 pt2", "stanza": ["s"], "retired": "r"},
        {"id": "7 pt1", "restated": [1, 2], "delegated": [THING], "held": ["Decision 3"]},
        {"id": "8 pt1", "restated": [1]},
        {"id": "7 note 1", "restated": [1]},
    ]
    assert c.render_point_map(units, 7) == "pt1 -> cl.1 and cl.2 and thing and Decision 3; pt2 -> stanza s and retired"


def test_threshold_is_delegated_within_bounds() -> None:
    import re

    from scripts.decisions_md import extract_entry_envelope, iter_decision_sections

    text = (ROOT / DEC).read_text(encoding="utf-8")
    found = [
        extract_entry_envelope(b)
        for _, b in iter_decision_sections(text)
        if "consolidation.threshold" in b and (extract_entry_envelope(b) or {}).get("amends") == [149]
    ]
    assert len(found) == 1
    bounds = {d["key"].split("#")[1]: d["bounds"] for d in found[0]["delegates"]}
    contract = yaml.safe_load((ROOT / c.CONTRACT).read_text(encoding="utf-8"))["consolidation"]
    assert bounds["consolidation.threshold"] == {"min": 3, "max": 9}
    assert 3 <= contract["threshold"] <= 9 and c.load_threshold(ROOT) == contract["threshold"]
    assert all(k in contract for k in ("manifest", "abort", "procedure"))
    assert re.fullmatch(r"\d+", str(found[0]["number"]))


def test_the_real_repository_has_no_manifest_problem_and_277_stubs_are_excluded() -> None:
    from scripts.decisions_md import is_compacted_stub

    entries = c._entries(ROOT)
    stubs = [n for n, _ in c._point_map_stubs(entries)]
    assert {92, 154, 190} <= set(stubs)
    assert all(
        extract == 277 for extract in (int(c.extract_superseded_by(entries[n]).removeprefix("dec-")) for n in (92, 154, 190))
    )
    assert is_compacted_stub(entries[92])
    problems, examined = c.scan(ROOT, lambda r, rel: None)
    assert problems == [] and examined >= 0


def test_key_text_flattens_nested_values_and_rejects_a_malformed_key(tmp_path: Path) -> None:
    (tmp_path / "k.yaml").write_text("a:\n  b: [x, {c: y}]\n", encoding="utf-8")
    assert c._key_text(tmp_path, "k.yaml#a.b") == "x c y"
    assert c._key_text(tmp_path, "not a key") is None
    assert c._key_text(tmp_path, "k.yaml#a.zz") is None
