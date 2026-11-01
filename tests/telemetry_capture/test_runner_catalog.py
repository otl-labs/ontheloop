"""Mirror tests for the catalog rules of scripts/telemetry_capture/runner.py: RN-5's pairing, RN-9's reset and RN-10's confirm.

Capture follows the catalog the deployed writer's describe names. A fresh Writer stands for the new catalog's empty store.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from scripts.telemetry_capture import runner, verify_due
from scripts.telemetry_capture.transport import Budget
from src.turn_capture.record_turn import PinOrderError
from tests.fixtures.telemetry_capture_writer import Writer, factory, records, run, state, write
from tests.fixtures.turn_capture_corpus import make_files

SMOKE, PROD = "ducklake_smoke", "ducklake_telemetry"
PINS = ("root_session_ref", "project_ref", "billing_shape", "session_started_at_ms")


def _prod() -> Writer:
    return Writer(meta_schema=PROD, data_path_is_smoke=False)


def _sent(writer: Writer) -> list[tuple[str, str]]:
    return [
        (batch["table"], row["external_ref"])
        for post in writer.appends()
        for batch in post["batches"]
        for row in batch["rows"]
    ]


def _envelope(tmp_path: Path) -> dict[str, Any]:
    return json.loads(state(tmp_path).state_file.read_text(encoding="utf-8"))


def _smoke_tree(tmp_path: Path) -> Writer:
    """A finalized tree that later grew, sent to smoke over two passes, then grown again so the next pass has rows."""
    smoke = Writer()
    write(tmp_path, make_files(records(1)))
    assert run(tmp_path, smoke, final=True).outcome == "sent"
    write(tmp_path, make_files(records(2)))
    assert run(tmp_path, smoke).outcome == "sent"
    assert _envelope(tmp_path)["catalog"] == SMOKE and _envelope(tmp_path)["cursor"]["finalize_cuts"]
    write(tmp_path, make_files(records(3)))
    return smoke


@pytest.mark.parametrize(
    ("meta_schema", "data_path_is_smoke", "accepted"),
    [
        (SMOKE, True, SMOKE),
        (PROD, False, PROD),
        (SMOKE, False, None),
        (PROD, True, None),
        ("ducklake_ops", True, None),
        ("ducklake_ops", False, None),
        (None, True, None),
    ],
)
def test_describe_accepts_only_a_known_catalog_with_its_paired_data_path(
    meta_schema: str | None, data_path_is_smoke: bool, accepted: str | None
) -> None:
    writer = Writer(meta_schema=meta_schema, data_path_is_smoke=data_path_is_smoke)  # type: ignore[arg-type]
    client = factory(writer)({}, None, Budget.start(60.0))
    if accepted is None:
        with pytest.raises(runner._Stop) as stopped:
            runner._describe(client)
        assert (stopped.value.outcome, stopped.value.reason, stopped.value.halt) == ("stopped", "describe_mismatch", None)
    else:
        protocol, catalog = runner._describe(client)
        assert catalog == accepted and protocol.max_items == writer.max_items


def test_a_mispaired_describe_stops_without_halting(tmp_path: Path) -> None:
    _smoke_tree(tmp_path)
    before = state(tmp_path).state_file.read_bytes()
    mispaired = Writer(meta_schema=PROD, data_path_is_smoke=True)
    result = run(tmp_path, mispaired)
    assert result.outcome == "stopped" and result.diag["diag"]["describe_mismatch"] == 1
    assert state(tmp_path).halt() is None and mispaired.appends() == []
    assert state(tmp_path).state_file.read_bytes() == before


@pytest.mark.parametrize("make", [Writer, _prod])
def test_a_pass_records_the_catalog_describe_answered(tmp_path: Path, make: Any) -> None:
    writer = make()
    root = write(tmp_path, make_files(records(1)))
    assert run(tmp_path, writer).outcome == "sent"
    loaded = state(tmp_path).load_cursor(root.read_bytes())
    assert loaded.valid and loaded.catalog == writer.meta_schema == _envelope(tmp_path)["catalog"]
    assert loaded.reset_at is None and "reset_at" not in _envelope(tmp_path)


def test_a_catalog_change_resends_the_whole_tree_keeping_pins(tmp_path: Path) -> None:
    smoke = _smoke_tree(tmp_path)
    held = _envelope(tmp_path)["cursor"]
    prod = _prod()
    result = run(tmp_path, prod)
    assert result.outcome == "sent" and result.diag["diag"]["catalog_reset"] == 1
    sent = _sent(prod)
    assert len(sent) == len(set(sent)) == len(prod.rows) > len(smoke.rows)
    assert all(prod.rows[key] == row for key, row in smoke.rows.items())
    envelope = _envelope(tmp_path)
    assert envelope["catalog"] == PROD and envelope["reset_at"] <= time.time()
    assert envelope["cursor"]["finalize_cuts"] == held["finalize_cuts"]
    assert {pin: envelope["cursor"][pin] for pin in PINS} == {pin: held[pin] for pin in PINS}
    again = run(tmp_path, prod)
    assert again.outcome == "no_rows" and _sent(prod) == sent
    back = run(tmp_path, smoke, final=True)
    assert back.diag["diag"]["catalog_reset"] == 1 and smoke.rows == {**prod.rows, **smoke.rows}


def test_a_conflict_after_a_catalog_reset_halts_with_its_own_reason(tmp_path: Path) -> None:
    smoke = _smoke_tree(tmp_path)
    prod = _prod()
    key = next(iter(smoke.rows))
    prod.rows[key] = {**smoke.rows[key], "name": "diverged"}
    before = state(tmp_path).state_file.read_bytes()
    result = run(tmp_path, prod)
    assert result.outcome == "halted" and result.diag["diag"]["grain_conflict_after_catalog_reset"] == 1
    assert state(tmp_path).halt()["reason"] == "grain_conflict_after_catalog_reset"
    assert state(tmp_path).state_file.read_bytes() == before


def test_a_producer_error_on_the_whole_tree_resend_halts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _smoke_tree(tmp_path)
    record = runner._record

    def resend_fails(*args: Any, resend_tree: bool) -> Any:
        if resend_tree:
            raise PinOrderError("an earlier record surfaced on the whole-tree parse")
        return record(*args, resend_tree=resend_tree)

    monkeypatch.setattr(runner, "_record", resend_fails)
    before = state(tmp_path).state_file.read_bytes()
    prod = _prod()
    result = run(tmp_path, prod)
    assert result.outcome == "halted" and result.diag["diag"]["catalog_reset"] == 1 and prod.appends() == []
    assert state(tmp_path).halt() == {"reason": "producer_error", "error_class": "PinOrderError"}
    assert state(tmp_path).state_file.read_bytes() == before


def test_a_no_rows_pass_keeps_the_recorded_catalog(tmp_path: Path) -> None:
    prod = _prod()
    write(tmp_path, make_files(records(1)))
    assert run(tmp_path, prod).outcome == "sent"
    st = state(tmp_path)
    envelope = {**_envelope(tmp_path), "reset_at": 1.0}
    st.state_file.write_text(json.dumps(envelope), encoding="utf-8")
    smoke = Writer()
    result = run(tmp_path, smoke)
    assert result.outcome == "no_rows" and smoke.posts == []
    assert _envelope(tmp_path)["catalog"] == PROD and _envelope(tmp_path)["reset_at"] == 1.0


def test_a_catalog_reset_pass_schedules_a_check(tmp_path: Path) -> None:
    _smoke_tree(tmp_path)
    st = state(tmp_path)
    result = run(tmp_path, _prod())
    assert result.outcome == "sent" and result.diag["verify_due"] is True
    held = st.verify_state()
    assert held["scheduled_at"] >= result.diag["at"] and held["checks_by_day"][verify_due.utc_day(time.time())] == 1

    capped = tmp_path / "capped"
    capped.mkdir()
    _smoke_tree(capped)
    today = verify_due.utc_day(time.time())
    state(capped).save_verify_state({"checks_by_day": {today: verify_due.MAX_CHECKS_PER_TREE_PER_DAY}})
    result = run(capped, _prod())
    assert result.outcome == "sent" and result.diag["diag"]["catalog_reset"] == 1 and result.diag["verify_due"] is False
    assert "scheduled_at" not in state(capped).verify_state()


def test_a_catalog_reset_under_a_detach_budget_detaches(tmp_path: Path) -> None:
    _smoke_tree(tmp_path)
    before = state(tmp_path).state_file.read_bytes()
    prod = _prod()
    result = run(tmp_path, prod, final=True, detach_below=1.0)
    assert result.outcome == "short_budget" and result.diag["diag"]["catalog_reset_detached"] == 1
    assert [post["action"] for post in prod.posts] == ["describe"] and prod.appends() == []
    assert state(tmp_path).state_file.read_bytes() == before and _envelope(tmp_path)["catalog"] == SMOKE
    resent = run(tmp_path, prod, final=True)
    assert resent.outcome == "sent" and resent.diag["diag"]["catalog_reset"] == 1 and _envelope(tmp_path)["catalog"] == PROD


def test_a_due_confirm_under_a_detach_budget_detaches(tmp_path: Path) -> None:
    _smoke_tree(tmp_path)
    prod = _prod()
    assert run(tmp_path, prod).diag["diag"]["catalog_reset"] == 1
    st = state(tmp_path)
    st.state_file.write_text(
        json.dumps({**_envelope(tmp_path), "reset_at": time.time() - runner.RESET_CONFIRM_AFTER_S - 1}), encoding="utf-8"
    )
    write(tmp_path, make_files(records(4)))
    before, sent = st.state_file.read_bytes(), len(_sent(prod))
    detached = run(tmp_path, prod, final=True, detach_below=1.0)
    assert detached.outcome == "short_budget" and detached.diag["diag"]["catalog_reset_detached"] == 1
    assert len(_sent(prod)) == sent and st.state_file.read_bytes() == before
    confirm = run(tmp_path, prod, final=True)
    assert confirm.outcome == "sent" and confirm.diag["diag"]["catalog_reset_confirm"] == 1
    assert "reset_at" not in _envelope(tmp_path)


def test_a_reset_is_confirmed_by_one_whole_tree_resend_after_the_window(tmp_path: Path) -> None:
    _smoke_tree(tmp_path)
    prod = _prod()
    assert run(tmp_path, prod).diag["diag"]["catalog_reset"] == 1
    reset_at = _envelope(tmp_path)["reset_at"]
    assert reset_at <= time.time()

    write(tmp_path, make_files(records(4)))
    sent = len(_sent(prod))
    inside = run(tmp_path, prod)
    new_rows = sum(inside.diag["rows"].values())
    assert inside.outcome == "sent" and "catalog_reset_confirm" not in inside.diag["diag"]
    assert len(_sent(prod)) - sent == new_rows < len(prod.rows) and _envelope(tmp_path)["reset_at"] == reset_at

    dropped = next(key for key in prod.rows if key[0] == "telemetry_observations")
    del prod.rows[dropped]
    st = state(tmp_path)
    st.state_file.write_text(
        json.dumps({**_envelope(tmp_path), "reset_at": time.time() - runner.RESET_CONFIRM_AFTER_S - 1}), encoding="utf-8"
    )
    write(tmp_path, make_files(records(5)))
    sent = len(_sent(prod))
    confirm = run(tmp_path, prod)
    assert confirm.outcome == "sent" and confirm.diag["diag"]["catalog_reset_confirm"] == 1
    assert len(_sent(prod)) - sent == len(prod.rows) and dropped in prod.rows
    assert "reset_at" not in _envelope(tmp_path) and _envelope(tmp_path)["catalog"] == PROD

    write(tmp_path, make_files(records(6)))
    sent = len(_sent(prod))
    after = run(tmp_path, prod)
    assert after.outcome == "sent" and "catalog_reset_confirm" not in after.diag["diag"]
    assert len(_sent(prod)) - sent == sum(after.diag["rows"].values()) < len(prod.rows)
