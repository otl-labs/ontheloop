"""OPAQUE fit result and --fit-check for IAM policies unknown at plan time (PLAN-pr-plan-opaque-iam-policy-check)."""

from __future__ import annotations

import glob
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci import terraform_plan_digest as digest
from scripts.ci.terraform_plan_digest import _POLICY_TYPES, DIGEST_SIZE_CAP, fit_report, main, opaque_entries

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "ci" / "terraform_plan_digest.py"
SENTINEL = "SENTINEL_DEADBEEF_77"
DEFERRED = "read_because_dependency_pending"
ANNOTATION = "::error title=Reviewer digest cannot review this plan::"
UNREADABLE = "terraform_plan_digest: UNREADABLE_PLAN the plan JSON cannot be read (detail not shown)"
FIT_LINES = (
    re.compile(r"^Reviewer digest: \d+ actionable of \d+ resource change\(s\), \d+ of \d+ bytes\.$"),
    re.compile(r"^Reviewer digest OVERFLOW: .*$"),
    re.compile(r"^Reviewer digest OPAQUE: policies unknown at plan time: [^;]+; deferred documents: .+\. The merge-time .*$"),
    re.compile(rf"^{re.escape(ANNOTATION)}(OPAQUE [0-9]+|OVERFLOW 1)$"),
)


def _rc(rtype, actions, address=None, unknown=None, reason=None, before=None, after=None) -> dict:
    entry = {
        "address": address or f"{rtype}.example",
        "type": rtype,
        "change": {"actions": actions, "before": before, "after": after, "after_unknown": {} if unknown is None else unknown},
    }
    if reason:
        entry["action_reason"] = reason
    return entry


def _doc(address: str) -> dict:
    return _rc("aws_iam_policy_document", ["read"], address=address, reason=DEFERRED)


def _plan(*entries: dict) -> dict:
    return {"resource_changes": list(entries)}


def _lambda(name: str) -> dict:
    return _rc("aws_lambda_function", ["update"], address=f"aws_lambda_function.{name}", before={"a": 1}, after={"a": 2})


REAL_SHAPE_PLANS: dict[str, dict] = {
    "rec_4718": _plan(
        *[_lambda(n) for n in ("a", "b", "c", "d")],
        _doc("data.aws_iam_policy_document.github_ci_branch"),
        _rc(
            "aws_iam_role_policy",
            ["update"],
            address="aws_iam_role_policy.github_ci_branch",
            unknown={"policy": True},
            before={"policy": "{}"},
            after={},
        ),
    ),
    "same_pr_log_group": _plan(
        _rc("aws_cloudwatch_log_group", ["create"], address="aws_cloudwatch_log_group.fn", after={"name": "fn"}),
        _rc(
            "aws_iam_role_policy",
            ["create"],
            address="aws_iam_role_policy.exec_logs",
            unknown={"policy": True, "id": True},
            after={"name": "exec_logs", "role": "exec"},
        ),
    ),
    "default_tags": _plan(
        *[
            _rc(rtype, ["update"], address=f"{rtype}.{name}", before={"tags_all": {"a": "1"}}, after={"tags_all": {"a": "2"}})
            for rtype, name in (
                ("aws_s3_bucket", "data_lake"),
                ("aws_s3_bucket", "ducklake_catalog_dr"),
                ("aws_dynamodb_table", "counters"),
                ("aws_sns_topic", "alerts"),
            )
        ],
        *[_doc(f"data.aws_iam_policy_document.{n}") for n in ("github_ci_branch", "github_ci_pr", "github_ci_planner")],
        *[
            _rc("aws_iam_role_policy", ["update"], address=f"aws_iam_role_policy.{n}", unknown={"policy": True})
            for n in ("github_ci_branch", "github_ci_pr", "github_ci_planner")
        ],
    ),
}

EXPECTED_LINES = {
    "rec_4718": (
        "Reviewer digest OPAQUE: policies unknown at plan time: aws_iam_role_policy.github_ci_branch; "
        "deferred documents: data.aws_iam_policy_document.github_ci_branch. "
        "The merge-time review cannot read them: build ARNs from names or split the PR."
    ),
    "same_pr_log_group": (
        "Reviewer digest OPAQUE: policies unknown at plan time: aws_iam_role_policy.exec_logs; deferred documents: none. "
        "The merge-time review cannot read them: build ARNs from names or split the PR."
    ),
    "default_tags": (
        "Reviewer digest OPAQUE: policies unknown at plan time: aws_iam_role_policy.github_ci_branch, "
        "aws_iam_role_policy.github_ci_planner, aws_iam_role_policy.github_ci_pr; deferred documents: "
        "data.aws_iam_policy_document.github_ci_branch, data.aws_iam_policy_document.github_ci_planner, "
        "data.aws_iam_policy_document.github_ci_pr. "
        "The merge-time review cannot read them: build ARNs from names or split the PR."
    ),
}


@pytest.mark.parametrize("name", sorted(REAL_SHAPE_PLANS))
def test_real_shape_fixtures_give_the_exact_opaque_line(name: str) -> None:
    assert fit_report(REAL_SHAPE_PLANS[name]) == EXPECTED_LINES[name]


@pytest.mark.parametrize("rtype", sorted(_POLICY_TYPES))
@pytest.mark.parametrize(
    "actions",
    [["create"], ["update"], ["delete", "create"], ["create", "delete"]],
    ids=["create", "update", "del-create", "create-del"],
)
def test_unknown_policy_trips_for_every_policy_type(rtype: str, actions: list[str]) -> None:
    plan = _plan(_rc(rtype, actions, address=f"{rtype}.p", unknown={"policy": True}))
    assert opaque_entries(plan) == ([f"{rtype}.p"], [])
    assert fit_report(plan).startswith("Reviewer digest OPAQUE: policies unknown at plan time: ")


@pytest.mark.parametrize("reason", [DEFERRED, "read_because_config_unknown"])
def test_deferred_document_read_trips(reason: str) -> None:
    plan = _plan(_rc("aws_iam_policy_document", ["read"], address="data.aws_iam_policy_document.d", reason=reason))
    assert opaque_entries(plan) == ([], ["data.aws_iam_policy_document.d"])
    assert "deferred documents: data.aws_iam_policy_document.d." in fit_report(plan)


@pytest.mark.parametrize(
    "entry",
    [
        _rc("aws_iam_role_policy", ["delete"], unknown={"policy": True}),
        _rc("aws_iam_role_policy", ["no-op"], unknown={"policy": True}),
        _rc("aws_iam_role_policy", ["update"], unknown={"policy": False}),
        _rc("aws_iam_role_policy", ["update"], unknown={}),
        _rc("aws_iam_policy_document", ["read"], address="data.aws_iam_policy_document.plain"),
        _rc("aws_lambda_function", ["update"], unknown={"policy": True}),
    ],
    ids=["delete", "no-op", "known", "no-unknown", "plain-read", "other-type"],
)
def test_non_trips_stay_fits(entry: dict) -> None:
    plan = _plan(entry)
    assert opaque_entries(plan) == ([], [])
    assert fit_report(plan).startswith("Reviewer digest: ")


def test_overflow_wins_over_opaque() -> None:
    plan = _plan(_rc("aws_iam_role_policy", ["update"], unknown={"policy": True}))
    assert fit_report(plan, cap=300).startswith("Reviewer digest OVERFLOW:")


def test_instance_keys_are_stripped_and_adversarial_keys_leak_nothing() -> None:
    plan = _plan(
        _rc("aws_iam_role_policy", ["update"], address='module.m["k1"].aws_iam_role_policy.p["x"]', unknown={"policy": True}),
        _rc("aws_iam_role_policy", ["update"], address='aws_iam_role_policy.q["x]deadbeef42[y"]', unknown={"policy": True}),
        _rc("aws_iam_role_policy", ["update"], address='aws_iam_role_policy.r["a\\"]tok3n[\\"b"]', unknown={"policy": True}),
        _rc("aws_iam_role_policy", ["update"], address="aws_iam_role_policy.s[0]", unknown={"policy": True}),
        _rc("aws_iam_role_policy", ["update"], address="aws_iam_role_policy.s[1]", unknown={"policy": True}),
    )
    policies, _ = opaque_entries(plan)
    assert policies == [
        "aws_iam_role_policy.q[*]",
        "aws_iam_role_policy.r[*]",
        "aws_iam_role_policy.s[*]",
        "module.m[*].aws_iam_role_policy.p[*]",
    ]
    text = fit_report(plan)
    assert not any(frag in text for frag in ("deadbeef42", "tok3n", "k1"))


def test_malformed_entries_are_skipped_not_raised() -> None:
    plan = _plan(
        "just a string",
        {"address": "a", "type": "aws_iam_role_policy", "change": ["list"]},
        {"address": "b", "type": "aws_iam_role_policy", "change": {"actions": "update", "after_unknown": {"policy": True}}},
        {"address": "c", "type": "aws_iam_role_policy", "change": {"actions": ["update"], "after_unknown": ["policy"]}},
    )
    assert opaque_entries(plan) == ([], [])


@pytest.mark.parametrize(
    "path",
    sorted(glob.glob(str(ROOT / "tests/fixtures/terraform_apply_guard/*.json"))),
)
def test_existing_fixtures_are_fits_with_unchanged_line(path: str, capsys: pytest.CaptureFixture[str]) -> None:
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    assert opaque_entries(plan) == ([], [])
    assert FIT_LINES[0].match(fit_report(plan))
    assert main(["--fit-check", path]) == 0
    assert FIT_LINES[0].match(capsys.readouterr().out.strip())


def test_shared_sentinel_fixture_is_opaque_since_it_holds_a_deferred_document() -> None:
    # Planned-change weakening disposition (PLAN-deferred-policy-digest acceptance_criteria[5]): the shared sentinel fixture
    # gained a deferred policy document (rec-4741), so the unchanged predicate now reports it OPAQUE instead of FITS.
    plan = json.loads((ROOT / "tests/fixtures/subagent_plan_review/plan.json").read_text(encoding="utf-8"))
    assert opaque_entries(plan) == (
        ["aws_iam_role_policy.deferred_reviewer"],
        ["data.aws_iam_policy_document.deferred_reviewer"],
    )


def _write(tmp_path: Path, plan: dict, name: str = "plan.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(plan), encoding="utf-8")
    return str(path)


def test_fit_check_exit_codes_and_annotation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--fit-check", _write(tmp_path, _plan(_lambda("a")))]) == 0
    assert capsys.readouterr().out.count("::error") == 0
    assert main(["--fit-check", _write(tmp_path, REAL_SHAPE_PLANS["rec_4718"])]) == 4
    out = capsys.readouterr().out.splitlines()
    assert out[0] == EXPECTED_LINES["rec_4718"] and out[1] == f"{ANNOTATION}OPAQUE 2"
    big = _plan(*[_rc("t", ["update"], address=f"t.r{i:04d}", before={"a": 1}, after={"a": 2}) for i in range(2000)])
    assert main(["--fit-check", _write(tmp_path, big)]) == 3
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("Reviewer digest OVERFLOW:") and out[1] == f"{ANNOTATION}OVERFLOW 1"
    assert DIGEST_SIZE_CAP > 0


def test_fit_check_output_matches_the_fixed_grammar(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for name, plan in REAL_SHAPE_PLANS.items():
        main(["--fit-check", _write(tmp_path, plan, f"{name}.json")])
        for line in capsys.readouterr().out.splitlines():
            assert any(p.match(line) for p in FIT_LINES), line


def test_fit_check_with_fit_report_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--fit-check", "--fit-report", _write(tmp_path, _plan())])
    assert exc.value.code == 2


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def test_fit_check_by_subprocess_exit_codes(tmp_path: Path) -> None:
    assert _run("--fit-check", _write(tmp_path, _plan())).returncode == 0
    result = _run("--fit-check", _write(tmp_path, REAL_SHAPE_PLANS["same_pr_log_group"]))
    assert result.returncode == 4
    assert result.stdout.splitlines()[1] == f"{ANNOTATION}OPAQUE 1"


def _unreadable_cases(tmp_path: Path) -> list[Path]:
    secret_dir = tmp_path / SENTINEL
    secret_dir.mkdir()
    bad_json = secret_dir / "plan.json"
    bad_json.write_text(f"not json {SENTINEL}", encoding="utf-8")
    bad_utf8 = secret_dir / "utf8.json"
    bad_utf8.write_bytes(b'{"x": "\xff\xfe' + SENTINEL.encode() + b'"}')
    string_entry = secret_dir / "string.json"
    string_entry.write_text(json.dumps({"resource_changes": [SENTINEL]}), encoding="utf-8")
    list_change = secret_dir / "list.json"
    list_change.write_text(
        json.dumps({"resource_changes": [{"address": SENTINEL, "type": "aws_iam_role_policy", "change": [SENTINEL]}]}),
        encoding="utf-8",
    )
    top_level = secret_dir / "top.json"
    top_level.write_text(json.dumps([SENTINEL]), encoding="utf-8")
    return [bad_json, bad_utf8, string_entry, list_change, top_level, secret_dir / "missing.json"]


def test_unreadable_plans_print_only_the_fixed_line_by_subprocess(tmp_path: Path) -> None:
    for path in _unreadable_cases(tmp_path):
        result = _run("--fit-check", str(path))
        assert result.returncode == 1, path
        assert result.stderr.strip() == UNREADABLE, path
        assert SENTINEL not in result.stdout + result.stderr and "Traceback" not in result.stderr
        assert result.stdout == ""


def test_unreadable_plans_in_process(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for path in _unreadable_cases(tmp_path):
        assert main(["--fit-check", str(path)]) == 1
        captured = capsys.readouterr()
        assert captured.err.strip() == UNREADABLE and captured.out == ""


def test_module_exports_build_digest_unchanged_signature() -> None:
    assert digest.build_digest(REAL_SHAPE_PLANS["rec_4718"]).startswith(digest.REVIEWER_PREAMBLE)
