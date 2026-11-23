"""rec-5509: the claude CLI -> claude_p_retry.sh -> FILED parser seam, replayed from goldens of the pinned CLI.

A stub `claude` on PATH replays tests/fixtures/claude_cli_envelopes/<pin>/ ("success.stdout.json",
"success.stderr.txt", "version.txt") through the real "scripts/ci/claude_p_retry.sh" and the real
scripts.ci_rca.filing parser. The pin is "config/claude-cli-version"; the one installer is
"scripts/ci/install_claude_cli.sh". Those names are spelled here so --pre's data-edge channel selects this file
whenever one of them changes; test_fast_tier_selects_the_probe_for_every_seam_input proves it.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest

from scripts import dependency_graph
from scripts.checks.deps.affected_tests import derive_affected_tests
from scripts.ci import claude_p_seam_contract as seam

THIS_TEST = "tests/ci/test_claude_p_seam_contract.py"
WARNING_2311 = "Warning: workspace has not been trusted; project settings (.claude/settings.json) are ignored"


def _pin() -> str:
    return seam.PIN_FILE.read_text(encoding="utf-8").strip()


def _golden_dir() -> Path:
    return seam.GOLDEN_ROOT / _pin()


_STUB = """\
#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then
    printf '%s\\n' "$SEAM_STUB_VERSION"
    exit 0
fi
cat > /dev/null
if [ -n "${SEAM_STUB_ECHO_CWD:-}" ]; then
    echo "cwd: $PWD" >&2
fi
cat "$SEAM_STUB_STDERR" >&2
cat "$SEAM_STUB_STDOUT"
exit "${SEAM_STUB_EXIT:-0}"
"""


def _executable(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _golden(name: str) -> str:
    return (_golden_dir() / name).read_text(encoding="utf-8")


def _key_paths(value: Any, prefix: str = "") -> set[str]:
    if isinstance(value, dict):
        return {f"{prefix}.{k}" for k in value} | {p for k, v in value.items() for p in _key_paths(v, f"{prefix}.{k}")}
    if isinstance(value, list):
        return {p for i, v in enumerate(value) for p in _key_paths(v, f"{prefix}[{i}]")}
    return set()


@pytest.fixture()
def stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A replaying `claude` first on PATH; returns the directory the replayed streams are written into.

    The probe's own `claude --version` call is routed to the stub by path, and refused if PATH resolves any other
    claude, so the root conftest's guard against reaching a real LLM CLI still holds.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _executable(bin_dir / "claude", _STUB)
    guarded_run = subprocess.run

    def _stub_cli_run(args: Any, *a: Any, **kw: Any) -> Any:
        if list(args)[:1] != ["claude"]:
            return guarded_run(args, *a, **kw)
        resolved = shutil.which("claude")
        if resolved is None:
            raise FileNotFoundError(2, "No such file or directory", "claude")
        assert Path(resolved).parent == bin_dir, f"refusing a real claude CLI: {resolved}"
        return guarded_run(["bash", resolved, *list(args)[1:]], *a, **kw)

    monkeypatch.setattr(subprocess, "run", _stub_cli_run)
    streams = tmp_path / "streams"
    streams.mkdir()
    (streams / "stdout").write_text(_golden("success.stdout.json"), encoding="utf-8")
    (streams / "stderr").write_text(_golden("success.stderr.txt"), encoding="utf-8")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("SEAM_STUB_STDOUT", str(streams / "stdout"))
    monkeypatch.setenv("SEAM_STUB_STDERR", str(streams / "stderr"))
    monkeypatch.setenv("SEAM_STUB_VERSION", _golden("version.txt").strip())
    monkeypatch.setenv("CLAUDE_P_RETRY_BACKOFF_BASE", "0")
    return streams


def _envelope_with(**fields: Any) -> str:
    envelope = json.loads(_golden("success.stdout.json"))
    envelope.update(fields)
    return json.dumps(envelope) + "\n"


def test_golden_envelope_passes_real_wrapper_and_parser(stub: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert seam.main([]) == 0
    assert capsys.readouterr().out.startswith("PASS claude seam contract")


def test_stderr_in_envelope_fails_the_probe(stub: Path, capsys: pytest.CaptureFixture[str]) -> None:
    preamble = f"{WARNING_2311}\n{_golden('success.stderr.txt')}"
    (stub / "stdout").write_text(preamble + _golden("success.stdout.json"), encoding="utf-8")
    assert seam.main([]) == 1
    out = capsys.readouterr().out
    assert "FAIL envelope: the output file is not exactly one JSON object" in out
    assert "FAIL marker: envelope broken" in out


def test_marker_missing_is_named(stub: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (stub / "stdout").write_text(_envelope_with(result="I could not file anything."), encoding="utf-8")
    out_file = stub / "probe.json"
    assert seam.main(["--out", str(out_file)]) == 1
    out = capsys.readouterr().out
    assert "FAIL marker: envelope ok, marker missing (model reply)" in out
    assert "FAIL envelope" not in out
    assert out_file.is_file()


def test_wrapper_failure_and_error_envelope_are_named(
    stub: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (stub / "stdout").write_text('{"type":"result","is_error":true,"result":""}\n', encoding="utf-8")
    monkeypatch.setenv("SEAM_STUB_EXIT", "1")
    assert seam.main([]) == 1
    out = capsys.readouterr().out
    assert "FAIL wrapper exit: claude_p_retry.sh exited 1, want 0" in out
    assert "FAIL envelope: is_error is not false or result is not a string" in out


def test_wrapper_without_stderr_split_is_named(
    stub: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    body = '#!/usr/bin/env bash\ncat > /dev/null\ncat "$SEAM_STUB_STDOUT" > "$1"\n'
    unsplit = _executable(tmp_path / "unsplit_wrapper.sh", body)
    monkeypatch.setattr(seam, "WRAPPER", unsplit)
    assert seam.main([]) == 1
    assert "FAIL stderr split: the wrapper wrote no <out>.stderr file" in capsys.readouterr().out


def test_golden_matches_pin() -> None:
    others = sorted(p.name for p in seam.GOLDEN_ROOT.iterdir() if p.is_dir() and p.name != _pin())
    procedure = (
        f"config/claude-cli-version pins {_pin()}, but tests/fixtures/claude_cli_envelopes/ does not hold exactly its "
        f"goldens. Pin-bump procedure (operator-owned Claude Code session): put the real CLI at {_pin()} first on PATH "
        f"(npm install --prefix <scratch> of the CLI package at {_pin()}, then <scratch>/node_modules/.bin), run "
        f"`bin/venv-python -m scripts.ci.claude_p_seam_contract --capture tests/fixtures/claude_cli_envelopes/{_pin()}` "
        f"(it writes goldens only on PASS), then delete the old version directories: {others}"
    )
    assert _golden_dir().is_dir(), procedure
    assert all((_golden_dir() / name).is_file() for name in seam.GOLDEN_FILES), procedure
    assert _pin() in _golden("version.txt"), procedure
    assert not others, procedure


def test_fast_tier_selects_the_probe_for_every_seam_input() -> None:
    graph = dependency_graph.import_subgraph(dependency_graph.build_graph())
    modules = {seam.__name__, *dependency_graph.forward_closure(graph, seam.__name__)}
    assert "scripts.ci_rca.filing" in modules, sorted(modules)
    root = seam.ROOT
    module_files = []
    for module in sorted(modules):
        as_package = root / module.replace(".", "/") / "__init__.py"
        as_module = root / (module.replace(".", "/") + ".py")
        module_files.append((as_package if as_package.exists() else as_module).relative_to(root))
    inputs = [
        *seam.SEAM_FILES,
        *(_golden_dir().relative_to(root).joinpath(name).as_posix() for name in seam.GOLDEN_FILES),
        *(path.as_posix() for path in module_files),
    ]
    missed = [path for path in inputs if THIS_TEST not in derive_affected_tests([("M", path)])["selected"]]
    assert not missed, f"--pre would not run the seam probe when these change: {missed}"


def test_capture_normalises_ids(stub: Path, tmp_path: Path) -> None:
    session_id, message_uuid = str(uuid.uuid4()), str(uuid.uuid4())
    raw = _envelope_with(session_id=session_id, uuid=message_uuid)
    (stub / "stdout").write_text(raw, encoding="utf-8")
    target = tmp_path / "goldens" / _pin()
    assert seam.main(["--capture", str(target)]) == 0
    assert sorted(p.name for p in target.iterdir()) == sorted(seam.GOLDEN_FILES)
    written = json.loads((target / "success.stdout.json").read_text(encoding="utf-8"))
    original = json.loads(raw)
    assert written["session_id"] == written["uuid"] == seam.ZERO_UUID
    assert _key_paths(written) == _key_paths(original)
    assert {k: v for k, v in written.items() if k not in {"session_id", "uuid"}} == {
        k: v for k, v in original.items() if k not in {"session_id", "uuid"}
    }
    assert _pin() in (target / "version.txt").read_text(encoding="utf-8")
    assert session_id not in (target / "success.stdout.json").read_text(encoding="utf-8")


def test_capture_scrubs_private_fields(stub: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SEAM_STUB_ECHO_CWD", "1")
    raw = _envelope_with(
        transcript_path=f"{home}/.claude/projects/x.jsonl",
        email="someone@example.com",
        account_id="acct_0123",
        organization={"id": "org-1", "names": ["a", "b"]},
    )
    (stub / "stdout").write_text(raw, encoding="utf-8")
    (stub / "stderr").write_text(f"note: config at {home}/.claude.json\n", encoding="utf-8")
    target = tmp_path / "goldens"
    assert seam.main(["--capture", str(target)]) == 0
    written = json.loads((target / "success.stdout.json").read_text(encoding="utf-8"))
    assert written["transcript_path"] == "<HOME>/.claude/projects/x.jsonl"
    assert written["email"] == written["account_id"] == "<redacted>"
    assert written["organization"] == {"id": "<redacted>", "names": ["<redacted>", "<redacted>"]}
    assert _key_paths(written) == _key_paths(json.loads(raw))
    stderr = (target / "success.stderr.txt").read_text(encoding="utf-8")
    assert "cwd: <WORKDIR>" in stderr
    assert "<HOME>/.claude.json" in stderr
    assert str(home) not in stderr


def test_capture_refuses_a_never_commit_shape(stub: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (stub / "stdout").write_text(_envelope_with(note="AKIA" + "Q" * 16), encoding="utf-8")
    target = tmp_path / "goldens"
    assert seam.main(["--capture", str(target)]) == 2
    assert "never-commit shape" in capsys.readouterr().out
    assert not target.exists()


def test_capture_writes_nothing_on_fail(stub: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (stub / "stdout").write_text(f"{WARNING_2311}\n{_golden('success.stdout.json')}", encoding="utf-8")
    target = tmp_path / "goldens"
    assert seam.main(["--capture", str(target)]) == 1
    assert "FAIL envelope" in capsys.readouterr().out
    assert not target.exists()


def test_capture_refuses_a_cli_off_pin(
    stub: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "goldens"
    monkeypatch.setenv("SEAM_STUB_VERSION", "0.0.1 (Claude Code)")
    assert seam.main(["--capture", str(target)]) == 2
    assert f"not the pin {_pin()}" in capsys.readouterr().out
    empty = tmp_path / "empty_path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert seam.main(["--capture", str(target)]) == 2
    assert "claude --version is ''" in capsys.readouterr().out
    assert not target.exists()
