"""Plan B (rec-lints-advisory): the glossary legs report and never refuse; the acceptance and dependency legs still refuse."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts import contract_rules
from scripts.ops_portal import write_validators as wv
from scripts.ops_portal.write_validators import LintResult, gate_write
from tests.fixtures.ducklake_fakes import ops_rec_fields
from tests.fixtures.ops_portal_records import VALID_FIELDS
from tests.fixtures.ops_rec_rows import reader_rec_row, writer_stand_in
from tests.fixtures.portal_gate import assert_portal_gate_clean

_BAD_TITLE = "Fix the platform runner for retries"


@pytest.fixture(autouse=True)
def _clean_caches():
    wv._producer_params.cache_clear()
    wv._load_glossary.cache_clear()
    yield
    wv._producer_params.cache_clear()
    wv._load_glossary.cache_clear()


def _by_column(results: tuple[LintResult, ...], column: str, kind: str = "glossary_lint") -> LintResult:
    return next(r for r in results if r.column == column and r.kind == kind)


def test_glossary_finding_is_reported_and_the_write_proceeds(capsys: pytest.CaptureFixture[str]) -> None:
    results = gate_write("ops_recommendations", {**ops_rec_fields(), "title": _BAD_TITLE}, insert=True)
    found = _by_column(results, "title")
    assert found.outcome == "finding"
    assert found.message.startswith('title: unqualified "platform" -- say instead: On The Loop, core')
    assert _by_column(results, "context").outcome == "pass"
    assert "[REC_LINT_ADVISORY] ops_recommendations.title glossary_lint finding" in capsys.readouterr().err


def test_every_advisory_finding_is_collected() -> None:
    record = {
        **ops_rec_fields(),
        "title": _BAD_TITLE,
        "verification": "Check the platform runner.",
        "context": "The platform runner stalls on retry. " * 4,
    }
    results = gate_write("ops_recommendations", record, insert=True)
    assert {r.column for r in results if r.outcome == "finding"} == {"title", "verification", "context"}


def test_unloadable_glossary_is_an_error_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for i, content in enumerate([None, "", "- just\n- a list\n", "relative_words: {platform: {}}\n"]):
        path = tmp_path / f"glossary{i}.yaml"
        if content is not None:
            path.write_text(content, encoding="utf-8")
        wv._load_glossary.cache_clear()
        with patch.object(wv, "_GLOSSARY_PATH", path):
            results = gate_write("ops_recommendations", {**ops_rec_fields(), "title": _BAD_TITLE}, insert=True)
        assert _by_column(results, "title").outcome == "error", content
        assert "glossary_lint error" in capsys.readouterr().err


def test_unchanged_column_is_skipped() -> None:
    stored = reader_rec_row()
    results = gate_write("ops_recommendations", {**stored, "priority": "High"}, insert=False, existing=stored)
    assert _by_column(results, "title").outcome == "skipped"
    changed = gate_write("ops_recommendations", {**stored, "title": _BAD_TITLE}, insert=False, existing=stored)
    assert _by_column(changed, "title").outcome == "finding"


def test_refusing_legs_still_refuse_beside_findings(capsys: pytest.CaptureFixture[str]) -> None:
    bad_acceptance = {
        **ops_rec_fields(),
        "title": _BAD_TITLE,
        "acceptance": "grep -q ops_data_portal scripts/ops_data_portal.py",
    }
    with pytest.raises(ValueError, match="does not discriminate"):
        gate_write("ops_recommendations", bad_acceptance, insert=True)
    assert "ops_recommendations.title glossary_lint finding" in capsys.readouterr().err
    wv._rec_exists_memo.clear()
    absent = MagicMock()
    absent.named.return_value = []
    dangling = {**ops_rec_fields(), "title": _BAD_TITLE, "dependencies": ["rec-8"]}
    with patch("src.common.ducklake_reader_client.make_reader", return_value=absent):
        with pytest.raises(ValueError, match="rec-8"):
            gate_write("ops_recommendations", dangling, insert=True)
    assert "ops_recommendations.title glossary_lint finding" in capsys.readouterr().err
    wv._rec_exists_memo.clear()


def test_contract_advisory_key_drives_the_mode(tmp_path: Path) -> None:
    contracts = tmp_path / "contracts"
    shutil.copytree(Path("docs/contracts"), contracts)
    target = contracts / "ops_recommendations.yaml"
    text = target.read_text(encoding="utf-8")
    assert "glossary_lint: {advisory: true}" in text
    target.write_text(text.replace("glossary_lint: {advisory: true}", "glossary_lint: {}"), encoding="utf-8")
    real = contract_rules.declared_rules
    with patch.object(contract_rules, "declared_rules", lambda table: real(table, contracts_dir=contracts)):
        wv._producer_params.cache_clear()
        with pytest.raises(ValueError, match='unqualified "platform"'):
            gate_write("ops_recommendations", {**ops_rec_fields(), "title": _BAD_TITLE}, insert=True)


def test_stderr_line_names_the_fix_and_the_write(capsys: pytest.CaptureFixture[str]) -> None:
    gate_write("ops_recommendations", {**ops_rec_fields(), "title": _BAD_TITLE}, insert=True)
    lines = [ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("[REC_LINT_ADVISORY]")]
    assert len(lines) == 1
    assert "say instead: On The Loop" in lines[0]
    assert lines[0].endswith("-- the portal does not refuse; an unenforced residual owned by rec-5239")


def test_file_rec_writes_a_rec_with_a_glossary_finding() -> None:
    write = writer_stand_in()
    with patch("scripts.ops_data_portal._ducklake_write", side_effect=write), patch("scripts.ops_data_portal._sync_table"):
        from scripts.ops_data_portal import file_rec

        file_rec({**VALID_FIELDS, "title": _BAD_TITLE})
    assert [(t, a) for t, a, _ in write.writes] == [("ops_recommendations", "file_ops")]


def test_update_rec_writes_a_change_with_a_glossary_finding() -> None:
    existing = reader_rec_row()
    write = writer_stand_in([existing])
    with (
        patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(existing)),
        patch("scripts.ops_data_portal._ducklake_write", side_effect=write),
        patch("scripts.ops_data_portal._sync_table"),
    ):
        from scripts.ops_data_portal import update_rec

        update_rec(existing["id"], {"context": existing["context"] + " The platform runner hangs too."})
    assert [(t, a) for t, a, _ in write.writes] == [("ops_recommendations", "update_ops")]


def test_contract_marks_glossary_legs_advisory_not_acceptance() -> None:
    legs = {(r.column, r.kind): dict(r.params) for r in contract_rules.declared_rules("ops_recommendations")}
    for column in ("title", "context", "verification"):
        assert legs[(column, "glossary_lint")].get("advisory") is True, column
    assert "advisory" not in legs[("acceptance", "acceptance_lint")]


def test_portal_gate_helper_fails_on_a_glossary_finding() -> None:
    assert_portal_gate_clean("ops_recommendations", ops_rec_fields(), insert=True)
    with pytest.raises(AssertionError, match="title glossary_lint finding"):
        assert_portal_gate_clean("ops_recommendations", {**ops_rec_fields(), "title": _BAD_TITLE}, insert=True)
