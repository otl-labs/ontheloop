"""scripts/ci/terraform_bootstrap_apply_gate.py and its classify and screen modules (PLAN-bootstrap-ci-gated-apply VP 8).

Synthetic plan JSON drives every classify, preview, check and prove path. Account digits are a placeholder; no real
ARN or account id appears here, and no output may carry an ARN or a twelve-digit run.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from scripts.ci import terraform_bootstrap_apply_classify as cls
from scripts.ci import terraform_bootstrap_apply_gate as gate
from scripts.ci import terraform_bootstrap_apply_screen as screen

ROOT = Path(__file__).resolve().parents[1]
ACCT = "123456789012"
BOUNDARY = f"arn:aws:iam::{ACCT}:policy/agent-platform-github-ci-apply-boundary"
HCL = (ROOT / "terraform/bootstrap/bootstrap_ci_apply.tf").read_text(encoding="utf-8")


def doc(*statements: dict[str, Any]) -> str:
    return json.dumps({"Version": "2012-10-17", "Statement": list(statements)})


def allow(action: Any, resource: Any, sid: str = "S", **extra: Any) -> dict[str, Any]:
    return {"Sid": sid, "Effect": "Allow", "Action": action, "Resource": resource, **extra}


def row(
    address: str, actions: list[str], after: dict[str, Any] | None, before: dict[str, Any] | None = None, **extra: Any
) -> dict:
    change: dict[str, Any] = {"actions": actions, "before": before, "after": after, "after_unknown": extra.pop("unknown", {})}
    change.update(extra.pop("change", {}))
    return {"address": address, "mode": "managed", "type": address.split(".")[-2], "change": change, **extra}


def role_row(name: str = "agent-platform-new", actions: list[str] | None = None, **after: Any) -> dict:
    body = {"name": name, "permissions_boundary": BOUNDARY, "assume_role_policy": doc(), **after}
    return row(f"aws_iam_role.{name.replace('-', '_')}", actions or ["create"], body, body if actions == ["update"] else None)


def policy_row(kind: str, name: str, before: str | None, after: str, key: str = "role", **extra: Any) -> dict:
    addr = f"{kind}.{name.replace('-', '_')}"
    base = {key: name, "name": name} if kind == "aws_iam_policy" else {"role": name, "name": "inline"}
    updating = before is not None
    return row(
        addr,
        ["update"] if updating else ["create"],
        {**base, "policy": after},
        {**base, "policy": before} if updating else None,
        **extra,
    )


def plan(*changes: dict, prior: list[dict] | None = None, cfg: list[dict] | None = None) -> dict:
    return {
        "resource_changes": list(changes),
        "prior_state": {"values": {"root_module": {"resources": prior or []}}},
        "configuration": {"root_module": {"resources": cfg or []}},
    }


def prior(rtype: str, address: str, **values: Any) -> dict[str, Any]:
    return {"address": address, "type": rtype, "values": values}


PRIOR_PROTECTED = [
    prior("aws_cloudwatch_log_group", "aws_cloudwatch_log_group.hb", name="platform-security-hb",
          arn=f"arn:aws:logs:eu-west-2:{ACCT}:log-group:platform-security-hb"),
    prior("aws_cloudwatch_metric_alarm", "aws_cloudwatch_metric_alarm.a", name="platform-security-a",
          arn=f"arn:aws:cloudwatch:eu-west-2:{ACCT}:alarm:platform-security-a"),
    prior("aws_iam_role", "aws_iam_role.admin", name="PlatformAdmin", arn=f"arn:aws:iam::{ACCT}:role/PlatformAdmin"),
]  # fmt: skip


def reasons(p: dict, index: int = 0) -> list[str]:
    pl = cls.Plan(p)
    return cls.protected_reasons(pl, pl.changes[index], cls.protected_arns(pl))


def rows_of(p: dict) -> list[dict[str, Any]]:
    return cls.build_rows(cls.Plan(p))


def test_new_boundary_role_is_ci_writable() -> None:
    assert reasons(plan(role_row())) == []


@pytest.mark.parametrize(
    "name", ["PlatformAdmin", "PlatformDev", "github-settings-apply", "bootstrap-apply", "agent-platform-session-guard", "x"]
)
def test_roles_outside_the_agent_prefix_or_session_are_protected(name: str) -> None:
    assert "name" in reasons(plan(role_row(name)))


def test_boundary_policy_user_and_alarm_are_protected() -> None:
    boundary = policy_row("aws_iam_policy", "agent-platform-github-ci-apply-boundary", None, doc())
    assert "name" in reasons(plan(boundary))
    assert "type" in reasons(plan(row("aws_iam_user.u", ["create"], {"name": "u"})))
    assert "type" in reasons(plan(row("aws_cloudwatch_metric_alarm.a", ["update"], {"alarm_name": "platform-security-a"})))


def test_role_without_boundary_or_with_inline_grants_is_protected() -> None:
    assert "boundary" in reasons(plan(role_row(permissions_boundary="")))
    assert "inline_or_managed_grant" in reasons(plan(role_row(inline_policy=[{"name": "x"}])))
    assert "inline_or_managed_grant" in reasons(plan(role_row(managed_policy_arns=["arn:aws:iam::aws:policy/X"])))


@pytest.mark.parametrize("actions", [["delete"], ["delete", "create"], ["create", "delete"], ["read"]])
def test_destroy_replace_and_read_are_protected(actions: list[str]) -> None:
    assert "action" in reasons(plan(role_row(actions=actions)))


def test_import_and_moved_rows_are_protected() -> None:
    assert "action" in reasons(
        plan(
            row(
                "aws_iam_role.agent_platform_new",
                ["create"],
                {"name": "agent-platform-new"},
                change={"importing": {"id": "x"}},
            )
        )
    )
    moved = role_row()
    moved["previous_address"] = "aws_iam_role.old"
    assert "action" in reasons(plan(moved))


def test_unknown_name_without_reference_is_protected_and_references_resolve() -> None:
    unresolved = row("aws_iam_role.r", ["create"], {"permissions_boundary": BOUNDARY}, unknown={"name": True})
    assert "name" in reasons(plan(unresolved))
    new_role = role_row("agent-platform-new")
    inline = row(
        "aws_iam_role_policy.p",
        ["create"],
        {"policy": doc(allow("logs:Describe*", "*"))},
        unknown={"role": True},
    )
    attach = row("aws_iam_role_policy_attachment.a", ["create"], {}, unknown={"role": True, "policy_arn": True})
    pol = policy_row("aws_iam_policy", "agent-platform-pol", None, doc(allow("logs:Describe*", "*")))
    role_refs = {"references": ["aws_iam_role.agent_platform_new.name", "aws_iam_role.agent_platform_new"]}
    pol_refs = {"references": ["aws_iam_policy.agent_platform_pol.arn", "aws_iam_policy.agent_platform_pol"]}
    cfg = [
        {"address": "aws_iam_role_policy.p", "expressions": {"role": role_refs}},
        {"address": "aws_iam_role_policy_attachment.a", "expressions": {"role": role_refs, "policy_arn": pol_refs}},
    ]
    p = plan(new_role, inline, attach, pol, cfg=cfg)
    pl = cls.Plan(p)
    assert all(cls.protected_reasons(pl, rc, []) == [] for rc in pl.changes)


def test_aws_managed_attachment_is_refused() -> None:
    attach = row(
        "aws_iam_role_policy_attachment.a",
        ["create"],
        {"role": "agent-platform-new", "policy_arn": "arn:aws:iam::aws:policy/AdministratorAccess"},
    )
    assert "name" in reasons(plan(attach))


def test_managed_policy_attached_outside_bounded_roles_is_protected() -> None:
    update = policy_row(
        "aws_iam_policy",
        "agent-platform-shared",
        doc(allow("logs:Describe*", "*")),
        doc(allow("logs:Describe*", "*", sid="T")),
    )
    attached = [
        prior("aws_iam_role_policy_attachment", "aws_iam_role_policy_attachment.dev", role="PlatformDev",
              policy_arn=f"arn:aws:iam::{ACCT}:policy/agent-platform-shared"),
    ]  # fmt: skip
    assert "attached_outside_bounded_roles" in reasons(plan(update, prior=attached))
    assert "attached_outside_bounded_roles" not in reasons(plan(update))


def test_trust_update_only_on_trust_writable_roles() -> None:
    ok = role_row(cls.APPLY_ROLE, actions=["update"])
    ok["change"]["before"] = {**ok["change"]["after"], "assume_role_policy": doc(allow("sts:AssumeRole", "*"))}
    assert "trust_write" not in reasons(plan(ok))
    other = role_row("agent-platform-other", actions=["update"])
    other["change"]["before"] = {**other["change"]["after"], "assume_role_policy": doc(allow("sts:AssumeRole", "*"))}
    assert "trust_write" in reasons(plan(other))


def test_provisioner_makes_a_row_protected() -> None:
    cfg = [{"address": "aws_iam_role.agent_platform_new", "provisioners": [{"type": "local-exec"}]}]
    assert "provisioner" in reasons(plan(role_row(), cfg=cfg))


APPLY_INLINE = f"role/{cls.APPLY_ROLE}"


def grant(action: Any, resource: Any = "*", **extra: Any) -> dict:
    before = doc(allow("logs:Describe*", "*", sid="Base"))
    after = doc(allow("logs:Describe*", "*", sid="Base"), allow(action, resource, sid="New", **extra))
    return policy_row("aws_iam_role_policy", cls.APPLY_ROLE, before, after)


@pytest.mark.parametrize(
    ("action", "resource", "why"),
    [
        ("iam:UpdateAssumeRolePolicy", f"arn:aws:iam::{ACCT}:role/PlatformAdmin", "iam_write_outside_agent_platform"),
        ("iam:PutRolePolicy", "*", "iam_write_outside_agent_platform"),
        ("*", "*", "iam_write_outside_agent_platform"),
        ("iam:*", "*", "iam_write_outside_agent_platform"),
        ("IAM:PutRolePolicy", "*", "iam_write_outside_agent_platform"),
        ("secretsmanager:GetSecretValue", "*", "secret_read"),
        ("secretsmanager:*", "*", "secret_read"),
        ("secretsmanager:Get*", "*", "secret_read"),
        ("ssm:*", "*", "secret_read"),
        ("ssm:GetParameter", f"arn:aws:ssm:eu-west-2:{ACCT}:parameter/secret/x", "secret_read"),
        ("s3:GetObject", cls.STATE_OBJECT_ARNS[0], "state_read"),
        ("s3:PutObject", cls.STATE_OBJECT_ARNS[0], "write_reaches_protected"),
        ("logs:CreateLogStream", f"arn:aws:logs:eu-west-2:{ACCT}:log-group:platform-security-*", "write_reaches_protected"),
        (
            "cloudwatch:PutMetricAlarm",
            f"arn:aws:cloudwatch:eu-west-2:{ACCT}:alarm:platform-security-*",
            "write_reaches_protected",
        ),
        ("ec2:RunInstances", "*", "write_reaches_protected"),
    ],
)
def test_grants_that_reach_the_protected_set_or_secrets_are_refused(action: str, resource: str, why: str) -> None:
    assert why in reasons(plan(grant(action, resource), prior=PRIOR_PROTECTED))


def test_not_action_and_not_resource_allows_are_refused() -> None:
    for extra in ({"NotResource": "arn:x"}, {"NotAction": ["iam:Get*"]}):
        stmt = {"Sid": "N", "Effect": "Allow", **extra}
        if "NotAction" in extra:
            stmt["Resource"] = "*"
        else:
            stmt["Action"] = "logs:Describe*"
        before, after = doc(), doc(stmt)
        assert "not_action_or_resource" in reasons(plan(policy_row("aws_iam_role_policy", cls.APPLY_ROLE, before, after)))


def test_reads_on_star_and_agent_platform_iam_writes_are_accepted() -> None:
    assert reasons(plan(grant("logs:Describe*", "*"), prior=PRIOR_PROTECTED)) == []
    ok = grant("iam:PutRolePolicy", f"arn:aws:iam::{ACCT}:role/agent-platform-x")
    assert reasons(plan(ok, prior=PRIOR_PROTECTED)) == []


def test_s3_resource_parts_print_without_account_digits() -> None:
    p = plan(grant("s3:GetObject", f"arn:aws:s3:::trail-{ACCT}/AWSLogs/x"))
    r = rows_of(p)[0]
    assert "trail-<account>/AWSLogs/x" in r["resources"] and ACCT not in json.dumps(r)
    plain = rows_of(plan(grant("s3:GetObject", "arn:aws:s3:::bkt/key/*")))[0]
    assert plain["resources"] == ["bkt/key/*"]


def test_iam_resources_print_as_their_name_part() -> None:
    r = rows_of(plan(grant("iam:PutRolePolicy", f"arn:aws:iam::{ACCT}:role/agent-platform-x")))[0]
    assert r["resources"] == ["role/agent-platform-x"] and r["allows"] == ["iam:PutRolePolicy"]


def test_narrows_flag_on_the_apply_role_only() -> None:
    assert "NARROWS" in rows_of(plan(grant("logs:Describe*", "*")))[0]["flags"]
    other = policy_row("aws_iam_role_policy", "agent-platform-other", doc(), doc(allow("logs:Describe*", "*")))
    assert "NARROWS" not in rows_of(plan(other))[0]["flags"]
    attach = row(
        "aws_iam_role_policy_attachment.a",
        ["create"],
        {"role": cls.APPLY_ROLE, "policy_arn": f"arn:aws:iam::{ACCT}:policy/agent-platform-p"},
    )
    assert "NARROWS" in rows_of(plan(attach))[0]["flags"]


def test_grant_shape_flags_removed_deny_dropped_condition_and_widened_resource() -> None:
    deny = {"Sid": "D", "Effect": "Deny", "Action": "iam:*", "Resource": "*"}
    cond = allow("logs:Describe*", "arn:a", sid="C", Condition={"StringEquals": {"k": "v"}})
    base = doc(deny, cond)
    removed = policy_row("aws_iam_role_policy", "agent-platform-r", base, doc(cond))
    dropped = policy_row("aws_iam_role_policy", "agent-platform-r", base, doc(deny, allow("logs:Describe*", "arn:a", sid="C")))
    widened = policy_row(
        "aws_iam_role_policy",
        "agent-platform-r",
        base,
        doc(deny, allow("logs:Describe*", ["arn:a", "*"], sid="C", Condition={"StringEquals": {"k": "v"}})),
    )
    for p in (removed, dropped, widened):
        assert "GRANT_SHAPE" in rows_of(plan(p))[0]["flags"]


def test_two_instances_of_one_for_each_resource_are_one_protected_group() -> None:
    a = row('aws_iam_role.r["k1"]', ["create"], {"name": "agent-platform-a", "permissions_boundary": BOUNDARY})
    b = row('aws_iam_role.r["k2"]', ["create"], {"name": "agent-platform-b", "permissions_boundary": BOUNDARY})
    rows = rows_of(plan(a, b))
    assert len(rows) == 1 and rows[0]["address"] == "aws_iam_role.r" and rows[0]["count"] == 2
    assert "instance_key" in rows[0]["protected"] and "k1" not in json.dumps(rows) and "k2" not in json.dumps(rows)
    assert "x2" in gate.table(rows, "t")


def test_verdicts_and_preview_outputs(tmp_path: Path) -> None:
    out = tmp_path / "gh"
    assert gate.preview(plan())[0] == 0 and "verdict=NO_CHANGES" in gate.preview(plan())[2]
    rc, text, gh = gate.preview(plan(role_row()), "push")
    assert rc == 0 and "verdict=REVIEWABLE" in gh and "has_changes=true" in gh and "NEW_ROLE" in text
    assert "1 changes; 0 privilege-affecting" in text or "privilege-affecting" in text
    protected = plan(role_row("PlatformAdmin"))
    rc, text, gh = gate.preview(protected, "push")
    assert rc == 0 and "::warning::CLOUDSHELL_REQUIRED 1" in text and "has_changes=false" in gh
    assert gate.preview(protected, "workflow_dispatch")[0] == 1
    assert not re.search(r"arn:aws|[0-9]{12}", text) and not out.exists()


def test_check_requires_every_change_reviewed_and_ci_writable() -> None:
    p = plan(role_row())
    reviewed = gate.reviewed_rows(rows_of(p))
    assert gate.check(p, reviewed)[0] == 0
    assert gate.check(p, [])[0] == 1 and "unreviewed" in gate.check(p, [])[1]
    drifted = [{**reviewed[0], "changed": []}]
    assert gate.check(p, drifted)[0] == 1
    other = [{**reviewed[0], "allows": ["iam:*"]}]
    assert gate.check(p, other)[0] == 1
    assert "protected" in gate.check(plan(role_row("PlatformAdmin")), reviewed)[1]


def test_simulate_owed_for_the_apply_role_only() -> None:
    p = plan(grant("logs:Describe*", "*"))
    out = gate.check(p, gate.reviewed_rows(rows_of(p)))[1]
    assert "SIMULATE_OWED" in out and "NARROWS_PRESENT" in out
    q = plan(role_row())
    assert "SIMULATE_OWED" not in gate.check(q, gate.reviewed_rows(rows_of(q)))[1]


def test_prove_needs_an_empty_plan() -> None:
    assert gate.prove(plan()) == (0, "PROVE_OK\n")
    assert gate.prove(plan(role_row()))[0] == 1


def test_unreadable_plan_fails_closed(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    bad = tmp_path / "p.json"
    bad.write_text("{}", encoding="utf-8")
    rc = gate.main(["prove", "--plan-json", str(bad)])
    assert rc == 1 and "BOOTSTRAP_APPLY_UNREADABLE" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("workflow", "event", "status", "expect_blocked"),
    [
        ("terraform-apply-sandbox.yml", "push", "waiting", True),
        ("reconcile.yml", "workflow_dispatch", "in_progress", True),
        ("terraform-apply-sandbox.yml", "pull_request", "queued", False),
        ("reconcile.yml", "pull_request_target", "in_progress", False),
    ],
)
def test_pending_runs(workflow: str, event: str, status: str, expect_blocked: bool) -> None:
    def fetch(wf: str, st: str) -> dict:
        if wf == workflow and st == status:
            return {"workflow_runs": [{"id": 42, "event": event, "head_commit": {"message": "secret text"}}]}
        return {"workflow_runs": []}

    found = gate.pending_runs(fetch)
    assert bool(found) is expect_blocked and (not found or found == [("42", status)])
    assert gate.pending_runs(lambda *_: {}) == []


def test_trust_writable_roles_match_hcl() -> None:
    m = re.search(r'Sid\s*=\s*"IAMRoleTrustWrite".*?Resource\s*=\s*\[(.*?)\]', HCL, re.S)
    assert m is not None
    names = set(re.findall(r":role/([\w*-]+)\"", m.group(1)))
    assert names == set(cls.TRUST_WRITABLE_ROLES)


def test_protected_patterns_match_hcl_denies() -> None:
    found: set[str] = set()
    for sid in ("DenyProtectedRoleWrite", "DenyProtectedPolicyWrite"):
        m = re.search(rf'Sid\s*=\s*"{sid}".*?Resource\s*=\s*\[(.*?)\]', HCL, re.S)
        assert m is not None
        found |= set(re.findall(r":(?:role|policy)/[\w*-]+", m.group(1)))
    assert {f.lstrip(":") for f in found} == set(cls.PROTECTED_NAME_PATTERNS)


def test_state_object_arns_match_hcl_locals() -> None:
    assert "agent-platform-bootstrap-tfstate" in (ROOT / "terraform/bootstrap/main.tf").read_text(encoding="utf-8")
    prefixes = ("arn:aws:s3:::agent-platform-bootstrap-tfstate/", "arn:aws:s3:::agent-platform-bootstrap-tfstate-sit/")
    assert all(a.startswith(prefixes) and "terraform.tfstate" in a for a in cls.STATE_OBJECT_ARNS)


def tree(**overrides: str | None) -> dict[str, str]:
    files = {p.name: p.read_text(encoding="utf-8") for p in (ROOT / "terraform/bootstrap").iterdir() if p.is_file()}
    for name, text in overrides.items():
        files.pop(name, None) if text is None else files.__setitem__(name, text)
    return files


def rules(files: dict[str, str]) -> set[str]:
    return {f[0] for f in screen.screen_files(files)}


def test_the_real_tree_passes_the_screen() -> None:
    assert screen.screen_root(ROOT / "terraform/bootstrap") == []
    assert rules(tree()) == set()


@pytest.mark.parametrize(
    ("body", "rule"),
    [
        ('resource "aws_iam_role" "x" {\n provisioner "local-exec" {\n command = "x"\n }\n}\n', "provisioner_block"),
        ('module "m" {\n source = "./x"\n}\n', "module_block"),
        ('data "external" "e" {\n program = ["x"]\n}\n', "data_source"),
        ('data "http" "e" {\n url = "x"\n}\n', "data_source"),
        ('provider "aws" {\n region = "x"\n}\n', "provider_block"),
        ('provider "google" {\n region = "x"\n}\n', "provider_block"),
        ('provider "aws" {\n region = "x"\n endpoints {\n s3 = "x"\n }\n}\n', "provider_argument"),
        ('provider "aws" {\n region = "x"\n assume_role {\n role_arn = "x"\n }\n}\n', "provider_argument"),
        ('terraform {\n required_providers {\n google = { source = "hashicorp/google" }\n }\n}\n', "required_providers_entry"),
        ('terraform {\n backend "local" {}\n}\n', "backend"),
        ('locals {\n a = file("x")\n}\n', "file_function"),
        ('locals {\n a = templatefile("x", {})\n}\n', "file_function"),
        ('locals {\n a = "${file("x")}"\n}\n', "file_function"),
        ('locals {\n a = <<-EOT\n ${file("x")}\n EOT\n}\n', "heredoc_interpolation"),
        ('locals {\n a = <<-EOT\n { unbalanced\n EOT\n}\nmodule "m" {}\n', "module_block"),
    ],
)
def test_screen_refuses_each_construct(body: str, rule: str) -> None:
    assert rule in rules(tree(**{"extra.tf": body}))


@pytest.mark.parametrize(
    "name",
    ["main.tf.json", "override.tf", "x_override.tf", "terraform.tfvars", "x.auto.tfvars", "stray.txt"],
)
def test_screen_refuses_file_shapes(name: str) -> None:
    assert rules(tree(**{name: "x"}))


def test_screen_refuses_a_subdirectory_and_a_second_lock_provider(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "main.tf").write_text("", encoding="utf-8")
    assert {f[0] for f in screen.screen_root(tmp_path)} == {"subdirectory"}
    lock = tree()[".terraform.lock.hcl"] + '\nprovider "registry.terraform.io/hashicorp/random" {\n}\n'
    assert "lock_provider" in rules(tree(**{".terraform.lock.hcl": lock}))


def test_screen_accepts_heredoc_without_interpolation_and_cli_prints_the_rule(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    ok = "locals {\n a = <<-EOT\n { not counted\n EOT\n}\n"
    assert rules(tree(**{"extra.tf": ok})) == set()
    (tmp_path / "x.tf").write_text('module "m" {}\n', encoding="utf-8")
    assert gate.main(["screen", "--root", str(tmp_path)]) == 1
    assert "SCREEN_REFUSED module_block x.tf:1" in capsys.readouterr().out


def test_interpolation_does_not_corrupt_the_screen_block_stack() -> None:
    body = 'provider "aws" {\n region = "${var.r}"\n endpoints {\n sts = "https://x"\n }\n}\n'
    assert "provider_argument" in rules(tree(**{"extra.tf": body}))
    backend = 'terraform {\n backend "s3" {\n bucket = "${var.b}"\n endpoints = {\n s3 = "x"\n }\n }\n}\n'
    assert "backend_argument" in rules(tree(**{"extra.tf": backend}))


@pytest.mark.parametrize(
    ("action", "resource", "why"),
    [
        ("s3:DeleteObject", "arn:aws:s3:::acct-trail/*", "write_reaches_protected"),
        ("s3:GetObjectVersion", cls.STATE_OBJECT_ARNS[0], "state_read"),
        ("secretsmanager:BatchGetSecretValue", "*", "secret_read"),
    ],
)
def test_trail_objects_versioned_state_reads_and_batch_secret_reads_are_refused(action: str, resource: str, why: str) -> None:
    bucket = prior("aws_s3_bucket", "aws_s3_bucket.trail", arn="arn:aws:s3:::acct-trail")
    assert why in reasons(plan(grant(action, resource), prior=[bucket]))


def test_unknown_managed_policy_arns_and_a_pure_moved_row_are_protected() -> None:
    unknown = role_row()
    unknown["change"]["after_unknown"] = {"managed_policy_arns": True}
    assert "inline_or_managed_grant" in reasons(plan(unknown))
    moved = row(
        "aws_iam_role.agent_platform_new", ["no-op"], {"name": "agent-platform-new"}, previous_address="aws_iam_role.old"
    )
    rows = rows_of(plan(moved))
    assert len(rows) == 1 and "action" in rows[0]["protected"]


def test_policy_strings_cannot_inject_workflow_commands_into_the_table() -> None:
    hostile = grant("logs:Describe*", "arn:aws:s3:::b/x\n::add-mask::y")
    text = gate.table(rows_of(plan(hostile)), "t")
    assert "\n::add-mask::" not in text and "::add-mask::" not in text.replace(": :", "")
