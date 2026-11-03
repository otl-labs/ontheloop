-- Owner TEMPLATE for one DuckLake catalog schema (Decision 230 cl.2 and cl.4): {owner_role} and {schema}.
--
-- WHAT THIS IS: each catalog (ducklake_smoke, ducklake_telemetry) is owned, schema and every relation, by a NOLOGIN role so
-- that "which catalog can this credential change" is a Postgres fact. The shared owner login stays a member of each role
-- (INHERIT and SET), and a per-catalog DDL login takes the role as its session role.
--
-- HOW IT IS APPLIED: by the ducklake_maintenance verbs provision_catalog_owner and provision_telemetry_catalog, as the shared
-- owner login, with no query parameters (a percent sign in a format call stays literal). render_owner_sql selects ONE named
-- section by mode; no other mode exists. Every statement is a no-op on a converged catalog, so a re-run is also the
-- normalisation step after an extension migration created a relation under the owner login. SET LOCAL lock_timeout bounds the
-- wait on a relation an in-flight commit holds: the whole transaction then fails atomically (55P03) and is simply re-run.

-- section: adopt
SET LOCAL lock_timeout = '5s';
SET LOCAL createrole_self_grant = 'set, inherit';
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{owner_role}') THEN
        CREATE ROLE {owner_role} NOLOGIN;
    END IF;
END
$$;
GRANT {owner_role} TO CURRENT_USER WITH INHERIT TRUE, SET TRUE;
DO $$
DECLARE
    rel record;
    db text := current_database();
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = '{schema}') THEN
        EXECUTE format('CREATE SCHEMA %I AUTHORIZATION %I', '{schema}', '{owner_role}');
    ELSIF (SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname = '{schema}') <> '{owner_role}' THEN
        EXECUTE format('GRANT CREATE ON DATABASE %I TO %I', db, '{owner_role}');
        EXECUTE format('ALTER SCHEMA %I OWNER TO %I', '{schema}', '{owner_role}');
        EXECUTE format('REVOKE CREATE ON DATABASE %I FROM %I', db, '{owner_role}');
    END IF;
    FOR rel IN
        SELECT c.relname, c.relkind
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = '{schema}' AND c.relkind IN ('r', 'p', 'S', 'v', 'm')
          AND c.relowner <> (SELECT oid FROM pg_roles WHERE rolname = '{owner_role}')
          AND NOT (c.relkind = 'S' AND EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype IN ('a', 'i')))
    LOOP
        EXECUTE format(
            'ALTER %s %I.%I OWNER TO %I',
            CASE rel.relkind WHEN 'S' THEN 'SEQUENCE' WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW' ELSE 'TABLE' END,
            '{schema}', rel.relname, '{owner_role}'
        );
    END LOOP;
END
$$;

-- section: release
SET LOCAL lock_timeout = '5s';
DO $$
DECLARE
    rel record;
BEGIN
    IF (SELECT nspowner FROM pg_namespace WHERE nspname = '{schema}') <> (SELECT oid FROM pg_roles WHERE rolname = current_user) THEN
        EXECUTE format('ALTER SCHEMA %I OWNER TO CURRENT_USER', '{schema}');
    END IF;
    FOR rel IN
        SELECT c.relname, c.relkind
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = '{schema}' AND c.relkind IN ('r', 'p', 'S', 'v', 'm')
          AND c.relowner <> (SELECT oid FROM pg_roles WHERE rolname = current_user)
          AND NOT (c.relkind = 'S' AND EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype IN ('a', 'i')))
    LOOP
        EXECUTE format(
            'ALTER %s %I.%I OWNER TO CURRENT_USER',
            CASE rel.relkind WHEN 'S' THEN 'SEQUENCE' WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW' ELSE 'TABLE' END,
            '{schema}', rel.relname
        );
    END LOOP;
END
$$;
