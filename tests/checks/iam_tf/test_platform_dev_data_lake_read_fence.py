"""Standing guard for PLAN-datalake-read-visibility's version-level read grant + DEP-13 fence in
terraform/bootstrap/platform_dev_policies.tf (PLAN-platform-iam-bootstrap-adopt, the durable owner).

Asserts grant and Deny SHAPES, not merely verb-string presence -- a text `grep` cannot see that
s3:GetObjectVersion is OBJECT-level (Resource must be an object-prefix ARN) while
s3:ListBucketVersions is BUCKET-level (Resource must be the bare bucket ARN, prefix-scopable only
via the s3:prefix condition key). A Deny listing ListBucketVersions against an object-prefix ARN
never matches that bucket-level action and is INERT; only a real IAM simulate call (VP step 10,
admin-only) proves that live, but this guard rejects the inert HCL shape at PR time.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_POLICY_FILE = _REPO_ROOT / "terraform" / "bootstrap" / "platform_dev_policies.tf"

_CONFIG_GRANT_SID = "DataLakeBucketConfigRead"
_LIST_GRANT_SID = "DataLakeVersionListRead"
_OBJECT_GRANT_SID = "DataLakeObjectVersionRead"
_OBJECT_DENY_SID = "DenyStateAndConvergenceVersionRead"
_LIST_DENY_SID = "DenyStateAndConvergenceVersionList"

_EXPECTED_CONFIG_ACTIONS = frozenset({"s3:GetLifecycleConfiguration", "s3:GetBucketVersioning"})
_EXPECTED_LIST_CONDITION_PREFIXES = frozenset(
    {
        "${local.ducklake_prod_data_prefix}/*",
        "${local.ducklake_smoke_data_prefix}/*",
    }
)
_EXPECTED_OBJECT_GRANT_RESOURCES = frozenset(
    {
        "${local.data_lake_bucket_arn}/${local.ducklake_prod_data_prefix}/*",
        "${local.data_lake_bucket_arn}/${local.ducklake_smoke_data_prefix}/*",
    }
)
_EXPECTED_FENCED_OBJECT_RESOURCES = frozenset(
    {
        "${local.data_lake_bucket_arn}/convergence/personal/*",
        "${local.data_lake_bucket_arn}/tfplan/personal/*",
        "${local.data_lake_bucket_arn}/tfstate/personal/*",
    }
)
_PINNED_LOCALS = {"ducklake_prod_data_prefix": "ducklake", "ducklake_smoke_data_prefix": "ducklake-neon-smoke"}
_STATE_PREFIXES = (
    "convergence/personal/",
    "convergence/personal/x",
    "tfplan/personal/",
    "tfplan/personal/x",
    "tfstate/personal/",
    "tfstate/personal/x",
    "tf",
)
_VERSION_READ_VERB_FAMILY = frozenset(
    {
        "s3:GetObjectVersion",
        "s3:GetObjectVersionAcl",
        "s3:GetObjectVersionTagging",
        "s3:GetObjectVersionAttributes",
    }
)


def _statement_blocks(text: str) -> dict[str, str]:
    """Split a Statement array into per-Sid text blocks, each running from its own
    `Sid = "..."` to the next one's (or end of file for the last)."""
    matches = list(re.finditer(r'Sid\s*=\s*"([^"]+)"', text))
    blocks: dict[str, str] = {}
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        blocks[m.group(1)] = text[start:end]
    return blocks


def _bracket_body(block: str, field: str) -> str | None:
    m = re.search(rf"{field}\s*=\s*\[(?P<body>.*?)\]", block, re.S)
    return m.group("body") if m else None


def _bracket_list(block: str, field: str) -> list[str]:
    body = _bracket_body(block, field)
    if body is None:
        return []
    return re.findall(r'"([^"]+)"', body)


def _is_bare_bucket_arn(block: str, field: str) -> bool:
    body = _bracket_body(block, field)
    return body is not None and body.strip() == "local.data_lake_bucket_arn"


def _effect(block: str) -> str | None:
    m = re.search(r'Effect\s*=\s*"([^"]+)"', block)
    return m.group(1) if m else None


def _condition_prefix_values(block: str, operator: str = "StringLike") -> list[str] | None:
    m = re.search(rf'Condition\s*=\s*\{{.*?\b{operator}\s*=\s*\{{.*?"s3:prefix"\s*=\s*\[(?P<body>.*?)\]', block, re.S)
    if not m:
        return None
    return re.findall(r'"([^"]+)"', m.group("body"))


def _check_config_grant(blocks: dict[str, str]) -> list[str]:
    sid = _CONFIG_GRANT_SID
    block = blocks.get(sid)
    if block is None:
        return [f"{sid} Sid is missing"]
    failed: list[str] = []
    if _effect(block) != "Allow":
        failed.append(f"{sid} Effect is {_effect(block)!r}, expected Allow")
    actions = set(_bracket_list(block, "Action"))
    if not _EXPECTED_CONFIG_ACTIONS <= actions:
        failed.append(f"{sid} Action {sorted(actions)} is missing one of {sorted(_EXPECTED_CONFIG_ACTIONS)}")
    if not _is_bare_bucket_arn(block, "Resource"):
        failed.append(f"{sid} Resource is not the bare bucket ARN")
    return failed


def _check_list_grant(blocks: dict[str, str]) -> list[str]:
    sid = _LIST_GRANT_SID
    block = blocks.get(sid)
    if block is None:
        return [f"{sid} Sid is missing"]
    failed: list[str] = []
    if _effect(block) != "Allow":
        failed.append(f"{sid} Effect is {_effect(block)!r}, expected Allow")
    actions = set(_bracket_list(block, "Action"))
    if actions != {"s3:ListBucketVersions"}:
        failed.append(f"{sid} Action is {sorted(actions)}, expected exactly s3:ListBucketVersions")
    if not _is_bare_bucket_arn(block, "Resource"):
        failed.append(
            f"{sid} Resource is not the bare bucket ARN -- s3:ListBucketVersions is BUCKET-level, "
            "prefix-scopable only via the s3:prefix condition key"
        )
    prefixes = _condition_prefix_values(block)
    if prefixes is None:
        failed.append(f"{sid} has no s3:prefix StringLike condition")
    elif set(prefixes) != _EXPECTED_LIST_CONDITION_PREFIXES:
        failed.append(
            f"{sid} s3:prefix condition is {sorted(prefixes)}, expected exactly "
            f"{sorted(_EXPECTED_LIST_CONDITION_PREFIXES)} -- admitting only one DuckLake prefix leaves the "
            "other unreadable"
        )
    return failed


def _check_object_grant(blocks: dict[str, str]) -> list[str]:
    sid = _OBJECT_GRANT_SID
    block = blocks.get(sid)
    if block is None:
        return [f"{sid} Sid is missing"]
    failed: list[str] = []
    if _effect(block) != "Allow":
        failed.append(f"{sid} Effect is {_effect(block)!r}, expected Allow")
    actions = set(_bracket_list(block, "Action"))
    if actions != {"s3:GetObjectVersion"}:
        failed.append(f"{sid} Action is {sorted(actions)}, expected exactly s3:GetObjectVersion")
    resources = set(_bracket_list(block, "Resource"))
    if resources != _EXPECTED_OBJECT_GRANT_RESOURCES:
        failed.append(
            f"{sid} Resource is {sorted(resources)}, expected exactly the two DuckLake object "
            f"prefixes {sorted(_EXPECTED_OBJECT_GRANT_RESOURCES)} -- never bucket-wide"
        )
    return failed


def _check_object_deny(blocks: dict[str, str]) -> list[str]:
    sid = _OBJECT_DENY_SID
    block = blocks.get(sid)
    if block is None:
        return [f"{sid} Sid is missing"]
    failed: list[str] = []
    if _effect(block) != "Deny":
        failed.append(f"{sid} Effect is {_effect(block)!r}, expected Deny")
    actions = set(_bracket_list(block, "Action"))
    if actions != _VERSION_READ_VERB_FAMILY:
        failed.append(
            f"{sid} Action is {sorted(actions)}, expected the whole version-read verb family "
            f"{sorted(_VERSION_READ_VERB_FAMILY)} -- a sibling verb granted later must not silently land "
            "outside the fence"
        )
    resources = set(_bracket_list(block, "Resource"))
    if resources != _EXPECTED_FENCED_OBJECT_RESOURCES:
        failed.append(f"{sid} Resource is {sorted(resources)}, expected exactly {sorted(_EXPECTED_FENCED_OBJECT_RESOURCES)}")
    return failed


def _state_prefix_failures(sid: str, prefixes: list[str], text: str) -> list[str]:
    failed: list[str] = []
    resolved: list[str] = []
    for value in prefixes:
        for name in re.findall(r"\$\{local\.(\w+)\}", value):
            m = re.search(rf'^\s*{name}\s*=\s*"([^"]*)"', text, re.M)
            if not m:
                failed.append(f"{sid} local {name} is not defined in the file")
                break
            if name in _PINNED_LOCALS and m.group(1) != _PINNED_LOCALS[name]:
                failed.append(f"{sid} local {name} is {m.group(1)!r}, pinned to {_PINNED_LOCALS[name]!r}")
            value = value.replace("${local." + name + "}", m.group(1))
        resolved.append(value)
    for state in _STATE_PREFIXES:
        if any(fnmatch.fnmatchcase(state, pattern) for pattern in resolved):
            failed.append(f"{sid} StringNotLike value matches state prefix {state!r}, so it would not be denied")
    return failed


def _check_list_deny(blocks: dict[str, str], locals_text: str) -> list[str]:
    sid = _LIST_DENY_SID
    block = blocks.get(sid)
    if block is None:
        return [f"{sid} Sid is missing"]
    failed: list[str] = []
    if _effect(block) != "Deny":
        failed.append(f"{sid} Effect is {_effect(block)!r}, expected Deny")
    actions = set(_bracket_list(block, "Action"))
    if actions != {"s3:ListBucketVersions"}:
        failed.append(f"{sid} Action is {sorted(actions)}, expected exactly s3:ListBucketVersions")
    if not _is_bare_bucket_arn(block, "Resource"):
        failed.append(
            f"{sid} Resource is not the bare bucket ARN -- an object-prefix Resource shape is "
            "INERT against the BUCKET-level s3:ListBucketVersions action"
        )
    if _condition_prefix_values(block, "StringLike") is not None:
        failed.append(f"{sid} carries a positive StringLike s3:prefix condition, expected StringNotLike")
    prefixes = _condition_prefix_values(block, "StringNotLike")
    if prefixes is None:
        failed.append(f"{sid} has no s3:prefix StringNotLike condition")
    elif set(prefixes) != _EXPECTED_LIST_CONDITION_PREFIXES:
        failed.append(
            f"{sid} s3:prefix condition is {sorted(prefixes)}, expected exactly the Allow's "
            f"{sorted(_EXPECTED_LIST_CONDITION_PREFIXES)}"
        )
    else:
        failed.extend(_state_prefix_failures(sid, prefixes, locals_text))
    return failed


def validate_fence_shapes(text: str) -> list[str]:
    """Validate the five statement SHAPES the read-visibility fence requires. Returns a list of
    failure strings; an empty list means every shape check passed."""
    blocks = _statement_blocks(text)
    return [
        *_check_config_grant(blocks),
        *_check_list_grant(blocks),
        *_check_object_grant(blocks),
        *_check_object_deny(blocks),
        *_check_list_deny(blocks, text),
    ]


_GOOD_FENCE_TEXT = """
  ducklake_prod_data_prefix  = "ducklake"
  ducklake_smoke_data_prefix = "ducklake-neon-smoke"

      {
        Sid      = "DataLakeBucketConfigRead"
        Effect   = "Allow"
        Action   = ["s3:GetLifecycleConfiguration", "s3:GetBucketVersioning"]
        Resource = [local.data_lake_bucket_arn]
      },
      {
        Sid      = "DataLakeVersionListRead"
        Effect   = "Allow"
        Action   = ["s3:ListBucketVersions"]
        Resource = [local.data_lake_bucket_arn]
        Condition = {
          StringLike = {
            "s3:prefix" = [
              "${local.ducklake_prod_data_prefix}/*",
              "${local.ducklake_smoke_data_prefix}/*",
            ]
          }
        }
      },
      {
        Sid    = "DataLakeObjectVersionRead"
        Effect = "Allow"
        Action = ["s3:GetObjectVersion"]
        Resource = [
          "${local.data_lake_bucket_arn}/${local.ducklake_prod_data_prefix}/*",
          "${local.data_lake_bucket_arn}/${local.ducklake_smoke_data_prefix}/*",
        ]
      },
      {
        Sid    = "DenyStateAndConvergenceVersionRead"
        Effect = "Deny"
        Action = [
          "s3:GetObjectVersion",
          "s3:GetObjectVersionAcl",
          "s3:GetObjectVersionTagging",
          "s3:GetObjectVersionAttributes",
        ]
        Resource = [
          "${local.data_lake_bucket_arn}/convergence/personal/*",
          "${local.data_lake_bucket_arn}/tfplan/personal/*",
          "${local.data_lake_bucket_arn}/tfstate/personal/*",
        ]
      },
      {
        Sid      = "DenyStateAndConvergenceVersionList"
        Effect   = "Deny"
        Action   = ["s3:ListBucketVersions"]
        Resource = [local.data_lake_bucket_arn]
        Condition = {
          StringNotLike = {
            "s3:prefix" = [
              "${local.ducklake_prod_data_prefix}/*",
              "${local.ducklake_smoke_data_prefix}/*",
            ]
          }
        }
      },
"""


def test_good_fixture_passes() -> None:
    assert validate_fence_shapes(_GOOD_FENCE_TEXT) == []


def test_real_tree_fence_shapes_pass() -> None:
    failed = validate_fence_shapes(_POLICY_FILE.read_text(encoding="utf-8"))
    assert failed == [], failed


def test_bucket_wide_object_version_grant_is_rejected() -> None:
    bad = _GOOD_FENCE_TEXT.replace(
        '"${local.data_lake_bucket_arn}/${local.ducklake_prod_data_prefix}/*",\n'
        '          "${local.data_lake_bucket_arn}/${local.ducklake_smoke_data_prefix}/*",',
        '"${local.data_lake_bucket_arn}/*",',
    )
    assert bad != _GOOD_FENCE_TEXT
    failed = validate_fence_shapes(bad)
    assert any(_OBJECT_GRANT_SID in f for f in failed), failed


def test_missing_object_deny_is_rejected() -> None:
    blocks = _statement_blocks(_GOOD_FENCE_TEXT)
    bad = _GOOD_FENCE_TEXT.replace(blocks[_OBJECT_DENY_SID], "")
    assert bad != _GOOD_FENCE_TEXT
    failed = validate_fence_shapes(bad)
    assert any(_OBJECT_DENY_SID in f for f in failed), failed


def test_single_prefix_list_condition_is_rejected() -> None:
    bad = _GOOD_FENCE_TEXT.replace(
        '"${local.ducklake_prod_data_prefix}/*",\n              "${local.ducklake_smoke_data_prefix}/*",',
        '"${local.ducklake_prod_data_prefix}/*",',
    )
    assert bad != _GOOD_FENCE_TEXT
    failed = validate_fence_shapes(bad)
    assert any(_LIST_GRANT_SID in f for f in failed), failed


def test_inert_object_prefix_list_deny_is_rejected() -> None:
    bad = _GOOD_FENCE_TEXT.replace(
        'Sid      = "DenyStateAndConvergenceVersionList"\n'
        '        Effect   = "Deny"\n'
        '        Action   = ["s3:ListBucketVersions"]\n'
        "        Resource = [local.data_lake_bucket_arn]",
        'Sid      = "DenyStateAndConvergenceVersionList"\n'
        '        Effect   = "Deny"\n'
        '        Action   = ["s3:ListBucketVersions"]\n'
        "        Resource = [\n"
        '          "${local.data_lake_bucket_arn}/tfstate/personal/*",\n'
        "        ]",
    )
    assert bad != _GOOD_FENCE_TEXT
    failed = validate_fence_shapes(bad)
    assert any(_LIST_DENY_SID in f for f in failed), failed


def test_old_stringlike_state_prefix_list_deny_is_rejected() -> None:
    old = (
        "StringLike = {\n"
        '            "s3:prefix" = [\n'
        '              "convergence/personal/*",\n'
        '              "tfplan/personal/*",\n'
        '              "tfstate/personal/*",\n'
        "            ]\n"
        "          }"
    )
    start = _GOOD_FENCE_TEXT.index("StringNotLike", _GOOD_FENCE_TEXT.index(_LIST_DENY_SID))
    end = _GOOD_FENCE_TEXT.index("}", _GOOD_FENCE_TEXT.index("]", start)) + 1
    bad = _GOOD_FENCE_TEXT[:start] + old + _GOOD_FENCE_TEXT[end:]
    failed = validate_fence_shapes(bad)
    assert any(_LIST_DENY_SID in f for f in failed), failed


def test_list_deny_set_differing_from_the_allow_is_rejected() -> None:
    start = _GOOD_FENCE_TEXT.index("StringNotLike")
    bad = _GOOD_FENCE_TEXT[:start] + _GOOD_FENCE_TEXT[start:].replace('"${local.ducklake_smoke_data_prefix}/*",', "", 1)
    assert bad != _GOOD_FENCE_TEXT
    failed = validate_fence_shapes(bad)
    assert any(_LIST_DENY_SID in f for f in failed), failed


def test_local_mutated_to_a_state_prefix_is_rejected() -> None:
    bad = _GOOD_FENCE_TEXT.replace(
        'ducklake_prod_data_prefix  = "ducklake"', 'ducklake_prod_data_prefix  = "tfstate/personal"'
    )
    assert bad != _GOOD_FENCE_TEXT
    failed = validate_fence_shapes(bad)
    assert any("pinned" in f for f in failed), failed
    assert any("matches state prefix" in f for f in failed), failed
