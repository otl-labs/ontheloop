"""Tests for scripts.checks.contracts._retired_identity: rec-3903's row evaluators for the org-transfer contract.

Fixture repositories are tmp git repositories built with git init and git add only (git grep reads the index; a
commit would need an identity), with fixture contracts and monkeypatched pin maps.
"""

from __future__ import annotations

import copy
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.checks.contracts import _retired_identity as ri
from scripts.checks.contracts.validate_github_org_transfer import load_contract

_REAL_ROOT = Path(__file__).resolve().parents[3]
_OLD = "benjamin-blake/theseus"
_GATE_BOTH = (
    "github.event.pull_request.user.login == 'dependabot[bot]' && "
    "(github.repository == 'benjamin-blake/theseus' || github.repository == 'otl-labs/ontheloop')"
)
_GATE_OLD = "github.event.pull_request.user.login == 'dependabot[bot]' && github.repository == 'benjamin-blake/theseus'"


def _contract(phase: str = "pre_transfer", staged: str | None = None) -> dict[str, Any]:
    identity: dict[str, Any] = {"phase": phase, "target": {"owner": "otl-labs", "repository": "ontheloop"}}
    if staged is not None:
        identity["staged_phase"] = staged
    rows = [{"id": rid, "detection": "static", "rec": "rec-3903", "locations": ["packages/"]} for rid in ri.ROWS]
    locations = ["infra/trust.tf", "infra/mod/", "console"]
    rows.append({"id": "trust", "detection": "static", "rec": "rec-1", "locations": locations})
    rows.append({"id": "live", "detection": "live", "rec": "rec-2", "locations": ["live/thing.txt"]})
    return {"identity": identity, "breakage_map": rows}


def _repo(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    return root


def _pins(
    monkeypatch: pytest.MonkeyPatch, sweep: dict[str, str] | None = None, survivor: dict[str, str] | None = None
) -> None:
    monkeypatch.setattr(ri, "SWEEP_PINS", sweep or {})
    monkeypatch.setattr(ri, "SURVIVOR_PINS", survivor or {})


def _evaluate(contract: dict[str, Any], root: Path) -> list[str]:
    failed: list[str] = []
    for evaluator in ri.ROW_EVALUATORS.values():
        evaluator(contract, root, failed)
    return failed


@pytest.fixture(autouse=True)
def _isolated_git(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    "line",
    ['x = "benjamin-blake/theseus"\n', 'owner: str = "benjamin-blake"\n', '_REPO_NAME = "theseus"\n'],
)
def test_new_unpinned_literal_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, line: str) -> None:
    _pins(monkeypatch)
    root = _repo(tmp_path, {"scripts/new.py": "pass\n" + line})
    failed = _evaluate(_contract(), root)
    unpinned = "names the retired identity but is in neither SWEEP_PINS nor SURVIVOR_PINS"
    assert failed == [f"{ri._LABEL}: scripts/new.py:2 {unpinned}"]


def test_stale_pin_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pins(monkeypatch, sweep={"docs/old.md": "descriptive_literals"}, survivor={"tests/gone.py": "fixture"})
    root = _repo(tmp_path, {"docs/old.md": "moved to otl-labs/ontheloop\n", "tests/gone.py": "pass\n"})
    failed = _evaluate(_contract(), root)
    assert f"{ri._LABEL}: stale sweep pin docs/old.md no longer names the retired identity; remove the pin" in failed
    assert f"{ri._LABEL}: stale survivor pin tests/gone.py no longer names the retired identity; remove the pin" in failed
    assert len(failed) == 2


def test_person_reference_brand_and_frozen_identifier_do_not_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pins(monkeypatch)
    files = {
        ".github/CODEOWNERS": "* @benjamin-blake\n",
        "docs/brand.md": "Theseus platform, the theseus monorepo, benjamin-blake's account\n",
        "infra/names.tf": 'bucket = "theseus-ops"\nrepo = "benjamin-blake/theseus-archive"\nname = "theseus"\n',
        "scripts/frozen.py": 'PREFIX = "x.benjamin-blake/theseus"\nREPO_URL = "https://example.com/" + "a" * 40 + "theseus"\n',
    }
    assert _evaluate(_contract(), _repo(tmp_path, files)) == []


def test_historical_records_and_other_recs_files_are_exempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pins(monkeypatch)
    literal = f"see {_OLD}\n"
    exempt = [
        "docs/plans/PLAN-x.yaml",
        "audits/a.md",
        "docs/audit-prompts/AUDIT-x.md",
        "tests/fixtures/payload.json",
        "docs/DECISIONS.md",
        "docs/DECISIONS_ARCHIVE.md",
        "docs/SESSION_LOG_ARCHIVE.md",
        "docs/decisions-index.json",
        "infra/trust.tf",
        "infra/mod/main.tf",
    ]
    files = dict.fromkeys(exempt, literal)
    assert _evaluate(_contract(), _repo(tmp_path, dict(files))) == []
    files["live/thing.txt"] = literal
    files["packages/pkg.json"] = literal
    failed = _evaluate(_contract(), _repo(tmp_path, files))
    assert [f.split(": ")[1].split(" ")[0] for f in failed] == ["live/thing.txt:1", "packages/pkg.json:1"]


def test_sweep_pin_must_cite_a_rec_3903_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path, {"a.md": f"{_OLD}\n", "b.md": f"{_OLD}\n"})
    _pins(monkeypatch, sweep={"a.md": "trust", "b.md": "descriptive_literals"})
    assert _evaluate(_contract(), root) == [
        f"{ri._LABEL}: sweep pin a.md cites 'trust', which is not one of rec-3903's rows {list(ri.ROWS)}"
    ]
    contract = _contract()
    next(r for r in contract["breakage_map"] if r["id"] == "descriptive_literals")["rec"] = "rec-9"
    expected = f"{ri._LABEL}: breakage_map row 'descriptive_literals' must exist with rec rec-3903"
    assert _evaluate(contract, root)[0] == expected
    _pins(monkeypatch, sweep={"a.md": "codeowners", "b.md": "descriptive_literals"})
    assert any("codeowners carries no repository literal" in f for f in _evaluate(_contract(), root))


@pytest.mark.parametrize(
    ("phase", "gate", "ok"),
    [
        ("pre_transfer", _GATE_OLD, True),
        ("pre_staged", _GATE_OLD, False),
        ("pre_staged", _GATE_BOTH, True),
        ("transferred", _GATE_OLD, False),
        ("proven", _GATE_BOTH, True),
        ("predecessor_removed", "github.repository == 'otl-labs/ontheloop'", True),
    ],
)
def test_dependabot_gate_must_name_the_target_from_pre_staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str, gate: str, ok: bool
) -> None:
    _pins(monkeypatch)
    workflow = f'jobs:\n  auto-merge:\n    if: "{gate}"\n'
    failed = _evaluate(_contract(phase), _repo(tmp_path, {ri._DEPENDABOT_WORKFLOW: workflow.replace(_OLD, "x/y")}))
    assert (failed == []) is ok
    if not ok:
        rule = "from pre_staged on the dependabot auto-merge gate must contain github.repository == 'otl-labs/ontheloop'"
        assert failed == [f"{ri._LABEL}: {rule}"]


def test_no_sweep_pin_may_remain_at_predecessor_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = "jobs:\n  auto-merge:\n    if: github.repository == 'otl-labs/ontheloop'\n"
    root = _repo(tmp_path, {ri._DEPENDABOT_WORKFLOW: gate, "docs/a.md": f"{_OLD}\n", "docs/b.md": f"{_OLD}\n"})
    _pins(monkeypatch, sweep={"docs/a.md": "descriptive_literals", "docs/b.md": "owner_literal_defaults"})
    failed = _evaluate(_contract("predecessor_removed"), root)
    assert sorted(failed) == sorted(
        [
            f"{ri._LABEL}: at predecessor_removed no sweep pin may cite 'descriptive_literals': ['docs/a.md']",
            f"{ri._LABEL}: at predecessor_removed no sweep pin may cite 'owner_literal_defaults': ['docs/b.md']",
        ]
    )
    _pins(monkeypatch, survivor={"docs/a.md": "record", "docs/b.md": "record"})
    assert _evaluate(_contract("predecessor_removed"), root) == []


def test_sweep_pins_pass_at_proven_with_staged_predecessor_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workflow = f'jobs:\n  auto-merge:\n    if: "{_GATE_BOTH}"\n'
    root = _repo(tmp_path, {ri._DEPENDABOT_WORKFLOW: workflow, "docs/a.md": f"{_OLD}\n"})
    _pins(monkeypatch, sweep={ri._DEPENDABOT_WORKFLOW: "dependabot_repository_guard", "docs/a.md": "descriptive_literals"})
    assert _evaluate(_contract("proven", staged="predecessor_removed"), root) == []


def test_unreadable_contract_or_git_failure_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pins(monkeypatch)
    failed = _evaluate({"identity": "not-a-mapping"}, _repo(tmp_path, {"a.md": "clean\n"}))
    assert sorted(failed) == sorted(f"{ri._LABEL}: breakage_map row {rid!r} must exist with rec rec-3903" for rid in ri.ROWS)
    assert any("auto-merge gate unreadable" in f for f in _evaluate(_contract("pre_staged"), tmp_path))
    failure = subprocess.CompletedProcess(args=[], returncode=128, stdout="", stderr="fatal")
    monkeypatch.setattr(ri.subprocess, "run", lambda *a, **k: failure)
    closed = f"{ri._LABEL}: git grep failed (exit 128); the retired-identity scan fails closed"
    assert closed in _evaluate(_contract(), tmp_path)

    def _missing_git(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        raise FileNotFoundError("git")

    monkeypatch.setattr(ri.subprocess, "run", _missing_git)
    assert any("git grep could not run" in f for f in _evaluate(_contract(), tmp_path))


def test_pin_on_an_exempt_path_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pins(monkeypatch, survivor={"docs/DECISIONS.md": "record"})
    root = _repo(tmp_path, {"docs/DECISIONS.md": f"{_OLD}\n"})
    expected = f"{ri._LABEL}: survivor pin docs/DECISIONS.md is on an exempt path; remove the pin"
    assert _evaluate(_contract(), root) == [expected]


def test_pins_are_literal_string_maps_on_disjoint_paths() -> None:
    pairs = [*ri.SWEEP_PINS.items(), *ri.SURVIVOR_PINS.items()]
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in pairs)
    assert not set(ri.SWEEP_PINS) & set(ri.SURVIVOR_PINS)
    assert set(ri.SWEEP_PINS.values()) <= set(ri.ROWS) - {"codeowners"}


def test_live_tree_passes() -> None:
    contract = load_contract(_REAL_ROOT)
    assert isinstance(contract, dict)
    assert _evaluate(copy.deepcopy(contract), _REAL_ROOT) == []
