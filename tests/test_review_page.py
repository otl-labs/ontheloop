"""The plain-reading review page (rec-4769). Imports only review_page; render's locating and refusals are
approved_words code, tested in tests/test_approved_words.py."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from scripts.roadmap import review_page

HISTORY = yaml.safe_load(
    (Path(__file__).parent / "fixtures" / "approved_text" / "sidecar-history.yaml").read_text(encoding="utf-8")
)
OWN = {b["id"]: SimpleNamespace(**b) for b in yaml.safe_load(HISTORY["approved-text-operator-approval@211f307c"])["blocks"]}
APPROVED = "ab" * 32
WORDING = "cd" * 32


def _page(*blocks: SimpleNamespace, places: dict[str, str] | None = None) -> str:
    return review_page.render(list(blocks), places or {}, slug="demo", approved=APPROVED, wording=WORDING)


def _block(before: str, after: str, block_id: str = "b", file: str = "doc.md") -> SimpleNamespace:
    return SimpleNamespace(id=block_id, file=file, before=before, after=after)


class TestForms:
    def test_a_replace_of_many_runs_renders_was_and_becomes(self) -> None:
        text = _page(OWN["git-ops-binding"])
        assert "**Was**" in text and "**Becomes**" in text and "Provenance" in text

    def test_an_edit_of_few_runs_renders_in_place_with_marks(self) -> None:
        text = _page(OWN["planning-step-8b"])
        assert "**Edited in place**" in text and "<del>" in text and "<ins>" in text

    def test_a_pure_insertion_after_its_anchor_renders_new_text(self) -> None:
        text = _page(OWN["decision-83-update"])
        assert "**New text**" in text and "Follows: ...The ruleset configuration above is unchanged." in text
        assert "<del>" not in text

    def test_a_pure_insertion_before_its_anchor_renders_new_text_with_what_it_precedes(self) -> None:
        text = _page(OWN["decision-new"])
        assert "**New text**" in text and "Precedes: " in text and "Follows:" not in text

    def test_an_insertion_into_an_empty_before_has_no_anchor_to_name(self) -> None:
        text = _page(_block("", "brand new words"))
        assert "**New text**" in text and "brand new words" in text

    def test_a_pure_deletion_renders_deleted(self) -> None:
        text = _page(_block("alpha beta gamma", "alpha gamma"))
        assert "**Deleted**" in text and "<del>beta</del>" in text

    def test_equal_words_render_no_change(self) -> None:
        assert "**No change**" in _page(_block("same words", "same words"))

    def test_a_replace_with_nothing_removed_or_added_marks_only_what_changed(self) -> None:
        text = _page(_block("a b c", "a X c"))
        assert "<del>b</del> <ins>X</ins>" in text

    def test_five_changed_runs_fall_back_to_was_and_becomes(self) -> None:
        text = _page(_block("a b c d e f g h i j", "a1 b c1 d e1 f g1 h i1 j"))
        assert "**Was**" in text and "**Becomes**" in text

    def test_placeholders_show_as_n_and_date(self) -> None:
        text = _page(OWN["decision-new"], OWN["git-ops-version"])
        assert "[N]" in text and "[date]" in text and "{{" not in text


class TestLocationsAndHeader:
    def test_each_block_shows_its_file_and_the_place_its_caller_found(self) -> None:
        text = _page(_block("a", "a b", "x", "docs/x.md"), places={"x": "## Heading"})
        assert "Location: `docs/x.md`, \\#\\# Heading" in text

    def test_a_block_without_a_found_place_says_so(self) -> None:
        assert "place not found" in _page(_block("a", "a b"))

    def test_the_header_shows_slug_fingerprint_and_both_digests(self) -> None:
        text = _page(_block("a", "a b"))
        assert "# Approve wording: demo" in text and review_page.fingerprint(APPROVED) in text
        assert APPROVED in text and WORDING in text and "Blocks: 1" in text


class TestEscaping:
    def test_markdown_and_html_in_wording_cannot_restyle_the_page(self) -> None:
        text = _page(_block("a", "a **bold** <del>x</del> [l](u) # h & more"))
        assert "\\*\\*bold\\*\\*" in text and "&lt;del" in text and "\\[l\\]" in text and "\\# h" in text
        assert "&amp; more" in text and "<del>x</del>" not in text


class TestFingerprint:
    def test_four_words_stable_and_following_the_digest(self) -> None:
        one, two = review_page.fingerprint("00" * 32), review_page.fingerprint("01" + "00" * 31)
        assert len(one.split("-")) == 4 and one == review_page.fingerprint("00" * 32) and one != two

    def test_the_word_list_has_256_distinct_words(self) -> None:
        assert len(review_page._FINGERPRINT_WORDS) == len(set(review_page._FINGERPRINT_WORDS)) == 256


class TestTldr:
    def _with(self, tldr: str | None) -> list[str]:
        block = _block("a", "a b")
        return review_page.render([block], {}, slug="demo", approved=APPROVED, wording=WORDING, tldr=tldr).splitlines()

    def test_the_tldr_follows_the_h1_and_precedes_the_fingerprint(self) -> None:
        lines = self._with("Why this change.")
        assert lines[:3] == ["# Approve wording: demo", "", "**TLDR:** Why this change."]
        assert lines.index("**TLDR:** Why this change.") < lines.index(f"Fingerprint: **{review_page.fingerprint(APPROVED)}**")

    def test_the_context_only_line_is_present(self) -> None:
        assert "_Context only: the TLDR is not part of the approved words._" in self._with("x")

    def test_the_tldr_is_whitespace_collapsed_and_escaped(self) -> None:
        lines = self._with("one  two\n*bold* <b> & [l](u)")
        assert lines[2] == "**TLDR:** one two \\*bold\\* &lt;b\\> &amp; \\[l\\]\\(u\\)"

    def test_without_a_tldr_the_page_is_todays_shape(self) -> None:
        for absent in (None, ""):
            lines = self._with(absent)
            assert lines[:3] == ["# Approve wording: demo", "", f"Fingerprint: **{review_page.fingerprint(APPROVED)}**"]
            assert "TLDR" not in "\n".join(lines)
