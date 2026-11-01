"""CI branch-role state fence (rec-4632, slice S1 of the trusted plan executor).

agent-platform-github-ci-branch is trusted from refs/heads/agent/*, so unreviewed pushes run under it. Its policy
document (source_policy_documents resolved transitively) must keep at least one unconditioned Deny of the five S3
object verbs on tfstate/*, tfplan/* and ci-rca-evidence/review-transcripts/*. An unresolvable document fails loud
(Decision 129 clause 4). Credential-free text parsing; the live-IAM proof is the post-deploy simulate in the plan.
"""

from __future__ import annotations

import re

from scripts.checks import _common, registry
from scripts.checks.iam_tf import validate_convergence_writer_isolation as _cwi
from scripts.checks.iam_tf import validate_invoke_implies_resolve as _vir

FENCE_SID = "DenyStatePlanAndReviewTranscriptIO"
FENCE_ACTIONS = frozenset({"s3:GetObject", "s3:GetObjectVersion", "s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion"})
FENCE_PREFIXES = ("tfstate/*", "tfplan/*", "ci-rca-evidence/review-transcripts/*")
_BUCKET_REF = "${aws_s3_bucket.data_lake.arn}"
_ROLE = "github_ci_branch"
_COMMENT_LINE_RE = re.compile(r"^[ \t]*(?:#|//).*$", re.MULTILINE)
_UNPARSED_RE = re.compile(r'\b(?:not_resources|not_actions|override_policy_documents)\b|\bdynamic\s+"')


def _is_fence(stmt: dict) -> bool:
    if stmt["effect"] != "Deny" or stmt["conditions"]:
        return False
    actions = set(stmt["actions"])
    if "s3:*" not in actions and not FENCE_ACTIONS <= actions:
        return False
    resources = set(_vir._QUOTED_RE.findall(stmt["resources_raw"]))
    return all(f"{_BUCKET_REF}/{p}" in resources for p in FENCE_PREFIXES)


def fence_problems(text: str) -> list[str]:
    text = _COMMENT_LINE_RE.sub("", text)
    docs = _cwi._parse_full_policy_documents(text)
    role_policy = _vir._parse_role_policy_map(text)
    doc_name = role_policy.get(_ROLE)
    if not doc_name:
        return [f"cannot resolve {_ROLE}'s policy document from the HCL"]
    block = next((m for m in _vir._DATA_BLOCK_RE.finditer(text) if m.group("name") == doc_name), None)
    if block and _UNPARSED_RE.search(_vir._extract_block(text, block.end() - 1)):
        return [
            f"{doc_name} uses not_resources, not_actions, override_policy_documents or dynamic blocks the check cannot parse"
        ]
    unresolved: list[str] = []
    statements = _vir._resolve_statements(doc_name, docs, unresolved=unresolved)
    if unresolved:
        return [f"{_ROLE} references unresolvable policy document(s) {sorted(set(unresolved))!r}"]
    if not any(_is_fence(s) for s in statements):
        return [
            (
                f"{_ROLE} has no unconditioned Deny of {sorted(FENCE_ACTIONS)} on {list(FENCE_PREFIXES)} "
                f"(expected statement {FENCE_SID})"
            )
        ]
    return []


@registry.register("validate_ci_branch_state_fence", owner="platform")
def validate_ci_branch_state_fence(failed: list[str]) -> None:
    """Fail unless github_ci_branch keeps the state / plan / review-transcript Deny (rec-4632)."""
    print("\n=== CI branch-role state fence (rec-4632) ===")
    personal_dir = _common.ROOT / _vir._PERSONAL_DIR_REL
    text = _vir._read_root_text(personal_dir)
    if not text:
        registry.skipped(f"no *.tf file readable under {personal_dir}")
        failed.append(f"ci-branch-state-fence: cannot read any *.tf file under {personal_dir}")
        print(f"  FAIL: cannot read any *.tf file under {personal_dir}")
        return
    registry.examined(len(list(personal_dir.glob("*.tf"))), unit="personal_tf_files")
    problems = fence_problems(text)
    for problem in problems:
        failed.append(f"ci-branch-state-fence: {problem}")
        print(f"  FAIL: {problem}")
    if not problems:
        print(f"  PASS: {_ROLE} carries {FENCE_SID}.")


if __name__ == "__main__":  # pragma: no cover
    _failed: list[str] = []
    validate_ci_branch_state_fence(_failed)
    for _f in _failed:
        print(f"  - {_f}")
    raise SystemExit(1 if _failed else 0)
