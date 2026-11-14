#!/usr/bin/env python3
"""Plan classifier of the terraform/bootstrap CI apply (PLAN-bootstrap-ci-gated-apply).

Stdlib only. Decides which plan rows a CI role may write (the gate's `classify`) and builds the names-only rows the
preview prints. A row is {address, actions, changed, flags, allows, resources}; names and public HCL action names
only, with account digits replaced, and an instance key never printed (rows group on the normalised address).
Fail closed: anything the classifier cannot resolve is protected.
"""

from __future__ import annotations

import fnmatch
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_core = _load("plan_names_core")

CI_TYPES = frozenset({"aws_iam_role", "aws_iam_role_policy", "aws_iam_role_policy_attachment", "aws_iam_policy"})
OTHER_ATTACH_TYPES = frozenset(
    {"aws_iam_user_policy_attachment", "aws_iam_group_policy_attachment", "aws_iam_policy_attachment"}
)
BOUNDARY_NAME = "agent-platform-github-ci-apply-boundary"
BOUNDARY_SUFFIX = f":policy/{BOUNDARY_NAME}"
APPLY_ROLE = "agent-platform-github-ci-apply"
TRUST_WRITABLE_ROLES = frozenset({APPLY_ROLE})
PROTECTED_NAME_PATTERNS = frozenset(
    {
        "role/PlatformAdmin",
        "role/PlatformDev",
        "role/github-settings-*",
        "role/bootstrap-*",
        "role/platform-security-*",
        "role/ducklake-api-*",
        f"policy/{BOUNDARY_NAME}",
        "policy/agent-platform-session-*",
        "policy/platform-security-*",
        "policy/bootstrap-*",
    }
)
STATE_OBJECT_ARNS = (
    "arn:aws:s3:::agent-platform-bootstrap-tfstate/terraform.tfstate",
    "arn:aws:s3:::agent-platform-bootstrap-tfstate/terraform.tfstate.tflock",
)
SYNTHETIC_ARNS = (
    "arn:aws:iam::<account>:user/x",
    "arn:aws:iam::<account>:group/x",
    "arn:aws:iam::<account>:oidc-provider/x",
    "arn:aws:iam::<account>:saml-provider/x",
)
IAM_WRITE_PROBES = (
    "iam:createrole", "iam:deleterole", "iam:putrolepolicy", "iam:deleterolepolicy", "iam:attachrolepolicy",
    "iam:detachrolepolicy", "iam:updateassumerolepolicy", "iam:putrolepermissionsboundary", "iam:createpolicy",
    "iam:createpolicyversion", "iam:deletepolicy", "iam:setdefaultpolicyversion", "iam:passrole", "iam:createuser",
    "iam:createaccesskey", "iam:attachuserpolicy", "iam:putuserpolicy", "iam:creategroup", "iam:addusertogroup",
    "iam:createopenidconnectprovider", "iam:tagrole", "iam:updaterole",
)  # fmt: skip
SECRET_PROBES = ("secretsmanager:getsecretvalue", "ssm:getparameter", "ssm:getparameters", "ssm:getparametersbypath")
_ACCOUNT_RE = re.compile(r"[0-9]{12}")
_READ_VERBS = ("get", "list", "describe")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else ([] if value is None else [value])


class Unreadable(Exception):
    pass


class Plan:
    """Indexes of one `terraform show -json` plan: changed rows, prior-state resources and configuration."""

    def __init__(self, plan: Any) -> None:
        if not isinstance(plan, dict) or not isinstance(plan.get("resource_changes"), list):
            raise Unreadable("plan JSON missing or malformed")
        self.raw = plan
        self.changes = [rc for rc in plan["resource_changes"] if isinstance(rc, dict) and rc.get("mode") != "data"]
        self.cfg = {
            str(r.get("address")): r
            for r in _dict(_dict(plan.get("configuration")).get("root_module")).get("resources") or []
            if isinstance(r, dict)
        }
        values = _dict(_dict(plan.get("prior_state")).get("values"))
        self.prior = [r for r in _dict(values.get("root_module")).get("resources") or [] if isinstance(r, dict)]
        self.by_address = {_core.normalise_address(str(rc.get("address", ""))): rc for rc in self.changes}

    def _prior_by_address(self, address: str) -> dict[str, Any]:
        for r in self.prior:
            if _core.normalise_address(str(r.get("address", ""))) == address:
                return _dict(r.get("values"))
        return {}

    def value(self, address: str, key: str) -> Any:
        rc = self.by_address.get(address)
        if rc:
            after = _dict(_dict(rc.get("change")).get("after"))
            if key in after:
                return after[key]
        prior = self._prior_by_address(address)
        if key in prior and not (rc and _dict(_dict(rc.get("change")).get("after_unknown")).get(key)):
            return prior[key]
        const = _dict(_dict(_dict(self.cfg.get(address)).get("expressions")).get(key)).get("constant_value")
        return const if isinstance(const, (str, list, dict)) else None

    def refs(self, address: str, key: str) -> list[str]:
        expr = _dict(_dict(_dict(self.cfg.get(address)).get("expressions")).get(key))
        return [r for r in expr.get("references") or [] if isinstance(r, str)]


def _row_type(address: str) -> str:
    parts = address.split(".")
    return parts[-2] if len(parts) >= 2 else ""


def _name_part(value: Any, marker: str) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value.rsplit(marker, 1)[-1] if marker and marker in value else value


def _resolve(plan: Plan, address: str, key: str, marker: str = "") -> str | None:
    """The name a role/policy reference field resolves to: a known value, else a same-plan resource's planned name."""
    known = _name_part(plan.value(address, key), marker)
    if known:
        return known
    for ref in plan.refs(address, key):
        target = ".".join(ref.split(".")[:2])
        if target in plan.by_address and _row_type(target) in ("aws_iam_role", "aws_iam_policy"):
            name = _name_part(plan.value(target, "name"), "")
            if name:
                return name
    return None


def target_names(plan: Plan, address: str) -> list[str | None]:
    kind = _row_type(address)
    if kind in ("aws_iam_role", "aws_iam_policy"):
        return [_resolve(plan, address, "name")]
    if kind == "aws_iam_role_policy":
        return [_resolve(plan, address, "role")]
    if kind == "aws_iam_role_policy_attachment":
        return [_resolve(plan, address, "role"), _resolve(plan, address, "policy_arn", ":policy/")]
    return [None]


def _name_ok(name: str | None) -> bool:
    return (
        name is not None
        and name.startswith("agent-platform-")
        and name != BOUNDARY_NAME
        and not name.startswith("agent-platform-session-")
    )  # type: ignore[union-attr]


def _statements(doc: Any) -> list[dict[str, Any]]:
    if isinstance(doc, str):
        try:
            doc = json.loads(doc)
        except ValueError:
            return []
    stmts = _dict(doc).get("Statement")
    return [s for s in _as_list(stmts) if isinstance(s, dict)]


def _grants(doc: Any) -> set[tuple[str, str, str]]:
    out: set[tuple[str, str, str]] = set()
    for s in _statements(doc):
        if s.get("Effect") != "Allow":
            continue
        cond = json.dumps(s.get("Condition") or {}, sort_keys=True)
        if "NotAction" in s or "NotResource" in s:
            out.add(("!notaction-or-notresource", json.dumps(s, sort_keys=True), cond))
            continue
        for a in _as_list(s.get("Action")):
            for r in _as_list(s.get("Resource")):
                out.add((str(a), str(r), cond))
    return out


def _resource_part(arn: str) -> str:
    parts = arn.split(":", 5)
    return parts[5] if arn.startswith("arn:") and len(parts) == 6 else arn


def _iam_resource_ok(resource: str) -> bool:
    low = resource.lower()
    return low.startswith("arn:aws:iam:") and _resource_part(low).startswith(
        ("role/agent-platform-", "policy/agent-platform-")
    )


def protected_arns(plan: Plan) -> list[str]:
    arns = list(STATE_OBJECT_ARNS) + list(SYNTHETIC_ARNS)
    for r in plan.prior:
        values = _dict(r.get("values"))
        name = values.get("name") if isinstance(values.get("name"), str) else None
        if str(r.get("type")) in CI_TYPES and _name_ok(name):
            continue
        arns += [str(values[k]) for k in ("arn", "execution_arn") if isinstance(values.get(k), str)]
    return [a.lower() for a in arns]


def _grant_protected(action: str, resource: str, protected: list[str]) -> str | None:
    if action.startswith("!notaction"):
        return "not_action_or_resource"
    a, r = action.lower(), resource.lower()
    if any(fnmatch.fnmatchcase(p, a) for p in IAM_WRITE_PROBES) and not _iam_resource_ok(resource):
        return "iam_write_outside_agent_platform"
    if any(fnmatch.fnmatchcase(p, a) for p in SECRET_PROBES):
        return "secret_read"
    if fnmatch.fnmatchcase("s3:getobject", a) and any(fnmatch.fnmatchcase(s, r) for s in STATE_OBJECT_ARNS):
        return "state_read"
    verb = a.split(":", 1)[-1]
    if not verb.startswith(_READ_VERBS) and any(fnmatch.fnmatchcase(p, r) for p in protected):
        return "write_reaches_protected"
    return None


def _policy_docs(rc: dict[str, Any]) -> tuple[Any, Any, bool]:
    change = _dict(rc.get("change"))
    unknown = bool(_dict(change.get("after_unknown")).get("policy"))
    return _dict(change.get("before")).get("policy"), _dict(change.get("after")).get("policy"), unknown


def _role_reasons(plan: Plan, address: str, change: dict[str, Any]) -> list[str]:
    after = _dict(change.get("after"))
    boundary = after.get("permissions_boundary")
    reasons: list[str] = []
    if _dict(change.get("after_unknown")).get("permissions_boundary") or not (
        isinstance(boundary, str) and boundary.endswith(BOUNDARY_SUFFIX)
    ):
        reasons.append("boundary")
    if after.get("inline_policy") or after.get("managed_policy_arns"):
        reasons.append("inline_or_managed_grant")
    updates_trust = change.get("actions") == ["update"] and "assume_role_policy" in _core.changed_names(change)
    if updates_trust and not set(target_names(plan, address)) <= TRUST_WRITABLE_ROLES:
        reasons.append("trust_write")
    return reasons


def _document_reasons(rc: dict[str, Any], protected: list[str]) -> list[str]:
    before_doc, after_doc, doc_unknown = _policy_docs(rc)
    reasons = ["policy_unknown"] if doc_unknown else []
    for action, resource, _cond in sorted(_grants(after_doc) - _grants(before_doc)):
        why = _grant_protected(action, resource, protected)
        if why:
            reasons.append(why)
    return reasons


def protected_reasons(plan: Plan, rc: dict[str, Any], protected: list[str]) -> list[str]:
    address = _core.normalise_address(str(rc.get("address", "")))
    change = _dict(rc.get("change"))
    actions = [str(a) for a in change.get("actions") or []]
    kind = _row_type(address)
    reasons: list[str] = []
    if actions not in (["create"], ["update"]) or change.get("importing") or rc.get("previous_address"):
        reasons.append("action")
    if "[" in str(rc.get("address", "")):
        reasons.append("instance_key")
    if kind not in CI_TYPES:
        return reasons + ["type"]
    if _dict(plan.cfg.get(address)).get("provisioners"):
        reasons.append("provisioner")
    if not all(_name_ok(n) for n in target_names(plan, address)):
        reasons.append("name")
    if kind == "aws_iam_role":
        reasons += _role_reasons(plan, address, change)
    if kind in ("aws_iam_role_policy", "aws_iam_policy"):
        reasons += _document_reasons(rc, protected)
    if kind == "aws_iam_policy" and actions == ["update"] and _attached_elsewhere(plan, address):
        reasons.append("attached_outside_bounded_roles")
    return sorted(set(reasons))


ATTACH_TYPES = frozenset({"aws_iam_role_policy_attachment"}) | OTHER_ATTACH_TYPES


def _attachments(plan: Plan) -> list[tuple[str, str | None, str | None]]:
    """(type, role name, policy name) of every attachment in prior state and the plan."""
    out: list[tuple[str, str | None, str | None]] = []
    for r in plan.prior:
        values = _dict(r.get("values"))
        if str(r.get("type")) in ATTACH_TYPES:
            out.append(
                (str(r.get("type")), _name_part(values.get("role"), ""), _name_part(values.get("policy_arn"), ":policy/"))
            )
    for address in plan.by_address:
        if _row_type(address) in ATTACH_TYPES:
            out.append(
                (_row_type(address), _resolve(plan, address, "role"), _resolve(plan, address, "policy_arn", ":policy/"))
            )
    return out


def _bounded_role(plan: Plan, role: str | None) -> bool:
    if not _name_ok(role):
        return False
    for address, rc in plan.by_address.items():
        if _row_type(address) == "aws_iam_role" and plan.value(address, "name") == role:
            boundary = _dict(_dict(rc.get("change")).get("after")).get("permissions_boundary")
            return isinstance(boundary, str) and boundary.endswith(BOUNDARY_SUFFIX)
    for r in plan.prior:
        values = _dict(r.get("values"))
        if str(r.get("type")) == "aws_iam_role" and values.get("name") == role:
            boundary = values.get("permissions_boundary")
            return isinstance(boundary, str) and boundary.endswith(BOUNDARY_SUFFIX)
    return False


def _attached_elsewhere(plan: Plan, address: str) -> bool:
    name = _resolve(plan, address, "name")
    for kind, role, policy in _attachments(plan):
        if policy != name:
            continue
        if kind != "aws_iam_role_policy_attachment" or not _bounded_role(plan, role):
            return True
    return False


def _sanitise(text: str) -> str:
    return _ACCOUNT_RE.sub("<account>", text)


def _shown_resource(resource: str) -> str:
    return _sanitise(_resource_part(resource))


def _deny_changes(before: Any, after: Any) -> list[str]:
    def denies(doc: Any) -> dict[str, str]:
        return {str(s.get("Sid", "")): json.dumps(s, sort_keys=True) for s in _statements(doc) if s.get("Effect") == "Deny"}

    old, new = denies(before), denies(after)
    sids = {sid for sid in old if new.get(sid) != old[sid]}
    old_allow = {str(s.get("Sid", "")): s for s in _statements(before) if s.get("Effect") == "Allow"}
    for s in _statements(after):
        prior = old_allow.get(str(s.get("Sid", "")))
        if s.get("Effect") != "Allow" or not prior:
            continue
        if (prior.get("Condition") and not s.get("Condition")) or set(map(str, _as_list(s.get("Resource")))) - set(
            map(str, _as_list(prior.get("Resource")))
        ):
            sids.add(str(s.get("Sid", "")))
    return sorted(sids)


def build_rows(plan: Plan) -> list[dict[str, Any]]:
    """One row per normalised address with a change; a keyed group prints its address and count only."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for rc in plan.changes:
        change = _dict(rc.get("change"))
        actions = [str(a) for a in change.get("actions") or []]
        if _core.action_bucket(actions, bool(change.get("importing"))) is None:
            continue
        groups.setdefault(_core.normalise_address(str(rc.get("address", ""))), []).append(rc)
    protected = protected_arns(plan)
    rows: list[dict[str, Any]] = []
    for address in sorted(groups):
        members = groups[address]
        rc = members[0]
        change = _dict(rc.get("change"))
        actions = [str(a) for a in change.get("actions") or []] + (["import"] if change.get("importing") else [])
        reasons = sorted({r for m in members for r in protected_reasons(plan, m, protected)})
        if len(members) > 1:
            reasons = sorted(set(reasons) | {"instance_key"})
        kind = _row_type(address)
        flags: list[str] = []
        changed = _core.changed_names(change)
        before_doc, after_doc, _unknown = _policy_docs(rc)
        if kind == "aws_iam_role" and actions == ["create"]:
            flags.append("NEW_ROLE")
        if "assume_role_policy" in changed:
            flags.append("TRUST")
        if (
            kind in ("aws_iam_role_policy", "aws_iam_policy") and "policy" in changed
        ) or kind == "aws_iam_role_policy_attachment":
            flags.append("GRANT")
        deny_sids = _deny_changes(before_doc, after_doc) if kind in ("aws_iam_role_policy", "aws_iam_policy") else []
        if deny_sids:
            flags.append("GRANT_SHAPE")
        if APPLY_ROLE in [n for n in target_names(plan, address) if n] and kind != "aws_iam_role":
            flags.append("NARROWS")
        if kind == "aws_iam_policy" and _attached_to(plan, address, APPLY_ROLE):
            flags.append("NARROWS")
        gained = sorted(_grants(after_doc) - _grants(before_doc)) if "GRANT" in flags else []
        rows.append(
            {
                "address": address,
                "actions": actions,
                "changed": changed,
                "count": len(members),
                "flags": sorted(set(flags)),
                "allows": sorted({g[0] for g in gained if not g[0].startswith("!")}),
                "resources": sorted({_shown_resource(g[1]) for g in gained if not g[0].startswith("!")}),
                "sids": deny_sids,
                "protected": reasons,
            }
        )
    return rows


def _attached_to(plan: Plan, address: str, role: str) -> bool:
    name = _resolve(plan, address, "name")
    return any(r == role and p == name and k == "aws_iam_role_policy_attachment" for k, r, p in _attachments(plan))
