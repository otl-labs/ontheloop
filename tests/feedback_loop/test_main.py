"""The CLI contract: counts line grammar, CI refusals, the assert-quiet exit contract, error lines and the package boundary."""

from __future__ import annotations

import ast
import io
import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from scripts.feedback_loop import __main__ as cli
from scripts.feedback_loop import friction
from src.common.ducklake_reader_client import ReaderInvokeError
from tests import feedback_loop as fx

TODAY = date(2026, 11, 21)
LINE = re.compile(
    r"FRICTION window=\d{4}-\d\d-\d\d\.\.\d{4}-\d\d-\d\d detector_version=\d+ classifier_version=\d+ calls=\d+ rows=\d+ "
    r"friction_trees=\d+ process_event_facts=\d+ unmapped=\S+ coverage=(finding|ok|unmeasurable) findings=\d+ proposed=\d+ "
    r"decided=\d+ unparsed_recs=\d+ other_version_rows=\d+ conflicted_rows=\d+ response_bytes=\d+ elapsed_s=\d+\n"
)
PLANTED_HOST = "https://planted-host.lambda-url.eu-west-2.on.aws"
V = fx.VERSION


def many(trees: int = 12) -> list[dict[str, Any]]:
    return [fx.row(i, fact_count=2) for i in range(1, trees + 1)] + [fx.generic_row(30, 5)]


def run(argv: list[str], reader: Any, monkeypatch: pytest.MonkeyPatch | None = None) -> tuple[int, str]:
    out = io.StringIO()
    code = cli.main(["friction", *argv], reader=reader, today=TODAY, out=out, err=io.StringIO())
    return code, out.getvalue()


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


def test_counts_line_grammar_and_exit_zero_whatever_the_findings(tmp_path: Path) -> None:
    code, out = run([], fx.Reader(many()))
    assert code == 0 and LINE.fullmatch(out), out
    code, out = run([], fx.Reader([]))
    assert code == 0 and LINE.fullmatch(out) and "coverage=unmeasurable" in out


def test_json_and_sheet_are_refused_under_github_actions_before_any_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    for flag in ("--json", "--sheet"):
        reader = fx.Reader(many())
        code, out = run([flag, str(tmp_path / "x")], reader)
        assert code == 2 and out == "FRICTION_REFUSED reason=private_output_in_ci\n"
        assert reader.calls == [] and not (tmp_path / "x").exists()


def test_to_today_or_later_is_refused() -> None:
    assert run(["--to", "2026-11-21"], fx.Reader())[0] == 2
    assert run(["--to", "2026-11-22"], fx.Reader())[0] == 2


def test_json_shape_every_finding_carries_a_draft(tmp_path: Path) -> None:
    path = tmp_path / "f.json"
    rows = many() + [fx.generic_row(31, 60)]
    code, _ = run(["--json", str(path), "--sheet", str(tmp_path / "s.md")], fx.Reader(rows))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert code == 0 and set(payload) == {"window", "versions", "counts", "findings"}
    assert set(payload["window"]) == {"from", "to"} and payload["window"]["to"] == "2026-11-20"
    assert payload["findings"]
    for item in payload["findings"]:
        assert set(item) == {"detector_id", "subject_key", "state", "rec_id", "draft"}
        assert set(item["draft"]) == {"fields", "context_v2_json"}
    assert (tmp_path / "s.md").read_text(encoding="utf-8").startswith("# Friction review sheet")


def test_summary_appends(tmp_path: Path) -> None:
    path = tmp_path / "sum.md"
    path.write_text("existing\n", encoding="utf-8")
    run(["--summary", str(path)], fx.Reader(many()))
    run(["--summary", str(path)], fx.Reader(many()))
    text = path.read_text(encoding="utf-8")
    assert text.startswith("existing\n") and text.count("## Friction detector") == 2


def test_the_logging_disable_level_is_restored() -> None:
    logging.disable(logging.WARNING)
    try:
        run([], fx.Reader(many()))
        assert logging.root.manager.disable == logging.WARNING
    finally:
        logging.disable(logging.NOTSET)


class Boom(ReaderInvokeError):
    pass


def test_a_window_failure_prints_stage_type_and_status_only() -> None:
    err = ReaderInvokeError("named", 502, f"bad gateway {PLANTED_HOST}", {"error_type": "upstream_failed"})
    code, out = run([], fx.Reader(error=err))
    assert code == 1 and out == "FRICTION_STOP stage=window error_type=upstream_failed status=502\n"
    assert PLANTED_HOST not in out


def test_a_rec_read_failure_prints_stage_recs() -> None:
    err = ReaderInvokeError("current_state", 503, f"down {PLANTED_HOST}", None)
    code, out = run([], fx.Reader(many(), rec_error=err))
    assert code == 1 and out == "FRICTION_STOP stage=recs error_type=ReaderInvokeError status=503\n"


def test_an_unexpected_exception_prints_internal_with_no_traceback_in_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    err = io.StringIO()
    out = io.StringIO()
    monkeypatch.setattr(friction, "detect", lambda *a, **k: (_ for _ in ()).throw(ValueError(PLANTED_HOST)))
    code = cli.main(["friction"], reader=fx.Reader(many()), today=TODAY, out=out, err=err)
    assert code == 1 and out.getvalue() == "FRICTION_STOP stage=internal error_type=ValueError status=none\n"
    assert err.getvalue() == ""


def quiet(detector: str, subject: str, version: int, reader: Any) -> tuple[int, str]:
    return run(["--assert-quiet", detector, subject, "--classifier-version", str(version)], reader)


FIRING = fx.recurring(3, 6) + [
    fx.row(i, fact_count=1, signature="hook:merge_gate", rule_id="r07", label="merge_gate_block") for i in range(4, 14)
]


def test_assert_quiet_recurring_exit_contract() -> None:
    subject = "r01|hook:never_on_main"
    assert quiet(friction.RECURRING, subject, V, fx.Reader(FIRING))[0] == 2
    assert quiet(friction.RECURRING, subject, V, fx.Reader(FIRING))[1] == "FRICTION_QUIET verdict=not_quiet\n"
    assert quiet(friction.RECURRING, "r09|hook:protected_action_gate", V, fx.Reader(FIRING)) == (
        0,
        "FRICTION_QUIET verdict=quiet\n",
    )
    reason = lambda r: f"FRICTION_QUIET verdict=unmeasurable reason={r}\n"  # noqa: E731
    assert quiet(friction.RECURRING, subject, V - 1, fx.Reader(FIRING)) == (2, reason("version_mismatch"))
    assert quiet(friction.RECURRING, subject, V + 1, fx.Reader(FIRING)) == (2, reason("version_mismatch"))
    assert quiet(friction.RECURRING, subject, V, fx.Reader(fx.recurring(3, 6))) == (2, reason("few_trees"))
    assert quiet(friction.RECURRING, subject, V, fx.Reader([])) == (2, reason("few_trees"))
    failing = ReaderInvokeError("named", 503, "down", None)
    assert quiet(friction.RECURRING, subject, V, fx.Reader(error=failing)) == (2, reason("read_failed"))


def test_assert_quiet_coverage_exit_contract() -> None:
    named = [fx.row(i, fact_count=10) for i in range(1, 13)]
    reason = lambda r: f"FRICTION_QUIET verdict=unmeasurable reason={r}\n"  # noqa: E731
    assert quiet(friction.COVERAGE, "coverage", V, fx.Reader(named)) == (0, "FRICTION_QUIET verdict=quiet\n")
    assert quiet(friction.COVERAGE, "coverage", V - 1, fx.Reader(named)) == (0, "FRICTION_QUIET verdict=quiet\n")
    assert quiet(friction.COVERAGE, "coverage", V + 1, fx.Reader(named)) == (2, reason("version_mismatch"))
    assert quiet(friction.COVERAGE, "coverage", V, fx.Reader(named + [fx.generic_row(40, 90)])) == (
        2,
        "FRICTION_QUIET verdict=not_quiet\n",
    )
    few_facts = [fx.row(i, fact_count=1) for i in range(1, 13)]
    assert quiet(friction.COVERAGE, "coverage", V, fx.Reader(few_facts)) == (2, reason("coverage_unmeasurable"))


def test_assert_quiet_needs_the_version_a_known_detector_and_skips_the_rec_read() -> None:
    assert run(["--assert-quiet", friction.COVERAGE, "coverage"], fx.Reader())[0] == 2
    assert run(["--assert-quiet", "other", "x", "--classifier-version", "3"], fx.Reader())[0] == 2
    reader = fx.Reader(FIRING)
    quiet(friction.RECURRING, "r09|hook:protected_action_gate", V, reader)
    assert all(call[0] == "friction_signature_counts" for call in reader.calls)


_PACKAGE = Path(__file__).resolve().parents[2] / "scripts" / "feedback_loop"
_FORBIDDEN_NAMES = {"file_rec", "update_rec", "file_ops"}


def _imports(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
    return names


class TestPackageBoundary:
    def test_no_portal_or_writer_import_and_no_filing_name(self) -> None:
        files = sorted(_PACKAGE.glob("*.py"))
        assert {f.name for f in files} >= {"__init__.py", "friction.py", "proposals.py", "render.py", "__main__.py"}
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for name in _imports(tree):
                assert not name.startswith(("scripts.ops_data_portal", "scripts.ops_portal")), (path.name, name)
                assert "ducklake_writer" not in name, (path.name, name)
            used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
                n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
            }
            aliases = {
                a.asname or a.name.rsplit(".", 1)[-1]
                for n in ast.walk(tree)
                if isinstance(n, (ast.Import, ast.ImportFrom))
                for a in n.names
            }
            assert not ((used | aliases) & _FORBIDDEN_NAMES), path.name

    def test_the_scan_flags_what_it_forbids(self) -> None:
        tree = ast.parse("from scripts.ops_data_portal import file_rec\nimport src.common.ducklake_writer_x\n")
        assert {"scripts.ops_data_portal", "src.common.ducklake_writer_x"} <= _imports(tree)


def test_an_output_write_failure_is_an_internal_stop_without_a_traceback_in_ci(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    err = io.StringIO()
    out = io.StringIO()
    code = cli.main(
        ["friction", "--summary", str(tmp_path / "missing" / "sum.md")],
        reader=fx.Reader(many()),
        today=TODAY,
        out=out,
        err=err,
    )
    assert code == 1 and out.getvalue() == "FRICTION_STOP stage=internal error_type=FileNotFoundError status=none\n"
    assert err.getvalue() == ""


def test_assert_quiet_internal_error_prints_the_internal_stage_and_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(friction, "detect", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    code, out = quiet(friction.RECURRING, "r01|hook:never_on_main", V, fx.Reader(many()))
    assert (code, out) == (2, "FRICTION_STOP stage=internal error_type=ValueError status=none\n")
