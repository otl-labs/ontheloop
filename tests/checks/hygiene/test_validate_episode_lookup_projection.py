"""Mirror test for scripts/checks/hygiene/validate_episode_lookup_projection.py (rec-3291 /
rec-3563 class guard). One case per input class: fires on the pre-migration defect shape; passes
on the migrated tree; does NOT fire on scripts/preflight/recs_cache.py's two-data-path shape;
does NOT raise on a sentinel VERB_FIELDS entry; does NOT fire on
scripts/ci/reconcile_target.py's callable indirection; records skipped(reason) for a verb absent
from VERB_FIELDS; declares on every exit path.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from scripts.checks import _common, registry
from scripts.checks.hygiene.validate_episode_lookup_projection import (
    _iter_source_files,
    _literal_str,
    _named_verb_call,
    _scan_comprehension,
    _scan_for_loop,
    _ScanResult,
    scan_paths,
    validate_episode_lookup_projection,
)


def _write(tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


class TestScanPathsDefectShape:
    def test_fires_on_the_pre_migration_defect_shape(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "defect.py",
            "def find_open_convergence_stale_rec(reader):\n"
            "    open_recs = reader.named('open_recs')\n"
            "    for rec in open_recs:\n"
            "        if rec.get('source') == 'x' and rec.get('status') == 'open':\n"
            "            return rec\n"
            "    return None\n",
        )
        result = scan_paths([path], tmp_path)
        assert len(result.violations) == 2
        assert any("'source'" in v for v in result.violations)
        assert any("'status'" in v for v in result.violations)
        assert result.examined == 2

    def test_fires_on_the_or_fallback_idiom(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "defect_or.py",
            "def find_rec(reader):\n"
            "    open_recs = reader.named('open_recs') or []\n"
            "    for rec in open_recs:\n"
            "        if rec.get('source') == 'x':\n"
            "            return rec\n"
            "    return None\n",
        )
        result = scan_paths([path], tmp_path)
        assert len(result.violations) == 1

    def test_fires_in_a_list_comprehension(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "defect_comp.py",
            "def matched(reader):\n"
            "    open_recs = reader.named('open_recs')\n"
            "    return [r for r in open_recs if r.get('source') == 'x']\n",
        )
        result = scan_paths([path], tmp_path)
        assert len(result.violations) == 1


class TestScanPathsMigratedShapePasses(object):
    def test_filtering_only_within_projection_does_not_fire(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "migrated.py",
            "def tally(reader):\n"
            "    open_recs = reader.named('open_recs')\n"
            "    return [{'id': r.get('id'), 'title': r.get('title')} for r in open_recs]\n",
        )
        result = scan_paths([path], tmp_path)
        assert result.violations == []
        assert result.examined == 2


class TestRecsCacheTwoPathShapeNotFlagged:
    """scripts/preflight/recs_cache.py: the named('open_recs') result is passed onward
    unfiltered, and the status=='open' filters live in a DIFFERENT function filtering a `rows`
    parameter -- two data paths that share a module, not one flagged co-occurrence."""

    def test_real_recs_cache_file_is_not_flagged(self) -> None:
        root = _common.ROOT
        path = root / "scripts" / "preflight" / "recs_cache.py"
        assert path.is_file()
        result = scan_paths([path], root)
        assert result.violations == []


class TestSentinelVerbNeverRaisesOrFlags:
    def test_sentinel_verb_filtered_on_an_arbitrary_key_does_not_raise_or_flag(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "sentinel.py",
            "def find_by_id(reader, rec_id):\n"
            "    rows = reader.named('rec_by_id', id=rec_id)\n"
            "    for row in rows:\n"
            "        if row.get('status') == 'open' and row.get('anything_at_all') == 'x':\n"
            "            return row\n"
            "    return None\n",
        )
        result = scan_paths([path], tmp_path)  # must not raise TypeError
        assert result.violations == []
        assert result.examined == 2


class TestReconcileTargetIndirectionNotFlagged:
    """scripts/ci/reconcile_target.py: the named('rec_by_id') call lives inside a DIFFERENT
    function (a nested closure) from the one that filters the result on 'status' -- the
    per-function scope boundary never connects them."""

    def test_real_reconcile_target_file_is_not_flagged(self) -> None:
        root = _common.ROOT
        path = root / "scripts" / "ci" / "reconcile_target.py"
        assert path.is_file()
        result = scan_paths([path], root)
        assert result.violations == []


class TestUnresolvedVerbRecordsSkipped:
    def test_absent_verb_is_recorded_as_unresolved_never_flagged(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "unresolved.py",
            "def check(reader):\n"
            "    rows = reader.named('budget_breach_recent')\n"
            "    for r in rows:\n"
            "        if r.get('anything') == 'x':\n"
            "            return r\n"
            "    return None\n",
        )
        result = scan_paths([path], tmp_path)
        assert result.violations == []
        assert result.examined == 0
        assert result.unresolved_verbs == {"budget_breach_recent"}

    def test_fixture_file_carrying_an_unregistered_verb_is_unresolved(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "unregistered.py",
            "def check(reader):\n"
            "    rows = reader.named('not_a_registered_verb')\n"
            "    for r in rows:\n"
            "        if r.get('anything') == 'x':\n"
            "            return r\n"
            "    return None\n",
        )
        result = scan_paths([path], tmp_path)
        assert result.violations == []
        assert "not_a_registered_verb" in result.unresolved_verbs


class TestRegisteredCheckDeclaresOnEveryExitPath:
    def test_violation_path_declares_examined_and_appends_failed(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(_common, "ROOT", tmp_path)
        (tmp_path / "scripts").mkdir()
        _write(
            tmp_path / "scripts",
            "defect.py",
            "def find_rec(reader):\n"
            "    open_recs = reader.named('open_recs')\n"
            "    for rec in open_recs:\n"
            "        if rec.get('source') == 'x':\n"
            "            return rec\n"
            "    return None\n",
        )
        failed: list[str] = []
        with registry.outcome_scope("validate_episode_lookup_projection"):
            validate_episode_lookup_projection(failed)
            declaration = registry.pop_declaration()
        assert failed
        assert declaration is not None
        assert declaration.kind == "examined"
        assert declaration.count == 1

    def test_vacuous_path_declares_examined_zero(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(_common, "ROOT", tmp_path)
        (tmp_path / "scripts").mkdir()
        _write(tmp_path / "scripts", "empty.py", "def noop():\n    return None\n")
        failed: list[str] = []
        with registry.outcome_scope("validate_episode_lookup_projection"):
            validate_episode_lookup_projection(failed)
            declaration = registry.pop_declaration()
        assert failed == []
        assert declaration is not None
        assert declaration.kind == "examined"
        assert declaration.count == 0

    def test_skipped_path_declares_skipped(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(_common, "ROOT", tmp_path)
        (tmp_path / "scripts").mkdir()
        _write(
            tmp_path / "scripts",
            "unresolved.py",
            "def check(reader):\n"
            "    rows = reader.named('budget_breach_recent')\n"
            "    for r in rows:\n"
            "        if r.get('anything') == 'x':\n"
            "            return r\n"
            "    return None\n",
        )
        failed: list[str] = []
        with registry.outcome_scope("validate_episode_lookup_projection"):
            validate_episode_lookup_projection(failed)
            declaration = registry.pop_declaration()
        assert failed == []
        assert declaration is not None
        assert declaration.kind == "skipped"

    def test_clean_pass_with_a_coexisting_unresolved_verb_surfaces_both(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch
    ) -> None:
        """A real scan can find BOTH a clean, within-projection named() access AND an unresolved
        verb elsewhere in the same tree. The unresolved verb must never be silently dropped just
        because something else in the scan passed -- it is printed, and the declaration still
        reflects the genuine `examined` work (never mislabeled `skipped` when real work happened)."""
        monkeypatch.setattr(_common, "ROOT", tmp_path)
        (tmp_path / "scripts").mkdir()
        _write(
            tmp_path / "scripts",
            "clean.py",
            "def tally(reader):\n    open_recs = reader.named('open_recs')\n    return [r.get('id') for r in open_recs]\n",
        )
        _write(
            tmp_path / "scripts",
            "unresolved.py",
            "def check(reader):\n"
            "    rows = reader.named('budget_breach_recent')\n"
            "    for r in rows:\n"
            "        if r.get('anything') == 'x':\n"
            "            return r\n"
            "    return None\n",
        )
        failed: list[str] = []
        with registry.outcome_scope("validate_episode_lookup_projection"):
            validate_episode_lookup_projection(failed)
            declaration = registry.pop_declaration()
        assert failed == []
        assert declaration is not None
        assert declaration.kind == "examined"
        assert declaration.count == 1
        out = capsys.readouterr().out
        assert "budget_breach_recent" in out

    def test_clean_pass_path_declares_examined_enforced(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(_common, "ROOT", tmp_path)
        (tmp_path / "scripts").mkdir()
        _write(
            tmp_path / "scripts",
            "clean.py",
            "def tally(reader):\n    open_recs = reader.named('open_recs')\n    return [r.get('id') for r in open_recs]\n",
        )
        failed: list[str] = []
        with registry.outcome_scope("validate_episode_lookup_projection"):
            validate_episode_lookup_projection(failed)
            declaration = registry.pop_declaration()
        assert failed == []
        assert declaration is not None
        assert declaration.kind == "examined"
        assert declaration.count == 1


class TestRegisteredCheckResolvesAndPassesAgainstMigratedTree:
    def test_resolves_through_the_registry_and_passes(self) -> None:
        failed: list[str] = []
        registry.resolve("validate_episode_lookup_projection")(failed)
        assert failed == []


class TestIterSourceFilesEdgeCases:
    def test_absent_scan_root_yields_nothing(self, tmp_path: Path) -> None:
        assert list(_iter_source_files(tmp_path)) == []

    def test_excluded_dir_part_is_skipped(self, tmp_path: Path) -> None:
        scripts_dir = tmp_path / "scripts"
        (scripts_dir / "tests").mkdir(parents=True)
        (scripts_dir / "tests" / "helper.py").write_text("x = 1\n", encoding="utf-8")
        (scripts_dir / "real.py").write_text("x = 1\n", encoding="utf-8")
        found = list(_iter_source_files(tmp_path))
        assert [p.name for p in found] == ["real.py"]


class TestLiteralStrEdgeCases:
    def test_non_constant_node_returns_none(self) -> None:
        node = ast.parse("x", mode="eval").body
        assert _literal_str(node) is None

    def test_none_node_returns_none(self) -> None:
        assert _literal_str(None) is None


class TestNamedVerbCallEdgeCases:
    def test_named_call_with_no_args_returns_none(self) -> None:
        call = ast.parse("reader.named()", mode="eval").body
        assert _named_verb_call(call) is None


class TestScanForLoopEdgeCases:
    def test_tuple_target_is_ignored(self) -> None:
        tree = ast.parse("for a, b in open_recs:\n    pass\n")
        for_node = tree.body[0]
        out = _ScanResult()
        _scan_for_loop(for_node, {"open_recs": "open_recs"}, "f.py", out)
        assert out.examined == 0
        assert out.violations == []

    def test_non_name_iter_is_ignored(self) -> None:
        tree = ast.parse("for rec in reader.get_rows():\n    pass\n")
        for_node = tree.body[0]
        out = _ScanResult()
        _scan_for_loop(for_node, {}, "f.py", out)
        assert out.examined == 0


class TestScanComprehensionEdgeCases:
    def test_tuple_generator_target_is_skipped(self) -> None:
        tree = ast.parse("[a for a, b in open_recs]", mode="eval")
        out = _ScanResult()
        _scan_comprehension(tree.body, {"open_recs": "open_recs"}, "f.py", out)
        assert out.examined == 0

    def test_untracked_iter_variable_is_skipped(self) -> None:
        tree = ast.parse("[r for r in something_else]", mode="eval")
        out = _ScanResult()
        _scan_comprehension(tree.body, {"open_recs": "open_recs"}, "f.py", out)
        assert out.examined == 0


class TestScanPathsSyntaxErrorSkipped:
    def test_a_file_with_invalid_syntax_is_skipped_not_raised(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.py"
        path.write_text("def f(:\n", encoding="utf-8")
        result = scan_paths([path], tmp_path)
        assert result.violations == []
        assert result.examined == 0
