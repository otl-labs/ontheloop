"""Hermetic: lexical term derivation, stoplist, AST symbols without import, sanitising, the 40-term cap."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.recs_related import terms as tm

pytestmark = pytest.mark.unit


def _by_kind(items: list[tm.Term]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for item in items:
        out.setdefault(item.kind, []).append(item.text)
    return out


class TestTerms:
    def test_python_file_yields_stem_module_basename_path_and_symbols(self, tmp_path: Path):
        (tmp_path / "scripts" / "preflight").mkdir(parents=True)
        (tmp_path / "scripts/preflight/ci_rca_signals.py").write_text(
            "def read_signals():\n    pass\n\nclass SignalRow:\n    pass\n\n"
            "async def fetch_all():\n    pass\n\ndef go():\n    pass\n",
            encoding="utf-8",
        )
        got = _by_kind(tm.derive_terms("scripts/preflight/ci_rca_signals.py", tmp_path))
        assert got[tm.KIND_STEM] == ["ci_rca_signals"]
        assert got[tm.KIND_MODULE] == ["scripts.preflight.ci_rca_signals"]
        assert got[tm.KIND_BASENAME] == ["ci_rca_signals.py"]
        assert got[tm.KIND_PATH] == ["scripts/preflight/ci_rca_signals.py"]
        assert got[tm.KIND_SYMBOL] == ["read_signals", "signalrow", "fetch_all"]  # lower-cased; 'go' is under 5 chars

    def test_non_python_file_has_no_module_or_symbols(self, tmp_path: Path):
        got = _by_kind(tm.derive_terms("docs/contracts/read-engine.yaml", tmp_path))
        assert got == {
            tm.KIND_STEM: ["read-engine"],
            tm.KIND_BASENAME: ["read-engine.yaml"],
            tm.KIND_PATH: ["docs/contracts/read-engine.yaml"],
        }

    @pytest.mark.parametrize("name", ["__init__", "conftest", "manifest", "skill", "handler", "claude", "readme", "README"])
    def test_generic_stems_are_skipped_but_the_path_is_kept(self, tmp_path: Path, name: str):
        got = _by_kind(tm.derive_terms(f"pkg/{name}.md", tmp_path))
        assert tm.KIND_STEM not in got and tm.KIND_BASENAME not in got
        assert got[tm.KIND_PATH] == [f"pkg/{name}.md".lower()]

    def test_package_init_is_named_by_its_package_module(self, tmp_path: Path):
        got = _by_kind(tm.derive_terms("scripts/recs_related/__init__.py", tmp_path))
        assert got[tm.KIND_MODULE] == ["scripts.recs_related"]
        assert tm.KIND_STEM not in got

    def test_tiny_stem_is_skipped(self, tmp_path: Path):
        assert tm.KIND_STEM not in _by_kind(tm.derive_terms("docs/ab.md", tmp_path))

    def test_symbols_are_read_by_ast_and_the_file_is_never_imported_or_run(self, tmp_path: Path):
        marker = tmp_path / "ran.txt"
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts/boom.py").write_text(
            f"import not_a_real_module_zzz\nopen({str(marker)!r}, 'w').write('ran')\nraise SystemExit(3)\n"
            "def surviving_function():\n    pass\n",
            encoding="utf-8",
        )
        got = _by_kind(tm.derive_terms("scripts/boom.py", tmp_path))
        assert got[tm.KIND_SYMBOL] == ["surviving_function"]
        assert not marker.exists()

    def test_only_top_level_symbols_and_at_most_40(self, tmp_path: Path):
        (tmp_path / "scripts").mkdir()
        body = "class Holder:\n    def method_inside(self):\n        pass\n\n" + "".join(
            f"def func_{i:03d}():\n    pass\n\n" for i in range(60)
        )
        (tmp_path / "scripts/many.py").write_text(body, encoding="utf-8")
        symbols = _by_kind(tm.derive_terms("scripts/many.py", tmp_path))[tm.KIND_SYMBOL]
        assert len(symbols) == tm.MAX_SYMBOLS and "method_inside" not in symbols and symbols[0] == "holder"

    @pytest.mark.parametrize("content", ["def broken(:\n", None])
    def test_unparseable_or_missing_python_yields_no_symbols(self, tmp_path: Path, content: str | None):
        (tmp_path / "scripts").mkdir()
        if content is not None:
            (tmp_path / "scripts/bad.py").write_text(content, encoding="utf-8")
        assert tm.KIND_SYMBOL not in _by_kind(tm.derive_terms("scripts/bad.py", tmp_path))

    def test_null_bytes_in_source_yield_no_symbols(self, tmp_path: Path):
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts/nul.py").write_bytes(b"def named_function():\n    pass\n\x00")
        assert tm.KIND_SYMBOL not in _by_kind(tm.derive_terms("scripts/nul.py", tmp_path))

    def test_a_derived_term_with_a_disallowed_character_is_dropped_not_rewritten(self, tmp_path: Path):
        got = _by_kind(tm.derive_terms("docs/we ird,name|x.md", tmp_path))
        assert got == {}

    def test_terms_are_deduplicated_by_text_first_kind_wins(self, tmp_path: Path):
        got = tm.derive_terms("ci.py", tmp_path)
        texts = [t.text for t in got]
        assert len(texts) == len(set(texts))
        assert [t.kind for t in got if t.text == "ci.py"] == [tm.KIND_BASENAME]

    def test_sanitise_lowercases_and_strips_bind_hostile_characters(self):
        assert tm.sanitise("Foo,Bar|Baz qux!") == "foobarbazqux"
        assert tm.sanitise("a/b.c-d_e") == "a/b.c-d_e"

    def test_free_text_terms_are_sanitised_deduplicated_and_labelled(self):
        got = tm.free_text_terms("Retry  BACKOFF, retry x a|b !!")
        assert [(t.text, t.kind) for t in got] == [("retry", "user"), ("backoff", "user"), ("ab", "user")]

    def test_cap_keeps_the_heaviest_and_reports_truncation(self):
        weighted = {f"term{i:02d}": float(i) for i in range(50)}
        kept, truncated = tm.cap_terms(weighted)
        assert truncated is True and len(kept) == tm.MAX_TERMS
        assert min(kept.values()) == 10.0 and "term49" in kept and "term09" not in kept

    def test_cap_leaves_a_small_set_untouched(self):
        kept, truncated = tm.cap_terms({"b": 1.0, "a": 1.0})
        assert truncated is False and list(kept) == ["a", "b"]
