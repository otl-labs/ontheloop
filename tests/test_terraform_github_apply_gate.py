"""scripts/ci/terraform_github_apply_gate.py (Decision 248, PLAN-terraform-github-gated-apply VP 8).

Synthetic plan JSON drives every preview and check path; probe-verdict is checked against the session probe's own
classifier on one shared table of responses. Sentinels stand in for reviewer ids, descriptions and any value outside
the allowlist, and must never reach stdout, GITHUB_OUTPUT or the step summary.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.ci import terraform_github_apply_gate as gate
from scripts.ci import terraform_github_plan_report as rpt
from scripts.session import deployment_approval_probe as probe

SENTINEL_ID = 987654321
SENTINEL_TEXT = "sentinel-value-never-printed"
ENV = "github_repository_environment"
POLICY = "github_repository_environment_deployment_policy"
RULESET = "github_repository_ruleset"
REPO = "github_repository"
HIDDEN = {"security_and_analysis": "hidden", "ruleset_bypass_actors": "hidden"}


def _env_after(name: str = "tf-github-apply", reviewers: list[int] | None = None, **over: Any) -> dict[str, Any]:
    after: dict[str, Any] = {
        "environment": name,
        "repository": "ontheloop",
        "can_admins_bypass": False,
        "prevent_self_review": False,
        "wait_timer": 0,
        "reviewers": [{"teams": [], "users": [SENTINEL_ID] if reviewers is None else reviewers}],
        "deployment_branch_policy": [{"protected_branches": False, "custom_branch_policies": True}],
        "id": SENTINEL_TEXT,
    }
    after.update(over)
    return after


def _rc(
    address: str,
    actions: list[str],
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    importing: bool = False,
) -> dict[str, Any]:
    change: dict[str, Any] = {"actions": actions, "before": before, "after": after, "after_unknown": {}}
    if importing:
        change["importing"] = {"id": SENTINEL_TEXT}
    return {"address": address, "mode": "managed", "type": address.split(".")[-2], "change": change}


def _plan(*rcs: dict[str, Any]) -> dict[str, Any]:
    return {"resource_changes": list(rcs)}


def _env_update(address: str = f"{ENV}.tf_github_apply", **after_over: Any) -> dict[str, Any]:
    before = _env_after(prevent_self_review=True)
    return _rc(address, ["update"], before, _env_after(**after_over))


def _env_create(address: str = f"{ENV}.tf_github_apply", name: str = "tf-github-apply") -> dict[str, Any]:
    return _rc(address, ["create"], None, _env_after(name))


def _repo_update() -> dict[str, Any]:
    return _rc(f"{REPO}.ontheloop", ["update"], {"description": "a"}, {"description": SENTINEL_TEXT})


def _bypass_only() -> dict[str, Any]:
    return _rc(
        f"{RULESET}.main_protection",
        ["update"],
        {"bypass_actors": [], "enforcement": "active"},
        {"bypass_actors": [{"actor_id": SENTINEL_ID}], "enforcement": "active"},
    )


def _reviewed(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return gate.rows(plan)


def _markers(out: str) -> list[str]:
    return [line.split()[0] for line in out.splitlines() if line]


class TestRows:
    def test_values_hold_the_allowlist_only(self) -> None:
        [row] = gate.rows(_plan(_env_create()))
        assert row["values"] == {
            "environment": "tf-github-apply",
            "can_admins_bypass": False,
            "prevent_self_review": False,
            "reviewer_count": 1,
            "protected_branches": False,
            "custom_branch_policies": True,
        }
        assert SENTINEL_TEXT not in json.dumps(row) and str(SENTINEL_ID) not in json.dumps(row)

    def test_import_is_kept_and_marked(self) -> None:
        [row] = gate.rows(_plan(_rc(f"{ENV}.x", ["no-op"], _env_after(), _env_after(), importing=True)))
        assert row["actions"] == ["no-op", "import"]

    def test_no_op_and_data_rows_are_skipped(self) -> None:
        data = {"address": "data.github_user.x", "mode": "data", "change": {"actions": ["read"]}}
        assert gate.rows(_plan(_rc(f"{ENV}.x", ["no-op"], _env_after(), _env_after()), data)) == []

    def test_non_scalar_allowlisted_value_is_dropped(self) -> None:
        [row] = gate.rows(_plan(_rc(f"{POLICY}.p", ["create"], None, {"environment": "e", "branch_pattern": ["x"]})))
        assert row["values"] == {"environment": "e", "branch_pattern": None}

    @pytest.mark.parametrize("plan", [None, [], {}, {"resource_changes": "x"}])
    def test_malformed_plan_is_unreadable(self, plan: Any) -> None:
        with pytest.raises(gate.Unreadable):
            gate.rows(plan)

    def test_two_rows_on_one_address_are_unreadable(self) -> None:
        a = _env_create(f'{ENV}.x["a"]')
        b = _env_create(f'{ENV}.x["b"]')
        with pytest.raises(gate.Unreadable):
            gate.rows(_plan(a, b))


class TestPreview:
    def test_no_changes_has_no_changes(self) -> None:
        rc, out, gh = gate.preview(_plan(), HIDDEN, [], False)
        assert rc == 0 and "has_changes=false" in gh and "reviewed=[]" in gh
        assert "No changes." in out

    def test_visibility_gated_only_has_no_changes(self) -> None:
        rc, out, gh = gate.preview(_plan(_bypass_only()), HIDDEN, [], False)
        assert rc == 0 and "verdict=VISIBILITY_GATED_ONLY" in out and "has_changes=false" in gh
        assert f"{RULESET}.main_protection" in gh

    def test_a_dispatch_input_asks_even_with_no_changes(self) -> None:
        assert "has_changes=true" in gate.preview(_plan(), HIDDEN, [], True)[2]

    def test_a_non_gate_change_has_changes(self) -> None:
        rc, out, gh = gate.preview(_plan(_repo_update()), HIDDEN, [], False)
        assert rc == 0 and "has_changes=true" in gh and "GATE CHANGE" not in out

    def test_a_named_gate_change_is_bannered(self) -> None:
        rc, out, gh = gate.preview(_plan(_env_update()), HIDDEN, [f"{ENV}.tf_github_apply"], False)
        assert rc == 0 and f"**GATE CHANGE**: `{ENV}.tf_github_apply`" in out and "has_changes=true" in gh
        assert "prevent_self_review=false" in out and "reviewer_count=1" in out

    @pytest.mark.parametrize(
        "change",
        [
            _env_update(),
            _env_create(f"{ENV}.tf_github_apply_copy", "tf-gated-apply"),
            _rc("github_actions_repository_permissions.this", ["create"], None, {"enabled": True}),
            _rc("github_workflow_repository_permissions.this", ["create"], None, {"can_approve": True}),
            _rc("github_branch_protection.main", ["create"], None, {"pattern": "main"}),
        ],
        ids=["env-update", "env-create-new-address", "actions-permissions", "workflow-permissions", "unclassified"],
    )
    def test_an_unnamed_gate_row_stops_before_asking(self, change: dict[str, Any]) -> None:
        rc, out, gh = gate.preview(_plan(change), HIDDEN, [], True)
        assert rc == 1 and gh == ""
        assert out.splitlines()[-1] == f"TF_GITHUB_APPLY_GATE_CHANGE_UNNAMED {change['address']}"

    @pytest.mark.parametrize(
        "change",
        [
            _env_update(),
            _env_create(f"{ENV}.tf_github_apply_copy", "tf-gated-apply"),
            _rc("github_actions_repository_permissions.this", ["create"], None, {"enabled": True}),
            _rc("github_workflow_repository_permissions.this", ["create"], None, {"can_approve": True}),
            _rc("github_branch_protection.main", ["create"], None, {"pattern": "main"}),
        ],
        ids=["env-update", "env-create-new-address", "actions-permissions", "workflow-permissions", "unclassified"],
    )
    def test_a_push_run_with_an_unnamed_gate_change_ends_neutral(self, change: dict[str, Any]) -> None:
        addr = change["address"]
        rc, out, gh = gate.preview(_plan(change), HIDDEN, [], False, "push")
        assert rc == 0 and gh == "has_changes=false\n"
        lines = out.splitlines()
        assert lines[-2].startswith("TF_GITHUB_APPLY_PREVIEW verdict=") and lines[-2].endswith("has_changes=false")
        assert lines[-1].startswith("::warning::TF_GITHUB_APPLY_GATE_CHANGE_UNNAMED ")
        assert lines[-1].count(addr) == 2 and f"gate_change={addr}" in lines[-1]

    def test_a_push_run_with_mixed_rows_skips_the_apply(self) -> None:
        rc, out, gh = gate.preview(_plan(_env_update(), _repo_update()), HIDDEN, [], False, "push")
        assert rc == 0 and gh == "has_changes=false\n"

    @pytest.mark.parametrize("event", ["workflow_dispatch", "", "pull_request", "PUSH"])
    def test_a_non_push_run_with_an_unnamed_gate_change_fails_closed(self, event: str) -> None:
        change = _env_update()
        rc, out, gh = gate.preview(_plan(change), HIDDEN, [], False, event)
        assert rc == 1 and gh == ""
        assert out.splitlines()[-1] == f"TF_GITHUB_APPLY_GATE_CHANGE_UNNAMED {change['address']}"

    def test_a_push_run_with_a_dispatch_input_fails_closed(self) -> None:
        rc, _, gh = gate.preview(_plan(_env_update()), HIDDEN, [], True, "push")
        assert rc == 1 and gh == ""


class TestCheck:
    def _check(self, plan: dict[str, Any], reviewed_plan: dict[str, Any] | None = None, names: list[str] | None = None):
        return gate.check(plan, _reviewed(plan if reviewed_plan is None else reviewed_plan), names or [])

    def test_an_empty_admin_plan_applies_nothing(self) -> None:
        assert gate.check(_plan(), [], []) == (0, "TF_GITHUB_APPLY_NOTHING\n")

    def test_a_reviewed_non_gate_change_passes(self) -> None:
        assert self._check(_plan(_repo_update())) == (0, "TF_GITHUB_APPLY_CHECKED add=0 change=1 import=0 other=0\n")

    @pytest.mark.parametrize(
        ("change", "counts"),
        [
            (_env_update(), "add=0 change=1 import=0 other=0"),
            (_env_create(), "add=1 change=0 import=0 other=0"),
            (
                _rc(f"{ENV}.tf_github_apply", ["no-op"], _env_after(), _env_after(), importing=True),
                "add=0 change=0 import=1 other=0",
            ),
        ],
        ids=["update", "create", "import"],
    )
    def test_a_named_gate_change_passes_only_with_its_exact_address(self, change: dict[str, Any], counts: str) -> None:
        plan = _plan(change)
        assert self._check(plan, names=[f"{ENV}.tf_github_apply"]) == (0, f"TF_GITHUB_APPLY_CHECKED {counts}\n")
        rc, out = self._check(plan, names=[f"{ENV}.tf_github_apply_other"])
        assert rc == 1
        assert _markers(out) == ["TF_GITHUB_APPLY_GATE_CHANGE_UNNAMED", "TF_GITHUB_APPLY_GATE_CHANGE_STALE"]

    @pytest.mark.parametrize(
        "change",
        [
            _env_create(f"{ENV}.tf_github_apply_copy", "tf-gated-apply"),
            _rc("github_actions_repository_permissions.this", ["create"], None, {"enabled": True}),
        ],
        ids=["env-create-new-address", "actions-permissions"],
    )
    def test_an_unnamed_gate_create_is_refused(self, change: dict[str, Any]) -> None:
        rc, out = self._check(_plan(change))
        assert rc == 1 and _markers(out) == ["TF_GITHUB_APPLY_GATE_CHANGE_UNNAMED"]

    def test_an_unclassified_type_is_refused_unless_named(self) -> None:
        plan = _plan(_rc("github_branch_protection.main", ["create"], None, {"pattern": "main"}))
        assert _markers(self._check(plan)[1]) == ["TF_GITHUB_APPLY_GATE_CHANGE_UNNAMED"]
        assert self._check(plan, names=["github_branch_protection.main"])[0] == 0

    def test_a_reviewed_forget_row_is_counted_as_other(self) -> None:
        plan = _plan(_rc(f"{REPO}.ontheloop", ["forget"], {"name": "x"}, None))
        assert self._check(plan) == (0, "TF_GITHUB_APPLY_CHECKED add=0 change=0 import=0 other=1\n")

    def test_a_stale_gate_change_address_is_refused(self) -> None:
        rc, out = self._check(_plan(_repo_update()), names=[f"{ENV}.tf_github_apply"])
        assert rc == 1 and out == f"TF_GITHUB_APPLY_GATE_CHANGE_STALE {ENV}.tf_github_apply\n"

    @pytest.mark.parametrize(
        "change",
        [
            _rc(f"{REPO}.ontheloop", ["delete"], {"name": "x"}, None),
            _rc(f"{REPO}.ontheloop", ["delete", "create"], {"name": "x"}, {"name": "y"}),
            _rc(f"{REPO}.ontheloop", ["delete", "create"], {"name": "x"}, {"name": "y"}, importing=True),
            _rc(f"{REPO}.ontheloop", ["create", "delete"], {"name": "x"}, {"name": "y"}, importing=True),
        ],
        ids=["destroy", "replace", "import-and-replace", "import-and-create-before-destroy"],
    )
    def test_destroy_and_replace_are_refused(self, change: dict[str, Any]) -> None:
        rc, out = self._check(_plan(change))
        assert rc == 1 and _markers(out) == ["TF_GITHUB_APPLY_DESTRUCTIVE"]

    def test_a_reviewed_visibility_gated_ruleset_row_licenses_nothing(self) -> None:
        plan = _plan(_bypass_only())
        rc, out = self._check(plan)
        assert rc == 1 and out == f"TF_GITHUB_APPLY_RULESET_REFUSED {RULESET}.main_protection\n"

    def test_a_row_absent_from_the_reviewed_set_is_refused(self) -> None:
        rc, out = gate.check(_plan(_repo_update()), [], [])
        assert rc == 1 and out == f"TF_GITHUB_APPLY_UNREVIEWED {REPO}.ontheloop changed=description\n"

    def test_a_changed_name_outside_the_reviewed_names_is_refused(self) -> None:
        reviewed = _plan(_repo_update())
        applied = _plan(_rc(f"{REPO}.ontheloop", ["update"], {"description": "a"}, {"description": "b", "topics": ["t"]}))
        rc, out = self._check(applied, reviewed)
        assert rc == 1 and out == f"TF_GITHUB_APPLY_UNREVIEWED {REPO}.ontheloop changed=description,topics\n"

    def test_a_changed_allowlisted_value_is_refused(self) -> None:
        names = [f"{ENV}.tf_github_apply"]
        reviewed = _plan(_env_update(reviewers=[SENTINEL_ID]))
        rc, out = self._check(_plan(_env_update(reviewers=[])), reviewed, names)
        assert rc == 1 and _markers(out) == ["TF_GITHUB_APPLY_UNREVIEWED"]
        assert str(SENTINEL_ID) not in out

    def test_a_changed_action_is_refused(self) -> None:
        reviewed = _plan(_env_update())
        applied = _plan(_rc(f"{ENV}.tf_github_apply", ["create"], None, _env_after(prevent_self_review=False)))
        rc, out = self._check(applied, reviewed, [f"{ENV}.tf_github_apply"])
        assert rc == 1 and _markers(out) == ["TF_GITHUB_APPLY_UNREVIEWED"]

    @pytest.mark.parametrize("reviewed", [None, {}, ["x"]])
    def test_a_malformed_reviewed_set_is_unreadable(self, reviewed: Any) -> None:
        with pytest.raises(gate.Unreadable):
            gate.check(_plan(), reviewed, [])


class TestGateChange:
    def test_parses_comma_separated_addresses(self) -> None:
        assert gate.parse_gate_change(f" {ENV}.a , ,{POLICY}.b ") == [f"{ENV}.a", f"{POLICY}.b"]

    @pytest.mark.parametrize("raw", ["a;b", "a b", 'x["k"]', "$(id)"])
    def test_rejects_anything_but_an_address(self, raw: str) -> None:
        with pytest.raises(gate.Unreadable):
            gate.parse_gate_change(raw)


def _headers(extra: dict[str, str] | None = None, request_id: bool = True) -> dict[str, str]:
    return ({"x-github-request-id": "R"} if request_id else {}) | (extra or {})


_PROBE_TABLE: list[tuple[str, int, str, dict[str, str], bool | None, str]] = [
    ("approved", 204, "", _headers(), True, "APPROVED"),
    ("redirect", 302, "", _headers(), None, "PROBE_INVALID"),
    ("no-request-id", 403, "Forbidden", _headers(request_id=False), False, "PROXY_BLOCKED"),
    ("unauthorized", 401, "Bad credentials", _headers(), None, "UNAVAILABLE"),
    ("rate-limit-message", 403, "API rate limit exceeded", _headers(), False, "INCONCLUSIVE"),
    ("rate-limit-header", 403, "Forbidden", _headers({"x-ratelimit-remaining": "0"}), False, "INCONCLUSIVE"),
    ("rate-limit-429", 429, "secondary rate limit", _headers(), None, "INCONCLUSIVE"),
    ("integration-message", 403, "Resource not accessible by integration", _headers(), None, "TOKEN_LACKS_PERMISSION"),
    (
        "integration-header",
        403,
        "x",
        _headers({"x-accepted-github-permissions": "deployments=write"}),
        None,
        "TOKEN_LACKS_PERMISSION",
    ),
    ("not-reviewer-422", 422, "x", _headers(), False, "NOT_REVIEWER"),
    ("not-reviewer-403", 403, "x", _headers(), False, "NOT_REVIEWER"),
    ("forbidden-unknown-reviewer", 403, "x", _headers(), None, "INCONCLUSIVE"),
    ("server-error", 500, "x", _headers(), False, "INCONCLUSIVE"),
    ("no-response", 0, "", _headers(request_id=False), None, "PROXY_BLOCKED"),
]


class TestProbeVerdict:
    @pytest.mark.parametrize(("case", "status", "message", "headers", "can", "expected"), _PROBE_TABLE)
    def test_classify_post_agrees_with_the_session_probe(
        self, case: str, status: int, message: str, headers: dict[str, str], can: bool | None, expected: str
    ) -> None:
        session = probe._classify_post(probe._Resp(status, headers, {"message": message} if message else {}), can)
        assert gate.classify_post(status, message, headers, can) == session == expected

    def test_every_session_classifier_is_covered(self) -> None:
        assert {rule.verdict for rule in probe._CLASSIFIERS} <= {row[-1] for row in _PROBE_TABLE}

    @pytest.mark.parametrize(
        ("post", "pending_after", "verdict"),
        [
            ("APPROVED", None, "ACCEPTED"),
            ("APPROVED", True, "ACCEPTED"),
            ("TOKEN_LACKS_PERMISSION", True, "REFUSED"),
            ("NOT_REVIEWER", True, "REFUSED"),
            ("TOKEN_LACKS_PERMISSION", False, "INCONCLUSIVE"),
            ("NOT_REVIEWER", None, "INCONCLUSIVE"),
            ("PROXY_BLOCKED", True, "INCONCLUSIVE"),
            ("UNAVAILABLE", True, "INCONCLUSIVE"),
            ("PROBE_INVALID", True, "INCONCLUSIVE"),
            ("INCONCLUSIVE", True, "INCONCLUSIVE"),
        ],
    )
    def test_verdict_maps_the_post_and_the_re_read(self, post: str, pending_after: bool | None, verdict: str) -> None:
        status, message, headers, can = next((r[1], r[2], r[3], r[4]) for r in _PROBE_TABLE if r[-1] == post)
        assert gate.probe_verdict(status, message, headers, can, pending_after) == verdict

    @pytest.mark.parametrize(
        ("args", "rc", "marker"),
        [
            (["--status", "204", "--request-id", "R", "--can-approve", "true"], 1, "APPLY_APP_APPROVAL_ACCEPTED"),
            (
                [
                    "--status",
                    "403",
                    "--message",
                    "Resource not accessible by integration",
                    "--request-id",
                    "R",
                    "--pending-after",
                    "true",
                ],
                0,
                "APPLY_APP_APPROVAL_REFUSED",
            ),
            (
                ["--status", "403", "--request-id", "R", "--can-approve", "false", "--pending-after", "true"],
                0,
                "APPLY_APP_APPROVAL_REFUSED",
            ),
            (
                ["--status", "403", "--request-id", "R", "--ratelimit-remaining", "0", "--pending-after", "true"],
                1,
                "APPLY_APP_PROBE_INCONCLUSIVE",
            ),
            (["--status", "403", "--accepted-permissions", "x", "--pending-after", "true"], 1, "APPLY_APP_PROBE_INCONCLUSIVE"),
            (["--status", "000"], 1, "APPLY_APP_PROBE_INCONCLUSIVE"),
            (
                ["--status", "403", "--request-id", "R", "--can-approve", "false", "--pending-after", "unknown"],
                1,
                "APPLY_APP_PROBE_INCONCLUSIVE",
            ),
        ],
        ids=[
            "accepted",
            "refused-integration",
            "refused-not-reviewer",
            "rate-limited",
            "no-request-id",
            "no-response",
            "re-read-unknown",
        ],
    )
    def test_cli_marker_and_exit(self, capsys: pytest.CaptureFixture[str], args: list[str], rc: int, marker: str) -> None:
        assert gate.main(["probe-verdict", *args]) == rc
        assert capsys.readouterr().out.split()[0] == marker


class TestReporterLine:
    def test_the_visibility_gated_line_names_the_ci_apply_admin_plan(self) -> None:
        text = rpt.render(rpt.classify(_plan(_bypass_only()), HIDDEN), "sha", "url", 2)
        line = next(x for x in text.splitlines() if x.startswith("Visibility-gated rows"))
        assert "the CI apply's admin plan (terraform-github-apply.yml), which refuses any ruleset change" in line
        assert "operator's own plan" not in text


def _write(tmp_path: Path, name: str, data: Any) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


class TestCli:
    def _preview(self, tmp_path: Path, plan: dict[str, Any], *extra: str) -> tuple[int, Path]:
        out = tmp_path / "gh_output"
        vis = _write(tmp_path, "vis.json", {"security_and_analysis": "false", "ruleset_bypass_actors": "false"})
        rc = gate.main(
            [
                "preview",
                "--plan-json",
                _write(tmp_path, "plan.json", plan),
                "--visibility",
                vis,
                "--github-output",
                str(out),
                *extra,
            ]
        )
        return rc, out

    def test_a_push_preview_writes_only_has_changes_false(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        rc, gh = self._preview(tmp_path, _plan(_env_update()), "--event-name", "push")
        assert rc == 0 and gh.read_text(encoding="utf-8") == "has_changes=false\n"
        assert "::warning::TF_GITHUB_APPLY_GATE_CHANGE_UNNAMED" in capsys.readouterr().out

    def test_an_unreadable_plan_on_push_still_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        rc, _ = self._preview(tmp_path, {"resource_changes": "x"}, "--event-name", "push")
        assert rc == 1 and capsys.readouterr().out.startswith("TF_GITHUB_APPLY_PLAN_UNREADABLE")

    def test_sentinels_never_reach_stdout_github_output_or_summary(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        plan = _plan(
            _env_create(), _repo_update(), _rc("github_branch_protection.main", ["create"], None, {"x": SENTINEL_TEXT})
        )
        names = f"{ENV}.tf_github_apply,github_branch_protection.main"
        rc, gh = self._preview(tmp_path, plan, "--gate-change", names)
        assert rc == 0
        reviewed = gh.read_text(encoding="utf-8").splitlines()[0].removeprefix("reviewed=")
        summary = tmp_path / "summary.md"
        assert (
            gate.main(
                [
                    "check",
                    "--plan-json",
                    str(tmp_path / "plan.json"),
                    "--reviewed",
                    reviewed,
                    "--gate-change",
                    names,
                    "--summary",
                    str(summary),
                ]
            )
            == 0
        )
        printed = capsys.readouterr().out
        assert "TF_GITHUB_APPLY_CHECKED add=2 change=1 import=0 other=0" in printed
        for text in (printed, gh.read_text(encoding="utf-8"), summary.read_text(encoding="utf-8")):
            assert SENTINEL_TEXT not in text and str(SENTINEL_ID) not in text
        assert "reviewer_count=1" in summary.read_text(encoding="utf-8")

    def test_preview_writes_no_output_when_it_stops(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        rc, gh = self._preview(tmp_path, _plan(_env_update()))
        assert rc == 1 and not gh.exists()
        assert "TF_GITHUB_APPLY_GATE_CHANGE_UNNAMED" in capsys.readouterr().out

    def test_check_refusal_writes_no_summary(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        summary = tmp_path / "summary.md"
        plan = _write(tmp_path, "plan.json", _plan(_repo_update()))
        assert gate.main(["check", "--plan-json", plan, "--reviewed", "[]", "--summary", str(summary)]) == 1
        assert not summary.exists() and "TF_GITHUB_APPLY_UNREVIEWED" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "argv",
        [
            ["check", "--plan-json", "/nonexistent/plan.json", "--reviewed", "[]"],
            ["check", "--plan-json", "PLAN", "--reviewed", "not json"],
            ["check", "--plan-json", "PLAN", "--reviewed", "{}"],
            ["check", "--plan-json", "PLAN", "--reviewed", "[]", "--gate-change", "a;b"],
            ["preview", "--plan-json", "BAD", "--visibility", "/nonexistent", "--github-output", "OUT"],
        ],
        ids=["no-plan", "reviewed-not-json", "reviewed-not-list", "bad-gate-change", "malformed-plan"],
    )
    def test_unreadable_inputs_fail_closed(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], argv: list[str]) -> None:
        good = _write(tmp_path, "plan.json", _plan())
        bad = tmp_path / "bad.json"
        bad.write_text("{", encoding="utf-8")
        subs = {"PLAN": good, "BAD": str(bad), "OUT": str(tmp_path / "out")}
        assert gate.main([subs.get(a, a) for a in argv]) == 1
        assert capsys.readouterr().out.startswith("TF_GITHUB_APPLY_PLAN_UNREADABLE")
        assert not (tmp_path / "out").exists()
