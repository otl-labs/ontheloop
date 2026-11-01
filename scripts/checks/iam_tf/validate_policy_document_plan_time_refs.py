"""Credential-free invariant: terraform/personal policy documents never reference managed resources (rec-4685, rec-4718).

A data "aws_iam_policy_document" that references a managed resource (aws_lambda_function.x.arn) is deferred to apply
whenever that resource has a pending change, so the role policy shows as "(known after apply)" to the merge-time guard
and reviewer (rec-4718: sandbox apply REVISE on 00825368). Name the resource by a built string from a local instead.

ALLOWED_REFERENCES are tolerated because those buckets, tables and topics rarely change in place and the refresh-read,
branch-fence and writer-isolation verifiers key on their resource form. Residual: any in-place change to one of them
(a provider default_tags change, e.g. var.owner_email, updates tags_all on all four at once) still defers every CI
document that names it; the PR-time plan check planned separately owns that residual.

Filesystem-only regex scan (Decision 182); no terraform invocation (Decision 119).
"""

from __future__ import annotations

import re
import sys

from scripts.checks import _common, registry
from scripts.checks.iam_tf.validate_invoke_implies_resolve import (
    _DATA_BLOCK_RE,
    _PERSONAL_DIR_REL,
    _extract_block,
    _read_root_text,
)

ALLOWED_REFERENCES = frozenset(
    {
        "aws_s3_bucket.data_lake",
        "aws_s3_bucket.ducklake_catalog_dr",
        "aws_dynamodb_table.counters",
        "aws_sns_topic.alerts",
    }
)

REQUIRED_DOCUMENTS = (
    "github_ci_branch",
    "github_ci_pr",
    "github_ci_planner",
    "github_ci_deploy",
    "github_ci_trusted_plan",
    "ci_full_refresh_read",
    "ci_ssm_refresh_read",
)

_REF_RE = re.compile(r"(?<![\w.])((?:aws|neon)_\w+)\.(\w+)(?:\[[^\]]*\])?(?:\.\w+)?")


def _strip_comments(body: str) -> str:
    """Drop #, // and /* */ comments, honouring double-quoted strings (and \\" escapes inside them)."""
    out: list[str] = []
    i, n = 0, len(body)
    in_str = False
    while i < n:
        c = body[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(body[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
        elif c == '"':
            in_str = True
            out.append(c)
            i += 1
        elif c == "#" or body.startswith("//", i):
            while i < n and body[i] != "\n":
                i += 1
        elif body.startswith("/*", i):
            end = body.find("*/", i + 2)
            i = n if end == -1 else end + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _documents(text: str) -> dict[str, str]:
    return {m.group("name"): _extract_block(text, m.end() - 1) for m in _DATA_BLOCK_RE.finditer(text)}


def reference_problems(text: str) -> list[str]:
    """One problem per distinct (document, type.name) managed-resource reference outside ALLOWED_REFERENCES."""
    problems: list[str] = []
    for name, body in _documents(text).items():
        seen: set[str] = set()
        for m in _REF_RE.finditer(_strip_comments(body)):
            ref = f"{m.group(1)}.{m.group(2)}"
            if ref in ALLOWED_REFERENCES or ref in seen:
                continue
            seen.add(ref)
            problems.append(
                f"policy document {name!r} references {ref} - name it by a built string from a local so the document "
                "is readable at plan time (rec-4685)"
            )
    return problems


def missing_required_documents(text: str) -> list[str]:
    present = _documents(text)
    return [f"required policy document {name!r} not found" for name in REQUIRED_DOCUMENTS if name not in present]


@registry.register("validate_policy_document_plan_time_refs", owner="platform")
def validate_policy_document_plan_time_refs(failed: list[str]) -> None:
    """Fail on any non-allowlisted managed-resource reference in a policy document, or a missing required document."""
    print("\n=== Policy documents readable at plan time (rec-4685) ===")
    personal_dir = _common.ROOT / _PERSONAL_DIR_REL
    text = _read_root_text(personal_dir)
    if not text:
        registry.skipped(f"no *.tf file readable under {personal_dir}")
        failed.append(f"policy-document-plan-time-refs: cannot read any *.tf file under {personal_dir}")
        print(f"  FAIL: cannot read any *.tf file under {personal_dir}")
        return
    count = len(_documents(text))
    registry.examined(count, unit="policy_documents")
    problems = reference_problems(text) + missing_required_documents(text)
    for problem in problems:
        failed.append(f"policy-document-plan-time-refs: {problem}")
        print(f"  FAIL: {problem}")
    if not problems:
        print(f"  PASS: {count} policy documents carry no managed-resource reference outside the allowlist")


if __name__ == "__main__":  # pragma: no cover
    failures: list[str] = []
    validate_policy_document_plan_time_refs(failures)
    sys.exit(1 if failures else 0)
