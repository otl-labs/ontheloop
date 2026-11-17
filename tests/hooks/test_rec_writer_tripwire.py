"""Hook plumbing for the rec writer session tripwire (PLAN-rec-writer-session-tripwire)."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / ".claude/hooks/rec_writer_tripwire.py"
A1 = "curl -X POST https://example.invalid/prod/ops/writer -d @rec.json"


def _run(payload: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("rec_writer_tripwire", HOOK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _in_process(monkeypatch: pytest.MonkeyPatch, stdin: str) -> tuple[int, str]:
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    return _load().main(), ""


@pytest.mark.parametrize("tool", ["Bash", "Monitor"])
def test_hook_refuses_direct_writer_call_with_portal_pointer(tool: str) -> None:
    result = _run({"tool_name": tool, "tool_input": {"command": A1}})
    assert result.returncode == 2
    assert result.stderr.startswith("rec-writer-tripwire: refused.")
    assert "scripts.ops_data_portal" in result.stderr
    assert result.stdout == ""


def test_hook_passes_ordinary_command() -> None:
    result = _run({"tool_name": "Bash", "tool_input": {"command": "grep -rn ops/writer scripts/"}})
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def _bash(command: str) -> str:
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})


def _raiser(exc: Exception) -> Any:
    def fail(_command: str) -> None:
        raise exc

    return fail


FAIL_OPEN = [
    "invalid-json",
    "json-list",
    "no-command",
    "edit-names-writer",
    "classify-valueerror",
    "classify-runtimeerror",
    "classifier-import-fails",
]


@pytest.mark.parametrize("case", FAIL_OPEN)
def test_hook_fails_open(case: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.session import rec_writer_match

    stdin = {
        "invalid-json": "{not json",
        "json-list": "[1, 2]",
        "no-command": json.dumps({"tool_name": "Bash", "tool_input": {}}),
        "edit-names-writer": json.dumps({"tool_name": "Edit", "tool_input": {"content": "ops/writer " + A1}}),
    }.get(case, _bash(A1))
    if case == "classify-valueerror":
        monkeypatch.setattr(rec_writer_match, "classify", _raiser(ValueError("lexer")))
    elif case == "classify-runtimeerror":
        monkeypatch.setattr(rec_writer_match, "classify", _raiser(RuntimeError("boom")))
    elif case == "classifier-import-fails":
        monkeypatch.setitem(sys.modules, "scripts.session.rec_writer_match", None)
    code, _ = _in_process(monkeypatch, stdin)
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "" and captured.err == ""


def _registers(groups: list[dict[str, Any]]) -> bool:
    import re

    found = False
    for group in groups:
        if any("rec_writer_tripwire.py" in h.get("command", "") for h in group["hooks"]):
            pattern = re.compile(group["matcher"])
            assert pattern.search("Bash") and pattern.search("Monitor")
            assert not any(pattern.search(t) for t in ("Edit", "Write", "MultiEdit", "NotebookEdit"))
            found = True
    return found


def test_hook_registered_for_bash_and_monitor() -> None:
    for path in (".claude/settings.json", ".claude/hooks/hooks.json"):
        data = json.loads((ROOT / path).read_text(encoding="utf-8"))
        assert _registers(data["hooks"]["PreToolUse"]), path
