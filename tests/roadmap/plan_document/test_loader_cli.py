"""Tests for scripts/roadmap/plan_document.py -- file loading, CLI, and validate_plan_documents
integration, covering the T1.11 exit criteria.

Split from the former tests/test_plan_document.py monolith (PLAN-decompose-test-plan-document,
Decision 128 decompose-by-default): TestLoader, TestCli, TestValidateIntegration relocated
verbatim.
"""

from __future__ import annotations

import shutil

import pytest
import yaml
from pydantic import ValidationError

from scripts.checks.roadmap.validate_plan_documents import validate_plan_documents
from scripts.roadmap.plan_document import context_block_lines, load, main, validate_paths
from tests.fixtures.plan_document_helpers import FIXTURES, _mutate
from tests.fixtures.yaml_parse_spy import install as install_yaml_parse_spy


def test_context_block_lines_counts_rendered_span(tmp_path):
    p = tmp_path / "PLAN-zz-span.yaml"
    p.write_text("slug: x\ncontext:\n  - a\n  - b\nphase: y\n", encoding="utf-8")
    assert context_block_lines(p) == 3  # next-top-level-key branch


def test_context_block_lines_to_eof(tmp_path):
    p = tmp_path / "PLAN-zz-eof.yaml"
    p.write_text("slug: x\ncontext:\n  - a\n  - b\n", encoding="utf-8")
    assert context_block_lines(p) == 3  # EOF branch


def test_context_block_lines_absent_is_zero(tmp_path):
    p = tmp_path / "PLAN-zz-none.yaml"
    p.write_text("slug: x\nphase: y\n", encoding="utf-8")
    assert context_block_lines(p) == 0  # absent branch


class TestLoader:
    def test_load_valid_file(self, tmp_path):
        target = tmp_path / "PLAN-zz-valid-demo.yaml"
        shutil.copy(FIXTURES / "valid.yaml", target)
        doc = load(target)
        assert doc.slug == "zz-valid-demo"

    def test_load_malformed_fixture_fails_on_command(self, tmp_path):
        target = tmp_path / "PLAN-zz-malformed-demo.yaml"
        shutil.copy(FIXTURES / "malformed_missing_command.yaml", target)
        with pytest.raises(ValidationError, match="non-empty executable command"):
            load(target)

    def test_filename_slug_guard(self, tmp_path):
        target = tmp_path / "PLAN-wrong-name.yaml"
        shutil.copy(FIXTURES / "valid.yaml", target)
        with pytest.raises(ValueError, match="does not match slug"):
            load(target)

    def test_validate_paths_reports_failures(self, tmp_path):
        good = tmp_path / "PLAN-zz-valid-demo.yaml"
        bad = tmp_path / "PLAN-zz-malformed-demo.yaml"
        shutil.copy(FIXTURES / "valid.yaml", good)
        shutil.copy(FIXTURES / "malformed_missing_command.yaml", bad)
        failures = validate_paths([good, bad])
        assert len(failures) == 1
        assert failures[0][0] == bad


class TestLoaderUsesLibyaml:
    def test_load_parses_with_libyaml_not_the_pure_python_loader(self, tmp_path, monkeypatch):
        """rec-4778: corpus-wide callers parse every plan, and the pure-Python parser made the corpus
        tests cross the 120s pytest timeout on slow CI runners."""
        target = tmp_path / "PLAN-zz-valid-demo.yaml"
        shutil.copy(FIXTURES / "valid.yaml", target)
        log = install_yaml_parse_spy(monkeypatch)
        load(target)
        assert log.pure_entries == []
        assert len(log.libyaml_sizes) == 1


class TestCli:
    def test_main_pass_on_valid(self, tmp_path, capsys):
        target = tmp_path / "PLAN-zz-valid-demo.yaml"
        shutil.copy(FIXTURES / "valid.yaml", target)
        assert main([str(target)]) == 0
        assert "PASS" in capsys.readouterr().out

    def test_main_fail_on_malformed(self, tmp_path, capsys):
        target = tmp_path / "PLAN-zz-malformed-demo.yaml"
        shutil.copy(FIXTURES / "malformed_missing_command.yaml", target)
        assert main([str(target)]) == 1
        assert "FAIL" in capsys.readouterr().out

    def test_main_default_glob_empty_dir(self, tmp_path, capsys):
        assert main([], plans_root=tmp_path) == 0
        assert "no PLAN-*.yaml files found" in capsys.readouterr().out

    def test_main_default_glob_finds_files(self, tmp_path, capsys):
        target = tmp_path / "PLAN-zz-valid-demo.yaml"
        shutil.copy(FIXTURES / "valid.yaml", target)
        assert main([], plans_root=tmp_path) == 0
        assert "PASS" in capsys.readouterr().out

    def test_cli_warns_on_oversized_context_block(self, tmp_path, capsys):
        data = _mutate(context=[f"line {i}" for i in range(45)])
        target = tmp_path / f"PLAN-{data['slug']}.yaml"
        target.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        assert main([str(target)]) == 0  # still non-blocking
        out = capsys.readouterr().out
        assert "WARN:" in out and "context block is 46 rendered lines" in out

    def test_cli_silent_on_small_context_block(self, tmp_path, capsys):
        data = _mutate(context=[f"line {i}" for i in range(3)])
        target = tmp_path / f"PLAN-{data['slug']}.yaml"
        target.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        assert main([str(target)]) == 0
        assert "context block is" not in capsys.readouterr().out


class TestValidateIntegration:
    def test_validate_plan_documents_passes_on_valid_dir(self, tmp_path, capsys):
        target = tmp_path / "PLAN-zz-valid-demo.yaml"
        shutil.copy(FIXTURES / "valid.yaml", target)
        failed: list[str] = []
        validate_plan_documents(failed, plans_dir=tmp_path)
        assert failed == []
        assert "PASS" in capsys.readouterr().out

    def test_validate_plan_documents_fails_on_malformed(self, tmp_path, capsys):
        target = tmp_path / "PLAN-zz-malformed-demo.yaml"
        shutil.copy(FIXTURES / "malformed_missing_command.yaml", target)
        failed: list[str] = []
        validate_plan_documents(failed, plans_dir=tmp_path)
        assert "Plan document schema validation" in failed
        assert "FAIL" in capsys.readouterr().out

    def test_validate_plan_documents_empty_dir_passes(self, tmp_path, capsys):
        failed: list[str] = []
        validate_plan_documents(failed, plans_dir=tmp_path)
        assert failed == []
        assert "no PLAN-*.yaml files to validate" in capsys.readouterr().out
