"""Tests for scripts/roadmap/read_set_report.py: the measure-only read-set report (rec-5256)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.roadmap import read_set_report as rsr

REPO = Path(__file__).resolve().parents[1]

_CONFIG = {
    "runs": {
        "alpha": {
            "entry_files": ["entry.md"],
            "files": [
                {"path": "entry.md", "band": "always_read", "basis": "x"},
                {"path": "big.yaml", "band": "always_read", "alt_band": "on_demand", "basis": "x"},
                {"path": "gate.md", "band": "gate_subagent", "basis": "x"},
                {"path": "later.md", "band": "on_demand", "basis": "x"},
                {"path": "absent.md", "band": "on_demand", "basis": "x"},
            ],
            "extra": [{"path": "extra.md", "when": "sometimes"}],
            "exclude": [{"path": "skipped.md", "reason": "output only"}],
            "baselines": [
                {
                    "boundary": "old",
                    "ref": "HEAD",
                    "primary": {"always_read": 1, "gate_subagent": 2, "on_demand": 3},
                    "alternative": {"always_read": 1, "gate_subagent": 2, "on_demand": 3},
                }
            ],
        },
        "beta": {"files": [{"path": "entry.md", "band": "always_read", "basis": "x"}]},
    }
}
_ENTRY = (
    "Read `unlisted.md` and docs/contracts/bare.yaml and the `demo` skill. Also big.yaml, skipped.md, extra.md,\n"
    "docs/plans/PLAN-x.yaml, logs/x.json, scripts/mod.py, nothere.md, and the `ghost` skill.\n"
)


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")
    _write(root, "entry.md", _ENTRY)
    _write(root, "big.yaml", "x" * 100)
    _write(root, "gate.md", "g" * 40)
    _write(root, "later.md", "l" * 20)
    _write(root, "extra.md", "e" * 7)
    _write(root, "unlisted.md", "u" * 11)
    _write(root, "skipped.md", "s")
    _write(root, "docs/contracts/bare.yaml", "b" * 5)
    _write(root, "docs/plans/PLAN-x.yaml", "p")
    _write(root, "logs/x.json", "{}")
    _write(root, "scripts/mod.py", "pass")
    _write(root, ".claude/skills/demo/SKILL.md", "d" * 3)
    _write(root, "config/agent/read_sets.yaml", yaml.safe_dump(_CONFIG))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "one")
    _write(root, "big.yaml", "x" * 200)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "two")
    return root


def _lines(capsys: pytest.CaptureFixture[str]) -> list[str]:
    return capsys.readouterr().out.splitlines()


def test_read_set_bytes_reported_by_band(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert rsr.main(["--root", str(repo)]) == 0
    lines = _lines(capsys)
    entry = len(_ENTRY.encode())
    assert (
        f"alpha @worktree primary always_read={entry + 200} gate_subagent=40 on_demand=20 total={entry + 260} tokens="
        in lines[0]
    )
    assert f"alpha @worktree alternative always_read={entry} gate_subagent=40 on_demand=220 total={entry + 260}" in lines[1]
    assert any(line.startswith("beta @worktree primary") for line in lines)
    assert any(line.startswith("beta @worktree alternative") for line in lines)


def test_tokens_and_baseline_delta(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rsr.main(["--root", str(repo)])
    lines = _lines(capsys)
    entry = len(_ENTRY.encode())
    assert f"tokens={(entry + 240) // 4}" in lines[0]
    base = next(line for line in lines if line.startswith("alpha baseline old primary"))
    assert "total=6" in base and f"delta_total=+{entry + 254}" in base
    assert "alpha extra total=7" in lines


def test_ref_mode_reads_the_tree_at_the_ref(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rsr.main(["--root", str(repo), "--ref", "HEAD~1"])
    lines = _lines(capsys)
    entry = len(_ENTRY.encode())
    assert any(line.startswith(f"alpha @HEAD~1 primary always_read={entry + 100} ") for line in lines)
    assert "alpha @HEAD~1 drift (curated at HEAD; old-ref paths expected)" in lines


def test_absent_file_counts_zero_and_files_rows(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rsr.main(["--root", str(repo), "--files"])
    lines = _lines(capsys)
    assert "alpha file absent.md on_demand - 0" in lines
    assert "alpha file big.yaml always_read on_demand 200" in lines


def test_drift_scan_lists_unbanded_path_and_skill(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rsr.main(["--root", str(repo)])
    lines = _lines(capsys)
    unlisted = [line for line in lines if line.startswith("alpha unlisted")]
    assert unlisted == [
        "alpha unlisted .claude/skills/demo/SKILL.md 3",
        "alpha unlisted docs/contracts/bare.yaml 5",
        "alpha unlisted unlisted.md 11",
    ]
    assert "beta unlisted: none" in lines


def test_named_paths_skips_non_targets() -> None:
    tracked = {"a.md", "docs/plans/PLAN-x.yaml", "logs/y.json", "m.py", "docs/contracts/c.yaml"}
    text = "a.md docs/plans/PLAN-x.yaml logs/y.json m.py c.yaml gone.md"
    assert rsr.named_paths(text, tracked) == {"a.md", "docs/contracts/c.yaml"}


def test_malformed_or_missing_config_prints_and_returns_zero(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for name, text in {
        "bad": "runs: [",
        "norun": "a: 1\n",
        "badrow": "runs:\n  r:\n    files: [{path: x, band: nope}]\n",
    }.items():
        _write(tmp_path, f"{name}.yaml", text)
        assert rsr.main(["--root", str(repo), "--config", str(tmp_path / f"{name}.yaml")]) == 0
        assert "config error" in capsys.readouterr().out
    assert rsr.main(["--root", str(repo), "--config", str(tmp_path / "missing.yaml")]) == 0
    assert "config error" in capsys.readouterr().out


def test_unknown_ref_and_empty_ref_print_and_return_zero(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert rsr.main(["--root", str(repo), "--ref", "no-such-ref"]) == 0
    assert "git error" in capsys.readouterr().out
    assert rsr.main(["--root", str(tmp_path), "--config", str(repo / "config/agent/read_sets.yaml")]) == 0
    assert "git error" in capsys.readouterr().out
    _git(repo, "checkout", "-q", "--orphan", "empty")
    _git(repo, "rm", "-rfq", ".")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "empty")
    assert rsr.main(["--root", str(repo), "--ref", "HEAD", "--config", str(tmp_path / "bad.yaml")]) == 0
    capsys.readouterr()
    _write(tmp_path, "ok.yaml", yaml.safe_dump(_CONFIG))
    assert rsr.main(["--root", str(repo), "--ref", "HEAD", "--config", str(tmp_path / "ok.yaml")]) == 0
    assert "lists no files" in capsys.readouterr().out


def test_entry_file_missing_reads_as_empty(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / "entry.md").unlink()
    rsr.main(["--root", str(repo)])
    assert "alpha unlisted: none" in _lines(capsys)


def test_real_config_has_three_bands_per_run() -> None:
    data, error = rsr.load_read_sets(REPO / rsr.DEFAULT_CONFIG)
    assert error is None and data is not None
    assert set(data["runs"]) == {"planning", "implementing"}
    for run in data["runs"].values():
        assert {r["band"] for r in run["files"]} == set(rsr.BANDS)


def test_stored_baselines_match_recompute() -> None:
    data, _ = rsr.load_read_sets(REPO / rsr.DEFAULT_CONFIG)
    assert data is not None
    for name, run in data["runs"].items():
        for base in run["baselines"]:
            probe = subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", base["ref"]], capture_output=True, check=False)
            if probe.returncode != 0:
                pytest.skip("env: baseline ref not in this clone")
            sizes = rsr.file_sizes(REPO, base["ref"])
            for reading in rsr.READINGS:
                assert base[reading] == rsr.band_totals(run, sizes, reading), (name, base["boundary"], reading)
