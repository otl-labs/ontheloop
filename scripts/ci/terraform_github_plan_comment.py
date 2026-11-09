#!/usr/bin/env python3
"""Code-owned comment gate for the trusted terraform/github plan job (PLAN-trusted-plan-github-root PR 4, rec-4638).

Stdlib-only, run by path with python3 -I so nothing repo-controlled can resolve. Two modes:
  refusal --category CAT --head-sha SHA --run-url URL --overlay-file F --screen-file F
  assert-clean --values-file F    (body on stdin)
Messages are fixed text: nothing read from a file or from the body is echoed on a failure.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

HEADER = "### terraform/github speculative plan (trusted, Decision 238)"
CATEGORIES = (
    "CONFIG_MISSING",
    "OVERLAY_REFUSED",
    "SCREEN_REFUSED",
    "ASSUME_FAILED",
    "MINT_FAILED",
    "REVIEWER_LOOKUP_FAILED",
    "INIT_FAILED",
    "MIRROR_FAILED",
    "PLAN_FAILED",
    "BACKSTOP_REFUSED",
    "REPORT_FAILED",
)
_HEX40 = re.compile(r"[0-9a-f]{40}")
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
_TOKEN_SHAPE = re.compile(r"gh[pousr]_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,}")
_PEM = re.compile(r"-----BEGIN [A-Z ]*-----")


def _read_lines(path: str) -> list[str]:
    try:
        return Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []


def render_refusal(category: str, head_sha: str, run_url: str, overlay_file: str, screen_file: str) -> str:
    if category not in CATEGORIES:
        raise ValueError("unknown category")
    if not _HEX40.fullmatch(head_sha) or not _URL.fullmatch(run_url):
        raise ValueError("bad head sha or run url")
    lines = [
        HEADER,
        f"PR head `{head_sha}` - [run]({run_url}) - advisory and read-only: it never applies.",
        f"State: {category}",
    ]
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
    body = "\n".join(lines) + "\n"
    for line in body.splitlines():
        if line != HEADER and not _BODY_LINE.match(line):
            raise ValueError("body line outside the closed grammar")
    return body


def _whole(value: str, text: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])", text) is not None


def _reviewer_ids(path: str) -> list[int] | None:
    if not os.path.exists(path):
        return []
    try:
        ids = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        return None
    return ids


def assert_clean(body: str, values_file: str) -> str | None:
    """Return the name of the first rule the body breaks, or None when it is clean."""
    ids = _reviewer_ids(values_file)
    if ids is None:
        return "values-file"
    if _TOKEN_SHAPE.search(body):
        return "token-shape"
    if _PEM.search(body):
        return "pem-marker"
    for rule, env in (
        ("client-id", "APP_CLIENT_ID"),
        ("account-id", "AWS_ACCOUNT_ID"),
        ("role-id-part", "AWS_ROLE_ID_ACCOUNT_PART"),
    ):
        value = os.environ.get(env, "")
        if value and _whole(value, body):
            return rule
    if any(_whole(str(i), body) for i in ids):
        return "reviewer-id"
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="terraform_github_plan_comment.py")
    sub = ap.add_subparsers(dest="mode", required=True)
    ref = sub.add_parser("refusal")
    ref.add_argument("--category", required=True)
    ref.add_argument("--head-sha", required=True)
    ref.add_argument("--run-url", required=True)
    ref.add_argument("--overlay-file", default="")
    ref.add_argument("--screen-file", default="")
    clean = sub.add_parser("assert-clean")
    clean.add_argument("--values-file", required=True)
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if args.mode == "refusal":
        try:
            sys.stdout.write(render_refusal(args.category, args.head_sha, args.run_url, args.overlay_file, args.screen_file))
        except ValueError:
            print("terraform_github_plan_comment: refused to render (fixed message)", file=sys.stderr)
            return 2
        return 0
    broken = assert_clean(sys.stdin.read(), args.values_file)
    if broken is not None:
        print(f"terraform_github_plan_comment: body fails the {broken} rule", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
