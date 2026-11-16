"""scripts/ci/terraform_bootstrap_apply_analyzer.py and its select module (PLAN-bootstrap-iam-analyzer-checks VP 4).

The aws CLI and the GitHub compare fetch are injected fakes (tests/fixtures/bootstrap_analyzer.py); synthetic plan JSON drives
every selection, class and check path. No real ARN or account id appears here, and no output may carry one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.ci import terraform_bootstrap_apply_analyzer as an
from scripts.ci import terraform_bootstrap_apply_gate as gate
from tests.fixtures.bootstrap_analyzer import (
    ACCT,
    FINDING_TEXT,
    MANAGED,
    REGION,
    ZERO,
    FakeAws,
    allow,
    attach_rc,
    cfg_refs,
    doc,
    inline_rc,
    plan,
    policy_rc,
    prior,
    rc,
    role_rc,
)

ROOT = Path(__file__).resolve().parents[1]
INV = ROOT / "config" / "iam-invariants.json"
TRUST = doc({"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"})
OPEN_TRUST = doc({"Effect": "Allow", "Principal": {"AWS": "*"}, "Action": "sts:AssumeRole"})
SESSION_USER = "agent-platform-session"
S3_GRANT = doc(allow("s3:GetObject", "*", sid="Leak"))
ARN = f"arn:aws:iam::{ACCT}:policy/"


def execute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], p: dict, *argv: str, fake: Any = None
) -> tuple[int, list[str], dict[str, Any], Any]:
    fake = fake or FakeAws()
    monkeypatch.setattr(an, "RUN", fake)
    monkeypatch.setenv("AWS_REGION", REGION)
    plan_path, out = tmp_path / "plan.json", tmp_path / "analyzer.json"
    plan_path.write_text(json.dumps(p), encoding="utf-8")
    args = [
        "--plan-json",
        str(plan_path),
        "--invariants",
        str(INV),
        "--out",
        str(out),
        "--event-name",
        "workflow_dispatch",
        *argv,
    ]
    code = an.main(args)
    lines = capsys.readouterr().out.splitlines()
    report = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    return code, lines, report, fake


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> Any:
    def go(p: dict, *argv: str, fake: Any = None) -> tuple[int, list[str], dict[str, Any], Any]:
        return execute(tmp_path, monkeypatch, capsys, p, *argv, fake=fake)

    return go


def verdicts(report: dict[str, Any], address: str) -> dict[str, str]:
    return {c["check"]: c["verdict"] for r in report["results"] if r["address"] == address for c in r["checks"]}


def subs(fake: FakeAws) -> list[str]:
    return [c["sub"] for c in fake.calls]


def test_changed_mode_selects_exactly_the_checked_documents(run: Any) -> None:
    new = policy_rc("agent-platform-a", doc(allow("logs:Describe*")))
    wide = policy_rc(
        "agent-platform-b",
        doc(allow("logs:Describe*", sid="One"), allow("ssm:ListTagsForResource")),
        before=doc(allow("logs:Describe*", sid="One")),
    )
    noop = rc(
        "aws_iam_policy.agent_platform_c",
        ["no-op"],
        {"name": "agent-platform-c", "policy": S3_GRANT},
        {"name": "agent-platform-c", "policy": S3_GRANT},
    )
    trust = role_rc("agent-platform-r", TRUST)
    attach_new = attach_rc("aws_iam_role_policy_attachment", "agent-platform-r2", f"{ARN}agent-platform-a")
    managed = attach_rc("aws_iam_role_policy_attachment", "agent-platform-r3", MANAGED)
    code, lines, report, fake = run(plan(new, wide, noop, trust, attach_new, managed))
    assert code == 0 and report["mode"] == "changed"
    addresses = {r["address"] for r in report["results"]}
    assert "aws_iam_policy.agent_platform_c" not in addresses
    assert verdicts(report, "aws_iam_policy.agent_platform_b")["no-new-access"] == "WIDENS"
    assert verdicts(report, "aws_iam_policy.agent_platform_a")["no-new-access"] == "NEW"
    assert verdicts(report, "aws_iam_role_policy_attachment.agent_platform_r3_att") == {"managed-on-session": "UNCHECKED"}
    pulled = [r for r in report["results"] if r["address"] == "aws_iam_role_policy_attachment.agent_platform_r2_att"][0]
    created = [r for r in report["results"] if r["address"] == "aws_iam_policy.agent_platform_a"][0]
    assert pulled["digest"] == created["digest"] and "validate-policy" in verdicts(report, pulled["address"])
    assert lines[-1].startswith("ANALYZER_SUMMARY verdict=PASS mode=changed documents=5 fail=0 widens=1 unchecked=1")


def test_a_trust_is_sent_only_to_check_no_public_access(run: Any) -> None:
    _code, lines, report, fake = run(plan(role_rc("agent-platform-r", TRUST)))
    assert subs(fake) == ["check-no-public-access"] and verdicts(report, "aws_iam_role.agent_platform_r") == {
        "no-public-access": "PASS"
    }
    code, lines, report, fake = run(plan(role_rc("agent-platform-r", OPEN_TRUST)))
    assert code == 1 and "ANALYZER aws_iam_role.agent_platform_r no-public-access FAIL" in lines[0]


def test_a_moved_or_imported_role_with_an_unchanged_trust_is_selected(run: Any) -> None:
    moved = role_rc("agent-platform-r", TRUST, actions=["no-op"], previous_address="aws_iam_role.old")
    other_trust = doc({"Effect": "Allow", "Principal": {"Service": "events.amazonaws.com"}, "Action": "sts:AssumeRole"})
    imported = role_rc("agent-platform-i", other_trust, actions=["no-op"], change={"importing": {"id": "agent-platform-i"}})
    _code, _lines, report, fake = run(plan(moved, imported))
    assert subs(fake) == ["check-no-public-access"] * 2 and len(report["results"]) == 2
    want = {an.cls.document_digest(json.loads(t)) for t in (TRUST, other_trust)}
    assert {r["digest"] for r in report["results"]} == want


@pytest.mark.parametrize(
    "trigger",
    [
        "config/iam-invariants.json",
        "scripts/ci/terraform_bootstrap_apply_analyzer.py",
        "scripts/ci/terraform_bootstrap_apply_analyzer_select.py",
        "scripts/ci/terraform_bootstrap_apply_classify.py",
        "scripts/ci/plan_names_core.py",
    ],
)
def test_a_push_changing_a_trigger_file_sweeps(run: Any, monkeypatch: pytest.MonkeyPatch, trigger: str) -> None:
    monkeypatch.setattr(an, "fetch_changed", lambda *_a: ["docs/x.md", trigger])
    assert run(plan(), "--event-name", "push", "--before", "abc123")[2]["mode"] == "full"


def test_a_push_with_no_trigger_file_stays_changed(run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(an, "fetch_changed", lambda *_a: ["terraform/bootstrap/x.tf"])
    assert run(plan(), "--event-name", "push", "--before", "abc123")[2]["mode"] == "changed"


@pytest.mark.parametrize("before", ["", ZERO])
def test_a_push_with_an_empty_or_zero_before_sweeps(run: Any, before: str) -> None:
    assert run(plan(), "--event-name", "push", "--before", before)[2]["mode"] == "full"


def test_an_unreadable_or_truncated_compare_sweeps_and_a_dispatch_never_does(
    run: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_a: Any) -> list[str]:
        raise an.Unreadable("compare")

    monkeypatch.setattr(an, "fetch_changed", broken)
    assert run(plan(), "--event-name", "push", "--before", "abc123")[2]["mode"] == "full"
    monkeypatch.setattr(an, "fetch_changed", lambda *_a: ["config/iam-invariants.json"])
    assert run(plan(), "--before", "abc123")[2]["mode"] == "changed"
    assert run(plan(), "--full-sweep")[2]["mode"] == "full"


def test_the_compare_fetch_pages_and_a_run_of_full_pages_up_to_300_is_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    def serve(sizes: list[int]) -> Any:
        class Resp:
            def __init__(self, body: bytes) -> None:
                self.body = body

            def __enter__(self) -> Resp:
                return self

            def __exit__(self, *_a: Any) -> None:
                return None

            def read(self) -> bytes:
                return self.body

        def urlopen(req: Any, **_k: Any) -> Resp:
            asked.append(req.full_url)
            page = int(req.full_url.rsplit("page=", 1)[1])
            n = sizes[page - 1] if page <= len(sizes) else 0
            return Resp(json.dumps({"files": [{"filename": f"p{page}f{i}"} for i in range(n)]}).encode())

        monkeypatch.setattr(an.urllib.request, "urlopen", urlopen)

    serve([2])
    assert an.fetch_changed("o/r", "t", "b", "h") == ["p1f0", "p1f1"] and len(asked) == 1
    asked.clear()
    serve([100, 100, 50])
    assert len(an.fetch_changed("o/r", "t", "b", "h")) == 250 and len(asked) == 3
    assert all("per_page=100" in u for u in asked)
    serve([100, 100, 100])
    with pytest.raises(an.Unreadable, match="truncated"):
        an.fetch_changed("o/r", "t", "b", "h")
    serve([300])
    with pytest.raises(an.Unreadable):
        an.fetch_changed("o/r", "t", "b", "h")
    monkeypatch.setattr(an.urllib.request, "urlopen", lambda *_a, **_k: (_ for _ in ()).throw(OSError("net")))
    with pytest.raises(an.Unreadable):
        an.fetch_changed("o/r", "t", "b", "h")


def test_class_union_checks_a_session_policy_against_both_classes_and_other_gets_validate_only(run: Any) -> None:
    both = policy_rc("agent-platform-session-x", doc(allow("logs:Describe*")))
    other = policy_rc("unrelated-p", doc(allow("logs:Describe*")))
    _code, _lines, report, fake = run(plan(both, other))
    both_checks = verdicts(report, "aws_iam_policy.agent_platform_session_x")
    assert {k.split(":")[1] for k in both_checks if k.startswith("never:")} == {"session", "ci_writable"}
    assert [r["classes"] for r in report["results"] if r["address"] == "aws_iam_policy.unrelated_p"] == [[]]
    assert set(verdicts(report, "aws_iam_policy.unrelated_p")) == {"validate-policy", "no-new-access"}


@pytest.mark.parametrize("cfg_kind", ["attachment", "prior_attachment"])
def test_a_policy_outside_every_glob_attached_to_the_session_user_is_a_session_grant(run: Any, cfg_kind: str) -> None:
    if cfg_kind == "attachment":
        changes = [
            policy_rc("harmless-name", S3_GRANT),
            attach_rc("aws_iam_user_policy_attachment", SESSION_USER, f"{ARN}harmless-name"),
        ]
        p = plan(*changes)
    else:
        p = plan(
            policy_rc("harmless-name", S3_GRANT, before=doc(allow("logs:Describe*"))),
            prior=[
                prior(
                    "aws_iam_user_policy_attachment",
                    "aws_iam_user_policy_attachment.s",
                    user=SESSION_USER,
                    policy_arn=f"{ARN}harmless-name",
                )
            ],
        )
    code, lines, report, _fake = run(p)
    assert code == 1 and verdicts(report, "aws_iam_policy.harmless_name")["never:session:s3_objects"] == "FAIL"
    assert any("never:session:s3_objects FAIL sid=Leak" in line for line in lines)


def test_boundaries_ceiling_skips_never_rows_and_a_session_boundary_is_a_grant(run: Any) -> None:
    ceiling = policy_rc("agent-platform-github-ci-apply-boundary", doc(allow("s3:*"), allow("secretsmanager:*", sid="B")))
    code, _lines, report, _fake = run(plan(ceiling))
    assert code == 0 and set(verdicts(report, "aws_iam_policy.agent_platform_github_ci_apply_boundary")) == {
        "validate-policy",
        "no-new-access",
    }
    boundary = policy_rc(
        "agent-platform-session-boundary", doc(allow("ssm:GetParameter", f"arn:aws:ssm:{REGION}:{ACCT}:parameter/secret/x"))
    )
    code, _lines, report, _fake = run(plan(boundary))
    got = verdicts(report, "aws_iam_policy.agent_platform_session_boundary")
    assert code == 1
    assert got["never:session:secret_values_ssm"] == "FAIL"  # pragma: allowlist secret


def test_a_ceiling_named_policy_attached_anywhere_and_a_decoy_boundary_are_grants(run: Any) -> None:
    bad = doc(allow("iam:PutRolePolicy", f"arn:aws:iam::{ACCT}:role/bootstrap-apply"))
    attached = plan(
        policy_rc("agent-platform-github-ci-apply-boundary", bad),
        attach_rc("aws_iam_role_policy_attachment", "agent-platform-r", f"{ARN}agent-platform-github-ci-apply-boundary"),
    )
    code, _lines, report, _fake = run(attached)
    assert (
        code == 1
        and verdicts(report, "aws_iam_policy.agent_platform_github_ci_apply_boundary")[
            "never:ci_writable:protected_role_write"
        ]
        == "FAIL"
    )
    decoy_role = role_rc("agent-platform-decoy", TRUST)
    decoy_role["change"]["after"]["permissions_boundary"] = f"{ARN}agent-platform-grant"
    grant = policy_rc("agent-platform-grant", doc(allow("iam:DeletePolicy", f"arn:aws:iam::{ACCT}:policy/bootstrap-x")))
    code, _lines, report, _fake = run(plan(decoy_role, grant))
    assert (
        code == 1
        and verdicts(report, "aws_iam_policy.agent_platform_grant")["never:ci_writable:protected_policy_write"] == "FAIL"
    )


@pytest.mark.parametrize(
    ("finding", "name", "fails"),
    [
        ("SECURITY_WARNING", "agent-platform-session-w", True),
        ("SECURITY_WARNING", "agent-platform-w", True),
        ("SECURITY_WARNING", "unrelated-w", False),
        ("SECURITY_WARNING", "agent-platform-github-ci-apply-boundary", False),
        ("ERROR", "unrelated-w", True),
        ("ERROR", "agent-platform-github-ci-apply-boundary", True),
        ("ERROR", "agent-platform-session-w", True),
    ],
)
def test_validate_policy_findings_refuse_by_class(run: Any, finding: str, name: str, fails: bool) -> None:
    code, _lines, _report, _fake = run(
        plan(policy_rc(name, doc(allow("logs:Describe*", sid="MARK")))), fake=FakeAws(findings={"MARK": [finding]})
    )
    assert code == (1 if fails else 0)


def test_a_keyed_group_is_one_normalised_result_with_the_sorted_digest(run: Any) -> None:
    docs = [doc(allow("logs:Describe*", sid="A")), doc(allow("logs:Describe*", sid="B"))]
    members = [
        rc(f'aws_iam_policy.agent_platform_k["{key}"]', ["create"], {"name": f"agent-platform-k{key}", "policy": d})
        for key, d in zip("ab", docs, strict=True)
    ]
    _code, _lines, report, _fake = run(plan(*members))
    assert len(report["results"]) == 1 and report["results"][0]["address"] == "aws_iam_policy.agent_platform_k"
    want = an.cls.group_digest([an.cls.document_digest(json.loads(d)) for d in docs])
    assert report["results"][0]["digest"] == want and '["a"]' not in json.dumps(report)


def test_references_unknown_at_plan_time_class_by_the_resolved_principal(run: Any) -> None:
    grant = policy_rc("agent-platform-grant", doc(allow("s3:GetObject")))
    attach = attach_rc("aws_iam_user_policy_attachment", SESSION_USER, None)
    refs = cfg_refs(
        attach["address"], policy_arn=["aws_iam_policy.agent_platform_grant.arn", "aws_iam_policy.agent_platform_grant"]
    )
    code, _lines, report, _fake = run(plan(grant, attach, cfg=[refs]))
    assert code == 1
    assert verdicts(report, "aws_iam_policy.agent_platform_grant")["never:session:s3_objects"] == "FAIL"
    assert verdicts(report, attach["address"])["never:session:s3_objects"] == "FAIL"
    role = role_rc("agent-platform-r", TRUST)
    inline = rc(
        "aws_iam_role_policy.in",
        ["create"],
        {"name": "inline", "policy": doc(allow("iam:PutRolePolicy", f"arn:aws:iam::{ACCT}:role/bootstrap-apply"))},
        unknown={"role": True},
    )
    code, _lines, report, _fake = run(
        plan(
            role,
            inline,
            cfg=[
                cfg_refs(
                    "aws_iam_role_policy.in", role=["aws_iam_role.agent_platform_r.name", "aws_iam_role.agent_platform_r"]
                )
            ],
        )
    )
    assert code == 1 and verdicts(report, "aws_iam_role_policy.in")["never:ci_writable:protected_role_write"] == "FAIL"
    assert not any(k.startswith("never:session") for k in verdicts(report, "aws_iam_role_policy.in"))
    nameless = rc("aws_iam_role_policy.n", ["create"], {"name": "inline", "policy": S3_GRANT}, unknown={"role": True})
    code, _lines, report, _fake = run(plan(nameless))
    assert code == 1 and verdicts(report, "aws_iam_role_policy.n")["never:session:s3_objects"] == "FAIL"


def test_an_aws_managed_policy_on_the_session_user_fails_and_fresh_attachments_are_selected(run: Any) -> None:
    on_user = attach_rc("aws_iam_user_policy_attachment", SESSION_USER, MANAGED)
    code, lines, report, _fake = run(plan(on_user))
    assert code == 1 and verdicts(report, on_user["address"]) == {"managed-on-session": "FAIL"}
    p_prior = [prior("aws_iam_policy", "aws_iam_policy.p", name="agent-platform-p", policy=S3_GRANT)]
    for extra, actions in (
        ({}, ["delete", "create"]),
        ({"change": {"importing": {"id": "x"}}}, ["no-op"]),
        ({"previous_address": "aws_iam_user_policy_attachment.old"}, ["no-op"]),
    ):
        custom = attach_rc("aws_iam_user_policy_attachment", SESSION_USER, f"{ARN}agent-platform-p", actions, **extra)
        code, _lines, report, _fake = run(plan(custom, prior=p_prior))
        assert code == 1 and verdicts(report, custom["address"])["never:session:s3_objects"] == "FAIL", actions
        managed = attach_rc("aws_iam_user_policy_attachment", SESSION_USER, MANAGED, actions, **extra)
        assert run(plan(managed))[0] == 1
    assert an.sel.cls.checked_document(an.cls.Plan(plan()), {"address": "x.y", "change": {"actions": ["no-op"]}}) is None


def test_a_unit_with_prior_state_in_a_full_sweep_and_a_destroyed_row_is_skipped(run: Any) -> None:
    wide = policy_rc(
        "agent-platform-u",
        doc(allow("logs:Describe*", sid="One"), allow("logs:GetLogEvents", sid="Two")),
        before=doc(allow("logs:Describe*", sid="One")),
    )
    keyed = [
        rc(
            'aws_iam_policy.agent_platform_g["a"]',
            ["update"],
            {"name": "agent-platform-ga", "policy": doc(allow("logs:Describe*", sid="N"))},
            {"name": "agent-platform-ga", "policy": doc(allow("logs:Describe*", sid="O"))},
        )
    ]
    destroyed = rc(
        "aws_iam_policy.agent_platform_bad", ["delete"], None, {"name": "agent-platform-session-bad", "policy": S3_GRANT}
    )
    tagged = role_rc("agent-platform-t", TRUST, actions=["update"])
    p = plan(
        wide,
        *keyed,
        destroyed,
        tagged,
        prior=[
            prior(
                "aws_iam_policy",
                'aws_iam_policy.agent_platform_g["b"]',
                name="agent-platform-gb",
                policy=doc(allow("logs:Describe*", sid="Sib")),
            ),
            prior(
                "aws_iam_policy",
                "aws_iam_policy.agent_platform_still",
                name="agent-platform-still",
                policy=doc(allow("logs:Describe*", sid="Still")),
            ),
            prior(
                "aws_iam_role", "aws_iam_role.agent_platform_other", name="agent-platform-other", assume_role_policy=OPEN_TRUST
            ),
        ],
    )
    code, lines, report, fake = run(p, "--full-sweep")
    by = {r["address"]: r for r in report["results"]}
    assert code == 1 and report["mode"] == "full"
    assert "aws_iam_policy.agent_platform_bad" not in by
    assert verdicts(report, "aws_iam_role.agent_platform_other") == {"no-public-access": "FAIL"}
    assert "aws_iam_policy.agent_platform_still" in by and "aws_iam_role.agent_platform_t" in by
    changed_only = run(p)[2]
    changed = {r["address"]: r["digest"] for r in changed_only["results"]}
    assert all(by[a]["digest"] == d for a, d in changed.items())
    assert by["aws_iam_policy.agent_platform_g"]["digest"] == changed["aws_iam_policy.agent_platform_g"]
    assert len([c for c in by["aws_iam_policy.agent_platform_g"]["checks"] if c["check"] == "validate-policy"]) == 2
    pl = an.cls.Plan(p)
    rows = an.cls.build_rows(pl)
    assert gate.preview(p, report, "")[0] == 1
    safe = {**report, "verdict": "PASS"}
    assert gate.preview(p, safe, "push")[1].count("Full sweep: mode=full") == 1 and len(rows) == 4
    assert "FAIL aws_iam_role.agent_platform_other" in gate.preview(p, safe, "push")[1]


def test_identical_documents_in_one_class_set_share_one_call_set(run: Any) -> None:
    same = doc(allow("logs:Describe*"))
    _code, _lines, report, fake = run(plan(policy_rc("agent-platform-d1", same), policy_rc("agent-platform-d2", same)))
    assert subs(fake).count("validate-policy") == 1 and subs(fake).count("check-access-not-granted") == 6
    assert verdicts(report, "aws_iam_policy.agent_platform_d1") == verdicts(report, "aws_iam_policy.agent_platform_d2")


def test_one_never_call_per_row_with_placeholders_filled(run: Any) -> None:
    _code, _lines, _report, fake = run(plan(policy_rc("agent-platform-session-p", doc(allow("logs:Describe*")))))
    access = [c["access"][0] for c in fake.calls if c["sub"] == "check-access-not-granted"]
    assert len(access) == 13
    ssm = [a for a in access if a["actions"][0] == "ssm:GetParameter"][0]
    assert ssm["resources"] == [f"arn:aws:ssm:{REGION}:{ACCT}:parameter/secret/*"]
    assert not any("{account}" in json.dumps(a) or "{region}" in json.dumps(a) for a in access)


def test_a_fail_refuses_and_prints_its_sid_or_index_and_widens_never_refuses(run: Any) -> None:
    code, lines, _report, _fake = run(plan(policy_rc("agent-platform-session-p", S3_GRANT)))
    assert code == 1 and "ANALYZER aws_iam_policy.agent_platform_session_p never:session:s3_objects FAIL sid=Leak" in lines
    assert lines[-1].startswith("ANALYZER_SUMMARY verdict=REFUSED mode=changed documents=1 fail=1")
    widen = policy_rc(
        "agent-platform-w",
        doc(allow("logs:Describe*", sid="A"), allow("logs:GetLogEvents", sid="B")),
        before=doc(allow("logs:Describe*", sid="A")),
    )
    code, lines, _report, _fake = run(plan(widen))
    assert code == 0 and "no-new-access WIDENS" in "\n".join(lines)


@pytest.mark.parametrize("sid", ["bad::sid", "has space", "line\nbreak", "S123456789012", "x" * 65])
def test_a_hostile_sid_prints_as_an_index(run: Any, sid: str) -> None:
    hostile = doc(allow("logs:Describe*", sid="First"), allow("s3:GetObject", "*", sid=sid))
    code, lines, _report, _fake = run(plan(policy_rc("agent-platform-session-h", hostile)))
    assert code == 1 and "never:session:s3_objects FAIL index=1" in "\n".join(lines)
    assert all(line.startswith("ANALYZER") for line in lines) and not any("::" in line for line in lines)


@pytest.mark.parametrize("fail", ["AccessDeniedException", "ThrottlingException", "garbage"])
def test_a_cli_error_exits_two_with_a_code_and_no_stderr_text(run: Any, fail: str) -> None:
    code, lines, report, _fake = run(
        plan(policy_rc("agent-platform-a", doc(allow("logs:Describe*")))), fake=FakeAws(fail=fail)
    )
    want = fail if fail.endswith("Exception") else "cli_error"
    assert code == 2 and lines == [f"ANALYZER_UNREADABLE {want}"] and report == {}


def test_a_malformed_response_a_bad_plan_and_a_bad_invariants_file_exit_two(
    run: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def junk(args: list[str]) -> tuple[int, str, str]:
        return (0, f"{ACCT}\n", "") if args[1] == "sts" else (0, "not json", "")

    code, lines, _r, _f = run(plan(policy_rc("agent-platform-a", doc(allow("logs:Describe*")))), fake=junk)
    assert code == 2 and lines == ["ANALYZER_UNREADABLE cli_error"]
    code, lines, _r, _f = run({"nothing": 1})
    assert code == 2 and lines == ["ANALYZER_UNREADABLE plan"]
    monkeypatch.setattr(an, "RUN", FakeAws())
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    assert an.main(["--plan-json", str(bad), "--invariants", str(INV), "--out", str(tmp_path / "o")]) == 2
    assert capsys.readouterr().out.strip() == "ANALYZER_UNREADABLE plan"
    good = tmp_path / "p.json"
    good.write_text(json.dumps(plan()), encoding="utf-8")
    assert an.main(["--plan-json", str(good), "--invariants", str(bad), "--out", str(tmp_path / "o")]) == 2
    assert capsys.readouterr().out.strip() == "ANALYZER_UNREADABLE invariants"


def test_digests_equal_the_classifiers_and_no_output_leaks(run: Any) -> None:
    marker = "UniqueDocMarker"
    p = plan(
        policy_rc(
            "agent-platform-session-p",
            doc(allow("s3:GetObject", f"arn:aws:s3:::{marker}/*", sid="One"), allow("logs:Describe*", sid="Two")),
        ),
        role_rc("agent-platform-r", TRUST),
        attach_rc("aws_iam_role_policy_attachment", "agent-platform-r", MANAGED),
    )
    code, lines, report, fake = run(p, fake=FakeAws(findings={marker: ["ERROR"]}))
    rows = {r["address"]: r["digest"] for r in an.cls.build_rows(an.cls.Plan(p))}
    assert {r["address"]: r["digest"] for r in report["results"] if r["digest"]} == {a: d for a, d in rows.items() if d}
    stripped = json.dumps([{k: v for k, v in r.items() if k != "digest"} for r in report["results"]])
    for text in ("\n".join(lines), stripped):
        assert not re.search(r"[0-9]{12}|arn:|" + FINDING_TEXT + "|" + marker + "|Statement", text)
    assert code == 1


def test_a_decoy_label_never_makes_an_inline_policy_the_ceiling(run: Any) -> None:
    bad = doc(allow("iam:PutRolePolicy", f"arn:aws:iam::{ACCT}:role/bootstrap-apply"))
    inline = inline_rc("aws_iam_role_policy", "agent-platform-x", bad)
    inline["address"] = "aws_iam_role_policy.aws_iam_policy_x"
    inline["change"]["after"]["name"] = "agent-platform-github-ci-apply-boundary"
    code, _lines, report, _fake = run(plan(inline))
    assert (
        code == 1
        and verdicts(report, "aws_iam_role_policy.aws_iam_policy_x")["never:ci_writable:protected_role_write"] == "FAIL"
    )


def test_name_globs_ignore_case(run: Any) -> None:
    p = plan(
        policy_rc("harmless-name", S3_GRANT),
        attach_rc("aws_iam_user_policy_attachment", SESSION_USER.upper(), f"{ARN}Harmless-Name"),
    )
    code, _lines, report, _fake = run(p)
    assert code == 1 and verdicts(report, "aws_iam_policy.harmless_name")["never:session:s3_objects"] == "FAIL"


def test_an_attachment_whose_policy_resolves_to_no_document_is_unchecked_and_named(run: Any) -> None:
    unknown = attach_rc("aws_iam_user_policy_attachment", SESSION_USER, f"{ARN}elsewhere")
    code, lines, report, fake = run(plan(unknown))
    assert code == 0 and verdicts(report, unknown["address"]) == {"unknown-document": "UNCHECKED"} and fake.calls == []
    assert f"ANALYZER {unknown['address']} unknown-document UNCHECKED" in lines
    assert lines[-1].endswith("documents=1 fail=0 widens=0 unchecked=1")
    assert run(plan(attach_rc("aws_iam_user_policy_attachment", SESSION_USER, f"{ARN}x", ["delete"])))[2]["results"] == []


def test_an_inline_policy_update_runs_check_no_new_access(run: Any) -> None:
    now, before = (
        doc(allow("logs:Describe*", sid="A"), allow("logs:GetLogEvents", sid="B")),
        doc(allow("logs:Describe*", sid="A")),
    )
    inline = inline_rc("aws_iam_role_policy", "agent-platform-i", now, before={"policy": before})
    inline["change"]["actions"] = ["update"]
    code, _lines, report, fake = run(plan(inline))
    assert code == 0 and verdicts(report, inline["address"])["no-new-access"] == "WIDENS"
    assert "check-no-new-access" in subs(fake)


def test_a_missing_cli_a_timeout_an_unset_region_and_an_internal_error_exit_two_with_a_code(
    run: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = plan(policy_rc("agent-platform-a", doc(allow("logs:Describe*"))))
    for boom in (FileNotFoundError("aws"), an.subprocess.TimeoutExpired("aws", 1)):
        assert run(p, fake=lambda _a, boom=boom: (_ for _ in ()).throw(boom))[:2] == (2, ["ANALYZER_UNREADABLE cli_error"])
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        an.subprocess, "run", lambda *_a, **kw: seen.update(kw) or SimpleNamespace(returncode=0, stdout="", stderr="")
    )
    an.run_cli(["aws"])
    assert seen["timeout"] == an.CLI_TIMEOUT
    monkeypatch.setattr(an.sel, "build", lambda *_a: 1 / 0)
    assert run(p)[:2] == (2, ["ANALYZER_UNREADABLE internal_error"])
    monkeypatch.delenv("AWS_REGION", raising=False)
    with pytest.raises(an.Unreadable, match="region"):
        an.Cli()
