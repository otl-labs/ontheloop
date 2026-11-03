"""serves/kind on docs/decisions-index.json rows (PLAN-decision-serves-kind-grammar, Decision 243 clause 8)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from scripts import decisions_index
from scripts.decisions_index import build_index
from scripts.ops_portal.decisions import _DECISION_BACKFILL_COLS

_ENVELOPED_WITH_KIND = (
    "## Decision 1: With kind\n\n```yaml\nnumber: 1\nserves: [NS.1, NS.2]\n"
    "kind:\n  value: direction\n  justification: j.\n```\n\n**Intent:** x\n"
)
_ENVELOPED_NO_KIND = "## Decision 2: No kind\n\n```yaml\nnumber: 2\nstatus: Decided\n```\n\n**Intent:** x\n"
_PRE_ENVELOPE = "## Decision 3: Old\n\n**Status:** Decided\n\n**Intent:** x\n"
_ARCHIVE_ENVELOPED = "## Decision 4: Archived\n\n```yaml\nnumber: 4\nkind: {value: mechanism, justification: j}\n```\n"


def _index(tmp_path: Path) -> dict[int, dict]:
    live = tmp_path / "DECISIONS.md"
    archive = tmp_path / "DECISIONS_ARCHIVE.md"
    live.write_text("\n".join([_ENVELOPED_WITH_KIND, _ENVELOPED_NO_KIND, _PRE_ENVELOPE]), encoding="utf-8")
    archive.write_text(_ARCHIVE_ENVELOPED, encoding="utf-8")
    from scripts import decisions_md

    with (
        patch.object(decisions_md, "_DECISIONS_MD_PATHS", [live, archive]),
        patch.object(decisions_index, "_LIVE_PATH", live),
    ):
        return {e["number"]: e for e in build_index()["decisions"]}


class TestServesKindBand:
    def test_declared_kind_projects_the_value_only_and_the_serves_list(self, tmp_path: Path) -> None:
        row = _index(tmp_path)[1]
        assert row["kind"] == {"value": "direction"}
        assert row["serves"] == ["NS.1", "NS.2"]

    def test_enveloped_row_without_kind_projects_null_and_empty_list(self, tmp_path: Path) -> None:
        row = _index(tmp_path)[2]
        assert row["kind"] is None
        assert row["serves"] == []

    def test_pre_envelope_row_carries_neither_key(self, tmp_path: Path) -> None:
        row = _index(tmp_path)[3]
        assert "kind" not in row and "serves" not in row

    def test_archive_row_carries_neither_key(self, tmp_path: Path) -> None:
        row = _index(tmp_path)[4]
        assert not row["live"]
        assert "kind" not in row and "serves" not in row


class TestNoWarehouseLeak:
    def test_backfill_columns_exclude_serves_and_kind(self) -> None:
        assert not {"serves", "kind"} & set(_DECISION_BACKFILL_COLS)
