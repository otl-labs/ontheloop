"""rec-4741 acceptance: the digest shows a deferred policy document's planned statements instead of the opaque line.

Fixtures are real `terraform show -json` output (Terraform 1.10.5, hashicorp/aws ~> 5.0, no AWS call). Recipe, in a scratch
directory outside the repo:
  1. Write a probe root: terraform_data stand-ins for the managed resources the CI documents reference (their `.id`
     replaces the resource ARN), the documents and aws_iam_role_policy resources copied from terraform/personal with a
     non-numeric account placeholder, provider region eu-west-2 with skip_credentials_validation,
     skip_requesting_account_id and skip_metadata_api_check, and dummy AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY from the
     environment only.
  2. `terraform apply -target=<stand-ins and documents>` once (local state; nothing calls AWS), then
     `terraform plan -var gen=v2 -out=p.bin` (the stand-ins change in place, which defers the documents) and
     `terraform show -json p.bin`; `terraform show -json` of the state gives the provider JSON per document.
  3. Trim to format_version, terraform_version, resource_changes and configuration. Convert each role-policy create
     (the policies were targeted out of step 2) to the incident's in-place update: actions ["update"]; before is the
     planned after plus id "<role>:<name>" and policy set to the document's JSON from the state; after gains the same id
     and keeps policy null; after_unknown is exactly {"policy": true}. The unknown-element policy stays a create.
  4. Keep the probe HCL as source.tf.txt (no .tf suffix, so no repo scanner reads it).
"""

from __future__ import annotations

import copy
import glob
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci.terraform_plan_digest import REVIEWER_PREAMBLE, build_digest, fit_report, load_literals, main
from scripts.ci.terraform_plan_policy_rebuild import (
    REASONS,
    Incomplete,
    PolicyLinks,
    Rebuilt,
    canonical,
    load_policy_links,
    rebuild_policy,
)

ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures" / "terraform_plan_digest_deferred"
CI = FIXTURES / "ci_policies"
UNKNOWN = FIXTURES / "unknown_and_fallbacks"
DIGEST_PY = ROOT / "scripts" / "ci" / "terraform_plan_digest.py"
DOC = "data.aws_iam_policy_document."
SENTENCE = (
    "A policy shown as rebuilt from a deferred read was computed at plan time from the document's planned arguments; "
    "elements counted as unknown are decided at apply."
)


def _load(directory: Path) -> dict:
    return json.loads((directory / "plan.json").read_text(encoding="utf-8"))


def ci_policies_plan() -> dict:
    return _load(CI)


def ci_policies_literals() -> frozenset[str]:
    return load_literals([str(CI / "source.tf.txt")])


def ci_policies_links() -> PolicyLinks:
    return load_policy_links([str(CI / "source.tf.txt")])


def padded_ci_policies_plan() -> dict:
    plan = ci_policies_plan()
    for i in range(146 - len(plan["resource_changes"])):
        plan["resource_changes"].append(
            {
                "address": f"aws_inert_{i % 12:02d}.item_{i}",
                "type": f"aws_inert_{i % 12:02d}",
                "change": {"actions": ["no-op"], "before": {"k": 1}, "after": {"k": 1}},
            }
        )
    return plan


_HARMFUL_BLOCK = {
    "actions": ["iam:*"],
    "condition": [],
    "effect": "Allow",
    "not_actions": None,
    "not_principals": [],
    "not_resources": None,
    "principals": [],
    "resources": ["*"],
    "sid": "AdminEscalation",
}
_HARMFUL_FLAGS = {"actions": [False], "condition": [], "not_principals": [], "principals": [], "resources": [False]}


def harmful_ci_policies_plan() -> dict:
    plan = ci_policies_plan()
    change = _read(plan, "github_ci_branch")["change"]
    change["after"]["statement"].append(dict(_HARMFUL_BLOCK))
    change["after_unknown"]["statement"].append(dict(_HARMFUL_FLAGS))
    return plan


def harmful_ci_policies_source() -> str:
    text = (CI / "source.tf.txt").read_text(encoding="utf-8")
    head = 'data "aws_iam_policy_document" "github_ci_branch" {\n'
    block = '  statement {\n    sid       = "AdminEscalation"\n    actions   = ["iam:*"]\n    resources = ["*"]\n  }\n'
    assert head in text
    return text.replace(head, head + block, 1)


def _read(plan: dict, name: str) -> dict:
    return next(e for e in plan["resource_changes"] if e["address"] == f"{DOC}{name}")


def _policy(plan: dict, name: str) -> dict:
    return next(e for e in plan["resource_changes"] if e["address"] == f"aws_iam_role_policy.{name}")


def _line(digest: str, name: str) -> str:
    return next(ln for ln in digest.split("\n") if ln.startswith(f"- aws_iam_role_policy.{name} "))


def _rebuild(plan: dict, name: str, links: PolicyLinks) -> Rebuilt | Incomplete:
    refs = [f"{DOC}{name}.json", f"{DOC}{name}"]
    return rebuild_policy(plan, _policy(plan, name), links, refs)


def _sets(statements: list[dict]) -> list[str]:
    return sorted(json.dumps(s, sort_keys=True) for s in canonical(statements))


CI_NAMES = ("github_ci_branch", "github_ci_matrix", "github_ci_planner", "github_ci_pr")


def _provider_statements(plan: dict, name: str) -> list[dict]:
    return json.loads(_policy(plan, name)["change"]["before"]["policy"])["Statement"]


def test_ci_policies_rebuild_with_no_delta() -> None:
    plan = ci_policies_plan()
    digest = build_digest(plan, ci_policies_literals(), links=ci_policies_links())
    for name in CI_NAMES:
        count = len(_provider_statements(plan, name))
        assert _line(digest, name) == (
            f"- aws_iam_role_policy.{name} (aws_iam_role_policy) actions=[update] changed=[policy] policy: rebuilt from "
            f"deferred read {DOC}{name} ({count} statements, 0 elements unknown until apply): no action, Sid, or count delta"
        )


def test_rebuilt_statements_equal_the_provider_json() -> None:
    plan, links = ci_policies_plan(), ci_policies_links()
    for name in CI_NAMES:
        result = _rebuild(plan, name, links)
        assert isinstance(result, Rebuilt), (name, result)
        assert _sets(result.statements) == _sets(_provider_statements(plan, name))
        assert result.unknown_elements == 0


def test_dynamic_statements_are_included() -> None:
    plan = ci_policies_plan()
    result = _rebuild(plan, "github_ci_matrix", ci_policies_links())
    assert isinstance(result, Rebuilt)
    assert {"Matrixalpha", "Matrixbeta", "MatrixBase"} <= {s.get("Sid") for s in result.statements}


def test_deferred_source_chain_is_rebuilt() -> None:
    plan, links = ci_policies_plan(), ci_policies_links()
    assert _read(plan, "github_ci_planner")["action_reason"] == "read_because_config_unknown"
    result = _rebuild(plan, "github_ci_planner", links)
    assert isinstance(result, Rebuilt)
    sids = {s.get("Sid") for s in result.statements}
    assert {"SSMParameterRead", "TfstateRead", "TfplanWrite"} <= sids


def test_own_statement_replaces_sourced_statement_with_same_sid() -> None:
    plan = ci_policies_plan()
    block = _read(plan, "github_ci_matrix")["change"]["after"]["statement"][0]
    block["sid"], block["actions"] = "SSMParameterRead", ["ssm:GetParameter"]
    result = _rebuild(plan, "github_ci_matrix", ci_policies_links())
    assert isinstance(result, Rebuilt)
    assert len(result.statements) == 4
    assert result.statements[0]["Sid"] == "SSMParameterRead" and result.statements[0]["Action"] == ["ssm:GetParameter"]


def test_harmful_delta_is_shown(tmp_path: Path) -> None:
    source = tmp_path / "source.tf.txt"
    source.write_text(harmful_ci_policies_source(), encoding="utf-8")
    literals, links = load_literals([str(source)]), load_policy_links([str(source)])
    line = _line(build_digest(harmful_ci_policies_plan(), literals, links=links), "github_ci_branch")
    assert line.endswith("(19 statements, 0 elements unknown until apply): Allow +[iam:*] -[]; Sids +[AdminEscalation] -[]")
    gated = build_digest(harmful_ci_policies_plan(), ci_policies_literals(), links=ci_policies_links())
    assert "Allow +[<non-literal>] -[]; Sids +[<non-literal>] -[]" in _line(gated, "github_ci_branch")


def test_unknown_element_is_counted_and_rendered() -> None:
    plan = _load(UNKNOWN)
    literals = load_literals([str(UNKNOWN / "source.tf.txt")])
    links = load_policy_links([str(UNKNOWN / "source.tf.txt")])
    line = _line(build_digest(plan, literals, links=links), "created_policy")
    assert line.endswith(
        f"policy: rebuilt from deferred read {DOC}created_doc (1 statement, 1 element unknown until apply): "
        "Allow +[sqs:SendMessage] -[]; Sids +[CreatedStatement] -[]"
    )
    assert "\x00" not in line


def _bad_after_unknown(plan: dict) -> None:
    _read(plan, "github_ci_pr")["change"]["after_unknown"] = "bad"


def _duplicate_read(plan: dict) -> None:
    plan["resource_changes"].append(copy.deepcopy(_read(plan, "github_ci_pr")))


def _unknown_sid(plan: dict) -> None:
    _read(plan, "github_ci_pr")["change"]["after_unknown"]["statement"][0]["sid"] = True


def _unparsed_current(plan: dict) -> None:
    _policy(plan, "github_ci_pr")["change"]["before"]["policy"] = "not json"


_REASON_CASES = [
    ("malformed plan entry", CI, "github_ci_pr", "github_ci_pr", _bad_after_unknown),
    ("policy is not exactly the document's json", UNKNOWN, "transformed_policy", "transformed_doc", None),
    ("several instances", CI, "github_ci_pr", "github_ci_pr", _duplicate_read),
    ("statement list unknown", UNKNOWN, "dynamic_unknown_policy", "dynamic_unknown_doc", None),
    ("statement field unknown", CI, "github_ci_pr", "github_ci_pr", _unknown_sid),
    ("unmodelled argument", UNKNOWN, "overridden_policy", "overridden_doc", None),
    ("sourced document not resolvable", UNKNOWN, "chained_policy", "chained_doc", None),
    ("current policy unparsed", CI, "github_ci_pr", "github_ci_pr", _unparsed_current),
]


def test_reason_cases_cover_the_closed_list() -> None:
    assert [case[0] for case in _REASON_CASES] == list(REASONS) and len(REASONS) == 8


@pytest.mark.parametrize(("reason", "directory", "name", "doc", "mutate"), _REASON_CASES, ids=[c[0] for c in _REASON_CASES])
def test_incomplete_reasons_print_the_fallback_line(reason: str, directory: Path, name: str, doc: str, mutate) -> None:
    plan = _load(directory)
    if mutate:
        mutate(plan)
    literals = load_literals([str(directory / "source.tf.txt")])
    links = load_policy_links([str(directory / "source.tf.txt")])
    assert _line(build_digest(plan, literals, links=links), name) == (
        f"- aws_iam_role_policy.{name} (aws_iam_role_policy) actions=[update] changed=[policy] "
        f"policy=(known after apply; deferred data read: {DOC}{doc}; not rendered: {reason})"
    )


def test_transformed_policy_expression_is_not_rendered(tmp_path: Path) -> None:
    source = (CI / "source.tf.txt").read_text(encoding="utf-8")
    exact = "policy = data.aws_iam_policy_document.github_ci_pr.json"
    assert exact in source
    path = tmp_path / "main.tf.txt"
    path.write_text(source.replace(exact, 'policy = replace(data.aws_iam_policy_document.github_ci_pr.json, "a", "b")'))
    links = load_policy_links([str(path)])
    assert "aws_iam_role_policy.github_ci_pr" not in links.policies
    assert "aws_iam_role_policy.github_ci_branch" in links.policies
    line = _line(build_digest(ci_policies_plan(), ci_policies_literals(), links=links), "github_ci_pr")
    assert "rebuilt" not in line and line.endswith("not rendered: policy is not exactly the document's json)")


def test_lex_refusal_contributes_no_link(tmp_path: Path) -> None:
    (tmp_path / "bad.tf").write_text(
        'resource "t" "bad" {\n  policy = data.aws_iam_policy_document.x.json\n  note = <<EOT\n}\n'
    )
    (tmp_path / "good.tf").write_text('resource "t" "good" {\n  policy = data.aws_iam_policy_document.y.json\n}\n')
    links = load_policy_links([str(tmp_path)])
    assert links.policies == {"t.good": "y"}


def test_override_file_disables_links(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text('resource "t" "good" {\n  policy = data.aws_iam_policy_document.y.json\n}\n')
    assert load_policy_links([str(tmp_path)]).policies == {"t.good": "y"}
    for name in ("override.tf", "extra_override.tf", "override.tf.json", "x_override.tf.json"):
        (tmp_path / name).write_text("")
        assert load_policy_links([str(tmp_path)]) == PolicyLinks({}, {})
        (tmp_path / name).unlink()
    (tmp_path / "x_override.tf").write_text("")
    assert load_policy_links([str(tmp_path / "x_override.tf")]) == PolicyLinks({}, {})


def test_duplicate_addresses_contribute_no_link(tmp_path: Path) -> None:
    (tmp_path / "a.tf").write_text('resource "t" "p" {\n  policy = data.aws_iam_policy_document.y.json\n}\n')
    (tmp_path / "b.tf").write_text('resource "t" "p" {\n  policy = data.aws_iam_policy_document.z.json\n}\n')
    (tmp_path / "c.tf").write_text('data "aws_iam_policy_document" "d" {\n}\ndata "aws_iam_policy_document" "d" {\n}\n')
    links = load_policy_links([str(tmp_path)])
    assert links.policies == {} and "d" not in links.documents


def test_source_list_must_be_exact_references(tmp_path: Path) -> None:
    (tmp_path / "a.tf").write_text(
        'data "aws_iam_policy_document" "multi" {\n  source_policy_documents = [\n    data.aws_iam_policy_document.a.json,\n'
        "    data.aws_iam_policy_document.b.minified_json,\n  ]\n}\n"
        'data "aws_iam_policy_document" "mixed" {\n'
        "  source_policy_documents = [data.aws_iam_policy_document.a.json, local.x]\n}\n"
    )
    links = load_policy_links([str(tmp_path)])
    assert links.documents == {"multi": ["a", "b"]}


def test_no_links_keeps_todays_lines() -> None:
    digest = build_digest(ci_policies_plan(), ci_policies_literals())
    for name in CI_NAMES:
        assert _line(digest, name) == (
            f"- aws_iam_role_policy.{name} (aws_iam_role_policy) actions=[update] changed=[policy] "
            f"policy=(known after apply; deferred data read: {DOC}{name})"
        )


def test_links_never_change_known_policy_lines() -> None:
    plans = [
        json.load(open(p, encoding="utf-8"))
        for p in sorted(glob.glob(str(ROOT / "tests/fixtures/terraform_apply_guard/*.json")))
    ]
    plans.append(
        {
            "resource_changes": [
                {
                    "address": "aws_iam_role_policy.known",
                    "type": "aws_iam_role_policy",
                    "change": {
                        "actions": ["update"],
                        "before": {
                            "policy": json.dumps({"Statement": [{"Sid": "A", "Effect": "Allow", "Action": "s3:GetObject"}]})
                        },
                        "after": {
                            "policy": json.dumps({"Statement": [{"Sid": "B", "Effect": "Allow", "Action": "s3:PutObject"}]})
                        },
                        "after_unknown": {},
                    },
                }
            ]
        }
    )
    literals = load_literals([str(ROOT / "terraform" / "personal")])
    links = load_policy_links([str(ROOT / "terraform" / "personal")])
    for plan in plans:
        assert build_digest(plan, literals, links=links) == build_digest(plan, literals)


def test_config_unknown_read_is_named_in_the_note() -> None:
    plan = _load(UNKNOWN)
    digest = build_digest(plan)
    assert f"deferred data read: {DOC}created_doc)" in _line(digest, "created_policy")
    unlinked = copy.deepcopy(plan)
    for res in unlinked["configuration"]["root_module"]["resources"]:
        if res["address"] == "aws_iam_role_policy.created_policy":
            res["expressions"]["policy"]["references"] = []
    line = _line(build_digest(unlinked), "created_policy")
    assert line.endswith(
        "policy=(known after apply; plan has deferred data reads "
        "(read_because_config_unknown, read_because_dependency_pending))"
    )


def test_preamble_ends_with_the_approved_sentence() -> None:
    assert REVIEWER_PREAMBLE.endswith(SENTENCE)
    assert build_digest(ci_policies_plan()).split("\n")[0].endswith(SENTENCE)


def test_sentinel_values_never_print() -> None:
    plan = ci_policies_plan()
    marker = "SENTINEL-leak"
    read = _read(plan, "github_ci_matrix")["change"]["after"]
    for block in read["statement"]:
        block["resources"] = [marker if i == 0 else v for i, v in enumerate(block["resources"] or [])] or None
        block["condition"] = [{"test": "StringEquals", "variable": marker, "values": [marker]}]
        block["principals"] = [{"type": "AWS", "identifiers": [marker]}]
    flags = _read(plan, "github_ci_matrix")["change"]["after_unknown"]["statement"]
    for flag in flags:
        flag["condition"] = [{"test": False, "variable": False, "values": [False]}]
        flag["principals"] = [{"type": False, "identifiers": [False]}]
    policy = _policy(plan, "github_ci_matrix")
    policy["change"]["before"]["policy"] = json.dumps(
        {
            "Statement": [
                {
                    "Sid": "Old",
                    "Effect": "Allow",
                    "Action": "s3:ListBucket",
                    "Resource": marker,
                    "Condition": {"Bool": {marker: marker}},
                }
            ]
        }
    )
    keyed = f'["{marker}"]'
    policy["address"] += keyed
    _read(plan, "github_ci_matrix")["address"] += keyed
    digest = build_digest(plan, ci_policies_literals(), links=ci_policies_links())
    assert "aws_iam_role_policy.github_ci_matrix[*] " in digest and "policy: rebuilt from deferred read" in digest
    assert "SENTINEL" not in digest and marker not in digest


def test_real_scale_plan_stays_within_the_byte_budget_and_renders_every_policy() -> None:
    plan, literals, links = padded_ci_policies_plan(), ci_policies_literals(), ci_policies_links()
    assert len(plan["resource_changes"]) == 146
    digest = build_digest(plan, literals, links=links)
    assert len(digest.encode("utf-8")) <= 32768
    for name in CI_NAMES:
        assert "policy: rebuilt from deferred read" in _line(digest, name)
    assert fit_report(plan, literals, links=links).startswith("Reviewer digest OPAQUE:")


def test_cli_reads_links_from_literals_from(capsys: pytest.CaptureFixture[str]) -> None:
    args = ["--literals-from", str(CI / "source.tf.txt"), str(CI / "plan.json")]
    assert main(args) == 0
    assert "policy: rebuilt from deferred read" in capsys.readouterr().out
    assert main([str(CI / "plan.json")]) == 0
    assert "policy: rebuilt" not in capsys.readouterr().out
    assert main(["--fit-check", *args]) == 4
    capsys.readouterr()
    run = subprocess.run(
        [sys.executable, "-I", str(DIGEST_PY), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd="/",
    )
    assert run.returncode == 0 and "policy: rebuilt from deferred read" in run.stdout, run.stderr


def _fallback_line(plan: dict, reason: str) -> None:
    links, literals = ci_policies_links(), ci_policies_literals()
    line = _line(build_digest(plan, literals, links=links), "github_ci_pr")
    assert line.endswith(f"; not rendered: {reason})"), line
    assert fit_report(plan, literals, links=links).startswith("Reviewer digest OPAQUE:")


def test_wholly_unknown_source_list_is_not_rendered() -> None:
    plan = ci_policies_plan()
    change = _read(plan, "github_ci_pr")["change"]
    change["after"]["source_policy_documents"] = None
    change["after_unknown"]["source_policy_documents"] = True
    _fallback_line(plan, "unmodelled argument")


@pytest.mark.parametrize("condition", ["x", {"StringEquals": "v"}, ["a"]])
def test_malformed_sourced_condition_falls_back_instead_of_raising(condition: object) -> None:
    plan = ci_policies_plan()
    sourced = json.dumps(
        {"Statement": [{"Sid": "Odd", "Effect": "Allow", "Action": "s3:Get*", "Resource": "*", "Condition": condition}]}
    )
    _read(plan, "github_ci_pr")["change"]["after"]["source_policy_documents"] = [sourced]
    _fallback_line(plan, "malformed plan entry")


def test_unknown_elements_are_counted_per_distinct_document() -> None:
    plan = _load(UNKNOWN)
    result = rebuild_policy(
        plan, _policy(plan, "created_policy"), load_policy_links([str(UNKNOWN / "source.tf.txt")]), [f"{DOC}created_doc"]
    )
    assert isinstance(result, Rebuilt) and result.unknown_elements == 1


def _flag_past_actions(plan: dict) -> None:
    _read(plan, "github_ci_pr")["change"]["after_unknown"]["statement"][0]["actions"].append(True)


def _flag_past_statements(plan: dict) -> None:
    _read(plan, "github_ci_pr")["change"]["after_unknown"]["statement"].append({"actions": True})


def _flag_past_sources(plan: dict) -> None:
    _read(plan, "github_ci_pr")["change"]["after_unknown"]["source_policy_documents"].append(True)


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (_flag_past_actions, "statement field unknown"),
        (_flag_past_statements, "statement field unknown"),
        (_flag_past_sources, "sourced document not resolvable"),
    ],
)
def test_unknown_flags_past_the_planned_list_are_not_dropped(mutate, reason: str) -> None:
    plan = ci_policies_plan()
    mutate(plan)
    _fallback_line(plan, reason)


def _minimal(statement: dict, flags: dict) -> tuple[dict, PolicyLinks]:
    block = {"actions": ["s3:Get*"], "resources": ["*"], "condition": [], "principals": [], "sid": "S", **statement}
    read = {
        "address": f"{DOC}d",
        "type": "aws_iam_policy_document",
        "action_reason": "read_because_config_unknown",
        "change": {"actions": ["read"], "after": {"statement": [block]}, "after_unknown": {"statement": [flags]}},
    }
    policy = {
        "address": "aws_iam_role_policy.p",
        "type": "aws_iam_role_policy",
        "change": {"actions": ["create"], "before": None, "after": {}, "after_unknown": {"policy": True}},
    }
    return {"resource_changes": [policy, read]}, PolicyLinks({"aws_iam_role_policy.p": "d"}, {})


@pytest.mark.parametrize(
    ("statement", "flags"),
    [
        ({"actions": []}, {"actions": [True]}),
        ({"condition": []}, {"condition": [{"values": [True]}]}),
        ({"condition": [{"test": "Bool", "variable": "v", "values": []}]}, {"condition": [{"values": [True]}]}),
        ({"principals": [{"type": "AWS", "identifiers": []}]}, {"principals": [{"identifiers": [True]}]}),
    ],
)
def test_empty_planned_lists_with_unknown_flags_are_incomplete(statement: dict, flags: dict) -> None:
    plan, links = _minimal(statement, flags)
    result = rebuild_policy(plan, plan["resource_changes"][0], links, [f"{DOC}d.json"])
    assert isinstance(result, Incomplete) and result.reason == "statement field unknown"


@pytest.mark.parametrize(
    "flags",
    [
        {"actions": {"x": True}},
        {"resources": {"0": True}},
        {"not_actions": "yes"},
        {"principals": {"0": True}},
        {"condition": {"0": True}},
        {"actions": [True], "future_attribute": [True]},
        {"future_attribute": True},
    ],
)
def test_wrongly_shaped_unknown_flags_are_incomplete(flags: dict) -> None:
    plan, links = _minimal({}, flags)
    result = rebuild_policy(plan, plan["resource_changes"][0], links, [f"{DOC}d.json"])
    assert isinstance(result, Incomplete)


@pytest.mark.parametrize("statement_flags", [[True], {"0": True}, "yes"])
def test_wrongly_shaped_statement_flags_are_incomplete(statement_flags: object) -> None:
    plan, links = _minimal({}, {})
    plan["resource_changes"][1]["change"]["after_unknown"]["statement"] = statement_flags
    result = rebuild_policy(plan, plan["resource_changes"][0], links, [f"{DOC}d.json"])
    assert isinstance(result, Incomplete)


@pytest.mark.parametrize("source_flags", [{"0": True}, {"x": True}, "yes"])
def test_wrongly_shaped_source_flags_are_incomplete(source_flags: object) -> None:
    plan, links = _minimal({}, {})
    change = plan["resource_changes"][1]["change"]
    change["after"]["source_policy_documents"] = [
        json.dumps({"Statement": [{"Effect": "Allow", "Action": "iam:*", "Resource": "*"}]})
    ]
    change["after_unknown"]["source_policy_documents"] = source_flags
    result = rebuild_policy(plan, plan["resource_changes"][0], links, [f"{DOC}d.json"])
    assert isinstance(result, Incomplete)
