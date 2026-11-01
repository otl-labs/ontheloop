#!/usr/bin/env python3
"""Names-only PR comment for the trusted terraform/personal plan (Decision 238 cl.4).

Run by path with `python3 -I` from terraform-plan-trusted.yml. It reads `terraform show -json` output and prints a
markdown body of counts, normalised resource addresses, changed attribute NAMES and output NAMES. It never prints a
value, an instance key, plan text or terraform or provider diagnostic text. Stdlib only.

  report    --category CAT --head-sha SHA --run-url URL [--plan-json F --verdict V --fit-file F --plan-sha256 H
            --saved-plan yes|no --github-output F]
  --refusal CAT --head-sha SHA --run-url URL --overlay-file F --screen-file F   (pre-credential categories; needs no
            tfvars and validates its own body against a closed grammar before printing)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(_ROOT))

from scripts.ci import plan_names_core as core  # noqa: E402

HEADER = "### terraform/personal speculative plan (trusted, Decision 238)"
NO_SAVED_PLAN = "No saved plan: after merge, apply by a blank workflow_dispatch of terraform-apply-sandbox."
SAVED_PLAN = "This saved plan is applied at merge (apply-the-saved-plan path); a stale plan fails closed at merge."
FAILURE_CATEGORIES = (
    "OVERLAY_REFUSED",
    "SCREEN_REFUSED",
    "ASSUME_FAILED",
    "INIT_FAILED",
    "PLAN_FAILED",
    "BACKSTOP_REFUSED",
    "GUARD_ERROR",
    "PERSIST_FAILED",
)
REFUSAL_CATEGORIES = ("OVERLAY_REFUSED", "SCREEN_REFUSED", "ASSUME_FAILED")
_SAFE = re.compile(r"[^A-Za-z0-9_.\-]")
_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_URL = re.compile(r"https://github\.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+/actions/runs/[0-9]+(/attempts/[0-9]+)?")
_NAME = r"[A-Za-z0-9._-]+"
_PATH = r"[A-Za-z0-9._/-]+"
_OVERLAY_LINE = re.compile(rf"^OVERLAY_REFUSED ([a-z_]+)(?: ({_PATH}))?$")
_SCREEN_LINE = re.compile(rf"^REFUSED ([a-z_]+) ({_NAME}):([0-9]+)$")
_BODY_LINE = re.compile(
    rf"^(REFUSED (overlay:)?[a-z_]+ ({_PATH}|-):[0-9]+"
    r"|State: [A-Z_]+"
    r"|PR head `[0-9a-f]{40}` - \[run\]\(https://github\.com/[^ )]+\) - advisory and read-only: it never applies\."
    r")$"
)


def safe(text: str) -> str:
    return _SAFE.sub("?", core.normalise_address(text))


def _resource_rows(plan: dict[str, Any]) -> dict[str, Any]:
    rows: dict[str, dict[str, Any]] = {}
    counts = {"import": 0, "add": 0, "change": 0, "destroy": 0, "replace": 0}
    for rc in plan.get("resource_changes") or []:
        if rc.get("mode") == "data":
            continue
        change = rc.get("change") or {}
        actions = list(change.get("actions") or [])
        bucket = core.action_bucket(actions, bool(change.get("importing")))
        if bucket is None:
            continue
        if bucket in counts:
            counts[bucket] += 1
        if bucket == "replace":
            counts["add"] += 1
            counts["destroy"] += 1
        row = rows.setdefault(safe(str(rc.get("address", ""))), {"actions": set(), "names": set()})
        row["actions"].update(actions)
        row["names"].update(core.changed_names(change))
    return {"rows": rows, "counts": counts}


_ADDR = r"[A-Za-z0-9._\-\[\]*]+"
_OPAQUE_LINE = re.compile(
    rf"^Reviewer digest OPAQUE: policies unknown at plan time: ({_ADDR}(?:, {_ADDR})*|none); "
    rf"deferred documents: ({_ADDR}(?:, {_ADDR})*|none)\. "
    r"The merge-time review cannot read them: build ARNs from names or split the PR\.$"
)


def opaque_addresses(text: str) -> list[str]:
    m = _OPAQUE_LINE.match(text)
    if not m:
        return []
    return [a for group in m.groups() if group != "none" for a in group.split(", ")]


def fit_word(text: str) -> str:
    if text.startswith("Reviewer digest OPAQUE:"):
        return "OPAQUE"
    if "OVERFLOW" in text:
        return "OVERFLOW"
    return "FITS" if text.startswith("Reviewer digest:") else "UNKNOWN"


def render_plan(args: argparse.Namespace, plan: dict[str, Any] | None) -> tuple[str, str]:
    """Return (body, state)."""
    category = args.category
    lines = [HEADER, _head_line(args.head_sha, args.run_url)]
    state = category
    if category == "OK":
        if plan is None:
            raise ValueError("plan json required")
        info = _resource_rows(plan)
        outputs = core.changed_output_names(plan)
        state = "CHANGES" if info["rows"] or outputs else "NO_CHANGES"
    lines.append(f"State: {state}")
    if args.verdict in ("PASS", "BLOCKED") and category in ("OK", "PERSIST_FAILED"):
        lines.append(f"Predicted guard verdict: {args.verdict} (BLOCKED routes to tf-gated-apply at merge).")
    if plan is not None and category in ("OK", "GUARD_ERROR", "PERSIST_FAILED"):
        info = _resource_rows(plan)
        c = info["counts"]
        lines.append(
            f"{c['import']} to import, {c['add']} to add, {c['change']} to change, {c['destroy']} to destroy "
            f"({c['replace']} replaced)."
        )
        if info["rows"]:
            lines += ["", "| Resource | Action | Changed attributes |", "|---|---|---|"]
            for addr in sorted(info["rows"]):
                row = info["rows"][addr]
                names = ", ".join(sorted(_SAFE.sub("?", n) for n in row["names"]))
                lines.append(f"| {addr} | {','.join(sorted(_SAFE.sub('?', a) for a in row['actions']))} | {names} |")
        outs = [safe(n) for n in core.changed_output_names(plan)]
        if outs:
            lines += ["", "Changed outputs: " + ", ".join(outs)]
        drift = core.drift_addresses(plan, normalise=True)
        if drift:
            lines += ["", "Drift detected outside Terraform: " + ", ".join(f"`{safe(a)}`" for a in drift)]
    if args.fit_file:
        try:
            fit_text = Path(args.fit_file).read_text(encoding="utf-8").strip()
            lines.append("Reviewer digest fit: " + fit_word(fit_text))
            if fit_word(fit_text) == "OPAQUE":
                quoted = [f"`{safe(a)}`" for a in opaque_addresses(fit_text)]
                if quoted:
                    lines.append("Unknown at plan time: " + ", ".join(quoted))
        except OSError:
            lines.append("Reviewer digest fit: UNKNOWN")
    if args.plan_sha256 and _HEX64.fullmatch(args.plan_sha256):
        lines.append(f"Saved-plan sha256: `{args.plan_sha256}`")
    lines.append(SAVED_PLAN if args.saved_plan == "yes" and state in ("CHANGES", "NO_CHANGES") else NO_SAVED_PLAN)
    return "\n".join(lines) + "\n", state


def _head_line(head_sha: str, run_url: str) -> str:
    if not _HEX40.fullmatch(head_sha) or not _URL.fullmatch(run_url):
        raise ValueError("bad head sha or run url")
    return f"PR head `{head_sha}` - [run]({run_url}) - advisory and read-only: it never applies."


def render_refusal(category: str, head_sha: str, run_url: str, overlay_file: str, screen_file: str) -> str:
    if category not in REFUSAL_CATEGORIES:
        raise ValueError("not a pre-credential category")
    lines = [HEADER, _head_line(head_sha, run_url), f"State: {category}"]
    if category == "OVERLAY_REFUSED":
        for raw in _read_lines(overlay_file):
            m = _OVERLAY_LINE.match(raw)
            if m:
                lines.append(f"REFUSED overlay:{m.group(1)} {m.group(2) or '-'}:0")
    elif category == "SCREEN_REFUSED":
        for raw in _read_lines(screen_file):
            m = _SCREEN_LINE.match(raw)
            if m:
                lines.append(f"REFUSED {m.group(1)} {m.group(2)}:{m.group(3)}")
    lines.append(NO_SAVED_PLAN)
    body = "\n".join(lines) + "\n"
    validate_refusal_body(body)
    return body


def _read_lines(path: str) -> list[str]:
    try:
        return Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []


def validate_refusal_body(body: str) -> None:
    """Closed grammar: any line outside it fails closed."""
    for line in body.splitlines():
        if line in ("", HEADER, NO_SAVED_PLAN) or _BODY_LINE.match(line):
            continue
        raise ValueError("body line outside the closed grammar")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="terraform_personal_plan_report.py")
    ap.add_argument("--refusal", metavar="CATEGORY")
    ap.add_argument("--category", default="OK", choices=["OK", *FAILURE_CATEGORIES])
    ap.add_argument("--head-sha", required=True)
    ap.add_argument("--run-url", required=True)
    ap.add_argument("--plan-json")
    ap.add_argument("--verdict", default="")
    ap.add_argument("--fit-file")
    ap.add_argument("--plan-sha256", default="")
    ap.add_argument("--saved-plan", choices=["yes", "no"], default="no")
    ap.add_argument("--overlay-file", default="")
    ap.add_argument("--screen-file", default="")
    ap.add_argument("--github-output")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if not Path(core.__file__).resolve().is_relative_to(_ROOT):
        print("terraform_personal_plan_report: scripts package did not resolve under the repo root", file=sys.stderr)
        return 2
    try:
        if args.refusal:
            sys.stdout.write(render_refusal(args.refusal, args.head_sha, args.run_url, args.overlay_file, args.screen_file))
            return 0
        plan = None
        if args.plan_json:
            try:
                plan = json.loads(Path(args.plan_json).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                plan = None
            if not isinstance(plan, dict):
                plan = None
                if args.category == "OK":
                    args.category = "PLAN_FAILED"
        body, state = render_plan(args, plan)
    except ValueError:
        print("terraform_personal_plan_report: refused to render (fixed message)", file=sys.stderr)
        return 2
    sys.stdout.write(body)
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as fh:
            fh.write(f"state={state}\nverdict={args.verdict or 'NONE'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
