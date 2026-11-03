"""Tests for scripts/platform_roadmap_state.py's load() -- rec-4365's libyaml switch.

Mirror package member (Decision 131); shared fixture helpers live in
tests/fixtures/platform_roadmap_state.py -- never import from a sibling test_*.py module.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.platform_roadmap_state import load
from tests.fixtures.platform_roadmap_state import _BASE_DOC
from tests.fixtures.yaml_parse_spy import install as install_yaml_parse_spy


class TestLoadParser:
    def test_load_parses_with_libyaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        roadmap_path = tmp_path / "ROADMAP-TEST.yaml"
        roadmap_path.write_text(yaml.safe_dump(_BASE_DOC), encoding="utf-8")
        log = install_yaml_parse_spy(monkeypatch)
        doc = load(roadmap_path)
        assert doc.document.id == "ROADMAP-TEST"
        assert len(log.entries) == 1
        assert log.entries[0].kind == "libyaml"
