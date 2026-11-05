"""The plain-reading review page and `approved_words render` (rec-4769). Base texts come from a pinned fixture copy
of main, never the working tree."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.roadmap import approved_words as aw
from scripts.roadmap import review_page

FIXTURES = Path(__file__).parent / "fixtures" / "approved_text"
HISTORY = yaml.safe_load((FIXTURES / "sidecar-history.yaml").read_text(encoding="utf-8"))
BASE = yaml.safe_load((FIXTURES / "review_page_base.yaml").read_text(encoding="utf-8"))
OWN = aw.parse_sidecar(HISTORY["approved-text-operator-approval@211f307c"])
SLUG = "demo"


@pytest.fixture(scope="module")
def base_dir(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("main")
    for rel, excerpts in BASE["files"].items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(excerpts), encoding="utf-8")
    return root


def _sidecar(*blocks: dict[str, str]) -> aw.Sidecar:
    return aw.Sidecar(schema_version=1, slug=SLUG, blocks=[aw.Block(**b) for b in blocks])


def _page(sidecar: aw.Sidecar, base: Path, **over: str) -> str:
    approved = over.get("expected", aw.approved_digest(sidecar))
    return aw.render_page(
        sidecar,
        over.get("slug", SLUG),
        approved,
        over["finger"] if "finger" in over else review_page.fingerprint(approved),
        base,
    )


def _section(page: str, block_id: str) -> str:
    return page.split(f"## {block_id}\n", 1)[1].split("\n## ", 1)[0]


class TestForms:
    def test_a_replace_of_many_runs_renders_was_and_becomes(self, base_dir) -> None:
        text = _section(_page(OWN, base_dir), "git-ops-binding")
        assert "**Was**" in text and "**Becomes**" in text and "Provenance" in text

    def test_an_edit_of_few_runs_renders_in_place_with_marks(self, base_dir) -> None:
        text = _section(_page(OWN, base_dir), "planning-step-8b")
        assert "**Edited in place**" in text and "<del>" in text and "<ins>" in text

    def test_a_pure_insertion_renders_new_text_and_its_anchor(self, base_dir) -> None:
        text = _section(_page(OWN, base_dir), "decision-83-update")
        assert "**New text**" in text and "Follows: ...The ruleset configuration above is unchanged." in text
        assert "<del>" not in text

    def test_a_pure_deletion_renders_deleted(self, base_dir) -> None:
        block = {"id": "gone", "file": "docs/DECISIONS.md", "before": "alpha beta gamma", "after": "alpha gamma"}
        text = _section(_page(_sidecar(block), base_dir), "gone")
        assert "**Deleted**" in text and "<del>beta</del>" in text

    def test_placeholders_show_as_n_and_date(self, base_dir) -> None:
        text = _page(OWN, base_dir)
        assert "[N]" in text and "[date]" in text and "{{" not in text


class TestLocations:
    def test_each_block_shows_file_and_nearest_heading_or_key(self, base_dir) -> None:
        page = _page(OWN, base_dir)
        assert "`docs/DECISIONS.md`, \\#\\# Decision 245" in _section(page, "decision-new")
        assert "Decision 83: Branch Protection" in _section(page, "decision-83-update")
        assert "`docs/contracts/git-ops.yaml`, contract:" in _section(page, "git-ops-version")
        assert "amendment\\_log:" in _section(page, "git-ops-log")
        assert "approved\\_text\\_binding" in _section(page, "git-ops-binding")
        assert "Plan Lint and Wording Check" in _section(page, "planning-step-8b")

    def test_a_block_whose_before_is_absent_renders_from_the_place_of_its_after(self, base_dir) -> None:
        block = {
            "id": "moved",
            "file": "docs/DECISIONS.md",
            "before": "words that main no longer carries",
            "after": "The ruleset configuration above is unchanged.",
        }
        assert "Decision 83: Branch Protection" in _section(_page(_sidecar(block), base_dir), "moved")

    def test_a_block_found_nowhere_says_place_not_found_at_main(self, base_dir) -> None:
        block = {"id": "lost", "file": "docs/DECISIONS.md", "before": "nowhere to be found", "after": "still nowhere"}
        assert "place not found at main" in _section(_page(_sidecar(block), base_dir), "lost")

    def test_a_missing_file_says_place_not_found_at_main(self, base_dir) -> None:
        block = {"id": "nofile", "file": "docs/absent.md", "before": "x", "after": "y"}
        assert "place not found at main" in _section(_page(_sidecar(block), base_dir), "nofile")

    def test_an_after_carrying_a_placeholder_matches_mains_filled_text(self, base_dir) -> None:
        block = {
            "id": "filled",
            "file": "docs/contracts/git-ops.yaml",
            "before": "no match here",
            "after": "contract_version: {{N}}",
        }
        assert "contract:" in _section(_page(_sidecar(block), base_dir), "filled")


class TestEscaping:
    def test_markdown_and_html_in_wording_cannot_restyle_the_page(self, base_dir) -> None:
        block = {"id": "inj", "file": "docs/DECISIONS.md", "before": "a", "after": "a **bold** <del>x</del> [l](u) # h"}
        text = _section(_page(_sidecar(block), base_dir), "inj")
        assert "\\*\\*bold\\*\\*" in text and "&lt;del" in text and "\\[l\\]" in text and "\\# h" in text
        assert "<del>x</del>" not in text


class TestFingerprint:
    def test_four_words_stable_and_following_the_digest(self) -> None:
        one, two = review_page.fingerprint("00" * 32), review_page.fingerprint("01" + "00" * 31)
        assert len(one.split("-")) == 4 and one == review_page.fingerprint("00" * 32) and one != two

    def test_the_word_list_has_256_distinct_words(self) -> None:
        assert len(review_page._FINGERPRINT_WORDS) == len(set(review_page._FINGERPRINT_WORDS)) == 256

    def test_the_page_header_shows_the_fingerprint_and_both_digests(self, base_dir) -> None:
        page = _page(OWN, base_dir)
        assert review_page.fingerprint(aw.approved_digest(OWN)) in page and aw.approved_digest(OWN) in page
        assert aw.digest(OWN) in page


class TestRefusals:
    def _block(self, **over: str) -> aw.Sidecar:
        return _sidecar({"id": "b", "file": "docs/DECISIONS.md", "before": "a", "after": "a b", **over})

    def test_a_bad_slug_is_refused(self, base_dir) -> None:
        with pytest.raises(aw.RenderRefused, match="slug"):
            _page(self._block(), base_dir, slug="Bad Slug")

    def test_a_digest_that_is_not_64_hex_is_refused(self, base_dir) -> None:
        with pytest.raises(aw.RenderRefused, match="64"):
            _page(self._block(), base_dir, expected="abc", finger="x")

    def test_a_digest_mismatch_is_refused(self, base_dir) -> None:
        with pytest.raises(aw.RenderRefused, match="differs"):
            _page(self._block(), base_dir, expected="0" * 64)

    def test_a_fingerprint_mismatch_is_refused(self, base_dir) -> None:
        with pytest.raises(aw.RenderRefused, match="fingerprint"):
            _page(self._block(), base_dir, finger="acorn-acorn-acorn-acorn")

    @pytest.mark.parametrize(
        "rel", ["/etc/passwd", "../x.md", "docs/../../x.md", ".git/config", "docs/.git/hooks/x", "a\\b.md"]
    )
    def test_a_block_file_outside_the_checkout_is_refused(self, base_dir, rel) -> None:
        with pytest.raises(aw.RenderRefused, match="block file"):
            _page(self._block(file=rel), base_dir)

    @pytest.mark.parametrize("bad", ["\x07", "‮", "​", "﻿", "\x1b"], ids=["bell", "bidi", "zero-width", "bom", "escape"])
    def test_a_control_bidi_or_zero_width_code_point_is_refused(self, base_dir, bad) -> None:
        with pytest.raises(aw.RenderRefused, match="code point"):
            _page(self._block(after=f"a{bad}b"), base_dir)

    def test_newline_and_tab_are_allowed(self, base_dir) -> None:
        assert "b" in _page(self._block(after="a\n\tb"), base_dir)

    def test_a_page_over_one_mebibyte_is_refused(self, base_dir) -> None:
        with pytest.raises(aw.RenderRefused, match="1 MiB"):
            _page(self._block(after="a " + "word " * 300000), base_dir)

    def test_the_cli_refuses_with_a_non_zero_exit_and_prints_the_page_otherwise(self, base_dir, tmp_path, capsys) -> None:
        side = tmp_path / "s.yaml"
        side.write_text(HISTORY["approved-text-operator-approval@211f307c"], encoding="utf-8")
        approved = aw.approved_digest(OWN)
        argv = ["render", "--sidecar-file", str(side), "--slug", SLUG, "--base-file-dir", str(base_dir)]
        assert aw.main([*argv, "--expect-approved", approved, "--fingerprint", review_page.fingerprint(approved)]) == 0
        assert "# Approve wording: demo" in capsys.readouterr().out
        assert aw.main([*argv, "--expect-approved", "0" * 64, "--fingerprint", review_page.fingerprint("0" * 64)]) == 2
        captured = capsys.readouterr()
        assert captured.out == "" and "REFUSED" in captured.err
