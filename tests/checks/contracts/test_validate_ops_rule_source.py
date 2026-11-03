"""Mirror test for validate_ops_rule_source (rec-4158 plan A): the real tree passes, each defect fails on a fixture, the
ratchet keeps exclude_before dates one-way, and the portal validators agree with the contract's rules."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

import pytest
import yaml

from scripts.checks.contracts import _rule_grammar as grammar
from scripts.checks.contracts import validate_ops_rule_source as check_mod
from scripts.checks.contracts._manifest import ENTRIES
from scripts.contract_rules import declared_rules
from scripts.ops_portal.write_validators import gate_write
from tests.fixtures.ducklake_fakes import ops_rec_fields

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WRITER_TABLES = (
    "ops_recommendations",
    "ops_decisions",
    "ops_entity_counters",
    "ops_tenants",
    "ops_projects",
    "telemetry_sessions",
    "telemetry_observations",
    "telemetry_transcripts",
    "telemetry_agents",
)
_BASELINES = (
    "source-lineage",
    "session-id",
    "tenant-id",
    "project-id",
    "observation-id",
    "parent-observation-id",
    "telemetry-event-envelope",
)
_FILE_BACKED = ("exit-criteria-ledger", "verification-registry")
_FILES = (
    *(f"docs/contracts/{name}.yaml" for name in (*_WRITER_TABLES, *_BASELINES, *_FILE_BACKED)),
    "config/lambda/ducklake/field_semantics.yaml",
    "config/agent/data_quality/ops.yaml",
    "config/agent/data_quality/source_registry.yaml",
    "src/common/ducklake_scd2_schema.py",
    "scripts/executor/jsonl_store.py",
    "scripts/ops_portal/write_validators.py",
    check_mod.CHECK_REL,
)
_RECS = "docs/contracts/ops_recommendations.yaml"


def _make_root(tmp_path: Path, *, with_check: bool = True) -> Path:
    for rel in _FILES:
        if rel == check_mod.CHECK_REL and not with_check:
            continue
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(_REPO_ROOT / rel, dest)
    return tmp_path


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=root, check=True, capture_output=True)


def _git_root(tmp_path: Path, *, base_has_check: bool = True, base_mutation: Callable[[Path], None] | None = None) -> Path:
    root = _make_root(tmp_path)
    if base_mutation is not None:
        base_mutation(root)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    if not base_has_check:
        _git(root, "rm", "-q", "--cached", check_mod.CHECK_REL)
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    if base_mutation is not None:
        _make_root(root)
    return root


def _edit(root: Path, rel: str, change: Callable[[dict], None]) -> None:
    path = root / rel
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _edit_recs(root: Path, change: Callable[[dict], None]) -> None:
    _edit(root, _RECS, change)


def _run(root: Path) -> list[str]:
    failed: list[str] = []
    check_mod.validate_ops_rule_source(failed, root=root)
    return failed


def _fields(doc: dict) -> dict:
    return doc["fields"]


def test_real_tree_passes(capsys: pytest.CaptureFixture[str]) -> None:
    failed: list[str] = []
    check_mod.validate_ops_rule_source(failed)
    assert failed == []
    out = capsys.readouterr().out
    assert "PASS" in out
    fs = yaml.safe_load((_REPO_ROOT / "config/lambda/ducklake/field_semantics.yaml").read_text(encoding="utf-8"))
    derived = {t for t in fs["ops_tables"] if (_REPO_ROOT / "docs/contracts" / f"{t}.yaml").is_file()}
    assert set(_WRITER_TABLES) <= derived
    assert f"across {len(derived)} tables" in out


def _set(field: str, key: str, value) -> Callable[[dict], None]:
    def change(doc: dict) -> None:
        _fields(doc)[field]["dq_intent"][key] = value

    return change


def _bad_leg(field: str, kind: str, **sub) -> Callable[[dict], None]:
    def change(doc: dict) -> None:
        leg = _fields(doc)[field]["dq_intent"][kind]
        leg.update(sub)

    return change


def _drop_exemption(doc: dict) -> None:
    del _fields(doc)["acceptance"]["dq_intent"]["write_time_exemptions"]


def _exemption(field: str, kind: str, **values) -> Callable[[dict], None]:
    def change(doc: dict) -> None:
        _fields(doc)[field]["dq_intent"]["write_time_exemptions"][kind].update(values)

    return change


def _remove_key(field: str, *path: str) -> Callable[[dict], None]:
    def change(doc: dict) -> None:
        node = _fields(doc)[field]["dq_intent"]
        for step in path[:-1]:
            node = node[step]
        del node[path[-1]]

    return change


def _status_values(doc: dict) -> None:
    _fields(doc)["status"]["dq_intent"]["accepted_values"]["values"].append("in_progress")


def _stale_exemption(doc: dict) -> None:
    _fields(doc)["title"]["dq_intent"]["write_time_exemptions"] = {
        "pattern": {"class": "temporary", "reason": "r", "owner": "rec-1"}
    }


_DEFECTS: list[tuple[str, Callable[[dict], None], str]] = [
    ("vocabulary", _set("title", "mystery", 1), "outside the scd2 vocabulary"),
    ("subkey", _bad_leg("title", "min_length", bogus=1), "unknown sub-key"),
    ("lookaround", _set("id", "pattern", "^(?=a)b$"), "outside the RE2-and-Python subset"),
    ("backreference", _set("id", "pattern", r"^(a)\1$"), "backreference"),
    ("invalid-regex", _set("id", "pattern", "^(a$"), "not a valid regex"),
    ("contradictory-not-null", _set("ulid", "not_null", {"enforced": False}), "`enforced` key is retired"),
    ("nullable-false-without-not-null", _remove_key("ulid", "not_null"), "nullable false column without `not_null: true`"),
    ("floor", _bad_leg("title", "min_length", exclude_before="2026-04-01"), "earlier than the Decision 64 anchor"),
    ("non-iso-date", _bad_leg("title", "min_length", exclude_before="May 2026"), "not an ISO date"),
    ("impossible-date", _bad_leg("title", "min_length", exclude_before="2026-13-45"), "not a real calendar date"),
    ("producer-side-without-exemption", _drop_exemption, "needs a write_time_exemptions entry"),
    ("exemption-class", _exemption("acceptance", "acceptance_lint", **{"class": "whenever"}), "is not one of"),
    ("exemption-reason", _exemption("acceptance", "acceptance_lint", reason=" "), "needs a reason"),
    ("exemption-owner-missing", _exemption("acceptance", "acceptance_lint", owner=""), "needs an owner"),
    ("cross-row-owner", _exemption("dependencies", "array_element_reference", owner="someone"), "must be a rec id"),
    (
        "repository-state-owner",
        _exemption("acceptance", "acceptance_lint", owner="scripts/no_such_file.py"),
        "existing repository path",
    ),
    ("exemption-not-a-mapping", _set("title", "write_time_exemptions", {"min_length": "text"}), "must be a mapping"),
    ("exemption-for-undeclared-rule", _stale_exemption, "names a rule the field does not declare"),
    ("dq-accepted-values-superset", _bad_leg("effort", "accepted_values", values=["XS"]), "ops.yaml effort.accepted_values"),
    ("dq-not-null-stricter", _remove_key("risk", "not_null"), "ops.yaml risk.not_null"),
    ("status-vocabulary", _status_values, "status vocabulary"),
]


_PLANTED = (
    (
        "telemetry-contract",
        "telemetry_sessions",
        lambda d: d["fields"]["producer"]["dq_intent_local"].update(not_null={"enforced": True}),
    ),
    ("class-c-baseline", "session-id", lambda d: d["fields"]["session_id"]["dq_intent"].update(not_null={"enforced": False})),
    (
        "file-backed-contract",
        "exit-criteria-ledger",
        lambda d: next(iter(d["fields"].values()))["dq_intent"].update(not_null={"enforced": True}),
    ),
)


def test_each_defect_fails(tmp_path: Path) -> None:
    for name, change, message in _DEFECTS:
        root = _make_root(tmp_path / name)
        assert _run(root) == [], name
        _edit_recs(root, change)
        failed = _run(root)
        assert any(message in item for item in failed), (name, failed)
    for name, table, change in _PLANTED:
        root = _make_root(tmp_path / name)
        _edit(root, f"docs/contracts/{table}.yaml", change)
        failed = _run(root)
        assert any(f"{table}.yaml" in item and "`enforced` key is retired" in item for item in failed), (name, failed)


def test_unreadable_inputs_fail_closed(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    (root / "docs/contracts/ops_decisions.yaml").write_text("- not a mapping\n", encoding="utf-8")
    assert any("contract unreadable" in item for item in _run(root))
    root = _make_root(tmp_path / "b")
    (root / "scripts/executor/jsonl_store.py").write_text("_VALID_STATUSES = []\nx = (\n", encoding="utf-8")
    assert any("parity inputs unreadable" in item for item in _run(root))
    root = _make_root(tmp_path / "c")
    (root / "scripts/executor/jsonl_store.py").write_text("OTHER = 1\n", encoding="utf-8")
    failed = _run(root)
    assert any("could not read the jsonl_store._VALID_STATUSES" in item for item in failed)
    assert any("could not read the Recommendation status Literal" in item for item in failed)
    root = _make_root(tmp_path / "d")
    (root / "src/common/ducklake_scd2_schema.py").write_text("STATUS_TRANSITIONS = {}\n", encoding="utf-8")
    assert any("STATUS_TRANSITIONS enforced set" in item for item in _run(root))
    root = _make_root(tmp_path / "g")
    (root / "scripts/executor/jsonl_store.py").write_text("_VALID_STATUSES = frozenset(compute())\n", encoding="utf-8")
    assert any("could not read the jsonl_store._VALID_STATUSES" in item for item in _run(root))
    root = _make_root(tmp_path / "h")
    (root / "src/common/ducklake_scd2_schema.py").write_text("STATUS_TRANSITIONS = build()\n", encoding="utf-8")
    assert any("STATUS_TRANSITIONS enforced set" in item for item in _run(root))
    root = _make_root(tmp_path / "e")
    _edit(
        root,
        "config/agent/data_quality/ops.yaml",
        lambda d: d["tables"]["ops_recommendations"]["columns"]["title"]["tests"].append({"made_up": {"write_time": False}}),
    )
    assert any("`write_time` key remains" in item for item in _run(root))
    root = _make_root(tmp_path / "f")
    _edit(
        root,
        "config/agent/data_quality/ops.yaml",
        lambda d: d["tables"]["ops_recommendations"]["columns"].update(ghost={"tests": [{"not_null": {}}]}),
    )
    assert any("column the contract does not declare" in item for item in _run(root))


def test_unreadable_or_absent_base_legs(tmp_path: Path) -> None:
    def garble(root: Path) -> None:
        (root / "docs/contracts/ops_decisions.yaml").write_text("- not a mapping\n", encoding="utf-8")

    failed = _run(_git_root(tmp_path / "garbled", base_mutation=garble))
    assert any("ratchet: could not read the rule legs" in item for item in failed), failed

    def absent(root: Path) -> None:
        (root / "docs/contracts/ops_decisions.yaml").unlink()

    assert _run(_git_root(tmp_path / "absent", base_mutation=absent)) == []


def _later(doc: dict) -> None:
    _fields(doc)["title"]["dq_intent"]["min_length"]["exclude_before"] = "2026-10-01"


def _newly_dated(doc: dict) -> None:
    _fields(doc)["status"]["dq_intent"]["not_null"] = {"exclude_before": "2026-06-01"}


def _removed(doc: dict) -> None:
    del _fields(doc)["tags"]["dq_intent"]["array_element_format"]


def _earlier(doc: dict) -> None:
    _fields(doc)["tags"]["dq_intent"]["array_element_format"]["exclude_before"] = "2026-09-01"


def _earlier_with_census(doc: dict) -> None:
    _earlier(doc)
    _fields(doc)["tags"].setdefault("amendment_log", []).append(
        {
            "date": "2026-10-15",
            "semantic_break": True,
            "change_class": "prose_improvement",
            "migration_story": "tags date moved earlier; census: 0 violators (2026-10-15)",
        }
    )


def _date_deleted(doc: dict) -> None:
    del _fields(doc)["effort"]["dq_intent"]["accepted_values"]["exclude_before"]


def _newly_dated_new_leg(doc: dict) -> None:
    _fields(doc)["verification"]["dq_intent"]["not_null"] = {"exclude_before": "2026-10-04"}


def test_exclude_before_cannot_move_later(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _git_root(tmp_path / "armed")
    assert _run(root) == []
    _edit_recs(root, _earlier_with_census)
    assert _run(root) == []
    _edit_recs(root, _newly_dated_new_leg)
    assert _run(root) == []
    armed = _git_root(tmp_path / "armed-earlier")
    _edit_recs(armed, _earlier)
    assert any("ratchet: ops_recommendations.tags" in item and "moved earlier" in item for item in _run(armed))
    for change, message in (
        (_later, "moved later"),
        (_newly_dated, "moved later"),
        (_removed, "was removed"),
        (_date_deleted, "exclude_before was removed"),
    ):
        armed = _git_root(tmp_path / f"armed-{change.__name__}")
        _edit_recs(armed, change)
        failed = _run(armed)
        assert any("ratchet: ops_recommendations." in item and message in item for item in failed), (change.__name__, failed)
    # ops_decisions legs are ratcheted too: a deleted not_null leg is a removal
    decisions = _git_root(tmp_path / "decisions")
    _edit(decisions, "docs/contracts/ops_decisions.yaml", lambda d: d["fields"]["title"]["dq_intent"].pop("not_null"))
    assert any("ratchet: ops_decisions.title.not_null was removed" in item for item in _run(decisions))
    capsys.readouterr()
    dormant = _git_root(tmp_path / "dormant", base_has_check=False)
    _edit_recs(dormant, _later)
    assert _run(dormant) == []
    assert "ratchet dormant" in capsys.readouterr().out
    bare = _make_root(tmp_path / "bare")
    _edit_recs(bare, _later)
    assert _run(bare) == []
    assert "ratchet leg skipped: no merge-base with origin/main resolves" in capsys.readouterr().out


def test_portal_validators_agree_with_contract() -> None:
    """The portal's gate accepts a path exactly when the contract's file pattern matches, and a context exactly when its
    stripped length meets the contract's min_length (rec-4158: gate_write drives the writer's own rules)."""
    rules = {(r.column, r.kind): r for r in declared_rules("ops_recommendations")}
    pattern = rules[("file", "pattern")].params["value"]
    corpus = ["a.py", "a/b/c.py", "/abs", "C:/x", "c:\\x", "a\\b", "C:x", "C:", "1:/x", "", "..", "./x", "x:/", "é/ü"]

    def accepts(field: str, value: str) -> bool:
        try:
            gate_write("ops_recommendations", {**ops_rec_fields(), field: value}, insert=True)
        except ValueError:
            return False
        return True

    for path in corpus:
        if path:
            assert (re.fullmatch(pattern, path) is not None) == accepts("file", path), path
    minimum = rules[("context", "min_length")].params["value"]
    for text in (
        "x" * minimum,
        "x" * (minimum - 1),
        " " * 5 + "x" * minimum + " " * 5,
        " " * 200,
        "\u3000" + "y" * (minimum - 1),
    ):
        assert accepts("context", text) == (len(text.strip()) >= minimum), repr(text)


def test_registered_in_the_contracts_manifest() -> None:
    entry = next(e for e in ENTRIES if e.name == "validate_ops_rule_source")
    assert (entry.module, entry.attr, entry.pre, entry.full_segment) == (
        "scripts.checks.contracts.validate_ops_rule_source",
        "validate_ops_rule_source",
        True,
        "full_after_lint",
    )
    globs = set(entry.pre_globs or ())
    for required in (
        "docs/contracts/ops_recommendations.yaml",
        "docs/contracts/ops_decisions.yaml",
        "docs/contracts/source-lineage.yaml",
        "docs/contracts/*.yaml",
        "config/lambda/ducklake/field_semantics.yaml",
        "config/agent/data_quality/ops.yaml",
        "config/agent/data_quality/source_registry.yaml",
        "scripts/contract_rules.py",
        "scripts/executor/jsonl_store.py",
        "src/common/ducklake_scd2_schema.py",
        "scripts/ops_portal/write_validators.py",
        "scripts/checks/contracts/**",
        "scripts/checks/_common.py",
        "scripts/checks/registry.py",
    ):
        assert required in globs, required


def test_regex_subset_helper_scans_escapes_and_classes() -> None:
    assert grammar.regex_subset_error(r"^[^\\]*$") is None
    assert grammar.regex_subset_error(r"^a\\1$") is None
    assert "backreference" in grammar.regex_subset_error(r"^(a)\1$")
    assert "lookaround" in grammar.regex_subset_error("^(?<!a)b$")
    assert "lookaround" in grammar.regex_subset_error("^(?P<n>a)(?P=n)$")


def test_expression_tests_are_ignored(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    _edit(
        root,
        "config/agent/data_quality/ops.yaml",
        lambda d: d["tables"]["ops_recommendations"]["columns"]["title"]["tests"].append({"expression": {"sql": "1 = 1"}}),
    )
    assert _run(root) == []


def test_malformed_leg_shapes_fail_with_a_message(tmp_path: Path) -> None:
    root = _make_root(tmp_path / "a")
    _edit_recs(root, lambda d: _fields(d)["title"]["dq_intent"].update(min_length=10))
    failed = _run(root)
    assert any("title: min_length leg must be a mapping, got int" in item for item in failed), failed

    root = _make_root(tmp_path / "b")
    _edit_recs(root, lambda d: _fields(d)["title"]["dq_intent"].update(not_null=False))
    failed = _run(root)
    assert any("title: not_null leg must be true or an exclude_before mapping, got False" in item for item in failed), failed
    _edit_recs(root, lambda d: _fields(d)["title"]["dq_intent"].update(not_null="yes"))
    assert any("title: not_null leg must be true or an exclude_before mapping, got 'yes'" in item for item in _run(root))

    root = _make_root(tmp_path / "c")
    _edit(
        root,
        "config/agent/data_quality/ops.yaml",
        lambda d: d["tables"]["ops_recommendations"]["columns"]["title"]["tests"].extend(["a_bare_name", {}]),
    )
    failed = _run(root)
    assert any("ops.yaml title: test entry {} must be a name or a single-key mapping" in item for item in failed), failed

    root = _make_root(tmp_path / "d")
    _edit_recs(root, lambda d: _fields(d)["title"]["dq_intent"].update(accepted_values=["a"]))
    assert any("title: accepted_values leg must be a mapping, got list" in item for item in _run(root))


def test_registry_removal_fails_the_ratchet(tmp_path: Path) -> None:
    registry = "config/agent/data_quality/source_registry.yaml"

    def add_retired_source(root: Path) -> None:
        _edit(root, registry, lambda d: d["entries"].append({"canonical_id": "retired-source", "description": "x"}))

    failed = _run(_git_root(tmp_path / "removed", base_mutation=add_retired_source))
    assert any("ratchet: source 'retired-source' was removed from the source registry" in item for item in failed), failed

    def drop_one(root: Path) -> None:
        _edit(root, registry, lambda d: d["entries"].pop())

    assert _run(_git_root(tmp_path / "added", base_mutation=drop_one)) == []
