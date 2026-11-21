"""The telemetry fact lookup: golden queries, ranking, no-match exit and the contract pointers (Decision 131 home)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.session import telemetry_facts
from scripts.telemetry_fact_index import project

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def lexicon() -> dict[str, Any]:
    return yaml.safe_load((REPO / "docs" / "contracts" / "telemetry-lexicon.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rows(lexicon: dict[str, Any]) -> list[Any]:
    return project(REPO, lexicon=lexicon)


def _top(rows: list[Any], lexicon: dict[str, Any], query: str, n: int = 10) -> list[str]:
    found, _ = telemetry_facts.search(rows, query.split(), lexicon)
    return [r.address for r in found[:n]]


def test_tool_input_digest_finds_stored_column(rows: list[Any], lexicon: dict[str, Any]) -> None:
    target = "telemetry_transcripts.content_sha256[purpose=tool_input]"
    assert target in _top(rows, lexicon, "tool input digest", 3)
    assert target in _top(rows, lexicon, "tool input hash", 3)
    without = yaml.safe_load(yaml.safe_dump(lexicon))
    for term in without["lexicon"]["terms"]:
        if term["term"] == "digest":
            term.pop("aka")
    assert target not in _top(project(REPO, lexicon=without), without, "tool input hash")


def test_skill_name_lists_payload_fact(rows: list[Any], lexicon: dict[str, Any]) -> None:
    found, _ = telemetry_facts.search(rows, ["skill", "name"], lexicon)
    skill = next(r for r in found if r.address == "telemetry_transcripts.content#skill[purpose=tool_input]")
    assert skill.kind == "payload_key" and any(s.verb == "session_transcript_masked" and s.masked for s in skill.serving)


def test_stop_reason_lists_metadata_key_on_model_call(rows: list[Any], lexicon: dict[str, Any]) -> None:
    assert any("metadata#stop_reason" in a and "model_call" in a for a in _top(rows, lexicon, "stop reason"))


def test_tool_call_input_link_lists_the_ref_link(rows: list[Any], lexicon: dict[str, Any]) -> None:
    found, _ = telemetry_facts.search(rows, ["tool", "call", "input", "link"], lexicon)
    link = next(r for r in found if r.address == "ref_link:tool_call_open_to_tool_input")
    assert link.link_from and link.link_to


def test_surface_is_unserved_while_realized_is_false(rows: list[Any]) -> None:
    surface = next(r for r in rows if r.address == "telemetry_sessions.surface")
    assert surface.kind == "read_derived" and surface.serving == []


def test_nonsense_phrase_exits_one_with_no_proof_line(capsys: pytest.CaptureFixture[str]) -> None:
    assert telemetry_facts.main(["zzqqxx", "vvwwyy"]) == 1
    assert "no match is not proof of absence" in capsys.readouterr().out


def test_prints_at_most_ten_rows(capsys: pytest.CaptureFixture[str]) -> None:
    assert telemetry_facts.main(["session"]) == 0
    out = capsys.readouterr().out
    assert out.count("  kind=") <= telemetry_facts.MAX_ROWS and " shown" in out.splitlines()[-1]


def test_file_router_routes_telemetry_facts() -> None:
    data = yaml.safe_load((REPO / "docs" / "contracts" / "file-router.yaml").read_text(encoding="utf-8"))
    route = next(r for r in data["routes"] if r["topic"] == "telemetry-facts")
    assert route["targets"] == ["scripts/session/telemetry_facts.py", "scripts/telemetry_fact_index/"]


def test_design_walk_step_zero_names_the_lookup() -> None:
    text = (REPO / "docs" / "contracts" / "data-modeling-standard.yaml").read_text(encoding="utf-8")
    assert (
        "bin/venv-python -m\n  scripts.session.telemetry_facts <words>" in text
        or "scripts.session.telemetry_facts <words>" in text
    )


def test_lexicon_concepts_carry_their_facts(lexicon: dict[str, Any]) -> None:
    terms = {t["term"]: t for t in lexicon["lexicon"]["terms"]}
    assert terms["digest"]["aka"] == ["hash", "fingerprint", "checksum", "sha256"]
    assert len(terms["digest"]["facts"]) == 4 and len(terms["skill name"]["facts"]) == 3


def test_render_shows_each_row_kind(rows: list[Any]) -> None:
    by_address = {r.address: r for r in rows}
    surface = telemetry_facts.render(by_address["telemetry_sessions.surface"])
    assert "served by: unserved" in surface
    owned = next(r for r in rows if r.kind == "unproduced")
    assert f"owner: unproduced owner {owned.owner}" in telemetry_facts.render(owned)
    meta = next(r for r in rows if r.kind == "metadata_key")
    assert "declared: " in telemetry_facts.render(meta)
    link = telemetry_facts.render(by_address["ref_link:tool_call_open_to_tool_input"])
    assert "from: telemetry_observations" in link and "to: telemetry_transcripts" in link
