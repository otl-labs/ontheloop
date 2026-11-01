"""Describe, version and registry tests for src/common/ducklake_event_reads.py (concern split)."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from src.common import ducklake_event_read_verbs as verbs
from src.common import ducklake_event_reads as ev
from src.common import ducklake_runtime as rt
from src.common.ducklake_event_read_sql import AGENTS, OBSERVATIONS, SESSIONS, TRANSCRIPTS
from src.common.ducklake_scd2_schema import NAMED_READS
from src.telemetry.append import EventTableSpec

_CONTENT = verbs.EVENT_READS["transcript_content"]


_ORPHANS = verbs.EVENT_READS["telemetry_orphan_counts"]


_SEAM = """
import builtins, io, pathlib
def _refuse(name):
    if "field_semantics.yaml" in str(name):
        raise RuntimeError("field_semantics.yaml read at import")
_open = builtins.open
def guarded(file, *a, **k):
    _refuse(file)
    return _open(file, *a, **k)
builtins.open = io.open = guarded
_read_text, _path_open = pathlib.Path.read_text, pathlib.Path.open
pathlib.Path.read_text = lambda self, *a, **k: (_refuse(self), _read_text(self, *a, **k))[1]
pathlib.Path.open = lambda self, *a, **k: (_refuse(self), _path_open(self, *a, **k))[1]
"""


_IMPORT = """
from src.common import ducklake_runtime as rt
def refuse(*a, **k):
    raise RuntimeError("load_field_semantics called at import")
rt.load_field_semantics = refuse
import src.common.ducklake_event_read_verbs, src.common.ducklake_event_reads as ev
try:
    ev.telemetry_joins()
except RuntimeError:
    print("IMPORT_READS_NO_FILE")
"""


def test_read_version_moves_with_every_datum_sql_and_column(monkeypatch: pytest.MonkeyPatch) -> None:
    base = ev.event_read_version("session_friction_rollup")
    assert base == ev.event_read_version("session_friction_rollup")
    assert len(base) == 16
    entry = verbs.EVENT_READS["session_friction_rollup"]

    def changed(**overrides: Any) -> str:
        patched = dict(verbs.EVENT_READS) | {"session_friction_rollup": dataclasses.replace(entry, **overrides)}
        monkeypatch.setattr(verbs, "EVENT_READS", patched)
        return ev.event_read_version("session_friction_rollup")

    seen = {base}
    data = dict(entry.data)
    for key, value in (("repeat_threshold", 4), ("classifier_version", 2), ("run_label", "other_label")):
        seen.add(changed(data=data | {key: value}))
    seen.add(changed(data=data | {"rules": data["rules"][:-1]}))
    assert changed(sql=entry.sql + "   ") == base, "whitespace-only SQL edits must not re-version"
    seen.add(changed(sql=entry.sql + " -- edit"))
    seen.add(changed(params=("session_id", "x")))
    seen.add(changed(paginable=True))
    seen.add(changed(max_window_days=3))
    assert len(seen) == 9, "every datum, the SQL, params, paginable flag and window cap must each re-version the verb"
    semantics = ev.rt.load_field_semantics()
    narrowed = {**semantics, "ops_tables": {**semantics["ops_tables"]}}
    narrowed["ops_tables"][OBSERVATIONS] = {
        **semantics["ops_tables"][OBSERVATIONS],
        "columns": {k: v for k, v in semantics["ops_tables"][OBSERVATIONS]["columns"].items() if k != "metadata"},
    }
    monkeypatch.setattr(verbs, "EVENT_READS", dict(verbs.EVENT_READS))
    assert ev.event_read_version("session_friction_rollup", narrowed) != ev.event_read_version("session_friction_rollup")


@pytest.mark.parametrize("table", [SESSIONS, OBSERVATIONS, TRANSCRIPTS, AGENTS])
def test_compared_columns_match_the_writer_compare_set(table: str) -> None:
    entry = ev.rt.load_field_semantics()["ops_tables"][table]
    spec = EventTableSpec.from_projection(table, entry)
    assert set(ev.compared_columns(table)) == set(spec.columns) - spec.compare_excluded
    assert {"created_timestamp", "producer_version"}.isdisjoint(ev.compared_columns(table))


def test_event_verbs_are_described_with_their_class_bounds_and_audience() -> None:
    described = ev.describe_all()
    event = {v: described[v] for v in verbs.EVENT_READS}
    assert all(row["kind"] == "event_journal" and row["catalog"] == "telemetry" for row in event.values())
    assert event["sessions_window"]["max_window_days"] == verbs.MAX_WINDOW_DAYS
    assert event["sessions_window"]["binding"] == "window" and event["telemetry_health_count"]["binding"] == "none"
    assert {v for v, row in event.items() if not row["agent_facing"]} == {
        "session_tree_rows",
        "sessions_window_all_purposes",
        "telemetry_health_count",
        "telemetry_orphan_counts",
        "telemetry_root_sessions",
        "transcript_content",
    }
    served = set(verbs.EVENT_READS) - {"transcript_content"}
    assert all("production telemetry catalog through the telemetry reader" in verbs.EVENT_READS[v].description for v in served)
    assert "Served by no deployed function" in verbs.EVENT_READS["transcript_content"].description
    assert not [v for v in verbs.EVENT_READS if "smoke catalog" in verbs.EVENT_READS[v].description]
    assert not [v for v in verbs.EVENT_READS if "excluded" in verbs.EVENT_READS[v].description]
    assert all(row["read_version"] == ev.event_read_version(v) for v, row in event.items())
    assert event["session_state_and_duration"]["tables"][0] == SESSIONS


def test_event_and_ops_verb_names_are_disjoint() -> None:
    assert not set(verbs.EVENT_READS) & set(NAMED_READS)
    assert all(entry.verb == name for name, entry in verbs.EVENT_READS.items())


def test_event_describe_rows_carry_every_ops_describe_key() -> None:
    described = ev.describe_all()
    ops_keys = set(next(iter(rt.describe_named_reads().values())))
    for verb in verbs.EVENT_READS:
        assert ops_keys <= set(described[verb]), verb
    assert {"kind", "tables", "catalog", "binding", "max_window_days"} <= set(described["sessions_window"])


def test_mcp_tool_registry_builds_from_describe_all_and_projects_only_agent_facing() -> None:
    from scripts.agent_sdk.mcp_server import build_tool_registry  # noqa: PLC0415

    described = ev.describe_all()
    names = {tool.name for tool in build_tool_registry(described)}
    event_agent_facing = {v for v, e in verbs.EVENT_READS.items() if e.agent_facing}
    assert event_agent_facing <= names
    assert not names & {"sessions_window_all_purposes", "telemetry_health_count"}
    assert names == {v for v, meta in described.items() if meta["agent_facing"]}


def test_transcript_content_is_registered_not_agent_facing() -> None:
    assert _CONTENT.binding == "session" and _CONTENT.agent_facing is False and _CONTENT.tables == (TRANSCRIPTS,)
    assert _CONTENT.params == ("session_id", "producer", "event_id", "parser_version", "offset", "max_bytes")
    assert _CONTENT.int_params == ("parser_version", "offset", "max_bytes") and not _CONTENT.paginable
    sql = " ".join(ev.render_sql(_CONTENT).split())
    assert "producer = $producer AND event_id = $event_id AND parser_version = $parser_version" in sql
    assert "as_of" not in sql and "v_star" not in sql and "QUALIFY" not in sql and "compared_versions" in sql
    row = ev.describe_all()["transcript_content"]
    props = row["params_schema"]["properties"]
    assert [p for p, t in props.items() if t == {"type": "integer"}] == ["parser_version", "offset", "max_bytes"]
    assert props["producer"] == {"type": "string"} and row["agent_facing"] is False
    assert ev.event_read_version("transcript_content") == row["read_version"]
    others = {v: ev.event_read_version(v) for v in verbs.EVENT_READS if v != "transcript_content"}
    assert len(set(others.values())) == len(others)


def test_telemetry_joins_come_from_the_projection() -> None:
    projected = rt.load_field_semantics()["telemetry_joins"]
    assert ev.telemetry_joins() == projected and projected
    sql = ev.render_sql(_ORPHANS)
    assert all(join["join_id"] in sql for join in projected) and "{telemetry_joins}" not in sql
    with pytest.raises(rt.DuckLakeRuntimeError, match="telemetry_joins"):
        ev.telemetry_joins({"ops_tables": {}})
    hostile = [dict(projected[0], child_column="x; DROP TABLE t")]
    with pytest.raises(rt.DuckLakeRuntimeError, match="unsupported"):
        ev.render_sql(_ORPHANS, {**rt.load_field_semantics(), "telemetry_joins": hostile})


def test_orphan_read_version_moves_with_a_projected_join() -> None:
    semantics = rt.load_field_semantics()
    changed = {**semantics, "telemetry_joins": [dict(semantics["telemetry_joins"][0], parent_column="session_id")]}
    changed["telemetry_joins"] += semantics["telemetry_joins"][1:]
    assert ev.event_read_version("telemetry_orphan_counts", changed) != ev.event_read_version(
        "telemetry_orphan_counts", semantics
    )
    for verb in verbs.EVENT_READS:
        if verb != "telemetry_orphan_counts":
            assert ev.event_read_version(verb, changed) == ev.event_read_version(verb, semantics), verb


def test_registry_import_reads_no_file(tmp_path: Any) -> None:
    import os  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    (tmp_path / "sitecustomize.py").write_text(_SEAM, encoding="utf-8")
    root = Path(__file__).resolve().parents[3]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(tmp_path), str(root)])}
    done = subprocess.run(
        [sys.executable, "-c", _IMPORT], cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60
    )
    assert done.returncode == 0 and "IMPORT_READS_NO_FILE" in done.stdout, done.stderr[-400:]
