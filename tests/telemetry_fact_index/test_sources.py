"""Joins, ref links, metadata keys, lexicon facts and verb serving over a synthetic root and the real one."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.telemetry_fact_index import project
from scripts.telemetry_fact_index.verb_outputs import VerbOutput
from tests.telemetry_fact_index import make_root

REPO = Path(__file__).resolve().parents[2]
DIGEST_AND_SKILL_ADDRESSES = [
    "telemetry_transcripts.content_sha256[purpose=tool_input]",
    "telemetry_transcripts.content_sha256[purpose=tool_result]",
    "telemetry_transcripts.content_sha256[purpose=tool_output]",
    "telemetry_observations.output_sha256[observation_type=tool_call]",
    "telemetry_transcripts.content#skill[purpose=tool_input]",
    "telemetry_observations.name[observation_type=tool_call]",
    "telemetry_sessions.workflow",
]


@pytest.fixture(scope="module")
def real_rows() -> dict[str, Any]:
    return {r.address: r for r in project(REPO)}


def _write_golden(root: Path) -> None:
    corpus = root / "golden"
    corpus.mkdir()
    rows = [
        {
            "table": "telemetry_observations",
            "event_kind": "point",
            "observation_type": "model_call",
            "metadata": json.dumps({"declared_key": 1, "golden_only": 2}),
        },
        {"table": "telemetry_observations", "event_kind": "open", "observation_type": "turn", "metadata": None},
    ]
    (corpus / "case.json").write_text(json.dumps({"expected_rows": rows}), encoding="utf-8")
    (corpus / "index.json").write_text(json.dumps({"cases": ["case.json"]}), encoding="utf-8")
    config = root / "config" / "telemetry"
    config.mkdir(parents=True)
    (config / "parser_versions.yaml").write_text(
        yaml.safe_dump({"producers": {"claude_code": {"golden_index": "golden/index.json"}}}), encoding="utf-8"
    )
    (root / "docs" / "contracts" / "_joins.yaml").write_text(yaml.safe_dump({"joins": {}, "ref_links": {}}), encoding="utf-8")


def _synthetic(tmp_path: Path, outputs: dict[str, VerbOutput] | None = None, registry: bool = False) -> dict[str, Any]:
    root = make_root(tmp_path)
    _write_golden(root)
    if registry:
        entry = {
            "producer": "claude_code",
            "observation_type": "model_call",
            "event_kind": "point",
            "key": "declared_key",
            "meaning": "A declared key.",
        }
        (root / "docs" / "contracts" / "telemetry-metadata-keys.yaml").write_text(
            yaml.safe_dump({"keys": [entry]}), encoding="utf-8"
        )
    lexicon = {"lexicon": {"terms": []}}
    return {r.address: r for r in project(root, lexicon=lexicon, verb_outputs=outputs or {})}


def test_registry_key_declared_and_golden_only_key_undeclared(tmp_path: Path) -> None:
    rows = _synthetic(tmp_path, registry=True)
    declared = rows["telemetry_observations.metadata#declared_key[event_kind=point,observation_type=model_call]"]
    assert declared.declared is True and declared.description == "A declared key."
    assert rows["telemetry_observations.metadata#golden_only[event_kind=point,observation_type=model_call]"].declared is False


def test_without_registry_every_golden_key_is_undeclared_and_null_metadata_skipped(tmp_path: Path) -> None:
    rows = _synthetic(tmp_path)
    keys = [r for r in rows.values() if r.kind == "metadata_key"]
    assert {r.key for r in keys} == {"declared_key", "golden_only"} and all(r.declared is False for r in keys)


def test_serving_single_table_ambiguous_and_row_json(tmp_path: Path) -> None:
    outputs = {
        "one_table": VerbOutput("one_table", ("telemetry_agents",), ("agent_type", "extra_col")),
        "many": VerbOutput("many", ("telemetry_sessions", "telemetry_observations"), ("name", "session_id")),
        "blob": VerbOutput(
            "blob", ("telemetry_observations",), ("row_json",), row_json_columns={"telemetry_observations": ("legacy",)}
        ),
    }
    rows = _synthetic(tmp_path, outputs)
    assert [s.verb for s in rows["telemetry_agents.agent_type"].serving] == ["one_table"]
    name = rows["telemetry_observations.name"].serving
    assert [(s.verb, s.ambiguous) for s in name] == [("many", False)]
    assert [s.verb for s in rows["telemetry_observations.legacy"].serving] == ["blob"]
    assert "telemetry_transcripts.content" in rows and rows["telemetry_transcripts.content"].serving == []
    assert "verb:one_table.extra_col" in rows and rows["verb:one_table.extra_col"].kind == "verb_only"


def test_same_named_column_on_several_holding_tables_is_ambiguous(tmp_path: Path) -> None:
    outputs = {"many": VerbOutput("many", ("telemetry_sessions", "telemetry_agents"), ("session_id",))}
    root = make_root(tmp_path)
    _write_golden(root)
    path = root / "docs" / "contracts" / "telemetry_agents.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["fields"]["session_id"] = {"type": "str", "iceberg_type": "string", "nullable": True, "description": "x"}
    doc["realization"]["produced"]["claude_code"].append("session_id")
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    rows = {r.address: r for r in project(root, lexicon={"lexicon": {"terms": []}}, verb_outputs=outputs)}
    assert all(
        rows[a].serving and rows[a].serving[0].ambiguous
        for a in ("telemetry_sessions.session_id", "telemetry_agents.session_id")
    )


def test_joins_and_ref_link_rows(real_rows: dict[str, Any]) -> None:
    link = real_rows["ref_link:tool_call_open_to_tool_input"]
    assert link.kind == "ref_link" and "tool_call" in link.link_from and "tool_input" in link.link_to
    assert real_rows["telemetry_observations.session_id"].joins


def test_metadata_keys_from_every_registered_corpus(real_rows: dict[str, Any]) -> None:
    loss = real_rows["telemetry_observations.metadata#loss[event_kind=point,observation_type=model_call]"]
    stop = real_rows["telemetry_observations.metadata#stop_reason[event_kind=point,observation_type=model_call]"]
    assert "litellm" in loss.producers and "claude_code" in stop.producers


def test_lexicon_facts_each_address_has_a_row(real_rows: dict[str, Any]) -> None:
    for address in DIGEST_AND_SKILL_ADDRESSES:
        assert address in real_rows, address
    assert "digest" in real_rows[DIGEST_AND_SKILL_ADDRESSES[0]].concept_words
