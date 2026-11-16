#!/usr/bin/env python3
"""IAM Access Analyzer checks of the terraform/bootstrap CI apply preview (PLAN-bootstrap-iam-analyzer-checks).

Run with python3 -I by path from the preview job, after the read-only plan and before the gate's preview. Stdlib only; the
aws CLI is the only client. Checks the plan's changed IAM documents (every document on a full sweep) against
config/iam-invariants.json and prints names and verdicts only: never a document, ARN, account, finding text or CLI stderr.
Exit 0 PASS, 1 REFUSED, 2 ANALYZER_UNREADABLE.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
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


sel = _load("terraform_bootstrap_apply_analyzer_select")
cls = sel.cls
Unreadable = cls.Unreadable
SWEEP_TRIGGERS = ("config/iam-invariants.json",)
SWEEP_PREFIXES = ("scripts/ci/terraform_bootstrap_apply_analyzer",)
SWEEP_FILES = ("scripts/ci/terraform_bootstrap_apply_classify.py", "scripts/ci/plan_names_core.py")
COMPARE_LIMIT = 300
ACCESS_CHUNK = 100
_SID_RE = re.compile(r"[A-Za-z0-9]{1,64}")
_ERR_RE = re.compile(r"[A-Za-z]+(?:Exception|Error)")


def run_cli(args: list[str]) -> tuple[int, str, str]:
    done = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    return done.returncode, done.stdout, done.stderr


RUN = run_cli


def fetch_changed(repo: str, token: str, before: str, head: str) -> list[str]:
    url = f"https://api.github.com/repos/{repo}/compare/{before}...{head}?per_page=100"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            body = json.loads(resp.read().decode("utf-8"))
        files = [str(f.get("filename")) for f in body["files"]]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Unreadable("compare") from exc
    if len(files) >= COMPARE_LIMIT:
        raise Unreadable("compare truncated")
    return files


def full_sweep_wanted(args: argparse.Namespace) -> bool:
    if args.full_sweep:
        return True
    if args.event_name != "push":
        return False
    if not args.before or set(args.before) == {"0"}:
        return True
    try:
        files = fetch_changed(
            os.environ.get("GITHUB_REPOSITORY", ""),
            os.environ.get("GH_TOKEN", ""),
            args.before,
            os.environ.get("GITHUB_SHA", ""),
        )
    except Unreadable:
        return True
    return any(f in SWEEP_TRIGGERS or f in SWEEP_FILES or f.startswith(SWEEP_PREFIXES) for f in files)


class Cli:
    """The aws CLI with one private temp file per document, deleted after the call; stderr is never kept."""

    def __init__(self) -> None:
        self.account = ""
        self.region = os.environ.get("AWS_REGION", "")

    def call(self, args: list[str], docs: dict[str, Any]) -> Any:
        paths: list[str] = []
        try:
            full = ["aws", "accessanalyzer", *args]
            for flag, doc in docs.items():
                fd, path = tempfile.mkstemp(prefix="iam-doc-", suffix=".json")
                paths.append(path)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(doc, fh)
                full += [flag, f"file://{path}"]
            rc, out, err = RUN(full)
        finally:
            for path in paths:
                Path(path).unlink(missing_ok=True)
        if rc != 0:
            match = _ERR_RE.search(err or "")
            raise Unreadable(match.group(0) if match else "cli_error")
        try:
            return json.loads(out)
        except ValueError as exc:
            raise Unreadable("cli_error") from exc

    def identity(self) -> None:
        rc, out, err = RUN(["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"])
        if rc != 0 or not out.strip():
            raise Unreadable("cli_error")
        self.account = out.strip()


def _loc(doc: dict[str, Any], index: Any) -> str:
    if not isinstance(index, int):
        return ""
    stmts = doc.get("Statement")
    sid = (
        stmts[index].get("Sid")
        if isinstance(stmts, list) and 0 <= index < len(stmts) and isinstance(stmts[index], dict)
        else None
    )
    if isinstance(sid, str) and _SID_RE.fullmatch(sid) and not re.search(r"[0-9]{12}", sid):
        return f"sid={sid}"
    return f"index={index}"


def _fill(value: str, cli: Cli) -> str:
    return value.replace("{account}", cli.account).replace("{region}", cli.region)


def _chunks(row: dict[str, Any], cli: Cli) -> list[list[dict[str, Any]]]:
    actions = row["actions"]
    resources = [_fill(r, cli) for r in row.get("resources", [])]
    out = []
    for i in range(0, len(actions), ACCESS_CHUNK):
        access: dict[str, Any] = {"actions": actions[i : i + ACCESS_CHUNK]}
        if resources:
            access["resources"] = resources
        out.append([access])
    return out


def _validate(cli: Cli, unit: Any, block: list[str]) -> tuple[str, str]:
    body = cli.call(
        ["validate-policy", "--policy-type", "IDENTITY_POLICY", "--output", "json"], {"--policy-document": unit.doc}
    )
    findings = body.get("findings")
    if not isinstance(findings, list):
        raise Unreadable("cli_error")
    for f in findings:
        if isinstance(f, dict) and f.get("findingType") in block:
            path = [p for loc in f.get("locations") or [] for p in (loc.get("path") or []) if isinstance(p, dict)]
            index = next((p["index"] for p in path if "index" in p), None)
            return "FAIL", _loc(unit.doc, index)
    return "PASS", ""


def _never(cli: Cli, unit: Any, row: dict[str, Any]) -> tuple[str, str]:
    for access in _chunks(row, cli):
        body = cli.call(
            [
                "check-access-not-granted",
                "--policy-type",
                "IDENTITY_POLICY",
                "--access",
                json.dumps(access),
                "--output",
                "json",
            ],
            {"--policy-document": unit.doc},
        )
        if body.get("result") not in ("PASS", "FAIL"):
            raise Unreadable("cli_error")
        if body["result"] == "FAIL":
            reasons = [r for r in body.get("reasons") or [] if isinstance(r, dict)]
            return "FAIL", _loc(unit.doc, reasons[0].get("statementIndex") if reasons else None)
    return "PASS", ""


def _simple(cli: Cli, args: list[str], docs: dict[str, Any]) -> str:
    result = cli.call([*args, "--output", "json"], docs).get("result")
    if result not in ("PASS", "FAIL"):
        raise Unreadable("cli_error")
    return str(result)


def unit_checks(
    cli: Cli, inv: dict[str, Any], plan: Any, unit: Any, cache: dict[Any, tuple[str, str]]
) -> list[dict[str, str]]:
    """The checks of one document as {check, verdict, loc}; a verdict is shared by every address with the same pair."""
    checks: list[dict[str, str]] = []

    def run(check: str, key: Any, fn: Any) -> None:
        if key not in cache:
            cache[key] = fn()
        checks.append({"check": check, "verdict": cache[key][0], "loc": cache[key][1]})

    if unit.kind == "managed":
        classes = sel.classes_of(inv, unit)
        hit = (
            any(c["id"] == "session" for c in classes) and bool(unit.principals) or any(n is None for _w, n in unit.principals)
        )
        checks.append({"check": "managed-on-session", "verdict": "FAIL" if hit else "UNCHECKED", "loc": ""})
        return checks
    if unit.kind == "trust":
        run(
            "no-public-access",
            ("trust", unit.digest),
            lambda: (
                _simple(
                    cli,
                    ["check-no-public-access", "--resource-type", "AWS::IAM::AssumeRolePolicyDocument"],
                    {"--policy-document": unit.doc},
                ),
                "",
            ),
        )
        return checks
    ceiling = sel.is_ceiling(inv, plan, unit)
    classes = [] if ceiling else sel.classes_of(inv, unit)
    ids = tuple(sorted(c["id"] for c in classes)) or (("boundary",) if ceiling else ("other",))
    block = sorted({b for i in ids for b in inv["validate_policy_block"].get(i, [])})
    run("validate-policy", ("validate", unit.digest, tuple(block)), lambda: _validate(cli, unit, block))
    for c in classes:
        for row in c["never"]:
            run(
                f"never:{c['id']}:{row['id']}",
                ("never", unit.digest, c["id"], row["id"]),
                lambda row=row: _never(cli, unit, row),
            )
    if unit.prior is None:
        checks.append({"check": "no-new-access", "verdict": "NEW", "loc": ""})
    else:
        docs = {"--existing-policy-document": unit.prior, "--new-policy-document": unit.doc}
        prior_digest = cls.document_digest(unit.prior)
        run(
            "no-new-access",
            ("widen", unit.digest, prior_digest),
            lambda: (
                "WIDENS"
                if _simple(cli, ["check-no-new-access", "--policy-type", "IDENTITY_POLICY"], docs) == "FAIL"
                else "PASS",
                "",
            ),
        )
    return checks


def _safe(address: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.\-]", "_", address)


def _verdict(checks: list[dict[str, str]]) -> str:
    order = ("FAIL", "WIDENS", "UNCHECKED", "NEW", "PASS")
    return next((v for v in order if any(c["verdict"] == v for c in checks)), "PASS")


def analyse(plan_json: Any, inv: dict[str, Any], full: bool, cli: Cli) -> dict[str, Any]:
    plan = cls.Plan(plan_json)
    cli.identity()
    cache: dict[Any, tuple[str, str]] = {}
    results = []
    for address, digest, units in sel.build(plan, cls.kept_groups(plan), full):
        checks = [c for u in units for c in unit_checks(cli, inv, plan, u, cache)]
        classes = sorted({c["id"] for u in units if u.kind == "identity" for c in sel.classes_of(inv, u)})
        results.append({"address": address, "classes": classes, "digest": digest, "checks": checks})
    refused = any(c["verdict"] == "FAIL" for r in results for c in r["checks"])
    return {
        "schema_version": 1,
        "mode": "full" if full else "changed",
        "verdict": "REFUSED" if refused else "PASS",
        "results": results,
    }


def render(report: dict[str, Any]) -> str:
    lines = []
    counts = {"FAIL": 0, "WIDENS": 0, "UNCHECKED": 0}
    for r in report["results"]:
        for c in r["checks"]:
            tail = f" {c['loc']}" if c["verdict"] == "FAIL" and c["loc"] else ""
            lines.append(f"ANALYZER {_safe(r['address'])} {c['check']} {c['verdict']}{tail}")
        worst = _verdict(r["checks"])
        if worst in counts:
            counts[worst] += 1
    lines.append(
        f"ANALYZER_SUMMARY verdict={report['verdict']} mode={report['mode']} documents={len(report['results'])} "
        f"fail={counts['FAIL']} widens={counts['WIDENS']} unchecked={counts['UNCHECKED']}"
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan-json", required=True)
    ap.add_argument("--invariants", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--event-name", default="")
    ap.add_argument("--before", default="")
    ap.add_argument("--full-sweep", action="store_true")
    args = ap.parse_args(argv)
    try:
        try:
            plan_json = json.loads(Path(args.plan_json).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise Unreadable("plan") from exc
        inv = sel.load_invariants(args.invariants)
        report = analyse(plan_json, inv, full_sweep_wanted(args), Cli())
    except Unreadable as exc:
        print(f"ANALYZER_UNREADABLE {str(exc).split()[0] if str(exc) else 'cli_error'}")
        return 2
    Path(args.out).write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    sys.stdout.write(render(report))
    return 1 if report["verdict"] == "REFUSED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
