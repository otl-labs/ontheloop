#!/usr/bin/env python3
"""Gate of the terraform/github CI apply (Decision 248, PLAN-terraform-github-gated-apply).

Three subcommands, run with python3 -I by path from .github/workflows/terraform-github-apply.yml and
scripts/ci/terraform_github_apply.sh. Stdlib only. A row is {address, actions, changed, values}: values holds the
after-values of the ALLOWLIST attributes only (never an id, a description or any other value), so the preview, the
reviewed set and every printed line carry names, actions and allowlisted values only. Addresses drop instance keys
(plan_names_core.normalise_address), so a for_each or count resource makes a plan unreadable: it fails closed.

  preview        the read-only plan: a Markdown table with a GATE CHANGE banner per gate row, and the reviewed set
                 and has_changes to GITHUB_OUTPUT. An unnamed gate row stops it before writing them: a push run ends
                 neutral (exit 0, ::warning::, has_changes=false); any other run exits 1
  check          the admin plan against the reviewed set; prints a refusal marker and exits 1, or CHECKED / NOTHING
  probe-verdict  classifies the apply App's pending-deployment approval attempt as the session probe does
"""

from __future__ import annotations

import argparse
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
    sys.modules[name] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


_report = _load("terraform_github_plan_report")
_core = _report._core

GATE_TYPES = frozenset(
    {
        "github_repository_environment",
        "github_repository_environment_deployment_policy",
        "github_actions_repository_permissions",
        "github_workflow_repository_permissions",
    }
)
NON_GATE_TYPES = frozenset({"github_repository", "github_repository_ruleset"})
RULESET = "github_repository_ruleset"
_ADDRESS_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _scalar(value: Any) -> Any:
    return value if value is None or isinstance(value, (bool, int, str)) else None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first(value: Any) -> dict[str, Any]:
    return value[0] if isinstance(value, list) and value and isinstance(value[0], dict) else {}


def _environment(after: dict[str, Any]) -> dict[str, Any]:
    users = _first(after.get("reviewers")).get("users")
    policy = _first(after.get("deployment_branch_policy"))
    return {
        "environment": after.get("environment"),
        "can_admins_bypass": after.get("can_admins_bypass"),
        "prevent_self_review": after.get("prevent_self_review"),
        "reviewer_count": len(users) if isinstance(users, list) else 0,
        "protected_branches": policy.get("protected_branches"),
        "custom_branch_policies": policy.get("custom_branch_policies"),
    }


ALLOWLIST = {
    "github_repository_environment": _environment,
    "github_repository_environment_deployment_policy": lambda a: {
        "environment": a.get("environment"),
        "branch_pattern": a.get("branch_pattern"),
    },
    RULESET: lambda a: {"enforcement": a.get("enforcement")},
}


class Unreadable(Exception):
    pass


def _type(address: str) -> str:
    parts = address.split(".")
    return parts[-2] if len(parts) >= 2 else ""


def rows(plan: Any) -> list[dict[str, Any]]:
    if not isinstance(plan, dict) or not isinstance(plan.get("resource_changes"), list):
        raise Unreadable("plan JSON missing or malformed")
    out: dict[str, dict[str, Any]] = {}
    for rc in plan["resource_changes"]:
        if not isinstance(rc, dict) or rc.get("mode") == "data":
            continue
        change = _dict(rc.get("change"))
        actions = [str(a) for a in change.get("actions") or []]
        importing = bool(change.get("importing"))
        if _core.action_bucket(actions, importing) is None:
            continue
        address = _core.normalise_address(str(rc.get("address", "")))
        if address in out:
            raise Unreadable("two rows share one address")
        after = _dict(change.get("after"))
        extract = ALLOWLIST.get(_type(address))
        values = {k: _scalar(v) for k, v in extract(after).items()} if extract and after else {}
        out[address] = {
            "address": address,
            "actions": actions + (["import"] if importing else []),
            "changed": _core.changed_names(change),
            "values": values,
        }
    return [out[a] for a in sorted(out)]


def _bucket(row: dict[str, Any]) -> str | None:
    actions = [a for a in row["actions"] if a != "import"]
    return _core.action_bucket(actions, "import" in row["actions"])


def is_gate(row: dict[str, Any]) -> bool:
    return _type(row["address"]) not in NON_GATE_TYPES


def parse_gate_change(raw: str) -> list[str]:
    names = [x.strip() for x in raw.split(",") if x.strip()]
    if any(not _ADDRESS_RE.match(n) for n in names):
        raise Unreadable("gate_change holds a value that is not a resource address")
    return names


def table(plan_rows: list[dict[str, Any]], title: str) -> str:
    lines = [f"### {title}", ""]
    gates = [r["address"] for r in plan_rows if is_gate(r)]
    lines += [f"**GATE CHANGE**: `{a}` (must be named in gate_change)" for a in gates]
    if not plan_rows:
        return "\n".join(lines + ["No changes."]) + "\n"
    lines += ["", "| Resource | Action | Changed attributes | Allowlisted values |", "|---|---|---|---|"]
    for r in plan_rows:
        values = ", ".join(f"{k}={json.dumps(v)}" for k, v in sorted(r["values"].items()))
        lines.append(f"| {r['address']} | {','.join(r['actions'])} | {', '.join(r['changed'])} | {values} |")
    return "\n".join(lines) + "\n"


def preview(
    plan: Any, visibility: dict[str, str], gate_change: list[str], dispatched_input: bool, event_name: str = ""
) -> tuple[int, str, str]:
    """Returns (exit code, stdout, GITHUB_OUTPUT text)."""
    plan_rows = rows(plan)
    verdict = _report.classify(plan, visibility).verdict
    out = table(plan_rows, "terraform/github CI apply preview (read-only plan; the reviewed set)")
    unnamed = [r["address"] for r in plan_rows if is_gate(r) and r["address"] not in gate_change]
    if unnamed:
        names = ",".join(unnamed)
        if event_name == "push" and not dispatched_input:
            out += f"TF_GITHUB_APPLY_PREVIEW verdict={verdict} rows={len(plan_rows)} has_changes=false\n"
            out += (
                f"::warning::TF_GITHUB_APPLY_GATE_CHANGE_UNNAMED {names} push run ends without applying; "
                f"a dispatch of terraform-github-apply with gate_change={names} is owed\n"
            )
            return 0, out, "has_changes=false\n"
        return 1, out + f"TF_GITHUB_APPLY_GATE_CHANGE_UNNAMED {names}\n", ""
    has_changes = verdict not in ("NO_CHANGES", "VISIBILITY_GATED_ONLY") or dispatched_input
    reviewed = json.dumps(plan_rows, sort_keys=True, separators=(",", ":"))
    out += f"TF_GITHUB_APPLY_PREVIEW verdict={verdict} rows={len(plan_rows)} has_changes={str(has_changes).lower()}\n"
    return 0, out, f"reviewed={reviewed}\nhas_changes={str(has_changes).lower()}\n"


def _matches(row: dict[str, Any], reviewed: list[dict[str, Any]]) -> bool:
    return any(
        r.get("address") == row["address"]
        and r.get("actions") == row["actions"]
        and set(row["changed"]) <= set(r.get("changed") or [])
        and r.get("values") == row["values"]
        for r in reviewed
    )


def check(plan: Any, reviewed: Any, gate_change: list[str]) -> tuple[int, str]:
    plan_rows = rows(plan)
    if not isinstance(reviewed, list) or not all(isinstance(r, dict) for r in reviewed):
        raise Unreadable("reviewed set missing or malformed")
    refusals = []
    for r in plan_rows:
        if "delete" in r["actions"]:  # an import can carry a replace, and action_bucket reports it as an import
            refusals.append(f"TF_GITHUB_APPLY_DESTRUCTIVE {r['address']}")
        if _type(r["address"]) == RULESET:
            refusals.append(f"TF_GITHUB_APPLY_RULESET_REFUSED {r['address']}")
        if is_gate(r) and r["address"] not in gate_change:
            refusals.append(f"TF_GITHUB_APPLY_GATE_CHANGE_UNNAMED {r['address']}")
        if not _matches(r, reviewed):
            refusals.append(f"TF_GITHUB_APPLY_UNREVIEWED {r['address']} changed={','.join(r['changed'])}")
    present = {r["address"] for r in plan_rows}
    refusals += [f"TF_GITHUB_APPLY_GATE_CHANGE_STALE {a}" for a in gate_change if a not in present]
    if refusals:
        return 1, "\n".join(refusals) + "\n"
    if not plan_rows:
        return 0, "TF_GITHUB_APPLY_NOTHING\n"
    counts = {"add": 0, "change": 0, "import": 0, "other": 0}
    for r in plan_rows:
        bucket = str(_bucket(r))
        counts[bucket if bucket in counts else "other"] += 1
    return 0, "TF_GITHUB_APPLY_CHECKED " + " ".join(f"{k}={v}" for k, v in counts.items()) + "\n"


def classify_post(status: int, message: str, headers: dict[str, str], can_approve: bool | None) -> str:
    """scripts/session/deployment_approval_probe.py's _CLASSIFIERS, in their order."""
    msg = message.lower()
    if 200 <= status < 300:
        return "APPROVED"
    if 300 <= status < 400:
        return "PROBE_INVALID"
    if not headers.get("x-github-request-id"):
        return "PROXY_BLOCKED"
    if status == 401:
        return "UNAVAILABLE"
    if status in (403, 429) and ("rate limit" in msg or headers.get("x-ratelimit-remaining") == "0"):
        return "INCONCLUSIVE"
    if status == 403 and ("resource not accessible by integration" in msg or "x-accepted-github-permissions" in headers):
        return "TOKEN_LACKS_PERMISSION"
    if status in (403, 422) and can_approve is False:
        return "NOT_REVIEWER"
    return "INCONCLUSIVE"


def probe_verdict(
    status: int, message: str, headers: dict[str, str], can_approve: bool | None, pending_after: bool | None
) -> str:
    verdict = classify_post(status, message, headers, can_approve)
    if verdict == "APPROVED":
        return "ACCEPTED"
    if verdict in ("TOKEN_LACKS_PERMISSION", "NOT_REVIEWER") and pending_after is True:
        return "REFUSED"
    return "INCONCLUSIVE"


_PROBE_MARKERS = {
    "ACCEPTED": ("APPLY_APP_APPROVAL_ACCEPTED", 1),
    "REFUSED": ("APPLY_APP_APPROVAL_REFUSED", 0),
    "INCONCLUSIVE": ("APPLY_APP_PROBE_INCONCLUSIVE", 1),
}


def _tri(value: str) -> bool | None:
    return {"true": True, "false": False}.get(value.strip().lower())


def _read_json(path: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Unreadable(f"{path} unreadable") from exc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("preview")
    p.add_argument("--plan-json", required=True)
    p.add_argument("--visibility", required=True)
    p.add_argument("--gate-change", default="")
    p.add_argument("--dispatched-input", choices=["true", "false"], default="false")
    p.add_argument("--github-output", required=True)
    p.add_argument("--event-name", default="")
    c = sub.add_parser("check")
    c.add_argument("--plan-json", required=True)
    c.add_argument("--reviewed", required=True, help="the reviewed set as JSON text")
    c.add_argument("--gate-change", default="")
    c.add_argument("--summary")
    v = sub.add_parser("probe-verdict")
    v.add_argument("--status", required=True)
    v.add_argument("--message", default="")
    v.add_argument("--ratelimit-remaining", default="")
    v.add_argument("--accepted-permissions", default="")
    v.add_argument("--request-id", default="")
    v.add_argument("--can-approve", default="")
    v.add_argument("--pending-after", default="")
    args = ap.parse_args(argv)
    if args.cmd == "probe-verdict":
        headers = {
            k: val
            for k, val in (
                ("x-ratelimit-remaining", args.ratelimit_remaining),
                ("x-accepted-github-permissions", args.accepted_permissions),
                ("x-github-request-id", args.request_id),
            )
            if val
        }
        status = int(args.status) if args.status.isdigit() else 0
        verdict = probe_verdict(status, args.message, headers, _tri(args.can_approve), _tri(args.pending_after))
        marker, rc = _PROBE_MARKERS[verdict]
        print(f"{marker} post={classify_post(status, args.message, headers, _tri(args.can_approve))} status={status}")
        return rc
    try:
        gate_change = parse_gate_change(args.gate_change)
        if args.cmd == "preview":
            visibility = _report.load_visibility(args.visibility)
            rc, out, gh_out = preview(
                _read_json(args.plan_json), visibility, gate_change, args.dispatched_input == "true", args.event_name
            )
            sys.stdout.write(out)
            if rc == 0:
                with open(args.github_output, "a", encoding="utf-8") as fh:
                    fh.write(gh_out)
            return rc
        try:
            reviewed = json.loads(args.reviewed)
        except ValueError as exc:
            raise Unreadable("reviewed set unreadable") from exc
        plan = _read_json(args.plan_json)
        rc, out = check(plan, reviewed, gate_change)
        sys.stdout.write(out)
        if rc == 0 and args.summary:
            with open(args.summary, "a", encoding="utf-8") as fh:
                fh.write(table(rows(plan), "terraform/github CI apply: the admin plan this job applies"))
        return rc
    except Unreadable as exc:
        print(f"TF_GITHUB_APPLY_PLAN_UNREADABLE {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
