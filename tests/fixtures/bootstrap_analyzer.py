"""Builders and a fake aws CLI for the bootstrap analyzer and gate tests (PLAN-bootstrap-iam-analyzer-checks).

Synthetic plan JSON and an in-memory Access Analyzer. The account is a placeholder; no real ARN or id appears here.
"""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any

ACCT = "123456789012"
REGION = "eu-west-2"
FINDING_TEXT = "FINDING-DESCRIPTION-TEXT"
BOUNDARY = f"arn:aws:iam::{ACCT}:policy/agent-platform-github-ci-apply-boundary"
MANAGED = "arn:aws:iam::aws:policy/ReadOnlyAccess"
ZERO = "0" * 40


def doc(*statements: dict[str, Any]) -> str:
    return json.dumps({"Version": "2012-10-17", "Statement": list(statements)})


def allow(action: Any, resource: Any = "*", sid: str = "S") -> dict[str, Any]:
    return {"Sid": sid, "Effect": "Allow", "Action": action, "Resource": resource}


def rc(
    address: str, actions: list[str], after: dict[str, Any] | None, before: dict[str, Any] | None = None, **extra: Any
) -> dict:
    change = {
        "actions": actions,
        "before": before,
        "after": after,
        "after_unknown": extra.pop("unknown", {}),
        **extra.pop("change", {}),
    }
    return {"address": address, "mode": "managed", "type": address.split(".")[-2].split("[")[0], "change": change, **extra}


def policy_rc(name: str, document: str, before: str | None = None, key: str = "") -> dict:
    addr = f"aws_iam_policy.{name.replace('-', '_')}{key}"
    body = {"name": name, "policy": document}
    return rc(addr, ["update"] if before else ["create"], body, {"name": name, "policy": before} if before else None)


def inline_rc(kind: str, principal: str, document: str, **extra: Any) -> dict:
    who = "user" if "user" in kind else "role"
    return rc(
        f"{kind}.{principal.replace('-', '_')}", ["create"], {who: principal, "name": "inline", "policy": document}, **extra
    )


def attach_rc(kind: str, principal: str, policy_arn: str | None, actions: list[str] | None = None, **extra: Any) -> dict:
    who = "user" if "user" in kind else "role"
    after = {who: principal, **({"policy_arn": policy_arn} if policy_arn else {})}
    unknown = {} if policy_arn else {"policy_arn": True}
    return rc(f"{kind}.{principal.replace('-', '_')}_att", actions or ["create"], after, unknown=unknown, **extra)


def role_rc(name: str, trust: str, actions: list[str] | None = None, before_trust: str | None = None, **extra: Any) -> dict:
    after = {"name": name, "permissions_boundary": BOUNDARY, "assume_role_policy": trust, "tags": extra.pop("tags", None)}
    before = {**after, "assume_role_policy": before_trust or trust} if (actions or ["create"]) != ["create"] else None
    return rc(f"aws_iam_role.{name.replace('-', '_')}", actions or ["create"], after, before, **extra)


def plan(*changes: dict, prior: list[dict] | None = None, cfg: list[dict] | None = None) -> dict:
    return {
        "resource_changes": list(changes),
        "prior_state": {"values": {"root_module": {"resources": prior or []}}},
        "configuration": {"root_module": {"resources": cfg or []}},
    }


def prior(rtype: str, address: str, **values: Any) -> dict[str, Any]:
    return {"address": address, "type": rtype, "values": values}


def cfg_refs(address: str, **refs: list[str]) -> dict[str, Any]:
    return {"address": address, "expressions": {k: {"references": v} for k, v in refs.items()}}


def pass_analyzer(rows: list[dict[str, Any]], verdict: str = "PASS", mode: str = "changed") -> dict[str, Any]:
    results = [
        {
            "address": r["address"],
            "classes": [],
            "digest": r["digest"],
            "checks": [{"check": "validate-policy", "verdict": "PASS", "loc": ""}],
        }
        for r in rows
        if r["digest"]
    ]
    return {"schema_version": 1, "mode": mode, "verdict": verdict, "results": results}


def _statements(document: Any) -> list[dict[str, Any]]:
    return [s for s in (document.get("Statement") or []) if isinstance(s, dict)]


def _hit(a: str, b: str) -> bool:
    a, b = a.lower(), b.lower()
    return fnmatch.fnmatchcase(a, b) or fnmatch.fnmatchcase(b, a)


def _list(value: Any) -> list[str]:
    return value if isinstance(value, list) else [value]


class FakeAws:
    """RUN replacement: sts identity plus an in-memory accessanalyzer; every call is recorded for the tests."""

    def __init__(self, findings: dict[str, list[str]] | None = None, fail: str | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.findings = findings or {}
        self.fail = fail

    def __call__(self, args: list[str]) -> tuple[int, str, str]:
        if args[1] == "sts":
            return 0, f"{ACCT}\n", ""
        if self.fail:
            return 254, "", f"An error occurred ({self.fail}) when calling: {FINDING_TEXT}"
        flags: dict[str, str] = dict(zip(args[3::2], args[4::2], strict=False))
        docs = {
            k: json.loads(Path(v[len("file://") :]).read_text(encoding="utf-8"))
            for k, v in flags.items()
            if v.startswith("file://")
        }
        sub = args[2]
        access = json.loads(flags["--access"]) if "--access" in flags else []
        self.calls.append({"sub": sub, "docs": docs, "access": access})
        return 0, json.dumps(getattr(self, "_" + sub.replace("-", "_"))(docs, access)), ""

    def _validate_policy(self, docs: dict[str, Any], _access: list[Any]) -> dict[str, Any]:
        document = docs["--policy-document"]
        types = next((v for k, v in self.findings.items() if k in json.dumps(document)), [])
        return {
            "findings": [
                {
                    "findingType": t,
                    "findingDetails": FINDING_TEXT,
                    "locations": [{"path": [{"value": "Statement"}, {"index": 0}]}],
                }
                for t in types
            ]
        }

    def _check_access_not_granted(self, docs: dict[str, Any], access: list[Any]) -> dict[str, Any]:
        for i, stmt in enumerate(_statements(docs["--policy-document"])):
            if stmt.get("Effect") != "Allow":
                continue
            for want in access:
                actions = [a for a in _list(stmt.get("Action")) if any(_hit(a, w) for w in want["actions"])]
                wanted = want.get("resources") or ["*"]
                if actions and any(_hit(r, w) for r in _list(stmt.get("Resource")) for w in wanted):
                    return {
                        "result": "FAIL",
                        "reasons": [{"description": FINDING_TEXT, "statementIndex": i, "statementId": stmt.get("Sid")}],
                    }
        return {"result": "PASS"}

    def _check_no_new_access(self, docs: dict[str, Any], _access: list[Any]) -> dict[str, Any]:
        def pairs(document: Any) -> set[tuple[str, str]]:
            return {(a, r) for s in _statements(document) for a in _list(s.get("Action")) for r in _list(s.get("Resource"))}

        grown = pairs(docs["--new-policy-document"]) - pairs(docs["--existing-policy-document"])
        return {"result": "FAIL" if grown else "PASS"}

    def _check_no_public_access(self, docs: dict[str, Any], _access: list[Any]) -> dict[str, Any]:
        open_ = any(s.get("Principal") in ("*", {"AWS": "*"}) for s in _statements(docs["--policy-document"]))
        return {"result": "FAIL" if open_ else "PASS"}
