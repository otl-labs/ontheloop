"""Tests for the byte_budgets raise-marker legs (Decision 275, rec-5262)."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.checks.structural import _byte_raises
from scripts.checks.structural.budget_raises import validate_structural_size_budget_raises

_REG = "config/structural_size_budgets.yaml"
_HEAD_REG = "schema_version: 1\nbyte_budgets:\n{entries}\nlong_line_budgets: {{}}\n"

_SEED = "ports.yaml: 100  # raise-approved: dec-274 seed"


def _registry(*entries: str) -> str:
    return _HEAD_REG.format(entries="\n".join(f"  {e}" for e in entries) or "  {}")


def _decisions(**bodies: str) -> str:
    return "\n".join(f"## Decision {n[1:]}: Title (Decided)\n\n{body}\n" for n, body in sorted(bodies.items()))


def _run(
    tmp_path: Path,
    base_reg: str | None,
    head_reg: str,
    base_dec: str | None,
    head_dec: str,
    archive: str | None = "",
) -> list[str]:
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / _REG).write_text(head_reg, encoding="utf-8")
    (tmp_path / "docs" / "DECISIONS.md").write_text(head_dec, encoding="utf-8")

    def reader(rel: str) -> str | None:
        return {"docs/DECISIONS.md": base_dec, "docs/DECISIONS_ARCHIVE.md": archive}.get(rel)

    with patch("scripts.checks._common.ROOT", tmp_path), contextlib.redirect_stdout(io.StringIO()):
        return _byte_raises.violations(base_reg, reader)


_NAMES_PORTS = {"d274": "Seeds ports.yaml and file-router.yaml.", "d275": "Authorizes nothing here."}


class TestRaiseNeedsFreshMention:
    def test_unmarked_raise_fails(self, tmp_path: Path) -> None:
        d = _decisions(**_NAMES_PORTS)
        out = _run(tmp_path, _registry(_SEED), _registry("ports.yaml: 200"), d, d)
        assert out

    def test_raise_citing_the_seed_decision_again_fails(self, tmp_path: Path) -> None:
        head = _registry("ports.yaml: 200  # raise-approved: dec-274 again")
        out = _run(tmp_path, _registry(_SEED), head, _decisions(**_NAMES_PORTS), _decisions(**_NAMES_PORTS))
        assert out

    def test_raise_citing_a_decision_whose_mention_is_not_new_fails(self, tmp_path: Path) -> None:
        bodies = {"d274": "Seeds ports.yaml.", "d275": "Names ports.yaml already."}
        head = _registry("ports.yaml: 200  # raise-approved: dec-275 raise")
        out = _run(tmp_path, _registry(_SEED), head, _decisions(**bodies), _decisions(**bodies))
        assert any("no text added" in m for m in out)

    def test_raise_with_a_freshly_added_naming_decision_passes(self, tmp_path: Path) -> None:
        base = {"d274": "Seeds ports.yaml.", "d275": "Other text."}
        head_bodies = {"d274": "Seeds ports.yaml.", "d275": "Other text.\nAlso raises docs/contracts/ports.yaml."}
        head = _registry("ports.yaml: 200  # raise-approved: dec-275 raise")
        assert _run(tmp_path, _registry(_SEED), head, _decisions(**base), _decisions(**head_bodies)) == []

    def test_shrink_passes_without_a_marker(self, tmp_path: Path) -> None:
        d = _decisions(**_NAMES_PORTS)
        assert _run(tmp_path, _registry(_SEED), _registry("ports.yaml: 90"), d, d) == []


class TestNewEntriesNeedFreshMention:
    def test_unmarked_new_registration_fails(self, tmp_path: Path) -> None:
        d = _decisions(**_NAMES_PORTS)
        out = _run(tmp_path, _registry(_SEED), _registry(_SEED, "git-ops.yaml: 50"), d, d)
        assert any("git-ops.yaml" in m for m in out)

    def test_new_registration_citing_a_stale_mention_fails(self, tmp_path: Path) -> None:
        bodies = {"d274": "Seeds ports.yaml.", "d275": "Names git-ops.yaml already."}
        head = _registry(_SEED, "git-ops.yaml: 50  # raise-approved: dec-275 new")
        out = _run(tmp_path, _registry(_SEED), head, _decisions(**bodies), _decisions(**bodies))
        assert any("no text added" in m for m in out)

    def test_entry_dropped_and_re_added_citing_the_seed_decision_fails(self, tmp_path: Path) -> None:
        d = _decisions(**_NAMES_PORTS)
        head = _registry("ports.yaml: 100  # raise-approved: dec-274 back again")
        out = _run(tmp_path, _registry(), head, d, d)
        assert any("no text added" in m for m in out)

    def test_new_registration_with_a_fresh_full_path_or_bare_name_passes(self, tmp_path: Path) -> None:
        base = {"d275": "Old."}
        head_bodies = {"d275": "Old.\nNames docs/contracts/git-ops.yaml and telemetry_agents.yaml."}
        head = _registry(
            "git-ops.yaml: 50  # raise-approved: dec-275 new",
            "telemetry_agents.yaml: 60  # raise-approved: dec-275 new",
        )
        assert _run(tmp_path, _registry(), head, _decisions(**base), _decisions(**head_bodies)) == []

    def test_install_change_with_a_new_decision_passes(self, tmp_path: Path) -> None:
        head = _registry(_SEED)
        assert _run(tmp_path, _registry(), head, _decisions(d1="Old."), _decisions(d1="Old.", **_NAMES_PORTS)) == []


class TestMentionBoundaries:
    @pytest.mark.parametrize(
        "added",
        [
            "Mentions docs/contracts only.",
            "Names file-router.yaml only.",
            "Names x_ports.yaml only.",
        ],
    )
    def test_non_naming_fresh_text_never_authorizes(self, tmp_path: Path, added: str) -> None:
        base = {"d275": "Old."}
        head_bodies = {"d275": f"Old.\n{added}"}
        head = _registry("ports.yaml: 100  # raise-approved: dec-275 new")
        out = _run(tmp_path, _registry(), head, _decisions(**base), _decisions(**head_bodies))
        assert out


class TestRetroactiveScan:
    def test_present_marker_whose_decision_no_longer_names_the_contract_fails(self, tmp_path: Path) -> None:
        d = _decisions(d274="Names nothing relevant.")
        out = _run(tmp_path, _registry(_SEED), _registry(_SEED), d, d)
        assert any("retroactive scan" in m for m in out)


class TestSkips:
    def test_unreadable_base_corpus_skips_freshness_only(self, tmp_path: Path) -> None:
        head = _registry("ports.yaml: 200  # raise-approved: dec-275 raise")
        d = _decisions(d274="Seeds ports.yaml.", d275="Names ports.yaml.")
        assert _run(tmp_path, _registry(_SEED), head, None, d) == []

    def test_unreachable_base_registry_skips_the_diff_legs(self, tmp_path: Path) -> None:
        d = _decisions(**_NAMES_PORTS)
        assert _run(tmp_path, None, _registry(_SEED), d, d) == []


def test_entry_count_counts_roster_entries() -> None:
    assert _byte_raises.entry_count(_registry(_SEED, "git-ops.yaml: 5  # raise-approved: dec-274 x")) == 2
    assert _byte_raises.entry_count(_registry()) == 0


def test_unmarked_byte_raise_fails_validate_structural_size_budget_raises(tmp_path: Path) -> None:
    d = _decisions(**_NAMES_PORTS)
    base = _registry(_SEED)
    (tmp_path / "config").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / _REG).write_text(_registry("ports.yaml: 200"), encoding="utf-8")
    (tmp_path / "docs" / "DECISIONS.md").write_text(d, encoding="utf-8")

    def reader(rel: str) -> str | None:
        return {_REG: base, "docs/DECISIONS.md": d, "docs/DECISIONS_ARCHIVE.md": ""}.get(rel)

    failed: list[str] = []
    with patch("scripts.checks._common.ROOT", tmp_path), contextlib.redirect_stdout(io.StringIO()):
        validate_structural_size_budget_raises(failed, base_reader=reader)
    assert failed


def test_absent_current_registry_reports_nothing(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    with patch("scripts.checks._common.ROOT", tmp_path), contextlib.redirect_stdout(io.StringIO()):
        assert _byte_raises._new_entry_leg(_registry(_SEED), lambda _rel: "") == []
