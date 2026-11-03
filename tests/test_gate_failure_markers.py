"""rec-4172 acceptance: gate failure markers through the real dispatch path and the pre-commit grammar."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.checks import registry, validation_result
from src.turn_capture.markers import SIGNATURE_PATTERN, format_gate_marker, parse_markers

_ROOT = Path(__file__).resolve().parents[1]
_CONTRACT = _ROOT / "docs" / "contracts" / "telemetry_observations.yaml"


@pytest.fixture(autouse=True)
def _reset_accumulators():
    validation_result._ATTRIBUTIONS.clear()
    validation_result._SCAFFOLD_FAILURES.clear()
    yield
    validation_result._ATTRIBUTIONS.clear()
    validation_result._SCAFFOLD_FAILURES.clear()


def _contract_pattern() -> re.Pattern[str]:
    spec = yaml.safe_load(_CONTRACT.read_text(encoding="utf-8"))["fields"]["name"]["dq_intent"]["pattern_when"][0]
    return re.compile(spec["pattern"])


def _scaffold_names() -> set[str]:
    names = set(registry._PRE_TIER_LEADING_SCAFFOLDS) | set(registry._PRE_TIER_TRAILING_SCAFFOLDS)
    return names | {name for name, _ in registry._FULL_TIER_SKELETON}


def test_one_marker_per_failed_check_and_none_on_success(capsys: pytest.CaptureFixture[str]) -> None:
    failed: list[str] = []
    validation_result.dispatch_recording("synthetic_ok_check", failed, lambda f: None)
    validation_result.run_scaffold("synthetic_ok_scaffold", failed, lambda: None)
    assert validation_result.failure_markers() == []

    def two_labels(f: list[str]) -> None:
        f.extend(["first label", "second label"])

    validation_result.dispatch_recording("synthetic_bad_check", failed, two_labels)
    validation_result.run_scaffold("synthetic_bad_scaffold", failed, lambda: failed.append("scaffold label"))
    validation_result.print_failure_summary(failed)
    out = capsys.readouterr().out
    assert out.count("GATE-FAILURE") == 2
    assert [(m.source, m.signature) for m in parse_markers(out)] == [
        ("gate", "synthetic_bad_check"),
        ("gate", "synthetic_bad_scaffold"),
    ]


def test_every_gate_and_precommit_name_fits_the_contract_pattern() -> None:
    pattern = _contract_pattern()
    checks = set(registry.all_checks())
    scaffolds = _scaffold_names()
    assert checks.isdisjoint(scaffolds), checks & scaffolds
    for name in sorted(checks | scaffolds):
        marker = format_gate_marker(name)
        assert [m.signature for m in parse_markers(marker)] == [name]
        assert pattern.fullmatch(f"gate:{name}"), name
    config = yaml.safe_load((_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hook_ids = [hook["id"] for repo in config["repos"] for hook in repo["hooks"]]
    assert hook_ids
    for hook_id in hook_ids:
        assert re.fullmatch(SIGNATURE_PATTERN, hook_id), hook_id
        assert pattern.fullmatch(f"precommit:{hook_id}"), hook_id


def test_real_precommit_failure_output_parses_to_hook_id(tmp_path: Path) -> None:
    pytest.importorskip("pre_commit")
    repo = tmp_path / "repo"
    repo.mkdir()
    config = {
        "repo": "local",
        "hooks": [
            {"id": "always-fails", "name": "always-fails", "entry": "fail", "language": "fail", "files": r"\.txt$"},
            {
                "id": "modifies-file",
                "name": "modifies-file",
                "entry": 'bash -c \'for f in "$@"; do echo changed >> "$f"; done\' --',
                "language": "system",
                "files": r"\.txt$",
            },
            {
                "id": "passes-verbose",
                "name": "passes-verbose",
                "entry": "true",
                "language": "system",
                "files": r"\.txt$",
                "verbose": True,
                "pass_filenames": False,
            },
        ],
    }
    (repo / ".pre-commit-config.yaml").write_text(yaml.safe_dump({"repos": [config]}), encoding="utf-8")
    env = {
        **os.environ,
        "PRE_COMMIT_HOME": str(tmp_path / "pc-home"),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "NO_COLOR": "1",
    }

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, cwd=repo, env=env, capture_output=True, text=True, check=False)

    run("git", "init", "-q")
    (repo / "a.txt").write_text("x\n", encoding="utf-8")
    run("git", "add", "-A")
    result = run(sys.executable, "-m", "pre_commit", "run", "--all-files")
    assert result.returncode != 0, result.stdout + result.stderr
    found = parse_markers(result.stdout + result.stderr)
    assert [(m.source, m.signature) for m in found] == [("precommit", "always-fails"), ("precommit", "modifies-file")]
