-- Reader TEMPLATE (Decision 230 cl.3): {login}, {schema}, {owner_role}, {connection_limit}.
--
-- WHAT THIS IS: the SELECT-only login of the telemetry reader on one catalog schema. Read-only by default (the session default
-- and, at attach, DuckLake's READ_ONLY option), nothing on any other schema or on public, a connection limit sized to the
-- reader function's reserved concurrency.
--
-- HOW IT IS APPLIED: by provision_telemetry_login as the shared owner login, with no query parameters; idempotent. The password
-- is set by the verb afterwards, so this file stays password-free.

SET LOCAL lock_timeout = '5s';
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{login}') THEN
        CREATE ROLE {login} NOLOGIN;
    END IF;
END
$$;

ALTER ROLE {login} LOGIN NOINHERIT NOCREATEDB NOCREATEROLE CONNECTION LIMIT {connection_limit};
ALTER ROLE {login} SET statement_timeout = '100s';
ALTER ROLE {login} SET default_transaction_read_only = on;

GRANT CONNECT ON DATABASE ducklake_ops TO {login};
GRANT USAGE ON SCHEMA {schema} TO {login};
REVOKE ALL ON ALL TABLES IN SCHEMA {schema} FROM {login};
REVOKE ALL ON ALL SEQUENCES IN SCHEMA {schema} FROM {login};
GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {login};

ALTER DEFAULT PRIVILEGES FOR ROLE ducklake_ops IN SCHEMA {schema} GRANT SELECT ON TABLES TO {login};
ALTER DEFAULT PRIVILEGES FOR ROLE {owner_role} IN SCHEMA {schema} GRANT SELECT ON TABLES TO {login};
