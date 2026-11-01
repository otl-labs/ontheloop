"""Tests for validate_account_credential_inventory(): the account-credential-inventory contract's evaluator."""

from __future__ import annotations

import copy
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from scripts.checks import registry
from scripts.checks.contracts import _manifest
from scripts.checks.contracts import validate_account_credential_inventory as module
from scripts.checks.contracts.validate_account_credential_inventory import validate_account_credential_inventory

_CONTRACT = "docs/contracts/account-credential-inventory.yaml"
_REAL_ROOT = Path(__file__).resolve().parents[3]
_REAL = yaml.safe_load((_REAL_ROOT / _CONTRACT).read_text(encoding="utf-8"))
_Mutator = Callable[[dict[str, Any]], None]
_LATEST = max(str(e["date"]) for e in _REAL["evidence_ledger"])


def _ref_files() -> set[str]:
    return {r["ref"].partition("#")[0] for r in _REAL["rows"] if "ref" in r}


def _row(doc: dict[str, Any], rid: str) -> dict[str, Any]:
    return next(r for section in ("rows", "billing") for r in doc[section] if r["id"] == rid)


def _build(root: Path, doc: dict[str, Any] | str) -> None:
    for rel in _ref_files() | {_CONTRACT}:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(_REAL_ROOT / rel, root / rel)
    text = doc if isinstance(doc, str) else yaml.safe_dump(doc, sort_keys=False, width=10_000)
    (root / _CONTRACT).write_text(text, encoding="utf-8")


def _run(root: Path, base: dict[str, Any] | None = None) -> list[str]:
    failed: list[str] = []
    with patch("scripts.checks._common.ROOT", root), patch.object(module, "_base_contract", return_value=base):
        validate_account_credential_inventory(failed)
    return failed


def _mutated(mutate: _Mutator) -> dict[str, Any]:
    doc = copy.deepcopy(_REAL)
    mutate(doc)
    return doc


def _set(rid: str, **fields: Any) -> _Mutator:
    return lambda d: _row(d, rid).update(fields)


def _drop(rid: str, key: str) -> _Mutator:
    return lambda d: _row(d, rid).pop(key)


def _closed(d: dict[str, Any]) -> None:
    d["programme"]["phase"] = "closed"


def _all_terminal(d: dict[str, Any]) -> None:
    for section in ("rows", "billing"):
        for r in d[section]:
            r.update(status="company")
            r["owner_today" if section == "rows" else "payer"] = "company"
            r.pop("follows", None)
    d["evidence_ledger"] = [
        {"date": "2026-10-21", "row": r["id"], "status": "company", "evidence": "test"}
        for s in ("rows", "billing")
        for r in d[s]
    ]


def _closed_terminal(d: dict[str, Any]) -> None:
    _closed(d)
    _all_terminal(d)


def _closed_on_open_target(d: dict[str, Any]) -> None:
    _closed_terminal(d)
    _row(d, "AWS-01").update(status="personal")
    _row(d, "AWS-04").update(status="follows", follows="AWS-01")
    d["evidence_ledger"] = [e for e in d["evidence_ledger"] if e["row"] != "AWS-01"]


def _ledger(**fields: Any) -> _Mutator:
    return lambda d: d["evidence_ledger"].append(
        {"date": _LATEST, "row": "GH-03", "status": "company", "evidence": "t", **fields}
    )


def _swap_ledger_dates(d: dict[str, Any]) -> None:
    d["evidence_ledger"][0]["date"] = "2026-10-22"


def _reorder_rows(d: dict[str, Any]) -> None:
    d["rows"][0], d["rows"][1] = d["rows"][1], d["rows"][0]


_PROBES = {
    "at-sign": lambda: "x" + chr(64) + "y",
    "12-digit": lambda: "1" * 12,
    "ARN": lambda: "arn" + ":aws",
    "Neon organisation": lambda: "org-" + "aa-bb-" + "123",
    "Neon project": lambda: "aa-bb-" + "1" * 8,
}

_RED: list[tuple[str, _Mutator, str]] = [
    ("missing key", _drop("GH-01", "target"), "GH-01: missing keys"),
    ("missing billing key", _drop("BILL-01", "payer"), "BILL-01: missing keys"),
    ("unknown row key", _set("GH-01", extra="x"), "unknown keys"),
    ("unknown billing key", _set("BILL-01", ref="x"), "unknown keys"),
    ("null value", _set("GH-01", item=None), "null values"),
    ("duplicate id", lambda d: d["rows"].append(copy.deepcopy(d["rows"][0])), "duplicate id"),
    ("billing id collides", _set("BILL-02", id="GH-01"), "duplicate id"),
    ("unknown status", _set("GH-01", status="bogus"), "status 'bogus' not in status_values"),
    ("unknown stage", _set("GH-01", stage="bogus"), "stage 'bogus'"),
    ("unknown owner", _set("GH-01", owner_today="bogus"), "owner_today 'bogus'"),
    ("bad payer", _set("BILL-01", payer="bogus"), "payer 'bogus'"),
    ("malformed rec", _set("GH-01", rec=["4356"]), "rec must be a list"),
    ("rec not a list", _set("GH-01", rec="rec-1"), "rec must be a list"),
    ("ledger date regression", _swap_ledger_dates, "earlier than the entry before it"),
    ("ledger row unknown", _ledger(row="NOPE-01"), "does not exist"),
    ("ledger status unknown", _ledger(status="bogus"), "not in status_values"),
    (
        "ledger missing field",
        lambda d: d["evidence_ledger"].append({"date": _LATEST, "row": "GH-03", "status": "company"}),
        "missing",
    ),
    ("ledger unknown key", _ledger(extra="x"), "unknown keys"),
    ("ledger non-ISO date", _ledger(date="22/10/2026"), "malformed"),
    ("status disagrees with ledger", _set("GH-05", status="unknown"), "disagrees with its latest ledger"),
    ("evidence-required status without entry", _set("GH-07", status="moved"), "needs a latest evidence_ledger entry"),
    ("unresolvable ref", _set("GH-01", ref="docs/contracts/github-org-transfer.yaml#identity.nope"), "does not resolve"),
    ("ref to missing file", _set("GH-01", ref="docs/contracts/absent.yaml#identity"), "does not resolve"),
    ("ref without fragment", _set("GH-01", ref="docs/contracts/github-org-transfer.yaml"), "does not resolve"),
    ("follows without key", _drop("AWS-04", "follows"), "go together"),
    ("follows key on non-follows row", _set("GH-01", follows="GH-02"), "go together"),
    ("follows chain", _set("AWS-05", follows="AWS-04"), "not itself follows"),
    ("follows nonexistent id", _set("AWS-04", follows="NOPE-01"), "must name an existing row"),
    ("bad expires", _set("GH-06", expires="soon"), "expires is not an ISO date"),
    ("closed with open row", _closed, "programme is closed"),
    (
        "closed with follows row on open target",
        _closed_on_open_target,
        "programme is closed",
    ),
    ("terminal contains open status", lambda d: d["terminal_statuses"].append("personal"), "includes an open status"),
    ("phase outside values", lambda d: d["programme"].update(phase="bogus"), "programme.phase 'bogus'"),
    (
        "evidence_required outside status_values",
        lambda d: d["evidence_required"].append("bogus"),
        "evidence_required names statuses",
    ),
    ("terminal outside status_values", lambda d: d["terminal_statuses"].append("bogus"), "terminal_statuses names statuses"),
    ("open status missing", lambda d: d["status_values"].pop("unknown"), "missing an open status"),
    ("proven row owned by founder", _set("GH-05", status="proven", owner_today="founder"), "disagrees with status proven"),
    ("company row owned by none", _set("GH-03", owner_today="none"), "disagrees with status company"),
    ("company row owned by founder", _set("GH-03", owner_today="founder"), "disagrees with status company"),
    ("retired row owned by founder", _set("GH-05", status="retired", owner_today="founder"), "disagrees with status retired"),
    ("stays row owned by company", _set("GH-02", owner_today="company"), "disagrees with status stays"),
    ("billing proven paid personally", _set("BILL-01", status="proven"), "payer 'personal' disagrees with status proven"),
    ("billing company paid personally", _set("BILL-07", payer="personal"), "disagrees with status company"),
    (
        "billing stays paid by company",
        lambda d: _row(d, "BILL-01").update(status="stays", payer="company"),
        "disagrees with status stays",
    ),
    ("non-scalar status", _set("GH-01", status=["x"]), "non-scalar values"),
    ("non-scalar follows", _set("AWS-04", follows=["a"]), "non-scalar values"),
    ("non-scalar id", _set("GH-01", id=["a"]), "non-scalar values"),
    ("non-scalar ledger row", _ledger(row=["a"]), "non-scalar values"),
    ("list-valued vocabulary", lambda d: d.update(status_values=[["a"]]), "not in status_values"),
    ("ref escaping the repository", _set("GH-01", ref="../outside.yaml#a"), "does not resolve"),
    ("ledger entry not a mapping", lambda d: d["evidence_ledger"].append("scalar"), "evidence_ledger["),
    ("rows not a list", lambda d: d.update(rows={"a": 1}), "rows is not a list"),
    ("row not a mapping", lambda d: d["rows"].append("scalar"), "rows["),
]


class TestValidateAccountCredentialInventory:
    def test_green_on_the_real_tree(self) -> None:
        failed: list[str] = []
        with patch("scripts.checks._common.ROOT", _REAL_ROOT), patch.object(registry, "examined") as examined:
            validate_account_credential_inventory(failed)
        assert failed == []
        examined.assert_called_once_with(len(_REAL["rows"]) + len(_REAL["billing"]), unit="inventory_rows")

    def test_green_on_a_copy(self, tmp_path: Path) -> None:
        _build(tmp_path, copy.deepcopy(_REAL))
        assert _run(tmp_path) == []

    @pytest.mark.parametrize(("name", "mutate", "needle"), _RED, ids=[r[0] for r in _RED])
    def test_red_cases(self, tmp_path: Path, name: str, mutate: _Mutator, needle: str) -> None:
        _build(tmp_path, _mutated(mutate))
        failed = _run(tmp_path)
        assert any(needle in f for f in failed), (name, failed)

    @pytest.mark.parametrize("label", sorted(_PROBES))
    def test_shape_guard(self, tmp_path: Path, label: str) -> None:
        text = (_REAL_ROOT / _CONTRACT).read_text(encoding="utf-8") + f"# {_PROBES[label]()}\n"
        _build(tmp_path, text)
        failed = _run(tmp_path)
        assert any("publication_rule" in f for f in failed), failed
        assert not any(_PROBES[label]() in f for f in failed)

    def test_closed_phase_passes_when_every_row_is_terminal(self, tmp_path: Path) -> None:
        _build(tmp_path, _mutated(_closed_terminal))
        assert _run(tmp_path) == []

    def test_follows_row_takes_its_target_status_when_closed(self, tmp_path: Path) -> None:
        def mutate(d: dict[str, Any]) -> None:
            _closed(d)
            _all_terminal(d)
            _row(d, "AWS-04").update(status="follows", follows="AWS-01", owner_today="account")
            d["evidence_ledger"] = [e for e in d["evidence_ledger"] if e["row"] != "AWS-04"]

        _build(tmp_path, _mutated(mutate))
        assert _run(tmp_path) == []

    def test_billing_stays_paid_personally_is_green(self, tmp_path: Path) -> None:
        def mutate(d: dict[str, Any]) -> None:
            _row(d, "BILL-01").update(status="stays")
            d["evidence_ledger"].append({"date": _LATEST, "row": "BILL-01", "status": "stays", "evidence": "t"})

        _build(tmp_path, _mutated(mutate))
        assert _run(tmp_path) == []

    def test_unparseable_contract_fails(self, tmp_path: Path) -> None:
        _build(tmp_path, "rows: [unclosed\n")
        assert any("missing or unparseable" in f for f in _run(tmp_path))

    def test_non_mapping_contract_fails(self, tmp_path: Path) -> None:
        _build(tmp_path, "- a\n- b\n")
        assert any("missing or unparseable" in f for f in _run(tmp_path))

    def test_missing_contract_fails(self, tmp_path: Path) -> None:
        failed = _run(tmp_path)
        assert any("missing or unparseable" in f for f in failed)

    def test_list_ref_with_dotted_id_resolves(self, tmp_path: Path) -> None:
        _build(tmp_path, copy.deepcopy(_REAL))
        target = tmp_path / "docs/contracts/github-org-transfer.yaml"
        target.write_text(yaml.safe_dump({"items": [{"id": "a.b", "child": {"k": 1}}]}), encoding="utf-8")
        assert module._ref_resolves(tmp_path, "docs/contracts/github-org-transfer.yaml#items.a.b.child.k")
        assert not module._ref_resolves(tmp_path, "docs/contracts/github-org-transfer.yaml#items.a.b.child.z")
        assert not module._ref_resolves(tmp_path, "docs/contracts/github-org-transfer.yaml#items.a")

    def test_is_registered_and_dispatched(self) -> None:
        assert "validate_account_credential_inventory" in registry._REGISTRY
        entry = next(e for e in _manifest.ENTRIES if e.name == "validate_account_credential_inventory")
        assert entry.pre is True
        assert entry.pre_globs is None
        assert entry.full_segment == "full_after_lint"


class TestAppendOnlyAgainstBase:
    @pytest.mark.parametrize(
        ("name", "mutate", "needle"),
        [
            ("deleted base row", lambda b: b["rows"].append({"id": "GONE-01"}), "GONE-01: removed since the base"),
            ("deleted base billing row", lambda b: b["billing"].append({"id": "GONE-02"}), "GONE-02: removed since the base"),
            ("reordered base rows", _reorder_rows, "rows reordered"),
            ("edited base ledger entry", lambda b: b["evidence_ledger"][0].update(evidence="other"), "not a prefix"),
            (
                "removed base ledger entry",
                lambda b: b["evidence_ledger"].append({"date": "2026-10-23", "row": "GH-03"}),
                "not a prefix",
            ),
        ],
    )
    def test_red(self, tmp_path: Path, name: str, mutate: _Mutator, needle: str) -> None:
        base = copy.deepcopy(_REAL)
        mutate(base)
        _build(tmp_path, copy.deepcopy(_REAL))
        failed = _run(tmp_path, base)
        assert any(needle in f for f in failed), (name, failed)

    def test_inserted_row_is_green(self, tmp_path: Path) -> None:
        head = copy.deepcopy(_REAL)
        head["rows"].insert(
            1, {**_row(head, "GH-03"), "id": "NEW-01", "status": "personal", "owner_today": "founder", "stage": "none"}
        )
        _build(tmp_path, head)
        assert _run(tmp_path, copy.deepcopy(_REAL)) == []

    def test_appended_ledger_entry_is_green(self, tmp_path: Path) -> None:
        head = _mutated(_ledger())
        _build(tmp_path, head)
        assert _run(tmp_path, copy.deepcopy(_REAL)) == []

    def test_missing_base_skips_without_failing(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _build(tmp_path, copy.deepcopy(_REAL))
        assert _run(tmp_path, None) == []
        assert "SKIP" in capsys.readouterr().out

    def test_unparseable_base_skips(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _build(tmp_path, copy.deepcopy(_REAL))
        failed: list[str] = []
        fake = type("R", (), {"returncode": 0, "stdout": "rows: [unclosed\n"})()
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._common.status_diff_base", return_value="abc"),
            patch("scripts.checks._common.run", return_value=fake),
        ):
            validate_account_credential_inventory(failed)
        assert failed == []
        assert "SKIP" in capsys.readouterr().out

    def test_base_file_absent_skips(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _build(tmp_path, copy.deepcopy(_REAL))
        failed: list[str] = []
        fake = type("R", (), {"returncode": 128, "stdout": ""})()
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._common.status_diff_base", return_value="abc"),
            patch("scripts.checks._common.run", return_value=fake),
        ):
            validate_account_credential_inventory(failed)
        assert failed == []
        assert "SKIP" in capsys.readouterr().out


class TestBaseAndRefEdges:
    def test_no_base_ref_skips(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _build(tmp_path, copy.deepcopy(_REAL))
        failed: list[str] = []
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._common.status_diff_base", return_value=None),
        ):
            validate_account_credential_inventory(failed)
        assert failed == []
        assert "SKIP" in capsys.readouterr().out

    def test_non_mapping_base_skips(self, tmp_path: Path) -> None:
        fake = type("R", (), {"returncode": 0, "stdout": "- a\n"})()
        with (
            patch("scripts.checks._common.status_diff_base", return_value="abc"),
            patch("scripts.checks._common.run", return_value=fake),
        ):
            assert module._base_contract(tmp_path) is None

    def test_unparseable_ref_target_does_not_resolve(self, tmp_path: Path) -> None:
        (tmp_path / "x.yaml").write_text("a: [unclosed\n", encoding="utf-8")
        assert not module._ref_resolves(tmp_path, "x.yaml#a")
