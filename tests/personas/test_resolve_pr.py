"""PR resolution from GitHub-set fields only, the exactly-one rule, and the fail-closed author allowlist (Decision 131)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.personas import resolve_pr

SHA = "b" * 40
REPO = "o/r"


def _event(**over: Any) -> dict[str, Any]:
    run = {"head_sha": SHA, "head_repository": {"full_name": REPO}, "head_branch": "feat", "event": "pull_request"}
    run.update(over)
    return {"workflow_run": run}


def _row(number: int = 7, sha: str = SHA, repo: str = REPO, login: str = "dev") -> dict[str, Any]:
    return {
        "number": number,
        "draft": True,
        "head": {"sha": sha, "repo": {"full_name": repo}},
        "base": {"sha": "c" * 40, "ref": "main"},
        "user": {"login": login, "id": 9},
    }


def _fake(rows: list[dict[str, Any]], role: str | int | None = "admin", seen: list[str] | None = None):
    def request(method: str, url: str, token: str | None, body: Any = None, accept: str = "") -> tuple[int, bytes]:
        if seen is not None:
            seen.append(url)
        if "/permission" in url:
            if role is None:
                raise OSError("boom")
            if isinstance(role, int):
                return role, b"{}"
            return 200, json.dumps({"role_name": role}).encode()
        return 200, json.dumps(rows).encode()

    return request


def test_resolves_one_pr_and_ignores_pull_requests_array(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resolve_pr, "request", _fake([_row()]))
    event = _event(pull_requests=[{"number": 999}])
    out = resolve_pr.resolve(event, REPO, "t")
    assert out["resolved"] and out["number"] == 7 and out["change_id"] == f"{REPO}#7"
    assert out["head_sha"] == SHA and out["base_ref"] == "main" and out["draft"] is True
    assert out["author_login"] == "dev" and out["author_id"] == 9 and out["allowlisted"] is True


def test_non_pull_request_event_has_no_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resolve_pr, "request", _fake([_row()]))
    assert resolve_pr.resolve(_event(event="push"), REPO, "t")["resolved"] is False


@pytest.mark.parametrize("rows", [[], [_row(), _row(8)]])
def test_exactly_one_rule(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]]) -> None:
    monkeypatch.setattr(resolve_pr, "request", _fake(rows))
    assert resolve_pr.resolve(_event(), REPO, "t")["resolved"] is False


def test_stale_head_sha_and_fork_mismatch_do_not_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resolve_pr, "request", _fake([_row(sha="d" * 40)]))
    assert resolve_pr.resolve(_event(), REPO, "t")["resolved"] is False
    monkeypatch.setattr(resolve_pr, "request", _fake([_row(repo="fork/r")]))
    assert resolve_pr.resolve(_event(), REPO, "t")["resolved"] is False


def test_query_names_the_head_owner_and_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(resolve_pr, "request", _fake([_row()], seen=seen))
    resolve_pr.resolve(_event(), REPO, "t")
    assert "head=o:feat" in seen[0]


@pytest.mark.parametrize(
    ("role", "expected"),
    [("admin", True), ("maintain", True), ("write", False), ("read", False), ("none", False), (403, False), (None, False)],
)
def test_allowlist_is_a_live_role_read_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch, role: str | int | None, expected: bool
) -> None:
    monkeypatch.setattr(resolve_pr, "request", _fake([_row()], role=role))
    assert resolve_pr.resolve(_event(), REPO, "t")["allowlisted"] is expected


def test_main_writes_resolution_log_line_and_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(_event()))
    out_file = tmp_path / "out"
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
    monkeypatch.setattr(resolve_pr, "request", _fake([_row()]))
    assert resolve_pr.main([]) == 0
    assert (
        capsys.readouterr().out.strip() == f"PERSONA-VERDICT head_sha={SHA} change_id={REPO}#7 allowlisted=true override=none"
    )
    assert out_file.read_text() == "allowlisted=true\n"
    assert json.loads((tmp_path / "prepared" / "resolution.json").read_text())["number"] == 7


OVERRIDE_RUN = {
    "id": 70,
    "event": "workflow_dispatch",
    "path": resolve_pr.OVERRIDE_WORKFLOW,
    "head_sha": "d" * 40,
    "head_branch": "main",
    "head_repository": {"full_name": REPO},
}
PROVEN = {
    "run_id": 70,
    "pr": 7,
    "head_sha": SHA,
    "route": "verdict_probe.block",
    "check_run_id": 55,
    "ids_digest": "f" * 64,
    "approver": "op",
    "approved_at": "t",
    "url": "u",
}


def _override(monkeypatch: pytest.MonkeyPatch, proven: Any, pull: dict[str, Any] | None = None) -> dict[str, Any]:
    from scripts.personas import override

    monkeypatch.setattr(override, "approval", lambda repo, token, run_id: proven)
    row = {"state": "open", "head": {"sha": SHA, "repo": {"full_name": REPO}}, "base": {"sha": "c" * 40, "ref": "main"}}
    row.update(pull or {})
    monkeypatch.setattr(resolve_pr, "get_json", lambda path, token: row)
    return resolve_pr.resolve({"workflow_run": OVERRIDE_RUN}, REPO, "t")


def test_override_path_resolves_an_approved_run_with_no_author_check(monkeypatch: pytest.MonkeyPatch) -> None:
    got = _override(monkeypatch, PROVEN)
    assert got["resolved"] and got["number"] == 7 and got["head_sha"] == SHA and not got["allowlisted"]
    assert got["override"]["run_id"] == 70 and got["override"]["approver"] == "op"


@pytest.mark.parametrize(
    "pull",
    [
        {"state": "closed"},
        {"head": {"sha": "9" * 40, "repo": {"full_name": REPO}}},
        {"head": {"sha": SHA, "repo": {"full_name": "x/y"}}},
    ],
)
def test_override_path_closed_moved_or_foreign_is_unresolved(monkeypatch: pytest.MonkeyPatch, pull: dict[str, Any]) -> None:
    assert not _override(monkeypatch, PROVEN, pull)["resolved"]


def test_override_path_unproven_run_is_unresolved(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not _override(monkeypatch, None)["resolved"]
    from scripts.personas import override

    def boom(*a: Any) -> Any:
        raise override.Refused("api")

    monkeypatch.setattr(override, "approval", boom)
    assert not resolve_pr.resolve({"workflow_run": OVERRIDE_RUN}, REPO, "t")["resolved"]


def test_a_dispatch_run_of_another_path_keeps_the_no_routes_rule() -> None:
    other = {**OVERRIDE_RUN, "path": ".github/workflows/other.yml"}
    assert not resolve_pr.resolve({"workflow_run": other}, REPO, "t")["resolved"]
