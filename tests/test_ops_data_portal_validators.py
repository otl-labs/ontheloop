"""Unit tests for the portal's derived row-rule pre-check (gate_write), its producer-side rules and compute_automatable."""

from __future__ import annotations

import copy
import logging
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from scripts import contract_rules
from scripts.ops_data_portal import compute_automatable, file_rec, update_rec
from scripts.ops_portal import write_validators as _write_validators
from scripts.ops_portal.write_validators import RowRuleRejected, gate_write
from scripts.ops_portal.writer_transport import _project_ops_record
from src.common import ducklake_runtime as rt
from src.common import ducklake_scd2_schema as scd2
from src.common.ducklake_write_rules import RowRuleViolationError, rules_for
from src.row_rules.rules import RowRules
from tests.fixtures.ducklake_fakes import FakeCon, ops_rec_fields
from tests.fixtures.ops_portal_records import VALID_DECISION_FIELDS
from tests.fixtures.ops_rec_rows import OLDER_THAN_EVERY_RULE_DATE, reader_rec_row, writer_stand_in
from tests.fixtures.portal_removal_ledger import LEDGER

_TABLES = ("ops_recommendations", "ops_decisions")
_REF = "src.common.ducklake_reader_client.make_reader"


def _clean(table: str) -> dict[str, Any]:
    return ops_rec_fields() if table == "ops_recommendations" else {"id": "dec-001", **VALID_DECISION_FIELDS}


def _violating_value(rules: Any, kind: str, column: str) -> Any:
    if kind == "not_null":
        return None
    if kind == "accepted_values":
        return "~not-a-member~"
    if kind == "min_length":
        return ""
    if kind == "array_element_format":
        return ["~bad~"]
    return next(v for v in ("/abs", "\\", "~!~", "Z Z") if rules.patterns[column].fullmatch(v) is None)


def _mutations(spec: Any, rules: Any) -> list[tuple[str, str, Any]]:
    """(kind, column, violating value) for every rule kind the table declares on a caller-supplied input column."""
    for kind in ("pattern_when", "required_when", "exactly_one_of", "null_or_zero_when", "max_after_write_seconds"):
        assert not getattr(rules, kind), f"{kind} is declared: extend _violating_value"
    for column in (*rules.not_before, *rules.at_most):
        assert spec.fields[column]["role"] == "derived", f"{column}: a two-column rule on an input needs a mutation"
    out: list[tuple[str, str, Any]] = []
    for kind, columns in (
        ("not_null", rules.not_null),
        ("accepted_values", rules.accepted_values),
        ("pattern", rules.patterns),
        ("min_length", rules.min_length),
        ("array_element_format", rules.element_formats),
    ):
        out.extend(
            (kind, c, _violating_value(rules, kind, c))
            for c in sorted(columns)
            if spec.fields[c].get("role") == "input" and c != spec.merge_key
        )
    return out


def _stored(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "created_timestamp": datetime.fromisoformat(row["created_timestamp"])}


def _writer_verdict(table: str, record: dict[str, Any], *, prior: dict[str, Any] | None = None) -> Exception | None:
    """The writer's own verdict, driven through its primitives over the FakeCon connection double."""
    record = _project_ops_record(table, record)
    try:
        if prior is not None or table != "ops_recommendations":
            lookup = [] if prior is None else [_stored(prior)]
            rt.write_scd2(
                FakeCon(created_lookup=lookup), record, table=table, require_exists=prior is not None, sleep=lambda s: None
            )
        else:
            rt.file_scd2(FakeCon(), record, table=table)
    except rt.SchemaGateError as exc:
        return exc
    except rt.DuckLakeRuntimeError as exc:
        if "entity counter" in str(exc):
            return None  # past the schema gate and the row rules: the fake catalog has no entity counter
        raise
    return None


def _gate_verdict(
    table: str, record: dict[str, Any], *, insert: bool, existing: dict[str, Any] | None = None
) -> Exception | None:
    try:
        gate_write(table, record, insert=insert, existing=existing)
    except ValueError as exc:
        return exc
    return None


def test_portal_gate_is_the_writer_gate() -> None:
    """rec-4158's acceptance: for every rule kind the tables declare, a mutation the writer refuses is refused by the gate
    naming the same rule and column, and a rule-clean record passes both."""
    for table in _TABLES:
        spec = rt.resolve_table_spec(table)
        rules = rules_for(spec)
        insert = table == "ops_recommendations"
        assert _writer_verdict(table, _clean(table)) is None and _gate_verdict(table, _clean(table), insert=insert) is None
        mutations = _mutations(spec, rules)
        assert {k for k, _, _ in mutations} >= (
            {"not_null", "accepted_values", "pattern", "min_length"} if insert else {"not_null"}
        )
        for kind, column, value in mutations:
            record = {**_clean(table), column: value}
            refused, gated = _writer_verdict(table, record), _gate_verdict(table, record, insert=insert)
            assert refused is not None, (table, kind, column)
            if isinstance(refused, RowRuleViolationError):
                assert isinstance(gated, RowRuleRejected), (table, kind, column, gated)
                assert (gated.rule, gated.column) == (refused.rule, refused.column)
            else:
                assert gated is not None and not isinstance(gated, RowRuleRejected) and str(gated) == str(refused), (
                    table,
                    kind,
                    column,
                )


def test_update_gate_is_never_stricter_than_the_writer() -> None:
    """An older row violating dated legs on untouched columns passes the gate and the writer alike; every undated kind
    refuses on both."""
    older = reader_rec_row(
        created_timestamp=OLDER_THAN_EVERY_RULE_DATE, title="short", context="too short", tags=["Bad_Tag"], file="/abs/old.py"
    )
    merged = {**older, "priority": "High"}
    assert _gate_verdict("ops_recommendations", merged, insert=False, existing=older) is None
    assert _writer_verdict("ops_recommendations", merged, prior=older) is None
    stand_in = writer_stand_in([older])
    with (
        patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(older)),
        patch("scripts.ops_data_portal._ducklake_write", stand_in),
    ):
        assert update_rec(older["id"], {"priority": "High"}) is True
    assert len(stand_in.writes) == 1
    for table in _TABLES:
        spec = rt.resolve_table_spec(table)
        undated = RowRules.from_projection(_write_validators._undated_projection(spec.fields))
        mutations = _mutations(spec, undated)
        assert mutations, table
        for kind, column, value in mutations:
            base = reader_rec_row() if table == "ops_recommendations" else _clean(table)
            record = {**base, column: value}
            prior = base if table == "ops_recommendations" else None
            assert _gate_verdict(table, record, insert=False, existing=prior) is not None, (table, kind, column)
            assert _writer_verdict(table, record, prior=prior) is not None, (table, kind, column)


@pytest.fixture
def clean_producer_cache():
    _write_validators._producer_params.cache_clear()
    yield
    _write_validators._producer_params.cache_clear()


def test_producer_side_rules_follow_the_contract_exemptions(tmp_path: Path, clean_producer_cache) -> None:
    """Acceptance lint and dependency existence run from the contract's exemptions, with params from the declared leg."""
    lone_grep = {**ops_rec_fields(), "acceptance": "grep -q ops_data_portal scripts/ops_data_portal.py"}
    with pytest.raises(ValueError, match="does not discriminate"):
        gate_write("ops_recommendations", lone_grep, insert=True)

    contracts = tmp_path / "contracts"
    shutil.copytree(Path("docs/contracts"), contracts)
    target = contracts / "ops_recommendations.yaml"
    text = target.read_text(encoding="utf-8")
    assert "acceptance_lint: {require_discrimination: true}" in text
    target.write_text(
        text.replace("acceptance_lint: {require_discrimination: true}", "acceptance_lint: {require_discrimination: false}")
    )
    real = contract_rules.declared_rules
    _write_validators._producer_params.cache_clear()
    with patch.object(contract_rules, "declared_rules", lambda table: real(table, contracts_dir=contracts)):
        gate_write("ops_recommendations", lone_grep, insert=True)
    _write_validators._producer_params.cache_clear()

    with patch.object(
        _write_validators,
        "_producer_handlers",
        return_value={"acceptance_lint": lambda *a: None, "glossary_lint": lambda *a: None},
    ):
        with pytest.raises(ValueError, match="array_element_reference.*fails closed"):
            gate_write("ops_recommendations", ops_rec_fields(), insert=True)

    existing = reader_rec_row(dependencies=["rec-7"])
    with patch(_REF, side_effect=AssertionError("an unchanged dependencies column must not touch the reader")):
        gate_write("ops_recommendations", {**existing, "priority": "High"}, insert=False, existing=existing)
        with pytest.raises(ValueError, match="does not discriminate"):
            gate_write("ops_recommendations", {**existing, "acceptance": "grep -q x f"}, insert=False, existing=existing)
    _write_validators._rec_exists_memo.clear()
    absent = MagicMock()
    absent.named.return_value = []
    with patch(_REF, return_value=absent), pytest.raises(ValueError, match="rec-8"):
        gate_write("ops_recommendations", {**existing, "dependencies": ["rec-8"]}, insert=False, existing=existing)
        raise AssertionError("unreachable")
    with patch(_REF, return_value=absent), pytest.raises(ValueError, match="rec-7"):
        gate_write("ops_recommendations", {**existing}, insert=False, existing=None)
    _write_validators._rec_exists_memo.clear()


def _refusal(record: dict[str, Any], **kwargs: Any) -> str:
    with pytest.raises(ValueError, match="unqualified") as exc:
        gate_write("ops_recommendations", record, **kwargs)
    return str(exc.value)


def test_glossary_lint_refuses_unqualified_title() -> None:
    message = _refusal({**ops_rec_fields(), "title": "Fix the platform runner for retries"}, insert=True)
    assert message.startswith('title: unqualified "platform" -- say instead: On The Loop, core')
    assert "runner for retries" not in message
    assert "context_v2_json" not in message


def test_glossary_lint_update_judges_only_new_sentences() -> None:
    stored = reader_rec_row(context="The platform runner stalls. " + "x" * 90)
    kept = {**stored, "context": stored["context"] + " Retry with a bounded delay."}
    gate_write("ops_recommendations", kept, insert=False, existing=stored)
    added = {**stored, "context": stored["context"] + " The product hangs too."}
    assert 'unqualified "product"' in _refusal(added, insert=False, existing=stored)


def test_glossary_lint_fails_closed_without_contract(tmp_path: Path) -> None:
    _write_validators._load_glossary.cache_clear()
    with patch.object(_write_validators, "_GLOSSARY_PATH", tmp_path / "absent.yaml"):
        with pytest.raises(ValueError, match="failing closed"):
            gate_write("ops_recommendations", ops_rec_fields(), insert=True)
    _write_validators._load_glossary.cache_clear()


def test_glossary_lint_covers_verification_not_resolution() -> None:
    assert 'verification: unqualified "platform"' in _refusal(
        {**ops_rec_fields(), "verification": "Check the platform runner."}, insert=True
    )
    gate_write("ops_recommendations", {**ops_rec_fields(), "resolution": "Fixed the platform runner."}, insert=True)


def test_glossary_lint_context_message_names_derivation() -> None:
    long_context = "The platform runner stalls on retry. " * 4
    assert "context_v2_json" in _refusal({**ops_rec_fields(), "context": long_context}, insert=True)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("title", 'Fix the "platform" wording in the runner'),
        ("title", "Fix agent-platform-data-lake access"),
        ("verification", None),
    ],
)
def test_glossary_lint_passes_quotes_identifiers_and_none(column: str, value: str | None) -> None:
    gate_write("ops_recommendations", {**ops_rec_fields(), column: value}, insert=True)


# ---------------------------------------------------------------------------
# compute_automatable
# ---------------------------------------------------------------------------


def test_compute_automatable_boundary_file():
    """Files matching a boundary pattern return False regardless of risk score."""
    # "scripts/executor/" is in the boundary_patterns of config/agent/executor/capabilities.yaml
    result = compute_automatable("scripts/executor/some_tool.py", "S")
    assert result is False


def test_compute_automatable_high_risk_score():
    """R > maturity_ceiling returns False.

    compute_automatable moved to scripts/ops_portal/risk_scoring.py, which calls
    _compute_risk_score as its own module-local sibling -- the patch target is that
    module, not the facade re-export (Decision 124 namespace migration).
    """
    with patch("scripts.ops_portal.risk_scoring._compute_risk_score", return_value=999.0):
        result = compute_automatable("scripts/some_new_module.py", "M")
    assert result is False


def test_compute_automatable_valid():
    """Normal file with R <= maturity_ceiling returns True."""
    with patch("scripts.ops_portal.risk_scoring._compute_risk_score", return_value=0.5):
        result = compute_automatable("scripts/some_new_module.py", "XS")
    assert result is True


@pytest.mark.parametrize(
    "file_path",
    [
        "src/executor_loop/rendered/executor_loop.asl.json",
        "docs/contracts/personas/plan_agent.yaml",
        "docs/contracts/executor-loop-policy.yaml",
        "config/agent/executor/personas.yaml",
        ".github/CODEOWNERS",
        "scripts/ci/executor_boundary_guard.py",
        "scripts/checks/executor/validate_executor_boundary.py",
    ],
)
def test_compute_automatable_executor_loop_kernel_path_is_never_automatable(file_path):
    """The executor loop kernel, its rendered ASL, its contracts and its own boundary-guard
    implementation (Decision 185 clause 3) never derive automatable. The risk score is patched
    to 0.0 so a boundary_patterns hit is the ONLY path to False -- an unpatched score would
    already be low for a tiny new file, which would pass vacuously without exercising the
    boundary check at all."""
    with patch("scripts.ops_portal.risk_scoring._compute_risk_score", return_value=0.0):
        result = compute_automatable(file_path, "XS")
    assert result is False


# ---------------------------------------------------------------------------
# automatable override warning in file_rec
# ---------------------------------------------------------------------------


def test_automatable_override_warning(caplog):
    """Caller-supplied automatable=True overridden to False by formula; WARNING emitted."""
    long_context = "This recommendation exists because the system needs improvement to handle edge cases."
    fields = {
        "title": "Test boundary recommendation",
        "file": "scripts/executor/some_tool.py",  # boundary file -> compute_automatable returns False
        "context": long_context,
        "acceptance": "grep -q 'pattern' scripts/executor/some_tool.py && grep -q 'other' scripts/executor/some_tool.py",
        "effort": "S",
        "priority": "Low",
        "source": "manual",
        "status": "open",
        "automatable": True,  # caller supplies True; formula will derive False (boundary file)
    }
    with (
        patch("scripts.ops_data_portal.validate_source"),
        patch("scripts.ops_data_portal.Recommendation.model_validate"),
        patch("scripts.ops_data_portal._ducklake_write", return_value={"key": "rec-999"}),
        patch("scripts.ops_data_portal._append_to_local_jsonl"),
        patch("scripts.ops_data_portal._sync_table"),
        caplog.at_level(logging.WARNING, logger="scripts.ops_data_portal"),
    ):
        file_rec(fields)

    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("automatable" in str(m) for m in warning_msgs), (
        f"Expected automatable override WARNING in logs; got: {warning_msgs}"
    )


def test_array_element_format_rejects_malformed_element():
    """A malformed dependencies element is refused by the gate and through file_rec; a valid list and None pass."""
    with pytest.raises(ValueError, match="dependencies"):
        gate_write("ops_recommendations", {**ops_rec_fields(), "dependencies": ["rec-1", "nonsense"]}, insert=True)
    gate_write("ops_recommendations", {**ops_rec_fields(), "dependencies": None}, insert=True)
    fields = {**ops_rec_fields(), "dependencies": ["rec-1", "nonsense"]}
    with patch("scripts.ops_data_portal.validate_source"):
        with pytest.raises(ValueError, match="dependencies"):
            file_rec(fields)


def _deps(value: object) -> dict[str, Any]:
    return {**ops_rec_fields(), "dependencies": value}


def test_array_element_reference_rejects_absent_rec():
    """Existence resolves against the FULL corpus via the gate's own make_reader/rec_by_id call; a well-formed-but-absent
    id is rejected while a present id is accepted."""
    _write_validators._rec_exists_memo.clear()
    fake_reader = MagicMock()
    fake_reader.named.side_effect = lambda verb, id: [{"id": id}] if id == "rec-1" else []  # noqa: A006
    with patch(_REF, return_value=fake_reader):
        with pytest.raises(ValueError, match="rec-999999"):
            gate_write("ops_recommendations", _deps(["rec-1", "rec-999999"]), insert=True)
        gate_write("ops_recommendations", _deps(["rec-1"]), insert=True)
        gate_write("ops_recommendations", _deps(None), insert=True)
    _write_validators._rec_exists_memo.clear()


def test_array_element_reference_loud_fails_on_unreachable_reader():
    """An unreachable reader propagates (Decision 55 / 84 I-4) rather than admitting the write."""
    _write_validators._rec_exists_memo.clear()
    with (
        patch(_REF, side_effect=RuntimeError("reader unreachable")),
        pytest.raises(RuntimeError, match="unreachable"),
    ):
        gate_write("ops_recommendations", _deps(["rec-1"]), insert=True)
    _write_validators._rec_exists_memo.clear()


def test_array_element_reference_memoises_positive_results_only():
    """Positive existence results are memoised (bounding repeated Neon egress, Decision 88); an absent verdict is never
    cached, so a later-filed id is still accepted."""
    _write_validators._rec_exists_memo.clear()
    calls: list[str] = []

    def _fake_named(verb, id):  # noqa: A002
        calls.append(id)
        return [{"id": id}]

    fake_reader = MagicMock()
    fake_reader.named.side_effect = _fake_named
    with patch(_REF, return_value=fake_reader):
        assert _write_validators._rec_exists("rec-1") is True
        assert _write_validators._rec_exists("rec-1") is True
    assert calls == ["rec-1"], "second call for the same id must be served from the positive memo, not re-fetched"
    _write_validators._rec_exists_memo.clear()


def test_array_element_reference_holds_one_reader_per_write():
    """Decision 88 invariant (i): a write with N distinct not-yet-memoised elements constructs exactly ONE reader."""
    _write_validators._rec_exists_memo.clear()
    fake_reader = MagicMock()
    fake_reader.named.side_effect = lambda verb, id: [{"id": id}]  # noqa: A006
    with patch(_REF, return_value=fake_reader) as mock_make_reader:
        gate_write("ops_recommendations", _deps(["rec-10", "rec-11", "rec-12", "rec-13"]), insert=True)
    assert mock_make_reader.call_count == 1
    assert fake_reader.named.call_count == 4
    _write_validators._rec_exists_memo.clear()


def test_array_element_reference_skips_reader_when_all_elements_memoised():
    """When every element is already positively memoised, the gate never constructs a reader."""
    _write_validators._rec_exists_memo.clear()
    _write_validators._rec_exists_memo["rec-20"] = True
    _write_validators._rec_exists_memo["rec-21"] = True
    with patch(_REF) as mock_make_reader:
        gate_write("ops_recommendations", _deps(["rec-20", "rec-21"]), insert=True)
    mock_make_reader.assert_not_called()
    _write_validators._rec_exists_memo.clear()


# ---------------------------------------------------------------------------
# The removal ledger: every retired portal check maps to the contract leg that replaces it
# ---------------------------------------------------------------------------


def _without_leg(semantics: dict[str, Any], column: str, leg: str) -> dict[str, Any]:
    mutated = copy.deepcopy(semantics)
    field = mutated["ops_tables"]["ops_recommendations"]["columns"][column]
    if leg == "schema_gate":
        if field["nullable"] is False:
            field["nullable"] = True
        else:
            field["sql_type"] = "VARCHAR"
        return mutated
    if leg in ("acceptance_lint", "array_element_reference"):
        field["write_time_exemptions"].pop(leg)
        return mutated
    field.pop(leg)
    field.get("exclude_before", {}).pop(leg, None)
    if not field.get("exclude_before", True):
        field.pop("exclude_before")
    return mutated


_node_results: dict[str, int] = {}


def _node_passes(node: str) -> bool:
    if node not in _node_results:
        proc = subprocess.run([sys.executable, "-m", "pytest", node, "-q"], capture_output=True, text=True, check=False)
        _node_results[node] = proc.returncode
    return _node_results[node] == 0


@pytest.mark.parametrize("row", LEDGER, ids=lambda r: f"{r['column']}-{r['check'].replace(' ', '_')}")
def test_each_retired_validator_is_replaced_by_its_contract_leg(row, clean_producer_cache) -> None:
    column, leg = str(row["column"]), str(row["leg"])
    record = {**ops_rec_fields(), column: row["input"]}
    reader = MagicMock()
    reader.named.return_value = [] if leg == "array_element_reference" else [{"id": "present"}]
    _write_validators._rec_exists_memo.clear()
    with patch(_REF, return_value=reader):
        refused = _gate_verdict("ops_recommendations", record, insert=True)
        assert refused is not None, row
        if leg in ("acceptance_lint", "array_element_reference", "schema_gate"):
            assert not isinstance(refused, RowRuleRejected), row
        else:
            assert isinstance(refused, RowRuleRejected) and (refused.rule, refused.column) == (leg, column), (row, refused)
        with patch.object(scd2, "load_field_semantics", return_value=_without_leg(scd2.load_field_semantics(), column, leg)):
            assert _gate_verdict("ops_recommendations", record, insert=True) is None, f"{row}: still refused without its leg"
    spec_field = rt.resolve_table_spec("ops_recommendations").fields[column]
    dated = leg in spec_field.get("exclude_before", {})
    assert (row["update_path"] is not None) == dated, row
    for node in row["update_path"] or ():
        assert _node_passes(str(node)), node
    _write_validators._rec_exists_memo.clear()


# ---------------------------------------------------------------------------
# repair_dependency_tokens dry-run (PLAN-dependency-referential-integrity)
# ---------------------------------------------------------------------------


def test_repair_dependency_tokens_dry_run_writes_nothing():
    """The backfill helper enumerates malformed dependencies rows via current_state, reports the
    strip-bracket repair per row, and calls update_rec zero times under dry_run."""
    from scripts.ops_portal.maintenance_ops import repair_dependency_tokens

    rows = [
        {"id": "rec-100", "dependencies": ["[rec-1]"]},
        {"id": "rec-101", "dependencies": ["rec-2", "rec-3"]},  # already well-formed
        {"id": "rec-102", "dependencies": None},
        {"id": "rec-103", "dependencies": ["[rec-4", "rec-5]"]},  # split-pair shape
    ]
    fake_reader = MagicMock()
    fake_reader.current_state.return_value = rows

    with (
        patch("src.common.ducklake_reader_client.make_reader", return_value=fake_reader),
        patch("scripts.ops_data_portal.update_rec") as mock_update,
    ):
        result = repair_dependency_tokens(dry_run=True)

    mock_update.assert_not_called()
    assert result["repaired"] == 0
    assert result["matched"] == [
        {"id": "rec-100", "before": ["[rec-1]"], "after": ["rec-1"]},
        {"id": "rec-103", "before": ["[rec-4", "rec-5]"], "after": ["rec-4", "rec-5"]},
    ]
