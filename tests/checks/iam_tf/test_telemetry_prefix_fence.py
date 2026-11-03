"""Decision 230 cl.5 standing guard: the boundary fences the production telemetry prefixes and the blob root.

Four Deny statements in terraform/bootstrap/github_ci_apply_boundary.tf. Each predicate returns a problem list so its
red cases drive the real predicate on synthetic HCL.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts.checks.iam_tf._read_coverage import _extract_bracket_block, _split_top_level_objects
from tests.checks.iam_tf._platform_security_hcl import _block_body, _mask, _strip_comments

_ACCOUNT = "arn:aws:iam::${var.account_id}:role/agent-platform-ducklake-"
_BUCKET = "arn:aws:s3:::agent-platform-data-lake"
_PROD = f"{_BUCKET}/ducklake-telemetry/*"
_PROD_BLOBS = f"{_BUCKET}/telemetry-blobs/ducklake_telemetry/*"
_BLOBS = f"{_BUCKET}/telemetry-blobs/*"
_WRITER, _TWIN, _READER, _ADMIN = (
    f"{_ACCOUNT}{n}" for n in ("telemetry-writer", "telemetry-writer-smoke", "telemetry-reader", "maintenance")
)
_IO_ACTIONS = ("s3:GetObject", "s3:GetObjectVersion", "s3:PutObject", "s3:DeleteObject")
_SPECS = {
    "DenyTelemetryProdPrefixIO": (_IO_ACTIONS, (_PROD, _PROD_BLOBS), (_WRITER, _READER, _ADMIN)),
    "DenyTelemetryBlobPut": (("s3:PutObject",), (_BLOBS,), (_WRITER, _TWIN)),
    "DenyTelemetryBlobDelete": (("s3:DeleteObject",), (_BLOBS,), (_ADMIN,)),
    "DenyTelemetryVersionDelete": (("s3:DeleteObjectVersion",), (_BLOBS, _PROD), ()),
}

_ROOT = Path(__file__).resolve().parents[3]


def _list(raw: str) -> list[str]:
    return re.findall(r'"([^"]*)"', raw)


def _field(body: str, name: str, anchor: str = "") -> str:
    match = re.search(rf"{anchor}\b{name}\s*=\s*(\[[^\]]*\]|\"[^\"]*\"|[^\s,]+)", body, re.M)
    return match.group(1) if match else ""


def _json_conditions(body: str) -> list[tuple[str, str, list[str]]]:
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
                    "conditions": _json_conditions(body),
                }
            )
    return out


def _block_statements(text: str) -> list[dict]:
    clean, out = _strip_comments(text), []
    for head in re.finditer(r"\bstatement\s*\{", _mask(clean)):
        body, conds = _block_body(clean, head.end() - 1), []
        for cond in re.finditer(r"\bcondition\s*\{", _mask(body)):
            cbody = _block_body(body, cond.end() - 1)
            conds.append(
                (_list(_field(cbody, "test"))[0], _list(_field(cbody, "variable"))[0], _list(_field(cbody, "values")))
            )
        out.append(
            {
                "sid": (_list(_field(body, "sid", "^\\s*")) or [""])[0],
                "effect": (_list(_field(body, "effect", "^\\s*")) or ["Allow"])[0],
                "actions": _list(_field(body, "actions", "^\\s*")),
                "resources": _field(body, "resources", "^\\s*"),
                "conditions": conds,
            }
        )
    return out


def _bootstrap(name: str) -> str:
    return (_ROOT / "terraform" / "bootstrap" / name).read_text(encoding="utf-8")


def statement_problems(text: str, sid: str) -> list[str]:
    actions, resources, principals = _SPECS[sid]
    stmt = next((s for s in _json_statements(text) if s["sid"] == sid), None)
    if stmt is None or stmt["effect"] != "Deny":
        return [f"{sid}: Deny statement missing"]
    expected = [("ArnNotLike", "aws:PrincipalArn", sorted(principals))] if principals else []
    found = (
        sorted(stmt["actions"]),
        sorted(_list(stmt["resources"])),
        [(op, key, sorted(values)) for op, key, values in stmt["conditions"]],
    )
    return [] if found == (sorted(actions), sorted(resources), expected) else [f"{sid}: differs from Decision 230 cl.5"]


def _hcl(sid: str, *, effect: str = "Deny", drop: str = "", add: str = "") -> str:
    actions, resources, principals = _SPECS[sid]
    quote = lambda items: ", ".join(f'"{i}"' for i in items if i != drop)  # noqa: E731
    cond = (
        f'Condition = {{ ArnNotLike = {{ "aws:PrincipalArn" = [{quote(principals + ((add,) if add else ()))}] }} }}'
        if principals
        else ""
    )
    return (
        f'locals {{ p = jsonencode({{ Statement = [{{ Sid = "{sid}", Effect = "{effect}", '
        f"Action = [{quote(actions)}], Resource = [{quote(resources)}], {cond} }}] }}) }}"
    )


def _real(sid: str) -> list[str]:
    return statement_problems(_bootstrap("github_ci_apply_boundary.tf"), sid)


@pytest.mark.parametrize("sid", sorted(_SPECS))
def test_predicate_is_green_on_synthetic_hcl_and_red_when_the_statement_is_absent(sid: str) -> None:
    assert statement_problems(_hcl(sid), sid) == []
    assert statement_problems("locals { p = jsonencode({ Statement = [] }) }", sid) == [f"{sid}: Deny statement missing"]
    assert statement_problems(_hcl(sid, effect="Allow"), sid) == [f"{sid}: Deny statement missing"]


def test_real_boundary_fences_the_production_telemetry_prefixes() -> None:
    assert _real("DenyTelemetryProdPrefixIO") == []


@pytest.mark.parametrize("dropped", _IO_ACTIONS)
def test_prefix_io_predicate_is_red_per_dropped_action(dropped: str) -> None:
    assert statement_problems(_hcl("DenyTelemetryProdPrefixIO", drop=dropped), "DenyTelemetryProdPrefixIO")


@pytest.mark.parametrize("dropped", [_PROD, _PROD_BLOBS])
def test_prefix_io_predicate_is_red_per_dropped_resource(dropped: str) -> None:
    assert statement_problems(_hcl("DenyTelemetryProdPrefixIO", drop=dropped), "DenyTelemetryProdPrefixIO")


@pytest.mark.parametrize("sid", ["DenyTelemetryProdPrefixIO", "DenyTelemetryBlobPut", "DenyTelemetryBlobDelete"])
def test_principal_lists_are_red_when_widened_or_narrowed(sid: str) -> None:
    assert statement_problems(_hcl(sid, add=f"{_ACCOUNT}other"), sid)
    for principal in _SPECS[sid][2]:
        assert statement_problems(_hcl(sid, drop=principal), sid), principal


def test_real_boundary_blob_put_is_writer_roles_only() -> None:
    assert _real("DenyTelemetryBlobPut") == []
    assert statement_problems(_hcl("DenyTelemetryBlobPut", add=_ADMIN), "DenyTelemetryBlobPut")
    assert statement_problems(_hcl("DenyTelemetryBlobPut", drop=_BLOBS), "DenyTelemetryBlobPut")


def test_real_boundary_blob_delete_is_admin_maintenance_only() -> None:
    assert _real("DenyTelemetryBlobDelete") == []
    assert statement_problems(_hcl("DenyTelemetryBlobDelete", add=_WRITER), "DenyTelemetryBlobDelete")
    assert statement_problems(_hcl("DenyTelemetryBlobDelete", drop=_ADMIN), "DenyTelemetryBlobDelete")


def test_real_boundary_denies_every_version_delete() -> None:
    sid = "DenyTelemetryVersionDelete"
    assert _real(sid) == []
    assert statement_problems(_hcl(sid, drop=_PROD), sid)
    assert statement_problems(_hcl(sid, drop=_BLOBS), sid)
    conditioned = _hcl(sid).replace(
        "Resource = [", 'Condition = { ArnNotLike = { "aws:PrincipalArn" = ["x"] } }, Resource = [', 1
    )
    assert statement_problems(conditioned, sid)
