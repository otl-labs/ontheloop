"""Mirror tests for the catalog rules of scripts/telemetry_capture/verify.py: ST-6's keyed verify state and RD-2's resolution.

A check reads the catalog the routed reader serves before it takes the tree lock, and only with credentials; a tree recorded
under another catalog is skipped, and verify state recorded under another catalog never seeds a read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scripts.telemetry_capture import read_route, verify_due
from scripts.telemetry_capture import state as st
from scripts.telemetry_capture.state import CaptureState
from tests.fixtures.telemetry_golden_chain import ROOT_ID, Reader, Tree, captured_rows, corpus, ledger_tree

SMOKE, PROD = "ducklake_smoke", "ducklake_telemetry"
SMOKE_ID = "01SMOKESMOKESMOKESMOKESMOK"
OLD_FLOORS = {"root_session_id": SMOKE_ID, "last_ledger_line": 999, "held_ledger_line": 999}


class _ServedReader(Reader):
    """The routed reader's contract: a catalog attribute, and session_tree_rows answering only the catalog's own root id."""

    def __init__(self, stored: dict[str, list[dict[str, Any]]], catalog: str) -> None:
        super().__init__(stored)
        self.catalog = catalog

    def named(self, verb: str, **params: Any) -> list[dict[str, Any]]:
        if verb == "session_tree_rows" and params["session_id"] != ROOT_ID:
            self.calls.append((verb, params))
            return []
        return super().named(verb, **params)


class _Unresolved(Reader):
    def __init__(self, stored: dict[str, list[dict[str, Any]]], events: list[str]) -> None:
        super().__init__(stored)
        self.events = events

    @property
    def catalog(self) -> str:
        self.events.append("catalog")
        raise read_route.TelemetryCatalogUnresolved("the telemetry catalog is unreadable from a source")


def _recorded_in(tree: Tree, catalog: str) -> Tree:
    cursor = tree.state.cursor_on_disk()
    assert cursor is not None
    tree.state.save_cursor(cursor, tree.path.read_bytes(), st.Loaded(None, st.LOST), catalog=catalog)
    return tree


def _served(tree: Tree, catalog: str) -> Tree:
    tree.reader = _ServedReader(captured_rows(tree.result), catalog)
    return tree


def test_a_tree_recorded_in_another_catalog_is_skipped(tmp_path: Path) -> None:
    tree = _served(corpus(tmp_path / "prod"), PROD)
    result = tree.run()
    assert result.status == "catalog_skip" and not result.completed and tree.reader.calls == []
    held = tree.state.verify_state()
    assert "incomplete_streak" not in held, "a per-tree skip is benign"
    assert held["verify_catalog"] == SMOKE, "the record stays keyed to the catalog the tree was sent to"

    for name, reader in (("plain", None), ("smoke", SMOKE)):
        same = corpus(tmp_path / name)
        if reader is not None:
            _served(same, reader)
        assert same.run().completed and same.state.verify_state()["verify_catalog"] == SMOKE


def test_a_catalog_reset_never_reads_a_cached_session_from_the_old_catalog(tmp_path: Path) -> None:
    tree = _served(_recorded_in(ledger_tree(tmp_path / "ledger"), PROD), PROD)
    tree.state.save_verify_state(dict(OLD_FLOORS))
    result = tree.run()
    assert result.completed, result.status
    reads = [params["session_id"] for verb, params in tree.reader.calls if verb == "session_tree_rows"]
    assert reads and set(reads) == {ROOT_ID} and "missing_in_store" not in result.counts
    assert [verb for verb, _ in tree.reader.calls].count("telemetry_root_sessions") == 1
    held = tree.state.verify_state()
    assert (held["verify_catalog"], held["root_session_id"]) == (PROD, ROOT_ID)
    assert held["held_ledger_line"] == held["last_ledger_line"] < 999

    bare = _served(_recorded_in(corpus(tmp_path / "bare"), PROD), PROD)
    bare.state.save_verify_state(dict(OLD_FLOORS))
    assert bare.run().completed
    assert bare.state.verify_state() == {"incomplete_streak": 0, "root_session_id": ROOT_ID, "verify_catalog": PROD}

    operator = _served(_recorded_in(ledger_tree(tmp_path / "operator"), PROD), PROD)
    operator.state.save_verify_state(dict(OLD_FLOORS))
    assert operator.run(trigger="operator", scheduling_record=None, alarms=False).completed
    held = operator.state.verify_state()
    assert "verify_catalog" not in held and held["root_session_id"] == ROOT_ID


def test_a_lost_cursor_keeps_the_verify_state_of_the_catalog_it_was_recorded_under(tmp_path: Path) -> None:
    tree = _served(corpus(tmp_path / "lost"), PROD)
    tree.state.save_verify_state({**OLD_FLOORS, "verify_catalog": PROD})
    tree.state.state_file.unlink()
    assert tree.run().status == "no_cursor"
    held = tree.state.verify_state()
    assert held["verify_catalog"] == PROD and {name: held[name] for name in OLD_FLOORS} == OLD_FLOORS


def test_the_catalog_resolves_before_the_lock_and_only_with_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    acquire = CaptureState.acquire
    monkeypatch.setattr(CaptureState, "acquire", lambda self, wait: events.append("lock") or acquire(self, wait))

    offline = corpus(tmp_path / "offline")
    offline.reader = _Unresolved(captured_rows(offline.result), events)
    result = offline.run(credentials=lambda: False)
    assert result.status == "no_credentials" and "catalog" not in events and result.counts["root_calls"] > 0

    events.clear()
    tree = corpus(tmp_path / "failing")
    tree.reader = _Unresolved(captured_rows(tree.result), events)
    seeded = {"incomplete_streak": 2, "verify_catalog": PROD, "root_session_id": ROOT_ID}
    tree.state.save_verify_state(dict(seeded))
    result = tree.run()
    assert result.status == "verify_error:TelemetryCatalogUnresolved" and events == ["catalog", "lock"]
    assert result.counts["verify_incomplete"] == 1 and result.alarms == ["verify_incomplete"]
    held = tree.state.verify_state()
    assert held["incomplete_streak"] == 3 and held["retry_s"] == 2 * verify_due.VERIFY_RETRY_S
    assert (held["verify_catalog"], held["root_session_id"]) == (PROD, ROOT_ID) and tree.reader.calls == []
