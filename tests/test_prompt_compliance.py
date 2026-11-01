"""Tests for scripts/prompt_compliance.py."""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "prompt_compliance.py"
_spec = importlib.util.spec_from_file_location("prompt_compliance", _SCRIPT_PATH)
_compliance = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_compliance)  # type: ignore[union-attr]
sys.modules["prompt_compliance"] = _compliance

parse_invariants = _compliance.parse_invariants
parse_retro_lite_log = _compliance.parse_retro_lite_log
parse_execution_state = _compliance.parse_execution_state
check_retro_lite_compliance = _compliance.check_retro_lite_compliance
_load_instruction_architecture = _compliance._load_instruction_architecture
get_behavioural_invariant_sources = _compliance.get_behavioural_invariant_sources
check_layer_compliance = _compliance.check_layer_compliance


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset the module-level cache before each test."""
    _compliance._INSTRUCTION_ARCH_REGISTRY = None
    yield
    _compliance._INSTRUCTION_ARCH_REGISTRY = None


class TestLoadInstructionArchitecture:
    """Tests for _load_instruction_architecture()."""

    def test_no_io_at_import(self) -> None:
        """_INSTRUCTION_ARCH_REGISTRY is None at import -- loader is lazy."""
        _compliance._INSTRUCTION_ARCH_REGISTRY = None
        assert _compliance._INSTRUCTION_ARCH_REGISTRY is None

    def test_reads_yaml_from_disk(self) -> None:
        """Loader reads the real instruction-architecture.yaml and caches it."""
        data = _load_instruction_architecture()
        assert isinstance(data, dict)
        assert "layers" in data
        assert "behavioural_invariant_sources" in data
        # Verify it is cached after first call
        assert _compliance._INSTRUCTION_ARCH_REGISTRY is data

    def test_absent_yaml_returns_fallback(self, tmp_path: Path) -> None:
        """Missing YAML returns fallback without raising."""
        orig = _compliance._INSTRUCTION_ARCH_PATH
        _compliance._INSTRUCTION_ARCH_PATH = tmp_path / "nope.yaml"
        try:
            data = _load_instruction_architecture()
        finally:
            _compliance._INSTRUCTION_ARCH_PATH = orig
        assert data == {"behavioural_invariant_sources": [".claude/skills/*/SKILL.md"], "layers": []}

    def test_unparseable_yaml_returns_fallback(self, tmp_path: Path) -> None:
        """YAML with a parse error returns fallback without raising."""
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text(": : invalid\n", encoding="utf-8")
        orig = _compliance._INSTRUCTION_ARCH_PATH
        _compliance._INSTRUCTION_ARCH_PATH = bad_yaml
        try:
            data = _load_instruction_architecture()
        finally:
            _compliance._INSTRUCTION_ARCH_PATH = orig
        assert data["behavioural_invariant_sources"] == [".claude/skills/*/SKILL.md"]

    def test_yaml_import_unavailable_returns_fallback(self, tmp_path: Path) -> None:
        """If yaml is unavailable (ImportError), loader returns fallback."""
        orig_path = _compliance._INSTRUCTION_ARCH_PATH
        contract_file = tmp_path / "ia.yaml"
        contract_file.write_text(
            "behavioural_invariant_sources: ['.claude/skills/*/SKILL.md']\nlayers: []\n", encoding="utf-8"
        )
        _compliance._INSTRUCTION_ARCH_PATH = contract_file

        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "yaml":
                raise ImportError("yaml not available")
            return real_import(name, *args, **kwargs)

        try:
            with patch("builtins.__import__", side_effect=mock_import):
                data = _load_instruction_architecture()
        finally:
            _compliance._INSTRUCTION_ARCH_PATH = orig_path

        assert data["behavioural_invariant_sources"] == [".claude/skills/*/SKILL.md"]

    def test_caches_result(self) -> None:
        """Second call returns the same object without re-reading disk."""
        data1 = _load_instruction_architecture()
        data2 = _load_instruction_architecture()
        assert data1 is data2


class TestGetBehaviouralInvariantSources:
    """Tests for get_behavioural_invariant_sources()."""

    def test_reads_from_yaml(self) -> None:
        """Returns the list from the real instruction-architecture.yaml."""
        sources = get_behavioural_invariant_sources()
        assert isinstance(sources, list)
        assert len(sources) >= 1
        assert ".claude/skills" in "".join(sources)

    def test_fallback_when_yaml_absent(self, tmp_path: Path) -> None:
        """Returns default fallback when YAML is missing."""
        orig = _compliance._INSTRUCTION_ARCH_PATH
        _compliance._INSTRUCTION_ARCH_PATH = tmp_path / "nope.yaml"
        try:
            sources = get_behavioural_invariant_sources()
        finally:
            _compliance._INSTRUCTION_ARCH_PATH = orig
        assert sources == [".claude/skills/*/SKILL.md"]

    def test_fallback_when_sources_empty(self) -> None:
        """Returns default fallback when YAML has empty behavioural_invariant_sources."""
        _compliance._INSTRUCTION_ARCH_REGISTRY = {"behavioural_invariant_sources": [], "layers": []}
        sources = get_behavioural_invariant_sources()
        assert sources == [".claude/skills/*/SKILL.md"]


class TestCheckLayerCompliance:
    """Tests for check_layer_compliance()."""

    def test_passes_against_live_repo(self) -> None:
        """No violations on the real repo -- every declared layer resolves."""
        contract = _load_instruction_architecture()
        violations = check_layer_compliance(contract)
        assert violations == [], violations

    def test_violation_when_glob_matches_nothing(self, tmp_path: Path) -> None:
        """Returns a violation when a layer content_locations glob matches nothing."""
        contract = {
            "layers": [
                {
                    "layer": 99,
                    "name": "Ghost layer",
                    "content_locations": ["does/not/exist/*.md"],
                }
            ]
        }
        orig_root = _compliance.ROOT
        _compliance.ROOT = tmp_path
        try:
            violations = check_layer_compliance(contract)
        finally:
            _compliance.ROOT = orig_root
        assert len(violations) == 1
        assert "layer 99" in violations[0]
        assert "does/not/exist/*.md" in violations[0]

    def test_venv_files_excluded(self, tmp_path: Path) -> None:
        """Files under .venv/ do not satisfy a content_locations glob."""
        venv_claude = tmp_path / ".venv" / "some_pkg"
        venv_claude.mkdir(parents=True)
        (venv_claude / "CLAUDE.md").write_text("# pkg claude", encoding="utf-8")

        contract = {
            "layers": [
                {
                    "layer": 1,
                    "name": "Universal rules",
                    "content_locations": ["**/CLAUDE.md"],
                }
            ]
        }
        orig_root = _compliance.ROOT
        _compliance.ROOT = tmp_path
        try:
            violations = check_layer_compliance(contract)
        finally:
            _compliance.ROOT = orig_root
        # Only file under .venv -- should not count as satisfying the glob
        assert len(violations) == 1
        assert "layer 1" in violations[0]

    def test_git_files_excluded(self, tmp_path: Path) -> None:
        """Files under .git/ do not satisfy a content_locations glob."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "CLAUDE.md").write_text("# git claude", encoding="utf-8")

        contract = {
            "layers": [
                {
                    "layer": 1,
                    "name": "Universal rules",
                    "content_locations": ["**/CLAUDE.md"],
                }
            ]
        }
        orig_root = _compliance.ROOT
        _compliance.ROOT = tmp_path
        try:
            violations = check_layer_compliance(contract)
        finally:
            _compliance.ROOT = orig_root
        assert len(violations) == 1

    def test_first_party_files_count(self, tmp_path: Path) -> None:
        """A first-party CLAUDE.md (not under .venv/.git) satisfies the glob."""
        (tmp_path / "CLAUDE.md").write_text("# root", encoding="utf-8")

        contract = {
            "layers": [
                {
                    "layer": 1,
                    "name": "Universal rules",
                    "content_locations": ["**/CLAUDE.md"],
                }
            ]
        }
        orig_root = _compliance.ROOT
        _compliance.ROOT = tmp_path
        try:
            violations = check_layer_compliance(contract)
        finally:
            _compliance.ROOT = orig_root
        assert violations == []

    def test_empty_layers_no_violations(self) -> None:
        """Empty layers list produces no violations."""
        assert check_layer_compliance({"layers": []}) == []


class TestParseInvariants:
    """Tests for parse_invariants()."""

    def test_parses_invariants_from_prompt(self, tmp_path: Path) -> None:
        """Valid ## Behavioural Invariants YAML block is parsed correctly."""
        prompt = tmp_path / "implement.prompt.md"
        prompt.write_text(
            "## Intent\n\nDo stuff.\n\n"
            "## Behavioural Invariants\n\n"
            "```yaml\n"
            "retro_lite_per_step: true\n"
            "checkpoint_per_step: true\n"
            "```\n\n"
            "## Step 1\n",
            encoding="utf-8",
        )
        result = parse_invariants(prompt)
        assert result == {"retro_lite_per_step": True, "checkpoint_per_step": True}

    def test_returns_empty_dict_when_no_section(self, tmp_path: Path) -> None:
        """Prompt without Behavioural Invariants returns empty dict."""
        prompt = tmp_path / "other.prompt.md"
        prompt.write_text("## Intent\n\nNo invariants here.\n", encoding="utf-8")
        result = parse_invariants(prompt)
        assert result == {}

    def test_returns_empty_dict_for_missing_file(self, tmp_path: Path) -> None:
        """Missing file returns empty dict (no exception)."""
        result = parse_invariants(tmp_path / "nonexistent.prompt.md")
        assert result == {}

    def test_returns_empty_dict_for_invalid_yaml(self, tmp_path: Path) -> None:
        """Malformed YAML in invariants block returns empty dict."""
        prompt = tmp_path / "bad.prompt.md"
        prompt.write_text(
            "## Behavioural Invariants\n\n```yaml\n: : bad yaml\n```\n",
            encoding="utf-8",
        )
        result = parse_invariants(prompt)
        assert result == {}

    def test_boolean_coercion(self, tmp_path: Path) -> None:
        """YAML true/false values are coerced to Python bool."""
        prompt = tmp_path / "coerce.prompt.md"
        prompt.write_text(
            "## Behavioural Invariants\n\n```yaml\nfoo: true\nbar: false\n```\n",
            encoding="utf-8",
        )
        result = parse_invariants(prompt)
        assert result["foo"] is True
        assert result["bar"] is False


class TestParseRetroLiteLog:
    """Tests for parse_retro_lite_log()."""

    def test_parses_valid_jsonl(self, tmp_path: Path) -> None:
        """Valid JSONL entries are parsed and returned."""
        log = tmp_path / ".retro-lite-log.jsonl"
        log.write_text(
            '{"timestamp": "2026-01-01T00:00:00Z", "session": "agent/foo", "friction": "none"}\n'
            '{"timestamp": "2026-01-02T00:00:00Z", "session": "agent/bar", "friction": "retry"}\n',
            encoding="utf-8",
        )
        result = parse_retro_lite_log(log)
        assert len(result) == 2
        assert result[0]["session"] == "agent/foo"

    def test_session_filter(self, tmp_path: Path) -> None:
        """session_filter returns only matching entries."""
        log = tmp_path / ".retro-lite-log.jsonl"
        log.write_text(
            '{"session": "agent/foo", "friction": "none"}\n{"session": "agent/bar", "friction": "retry"}\n',
            encoding="utf-8",
        )
        result = parse_retro_lite_log(log, session_filter="agent/foo")
        assert len(result) == 1
        assert result[0]["session"] == "agent/foo"

    def test_skips_malformed_lines(self, tmp_path: Path) -> None:
        """Malformed JSON lines are skipped without raising an exception."""
        log = tmp_path / ".retro-lite-log.jsonl"
        log.write_text(
            '{"session": "agent/foo", "friction": "none"}\nthis is not json\n{"session": "agent/bar", "friction": "retry"}\n',
            encoding="utf-8",
        )
        result = parse_retro_lite_log(log)
        assert len(result) == 2

    def test_returns_empty_for_missing_file(self, tmp_path: Path) -> None:
        """Missing log file returns empty list (no exception)."""
        result = parse_retro_lite_log(tmp_path / "nonexistent.jsonl")
        assert result == []


class TestParseExecutionState:
    """Tests for parse_execution_state()."""

    def test_parses_valid_json(self, tmp_path: Path) -> None:
        """Valid execution state JSON is parsed and returned as dict."""
        state = tmp_path / ".execution-state.json"
        state.write_text(
            json.dumps({"branch": "agent/foo", "current_step": 3, "total_steps": 5}),
            encoding="utf-8",
        )
        result = parse_execution_state(state)
        assert result is not None
        assert result["current_step"] == 3
        assert result["total_steps"] == 5

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        """Missing state file returns None (no exception)."""
        result = parse_execution_state(tmp_path / "nonexistent.json")
        assert result is None

    def test_returns_none_for_invalid_json(self, tmp_path: Path) -> None:
        """Invalid JSON in state file returns None."""
        state = tmp_path / ".execution-state.json"
        state.write_text("not json", encoding="utf-8")
        result = parse_execution_state(state)
        assert result is None


class TestCheckRetroLiteCompliance:
    """Tests for check_retro_lite_compliance()."""

    def test_passes_when_entry_count_matches_steps(self) -> None:
        """No violations when retro-lite entry count equals total_steps."""
        invariants = {"retro_lite_per_step": True}
        entries = [
            {"session": "agent/foo"},
            {"session": "agent/foo"},
            {"session": "agent/foo"},
        ]
        state = {"total_steps": 3, "current_step": 3, "status": "COMPLETE"}
        violations = check_retro_lite_compliance(invariants, entries, state)
        assert violations == []

    def test_fails_when_entries_less_than_steps(self) -> None:
        """Violation raised when fewer retro entries than total_steps."""
        invariants = {"retro_lite_per_step": True}
        entries = [{"session": "agent/foo"}]
        state = {"total_steps": 3, "current_step": 1, "status": "IN_PROGRESS"}
        violations = check_retro_lite_compliance(invariants, entries, state)
        assert len(violations) == 1
        assert "retro_lite_per_step" in violations[0]

    def test_passes_when_invariant_not_set(self) -> None:
        """No violations when retro_lite_per_step is not in invariants."""
        invariants: dict = {}
        violations = check_retro_lite_compliance(invariants, [], None)
        assert violations == []

    def test_no_violations_when_state_is_none(self) -> None:
        """No violations when execution state is None (cannot check)."""
        invariants = {"retro_lite_per_step": True}
        violations = check_retro_lite_compliance(invariants, [], None)
        assert violations == []


class TestMainPromptMode:
    def test_main_prompt_mode_reports_no_violations(self, tmp_path: Path, monkeypatch, capsys) -> None:
        skill = tmp_path / "SKILL.md"
        skill.write_text(
            "# Skill\n\n## Behavioural Invariants\n\n```yaml\npreflight_run: true\n```\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(_compliance, "ROOT", tmp_path)
        monkeypatch.setattr(sys, "argv", ["prompt_compliance", "--prompt", str(skill)])
        with pytest.raises(SystemExit) as exc:
            _compliance.main()
        assert exc.value.code == 0
        assert "no violations" in capsys.readouterr().out
