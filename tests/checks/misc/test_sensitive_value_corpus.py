"""Mirror home of the sensitive-value corpus module."""

from __future__ import annotations

import pytest

from scripts.checks.misc import sensitive_value_corpus as corpus
from src.sensitive_values import PROFILES


def test_case_ids_unique_and_well_formed() -> None:
    ids = [c.case_id for c in corpus.all_cases()]
    assert len(ids) == len(set(ids))
    assert all(corpus.CASE_ID_RE.match(i) for i in ids)


def test_at_least_two_positives_per_class_of_every_profile() -> None:
    per_class: dict[str, int] = {}
    for case in corpus.positives():
        per_class[case.class_id] = per_class.get(case.class_id, 0) + 1
    for ids in PROFILES.values():
        for class_id in ids:
            assert per_class.get(class_id, 0) >= 2, class_id


def test_planted_values_occur_and_negatives_carry_no_class() -> None:
    assert all(c.planted_value and c.planted_value in c.text for c in corpus.positives())
    assert all(c.class_id is None and c.planted_value is None for c in corpus.negatives())
    assert all(case.class_id is None for case, _ in corpus.known_false_positives())


def test_duplicate_or_slash_case_id_raises_at_call_time() -> None:
    one = corpus.Case("dup", None, "t", None)
    with pytest.raises(ValueError):
        corpus._check((one, one))
    with pytest.raises(ValueError):
        corpus._check((corpus.Case("a/b", None, "t", None),))


def test_no_credential_shape_literals_in_module_source() -> None:
    import re
    from pathlib import Path

    source = Path(corpus.__file__).read_text(encoding="utf-8")
    assert not re.search(r"(?<![0-9A-Za-z])\d{12}(?![0-9A-Za-z])", source)
    assert not re.search(r"\b(?:AKIA|ASIA|AIDA|AROA)[A-Z0-9]{16}\b", source)
