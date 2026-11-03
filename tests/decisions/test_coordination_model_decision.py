"""Pins the coordination-model Decision, its Decision 229 annotation and every Coverage owner's citation."""

import re
from pathlib import Path

import yaml

from scripts.decisions_md import extract_entry_envelope, iter_decision_sections, parse_decisions_md

ROOT = Path(__file__).resolve().parents[2]
DECISIONS = ROOT / "docs" / "DECISIONS.md"
ROADMAP = ROOT / "docs" / "ROADMAP-PLATFORM.yaml"
TITLE_PREFIX = "The coordination model -- a deterministic engine runs the work graph"
LIVE_ITEMS = ("T4.1", "T4.3", "T4.4", "T4.30")


def _strings(node: object) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in _strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return []


def _items() -> dict[str, dict]:
    data = yaml.safe_load(ROADMAP.read_text(encoding="utf-8"))
    found: dict[str, dict] = {}

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("id"), str) and re.fullmatch(r"T\d+\.\w+", node["id"]) and "tier" in node:
                found[node["id"]] = node
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return found


def _decision() -> tuple[int, str]:
    rows = [r for r in parse_decisions_md([DECISIONS]) if str(r.get("title", "")).startswith(TITLE_PREFIX)]
    assert len(rows) == 1
    number = int(rows[0]["decision_id"].split("-")[-1]) if isinstance(rows[0]["decision_id"], str) else rows[0]["decision_id"]
    content = DECISIONS.read_text(encoding="utf-8")
    block = next(b for m, b in iter_decision_sections(content) if int(m.group(1)) == number)
    return number, block


def test_coordination_model_decision_recorded() -> None:
    number, block = _decision()
    envelope = extract_entry_envelope(block)
    assert envelope is not None
    assert envelope["amends"] == [229]
    assert envelope["status"] == "Decided"

    content = DECISIONS.read_text(encoding="utf-8")
    d229 = next(b for m, b in iter_decision_sections(content) if int(m.group(1)) == 229)
    assert f"Amended by Decision {number}" in d229

    coverage = next(line for line in block.splitlines() if line.startswith("**Coverage"))
    owners = sorted(set(re.findall(r"T\d+\.\w+", coverage)))
    assert owners
    items = _items()
    for owner in owners:
        assert owner in items, owner
        assert any(f"Decision {number}" in s for s in _strings(items[owner])), owner

    assert items["T4.37"]["status"] == "deferred_post_mvp"
    for item_id, item in items.items():
        if item["status"] != "deferred_post_mvp":
            assert "T4.37" not in (item.get("depends_on") or []), item_id


def test_live_items_gain_no_open_criteria() -> None:
    number, _ = _decision()
    items = _items()
    for item_id in LIVE_ITEMS:
        item = items[item_id]
        assert f"Decision {number}" in "\n".join(_strings(item.get("progress_note", "")))
        assert f"Decision {number}" not in "\n".join(_strings(item.get("exit_criteria", [])))
