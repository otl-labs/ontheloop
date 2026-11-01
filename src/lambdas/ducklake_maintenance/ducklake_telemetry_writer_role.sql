-- Writer TEMPLATE (Decision 230 cl.3, rec-4297): {login}, {schema}, {owner_role}, {connection_limit}.
--
-- WHAT THIS IS: the scoped catalog login of the telemetry writer on ONE catalog schema (ducklake_smoke for the smoke binding,
-- ducklake_telemetry for production). Every catalog-connecting Lambda used to share the ducklake_ops owner login; this role is
-- SQL-created (not a neon_superuser member), LOGIN only, with a connection limit, and holds DML on its own schema only.
--
-- MEASURED GRANTS: the privileges below are the (relation, privilege) pairs the writer verbs issue, collected with
-- log_statement = 'all' on a local Postgres 16 stage while running the verify-and-seed, register_tenant, register_project, an
-- append through the real append core (inline rows and a stored no-op re-send), an owner-role column add followed by another
-- append, and the probe reads. SELECT covers every catalog table (DuckLake reads the whole catalog at attach); writes are only
-- INSERT on the file, stats, snapshot and partition-value tables, plus UPDATE on the data-file and stats tables. There is no
-- UPDATE or DELETE on ducklake_table, ducklake_schema, ducklake_column or ducklake_snapshot, so a DuckLake DROP TABLE or an
-- ALTER fails at commit. The per-table physical tables DuckLake creates per table version (ducklake_inlined_data_*) have
-- version-dependent names that exist before this file runs, so they are granted by class below. Re-measure when a DuckLake
-- extension bump changes the statements a verb issues (Decision 230 reversal condition measured-grants-drift).
--
-- HOW IT IS APPLIED: by the ducklake_maintenance verb provision_telemetry_login, as the shared owner login, with no query
-- parameters (a percent sign in a comment stays literal). One transaction, so the revoke and the re-grant are atomic. It is
-- idempotent: a re-run is rotation and the RDS swap's re-apply. The role's password is not set here; the verb sets it from the
-- role's secret afterwards, so this file stays password-free. Schema migrations stay an owner-login step (Decision 213 cl.3).

SET LOCAL lock_timeout = '5s';
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{login}') THEN
        CREATE ROLE {login} NOLOGIN;
    END IF;
END
$$;

ALTER ROLE {login} LOGIN NOINHERIT NOCREATEDB NOCREATEROLE CONNECTION LIMIT {connection_limit};
ALTER ROLE {login} SET statement_timeout = '60s';

GRANT CONNECT ON DATABASE ducklake_ops TO {login};
GRANT USAGE ON SCHEMA {schema} TO {login};
REVOKE ALL ON ALL TABLES IN SCHEMA {schema} FROM {login};
REVOKE ALL ON ALL SEQUENCES IN SCHEMA {schema} FROM {login};
ALTER DEFAULT PRIVILEGES FOR ROLE ducklake_ops IN SCHEMA {schema} REVOKE ALL ON TABLES FROM {login};
ALTER DEFAULT PRIVILEGES FOR ROLE ducklake_ops IN SCHEMA {schema} REVOKE ALL ON SEQUENCES FROM {login};

GRANT SELECT ON {schema}.ducklake_column TO {login};
GRANT SELECT ON {schema}.ducklake_column_mapping TO {login};
GRANT SELECT ON {schema}.ducklake_column_tag TO {login};
GRANT SELECT, INSERT, UPDATE ON {schema}.ducklake_data_file TO {login};
GRANT SELECT ON {schema}.ducklake_delete_file TO {login};
GRANT SELECT, INSERT ON {schema}.ducklake_file_column_stats TO {login};
GRANT SELECT, INSERT ON {schema}.ducklake_file_partition_value TO {login};
GRANT SELECT ON {schema}.ducklake_file_variant_stats TO {login};
GRANT SELECT ON {schema}.ducklake_files_scheduled_for_deletion TO {login};
GRANT SELECT ON {schema}.ducklake_inlined_data_tables TO {login};
GRANT SELECT ON {schema}.ducklake_macro TO {login};
GRANT SELECT ON {schema}.ducklake_macro_impl TO {login};
GRANT SELECT ON {schema}.ducklake_macro_parameters TO {login};
GRANT SELECT ON {schema}.ducklake_metadata TO {login};
GRANT SELECT ON {schema}.ducklake_name_mapping TO {login};
GRANT SELECT ON {schema}.ducklake_partition_column TO {login};
GRANT SELECT ON {schema}.ducklake_partition_info TO {login};
GRANT SELECT ON {schema}.ducklake_schema TO {login};
GRANT SELECT ON {schema}.ducklake_schema_versions TO {login};
GRANT SELECT, INSERT ON {schema}.ducklake_snapshot TO {login};
GRANT SELECT, INSERT ON {schema}.ducklake_snapshot_changes TO {login};
GRANT SELECT ON {schema}.ducklake_sort_expression TO {login};
GRANT SELECT ON {schema}.ducklake_sort_info TO {login};
GRANT SELECT ON {schema}.ducklake_table TO {login};
GRANT SELECT, INSERT, UPDATE ON {schema}.ducklake_table_column_stats TO {login};
GRANT SELECT, INSERT, UPDATE ON {schema}.ducklake_table_stats TO {login};
GRANT SELECT ON {schema}.ducklake_tag TO {login};
GRANT SELECT ON {schema}.ducklake_view TO {login};

DO $$
DECLARE
    rel record;
BEGIN
    FOR rel IN
        SELECT c.relname
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = '{schema}' AND c.relkind = 'r' AND starts_with(c.relname, 'ducklake_inlined_data_')
    LOOP
        EXECUTE format('GRANT SELECT ON %I.%I TO %I', '{schema}', rel.relname, '{login}');
    END LOOP;
END
$$;

-- Relations created later (a new per-table physical table, a new catalog table after an extension bump) are readable and
-- nothing more, whichever role creates them; a write on one fails loud until the grants are re-measured.
ALTER DEFAULT PRIVILEGES FOR ROLE ducklake_ops IN SCHEMA {schema} GRANT SELECT ON TABLES TO {login};
ALTER DEFAULT PRIVILEGES FOR ROLE {owner_role} IN SCHEMA {schema} GRANT SELECT ON TABLES TO {login};
