import json
import os
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
    data["capabilities"].append("read_repo")
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
    assert proc.returncode == 2 and "read_repo" in proc.stderr and proc.stdout == ""


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


def _warehouse_contract(tmp_path):
    root = tmp_path / "wc"
    (root / "rendered").mkdir(parents=True)
    data = yaml.safe_load(fakes.FIXTURE.read_text(encoding="utf-8"))
    data["capabilities"].append("warehouse_read:fake_rows")
    (root / "fixture_persona.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    shutil.copy(fakes.FIXTURE.parent / "rendered" / "fixture_persona.txt", root / "rendered" / "fixture_persona.txt")
    return root / "fixture_persona.yaml"


def test_server_module_matches_index_binding():
    index = yaml.safe_load((ROOT / "docs" / "contracts" / "executor-personas.yaml").read_text(encoding="utf-8"))
    row = next(r for r in index["capability_vocabulary"]["capabilities"] if r["id"] == "warehouse_read")
    assert row["binding"]["litellm"]["server"] == run_persona.MCP_SERVER_MODULE.replace(".", "/") + ".py"


def test_server_env_withholds_inference_keys():
    config = yaml.safe_load((ROOT / "docs" / "contracts" / "project-configuration.yaml").read_text(encoding="utf-8"))
    secrets = {
        v for row in _walk_rows(config) if row.get("kind") == "secret" for v in [row.get("variable")] if isinstance(v, str)
    }
    assert {"DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN"} <= secrets
    environ = dict.fromkeys(secrets, "synthetic") | {"AWS_ACCESS_KEY_ID": "synthetic"}
    assert run_persona.server_env(environ) == {"AWS_ACCESS_KEY_ID": "synthetic"}


def _walk_rows(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_rows(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_rows(value)


def test_server_env_keeps_reader_and_proxy_variables():
    environ = {
        "AWS_REGION": "a",
        "DUCKLAKE_X": "b",
        "OTL_OPS_VERBS_TRANSPORT": "c",
        "HTTPS_PROXY": "d",
        "no_proxy": "e",
        "SSL_CERT_FILE": "f",
        "PATH": "/bin",
        "HOME": "/h",
    }
    assert run_persona.server_env(environ) == {k: v for k, v in environ.items() if k not in {"PATH", "HOME"}}


def test_server_parameters_run_the_module_from_the_root():
    params = run_persona.server_parameters({"AWS_REGION": "x", "GITHUB_TOKEN": "t"})
    assert params.command == sys.executable and params.args == ["-m", run_persona.MCP_SERVER_MODULE]
    assert Path(params.cwd) == ROOT and params.env == {"AWS_REGION": "x"}


def test_main_passes_no_server_stderr(tmp_path, monkeypatch, capfd):
    from mcp.client.stdio import StdioServerParameters

    ran = tmp_path / "ran"
    monkeypatch.setenv("FAKE_MCP_RAN_FILE", str(ran))
    monkeypatch.setattr(
        run_persona,
        "server_parameters",
        lambda: StdioServerParameters(
            command=sys.executable,
            args=["-m", "tests.persona_backends.fake_mcp_server", "crash"],
            cwd=ROOT,
            env={**os.environ, "FAKE_MCP_RAN_FILE": str(ran)},
        ),
    )
    args = _args(tmp_path)
    args[1] = str(_warehouse_contract(tmp_path))
    assert run_persona.main(args) == 4
    captured = capfd.readouterr()
    assert json.loads(captured.err) == {"error": "tool_server_unavailable"} and captured.out == ""
    assert ran.exists() and "FAKE-SERVER-STDERR-MARKER" not in captured.out + captured.err
    document = json.loads((tmp_path / "events.json").read_text())
    assert document["end"]["outcome"] == "aborted"


_NO_MCP = """
import runpy, sys
sys.modules["mcp"] = None
{setup}
sys.argv = ["run_persona.py", "--contract", {contract!r}, "--input", {inp!r}, "--events", {events!r}]
try:
    runpy.run_path({script!r}, run_name="__main__")
except SystemExit as exc:
    sys.exit(exc.code)
"""

_SCRIPTED = """
sys.path.insert(0, {root!r})
import json
from tests.persona_backends import fakes
import scripts.llm.litellm_transport as t
script = [fakes.tool({{"blob": "notes", "mode": "lines"}}), fakes.final()]
async def acomplete(*a, **k):
    return script.pop(0)
t.acomplete = acomplete
"""


def test_non_warehouse_run_imports_no_mcp(tmp_path):
    script = str(ROOT / "scripts" / "run_persona.py")

    def go(input_data, setup, name):
        inp = tmp_path / f"{name}.json"
        inp.write_text(json.dumps(input_data), encoding="utf-8")
        events = tmp_path / f"{name}-events.json"
        code = _NO_MCP.format(setup=setup, contract=str(fakes.FIXTURE), inp=str(inp), events=str(events), script=script)
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
        )
        return proc, json.loads(events.read_text(encoding="utf-8"))

    proc, document = go({"change_id": "c"}, "", "bad")
    assert proc.returncode == 0 and json.loads(proc.stdout)["failure"] == "input_incomplete" and document["turns"] == []
    proc, document = go(fakes.good_input(), _SCRIPTED.format(root=str(ROOT)), "good")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["outcome"] == "success" and len(document["turns"]) == 2
