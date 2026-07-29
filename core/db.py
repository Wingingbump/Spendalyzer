import os
import secrets
import psycopg2
import psycopg2.pool
import psycopg2.extras
from contextlib import contextmanager

# Heroku/Railway supply postgres:// — psycopg2 needs postgresql://
_raw_url = os.environ.get("DATABASE_URL", "")
if _raw_url.startswith("postgres://"):
    _raw_url = _raw_url.replace("postgres://", "postgresql://", 1)

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        if not _raw_url:
            raise RuntimeError(
                "DATABASE_URL environment variable is not set. "
                "Add it to your .env file: DATABASE_URL=postgresql://user:pass@host/dbname"
            )
        _pool = psycopg2.pool.ThreadedConnectionPool(1, 10, dsn=_raw_url)
    return _pool


class _Conn:
    """Thin wrapper giving psycopg2 connections a sqlite3-compatible execute() API."""

    def __init__(self, conn):
        self._conn = conn
        self._cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def execute(self, sql: str, params=None):
        self._cur.execute(sql, params)
        return self._cur

    def executemany(self, sql: str, seq):
        self._cur.executemany(sql, seq)
        return self._cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()


@contextmanager
def get_conn():
    pool = _get_pool()
    raw = pool.getconn()
    raw.autocommit = False
    conn = _Conn(raw)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(raw)


@contextmanager
def get_user_conn(user_id: int):
    """Open a connection with RLS scoped to a specific user.

    With FORCE ROW LEVEL SECURITY enabled on tenant tables, every query in
    the transaction will be filtered to rows where user_id matches.
    """
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(int(user_id)),))
        yield conn


# ── Schema bootstrap ─────────────────────────────────────────────────────────────

def _run_migrations():
    with get_conn() as conn:

        # Users
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id         SERIAL PRIMARY KEY,
                username   TEXT NOT NULL,
                password   TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS users_username_lower_idx
            ON users (LOWER(username))
        """)

        # Credentials (Plaid API keys — global/shared)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS credentials (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Connected accounts
        conn.execute("""
            CREATE TABLE IF NOT EXISTS connected_accounts (
                id           SERIAL PRIMARY KEY,
                name         TEXT NOT NULL,
                account_type TEXT NOT NULL DEFAULT 'bank',
                access_token TEXT NOT NULL,
                user_id      INTEGER REFERENCES users(id),
                created_at   TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Plaid accounts
        conn.execute("""
            CREATE TABLE IF NOT EXISTS plaid_accounts (
                id                   SERIAL PRIMARY KEY,
                connected_account_id INTEGER NOT NULL
                                     REFERENCES connected_accounts(id) ON DELETE CASCADE,
                plaid_account_id     TEXT NOT NULL UNIQUE,
                name                 TEXT NOT NULL,
                official_name        TEXT,
                mask                 TEXT,
                type                 TEXT,
                subtype              TEXT,
                created_at           TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Transactions  — NUMERIC(12,2) instead of REAL to avoid floating-point errors
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id               TEXT PRIMARY KEY,
                date             DATE NOT NULL,
                name             TEXT,
                amount           NUMERIC(12,2),
                category         TEXT,
                pending          BOOLEAN DEFAULT FALSE,
                institution      TEXT,
                plaid_account_id TEXT,
                user_id          INTEGER REFERENCES users(id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS transactions_user_date_idx "
            "ON transactions (user_id, date DESC)"
        )
        # Add is_manual column if not present (migration for existing DBs)
        conn.execute("""
            ALTER TABLE transactions ADD COLUMN IF NOT EXISTS is_manual BOOLEAN DEFAULT FALSE
        """)
        # Plaid enrichment fields (Personal Finance Category + counterparty data).
        # These are the primary signals for transfer/income/spend classification —
        # far more reliable than descriptor keyword matching. Nullable so manual and
        # pre-enrichment rows are unaffected. counterparties is JSON text (a small
        # list of {name, type}) parsed in Python, not queried in SQL.
        for _col, _type in (
            ("pfc_primary",         "TEXT"),
            ("pfc_detailed",        "TEXT"),
            ("pfc_confidence",      "TEXT"),
            ("payment_channel",     "TEXT"),
            ("plaid_merchant_name", "TEXT"),
            ("counterparties",      "TEXT"),
        ):
            conn.execute(
                f"ALTER TABLE transactions ADD COLUMN IF NOT EXISTS {_col} {_type}"
            )

        # Overrides
        conn.execute("""
            CREATE TABLE IF NOT EXISTS overrides (
                transaction_id TEXT PRIMARY KEY,
                user_id        INTEGER REFERENCES users(id) ON DELETE CASCADE,
                category       TEXT,
                amount         NUMERIC(12,2),
                notes          TEXT,
                updated_at     TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        # Backfill user_id on pre-existing rows and tenant-scope the table.
        conn.execute("ALTER TABLE overrides ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")
        # Per-transaction "exclude from reports" flag — keeps the row in the ledger
        # but drops it from all spending/income analytics (the monthly Overview).
        conn.execute("ALTER TABLE overrides ADD COLUMN IF NOT EXISTS excluded BOOLEAN NOT NULL DEFAULT FALSE")
        conn.execute("""
            UPDATE overrides o SET user_id = t.user_id
            FROM transactions t
            WHERE t.id = o.transaction_id AND o.user_id IS NULL
        """)

        # Dedup cache
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dedup_cache (
                fingerprint  TEXT PRIMARY KEY,
                is_duplicate BOOLEAN DEFAULT FALSE,
                is_transfer  BOOLEAN DEFAULT FALSE,
                source       TEXT,
                reason       TEXT
            )
        """)

        # Category map — per-user
        conn.execute("""
            CREATE TABLE IF NOT EXISTS category_map (
                user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                external_category TEXT NOT NULL,
                internal_category TEXT NOT NULL,
                PRIMARY KEY (user_id, external_category)
            )
        """)
        # Migrate from old global schema (no user_id column) to per-user
        has_user_col = conn.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'category_map' AND column_name = 'user_id'
        """).fetchone()
        if not has_user_col:
            conn.execute("ALTER TABLE category_map RENAME TO category_map_global")
            conn.execute("""
                CREATE TABLE category_map (
                    user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    external_category TEXT NOT NULL,
                    internal_category TEXT NOT NULL,
                    PRIMARY KEY (user_id, external_category)
                )
            """)
            # Copy global rows to every existing user
            conn.execute("SET LOCAL app.current_user_id = 'bypass'")
            conn.execute("""
                INSERT INTO category_map (user_id, external_category, internal_category)
                SELECT u.id, g.external_category, g.internal_category
                FROM category_map_global g
                CROSS JOIN users u
                ON CONFLICT DO NOTHING
            """)
            conn.execute("DROP TABLE category_map_global")

        # Normalization cache
        conn.execute("""
            CREATE TABLE IF NOT EXISTS normalization_cache (
                raw_name   TEXT PRIMARY KEY,
                clean_name TEXT NOT NULL
            )
        """)

        # Shared merchant dictionary — learned brand-per-bucket, global across
        # users. Populated by clustering + AI; lets a brand resolved once snap a
        # new user's lone variant into line. Holds no user-specific data.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS merchant_dictionary (
                bucket     TEXT PRIMARY KEY,
                canonical  TEXT NOT NULL,
                members    INTEGER DEFAULT 1,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Memo -> category cache for P2P reimbursement notes (e.g. "Freddys burger"
        # -> "Food & Drink"). Global; only stores the resolved category label.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memo_category_cache (
                memo     TEXT PRIMARY KEY,
                category TEXT NOT NULL
            )
        """)

        # Merchant overrides — per-user display name corrections
        conn.execute("""
            CREATE TABLE IF NOT EXISTS merchant_overrides (
                user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                raw_name     TEXT    NOT NULL,
                display_name TEXT    NOT NULL,
                updated_at   TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (user_id, raw_name)
            )
        """)

        # User-defined categories — the list that appears in all dropdowns
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_categories (
                id         SERIAL PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name       TEXT NOT NULL,
                position   INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (user_id, name)
            )
        """)

        # Merchant category overrides — per-user merchant→category rules
        conn.execute("""
            CREATE TABLE IF NOT EXISTS merchant_category_overrides (
                user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                merchant_normalized TEXT    NOT NULL,
                category            TEXT    NOT NULL,
                updated_at          TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (user_id, merchant_normalized)
            )
        """)

        # Transfer overrides — per-user, per-counterparty "treat as transfer" memory.
        # A user correction ("this Zelle to a person is NOT a transfer", or "this IS
        # an internal move the auto-detection missed") is remembered by merchant key
        # and re-applied to every past and future transaction from that counterparty,
        # so the same mistake is never surfaced twice.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transfer_overrides (
                user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                merchant_key TEXT    NOT NULL,
                is_transfer  BOOLEAN NOT NULL,
                updated_at   TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (user_id, merchant_key)
            )
        """)

        # User-marked recurring rules — bypass auto-detection thresholds
        # amount_tolerance_abs and amount_tolerance_pct are OR'd: a transaction
        # matches if |amount - amount_center| <= max(abs, center * pct/100)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_recurring_rules (
                id                     SERIAL PRIMARY KEY,
                user_id                INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                merchant_key           TEXT    NOT NULL,
                amount_center          NUMERIC(12,2) NOT NULL,
                amount_tolerance_abs   NUMERIC(12,2) NOT NULL DEFAULT 2.00,
                amount_tolerance_pct   NUMERIC(5,2)  NOT NULL DEFAULT 15.00,
                label                  TEXT,
                frequency_hint         TEXT,
                created_at             TIMESTAMPTZ DEFAULT NOW(),
                updated_at             TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (user_id, merchant_key)
            )
        """)
        # A suppressed row means "the user said this merchant is NOT recurring" —
        # auto-detection skips the key so a heuristic match can't keep re-tagging it.
        conn.execute(
            "ALTER TABLE user_recurring_rules "
            "ADD COLUMN IF NOT EXISTS is_suppressed BOOLEAN NOT NULL DEFAULT FALSE"
        )

        # Plaid item health — cached result of /item/get per connected account
        conn.execute("""
            CREATE TABLE IF NOT EXISTS plaid_item_health (
                connected_account_id INTEGER PRIMARY KEY
                                     REFERENCES connected_accounts(id) ON DELETE CASCADE,
                error_code           TEXT,
                error_message        TEXT,
                consent_expiration_time TIMESTAMPTZ,
                last_successful_update  TIMESTAMPTZ,
                last_failed_update      TIMESTAMPTZ,
                checked_at           TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Dismissed duplicate pairs — user-confirmed "these are not duplicates"
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dismissed_duplicates (
                id               SERIAL PRIMARY KEY,
                user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                transaction_id_a TEXT    NOT NULL,
                transaction_id_b TEXT    NOT NULL,
                created_at       TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(user_id, transaction_id_a, transaction_id_b)
            )
        """)

        # Budgets
        conn.execute("""
            CREATE TABLE IF NOT EXISTS budgets (
                id         SERIAL PRIMARY KEY,
                user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,
                category   TEXT NOT NULL,
                amount     NUMERIC(12,2) NOT NULL,
                period     TEXT NOT NULL DEFAULT 'monthly',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(user_id, category)
            )
        """)

        # Custom groups
        conn.execute("""
            CREATE TABLE IF NOT EXISTS custom_groups (
                id         SERIAL PRIMARY KEY,
                user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,
                name       TEXT NOT NULL,
                color      TEXT NOT NULL DEFAULT '#c8ff00',
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Group → transaction membership (manual tagging)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS group_transactions (
                group_id       INTEGER NOT NULL REFERENCES custom_groups(id) ON DELETE CASCADE,
                transaction_id TEXT    NOT NULL,
                PRIMARY KEY (group_id, transaction_id)
            )
        """)

        # Canvas — migrate from old single-canvas schema (user_id PK) to multi-canvas
        conn.execute("""
            CREATE TABLE IF NOT EXISTS canvases (
                id         SERIAL PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name       TEXT NOT NULL DEFAULT 'My Canvas',
                layout     JSONB NOT NULL DEFAULT '[]',
                widgets    JSONB NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        # If old schema exists (user_id was PK, no id column), migrate it
        old_schema = conn.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'canvases' AND column_name = 'id'
        """).fetchone()
        if not old_schema:
            conn.execute("ALTER TABLE canvases RENAME TO canvases_old")
            conn.execute("""
                CREATE TABLE canvases (
                    id         SERIAL PRIMARY KEY,
                    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    name       TEXT NOT NULL DEFAULT 'My Canvas',
                    layout     JSONB NOT NULL DEFAULT '[]',
                    widgets    JSONB NOT NULL DEFAULT '{}',
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            conn.execute("""
                INSERT INTO canvases (user_id, name, layout, widgets)
                SELECT user_id, 'My Canvas', layout, widgets FROM canvases_old
            """)
            conn.execute("DROP TABLE canvases_old")

        # Add goal column to custom_groups if not present
        conn.execute("""
            ALTER TABLE custom_groups ADD COLUMN IF NOT EXISTS goal NUMERIC(12,2)
        """)

        # Scheduled account deletion
        conn.execute("""
            ALTER TABLE users ADD COLUMN IF NOT EXISTS deletion_scheduled_at TIMESTAMPTZ DEFAULT NULL
        """)

        # Fix missing ON DELETE CASCADE on early tables
        for tbl, fkey in [
            ("connected_accounts", "connected_accounts_user_id_fkey"),
            ("transactions",       "transactions_user_id_fkey"),
        ]:
            conn.execute(f"""
                DO $$ BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.table_constraints
                        WHERE constraint_name = '{fkey}'
                    ) THEN
                        ALTER TABLE {tbl} DROP CONSTRAINT {fkey};
                        ALTER TABLE {tbl} ADD CONSTRAINT {fkey}
                            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
                    END IF;
                END $$
            """)

        # User profile fields
        conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name TEXT")
        conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_name TEXT")
        conn.execute("ALTER TABLE users DROP COLUMN IF EXISTS name")
        conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT")
        conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone TEXT")
        conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url TEXT")
        conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT FALSE")
        conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_idx ON users (LOWER(email)) WHERE email IS NOT NULL"
        )

        # Email verification tokens
        conn.execute("""
            CREATE TABLE IF NOT EXISTS email_verification_tokens (
                token      TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Password reset tokens (email-based, replaces static secret)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                token      TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TIMESTAMPTZ NOT NULL,
                used       BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Refresh token store (for rotation — each token is single-use)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS refresh_tokens (
                jti        TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS refresh_tokens_user_idx ON refresh_tokens (user_id)"
        )

        # ── Memory Layer ─────────────────────────────────────────────────────────
        # Enable pgvector (ships with Supabase — no-op if already installed)
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")

        # User goals — structured financial goals
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_goals (
                id             SERIAL PRIMARY KEY,
                user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title          TEXT NOT NULL,
                type           TEXT NOT NULL DEFAULT 'other',
                target_amount  NUMERIC(12,2),
                current_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
                deadline       DATE,
                priority       SMALLINT NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),
                status         TEXT NOT NULL DEFAULT 'active',
                notes          TEXT,
                created_at     TIMESTAMPTZ DEFAULT NOW(),
                updated_at     TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Financial events — significant moments worth remembering
        conn.execute("""
            CREATE TABLE IF NOT EXISTS financial_events (
                id          SERIAL PRIMARY KEY,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                event_type  TEXT NOT NULL,
                title       TEXT NOT NULL,
                amount      NUMERIC(12,2),
                event_date  DATE NOT NULL DEFAULT CURRENT_DATE,
                description TEXT,
                created_at  TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # User behavioral/preference profile — one row per user, upserted
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_profile (
                user_id             INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                life_stage          TEXT,
                risk_tolerance      TEXT,
                income_estimate     NUMERIC(12,2),
                savings_rate_pct    NUMERIC(5,2),
                communication_style TEXT,
                spending_triggers   JSONB NOT NULL DEFAULT '[]',
                preferences         JSONB NOT NULL DEFAULT '{}',
                updated_at          TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Financial snapshots — periodic time-series state derived from Plaid
        conn.execute("""
            CREATE TABLE IF NOT EXISTS financial_snapshots (
                id               SERIAL PRIMARY KEY,
                user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                snapshot_date    DATE NOT NULL DEFAULT CURRENT_DATE,
                income_estimate  NUMERIC(12,2),
                total_expenses   NUMERIC(12,2),
                savings_rate_pct NUMERIC(5,2),
                total_debt       NUMERIC(12,2),
                total_assets     NUMERIC(12,2),
                net_worth        NUMERIC(12,2),
                created_at       TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (user_id, snapshot_date)
            )
        """)

        # Conversation memory — semantic memory with pgvector embeddings.
        # This is the beating heart of personalization — searched before every LLM call.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation_memory (
                id               SERIAL PRIMARY KEY,
                user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                content          TEXT NOT NULL,
                memory_type      TEXT NOT NULL DEFAULT 'context',
                importance       SMALLINT NOT NULL DEFAULT 3 CHECK (importance BETWEEN 1 AND 5),
                source           TEXT NOT NULL DEFAULT 'conversation',
                embedding        VECTOR(512),
                created_at       TIMESTAMPTZ DEFAULT NOW(),
                last_accessed_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS conversation_memory_user_idx ON conversation_memory (user_id)"
        )
        # HNSW index for fast approximate nearest-neighbor search
        conn.execute("""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_indexes WHERE indexname = 'conversation_memory_embedding_idx'
                ) THEN
                    CREATE INDEX conversation_memory_embedding_idx
                    ON conversation_memory USING hnsw (embedding vector_cosine_ops);
                END IF;
            END $$
        """)

        # Advice history — every AI response given, with user reaction and outcome
        conn.execute("""
            CREATE TABLE IF NOT EXISTS advice_history (
                id               SERIAL PRIMARY KEY,
                user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                prompt_summary   TEXT,
                response_text    TEXT NOT NULL,
                category         TEXT,
                compliance_flags JSONB NOT NULL DEFAULT '[]',
                user_reaction    TEXT NOT NULL DEFAULT 'unknown',
                outcome_notes    TEXT,
                created_at       TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Proactive nudges — generated by the analysis pipeline after each sync
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nudges (
                id           SERIAL PRIMARY KEY,
                user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                type         TEXT NOT NULL,
                severity     TEXT NOT NULL DEFAULT 'info',
                title        TEXT NOT NULL,
                body         TEXT NOT NULL,
                data         JSONB NOT NULL DEFAULT '{}',
                created_at   TIMESTAMPTZ DEFAULT NOW(),
                read_at      TIMESTAMPTZ,
                dismissed_at TIMESTAMPTZ
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS nudges_user_idx ON nudges (user_id, created_at DESC)"
        )

        # ── Row-Level Security ───────────────────────────────────────────────────
        # RLS isolates tenants at the database layer so a forgotten WHERE clause
        # can't leak across users. IMPORTANT: this only takes effect when the app
        # connects as a NON-superuser role WITHOUT BYPASSRLS (see
        # scripts/provision_app_role.sql). The default 'postgres' superuser
        # bypasses all of this — these policies are then inert but harmless.
        #
        # Policies are written with CASE (not OR) so the 'bypass' sentinel never
        # gets cast to integer. Each per-user write/read path that runs under
        # `SET app.current_user_id = 'bypass'` is permitted; user-scoped contexts
        # are filtered to their own rows.
        _CUR = "current_setting('app.current_user_id', TRUE)"
        _UID = f"NULLIF({_CUR}, '')::integer"

        def _policy(table: str, expr: str):
            conn.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            conn.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            conn.execute(f"DROP POLICY IF EXISTS user_isolation ON {table}")
            conn.execute(f"CREATE POLICY user_isolation ON {table} USING ({expr}) WITH CHECK ({expr})")

        # Tables with a direct user_id column.
        for table in ("transactions", "connected_accounts", "budgets", "canvases",
                      "custom_groups", "category_map", "user_goals", "financial_events",
                      "user_profile", "financial_snapshots", "conversation_memory",
                      "advice_history", "nudges", "merchant_overrides",
                      "merchant_category_overrides", "transfer_overrides",
                      "user_categories",
                      "user_recurring_rules", "dismissed_duplicates", "overrides",
                      "refresh_tokens", "email_verification_tokens", "password_reset_tokens"):
            _policy(table, f"CASE WHEN {_CUR} = 'bypass' THEN TRUE ELSE user_id = {_UID} END")

        # Identity table — a user may see only their own row (keyed on id).
        _policy("users", f"CASE WHEN {_CUR} = 'bypass' THEN TRUE ELSE id = {_UID} END")

        # Child tables (no user_id) — isolate via the owning parent's user_id.
        for table, parent, fk in (("plaid_accounts", "connected_accounts", "connected_account_id"),
                                  ("plaid_item_health", "connected_accounts", "connected_account_id"),
                                  ("group_transactions", "custom_groups", "group_id")):
            _policy(table, f"CASE WHEN {_CUR} = 'bypass' THEN TRUE ELSE EXISTS "
                           f"(SELECT 1 FROM {parent} p WHERE p.id = {table}.{fk} AND p.user_id = {_UID}) END")

        # Global shared tables hold no per-user data, so there's nothing to scope
        # by user_id. But they're still exposed over Supabase's PostgREST API, where
        # the `anon`/`authenticated` roles hold full DML grants — leaving RLS off
        # means anyone with the anon key can read/write them (credentials is a secret
        # store). So: enable RLS, revoke the PostgREST roles' grants to close the REST
        # hole, and add a permissive policy so the direct-Postgres app role keeps full
        # access. The policy is scoped to `app_user` (not PUBLIC) on purpose: the owner
        # (postgres) and service_role bypass RLS already, app_user is the only role that
        # needs it, and a PUBLIC `USING (true)` write policy trips the 0024 linter. When
        # app_user doesn't exist (non-Supabase / dev as the owner role) RLS is bypassed
        # anyway, so fall back to a PUBLIC policy to keep any non-owner role working.
        for table in ("credentials", "dedup_cache", "normalization_cache",
                      "merchant_dictionary", "memo_category_cache"):
            conn.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            conn.execute(f"DROP POLICY IF EXISTS shared_app ON {table}")
            # The PostgREST/app_user roles only exist on Supabase; guard other envs.
            conn.execute(
                "DO $do$ BEGIN "
                "IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN "
                f"  CREATE POLICY shared_app ON {table} TO app_user USING (TRUE) WITH CHECK (TRUE); "
                "ELSE "
                f"  CREATE POLICY shared_app ON {table} USING (TRUE) WITH CHECK (TRUE); "
                "END IF; "
                "IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN "
                f"  REVOKE ALL ON {table} FROM anon, authenticated; "
                "END IF; END $do$"
            )

        # ── Legacy migration: promote sqlite admin user if present ───────────────
        n = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        if n == 0:
            pw_row = conn.execute(
                "SELECT value FROM credentials WHERE key = 'app_password'"
            ).fetchone()
            if pw_row:
                from core.crypto import decrypt
                try:
                    pw_hash = decrypt(pw_row["value"])
                except Exception:
                    pw_hash = pw_row["value"]
                # Bypass RLS for this initial seeding
                conn.execute("SET LOCAL app.current_user_id = 'bypass'")
                admin_id = conn.execute(
                    "INSERT INTO users (username, password) VALUES ('admin', %s) RETURNING id",
                    (pw_hash,)
                ).fetchone()["id"]
                conn.execute(
                    "UPDATE connected_accounts SET user_id = %s WHERE user_id IS NULL",
                    (admin_id,)
                )
                conn.execute(
                    "UPDATE transactions SET user_id = %s WHERE user_id IS NULL",
                    (admin_id,)
                )
                conn.execute("DELETE FROM credentials WHERE key = 'app_password'")


_run_migrations()


# ── Users ────────────────────────────────────────────────────────────────────────

def get_user_by_username(username: str) -> dict | None:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        row = conn.execute(
            "SELECT id, username, password, email, first_name, last_name, email_verified, is_active FROM users WHERE LOWER(username) = LOWER(%s)",
            (username,)
        ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        row = conn.execute(
            "SELECT id, username, first_name, last_name, email FROM users WHERE id = %s", (user_id,)
        ).fetchone()
    return dict(row) if row else None


def update_user_password(user_id: int, password_hash: str):
    """Update a user's password. Caller must pass an already-hashed password."""
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        conn.execute(
            "UPDATE users SET password = %s WHERE id = %s",
            (password_hash, user_id)
        )


def create_user(username: str, password_hash: str, first_name: str = "", last_name: str = "", email: str = "", phone: str = "") -> int:
    """Insert a new unverified user. Caller must pass an already-hashed password."""
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        row = conn.execute(
            """INSERT INTO users (username, password, first_name, last_name, email, phone, email_verified, is_active)
               VALUES (%s, %s, %s, %s, %s, %s, FALSE, FALSE) RETURNING id""",
            (username.strip(), password_hash, first_name.strip(), last_name.strip(), email.strip().lower(), phone.strip())
        ).fetchone()
    return row["id"]


def get_user_by_email(email: str) -> dict | None:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        row = conn.execute(
            "SELECT id, username, password, email, first_name, last_name, email_verified, is_active FROM users WHERE LOWER(email) = LOWER(%s)",
            (email,)
        ).fetchone()
    return dict(row) if row else None


def update_user_profile(user_id: int, first_name: str, last_name: str, phone: str):
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        conn.execute(
            "UPDATE users SET first_name = %s, last_name = %s, phone = %s WHERE id = %s",
            (first_name, last_name, phone, user_id)
        )


def update_user_avatar(user_id: int, avatar_url: str):
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        conn.execute(
            "UPDATE users SET avatar_url = %s WHERE id = %s",
            (avatar_url, user_id)
        )


def get_user_profile(user_id: int) -> dict | None:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        row = conn.execute(
            "SELECT id, username, first_name, last_name, email, phone, avatar_url, email_verified, created_at FROM users WHERE id = %s",
            (user_id,)
        ).fetchone()
    return dict(row) if row else None


# ── Email verification tokens ─────────────────────────────────────────────────

def create_email_verification_token(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        conn.execute("DELETE FROM email_verification_tokens WHERE user_id = %s", (user_id,))
        conn.execute(
            "INSERT INTO email_verification_tokens (token, user_id, expires_at) VALUES (%s, %s, NOW() + INTERVAL '24 hours')",
            (token, user_id)
        )
    return token


def consume_email_verification_token(token: str) -> int | None:
    """Validate and consume token. Returns user_id or None."""
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        row = conn.execute(
            "DELETE FROM email_verification_tokens WHERE token = %s AND expires_at > NOW() RETURNING user_id",
            (token,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE users SET email_verified = TRUE, is_active = TRUE WHERE id = %s",
                (row["user_id"],)
            )
    return row["user_id"] if row else None


# ── Password reset tokens ─────────────────────────────────────────────────────

def create_password_reset_token(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        conn.execute("DELETE FROM password_reset_tokens WHERE user_id = %s", (user_id,))
        conn.execute(
            "INSERT INTO password_reset_tokens (token, user_id, expires_at) VALUES (%s, %s, NOW() + INTERVAL '1 hour')",
            (token, user_id)
        )
    return token


def consume_password_reset_token(token: str) -> int | None:
    """Validate and consume token. Returns user_id or None."""
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        row = conn.execute(
            """DELETE FROM password_reset_tokens
               WHERE token = %s AND expires_at > NOW() AND used = FALSE
               RETURNING user_id""",
            (token,)
        ).fetchone()
    return row["user_id"] if row else None


def list_users() -> list[dict]:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        rows = conn.execute(
            "SELECT id, username, created_at FROM users ORDER BY created_at"
        ).fetchall()
    return [dict(r) for r in rows]


# ── Refresh tokens (rotation) ─────────────────────────────────────────────────────

def store_refresh_token(jti: str, user_id: int, expires_at):
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        conn.execute(
            "INSERT INTO refresh_tokens (jti, user_id, expires_at) VALUES (%s, %s, %s)",
            (jti, user_id, expires_at)
        )


def consume_refresh_token(jti: str) -> dict | None:
    """Validate the JTI exists and has not expired, then delete it (single-use)."""
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        row = conn.execute(
            "DELETE FROM refresh_tokens WHERE jti = %s AND expires_at > NOW() RETURNING user_id",
            (jti,)
        ).fetchone()
    return dict(row) if row else None


def revoke_user_refresh_tokens(user_id: int):
    """Invalidate all refresh tokens for a user (e.g. on logout or password change)."""
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        conn.execute("DELETE FROM refresh_tokens WHERE user_id = %s", (user_id,))


def cleanup_expired_refresh_tokens():
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        conn.execute("DELETE FROM refresh_tokens WHERE expires_at <= NOW()")


# ── Transactions ─────────────────────────────────────────────────────────────────

def get_latest_transaction_date(user_id: int) -> str | None:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        row = conn.execute(
            "SELECT MAX(date) AS latest FROM transactions WHERE user_id = %s",
            (user_id,)
        ).fetchone()
    return str(row["latest"]) if row and row["latest"] else None


def fetch_transactions(user_id: int) -> list[dict]:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        rows = conn.execute("""
            SELECT
                t.id,
                t.date,
                t.name,
                COALESCE(o.amount,    t.amount)   AS amount,
                COALESCE(o.category,  t.category) AS category,
                t.pending,
                t.institution,
                t.plaid_account_id,
                pa.name                            AS account_name,
                pa.mask                            AS account_mask,
                pa.subtype                         AS account_subtype,
                COALESCE(o.notes, '')              AS notes,
                (o.transaction_id IS NOT NULL
                 AND o.category IS NOT NULL)       AS has_user_override,
                COALESCE(o.excluded, FALSE)        AS is_excluded,
                COALESCE(t.is_manual, FALSE)       AS is_manual,
                t.pfc_primary,
                t.pfc_detailed,
                t.pfc_confidence,
                t.payment_channel,
                t.plaid_merchant_name,
                t.counterparties
            FROM transactions t
            LEFT JOIN overrides o       ON t.id = o.transaction_id
            LEFT JOIN plaid_accounts pa ON t.plaid_account_id = pa.plaid_account_id
            WHERE t.user_id = %s
            ORDER BY t.date DESC
        """, (user_id,)).fetchall()
    return [dict(r) for r in rows]


def get_pending_transactions_in_window(user_id: int, plaid_account_ids: list[str],
                                        start_date: str, end_date: str) -> list[dict]:
    """Return all pending transactions for the given accounts within the date window."""
    if not plaid_account_ids:
        return []
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        placeholders = ",".join(["%s"] * len(plaid_account_ids))
        rows = conn.execute(f"""
            SELECT id, date, name, amount, plaid_account_id
            FROM transactions
            WHERE user_id = %s
              AND pending = TRUE
              AND plaid_account_id IN ({placeholders})
              AND date BETWEEN %s AND %s
              AND is_manual IS NOT TRUE
        """, (user_id, *plaid_account_ids, start_date, end_date)).fetchall()
    return [dict(r) for r in rows]


def delete_transactions_by_ids(transaction_ids: list[str]) -> int:
    """Hard-delete transactions by ID. Used to remove stale pending entries."""
    if not transaction_ids:
        return 0
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        placeholders = ",".join(["%s"] * len(transaction_ids))
        # Delete overrides first (FK), then the transactions
        conn.execute(f"DELETE FROM overrides WHERE transaction_id IN ({placeholders})",
                     transaction_ids)
        result = conn.execute(
            f"DELETE FROM transactions WHERE id IN ({placeholders})",
            transaction_ids
        )
    return result.rowcount


_ENRICHMENT_COLS = (
    "pfc_primary", "pfc_detailed", "pfc_confidence",
    "payment_channel", "plaid_merchant_name", "counterparties",
)


def upsert_transactions(transactions: list[dict]):
    # Ensure every enrichment column is present on each record so the named
    # placeholders resolve even for callers that don't supply them.
    records = [{**{c: None for c in _ENRICHMENT_COLS}, **t} for t in transactions]
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        conn.executemany("""
            INSERT INTO transactions
                (id, date, name, amount, category, pending, institution,
                 plaid_account_id, user_id,
                 pfc_primary, pfc_detailed, pfc_confidence,
                 payment_channel, plaid_merchant_name, counterparties)
            VALUES
                (%(id)s, %(date)s, %(name)s, %(amount)s, %(category)s, %(pending)s,
                 %(institution)s, %(plaid_account_id)s, %(user_id)s,
                 %(pfc_primary)s, %(pfc_detailed)s, %(pfc_confidence)s,
                 %(payment_channel)s, %(plaid_merchant_name)s, %(counterparties)s)
            ON CONFLICT (id) DO UPDATE SET
                pending     = EXCLUDED.pending,
                amount      = EXCLUDED.amount,
                name        = EXCLUDED.name,
                date        = EXCLUDED.date,
                -- Refresh enrichment when Plaid provides it, but never overwrite a
                -- previously-stored value with NULL (a later thin sync shouldn't wipe it).
                pfc_primary         = COALESCE(EXCLUDED.pfc_primary,         transactions.pfc_primary),
                pfc_detailed        = COALESCE(EXCLUDED.pfc_detailed,        transactions.pfc_detailed),
                pfc_confidence      = COALESCE(EXCLUDED.pfc_confidence,      transactions.pfc_confidence),
                payment_channel     = COALESCE(EXCLUDED.payment_channel,     transactions.payment_channel),
                plaid_merchant_name = COALESCE(EXCLUDED.plaid_merchant_name, transactions.plaid_merchant_name),
                counterparties      = COALESCE(EXCLUDED.counterparties,      transactions.counterparties)
        """, records)



# ── Overrides ────────────────────────────────────────────────────────────────────

def save_override(user_id: int, transaction_id: str, category: str = None,
                  amount: float = None, notes: str = None, excluded: bool = None):
    # Runs under the user's RLS context so the override is bound to them.
    # `excluded` is COALESCEd like the rest: pass None to leave it unchanged,
    # True/False to toggle the "exclude from reports" flag.
    with get_user_conn(user_id) as conn:
        conn.execute("""
            INSERT INTO overrides (transaction_id, user_id, category, amount, notes, excluded, updated_at)
            VALUES (%s, %s, %s, %s, %s, COALESCE(%s, FALSE), NOW())
            ON CONFLICT (transaction_id) DO UPDATE SET
                category   = COALESCE(EXCLUDED.category,  overrides.category),
                amount     = COALESCE(EXCLUDED.amount,    overrides.amount),
                notes      = COALESCE(EXCLUDED.notes,     overrides.notes),
                excluded   = COALESCE(%s,                 overrides.excluded),
                updated_at = NOW()
        """, (transaction_id, user_id, category, amount, notes, excluded, excluded))


def insert_manual_transaction(user_id: int, name: str, date: str, amount: float,
                               category: str = None, notes: str = None) -> str:
    import uuid
    tx_id = f"manual_{uuid.uuid4().hex}"
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        conn.execute("""
            INSERT INTO transactions
                (id, date, name, amount, category, pending, institution, user_id, is_manual)
            VALUES (%s, %s, %s, %s, %s, FALSE, 'Manual', %s, TRUE)
        """, (tx_id, date, name, amount, category, user_id))
        if notes:
            conn.execute("""
                INSERT INTO overrides (transaction_id, notes, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (transaction_id) DO UPDATE SET notes = EXCLUDED.notes, updated_at = NOW()
            """, (tx_id, notes))
    return tx_id


def transaction_belongs_to_user(transaction_id: str, user_id: int) -> bool:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        row = conn.execute(
            "SELECT 1 FROM transactions WHERE id = %s AND user_id = %s",
            (transaction_id, user_id)
        ).fetchone()
    return row is not None


def delete_transaction(transaction_id: str, user_id: int) -> bool:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        conn.execute("DELETE FROM overrides WHERE transaction_id = %s", (transaction_id,))
        result = conn.execute(
            "DELETE FROM transactions WHERE id = %s AND user_id = %s",
            (transaction_id, user_id)
        )
    return result.rowcount > 0


# ── Plaid accounts ────────────────────────────────────────────────────────────────

def upsert_plaid_accounts(connected_account_id: int, accounts: list):
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        conn.executemany("""
            INSERT INTO plaid_accounts
                (connected_account_id, plaid_account_id, name, official_name,
                 mask, type, subtype)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (plaid_account_id) DO UPDATE SET
                name          = EXCLUDED.name,
                official_name = EXCLUDED.official_name,
                mask          = EXCLUDED.mask,
                type          = EXCLUDED.type,
                subtype       = EXCLUDED.subtype
        """, [
            (connected_account_id,
             a["account_id"], a.get("name", ""), a.get("official_name"),
             a.get("mask"), str(a.get("type", "")), str(a.get("subtype", "")))
            for a in accounts
        ])


def list_plaid_accounts(connected_account_id: int = None) -> list[dict]:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        if connected_account_id:
            rows = conn.execute("""
                SELECT pa.*, ca.name AS institution_name
                FROM plaid_accounts pa
                JOIN connected_accounts ca ON pa.connected_account_id = ca.id
                WHERE pa.connected_account_id = %s
            """, (connected_account_id,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT pa.*, ca.name AS institution_name
                FROM plaid_accounts pa
                JOIN connected_accounts ca ON pa.connected_account_id = ca.id
                ORDER BY ca.name, pa.name
            """).fetchall()
    return [dict(r) for r in rows]


def get_plaid_account_map(user_id: int) -> dict:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        rows = conn.execute("""
            SELECT pa.plaid_account_id, pa.name, pa.mask, pa.type, pa.subtype,
                   ca.name AS institution_name
            FROM plaid_accounts pa
            JOIN connected_accounts ca ON pa.connected_account_id = ca.id
            WHERE ca.user_id = %s
        """, (user_id,)).fetchall()
    return {r["plaid_account_id"]: dict(r) for r in rows}


# ── Credentials (Plaid API keys — global/shared) ─────────────────────────────────

def get_credential(key: str) -> str | None:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        row = conn.execute(
            "SELECT value FROM credentials WHERE key = %s", (key,)
        ).fetchone()
    if not row:
        return None
    from core.crypto import decrypt
    return decrypt(row["value"])


def set_credential(key: str, value: str):
    from core.crypto import encrypt
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        conn.execute("""
            INSERT INTO credentials (key, value, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (key) DO UPDATE SET
                value      = EXCLUDED.value,
                updated_at = NOW()
        """, (key, encrypt(value)))


def get_last_synced_at(user_id: int) -> str | None:
    return get_credential(f"last_synced_at_{user_id}")


def set_last_synced_at(user_id: int, value: str):
    set_credential(f"last_synced_at_{user_id}", value)


# ── Connected accounts ────────────────────────────────────────────────────────────

def list_connected_accounts(user_id: int) -> list[dict]:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        rows = conn.execute(
            "SELECT id, name, account_type, created_at "
            "FROM connected_accounts WHERE user_id = %s ORDER BY created_at",
            (user_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def add_connected_account(name: str, account_type: str,
                          access_token: str, user_id: int) -> int:
    from core.crypto import encrypt
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        row = conn.execute(
            "INSERT INTO connected_accounts (name, account_type, access_token, user_id) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (name, account_type, encrypt(access_token), user_id)
        ).fetchone()
    return row["id"]


def get_connected_account_by_name(name: str, user_id: int) -> dict | None:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        row = conn.execute(
            "SELECT id, name, account_type FROM connected_accounts "
            "WHERE LOWER(name) = LOWER(%s) AND user_id = %s",
            (name, user_id)
        ).fetchone()
    return dict(row) if row else None


def remove_connected_account(account_id: int, user_id: int):
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        conn.execute(
            "DELETE FROM connected_accounts WHERE id = %s AND user_id = %s",
            (account_id, user_id),
        )


def get_connected_account_tokens(user_id: int) -> list[dict]:
    from core.crypto import decrypt
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        rows = conn.execute(
            "SELECT id, name, account_type, access_token "
            "FROM connected_accounts WHERE user_id = %s ORDER BY created_at",
            (user_id,)
        ).fetchall()
    return [
        {"id": r["id"], "name": r["name"], "account_type": r["account_type"],
         "access_token": decrypt(r["access_token"])}
        for r in rows
    ]


# ── Dedup cache ───────────────────────────────────────────────────────────────────

def load_dedup_cache() -> dict:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        rows = conn.execute("SELECT * FROM dedup_cache").fetchall()
    return {
        r["fingerprint"]: {
            "is_duplicate": bool(r["is_duplicate"]),
            "is_transfer":  bool(r["is_transfer"]),
            "source":       r["source"],
            "reason":       r["reason"],
        }
        for r in rows
    }


def save_dedup_cache(cache: dict):
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        conn.executemany("""
            INSERT INTO dedup_cache (fingerprint, is_duplicate, is_transfer, source, reason)
            VALUES (%(fingerprint)s, %(is_duplicate)s, %(is_transfer)s, %(source)s, %(reason)s)
            ON CONFLICT (fingerprint) DO UPDATE SET
                is_duplicate = EXCLUDED.is_duplicate,
                is_transfer  = EXCLUDED.is_transfer,
                source       = EXCLUDED.source,
                reason       = EXCLUDED.reason
        """, [
            {"fingerprint": k, "is_duplicate": v["is_duplicate"],
             "is_transfer": v["is_transfer"], "source": v["source"], "reason": v["reason"]}
            for k, v in cache.items()
        ])


def upsert_dedup_entry(fingerprint: str, is_duplicate: bool,
                       is_transfer: bool, source: str, reason: str):
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        conn.execute("""
            INSERT INTO dedup_cache (fingerprint, is_duplicate, is_transfer, source, reason)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (fingerprint) DO UPDATE SET
                is_duplicate = EXCLUDED.is_duplicate,
                is_transfer  = EXCLUDED.is_transfer,
                source       = EXCLUDED.source,
                reason       = EXCLUDED.reason
        """, (fingerprint, is_duplicate, is_transfer, source, reason))


# ── Category map ──────────────────────────────────────────────────────────────────

PLAID_CATEGORY_SEEDS = {
    "Food and Drink":   "Food & Drink",
    "Transportation":   "Transport",
    "Shops":            "Shopping",
    "Travel":           "Travel",
    "Healthcare":       "Health",
    "Recreation":       "Other",
    "Service":          "Other",
    "Bank Fees":        "Other",
    "Community":        "Other",
    "Tax":              "Other",
    "Payment":          "Payments",
    "Transfer":         "Payments",
    "Interest":         "Income / Interest",
    "Payroll":          "Income / Interest",
}


def seed_category_map(user_id: int):
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        conn.executemany("""
            INSERT INTO category_map (user_id, external_category, internal_category)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, external_category) DO NOTHING
        """, [(user_id, ext, intern) for ext, intern in PLAID_CATEGORY_SEEDS.items()])


def seed_category_map_all_users():
    """Seed defaults for every existing user that is missing them (used at startup)."""
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        users = conn.execute("SELECT id FROM users").fetchall()
    for row in users:
        seed_category_map(row["id"])


def load_category_map(user_id: int) -> dict:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        rows = conn.execute(
            "SELECT external_category, internal_category FROM category_map WHERE user_id = %s",
            (user_id,)
        ).fetchall()
    return {r["external_category"]: r["internal_category"] for r in rows}


def upsert_category_mapping(user_id: int, external_category: str, internal_category: str):
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        conn.execute("""
            INSERT INTO category_map (user_id, external_category, internal_category)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, external_category) DO UPDATE SET
                internal_category = EXCLUDED.internal_category
        """, (user_id, external_category, internal_category))


def delete_category_mapping(user_id: int, external_category: str):
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        conn.execute(
            "DELETE FROM category_map WHERE user_id = %s AND external_category = %s",
            (user_id, external_category)
        )


# ── Normalization cache ───────────────────────────────────────────────────────────

def load_normalization_cache() -> dict:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        try:
            rows = conn.execute(
                "SELECT raw_name, clean_name FROM normalization_cache"
            ).fetchall()
            return {r["raw_name"]: r["clean_name"] for r in rows}
        except Exception:
            return {}


def save_normalization_entry(raw_name: str, clean_name: str):
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        conn.execute("""
            INSERT INTO normalization_cache (raw_name, clean_name)
            VALUES (%s, %s)
            ON CONFLICT (raw_name) DO UPDATE SET clean_name = EXCLUDED.clean_name
        """, (raw_name, clean_name))


# ── Shared merchant dictionary (global brand learning) ───────────────────────────

def load_merchant_dictionary() -> dict:
    """bucket -> canonical, shared across all users."""
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        try:
            rows = conn.execute(
                "SELECT bucket, canonical FROM merchant_dictionary"
            ).fetchall()
            return {r["bucket"]: r["canonical"] for r in rows}
        except Exception:
            return {}


def upsert_merchant_dictionary(entries: dict[str, str]):
    """entries: bucket -> canonical. Bumps members on conflict."""
    if not entries:
        return
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        for bucket, canonical in entries.items():
            conn.execute("""
                INSERT INTO merchant_dictionary (bucket, canonical, members)
                VALUES (%s, %s, 1)
                ON CONFLICT (bucket) DO UPDATE SET
                    canonical  = EXCLUDED.canonical,
                    members    = merchant_dictionary.members + 1,
                    updated_at = NOW()
            """, (bucket, canonical))


# ── Memo -> category cache (reimbursement notes) ─────────────────────────────────

def load_memo_category_cache() -> dict:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        try:
            rows = conn.execute("SELECT memo, category FROM memo_category_cache").fetchall()
            return {r["memo"]: r["category"] for r in rows}
        except Exception:
            return {}


def save_memo_category(memo: str, category: str):
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        conn.execute("""
            INSERT INTO memo_category_cache (memo, category)
            VALUES (%s, %s)
            ON CONFLICT (memo) DO UPDATE SET category = EXCLUDED.category
        """, (memo, category))


# ── Merchant overrides ───────────────────────────────────────────────────────────

def get_merchant_overrides(user_id: int) -> dict:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        rows = conn.execute(
            "SELECT raw_name, display_name FROM merchant_overrides WHERE user_id = %s",
            (user_id,)
        ).fetchall()
    return {r["raw_name"]: r["display_name"] for r in rows}


def save_merchant_override(user_id: int, raw_name: str, display_name: str):
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        # If raw_name matches the display_name of an existing override, the caller
        # is re-renaming an already-renamed merchant. Update the existing row so
        # the original raw key keeps mapping forward, instead of inserting a new
        # orphan override that never matches any real transaction.
        result = conn.execute(
            "UPDATE merchant_overrides SET display_name = %s, updated_at = NOW() "
            "WHERE user_id = %s AND display_name = %s AND raw_name <> %s",
            (display_name, user_id, raw_name, raw_name)
        )
        if result.rowcount > 0:
            return
        conn.execute("""
            INSERT INTO merchant_overrides (user_id, raw_name, display_name, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (user_id, raw_name) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                updated_at   = NOW()
        """, (user_id, raw_name, display_name))


def delete_merchant_override(user_id: int, raw_name: str):
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        conn.execute(
            "DELETE FROM merchant_overrides WHERE user_id = %s AND raw_name = %s",
            (user_id, raw_name)
        )


# ── User categories ──────────────────────────────────────────────────────────────

DEFAULT_CATEGORIES = [
    "Food & Drink",
    "Groceries",
    "Transport",
    "Shopping",
    "Subscriptions",
    "Health & Fitness",
    "Utilities",
    "Travel",
    "Entertainment",
    "Personal Care",
    "Home",
    "Education",
    "Business Services",
    "Income",
    "Transfer",
    "Other",
]


def _seed_user_categories(user_id: int):
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        conn.executemany("""
            INSERT INTO user_categories (user_id, name, position)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, name) DO NOTHING
        """, [(user_id, name, i) for i, name in enumerate(DEFAULT_CATEGORIES)])


def get_user_categories(user_id: int) -> list:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        rows = conn.execute(
            "SELECT name FROM user_categories WHERE user_id = %s ORDER BY position, id",
            (user_id,)
        ).fetchall()
    if not rows:
        _seed_user_categories(user_id)
        return DEFAULT_CATEGORIES[:]
    return [r["name"] for r in rows]


def add_user_category(user_id: int, name: str):
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        max_pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) AS m FROM user_categories WHERE user_id = %s",
            (user_id,)
        ).fetchone()["m"]
        conn.execute("""
            INSERT INTO user_categories (user_id, name, position)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, name) DO NOTHING
        """, (user_id, name.strip(), max_pos + 1))


def delete_user_category(user_id: int, name: str):
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        conn.execute(
            "DELETE FROM user_categories WHERE user_id = %s AND name = %s",
            (user_id, name)
        )


# ── Merchant category overrides ──────────────────────────────────────────────────

def get_merchant_category_overrides(user_id: int) -> dict:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        rows = conn.execute(
            "SELECT merchant_normalized, category FROM merchant_category_overrides WHERE user_id = %s",
            (user_id,)
        ).fetchall()
    return {r["merchant_normalized"]: r["category"] for r in rows}


def upsert_merchant_category_override(user_id: int, merchant_normalized: str, category: str):
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        conn.execute("""
            INSERT INTO merchant_category_overrides (user_id, merchant_normalized, category, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (user_id, merchant_normalized) DO UPDATE SET
                category   = EXCLUDED.category,
                updated_at = NOW()
        """, (user_id, merchant_normalized, category))


def delete_merchant_category_override(user_id: int, merchant_normalized: str):
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        conn.execute(
            "DELETE FROM merchant_category_overrides WHERE user_id = %s AND merchant_normalized = %s",
            (user_id, merchant_normalized)
        )


# ── Transfer overrides (per-counterparty "treat as transfer" memory) ──────────────

def get_transfer_overrides(user_id: int) -> dict:
    """Return {merchant_key: is_transfer} — the user's remembered transfer rulings."""
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        rows = conn.execute(
            "SELECT merchant_key, is_transfer FROM transfer_overrides WHERE user_id = %s",
            (user_id,)
        ).fetchall()
    return {r["merchant_key"]: bool(r["is_transfer"]) for r in rows}


def upsert_transfer_override(user_id: int, merchant_key: str, is_transfer: bool):
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        conn.execute("""
            INSERT INTO transfer_overrides (user_id, merchant_key, is_transfer, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (user_id, merchant_key) DO UPDATE SET
                is_transfer = EXCLUDED.is_transfer,
                updated_at  = NOW()
        """, (user_id, merchant_key, is_transfer))


def delete_transfer_override(user_id: int, merchant_key: str):
    """Clear a remembered ruling so the counterparty reverts to auto-detection."""
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        conn.execute(
            "DELETE FROM transfer_overrides WHERE user_id = %s AND merchant_key = %s",
            (user_id, merchant_key)
        )


def bulk_apply_category_override(user_id: int, transaction_ids: list, category: str) -> int:
    """Insert per-transaction overrides for a list of IDs, setting their category."""
    if not transaction_ids:
        return 0
    with get_user_conn(user_id) as conn:
        conn.executemany("""
            INSERT INTO overrides (transaction_id, user_id, category, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (transaction_id) DO UPDATE SET
                category   = EXCLUDED.category,
                updated_at = NOW()
        """, [(tid, user_id, category) for tid in transaction_ids])
    return len(transaction_ids)


# ── Dismissed duplicates ─────────────────────────────────────────────────────────

def get_dismissed_duplicate_pairs(user_id: int) -> set:
    """Return set of frozensets({id_a, id_b}) for pairs the user has dismissed."""
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        rows = conn.execute(
            "SELECT transaction_id_a, transaction_id_b FROM dismissed_duplicates WHERE user_id = %s",
            (user_id,)
        ).fetchall()
    return {frozenset([r["transaction_id_a"], r["transaction_id_b"]]) for r in rows}


def dismiss_duplicate_pair(user_id: int, id_a: str, id_b: str):
    """Record that the user confirmed two transactions are NOT duplicates."""
    # Normalize order so the unique constraint fires correctly regardless of which
    # ID the caller passes as a or b.
    a, b = sorted([str(id_a), str(id_b)])
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        conn.execute("""
            INSERT INTO dismissed_duplicates (user_id, transaction_id_a, transaction_id_b)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, transaction_id_a, transaction_id_b) DO NOTHING
        """, (user_id, a, b))


# ── Plaid item health ────────────────────────────────────────────────────────────

def upsert_item_health(connected_account_id: int, data: dict):
    """
    Store the result of a /item/get call for a connected account.
    `data` keys: error_code, error_message, consent_expiration_time,
                 last_successful_update, last_failed_update
    """
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        conn.execute("""
            INSERT INTO plaid_item_health (
                connected_account_id, error_code, error_message,
                consent_expiration_time, last_successful_update,
                last_failed_update, checked_at
            ) VALUES (%s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (connected_account_id) DO UPDATE SET
                error_code              = EXCLUDED.error_code,
                error_message           = EXCLUDED.error_message,
                consent_expiration_time = EXCLUDED.consent_expiration_time,
                last_successful_update  = EXCLUDED.last_successful_update,
                last_failed_update      = EXCLUDED.last_failed_update,
                checked_at              = NOW()
        """, (
            connected_account_id,
            data.get("error_code"),
            data.get("error_message"),
            data.get("consent_expiration_time"),
            data.get("last_successful_update"),
            data.get("last_failed_update"),
        ))


def get_item_health(user_id: int) -> list[dict]:
    """Return item health rows for all connected accounts belonging to user_id."""
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        rows = conn.execute("""
            SELECT ca.id AS connected_account_id, ca.name AS institution_name,
                   ih.error_code, ih.error_message,
                   ih.consent_expiration_time, ih.last_successful_update,
                   ih.last_failed_update, ih.checked_at
            FROM connected_accounts ca
            LEFT JOIN plaid_item_health ih ON ih.connected_account_id = ca.id
            WHERE ca.user_id = %s
        """, (user_id,)).fetchall()
        return [dict(r) for r in rows]


# ── Account deletion ─────────────────────────────────────────────────────────────

def schedule_user_deletion(user_id: int):
    grace_days = int(os.getenv("DELETION_GRACE_DAYS", "30"))
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        conn.execute(
            f"UPDATE users SET deletion_scheduled_at = NOW() + INTERVAL '{grace_days} days' WHERE id = %s",
            (user_id,)
        )


def purge_deleted_users():
    """Delete accounts whose deletion grace period has expired."""
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        conn.execute(
            "DELETE FROM users WHERE deletion_scheduled_at IS NOT NULL AND deletion_scheduled_at <= NOW()"
        )


def cancel_user_deletion(user_id: int):
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        conn.execute(
            "UPDATE users SET deletion_scheduled_at = NULL WHERE id = %s",
            (user_id,)
        )


def get_deletion_scheduled_at(user_id: int):
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = 'bypass'")
        row = conn.execute(
            "SELECT deletion_scheduled_at FROM users WHERE id = %s",
            (user_id,)
        ).fetchone()
    return row["deletion_scheduled_at"] if row else None


# ── User Goals ───────────────────────────────────────────────────────────────────

def create_goal(user_id: int, title: str, type: str = 'other', target_amount: float = None,
                current_amount: float = 0, deadline: str = None, priority: int = 3,
                notes: str = None) -> int:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        row = conn.execute("""
            INSERT INTO user_goals
                (user_id, title, type, target_amount, current_amount, deadline, priority, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (user_id, title, type, target_amount, current_amount, deadline, priority, notes)
        ).fetchone()
    return row["id"]


def list_goals(user_id: int, status: str = None) -> list[dict]:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        if status:
            rows = conn.execute(
                "SELECT * FROM user_goals WHERE user_id = %s AND status = %s ORDER BY priority, created_at",
                (user_id, status)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM user_goals WHERE user_id = %s ORDER BY priority, created_at",
                (user_id,)
            ).fetchall()
    return [dict(r) for r in rows]


def get_goal(user_id: int, goal_id: int) -> dict | None:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        row = conn.execute(
            "SELECT * FROM user_goals WHERE id = %s AND user_id = %s",
            (goal_id, user_id)
        ).fetchone()
    return dict(row) if row else None


def update_goal(user_id: int, goal_id: int, **fields) -> bool:
    allowed = {"title", "type", "target_amount", "current_amount",
               "deadline", "priority", "status", "notes"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    updates["updated_at"] = "NOW()"
    set_clause = ", ".join(
        f"{k} = NOW()" if v == "NOW()" else f"{k} = %s" for k, v in updates.items()
    )
    values = [v for v in updates.values() if v != "NOW()"]
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        conn.execute(
            f"UPDATE user_goals SET {set_clause} WHERE id = %s AND user_id = %s",
            (*values, goal_id, user_id)
        )
    return True


def delete_goal(user_id: int, goal_id: int):
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        conn.execute(
            "DELETE FROM user_goals WHERE id = %s AND user_id = %s",
            (goal_id, user_id)
        )


# ── Financial Events ──────────────────────────────────────────────────────────────

def create_financial_event(user_id: int, event_type: str, title: str,
                           amount: float = None, event_date: str = None,
                           description: str = None) -> int:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        row = conn.execute("""
            INSERT INTO financial_events (user_id, event_type, title, amount, event_date, description)
            VALUES (%s, %s, %s, %s, COALESCE(%s::date, CURRENT_DATE), %s)
            RETURNING id
        """, (user_id, event_type, title, amount, event_date, description)
        ).fetchone()
    return row["id"]


def list_financial_events(user_id: int, limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        rows = conn.execute(
            "SELECT * FROM financial_events WHERE user_id = %s ORDER BY event_date DESC LIMIT %s",
            (user_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]


def delete_financial_event(user_id: int, event_id: int):
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        conn.execute(
            "DELETE FROM financial_events WHERE id = %s AND user_id = %s",
            (event_id, user_id)
        )


# ── User Profile (behavioral/preference layer) ────────────────────────────────────

def get_user_financial_profile(user_id: int) -> dict | None:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        row = conn.execute(
            "SELECT * FROM user_profile WHERE user_id = %s", (user_id,)
        ).fetchone()
    return dict(row) if row else None


def upsert_user_financial_profile(user_id: int, **fields) -> None:
    allowed = {"life_stage", "risk_tolerance", "income_estimate", "savings_rate_pct",
               "communication_style", "spending_triggers", "preferences"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    cols = ", ".join(updates.keys())
    placeholders = ", ".join(["%s"] * len(updates))
    set_clause = ", ".join(f"{k} = EXCLUDED.{k}" for k in updates)
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        conn.execute(f"""
            INSERT INTO user_profile (user_id, {cols}, updated_at)
            VALUES (%s, {placeholders}, NOW())
            ON CONFLICT (user_id) DO UPDATE SET {set_clause}, updated_at = NOW()
        """, (user_id, *updates.values()))


# ── Financial Snapshots ───────────────────────────────────────────────────────────

def create_financial_snapshot(user_id: int, snapshot_date: str = None,
                              income_estimate: float = None, total_expenses: float = None,
                              savings_rate_pct: float = None, total_debt: float = None,
                              total_assets: float = None, net_worth: float = None) -> int:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        row = conn.execute("""
            INSERT INTO financial_snapshots
                (user_id, snapshot_date, income_estimate, total_expenses,
                 savings_rate_pct, total_debt, total_assets, net_worth)
            VALUES (%s, COALESCE(%s::date, CURRENT_DATE), %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, snapshot_date) DO UPDATE SET
                income_estimate  = EXCLUDED.income_estimate,
                total_expenses   = EXCLUDED.total_expenses,
                savings_rate_pct = EXCLUDED.savings_rate_pct,
                total_debt       = EXCLUDED.total_debt,
                total_assets     = EXCLUDED.total_assets,
                net_worth        = EXCLUDED.net_worth
            RETURNING id
        """, (user_id, snapshot_date, income_estimate, total_expenses,
              savings_rate_pct, total_debt, total_assets, net_worth)
        ).fetchone()
    return row["id"]


def list_financial_snapshots(user_id: int, limit: int = 12) -> list[dict]:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        rows = conn.execute(
            "SELECT * FROM financial_snapshots WHERE user_id = %s ORDER BY snapshot_date DESC LIMIT %s",
            (user_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]


# ── Conversation Memory ───────────────────────────────────────────────────────────
# These two functions are the most important in the memory layer.

def store_memory(user_id: int, content: str, memory_type: str = 'context',
                 importance: int = 3, source: str = 'conversation',
                 embedding: list[float] | None = None) -> int:
    """Store a memory. Pass embedding as a list of floats if available."""
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        row = conn.execute("""
            INSERT INTO conversation_memory
                (user_id, content, memory_type, importance, source, embedding)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (user_id, content, memory_type, importance, source,
              embedding)  # psycopg2 passes list as array; pgvector accepts it via VECTOR cast
        ).fetchone()
    return row["id"]


def retrieve_relevant_memories(user_id: int, embedding: list[float],
                                limit: int = 10,
                                memory_type: str | None = None) -> list[dict]:
    """Semantic search over stored memories using cosine similarity.

    Returns memories ordered by relevance (closest embedding first).
    Also updates last_accessed_at so we can track what's being used.
    """
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        if memory_type:
            rows = conn.execute("""
                SELECT id, content, memory_type, importance, source, created_at,
                       1 - (embedding <=> %s::vector) AS similarity
                FROM conversation_memory
                WHERE user_id = %s AND memory_type = %s AND embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """, (embedding, user_id, memory_type, embedding, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT id, content, memory_type, importance, source, created_at,
                       1 - (embedding <=> %s::vector) AS similarity
                FROM conversation_memory
                WHERE user_id = %s AND embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """, (embedding, user_id, embedding, limit)).fetchall()

        if rows:
            ids = [r["id"] for r in rows]
            conn.execute(
                f"UPDATE conversation_memory SET last_accessed_at = NOW() WHERE id = ANY(%s)",
                (ids,)
            )
    return [dict(r) for r in rows]


def list_memories(user_id: int, memory_type: str | None = None,
                  limit: int = 50) -> list[dict]:
    """List memories without semantic search — for browsing/debugging."""
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        if memory_type:
            rows = conn.execute(
                "SELECT id, content, memory_type, importance, source, created_at "
                "FROM conversation_memory WHERE user_id = %s AND memory_type = %s "
                "ORDER BY importance DESC, created_at DESC LIMIT %s",
                (user_id, memory_type, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, content, memory_type, importance, source, created_at "
                "FROM conversation_memory WHERE user_id = %s "
                "ORDER BY importance DESC, created_at DESC LIMIT %s",
                (user_id, limit)
            ).fetchall()
    return [dict(r) for r in rows]


def delete_memory(user_id: int, memory_id: int):
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        conn.execute(
            "DELETE FROM conversation_memory WHERE id = %s AND user_id = %s",
            (memory_id, user_id)
        )


# ── Advice History ────────────────────────────────────────────────────────────────

def store_advice(user_id: int, response_text: str, prompt_summary: str = None,
                 user_message: str = None, category: str = None,
                 compliance_flags: list = None, prompt_tokens: int = None,
                 completion_tokens: int = None, latency_ms: int = None) -> int:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        import json
        row = conn.execute("""
            INSERT INTO advice_history
                (user_id, prompt_summary, user_message, response_text, category,
                 compliance_flags, prompt_tokens, completion_tokens, latency_ms)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (user_id, prompt_summary, user_message, response_text, category,
              json.dumps(compliance_flags or []), prompt_tokens, completion_tokens, latency_ms)
        ).fetchone()
    return row["id"]


def list_advice(user_id: int, limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        rows = conn.execute(
            "SELECT * FROM advice_history WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
            (user_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]


def update_advice_reaction(user_id: int, advice_id: int,
                           reaction: str, outcome_notes: str = None):
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        conn.execute("""
            UPDATE advice_history
            SET user_reaction = %s, outcome_notes = COALESCE(%s, outcome_notes)
            WHERE id = %s AND user_id = %s
        """, (reaction, outcome_notes, advice_id, user_id))


# Prune stale tokens at import time
cleanup_expired_refresh_tokens()


# ── Nudges ───────────────────────────────────────────────────────────────────

def save_nudge(
    user_id: int,
    nudge_type: str,
    severity: str,
    title: str,
    body: str,
    data: dict,
) -> int:
    import json as _json
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        row = conn.execute(
            """INSERT INTO nudges (user_id, type, severity, title, body, data)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (user_id, nudge_type, severity, title, body, _json.dumps(data)),
        ).fetchone()
    return row["id"]


def get_active_nudges(user_id: int) -> list[dict]:
    """Return undismissed nudges for a user, newest first."""
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        rows = conn.execute(
            """SELECT id, type, severity, title, body, data, created_at, read_at
               FROM nudges
               WHERE user_id = %s AND dismissed_at IS NULL
               ORDER BY created_at DESC""",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def dismiss_nudge(nudge_id: int, user_id: int) -> None:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        conn.execute(
            """UPDATE nudges SET dismissed_at = NOW()
               WHERE id = %s AND user_id = %s""",
            (nudge_id, user_id),
        )


def mark_nudges_read(user_id: int) -> None:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        conn.execute(
            """UPDATE nudges SET read_at = NOW()
               WHERE user_id = %s AND read_at IS NULL AND dismissed_at IS NULL""",
            (user_id,),
        )


def clear_stale_nudges(user_id: int, days: int = 7) -> None:
    """Remove nudges older than `days` days so re-running analysis stays fresh."""
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        conn.execute(
            "DELETE FROM nudges WHERE user_id = %s AND created_at < NOW() - make_interval(days => %s)",
            (user_id, days),
        )


def get_unread_nudge_count(user_id: int) -> int:
    with get_conn() as conn:
        conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
        row = conn.execute(
            """SELECT COUNT(*) AS n FROM nudges
               WHERE user_id = %s AND read_at IS NULL AND dismissed_at IS NULL""",
            (user_id,),
        ).fetchone()
    return int(row["n"]) if row else 0
