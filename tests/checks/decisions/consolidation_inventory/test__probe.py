"""Mirror of _probe.py: the probe on a tmp_path git repo that carries the parser modules and bin/venv-python."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.checks import _process_group
from scripts.checks.decisions.consolidation_inventory import _probe, inventory, probe
from scripts.checks.decisions.consolidation_inventory.__main__ import main
from tests.checks.decisions.consolidation_inventory import build_repo

OUTPUT_RECORD = (
    "check_id: pin-output-record\nprimitive_slot: command_output_matches\nguard_target: docs/DECISIONS.md\n"
    "plan_slug: fixture\ngraduated_at: '2026-01-01'\ncheck_spec:\n  command:\n    - bin/venv-python\n    - -c\n    - |\n"
    "      t = open('docs/DECISIONS.md').read()\n"
    "      print('pinned' if 'ZZZ-BODY-PIN' in t.split('## Decision 9002:')[1] else 'gone')\n"
    "  expected: pinned\n"
)
ARCHIVE_TEST = (
    "from pathlib import Path\n\nARCHIVE = Path(__file__).resolve().parents[1] / 'docs' / 'DECISIONS_ARCHIVE.md'\n\n\n"
    "def test_archive_pin():\n    assert 'Decision 9100' in ARCHIVE.read_text(encoding='utf-8')\n"
)
ARCHIVE = "## Decision 9100: Archived (Decided)\n\n**Decision:** old.\n"


def _git_list(root: Path) -> str:
    return subprocess.run(["git", "worktree", "list"], cwd=root, capture_output=True, text=True, check=True).stdout


@pytest.fixture(scope="module")
def probed(tmp_path_factory: pytest.TempPathFactory):
    root = build_repo(
        tmp_path_factory.mktemp("probed"),
        {"config/agent/verification_registry/entries/pin-output-record.yaml": OUTPUT_RECORD},
        with_tools=True,
    )
    before = _git_list(root)
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("OTL_VENV_PYTHON", raising=False)
        inv = probe(root, inventory(root, [9001, 9002]))
    return root, inv, before


def _state(inv, locator_part: str) -> str:
    found = [h for h in inv.hits if locator_part in h.locator]
    assert found, locator_part
    return found[0].probe


def test_body_pin_breaks_number_pin_survives(probed) -> None:
    _, inv, _ = probed
    assert inv.probe_failed == ""
    assert _state(inv, "tests/test_pin.py::test_body_pin") == "breaks"
    assert _state(inv, "tests/test_pin.py::test_number_pin") == "survives"
    assert _state(inv, "tests/test_pin.py::test_red_before") == "red_before"
    assert _state(inv, "tests/test_pin.py::TestInClass::test_method_pin") == "breaks"
    survivor = next(h for h in inv.hits if h.locator.endswith("test_number_pin"))
    assert survivor.effect == "survives"
    breaker = next(h for h in inv.hits if h.locator.endswith("::test_body_pin"))
    assert breaker.effect == "retarget_or_retire_test" and breaker.tier == "confirmed"


def test_stubs_keep_their_entry_separator(probed) -> None:
    _, inv, _ = probed
    assert _state(inv, "tests/test_pin.py::test_regex_entry_pin") == "breaks"


def test_parametrized_body_pin_folds_to_one_id(probed) -> None:
    _, inv, _ = probed
    folded = [h for h in inv.hits if h.kind == "test_node" and h.locator.endswith("test_parametrized_body_pin")]
    assert len(folded) == 1 and folded[0].probe == "breaks"


def test_records_break_by_slot_judgement(probed) -> None:
    _, inv, _ = probed
    assert _state(inv, "pin-record") == "breaks"
    assert _state(inv, "pin-output-record") == "breaks"


def test_venv_python_record_breaks_without_parent_env(probed) -> None:
    _, inv, _ = probed
    assert _state(inv, "entries/pin-record.yaml") != "red_before"


def test_worktree_removed_and_tree_untouched(probed) -> None:
    root, _, before = probed
    assert _git_list(root) == before
    assert subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout.strip() == ""


def test_probe_output_states_head_template_and_note(probed) -> None:
    root, inv, _ = probed
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
    assert inv.probe_info["head"] == head
    assert inv.probe_info["stub_template"] == "compacted-stub-v1"
    assert "not probed" in inv.probe_info["note"]
    assert inv.probe_info["dirty"] is False


def test_archive_only_victim_stays_not_run(tmp_path: Path) -> None:
    root = build_repo(tmp_path, {"tests/test_archive.py": ARCHIVE_TEST, "docs/DECISIONS_ARCHIVE.md": ARCHIVE}, with_tools=True)
    (root / "scratch.txt").write_text("dirty", encoding="utf-8")
    inv = probe(root, inventory(root, [9100]))
    hit = next(h for h in inv.hits if h.locator.endswith("test_archive_pin"))
    assert hit.probe == "not_run"
    assert "no live section" in inv.probe_info["reasons"][hit.locator]
    assert inv.probe_info["dirty"] is True


def test_git_failure_leaves_hits_not_run_and_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_repo(tmp_path, with_tools=True)

    def boom(cwd: Path, *args: str) -> str:
        raise _probe.ProbeError("git unavailable")

    monkeypatch.setattr(_probe, "_git", boom)
    inv = probe(root, inventory(root, [9002]))
    assert inv.probe_failed == "git unavailable"
    assert all(h.probe == "not_run" for h in inv.hits)
    assert main(["9002", "--root", str(root), "--probe"]) == 1
    assert "probe failed" in capsys.readouterr().err


def test_timeout_reaches_the_process_group_kill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = build_repo(tmp_path, with_tools=True)
    killed: list[int] = []
    real = _process_group.kill_and_reap

    def spy(popen):
        killed.append(popen.pid)
        return real(popen)

    monkeypatch.setattr(_process_group, "kill_and_reap", spy)
    monkeypatch.setattr(_probe, "NODE_TIMEOUT_S", 0.05)
    inv = probe(root, inventory(root, [9002]))
    assert killed and "timed out" in inv.probe_failed
    assert all(h.probe == "not_run" for h in inv.hits)
    assert _git_list(root).count("\n") == 1


def test_a_directory_that_is_not_a_repository_fails_loudly(tmp_path: Path) -> None:
    inv = probe(tmp_path, inventory(tmp_path, [9001]))
    assert "git rev-parse HEAD failed" in inv.probe_failed


def test_git_that_cannot_run_is_a_probe_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args, **kwargs):
        raise OSError("no git here")

    monkeypatch.setattr(_probe.subprocess, "run", broken)
    inv = probe(tmp_path, inventory(tmp_path, [9001]))
    assert "no git here" in inv.probe_failed


def test_a_worktree_without_the_index_module_skips_regeneration(tmp_path: Path) -> None:
    root = build_repo(tmp_path)
    inv = probe(root, inventory(root, [9002]))
    assert inv.probe_failed == "" and any("index regeneration skipped" in n for n in inv.probe_info["notes"])


def test_an_index_regeneration_timeout_is_a_probe_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = build_repo(tmp_path, with_tools=True)
    real = _probe._run_group

    def slow_index(cmd, cwd, env, timeout):
        return (True, "", "") if "scripts.decisions_index" in cmd else real(cmd, cwd, env, timeout)

    monkeypatch.setattr(_probe, "_run_group", slow_index)
    assert "decisions_index --write timed out" in probe(root, inventory(root, [9002])).probe_failed


def test_node_keys_and_missing_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _probe._node_key("weird.mod", "test_x[1]", tmp_path) == "weird.mod::test_x"
    assert _probe._run_nodes(tmp_path, [], tmp_path, "none") == set()
    monkeypatch.setattr(_probe, "_run_group", lambda *a, **k: (False, "", ""))
    assert _probe._run_nodes(tmp_path, ["a.py::t"], tmp_path, "no-report") == {"a.py::t"}


def test_record_children_that_time_out_or_print_garbage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_probe, "_run_group", lambda *a, **k: (False, "garbage", ""))
    assert _probe._run_record(tmp_path, "any", "") is False
    assert _probe._run_records(tmp_path, [], "") == set()
    monkeypatch.setattr(_probe, "_run_group", lambda *a, **k: (True, "", ""))
    with pytest.raises(_probe.ProbeError, match="timed out"):
        _probe._run_record(tmp_path, "any", "")
