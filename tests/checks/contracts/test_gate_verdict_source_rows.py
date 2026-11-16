"""rec-5121 acceptance (T4.32 c5): the gates.<persona>.verdict_source rows, read by the merge gate's own parser, and the
placed contract readings. Phrase pins are growth-safe: each asserts a placed phrase is present, never a count."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from scripts.session import merge_checks as mc

_REPO = Path(__file__).resolve().parents[3]
_CONFIG = "docs/contracts/project-configuration.yaml"


def _text(rel: str) -> str:
    return (_REPO / rel).read_text(encoding="utf-8")


def _flat(rel: str) -> str:
    return re.sub(r"\s+", " ", _text(rel))


def _real_sources() -> dict[str, str]:
    sources = mc.verdict_sources(_text(_CONFIG))
    assert isinstance(sources, dict), sources
    return sources


def _with(persona: str, value: str) -> str:
    data = yaml.safe_load(_text(_CONFIG))
    for row in data["settings"]:
        if row["id"] == f"gates.{persona}.verdict_source":
            row["defaults"] = {"local": value, "cloud": value}
    return yaml.safe_dump(data)


def test_rows_read_in_session() -> None:
    assert _real_sources() == dict.fromkeys(mc.PERSONA_CLASSES, "in_session")


def test_one_field_flip_requires_the_persona_check() -> None:
    flipped = mc.verdict_sources(_with("code_reviewer", "actions"))
    assert isinstance(flipped, dict)
    assert mc.persona_checks(flipped, "code") == [mc.persona_check_name("code_reviewer")]
    assert mc.persona_checks(flipped, "plan") == []
    assert mc.persona_checks(_real_sources(), "code") == [] == mc.persona_checks(_real_sources(), "plan")


def test_switch_back_names_the_persona() -> None:
    flipped = mc.verdict_sources(_with("code_reviewer", "actions"))
    assert isinstance(flipped, dict)
    assert mc.switch_backs(flipped, _real_sources()) == ["code_reviewer"]
    assert mc.switch_backs(_real_sources(), flipped) == []
    assert mc.switch_backs(_real_sources(), _real_sources()) == []


def test_persona_classes_match_the_registry_and_the_rows() -> None:
    registry = yaml.safe_load(_text("docs/contracts/executor-personas.yaml"))["personas"]
    verdict = {p["persona"] for p in registry if p["kind"] == "verdict"}
    assert set(mc.PERSONA_CLASSES) == verdict
    assert set(_real_sources()) == verdict


def test_a_two_actions_copy_names_each_class_only() -> None:
    data = yaml.safe_load(_text(_CONFIG))
    for row in data["settings"]:
        if row["id"] == "gates.plan_critic.verdict_source":
            row["defaults"] = {"local": "actions", "cloud": "actions"}
    sources = mc.verdict_sources(yaml.safe_dump(data))
    assert isinstance(sources, dict)
    assert mc.persona_checks(sources, "plan") == [mc.persona_check_name("plan_critic")]
    assert mc.persona_checks(sources, "code") == []


def test_coordinator_readings_are_placed() -> None:
    text = _flat("docs/contracts/coordinator.yaml")
    for phrase in (
        "verdict_source: >- Where a verdict persona's verdict comes from",
        "a verdict gate whose verdict_source is actions is read from its check run, never run as a subagent",
        "each round is the plan-critique check run on the pushed head of the ready plan PR",
        "open it as a draft before that persona's step",
        "gets the code review verdict from the lane its verdict_source selects",
        "Merge a plan PR on critique PROCEED (lifecycle.verdict_source) + green CI.",
        "Merge a build PR on code review PROCEED (lifecycle.verdict_source) + green CI",
    ):
        assert phrase in text, phrase


def test_lifecycle_and_alignment_readings_are_placed() -> None:
    lifecycle = _flat("docs/contracts/tier-item-lifecycle.yaml")
    assert "Get the code-review verdict from the lane gates.code_reviewer.verdict_source selects" in lifecycle
    assert "the bookkeeping commit is a new head, so the merge waits for its own check run" in lifecycle
    assert "which lane gives each persona's verdict is its gates.<persona>.verdict_source row" in _flat(
        "docs/contracts/project-alignment.yaml"
    )


def test_ports_records_the_persona_check_coupling() -> None:
    ports = yaml.safe_load(_text("docs/contracts/ports.yaml"))
    row = next(c for c in ports["coupling_findings"] if c["id"] == "PC-10")
    assert row["status"] == "open" and row["owning_item"] == "T4.2:c9"
    assert "persona-verdict/<persona>" in row["description"]


def test_roadmap_carries_the_code_pr_amendment() -> None:
    assert "a PR changing code is every pull request whose diff from its merge base is not confined to docs/plans/" in _flat(
        "docs/ROADMAP-PLATFORM.yaml"
    )
