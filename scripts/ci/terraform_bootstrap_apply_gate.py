#!/usr/bin/env python3
"""Gate of the terraform/bootstrap CI apply (PLAN-bootstrap-ci-gated-apply).

Run with python3 -I by path from .github/workflows/terraform-bootstrap-apply.yml and scripts/ci/terraform_bootstrap_apply.sh.
Stdlib only; the classifier is terraform_bootstrap_apply_classify.py, the screen terraform_bootstrap_apply_screen.py.

  screen    the whole root, before any credential: prints SCREEN_OK or SCREEN_REFUSED <rule> <file>, exit 1 on a refusal
  preview   the read-only plan: a Markdown table, and reviewed, has_changes and verdict to GITHUB_OUTPUT. A plan holding a
            protected change ends CLOUDSHELL_REQUIRED: a push run exits 0 with a ::warning::, any other run exits 1
  check     the apply-time plan against the reviewed set: exit 0 only when every change is CI-writable and matches a
            reviewed row; prints SIMULATE_OWED when an applied row changes the apply role's grant surface
  prove     exit 0 only when the plan has no change
  pending   with GH_TOKEN, exit 1 when a queued, waiting or in-progress run of a terraform/personal apply exists
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_cls = _load("terraform_bootstrap_apply_classify")
_screen = _load("terraform_bootstrap_apply_screen")
Unreadable = _cls.Unreadable
DEPENDENT_WORKFLOWS = ("terraform-apply-sandbox.yml", "reconcile.yml")
PENDING_STATUSES = ("queued", "waiting", "in_progress")
IGNORED_EVENTS = frozenset({"pull_request", "pull_request_target"})
_CLOUDSHELL_OUTPUT = "has_changes=false\nverdict=CLOUDSHELL_REQUIRED\n"  # pragma: allowlist secret
_CHECK_KEYS = ("address", "actions", "allows", "resources", "flags", "sids")


def _cell(text: str) -> str:
    return re.sub(r"[\x00-\x1f|]", " ", text).replace("::", ": :")


def table(rows: list[dict[str, Any]], title: str) -> str:
    privileged = sum(1 for r in rows if r["flags"])
    lines = [f"### {title}", "", f"{len(rows)} changes; {privileged} privilege-affecting"]
    if not rows:
        return "\n".join(lines + ["", "No changes."]) + "\n"
    lines += ["", "| Resource | Action | Changed attributes | Flags | Allow actions gained | Resources | Protected |"]
    lines.append("|---|---|---|---|---|---|---|")
    for r in rows:
        name = r["address"] + (f" (x{r['count']})" if r["count"] > 1 else "")
        cells = [
            name,
            ",".join(r["actions"]),
            ", ".join(r["changed"]),
            ",".join(r["flags"]) + (f" [{','.join(r['sids'])}]" if r["sids"] else ""),
            ", ".join(r["allows"]),
            ", ".join(r["resources"]),
            "PROTECTED " + ",".join(r["protected"]) if r["protected"] else "",
        ]
        lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
    return "\n".join(lines) + "\n"


def verdict_of(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "NO_CHANGES"
    return "CLOUDSHELL_REQUIRED" if any(r["protected"] for r in rows) else "REVIEWABLE"


def reviewed_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: r[k] for k in (*_CHECK_KEYS, "changed")} for r in rows]


def preview(plan: Any, event_name: str = "") -> tuple[int, str, str]:
    """(exit code, stdout, GITHUB_OUTPUT text)."""
    rows = _cls.build_rows(_cls.Plan(plan))
    verdict = verdict_of(rows)
    out = table(rows, "terraform/bootstrap CI apply preview (read-only plan; the reviewed set)")
    out += f"BOOTSTRAP_APPLY_PREVIEW verdict={verdict} rows={len(rows)}\n"
    if verdict == "CLOUDSHELL_REQUIRED":
        count = sum(1 for r in rows if r["protected"])
        out += f"::warning::CLOUDSHELL_REQUIRED {count} protected rows; the operator applies this plan from CloudShell\n"
        return (0 if event_name == "push" else 1), out, _CLOUDSHELL_OUTPUT
    has_changes = verdict == "REVIEWABLE"
    reviewed = json.dumps(reviewed_rows(rows), sort_keys=True, separators=(",", ":"))
    return 0, out, f"reviewed={reviewed}\nhas_changes={str(has_changes).lower()}\nverdict={verdict}\n"


def _matches(row: dict[str, Any], reviewed: list[dict[str, Any]]) -> bool:
    return any(
        all(r.get(k) == row[k] for k in _CHECK_KEYS) and set(row["changed"]) <= set(r.get("changed") or []) for r in reviewed
    )


def check(plan: Any, reviewed: Any) -> tuple[int, str]:
    rows = _cls.build_rows(_cls.Plan(plan))
    if not isinstance(reviewed, list) or not all(isinstance(r, dict) for r in reviewed):
        raise Unreadable("reviewed set missing or malformed")
    refusals = []
    for r in rows:
        if r["protected"]:
            refusals.append(f"APPLY_REFUSED protected {r['address']} {','.join(r['protected'])}")
        elif not _matches(r, reviewed):
            refusals.append(f"APPLY_REFUSED unreviewed {r['address']} changed={','.join(r['changed'])}")
    if refusals:
        return 1, "\n".join(refusals) + "\n"
    out = f"CHECKED rows={len(rows)}\n"
    if any("NARROWS" in r["flags"] for r in rows):
        out += "NARROWS_PRESENT\nSIMULATE_OWED\n"
    return 0, out


def prove(plan: Any) -> tuple[int, str]:
    rows = _cls.build_rows(_cls.Plan(plan))
    return (0, "PROVE_OK\n") if not rows else (1, f"PROVE_REFUSED rows={len(rows)}\n")


def pending_runs(fetch: Any) -> list[tuple[str, str]]:
    """Run ids and statuses of the dependent workflows' queued, waiting and in-progress runs; fetch(workflow, status)."""
    found: list[tuple[str, str]] = []
    for workflow in DEPENDENT_WORKFLOWS:
        for status in PENDING_STATUSES:
            body = fetch(workflow, status)
            for run in body.get("workflow_runs", []) if isinstance(body, dict) else []:
                if isinstance(run, dict) and run.get("event") not in IGNORED_EVENTS:
                    found.append((str(run.get("id")), status))
    return found


def _rest_fetch(repo: str, token: str) -> Any:
    def fetch(workflow: str, status: str) -> Any:
        url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/runs?status={status}&per_page=50"
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        )  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8"))
        except (OSError, ValueError) as exc:
            raise Unreadable("dependent run list unreadable") from exc

    return fetch


def _read_json(path: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Unreadable(f"{path} unreadable") from exc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("screen")
    s.add_argument("--root", default=_screen.ROOT)
    p = sub.add_parser("preview")
    p.add_argument("--plan-json", required=True)
    p.add_argument("--github-output", required=True)
    p.add_argument("--event-name", default="")
    c = sub.add_parser("check")
    c.add_argument("--plan-json", required=True)
    c.add_argument("--reviewed", required=True, help="the reviewed set as JSON text")
    pr = sub.add_parser("prove")
    pr.add_argument("--plan-json", required=True)
    sub.add_parser("pending")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "screen":
            findings = _screen.screen_root(Path(args.root))
            for rule, name, line in findings:
                print(f"SCREEN_REFUSED {rule} {name}:{line}")
            if not findings:
                print("SCREEN_OK")
            return 1 if findings else 0
        if args.cmd == "pending":
            token, repo = os.environ.get("GH_TOKEN", ""), os.environ.get("GITHUB_REPOSITORY", "")
            if not token or not repo:
                raise Unreadable("GH_TOKEN or GITHUB_REPOSITORY unset")
            runs = pending_runs(_rest_fetch(repo, token))
            for run_id, status in runs:
                print(f"DEPENDENT_RUN_PENDING {run_id} {status}")
            return 1 if runs else 0
        plan = _read_json(args.plan_json)
        if args.cmd == "preview":
            rc, out, gh_out = preview(plan, args.event_name)
            sys.stdout.write(out)
            with open(args.github_output, "a", encoding="utf-8") as fh:
                fh.write(gh_out)
            return rc
        if args.cmd == "prove":
            rc, out = prove(plan)
        else:
            try:
                reviewed = json.loads(args.reviewed)
            except ValueError as exc:
                raise Unreadable("reviewed set unreadable") from exc
            rc, out = check(plan, reviewed)
        sys.stdout.write(out)
        return rc
    except Unreadable as exc:
        print(f"BOOTSTRAP_APPLY_UNREADABLE {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
