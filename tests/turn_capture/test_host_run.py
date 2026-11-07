"""Host-run sidecar grammar, pin rule P-5, host_run_ref rendering and the top-level agent run rows."""

from __future__ import annotations

import json

import pytest

from src.turn_capture.host_run import (
    parse_host_run,
    render_host_run_ref,
    resolve_pin,
    valid_host_facts,
    valid_project_ref,
)
from src.turn_capture.record_turn import record_turn
from src.turn_capture.transcript import MemTree
from tests.fixtures import turn_capture_v8_cases as cases
from tests.fixtures.turn_capture_corpus import PROJECT_REF, SID, assistant, make_files, pin_attachment, pin_facts_text, prompt


def _sidecar(**over: object) -> dict:
    body = json.loads(cases.sidecar())
    body.update(over)
    return body


def _bytes(body: object) -> bytes:
    return json.dumps(body).encode()


def _rows(files: dict[str, str], *, final: bool = True) -> list[dict]:
    result = record_turn(MemTree(files), None, project_ref=PROJECT_REF, session_final=final)
    return [row for table, rows in result.batches for row in rows if table == "telemetry_agents"]


def test_a_valid_sidecar_parses_and_renders_the_host_run_ref() -> None:
    host = parse_host_run(cases.sidecar(ordinal=2, attempt="3").encode())
    assert host is not None and host.agent == "ci_rca" and host.content_policy == "full"
    assert render_host_run_ref(host) == "gha:123456789/3/rca/2"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.update(schema_version=2),
        lambda b: b.update(host=[]),
        lambda b: b["host"].pop("job"),
        lambda b: b.update(extra=1),
        lambda b: b.pop("agent"),
        lambda b: b.update(agent="Bad Agent"),
        lambda b: b.update(content_policy="partial"),
        lambda b: b.update(project_ref="no spaces allowed"),
        lambda b: b["host"].update(kind="ecs"),
        lambda b: b["host"].update(run_id="12ab"),
        lambda b: b["host"].update(retry_ordinal=0),
        lambda b: b["host"].update(retry_ordinal=True),
        lambda b: b["host"].update(event_name="pull_request"),
        lambda b: b["host"].update(sha="short"),
        lambda b: b["host"].update(checkout_sha="zz"),
        lambda b: b["host"].update(unknown="x"),
    ],
)
def test_a_sidecar_outside_the_grammar_is_none(mutate) -> None:
    body = _sidecar()
    mutate(body)
    assert parse_host_run(_bytes(body)) is None


@pytest.mark.parametrize("data", [None, b"", b"{", b"[]", b"null"])
def test_unreadable_sidecar_bytes_are_none(data: bytes | None) -> None:
    assert parse_host_run(data) is None


def _tree(pin: str | None, side: str | None) -> MemTree:
    root = [prompt("u1", 1, "p1", "hi"), assistant("a1", 2, "m1", [])]
    if pin is not None:
        root.insert(0, pin_attachment("s1", 0.5, pin, pin_facts_text()))
    files = make_files(root)
    if side is not None:
        files[f"{SID}/host-run.json"] = json.dumps(_sidecar(project_ref=side))
    return MemTree(files)


@pytest.mark.parametrize(
    ("pin", "side", "expected"),
    [
        (None, None, (None, "no_pin")),
        ("ontheloop", None, ("ontheloop", "pinned")),
        (None, "ontheloop", ("ontheloop", "pinned")),
        ("ontheloop", "ontheloop", ("ontheloop", "pinned")),
        ("ontheloop", "other", (None, "pin_conflict")),
    ],
)
def test_pin_rule_p5(pin: str | None, side: str | None, expected: tuple) -> None:
    assert resolve_pin(_tree(pin, side)) == expected


def test_a_top_level_run_has_an_open_and_a_close_with_no_observation_ref() -> None:
    rows = _rows(cases.ci_rca_hook_pin_and_host_run_files())
    top = [r for r in rows if r["entity_ref"].endswith("/run")]
    assert sorted(r["event_kind"] for r in top) == ["close", "open"]
    assert all(r.get("observation_id") is None and r["host_run_ref"] == "gha:123456789/1/rca/1" for r in top)
    assert {r["agent_type"] for r in top} == {"ci_rca"} and {r["trigger"] for r in top} == {"workflow_run"}


def test_no_close_row_before_the_final_pass() -> None:
    top = [r for r in _rows(cases.ci_rca_hook_pin_and_host_run_files(), final=False) if r["entity_ref"].endswith("/run")]
    assert all(r["event_kind"] == "open" for r in top)


def test_an_interrupted_or_failed_run_closes_with_a_vocabulary_outcome() -> None:
    closes = [r for r in _rows(cases.host_run_retry_attempts_files()) if r["event_kind"] == "close"]
    assert closes and {r["outcome"] for r in closes} <= {"success", "failed", "timeout", "throttled"}
    assert closes[-1]["outcome"] == "failed" and closes[-1]["host_run_ref"].endswith("/2/rca/2")


def test_a_malformed_sidecar_adds_no_top_level_rows() -> None:
    assert not [r for r in _rows(cases.host_run_malformed_files()) if r["entity_ref"].endswith("/run")]


def test_the_withheld_policy_stores_only_empty_digest_omissions() -> None:
    result = record_turn(MemTree(cases.host_run_only_withheld_files()), None, project_ref=PROJECT_REF, session_final=True)
    rows = [r for table, batch in result.batches if table == "telemetry_transcripts" for r in batch]
    withheld = [r for r in rows if r.get("content_omitted_reason") == "withheld_by_policy"]
    assert withheld
    assert all(r.get("content") is None and r["content_bytes"] == 0 for r in withheld)
    assert len({r["content_sha256"] for r in withheld}) == 1


def test_the_exported_validators_share_the_sidecar_grammar() -> None:
    host = _sidecar()["host"]
    assert valid_host_facts(host) and not valid_host_facts({**host, "kind": "ecs"}) and not valid_host_facts("gha")
    assert valid_project_ref("ontheloop") and not valid_project_ref("has space")


def test_a_root_with_no_close_or_assistant_yet_has_no_top_level_rows() -> None:
    files = {**make_files([prompt("u1", 1, "p1", "hi")]), f"{SID}/host-run.json": cases.sidecar()}
    assert not [r for r in _rows(files, final=False) if r["entity_ref"].endswith("/run")]
