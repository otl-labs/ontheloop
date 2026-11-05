"""rg-free guard that ensure_entity_counters_table( is only called from control_tables modules.

Holds the claim of the graduated record ungoverned-counter-creation-retired without ripgrep, which the CI runner lacks.
"""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
NEEDLE = "ensure_entity_counters_table" + "("
SCOPE = ("src", "scripts")


def _stray_files(root: Path) -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", *SCOPE],
        cwd=str(root),
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8", errors="replace")
    stray: list[str] = []
    for rel in filter(None, listed.split("\0")):
        if any("control_tables" in part for part in Path(rel).parts):
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if NEEDLE in text:
            stray.append(rel)
    return sorted(stray)


def test_no_stray_counter_table_creation() -> None:
    assert _stray_files(REPO_ROOT) == []


def test_stray_call_is_detected(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=str(tmp_path), capture_output=True, check=True)

    git("init", "-q")
    (tmp_path / "src").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "src" / "ducklake_control_tables.py").write_text(f"x = {NEEDLE})\n", encoding="utf-8")
    (tmp_path / "scripts" / "stray.sh").write_text(f"echo {NEEDLE}\n", encoding="utf-8")
    (tmp_path / "scripts" / "clean.py").write_text("x = 1\n", encoding="utf-8")
    git("add", "-A")
    assert _stray_files(tmp_path) == ["scripts/stray.sh"]
