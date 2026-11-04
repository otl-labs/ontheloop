"""Mirror tests for scripts/checks/contracts/validate_north_star.py (Decision 243), in a temporary git repository."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks import registry
from scripts.checks.contracts import _manifest
from scripts.checks.contracts import validate_north_star as vn
from scripts.north_star_models import load_north_star

_REPO = Path(__file__).resolve().parents[3]
_REL = "docs/contracts/north-star.yaml"
_RATIFIED = 243


def _git(root: Path, *args: str) -> str:
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    done = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True, env={**env, "PATH": "/usr/bin:/bin"}
    )
    return done.stdout.strip()


def _write(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8")


def _contract() -> dict[str, Any]:
    return yaml.safe_load((_REPO / _REL).read_text(encoding="utf-8"))


def _decisions(data: dict[str, Any]) -> str:
    numbers = {_RATIFIED, *data["mission"]["carried_by"]}
    for entry in data["principles"]:
        numbers.update(entry["carried_by"])
    return "# Decisions\n\n" + "".join(f"## Decision {n}: Title {n}\n\nBody.\n\n---\n" for n in sorted(numbers))


def _readme(root: Path) -> str:
    return vn.render_readme_block(load_north_star(root / _REL))


def _put_readme(root: Path, block: str) -> None:
    _write(root, "README.md", f"# Readme\n\n{block}\n\nTail.\n")


def _build(root: Path) -> None:
    _git(root, "init", "-q", "-b", "main")
    data = _contract()
    _write(root, _REL, (_REPO / _REL).read_text(encoding="utf-8"))
    _write(root, "docs/DECISIONS.md", _decisions(data))
    _write(root, "docs/ROADMAP-PLATFORM.yaml", f"north_star:\n  source: {_REL}\ntier_items: []\n")
    _write(root, "config/agent/executor/capabilities.yaml", f"boundary_patterns:\n  - scripts/executor/\n  - {_REL}\n")
    _put_readme(root, _readme(root))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "update-ref", "refs/remotes/origin/main", _git(root, "rev-parse", "HEAD"))
    _git(root, "checkout", "-q", "-b", "feature")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    _build(tmp_path)
    return tmp_path


def _run(root: Path) -> tuple[list[str], str]:
    failed: list[str] = []
    vn.validate_north_star(failed, root)
    return failed, ""


def _edit(root: Path, mutate: Any) -> None:
    data = yaml.safe_load((root / _REL).read_text(encoding="utf-8"))
    mutate(data)
    _write(root, _REL, yaml.safe_dump(data, sort_keys=False))
    _put_readme(root, _readme(root))


def _failing(root: Path, capsys: pytest.CaptureFixture[str]) -> str:
    failed, _ = _run(root)
    out = capsys.readouterr().out
    assert failed == ["North star"], out
    return out


def test_fixture_tree_passes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    failed, _ = _run(repo)
    assert failed == [], capsys.readouterr().out


def test_unknown_carried_by_fails(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _edit(repo, lambda d: d["principles"][0]["carried_by"].append(9999))
    assert "CARRIED: NS.1 carried_by 9999" in _failing(repo, capsys)


def test_unknown_mission_carried_by_fails(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _edit(repo, lambda d: d["mission"]["carried_by"].append(9998))
    assert "CARRIED: mission carried_by 9998" in _failing(repo, capsys)


def test_ratified_via_unresolved_fails(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _edit(repo, lambda d: d["contract"].update(ratified_via="Decision 9999 / PLAN-x"))
    assert "CONTRACT: ratified_via" in _failing(repo, capsys)


def _unknown(d: dict[str, Any]) -> None:
    d["partial_order"]["groups"][0].append("NS.99")


def _retired(d: dict[str, Any]) -> None:
    d["principles"][5]["status"] = "retired"


def _twice(d: dict[str, Any]) -> None:
    d["partial_order"]["groups"][1].append("NS.6")


@pytest.mark.parametrize(
    ("mutate", "needle"), [(_unknown, "NS.99 is not a known"), (_retired, "NS.6 is retired"), (_twice, "NS.6 appears more")]
)
def test_partial_order_defect_fails(repo: Path, capsys: pytest.CaptureFixture[str], mutate: Any, needle: str) -> None:
    _edit(repo, mutate)
    assert needle in _failing(repo, capsys)


def test_core_ids_required(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    _edit(repo, lambda d: d["principles"].pop(2))
    assert "IDS: core entry NS.3 is missing" in _failing(repo, capsys)


def test_removed_id_against_base_fails(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _edit(repo, lambda d: d["principles"].pop(8))
    out = _failing(repo, capsys)
    assert "IDS: NS.9 is on the base" in out


def test_retired_id_against_base_passes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def retire(d: dict[str, Any]) -> None:
        d["principles"][1]["status"] = "retired"

    _edit(repo, retire)
    failed, _ = _run(repo)
    assert failed == [], capsys.readouterr().out


def test_no_base_skips_base_leg_only(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    failed, _ = _run(repo)
    out = capsys.readouterr().out
    assert failed == [] and "SKIP: IDS leg has no diff base" in out


@pytest.mark.parametrize(
    "roadmap",
    [
        "tier_items: []\n",
        f"north_star:\n  source: {_REL}\n  extra: 1\ntier_items: []\n",
        "north_star:\n  principles:\n    - id: NS.1\ntier_items: []\n",
    ],
    ids=["key-missing", "extra-key", "old-principles"],
)
def test_roadmap_pointer_mismatch_fails(repo: Path, capsys: pytest.CaptureFixture[str], roadmap: str) -> None:
    _write(repo, "docs/ROADMAP-PLATFORM.yaml", roadmap)
    assert "ROADMAP: north_star" in _failing(repo, capsys)


@pytest.mark.parametrize(("patterns", "passes"), [(["scripts/executor/"], False), (["docs/contracts/"], True)])
def test_boundary_pattern_missing_fails(
    repo: Path, capsys: pytest.CaptureFixture[str], patterns: list[str], passes: bool
) -> None:
    _write(repo, "config/agent/executor/capabilities.yaml", yaml.safe_dump({"boundary_patterns": patterns}))
    failed, _ = _run(repo)
    out = capsys.readouterr().out
    assert (failed == []) is passes
    assert passes or f"BOUNDARY: no boundary_patterns entry covers {_REL}" in out


def test_readme_drift_fails(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    text = (repo / "README.md").read_text(encoding="utf-8")
    _write(repo, "README.md", text.replace("- **NS.4** - ", "- **NS.4** - drifted "))
    assert "README: the North Star block differs" in _failing(repo, capsys)


def _no_end(t: str) -> str:
    return t.replace(vn._END + "\n", "")


def _two_begins(t: str) -> str:
    return t.replace(vn._INTRO, vn._BEGIN + "\n" + vn._INTRO, 1)


def _end_first(t: str) -> str:
    block = vn.render_readme_block(load_north_star(_REPO / _REL))
    return t.replace(block, vn._END + "\nmid\n" + vn._BEGIN)


@pytest.mark.parametrize(
    "break_it", [_no_end, _two_begins, _end_first], ids=["end-missing", "begin-duplicated", "end-before-begin"]
)
def test_readme_marker_defect_fails(repo: Path, capsys: pytest.CaptureFixture[str], break_it: Any) -> None:
    _write(repo, "README.md", break_it((repo / "README.md").read_text(encoding="utf-8")))
    assert "README: expected one begin marker" in _failing(repo, capsys)


def test_unloadable_contract_fails_and_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    skipped: list[str] = []
    monkeypatch.setattr(registry, "skipped", lambda reason: skipped.append(reason))
    failed: list[str] = []
    vn.validate_north_star(failed, tmp_path)
    assert failed == ["North star"] and skipped and "FAIL" in capsys.readouterr().out


def test_real_tree_passes(capsys: pytest.CaptureFixture[str]) -> None:
    failed: list[str] = []
    vn.validate_north_star(failed)
    assert failed == [], capsys.readouterr().out


def test_entry_resolves_and_is_sequenced() -> None:
    assert registry.resolve("validate_north_star") is vn.validate_north_star
    entry = next(e for e in _manifest.ENTRIES if e.name == "validate_north_star")
    assert entry.pre is True and entry.full_segment == "full_after_lint"


def test_contract_header_defects_fail(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def wrong(d: dict[str, Any]) -> None:
        d["contract"]["subject"] = "other"
        d["contract"]["evaluator"] = {"check": "validate_other"}

    _edit(repo, wrong)
    out = _failing(repo, capsys)
    assert "contract.subject is not north-star" in out and "evaluator.check is not validate_north_star" in out


def test_unparseable_base_copy_is_ignored(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(repo, _REL, "not: [valid\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "bad base")
    _git(repo, "update-ref", "refs/remotes/origin/main", _git(repo, "rev-parse", "HEAD"))
    _write(repo, _REL, (_REPO / _REL).read_text(encoding="utf-8"))
    failed, _ = _run(repo)
    assert failed == [], capsys.readouterr().out


@pytest.mark.parametrize(
    ("rel", "needle"),
    [
        ("docs/ROADMAP-PLATFORM.yaml", "ROADMAP: docs/ROADMAP-PLATFORM.yaml is unreadable"),
        ("config/agent/executor/capabilities.yaml", "BOUNDARY: capabilities.yaml is unreadable"),
        ("README.md", "README: README.md is unreadable"),
    ],
)
def test_missing_projection_file_fails(repo: Path, capsys: pytest.CaptureFixture[str], rel: str, needle: str) -> None:
    (repo / rel).unlink()
    assert needle in _failing(repo, capsys)
