"""scripts.session.decision_renumber against real temporary git repos."""

from __future__ import annotations

import ast
from pathlib import Path

from scripts.session import decision_renumber as dr
from tests.fixtures.merge_step_repos import commit_file, git, make_repo, push_main, write

SEED = "# Decisions\n\n## Decision 2: Two (Decided)\n\nbody two\n\n## Decision 1: One (Decided)\n\nbody one\n"


def _block(number: int, title: str, extra: str = "") -> str:
    return f"## Decision {number}: {title} (Decided)\n\n**Warehouse ID:** dec-{number:03d}\n{extra}\n"


def _seed(tmp_path: Path) -> tuple[Path, str]:
    repo = make_repo(tmp_path)
    commit_file(repo, "docs/DECISIONS.md", SEED)
    commit_file(repo, "docs/DECISIONS_ARCHIVE.md", "# Archive\n")
    commit_file(repo, "docs/old.md", "Decision 3 is main's.\n")
    push_main(repo)
    return repo, git(repo, "rev-parse", "HEAD")


def _main_adds(repo: Path, base: str, number: int) -> None:
    """Advance origin/main past `base` with a Decision `number`, then return to a branch off `base`."""
    git(repo, "checkout", "-q", "-b", "mainline", base)
    text = (repo / "docs/DECISIONS.md").read_text(encoding="utf-8")
    commit_file(repo, "docs/DECISIONS.md", text.replace("## Decision 2:", _block(number, "Main") + "\n## Decision 2:", 1))
    push_main(repo)
    git(repo, "checkout", "-q", "-b", "feature", base)


def _post_rebase(repo: Path, base: str, branch_numbers: list[int], extra_files: dict[str, str] | None = None) -> str:
    """The rebased state: main's block present, the branch's blocks above it, one branch commit."""
    git(repo, "checkout", "-q", "-B", "feature", "origin/main")
    text = (repo / "docs/DECISIONS.md").read_text(encoding="utf-8")
    blocks = "\n".join(_block(n, "Branch") for n in sorted(branch_numbers, reverse=True))
    write(repo, "docs/DECISIONS.md", text.replace("## Decision ", blocks + "\n## Decision ", 1))
    for rel, body in (extra_files or {}).items():
        write(repo, rel, body)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "branch")
    return base


class TestCollisions:
    def test_reports_number_main_took(self, tmp_path: Path) -> None:
        repo, base = _seed(tmp_path)
        _main_adds(repo, base, 3)
        text = (repo / "docs/DECISIONS.md").read_text(encoding="utf-8")
        commit_file(repo, "docs/DECISIONS.md", text.replace("## Decision 2:", _block(3, "Branch") + "\n## Decision 2:", 1))
        assert dr.collisions(repo, "origin/main") == {3: 4}

    def test_ascending_mapping_for_two_numbers(self, tmp_path: Path) -> None:
        repo, base = _seed(tmp_path)
        _main_adds(repo, base, 3)
        text = (repo / "docs/DECISIONS.md").read_text(encoding="utf-8")
        both = _block(4, "Branch B") + "\n" + _block(3, "Branch A") + "\n"
        commit_file(repo, "docs/DECISIONS.md", text.replace("## Decision 2:", both + "## Decision 2:", 1))
        git(repo, "checkout", "-q", "mainline")
        main_text = (repo / "docs/DECISIONS.md").read_text(encoding="utf-8")
        commit_file(
            repo, "docs/DECISIONS.md", main_text.replace("## Decision 3:", _block(4, "Main 4") + "\n## Decision 3:", 1)
        )
        push_main(repo)
        git(repo, "checkout", "-q", "feature")
        assert dr.collisions(repo, "origin/main") == {3: 5, 4: 6}

    def test_empty_when_number_above_main(self, tmp_path: Path) -> None:
        repo, base = _seed(tmp_path)
        _main_adds(repo, base, 3)
        text = (repo / "docs/DECISIONS.md").read_text(encoding="utf-8")
        commit_file(repo, "docs/DECISIONS.md", text.replace("## Decision 2:", _block(4, "Branch") + "\n## Decision 2:", 1))
        assert dr.collisions(repo, "origin/main") == {}

    def test_empty_when_branch_adds_none(self, tmp_path: Path) -> None:
        repo, base = _seed(tmp_path)
        _main_adds(repo, base, 3)
        commit_file(repo, "other.txt", "x\n")
        assert dr.collisions(repo, "origin/main") == {}

    def test_retitled_existing_heading_is_not_added(self, tmp_path: Path) -> None:
        repo, base = _seed(tmp_path)
        _main_adds(repo, base, 3)
        text = (repo / "docs/DECISIONS.md").read_text(encoding="utf-8")
        commit_file(repo, "docs/DECISIONS.md", text.replace("Two (Decided)", "Two renamed (Superseded by Decision 9)"))
        assert dr.branch_added(repo, base) == []
        assert dr.collisions(repo, "origin/main") == {}

    def test_writes_nothing(self, tmp_path: Path) -> None:
        repo, base = _seed(tmp_path)
        _main_adds(repo, base, 3)
        text = (repo / "docs/DECISIONS.md").read_text(encoding="utf-8")
        commit_file(repo, "docs/DECISIONS.md", text.replace("## Decision 2:", _block(3, "Branch") + "\n## Decision 2:", 1))
        dr.collisions(repo, "origin/main")
        assert git(repo, "status", "--porcelain") == ""


class TestRenumber:
    def test_rewrites_each_recognised_form_on_added_lines(self, tmp_path: Path) -> None:
        repo, base = _seed(tmp_path)
        _main_adds(repo, base, 3)
        files = {
            "docs/notes.md": "See Decision 3 and Decisions 3 and 1, plus dec-003 and dec-3.\n",
            "config/sloc_budgets.yaml": "a.py: 10  # raise-approved: dec-3 reason\n",
        }
        _post_rebase(repo, base, [3], files)
        result = dr.renumber(repo, "origin/main", base)
        assert result.mapping == {3: 4}
        notes = (repo / "docs/notes.md").read_text(encoding="utf-8")
        assert notes == "See Decision 4 and Decisions 4 and 1, plus dec-004 and dec-4.\n"
        assert "dec-4 reason" in (repo / "config/sloc_budgets.yaml").read_text(encoding="utf-8")
        live = (repo / "docs/DECISIONS.md").read_text(encoding="utf-8")
        assert "## Decision 4: Branch (Decided)" in live
        assert "**Warehouse ID:** dec-004" in live
        assert "## Decision 3: Main (Decided)" in live and "**Warehouse ID:** dec-003" in live
        assert result.bare == []

    def test_pre_existing_line_citing_main_is_untouched(self, tmp_path: Path) -> None:
        repo, base = _seed(tmp_path)
        _main_adds(repo, base, 3)
        _post_rebase(repo, base, [3])
        dr.renumber(repo, "origin/main", base)
        assert (repo / "docs/old.md").read_text(encoding="utf-8") == "Decision 3 is main's.\n"

    def test_two_numbers_map_without_chaining(self, tmp_path: Path) -> None:
        repo, base = _seed(tmp_path)
        _main_adds(repo, base, 4)
        git(repo, "checkout", "-q", "mainline")
        text = (repo / "docs/DECISIONS.md").read_text(encoding="utf-8")
        commit_file(repo, "docs/DECISIONS.md", text.replace("## Decision 4:", _block(3, "Main 3") + "\n## Decision 4:", 1))
        push_main(repo)
        _post_rebase(repo, base, [3, 4], {"docs/notes.md": "Decisions 3, 4 and Decision 4 then Decision 3\n"})
        result = dr.renumber(repo, "origin/main", base)
        assert result.mapping == {3: 5, 4: 6}
        assert (repo / "docs/notes.md").read_text(encoding="utf-8") == "Decisions 5, 6 and Decision 6 then Decision 5\n"

    def test_blocks_are_newest_first(self, tmp_path: Path) -> None:
        repo, base = _seed(tmp_path)
        _main_adds(repo, base, 3)
        git(repo, "checkout", "-q", "-B", "feature", "origin/main")
        text = (repo / "docs/DECISIONS.md").read_text(encoding="utf-8")
        blocks = _block(3, "Branch A") + "\n" + _block(5, "Branch B") + "\n"
        write(repo, "docs/DECISIONS.md", text.replace("## Decision 3:", blocks + "## Decision 3:", 1))
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "branch")
        dr.renumber(repo, "origin/main", base)
        numbers = [
            int(m.group(1)) for m in dr.iter_decision_headings((repo / "docs/DECISIONS.md").read_text(encoding="utf-8"))
        ]
        assert numbers == [6, 5, 3, 2, 1]

    def test_bare_tokens_and_non_decision_forms_are_reported(self, tmp_path: Path) -> None:
        repo, base = _seed(tmp_path)
        _main_adds(repo, base, 3)
        files = {
            "docs/bare.md": "the 3 rules, rec-3, 2026-10-31, 33, 3+1 and 3/1 here\n",
            "data.json": '{"number": 3}\n',
        }
        _post_rebase(repo, base, [3], files)
        result = dr.renumber(repo, "origin/main", base)
        found = {(f, t) for f, _l, t in result.bare}
        assert ("docs/bare.md", "3") in found and ("data.json", "3") in found
        bare_md = [b for b in result.bare if b[0] == "docs/bare.md"]
        assert len(bare_md) == 3
        assert (repo / "data.json").read_text(encoding="utf-8") == '{"number": 3}\n'
        assert (repo / "docs/bare.md").read_text(encoding="utf-8").startswith("the 3 rules, rec-3, 2026-10-31, 33, 3+1")

    def test_number_key_in_decisions_file_is_rewritten(self, tmp_path: Path) -> None:
        repo, base = _seed(tmp_path)
        _main_adds(repo, base, 3)
        _post_rebase(repo, base, [3])
        live = repo / "docs/DECISIONS.md"
        text = live.read_text(encoding="utf-8").replace(
            "**Warehouse ID:** dec-003\n\n\n## Decision 3", "number: 3\n\n## Decision 3", 1
        )
        live.write_text(text, encoding="utf-8")
        git(repo, "commit", "-qam", "key")
        result = dr.renumber(repo, "origin/main", base)
        assert "number: 4" in live.read_text(encoding="utf-8")
        assert result.bare == []

    def test_no_collision_changes_nothing(self, tmp_path: Path) -> None:
        repo, base = _seed(tmp_path)
        _main_adds(repo, base, 3)
        _post_rebase(repo, base, [4])
        result = dr.renumber(repo, "origin/main", base)
        assert result.mapping == {} and result.changed == []

    def test_retitled_heading_gets_no_renumber(self, tmp_path: Path) -> None:
        repo, base = _seed(tmp_path)
        _main_adds(repo, base, 3)
        git(repo, "checkout", "-q", "-B", "feature", "origin/main")
        text = (repo / "docs/DECISIONS.md").read_text(encoding="utf-8")
        commit_file(repo, "docs/DECISIONS.md", text.replace("Two (Decided)", "Two (Superseded by Decision 9)"))
        assert dr.renumber(repo, "origin/main", base).mapping == {}


def test_module_has_no_heading_regex_and_imports_the_shared_grammar() -> None:
    source = Path(dr.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "scripts.decisions_md" for a in n.names
    }
    assert "iter_decision_headings" in imported
    assert "#{2,3}" not in source


def test_unknown_onto_ref_reports_nothing(tmp_path: Path) -> None:
    repo, _base = _seed(tmp_path)
    assert dr.collisions(repo, "origin/nope") == {}
    assert dr.renumber(repo, "origin/nope").mapping == {}
