"""approved_words: the sidecar models and matcher, the wording and approved-words digests, and the `render` CLI
(rec-4769). Base texts come from the pinned fixture copy of main, never the working tree."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.roadmap import approved_text, review_page
from scripts.roadmap import approved_words as aw

FIXTURES = Path(__file__).parent / "fixtures" / "approved_text"
HISTORY = yaml.safe_load((FIXTURES / "sidecar-history.yaml").read_text(encoding="utf-8"))
BASE = yaml.safe_load((FIXTURES / "review_page_base.yaml").read_text(encoding="utf-8"))
OWN_TEXT = HISTORY["approved-text-operator-approval@211f307c"]
OWN = aw.parse_sidecar(OWN_TEXT)
SLUG = "demo"


def _sidecar(*blocks: dict[str, str]) -> aw.Sidecar:
    return aw.Sidecar(schema_version=1, slug=SLUG, blocks=[aw.Block(**b) for b in blocks])


def _blk(before: str, after: str, block_id: str = "b", file: str = "docs/DECISIONS.md") -> dict[str, str]:
    return {"id": block_id, "file": file, "before": before, "after": after}


def _digest_of(*blocks: dict[str, str]) -> str:
    return aw.approved_digest(_sidecar(*blocks))


@pytest.fixture(scope="module")
def base_dir(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("main")
    for rel, excerpts in BASE["files"].items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(excerpts), encoding="utf-8")
    return root


def _page(sidecar: aw.Sidecar, base: Path, **over: str) -> str:
    approved = over.get("expected", aw.approved_digest(sidecar))
    finger = over["finger"] if "finger" in over else review_page.fingerprint(approved)
    return aw.render_page(sidecar, over.get("slug", SLUG), approved, finger, base)


def _section(page: str, block_id: str) -> str:
    return page.split(f"## {block_id}\n", 1)[1].split("\n## ", 1)[0]


class TestModels:
    def test_a_sidecar_needs_blocks(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            aw.Sidecar(schema_version=1, slug=SLUG, blocks=[])

    def test_block_ids_are_unique(self) -> None:
        with pytest.raises(ValueError, match="unique"):
            _sidecar(_blk("a", "b"), _blk("c", "d"))

    @pytest.mark.parametrize("text", ["schema_version: [1\n", "schema_version: 2\nslug: x\nblocks: []\n", "- not a mapping\n"])
    def test_parse_sidecar_refuses_bad_yaml_and_schema_misses(self, text) -> None:
        with pytest.raises(aw.ApprovedTextError, match="does not parse"):
            aw.parse_sidecar(text)

    def test_words_and_sidecar_rel(self) -> None:
        assert aw.words("a  b\n c\t") == ["a", "b", "c"]
        assert aw.sidecar_rel("x") == "docs/plans/approved/x.yaml"

    def test_token_matching_is_exact_unless_a_placeholder_is_filled(self) -> None:
        assert aw._token_matches("word", "word") and not aw._token_matches("word", "Word")
        assert aw._token_matches("v{{N}}", "v12") and not aw._token_matches("v{{N}}", "vx")
        assert aw._token_matches('"{{DATE}}"', '"2026-11-05"') and not aw._token_matches("{{DATE}}", "soon")

    def test_find_all_compares_plainly(self) -> None:
        assert aw.find_all(["a", "b", "a", "b"], ["a", "b"]) == [0, 2]
        assert aw.find_all(["{{N}}"], ["5"]) == [] and aw.find_all(["a"], ["a", "b"]) == []

    def test_the_wording_digest_is_stable_and_follows_the_words(self) -> None:
        one = aw.digest(_sidecar(_blk("a b", "a c")))
        assert one == aw.digest(_sidecar(_blk("a  b", "a\nc"))) != aw.digest(_sidecar(_blk("a b", "a d")))
        assert aw.digest(OWN).startswith("432f7f82")

    def test_approved_text_re_exports_the_same_objects(self) -> None:
        for name in (
            "Sidecar",
            "Block",
            "ApprovedTextError",
            "find_all",
            "digest",
            "parse_sidecar",
            "approved_digest",
            "words",
        ):
            assert getattr(approved_text, name) is getattr(aw, name)
        assert approved_text._PLACEHOLDERS is aw._PLACEHOLDERS and approved_text._token_matches is aw._token_matches


class TestApprovedDigest:
    def test_shared_prefix_and_suffix_words_drop_out(self) -> None:
        assert _digest_of(_blk("x a b y", "x a Z y")) == _digest_of(_blk("q a b r s", "q a Z r s"))

    def test_the_removed_and_the_added_words_both_count(self) -> None:
        base = _digest_of(_blk("x a y", "x b y"))
        assert base != _digest_of(_blk("x c y", "x b y")) and base != _digest_of(_blk("x a y", "x d y"))

    def test_a_pure_insertion_and_a_pure_deletion_keep_their_words(self) -> None:
        assert _digest_of(_blk("x y", "x NEW y")) != _digest_of(_blk("x y", "x OTHER y"))
        assert _digest_of(_blk("x OLD y", "x y")) != _digest_of(_blk("x OTHER y", "x y"))

    def test_a_number_or_date_a_placeholder_replaces_is_recorded_as_the_placeholder(self) -> None:
        assert _digest_of(_blk("v 12", "v {{N}}")) == _digest_of(_blk("v 13", "v {{N}}"))
        assert _digest_of(_blk("on 2026-11-03", "on {{DATE}}")) == _digest_of(_blk("on 2026-11-05", "on {{DATE}}"))
        assert _digest_of(_blk("v twelve", "v {{N}}")) != _digest_of(_blk("v 13", "v {{N}}"))

    def test_the_placeholder_rule_needs_an_equal_length_replace_run(self) -> None:
        assert _digest_of(_blk("v 12 extra", "v {{N}}")) != _digest_of(_blk("v 13", "v {{N}}"))

    def test_block_order_and_ids_do_not_count_but_pairing_does(self) -> None:
        one, two = _blk("a b", "a c", "one"), _blk("d e", "d f", "two", "docs/other.md")
        assert _digest_of(one, two) == _digest_of({**two, "id": "x"}, {**one, "id": "y"})
        swapped = _digest_of(_blk("a b", "a f", "one"), _blk("d e", "d c", "two", "docs/other.md"))
        assert swapped != _digest_of(one, two)

    def test_the_real_sidecar_digest_is_pinned(self) -> None:
        assert aw.approved_digest(OWN).startswith("ada607a0")


class TestRender:
    def test_a_small_sidecar_renders_a_page_for_its_digest(self, base_dir) -> None:
        sidecar = _sidecar(
            _blk("The ruleset configuration above is unchanged.", "The ruleset configuration above is unchanged. More.")
        )
        page = _page(sidecar, base_dir)
        assert "# Approve wording: demo" in page and "Decision 83: Branch Protection" in _section(page, "b")

    def test_the_real_sidecar_renders_every_block_with_its_place(self, base_dir) -> None:
        page = _page(OWN, base_dir)
        assert "\\#\\# Decision 245" in _section(page, "decision-new")
        assert "contract:" in _section(page, "git-ops-version")
        assert "amendment\\_log:" in _section(page, "git-ops-log")
        assert "Plan Lint and Wording Check" in _section(page, "planning-step-8b")

    def test_a_block_located_by_its_after_when_main_already_carries_it(self, base_dir) -> None:
        block = _blk("words that main no longer carries", "The ruleset configuration above is unchanged.")
        assert "Decision 83: Branch Protection" in _section(_page(_sidecar(block), base_dir), "b")

    def test_an_after_carrying_a_placeholder_matches_mains_filled_text(self, base_dir) -> None:
        block = _blk("no match here", "contract_version: {{N}}", file="docs/contracts/git-ops.yaml")
        assert "contract:" in _section(_page(_sidecar(block), base_dir), "b")

    @pytest.mark.parametrize("file", ["docs/DECISIONS.md", "docs/absent.md"])
    def test_a_block_found_nowhere_says_place_not_found_at_main(self, base_dir, file) -> None:
        text = _section(_page(_sidecar(_blk("nowhere at all", "still nowhere", file=file)), base_dir), "b")
        assert "place not found at main" in text

    def test_the_sha_comes_from_the_base_directory_head(self, tmp_path) -> None:
        for args in (
            ["init", "-q"],
            ["-c", "user.name=t", "-c", "user.email=t@e.x", "commit", "-q", "--allow-empty", "-m", "x"],
        ):
            subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
        head = subprocess.run(
            ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()
        page = _page(_sidecar(_blk("nowhere", "none")), tmp_path)
        assert f"place not found at main {head[:12]}" in page

    def test_without_a_git_repository_the_sha_reads_unknown(self, tmp_path) -> None:
        assert "place not found at main unknown" in _page(_sidecar(_blk("nowhere", "none")), tmp_path)

    def test_each_location_kind(self, tmp_path) -> None:
        (tmp_path / "a.md").write_text("intro words\n\n## Heading\ntarget here\n", encoding="utf-8")
        (tmp_path / "b.yaml").write_text("top:\n  nested: value\nother: 1\n", encoding="utf-8")
        (tmp_path / "c.md").write_text("target before any heading\n", encoding="utf-8")
        (tmp_path / "d.txt").write_text("plain target\n", encoding="utf-8")
        blocks = [
            _blk("target here", "target here x", "md", "a.md"),
            _blk("nested: value", "nested: value x", "yaml", "b.yaml"),
            _blk("target before", "target before x", "bare", "c.md"),
            _blk("plain target", "plain target x", "txt", "d.txt"),
        ]
        page = _page(_sidecar(*blocks), tmp_path)
        assert "\\#\\# Heading" in _section(page, "md") and "top:" in _section(page, "yaml")
        assert "before the first heading" in _section(page, "bare") and "before the first heading" in _section(page, "txt")

    def test_the_line_of_a_word_past_the_end_is_the_last_line(self) -> None:
        assert aw._line_of_word("a b\nc\n", 99) == 1 and aw._line_of_word("", 0) == 0


class TestRefusals:
    def _one(self, **over: str) -> aw.Sidecar:
        return _sidecar(_blk("a", "a b", **over))

    def test_a_bad_slug_is_refused(self, base_dir) -> None:
        with pytest.raises(aw.RenderRefused, match="slug"):
            _page(self._one(), base_dir, slug="Bad Slug")

    def test_a_digest_that_is_not_64_hex_is_refused(self, base_dir) -> None:
        with pytest.raises(aw.RenderRefused, match="64"):
            _page(self._one(), base_dir, expected="abc", finger="x")

    def test_a_digest_mismatch_is_refused(self, base_dir) -> None:
        with pytest.raises(aw.RenderRefused, match="differs"):
            _page(self._one(), base_dir, expected="0" * 64)

    def test_a_fingerprint_mismatch_is_refused(self, base_dir) -> None:
        with pytest.raises(aw.RenderRefused, match="fingerprint"):
            _page(self._one(), base_dir, finger="acorn-acorn-acorn-acorn")

    @pytest.mark.parametrize(
        "rel", ["/etc/passwd", "../x.md", "docs/../../x.md", ".git/config", "docs/.git/hooks/x", "a\\b.md"]
    )
    def test_a_block_file_outside_the_checkout_is_refused(self, base_dir, rel) -> None:
        with pytest.raises(aw.RenderRefused, match="block file"):
            _page(self._one(file=rel), base_dir)

    @pytest.mark.parametrize("bad", ["\x07", "‮", "​", "﻿", "\x1b"], ids=["bell", "bidi", "zero-width", "bom", "escape"])
    def test_a_control_bidi_or_zero_width_code_point_is_refused(self, base_dir, bad) -> None:
        with pytest.raises(aw.RenderRefused, match="code point"):
            _page(_sidecar(_blk("a", f"a{bad}b")), base_dir)

    def test_newline_and_tab_are_allowed(self, base_dir) -> None:
        assert "b" in _page(_sidecar(_blk("a", "a\n\tb")), base_dir)

    def test_a_page_over_one_mebibyte_is_refused(self, base_dir) -> None:
        with pytest.raises(aw.RenderRefused, match="1 MiB"):
            _page(_sidecar(_blk("a", "a " + "word " * 300000)), base_dir)


class TestCli:
    def _argv(self, side: Path, base: Path, approved: str, finger: str) -> list[str]:
        return [
            "render", "--sidecar-file", str(side), "--slug", SLUG, "--base-file-dir", str(base),
            "--expect-approved", approved, "--fingerprint", finger,
        ]  # fmt: skip

    def test_a_good_render_prints_the_page(self, base_dir, tmp_path, capsys) -> None:
        side = tmp_path / "s.yaml"
        side.write_text(OWN_TEXT, encoding="utf-8")
        approved = aw.approved_digest(OWN)
        assert aw.main(self._argv(side, base_dir, approved, review_page.fingerprint(approved))) == 0
        assert "# Approve wording: demo" in capsys.readouterr().out

    def test_a_refusal_exits_non_zero_with_nothing_on_stdout(self, base_dir, tmp_path, capsys) -> None:
        side = tmp_path / "s.yaml"
        side.write_text(OWN_TEXT, encoding="utf-8")
        assert aw.main(self._argv(side, base_dir, "0" * 64, review_page.fingerprint("0" * 64))) == 2
        captured = capsys.readouterr()
        assert captured.out == "" and "REFUSED" in captured.err

    @pytest.mark.parametrize("content", ["schema_version: [1\n", None], ids=["unparsable", "missing"])
    def test_a_sidecar_file_that_does_not_parse_or_read_is_refused(self, base_dir, tmp_path, capsys, content) -> None:
        side = tmp_path / "s.yaml"
        if content is not None:
            side.write_text(content, encoding="utf-8")
        assert aw.main(self._argv(side, base_dir, "0" * 64, "x")) == 2
        assert "REFUSED" in capsys.readouterr().err

    def test_main_reads_sys_argv_when_given_no_arguments(self, base_dir, tmp_path, monkeypatch, capsys) -> None:
        side = tmp_path / "s.yaml"
        side.write_text(OWN_TEXT, encoding="utf-8")
        approved = aw.approved_digest(OWN)
        monkeypatch.setattr(
            sys, "argv", ["approved_words", *self._argv(side, base_dir, approved, review_page.fingerprint(approved))]
        )
        assert aw.main() == 0
        assert "Approve wording" in capsys.readouterr().out
