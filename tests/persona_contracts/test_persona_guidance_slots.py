"""Persona guidance slots and the provider seam (Decision 280, rec-5569)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from scripts.checks.contracts.validate_persona_contracts import validate_persona_contracts
from scripts.persona_contracts.render import render, sha256_hex
from scripts.persona_contracts.schema import PersonaContract, load_index
from scripts.personas import guidance
from tests.persona_contracts import fixture_tree as ft

SLOT = "project_guidance"


def _declare(c: dict[str, Any], slot: str = SLOT) -> None:
    c["input_schema"]["properties"][slot] = {"type": "string", "description": "Project guidance."}
    c["input_schema"]["required"].append(slot)
    c["capabilities"].append(f"read_input:{slot}")
    c["guidance_slots"] = [slot]


def _failures(root: Path) -> list[str]:
    failed: list[str] = []
    validate_persona_contracts(failed, root)
    return failed


def _slot_tree(tmp_path: Path) -> Path:
    root = ft.build_tree(tmp_path)
    ft.edit_contract(root, _declare, rerender=True)
    return root


@pytest.fixture(autouse=True)
def _reset_registry() -> Any:
    saved = dict(guidance._REGISTRY)
    guidance._REGISTRY.clear()
    yield
    guidance._REGISTRY.clear()
    guidance._REGISTRY.update(saved)


def test_contract_schema_declares_generic_guidance_slot(tmp_path: Path) -> None:
    index = load_index(ft.REAL)
    raw = yaml.safe_load((ft.REAL / ft.INDEX_REL).read_text(encoding="utf-8"))
    assert "guidance_slots" in raw["persona_contract"]["fields"]
    assert index.guidance_slot.home == guidance.HOME
    assert index.guidance_slot.provider_seam == "scripts/personas/guidance.py"
    contract = ft._contract_dict()
    _declare(contract)
    assert PersonaContract.model_validate(contract).guidance_slots == [SLOT]
    assert _failures(_slot_tree(tmp_path)) == []


def _drop_property(c: dict[str, Any]) -> None:
    _declare(c)
    del c["input_schema"]["properties"][SLOT]


def _prop(schema: dict[str, Any]) -> Callable[[dict[str, Any]], None]:
    def change(c: dict[str, Any]) -> None:
        _declare(c)
        c["input_schema"]["properties"][SLOT] = schema

    return change


def _rename(name: str) -> Callable[[dict[str, Any]], None]:
    def change(c: dict[str, Any]) -> None:
        c["input_schema"]["properties"].setdefault(name, {"type": "string"})
        if name not in c["input_schema"]["required"]:
            c["input_schema"]["required"].append(name)
        c["capabilities"].append(f"read_input:{name}")
        c["guidance_slots"] = [name]

    return change


def _not_required(c: dict[str, Any]) -> None:
    _declare(c)
    c["input_schema"]["required"].remove(SLOT)


def _duplicated(c: dict[str, Any]) -> None:
    _declare(c)
    c["guidance_slots"] = [SLOT, SLOT]


def _no_capability(c: dict[str, Any]) -> None:
    _declare(c)
    c["capabilities"].remove(f"read_input:{SLOT}")


def _bad_properties(c: dict[str, Any]) -> None:
    _declare(c)
    c["input_schema"]["properties"] = ["not", "a", "mapping"]


def _unhashable(c: dict[str, Any]) -> None:
    _declare(c)
    c["guidance_slots"] = [[SLOT]]


def _string_required(c: dict[str, Any]) -> None:
    _declare(c)
    c["input_schema"]["required"] = "change_id head_sha project_guidance"


MALFORMED: dict[str, Callable[[dict[str, Any]], None]] = {
    "absent": _drop_property,
    "integer": _prop({"type": "integer"}),
    "minLength_0": _prop({"type": "string", "minLength": 0}),
    "minLength_1": _prop({"type": "string", "minLength": 1}),
    "maxLength_0": _prop({"type": "string", "maxLength": 0}),
    "const_empty": _prop({"type": "string", "const": ""}),
    "enum": _prop({"type": "string", "enum": ["a"]}),
    "pattern": _prop({"type": "string", "pattern": "^a$"}),
    "not_required": _not_required,
    "change_id": _rename("change_id"),
    "head_sha": _rename("head_sha"),
    "plan": _rename("plan"),
    "duplicated": _duplicated,
    "slash": _rename("a/b"),
    "no_capability": _no_capability,
    "properties_not_mapping": _bad_properties,
    "unhashable_entry": _unhashable,
    "string_required": _string_required,
}


@pytest.mark.parametrize("case", sorted(MALFORMED))
def test_malformed_slot_is_refused(tmp_path: Path, case: str) -> None:
    root = ft.build_tree(tmp_path)
    ft.edit_contract(root, MALFORMED[case], rerender=False)
    failures = [f for f in _failures(root) if "guidance_slots" in f or (case == "unhashable_entry" and "off its schema" in f)]
    assert failures, case
    name = {"change_id": "change_id", "head_sha": "head_sha", "plan": "plan", "slash": "a/b"}.get(case, SLOT)
    if case not in ("properties_not_mapping", "unhashable_entry", "string_required"):
        assert any(name in f for f in failures), failures


def test_empty_guidance_slots_passes(tmp_path: Path) -> None:
    root = ft.build_tree(tmp_path)
    ft.edit_contract(root, lambda c: c.__setitem__("guidance_slots", []), rerender=True)
    assert _failures(root) == []


def test_guidance_home_leaves_boundary_is_refused(tmp_path: Path) -> None:
    root = _slot_tree(tmp_path)
    assert not [f for f in _failures(root) if "leash" in f]
    ft.edit_text(root, "config/agent/executor/capabilities.yaml", "  - ontheloop/guidance/\n", "")
    assert any("ontheloop/guidance/" in f and "leash" in f for f in _failures(root))
    ft.edit_text(root, ft.INDEX_REL, 'home: "ontheloop/guidance/<persona>', 'home: "elsewhere/guidance/<persona>')
    failures = _failures(root)
    assert any("elsewhere/guidance/" in f and "leash" in f for f in failures)
    assert not any("leash path ontheloop/guidance/" in f for f in failures)


def test_slot_placeholder_in_template_is_refused(tmp_path: Path) -> None:
    root = _slot_tree(tmp_path)

    def change(c: dict[str, Any]) -> None:
        c["prompt_template"] += f"\nGuidance: ${SLOT}\n"

    ft.edit_contract(root, change, rerender=False)
    assert any("does not render" in f for f in _failures(root))


def test_fixture_guidance_reaches_input_without_contract_change(tmp_path: Path) -> None:
    root = _slot_tree(tmp_path)
    base = tmp_path / "base"
    path = base / "ontheloop/guidance/fixture_verdict" / f"{SLOT}.md"
    path.parent.mkdir(parents=True)
    contract = PersonaContract.model_validate(ft.read_contract(root))
    index = load_index(root)
    before = render(contract, index)
    for text in ("first guidance", "second guidance"):
        path.write_text(text, encoding="utf-8")
        assert guidance.fill(base, "fixture_verdict", [SLOT]) == {SLOT: text}
        after = render(contract, index)
        assert after == before
        assert sha256_hex(after) == contract.rendered_prompt_sha256


def test_absent_guidance_file_is_empty(tmp_path: Path) -> None:
    assert guidance.file_default(tmp_path, "fixture_verdict", SLOT) == ""
    assert guidance.fill(tmp_path, "fixture_verdict", [SLOT]) == {SLOT: ""}


def test_registered_provider_overrides_file_default(tmp_path: Path) -> None:
    seen: list[tuple[Path, str, str]] = []

    def provider(base: Path, persona: str, slot: str) -> str:
        seen.append((base, persona, slot))
        return "from provider"

    guidance.register(SLOT, provider)
    assert guidance.fill(tmp_path, "p", [SLOT, "other"]) == {SLOT: "from provider", "other": ""}
    assert seen == [(tmp_path, "p", SLOT)]
    with pytest.raises(ValueError):
        guidance.register(SLOT, provider)


def test_non_utf8_guidance_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "ontheloop/guidance/p" / f"{SLOT}.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(ValueError, match="UTF-8"):
        guidance.file_default(tmp_path, "p", SLOT)


def test_symlinked_or_off_pattern_guidance_is_refused(tmp_path: Path) -> None:
    folder = tmp_path / "ontheloop/guidance/p"
    folder.mkdir(parents=True)
    target = tmp_path / "secret.md"
    target.write_text("x", encoding="utf-8")
    (folder / f"{SLOT}.md").symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        guidance.file_default(tmp_path, "p", SLOT)
    (folder / "dir_slot.md").mkdir()
    with pytest.raises(ValueError, match="regular file"):
        guidance.file_default(tmp_path, "p", "dir_slot")
    for persona, slot in (("a/b", SLOT), ("p", "a/b"), ("p.q", SLOT), ("p", "a.b"), ("..", SLOT), ("P", SLOT)):
        with pytest.raises(ValueError):
            guidance.file_default(tmp_path, persona, slot)
    with pytest.raises(ValueError, match="not a directory"):
        guidance.file_default(tmp_path / "missing", "p", SLOT)


def test_symlinked_parent_escaping_base_root_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    (outside / "p").mkdir(parents=True)
    (outside / "p" / f"{SLOT}.md").write_text("x", encoding="utf-8")
    base = tmp_path / "base"
    (base / "ontheloop").mkdir(parents=True)
    (base / "ontheloop/guidance").symlink_to(outside)
    with pytest.raises(ValueError, match="outside"):
        guidance.file_default(base, "p", SLOT)


def test_fill_reads_only_the_given_base_root(tmp_path: Path) -> None:
    head, base = tmp_path / "head", tmp_path / "base"
    (head / "ontheloop/guidance/p").mkdir(parents=True)
    (head / "ontheloop/guidance/p" / f"{SLOT}.md").write_text("head text", encoding="utf-8")
    base.mkdir()
    assert guidance.fill(base, "p", [SLOT]) == {SLOT: ""}
    assert guidance.fill(head, "p", [SLOT]) == {SLOT: "head text"}


def test_instruction_architecture_records_persona_layers() -> None:
    data = yaml.safe_load((ft.REAL / "docs/contracts/instruction-architecture.yaml").read_text(encoding="utf-8"))
    layers = {item["layer"]: item for item in data["layers"]}
    assert layers[6]["content_locations"] == ["docs/contracts/personas/*.yaml"]
    assert 7 in layers
    assert data["consumers"]["run_persona"]["consumes_layers"] == [6, 7]
    assert copy.deepcopy(layers[7]["consumers"]) == ["run_persona"]


def test_case_provider_reads_the_named_case_at_base_root(tmp_path: Path) -> None:
    import os

    case_dir = tmp_path / "tests/fixtures/persona_verdict/guidance_cases/p_one"
    case_dir.mkdir(parents=True)
    (case_dir / "c_one.md").write_text("CASE", encoding="utf-8")
    assert guidance.fill(tmp_path, "p_one", [guidance.FIXTURE_CASE_SLOT]) == {guidance.FIXTURE_CASE_SLOT: ""}
    assert guidance.fill(tmp_path, "p_one", [guidance.FIXTURE_CASE_SLOT], "c_one") == {guidance.FIXTURE_CASE_SLOT: "CASE"}
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError):
        guidance.fill(other, "p_one", [guidance.FIXTURE_CASE_SLOT], "c_one")
    with pytest.raises(ValueError):
        guidance.case_file(tmp_path, "p_one", "s", "Bad-Case")
    os.symlink(case_dir / "c_one.md", case_dir / "c_link.md")
    with pytest.raises(ValueError):
        guidance.case_file(tmp_path, "p_one", "s", "c_link")
    with pytest.raises(ValueError):
        guidance.register("fixture_guidance", lambda *a: "")
    guidance.register("plain", lambda *a: "")
    with pytest.raises(ValueError):
        guidance.register_case("plain", lambda *a: "")
    assert guidance.has_case_provider(guidance.FIXTURE_CASE_SLOT)
