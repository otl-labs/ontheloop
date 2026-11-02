"""rec-4478: Decision 67 is superseded, CD.17 is disposed, and no live instruction surface still describes a freeze."""

import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from scripts.decisions_md import extract_entry_envelope, iter_decision_sections
from scripts.roadmap.plan_document import PlanDocument

ROOT = Path(__file__).resolve().parents[2]
DECISIONS = ROOT / "docs" / "DECISIONS.md"
ROADMAP = ROOT / "docs" / "ROADMAP-PLATFORM.yaml"
TITLE_PREFIX = "The legacy Python executor is retired and the STRATEGIC plan type is shelved"
CD17_AFFECTS = {"T1.5", "T1.6", "T2.1", "T3.3", "T3.4", "T4.1", "T4.2", "T4.4"}
FREEZE_PHRASES = (
    "strategic plans suspended",
    "decision 67 reversal",
    "cd.17 reversal",
    "cd.17 / t4.2",
    "(frozen, decision 67)",
    "temporary strategic/executor freeze",
    "executor frozen (decision 67)",
    "executor is frozen (decision 67)",
    "cd.17-frozen",
    "frozen pending decision 67",
    "executor unfreeze",
)
ROADMAP_BANNED = (
    "inert under the decision 67 / cd.17 freeze",
    "suspended while the cd.17",
    "strategic remains suspended",
)


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def _strings(node: object) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in _strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return []


def _roadmap() -> dict:
    return yaml.safe_load(ROADMAP.read_text(encoding="utf-8"))


def _items() -> dict[str, dict]:
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

    walk(_roadmap())
    return found


def _candidate(cd_id: str) -> dict:
    rows: list[dict] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if node.get("id") == cd_id and "title" in node:
                rows.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(_roadmap())
    assert len(rows) == 1
    return rows[0]


def _sections() -> dict[int, str]:
    return {int(m.group(1)): block for m, block in iter_decision_sections(DECISIONS.read_text(encoding="utf-8"))}


def _new_decision() -> tuple[int, str]:
    hits = [(n, b) for n, b in _sections().items() if b.splitlines()[0].split(":", 1)[-1].strip().startswith(TITLE_PREFIX)]
    assert len(hits) == 1
    return hits[0]


def _criterion_texts(item: dict) -> list[str]:
    return [c["text"] if isinstance(c, dict) else c for c in item.get("exit_criteria", [])]


def test_decision_67_superseded_and_cd17_disposed() -> None:
    number, block = _new_decision()
    envelope = extract_entry_envelope(block)
    assert envelope is not None and envelope["supersedes"] == [67]
    assert len(block.encode("utf-8")) <= 6144
    stub = _sections()[67]
    assert f"Superseded by Decision {number}" in stub and "**Status:** Superseded" in stub

    cd17 = _candidate("CD.17")
    assert cd17["state"] == "superseded" and cd17["gates"] == [] and set(cd17["affects"]) == CD17_AFFECTS
    assert "detail" not in cd17

    with pytest.raises(ValidationError):
        PlanDocument.model_validate({"plan_type": "STRATEGIC"})

    items = _items()
    for tid, parts in (
        ("T2.15", ("intent", "exit_criteria", "progress_note")),
        ("T3.19", ("intent", "progress_note")),
    ):
        assert "CD.17" not in " ".join(_strings({k: items[tid].get(k) for k in parts})), tid
    t42 = items["T4.2"]
    assert "CD.17" not in t42["intent"]
    assert not any("CD.17" in s for s in _criterion_texts(t42) if s.startswith("Stability"))
    assert any(s.startswith("Stability") for s in _criterion_texts(t42))
    t319 = items["T3.19"]
    assert "T4.2" in f"{t319['intent']} {t319.get('progress_note', '')}"

    raw = _norm(ROADMAP.read_text(encoding="utf-8"))
    for phrase in ROADMAP_BANNED:
        assert phrase not in raw, phrase
    assert "T4.2" not in items["T5.6"]["depends_on"]
    assert any("decomposed" in s for s in _criterion_texts(items["T4.31"]))
    assert any("inherit" in s.casefold() for s in _criterion_texts(items["T4.4"]))


def _contract_text(path: Path) -> str:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data.pop("amendment_log", None)
    return " ".join(_strings(data))


def test_live_instruction_surfaces_carry_no_freeze() -> None:
    surfaces: list[tuple[Path, str]] = []
    for name in ("AGENTS.md", "README.md", "docs/PROJECT_CONTEXT.md"):
        surfaces.append((ROOT / name, (ROOT / name).read_text(encoding="utf-8")))
    for pattern in (".claude/skills/**/*.md", ".claude/commands/*.md"):
        surfaces += [(p, p.read_text(encoding="utf-8")) for p in sorted(ROOT.glob(pattern))]
    surfaces += [(p, _contract_text(p)) for p in sorted((ROOT / "docs/contracts").glob("*.yaml"))]
    skip = ROOT / "config/agent/data_quality/decisions"
    for pattern in ("config/agent/**/*.yaml", "config/agent/**/*.md"):
        surfaces += [
            (p, p.read_text(encoding="utf-8")) for p in sorted(ROOT.glob(pattern)) if skip not in p.parents and p.is_file()
        ]
    offenders = [(str(p.relative_to(ROOT)), ph) for p, text in surfaces for ph in FREEZE_PHRASES if ph in _norm(text)]
    assert not offenders, offenders
    assert not (ROOT / ".claude/commands/develop-executor.md").exists()
