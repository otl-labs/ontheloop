"""Tests for scripts/terraform_apply_guard.py (Decision 77 deterministic sandbox-apply guard).

The clean-create case loads a fixture captured from a REAL `terraform show -json` run
(tests/fixtures/terraform_apply_guard/clean_create_real.json) so the guard's field paths are
validated against the actual schema, not hand-authored guesses. The destructive / IAM / trust
cases are synthesised to the same schema.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.terraform_apply_guard import (
    _classify_iam_change,
    _normalise_policy,
    _trust_changed,
    evaluate_plan,
    iter_resource_changes,
    main,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "terraform_apply_guard"
_REAL_CLEAN_CREATE = _FIXTURES / "clean_create_real.json"


def _write(tmp_path: Path, payload: dict) -> str:
    target = tmp_path / "plan.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return str(target)


def _rc(rtype: str, actions: list[str], before=None, after=None, address: str | None = None) -> dict:
    return {
        "address": address or f"{rtype}.example",
        "type": rtype,
        "name": "example",
        "change": {"actions": actions, "before": before, "after": after},
    }


def test_real_clean_create_passes() -> None:
    # Real terraform show -json fixture: a single create on a non-IAM resource.
    assert _REAL_CLEAN_CREATE.exists()
    plan = json.loads(_REAL_CLEAN_CREATE.read_text(encoding="utf-8"))
    assert plan["resource_changes"][0]["type"] == "terraform_data"
    assert evaluate_plan(plan) == []
    assert main([str(_REAL_CLEAN_CREATE)]) == 0


def test_delete_blocks(tmp_path: Path) -> None:
    plan = {"resource_changes": [_rc("aws_s3_bucket", ["delete"], before={"id": "b"})]}
    assert main([_write(tmp_path, plan)]) == 2


def test_replacement_delete_create_blocks(tmp_path: Path) -> None:
    plan = {"resource_changes": [_rc("aws_dynamodb_table", ["delete", "create"])]}
    assert main([_write(tmp_path, plan)]) == 2


def test_replacement_create_delete_blocks(tmp_path: Path) -> None:
    plan = {"resource_changes": [_rc("aws_dynamodb_table", ["create", "delete"])]}
    assert main([_write(tmp_path, plan)]) == 2


def test_iam_update_blocks(tmp_path: Path) -> None:
    plan = {"resource_changes": [_rc("aws_iam_role_policy", ["update"], before={"policy": "{}"}, after={"policy": "{}"})]}
    assert main([_write(tmp_path, plan)]) == 2


def test_iam_create_blocks(tmp_path: Path) -> None:
    plan = {"resource_changes": [_rc("aws_iam_role", ["create"], after={"name": "r"})]}
    assert main([_write(tmp_path, plan)]) == 2


def test_iam_noop_passes(tmp_path: Path) -> None:
    # IAM-sensitive type but inert action -> not blocked by the IAM rule and no trust diff.
    plan = {"resource_changes": [_rc("aws_iam_role", ["no-op"], before={"name": "r"}, after={"name": "r"})]}
    assert main([_write(tmp_path, plan)]) == 0


def test_trust_diff_on_non_iam_resource_blocks(tmp_path: Path) -> None:
    # The trust check applies to ANY resource type, even otherwise-allowed ones.
    before = {"assume_role_policy": json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow"}]})}
    after = {"assume_role_policy": json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Deny"}]})}
    plan = {"resource_changes": [_rc("aws_some_resource", ["update"], before=before, after=after)]}
    assert main([_write(tmp_path, plan)]) == 2


def test_trust_diff_key_order_normalised_passes(tmp_path: Path) -> None:
    # Same policy, different key order -> normalised equal -> no trust finding.
    before = {"assume_role_policy": '{"Version":"2012-10-17","Statement":[]}'}
    after = {"assume_role_policy": '{"Statement":[],"Version":"2012-10-17"}'}
    plan = {"resource_changes": [_rc("aws_other_resource", ["update"], before=before, after=after)]}
    assert evaluate_plan(plan) == []
    assert main([_write(tmp_path, plan)]) == 0


def test_clean_update_passes(tmp_path: Path) -> None:
    plan = {"resource_changes": [_rc("aws_s3_bucket", ["update"], before={"tags": {}}, after={"tags": {"a": "b"}})]}
    assert main([_write(tmp_path, plan)]) == 0


# ---------------------------------------------------------------------------
# Neon-aware policy (T2.16b / CD.34): a neon_* change auto-applies only as a pure create / no-op /
# read; an update blocks; delete + replace are caught by the existing delete rule.
# ---------------------------------------------------------------------------


def test_neon_create_passes(tmp_path: Path) -> None:
    # The provisioning path. Compensating controls (TLS + scoped role + Secrets Manager DSN), not an
    # IP allow-list, carry the posture, so a bare create is safe; sensitive/unknown after-values are
    # irrelevant to the verdict (the guard never introspects neon attributes).
    plan = {"resource_changes": [_rc("neon_project", ["create"], after={"name": "ducklake-catalog"})]}
    assert evaluate_plan(plan) == []
    assert main([_write(tmp_path, plan)]) == 0


def test_neon_database_and_role_create_pass(tmp_path: Path) -> None:
    plan = {
        "resource_changes": [
            _rc("neon_role", ["create"], after={"name": "ducklake_ops"}),
            _rc("neon_database", ["create"], after={"name": "ducklake_ops"}),
        ]
    }
    assert main([_write(tmp_path, plan)]) == 0


def test_neon_noop_passes(tmp_path: Path) -> None:
    plan = {"resource_changes": [_rc("neon_project", ["no-op"], before={"name": "p"}, after={"name": "p"})]}
    assert main([_write(tmp_path, plan)]) == 0


def test_neon_read_passes(tmp_path: Path) -> None:
    plan = {"resource_changes": [_rc("neon_project", ["read"], after={"name": "p"})]}
    assert main([_write(tmp_path, plan)]) == 0


def test_neon_update_blocks(tmp_path: Path) -> None:
    # An update is where an allow-list widening / credential rotation / project-setting change lands.
    plan = {"resource_changes": [_rc("neon_project", ["update"], before={"name": "p"}, after={"name": "p2"})]}
    findings = evaluate_plan(plan)
    assert len(findings) == 1
    assert findings[0]["type"] == "neon_project"
    assert "neon_*" in findings[0]["reason"]
    assert main([_write(tmp_path, plan)]) == 2


def test_neon_replace_blocks(tmp_path: Path) -> None:
    # Replace (delete+create) is caught by the delete rule -- credential/endpoint churn is unsafe.
    plan = {"resource_changes": [_rc("neon_role", ["delete", "create"])]}
    assert main([_write(tmp_path, plan)]) == 2


def test_neon_delete_blocks(tmp_path: Path) -> None:
    plan = {"resource_changes": [_rc("neon_database", ["delete"], before={"name": "ducklake_ops"})]}
    assert main([_write(tmp_path, plan)]) == 2


def test_neon_create_alongside_aws_secret_create_passes(tmp_path: Path) -> None:
    # The DSN secret (aws_secretsmanager_secret) creates alongside the neon resources -- both safe.
    plan = {
        "resource_changes": [
            _rc("neon_project", ["create"], after={"name": "ducklake-catalog"}),
            _rc("aws_secretsmanager_secret", ["create"], after={"name": "ducklake-neon-catalog-dsn"}),
        ]
    }
    assert main([_write(tmp_path, plan)]) == 0


def test_aws_verdicts_unchanged_by_neon_rule(tmp_path: Path) -> None:
    # Regression guard: the aws_ side is unchanged. A non-neon update still passes, an aws destroy
    # still blocks, and an IAM create still blocks -- the neon rule never alters an aws verdict.
    assert main([_write(tmp_path, {"resource_changes": [_rc("aws_s3_bucket", ["update"], before={}, after={})]})]) == 0
    assert main([_write(tmp_path, {"resource_changes": [_rc("aws_s3_bucket", ["delete"], before={"id": "b"})]})]) == 2
    assert main([_write(tmp_path, {"resource_changes": [_rc("aws_iam_role", ["create"], after={"name": "r"})]})]) == 2


def test_empty_plan_passes(tmp_path: Path) -> None:
    assert main([_write(tmp_path, {})]) == 0


def test_malformed_json_errors(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    assert main([str(bad)]) == 1


def test_missing_file_errors(tmp_path: Path) -> None:
    assert main([str(tmp_path / "does_not_exist.json")]) == 1


def test_top_level_not_object_errors(tmp_path: Path) -> None:
    target = tmp_path / "list.json"
    target.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert main([str(target)]) == 1


def test_usage_error_no_args() -> None:
    assert main([]) == 1


def test_usage_error_too_many_args() -> None:
    assert main(["a", "b"]) == 1


def test_normalise_policy_unparseable_string() -> None:
    assert _normalise_policy("not json {{{") == "not json {{{"


def test_normalise_policy_non_string_passthrough() -> None:
    assert _normalise_policy({"already": "parsed"}) == {"already": "parsed"}
    assert _normalise_policy(None) is None


def test_trust_changed_handles_non_dict_states() -> None:
    assert _trust_changed(None, {"assume_role_policy": "{}"}) is False
    assert _trust_changed({"assume_role_policy": "{}"}, None) is False


# ---------------------------------------------------------------------------
# In-budget IAM classification (T2.25 / Decision 92 point 5): inline-policy /
# attachment UPDATE on managed boundary-carrying role auto-applies; everything
# else (wrong type, wrong action, unmanaged role, no budget) blocks.
# ---------------------------------------------------------------------------

# Budget v2 (Decision 144 / T2.48): boundary-carrying agent-platform-* managed-role PREFIX
# (not the v1 two-role enumeration) + in_budget_actions ["create","update"] (subset-matched).
_BUDGET = {
    "schema_version": 2,
    "boundary_policy_name": "agent-platform-github-ci-apply-boundary",
    "in_budget_managed_role_prefix": "agent-platform-",
    "in_budget_resource_types": ["aws_iam_role_policy", "aws_iam_role_policy_attachment"],
    "in_budget_actions": ["create", "update"],
}


def _make_budget_file(tmp_path: Path) -> Path:
    p = tmp_path / "authority_budget.json"
    p.write_text(json.dumps(_BUDGET), encoding="utf-8")
    return p


def test_in_budget_inline_policy_update_on_branch_role_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    budget_path = _make_budget_file(tmp_path)
    monkeypatch.setenv("TF_AUTHORITY_BUDGET", str(budget_path))
    plan = {
        "resource_changes": [
            _rc(
                "aws_iam_role_policy",
                ["update"],
                before={"role": "agent-platform-github-ci-branch", "policy": "{}"},
                after={"role": "agent-platform-github-ci-branch", "policy": '{"Version":"2012-10-17"}'},
                address="aws_iam_role_policy.ci_branch_inline",
            )
        ]
    }
    assert evaluate_plan(plan, _BUDGET) == []
    assert main([_write(tmp_path, plan)]) == 0


def test_in_budget_attachment_update_on_pr_role_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    budget_path = _make_budget_file(tmp_path)
    monkeypatch.setenv("TF_AUTHORITY_BUDGET", str(budget_path))
    plan = {
        "resource_changes": [
            _rc(
                "aws_iam_role_policy_attachment",
                ["update"],
                before={"role": "agent-platform-github-ci-pr", "policy_arn": "arn:aws:iam::aws:policy/OldPolicy"},
                after={"role": "agent-platform-github-ci-pr", "policy_arn": "arn:aws:iam::aws:policy/NewPolicy"},
            )
        ]
    }
    assert evaluate_plan(plan, _BUDGET) == []
    assert main([_write(tmp_path, plan)]) == 0


def test_out_of_budget_inline_policy_on_non_managed_role_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    budget_path = _make_budget_file(tmp_path)
    monkeypatch.setenv("TF_AUTHORITY_BUDGET", str(budget_path))
    plan = {
        "resource_changes": [
            _rc(
                "aws_iam_role_policy",
                ["update"],
                before={"role": "some-other-role", "policy": "{}"},
                after={"role": "some-other-role", "policy": '{"Version":"2012-10-17"}'},
            )
        ]
    }
    findings = evaluate_plan(plan, _BUDGET)
    assert len(findings) == 1
    assert "out-of-budget" in findings[0]["reason"]
    assert main([_write(tmp_path, plan)]) == 2


def test_trust_diff_on_managed_role_type_still_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Trust-diff check fires BEFORE IAM classification; a trust change on an in-budget type is gated.
    budget_path = _make_budget_file(tmp_path)
    monkeypatch.setenv("TF_AUTHORITY_BUDGET", str(budget_path))
    before = {
        "role": "agent-platform-github-ci-branch",
        "assume_role_policy": json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow"}]}),
    }
    after = {
        "role": "agent-platform-github-ci-branch",
        "assume_role_policy": json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Deny"}]}),
    }
    plan = {"resource_changes": [_rc("aws_iam_role_policy", ["update"], before=before, after=after)]}
    findings = evaluate_plan(plan, _BUDGET)
    assert len(findings) == 1
    assert "trust-policy" in findings[0]["reason"]
    assert main([_write(tmp_path, plan)]) == 2


def test_in_budget_inline_policy_create_on_agent_platform_role_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Budget v2 (Decision 144): CREATE of a new inline policy on a boundary-carrying agent-platform-*
    # role is now in-budget (subset-match ["create"] + prefix-match) -> auto-apply (exit 0).
    monkeypatch.setenv("TF_AUTHORITY_BUDGET", str(_make_budget_file(tmp_path)))
    plan = {"resource_changes": [_rc("aws_iam_role_policy", ["create"], after={"role": "agent-platform-x", "policy": "{}"})]}
    assert evaluate_plan(plan, _BUDGET) == []
    assert main([_write(tmp_path, plan)]) == 0


def test_aws_iam_role_create_still_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Role CREATE (aws_iam_role type) stays gated under v2: aws_iam_role is NOT an in_budget_resource_type
    # (Decision 144: gated-but-executable, never auto-applied).
    monkeypatch.setenv("TF_AUTHORITY_BUDGET", str(_make_budget_file(tmp_path)))
    plan = {"resource_changes": [_rc("aws_iam_role", ["create"], after={"name": "agent-platform-x"})]}
    findings = evaluate_plan(plan, _BUDGET)
    assert len(findings) == 1 and "out-of-budget" in findings[0]["reason"]
    assert main([_write(tmp_path, plan)]) == 2


def test_apply_role_self_exclusion_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # SECURITY-CRITICAL (Decision 144, guard-side counterpart to DenySelfInlinePolicyWrite): the
    # widened agent-platform-* prefix MATCHES agent-platform-github-ci-apply, so an inline-policy
    # write on the apply role's own ARN must NEVER auto-apply -- it routes to gated (T2.23 self-grant
    # break). Both UPDATE and CREATE on the apply role gate.
    budget_path = _make_budget_file(tmp_path)
    monkeypatch.setenv("TF_AUTHORITY_BUDGET", str(budget_path))
    for actions in (["update"], ["create"]):
        change = _rc(
            "aws_iam_role_policy",
            actions,
            before={"role": "agent-platform-github-ci-apply", "policy": "{}"},
            after={"role": "agent-platform-github-ci-apply", "policy": '{"Version":"2012-10-17"}'},
        )
        assert _classify_iam_change(change, _BUDGET) is False
        findings = evaluate_plan({"resource_changes": [change]}, _BUDGET)
        assert len(findings) == 1
        assert "out-of-budget" in findings[0]["reason"]


def test_classify_non_subset_action_is_out_of_budget() -> None:
    # Actions not a subset of in_budget_actions (e.g. a bare delete) -> out-of-budget.
    assert _classify_iam_change(_rc("aws_iam_role_policy", ["delete"], after={"role": "agent-platform-x"}), _BUDGET) is False


def test_classify_v1_budget_uses_enumerated_fallback() -> None:
    # A v1 budget (no in_budget_managed_role_prefix) falls back to enumerated in_budget_managed_roles.
    v1 = {
        "in_budget_resource_types": ["aws_iam_role_policy"],
        "in_budget_actions": ["update"],
        "in_budget_managed_roles": ["agent-platform-github-ci-branch"],
    }
    hit = _rc("aws_iam_role_policy", ["update"], after={"role": "agent-platform-github-ci-branch"})
    assert _classify_iam_change(hit, v1) is True
    assert _classify_iam_change(_rc("aws_iam_role_policy", ["update"], after={"role": "unlisted"}), v1) is False


def test_fail_closed_on_missing_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TF_AUTHORITY_BUDGET", str(tmp_path / "does_not_exist.json"))
    plan = {
        "resource_changes": [
            _rc(
                "aws_iam_role_policy",
                ["update"],
                before={"role": "agent-platform-github-ci-branch", "policy": "{}"},
                after={"role": "agent-platform-github-ci-branch", "policy": '{"Version":"2012-10-17"}'},
            )
        ]
    }
    # Without a valid budget table, all IAM changes are out-of-budget (fail-closed).
    assert _classify_iam_change(plan["resource_changes"][0], None) is False
    assert main([_write(tmp_path, plan)]) == 2


def test_in_budget_fixture_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    # VP step 2: the real fixture passes when the real budget table is loaded.
    fixture = Path(__file__).parent / "fixtures" / "terraform_apply_guard" / "iam_inline_update_branch.json"
    assert fixture.exists(), f"fixture missing: {fixture}"
    # Unset any override so the default budget path (the real authority_budget.json) is used.
    monkeypatch.delenv("TF_AUTHORITY_BUDGET", raising=False)
    assert main([str(fixture)]) == 0


# ---------------------------------------------------------------------------
# The reviewer digest moved to scripts/ci/terraform_plan_digest.py (rec-4639); the guard keeps the
# shared traversal and the redaction helper.
# ---------------------------------------------------------------------------


def test_digest_flag_is_a_usage_error(tmp_path: Path) -> None:
    path = _write(tmp_path, {"resource_changes": []})
    assert main(["--digest", path]) == 1


def test_iter_resource_changes_is_the_verdict_traversal() -> None:
    entries = [_rc("aws_s3_bucket", ["create"], after={"bucket": "b"}), _rc("aws_dynamodb_table", ["delete"])]
    assert iter_resource_changes({"resource_changes": entries}) == entries
    assert iter_resource_changes({}) == []
    assert iter_resource_changes({"resource_changes": None}) == []
    findings = evaluate_plan({"resource_changes": entries})
    assert [f["address"] for f in findings] == ["aws_dynamodb_table.example"]


def test_findings_unchanged_across_fixtures_via_iter_resource_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TF_AUTHORITY_BUDGET", str(_FIXTURES / "missing_budget.json"))
    expected = {"clean_create_real.json": 0, "iam_inline_update_branch.json": 1}
    for name, count in expected.items():
        plan = json.loads((_FIXTURES / name).read_text(encoding="utf-8"))
        assert len(evaluate_plan(plan)) == count
        assert [f["address"] for f in evaluate_plan(plan)] == [
            f["address"] for f in evaluate_plan({"resource_changes": iter_resource_changes(plan)})
        ]


def test_redact_arn_and_account_id() -> None:
    from scripts.terraform_apply_guard import _redact  # noqa: PLC0415

    assert _redact("account=336392948345 arn=arn:aws:s3:::my-bucket/336392948345/x") == "account=[ACCOUNT_ID] arn=[ARN]"


# ---------------------------------------------------------------------------
# Resource-based-policy classification (T2.45 / DEP-06): aws_lambda_permission,
# aws_s3_bucket_policy, aws_sns_topic_policy, aws_secretsmanager_secret_policy,
# aws_glue_resource_policy, aws_lambda_function_url. Evaluated LAST in evaluate_plan (after
# delete -> neon -> trust -> IAM); the only safe shape is the EventBridge aws_lambda_permission
# invoke pattern (principal + action + function_name-prefix, all three conjunctively).
# ---------------------------------------------------------------------------


def _lambda_permission(
    principal: Any,
    action: str = "lambda:InvokeFunction",
    function_name: str = "agent-platform-ducklake-writer",
    actions: list[str] | None = None,
    address: str | None = None,
) -> dict:
    after = {"principal": principal, "action": action, "function_name": function_name, "statement_id": "Allow"}
    return _rc("aws_lambda_permission", actions or ["create"], after=after, address=address)


def test_resource_policy_lambda_permission_wildcard_principal_blocks(tmp_path: Path) -> None:
    # T2.45:c1 -- Principal "*" exits the guard with code 2, via both evaluate_plan() and main().
    plan = {"resource_changes": [_lambda_permission(principal="*")]}
    findings = evaluate_plan(plan)
    assert len(findings) == 1
    assert findings[0]["type"] == "aws_lambda_permission"
    assert main([_write(tmp_path, plan)]) == 2


def test_resource_policy_eventbridge_safe_shape_passes(tmp_path: Path) -> None:
    # T2.45:c2 -- the known-safe shape (events.amazonaws.com + lambda:InvokeFunction +
    # agent-platform-* function) is in-budget with no false-positive regression.
    plan = {
        "resource_changes": [
            _lambda_permission(
                principal="events.amazonaws.com",
                action="lambda:InvokeFunction",
                function_name="agent-platform-ducklake-maintenance",
            )
        ]
    }
    assert evaluate_plan(plan) == []
    assert main([_write(tmp_path, plan)]) == 0


def test_resource_policy_lambda_permission_wrong_action_blocks(tmp_path: Path) -> None:
    # Folded-in note 2 (plan-critique): isolated ACTION-LEG negative -- principal and
    # function_name both match the safe shape, but action does not. Proves the conjunction
    # requires the action predicate, not just principal + function_name.
    plan = {
        "resource_changes": [
            _lambda_permission(
                principal="events.amazonaws.com", action="lambda:*", function_name="agent-platform-ducklake-writer"
            )
        ]
    }
    findings = evaluate_plan(plan)
    assert len(findings) == 1
    assert main([_write(tmp_path, plan)]) == 2


def test_resource_policy_lambda_permission_non_agent_platform_function_blocks(tmp_path: Path) -> None:
    # Function-name-leg negative: principal + action match the safe shape but function_name does
    # not start with agent-platform-.
    plan = {
        "resource_changes": [
            _lambda_permission(
                principal="events.amazonaws.com", action="lambda:InvokeFunction", function_name="some-other-function"
            )
        ]
    }
    assert main([_write(tmp_path, plan)]) == 2


def test_resource_policy_lambda_permission_s3_principal_blocks(tmp_path: Path) -> None:
    # Principal-leg negative, real-world shape: prod_lambdas.tf's S3-triggered permissions use
    # principal=s3.amazonaws.com -- not the EventBridge shape, so a non-inert change blocks.
    plan = {
        "resource_changes": [
            _lambda_permission(principal="s3.amazonaws.com", function_name="agent-platform-findings-processor")
        ]
    }
    assert main([_write(tmp_path, plan)]) == 2


def test_resource_policy_lambda_permission_missing_principal_blocks(tmp_path: Path) -> None:
    # Fail-closed: no principal attribute at all (absent from both after and before).
    plan = {
        "resource_changes": [
            _rc(
                "aws_lambda_permission",
                ["create"],
                after={"action": "lambda:InvokeFunction", "function_name": "agent-platform-x"},
            )
        ]
    }
    assert main([_write(tmp_path, plan)]) == 2


def test_resource_policy_lambda_permission_unknown_principal_blocks(tmp_path: Path) -> None:
    # Fail-closed: an unparseable/unknown service principal.
    plan = {"resource_changes": [_lambda_permission(principal="not-a-real-service.example.com")]}
    assert main([_write(tmp_path, plan)]) == 2


def test_resource_policy_lambda_permission_inert_passes(tmp_path: Path) -> None:
    # Inert (no-op/read) is always safe, even with an otherwise-blocking principal.
    plan = {"resource_changes": [_rc("aws_lambda_permission", ["no-op"], before={"principal": "*"}, after={"principal": "*"})]}
    assert evaluate_plan(plan) == []
    assert main([_write(tmp_path, plan)]) == 0


# --- T2.45:c3 -- each of the six resource-policy types is classified sensitive: a non-inert,
# non-safe-shape change exits 2. ---


def test_resource_policy_s3_bucket_policy_blocks(tmp_path: Path) -> None:
    plan = {
        "resource_changes": [
            _rc("aws_s3_bucket_policy", ["update"], before={"policy": "{}"}, after={"policy": '{"Statement": []}'})
        ]
    }
    findings = evaluate_plan(plan)
    assert len(findings) == 1
    assert findings[0]["type"] == "aws_s3_bucket_policy"
    assert main([_write(tmp_path, plan)]) == 2


def test_resource_policy_sns_topic_policy_blocks(tmp_path: Path) -> None:
    plan = {
        "resource_changes": [
            _rc("aws_sns_topic_policy", ["update"], before={"policy": "{}"}, after={"policy": '{"Statement": []}'})
        ]
    }
    assert main([_write(tmp_path, plan)]) == 2


def test_resource_policy_secretsmanager_secret_policy_blocks(tmp_path: Path) -> None:
    plan = {"resource_changes": [_rc("aws_secretsmanager_secret_policy", ["create"], after={"policy": '{"Statement": []}'})]}
    assert main([_write(tmp_path, plan)]) == 2


def test_resource_policy_glue_resource_policy_blocks(tmp_path: Path) -> None:
    plan = {
        "resource_changes": [
            _rc("aws_glue_resource_policy", ["update"], before={"policy": "{}"}, after={"policy": '{"Statement": []}'})
        ]
    }
    assert main([_write(tmp_path, plan)]) == 2


def test_resource_policy_lambda_function_url_blocks(tmp_path: Path) -> None:
    plan = {"resource_changes": [_rc("aws_lambda_function_url", ["create"], after={"authorization_type": "NONE"})]}
    findings = evaluate_plan(plan)
    assert len(findings) == 1
    assert findings[0]["type"] == "aws_lambda_function_url"
    assert main([_write(tmp_path, plan)]) == 2


# --- Ordering regression: a delete or trust-diff on a resource-policy type is still caught by
# the earlier rule, never reaching the resource-policy stage (proven via the finding's reason). ---


def test_resource_policy_type_delete_still_caught_by_delete_rule(tmp_path: Path) -> None:
    plan = {"resource_changes": [_rc("aws_lambda_permission", ["delete"], before={"principal": "events.amazonaws.com"})]}
    findings = evaluate_plan(plan)
    assert len(findings) == 1
    assert findings[0]["reason"] == "destroy or replacement"
    assert main([_write(tmp_path, plan)]) == 2


def test_resource_policy_type_trust_diff_still_caught_by_trust_rule(tmp_path: Path) -> None:
    # Synthetic cross-type case, mirroring test_trust_diff_on_non_iam_resource_blocks's own
    # precedent (the trust check applies to ANY resource type): otherwise this would be the safe
    # EventBridge shape, but the trust-policy diff must still gate it BEFORE the resource-policy
    # stage runs.
    before = {
        "principal": "events.amazonaws.com",
        "action": "lambda:InvokeFunction",
        "function_name": "agent-platform-ducklake-writer",
        "assume_role_policy": json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow"}]}),
    }
    after = dict(before, assume_role_policy=json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Deny"}]}))
    plan = {"resource_changes": [_rc("aws_lambda_permission", ["update"], before=before, after=after)]}
    findings = evaluate_plan(plan)
    assert len(findings) == 1
    assert "trust-policy" in findings[0]["reason"]
    assert main([_write(tmp_path, plan)]) == 2


def test_resource_policy_reason_redacts_external_principal() -> None:
    # The finding reason itself (not just the digest) is redaction-safe -- this repo is public,
    # and finding reasons are printed to stdout/CI logs by main().
    plan = {
        "resource_changes": [_lambda_permission(principal="arn:aws:iam::336392948345:root", function_name="agent-platform-x")]
    }
    findings = evaluate_plan(plan)
    assert len(findings) == 1
    assert "336392948345" not in findings[0]["reason"]
    assert "[ARN]" in findings[0]["reason"]
