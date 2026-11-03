"""Actionable-first digest, inert counts, overflow and --fit-report (plan-review-digest-actionable-first)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.ci.terraform_plan_digest import (
    DIGEST_SIZE_CAP,
    TRUNCATION_MARKER,
    DigestOverflow,
    budget_for,
    build_digest,
    fit_report,
    main,
)

SENTINEL = "SENTINEL_VALUE_9137"


def _rc(
    rtype: str, actions: list[str], before=None, after=None, address: str | None = None, unknown=None, reason=None
) -> dict:
    entry = {
        "address": address or f"{rtype}.example",
        "type": rtype,
        "change": {"actions": actions, "before": before, "after": after, "after_unknown": unknown or {}},
    }
    if reason:
        entry["action_reason"] = reason
    return entry


def _realistic_plan(opaque: bool = True) -> dict:
    unknown = {"policy": True} if opaque else {}
    doc_type = "aws_iam_policy_document" if opaque else "aws_ssm_parameter"
    changes = [
        _rc("aws_lambda_function", ["update"], {"a": 1}, {"a": 2}, address="aws_lambda_function.reader"),
        _rc("aws_lambda_function", ["update"], {"a": 1}, {"a": 2}, address="aws_lambda_function.writer"),
        _rc("aws_lambda_function", ["update"], {"a": 1}, {"a": 2}, address="aws_lambda_function.other"),
        _rc("aws_iam_role_policy", ["update"], {"x": 1}, {"x": 2}, address="aws_iam_role_policy.ci_a", unknown=unknown),
        _rc("aws_iam_role_policy", ["update"], {"x": 1}, {"x": 2}, address="aws_iam_role_policy.ci_b", unknown=unknown),
        _rc(
            doc_type,
            ["read"],
            address=f"data.{doc_type}.a",
            reason="read_because_dependency_pending",
        ),
        _rc(
            doc_type,
            ["read"],
            address=f"data.{doc_type}.b",
            reason="read_because_dependency_pending",
        ),
    ]
    for i in range(139):
        kind = ["no-op"] if i % 7 else ["read"]
        changes.append(
            _rc(
                f"aws_inert_{i % 12:02d}",
                kind,
                {"k": SENTINEL},
                {"k": SENTINEL},
                address=f"aws_inert_{i % 12:02d}.{SENTINEL}_{i}",
            )
        )
    return {
        "resource_changes": changes,
        "output_changes": {"unchanged": {"actions": ["no-op"]}, "new": {"actions": ["create"]}},
    }


def test_realistic_plan_lists_every_actionable_entry_under_cap() -> None:
    plan = _realistic_plan()
    digest = build_digest(plan)
    assert len(digest.encode("utf-8")) < DIGEST_SIZE_CAP
    for name in ("reader", "writer", "other", "ci_a", "ci_b"):
        assert f".{name}" in digest
    assert "data.aws_iam_policy_document.a" in digest and "data.aws_iam_policy_document.b" in digest
    assert SENTINEL not in digest
    assert "146 resource change(s), 2 output change(s)." in digest
    assert "Listed: 7 actionable resource(s), 1 changed output(s)." in digest
    assert "Counted only: 119 no-op, 20 read, 1 unchanged output(s)." in digest
    assert "TRUNCATED" not in digest


def test_ordering_groups() -> None:
    plan = {
        "resource_changes": [
            _rc("t", ["read"], address="t.r", reason="read_because_dependency_pending"),
            _rc("t", ["update"], {"a": 1}, {"a": 2}, address="t.u"),
            _rc("t", ["create"], None, {"a": 1}, address="t.c"),
            _rc("t", ["delete", "create"], address="t.rep"),
            _rc("t", ["delete"], address="t.d"),
        ],
        "output_changes": {"o": {"actions": ["create"]}},
    }
    digest = build_digest(plan)
    order = [digest.index(f"- {a} ") for a in ("t.d", "t.rep", "t.c", "t.u", "t.r")]
    assert order == sorted(order)
    assert digest.index("- output o") > order[-1]


def test_read_with_reason_is_not_inert_but_plain_read_is() -> None:
    plan = {
        "resource_changes": [
            _rc("t", ["read"], address="t.with", reason="read_because_dependency_pending"),
            _rc("t", ["read"], address="t.plain"),
        ]
    }
    digest = build_digest(plan)
    assert "t.with" in digest and "t.plain" not in digest
    assert "- read by type: t x1" in digest


def test_inert_tail_has_types_only() -> None:
    plan = {"resource_changes": [_rc("t_a", ["no-op"], {"k": SENTINEL}, {"k": SENTINEL}, address=f"t_a.{SENTINEL}")] * 3}
    digest = build_digest(plan)
    assert SENTINEL not in digest
    assert "- no-op by type: t_a x3" in digest


def test_identical_lines_merge_with_count() -> None:
    plan = {"resource_changes": [_rc("t", ["update"], {"a": 1}, {"a": 2}, address=f't.y["k{i}"]') for i in range(3)]}
    assert "changed=[a] x3" in build_digest(plan)


def _fitting_head_bytes(plan: dict) -> int:
    return int(fit_report(plan).split(", ")[-1].split(" of ")[0])


@pytest.mark.parametrize("delta", [-2, -1, 0, 1, 2])
def test_marker_reservation_boundary_and_fit_report_agree(delta: int) -> None:
    plan = _realistic_plan(opaque=False)
    head = _fitting_head_bytes(plan)
    cap = head + len(TRUNCATION_MARKER.encode("utf-8")) + delta
    overflow = delta < 0
    report = fit_report(plan, cap=cap)
    if overflow:
        with pytest.raises(DigestOverflow):
            build_digest(plan, size_cap=cap)
        assert "OVERFLOW" in report
    else:
        digest = build_digest(plan, size_cap=cap)
        assert len(digest.encode("utf-8")) <= cap
        for name in ("reader", "writer", "other", "ci_a", "ci_b"):
            assert f".{name}" in digest
        assert "OVERFLOW" not in report


def test_fit_report_lines() -> None:
    plan = _realistic_plan(opaque=False)
    ok = fit_report(plan)
    assert ok.startswith("Reviewer digest: 7 actionable of 146 resource change(s), ")
    assert ok.endswith(f" of {DIGEST_SIZE_CAP} bytes.")
    over = fit_report(plan, cap=1000)
    assert f"over the {budget_for(1000)}-byte budget (cap 1000)" in over
    assert "STARVED: digest overflow" in over


def test_cli_overflow_exits_3_and_fit_report_exits_0(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch) -> None:
    plan = {"resource_changes": [_rc("t", ["update"], {"a": 1}, {"a": 2}, address=f"t.r{i:04d}") for i in range(2000)]}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    assert main([str(path)]) == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "terraform_plan_digest: digest overflow: actionable content is" in captured.err
    assert main(["--fit-report", str(path)]) == 0
    assert "Reviewer digest OVERFLOW" in capsys.readouterr().out
    path.write_text(json.dumps(_realistic_plan(opaque=False)))
    assert main(["--fit-report", str(path)]) == 0
    assert capsys.readouterr().out.startswith("Reviewer digest: 7 actionable")
    assert main([str(tmp_path / "missing.json")]) == 1


def test_realistic_opaque_plan_fit_report_is_the_opaque_line() -> None:
    assert fit_report(_realistic_plan()) == (
        "Reviewer digest OPAQUE: policies unknown at plan time: aws_iam_role_policy.ci_a, aws_iam_role_policy.ci_b; "
        "deferred documents: data.aws_iam_policy_document.a, data.aws_iam_policy_document.b. "
        "The merge-time review cannot read them: build ARNs from names or split the PR."
    )
