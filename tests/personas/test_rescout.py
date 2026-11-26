"""The scout-input fingerprint carry: equal carries, moved re-runs, every doubt fails closed."""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

from scripts.personas import override, rescout, resolve_pr
from scripts.session import merge_checks

ROOT = Path(__file__).resolve().parents[2]
PLAN = "docs/plans/PLAN-persona-verdict-scout-carry.yaml"
PLAN_TEXT = (ROOT / PLAN).read_text(encoding="utf-8")
ROUTE = "decision_scout"
A, B, C = "a" * 40, "b" * 40, "c" * 40
APP_ID = 424242
RES = {"resolved": True, "allowlisted": True, "head_sha": B, "number": 7}


class Api:
    def __init__(self) -> None:
        self.plan = PLAN_TEXT
        self.sidecar_status = 404
        self.commits = [A, B]
        self.checks: dict[str, list[dict[str, Any]]] = {}
        self.fail: set[str] = set()
        self.calls: list[str] = []

    def request(self, method: str, url: str, token: str | None, body: Any = None, accept: str = "") -> tuple[int, bytes]:
        self.calls.append(url)
        if any(marker in url for marker in self.fail):
            return 500, b"{}"
        if "/contents/" in url:
            path = urllib.parse.unquote(url.split("/contents/")[1].split("?")[0])
            if path == PLAN:
                return 200, self.plan.encode()
            return self.sidecar_status, b"" if self.sidecar_status == 404 else b"not: [a sidecar"
        if "/pulls/7/commits" in url:
            return 200, json.dumps([{"sha": s} for s in self.commits] if "page=1" in url else []).encode()
        if "/check-runs" in url:
            sha = url.split("/commits/")[1].split("/")[0]
            return 200, json.dumps({"check_runs": self.checks.get(sha, [])}).encode()
        return 404, b"{}"


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Api:
    fake = Api()
    monkeypatch.setattr(resolve_pr, "request", fake.request)
    monkeypatch.setattr(override, "app_id", lambda: APP_ID)
    return fake


def _fps(plan_text: str, cites: list[str] | None = None) -> dict[str, str]:
    return rescout.fingerprints({PLAN: {"plan": plan_text, "sidecar": None}}, ROOT, cites or [])


def _run(run_id: int, text: str, app: int = APP_ID, name: str = f"persona-verdict/{ROUTE}") -> dict[str, Any]:
    return {"id": run_id, "name": name, "app": {"id": app}, "output": {"text": text}}


def _record(head: str, verdict: str = "NO_FLAGS", cites: list[str] | None = None, plan_text: str = PLAN_TEXT) -> str:
    line = rescout.render_input(ROUTE, head, verdict, cites or [], _fps(plan_text, cites), "9", None)
    return rescout.with_input(None, line)


def _decide(routes: list[str] | None = None, resolution: dict[str, Any] | None = None, **over: Any) -> rescout.Decision:
    args: dict[str, Any] = {"plan_paths": [PLAN], "unknown": False}
    args.update(over)
    return rescout.decide(
        "o/r", "t", resolution or RES, [ROUTE] if routes is None else routes, args["plan_paths"], args["unknown"], ROOT
    )


def test_equal_fingerprint_carries_the_scout_verdict_and_a_moved_one_reruns(api: Api) -> None:
    api.checks[A] = [_run(5, _record(A))]
    carried = _decide()
    assert carried.routes == [] and carried.rescout == {}
    assert carried.carried[0]["route"] == ROUTE and carried.carried[0]["verdict"] == "NO_FLAGS"
    assert carried.carried[0]["from_head"] == A and carried.carried[0]["from_check_run_id"] == 5
    api.plan = PLAN_TEXT.replace("Bind a decision-scout verdict", "Bind the decision-scout verdict now")
    moved = _decide()
    assert moved.routes == [ROUTE] and moved.rescout == {ROUTE: "moved"} and moved.carried == []
    assert moved.inputs is not None and PLAN in moved.inputs


def test_missing_untrusted_or_unreadable_record_routes_the_scout(api: Api) -> None:
    assert _decide().rescout == {ROUTE: "missing"}
    api.checks[A] = [_run(5, _record(C))]
    assert _decide().rescout == {ROUTE: "untrusted"}
    api.checks[A] = [_run(5, "no input line here")]
    assert _decide().rescout == {ROUTE: "unreadable"}
    api.checks[A] = [_run(5, _record(A).replace("NO_FLAGS", "no_flags"))]
    assert _decide().rescout == {ROUTE: "unreadable"}
    api.checks[A] = [_run(5, _record(A), app=1)]
    assert _decide().rescout == {ROUTE: "missing"}
    api.checks[A] = [_run(5, _record(A))]
    api.commits = [B, A]
    assert _decide().rescout == {ROUTE: "unreadable"}
    api.commits = [A, B]
    api.fail = {"/check-runs"}
    assert _decide().rescout == {ROUTE: "unreadable"}
    api.fail = {"/pulls/7/commits"}
    assert _decide().rescout == {ROUTE: "unreadable"}


def test_a_v1_record_that_disagrees_with_the_input_line_is_unreadable(api: Api) -> None:
    v1 = override.render_record(ROUTE, A, "BLOCK", ["x:1"], "9")
    api.checks[A] = [
        _run(5, rescout.with_input(v1, rescout.render_input(ROUTE, A, "NO_FLAGS", [], _fps(PLAN_TEXT), "9", None)))
    ]
    assert _decide().rescout == {ROUTE: "unreadable"}


def test_routes_that_cannot_carry_say_why_and_a_plain_route_makes_no_api_call(api: Api) -> None:
    assert _decide(resolution={**RES, "allowlisted": False}).rescout == {ROUTE: "not_allowlisted"}
    assert _decide(unknown=True).rescout == {ROUTE: "unknown_diff"}
    assert _decide(plan_paths=[]).rescout == {ROUTE: "no_plans"}
    assert api.calls == []
    plain = _decide(routes=["code_reviewer"])
    assert plain.routes == ["code_reviewer"] and plain.rescout == {} and api.calls == []


def test_a_bad_slug_or_plan_routes_the_scout_before_any_sidecar_fetch(api: Api) -> None:
    api.plan = PLAN_TEXT.replace('slug: "persona-verdict-scout-carry"', 'slug: "../evil"')
    assert _decide().rescout == {ROUTE: "unreadable"}
    assert not any("approved" in call for call in api.calls)
    api.plan = "a: &x 1\nb: *x\n"
    assert _decide().rescout == {ROUTE: "unreadable"}


def test_a_chain_of_carries_compares_against_the_first_record(api: Api) -> None:
    api.checks[A] = [_run(5, _record(A))]
    first = _decide().carried[0]
    line = rescout.render_input(ROUTE, B, first["verdict"], first["cites"], first["fingerprints"], "10", first["from_head"])
    api.commits = [A, B, C]
    api.checks[B] = [_run(6, rescout.with_input(None, line))]
    second = _decide(resolution={**RES, "head_sha": C}).carried[0]
    assert second["from_head"] == B and second["fingerprints"] == first["fingerprints"]
    assert rescout.parse_input(rescout.with_input(None, line))["carried_from"] == A


def test_sidecar_404_is_none_and_other_errors_are_unreadable(api: Api) -> None:
    assert rescout.head_inputs("o/r", "t", B, [PLAN])[PLAN]["sidecar"] is None
    api.sidecar_status = 500
    with pytest.raises(rescout.Unreadable):
        rescout.head_inputs("o/r", "t", B, [PLAN])
    api.sidecar_status = 200
    with pytest.raises(rescout.Unreadable):
        fps = rescout.head_inputs("o/r", "t", B, [PLAN])
        rescout.fingerprints(fps, ROOT, [])


def test_recorded_cites_merge_into_the_recomputed_fingerprint(api: Api) -> None:
    assert _fps(PLAN_TEXT, ["dec:55"]) != _fps(PLAN_TEXT, [])
    api.checks[A] = [_run(5, _record(A, cites=["dec:55"]))]
    assert _decide().carried[0]["cites"] == ["dec:55"]
    api.checks[A] = [_run(5, _record(A).replace('"cites":[]', '"cites":["dec:55"]'))]
    assert _decide().rescout == {ROUTE: "moved"}


def test_input_line_round_trips_beside_the_v1_record() -> None:
    line = rescout.render_input(ROUTE, A, "BLOCK", ["dec:9", "dec:2", "dec:9"], {PLAN: "v1:" + "0" * 64}, "3", B)
    assert "\n" not in line and rescout.parse_input(line) == {
        "route": ROUTE,
        "head_sha": A,
        "verdict": "BLOCK",
        "cites": ["dec:2", "dec:9"],
        "fingerprints": {PLAN: "v1:" + "0" * 64},
        "run_id": "3",
        "carried_from": B,
    }
    v1 = override.render_record(ROUTE, A, "BLOCK", ["x:1"], "3")
    text = rescout.with_input(v1 + "\n- x:1: t", line)
    assert text.split("\n")[0] == v1 and text.split("\n")[1] == line and override.parse_record(text) is not None
    assert rescout.with_input(None, line) == line and rescout.input_line(text) == line
    assert rescout.parse_input(text + "\n" + line) is None
    assert rescout.parse_input(line.replace('"run_id":"3"', '"run_id":3')) is None
    assert rescout.parse_input(line.replace(A, "xyz")) is None and rescout.parse_input(None) is None


def test_rescout_required_reads_the_rescout_map() -> None:
    assert rescout.rescout_required({"rescout": {ROUTE: "moved"}}) is True
    assert rescout.rescout_required({"rescout": {}}) is False and rescout.rescout_required({}) is False


def test_the_covered_set_is_the_plan_class() -> None:
    assert rescout.merge_checks.FINGERPRINT_PERSONAS <= {p for p, c in merge_checks.PERSONA_CLASSES.items() if c == "plan"}
