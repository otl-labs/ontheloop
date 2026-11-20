"""The friction rules guard (PLAN-friction-signature-coverage; pilot c2's method file path), fast tier, no duckdb.

The guard admits the status: prefix that pilot c2's text excludes, so a passing run does not meet c2 (the phase keeps it open).
rule_violations returns each violation as (kind, rule_id); the three unregistered-signature kinds are checked only when
registries are given, and live_registries() builds them from the working tree.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

import yaml

from scripts.checks.registry import full_sequence, pre_sequence
from src.common.ducklake_event_read_verbs import FRICTION_RULES

_ROOT = Path(__file__).resolve().parents[2]
_REGISTERED_SOURCES = ("hook", "gate", "precommit")
_RESERVED_RULE_ID = "run"
_HOOK_PATH = re.compile(r"\.claude/hooks/(\w+)\.(?:py|sh|bash)")

Rule = tuple[Any, ...]
Registries = Mapping[str, set[str]]


def live_registries() -> dict[str, set[str]]:
    hooks = json.loads((_ROOT / ".claude" / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    commands = [h["command"] for groups in hooks.values() for group in groups for h in group["hooks"]]
    config = yaml.safe_load((_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    return {
        "hook": {stem for command in commands for stem in _HOOK_PATH.findall(command)},
        "gate": {step.name for step in (*pre_sequence(), *full_sequence())},
        "precommit": {hook["id"] for repo in config["repos"] for hook in repo["hooks"]},
    }


def _declared_prefix_sources() -> set[str]:
    versions = yaml.safe_load((_ROOT / "config" / "telemetry" / "parser_versions.yaml").read_text(encoding="utf-8"))
    return {
        source
        for producer in versions["producers"].values()
        for entry in producer["history"]
        for source in entry.get("signature_prefixes", [])
    }


def rule_violations(rules: tuple[Rule, ...] | list[Rule], registries: Registries | None = None) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    declared = _declared_prefix_sources()
    ids: defaultdict[str, int] = defaultdict(int)
    forms: defaultdict[tuple[Any, ...], int] = defaultdict(int)
    classes: defaultdict[str, set[str]] = defaultdict(set)
    generic: set[tuple[str, str]] = set()
    kinds: set[tuple[str, str]] = set()
    for rule_id, observation_type, signature, prefix, disposition, label, klass in rules:
        ids[rule_id] += 1
        if ids[rule_id] > 1 or rule_id == _RESERVED_RULE_ID:
            out.append(("duplicate_or_reserved_rule_id", rule_id))
        forms[(observation_type, signature, prefix, disposition)] += 1
        if forms[(observation_type, signature, prefix, disposition)] == 2:
            out.append(("tie", rule_id))
        classes[label].add(klass)
        if len(classes[label]) == 2:
            out.append(("label_with_two_classes", rule_id))
        kinds.add((observation_type, disposition))
        if signature is None and prefix is None:
            generic.add((observation_type, disposition))
        if signature is not None:
            source = signature.split(":", 1)[0]
            if observation_type != "process_event" or source not in _REGISTERED_SOURCES:
                out.append(("exact_signature_on_unregistered_source", rule_id))
            elif registries is not None and signature.split(":", 1)[1] not in registries.get(source, set()):
                out.append((f"unregistered_{source}_signature", rule_id))
        if prefix is not None:
            if observation_type != "process_event":
                out.append(("exact_signature_on_unregistered_source", rule_id))
            elif prefix.rstrip(":") in _REGISTERED_SOURCES:
                out.append(("prefix_on_registered_source", rule_id))
            elif prefix != "status:" or prefix.rstrip(":") not in declared:
                out.append(("undeclared_prefix", rule_id))
            if "%" in prefix or "_" in prefix or "*" in prefix:
                out.append(("undeclared_prefix", rule_id))
    out.extend(("missing_generic_fallback", f"{kind}:{disposition}") for kind, disposition in sorted(kinds - generic))
    return out


def _rule(rule_id: str, signature: str | None = None, prefix: str | None = None, **over: str) -> Rule:
    return (
        rule_id,
        over.get("type", "process_event"),
        signature,
        prefix,
        over.get("disposition", "error"),
        over.get("label", f"label_{rule_id}"),
        over.get("klass", "exception"),
    )


_GENERIC = _rule("r90", label="unmapped_block")
_REGISTRIES = {"hook": {"known_hook"}, "gate": {"known_gate"}, "precommit": {"known-hook"}}


def _kinds(rules: list[Rule], registries: Registries | None = _REGISTRIES) -> set[str]:
    return {kind for kind, _ in rule_violations([*rules, _GENERIC], registries)}


def test_guard_real_rules_shape() -> None:
    assert rule_violations(FRICTION_RULES["rules"]) == []


def test_guard_accepts_registered_exact_rules() -> None:
    rules = [_rule("r01", "hook:known_hook"), _rule("r02", "gate:known_gate"), _rule("r03", "precommit:known-hook")]
    assert _kinds(rules) == set()


def test_guard_rejects_unregistered_hook_signature() -> None:
    assert _kinds([_rule("r01", "hook:other")]) == {"unregistered_hook_signature"}
    assert _kinds([_rule("r01", "hook:other")], None) == set()


def test_guard_rejects_unregistered_gate_signature() -> None:
    assert _kinds([_rule("r01", "gate:other")]) == {"unregistered_gate_signature"}


def test_guard_rejects_unregistered_precommit_signature() -> None:
    assert _kinds([_rule("r01", "precommit:other")]) == {"unregistered_precommit_signature"}


def test_guard_rejects_exact_signature_on_unregistered_source() -> None:
    assert "exact_signature_on_unregistered_source" in _kinds([_rule("r01", "other:thing")])
    assert "exact_signature_on_unregistered_source" in _kinds([_rule("r01", "status:x.failed")])
    assert "exact_signature_on_unregistered_source" in _kinds([_rule("r01", "Bash", type="tool_call")])
    assert "exact_signature_on_unregistered_source" in _kinds([_rule("r01", None, "status:", type="tool_call")])


def test_guard_rejects_undeclared_prefix() -> None:
    assert "undeclared_prefix" in _kinds([_rule("r01", None, "other:")])
    assert "undeclared_prefix" in _kinds([_rule("r01", None, "status:%")])


def test_guard_rejects_prefix_on_registered_source() -> None:
    for source in _REGISTERED_SOURCES:
        assert "prefix_on_registered_source" in _kinds([_rule("r01", None, f"{source}:")]), source


def test_guard_admits_the_status_prefix() -> None:
    assert _kinds([_rule("r01", None, "status:")]) == set()


def test_guard_rejects_tie() -> None:
    assert "tie" in _kinds([_rule("r01", "gate:known_gate"), _rule("r02", "gate:known_gate", label="other")])
    assert "tie" in _kinds([_rule("r01", None, "status:"), _rule("r02", None, "status:", label="other")])
    assert "tie" in [kind for kind, _ in rule_violations([_GENERIC, _rule("r91", label="x")], _REGISTRIES)]


def test_guard_rejects_label_with_two_classes() -> None:
    rules = [_rule("r01", "gate:known_gate", label="same"), _rule("r02", "hook:known_hook", label="same", klass="notice")]
    assert "label_with_two_classes" in _kinds(rules)


def test_guard_rejects_missing_generic_fallback() -> None:
    assert ("missing_generic_fallback", "process_event:warning") in rule_violations(
        [_GENERIC, _rule("r10", "gate:known_gate", disposition="warning")], _REGISTRIES
    )
    kinds = rule_violations([_GENERIC, _rule("r92", None, None, type="tool_call", disposition="blocked")], _REGISTRIES)
    assert kinds == []
    assert ("missing_generic_fallback", "tool_call:blocked") not in kinds
    unbacked = [_GENERIC, _rule("r92", "Bash", type="tool_call")]
    assert ("missing_generic_fallback", "tool_call:error") in rule_violations(unbacked, _REGISTRIES)


def test_guard_rejects_duplicate_or_reserved_rule_id() -> None:
    assert "duplicate_or_reserved_rule_id" in _kinds([_rule("r01", "gate:known_gate"), _rule("r01", "hook:known_hook")])
    assert "duplicate_or_reserved_rule_id" in _kinds([_rule("run", "gate:known_gate")])
