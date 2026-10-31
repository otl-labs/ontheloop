"""The wording check reports a Decision annotation that overflows a reader's cap before approval (rec-4731)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

from scripts.checks.deps.affected_tests import derive_affected_tests
from scripts.roadmap import plan_wording_check

_CAP_READER = "tests/checks/decisions/test_telemetry_production_catalog_decision.py"
_CAP_TEST = (
    "from pathlib import Path\n\n\n"
    "def test_entry_within_cap() -> None:\n"
    "    text = (Path(__file__).resolve().parents[1] / 'docs' / 'DECISIONS.md').read_text(encoding='utf-8')\n"
    "    assert len(text.encode('utf-8')) <= 60\n"
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def test_overflowing_annotation_is_reported_before_approval(tmp_path: Path) -> None:
    main = tmp_path / "main"
    (main / "docs").mkdir(parents=True)
    (main / "tests").mkdir()
    (main / "docs" / "DECISIONS.md").write_text("## Decision 1: demo\nshort body\n", encoding="utf-8")
    (main / "tests" / "test_cap.py").write_text(_CAP_TEST, encoding="utf-8")
    _git(main, "init", "-q", "-b", "main")
    _git(main, "-c", "user.email=t@example.com", "-c", "user.name=t", "add", "-A")
    _git(
        main, "-c", "user.email=t@example.com", "-c", "user.name=t", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "base"
    )
    _git(main, "update-ref", "refs/remotes/origin/main", "HEAD")
    scratch = tmp_path / "scratch"
    _git(main, "worktree", "add", "-q", "--detach", str(scratch), "HEAD")

    annotation = "short body\nUpdate: " + "an annotation that overflows the cap " * 3 + "\n"
    sidecar = tmp_path / "sidecar.yaml"
    blocks = [{"id": "d1", "file": "docs/DECISIONS.md", "before": "short body\n", "after": annotation}]
    sidecar.write_text(yaml.safe_dump({"schema_version": 1, "slug": "demo", "blocks": blocks}), encoding="utf-8")
    (scratch / "docs" / "DECISIONS.md").write_text("## Decision 1: demo\n" + annotation, encoding="utf-8")

    def runner(cmd: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
        if "scripts.validate" in cmd:
            return subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_cap.py"],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
            )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    result = plan_wording_check.run("demo", scratch, sidecar, runner)

    assert result.verdict == "FAIL"
    assert "test_entry_within_cap" in result.echo


def test_decisions_edit_selects_the_entry_cap_reader() -> None:
    selection = derive_affected_tests([("M", "docs/DECISIONS.md")])
    assert _CAP_READER in selection["selected"]
