"""Mirror tests for scripts/checks/contracts/validate_ports_contract.py (Decisions 211 and 212).

A fixture copies the real contract and its cross-reference inputs into a tmp root and touches every
path the contract names; each negative case mutates one field and asserts the check fails with a
finding that names the defect.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from scripts.checks import registry
from scripts.checks.contracts.validate_ports_contract import validate_ports_contract

_REPO = Path(__file__).resolve().parents[3]
_COPIED = (
    "docs/contracts/ports.yaml",
    "docs/contracts/inference-provider.yaml",
    "docs/ROADMAP-PLATFORM.yaml",
    "docs/ROADMAP-PLATFORM-ARCHIVE.yaml",
    "docs/DECISIONS.md",
    "docs/DECISIONS_ARCHIVE.md",
    "AGENTS.md",
)
_PORTS = "docs/contracts/ports.yaml"


def _touch(root: Path, rel: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    for rel in _COPIED:
        src = _REPO / rel
        if src.is_file():
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, tmp_path / rel)
    data = yaml.safe_load((tmp_path / _PORTS).read_text(encoding="utf-8"))
    for port in data["ports"]:
        for adapter in port["adapters"]:
            if adapter.get("location"):
                _touch(tmp_path, adapter["location"])
    for row in data["coupling_findings"] + data["structural_assumptions"]:
        if not row["location"].startswith("dec-"):
            _touch(tmp_path, row["location"])
    return tmp_path


def _mutate(root: Path, fn: Callable[[dict[str, Any]], None]) -> None:
    path = root / _PORTS
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _port(data: dict[str, Any], port_id: str) -> dict[str, Any]:
    return next(p for p in data["ports"] if p["id"] == port_id)


def _expect_failure(root: Path, capsys: pytest.CaptureFixture[str], needle: str) -> None:
    failed: list[str] = []
    validate_ports_contract(failed, root=root)
    assert failed == ["Ports contract"]
    assert needle in capsys.readouterr().out


def test_real_contract_passes() -> None:
    failed: list[str] = []
    validate_ports_contract(failed)
    assert failed == []
    declaration = registry._CURRENT_DECLARATION
    ports = yaml.safe_load((_REPO / _PORTS).read_text(encoding="utf-8"))["ports"]
    assert declaration is not None and declaration.count == sum(1 for _ in ports)


def test_rejects_unknown_adapter_tier(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _port(d, "ops_verbs")["adapters"][0].update(tier="beta"))
    _expect_failure(root, capsys, "tier")


def test_rejects_implemented_adapter_with_missing_location(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _port(d, "ops_verbs")["adapters"][1].update(location="nowhere/missing.py"))
    _expect_failure(root, capsys, "does not exist")


def test_rejects_pending_adapter_without_owning_item(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _port(d, "ops_verbs")["adapters"][0].pop("owning_item"))
    _expect_failure(root, capsys, "owning_item")


def test_rejects_owning_item_that_is_not_a_roadmap_item(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _port(d, "ops_verbs")["adapters"][0].update(owning_item="T9.99"))
    _expect_failure(root, capsys, "'T9.99' does not resolve")


def test_rejects_owning_item_naming_an_absent_criterion(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _port(d, "ops_verbs")["adapters"][0].update(owning_item="T4.24:c99"))
    _expect_failure(root, capsys, "'T4.24:c99' does not resolve")


def test_rejects_malformed_rec_owning_item(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _port(d, "ops_verbs")["adapters"][0].update(owning_item="rec-abc"))
    _expect_failure(root, capsys, "'rec-abc' does not resolve")


def test_rejects_dec_owning_item_without_decision_header(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: d["coupling_findings"][0].update(owning_item="dec-9999"))
    _expect_failure(root, capsys, "'dec-9999' does not resolve")


def test_rejects_adapter_oracle_not_among_adapters(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _port(d, "ops_verbs")["oracle"].update(ref="nonexistent"))
    _expect_failure(root, capsys, "not among the port's adapters")


def test_rejects_adapter_oracle_status_mismatch(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _port(d, "ops_verbs")["oracle"].update(status="present"))
    _expect_failure(root, capsys, "misstates adapter")


def test_rejects_harness_oracle_with_unresolvable_ref(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _port(d, "orchestration_host")["oracle"].update(ref="T9.99"))
    _expect_failure(root, capsys, "'T9.99' does not resolve")


def test_rejects_null_oracle_without_gap(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _port(d, "run_persona").pop("oracle_gap"))
    _expect_failure(root, capsys, "neither an oracle nor an oracle_gap")


def test_rejects_oracle_gap_beside_an_oracle(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _port(d, "ops_verbs").update(oracle_gap="a gap"))
    _expect_failure(root, capsys, "oracle_gap beside an oracle")


def test_rejects_present_suite_with_missing_path(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(
        root, lambda d: _port(d, "ops_verbs").update(conformance_suite={"status": "present", "path": "tests/absent_suite"})
    )
    _expect_failure(root, capsys, "suite path")


def test_rejects_coupling_finding_naming_undeclared_port(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: d["coupling_findings"][0].update(ports=["ops_verbs", "undeclared_port"]))
    _expect_failure(root, capsys, "'undeclared_port' is not a declared port")


def test_rejects_coupling_finding_with_single_port(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: d["coupling_findings"][0].update(ports=["ops_verbs"]))
    _expect_failure(root, capsys, "at least two distinct ports")


def test_rejects_finding_location_that_is_neither_path_nor_decision(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: d["coupling_findings"][0].update(location="nowhere/at/all.py"))
    _expect_failure(root, capsys, "neither an existing path nor a resolvable dec id")


def test_rejects_semantics_key_naming_no_field(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: d["semantics"].update(local_oracle="x"))
    _expect_failure(root, capsys, "'local_oracle' names no field")


def test_rejects_duplicate_port_ids(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: d["ports"].append(dict(d["ports"][0])))
    _expect_failure(root, capsys, "duplicate port ids")


def test_rejects_unknown_top_level_key(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: d.update(surprise=1))
    _expect_failure(root, capsys, "surprise")


def test_rejects_unknown_verbs_status(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(root, lambda d: _port(d, "ops_verbs").update(verbs_status="draft"))
    _expect_failure(root, capsys, "verbs_status")


def test_rejects_ambient_trigger_without_contract_pointer(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    agents = root / "AGENTS.md"
    agents.write_text(
        agents.read_text(encoding="utf-8").replace("docs/contracts/ports.yaml", "docs/contracts/gone.yaml"), encoding="utf-8"
    )
    _expect_failure(root, capsys, "no longer names docs/contracts/ports.yaml")


def test_rejects_run_persona_adapter_not_a_persona_backend(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _mutate(
        root,
        lambda d: _port(d, "run_persona")["adapters"].append(
            {"id": "gemini_cli", "tier": "experimental", "status": "pending", "owning_item": "T4.27"}
        ),
    )
    _expect_failure(root, capsys, "'gemini_cli' is not a persona_backend value")


def test_rejects_persona_backend_value_with_no_run_persona_adapter(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = root / "docs/contracts/inference-provider.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["vocabulary"]["axes"]["persona_backend"]["values"].append("gemini_cli")
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    _expect_failure(root, capsys, "'gemini_cli' has no run_persona adapter")


def test_accepts_structural_assumption_row(root: Path) -> None:
    _touch(root, "scripts/executor/example.py")
    _mutate(
        root,
        lambda d: d["structural_assumptions"].append(
            {
                "id": "SA-99",
                "assumption": "The loop reads docs/ROADMAP-PLATFORM.yaml at a fixed repo path.",
                "location": "scripts/executor/example.py",
                "status": "open",
                "owning_item": "T4.24",
            }
        ),
    )
    failed: list[str] = []
    validate_ports_contract(failed, root=root)
    assert failed == []


def test_failing_contract_appends_check_label(root: Path) -> None:
    _mutate(root, lambda d: d.update(surprise=1))
    failed: list[str] = []
    validate_ports_contract(failed, root=root)
    assert failed == ["Ports contract"]


_SCHEMA_DEFECTS: list[tuple[str, Callable[[dict[str, Any]], None], str]] = [
    ("adapter_id", lambda d: _port(d, "ops_verbs")["adapters"][0].update(id="Bad-Id"), "is not snake_case"),
    (
        "implemented_with_owner",
        lambda d: _port(d, "ops_verbs")["adapters"][1].update(owning_item="T4.23"),
        "needs location and no owning_item",
    ),
    ("present_suite_no_path", lambda d: _port(d, "ops_verbs").update(conformance_suite={"status": "present"}), "needs a path"),
    (
        "pending_suite_no_owner",
        lambda d: _port(d, "ops_verbs").update(conformance_suite={"status": "pending"}),
        "needs an owning_item",
    ),
    ("blank_oracle_ref", lambda d: _port(d, "ops_verbs")["oracle"].update(ref=" "), "oracle ref is blank"),
    ("port_id", lambda d: _port(d, "ops_verbs").update(id="Bad-Port"), "is not snake_case"),
    ("empty_verbs", lambda d: _port(d, "ops_verbs").update(required_verbs=[]), "non-empty and unique"),
    ("verb_case", lambda d: _port(d, "ops_verbs").update(required_verbs=["Bad-Verb"]), "non-snake_case verb"),
    ("no_adapters", lambda d: _port(d, "ops_verbs").update(adapters=[]), "non-empty with unique ids"),
    ("finding_id", lambda d: d["coupling_findings"][0].update(id="X-1"), "must match PC-NN"),
    ("finding_description", lambda d: d["coupling_findings"][0].update(description=" "), "blank description"),
    ("assumption_id", lambda d: d["structural_assumptions"].append(_assumption(id="bad")), "must match SA-NN"),
    ("assumption_blank", lambda d: d["structural_assumptions"].append(_assumption(assumption=" ")), "blank assumption"),
    ("no_ports", lambda d: d.update(ports=[], coupling_findings=[]), "ports must be non-empty"),
]


def _assumption(**overrides: str) -> dict[str, str]:
    row = {
        "id": "SA-01",
        "assumption": "A fixed repo path.",
        "location": "AGENTS.md",
        "status": "open",
        "owning_item": "T4.24",
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    ("mutation", "needle"), [(m, n) for _, m, n in _SCHEMA_DEFECTS], ids=[i for i, _, _ in _SCHEMA_DEFECTS]
)
def test_rejects_schema_defect(
    root: Path, capsys: pytest.CaptureFixture[str], mutation: Callable[[dict[str, Any]], None], needle: str
) -> None:
    _mutate(root, mutation)
    _expect_failure(root, capsys, needle)


def test_missing_contract_file_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _expect_failure(tmp_path, capsys, "ports.yaml")


def test_skips_persona_rule_without_run_persona_port(root: Path) -> None:
    _mutate(root, lambda d: d["ports"].remove(_port(d, "run_persona")))
    failed: list[str] = []
    validate_ports_contract(failed, root=root)
    assert failed == []
