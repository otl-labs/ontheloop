"""The analyzer's local rule for OIDC role trusts (PLAN-bootstrap-iam-analyzer-checks, VP 9 forward-fix).

Access Analyzer's check-no-public-access rejects a trust whose principal is a federated OIDC provider (the five GitHub
Actions role trusts), so the analyzer checks that shape itself and sends every other statement to the API. Synthetic plan
JSON and the fake aws CLI of tests/fixtures/bootstrap_analyzer.py; no real ARN or account id appears here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from scripts.ci import terraform_bootstrap_apply_analyzer as an
from tests.fixtures.bootstrap_analyzer import ACCT, FINDING_TEXT, REGION, FakeAws, doc, plan, role_rc

INV = Path(__file__).resolve().parents[1] / "config" / "iam-invariants.json"
HOST = "token.actions.githubusercontent.com"
PROVIDER = f"arn:aws:iam::{ACCT}:oidc-provider/{HOST}"
ADDRESS = "aws_iam_role.agent_platform_ci"
SAML = f"arn:aws:iam::{ACCT}:saml-provider/idp"


def oidc(
    principal: Any = PROVIDER, condition: Any = None, effect: str = "Allow", sid: str = "Web", **extra: Any
) -> dict[str, Any]:
    pinned = {"StringEquals": {f"{HOST}:aud": "sts.amazonaws.com", f"{HOST}:sub": ["repo:owner/repo:environment:e"]}}
    return {
        "Sid": sid,
        "Effect": effect,
        "Principal": {"Federated": principal},
        "Action": "sts:AssumeRoleWithWebIdentity",
        "Condition": pinned if condition is None else condition,
        **extra,
    }


def service(sid: str = "Svc") -> dict[str, Any]:
    return {"Sid": sid, "Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> Any:
    def go(
        *statements: dict[str, Any], fake: Any = None, trust: str | None = None
    ) -> tuple[int, list[str], dict[str, Any], FakeAws]:
        fake = fake or FakeAws()
        monkeypatch.setattr(an, "RUN", fake)
        monkeypatch.setenv("AWS_REGION", REGION)
        plan_path, out = tmp_path / "plan.json", tmp_path / "analyzer.json"
        plan_path.write_text(
            json.dumps(plan(role_rc("agent-platform-ci", doc(*statements) if trust is None else trust))), encoding="utf-8"
        )
        argv = [
            "--plan-json",
            str(plan_path),
            "--invariants",
            str(INV),
            "--out",
            str(out),
            "--event-name",
            "workflow_dispatch",
        ]
        code = an.main(argv)
        return (
            code,
            capsys.readouterr().out.splitlines(),
            json.loads(out.read_text(encoding="utf-8")) if out.exists() else {},
            fake,
        )

    return go


def verdicts(report: dict[str, Any]) -> dict[str, str]:
    return {c["check"]: c["verdict"] for r in report["results"] for c in r["checks"]}


@pytest.mark.parametrize("op", ["StringEquals", "StringLike"])
@pytest.mark.parametrize(
    "sub", ["repo:owner/repo", "repo:owner/repo:environment:e", "repo:owner@1/repo@2:ref:refs/heads/main"]
)
def test_a_pinned_oidc_trust_passes_without_calling_the_api(run: Any, sub: str, op: str) -> None:
    code, lines, report, fake = run(oidc(condition={op: {f"{HOST}:sub": [sub, "repo:owner/other:environment:e"]}}))
    assert code == 0 and fake.calls == [] and verdicts(report) == {"oidc-trust-pin": "PASS"}
    assert [ln for ln in lines if ln.startswith("ANALYZER ")] == [f"ANALYZER {ADDRESS} oidc-trust-pin PASS"]


@pytest.mark.parametrize(
    "stmt",
    [
        oidc(condition={}),
        {k: v for k, v in oidc().items() if k != "Condition"},
        {**oidc(), "Condition": "none"},
        oidc(condition={"StringEquals": {f"{HOST}:aud": "sts.amazonaws.com"}}),
        oidc(condition={"StringEquals": {f"{HOST}:sub": "repo:*"}}),
        oidc(condition={"StringLike": {f"{HOST}:sub": "repo:owner/*"}}),
        oidc(condition={"StringLike": {f"{HOST}:sub": "repo:owner/rep*"}}),
        oidc(condition={"StringLike": {f"{HOST}:sub": "repo:owner/repo:*"}}),
        oidc(condition={"StringLike": {f"{HOST}:sub": "repo:owner/repo:ref:refs/heads/*"}}),
        oidc(condition={"StringLike": {f"{HOST}:sub": "repo:owner/re?o"}}),
        oidc(condition={"StringEquals": {f"{HOST}:sub": "repo:owner/repo:ref:refs/heads/*"}}),
        oidc(condition={"StringEquals": {f"{HOST}:sub": "repo:owner/repo"}, "StringLike": {f"{HOST}:sub": "repo:o/*"}}),
        oidc(condition={"StringEquals": {f"{HOST}:sub": "repo:owner/repo"}, "StringLike": {f"{HOST}:sub": []}}),
        oidc(condition={"StringEquals": {f"{HOST}:sub": "repo:owner/repo", f"{HOST.upper()}:SUB": "repo:*"}}),
        oidc(condition={"StringEquals": {f"{HOST}:sub": "repo:owner/repo:environment:${%s:environment}" % HOST}}),
        oidc(condition={"StringEquals": {f"{HOST}:sub": "repo:owner/repo:$e"}}),
        oidc(condition={"StringLike": {f"{HOST}:sub": "repo:$o/repo"}}),
        oidc(condition={"StringLike": {f"{HOST}:sub": "repo:ow*/repo:ref:x"}}),
        oidc(condition={"StringLike": {f"{HOST}:sub": ["repo:owner/repo:*", "*"]}}),
        oidc(condition={"StringEqualsIfExists": {f"{HOST}:sub": "repo:owner/repo"}}),
        oidc(condition={"ForAnyValue:StringEquals": {f"{HOST}:sub": "repo:owner/repo"}}),
        oidc(condition={"ForAllValues:StringEquals": {f"{HOST}:sub": "repo:owner/repo"}}),
        oidc(condition={"StringEquals": {f"{HOST}:sub": "repo:/repo"}}),
        oidc(condition={"StringEquals": {f"{HOST}:sub": "repo:owner/"}}),
        oidc(condition={"StringEquals": {f"{HOST}:sub": "repo:owner/:environment:e"}}),
        oidc(effect="deny", condition={}),
        oidc(effect="Permit", condition={}),
        {k: v for k, v in oidc(condition={}).items() if k != "Effect"},
        oidc(condition={"StringEquals": {"other.example.com:sub": "repo:owner/repo"}}),
        oidc(condition={"StringEquals": {f"{HOST}:sub": []}}),
        oidc(condition={"StringEquals": {f"{HOST}:sub": [7]}}),
        oidc(principal="*"),
        oidc(principal="accounts.google.com"),
        oidc(principal=[f"arn:aws:iam::{ACCT}:saml-provider/*"]),
        oidc(principal=[7]),
        oidc(principal=f"arn:aws:iam::{'9' * 12}:oidc-provider/{HOST}"),
        oidc(condition={"StringNotEquals": {f"{HOST}:sub": "repo:owner/repo"}}),
        oidc(principal=f"arn:aws:iam::{ACCT}:oidc-provider/*"),
        oidc(principal=[PROVIDER, SAML]),
        oidc(NotPrincipal={"AWS": "*"}),
        {**oidc(), "Principal": {"Federated": PROVIDER, "AWS": "*"}},
    ],
)
def test_an_oidc_trust_without_a_repo_pin_or_a_named_provider_refuses(run: Any, stmt: dict[str, Any]) -> None:
    code, lines, report, fake = run(stmt)
    assert code == 1 and fake.calls == [] and verdicts(report) == {"oidc-trust-pin": "FAIL"}
    assert f"ANALYZER {ADDRESS} oidc-trust-pin FAIL sid=Web" in lines


def test_the_live_trust_shapes_pass_with_their_extra_condition_keys(run: Any) -> None:
    repos = ["owner@1/name@2", "owner@1/theseus@3"]
    ref = ["x/y/.github/workflows/w.yml@refs/heads/main"]
    shapes = [
        {f"{HOST}:sub": [f"repo:{r}:environment:tf-bootstrap-apply" for r in repos], f"{HOST}:job_workflow_ref": ref},
        {f"{HOST}:sub": [f"repo:{r}:{k}" for r in repos for k in ("ref:refs/heads/main", "environment:tf-gated-apply")]},
        {f"{HOST}:sub": [f"repo:{r}:environment:telemetry-content-deploy" for r in repos], f"{HOST}:job_workflow_ref": ref},
    ]
    for pins in shapes:
        code, _lines, report, fake = run(oidc(condition={"StringEquals": {f"{HOST}:aud": "sts.amazonaws.com", **pins}}))
        assert code == 0 and fake.calls == [] and verdicts(report) == {"oidc-trust-pin": "PASS"}


def test_both_operators_on_one_sub_key_and_its_case_are_each_held_to_the_pin(run: Any) -> None:
    both = {"StringEquals": {f"{HOST}:sub": "repo:o/r"}, "StringLike": {f"{HOST}:sub": ["repo:o/r:environment:e"]}}
    assert run(oidc(condition=both))[0] == 0
    assert run(oidc(condition={"StringEquals": {f"{HOST.upper()}:SUB": "repo:o/r"}}))[0] == 0


def test_a_second_provider_must_be_pinned_too(run: Any) -> None:
    other = f"arn:aws:iam::{ACCT}:oidc-provider/other.example.com"
    pins = {"StringEquals": {f"{HOST}:sub": "repo:owner/repo:ref:refs/heads/main"}}
    code, _lines, _report, _fake = run(oidc(principal=[PROVIDER, other], condition=pins))
    assert code == 1
    both = {"StringEquals": {**pins["StringEquals"], "other.example.com:sub": "repo:owner/repo:pull_request"}}
    assert run(oidc(principal=[PROVIDER, other], condition=both))[0] == 0


def test_a_failing_statement_is_named_by_its_index_when_its_sid_is_unsafe(run: Any) -> None:
    _code, lines, _report, _fake = run(oidc(sid="Has Space", condition={}))
    assert f"ANALYZER {ADDRESS} oidc-trust-pin FAIL index=0" in lines


def test_a_deny_statement_is_not_held_to_the_pin_and_is_not_sent_to_the_api(run: Any) -> None:
    code, _lines, report, fake = run(oidc(), oidc(effect="Deny", condition={}, sid="Block"))
    assert code == 0 and fake.calls == [] and verdicts(report) == {"oidc-trust-pin": "PASS"}


def test_a_trust_of_only_deny_oidc_statements_passes_without_calling_the_api(run: Any) -> None:
    code, _lines, report, fake = run(oidc(effect="Deny", condition={}))
    assert code == 0 and fake.calls == [] and verdicts(report) == {"oidc-trust-pin": "PASS"}


def test_the_fake_api_rejects_an_oidc_trust_as_the_real_one_does(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(an, "RUN", FakeAws())
    monkeypatch.setenv("AWS_REGION", REGION)
    call = ["check-no-public-access", "--resource-type", "AWS::IAM::AssumeRolePolicyDocument", "--output", "json"]
    with pytest.raises(an.Unreadable, match="InvalidParameterException"):
        an.Cli().call(call, {"--policy-document": json.loads(doc(oidc()))})
    assert an.Cli().call(call, {"--policy-document": json.loads(doc(service()))})["result"] == "PASS"


def test_a_single_statement_object_is_one_statement(run: Any) -> None:
    code, _lines, report, fake = run(trust=json.dumps({"Version": "2012-10-17", "Statement": oidc(principal="*")}))
    assert code == 1 and fake.calls == [] and verdicts(report) == {"oidc-trust-pin": "FAIL"}
    code, _lines, report, fake = run(trust=json.dumps({"Version": "2012-10-17", "Statement": oidc()}))
    assert code == 0 and fake.calls == [] and verdicts(report) == {"oidc-trust-pin": "PASS"}


def test_a_trust_with_no_statement_goes_whole_to_the_api(run: Any) -> None:
    code, _lines, report, fake = run()
    assert code == 0 and [c["docs"]["--policy-document"]["Statement"] for c in fake.calls] == [[]]
    assert verdicts(report) == {"no-public-access": "PASS"}


def test_the_other_statements_of_the_same_trust_still_go_to_the_api(run: Any) -> None:
    code, _lines, report, fake = run(oidc(), service())
    sent = [c["docs"]["--policy-document"]["Statement"] for c in fake.calls]
    assert code == 0 and sent == [[service()]] and verdicts(report) == {"oidc-trust-pin": "PASS", "no-public-access": "PASS"}
    open_ = {"Sid": "Open", "Effect": "Allow", "Principal": {"AWS": "*"}, "Action": "sts:AssumeRole"}
    code, lines, report, fake = run(oidc(), open_)
    assert code == 1 and f"ANALYZER {ADDRESS} no-public-access FAIL" in "\n".join(lines)
    assert verdicts(report) == {"oidc-trust-pin": "PASS", "no-public-access": "FAIL"}


def test_a_trust_without_an_oidc_statement_is_sent_whole_as_before(run: Any) -> None:
    saml = {"Effect": "Allow", "Principal": {"Federated": SAML}, "Action": "sts:AssumeRoleWithSAML"}
    not_principal = {"Effect": "Allow", "NotPrincipal": {"AWS": f"arn:aws:iam::{ACCT}:root"}, "Action": "sts:AssumeRole"}
    _code, _lines, report, fake = run(service(), saml, not_principal)
    assert [c["docs"]["--policy-document"]["Statement"] for c in fake.calls] == [[service(), saml, not_principal]]
    assert verdicts(report) == {"no-public-access": "PASS"}


def test_any_other_api_error_on_a_trust_still_exits_2(run: Any, capsys: pytest.CaptureFixture[str]) -> None:
    code, lines, _report, _fake = run(oidc(), service(), fake=FakeAws(fail="ThrottlingException"))
    assert code == 2 and lines == ["ANALYZER_UNREADABLE ThrottlingException"]


def test_the_output_carries_no_arn_account_or_finding_text(run: Any) -> None:
    _code, lines, _report, _fake = run(oidc(sid="Web"), service(), oidc(condition={}, sid="Bad"))
    text = "\n".join(lines)
    assert ACCT not in text and "arn:" not in text and FINDING_TEXT not in text and not re.search(r"[0-9]{12}", text)


def test_the_result_is_bound_to_the_digest_of_the_whole_trust(run: Any) -> None:
    statements = [oidc(), service()]
    _code, _lines, report, _fake = run(*statements)
    assert [r["digest"] for r in report["results"]] == [an.cls.document_digest(json.loads(doc(*statements)))]
