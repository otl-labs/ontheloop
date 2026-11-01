"""mask() and fingerprint() behaviour over the shared corpus."""

from __future__ import annotations

import time

import pytest

from scripts.checks.misc import sensitive_value_corpus as corpus
from src.sensitive_values import CLASSES, MARKER_FORMAT, PROFILES, DetectorClass, MaskResult, engine, fingerprint, mask
from src.sensitive_values import classes as classes_mod

PROFILE_NAMES = sorted(PROFILES)


@pytest.mark.parametrize("case", corpus.positives(), ids=lambda c: c.case_id)
def test_read_masked_masks_every_positive(case: corpus.Case) -> None:
    result = mask(case.text, "read_masked")
    assert case.planted_value not in result.text
    assert MARKER_FORMAT.format(cls=case.class_id) in result.text
    assert result.counts.get(case.class_id, 0) >= 1


@pytest.mark.parametrize("profile", PROFILE_NAMES)
def test_second_pass_is_idempotent(profile: str) -> None:
    for case in corpus.positives():
        first = mask(case.text, profile)
        second = mask(first.text, profile)
        assert second.text == first.text, case.case_id
        assert second.counts == {}, case.case_id


@pytest.mark.parametrize("profile", PROFILE_NAMES)
def test_never_raises_and_counts_carry_ids_only(profile: str) -> None:
    for text in ("", "\ud800 lone surrogate", "x" * 10000, "\x00\n\t", "Bearer " + "9" * 5000):
        result = mask(text, profile)
        assert all(k in PROFILES[profile] and isinstance(v, int) for k, v in result.counts.items())


def test_negatives_untouched_and_known_false_positive() -> None:
    for case in corpus.negatives():
        assert mask(case.text, "read_masked").text == case.text, case.case_id
    for case, class_id in corpus.known_false_positives():
        assert mask(case.text, "read_masked").counts == {class_id: 1}


def test_write_time_leaves_read_only_classes_alone() -> None:
    case = next(c for c in corpus.positives() if c.class_id == "ACCOUNT_ID")
    assert mask(case.text, "write_time") == MaskResult(case.text, {})


def test_unknown_profile_and_accept_raise_key_error(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(KeyError) as err:
        mask("text", "no_such_profile")
    assert err.value.args == ("no_such_profile",)
    bad = DetectorClass("BEARER_TOKEN", "token", r"(a)(b)", "keep_prefix", classes_mod.AcceptSpec("nope"))
    monkeypatch.setitem(CLASSES, "BEARER_TOKEN", bad)
    with pytest.raises(KeyError):
        mask("ab", "write_time")


def test_mask_result_unpacks() -> None:
    text, counts = mask("nothing here", "write_time")
    assert (text, counts) == ("nothing here", {})


def test_fingerprint_stable_distinct_and_sensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    assert fingerprint("read_masked") == fingerprint("read_masked")
    assert fingerprint("write_time") != fingerprint("read_masked")
    before = fingerprint("write_time")
    row = CLASSES["BEARER_TOKEN"]
    monkeypatch.setitem(CLASSES, "BEARER_TOKEN", DetectorClass(row.id, row.family, row.pattern, row.kind, row.accept, flags=2))
    assert fingerprint("write_time") != before
    monkeypatch.setitem(
        CLASSES,
        "BEARER_TOKEN",
        DetectorClass(row.id, row.family, row.pattern, row.kind, classes_mod.AcceptSpec("long_or_digit", (("min_len", 31),))),
    )
    assert fingerprint("write_time") != before
    monkeypatch.setitem(CLASSES, "BEARER_TOKEN", DetectorClass(row.id, row.family, row.pattern + "x", row.kind, row.accept))
    assert fingerprint("write_time") != before


def test_accept_predicate_semantics() -> None:
    keep = engine.ACCEPT["long_or_digit"](min_len=32)
    assert keep("a" * 32) and keep("short1") and not keep("shortletters")


@pytest.mark.parametrize("profile", PROFILE_NAMES)
def test_long_alphanumeric_run_masks_in_bounded_time(profile: str) -> None:
    started = time.monotonic()
    mask("A" * 200_000, profile)
    mask("a1" * 100_000 + "-" * 50_000, profile)
    assert time.monotonic() - started < 2.0
