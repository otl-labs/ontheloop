"""docs/contracts/approval-routing.yaml: rec-5137's acceptance and the map's integrity (PLAN-wording-approval-routing)."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
MAP_REL = "docs/contracts/approval-routing.yaml"
_MAP = yaml.safe_load((ROOT / MAP_REL).read_text(encoding="utf-8"))
_GATED = _MAP["gated"]


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _row_file(row: object) -> str | None:
    if isinstance(row, str):
        return row
    return row["file"] if isinstance(row, dict) and isinstance(row.get("file"), str) else None


def test_kind_routes_wording_approvals() -> None:
    semantics = yaml.safe_load(_text("docs/contracts/decision-kind.yaml"))["semantics"]
    assert "approval-routing.yaml" in semantics
    assert "kind grants and gates nothing" not in semantics
    lane = _MAP["lane"]
    assert lane["rung"] == "sampled" and lane["rung"] in lane["rungs"]
    numbers = [
        int(m.group(1))
        for m in re.finditer(
            r"^## Decision (\d+): Wording approval follows a declared gated set", _text("docs/DECISIONS.md"), re.M
        )
    ]
    assert numbers == [lane["kind_floor"]]


def test_gated_files_are_exact_existing_paths_or_basenames_at_any_depth() -> None:
    for entry in _GATED["files"]:
        assert (ROOT / entry).is_file() or (entry.startswith("**/") and "/" not in entry[3:]), entry


def test_yaml_keys_files_parse_with_each_listed_key() -> None:
    for rel, keys in _GATED["yaml_keys"].items():
        data = yaml.safe_load(_text(rel))
        assert isinstance(data, dict) and keys, rel
        assert [k for k in keys if k not in data] == [], rel


def test_md_sections_name_one_heading_or_list_item_each() -> None:
    for rel, entries in _GATED["md_sections"].items():
        lines = _text(rel).splitlines()
        for entry in entries:
            hits = [ln for ln in lines if (ln.startswith(entry) if entry.endswith(".") else ln.rstrip() == entry)]
            assert len(hits) == 1, (rel, entry, len(hits))


def test_demotions_are_lists_of_paths_or_file_mappings() -> None:
    for key in ("rows", "restorations"):
        rows = _MAP["demotions"][key]
        assert isinstance(rows, list)
        assert all(_row_file(row) for row in rows), key


def test_the_coordinator_and_the_planning_skill_name_the_map() -> None:
    always_ask = yaml.safe_load(_text("docs/contracts/coordinator.yaml"))["always_ask"]
    assert "approval-routing.yaml" in yaml.safe_dump(always_ask)
    skill = _text(".claude/skills/planning/SKILL.md")
    start = skill.index("## Plan Lint and Wording Check (Workflow Step 8b")
    nxt = re.search(r"^## ", skill[start + 3 :], re.M)
    section = skill[start : start + 3 + nxt.start()] if nxt else skill[start:]
    assert "approval-routing.yaml" in section


def test_the_notice_surfaces_are_directories_of_this_repository() -> None:
    for surface in _MAP["notice"]["surfaces"]:
        assert (ROOT / PurePosixPath(surface)).is_dir(), surface
    assert all(s.startswith(".") for s in _MAP["notice"]["wording_suffixes"])


@pytest.mark.parametrize("rel", sorted(_GATED["yaml_keys"]))
def test_each_gated_yaml_file_is_a_governed_path(rel: str) -> None:
    assert (ROOT / rel).is_file()
