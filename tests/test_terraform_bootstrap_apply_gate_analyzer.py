"""The bootstrap gate with analyzer results, and the classifier's document digests (PLAN-bootstrap-iam-analyzer-checks VP 4).

Synthetic plan JSON and analyzer results (tests/fixtures/bootstrap_analyzer.py); no real ARN or account id appears here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.ci import terraform_bootstrap_apply_classify as cls
from scripts.ci import terraform_bootstrap_apply_gate as gate
from tests.fixtures.bootstrap_analyzer import (
    ACCT,
    MANAGED,
    allow,
    attach_rc,
    doc,
    pass_analyzer,
    plan,
    policy_rc,
    prior,
    rc,
    role_rc,
)

GRANT = doc(allow("logs:Describe*", sid="One"))
TRUST = doc({"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"})
ARN = f"arn:aws:iam::{ACCT}:policy/"


def rows_of(p: dict) -> list[dict[str, Any]]:
    return cls.build_rows(cls.Plan(p))


def refused(rows: list[dict[str, Any]]) -> dict[str, Any]:
    report = pass_analyzer(rows, verdict="REFUSED")
    report["results"][0]["checks"] = [{"check": "never:session:s3_objects", "verdict": "FAIL", "loc": "sid=Leak"}]
    return report


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_a_pass_keeps_todays_verdicts(event: str) -> None:
    p = plan(role_rc("agent-platform-new", TRUST))
    rc_, text, out = gate.preview(p, pass_analyzer(rows_of(p)), event)
    assert rc_ == 0 and "verdict=REVIEWABLE" in out and "has_changes=true" in out and "| PASS |" in text


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_a_refused_verdict_refuses_on_every_event(event: str) -> None:
    p = plan(policy_rc("agent-platform-a", GRANT))
    rc_, text, out = gate.preview(p, refused(rows_of(p)), event)
    assert rc_ == 1 and "has_changes=false" in out and "verdict=ANALYZER_REFUSED" in out
    assert "::error::ANALYZER_REFUSED" in text and "FAIL never:session:s3_objects sid=Leak" in text and "reviewed=" not in out


def test_a_refused_verdict_with_a_protected_row_is_cloudshell_required() -> None:
    p = plan(policy_rc("agent-platform-a", GRANT), role_rc("PlatformAdmin", TRUST))
    rc_, text, out = gate.preview(p, refused(rows_of(p)), "push")
    assert (
        rc_ == 1
        and "verdict=CLOUDSHELL_REQUIRED" in out
        and "::error::ANALYZER_REFUSED" in text
        and "has_changes=false" in out
    )


@pytest.mark.parametrize("break_it", ["missing", "stale", "malformed", "no_verdict"])
def test_a_missing_stale_or_malformed_result_is_unreadable(break_it: str) -> None:
    p = plan(policy_rc("agent-platform-a", GRANT))
    report: Any = pass_analyzer(rows_of(p))
    if break_it == "missing":
        report["results"] = []
    elif break_it == "stale":
        report["results"][0]["digest"] = "0" * 64
    elif break_it == "malformed":
        report = {"verdict": "PASS", "results": "x"}
    else:
        del report["verdict"]
    with pytest.raises(gate.Unreadable):
        gate.preview(p, report, "")


def test_the_cli_requires_the_analyzer_json_and_reports_unreadable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = plan(policy_rc("agent-platform-a", GRANT))
    plan_path, out = tmp_path / "plan.json", tmp_path / "gh"
    plan_path.write_text(json.dumps(p), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        gate.main(["preview", "--plan-json", str(plan_path), "--github-output", str(out)])
    assert exc.value.code == 2
    missing = [
        "preview",
        "--plan-json",
        str(plan_path),
        "--analyzer-json",
        str(tmp_path / "none.json"),
        "--github-output",
        str(out),
    ]
    assert gate.main(missing) == 1 and "BOOTSTRAP_APPLY_UNREADABLE" in capsys.readouterr().out
    analyzer = tmp_path / "analyzer.json"
    analyzer.write_text(json.dumps(pass_analyzer(rows_of(p))), encoding="utf-8")
    assert gate.main([*missing[:4], str(analyzer), *missing[5:]]) == 0 and "has_changes=true" in out.read_text(
        encoding="utf-8"
    )


def test_reviewed_rows_carry_the_digest_and_check_refuses_a_changed_document() -> None:
    p = plan(policy_rc("agent-platform-a", GRANT))
    reviewed = gate.reviewed_rows(rows_of(p))
    assert reviewed[0]["digest"] == cls.document_digest(json.loads(GRANT)) and gate.check(p, reviewed)[0] == 0
    changed = plan(
        policy_rc("agent-platform-a", doc(allow("logs:Describe*", sid="One"), allow("logs:GetLogEvents", sid="Two")))
    )
    code, out = gate.check(changed, reviewed)
    assert code == 1 and "APPLY_REFUSED unreviewed aws_iam_policy.agent_platform_a" in out


def test_a_full_sweep_section_lists_fails_outside_the_changed_rows() -> None:
    p = plan(policy_rc("agent-platform-a", GRANT))
    report = pass_analyzer(rows_of(p), mode="full")
    report["results"].append(
        {
            "address": "aws_iam_policy.old",
            "classes": [],
            "digest": "",
            "checks": [{"check": "validate-policy", "verdict": "FAIL", "loc": "index=3"}],
        }
    )
    _rc, text, _out = gate.preview(p, report, "")
    assert "Full sweep: mode=full documents=2" in text and "FAIL aws_iam_policy.old FAIL validate-policy index=3" in text
    assert "Full sweep" not in gate.preview(p, pass_analyzer(rows_of(p)), "")[1]


def test_document_digests_on_create_update_replace_import_and_moved_rows() -> None:
    after = {"name": "agent-platform-a", "policy": GRANT}
    before = {"name": "agent-platform-a", "policy": doc(allow("logs:Describe*", sid="Old"))}
    kept = [
        rc("aws_iam_policy.created", ["create"], after),
        rc("aws_iam_policy.updated", ["update"], after, before),
        rc("aws_iam_policy.replaced", ["delete", "create"], after, before),
        rc("aws_iam_policy.imported", ["no-op"], after, after, change={"importing": {"id": "x"}}),
        rc("aws_iam_policy.moved", ["no-op"], after, after, previous_address="aws_iam_policy.old"),
    ]
    want = cls.document_digest(json.loads(GRANT))
    assert {r["address"]: r["digest"] for r in rows_of(plan(*kept))} == {r["address"]: want for r in kept}
    empty = [
        rc("aws_iam_policy.noop", ["no-op"], after, after),
        rc("aws_iam_policy.gone", ["delete"], None, before),
        rc("aws_iam_policy.unknown", ["create"], {"name": "agent-platform-u"}, unknown={"policy": True}),
    ]
    assert {r["address"]: r["digest"] for r in rows_of(plan(*empty))} == {
        "aws_iam_policy.gone": "",
        "aws_iam_policy.unknown": "",
    }


def test_an_attachment_rows_digest_is_its_attached_policys_and_managed_has_none() -> None:
    attach = attach_rc("aws_iam_role_policy_attachment", "agent-platform-r", f"{ARN}agent-platform-a")
    p = plan(policy_rc("agent-platform-a", GRANT), attach)
    digests = {r["address"]: r["digest"] for r in rows_of(p)}
    assert digests[attach["address"]] == digests["aws_iam_policy.agent_platform_a"] != ""
    assert rows_of(plan(attach_rc("aws_iam_role_policy_attachment", "agent-platform-r", MANAGED)))[0]["digest"] == ""
    from_prior = plan(
        attach, prior=[prior("aws_iam_policy", "aws_iam_policy.agent_platform_a", name="agent-platform-a", policy=GRANT)]
    )
    assert rows_of(from_prior)[0]["digest"] == digests["aws_iam_policy.agent_platform_a"]


def test_group_digest_rules() -> None:
    d = cls.document_digest(json.loads(GRANT))
    assert cls.group_digest(["", ""]) == "" and cls.group_digest([]) == ""
    assert cls.group_digest([d]) != d and cls.group_digest(["", d]) == cls.group_digest([d])
    assert cls.group_digest(["b", "a"]) == cls.group_digest(["a", "b"])
    tagged = [role_rc("agent-platform-t", TRUST, actions=["update"]) for _ in range(2)]
    for i, r in enumerate(tagged):
        r["address"] = f'aws_iam_role.agent_platform_t["{i}"]'
    gone = [rc(f'aws_iam_policy.g["{i}"]', ["delete"], None, {"name": "agent-platform-g", "policy": GRANT}) for i in range(2)]
    assert [r["digest"] for r in rows_of(plan(*tagged, *gone))] == ["", ""]
    single = rc("aws_iam_policy.s[0]", ["create"], {"name": "agent-platform-s", "policy": GRANT})
    assert rows_of(plan(single))[0]["digest"] == cls.group_digest([d])
    mixed = [single, rc("aws_iam_policy.s[1]", ["delete"], None, {"name": "agent-platform-s", "policy": GRANT})]
    assert rows_of(plan(*mixed))[0]["digest"] == cls.group_digest([d])


def test_the_gate_demands_no_result_for_an_empty_digest() -> None:
    p = plan(rc("aws_iam_policy.gone", ["delete"], None, {"name": "agent-platform-g", "policy": GRANT}))
    assert gate.preview(p, {"verdict": "PASS", "results": []}, "")[0] == 1


def test_imported_moved_and_replaced_policies_still_end_cloudshell_required() -> None:
    after = {"name": "agent-platform-a", "policy": GRANT}
    p = plan(
        rc("aws_iam_policy.imported", ["no-op"], after, after, change={"importing": {"id": "x"}}),
        rc("aws_iam_policy.moved", ["no-op"], after, after, previous_address="aws_iam_policy.old"),
        rc("aws_iam_policy.replaced", ["delete", "create"], after, after),
    )
    rc_, text, out = gate.preview(p, pass_analyzer(rows_of(p)), "push")
    assert rc_ == 0 and "verdict=CLOUDSHELL_REQUIRED" in out and "BOOTSTRAP_APPLY_UNREADABLE" not in text
