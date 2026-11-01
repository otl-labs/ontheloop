"""Mirror tests for scripts/checks/contracts/validate_project_configuration.py (Decision 224).

A fixture copies the real contract and its cross-reference inputs into a tmp root and touches
every path the rows name; each negative case mutates one field and asserts the check fails with
a finding that names the defect, modelled on tests/checks/contracts/test_validate_ports_contract.py.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from scripts.checks import registry
from scripts.checks.contracts.validate_project_configuration import validate_project_configuration

_REPO = Path(__file__).resolve().parents[3]
_CONTRACT = "docs/contracts/project-configuration.yaml"
_COPIED = (
    _CONTRACT,
    "docs/contracts/ports.yaml",
    "docs/contracts/inference-provider.yaml",
    "docs/ROADMAP-PLATFORM.yaml",
    "docs/ROADMAP-PLATFORM-ARCHIVE.yaml",
    "docs/DECISIONS.md",
    "docs/DECISIONS_ARCHIVE.md",
    "scripts/CLAUDE.md",
)
_TOUCHED = (
    ".github/actions/aws-region/action.yml",
    "terraform/bootstrap/CLAUDE.md",
    "terraform/github/CLAUDE.md",
    "src/common/config.py",
    "scripts/aws_profile.py",
    "src/common/ducklake_reader_client.py",
    "src/turn_capture/transcript.py",
    "scripts/telemetry_capture/runner.py",
    ".claude/settings.json",
    "config/aws-region",
)


def _touch(root: Path, rel: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    for rel in _COPIED:
        src = _REPO / rel
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, tmp_path / rel)
    for rel in _TOUCHED:
        _touch(tmp_path, rel)
    return tmp_path


def _mutate(root: Path, fn: Callable[[dict[str, Any]], None]) -> None:
    path = root / _CONTRACT
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _row(data: dict[str, Any], row_id: str) -> dict[str, Any]:
    return next(s for s in data["settings"] if s["id"] == row_id)


def _expect_failure(root: Path, capsys: pytest.CaptureFixture[str], needle: str) -> None:
    failed: list[str] = []
    validate_project_configuration(failed, root=root)
    assert failed == ["Project configuration contract"]
    assert needle in capsys.readouterr().out


def test_real_contract_passes() -> None:
    failed: list[str] = []
    validate_project_configuration(failed)
    assert failed == []
    declaration = registry._CURRENT_DECLARATION
    settings = yaml.safe_load((_REPO / _CONTRACT).read_text(encoding="utf-8"))["settings"]
    assert declaration is not None and declaration.count == len(settings)


def test_missing_contract_file_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _expect_failure(tmp_path, capsys, "project-configuration.yaml")


def test_rejects_blank_basis(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _row(d, "clouds.aws.profile").update(basis=" "))
    _expect_failure(root, capsys, "basis is blank")


# (a) duplicate row ids
def test_rejects_duplicate_setting_ids(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: d["settings"].append(dict(d["settings"][0])))
    _expect_failure(root, capsys, "duplicate setting ids")


# (b) kind must be a key of kinds
def test_rejects_unknown_kind(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _row(d, "clouds.aws.profile").update(kind="exotic"))
    _expect_failure(root, capsys, "is not a key of kinds")


# (c) the defining rule: a secret or private row may never name a tracked or in-tree home.
def test_rejects_secret_row_in_tracked_home(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _row(d, "ports.change_surface.github.token")["homes"].update(local="platform_file"))
    _expect_failure(root, capsys, "secret/private row names tracked/in-tree home")


def test_rejects_undeclared_home_in_kind_homes(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: d["kind_homes"]["machine"]["local"].append("nonexistent_home"))
    _expect_failure(root, capsys, "names undeclared home")


def test_rejects_kind_homes_secret_naming_tracked_home(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: d["kind_homes"]["secret"]["local"].append("clone_file"))
    _expect_failure(root, capsys, "kind_homes.secret.local names tracked/in-tree home")


def test_rejects_home_not_allowed_for_kind(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _row(d, "clouds.aws.profile")["homes"].update(local="platform_file"))
    _expect_failure(root, capsys, "is not allowed for kind")


def test_rejects_row_home_not_a_declared_home(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _row(d, "clouds.aws.profile")["homes"].update(local="nonexistent_home"))
    _expect_failure(root, capsys, "is not a declared home")


def _add_vendor_store_without_citation(d: dict[str, Any]) -> None:
    _row(d, "ports.run_persona.litellm.api_key")["homes"].update(local=["user_env_file", "vendor_store"])
    if "vendor_store" not in d["kind_homes"]["secret"]["local"]:
        d["kind_homes"]["secret"]["local"].append("vendor_store")


def test_rejects_vendor_store_home_without_decision_206_citation(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, _add_vendor_store_without_citation)
    _expect_failure(root, capsys, "needs 'Decision 206 clause 2' cited in basis")


# (d) applies_when shape and the declared port/adapter pair
def test_rejects_undeclared_port_adapter_pair(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(
        root,
        lambda d: _row(d, "ports.run_persona.litellm.model").update(applies_when={"port": "run_persona", "adapter": "gemini"}),
    )
    _expect_failure(root, capsys, "does not name a declared port/adapter pair")


def test_rejects_malformed_applies_when(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _row(d, "clouds.aws.region").update(applies_when={"weird": "shape"}))
    _expect_failure(root, capsys, "is neither always, {port, adapter} nor {cloud}")


def test_rejects_undeclared_cloud_in_applies_when(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _row(d, "clouds.aws.region").update(applies_when={"cloud": "gcp"}, id="clouds.gcp.region"))
    _expect_failure(root, capsys, "applies_when cloud 'gcp' is not declared")


def test_rejects_port_adapter_id_on_always_row(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _row(d, "ports.<port>.adapter").update(id="ports.foo.bar.adapter"))
    _expect_failure(root, capsys, "needs applies_when port/adapter, not always")


# (k) a row id must fit its applies_when
def test_rejects_id_applies_when_mismatch(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(
        root,
        lambda d: _row(d, "ports.run_persona.litellm.model").update(
            applies_when={"port": "run_persona", "adapter": "claude_cli"}
        ),
    )
    _expect_failure(root, capsys, "id does not match its applies_when port/adapter")


def test_rejects_cloud_id_applies_when_mismatch(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _row(d, "clouds.aws.region").update(id="clouds.gcp.region"))
    _expect_failure(root, capsys, "id does not match its applies_when cloud")


# (e) variables and exports
def test_rejects_secret_row_with_null_variable(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _row(d, "ports.change_surface.github.token").update(variable=None))
    _expect_failure(root, capsys, "needs a non-null variable")


def test_rejects_secret_row_with_non_null_default(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _row(d, "ports.change_surface.github.token")["defaults"].update(local="x"))
    _expect_failure(root, capsys, "needs null defaults")


def test_rejects_project_row_with_malformed_override_variable(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _row(d, "clouds.aws.region").update(variable="region"))
    _expect_failure(root, capsys, "does not match")


def test_rejects_malformed_export(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _row(d, "clouds.aws.region").update(exports=["aws_region"]))
    _expect_failure(root, capsys, "export 'aws_region' does not match")


# (f) readers
def test_rejects_reader_that_does_not_resolve(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _row(d, "clouds.aws.region").update(readers=["nowhere/missing.py"]))
    _expect_failure(root, capsys, "is neither an existing path nor a resolvable owning item")


# (g) this_repository shape
def test_rejects_this_repository_owning_item_that_does_not_resolve(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _row(d, "clouds.aws.region")["this_repository"].update(owning_item="T9.99:c1"))
    _expect_failure(root, capsys, "does not resolve")


def test_rejects_resolved_row_current_home_mismatch(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(
        root,
        lambda d: _row(d, "clouds.aws.region")["this_repository"].update(status="resolved", current_home="wrong/path.yaml"),
    )
    _expect_failure(root, capsys, "does not match its local home")


def test_rejects_resolved_secret_row_with_non_null_current_home(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(
        root,
        lambda d: _row(d, "ports.change_surface.github.token").update(
            this_repository={"current_home": "somewhere.yaml", "status": "resolved", "owning_item": "T4.24:c16"}
        ),
    )
    _expect_failure(root, capsys, "resolved secret/private row needs a null current_home")


def test_rejects_accepted_this_repository_without_dec_owner(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(
        root,
        lambda d: _row(d, "ports.run_persona.litellm.model")["this_repository"].update(status="accepted", owning_item="T4.2"),
    )
    _expect_failure(root, capsys, "needs a dec-NNN owning_item")


def test_rejects_this_repository_null_current_home_without_note(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _row(d, "ports.<port>.adapter")["this_repository"].update(note=None))
    _expect_failure(root, capsys, "current_home null needs a non-empty note")


# (h) enforcement
def test_rejects_unknown_enforcement(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _row(d, "clouds.aws.profile").update(enforcement="validate_nonexistent_check"))
    _expect_failure(root, capsys, "is neither 'advisory' nor a registered check")


# (i) semantics keys
def test_rejects_semantics_key_naming_no_field(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: d["semantics"].update(surprise_field="x"))
    _expect_failure(root, capsys, "'surprise_field' names no field")


# (j) ambient trigger
def test_rejects_ambient_trigger_without_contract_pointer(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    claude_md = root / "scripts/CLAUDE.md"
    claude_md.write_text(
        claude_md.read_text(encoding="utf-8").replace("docs/contracts/project-configuration.yaml", "docs/contracts/gone.yaml"),
        encoding="utf-8",
    )
    _expect_failure(root, capsys, "no longer names docs/contracts/project-configuration.yaml")


def test_rejects_unknown_top_level_key(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: d.update(surprise=1))
    _expect_failure(root, capsys, "surprise")


def test_failing_contract_appends_check_label(root: Path) -> None:
    _mutate(root, lambda d: d.update(surprise=1))
    failed: list[str] = []
    validate_project_configuration(failed, root=root)
    assert failed == ["Project configuration contract"]


def test_entry_resolves_and_is_sequenced() -> None:
    assert registry.resolve("validate_project_configuration") is validate_project_configuration
    assert "validate_project_configuration" in {s.name for s in registry.pre_sequence()}
    assert "validate_project_configuration" in {s.name for s in registry.full_sequence()}
