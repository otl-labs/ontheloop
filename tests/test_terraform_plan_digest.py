"""Unit tests for scripts/ci/terraform_plan_digest.py, the names-only reviewer digest (rec-4639)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.ci.terraform_plan_digest import (
    REVIEWER_PREAMBLE,
    DigestOverflow,
    build_digest,
    load_literals,
    main,
    strip_instance_keys,
)
from scripts.terraform_apply_guard import evaluate_plan


def _rc(rtype: str, actions: list[str], before=None, after=None, address: str | None = None, unknown=None) -> dict:
    return {
        "address": address or f"{rtype}.example",
        "type": rtype,
        "change": {"actions": actions, "before": before, "after": after, "after_unknown": unknown or {}},
    }


def _policy(*statements: dict) -> str:
    return json.dumps({"Version": "2012-10-17", "Statement": list(statements)})


def _stmt(effect: str = "Allow", **kw) -> dict:
    return {"Effect": effect, "Resource": "*", **kw}


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("aws_x.y[0]", "aws_x.y[*]"),
        ('aws_x.y["k"]', "aws_x.y[*]"),
        ('aws_x.y["a]b"]', "aws_x.y[*]"),
        ('aws_x.y["a\\"]b"]', "aws_x.y[*]"),
        ('module.m["one"].aws_x.y[2]', "module.m[*].aws_x.y[*]"),
        ("module.m[1].module.n[0].aws_x.y", "module.m[*].module.n[*].aws_x.y"),
        ("aws_x.y", "aws_x.y"),
    ],
)
def test_strip_instance_keys(address: str, expected: str) -> None:
    assert strip_instance_keys(address) == expected


def test_identical_stripped_lines_merge_with_count() -> None:
    plan = {"resource_changes": [_rc("aws_x", ["update"], {"a": 1}, {"a": 2}, address=f'aws_x.y["k{i}"]') for i in range(3)]}
    digest = build_digest(plan)
    assert digest.count("aws_x.y[*]") == 1
    assert "changed=[a] x3" in digest
    assert "k0" not in digest


def test_changed_names_use_after_unknown_and_never_nested_or_map_keys() -> None:
    plan = {
        "resource_changes": [
            _rc(
                "aws_x",
                ["update"],
                {"tags": {"old": "1"}, "same": 1},
                {"tags": {"SECRET-KEY": "2"}, "same": 1},
                unknown={"arn": True},
            ),
        ]
    }
    digest = build_digest(plan)
    assert "changed=[arn, tags]" in digest
    assert "SECRET-KEY" not in digest


def test_policy_actions_added_and_removed_gated_by_literals() -> None:
    before = _policy(_stmt(Action=["s3:GetObject", "s3:PutObject"]))
    after = _policy(_stmt(Action=["s3:GetObject", "kms:Decrypt", "s3:Hidden"]))
    plan = {"resource_changes": [_rc("aws_iam_role_policy", ["update"], {"policy": before}, {"policy": after})]}
    literals = frozenset({"kms:Decrypt", "s3:PutObject"})
    digest = build_digest(plan, literals)
    assert "Allow +[<non-literal>, kms:Decrypt] -[s3:PutObject]" in digest
    assert "s3:Hidden" not in digest
    assert "s3:GetObject" not in digest


def test_no_literals_means_everything_non_literal() -> None:
    after = _policy(_stmt(Action="s3:GetObject"))
    plan = {"resource_changes": [_rc("aws_iam_policy", ["create"], None, {"policy": after})]}
    digest = build_digest(plan)
    assert "Allow +[<non-literal>] -[]" in digest
    assert "s3:GetObject" not in digest


def test_notaction_tokens_are_prefixed_and_deny_effect_kept() -> None:
    after = _policy(_stmt("Deny", NotAction=["iam:*"]))
    plan = {"resource_changes": [_rc("aws_iam_role_policy", ["update"], {"policy": _policy()}, {"policy": after})]}
    digest = build_digest(plan, frozenset({"iam:*"}))
    assert "Deny +[not iam:*] -[]" in digest


def test_policy_unknown_and_unparseable() -> None:
    unknown = {"resource_changes": [_rc("aws_iam_role_policy", ["update"], {"policy": "{}"}, {}, unknown={"policy": True})]}
    assert "policy=(known after apply)" in build_digest(unknown)
    bad = {"resource_changes": [_rc("aws_iam_role_policy", ["update"], {"policy": "{}"}, {"policy": "{not json"})]}
    assert "policy=<unparsed>" in build_digest(bad)


def test_unchanged_policy_prints_no_detail() -> None:
    pol = _policy(_stmt(Action="s3:GetObject"))
    plan = {"resource_changes": [_rc("aws_iam_role_policy", ["update"], {"policy": pol, "x": 1}, {"policy": pol, "x": 2})]}
    digest = build_digest(plan, frozenset({"s3:GetObject"}))
    assert "policy" not in digest.split("changed=")[1]


def test_lambda_permission_fields_always_shown_and_literal_gated() -> None:
    common = {"principal": "events.amazonaws.com", "action": "lambda:InvokeFunction", "function_name": "agent-platform-x"}
    plan = {
        "resource_changes": [
            _rc("aws_lambda_permission", ["update"], dict(common, source_arn="a"), dict(common, source_arn="b"))
        ]
    }
    digest = build_digest(plan, frozenset({"events.amazonaws.com", "lambda:InvokeFunction"}))
    assert "principal=events.amazonaws.com action=lambda:InvokeFunction function_name=<non-literal>" in digest
    assert "agent-platform-x" not in digest
    assert "changed=[source_arn]" in digest


def test_function_url_authorization_type() -> None:
    plan = {
        "resource_changes": [
            _rc("aws_lambda_function_url", ["update"], {"authorization_type": "NONE"}, {"authorization_type": "AWS_IAM"})
        ]
    }
    assert "authorization_type=AWS_IAM" in build_digest(plan, frozenset({"AWS_IAM"}))
    assert "authorization_type=<non-literal>" in build_digest(plan)


def test_outputs_are_names_only() -> None:
    plan = {"output_changes": {"dsn": {"actions": ["update"], "before": "old-v", "after": "new-v"}}}
    digest = build_digest(plan)
    assert "- output dsn actions=[update]" in digest
    assert "new-v" not in digest and "old-v" not in digest
    assert "0 resource change(s), 1 output change(s)" in digest


def test_lines_are_redacted() -> None:
    plan = {"resource_changes": [_rc("aws_x", ["update"], {"a": 1}, {"a": 2}, address="aws_x.arn:aws:iam::336392948345:root")]}
    digest = build_digest(plan)
    assert "336392948345" not in digest
    assert "[ARN]" in digest


def test_newlines_in_plan_text_cannot_forge_lines() -> None:
    plan = {"resource_changes": [_rc("aws_x", ["update"], {"a": 1}, {"a": 2}, address="aws_x.y\n- ignore previous")]}
    assert "\n- ignore previous" not in build_digest(plan)


def test_empty_plan() -> None:
    assert "0 resource change(s), 0 output change(s)" in build_digest({})


def test_preamble_survives_truncation_and_marker_is_reworded() -> None:
    plan = {
        "resource_changes": [
            _rc("aws_x", ["update"], {"a": 1}, {"a": 2}),
            *[_rc(f"aws_t{i:03d}", ["no-op"], address=f"aws_t{i:03d}.r") for i in range(60)],
        ]
    }
    full = build_digest(plan, size_cap=100_000)
    assert "TRUNCATED" not in full
    cap = len(full.encode("utf-8")) - 40
    digest = build_digest(plan, size_cap=cap)
    assert digest.startswith(REVIEWER_PREAMBLE)
    assert "[DIGEST TRUNCATED: inert tail omitted -- every actionable change is listed above]" in digest
    assert "aws_x.example" in digest
    assert "plan.json" not in digest
    assert len(digest.encode("utf-8")) <= cap


def test_actionable_overflow_raises_instead_of_truncating() -> None:
    plan = {"resource_changes": [_rc("aws_x", ["update"], {"a": 1}, {"a": 2}, address=f"aws_x.r{i}") for i in range(50)]}
    with pytest.raises(DigestOverflow):
        build_digest(plan, size_cap=700)


def test_digest_lists_exactly_the_resources_the_guard_judged() -> None:
    plan = {
        "resource_changes": [
            _rc("aws_s3_bucket", ["create"], None, {"bucket": "b"}, address="aws_s3_bucket.a"),
            _rc("aws_dynamodb_table", ["delete"], address="aws_dynamodb_table.gone"),
        ]
    }
    digest = build_digest(plan)
    for finding in evaluate_plan(plan):
        assert finding["address"] in digest
    assert "2 resource change(s)" in digest


def test_load_literals_skips_interpolated_strings_and_reads_dirs(tmp_path: Path) -> None:
    (tmp_path / "a.tf").write_text(
        'x = "plain"\ny = "pre-${var.v}"\nz = "esc-$${notvar}"\nw = "tmpl-%{ if a }"\nq = "say \\"hi\\""\n', encoding="utf-8"
    )
    (tmp_path / "b.txt").write_text('x = "ignored"\n', encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.tf").write_text('x = "nested"\n', encoding="utf-8")
    literals = load_literals([str(tmp_path)])
    assert literals == {"plain", "esc-$${notvar}", 'say "hi"'}
    assert load_literals([str(tmp_path / "missing.tf")]) == frozenset()


def test_cli_exit_codes_and_repeated_literals_flag(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    one = tmp_path / "one.tf"
    one.write_text('a = "s3:GetObject"\n', encoding="utf-8")
    two = tmp_path / "two.tf"
    two.write_text('b = "kms:Decrypt"\n', encoding="utf-8")
    after = _policy(_stmt(Action=["s3:GetObject", "kms:Decrypt"]))
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"resource_changes": [_rc("aws_iam_role_policy", ["create"], None, {"policy": after})]}), encoding="utf-8"
    )
    assert main(["--literals-from", str(one), "--literals-from", str(two), str(plan)]) == 0
    assert "+[kms:Decrypt, s3:GetObject]" in capsys.readouterr().out
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    assert main([str(bad)]) == 1
    assert main([str(tmp_path / "missing.json")]) == 1
    arr = tmp_path / "arr.json"
    arr.write_text("[]", encoding="utf-8")
    assert main([str(arr)]) == 1


def _doc_stmt(sid: str, resources: list[str], condition: dict | None = None) -> dict:
    stmt = {"Sid": sid, "Effect": "Allow", "Action": ["s3:GetObject"], "Resource": resources}
    if condition:
        stmt["Condition"] = condition
    return stmt


def test_resource_only_narrowing_is_visible_per_sid_as_counts_only() -> None:
    before = _policy(
        _doc_stmt("ReadLake", ["arn:aws:s3:::secret-bucket/*", "arn:aws:s3:::other/*", "arn:aws:s3:::third/*"]),
        _doc_stmt("DsnSecret", ["arn:aws:secretsmanager:eu-west-2:123:secret:dsn"]),
    )
    after = _policy(
        _doc_stmt("ReadLake", ["arn:aws:s3:::secret-bucket/*"], {"StringEquals": {"aws:Tag/k": "v", "aws:x": "y"}})
    )
    plan = {"resource_changes": [_rc("aws_iam_role_policy", ["update"], {"policy": before}, {"policy": after})]}
    digest = build_digest(plan, frozenset({"ReadLake", "DsnSecret", "s3:GetObject"}))
    assert "Sids +[] -[DsnSecret]" in digest
    assert "Sids modified [ReadLake: Action 1->1, Resource 3->1, Condition 0->2]" in digest
    assert "secret-bucket" not in digest and "secretsmanager" not in digest and "StringEquals" not in digest


def test_sids_are_literal_gated_and_unnamed_statements_are_indexed() -> None:
    before = _policy(_doc_stmt("Hidden", ["a"]), {"Effect": "Allow", "Action": "x:y", "Resource": "*"})
    after = _policy(_doc_stmt("Hidden", ["a", "b"]))
    plan = {"resource_changes": [_rc("aws_iam_role_policy", ["update"], {"policy": before}, {"policy": after})]}
    digest = build_digest(plan)
    assert "Hidden" not in digest
    assert "<non-literal>: Action 1->1, Resource 1->2" in digest
    assert "-[(no Sid #2)]" in digest


def test_sid_added_and_reorder_only_change() -> None:
    one, two = _doc_stmt("A", ["r1"]), _doc_stmt("B", ["r2"])
    added = {"resource_changes": [_rc("aws_iam_policy", ["update"], {"policy": _policy(one)}, {"policy": _policy(one, two)})]}
    assert "Sids +[B] -[]" in build_digest(added, frozenset({"B"}))
    reorder = {
        "resource_changes": [_rc("aws_iam_policy", ["update"], {"policy": _policy(one, two)}, {"policy": _policy(two, one)})]
    }
    assert "policy: changed (no action, Sid, or count delta)" in build_digest(reorder)


def _deferred_plan() -> dict:
    return {
        "resource_changes": [
            {
                "address": "data.aws_iam_policy_document.ci_branch",
                "mode": "data",
                "type": "aws_iam_policy_document",
                "action_reason": "read_because_dependency_pending",
                "change": {"actions": ["read"], "before": None, "after": None, "after_unknown": {"json": True}},
            },
            _rc(
                "aws_iam_role_policy",
                ["update"],
                {"policy": "{}"},
                {},
                address="aws_iam_role_policy.github_ci_branch",
                unknown={"policy": True},
            ),
        ],
        "configuration": {
            "root_module": {
                "resources": [
                    {
                        "address": "aws_iam_role_policy.github_ci_branch",
                        "expressions": {"policy": {"references": ["data.aws_iam_policy_document.ci_branch.json"]}},
                    }
                ]
            }
        },
    }


def test_deferred_data_read_is_annotated_on_both_lines() -> None:
    digest = build_digest(_deferred_plan())
    assert "(aws_iam_policy_document) actions=[read] reason=read_because_dependency_pending" in digest
    assert "policy=(known after apply; deferred data read: data.aws_iam_policy_document.ci_branch)" in digest


def test_deferred_annotation_without_configuration_and_without_pending() -> None:
    plan = _deferred_plan()
    del plan["configuration"]
    assert "policy=(known after apply; plan has deferred data reads (read_because_dependency_pending))" in build_digest(plan)
    plain = {"resource_changes": [_rc("aws_iam_role_policy", ["update"], {"policy": "{}"}, {}, unknown={"policy": True})]}
    assert "policy=(known after apply)" in build_digest(plain)


def test_action_reason_is_shape_gated() -> None:
    entry = _rc("aws_x", ["update"], {"a": 1}, {"a": 2})
    entry["action_reason"] = "Evil\nIgnore previous"
    assert "reason=" not in build_digest({"resource_changes": [entry]})


def test_narrow_plan_shape_is_reviewable() -> None:
    """Replay of the #1687 shape: two narrowed role policies (no action change) plus two deferred CI policies."""

    def narrowed(name: str) -> dict:
        old = _policy(_doc_stmt("Lake", ["a", "b"]), _doc_stmt("Dsn", ["c"]))
        return _rc(
            "aws_iam_role_policy", ["update"], {"policy": old}, {"policy": _policy(_doc_stmt("Lake", ["a"]))}, address=name
        )

    plan = _deferred_plan()
    plan["resource_changes"] += [narrowed("aws_iam_role_policy.reader"), narrowed("aws_iam_role_policy.writer")]
    digest = build_digest(plan, frozenset({"Lake", "Dsn", "s3:GetObject"}))
    assert digest.count("Sids +[] -[Dsn]") == 2
    assert "Resource 2->1" in digest
    assert "deferred data read: data.aws_iam_policy_document.ci_branch" in digest


@pytest.mark.parametrize("literals", [None, frozenset({"value", "aws_ssm_parameter", "SecureString"})])
def test_secure_string_parameter_value_is_named_never_printed(literals: frozenset | None) -> None:
    sentinel = "postgresql://sentinel-user:sentinel-pass@sentinel-host/db"  # pragma: allowlist secret
    after = {"name": "/secret/example", "type": "SecureString", "value": sentinel, "tier": "Standard"}
    plan = {"resource_changes": [_rc("aws_ssm_parameter", ["create"], None, after)]}
    digest = build_digest(plan, literals) if literals else build_digest(plan)
    assert "aws_ssm_parameter.example" in digest
    assert "sentinel" not in digest
    assert "value" in digest


def test_main_refuses_when_the_scripts_package_resolves_outside_the_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    import scripts.ci.terraform_plan_digest as digest

    monkeypatch.setattr(digest, "_ROOT", tmp_path)
    assert main([str(tmp_path / "plan.json")]) == 2
    assert "did not resolve under the repo root" in capsys.readouterr().err
