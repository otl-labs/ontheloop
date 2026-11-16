"""Tests for scripts/roadmap/plan_fingerprint.py: the FINGERPRINT-COMPLETENESS TEST (T4.32 c4)."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.roadmap import plan_fingerprint as pf
from scripts.roadmap.approved_words import Sidecar
from scripts.roadmap.plan_document import PlanDocument, ScopeEntry

_DECISIONS = "## Decision 217: Title (Decided)\n\nbody one\n\n## Decision 229: Other (Decided)\n\nbody two\n"
_PLAN = {
    "slug": "demo",
    "intent": "Build the thing",
    "plan_type": "IMPLEMENTATION",
    "verification_tier": "V2",
    "phase": "T4.32",
    "scope": [{"file": "src/a.py", "action": "Create", "purpose": "p"}],
    "cites": ["dec:217"],
    "amends": ["dec:229#4"],
    "introduces": ["thing"],
    "closes_criteria": [],
    "context": ["free text"],
    "execution_steps": ["do it"],
    "constraints": ["c"],
    "acceptance_criteria": ["a"],
    "verification_plan": [{"step": 1}],
    "gate_rounds": [],
}


def _roadmap() -> dict:
    return {
        "T1.1": {"tier": "T1", "name": "base", "status": "complete"},
        "T4.32": {
            "tier": "T4",
            "name": "verdicts",
            "status": "in_progress",
            "intent": "i",
            "depends_on": ["T1.1", "T4.9"],
            "start_edges": [],
            "files_in_scope": ["src/a.py"],
            "related_candidate_decisions": ["CD.1", "CD.2"],
            "exit_criteria": [
                {"id": "c1", "text": "one", "status": "open"},
                {"id": "c2", "text": "two", "status": "open"},
                {"id": "c3", "text": "three", "status": "met", "met_by": "PLAN-x"},
            ],
        },
        "T4.9": {"tier": "T4", "name": "nine", "status": "complete"},
    }


def _state() -> dict[str, Any]:
    return {"plan": copy.deepcopy(_PLAN), "decisions": _DECISIONS, "roadmap": _roadmap(), "north": "stars\n", "sidecar": None}


def _write_tree(root: Path, state: dict[str, Any]) -> None:
    (root / "docs" / "contracts").mkdir(parents=True)
    (root / "docs" / "DECISIONS.md").write_text(state["decisions"], encoding="utf-8")
    items = [{"id": k, **v} for k, v in state["roadmap"].items()]
    doc = {
        "document": {"id": "R", "version": 1, "status": "ratified", "filed_via": "ops_decisions:dec-108"},
        "tier_items": items,
    }
    (root / "docs" / "ROADMAP-PLATFORM.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    if state["north"] is not None:
        (root / "docs" / "contracts" / "north-star.yaml").write_text(state["north"], encoding="utf-8")


def _payload(tmp_path: Path, state: dict[str, Any], name: str = "t", extra: tuple[str, ...] = ()) -> dict[str, Any]:
    root = tmp_path / name
    _write_tree(root, state)
    return pf.projection(state["plan"], pf.load_tree(root), state["sidecar"], extra)


def _digest(tmp_path: Path, state: dict[str, Any], name: str = "t") -> str:
    return pf.fingerprint(_payload(tmp_path, state, name))


def _sidecar(file: str, before: str = "a b", after: str = "a c") -> Sidecar:
    return Sidecar.model_validate(
        {"schema_version": 1, "slug": "demo", "blocks": [{"id": "b1", "file": file, "before": before, "after": after}]}
    )


def _set(key: str, value: Any) -> Callable[[dict], None]:
    return lambda s: s["plan"].__setitem__(key, value)


def _rm_edit(fn: Callable[[dict], None]) -> Callable[[dict], None]:
    return lambda s: fn(s["roadmap"])


MOVES: dict[str, list[Callable[[dict], None]]] = {
    "intent": [_set("intent", "Build another thing")],
    "scope_paths": [lambda s: s["plan"]["scope"].append({"file": "src/b.py"})],
    "verification_tier": [_set("verification_tier", "V3")],
    "plan_type": [_set("plan_type", "REPORT-ONLY")],
    "cites": [_set("cites", ["dec:217", "dec:229"])],
    "amends": [_set("amends", ["dec:229#4", "dec:217"])],
    "introduces": [_set("introduces", ["thing", "other"])],
    "decisions": [lambda s: s.__setitem__("decisions", s["decisions"].replace("body one", "body 1"))],
    "items": [_set("closes_criteria", ["T1.1:c1"]), _set("phase", "T4.9")],
    "edges": [
        _rm_edit(lambda r: r["T4.32"].__setitem__("depends_on", ["T1.1"])),
        _rm_edit(lambda r: r["T4.32"].__setitem__("start_edges", [{"ref": "T4.9", "once": "in_progress"}])),
    ],
    "roadmap_projection": [
        _rm_edit(lambda r: r["T4.32"]["exit_criteria"][0].__setitem__("text", "uno")),
        _rm_edit(lambda r: r["T4.32"].__setitem__("status", "complete")),
    ],
    "approved_blocks": [
        lambda s: s.__setitem__("sidecar", _sidecar(f))
        for f in ("docs/DECISIONS.md", "docs/ROADMAP-PLATFORM.yaml", "docs/contracts/north-star.yaml")
    ],
    "north_star": [lambda s: s.__setitem__("north", "other stars\n")],
}

SAME = {
    "context edit": lambda s: s["plan"].__setitem__("context", ["changed"]),
    "scope purpose": lambda s: s["plan"]["scope"][0].__setitem__("purpose", "other"),
    "execution_steps": _set("execution_steps", ["different"]),
    "constraints": _set("constraints", ["different"]),
    "acceptance": _set("acceptance_criteria", ["different"]),
    "verification step": _set("verification_plan", [{"step": 1, "command": "x"}]),
    "gate row": _set("gate_rounds", [{"gate": "plan-critique", "round": 1, "verdict": "PROCEED"}]),
    "intent rewrap": _set("intent", "Build   the\n thing"),
    "scope reorder": lambda s: s["plan"]["scope"].insert(0, {"file": "src/a.py"}),
    "cites reorder": _set("cites", ["dec:217", "dec:217"]),
    "other-file sidecar": lambda s: s.__setitem__("sidecar", _sidecar("docs/other.yaml")),
    "item lists reorder": _rm_edit(
        lambda r: r["T4.32"].update(
            depends_on=["T4.9", "T1.1"],
            related_candidate_decisions=["CD.2", "CD.1"],
            exit_criteria=list(reversed(r["T4.32"]["exit_criteria"])),
        )
    ),
    "item named thrice": _set("closes_criteria", ["T4.32:c1", "T4.32:c2"]),
}


def test_every_payload_key_has_a_fixture() -> None:
    assert set(MOVES) | {"v"} == set(pf.PAYLOAD_KEYS)


@pytest.mark.parametrize("key", sorted(MOVES))
def test_each_input_moves_the_digest(key: str, tmp_path: Path) -> None:
    base = _digest(tmp_path, _state(), "base")
    for i, move in enumerate(MOVES[key]):
        state = _state()
        move(state)
        payload = _payload(tmp_path, state, f"m{i}")
        assert set(payload) == set(pf.PAYLOAD_KEYS)
        assert pf.fingerprint(payload) != base, (key, i)


def test_version_moves_digest_and_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _digest(tmp_path, _state(), "base")
    monkeypatch.setattr(pf, "PAYLOAD_VERSION", 2)
    bumped = _digest(tmp_path, _state(), "bump")
    assert bumped.startswith("v2:") and bumped != base


@pytest.mark.parametrize("name", sorted(SAME))
def test_approach_only_changes_leave_the_digest(name: str, tmp_path: Path) -> None:
    base = _digest(tmp_path, _state(), "base")
    state = _state()
    SAME[name](state)
    assert _digest(tmp_path, state, "same") == base


def test_digest_shape_and_every_input_key_classified(tmp_path: Path) -> None:
    digest = _digest(tmp_path, _state())
    assert digest.startswith("v1:") and len(digest) == 67
    expected = {f"PlanDocument.{n}" for n in PlanDocument.model_fields} | {f"ScopeEntry.{n}" for n in ScopeEntry.model_fields}
    assert set(pf.FIELD_CLASSES) == expected
    assert set(pf.FIELD_CLASSES.values()) <= {"hashed", "derived", "excluded"}


def test_unknown_decision_hashes_as_null(tmp_path: Path) -> None:
    state = _state()
    state["plan"]["cites"] = ["dec:999"]
    assert _payload(tmp_path, state)["decisions"] == [[229, _payload(tmp_path, _state(), "x")["decisions"][1][1]], [999, None]]


def test_with_cites_equals_writing_the_cite(tmp_path: Path) -> None:
    state = _state()
    with_flag = pf.fingerprint(_payload(tmp_path, state, "a", ("dec:229",)))
    state["plan"]["cites"] = ["dec:217", "dec:229"]
    assert pf.fingerprint(_payload(tmp_path, state, "b")) == with_flag


@pytest.mark.parametrize("key", ["intent", "scope", "verification_tier", "plan_type"])
def test_missing_required_key_raises_naming_it(key: str, tmp_path: Path) -> None:
    state = _state()
    del state["plan"][key]
    with pytest.raises(pf.FingerprintError, match=key):
        _payload(tmp_path, state)


def test_non_list_scope_raises(tmp_path: Path) -> None:
    state = _state()
    state["plan"]["scope"] = "src/a.py"
    with pytest.raises(pf.FingerprintError, match="scope"):
        _payload(tmp_path, state)


def test_missing_decisions_file_raises(tmp_path: Path) -> None:
    _write_tree(tmp_path / "r", _state())
    (tmp_path / "r" / "docs" / "DECISIONS.md").unlink()
    with pytest.raises(pf.FingerprintError, match="decisions file"):
        pf.load_tree(tmp_path / "r")


def _cli(tmp_path: Path, capsys: pytest.CaptureFixture[str], plan: dict | None, *extra: str) -> tuple[int, str, str]:
    state = _state()
    _write_tree(tmp_path / "r", state)
    path = tmp_path / "PLAN-demo.yaml"
    if plan is not None:
        path.write_text(yaml.safe_dump(plan), encoding="utf-8")
    rc = pf.main(["--plan", str(path), "--root", str(tmp_path / "r"), *extra])
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_cli_prints_one_line_and_is_deterministic(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc, out, _ = _cli(tmp_path, capsys, copy.deepcopy(_PLAN))
    assert (
        rc == 0 and out.startswith("scout_input_fingerprint=v1:") and len(out.strip()) == len("scout_input_fingerprint=") + 67
    )
    assert _cli(tmp_path / "again", capsys, copy.deepcopy(_PLAN))[1] == out


def test_cli_json_prints_the_payload(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc, out, _ = _cli(tmp_path, capsys, copy.deepcopy(_PLAN), "--json")
    assert rc == 0 and set(json.loads(out)) == set(pf.PAYLOAD_KEYS)


def test_cli_reads_a_sidecar_when_present(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plain = _cli(tmp_path / "p", capsys, copy.deepcopy(_PLAN))[1]
    side = tmp_path / "s"
    _write_tree(side / "r", _state())
    (side / "r" / "docs" / "plans" / "approved").mkdir(parents=True)
    block = {"id": "b1", "file": "docs/DECISIONS.md", "before": "a", "after": "b"}
    (side / "r" / "docs" / "plans" / "approved" / "demo.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "slug": "demo", "blocks": [block]}), encoding="utf-8"
    )
    plan = side / "PLAN-demo.yaml"
    plan.write_text(yaml.safe_dump(_PLAN), encoding="utf-8")
    assert pf.main(["--plan", str(plan), "--root", str(side / "r")]) == 0
    assert capsys.readouterr().out != plain


def test_cli_with_cites_malformed_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc, out, err = _cli(tmp_path, capsys, copy.deepcopy(_PLAN), "--with-cites", "dec-1")
    assert rc == 2 and out == "" and err.startswith("FINGERPRINT-ERROR:")


def test_cli_missing_plan_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc, out, err = _cli(tmp_path, capsys, None)
    assert rc == 2 and out == "" and err.startswith("FINGERPRINT-ERROR: cannot read plan")


def test_cli_plan_without_slug_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan = {k: v for k, v in _PLAN.items() if k != "slug"}
    rc, out, err = _cli(tmp_path, capsys, plan)
    assert rc == 2 and out == "" and "slug" in err


def test_cli_non_mapping_plan_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc, out, err = _cli(tmp_path, capsys, ["a"])  # type: ignore[arg-type]
    assert rc == 2 and out == "" and "not a mapping" in err


@pytest.mark.parametrize("breaker", ["decisions", "roadmap", "sidecar"])
def test_cli_unreadable_tree_exits_2(breaker: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "r"
    _write_tree(root, _state())
    if breaker == "decisions":
        (root / "docs" / "DECISIONS.md").unlink()
    elif breaker == "roadmap":
        (root / "docs" / "ROADMAP-PLATFORM.yaml").write_text("document: [unclosed\n", encoding="utf-8")
    else:
        (root / "docs" / "plans" / "approved").mkdir(parents=True)
        (root / "docs" / "plans" / "approved" / "demo.yaml").write_text("schema_version: 1\nblocks: []\n", encoding="utf-8")
    plan = tmp_path / "PLAN-demo.yaml"
    plan.write_text(yaml.safe_dump(_PLAN), encoding="utf-8")
    rc = pf.main(["--plan", str(plan), "--root", str(root)])
    out = capsys.readouterr()
    assert rc == 2 and out.out == "" and out.err.startswith("FINGERPRINT-ERROR:")


def test_non_string_intent_raises(tmp_path: Path) -> None:
    state = _state()
    state["plan"]["intent"] = 3
    with pytest.raises(pf.FingerprintError, match="intent"):
        _payload(tmp_path, state)


def test_scope_entry_without_a_file_raises(tmp_path: Path) -> None:
    state = _state()
    state["plan"]["scope"] = [{"purpose": "no file"}]
    with pytest.raises(pf.FingerprintError, match="file"):
        _payload(tmp_path, state)


@pytest.mark.parametrize("target", ["plan", "sidecar"])
def test_cli_non_utf8_input_exits_2(target: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "r"
    _write_tree(root, _state())
    plan = tmp_path / "PLAN-demo.yaml"
    plan.write_text(yaml.safe_dump(_PLAN), encoding="utf-8")
    bad = plan if target == "plan" else root / "docs" / "plans" / "approved" / "demo.yaml"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"\xff\xfe\x00bad")
    rc = pf.main(["--plan", str(plan), "--root", str(root)])
    out = capsys.readouterr()
    assert rc == 2 and out.out == "" and out.err.startswith("FINGERPRINT-ERROR: cannot read")


@pytest.mark.parametrize("key", ["verification_tier", "plan_type"])
def test_non_string_tier_or_type_raises(key: str, tmp_path: Path) -> None:
    state = _state()
    state["plan"][key] = ["V2"]
    with pytest.raises(pf.FingerprintError, match=key):
        _payload(tmp_path, state)


def test_malformed_decisions_row_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_tree(tmp_path / "r", _state())
    monkeypatch.setattr(pf, "parse_decisions_md", lambda paths: [{"decision_id": 1}])
    with pytest.raises(pf.FingerprintError, match="usable decision_id"):
        pf.load_tree(tmp_path / "r")
