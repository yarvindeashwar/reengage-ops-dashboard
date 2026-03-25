"""
Local SQLite database for v2 tables (users, assignments, ops_log).
BigQuery is read-only; all writes go here.
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "reengage_v2.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Create tables if they don't exist. Safe to call on every app start."""
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        email TEXT PRIMARY KEY,
        name TEXT,
        role TEXT NOT NULL CHECK(role IN ('lead', 'tenured_operator', 'new_operator')),
        approved INTEGER DEFAULT 1,
        reduction_pct REAL DEFAULT 0,
        added_by TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS assignments (
        assignment_id TEXT PRIMARY KEY,
        review_uid TEXT NOT NULL,
        order_id TEXT,
        operator_email TEXT NOT NULL,
        chain_name TEXT,
        platform TEXT,
        days_left INTEGER,
        status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'completed', 'expired')),
        assigned_at TEXT DEFAULT (datetime('now')),
        completed_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_asgn_operator ON assignments(operator_email, status);
    CREATE INDEX IF NOT EXISTS idx_asgn_review ON assignments(review_uid);

    CREATE TABLE IF NOT EXISTS ops_log (
        id TEXT PRIMARY KEY,
        review_uid TEXT,
        platform TEXT,
        chain_name TEXT,
        action TEXT,
        operator_email TEXT,
        performed_by TEXT,
        remarks TEXT,
        processing_timestamp TEXT DEFAULT (datetime('now'))
    );
    """)
    conn.commit()
    conn.close()


# Auto-init on import
init_db()
