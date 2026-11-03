"""VERB_FIELDS <-> NAMED_READS SQL parity (rec-3563 substrate, Decision 84 I-3).

Every fixed-column VERB_FIELDS entry is proven against the column set its verb's REGISTERED SQL
actually produces, executed in an in-memory DuckDB -- never compared to a second hand-copied list.
The four SELECT * verbs resolve through resolve_table_spec(table).ordered_columns instead: a bare
`SELECT *` against a table this test built itself would prove nothing about the real ops_tables
contract, only that this test is internally consistent.

COPIES (never imports) the DuckDB verb-execution harness pattern from
tests/test_session_preflight_cache_serving.py (_con/_run_verb, ~lines 65-85):
validate_no_cross_test_imports forbids importing any module whose final dotted component starts
with test_, and tests/fixtures/ducklake_fakes.py's FakeCon is a connection double that cannot
execute SQL.
"""

from __future__ import annotations

import pytest

duckdb = pytest.importorskip("duckdb")

from src.common.ducklake_event_read_verbs import EVENT_READS  # noqa: E402
from src.common.ducklake_reader_client import _NAMED_READS_VERSION_PIN, ALL_TABLE_COLUMNS, VERB_FIELDS  # noqa: E402
from src.common.ducklake_scd2_schema import NAMED_READS, NAMED_READS_VERSION, ScdTableSpec, resolve_table_spec  # noqa: E402

# The four SELECT * verbs: their VERB_FIELDS entry is the ALL_TABLE_COLUMNS sentinel, resolved
# through the table spec below rather than hand-copied (see module docstring).
_SELECT_STAR_VERBS = frozenset({"rec_by_id", "recs_by_ids", "rec_history", "decision_by_id", "priority_queue_current"})

# Only `since_ts` binds need a value CAST-able to TIMESTAMPTZ; every other bound param is a plain
# string equality/LIKE/lookup value, so any non-empty string discriminates without erroring. recs_related's
# near/terms binds are "key|weight" pairs (CAST to DOUBLE) and max_rows is CAST to INTEGER.
_PARAM_VALUES = {"since_ts": "2024-01-01T00:00:00+00:00", "near": "a.py|1", "terms": "x|1", "max_rows": "5"}


def _con() -> "duckdb.DuckDBPyConnection":
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    return con


def _create_table(con: "duckdb.DuckDBPyConnection", table_name: str, spec: ScdTableSpec) -> None:
    ddl = ", ".join(f"{name} {sql_type}" for name, sql_type in spec.ordered_columns)
    con.execute(f"CREATE TABLE {table_name} ({ddl})")


def _run_verb_sql(con: "duckdb.DuckDBPyConnection", verb: str, table_name: str, params: list) -> list[str]:
    """Execute *verb*'s registered SQL (both {tbl}/{hist} pointed at *table_name*); return its column names."""
    sql = NAMED_READS[verb].sql.replace("{tbl}", table_name).replace("{hist}", table_name)
    cur = con.execute(sql, params)
    return [d[0] for d in cur.description]


@pytest.mark.parametrize("verb", sorted(NAMED_READS))
def test_verb_fields_match_registry_sql(verb: str) -> None:
    nr = NAMED_READS[verb]
    spec = resolve_table_spec(nr.table)
    con = _con()
    _create_table(con, "t", spec)
    params = [_PARAM_VALUES.get(p, "x") for p in nr.params]
    actual_columns = _run_verb_sql(con, verb, "t", params)

    if verb in _SELECT_STAR_VERBS:
        assert VERB_FIELDS[verb] is ALL_TABLE_COLUMNS, f"{verb}: expected the ALL_TABLE_COLUMNS sentinel"
        expected = [name for name, _ in spec.ordered_columns]
    else:
        assert VERB_FIELDS[verb] is not ALL_TABLE_COLUMNS, f"{verb}: unexpected ALL_TABLE_COLUMNS sentinel"
        expected = list(VERB_FIELDS[verb])

    assert actual_columns == expected, f"{verb}: VERB_FIELDS {expected} != registry SQL columns {actual_columns}"


def test_verb_fields_covers_every_registered_verb() -> None:
    registered = set(NAMED_READS) | set(EVENT_READS)
    assert set(VERB_FIELDS) == registered, (
        f"VERB_FIELDS/registry drift: missing={registered - set(VERB_FIELDS)} extra={set(VERB_FIELDS) - registered}"
    )


def test_version_pin_matches_registry() -> None:
    assert _NAMED_READS_VERSION_PIN == NAMED_READS_VERSION


class TestRecsReadModelParity:
    """The bounded preflight read model supplies every Phase-B derivation the full rows did (rec-4232)."""

    def _db(self, rows: list[dict]):
        spec = resolve_table_spec("ops_recommendations")
        con = _con()
        _create_table(con, "t", spec)
        names = [n for n, _ in spec.ordered_columns]
        for i, row in enumerate(rows):
            full = {"ulid": f"u{i}", **row}
            con.execute(
                f"INSERT INTO t ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})",
                [full.get(n) for n in names],
            )
        return con

    def _verb(self, con, verb: str, *params) -> list[dict]:
        sql = NAMED_READS[verb].sql.replace("{tbl}", "t")
        cur = con.execute(sql, list(params))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    @staticmethod
    def _ago(**kw):
        from datetime import datetime, timedelta, timezone

        return datetime.now(timezone.utc) - timedelta(**kw)

    def _fixture(self) -> list[dict]:
        ago = self._ago
        return [
            {"id": "rec-1", "status": "open", "automatable": True, "created_timestamp": ago(days=5), "source": "agent"},
            {"id": "rec-2", "status": "open", "automatable": False, "created_timestamp": ago(days=40), "source": "agent"},
            {"id": "rec-3", "status": "open", "automatable": None, "created_timestamp": ago(days=31, hours=1)},
            {"id": "rec-4", "status": "open", "automatable": False, "created_timestamp": ago(days=30, hours=23)},
            {"id": "rec-5", "status": "closed", "automatable": False, "created_timestamp": ago(days=90)},
            {"id": "rec-6", "status": "open", "automatable": True, "created_timestamp": None},
        ]

    def test_open_recs_tally_equals_python_derivation(self):
        from scripts.preflight.recs_cache import _tally_rec_counts

        rows = self._fixture()
        con = self._db(rows)
        (tally,) = self._verb(con, "open_recs_tally")
        open_rows = [dict(r) for r in rows if r["status"] == "open"]
        expected = _tally_rec_counts(open_rows)[:3]
        assert (tally["open_count"], tally["aging_count"], tally["non_automatable_count"]) == expected == (5, 2, 2)

    def test_open_recs_tally_over_no_open_rows_is_zeroes(self):
        con = self._db([{"id": "rec-1", "status": "closed"}])
        assert self._verb(con, "open_recs_tally") == [{"open_count": 0, "aging_count": 0, "non_automatable_count": 0}]

    def test_signal_rows_null_heavy_columns_on_old_closed_rows_but_keep_context_v2(self):
        ago = self._ago
        base = {"context": "ctx", "acceptance": "acc", "context_v2_json": '{"preventive_action": "p"}'}
        rows = [
            {"id": "rec-old", "status": "closed", "source": "ci_rca", "created_timestamp": ago(days=400), **base},
            {"id": "rec-new", "status": "closed", "source": "ci_rca", "created_timestamp": ago(days=3), **base},
            {"id": "rec-open", "status": "open", "source": "tf_drift", "created_timestamp": ago(days=400), **base},
            {
                "id": "rec-crit",
                "status": "open",
                "priority": "Critical",
                "source": "agent",
                "created_timestamp": ago(days=1),
                **base,
            },
            {
                "id": "rec-plain",
                "status": "open",
                "source": "agent",
                "priority": "low",
                "created_timestamp": ago(days=1),
                **base,
            },
            {"id": "rec-fo", "status": "open", "source": "agent", "priority": "low", "created_timestamp": ago(days=1), **base},
            {"id": "rec-fo-closed", "status": "closed", "source": "agent", "created_timestamp": ago(days=1), **base},
        ]
        got = {r["id"]: r for r in self._verb(self._db(rows), "preflight_signal_rows", "rec-fo,rec-fo-closed")}
        assert set(got) == {"rec-old", "rec-new", "rec-open", "rec-crit", "rec-fo"}
        assert got["rec-old"]["context"] is None and got["rec-old"]["acceptance"] is None
        assert got["rec-old"]["context_v2_json"] == base["context_v2_json"]
        for rid in ("rec-new", "rec-open", "rec-crit"):
            assert (got[rid]["context"], got[rid]["acceptance"]) == ("ctx", "acc"), rid
        assert (got["rec-fo"]["context"], got["rec-fo"]["acceptance"], got["rec-fo"]["context_v2_json"]) == (None, None, None)

    def test_signal_rows_empty_followon_ids_matches_nothing(self):
        rows = [{"id": "rec-plain", "status": "open", "source": "agent", "priority": "low"}]
        assert self._verb(self._db(rows), "preflight_signal_rows", "") == []

    def test_preventive_regressions_parity(self):
        from scripts.ci_rca.back_validation import find_preventive_regressions

        ago = self._ago
        ctx = '{"preventive_action": "add a guard", "closure_waiver_category": "x"}'
        rows = [
            {
                "id": "rec-p",
                "status": "closed",
                "source": "ci_rca",
                "file": "a.py",
                "context_v2_json": ctx,
                "created_timestamp": ago(days=300),
                "last_updated_timestamp": ago(days=200),
                "context": "old",
                "acceptance": "old",
            },
            {
                "id": "rec-n",
                "status": "open",
                "source": "ci_rca",
                "file": "a.py",
                "created_timestamp": ago(days=2),
                "last_updated_timestamp": ago(days=2),
                "context": "new",
                "acceptance": "new",
            },
            {
                "id": "rec-x",
                "status": "open",
                "source": "agent",
                "file": "a.py",
                "created_timestamp": ago(days=2),
                "last_updated_timestamp": ago(days=2),
            },
        ]
        con = self._db(rows)
        full = self._verb(con, "rec_by_id", "x")  # sanity: the full-row verb is the SELECT * shape
        assert full == []
        all_rows = [
            dict(zip([c for c, _ in resolve_table_spec("ops_recommendations").ordered_columns], r))
            for r in con.execute("SELECT * FROM t").fetchall()
        ]
        bounded = self._verb(con, "preflight_signal_rows", "")
        assert find_preventive_regressions(bounded) == find_preventive_regressions(all_rows) != []
