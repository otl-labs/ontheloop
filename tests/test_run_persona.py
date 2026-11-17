import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts import run_persona
from tests.persona_backends import fakes

ROOT = Path(__file__).resolve().parents[1]


def _args(tmp_path, input_data=None):
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps(fakes.good_input() if input_data is None else input_data), encoding="utf-8")
    return ["--contract", str(fakes.FIXTURE), "--input", str(inp), "--events", str(tmp_path / "events.json")]


def _patch(monkeypatch, script):
    transport = fakes.ScriptedTransport(script)
    monkeypatch.setattr("scripts.llm.litellm_transport.acomplete", transport)
    return transport


def test_result_alone_on_stdout_and_events_per_turn(tmp_path, monkeypatch, capsys):
    _patch(monkeypatch, [fakes.tool({"blob": "notes", "mode": "lines"}), fakes.final()])
    assert run_persona.main(_args(tmp_path)) == 0
    out = capsys.readouterr().out
    assert json.loads(out)["outcome"] == "success" and out.count("\n") == 1
    document = json.loads((tmp_path / "events.json").read_text())
    assert len(document["turns"]) == 2 and document["end"]["turns_used"] == 2
    assert not list(tmp_path.glob("*.tmp"))


def test_failure_value_is_still_exit_zero(tmp_path, monkeypatch, capsys):
    _patch(monkeypatch, [])
    assert run_persona.main(_args(tmp_path, {"change_id": "c"})) == 0
    assert json.loads(capsys.readouterr().out)["failure"] == "input_incomplete"


def test_refused_contract_exits_two(tmp_path, monkeypatch, capsys):
    _patch(monkeypatch, [])
    args = _args(tmp_path)
    args[1] = str(tmp_path / "missing.yaml")
    assert run_persona.main(args) == 2
    assert "refused" in capsys.readouterr().err


def test_unreadable_input_exits_two(tmp_path, capsys):
    args = _args(tmp_path)
    (tmp_path / "in.json").write_text("{", encoding="utf-8")
    assert run_persona.main(args) == 2
    (tmp_path / "in.json").write_text("[]", encoding="utf-8")
    assert run_persona.main(args) == 2


def test_aborted_run_exits_four_with_events(tmp_path, monkeypatch, capsys):
    _patch(monkeypatch, [fakes.error("outage", (fakes.attempt(outcome="outage"),))])
    assert run_persona.main(_args(tmp_path)) == 4
    captured = capsys.readouterr()
    assert captured.out == "" and json.loads(captured.err) == {"error": "outage"}
    document = json.loads((tmp_path / "events.json").read_text())
    assert document["end"]["outcome"] == "aborted" and document["turns"][0]["kind"] == "transport_error"


def test_subprocess_under_isolated_mode_refuses_unbuilt_capability(tmp_path):
    root = tmp_path / "contract"
    (root / "rendered").mkdir(parents=True)
    data = yaml.safe_load(fakes.FIXTURE.read_text(encoding="utf-8"))
    data["capabilities"].append("warehouse_read:x")
    (root / "fixture_persona.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    shutil.copy(fakes.FIXTURE.parent / "rendered" / "fixture_persona.txt", root / "rendered" / "fixture_persona.txt")
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps(fakes.good_input()), encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    proc = subprocess.run(
        [
            sys.executable,
            "-I",
            str(ROOT / "scripts" / "run_persona.py"),
            "--contract",
            str(root / "fixture_persona.yaml"),
            "--input",
            str(inp),
            "--events",
            str(tmp_path / "e.json"),
        ],
        cwd=work,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert proc.returncode == 2 and "warehouse_read:x" in proc.stderr and proc.stdout == ""


@pytest.mark.parametrize("flag", ["--contract", "--input", "--events"])
def test_missing_required_argument_exits_two(flag):
    argv = ["--contract", "c", "--input", "i", "--events", "e"]
    index = argv.index(flag)
    del argv[index : index + 2]
    with pytest.raises(SystemExit) as info:
        run_persona.main(argv)
    assert info.value.code == 2


def test_import_inserts_the_repo_root_when_absent(monkeypatch):
    import importlib

    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != str(ROOT)])
    importlib.reload(run_persona)
    assert sys.path[0] == str(ROOT)
