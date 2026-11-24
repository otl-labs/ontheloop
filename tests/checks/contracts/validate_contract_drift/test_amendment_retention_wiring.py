"""Pass 2 wiring of the amendment_log retention and rewrite checks (Decision 275 cl.4, rec-4119)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from scripts.checks.contracts import _amendment_retention, _population, amendment_compact

from .conftest import validate_contract_drift

_REAL_LOAD_RETENTION = _amendment_retention.load_retention
_REPO = Path(__file__).resolve().parents[4]
_CFG = {
    "keep_full": 5,
    "terse_row": {"keys": ["date", "semantic_break", "change_class", "summary"], "summary_max_chars": 160},
    "exempt_files": ["docs/contracts/north-star.yaml"],
}


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
        new = {k: v for k, v in _row(9).items() if k != "migration_story"}
        failed = _run(
            tmp_path,
            install_fake_git,
            write_contract,
            FakeGit,
            _doc(class_d_yaml, log, "a"),
            _doc(class_d_yaml, [new] + log[:4] + [terse], "b"),
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


def _plain(n: int, date: str) -> dict:
    return {
        "date": date,
        "semantic_break": False,
        "change_class": "field_add",
        "summary": f"PLAN-x-{n}: " + "long wording " * 30,
    }


class TestDecayWiring:
    def test_decay_failure_names_the_tool_command(
        self, tmp_path, install_fake_git, write_contract, FakeGit, class_d_yaml
    ) -> None:
        base = [_plain(n, f"2026-10-{10 + n:02d}") for n in range(7)]
        failed = _run(
            tmp_path,
            install_fake_git,
            write_contract,
            FakeGit,
            _doc(class_d_yaml, base, "a"),
            _doc(class_d_yaml, [_plain(9, "2026-11-20")] + base, "b"),
        )
        hits = [f for f in failed if f.startswith("Contract drift (retention): amend.yaml")]
        assert hits and "scripts.checks.contracts.amendment_compact --write --rows 2" in hits[0], failed

    def test_pass2_receives_file_texts(self, tmp_path, install_fake_git, write_contract, FakeGit, class_d_yaml) -> None:
        base = [_plain(n, f"2026-10-{10 + n:02d}") for n in range(7)]
        base_text = _doc(class_d_yaml, base, "a")
        head_text = re.sub(
            r"(?m)^  semantic_break:", "  # shared note\n  semantic_break:", _doc(class_d_yaml, base, "b"), count=1
        )
        assert "# shared note" in head_text
        failed = _run(tmp_path, install_fake_git, write_contract, FakeGit, base_text, head_text)
        assert any(f.startswith("Contract drift (selectability): amend.yaml") for f in failed), failed


_PROBE = '{date: "2099-01-01", semantic_break: false, change_class: field_add, summary: "%s"}'


def _with_row(text: str, summary: str) -> str | None:
    spans = amendment_compact.locate_log(text)
    if not spans:
        return None
    lines = amendment_compact.split_lines(text)
    first = spans[0]
    if not lines[first.first].lstrip().startswith("- "):
        return None
    lines.insert(first.first, f"{first.indent}- {_PROBE % summary}\n")
    return "".join(lines)


class TestRealTree:
    def test_real_contracts_can_meet_the_ratchet(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(_amendment_retention, "load_retention", _REAL_LOAD_RETENTION)
        cfg = _REAL_LOAD_RETENTION(_REPO)
        assert cfg is not None
        for path in sorted((_REPO / "docs" / "contracts").glob("*.yaml")):
            if amendment_compact.exempt_file(path, cfg):
                continue
            text = path.read_text(encoding="utf-8")
            base_data = yaml.safe_load(text)
            base_log = base_data.get("amendment_log") if isinstance(base_data, dict) else None
            if not isinstance(base_log, list) or len(_amendment_retention.full_rows(base_log, cfg)) < cfg["keep_full"]:
                continue
            for summary in ("PLAN-ratchet-probe: probe", "PLAN-ratchet-probe: " + "long wording " * 30):
                head = _with_row(text, summary)
                if head is None:
                    break
                msgs = _amendment_retention.pass2_messages(path.name, base_data, yaml.safe_load(head), False, text, head)
                asked = re.search(r"--rows (\d+)", " ".join(msgs))
                if asked:
                    scratch = tmp_path / path.name
                    scratch.write_text(head, encoding="utf-8")
                    code = amendment_compact.main(["--write", "--rows", asked.group(1), str(scratch)])
                    assert code == 0, f"{path.name}: the tool failed"
                    head = scratch.read_text(encoding="utf-8")
                    msgs = _amendment_retention.pass2_messages(path.name, base_data, yaml.safe_load(head), False, text, head)
                terse = summary.endswith("probe")
                assert not msgs or (not terse and all("by hand" in m or "selects no row" in m for m in msgs)), (
                    path.name,
                    msgs,
                )
