-- Maintainer TEMPLATE (Decision 230 cl.2): {login}, {owner_role}, {connection_limit}.
--
-- WHAT THIS IS: the smoke-only DDL login of the CI-reachable ducklake_maintenance_smoke function. It is a member of exactly one
-- owner role and takes it as its session role, so every connection it opens, DuckLake's included, runs DDL as the catalog's
-- owner role and that role's default privileges apply. It is a member of no other role and holds nothing on another catalog.
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

ALTER ROLE {login} LOGIN INHERIT NOCREATEDB NOCREATEROLE CONNECTION LIMIT {connection_limit};
ALTER ROLE {login} SET statement_timeout = '300s';

GRANT CONNECT ON DATABASE ducklake_ops TO {login};
GRANT {owner_role} TO {login} WITH INHERIT TRUE, SET TRUE;
ALTER ROLE {login} SET role = '{owner_role}';
