"""Unit tests of the required-checks predicate (scripts.session.merge_checks), in process."""

from __future__ import annotations

from scripts.session import merge_checks as mc

REQ = ["pr-validate", "terraform-validate"]


def run(
    name: str,
    conclusion: str | None = "success",
    status: str = "completed",
    started: str | None = "2026-11-12T10:00:00Z",
    run_id: int = 1,
    app: str = "github-actions",
) -> dict[str, object]:
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "started_at": started,
        "id": run_id,
        "app": {"slug": app},
    }


def green() -> list[dict[str, object]]:
    return [run("pr-validate", run_id=1), run("terraform-validate", run_id=2)]


def judge(runs: list[dict[str, object]], required: list[str] | None = None, total: int | None = None) -> mc.Verdict:
    return mc.verdict(REQ if required is None else required, runs, len(runs) if total is None else total)


def test_all_green_is_clear() -> None:
    assert judge(green()) == mc.Verdict(mc.CLEAR)


def test_one_failure_refuses_naming_it() -> None:
    v = judge([run("pr-validate", "failure"), run("terraform-validate", run_id=2)])
    assert v.kind == mc.REFUSE and len(v.reasons) == 1 and "pr-validate" in v.reasons[0] and "failure" in v.reasons[0]


def test_other_conclusions_refuse() -> None:
    for conclusion in ("skipped", "neutral", "cancelled", "timed_out", "action_required", "stale"):
        v = judge([run("pr-validate", conclusion), run("terraform-validate", run_id=2)])
        assert v.kind == mc.REFUSE and conclusion in v.reasons[0]


def test_newer_pending_rerun_over_older_success_refuses() -> None:
    old = run("pr-validate", started="2026-11-12T10:00:00Z", run_id=1)
    for status in ("queued", "in_progress"):
        newer = run("pr-validate", None, status, started="2026-11-12T11:00:00Z", run_id=5)
        v = judge([old, newer, run("terraform-validate", run_id=2)])
        assert v.kind == mc.REFUSE and status in v.reasons[0]


def test_older_failure_under_newer_success_is_clear_and_ties_break_by_id() -> None:
    fail = run("pr-validate", "failure", started="2026-11-12T10:00:00Z", run_id=1)
    ok = run("pr-validate", started="2026-11-12T11:00:00Z", run_id=2)
    assert judge([fail, ok, run("terraform-validate", run_id=3)]).kind == mc.CLEAR
    tie_fail = run("pr-validate", "failure", started="2026-11-12T10:00:00Z", run_id=9)
    tie_ok = run("pr-validate", started="2026-11-12T10:00:00Z", run_id=8)
    assert judge([tie_ok, tie_fail, run("terraform-validate", run_id=3)]).kind == mc.REFUSE


def test_missing_run_refuses() -> None:
    v = judge([run("pr-validate")])
    assert v.kind == mc.REFUSE and "terraform-validate" in v.reasons[0]


def test_other_app_run_counts_as_missing() -> None:
    v = judge([run("pr-validate", app="some-app"), run("terraform-validate", run_id=2)])
    assert v.kind == mc.REFUSE and "pr-validate" in v.reasons[0]


def test_unknown_required_is_unavailable() -> None:
    assert judge(green(), required=[]).kind == mc.UNAVAILABLE
    assert mc.verdict(None, green(), 2).kind == mc.UNAVAILABLE


def test_partial_read_is_unavailable() -> None:
    assert judge(green(), total=150).kind == mc.UNAVAILABLE


def test_non_required_failure_is_clear() -> None:
    assert judge([*green(), run("lint", "failure", run_id=7)]).kind == mc.CLEAR


def test_contexts_from_two_rules_are_both_required() -> None:
    required = sorted({"pr-validate"} | {"terraform-validate"})
    assert judge([run("pr-validate")], required=required).kind == mc.REFUSE


def test_null_started_at_outranks_a_completed_success() -> None:
    queued = run("pr-validate", None, "queued", started=None, run_id=3)
    assert judge([run("pr-validate", run_id=1), queued, run("terraform-validate", run_id=2)]).kind == mc.REFUSE


def test_run_without_an_app_object_is_never_a_github_actions_run() -> None:
    bare = {"name": "pr-validate", "status": "completed", "conclusion": "success", "started_at": None, "id": 1}
    assert judge([bare, run("terraform-validate", run_id=2)]).kind == mc.REFUSE
