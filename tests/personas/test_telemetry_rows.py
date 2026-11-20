"""scripts.personas.telemetry_rows: exit codes, atomic write, equality with map_run, no writer imports."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from scripts.personas import telemetry_rows as entry
from src.turn_capture import litellm_producer as lp
from tests.fixtures.turn_capture_litellm_cases import HOST_RUN, LITELLM_BUILDERS, load_litellm_case

ROOT = Path(__file__).resolve().parents[2]


def _run(tmp_path, events, host=HOST_RUN):
    events_path = tmp_path / "events.json"
    events_path.write_text(events if isinstance(events, str) else json.dumps(events), encoding="utf-8")
    host_path = tmp_path / "host-run.json"
    if host is not None:
        host_path.write_text(host, encoding="utf-8")
    out = tmp_path / "rows.json"
    code = entry.main(["--events", str(events_path), "--host-run", str(host_path), "--out", str(out)])
    return code, out


def _events(name="single_call.json"):
    return json.loads(load_litellm_case(name)[0]["events.json"])


def test_exit_0_writes_the_rows_file_equal_to_map_run(tmp_path):
    code, out = _run(tmp_path, _events("fail_over_probe.json"))
    assert code == 0 and not Path(f"{out}.tmp").exists()
    document = json.loads(out.read_text(encoding="utf-8"))
    mapped = lp.map_run(_events("fail_over_probe.json"), HOST_RUN.encode())
    assert isinstance(mapped, lp.Mapped)
    assert document == entry.rows_document(mapped) | {"batches": document["batches"]}
    assert [b["table"] for b in document["batches"]] == ["telemetry_observations", "telemetry_agents", "telemetry_sessions"]
    assert document["batches"] == entry.rows_document(mapped)["batches"]
    assert (document["producer"], document["parser_version"], document["producer_version"]) == ("litellm", 1, "litellm-1")
    stamp = document["batches"][2]["rows"][0]["event_timestamp"]
    assert stamp.endswith("Z") and len(stamp.split(".")[1]) == 4 and stamp.startswith("2026-")


def test_exit_2_unreadable_and_wrong_schema(tmp_path, capsys):
    code, out = _run(tmp_path, "not json")
    assert code == 2 and not out.exists()
    old = _events()
    old["header"]["schema"] = 1
    code, out = _run(tmp_path, old)
    assert code == 2 and not out.exists() and "refused" in capsys.readouterr().err


def test_exit_3_deferred_writes_nothing(tmp_path, capsys):
    code, out = _run(tmp_path, _events(), host=None)
    assert code == 3 and not out.exists() and not Path(f"{out}.tmp").exists()
    assert capsys.readouterr().err.count("\n") == 1


def test_exit_4_halt_writes_nothing(tmp_path):
    events = _events()
    events["header"]["started_at_ms"] += 10**9
    code, out = _run(tmp_path, events)
    assert code == 4 and not out.exists() and not Path(f"{out}.tmp").exists()


def test_every_case_maps_through_the_entry(tmp_path):
    for name in LITELLM_BUILDERS:
        assert _run(tmp_path, _events(name))[0] == 0, name


def test_entry_imports_no_writer_or_network_module():
    tree = ast.parse((ROOT / "scripts/personas/telemetry_rows.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    banned = (
        "boto3",
        "botocore",
        "urllib.request",
        "http.client",
        "ssl",
        "socket",
        "src.telemetry.append",
        "src.telemetry_client",
    )
    assert not [n for n in names if n in banned or "writer" in n or "append" in n], names
