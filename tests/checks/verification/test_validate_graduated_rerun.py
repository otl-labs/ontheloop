"""Mirror of scripts/checks/verification/validate_graduated_rerun.py (standing re-run of graduated records)."""

import os
import tempfile
import time
from pathlib import Path

import pytest
import yaml

from scripts.checks import registry
from scripts.checks.verification import validate_graduated_rerun as rerun
from scripts.checks.verification.validate_known_red_shrinks import KNOWN_RED_REL
from scripts.verification_graduation import shard_path_for

GREEN = ["bash", "-c", "exit 0"]
RED = ["bash", "-c", "echo boom-output >&2; exit 1"]


def _record(root: Path, check_id: str, command: list[str] | None = None, **extra: object) -> None:
    row: dict = {
        "check_id": check_id,
        "primitive_slot": "command_exit_zero",
        "guard_target": f"scripts/{check_id}.py",
        "check_spec": {"command": command if command is not None else GREEN},
    }
    row.update(extra)
    path = shard_path_for(check_id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(row), encoding="utf-8")


def _listed(root: Path, ids: list[str]) -> None:
    path = root / KNOWN_RED_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = [{"check_id": i, "owner_rec": "rec-1", "entered_at": "2026-11-03"} for i in ids]
    path.write_text(yaml.safe_dump({"schema_version": 1, "entries": entries}), encoding="utf-8")


def _alive(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def _decl() -> registry._Declaration:
    declaration = registry._CURRENT_DECLARATION
    assert declaration is not None
    return declaration


def _run(root: Path) -> list[str]:
    failed: list[str] = []
    rerun.validate_graduated_rerun(failed, root=root)
    return failed


class TestRerun:
    def test_unlisted_red_record_fails(self, tmp_path: Path) -> None:
        _record(tmp_path, "rec-red", RED)
        _record(tmp_path, "rec-ok")
        _listed(tmp_path, [])
        failed = _run(tmp_path)
        assert len(failed) == 1
        assert "rec-red" in failed[0]
        assert "rec-ok" not in failed[0]

    def test_listed_red_record_passes(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _record(tmp_path, "rec-red", RED)
        _listed(tmp_path, ["rec-red"])
        failed = _run(tmp_path)
        assert failed == []
        out = capsys.readouterr().out
        assert "=== Graduated record re-run ===" in out
        assert "1 graduated_records examined, 1 red (1 listed), 0 load-sensitive" in out

    def test_listed_green_record_fails(self, tmp_path: Path) -> None:
        _record(tmp_path, "rec-ok")
        _listed(tmp_path, ["rec-ok"])
        failed = _run(tmp_path)
        assert failed
        assert "rec-ok" in failed[0]
        assert "passes" in failed[0]

    def test_listed_id_not_live_fails(self, tmp_path: Path) -> None:
        _record(tmp_path, "rec-ok")
        _listed(tmp_path, ["rec-gone"])
        failed = _run(tmp_path)
        assert failed
        assert "rec-gone" in failed[0]
        assert "retired or renamed" in failed[0]

    def test_duplicate_listed_id_fails(self, tmp_path: Path) -> None:
        _record(tmp_path, "rec-red", RED)
        _listed(tmp_path, ["rec-red", "rec-red"])
        failed = _run(tmp_path)
        assert failed
        assert "twice" in failed[0]

    def test_missing_list_file_fails(self, tmp_path: Path) -> None:
        _record(tmp_path, "rec-ok")
        failed = _run(tmp_path)
        assert failed
        assert "known_red.yaml" in failed[0]

    def test_stale_node_id_fails(self, tmp_path: Path) -> None:
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text("def test_real(): pass\n", encoding="utf-8")
        _record(tmp_path, "sel-stale", primitive_slot="test_selector", check_spec={"node_id": "tests/test_x.py::test_gone"})
        _record(tmp_path, "cmd-stale", ["python", "-m", "pytest", "tests/test_x.py::test_gone", "-q"])
        _record(tmp_path, "sel-live", primitive_slot="test_selector", check_spec={"node_id": "tests/test_x.py::test_real"})
        _listed(tmp_path, [])
        failed = _run(tmp_path)
        assert len(failed) == 2
        assert any("sel-stale" in f and "does not resolve" in f for f in failed)
        assert any("cmd-stale" in f for f in failed)

    def test_load_sensitive_record_passes_serial_confirm(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        flag = tmp_path / "flag"
        script = f"if [ -e {flag} ]; then exit 0; else touch {flag}; exit 1; fi"
        _record(tmp_path, "rec-flaky", ["bash", "-c", script])
        _listed(tmp_path, [])
        failed = _run(tmp_path)
        assert failed == []
        out = capsys.readouterr().out
        assert "load-sensitive" in out
        assert "0 red (0 listed), 1 load-sensitive" in out

    def test_shell_wrapped_pytest_is_run(self, tmp_path: Path) -> None:
        row = {
            "check_id": "wrapped",
            "primitive_slot": "command_exit_zero",
            "check_spec": {"command": ["bash", "-c", "python -m pytest tests/test_x.py::test_gone"]},
        }
        assert rerun._static_verdict(row, tmp_path) is None
        out_row = {**row, "primitive_slot": "command_output_matches"}
        out_row["check_spec"] = {"command": ["python", "-m", "pytest", "tests/test_x.py::test_gone"], "expected": "x"}
        assert rerun._static_verdict(out_row, tmp_path) is None

    def test_integration_marked_pin_is_run(self, tmp_path: Path) -> None:
        (tmp_path / "tests").mkdir()
        body = "import pytest\n\n@pytest.mark.integration\nclass TestReal:\n    def test_a(self): pass\n\ndef test_b(): pass\n"
        (tmp_path / "tests" / "test_x.py").write_text(body, encoding="utf-8")
        for args in (["tests/test_x.py::TestReal::test_a"], ["tests/test_x.py", "-k", "TestReal"]):
            row = {
                "check_id": "pin",
                "primitive_slot": "command_exit_zero",
                "check_spec": {"command": ["python", "-m", "pytest", *args]},
            }
            assert rerun._static_verdict(row, tmp_path) is None
        plain = {"check_id": "p2", "primitive_slot": "test_selector", "check_spec": {"node_id": "tests/test_x.py::test_b"}}
        verdict = rerun._static_verdict(plain, tmp_path)
        assert verdict is not None
        assert not verdict.red

    def test_hung_record_times_out_red(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(rerun, "RECORD_TIMEOUT_S", 6)
        created = _spy_mkdtemp(monkeypatch)
        pidfile = tmp_path / "sleeper.pid"
        _record(tmp_path, "rec-hung", ["bash", "-c", f"sleep 120 & echo $! > {pidfile}; wait"])
        _listed(tmp_path, [])
        failed = _run(tmp_path)
        assert failed
        assert "rec-hung" in failed[0]
        assert "timed out after 6 s" in failed[0]
        assert created
        assert not any(Path(d).exists() for d in created)
        pid = int(pidfile.read_text(encoding="utf-8").strip())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _alive(pid):
            time.sleep(0.1)
        assert not _alive(pid)

    def test_examined_count_equals_records_evaluated(self, tmp_path: Path) -> None:
        _record(tmp_path, "a")
        _record(tmp_path, "b")
        _listed(tmp_path, [])
        _run(tmp_path)
        assert _decl().kind == "examined"
        assert _decl().count == 2
        assert rerun.live_ids(tmp_path) == {"a", "b"}
        assert rerun.red_records(tmp_path) == set()


def _spy_mkdtemp(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    created: list[str] = []
    real = tempfile.mkdtemp

    def spy(*args: object, **kwargs: object) -> str:
        path = real(*args, **kwargs)  # type: ignore[call-overload]
        created.append(path)
        return path

    monkeypatch.setattr(tempfile, "mkdtemp", spy)
    return created


def _fake_rg_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = tmp_path / "fakebin"
    fake.mkdir()
    (fake / "rg").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (fake / "rg").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}{os.pathsep}{os.environ.get('PATH', '')}")


class TestHelpers:
    def test_child_runs_see_rg_absent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_rg_on_path(tmp_path, monkeypatch)
        row = {"check_id": "rec-rg", "primitive_slot": "command_exit_zero", "guard_target": "x.py"}
        _record(tmp_path, "rec-rg", ["bash", "-c", "rg --version"])
        row["check_spec"] = {"command": ["bash", "-c", "rg --version"]}
        passed, _ = rerun._run_child(row, tmp_path)
        assert passed is False

    def test_rg_stub_dir_removed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_rg_on_path(tmp_path, monkeypatch)
        _record(tmp_path, "rec-rg", ["bash", "-c", "rg --version"])
        _record(tmp_path, "rec-ok")
        created = _spy_mkdtemp(monkeypatch)
        row = {"check_id": "rec-ok", "primitive_slot": "command_exit_zero", "guard_target": "x.py"}
        assert rerun._run_child(row, tmp_path)[0] is True
        assert len(created) == 1
        assert not Path(created[0]).exists()
        created.clear()
        outcomes = {o.check_id: o for o in rerun.evaluate(tmp_path)}
        assert outcomes["rec-rg"].red is True
        assert outcomes["rec-ok"].red is False
        assert len(created) == 1
        assert not Path(created[0]).exists()

    def test_first_line_skips_blank_and_empty(self) -> None:
        assert rerun._first_line("\n  \n  hello \nworld") == "hello"
        assert rerun._first_line("") == ""

    def test_pure_python_slot_and_graduation_error_run_in_process(self, tmp_path: Path) -> None:
        (tmp_path / "present.txt").write_text("x", encoding="utf-8")
        ok = {"check_id": "f1", "primitive_slot": "file_presence", "check_spec": {"path": "present.txt"}}
        assert rerun._run_record(ok, tmp_path)[0] is True
        bad = {"check_id": "f2", "primitive_slot": "file_presence", "check_spec": {"path": "absent.txt"}}
        assert rerun._run_record(bad, tmp_path)[0] is False
        broken = {"check_id": "f3", "primitive_slot": "nope", "check_spec": {}}
        passed, detail = rerun._run_in_process(broken, tmp_path)
        assert passed is False
        assert "unknown primitive_slot" in detail

    def test_child_without_verdict_is_red(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(rerun, "_MODULE", "json.tool")
        created = _spy_mkdtemp(monkeypatch)
        passed, detail = rerun._run_child({"check_id": "x"}, tmp_path)
        assert passed is False
        assert detail
        assert len(created) == 1
        assert not Path(created[0]).exists()

    def test_child_main_reports_verdict_and_missing_id(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _record(tmp_path, "rec-ok")
        assert rerun._child_main(["--check-id", "rec-ok", "--root", str(tmp_path)]) == 0
        assert '"passed": true' in capsys.readouterr().out
        assert rerun._child_main(["--check-id", "absent", "--root", str(tmp_path)]) == 0
        assert "not found" in capsys.readouterr().out

    def test_malformed_known_red_list_fails(self, tmp_path: Path) -> None:
        _record(tmp_path, "rec-ok")
        path = tmp_path / KNOWN_RED_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("entries: [1, 2]\n", encoding="utf-8")
        failed = _run(tmp_path)
        assert failed
        assert "malformed" in failed[0]
