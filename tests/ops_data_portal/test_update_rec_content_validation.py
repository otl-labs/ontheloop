"""Decision 131 mirror home for scripts/ops_data_portal.py's update_rec write-boundary changes in
PLAN-ops-portal-enforcement-gaps: dependencies is gated on update (rec-3307),
and update_rec's YAML-driven acceptance lint now requires a discriminating probe shape too,
matching file_rec (rec-3306 core).

Module-level test functions (not class-nested) so the node ids match the plan's Verification Plan
commands verbatim (tests/ops_data_portal/test_update_rec_content_validation.py::test_<name>).

The existing-row fixture is the shared reader-shaped, rule-clean factory in tests/fixtures/ops_rec_rows.py.

No `duckdb = pytest.importorskip("duckdb")` guard: _fetch_rec_from_reader / _ducklake_write /
_sync_table are mocked throughout, so this module never touches a real DuckLake connection and
scripts.ops_data_portal itself has no duckdb import at module scope -- see
tests/test_ops_data_portal_validators.py and test_acceptance_discrimination.py for the same
unguarded-import precedent (the guard would also break VP-step replay under
requirements-fast.txt, which carries no duckdb).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.fixtures.ops_rec_rows import reader_rec_row, writer_stand_in

_EXISTING = reader_rec_row()


def test_dependencies_reject_malformed_element(tmp_path: Path) -> None:
    """an update setting a malformed dependencies element is rejected through update_rec's
    write-time gate (rec-3307)."""
    recs_file = tmp_path / "recs.jsonl"
    with (
        patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(_EXISTING)),
        patch("scripts.ops_data_portal._ducklake_write") as mock_write,
        patch("scripts.ops_data_portal._sync_table"),
        patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
    ):
        from scripts.ops_data_portal import update_rec

        with pytest.raises(ValueError, match="dependencies"):
            update_rec("rec-4001", {"dependencies": ["rec-1", "nonsense"]})
    mock_write.assert_not_called()


def test_dependencies_accept_valid_list(tmp_path: Path) -> None:
    """The same write-time gate accepts a well-formed dependencies list through update_rec.

    array_element_reference resolves existence via its own make_reader/rec_by_id call (not the
    facade -- scripts.ops_portal may not import scripts.ops_data_portal, .importlinter
    no-cycles-ops-data-portal-executor), so a well-formed list also needs a reachable reader
    reporting both elements present."""
    from scripts.ops_portal.write_validators import _rec_exists_memo

    _rec_exists_memo.clear()
    recs_file = tmp_path / "recs.jsonl"
    fake_reader = MagicMock()
    fake_reader.named.return_value = [{"id": "present"}]
    with (
        patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(_EXISTING)),
        patch("src.common.ducklake_reader_client.make_reader", return_value=fake_reader),
        patch("scripts.ops_data_portal._ducklake_write", return_value={"ok": True}) as mock_write,
        patch("scripts.ops_data_portal._sync_table"),
        patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
    ):
        from scripts.ops_data_portal import update_rec

        result = update_rec("rec-4001", {"dependencies": ["rec-1", "rec-2"]})

    assert result is True
    mock_write.assert_called_once()
    _rec_exists_memo.clear()


def test_dependencies_reject_dangling_reference(tmp_path: Path) -> None:
    """FAILING-FIRST: update_rec rejects a dependencies element that is well-formed but names a
    rec id absent from the corpus (rec-3307 referential half, PLAN-dependency-referential-integrity).

    Referential existence resolves via its own make_reader/rec_by_id call, independent of the
    _fetch_rec_from_reader patch used for update_rec's own current-record fetch (see
    test_dependencies_accept_valid_list for why)."""
    from scripts.ops_portal.write_validators import _rec_exists_memo

    _rec_exists_memo.clear()
    recs_file = tmp_path / "recs.jsonl"
    fake_reader = MagicMock()
    fake_reader.named.return_value = []  # every dependency target is absent

    with (
        patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(_EXISTING)),
        patch("src.common.ducklake_reader_client.make_reader", return_value=fake_reader),
        patch("scripts.ops_data_portal._ducklake_write") as mock_write,
        patch("scripts.ops_data_portal._sync_table"),
        patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
    ):
        from scripts.ops_data_portal import update_rec

        with pytest.raises(ValueError, match="dependencies"):
            update_rec("rec-4001", {"dependencies": ["rec-999999"]})
    mock_write.assert_not_called()
    _rec_exists_memo.clear()


def test_dependency_on_closed_rec_is_accepted(tmp_path: Path) -> None:
    """INVARIANT: a dependency naming a CLOSED rec is accepted -- existence resolves against the
    full corpus via rec_by_id, not the open set (guards the backlog-health Class 3 mistake)."""
    from scripts.ops_portal.write_validators import _rec_exists_memo

    _rec_exists_memo.clear()
    recs_file = tmp_path / "recs.jsonl"
    fake_reader = MagicMock()
    fake_reader.named.return_value = [{"id": "rec-1", "status": "closed"}]

    with (
        patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(_EXISTING)),
        patch("src.common.ducklake_reader_client.make_reader", return_value=fake_reader),
        patch("scripts.ops_data_portal._ducklake_write", return_value={"ok": True}) as mock_write,
        patch("scripts.ops_data_portal._sync_table"),
        patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
    ):
        from scripts.ops_data_portal import update_rec

        result = update_rec("rec-4001", {"dependencies": ["rec-1"]})

    assert result is True
    mock_write.assert_called_once()
    _rec_exists_memo.clear()


def test_update_rec_rejects_nonconforming_tag(tmp_path: Path) -> None:
    """A non-conforming tag element is refused on update (Decision 181 clause 2). The tags format leg is dated, so the
    portal's update pre-check skips it; the writer binds it because the column changes. The shared fake writer runs the
    writer's own check_write with the stored row as prior and stores nothing on refusal."""
    recs_file = tmp_path / "recs.jsonl"
    fake_writer = writer_stand_in([_EXISTING])
    with (
        patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(_EXISTING)),
        patch("scripts.ops_data_portal._ducklake_write", fake_writer),
        patch("scripts.ops_data_portal._sync_table"),
        patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
    ):
        from scripts.ops_data_portal import update_rec

        with pytest.raises(ValueError, match="tags"):
            update_rec("rec-4001", {"tags": ["T2.56"]})
    assert fake_writer.writes == []


def test_legacy_nonconforming_tag_still_updates(tmp_path: Path) -> None:
    """Row-level grandfathering: an update touching another field on a rec that already carries a
    legacy non-conforming tag still succeeds, because tags is absent from that call's `updates`."""
    recs_file = tmp_path / "recs.jsonl"
    legacy = {**_EXISTING, "tags": ["T2.17"]}
    with (
        patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(legacy)),
        patch("scripts.ops_data_portal._ducklake_write", return_value={"ok": True}) as mock_write,
        patch("scripts.ops_data_portal._sync_table"),
        patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
    ):
        from scripts.ops_data_portal import update_rec

        result = update_rec("rec-4001", {"status": "closed"})

    assert result is True
    mock_write.assert_called_once()


def test_update_rec_acceptance_requires_discrimination(tmp_path: Path) -> None:
    """update_rec()'s YAML-driven acceptance_lint validator now requires a discriminating probe
    shape too (rec-3306 core; write_validators.py's _check_acceptance closure gains
    require_discrimination=True) -- the same rule file_rec already applies at its explicit call
    site. VP steps 4-7 guard this keystone change."""
    recs_file = tmp_path / "recs.jsonl"

    # A lone literal grep -- non-discriminating.
    with (
        patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(_EXISTING)),
        patch("scripts.ops_data_portal._ducklake_write") as mock_write,
        patch("scripts.ops_data_portal._sync_table"),
        patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
    ):
        from scripts.ops_data_portal import update_rec

        with pytest.raises(ValueError, match="does not discriminate"):
            update_rec("rec-4001", {"acceptance": "grep -q ops_data_portal scripts/ops_data_portal.py"})
    mock_write.assert_not_called()

    # A bare pytest path against an already-existing test file -- non-discriminating.
    with (
        patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(_EXISTING)),
        patch("scripts.ops_data_portal._ducklake_write") as mock_write2,
        patch("scripts.ops_data_portal._sync_table"),
        patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
    ):
        from scripts.ops_data_portal import update_rec

        with pytest.raises(ValueError, match="does not discriminate"):
            update_rec(
                "rec-4001",
                {"acceptance": "bin/venv-python -m pytest tests/test_executor_acceptance_lint.py -q"},
            )
    mock_write2.assert_not_called()

    # A chained second assertion -- genuinely discriminating -- must still be accepted.
    with (
        patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(_EXISTING)),
        patch("scripts.ops_data_portal._ducklake_write", return_value={"ok": True}) as mock_write3,
        patch("scripts.ops_data_portal._sync_table"),
        patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
    ):
        from scripts.ops_data_portal import update_rec

        result = update_rec(
            "rec-4001",
            {
                "acceptance": (
                    "grep -q ops_data_portal scripts/ops_data_portal.py && grep -q update_rec scripts/ops_data_portal.py"
                )
            },
        )
    assert result is True
    mock_write3.assert_called_once()


# -- Decision 186 closure-time artifact obligation (PLAN-escape-closure-obligation) -------------

_ESCAPE_EXISTING = {
    **_EXISTING,
    "id": "rec-5001",
    "status": "open",
    "context_v2_json": json.dumps({"escape_class": "no-edge"}),
}


def test_rec_3131_replay_is_refused(tmp_path: Path) -> None:
    """The historical rec-3131 case, replayed verbatim: status closed, resolution 'Duplicate of
    rec-3132...', no closure_artifact, no waiver. Refused -- this single refusal IS audit finding
    LSA-04, not a defect in the mechanism (Decision 186 Problem statement)."""
    existing = {
        **_EXISTING,
        "id": "rec-3131",
        "status": "open",
        "context_v2_json": json.dumps({"detection_gap": {"escape_mode": "tier_misplaced"}}),
    }
    recs_file = tmp_path / "recs.jsonl"
    with (
        patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(existing)),
        patch("scripts.ops_data_portal._ducklake_write") as mock_write,
        patch("scripts.ops_data_portal._sync_table"),
        patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
    ):
        from scripts.ops_data_portal import ClosureArtifactRequired, update_rec

        with pytest.raises(ClosureArtifactRequired):
            update_rec(
                "rec-3131",
                {"status": "closed", "resolution": "Duplicate of rec-3132, which carries the full RCA and is closed"},
            )
    mock_write.assert_not_called()


def test_named_artifact_absent_is_refused(tmp_path: Path) -> None:
    """A closure_artifact naming a shard id that does not exist in the tree is refused -- the
    write-boundary integration of the known-bad-fixture case."""
    recs_file = tmp_path / "recs.jsonl"
    with (
        patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(_ESCAPE_EXISTING)),
        patch("scripts.ops_data_portal._ducklake_write") as mock_write,
        patch("scripts.ops_data_portal._sync_table"),
        patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
    ):
        from scripts.ops_data_portal import ClosureArtifactRequired, update_rec

        with pytest.raises(ClosureArtifactRequired):
            update_rec(
                "rec-5001",
                {"status": "closed", "resolution": "fixed"},
                closure_artifact="shard:this-shard-id-does-not-exist-anywhere",
                closure_fix_sha="cafef00dcafef00dcafef00dcafef00cafef00d",
            )
    mock_write.assert_not_called()


def test_closing_write_that_blanks_context_is_still_refused(tmp_path: Path) -> None:
    """D-B3: a closing write that also blanks context_v2_json in the SAME update_rec call is
    still refused -- the predicate reads existing UNION merged, so the write cannot erase the
    classification that gates it (context_v2_json carries no contract rule and
    carries no monotonicity guard)."""
    recs_file = tmp_path / "recs.jsonl"
    with (
        patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(_ESCAPE_EXISTING)),
        patch("scripts.ops_data_portal._ducklake_write") as mock_write,
        patch("scripts.ops_data_portal._sync_table"),
        patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
    ):
        from scripts.ops_data_portal import ClosureArtifactRequired, update_rec

        with pytest.raises(ClosureArtifactRequired):
            update_rec("rec-5001", {"status": "closed", "context_v2_json": "{}"})
    mock_write.assert_not_called()


def test_open_to_declined_is_gated(tmp_path: Path) -> None:
    """D-B2: open -> declined is gated too (the bound set is {closed, declined, superseded}),
    closing the two-step open -> declined -> superseded route a closed-alone gate would leave
    open."""
    recs_file = tmp_path / "recs.jsonl"
    with (
        patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(_ESCAPE_EXISTING)),
        patch("scripts.ops_data_portal._ducklake_write") as mock_write,
        patch("scripts.ops_data_portal._sync_table"),
        patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
    ):
        from scripts.ops_data_portal import ClosureArtifactRequired, update_rec

        with pytest.raises(ClosureArtifactRequired):
            update_rec("rec-5001", {"status": "declined", "resolution": "not pursuing"})
    mock_write.assert_not_called()


def test_closure_fix_sha_stamps_fixed_by_sha_in_the_gated_write(tmp_path: Path) -> None:
    """closure_fix_sha stamps the EXISTING context_v2_json.fixed_by_sha key into the SAME merged
    payload the gate evaluates -- the artifact route is satisfiable in one call."""
    fix_sha = "cafef00dcafef00dcafef00dcafef00cafef00d"
    recs_file = tmp_path / "recs.jsonl"
    with (
        patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(_ESCAPE_EXISTING)),
        patch("scripts.ops_data_portal._ducklake_write", return_value={"ok": True}) as mock_write,
        patch("scripts.ops_data_portal._sync_table"),
        patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
        patch("scripts.ops_portal.closure_gate.resolve_closure_artifact") as mock_resolve,
    ):
        mock_resolve.return_value = True
        from scripts.ops_data_portal import update_rec

        result = update_rec(
            "rec-5001",
            {"status": "closed", "resolution": "fixed"},
            closure_artifact="pytest:tests/some_test.py::test_thing",
            closure_fix_sha=fix_sha,
        )

    assert result is True
    # the resolver was handed the SAME sha the written payload carries
    _, called_fix_sha = mock_resolve.call_args[0][:2]
    assert called_fix_sha == fix_sha
    _, written_rec = mock_write.call_args[0]
    written_ctx = json.loads(written_rec["context_v2_json"])
    assert written_ctx["fixed_by_sha"] == fix_sha


def test_closure_kwarg_on_a_rec_without_context_v2_json_refuses_before_any_write(tmp_path: Path) -> None:
    """The closure_* kwargs stamp INTO an existing context_v2_json blob; they never mint one.

    A rec with no context blob has nothing to stamp into, so supplying any closure_* kwarg for it
    is a caller error, not a silent no-op -- if it were tolerated the stamp would vanish and the
    gate would then evaluate a rec whose closure evidence was never recorded. update_rec raises
    before reaching _ducklake_write, so the refusal costs no write.
    """
    recs_file = tmp_path / "recs.jsonl"
    with (
        patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(_EXISTING)),
        patch("scripts.ops_data_portal._ducklake_write") as mock_write,
        patch("scripts.ops_data_portal._sync_table"),
        patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
    ):
        from scripts.ops_data_portal import update_rec

        assert not _EXISTING.get("context_v2_json")
        with pytest.raises(ValueError, match="closure_stamps_applicable.*no existing context_v2_json"):
            update_rec("rec-4001", {"status": "closed"}, closure_fix_sha="cafef00dcafef00d")

    mock_write.assert_not_called()


def test_closure_kwarg_on_a_blob_parsing_to_nothing_refuses_before_any_write(tmp_path: Path) -> None:
    """A context_v2_json blob that PARSES TO NOTHING ("{}", "null", or malformed JSON) refuses a
    closure_* kwarg exactly as an absent blob already does. A STRENGTHENING, never a relaxation
    (Decision 186 pt 8 is not engaged here -- the precondition's acceptance domain narrows, it
    does not widen): before this plan, "{}" was truthy under a bare `if not
    merged.get("context_v2_json")` check, so the stamp was accepted and {}.update(stamps) would
    have minted a valid blob -- violating the ratified never-mint-one contract this file's own
    docstring states (see test_closure_kwarg_on_a_rec_without_context_v2_json_refuses_before_any_write,
    :367-374). The parsed-dict predicate (closure_stamps_applicable) treats a blob that parses to
    nothing as equivalent to no blob at all."""
    recs_file = tmp_path / "recs.jsonl"
    for blob in ("{}", "null", "not json at all {"):
        existing = {**_EXISTING, "context_v2_json": blob}
        with (
            patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=existing),
            patch("scripts.ops_data_portal._ducklake_write") as mock_write,
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
        ):
            from scripts.ops_data_portal import update_rec

            with pytest.raises(ValueError, match="closure_stamps_applicable.*no existing context_v2_json"):
                update_rec("rec-4001", {"status": "closed"}, closure_fix_sha="cafef00dcafef00d")

        mock_write.assert_not_called()


def test_resolvable_artifact_closes(tmp_path: Path) -> None:
    """A closure supplying a resolvable artifact succeeds and writes."""
    recs_file = tmp_path / "recs.jsonl"
    with (
        patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(_ESCAPE_EXISTING)),
        patch("scripts.ops_data_portal._ducklake_write", return_value={"ok": True}) as mock_write,
        patch("scripts.ops_data_portal._sync_table"),
        patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
        patch("scripts.ops_portal.closure_gate.resolve_closure_artifact", return_value=True),
    ):
        from scripts.ops_data_portal import update_rec

        result = update_rec(
            "rec-5001",
            {"status": "closed", "resolution": "fixed"},
            closure_artifact="shard:some-shard",
            closure_fix_sha="cafef00dcafef00dcafef00dcafef00cafef00d",
        )

    assert result is True
    mock_write.assert_called_once()


def test_waived_closure_closes(tmp_path: Path) -> None:
    """A closure supplying a well-formed categorised waiver (category is a member of
    WAIVER_CATEGORIES, reason non-empty) succeeds without any artifact -- no fix commit needed."""
    recs_file = tmp_path / "recs.jsonl"
    with (
        patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(_ESCAPE_EXISTING)),
        patch("scripts.ops_data_portal._ducklake_write", return_value={"ok": True}) as mock_write,
        patch("scripts.ops_data_portal._sync_table"),
        patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
    ):
        from scripts.ops_data_portal import update_rec

        result = update_rec(
            "rec-5001",
            {"status": "closed", "resolution": "environment flake, not reproducible"},
            closure_waiver_category="environment_only",
            closure_waiver_reason="sandbox-only failure with no premerge signal; see run 12345",
        )

    assert result is True
    mock_write.assert_called_once()


def test_duplicate_citing_master_artifact_closes(tmp_path: Path) -> None:
    """A duplicate closure passes only by citing a FIX-BOUND closure_artifact token (never a rec
    id) -- no promise transfer, no rec-to-rec pointer resolved, no reader egress added."""
    recs_file = tmp_path / "recs.jsonl"
    with (
        patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(_ESCAPE_EXISTING)),
        patch("scripts.ops_data_portal._ducklake_write", return_value={"ok": True}) as mock_write,
        patch("scripts.ops_data_portal._sync_table"),
        patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
        patch("scripts.ops_portal.closure_gate.resolve_closure_artifact", return_value=True) as mock_resolve,
    ):
        from scripts.ops_data_portal import update_rec

        result = update_rec(
            "rec-5001",
            {"status": "closed", "resolution": "Duplicate of rec-9999, which carries the full RCA and is closed"},
            closure_artifact="check:validate_workflow_agent_safety",
            closure_fix_sha="cafef00dcafef00dcafef00dcafef00cafef00d",
        )

    assert result is True
    mock_write.assert_called_once()
    # the token resolved is the CLASS artifact, never a rec id
    called_token = mock_resolve.call_args[0][0]
    assert called_token == "check:validate_workflow_agent_safety"


def test_status_preserving_write_on_closed_escape_rec_is_not_gated(tmp_path: Path) -> None:
    """A status-preserving write on an already-closed escape rec (stamp_fixed_by_sha, occurrence
    bump, context correction) is NOT gated -- 'terminal' is defined on the FROM side, and the
    obligation was already discharged when the rec first closed."""
    already_closed = {
        **_EXISTING,
        "id": "rec-5001",
        "status": "closed",
        "context_v2_json": json.dumps({"escape_class": "no-edge"}),
    }
    recs_file = tmp_path / "recs.jsonl"
    with (
        patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(already_closed)),
        patch("scripts.ops_data_portal._ducklake_write", return_value={"ok": True}) as mock_write,
        patch("scripts.ops_data_portal._sync_table"),
        patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
    ):
        from scripts.ops_data_portal import update_rec

        result = update_rec(
            "rec-5001",
            {"context_v2_json": json.dumps({"escape_class": "no-edge", "fixed_by_sha": "abc1234"})},
        )

    assert result is True
    mock_write.assert_called_once()
