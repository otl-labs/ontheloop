#!/usr/bin/env python3
"""D4 cache-serving unit tests for scripts/session/preflight.py (neon-egress-reduction).

Two properties (VP step 1):

(a) Absence-of-call -- with a fresh warm-sync cache, the Phase-B signals are computed locally and the
    reader records ZERO named()/current_state() calls in Phase B (only the warm-up sync touches it).

(b) Equivalence -- each client-side derivation EQUALS what the corresponding ducklake_reader named
    verb returns for the SAME seeded data. The verb's canonical SQL (NAMED_READS) is executed over a
    fixture in an in-process DuckDB so the assertion is against the real SQL, not a paraphrase. The
    fixtures are deliberately built to expose the traps the plan calls out: forward_fix_recursion's
    same-file/24h window, ci_rca ordering+limit, the 7-day budget window with a >10 overflow, and the
    priority_queue_current Decision-70 "ALL entries of the LATEST curator run" semantics.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import duckdb
import pytest

from scripts.preflight import _common, aws_infra, ci_rca_signals, env_git
from scripts.roadmap import platform_roadmap
from src.common.ducklake_scd2_schema import NAMED_READS
from tests.fixtures.platform_roadmap_state import live_state_dict

# Load the module under test as a PRIVATE handle. Deliberately NOT registered as
# sys.modules["session_preflight"] -- the sibling suite (test_session_preflight.py) registers that
# name, and clobbering it makes its string-target patches ("session_preflight.<sym>") resolve to the
# wrong module object. Moved-symbol patches target their home module directly (patch.object(env_git,
# ...), patch.object(_common, ...), etc.) -- the same canonical target regardless of which alias this
# file imports the facade under. Only the facade-resident PREFLIGHT_REPORT still patches via
# patch.object(_preflight, ...), collision-free regardless of test ordering (pytest-randomly).
_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "session" / "preflight.py"
_spec = importlib.util.spec_from_file_location("session_preflight_cache_serving_mod", _MODULE_PATH)
assert _spec and _spec.loader
_preflight = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_preflight)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# DuckDB fixture helpers: load rows + run a verb's canonical SQL over them.
# ---------------------------------------------------------------------------

_RECS_COLS = (
    "id VARCHAR, title VARCHAR, context VARCHAR, created_timestamp TIMESTAMPTZ, "
    "automatable BOOLEAN, status VARCHAR, priority VARCHAR, source VARCHAR, file VARCHAR"
)
_RECS_FIELDS = ["id", "title", "context", "created_timestamp", "automatable", "status", "priority", "source", "file"]


def _utc(s: str) -> datetime:
    return (
        datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        if "+" not in s and "Z" not in s
        else datetime.fromisoformat(s.replace("Z", "+00:00"))
    )


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")  # naive TIMESTAMPTZ casts resolve as UTC (matches _parse_ts_utc)
    return con


def _load_recs(con: duckdb.DuckDBPyConnection, rows: list[dict]) -> None:
    con.execute(f"CREATE TABLE recs ({_RECS_COLS})")
    for r in rows:
        con.execute(
            "INSERT INTO recs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [r.get(f) for f in _RECS_FIELDS],
        )


def _run_verb(con: duckdb.DuckDBPyConnection, verb: str, table: str, params: list | None = None) -> list[dict]:
    sql = NAMED_READS[verb].sql.replace("{tbl}", table).replace("{hist}", table)
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _norm_ts(value: object) -> str | None:
    """Normalise a timestamp (datetime or ISO string) to a UTC ISO string for cross-engine compare."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    else:
        dt = _preflight._parse_ts_utc(str(value))
        if dt is None:
            return None
    return dt.astimezone(timezone.utc).isoformat()


# A recs fixture exercising every recs-derived verb at once. Distinct timestamps so DESC ordering is
# deterministic across DuckDB and Python (no tie-break ambiguity).
def _recs_fixture(now: datetime) -> list[dict]:
    def ts(days_ago: float) -> str:
        return (now - timedelta(days=days_ago)).isoformat()

    rows: list[dict] = []
    # open / closed / superseded mix for open_recs.
    rows += [
        {
            "id": "rec-003",
            "title": "c",
            "context": "x",
            "created_timestamp": ts(1),
            "automatable": True,
            "status": "open",
            "priority": "Low",
            "source": "manual",
            "file": "a.py",
        },
        {
            "id": "rec-001",
            "title": "a",
            "context": "x",
            "created_timestamp": ts(40),
            "automatable": False,
            "status": "open",
            "priority": "High",
            "source": "planning",
            "file": "b.py",
        },
        {
            "id": "rec-002",
            "title": "b",
            "context": "x",
            "created_timestamp": ts(2),
            "automatable": True,
            "status": "closed",
            "priority": "Low",
            "source": "manual",
            "file": "c.py",
        },
    ]
    # ci_rca recs: 6 open/in_progress (to exercise the LIMIT 5), plus same-file clustering for
    # forward_fix_recursion. file 'fwd.py' gets 3 within 24h (-> recursion); 'two.py' gets 2 (-> no).
    for i, days in enumerate([0.1, 0.2, 0.3]):  # 3 on fwd.py within 24h
        rows.append(
            {
                "id": f"rec-1{i:02d}",
                "title": f"ci{i}",
                "context": "x",
                "created_timestamp": ts(days),
                "automatable": True,
                "status": "open",
                "priority": "Critical",
                "source": "ci_rca",
                "file": "fwd.py",
            }
        )
    rows += [
        {
            "id": "rec-110",
            "title": "ci-old",
            "context": "x",
            "created_timestamp": ts(5),
            "automatable": True,
            "status": "open",
            "priority": "Critical",
            "source": "ci_rca",
            "file": "fwd.py",
        },  # OUTSIDE 24h window
        {
            "id": "rec-120",
            "title": "two-1",
            "context": "x",
            "created_timestamp": ts(0.4),
            "automatable": True,
            "status": "in_progress",
            "priority": "High",
            "source": "ci_rca",
            "file": "two.py",
        },
        {
            "id": "rec-121",
            "title": "two-2",
            "context": "x",
            "created_timestamp": ts(0.5),
            "automatable": True,
            "status": "open",
            "priority": "High",
            "source": "ci_rca",
            "file": "two.py",
        },
        {
            "id": "rec-130",
            "title": "closed-ci",
            "context": "x",
            "created_timestamp": ts(0.05),
            "automatable": True,
            "status": "closed",
            "priority": "High",
            "source": "ci_rca",
            "file": "closed.py",
        },  # closed -> not ci_rca_open
    ]
    # budget_bypass: 11 in the last 7 days (overflow LIMIT 10) + 1 older than 7 days (excluded).
    for i in range(11):
        rows.append(
            {
                "id": f"rec-2{i:02d}",
                "title": f"bb{i}",
                "context": "x",
                "created_timestamp": ts(0.1 * (i + 1)),
                "automatable": True,
                "status": "open",
                "priority": "Low",
                "source": "budget_bypass",
                "file": "x.py",
            }
        )
    rows.append(
        {
            "id": "rec-299",
            "title": "bb-old",
            "context": "x",
            "created_timestamp": ts(30),
            "automatable": True,
            "status": "open",
            "priority": "Low",
            "source": "budget_bypass",
            "file": "x.py",
        }
    )
    return rows


class TestVerbEquivalence:
    """Each cache-served derivation EQUALS the canonical verb SQL run over the same rows."""

    def test_open_recs_tally_equivalence(self) -> None:
        now = datetime.now(timezone.utc)
        rows = _recs_fixture(now)
        con = _con()
        _load_recs(con, rows)
        (tally,) = _run_verb(con, "open_recs_tally", "recs")
        counts = _preflight._count_recommendations_reader(tally)
        assert counts[0] == sum(1 for r in rows if r["status"] == "open")
        assert counts[2] == sum(1 for r in rows if r["status"] == "open" and not r["automatable"])

    def test_ci_rca_open_equivalence_with_limit(self) -> None:
        now = datetime.now(timezone.utc)
        rows = _recs_fixture(now)
        con = _con()
        _load_recs(con, rows)
        verb_rows = _run_verb(con, "ci_rca_open", "recs")
        derived = _preflight._derive_ci_rca_open(rows)
        assert len(verb_rows) == 5  # LIMIT 5 fires (6 open/in_progress ci_rca recs in the fixture)
        assert [r["id"] for r in derived] == [r["id"] for r in verb_rows]
        # Both projections must carry the `file` field (Decision 84 I-3 divergence guard).
        for row in derived:
            assert "file" in row, f"derive row {row['id']} missing 'file' key"
        for row in verb_rows:
            assert "file" in row, f"verb row {row['id']} missing 'file' key"

    def test_ci_rca_since_equivalence(self) -> None:
        now = datetime.now(timezone.utc)
        rows = _recs_fixture(now)
        since = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        con = _con()
        _load_recs(con, rows)
        verb_rows = _run_verb(con, "ci_rca_since", "recs", [since])
        derived = _preflight._derive_ci_rca_since(rows, since)
        assert sorted(r["id"] for r in derived) == sorted(r["id"] for r in verb_rows)

    def test_forward_fix_recursion_equivalence_same_file_window(self) -> None:
        """The 24h same-file window trap: only fwd.py (>=3 within 24h) is flagged, never two.py (2) or the old one."""
        now = datetime.now(timezone.utc)
        rows = _recs_fixture(now)
        since = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        con = _con()
        _load_recs(con, rows)
        verb_rows = _run_verb(con, "forward_fix_recursion", "recs", [since])
        derived = _preflight._derive_forward_fix_recursion(rows, since)
        assert {r["file"]: r["cnt"] for r in derived} == {r["file"]: r["cnt"] for r in verb_rows}
        assert {r["file"] for r in derived} == {"fwd.py"}  # two.py (2) excluded; the -5d ci_rca excluded

    def test_budget_bypass_recent_equivalence_with_window_and_limit(self) -> None:
        now = datetime.now(timezone.utc)
        rows = _recs_fixture(now)
        con = _con()
        _load_recs(con, rows)
        verb_rows = _run_verb(con, "budget_bypass_recent", "recs")
        derived = _preflight._derive_budget_bypass_recent(rows, now=now)
        assert len(verb_rows) == 10  # LIMIT 10 (11 within 7d) and the 30d-old one excluded
        assert [r["id"] for r in derived] == [r["id"] for r in verb_rows]

    def test_decisions_max_updated_equivalence(self) -> None:
        decisions = [
            {"id": "dec-001", "last_updated_timestamp": "2026-06-10T00:00:00+00:00"},
            {"id": "dec-002", "last_updated_timestamp": "2026-06-15T11:25:20.413542+00:00"},
            {"id": "dec-003", "last_updated_timestamp": "2026-06-12T00:00:00+00:00"},
        ]
        con = _con()
        con.execute("CREATE TABLE decs (id VARCHAR, last_updated_timestamp TIMESTAMPTZ)")
        for d in decisions:
            con.execute("INSERT INTO decs VALUES (?, ?)", [d["id"], d["last_updated_timestamp"]])
        verb_rows = _run_verb(con, "decisions_max_updated", "decs")
        derived = _preflight._derive_decisions_max_updated(decisions)
        assert _norm_ts(derived[0]["ts"]) == _norm_ts(verb_rows[0]["ts"])

    def test_priority_queue_current_is_served_not_rederived(self) -> None:
        """Decision-70 trap: the cache IS priority_queue_current's output (the warm-up sync pulled it via
        the verb), so read_priority_queue only SHAPES it -- the latest-run selection is never re-derived
        client-side. We seed two runs, run the verb to get the latest-run rows, and confirm feeding THOSE
        to read_priority_queue yields exactly the latest run's entries (no leakage of the older run)."""
        con = _con()
        con.execute(
            "CREATE TABLE pq (queue_run_id VARCHAR, last_updated_timestamp TIMESTAMPTZ, rank INTEGER, "
            "rec_id VARCHAR, rationale VARCHAR, north_star_impact VARCHAR)"
        )
        seed = [
            ("run-old", "2026-06-01T00:00:00+00:00", 1, "rec-900", "old-1", "n"),
            ("run-old", "2026-06-01T00:00:00+00:00", 2, "rec-901", "old-2", "n"),
            ("run-new", "2026-06-15T00:00:00+00:00", 2, "rec-911", "new-2", "n"),
            ("run-new", "2026-06-15T00:00:00+00:00", 1, "rec-910", "new-1", "n"),
        ]
        for s in seed:
            con.execute("INSERT INTO pq VALUES (?, ?, ?, ?, ?, ?)", list(s))
        verb_rows = _run_verb(con, "priority_queue_current", "pq")
        # The verb returns ONLY the latest run, rank-ordered.
        assert [r["rec_id"] for r in verb_rows] == ["rec-910", "rec-911"]
        shaped = _preflight.read_priority_queue(cache_rows=verb_rows)
        assert [r["rec_id"] for r in shaped] == ["rec-910", "rec-911"]
        assert all(r["rec_id"].startswith("rec-91") for r in shaped)  # no old-run leakage


class TestPhaseBAbsenceOfReaderCalls:
    """main() issues the two bounded recs reads (Decision 215), one creds-gated telemetry gauge call and one
    sessions_window call; Phase B adds ZERO."""

    def test_main_phase_b_makes_no_reader_calls(self, tmp_path: Path) -> None:
        verb_calls: dict[str, int] = {}
        reader = MagicMock()

        def _named(verb: str, **kwargs: object) -> list:
            verb_calls[verb] = verb_calls.get(verb, 0) + 1
            if verb == "open_recs_tally":
                return [{"open_count": 2, "aging_count": 0, "non_automatable_count": 0}]
            return _recs_fixture(datetime.now(timezone.utc)) if verb == "preflight_signal_rows" else []

        reader.named.side_effect = _named
        reader.current_state.side_effect = lambda *a, **k: (
            verb_calls.__setitem__("current_state", verb_calls.get("current_state", 0) + 1) or []
        )

        warm = {
            "drained": {},
            "pulled": {"ops_decisions": 1, "ops_priority_queue": 0},
            "rows": {
                "ops_decisions": [{"id": "dec-001", "last_updated_timestamp": "2026-06-12T00:00:00+00:00"}],
                "ops_priority_queue": [],
            },
            "reader_ok": {"ops_decisions": True, "ops_priority_queue": True},
        }

        freshness = {
            "status": "ok",
            "fetched_at": "2026-06-15T00:00:00+00:00",
            "commits_behind": 0,
            "commits_ahead": 0,
            "main_files_changed_since_branch": [],
        }
        with (
            patch.object(_common, "_make_reader", return_value=reader),
            patch.object(_common, "_make_event_reader", return_value=reader),
            patch("scripts.sync.ops.warm_sync", return_value=warm),
            patch.object(env_git, "check_venv", return_value=True),
            patch.object(env_git, "get_git_status", return_value=("agent/test", False, [])),
            patch.object(env_git, "check_main_freshness", return_value=freshness),
            patch.object(aws_infra, "check_terraform_pending", return_value=False),
            patch.object(aws_infra, "check_credentials", return_value="ok"),
            patch.object(env_git, "_get_recent_main_commits", return_value=[]),
            patch.object(env_git, "run_log_sync", return_value={"status": "skipped", "files": []}),
            patch.object(ci_rca_signals, "_check_ci_rca_liveness", return_value=None),
            patch.object(platform_roadmap, "compute_state_dict", new=live_state_dict),
            patch.object(_preflight, "PREFLIGHT_REPORT", tmp_path / ".preflight-report.json"),
            patch("builtins.print"),
        ):
            _preflight.main()

        assert verb_calls == {
            "open_recs_tally": 1,
            "preflight_signal_rows": 1,
            "telemetry_health_count": 1,
            "sessions_window": 1,
        }, verb_calls


class TestLiveDuckLakeCiRcaOpenFile:
    """RUN_LIVE_DUCKLAKE=1 gated: prove the DEPLOYED reader's ci_rca_open verb returns `file`."""

    @pytest.mark.enable_socket()
    def test_live_file_ci_rca_open_returns_file(self) -> None:
        """File a throwaway ci_rca rec with a known `file` value, read it back via the deployed
        ci_rca_open verb, assert `file` is populated and correct, then close the rec (self-cleaning,
        Decision 70). Skipped unless RUN_LIVE_DUCKLAKE=1."""
        if not os.environ.get("RUN_LIVE_DUCKLAKE"):
            pytest.skip("set RUN_LIVE_DUCKLAKE=1 to run live DuckLake roundtrip")

        import scripts.ops_data_portal as p
        from src.common.ducklake_reader_client import make_reader

        marker_file = "scripts/ci_rca/tier_map.py"
        rec_id = p.file_rec(
            {
                "title": "test_live_file_ci_rca_open_returns_file (ci-rca-likely-resolved-detection VP7)",
                "context": (
                    "Live smoke test verifying the deployed ci_rca_open verb returns the `file` column. "
                    "This rec will be immediately closed (self-cleaning, Decision 70). "
                    "Filed by TestLiveDuckLakeCiRcaOpenFile."
                ),
                "acceptance": "`file` key present in ci_rca_open row for this rec",
                "effort": "XS",
                "status": "open",
                "priority": "Low",
                "source": "ci_rca",
                "file": marker_file,
                "automatable": False,
            }
        )
        assert rec_id.startswith("rec-"), f"Expected rec-NNN, got {rec_id!r}"

        try:
            reader = make_reader(profile="agent_platform")
            rows = reader.named("ci_rca_open")
            matched = [r for r in rows if r.get("id") == rec_id]
            assert matched, f"{rec_id} not found in ci_rca_open rows: {[r.get('id') for r in rows]}"
            assert "file" in matched[0], f"ci_rca_open verb did not return `file` for {rec_id}: {matched[0]}"
            assert matched[0]["file"] == marker_file, f"Expected file={marker_file!r}, got {matched[0]['file']!r} for {rec_id}"
        finally:
            p.update_rec(
                rec_id,
                {
                    "status": "closed",
                    "resolution": "test_live_file_ci_rca_open_returns_file self-cleaning close (VP7)",
                },
            )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
