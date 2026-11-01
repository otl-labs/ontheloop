"""Standing guard for PLAN-platformdev-plan-state-fence (rec-4635, absorbing rec-2768 and rec-4353).

Asserts the four Deny statements that fence PlatformDev off every Terraform state object, every saved
plan and the merge-time review transcripts in terraform/bootstrap/platform_dev_policies.tf, by parsing
the statement objects (Effect, Action set, Resource set, absence of a Condition key), never by raw
block substring: the explanatory comments name prefixes and fall inside neighbouring blocks.
"""

from __future__ import annotations

import re
from pathlib import Path

from scripts.checks.iam_tf._read_coverage import _locate_policy_statement_array, _split_top_level_objects

_REPO_ROOT = Path(__file__).resolve().parents[3]
_POLICY_FILE = _REPO_ROOT / "terraform" / "bootstrap" / "platform_dev_policies.tf"

_B = "${local.data_lake_bucket_arn}"
_READ = frozenset(
    {
        "s3:GetObject",
        "s3:GetObjectVersion",
        "s3:GetObjectVersionAcl",
        "s3:GetObjectVersionTagging",
        "s3:GetObjectVersionAttributes",
        "s3:GetObjectAttributes",
    }
)
_WRITE = frozenset({"s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion"})
_STATE_PLAN = frozenset({f"{_B}/tfstate/*", f"{_B}/tfplan/*"})
_TRANSCRIPTS = frozenset({f"{_B}/ci-rca-evidence/review-transcripts/*"})
_EXPECTED = {
    "DenyTerraformArtifactRead": (_READ, _STATE_PLAN),
    "DenyTerraformArtifactWrite": (_WRITE, _STATE_PLAN),
    "DenyReviewTranscriptRead": (_READ, _TRANSCRIPTS),
    "DenyReviewTranscriptWrite": (_WRITE, _TRANSCRIPTS),
}
_OPEN_PARENTS = frozenset({f"{_B}/*", f"{_B}/convergence/*", f"{_B}/ci-rca-evidence/*"})
_DEP13_COUNT = 4

_SID_RE = re.compile(r'\bSid\s*=\s*"([^"]*)"')
_EFFECT_RE = re.compile(r'\bEffect\s*=\s*"([^"]*)"')
_QUOTED_RE = re.compile(r'"([^"]*)"')


def _field_values(body: str, field: str) -> frozenset[str]:
    m = re.search(rf"\b{field}\s*=\s*\[(.*?)\]", body, re.DOTALL)
    if m:
        return frozenset(_QUOTED_RE.findall(m.group(1)))
    m = re.search(rf'\b{field}\s*=\s*"([^"]*)"', body)
    return frozenset({m.group(1)}) if m else frozenset()


def _statements(text: str) -> list[dict]:
    array_text = _locate_policy_statement_array(text, "aws_iam_role_policy", "platform_dev_runtime")
    if array_text is None:
        return []
    out = []
    for body in _split_top_level_objects(array_text):
        sid = _SID_RE.search(body)
        effect = _EFFECT_RE.search(body)
        out.append(
            {
                "sid": sid.group(1) if sid else None,
                "effect": effect.group(1) if effect else None,
                "actions": _field_values(body, "Action"),
                "resources": _field_values(body, "Resource"),
                "has_condition": re.search(r"\bCondition\s*=", body) is not None,
            }
        )
    return out


def validate_plan_state_fence(text: str) -> list[str]:
    """Problems with the four fence Denies in the policy file text. [] when the fence is intact."""
    stmts = _statements(text)
    if not stmts:
        return ["platform_dev_runtime statements not found"]
    problems: list[str] = []
    for sid, (actions, resources) in _EXPECTED.items():
        found = [s for s in stmts if s["sid"] == sid]
        if len(found) != 1:
            problems.append(f"{sid}: expected exactly one statement, found {len(found)}")
            continue
        s = found[0]
        if s["effect"] != "Deny":
            problems.append(f"{sid}: Effect must be Deny, got {s['effect']}")
        if s["has_condition"]:
            problems.append(f"{sid}: must carry no Condition")
        if s["actions"] != actions:
            problems.append(f"{sid}: Action set differs from the scoped set")
        if s["resources"] != resources:
            problems.append(f"{sid}: Resource set differs from the scoped set")
    for s in stmts:
        if s["effect"] == "Deny" and s["resources"] & _OPEN_PARENTS:
            problems.append(f"{s['sid']}: Deny on an agent-read parent prefix")
    dep13 = [s for s in stmts if (s["sid"] or "").startswith("DenyState")]
    if len(dep13) != _DEP13_COUNT:
        problems.append(f"DenyState* Sid count must stay {_DEP13_COUNT}, found {len(dep13)}")
    return problems


def _tree() -> str:
    return _POLICY_FILE.read_text(encoding="utf-8")


def _stmt_block(text: str, sid: str) -> tuple[int, int]:
    i = re.search(rf'Sid\s*=\s*"{sid}"', text).start()
    start = text.rindex("      {\n", 0, i)
    return start, text.index("\n      },", i) + len("\n      },")


def _replace_in(text: str, sid: str, old: str, new: str) -> str:
    s, e = _stmt_block(text, sid)
    block = text[s:e]
    assert old in block, (sid, old)
    return text[:s] + block.replace(old, new, 1) + text[e:]


def test_real_tree_passes() -> None:
    assert validate_plan_state_fence(_tree()) == []


def test_missing_policy_is_reported() -> None:
    assert validate_plan_state_fence("") == ["platform_dev_runtime statements not found"]


def test_sid_dropped() -> None:
    t = _tree()
    s, e = _stmt_block(t, "DenyReviewTranscriptWrite")
    problems = validate_plan_state_fence(t[:s] + t[e:])
    assert any("DenyReviewTranscriptWrite" in p for p in problems)


def test_resource_narrowed() -> None:
    t = _replace_in(_tree(), "DenyTerraformArtifactRead", f"{_B}/tfstate/*", f"{_B}/tfstate/personal/*")
    assert any("DenyTerraformArtifactRead: Resource" in p for p in validate_plan_state_fence(t))


def test_get_object_dropped_from_read_set() -> None:
    t = _replace_in(_tree(), "DenyReviewTranscriptRead", '"s3:GetObject",', "")
    assert any("DenyReviewTranscriptRead: Action" in p for p in validate_plan_state_fence(t))


def test_delete_object_version_dropped_from_write_set() -> None:
    t = _replace_in(_tree(), "DenyTerraformArtifactWrite", '"s3:DeleteObjectVersion"', "")
    assert any("DenyTerraformArtifactWrite: Action" in p for p in validate_plan_state_fence(t))


def test_effect_flipped_to_allow() -> None:
    t = _replace_in(_tree(), "DenyTerraformArtifactWrite", '"Deny"', '"Allow"')
    assert any("DenyTerraformArtifactWrite: Effect" in p for p in validate_plan_state_fence(t))


def test_condition_added() -> None:
    t = _replace_in(
        _tree(),
        "DenyReviewTranscriptWrite",
        "Effect",
        'Condition = { StringLike = { "aws:username" = ["x"] } }\n        Effect',
    )
    assert any("DenyReviewTranscriptWrite: must carry no Condition" in p for p in validate_plan_state_fence(t))


def test_new_denystate_sid_name() -> None:
    t = _tree().replace('"DenyReviewTranscriptRead"', '"DenyStateX"')
    problems = validate_plan_state_fence(t)
    assert any("DenyState* Sid count" in p for p in problems)


def test_deny_on_ci_rca_evidence_parent() -> None:
    t = _replace_in(
        _tree(),
        "DenyReviewTranscriptWrite",
        f"{_B}/ci-rca-evidence/review-transcripts/*",
        f"{_B}/ci-rca-evidence/*",
    )
    assert any("agent-read parent prefix" in p for p in validate_plan_state_fence(t))
