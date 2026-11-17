#!/usr/bin/env python3
"""Selection and classing for the bootstrap preview's IAM Access Analyzer checks (PLAN-bootstrap-iam-analyzer-checks).

Stdlib only; it runs no subprocess and makes no network call, and reaches the classifier only through bind(), the
module object the analyzer passes in. Turns one `terraform show -json` plan into units, one per checked document, each
with the classes it belongs to (config/iam-invariants.json). The selection predicate is the classifier's
checked_document, never reimplemented here. Names and digests only; a document is carried in memory, never printed.
"""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any

cls: Any = None
core: Any = None
Unreadable: Any = None
SWEPT_TYPES = ("aws_iam_policy", "aws_iam_role_policy", "aws_iam_user_policy", "aws_iam_role")
ATTACH_TYPES = ("aws_iam_role_policy_attachment", "aws_iam_user_policy_attachment")
INLINE_OR_MANAGED = ("aws_iam_policy", "aws_iam_role_policy", "aws_iam_user_policy")


def bind(classify: Any) -> None:
    """Adopt the analyzer's one load of the classifier, so there is one Plan and one Unreadable class."""
    global cls, core, Unreadable
    cls, core, Unreadable = classify, classify._core, classify.Unreadable


def load_invariants(path: str) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        assert isinstance(data["classes"], list) and isinstance(data["validate_policy_block"], dict)
        return data
    except (OSError, ValueError, KeyError, AssertionError) as exc:
        raise Unreadable("invariants") from exc


def _same(a: str | None, b: str | None) -> bool:
    return a is not None and b is not None and a.casefold() == b.casefold()


def _match(name: str | None, globs: list[str]) -> bool:
    """IAM names are case-insensitive, so a glob matches either case (the fail-closed direction)."""
    return name is not None and any(fnmatch.fnmatchcase(name.casefold(), g.casefold()) for g in globs)


def attachments(plan: Any) -> list[tuple[str, str | None, str | None]]:
    """(principal kind, principal name, policy name) of every attachment in prior state and in the plan."""
    out: list[tuple[str, str | None, str | None]] = []
    for r in plan.prior:
        values = cls._dict(r.get("values"))
        kind = str(r.get("type"))
        if kind in ATTACH_TYPES:
            who = "user" if "user" in kind else "role"
            out.append((who, cls._name_part(values.get(who), ""), cls._name_part(values.get("policy_arn"), ":policy/")))
    for address in plan.by_address:
        kind = cls._row_type(address)
        if kind in ATTACH_TYPES:
            who = "user" if "user" in kind else "role"
            out.append((who, cls._resolve(plan, address, who), cls._resolve(plan, address, "policy_arn", ":policy/")))
    return out


class Unit:
    """One checked document: its kind (identity, trust, managed or unknown), its classes and its checks' inputs."""

    def __init__(self, address: str, kind: str, doc: Any, names: tuple[str | None, list[tuple[str, str | None]]]) -> None:
        self.address, self.kind, self.doc = address, kind, doc
        self.policy_name, self.principals = names
        self.prior: Any = None
        self.attached = False
        self.digest = cls.document_digest(doc)


def _unit_names(plan: Any, address: str, kind: str, values: dict[str, Any] | None = None, after: Any = None) -> Any:
    """(policy name, principals) of an identity document: from prior-state values, else the instance's own after values."""

    def get(key: str, marker: str = "") -> str | None:
        source = values if values is not None else after
        known = cls._name_part(source.get(key), marker) if isinstance(source, dict) else None
        if known or values is not None:
            return known
        return cls._resolve(plan, address, key, marker)

    if kind == "aws_iam_policy":
        name = get("name")
        return name, [(w, p) for w, p, pol in attachments(plan) if name and _same(pol, name)]
    who = "user" if "user" in kind else "role"
    if kind in ("aws_iam_role_policy", "aws_iam_user_policy"):
        inline = get("name")
        return inline, [(who, get(who))]
    return get("policy_arn", ":policy/"), [(who, get(who))]


def classes_of(inv: dict[str, Any], unit: Unit) -> list[dict[str, Any]]:
    """Every class whose glob matches the document's own name or a principal it grants; unresolved means all."""
    classes = inv["classes"]
    if unit.policy_name is None and unit.kind == "identity" and not unit.principals:
        return list(classes)
    if any(name is None for _who, name in unit.principals):
        return list(classes)
    hits = []
    for c in classes:
        by_policy = _match(unit.policy_name, c["policy_names"])
        by_principal = any(_match(n, c["role_names"] if w == "role" else c["user_names"]) for w, n in unit.principals)
        if by_policy or by_principal:
            hits.append(c)
    return hits


def is_ceiling(inv: dict[str, Any], plan: Any, unit: Unit) -> bool:
    if unit.kind != "identity" or not any(_same(unit.policy_name, c) for c in inv.get("ceilings", [])):
        return False
    return cls._row_type(unit.address) == "aws_iam_policy" and not any(
        _same(pol, unit.policy_name) for _w, _p, pol in attachments(plan)
    )


def _prior_policy(rc: dict[str, Any]) -> Any:
    change = cls._dict(rc.get("change"))
    if change.get("actions") == ["update"]:
        return cls._parsed(cls._dict(change.get("before")).get("policy"))
    return None


def _changed_units(plan: Any, groups: dict[str, list[dict[str, Any]]], full: bool) -> dict[str, list[Unit]]:
    out: dict[str, list[Unit]] = {}
    for address, members in groups.items():
        kind = cls._row_type(address)
        units: list[Unit] = []
        for rc in members:
            inst = core.normalise_address(str(rc.get("address", "")))
            change = cls._dict(rc.get("change"))
            after = cls._dict(change.get("after"))
            doc = cls.checked_document(plan, rc)
            if kind in ATTACH_TYPES and doc is None and is_fresh(rc):
                shape = "managed" if is_managed(plan, rc) else "unknown"
                units.append(Unit(inst, shape, None, _unit_names(plan, inst, kind, None, after)))
            elif doc is None and full and kind == "aws_iam_role" and "delete" not in (change.get("actions") or []):
                trust = (
                    None
                    if cls._dict(change.get("after_unknown")).get("assume_role_policy")
                    else after.get("assume_role_policy")
                )
                if cls._parsed(trust) is not None:
                    units.append(Unit(inst, "trust", cls._parsed(trust), (None, [])))
            elif doc is not None and kind == "aws_iam_role":
                units.append(Unit(inst, "trust", doc, (None, [])))
            elif doc is not None:
                unit = Unit(inst, "identity", doc, _unit_names(plan, inst, kind, None, after))
                unit.prior = _prior_policy(rc) if kind in INLINE_OR_MANAGED else None
                units.append(unit)
        out[address] = units
    return out


def is_fresh(rc: dict[str, Any]) -> bool:
    change = cls._dict(rc.get("change"))
    actions = [str(a) for a in change.get("actions") or []]
    return "create" in actions or bool(change.get("importing")) or bool(rc.get("previous_address"))


def is_managed(plan: Any, rc: dict[str, Any]) -> bool:
    address = core.normalise_address(str(rc.get("address", "")))
    return is_fresh(rc) and cls.is_managed_arn(plan.value(address, "policy_arn"))


def _swept_units(plan: Any, groups: dict[str, list[dict[str, Any]]]) -> dict[str, list[Unit]]:
    """Prior-state documents of every address or instance the plan does not change, plus the unchanged trusts of kept roles."""
    out: dict[str, list[Unit]] = {}
    changed_instances = {str(rc.get("address", "")) for ms in groups.values() for rc in ms}
    for r in plan.prior:
        raw, kind, values = str(r.get("address", "")), str(r.get("type")), cls._dict(r.get("values"))
        address = core.normalise_address(raw)
        if raw in changed_instances or kind not in (*SWEPT_TYPES, *ATTACH_TYPES):
            continue
        if kind == "aws_iam_role":
            unit = Unit(address, "trust", cls._parsed(values.get("assume_role_policy")), (None, []))
        elif kind in ATTACH_TYPES:
            if not cls.is_managed_arn(values.get("policy_arn")):
                continue
            unit = Unit(address, "managed", None, _unit_names(plan, address, kind, values))
        else:
            unit = Unit(address, "identity", cls._parsed(values.get("policy")), _unit_names(plan, address, kind, values))
        if unit.doc is not None or unit.kind == "managed":
            out.setdefault(address, []).append(unit)
    return out


def build(plan: Any, groups: dict[str, list[dict[str, Any]]], full: bool) -> list[tuple[str, str, list[Unit]]]:
    """(address, row digest, units) per result; full mode adds the unchanged prior-state addresses."""
    changed = _changed_units(plan, groups, full)
    swept = _swept_units(plan, groups) if full else {}
    results = []
    for address, members in groups.items():
        units = changed[address] + (swept.pop(address, []) if full else [])
        if units or any(cls.checked_document(plan, m) is not None for m in members):
            results.append((address, cls.row_digest(plan, members), units))
    for address in sorted(swept):
        results.append((address, cls.group_digest([u.digest for u in swept[address]]), swept[address]))
    return results
