"""Mirror test for validate_policy_document_plan_time_refs (rec-4685, rec-4718)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.checks import _common, registry
from scripts.checks.iam_tf import _manifest
from scripts.checks.iam_tf.validate_invoke_implies_resolve import _read_root_text
from scripts.checks.iam_tf.validate_policy_document_plan_time_refs import (
    ALLOWED_REFERENCES,
    REQUIRED_DOCUMENTS,
    _documents,
    missing_required_documents,
    reference_problems,
    validate_policy_document_plan_time_refs,
)

_NAME_BUILT = '"arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_writer_function}"'


def _doc(resources: str, extra: str = "", name: str = "x") -> str:
    return (
        f'data "aws_iam_policy_document" "{name}" {{\n'
        f"  {extra}\n"
        "  statement {\n"
        '    sid       = "S"\n'
        '    effect    = "Allow"\n'
        '    actions   = ["lambda:InvokeFunction"]\n'
        f"    resources = [{resources}]\n"
        "  }\n"
        "}\n"
    )


def test_live_tree_passes() -> None:
    failed: list[str] = []
    with registry.outcome_scope("validate_policy_document_plan_time_refs"):
        validate_policy_document_plan_time_refs(failed)
        declaration = registry.pop_declaration()
    assert failed == []
    docs = _documents(_read_root_text(_common.ROOT / "terraform" / "personal"))
    assert declaration is not None
    assert declaration.kind == "examined"
    assert declaration.count == len(docs) > 0
    assert set(REQUIRED_DOCUMENTS) <= set(docs)


def test_allowlist_is_pinned() -> None:
    assert ALLOWED_REFERENCES == {
        "aws_s3_bucket.data_lake",
        "aws_s3_bucket.ducklake_catalog_dr",
        "aws_dynamodb_table.counters",
        "aws_sns_topic.alerts",
    }


def test_direct_lambda_reference_fails() -> None:
    problems = reference_problems(_doc("aws_lambda_function.x.arn"))
    assert len(problems) == 1
    assert "aws_lambda_function.x" in problems[0]


@pytest.mark.parametrize(
    "resources, extra",
    [
        ('"${aws_lambda_function.x.arn}:*"', ""),
        ("", "depends_on = [aws_lambda_function.x]"),
        ('aws_lambda_function.x["k"].arn', ""),
        ("aws_lambda_function.x[0].arn", ""),
        ("aws_lambda_function.x[*].arn", ""),
        ("neon_project.x.id", ""),
        ('"s3://bucket/key", aws_lambda_function.x.arn', ""),
        ('"a\\"b#c", aws_lambda_function.x.arn', ""),
        ("aws_s3_bucket.other.arn", ""),
    ],
)
def test_red_forms(resources: str, extra: str) -> None:
    assert len(reference_problems(_doc(resources, extra))) == 1


def test_reference_in_condition_values_fails() -> None:
    text = _doc('"*"').replace(
        '    resources = ["*"]\n',
        '    resources = ["*"]\n    condition {\n      test = "StringEquals"\n'
        '      variable = "aws:SourceArn"\n      values = [aws_lambda_function.x.arn]\n    }\n',
    )
    assert len(reference_problems(text)) == 1


def test_reference_in_inline_json_fails() -> None:
    text = (
        'data "aws_iam_policy_document" "x" {\n  source_policy_documents = [jsonencode({r = aws_lambda_function.x.arn})]\n}\n'
    )
    assert len(reference_problems(text)) == 1


def test_missing_required_document_reported() -> None:
    assert len(missing_required_documents(_doc(_NAME_BUILT, name="github_ci_branch"))) == len(REQUIRED_DOCUMENTS) - 1


@pytest.mark.parametrize(
    "resources, extra",
    [
        ("aws_s3_bucket.data_lake.arn", ""),
        ('"${aws_s3_bucket.data_lake.arn}/*"', ""),
        ("", "source_policy_documents = [data.aws_iam_policy_document.y.json]"),
        (_NAME_BUILT, ""),
        ('"arn:aws:s3:::${var.aws_region}"', ""),
        (_NAME_BUILT, "# aws_lambda_function.x.arn\n  // aws_lambda_function.x.arn\n  /* aws_lambda_function.x.arn */"),
    ],
)
def test_green_forms(resources: str, extra: str) -> None:
    assert reference_problems(_doc(resources, extra)) == []


def test_pre_change_pr_document_yields_one_problem_per_lambda() -> None:
    body = (
        "\n".join(
            f"      {ref},"
            for fn in ("ducklake_writer", "ducklake_reader")
            for ref in (f"aws_lambda_function.{fn}.arn", f'"${{aws_lambda_function.{fn}.arn}}:*"')
        )
        * 2
    )
    text = _doc("\n" + body + "\n", name="github_ci_pr")
    assert len(reference_problems(text)) == 2


def test_unreadable_root_fails_and_skips(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(_common, "ROOT", tmp_path)
    failed: list[str] = []
    with registry.outcome_scope("validate_policy_document_plan_time_refs"):
        validate_policy_document_plan_time_refs(failed)
        declaration = registry.pop_declaration()
    assert failed
    assert declaration is not None and declaration.kind == "skipped"


def test_registered_pre_and_full() -> None:
    names = {s.name for s in registry.pre_sequence() if s.kind == "check"}
    assert "validate_policy_document_plan_time_refs" in names
    entry = next(e for e in _manifest.ENTRIES if e.name == "validate_policy_document_plan_time_refs")
    assert entry.full_segment == "full_after_lint"


def test_bad_tree_appends_failures(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "terraform" / "personal"
    root.mkdir(parents=True)
    (root / "a.tf").write_text(_doc("aws_lambda_function.x.arn", name="github_ci_branch"), encoding="utf-8")
    monkeypatch.setattr(_common, "ROOT", tmp_path)
    failed: list[str] = []
    with registry.outcome_scope("validate_policy_document_plan_time_refs"):
        validate_policy_document_plan_time_refs(failed)
        registry.pop_declaration()
    assert any("aws_lambda_function.x" in f for f in failed)
    assert any("not found" in f for f in failed)
