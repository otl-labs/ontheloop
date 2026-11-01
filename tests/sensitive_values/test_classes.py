"""Class-row well-formedness."""

from __future__ import annotations

import re

from src.sensitive_values import CLASSES, PROFILES
from src.sensitive_values.engine import ACCEPT


def test_rows_are_well_formed() -> None:
    assert len(CLASSES) == 13
    for class_id, row in CLASSES.items():
        assert row.id == class_id
        assert isinstance(row.regex, re.Pattern) and row.regex.pattern == row.pattern
        assert row.kind in {"whole", "keep_prefix"}
        if row.kind == "keep_prefix":
            assert row.regex.groups >= 1, class_id
        if row.accept is not None:
            assert row.accept.name in ACCEPT, class_id
            assert row.regex.groups >= 2, class_id


def test_ids_unique_and_in_no_profile_twice() -> None:
    for ids in PROFILES.values():
        assert len(ids) == len(set(ids))
        assert set(ids) <= set(CLASSES)


def test_replaced_part_refuses_a_marker() -> None:
    for class_id, row in CLASSES.items():
        marker = f"[REDACTED:{class_id}]"
        assert row.regex.search(marker) is None, class_id
        for text in (f"https://{marker}/x", f"password={marker}", f"assumed-role/R/{marker}", f"Bearer {marker}"):
            assert row.regex.search(text) is None, (class_id, text)
