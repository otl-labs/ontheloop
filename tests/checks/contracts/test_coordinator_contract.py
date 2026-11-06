"""Content test for docs/contracts/coordinator.yaml: pointers resolve and no ids leak."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).parents[3]
_CONTRACT_PATH = _ROOT / "docs" / "contracts" / "coordinator.yaml"
_TEXT = _CONTRACT_PATH.read_text(encoding="utf-8")
_DOC = yaml.safe_load(_TEXT)
_ID_SHAPES = [
    r"cmsg_[A-Za-z0-9]{6,}",
    r"session_[A-Za-z0-9]{10,}",
    r"trig_[A-Za-z0-9]{6,}",
    r"chan_[A-Za-z0-9]{6,}",
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    r"\b\d{12}\b",
]


def _see_values(node: Any) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "see" and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_see_values(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_see_values(item))
    return found


def _dotted(doc: Any, path: str) -> bool:
    for part in path.split("."):
        if not isinstance(doc, dict) or part not in doc:
            return False
        doc = doc[part]
    return True


def test_see_pointers_resolve() -> None:
    pointers = _see_values(_DOC)
    assert pointers
    for pointer in pointers:
        file_part, _, anchor = pointer.partition("#")
        target = _ROOT / file_part
        assert target.is_file(), pointer
        if not anchor:
            continue
        if file_part.endswith(".yaml"):
            assert _dotted(yaml.safe_load(target.read_text(encoding="utf-8")), anchor), pointer
        else:
            assert anchor in target.read_text(encoding="utf-8"), pointer


def test_pointer_line_names_the_shared_copy() -> None:
    assert "coordinator/coordinator.yaml" in _DOC["surfaces"]["pointer_line"]


def test_no_ids_in_text() -> None:
    for shape in _ID_SHAPES:
        assert not re.search(shape, _TEXT), shape


def test_consumer_row_names_the_contract() -> None:
    arch = yaml.safe_load((_ROOT / "docs" / "contracts" / "instruction-architecture.yaml").read_text(encoding="utf-8"))
    consumers = next(v for v in arch.values() if isinstance(v, dict) and "repo_less_coordinator" in v)
    assert "coordinator.yaml" in consumers["repo_less_coordinator"]["notes"]


def test_structure_bars_nested_doing_agents_but_allows_gates() -> None:
    structure = _flat(_DOC["roles"]["structure"])
    assert "starts no planning or implementation agent of its own (Decision 229 cl.2)" in structure
    assert "may run the repo's verdict gates and advisor consults as subagents" in structure


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_build_merge_skips_second_word_only_for_bound_text() -> None:
    items = [_flat(item) for item in _DOC["standing_authorisation"]]
    bound = [item for item in items if "approved_text verify" in item]
    assert len(bound) == 1
    for needle in (
        "code review PROCEED + green CI",
        "no second operator word",
        "validate_merge_step",
        "has no commit after that plan merge",
        (
            "the operator's word on the final diff stays required for CODEOWNERS paths (rec-2827), hook and other "
            "security code, IAM, spend and deploys not run by the reviewed CI channel"
        ),
        "rec-4769",
        "overrides always_ask handling",
        "a loosened test or assertion",
    ):
        assert needle in bound[0], needle
    build = next(step for step in _DOC["lifecycle"]["steps"] if step["step"] == "build")
    assert _flat(build["rule"]).endswith("that standing_authorisation does not already cover.")
    acting = [_flat(item) for item in _DOC["approvals"] if "is given in that worker" in item]
    assert len(acting) == 1
    assert "except approved text a build places under standing_authorisation" in acting[0]
    handoff = _flat(_DOC["templates"]["handoff_note"])
    assert "Approved text: <sidecar path, approved digest, plan merge commit;" in handoff
    assert _DOC["contract"]["contract_version"] == 2
    entry = next(e for e in _DOC["amendment_log"] if "PLAN-coordinator-approve-once" in _flat(e["summary"]))
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(entry["date"]))
    assert entry["semantic_break"] is True
