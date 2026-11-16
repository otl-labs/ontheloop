"""rec-5231: every action the bootstrap-apply guard names is a real action of its service.

Access Analyzer's ValidatePolicy returns ERROR INVALID_ACTION for an action in the wrong namespace, which the guard's
Deny would silently never match. Text predicates over local bootstrap_apply_guard_json; Action lists only, never
Condition keys.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.checks.iam_tf._ci_platform_role_iam import _jsonencode_statement_blocks, _parse_jsonencode_statement
from tests.checks.iam_tf._platform_security_hcl import _block_body

_ROOT = Path(__file__).resolve().parents[3]
_FILE = _ROOT / "terraform" / "bootstrap" / "bootstrap_ci_apply.tf"
_LOCAL = "bootstrap_apply_guard_json"
_PROVIDER_SID = "DenyProviderAndAccountWrite"
_MINTING_SID = "DenyCredentialMinting"
_REQUIRED_PROVIDER_ACTIONS = frozenset(
    {
        "iam:CreateOpenIDConnectProvider",
        "iam:DeleteOpenIDConnectProvider",
        "iam:UpdateOpenIDConnectProviderThumbprint",
        "iam:AddClientIDToOpenIDConnectProvider",
        "iam:RemoveClientIDFromOpenIDConnectProvider",
        "iam:TagOpenIDConnectProvider",
        "iam:UntagOpenIDConnectProvider",
        "iam:CreateSAMLProvider",
        "iam:UpdateSAMLProvider",
        "iam:DeleteSAMLProvider",
        "iam:TagSAMLProvider",
        "iam:UntagSAMLProvider",
        "iam:CreateServiceLinkedRole",
        "iam:DeleteServiceLinkedRole",
        "iam:CreateAccountAlias",
        "iam:DeleteAccountAlias",
        "iam:UpdateAccountPasswordPolicy",
        "iam:DeleteAccountPasswordPolicy",
        "iam:SetSecurityTokenServicePreferences",
    }
)
_PERMISSION_ONLY = frozenset({"iam:PassRole"})
_SERVICES = {"iam": "iam", "sts": "sts"}


def _guard_actions(text: str) -> dict[str, list[str]]:
    m = re.search(rf"^\s*{_LOCAL}\s*=\s*jsonencode\(\{{", text, re.M)
    assert m, f"local {_LOCAL} not found"
    body = _block_body(text, m.end() - 1)
    out: dict[str, list[str]] = {}
    for block in _jsonencode_statement_blocks(body):
        stmt = _parse_jsonencode_statement(block)
        out[stmt.sid or ""] = list(stmt.actions)
    return out


def _model_operations(service: str) -> frozenset[str]:
    import botocore.session

    return frozenset(botocore.session.get_session().get_service_model(service).operation_names)


def guard_action_problems(text: str) -> list[str]:
    stmts = _guard_actions(text)
    problems: list[str] = []
    missing = sorted(_REQUIRED_PROVIDER_ACTIONS - set(stmts.get(_PROVIDER_SID, [])))
    if missing:
        problems.append(f"required-subset: {_PROVIDER_SID} lacks {missing}")
    minting = set(stmts.get(_MINTING_SID, []))
    for sid, actions in stmts.items():
        stray = sorted(a for a in actions if a.startswith("sts:") and a not in minting)
        if stray:
            problems.append(f"sts-subset: {sid} names sts actions outside {_MINTING_SID}: {stray}")
    ops = {svc: _model_operations(model) for svc, model in _SERVICES.items()}
    for sid, actions in stmts.items():
        for action in actions:
            svc, _, name = action.partition(":")
            if svc in ops and "*" not in name and action not in _PERMISSION_ONLY and name not in ops[svc]:
                problems.append(f"botocore-model: {sid} names {action}, not an operation of {svc}")
    return problems


def test_every_guard_action_is_a_real_iam_action() -> None:
    assert guard_action_problems(_FILE.read_text(encoding="utf-8")) == []


def test_swapping_the_token_back_to_sts_is_red_on_every_predicate() -> None:
    text = _FILE.read_text(encoding="utf-8")
    bad = text.replace("iam:SetSecurityTokenServicePreferences", "sts:SetSecurityTokenServicePreferences", 1)
    assert bad != text
    problems = guard_action_problems(bad)
    for predicate in ("required-subset", "sts-subset", "botocore-model"):
        assert any(p.startswith(predicate) for p in problems), (predicate, problems)


@pytest.mark.parametrize("action", ["iam:NotARealOperation"])
def test_unknown_iam_operation_is_red(action: str) -> None:
    text = _FILE.read_text(encoding="utf-8")
    bad = text.replace('"iam:CreateAccountAlias"', f'"{action}"', 1)
    assert any(p.startswith("botocore-model") for p in guard_action_problems(bad))
