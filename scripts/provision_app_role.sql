-- Provision a restricted application DB role so Row-Level Security actually
-- enforces tenant isolation.
--
-- WHY: the app currently connects as `postgres`, which has BYPASSRLS — so every
-- RLS policy is silently skipped and tenant isolation rests entirely on the
-- WHERE clauses in core/db.py. RLS only takes effect for a NON-superuser role
-- WITHOUT BYPASSRLS. This script creates that role.
--
-- Run once as an admin (e.g. via the Supabase SQL editor or `psql` as postgres).
-- The RLS policies themselves are created/maintained by core.db._run_migrations.

-- 1. The restricted role (no superuser, no createdb/role, NO bypassrls).
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
        CREATE ROLE app_user LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
END $$;

-- 2. Set a strong password (choose your own; keep it only in DATABASE_URL).
ALTER ROLE app_user PASSWORD 'CHANGE_ME_to_a_strong_secret';

-- 3. Grant the DML it needs (it must NOT own the tables — postgres does — so that
--    FORCE RLS + NOBYPASSRLS subjects it to the policies).
GRANT USAGE ON SCHEMA public TO app_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user;

-- 4. Future tables/sequences created by migrations get the same grants.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO app_user;

-- 5. ACTIVATE: point the app at the restricted role. On Supabase's pooler the
--    username takes the form `app_user.<project-ref>`:
--
--      DATABASE_URL=postgresql://app_user.<project-ref>:<password>@<pooler-host>:5432/postgres
--
--    Then restart the API. Verify with:
--      SET app.current_user_id = '<some user id>';
--      SELECT count(*) FROM transactions;        -- should show only that user's rows
--      SELECT count(*) FROM transactions WHERE user_id = <other id>;  -- should be 0
--
-- NOTE: migrations (DDL, role-agnostic, run as postgres) and any genuinely
-- cross-user maintenance still use the postgres connection; the app's per-request
-- traffic should use app_user.
