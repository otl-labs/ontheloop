"""Tests for validate_dependabot_config() -- dependabot pip-group denylist exclusion guard."""

from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.checks import registry
from scripts.checks.ci_guards.validate_dependabot_config import validate_dependabot_config

_MODULE = "scripts.checks.ci_guards.validate_dependabot_config"
_ROOT = Path(__file__).resolve().parents[3]
_CHECK = "validate_dependabot_config"
_TAXONOMY_PATH = _ROOT / "config/ci_rca_taxonomy.yaml"

_VALID_SCRIPT = '_DENIED_DEPENDENCIES="duckdb ducklake"\n'

_VALID_CONFIG = """\
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    groups:
      minor-and-patch:
        update-types:
          - "minor"
          - "patch"
        exclude-patterns:
          - "duckdb"
          - "ducklake"
    open-pull-requests-limit: 5

  - package-ecosystem: "github-actions"
    directory: "/"
    cooldown:
      default-days: 7
    groups:
      minor-and-patch:
        update-types:
          - "minor"
          - "patch"
    open-pull-requests-limit: 5
"""


def _write(tmp_path: Path, *, script: str = _VALID_SCRIPT, config: str = _VALID_CONFIG) -> None:
    script_path = tmp_path / "scripts" / "ci" / "dependabot_auto_merge.sh"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script, encoding="utf-8")
    (tmp_path / ".github").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".github" / "dependabot.yml").write_text(config, encoding="utf-8")


def _run(tmp_path: Path) -> list[str]:
    with patch(f"{_MODULE}._common.ROOT", tmp_path):
        failed: list[str] = []
        validate_dependabot_config(failed)
    return failed


class TestPassPath:
    def test_passes_against_the_real_repo_config(self) -> None:
        failed: list[str] = []
        validate_dependabot_config(failed)
        assert failed == []

    def test_passes_with_well_formed_isolated_fixture(self, tmp_path: Path) -> None:
        _write(tmp_path)
        assert _run(tmp_path) == []


class TestExclusionFailPath:
    def test_group_with_no_exclude_patterns_key_at_all_fails(self, tmp_path: Path) -> None:
        config = """\
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    groups:
      minor-and-patch:
        update-types:
          - "minor"
          - "patch"
    open-pull-requests-limit: 5
"""
        _write(tmp_path, config=config)
        failed = _run(tmp_path)
        assert any("excludes every denylisted dependency" in f for f in failed)

    def test_exclude_patterns_missing_one_denied_name_fails(self, tmp_path: Path) -> None:
        # Fail-path fixture: a literal missing exactly one denied name IS the condition under
        # test (Decision 187's derive-don't-enumerate exemption for fail-path fixtures).
        config = _VALID_CONFIG.replace('          - "ducklake"\n', "")
        _write(tmp_path, config=config)
        failed = _run(tmp_path)
        assert any("excludes every denylisted dependency" in f for f in failed)

    def test_non_pip_ecosystem_group_is_never_examined(self, tmp_path: Path) -> None:
        """The github-actions group in _VALID_CONFIG carries no exclude-patterns and must not fail
        -- the assertion is scoped to package-ecosystem: pip only."""
        _write(tmp_path)
        assert _run(tmp_path) == []

    def test_updates_key_not_a_list_yields_no_pip_updates(self, tmp_path: Path) -> None:
        """A structurally malformed `updates:` value is not a groups.yaml concern of this check --
        it examines zero groups rather than raising."""
        _write(tmp_path, config="version: 2\nupdates: not-a-list\n")
        assert _run(tmp_path) == []

    def test_pip_update_with_no_groups_key_is_skipped_not_examined(self, tmp_path: Path) -> None:
        """A pip update entry with no `groups:` mapping at all contributes zero examined groups and
        must not fail -- only a real group can carry (or omit) exclude-patterns."""
        config = """\
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    open-pull-requests-limit: 5
  - package-ecosystem: "github-actions"
    directory: "/"
    cooldown:
      default-days: 7
"""
        _write(tmp_path, config=config)
        assert _run(tmp_path) == []


class TestCooldownLeg:
    def test_github_actions_without_cooldown_fails(self, tmp_path: Path) -> None:
        _write(tmp_path, config=_VALID_CONFIG.replace("    cooldown:\n      default-days: 7\n", ""))
        assert any("cooldown" in f for f in _run(tmp_path))

    def test_github_actions_cooldown_below_seven_days_fails(self, tmp_path: Path) -> None:
        _write(tmp_path, config=_VALID_CONFIG.replace("default-days: 7", "default-days: 6"))
        assert any("cooldown" in f for f in _run(tmp_path))

    def test_github_actions_cooldown_not_an_int_fails(self, tmp_path: Path) -> None:
        _write(tmp_path, config=_VALID_CONFIG.replace("default-days: 7", 'default-days: "7"'))
        assert any("cooldown" in f for f in _run(tmp_path))

    def test_config_without_github_actions_entry_fails(self, tmp_path: Path) -> None:
        config = _VALID_CONFIG.split('  - package-ecosystem: "github-actions"')[0]
        _write(tmp_path, config=config)
        assert any("cooldown" in f for f in _run(tmp_path))

    def test_github_actions_cooldown_seven_days_passes(self, tmp_path: Path) -> None:
        _write(tmp_path)
        assert _run(tmp_path) == []


class TestDenylistParseFailPath:
    def test_unparseable_denylist_fails_loudly(self, tmp_path: Path) -> None:
        _write(tmp_path, script="# no _DENIED_DEPENDENCIES literal here\n")
        failed = _run(tmp_path)
        assert any("could not parse _DENIED_DEPENDENCIES" in f for f in failed)

    def test_empty_denylist_fails_loudly(self, tmp_path: Path) -> None:
        _write(tmp_path, script='_DENIED_DEPENDENCIES=""\n')
        failed = _run(tmp_path)
        assert any("_DENIED_DEPENDENCIES parsed empty" in f for f in failed)

    def test_missing_script_fails(self, tmp_path: Path) -> None:
        (tmp_path / ".github").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".github" / "dependabot.yml").write_text(_VALID_CONFIG, encoding="utf-8")
        failed = _run(tmp_path)
        assert any("denylist script unreadable" in f for f in failed)

    def test_missing_config_fails(self, tmp_path: Path) -> None:
        script_path = tmp_path / "scripts" / "ci" / "dependabot_auto_merge.sh"
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(_VALID_SCRIPT, encoding="utf-8")
        failed = _run(tmp_path)
        assert any("dependabot config unreadable" in f for f in failed)


class TestAccountingDeclaration:
    """Decision 170: a new check must declare examined()/skipped() on every reachable exit path."""

    def test_examined_is_declared_on_the_pass_path(self, tmp_path: Path) -> None:
        _write(tmp_path)
        with patch(f"{_MODULE}._common.ROOT", tmp_path), registry.outcome_scope("validate_dependabot_config"):
            failed: list[str] = []
            validate_dependabot_config(failed)
        declaration = registry.pop_declaration()
        assert declaration is not None
        assert declaration.kind == "examined"
        assert declaration.count >= 1
        assert declaration.unit == "dependabot_pip_groups"

    def test_examined_is_declared_when_the_denylist_is_unparseable(self, tmp_path: Path) -> None:
        _write(tmp_path, script="# nothing to parse\n")
        with patch(f"{_MODULE}._common.ROOT", tmp_path), registry.outcome_scope("validate_dependabot_config"):
            failed: list[str] = []
            validate_dependabot_config(failed)
        declaration = registry.pop_declaration()
        assert declaration is not None
        assert declaration.kind == "examined"
        assert declaration.count == 0


class TestTaxonomyRegistration:
    def test_taxonomy_row_exists(self) -> None:
        taxonomy = yaml.safe_load(_TAXONOMY_PATH.read_text(encoding="utf-8"))
        assert taxonomy["function_to_category"][_CHECK] == "code_regression"
