-- Stats index for the DuckLake catalog (Neon Postgres), one per catalog schema.
--
-- WHAT THIS IS: DuckLake reads ducklake_file_column_stats on every query to prune data files. Upstream
-- (duckdb/ducklake issue 859, fixed by PR 1147) pushes that filter down to Postgres through postgres_query()
-- only when an index on (table_id, column_id) exists: the index must currently be manually created by the
-- user. Without it every query scans the whole table over the wire.
--
-- HOW IT IS APPLIED: by the ducklake_maintenance verb ensure_catalog_indexes (operator-invoked, Decision 81
-- cl.6) and by catalog_reinit on a schema it re-initialises. Both run this one statement as the ducklake_ops
-- owner login, under autocommit with a bounded statement_timeout, never inside a transaction (CONCURRENTLY
-- cannot run in one). The placeholder is replaced with a validated bare identifier by the verb.
--
-- NO IF NOT EXISTS: a failed or timed-out concurrent build leaves an INVALID index of this name, which
-- IF NOT EXISTS would silently accept. The verb inspects validity first and drops an invalid index before
-- running this statement.

CREATE INDEX CONCURRENTLY ducklake_file_column_stats_table_id_column_id_idx ON {meta_schema}.ducklake_file_column_stats (table_id, column_id);
