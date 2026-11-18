"""rec-5320: the validator's and the loader's rule shape checks give the same answer on every generated variant."""

from __future__ import annotations

import copy
from typing import Any

from scripts.checks.contracts.validate_persona_contracts import _malformed_rule
from src.persona_backends.contract import _rule_malformed
from tests.persona_contracts.fixture_tree import MALFORMED_RULES, RULE

_REPLACEMENTS: list[Any] = [None, 5, "x", [], {}]
_REPLACEMENTS += [[r] for r in list(_REPLACEMENTS)]


def _paths(node: Any, trail: tuple = ()) -> list[tuple]:
    out: list[tuple] = [trail]
    if isinstance(node, dict):
        for key, child in node.items():
            out += _paths(child, (*trail, key))
    elif isinstance(node, list):
        for index, child in enumerate(node):
            out += _paths(child, (*trail, index))
    return out


def _get(root: Any, trail: tuple) -> Any:
    for step in trail:
        root = root[step]
    return root


def _variants() -> list[Any]:
    out: list[Any] = list(MALFORMED_RULES.values())
    for trail in _paths(RULE):
        if not trail:
            for value in _REPLACEMENTS:
                out.append(copy.deepcopy(value))
            continue
        for value in _REPLACEMENTS:
            variant = copy.deepcopy(RULE)
            _get(variant, trail[:-1])[trail[-1]] = copy.deepcopy(value)
            out.append(variant)
        variant = copy.deepcopy(RULE)
        parent = _get(variant, trail[:-1])
        if isinstance(parent, dict):
            del parent[trail[-1]]
            out.append(variant)
    for trail in _paths(RULE):
        variant = copy.deepcopy(RULE)
        node = _get(variant, trail)
        if isinstance(node, dict):
            node["extra"] = 1
            out.append(variant)
    return out


def test_both_shape_checks_agree_on_generated_rules() -> None:
    variants = _variants()
    answers = []
    for variant in variants:
        validator, loader = _malformed_rule(variant), _rule_malformed(variant)
        assert validator == loader, variant
        answers.append(validator)
    assert len(variants) >= 60
    assert True in answers and False in answers
