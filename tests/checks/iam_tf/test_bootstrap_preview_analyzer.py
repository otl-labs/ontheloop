"""Standing guard for PLAN-bootstrap-iam-analyzer-checks (PR 1): only bootstrap-preview may call the four IAM Access
Analyzer checks, through its own policy, and neither the apply role nor the shared plan-reads list names one.

Credential-free text tests in the iam_tf predicate convention: the predicate returns a problem list, the real-tree node
asserts it is empty, and a parametrized red node drives one mutation per class.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

_BOOTSTRAP = Path(__file__).resolve().parents[3] / "terraform" / "bootstrap"
_NEW = "bootstrap_preview_analyzer.tf"
_APPLY = "bootstrap_ci_apply.tf"
_ACTIONS = [
    "access-analyzer:ValidatePolicy",
    "access-analyzer:CheckAccessNotGranted",
    "access-analyzer:CheckNoNewAccess",
    "access-analyzer:CheckNoPublicAccess",
]


def _texts() -> dict[str, str]:
    return {f.name: f.read_text(encoding="utf-8") for f in sorted(_BOOTSTRAP.glob("*.tf"))}


def _nocomment(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _balanced(text: str, open_idx: int) -> str:
    depth = 0
    for i in range(open_idx, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[open_idx : i + 1]
    return text[open_idx:]


def _local(text: str, name: str) -> str:
    t = _nocomment(text)
    m = re.search(rf"^\s*{name}\s*=\s*", t, re.M)
    if not m:
        return ""
    brace, bracket = t.find("{", m.end()), t.find("[", m.end())
    idx = min(i for i in (brace, bracket) if i != -1)
    return _balanced(t, idx) if t[idx] == "{" else t[idx : t.find("]", idx) + 1]


def _strings(raw: str) -> list[str]:
    return re.findall(r'"([^"]+)"', raw)


def analyzer_problems(texts: dict[str, str]) -> list[str]:
    problems: list[str] = []
    new = texts.get(_NEW, "")
    if _strings(_local(new, "bootstrap_preview_analyzer_actions")) != _ACTIONS:
        problems.append("bootstrap_preview_analyzer_actions must be exactly the four analyzer actions")
    doc = _local(new, "bootstrap_preview_analyzer_json")
    if doc.count("Effect") != 1 or not re.search(r'Effect\s*=\s*"Allow"', doc):
        problems.append("the analyzer policy must hold exactly one Allow statement")
    m = re.search(r"Action\s*=\s*(\[[^\]]*\])", doc)
    if m is None or _strings(m.group(1)) != _ACTIONS:
        problems.append("the analyzer statement's actions must be exactly the four analyzer actions, as literals")
    if not re.search(r'Resource\s*=\s*\["\*"\]', doc):
        problems.append('the analyzer statement must be on "*"')
    attachments = [
        (m.group(1), _balanced(_nocomment(new), m.end() - 1))
        for m in re.finditer(r'resource\s+"aws_iam_role_policy_attachment"\s+"(\w+)"\s*\{', _nocomment(new))
    ]
    if len(attachments) != 1 or "aws_iam_role.bootstrap_preview.name" not in attachments[0][1]:
        problems.append("exactly one attachment of the analyzer policy, to aws_iam_role.bootstrap_preview")
    elif "aws_iam_policy.bootstrap_preview_analyzer.arn" not in attachments[0][1]:
        problems.append("the attachment must name aws_iam_policy.bootstrap_preview_analyzer")
    for name, text in texts.items():
        for m in re.finditer(r'resource\s+"aws_iam_role_policy_attachment"\s+"(\w+)"\s*\{', _nocomment(text)):
            body = _balanced(_nocomment(text), m.end() - 1)
            if "bootstrap_preview_analyzer" in body and "aws_iam_role.bootstrap_apply" in body:
                problems.append(f"{name}: an analyzer policy is attached to bootstrap-apply")
    apply_text = texts.get(_APPLY, "")
    for local in ("bootstrap_plan_read_actions", "bootstrap_apply_write_json", "bootstrap_apply_guard_json"):
        if "access-analyzer" in _local(apply_text, local):
            problems.append(f"{local} names an access-analyzer action")
    return problems


def _mut(file: str, old: str, new: str) -> Callable[[dict[str, str]], dict[str, str]]:
    def run(texts: dict[str, str]) -> dict[str, str]:
        assert old in texts[file], f"mutation anchor missing: {old!r}"
        return {**texts, file: texts[file].replace(old, new, 1)}

    return run


_MUTATIONS: dict[str, Callable[[dict[str, str]], dict[str, str]]] = {
    "fifth_action": _mut(
        _NEW, '"access-analyzer:CheckNoPublicAccess",\n  ]', '"access-analyzer:CheckNoPublicAccess",\n    "iam:PassRole",\n  ]'
    ),
    "fifth_action_in_statement": _mut(
        _NEW,
        '"access-analyzer:CheckNoPublicAccess",\n        ]',
        '"access-analyzer:CheckNoPublicAccess",\n          "iam:PassRole",\n        ]',
    ),
    "resource_not_star": _mut(_NEW, 'Resource = ["*"]', 'Resource = ["arn:aws:iam::*:policy/x"]'),
    "attached_to_apply": _mut(_NEW, "aws_iam_role.bootstrap_preview.name", "aws_iam_role.bootstrap_apply.name"),
    "appended_to_shared_reads": _mut(
        _APPLY,
        "  bootstrap_plan_read_actions = [\n",
        '  bootstrap_plan_read_actions = [\n    "access-analyzer:ValidatePolicy",\n',
    ),
    "dropped_attachment": _mut(_NEW, "aws_iam_role_policy_attachment", "aws_iam_role_policy_attachment_x"),
}


def test_bootstrap_preview_analyzer_grant_is_scoped() -> None:
    assert analyzer_problems(_texts()) == []


@pytest.mark.parametrize("name", sorted(_MUTATIONS))
def test_analyzer_predicate_is_red_on_each_mutation(name: str) -> None:
    assert analyzer_problems(_MUTATIONS[name](_texts())), f"{name}: the analyzer predicate stayed green"
