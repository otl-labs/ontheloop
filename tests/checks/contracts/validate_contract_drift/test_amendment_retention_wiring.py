"""Pass 2 wiring of the amendment_log retention and rewrite checks (Decision 275 cl.4, rec-4119)."""

from __future__ import annotations

import pytest

from scripts.checks.contracts import _amendment_retention, _population

from .conftest import validate_contract_drift

_CFG = {"keep_full": 5, "exempt_files": ["docs/contracts/north-star.yaml"]}


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    monkeypatch.setattr(_population, "resolve_evaluator", lambda *a, **k: (True, "bypassed for this test module"))
    monkeypatch.setattr(_amendment_retention, "load_retention", lambda root=None: _CFG)


def _row(n: int, summary: str | None = None) -> dict:
    return {
        "date": "2026-11-01",
        "semantic_break": False,
        "change_class": "field_add",
        "summary": summary or f"PLAN-x-{n}: " + "long wording " * 30,
        "migration_story": "story",
    }


def _run(tmp_path, install_fake_git, write_contract, FakeGit, base: str, head: str) -> list[str]:
    write_contract(tmp_path, "amend.yaml", head)
    install_fake_git(
        FakeGit(
            merge_base_rc=0,
            ls_tree=["docs/contracts/amend.yaml"],
            changed=["docs/contracts/amend.yaml"],
            show_map={"docs/contracts/amend.yaml": base},
            batch_map={"docs/contracts/amend.yaml": base},
        )
    )
    failed: list[str] = []
    validate_contract_drift(failed, contracts_dir=tmp_path)
    return failed


def _doc(class_d_yaml, log: list, note: str) -> str:
    return class_d_yaml(
        contract_id="amend",
        subject="amend-subject",
        evaluator={"check": "validate_placement"},
        amendment_log=log,
        extra={"note": note},
    )


class TestRetentionWiring:
    def test_retention_violation_on_a_changed_contract_fails(
        self, tmp_path, install_fake_git, write_contract, FakeGit, class_d_yaml
    ) -> None:
        log = [_row(n) for n in range(5)]
        failed = _run(
            tmp_path,
            install_fake_git,
            write_contract,
            FakeGit,
            _doc(class_d_yaml, log, "a"),
            _doc(class_d_yaml, [_row(9)] + log, "b"),
        )
        assert any(f.startswith("Contract drift (retention): amend.yaml") for f in failed), failed

    def test_a_dropped_row_fails(self, tmp_path, install_fake_git, write_contract, FakeGit, class_d_yaml) -> None:
        log = [_row(n) for n in range(3)]
        failed = _run(
            tmp_path,
            install_fake_git,
            write_contract,
            FakeGit,
            _doc(class_d_yaml, log, "a"),
            _doc(class_d_yaml, log[:2] + [_row(7)], "b"),
        )
        assert any("Contract drift (retention)" in f for f in failed) or any("Contract drift (amendment)" in f for f in failed)
        dropped = _run(
            tmp_path,
            install_fake_git,
            write_contract,
            FakeGit,
            _doc(class_d_yaml, log, "a"),
            _doc(class_d_yaml, log[:2], "a"),
        )
        assert any("lost 1 row" in f for f in dropped), dropped

    def test_reworded_old_row_offered_as_the_new_row_fails_on_a_class_d_contract(
        self, tmp_path, install_fake_git, write_contract, FakeGit, class_d_yaml
    ) -> None:
        base_log = [_row(1)]
        head_log = [_row(1, summary="reworded to look new")]
        failed = _run(
            tmp_path,
            install_fake_git,
            write_contract,
            FakeGit,
            _doc(class_d_yaml, base_log, "a"),
            _doc(class_d_yaml, head_log, "b"),
        )
        assert any(f.startswith("Contract drift (amendment): amend.yaml") and "rewrites" in f for f in failed), failed

    def test_genuinely_new_row_with_a_compaction_passes(
        self, tmp_path, install_fake_git, write_contract, FakeGit, class_d_yaml
    ) -> None:
        log = [_row(n) for n in range(5)]
        terse = {"date": "2026-11-01", "semantic_break": False, "change_class": "field_add", "summary": "PLAN-x-4: short"}
        failed = _run(
            tmp_path,
            install_fake_git,
            write_contract,
            FakeGit,
            _doc(class_d_yaml, log, "a"),
            _doc(class_d_yaml, [_row(9)] + log[:4] + [terse], "b"),
        )
        assert failed == [], "\n".join(failed)


class TestRitualContract:
    def test_field_edit_with_only_a_field_level_row_passes(
        self, tmp_path, install_fake_git, write_contract, FakeGit, valid_class_a
    ) -> None:
        base = valid_class_a(contract_id="amend")
        head = base.replace(
            "                description: A field",
            "                description: A field",
        )
        head = base.replace("    description: A field\n", "    description: A reworded field\n").replace(
            "    dq_intent:",
            "    amendment_log:\n"
            "      - {date: '2026-11-18', semantic_break: false, change_class: prose_improvement}\n"
            "    dq_intent:",
        )
        failed = _run(tmp_path, install_fake_git, write_contract, FakeGit, base, head)
        assert not any("retention" in f or "(amendment)" in f for f in failed), failed
