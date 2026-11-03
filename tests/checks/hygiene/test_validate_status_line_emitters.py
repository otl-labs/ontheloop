from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from scripts.checks import _common
from scripts.checks.hygiene import validate_status_line_emitters as mod
from scripts.checks.hygiene.validate_status_line_emitters import ParsedModule, emitter_errors
from src.turn_capture import markers

_NEEDLE = f"{markers.STATUS_MARKER_PREFIX} v{markers.STATUS_MARKER_VERSION}"
_FORMATTER = "format_" + "status_marker"
_GOOD = 'emit_status("demo.run", "done")\nemit_status("demo.run", "failed", "bad")\n'


def _module(text: str) -> ParsedModule:
    return ParsedModule(text, ast.parse(text))


def _roster(**overrides: Any) -> dict[str, Any]:
    row = {"module": "scripts/demo.py", "command": "bin/venv-python -m scripts.demo", "results": ["done", "failed"]}
    return {"demo.run": {**row, **overrides}}


def _run(roster: dict[str, Any], demo: str = _GOOD, **extra: str) -> list[str]:
    modules = {"scripts/demo.py": _module(demo), **{path: _module(text) for path, text in extra.items()}}
    return emitter_errors(roster, modules)


def test_clean_synthetic_roster_passes() -> None:
    assert _run(_roster()) == []


_CASES = [
    ("E1", {"demo.bad source": _roster()["demo.run"]}, _GOOD, {}),
    ("E1", {"x" * 193: _roster()["demo.run"]}, _GOOD, {}),
    ("E2", _roster(results=["done", "unknown"]), _GOOD, {}),
    ("E3", _roster(module="scripts/missing.py"), _GOOD, {}),
    ("E3", _roster(), 'emit_status("demo.run", "done")\n', {}),
    ("E3", _roster(), _GOOD + 'emit_status("demo.run", "skipped")\n', {}),
    ("E3", _roster(), 'emit_status("demo.run", "done")\nemit_status("demo.run", "failed", "bad reason")\n', {}),
    ("E4", _roster(), _GOOD + 'emit_status("other.run", "done")\n', {}),
    ("E4", _roster(), _GOOD + 'emit_status(name, "done")\n', {}),
    ("E4", _roster(), _GOOD + 'emit_status("demo.run", result)\n', {}),
    ("E4", _roster(), _GOOD + 'emit_status("demo.run", "done", reason)\n', {}),
    ("E4", _roster(), _GOOD, {"scripts/elsewhere.py": 'emit_status("demo.run", "done")\n'}),
    ("E5", _roster(command="python demo.py"), _GOOD, {}),
    ("E6", _roster(), _GOOD, {"scripts/stray.py": f"{_FORMATTER}('x', 'done')\n"}),
    ("E6", _roster(), _GOOD, {"scripts/stray.py": f"LINE = '{_NEEDLE} source=x'\n"}),
]


@pytest.mark.parametrize(("rule", "roster", "demo", "extra"), _CASES, ids=[f"{c[0]}-{i}" for i, c in enumerate(_CASES)])
def test_each_rule_fails_closed(rule: str, roster: dict[str, Any], demo: str, extra: dict[str, str]) -> None:
    errors = _run(roster, demo, **extra)
    assert any(f" {rule} " in error for error in errors), errors
    assert all(("scripts/" in error or "config/" in error) for error in errors), errors


def test_formatter_homes_may_hold_the_needle() -> None:
    assert _run(_roster(), **{"scripts/telemetry_capture/status_line.py": f"{_FORMATTER}('x', 'done')\n"}) == []


def test_live_repository_passes() -> None:
    failed: list[str] = []
    mod.validate_status_line_emitters(failed)
    assert failed == []


def _point_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, roster: str | None) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "config" / "telemetry").mkdir(parents=True)
    if roster is not None:
        (tmp_path / mod.ROSTER_PATH).write_text(roster, encoding="utf-8")
    monkeypatch.setattr(_common, "ROOT", tmp_path)


def test_missing_roster_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _point_root(monkeypatch, tmp_path, None)
    failed: list[str] = []
    mod.validate_status_line_emitters(failed)
    assert failed


def test_unparseable_roster_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _point_root(monkeypatch, tmp_path, "sources: [unclosed\n")
    failed: list[str] = []
    mod.validate_status_line_emitters(failed)
    assert failed


def test_list_shaped_roster_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _point_root(monkeypatch, tmp_path, "sources:\n  - demo.run\n")
    failed: list[str] = []
    mod.validate_status_line_emitters(failed)
    assert failed


def test_unparseable_python_file_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _point_root(monkeypatch, tmp_path, "sources:\n  demo.run:\n    module: scripts/demo.py\n")
    (tmp_path / "scripts" / "broken.py").write_text("emit_status(\ndef (:\n", encoding="utf-8")
    failed: list[str] = []
    mod.validate_status_line_emitters(failed)
    assert any("broken.py" in entry for entry in failed)


def test_keyword_and_attribute_call_forms_are_read() -> None:
    demo = 'status_line.emit_status(source="demo.run", result="done")\nemit_status("demo.run", result="failed", reason=None)\n'
    assert _run(_roster(), demo) == []
