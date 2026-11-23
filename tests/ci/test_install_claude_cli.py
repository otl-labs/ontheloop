"""rec-5509: scripts/ci/install_claude_cli.sh fails closed on a bad pin and installs exactly the pinned CLI.

Decision 162 R2: every case runs the installer under the literal composite-step argv GitHub uses, with PATH limited
to a stub directory plus /usr/bin and /bin, so sudo and npm are recording stubs and never touch the network.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "ci" / "install_claude_cli.sh"
GITHUB_COMPOSITE_BASH = ("bash", "--noprofile", "--norc", "-e", "-o", "pipefail")


def _pin() -> str:
    return (ROOT / "config" / "claude-cli-version").read_text(encoding="utf-8").strip()


_RECORDER = """\
#!/usr/bin/env bash
printf '%s\\n' "$(basename "$0") $*" >> "$CALL_LOG"
if [ "$(basename "$0")" = "sudo" ]; then
    exec "$@"
fi
if [ "${1:-}" = "install" ] && [ -n "${INSTALLED_VERSION:-}" ]; then
    printf '#!/usr/bin/env bash\\necho "%s (Claude Code)"\\n' "$INSTALLED_VERSION" > "$STUB_BIN/claude"
    chmod +x "$STUB_BIN/claude"
fi
exit 0
"""


class _Run:
    def __init__(self, tmp_path: Path) -> None:
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.log = tmp_path / "calls"
        for name in ("sudo", "npm"):
            path = self.bin / name
            path.write_text(_RECORDER, encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self.env = {
            "PATH": f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "CALL_LOG": str(self.log),
            "STUB_BIN": str(self.bin),
        }

    def __call__(self, root: Path, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*GITHUB_COMPOSITE_BASH, str(INSTALLER), str(root)],
            env={**self.env, **env},
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    @property
    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    @property
    def npm_calls(self) -> list[str]:
        return [line for line in self.calls if line.startswith("npm ")]


@pytest.fixture()
def run(tmp_path: Path) -> _Run:
    return _Run(tmp_path)


def _root_with_pin(tmp_path: Path, text: str | None) -> Path:
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True)
    if text is not None:
        (root / "config" / "claude-cli-version").write_text(text, encoding="utf-8")
    return root


def test_missing_pin_fails_closed_without_npm(run: _Run, tmp_path: Path) -> None:
    result = run(_root_with_pin(tmp_path, None))
    assert result.returncode == 2
    assert "pin file missing" in result.stderr
    assert run.calls == []


def test_malformed_pin_fails_closed_without_npm(run: _Run, tmp_path: Path) -> None:
    result = run(_root_with_pin(tmp_path, "latest\n"))
    assert result.returncode == 2
    assert "pin file malformed" in result.stderr
    assert run.calls == []


def test_unreadable_pin_fails_closed_without_npm(run: _Run, tmp_path: Path) -> None:
    root = _root_with_pin(tmp_path, None)
    (root / "config" / "claude-cli-version").mkdir()
    result = run(root)
    assert result.returncode == 2
    assert "pin file unreadable" in result.stderr
    assert run.calls == []


def test_missing_root_argument_is_a_usage_error(run: _Run) -> None:
    result = subprocess.run([*GITHUB_COMPOSITE_BASH, str(INSTALLER)], env=run.env, capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert "usage" in result.stderr
    assert run.calls == []


def test_valid_pin_installs_exactly_the_pinned_cli(run: _Run) -> None:
    result = run(ROOT, INSTALLED_VERSION=_pin())
    assert result.returncode == 0, result.stderr
    assert run.npm_calls == [
        f"npm install -g @anthropic-ai/claude-code@{_pin()} --omit=dev --omit=optional",
        "npm cache clean --force",
    ]
    assert [line.split()[:2] for line in run.calls if line.startswith("sudo ")] == [["sudo", "npm"], ["sudo", "npm"]]
    assert _pin() in result.stdout


def test_claude_already_on_path_is_kept_without_npm(run: _Run) -> None:
    present = run.bin / "claude"
    present.write_text('#!/usr/bin/env bash\necho never-invoked >> "$CALL_LOG"\n', encoding="utf-8")
    present.chmod(0o755)
    result = run(ROOT)
    assert result.returncode == 0
    assert result.stdout.strip() == str(present)
    assert run.calls == []


def test_installed_cli_off_pin_fails(run: _Run) -> None:
    result = run(ROOT, INSTALLED_VERSION="0.0.1")
    assert result.returncode == 1
    assert f"does not name the pin {_pin()}" in result.stderr
    assert len(run.npm_calls) == 2
