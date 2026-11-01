"""rec-4645 acceptance: the admin-applied boundary denies the CI branch and CI PR roles state, plan and transcript IO.

Decision 143 cl.1; plan docs/plans/PLAN-ci-branch-boundary-state-fence.yaml. boundary_fence_problems returns a problem
list so its red cases drive the real predicate on synthetic HCL.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts.checks.iam_tf._read_coverage import _extract_bracket_block, _split_top_level_objects
from tests.checks.iam_tf._platform_security_hcl import _block_body, _mask, _strip_comments

_SID = "DenyCiAgentRoleStatePlanAndTranscriptIO"
_BUCKET = "arn:aws:s3:::agent-platform-data-lake"
_ACTIONS = ("s3:GetObject", "s3:GetObjectVersion", "s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion")
_RESOURCES = (f"{_BUCKET}/tfstate/*", f"{_BUCKET}/tfplan/*", f"{_BUCKET}/ci-rca-evidence/review-transcripts/*")
_ROLE = "arn:aws:iam::${var.account_id}:role/agent-platform-github-ci-"
_ROLES = (f"{_ROLE}branch", f"{_ROLE}pr")
_ROOT = Path(__file__).resolve().parents[3]


def _read(name: str) -> str:
    return (_ROOT / "terraform" / "bootstrap" / name).read_text(encoding="utf-8")


def _list(raw: str) -> list[str]:
    return re.findall(r'"([^"]*)"', raw)


def _field(body: str, name: str) -> str:
    match = re.search(rf"\b{name}\s*=\s*(\[[^\]]*\]|\"[^\"]*\"|[^\s,]+)", body, re.M)
    return match.group(1) if match else ""


def _conditions(body: str) -> list[tuple[str, str, list[str]]]:
    head = re.search(r"\bCondition\s*=\s*\{", _mask(body))
    if head is None:
        return []
    inner, out = _block_body(body, head.end() - 1), []
    for op in re.finditer(r"(\w+)\s*=\s*\{", _mask(inner)):
        for key in re.finditer(r'"([^"]+)"\s*=\s*(\[[^\]]*\]|"[^"]*")', _block_body(inner, op.end() - 1)):
            out.append((op.group(1), key.group(1), _list(key.group(2))))
    return out


def _json_statements(text: str) -> list[dict]:
    clean, out = _strip_comments(text), []
    for head in re.finditer(r"\bStatement\s*=\s*\[", clean):
        for body in _split_top_level_objects(_extract_bracket_block(clean, head.end() - 1)):
            out.append(
                {
                    "sid": (_list(_field(body, "Sid")) or [""])[0],
                    "effect": (_list(_field(body, "Effect")) or [""])[0],
                    "actions": _list(_field(body, "Action")),
                    "resources": _field(body, "Resource"),
                    "conditions": _conditions(body),
                }
            )
    return out


def boundary_fence_problems(text: str) -> list[str]:
    stmts = _json_statements(text)
    mine = [s for s in stmts if s["sid"] == _SID]
    if len(mine) != 1 or mine[0]["effect"] != "Deny":
        return [f"{_SID}: Deny statement missing or duplicated"]
    stmt, problems = mine[0], []
    if sorted(stmt["actions"]) != sorted(_ACTIONS):
        problems.append(f"{_SID}: actions differ")
    if sorted(_list(stmt["resources"])) != sorted(_RESOURCES):
        problems.append(f"{_SID}: resources differ")
    if [(op, key, sorted(vals)) for op, key, vals in stmt["conditions"]] != [("ArnLike", "aws:PrincipalArn", sorted(_ROLES))]:
        problems.append(f"{_SID}: condition differs")
    for other in stmts:
        if other is not stmt and any(r in other["resources"] for r in _RESOURCES):
            problems.append(f"{other['sid']}: second statement names a fenced resource")
    return problems


def _hcl(
    *, effect: str = "Deny", drop: str = "", op: str = "ArnLike", cond: bool = True, add: str = "", extra: str = ""
) -> str:
    def quote(items: tuple[str, ...]) -> str:
        return ", ".join(f'"{i}"' for i in items if i != drop)

    roles = quote(_ROLES + ((add,) if add else ()))
    condition = f'Condition = {{ {op} = {{ "aws:PrincipalArn" = [{roles}] }} }}' if cond else ""
    return (
        f'locals {{ p = jsonencode({{ Statement = [{{ Sid = "{_SID}", Effect = "{effect}", '
        f"Action = [{quote(_ACTIONS)}], Resource = [{quote(_RESOURCES)}], {condition} }}{extra}] }}) }}"
    )


def test_synthetic_good_hcl_passes() -> None:
    assert boundary_fence_problems(_hcl()) == []


def test_real_boundary_fences_ci_agent_roles() -> None:
    assert boundary_fence_problems(_read("github_ci_apply_boundary.tf")) == []


def test_statement_absent_is_red() -> None:
    assert boundary_fence_problems("locals { p = jsonencode({ Statement = [] }) }")


def test_effect_allow_is_red() -> None:
    assert boundary_fence_problems(_hcl(effect="Allow"))


@pytest.mark.parametrize("dropped", _ACTIONS + _RESOURCES + _ROLES)
def test_each_dropped_element_is_red(dropped: str) -> None:
    assert boundary_fence_problems(_hcl(drop=dropped))


def test_condition_removed_is_red() -> None:
    assert boundary_fence_problems(_hcl(cond=False))


def test_operator_changed_is_red() -> None:
    assert boundary_fence_problems(_hcl(op="ArnNotLike"))


@pytest.mark.parametrize("added", [f"{_ROLE}apply", f"{_ROLE}*"])
def test_widened_role_list_is_red(added: str) -> None:
    assert boundary_fence_problems(_hcl(add=added))


def test_second_statement_naming_tfstate_is_red() -> None:
    extra = f', {{ Sid = "Other", Effect = "Deny", Action = ["s3:GetObject"], Resource = ["{_RESOURCES[0]}"] }}'
    assert boundary_fence_problems(_hcl(extra=extra))


def test_allow_statement_naming_a_fenced_prefix_is_red() -> None:
    extra = f', {{ Sid = "Other", Effect = "Allow", Action = ["s3:GetObject"], Resource = ["{_RESOURCES[1]}"] }}'
    assert boundary_fence_problems(_hcl(extra=extra))
