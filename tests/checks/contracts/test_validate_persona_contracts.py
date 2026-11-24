"""validate_persona_contracts: real tree green, fixture tree green, one red test per rule (T4.10a c2-c8)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

import pytest

from scripts.checks import registry
from scripts.checks.contracts._manifest import ENTRIES
from scripts.checks.contracts.validate_persona_contracts import validate_persona_contracts
from tests.persona_contracts.fixture_tree import (
    CONTRACT_REL,
    EXPECTED,
    MUTATORS,
    RENDERED_REL,
    build_tree,
    edit_contract,
)

_NAMES = ("validate_persona_contracts", "validate_persona_contract_binding")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return build_tree(tmp_path)


def _run(root: Path | None = None) -> list[str]:
    failed: list[str] = []
    validate_persona_contracts(failed, root)
    return failed


def test_real_tree_green_and_examined() -> None:
    registry.pop_declaration()
    assert _run() == []
    declaration = registry.pop_declaration()
    assert declaration is not None and declaration.kind == "examined" and declaration.count >= 5


def test_fixture_tree_green(tree: Path) -> None:
    assert _run(tree) == []


def test_mutators_and_expectations_cover_the_same_rules() -> None:
    assert set(MUTATORS) == set(EXPECTED)


@pytest.mark.parametrize("rule", sorted(MUTATORS))
def test_red_rule(rule: str, tree: Path) -> None:
    MUTATORS[rule](tree)
    failures = _run(tree)
    assert any(EXPECTED[rule] in f for f in failures), failures


@pytest.mark.parametrize("rule", sorted(MUTATORS))
def test_red_rule_on_fixture_base(rule: str, tmp_path: Path) -> None:
    root = build_tree(tmp_path, kind="fixture")
    MUTATORS[rule](root)
    failures = _run(root)
    assert any(EXPECTED[rule] in f for f in failures), failures


def test_fixture_contract_passes_every_verdict_rule(tmp_path: Path) -> None:
    assert _run(build_tree(tmp_path, kind="fixture")) == []


def test_both_checks_registered_in_both_sequences() -> None:
    entries = {e.name: e for e in ENTRIES}
    for name in _NAMES:
        entry = entries[name]
        assert entry.pre is True and entry.full_segment is not None
        assert entry.pre_globs is None
        assert name in {getattr(step, "name", step) for step in registry.pre_sequence()}
        assert name in {getattr(step, "name", step) for step in registry.full_sequence()}


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def test_rendered_prompt_change_requires_version_raise(tree: Path) -> None:
    _git(tree, "init", "-q")
    _git(tree, "config", "user.email", "t@example.com")
    _git(tree, "config", "user.name", "t")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-q", "-m", "base")
    _git(tree, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert _run(tree) == []
    edit_contract(tree, lambda c: c.__setitem__("prompt_template", c["prompt_template"] + "More text.\n"), rerender=True)
    assert any("without raising contract_version" in f for f in _run(tree))
    edit_contract(tree, lambda c: c.__setitem__("contract_version", 2), rerender=True)
    assert _run(tree) == []
    assert (tree / CONTRACT_REL).is_file() and (tree / RENDERED_REL).is_file()


def test_unreadable_index_fails_and_skips(tmp_path: Path) -> None:
    registry.pop_declaration()
    failed: list[str] = []
    validate_persona_contracts(failed, tmp_path)
    assert any("unreadable or off its schema" in f for f in failed)
    declaration = registry.pop_declaration()
    assert declaration is not None and declaration.kind == "skipped"


def test_missing_source_file_is_a_failure_not_a_crash(tree: Path) -> None:
    (tree / "docs/ROADMAP-PLATFORM.yaml").unlink()
    assert any("missing or unreadable" in f for f in _run(tree))


def test_prose_containing_a_substring_is_not_a_money_budget(tree: Path) -> None:
    edit_contract(tree, lambda c: c["budget"]["provisional"].__setitem__("measure", "neuron turn counts"), rerender=True)
    assert _run(tree) == []


def test_compute_plane_contract_needs_no_skill_tether(tree: Path) -> None:
    from tests.persona_contracts.fixture_tree import INDEX_REL, edit_text

    edit_text(tree, INDEX_REL, "    kind: verdict\n    contract:", "    kind: compute_plane\n    contract:")
    edit_contract(
        tree,
        lambda c: (c.pop("skill_tether"), c.pop("verdict_consistency"), c.__setitem__("kind", "compute_plane")),
        rerender=True,
    )
    assert _run(tree) == []


def test_compute_plane_contract_with_verdict_rule_is_refused(tree: Path) -> None:
    from tests.persona_contracts.fixture_tree import INDEX_REL, edit_text

    edit_text(tree, INDEX_REL, "    kind: verdict\n    contract:", "    kind: compute_plane\n    contract:")
    edit_contract(tree, lambda c: (c.pop("skill_tether"), c.__setitem__("kind", "compute_plane")), rerender=True)
    assert any("verdict_consistency on a compute_plane contract" in f for f in _run(tree))


def test_row_without_registry_key_is_refused(tree: Path) -> None:
    from tests.persona_contracts.fixture_tree import INDEX_REL, edit_text

    edit_text(tree, INDEX_REL, "    registry_key: fixture-verdict\n", "")
    assert any("is unreadable or off its schema" in f for f in _run(tree))


def test_registry_is_not_read_when_the_index_has_no_rows(tree: Path) -> None:
    from tests.persona_contracts.fixture_tree import INDEX_REL, REGISTRY_REL

    index = tree / INDEX_REL
    head, sep, _ = index.read_text(encoding="utf-8").rpartition("\npersonas:")
    assert sep
    index.write_text(head + "\npersonas: []\n", encoding="utf-8")
    (tree / REGISTRY_REL).unlink()
    assert not any("source_registry" in f or "registry_key" in f for f in _run(tree))


def test_array_only_rule_is_accepted(tree: Path) -> None:
    rule = {"cases": [{"verdict": "REVISE", "any": [{"array": "findings"}]}], "otherwise": "PROCEED"}
    edit_contract(tree, lambda c: c.__setitem__("verdict_consistency", rule), rerender=True)
    assert _run(tree) == []


def test_verdict_contract_without_rule_is_refused(tree: Path) -> None:
    edit_contract(tree, lambda c: c.pop("verdict_consistency"), rerender=True)
    assert any("missing required field(s)" in f for f in _run(tree))


def _declare_slot(c: dict[str, Any], slot: str = "project_guidance") -> None:
    c["input_schema"]["properties"][slot] = {"type": "string", "description": "Project guidance."}
    c["input_schema"]["required"].append(slot)
    c["capabilities"].append(f"read_input:{slot}")
    c["guidance_slots"] = [slot]


def _slot_mutation(name: str) -> Callable[[dict[str, Any]], None]:
    def change(c: dict[str, Any]) -> None:
        _declare_slot(c)
        props = c["input_schema"]["properties"]
        if name == "not_a_list":
            c["guidance_slots"] = "project_guidance"
        elif name == "duplicated":
            c["guidance_slots"] = ["project_guidance", "project_guidance"]
        elif name == "bad_name":
            c["guidance_slots"] = ["Bad/Name"]
        elif name == "head_filled":
            c["guidance_slots"] = ["plan"]
        elif name == "absent_property":
            del props["project_guidance"]
        elif name == "wrong_type":
            props["project_guidance"] = {"type": "integer"}
        elif name == "extra_keyword":
            props["project_guidance"] = {"type": "string", "minLength": 1}
        elif name == "description_not_string":
            props["project_guidance"] = {"type": "string", "description": 5}
        elif name == "not_required":
            c["input_schema"]["required"].remove("project_guidance")
        elif name == "no_capability":
            c["capabilities"].remove("read_input:project_guidance")
        elif name == "properties_not_mapping":
            c["input_schema"]["properties"] = ["x"]
        elif name == "required_not_list":
            c["input_schema"]["required"] = "change_id head_sha project_guidance"

    return change


_SLOT_REFUSALS = (
    "duplicated",
    "bad_name",
    "head_filled",
    "absent_property",
    "wrong_type",
    "extra_keyword",
    "description_not_string",
    "not_required",
    "no_capability",
    "properties_not_mapping",
    "required_not_list",
)


@pytest.mark.parametrize("name", _SLOT_REFUSALS)
def test_malformed_guidance_slot_is_refused(tree: Path, name: str) -> None:
    edit_contract(tree, _slot_mutation(name), rerender=False)
    assert any("guidance_slots" in f for f in _run(tree)), name


def test_well_formed_and_empty_guidance_slots_pass(tree: Path) -> None:
    edit_contract(tree, _declare_slot, rerender=True)
    assert _run(tree) == []
    edit_contract(tree, lambda c: c.__setitem__("guidance_slots", []), rerender=True)
    assert _run(tree) == []
    edit_contract(tree, lambda c: c.__setitem__("input_schema", {**c["input_schema"], "properties": ["x"]}))
    assert not any("guidance_slots" in f for f in _run(tree))


def test_guidance_home_outside_boundary_patterns_is_refused(tree: Path) -> None:
    caps = tree / "config/agent/executor/capabilities.yaml"
    caps.write_text(caps.read_text(encoding="utf-8").replace("  - ontheloop/guidance/\n", ""), encoding="utf-8")
    assert any("ontheloop/guidance/" in f and "leash" in f for f in _run(tree))


def test_non_list_guidance_slots_is_off_schema(tree: Path) -> None:
    edit_contract(tree, _slot_mutation("not_a_list"), rerender=False)
    assert any("off its schema" in f for f in _run(tree))
