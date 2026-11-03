"""The process_event name grammar is write-enforced from the contract through the shared row-rules engine (rec-4176).

Fast tier, no duckdb. The regex is read from the contract at test time and never restated in Python (Decision 210 cl.3);
its content is pinned by behaviour through the shared vectors.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks.registry import all_checks
from src.row_rules.rules import RowRuleError, RowRules, check_row
from src.turn_capture.observations import hook_name
from tests.fixtures.process_event_name_vectors import ACCEPT, HOOK_NAME_CASES, REJECT

_ROOT = Path(__file__).resolve().parents[2]
_CONTRACT = _ROOT / "docs" / "contracts" / "telemetry_observations.yaml"
_SHIPPED = _ROOT / "config" / "lambda" / "ducklake" / "field_semantics.yaml"
_GOLDEN = _ROOT / "tests" / "fixtures" / "turn_capture" / "golden"
_NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)
_HOOK_EVENTS = (
    "PreToolUse",
    "PostToolUse",
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "Stop",
    "StopFailure",
    "SubagentStop",
    "PreCompact",
)
_TOOLS = ("Bash", "Edit", "Write", "mcp__github-full__create_pull_request", "mcp__github__push_files", "resume", "startup")


def _contract_rule() -> list[dict[str, Any]]:
    fields = yaml.safe_load(_CONTRACT.read_text(encoding="utf-8"))["fields"]
    return fields["name"]["dq_intent"]["pattern_when"]


def _regex() -> re.Pattern[str]:
    return re.compile(_contract_rule()[0]["pattern"])


def _shipped_columns() -> dict[str, Any]:
    return yaml.safe_load(_SHIPPED.read_text(encoding="utf-8"))["ops_tables"]["telemetry_observations"]["columns"]


def _rules() -> RowRules:
    columns = _shipped_columns()
    return RowRules.from_projection({k: columns[k] for k in ("event_kind", "observation_type", "name")})


def _row(observation_type: str, name: str | None, event_kind: str = "point") -> dict[str, Any]:
    return {"event_kind": event_kind, "observation_type": observation_type, "name": name}


def test_contract_declares_the_name_grammar() -> None:
    rule = _contract_rule()
    assert len(rule) == 1
    assert rule[0]["when"] == {"observation_type": ["process_event"]}
    pattern = rule[0]["pattern"]
    assert pattern.startswith("^") and pattern.endswith("$")
    assert re.compile(pattern)


def test_shipped_projection_carries_the_rule() -> None:
    assert _shipped_columns()["name"]["pattern_when"] == _contract_rule()


def test_every_emitted_v2_name_conforms() -> None:
    regex = _regex()
    names = []
    for path in sorted(_GOLDEN.glob("*.json")):
        if path.name == "index.json":
            continue
        for row in json.loads(path.read_text(encoding="utf-8"))["expected_rows"]:
            if row.get("table") == "telemetry_observations" and row.get("observation_type") == "process_event":
                names.append(row["name"])
    assert names
    assert [n for n in names if regex.fullmatch(n) is None] == []


def test_registered_hooks_and_planned_sources_conform() -> None:
    regex = _regex()
    settings = json.loads((_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    commands = [h["command"] for groups in settings["hooks"].values() for g in groups for h in g["hooks"]]
    assert commands
    names = [hook_name({"command": c}) for c in commands]
    names += [hook_name({"hookName": f"{event}:{tool}"}) for event in _HOOK_EVENTS for tool in _TOOLS]
    names += [hook_name({"hookName": event}) for event in _HOOK_EVENTS]
    names += [hook_name({"command": c, "hookName": f"PreToolUse:{t}"}) for c in commands for t in _TOOLS]
    names += [hook_name(att) for att, _ in HOOK_NAME_CASES]
    checks = sorted(all_checks())
    assert checks
    names += [f"gate:{check}" for check in checks]
    config = yaml.safe_load((_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hook_ids = [h["id"] for repo in config["repos"] for h in repo["hooks"]]
    assert hook_ids
    names += [f"precommit:{hook_id}" for hook_id in hook_ids]
    assert [n for n in names if regex.fullmatch(n) is None] == []


def test_hook_name_vectors_hold_and_conform() -> None:
    regex = _regex()
    for att, expected in HOOK_NAME_CASES:
        assert hook_name(att) == expected and regex.fullmatch(expected), att
        assert expected in ACCEPT or regex.fullmatch(expected)


def test_rule_binds_process_event_rows_only() -> None:
    rules = _rules()
    for observation_type, name in (
        ("tool_call", "Bash"),
        ("tool_call", "mcp__github__create_pull_request"),
        ("step", "a step title with spaces"),
        ("turn", None),
    ):
        check_row("telemetry_observations", _row(observation_type, name, "open"), rules, _NOW)
    for name in ACCEPT:
        check_row("telemetry_observations", _row("process_event", name), rules, _NOW)
    for name in REJECT:
        with pytest.raises(RowRuleError) as raised:
            check_row("telemetry_observations", _row("process_event", name), rules, _NOW)
        assert (raised.value.rule, raised.value.column) == ("pattern_when", "name"), name


def test_no_python_file_restates_the_rule() -> None:
    pattern = _contract_rule()[0]["pattern"]
    this_file = Path(__file__).resolve()
    hits = [
        str(path.relative_to(_ROOT))
        for root in ("src", "scripts", "tests")
        for path in (_ROOT / root).rglob("*.py")
        if path.resolve() != this_file and pattern in path.read_text(encoding="utf-8", errors="replace")
    ]
    assert hits == []
