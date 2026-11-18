"""The conclusion mapping and one completed check run per route on the resolved head SHA."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.personas import post_verdict, resolve_pr

ROOT = Path(__file__).resolve().parents[2]
SHA = "e" * 40


def _out(verdict: str | None = None, **over: Any) -> str:
    body: dict[str, Any] = {"outcome": "success", "verdict": verdict} if verdict else {}
    body.update(over)
    return json.dumps(body)


def _result(route: str, stdout: str, **over: Any) -> dict[str, Any]:
    base = {"route": route, "exit_code": 0, "timed_out": False, "stdout": stdout}
    base.update(over)
    return base


@pytest.mark.parametrize(
    ("stdout", "conclusion"),
    [
        (_out("PROCEED"), "success"),
        (_out("REVISE"), "failure"),
        (json.dumps({"outcome": "failure", "failure": "input_unusable", "detail": "d"}), "failure"),
        ("not json {", "failure"),
        (json.dumps({"outcome": "success", "verdict": "BLOCK"}), "failure"),
        (json.dumps({"outcome": "success"}), "failure"),
        (json.dumps({"outcome": "maybe"}), "failure"),
    ],
)
def test_conclusion_mapping_for_the_probe(stdout: str, conclusion: str) -> None:
    route = "verdict_probe.proceed"
    assert post_verdict.judge(ROOT, route, _result(route, stdout), True)[0] == conclusion


def test_other_failure_shapes_are_failures() -> None:
    route = "verdict_probe.revise"
    good = _out("PROCEED")
    assert post_verdict.judge(ROOT, route, None, True)[1] == "no result"
    assert post_verdict.judge(ROOT, route, _result("other", good), True)[1] == "no result"
    assert post_verdict.judge(ROOT, route, _result(route, "", timed_out=True, exit_code=None), True)[1] == "timed out"
    assert post_verdict.judge(ROOT, route, {"route": route, "failure": "input_unusable"}, True)[0] == "failure"
    assert post_verdict.judge(ROOT, route, _result(route, good, exit_code=1), True)[0] == "failure"
    assert post_verdict.judge(ROOT, route, _result(route, good), False)[1] == "author not on allowlist"


def test_unmapped_verdict_values_follow_the_audit_table(monkeypatch: pytest.MonkeyPatch) -> None:
    schema = {"type": "object"}
    monkeypatch.setattr(post_verdict.execute, "schema_for", lambda *a: schema)
    for verdict, conclusion in (
        ("NO_FLAGS", "success"),
        ("FLAGS_FOUND", "success"),
        ("PROCEED", "success"),
        ("BLOCK", "failure"),
        ("REVISE", "failure"),
        ("OTHER", "failure"),
    ):
        assert post_verdict.judge(ROOT, "r", _result("r", _out(verdict)), True)[0] == conclusion


def _prepare(tmp_path: Path, routes: list[str], allowlisted: bool = True, resolved: bool = True) -> Path:
    work = tmp_path / "work"
    (work / "prepared").mkdir(parents=True)
    (work / "results").mkdir()
    res = {"resolved": resolved, "head_sha": SHA, "allowlisted": allowlisted}
    (work / "prepared" / "resolution.json").write_text(json.dumps(res))
    (work / "prepared" / "routes.json").write_text(json.dumps({"routes": routes, "plan_paths": []}))
    return work


def test_one_completed_check_per_route_on_the_resolved_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    routes = ["verdict_probe.proceed", "verdict_probe.revise"]
    work = _prepare(tmp_path, routes)
    (work / "results" / "verdict_probe.proceed.json").write_text(json.dumps(_result(routes[0], _out("PROCEED"))))
    calls: list[tuple[str, str, Any]] = []
    monkeypatch.setenv("GITHUB_SHA", "f" * 40)

    def fake(method: str, url: str, token: str | None, body: Any = None, accept: str = "") -> tuple[int, bytes]:
        calls.append((method, url, body))
        return 201, b"{}"

    monkeypatch.setattr(resolve_pr, "request", fake)
    posted = post_verdict.post_all(ROOT, work, "o/r", "t")
    assert posted == [f"persona-verdict/{r}" for r in routes]
    assert [c[1] for c in calls] == ["/repos/o/r/check-runs"] * 2
    first, second = (c[2] for c in calls)
    assert first["head_sha"] == SHA and first["status"] == "completed" and first["conclusion"] == "success"
    assert first["name"] == "persona-verdict/verdict_probe.proceed"
    assert second["conclusion"] == "failure" and second["output"]["title"] == "no result"
    assert len(first["output"]["summary"]) < 65535


def test_not_allowlisted_posts_a_failure_per_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = _prepare(tmp_path, ["verdict_probe.fail"], allowlisted=False)
    bodies: list[Any] = []
    monkeypatch.setattr(resolve_pr, "request", lambda m, u, t, b=None, accept="": bodies.append(b) or (201, b"{}"))
    post_verdict.post_all(ROOT, work, "o/r", "t")
    assert bodies[0]["conclusion"] == "failure" and bodies[0]["output"]["title"] == "author not on allowlist"


def test_unresolved_posts_nothing_and_a_missing_prepared_artifact_is_a_shell_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(resolve_pr, "request", lambda *a, **k: (_ for _ in ()).throw(AssertionError("post")))
    assert post_verdict.post_all(ROOT, _prepare(tmp_path, ["x"], resolved=False), "o/r", "t") == []
    with pytest.raises(RuntimeError):
        post_verdict.post_all(ROOT, tmp_path / "empty", "o/r", "t")


def test_post_retries_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []
    sleeps: list[float] = []

    def fail(*a: Any, **k: Any) -> tuple[int, bytes]:
        attempts.append(1)
        return 500, b""

    monkeypatch.setattr(resolve_pr, "request", fail)
    with pytest.raises(RuntimeError):
        post_verdict.post_check("o/r", "t", SHA, "n", "success", "t", "s", sleep=sleeps.append)
    assert len(attempts) == 3 and len(sleeps) == 2
    seq = iter([500, 201])
    monkeypatch.setattr(resolve_pr, "request", lambda *a, **k: (next(seq), b""))
    post_verdict.post_check("o/r", "t", SHA, "n", "success", "t", "s", sleep=lambda s: None)


BLOCK = "verdict_probe.block"
BLOCK_OUT = json.dumps(
    {
        "outcome": "success",
        "verdict": "BLOCK",
        "findings": [
            {"id": "probe:block-1", "title": "one <!-- forged", "blocking": True},
            {"id": "probe:block-2", "title": "two", "blocking": True},
        ],
    }
)


def _post_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str, stdout: str) -> tuple[Path, list[Any]]:
    work = _prepare(tmp_path, [route])
    res = json.loads((work / "prepared" / "resolution.json").read_text())
    res["number"] = 7
    (work / "prepared" / "resolution.json").write_text(json.dumps(res))
    (work / "results" / f"{route}.json").write_text(json.dumps(_result(route, stdout)))
    bodies: list[Any] = []
    monkeypatch.setattr(resolve_pr, "request", lambda m, u, t, b=None, accept="": (bodies.append((m, b)), (201, b"{}"))[1])
    monkeypatch.setenv("GITHUB_RUN_ID", "900")
    monkeypatch.setattr(post_verdict.override, "lookup", lambda *a: None)
    return work, bodies


def test_record_line_on_block_revise_and_flags_for_an_overridable_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work, bodies = _post_setup(tmp_path, monkeypatch, BLOCK, BLOCK_OUT)
    post_verdict.post_all(ROOT, work, "o/r", "t")
    body = bodies[0][1]
    first = body["output"]["text"].split("\n")[0]
    rec = post_verdict.override.parse_record(first)
    assert body["conclusion"] == "failure" and rec and rec["blocking_ids"] == ["probe:block-1", "probe:block-2"]
    assert rec["run_id"] == "900" and "&lt;!-- forged" in body["output"]["text"]
    assert body["output"]["text"].count("<!--") == 1


def test_no_record_for_proceed_or_a_failure_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for out in (_out("PROCEED"), json.dumps({"outcome": "failure", "failure": "input_unusable", "detail": "d"}), "junk {"):
        work, bodies = _post_setup(tmp_path / str(len(out)), monkeypatch, BLOCK, out)
        post_verdict.post_all(ROOT, work, "o/r", "t")
        assert "text" not in bodies[0][1]["output"]


def test_a_route_with_no_extractor_gets_no_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(post_verdict.override, "EXTRACTORS", {})
    work, bodies = _post_setup(tmp_path, monkeypatch, BLOCK, BLOCK_OUT)
    post_verdict.post_all(ROOT, work, "o/r", "t")
    assert "text" not in bodies[0][1]["output"]


APPROVED = {"run_id": 70, "approver": "op", "approved_at": "2026-11-17T15:00:00Z", "url": "https://x/70"}


def test_an_approved_lookup_posts_the_override_form_on_a_fresh_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work, bodies = _post_setup(tmp_path, monkeypatch, BLOCK, BLOCK_OUT)
    monkeypatch.setattr(post_verdict.override, "lookup", lambda *a: APPROVED)
    post_verdict.post_all(ROOT, work, "o/r", "t")
    body = bodies[0][1]
    assert body["conclusion"] == "success" and "overridden by op" in body["output"]["title"]
    assert "persona-verdict-override v1" in body["output"]["text"]


def test_a_lookup_error_posts_the_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work, bodies = _post_setup(tmp_path, monkeypatch, BLOCK, BLOCK_OUT)

    def boom(*a: Any) -> Any:
        raise post_verdict.override.Refused("api")

    monkeypatch.setattr(post_verdict.override, "lookup", boom)
    post_verdict.post_all(ROOT, work, "o/r", "t")
    assert bodies[0][1]["conclusion"] == "failure"


def _override_resolution(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    (work / "prepared").mkdir(parents=True)
    ov = {"run_id": 70, "pr": 7, "head_sha": SHA, "route": BLOCK, "check_run_id": 55, "ids_digest": "", **APPROVED}
    ov["ids_digest"] = post_verdict.override.ids_digest(["probe:block-1", "probe:block-2"])
    res = {"resolved": True, "head_sha": SHA, "allowlisted": False, "override": ov}
    (work / "prepared" / "resolution.json").write_text(json.dumps(res))
    (work / "prepared" / "routes.json").write_text(json.dumps({"routes": [], "plan_paths": []}))
    return work


def test_the_override_resolution_posts_one_new_success_run_and_never_patches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = _override_resolution(tmp_path)
    rec = {
        "route": BLOCK,
        "head_sha": SHA,
        "verdict": "BLOCK",
        "blocking_ids": ["probe:block-1", "probe:block-2"],
        "run_id": "9",
    }
    monkeypatch.setattr(post_verdict.override, "origin", lambda *a: (rec, ""))
    calls: list[Any] = []
    monkeypatch.setattr(resolve_pr, "request", lambda m, u, t, b=None, accept="": (calls.append((m, u, b)), (201, b"{}"))[1])
    assert post_verdict.post_all(ROOT, work, "o/r", "t") == [f"persona-verdict/{BLOCK}"]
    assert [c[0] for c in calls] == ["POST"] and calls[0][1] == "/repos/o/r/check-runs"
    body = calls[0][2]
    assert body["conclusion"] == "success" and body["name"] == f"persona-verdict/{BLOCK}" and body["head_sha"] == SHA
    assert (
        "op" in body["output"]["title"] and "probe:block-1" in body["output"]["summary"] and "/70" in body["output"]["summary"]
    )


def test_a_failed_recheck_posts_nothing_and_is_a_shell_fault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = _override_resolution(tmp_path)

    def refuse(*a: Any) -> Any:
        raise post_verdict.override.Refused("no record")

    monkeypatch.setattr(post_verdict.override, "origin", refuse)
    monkeypatch.setattr(resolve_pr, "request", lambda *a, **k: (_ for _ in ()).throw(AssertionError("post")))
    with pytest.raises(RuntimeError):
        post_verdict.post_all(ROOT, work, "o/r", "t")
    monkeypatch.setattr(
        post_verdict.override, "origin", lambda *a: ({"verdict": "BLOCK", "blocking_ids": ["other"], "run_id": "9"}, "")
    )
    with pytest.raises(RuntimeError):
        post_verdict.post_all(ROOT, work, "o/r", "t")
    monkeypatch.setattr(
        post_verdict.override, "origin", lambda *a: ({"verdict": "BLOCK", "blocking_ids": [], "run_id": "9"}, "")
    )
    with pytest.raises(RuntimeError):
        post_verdict.post_all(ROOT, work, "o/r", "t")
