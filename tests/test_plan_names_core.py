"""Names-only core shared by the Terraform plan reporters (PLAN-trusted-plan-cutover PR 2)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.ci import plan_names_core as core  # noqa: E402


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("aws_s3_bucket.a", "aws_s3_bucket.a"),
        ('aws_iam_role.r["secret-key"]', "aws_iam_role.r"),
        ("aws_iam_role.r[3]", "aws_iam_role.r"),
        ('module.m["k1"].aws_x.y["k2"]', "module.m.aws_x.y"),
        ('module.a["x"].module.b[0].aws_x.y', "module.a.module.b.aws_x.y"),
        ('aws_x.y["a]b"]', "aws_x.y"),
        ('aws_x.y["a\\"]b"]', "aws_x.y"),
        ('aws_x.y["a[b]"]', "aws_x.y"),
        ('data.aws_iam_policy_document.d["k"]', "data.aws_iam_policy_document.d"),
    ],
)
def test_normalise_address_strips_every_key(raw: str, expected: str) -> None:
    assert core.normalise_address(raw) == expected


def test_normalise_address_never_leaks_key_text() -> None:
    assert "hunter2" not in core.normalise_address('module.m["hunter2"].aws_x.y["hunter2"]')


@pytest.mark.parametrize(
    ("actions", "importing", "bucket"),
    [
        (["create"], False, "add"),
        (["update"], False, "change"),
        (["delete"], False, "destroy"),
        (["delete", "create"], False, "replace"),
        (["create", "delete"], False, "replace"),
        (["no-op"], False, None),
        (["read"], False, None),
        ([], False, None),
        (["no-op"], True, "import"),
        (["forget"], False, "other"),
    ],
)
def test_action_bucket(actions: list[str], importing: bool, bucket: str | None) -> None:
    assert core.action_bucket(actions, importing) == bucket


def test_changed_names_compares_values_but_returns_only_names() -> None:
    change = {
        "before": {"a": "old-secret", "b": 1, "c": "same"},
        "after": {"a": "new-secret", "b": 1, "c": "same", "d": "x"},
        "after_unknown": {"e": True, "f": {"g": [False, True]}, "h": False},
    }
    assert core.changed_names(change) == ["a", "d", "e", "f"]
    assert "secret" not in json.dumps(core.changed_names(change))


def test_drift_and_output_names() -> None:
    plan = {
        "resource_drift": [{"address": 'aws_x.y["k1"]'}, {"address": 'aws_x.y["k2"]'}, {"address": "aws_a.b"}],
        "output_changes": {"z": {"actions": ["update"]}, "q": {"actions": ["no-op"]}, "m": {"actions": ["create"]}},
    }
    assert core.drift_addresses(plan, normalise=True) == ["aws_a.b", "aws_x.y"]
    assert core.drift_addresses(plan) == ["aws_a.b", 'aws_x.y["k1"]', 'aws_x.y["k2"]']
    assert core.changed_output_names(plan) == ["m", "z"]


def test_github_reporter_counts_match_core() -> None:
    spec = importlib.util.spec_from_file_location("gh_report", ROOT / "scripts/ci/terraform_github_plan_report.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gh_report"] = mod
    spec.loader.exec_module(mod)
    plan = {
        "resource_changes": [
            {"address": "a.b", "type": "t", "change": {"actions": ["create"], "before": None, "after": {"n": 1}}},
            {
                "address": "a.c",
                "type": "t",
                "change": {"actions": ["delete", "create"], "before": {"n": 1}, "after": {"n": 2}},
            },
        ]
    }
    rep = mod.classify(plan, {})
    assert (rep.add, rep.destroy, rep.replace) == (2, 1, 1)
