"""Hermetic tests for scripts/ci/environment_reviewer_preflight.py (rec-4979, rec-4961). urllib is stubbed."""

from __future__ import annotations

import ast
import io
import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from scripts.ci import environment_reviewer_preflight as pre

SCRIPT = Path(pre.__file__)
TOKEN = "ghs_tokenvalue123"  # pragma: allowlist secret


class _Resp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _entry(login: str = "operator-fixture", env: str = "approved-text", state: str = "approved") -> dict[str, Any]:
    return {"user": {"login": login}, "state": state, "environments": [{"name": env}]}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_RUN_ID", "9")
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    monkeypatch.setenv("REVIEWER_LOGINS", "operator-fixture")
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    sleeps: list[float] = []
    monkeypatch.setattr(pre.time, "sleep", sleeps.append)
    return sleeps


def _serve(monkeypatch: pytest.MonkeyPatch, *responses: object) -> list[Any]:
    """Each response is a body (json-encoded), raw bytes, or an exception to raise."""
    requests: list[Any] = []
    queue = list(responses)

    def fake(request: Any, timeout: int = 0) -> _Resp:
        requests.append(request)
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, BaseException):
            raise item
        return _Resp(item if isinstance(item, bytes) else json.dumps(item).encode())

    monkeypatch.setattr(pre.urllib.request, "urlopen", fake)
    return requests


def _http(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("u", code, "m", {}, io.BytesIO(b""))  # type: ignore[arg-type]


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    rc = pre.main(list(argv))
    out = capsys.readouterr().out
    assert TOKEN not in out
    return rc, out.strip()


def test_approval_for_this_environment_passes(monkeypatch, capsys) -> None:
    requests = _serve(monkeypatch, [_entry()])
    rc, out = _run(capsys, "approvals", "--environment", "approved-text")
    assert (rc, out) == (0, "ENVIRONMENT_APPROVAL_OK environment=approved-text approver=operator-fixture")
    assert requests[0].full_url == "https://api.github.com/repos/o/r/actions/runs/9/approvals"
    assert requests[0].get_header("Authorization") == f"Bearer {TOKEN}"


def test_an_empty_approvals_list_is_refused(monkeypatch, capsys, _env) -> None:
    requests = _serve(monkeypatch, [])
    rc, out = _run(capsys, "approvals", "--environment", "approved-text")
    assert (rc, out) == (1, "ENVIRONMENT_APPROVAL_MISSING environment=approved-text entries=0")
    assert len(requests) == 2
    assert _env == [5]


def test_approval_recorded_late_is_found_on_the_single_reread(monkeypatch, capsys) -> None:
    requests = _serve(monkeypatch, [], [_entry()])
    rc, out = _run(capsys, "approvals", "--environment", "approved-text")
    assert rc == 0 and out.startswith("ENVIRONMENT_APPROVAL_OK")
    assert len(requests) == 2


@pytest.mark.parametrize(
    "entries",
    [
        [_entry(env="tf-gated-apply")],
        [_entry(state="rejected")],
        [_entry(login="someone-else")],
        [{"user": None, "state": "approved", "environments": [{"name": "approved-text"}]}],
        [{"user": {"login": "operator-fixture"}, "state": "approved", "environments": None}],
    ],
)
def test_wrong_environment_state_or_approver_is_refused(monkeypatch, capsys, entries) -> None:
    _serve(monkeypatch, entries)
    rc, out = _run(capsys, "approvals", "--environment", "approved-text")
    assert (rc, out) == (1, f"ENVIRONMENT_APPROVAL_MISSING environment=approved-text entries={len(entries)}")


def test_logins_are_split_trimmed_and_case_insensitive(monkeypatch, capsys) -> None:
    monkeypatch.setenv("REVIEWER_LOGINS", " Other ,, Operator-Fixture  third")
    _serve(monkeypatch, [_entry(login="OPERATOR-fixture")])
    rc, out = _run(capsys, "approvals", "--environment", "approved-text")
    assert rc == 0 and out.endswith("approver=OPERATOR-fixture")


def test_empty_reviewer_logins_is_refused_before_any_request(monkeypatch, capsys) -> None:
    monkeypatch.setenv("REVIEWER_LOGINS", " , ,")
    requests = _serve(monkeypatch, [_entry()])
    assert _run(capsys, "approvals", "--environment", "approved-text") == (1, "REVIEWER_LOGINS_EMPTY")
    assert requests == []


def test_malformed_name_is_refused_before_any_request(monkeypatch, capsys) -> None:
    requests = _serve(monkeypatch, [_entry()])
    assert _run(capsys, "approvals", "--environment", "a b;rm") == (1, "ENVIRONMENT_NAME_INVALID")
    assert _run(capsys, "environment", "--environment", "../x") == (1, "ENVIRONMENT_NAME_INVALID")
    assert _run(capsys, "environment", "--environment", "..") == (1, "ENVIRONMENT_NAME_INVALID")
    assert requests == []


@pytest.mark.parametrize("body", [{"a": 1}, ["not-a-dict"], b"<html>", b"not json"])
def test_wrong_shaped_or_non_json_approvals_are_unreadable(monkeypatch, capsys, body) -> None:
    _serve(monkeypatch, body)
    assert _run(capsys, "approvals", "--environment", "approved-text") == (
        1,
        "ENVIRONMENT_CHECK_UNREADABLE approvals status=none",
    )


def test_403_is_unreadable_without_retry(monkeypatch, capsys) -> None:
    requests = _serve(monkeypatch, _http(403))
    assert _run(capsys, "approvals", "--environment", "approved-text") == (
        1,
        "ENVIRONMENT_CHECK_UNREADABLE approvals status=403",
    )
    assert len(requests) == 1


def test_5xx_is_retried_with_backoff_then_refused(monkeypatch, capsys, _env) -> None:
    requests = _serve(monkeypatch, _http(502))
    assert _run(capsys, "approvals", "--environment", "approved-text") == (
        1,
        "ENVIRONMENT_CHECK_UNREADABLE approvals status=502",
    )
    assert len(requests) == 3
    assert _env == [2, 4]


def test_transport_error_is_retried_then_refused_and_recovers(monkeypatch, capsys) -> None:
    requests = _serve(monkeypatch, urllib.error.URLError("down"))
    assert _run(capsys, "approvals", "--environment", "approved-text")[1].endswith("status=none")
    assert len(requests) == 3
    _serve(monkeypatch, urllib.error.URLError("down"), _http(500), [_entry()])
    assert _run(capsys, "approvals", "--environment", "approved-text")[0] == 0


def test_approvals_404_is_unreadable(monkeypatch, capsys) -> None:
    _serve(monkeypatch, _http(404))
    assert _run(capsys, "approvals", "--environment", "approved-text")[1].endswith("approvals status=404")


def _env_body(reviewers: Any = (1,), bypass: Any = False, rule: str = "required_reviewers") -> dict[str, Any]:
    body: dict[str, Any] = {"protection_rules": [{"type": rule, "reviewers": [{"id": i} for i in reviewers]}]}
    if bypass is not None:
        body["can_admins_bypass"] = bypass
    return body


def test_gated_environment_passes_and_reports_bypass_words(monkeypatch, capsys) -> None:
    _serve(monkeypatch, _env_body(bypass=False))
    assert _run(capsys, "environment", "--environment", "approved-text", "--require-no-admin-bypass") == (
        0,
        "ENVIRONMENT_GATED_OK environment=approved-text reviewers=1 admin_bypass=false",
    )
    _serve(monkeypatch, _env_body(reviewers=(1, 2), bypass=True))
    assert _run(capsys, "environment", "--environment", "tf-gated-apply") == (
        0,
        "ENVIRONMENT_GATED_OK environment=tf-gated-apply reviewers=2 admin_bypass=true",
    )
    _serve(monkeypatch, _env_body(bypass=None))
    assert _run(capsys, "environment", "--environment", "tf-gated-apply")[1].endswith("admin_bypass=absent")


@pytest.mark.parametrize(
    "body", [{"protection_rules": []}, _env_body(reviewers=()), _env_body(rule="wait_timer"), {"protection_rules": "x"}, {}]
)
def test_environment_without_reviewers_is_ungated(monkeypatch, capsys, body) -> None:
    _serve(monkeypatch, body)
    assert _run(capsys, "environment", "--environment", "x") == (1, "ENVIRONMENT_UNGATED environment=x")


def test_non_dict_rules_are_ungated(monkeypatch, capsys) -> None:
    _serve(monkeypatch, {"protection_rules": ["junk"]})
    assert _run(capsys, "environment", "--environment", "x")[1].startswith("ENVIRONMENT_UNGATED")


@pytest.mark.parametrize("bypass", [True, None])
def test_admin_bypass_not_false_is_refused_only_under_the_flag(monkeypatch, capsys, bypass) -> None:
    _serve(monkeypatch, _env_body(bypass=bypass))
    assert _run(capsys, "environment", "--environment", "x", "--require-no-admin-bypass") == (
        1,
        "ENVIRONMENT_ADMIN_BYPASS environment=x",
    )
    _serve(monkeypatch, _env_body(bypass=bypass))
    assert _run(capsys, "environment", "--environment", "x")[0] == 0


def test_environment_404_403_and_wrong_shape(monkeypatch, capsys) -> None:
    _serve(monkeypatch, _http(404))
    assert _run(capsys, "environment", "--environment", "x") == (1, "ENVIRONMENT_MISSING environment=x")
    _serve(monkeypatch, _http(403))
    assert _run(capsys, "environment", "--environment", "x") == (1, "ENVIRONMENT_CHECK_UNREADABLE environment status=403")
    _serve(monkeypatch, [1])
    assert _run(capsys, "environment", "--environment", "x") == (1, "ENVIRONMENT_CHECK_UNREADABLE environment status=none")


def test_api_url_comes_from_the_runner(monkeypatch, capsys) -> None:
    monkeypatch.setenv("GITHUB_API_URL", "https://ghe.example/api/v3/")
    requests = _serve(monkeypatch, _env_body())
    _run(capsys, "environment", "--environment", "x")
    assert requests[0].full_url == "https://ghe.example/api/v3/repos/o/r/environments/x"


def test_source_is_stdlib_only_and_never_writes_workflow_files() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    stdlib = set(__import__("sys").stdlib_module_names)
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Import):
            assert all(a.name.split(".")[0] in stdlib for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0 and (node.module or "").split(".")[0] in stdlib
    for name in ("GITHUB_ENV", "GITHUB_PATH", "GITHUB_OUTPUT"):
        assert name not in text
