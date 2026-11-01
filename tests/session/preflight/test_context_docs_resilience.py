"""Attribution half of the context_docs honesty set plus the shape matrix.

_check_endstate_drift carries a reason from a CLOSED seven-value vocabulary on every return
branch, so three previously indistinguishable stale causes become three named ones;
_scan_provisional_contracts names a truncated or unavailable scan on stderr while keeping its
list[str] return type; and no touched entry point raises for any shape in the matrix, because
scripts/session/preflight.py retrieves every future via .result() and a raise in any of these
callees aborts session open.

Patches scripts.preflight.context_docs.subprocess.run directly, never the session_preflight facade
module, so this module does not depend on a surface the sibling sub-plans also touch.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.preflight import _common as preflight_common
from scripts.preflight import context_docs

boto3 = pytest.importorskip("boto3")

_REASON_VOCABULARY = frozenset(
    {
        "ok",
        "stamp_absent",
        "parse_error",
        "stamp_ref_not_a_commit",
        "stamp_ref_unresolvable",
        "stamp_ref_hash_mismatch",
        "stamp_ref_new_ids_named",
    }
)

_OLD_IDS = ["T1.1", "T1.2"]
_NEW_IDS = ["T1.1", "T1.2", "ZZ9.99"]


def _roadmap_yaml(ids: list[str]) -> str:
    body = "".join(f"  - id: {i}\n    name: Item {i}\n    status: not_started\n" for i in ids)
    return f"tier_items:\n{body}"


def _hash_of(ids: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()


def _tree(tmp_path: Path, context_text: str | None, roadmap_text: str | None) -> Path:
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    if context_text is not None:
        (tmp_path / "docs" / "PROJECT_CONTEXT.md").write_text(context_text, encoding="utf-8")
    if roadmap_text is not None:
        (tmp_path / "docs" / "ROADMAP-PLATFORM.yaml").write_text(roadmap_text, encoding="utf-8")
    return tmp_path


def _context_md(stamped_hash: str, commit: str | None = "abc1234") -> str:
    ref_line = f"Derived from ROADMAP-PLATFORM.yaml @ {commit} (2026-06-28).\n" if commit else "Derived from nothing.\n"
    return f"{ref_line}roadmap_tier_id_set sha256: {stamped_hash}\n"


def _git_show(returncode: int, stdout: str) -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout)


class TestEndstateDriftReasons:
    """One distinct reason per End-State branch; the vocabulary is closed at seven values."""

    def test_in_sync_reason_is_ok(self, tmp_path: Path, monkeypatch) -> None:
        root = _tree(tmp_path, _context_md(_hash_of(_OLD_IDS)), _roadmap_yaml(_OLD_IDS))
        monkeypatch.setattr(preflight_common, "ROOT", root)
        result = context_docs._check_endstate_drift()
        assert result["stale"] is False
        assert result["reason"] == "ok"
        assert result["stamp_ref"] == "abc1234"

    def test_stamp_absent_reason(self, tmp_path: Path, monkeypatch) -> None:
        root = _tree(tmp_path, "no fingerprint here at all\n", _roadmap_yaml(_OLD_IDS))
        monkeypatch.setattr(preflight_common, "ROOT", root)
        result = context_docs._check_endstate_drift()
        assert result["reason"] == "stamp_absent"
        assert result["stale"] is False

    def test_parse_error_reason(self, tmp_path: Path, monkeypatch) -> None:
        root = _tree(tmp_path, _context_md(_hash_of(_OLD_IDS)), None)
        monkeypatch.setattr(preflight_common, "ROOT", root)
        result = context_docs._check_endstate_drift()
        assert result["reason"] == "parse_error"
        assert result["stale"] is False

    def test_stale_without_a_ref_is_stamp_ref_not_a_commit(self, tmp_path: Path, monkeypatch) -> None:
        root = _tree(tmp_path, _context_md(_hash_of(_OLD_IDS), commit=None), _roadmap_yaml(_NEW_IDS))
        monkeypatch.setattr(preflight_common, "ROOT", root)
        result = context_docs._check_endstate_drift()
        assert result["stale"] is True
        assert result["reason"] == "stamp_ref_not_a_commit"
        assert result["stamp_ref"] is None

    def test_stale_with_a_ref_git_cannot_resolve_is_stamp_ref_unresolvable(self, tmp_path: Path, monkeypatch) -> None:
        root = _tree(tmp_path, _context_md(_hash_of(_OLD_IDS)), _roadmap_yaml(_NEW_IDS))
        monkeypatch.setattr(preflight_common, "ROOT", root)
        with patch("scripts.preflight.context_docs.subprocess.run", return_value=_git_show(128, "")):
            result = context_docs._check_endstate_drift()
        assert result["reason"] == "stamp_ref_unresolvable"
        assert result["new_ids"] == []

    def test_stale_with_a_resolvable_ref_whose_hash_disagrees_is_hash_mismatch(self, tmp_path: Path, monkeypatch) -> None:
        root = _tree(tmp_path, _context_md(_hash_of(_OLD_IDS)), _roadmap_yaml(_NEW_IDS))
        monkeypatch.setattr(preflight_common, "ROOT", root)
        with patch(
            "scripts.preflight.context_docs.subprocess.run",
            return_value=_git_show(0, _roadmap_yaml(["SOMETHING.ELSE"])),
        ):
            result = context_docs._check_endstate_drift()
        assert result["reason"] == "stamp_ref_hash_mismatch"
        assert result["new_ids"] == []

    def test_stale_with_a_matching_old_id_set_is_new_ids_named(self, tmp_path: Path, monkeypatch) -> None:
        root = _tree(tmp_path, _context_md(_hash_of(_OLD_IDS)), _roadmap_yaml(_NEW_IDS))
        monkeypatch.setattr(preflight_common, "ROOT", root)
        with patch("scripts.preflight.context_docs.subprocess.run", return_value=_git_show(0, _roadmap_yaml(_OLD_IDS))):
            result = context_docs._check_endstate_drift()
        assert result["reason"] == "stamp_ref_new_ids_named"
        assert result["new_ids"] == ["ZZ9.99"]

    def test_git_show_that_itself_raises_stays_unresolvable(self, tmp_path: Path, monkeypatch) -> None:
        root = _tree(tmp_path, _context_md(_hash_of(_OLD_IDS)), _roadmap_yaml(_NEW_IDS))
        monkeypatch.setattr(preflight_common, "ROOT", root)
        with patch("scripts.preflight.context_docs.subprocess.run", side_effect=OSError("git is gone")):
            result = context_docs._check_endstate_drift()
        assert result["reason"] == "stamp_ref_unresolvable"


class TestEndstateDriftLive:
    def test_live_reason_is_in_the_closed_vocabulary_and_carries_a_stamp_ref_key(self) -> None:
        result = context_docs._check_endstate_drift()
        assert result["reason"] in _REASON_VOCABULARY, result
        assert "stamp_ref" in result, sorted(result)


class TestProvisionalScanAttribution:
    def test_raising_provider_names_an_incomplete_scan_on_one_line(self, capsys: pytest.CaptureFixture) -> None:
        def _explode(_doc: object) -> dict:
            raise RuntimeError("injected\nmulti-line failure")

        due = context_docs._scan_provisional_contracts(metrics_provider=_explode)
        err = capsys.readouterr().err
        assert due == []
        assert "provisional-contract scan INCOMPLETE" in err
        assert "the due list is partial" in err
        assert len(err.strip().splitlines()) == 1, err

    def test_missing_contracts_dir_names_an_unavailable_scan(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        due = context_docs._scan_provisional_contracts(contracts_dir=tmp_path / "absent")
        err = capsys.readouterr().err
        assert due == []
        assert "provisional-contract scan UNAVAILABLE" in err

    def test_healthy_scan_is_silent_and_returns_its_list(self, capsys: pytest.CaptureFixture) -> None:
        """Live-tree-coupled: scans the REAL docs/contracts/. Its assertion messages name the DATA
        condition, so landing a contract whose provider raises is triaged as data, not as code."""
        live_data = (
            "DATA condition, not a code regression: every docs/contracts/ file must load and "
            "evaluate its provisional trigger -- inspect the newest contract before reading "
            "scripts/preflight/context_docs.py"
        )
        due = context_docs._scan_provisional_contracts()
        assert isinstance(due, list), f"{live_data} -- the scan returned {type(due).__name__}"
        assert all(isinstance(item, str) for item in due), f"{live_data} -- a due id is not a str: {due}"
        assert capsys.readouterr().err == "", f"{live_data} -- the live scan was not silent"

    def test_exploding_loader_names_an_unavailable_scan(self, capsys: pytest.CaptureFixture) -> None:
        with patch("scripts.contracts.load_all_contracts", side_effect=RuntimeError("loader exploded")):
            due = context_docs._scan_provisional_contracts()
        err = capsys.readouterr().err
        assert due == []
        assert "provisional-contract scan UNAVAILABLE" in err
        assert "load_all_contracts failed" in err


class TestContextDocsTotality:
    """Every cell asserts a RETURN VALUE, never an exception."""

    def _drift(self, monkeypatch, root: Path) -> dict:
        monkeypatch.setattr(preflight_common, "ROOT", root)
        return context_docs._check_endstate_drift()

    def test_context_file_absent(self, tmp_path: Path, monkeypatch) -> None:
        assert self._drift(monkeypatch, _tree(tmp_path, None, _roadmap_yaml(_OLD_IDS)))["reason"] == "parse_error"

    def test_context_file_empty(self, tmp_path: Path, monkeypatch) -> None:
        assert self._drift(monkeypatch, _tree(tmp_path, "", _roadmap_yaml(_OLD_IDS)))["reason"] == "stamp_absent"

    def test_context_file_has_a_ref_but_no_hash(self, tmp_path: Path, monkeypatch) -> None:
        text = "Derived from ROADMAP-PLATFORM.yaml @ abc1234 (2026-06-28).\n"
        result = self._drift(monkeypatch, _tree(tmp_path, text, _roadmap_yaml(_OLD_IDS)))
        assert result["reason"] == "stamp_absent"
        assert result["stamp_ref"] == "abc1234"

    def test_context_file_has_a_truncated_hash(self, tmp_path: Path, monkeypatch) -> None:
        text = "roadmap_tier_id_set sha256: deadbeef\n"
        assert self._drift(monkeypatch, _tree(tmp_path, text, _roadmap_yaml(_OLD_IDS)))["reason"] == "stamp_absent"

    def test_context_file_is_a_directory(self, tmp_path: Path, monkeypatch) -> None:
        (tmp_path / "docs").mkdir(parents=True)
        (tmp_path / "docs" / "PROJECT_CONTEXT.md").mkdir()
        (tmp_path / "docs" / "ROADMAP-PLATFORM.yaml").write_text(_roadmap_yaml(_OLD_IDS), encoding="utf-8")
        monkeypatch.setattr(preflight_common, "ROOT", tmp_path)
        assert context_docs._check_endstate_drift()["reason"] == "parse_error"

    def test_roadmap_absent(self, tmp_path: Path, monkeypatch) -> None:
        assert self._drift(monkeypatch, _tree(tmp_path, _context_md(_hash_of(_OLD_IDS)), None))["reason"] == "parse_error"

    def test_roadmap_empty(self, tmp_path: Path, monkeypatch) -> None:
        assert self._drift(monkeypatch, _tree(tmp_path, _context_md(_hash_of(_OLD_IDS)), ""))["reason"] == "parse_error"

    def test_roadmap_is_a_bare_scalar(self, tmp_path: Path, monkeypatch) -> None:
        result = self._drift(monkeypatch, _tree(tmp_path, _context_md(_hash_of(_OLD_IDS)), "just a string\n"))
        assert result["reason"] == "parse_error"

    def test_roadmap_tier_items_is_not_a_list_of_mappings(self, tmp_path: Path, monkeypatch) -> None:
        result = self._drift(monkeypatch, _tree(tmp_path, _context_md(_hash_of([])), "tier_items:\n  - just-a-string\n"))
        assert result["reason"] in _REASON_VOCABULARY

    def test_scan_over_unimportable_contract_machinery_returns_a_list(
        self, monkeypatch, capsys: pytest.CaptureFixture
    ) -> None:
        """The two function-local imports are guarded SEPARATELY from the loader: either can raise
        for a malformed contracts tree, and an unguarded one would abort session open."""
        monkeypatch.setitem(sys.modules, "scripts.contracts", None)
        assert context_docs._scan_provisional_contracts() == []
        assert "provisional-contract scan UNAVAILABLE" in capsys.readouterr().err

    def test_scan_over_an_always_raising_provider_returns_a_list(self, capsys: pytest.CaptureFixture) -> None:
        """No-raise cell FIRST: the empty list alone is a value origin/main also returns, because
        its single outer try swallowed the raising provider and truncated silently. The stderr
        clause is added so this cell is not mistaken for the attribution guard -- that is
        TestProvisionalScanAttribution.test_raising_provider_names_an_incomplete_scan_on_one_line,
        which additionally pins the line's SINGLE-line-ness."""
        due = context_docs._scan_provisional_contracts(
            metrics_provider=lambda _doc: (_ for _ in ()).throw(RuntimeError("injected"))
        )
        err = capsys.readouterr().err
        assert due == []
        assert "provisional-contract scan INCOMPLETE" in err, err
        assert "the due list is partial" in err, err

    def test_loader_oserror_is_attributed_to_the_loader_not_the_directory(self, capsys: pytest.CaptureFixture) -> None:
        """A stat that failed is not a loader that failed: an OSError raised INSIDE load_all_contracts
        must render the load_all_contracts clause, never the directory-could-not-be-read clause."""
        with patch("scripts.contracts.load_all_contracts", side_effect=OSError(5, "Input/output error")):
            due = context_docs._scan_provisional_contracts()
        err = capsys.readouterr().err
        assert due == []
        assert "load_all_contracts failed" in err, err
        assert "could not be read" not in err, err

    def test_scan_over_an_unstattable_directory_returns_a_list(self, capsys: pytest.CaptureFixture) -> None:
        """Path.is_dir re-raises any OSError outside (ENOENT, ENOTDIR, EBADF, ELOOP) -- ENAMETOOLONG
        here, EACCES for a permission-denied docs/contracts under a non-root runner -- so the probe
        belongs inside a guard. origin/main never called is_dir at all and returned []."""
        due = context_docs._scan_provisional_contracts(contracts_dir=Path("/" + "n" * 300) / "contracts")
        err = capsys.readouterr().err
        assert due == []
        assert "provisional-contract scan UNAVAILABLE" in err, err
        assert "File name too long" in err, err

    def test_scan_over_a_loader_returning_a_non_mapping_returns_a_list(self, capsys: pytest.CaptureFixture) -> None:
        """The CONSUMPTION of load_all_contracts' return, not the call: .items() on a non-mapping
        raises AttributeError, so it belongs inside the same guard that wraps the call."""
        with patch("scripts.contracts.load_all_contracts", return_value=["not", "a", "mapping"]):
            due = context_docs._scan_provisional_contracts()
        err = capsys.readouterr().err
        assert due == []
        assert "provisional-contract scan UNAVAILABLE" in err, err
        assert "load_all_contracts failed" in err, err

    def test_read_context_files_over_a_bare_root(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(preflight_common, "ROADMAP_FILE", tmp_path / "absent-roadmap.yaml")
        monkeypatch.setattr(preflight_common, "DECISIONS_FILE", tmp_path / "absent-decisions.md")
        result = context_docs.read_context_files(open_recs_count=0)
        assert result["roadmap_phase"] == "unknown"
        assert result["open_decisions_count"] == 0
