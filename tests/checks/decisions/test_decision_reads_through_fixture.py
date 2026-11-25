"""Mirror test for validate_decision_reads_through_fixture (rec-5638): synthetic two-commit repos under tmp_path."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.checks import registry
from scripts.checks.decisions import decision_reads_through_fixture as drf
from scripts.checks.decisions.decision_reads_through_fixture import ad_hoc_sites, validate_decision_reads_through_fixture

TEST_REL = "tests/test_x.py"
REAL = 'from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\nDEC = "docs/DECISIONS.md"\n'
READ = "def test_a():\n    text = (ROOT / DEC).read_text(encoding='utf-8')\n"
ONE = READ + '    assert text.split("## Decision 72:")[1]\n'
TWO = ONE + '    assert text.split("## Decision 73:")[1]\n'
FIXTURE_READ = READ + "    block = decision_entry(text, 72)\n    assert block\n"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _write(root: Path, files: dict[str, str | None]) -> None:
    for rel, text in files.items():
        target = root / rel
        if text is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def _commit(root: Path, msg: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "--allow-empty", "-m", msg)


def _repo(tmp_path: Path, base: dict[str, str | None], branch: dict[str, str | None], commit_branch: bool = True) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    for key, value in (("user.name", "t"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
        _git(root, "config", key, value)
    _write(root, base)
    _commit(root, "base")
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(bare))
    _git(root, "remote", "add", "origin", str(bare))
    _git(root, "push", "-q", "origin", "main")
    _git(root, "fetch", "-q", "origin")
    _git(root, "checkout", "-q", "-b", "feature")
    _write(root, branch)
    if commit_branch:
        _commit(root, "branch")
    return root


def _run(root: Path) -> tuple[list[str], registry._Declaration | None]:
    failed: list[str] = []
    registry.pop_declaration()
    validate_decision_reads_through_fixture(failed, root=root)
    return failed, registry.pop_declaration()


@pytest.fixture(autouse=True)
def _no_push_context(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GITHUB_EVENT_NAME", "GITHUB_EVENT_BEFORE", "FULL_TIER_PREV", "FULL_TIER_LAST_GREEN"):
        monkeypatch.delenv(name, raising=False)


def test_ad_hoc_entry_reads_never_rise_against_base(tmp_path: Path) -> None:
    root = _repo(tmp_path, {TEST_REL: REAL + ONE}, {TEST_REL: REAL + TWO})
    failed, decl = _run(root)
    assert len(failed) == 1
    assert TEST_REL in failed[0] and "from 1 to 2" in failed[0] and "decision_entry" in failed[0]
    equal = tmp_path / "equal"
    equal.mkdir()
    root = _repo(equal, {TEST_REL: REAL + ONE}, {TEST_REL: REAL + ONE + "# touched\n"})
    failed, decl = _run(root)
    assert failed == []
    assert decl is not None and decl.kind == "examined" and decl.count == 1


def test_fixture_reads_are_not_counted(tmp_path: Path) -> None:
    sites, gate = ad_hoc_sites(REAL + FIXTURE_READ)
    assert sites == [] and gate is True
    root = _repo(tmp_path, {TEST_REL: REAL + ONE}, {TEST_REL: REAL + FIXTURE_READ})
    assert _run(root)[0] == []
    sites, _ = ad_hoc_sites(REAL + READ + "    live_block(text, 72)\n    resolve_entry(text, 72)\n")
    assert sites == []


def test_synthetic_log_reader_is_not_counted() -> None:
    src = (
        'def test_a(tmp_path):\n    p = tmp_path / "docs" / "DECISIONS.md"\n'
        '    t = p.read_text()\n    t.split("## Decision 5: X")\n'
    )
    sites, gate = ad_hoc_sites(src)
    assert gate is False and len(sites) == 1


def test_whole_log_walk_is_not_counted() -> None:
    src = REAL + (
        "from scripts.decisions_md import iter_decision_sections\n"
        "def test_a():\n    text = (ROOT / DEC).read_text()\n"
        "    for match, block in iter_decision_sections(text):\n        assert block\n"
        "    assert [b for m, b in iter_decision_sections(text)]\n"
    )
    assert ad_hoc_sites(src) == ([], True)


def test_wrapped_group_filter_is_counted() -> None:
    loop = (
        "def test_a():\n    text = (ROOT / DEC).read_text()\n"
        "    for match, block in iter_decision_sections(text):\n"
        "        if int(match.group(1)) == 72:\n            assert block\n"
    )
    sites, gate = ad_hoc_sites(REAL + loop)
    assert gate and [shape for _, shape in sites] == ["sections"]
    comp = "    x = [b for m, b in _iter_decision_sections(text) if m.group(1) in ('1', '2')]\n"
    dict_comp = "    y = {int(m.group(1)): b for m, b in iter_decision_sections(text)}\n"
    sites, _ = ad_hoc_sites(REAL + READ + comp + dict_comp)
    assert len(sites) == 2


def test_module_constant_path_is_a_real_read() -> None:
    for src in (
        'DEC = "docs/DECISIONS.md"\nROOT = None\nX = ROOT / DEC\n',
        'from pathlib import Path\nX = Path(__file__).resolve().parents[2] / "docs" / "DECISIONS.md"\n',
        'from pathlib import Path\nROOT = Path(__file__)\nX = ROOT.joinpath("docs", "DECISIONS.md")\n',
        'LOG: str = "docs/DECISIONS.md"\nfrom pathlib import Path\nX = Path(LOG)\n',
    ):
        assert ad_hoc_sites(src)[1] is True, src
    assert ad_hoc_sites('def f(root):\n    return root / "docs" / "DECISIONS.md"\n')[1] is False


def test_def_helper_read_is_a_real_read() -> None:
    src = 'def _read(p):\n    return p\nDEC = "docs/DECISIONS.md"\nT = _read("docs/DECISIONS.md")\nU = _read(DEC)\n'
    assert ad_hoc_sites(src)[1] is True
    sites, _ = ad_hoc_sites(
        src + 'import re\nV = re.findall(r"## Decision 5: Title", T)\n' + 'N = 3\nW = "## Decision %d: " % N\nT.index(W)\n'
    )
    assert len(sites) == 2
    fmt = 'T.find("## Decision {}:".format(5))\nT.find(f"## Decision {5}:")\nT.find("## Decision " + str(5))\n'
    assert len(ad_hoc_sites(src + fmt)[0]) == 3


def test_gate_true_at_base_still_counts_after_a_path_rewrite(tmp_path: Path) -> None:
    rewritten = (
        "def test_a(tmp_path):\n    text = tmp_path.read_text()\n"
        '    text.split("## Decision 72:")\n    text.split("## Decision 73:")\n'
    )
    root = _repo(tmp_path, {TEST_REL: REAL + ONE}, {TEST_REL: rewritten})
    failed, _ = _run(root)
    assert len(failed) == 1 and "from 1 to 2" in failed[0]


def test_imported_module_receiver_is_not_counted() -> None:
    src = (
        REAL
        + "import consolidation\ndef test_a():\n    (ROOT / DEC).read_text()\n"
        + '    consolidation.partition(5, "## Decision 5: Title")\n'
    )
    assert ad_hoc_sites(src) == ([], True)


def test_rename_compares_against_the_old_path(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        {TEST_REL: REAL + TWO, "tests/test_gone.py": REAL + TWO},
        {TEST_REL: None, "tests/sub/test_y.py": REAL + TWO, "tests/test_gone.py": None},
    )
    failed, decl = _run(root)
    assert failed == []
    assert decl is not None and decl.count == 1


def test_new_file_with_ad_hoc_read_fails(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"README.md": "x\n"}, {TEST_REL: REAL + ONE}, commit_branch=False)
    failed, _ = _run(root)
    assert len(failed) == 1 and TEST_REL in failed[0] and "from 0 to 1" in failed[0]
    _commit(root, "branch")
    assert len(_run(root)[0]) == 1
    (root / "tests" / "test_new.py").write_text(REAL + ONE, encoding="utf-8")
    assert len(_run(root)[0]) == 2


def test_unparseable_head_fails_naming_the_file(tmp_path: Path) -> None:
    root = _repo(tmp_path, {TEST_REL: "def broken(:\n"}, {TEST_REL: "def still_broken(:\n"})
    failed, _ = _run(root)
    assert len(failed) == 1 and TEST_REL in failed[0] and "does not parse" in failed[0]
    other = tmp_path / "other"
    other.mkdir()
    failed, _ = _run(_repo(other, {TEST_REL: "def broken(:\n"}, {TEST_REL: REAL + ONE}))
    assert len(failed) == 1 and "from 0 to 1" in failed[0]


def test_diff_failure_fails_never_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path, {TEST_REL: REAL + ONE}, {TEST_REL: REAL + ONE})
    monkeypatch.setattr(drf._common, "status_diff_base", lambda r=None: "0" * 40)
    failed, decl = _run(root)
    assert failed and "git diff" in failed[0] and decl is not None and decl.kind == "examined"

    def boom(r=None):
        raise RuntimeError("prev is not an ancestor")

    monkeypatch.setattr(drf._common, "status_diff_base", boom)
    failed, _ = _run(root)
    assert failed and "not an ancestor" in failed[0]
    monkeypatch.setattr(drf._common, "status_diff_base", lambda r=None: "HEAD")
    real_git = drf._git

    def failing_listing(r: Path, *args: str):
        res = real_git(r, *args)
        if args[0] == "ls-files":
            res.returncode = 1
        return res

    monkeypatch.setattr(drf, "_git", failing_listing)
    failed, _ = _run(root)
    assert failed and "untracked listing failed" in failed[0]


def test_no_base_is_skipped_never_passed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path, {TEST_REL: REAL + ONE}, {TEST_REL: REAL + TWO})
    monkeypatch.setattr(drf._common, "status_diff_base", lambda r=None: None)
    failed, decl = _run(root)
    assert failed == [] and decl is not None and decl.kind == "skipped"


def test_fixture_module_is_never_scanned(tmp_path: Path) -> None:
    fixture = "tests/fixtures/decision_entry.py"
    root = _repo(tmp_path, {fixture: REAL + ONE}, {fixture: REAL + TWO})
    failed, decl = _run(root)
    assert failed == [] and decl is not None and decl.count == 0


def test_header_only_pattern_is_not_counted() -> None:
    header = 'import re\nT = ""\nre.findall(r"^## Decision (\\d+):", T)\nT.find("\\n## Decision ", 3)\n'
    assert ad_hoc_sites(REAL + header)[0] == []
    titled = (
        'import re\nT = ""\nre.search(r"## Decision (?P<n>\\d+): Some Title", T)\nre.search(r"## Decision 5 (Decided)", T)\n'
    )
    assert len(ad_hoc_sites(REAL + titled)[0]) == 2


def test_non_python_test_data_is_ignored(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"tests/data.json": "{}", "tests/notes.txt": "a"}, {"tests/data.json": "[", "tests/notes.txt": "b"})
    failed, decl = _run(root)
    assert failed == [] and decl is not None and decl.count == 0


def test_manifest_runs_on_any_test_change() -> None:
    entry = registry._ALL_ENTRIES["validate_decision_reads_through_fixture"]
    assert entry.pre is True and entry.full_segment == "full_after_lint"
    for path in ("tests/test_x.py", "tests/a/test_x.py"):
        assert any(registry._pre_glob_match(path, g) for g in entry.pre_globs), path
